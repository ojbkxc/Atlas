"""W3 追加项离线测：review-code 读链不崩 + SAFE-001 保留域豁免（team-lead 指派）。

背景①（预存红灯）：`atlas review-code tests/` 遇 `__pycache__/*.pyc` 裸崩
UnicodeDecodeError——旧 `_iter_targets` 用 `rglob("*")` 收所有文件、
`scan_pytest_file` 严格 utf-8 读。修法=目标只收 .py/.yaml/.yml/.json + 读侧
errors="replace"（#131 纪律：坏字节点名继续，绝不整份崩）。migration_gate 的
`--baseline` 走同一读链，这颗雷必须先修。

背景②（SAFE-001 豁免口径，硬约束两轮已钉）：豁免**只认 RFC 保留域**
（example 家族/.test/.invalid/localhost/回环），严禁扩成「tests 目录整体不判」。
字面秘密赋值（password=/token=/api_key=/secret=）**永不豁免**——C4 故意放进
tests/ 的字面凭据样本必须继续吃 critical（红线 #7 反馈环：看着刺眼是对的）。
本文件双向钉：保留域样本放行 + 仿真实 host/字面 token 仍 critical（豁免可测、
不给自己留后门）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas.review_code import (_safe001_urls_exempt, _url_host_reserved,
                               review_code, scan_pytest_file, load_config,
                               _iter_targets)

CFG = None  # 用默认配置


def _cfg():
    return load_config(None)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── 1. 读链：目录扫描不收集非文本形态（.pyc 雷拆除）──────────────────────

def test_directory_scan_skips_pyc_and_junk(tmp_path):
    pkg = tmp_path / "pkg"
    _write(pkg, "test_real.py", "def test_x():\n    assert True\n")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "test_real.cpython-313.pyc").write_bytes(b"\x00\x01\x02\x03\x00")
    (pkg / "notes.txt").write_bytes(b"ignore me \xff\x00")
    targets = [str(p) for p, _k in _iter_targets([pkg], "auto")]
    assert targets == [str(pkg / "test_real.py")]
    # 端到端：review_code 对同目录不抛（旧实现在这里 UnicodeDecodeError）
    out = review_code([pkg])
    assert out["rc"] in (0, 1)


def test_explicit_junk_file_replaced_not_crash(tmp_path):
    """显式点名的坏字节文件：replace 读入 → 语法失败落 MAINT-006，不裸崩。"""
    f = _write(tmp_path, "bad.py", "placeholder")
    f.write_bytes(b"def \xff\xfe broken(\n")
    findings = scan_pytest_file(f, _cfg())
    assert isinstance(findings, list)


def test_baseline_chain_survives_bad_bytes(tmp_path):
    """--baseline 读链同纪律：基线文件坏字节不崩整份评审（migration_gate 共链）。"""
    cur = _write(tmp_path, "test_cur.py", "def test_x():\n    assert a == 1\n")
    bl = tmp_path / "base.py"
    bl.write_bytes("def test_x():\n    assert a == 1\n".encode("utf-8") + b"\xff\xfe\x00")
    out = review_code([cur], baseline=bl)
    assert out["rc"] in (0, 1)


# ── 2. SAFE-001：保留域豁免判据本身 ───────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://sso.example/cb", "https://ics.example.com/web/list",
    "http://localhost:8765", "https://foo.test/x", "https://a.invalid",
    "http://127.0.0.1/api", "https://EXAMPLE.COM/x",           # 大小写归一
])
def test_reserved_hosts_exempt(url):
    assert _url_host_reserved(url)


@pytest.mark.parametrize("url", [
    "https://api.lxseek.com/v1", "https://ics.corp.internal/api",
    "https://104.223.65.202/x", "https://x/y",
    # 形似保留域实非：example.cn / example.com.cn 不在 RFC 名单（保守拒绝）
    "https://ics.example.com.cn/x", "https://notexample.com/x",
    "https://example.com.evil.cn/x",
])
def test_non_reserved_hosts_not_exempt(url):
    assert not _url_host_reserved(url)


def test_escaped_trailing_still_exempt():
    """源码转义尾（`https://real.example\\n`）：host 按首个反斜杠截断后仍认保留域。"""
    assert _url_host_reserved("https://real.example\\n")
    assert _safe001_urls_exempt('x = "https://real.example\\n"')


# ── 3. SAFE-001 行级双向钉 ────────────────────────────────────────────────

def test_reserved_url_line_no_critical(tmp_path):
    f = _write(tmp_path, "test_sanitized.py", '''\
CB = "https://sso.example/cb"
ICS = "https://ics.example.com/web/list?ticket=ST-LONG-SECRET-VALUE"
LOCAL = "http://localhost:8765/index.html"
def test_x():
    assert CB
''')
    findings = scan_pytest_file(f, _cfg())
    assert not [x for x in findings if x["rule_id"] == "SAFE-001"]


def test_real_host_url_line_still_critical(tmp_path):
    f = _write(tmp_path, "test_real.py", '''\
API = "https://ics.real-corp.com/api/list"
def test_x():
    assert API
''')
    findings = scan_pytest_file(f, _cfg())
    assert [x for x in findings if x["rule_id"] == "SAFE-001"
            and x["severity"] == "critical"]


def test_literal_token_never_exempt_even_on_example_host(tmp_path):
    """硬约束：字面秘密赋值与保留域同行也 critical——豁免面只有 URL 形态这一条。"""
    f = _write(tmp_path, "test_tok.py", '''\
CALLBACK = "https://sso.example/cb"
token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
def test_x():
    assert CALLBACK
''')
    findings = scan_pytest_file(f, _cfg())
    hits = [x for x in findings if x["rule_id"] == "SAFE-001"]
    assert any("token" in x["snippet"] for x in hits), \
        "真字面 token 必须继续吃 critical（整目录/整行豁免=静默口）"


def test_mixed_line_not_exempt(tmp_path):
    """一行同时含保留域与真实 host：保守判红（豁免只给「全保留域」行）。"""
    line = 'a = "https://sso.example/cb"; b = "https://real-ics.corp.com/api"'
    assert not _safe001_urls_exempt(line)


# ── 4. 行为面：tests/ 真实目录整扫不崩且保留域样本不再贡献误报 ─────────────

def test_review_code_on_repo_tests_survives_and_no_exemptable_line_remains():
    """预存红灯本体回归：`atlas review-code tests/` 全目录跑通（含 __pycache__），
    且不变量成立——**仍报的 SAFE-001 没有一条是「仅保留域 URL」行**（该豁免的
    都豁免了；残留只允许字面秘密赋值或含非保留 host 的行，逐条核验不靠猜）。"""
    from atlas.review_code import _CREDENTIAL_PATTERNS, _URL_PATTERN
    root = Path(__file__).resolve().parent.parent
    out = review_code([root / "tests"])
    crit = [f for fr in out["results"] for f in fr["findings"]
            if f["rule_id"] == "SAFE-001"]
    assert crit, "tests/ 里现仍有字面凭据/非保留 host 样本——若全被豁免说明豁免面失控"
    for f in crit:
        if any(p.search(f["snippet"]) for p in _CREDENTIAL_PATTERNS):
            continue  # 字面秘密赋值：永不豁免面，合法 red
        urls = _URL_PATTERN.findall(f["snippet"])
        assert urls and not all(_url_host_reserved(u) for u in urls), \
            f"仅保留域的行不该再吃 critical：{f['file']}:{f['line']}"

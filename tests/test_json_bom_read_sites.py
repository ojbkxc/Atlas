"""JSON **对象文件**读侧的 BOM 安全：单点收口 + 全仓守卫。

## 挡的是什么（2026-11-24 实测）

`overlay` 读 BU 坐标 JSON 用 `read_text(encoding="utf-8")`，Windows 上 PowerShell
`ConvertTo-Json | Set-Content`（PS5.1 默认 `-Encoding Unicode`，但 `Out-File -Encoding utf8`
与 VS Code「UTF-8 with BOM」都产 BOM）写出的文件直接报错退出：

    Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)   → rc 2

**根因不是「解码失败」，是「解码成功得太顺利」**：BOM 是合法 UTF-8 字节（`EF BB BF`），
裸 `encoding="utf-8"` 读它**不抛 `UnicodeDecodeError`**，只是把 `\ufeff` 留在文本首字符，
然后 `json.loads` 才抛 `JSONDecodeError`。这一层之隔让所有「我兜了 UnicodeDecodeError」
的写法**全部失效**——看起来处理了 BOM，实际一行都没拦到。

后果分两族，第二族比崩溃坏得多：

1. **裸崩族**：`overlay.load_overlays` rc 2；`contracts.ContractRegistry.load` 的
   `JSONDecodeError` 顶掉整条契约链（包内回退都走不到）；`review_code.load_config` 崩；
   `plugins/demo/creators.build_payload` 建单链死。
2. **静默降级族**（本文件重点）：这些地方**本来就有** `except (OSError, JSONDecodeError,
   UnicodeDecodeError)` 兜底，BOM 撞进去就等于「文件不存在/损坏」：

   - `knowledge._read_mapping` → `None` → **整份私有知识文件静默消失**，回退下一个来源；
   - `ui_family.load_family_table` → `{}` → **静默回退内置族表**；
   - `allure_analysis` → result 文件计入 `skipped` → **同步上去的用例少几条**；
   - `scheduling.lease.read_lock` → `None` = **「视同无锁」→ 并发闸失效**（最危险的一处：
     锁文件本身把闸关掉了）；
   - `devops_sync.load_mcp_servers` → `DevopsSyncError: 解析失败`（同一个 mcp.json 别的
     工具都能读，只有 Atlas 报错）；
   - `legacy_fast.load_site_rules` → `[]` + 一行 WARN → **规则全失效，回归照跑**。

   报告照常产出、退出码正常，只有结论悄悄变错。这与 `--fail-under -1`（评分 0 也 rc 0）、
   `--max-steps -1`（零迭代却报「步数用尽」）同一族：**参数/输入没校验，功能静默失效**。

## 修法

判据单点 `atomic_io.read_json_file(path)`（= `read_text_retry` + `utf-8-sig` + `json.loads`）。
与 JSONL 侧的 `read_jsonl_text` 同取向，但**不做** `errors="replace"` 回落——这点差异是刻意的：
JSONL 一行坏字节只毁一行且损失可见；JSON **对象**用 replace 会让坏字节变成合法的 `U+FFFD`
并照样通过 `json.loads`，于是错误数据被当正确配置消费，没有任何报错。JSON 是契约文件，
必须精确或大声失败。

`legacy_fast.py` 是例外：它明确禁用相对 import（无包上下文 standalone 加载），所以内联
`encoding="utf-8-sig"`，不走单点——口径相同，形态受限。

## 五条

1. `test_read_json_file_accepts_bom`：单点自身吃 BOM（正向）；
2. `test_read_json_file_rejects_bad_bytes_without_replacing`：**坏字节必须抛**，不许
   `errors="replace"` 静默通过（否则回到「错误数据被当配置用」）；
3. `test_loaders_accept_bom[各加载器]`：参数化打穿**全部**改过的加载器——每个都喂带 BOM 的
   真文件，断言解析结果与无 BOM 版本**等值**（不是「不抛异常」就算过）；
4. `test_no_bare_utf8_json_file_reads`：源码扫描守卫，`src/` 里不得再有
   `json.loads(...read_text(encoding="utf-8"))` 这类裸写法；
5. `test_guard_scanned_floor`：正向对照下限，防「正则写坏 = 恒绿」。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from atlas import atomic_io
from atlas.boundaries import load_ledger
from atlas.contracts import ContractRegistry
from atlas.devops_sync import load_mcp_servers
from atlas.knowledge import _read_mapping
from atlas.legacy_fast import load_site_rules
from atlas.overlay import load_overlays
from atlas.review_code import load_config
from atlas.scheduling.lease import read_lock
from atlas.ui_family import load_family_table

_REPO = Path(__file__).resolve().parents[1]
_BOM = "\ufeff"


def _write_bom_json(path: Path, payload) -> Path:
    """按 PowerShell/记事本的形态写一个带 BOM 的 UTF-8 JSON 文件。"""
    path.write_text(_BOM + json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert path.read_bytes()[:3] == b"\xef\xbb\xbf", "夹具本身没写出 BOM，断言无意义"
    return path


# ── 1/2：单点自身 ────────────────────────────────────────────────────────

def test_read_json_file_accepts_bom(tmp_path):
    path = _write_bom_json(tmp_path / "a.json", {"k": ["v"]})
    assert atomic_io.read_json_file(path) == {"k": ["v"]}


def test_read_json_file_accepts_no_bom(tmp_path):
    """正向对照：无 BOM 时 `utf-8-sig` 与 `utf-8` 逐字节等价，行为不得变化。"""
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"k": "v"}), encoding="utf-8")
    assert atomic_io.read_json_file(path) == {"k": "v"}


def test_read_json_file_rejects_bad_bytes_without_replacing(tmp_path):
    """坏字节必须抛，不许 replace 后静默通过。

    这是与 `read_jsonl_text` 的**刻意分歧**：JSON 对象若把坏字节换成 `U+FFFD` 再解析成功，
    错误数据就被当成正确配置消费。断言具体异常类型而非「抛了就行」。
    """
    path = tmp_path / "bad.json"
    path.write_bytes(b'{"k": "v\xff"}')          # 非法 UTF-8 尾字节
    with pytest.raises(UnicodeDecodeError):
        atomic_io.read_json_file(path)


def test_read_json_file_surfaces_json_error(tmp_path):
    """语法坏 JSON 原样抛 `JSONDecodeError`——由调用方按自己的契约处置，单点不替它决定。"""
    path = tmp_path / "broken.json"
    path.write_text('{"k": ', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        atomic_io.read_json_file(path)


# ── 3：全部改过的加载器，逐个吃 BOM ──────────────────────────────────────

def test_overlay_loads_bom(tmp_path):
    path = _write_bom_json(tmp_path / "bu.json",
                           {"BU-A": {"ou": "1"}, "BU-B": {"ou": "2"}})
    assert load_overlays(path)["BU-A"]["ou"] == "1"


def test_contract_registry_loads_bom(tmp_path):
    path = _write_bom_json(tmp_path / "contracts.json", {"contracts": []})
    assert ContractRegistry.load(path).contracts == {}


def test_knowledge_read_mapping_loads_bom(tmp_path):
    """改前：BOM → JSONDecodeError → 被 except 吞成 None（整份知识文件静默消失）。"""
    path = _write_bom_json(tmp_path / "pitfalls.json", {"pitfalls": [{"id": "p1"}]})
    assert _read_mapping(path) == {"pitfalls": [{"id": "p1"}]}


def test_knowledge_read_mapping_still_none_on_bad_json(tmp_path):
    """正向对照：真坏文件仍回 None（口径没被放松）。"""
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert _read_mapping(path) is None


def test_ui_family_loads_bom(tmp_path, monkeypatch):
    """改前：BOM → except → `{}` → 静默回退内置族表。这里直接喂族表路径。"""
    path = _write_bom_json(tmp_path / "ui_family.json",
                           {"default": "antd", "families": {"antd": {"prefixes": [".ant-"]}}})
    monkeypatch.setattr("atlas.ui_family._table_path", lambda: path)
    assert load_family_table()["default"] == "antd"


def test_review_config_loads_bom(tmp_path):
    path = _write_bom_json(tmp_path / "reviewcode.json", {"weights": {}})
    assert isinstance(load_config(path), dict)


def test_devops_mcp_servers_loads_bom(tmp_path):
    """改前：BOM 撞进 `except UnicodeDecodeError` 的第一个分支（永不触发）→ 报「解析失败」。"""
    path = _write_bom_json(tmp_path / "mcp.json",
                           {"mcpServers": {"devops-mcp-sit": {"url": "http://x"}}})
    assert "devops-mcp-sit" in load_mcp_servers(path)


def test_lease_read_lock_loads_bom(tmp_path):
    """改前：BOM → 坏 JSON → None = **「视同无锁」**，锁文件把并发闸自己关掉了。"""
    path = _write_bom_json(tmp_path / "lease.json", {"token": "t1", "pid": 1})
    assert read_lock(path) == {"token": "t1", "pid": 1}


def test_boundaries_ledger_loads_bom(tmp_path):
    """改前：BOM → JSON 解析失败 → 账本整份判为不可用，边界判定随之失效。"""
    path = _write_bom_json(tmp_path / "ledger.json", {"schema_version": 1, "entries": []})
    data, problems = load_ledger(path)
    assert data.get("schema_version") == 1
    assert not any("不可读" in p or "解析失败" in p for p in problems), problems


def test_legacy_fast_site_rules_load_bom(tmp_path):
    """改前：BOM → `except Exception` → 只打 WARN 然后返回 `[]`（规则全失效，回归照跑）。"""
    path = _write_bom_json(tmp_path / "wait_rules.json",
                           {"rules": [{"file": "test_a.py", "line": 10, "kind": "js",
                                       "js": "window.ready"}]})
    rules = load_site_rules(path)
    assert len(rules) == 1 and rules[0]["file"] == "test_a.py"


# ── 4/5：源码守卫 ────────────────────────────────────────────────────────

# 裸写法：`json.loads(` 与同一表达式里的 `read_text(encoding="utf-8")` 同行。
# 允许的形态是 `read_json_file(...)` / `read_json_text(...)` / `encoding="utf-8-sig"`。
_BARE = re.compile(r'json\.loads\([^)\n]*read_text\(\s*encoding\s*=\s*["\']utf-8["\']')


def _json_read_sources() -> list[Path]:
    return sorted((_REPO / "src").rglob("*.py"))


def test_no_bare_utf8_json_file_reads():
    offenders = []
    for path in _json_read_sources():
        text = path.read_text(encoding="utf-8")
        for m in _BARE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.relative_to(_REPO)}:{line}: {m.group(0)}")
    assert not offenders, (
        "以下位置用裸 `utf-8` 读 JSON 文件——BOM 是合法 UTF-8，不会抛 UnicodeDecodeError，"
        "只会在 json.loads 处失败（或撞进 except 静默降级）。改用 "
        "`atomic_io.read_json_file` / `read_json_text`（`utf-8-sig`）：\n" + "\n".join(offenders))


def test_guard_scanned_floor():
    """正向对照：正则若写坏（恒不匹配）这条会红，而不是笑纳一个空列表。"""
    sources = _json_read_sources()
    assert len(sources) >= 50, f"只扫到 {len(sources)} 个源文件，守卫可能失效"
    hits = sum(len(_BARE.findall(p.read_text(encoding="utf-8"))) for p in sources)
    assert hits == 0, f"仍命中 {hits} 处裸写法"


def test_guard_can_still_catch_the_pattern():
    """守卫的**自证**：拿一段已知坏样本喂给正则，必须命中。

    防「正则被改坏 → 全仓恒绿」——这是所有源码扫描型守卫的共同失效模式。
    """
    sample = 'raw = json.loads(path.read_text(encoding="utf-8"))'
    assert _BARE.search(sample) is not None


# ── 6/7：JSONL 读侧的**扫描式**守卫（补白名单漏点）─────────────────────────
#
# 背景（2026-11-25 实测）：`tests/test_jsonl_bom_first_row.py::test_jsonl_readers_use_single_point`
# 是**白名单式**的——只核 5 个具名文件。于是 `cli.py` 里两处 JSONL 读侧（`_recycle` 的回收
# 清单、`_review --apply` 的审批决策）用裸 `utf-8` 读了一整年都没人发现，直到本波人工 grep
# 才扫出来。白名单的缺陷是**结构性的**：新增读侧不会自己进名单，而漏掉的后果是静默丢行。
# 所以补这道**扫描式**守卫：按「JSONL 读侧形态」认，而不是按文件名认。

_JSONL_SHAPE = re.compile(
    r"read_text\(\s*encoding\s*=\s*[\"']utf-8[\"']\s*\)"
    r"(?:.|\n){0,400}?"                       # 同一函数体内不远处的消费者
    r"(?:json|_json)\.loads\(\s*line")


def _jsonl_bare_read_offenders() -> list[str]:
    offenders: list[str] = []
    for path in _json_read_sources():
        text = path.read_text(encoding="utf-8")
        for m in _JSONL_SHAPE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.relative_to(_REPO)}:{line}")
    return offenders


def test_jsonl_readers_go_through_single_point():
    """凡是「读完按行 `json.loads`」的地方，都必须走 `read_jsonl_text`。

    裸 `utf-8` 的后果不是崩，是**首行变成 `\ufeff{...}` → 落进 skipped/broken 分支**
    ——一条已产出的单号或一条已批准的决策，静默变成「格式错误」而无人察觉。
    """
    offenders = _jsonl_bare_read_offenders()
    assert not offenders, (
        "以下位置是 JSONL 读侧却用裸 `utf-8`（BOM 会让首行静默变成残行）：\n  "
        + "\n  ".join(offenders)
        + "\n改用 `atomic_io.read_jsonl_text`（`utf-8-sig` + 锁重试 + 坏字节 replace 回落）。")


def test_jsonl_scan_can_still_catch_the_pattern():
    """扫描自证：已知坏形态必须命中，正形态必须放行（防守卫恒绿/恒红）。"""
    bad = ('raw = path.read_text(encoding="utf-8")\n'
           'for line in raw.splitlines():\n'
           '    obj = json.loads(line)\n')
    good = ('raw = read_jsonl_text(path)\n'
            'for line in raw.splitlines():\n'
            '    obj = json.loads(line)\n')
    assert _JSONL_SHAPE.search(bad) is not None
    assert _JSONL_SHAPE.search(good) is None


# ── 8：CLI 两处 JSONL 读侧的**行为**验证（扫描守卫的配套）───────────────────

def test_recycle_list_reads_bom_first_row(tmp_path, capsys):
    """`atlas recycle --input <带 BOM 的 jsonl>` 必须列得出第一条回收单。

    改前：BOM 让首行 `\ufeff{...}` 解析失败 → `skipped += 1` → **一条已真实产生的单据
    从此不在回收清单里**（单据存在、单号没人知道），退出码仍是 0。
    """
    from types import SimpleNamespace

    from atlas.cli import _recycle

    path = _write_bom_json(tmp_path / "recycle_list.jsonl",
                           {"entity": "SO-1", "id": "r1"})
    args = SimpleNamespace(input=path, cleanup=False)
    config = SimpleNamespace(reports_dir=tmp_path)
    assert _recycle(args, config) == 0
    out = capsys.readouterr().out
    assert "SO-1" in out, f"BOM 首条回收单被静默丢弃，实际输出：{out!r}"


def test_recycle_list_still_tolerates_bad_bytes(tmp_path, capsys):
    """正向对照：真·坏字节仍走 replace 回落（不因 BOM 修复而变脆）。"""
    from types import SimpleNamespace

    from atlas.cli import _recycle

    path = tmp_path / "recycle_list.jsonl"
    path.write_bytes(b'{"entity": "SO-2"}\n{"bad": "\xff"}\n')
    args = SimpleNamespace(input=path, cleanup=False)
    config = SimpleNamespace(reports_dir=tmp_path)
    assert _recycle(args, config) == 0
    out = capsys.readouterr().out
    assert "SO-2" in out, f"合法行不该被坏字节连带丢掉：{out!r}"

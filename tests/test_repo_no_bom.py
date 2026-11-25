"""**仓内写方 BOM 零残留**守卫：自己产出的文件不许带 BOM。

## 为什么钉它（2026-11-23，BOM 波的写侧收口）

读侧刚钉完 BOM 安全（`atomic_io.read_jsonl_text` 用 `utf-8-sig`，`tests/test_jsonl_bom_first_row.py`）。
但**读侧兜底不能成为写侧借口**：Atlas 的产物是「要交给别人独立运行的包」，产物若带 BOM，
消费方未必都像本仓一样用 `utf-8-sig` 读——`pytest.ini`/`pyproject.toml` 的解析器、用户自己的
编辑器都可能踩。写方唯一正确形态是 **`encoding="utf-8"` + `write_text`**（Python 的
`write_text` 从不写 BOM）；`utf-8-sig` 写、或经由会加 BOM 的外层工具写，都是写侧缺陷。

本仓扫过一遍：`src/`+`scripts/` 全部 .py、`knowledge/`、`examples/`、`flows/`、根目录文本
——**零 BOM**（`write_text(..., encoding="utf-8")` 从不产 BOM）。钉成守卫防两个回潮口：
①有人把某个写点改成 `encoding="utf-8-sig"`；②新写点用 `utf-16` 系（PS5.1 `>` 重定向默认）。

## 四条

1. `test_repo_text_files_have_no_bom`：全仓文本文件（py/json/yaml/md/ini/toml/txt/cfg/env）
   逐一检查前 3 字节——含 `tests/`、`scripts/`、`knowledge/`、`examples/`、`flows/`、根目录；
2. `test_source_never_writes_bom_encoding`：`src/`+`scripts/` 源码里不得出现
   `encoding="utf-8-sig"` 的**写**调用（读侧允许——`read_jsonl_text`/`config` 两处正当用例）；
3. `test_source_avoids_utf16_writes`：写侧不得用 `utf-16`/`utf-16-le` 系编码（PS5.1 重定向
   的默认，会产 UTF-16 文件）；
4. `test_guard_scanned_floor`：正向对照下限（扫到的文件数 ≥200、写调用 ≥20），
   防「解析不出 = 恒绿」。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".md", ".ini", ".toml", ".txt",
                  ".cfg", ".env", ".jmx", ".md"}
_SKIP_DIRS = {".git", ".pytest_cache", ".ruff_cache", "build", "artifacts", "exports",
              "reports", ".venv", "node_modules", "__pycache__"}


def _bom_files() -> list[str]:
    found: list[str] = []
    for path in _repo_text_files():
        head = path.open("rb").read(3)
        if head == b"\xef\xbb\xbf":
            found.append(str(path.relative_to(_REPO)))
    return found


def _repo_text_files() -> list[Path]:
    files: list[Path] = []
    for path in _REPO.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def _code_sources() -> list[Path]:
    roots = [_REPO / "src", _REPO / "scripts"]
    return [p for root in roots for p in root.rglob("*.py")]


def test_repo_text_files_have_no_bom():
    found = _bom_files()
    assert not found, (
        "以下文件带 UTF-8 BOM（读侧有 utf-8-sig 兜底，但产物/配置文件交给别人时 BOM 仍是"
        "写侧缺陷；write_text(encoding='utf-8') 不会产 BOM）：\n" + "\n".join(found))


def _call_args(text: str, open_paren: int) -> str:
    """从 `(` 后一个字符开始，返回该调用的参数串（到配对 `)` 前为止）。"""
    depth = 1
    i = open_paren
    while i < len(text) and depth:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        i += 1
    return text[open_paren:i - 1]


def _open_mode(args: str) -> str:
    """从 `open(...)` 的参数串里认出 mode（缺省 'r'）。"""
    m = re.search(r"\bmode\s*=\s*['\"]([^'\"]*)['\"]", args)
    if m:
        return m.group(1)
    # 位置参数里的 mode 字面量：`open(p, "a", encoding=…)`——跳过第一个路径参数
    for lit in re.finditer(r"['\"]([^'\"]*)['\"]", args):
        val = lit.group(1)
        if val and set(val) <= set("rwaxbt+U"):
            return val
    return "r"


def _bom_write_offenders() -> list[str]:
    """源码里**写侧**用 `utf-8-sig` 的位置。

    **形态区分是必然的，不是放宽**（与同文件 `test_source_avoids_utf16_writes` 同一口径）：
    本波给 JSON 读侧加 BOM 安全必需 `open(path, "r", encoding="utf-8-sig")`，它是**读**——
    写成写侧缺陷会让守卫禁掉正确的修法，反过来逼人放弃 BOM 安全。缺陷的定义始终是
    「用 utf-8-sig **写**」（会产 BOM），读侧本就是为兼容别人写的 BOM。

    因此按**调用形态**判：`write_text`/`write_bytes` 命中即违规；`open(...)` 还必须
    mode 含 `w`/`a`/`x`/`+` 才算。
    """
    offenders: list[str] = []
    for path in _code_sources():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\b(write_text|write_bytes|open)\s*\(", text):
            name = m.group(1)
            args = _call_args(text, m.end())
            if not re.search(r'encoding\s*=\s*["\']utf-8-sig["\']', args):
                continue
            if name == "open" and not set(_open_mode(args)) & set("wax+"):
                continue          # 读模式的 open：BOM 兼容正是它该做的事
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.relative_to(_REPO)}:{line}: {name}(…utf-8-sig…) 写侧")
    return offenders


def test_source_never_writes_bom_encoding():
    offenders = _bom_write_offenders()
    assert not offenders, "写侧用 utf-8-sig（会产 BOM，交给别人的产物不该带）：\n" + "\n".join(offenders)


def test_bom_write_guard_still_catches_writes():
    """守卫**自证**：形态区分不能变成「啥也不拦」——真写样本必须被抓。

    前一条钉「现有源码无违规」，这条钉「判据本身有牙齿」。两者缺一：只靠前者，
    把 `_open_mode` 改成恒返 'r' 也能全绿（恒绿守卫比没守卫更坏）。
    """
    assert _bom_write_offenders() == []
    # 直接验判据函数：三个写形态都要点名，两个合法读形态都要放行
    cases = {
        'p.write_text(d, encoding="utf-8-sig")': True,
        'p.write_bytes(d) if False else open(p, "a", encoding="utf-8-sig")': True,
        'open(p, mode="x", encoding="utf-8-sig")': True,
        'open(p, "r", encoding="utf-8-sig")': False,
        'p.read_text(encoding="utf-8-sig")': False,
    }
    for snippet, should_flag in cases.items():
        flagged = False
        for m in re.finditer(r"\b(write_text|write_bytes|open)\s*\(", snippet):
            name, args = m.group(1), _call_args(snippet, m.end())
            if not re.search(r'encoding\s*=\s*["\']utf-8-sig["\']', args):
                continue
            if name == "open" and not set(_open_mode(args)) & set("wax+"):
                continue
            flagged = True
        assert flagged is should_flag, f"形态判据错：{snippet!r} 期望命中={should_flag}"


def test_source_avoids_utf16_writes():
    offenders = []
    for path in _code_sources():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'encoding\s*=\s*"utf-16(?:-\w+)?"', text):
            # `config._read_dotenv_pairs` 的 utf-16 是**读侧 BOM 探测分支**（.env 由用户手工/
            # PS5.1 产出，读侧必须兼容）——只有**写**侧用它才是缺陷。该合法分支的形态是
            # 「encoding = <字符串>」赋值给局部变量、随后 `.decode(encoding)` 消费；写侧
            # 形态是 `write_text(..., encoding="utf-16…")`。按**调用形态**区分：
            # 同一行里 encoding 字面量与 write/open(a*/w*) 同现才算写侧。
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            line = text[start:end if end > 0 else len(text)]
            if re.search(r"\b(write_text|write_bytes|\.open\(\s*[^\n]*['\"][aw])", line):
                offenders.append(f"{path.relative_to(_REPO)}: {line.strip()}")
    assert not offenders, "写侧出现 utf-16 系编码（会产 UTF-16 文件）：\n" + "\n".join(offenders)


def test_guard_scanned_floor():
    files = _repo_text_files()
    assert len(files) >= 200, f"只扫到 {len(files)} 个文本文件，判据可能失效"
    writes = 0
    for path in _code_sources():
        writes += len(re.findall(r"write_text\(", path.read_text(encoding="utf-8")))
    assert writes >= 20, f"只数到 {writes} 个 write_text 调用，判据可能失效"
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


def test_source_never_writes_bom_encoding():
    offenders = []
    for path in _code_sources():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'(write_text|write_bytes|open)\((?:(?!\)).)*encoding\s*=\s*"utf-8-sig"', text):
            offenders.append(f"{path.relative_to(_REPO)}: BOM 写入")
    assert not offenders, "\n".join(offenders)


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
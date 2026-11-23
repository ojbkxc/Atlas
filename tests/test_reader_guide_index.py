"""文档索引守卫：`docs/reader-guide.md` 是接手者的**第一张地图**，不许漏行。

## 挡的是什么（2026-11-23 补 `docs/lessons-artifact-side.md` 时发现）

`docs/reader-guide.md` 的「文档全景」表是新人找文档的**唯一索引**。AGENTS §5 写了维护义务
（「新增/移动文档后请在本页补一行」），但此前**零机器检查** —— 与 `CLAUDE.md` 模块表漏 14 个
模块同族（`tests/test_doc_module_table.py` 已钉那一面）：地图缺格比地图画错更常见、更难发现。

## 三条

1. `test_every_docs_md_is_indexed`：`docs/*.md` 除 `reader-guide.md` 自身外，每个文件都必须
   在索引表里出现（链接形态 `(名字.md)`）；
2. `test_index_entries_point_to_real_files`：索引里每个 `(xxx.md)` 必须真存在（死条目比缺条目
   更误导——点名不存在的文档让人怀疑自己看错了版本）；
3. `test_guard_has_positive_floor`：正向对照下限（表至少 6 行、`docs/` 至少 6 个 md），
   防止「解析不出来 = 恒绿」。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DOCS = _REPO / "docs"
_GUIDE = _DOCS / "reader-guide.md"

_LINK = re.compile(r"\]\(([^)#]+\.md)\)")


def _guide_text() -> str:
    return _GUIDE.read_text(encoding="utf-8")


def test_every_docs_md_is_indexed():
    text = _guide_text()
    linked = {Path(m).name for m in _LINK.findall(text)}
    missing = sorted(p.name for p in _DOCS.glob("*.md")
                     if p.name != _GUIDE.name and p.name not in linked)
    assert not missing, (
        "docs/ 下这些文档没进 reader-guide 索引（AGENTS §5 维护义务，新文档必须补一行）：\n"
        + "\n".join(missing))


def test_index_entries_point_to_real_files():
    text = _guide_text()
    targets = [m for m in _LINK.findall(text) if not m.startswith(("http", "../", "gaps/", "ownership/"))]
    dead = sorted({t for t in targets if not (_DOCS / t).exists()})
    assert not dead, f"reader-guide 索引指向不存在的文件（死条目）：{dead}"


def test_guard_has_positive_floor():
    rows = [ln for ln in _guide_text().splitlines() if ln.strip().startswith("| [")]
    assert len(rows) >= 6, f"只解析出 {len(rows)} 行索引，判据可能失效"
    assert len(list(_DOCS.glob("*.md"))) >= 6
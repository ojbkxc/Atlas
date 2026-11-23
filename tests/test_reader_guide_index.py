"""文档索引守卫（v2）：`docs/reader-guide.md` 是接手者的**第一张地图**，不许漏行、不许死链。

## v1 挡的（2026-11-23 补 lessons 文档时发现）

索引表是新人找文档的**唯一索引**，AGENTS §5 写了维护义务但**零机器检查**——与 CLAUDE 模块表
漏 14 个模块同族：地图缺格比画错更难发现。v1 实测漏 8 份并补齐 + 守卫。

## v2 修的（2026-11-24 CI linux/3.13 Offline gate **真翻车**：bare clone 上 5 个死条目）

v1 的「索引条目必须指向真实文件」用**本地文件系统**判存在。但 `.gitignore` 把五份迁移文档
标为 sensitive（`/docs/migration-*.md`、`legacy-adapter-setup.md` 等——真实账号/内网域名，
只活在受控磁盘不进仓库）：**本地全绿（3177 passed）、bare clone 当场红**。判「文件存在」
必须说清口径（本会话「结论绑来源」纪律的文档版）：守卫的读者是「下一个 bare clone 的人」，
死条目判据必须按 **git tracked 面**。五份 sensitive 条目在索引里**保留**（受控磁盘上的完整
地图对内部读者有价值），tracked 判据下它们对 CI「不存在」是预期而非漂移。

## 三条

1. `test_every_docs_md_is_indexed`：本地 `docs/*.md` 每份进索引（含 sensitive 本地副本）；
2. `test_index_entries_point_to_real_files`：死条目按 **git tracked 面**判（v2 核心）；
3. `test_guard_has_positive_floor`：正向下限（≥6 行 / ≥6 份）。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DOCS = _REPO / "docs"
_GUIDE = _DOCS / "reader-guide.md"

_LINK = re.compile(r"\]\(([^)#]+\.md)\)")


def _tracked_docs() -> set[str]:
    """`.gitignore` 里五份迁移文档被列 sensitive（真实账号/内网域名；AGENTS 交付约定同口径），
    只活在受控磁盘、不进仓库。守卫的死条目判据必须以 **tracked 面**为准——否则本地全绿、
    bare clone（CI/新机器）红（2026-11-24 CI 实测翻车形态）。判据单点在此。

    敏感本地文件清单与 `.gitignore` 的 `/docs/<name>.md` 行**逐条锁步**：本地有本地副本
    （索引条目保留），bare clone 上允许缺席（CI「不存在」是预期而非漂移）。"""
    import subprocess
    out = subprocess.run(["git", "ls-files", "docs"], cwd=_REPO, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    tracked = {Path(line).name for line in out.stdout.splitlines() if line.endswith(".md")}
    # sensitive：.gitignore 的 /docs/<name>.md 行**本身**就是「这条索引条目合法」的登记——
    # 被索引且被 .gitignore 登记 = 有意不入库的受控文档（bare clone 上读不到是预期）。
    # 交集**不**再要求本地存在：bare clone 的守卫读者没有本地副本，仍须认这些条目合法
    #（否则又回到 v1 的「本地绿 / CI 红」形态）。
    gitignore = (_REPO / ".gitignore").read_text(encoding="utf-8")
    sensitive = {Path(line.strip()).name for line in gitignore.splitlines()
                 if line.strip().startswith("/docs/") and line.strip().endswith(".md")}
    return tracked | sensitive


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
    tracked = _tracked_docs()
    dead = sorted({t for t in targets if Path(t).name not in tracked})
    assert not dead, (
        f"reader-guide 索引指向仓库里不存在的文件（死条目；注意 .gitignore 的 sensitive 迁移文档"
        f"只活在受控磁盘——引用它们要么去掉，要么确认所有人都持有本地副本）：{dead}")


def test_guard_has_positive_floor():
    rows = [ln for ln in _guide_text().splitlines() if ln.strip().startswith("| [")]
    assert len(rows) >= 6, f"只解析出 {len(rows)} 行索引，判据可能失效"
    assert len(list(_DOCS.glob("*.md"))) >= 6
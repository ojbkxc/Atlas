"""**单文件注入预算**守卫：`AGENTS.md` 不得超 65536 字节（超了会被静默截断）。

## 挡的是什么（2026-11-24 实测：§3 架构不变量被截掉）

本仓 `AGENTS.md` 靠「§2 里逐波追加根因」积累，到 2026-11-24 长到 **95309 字节**。工作区指令
注入有 **65536 字节预算**，超了从中间截断——实测被截到 65175，**从「§3 架构不变量」开始
整段消失**（`§4 Ask first`、`§5 文档维护` 也没了）。

后果比缺历史严重得多：注入的 AGENTS.md 是**每轮都读**的硬约束，被截掉的部分**看起来就是
不存在**——AI/新人不会知道自己少读了 §3 那 20 条架构不变量（单点收敛、原子 IO、终态家族…），
而截断本身**零告警**。

## 修法

§2 的过程叙事（264 条波次条目、36KB）搬到 `docs/verification-history.md`，AGENTS.md 只留
**活约束 + 四处计数锚点 + 指针**：94530 → **59376 字节**（余量 ~6KB）。

## 三条

1. `test_agents_md_within_injection_budget`：`AGENTS.md` ≤ 65536 字节；
2. `test_critical_sections_present`：§0/§1/§2/§3/§4/§5 六个标题必须都在（截断通常吃掉尾部，
   而尾部正是 §3~§5——直接钉「节在不在」比只量字节更贴近危害）；
3. `test_history_doc_exists_and_is_indexed`：历史文档存在、被 reader-guide 索引、
   且含当前计数（防止「搬走了但指针断」）。

> 增长时怎么办（写在这里，别只在 commit message 里）：新增波次**先写
> `docs/verification-history.md`**，AGENTS.md §2 只更新计数锚点与一句话摘要；需要新增强制
> 约束时才动 AGENTS.md，且动完跑这条守卫。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_AGENTS = _REPO / "AGENTS.md"
_HISTORY = _REPO / "docs" / "verification-history.md"
_BUDGET = 65536
_SECTIONS = ("## 0. ", "## 1. ", "## 2. ", "## 3. ", "## 4. ", "## 5. ")


def _size(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").encode("utf-8"))


def test_agents_md_within_injection_budget():
    size = _size(_AGENTS)
    assert size <= _BUDGET, (
        f"AGENTS.md {size} 字节超出注入预算 {_BUDGET}——超了会被**静默截断**，"
        f"2026-11-24 实测从 §3 架构不变量开始整段消失（§4/§5 也没了）。"
        f"新增波次请写 docs/verification-history.md，AGENTS.md 只更新计数与摘要。")


def test_critical_sections_present():
    text = _AGENTS.read_text(encoding="utf-8")
    missing = [head for head in _SECTIONS if head not in text]
    assert not missing, (
        f"AGENTS.md 缺这些节：{missing}——截断通常吃掉尾部（§3 架构不变量 ~20 条硬约束、"
        f"§4 Ask first、§5 文档维护），而这些是每轮都要读的硬约束")


def test_history_doc_exists_and_is_indexed():
    assert _HISTORY.is_file(), "波次历史文档不在（§2 的指针会断）"
    guide = (_REPO / "docs" / "reader-guide.md").read_text(encoding="utf-8")
    assert "verification-history.md" in guide, "历史文档没进 reader-guide 索引"
    hist = _HISTORY.read_text(encoding="utf-8")
    agents = _AGENTS.read_text(encoding="utf-8")
    count = re.search(r"当前基线：\*\*(\d+) passed\*\*", agents)
    assert count, "AGENTS §2 的计数锚点格式变了（test_doc_verification_count 会红）"
    assert count.group(1) in hist, (
        f"历史文档里没有当前计数 {count.group(1)}——指针在但内容没跟上")

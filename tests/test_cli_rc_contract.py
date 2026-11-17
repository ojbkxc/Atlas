"""CLI 退出码契约：README 声明的 rc 表必须与实际代码路径一致。

## 为什么需要

README 有一张 8 行的「退出码按子命令读，不跨命令并表」表（约 README:554-563）。
它是**给 CI 和调度器看的契约**——`atlas scheduling` 自己的巡检、CI 的 pass/fail 判定、
批处理脚本的「要不要继续下一条」分支，全都按这些数字走。

但这张表此前**没有任何机器检查**：它是手写的散文，代码改了 rc 它不会红。而本仓已经
因为「同名不同义会误导 CI」专门写过一段说明（README 原文），说明作者知道这件事危险——
只是没把它变成判据。

## 判据

对每个在 README 表里声明过的 rc 值，源码里必须**真的存在**产生它的路径；
且反过来，`cli.py` 里出现的 `return <n>`（n 为 3 位数以内的正整数常量）必须全部
在表里被声明过（否则就是「代码悄悄新增了一个退出码，文档和消费方都不知道」）。

## 刻意不做的事

- **不跑子命令去实测 rc**：那需要真实环境/凭据，且每条命令的 rc 取决于输入合法性，
  端到端跑一遍既慢又不可判定。这里验的是「契约表 ↔ 代码字面量」的一致性，
  行为正确性由各自的功能测试负责（`test_preflight_single_point.py` 等）。
- **不解析表达式的值**：只收集整数常量形态的 `return`。`return 0 if ok else 1`
  这种由行为测试背书，静态不猜。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "src" / "atlas" / "cli.py"
README_PATH = REPO_ROOT / "README.md"


def _declared_rc_from_readme() -> set[int]:
    """从 README 的 rc 表里取所有声明过的退出码。

    表行形如 `| `atlas run` | 0 / 1 / 2 | 通过 / ... |`，rc 列是 `0 / 1 / 2` 或 `**3**`。
    取表格第 2 列（rc 列）里的所有整数。

    注意：这张表**嵌在一个列表项里**，每行前面有两个空格缩进（README:558 起）。
    按行首 `|` 匹配会一行都取不到——所以先 strip 再判。
    """
    text = README_PATH.read_text(encoding="utf-8")
    start = text.index("| 子命令 | rc |")
    # 表尾：第一条不是表格形状的行
    lines = text[start:].splitlines()
    block: list[str] = []
    for line in lines:
        cur = line.strip()
        if not cur.startswith("|"):
            if block:
                break
            continue
        block.append(cur)
    assert len(block) >= 8, f"rc 表只抓到 {len(block)} 行——表格式可能被改写，解析需同步"

    codes: set[int] = set()
    for row in block:
        if set(row) <= set("|- "):     # 分隔行 |---|---|
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 2 or "子命令" in cells[0]:
            continue
        for n in re.findall(r"\b(\d{1,3})\b", cells[1]):
            codes.add(int(n))
    return codes


def _return_constants(path: Path) -> dict[int, list[int]]:
    """收集 `return <int常量>` 的行号（按值分组）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[int, list[int]] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)
                and not isinstance(node.value.value, bool)):
            found.setdefault(node.value.value, []).append(node.lineno)
    return found


def test_readme_rc_table_parses():
    """正向对照：表必须解析出东西，否则下面两条恒真。"""
    codes = _declared_rc_from_readme()
    assert len(codes) >= 6, f"从 README rc 表里只解析出 {sorted(codes)}——表格式可能被改写，正则需同步"
    # 这几条是本仓长期承诺的核心约定，缺任何一个都说明表被改坏了
    assert {0, 1, 2} <= codes, f"rc 表缺核心约定 0/1/2：{sorted(codes)}"
    assert 3 in codes, f"rc 表缺「等人=3」这条（C9 显态）：{sorted(codes)}"


def test_every_declared_rc_exists_in_code():
    """表里声明的每个 rc，源码必须真有产生它的路径。

    缺了就是「文档承诺了一个代码不产生的退出码」——消费方按它写分支永远等不到。
    """
    declared = _declared_rc_from_readme()
    actual = set(_return_constants(CLI_PATH))
    # 130/124 在 _case_timeout_child_argv 的超时看护里，也在 supervisor 的 __main__ 面
    missing = sorted(declared - actual)
    assert not missing, (
        f"README rc 表声明了 {missing}，但 cli.py 里没有 `return {missing[0]}` 这样的路径——"
        f"文档在承诺代码不产生的退出码。代码实际出现的 rc 常量：{sorted(actual)}")


def test_no_undeclared_rc_in_code():
    """反向：代码里出现的 3 位以内正整数 `return`，必须都在表里声明过。

    这条抓「悄悄新增退出码」：代码多了一个 `return 4`，而 README、CI 配置、调度器的
    分支判断全都不知道它 —— 消费方会把它当「非 0 的未知失败」处理，或更糟，当成
    `rc != 2` 于是"重试"一条输入错误的命令。
    """
    declared = _declared_rc_from_readme()
    actual = _return_constants(CLI_PATH)
    undeclared = sorted(set(actual) - declared)
    detail = "; ".join(f"rc={n} 在 cli.py:{actual[n]}" for n in undeclared)
    assert not undeclared, (
        f"cli.py 返回了 README rc 表未声明的退出码：{detail}——"
        "新增退出码必须同时更新 README 的 rc 表（消费方按它写分支），"
        "否则等于悄悄改变了 CLI 契约")


def test_rc_3_meanings_are_not_merged():
    """`rc 3` 在两处含义不同（等人 / gen 结构自检失败），README 明确要求**不并表**。

    这条钉住那条说明仍在文档里——它是本仓唯一提醒「同名不同义会误导 CI」的地方。
    松掉它，下一个人很容易把两义合并成「3 = 需要人」。
    """
    text = README_PATH.read_text(encoding="utf-8")
    assert "别并表" in text or "不并表" in text, (
        "README 里「rc 3 两义不并表」的说明消失了——它是防止两人误读同一退出码的唯一提示")
    # 两处 3 的来源都要在代码里能找见
    src = CLI_PATH.read_text(encoding="utf-8")
    assert "WAITING-HUMAN" in src, "run 轨的「等人」显态标记不见了"
    assert "结构自检失败" in src, "gen 轨的「结构自检失败」文案不见了"
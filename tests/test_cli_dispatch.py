"""CLI 分派表守卫：`atlas` 的子命令必须**恰好**被分派一次，且签名与表登记一致。

## 为什么需要

`main()` 改造前 473 行、27 个 `if args.command == "xxx":` 串联，其中 run/doctor/agent/legacy
四块占一半以上行数，其余全是「一句转发」——真正的语义边界被淹没在分派噪音里。

2026-11-16 的结构治理把每个分支抽成独立 handler，`main()` 收到 102 行，剩下的分派集中到
一张 `_DISPATCH` 表。抽取是**机械的**（脚本按 AST 剪切粘贴主体、不改一个字符的判据），
但机械重构最容易出的错不是逻辑而是**登记错误**：

- 某个命令忘了进表 → 它静默落到末尾 `return 2`，用户看到「rc 2 但什么都没输出」；
- 表里多一条已删的命令 → 死条目，下次有人照它加分支就重复；
- 表登记的参数与 handler 签名不符 → 运行期 TypeError（比前两条晚、更难查）。

这三条都不需要跑业务逻辑就能判，所以放在测试里而不是靠人核。

## 判据

1. `_parser()` 能产出的每个顶层子命令，`main()` 都必须有路径处理（表里或显式分支里）；
2. 表里每条 handler 的形参名与表登记的 `wanted` **逐位相等**（`(args, *wanted)`）；
3. 表里没有 parser 产不出的命令（无死条目）。
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "src" / "atlas" / "cli.py"


def _tree() -> ast.Module:
    return ast.parse(CLI_PATH.read_text(encoding="utf-8"))


def _main_fn(tree: ast.Module) -> ast.FunctionDef:
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "main":
            return n
    raise AssertionError("cli.py 里找不到 main()")


def _dispatch_table(tree: ast.Module) -> dict[str, tuple[str, tuple[str, ...]]]:
    """解析 `_DISPATCH = {cmd: (handler, (argname, ...))}` 字面量。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_DISPATCH":
            out = {}
            for k, v in zip(n.value.keys, n.value.values):
                assert isinstance(k, ast.Constant), "分派表的键必须是字面量命令名"
                fn = v.elts[0]
                wanted = v.elts[1]
                assert isinstance(fn, ast.Name), "分派表的值必须是模块级函数名"
                names = tuple(x.value for x in wanted.elts) if isinstance(wanted, ast.Tuple) else ()
                out[k.value] = (fn.id, names)
            return out
    raise AssertionError("cli.py 里找不到 _DISPATCH 分派表")


def _parser_commands() -> set[str]:
    """跑真 parser 取顶层子命令集合（不靠源码正则——argparse 结构才是真相）。"""
    from atlas.cli import _parser

    parser = _parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("parser 里找不到子命令")


def _handled_in_main(tree: ast.Module) -> set[str]:
    """`main()` 里以 `if args.command == "x"` 形式显式处理的命令（表之外的）。"""
    out: set[str] = set()
    for node in ast.walk(_main_fn(tree)):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute)
                and node.left.attr == "command" and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Constant)
                and isinstance(node.comparators[0].value, str)):
            out.add(node.comparators[0].value)
    return out


def test_dispatch_table_covers_every_subcommand():
    """每个子命令都必须有处理路径——否则它会静默落到末尾 `return 2`。

    `init` / `config` 是**有意**留在 `main()` 里的显式分支（它们在 AtlasConfig/plugin
    装配之前就要 return，进表会强迫先装配一个还不存在的项目根），所以判据接受
    「表里 **或** main 显式分支里」二者之一。
    """
    tree = _tree()
    table = set(_dispatch_table(tree))
    explicit = _handled_in_main(tree)
    covered = table | explicit
    commands = _parser_commands()
    missing = sorted(commands - covered)
    assert not missing, (
        "下列子命令既不在 _DISPATCH 表里、也没有 main() 的显式分支——"
        f"它们会静默落到 `return 2`（rc 2 且无任何输出，最难查的失败形态）：{missing}")


def test_dispatch_table_has_no_dead_entries():
    """表里不许有 parser 产不出的命令（死条目）。

    死条目本身不炸，但它是「照它加代码」的陷阱：下一个人看到 `"foo": (_foo, ...)`
    会以为 `atlas foo` 存在。命令删了就把条目一起删。
    """
    tree = _tree()
    table = set(_dispatch_table(tree))
    commands = _parser_commands()
    dead = sorted(table - commands)
    assert not dead, (
        f"_DISPATCH 表里有 parser 产不出的命令（死条目）：{dead}——"
        "命令已删/改名的话请同时删条目")


def test_dispatch_signatures_match_handlers():
    """表登记的 `wanted` 必须与 handler 形参**逐位相等**：`(args, *wanted)`。

    这条防的是抽取那波最可能的机械错：把 handler 写成 `(args, config, plugin)` 却在表里
    只登记 `("config",)`——参数传错会在**调用时** TypeError，比签名检查晚、且只在真正
    跑到那条命令时才暴露（离线全量测试很容易漏掉某条命令的端到端）。
    """
    tree = _tree()
    table = _dispatch_table(tree)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    bad: list[str] = []
    for cmd, (fname, wanted) in sorted(table.items()):
        assert fname in funcs, f"_DISPATCH[{cmd!r}] 指向不存在的函数 {fname}"
        params = tuple(a.arg for a in funcs[fname].args.args)
        expected = ("args",) + wanted
        if params != expected:
            bad.append(f"{cmd}: handler {fname}{params} vs 表登记 {expected}")
    assert not bad, "分派表与实际签名不符：\n  " + "\n  ".join(bad)


def test_main_is_short_and_has_no_giant_branch():
    """`main()` 保持结构性短小（改造前 473 行）。

    这条不是风格洁癖：main 一旦重新长回几百行，就意味着有人又把业务逻辑塞回分派层，
    「一个子命令的完整语义」再次被拆散在 `main()` 与 handler 两处。上限取 160 行
    （当前 102 行，留出注释与新增命令的余量，但挡住「又堆回去」）。
    """
    fn = _main_fn(_tree())
    lines = fn.end_lineno - fn.lineno + 1
    assert lines <= 160, (
        f"main() 已增长到 {lines} 行（上限 160）——"
        "新子命令请抽成 handler 并登记进 _DISPATCH，不要内联回 main()")


def test_every_handler_is_registered_or_explicit():
    """反向：命令 handler 不应成为孤儿（定义了却没人调用）。

    孤儿的典型成因是「抽出来了但忘了删旧分支/忘了加表」，会留下两份实现路径。
    **判据放宽到「全文件任意位置被调用」**而不是只查 main()：像 `_explore_auto` 这种
    是 `_explore` 的内部子分支（`explore --auto`），`_schedule_add_spec` 是 `_schedule`
    的辅助——它们不是顶层命令 handler，不该被要求进分派表。只查「完全没人调用」。
    """
    tree = _tree()
    table = _dispatch_table(tree)
    registered = {fname for fname, _ in table.values()}
    all_called = {x.func.id for x in ast.walk(tree)
                  if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)}
    referenced = {x.id for x in ast.walk(tree)
                  if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
    orphans = []
    for n in tree.body:
        if not isinstance(n, ast.FunctionDef):
            continue
        name = n.name
        if not name.startswith("_") or name in registered:
            continue
        if name in all_called or name in referenced:
            continue
        orphans.append(name)
    assert not orphans, (
        f"这些 `_` 函数在 cli.py 里没有任何引用（死代码）：{orphans}")
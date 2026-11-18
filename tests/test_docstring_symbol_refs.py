"""文档里 `_xxx` 形式的符号引用必须真的存在——防止「注释指向一个不存在的实现」。

## 为什么需要（2026-11-18 结构审计）

本仓的注释风格是**用名字指向唯一实现**（#92 单点纪律的语言层体现）：
「判据唯一在 `atlas.ui_family`」「调用方（dsl `_ReconcileAssert.run`）」「落盘口径见
`atomic_io.atomic_write_text`」。这种写法在**名字正确时**是极高价值的路标——读者
grep 一下就找到真源。

但它有个前提：**名字必须还在**。名字一旦因重命名/重构失效，注释就变成一条**假的
路标**：读者照它去 grep，找不到，然后开始怀疑是自己搜错了，或者以为代码被删了。
比「没有注释」更坏——没有注释至少不会骗人。

实测（2026-11-18）扫出 3 处真失效：

| 位置 | 写的 | 实际 |
|---|---|---|
| `failures.py:285` | dsl `_assert_reconcile` | 类 `_ReconcileAssert`（方法 `.run`）|
| `legacy_fast.py:641` | 传 `_RULE_FALSE_JS` | 本模块无此常量（只有 `_RULE_KINDS`/`_RULE_MASK_*_JS`）|
| `runtime.py:278` | CLI 预检 `_precheck_session_inputs` | 真名 `precheck_session_inputs`（无前导下划线）|

第 3 条是我自己上一波留下的：把散落的预检序列收敛成 `precheck_session_inputs` 时，
`resolve_account` 的 docstring 里那个**旧的、带下划线的**名字没跟着改。也就是说
**这类漂移连「刚刚亲手改过的人」都会漏**——这正是它需要机器检查的理由。

## 判据

只查**下划线开头的私有标识符**（`` `_xxx` ``）：公有名噪音太大（内建异常
`PermissionError`、外部 `fsync`/`flock`、JSON 键 `fallbacks`、业务任务名 `demo_api`
全都会被误报）。私有标识符则几乎必然是「本仓某个实现」——指错了就是真错。

## 白名单（两类合法外部引用，逐条写明理由）

1. **旧仓（`D:/GitHub/UI AUTO TEST`）的符号**——本仓注释会对照旧实现说明差异
   （`_case_watchdog`、`_save_fail_screenshot`）。它们本来就不该在本仓存在。
2. **产物侧符号**——`_atlas_fast` / `_fast_runlog` / `_FAST_*` 是**生成物**里的名字，
   不在 `src/` 里定义（模板常量另算：`_FAST_SUPPORT_SOURCE` 这类确实在仓里）。

白名单是**显式登记 + 逐条理由**，不是「扫到不认识就跳过」——后者等于没检查。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "atlas"

# 只匹配反引号包住的私有标识符（含点号后缀时不取整串，只取首段）
_PRIVATE_REF = re.compile(r"`(_[A-Za-z][A-Za-z0-9_]*[A-Za-z0-9])`")

# 合法外部引用：本仓**不该**定义它们。每条都要能说清来源。
_EXTERNAL = {
    # 旧仓 UI AUTO TEST 的符号（本仓注释用来对照旧实现的行为差异）
    "_case_watchdog": "旧仓 conftest 的用例看门狗（engine.py 注释对照用）",
    "_save_fail_screenshot": "旧仓 runner 的失败截图落盘（fastplugin.py 注释对照用）",
    # 产物侧符号：生成出来的文件里的名字，不在 src/ 定义
    "_atlas_fast": "快包产物底座模块名（生成物，不在 src 定义）",
    "_atlas_support": "pytest 包产物底座模块名（生成物）",
    "_fast_runlog": "快包产物 conftest 里的 fixture 名（生成物）",
    "_fast_excinfos": "产物 conftest 挂在 item 上的属性名（运行期动态挂载）",
    "_fast_runlog_entered": "同上：产物 conftest 的进场标记属性",
    "_write_gate": "快包产物 conftest 的写闸 fixture 名（生成物）",
    # 非符号引用：是**键名 / 目录名 / 名字前缀**，不是本仓的私有实现
    "_data": "ctx 字典里的引擎内部键前缀（运行时数据键，不是符号，dsl.py 注释在讲键约定）",
    "_FAIL": "Allure attach 的名字前缀形态 `step<N>_FAIL_*.png`（文件命名约定，不是符号）",
    "_common": "通用知识包目录名 `plugins/_common/knowledge`（路径片段，不是符号）",
}


def _defined_symbols() -> set[str]:
    """全仓 `src/atlas` 里定义的符号名（函数/类/变量/import 名）。"""
    out: set[str] = set()
    for p in SRC.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.Assign):
                for tg in n.targets:
                    if isinstance(tg, ast.Name):
                        out.add(tg.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
            elif isinstance(n, (ast.ImportFrom, ast.Import)):
                for a in n.names:
                    out.add(a.asname or a.name.split(".")[0])
            # 函数参数/局部变量也算「存在」：注释里可能引用它们
            elif isinstance(n, ast.arg):
                out.add(n.arg)
    return out


def _docstring_texts(path: Path) -> list[str]:
    """模块 + 所有函数/类的 docstring 文本。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    texts = [ast.get_docstring(tree) or ""]
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(n)
            if d:
                texts.append(d)
    return texts


def _stale_references() -> list[tuple[str, int, str]]:
    """返回 [(相对路径, 行号, 符号名)]。

    行号取该 docstring 的起始行（足够定位是哪个函数的文档）。
    """
    defined = _defined_symbols()
    stale: list[tuple[str, int, str]] = []
    for p in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            lineno = getattr(node, "lineno", 1)
            for m in _PRIVATE_REF.finditer(doc):
                name = m.group(1)
                if len(name) < 4:
                    continue          # `_x` / `_ab` 这类太短，多为占位或缩写
                if name in defined or name in _EXTERNAL:
                    continue
                stale.append((str(p.relative_to(SRC)), lineno, name))
    return stale


def test_guard_has_real_coverage():
    """正向对照：扫描必须真扫到东西，否则「无失效引用」是恒真。

    下限取 60：2026-11-18 实测 84 处（正则要求名字以字母数字结尾，排除 `_FAST_` 这类
    前缀片段）。取一个明显低于实际值的下限，既能挡住「AST/正则写错导致扫不到」，
    又不会因为正常删改而频繁假红。
    """
    defined = _defined_symbols()
    assert len(defined) > 500, f"符号表过小（{len(defined)}）——AST 收集本身失效了"

    total_refs = 0
    for p in SRC.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        for text in _docstring_texts(p):
            total_refs += sum(1 for m in _PRIVATE_REF.finditer(text) if len(m.group(1)) >= 4)
    assert total_refs >= 60, (
        f"只扫到 {total_refs} 处私有符号引用——扫描面异常（正则或 docstring 提取坏了？）")

    # 白名单里的名字不该在本仓被定义（否则该条目已过期，应删掉）
    expired = sorted(name for name in _EXTERNAL if name in defined)
    assert not expired, (
        f"白名单里的名字现在在本仓**已定义**，条目过期了（会掩盖真检查）：{expired}——"
        "如果它们已搬进本仓，请从 _EXTERNAL 删掉")


def test_no_docstring_points_at_a_nonexistent_private_symbol():
    """核心判据：docstring 里 `` `_xxx` `` 指向的符号必须存在（白名单除外）。

    失效路标比没有路标更坏：读者 grep 不到时会以为代码被删了，而不是「注释旧了」。
    """
    stale = _stale_references()
    detail = "\n  ".join(f"{rel}:{ln} → `{name}`" for rel, ln, name in stale)
    assert not stale, (
        "下列 docstring 引用了**不存在的**私有符号（失效路标，比没有注释更坏）：\n  "
        + detail +
        "\n修法二选一：①把注释改成真实存在的名字；②若它确实来自本仓之外"
        "（旧仓符号 / 产物侧符号），登记进 _EXTERNAL 并写明来源。")


def test_external_allowlist_entries_carry_a_reason():
    """白名单必须逐条写理由——空理由的条目等于「扫到不认识就跳过」。"""
    for name, reason in _EXTERNAL.items():
        assert reason and len(reason) >= 8, (
            f"_EXTERNAL[{name!r}] 没有写清理由（会变成无脑豁免）：{reason!r}")

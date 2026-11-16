"""知识读层守卫：`load()`（影子层）还是 `load_composed()`（组合层），由机器判。

## 为什么需要这条

`KnowledgeBase` 有两个读方法（`knowledge.py:253` / `:270`），**类型上无法区分**
「读私有」和「读私有+通用包」。加组合层那一波（2535）之后，消费点一半走新层、一半
仍读旧层，被遮蔽的通用条目对后者**根本不报错，只是不存在**：

- `emit_pytest._default_components()` 读影子层 → 15 个通用组件对生成器不可见 →
  F-10 声明位闸把「用通用组件的合法 flow」判成「类名不存在」拒绝；
- `review_code` knowledge 模式同款 → MAINT-002 对用了通用组件的合法用例误报。

两处都在 2026-11-16（wave6, fb50531）才修，**此前 2984 测试全绿**——因为没有任何
一条断言说「这个读点必须走组合层」。

## 遮蔽风险的精确条件（两个都成立才危险）

1. 该名字**在通用包里存在**（`_common/knowledge/<name>.json`）——否则组合层无物可并；
2. 该名字**在平台层也存在**（`plugins/<domain>/knowledge/<name>.json`）——否则
   `load()` 的「第一个存在的根整份胜出」取到的就是唯一那份，不可能遮蔽。

只有两层都有的名字，`load()` 才会让通用条目**整份消失**。2026-11-16 实测满足的是
`components` / `domains` / `pitfalls` 三个（`ui_family` 只在通用包，平台无同名文件，
且由 `ui_family._table_path()` 直接按路径读，根本不经过 `load()`——不是遮蔽面）。

这两个集合都从**数据目录**算，不写死清单：往通用包或平台包加/删一个同名 json，
受守卫的名字集合当场变化（写死清单本身就是下一处漂移，F-15 同族教训）。

## 判据

对任一满足「两层都有」的名字，读路径必须走 `load_composed()`。`load()` 只在两类
地方合法：

1. **写路径**：读私有 → 改 → 整份写回私有库。用组合层会把通用条目复制进私有库
   污染资产（红线 #11）。逐点登记在 `_SHADOW_WRITE_PATHS`，且白名单本身有活性检查。
2. **不满足上面两个条件之一**：不存在遮蔽，`load()` 语义正确（此时 `load_composed`
   逐字退化为 `load`，见 `knowledge.py:272`）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from atlas.plugin_api import common_knowledge_root

REPO_ROOT = Path(__file__).resolve().parents[1]


def _common_layer_names() -> set[str]:
    """通用知识包里实际存在的知识名——遮蔽风险的**必要条件之一**。"""
    root = common_knowledge_root()
    assert root.is_dir(), f"通用知识包目录缺失：{root}（package-data 漏打包？）"
    return {p.stem for p in root.glob("*.json")}


def _shadowable_names() -> set[str]:
    """真正会被遮蔽的知识名 = 通用包有 ∩ 至少一个平台包也有。

    只取内置插件目录（`src/atlas/plugins/*/knowledge`）；外部插件（`ATLAS_PLUGIN`
    指到仓外）在离线一次性扫描里无法枚举，其平台层同名文件**同样**会遮蔽——所以
    本判据对内置插件是全等的，对外部插件是「内置子集」：一种已知的覆盖边界，
    靠「新增平台插件时把目录加进这里」维持。这条边界写在此处而不是注释在别处。
    """
    common = _common_layer_names()
    platforms: set[str] = set()
    for d in (REPO_ROOT / "src" / "atlas" / "plugins").iterdir():
        # `_common` 是通用包自己的目录（就是 common 那一侧），不是平台层，必须排除——
        # 否则「两层同名」会把它自己算进来，把 ui_family 这类纯通用资产误判成遮蔽面。
        if not d.is_dir() or d.name == "_common":
            continue
        kdir = d / "knowledge"
        if kdir.is_dir():
            platforms |= {p.stem for p in kdir.glob("*.json")}
    return common & platforms


# 写路径白名单：这些读 `load()` 是**有意**的（读私有 → 改 → 写回私有）。
# 键 = (相对仓库路径, 所在函数名, 知识名)。值 = 为什么必须用影子层。
_SHADOW_WRITE_PATHS: dict[tuple[str, str, str], str] = {
    ("src/atlas/knowledge.py", "save_component_selector", "components"):
        "读私有组件库 → 合并新选择器 → 整份写回；组合层会把通用组件复制进私有库",
    ("src/atlas/knowledge.py", "save_overlay", "bu_overlays"):
        "BU 覆盖写回私有库；组合层污染同上",
    ("src/atlas/knowledge.py", "save_task_entry", "task_library"):
        "任务库写回私有库；组合层污染同上",
    ("scripts/pitfall_review.py", "run_report", "pitfalls"):
        "读私有→翻 status→整份写回；组合层会把通用包 82 条复制进私有库",
    ("scripts/pitfall_review.py", "run_import", "pitfalls"):
        "同上（导入去重需要私有库视角）",
    ("scripts/pitfall_review.py", "run_enable", "pitfalls"):
        "同上（就地翻 status 必须只写私有库条目）",
}


def _knowledge_reads() -> list[tuple[str, str, str, int]]:
    """扫 src/ 与 scripts/，收集全部 `*.load("<name>")` / `*.load_composed("<name>")`。

    返回 (相对路径, 所在函数名, 方法名, 行号, 知识名) 五元组的列表——
    只认「属性调用 + 首参字面量」这一个形态；`getattr(obj, "load")` 之类目前不存在，
    真出现时本扫描会漏，届时由人工评审兜（本条不追求覆盖不可静态判定的动态派发）。
    """
    reads: list[tuple[str, str, str, int, str]] = []
    targets = list((REPO_ROOT / "src").rglob("*.py")) + list((REPO_ROOT / "scripts").glob("*.py"))
    for path in targets:
        if "__pycache__" in str(path):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("load", "load_composed")
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    continue
                reads.append((rel, fn.name, node.func.attr, node.lineno, node.args[0].value))
    return reads


def test_guard_has_real_coverage():
    """正向对照：扫描必须真扫到东西，否则下面两条「无违规」是恒真。

    这一条防的是「守卫自己烂掉」——AST 条件写错后，违规检查会安静地全绿，
    看起来比修好之前还要正常。点数下限取 18（2026-11-16 实测 21 处读点：src 18 + scripts 3）。
    """
    reads = _knowledge_reads()
    assert len(reads) >= 18, f"扫描命中过少（{len(reads)}）——守卫本身可能已失效"
    methods = {r[2] for r in reads}
    assert methods == {"load", "load_composed"}, f"两种读法都要被扫到：{methods}"
    # 遮蔽名字集合必须非空，否则下面的核心判据无对象、恒真
    shadowable = _shadowable_names()
    assert shadowable >= {"components", "pitfalls"}, (
        f"两层同名的知识名集合异常：{sorted(shadowable)}")
    # 并反向钉住「不是所有通用包名字都算遮蔽」——ui_family 只在通用包，不是遮蔽面
    assert "ui_family" in _common_layer_names() - shadowable


def test_shadow_reads_on_common_names_are_only_registered_write_paths():
    """核心判据：会被遮蔽的知识名，读路径不许用 `load()`（写路径白名单除外）。

    违规形态 = 有人加了一层新数据源（组合层），但某个消费点仍读旧层 →
    新数据对它静默不可见。这是 wave6 修掉的那两处的同族第三人。
    """
    shadowable = _shadowable_names()
    violations = []
    for rel, func, method, lineno, name in _knowledge_reads():
        if method != "load":
            continue
        if name not in shadowable:
            # 不满足遮蔽条件（通用包没有它，或平台层没有同名文件）：影子层语义正确。
            continue
        if (rel, func, name) in _SHADOW_WRITE_PATHS:
            continue
        violations.append(f"{rel}:{lineno} {func}() → load({name!r})")
    assert not violations, (
        "下列读点对「两层同名（会被遮蔽）」的知识名用了影子层 load()——通用条目对它们"
        "不可见，且不会报错（wave6 同族缺陷）。改用 load_composed()，或登记进 "
        "_SHADOW_WRITE_PATHS 并写明为什么必须只读私有库：\n  " + "\n  ".join(violations))


def test_write_path_allowlist_is_not_dead():
    """反向对照：白名单里的每一项都必须对应一个真实存在的 `load()` 调用点。

    白名单只增不删是最常见的守卫腐化形态——调用点改名/删掉之后，条目还留着，
    下次有人在同一位置加了读操作，守卫会因为他「已在白名单里」而放行。
    """
    actual = {(rel, func, name) for rel, func, method, _ln, name in _knowledge_reads()
              if method == "load"}
    dead = sorted(set(_SHADOW_WRITE_PATHS) - actual)
    assert not dead, (
        "写路径白名单有失效条目（对应的 load() 调用点已不存在或已改名）：\n  "
        + "\n  ".join(f"{r}:{f}() load({n!r})" for r, f, n in dead))


def test_common_layer_names_are_actually_composed_when_on_read_paths():
    """组合层确实把通用条目交给了读路径——判据不是空转的。

    直接对比影子层与组合层的可见条数：若 `load_composed` 退化成 `load`（base_roots
    没接线），上面的守卫会全绿而问题依旧存在——这一条钉住「组合层真的组合了」。
    """
    from atlas.plugins.demo import DemoPlugin

    kb = DemoPlugin().knowledge()
    shadow = kb.load("components").get("components") or {}
    composed = kb.load_composed("components").get("components") or {}
    assert set(composed) >= set(shadow), "组合层必须包含影子层全部条目（靠前者赢）"
    assert len(composed) > len(shadow), (
        f"组合层没比影子层多任何组件（shadow={len(shadow)} composed={len(composed)}）"
        "——base_roots 未接线，通用包对读路径不可见")
    # pitfalls 同款：通用包 82 条必须对平台域可见
    assert len(kb.pitfalls("demo")) > len(kb.load("pitfalls").get("pitfalls") or [])


@pytest.mark.parametrize("name", sorted(_shadowable_names()))
def test_every_shadowable_name_has_a_composed_read_path(name):
    """会被遮蔽的每个知识名，至少有一个消费点走组合层。

    否则等于「通用包这份资产对全仓没有任何读路径可见」——数据是死的，`doctor` 不会报，
    测试不会红（ob-7 接入清单零消费方同族）。只对**会被遮蔽**的名字要求，因为
    `ui_family` 这类平台层无同名文件的资产由 `ui_family._table_path()` 直接按路径读，
    不经过 `load()`，强行要求一个 `load_composed("ui_family")` 是造需求不是修缺陷。
    """
    composed_readers = [r for r in _knowledge_reads()
                        if r[2] == "load_composed" and r[4] == name]
    assert composed_readers, (
        f"{name}.json 会被平台层同名文件遮蔽，但全仓没有一处 load_composed({name!r}) —— "
        f"通用包这份资产对读路径完全不可见（dead asset）")

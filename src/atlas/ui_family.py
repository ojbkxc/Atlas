"""UI 组件库族表：把「换平台后等待/读取判据的静默失效」变成**数据驱动**的显式判据。

族表是**知识（数据），不是散落在引擎里的 if/else**（AGENTS §0）：族→结构类/签名选择器/
has_mask 的事实住在通用知识包 `plugins/_common/knowledge/ui_family.json`；平台用自己
`components.json` 的**顶层 `family` 键**声明属哪个族（缺省 `element-ui`）。加一个新族 =
加一条数据，不改引擎。

本模块只做**薄加载器 + 默认值解析**，三件事：
- `resolve_family(name)` → `{name, signature, has_mask, prefixes}`（等待/生成/体检的唯一取值口）；
- `UiFamilyMismatch` —— 页面签名缺席时的点名异常（换平台后不再静默当成「已落定」）；
- `family_prefix_problems(...)` —— 声明族 vs 选择器前缀一致性（doctor / 生成期闸共用）。

**不在模块顶层 import 知识库**：`waits` 在调用点加载即可（产物模板零 import atlas，
自带固化副本；引擎侧正常安装恒有通用包）。判据单一（#92）：族名解析与族条目解析只此一处。
"""
from __future__ import annotations
import json
import re
from pathlib import Path

# 内置兜底族名（仅当通用包族表读不到/无 default 时用）。
DEFAULT_FAMILY = "element-ui"

# 内置兜底条目（仅当族表读不到时用；正常安装恒走知识表）。与知识表 element-ui 条目等值由
# tests/test_ui_family.py 的锁步测钉死——绝不让兜底与知识表各自漂移（#92）。
_BUILTIN_FALLBACK = {
    "signature": ".el-button, .el-table, .el-menu, .el-dialog__wrapper, .el-input__inner",
    "has_mask": True,
    "prefixes": [".el-"],
}

# 选择器里的 class token（`.el-button` / `.el-table__body` 里的 `.el-...`）——提取「框架前缀」
# = 点号 + 首个字母段 + 其后的一个短横线（`.el-button` → `.el-`，`.ant-btn` → `.ant-`）。
# 前缀**带点号**，与族表 `prefixes` 的字面形态一致（比对无需再补点，不留两处形态）。
_CLASS_TOKEN = re.compile(r"(\.[a-zA-Z][\w]*-)")


class UiFamilyMismatch(RuntimeError):
    """页面未呈现该族的 class 前缀特征（DOM 层）：等待判据（遮罩/行数）不可信。

    换平台后 `wait_for_selector(state="hidden")` 对「选择器不存在」恒满足——静默秒回
    「已落定」是比报错更坏的假绿（uf-1）。此异常把「这页根本不是本族」显式化，点名期望族与
    **前缀特征**（按 class token 边界判，不用子串：#102/#105 假绿同族的显式化，不猜、不静默放行）。
    `declared_in_table=False` 时另点名「族表缺该条目」（避免归因漂移：拿 default 条目的前缀去查
    一个族表里根本没有的族，报错看着像页面问题，真因是族表缺条目）。
    """

    def __init__(self, family: str, prefixes, declared_in_table: bool = True):
        self.family = family
        self.prefixes = list(prefixes or [])
        self.declared_in_table = declared_in_table
        tail = ("" if declared_in_table
                else f"；且族表缺族 {family!r} 条目——已按 default 条目前缀判定，请补该族条目")
        super().__init__(
            f"页面未呈现族 {family!r} 的前缀特征（期望 class token 以 {self.prefixes} 开头）——"
            f"换平台后等待/读取判据不可信，请核对 components.json 的 family 声明与该平台真实选择器"
            f"{tail}")


def _table_path() -> Path | None:
    """通用包 ui_family.json 路径（惰性 import plugin_api，避免与插件包成环）。"""
    try:
        from .plugin_api import common_knowledge_root
        return Path(common_knowledge_root()) / "ui_family.json"
    except Exception:
        return None


def load_family_table() -> dict:
    """读通用包 ui_family.json 顶层映射；缺失/坏字节/坏 JSON/顶层非映射 → {}（回退内置）。"""
    path = _table_path()
    if path is None:
        return {}
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _table_default_family(table: dict) -> str:
    default = table.get("default")
    return default if isinstance(default, str) and default.strip() else DEFAULT_FAMILY


def declared_family() -> str:
    """平台在 `components.json` 顶层声明的族名（缺省：族表 default → 内置 element-ui）。

    经 `KnowledgeBase.load_composed("components")` 读（私有 > 平台 > 通用组合层，`family`
    键可来自任一层）。知识库不可用/坏形态时回落表 default——绝不因读崩抛穿（读路径不先于
    被读对象死，doctor/等待都调它）。
    """
    table = load_family_table()
    default = _table_default_family(table)
    try:
        from .plugin_api import resolve_plugin
        data = resolve_plugin().knowledge().load_composed("components")
    except Exception:
        return default
    fam = data.get("family") if isinstance(data, dict) else None
    return fam if isinstance(fam, str) and fam.strip() else default


def resolve_family(name: str | None = None) -> dict:
    """解析族的判据值：`{name, signature, has_mask, prefixes, declared_in_table}`（唯一取值口）。

    查不到声明族条目 → 回落族表 default 条目 → 再不行用内置兜底（绝不返回空 prefix 判据：
    「无判据可查」与「特征缺席」是两回事，后者才是族不匹配，前者不能静默放行）。
    `declared_in_table=False` 记「声明族不在族表」（消费方据此在报错文案里点名族表缺条目，
    不让归因漂移成「页面问题」）。
    """
    table = load_family_table()
    families = table.get("families")
    families = families if isinstance(families, dict) else {}
    fam_name = name if isinstance(name, str) and name.strip() else declared_family()
    entry = families.get(fam_name)
    declared_in_table = isinstance(entry, dict)
    if not declared_in_table:
        entry = families.get(_table_default_family(table))
    entry = entry if isinstance(entry, dict) else {}
    signature = entry.get("signature")
    has_mask = entry.get("has_mask")
    prefixes = entry.get("prefixes")
    if not (isinstance(signature, str) and signature.strip()):
        signature = _BUILTIN_FALLBACK["signature"]
    if not isinstance(has_mask, bool):
        has_mask = _BUILTIN_FALLBACK["has_mask"]
    if not (isinstance(prefixes, list) and all(isinstance(p, str) for p in prefixes)):
        prefixes = list(_BUILTIN_FALLBACK["prefixes"])
    return {"name": fam_name, "signature": signature, "has_mask": has_mask,
            "prefixes": prefixes, "declared_in_table": declared_in_table}


def _selector_prefixes(components: dict) -> set:
    """遍历 components 里所有 CSS 字符串选择器，收集其框架 class 前缀（`.el-` 等）。

    structured 描述（dict）与空值跳过；坏形态逐条跳过不抛（本函数是展示/体检面，不先于
    被检查对象死）。"""
    found: set = set()
    comps = components.get("components") if isinstance(components, dict) else None
    if not isinstance(comps, dict):
        return found
    for spec in comps.values():
        selectors = spec.get("selectors") if isinstance(spec, dict) else None
        if not isinstance(selectors, dict):
            continue
        for value in selectors.values():
            if isinstance(value, str):
                found.update(_CLASS_TOKEN.findall(value))
    return found


def family_prefix_problems(components: dict, family: str | None = None) -> list[str]:
    """声明族与其选择器前缀的一致性（doctor 展示项 / 生成期闸共用，判据单点 #92）。

    仅在**显式声明了族**时校验（`family` 参数或 components 顶层 `family` 键）——未声明族时
    无可比对（返回空），不猜（避免「通用包经 base_roots 带入 element-ui 族」把业务插件误报）。

    判据（正证据才报，避免假阳性）：
    - 声明族不在通用包族表 → 点名（族名写错 / 缺条目）；
    - 组件选择器里出现**别的已知族的 class 前缀**（族表 `foreign_prefixes` 前缀→族名映射里
      归属 ≠ 声明族的项）→ 点名（例：声明 element-ui 却用了 `.ant-`）。
    返回问题清单（空=一致）；坏形态/无前缀判据不抛（展示/体检面，不先于被检查对象死）。
    """
    declared = family if isinstance(family, str) and family.strip() else None
    if declared is None and isinstance(components, dict):
        key = components.get("family")
        declared = key if isinstance(key, str) and key.strip() else None
    if declared is None:
        return []   # 未声明族：无可比对
    table = load_family_table()
    families = table.get("families")
    families = families if isinstance(families, dict) else {}
    if declared not in families:
        return [f"声明族 {declared!r} 不在通用包族表 ui_family.json 中"
                f"（已知：{sorted(families)}）——请补族条目或改正族名"]
    foreign = table.get("foreign_prefixes")
    if not isinstance(foreign, dict):
        return []
    used = _selector_prefixes(components)
    clashes = sorted(p for p in used
                     if isinstance(foreign.get(p), str) and foreign[p] != declared)
    if clashes:
        owners = {p: foreign[p] for p in clashes}
        return [f"声明族 {declared!r} 但组件选择器用了别的族的 class 前缀 {clashes}"
                f"（前缀→族：{owners}）——family 声明与选择器不符，请核对"]
    return []

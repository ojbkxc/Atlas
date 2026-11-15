"""项目知识是数据，不固化进引擎；写入均支持人工审核。"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from .failures import FailureClass, classify_failure, exception_text
from . import locators
from .atomic_io import atomic_write_text

# 平台维度（en-2）：知识条目可带可选 `plugin` 字段（组件条目/draft 行），把「哪个平台批准的
# 选择器」变成数据。缺失=兼容可见（存量数据默认行为逐字节不变）；"generic"=对所有平台可见。
# 判据只此一处（#92）：SelectorRegistry 读侧过滤与 KnowledgeBase.pitfalls 共用。
GENERIC_PLUGIN = "generic"


def _entry_plugin(entry) -> str | None:
    """条目上的平台标签（非空字符串才认；缺失/空白/坏形态=未标注 → None）。"""
    tag = entry.get("plugin") if isinstance(entry, dict) else None
    return tag.strip() if isinstance(tag, str) and tag.strip() else None


def _entry_visible(entry, current: str | None) -> bool:
    """条目对当前平台是否可见——平台维度的**唯一判据**。

    - 未标注 `plugin` → 可见（存量数据兼容；默认路径逐字节不变）。
    - `plugin == "generic"` → 对所有平台可见（通用包条目的显式形态）。
    - 当前平台不可判定（插件解析失败/未指定）→ 不过滤（回落旧行为，不猜）。
    - 其余 → 仅当与当前平台同名时可见（换平台后上一平台的批准不再静默生效）。
    """
    tag = _entry_plugin(entry)
    if tag is None or tag == GENERIC_PLUGIN:
        return True
    if not current:
        return True
    return tag == current


def current_plugin_name() -> str | None:
    """当前业务插件名（平台维度的**唯一取值点**：`resolve_plugin().name`）。

    惰性 import `plugin_api`（反过来 import plugins.demo → knowledge，模块级 import 会成环）。
    解析失败回落 None = 不过滤（读路径不先于被读对象死；ATLAS_PLUGIN 写坏时 CLI 早已 rc 2，
    doctor/离线读不该因此崩）。
    """
    try:
        from .plugin_api import resolve_plugin
        name = getattr(resolve_plugin(), "name", None)
    except Exception:
        return None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _scope_list(row: dict) -> list:
    """坑点 scope 的单一判据（#127 面2）：键存在值为 null 时 `row.get("scope", [])`
    返回 None 而非默认值（#77 同红线——默认值只在键缺失时生效），`domain in None`
    TypeError 顶掉整个坑点子系统。非列表形态（字符串/数字）同理归空——scope 语义是
    「适用域清单」，坏形态当无域限定处理不猜。"""
    scope = row.get("scope")
    return scope if isinstance(scope, list) else []

def _read_mapping(path: Path) -> dict | None:
    """读一个知识 JSON 文件，返回其**顶层映射**；文件缺失/坏字节/坏 JSON/顶层非映射
    一律 None（调用方据此回退下一个来源）。读取侧唯一判据，load 与 load_composed 共用
    ——不复制第二份「坏形态视同损坏」的口径（#92 改一漏一）。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None

def _row_digest(row: dict) -> str:
    """无 id 的坑点行按内容摘要去重（组合层合并时）。1 个坏值不进 sha1 就回落 repr，
    绝不因一行坏数据抛穿合并（组合是读路径，不能先于被合并对象死）。"""
    try:
        blob = json.dumps(row, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = repr(row)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()

def _merge_pitfall_rows(primary_rows, overlay_rows) -> list:
    """pitfalls 行列表合并：靠前（primary）先入，按 id 去重，无 id 按内容摘要去重。
    非列表/非映射的坏形态逐条跳过——组合层是读路径，坏行不得顶掉整次合并（#127 同纪律）。"""
    rows: list = []
    seen: set[str] = set()
    for source in (primary_rows, overlay_rows):
        if not isinstance(source, list):
            continue
        for row in source:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            key = row_id if isinstance(row_id, str) and row_id else _row_digest(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows

def _merge_components(primary, overlay) -> dict:
    """components 子映射按组件名合并：靠前（primary）赢同名组件（整条覆盖）。"""
    merged = dict(overlay) if isinstance(overlay, dict) else {}
    if isinstance(primary, dict):
        merged.update(primary)
    return merged

def _merge_composed(primary: dict, overlay: dict) -> dict:
    """把较低特异性的 overlay（通用包等）并入 primary：顶层 dict 按 key 合并（靠前者赢），
    pitfalls 行按 id 去重合并、components 按组件名合并，其余 key 靠前者整值覆盖。"""
    merged = dict(overlay)
    for key, value in primary.items():
        if key == "pitfalls":
            merged[key] = _merge_pitfall_rows(value, overlay.get("pitfalls"))
        elif key == "components":
            merged[key] = _merge_components(value, overlay.get("components"))
        else:
            merged[key] = value
    return merged


# ob-7：playbook §9 八项与 _common/domains.json checklist 的对应关系（单一真相，doctor 与
# 文档都消费这一份映射，不留第二份清单 #92）。顺序即 playbook §9 原文顺序：
#   网络→登录→技术栈/入口/权限→接口面→数据卫生→能力边界→可复原性
# domains.json 的七类键 ↔ §9 的八个复选框（stack_entry_and_accounts 一类对应
# 技术栈+入口+权限三项——原论文就是一组开工前确认，拆开反而丢「先备账号再开工」的次序）。
PLAYBOOK_CHECKLIST_MAP: tuple[tuple[str, str], ...] = (
    ("网络（代理方向逐入口实测）", "network_and_proxy"),
    ("登录（判据/验证码/认证头）", "login_and_captcha"),
    ("技术栈与组件库族", "stack_entry_and_accounts"),
    ("入口拓扑（几个入口/共用 API 域）", "stack_entry_and_accounts"),
    ("接口面（状态类端点/参数传递）", "api_surface_and_params"),
    ("数据卫生（DELETE/冲销纪律）", "data_hygiene"),
    ("能力边界（不存在功能/外部依赖）", "capability_boundaries"),
    ("权限（越权测试账号）", "stack_entry_and_accounts"),
    ("可复原性（回不去的操作清单）", "restorability"),
)


def platform_checklist(kb: "KnowledgeBase") -> dict:
    """ob-7：新平台接入检查清单（playbook §9）→ doctor 子项的**唯一加载/校验实现**。

    读 `_common/knowledge/domains.json` 的 `checklist`（经 KnowledgeBase.load_composed
    组合层——平台插件可用自己的 domains.json 覆盖/补全通用包条目）。纯只读、零网络；
    缺文件/坏形态如实 `present:false`（与 boundary_ledger 缺失同口径：缺清单 ≠ 漂移），
    绝不抛——doctor 的职责是汇报状态，判据函数不得先于被诊断对象死（#89 家族）。

    返回形态（doctor 展示面）：
        {"present": True, "categories": <类数>, "items": <总条数>,
         "playbook_sections": [[标签, 类名], ...], "policy_len": <policy 字符数>}
    缺失/坏形态：{"present": False}
    """
    data = kb.load_composed("domains") if kb is not None else None
    checklist = data.get("checklist") if isinstance(data, dict) else None
    if not isinstance(checklist, dict) or not checklist:
        return {"present": False}
    items = 0
    for spec in checklist.values():
        if isinstance(spec, dict) and isinstance(spec.get("items"), list):
            items += len(spec["items"])
    return {
        "present": True,
        "categories": len(checklist),
        "items": items,
        # playbook §9 八项 → checklist 类键的映射原文（doctor --format text 展示用；
        # JSON 消费方拿这份映射就能把「§9 还差哪项没确认」算出来）
        "playbook_sections": [list(pair) for pair in PLAYBOOK_CHECKLIST_MAP],
        "policy_len": len(data.get("policy", "")) if isinstance(data.get("policy"), str) else 0,
    }


class KnowledgeBase:
    def __init__(self, root: Path | None,
                 fallback_root: Path | list[Path] | tuple[Path, ...] | None = None,
                 base_roots: Path | list[Path] | tuple[Path, ...] | None = None,
                 plugin: str | None = None):
        # #135（AGENTS 红线 #11「批准永不写包内文件」的代码化）：root 是**写目标**、
        # fallback_root 只供读取——两者在类型上分离，包内资产目录只能作 fallback。
        # 旧 plugin 在 ATLAS_KNOWLEDGE_DIR 缺失时构造 `KnowledgeBase(包内根)`：包内目录
        # 同时当读写根，`review --approve/--apply` 一批准就把业务坑点/选择器/坐标写进
        # git 资产（探针实锤 2026-11-04：包内 pitfalls.json 30→31，doctor 计数当场变红）。
        # 测试靠 _stub_cli 指 tmp 规避，真实用户忘设 env 毫无保护。现 root=None=只读库：
        # load 按 私有→包内 回退照常「读不残缺」，save_*（批准唯一写路径）拒绝点名
        # ATLAS_KNOWLEDGE_DIR。判据不靠路径包含启发式（Windows resolve/符号链接可绕过，
        # 且「显式调用方拿包内路径构造」与「忘设 env 的 plugin 实例」无法从路径区分）——
        # 引擎入口（DemoPlugin.knowledge）不再产出指向包内的可写实例，写闸门归类型契约。
        self.root = root
        # 私有目录（ATLAS_KNOWLEDGE_DIR）逐文件回退包内虚构默认：
        # 内部知识不入库，读不残缺、写只落私有。
        # fallback_root 可为单个 Path（存量调用，逐字兼容）或**有序 Path 列表**（影子层）：
        # load() 逐文件取根序列里**第一个存在**的根，同名文件整份胜出（影子遮蔽）。原始传入
        # 值原样保留在属性上（单 Path 仍是 Path，消费方如 plugins/demo 的校验靠它）。
        # 通用知识包**不**进这里——影子语义会让它被平台自带的同名文件整份挡住，改走 base_roots。
        self.fallback_root = fallback_root
        # 组合层（task #2 复核修正）：base_roots 是**最不特异**的共享知识根（如通用知识包），
        # 与 root/fallback 的影子语义**不同**——它按 key/条目**并入**（靠前者赢），使自带同名
        # 文件的平台插件仍能读到通用条目（影子遮蔽会整份挡住通用包）。单 Path 或有序列表；
        # 默认 None = 零行为变化（load_composed 退化为 load，见下）。
        self.base_roots = base_roots
        # en-2 平台维度：知识条目可带 `plugin` 标签。这里存**当前平台名**（None=读时按
        # resolve_plugin().name 惰性解析；显式传入用于测试/离线场景）。读写两侧都取
        # `plugin_name()` 这一个口，不各抄一份平台名（#92）。
        self.plugin = plugin

    def plugin_name(self) -> str | None:
        """当前平台名（en-2 唯一取值口）：显式 `plugin=` 优先，否则 `resolve_plugin().name`。"""
        if self.plugin is not None:
            return self.plugin or None
        return current_plugin_name()

    @staticmethod
    def _expand(value) -> tuple[Path | None, ...]:
        """单 Path / 有序列表统一展开为 tuple；None → 空。"""
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return (value,)

    def _read_roots(self) -> tuple[Path | None, ...]:
        """load() 的**影子**根序列：写根 root 恒最优先，其后按 fallback_root 顺序展开
        （单 Path 与「有序 Path 列表」同形展开，#115 旧调用逐字不变）。**不含 base_roots**
        ——组合层由 load_composed 单独并入，影子语义不受其影响；写路径**不**经本函数，
        只认 root（#135，见 _writable_root），fallback 与 base 一律只读。
        """
        return (self.root, *self._expand(self.fallback_root))

    def _writable_root(self) -> Path:
        """批准写回前的闸门：只读库（root=None，未配私有目录时的引擎唯一形态）拒绝写入。"""
        if self.root is None:
            raise PermissionError(
                "知识库写回需要私有目录：包内虚构默认是只读资产（AGENTS 红线 #11），"
                "真实项目先设 ATLAS_KNOWLEDGE_DIR 指到仓库外目录再批准")
        return self.root

    def load(self, name: str) -> dict:
        # 私有文件写坏（截断/空/坏字节）不得顶掉整个知识子系统：逐文件回退包内虚构默认，
        # 兑现「读不残缺」——否则一个残 JSON 让 doctor/selectors/review --approve（本身要读库）/run
        # 全部崩，且塔因读不到库而再也清不动；下次批准会以 write_text 重写好私有文件自愈。
        # #127 面1：旧闸门只拦「坏字节/坏 JSON」，放过「合法 JSON 但顶层非映射」（手滑存成
        # 数组/字符串）——所有消费方按 dict 读（pitfalls/load_overlays/save 的 .get、SelectorRegistry
        # 的 [key]），穿透即 AttributeError 顶掉 engine 失败路径/自愈准入/批准写回/doctor，与坏字节
        # 顶子系统同一后果。形态不对视同损坏：continue 换下一个来源，全部根都坏回落空 dict。
        for base in self._read_roots():
            if base is None:
                continue
            data = _read_mapping(Path(base) / f"{name}.json")
            if data is not None:
                return data
            # 缺失/坏字节/坏 JSON/顶层非映射：同属损坏，回退下一个来源，绝不返回坏形态
        return {}

    def load_composed(self, name: str) -> dict:
        """组合层读取：在 **load() 的影子结果**（root → fallback，靠前者整份胜出）之上，
        按特异性顺序并入 base_roots（通用包等，靠前者赢）。base_roots=None/空 → 与 load()
        逐字等价（#115 零行为变化）。

        为什么不用影子：snalai 这类自带 pitfalls.json/components.json 的平台插件，
        「同名文件取第一个存在的根」会让通用包整份不可见——组合层按 key/条目并入才对
        （通用条目补齐平台缺的那部分，平台专有条目仍胜出）。#135：写路径不在此，只认 root。
        """
        primary = self.load(name)
        for base in self._expand(self.base_roots):
            if base is None:
                continue
            overlay = _read_mapping(Path(base) / f"{name}.json")
            if overlay is None:
                continue
            primary = _merge_composed(primary, overlay)
        return primary

    def pitfalls(self, domain: str = "") -> list[dict]:
        # #127 面2：pitfalls 键值非列表（手滑写成 dict/字符串——load 的顶层闸门管不到内层键，
        # #77 同族「键在但形态不对」）或行非映射（数组直接装字符串）时逐行跳过：一条坏行
        # 不得顶掉整个坑点子系统——读侧（experience/cli doctor/engine 失败路径）都只该看到好行。
        # 组合层：平台自带的坑点文件与通用包按 id 去重并入（load_composed），否则通用包整份
        # 被影子遮蔽、对平台域永远不可见。
        rows = self.load_composed("pitfalls").get("pitfalls", [])
        if not isinstance(rows, list):
            rows = []
        # 可见性（task #2 复核修正 / docs §5.1）：`domain in scope or "generic" in scope`
        # ——通用包每行 scope 带 `generic` 标记（数据层负责），故对任意平台域可见；
        # 平台专有行 scope 只有平台名+机制标签，不带 generic，对别的域不可见（不误配）。
        # en-2/ob-4：行可另带可选 `plugin` 标签——判据复用 `_entry_visible`（与组件库同一把
        # 尺，不新开第二份）；缺失=兼容可见（存量行逐字节行为不变），generic=任意平台可见。
        current = self.plugin_name()
        return [row for row in rows
                if isinstance(row, dict) and (not domain
                                              or domain in _scope_list(row)
                                              or "generic" in _scope_list(row))
                and _entry_visible(row, current)]

    def match_pitfall_from_text(self, text: str, domain: str = "",
                                status: str | None = None) -> dict | None:
        """文本 → 坑点匹配的单一真相；match_pitfall 与 Allure 分析共用。

        trigger 为空/缺省绝不匹配（#59）：`"" in text` 恒真，一条缺 trigger 的知识会命中
        所有异常——自愈据此 can_auto_heal 就会对任意只读失败重跑无关修复。数据完整性防线。
        #127 面3：trigger 键在值 null（手滑删值）——`.get("trigger", "")` 默认值不生效（#77），
        None.strip() AttributeError 顶子系统。非字符串形态同拒：trigger 语义是匹配字面量子串。
        status 过滤（自愈准入用 status="approved"）必须在**择一之前**：否则一条 trigger 更长的
        pending 草稿会遮蔽同一异常上已审核的坑点，can_auto_heal 单看胜者的 status 就误判无解可自愈，
        heal 模式被未审核数据静默压制（知识库靠 explore/失败草稿不断积 pending，这是常态非偶发）。
        诊断调用点（engine 记 pitfall_id / propose）不传 status，保持"最长最具体匹配"原义。
        """
        lowered = text.lower()
        matches = [row for row in self.pitfalls(domain)
                   if isinstance(trig := row.get("trigger"), str)
                   and (trigger := trig.strip().lower()) and trigger in lowered]
        if status is not None:
            matches = [row for row in matches if row.get("status") == status]
        # #127 面3 续：key 取 len 遇 trigger null 行同样炸（坏行已被上面过滤，但防御按
        # isinstance 取 len——坏数据进不了 matches 就不该再有机会崩 max）。
        return max(matches, key=lambda row: len(row.get("trigger", "") or ""), default=None)

    def match_pitfall(self, exc: BaseException, domain: str = "",
                      status: str | None = None) -> dict | None:
        # #127 面6：f"{类型名} {exc}" 在异常 __str__ 自身崩时炸（业务插件自定义异常引用未初始化
        # 属性是真实雷区，#126 面A 同形态）——classify_failure 已走单一真相 exception_text，
        # 这里漏一份就违反 #92「改一漏一必漂移」，且本函数被 engine 失败路径/healing 准入直调，
        # 顶掉的是原用例异常与自愈判定。委托 match_pitfall_from_text 保持单一真相。
        return self.match_pitfall_from_text(exception_text(exc), domain, status)

    def draft_pitfall(self, exc: BaseException, *, symptom: str, root_cause: str,
                      remedy: str, scope: list[str], source: str) -> dict:
        """只生成待审核草稿，未经人审不写进正式知识库。"""
        kind: FailureClass = classify_failure(exc)
        # sha1 而非 hash()：内置 hash 受 PYTHONHASHSEED 进程随机化，
        # 同一坑点两次审核会拿到不同 id，正式库里就会堆重复条目。
        digest = hashlib.sha1("|".join([symptom, root_cause, remedy, *scope])
                              .encode("utf-8")).hexdigest()[:12]
        return {"id": "draft-" + digest, "trigger": type(exc).__name__,
                "symptom": symptom, "root_cause": root_cause, "remedy": remedy,
                "scope": scope, "severity": "medium", "source": source,
                "failure_class": kind.value, "status": "pending_review"}

    def save_reviewed(self, name: str, records: list[dict], *, approved: bool = False) -> Path:
        if not approved:
            raise PermissionError("知识库写回需要人工审核；请传 approved=True 明确批准")
        path = self._writable_root() / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # wave4 rb-1：write_text 非原子——写中崩把已批准知识截断且命令仍 rc0（假成功），
        # 下次 load 静默回退包内虚构默认 = 资产无声消失。改原子写（tmp+fsync+os.replace）。
        atomic_write_text(path, json.dumps({name: records}, ensure_ascii=False, indent=2))
        return path

    def save_component_selector(self, component: str, element: str, selector, *,
                                approved: bool = False, source: str = "review",
                                note: str = "") -> dict:
        """批准的选择器写回组件库——selector_candidate 的唯一入库路径。

        `selector` 两形态（gap13）：CSS 字符串（存量）或结构化描述
        `{"role": "button", "name": "保存"}` / `{"label":...}` / `{"testid":...}`。
        **落盘前**过 `locators.parse` 校验：坏形态当场点名（LocatorSpecError），
        脏形态绝不进知识文件（进了就是「产得出、入不了库」的反面：入了库但跑不了）。

        保持 components.json 的嵌套结构（{"components": {...}}），
        与 save_reviewed 的列表型知识库不同，不能混用。
        """
        if not approved:
            raise PermissionError("组件选择器写回需要人工审核；请传 approved=True 明确批准")
        # 形态判据前置到读旧库之前：坏输入不留任何中间状态（也不白读一遍盘）。
        # 落**用户形态**（CSS 恒为字符串、结构化描述不带内部 kind 标签）：存量逐字节不变，
        # 且存进去的值再读出来能原样 parse 回来（落 IR 会存出一份自己解析不了的知识）。
        stored = locators.normalize(selector)
        data = self.load("components")
        # #127 面4：load 的顶层闸门保证 data 是 dict，但「components」键值仍可能是手滑的
        # 非映射（字符串/列表——#77 键在值 null/坏形态默认不生效同族），旧实现直接 .get 后
        # setdefault 一路 AttributeError，批准（review --approve 的唯一入库路径）半路崩、
        # 塔永远清不动（#73 场景）。坏形态当空库重建：批准会以 write_text 把好结构写回私有。
        components = data.get("components")
        if not isinstance(components, dict):
            components = dict(data) if data else {}
            if components and "components" in components:
                components = {}   # 顶层混着 components 键却非映射：只认 components，丢弃噪音
        spec = components.get(component)
        if not isinstance(spec, dict):
            spec = {}
            components[component] = spec
        selectors = spec.get("selectors")
        if not isinstance(selectors, dict):
            selectors = {}
            spec["selectors"] = selectors
        overwritten = element in selectors
        selectors[element] = stored
        if note and not spec.get("notes"):
            spec["notes"] = note
        # en-2：批准时把**当前平台名**写进组件条目（`resolve_plugin().name` 单点，
        # 见 `plugin_name()`）——换平台后上一平台批准的选择器读侧不可见，撞名不再静默点错。
        # 标签跟「最近一次批准」走：selector 值本身已是 element 级 last-writer-wins，标签
        # 不引入额外丢失；两平台共用一个私有根本就该各配一份（ATLAS_KNOWLEDGE_DIR 按项目）。
        # **generic 条目不覆盖**：`generic`=平台中立知识，B 批准同组件新元素时若盖成 B 的标签，
        # 会把通用选择器静默窄化成 B 专用（「通用包变私有」），故 generic 标签原样保留。
        current = self.plugin_name()
        if current:
            existing_tag = spec.get("plugin")
            if not (isinstance(existing_tag, str) and existing_tag.strip() == GENERIC_PLUGIN):
                spec["plugin"] = current
        path = self._writable_root() / "components.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 保留写目标文件里 `components` 之外的顶层键（如平台声明的 `family` 族标识）——
        # 旧实现无条件重写 `{"components": ...}`，第一次批准选择器就把这些键静默丢弃、
        # 平台族判据无声失效（#102/#105 假绿同族）。读**写目标文件本身**（非 load()）：
        # load 在私有文件缺失时回退包内默认，把包内顶层键抄进私有会改变首批准行为
        # （#115）；目标文件缺失 → 无键可留，落盘仍为 `{"components": ...}`，与旧行为逐字节一致。
        existing = _read_mapping(path)
        payload: dict = {}
        if isinstance(existing, dict):
            payload = {k: v for k, v in existing.items() if k != "components"}
        payload["components"] = components
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        return {"component": component, "element": element, "selector": stored,
                "status": "approved", "source": source, "overwritten": overwritten,
                "plugin": spec.get("plugin")}

    def save_overlay(self, bu: str, key: str, value, *, approved: bool = False,
                    source: str = "review") -> dict:
        """批准过的 BU 坐标写入私有 overlay 库（人审是唯一入口）。"""
        if not approved:
            raise PermissionError("BU 坐标写回需要人工审核；请传 approved=True 明确批准")
        data = self.load("bu_overlays")
        # #127 面5：overlays 键值坏形态（手滑成列表/字符串、或 BU 子映射坏）与面4 同族——
        # 批准坐标是 run --bu 执行链的唯一入库路径，半路崩=塔清不动、坐标永远进不了执行。
        overlays = data.get("overlays")
        if not isinstance(overlays, dict):
            overlays = {}
        bu_map = overlays.get(bu)
        if not isinstance(bu_map, dict):
            bu_map = {}
            overlays[bu] = bu_map
        existed = key in bu_map
        bu_map[key] = value
        path = self._writable_root() / "bu_overlays.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"overlays": overlays}, ensure_ascii=False, indent=2))
        return {"bu": bu, "key": key, "value": value, "status": "approved",
                "source": source, "overwritten": existed}

    def save_task_entry(self, entry: dict, *, approved: bool = False,
                        source: str = "review") -> dict:
        """批准的流程候选写回公共任务库——flow_candidate 的唯一入库路径。

        task_library.json 存跨流程可复用的**动作序列**（{"tasks": [...]}），按 name 唯一：
        同名再批准是更新不是堆叠（save_component_selector 的 element 语义同款），
        overwritten 显式回传让人知道盖了旧条目。steps 非空且逐条为映射才收——
        空序列入库是「看似完整实为空」的假资产（#102 同红线），宁可在批准时点名。
        写闸门与 save_reviewed 同源（_writable_root，红线 #11：包内虚构默认是只读资产）。
        """
        if not approved:
            raise PermissionError("公共任务库写回需要人工审核；请传 approved=True 明确批准")
        if not isinstance(entry, dict):
            raise ValueError(f"任务库条目必须是映射，实为 {type(entry).__name__}")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ValueError("任务库条目必须有非空 name（库按名字取用，无名条目无法被引用）")
        steps = entry.get("steps")
        if not isinstance(steps, list) or not steps \
                or not all(isinstance(item, dict) for item in steps):
            raise ValueError(f"任务库条目 steps 必须是非空映射列表，实为 "
                             f"{type(steps).__name__}（空/坏形态入库=假资产）")
        data = self.load("task_library")
        # #127 面4/面5 同族：tasks 键值坏形态（手滑成映射/字符串）当空库重建，
        # 行非映射的旧条目同样丢弃——批准会以 write_text 把好结构写回私有，
        # 半路崩=塔清不动（批准是唯一写路径，闸门必须能走通）。
        tasks = data.get("tasks")
        if not isinstance(tasks, list):
            tasks = []
        overwritten = any(isinstance(t, dict) and t.get("name") == name for t in tasks)
        tasks = [t for t in tasks
                 if not (isinstance(t, dict) and t.get("name") == name)]
        record = {"id": hashlib.sha1(f"task-library:{name}".encode("utf-8")).hexdigest()[:12],
                  "name": name, "steps": steps, "status": "approved",
                  "source": source,
                  "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if isinstance(entry.get("assert"), list):
            record["assert"] = entry["assert"]
        description = str(entry.get("description", "")).strip()
        if description:
            record["description"] = description
        source_run = str(entry.get("source_run", "")).strip()
        if source_run:
            record["source_run"] = source_run
        tasks.append(record)
        path = self._writable_root() / "task_library.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2))
        return {"id": record["id"], "name": name, "steps": len(steps),
                "status": "approved", "source": source, "overwritten": overwritten}

    def selectors(self, plugin: str | None = None) -> SelectorRegistry:
        """组件注册表读取口（en-2）：按当前平台过滤条目——平台维度判据唯一在
        `_entry_visible`/`SelectorRegistry`，读侧不再各抄一份（#92）。

        显式 `plugin=` 仅用于离线判据/测试；缺省用 `plugin_name()`（`resolve_plugin().name`
        单点）。当前插件名解析不出时 `SelectorRegistry` 按「不过滤」回落（旧行为，不猜）。
        """
        return SelectorRegistry(self.load_composed("components"),
                                plugin=plugin if plugin is not None else self.plugin_name())

    def load_overlays(self) -> dict:
        overlays = self.load("bu_overlays").get("overlays")
        return overlays if isinstance(overlays, dict) else {}

class SelectorRegistry:
    def __init__(self, components: dict, plugin: str | None = None):
        raw = components.get("components", components) if isinstance(components, dict) else components
        # #127 面4 读侧：坏条目（spec 非映射，如手滑 {"按钮": ".css"} 忘了包一层）整条当未注册
        # ——get/find/names/missing 统一按「不存在」拒绝（#63 延伸：存在≠可用），消费点不逐处
        # 类型防御、归因不散落。selectors 键值 null 不在这里裁，留给 get 按缺失点名（missing 语义）。
        entries = {name: spec for name, spec in (raw or {}).items()
                   if isinstance(spec, dict)} if isinstance(raw, dict) else {}
        # en-2 平台维度：组件条目可带可选 `plugin` 标签（批准时写入当前插件名）。读侧按当前
        # 平台过滤——判据复用 `_entry_visible` 单点（与 pitfalls 同一把尺，#92）。`plugin`
        # 缺省=兼容可见（存量数据默认行为不变），`generic`=任意平台可见，别的平台条目不可见
        # 且**同名遮蔽要点名**（进 `shadowed`，供 add/doctor 诊断，不静默）。
        self.plugin = plugin
        self.shadowed: dict[str, str] = {}
        current = plugin if plugin is not None else current_plugin_name()
        self.components = {}
        for name, spec in entries.items():
            if _entry_visible(spec, current):
                self.components[name] = spec
            else:
                self.shadowed[name] = _entry_plugin(spec) or ""

    def shadowed_components(self) -> dict[str, str]:
        """被别的平台标签遮蔽的组件名 → 它的 plugin 标签（诊断用；不静默丢）。"""
        return dict(self.shadowed)

    def get(self, component: str, element: str):
        """返回登记的**原值**（字符串=CSS，或结构化描述 dict）——老调用方零影响。"""
        spec = self.components.get(component)
        selectors = spec.get("selectors") if spec else None
        # selectors 键值 null/非映射按「未注册」归因，不再 None[element] 裸 TypeError
        if isinstance(selectors, dict) and element in selectors:
            return selectors[element]
        raise KeyError(self._missing_message(component, element))

    def _missing_message(self, component: str, element: str = "") -> str:
        """未注册归因文本（存量两种口径逐字保留：组件级「组件未注册」/元素级「选择器未注册」）；
        被**别的平台标签**遮蔽时追加点名该标签（en-2：不静默丢）。"""
        where = f"{component}.{element}" if element else component
        head = f"选择器未注册：{where}" if element else f"组件未注册：{where}"
        owner = self.shadowed.get(component)
        if owner is not None:
            return (f"{head}｜同名组件由平台 {owner!r} 批准，当前平台 "
                    f"{self.plugin or current_plugin_name() or '?'!s} 不可见"
                    f"（换平台后上一平台的批准不生效；确需复用请用 `generic` 标签或改 ATLAS_KNOWLEDGE_DIR）")
        return head

    def resolve(self, page, component: str, element: str):
        """登记的定位描述 → Playwright locator（两形态通吃；判据在 `locators.resolve` 一处）。"""
        return locators.resolve(page, self.get(component, element))

    def describe(self, component: str, element: str) -> str:
        """登记的定位描述 → 可读单行文本（展示/留痕；dict 不可切片，别裸用原值）。"""
        return locators.canonical(self.get(component, element))

    def find(self, component: str) -> dict:
        """返回组件的全部选择器与说明，供 agent 探查后再动手。"""
        if component not in self.components:
            raise KeyError(self._missing_message(component))
        return self.components[component]

    def names(self) -> list[str]:
        return sorted(self.components)

    def missing(self) -> list[str]:
        """列出缺少 selectors 的组件，区分“只有知识说明”与“可直接定位”。"""
        return sorted(name for name, spec in self.components.items() if not spec.get("selectors"))

    def draft_update(self, component: str, element: str, selector: str) -> dict:
        return {"component": component, "element": element, "selector": selector,
                "status": "pending_review"}

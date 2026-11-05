"""项目知识是数据，不固化进引擎；写入均支持人工审核。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
from .failures import FailureClass, classify_failure, exception_text

def _scope_list(row: dict) -> list:
    """坑点 scope 的单一判据（#127 面2）：键存在值为 null 时 `row.get("scope", [])`
    返回 None 而非默认值（#77 同红线——默认值只在键缺失时生效），`domain in None`
    TypeError 顶掉整个坑点子系统。非列表形态（字符串/数字）同理归空——scope 语义是
    「适用域清单」，坏形态当无域限定处理不猜。"""
    scope = row.get("scope")
    return scope if isinstance(scope, list) else []

class KnowledgeBase:
    def __init__(self, root: Path | None, fallback_root: Path | None = None):
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
        self.fallback_root = fallback_root

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
        # 顶子系统同一后果。形态不对视同损坏：continue 换下一个来源，两级都坏回落空 dict。
        for base in (self.root, self.fallback_root):
            if base is None:
                continue
            path = Path(base) / f"{name}.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue   # 这个基目录的文件坏了：换下一个来源（私有坏→回退包内默认）
            if isinstance(data, dict):
                return data
            # 顶层非映射：合法 JSON 但形态不对——同属损坏，回退下一个来源，绝不返回坏形态
        return {}

    def pitfalls(self, domain: str = "") -> list[dict]:
        # #127 面2：pitfalls 键值非列表（手滑写成 dict/字符串——load 的顶层闸门管不到内层键，
        # #77 同族「键在但形态不对」）或行非映射（数组直接装字符串）时逐行跳过：一条坏行
        # 不得顶掉整个坑点子系统——读侧（experience/cli doctor/engine 失败路径）都只该看到好行。
        rows = self.load("pitfalls").get("pitfalls", [])
        if not isinstance(rows, list):
            rows = []
        return [row for row in rows
                if isinstance(row, dict) and (not domain or domain in _scope_list(row))]

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
        path.write_text(json.dumps({name: records}, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_component_selector(self, component: str, element: str, selector: str, *,
                                approved: bool = False, source: str = "review",
                                note: str = "") -> dict:
        """批准的选择器写回组件库——selector_candidate 的唯一入库路径。

        保持 components.json 的嵌套结构（{"components": {...}}），
        与 save_reviewed 的列表型知识库不同，不能混用。
        """
        if not approved:
            raise PermissionError("组件选择器写回需要人工审核；请传 approved=True 明确批准")
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
        selectors[element] = selector
        if note and not spec.get("notes"):
            spec["notes"] = note
        path = self._writable_root() / "components.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"components": components}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return {"component": component, "element": element, "selector": selector,
                "status": "approved", "source": source, "overwritten": overwritten}

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
        path.write_text(json.dumps({"overlays": overlays}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return {"bu": bu, "key": key, "value": value, "status": "approved",
                "source": source, "overwritten": existed}

    def load_overlays(self) -> dict:
        overlays = self.load("bu_overlays").get("overlays")
        return overlays if isinstance(overlays, dict) else {}

class SelectorRegistry:
    def __init__(self, components: dict):
        raw = components.get("components", components) if isinstance(components, dict) else components
        # #127 面4 读侧：坏条目（spec 非映射，如手滑 {"按钮": ".css"} 忘了包一层）整条当未注册
        # ——get/find/names/missing 统一按「不存在」拒绝（#63 延伸：存在≠可用），消费点不逐处
        # 类型防御、归因不散落。selectors 键值 null 不在这里裁，留给 get 按缺失点名（missing 语义）。
        self.components = {name: spec for name, spec in (raw or {}).items()
                           if isinstance(spec, dict)} if isinstance(raw, dict) else {}

    def get(self, component: str, element: str) -> str:
        spec = self.components.get(component)
        selectors = spec.get("selectors") if spec else None
        # selectors 键值 null/非映射按「未注册」归因，不再 None[element] 裸 TypeError
        if isinstance(selectors, dict) and element in selectors:
            return selectors[element]
        raise KeyError(f"选择器未注册：{component}.{element}")

    def find(self, component: str) -> dict:
        """返回组件的全部选择器与说明，供 agent 探查后再动手。"""
        if component not in self.components:
            raise KeyError(f"组件未注册：{component}")
        return self.components[component]

    def names(self) -> list[str]:
        return sorted(self.components)

    def missing(self) -> list[str]:
        """列出缺少 selectors 的组件，区分“只有知识说明”与“可直接定位”。"""
        return sorted(name for name, spec in self.components.items() if not spec.get("selectors"))

    def draft_update(self, component: str, element: str, selector: str) -> dict:
        return {"component": component, "element": element, "selector": selector,
                "status": "pending_review"}

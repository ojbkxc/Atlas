"""首批 10 个核心 Task：元数据可独立用于流程拼装，实现委托旧页面库。"""
from __future__ import annotations
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any
from ...core import Channel, Task, TaskMetadata

# session.json 是虚构示例默认值；指向真实业务时用环境变量整体覆盖（与 bridge 映射同精神：
# 引擎/仓库零真实字面量，真实值只在本机 .env 里）。
SESSION_ENV_KEYS = {"account": "ATLAS_SESSION_ACCOUNT", "ou": "ATLAS_SESSION_OU",
                    "division_code": "ATLAS_SESSION_DIVISION", "language": "ATLAS_SESSION_LANGUAGE"}

def overlay_session(defaults: dict) -> dict:
    for key, env in SESSION_ENV_KEYS.items():
        value = os.environ.get(env, "").strip()
        if value:
            defaults[key] = value
    return defaults


# 已批准 overlay 坐标 → 会话链环境变量。空值与非会话键不改执行路径（只做数据池参考）。
_BU_ENV_KEYS = {"ou": "ATLAS_SESSION_OU", "division": "ATLAS_SESSION_DIVISION",
                "division_code": "ATLAS_SESSION_DIVISION", "account": "ATLAS_SESSION_ACCOUNT",
                "language": "ATLAS_SESSION_LANGUAGE"}

def _is_coordinate(value) -> bool:
    """合法坐标 = 非空标量（字符串/数字），与 probes.find_document_number 的 #117 单号
    判据同纪律（#92/#97：多点位同一判据收敛）。旧判据 `value in (None, "")` 漏了空容器：
    `[]`/`{}` 被 `str()` 成字面量 `"[]"`/`"{}"` 写进 ATLAS_SESSION_*（探针实锤
    2026-11-04），真实执行 switch_to_ou 拿 `"[]"` 必超时——坏形态伪装合法坐标
    （#77 键在值空不算 / #102 假绿同族）。bool 也不算坐标（True→"True" 是假 OU），
    数字 0/空白串以外照常接受（#115：标量正常路径零涟漪，不翻成错误）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, (str, int, float)):
        return str(value).strip() != ""
    return False

def apply_bu(bu: str, overlays: dict, environ=os.environ) -> dict:
    """`--bu` 已批准坐标接进 session 默认链；显式 ATLAS_SESSION_* env 优先于 --bu。

    #134：顶层 overlays 由 `load_overlays` 保证是 dict，但**某个 BU 的值**手滑存成
    数组/字符串（合法 JSON 但形态不对，#127 同族）时旧实现 `.items()` 裸
    AttributeError——CLI run --bu 只捕 KeyError，漏网变 traceback。现点名
    「BU 值形态非映射」抛 ValueError（可定位归因，接 #79/#80 rc2 口径）。"""
    if bu not in overlays:
        raise KeyError(f"BU 不在已批准 overlay 库：{bu!r}，可用：{sorted(overlays)}")
    coords = overlays[bu]
    if not isinstance(coords, dict):
        raise ValueError(f"BU {bu!r} 的坐标形态是 {type(coords).__name__} 而非映射，"
                         f"无法接入会话链：{coords!r}")
    applied, ignored = {}, []
    for key, value in coords.items():
        env = _BU_ENV_KEYS.get(key)
        if not env or not _is_coordinate(value):
            ignored.append(key)
            continue
        if environ.get(env):
            ignored.append(f"{key}(env显式优先)")
            continue
        environ[env] = str(value)
        applied[key] = value
    return {"bu": bu, "applied": applied, "ignored": ignored}

def _session_default(key: str):
    """业务会话默认值的唯一数据源——委托 `DemoPlugin.session_defaults()`
    （私有→包内逐文件回退 + ATLAS_SESSION_* 覆盖）。

    #137：旧实现自己 `json.loads` 直读**包内** session.json，绕开 KnowledgeBase
    回退链——探针实锤设 `ATLAS_KNOWLEDGE_DIR` 指真实业务后，CLI/运行时
    （session_defaults）拿私有真实 division，而 auto API 快路径
    （select_customer/read_pt 的 division 兜底）拿虚构默认查错客户：
    两份「唯一来源」漂移（#92 改一漏一），红线 #11「真实值只在私有目录」被
    引擎侧旁路击穿。现收敛单一真相；私有文件损坏时与 CLI 同回落包内（load 语义），
    行为一致可预期。"""
    from .plugin import DemoPlugin
    try:
        return DemoPlugin().session_defaults()[key]
    except KeyError:
        raise KeyError(
            f"会话默认值缺键 {key!r}：私有/包内 session.json 未覆盖该键，"
            f"且 ATLAS_SESSION_* 未设置——补齐文件或显式设 env") from None


class Pages:
    def __init__(self, path: Path | None = None):
        if path is None:
            from ...config import AtlasConfig
            config = AtlasConfig.from_root()
            path = (config.legacy_project / config.legacy_web / "lib" / "pages.py")
        self.path = Path(path)
        self._module = None

    def get(self):
        if self._module is None:
            # 旧 pages.py 依赖 `from lib.runner import ...`，必须先把 web 子目录
            # （默认 legacy-web，真实项目可能是 iservice）放进 sys.path，
            # 否则真实执行时报 No module named 'lib'（离线测试发现不了）。
            root = self.path.parent.parent  # .../<legacy_web>
            if (root / "lib").is_dir() and str(root) not in sys.path:
                sys.path.insert(0, str(root))
            spec = importlib.util.spec_from_file_location("atlas_demo_legacy_pages", self.path)
            if not spec or not spec.loader:
                raise ImportError(str(self.path))
            self._module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._module)
        return self._module

    def call(self, name: str, actor, *args, **kwargs) -> Any:
        function = getattr(self.get(), name)
        return function(actor.page, *args, **kwargs)

class DemoTask(Task):
    function = ""
    parameters: tuple[str, ...] = ()
    # 业务默认值：agent/DSL 省略参数时用它，避免真实执行才暴露 TypeError。
    # 放在类属性而非实例，registry 重建实例（register_tasks 的 lambda）时才不会丢。
    defaults: dict = {}
    # 方案 3.2：channel=auto 时是否优先走 API。只有 api_operation 非空且 Task 实现了
    # _perform_api 的才为 True。引擎据此决定是否尝试 API 路径，失败再降级 UI。
    supports_api = False

    def __init__(self, metadata: TaskMetadata, pages_path: Path | None = None,
                 defaults: dict | None = None, **kwargs):
        self.metadata, self.pages_path = metadata, pages_path
        self.function = metadata.tags[0]
        self.defaults = {**type(self).defaults, **(defaults or {})}
        self.params = {**self.defaults, **kwargs}
        # 有 api_operation 且本类实现了 _perform_api 才允许引擎走 API 路由
        if metadata.api_operation and type(self)._perform_api is not DemoTask._perform_api:
            self.supports_api = True

    @property
    def name(self) -> str:
        return self.function

    def clone(self, **kwargs) -> "DemoTask":
        return type(self)(self.metadata, self.pages_path, defaults=self.defaults, **kwargs)

    def _perform_api(self, actor) -> Any:
        """子类覆盖此方法以实现 channel=auto 的 API 路径。

        默认实现抛 NotImplementedError，引擎捕获后降级 UI。
        禁止在此方法里做写操作——写操作不走 auto 路由。
        """
        raise NotImplementedError(f"{self.function} 未实现 API 路径")

    def perform(self, actor) -> Any:
        result = Pages(self.pages_path).call(self.function, actor, **self.params)
        # 单产出时取业务主键，避免调用方拿到整个 dict 当单号用。
        if len(self.metadata.produces) == 1 and isinstance(result, dict):
            key = self.metadata.produces[0]
            for candidate in (key, "docNo", "orderNo", "no"):
                if candidate in result:
                    return {key: result[candidate]}
            return {key: result}
        return result

def _task(function: str, intent: str, *, requires=(), produces=(), channel=Channel.UI,
          operation="", fallback="", read_only=False, pages_path=None,
          defaults: dict | None = None) -> DemoTask:
    return DemoTask(TaskMetadata(domain="demo", intent=intent, requires=tuple(requires),
        produces=tuple(produces), channel=channel, api_operation=operation, ui_fallback=fallback,
        read_only=read_only, tags=(function,)), pages_path, defaults=defaults)


class SelectCustomerTask(DemoTask):
    """选客户：channel=auto 时优先走 API 查客户详情，失败降级 UI。

    API 路径调用 DemoApi.customer_detail，产出 customer 与 payment_terms。
    """
    supports_api = True

    def _perform_api(self, actor) -> Any:
        api = getattr(actor, "demo_api", None)
        if api is None:
            raise NotImplementedError("未装配 DemoApi")
        if "customer_detail" not in getattr(api, "atlas_allowed_operations", set()):
            raise NotImplementedError("customer_detail 不在白名单")
        code_full = self.params.get("code_full") or self.params.get("code")
        division = self.params.get("division_code") or _session_default("division_code")
        response = api.customer_detail(code_full, division)
        if not response.get("ok"):
            raise NotImplementedError(f"API 查客户失败：{response.get('status')}")
        data = response.get("data", {})
        payments = data.get("collectPayments") or []
        pt = payments[0].get("paymentTerms", "") if payments else ""
        return {"customer": {"code": self.params.get("code"), "code_full": code_full,
                              "paymentTerms": pt}}


class ReadPtTask(DemoTask):
    """读 PT：channel=auto 时优先走 API 从客户详情取 PT，失败降级 UI。"""
    supports_api = True

    def _perform_api(self, actor) -> Any:
        api = getattr(actor, "demo_api", None)
        if api is None:
            raise NotImplementedError("未装配 DemoApi")
        customer = actor.ctx.get("customer", {})
        code_full = customer.get("code_full") or customer.get("code")
        if not code_full:
            raise NotImplementedError("ctx 缺少 customer.code_full")
        division = self.params.get("division_code") or _session_default("division_code")
        response = api.customer_detail(code_full, division)
        if not response.get("ok"):
            raise NotImplementedError(f"API 查客户失败：{response.get('status')}")
        data = response.get("data", {})
        payments = data.get("collectPayments") or []
        pt = payments[0].get("paymentTerms", "") if payments else ""
        return {"payment_terms": pt}


def core_tasks(pages_path: Path | None = None) -> list[DemoTask]:
    # 末尾第 9 项为业务默认参数：pages.py 的必需位置参数必须有默认值，
    # 否则 agent/DSL 省略参数时会在真实执行阶段 TypeError。
    # select_customer / read_pt 用具体子类以实现 channel=auto 的 API 路由。
    specs = [
        ("open_doc_list", "打开业务单据列表", (), ("doc_list_ready",), Channel.AUTO, "", "", True,
         {"page_key": "sales_order"}),
        ("click_new", "进入新建单据页", ("doc_list_ready",), ("new_document_ready",), Channel.UI, "", "", False,
         {"page_key": "sales_order"}),
        ("select_pt", "改选 PT 并同步保存模型", ("payment_terms",), ("selected_payment_terms",), Channel.UI, "", "", False,
         {"target": "NET30"}),
        ("ensure_warehouse", "选择仓库并刷新校验状态", ("new_document_ready",), ("warehouse",), Channel.UI, "", "", False,
         {"wh": "WH-DEMO"}),
        ("ensure_sales_person", "选择销售员", ("customer",), ("sales_person",), Channel.UI, "", "", False,
         {}),
        ("mount_other_header", "挂载扩展头，规避保存链缺陷", ("new_document_ready",), ("other_header_ready",), Channel.UI, "", "", True,
         {}),
        ("click_save", "保存业务单据", ("customer", "warehouse", "sales_person", "other_header_ready"), ("save_result",), Channel.UI, "", "", False,
         {}),
        ("wait_save_result", "等待保存结果并提取单号", ("save_result",), ("document_no",), Channel.AUTO, "", "wait_save_result", True,
         {}),
    ]
    tasks = [_task(*spec[:2], requires=spec[2], produces=spec[3], channel=spec[4],
                   operation=spec[5], fallback=spec[6], read_only=spec[7],
                   defaults=spec[8], pages_path=pages_path)
             for spec in specs]
    # 插入有 API 路由的具体子类
    select_meta = TaskMetadata(domain="demo", intent="选择客户并等待异步回填",
        requires=("new_document_ready",), produces=("customer",), channel=Channel.AUTO,
        api_operation="customer_detail", ui_fallback="select_customer", read_only=False,
        tags=("select_customer",))
    tasks.insert(2, SelectCustomerTask(select_meta, pages_path, defaults={"code": "CUST0000001", "code_full": "CUST00000010001"}))
    read_pt_meta = TaskMetadata(domain="demo", intent="读取 Payment Terms",
        requires=("customer",), produces=("payment_terms",), channel=Channel.AUTO,
        api_operation="customer_detail", ui_fallback="read_pt", read_only=True,
        tags=("read_pt",))
    tasks.insert(3, ReadPtTask(read_pt_meta, pages_path, defaults={}))
    return tasks

DemoTasks = core_tasks

"""真实 Actor 装配：登录只发生在会话层，UI/API 共享会话。"""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from .core import Channel, TaskMetadata
from .actor import Actor
from .config import AtlasConfig, load_dotenv
from .contracts import ContractRegistry
from .dsl import TaskRegistry
from .plugins.demo import DemoPlugin, make_demo_provider
from .probes import DataProvider
from .session import BrowserSession
from .tasks import AssertApi, CallEndpoint, UiAction


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise ImportError(str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class DemoRuntime:
    """一次登录、一个 Actor、一套任务/契约/探针；不复制原项目代码。"""
    def __init__(self, config: AtlasConfig, *, login: bool = True, account: str | None = None,
                 ou: str | None = None, division_code: str | None = None,
                 create_quota: int = 0, language: str | None = None):
        # 会话默认值来自业务插件唯一数据源（knowledge/session.json），密码永不在此。
        defaults = DemoPlugin().session_defaults()
        account = account or defaults["account"]
        ou = ou or defaults["ou"]
        division_code = division_code or defaults["division_code"]
        language = language or defaults["language"]
        self.config, self.account, self.ou, self.division_code = config, account, ou, division_code
        self.should_login, self.create_quota = login, create_quota
        # 选择器优先写英文文案；语言不统一会让菜单/字段定位随机失败。
        self.language = language
        self.session: BrowserSession | None = None
        self.actor: Actor | None = None
        self.demo_api = None
        self.provider: DataProvider | None = None

    def __enter__(self) -> "DemoRuntime":
        legacy = self.config.external_project("ATLAS_LEGACY_PROJECT", "../legacy-suite")
        load_dotenv(legacy)  # 沿用同事项目 .env；已有进程变量优先，不复制凭据。
        if not self.config.base_url:
            raise ValueError(f"缺少 {self.config.env} 对应的 DEMO_URL_* 配置")
        self.config.validate_base_url(self.config.base_url)
        self.session = BrowserSession(self.config).__enter__()
        self.actor = self.session.actor(self.account)
        if self.should_login:
            login_module = _load(legacy / "legacy-web/lib/login.py", "atlas_demo_session_login")
            _, password = self.config.credential(self.account)
            login_module.do_login(self.actor.page, self.config.base_url, self.account, password)
            self._wait_topbar(login_module)
            if self.ou:
                login_module.switch_to_ou(self.actor.page, self.ou)
                self._wait_topbar(login_module)
            # 语言单独显式切换：reload/切 OU 后可能重置为中文，不依赖隐式副作用。
            if self.language:
                login_module.switch_to_lang(self.actor.page, self.language)
        self.actor.bake_session()
        api_root = self.config.external_project("ATLAS_DEMO_API_PROJECT", "../legacy-api")
        self.demo_api = self.actor.api.attach_demo(api_root)
        self.actor.demo_api = self.demo_api
        self.demo_api.atlas_allowed_operations = frozenset({
            "customer_detail", "customer_list", "sale_order_list",
            "sale_order_change_list", "sale_order_return_list", "find_cust_details",
        })
        self.provider = make_demo_provider(self.config, self.demo_api, self.division_code)
        self.provider.quota.limit = self.create_quota
        plugin = DemoPlugin()
        self.actor.knowledge = plugin.knowledge()
        self.actor.contracts = self.contracts()
        # 自愈、经验与 API 实证在运行时统一装配，避免 DSL 里出现“模块存在但没接”。
        from .experience import ExperienceJournal
        from .healing import Healer
        from .session import ApiEvidence
        self.actor.healer = Healer(self.actor.knowledge)
        self.actor.journal = ExperienceJournal(self.config.reports_dir)
        self.actor.evidence = ApiEvidence()
        # 会话保活：长时间运行时检测到 auth_expired 自动重新烘焙 Cookie
        def _keepalive():
            if self.session and not self.session.page.is_closed():
                self.actor.bake_session()
        self.actor.api.set_keepalive(_keepalive)
        return self

    def registry(self) -> TaskRegistry:
        registry = TaskRegistry()
        DemoPlugin().register_tasks(registry)
        registry.register("api_call", lambda **kwargs: CallEndpoint(**kwargs))
        registry.register("ui_action", lambda **kwargs: UiAction(**kwargs))
        registry.register("demo_api", lambda **kwargs: DemoApiOperation(**kwargs))
        registry.register("assert_api", lambda **kwargs: AssertApi(**kwargs))
        registry.register("legacy_step", lambda **kwargs: LegacyStepTask(**kwargs))
        return registry

    def contracts(self) -> ContractRegistry:
        path = Path(DemoPlugin().knowledge_root) / "contracts/core.json"
        return ContractRegistry.load(path)

    def _wait_topbar(self, login_module) -> None:
        """顶栏可见性兜底（demo-014 变体）：switch_to_ou/switch_to_english 强依赖顶栏，
        但 SIT 偶发顶栏 >30s 才渲染。do_login 内部有 60s+reload 兜底，这里补齐
        OU/语言切换前的检查：顶栏已渲染则立即返回，未渲染则等待并 reload 一次。
        """
        page = self.actor.page
        indicator = getattr(login_module, "LOCATORS", {}).get("login_indicator")
        if not indicator:
            return
        try:
            page.wait_for_selector(indicator, state="visible", timeout=8000)
            return  # 顶栏已渲染（绝大多数情况），零开销通过
        except Exception:
            pass
        for attempt in range(2):
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            try:
                page.wait_for_selector(indicator, state="visible", timeout=30000)
                return
            except Exception:
                continue
        raise RuntimeError(f"顶栏指示器 {indicator} reload 2 次后仍未出现，URL={page.url}")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.session:
            self.session.__exit__(exc_type, exc, tb)


class LegacyStepTask:
    """把旧 step_N 接进 Atlas 流程：原目录、原 page/ctx 调用，不改旧代码。

    module 取旧项目的**相对路径**（相对 legacy-web/testcases/），例如：
        demo_sit_regression/01_sales_order_default_pt/sales_order_default_pt.py
    """
    def __init__(self, module: str, step: int, case_path: str | None = None, save_as: str = ""):
        self.module, self.step, self.case_path = module, int(step), case_path
        self.metadata = TaskMetadata(domain="demo", intent=f"旧步骤 {module}#{step}",
            produces=(save_as,) if save_as else (), channel=Channel.UI,
            read_only=False, tags=("legacy",))

    @property
    def name(self) -> str:
        return f"legacy:{self.module}#step_{self.step}"

    def perform(self, actor):
        from .legacy import LegacyStep
        root = self._legacy_root() / "legacy-web" / "testcases"
        case_module = Path(self.case_path) if self.case_path else root / self.module
        if not case_module.is_absolute():
            case_module = root / case_module
        return LegacyStep.from_module(case_module, self.step).perform(actor)

    def _legacy_root(self) -> Path:
        from .config import AtlasConfig
        return AtlasConfig.from_root().external_project("ATLAS_LEGACY_PROJECT", "../legacy-suite")


class DemoApiOperation:
    """显式调用 DemoApi 的方法，不允许从 YAML 任意访问对象/属性。"""
    def __init__(self, operation: str, args: dict | None = None, save_as: str = "response",
                 read_only: bool = True):
        self.operation, self.params = operation, args or {}
        self.metadata = TaskMetadata(domain="demo", intent=f"调用 {operation}",
            produces=(save_as,), channel=Channel.API, api_operation=operation, read_only=read_only)

    @property
    def name(self):
        return self.operation

    def perform(self, actor):
        api = getattr(actor, "demo_api", None)
        if api is None:
            raise RuntimeError("DemoRuntime 尚未装配 DemoApi")
        if self.operation not in getattr(api, "atlas_allowed_operations", set()):
            raise PermissionError(f"未声明允许的 DemoApi 操作：{self.operation}")
        method = getattr(api, self.operation)
        return method(**self.params)

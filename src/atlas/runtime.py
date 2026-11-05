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
from .plugin_api import resolve_plugin
from .plugins.demo import make_demo_provider
from .plugins.demo.legacy_case import LegacyCaseTask
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


def check_demo_api(config: AtlasConfig) -> None:
    """fail-fast 预检（#103，与 #90 legacy.check_exec_dir / #97 run 结构校验同口径）：
    DemoApi 适配模块路径必须在启动浏览器、登录、烘焙 Cookie **之前**检查。
    旧实现等 attach_demo 执行时才撞 FileNotFoundError——真实踩点：ATLAS_DEMO_API_PROJECT
    指错会先起真实 Chromium 走真实 SIT 登录，白烧一次会话才报路径错误，还可能在 SIT
    留半截现场。单独成函数供 CLI 在 DemoRuntime 构造前接「一行 stderr + rc 2」通道。"""
    path = config.api_project / "scripts" / config.api_module_file
    if not path.is_file():
        raise FileNotFoundError(
            f"找不到 DemoApi 适配目标：{path}"
            "（检查 ATLAS_DEMO_API_PROJECT / ATLAS_DEMO_API_FILE 是否指向旧项目的 scripts 目录）")


class DemoRuntime:
    """一次登录、一个 Actor、一套任务/契约/探针；不复制原项目代码。"""
    def __init__(self, config: AtlasConfig, *, login: bool = True, account: str | None = None,
                 ou: str | None = None, division_code: str | None = None,
                 create_quota: int = 0, language: str | None = None):
        # 会话默认值来自业务插件唯一数据源（knowledge/session.json），密码永不在此。
        defaults = resolve_plugin().session_defaults()
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
        legacy = self.config.legacy_project
        load_dotenv(legacy)  # 沿用同事项目 .env；已有进程变量优先，不复制凭据。
        if not self.config.base_url:
            raise ValueError(f"缺少 {self.config.env} 对应的 {self.config.env_url_key} 配置")
        self.config.validate_base_url(self.config.base_url)
        # fail-fast 预检（#103，口径与原因见 check_demo_api）：浏览器启动前就拒绝坏适配路径。
        # CLI 也在 DemoRuntime 构造前做过同口径检查，这里再走一次保证 examples 直接
        # `with DemoRuntime()` 同样受保护——is_file 幂等无副作用。
        check_demo_api(self.config)
        # #103 同口径补漏（run 轨）：旧项目根/web 子目录缺失也必须浏览器启动前拒绝。
        # 旧实现只预检 DemoApi 路径——ATLAS_LEGACY_WEB 指错要等下面 _load(login.py)
        # 才撞裸 FileNotFoundError：真实 Chromium 已起、SIT 登录已烧、消息不点名配置键
        # （#90 归因漂移）。pytest 轨的 check_exec_dir 早就点名 ATLAS_LEGACY_*；run 轨是漏网面。
        self.config.check_legacy_paths()
        self.session = BrowserSession(self.config).__enter__()
        # __enter__ 抛出时 `with DemoRuntime(...)` 不会调用 __exit__——浏览器已开却中途失败
        # （登录报错、_wait_topbar/switch_to_ou 超时[README 记录 SIT 常见]、attach_demo 缺文件、
        # contracts 缺失）会让 Chromium + Playwright 驱动进程双双泄漏，每次失败累积孤儿进程。
        # 故这里兜底：入口任一环节失败就关闭已开会话，再原样抛出**真因**（清理失败绝不顶掉原始异常，
        # 与 #82 的 BrowserSession.__exit__ 分工相反——那边是"有用例异常时不掩埋"，这边是"入口异常优先")。
        try:
            self.actor = self.session.actor(self.account)
            if self.should_login:
                login_module = _load(legacy / self.config.legacy_web / "lib/login.py",
                                     "atlas_demo_session_login")
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
            api_root = self.config.api_project
            self.demo_api = self.actor.api.attach_demo(
                api_root, module_file=self.config.api_module_file,
                class_name=self.config.api_class_name, spec_file=self.config.api_spec_file,
                app_header=self.config.app_header)
            self.actor.demo_api = self.demo_api
            # #121 面B：白名单是 Demo 业务资产，定义收敛 DemoPlugin.allowed_api_operations
            # 单一真相（旧实现硬编码在引擎装配行——换业务要改 runtime.py，违反 AGENTS 红线 11；
            # 插件消费者 tasks.py 读 api.atlas_allowed_operations 的语义不变）。
            self.demo_api.atlas_allowed_operations = DemoPlugin.allowed_api_operations
            self.provider = make_demo_provider(self.config, self.demo_api, self.division_code)
            self.provider.quota.limit = self.create_quota
            plugin = resolve_plugin()
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
        except BaseException:
            session, self.session = self.session, None   # 先摘走，避免 __exit__ 二次清理同一对象
            try:
                session.__exit__(None, None, None)
            except Exception:
                pass   # 关闭失败不得顶掉原始入口异常（login/OU/attach 的真因更重要）
            raise

    def registry(self) -> TaskRegistry:
        registry = TaskRegistry()
        resolve_plugin().register_tasks(registry)
        registry.register("api_call", lambda **kwargs: CallEndpoint(**kwargs))
        registry.register("ui_action", lambda **kwargs: UiAction(**kwargs))
        registry.register("demo_api", lambda **kwargs: DemoApiOperation(**kwargs))
        registry.register("assert_api", lambda **kwargs: AssertApi(**kwargs))
        registry.register("legacy_step", lambda **kwargs: LegacyStepTask(**kwargs))
        registry.register("legacy_case", lambda **kwargs: LegacyCaseTask(**kwargs))
        return registry

    def contracts(self) -> ContractRegistry:
        """契约解析统一委托 DemoPlugin.contracts()（私有→包内逐文件回退），与 atlas CLI 的
        contracts 展示共用单一来源，杜绝两处漂移。真实项目用 ATLAS_KNOWLEDGE_DIR 覆盖
        contracts/core.json 时，运行时校验与 `atlas contracts` 输出必然同源同档。"""
        return resolve_plugin().contracts()

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
        # 回收链落地：红线 3「create 要受配额限制并保留可回收信息」。旧实现 save_for_recycle
        # 只是方法、从未接入执行链——真实 SIT 建单仅存于内存 quota，进程退出即丢，
        # atlas recycle 永远列不出本次造的单。清理失败不得顶掉在用例异常（与 #82 分工一致）。
        # #121 面D：旧实现 session.__exit__ 抛错时直接向外传播，`recycle_error` 永远够不着
        # raise——两个清理环节（回收落盘/浏览器关闭）各自都是尽力而为，但彼此互掩：驱动崩了
        # 关不上浏览器的现场，恰好也是磁盘满写不进回收清单的现场，运维只看到一半真相
        # （#85 拦截无痕同红线：清理错误链也要全留痕）。现逐个 try 收集，多个错误聚合成
        # ExceptionGroup，单错误原样抛；有在用例异常时仍让位真因（#82 口径不变）。
        cleanup_errors: list[Exception] = []
        if self.provider is not None and self.provider.quota.created:
            try:
                self.provider.quota.save_for_recycle(self.config.reports_dir / "recycle_list.jsonl")
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
        if self.session:
            try:
                self.session.__exit__(exc_type, exc, tb)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
        if exc_type is None and cleanup_errors:
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise ExceptionGroup("退出清理失败（回收落盘/会话关闭）", cleanup_errors)


class LegacyStepTask:
    """把旧 step_N 接进 Atlas 流程：原目录、原 page/ctx 调用，不改旧代码。

    module 取旧项目的**相对路径**（相对 {legacy_web}/testcases/），例如：
        demo_sit_regression/01_sales_order_default_pt/sales_order_default_pt.py
    """
    def __init__(self, module: str, step: int, case_path: str | None = None, save_as: str = ""):
        self.step = self._validate_step(step)
        self.module, self.case_path, self.save_as = module, case_path, save_as
        self.metadata = TaskMetadata(domain="demo", intent=f"旧步骤 {module}#{self.step}",
            produces=(save_as,) if save_as else (), channel=Channel.UI,
            read_only=False, tags=("legacy",))

    @staticmethod
    def _validate_step(step) -> int:
        """step 必须是整数或十进制整值字符串。#121 面A：YAML 1.1 里 `step: yes/on/true`
        解析成 bool True，旧实现 `int(True)` 静默变 step_1——「步骤 1」执行「是」的笔误
        （#116 str(null)→'None' 假合法同族）；`step: 3.7` 静默截断成 step_3。构造期点名
        原始类型与值，不静默改写。字符串数字（"3"）是 YAML 引号形态，合法放行（#115 教训：
        归因修复不得把正常路径翻成错误）。"""
        if isinstance(step, bool):
            raise ValueError(f"legacy_step 的 step 必须是整数（step_N），收到 YAML 布尔值 "
                             f"{step!r}——`step: yes/on/true` 在 YAML 1.1 里是布尔，请写数字如 step: 1")
        if isinstance(step, int):
            value = step
        elif isinstance(step, float):
            if step != int(step):
                raise ValueError(f"legacy_step 的 step 必须是整数，收到小数 {step!r}"
                                 "（静默截断会把步骤号改掉）")
            value = int(step)
        elif isinstance(step, str):
            try:
                value = int(step)
            except ValueError:
                raise ValueError(f"legacy_step 的 step 必须是整数或数字字符串，收到 {step!r}") from None
        else:
            raise ValueError(f"legacy_step 的 step 必须是整数，收到 {type(step).__name__}：{step!r}")
        if value < 1:
            raise ValueError(f"旧项目的 step_N 从 1 起编号，收到 step: {value}")
        return value

    @property
    def name(self) -> str:
        return f"legacy:{self.module}#step_{self.step}"

    def perform(self, actor):
        from .legacy import LegacyStep
        root = self._legacy_root() / self._config().legacy_web / "testcases"
        case_module = Path(self.case_path) if self.case_path else root / self.module
        if not case_module.is_absolute():
            case_module = root / case_module
        result = LegacyStep.from_module(case_module, self.step).perform(actor)
        # 旧 step 返回形态由同事的库决定；声明 save_as 即「把返回值存进该键」——
        # 自兑现而不是靠引擎兜底，否则旧 step 恰好返回 dict 时会被 produce 契约校验拦崩。
        return {self.save_as: result} if self.save_as else result

    @staticmethod
    def _config():
        from .config import AtlasConfig
        return AtlasConfig.from_root()

    def _legacy_root(self) -> Path:
        return self._config().legacy_project


class DemoApiOperation:
    """显式调用 DemoApi 的方法，不允许从 YAML 任意访问对象/属性。"""
    def __init__(self, operation: str, args: dict | None = None, save_as: str = "response",
                 read_only: bool = True):
        self.operation, self.params, self.save_as = operation, args or {}, save_as
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
        # 按产出键包装：save_as → 整个响应。契约由 Task 兑现，不再靠引擎兜底猜测。
        return {self.save_as: method(**self.params)}

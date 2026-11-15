"""真实 Actor 装配：登录只发生在会话层，UI/API 共享会话。"""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from .core import Channel, TaskMetadata
from .actor import Actor
from .config import AtlasConfig, load_legacy_dotenv
from .contracts import ContractRegistry
from .credential_gate import sanitize_url
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


# 登录协议的四入口点（en-13）：新平台插件缺函数/属性时，旧实现到调用点才撞裸
# AttributeError（`module 'login' has no attribute 'switch_to_ou'`），消息不点名
# 「哪个入口缺、该补什么」——归因漂成框架缺陷（#90/#103/#122 面A 同族）。
# 判据单点在本函数，装配期（登录动作之前）一次性点名全部缺失项。
LOGIN_PROTOCOL = (("do_login", True), ("switch_to_ou", False),
                  ("switch_to_lang", False), ("LOCATORS", False))


def check_login_protocol(module, where: str = "lib/login.py") -> None:
    """登录协议装配期预检（en-13）：逐点 getattr 检查并点名缺失项。

    `do_login` 是**必填**（无它等于没有登录能力）；`switch_to_ou`/`switch_to_lang`/
    `LOCATORS` 是**可选**——缺失时调用点已各自有 getattr/条件守卫（`if self.ou:`、
    `getattr(login_module, "LOCATORS", {})`），故本函数只对「存在但不可调用」判错，
    不对缺失判错（那会把「单入口平台不切 OU」的正常路径翻成错误，#115 教训）。
    `do_login` 缺失或不可调用 → ValueError 点名文件与入口名，装配期拒绝（早于浏览器）。
    """
    names = []
    for name, required in LOGIN_PROTOCOL:
        member = getattr(module, name, None)
        if name == "LOCATORS":
            if member is not None and not isinstance(member, dict):
                names.append(f"LOCATORS（形态应为 dict，实际 {type(member).__name__}）")
            continue
        if member is None:
            if required:
                names.append(f"{name}（缺失，必填）")
            continue
        if not callable(member):
            names.append(f"{name}（应为可调用函数，实际 {type(member).__name__}）")
    if names:
        raise ValueError(
            f"{where} 不符合登录协议：{('、'.join(names))}"
            "——请在旧项目登录模块补齐（或用 ATLAS_LEGACY_* 指向正确模块）")


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


def resolve_account(config: AtlasConfig, account: str | None = None, *,
                    defaults: dict | None = None) -> str:
    """会话账号**解析单点**：显式 `account` 参数 > 插件 `session_defaults(env).account`
    （其内部已含 私有 `session.json` → `envs.json` 环境坐标 → `ATLAS_SESSION_*` 三层）。

    消费方 = `DemoRuntime.__init__` 与 CLI 预检（`_precheck_session_inputs`），不留第二份
    「参数 or 默认值」的写法（#92）。"""
    if account:
        return account
    if defaults is None:
        defaults = resolve_plugin().session_defaults(env=config.env)
    value = defaults.get("account") if isinstance(defaults, dict) else None
    return value or ""


def require_account(config: AtlasConfig, account: str | None) -> None:
    """空账号闸门**单点**（判据与消息唯一来源）。

    **为什么必须有 CLI 侧预检**：这段判据旧实现内联在 `DemoRuntime.__enter__`，而
    `with DemoRuntime(...)` 所在的 run/agent/explore 分支只捕 `AwaitingHuman` /
    `PermissionError`——`atlas run --env prod-eu`（该环境坐标 `account` 为空）抛出的
    ValueError 直接穿透 `main()`：用户拿到 ~20 行 traceback，可操作的那一行被埋掉。
    base_url / `check_demo_api` / `check_legacy_paths` 三条早就有「`with` 之前预检 →
    一行 stderr + rc 2」的通道（#103 同族），唯独账号缺这一条，于是这条闸门自己成了
    #103 想消灭的那种「失败点漂到不该漂的地方」。现 CLI 在 `with` 前调它（rc 2），
    `__enter__` 仍调它兜直接使用 `DemoRuntime` 的调用方（examples/探索轨道）。

    账号为空还照跑会走 `credential("")`——命中旧项目 `.env` 里 `ISERVICE_PWD_` 这类
    「键在、值为空」的残留键，拿到空密码后照样起 Chromium 走 SSO，失败点漂进旧登录
    代码、消息不提「哪个配置没给」（#103 的原始形态）。
    """
    if not str(account or "").strip():
        raise ValueError(
            f"环境 {config.env!r} 没有可用账号：插件会话默认值与 envs.json 环境坐标"
            f"都为空，且未传 --account / ATLAS_SESSION_ACCOUNT。"
            f"补救：传 --account <账号>，或把账号写进私有知识点"
            f"（knowledge/<域>/envs.json 的 environments.{config.env}.account）"
        )


class DemoRuntime:
    """一次登录、一个 Actor、一套任务/契约/探针；不复制原项目代码。"""
    def __init__(self, config: AtlasConfig, *, login: bool = True, account: str | None = None,
                 ou: str | None = None, division_code: str | None = None,
                 create_quota: int = 0, language: str | None = None):
        # 会话默认值来自业务插件唯一数据源（knowledge/session.json + envs.json 的环境坐标，
        # 最后 ATLAS_SESSION_* 覆盖）。传 env 是为了让 `--env uat-eu` 自动带出该环境的账号/OU
        # ——旧实现不传，账号恒空串，真实跑时 `credential("")` 命中 `ISERVICE_PWD_` 空值键，
        # 白烧一次登录才在 SSO 页面报错（#103 归因漂移同族）。
        defaults = resolve_plugin().session_defaults(env=config.env)
        account = resolve_account(config, account, defaults=defaults)
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
        # en-5/ob-5：旧仓 .env 只并入**白名单键**（ATLAS_* 与当前前缀的凭据/URL 键），
        # 其余键隔离 + WARN 计数——旧实现整份 setdefault 进 os.environ，上一平台的
        # `ISERVICE_*`/`DEMO_*` 会跨平台残留，后续 os.environ.get 读到串味值（归因漂移）。
        # 解码/形态判据仍与 load_dotenv 共用 `_read_dotenv_pairs`（#92 单点）。
        load_legacy_dotenv(legacy, where=f"{legacy.name}/.env")
        # 空账号闸门必须在开浏览器 / 登录之前（#103 同族，判据与消息在 require_account 单点，
        # 同一函数也被 CLI 预检调用以投递 rc 2）。`credential("")` 会命中 `ISERVICE_PWD_`
        # 这类「键在、值为空」的残留键，拿到空密码后照样起 Chromium 走 SSO，失败点漂进旧登录
        # 代码、消息不提「哪个配置没给」。没指定 --account 且插件/知识库也没给出该环境坐标时，
        # 这里点名 env 与两条补救路径。位置有意在 `load_dotenv(legacy)` **之后**：账号可能只写在
        # 旧项目 .env 的 `ATLAS_SESSION_ACCOUNT` 里，早于它判定会误拒正常路径（#115）。
        if self.should_login:
            require_account(self.config, self.account)
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
                # en-13：协议预检放在 do_login 调用之前——缺失/坏形态在此点名，
                # 不让 AttributeError 漂到登录链深处（此处浏览器已开，但登录动作尚未发出）。
                check_login_protocol(
                    login_module, where=f"{self.config.legacy_web}/lib/login.py")
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
            self.demo_api.atlas_allowed_operations = resolve_plugin().allowed_api_operations
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

    def start_flow(self) -> None:
        """pf-5 多 flow 串行（`atlas run f1.yaml f2.yaml ...`）：每条 flow 开始前重置
        会话级变量产出（actor.ctx）。

        会话复用只共享「一次登录的浏览器/账号/Cookie/配额」；**变量产出不跨 flow 泄漏**
        ——上一条 flow 的 {{doc_no}} 若流进下一条 flow 的插值/断言，等于静默拿错数据跑
        真实系统（#7 同红线）。需要把产出传给后续步骤，请写进同一条 flow（compose 合成
        或任务库复用）；跨 flow 的数据传递是显式编排的职责，不由会话层兜底。

        ctx 里同时有引擎簿记键（_steps_skipped/_dispatch_routes/_auto_degraded_to_ui/
        _env_blocked/_flow_skipped/_data_actions/_data_skipped），同为 flow 级事实，
        与产出键一起随 flow 重置；provider 的探针/配额状态是会话级资产，跨 flow 连续。
        单 flow 路径不调用本方法（历史行为零涟漪）。"""
        if self.actor is not None:
            self.actor.ctx.clear()

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
        # C4② 之一：SSO 回调/登录跳转的 URL query 常带 ticket/code/state——框架自己把
        # page.url 写进异常文本，就是确定发生的泄漏面（该异常会进 runs.jsonl/草稿/终端）。
        # 净化用 credential_gate 的逐键刀：业务参数（docNo/page/ou）保留（#85 取证证据面）。
        raise RuntimeError(f"顶栏指示器 {indicator} reload 2 次后仍未出现，"
                           f"URL={sanitize_url(page.url)}")

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

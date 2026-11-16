"""真实 Actor 装配：登录只发生在会话层，UI/API 共享会话。"""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from .core import Channel, TaskMetadata
from .actor import Actor
from .config import AtlasConfig, legacy_check_skipped, load_legacy_dotenv
from .contracts import ContractRegistry
from .credential_gate import sanitize_url
from .dsl import TaskRegistry
from .plugin_api import resolve_plugin
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


def flow_needs_legacy(tasks) -> bool:
    """ob-5：flow 是否真的需要旧项目目录（**compile 期判定**，判据=任务类属性）。

    `legacy_step`/`legacy_case` 的 Task 类声明 `needs_legacy_project = True`；其余任务
    （UI/API/通用 Task）不依赖旧仓目录。清单不写在本函数里——新增 legacy 族任务只改
    任务类一处（#92），本函数只问属性、不猜任务名。
    """
    return any(getattr(task, "needs_legacy_project", False) for task in tasks)


def legacy_required_for(*, no_login: bool, tasks) -> bool:
    """ob-5 判据单点：本次 run 是否需要旧仓目录。

    - 登录轨（`no_login=False`）**恒需要**——登录模块 `{legacy_web}/lib/login.py` 在旧仓；
    - `--no-login` 时只看 flow 是否含 legacy 任务（`flow_needs_legacy`）。

    单 flow 与多 flow（多 flow 传全部任务的并集）共用，CLI 不复制第二条 if（#92）。
    """
    return (not no_login) or flow_needs_legacy(tasks)


def check_legacy_required(config: AtlasConfig, *, required: bool) -> None:
    """ob-5：旧仓路径预检的**唯一消费口**（运行时与 CLI 预检共用，#92）。

    `required=False`（典型：`--no-login` 且 flow 无 legacy 任务）时跳过预检——旧实现
    无条件要求旧仓目录，无旧仓平台只能被迫假造目录。显式 `ATLAS_SKIP_LEGACY_CHECK=1`
    时同样跳过但打一行 ASCII WARN：跳过的只是 fail-fast 预检，真实需要旧仓的调用点
    （登录模块 `_load(login.py)` / legacy 任务执行）仍按自身语义报错，绝不静默成功。
    """
    if not required:
        return
    if legacy_check_skipped():
        print("WARN: ATLAS_SKIP_LEGACY_CHECK=1——跳过旧项目路径预检；"
              "真实需要旧仓的步骤仍会在使用点报错", file=sys.stderr)
        return
    config.check_legacy_paths()


def precheck_session_inputs(config: AtlasConfig, *, account: str | None = None,
                            no_login: bool = False,
                            legacy_required: bool = True,
                            dotenv_where: str = "旧仓 .env") -> None:
    """**运行时入口输入预检的唯一序列**（判定顺序即语义，勿在调用点重排/裁剪）。

    为什么要有这个函数：这段逻辑此前在 CLI 里被**抄了 4 份**（`run` / `agent --execute` /
    多 flow / `explore`），第 5 份在 `DemoRuntime.__enter__`。单点化只收敛了**判据函数**
    （`check_demo_api` 等各一份），没收敛**调用序列**——于是 `_explore` / `_explore_auto`
    漏掉了 base_url 与 DemoApi 两条，实测：

        atlas run     → rc 2 + 一行「ValueError: 缺少 sit 对应的 DEMO_URL_SIT 配置」
        atlas explore → rc 1 + 16 行裸 traceback（runtime.py 内联判据穿透 main）

    这正是 #103 想消灭的「失败点漂到不该漂的地方」，也是「compile 绿 ≠ run 绿」的同族
    第 3 例。收敛成一个序列后，新增第 6 个入口不会再有「抄漏一条」的机会。

    判定顺序（每一步都有理由，改序会翻正常路径）：

    1. `load_legacy_dotenv` —— 必须最先。账号/base_url 可能**只写在旧仓 .env** 里
       （`ATLAS_SESSION_ACCOUNT` / `URL_*` 在白名单内），早于它判定会误拒正常路径（#115）。
       只并入白名单键，白名单外的键隔离 + WARN（en-5）。
    2. `base_url` 存在性 + `validate_base_url` —— 缺环境地址是输入错误，不是执行失败。
    3. `check_demo_api` —— 坏适配路径必须在开浏览器/真实登录之前拒绝（#103）。
    4. `check_legacy_required` —— 按需判旧仓目录（ob-5：`--no-login` 且无 legacy 任务
       时不该要求旧仓存在）。
    5. `require_account` —— **只在真要登录时**（`no_login=False`）。`--no-login` 时
       没有会话就没有「拿空账号去登录」这件事，判它会把正常路径翻成错误（#115）。

    抛 `ValueError` / `OSError`（`FileNotFoundError` 是 `OSError` 子类）——调用方统一
    走「一行 stderr + rc 2」通道，不得让它穿透成 traceback。

    `dotenv_where`：传给 `load_legacy_dotenv` 的 WARN 出处标签。`DemoRuntime.__enter__`
    传 `"{旧仓名}/.env"`（日志里点名是哪个仓），CLI 侧用缺省值——纯文案，不改判据。
    """
    load_legacy_dotenv(config.legacy_project, where=dotenv_where)
    if not config.base_url:
        raise ValueError(f"缺少 {config.env} 对应的 {config.env_url_key} 配置")
    config.validate_base_url(config.base_url)
    check_demo_api(config)
    check_legacy_required(config, required=legacy_required)
    if not no_login:
        require_account(config, resolve_account(config, account))


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


def _session_default(defaults, key: str, plugin_name: str):
    """插件 `session_defaults` 的必需键取值单点：缺键/坏形态点名插件与键名。

    插件协议（`atlas config add` 生成的骨架同形）以
    `account / ou / division_code / language` 四键承载会话默认值；本函数只服务
    **调用方没显式给值、必须回落到插件默认值**的键（显式参数由上游 `or` 短路，
    不再向插件索取——#115：别把正常路径翻成错误）。

    旧实现 `defaults["ou"]` 直取：第三方插件只实现最小形态 `{"account": ...}` 时抛
    裸 `KeyError: 'ou'`，消息不含插件名，排障者只会怀疑框架（同模块 `resolve_account`
    对非映射 defaults 反而有 isinstance 守卫——两套宽严是本次收敛的动机）。现在失败点
    点名「哪个插件的 session_defaults 少了哪个键」，非映射形态同样点名类型。
    """
    if not isinstance(defaults, dict):
        raise RuntimeError(
            f"插件 {plugin_name} 的 session_defaults 返回 {type(defaults).__name__} 而非映射，"
            f"无法取会话默认值 {key!r}")
    if key not in defaults:
        raise RuntimeError(f"插件 {plugin_name} 的 session_defaults 缺少 {key!r}")
    return defaults[key]


def build_registry() -> TaskRegistry:
    """Task registry 装配**单点**：执行侧（`DemoRuntime.registry()`）与结构预检侧
    （`cli._full_registry()`，compile/run/export-pytest/schedule 的 validate_flow）共用。

    两处各写一份的旧做法曾让「compile 绿 ⇒ run 绿」这条承诺失效：P2-2 把
    `demo_api` 改成按插件注册（白名单为空的插件不该看到该任务）后，预检侧仍无条件
    注册，于是 iservice 下含 `demo_api` 的 flow 过得了 `atlas compile`、却要在真实
    执行期才撞「未注册的 Task」（#92「判据两把尺」在装配面的形态）。
    """
    registry = TaskRegistry()
    plugin = resolve_plugin()
    plugin.register_tasks(registry)
    registry.register("api_call", lambda **kwargs: CallEndpoint(**kwargs))
    registry.register("ui_action", lambda **kwargs: UiAction(**kwargs))
    # P2-2：`demo_api` 任务委托 `actor.demo_api`，可调用操作白名单是**插件资产**
    # （`allowed_api_operations`，红线 11）。白名单为空 = 该插件没有任何可用 API
    # 操作（iservice 如实留空），注册此任务只会把编译期「未注册的 Task」错误推迟成
    # 运行时 PermissionError——故按插件注册，iservice 装配路径不再看到 demo 任务。
    if plugin.allowed_api_operations:
        registry.register("demo_api", lambda **kwargs: DemoApiOperation(**kwargs))
    registry.register("assert_api", lambda **kwargs: AssertApi(**kwargs))
    registry.register("legacy_step", lambda **kwargs: LegacyStepTask(**kwargs))
    registry.register("legacy_case", lambda **kwargs: LegacyCaseTask(**kwargs))
    return registry


class DemoRuntime:
    """一次登录、一个 Actor、一套任务/契约/探针；不复制原项目代码。"""
    def __init__(self, config: AtlasConfig, *, login: bool = True, account: str | None = None,
                 ou: str | None = None, division_code: str | None = None,
                 create_quota: int = 0, language: str | None = None,
                 legacy_required: bool | None = None):
        # ob-5：`legacy_required` 由调用方按「本次要跑的 flow 是否需要旧仓」传入
        # （CLI 在 compile 期判定）。缺省 None = 旧行为（无条件预检）——examples/脚本
        # 直接 `with DemoRuntime()` 时语义逐字节不变（#115）。
        self.legacy_required = True if legacy_required is None else bool(legacy_required)
        # 会话默认值来自业务插件唯一数据源（knowledge/session.json + envs.json 的环境坐标，
        # 最后 ATLAS_SESSION_* 覆盖）。传 env 是为了让 `--env uat-eu` 自动带出该环境的账号/OU
        # ——旧实现不传，账号恒空串，真实跑时 `credential("")` 命中 `ISERVICE_PWD_` 空值键，
        # 白烧一次登录才在 SSO 页面报错（#103 归因漂移同族）。
        plugin = resolve_plugin()
        # 插件名用于缺键报错归因；协议成员 `name` 缺失时回落类名（不因一个展示名把
        # 装配翻成 AttributeError——与 resolve_account 的宽容面同一口径）。
        plugin_name = getattr(plugin, "name", "") or type(plugin).__name__
        defaults = plugin.session_defaults(env=config.env)
        account = resolve_account(config, account, defaults=defaults)
        # 缺键只在**真正要回落**时判错（显式 --ou/--division/--language 给的路径不碰插件键，
        # #115 不翻正常路径）；错误消息带插件名与键名（旧实现裸 KeyError: 'ou'）。
        ou = ou or _session_default(defaults, "ou", plugin_name)
        division_code = division_code or _session_default(defaults, "division_code", plugin_name)
        language = language or _session_default(defaults, "language", plugin_name)
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
        # 入口输入预检**唯一序列**（`precheck_session_inputs` 单点，CLI 四入口同款）。
        # 此处再走一遍是给 direct-use 的调用方兜底（examples/探索轨道直接
        # `with DemoRuntime()`）：CLI 恰好在 `with` 之前也调了同一个函数，幂等。
        #
        # 顺序（`load_legacy_dotenv` 必须最先，理由见该函数 docstring）与本类历史行为
        # **逐条等价**：旧实现把 base_url/demo_api/legacy 三条写在 require_account 之后，
        # 收敛后统一为「账号在最后」——差异只在「同时缺 base_url 与账号」时报哪一条，
        # 两者都是 rc 2 输入错误，无行为后果（且缺 base_url 更基础，先报它对用户更可操作）。
        # en-5/ob-5：旧仓 .env 只并入白名单键，其余隔离 + WARN（`where` 点名是哪个仓）。
        precheck_session_inputs(
            self.config,
            account=self.account,
            no_login=not self.should_login,
            legacy_required=self.legacy_required,
            dotenv_where=f"{legacy.name}/.env")
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
            plugin = resolve_plugin()
            self.demo_api.atlas_allowed_operations = plugin.allowed_api_operations
            # P2-1：provider 从**插件**取（协议成员 `make_provider(config, api, division_code)`，
            # config_add 生成的骨架与 iservice 同形）。旧实现无条件 `make_demo_provider`——
            # ATLAS_PLUGIN=iservice 时数据供给仍是 demo 的 SalesOrder/Customer 探针，
            # 业务插件的 make_provider 成了没人调用的死方法（换业务只换插件断在这里）。
            self.provider = plugin.make_provider(self.config, self.demo_api, self.division_code)
            self.provider.quota.limit = self.create_quota
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
        return build_registry()

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
    # ob-5：compile 期判定「这个 Task 需要旧项目目录」的唯一标记（runtime.flow_needs_legacy）。
    needs_legacy_project = True

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

"""只从环境/.env 取秘密；业务 YAML 不承担凭据与秘密配置。"""
from __future__ import annotations
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# 生产写护栏的显式放行开关（重构波松绑：默认关，行为与放行前逐字节一致）。
# 打开时调用方**必须**打这一行 ASCII 警告并把该决定写进 runs.jsonl（可审计）——
# 不得做成静默生效的隐式默认（AGENTS §1 #2 的放松不是「默认允许」，是「显式 opt-in」）。
PROD_WRITE_ENV_KEY = "ATLAS_ALLOW_PROD_WRITE"
PROD_WRITE_WARNING = ("WARN: ATLAS_ALLOW_PROD_WRITE=1 已放行生产环境写操作"
                      "（默认应为关；请核对 runs.jsonl 留痕）")

# 键名后缀是引擎的环境知识；前缀是「对接哪个业务项目」的开关。
# 默认 DEMO_ 对应仓库自带的虚构示例；指向真实项目时用 ATLAS_ENV_PREFIX 覆盖
# （如 ISERVICE_），通用引擎里不出现任何真实业务字面量。
ENV_PREFIX_DEFAULT = "DEMO_"
ENV_URL_SUFFIXES = {
    "sit": "URL_SIT", "uat-eu": "URL_UAT_EU",
    "uat-amer": "URL_UAT_AMER", "uat-hk": "URL_UAT_HK",
    "prod-eu": "URL_PROD_EU", "prod-amer": "URL_PROD_AMER",
    "prod-hk": "URL_PROD_HK",
}

def env_prefix() -> str:
    """凭据/URL 键前缀。默认 DEMO_；真实项目用 ATLAS_ENV_PREFIX 覆盖。"""
    return os.environ.get("ATLAS_ENV_PREFIX", ENV_PREFIX_DEFAULT)

def env_url_keys(prefix: str | None = None) -> dict[str, str]:
    prefix = env_prefix() if prefix is None else prefix
    return {name: f"{prefix}{suffix}" for name, suffix in ENV_URL_SUFFIXES.items()}

# 默认前缀下的完整键名。环境集合校验与 plugins/demo/knowledge/envs.json 以它为准。
ENV_URL_KEYS = env_url_keys(ENV_PREFIX_DEFAULT)

def load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.exists():
        return
    # 编码兜底（Windows 真实路径）：记事本旧版「另存为 UTF-8」带 BOM，PowerShell 5 的
    # Set-Content/Out-File 默认 UTF-16 LE——旧实现直读 utf-8：utf-16 文件 UnicodeDecodeError
    # 从 main()/runtime 装配链裸崩 traceback，UTF-8 BOM 则把 `DEMO_USERNAME` 静默变成
    # `\ufeffDEMO_USERNAME` 永不命中——人只会看到「未配置凭据」，永远查不到根因（文件明明在）。
    # 必须先看 BOM 字节再解码：纯「utf-8 试失败再试 utf-16」会被反坑——UTF-16LE 的纯 ASCII 内容
    # （每字符 <0x80）恰是合法 UTF-8，解出一串夹 \x00 的垃圾键静默入库，比崩更隐蔽。
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encoding = "utf-16"
    elif data[:3] == b"\xef\xbb\xbf":
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        text = data.decode(encoding)
    except (UnicodeDecodeError, OSError) as exc:
        raise ValueError(f"{path} 无法按 {encoding} 解码（期望 UTF-8；PowerShell 请 "
                         f"`Set-Content -Encoding UTF8`），原始错误：{exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or key[0] in "\"'" or " " in key:
            raise ValueError(f"{path} 存在无效环境变量名（含空格/引号或为空）：{key!r}")
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)

def _asset_path(root: Path, key: str, default: str) -> Path:
    value = Path(os.environ.get(key, default))
    return (root / value).resolve() if not value.is_absolute() else value.resolve()

def _int_env(key: str, default: int, *, minimum: int = 0) -> int:
    """整数环境项解析（flaky 兜底旋钮的单一读取点）：缺省/空白 → default；
    非法值（非整数或低于 minimum）点名键名 raise ValueError——坏配置不静默当默认值
    （#79/#80 口径：用户输入错误宁可点名，不猜）。"""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{key} 必须是整数，收到 {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{key} 不能小于 {minimum}，收到 {value}")
    return value

def _flag_env(key: str) -> bool:
    """布尔开关（显式 opt-in）的单一读取点：**只认字面 `'1'` 为开**。

    缺省/空白 → 关；其它任何值（`yess`/`true`/`on`/`0`/`yes`…）→ 关且 WARN。
    坏值绝不静默生效：开关类配置写错是「看着像开其实没开」的假绿入口——若坏值当开，
    生产写这种不可逆动作会被一个拼错的环境变量悄悄放行；若坏值当关却不吭声，人又
    以为已开。两种都坏，所以「按关处理 + 点名」是唯一诚实落点（#79/#80 口径）。
    """
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return False
    if raw.strip() == "1":
        return True
    print(f"WARN: {key}={raw!r} 不是合法开关值（只认 '1'），按关处理", file=sys.stderr)
    return False


def check_legacy_paths(root: Path, web: str) -> None:
    """旧项目根/web 子目录存在性预检——判据与消息的单一真相。

    #103 fail-fast 同口径延伸：run/agent --execute 装配链（runtime.__enter__）要在
    浏览器启动、真实登录**之前**拒绝坏路径。旧实现只预检 DemoApi 路径，
    ATLAS_LEGACY_WEB 指错要等 `_load(legacy_web/lib/login.py)` 执行时才撞裸
    FileNotFoundError——真实 Chromium 已起、SIT 登录已烧，还可能在真实环境留半截现场，
    且错误消息不点名任何配置键（#90 归因漂移同族）。pytest 轨的 check_exec_dir
    早就有这套消息；run 轨是漏网面。两处（AtlasConfig 方法 / LegacyAdapter）共用此函数，
    杜绝改一漏一漂移（#92/#97 同红线）。"""
    if not root.is_dir():
        raise FileNotFoundError(
            f"旧项目根不存在：{root}（检查 ATLAS_LEGACY_PROJECT / .env 的 legacy 路径）")
    exec_dir = root / web
    if not exec_dir.is_dir():
        raise FileNotFoundError(
            f"旧项目 web 子目录不存在：{exec_dir}"
            f"（检查 ATLAS_LEGACY_WEB，旧版可能是 legacy-web 或 iservice）")

@dataclass(frozen=True)
class AtlasConfig:
    root: Path
    env: str = "sit"
    mode: str = "run"
    headless: bool = True
    channel: str = "chrome"

    def __post_init__(self) -> None:
        if self.mode not in {"run", "debug", "heal"}:
            raise ValueError("mode 只支持 run/debug/heal")
        if self.env not in ENV_URL_KEYS:
            raise ValueError(f"未知环境 {self.env!r}，可选：{', '.join(ENV_URL_KEYS)}")

    @classmethod
    def from_root(cls, root: str | Path | None = None, **overrides) -> "AtlasConfig":
        project_root = Path(root or os.environ.get("ATLAS_ROOT", Path.cwd())).resolve()
        if not (project_root / ".atlas-root").exists() and "ATLAS_ROOT" not in os.environ:
            for candidate in (project_root, *project_root.parents):
                if (candidate / ".atlas-root").exists():
                    project_root = candidate
                    break
        load_dotenv(project_root)
        values = {
            "env": os.environ.get("ATLAS_ENV", "sit"),
            "mode": os.environ.get("ATLAS_MODE", "run"),
            "headless": os.environ.get("ATLAS_HEADED", "0") != "1",
            "channel": os.environ.get("ATLAS_BROWSER_CHANNEL", "chrome"),
        }
        values.update(overrides)
        return cls(root=project_root, **values)

    @property
    def base_url(self) -> str:
        return os.environ.get(self.env_url_key, "").rstrip("/")

    @property
    def env_url_key(self) -> str:
        return env_url_keys()[self.env]

    @property
    def is_production(self) -> bool:
        return self.env.startswith("prod-")

    @property
    def allow_prod_write(self) -> bool:
        """`ATLAS_ALLOW_PROD_WRITE=1`：显式放行生产环境写操作（默认关）。

        重构波松绑：原 `validate_write` 对 `prod-*` 无条件拒绝，无任何放行通道。
        现降级为显式 env 开关——**默认关时行为与放行前逐字节一致**（红线 #2 的默认
        保护不松动）；打开才放行，且调用方（engine）须打 ASCII 警告并把该事实写进
        runs.jsonl（可审计）。开关值写坏一律按关处理（见 `_flag_env`），不静默生效。"""
        return _flag_env(PROD_WRITE_ENV_KEY)

    @property
    def prod_write_allowed(self) -> bool:
        """「生产环境 ∧ 显式放行」——写护栏与其审计留痕的**唯一判据位**（#92）。

        engine 据此打警告 + 留痕、`validate_write` 据此决定是否抛——同一判据不留第二份。"""
        return self.is_production and self.allow_prod_write

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def slow_mo(self) -> int:
        return 300 if self.mode == "debug" and not self.headless else 0

    # ---- flaky 兜底（默认全部保守：重试 0 次 / 超时关闭 → 既有行为逐字不变，零回归）----
    @property
    def step_retry(self) -> int:
        """步骤级 flaky 重试次数（ATLAS_STEP_RETRY，默认 0=不重试）。仅对「只读 Task +
        可恢复的交互类失败」生效；写操作与断言失败永不重试（红线 #7：重试不放宽断言）。"""
        return _int_env("ATLAS_STEP_RETRY", 0)

    @property
    def step_retry_interval_ms(self) -> int:
        """步骤级重试间隔毫秒（ATLAS_STEP_RETRY_INTERVAL_MS，默认 500）。"""
        return _int_env("ATLAS_STEP_RETRY_INTERVAL_MS", 500)

    @property
    def case_timeout(self) -> int:
        """用例级超时秒数（ATLAS_CASE_TIMEOUT，默认 0=关闭）。>0 时启用后台看门狗，
        超时向主线程投递 KeyboardInterrupt → 记 interrupted 终态（绝不伪装 pass）。
        默认关闭保证零回归；UI AUTO TEST 的对齐值建议 600。"""
        return _int_env("ATLAS_CASE_TIMEOUT", 0)

    def external_project(self, key: str, default: str) -> Path:
        return _asset_path(self.root, key, default)

    # ---- 外部项目对接映射 ----
    # 默认值对应仓库自带的虚构 demo；指向真实旧项目时用 ATLAS_* 覆盖，
    # 使「通用引擎 + 虚构示例」与「本机真实资产」可以共存，且引擎里不留真实字面量。
    @property
    def legacy_web(self) -> str:
        """旧项目内的 web 子目录名（默认 legacy-web，真实项目可能是 iservice）。"""
        return os.environ.get("ATLAS_LEGACY_WEB", "legacy-web")

    @property
    def legacy_test_node(self) -> str:
        return os.environ.get("ATLAS_LEGACY_TEST_NODE",
                              "testcases/demo_sit_regression/test_demo_sit_regression.py")

    @property
    def legacy_project(self) -> Path:
        return self.external_project("ATLAS_LEGACY_PROJECT", "../legacy-suite")

    @property
    def api_project(self) -> Path:
        return self.external_project("ATLAS_DEMO_API_PROJECT", "../legacy-api")

    @property
    def api_module_file(self) -> str:
        """API 客户端模块文件名（默认 demo_api.py，真实项目可能是 ics_api.py）。"""
        return os.environ.get("ATLAS_DEMO_API_FILE", "demo_api.py")

    @property
    def api_class_name(self) -> str:
        return os.environ.get("ATLAS_DEMO_API_CLASS", "DemoApi")

    @property
    def api_spec_file(self) -> str:
        return os.environ.get("ATLAS_DEMO_API_SPEC", "demo_endpoints.json")

    @property
    def app_header(self) -> tuple[str, str]:
        """业务识别头（默认 demo-app: legacy-web）；后端会校验，改名后必须同步。"""
        return (os.environ.get("ATLAS_APP_HEADER_KEY", "demo-app"),
                os.environ.get("ATLAS_APP_HEADER_VALUE", "legacy-web"))

    def credential(self, username: str) -> tuple[str, str]:
        prefix = env_prefix()
        safe = re.sub(r"[^A-Za-z0-9_]", "_", username).upper()
        key = f"{prefix}PWD_{safe}"
        password = os.environ.get(key)
        if password is None and username == os.environ.get(f"{prefix}USERNAME"):
            password = os.environ.get(f"{prefix}PASSWORD", "")
        if password is None and self.is_production and username == os.environ.get(f"{prefix}PROD_USERNAME"):
            password = os.environ.get(f"{prefix}PROD_PASSWORD", "")
        return key, password or ""

    def validate_write(self, remark: str = "") -> None:
        # 生产禁写的放松是**显式 opt-in**（ATLAS_ALLOW_PROD_WRITE=1）：默认仍拒绝，
        # 行为与放行前逐字节一致。放行时的警告与 runs.jsonl 留痕由调用方（engine）负责
        # ——本方法只做「抛 or 不抛」的判据，写盘/终端副作用不在这里（config 无 recorder）。
        if self.is_production and not self.allow_prod_write:
            raise PermissionError("生产环境禁止建单、保存、提交、删除等写操作")
        # 无条件要求备注标记：原 `if remark and ...` 让空备注绕过——把 ctx["remark"]
        # 置空即可跳过「非人工创建」护栏。自动单据必须带标记，空/缺备注本身即违规。
        # 放行生产写**不**豁免此标记（红线 #3 与 #2 独立，松绑 #2 不牵连 #3）。
        if "非人工创建" not in remark:
            raise ValueError("自动创建业务单据的备注必须包含「非人工创建」（空备注不允许）")

    def validate_base_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url 必须是有效的 HTTP(S) 地址")

    def check_legacy_paths(self) -> None:
        """实例门面：判据与消息见模块级 `check_legacy_paths` 单一真相（#92）。"""
        check_legacy_paths(self.legacy_project, self.legacy_web)

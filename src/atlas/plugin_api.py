"""业务插件解析单一入口（引擎级缝）。

cli/runtime 里旧硬编码 `DemoPlugin()` 的机械替换点：未设 ATLAS_PLUGIN 时行为与旧实现
完全一致（含 make_provider 语义）；设 ATLAS_PLUGIN=<domain> 时加载对应插件。

引擎不含业务字面量——domain→类 的映射由插件注册（register_plugin），默认仅 demo
（引擎自带的虚构示例插件）。这是本轮唯一引擎改动，不得引入业务字面量。

Gap2 增强：未在 registry 注册的 domain 尝试动态加载——ATLAS_PLUGIN 含路径分隔符/
以 .py 结尾 → 按文件加载取模块级 PLUGIN；否则 → import atlas.plugins.<name> → import
<name>（外部项目把 PYTHONPATH 指到项目根后 `plugins.<domain>` 可被发现）。
加载成功返回实例；全部失败点名已注册清单与尝试过的路径（#90/#103 归因口径）。

en-1（wave5）：回落 demo **不再沉默**——未设 ATLAS_PLUGIN 时在 stderr 打一行 ASCII
前缀 WARN（同一进程只打一次）；显式 opt-in `ATLAS_REQUIRE_PLUGIN=1`（只认精确 "1"，
与 config._flag_env 同一判据）时无 ATLAS_PLUGIN 直接 ValueError 点名（CLI 已有
ValueError→rc2 口径）。开关坏值按关处理并点名原值 repr（_flag_env 单点，#79/#80）。
"""
from __future__ import annotations
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from .config import _flag_env
from .plugins.demo import DemoPlugin

_REGISTRY: dict[str, type] = {}

# en-1 开关键名单点（值语义委托 config._flag_env，只认精确 "1"、坏值按关处理并点名 repr）。
REQUIRE_PLUGIN_ENV_KEY = "ATLAS_REQUIRE_PLUGIN"

# 回落 WARN「同一进程只打一次」的进程级旗标（测试经 monkeypatch 重置，见 test_plugin_api）。
_FALLBACK_WARN_PRINTED = False

def demo_fixture_account() -> str:
    """demo 插件的**虚构账号**——从数据源读，不在代码里写死（业务字面量单一来源）。

    为什么不是模块级常量：`tests/test_engine_and_dsl.py::test_demo_session_defaults_are_single_source`
    规定 demo 的业务会话字面量（账号/OU/分区号）**只能**住在 `plugins/demo/knowledge/session.json`；
    任何 `.py` 里再写一遍都算漂移（改了数据源而代码没跟，闸门就会去拦一个已不存在的账号名）。
    所以这里是**函数**而不是常量——连本注释都不写那些字面量，否则一样会被那条测扫到。

    读失败（知识库不可用/改坏了）返回空串：闸门随之退化为「不拦」——
    **读不到就不拦**是可接受的方向（少拦一次不改变任何执行语义；误拦会挡住正常路径，#115）。
    """
    try:
        value = DemoPlugin().session_defaults().get("account")
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def plugin_is_demo_fallback() -> bool:
    """当前插件是否是**回落**到 demo（不是显式 `ATLAS_PLUGIN=demo`）。

    与 `_warn_fallback_once()` 同一判据（`ATLAS_PLUGIN` 未设/空白），两处消费同一份事实：
    WARN 用它决定要不要提示，登录闸门用它决定要不要拦。**显式 demo 不算回落**——那是
    人的决定（样例/dry-run 场景），拦它等于把正常路径翻成错误（#115）。
    """
    return not os.environ.get("ATLAS_PLUGIN", "").strip()


def _reset_fallback_warn() -> None:
    """测试钩子：重置「同一进程只打一次」的 WARN 旗标（不属生产路径）。"""
    global _FALLBACK_WARN_PRINTED
    _FALLBACK_WARN_PRINTED = False


def _warn_fallback_once() -> None:
    """en-1：回落 demo 时打一行 ASCII WARN，**同一进程只打一次**。

    resolve_plugin 在知识读侧身份解析（en-2 plugin_name / en-3 草稿 identity）中被
    反复调用，逐次打印会把 stderr 变成瀑布；「静默回落」要揭示的是进程级事实
    （这个进程在拿虚构 demo 知识跑），打一次即达成目的。

    **2026-11-16 真机追加（措辞升级为点名后果）**：原措辞只说了「fallback」，读者
    不一定知道后果。真机实测踩到：真实项目未设 `ATLAS_PLUGIN` → 回落 demo →
    `session_defaults()` 给出数据源里那个虚构账号 / 空密码 → 拿它去 SSO 登录 →
    被拒 → 停在登录表单 → 45s 后报「未见登录指示器」→ `atlas run` rc 1。
    **排障者看到的是一条登录超时，看不到「你在用虚构账号」。** 所以这里把后果写进
    同一行（仍是一条，不破坏「只打一次」的初衷）：点明会话坐标来自 demo，跑真环境
    前必须设 `ATLAS_PLUGIN`。
    """
    global _FALLBACK_WARN_PRINTED
    if _FALLBACK_WARN_PRINTED:
        return
    _FALLBACK_WARN_PRINTED = True
    # 账号值**在运行期从数据源读**再插进消息：既让读者看到具体后果（虚构账号名一眼可辨），
    # 又不把业务字面量写进 .py（单一来源纪律，见
    # tests/test_engine_and_dsl.py::test_demo_session_defaults_are_single_source）。
    account = demo_fixture_account() or "<demo 数据源的虚构账号>"
    print(f"WARN: plugin=demo (fallback; set ATLAS_PLUGIN=<domain>) —— "
          f"会话坐标/知识/任务全来自虚构 demo（账号 {account!r}、密码为空）："
          f"对真实环境跑会以登录失败收场，且报错不提「用的是 demo 账号」",
          file=sys.stderr)


# 通用知识包目录（业务中立、跨插件共享）的路径**单一来源**：平台插件把三级知识根按
# 「私有目录 > 平台包内知识 > 通用知识包」传给 KnowledgeBase 时，最后一层从这里取，
# 不得各自拼路径（改一漏一即漂移，同 _REGISTRY 单一入口纪律）。
_COMMON_KNOWLEDGE_DIR = Path(__file__).resolve().parent / "plugins" / "_common" / "knowledge"


def common_knowledge_root() -> Path:
    """返回通用知识包目录 `src/atlas/plugins/_common/knowledge`（只读）。

    用法（平台插件，多根逐文件回退，task #2）：
        KnowledgeBase(priv, fallback_root=[self.knowledge_root, common_knowledge_root()])
    写路径只认 root（#135），通用包恒为只读 fallback。
    """
    return _COMMON_KNOWLEDGE_DIR


def register_plugin(domain: str, cls: type) -> None:
    """插件包导入时登记自己（未来多插件扩展点；引擎自带 demo 已在下方默认注册）。"""
    _REGISTRY[domain] = cls


def _load_external_plugin(domain: str):
    """按 ATLAS_PLUGIN 取值动态加载外部插件模块，取模块级 PLUGIN。

    路径形态（含 / \\ 或 .py 结尾）→ 文件加载；模块名 → import atlas.plugins.<name>
    再退 import <name>。全部失败返回 None，由 resolve_plugin 统一点名。
    """
    path = Path(domain)
    if domain.endswith(".py") or "/" in domain or "\\" in domain:
        if not path.is_file():
            return None
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        spec.loader.exec_module(module)
    else:
        for candidate in (f"atlas.plugins.{domain}", domain):
            try:
                module = importlib.import_module(candidate)
                break
            except ModuleNotFoundError:
                continue
        else:
            return None
    return getattr(module, "PLUGIN", None) or getattr(module, "get_plugin", lambda: None)()


def resolve_plugin():
    """按 ATLAS_PLUGIN 解析业务插件实例；缺省/空 = demo（与旧 DemoPlugin() 完全等价）。

    ATLAS_PLUGIN 指到未注册 domain 时先尝试动态加载（Gap2 外部插件）；
    仍找不到抛 ValueError——CLI 入口接 _user_error（rc 2）。

    en-1（wave5）：回落 demo 不再沉默——
    - 未设 ATLAS_PLUGIN → stderr 一行 ASCII WARN（`WARN: plugin=demo (fallback; …)`），
      同一进程只打一次（resolve_plugin 会被 en-2/en-3 身份解析反复调用）。
    - `ATLAS_REQUIRE_PLUGIN=1`（显式 opt-in，值判据委托 `config._flag_env` 单点）→
      无 ATLAS_PLUGIN 直接 ValueError 点名（CLI 已有 ValueError→rc2 口径）。
    设了 ATLAS_PLUGIN 的路径零涟漪：不 WARN、不拒。
    """
    raw_domain = os.environ.get("ATLAS_PLUGIN", "")
    domain = raw_domain.strip() or "demo"
    if domain == "demo":
        # en-1：显式 demo 与「回落」是两个语义——只有「ATLAS_PLUGIN 未设/空」才提示
        # （显式 ATLAS_PLUGIN=demo 是人的决定，再 WARN 就是噪音）。
        if not raw_domain:
            if _flag_env(REQUIRE_PLUGIN_ENV_KEY):
                raise ValueError(
                    "ATLAS_REQUIRE_PLUGIN=1 且未设置 ATLAS_PLUGIN：拒绝静默回落 demo 插件"
                    "（虚构知识/契约可能被拿去跑真实系统；显式设置 ATLAS_PLUGIN=<domain>）")
            _warn_fallback_once()
    cls = _REGISTRY.get(domain)
    if cls is not None:
        return cls()
    plugin = _load_external_plugin(domain)
    if plugin is not None:
        return plugin
    raise ValueError(f"未知业务插件 {domain!r}（已注册：{', '.join(sorted(_REGISTRY))}；"
                     f"动态加载亦未找到）")


register_plugin("demo", DemoPlugin)

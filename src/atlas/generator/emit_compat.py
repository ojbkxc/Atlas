"""gap11 #18（先判据部分）：``--compat legacy-suite`` 产物的两道硬判据底座。

本文件当前只承载 lead 派单点名可先行的两条判据机制（产物形态渲染等 scout 的
gap11 设计稿 ``docs/gaps/gap11-legacy-fast-compat.md`` 落定后按定稿补）：

1. **atlas import 扫描**——产物**不得 import atlas**（旧仓机器没装 atlas，本任务第一红线）。
   权威判据是 AST：绝对 import 的模块根 == ``atlas`` 即命中（``import atlas`` /
   ``import atlas.x`` / ``from atlas... import`` 全覆盖；``import atlas_helper`` 不误报）。
   另配一条行首语句形态的正则扫描，与离线判据原文「``import atlas``/``from atlas`` 0 命中」
   对齐——但它只匹配 import 语句行，**不匹配 docstring/注释里的叙述句**：垫片源文件
   docstring 里就有「旧仓运行时绝不 import atlas 包」这类纪律陈述，vendoring 要求产物
   逐字节=源文件，扫描器若把叙述行判违规，等于逼产物偏离源文——两道闸互相打架就会
   漏，所以分工写死在这里，谁也不许顺手扩。
2. **vendoring**——生成期按真文件字节把 ``src/atlas/legacy_fast.py`` /
   ``src/atlas/fastplugin.py`` 原样写进产物，头部加 provenance（源相对路径 + sha256）。
   禁止在 emit 层手抄第二份垫片逻辑（#92/#15 同源）；源文件改了不重新生成 → 判据红。

红线：#9 只生成不执行（本模块只读文件、只返回字符串，绝不 import 垫片本体）。
判据位纪律：不写死全仓计数（F-24）。
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path


class CompatEmitError(ValueError):
    """--compat 产物生成/校验的结构问题（垫片文件缺失、vendor 内容与源不符）。"""


# 垫片源文件清单（lead 派单原文点名 + 2026-11-09 盘上核实在位，名字变更必先改契约）。
SHIM_SOURCE_FILES = ("src/atlas/legacy_fast.py", "src/atlas/fastplugin.py")

# 行首 import 语句形态：`import atlas...` / `from atlas...`（缩进的也算语句，
# 但必须是语句开头——docstring 内叙述行前有「- 」「**」等前缀，不匹配本式）。
_ATLAS_IMPORT_STMT = re.compile(r"^[ \t]*(?:from|import)[ \t]+atlas(?:[ \t.]|$|\.)")

_PROVENANCE_RE = re.compile(
    rb"^# vendored \(gap11 #18 provenance\): source=(?P<rel>\S+) sha256=(?P<sha>[0-9a-f]{64})\n"
    rb"# Vendored content is byte-for-byte equal to source bytes;.*\n")


def _as_scan_text(source, name: str) -> str:
    """扫描入口统一取文本：bytes **严格** decode——坏字节当场点名（UnicodeDecodeError
    包装后上抛），文本哨兵绝不在坏输入上静默 0 命中（#102/#106 假绿同族，support-impl
    22:3x 点名的 bytes 喂 str 正则崩溃的正解；不选 try/except 吞）。"""
    if isinstance(source, bytes):
        try:
            return source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CompatEmitError(
                f"{name}: 待扫描字节非合法 UTF-8——扫描器拒绝在坏输入上给出『0 命中』的"
                f"假干净结论（{exc}）") from exc
    return source


def atlas_import_hits(source, name: str = "<gen>") -> list[str]:
    """AST 权威扫描：模块根为 ``atlas`` 的绝对 import → 命中清单（人读定位串）。

    语法坏的源不静默：SyntaxError 原样抛（坏源本来就过不了产物 compile 判据，
    扫描器不得先于被扫描对象给出假干净结论——#89 同族）。接受 str/bytes（bytes 严格
    decode，见 _as_scan_text）。
    """
    tree = ast.parse(_as_scan_text(source, name), filename=name)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "atlas":
                    hits.append(f"{name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            # level>0 是相对 import——产物是顶层文件本就不该出现；相对 import 不含
            # 模块名，与 atlas 包无绑定，不算命中（不误伤 `from .x`，虽然垫片已禁）。
            if not node.level and node.module and node.module.split(".")[0] == "atlas":
                hits.append(f"{name}:{node.lineno} from {node.module} import …")
    return hits


def atlas_import_text_hits(source, name: str = "<gen>") -> list[str]:
    """语句形态正则扫描（判据原文的「import atlas/from atlas」字面口径）。

    只认行首 import 语句；docstring/注释里的「绝不 import atlas 包」叙述不算违规——
    见模块 docstring 的分工说明。行为与 AST 扫描互补：AST 是权威，本式是文本哨兵，
    产物两个都得 0 命中。输入 str/bytes 皆可（bytes 严格 decode，坏字节可见地炸，
    不静默 0 命中——lead 4 红裁定后选定的单一入口判据形态，理由在 _as_scan_text）。
    """
    text = _as_scan_text(source, name)
    return [f"{i + 1}: {line.strip()}"
            for i, line in enumerate(text.splitlines())
            if _ATLAS_IMPORT_STMT.match(line)]


def is_atlas_free(source, name: str = "<gen>") -> list[str]:
    """产物单文件双扫描的合并入口：返回命中清单（空=干净）。str/bytes 皆入。"""
    return atlas_import_hits(source, name) + atlas_import_text_hits(source, name)


def vendor_shim_source(repo_root: Path, rel_path: str) -> bytes:
    """按真文件**字节** vendoring：provenance 头（相对路径 + 源 bytes 的 sha256）＋源原文。

    字节级而非文本级（lead 交付前裁定 G11-4 补正）：``read_text`` 的 newline=None
    通用换行会把 CRLF 归一成 LF——文本级哈希在「源=CRLF、vendor 后=LF」上两边一起
    归一，锁步判据恒绿，恰好放过「换行被悄悄改写」这类真实漂移（git autocrlf 场景
    实测存在）；utf-8 BOM 在文本解码下也会失真。字节面哈希 + 字节面比对，CRLF/BOM
    全部如实进判据。

    源缺失 → ``CompatEmitError`` 点名（不产半成品——垫片没落地就生成 --compat 包
    等于产一个注定 import 失败的壳，违 #9）。
    """
    src = Path(repo_root) / rel_path
    if not src.is_file():
        raise CompatEmitError(
            f"垫片源文件缺失，无法 vendor：{rel_path}（gap11 #17 未落地/被移动——"
            f"先补源文件或改契约，不许在 emit 层手写第二份垫片逻辑）")
    raw = src.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    header = (
        f"# vendored (gap11 #18 provenance): source={rel_path} sha256={digest}\n"
        "# Vendored content is byte-for-byte equal to source bytes; 源文件变更后须重新生成。\n"
    ).encode("utf-8")
    return header + raw


def vendor_body(vendored: bytes, name: str = "<vendored>") -> bytes:
    """剥 provenance 头取**字节**正文；头缺失/形态坏 → CompatEmitError（不许静默当全文返回）。"""
    if isinstance(vendored, str):  # 文本面调用=旧口径残留，判据拒收（防两套混用互相掩盖）
        raise CompatEmitError(
            f"{name}: vendor 判据是字节级接口，收到 str——文本读法会归一换行、掩盖漂移")
    m = _PROVENANCE_RE.match(vendored)
    if not m:
        raise CompatEmitError(
            f"{name}: 缺少 vendored provenance 头（首两行格式见 _PROVENANCE_RE）——"
            f"手抄内容混进了产物通道")
    return vendored[m.end():]


def vendor_provenance(vendored: bytes, name: str = "<vendored>") -> tuple[str, str]:
    """从头部取 (源相对路径, 声称 sha256)。str 输入同样拒收（字节级接口）。"""
    if isinstance(vendored, str):
        raise CompatEmitError(f"{name}: vendor provenance 是字节级接口，收到 str")
    m = _PROVENANCE_RE.match(vendored)
    if not m:
        raise CompatEmitError(f"{name}: 缺少 vendored provenance 头")
    return m.group("rel").decode("utf-8"), m.group("sha").decode("utf-8")


def vendor_is_current(vendored: bytes, source_bytes: bytes) -> bool:
    """「vendor 内容 == 源文件内容（除 provenance 头）」判据，**字节面** sha 与字节面正文双核。

    源文件改了（含换行风格/BOM 这类文本读法看不见的变更）而产物没重新生成 → False。
    """
    if isinstance(source_bytes, str):
        raise CompatEmitError("vendor 等值判据必须走 bytes（文本读法两边同归一=假绿面）")
    _rel, claimed = vendor_provenance(vendored)
    actual = hashlib.sha256(source_bytes).hexdigest()
    return claimed == actual and vendor_body(vendored) == source_bytes


# ===========================================================================
# 产物渲染（gap11 C.1-C.5，#18）
# ===========================================================================

# fixture 对拍表（设计 A0/C.5 定稿名位；scope/autouse 面 = 旧仓 conftest 实测，
# emit-impl 事实包修正②：browser=session 其余 module；watchdog autouse）。
# 「对拍测」以此表为契约侧尺；另有一条**从旧仓 conftest AST 提取实名**的交叉面测
# （旧仓不可见时如实 skip——任务判据「从旧仓 AST 提，不手抄」的兑现形态）。
CONTRACT_FIXTURES = (
    ("plan_ctx", "module", False),
    ("_case_watchdog", None, True),
    ("sole_page", "module", False),
    ("page", "module", False),
    ("context", "module", False),
    ("browser", "session", False),
    ("get_page", "module", False),
)

# 兼容产物允许的第三方 import 根（D.1.5 三值外零命中；allure 为旧仓既有可选件，
# lead 任务原文「import pytest + 可选 import allure」）。atlas **不在表内=第一红线**。
# 判据消费方 = tests/test_emit_compat.py::test_compat_artifact_import_roots_within_third_party
# （扫产物全部 .py 的 AST import 根，越界即红）——常量与判据同住一处，不留手抄副本。
COMPAT_ALLOWED_ROOTS = ("pytest", "playwright", "allure")

# 旧仓业务选择器哨兵（lead 交付注意②：产物文本 0 命中；正向对照=旧仓 conftest 实文必命中）。
LEGACY_BUSINESS_SELECTOR_MARKS = ("app-topbar-obj", "el-dropdown-selfdefine")

_LEGACY_WEB_ENV_KEY = "ATLAS_LEGACY_WEB"
_LEGACY_PROJECT_ENV_KEY = "ATLAS_LEGACY_PROJECT"
_LEGACY_PAGES_MODULE = "lib.pages"  # 模块位=旧仓结构位；值形制与 emit_pytest atlas_pages 模板常量
# （LEGACY_PAGES_MODULE）一致——两处消费同一旧仓结构事实（旧仓布局本身），互不引用对方的字面量。


def _legacy_web_dir() -> Path:
    """旧仓 web 子目录定位（生成期）——解析口径**镜像 `config.check_legacy_paths`**。

    Atlas 标准 .env 形态是 `ATLAS_LEGACY_PROJECT=<绝对根>` +
    `ATLAS_LEGACY_WEB=<子目录名>`（`exec_dir = legacy_project / legacy_web`，见
    `LegacyAdapter`/`AtlasConfig.legacy_web`）。相对形态的存在性判据**直接委托**
    `config.check_legacy_paths`（单一真相，绝不另写第三条路径规则 #92/#97）；WEB 本身
    给绝对路径时直接用（pathlib 拼接同语义，向后兼容旧用法）。两键缺/相对 WEB 无
    PROJECT → `CompatEmitError` **点名是哪个键**（#103 归因口径：不让路径问题漂成框架
    缺陷，也不产运行期才炸的半成品）。产物侧同名解析在 `_compat_runtime_lines` 内联
    （产物绝不 import atlas），两侧等值由 tests/test_emit_compat.py 锁步测守。
    """
    web = os.environ.get(_LEGACY_WEB_ENV_KEY, "").strip()
    proj = os.environ.get(_LEGACY_PROJECT_ENV_KEY, "").strip()
    if not web:
        raise CompatEmitError(
            f"缺配置：{_LEGACY_WEB_ENV_KEY} 未设置（旧仓 web 子目录名——相对 "
            f"{_LEGACY_PROJECT_ENV_KEY} 的旧项目根，或本身给绝对路径）")
    path = Path(web)
    if path.is_absolute():
        if not path.is_dir():
            raise CompatEmitError(
                f"缺配置：{_LEGACY_WEB_ENV_KEY} 指向的目录不存在：{web}")
        return path
    if not proj:
        raise CompatEmitError(
            f"缺配置：{_LEGACY_WEB_ENV_KEY}={web!r} 是相对子目录名，需要 "
            f"{_LEGACY_PROJECT_ENV_KEY} 指旧项目根（Atlas 标准 .env 形态）")
    from ..config import check_legacy_paths
    try:
        check_legacy_paths(Path(proj), web)  # 判据与消息的唯一出处（不复写）
    except FileNotFoundError as exc:
        raise CompatEmitError(f"缺配置：{exc}") from exc
    return Path(proj) / web


@dataclass
class CompatBridgeStep:
    """legacy_case 桥接腿：整条委托旧 run_case_py（C.2——ctx 播种唯一真相留旧代码）。"""
    order: int
    case_id: str
    module_rel: str
    intent: str
    is_write: bool
    skip_postcondition: bool
    requires: tuple = ()


@dataclass
class CompatDelegateStep:
    """原生腿（委托形态）：legacy_pages 声明 → 运行期经 conftest 载旧 lib.pages 函数。"""
    order: int
    fn: str
    intent: str
    slug: str
    is_write: bool
    requires: tuple = ()


def _compat_ok_key(order: int) -> str:
    return f"_tc{order}_ok"


def compat_specs_from_flow(flow: dict) -> list:
    """flow steps → 兼容腿清单 + 生成期校验（缺失/坏形/不支持面点名，先于任何写盘）。

    分工（两道闸，判据零复制）：声明位**形态**第一道在 compile 静态段；本函数只做
    兼容面**支持性**分类——page_object/api_call 等兼容形态没有的腿在此点名拒绝，
    不产运行期必炸的半成品（红线 #9）。
    """
    from .emit_pytest import _ident_slug  # 同层自用（惰性方向：emit_pytest 只在函数体内 import 本模块，不成环）
    steps = flow.get("steps") or []
    if not steps:
        raise CompatEmitError("兼容包需要至少一个 step（空 flow 出包=空产物假成功）")
    out: list = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise CompatEmitError(f"flow 步骤 {i} 非映射（{type(step).__name__}）")
        task = str(step.get("task") or "")
        raw_requires = step.get("requires")
        if raw_requires is None:
            requires: tuple = ()
        elif isinstance(raw_requires, (list, tuple)) and all(
                isinstance(k, str) and k.strip() for k in raw_requires):
            requires = tuple(raw_requires)
        else:
            raise CompatEmitError(f"flow 步骤 {i} 的 requires 形态不合法：{raw_requires!r}")
        if task == "api_call":
            raise CompatEmitError(
                f"flow 步骤 {i} 是 api_call——兼容形态（--compat legacy-suite）没有 API 腿"
                f"（产物树无 _atlas_support 通道，设计 C.1）；纯 API flow 请用 --compat none"
                f" 的 gap9b 形态出包，别在这里产半兼容半缺失的壳。")
        if step.get("page_object"):
            raise CompatEmitError(
                f"flow 步骤 {i} 声明 page_object={step['page_object']!r}——兼容产物树没有 "
                f"_atlas_fast 底座与 atlas_pages 派生腿（设计 C.4 只 vendor 两垫片），"
                f"页对象腿请走 --compat none 形态；此处拒绝而非造第二套委托。")
        if task == "legacy_case":
            module_rel = step.get("module")
            case_id = step.get("case_id")
            if not isinstance(module_rel, str) or not module_rel.strip():
                raise CompatEmitError(
                    f"flow 步骤 {i}（legacy_case）缺 module 声明位（旧 yaml 相对 testcases/ "
                    f"的路径，非空字符串）——引擎不猜用例文件。")
            # 绝对路径判据双平台同答（X1 纪律：os.path.isabs 在 Windows 上放行 "/x" 形态，
            # 单用它=同一 flow 两台机一个拒一个过——PurePosix 面补住 Unix 式绝对路径）
            from pathlib import PurePosixPath
            if (".." in Path(module_rel).parts or Path(module_rel).is_absolute()
                    or PurePosixPath(module_rel).is_absolute()
                    or module_rel[:1] in ("/", "\\")):
                raise CompatEmitError(
                    f"flow 步骤 {i}（legacy_case）的 module={module_rel!r} 必须是相对旧仓 "
                    f"testcases/ 的路径（绝对/上跳不接——目录定位只经 {_LEGACY_PROJECT_ENV_KEY}"
                    f"/{_LEGACY_WEB_ENV_KEY} 两键）。")
            if not isinstance(case_id, str) or not case_id.strip():
                raise CompatEmitError(
                    f"flow 步骤 {i}（legacy_case）缺 case_id 声明位（与旧 yaml cases[].id 对应）。")
            sp = step.get("skip_postcondition", False)
            if not isinstance(sp, bool):
                raise CompatEmitError(
                    f"flow 步骤 {i} 的 skip_postcondition 必须是布尔（当前 "
                    f"{type(sp).__name__}: {sp!r}）。")
            out.append(CompatBridgeStep(
                order=i, case_id=case_id.strip(), module_rel=module_rel.strip(),
                intent=str(step.get("case_intent") or step.get("intent")
                           or f"{case_id.strip()}"),
                is_write=(step.get("write") is not False),  # legacy_case 默认 write=True，同实签
                skip_postcondition=sp, requires=requires))
            continue
        lp = step.get("legacy_pages")
        if lp is not None and not (isinstance(lp, str) and lp.strip()):
            raise CompatEmitError(
                f"flow 步骤 {i} 的 legacy_pages 形态不合法（需非空字符串函数名，当前 "
                f"{type(lp).__name__}: {lp!r}）。")
        if lp:
            out.append(CompatDelegateStep(
                order=i, fn=lp.strip(),
                intent=str(step.get("intent") or step.get("id") or task or f"step_{i}"),
                slug=_ident_slug(step.get("id") or task or step.get("intent") or f"step_{i}",
                                 fallback=f"step_{i}"),
                is_write=(step.get("read_only") is False), requires=requires))
            continue
        raise CompatEmitError(
            f"flow 步骤 {i}（task {task!r}）缺声明位：兼容形态需要 legacy_case"
            f"（module+case_id 桥接腿）或 legacy_pages（旧 lib.pages 函数名委托腿）——"
            f"引擎不猜旧用例、不猜函数（gap9b B-2 同纪律，#11）。")
    return out


def compat_persist_keys(steps: list) -> set:
    """plan_ctx 白名单 = 本包真实写入/引用的键派生（旧仓手抄 _tc1..18 表的生成期等价）。"""
    keys = {"_logged_in_account"}
    for s in steps:
        if isinstance(s, CompatBridgeStep):
            keys.add(_compat_ok_key(s.order))
        for k in s.requires:
            keys.add(k)
    return keys


def _compat_runtime_lines() -> list[str]:
    """产物运行期 helper 的**唯一渲染源**（conftest 与用例模块各嵌一份，文本同源）。

    xcheck C5c 口径：产物 conftest 不得新增旧仓没有的 fixture 名——旧仓定位/委托/缺前置
    这些把手因此是**普通模块函数**，不是 fixture；两文件各嵌一份不是判据复制（#92 管
    引擎侧判据位，生成物文本由本函数单源渲染，改一处必两处同红——锁步测守）。
    """
    return [
        "class EnvBlocked(RuntimeError):",
        '    """配置/资产缺失终态：点名缺什么——「环境没配好」不是代码缺陷，显式、不静默。"""',
        "",
        "",
        "_LEGACY_WEB_ENV_KEY = \"" + _LEGACY_WEB_ENV_KEY + "\"",
        "_LEGACY_PROJECT_ENV_KEY = \"" + _LEGACY_PROJECT_ENV_KEY + "\"",
        "_LEGACY_PAGES_MODULE = \"" + _LEGACY_PAGES_MODULE + "\"",
        "",
        "",
        "def _legacy_sys_path() -> str:",
        '    """旧仓 web 子目录定位：只信 env，不猜路径；缺/坏 → EnvBlocked 点名配置键。',
        "",
        "    解析口径**镜像 Atlas 侧 config.check_legacy_paths**（exec_dir =",
        "    legacy_project / legacy_web 子目录名）：ATLAS_LEGACY_WEB 给相对子目录名时与",
        "    ATLAS_LEGACY_PROJECT 拼接，本身给绝对路径时直接用（pathlib 同语义，向后兼容）。",
        "    产物绝不 import atlas（C.3 第一红线），故同规则在此内联；两侧等值由",
        "    tests/test_emit_compat.py 的锁步测守——Atlas 侧路径口径变了，产物这份必红。",
        '    """',
        '    web = os.environ.get(_LEGACY_WEB_ENV_KEY, "").strip()',
        '    proj = os.environ.get(_LEGACY_PROJECT_ENV_KEY, "").strip()',
        "    if not web:",
        "        raise EnvBlocked(",
        '            f"缺配置：{_LEGACY_WEB_ENV_KEY} 未设置（旧仓 web 子目录名——相对 "',
        '            f"{_LEGACY_PROJECT_ENV_KEY} 的旧项目根，或本身给绝对路径；兼容包只经"',
        '            f"这两键定位旧 lib，不猜路径）")',
        "    root = Path(web)",
        "    if not root.is_absolute():",
        "        if not proj:",
        "            raise EnvBlocked(",
        '                f"缺配置：{_LEGACY_WEB_ENV_KEY}={web!r} 是相对子目录名，需要 "',
        '                f"{_LEGACY_PROJECT_ENV_KEY} 指旧项目根（Atlas 标准 .env 形态）")',
        "        root = Path(proj) / root",
        "    if not root.is_dir():",
        "        raise EnvBlocked(",
        '            f"缺配置：旧仓 web 子目录不存在：{root}（检查 "',
        '            f"{_LEGACY_PROJECT_ENV_KEY} / {_LEGACY_WEB_ENV_KEY}）")',
        "    if str(root) not in sys.path:",
        "        sys.path.insert(0, str(root))",
        "    return str(root)",
        "",
        "",
        "def _legacy_import(module_name: str):",
        '    """惰性载旧 lib 模块：模块级零 import（产物收集期不依赖旧仓在位，D.1.5）。"""',
        "    _legacy_sys_path()",
        "    try:",
        "        return importlib.import_module(module_name)",
        "    except ImportError as exc:",
        "        raise EnvBlocked(",
        '            f"缺配置：{_LEGACY_WEB_ENV_KEY} 下无法 import {module_name}（{exc}）") from exc',
        "",
        "",
        "def _legacy_testcases_root() -> Path:",
        '    """旧仓 testcases/ 根（yaml 相对定位的唯一基准）。"""',
        "    return Path(_legacy_sys_path()) / \"testcases\"",
        "",
        "",
        "def _require_ctx(ctx, key, upstream):",
        '    """缺前置=显式终止（EnvBlocked），不沿用旧仓 pytest.skip 静默形态（F5/gap9b 纪律）。"""',
        "    if key not in ctx:",
        "        raise EnvBlocked(",
        '            f"缺前置：plan_ctx 无 {key!r}（{upstream}）——先跑前置用例/勿删 "',
        '            f"reports/plan_ctx.json（跨批上下文同 Atlas 分批纪律）")',
        "",
        "",
        "def _call_pages_fn(fn_name, page, ctx):",
        '    """原生腿委托位：旧 lib.pages 函数名经人审声明（legacy_pages），缺失点名不猜。"""',
        "    mod = _legacy_import(_LEGACY_PAGES_MODULE)",
        "    fn = getattr(mod, str(fn_name), None)",
        "    if fn is None or not callable(fn):",
        "        raise EnvBlocked(",
        '            f"缺配置：旧套件 {_LEGACY_PAGES_MODULE} 无可调用函数 {fn_name!r}"',
        '            f"（函数名须人审核对，不猜）")',
        "    return fn(page, ctx)",
        "",
    ]


def _compat_fast_defaults() -> tuple:
    """快旋钮默认位**取自 CLI 面单点** `legacy.LegacyAdapter.FAST_DEFAULTS`（#31/#30 同族）。

    lead 要求「atlas legacy --fast」与「--compat 产物」两侧的一字不差——那就不留第二份清单
    （#92）：生成期从它在位读，读不到/形态坏一律 `CompatEmitError` 点名两处，绝不静默少开
    一个旋钮（少开=用户拿到「以为是快包其实没开」的产物，与 #30 的原缺陷同形态）。
    #36 之后 CLI 侧清单只剩截图抑制（等待垫片改为显式 opt-in），产物侧自动同形——
    本函数不判断「该开哪些」，只负责同源；判断位唯一在 `LegacyAdapter.FAST_DEFAULTS`。
    """
    from ..legacy import LegacyAdapter
    pairs = getattr(LegacyAdapter, "FAST_DEFAULTS", None)
    if not isinstance(pairs, (tuple, list)) or not pairs:
        raise CompatEmitError(
            "快旋钮默认位取不到：legacy.LegacyAdapter.FAST_DEFAULTS 缺失或为空——"
            "产物侧默认置位与 CLI 侧必须同源（#92/lead #31），请对齐后重生成，不猜默认值")
    out = tuple((str(k), str(v)) for k, v in pairs)
    if any(not k or not v for k, v in out):
        raise CompatEmitError(
            f"快旋钮默认位形态不对（需 (键, 值) 且都非空）：{pairs!r}")
    return out


def render_compat_conftest(steps: list, flow_id: str) -> str:
    """C.5 同名同 scope fixture 面 + C.4 垫片装配（vendored fastplugin 经 conftest 委托）。

    fixture 名集**恰等于** CONTRACT_FIXTURES 七条（xcheck C5c 口径：不新增旧仓没有的
    fixture 名——定位/委托/缺前置把手是 _compat_runtime_lines 普通函数，与用例模块
    共享同一渲染源）；scope/autouse 对拍旧仓实文（browser=session，其余 module，
    watchdog autouse）。登录判定=页面指示器**实查**为权威（磁盘账号记录只作参考，F1
    口径）；指示器选择器只来自 env（业务字面量零进模板，红线 #11）；登录/登出动作委托
    旧 lib.login（缺配置 EnvBlocked 点名——产物不实现登录表单，不留第二份业务知识）。
    """
    keys = sorted(compat_persist_keys(steps))
    key_literal = ", ".join(repr(k) for k in keys)
    fast_defaults = _compat_fast_defaults()   # 同源 CLI 面 FAST_DEFAULTS（#92，不写第二份）
    # 尾逗号硬要求：单元素清单（#36 后就是这种）漏了它，`(('k','v'))` 会退化成
    # `('k','v')`——产物 conftest 的 `for _key, _default in` 当场 ValueError（离线测抓到）。
    fast_default_literal = "".join(f"({k!r}, {v!r}), " for k, v in fast_defaults)
    lines = [
        '"""生成候选（generator/emit_compat --compat legacy-suite）：兼容包自足 conftest。',
        "",
        "fixture 名/scope 面对拍 gap11 设计 A0/C.5（browser=session，其余 module，",
        "_case_watchdog autouse），且**只含**旧仓同名七件套——定位/委托/缺前置把手是",
        "模块级普通函数（与用例模块共享 _compat_runtime_lines 单一渲染源，xcheck C5c）。",
        "垫片与截图抑制器在 pytest_configure 装配：委托同目录 vendored fastplugin",
        "（类级补丁命中旧页面对象，不改旧仓任何文件，红线 #4）。",
        "第一红线（C.3）：本文件零 import atlas；旧 lib 只经 " + _LEGACY_WEB_ENV_KEY + " 定位。",
        "登录态=指示器实查权威、磁盘值仅记录；指示器选择器只来自 env（#11）。",
        '"""',
        "from __future__ import annotations",
        "",
        "import importlib",
        "import json",
        "import os",
        "import sys",
        "import threading",
        "from pathlib import Path",
        "from typing import Any, Dict",
        "",
        "import pytest",
        "from playwright.sync_api import Page, sync_playwright",
        "",
        "# 同目录 vendored 垫片插件（fastplugin.py / legacy_fast.py，provenance 头=锁步判据 C.4）",
        "import fastplugin",
        "",
        "",
    ]
    lines += _compat_runtime_lines()
    lines += [
        "_ART_ROOT = Path(__file__).parent",
        '_PLAN_CTX_FILE = _ART_ROOT / "reports" / "plan_ctx.json"',
        '_INDICATOR_ENV_KEY = "ATLAS_FAST_LOGIN_INDICATOR"',
        "",
        "# 快旋钮默认位（生成期取自 CLI 面唯一清单 legacy.LegacyAdapter.FAST_DEFAULTS，",
        "# 与 `atlas legacy --fast` 同源——两侧要是一字不差，就不许在这里手抄第二份 #92）",
        f"_FAST_DEFAULTS = ({fast_default_literal})",
        "",
        "# 白名单键=本包步骤派生（旧仓手抄 _tcN 表的生成期等价，同语义、不漂移）",
        f"_CTX_PERSIST_KEYS = {{{key_literal}}}",
        "",
        "",
        "def _load_plan_ctx():",
        "    if _PLAN_CTX_FILE.exists():",
        "        try:",
        '            return json.loads(_PLAN_CTX_FILE.read_text(encoding="utf-8"))',
        "        except Exception:",
        "            return {}",
        "    return {}",
        "",
        "",
        "def _save_plan_ctx(ctx):",
        "    _PLAN_CTX_FILE.parent.mkdir(parents=True, exist_ok=True)",
        "    to_save = {k: v for k, v in ctx.items() if k in _CTX_PERSIST_KEYS}",
        "    _PLAN_CTX_FILE.write_text(",
        '        json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")',
        "",
        "",
        '@pytest.fixture(scope="module")',
        "def plan_ctx() -> Dict[str, Any]:",
        "    ctx = _load_plan_ctx()",
        "    yield ctx",
        "    _save_plan_ctx(ctx)",
        "",
        "",
        '@pytest.fixture(autouse=True)',
        "def _case_watchdog(request):",
        '    """单条用例看门狗（旧仓同源机制）：CASE_TIMEOUT 默认 600s，超时 interrupt 主线程。',
        "",
        "    旧仓 2026-10-31 实测：Playwright 调用偶发挂起无 traceback，后台线程兜底。",
        '    """',
        '    timeout = int(os.environ.get("CASE_TIMEOUT", "600"))',
        '    finished = {"done": False}',
        "",
        "    def _watch():",
        "        import time as _t",
        "        waited = 0",
        "        while waited < timeout:",
        "            _t.sleep(1)",
        "            waited += 1",
        '            if finished["done"]:',
        "                return",
        "        import _thread",
        "        _thread.interrupt_main()",
        "",
        "    th = threading.Thread(target=_watch, daemon=True)",
        "    th.start()",
        "    try:",
        "        yield",
        "    finally:",
        '        finished["done"] = True',
        "",
        "",
        "def _resolve_headless(request) -> bool:",
        '    """旧仓 flag 语义：默认无头；--headed 切有头；--headless 强制无头（同签名 C.5）。"""',
        '    if request.config.getoption("--headless"):',
        "        return True",
        '    if request.config.getoption("--headed"):',
        "        return False",
        "    return True",
        "",
        "",
        '@pytest.fixture(scope="module")',
        "def sole_page(request) -> Page:",
        '    """单一浏览器（module scope；旧仓同参数组：channel=chrome、六项禁用参数、',
        "",
        "    无头 1920x1080 viewport / 有头 --start-maximized；CDP_DEBUG=1 强制有头+调试端口）。",
        '    """',
        "    headless = _resolve_headless(request)",
        '    cdp_debug = os.environ.get("CDP_DEBUG", "").strip() == "1"',
        "    if cdp_debug:",
        "        headless = False",
        '    executable = os.environ.get("ATLAS_BROWSER_EXECUTABLE", "").strip() or None',
        "    with sync_playwright() as pw:",
        "        if headless:",
        "            browser = pw.chromium.launch(",
        '                channel="chrome", executable_path=executable,',
        "                headless=True, slow_mo=0,",
        '                args=["--disable-gpu", "--disable-dev-shm-usage",',
        '                      "--disable-crash-reporter", "--disable-breakpad",',
        '                      "--no-first-run", "--disable-extensions"])',
        '            ctx = browser.new_context(viewport={"width": 1920, "height": 1080})',
        "        elif cdp_debug:",
        "            browser = pw.chromium.launch(",
        '                channel="chrome", executable_path=executable,',
        "                headless=False, slow_mo=300,",
        '                args=["--start-maximized", "--remote-debugging-port=9222",',
        '                      "--disable-gpu", "--disable-dev-shm-usage"])',
        "            ctx = browser.new_context(no_viewport=True)",
        "        else:",
        "            browser = pw.chromium.launch(",
        '                channel="chrome", executable_path=executable,',
        "                headless=False, slow_mo=300,",
        '                args=["--start-maximized", "--disable-gpu", "--disable-dev-shm-usage",',
        '                      "--disable-crash-reporter", "--disable-breakpad",',
        '                      "--no-first-run", "--disable-extensions"])',
        "            ctx = browser.new_context(no_viewport=True)",
        "        page = ctx.new_page()",
        "        page._headless = headless  # 旧仓同位赋值（R-3：保留赋值、不做消费假设）",
        "        yield page",
        "        ctx.close()",
        "        browser.close()",
        "",
        "",
        '@pytest.fixture(scope="module")',
        "def page(sole_page): return sole_page",
        "",
        "",
        '@pytest.fixture(scope="module")',
        "def context(sole_page): return sole_page.context",
        "",
        "",
        '@pytest.fixture(scope="session")',
        "def browser(sole_page): return sole_page.context.browser",
        "",
        "",
        '@pytest.fixture(scope="module")',
        "def get_page(sole_page, plan_ctx):",
        '    """单浏览器工厂（旧仓形态：指示器实查权威、异账号先登出；磁盘记录只作参考）。',
        "",
        "    与旧仓的两处兼容面差异（README 同步声明）：",
        "    1) 指示器选择器来自 env（旧仓把业务选择器写死在 conftest——产物模板零业务",
        "       字面量，红线 #11；缺 → EnvBlocked 点名，不猜）；",
        "    2) 登录/登出委托旧 lib.login（表单与账号映射唯一真相留旧代码，C.2 同纪律）；",
        "       原生腿无 yaml case 时凭据经 env（ATLAS_FAST_ACCOUNT/PASSWORD，回落 DEMO_*）。",
        '    """',
        "    def _factory(case, ctx, account=None):",
        "        case = case or {}",
        '        account = (account or case.get("session", {}).get("account", "")',
        '                   or os.environ.get("ATLAS_FAST_ACCOUNT", "")',
        '                   or os.environ.get("DEMO_USERNAME", ""))',
        '        base_url = case.get("_base_url", "") or os.environ.get("ATLAS_FAST_BASE_URL", "")',
        '        password = (case.get("_accounts", {}).get(account, {}).get("password", "")',
        '                    or os.environ.get("ATLAS_FAST_PASSWORD", "")',
        '                    or os.environ.get("DEMO_PASSWORD", ""))',
        '        logged_in = ctx.get("_logged_in_account")',
        '        indicator = os.environ.get(_INDICATOR_ENV_KEY, "").strip()',
        "        if not indicator:",
        "            raise EnvBlocked(",
        '                f"缺配置：{_INDICATOR_ENV_KEY} 未设置（登录态指示器选择器是业务资产，"',
        '                f"必须来自显式配置——兼容产物模板不携带旧仓选择器）")',
        "        actually_in = sole_page.locator(indicator).count() > 0",
        "        if actually_in and logged_in == account:",
        "            return sole_page",
        '        if actually_in and not account:',
        "            raise EnvBlocked(",
        '                "缺配置：页面已登录但目标账号无法确定（case.session.account 与 "',
        '                "ATLAS_FAST_ACCOUNT/DEMO_USERNAME 皆空——不猜账号，#11）")',
        "        login = _legacy_import(\"lib.login\")",
        "        if actually_in:",
        "            login.do_logout(sole_page)",
        "        login.do_login(sole_page, base_url, account, password)",
        '        ctx["_logged_in_account"] = account',
        "        return sole_page",
        "    yield _factory",
        "",
        "",
        "def pytest_addoption(parser):",
        '    parser.addoption("--headless", action="store_true", default=False,',
        '                       help="强制无头模式")',
        '    parser.addoption("--headed", action="store_true", default=False,',
        '                       help="强制有头模式（浏览器可见）")',
        "",
        "",
        "def pytest_configure(config):",
        "    # gap11 #31：兼容包**按构造就是快包**（--compat legacy-suite 强制配 --fast），",
        "    # 故这里给**默认清单内**的旋钮补默认值。规则与 CLI 面 `atlas legacy --fast`",
        "    # （legacy.LegacyAdapter.FAST_DEFAULTS）**一字不差**：只补 unset/空值，**显式设置",
        "    # 恒优先**（含 off/0/false 退回旧行为）。不用 os.environ.setdefault——setdefault",
        "    # 不覆盖空串，`ATLAS_FAST_NO_STEP_SHOTS=`（显式空值）这一格会与 CLI 面分叉（lead 2026-11-10",
        "    # 要求两侧同语义；等值性由 tests/test_emit_compat.py 的跨面锁步测守）。",
        "    # 层次要说清：垫片/插件本体仍是「未置位=默认关」（裸跑旧仓语义不变，设计 A 硬判据），",
        "    # 本产物作为显式生成的快包在这里替用户打开**清单内**的开关。",
        "    # #36（2026-11-10 真机实证）：清单**只剩截图抑制**——等待垫片不默认开。",
        "    # `mask`/`mask+xhr` 在真机上间歇把失败点前移（同码同配置有时过有时挂），",
        "    # `mutation` 安全但零节省；按 G11-1 硬规则不能把「会间歇改变行为」的档位默认发出去。",
        "    for _key, _default in _FAST_DEFAULTS:",
        "        if not os.environ.get(_key):",
        "            os.environ[_key] = _default",
        "    # 旧仓引用名实（setup_logging：runner 定义/conftest 别名，C.5 不新造）：旧仓在场",
        "    # 尽力而为装上，缺席/失败 WARN 一行 ASCII——日志装载是旧 runner 职责，不顶掉整个 run。",
        '    if os.environ.get(_LEGACY_WEB_ENV_KEY, "").strip():',
        "        try:",
        '            runner = _legacy_import("lib.runner")',
        '            runner.setup_logging(log_dir=_ART_ROOT / "reports" / "logs")',
        "        except Exception as exc:",
        '            sys.stderr.write(f"WARN: legacy setup_logging 未装载: {type(exc).__name__}: {exc}\\n")',
        "    try:",
        "        fastplugin.pytest_configure(config)  # 垫片/抑制器装配（清单内已补默认；清单外的等待腿要人显式设）",
        "    except Exception as exc:",
        '        sys.stderr.write(f"WARN: fast 垫片未装配: {type(exc).__name__}: {exc}\\n")',
        "",
        "",
        "def pytest_unconfigure(config):",
        "    try:",
        "        fastplugin.pytest_unconfigure(config)",
        "    except Exception as exc:",
        '        sys.stderr.write(f"WARN: fast 垫片卸载异常: {type(exc).__name__}: {exc}\\n")',
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def compat_symbol_problems(steps: list) -> list[str]:
    """生成期 legacy_pages 函数名核验（可核则核）：旧仓 env 可见（ATLAS_LEGACY_WEB 或
    ATLAS_LEGACY_PROJECT 任一在位）时按 PROJECT/WEB 定位旧 lib.pages，取其顶层 AST 函数名
    对表，查无/不可读/配错点名拒绝——不产运行期才炸的半成品；两键都没配时**不装验证过**
    （返回空，运行期由 _call_pages_fn 的 EnvBlocked 兜——两面分开，不把「没查过」冒充
    「查过」，#64 诚实面）。
    """
    delegates = [s for s in steps if isinstance(s, CompatDelegateStep)]
    if not delegates:
        return []
    web = os.environ.get(_LEGACY_WEB_ENV_KEY, "").strip()
    proj = os.environ.get(_LEGACY_PROJECT_ENV_KEY, "").strip()
    if not web and not proj:
        return []
    try:
        web_dir = _legacy_web_dir()
    except CompatEmitError as exc:
        # 配了旧仓 env 却配错（相对 WEB 无 PROJECT / 目录不存在）=点名拒绝，不静默放行
        return [f"步骤 {s.order}：legacy_pages={s.fn!r} 无法核验——{exc}"
                for s in delegates]
    pages = web_dir / "lib" / "pages.py"
    problems: list[str] = []
    if not pages.is_file():
        for s in delegates:
            problems.append(
                f"步骤 {s.order}：legacy_pages={s.fn!r} 无法核验——旧仓 web 目录（{web_dir}）下"
                f"读不到 lib/pages.py；不可核即不产（拒半成品），请配好旧仓位后重生成")
        return problems
    try:
        tree = ast.parse(pages.read_bytes().decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        for s in delegates:
            problems.append(
                f"步骤 {s.order}：legacy_pages={s.fn!r} 无法核验——旧 lib/pages.py 解析失败（{exc}）")
        return problems
    names = {n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for s in delegates:
        if s.fn not in names:
            problems.append(
                f"步骤 {s.order}：legacy_pages 函数 {s.fn!r} 不在旧 lib.pages 顶层函数清单——"
                f"引擎不猜函数名（请人审核对，或先经 review 批准后重生成）")
    return problems


def render_compat_test_module(suite_slug: str, steps: list, flow_id: str) -> str:
    """C.1/C.2 用例模块：class Test<Suite>Plan + @pytest.mark.order(n)（次序=flow 步序）。

    模块体只依赖 conftest 的七个同名 fixture（get_page/plan_ctx/…）＋自身内嵌的
    _compat_runtime_lines 普通函数（单一渲染源）——**不新增 fixture 名**（xcheck C5c），
    旧 lib 的 import 全在普通函数里延迟发生（收集期不依赖旧仓在位，D.1.5）。
    """
    from .emit_pytest import _ident_slug, _pascal
    cls = "Test" + _pascal(suite_slug) + "Plan"
    lines = [
        '"""生成候选（generator/emit_compat --compat legacy-suite）：旧仓兼容快包用例。',
        "",
        f"来源 flow：{flow_id or '(未命名)'}。**只生成不执行**——执行是人在配好环境后的决定。",
        "桥接腿整条委托旧 run_case_py：不重写 step_N、不弱化断言、不复制 ctx 播种（红线",
        "#4/#7，C.2——断言/抽取/runlog/SKIP 语义全在旧代码里）；「快」只来自 conftest 装配",
        "的 vendored 垫片（有界语义等待）与截图抑制器（只丢成功 attach、失败证据保留）。",
        "第一红线（C.3）：本产物树零 import atlas；旧 lib 只经 ATLAS_LEGACY_PROJECT +",
        "ATLAS_LEGACY_WEB 两键定位（WEB 为相对子目录名或绝对路径皆可），",
        "缺配置运行期 EnvBlocked 显式点名，不静默、不猜路径。",
        '"""',
        "from __future__ import annotations",
        "",
        "import importlib",
        "import os",
        "import sys",
        "from pathlib import Path",
        "",
        "import pytest",
        "",
        "try:  # allure 在场=旧仓 title 形态；缺席=透传降级（旧 runner 有 _allure 短路先例）",
        "    import allure",
        "",
        "    def _title(text):",
        "        return allure.title(text)",
        "except ImportError:",
        "    def _title(text):",
        "        def _deco(func):",
        "            return func",
        "        return _deco",
        "",
        "",
    ]
    lines += _compat_runtime_lines()
    lines += [
        "",
        f"class {cls}:",
        f'    """旧仓兼容快包（flow {flow_id or "(未命名)"}）。order 编号=flow 步序（沿用旧次序）。"""',
        "",
    ]
    for s in steps:
        if isinstance(s, CompatBridgeStep):
            method = f"test_{s.case_id.lower()}_{_ident_slug(Path(s.module_rel).stem)}"
            tag = "[写操作-需人工确认] " if s.is_write else ""
            lines += [
                f"    @pytest.mark.order({s.order})",
                f"    @_title({s.intent!r})",
                f"    def {method}(self, get_page, plan_ctx):",
                f'        """{tag}{s.intent}  [桥接腿：整条委托旧 run_case_py，ctx 播种留旧代码]"""',
            ]
            for k in s.requires:
                upstream = f"flow 步骤 {s.order} 的前置（期望由先前批次写入）"
                lines.append(f"        _require_ctx(plan_ctx, {k!r}, {upstream!r})")
            lines += [
                f"        case_file = str(_legacy_testcases_root() / {s.module_rel!r})",
                "        runner = _legacy_import(\"lib.runner\")",
                f"        case = runner.load_yaml_case(case_file, {s.case_id!r})",
                "        page = get_page(case, plan_ctx)",
                (f"        runner.run_case_py(case_file, {s.case_id!r}, page, plan_ctx, "
                 f"skip_postcondition={s.skip_postcondition})"),
                f"        plan_ctx[{_compat_ok_key(s.order)!r}] = True",
                "",
            ]
        else:
            method = f"test_{s.order:02d}_{s.slug}"
            tag = "[写操作-需人工确认] " if s.is_write else ""
            lines += [
                f"    @pytest.mark.order({s.order})",
                f"    @_title({s.intent!r})",
                f"    def {method}(self, get_page, plan_ctx):",
                f'        """{s.intent}  [原生腿：legacy_pages 委托旧 lib.pages 函数；只读复用，绝不写旧仓]"""',
            ]
            for k in s.requires:
                lines.append(f"        _require_ctx(plan_ctx, {k!r}, '前置键需先前批次写入')")
            lines += [
                "        page = get_page(None, plan_ctx)",
                f"        _call_pages_fn({s.fn!r}, page, plan_ctx)",
                "",
            ]
    return "\n".join(lines).rstrip() + "\n"



def compat_combo_problem(compat: object, fast: object) -> str:
    """`--compat` 组合判据**唯一函数位**：非 none 必须与 --fast 同用，未知值点名。

    CLI 面（cli.py `_compat_gate`，cli-impl 文件）与生成器面（emit_pytest_package）
    共用本函数——判据只这一份（#92/lead P0）；谁另写一份 if 谁漂移。返回问题串（空=合法）。
    """
    if compat == "none":
        return ""
    if compat != "legacy-suite":
        return (f"未知 --compat 形态 {compat!r}（当前契约仅 none | legacy-suite；"
                f"新形态先进契约再实现，不产猜测中的产物）")
    if not fast:
        return ("--compat legacy-suite 需与 --fast 同用（兼容形态是快包的输出变体，"
                "--compat 不会隐式打开 --fast）")
    return ""


# ===========================================================================
# 产物剩余面（C.1 ini / README / 包入口）
# ===========================================================================


def render_compat_pytest_ini() -> str:
    """C.1 v2：产物自带 pytest.ini——判据面与旧仓对齐（emit-impl 修正③入档）：
    strict-markers 在场=未注册 marker 即红；order 注册；-p no:playwright/-p no:randomly。
    垫片注入走 conftest 委托 vendored fastplugin，**不用** -p atlas.fastplugin（C.3 零 atlas）。
    """
    return (
        "[pytest]\n"
        "# 生成候选（generator/emit_compat）：判据面对齐旧仓 iservice/pytest.ini（gap11 C.1）\n"
        "testpaths = testcases\n"
        "python_files = test_*.py\n"
        "python_classes = Test*\n"
        "python_functions = test_*\n"
        "\n"
        "markers =\n"
        "    order: 控制用例执行顺序（需 pytest-order>=1.1 插件，requirements 实证旧仓依赖）\n"
        "\n"
        "addopts =\n"
        "    --strict-markers\n"
        "    -p no:randomly\n"
        "    -p no:playwright\n"
    )


def render_compat_readme(flow_id: str, suite_slug: str, steps: list) -> str:
    fast_defaults = _compat_fast_defaults()   # 同源 CLI 面清单（README 不手抄默认值，#92）
    n_bridge = sum(1 for s in steps if isinstance(s, CompatBridgeStep))
    n_delegate = len(steps) - n_bridge
    lines = [
        "# atlas export-pytest --compat legacy-suite 产物（候选）",
        "",
        f"来源 flow：`{flow_id or '(未命名)'}`；步骤 {len(steps)}（桥接腿 {n_bridge} / "
        f"legacy_pages 委托腿 {n_delegate}）。",
        "",
        "> **横幅：只生成不执行**。产物可被具备旧仓的机器直接跑，但**绝不 import atlas**"
        "（第一红线 C.3）——旧代码只经 `ATLAS_LEGACY_WEB` 定位、整条委托，不重写 step_N、"
        "不弱化断言（红线 #4/#7）。",
        "",
        "## 依赖面（旧仓环境实证）",
        "",
        "- `pytest` + **`pytest-order>=1.1`**（`@pytest.mark.order` 离了它不生效；"
        "`--strict-markers` 在场，未注册 marker 直接红——ini 已注册 `order`）。",
        "- `playwright`（旧仓同款 `channel=\"chrome\"`，需系统 Chrome；无则设 "
        "`ATLAS_BROWSER_EXECUTABLE` 指向可执行文件）。",
        "- `allure-pytest` 可选：在场=title/附件形态同旧仓，缺席=title 透传降级（runner 先例）。",
        "- 产物内禁件：atlas 包本体（C.3）、pytest-playwright（ini 已 `-p no:playwright`，"
        "旧仓实测防第二个浏览器）、requests（走旧代码自有通道）。",
        "",
        "## 怎么跑",
        "",
        "```bash",
        "export ATLAS_LEGACY_PROJECT='<旧项目根，绝对>'            # 与 WEB 一起用（Atlas 标准 .env 形态）",
        "export ATLAS_LEGACY_WEB='iservice'                        # web 子目录名；给绝对路径也可单独使用",
        "                                                              # 两键缺/拼不出目录→EnvBlocked 点名是哪个键",
        "export ATLAS_FAST_LOGIN_INDICATOR='<登录态指示器选择器>'  # 必填；业务选择器只来自配置（#11）",
        "export ATLAS_FAST_ACCOUNT='<账号>' ATLAS_FAST_PASSWORD='<密码>'  # 回落 DEMO_*；凭据只从 env 读（#1）",
        "# export ATLAS_FAST_WAITS=mask            # 可选/默认关（#36）：要提速才显式启用 mask|mask+xhr|mutation",
        "                                          # 注意：真机 A/B 实证 mask/mask+xhr 在数据敏感链路上**间歇**",
        "                                          #   把失败点前移（同码同配置有时过、有时挂）；mutation 安全但零节省。",
        "                                          #   启用后必须重跑「失败点与基线对齐」验证，见本文件末节",
        "export ATLAS_FAST_NO_STEP_SHOTS=0         # 默认已开（补 %r）；显式设 0/off/false 才恢复每步成功截图"
        % (dict(fast_defaults).get("ATLAS_FAST_NO_STEP_SHOTS",),),
        "                                          # ↑ conftest 在 pytest_configure 里**只补 unset/空值**：--compat",
        "                                          #   产物按构造就是快包（--compat 强制配 --fast），不设 env 也默认",
        "                                          #   生效的只有**清单内**的开关（当前=截图抑制）；显式值恒优先",
        "export ATLAS_FAST_WAITS_STATS=./reports/settle.jsonl  # 可选：**不设=不落任何统计文件**（只打终端 summary 行）",
        "pytest testcases/" + suite_slug + " -q",
        "```",
        "",
        "## 与旧套件的差异（「快」的全部来源，其余逐字同形制）",
        "",
        "1. **有界语义等待（预算制，默认关闭，需显式 opt-in）**：vendored `legacy_fast.py` 把"
        "`wait_for_timeout(ms)` 换成「**本次等待内先观测到忙**（可见遮罩 / xhr 在途 / 真实 DOM 变化）"
        "**、随后转静默**」才提前返回；**整段从未观测到忙 → 按原 ms 睡满**（fail-safe，等价旧行为，"
        "统计里记 `no-busy-observed`）。原口径「探针一静就放行」已撤——真机 A/B 两轮实证它会把"
        "失败点前移（不带遮罩的等待首拍即真，「压根没东西可等」被读成「已经等完了」）。硬边界不变："
        "`actual_ms ≤ requested_ms`（**绝不更长**）、永不静默恰好睡满、probe 异常回退**剩余预算**。"
        "`readyState` 与就绪锚点属「**门**」（不满足就不许提前），本身不构成提前许可；"
        "`ATLAS_FAST_MIN_MS` 只是下限。",
        "   **这是实验档，不是默认开的功能**（#36，2026-11-10 真机三轮 A/B + 8 组档位探针后的裁决）："
        "`mask`（默认下限 400）与 `mask+xhr` 都在真机上**间歇**把失败点前移；`mutation` 2/2 同点"
        "但 `saved_ms≈0`（安全=没提速）；`mask`+`MIN_MS=3000` 3/3 同点但省下的 1–2s 落在基线跨批"
        "噪声（93–177s）以内、且本质是把语义等待退化成更短的固定等待。按 G11-1 硬规则"
        "（失败点前移即判行为变更），一个会间歇改变行为的档位不许默认发出去——所以 conftest"
        "**不再代置位 `ATLAS_FAST_WAITS`**，要快请自己设并自担验证责任。",
        "   启用后的通过闸（缺一不可）：① 失败点与基线**同一步**（前移即作废）；② 装配生效"
        "（`ATLAS_FAST_WAITS_STATS` jsonl 存在且 `calls>0`，或收尾 summary 行出现且 `calls>0`）；"
        "③ 才谈墙钟（多轮/中位）。`saved_ms` 与 `no_busy_observed` 是报告量，**不作任何通过条件**。"
        "本产物 README **不作「不会比原版更早失败」的承诺**。",
        "   档位：`ATLAS_FAST_WAITS=mask|mask+xhr|mutation`（xhr/mutation 为同档递加），下限 "
        "`ATLAS_FAST_MIN_MS`（默认 min(400,requested)）、就绪锚点 `ATLAS_FAST_SETTLE_ANCHOR`"
        "（选择器来自 env，模板不带业务字面量）。",
        "2. **成功截图抑制**：**默认已开**（unset/空值补 "
        f"`{dict(fast_defaults).get('ATLAS_FAST_NO_STEP_SHOTS', '')}`；显式 `ATLAS_FAST_NO_STEP_SHOTS=0` 恢复旧形态）。"
        "开启时只丢 `stepN_result` attach；"
        "`stepN_FAIL` 与失败落盘字节保留；PNG 编码耗时不在本波节省声明内（B.2 诚实边界）。",
        "3. **缺前置显式终止**：普通函数 `_require_ctx`（非 fixture，C5c 面）缺键抛 EnvBlocked 点名，"
        "不沿用旧仓 `pytest.skip` "
        "静默形态（F5/gap9b 纪律——这是与旧文件的一处**有意**行为差异）。",
        "4. plan_ctx 白名单键=本包步骤派生（旧仓手抄表等价，不漂移），落"
        "`<本目录>/reports/plan_ctx.json`。",
        "5. 运行记录面=旧 allure-results。**统计只属于等待垫片**（#36 后默认不启用——要统计先按第 1 条显式设 "
        "`ATLAS_FAST_WAITS`）。垫片启用时，**统计落盘只在显式设置时发生**：设 "
        "`ATLAS_FAST_WAITS_STATS=<路径>.jsonl` → 垫片收尾写**逐调用 jsonl**（每行 "
        "caller_file/caller_line/caller_func/requested_ms/actual_ms/probe/fallback）+ 终端一行 "
        "summary（calls/original_ms/actual_ms/saved_ms/fallbacks/no_busy_observed/floor/anchor/"
        "tiers）；**不设该键=不产生任何统计文件**，只打终端 summary 行。三度量（等待节省、真回退数、"
        "被抑制 attach 张数）就从这份 jsonl 与 allure 条目数读——产物**不承诺任何别处不存在的文件**。"
        "**compat 形态不产 runs.jsonl**——报告面单一"
        "真相留在旧 allure，不开第二条消费链（需要 Atlas 汇总形态请用 --compat none）。",
        "",
        "## 垫片来源（锁步判据）",
        "",
        "`fastplugin.py` / `legacy_fast.py` 为 vendored 原样字节拷贝（头部 provenance=源相对路径"
        "+sha256，源=`src/atlas/legacy_fast.py`、`src/atlas/fastplugin.py`）。判据测钉"
        "「vendor 字节==源字节（除头）」——源改了不重新生成即红。请勿手抄/改动产物内垫片；"
        "要调整改源文件，再重新 `atlas export-pytest --compat legacy-suite`。",
        "",
        "## 红线警示",
        "",
        "- 绝不写旧仓（#4）：产物全部落 Atlas 侧导出目录；旧 lib 只读复用。",
        "- 写操作腿（docstring `[写操作-需人工确认]`）执行前人审环境只读保护与「非人工创建」"
        "备注；生产环境保护不随产物迁移——产物运行不受 Atlas engine 护栏约束，环境选择永远是"
        "人的决定（#2/#13）。",
        "- 本产物进 `atlas review` 审核塔（trace_step），不自动写知识（#9/#10）。",
    ]
    return "\n".join(lines) + "\n"


def _atlas_repo_root() -> Path:
    """Atlas 仓根（垫片源文件定位）：本文件在 src/atlas/generator/ 下，上溯四级；不依赖 cwd/env。"""
    return Path(__file__).resolve().parents[3]


def emit_compat_package(*, out_dir: Path, flow: dict, flow_id: str = "",
                        force: bool = False) -> list[str]:
    """写出旧仓兼容快包（C.1 产物树），返回写入路径。**一切拒绝先于任何写盘**（零残留）。

    确定性输出（无时间戳/随机）；重复生成同内容。判据测面：产物每个 .py 过 atlas-free 双扫描
    + ast.parse；vendor 字节==源（除头）；fixture 对拍 AST 面一致。
    """
    out_dir = Path(out_dir)
    if not flow:
        raise CompatEmitError(
            "compat 形态只接 flow 源（桥接腿/声明位都来自 flow steps；"
            "endpoints 源没有旧用例把手，不产半成品）")
    flow_id = flow_id or str(flow.get("id") or "")
    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        raise CompatEmitError(f"目标目录非空：{out_dir}（加 force=True 覆盖/继续）")
    steps = compat_specs_from_flow(flow)  # 支持性/缺失/坏形全在此点名（写盘前）
    problems = compat_symbol_problems(steps)
    if problems:
        raise CompatEmitError("；".join(problems))
    from .emit_pytest import slugify as _slug
    # 旧仓套件命名惯制=下划线（test_prod_am_regression.py）；flow id 常含 dash，归一
    suite_slug = _slug(flow_id or "compat-suite", fallback="compat-suite").replace("-", "_")
    test_rel = Path("testcases") / suite_slug / f"test_{suite_slug}.py"

    conftest_src = render_compat_conftest(steps, flow_id)
    module_src = render_compat_test_module(suite_slug, steps, flow_id)
    ini_src = render_compat_pytest_ini()
    vendored = {Path(rel).name: vendor_shim_source(_atlas_repo_root(), rel)
                for rel in SHIM_SOURCE_FILES}
    readme_src = render_compat_readme(flow_id, suite_slug, steps)

    # 语法自检在写盘之前：坏模板不落半成品（#9「不假装可用」）
    for text, name in ((conftest_src, "conftest.py"), (module_src, str(test_rel))):
        ast.parse(text, filename=name)
    for name, blob in sorted(vendored.items()):
        ast.parse(blob.decode("utf-8"), filename=name)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    (out_dir / "conftest.py").write_text(conftest_src, encoding="utf-8")
    written.append(str(out_dir / "conftest.py"))
    (out_dir / "pytest.ini").write_text(ini_src, encoding="utf-8")
    written.append(str(out_dir / "pytest.ini"))
    test_path = out_dir / test_rel
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(module_src, encoding="utf-8")
    written.append(str(test_path))
    for name, blob in sorted(vendored.items()):
        (out_dir / name).write_bytes(blob)
        written.append(str(out_dir / name))
    (out_dir / "README.md").write_text(readme_src, encoding="utf-8")
    written.append(str(out_dir / "README.md"))
    return written

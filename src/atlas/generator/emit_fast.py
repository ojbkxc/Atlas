"""快模式底座模板（gap9 F1–F9 + lead 裁决 F-1…F-7）：产出产物侧自包含运行底座与会话层源码文本。

本模块只产出**源码字符串**（红线 #9 只生成不执行）：绝不 import 被执行产物、绝不发网络
请求、绝不起子进程。产物依赖栈 = pytest + playwright（F9 论证：playwright 是既有声明依赖，
不触 AGENTS §4「浏览器驱动之外的运行时」；allure-pytest / pytest-playwright / pytest-timeout /
requests 一律禁入产物）。

产物内容（`_atlas_fast.py` + `conftest.py`）：单浏览器/单登录（指示器实查为权威）、无头默认
+启动参数组、语义等待（waits.py 同源冻结派生版，#133 瞬态分流）、零成功截图（仅失败腿取证，
attachment 写绝对路径）、plan_ctx 跨批上下文（缺前置 = EnvBlocked 显式终止）、用例看门狗
（超时行 status=timeout，close 幂等不许翻绿）、Atlas 兼容 runs.jsonl 写手（`atlas allure` 直收）。

红线落点：
- #7 不洗绿：终态只有 `RUN_STATUS_VOCAB` 六值；`NotWired`（F-2）是未接线**唯一**异常出口，
  归因走类型继承既有 `EnvBlocked→env_blocked` 支，绝不按异常文本/op 猜；真代码缺陷
  （含裸 `NotImplementedError`）仍落 unknown/failed。
- #11 零业务字面量：模板不含客户号/域名/URL/登录指示器——全部 env 旋钮，缺配即 EnvBlocked。
- F-5 reason 边界：run 级 reason 只取框架自己生成的点名文案（`framework_reason` 单点，
  非空才写键、截 200）；其他异常类型（HTTP 正文、URL query、服务端原文）根本不写盘——
  「不写不可控文本」强于「写了再洗」，产物里不复制第二把净化刀（#92 反面）。

接口契约见 `docs/gaps/gap9-fast-pytest-runner.md` §C.1 + §F 与 `docs/gaps/gap9-lead-rulings.md`。
**不 import emit_pytest**（防环：emit_pytest 的 fast 分支反向 import 本模块）。
"""
from __future__ import annotations

from pathlib import Path

from .dsl_utils import artifact_compile_problems

# 产物底座模块名（同 PAGES_MODULE_NAME 模式；C.1 钉死值）
FAST_SUPPORT_MODULE_NAME = "_atlas_fast"


class FastEmitError(ValueError):
    """快模式产物写出时的结构问题（目标文件冲突、形态错误）。不 import emit_pytest（防环）。"""


# ---------------------------------------------------------------------------
# 产物底座 `_atlas_fast.py`（自包含：仅标准库；playwright 只 duck-type 使用不 import）
# ---------------------------------------------------------------------------

_FAST_SUPPORT_SOURCE = '''"""生成候选（generator/emit_fast）：快模式底座（自包含：仅标准库+playwright，可脱离 Atlas 运行）。

Gap9 快模式运行底座。产物目录内与 Atlas 解耦运行：**不 import atlas 包、不发网络请求**。
本文件是生成产物的冻结模板——语义等待/终态词汇/瞬态判据的上游真相在
src/atlas/{waits,failures,recorder}.py；产物侧为同源派生版，更新靠重新生成，漂移由
tests/test_emit_fast.py 锁步测（gap9 E.1.4）拦截，不在产物内独立演化（#92 派生治理）。

=== 本文件结构（读者导航：想改什么→看哪节）===
- 终态词汇 RUN_STATUS_VOCAB / 异常出口 EnvBlocked·NotWired·AwaitingHuman：改「一条 run 最终叫什么」。
- 分类 classify_fail_class / FAIL_CLASS_MAP：改「失败怎么归因」（只看异常类型）。
- 会话通道 session_channel / attach_denied_by_prod：改「launch 还是 attach / 生产禁 attach」。
- 等待语义 wait_for_rows / wait_for_mask_gone / wait_for_vue_field / wait_for_url_change：
  改「等表格/遮罩/Vue 字段/URL 变」的判据（轮询 tick=TICK_MS，无固定睡眠）。
- 浏览器矩阵 browser_channel / browser_family / launch_kwargs / missing_browser_hint：改「用哪个浏览器引擎」。
- 跨批上下文 load_plan_ctx / save_plan_ctx / safe_persist / require_ctx：改「前置值怎么存/校验」。
- runlog RunWriter / framework_reason：改「runs.jsonl 行怎么写」。
- 看门狗与用例外壳 fast_case / trace_enabled：改「单用例超时/失败留证」。

=== 产物读者：runs.jsonl 一行长这样（_fast_runlog 写，Atlas 兼容）===
{"run_id": "…", "case": "test_01_…", "env": "local", "mode": "run", "status": "passed",
 "started_at": "…Z", "finished_at": "…Z", "steps": [{"step": "…", "op": "…", "ok": true,
 "dur_ms": 12.3}], "reason": "…"?}
（缺配置/前置/未接线时 status 是 env_blocked 且 reason 点名在等什么；等人=waiting；
 断言失败=failed；被看门狗掐表=timeout。具体键缺省不写，与 Atlas recorder.py 逐字对齐。）

=== 治理注记对照表（维护者看这里；产物接线人可跳过）===
产物侧出现的一切 #编号/F-x/锁步引用，其裁决与出处集中在下表，正文只保留「行为+判据+失败面」：
- 红线 #7：终态决策——缺配置/缺前置/未接线一律显式 EnvBlocked/NotWired 终止，绝不静默跳过、
  绝不把失败映射成通过（对齐 AGENTS §红线 #7）。
- 红线 #11：模板零业务字面量——URL/账号/登录指示器/遮罩选择器以外的业务值只从 env 读。
- 红线 #2：失败腿 trace.zip 含密码，双确认才开（默认关=成功腿零开销）。
- F-2：NotWired 是未接线唯一出口，归因只看类型（继承 EnvBlocked），不按文本猜。
- F-3/F-4：等待一律语义轮询，page.wait_for_timeout 实参恰为裸 TICK_MS（禁字面毫秒/乘算/time.sleep）。
- F-5：run 级 reason 只经 framework_reason() 取框架点名文案（非空才写键、截 200）。
- F-6：底座模板唯一来源是本模块（别处在生成期 import 这些符号，不复制第二份模板文本）。
- F-11/B-3：attach 三条件（CDP 非空 ∧ ATLAS_FAST_INTERACTIVE==1 ∧ 非 prod-）单一判据在 session_channel。
- T3/T5/T4：分片(--parallel)/浏览器矩阵/trace 三个运行期旋钮的判据各自成节。
- uf-1：UI 族签名 UI_FAMILY* 由通用包 ui_family.json 固化，锁步测比对（不留两份漂移）。
- #92：派生治理——上游改一处、产物重生成，锁步测拦截漂移，不在产物内独立演化。
- #36/G11：等待垫片默认关；本底座只做语义轮询，不默认置位 ATLAS_FAST_WAITS。
- #31/#30：装配状态读 _STATE 事实而非 env 面文本（「以为开了其实没装」教训）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:  # 跨进程文件锁：Windows 用 msvcrt，POSIX 用 fcntl（对齐 recorder.py 并发双保险纪律）
    import msvcrt as _filelock

    _LOCK_KIND = "msvcrt"
except ImportError:
    import fcntl as _filelock

    _LOCK_KIND = "fcntl"

# ---------------------------------------------------------------------------
# 终态词汇（锁步 atlas.failures：env_blocked/interrupted/waiting ∈ FailureClass 值域，
#    timeout == failures.FLOW_STATUS_TIMEOUT；passed/failed 是 run 级终态非失败类）
# ---------------------------------------------------------------------------
FLOW_STATUS_TIMEOUT = "timeout"
RUN_STATUS_VOCAB = ("passed", "failed", "env_blocked", "interrupted", "waiting",
                    FLOW_STATUS_TIMEOUT)

ARTIFACTS_DIR_NAME = "artifacts"

# 语义等待轮询节奏（锁步 atlas.waits 的 TICK_MS tick；pf-4：25ms 对齐 legacy_fast 垫片，
# 只降解析延迟上限、不动忙判据 #36）。
# F-3/F-4：这是全文件唯一允许的等待步长——page.wait_for_timeout 的实参必须是裸 TICK_MS。
TICK_MS = 25

# 导航瞬态判据（锁步 atlas.waits._TRANSIENT_MARKS）：只有这类抛错值得穿越 reload 继续等。
# 完整子串口径：navigating≠navigation；"detached" 兜 frame-detached 形态。
_TRANSIENT_MARKS = ("execution context was destroyed", "navigation", "detached")

# UI 族签名（uf-1）：生成期从通用包 plugins/_common/knowledge/ui_family.json 解析后**固化**到
# 产物（产物零 import atlas，运行期读不到知识包）。页面无此签名 = 非本族，mask「已落定」不可信。
# 锁步测（tests/test_emit_fast.py）从本源提取这几个字面量与 atlas.ui_family.resolve_family() 比对，
# 通用包改族条目而这里没同步即红（#92：不留两份各自漂移）。
UI_FAMILY = "element-ui"
UI_FAMILY_SIGNATURE = ".el-button, .el-table, .el-menu, .el-dialog__wrapper, .el-input__inner"
UI_FAMILY_PREFIXES = (".el-",)
UI_FAMILY_HAS_MASK = True

# run 级 reason 截断（同 Atlas 口径）
REASON_MAX_LEN = 200


class EnvBlocked(RuntimeError):
    """环境/配置阻塞终态（F5）：缺配置、缺前置、依赖缺席都走这里——显式终止，绝不静默跳。"""


class NotWired(EnvBlocked):
    """「选择器/业务操作未经人审接线」哨兵（lead 裁决 F-2）——产物侧未接线的**唯一**异常出口。

    类型级判据：归因走继承（既有 EnvBlocked→env_blocked 一支自动生效），映射表零新增分支；
    绝不按异常文本猜「未接线」——那是给真代码缺陷开一条洗成 skipped 的门（红线 #7 反面，
    对齐 failures.classify_failure 只看类型、_RULES 一字不加的同族防线）。
    reason 必须点名组件与元素，裸一条不许（空消息构造期拒，对齐 Atlas AwaitingHuman 纪律）。
    真 NotImplementedError（代码缺陷类）不在此列：它继续落 unknown/failed。"""

    def __init__(self, reason: str):
        if not str(reason).strip():
            raise ValueError("NotWired 必须点名未接线的组件/元素（裸一条=新型静默）")
        super().__init__(reason)


class AwaitingHuman(RuntimeError):
    """等待人工终态（词汇对齐 Atlas C9）：强制非空原因——裸一条 waiting 落盘等于新型静默。"""

    def __init__(self, reason: str):
        if not str(reason).strip():
            raise ValueError("AwaitingHuman 必须带非空原因（在等什么、需要谁）")
        super().__init__(reason)


# 控制流不是业务失败（锁步 atlas.failures.CONTROL_FLOW 三元组）
_CONTROL_FLOW = (KeyboardInterrupt, SystemExit, GeneratorExit)

# fail_class 局部映射表（F7/F-2）：**只看类型**，值域必须是 atlas.failures.FailureClass
# 成员（锁步测钉死）。NotWired 不入表——isinstance(EnvBlocked) 支经继承自动生效；
# NotImplementedError/其余异常落 unknown（真缺陷不得被任何支洗成 env_blocked/skipped）。
FAIL_CLASS_MAP = (
    (AssertionError, "assertion"),
    (TimeoutError, "interaction"),
    (EnvBlocked, "env_blocked"),
)
FAIL_CLASS_DEFAULT = "unknown"
FAIL_CLASS_INTERRUPTED = "interrupted"
FAIL_CLASS_WAITING = "waiting"


def classify_fail_class(exc: BaseException) -> str:
    """异常 → fail_class。次序：控制流 → 等人 → 映射表（类型 isinstance）→ unknown。"""
    if isinstance(exc, _CONTROL_FLOW):
        return FAIL_CLASS_INTERRUPTED
    if isinstance(exc, AwaitingHuman):
        return FAIL_CLASS_WAITING
    for exc_type, fail_class in FAIL_CLASS_MAP:
        if isinstance(exc, exc_type):
            return fail_class
    return FAIL_CLASS_DEFAULT


def framework_reason(exc: BaseException) -> str:
    """run 级 reason 的唯一取文入口（F-5）：只认框架点名文案，不复制第二把净化刀。

    EnvBlocked/NotWired/AwaitingHuman 的消息**只由产物模板与生成代码产生**（恒定句式、
    点名 env 键/组件/元素/前置键）；其余异常类型（HTTP 正文、URL query、服务端原文）
    一律返回空串——「根本不写不可控文本」强于「写了再洗」：产物不 import atlas，在模板里
    复制 sanitize_text 会得到两把各自演化的刀（#92 反面）。"""
    if isinstance(exc, (EnvBlocked, AwaitingHuman)):
        try:
            text = str(exc)
        except Exception:
            return ""
        return text[:REASON_MAX_LEN]
    return ""


# ---------------------------------------------------------------------------
# 会话通道（gap9b B-3；conftest 只调不复制）
# ---------------------------------------------------------------------------
# attach 的语义前提=「有人看着这次运行」。CI/shell 残留的 ATLAS_FAST_CDP 绝不允许
# 悄悄接管网页——在场闸（ATLAS_FAST_INTERACTIVE=="1"）是有意的冗余（F-16 裁决）。
# prod 前缀判据与 Atlas config.py 的 `env.startswith("prod-")` 等值：锁步测从 config.py
# 源文本提取比对（B-4.9），本模块手写的这一处是唯一实现位。

def _attach_endpoint(env) -> str:
    env = env or {}
    return str(env.get("ATLAS_FAST_CDP", "") or "").strip()


def _is_production(env) -> bool:
    env = env or {}
    return str(env.get("ATLAS_FAST_ENV", "") or "").strip().lower().startswith("prod-")


def session_channel(env) -> str:
    """唯一判据位（纯函数）：env 映射 → "attach" | "launch"（gap9b B-3 原文三条件）。

    `"attach"` ⇔ `ATLAS_FAST_CDP` 非空 ∧ `ATLAS_FAST_INTERACTIVE == "1"` ∧ `ATLAS_FAST_ENV`
    不以 "prod-" 开头；任一不满足 → "launch"。prod+CDP 同场**不抛错**——落 launch +
    WARN（WARN 由 conftest 经 attach_denied_by_prod 决定打不打，B-3.4：不给强制开关）。
    只吃传入的 env，不读进程环境、无副作用：八向真值表测直接喂 dict。
    """
    env = env or {}
    if not _attach_endpoint(env):
        return "launch"
    if str(env.get("ATLAS_FAST_INTERACTIVE", "") or "").strip() != "1":
        return "launch"
    if _is_production(env):
        return "launch"
    return "attach"


def attach_denied_by_prod(env) -> bool:
    """True=CDP 在场且 prod- 前缀（B-3.4 WARN 行的触达判据，ASCII 前缀 ATTACH-DISALLOWED-IN-PROD）。

    与 session_channel 共享私有原子（端点/前缀判据各只一处），本函数只组合「CDP 在场
    且环境为 prod」这一负向形态——prod 检查**不**外流给 conftest 复制（#92）。
    触发不含在场闸：prod 环境里残留 CDP 本身就值得喊一声，无论谁在场。"""
    env = env or {}
    return bool(_attach_endpoint(env)) and _is_production(env)


# ---------------------------------------------------------------------------
# 并行分片（T3）：ATLAS_FAST_PARALLEL 运行期旋钮 + 缺依赖点名（绝不静默降级）
# ---------------------------------------------------------------------------
# 产物默认单进程（不引新依赖）；>1 才需 pytest-xdist。判据单点在本模块，conftest 只调不复制。
# 边界（写清「怎么坏」）：xdist 按**用例**分发，本底座 sole_page 是 module scope——每个
# worker 首次触达某模块即各自 launch+login 一次，故 N worker ≈ 最多 N 只浏览器/N 次登录；
# 跨批 plan_ctx 在并行下不可靠（worker 无固定顺序、同一文件并发读写），并行只适用于
# **彼此独立**的批次（详见产物 README 的并行段）。

def parallel_workers(env) -> int:
    """env → 请求的 worker 数（1=单进程）。ATLAS_FAST_PARALLEL 缺省/空白 → 1；
    非整数 / <=0 → EnvBlocked 点名键（不静默当单进程跑——那正是「静默降级」本身）。"""
    env = env or {}
    raw = str(env.get("ATLAS_FAST_PARALLEL", "") or "").strip()
    if not raw:
        return 1
    try:
        workers = int(raw)
    except ValueError:
        raise EnvBlocked(
            f"ATLAS_FAST_PARALLEL={raw!r} 非整数：留空/1=单进程，>1 需 pytest-xdist")
    if workers < 1:
        raise EnvBlocked(f"ATLAS_FAST_PARALLEL={workers} 须为正整数（1=单进程）")
    return workers


def xdist_available() -> bool:
    """pytest-xdist 是否可导入（并发诉求的**真实依赖**）。用 __import__ 不引 import 语句
    （产物 import 面保持 stdlib+pytest+playwright；#92 不留死依赖）。异常一律当不可用。"""
    try:
        __import__("xdist")
        return True
    except Exception:
        return False


def parallel_setup_problem(workers: int, *, xdist_available: bool) -> str:
    """返回空串（可继续）或点名文案：workers>1 缺 xdist → 报错点名 pip install（不降级）。"""
    if workers <= 1:
        return ""
    if not xdist_available:
        return (f"缺依赖：ATLAS_FAST_PARALLEL={workers} 需 pytest-xdist——运行 "
                "`pip install pytest-xdist`（不静默降级为单进程）")
    return ""


def _exception_text(exc: BaseException) -> str:
    """异常定性文本：类型名+消息；坏 __str__ 回落类型名（定性器不得先于被定性对象死，#89）。"""
    try:
        return f"{type(exc).__name__} {exc}"
    except Exception:
        return type(exc).__name__


def _is_transient(exc: BaseException) -> bool:
    """导航瞬态才值得继续等；其余（Target closed/语法错/业务 JS 自抛超时）立即传播——
    烧满窗口后谎报「未达 N 行/无值」= 框架故障伪装成等待契约失败（#133/#102 同族）。"""
    text = _exception_text(exc)
    return any(mark in text for mark in _TRANSIENT_MARKS)


# ---------------------------------------------------------------------------
# runs.jsonl 写手（F7：复刻 Atlas recorder 行 schema，`atlas allure` 直接可吃）
# ---------------------------------------------------------------------------

_INPROC_LOCK = threading.Lock()

if _LOCK_KIND == "msvcrt":

    def _lock_exclusive(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        _filelock.locking(fd, _filelock.LK_LOCK, 1)

    def _release_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        _filelock.locking(fd, _filelock.LK_UNLCK, 1)
else:

    def _lock_exclusive(fd: int) -> None:
        _filelock.flock(fd, _filelock.LOCK_EX)

    def _release_lock(fd: int) -> None:
        _filelock.flock(fd, _filelock.LOCK_UN)


def _append_line_locked(path: Path, payload: bytes) -> None:
    """进程内串行 + 跨进程排他锁，锁内整行一次追加（残行会毁掉整份审计链）。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY  # Windows：关掉 \\n→\\r\\n 翻译，JSONL 字节形态跨平台一致
    fd = os.open(path, flags, 0o644)
    try:
        with _INPROC_LOCK:
            _lock_exclusive(fd)
            try:
                view = memoryview(payload)
                while view:  # os.write 不承诺一次写全，短写循环补齐（仍在锁内）
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                try:
                    _release_lock(fd)
                except OSError:
                    pass  # 解锁失败不顶掉已落盘的证据；锁随 fd 关闭必然释放
    finally:
        os.close(fd)


# 当前活动页面（截图收口用）：由 conftest 的 get_page 取到会话页面时写入。
# 模块级单点而不是 fixture 依赖：API 腿用例不应为「可能的截图」被迫拉起浏览器。
_CURRENT_PAGE = [None]


def set_current_page(page) -> None:
    _CURRENT_PAGE[0] = page


def current_page():
    return _CURRENT_PAGE[0]


# ---------------------------------------------------------------------------
# 失败腿 trace 归档（T4）：ATLAS_FAST_TRACE=="1" 才启用；默认关=零开销（红线 #8 同族）
# ---------------------------------------------------------------------------
# ⚠ 凭据红线（如实标注 + 最小硬化，不写假脱敏）：Playwright trace.zip（screenshots+snapshots）会
# 带进**网络请求/响应体与头**，且动作元数据记录 fill()/press() 的**输入值**——登录链路把密码
# 经 fill/网络 POST 送出时，明文可能落进 trace.zip。Playwright 无内置脱敏。故**双确认**才开：
# 仅 ATLAS_FAST_TRACE=1 **不足以**（缺第二键 → trace 不开 + 点名 WARN），还需
# ATLAS_FAST_TRACE_ACCEPT_SECRETS=1（人显式承认「会有密码落 zip，我自己保管」）。
#   规避：①不设两键（默认即关，成功腿连 start 都不调=零开销）；②trace.zip 视同敏感取证物
#        （落 reports/traces/，gitignore 内，勿提交/外发）；③分享前人工审阅裁剪。
#        sources=False 只是不打包脚本源码，**不**擦网络载荷。
TRACES_DIR_NAME = "traces"
TRACE_ACK_WARN_PREFIX = "WARN: TRACE-ACK-REQUIRED"


def trace_enabled(env) -> bool:
    """**双确认**才启用 trace：ATLAS_FAST_TRACE=="1" ∧ ATLAS_FAST_TRACE_ACCEPT_SECRETS=="1"。

    缺任一 → False（红线 #2 凭据不落盘：默认关使「成功腿零开销」字面成立——连 start 都不调）。
    只认**精确 "1"**（不 strip、不大小写折叠）：`"1 "`/`"yess"` 等坏值一律**按关**处理，
    绝不静默生效（凭据面从宽=事故）。"""
    env = env or {}
    armed = str(env.get("ATLAS_FAST_TRACE", "") or "") == "1"
    acked = str(env.get("ATLAS_FAST_TRACE_ACCEPT_SECRETS", "") or "") == "1"
    return armed and acked


def trace_ack_required(env) -> bool:
    """ATLAS_FAST_TRACE=1 但**缺** ACCEPT_SECRETS 确认 → True（trace 不开，由 conftest 喊 WARN）。"""
    env = env or {}
    armed = str(env.get("ATLAS_FAST_TRACE", "") or "") == "1"
    acked = str(env.get("ATLAS_FAST_TRACE_ACCEPT_SECRETS", "") or "") == "1"
    return armed and not acked


def trace_ack_warning(env) -> str:
    """缺第二确认键时的点名 WARN（X4：ASCII 前缀可 cp936）；无需告警则空串。"""
    if not trace_ack_required(env):
        return ""
    return (f"{TRACE_ACK_WARN_PREFIX}: ATLAS_FAST_TRACE=1 会把网络载荷/fill 输入值（含密码）"
            "写进 trace.zip，本波红线 #2 禁止——trace 未开启。确要取证请显式设 "
            "ATLAS_FAST_TRACE_ACCEPT_SECRETS=1（=承认 zip 含密码）；开启后 zip 落 "
            "reports/traces/（gitignore 内），**勿外发/勿入库**，事后审计见运行行 "
            "trace_secrets_ack=true")


def trace_path(runs_path, run_id) -> Path:
    """trace.zip 落点：<runs 同目录>/traces/<run_id>.zip（与截图 artifacts 同族、gitignore 内）。"""
    return Path(runs_path).parent / TRACES_DIR_NAME / f"{run_id}.zip"


def start_tracing(page) -> bool:
    """尽力而为启动 Playwright tracing（截图+DOM 快照;sources=False 不带脚本源码）。
    无 page / 无 tracing API / 抛错 → False（零涟漪：取证自身失败绝不改变用例判定，session.py 纪律）。"""
    if page is None:
        return False
    try:
        page.context.tracing.start(screenshots=True, snapshots=True, sources=False)
        return True
    except Exception:
        return False


def stop_tracing(page, path=None) -> bool:
    """停止 tracing；path 非空则落盘 trace.zip（否则丢弃缓冲区=成功腿不产文件）。失败静默 False。"""
    if page is None:
        return False
    try:
        if path:
            page.context.tracing.stop(path=str(path))
        else:
            page.context.tracing.stop()
        return True
    except Exception:
        return False


def default_runs_path() -> Path:
    """产物默认 runs.jsonl = <底座文件所在目录>/reports/runs.jsonl（C.1 钉死路径）。"""
    return Path(__file__).resolve().parent / "reports" / "runs.jsonl"


def _safe_default(obj):
    try:
        return str(obj)
    except Exception:
        return f"<unrepresentable {type(obj).__name__}>"


class RunWriter:
    """runs.jsonl 写手：复刻 Atlas recorder.py 的行 schema（键名/类型/缺省不写键逐字对齐）。

    - 一 test 一行：`.step(name)` 记动作/断言（断言步名以 "assert " 开头，对齐
      allure_analysis 的 assert 计数口径），`.close(status, reason)` 落整行。
    - `close` 缺省不写 reason 键（recorder C9 纪律）；reason 只准经 framework_reason() 来
      （F-5）。重复 close 幂等——看门狗先写 timeout 行后，用例收尾**不得**把它改写回
      passed/skipped（F6③）。
    - `path=None` → 产物目录 reports/runs.jsonl；`ATLAS_RUNS_DIR` 并发逃生舱与 Atlas 同语义
      （相对值按目标文件父目录解析、绝对值原样用、文件名不变）。
    - 成功步骤不截图（红线 #8）；失败腿尽力而为写 step["attachment"]，**绝对路径**
      （Allure 对绝对路径直通，相对路径会按 Atlas root 解析而悬空）。
    """

    def __init__(self, case, env, path=None, mode: str = "run"):
        base = Path(path) if path is not None else default_runs_path()
        override = os.environ.get("ATLAS_RUNS_DIR", "").strip()
        if override:
            directory = Path(override)
            if not directory.is_absolute():
                directory = base.parent / directory
            base = directory / base.name
        self.case, self.env, self.mode = str(case), str(env), str(mode)
        self.path = Path(base)
        self.run_id = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.steps: list = []
        self._closed = False
        self._lock = threading.Lock()
        self.trace = None   # 失败腿 trace.zip 绝对路径（T4）；None=不写键（成功腿/未启用）
        self.trace_secrets_ack = None   # 双确认取证的可审计标记（红线 #2）：True 才写键

    @property
    def closed(self) -> bool:
        return self._closed

    def set_trace(self, path) -> None:
        """登记失败腿 trace.zip 路径（T4，尽力而为）：仅当 tracing 真启动且落盘成功才调。
        只写**路径**进 runs.jsonl——不回写内容（凭据面见 trace_enabled 段注释）。"""
        self.trace = str(path)

    def set_trace_secrets_ack(self, value=True) -> None:
        """双确认取证的可审计标记（红线 #2）：让事后审计看得出「这份 trace 是有人明知故犯取的」。"""
        self.trace_secrets_ack = bool(value)

    @contextmanager
    def step(self, name, op: str = "action"):
        index = len(self.steps) + 1
        item = {"step": str(name), "op": op, "started": time.perf_counter()}
        try:
            yield item
        except BaseException as exc:
            # 定性器不得先于被定性对象死（#126 面A 同纪律）：定性失败诚实回落 unknown。
            try:
                item["fail_class"] = classify_fail_class(exc)
            except Exception:
                item["fail_class"] = FAIL_CLASS_DEFAULT
            self._capture(item, index)  # F4：只有失败腿走到这里
            raise
        finally:
            started = item.pop("started")
            item["dur_ms"] = round((time.perf_counter() - started) * 1000, 1)
            item["ok"] = "fail_class" not in item
            with self._lock:
                self.steps.append(item)

    def _capture(self, item: dict, index: int) -> None:
        """失败截图（尽力而为）：取证失败绝不改变用例判定、绝不顶掉真因（session.py 纪律）。"""
        page = _CURRENT_PAGE[0]
        if page is None:
            return
        try:
            if page.is_closed():
                return
            folder = self.path.parent / ARTIFACTS_DIR_NAME
            folder.mkdir(parents=True, exist_ok=True)
            safe_case = re.sub(r"[^A-Za-z0-9_.-]", "_", self.case)
            shot = folder / f"{safe_case}_{index}.png"
            page.screenshot(path=str(shot), full_page=False)
            item["attachment"] = str(shot.resolve())  # 绝对路径（F4：Allure 直收）
        except Exception:
            return

    def close(self, status: str = "passed", reason: str = "") -> Path:
        if status not in RUN_STATUS_VOCAB:
            raise ValueError(
                f"未知终态 {status!r}：只许 RUN_STATUS_VOCAB 六值（词汇锁步 atlas.failures，"
                "不许把失败洗成其它态）")
        with self._lock:
            if self._closed:
                return self.path  # 幂等：首行落盘后不得改写（F6 看门狗行的防线）
            self.path.parent.mkdir(parents=True, exist_ok=True)
            record = {"run_id": self.run_id, "case": self.case, "env": self.env,
                      "mode": self.mode, "status": status, "started_at": self.started_at,
                      "finished_at": datetime.now(timezone.utc).isoformat(),
                      "steps": list(self.steps)}
            if self.trace:
                record["trace"] = self.trace  # T4：失败腿取证路径（只写路径，见 set_trace）
            if self.trace_secrets_ack:
                record["trace_secrets_ack"] = True  # 红线 #2 双确认标记（可审计）
            if reason:
                record["reason"] = str(reason)[:REASON_MAX_LEN]  # 非空才写键（缺省形态=存量）
            # default 兜底永不抛（#126 面B）：坏字段可以有噪音，不能丢整轮证据。
            line = json.dumps(record, ensure_ascii=False, default=_safe_default)
            _append_line_locked(self.path, (line + "\\n").encode("utf-8"))
            self._closed = True  # 仅在证据确实落盘后置位：写失败可重试不谎报
            return self.path


# ---------------------------------------------------------------------------
# 语义等待（waits.py 同源冻结派生版，F3；#133 瞬态分流；uf-1 族前缀特征 / uf-2 运行时点名）
# ---------------------------------------------------------------------------


class UiFamilyMismatch(RuntimeError):
    """页面未呈现族前缀特征（uf-1）：mask「已落定」不可信——显式点名，不静默秒回。"""

    def __init__(self, family, prefixes, declared_in_table=True):
        self.family = family
        self.prefixes = list(prefixes or [])
        self.declared_in_table = declared_in_table
        tail = ("" if declared_in_table
                else "；且族表缺族 %r 条目——已按 default 条目前缀判定" % family)
        super().__init__(
            "页面未呈现族 %r 的前缀特征（期望 class token 以 %s 开头）——换平台后等待/读取"
            "判据不可信（核对 components.json 的 family 声明与页面真实选择器）%s"
            % (family, self.prefixes, tail))


# 族前缀特征存在性（与 atlas.waits._FAMILY_PRESENT_JS 逐字等值，锁步测钉）：按 class token
# 边界判（token 以族前缀开头），**不用子串**——`model-x` 含 "el-" 子串但 token 不以 `el-`
# 开头，子串匹配会把非本族页面当本族（uf-1 要防的假绿）。
_FAMILY_PRESENT_JS = r"""(prefixes) => {
    const ps = (prefixes || []).map(p => String(p).replace(/^\\./, '')).filter(Boolean);
    if (!ps.length) return false;
    const nodes = document.querySelectorAll('[class]');
    for (let i = 0; i < nodes.length; i++) {
        const tokens = (nodes[i].getAttribute('class') || '').split(/\\s+/);
        for (let j = 0; j < tokens.length; j++) {
            for (let k = 0; k < ps.length; k++) {
                if (tokens[j].indexOf(ps[k]) === 0) return true;
            }
        }
    }
    return false;
}"""


def _family_present(page):
    """页面是否呈现族前缀特征（DOM 层）。取不到 evaluate 或抛错 → False（fail-closed）。"""
    ev = getattr(page, "evaluate", None)
    if ev is None:
        return False
    try:
        return bool(ev(_FAMILY_PRESENT_JS, list(UI_FAMILY_PREFIXES)))
    except Exception:
        return False


def _settle_mask(page, mask_selector, timeout_ms):
    """等 mask 落定；mask 缺席时先由族前缀特征确认页面确属本族（uf-1，模板与 waits 同构）。"""
    if not UI_FAMILY_HAS_MASK:
        return
    qs = getattr(page, "query_selector", None)
    present = None
    if qs is not None:
        try:
            present = qs(mask_selector)
        except Exception:
            present = None
    if present is not None:
        page.wait_for_selector(mask_selector, state="hidden", timeout=timeout_ms)
        return
    if not _family_present(page):
        raise UiFamilyMismatch(UI_FAMILY, UI_FAMILY_PREFIXES)


_TABLE_ROWS_JS = """(sel) => {
    const t = document.querySelector(sel);
    if (!t) return "__atlas_no_table__";
    const vm = t.__vue__;
    if (!vm) return "__atlas_no_vue__";
    if (!Array.isArray(vm.data)) return "__atlas_no_array__";
    return vm.data.length;
}"""

_VM_FIELD_JS = """(path) => {
    const root = document.querySelector('#app');
    if (!root) return "__atlas_no_app__";
    const vm = root.__vue__;
    if (!vm) return "__atlas_no_vue__";
    let v = vm;
    for (const key of path.split('.')) {
        if (v == null) return null;
        v = v[key];
    }
    return (v === null || v === undefined || v === '') ? null : v;
}"""


def wait_for_rows(page, table_selector: str = ".el-table", min_rows: int = 1,
                  timeout_ms: int = 10000, mask_selector: str = ".el-loading-mask") -> int:
    """等待表格查询**落定**并返回行数（读 vm.data，不靠 DOM 行数）。

    表格容器用 state="attached" 而非默认 visible——空结果时 .el-table 高度为 0 会被判
    hidden，「合法空表」第一步就超时（真实 Chrome 取证，waits.py 同款坑）。
    min_rows=0：只等落定、如实返回行数（「无来源单」这类业务事实不是超时）。
    evaluate 抛错分流：导航瞬态穿越 reload 下一拍再读；其余立即传播（#133）。
    mask 缺席时先用族签名判「未加载 vs 非本族」（uf-1）；读行数遇无 __vue__/data 非数组
    点名 RuntimeError（uf-2），绝不回落 0 伪装成「未达 N 行」。"""
    page.wait_for_selector(table_selector, state="attached", timeout=timeout_ms)
    _settle_mask(page, mask_selector, timeout_ms)
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            result = page.evaluate(_TABLE_ROWS_JS, table_selector)
        except Exception as exc:
            if not _is_transient(exc):
                raise
            page.wait_for_timeout(TICK_MS)
            continue
        if isinstance(result, str) and result.startswith("__atlas_"):
            if result == "__atlas_no_table__":
                count = 0
            else:
                raise RuntimeError(
                    "读取表格行数失败：页面非 Vue2 运行时或字段位置不同"
                    "（%s 的 __vue__.data 不可读，%s）——换族/换运行时后等待判据不可信（uf-2）"
                    % (table_selector, result))
        else:
            count = result or 0
        if count >= min_rows:
            return count
        page.wait_for_timeout(TICK_MS)
    raise TimeoutError(f"{table_selector} 在 {timeout_ms}ms 内未达 {min_rows} 行（mask 已落定）")


def wait_for_mask_gone(page, mask_selector: str = ".el-loading-mask", timeout_ms: int = 15000) -> None:
    """等待全屏 loading mask 消失（Element UI 的 .el-loading-mask 遮挡点击）。
    uf-1：mask 缺席时先由族签名确认页面确属本族（「无遮罩=未加载」才可信），签名缺席则
    UiFamilyMismatch，绝不静默秒回（`state="hidden"` 对不存在的选择器恒满足）。"""
    _settle_mask(page, mask_selector, timeout_ms)


def wait_for_vue_field(page, vm_path: str, timeout_ms: int = 10000):
    """等待 Vue 组件某个字段有值（非 null/undefined/空串）。vm_path 点路径，如 a.b.c。

    evaluate 抛错按 #133 分流：导航瞬态容忍重试，不可恢复错误立即传播；窗口耗尽是带
    归因的 TimeoutError 而不是裸崩。uf-2：`#app` 有而 `__vue__` 缺 → 点名 RuntimeError
    （非 Vue2 运行时），不回落 null 伪装「无值」；`#app` 自身缺席按未就绪重试。"""
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            value = page.evaluate(_VM_FIELD_JS, vm_path)
        except Exception as exc:
            if not _is_transient(exc):
                raise
            page.wait_for_timeout(TICK_MS)
            continue
        if isinstance(value, str) and value.startswith("__atlas_"):
            if value == "__atlas_no_app__":
                page.wait_for_timeout(TICK_MS)
                continue
            raise RuntimeError(
                "读取 Vue 字段 %s 失败：页面非 Vue2 运行时或字段位置不同"
                "（#app 无 __vue__，%s）——换运行时后取值判据不可信（uf-2）" % (vm_path, value))
        if value is not None:
            return value
        page.wait_for_timeout(TICK_MS)
    raise TimeoutError(f"Vue 字段 {vm_path} 在 {timeout_ms}ms 内无值")


def wait_for_url_change(page, timeout_ms: int = 10000) -> str:
    """等待 URL 发生变化（hash 路由跳转完成的信号）。

    F-4：轮询节拍用裸 TICK_MS——退避乘算会穿过「实参是裸标识符」这把扫描尺，
    真要退避先改判据再改代码，顺序不能反。page 访问抛错分流同 #133。"""
    old_url = page.url
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            current = page.url
        except Exception as exc:
            if not _is_transient(exc):
                raise  # page 已关闭时访问 .url 抛 Target closed：谎报「URL 未变化」
                       # 掩盖会话已死；导航瞬态才视为未变继续等
            current = old_url
        if current != old_url:
            return current
        page.wait_for_timeout(TICK_MS)
    raise TimeoutError(f"URL 在 {timeout_ms}ms 内未变化（仍为 {old_url}）")


# ---------------------------------------------------------------------------
# 浏览器矩阵（T5）：ATLAS_BROWSER_CHANNEL + ATLAS_BROWSER_EXECUTABLE
# 判据单点：本模块是取值域/家族映射的唯一实现位；引擎侧 config.py 用**同名键**
# `ATLAS_BROWSER_CHANNEL`（锁步测钉「键名同源」）。默认**不同**是有意取舍：产物默认
# chromium（自带、离线可跑，红线 #12「骨架可脱离 Atlas 运行」），引擎默认 chrome（复用
# 系统 Chrome）——两义都写在这里，别拿一边的默认去「对齐」另一边。
# ---------------------------------------------------------------------------

BROWSER_CHANNELS = ("chromium", "chrome", "firefox", "webkit")
DEFAULT_BROWSER_CHANNEL = "chromium"

# 无头启动禁用参数组（锁步 session.py:26-27 六项 + 旧套件同款）
_LAUNCH_DISABLE_ARGS = ("--disable-gpu", "--disable-dev-shm-usage", "--disable-crash-reporter",
                        "--disable-breakpad", "--no-first-run", "--disable-extensions")

# 通道 → playwright 浏览器**家族属性**名：chrome 是 chromium 家族的 channel="chrome"
# （复用系统 Chrome），不是另一只浏览器对象。
_CHANNEL_FAMILY = {"chromium": "chromium", "chrome": "chromium",
                   "firefox": "firefox", "webkit": "webkit"}


def browser_channel(env) -> str:
    """env → 归一化通道名（唯一判据位）。缺省/空白 → chromium（产物默认，自带可跑）；
    未知值 → EnvBlocked 点名通道名与可选值（不猜、不静默回退到 chromium 掩盖拼写错误）。"""
    env = env or {}
    raw = str(env.get("ATLAS_BROWSER_CHANNEL", "") or "").strip().lower()
    if not raw:
        return DEFAULT_BROWSER_CHANNEL
    if raw not in BROWSER_CHANNELS:
        raise EnvBlocked(
            f"未知 ATLAS_BROWSER_CHANNEL={raw!r}：可选 {'/'.join(BROWSER_CHANNELS)}"
            f"（默认 {DEFAULT_BROWSER_CHANNEL}）")
    return raw


def browser_family(channel: str) -> str:
    """通道名 → playwright 浏览器家族属性（chromium/firefox/webkit）。conftest 据此选
    launch 对象；未知通道原样返回（browser_channel 已在入口拦，不在此二次猜）。"""
    return _CHANNEL_FAMILY.get(channel, channel)


def missing_browser_hint(exc: BaseException, channel: str) -> str:
    """Playwright「缺浏览器可执行」错误 → 点名 `python -m playwright install` 指引；否则空串。

    只认 Playwright 缺包文案两形态（"Executable doesn't exist" / "playwright install"）；
    端口占用/权限/沙箱等**真实启动故障原样传播**——不给真故障套「缺浏览器」的皮（#102/#105
    假归因同族），也不静默降级去 launch 别的浏览器。"""
    text = _exception_text(exc)
    if "playwright install" in text or "Executable doesn't exist" in text:
        family = browser_family(channel)
        return (f"缺浏览器可执行：{channel}（{family}）未安装——运行 "
                f"`python -m playwright install {family}`，"
                "或设 ATLAS_BROWSER_EXECUTABLE 指向系统浏览器（Linux CI 先例）")
    return ""


def launch_kwargs(headless: bool, env=None) -> dict:
    """浏览器 launch 参数组。无头默认：slow_mo=0 + 六项禁用参数（固定 viewport 在
    new_context 侧，见 conftest）；有头：slow_mo=300 + --start-maximized。

    CDP_DEBUG=1 且有头时追加 --remote-debugging-port（冻场调试逃生舱，对齐 session.py:31-32）；
    ATLAS_BROWSER_EXECUTABLE 系统浏览器优先（Linux CI 下载不到 Playwright 自带包的先例）——
    显式 executable 时 channel 置 None（两者同传会被 Playwright 拒）。
    channel 键：chromium/其它家族 → None（chromium 走自带）；chrome → "chrome"（系统 Chrome 通道）。
    `env=None` 落 os.environ（存量调用逐字兼容 #115）；conftest 传快照 dict 供同判据面。"""
    env = os.environ if env is None else env
    headless = bool(headless)
    args = list(_LAUNCH_DISABLE_ARGS)
    if not headless:
        args.append("--start-maximized")
        if env.get("CDP_DEBUG") == "1":
            args.append("--remote-debugging-port=" + env.get("CDP_PORT", "9222"))
    kwargs = {"headless": headless, "slow_mo": 0 if headless else 300, "args": args}
    executable = str(env.get("ATLAS_BROWSER_EXECUTABLE", "") or "").strip()
    channel = browser_channel(env)
    if executable:
        kwargs["executable_path"] = executable
        kwargs["channel"] = None
    else:
        kwargs["channel"] = "chrome" if channel == "chrome" else None
    return kwargs



# ---------------------------------------------------------------------------
# plan_ctx 跨批上下文（F5：缺前置 = EnvBlocked 显式终止，绝不静默跳）
# ---------------------------------------------------------------------------


# plan_ctx 落盘标量上限（B-3.5）：超限=疑似 HTTP 正文/整页响应——拒收，值永不回显。
# F-27 §3 provenance（为什么 500 而不是并到 REASON_MAX_LEN=200）：两个常量管两种消费面，
# 失效形态不同——reason 面是**人读**的点名文案，截 200 后剩半句仍指路（Atlas dsl 先例）；
# plan_ctx 值是**机读**的回读数据（require_ctx → 后继请求参数），腰斩的半截单号=坏数据
# 继续往下跑（#124 谎报漂移同族），所以本层只能整键拒收、阈值语义完全不同，不该同值。
# 500 的取位：高于任何合法标量结论（单号/状态位/短文本）的量级，低于典型响应体的量级——
# 目标是拦「整页正文混进 ctx」，不是修剪长文案。改动只需动这一处（唯一实现位）。
SCALAR_MAX_LEN = 500


def safe_persist(key, value) -> bool:
    """plan_ctx 落盘标量闸（B-3.5 唯一判据位；save_plan_ctx 逐键调用）。

    放行：strip 后非空的 str、int、float。拒收（False + 一行 WARN，点名键、
    **值绝不回显**）：bool、容器、None、空/空白文本、超 `SCALAR_MAX_LEN` 长文本。
    动机（A.1 fetch 绕过面的落盘收口）：委托腿可能把响应正文/凭据文本带回 ctx——
    写进跨批 plan_ctx 等于把不可控文本第二次扩散；WARN 回显值等于把它抄进日志。
    bool 刻意拒（#117「非空标量才算」同纪律）：True 看着像前置结论，落盘读回却是
    字符串 "True"——文本语义漂移比拒绝更坏。前置标记请写 "1"/"ok" 这类标量。"""
    if isinstance(value, bool):
        print(f"WARN: plan_ctx 键 {key} 拒落盘：bool 非业务标量（前置标记写 1/ok 这类标量，不猜）")
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        if not value.strip():
            print(f"WARN: plan_ctx 键 {key} 拒落盘：空/空白文本不算有效结论")
            return False
        if len(value) > SCALAR_MAX_LEN:
            print(f"WARN: plan_ctx 键 {key} 拒落盘：文本超 {SCALAR_MAX_LEN} 字符"
                  "（疑似响应正文，值不回显）")
            return False
        return True
    print(f"WARN: plan_ctx 键 {key} 拒落盘：形态 {type(value).__name__} 非标量（值不回显）")
    return False


def load_plan_ctx(path) -> dict:
    """读跨批上下文。文件缺失/坏 JSON/顶层非映射 → 空 ctx 起点（坏数据点名不裸崩）。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"WARN: plan_ctx 读取失败（按空上下文起步）{p}: {type(exc).__name__}")
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        print(f"WARN: plan_ctx 非法 JSON（按空上下文起步）{p}: {type(exc).__name__}")
        return {}
    if not isinstance(data, dict):
        print(f"WARN: plan_ctx 顶层非映射（按空上下文起步）{p}")
        return {}
    return data


def _plan_ctx_lock_path(p: Path) -> Path:
    """plan_ctx 的独立锁文件（与 plan_ctx 同目录）。

    锁**不能**加在 plan_ctx.json 自身上：落盘走 tmp+os.replace，Windows 上目标文件存在任何
    打开 fd 时 os.replace 报 WinError 5（Atlas wave4 实测），而锁需要一个打开 fd。
    独立 .lock 旁路沿用同一把锁实现（_lock_exclusive，与 runs.jsonl 写手同款）。"""
    return p.with_name(p.name + ".lock")


def _exclusive_plan_lock(p: Path):
    """持有 plan_ctx 跨进程排他锁的上下文（线程内 _INPROC_LOCK + 跨进程 msvcrt/flock）。"""
    lock_path = _plan_ctx_lock_path(p)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(lock_path, flags, 0o644)

    class _Lock:
        def __enter__(self_inner):
            _INPROC_LOCK.acquire()
            _lock_exclusive(fd)
            return self_inner

        def __exit__(self_inner, *exc_info):
            try:
                _release_lock(fd)
            except OSError:
                pass   # 解锁失败不顶掉已落盘结论；锁随 fd 关闭必然释放
            finally:
                _INPROC_LOCK.release()
                os.close(fd)
            return False

    return _Lock()


def save_plan_ctx(path, ctx: dict, keys) -> None:
    """白名单落盘（先 .tmp 再 os.replace 原子换）：keys 列出**且过 safe_persist 标量闸**才写入。

    坏形态逐键拒收 + WARN（B-3.5），其余合法键照常落——一条坏值不得顶掉整批前置结论。

    cc-5 合并语义（wave4 第二批）：**读盘上已有内容并入**，再写回本次 keys 的合法键——
    旧实现只写本次 ctx 的键、不读盘，两个并行 worker 各自保存不同键时后写者整文件覆盖
    前者（实测 k_a 静默丢失、后继批次 require_ctx 报缺前置）。合并后：非本次 keys 的
    既有键原样保留；本次 keys 的键照旧由本次值决定（同键后写赢=与旧行为一致）；读盘
    也**在锁内**（读-合并-写整段进同一把跨进程锁），并行 worker 不再互相覆盖。
    读盘坏形态按 `load_plan_ctx` 同口径回落空起点（点名 WARN，不裸崩）。"""
    p = Path(path)
    selected = {}
    if isinstance(ctx, dict):
        for key in keys:
            if key in ctx and safe_persist(key, ctx[key]):
                selected[str(key)] = ctx[key]
    p.parent.mkdir(parents=True, exist_ok=True)
    # 不吞任何异常：OSError（磁盘/权限）照旧传播给调用方（conftest fixture 的
    # `except OSError` 兜底，绝不顶用例结论）——锁与合并只改并发语义，不改失败面。
    with _exclusive_plan_lock(p):
        merged = load_plan_ctx(p)      # 锁内读盘（含 bad-shape 的 WARN 回落）
        merged.update(selected)        # 本次 keys 的合法值覆盖同名旧值（同键后写赢）
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2, default=_safe_default),
                       encoding="utf-8")
        os.replace(tmp, p)


def require_ctx(ctx: dict, key: str):
    """前置键缺失 = EnvBlocked 显式终止（F5）。点名前置键与补救路径，绝不静默跳过——
    「数据缺失当没发生」会让后续用例拿着空值跑出一个假绿/假坏（同 Atlas FAQ 分批纪律）。"""
    if not isinstance(ctx, dict) or key not in ctx:
        raise EnvBlocked(
            f"缺前置键 {key}：先跑前置用例/勿删 plan_ctx.json（同 Atlas FAQ 分批纪律）")
    return ctx[key]


# ---------------------------------------------------------------------------
# 用例元数据标记（F8 写闸/runlog 的判据入口）
# ---------------------------------------------------------------------------


def fast_case(op: str, intent: str = ""):
    """给测试函数挂 __fast_case__ = {"op":..., "intent":...}。

    conftest 的 _write_gate 按 op=="write" 拦（默认不执行 body），_fast_runlog 按标记建行。
    op 必须非空——裸标记等于「闸不知道这是不是写」，不猜（红线 #7 反面）。"""
    op = str(op or "").strip()
    if not op:
        raise ValueError("fast_case 必须声明 op（write/ui/api/read…）")
    meta = {"op": op, "intent": str(intent or "")}

    def _decorator(func):
        func.__fast_case__ = meta
        return func

    return _decorator
'''


# ---------------------------------------------------------------------------
# 产物 conftest（F1/F6/F7/F8 + F-5/F-7：单浏览器/看门狗/写闸/runlog/两义分立）
# ---------------------------------------------------------------------------

_FAST_CONFTEST_SOURCE = '''"""生成候选（generator/emit_fast）：快模式 fixtures（单浏览器/单登录/看门狗/写闸/runlog）

Gap9 快模式 conftest（产物侧，不 import atlas 包；依赖栈 = pytest + playwright）。
机制对标旧套件权威实现（module 级单浏览器 + 登录指示器实查），并修正判据：

- **单浏览器**：sole_page(scope="module") 一条模块只 launch 一次；page/context/browser
  全部转发给它，任何用例不得自行 launch。pytest_configure 注销 pytest-playwright——
  插件自带 page fixture 会起第二个浏览器，与单浏览器口径冲突（F9）。
- **登录判定以页面实查为权威**：指示器元素存在=已登录，直接复用页面。
  plan_ctx["_logged_in_account"] 只做记录，**不做放行依据**——进程重启后浏览器是全新的，
  磁盘上的登录标记必须被实查否决（旧套件同实现，显式写出防误读）。
- **两义分立（F-7）**：配置缺失 → EnvBlocked("缺配置：<点名 env 键>")；登录动作未经人审
  接线 → NotWired("登录未接线：<点名组件/元素>")。求值次序固定：先查 env，再判接线；
  禁止「未接线或未配置」这种 or 文案——「去 .env 补键」和「等人写代码过人审」是两种下一步。
- **缺依赖/缺配置 = EnvBlocked 显式终止**：playwright 缺席、指示器未配、凭据未配都记
  env_blocked 行，绝不静默 skip 假绿（产物用例层禁裸 pytest.skip）。
- **会话通道二选一（gap9b B-3）**：判据唯一位在 _atlas_fast.session_channel（CDP 非空 ∧
  INTERACTIVE=="1" 在场声明 ∧ 非 prod- 前缀 → "attach"，否则 "launch"），这里只调不复制。
  attach=借用人已登录的目标页：按 ATLAS_FAST_ATTACH_MATCH 点名选页，**零 pages[0]/
  contexts[0] 兜底**（拿人的无关页开火比失败更坏；多命中同样点名拒绝，不猜标签页）；
  attach 实查未登录=EnvBlocked，**永不在人的浏览器里发登录动作**（#13）；teardown 只断连。
  CDP 在场+prod → 落 launch + WARN ATTACH-DISALLOWED-IN-PROD（不给强制开关）。
- **reason 边界（F-5）**：run 级 reason 只取框架点名文案（framework_reason 单点），
  其它异常文本（HTTP 正文/URL query）不落盘——产物里没有第二把净化刀，也不该有。

运行期 env 旋钮（全量清单见本文件顶部列表；生成器侧自证测 `emit_fast.ENV_KEYS` 穷举这些键，
产物内无独立 ENV_KEYS 常量）：
CASE_TIMEOUT / ATLAS_FAST_ENV / ATLAS_FAST_LOGIN_INDICATOR / ATLAS_FAST_ACCOUNT(→DEMO_USERNAME) /
ATLAS_FAST_PASSWORD(→DEMO_PASSWORD) / ATLAS_FAST_ALLOW_WRITES / ATLAS_RUNS_DIR /
ATLAS_BROWSER_EXECUTABLE / ATLAS_BROWSER_CHANNEL(T5 浏览器矩阵) / CDP_DEBUG / CDP_PORT /
ATLAS_FAST_BASE_URL(→ATLAS_BASE_URL) / ATLAS_FAST_CDP / ATLAS_FAST_INTERACTIVE /
ATLAS_FAST_ATTACH_MATCH / ATLAS_FAST_PARALLEL(T3 分片，>1 需 pytest-xdist) /
ATLAS_FAST_TRACE(T4 失败腿 trace.zip，默认关=零开销) /
ATLAS_FAST_TRACE_ACCEPT_SECRETS(T4 第二确认键：只给 TRACE=1 不开 trace 且 WARN；两键齐才开，
见 _atlas_fast.trace_enabled 注/红线 #2)。

F-11 留白（本波**不实现**，别顺手加）：产物不做「page.evaluate(fetch(...)) 发 API」通道——
那条路绕过 abilities.py 的 auth_expired 判据与净化刀，要接须先落判据再落代码。
"""
from __future__ import annotations

import _thread
import os
import threading
from pathlib import Path

import pytest

from _atlas_fast import (FLOW_STATUS_TIMEOUT, EnvBlocked, NotWired, RunWriter,
                         attach_denied_by_prod, browser_channel, browser_family,
                         classify_fail_class, current_page, framework_reason,
                         launch_kwargs, load_plan_ctx, missing_browser_hint,
                         parallel_setup_problem, parallel_workers, save_plan_ctx,
                         session_channel, set_current_page, start_tracing, stop_tracing,
                         trace_ack_warning, trace_enabled, trace_path, xdist_available)

HERE = Path(__file__).resolve().parent
RUNS_PATH = HERE / "reports" / "runs.jsonl"
PLAN_CTX_PATH = HERE / "reports" / "plan_ctx.json"


# ---------------------------------------------------------------------------
# 插件/选项防线（F1/F2/F9）
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    group = parser.getgroup("atlas-fast", "Atlas 快模式（gap9）")
    group.addoption("--headed", action="store_true", default=False,
                    help="有头跑浏览器（slow_mo=300；默认无头）")
    group.addoption("--headless", action="store_true", default=False,
                    help="显式无头（本就是默认值；提供与旧套件同款的显式开关）")


def pytest_configure(config):
    # 单浏览器防线（等价 -p no:playwright，但不依赖调用方传参；README 建议 ini 双保险）：
    # pytest-playwright 若在场会自带 page/browser fixture 再起一个浏览器。
    playwright_plugin = config.pluginmanager.getplugin("playwright")
    if playwright_plugin is not None:
        config.pluginmanager.unregister(playwright_plugin)
    # 并行分片依赖闸（T3，判据单点在 _atlas_fast）：声明了 ATLAS_FAST_PARALLEL>1 却缺
    # pytest-xdist → 点名报错，绝不静默单进程（缺依赖不降级）。坏旋钮值同样点名退出。
    try:
        workers = parallel_workers(dict(os.environ))
    except EnvBlocked as exc:
        raise pytest.UsageError(str(exc)) from exc
    problem = parallel_setup_problem(workers, xdist_available=xdist_available())
    if problem:
        raise pytest.UsageError(problem)
    # trace 双确认闸（T4/红线 #2）：只给 ATLAS_FAST_TRACE=1 缺 ACCEPT_SECRETS → trace 不开 +
    # 一行点名 WARN（缺第二键不静默生效）。会话级打一次。
    warn = trace_ack_warning(dict(os.environ))
    if warn:
        print(warn)


def _headless_decision(config) -> bool:
    """默认无头（F2）。CDP_DEBUG=1 强制有头（对齐 session.py:25）优先于命令行开关。"""
    if os.environ.get("CDP_DEBUG") == "1":
        return False
    try:
        if config.getoption("--headed"):
            return False
    except ValueError:
        pass  # 选项名被其他插件占用时按默认无头走，不猜
    return True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """收集 setup/call 阶段的第一个异常，供 _fast_runlog 判终态（teardown 自己的异常不算）。"""
    outcome = yield
    outcome.get_result()  # 报告照常产出，本钩子只留痕
    if call.excinfo is not None and call.when in ("setup", "call"):
        entries = getattr(item, "_fast_excinfos", None)
        if entries is None:
            entries = []
            item._fast_excinfos = entries
        entries.append((call.when, call.excinfo.value))
    # setup 期就挂、且 `_fast_runlog` **从未进过场** → 没有任何人在收尾时写行。
    # 这是 2026-11-17 实测的真空：`sole_page` 是 module 作用域，**比函数作用域的
    # `_fast_runlog` 更早建立**；它抛 EnvBlocked（缺 playwright / 缺浏览器 / attach 选页
    # 失败）时 `_fast_runlog` 压根没进 setup，收尾一刀没走 → runs.jsonl 文件都不存在。
    # （函数级 fixture 抛是另一回事：autouse 先于显式请求，所以那种会留行。）
    if (call.when == "setup" and call.excinfo is not None
            and not getattr(item, "_fast_runlog_entered", False)):
        _write_setup_failure_row(item, call.excinfo.value)


def _write_setup_failure_row(item, exc) -> None:
    """模块级 fixture 先挂时补一行——否则该用例在 runs.jsonl 里**完全缺席**。

    为什么必须补：`atlas summary` / `atlas allure` / `atlas report` 全靠 runs.jsonl 判
    「这条跑没跑、怎么挂的」。缺席会被读成「不存在」（无痕族 #106/#86）；无人值守时
    「跑过且挂了」就只活在会被轮转掉的终端日志里。gap9 F-33.4 ① 登记的就是这一格。

    纪律（三条，都别松）：
    - 状态词由 `_runlog_status_reason` **同一把定性刀**产出（不按异常文本猜，F-5/红线 #7）；
    - 落盘失败只 WARN，绝不顶用例结论（真因是 fixture 那个异常）；
    - 只在 `_fast_runlog` 未进场时写，避免与正常收尾重复落行。
    """
    case = f"{item.path.stem}::{item.originalname or item.name}"
    env = os.environ.get("ATLAS_FAST_ENV", "").strip() or "local"
    status, reason = _runlog_status_reason([("setup", exc)])
    try:
        writer = RunWriter(case, env, path=RUNS_PATH)
        writer.close(status, reason=reason)
    except OSError as write_exc:
        print(f"WARN: setup 期失败行落盘失败（不顶用例结论）: {type(write_exc).__name__}")


# ---------------------------------------------------------------------------
# 单浏览器 / 会话复用（F1/F2 + gap9b B-3 会话通道）
# ---------------------------------------------------------------------------


def _attach_pick_page(browser, match_value: str):
    """attach 点名匹配选页（B-3.1）：全部打开页中 URL 子串命中，**零兜底**。

    - 无 context → EnvBlocked 点名——不伪造 new_context（外部浏览器归人）；
    - 0 命中 → EnvBlocked 点名现有页数与 MATCH 值——util.py 的 ctx.pages[0] 兜底被
      契约明令弃用：拿人的无关页面开火比失败更坏；
    - ≥2 命中 → 同样 EnvBlocked：多标签页按位置取首个是 pages[0] 的换皮，不猜。
    """
    contexts = list(browser.contexts)
    if not contexts:
        raise EnvBlocked(
            f"attach 无可连上下文（contexts=0，MATCH={match_value}）：先确认人的浏览器已就绪")
    pages = []
    for ctx in contexts:
        pages.extend(list(ctx.pages))
    hits = [p for p in pages if match_value in str(p.url)]
    if not hits:
        raise EnvBlocked(
            f"attach 未找到目标页（MATCH={match_value}，现有 {len(pages)} 页无一命中）："
            "请人工打开目标页或改宽匹配值——不回落 pages[0]")
    if len(hits) > 1:
        raise EnvBlocked(
            f"attach MATCH={match_value} 同时命中 {len(hits)} 页：不猜标签页，"
            "请把匹配值收窄到唯一（多命中按位置取页=pages[0] 换皮，禁）")
    return hits[0]


def _attach_channel(pw, env_snapshot):
    """attach 通道（B-3.1）：借用人已打开的目标页——不 new_context/new_page（那是人的浏览器）。

    CDP 是 Chromium 家族专有，attach 通道恒走 chromium（与 browser_fam 解耦，不猜）。
    返回 (browser, context, page)；人为错误一律 EnvBlocked 点名（不兜 pages[0]）。
    """
    browser = pw.chromium.connect_over_cdp(
        env_snapshot.get("ATLAS_FAST_CDP", "").strip())
    match_value = os.environ.get("ATLAS_FAST_ATTACH_MATCH", "").strip()
    if not match_value:
        raise EnvBlocked(
            "attach 需 ATLAS_FAST_ATTACH_MATCH 点名目标页（URL 子串；"
            "业务域名只来自 env，模板零默认——红线 #11）")
    page = _attach_pick_page(browser, match_value)
    return browser, page.context, page


def _launch_channel(pw, browser_fam, headless, env_snapshot):
    """launch 通道（缺省）：自起浏览器 + 新上下文 + 新页（生命周期自理）。

    家族由 browser_family 判定（firefox/webkit 各自 launch 对象，其余走 chromium）。
    返回 (browser, context, page)；启动故障原样传播（真因不套皮，由 sole_page 归因）。
    """
    kwargs = launch_kwargs(headless, env_snapshot)
    if browser_fam == "firefox":
        browser = pw.firefox.launch(**kwargs)
    elif browser_fam == "webkit":
        browser = pw.webkit.launch(**kwargs)
    else:
        browser = pw.chromium.launch(**kwargs)
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080} if headless else None,
        accept_downloads=True)
    page = context.new_page()
    return browser, context, page


@pytest.fixture(scope="module")
def sole_page(request):
    """一条模块只 launch 一次 chromium（旧套件 conftest:134-180 的权威结构）。

    会话通道二选一（gap9b B-3，判据唯一位在 _atlas_fast.session_channel，这里只调不复制）：
    - "attach"：connect_over_cdp(ATLAS_FAST_CDP) → 按 ATLAS_FAST_ATTACH_MATCH 点名选页
      （零 pages[0]/contexts[0] 兜底）。语义前提=「有人看着这次运行」，需
      ATLAS_FAST_INTERACTIVE=="1" 在场声明；attach 的页归调试者所有，teardown 只断连
      （pw.stop），**绝不 close 人的 page/context/browser**（B-3.3）；
    - "launch"：缺省通道，launch+login，生命周期自理。
    CDP 在场 + prod- 前缀 → 落 launch + WARN ATTACH-DISALLOWED-IN-PROD（B-3.4，不给强制开关）。
    playwright 缺席 → EnvBlocked（UI 腿依赖栈=pytest+playwright；缺席记环境阻塞行，
    绝不在 collection 期 import 崩整场，也不假绿）。半启动失败尽力清理不泄漏驱动进程
    （#111 纪律），原始真因原样传播。
    浏览器矩阵（T5）：launch 家族由 _atlas_fast.browser_channel/browser_family 单点判定
    （默认 chromium；chrome→系统 Chrome channel；firefox/webkit 各自 launch 对象），
    判据先行于驱动 start——坏通道值在此点名；缺浏览器可执行 → EnvBlocked 点名
    `python -m playwright install`（真实启动故障原样传播，不套「缺浏览器」的皮）。

    ae-6 可读性：通道细节已拆进 _attach_channel()/_launch_channel()（行为不变）；
    本函数只保留「判据裁决 + 两通道分派 + 清理链」主干。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise EnvBlocked(
            "缺依赖：playwright 未安装——UI 腿依赖栈为 pytest+playwright（README F9）") from None
    headless = _headless_decision(request.config)
    env_snapshot = dict(os.environ)
    channel = session_channel(env_snapshot)
    if channel == "launch" and attach_denied_by_prod(env_snapshot):
        # F-27 §1（lead 裁）：意图被降级要说清降成了什么——三要素缺一即 WARN 失真：
        # ①已设 ATLAS_FAST_CDP ②ATLAS_FAST_ENV 为 prod- 前缀故拒 attach ③改走自起浏览器并用 env 凭据登录
        print("WARN: ATTACH-DISALLOWED-IN-PROD: 已设 ATLAS_FAST_CDP，但 ATLAS_FAST_ENV 为 "
              "prod- 前缀，本次改为自起浏览器并用 env 凭据登录"
              "（attach 恒禁生产，不给强制开关）")
    attached = channel == "attach"
    # 浏览器家族判据先行（T5）：坏通道值在驱动 start 之前点名（判据先于被判定对象）。
    browser_ch = browser_channel(env_snapshot)
    browser_fam = browser_family(browser_ch)
    pw = sync_playwright().start()
    browser = None
    context = None
    page = None
    try:
        if attached:
            browser, context, page = _attach_channel(pw, env_snapshot)
        else:
            browser, context, page = _launch_channel(pw, browser_fam, headless, env_snapshot)
    except BaseException as exc:
        # launch 通道自己开的才关；attach 半途失败时没开过任何东西（browser 是借的，只断连）
        if not attached:
            for obj in (context, browser):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass
        try:
            pw.stop()
        except Exception:
            pass
        # 缺浏览器可执行（T5）→ 点名 python -m playwright install；其余启动故障原样传播（#102）。
        hint = missing_browser_hint(exc, browser_ch)
        if hint:
            raise EnvBlocked(hint) from exc
        raise
    set_current_page(page)
    yield page
    errors = []
    if not attached:
        # launch 收尾关自己开的；attach 收尾只断连——close 人的页/上下文/浏览器=越权（B-3.3）
        for obj in (context, browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception as exc:
                    errors.append(exc)
    try:
        pw.stop()
    except Exception as exc:
        errors.append(exc)
    set_current_page(None)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise ExceptionGroup("sole_page 清理连环失败（已逐个尽力，不静默）", errors)


@pytest.fixture()
def page(sole_page):
    """转发给 sole_page——单浏览器权威，任何用例不得自行 launch。"""
    return sole_page


@pytest.fixture()
def context(page):
    return page.context


@pytest.fixture()
def browser(page):
    return page.browser


@pytest.fixture()
def plan_ctx():
    """跨批上下文（reports/plan_ctx.json）：读入 → 用例写键 → 白名单落盘。

    前置缺失的用例经 require_ctx 显式 EnvBlocked（缺键名进 reason），绝不静默跳过（F5）。

    私有键约定：以 `__` 开头的键**不落盘**（见下 `startswith("__")` 过滤）——想在本进程
    内跨用例传临时值又不写进 plan_ctx.json 时用它；单下划线（如底座的 `_logged_in_account`）
    不在此列、照常落盘（它是有意的「记录而非放行依据」标记）。"""
    ctx = load_plan_ctx(PLAN_CTX_PATH)
    yield ctx
    keys = [k for k in ctx if not str(k).startswith("__")]
    if not keys and not PLAN_CTX_PATH.exists():
        return
    try:
        save_plan_ctx(PLAN_CTX_PATH, ctx, keys)
    except OSError as exc:
        print(f"WARN: plan_ctx 落盘失败（不顶用例结论）: {type(exc).__name__}")


@pytest.fixture()
def get_page(sole_page, plan_ctx):
    """工厂：返回「已登录」页面。判定权威 = 页面指示器实查；磁盘标记只记录不放行。

    两义分立（F-7，先查 env 再判接线）：缺配置 → EnvBlocked("缺配置：…")；登录动作未
    人审接线 → NotWired("登录未接线：…")。attach 通道（B-3.2）：取的是「已登录 page」，
    实查不在场 = EnvBlocked——**attach 永不发登录动作**（在人的现用会话上写副作用=越权，
    #13 同理），判据位仍是同一条指示器实查，不复制第二套。launch 支 login 声称成功但
    指示器仍无 → 真失败（RuntimeError→failed），不给半登录态放行——实查不得被文案替代。"""

    def _get():
        page = sole_page
        indicator = os.environ.get("ATLAS_FAST_LOGIN_INDICATOR", "").strip()
        if not indicator:
            raise EnvBlocked(
                "缺配置：ATLAS_FAST_LOGIN_INDICATOR——登录判定无默认指示器"
                "（红线 #11：模板不含业务选择器，按环境人审提供）")
        set_current_page(page)
        if page.locator(indicator).count() > 0:
            return page  # 实查已登录：同账号会话直接复用（不看磁盘标记；attach/launch 同判据）
        if session_channel(dict(os.environ)) == "attach":
            raise EnvBlocked(
                "attach 会话未登录/已过期：请先人工登录"
                "（attach 模式不在人的浏览器里执行登录动作）")
        account = (os.environ.get("ATLAS_FAST_ACCOUNT", "").strip()
                   or os.environ.get("DEMO_USERNAME", "").strip())
        password = (os.environ.get("ATLAS_FAST_PASSWORD", "").strip()
                    or os.environ.get("DEMO_PASSWORD", "").strip())
        if not account or not password:
            raise EnvBlocked(
                "缺配置：ATLAS_FAST_ACCOUNT/DEMO_USERNAME 或 "
                "ATLAS_FAST_PASSWORD/DEMO_PASSWORD（凭据只从 env 读，红线 #1）")
        base_url = (os.environ.get("ATLAS_FAST_BASE_URL", "").strip()
                    or os.environ.get("ATLAS_BASE_URL", "").strip())
        if not base_url:
            raise EnvBlocked(
                "缺配置：ATLAS_FAST_BASE_URL（或 ATLAS_BASE_URL）——登录需要目标地址，"
                "业务 URL 不进模板（红线 #11）")
        try:
            import atlas_pages
        except ImportError:
            raise NotWired(
                "登录未接线：atlas_pages 模块缺失（组件库派生产物，需人审接 login）") from None
        login = getattr(atlas_pages, "login", None)
        if login is None:
            raise NotWired(
                "登录未接线：atlas_pages.login 缺失——需人审接入登录操作（不猜选择器、不假绿）")
        login(page, base_url=base_url, account=account, password=password)
        if page.locator(indicator).count() == 0:
            raise RuntimeError(
                "登录后指示器仍未出现：登录链路未生效（凭据/URL/选择器逐项核）")
        plan_ctx["_logged_in_account"] = account  # 只做记录，下次仍实查
        return page

    return _get


# ---------------------------------------------------------------------------
# 运行记录 / 看门狗 / 写闸（F6/F7/F8 + F-5）
# ---------------------------------------------------------------------------


def _runlog_status_reason(excinfos) -> tuple:
    """setup/call 收集到的异常 → (status, reason)。首个异常为准（setup 挂了 call 没跑）。

    reason 只经 framework_reason() 取框架点名文案（F-5）：断言消息/HTTP 正文/URL query
    不落 run 级 reason（断言细节走 step 名，以 "assert " 开头，allure 从 steps 取）。"""
    for when, exc in excinfos:
        if when not in ("setup", "call"):
            continue
        fail_class = classify_fail_class(exc)
        if fail_class == "env_blocked":
            return "env_blocked", framework_reason(exc)
        if fail_class == "waiting":
            return "waiting", framework_reason(exc)
        if fail_class == "interrupted":
            return "interrupted", ""
        return "failed", ""
    return "passed", ""


def _watchdog_timeout_reason(limit: float) -> str:
    """看门狗行的点名文案（框架常量模板，F-5 允许的唯一 watchdog 来源）。"""
    return f"watchdog CASE_TIMEOUT={limit:g}s"


@pytest.fixture(autouse=True)
def _fast_runlog(request):
    """一 test 一行 runs.jsonl（Atlas 兼容 schema，`atlas allure` 直接可吃，F7）。

    trace 归档（T4）：仅 ATLAS_FAST_TRACE=="1" 才 start_tracing——默认关使**成功腿零开销**
    （连 start 都不调，红线 #8 同族）；失败腿 stop_tracing(path=trace.zip) 落盘并把**路径**
    写进运行行；成功腿 stop(discard) 不产文件。凭据面见 _atlas_fast.trace_enabled 段注释
    （trace.zip 可能含网络载荷/fill 输入值——生产默认关、落 gitignore 内、勿外发）。"""
    case = f"{request.node.path.stem}::{request.node.originalname or request.node.name}"
    env = os.environ.get("ATLAS_FAST_ENV", "").strip() or "local"
    # 进场标记：makereport 据此判断「本用例的行是不是已经有人负责写」。
    # 置位后 setup 再失败也由本 fixture 的收尾统一落行（避免 makereport 重复写）。
    request.node._fast_runlog_entered = True
    writer = RunWriter(case, env, path=RUNS_PATH)
    tracing = start_tracing(current_page()) if trace_enabled(os.environ) else False
    yield writer
    status, reason = _runlog_status_reason(getattr(request.node, "_fast_excinfos", ()))
    if tracing:
        if status != "passed":
            tpath = trace_path(RUNS_PATH, writer.run_id)
            try:
                tpath.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            if stop_tracing(current_page(), tpath):
                writer.set_trace(tpath.resolve())
                writer.set_trace_secrets_ack(True)   # 红线 #2 双确认标记（可审计）
        else:
            stop_tracing(current_page())   # 成功腿丢弃缓冲区：不产文件、不写路径
    try:
        # close 幂等：看门狗已写 timeout 行时这里是 no-op——超时不许翻绿（F6③）
        writer.close(status, reason=reason)
    except OSError as exc:
        print(f"WARN: runs.jsonl 落盘失败（不顶用例结论）: {type(exc).__name__}")


@pytest.fixture(autouse=True)
def _case_watchdog(_fast_runlog):
    """用例级看门狗（F6，自研不引 pytest-timeout）：CASE_TIMEOUT 秒（默认 600）超时 →
    ①先落 timeout 行（此刻线程还活着）→②interrupt_main 让挂死调用带 traceback 浮出
    （旧套件 :106-107 实锤过 28 分钟无提示挂起）→③close 幂等使收尾不得改写该行。

    timeout **不映射** skipped/passed/interrupted——看护掐表不是「操作/环境终态」
    （failures 注记），Allure 侧读成 broken（fail-loud）。CASE_TIMEOUT<=0 关闭看护。"""
    writer = _fast_runlog
    raw = os.environ.get("CASE_TIMEOUT", "").strip() or "600"
    try:
        limit = float(raw)
    except ValueError:
        print(f"WARN: CASE_TIMEOUT={raw!r} 非数值，按默认 600s")
        limit = 600.0
    if limit <= 0:
        yield writer
        return

    def _fire():
        try:
            writer.close(FLOW_STATUS_TIMEOUT, reason=_watchdog_timeout_reason(limit))
        except OSError as exc:
            print(f"WARN: watchdog 落行失败: {type(exc).__name__}")
        _thread.interrupt_main()

    timer = threading.Timer(limit, _fire)
    timer.daemon = True
    timer.start()
    try:
        yield writer
    finally:
        timer.cancel()


@pytest.fixture(autouse=True)
def _write_gate(request, _fast_runlog):
    """写腿默认不执行 body（F8）：ATLAS_FAST_ALLOW_WRITES != "1" → EnvBlocked 点名旋钮。

    与 engine 把 PermissionError 归 env_blocked 完全同型（#114 面三）：策略阻挡不是代码
    缺陷；放行写=人的显式 env 决定（红线 #13 精神），机器永不自动放行。依赖 _fast_runlog
    确保 run 行已就位——被拦的写腿照常落一行 env_blocked+reason（点名 ATLAS_FAST_ALLOW_WRITES）。"""
    meta = getattr(request.function, "__fast_case__", None)
    if isinstance(meta, dict) and str(meta.get("op", "")) == "write":
        if os.environ.get("ATLAS_FAST_ALLOW_WRITES", "").strip() != "1":
            raise EnvBlocked(
                "写操作保护：ATLAS_FAST_ALLOW_WRITES 未置位（红线 #2/#7 策略阻挡，非缺陷）")
    yield
'''


def emit_fast_support_module() -> str:
    """产物底座 `_atlas_fast.py` 源码文本（确定性输出，无时间戳/随机）。"""
    return _FAST_SUPPORT_SOURCE


def emit_fast_conftest_module() -> str:
    """产物 `conftest.py` 源码文本（确定性输出）。"""
    return _FAST_CONFTEST_SOURCE


FAST_FIXTURE_NAMES = ("sole_page", "page", "context", "browser", "get_page",
                      "plan_ctx", "_case_watchdog", "_fast_runlog", "_write_gate")

# 产物运行期旋钮唯一清单（C.1 + lead 裁决 F-15）。None = 无默认（缺配即 EnvBlocked 或
# 走 launch 通道，红线 #11：模板不含业务字面量）；值列是回落默认。
# 「唯一清单」的兑现由 tests/test_emit_fast.py 的自证测钉死：模板内每一处
# os.environ 读取都必须落进本表（或外部回落名单 DEMO_USERNAME/DEMO_PASSWORD/
# ATLAS_BASE_URL），反向亦然——清单漏键=判据自破，多键=死旋钮，两头都红。
# 键数不设字面量：以本 dict 与模板实读的等值核对为准（F-15：写死数字本身就是下一处漂移）。
ENV_KEYS = {
    "CASE_TIMEOUT": "600",
    "ATLAS_FAST_ENV": "local",
    "ATLAS_FAST_LOGIN_INDICATOR": None,
    "ATLAS_FAST_ACCOUNT": "DEMO_USERNAME",
    "ATLAS_FAST_PASSWORD": "DEMO_PASSWORD",
    "ATLAS_FAST_ALLOW_WRITES": "0",
    "ATLAS_RUNS_DIR": None,
    "ATLAS_BROWSER_EXECUTABLE": None,
    "CDP_DEBUG": "0",
    "CDP_PORT": "9222",
    "ATLAS_FAST_BASE_URL": None,   # 登录目标地址，回落外部键 ATLAS_BASE_URL
    "ATLAS_FAST_CDP": None,        # F-11 attach 端点：None=缺省走 launch；置位≠attach（需在场闸）
    "ATLAS_FAST_INTERACTIVE": "0",  # B-3 在场声明闸：=="1" 才允许 attach（session_channel 单点）
    "ATLAS_FAST_ATTACH_MATCH": None,  # attach 选页 URL 子串（点名匹配，无 pages[0] 兜底）
    "ATLAS_BROWSER_CHANNEL": None,  # T5 浏览器矩阵：chromium/chrome/firefox/webkit（缺省 chromium）
    "ATLAS_FAST_PARALLEL": None,    # T3 分片：>1 需 pytest-xdist（缺依赖点名，绝不静默降级）
    "ATLAS_FAST_TRACE": "0",        # T4 trace：=="1" 才失败腿落 trace.zip（默认关=成功腿零开销）
    "ATLAS_FAST_TRACE_ACCEPT_SECRETS": "0",  # T4/红线#2 第二确认键：=="1" 才真开 trace（缺则 WARN）
}


def write_fast_artifacts(out_dir: Path, *, force: bool = False) -> list[str]:
    """写产物底座 + conftest（只写源码文本，绝不执行）。返回写入路径列表。

    冲突纪律（C.1）：目标文件已存在且**内容不同**且无 force → FastEmitError 点名冲突文件；
    内容相同视为幂等重写。存量坏字节按 replace 读入比对（#112：读崩不等于放行也不等于裸崩）。
    确定性：同输入两次调用产出的字节完全一致（模板为常量文本，无时间戳/随机/环境依赖）。
    """
    out_dir = Path(out_dir)
    targets = [
        (out_dir / f"{FAST_SUPPORT_MODULE_NAME}.py", _FAST_SUPPORT_SOURCE),
        (out_dir / "conftest.py", _FAST_CONFTEST_SOURCE),
    ]
    conflicts = []
    for path, text in targets:
        if not path.exists():
            continue
        try:
            existing = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            conflicts.append(path)
            continue
        if existing != text and not force:
            conflicts.append(path)
    if conflicts:
        raise FastEmitError(
            "目标文件已存在且内容不同（人审确认后加 force=True 覆盖）："
            + "、".join(str(p) for p in conflicts))
    # 写盘前严档自检（与 emit_pytest / emit_compat 同一口径，判据单点在
    # `dsl_utils.artifact_compile_problems`）：模板改成带插值后，非法转义（`\s` 这类）
    # 在默认档只是被忽略的 SyntaxWarning，产物会「写成功、一 import 就炸」。
    problems = artifact_compile_problems([(path.name, text) for path, text in targets])
    if problems:
        raise FastEmitError(
            "产物底座无法安全编译（拒绝写盘，零残留）：" + "；".join(problems))
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for path, text in targets:
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    return written


__all__ = ["FAST_SUPPORT_MODULE_NAME", "FastEmitError", "emit_fast_support_module",
           "emit_fast_conftest_module", "write_fast_artifacts", "FAST_FIXTURE_NAMES",
           "ENV_KEYS"]

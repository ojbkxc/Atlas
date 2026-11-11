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

纪律：
- 红线 #7：终态只有 RUN_STATUS_VOCAB 六值；缺配置/缺前置/未接线一律显式 EnvBlocked 终止，
  绝不静默跳过、绝不把失败映射成通过。`NotWired` 是「未经人审接线」唯一异常出口——
  归因只看类型（继承 EnvBlocked），不按消息文本/op 猜（那是给真缺陷开洗成 skipped 的门）。
- 红线 #11：不含具体业务字面量——URL/账号/登录指示器只从 env 读（无默认值即 EnvBlocked）。
- F3/F-3/F-4：等待一律语义轮询；每处 page.wait_for_timeout 的实参恰为裸标识符 TICK_MS
  （禁字面毫秒、禁对 tick 做算术、禁 time.sleep）。
- F4：成功步骤不截图；仅失败腿尽力而为补 step["attachment"]（绝对路径，Allure 直收）。
- F-5：run 级 reason 只经 framework_reason() 取框架点名文案（非空才写键、截 200）。
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

# ── 终态词汇（锁步 atlas.failures：env_blocked/interrupted/waiting ∈ FailureClass 值域，
#    timeout == failures.FLOW_STATUS_TIMEOUT；passed/failed 是 run 级终态非失败类）──────
FLOW_STATUS_TIMEOUT = "timeout"
RUN_STATUS_VOCAB = ("passed", "failed", "env_blocked", "interrupted", "waiting",
                    FLOW_STATUS_TIMEOUT)

ARTIFACTS_DIR_NAME = "artifacts"

# 语义等待轮询节奏（锁步 atlas.waits 的 timeout_ms // 100 tick）。
# F-3/F-4：这是全文件唯一允许的等待步长——page.wait_for_timeout 的实参必须是裸 TICK_MS。
TICK_MS = 100

# 导航瞬态判据（锁步 atlas.waits._TRANSIENT_MARKS）：只有这类抛错值得穿越 reload 继续等。
# 完整子串口径：navigating≠navigation；"detached" 兜 frame-detached 形态。
_TRANSIENT_MARKS = ("execution context was destroyed", "navigation", "detached")

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


# ── 会话通道判据单点（gap9b B-3；conftest 只调不复制）────────────────────────
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

    @property
    def closed(self) -> bool:
        return self._closed

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
            if reason:
                record["reason"] = str(reason)[:REASON_MAX_LEN]  # 非空才写键（缺省形态=存量）
            # default 兜底永不抛（#126 面B）：坏字段可以有噪音，不能丢整轮证据。
            line = json.dumps(record, ensure_ascii=False, default=_safe_default)
            _append_line_locked(self.path, (line + "\\n").encode("utf-8"))
            self._closed = True  # 仅在证据确实落盘后置位：写失败可重试不谎报
            return self.path


# ---------------------------------------------------------------------------
# 语义等待（waits.py 同源冻结派生版，F3；#133 瞬态分流）
# ---------------------------------------------------------------------------

_TABLE_ROWS_JS = """(sel) => {
    const t = document.querySelector(sel);
    if (!t) return 0;
    const vm = t.__vue__;
    if (!vm || !Array.isArray(vm.data)) return 0;
    return vm.data.length;
}"""

_VM_FIELD_JS = """(path) => {
    const root = document.querySelector('#app');
    const vm = root && root.__vue__;
    if (!vm) return null;
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
    evaluate 抛错分流：导航瞬态穿越 reload 下一拍再读；其余立即传播（#133）。"""
    page.wait_for_selector(table_selector, state="attached", timeout=timeout_ms)
    page.wait_for_selector(mask_selector, state="hidden", timeout=timeout_ms)
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            count = page.evaluate(_TABLE_ROWS_JS, table_selector) or 0
        except Exception as exc:
            if not _is_transient(exc):
                raise
            page.wait_for_timeout(TICK_MS)
            continue
        if count >= min_rows:
            return count
        page.wait_for_timeout(TICK_MS)
    raise TimeoutError(f"{table_selector} 在 {timeout_ms}ms 内未达 {min_rows} 行（mask 已落定）")


def wait_for_mask_gone(page, mask_selector: str = ".el-loading-mask", timeout_ms: int = 15000) -> None:
    """等待全屏 loading mask 消失（Element UI 的 .el-loading-mask 遮挡点击）。"""
    page.wait_for_selector(mask_selector, state="hidden", timeout=timeout_ms)


def wait_for_vue_field(page, vm_path: str, timeout_ms: int = 10000):
    """等待 Vue 组件某个字段有值（非 null/undefined/空串）。vm_path 点路径，如 a.b.c。

    evaluate 抛错按 #133 分流：导航瞬态容忍重试，不可恢复错误立即传播；窗口耗尽是带
    归因的 TimeoutError 而不是裸崩。"""
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            value = page.evaluate(_VM_FIELD_JS, vm_path)
        except Exception as exc:
            if not _is_transient(exc):
                raise
            page.wait_for_timeout(TICK_MS)
            continue
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
# 启动参数组（F2，锁步 session.py 六项 + 旧套件同款无头默认）
# ---------------------------------------------------------------------------

_LAUNCH_DISABLE_ARGS = ("--disable-gpu", "--disable-dev-shm-usage", "--disable-crash-reporter",
                        "--disable-breakpad", "--no-first-run", "--disable-extensions")


def launch_kwargs(headless: bool) -> dict:
    """chromium.launch 参数组。无头默认：slow_mo=0 + 六项禁用参数（固定 viewport 在
    new_context 侧，见 conftest）；有头：slow_mo=300 + --start-maximized。

    CDP_DEBUG=1 且有头时追加 --remote-debugging-port（冻场调试逃生舱，对齐 session.py:31-32）；
    ATLAS_BROWSER_EXECUTABLE 系统浏览器优先（Linux CI 下载不到 Playwright 自带包的先例）。"""
    headless = bool(headless)
    args = list(_LAUNCH_DISABLE_ARGS)
    if not headless:
        args.append("--start-maximized")
        if os.environ.get("CDP_DEBUG") == "1":
            args.append("--remote-debugging-port=" + os.environ.get("CDP_PORT", "9222"))
    kwargs = {"headless": headless, "slow_mo": 0 if headless else 300, "args": args}
    executable = os.environ.get("ATLAS_BROWSER_EXECUTABLE", "").strip()
    if executable:
        kwargs["executable_path"] = executable
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


def save_plan_ctx(path, ctx: dict, keys) -> None:
    """白名单落盘（先 .tmp 再 os.replace 原子换）：keys 列出**且过 safe_persist 标量闸**才写入。

    坏形态逐键拒收 + WARN（B-3.5），其余合法键照常落——一条坏值不得顶掉整批前置结论。"""
    p = Path(path)
    selected = {}
    if isinstance(ctx, dict):
        for key in keys:
            if key in ctx and safe_persist(key, ctx[key]):
                selected[str(key)] = ctx[key]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(selected, ensure_ascii=False, indent=2, default=_safe_default),
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

运行期 env 旋钮（全量以 _atlas_fast.ENV_KEYS 为准，唯一清单+自证测见 emit_fast）：
CASE_TIMEOUT / ATLAS_FAST_ENV / ATLAS_FAST_LOGIN_INDICATOR / ATLAS_FAST_ACCOUNT(→DEMO_USERNAME) /
ATLAS_FAST_PASSWORD(→DEMO_PASSWORD) / ATLAS_FAST_ALLOW_WRITES / ATLAS_RUNS_DIR /
ATLAS_BROWSER_EXECUTABLE / CDP_DEBUG / CDP_PORT / ATLAS_FAST_BASE_URL(→ATLAS_BASE_URL) /
ATLAS_FAST_CDP / ATLAS_FAST_INTERACTIVE / ATLAS_FAST_ATTACH_MATCH。

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
                         attach_denied_by_prod, classify_fail_class, framework_reason,
                         launch_kwargs, load_plan_ctx, save_plan_ctx, session_channel,
                         set_current_page)

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
    （#111 纪律），原始真因原样传播。"""
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
    pw = sync_playwright().start()
    browser = None
    context = None
    page = None
    try:
        if attached:
            # attach：只借用人已打开的目标页；不 new_context/new_page 造东西（那是人的浏览器）
            browser = pw.chromium.connect_over_cdp(
                env_snapshot.get("ATLAS_FAST_CDP", "").strip())
            match_value = os.environ.get("ATLAS_FAST_ATTACH_MATCH", "").strip()
            if not match_value:
                raise EnvBlocked(
                    "attach 需 ATLAS_FAST_ATTACH_MATCH 点名目标页（URL 子串；"
                    "业务域名只来自 env，模板零默认——红线 #11）")
            page = _attach_pick_page(browser, match_value)
            context = page.context
        else:
            browser = pw.chromium.launch(**launch_kwargs(headless))
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080} if headless else None,
                accept_downloads=True)
            page = context.new_page()
    except BaseException:
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

    前置缺失的用例经 require_ctx 显式 EnvBlocked（缺键名进 reason），绝不静默跳过（F5）。"""
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
    """一 test 一行 runs.jsonl（Atlas 兼容 schema，`atlas allure` 直接可吃，F7）。"""
    case = f"{request.node.path.stem}::{request.node.originalname or request.node.name}"
    env = os.environ.get("ATLAS_FAST_ENV", "").strip() or "local"
    writer = RunWriter(case, env, path=RUNS_PATH)
    yield writer
    status, reason = _runlog_status_reason(getattr(request.node, "_fast_excinfos", ()))
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
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for path, text in targets:
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    return written


__all__ = ["FAST_SUPPORT_MODULE_NAME", "FastEmitError", "emit_fast_support_module",
           "emit_fast_conftest_module", "write_fast_artifacts", "FAST_FIXTURE_NAMES",
           "ENV_KEYS"]

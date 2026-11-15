"""语义等待：替代硬 sleep，按业务条件轮询直到就绪或超时。

方案 L2 承诺「语义等待替代硬 sleep」。这些工具不依赖具体业务页面，
只依赖 Playwright page 的通用能力（DOM 查询、Vue 组件状态），
供 Task、DSL 和旧项目兼容层复用。

不引入 time.sleep：所有等待都用 page.wait_for_* 或 wait_for_timeout tick 轮询。
轮询时钟不依赖 page.evaluate("Date.now()")——reload/切 OU/切语言瞬间执行上下文
会被销毁，届时裸抛 Error 就不是「等待」而是崩溃；tick 计数由 wait_for_timeout
驱动墙钟推进，evaluate 单次抛错视为「页面尚在变动」，下一拍重试。

#133 轮询边界：「容忍重试」不能吞掉不可恢复错误——旧 except Exception 无差别重试，
页面/上下文/浏览器已关闭（Target closed）、JS 语法错、evaluate 内业务 JS 自抛的
TimeoutError 这类**永远不可能自愈**的抛错被烧满超时窗口后谎报「未达 N 行/无值」，
掩盖真因、拖慢排障，甚至让调用方把框架故障记成业务失败（#102 坏形态伪装合法结果
同族）。导航瞬态判据（Playwright 官方文案 "Execution context was destroyed" /
"navigation"）照常重试——这正是 reload 期要穿越的东西；其余抛错立即传播带归因。
控制流（Ctrl-C 等）不受影响：CONTROL_FLOW 不是 Exception，裸 except Exception
本就放行传播（#114 红线不变）。真实 Playwright 取证（2026-11-04 本地 Chrome）：
timeout=0 是「无超时」文档语义、负值由驱动归 1，底层不抛——窗口耗尽的 TimeoutError
契约由本模块自己保证（timeout_ms<=0 的调用方拿到的正是带归因的 TimeoutError，
探针实测），不伪造底层报错。

真实 flaky 兜底（对齐 UI AUTO TEST 的轮询重试语义）：`retry()` 是通用重试助手，
`is_recoverable()` 是「可重试/不可重试」判据的**单一真相**——只重试导航瞬态与交互类
失败，断言失败、业务/数据错误、契约违规、会话失效、环境阻塞、控制流一律不重试
（红线 #7：重试只兜 flaky，绝不把失败放宽成通过，否则是掩盖缺陷）。引擎的步骤级
重试与这里的判据同源，不留第二份名单（#92）。

**为什么本模块的表格/遮罩入参仍是 CSS 字符串，而不是 gap13 的结构化定位描述**：
`.el-table` / `.el-loading-mask` 是**框架结构类**（Element UI 的渲染约定），不是业务元素，
也没有稳定的无障碍名——用 `get_by_role` 定位不到，硬套「ARIA 优先」只会把能等的等待改成
等不到。业务元素的定位（可被 aria snapshot 复核、可进组件库人审）走 `atlas.locators`；
两者判据对象不同，不为好看而强行统一（红线 #12）。如实写在这里，而不是悄悄改成
`get_by_role` 再等真机才发现等不到。

**UI 族维度（uf-1，换平台静默失效）**：默认选择器字面量仍是 Element UI（存量逐字节不变
#115），但「无遮罩=未加载」这类乐观结论不再无条件成立——`wait_for_mask_gone`/`wait_for_rows`
在遮罩缺席时先用**族前缀特征**（DOM 层：页面存在 class token 以族前缀 `.el-` 开头的元素，
判据在 `ui_family.resolve_family`）确认页面确属本族，特征缺席则 `UiFamilyMismatch` 点名；
族有无遮罩概念由 `has_mask` 数据驱动。判据是**前缀存在性**而非固定签名选择器列表——纯展示/
详情页（只有 `.el-card`/`.el-row` 等）也算本族，不被误报；且按 class token 边界判，不用子串
（`model-x` 含 "el-" 子串不算本族）。换族页面通过显式 `family=<族名>` + 该族的选择器参数消费
同一份族表数据（不留第二份名单 #92）。及「读不到 Vue2 运行时」由点名 `RuntimeError` 显式化
（uf-2），不再把框架/运行时不匹配伪装成「未达 N 行 / 无值」（#102/#105 假绿同族）。
"""
from __future__ import annotations
import time
from typing import Any, Callable

from .failures import (CONTROL_FLOW, NEVER_AUTO_RETRY, FailureClass, classify_failure,
                       exception_text)
from .ui_family import UiFamilyMismatch, resolve_family

# 导航瞬态判据（Playwright 官方报错文案）：执行上下文因导航销毁、导航中止当前调用
# （"Execution context was destroyed, most likely because of a navigation" /
# "Navigating frame was detached"→ 文案含 "detached"）——这类抛错可能随页面就绪
# 消失，值得重试。注意判据子串用完整签名：navigating≠navigation（子串不匹配），
# 靠 "detached" 兜住 frame-detached 形态；Target closed/语法错/业务超时均不含三者。
_TRANSIENT_MARKS = ("execution context was destroyed", "navigation", "detached")

# 轮询节拍（pf-4）：语义等待的解析延迟上限 = 一个 tick。原内联 100ms 提为模块常量，
# 25ms 对齐 legacy_fast 有界等待垫片（gap11）——条件恰好在两拍之间满足时最多多等一个
# tick，降 tick 只降解析延迟上限（10s 窗口内从最多 100ms 降到 25ms），**不动忙判据**
# （#36：观测到忙才提前的闸一字不改）；代价是轮询次数 ×4（evaluate 调用密度）。
# emit_fast 产物模板的 TICK_MS 锁步等值（锁步测钉两边运行时值相等）。
TICK_MS = 25


def _is_transient(exc: BaseException) -> bool:
    """只重试「可能随页面就绪自愈」的导航瞬态；其余（Target closed/SyntaxError/
    evaluate 内业务 JS 自抛的超时）立即传播。旧实现 except Exception 无差别重试
    把**任何**异常（含 TimeoutError）都当瞬态烧满窗口谎报「无值/未达行数」，不可
    恢复故障伪装成业务性等待失败（#102 同族）。文本提取复用 `failures.exception_text`
    单一真相（#127/#129 纪律：坏 __str__ 不顶归因，不再手拼第三份 f-string）。"""
    text = exception_text(exc)
    return any(mark in text for mark in _TRANSIENT_MARKS)


def is_recoverable(exc: BaseException) -> bool:
    """「此异常值得重试」的**单一真相**（引擎步骤级重试与 retry() 同源，#92 不留第二份）。

    只认两类可能随重试自愈的失败：
    - 导航瞬态（`_is_transient`：reload/切 OU 期执行上下文销毁、frame detached）；
    - 交互类（`classify_failure` 判 INTERACTION：timeout / not visible / click /
      locator / intercept / element 等 UI 交互抖动）。

    其余一律 False——断言失败（ASSERTION）、业务/数据错误（DATA/CONTRACT）、会话失效
    （SESSION）、环境阻塞（ENV_BLOCKED）、无法归因（UNKNOWN）、控制流（CONTROL_FLOW）
    都不重试。**红线 #7**：断言失败重试不是「兜 flaky」而是「掩盖缺陷」——把一次真实
    失败重跑成通过，等于放宽断言。控制流（Ctrl-C/SystemExit/GeneratorExit）不是失败，
    更不该被重试吞掉（#114/#108）。
    C12-E 追加**类型级**硬闸：`failures.NEVER_AUTO_RETRY`（AwaitingHuman、
    DeliveryUncertain）无条件 False，且求值位先于文本瞬态判据——「等人」重跑等不来人，
    「投递不确定」重跑就是写操作双跑（正是 C12 存在的理由），两者的 reason/cause 文案
    都不该成为判据。
    """
    if isinstance(exc, CONTROL_FLOW):
        return False
    # C12-E：类型级拒绝先于任何文本判据（`_is_transient` 在 `classify_failure` 之前求值，
    # 文案里带 "navigation/detached" 的「等人」或「投递不确定」会被误判可重试）。
    # 判据来自 failures.NEVER_AUTO_RETRY 单点，本函数不抄名单（#92）。
    if isinstance(exc, NEVER_AUTO_RETRY):
        return False
    if _is_transient(exc):
        return True
    return classify_failure(exc) == FailureClass.INTERACTION


def retry(fn: Callable[[], Any], *, attempts: int = 1, interval_ms: float = 0.0,
          backoff: float = 1.0, sleep: Callable[[float], Any] | None = None,
          retry_on: Callable[[BaseException], bool] | None = None) -> Any:
    """通用重试助手：最多调用 fn `attempts` 次（attempts<=0 视同 1，即不重试）。

    仅在 fn 抛出满足 `retry_on`（默认 `is_recoverable`）的异常时重试；两次尝试之间等待
    `interval_ms` 毫秒，每次按 `backoff` 指数递增（backoff=1.0 为固定间隔）。**绝不吞异常**：
    不可重试的异常立即原样传播，重试耗尽也传播最后一次的原异常（红线 #7——重试只是给 flaky
    多一次机会，失败仍是失败，断言不被放宽）。

    控制流异常（CONTROL_FLOW）**无条件**立即传播，即使调用方传入一个恒 True 的 retry_on
    ——终止信号不是可重试失败（#114/#108 硬护栏，不依赖调用方正确性）。

    `sleep` 可注入（离线测试传假 sleep，零真实等待）；缺省 `time.sleep`。page 场景的等待
    仍应优先用 `wait_for_*`/`page.wait_for_timeout`（本助手是**通用**重试，不限于页面）。
    """
    total = attempts if isinstance(attempts, int) and attempts > 0 else 1
    predicate = is_recoverable if retry_on is None else retry_on
    sleeper = time.sleep if sleep is None else sleep
    delay = max(0.0, float(interval_ms)) / 1000.0
    for attempt in range(1, total + 1):
        try:
            return fn()
        except BaseException as exc:
            if isinstance(exc, CONTROL_FLOW):
                raise
            if attempt >= total or not predicate(exc):
                raise
            if delay > 0:
                sleeper(delay)
            delay *= backoff
    # 循环要么 return 要么 raise，理论不可达；保留以明示「绝不返回 None 冒充成功」。
    raise RuntimeError("retry: unreachable")   # pragma: no cover


_FAMILY_PRESENT_JS = r"""(prefixes) => {
    const ps = (prefixes || []).map(p => String(p).replace(/^\./, '')).filter(Boolean);
    if (!ps.length) return false;
    const nodes = document.querySelectorAll('[class]');
    for (let i = 0; i < nodes.length; i++) {
        const tokens = (nodes[i].getAttribute('class') || '').split(/\s+/);
        for (let j = 0; j < tokens.length; j++) {
            for (let k = 0; k < ps.length; k++) {
                if (tokens[j].indexOf(ps[k]) === 0) return true;
            }
        }
    }
    return false;
}"""


def _family_present(page, fam: dict) -> bool:
    """页面是否呈现该族的 class 前缀特征（DOM 层判据：这页是不是本族，Q2/uf-1）。

    按 **class token 边界**判（token 以族前缀开头），**不用子串匹配**——`model-x` / `panel-y`
    含 "el-" 子串但 token 不以 `el-` 开头，子串匹配（`[class*="el-"]`）会把非本族页面当本族
    （正是 uf-1 要防的假绿）。取不到 evaluate 或求值抛错 → 视为「无法确认」= False：
    **fail-closed**，宁可报族不匹配也不静默放行。
    """
    ev = getattr(page, "evaluate", None)
    if ev is None:
        return False
    try:
        return bool(ev(_FAMILY_PRESENT_JS, fam.get("prefixes") or []))
    except Exception:
        return False


def _settle_mask(page, mask_selector: str, fam: dict, timeout_ms: int) -> None:
    """等 loading mask 落定，并把「无 mask」与「页面非本族」分开（uf-1 单一实现）。

    - 族无遮罩概念（`has_mask` False）→ 无事可等，直接返回（数据驱动，非静默）；
    - 有遮罩概念时：mask **存在** → 正常等它消失（它存在本身即证页面是本族）；
      mask **不存在** → 先由族**前缀特征**确认页面确属本族（此时「无遮罩=未加载」才可信），
      特征缺席 → `UiFamilyMismatch` 点名——绝不静默把别的族当成「未加载」。
    """
    if not fam.get("has_mask", True):
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
    if not _family_present(page, fam):
        raise UiFamilyMismatch(fam["name"], fam.get("prefixes"),
                               fam.get("declared_in_table", True))


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
                  timeout_ms: int = 10000, mask_selector: str = ".el-loading-mask", *,
                  family: str | None = None) -> int:
    """等待表格查询**落定**并返回行数（读 vm.data，不靠 DOM 行数）。

    demo-012 坑是双向的：DOM 有行而 vm.data 未同步是旧数据，反过来
    「新查询未回、vm.data 还挂着上一次结果」时直接读行数会把 stale 当就绪。
    Element UI 的通用落定信号是 loading mask 消失（mask 不存在则立即视为落定），
    所以顺序固定为：表格出现 → mask 落定 → 读 vm 行数。

    - min_rows >= 1：等够行数，超时窗口内不达标 → TimeoutError；
    - min_rows = 0：只等落定，落定后如实返回行数——**合法空表**
      （如「无来源单」这类业务事实，probes 场景）是正常结果，
      旧实现把它当超时顶掉，或（min_rows=0 时）第一拍读 stale 数组立即误判。

    各阶段窗口均为 timeout_ms：正常路径（元素已在）零额外开销。
    `family`（uf-1）：换族页面显式给族名，mask 缺席时按该族签名判「未加载 vs 非本族」；
    缺省仍按通用包解析的族（正常安装=element-ui，逐字节旧行为 #115）。

    读取行数的 uf-2 分流：表格元素存在却无 `__vue__` / `vm.data` 非数组 → 点名
    `RuntimeError`（页面非 Vue2 运行时或字段位置不同），绝不回落 0 把运行时/族不匹配
    伪装成「未达 N 行」（#102/#105 假绿同族）；表格元素缺席仍按旧的「未落定」重试（导航
    期 DOM 重整），合法空表（data:[]）照常返回 0。

    真实浏览器取证（2026-11-04 本地 Chrome 冒烟）：表格容器用 state="attached" 而非默认
    visible——空结果时 .el-table 没有行、高度为 0，Playwright 判 hidden，等 visible 会让
    「合法空表」在第一步就超时，与 min_rows 契约自相矛盾。行数据是否存在由 vm.data 轮询判定。"""
    fam = resolve_family(family)
    page.wait_for_selector(table_selector, state="attached", timeout=timeout_ms)
    _settle_mask(page, mask_selector, fam, timeout_ms)
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            result = page.evaluate(_TABLE_ROWS_JS, table_selector)
        except Exception as exc:
            if not _is_transient(exc):
                raise   # Target closed/语法错/业务超时：重试永远不可能成功，
                        # 烧满窗口后谎报「未达 N 行」=框架故障伪装成等待失败（#133/#102）
            page.wait_for_timeout(TICK_MS)
            continue   # 页面正在跳转/重载：未落定，下一拍再读
        if isinstance(result, str) and result.startswith("__atlas_"):
            if result == "__atlas_no_table__":
                count = 0   # 表格在等待期间被移除/重渲染：视为未落定（旧行为，下一拍再读）
            else:
                raise RuntimeError(
                    f"读取表格行数失败：页面非 Vue2 运行时或字段位置不同"
                    f"（{table_selector} 的 __vue__.data 不可读，{result}）"
                    f"——换族/换运行时后等待判据不可信，请显式提供行数策略（uf-2）")
        else:
            count = result or 0
        if count >= min_rows:
            return count
        page.wait_for_timeout(TICK_MS)
    raise TimeoutError(f"{table_selector} 在 {timeout_ms}ms 内未达 {min_rows} 行（mask 已落定）")


def wait_for_mask_gone(page, mask_selector: str = ".el-loading-mask", timeout_ms: int = 15000, *,
                       family: str | None = None) -> None:
    """等待全屏 loading mask 消失。Element UI 的 .el-loading-mask 遮挡点击。

    uf-1：`wait_for_selector(state="hidden")` 对**不存在的选择器恒满足**——换族后旧实现静默
    秒回「已落定」，后续读 stale 数据（假绿/假红）。现 mask 缺席时先用族签名确认页面确属本族
    （「无遮罩=未加载」才可信），签名缺席 → `UiFamilyMismatch`。`family` 缺省按通用包解析的族。"""
    _settle_mask(page, mask_selector, resolve_family(family), timeout_ms)


def wait_for_vue_field(page, vm_path: str, timeout_ms: int = 10000) -> Any:
    """等待 Vue 组件某个字段有值（非 null/undefined/空串）。

    vm_path 用点路径，如 "saleOrderForm.paymentTerms"。
    从根 #app 的 __vue__ 开始取。evaluate 抛错（执行上下文销毁）容忍重试，
    窗口耗尽仍是 TimeoutError 而不是裸崩。

    uf-2 分流：`#app` 存在却无 `__vue__` → 页面非 Vue2 运行时（Vue3 挂 `__vue_app__`、
    非 Vue 框架无此属性）→ 点名 `RuntimeError`，绝不回落 null 把运行时错配伪装成「无值」
    （#102/#105）；`#app` 自身缺席按「未就绪」重试（导航期 DOM 重整，旧行为）。"""
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            value = page.evaluate(_VM_FIELD_JS, vm_path)
        except Exception as exc:
            if not _is_transient(exc):
                raise   # 同 wait_for_rows：不可恢复抛错不得烧窗口谎报「无值」（#133）
            page.wait_for_timeout(TICK_MS)
            continue
        if isinstance(value, str) and value.startswith("__atlas_"):
            if value == "__atlas_no_app__":
                page.wait_for_timeout(TICK_MS)
                continue   # #app 尚未挂载：未就绪，下一拍再读（导航期常态）
            raise RuntimeError(
                f"读取 Vue 字段 {vm_path} 失败：页面非 Vue2 运行时或字段位置不同"
                f"（#app 无 __vue__，{value}）——换运行时后取值判据不可信（uf-2）")
        if value is not None:
            return value
        page.wait_for_timeout(TICK_MS)
    raise TimeoutError(f"Vue 字段 {vm_path} 在 {timeout_ms}ms 内无值")


def wait_for_url_change(page, timeout_ms: int = 10000) -> str:
    """等待 URL 发生变化（hash 路由跳转完成的信号）。"""
    old_url = page.url
    for _ in range(max(1, timeout_ms // TICK_MS)):
        try:
            current = page.url
        except Exception as exc:
            if not _is_transient(exc):
                raise   # page 已关闭时访问 .url 抛 Target closed：谎报「URL 未变化」
                        # 掩盖会话已死（#133 同纪律）；导航瞬态才视为未变继续等
            current = old_url   # 极端瞬态：视为未变，继续等
        if current != old_url:
            return current
        page.wait_for_timeout(TICK_MS)
    raise TimeoutError(f"URL 在 {timeout_ms}ms 内未变化（仍为 {old_url}）")


def wait_for_dialog(page, dialog_selector: str = ".el-dialog__wrapper:visible",
                    timeout_ms: int = 10000) -> None:
    """等待弹窗出现。Element UI 弹窗 append 到 body，需 visible 过滤。"""
    page.wait_for_selector(dialog_selector, timeout=timeout_ms)


def wait_for_dialog_gone(page, dialog_selector: str = ".el-dialog__wrapper",
                         timeout_ms: int = 10000) -> None:
    """等待弹窗关闭。"""
    page.wait_for_selector(dialog_selector, state="hidden", timeout=timeout_ms)

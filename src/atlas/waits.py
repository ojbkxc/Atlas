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
"""
from __future__ import annotations
from typing import Any

from .failures import exception_text

# 导航瞬态判据（Playwright 官方报错文案）：执行上下文因导航销毁、导航中止当前调用
# （"Execution context was destroyed, most likely because of a navigation" /
# "Navigating frame was detached"→ 文案含 "detached"）——这类抛错可能随页面就绪
# 消失，值得重试。注意判据子串用完整签名：navigating≠navigation（子串不匹配），
# 靠 "detached" 兜住 frame-detached 形态；Target closed/语法错/业务超时均不含三者。
_TRANSIENT_MARKS = ("execution context was destroyed", "navigation", "detached")


def _is_transient(exc: BaseException) -> bool:
    """只重试「可能随页面就绪自愈」的导航瞬态；其余（Target closed/SyntaxError/
    evaluate 内业务 JS 自抛的超时）立即传播。旧实现 except Exception 无差别重试
    把**任何**异常（含 TimeoutError）都当瞬态烧满窗口谎报「无值/未达行数」，不可
    恢复故障伪装成业务性等待失败（#102 同族）。文本提取复用 `failures.exception_text`
    单一真相（#127/#129 纪律：坏 __str__ 不顶归因，不再手拼第三份 f-string）。"""
    text = exception_text(exc)
    return any(mark in text for mark in _TRANSIENT_MARKS)

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

    demo-012 坑是双向的：DOM 有行而 vm.data 未同步是旧数据，反过来
    「新查询未回、vm.data 还挂着上一次结果」时直接读行数会把 stale 当就绪。
    Element UI 的通用落定信号是 loading mask 消失（mask 不存在则立即视为落定），
    所以顺序固定为：表格出现 → mask 落定 → 读 vm 行数。

    - min_rows >= 1：等够行数，超时窗口内不达标 → TimeoutError；
    - min_rows = 0：只等落定，落定后如实返回行数——**合法空表**
      （如「无来源单」这类业务事实，probes 场景）是正常结果，
      旧实现把它当超时顶掉，或（min_rows=0 时）第一拍读 stale 数组立即误判。

    各阶段窗口均为 timeout_ms：正常路径（元素已在）零额外开销。

    真实浏览器取证（2026-11-04 本地 Chrome 冒烟）：表格容器用 state="attached" 而非默认
    visible——空结果时 .el-table 没有行、高度为 0，Playwright 判 hidden，等 visible 会让
    「合法空表」在第一步就超时，与 min_rows 契约自相矛盾。行数据是否存在由 vm.data 轮询判定。"""
    page.wait_for_selector(table_selector, state="attached", timeout=timeout_ms)
    page.wait_for_selector(mask_selector, state="hidden", timeout=timeout_ms)
    for _ in range(max(1, timeout_ms // 100)):
        try:
            count = page.evaluate(_TABLE_ROWS_JS, table_selector) or 0
        except Exception as exc:
            if not _is_transient(exc):
                raise   # Target closed/语法错/业务超时：重试永远不可能成功，
                        # 烧满窗口后谎报「未达 N 行」=框架故障伪装成等待失败（#133/#102）
            page.wait_for_timeout(100)
            continue   # 页面正在跳转/重载：未落定，下一拍再读
        if count >= min_rows:
            return count
        page.wait_for_timeout(100)
    raise TimeoutError(f"{table_selector} 在 {timeout_ms}ms 内未达 {min_rows} 行（mask 已落定）")


def wait_for_mask_gone(page, mask_selector: str = ".el-loading-mask", timeout_ms: int = 15000) -> None:
    """等待全屏 loading mask 消失。Element UI 的 .el-loading-mask 遮挡点击。"""
    page.wait_for_selector(mask_selector, state="hidden", timeout=timeout_ms)


def wait_for_vue_field(page, vm_path: str, timeout_ms: int = 10000) -> Any:
    """等待 Vue 组件某个字段有值（非 null/undefined/空串）。

    vm_path 用点路径，如 "saleOrderForm.paymentTerms"。
    从根 #app 的 __vue__ 开始取。evaluate 抛错（执行上下文销毁）容忍重试，
    窗口耗尽仍是 TimeoutError 而不是裸崩。"""
    for _ in range(max(1, timeout_ms // 100)):
        try:
            value = page.evaluate(_VM_FIELD_JS, vm_path)
        except Exception as exc:
            if not _is_transient(exc):
                raise   # 同 wait_for_rows：不可恢复抛错不得烧窗口谎报「无值」（#133）
            page.wait_for_timeout(100)
            continue
        if value is not None:
            return value
        page.wait_for_timeout(100)
    raise TimeoutError(f"Vue 字段 {vm_path} 在 {timeout_ms}ms 内无值")


def wait_for_url_change(page, timeout_ms: int = 10000) -> str:
    """等待 URL 发生变化（hash 路由跳转完成的信号）。"""
    old_url = page.url
    for _ in range(max(1, timeout_ms // 100)):
        try:
            current = page.url
        except Exception as exc:
            if not _is_transient(exc):
                raise   # page 已关闭时访问 .url 抛 Target closed：谎报「URL 未变化」
                        # 掩盖会话已死（#133 同纪律）；导航瞬态才视为未变继续等
            current = old_url   # 极端瞬态：视为未变，继续等
        if current != old_url:
            return current
        page.wait_for_timeout(100)
    raise TimeoutError(f"URL 在 {timeout_ms}ms 内未变化（仍为 {old_url}）")


def wait_for_dialog(page, dialog_selector: str = ".el-dialog__wrapper:visible",
                    timeout_ms: int = 10000) -> None:
    """等待弹窗出现。Element UI 弹窗 append 到 body，需 visible 过滤。"""
    page.wait_for_selector(dialog_selector, timeout=timeout_ms)


def wait_for_dialog_gone(page, dialog_selector: str = ".el-dialog__wrapper",
                         timeout_ms: int = 10000) -> None:
    """等待弹窗关闭。"""
    page.wait_for_selector(dialog_selector, state="hidden", timeout=timeout_ms)

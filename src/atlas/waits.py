"""语义等待：替代硬 sleep，按业务条件轮询直到就绪或超时。

方案 L2 承诺「语义等待替代硬 sleep」。这些工具不依赖具体业务页面，
只依赖 Playwright page 的通用能力（DOM 查询、Vue 组件状态），
供 Task、DSL 和旧项目兼容层复用。

不引入 time.sleep：所有等待都用 page.wait_for_* 或轮询 + page.expect_*。
"""
from __future__ import annotations
from typing import Any


def wait_for_rows(page, table_selector: str = ".el-table", min_rows: int = 1,
                  timeout_ms: int = 10000) -> int:
    """等待表格出现至少 min_rows 行数据（读 vm.data，不靠 DOM 行数）。

    DOM 有行但 vm.data 为空是异步未同步的典型坑（pitfalls demo-012），
    所以必须读 Vue 组件状态而非 DOM 行数。
    """
    page.wait_for_selector(table_selector, timeout=timeout_ms)
    deadline = page.evaluate("Date.now()") + timeout_ms
    while page.evaluate("Date.now()") < deadline:
        count = page.evaluate(
            """(sel) => {
                const t = document.querySelector(sel);
                if (!t) return 0;
                const vm = t.__vue__;
                if (!vm || !Array.isArray(vm.data)) return 0;
                return vm.data.length;
            }""", table_selector)
        if (count or 0) >= min_rows:
            return count or 0
        page.wait_for_timeout(100)
    raise TimeoutError(f"{table_selector} 在 {timeout_ms}ms 内未出现 {min_rows} 行数据")


def wait_for_mask_gone(page, mask_selector: str = ".el-loading-mask", timeout_ms: int = 15000) -> None:
    """等待全屏 loading mask 消失。Element UI 的 .el-loading-mask 遮挡点击。"""
    page.wait_for_selector(mask_selector, state="hidden", timeout=timeout_ms)


def wait_for_vue_field(page, vm_path: str, timeout_ms: int = 10000) -> Any:
    """等待 Vue 组件某个字段有值（非 null/undefined/空串）。

    vm_path 用点路径，如 "saleOrderForm.paymentTerms"。
    从根 #app 的 __vue__ 开始取。
    """
    deadline = page.evaluate("Date.now()") + timeout_ms
    while page.evaluate("Date.now()") < deadline:
        value = page.evaluate(
            """(path) => {
                const root = document.querySelector('#app');
                const vm = root && root.__vue__;
                if (!vm) return null;
                let v = vm;
                for (const key of path.split('.')) {
                    if (v == null) return null;
                    v = v[key];
                }
                return (v === null || v === undefined || v === '') ? null : v;
            }""", vm_path)
        if value is not None:
            return value
        page.wait_for_timeout(100)
    raise TimeoutError(f"Vue 字段 {vm_path} 在 {timeout_ms}ms 内无值")


def wait_for_url_change(page, timeout_ms: int = 10000) -> str:
    """等待 URL 发生变化（hash 路由跳转完成的信号）。"""
    old_url = page.url
    deadline = page.evaluate("Date.now()") + timeout_ms
    while page.evaluate("Date.now()") < deadline:
        current = page.url
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
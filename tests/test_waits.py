"""语义等待工具的离线测试：用 fake page 验证轮询逻辑，不依赖真实浏览器。"""
import pytest
from atlas.waits import (wait_for_rows, wait_for_mask_gone, wait_for_vue_field,
                         wait_for_url_change, wait_for_dialog, wait_for_dialog_gone)


class FakePage:
    """最小 fake：记录调用序列验证「先落定再读」；evaluate_errors 模拟执行上下文被销毁。"""
    def __init__(self, evaluations=None, url_sequence=None, evaluate_errors=0):
        self.evaluations = evaluations or []
        self.url_sequence = url_sequence or []
        self.evaluate_errors = evaluate_errors
        self.eval_index = 0
        self.eval_attempts = 0
        self.url_index = 0
        self.calls = []

    def wait_for_selector(self, selector, state=None, timeout=None):
        self.calls.append(("selector", selector, state))

    def wait_for_timeout(self, ms):
        self.calls.append(("tick", ms))

    @property
    def url(self):
        self.calls.append(("url",))
        if self.url_sequence and self.url_index < len(self.url_sequence):
            val = self.url_sequence[self.url_index]
            self.url_index += 1
            return val
        return "http://start"

    def evaluate(self, script, *args):
        self.calls.append(("evaluate",))
        self.eval_attempts += 1
        if self.eval_attempts <= self.evaluate_errors:
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
        if self.eval_index < len(self.evaluations):
            val = self.evaluations[self.eval_index]
            self.eval_index += 1
            return val
        return None


def test_wait_for_rows_returns_count_when_ready():
    page = FakePage(evaluations=[3])
    assert wait_for_rows(page, min_rows=2) == 3


def test_wait_for_rows_polls_until_ready():
    page = FakePage(evaluations=[0, 2])
    assert wait_for_rows(page, min_rows=2) == 2


def test_wait_for_rows_settles_before_counting():
    """必须先等 loading mask 落定再读行数：直接读会把上一次查询的 stale vm.data 当就绪
    （demo-012 坑的反向形态，模块 docstring 自己点名的双向异步）。"""
    page = FakePage(evaluations=[5])
    assert wait_for_rows(page, min_rows=1) == 5
    kinds = [(c[0], c[2] if len(c) > 2 else None) for c in page.calls]
    first_eval = next(i for i, c in enumerate(page.calls) if c[0] == "evaluate")
    mask = page.calls.index(("selector", ".el-loading-mask", "hidden"))
    assert mask < first_eval                      # mask 落定在首拍读行数之前
    # 真实 Chrome 取证：表格容器等 attached——空结果时 .el-table 高度为 0，
    # 默认等 visible 会让合法空表第一步就超时，与 min_rows 契约自相矛盾。
    assert page.calls[0] == ("selector", ".el-table", "attached")


def test_wait_for_rows_empty_result_returns_zero_not_timeout():
    """min_rows=0：合法空表（如「无来源单」业务事实）等落定后如实返回 0，
    旧实现要么恒等超时顶掉（min_rows≥1），要么第一拍读 stale 立即误判（min_rows=0）。"""
    page = FakePage(evaluations=[0])
    assert wait_for_rows(page, min_rows=0) == 0


def test_wait_for_mask_gone_does_not_raise():
    page = FakePage()
    wait_for_mask_gone(page)


def test_wait_for_vue_field_returns_value():
    page = FakePage(evaluations=[None, "NET30"])
    assert wait_for_vue_field(page, "saleOrderForm.paymentTerms") == "NET30"


def test_wait_for_vue_field_survives_context_destruction():
    """reload/切 OU 瞬间 evaluate 抛「上下文已销毁」：视为未落定重试，不得裸崩。"""
    page = FakePage(evaluations=["NET30"], evaluate_errors=1)
    assert wait_for_vue_field(page, "saleOrderForm.paymentTerms") == "NET30"


def test_wait_for_url_change_detects_change():
    # old_url 取 url_sequence[0]，循环取 url_sequence[1]
    page = FakePage(url_sequence=["http://start", "http://new"])
    result = wait_for_url_change(page)
    assert result == "http://new"


def test_wait_for_url_change_needs_no_evaluate_clock():
    """旧实现每拍先 page.evaluate("Date.now()")，导航期直接崩溃且 reload 无法穿越；
    新实现 tick 由 wait_for_timeout 驱动，全程不碰 evaluate 也能等到 URL 变化。"""
    page = FakePage(url_sequence=["http://start", "http://start", "http://done"],
                    evaluate_errors=10**9)   # 任何 evaluate 都炸
    assert wait_for_url_change(page, timeout_ms=1000) == "http://done"
    assert not any(c[0] == "evaluate" for c in page.calls)


def test_wait_for_rows_times_out():
    page = FakePage(evaluations=[])
    # 空表格永远不满足 min_rows=1，tick 窗口耗尽后仍报明确 TimeoutError
    with pytest.raises(TimeoutError):
        wait_for_rows(page, min_rows=1, timeout_ms=100)


def test_wait_for_dialog_calls_wait_for_selector():
    """wait_for_dialog 等待弹窗出现。"""
    page = FakePage()
    wait_for_dialog(page)  # 不抛即通过


def test_wait_for_dialog_gone_calls_wait_for_selector_hidden():
    """wait_for_dialog_gone 等待弹窗关闭。"""
    page = FakePage()
    wait_for_dialog_gone(page)  # 不抛即通过


class FakeErrorPage(FakePage):
    """evaluate 恒抛指定异常：区分「导航瞬态」与「不可恢复错误」的轮询分流取证。"""
    def __init__(self, exc):
        super().__init__()
        self._exc = exc

    def evaluate(self, script, *args):
        self.eval_attempts += 1
        raise self._exc


def test_wait_for_rows_target_closed_propagates_not_burns_window():
    """页面/上下文已关闭（Target closed）永远不可能自愈：旧实现 except Exception
    无差别重试烧满 10 拍后谎报「未达 1 行」——框架故障伪装成业务性等待失败（#133/#102），
    调用方把会话已死记成数据没出来。修复后立即传播原异常，只试一拍。"""
    page = FakeErrorPage(Exception("page.evaluate: Target page, context or browser has been closed"))
    with pytest.raises(Exception, match="has been closed"):
        wait_for_rows(page, min_rows=1, timeout_ms=5000)
    assert page.eval_attempts == 1          # 不烧窗口（修复前 50 拍）
    assert not any(c[0] == "tick" for c in page.calls)   # 一次都没多等


def test_wait_for_vue_field_business_timeout_not_swallowed():
    """evaluate 内业务 JS 自抛 TimeoutError：旧实现把它当导航瞬态吞掉重试，
    窗口耗尽谎报「无值」——真因（JS 自己超时）被掩埋。#133 同族：不可恢复的
    具体异常不得被泛化重试伪装成等待契约失败。"""
    page = FakeErrorPage(TimeoutError("业务 JS 轮询自身超时"))
    with pytest.raises(TimeoutError, match="业务 JS"):
        wait_for_vue_field(page, "saleOrderForm", timeout_ms=2000)
    assert page.eval_attempts == 1


def test_wait_for_rows_frame_detached_still_retries():
    """导航瞬态（frame detached）照常重试——正常路径不翻错（#115 教训）：
    reload 途中读行数抛 "Navigating frame was detached"，页面就绪后即恢复。"""
    page = FakeErrorPage(Exception("page.evaluate: Navigating frame was detached"))
    with pytest.raises(TimeoutError):      # 恒抛仍窗口耗尽 TimeoutError（等待契约不变）
        wait_for_rows(page, min_rows=1, timeout_ms=200)
    assert page.eval_attempts >= 2         # 确实重试过多拍，没被误判成不可恢复


def test_wait_for_url_change_closed_page_propagates():
    """页已关时访问 .url 抛 Target closed：旧实现折叠成「URL 未变化」烧完窗口
    谎报 TimeoutError——会话已死伪装成路由没跳完（#133）。"""
    class ClosedUrlPage(FakePage):
        @property
        def url(self):
            raise Exception("Page.url: Target page, context or browser has been closed")
    with pytest.raises(Exception, match="has been closed"):
        wait_for_url_change(ClosedUrlPage(), timeout_ms=200)

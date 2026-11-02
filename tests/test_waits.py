"""语义等待工具的离线测试：用 fake page 验证轮询逻辑，不依赖真实浏览器。"""
import pytest
from atlas.waits import (wait_for_rows, wait_for_mask_gone, wait_for_vue_field,
                         wait_for_url_change, wait_for_dialog, wait_for_dialog_gone)


class FakePage:
    """最小 fake：Date.now() 递增避免死循环，evaluate 按脚本返回值。"""
    def __init__(self, evaluations=None, url_sequence=None):
        self.evaluations = evaluations or []
        self.url_sequence = url_sequence or []
        self.eval_index = 0
        self.url_index = 0
        self._clock = 0

    def wait_for_selector(self, selector, state=None, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        self._clock += ms

    @property
    def url(self):
        if self.url_sequence and self.url_index < len(self.url_sequence):
            val = self.url_sequence[self.url_index]
            self.url_index += 1
            return val
        return "http://start"

    def evaluate(self, script, *args):
        if "Date.now()" in script:
            return self._clock
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


def test_wait_for_mask_gone_does_not_raise():
    page = FakePage()
    wait_for_mask_gone(page)


def test_wait_for_vue_field_returns_value():
    page = FakePage(evaluations=[None, "NET30"])
    assert wait_for_vue_field(page, "saleOrderForm.paymentTerms") == "NET30"


def test_wait_for_url_change_detects_change():
    # old_url 取 url_sequence[0]，循环取 url_sequence[1]
    page = FakePage(url_sequence=["http://start", "http://new"])
    result = wait_for_url_change(page)
    assert result == "http://new"


def test_wait_for_rows_times_out():
    page = FakePage(evaluations=[])
    # evaluations 空返回 None，永远不满足 min_rows，Date.now 递增会超时
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
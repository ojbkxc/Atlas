"""pf-4（wave4 C 轴）：waits.py 轮询节拍提为模块常量 TICK_MS。

判据面（离线 fake page，零网络零浏览器）：
- TICK_MS 常量存在（25ms 对齐 legacy_fast 垫片；只降解析延迟上限）
- wait_for_rows / wait_for_vue_field / wait_for_url_change 的每拍等待用 TICK_MS
  （fake page 记录 wait_for_timeout 实参）
- 语义不变：超时窗口拍数 = timeout_ms // TICK_MS；成功/超时/瞬态重试路径照旧
  （忙判据 #36 一字不动——本波只动节拍，不动判据）
- emit_fast 产物模板 TICK_MS 与 waits 锁步等值（值改动两边同步）
"""
from __future__ import annotations
import re
from pathlib import Path

from atlas import waits as waits_mod
from atlas.generator import emit_fast as FAST_EMIT_MODULE

_NAV_ERR = RuntimeError("execution context was destroyed, most likely because of a navigation")


class FakePage:
    """假 page：记录 wait_for_timeout 实参，驱动 evaluate/url 序列。"""

    def __init__(self, *, rows_seq=None, field_seq=None, url_seq=None,
                 transient_errors=None, mask_present=True):
        self.rows_seq = list(rows_seq or [])
        self.field_seq = list(field_seq or [])
        self.url_seq = list(url_seq or [])
        self.transient_errors = list(transient_errors or [])
        self.mask_present = mask_present
        self.timeouts = []

    def wait_for_selector(self, selector, state=None, timeout=None):
        return None

    def query_selector(self, selector):
        return object() if self.mask_present else None

    def evaluate(self, js, arg=None):
        if self.transient_errors:
            raise self.transient_errors.pop(0)
        if js == waits_mod._TABLE_ROWS_JS:
            return self.rows_seq.pop(0) if self.rows_seq else 0
        if js == waits_mod._VM_FIELD_JS:
            return self.field_seq.pop(0) if self.field_seq else None
        return True   # _FAMILY_PRESENT_JS（uf 族判据面）

    def wait_for_timeout(self, ms):
        self.timeouts.append(ms)

    @property
    def url(self):
        return self.url_seq.pop(0) if self.url_seq else "stable-url"


class TestTickMsConstant:
    def test_tick_ms_is_positive_int(self):
        assert isinstance(waits_mod.TICK_MS, int)
        assert waits_mod.TICK_MS > 0

    def test_tick_ms_value(self):
        """当前值 25ms（对齐 legacy_fast）；改值时同步本测与锁步测。"""
        assert waits_mod.TICK_MS == 25

    def test_tick_lockstep_with_emit_fast(self):
        """emit_fast 产物模板 TICK_MS 与 waits 锁步等值（值改动两边同步）。"""
        src = Path(FAST_EMIT_MODULE.__file__).read_text(encoding="utf-8")
        m = re.search(r"^TICK_MS = (\d+)", src, re.M)
        assert m, "emit_fast 模板内应有 TICK_MS 常量"
        assert int(m.group(1)) == waits_mod.TICK_MS


class TestWaitForRowsTick:
    def test_waits_use_tick_ms(self):
        """第一拍不达标 → 等一拍 → 第二拍达标：等待实参恰为 TICK_MS。"""
        page = FakePage(rows_seq=[0, 3])
        count = waits_mod.wait_for_rows(page, min_rows=1, timeout_ms=250)
        assert count == 3
        assert page.timeouts == [waits_mod.TICK_MS]

    def test_timeout_path_tick_count(self):
        """超时路径：拍数 = timeout_ms // TICK_MS，每拍等待 TICK_MS。"""
        page = FakePage(rows_seq=[0] * 12)
        try:
            waits_mod.wait_for_rows(page, min_rows=1, timeout_ms=250)
        except TimeoutError:
            pass
        else:
            raise AssertionError("应超时")
        assert len(page.timeouts) == 250 // waits_mod.TICK_MS
        assert set(page.timeouts) == {waits_mod.TICK_MS}

    def test_transient_retry_uses_tick_ms(self):
        """导航瞬态重试拍也用 TICK_MS（#133 分流路径）。"""
        page = FakePage(rows_seq=[5], transient_errors=[_NAV_ERR])
        count = waits_mod.wait_for_rows(page, min_rows=1, timeout_ms=250)
        assert count == 5
        assert page.timeouts == [waits_mod.TICK_MS]


class TestWaitForVueFieldTick:
    def test_waits_use_tick_ms(self):
        page = FakePage(field_seq=[None, "value"])
        value = waits_mod.wait_for_vue_field(page, "a.b", timeout_ms=250)
        assert value == "value"
        assert page.timeouts == [waits_mod.TICK_MS]

    def test_timeout_path_tick_count(self):
        page = FakePage(field_seq=[None] * 12)
        try:
            waits_mod.wait_for_vue_field(page, "a.b", timeout_ms=250)
        except TimeoutError:
            pass
        else:
            raise AssertionError("应超时")
        assert len(page.timeouts) == 250 // waits_mod.TICK_MS
        assert set(page.timeouts) == {waits_mod.TICK_MS}


class TestWaitForUrlChangeTick:
    def test_waits_use_tick_ms(self):
        page = FakePage(url_seq=["a", "a", "b"])
        result = waits_mod.wait_for_url_change(page, timeout_ms=250)
        assert result == "b"
        assert page.timeouts == [waits_mod.TICK_MS]

    def test_timeout_path_tick_count(self):
        page = FakePage(url_seq=["a"] * 13)
        try:
            waits_mod.wait_for_url_change(page, timeout_ms=250)
        except TimeoutError:
            pass
        else:
            raise AssertionError("应超时")
        assert len(page.timeouts) == 250 // waits_mod.TICK_MS
        assert set(page.timeouts) == {waits_mod.TICK_MS}

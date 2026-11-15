"""pf-3：autopilot `wait` 动作从固定 1000ms 睡 → 语义等待（复用 waits.py 单一判据）。

opt-in 边界：`action` **不写 target** = 旧行为逐字节不变（`page.wait_for_timeout(ms)`）；
写了 target 才走 waits 路径。判据全部在 `waits.*`（#92：autopilot 不复制第二把等待判据）。
"""
from __future__ import annotations

import pytest

from atlas import autopilot


class _RecordingPage:
    """记录 wait_for_timeout 调用，其它 playwright API 用 monkeypatch 替换。"""
    def __init__(self):
        self.url = "http://x/list"
        self.timeout_calls: list[int] = []

    def wait_for_timeout(self, ms):
        self.timeout_calls.append(ms)

    def aria_snapshot(self):
        return "- list"


# ---- opt-in 边界（负向对照）：不写 target = 旧行为逐字节不变 -------------------

def test_wait_without_target_keeps_fixed_sleep():
    """不写 target：仍走 `page.wait_for_timeout(ms)`（ms 缺省 1000）——旧路径零变化。"""
    page = _RecordingPage()
    action = {"action": "wait"}
    result = autopilot._execute(page, action, _Ctx())
    assert page.timeout_calls == [1000]
    assert result == {"action": "wait", "ms": 1000}


def test_wait_without_target_respects_ms():
    page = _RecordingPage()
    result = autopilot._execute(page, {"action": "wait", "ms": 250}, _Ctx())
    assert page.timeout_calls == [250]
    assert result["ms"] == 250


# ---- 语义路径：target 命中 → 委托 waits.py（单一判据）-------------------------

def test_wait_mask_gone_delegates_to_waits(monkeypatch):
    """target=mask_gone → waits.wait_for_mask_gone（page.wait_for_timeout 不被调用）。"""
    page = _RecordingPage()
    calls = []
    monkeypatch.setattr("atlas.waits.wait_for_mask_gone",
                        lambda p, timeout_ms, family=None: calls.append((p, timeout_ms, family)))
    result = autopilot._execute(
        page, {"action": "wait", "target": "mask_gone", "timeout_ms": 3000,
               "family": "element-ui"}, _Ctx())
    assert calls == [(page, 3000, "element-ui")]
    assert page.timeout_calls == []   # 不再无脑睡
    assert result["target"] == "mask_gone"


def test_wait_rows_delegates_to_waits(monkeypatch):
    page = _RecordingPage()
    calls = []
    monkeypatch.setattr("atlas.waits.wait_for_rows",
                        lambda p, table_selector, min_rows, timeout_ms, family=None,
                               mask_selector=".el-loading-mask":
                        calls.append((p, table_selector, min_rows, timeout_ms, family)))
    result = autopilot._execute(
        page, {"action": "wait", "target": "rows", "table": ".data-table",
               "min_rows": 5, "timeout_ms": 8000}, _Ctx())
    assert calls == [(page, ".data-table", 5, 8000, None)]
    assert result["target"] == "rows" and result["table"] == ".data-table"


def test_wait_rows_defaults_table_and_min_rows(monkeypatch):
    page = _RecordingPage()
    calls = []
    monkeypatch.setattr("atlas.waits.wait_for_rows",
                        lambda p, table_selector, min_rows, timeout_ms, family=None,
                               mask_selector=".el-loading-mask":
                        calls.append((table_selector, min_rows, timeout_ms)))
    autopilot._execute(page, {"action": "wait", "target": "rows"}, _Ctx())
    assert calls == [(".el-table", 1, 10000)]   # 缺省与 waits.wait_for_rows 默认一致


def test_wait_vue_field_delegates_to_waits(monkeypatch):
    page = _RecordingPage()
    calls = []
    monkeypatch.setattr("atlas.waits.wait_for_vue_field",
                        lambda p, vm_path, timeout_ms: calls.append((vm_path, timeout_ms)))
    result = autopilot._execute(
        page, {"action": "wait", "target": "vue_field", "vm_path": "$root.total",
               "timeout_ms": 5000}, _Ctx())
    assert calls == [("$root.total", 5000)]
    assert result["vm_path"] == "$root.total"


def test_wait_url_change_delegates_to_waits(monkeypatch):
    page = _RecordingPage()
    calls = []
    monkeypatch.setattr("atlas.waits.wait_for_url_change",
                        lambda p, timeout_ms: calls.append(timeout_ms))
    autopilot._execute(
        page, {"action": "wait", "target": "url_change", "timeout_ms": 7000}, _Ctx())
    assert calls == [7000]


# ---- 变异取证：target 写错必须当场点名（改坏必红）----------------------------

def test_wait_unknown_target_rejected():
    """未知 target 当场 ActionRejected（不静默回落固定睡）。"""
    page = _RecordingPage()
    with pytest.raises(autopilot.ActionRejected, match="mask_gone\\|rows"):
        autopilot._execute(
            page, {"action": "wait", "target": "nonsense"}, _Ctx())


class _Ctx:
    """最小 ctx 占位——wait 动作不读 ctx.values。"""
    def __init__(self):
        from types import SimpleNamespace
        self.values = SimpleNamespace()

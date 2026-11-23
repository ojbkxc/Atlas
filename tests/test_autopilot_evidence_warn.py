"""autopilot 的**证据链留痕**守卫：取证/决策落盘失败必须打一行 WARN，不许纯静默。

## 挡的是什么（2026-11-23 `review-code` 逐条核出的两处裸吞）

- `_wire_events`：`page.on("request"/"response")` 抛错时 `except Exception: pass`。后果是一整趟
  自主探索跑完**没有任何请求/响应快照**，而终端一个字都没有——事后查「为什么证据是空的」无迹可循
  （与「失败点漂移到不该漂的地方」同族）。
- `_record_decision`：`agent._record(...)` 抛错时同样 `pass`。后果是
  `reports/agent_decisions.jsonl` 少一条决策行，而「为什么走了这条动作」正是这份文件的唯一用途。

两处都**不该中止流程**（证据是旁路面，写盘失败不能顶掉动作结果），但必须留一行 WARN 并且
**过 `credential_gate.sanitize_text`**（异常文本可能带 URL/参数）。

## 三条

1. `test_wire_events_failure_warns`：`page.on` 抛错 → 不传播 + stderr 有 WARN + 含异常类型；
2. `test_record_decision_failure_warns`：`agent._record` 抛错 → 不传播 + stderr 有 WARN；
3. `test_warn_text_is_sanitized`：异常文本里的 `token=…` 在 WARN 里被抹掉（红线 #2 的终端面）。
"""
from __future__ import annotations

from atlas.autopilot import _record_decision, _wire_events
from atlas.agent import RiskLevel


class _BoomOnPage:
    def on(self, event, handler):
        raise RuntimeError("event api broken https://ops:S3cretPwd@intranet.corp/x?access_token=TOK9")


class _FineOnPage:
    def __init__(self):
        self.registered = []

    def on(self, event, handler):
        self.registered.append(event)


class _BoomAgent:
    def _record(self, decision):
        raise OSError("disk full at D:\\reports\\agent_decisions.jsonl")


class _Ctx:
    def __init__(self):
        self.events = []


def test_wire_events_failure_warns(capsys):
    _wire_events(_BoomOnPage(), _Ctx())
    captured = capsys.readouterr()
    assert "WARN" in captured.err and "证据挂载失败" in captured.err, captured.err
    assert "RuntimeError" in captured.err, captured.err


def test_wire_events_success_is_silent(capsys):
    page = _FineOnPage()
    _wire_events(page, _Ctx())
    assert page.registered == ["request", "response"]
    assert capsys.readouterr().err == ""


def test_record_decision_failure_warns(capsys):
    _record_decision(_BoomAgent(), "goal", "click", RiskLevel.READ_ONLY, True, "auto")
    captured = capsys.readouterr()
    assert "WARN" in captured.err and "决策留痕失败" in captured.err, captured.err
    assert "OSError" in captured.err, captured.err


def test_warn_text_is_sanitized(capsys):
    _wire_events(_BoomOnPage(), _Ctx())
    err = capsys.readouterr().err
    assert "TOK9" not in err and "S3cretPwd" not in err, f"WARN 文本没走净化刀：{err}"
    assert "<redacted>" in err, err

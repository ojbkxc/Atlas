"""真实 flaky 兜底（重试 + 超时 + 截图）的离线测试。

覆盖三块新能力：
1. `waits.retry` / `waits.is_recoverable`——通用重试助手与「可重试」判据单一真相；
2. engine 步骤级重试（仅只读 Task + 可恢复交互类失败）+ 用例级看门狗超时终态；
3. config 三个旋钮默认保守（重试 0 / 超时关闭）零回归 + env 解析与坏值点名。

全离线：重试用假 sleep（零真实等待）与计数替身；超时看门狗的逻辑用注入 on_timeout 验证，
「真投递 KeyboardInterrupt 打断挂死调用」另用真子进程验证（毫秒级 sleep 而非 mock——
被测对象就是「到点打断主线程」，mock 掉主线程就失去意义）。不碰真实浏览器。
"""
from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path

import pytest

from atlas import Actor, FunctionalTask, TaskMetadata, waits
from atlas.config import AtlasConfig
from atlas.engine import CaseWatchdog, ExecutionEngine

_SRC = str(Path(__file__).resolve().parents[1] / "src")


# ── 1. waits.retry: 通用重试助手语义 ────────────────────────────────────────

def test_retry_recovers_then_succeeds():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("元素未出现")
        return "ok"
    assert waits.retry(fn, attempts=5, interval_ms=0) == "ok"
    assert calls["n"] == 3


def test_retry_non_recoverable_called_once():
    """断言失败不是可恢复交互类：只调一次即传播（红线 #7）。"""
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise AssertionError("业务值不对")
    with pytest.raises(AssertionError):
        waits.retry(fn, attempts=5, interval_ms=0)
    assert calls["n"] == 1


def test_retry_control_flow_hard_guard_even_if_predicate_true():
    """控制流无条件立即传播——即使调用方传了恒 True 的 retry_on，
    也绝不重试 Ctrl-C（#114/#108 硬护栏，不依赖调用方正确性）。"""
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        waits.retry(fn, attempts=5, retry_on=lambda e: True, interval_ms=0)
    assert calls["n"] == 1


def test_retry_backoff_uses_injected_sleep():
    delays: list[float] = []
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 4:
            raise TimeoutError("t")
        return "ok"
    assert waits.retry(fn, attempts=4, interval_ms=100, backoff=2.0,
                       sleep=delays.append) == "ok"
    # 100ms -> 200ms -> 400ms（每次乘 backoff）；成功那次后不再睡
    assert delays == [0.1, 0.2, 0.4]


def test_retry_exhausted_reraises_last_exception():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise TimeoutError(f"t{calls['n']}")
    with pytest.raises(TimeoutError, match="t2"):
        waits.retry(fn, attempts=2, interval_ms=0)
    assert calls["n"] == 2


def test_retry_attempts_zero_is_single_call():
    """attempts<=0 视同 1：不重试，只调一次。"""
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise TimeoutError("t")
    with pytest.raises(TimeoutError):
        waits.retry(fn, attempts=0, interval_ms=0)
    assert calls["n"] == 1


def test_retry_success_first_call_no_sleep():
    delays: list[float] = []
    assert waits.retry(lambda: 42, attempts=3, interval_ms=100, sleep=delays.append) == 42
    assert delays == []


@pytest.mark.parametrize("builder,expected", [
    (lambda: TimeoutError("timeout"), True),
    (lambda: Exception("Execution context was destroyed, most likely because of a navigation"), True),
    (lambda: AssertionError("x"), False),
    (lambda: RuntimeError("查无数据"), False),
    (lambda: KeyboardInterrupt(), False),
])
def test_is_recoverable_table(builder, expected):
    assert waits.is_recoverable(builder()) is expected


# ── 2a. engine 步骤级重试 ────────────────────────────────────────────────────

def _enable_retry(monkeypatch, count, interval="0"):
    monkeypatch.setenv("ATLAS_STEP_RETRY", str(count))
    monkeypatch.setenv("ATLAS_STEP_RETRY_INTERVAL_MS", interval)


def test_engine_retries_recoverable_readonly_step(tmp_path, monkeypatch):
    """可恢复交互类失败 + 只读 Task：重试到成功，record 就地更正、产出照常写入。"""
    from atlas.recorder import RunRecorder
    _enable_retry(monkeypatch, 2)
    rec = RunRecorder("flaky", "sit", tmp_path / "runs.jsonl", "run")
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    calls = {"n": 0}
    def flaky(actor):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("元素未出现")
        return {"v": 1}
    task = FunctionalTask(TaskMetadata("demo", "read", produces=("v",)), flaky, "read")
    actor = Actor()
    outcome = engine.execute(actor, task)
    assert outcome.ok is True
    assert calls["n"] == 2
    assert actor.ctx["v"] == 1                       # 重试成功后产出正常写入
    step = rec.steps[0]
    assert step["ok"] is True and "fail_class" not in step   # 不残留 phantom 失败类
    assert step["retried"] == 1                      # 重试过可审计（#106）


def test_engine_default_no_retry_preserves_single_call(tmp_path):
    """零回归：默认 step_retry==0 时，可恢复失败也绝不重试（行为与旧实现一致）。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    calls = {"n": 0}
    def boom(actor):
        calls["n"] += 1
        raise TimeoutError("t")
    task = FunctionalTask(TaskMetadata("demo", "read"), boom, "read")
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)
    assert calls["n"] == 1


def test_engine_does_not_retry_write_step(tmp_path, monkeypatch):
    """写操作绝不自动重试（副作用双跑）：即使失败是可恢复交互类。"""
    _enable_retry(monkeypatch, 3)
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    calls = {"n": 0}
    def boom(actor):
        calls["n"] += 1
        raise TimeoutError("点击超时")
    task = FunctionalTask(TaskMetadata("demo", "save", read_only=False), boom, "save")
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)
    assert calls["n"] == 1


def test_engine_does_not_retry_assertion(tmp_path, monkeypatch):
    """断言失败绝不重试——重试不是「兜 flaky」是「掩盖缺陷」（红线 #7）。"""
    _enable_retry(monkeypatch, 3)
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    calls = {"n": 0}
    def boom(actor):
        calls["n"] += 1
        raise AssertionError("业务值不对")
    task = FunctionalTask(TaskMetadata("demo", "check"), boom, "check")
    with pytest.raises(AssertionError):
        engine.execute(Actor(), task)
    assert calls["n"] == 1


def test_engine_does_not_retry_data_failure(tmp_path, monkeypatch):
    """业务/数据错误（非交互类）不重试。"""
    _enable_retry(monkeypatch, 3)
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    calls = {"n": 0}
    def boom(actor):
        calls["n"] += 1
        raise RuntimeError("查无数据")
    task = FunctionalTask(TaskMetadata("demo", "read"), boom, "read")
    with pytest.raises(RuntimeError):
        engine.execute(Actor(), task)
    assert calls["n"] == 1


def test_engine_retry_exhausted_still_fails(tmp_path, monkeypatch):
    """重试耗尽仍是失败（不放宽断言）：调用 1+retries 次后如实抛最后一次，record 记失败。"""
    from atlas.recorder import RunRecorder
    _enable_retry(monkeypatch, 1)
    rec = RunRecorder("flaky", "sit", tmp_path / "runs.jsonl", "run")
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    calls = {"n": 0}
    def always(actor):
        calls["n"] += 1
        raise TimeoutError("超时")
    task = FunctionalTask(TaskMetadata("demo", "read"), always, "read")
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)
    assert calls["n"] == 2                            # 原始 1 次 + 重试 1 次
    assert rec.steps[0]["ok"] is False
    assert rec.steps[0]["fail_class"] == "interaction"


# ── 2b. engine 用例级超时看门狗 ──────────────────────────────────────────────

def test_case_watchdog_fires_on_timeout_when_not_done():
    fired: list[int] = []
    wd = CaseWatchdog(0.1, is_done=lambda: False,
                      on_timeout=lambda: fired.append(1), tick=0.02).start()
    deadline = time.monotonic() + 5
    while not fired and time.monotonic() < deadline:
        time.sleep(0.02)
    wd.stop()
    assert fired == [1] and wd.fired is True


def test_case_watchdog_silent_when_done():
    fired: list[int] = []
    state = {"done": False}
    wd = CaseWatchdog(0.1, is_done=lambda: state["done"],
                      on_timeout=lambda: fired.append(1), tick=0.02).start()
    state["done"] = True
    time.sleep(0.35)
    wd.stop()
    assert fired == []                                # 用例已结束 -> 绝不误伤后续代码


def test_case_watchdog_stop_prevents_fire():
    fired: list[int] = []
    wd = CaseWatchdog(0.3, is_done=lambda: False,
                      on_timeout=lambda: fired.append(1), tick=0.05).start()
    wd.stop()
    time.sleep(0.2)
    assert fired == []


def test_case_watchdog_disabled_when_timeout_nonpositive():
    wd = CaseWatchdog(0, is_done=lambda: False, on_timeout=lambda: None).start()
    assert wd._thread is None                         # 0/负值不启动（零回归）


def test_case_watchdog_real_interrupt_aborts_hung_call():
    """真子进程验证看门狗能及时**打断**主线程的挂起（对齐 UI AUTO TEST 机制）。

    注 1：中断在**下一个字节码边界**投递，故用活跃循环而非阻塞 sleep 作靶子（阻塞式
    C 呼叫只在返回字节码时响应——与 UI AUTO TEST _case_watchdog 一致的固有边界）。
    注 2：Windows/CPython 下 KeyboardInterrupt 的落地形态可能被 except 捕获（打印
    INTERRUPTED, rc 0）或以 SIGINT 退出码终止——两种都算「打断成功」。本测只钉真正的
    契约：**不空转到底**（命中 NO_INTERRUPT 才是失败）+ 远早于 15s 窗口结束。"""
    script = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import time\n"
        "from atlas.engine import CaseWatchdog\n"
        "CaseWatchdog(0.3, is_done=lambda: False, tick=0.05).start()\n"
        "try:\n"
        "    deadline = time.monotonic() + 15\n"
        "    while time.monotonic() < deadline:\n"
        "        pass\n"
        "    print('NO_INTERRUPT')\n"
        "except KeyboardInterrupt:\n"
        "    print('INTERRUPTED')\n"
    ) % (_SRC,)
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=25)
    elapsed = time.monotonic() - started
    assert "NO_INTERRUPT" not in proc.stdout     # 没被空转到窗口结束 -> 看门狗确实出手
    assert elapsed < 10                          # 0.3s 到点即断，不空转 15s


def test_engine_no_watchdog_by_default(tmp_path):
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    assert engine._watchdog is None                   # 默认 case_timeout=0 -> 不启动


def test_engine_marks_case_timeout_on_watchdog_interrupt(tmp_path, monkeypatch):
    """看门狗中断消息被标记 case_timeout，终态仍是 interrupted（不伪装 pass/failed）。
    真投递由 test_case_watchdog_real_interrupt_breaks_hung_call 覆盖；这里直接置位
    fired 验证 engine 的标记与终态路由。"""
    from atlas.recorder import RunRecorder
    monkeypatch.setenv("ATLAS_CASE_TIMEOUT", "1000")
    rec = RunRecorder("to", "sit", tmp_path / "runs.jsonl", "run")
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    assert engine._watchdog is not None
    engine._watchdog.fired = True                     # 模拟到点触发
    def halt(actor):
        raise KeyboardInterrupt()
    task = FunctionalTask(TaskMetadata("demo", "halt"), halt, "halt")
    with pytest.raises(KeyboardInterrupt):
        engine.execute(Actor(), task)
    engine.stop_case_guard()
    assert engine.results[0].failure_class == "interrupted"
    assert rec.steps[0]["fail_class"] == "interrupted"
    assert rec.steps[0].get("case_timeout") is True


# ── 3. config 旋钮：默认保守 + env 解析 + 坏值点名 ──────────────────────────

def test_flaky_config_defaults_are_conservative(tmp_path):
    cfg = AtlasConfig(tmp_path)
    assert cfg.step_retry == 0                        # 默认不重试
    assert cfg.case_timeout == 0                      # 默认关闭看门狗
    assert cfg.step_retry_interval_ms == 500


def test_flaky_config_reads_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STEP_RETRY", "3")
    monkeypatch.setenv("ATLAS_CASE_TIMEOUT", "600")
    monkeypatch.setenv("ATLAS_STEP_RETRY_INTERVAL_MS", "250")
    cfg = AtlasConfig(tmp_path)
    assert (cfg.step_retry, cfg.case_timeout, cfg.step_retry_interval_ms) == (3, 600, 250)


def test_flaky_config_rejects_malformed_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STEP_RETRY", "abc")
    with pytest.raises(ValueError, match="ATLAS_STEP_RETRY"):
        _ = AtlasConfig(tmp_path).step_retry


def test_flaky_config_rejects_negative_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_CASE_TIMEOUT", "-5")
    with pytest.raises(ValueError, match="ATLAS_CASE_TIMEOUT"):
        _ = AtlasConfig(tmp_path).case_timeout


def test_flaky_config_blank_value_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STEP_RETRY", "   ")
    assert AtlasConfig(tmp_path).step_retry == 0

"""wave5 backlog：en-11 `api_evidence` 只读 GET 重放生产侧（engine 失败路径）。

红线 #9：写任务绝不注册重放 handler（副作用双跑）；handler 本体 fail-safe
（#89 纪律：定性器绝不能先于被定性对象死）。负向对照：写任务 / UI 通道 / 无 api
Ability 都不注册，旧行为逐字节不变。
"""
from __future__ import annotations

import pytest

from atlas import Actor, Channel, FunctionalTask, TaskMetadata
from atlas.engine import ExecutionEngine
from atlas.failures import FailureClass


class _FakeResponse:
    def __init__(self, status=200, ok=True, data=None):
        self.status = status
        self.ok = ok
        self.data = data if data is not None else {"resultCode": "0"}
        self.url = "http://x/api"


class _FakeApi:
    """替身 CallApi：记录 get() 调用并回放预置响应；抛错形态可注入。"""
    def __init__(self, response=None, exc=None):
        self.calls: list[tuple] = []
        self._response = response or _FakeResponse()
        self._exc = exc

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self._exc:
            raise self._exc
        return {"ok": self._response.ok, "status": self._response.status,
                "data": self._response.data, "error": ""}


class _PathApi:
    """按路径回放不同响应的替身：记录每次 get() 的 path（重放归因断言用）。

    每个 path 的值是 (status, ok, data)；未登记的路径回落 200/ok。
    """
    def __init__(self, responses: dict[str, tuple[int, bool, object]] | None = None):
        self.calls: list[str] = []
        self._responses = responses or {}

    def get(self, path, **kwargs):
        self.calls.append(path)
        status, ok, data = self._responses.get(path, (200, True, {"resultCode": "0"}))
        return {"ok": ok, "status": status, "data": data, "error": ""}


class _NullRecorder:
    def step(self, *a, **kw):
        from contextlib import nullcontext
        return nullcontext({})

    def close(self, *a, **kw):
        pass


class _NullConfig:
    env = "test"
    reports_dir = None
    mode = "run"
    case_timeout = 0
    prod_write_allowed = False

    def validate_write(self, r):
        return None

    step_retry = 0
    step_retry_interval_ms = 0
    root = None


def _engine(actor, case_id="en11"):
    recorder = _NullRecorder()
    return ExecutionEngine(_NullConfig(), recorder=recorder,
                           case_id=case_id, session=None,
                           contracts=None, knowledge=None,
                           healer=None, journal=None,
                           evidence=actor.evidence)


def _api_read_task(op="/orders"):
    def _perform(actor):
        raise RuntimeError("业务失败：读超时")
    return FunctionalTask(
        TaskMetadata("demo", "read_order", read_only=True, channel=Channel.API,
                     api_operation=op),
        _perform, "read_order")


def _api_write_task(op="/orders"):
    def _perform(actor):
        raise RuntimeError("业务失败：写超时")
    return FunctionalTask(
        TaskMetadata("demo", "write_order", read_only=False, channel=Channel.API,
                     api_operation=op),
        _perform, "write_order")


def _ui_read_task():
    def _perform(actor):
        raise RuntimeError("业务失败：UI 超时")
    return FunctionalTask(
        TaskMetadata("demo", "ui_read", read_only=True, channel=Channel.UI),
        _perform, "ui_read")


class TestReadonlyReplayHandler:
    """en-11：engine 失败路径内置只读 GET 重放，接通 rich 分类。"""

    def test_readonly_api_task_registers_handler_and_classifies_session(self):
        """只读 API 任务失败 → 重放拿到 401 → SESSION 归类（rich 分类生效）。"""
        api = _FakeApi(_FakeResponse(status=401, ok=False,
                                     data={"message": "会话失效"}))
        actor = Actor()
        actor.can(api)
        actor.evidence = __import__("atlas.session", fromlist=["ApiEvidence"]).ApiEvidence()
        eng = _engine(actor)
        try:
            eng.execute(actor, _api_read_task())
            raise SystemExit("should have raised")
        except RuntimeError:
            pass
        assert len(api.calls) == 1
        assert api.calls[0][0] == "/orders"
        failure_class = eng.results[0].failure_class
        assert failure_class == FailureClass.SESSION.value, \
            f"重放拿到 401 应归 SESSION，实际 {failure_class}"

    def test_readonly_api_task_replay_5xx_lands_env_blocked(self):
        """重放拿到 502 → ENV_BLOCKED（不是 unknown，接通 rich 分类）。"""
        api = _FakeApi(_FakeResponse(status=502, ok=False, data={"err": "bad gateway"}))
        actor = Actor()
        actor.can(api)
        from atlas.session import ApiEvidence
        actor.evidence = ApiEvidence()
        eng = _engine(actor)
        try:
            eng.execute(actor, _api_read_task())
            raise SystemExit("should have raised")
        except RuntimeError:
            pass
        assert eng.results[0].failure_class == FailureClass.ENV_BLOCKED.value

    def test_replay_network_failure_does_not_mask_original(self):
        """重放自身网络崩 → handler 返回 None，原异常原样传播（#89 纪律）。"""
        api = _FakeApi(exc=ConnectionError("网络断了"))
        actor = Actor()
        actor.can(api)
        from atlas.session import ApiEvidence
        actor.evidence = ApiEvidence()
        eng = _engine(actor)
        try:
            eng.execute(actor, _api_read_task())
            raise SystemExit("should have raised")
        except ConnectionError:
            raise SystemExit("重放异常不应该顶掉原异常")
        except RuntimeError as exc:
            assert "读超时" in str(exc)
        # 重放确实尝试过但失败了
        assert len(api.calls) == 1
        # 定性回落到 classify_failure（unknown / interaction / data 按文本走）
        assert eng.results[0].failure_class is not None

    def test_write_task_never_registers_handler(self):
        """红线 #9：写任务绝不注册重放 handler——api.get 不被调用。"""
        api = _FakeApi()
        actor = Actor()
        actor.can(api)
        from atlas.session import ApiEvidence
        actor.evidence = ApiEvidence()
        eng = _engine(actor)
        try:
            eng.execute(actor, _api_write_task())
            raise SystemExit("should have raised")
        except RuntimeError:
            pass
        assert api.calls == [], "写任务绝不能触发重放"

    def test_ui_channel_task_never_registers_handler(self):
        """UI 通道（无 api_operation）不注册——UI 任务没有可重放的接口位。"""
        api = _FakeApi()
        actor = Actor()
        actor.can(api)
        from atlas.session import ApiEvidence
        actor.evidence = ApiEvidence()
        eng = _engine(actor)
        try:
            eng.execute(actor, _ui_read_task())
            raise SystemExit("should have raised")
        except RuntimeError:
            pass
        assert api.calls == []

    def test_no_api_ability_does_not_crash(self):
        """Actor 没 api Ability → 跳过注册，失败照常走 classify_failure。"""
        actor = Actor()   # 无 CallApi
        from atlas.session import ApiEvidence
        actor.evidence = ApiEvidence()
        eng = _engine(actor)
        try:
            eng.execute(actor, _api_read_task())
            raise SystemExit("should have raised")
        except RuntimeError as exc:
            assert "读超时" in str(exc)
        assert eng.results[0].failure_class is not None

    def test_positive_replay_does_not_override_non_api_failure(self):
        """读任务返回成功但原异常是业务断言 → 重放只发生在失败路径。"""
        api = _FakeApi(_FakeResponse(status=200, ok=True, data={"ok": True}))
        actor = Actor()
        actor.can(api)
        from atlas.session import ApiEvidence
        actor.evidence = ApiEvidence()
        eng = _engine(actor)
        # 顺利通过的任务：读一下、没抛错
        task = FunctionalTask(
            TaskMetadata("demo", "read_ok", read_only=True, channel=Channel.API,
                         api_operation="/health", produces=("ok",)),
            lambda a: {"ok": True}, "read_ok")
        eng.execute(actor, task)
        assert api.calls == [], "成功路径不触发重放"
        assert eng.results[0].ok is True


def _ok_api_read_task(op, name="read_ok"):
    return FunctionalTask(
        TaskMetadata("demo", name, read_only=True, channel=Channel.API,
                     api_operation=op),
        lambda a: None, name)


def _fail_api_read_task(op, name="read_fail"):
    def _perform(actor):
        raise RuntimeError("业务失败：读超时")
    return FunctionalTask(
        TaskMetadata("demo", name, read_only=True, channel=Channel.API,
                     api_operation=op),
        _perform, name)


class TestPerStepReplayIsolation:
    """P0 回归：失败归因只用「当前步」的重放结果（旧实现 handler 累积、首个命中）。"""

    def _actor(self, api):
        from atlas.session import ApiEvidence
        actor = Actor()
        actor.can(api)
        actor.evidence = ApiEvidence()
        return actor

    def test_second_step_failure_replays_second_step_only(self):
        """两步 API flow：第一步 /a 成功、第二步 /b 抛错且重放 500。

        旧实现：/a 的 handler 先注册，第二步失败时先重放 /a（200）→ 归 interaction。
        修复后：只用本步 /b 的重放（500）→ env_blocked，且请求只有 /b。
        """
        api = _PathApi({"/a": (200, True, {"resultCode": "0"}),
                        "/b": (500, False, {"err": "bad gateway"})})
        actor = self._actor(api)
        eng = _engine(actor)
        eng.execute(actor, _ok_api_read_task("/a", "read_a"))
        with pytest.raises(RuntimeError):
            eng.execute(actor, _fail_api_read_task("/b", "read_b"))
        # 重放只针对当前步的 /b（不是第一步的 /a）
        assert api.calls == ["/b"], f"重放应只请求 /b，实际 {api.calls}"
        assert eng.results[-1].failure_class == FailureClass.ENV_BLOCKED.value, \
            f"重放 /b 得 500 应归 env_blocked，实际 {eng.results[-1].failure_class}"

    def test_handler_does_not_accumulate_across_steps(self):
        """多步执行后 handler 槽不随步数增长（防累积回归）。"""
        api = _PathApi({"/step1": (200, True, {}), "/step2": (200, True, {}),
                        "/step3": (200, True, {}), "/step4": (200, True, {}),
                        "/step5": (200, True, {}), "/boom": (500, False, {})})
        actor = self._actor(api)
        eng = _engine(actor)
        for i in range(1, 6):
            eng.execute(actor, _ok_api_read_task(f"/step{i}", f"step{i}"))
        # 追加链保持为空（engine 走单槽替换，不再 append）；槽恒为一个可调用
        assert actor.evidence._handlers == [], \
            f"追加链不应累积，实际 {len(actor.evidence._handlers)} 个"
        assert callable(actor.evidence._step_replay)
        with pytest.raises(RuntimeError):
            eng.execute(actor, _fail_api_read_task("/boom", "boom"))
        # 失败步只重放一次（不是 5 步累积的多次重放）
        assert api.calls[-1] == "/boom"
        assert api.calls.count("/boom") == 1

    def test_second_flow_failure_does_not_replay_first_flow_request(self):
        """两 flow 串行（同一 engine/evidence）：flow2 失败不重放 flow1 的请求。

        旧实现 flow1 的 handler 残留在链里，flow2 失败时按注册顺序先重放 flow1 的接口。
        修复后每步起点先清槽，flow2 失败只重放本 flow 当前步的 /c。
        """
        api = _PathApi({"/a": (200, True, {}), "/c": (500, False, {"err": "gateway"})})
        actor = self._actor(api)
        eng = _engine(actor)
        # flow1：只有 /a，成功
        eng.execute(actor, _ok_api_read_task("/a", "flow1_read_a"))
        # flow2：/c 失败——不得回放 flow1 的 /a
        with pytest.raises(RuntimeError):
            eng.execute(actor, _fail_api_read_task("/c", "flow2_read_c"))
        assert "/a" not in api.calls[1:], f"flow2 失败不得重放 flow1 的 /a，实际 {api.calls}"
        assert api.calls[-1] == "/c"
        assert eng.results[-1].failure_class == FailureClass.ENV_BLOCKED.value

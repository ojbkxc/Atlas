"""wave5 backlog：en-12 负向断言 / en-15 跨模块对账 / en-10 外部事件等待（DSL 侧）。

全部 opt-in：不写 `raises` / `reconcile` / `await` 键 = 旧行为逐字节不变（负向对照测在
每个条目下都有）。判据单点：`failures.raise_type` / `failures.reconcile_diff` /
`failures.ReconciliationMismatch`（dsl 只消费、不复制第二把尺，#92）。
"""
from __future__ import annotations

import pytest

from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.dsl import FlowCompiler, FlowValidationError, TaskRegistry
from atlas.failures import (AwaitingHuman, FailureClass, ReconciliationMismatch,
                            raise_type, reconcile_diff)


# ---- 共用小工具 ---------------------------------------------------------------

def _throwing_task(exc: BaseException | type, *, name="boom", read_only=True):
    def _perform(actor):
        if isinstance(exc, type):
            raise exc(f"业务失败：{name}")
        raise exc
    return FunctionalTask(TaskMetadata("demo", name, read_only=read_only),
                          _perform, name)


def _value_task(**values):
    def _perform(actor):
        return values
    return FunctionalTask(TaskMetadata("demo", "set", produces=tuple(values)),
                          _perform, "set")


def _registry(*tasks) -> TaskRegistry:
    reg = TaskRegistry()
    for task in tasks:
        reg.register(task.name, lambda _t=task, **kw: _t)
    return reg


# ==== en-12：assert 块/步骤级 `raises` 负向断言 =================================

class TestRaisesNegativeAssertion:
    """en-12：期待被测步骤抛指定异常；未抛/抛别的 = 断言失败不洗绿。"""

    def test_raises_hit_passes_step(self):
        """步骤抛 raises.type 指定的异常 → 本步通过（异常被吞）。"""
        reg = _registry(_throwing_task(ValueError("boom")))
        actor = Actor()
        result = FlowCompiler(reg).run(
            {"steps": [{"task": "boom", "raises": {"type": "ValueError"}}]}, actor)
        assert result["status"] == "passed"

    def test_raises_miss_no_raise_fails_loud(self):
        """步骤正常返回（没抛） → AssertionError，flow 终态 failed（不洗绿）。"""
        reg = _registry(_value_task(x=1))
        actor = Actor()
        with pytest.raises(AssertionError, match="期望抛出 ValueError"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "set", "raises": {"type": "ValueError"}}]}, actor)

    def test_raises_wrong_type_propagates_original(self):
        """抛了别的异常 → 原异常原样传播（不吞、不翻成 AssertionError），归因指真因。"""
        reg = _registry(_throwing_task(TimeoutError("真实超时")))
        with pytest.raises(TimeoutError, match="真实超时"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "boom", "raises": {"type": "ValueError"}}]}, Actor())

    def test_raises_match_text_mismatch_fails(self):
        """命中异常类型但文本不匹配 /regex/ → AssertionError（不洗绿）。"""
        reg = _registry(_throwing_task(ValueError("业务失败：库存不足")))
        with pytest.raises(AssertionError, match="文本不匹配"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "boom",
                            "raises": {"type": "ValueError", "match": "库存超限"}}]}, Actor())

    def test_raises_match_text_hit_passes(self):
        """命中异常类型且文本匹配 → 通过。"""
        reg = _registry(_throwing_task(ValueError("业务失败：库存不足")))
        result = FlowCompiler(reg).run(
            {"steps": [{"task": "boom",
                        "raises": {"type": "ValueError", "match": "库存不足"}}]}, Actor())
        assert result["status"] == "passed"

    def test_raises_subclass_hit(self):
        """raises: OSError 命中 TimeoutError（子类）——与 Python except 同语义。"""
        reg = _registry(_throwing_task(TimeoutError("t")))
        result = FlowCompiler(reg).run(
            {"steps": [{"task": "boom", "raises": {"type": "OSError"}}]}, Actor())
        assert result["status"] == "passed"

    def test_raises_control_flow_always_propagates(self):
        """Ctrl-C（CONTROL_FLOW）恒穿透——终止信号不是「期望的抛」（#108/#114 同纪律）。"""
        def _ki(actor):
            raise KeyboardInterrupt()
        reg = TaskRegistry()
        reg.register("ki", lambda: FunctionalTask(
            TaskMetadata("demo", "ki", read_only=True), _ki, "ki"))
        with pytest.raises(KeyboardInterrupt):
            FlowCompiler(reg).run(
                {"steps": [{"task": "ki", "raises": {"type": "KeyboardInterrupt"}}]}, Actor())

    def test_raises_bad_type_name_compile_reject(self):
        """raises.type 不是内置异常名 → compile 期拒绝（坏名不进运行期）。"""
        reg = _registry(_throwing_task(ValueError("x")))
        with pytest.raises(FlowValidationError, match="不是内置异常名"):
            FlowCompiler(reg).compile(
                {"steps": [{"task": "boom", "raises": {"type": "MyCustomError"}}]})

    def test_raises_non_string_type_compile_reject(self):
        with pytest.raises(FlowValidationError, match="必须是非空字符串"):
            FlowCompiler(_registry()).compile(
                {"steps": [{"task": "x", "raises": {"type": 123}}]})

    def test_raises_non_mapping_compile_reject(self):
        with pytest.raises(FlowValidationError, match="必须是映射"):
            FlowCompiler(_registry()).compile(
                {"steps": [{"task": "x", "raises": "ValueError"}]})

    def test_raises_unknown_field_compile_reject(self):
        with pytest.raises(FlowValidationError, match="未知字段"):
            FlowCompiler(_registry()).compile(
                {"steps": [{"task": "x", "raises": {"type": "ValueError", "note": "n"}}]})

    def test_raises_bad_regex_compile_reject(self):
        with pytest.raises(FlowValidationError, match="不是合法正则"):
            FlowCompiler(_registry()).compile(
                {"steps": [{"task": "x",
                            "raises": {"type": "ValueError", "match": "(["}}]})

    def test_no_raises_key_unchanged(self):
        """负向对照：不写 raises 键 = 旧行为（步骤抛错就跑失败，不吞）。"""
        reg = _registry(_throwing_task(ValueError("boom")))
        with pytest.raises(ValueError, match="boom"):
            FlowCompiler(reg).run({"steps": [{"task": "boom"}]}, Actor())

    def test_raise_type_whitelist_rejects_non_exception(self):
        """raise_type 单点判据：非异常类（dict/list/int）一律拒绝。"""
        for bad in ("dict", "list", "int", "str", "", "   ", None, 123):
            with pytest.raises(ValueError):
                raise_type(bad)
        # 内置异常全部通过
        for good in ("ValueError", "AssertionError", "TimeoutError", "RuntimeError",
                     "OSError", "KeyError"):
            assert issubclass(raise_type(good), BaseException)


# ==== en-15：assert 块 `reconcile` 跨模块对账 ==================================

class TestReconcileAssertion:
    """en-15：两侧 save_as + 字段映射 + 值级 diff；只读、无副作用、不做模糊匹配。"""

    def test_reconcile_whole_value_equal(self):
        """无 fields 时整值比；相等通过。"""
        reg = _registry(_value_task(a={"x": 1, "y": 2}, b={"x": 1, "y": 2}))
        result = FlowCompiler(reg).run(
            {"steps": [{"task": "set"}],
             "assert": [{"reconcile": {"left": "${a}", "right": "${b}"}}]}, Actor())
        assert result["status"] == "passed"

    def test_reconcile_whole_value_diff_fails_with_both_sides(self):
        """整值不等 → ReconciliationMismatch，消息带两侧值与路径。"""
        reg = _registry(_value_task(a={"x": 1}, b={"x": 2}))
        with pytest.raises(ReconciliationMismatch,
                           match=r"left='\$\{a\}'.*right='\$\{b\}'.*left=\{'x': 1\}.*right=\{'x': 2\}"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "set"}],
                 "assert": [{"reconcile": {"left": "${a}", "right": "${b}"}}]}, Actor())

    def test_reconcile_fields_partial_diff(self):
        """fields 列表：只比列出的字段，其余字段不等不算失败。"""
        reg = _registry(_value_task(
            ui={"total": 10, "render_hint": "v1"}, api={"total": 10, "version": "v2"}))
        result = FlowCompiler(reg).run(
            {"steps": [{"task": "set"}],
             "assert": [{"reconcile": {"left": "${ui}", "right": "${api}",
                                       "fields": ["total"]}}]}, Actor())
        assert result["status"] == "passed"

    def test_reconcile_fields_value_diff_names_path(self):
        """字段值不等 → 失败消息点名 path 与两侧值。"""
        reg = _registry(_value_task(ui={"total": 10}, api={"total": 11}))
        with pytest.raises(ReconciliationMismatch,
                           match=r"path='total' left=10 right=11"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "set"}],
                 "assert": [{"reconcile": {"left": "${ui}", "right": "${api}",
                                           "fields": ["total"]}}]}, Actor())

    def test_reconcile_missing_path_is_mismatch_not_crash(self):
        """路径不可解析 → 记 <missing> 并算不等（不裸崩 KeyError，#89 同纪律）。"""
        reg = _registry(_value_task(ui={"total": 10}, api={"other": 1}))
        with pytest.raises(ReconciliationMismatch, match="__atlas_missing__"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "set"}],
                 "assert": [{"reconcile": {"left": "${ui}", "right": "${api}",
                                           "fields": ["total"]}}]}, Actor())

    def test_reconcile_strict_equality_no_fuzzy(self):
        """不做模糊匹配：'10'（字符串）≠ 10（整数）。"""
        reg = _registry(_value_task(a=10, b="10"))
        with pytest.raises(ReconciliationMismatch):
            FlowCompiler(reg).run(
                {"steps": [{"task": "set"}],
                 "assert": [{"reconcile": {"left": "${a}", "right": "${b}"}}]}, Actor())

    def test_reconcile_compile_shape_gates(self):
        """坏形态 compile 期点名拒绝：left/right 空、fields 非字符串列表、未知字段。"""
        base = {"steps": [{"task": "x"}]}
        with pytest.raises(FlowValidationError, match="reconcile.left"):
            FlowCompiler(_registry()).compile(
                {**base, "assert": [{"reconcile": {"left": "", "right": "${b}"}}]})
        with pytest.raises(FlowValidationError, match="reconcile.fields"):
            FlowCompiler(_registry()).compile(
                {**base, "assert": [{"reconcile": {"left": "${a}", "right": "${b}",
                                                   "fields": "total"}}]})
        with pytest.raises(FlowValidationError, match="未知字段"):
            FlowCompiler(_registry()).compile(
                {**base, "assert": [{"reconcile": {"left": "${a}", "right": "${b}",
                                                   "op": "fuzzy"}}]})
        with pytest.raises(FlowValidationError, match="必须是映射"):
            FlowCompiler(_registry()).compile(
                {**base, "assert": [{"reconcile": "a==b"}]})

    def test_reconcile_mismatch_is_assertion_subclass(self):
        """ReconciliationMismatch 继承 AssertionError：现有断言桶直接可吃。"""
        assert issubclass(ReconciliationMismatch, AssertionError)

    def test_reconcile_diff_no_side_effect(self):
        """reconcile_diff 是纯函数：入参不被改写、返回新结构。"""
        left = {"a": {"b": 1}}
        right = {"a": {"b": 2}}
        diff = reconcile_diff(left, right, fields=["a.b"])
        assert diff["equal"] is False
        assert left == {"a": {"b": 1}} and right == {"a": {"b": 2}}   # 入参原样
        assert diff["mismatches"][0]["path"] == "a.b"

    def test_no_reconcile_key_unchanged(self):
        """负向对照：assert 块不写 reconcile = 旧 assert 形态照常（不新增行为）。"""
        reg = _registry(_value_task(a=1, b=1))
        result = FlowCompiler(reg).run(
            {"steps": [{"task": "set"}], "assert": ["a == b"]}, Actor())
        assert result["status"] == "passed"


# ==== en-10：flow 级 `await: {kind: poll, ...}` 外部事件等待 ====================

class TestAwaitPoll:
    """en-10：轮询既有 assert 形态直到通过或超时；超时落 waiting（C9 口径）。"""

    def test_await_first_tick_passes(self):
        """条件已就绪：首拍立即通过（不先睡 interval）。"""
        reg = _registry(_value_task(ok=True))
        result = FlowCompiler(reg).run(
            {"steps": [{"task": "set"}],
             "await": {"kind": "poll", "timeout_ms": 1000, "interval_ms": 50,
                       "assert": ["ok == True"]}}, Actor())
        assert result["status"] == "passed"

    def test_await_becomes_ready_within_window(self):
        """窗口内条件变就绪 → 通过（轮询真实生效）。

        注意：await 只轮询 assert 子块，**不重跑 steps**——所以「条件随时间变就绪」要靠
        assert 子块里的内联 Task 自己计数（每次轮询重新执行该 Task）。这也钉住了 await 的
        语义边界：steps 阶段的产出在 await 期是**冻结**的，等待外部变化必须把探测放进 assert。"""
        reg = TaskRegistry()
        ticks = {"n": 0}

        def _probe(actor):
            ticks["n"] += 1
            return {"ready": ticks["n"] >= 3}
        reg.register("probe", lambda: FunctionalTask(
            TaskMetadata("demo", "probe", produces=("ready",)), _probe, "probe"))
        actor = Actor()
        result = FlowCompiler(reg).run(
            {"steps": [],
             "await": {"kind": "poll", "timeout_ms": 2000, "interval_ms": 10,
                       "assert": [{"task": "probe"}, "ready == True"]}}, actor)
        assert result["status"] == "passed"
        assert ticks["n"] >= 3   # 真的轮询了不止一拍

    def test_await_timeout_lands_waiting(self):
        """超时仍未就绪 → AwaitingHuman → flow 终态 waiting（绝不静默变绿）。"""
        reg = _registry(_value_task(ok=False))
        with pytest.raises(AwaitingHuman, match="await poll 超时"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "set"}],
                 "await": {"kind": "poll", "timeout_ms": 120, "interval_ms": 30,
                           "assert": ["ok == True"]}}, Actor())

    def test_await_timeout_via_engine_lands_waiting_status(self, tmp_path):
        """经 engine 跑：runs.jsonl 终态 status=waiting（C9 显态家族单点，非 failed）。"""
        from atlas.engine import ExecutionEngine
        from atlas.recorder import RunRecorder
        import json
        reg = _registry(_value_task(ok=False))
        actor = Actor()
        config = type("C", (), {"env": "test", "reports_dir": tmp_path, "mode": "run",
                                "case_timeout": 0, "prod_write_allowed": False,
                                "validate_write": lambda self, r: None,
                                "step_retry": 0, "step_retry_interval_ms": 0,
                                "root": tmp_path})()
        runs_path = tmp_path / "runs.jsonl"
        recorder = RunRecorder("await", "test", runs_path, "run")
        engine = ExecutionEngine(config, recorder=recorder, case_id="await")
        flow = {"engine": config, "steps": [{"task": "set"}],
                "await": {"kind": "poll", "timeout_ms": 80, "interval_ms": 20,
                          "assert": ["ok == True"]}}
        try:
            FlowCompiler(reg).run(flow, actor)
            raise SystemExit("should have raised AwaitingHuman")
        except AwaitingHuman:
            pass
        assert runs_path.exists() and runs_path.read_text(encoding="utf-8").strip()
        row = json.loads(runs_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert row["status"] == FailureClass.WAITING.value
        assert "await poll 超时" in row.get("reason", "")

    def test_await_non_assertion_exception_propagates(self):
        """子块抛非断言异常（业务/会话/契约）= 真失败，原样传播（不等也不吞）。"""
        reg = _registry(_throwing_task(RuntimeError("会话失效")))
        with pytest.raises(RuntimeError, match="会话失效"):
            FlowCompiler(reg).run(
                {"steps": [{"task": "boom"}],
                 "await": {"kind": "poll", "timeout_ms": 500, "interval_ms": 20,
                           "assert": ["x == 1"]}}, Actor())

    def test_await_control_flow_propagates(self):
        """await 轮询期 Ctrl-C 恒穿透（CONTROL_FLOW 单一真相 #114）。"""
        reg = TaskRegistry()
        reg.register("ki", lambda: FunctionalTask(
            TaskMetadata("demo", "ki", read_only=True),
            lambda actor: (_ for _ in ()).throw(KeyboardInterrupt()), "ki"))
        with pytest.raises(KeyboardInterrupt):
            FlowCompiler(reg).run(
                {"steps": [{"task": "ki"}],
                 "await": {"kind": "poll", "timeout_ms": 500, "interval_ms": 10,
                           "assert": ["x == 1"]}}, Actor())

    def test_await_compile_shape_gates(self):
        """坏形态 compile 期拒绝：kind/timeout_ms/interval_ms/assert 各有值域。"""
        base = {"steps": [{"task": "x"}]}
        with pytest.raises(FlowValidationError, match="kind 只支持 poll"):
            FlowCompiler(_registry()).compile(
                {**base, "await": {"kind": "event", "timeout_ms": 100,
                                   "assert": ["x == 1"]}})
        with pytest.raises(FlowValidationError, match="timeout_ms 必须是正整数"):
            FlowCompiler(_registry()).compile(
                {**base, "await": {"kind": "poll", "timeout_ms": 0,
                                   "assert": ["x == 1"]}})
        with pytest.raises(FlowValidationError, match="timeout_ms 必须是正整数"):
            FlowCompiler(_registry()).compile(
                {**base, "await": {"kind": "poll", "timeout_ms": True,
                                   "assert": ["x == 1"]}})
        with pytest.raises(FlowValidationError, match="interval_ms 必须是非负整数"):
            FlowCompiler(_registry()).compile(
                {**base, "await": {"kind": "poll", "timeout_ms": 100, "interval_ms": -1,
                                   "assert": ["x == 1"]}})
        with pytest.raises(FlowValidationError, match="interval_ms.*大于 timeout_ms"):
            FlowCompiler(_registry()).compile(
                {**base, "await": {"kind": "poll", "timeout_ms": 100, "interval_ms": 200,
                                   "assert": ["x == 1"]}})
        with pytest.raises(FlowValidationError, match="assert 必须是非空列表"):
            FlowCompiler(_registry()).compile(
                {**base, "await": {"kind": "poll", "timeout_ms": 100, "assert": []}})
        with pytest.raises(FlowValidationError, match="未知字段"):
            FlowCompiler(_registry()).compile(
                {**base, "await": {"kind": "poll", "timeout_ms": 100, "backoff": 2,
                                   "assert": ["x == 1"]}})
        with pytest.raises(FlowValidationError, match="必须是映射"):
            FlowCompiler(_registry()).compile(
                {**base, "await": "poll"})

    def test_await_sub_assert_reuses_existing_assert_gates(self):
        """await.assert 复用既有 _check_asserts：子块引用未注册 Task 一样编译拒绝。"""
        with pytest.raises(FlowValidationError, match="未注册"):
            FlowCompiler(_registry()).compile(
                {"steps": [{"task": "x"}],
                 "await": {"kind": "poll", "timeout_ms": 100,
                           "assert": [{"task": "ghost"}]}})

    def test_no_await_key_unchanged(self):
        """负向对照：不写 await = 旧 flow 行为逐字节不变。"""
        reg = _registry(_value_task(x=1))
        result = FlowCompiler(reg).run({"steps": [{"task": "set"}]}, Actor())
        assert result["status"] == "passed"

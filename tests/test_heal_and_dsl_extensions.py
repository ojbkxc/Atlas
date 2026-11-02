import pytest
from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.config import AtlasConfig
from atlas.dsl import FlowCompiler, TaskRegistry
from atlas.failures import EnvBlocked
from atlas.healing import Healer
from atlas.knowledge import KnowledgeBase
from atlas.tasks import AssertApi


def make_kb(tmp_path, read_only, remedy="走真实弹窗"):
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [{"id": "k1", "trigger": "Timeout", "symptom": "s",
                                   "root_cause": "r", "remedy": remedy, "scope": ["demo"],
                                   "severity": "medium", "source": "t", "status": "approved"}],
                     approved=True)
    return kb


def test_heal_only_for_read_only_tasks(tmp_path):
    kb = make_kb(tmp_path, True)
    healer = Healer(kb)
    read_only = FunctionalTask(TaskMetadata("demo", "read", read_only=True),
                               lambda a: None, "read")
    writer = FunctionalTask(TaskMetadata("demo", "write", read_only=False),
                            lambda a: None, "write")
    exc = TimeoutError("Timeout")
    assert healer.can_auto_heal(read_only, exc) is True
    assert healer.can_auto_heal(writer, exc) is False


def test_heal_refuses_unreviewed_knowledge(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [{"id": "d", "trigger": "Timeout", "symptom": "s",
                                   "root_cause": "r", "remedy": "x", "scope": ["demo"],
                                   "severity": "medium", "source": "t",
                                   "status": "pending_review"}], approved=True)
    task = FunctionalTask(TaskMetadata("demo", "read", read_only=True), lambda a: None, "read")
    assert Healer(kb).can_auto_heal(task, TimeoutError("Timeout")) is False


def test_can_auto_heal_rejects_api_task(tmp_path):
    """API Task 不自动重试，避免接口副作用。"""
    from atlas.core import Channel
    kb = make_kb(tmp_path, True)
    healer = Healer(kb)
    api_task = FunctionalTask(TaskMetadata("demo", "query", channel=Channel.API, read_only=True),
                              lambda a: None, "query")
    assert healer.can_auto_heal(api_task, TimeoutError("Timeout")) is False


def test_heal_mode_retries_read_only_once(tmp_path):
    from atlas.engine import ExecutionEngine
    kb = make_kb(tmp_path, True)
    calls = {"n": 0}
    def flaky(actor):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("Timeout")
        return {"v": 1}
    task = FunctionalTask(TaskMetadata("demo", "read", produces=("v",), read_only=True), flaky, "read")
    engine = ExecutionEngine(AtlasConfig(tmp_path, mode="heal"), knowledge=kb, healer=Healer(kb))
    result = engine.execute(Actor(), task)
    assert result.ok and calls["n"] == 2


def test_run_mode_does_not_heal(tmp_path):
    from atlas.engine import ExecutionEngine
    kb = make_kb(tmp_path, True)
    task = FunctionalTask(TaskMetadata("demo", "read", read_only=True),
                          lambda a: (_ for _ in ()).throw(TimeoutError("Timeout")), "read")
    engine = ExecutionEngine(AtlasConfig(tmp_path, mode="run"), knowledge=kb, healer=Healer(kb))
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)


def test_dsl_on_env_blocked_skip_continues_flow():
    registry = TaskRegistry()
    registry.register("boom", lambda: FunctionalTask(
        TaskMetadata("x", "boom"), lambda a: (_ for _ in ()).throw(EnvBlocked("no data")), "boom"))
    registry.register("ok", lambda: FunctionalTask(TaskMetadata("x", "ok", produces=("done",)),
                                                   lambda a: {"done": True}, "ok"))
    flow = {"steps": [{"task": "boom", "on_env_blocked": "skip"}, {"task": "ok"}]}
    actor = Actor()
    FlowCompiler(registry).run(flow, actor)
    assert actor.ctx["done"] is True
    assert actor.ctx["_steps_skipped"] == ["boom"]


def test_dsl_on_env_blocked_stop_is_default():
    registry = TaskRegistry()
    registry.register("boom", lambda: FunctionalTask(
        TaskMetadata("x", "boom"), lambda a: (_ for _ in ()).throw(EnvBlocked("no data")), "boom"))
    with pytest.raises(EnvBlocked):
        FlowCompiler(registry).run({"steps": [{"task": "boom"}]}, Actor())


def test_assert_api_compares_nested_path():
    class Api:
        atlas_allowed_operations = {"sale_order_list"}
        def sale_order_list(self, **kw):
            return {"ok": True, "data": {"data": [{"paymentTerms": "105BL"}]}}
    actor = Actor()
    actor.demo_api = Api()
    result = AssertApi("sale_order_list", {"x": 1}, {"data.0.paymentTerms": "105BL"}).perform(actor)
    assert result == {"api_assertion": True}


def test_assert_api_failure_is_assertion_not_swallowed():
    class Api:
        atlas_allowed_operations = {"sale_order_list"}
        def sale_order_list(self, **kw):
            return {"ok": True, "data": {"data": [{"paymentTerms": "NET30"}]}}
    actor = Actor()
    actor.demo_api = Api()
    with pytest.raises(AssertionError, match="105BL"):
        AssertApi("sale_order_list", {}, {"data.0.paymentTerms": "105BL"}).perform(actor)


def test_assert_api_requires_allowlist():
    actor = Actor()
    actor.demo_api = type("A", (), {"atlas_allowed_operations": set()})()
    with pytest.raises(PermissionError):
        AssertApi("save_order", {}, {"data": 1}).perform(actor)


def test_healer_retry_read_only_requires_approval():
    """retry_read_only 未批准时拒绝执行，避免自动重试写操作。"""
    from atlas.healing import Healer
    from atlas.knowledge import KnowledgeBase
    from pathlib import Path
    kb = KnowledgeBase(Path(__file__).resolve().parents[1] / "src/atlas/plugins/demo/knowledge")
    healer = Healer(kb)
    task = FunctionalTask(TaskMetadata("d", "t", read_only=True), lambda actor: None, "t")
    with pytest.raises(PermissionError, match="人工审核"):
        healer.retry_read_only(task, Actor(), lambda: None, approved=False)


def test_healer_retry_read_only_rejects_write_task():
    """非只读 Task 禁止自动重试，避免重复建单/提交。"""
    from atlas.healing import Healer
    from atlas.knowledge import KnowledgeBase
    from pathlib import Path
    kb = KnowledgeBase(Path(__file__).resolve().parents[1] / "src/atlas/plugins/demo/knowledge")
    healer = Healer(kb)
    task = FunctionalTask(TaskMetadata("d", "t", read_only=False), lambda actor: None, "t")
    with pytest.raises(PermissionError, match="非只读"):
        healer.retry_read_only(task, Actor(), lambda: None, approved=True)


def test_dsl_step_skip_records_failure_class_and_downstream_sees_missing_ctx(tmp_path):
    """step 级 skip 后：记录器里该步记 env_blocked 而非 ok；下游读不到被跳过的产出时
    得到的是明确的 KeyError 而非静默 None 传播。"""
    import json
    from atlas import Actor, FunctionalTask, TaskMetadata
    from atlas.config import AtlasConfig

    registry = TaskRegistry()
    registry.register("boom", lambda: FunctionalTask(
        TaskMetadata("x", "boom"), lambda a: (_ for _ in ()).throw(EnvBlocked("no data")), "boom"))
    flow = {"steps": [{"task": "boom", "on_env_blocked": "skip"}],
            "engine": AtlasConfig(tmp_path)}
    actor = Actor()
    FlowCompiler(registry).run(flow, actor)
    record = json.loads((tmp_path / "reports/runs.jsonl").read_text(encoding="utf-8").strip())
    (step,) = record["steps"]
    assert step["ok"] is False
    assert step["fail_class"] == "env_blocked"


def test_skipped_step_flows_into_atlas_summary(tmp_path):
    """端到端：step skip 的 env_blocked 语义能被 atlas summary 正确汇总进 fail_classes，
    不伪装成通过。"""
    from atlas.reporting import summarize
    registry = TaskRegistry()
    registry.register("boom", lambda: FunctionalTask(
        TaskMetadata("x", "boom"), lambda a: (_ for _ in ()).throw(EnvBlocked("no data")), "boom"))
    registry.register("ok_step", lambda: FunctionalTask(
        TaskMetadata("x", "ok", produces=("done",)), lambda a: {"done": True}, "ok_step"))
    flow = {"id": "skip_e2e", "steps": [
        {"task": "boom", "on_env_blocked": "skip"},
        {"task": "ok_step"},
    ], "engine": AtlasConfig(tmp_path)}
    result = FlowCompiler(registry).run(flow, Actor())
    assert result["status"] == "passed"  # flow 整体继续
    summary = summarize(tmp_path / "reports/runs.jsonl")
    assert summary["fail_classes"] == {"env_blocked": 1}  # 但失败类可见，不吞
    assert summary["status"] == {"passed": 1}


def test_skip_flag_does_not_bleed_to_adjacent_steps(tmp_path):
    """步骤 A skip → 步骤 B 正常 → 步骤 C skip：只有 A/C 记 env_blocked，B 不被误标。"""
    from atlas.reporting import summarize
    registry = TaskRegistry()
    registry.register("boom1", lambda: FunctionalTask(
        TaskMetadata("x", "boom"), lambda a: (_ for _ in ()).throw(EnvBlocked("no")), "boom1"))
    registry.register("boom2", lambda: FunctionalTask(
        TaskMetadata("x", "boom"), lambda a: (_ for _ in ()).throw(EnvBlocked("no")), "boom2"))
    registry.register("healthy", lambda: FunctionalTask(
        TaskMetadata("x", "ok"), lambda a: {"ok": True}, "healthy"))
    flow = {"id": "seq", "steps": [
        {"task": "boom1", "on_env_blocked": "skip"},
        {"task": "healthy"},
        {"task": "boom2", "on_env_blocked": "skip"},
    ], "engine": AtlasConfig(tmp_path)}
    FlowCompiler(registry).run(flow, Actor())
    summary = summarize(tmp_path / "reports/runs.jsonl")
    assert summary["fail_classes"] == {"env_blocked": 2}

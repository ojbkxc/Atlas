import pytest
from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.runtime import DemoApiOperation
from atlas.state_machine import StateMachine, Transition
from atlas.experience import ExperienceJournal
from atlas.knowledge import KnowledgeBase


def test_state_machine_finds_shortest_path_without_cycles():
    machine = StateMachine("draft", {"draft": [Transition("saved", "save")],
                                     "saved": [Transition("approved", "approve")], "approved": []})
    assert machine.path("approved") == ["save", "approve"]


def test_state_machine_rejects_ambiguous_transition():
    machine = StateMachine("a", {"a": [Transition("b", "one"), Transition("b", "two")], "b": []})
    with pytest.raises(ValueError, match="多个"):
        machine.run("b", {}, Actor())


def test_state_machine_guard_must_be_explicit():
    machine = StateMachine("a", {"a": [Transition("b", "go", "allowed")], "b": []})
    with pytest.raises(RuntimeError, match="守卫"):
        machine.run("b", {"go": FunctionalTask(TaskMetadata("d", "go"), lambda a: None)}, Actor())


def test_state_machine_run_with_passing_guard_reaches_target():
    """guard 通过时状态机正常迁移到目标状态。"""
    machine = StateMachine("draft", {"draft": [Transition("saved", "save", "can_save")],
                                     "saved": [Transition("approved", "approve")], "approved": []})
    tasks = {"save": FunctionalTask(TaskMetadata("d", "save"), lambda a: None),
             "approve": FunctionalTask(TaskMetadata("d", "approve"), lambda a: None)}
    guards = {"can_save": lambda actor: True}
    assert machine.run("approved", tasks, Actor(), guards=guards) == "approved"


def test_state_machine_run_without_guard_reaches_target():
    """无 guard 的迁移直接执行到目标。"""
    machine = StateMachine("a", {"a": [Transition("b", "go")], "b": []})
    tasks = {"go": FunctionalTask(TaskMetadata("d", "go"), lambda a: None)}
    assert machine.run("b", tasks, Actor()) == "b"


def test_state_machine_max_steps_prevents_infinite_loop():
    """max_steps 超限时抛出，不无限循环。"""
    machine = StateMachine("a", {"a": [Transition("b", "go")],
                                 "b": [Transition("a", "back")]})
    tasks = {"go": FunctionalTask(TaskMetadata("d", "go"), lambda a: None),
             "back": FunctionalTask(TaskMetadata("d", "back"), lambda a: None)}
    with pytest.raises(RuntimeError, match="max_steps"):
        machine.run("c", tasks, Actor(), max_steps=5)


def test_api_operation_uses_allowlist_and_actor_demo_api():
    class Api:
        atlas_allowed_operations = {"sale_order_list"}
        def sale_order_list(self, division_code):
            return {"ok": True, "division": division_code}
    actor = Actor()
    actor.demo_api = Api()
    result = DemoApiOperation("sale_order_list", {"division_code": "DEMO"}).perform(actor)
    assert result["division"] == "DEMO"


def test_api_operation_rejects_unknown_write_operation():
    actor = Actor()
    actor.demo_api = object()
    with pytest.raises(PermissionError):
        DemoApiOperation("save").perform(actor)


def test_demo_api_operation_raises_when_not_assembled():
    """未装配 DemoApi 时明确报错，不静默跳过。"""
    actor = Actor()
    with pytest.raises(RuntimeError, match="未装配"):
        DemoApiOperation("sale_order_list").perform(actor)


def test_demo_api_operation_metadata_reflects_operation():
    """DemoApiOperation 的元数据反映操作名与只读通道。"""
    op = DemoApiOperation("sale_order_list", {"division_code": "DEMO"}, read_only=True)
    assert op.metadata.api_operation == "sale_order_list"
    assert op.metadata.read_only is True
    assert op.name == "sale_order_list"


def test_state_machine_from_dict_rejects_missing_states():
    """from_dict 缺 states 时报错。"""
    with pytest.raises(ValueError, match="states"):
        StateMachine.from_dict({"initial": "a"})


def test_state_machine_from_dict_rejects_missing_to_or_task():
    """迁移缺 to 或 task 时报错。"""
    with pytest.raises(ValueError, match="to 与 task"):
        StateMachine.from_dict({"initial": "a", "states": {"a": [{"to": "b"}]}})


def test_state_machine_infer_terminal_from_empty_states():
    """无 terminal 时自动推断：无迁移的状态为终态。"""
    machine = StateMachine.from_dict({"initial": "a", "states": {"a": [{"to": "b", "task": "go"}], "b": []}})
    assert "b" in machine.terminal


def test_experience_requires_review_then_matches_second_time(tmp_path):
    journal = ExperienceJournal(tmp_path)
    draft = journal.draft(task="save", exc=RuntimeError("BUG-653"))
    assert draft["failure_class"] == "env_blocked"
    journal.append_draft(draft)
    kb = KnowledgeBase(tmp_path)
    with pytest.raises(PermissionError):
        journal.approve_pitfall(kb, draft, trigger="BUG-653", symptom="tax", root_cause="missing",
                                remedy="fix master data", scope=["demo"], source="test")
    journal.approve_pitfall(kb, draft, trigger="BUG-653", symptom="tax", root_cause="missing",
                            remedy="fix master data", scope=["demo"], source="test", approved=True)
    assert kb.match_pitfall(RuntimeError("BUG-653"), "demo")["remedy"] == "fix master data"


def test_experience_draft_has_correct_failure_class():
    """draft 的 failure_class 由 classify_failure 推导。"""
    journal = ExperienceJournal(__import__("pathlib").Path("."))
    draft = journal.draft(task="save", exc=TimeoutError("element not visible"))
    assert draft["failure_class"] == "interaction"
    assert draft["status"] == "pending_review"


def test_experience_append_draft_rejects_non_pending_status(tmp_path):
    """append_draft 只接受 pending_review 草稿。"""
    journal = ExperienceJournal(tmp_path)
    with pytest.raises(ValueError, match="pending_review"):
        journal.append_draft({"status": "approved", "task": "save"})


def test_experience_approve_pitfall_updates_id_prefix(tmp_path):
    """approve_pitfall 把 draft- 前缀改为 reviewed-。"""
    journal = ExperienceJournal(tmp_path)
    kb = KnowledgeBase(tmp_path)
    draft = journal.draft(task="save", exc=RuntimeError("BUG-653"))
    pitfall = journal.approve_pitfall(kb, draft, trigger="BUG-653", symptom="tax",
                                      root_cause="missing", remedy="fix", scope=["demo"],
                                      source="test", approved=True)
    assert pitfall["id"].startswith("reviewed-")
    assert pitfall["status"] == "approved"

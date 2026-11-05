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


def test_state_machine_missing_guard_is_not_silently_taken_via_fallback():
    """声明守卫但未注册：不能静默判假让同状态的无条件 fallback 边被选中并真实执行其 Task。
    必须显式报错，且不得执行任何分支任务（走错分支的副作用是真实建单/点击）。"""
    ran = []
    def mk(name):
        return FunctionalTask(TaskMetadata("d", name), lambda a, n=name: ran.append(n))
    machine = StateMachine("a", {"a": [Transition("b", "go", "allowed"),
                                       Transition("c", "fallback")], "b": [], "c": []})
    with pytest.raises(RuntimeError, match="未注册该守卫"):
        machine.run("c", {"go": mk("go"), "fallback": mk("fallback")}, Actor())
    assert ran == []                                    # 目标在 fallback 分支也没被悄悄执行


def test_state_machine_registered_guard_false_still_skips_edge():
    """守卫已注册且返回 False = 正常业务条件不满足，只跳过该边，合法走 fallback。
    钉死修复没把「判为假的合法守卫」误升级成未注册错误。"""
    noop = lambda a: None
    machine = StateMachine("a", {"a": [Transition("b", "go", "allowed"),
                                       Transition("c", "fallback")], "b": [], "c": []})
    tasks = {"go": FunctionalTask(TaskMetadata("d", "go"), noop),
             "fallback": FunctionalTask(TaskMetadata("d", "fallback"), noop)}
    assert machine.run("c", tasks, Actor(), guards={"allowed": lambda a: False}) == "c"


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
    # 契约自兑现：save_as（默认 "response"）→ 整响应；write_produces 严格校验键一致。
    # ctx 形态不变（ctx["response"] 仍是整响应），只是 Task 自己兑现而不是靠引擎兜底。
    assert result["response"] == {"ok": True, "division": "DEMO"}


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


def test_state_machine_from_dict_rejects_non_mapping_states():
    """states 写成列表（手改/畸形 YAML，atlas machine 可传入）：给可定位的 ValueError，
    不再在内部 .items() 崩成 AttributeError。"""
    with pytest.raises(ValueError, match="states 必须是"):
        StateMachine.from_dict({"initial": "a", "states": ["a", "b"]})


def test_state_machine_from_dict_rejects_non_list_transitions():
    """某状态的迁移写成非列表标量：给可定位的 ValueError，不再 TypeError 崩。"""
    with pytest.raises(ValueError, match="迁移必须是列表"):
        StateMachine.from_dict({"initial": "a", "states": {"a": 5}})


def test_state_machine_infer_terminal_from_empty_states():
    """无 terminal 时自动推断：无迁移的状态为终态。"""
    machine = StateMachine.from_dict({"initial": "a", "states": {"a": [{"to": "b", "task": "go"}], "b": []}})
    assert "b" in machine.terminal


def test_state_machine_from_dict_terminal_null_not_typeerror():
    """#116 面1：YAML 写了 `terminal:` 但忘填值（键在、值 null）——旧实现
    raw.get("terminal", []) 返回 None（默认值只在键缺失时生效），set(None) 裸 TypeError。
    cli.machine 只捕 (OSError, ValueError, yaml.YAMLError)，TypeError 漏网→整条命令 traceback
    裸崩（违反 #79/#80 rc2 口径；#77「键在值 null」同红线）。null 视同未填→自动推导终态。"""
    from atlas.cli import main
    machine = StateMachine.from_dict({"initial": "a",
                                      "states": {"a": [{"to": "b", "task": "go"}], "b": []},
                                      "terminal": None})
    assert "b" in machine.terminal
    # CLI e2e：terminal: null 的 YAML 走 atlas machine 不再 traceback，rc 0
    import yaml
    path = __import__("pathlib").Path(__file__).parent / "_terminal_null_probe.yaml"
    path.write_text(yaml.safe_dump({"initial": "a", "states": {"a": [{"to": "b", "task": "go"}], "b": []},
                                    "terminal": None}, sort_keys=False), encoding="utf-8")
    try:
        assert main(["--root", str(path.parent), "machine", str(path)]) == 0
    finally:
        path.unlink()
    # 非列表形态点名而非崩迭代器
    with pytest.raises(ValueError, match="terminal 必须是列表"):
        StateMachine.from_dict({"initial": "a", "states": {"a": []}, "terminal": 5})


def test_state_machine_from_dict_rejects_null_or_composite_edge_fields():
    """#116 面C：迁移 to/task 写成 null/标量空串/复合形态——旧实现 str() 一律兜成字符串，
    `task:` 忘填被兜成字面量 'None'，atlas machine --target 输出 ['None'] 当合法任务名假绿
    （坏形态伪装合法结果，#113 同红线；空串任务名进 run 才 KeyError，校验推迟）。
    guard 显式 null 回落「无守卫」（与缺省同义）而非假守卫字符串 'None'。"""
    base = {"initial": "a", "states": {"a": [{"to": "b", "task": "go"}], "b": []}}
    for bad_edge, label in [({"to": "b", "task": None}, "task=null"),
                            ({"to": None, "task": "go"}, "to=null"),
                            ({"to": "b", "task": ""}, "task=空串"),
                            ({"to": "b", "task": {"x": 1}}, "task=dict"),
                            ({"to": ["b"], "task": "go"}, "to=list")]:
        raw = {"initial": "a", "states": {"a": [bad_edge], "b": []}}
        with pytest.raises(ValueError, match="迁移 (to|task)"):
            StateMachine.from_dict(raw)
    # guard 语义零漂移：缺省/空串/null 都是无守卫；合法字符串仍是守卫名
    assert StateMachine.from_dict(base).states["a"][0].guard == ""
    raw_guard_null = {"initial": "a", "states": {"a": [{"to": "b", "task": "go", "guard": None}], "b": []}}
    assert StateMachine.from_dict(raw_guard_null).states["a"][0].guard == "", "null 守卫不得变字面量 'None'"
    raw_guard_ok = {"initial": "a", "states": {"a": [{"to": "b", "task": "go", "guard": "can"}], "b": []}}
    assert StateMachine.from_dict(raw_guard_ok).states["a"][0].guard == "can"
    with pytest.raises(ValueError, match="guard 必须是标量"):
        StateMachine.from_dict({"initial": "a", "states": {"a": [{"to": "b", "task": "go", "guard": [1]}]}, "b": []})


def test_state_machine_guard_non_callable_fails_loudly():
    """#116 面3：guards 注册了但值不可调用（误传字符串/None）——旧实现 guards[...](actor)
    裸 TypeError「'str' object is not callable」无守卫名无类型归因。守卫是迁移决策入口，
    坏注册当场点名哪个守卫收到什么类型（#63 声明未注册显式同族：注册≠可用）。"""
    machine = StateMachine("a", {"a": [Transition("b", "go", "allowed")], "b": []})
    task = FunctionalTask(TaskMetadata("d", "go", produces=()), lambda a: {}, "go")
    for bad_guard in ("truthy string", None, 42):
        with pytest.raises(RuntimeError, match="必须是可调用") as exc:
            machine.run("b", {"go": task}, Actor(), guards={"allowed": bad_guard})
        assert "allowed" in str(exc.value)
        assert type(bad_guard).__name__ in str(exc.value)


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

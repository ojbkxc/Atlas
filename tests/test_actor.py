import pytest
from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.composition import compose
from atlas.failures import FlowCompositionError


def task(name, requires=(), produces=()):
    return FunctionalTask(TaskMetadata("demo", name, tuple(requires), tuple(produces)),
                          lambda actor: {produces[0]: name} if produces else None, name)


def test_actor_stores_produced_context():
    actor = Actor().attempts_to(task("create", produces=("order",)))
    assert actor.ctx["order"] == "create"


def test_unordered_tasks_are_composed_by_metadata():
    tasks = [task("save", ("customer",), ("order",)), task("open", produces=("page",)),
             task("customer", ("page",), ("customer",))]
    assert [t.name for t in compose(tasks)] == ["open", "customer", "save"]


def test_missing_dependency_is_explicit():
    with pytest.raises(FlowCompositionError, match="缺"):
        compose([task("save", ("customer",), ("order",))])


def test_cycle_is_rejected():
    with pytest.raises(FlowCompositionError):
        compose([task("a", ("b",), ("a",)), task("b", ("a",), ("b",))])


def test_compose_for_goal_detects_circular_dependency():
    """compose_for_goal 的递归选择必须检测产出循环。"""
    from atlas.composition import compose_for_goal
    t1 = task("t1", ("b",), ("a",))
    t2 = task("t2", ("a",), ("b",))
    with pytest.raises(FlowCompositionError, match="循环"):
        compose_for_goal([t1, t2], "a")


def test_compose_for_goal_missing_output_is_reported():
    """目标产出无 Task 能提供时明确报错。"""
    from atlas.composition import compose_for_goal
    with pytest.raises(FlowCompositionError, match="没有 Task"):
        compose_for_goal([task("t", produces=("x",))], "nonexistent")


def test_compose_skips_tasks_when_dependency_is_provided():
    """provided 集合里的依赖不需要额外 Task 产出。"""
    tasks = [task("save", ("customer",), ("order",))]
    result = compose(tasks, provided={"customer"})
    assert [t.name for t in result] == ["save"]


def test_compose_for_goal_uses_provided_to_skip_upstream():
    """compose_for_goal 的 provided 参数跳过上游 Task。"""
    from atlas.composition import compose_for_goal
    upstream = task("open", produces=("page",))
    downstream = task("read", ("page",), ("data",))
    result = compose_for_goal([upstream, downstream], "data", provided={"page"})
    assert [t.name for t in result] == ["read"]


def test_metadata_requires_domain_and_intent():
    with pytest.raises(ValueError):
        TaskMetadata("", "")

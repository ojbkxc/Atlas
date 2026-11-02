from atlas.dsl import FlowCompiler, TaskRegistry
from atlas.runtime import LegacyStepTask
from atlas.tasks import AssertApi


def test_example_legacy_flow_is_parseable():
    registry = TaskRegistry()
    registry.register("legacy_step", lambda **kw: LegacyStepTask(**kw))
    registry.register("assert_api", lambda **kw: AssertApi(**kw))
    flow = FlowCompiler(registry).load("examples/legacy_step.yaml")
    tasks = FlowCompiler(registry).compile(flow)
    assert [t.name for t in tasks][0].startswith("legacy:")
    assert tasks[1].name == "assert_api:sale_order_list"


def test_legacy_step_task_metadata_is_write():
    task = LegacyStepTask("demo_sit_regression/01_sales_order_default_pt/sales_order_default_pt.py", 1)
    assert task.metadata.read_only is False
    assert task.name.endswith("step_1")


def test_legacy_step_task_name_includes_module_and_step():
    """LegacyStepTask 的 name 格式为 legacy:{module}#step_{N}。"""
    task = LegacyStepTask("path/to/case.py", 3)
    assert task.name == "legacy:path/to/case.py#step_3"


def test_legacy_step_task_save_as_produces():
    """save_as 参数决定 produces，供后续步骤引用。"""
    task = LegacyStepTask("case.py", 1, save_as="result")
    assert "result" in task.metadata.produces
    task_no_save = LegacyStepTask("case.py", 1)
    assert task_no_save.metadata.produces == ()

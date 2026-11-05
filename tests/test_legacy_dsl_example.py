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


def test_legacy_step_task_rejects_yaml_bool_and_float_silently_rewriting_step():
    """#121 面A：YAML 1.1 把 `step: yes/on/true` 解析成 bool True，旧实现 `int(step)`
    静默变成 step_1——执行了「步骤 1」冒充「是」的笔误（#116 str(null)→'None' 假合法同族）；
    `step: 3.7` 截断成 step_3。现构造期点名原始形态。兼容面（#115 教训）：字符串数字 '3'
    与整值浮点 3.0 是 YAML 合法引号/标量形态，放行不翻错。"""
    import pytest
    for bad in (True, False, 3.7):
        with pytest.raises(ValueError, match="step"):
            LegacyStepTask("sales_order.py", bad)
    assert LegacyStepTask("case.py", "3").step == 3       # YAML step: '3'
    assert LegacyStepTask("case.py", 3.0).step == 3       # YAML step: 3.0（整值浮点）
    assert LegacyStepTask("case.py", 1).step == 1
    with pytest.raises(ValueError, match="从 1 起"):       # 旧项目无 step_0
        LegacyStepTask("case.py", 0)

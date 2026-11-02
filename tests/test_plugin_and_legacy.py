import json
from atlas.dsl import interpolate
from atlas.legacy import LegacyAdapter
from atlas.plugins.demo import core_tasks


def test_core_tasks_have_ai_composable_metadata():
    tasks = core_tasks()
    assert len(tasks) == 10
    assert all(task.metadata.domain and task.metadata.intent for task in tasks)
    from atlas.composition import compose_for_goal
    assert [t.name for t in compose_for_goal(tasks, "save_result")][-1] == "click_save"


def test_dsl_interpolation_preserves_types():
    ctx = {"source_order": {"orderNo": "SO1", "status": 1003}}
    assert interpolate("${source_order}", ctx) == ctx["source_order"]
    assert interpolate("单号 ${source_order.orderNo}", ctx) == "单号 SO1"


def test_legacy_adapter_uses_original_pytest(tmp_path):
    from atlas.config import AtlasConfig
    project = tmp_path / "legacy-suite"
    adapter = LegacyAdapter(AtlasConfig(tmp_path), project)
    command = adapter.pytest_command()
    assert command[-1] == "--headless"
    assert "test_demo_sit_regression.py" in command[3]


def test_legacy_adapter_supports_keyword_batches(tmp_path):
    from atlas.config import AtlasConfig
    adapter = LegacyAdapter(AtlasConfig(tmp_path), tmp_path / "legacy-suite")
    command = adapter.pytest_command(keyword="test_tc7_ or test_tc8_")
    assert command[-2:] == ["-k", "test_tc7_ or test_tc8_"]
    assert adapter.pytest_command(headed=True, keyword="x")[-3] == "--headed"


def test_cli_tasks_do_not_need_credentials(tmp_path, capsys):
    from atlas.cli import main
    assert main(["--root", str(tmp_path), "tasks"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 10


def test_core_tasks_carry_required_default_args():
    """pages.py 的必需位置参数必须有默认值，否则 agent/DSL 真实执行会 TypeError。"""
    from atlas.dsl import TaskRegistry
    from atlas.plugins.demo import DemoPlugin
    registry = TaskRegistry()
    DemoPlugin().register_tasks(registry)
    assert registry.create("open_doc_list").params["page_key"]
    assert registry.create("click_new").params["page_key"]
    assert registry.create("select_customer").params["code"]
    assert registry.create("ensure_warehouse").params["wh"]


def test_registry_rebuild_keeps_defaults_after_clone():
    from atlas.dsl import TaskRegistry
    from atlas.plugins.demo import DemoPlugin
    registry = TaskRegistry()
    DemoPlugin().register_tasks(registry)
    overridden = registry.create("open_doc_list", page_key="quotation")
    assert overridden.params["page_key"] == "quotation"
    # 覆盖后再次构造，默认值仍然存在
    assert registry.create("open_doc_list").params["page_key"] == "sales_order"


def test_single_produce_extracts_document_number():
    """wait_save_result 返回 dict，但 produces=document_no，不能把整个 dict 当单号。"""
    from atlas.core import TaskMetadata
    from atlas.plugins.demo.tasks import DemoTask

    class Stub(DemoTask):
        def perform(self, actor):
            # 直接复用父类语义：模拟 pages 返回保存结果 dict
            self._raw = {"ok": True, "docNo": "SO-DEMO-0006", "msgs": []}
            return super().perform(actor)

    task = Stub(TaskMetadata("demo", "save", produces=("document_no",),
                             tags=("wait_save_result",)), None, defaults={})
    task._raw = {"ok": True, "docNo": "SO-DEMO-0006", "msgs": []}
    from atlas.plugins.demo.tasks import Pages
    original = Pages.call
    Pages.call = lambda self, name, actor, **kw: {"ok": True, "docNo": "SO-DEMO-0006"}
    try:
        assert task.perform(None) == {"document_no": "SO-DEMO-0006"}
    finally:
        Pages.call = original


def test_select_customer_supports_api_route():
    """方案 3.2：channel=auto 的 Task 有 supports_api=True，引擎可走 API。"""
    tasks = {t.name: t for t in core_tasks()}
    assert tasks["select_customer"].supports_api is True
    assert tasks["read_pt"].supports_api is True
    # 纯 UI Task 不应有 supports_api
    assert tasks["click_new"].supports_api is False
    assert tasks["click_save"].supports_api is False


def test_auto_route_degrades_to_ui_on_api_failure(tmp_path):
    """auto+supports_api 的 Task API 路径失败时降级 UI，不静默吞掉。"""
    from atlas import Actor, Channel, TaskMetadata
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine

    class AutoTask:
        supports_api = True
        metadata = TaskMetadata("demo", "auto", channel=Channel.AUTO,
                                api_operation="query", produces=("v",))
        name = "auto_task"
        ui_called = False
        def _perform_api(self, actor):
            raise NotImplementedError("API 未实现")
        def perform(self, actor):
            self.ui_called = True
            return {"v": "from_ui"}

    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = Actor()
    task = AutoTask()
    engine.execute(actor, task)
    assert actor.ctx["v"] == "from_ui"
    assert task.ui_called is True
    assert actor.ctx.get("_auto_degraded_to_ui")


def test_auto_route_calls_task_specific_perform_api(tmp_path):
    """auto+supports_api 的 Task 必须调自己的 _perform_api，不是通用 POST。

    之前的 bug：_perform_via_api 的子类判定恒为 False，导致即使 Task 实现了
    _perform_api 也走通用 POST，装配 CallApi 后会用错接口/参数。
    """
    from atlas import Actor, Channel, TaskMetadata
    from atlas.abilities import CallApi
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine

    class ApiTask:
        supports_api = True
        metadata = TaskMetadata("demo", "auto", channel=Channel.AUTO,
                                api_operation="customer_detail", produces=("customer",))
        name = "api_task"
        api_called = False
        def _perform_api(self, actor):
            self.api_called = True
            return {"customer": {"code": "CUST0000001", "from": "task_api"}}
        def perform(self, actor):
            return {"customer": {"from": "ui"}}

    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = Actor(abilities=[CallApi("http://test")])
    task = ApiTask()
    engine.execute(actor, task)
    assert task.api_called is True
    assert actor.ctx["customer"]["from"] == "task_api"


def test_register_tasks_populates_all_core_task_names():
    """register_tasks 必须注册全部 10 个核心 Task，缺一不可。"""
    from atlas.dsl import TaskRegistry
    from atlas.plugins.demo import DemoPlugin
    registry = TaskRegistry()
    DemoPlugin().register_tasks(registry)
    for name in ("open_doc_list", "click_new", "select_customer", "read_pt",
                 "ensure_warehouse", "ensure_sales_person", "mount_other_header",
                 "click_save", "wait_save_result", "select_pt"):
        task = registry.create(name)
        assert task.name == name
        assert task.metadata.domain == "demo"


def test_interpolate_handles_non_string_values():
    """interpolate 对非字符串值（int/dict/list）原样返回。"""
    assert interpolate(42, {}) == 42
    assert interpolate({"k": "${v}"}, {"v": "x"}) == {"k": "x"}
    assert interpolate(["${v}", 1], {"v": "x"}) == ["x", 1]
    assert interpolate(None, {}) is None


def test_interpolate_full_match_returns_native_value():
    """完整匹配 ${path} 时返回原生值，不转字符串。"""
    ctx = {"count": 5, "items": [1, 2, 3]}
    assert interpolate("${count}", ctx) == 5
    assert interpolate("${items}", ctx) == [1, 2, 3]

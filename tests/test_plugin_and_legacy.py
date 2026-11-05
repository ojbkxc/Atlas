import json
import pytest
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


def _auto_task_cls(api_exc, ui_ok=True):
    """auto+supports_api 的 Task：API 路径抛指定异常，UI 路径返回 {"v": ...}。"""
    from atlas import Channel, TaskMetadata

    class T:
        supports_api = True
        metadata = TaskMetadata("demo", "auto", channel=Channel.AUTO,
                                api_operation="query", produces=("v",), read_only=True)
        name = "auto_task"
        api_raised = False
        ui_called = False

        def _perform_api(self, actor):
            self.api_raised = True
            raise api_exc

        def perform(self, actor):
            self.ui_called = True
            return {"v": "from_ui"}
    return T


def test_auto_degradation_lands_in_runlog(tmp_path):
    """#106：auto 降级原因以前只进 ctx["_auto_degraded_to_ui"]（全仓零消费方），
    步骤在 runs.jsonl 里 ok=True 零痕迹——「API 坏→退 UI→通过」与「UI 本来就该走」
    事后无从区分（#102 静默 0 条/#105 假阴性同族：事件伪装成不存在=假阴性缺陷）。
    修复后降级事件并入 step 记录并落盘 runs.jsonl。"""
    from atlas import Actor
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder

    runs = tmp_path / "runs.jsonl"
    rec = RunRecorder("caseDeg", "sit", runs)
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    actor = Actor()
    task = _auto_task_cls(NotImplementedError("API 未实现"))()
    result = engine.execute(actor, task)
    rec.close("passed")
    assert result.ok and task.ui_called
    run = json.loads(runs.read_text(encoding="utf-8").strip().splitlines()[-1])
    step = next(s for s in run["steps"] if s["step"] == "auto_task")
    assert step["ok"] is True
    assert step["degraded_to_ui"] == [{"task": "auto_task", "reason": "API 未实现"}]


def test_no_degradation_keeps_step_clean(tmp_path):
    """不降级时 step 记录绝不得凭空多 degraded_to_ui 键（避免污染正常通过步骤）。"""
    from atlas import Actor, Channel, TaskMetadata
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder

    class PlainTask:
        metadata = TaskMetadata("demo", "go", channel=Channel.UI, produces=("v",), read_only=True)
        name = "plain"
        def perform(self, actor):
            return {"v": 1}

    runs = tmp_path / "runs.jsonl"
    rec = RunRecorder("caseClean", "sit", runs)
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    engine.execute(Actor(), PlainTask())
    rec.close("passed")
    run = json.loads(runs.read_text(encoding="utf-8").strip().splitlines()[-1])
    step = next(s for s in run["steps"] if s["step"] == "plain")
    assert "degraded_to_ui" not in step


def test_heal_retry_degradation_also_recorded(tmp_path):
    """heal 重试路径同口径：首次失败→重试时「API 坏→降级→UI 成功」，
    就地更正的同一条 step 记录必须同时带 healed=True 与 degraded_to_ui——
    自愈归因链不能只看坑点、丢掉驱动它的通道降级事实。"""
    from atlas import Actor
    from atlas.config import AtlasConfig
    from atlas.core import Channel, TaskMetadata
    from atlas.engine import ExecutionEngine
    from atlas.healing import Healer
    from atlas.knowledge import KnowledgeBase
    from atlas.recorder import RunRecorder

    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [{"id": "k1", "trigger": "Timeout", "symptom": "s",
                                   "root_cause": "r", "remedy": "退 UI", "scope": ["demo"],
                                   "severity": "medium", "source": "t", "status": "approved"}],
                     approved=True)

    class FlakyApiTask:
        from atlas.core import Channel, TaskMetadata

        supports_api = True
        metadata = TaskMetadata("demo", "auto", channel=Channel.AUTO,
                                api_operation="query", produces=("v",), read_only=True)
        name = "flaky_auto"
        n = 0
        def _perform_api(self, actor):
            self.n += 1
            if self.n == 1:
                raise TimeoutError("Timeout")
            raise NotImplementedError("API 坏了")   # 重试时 API 故障 → 降级
        def perform(self, actor):
            return {"v": "from_ui"}

    runs = tmp_path / "runs.jsonl"
    rec = RunRecorder("caseHealDeg", "sit", runs, mode="heal")
    engine = ExecutionEngine(AtlasConfig(tmp_path, mode="heal"), knowledge=kb,
                             healer=Healer(kb), recorder=rec)
    result = engine.execute(Actor(), FlakyApiTask())
    rec.close("passed")
    assert result.ok
    run = json.loads(runs.read_text(encoding="utf-8").strip().splitlines()[-1])
    step = next(s for s in run["steps"] if s["step"] == "flaky_auto")
    assert step["ok"] is True and step.get("healed") is True
    assert step["degraded_to_ui"] == [{"task": "flaky_auto", "reason": "API 坏了"}]


def test_auto_does_not_swallow_contract_violation(tmp_path):
    """#106 探针确证：契约违规（ContractViolation 属 AssertionError 系）与网络故障
    （ConnectionError 属 OSError 系）都**不在** _perform 的 (NotImplementedError, RuntimeError)
    捕获集内——真实 API 故障绝不被 auto 通道静默降级到 UI 掩盖。钉死这条红线：
    若哪天把 except 放宽到 Exception，本测会抓到「API 坏了却被 UI 翻绿」的掩蔽回归。"""
    from atlas.abilities import CallApi
    from atlas import Actor
    from atlas.config import AtlasConfig
    from atlas.contracts import Contract, ContractRegistry
    from atlas.engine import ExecutionEngine
    from atlas.failures import ContractViolation

    # ① 契约违规走通用 POST：Task 无 supports_api→命中不了 _perform_api 分支，走通用 POST，
    #    注册的 minimal 契约 ok=False 不通过 → ContractViolation 传播（不降级、UI 不被调用）。
    from atlas.core import Channel, TaskMetadata

    class GTask:
        # supports_api=True 但没有 _perform_api → _perform_via_api 落到通用 POST 分支
        supports_api = True
        params = {}
        metadata = TaskMetadata("demo", "q", channel=Channel.AUTO, api_operation="/x",
                                produces=("v",), read_only=True)
        name = "generic_post"
        ui_called = False
        def perform(self, actor):
            self.ui_called = True
            return {"v": "ui"}

    class FakeResp:
        def json(self): return {"resultCode": "FAIL"}
        ok = False
        status_code = 200
        url = "http://t/x"
        headers = {}

    class FakeHttp:
        headers = {}
        def request(self, *a, **k): return FakeResp()

    registry = ContractRegistry([Contract(name="/x", method="POST", path="/x",
                                          level="minimal", required={})])
    actor = Actor(abilities=[CallApi("http://t", session=FakeHttp())])
    engine = ExecutionEngine(AtlasConfig(tmp_path), contracts=registry)
    task = GTask()
    # minimal 契约 ok=False → ContractViolation；绝不能被降级吞成 UI 成功
    with pytest.raises(ContractViolation):
        engine.execute(actor, task)
    assert task.ui_called is False   # 没走 UI 降级——故障如实传播


def test_auto_does_not_swallow_connection_error(tmp_path):
    """② Task._perform_api 抛 ConnectionError（网络故障，OSError 系）：不在捕获集，
    直接传播为步骤失败，不降级 UI。与契约违规同红线。"""
    from atlas import Actor
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine

    actor = Actor()
    task = _auto_task_cls(ConnectionError("网关 502 连不上"))()
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    with pytest.raises(ConnectionError):
        engine.execute(actor, task)
    assert task.ui_called is False   # 网络故障没被降级掩盖
    assert not actor.ctx.get("_auto_degraded_to_ui")


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


def test_bad_interpolation_path_is_attributable(tmp_path):
    """写错的 ctx 路径必须报「哪条路径、卡在第几段」，而非裸 KeyError('pt')。
    多步/导入 flow 里裸 KeyError 无法归因；且仍必须是 KeyError——
    assert 表达式 _resolve_operand 依赖 KeyError 类型做「路径→字面量」回退，改类型会破语义。"""
    import pytest
    ctx = {"source_order": {"pt": 100}, "count": 5}
    with pytest.raises(KeyError, match=r"source_order\.ptp"):          # 整条路径在消息里
        interpolate("${source_order.ptp}", ctx)
    with pytest.raises(KeyError, match=r"第 2 段 'ptp'"):               # 点名失败的段
        interpolate("${source_order.ptp}", ctx)
    with pytest.raises(KeyError, match="source_order.missing.deep"):
        interpolate("前缀 ${source_order.missing.deep} 后缀", ctx)      # 中段插值同样可归因
    # 非 dict 容器无法按名解析：也归因，不再抛裸 AttributeError
    with pytest.raises(KeyError, match=r"count\.name"):
        interpolate("${count.name}", ctx)


def test_resolve_operand_still_falls_back_to_literal():
    """表达式断言里操作符两侧「先路径后字面量」：不是 ctx 路径的必须是字面量，
    靠 _lookup 抛 KeyError 触发回退。钉死这个契约，防止改异常类型静默破 assert。"""
    from atlas.dsl import FlowCompiler
    class _A:
        ctx = {"a": 7}
    assert FlowCompiler._resolve_operand("a", _A(), 1) == 7            # 命中路径
    assert FlowCompiler._resolve_operand("b", _A(), 1) == "b"          # 不在 ctx → 字面量 str
    assert FlowCompiler._resolve_operand("12", _A(), 1) == 12          # 数字字面量转 int

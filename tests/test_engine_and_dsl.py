import json
import pytest
from atlas import Actor, Channel, FunctionalTask, TaskMetadata
from atlas.config import AtlasConfig
from atlas.dsl import FlowCompiler, TaskRegistry
from atlas.engine import ExecutionEngine
from atlas.tasks import CallEndpoint


def test_dsl_engine_enforces_production_write_guard(tmp_path):
    registry = TaskRegistry()
    registry.register("noop", lambda: FunctionalTask(
        TaskMetadata("demo", "write", read_only=False), lambda actor: None, "noop"))
    flow = {"steps": [{"task": "noop"}], "engine": AtlasConfig(tmp_path, env="prod-hk")}
    with pytest.raises(PermissionError):
        FlowCompiler(registry).run(flow, Actor())


def test_execution_engine_records_output_without_api_ability(tmp_path):
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    task = FunctionalTask(TaskMetadata("demo", "read", produces=("x",), channel=Channel.AUTO),
                          lambda actor: {"x": 1}, "read")
    actor = Actor()
    engine.execute(actor, task)
    assert actor.ctx["x"] == 1


def test_dsl_save_as_is_passed_to_task_factory():
    registry = TaskRegistry()
    registry.register("call", lambda **kwargs: CallEndpoint("/health", **kwargs))
    task = FlowCompiler(registry).compile({"steps": [{"task": "call", "save_as": "health"}]})[0]
    assert task.metadata.produces == ("health",)


def test_engine_records_failure_and_does_not_swallow(tmp_path):
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    task = FunctionalTask(TaskMetadata("demo", "timeout"), lambda actor: (_ for _ in ()).throw(TimeoutError()), "timeout")
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)
    assert engine.results[0].failure_class == "interaction"


def test_dsl_assert_block_expression_passes():
    """方案 3.3：assert 块表达式断言 a == b。"""
    registry = TaskRegistry()
    registry.register("set_pt", lambda: FunctionalTask(
        TaskMetadata("demo", "读PT", produces=("pt_echo",)), lambda actor: {"pt_echo": "NET30"}, "set_pt"))
    registry.register("set_source", lambda: FunctionalTask(
        TaskMetadata("demo", "来源单", produces=("source_order",)),
        lambda actor: {"source_order": {"pt": "NET30"}}, "set_source"))
    flow = {
        "steps": [{"task": "set_source"}, {"task": "set_pt"}],
        "assert": ["pt_echo == source_order.pt"],
    }
    actor = Actor()
    FlowCompiler(registry).run(flow, actor)
    assert actor.ctx["pt_echo"] == "NET30"


def test_dsl_assert_block_expression_fails():
    registry = TaskRegistry()
    registry.register("set_pt", lambda: FunctionalTask(
        TaskMetadata("demo", "读PT", produces=("pt_echo",)), lambda actor: {"pt_echo": "90DF"}, "set_pt"))
    flow = {"steps": [{"task": "set_pt"}], "assert": ["pt_echo == NET30"]}
    with pytest.raises(AssertionError, match="assert 块"):
        FlowCompiler(registry).run(flow, Actor())


def test_dsl_assert_block_supports_inequality_operators():
    """assert 块支持 !=、>=、<=、>、< 五种比较运算符。"""
    registry = TaskRegistry()
    registry.register("set", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("n", "s")),
        lambda actor: {"n": 5, "s": "SP1"}, "set"))
    actor = Actor()
    FlowCompiler(registry).run({"steps": [{"task": "set"}],
        "assert": ["n != 0", "n >= 5", "n <= 5", "n > 4", "n < 6", "s != ''"]}, actor)
    assert actor.ctx["n"] == 5


def test_dsl_assert_block_inequality_failure_reports_clearly():
    registry = TaskRegistry()
    registry.register("set", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("n",)), lambda actor: {"n": 3}, "set"))
    flow = {"steps": [{"task": "set"}], "assert": ["n >= 5"]}
    with pytest.raises(AssertionError, match=r"n\(3\) >= 5\(5\) 不成立"):
        FlowCompiler(registry).run(flow, Actor())


def test_dsl_assert_block_rejects_expression_without_operator():
    registry = TaskRegistry()
    flow = {"steps": [], "assert": ["just_a_path"]}
    with pytest.raises(ValueError, match="缺少比较运算符"):
        FlowCompiler(registry).run(flow, Actor())


def test_dsl_compile_validates_assert_task_reference_early():
    """compile 时就验证 assert 块的 task 引用已注册，不等 run 时才暴露。"""
    registry = TaskRegistry()
    flow = {"steps": [], "assert": [{"task": "nonexistent"}]}
    with pytest.raises(KeyError, match="assert 块第 1 条引用未注册"):
        FlowCompiler(registry).compile(flow)


def test_dsl_compile_validates_assert_task_args_early():
    """compile 时就验证 assert 块 task 的参数合法（如 assert_api 需要 expect）。"""
    from atlas.tasks import AssertApi
    registry = TaskRegistry()
    registry.register("assert_api", lambda **kw: AssertApi(**kw))
    flow = {"steps": [], "assert": [{"task": "assert_api", "operation": "sale_order_list"}]}
    with pytest.raises(ValueError, match="expect"):
        FlowCompiler(registry).compile(flow)


def test_dsl_assert_block_mixed_forms_end_to_end():
    """方案 3.3 端到端：一个 flow 同时用表达式、键值、内联 Task 三种 assert。"""
    registry = TaskRegistry()
    registry.register("set", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("pt", "source", "count")),
        lambda actor: {"pt": "NET30", "source": {"pt": "NET30"}, "count": 5}, "set"))
    registry.register("check", lambda: FunctionalTask(
        TaskMetadata("demo", "check", produces=("ok",)), lambda actor: {"ok": True}, "check"))
    flow = {
        "steps": [{"task": "set"}],
        "assert": [
            "pt == source.pt",          # 表达式：ctx 路径比较
            "count >= 5",               # 表达式：数字比较
            {"task": "check"},          # 内联 Task 断言（产出 ok）
            {"ok": True},               # 键值断言（验证 check 产出）
        ],
    }
    actor = Actor()
    FlowCompiler(registry).run(flow, actor)
    assert actor.ctx["pt"] == "NET30"
    assert actor.ctx["ok"] is True


def test_dsl_assert_block_dict_kv():
    """assert 块 dict 不带 task：键值断言。"""
    registry = TaskRegistry()
    registry.register("set", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("x",)), lambda actor: {"x": 42}, "set"))
    flow = {"steps": [{"task": "set"}], "assert": [{"x": 42}]}
    FlowCompiler(registry).run(flow, Actor())


def test_dsl_assert_block_inline_task():
    """assert 块 dict 带 task：内联 Task 断言（如 assert_api）。"""
    registry = TaskRegistry()
    registry.register("read", lambda: FunctionalTask(
        TaskMetadata("demo", "read", produces=("v",)), lambda actor: {"v": 1}, "read"))
    registry.register("check", lambda: FunctionalTask(
        TaskMetadata("demo", "check", produces=("ok",)), lambda actor: {"ok": True}, "check"))
    flow = {"steps": [{"task": "read"}], "assert": [{"task": "check"}]}
    actor = Actor()
    FlowCompiler(registry).run(flow, actor)
    assert actor.ctx["ok"] is True


def test_dsl_flow_with_provision_and_assert_end_to_end(tmp_path):
    """端到端：requires provision + steps 插值 + assert 块完整流程。"""
    from atlas.config import AtlasConfig
    from atlas.probes import CallableProbe, DataProvider
    probe = CallableProbe("SalesOrder", lambda where: {"orderNo": "SO1", "pt": "NET30"})
    provider = DataProvider(AtlasConfig(tmp_path), [probe])
    registry = TaskRegistry()
    registry.register("echo", lambda: FunctionalTask(
        TaskMetadata("demo", "echo", produces=("order_no",)),
        lambda actor: {"order_no": actor.ctx["source_order"]["orderNo"]}, "echo"))
    flow = {
        "requires": {"source_order": {"entity": "SalesOrder", "strategy": "find_only"}},
        "steps": [{"task": "echo"}],
        "assert": ["order_no == SO1", "source_order.pt == NET30"],
    }
    actor = Actor()
    FlowCompiler(registry, provider).run(flow, actor)
    assert actor.ctx["order_no"] == "SO1"
    assert actor.ctx["source_order"]["pt"] == "NET30"
    assert actor.ctx["_data_actions"]["source_order"] == "find"


def test_dsl_flow_with_engine_records_results(tmp_path):
    """端到端：FlowCompiler.run 带 engine，验证 results 返回与 recorder 落盘。"""
    config = AtlasConfig(tmp_path, mode="run")
    registry = TaskRegistry()
    registry.register("set", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("v",)), lambda actor: {"v": 1}, "set"))
    flow = {"id": "test-flow", "engine": config, "steps": [{"task": "set"}]}
    actor = Actor()
    result = FlowCompiler(registry).run(flow, actor)
    assert result["status"] == "passed"
    assert result["steps"] == ["set"]
    assert actor.ctx["v"] == 1
    record = json.loads((tmp_path / "reports" / "runs.jsonl").read_text(encoding="utf-8"))
    assert record["case"] == "test-flow"
    assert record["status"] == "passed"
    assert record["steps"][0]["step"] == "set"
    assert record["steps"][0]["ok"] is True


def test_dsl_flow_with_engine_failure_records_fail_class(tmp_path):
    """engine 执行失败时 recorder 记录 fail_class，status=failed。"""
    config = AtlasConfig(tmp_path, mode="run")
    registry = TaskRegistry()
    registry.register("boom", lambda: FunctionalTask(
        TaskMetadata("demo", "boom"), lambda actor: (_ for _ in ()).throw(TimeoutError("超时")), "boom"))
    flow = {"id": "fail-flow", "engine": config, "steps": [{"task": "boom"}]}
    with pytest.raises(TimeoutError):
        FlowCompiler(registry).run(flow, Actor())
    record = json.loads((tmp_path / "reports" / "runs.jsonl").read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["steps"][0]["ok"] is False
    assert record["steps"][0]["fail_class"] == "interaction"


def test_examples_yaml_requires_are_parseable():
    """所有线性 DSL examples 的 requires 部分必须能被 DataRequirement 解析。"""
    from pathlib import Path
    from atlas.probes import DataRequirement
    for p in sorted(Path("examples").glob("*.yaml")):
        try:
            flow = FlowCompiler(TaskRegistry()).load(p)
        except ValueError:
            continue  # 非线性 DSL（如状态机）
        for name, raw in flow.get("requires", {}).items():
            req = DataRequirement.from_dict(raw)
            assert req.entity, f"{p.name} 的 {name} 缺 entity"


def test_all_linear_examples_compile_with_demo_registry():
    """所有线性 DSL examples 必须能用 Demo 插件的 TaskRegistry 编译通过。"""
    from pathlib import Path
    from atlas.plugins.demo import DemoPlugin
    from atlas.tasks import CallEndpoint, UiAction, AssertApi
    from atlas.runtime import DemoApiOperation, LegacyStepTask
    registry = TaskRegistry()
    DemoPlugin().register_tasks(registry)
    for name, factory in [("api_call", CallEndpoint), ("ui_action", UiAction),
                          ("demo_api", DemoApiOperation), ("assert_api", AssertApi),
                          ("legacy_step", LegacyStepTask)]:
        registry.register(name, lambda f=factory, **kw: f(**kw))
    for p in sorted(Path("examples").glob("*.yaml")):
        try:
            flow = FlowCompiler(registry).load(p)
        except ValueError:
            continue  # 非线性 DSL
        tasks = FlowCompiler(registry).compile(flow)
        assert len(tasks) >= 1, f"{p.name} 编译后无 Task"


def test_flow_compiler_load_rejects_missing_steps(tmp_path):
    """load 缺 steps 的 YAML 时报错。"""
    p = tmp_path / "bad.yaml"
    p.write_text("id: bad\n", encoding="utf-8")
    with pytest.raises(ValueError, match="steps"):
        FlowCompiler(TaskRegistry()).load(p)


def test_flow_compiler_load_rejects_non_dict(tmp_path):
    """load 非 dict 的 YAML 时报错。"""
    p = tmp_path / "bad.yaml"
    p.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="steps"):
        FlowCompiler(TaskRegistry()).load(p)


def test_flow_compiler_compile_rejects_step_without_task():
    """compile 缺 task 的步骤时报错。"""
    with pytest.raises(ValueError, match="缺少 task"):
        FlowCompiler(TaskRegistry()).compile({"steps": [{"name": "no_task"}]})


def test_task_registry_create_raises_on_unknown():
    """TaskRegistry.create 对未注册的 Task 名报错。"""
    with pytest.raises(KeyError, match="未注册"):
        TaskRegistry().create("nonexistent")


def test_flow_compiler_compile_rejects_invalid_on_env_blocked():
    """step 的 on_env_blocked 只支持 skip/stop。"""
    registry = TaskRegistry()
    registry.register("t", lambda: FunctionalTask(TaskMetadata("d", "t"), lambda a: None, "t"))
    with pytest.raises(ValueError, match="on_env_blocked"):
        FlowCompiler(registry).compile({"steps": [{"task": "t", "on_env_blocked": "bogus"}]})


def test_flow_compiler_run_rejects_invalid_flow_level_on_env_blocked():
    """flow 级 on_env_blocked 只支持 skip/stop。"""
    registry = TaskRegistry()
    with pytest.raises(ValueError, match="flow 级 on_env_blocked"):
        FlowCompiler(registry).run({"steps": [], "on_env_blocked": "bogus"}, Actor())


def test_dsl_interpolation_restores_task_attributes_after_perform():
    """插值后必须恢复 task 原属性，否则重用 task 实例会用上次的插值残留。"""
    from atlas.core import Task
    class ParamTask(Task):
        def __init__(self, code="${value}"):
            self.code = code
            self.metadata = TaskMetadata("demo", "echo", produces=("code",))
        @property
        def name(self):
            return "param_task"
        def perform(self, actor):
            return {"code": self.code}
    registry = TaskRegistry()
    registry.register("echo", lambda code="${value}": ParamTask(code=code))
    flow = {"steps": [{"task": "echo", "code": "${value}"}]}
    actor1 = Actor()
    actor1.ctx["value"] = "AAA"
    FlowCompiler(registry).run(flow, actor1)
    assert actor1.ctx["code"] == "AAA"
    actor2 = Actor()
    actor2.ctx["value"] = "BBB"
    FlowCompiler(registry).run(flow, actor2)
    assert actor2.ctx["code"] == "BBB"


def test_dsl_assert_block_handles_no_spaces_and_negative_literals():
    """assert 表达式支持无空格写法与负数字面量。"""
    registry = TaskRegistry()
    registry.register("set", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("n",)),
        lambda actor: {"n": -5}, "set"))
    FlowCompiler(registry).run({"steps": [{"task": "set"}],
        "assert": ["n==-5", "n < 0", "n != 0"]}, Actor())


def test_dsl_assert_block_handles_float_literals():
    """assert 表达式支持 float 字面量比较。"""
    registry = TaskRegistry()
    registry.register("set", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("pi",)),
        lambda actor: {"pi": 3.14}, "set"))
    FlowCompiler(registry).run({"steps": [{"task": "set"}],
        "assert": ["pi >= 3.0", "pi < 3.2", "pi != 3"]}, Actor())


def test_call_endpoint_metadata_is_correct():
    """CallEndpoint 的元数据必须反映 API 通道与产出键。"""
    task = CallEndpoint("/api/test", method="GET", save_as="result")
    assert task.metadata.channel == Channel.API
    assert task.metadata.api_operation == "/api/test"
    assert task.metadata.produces == ("result",)
    assert task.method == "GET"


def test_call_endpoint_perform_calls_api_request(monkeypatch):
    """CallEndpoint.perform 把 method/path/body 传给 actor.api.request。"""
    from atlas.abilities import CallApi
    task = CallEndpoint("/api/test", method="POST", body={"k": "v"}, save_as="result")
    actor = Actor(abilities=[CallApi("http://test")])
    calls = []
    def mock_request(self, method, path, **kw):
        calls.append((method, path, kw))
        return {"ok": True, "data": {"passed": True}}
    monkeypatch.setattr(CallApi, "request", mock_request)
    result = task.perform(actor)
    assert result["ok"] is True
    assert calls[0] == ("POST", "/api/test", {"body": {"k": "v"}})


def test_ui_action_perform_calls_action_with_page_and_ctx():
    """UiAction.perform 把 page 与 ctx 传给业务 action 函数。"""
    from atlas.tasks import UiAction
    from atlas.abilities import BrowseTheWeb
    called = []
    def action(page, ctx):
        called.append((page, dict(ctx)))
        return {"done": True}
    task = UiAction(action, domain="demo", intent="test", produces=("done",))
    actor = Actor(abilities=[BrowseTheWeb("fake_page")])
    actor.ctx["k"] = "v"
    result = task.perform(actor)
    assert result == {"done": True}
    assert called == [("fake_page", {"k": "v"})]
    assert task.metadata.channel == Channel.UI


def test_engine_prove_api_classifies_by_replay(tmp_path):
    """prove_api 由调用方提供重放器：API 正常归交互，401 归会话。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    assert engine.prove_api(TimeoutError("click"), lambda: {"ok": True}).value == "interaction"
    assert engine.prove_api(TimeoutError("click"), lambda: {"ok": False, "status": 401}).value == "session"
    assert engine.prove_api(TimeoutError("click"), lambda: {"ok": False, "status": 500}).value == "env_blocked"


def test_dsl_flow_level_on_env_blocked_skip():
    """flow 级 on_env_blocked: skip 不抛异常。"""
    from atlas.failures import EnvBlocked
    registry = TaskRegistry()
    registry.register("block", lambda: FunctionalTask(
        TaskMetadata("demo", "block"), lambda actor: (_ for _ in ()).throw(EnvBlocked("环境限制")), "block"))
    flow = {"steps": [{"task": "block"}], "on_env_blocked": "skip"}
    actor = Actor()
    result = FlowCompiler(registry).run(flow, actor)
    assert result["status"] == "env_blocked"
    assert actor.ctx.get("_flow_skipped") is True


def test_dsl_flow_level_on_env_blocked_stop():
    from atlas.failures import EnvBlocked
    registry = TaskRegistry()
    registry.register("block", lambda: FunctionalTask(
        TaskMetadata("demo", "block"), lambda actor: (_ for _ in ()).throw(EnvBlocked("环境限制")), "block"))
    flow = {"steps": [{"task": "block"}], "on_env_blocked": "stop"}
    with pytest.raises(EnvBlocked):
        FlowCompiler(registry).run(flow, Actor())


def test_demo_session_defaults_are_single_source():
    """业务会话默认值只在插件 knowledge/session.json 一处；引擎/CLI/插件 .py 不得再写死 Demo 字面量。"""
    from pathlib import Path as _P
    from atlas.plugins.demo import DemoPlugin
    defaults = DemoPlugin().session_defaults()
    assert defaults == {"account": "demo_user", "ou": "DEMO System Admin",
                        "division_code": "DIV-DEMO-0001", "language": "English US"}
    # 密码绝不进数据源（§1）：任何 *_PWD/secret/password 键都不允许出现
    assert not any("pwd" in k.lower() or "password" in k.lower() or "secret" in k.lower()
                   for k in defaults)
    # 会话默认值唯一数据源：任何 .py（含插件层）都不得再硬编码这些业务字面量，防多处漂移
    src_root = _P(__file__).resolve().parents[1] / "src/atlas"
    offenders = []
    for py in src_root.rglob("*.py"):
        body = py.read_text(encoding="utf-8")
        for literal in ("demo_user", "DEMO System Admin", "DIV-DEMO-0001"):
            if literal in body:
                offenders.append(f"{py.name}:{literal}")
    assert offenders == [], f"业务字面量硬编码漂移回：{offenders}"

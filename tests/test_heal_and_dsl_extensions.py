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


def test_heal_success_leaves_no_phantom_failure_in_recorder(tmp_path):
    """#81：自愈成功后运行记录必须与 results/flow 终态一致。旧实现第一次失败已被 recorder.step
    的 finally 写成 ok=False/fail_class，成功重试却不落记录——atlas summary 会把一个「已自愈通过」
    的 passed run 按 step 级 fail_class 误计出一个 phantom 失败类（status 取 flow 级、fail_classes
    取 step 级），且无成功取证。修复后就地更正同一条 step 为 healed+通过。"""
    import json as _json
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder
    from atlas.reporting import summarize
    kb = make_kb(tmp_path, True)
    calls = {"n": 0}
    def flaky(actor):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("Timeout")
        return {"v": 1}
    runs = tmp_path / "runs.jsonl"
    rec = RunRecorder("caseHeal", "sit", runs, mode="heal")
    task = FunctionalTask(TaskMetadata("demo", "read", produces=("v",), read_only=True), flaky, "read")
    engine = ExecutionEngine(AtlasConfig(tmp_path, mode="heal"), knowledge=kb, healer=Healer(kb), recorder=rec)
    result = engine.execute(Actor(), task)
    assert result.ok and calls["n"] == 2
    rec.close("passed")
    run = _json.loads(runs.read_text(encoding="utf-8").strip().splitlines()[-1])
    step = next(s for s in run["steps"] if s["step"] == "read")
    assert step["ok"] is True and "fail_class" not in step and step.get("healed") is True
    assert result.pitfall_id == "k1"                 # 自愈归因到命中的已审核坑点
    assert summarize(runs)["fail_classes"] == {}     # 通过的 run 不得被计出任何失败类


def test_can_auto_heal_rejects_control_flow_exceptions(tmp_path):
    """#108：KeyboardInterrupt/SystemExit/GeneratorExit 一律拒绝自愈——它们是
    操作者/宿主的终止信号，不是业务失败。探针实锤的可达路径：draft_pitfall 的
    trigger 恰是异常类名，人审批准一条含 KeyboardInterrupt 的草稿后
    `trigger in text` 命中 + approved + remedy → 旧实现放行 Ctrl-C 就地重试，
    中断被吞掉继续执行，违反 #70 的 interrupted 终态口径。"""
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [
        {"id": "k-int", "trigger": "KeyboardInterrupt", "symptom": "s", "root_cause": "r",
         "remedy": "刷新后重试", "scope": ["demo"], "severity": "medium", "source": "t",
         "status": "approved"},
        {"id": "k-sys", "trigger": "SystemExit", "symptom": "s", "root_cause": "r",
         "remedy": "刷新后重试", "scope": ["demo"], "severity": "medium", "source": "t",
         "status": "approved"},
        {"id": "k-gen", "trigger": "GeneratorExit", "symptom": "s", "root_cause": "r",
         "remedy": "刷新后重试", "scope": ["demo"], "severity": "medium", "source": "t",
         "status": "approved"},
    ], approved=True)
    healer = Healer(kb)
    read_only = FunctionalTask(TaskMetadata("demo", "read", read_only=True),
                               lambda a: None, "read")
    # 修复前这三行全部 True（探针实锤）：知识行存在也绝不放行控制流异常
    assert healer.can_auto_heal(read_only, KeyboardInterrupt()) is False
    assert healer.can_auto_heal(read_only, SystemExit(1)) is False
    assert healer.can_auto_heal(read_only, GeneratorExit()) is False
    # 对照组：同 kb 目录下业务异常仍走正常准入（防线不得反向误伤）
    kb_timeout = make_kb(tmp_path, True)
    assert Healer(kb_timeout).can_auto_heal(read_only, TimeoutError("Timeout")) is True


def test_heal_mode_keyboard_interrupt_propagates_untried(tmp_path):
    """端到端：heal 模式下只读 Task 抛 KeyboardInterrupt 且存在命中知识行时，
    异常必须原样传播、绝不重试——旧实现只靠 trigger 恰不命中侥幸挡住，
    engine 的 `except BaseException` 本就捕 KI，无任何防线。"""
    import json as _json
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [
        {"id": "k-int", "trigger": "KeyboardInterrupt", "symptom": "s", "root_cause": "r",
         "remedy": "刷新后重试", "scope": ["demo"], "severity": "medium", "source": "t",
         "status": "approved"},
    ], approved=True)
    calls = {"n": 0}
    def interrupted(actor):
        calls["n"] += 1
        raise KeyboardInterrupt()
    runs = tmp_path / "runs.jsonl"
    rec = RunRecorder("caseKi", "sit", runs, mode="heal")
    task = FunctionalTask(TaskMetadata("demo", "read", produces=("v",), read_only=True),
                          interrupted, "read")
    engine = ExecutionEngine(AtlasConfig(tmp_path, mode="heal"), knowledge=kb,
                             healer=Healer(kb), recorder=rec)
    with pytest.raises(KeyboardInterrupt):
        engine.execute(Actor(), task)
    assert calls["n"] == 1        # 未自愈重试——Ctrl-C 就是终止信号
    rec.close("interrupted")
    run = _json.loads(runs.read_text(encoding="utf-8").strip().splitlines()[-1])
    step = next(s for s in run["steps"] if s["step"] == "read")
    assert not step.get("healed")  # 不得被记成自愈成功


def test_run_mode_does_not_heal(tmp_path):
    from atlas.engine import ExecutionEngine
    kb = make_kb(tmp_path, True)
    task = FunctionalTask(TaskMetadata("demo", "read", read_only=True),
                          lambda a: (_ for _ in ()).throw(TimeoutError("Timeout")), "read")
    engine = ExecutionEngine(AtlasConfig(tmp_path, mode="run"), knowledge=kb, healer=Healer(kb))
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)


def test_match_pitfall_status_filter_before_longest_match(tmp_path):
    """status 过滤必须在「最长匹配择一」之前：trigger 更长的 pending 草稿不得遮蔽
    同一异常上已审核的坑点；不传 status 时保持最长最具体原义（诊断呈现用）。"""
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [
        {"id": "k-approved", "trigger": "timeout", "symptom": "s", "root_cause": "r",
         "remedy": "已验证修复", "scope": ["demo"], "severity": "medium", "source": "t",
         "status": "approved"},
        {"id": "draft-long", "trigger": "net::err_timeout", "symptom": "s", "root_cause": "r",
         "remedy": "猜测修复", "scope": ["demo"], "severity": "medium", "source": "explore",
         "status": "pending_review"},
    ], approved=True)
    exc = RuntimeError("net::err_timeout timeout waiting")
    # 诊断口径不变：无过滤仍取最长匹配（可以是 pending，供人审看最具体线索）
    assert kb.match_pitfall(exc, "demo")["id"] == "draft-long"
    # 自愈准入口径：只看已审核集，pending 不得遮蔽
    assert kb.match_pitfall(exc, "demo", status="approved")["id"] == "k-approved"


def test_pending_draft_does_not_suppress_legit_heal(tmp_path):
    """端到端：explore 积累的长 trigger pending 草稿同异常并存时，heal 模式仍按
    approved 坑点重试成功，且归因记 approved 那条（旧实现在此静默不重试）。"""
    from atlas.engine import ExecutionEngine
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [
        {"id": "k-approved", "trigger": "timeout", "symptom": "s", "root_cause": "r",
         "remedy": "重读 vm", "scope": ["demo"], "severity": "medium", "source": "t",
         "status": "approved"},
        {"id": "draft-long", "trigger": "net::err_timeout", "symptom": "s", "root_cause": "r",
         "remedy": "猜的", "scope": ["demo"], "severity": "medium", "source": "explore",
         "status": "pending_review"},
    ], approved=True)
    healer = Healer(kb)
    task_meta = TaskMetadata("demo", "read", produces=("v",), read_only=True)
    assert healer.can_auto_heal(FunctionalTask(task_meta, lambda a: None, "read"),
                                 RuntimeError("net::err_timeout timeout")) is True
    calls = {"n": 0}
    def flaky(actor):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("net::err_timeout timeout")
        return {"v": 1}
    task = FunctionalTask(task_meta, flaky, "read")
    engine = ExecutionEngine(AtlasConfig(tmp_path, mode="heal"), knowledge=kb, healer=healer)
    result = engine.execute(Actor(), task)
    assert result.ok and calls["n"] == 2
    assert result.pitfall_id == "k-approved"   # 归因到驱动自愈的已审核知识，不是更长的草稿


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


def test_assert_api_save_as_really_takes_effect():
    """#115 面A：save_as 是真旋钮不是假声明——旧 __init__ 只把它塞进 produces=(save_as,)、
    从不 self.save_as，perform 硬编码返回 {"api_assertion": True}。DSL step 透传 save_as
    （dsl.py:75 排除集不含它），到 write_produces 才 KeyError 点破「声明键与实际键不符」——
    半成品旋钮与同文件 CallEndpoint（save_as 真兑现）漂移，违反 #92 produces 自兑现契约。"""
    from atlas.core import write_produces
    actor = Actor()
    actor.demo_api = type("A", (), {
        "atlas_allowed_operations": {"sale_order_list"},
        "sale_order_list": lambda self, **kw: {"ok": True, "data": {"x": 1}}})()
    task = AssertApi("sale_order_list", {}, {"x": 1}, save_as="my_key")
    assert task.metadata.produces == ("my_key",)
    result = task.perform(actor)
    assert list(result.keys()) == ["my_key"], "perform 必须按声明键返回"
    ctx = {}
    write_produces(ctx, task, result)  # 不再 KeyError：声明与实写一致
    assert ctx["my_key"] is True
    # 默认值不回归：save_as 缺省仍是 api_assertion
    default = AssertApi("sale_order_list", {}, {"x": 1})
    assert list(default.perform(actor).keys()) == ["api_assertion"]


def test_assert_api_dig_path_errors_are_attributable_and_classified():
    """#115 面B：expect 路径打错的四种形态必须归因统一——旧实现三种裸抛 Python 原生异常
    （dict 缺键 KeyError / list 越界 IndexError / 非数字索引 int() ValueError），不带路径
    上下文，classify 分别落 unknown / unknown / contract：同为「路径写错」却落进不同缺陷类，
    atlas summary 无法归因（#58 查无归 data、#61/#113 路径点名同红线）。"""
    from atlas.tasks import _dig
    from atlas.failures import classify_failure, FailureClass
    cases = [
        ({"data": {"name": "x"}}, "data.age", "dict 缺键"),
        ({"data": [1, 2]}, "data.5", "list 越界"),
        ({"data": [1, 2]}, "data.two", "list 非数字索引"),
        ({"data": 42}, "data.name", "标量中间节点"),
    ]
    for value, path, label in cases:
        with pytest.raises(AssertionError) as exc:
            _dig(value, path)
        assert f"API 断言路径不存在：{path}" in str(exc.value), f"{label} 必须点名整条路径"
        assert classify_failure(exc.value) == FailureClass.DATA, f"{label} 应统一归 data"


def test_assert_api_valid_path_still_returns_value():
    """面B 兼容面：正确路径不回归（含负向不能用的越界保护不误伤合法索引）。"""
    from atlas.tasks import _dig
    assert _dig({"data": [{"paymentTerms": "105BL"}]}, "data.0.paymentTerms") == "105BL"
    assert _dig({"data": [1, 2, 3]}, "data.2") == 3
    # 负索引是合法路径（旧 value[int(part)] 支持）——归因修复不得把它误拒成「不是索引」。
    # 探针教训：首版修复用 part.isdigit() 判索引，恰好把 data.-1 翻成 AssertionError。
    assert _dig({"data": [1, 2, 3]}, "data.-1") == 3


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

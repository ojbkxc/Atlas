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


def test_compose_for_goal_detects_self_ring():
    """#107 对抗探针确证的 sound 边界：单个 Task 产出 a 又依赖 a（或产出 {a,b} 依赖 b）
    是自环——递归选择必须先命中 `visiting` 报「循环」，不得静默跳过或死循环。
    现有测只覆盖两 Task 互环；自环走的是 `select` 里 `key in visiting` 的独立分支，
    若哪天把环检测放宽成「已访问就 return」，自环会伪装成可解而返回残缺计划。"""
    from atlas.composition import compose_for_goal
    with pytest.raises(FlowCompositionError, match="循环"):
        compose_for_goal([task("self", ("a",), ("a",))], "a")
    # 多产出部分自环：产 {a,b} 且依 b，goal=a 仍须检出 b 上的环
    with pytest.raises(FlowCompositionError, match="循环"):
        compose_for_goal([task("self2", ("b",), ("a", "b"))], "a")


def test_compose_for_goal_diamond_is_not_a_cycle():
    """#107：菱形共享依赖（c→{a,b}→goal）是有向无环图，必须正常拓扑、绝不误判成环。
    同时钉死 `task in selected` 的身份去重路径：共享上游 c 被两条路径选中只排一次
    （若改成「每次重新 append」→ c 排两遍 → 写副作用双跑，正是 #93 那类漂移）。"""
    from atlas.composition import compose_for_goal
    root = task("c", produces=("c",))
    left = task("a", ("c",), ("a",))
    right = task("b", ("c",), ("b",))
    goal = task("z", ("a", "b"), ("goal",))
    plan = compose_for_goal([root, left, right, goal], "goal")
    names = [t.name for t in plan]
    assert names.count("c") == 1                       # 共享上游只排一次
    assert names.index("c") < names.index("a")         # 拓扑序：依赖先于消费者
    assert names.index("c") < names.index("b")
    assert names[-1] == "z"                            # 产 goal 的任务 z 排在两条依赖之后


def test_compose_for_goal_returns_empty_when_goal_already_provided():
    """#107：目标键已在 provided 里 → select 立即 return → 空计划（no-op）。
    agent.run 消费这条：goal 已就绪时 assess([]) 回落 READ_ONLY、不执行任何 Task。
    若去掉 `if key in provided: return`，会去给「其实已就绪」的目标强找生产者并报错，
    把 no-op 翻成失败——这条边界此前无测。"""
    from atlas.composition import compose_for_goal
    plan = compose_for_goal([task("only", produces=("done",))], "done", provided={"done"})
    assert plan == []
    # provided 打断环：goal=a，p 需 b 产 a、q 需 a 产 b；给了 b 就不再需要 q，只排 p
    p = task("p", ("b",), ("a",))
    q = task("q", ("a",), ("b",))
    assert [t.name for t in compose_for_goal([p, q], "a", provided={"b"})] == ["p"]


def test_compose_for_goal_missing_output_is_reported():
    """目标产出无 Task 能提供时明确报错。"""
    from atlas.composition import compose_for_goal
    with pytest.raises(FlowCompositionError, match="没有 Task"):
        compose_for_goal([task("t", produces=("x",))], "nonexistent")


def test_compose_for_goal_ambiguous_producers_are_named(tmp_path):
    """同一产出键挂两个不同生产者：必须点名报错，绝不 last-wins 静默选一个
    （旧实现会选中后注册的写任务顶掉读任务，agent 风险档被悄悄推高）。"""
    from atlas.composition import compose_for_goal
    s = task("s", produces=("session",))
    read = task("api_read", ("session",), ("customer",))
    write = task("ui_write", ("session",), ("customer",))
    with pytest.raises(FlowCompositionError) as exc:
        compose_for_goal([s, read, write], "customer")
    msg = str(exc.value)
    assert "customer" in msg and "api_read" in msg and "ui_write" in msg
    assert "多个 Task" in msg


def test_compose_for_goal_ambiguity_not_masked_by_dead_alternate():
    """可行生产者 + 死端生产者并存时，报「多生产者歧义」而不是误导的
    「没有 Task 能产出 <死依赖>」——旧实现静默选死端那份，错误信息与目录矛盾。"""
    from atlas.composition import compose_for_goal
    s = task("s", produces=("session",))
    good = task("api_read", ("session",), ("customer",))
    dead = task("ui_dead", ("nonexistent_state",), ("customer",))
    with pytest.raises(FlowCompositionError, match="多个 Task") as exc:
        compose_for_goal([s, good, dead], "customer")
    assert "nonexistent_state" not in str(exc.value)


def test_compose_for_goal_duplicate_registration_is_not_ambiguous():
    """调用方拼接同一份清单（同名重复注册）不算多生产者冲突，按同一产出方去重。"""
    from atlas.composition import compose_for_goal
    s = task("s", produces=("session",))
    read = task("api_read", ("session",), ("customer",))
    read_clone = task("api_read", ("session",), ("customer",))  # 同名不同实例
    plan = compose_for_goal([s, read, read, read_clone], "customer")
    assert [t.name for t in plan] == ["s", "api_read"]


def test_compose_for_goal_same_name_different_definition_is_named():
    """#135 探针实锤：同名去重旧只比 name——两份插件清单各定义同名 Task 但
    read_only/intent 不同时，写那份被静默丢弃，agent 只按幸存的读档评估风险
    （多生产者歧义从同名去重后门溜进来，#84 同红线、#128 contracts 同形态）。
    现比对 frozen metadata：不等值显式点名差异字段。"""
    from atlas.composition import compose_for_goal
    from atlas.core import FunctionalTask
    m_read = TaskMetadata(domain="cust", intent="读客户",
                          produces=("customer",), read_only=True)
    m_write = TaskMetadata(domain="cust", intent="写客户",
                           produces=("customer",), read_only=False)
    read = FunctionalTask(m_read, lambda a: "R", name_override="load_customer")
    write = FunctionalTask(m_write, lambda a: "W", name_override="load_customer")
    with pytest.raises(FlowCompositionError, match="不同定义") as exc:
        compose_for_goal([read, write], "customer")
    msg = str(exc.value)
    assert "load_customer" in msg and "read_only" in msg and "intent" in msg


def test_compose_for_goal_same_name_same_definition_dedupes_by_value():
    """零涟漪面（#115）：等值定义的同名实例（lambda 不同、metadata 相同）
    仍按同一产出方去重放行——不翻正常路径。"""
    from atlas.composition import compose_for_goal
    from atlas.core import FunctionalTask
    m = TaskMetadata(domain="cust", intent="读客户",
                     produces=("customer",), read_only=True)
    a = FunctionalTask(m, lambda x: 1, name_override="load_customer")
    b = FunctionalTask(m, lambda x: 2, name_override="load_customer")
    plan = compose_for_goal([a, b, a], "customer")
    assert [t.name for t in plan] == ["load_customer"]


def test_compose_skips_tasks_when_dependency_is_provided():
    """provided 集合里的依赖不需要额外 Task 产出。"""
    tasks = [task("save", ("customer",), ("order",))]
    result = compose(tasks, provided={"customer"})
    assert [t.name for t in result] == ["save"]


def test_compose_dedupes_repeated_task_instance():
    """同一 Task 实例在清单里重复出现（调用方拼接清单笔误）：旧实现排两次，
    执行链逐个 perform——写 Task 真实副作用跑两遍（建两单），配额只记一次，
    护栏与现场漂移。compose_for_goal 的 selected 本有去重，但 engine=None 时
    agent/dsl/state_machine 都直接调 compose()，入口必须同口径。
    按身份（is）去重：等值的两个不同生产者实例仍保留，冲突由 for_goal 点名。"""
    calls = {"n": 0}
    def side(actor):
        calls["n"] += 1
        return {"v": calls["n"]}
    t = FunctionalTask(TaskMetadata("demo", "create", produces=("v",)), side, "create")
    ordered = compose([t, t])
    assert len(ordered) == 1
    Actor().attempts_to(*ordered)
    assert calls["n"] == 1
    # 等值不同实例是两份不同身份，不得被误合（多生产者语义归 for_goal 管）
    a = FunctionalTask(TaskMetadata("demo", "x", produces=("k",)), lambda act: {"k": 1}, "x")
    b = FunctionalTask(TaskMetadata("demo", "x", produces=("k",)), lambda act: {"k": 1}, "x")
    assert len(compose([a, b])) == 2


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


def test_lies_about_produces_key_fails_loudly():
    """不变量「produces 必须与 perform 实际读写一致」强制落地。旧兜底
    `result[key] if key in result else result`：声明 order_no 而 perform 返回
    {'orderNo': …}（驼峰笔误）时把**整个 dict** 静默塞进 ctx——谎报 Task 照样
    「成功」，下游把 dict 当单号插值/断言，烂数据漂到远处才炸且报错不再指回元数据。
    必须当场点名声明键与实际键。"""
    from atlas.core import write_produces
    liar = FunctionalTask(TaskMetadata("demo", "read", produces=("order_no",)),
                          lambda a: {"orderNo": "SO-1"}, "liar")
    ctx = {}
    with pytest.raises(KeyError, match="order_no"):
        write_produces(ctx, liar, liar.perform(Actor()))
    assert ctx == {}   # 校验失败不得留下半写状态


def test_single_value_task_honors_non_dict_result():
    """result 非 dict 是合法路径（单值 Task）：整体赋给每个声明键，与历史语义一致。"""
    from atlas.core import write_produces
    one = FunctionalTask(TaskMetadata("demo", "v", produces=("page",)), lambda a: "P", "one")
    ctx = {}
    write_produces(ctx, one, one.perform(Actor()))
    assert ctx["page"] == "P"


def test_both_execution_paths_share_the_produces_contract(tmp_path):
    """attempts_to 与 engine.execute 两条执行路径必须同一契约（旧实现两处各有一份
    兜底副本，改一漏一必漂移）：谎报 produces 在两条路径都以 KeyError 暴露。"""
    from pathlib import Path
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine
    liar = FunctionalTask(TaskMetadata("demo", "r", produces=("k",)),
                          lambda a: {"other": 1}, "liar")
    with pytest.raises(KeyError, match="'k'"):
        Actor().attempts_to(liar)
    with pytest.raises(KeyError, match="'k'"):
        ExecutionEngine(AtlasConfig(Path(tmp_path))).execute(Actor(), liar)


def test_skipped_step_does_not_write_produces(tmp_path):
    """环境跳过步骤没真执行：声明键不得被 None 静默塞进 ctx——下游读到
    「有键但为 None」比 KeyError 更难归因（skip 语义 = 数据条件未满足）。"""
    import json
    from atlas.config import AtlasConfig
    from atlas.dsl import FlowCompiler, TaskRegistry
    from atlas.failures import EnvBlocked
    registry = TaskRegistry()
    registry.register("boom", lambda: FunctionalTask(
        TaskMetadata("x", "boom", produces=("never_written",)),
        lambda a: (_ for _ in ()).throw(EnvBlocked("no data")), "boom"))
    flow = {"steps": [{"task": "boom", "on_env_blocked": "skip"}], "engine": AtlasConfig(tmp_path)}
    actor = Actor()
    FlowCompiler(registry).run(flow, actor)
    assert "never_written" not in actor.ctx
    record = json.loads((tmp_path / "reports/runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["steps"][0]["fail_class"] == "env_blocked"   # 失败定性不变


def test_functional_task_rejects_non_callable_action_at_construction():
    """#125：action 非可调用（字符串/None）必须构造期点名——旧实现一路放行到 perform()
    才裸 `TypeError: 'str' object is not callable`，不提 Task 名与 intent，归因漂成
    引擎缺陷（与 #119 面B step_N 非可调用、#91 元数据契约同口径：坏形态前移构造期）。
    兼容面（#115）：lambda 等合法 callable 正常构造，DSL 三处 bound-lambda 路径零涟漪。"""
    from atlas.core import FunctionalTask, TaskMetadata
    meta = TaskMetadata("demo", "read", produces=("x",))
    with pytest.raises(TypeError, match="必须是可调用"):
        FunctionalTask(meta, "pages.read_pt")
    with pytest.raises(TypeError, match="必须是可调用"):
        FunctionalTask(meta, None)
    # 合法 callable 路径不翻错（lambda / 可调用类实例）
    ok = FunctionalTask(meta, lambda a: {"x": 1}, "read")
    assert ok.perform(Actor()) == {"x": 1}


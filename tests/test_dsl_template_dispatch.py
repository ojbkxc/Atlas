"""`{{slot}}` 骨架实例化 + `dispatch` 路由（吸收自 LiteFlow 的 replaceAbstractChain / SWITCH）。

覆盖两条红线的落点：
- 骨架没填完绝不产出可执行 flow（未填的 `{{customer}}` 一旦当客户号发给系统，就是假数据假通过）；
- 路由的目标集、default、`on` 字段全部编译期定死（引擎不猜运行时谁写 ctx，也不给隐式通道）。
"""
import json
import pathlib
import re

import pytest

from atlas.actor import Actor
from atlas.config import AtlasConfig
from atlas.core import FunctionalTask, TaskMetadata
from atlas.dsl import FlowCompiler, FlowValidationError, TaskRegistry, _gated_map
from atlas.failures import EnvBlocked

SKELETON = """id: so-default-pt-{{bu}}
requires:
  source_order:
    entity: SalesOrder
    strategy: find_only
steps:
  - task: open_doc_list
    page_key: sales_order
  - task: select_customer
    code: "{{customer}}"
    channel: ui
"""


def registry_with(**tasks) -> TaskRegistry:
    """建一个离线 registry：`名字=(produces, 动作)`，动作缺省为「原样回吐参数」。"""
    registry = TaskRegistry()
    for name, spec in tasks.items():
        # (("mode",), 动作) 是「产出+动作」二元组；("doc_list_ready",) 是纯产出元组
        produces, action = (spec if isinstance(spec, tuple) and len(spec) == 2
                            and isinstance(spec[0], tuple) else (spec, None))
        def make(name=name, produces=tuple(produces), action=action, **kwargs):
            task = FunctionalTask(
                TaskMetadata("demo", name, produces=tuple(produces)),
                action or (lambda actor, p=dict(kwargs), k=tuple(produces):
                           {key: p.get(key) for key in k} or {name: True}), name)
            task.params = dict(kwargs)          # 模板/步骤参数留痕，供断言取用
            return task

        registry.register(name, make)
    return registry


def write(tmp_path, name, text):
    path = pathlib.Path(tmp_path) / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- {{slot}} 骨架
def test_multi_bu_instances_share_one_skeleton(tmp_path):
    """多 BU 参数化：同一骨架 + 不同 slots → 两份各自可编译的 flow（骨架只有一份真相）。"""
    registry = registry_with(open_doc_list=("doc_list_ready",),
                             select_customer=("customer",))
    write(tmp_path, "skeleton.yaml", SKELETON)
    compiler = FlowCompiler(registry)
    cvt = compiler.load(write(tmp_path, "bu_cvt.yaml",
                              "template: skeleton.yaml\nslots:\n  bu: CVT\n  customer: CUST0000001\n"))
    ktki = compiler.load(write(tmp_path, "bu_ktki.yaml",
                               "template: skeleton.yaml\nslots:\n  bu: KTKI\n  customer: CUST0000002\n"))
    assert cvt["id"] == "so-default-pt-CVT" and ktki["id"] == "so-default-pt-KTKI"
    assert cvt.md5 != ktki.md5, "不同 BU 实例必须不同指纹（否则缓存会把 CVT 的编译结果给 KTKI 跑）"
    def code_of(flow):
        task = compiler.compile(flow)[1]
        while hasattr(task, "inner"):           # channel 覆盖层包着真实 Task
            task = task.inner
        return task.params["code"]

    assert code_of(cvt) == "CUST0000001"
    assert code_of(ktki) == "CUST0000002"
    assert code_of(cvt) == "CUST0000001"        # 缓存不串实例（同 md5 才复用）


def test_unfilled_slot_never_produces_executable_flow(tmp_path):
    """少填一个槽位 = 编译期拒绝，而不是把字面量 `{{customer}}` 当客户号发出去。"""
    registry = registry_with(open_doc_list=("doc_list_ready",),
                             select_customer=("customer",))
    write(tmp_path, "skeleton.yaml", SKELETON)
    instance = write(tmp_path, "bu_half.yaml", "template: skeleton.yaml\nslots:\n  bu: CVT\n")
    with pytest.raises(FlowValidationError, match="模板槽位未填全") as caught:
        FlowCompiler(registry).compile(FlowCompiler(registry).load(instance))
    message = str(caught.value)
    assert "customer" in message and "^" in message
    assert "skeleton.yaml" in message and "bu_half.yaml" in message   # 骨架行号 + 是哪个实例填的
    assert re.search(r"skeleton\.yaml:10:", message.replace("\\", "/")), message


def test_abstract_skeleton_is_not_runnable_on_its_own(tmp_path):
    """骨架本身不可执行（LiteFlow 的 abstract 语义）：直接 load 骨架也要点名未填槽位。"""
    registry = registry_with(open_doc_list=("doc_list_ready",),
                             select_customer=("customer",))
    skeleton = write(tmp_path, "skeleton.yaml", SKELETON)
    compiler = FlowCompiler(registry)
    with pytest.raises(FlowValidationError, match="模板槽位未填全"):
        compiler.compile(compiler.load(skeleton))


def test_slot_values_must_be_single_line_scalars(tmp_path):
    """容器/多行槽位会把骨架行号挤走，^ 指针就指错行——当场拒收，不给错行号。"""
    registry = registry_with(open_doc_list=("doc_list_ready",),
                             select_customer=("customer",))
    write(tmp_path, "skeleton.yaml", SKELETON)
    for value in ('{"a": 1}', '[1, 2]', '"第一行\\n第二行"'):
        instance = write(tmp_path, "bu_bad.yaml",
                         f"template: skeleton.yaml\nslots:\n  bu: CVT\n  customer: {value}\n")
        with pytest.raises(FlowValidationError, match="单行标量"):
            FlowCompiler(registry).load(instance)


def test_instance_must_not_carry_its_own_steps(tmp_path):
    """实例文件只带 template+slots：多出来的键（比如自己再写一段 steps）直接拒——
    骨架与实例各持一半流程，出事时没人知道真相在哪份文件里。"""
    registry = registry_with(open_doc_list=("doc_list_ready",),
                             select_customer=("customer",))
    write(tmp_path, "skeleton.yaml", SKELETON)
    instance = write(tmp_path, "bu_extra.yaml",
                     "template: skeleton.yaml\nslots: {bu: CVT, customer: C1}\n"
                     "steps:\n  - task: open_doc_list\n")
    with pytest.raises(FlowValidationError, match="只能再带 slots"):
        FlowCompiler(registry).load(instance)


def test_skeleton_cycle_is_rejected(tmp_path):
    """骨架互相引用必须被拒（不是靠递归深度撞栈，是按真实路径点名）。"""
    write(tmp_path, "a.yaml", "template: b.yaml\nslots: {}\n")
    write(tmp_path, "b.yaml", "template: a.yaml\nslots: {}\n")
    with pytest.raises(FlowValidationError, match="循环引用"):
        FlowCompiler(registry_with(any_task=())).load(pathlib.Path(tmp_path) / "a.yaml")


# ---------------------------------------------------------------- dispatch 路由
FLOW = """id: routed
steps:
  - id: seed
    task: seed
    mode: {mode}
  - id: pick
    dispatch:
      on: mode
      to:
        A: [left, after]
        B: right
        default: after
  - id: left
    task: branch
    tag: left
  - id: right
    task: branch
    tag: right
  - id: after
    task: after
"""


def routed_registry(trace):
    return registry_with(seed=(("mode",), lambda actor, t=trace: t.append("seed") or {"mode": "B"}),
                         branch=((), lambda actor, t=trace: t.append("branch") or None),
                         after=((), lambda actor, t=trace: t.append("after") or None))


def run_flow(tmp_path, mode="B"):
    """按 FLOW 模板跑一遍，返回 (执行过的步骤名, actor)。

    注意 seed 直接回吐 mode=B（参数里的 {mode} 只用于占位），路由取值来自 ctx。
    """
    trace: list[str] = []
    registry = registry_with(
        seed=(("mode",), lambda actor: {"mode": mode}),
        branch=((), lambda actor, t=trace: t.append("branch") or None),
        after=((), lambda actor, t=trace: t.append("after") or None))
    text = FLOW.replace("{mode}", "ignored")
    path = write(tmp_path, "routed.yaml", text)
    result = FlowCompiler(registry).run(FlowCompiler(registry).load(path), Actor())
    return trace, result


def test_dispatch_runs_only_the_selected_branch(tmp_path):
    """`on: mode` 取到 B → 只跑 right 分支；left/after 整段不在路径上。"""
    trace, result = run_flow(tmp_path, mode="B")
    assert result["status"] == "passed"
    assert trace == ["branch"]                      # 只有 right 被真实执行


def test_dispatch_can_route_a_multi_step_branch(tmp_path):
    """A → [left, after]：分支是一段步骤序列，不是单个步骤。"""
    trace, result = run_flow(tmp_path, mode="A")
    assert trace == ["branch", "after"]


def test_dispatch_uses_default_when_value_unmatched(tmp_path):
    """取值没命中 → 走 default（default 是显式分支，不是隐式通道）。"""
    trace, result = run_flow(tmp_path, mode="ZZZ")
    assert trace == ["after"]


def test_dispatch_compares_string_form_of_scalar_values(tmp_path):
    """YAML 键 `1003` 解析成 int，ctx 里常是 str("1003")（报文状态码惯用形态）——
    两侧按字符串对齐才不至于每次静默走 default。"""
    trace_box: list[str] = []
    registry = registry_with(
        seed=(("status",), lambda actor: {"status": "1003"}),
        hit=((), lambda actor: trace_box.append("hit") or None),
        miss=((), lambda actor: trace_box.append("miss") or None))
    path = write(tmp_path, "status.yaml", """
steps:
  - id: seed
    task: seed
  - dispatch:
      on: status
      to:
        1003: hit
        default: miss
  - id: hit
    task: hit
  - id: miss
    task: miss
""")
    FlowCompiler(registry).run(FlowCompiler(registry).load(path), Actor())
    assert trace_box == ["hit"]


def test_dispatch_default_is_mandatory(tmp_path):
    """缺 default 直接拒编译（无隐式通道）；报错带 ^ 指到 to 映射。"""
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: None))
    path = write(tmp_path, "nodefault.yaml", """
steps:
  - id: seed
    task: seed
  - dispatch:
      on: mode
      to:
        A: branch
  - id: branch
    task: branch
""")
    with pytest.raises(FlowValidationError, match="缺 default") as caught:
        FlowCompiler(registry).compile(FlowCompiler(registry).load(path))
    assert "^" in str(caught.value)


def test_dispatch_target_must_be_a_declared_step_id(tmp_path):
    """目标 id 不存在 = 未定义引用：编译期点名，并列出已声明的 id。"""
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: None))
    path = write(tmp_path, "ghost_target.yaml", """
steps:
  - id: seed
    task: seed
  - dispatch:
      on: mode
      to:
        A: nowhere
        default: branch
  - id: branch
    task: branch
""")
    with pytest.raises(FlowValidationError, match="不存在") as caught:
        FlowCompiler(registry).compile(FlowCompiler(registry).load(path))
    message = str(caught.value)
    assert "nowhere" in message and "'branch'" in message and "^" in message


def test_dispatch_on_must_be_a_declared_ctx_field(tmp_path):
    """路由只读已声明的 ctx 字段：`on` 写一个没人产出的键就拒编译（不猜谁在运行时塞进 ctx）。"""
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: None),
                             after=((), lambda actor: None))
    path = write(tmp_path, "undeclared.yaml", """
steps:
  - id: seed
    task: seed
  - dispatch:
      on: someone_elses_field
      to:
        A: branch
        default: after
  - id: branch
    task: branch
  - id: after
    task: after
""")
    with pytest.raises(FlowValidationError, match="不是已声明 ctx 字段") as caught:
        FlowCompiler(registry).compile(FlowCompiler(registry).load(path))
    assert "mode" in str(caught.value)             # 给出真实可用的键，不让人猜


def test_dispatch_cannot_route_to_a_step_already_passed(tmp_path):
    """目标必须在本路由之后：线性列表没有回边，指回前面那步既不会重跑也不该静默失效。"""
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: None))
    path = write(tmp_path, "backward.yaml", """
steps:
  - id: seed
    task: seed
  - id: branch
    task: branch
  - dispatch:
      on: mode
      to:
        A: branch
        default: branch
""")
    with pytest.raises(FlowValidationError, match="只能向后跳"):
        FlowCompiler(registry).compile(FlowCompiler(registry).load(path))


def test_duplicate_step_id_is_rejected(tmp_path):
    """步骤 id 重复 = dispatch 按 id 找人时出现两个答案。"""
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: None))
    path = write(tmp_path, "dup.yaml", """
steps:
  - id: seed
    task: seed
  - id: branch
    task: branch
  - id: branch
    task: branch
  - dispatch:
      on: mode
      to: {A: branch, default: branch}
""")
    with pytest.raises(FlowValidationError, match="重复"):
        FlowCompiler(registry).compile(FlowCompiler(registry).load(path))


def test_two_routers_cannot_claim_the_same_branch(tmp_path):
    """一处分支被两个路由认领 = 运行期按谁的决定走没有定义（#84/#128 同键多生产者同红线）。"""
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: None),
                             other=(("flag",), lambda actor: {"flag": "A"}))
    path = write(tmp_path, "shared.yaml", """
steps:
  - id: seed
    task: seed
  - id: first
    dispatch:
      on: mode
      to: {A: branch, default: other}
  - dispatch:
      on: flag
      to: {A: branch, default: other}
  - id: branch
    task: branch
  - id: other
    task: other
""")
    with pytest.raises(FlowValidationError, match="争一个分支"):
        FlowCompiler(registry).compile(FlowCompiler(registry).load(path))


def test_unclaimed_branch_step_runs_unconditionally(tmp_path):
    """现行规格钉桩（lead 裁决取档 3：文档+注释+测桩，不加 exclusive）：
    删掉 `to:` 里的一条 lane，原分支步骤变成**无主步骤**——`_gated_map` 只过滤「被某个路由
    认领」的步骤，无主步骤恒执行且编译零警告。这是取舍不是漏检：判「这步本该属于本路由」要猜
    作者的分支边界（启发式，会误报合法落空步骤），违「不猜」红线；收紧的预留方案是显式
    `dispatch.exclusive`，等真实多 lane flow 出现再上。
    本测锁三件事：① 无主步骤照常执行（left 在 trace 里）② 编译不拒绝（status 不是失败）
    ③ 被认领但未选中的步骤仍不执行（right 缺席）——将来谁想翻「无主=拒绝」的语义，
    必须先过这条测并过人审，不得无声改。"""
    trace: list[str] = []
    registry = registry_with(
        seed=(("mode",), lambda actor: {"mode": "A"}),
        left=((), lambda actor: trace.append("left") or None),
        right=((), lambda actor: trace.append("right") or None),
        after=((), lambda actor: trace.append("after") or None))
    path = write(tmp_path, "lane_removed.yaml", """
steps:
  - id: seed
    task: seed
  - id: pick
    dispatch:
      on: mode
      to:
        B: right
        default: after
  - id: left          # 原 A lane 的目标，lane 被删后没人认领
    task: left
  - id: right
    task: right
  - id: after
    task: after
""")
    compiler = FlowCompiler(registry)
    flow = compiler.load(path)
    tasks = compiler.validate_flow(flow)     # 编译期不拒绝：无主 ≠ 非法
    assert len(tasks) == 5
    result = compiler.run(flow, Actor())
    assert result["status"] == "passed"
    assert trace == ["left", "after"]        # 无主 left 恒执行；right 未选中；after 走 default


def test_missing_ctx_value_terminates_as_env_blocked(tmp_path):
    """声明过但运行时取不到值（产出步骤被 skip 掉）：EnvBlocked 明确终止，绝不静默走 default
    ——那会把「没数据」洗成「走了兜底分支」的假通过。"""
    registry = registry_with(
        blocked=(("mode",), lambda actor: (_ for _ in ()).throw(EnvBlocked("缺数据"))),
        branch=((), lambda actor: None),
        after=((), lambda actor: None))
    path = write(tmp_path, "noblock.yaml", """
id: no-value
steps:
  - id: seed
    task: blocked
    on_env_blocked: skip
  - dispatch:
      on: mode
      to: {A: branch, default: after}
  - id: branch
    task: branch
  - id: after
    task: after
""")
    compiler = FlowCompiler(registry)
    with pytest.raises(EnvBlocked, match="运行时不在 ctx"):
        compiler.run(compiler.load(path), Actor())
    # flow 级 on_env_blocked: skip 时归成 env_blocked 终态（与红线 #7 一致：不是 failed）
    actor = Actor()
    result = compiler.run({**compiler.load(path), "on_env_blocked": "skip"}, actor)
    assert result["status"] == "env_blocked"


def test_unselected_branch_leaves_no_step_record(tmp_path):
    """未选中分支不进 runs.jsonl：它既不是通过也不是被环境挡，压根不在本次路径上。"""
    config = AtlasConfig(tmp_path, mode="run")
    executed: list[str] = []
    registry = registry_with(
        seed=(("mode",), lambda actor: {"mode": "B"}),
        branch=((), lambda actor: executed.append("branch") or None),
        after=((), lambda actor: executed.append("after") or None))
    path = write(tmp_path, "recorded.yaml", """
id: routed-record
steps:
  - id: seed
    task: seed
  - id: pick
    dispatch:
      on: mode
      to: {A: left, B: right, default: after}
  - id: left
    task: after
  - id: right
    task: branch
  - id: after
    task: after
""")
    compiler = FlowCompiler(registry)
    result = compiler.run({**compiler.load(path), "engine": config}, Actor())
    assert result["status"] == "passed"
    assert executed == ["branch"]
    record = json.loads((tmp_path / "reports" / "runs.jsonl").read_text(encoding="utf-8"))
    steps = [item["step"] for item in record["steps"]]
    assert steps == ["seed", "dispatch:pick", "branch"]   # 路由节点自身有记录，未选中的没有
    assert "after" not in steps                    # 未选中的 left/after 一步都没记
    # 留痕（#106 同族补网）：路由节点的 step 记录带 dispatch 决策——取值与去向落盘，
    # 事后能回答「这轮为何走 right」；其他步骤不挂该键（留痕只属于做决策的那一步）。
    gate_record = next(item for item in record["steps"] if item["step"] == "dispatch:pick")
    assert gate_record["dispatch"] == [
        {"gate": "pick", "on": "mode", "value": "B", "selected": ["right"]}]
    assert all("dispatch" not in item for item in record["steps"]
               if item["step"] != "dispatch:pick")


def test_route_decision_is_auditable_in_ctx(tmp_path):
    """每次路由的取值与去向留在 ctx：事后能回答「这轮到底走的哪条分支」。"""
    trace: list[str] = []
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "Z"}),
                             branch=((), lambda actor: trace.append("b") or None),
                             after=((), lambda actor: trace.append("a") or None))
    path = write(tmp_path, "audit.yaml", """
steps:
  - id: seed
    task: seed
  - id: pick
    dispatch:
      on: mode
      to: {A: branch, default: after}
  - id: branch
    task: branch
  - id: after
    task: after
""")
    actor = Actor()
    FlowCompiler(registry).run(FlowCompiler(registry).load(path), actor)
    assert actor.ctx["_dispatch_selected"] == {"pick": ["after"]}
    assert actor.ctx["_dispatch_routes"] == [
        {"gate": "pick", "on": "mode", "value": "Z", "selected": ["after"]}]


def test_dispatch_step_without_task_is_a_pure_router(tmp_path):
    """纯路由节点（只写 dispatch 不写 task）合法：占位步骤不做业务动作，只选分支。"""
    trace: list[str] = []
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: trace.append("branch") or None),
                             after=((), lambda actor: trace.append("after") or None))
    path = write(tmp_path, "pure.yaml", """
steps:
  - id: seed
    task: seed
  - dispatch:
      on: mode
      to: {A: branch, default: after}
  - id: branch
    task: branch
  - id: after
    task: after
""")
    compiler = FlowCompiler(registry)
    assert [t.name for t in compiler.compile(compiler.load(path))][1] == "dispatch:#2"
    compiler.run(compiler.load(path), Actor())
    assert trace == ["branch"]


def test_bare_on_key_and_bare_yaml_lane_values_are_handled(tmp_path):
    """YAML 1.1 的 `on:` 会被解析成布尔键，`to: {on: x}` 的裸 on 同理：
    前者归一（规格定的键名），后者拒绝（布尔键与 ctx 字符串永远匹配不上，静默走 default）。"""
    registry = registry_with(seed=(("mode",), lambda actor: {"mode": "A"}),
                             branch=((), lambda actor: None),
                             after=((), lambda actor: None))
    good = write(tmp_path, "bare_on.yaml", """
steps:
  - id: seed
    task: seed
  - dispatch:
      on: mode
      to: {A: branch, default: after}
  - id: branch
    task: branch
  - id: after
    task: after
""")
    assert len(FlowCompiler(registry).compile(FlowCompiler(registry).load(good))) == 4
    bad = write(tmp_path, "bare_lane.yaml", """
steps:
  - id: seed
    task: seed
  - dispatch:
      on: mode
      to: {on: branch, default: after}
  - id: branch
    task: branch
  - id: after
    task: after
""")
    with pytest.raises(FlowValidationError, match="YAML 裸布尔"):
        FlowCompiler(registry).compile(FlowCompiler(registry).load(bad))


# ---------------------------------------------------------------- 兼容与文档面
def test_guard_docstring_points_at_dispatch_as_the_upgrade():
    """dispatch 是 on_env_blocked 的推荐升级：这一条写在守卫的文档字符串里（兼容性说明的单一来源）。"""
    from atlas.dsl import _GuardedTask
    assert "dispatch" in (_GuardedTask.__doc__ or "")


def test_flows_without_dispatch_compile_and_run_unchanged(tmp_path):
    """没用 dispatch 的 flow 行为零变化（线性列表、顺序执行、全步记录）。"""
    trace: list[str] = []
    registry = registry_with(one=((), lambda actor: trace.append("one") or None),
                             two=((), lambda actor: trace.append("two") or None))
    path = write(tmp_path, "plain.yaml", "steps:\n  - task: one\n  - task: two\n")
    compiler = FlowCompiler(registry)
    assert _gated_map(compiler.load(path)) == {}
    result = compiler.run(compiler.load(path), Actor())
    assert result["status"] == "passed" and trace == ["one", "two"]

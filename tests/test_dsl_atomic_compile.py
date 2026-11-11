"""flow 编译的「候选校验 → 原子应用」+ md5 缓存 + ^ 指针报错（吸收自 LiteFlow 的纪律）。

红线对照：
- 全有或全无 = 坏 flow 绝不带着半成品 Task 列表进入执行，也绝不在结构还没过的时候
  去占建单配额/造数据（AGENTS 红线 #14「禁止让 create 先建单再查配额」同一形状）；
- ^ 指针 = 未定义引用必须点名到源文本行列，作者不用回头猜哪一行写错（#90/#113 归因纪律）；
- md5 缓存只在进程内复用，不落盘（落盘 = 制造「磁盘产物 ≠ 当前代码」的脏状态）。
"""
import pathlib

import pytest

from atlas.actor import Actor
from atlas.core import FunctionalTask, TaskMetadata
from atlas.dsl import FlowCompiler, FlowValidationError, TaskRegistry
from atlas.failures import EnvBlocked
from atlas.probes import ProbeResult


class CountingRegistry(TaskRegistry):
    """记录工厂调用次数的 registry——用来证明「拒绝时零构造」。"""

    def __init__(self):
        super().__init__()
        self.built: list[str] = []

    def add(self, name, produces=None, fail=None):
        keys = tuple(produces if produces is not None else (name,))

        def make(**kwargs):
            self.built.append(name)
            if fail is not None:               # 模拟 AssertApi 那种「构造期校验参数」的工厂
                raise fail
            return FunctionalTask(TaskMetadata("demo", name, produces=keys),
                                  lambda actor, k=keys, n=name: {key: n for key in k}, name)

        self.register(name, make)


class RecordingProvider:
    """假 DataProvider：记录 supply 被调了几次（坏 flow 必须在供给之前就被拒）。"""

    def __init__(self):
        self.calls: list[str] = []

    def supply(self, requirement):
        self.calls.append(requirement.entity)
        return ProbeResult(entity=requirement.entity, data={"orderNo": "SO1"}, action="find")


def write(tmp_path, text, name="flow.yaml"):
    path = pathlib.Path(tmp_path) / name
    path.write_text(text, encoding="utf-8")
    return path


def compile_file(registry, path):
    """load 与 compile 用不同实例：源文本形态跟着 flow 走，定位与缓存都不依赖实例复用。"""
    return FlowCompiler(registry).compile(FlowCompiler(registry).load(path))


# ---------------------------------------------------------------- 原子性（全有或全无）
def test_unknown_task_rejects_whole_flow_with_zero_construction(tmp_path):
    """第 3 步引用不存在的 Task：整个 flow 被拒，且**一个 Task 都没构造**。

    旧实现边遍历边构造，第 3 步才炸——前两步的工厂（legacy 模块加载、建单脚本包装）已经跑过，
    对象留在世上；「不存在半成品列表进入执行」只靠异常传播兜着，校验与构造之间没有阶段边界。
    """
    registry = CountingRegistry()
    registry.add("alpha")
    registry.add("beta")
    path = write(tmp_path, "steps:\n  - task: alpha\n  - task: beta\n  - task: ghost\n")
    compiler = FlowCompiler(registry)
    with pytest.raises(FlowValidationError, match="未注册的 Task"):
        compiler.compile(compiler.load(path))
    assert registry.built == []            # 拒绝得干净：零构造，不留半成品
    assert compiler._cache == {}           # 失败的编译不进缓存


def test_run_executes_nothing_and_supplies_nothing_when_flow_is_invalid(tmp_path):
    """坏 flow 连数据供给都不该发生：requires 会占建单配额、真实建单（红线 #14）。

    旧实现 run() 先 provision 再 compile——第 2 步的 Task 名写错时，依赖数据已经按
    find_or_create 真实取过甚至造过，报错却还在后面。
    """
    registry = CountingRegistry()
    registry.add("alpha")
    provider = RecordingProvider()
    path = write(tmp_path, "requires:\n  source_order:\n    entity: SalesOrder\n"
                           "steps:\n  - task: alpha\n  - task: ghost\n")
    actor = Actor()
    with pytest.raises(FlowValidationError, match="ghost"):
        FlowCompiler(registry, provider).run(
            FlowCompiler(registry, provider).load(path), actor)
    assert provider.calls == []            # 没有占配额、没有造数
    assert registry.built == []            # 没有构造任何 Task
    assert "alpha" not in actor.ctx        # 没有半成品进入执行


def test_factory_failure_midway_does_not_leak_partial_list():
    """工厂自己报错（参数不对）：整份候选列表丢弃，调用方拿不到前几步的半成品。"""
    registry = CountingRegistry()
    registry.add("alpha")
    registry.add("broken", fail=ValueError("broken 至少需要一个 expect 条件"))
    compiler = FlowCompiler(registry)
    with pytest.raises(ValueError, match="expect"):
        compiler.compile({"steps": [{"task": "alpha"}, {"task": "broken"}]})
    assert compiler._cache == {}           # 半成品没进缓存，也就没人能拿到「前 1 步的 flow」


def test_all_problems_reported_together_with_first_caret(tmp_path):
    """多处问题一次报全（LiteFlow 同款）：首条带 ^，其余列清单——修一处再编译又炸下一处最费人。"""
    registry = CountingRegistry()
    registry.add("alpha")
    path = write(tmp_path, "steps:\n  - task: alpha\n    channel: nope\n  - task: ghost\n")
    with pytest.raises(FlowValidationError) as caught:
        compile_file(registry, path)
    message = str(caught.value)
    assert "channel 只支持 ui/api/auto" in message
    assert "另有 1 处编译问题" in message and "ghost" in message
    assert registry.built == []


# ---------------------------------------------------------------- md5 编译缓存
def test_compile_reuses_result_when_md5_unchanged(tmp_path):
    """同一实例、同一源文本 md5 → 复用已编译的 Task 列表（工厂不再被调用）。

    真实收益点：`FlowCompiler.run()` 内部经 validate_flow 再编译一次；批量回归对同一文件
    反复 load+compile 也走这里。
    """
    registry = CountingRegistry()
    registry.add("alpha")
    registry.add("beta")
    path = write(tmp_path, "steps:\n  - task: alpha\n  - task: beta\n")
    compiler = FlowCompiler(registry)
    flow = compiler.load(path)
    first = compiler.compile(flow)
    second = compiler.compile(flow)
    assert registry.built == ["alpha", "beta"]      # 只构造过一次
    assert second[0] is first[0]                    # 复用同一批 Task 实例（LiteFlow 同款语义）
    compiler.compile(compiler.load(path))           # 文本未变，重新 load 也命中
    assert registry.built == ["alpha", "beta"]


def test_cache_invalidates_when_source_text_changes(tmp_path):
    """md5 变了就重编译：改一行流程不能拿到旧列表（否则跑的是上一版步骤）。"""
    registry = CountingRegistry()
    registry.add("alpha")
    registry.add("beta")
    path = write(tmp_path, "steps:\n  - task: alpha\n")
    compiler = FlowCompiler(registry)
    assert len(compiler.compile(compiler.load(path))) == 1
    path.write_text("steps:\n  - task: alpha\n  - task: beta\n", encoding="utf-8")
    assert [t.name for t in compiler.compile(compiler.load(path))] == ["alpha", "beta"]
    assert registry.built == ["alpha", "alpha", "beta"]


def test_cache_invalidates_when_registry_changes(tmp_path):
    """注册表代次变了也失效：同名 Task 换了工厂，旧缓存会放行上一版实现。"""
    registry = CountingRegistry()
    registry.add("alpha")
    path = write(tmp_path, "steps:\n  - task: alpha\n")
    compiler = FlowCompiler(registry)
    first = compiler.compile(compiler.load(path))
    registry.add("alpha")                           # 重新注册（generation +1）
    second = compiler.compile(compiler.load(path))
    assert registry.built == ["alpha", "alpha"]
    assert second[0] is not first[0]


def test_previously_rejected_flow_compiles_after_registering_the_task(tmp_path):
    """代次失效的另一面：先拒后注册同名 Task，必须能编译过（缓存/代次不能把新注册挡在门外）。"""
    registry = CountingRegistry()
    path = write(tmp_path, "steps:\n  - task: late\n")
    compiler = FlowCompiler(registry)
    with pytest.raises(FlowValidationError, match="未注册的 Task"):
        compiler.compile(compiler.load(path))
    registry.add("late")
    assert [t.name for t in compiler.compile(compiler.load(path))] == ["late"]


def test_cache_is_per_instance(tmp_path):
    """缓存是实例级的：不同 FlowCompiler（不同 registry/provider）互不串编译结果。"""
    registry = CountingRegistry()
    registry.add("alpha")
    path = write(tmp_path, "steps:\n  - task: alpha\n")
    FlowCompiler(registry).compile(FlowCompiler(registry).load(path))
    FlowCompiler(registry).compile(FlowCompiler(registry).load(path))
    assert registry.built == ["alpha", "alpha"]     # 各编译各的，没有跨实例命中


def test_cache_hit_still_refuses_a_flow_mutated_after_load(tmp_path):
    """md5 是**源文本**的指纹，拿到的 flow 却是可变 dict：load 之后被改过一步，
    先查缓存就会把旧编译结果盖在新内容上放行。所以缓存只在静态校验通过之后才用，
    且键里带上当前 steps 的结构指纹。"""
    registry = CountingRegistry()
    registry.add("alpha")
    path = write(tmp_path, "steps:\n  - task: alpha\n")
    compiler = FlowCompiler(registry)
    flow = compiler.load(path)
    assert len(compiler.compile(flow)) == 1          # 进缓存
    flow["steps"].append({"task": "ghost"})          # 事后被脚本改过（源文本 md5 没变）
    with pytest.raises(FlowValidationError, match="未注册的 Task"):
        compiler.compile(flow)


def test_mutated_dispatch_is_not_silently_swallowed_by_the_cache(tmp_path):
    """改动「合法但不同」的步骤（换路由目标）必须重编译：只认 md5 会把改动静默吞掉，
    跑的还是上一版分支——比慢更糟，因为没人知道改动没生效。"""
    registry = CountingRegistry()
    registry.add("seed", produces=("mode",))
    registry.add("left")
    registry.add("right")
    path = write(tmp_path, """steps:
  - id: seed
    task: seed
  - id: pick
    dispatch:
      on: mode
      to: {A: left, default: right}
  - id: left
    task: left
  - id: right
    task: right
""")
    compiler = FlowCompiler(registry)
    flow = compiler.load(path)
    compiler.compile(flow)
    built_after_first = len(registry.built)
    flow["steps"][1]["dispatch"]["to"]["A"] = "right"     # 同 md5，不同内容
    compiler.compile(flow)
    assert len(registry.built) > built_after_first, "改动没触发重编译=缓存串味"


def test_in_memory_flow_has_no_md5_cache_but_still_compiles():
    """内存 dict 没有源文本 → 不进缓存（每次照构造），也不猜行号。"""
    registry = CountingRegistry()
    registry.add("alpha")
    compiler = FlowCompiler(registry)
    flow = {"steps": [{"task": "alpha"}]}
    compiler.compile(flow)
    compiler.compile(flow)
    assert registry.built == ["alpha", "alpha"]
    assert compiler._cache == {}


# ---------------------------------------------------------------- ^ 指针报错
def test_unknown_task_error_points_at_source_line_with_caret(tmp_path):
    """未注册 Task：报错带源文本行号/列与 ^（仿 LiteFlow buildDataNotFoundExceptionMsg）。"""
    registry = CountingRegistry()
    registry.add("alpha")
    path = write(tmp_path, "id: caret\nsteps:\n  - task: alpha\n  - task: ghost\n")
    with pytest.raises(FlowValidationError) as caught:
        compile_file(registry, path)
    message = str(caught.value)
    assert "ghost" in message
    assert ":4:" in message, message              # ghost 就在源文件第 4 行
    assert "^^^" in message                        # 尖号宽度对齐 ghost 本身
    assert "- task: ghost" in message              # 带出源行，不用回头开编辑器找


def test_requires_reference_error_carries_caret(tmp_path):
    """步骤级 requires 引用不存在：同样是未定义引用，带行号与 ^。"""
    registry = CountingRegistry()
    registry.add("alpha", produces=("doc",))
    path = write(tmp_path, "steps:\n  - task: alpha\n  - task: alpha\n    requires: nope\n")
    with pytest.raises(FlowValidationError, match="requires 引用不存在") as caught:
        compile_file(registry, path)
    message = str(caught.value)
    assert ":4:" in message and "^" in message


def test_missing_slot_error_carries_caret(tmp_path):
    """未填槽位（骨架没实例化）：^ 指到骨架里那个占位符，而不是让人猜是哪个 slot 漏了。"""
    registry = CountingRegistry()
    registry.add("alpha")
    write(tmp_path, "steps:\n  - task: alpha\n    code: {{customer}}\n", name="skeleton.yaml")
    instance = write(tmp_path, "template: skeleton.yaml\nslots:\n  other: X\n", name="bu_a.yaml")
    with pytest.raises(FlowValidationError, match="模板槽位未填全") as caught:
        FlowCompiler(registry).load(instance)
    message = str(caught.value)
    assert "customer" in message and ":3:" in message and "^" in message


def test_caret_falls_back_to_step_index_without_source_text():
    """没有源文本（内存 dict）时退化为「步骤 N」粗定位：点名步骤与值，但不猜行号。"""
    registry = CountingRegistry()
    with pytest.raises(FlowValidationError) as caught:
        FlowCompiler(registry).compile({"steps": [{"task": "ghost"}]})
    message = str(caught.value)
    assert "步骤 1" in message and "ghost" in message
    assert "^" not in message


def test_error_types_stay_compatible_for_existing_callers(tmp_path):
    """引用类报错同时是 KeyError（`TaskRegistry.create` 老口径），形态类是 ValueError：
    CLI 的 rc 2、generator 的 ChecklistError 都靠这两族接住，不能悄悄换类型。"""
    registry = CountingRegistry()
    path = write(tmp_path, "steps:\n  - task: ghost\n")
    with pytest.raises(KeyError) as caught:
        compile_file(registry, path)
    assert isinstance(caught.value, ValueError)
    assert "^" in str(caught.value)
    assert "\\n" not in str(caught.value)          # KeyError 默认 repr 会把多行消息转义成乱码
    registry.add("alpha")
    registry.add("ghost")
    with pytest.raises(ValueError) as shape:
        FlowCompiler(registry).compile({"steps": [{"task": "alpha", "channel": "nope"}]})
    assert not isinstance(shape.value, KeyError)   # 形态问题不是引用问题


# ---------------------------------------------------------------- 兼容面
def test_step_level_requires_is_still_not_passed_to_factory():
    """requires 仍是保留键：校验它引用的名字，但不透传给工厂（透传会 TypeError）。"""
    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return FunctionalTask(TaskMetadata("demo", "alpha", produces=("doc",)),
                              lambda actor: {"doc": 1}, "alpha")

    registry = TaskRegistry()
    registry.register("alpha", factory)
    flow = {"requires": {"source_order": {"entity": "SalesOrder"}},
            "steps": [{"task": "alpha", "requires": ["source_order"]}]}
    FlowCompiler(registry).compile(flow)
    assert "requires" not in seen


def test_step_level_requires_accepts_flow_supply_alias_and_save_as(tmp_path):
    """合法引用面不翻错（#115）：flow 级 requires 的 as 落点、上游 save_as、上游 produces 都认。"""
    registry = CountingRegistry()
    registry.add("alpha", produces=("doc",))
    path = write(tmp_path, "requires:\n  order:\n    entity: SalesOrder\n    as: source_order\n"
                           "steps:\n  - task: alpha\n    requires: source_order\n"
                           "    save_as: health\n  - task: alpha\n"
                           "    requires: [source_order, health, doc]\n")
    assert len(compile_file(registry, path)) == 2


def test_on_env_blocked_step_semantics_unchanged():
    """dispatch 引入后，步骤级 on_env_blocked 的 skip 语义一字不改（向后兼容钉桩）。"""
    registry = CountingRegistry()

    def blocked(**kwargs):
        def act(actor):
            raise EnvBlocked("环境缺数据")
        registry.built.append("boom")
        return FunctionalTask(TaskMetadata("demo", "boom"), act, "boom")

    registry.register("boom", blocked)
    registry.add("after")
    actor = Actor()
    result = FlowCompiler(registry).run(
        {"steps": [{"task": "boom", "on_env_blocked": "skip"}, {"task": "after"}]}, actor)
    assert result["status"] == "passed"
    assert actor.ctx["_steps_skipped"] == ["boom"]
    assert registry.built.count("after") == 1


def test_channel_override_still_interpolates_params():
    """channel 覆盖层不得吃掉 `${...}` 插值。

    旧实现把真实 Task 关在 `FunctionalTask(lambda: original.perform(...))` 的闭包里，而
    插值是按包装器的 `__dict__` 遍历的——碰到闭包就停，于是「写了 channel 覆盖的步骤」+
    「参数里有 ${var}」= 参数原样字面量进 perform（静默不生效，与 legacy_case 文档点名的
    「intent/read_only 写了也不透传」同一族死配置）。
    """
    from atlas.core import Task
    observed: list[str] = []

    class Probe(Task):
        def __init__(self, **kwargs):
            self.metadata = TaskMetadata("demo", "probe", produces=("seen",))
            self.params = kwargs

        @property
        def name(self):
            return "probe"

        def perform(self, actor):
            observed.append(self.params["code"])
            return {"seen": self.params["code"]}

    registry = TaskRegistry()
    registry.register("probe", lambda **kwargs: Probe(**kwargs))
    actor = Actor()
    actor.ctx["customer"] = "CUST9"
    FlowCompiler(registry).run(
        {"steps": [{"task": "probe", "code": "${customer}", "channel": "ui"}]}, actor)
    assert observed == ["CUST9"]                      # 插值穿透 channel 包装层
    # 编译期保持原样、执行后回滚（下一次 run 用的是上一次的残留就串味了）
    compiled = FlowCompiler(registry).compile(
        {"steps": [{"task": "probe", "code": "${customer}", "channel": "ui"}]})[0]
    assert compiled.metadata.channel.value == "ui"    # channel 覆盖仍然生效
    inner = compiled.inner
    assert inner.params == {"code": "${customer}"}

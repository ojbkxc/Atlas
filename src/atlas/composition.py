"""从 requires/produces 拓扑拼装，不让 AI 自由猜执行顺序。"""
from __future__ import annotations
from .core import Task
from .failures import FlowCompositionError

def compose(tasks: list[Task], provided: set[str] | None = None) -> list[Task]:
    available = set(provided or ())
    # 同一 Task 实例去重（调用方拼接清单的笔误）：旧实现把重复实例排两次，
    # 执行链对 ordered 逐个 perform——写 Task 真实副作用跑两遍（建两单），
    # 配额只记一次，护栏与现场漂移。compose_for_goal 的 selected 本有去重，
    # 但 engine=None 时 agent/dsl/state_machine 都直接调本函数，入口必须同口径。
    pending: list[Task] = []
    for task in tasks:
        if not any(task is kept for kept in pending):
            pending.append(task)
    ordered: list[Task] = []
    while pending:
        progressed = False
        for task in list(pending):
            missing = set(task.metadata.requires) - available
            if missing:
                continue
            ordered.append(task)
            pending.remove(task)
            available.update(task.metadata.produces)
            progressed = True
        if not progressed:
            details = "; ".join(f"{t.name} 缺 {sorted(set(t.metadata.requires) - available)}" for t in pending)
            raise FlowCompositionError(f"Task 依赖无法满足或存在环：{details}")
    return ordered

def compose_for_goal(tasks: list[Task], goal: str, provided: set[str] | None = None) -> list[Task]:
    """按目标产出递归选择必要 Task，再拓扑排序，避免执行无关业务操作。

    同一产出键挂多个生产者时**显式报错点名**，绝不静默选一个：引擎无法判断
    两个产出方副作用等价（API 查询不改 UI 状态，下游 UI Task 会运行时失败），
    last-wins 会遮蔽可行路径或把读目标悄悄翻成写操作（agent 风险档漂移）。
    同名重复注册（调用方拼接同一清单）按同一生产者去重，不误判成冲突——但
    **前提是定义等值**：#135 探针实锤旧判据只比 name，两个同名不同定义（如
    read_only True/False）的 Task 被静默去重丢一个，多生产者歧义从同名去重
    后门溜进来，agent 只按幸存那份评风险（与 #84 同红线、#128 contracts
    「同键不同定义」同形态）。现同名命中比对 frozen `TaskMetadata` 结构等值：
    等值=拼接清单去重放行（#115 正常路径不翻错），不等值显式点名差异字段。"""
    provided = set(provided or ())
    by_output: dict[str, list[Task]] = {}
    names_by_key: dict[str, dict[str, Task]] = {}
    for task in tasks:
        for key in task.metadata.produces:
            seen = names_by_key.setdefault(key, {})
            prev = seen.get(task.name)
            if prev is not None:
                if prev.metadata == task.metadata:
                    continue   # 同名同定义=同一产出方重复登记，去重不误判冲突
                diff = [f for f in ("domain", "intent", "requires", "produces",
                                    "channel", "ui_fallback", "api_operation",
                                    "read_only", "tags")
                        if getattr(prev.metadata, f) != getattr(task.metadata, f)]
                raise FlowCompositionError(
                    f"产出 {key} 存在同名 Task {task.name} 的**不同定义**（差异字段："
                    f"{'/'.join(diff)}）——同名去重只认同一定义；两份定义漂移必出一份，"
                    f"必须收敛或改用不同 name")
            seen[task.name] = task
            by_output.setdefault(key, []).append(task)
    selected: list[Task] = []
    visiting: set[str] = set()

    def select(key: str) -> None:
        if key in provided:
            return
        if key in visiting:
            raise FlowCompositionError(f"Task 产出存在循环：{key}")
        producers = by_output.get(key)
        if not producers:
            raise FlowCompositionError(f"没有 Task 能产出目标/依赖：{key}")
        if len(producers) > 1:
            names = ", ".join(t.name for t in producers)
            raise FlowCompositionError(
                f"产出 {key} 存在多个 Task 生产者：{names}；必须收敛为单一生产者（两份产出方迟早漂移）")
        task = producers[0]
        if task in selected:
            return
        visiting.add(key)
        for requirement in task.metadata.requires:
            select(requirement)
        visiting.remove(key)
        selected.append(task)

    select(goal)
    return compose(selected, provided)

def catalog(tasks: list[Task]) -> list[dict]:
    return [{"name": t.name, "domain": t.metadata.domain, "intent": t.metadata.intent,
             "requires": list(t.metadata.requires), "produces": list(t.metadata.produces),
             "channel": t.metadata.channel.value, "read_only": t.metadata.read_only,
             "api_operation": t.metadata.api_operation, "ui_fallback": t.metadata.ui_fallback}
            for t in tasks]

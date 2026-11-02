"""从 requires/produces 拓扑拼装，不让 AI 自由猜执行顺序。"""
from __future__ import annotations
from .core import Task
from .failures import FlowCompositionError

def compose(tasks: list[Task], provided: set[str] | None = None) -> list[Task]:
    available = set(provided or ())
    pending = list(tasks)
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
    """按目标产出递归选择必要 Task，再拓扑排序，避免执行无关业务操作。"""
    by_output = {key: task for task in tasks for key in task.metadata.produces}
    selected: list[Task] = []
    visiting: set[str] = set()

    def select(key: str) -> None:
        if key in (provided or set()):
            return
        if key in visiting:
            raise FlowCompositionError(f"Task 产出存在循环：{key}")
        if key not in by_output:
            raise FlowCompositionError(f"没有 Task 能产出目标/依赖：{key}")
        task = by_output[key]
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

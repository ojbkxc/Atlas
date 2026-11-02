"""仅复杂单据按需使用的状态机 DSL；线性流程不要转成状态机。"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
import yaml
from .core import Task


@dataclass(frozen=True)
class Transition:
    target: str
    task: str
    guard: str = ""


@dataclass
class StateMachine:
    initial: str
    states: dict[str, list[Transition]]
    terminal: set[str] = field(default_factory=set)

    def __post_init__(self):
        if self.initial not in self.states:
            raise ValueError(f"初始状态未定义：{self.initial}")
        if not self.terminal:
            self.terminal = set(self.states) - {name for name, edges in self.states.items() if edges}
        for name, edges in self.states.items():
            for edge in edges:
                if edge.target not in self.states:
                    raise ValueError(f"{name} 指向未定义状态 {edge.target}")

    def path(self, target: str) -> list[str]:
        """返回无环最短任务路径；不能到达时明确失败。"""
        if target not in self.states:
            raise ValueError(f"目标状态未定义：{target}")
        queue = [(self.initial, [])]
        visited = {self.initial}
        for state, path in queue:
            if state == target:
                return path
            for edge in self.states[state]:
                if edge.target not in visited:
                    visited.add(edge.target)
                    queue.append((edge.target, path + [edge.task]))
        raise ValueError(f"状态 {self.initial} 无法到达 {target}")

    def run(self, target: str, tasks: dict[str, Task], actor, *, guards: dict[str, Callable] | None = None,
            max_steps: int = 50) -> str:
        guards = guards or {}
        state, steps = self.initial, 0
        while state != target:
            if steps >= max_steps:
                raise RuntimeError("状态机超过 max_steps，可能存在循环或守卫恒假")
            candidates = [edge for edge in self.states.get(state, [])
                          if not edge.guard or guards.get(edge.guard, lambda a: False)(actor)]
            if not candidates:
                raise RuntimeError(f"状态 {state} 没有满足守卫的迁移，目标 {target}")
            if len(candidates) > 1:
                raise ValueError(f"状态 {state} 有多个可用迁移，需唯一确定性路径")
            edge = candidates[0]
            if edge.task not in tasks:
                raise KeyError(f"状态迁移 Task 未注册：{edge.task}")
            actor.attempts_to(tasks[edge.task])
            state, steps = edge.target, steps + 1
        return state

    @classmethod
    def from_dict(cls, raw: dict) -> "StateMachine":
        if not isinstance(raw, dict) or "states" not in raw:
            raise ValueError("状态机 DSL 至少需要 initial 与 states")
        states: dict[str, list[Transition]] = {}
        for name, spec in raw["states"].items():
            edges = []
            for item in spec or []:
                if not isinstance(item, dict) or "to" not in item or "task" not in item:
                    raise ValueError(f"状态 {name} 的迁移需要 to 与 task")
                edges.append(Transition(str(item["to"]), str(item["task"]), str(item.get("guard", ""))))
            states[str(name)] = edges
        return cls(str(raw.get("initial", "")), states,
                   {str(s) for s in raw.get("terminal", [])})

    @classmethod
    def load(cls, path: str | Path) -> "StateMachine":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)


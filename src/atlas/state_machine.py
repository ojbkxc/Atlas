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
            candidates = []
            for edge in self.states.get(state, []):
                if edge.guard:
                    # 声明了守卫却未在 guards 注册：属资源/拼写错误，必须显式报错。
                    # 不能静默当假值——否则同状态的另一条无条件边会被选中，
                    # 状态机悄悄走错分支并真实执行了本不该跑的 Task（副作用）。
                    if edge.guard not in guards:
                        raise RuntimeError(
                            f"状态 {state} 的迁移 {edge.task} 声明了守卫 '{edge.guard}'，"
                            f"但 guards 未注册该守卫（守卫必须显式提供，不能静默判假绕行走其它分支）")
                    guard_fn = guards[edge.guard]
                    if not callable(guard_fn):
                        # #116 面3：守卫注册了但值不可调用（误传字符串/None）——旧 `guards[...](actor)`
                        # 裸 TypeError「'str' object is not callable」无归因。守卫必须是可验证谓词
                        # （#63 声明未注册显式同族延伸：注册≠可用，还得是函数），当场点名收到什么类型。
                        raise RuntimeError(
                            f"守卫 '{edge.guard}' 必须是可调用（接收 actor 返回真假），"
                            f"实际收到 {type(guard_fn).__name__}")
                    if not guard_fn(actor):
                        continue
                candidates.append(edge)
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
        # 形态校验：states 必须是映射、每个状态的迁移必须是列表（dsl.load 同口径），
        # 否则 atlas machine 加载手改/畸形 YAML 时崩成 AttributeError/TypeError——
        # 应给出可定位的 ValueError，而不是让内部迭代裸崩。
        if not isinstance(raw["states"], dict):
            raise ValueError(f"states 必须是「状态名→迁移列表」的映射，实际 {type(raw['states']).__name__}")
        states: dict[str, list[Transition]] = {}
        for name, spec in raw["states"].items():
            if spec is not None and not isinstance(spec, list):
                raise ValueError(f"状态 {name} 的迁移必须是列表，实际 {type(spec).__name__}")
            edges = []
            for item in spec or []:
                if not isinstance(item, dict) or "to" not in item or "task" not in item:
                    raise ValueError(f"状态 {name} 的迁移需要 to 与 task")
                # #116 面C：to/task 值必须是「非空标量」。旧实现直接 str(item[...])——
                # YAML `task:` 忘填（值 null）被 str(None) 兜成字面量 'None'，
                # atlas machine --target 把 'None' 当合法任务名静默输出（假绿，坏形态伪装
                # 合法结果，#113 同红线）；dict/list 同样被兜成 repr 字符串。当场点名实际形态。
                target = cls._require_scalar(f"状态 {name} 迁移 to", item["to"])
                task_name = cls._require_scalar(f"状态 {name} 迁移 task", item["task"])
                guard_val = item.get("guard", "")
                # guard 允许缺省/空串/None（=无守卫）；显式写成 null 之外的复合类型点名。
                # 语义与旧 str(item.get("guard","")) 一致：只有 None/缺失回落无守卫，
                # 数字等标量仍按字面量当守卫名（不能 `or ""` 把合法的 0 误当 falsy 丢掉）。
                if guard_val is None:
                    guard_name = ""
                elif isinstance(guard_val, (dict, list, tuple, set)):
                    raise ValueError(
                        f"状态 {name} 迁移 guard 必须是标量或留空，实际 {type(guard_val).__name__}")
                else:
                    guard_name = str(guard_val)
                edges.append(Transition(target, task_name, guard_name))
            states[str(name)] = edges
        # #116 面1：terminal 键存在但值为 null（YAML `terminal:` 忘填）——旧实现
        # raw.get("terminal", []) 返回 None（默认值只在键缺失时生效），set(None) 裸
        # TypeError「'NoneType' object is not iterable」。cli.machine 只捕
        # (OSError, ValueError, yaml.YAMLError)（cli.py:285），TypeError 不在内→整条命令 traceback
        # 裸崩，违反 #79/#80 用户输入 rc2 口径。null 视同「未填终态」回落自动推导（与键缺失同义，
        # #77「键在值 null 默认值不生效」同红线）；非列表形态点名。
        terminal_raw = raw.get("terminal")
        if terminal_raw is not None and not isinstance(terminal_raw, (list, set, tuple)):
            raise ValueError(f"terminal 必须是列表，实际 {type(terminal_raw).__name__}")
        initial_raw = raw.get("initial")
        if initial_raw is None:
            raise ValueError(f"状态机缺少 initial（实际 {initial_raw!r}）")
        return cls(str(initial_raw), states,
                   {str(s) for s in (terminal_raw or [])})

    @staticmethod
    def _require_scalar(label: str, value: object) -> str:
        if value is None:
            raise ValueError(f"{label} 不能为空（YAML 里键在但值 null），收到 None")
        if isinstance(value, (dict, list, tuple, set)):
            raise ValueError(f"{label} 必须是标量，实际 {type(value).__name__}：{value!r}")
        text = str(value)
        if not text:
            raise ValueError(f"{label} 不能是空字符串")
        return text

    @classmethod
    def load(cls, path: str | Path) -> "StateMachine":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)


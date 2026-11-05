"""Screenplay 最小内核：Task 元数据既是执行契约，也是 AI 拼装契约。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .actor import Actor

class Channel(str, Enum):
    UI = "ui"
    API = "api"
    AUTO = "auto"

class Ability(ABC):
    """Actor 可插拔能力，禁止与具体业务单据耦合。"""

@dataclass(frozen=True)
class TaskMetadata:
    domain: str
    intent: str
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    channel: Channel = Channel.AUTO
    ui_fallback: str = ""
    api_operation: str = ""
    read_only: bool = True
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.domain.strip() or not self.intent.strip():
            raise ValueError("Task 必须声明非空 domain 与 intent")
        # 契约核心在构造期落地类型承诺：注解是 tuple[str, ...]，frozen 数据类还会按
        # 全字段做 __hash__/__eq__。类型漂移会一路静默到拼装/执行才以误导错崩，比当场
        # 拒绝危险得多（与上面 domain/intent 非空校验同一口径）。
        for field_name in ("requires", "produces", "tags"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                # 高频笔误：单元素写成 ("value") 漏尾逗号 → 实为 str 不是 tuple。
                # 消费点 set(produces) 逐字符迭代成 {'v','a','l','u','e'}，
                # compose_for_goal 反报「没有 Task 能产出：value」——作者明明写了
                # value，误导方向完全反了；requires 同样被拆成单字符永不满足。
                raise ValueError(
                    f"{field_name} 必须是 tuple，收到字符串 {value!r}；"
                    f"单元素请写 ({value!r},)（带尾逗号）")
            if not isinstance(value, tuple):
                raise ValueError(
                    f"{field_name} 必须是 tuple，收到 {type(value).__name__}"
                    f"（frozen 元数据按全字段 hash/eq，list 会在集合使用点崩）")
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"{field_name} 每项必须是非空 str，收到 {item!r}")

class Task(ABC):
    metadata: TaskMetadata

    @abstractmethod
    def perform(self, actor: "Actor") -> Any:
        """在 Actor 上执行；结果写入 actor.ctx 的键须在 produces 中声明。"""

    @property
    def name(self) -> str:
        return type(self).__name__

def write_produces(ctx: dict[str, Any], task: Task, result: Any) -> None:
    """把 produces 声明的产出写进 ctx——声明与实写不符必须当场点名。

    不变量「Task 的 requires/produces 必须与 perform() 实际读写一致」此前无人强制：
    旧兜底 `result[key] if key in result else result` 在声明键笔误（如 produces 写
    order_no 而 perform 返回 {'orderNo': …}）时把**整个 dict** 静默塞进 ctx，谎报 Task
    照样「成功」；下游把 dict 当单号插值/断言，烂数据漂到远处才炸，报错不再指回根因。
    result 非 dict 时按既有语义整体赋给每个声明键（单值 Task 合法路径）。
    """
    for key in task.metadata.produces:
        if isinstance(result, dict):
            if key not in result:
                raise KeyError(
                    f"Task {task.name} 声明产出 {key!r}，但 perform() 返回的 dict 里没有；"
                    f"实际键：{sorted(map(str, result))!r}——元数据与实际产出必须一致")
            ctx[key] = result[key]
        else:
            ctx[key] = result

@dataclass
class FunctionalTask(Task):
    metadata: TaskMetadata
    action: Any
    name_override: str = ""

    def __post_init__(self) -> None:
        # #125：action 非可调用（字符串/None/dict）旧实现构造期一路放行，直到 perform()
        # 才裸 `TypeError: 'str' object is not callable`——崩在 distant 执行现场，报错不提
        # Task 名与 metadata，归因漂到引擎缺陷（与 #119 面B step_N 非可调用、#91 元数据
        # 契约同口径：坏形态必须前移构造期点名）。DSL/engine 传 lambda·bound method 是
        # 合法路径，callable 判据不误伤。
        if not callable(self.action):
            raise TypeError(
                f"FunctionalTask 的 action 必须是可调用，收到 {type(self.action).__name__}"
                f"（{self.action!r:.80}）；intent={self.metadata.intent!r}")

    def perform(self, actor: "Actor") -> Any:
        return self.action(actor)

    @property
    def name(self) -> str:
        return self.name_override or super().name

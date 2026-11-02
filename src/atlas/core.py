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

class Task(ABC):
    metadata: TaskMetadata

    @abstractmethod
    def perform(self, actor: "Actor") -> Any:
        """在 Actor 上执行；结果写入 actor.ctx 的键须在 produces 中声明。"""

    @property
    def name(self) -> str:
        return type(self).__name__

@dataclass
class FunctionalTask(Task):
    metadata: TaskMetadata
    action: Any
    name_override: str = ""

    def perform(self, actor: "Actor") -> Any:
        return self.action(actor)

    @property
    def name(self) -> str:
        return self.name_override or super().name

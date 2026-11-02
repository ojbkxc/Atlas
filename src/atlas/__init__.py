"""Atlas：引擎与业务解耦、知识即数据的 UI/API 测试平台。"""
from .actor import Actor
from .core import Ability, Channel, FunctionalTask, Task, TaskMetadata
from .config import AtlasConfig

__all__ = ["Actor", "Ability", "AtlasConfig", "Channel", "FunctionalTask", "Task", "TaskMetadata"]
__version__ = "0.1.0"

"""知识驱动自愈：只修定位/等待等低风险资产，业务写操作不自愈。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from .core import Channel, Task

@dataclass
class HealingProposal:
    task: str
    trigger: str
    remedy: str
    pitfall_id: str
    status: str = "pending_review"

class Healer:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    def propose(self, task: Task, exc: BaseException) -> HealingProposal | None:
        if not task.metadata.read_only or task.metadata.channel == Channel.API:
            return None
        hit = self.knowledge.match_pitfall(exc, task.metadata.domain)
        if not hit:
            return None
        return HealingProposal(task.name, hit["trigger"], hit["remedy"], hit["id"])

    def retry_read_only(self, task: Task, actor, recover: Callable, *, approved: bool = False) -> bool:
        """人工批准后执行已审核修复；绝不吞掉失败或放宽业务断言。"""
        if not approved:
            raise PermissionError("自愈写操作/定位修复需要人工审核")
        if not task.metadata.read_only:
            raise PermissionError("非只读 Task 禁止自动重试，避免重复建单/提交")
        recover()
        task.perform(actor)
        return True

    def can_auto_heal(self, task: Task, exc: BaseException) -> bool:
        """heal 模式只允许“已审核知识 + 只读 Task”自动重试。

        写操作重复执行可能造成重复建单/提交，一律不自动重试。
        没有命中知识时也不重试——盲目重试会掩盖真实缺陷并放大 flaky。
        """
        if not task.metadata.read_only or task.metadata.channel == Channel.API:
            return False
        hit = self.knowledge.match_pitfall(exc, task.metadata.domain)
        return bool(hit and hit.get("status") == "approved" and hit.get("remedy"))


"""知识驱动自愈：只修定位/等待等低风险资产，业务写操作不自愈。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from .core import Channel, Task
from .failures import CONTROL_FLOW

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
        匹配必须带 status="approved" 过滤：pending 草稿的 trigger 更长时会遮蔽
        同异常的已审核坑点（纯按长度择一后单看胜者状态），正当自愈被静默压制。

        #108：控制流异常（KeyboardInterrupt/SystemExit/GeneratorExit）一律拒绝自愈——
        它们是操作者/宿主的终止信号，不是业务失败，remedy 知识无从「修复」它们。
        探针实锤可达路径：draft_pitfall 的 trigger 恰是异常类名，人审批准（review
        --template/--apply）一条含 KeyboardInterrupt 的草稿后，`trigger in text` 命中
        + approved + remedy → Ctrl-C 被就地重试吞掉继续执行，违反 #70 的 interrupted
        终态口径与 dsl.py:184 既有语义（engine 的 `except BaseException` 本就捕 KI，
        此前只靠 trigger 恰不命中侥幸挡住，无任何防线）。
        #114：判据收敛 failures.CONTROL_FLOW 单一真相——recorder/engine 同源；
        此处若仍内联硬编码三元组，新增控制流类型时只改一处必漂移（#92/#97 同红线）。"""
        if isinstance(exc, CONTROL_FLOW):
            return False
        if not task.metadata.read_only or task.metadata.channel == Channel.API:
            return False
        hit = self.knowledge.match_pitfall(exc, task.metadata.domain, status="approved")
        return bool(hit and hit.get("remedy"))


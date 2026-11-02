"""Agent 决策层：规划、风险分级、审批与决策留痕。

定位：agent 只负责“选哪些 Task、按什么顺序、能否自动执行”，
不负责自由点击页面，也不替代确定性回归资产。
所有决策都落盘，便于人审与事后追责。
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .composition import compose_for_goal
from .core import Task


class RiskLevel(str, Enum):
    """按业务影响分级，不按代码行数或 Task 名字分级。"""
    READ_ONLY = "read_only"          # 只查询，可自动执行
    LOW = "low"                      # 可回滚的写，如草稿、可删除测试单据
    MEDIUM = "medium"                # 有业务可见状态，如提交、改选并保存
    HIGH = "high"                    # 生产写、不可回滚、影响他人数据

    @property
    def order(self) -> int:
        return {"read_only": 0, "low": 1, "medium": 2, "high": 3}[self.value]


def classify_risk(task: Task, *, production: bool = False) -> RiskLevel:
    """确定性推导风险，避免 agent 自评过低。

    - 生产环境下任何写操作直接 HIGH
    - read_only Task → READ_ONLY
    - 带 save/submit/approve/delete 语义 → MEDIUM 起
    - 其余写操作 → LOW
    """
    meta = task.metadata
    if meta.read_only:
        return RiskLevel.READ_ONLY
    if production:
        return RiskLevel.HIGH
    name = f"{task.name} {meta.intent}".lower()
    if any(word in name for word in ("save", "submit", "approve", "delete", "confirm")):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


@dataclass
class Decision:
    """一条可审计的 agent 决策。"""
    goal: str
    tasks: list[str]
    risk: str
    approved: bool
    reason: str = ""
    rejected: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"goal": self.goal, "tasks": self.tasks, "risk": self.risk,
                "approved": self.approved, "reason": self.reason,
                "rejected": self.rejected}


class ApprovalRequired(PermissionError):
    """风险超过自动执行阈值且未获批准。"""


class AtlasAgent:
    """最小可用 agent：规划 → 评估风险 → 审批 → 执行 → 留痕。

    不做自由探索式点击；探索交给 toolbox，执行交给确定性 Task。
    """
    def __init__(self, *, production: bool = False, max_auto_risk: RiskLevel = RiskLevel.READ_ONLY,
                 journal: Path | None = None):
        self.production = production
        self.max_auto_risk = max_auto_risk
        self.journal = journal
        self.decisions: list[Decision] = []

    def plan(self, tasks: list[Task], goal: str, provided: set[str] | None = None) -> list[Task]:
        """按元数据依赖选必要 Task；缺依赖/成环会明确失败。"""
        return compose_for_goal(tasks, goal, provided)

    def assess(self, tasks: list[Task]) -> RiskLevel:
        levels = [classify_risk(t, production=self.production) for t in tasks]
        return max(levels, key=lambda level: level.order, default=RiskLevel.READ_ONLY)

    def can_auto_run(self, risk: RiskLevel) -> bool:
        return risk.order <= self.max_auto_risk.order

    def run(self, tasks: list[Task], goal: str, actor, *, approved: bool = False,
            provided: set[str] | None = None, engine=None) -> Decision:
        """执行规划结果。超出自动阈值时必须显式 approved=True。"""
        ordered = self.plan(tasks, goal, provided)
        risk = self.assess(ordered)
        if not self.can_auto_run(risk) and not approved:
            decision = Decision(goal, [t.name for t in ordered], risk.value, False,
                                reason=f"风险 {risk.value} 超过自动阈值 {self.max_auto_risk.value}，需人工批准")
            self._record(decision)
            raise ApprovalRequired(decision.reason)
        for task in ordered:
            if engine:
                engine.execute(actor, task)
            else:
                actor.attempts_to(task)
        decision = Decision(goal, [t.name for t in ordered], risk.value, True,
                            reason="auto" if self.can_auto_run(risk) else "human_approved")
        self._record(decision)
        return decision

    def propose(self, tasks: list[Task], goal: str, provided: set[str] | None = None) -> dict:
        """只规划不执行，供人审查看 agent 打算做什么。"""
        ordered = self.plan(tasks, goal, provided)
        risk = self.assess(ordered)
        return {"goal": goal, "risk": risk.value,
                "auto_allowed": self.can_auto_run(risk),
                "tasks": [{"name": t.name, "channel": t.metadata.channel.value,
                           "read_only": t.metadata.read_only,
                           "intent": t.metadata.intent} for t in ordered]}

    def _record(self, decision: Decision) -> None:
        self.decisions.append(decision)
        if not self.journal:
            return
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(decision.as_dict(), ensure_ascii=False) + "\n")


__all__ = ["ApprovalRequired", "AtlasAgent", "Decision", "RiskLevel", "classify_risk"]

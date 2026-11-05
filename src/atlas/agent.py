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
from .failures import CONTROL_FLOW, exception_message


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
    executed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"goal": self.goal, "tasks": self.tasks, "risk": self.risk,
                "approved": self.approved, "reason": self.reason,
                "rejected": self.rejected, "executed": self.executed}


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
        # 半途失败/Ctrl-C 也必须留痕：模块自述「所有决策都落盘，便于事后追责」，
        # 而审批门已放行意味着已执行的写 Task 真实改过系统——journal 无痕等于追责断链。
        completed: list[str] = []
        try:
            for task in ordered:
                if engine:
                    engine.execute(actor, task)
                else:
                    actor.attempts_to(task)
                completed.append(task.name)
        except BaseException as exc:   # 记录后原样重抛：留痕绝不吞异常、不顶真因
            failed = ordered[len(completed)].name
            # #129 面A：中断判据收敛 failures.CONTROL_FLOW 单一真相（#114 声明 engine/
            # healing/recorder 同源，agent 是漏改的第 4 处——GeneratorExit 是生成器侧的
            # 终止信号，旧三元组硬编码把它记成「执行失败」，终态口径与 classify_failure
            # (GeneratorExit)=interrupted 自相矛盾，#92 改一漏一必漂移）。
            outcome = "interrupted" if isinstance(exc, CONTROL_FLOW) else "执行失败"
            # #129 面B：异常文本走 failures.exception_message 单一真相——旧实现手拼
            # f"{type(exc).__name__}: {exc}"，业务异常 __str__ 自身崩（#126 面A 真实可达）
            # 时在 except 块里当场炸：①顶掉用例真因（#89 定性器先于被定性对象死）；
            # ②整条留痕随赋值一起丢失——已执行写 Task 的真实副作用从此无痕（#85 红线，
            # 比「journal 写失败」更糟：连内存 decisions 都没 append）。
            decision = Decision(goal, [t.name for t in ordered], risk.value, True,
                                reason=f"{outcome}，止步于 {failed}：{exception_message(exc)}",
                                executed=list(completed))
            try:
                self._record(decision)
            except Exception:
                pass   # 留痕写失败不得顶掉执行真因；内存 decisions 仍保留
            raise
        decision = Decision(goal, [t.name for t in ordered], risk.value, True,
                            reason="auto" if self.can_auto_run(risk) else "human_approved",
                            # #110：成功路径同样落执行事实——旧实现 executed 留空，journal 行
                            # 与「一个都没跑」同形（tasks 是计划不是执行），读痕人无从确认哪些写
                            # Task 真实发生过；#85「已执行写 Task 必须可追责」对成功路径同样成立。
                            executed=list(completed))
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
        self.decisions.append(decision)   # 内存留痕先行：落盘失败不丢记录
        if not self.journal:
            return
        # #110：落盘尽力而为——三个调用点（审批拒绝/执行失败/成功返回）都不得让坏盘顶掉
        # 业务语义。旧实现 #85 只护了失败路径（caller 包 try），成功路径与审批门前裸调：
        # ① 成功路径任务已全部真跑（写副作用已发生），mkdir/open 错误把 Decision 返回翻成
        #    FileExistsError——成功信号被基础设施故障掩埋，caller 拿不到 executed 清单；
        # ② 审批门前 raise ApprovalRequired 之前的 _record 崩，rc3 决策信号被顶成裸错误。
        # 只兜 OSError（盘/路径类故障）；非 OSError 是真 bug 不静默吞。
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(decision.as_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass


__all__ = ["ApprovalRequired", "AtlasAgent", "Decision", "RiskLevel", "classify_risk"]

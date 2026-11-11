"""`decide_slot` 纯函数与边界表（design_c1 §3）——无 IO，时钟由调用方注入。

上游对位 pi `catch-up.ts:24` 的 `decideSlot` 纯函数（Atlas 不抄常驻 tick：OS cron 调起的
一次性命令里，「现在」由 OS 给、水位线从台账读，函数本体只看数）。

拍板口径（用户定稿，实现不得私自放宽）：
- ≤2min 准点；**>2min 且 ≤7d 补跑只补最近一轮**，更老的槽计入 `skipped_slots` 不重放——
  报告能答「这台机错过几轮」，不会重放烧配额（catch-up.ts:16-48）。
- **>7d 丢弃**：水位线照样推进（否则每次 tick 都重新判一遍同样的旧账）。
- `due ≤ since`（已处理过/窗内无槽）→ 不动作。
- 改配置/重新 enable 的水位线重置在 `state.apply_patch`（§3 表 8），不在本函数——
  决策函数不看「配置什么时候改的」，只看水位线和现在。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .constants import CATCH_UP_WINDOW_S, ON_TIME_GRACE_S
from .cron_parse import load_tz, parse_expr, prev_slot, slots_between, wall_age
from .state import parse_iso


@dataclass(frozen=True)
class SlotDecision:
    """action ∈ {"none","fire","drop"}；trigger 仅 fire 时有意义（schedule=准点/catch_up=补跑）。"""

    action: str
    trigger: str = ""
    scheduled_for: datetime | None = None
    skipped_slots: int = 0


def _by_age(age: float, skipped: int) -> SlotDecision:
    """表 4/5/6：age ≤2min 准点、>2min 且 ≤7d 补跑最近一轮、>7d 丢弃（记录 skipped+1）。"""
    if age <= ON_TIME_GRACE_S:
        return SlotDecision("fire", "schedule", None, skipped)
    if age <= CATCH_UP_WINDOW_S:
        return SlotDecision("fire", "catch_up", None, skipped)
    return SlotDecision("drop", "", None, 1)


def _with_due(decision: SlotDecision, due: datetime) -> SlotDecision:
    return SlotDecision(decision.action, decision.trigger, due, decision.skipped_slots)


def decide_slot(task: dict, now: datetime, since: "datetime | None") -> SlotDecision:
    """按台账 task + now + 水位线 since 判本轮动作（表 1–7；8–10 见 state/dispatch 侧）。"""
    if not isinstance(task, dict):
        raise ValueError(f"task 必须是映射，收到 {type(task).__name__}")
    schedule = task.get("schedule")
    if not isinstance(schedule, dict):
        raise ValueError(f"task {task.get('id')!r} 的 schedule 必须是映射，收到 {type(schedule).__name__}")
    kind = schedule.get("kind")

    if kind == "manual":   # 表 1（catch-up.ts:26）：手动任务不参与自动补跑判定
        return SlotDecision("none")

    if kind == "once":     # 表 2/3（task-input.ts:134）
        due = parse_iso(schedule.get("at"), "schedule.at")
        if due is None:
            raise ValueError(f"once 任务 {task.get('id')!r} 缺 schedule.at——入库判据在 state，此处不猜")
        if due.tzinfo is None:
            raise ValueError("schedule.at 必须带偏移或时区（决策按绝对时刻比，不猜本地墙钟）")
        if due > now:      # 还没到点：创建/改配置之前的历史不归它补
            return SlotDecision("none")
        if since is not None and due <= since:   # 表 7：认领过的一次任务不再重复
            return SlotDecision("none")
        return _with_due(_by_age((now - due).total_seconds(), 0), due)   # once 没有中间槽，skipped 恒 0

    if kind == "cron":
        spec = parse_expr(schedule.get("expr"))
        tz = load_tz(schedule.get("tz"))
        due = prev_slot(spec, tz, now)
        if due is None:                # 表 7 的「窗内无槽」
            return SlotDecision("none")
        skipped = 0
        if since is not None:
            if due <= since:           # 表 7：已处理过
                return SlotDecision("none")
            skipped = slots_between(spec, tz, since, due)   # 开区间 (since, due)
        # 年龄取**墙钟域**：cron 说的是「当地几点」，DST 段里绝对瞬间与墙钟脱钩，用绝对差
        # 会把 2min/7d 窗口判成另一种结果（评审 C1-B 的 fold=0 口径；真机半小时区未实证，
        # design_c1 §10 继承）。
        return _with_due(_by_age(wall_age(tz, now, due), skipped), due)

    raise ValueError(f"未知 schedule.kind {kind!r}（只支持 cron/once/manual）")


# §3 表 10 的计败判据（调度域对 runner.ts:98 vs :176 的移植）：
# timeout=计败；interrupted 不计败；业务 rc≠0 计败；defer/lease 占用/drop 均不计败。
# 「不计败」分两种：成功打断连败（reset），非事件不动连败（keep）——把 Ctrl-C/没跑到读成
# 「这轮成功了」会抹掉故障前的真实连败，读成失败又违反 CONTROL_FLOW（#114 的第二实现）。
_POLICY = {
    "failed": "count",
    "aborted_time_limit": "count",
    "succeeded": "reset",
    "interrupted": "keep",
    "defer_lease": "keep",
    "dropped": "keep",
    "lease_reclaimed": "keep",
    # C9（design_c9 §3 scheduling 行）：子进程 `atlas run` 返回 rc3=等人 → outcome "waiting"。
    # **keep 而非 reset**，与 interrupted/defer 同族措辞：等人不是故障（不该 +1 把巡检推向
    # AUTO-PAUSED——那正好把人要看的任务停掉），也不是成功（不该清零前面真失败攒下的连败）。
    # 这是 CONTROL_FLOW #114 在调度域的**第三**实现：等人≠环境挂≠成功。
    # 词表**不与 failures.FailureClass 合并**（两套词汇各自语义，合并会把业务分类的语义灌进
    # 调度 defer/lease 面——反向 #92）；「是否算缺陷」的家族判据只在 failures.OPERATIONAL 一处。
    "waiting": "keep",
}


def counts_as_failure(outcome: str) -> bool:
    """表 10 的布尔口径（专项测直接钉这一条）：只有 timeout 与业务 rc≠0 计败。"""
    return failure_policy(outcome) == "count"


def failure_policy(outcome: str) -> str:
    """outcome → 连败策略（count/reset/keep）；未知 outcome 点名，不默认放行（不猜）。"""
    try:
        return _POLICY[outcome]
    except KeyError:
        raise ValueError(f"未知调度终态 {outcome!r}，可选：{sorted(_POLICY)}") from None

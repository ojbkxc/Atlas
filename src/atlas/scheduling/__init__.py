"""调度核（C1）公开面——`atlas schedule` 的实现层。

形态（design_c1 头注）：Atlas **不做常驻守护进程**，`atlas schedule run <id>` 是被 OS
cron / Windows 任务计划器调起的一次性薄命令；状态文件在 `reports/schedule/`（gitignore 内，
真实域名/账号名产物天然不入库）。

公开面按「判据只有一处」的原则导出：决策（decision）、cron 解析（cron_parse）、租约
（lease）、台账（state）、派发（dispatch）。写侧判据、水位线规则、计败策略都不在包外复制。
"""
from __future__ import annotations

from .constants import (CATCH_UP_WINDOW_S, DEFAULT_MAX_DURATION_MIN,
                        HEARTBEAT_INTERVAL_S, LEASE_TTL_S,
                        MAX_CONSECUTIVE_FAILURES, MIN_INTERVAL_MIN,
                        ON_TIME_GRACE_S, SCHEMA_VERSION)
from .cron_parse import (CronSpec, load_tz, min_gap_minutes, min_gap_ok, next_slot,
                         now_utc, parse_expr, prev_slot, slots_between)
from .decision import SlotDecision, counts_as_failure, decide_slot, failure_policy
from .dispatch import build_command, child_env, tick
from .lease import heartbeat, pid_alive, read_lock, release, try_acquire
from .state import (admission_problems, apply_failure_policy, apply_patch, build_task,
                    claim_slot, flow_for_ledger, guard_run_args, iso, lease_path,
                    load_doc, parse_iso, runs_path, save_doc, schedule_dir,
                    skipped_totals, tasks_path, validate_schedule)

__all__ = [
    "CronSpec", "SlotDecision",
    "parse_expr", "next_slot", "prev_slot", "slots_between",
    "min_gap_ok", "min_gap_minutes", "load_tz", "now_utc",
    "decide_slot", "counts_as_failure", "failure_policy",
    "try_acquire", "heartbeat", "read_lock", "release", "pid_alive",
    "build_task", "validate_schedule", "apply_patch", "apply_failure_policy",
    "claim_slot", "admission_problems", "guard_run_args",
    "load_doc", "save_doc", "tasks_path", "runs_path", "lease_path", "schedule_dir",
    "flow_for_ledger", "skipped_totals", "iso", "parse_iso",
    "tick", "build_command", "child_env",
    "ON_TIME_GRACE_S", "CATCH_UP_WINDOW_S", "MAX_CONSECUTIVE_FAILURES",
    "MIN_INTERVAL_MIN", "DEFAULT_MAX_DURATION_MIN", "LEASE_TTL_S",
    "HEARTBEAT_INTERVAL_S", "SCHEMA_VERSION",
]

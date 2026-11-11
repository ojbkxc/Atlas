"""C1 `decide_slot` 边界表（design_c1 §3 表 1–7 + 表 10 计败判据）。

全离线：时间由参数注入，不赌真实时钟。表 8（改配置重置水位线）在 `test_scheduling_state.py`
的 apply_patch 侧钉，表 9（连败 5 次暂停）在 state/cli 两侧钉。
"""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.scheduling.constants import CATCH_UP_WINDOW_S, ON_TIME_GRACE_S
from atlas.scheduling.decision import counts_as_failure, decide_slot, failure_policy

UTC = timezone.utc


def _cron_task(expr="0 * * * *", tz="UTC"):
    return {"id": "patrol", "schedule": {"kind": "cron", "expr": expr, "tz": tz}}


def _once_task(at, tz=None):
    schedule = {"kind": "once", "at": at}
    if tz:
        schedule["tz"] = tz
    return {"id": "once", "schedule": schedule}


# ── 表 1/2/3：manual 与 once ─────────────────────────────────────────

def test_row1_manual_is_always_none():
    decision = decide_slot({"id": "m", "schedule": {"kind": "manual"}},
                           datetime(2026, 11, 5, 12, 0, tzinfo=UTC), None)
    assert (decision.action, decision.trigger) == ("none", "")


def test_row2_once_in_future_is_none():
    """未来的 once：创建/改配置之前的历史不归它补，未到点也不动。"""
    decision = decide_slot(_once_task("2026-11-07T08:00:00+00:00"),
                           datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
                           datetime(2026, 11, 1, tzinfo=UTC))
    assert decision.action == "none"


def test_row3_once_past_fires_as_catch_up():
    decision = decide_slot(_once_task("2026-11-05T09:00:00+00:00"),
                           datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
                           datetime(2026, 11, 5, 0, 0, tzinfo=UTC))
    assert decision.action == "fire" and decision.trigger == "catch_up"
    assert decision.scheduled_for == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    assert decision.skipped_slots == 0        # once 没有中间槽


def test_once_exactly_at_due_is_on_time():
    decision = decide_slot(_once_task("2026-11-05T09:00:00+00:00"),
                           datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
                           datetime(2026, 11, 5, 8, 0, tzinfo=UTC))
    assert (decision.action, decision.trigger) == ("fire", "schedule")


def test_row3_once_dropped_after_window():
    decision = decide_slot(_once_task("2026-01-05T09:00:00+00:00"),
                           datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
                           datetime(2025, 11, 5, tzinfo=UTC))
    assert decision.action == "drop" and decision.skipped_slots == 1


def test_once_already_claimed_does_not_rerun():
    """表 7 的 once 面：认领过的一次任务不再重复（水位线==at）。"""
    decision = decide_slot(_once_task("2026-11-05T09:00:00+00:00"),
                           datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
                           datetime(2026, 11, 5, 9, 0, tzinfo=UTC))
    assert decision.action == "none"


# ── 表 4/5：准点与补跑（含 §2 等值三 case 的决策侧口径）──────────────

def test_row4_exactly_on_the_hour_is_on_time():
    """now 恰落整点 → 该槽即 due，age=0 → 准点触发（右闭判据）。"""
    decision = decide_slot(_cron_task(), datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
                           datetime(2026, 11, 5, 11, 0, tzinfo=UTC))
    assert (decision.action, decision.trigger) == ("fire", "schedule")
    assert decision.scheduled_for == datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    assert decision.skipped_slots == 0


def test_row4_half_second_after_the_hour_still_on_time():
    decision = decide_slot(_cron_task(), datetime(2026, 11, 5, 12, 0, 0, 500000, tzinfo=UTC),
                           datetime(2026, 11, 5, 11, 0, tzinfo=UTC))
    assert (decision.action, decision.trigger) == ("fire", "schedule")


def test_row4_half_second_before_the_hour_is_not_due_yet():
    """now=due−0.5s：本轮还没到，due 回落到上一槽（已认领）→ none。"""
    decision = decide_slot(_cron_task(), datetime(2026, 11, 5, 11, 59, 59, 500000, tzinfo=UTC),
                           datetime(2026, 11, 5, 11, 0, tzinfo=UTC))
    assert decision.action == "none"


@pytest.mark.parametrize("seconds, expected", [
    (ON_TIME_GRACE_S, "schedule"),          # ≤2min 仍算准点
    (ON_TIME_GRACE_S + 1, "catch_up"),      # 越界即补跑
])
def test_row4_grace_boundary_inclusive(seconds, expected):
    due = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    decision = decide_slot(_cron_task(), due + timedelta(seconds=seconds),
                           datetime(2026, 11, 5, 11, 0, tzinfo=UTC))
    assert decision.action == "fire" and decision.trigger == expected


def test_row5_catch_up_fires_only_the_latest_round_and_counts_the_rest():
    """>2min 且 ≤7d：只补最近一轮，更老的槽计入 skipped_slots（能答「错过几轮」，不重放烧配额）。"""
    now = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)          # due = 12:00，age=30min
    since = datetime(2026, 11, 5, 4, 30, tzinfo=UTC)         # 05:00…11:00 七个槽没跑
    decision = decide_slot(_cron_task(), now, since)
    assert decision.action == "fire" and decision.trigger == "catch_up"
    assert decision.scheduled_for == datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    assert decision.skipped_slots == 7


def test_row5_first_run_without_watermark_counts_nothing():
    """since=None（没水位线）时不猜历史，skipped 记 0。"""
    decision = decide_slot(_cron_task(), datetime(2026, 11, 5, 12, 30, tzinfo=UTC), None)
    assert decision.action == "fire" and decision.skipped_slots == 0


# ── 表 6：>7d 丢弃 ───────────────────────────────────────────────────

@pytest.mark.parametrize("now, expected_action", [
    (datetime(2026, 11, 8, 0, 0, tzinfo=UTC), "fire"),        # 恰 7d：含边界，仍补
    (datetime(2026, 11, 8, 0, 0, 1, tzinfo=UTC), "drop"),     # 7d+1s：丢弃
])
def test_row6_catch_up_window_boundary(now, expected_action):
    """monthly 表达式让 age 可控地跨过 7d 边界。"""
    decision = decide_slot(_cron_task("0 0 1 * *"), now, datetime(2026, 1, 1, tzinfo=UTC))
    assert decision.action == expected_action


def test_row6_drop_advances_with_skipped_one_and_no_replay():
    decision = decide_slot(_cron_task("0 0 1 * *"), datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
                           datetime(2025, 10, 1, tzinfo=UTC))
    assert decision.action == "drop"
    assert decision.scheduled_for == datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    assert decision.skipped_slots == 1     # 「记录 skipped+1」按拍板字面：一次丢弃记一笔


# ── 表 7：无槽 / 已处理 ──────────────────────────────────────────────

def test_row7_expression_with_no_slot_is_none():
    decision = decide_slot(_cron_task("0 0 30 2 *"), datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
                           datetime(2020, 1, 1, tzinfo=UTC))
    assert decision.action == "none"


def test_row7_watermark_ahead_of_due_is_none():
    decision = decide_slot(_cron_task(), datetime(2026, 11, 5, 12, 30, tzinfo=UTC),
                           datetime(2026, 11, 5, 12, 0, tzinfo=UTC))
    assert decision.action == "none"


# ── 形态错误点名，不猜 ───────────────────────────────────────────────

def test_unknown_kind_named():
    with pytest.raises(ValueError, match="未知 schedule.kind"):
        decide_slot({"id": "x", "schedule": {"kind": "weekly"}},
                    datetime(2026, 11, 5, tzinfo=UTC), None)


def test_missing_expr_named_by_field():
    """缺 expr 不静默当成 manual：点名收到的形态（parse_expr 的类型判据）。"""
    with pytest.raises(ValueError, match="必须是字符串"):
        decide_slot({"id": "x", "schedule": {"kind": "cron", "tz": "UTC"}},
                    datetime(2026, 11, 5, tzinfo=UTC), None)


def test_bad_task_shape_rejected():
    with pytest.raises(ValueError, match="必须是映射"):
        decide_slot("not-a-task", datetime(2026, 11, 5, tzinfo=UTC), None)


def test_once_without_at_rejected():
    with pytest.raises(ValueError, match="schedule.at"):
        decide_slot({"id": "x", "schedule": {"kind": "once"}},
                    datetime(2026, 11, 5, tzinfo=UTC), None)


# ── 表 10：计败判据（timeout 计败、interrupted/defer/drop 不计败）────

@pytest.mark.parametrize("outcome, counts", [
    ("failed", True),                      # 业务 rc≠0
    ("aborted_time_limit", True),          # timeout 计败
    ("succeeded", False),
    ("interrupted", False),                # 手动 Ctrl-C/被杀属 CONTROL_FLOW，不计败
    ("defer_lease", False),                # 「没跑到」≠失败
    ("dropped", False),
    ("lease_reclaimed", False),
])
def test_row10_failure_counting_policy(outcome, counts):
    assert counts_as_failure(outcome) is counts


@pytest.mark.parametrize("outcome, mode", [
    ("failed", "count"),
    ("aborted_time_limit", "count"),
    ("succeeded", "reset"),
    ("interrupted", "keep"),
    ("defer_lease", "keep"),
    ("dropped", "keep"),
])
def test_failure_policy_modes(outcome, mode):
    assert failure_policy(outcome) == mode


def test_unknown_outcome_is_named_not_silently_passed():
    with pytest.raises(ValueError, match="未知调度终态"):
        failure_policy("exploded")


# ── 评审 C1-B：DST 回拨段不重放（水位线 + 右闭半开区间）──────────────

def _has_tz(name):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, OSError, ValueError):
        return False


@pytest.mark.skipif(not _has_tz("Europe/Amsterdam"), reason="本机无 tzdb（X6：pip install tzdata）")
def test_dst_fall_back_second_occurrence_of_the_hour_does_not_replay():
    """02:00 这个 wall 时刻真实发生两次；认领过一次后第二个 02:30 不该再跑同一轮。"""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Amsterdam")
    first = datetime(2026, 10, 25, 2, 0, tzinfo=tz)                    # fold=0 → 00:00Z
    during_repeat = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=tz)   # 第二个 02:30 → 01:30Z
    assert first.astimezone(UTC) == datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    assert during_repeat.astimezone(UTC) == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    # 没认领过：这一轮 due（02:00）
    assert decide_slot(_cron_task(tz="Europe/Amsterdam"), during_repeat,
                       datetime(2026, 10, 25, 1, 0, tzinfo=tz)).scheduled_for == first
    # 已认领（水位线=02:00 的 ISO）：due ≤ since → none，不重放
    claimed = first.isoformat()
    parsed = datetime.fromisoformat(claimed)
    assert decide_slot(_cron_task(tz="Europe/Amsterdam"), during_repeat, parsed).action == "none"

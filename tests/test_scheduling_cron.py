"""C1 cron 自写解析器边界（design_c1 §2 + §8，评审通过项逐条钉死）。

全离线、无真实 cron：语法子集、7=0、names 拒、dom/dow OR 两 case、坏段点名段号、
min_gap、整分步进幂等，以及 §2 的等值三 case（整点 / 整点后半秒 / 整点前半秒）。
DST 两个钉桩按评审 C1-B 补齐（fold=0 口径，真机 tzdb 缺失时如实 skip）。
"""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.scheduling import cron_parse as cp

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))


def _has_tz(name: str) -> bool:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, OSError, ValueError):
        return False


AMS = "Europe/Amsterdam"
needs_tzdb = pytest.mark.skipif(not _has_tz(AMS),
                                reason=f"本机无 tzdb（缺 {AMS}）——X6：需 pip install tzdata 才能测 DST")


def _spec(expr):
    return cp.parse_expr(expr)


# ── §2 语法子集 ──────────────────────────────────────────────────────

def test_step_field_sets_and_star_bits():
    spec = _spec("*/30 1-8 * * *")
    assert set(spec.minute) == {0, 30}
    assert set(spec.hour) == set(range(1, 9))
    assert spec.dom_star and spec.dow_star   # 两段的 star 位都要落下（OR 判据的输入）


def test_range_with_step():
    spec = _spec("0-30/10 5 * * *")
    assert set(spec.minute) == {0, 10, 20, 30}
    assert set(spec.hour) == {5}


def test_comma_list():
    spec = _spec("0,15,45 * * * *")
    assert set(spec.minute) == {0, 15, 45}


def test_star_and_fixed_values_are_full_domain():
    assert set(_spec("* * * * *").dom) == set(range(1, 32))
    assert set(_spec("* * * * *").dow) == set(range(0, 7))
    assert set(_spec("* 0 1 1 *").month) == {1}


def test_dow_seven_is_sunday_zero():
    """POSIX 口径：7 归一为 0（不装第二种星期编号）。"""
    assert set(_spec("0 0 * * 7").dow) == {0}
    assert set(_spec("0 0 * * 5-7").dow) == {0, 5, 6}


@pytest.mark.parametrize("expr, field, token", [
    ("0 0 * JAN *", "第 4 段（月）", "JAN"),
    ("0 0 * * MON", "第 5 段（星期）", "MON"),
])
def test_named_month_and_weekday_rejected_by_name(expr, field, token):
    """M15：不支持就点名不支持（并点名是哪一段、收到什么），不装支持。"""
    with pytest.raises(ValueError) as excinfo:
        _spec(expr)
    message = str(excinfo.value)
    assert field in message and "命名" in message and token in message


def test_six_and_four_field_exprs_rejected():
    with pytest.raises(ValueError, match="只支持 5 段"):
        _spec("0 0 * * * 2026")
    with pytest.raises(ValueError, match="需要恰好 5 段"):
        _spec("0 0 * *")


def test_out_of_range_names_bounds():
    with pytest.raises(ValueError, match=r"第 1 段（分钟）.*越界.*0-59"):
        _spec("60 * * * *")
    with pytest.raises(ValueError, match=r"第 3 段（日）.*越界"):
        _spec("0 0 32 * *")


def test_reversed_range_rejected_not_guessed():
    with pytest.raises(ValueError, match="不支持反向区间"):
        _spec("30 10-2 * * *")


def test_bare_start_step_rejected():
    with pytest.raises(ValueError, match="不支持 a/n"):
        _spec("5/10 * * * *")


def test_empty_list_item_rejected():
    with pytest.raises(ValueError, match="空项"):
        _spec("1,,2 * * * *")


def test_zero_step_rejected():
    with pytest.raises(ValueError, match="步长必须是正整数"):
        _spec("*/0 * * * *")


def test_non_string_expr_rejected():
    with pytest.raises(ValueError, match="必须是字符串"):
        cp.parse_expr(None)


# ── §2 dom/dow：POSIX vixie 的 OR 语义（自写器最易漂移的一条）────────

def _days(spec, tz, start, end):
    days = []
    cursor = cp.next_slot(spec, tz, start - timedelta(minutes=1))
    while cursor is not None and cursor < end:
        days.append((cursor.month, cursor.day))
        cursor = cp.next_slot(spec, tz, cursor)
    return days


def test_dom_and_dow_both_restricted_is_or():
    """`30 4 1,15 * 5`：1 号、15 号、每个周五都要跑（naive AND 会漏掉整条周五分支）。"""
    tz = UTC
    days = _days(_spec("30 4 1,15 * 5"), tz, datetime(2026, 11, 1, tzinfo=tz),
                 datetime(2026, 12, 1, tzinfo=tz))
    assert days == [(11, 1), (11, 6), (11, 13), (11, 15), (11, 20), (11, 27)]


def test_dow_restricted_dom_star_is_and():
    """dom 是 `*` 时取 AND：只认星期（周五），1 号（周日）不该跑。"""
    tz = UTC
    days = _days(_spec("0 4 * * 5"), tz, datetime(2026, 11, 1, tzinfo=tz),
                 datetime(2026, 12, 1, tzinfo=tz))
    assert days == [(11, 6), (11, 13), (11, 20), (11, 27)]


def test_dom_restricted_dow_star_is_and():
    """dow 是 `*` 时取 AND：只认日（1/15 号），周五不该多跑。"""
    tz = UTC
    days = _days(_spec("0 4 1,15 * *"), tz, datetime(2026, 11, 1, tzinfo=tz),
                 datetime(2026, 12, 1, tzinfo=tz))
    assert days == [(11, 1), (11, 15)]


# ── §2 等值纪律：整点 / 半秒后 / 整点前半秒 ─────────────────────────

HOURLY = "0 * * * *"


def test_prev_slot_right_closed_three_equality_cases():
    """§2 明写的三个 case：now 恰落整点即 due（age=0）；后半秒仍 due；前半秒未 due。"""
    top = datetime(2026, 11, 5, 9, 0, 0, tzinfo=UTC)
    spec = _spec(HOURLY)
    assert cp.prev_slot(spec, UTC, top) == top                      # 整点：该槽即 due
    assert cp.prev_slot(spec, UTC, top + timedelta(microseconds=500000)) == top    # 半秒后
    assert cp.prev_slot(spec, UTC, top - timedelta(microseconds=500000)) == \
        datetime(2026, 11, 5, 8, 0, tzinfo=UTC)                     # 整点前半秒：未 due


def test_next_slot_is_strictly_after():
    """认领用半开区间 (since, due]：since 本身已处理，下一槽必须严格更晚。"""
    top = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    assert cp.next_slot(_spec(HOURLY), UTC, top) == datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def test_slots_between_open_interval_counts_missed_rounds():
    since = datetime(2026, 11, 5, 0, 15, tzinfo=UTC)
    until = datetime(2026, 11, 5, 3, 15, tzinfo=UTC)
    assert cp.slots_between(_spec(HOURLY), UTC, since, until) == 3   # 01,02,03 三点，不含 until
    assert cp.slots_between(_spec(HOURLY), UTC, since, datetime(2026, 11, 5, 1, 0, tzinfo=UTC)) == 0


def test_slots_are_whole_minute_datetimes():
    slot = cp.next_slot(_spec("*/7 * * * *"), UTC, datetime(2026, 11, 5, 9, 0, 30, tzinfo=UTC))
    assert slot.second == 0 and slot.microsecond == 0 and slot.fold == 0


def test_slot_step_is_idempotent_and_strictly_increasing():
    spec = _spec("*/15 2-4 * * *")
    cursor = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)
    seen = []
    for _ in range(6):
        cursor = cp.next_slot(spec, UTC, cursor)
        assert cursor is not None
        seen.append(cursor)
    assert seen == [datetime(2026, 11, 5, h, m, tzinfo=UTC)
                    for h, m in [(2, 0), (2, 15), (2, 30), (2, 45), (3, 0), (3, 15)]]
    assert all(a < b for a, b in zip(seen, seen[1:]))


def test_no_slot_in_horizon_returns_none():
    """2 月 30 日永不命中：窗内排不出槽就返回 None，不硬造一个日期。"""
    spec = _spec("0 0 30 2 *")
    at = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    assert cp.next_slot(spec, UTC, at) is None
    assert cp.prev_slot(spec, UTC, at) is None


def test_prev_slot_none_when_history_empty():
    spec = _spec("0 0 29 2 *")   # 上一个 2 月 29 日在 366 天窗之外（2028）
    assert cp.prev_slot(spec, UTC, datetime(2027, 3, 1, tzinfo=UTC)) is None


@pytest.mark.parametrize("moment", [
    datetime(2026, 11, 5, 9, 30, tzinfo=UTC),
    datetime(2026, 11, 5, 1, 30, tzinfo=SHANGHAI),
])
def test_same_wall_clock_in_fixed_offsets(moment):
    """同一 wall 表达式在两个固定偏移里给出同一 wall 读数（槽位定义在墙钟，不被偏移改写）。"""
    slot = cp.next_slot(_spec("30 2 * * *"), moment.tzinfo, moment)
    assert (slot.hour, slot.minute) == (2, 30)


@pytest.mark.parametrize("bad", [datetime(2026, 11, 5, 9, 0), "2026-11-05"])
def test_naive_or_non_datetime_rejected(bad):
    """不猜本地墙钟：naive datetime / 字符串都必须点名。"""
    with pytest.raises(ValueError):
        cp.prev_slot(_spec(HOURLY), UTC, bad)


# ── §2 最小触发间隔 ──────────────────────────────────────────────────

@pytest.mark.parametrize("expr,ok", [
    ("* * * * *", False),          # 每分钟：手滑典型
    ("*/4 * * * *", False),        # 4 分钟 < 5
    ("0-4,30 1 * * *", False),     # 同小时内出现 1 分钟间隙
    ("*/5 * * * *", True),         # 恰 5 分钟=下界（含）
    ("*/30 1-8 * * *", True),
    ("0 * * * *", True),
    ("0 0 * * 1", True),           # 每周一次
    ("0 0 30 2 *", True),          # 排不出两槽=无可违反的间隔（永不触发由它自己暴露）
])
def test_min_gap_gate(expr, ok):
    at = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    assert cp.min_gap_ok(_spec(expr), UTC, at) is ok


def test_min_gap_minutes_hourly_and_windowed():
    at = datetime(2026, 11, 5, 0, 5, tzinfo=UTC)
    assert cp.min_gap_minutes(_spec("0 * * * *"), UTC, at) == 60.0
    gap = cp.min_gap_minutes(_spec("*/30 1-8 * * *"), UTC, at)
    assert gap == 30.0
    assert cp.min_gap_minutes(_spec("0 0 30 2 *"), UTC, at) is None


def test_min_interval_constant_matches_design_floor():
    from atlas.scheduling.constants import MIN_INTERVAL_MIN as value
    assert value == 5


# ── 评审 C1-B：DST 两个钉桩 case（fold=0 口径）──────────────────────

@needs_tzdb
def test_dst_spring_gap_fires_once_not_twice():
    """2026-03-29 Amsterdam 02:00→03:00 春季缺口：`30 2 * * *` 当天恰有一个槽。

    钉死的选择（评审 C1-B「不双发」为硬要求，具体口径在此写明）：槽位定义在**当地墙钟**，
    缺口里的 02:30 依然是一个唯一槽，跳转后的任意墙钟时刻（03:10）就判它 due，于是这一轮
    **迟到一个多小时但仍然跑**；vixie crond 的做法是那天直接不跑。两种都比「跑两遍」正确，
    Atlas 取不丢轮的那个。绝对瞬间视角下 fold=0 的 02:30 落在 01:30Z，说明它没被重复认领。
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(AMS)
    spec = _spec("30 2 * * *")
    before = datetime(2026, 3, 28, 12, 0, tzinfo=tz)
    slot = cp.next_slot(spec, tz, before)
    assert slot == datetime(2026, 3, 29, 2, 30, tzinfo=tz)
    assert slot.astimezone(UTC) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    # 跳转后当地 03:10：prev 就是缺口日那一轮（唯一、不双发）
    assert cp.prev_slot(spec, tz, datetime(2026, 3, 29, 3, 10, tzinfo=tz)) == slot
    # 下一轮直接是 3/30——缺口日只此一槽
    assert cp.next_slot(spec, tz, slot) == datetime(2026, 3, 30, 2, 30, tzinfo=tz)
    assert cp.slots_between(spec, tz, datetime(2026, 3, 28, 2, 30, tzinfo=tz),
                            datetime(2026, 3, 30, 0, 0, tzinfo=tz)) == 1


@needs_tzdb
def test_dst_fall_back_does_not_double_count_slots():
    """2026-10-25 回拨：02:00 这个 wall 时刻真实发生两次，但按 fold=0 只数一次。

    防线是「槽位唯一 + 水位线右闭」：`slots_between` 的开区间与 `prev_slot ≤ now` 配合，
    重复的那一小时不会补跑第二轮（decision 侧另有不重放测）。
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(AMS)
    spec = _spec("0 * * * *")
    day_start = datetime(2026, 10, 25, 0, 0, tzinfo=tz)
    # 开区间 (00:00, 次日00:00)：01:00…23:00 共 23 轮
    assert cp.slots_between(spec, tz, day_start, datetime(2026, 10, 26, 0, 0, tzinfo=tz)) == 23
    hours = []
    cursor = day_start
    for _ in range(23):
        cursor = cp.next_slot(spec, tz, cursor)
        hours.append(cursor.hour)
    assert hours == list(range(1, 24))
    assert hours.count(2) == 1                     # 回拨重复的那一小时不双算
    fold0_two = datetime(2026, 10, 25, 2, 0, tzinfo=tz)
    assert fold0_two.astimezone(UTC) == datetime(2026, 10, 25, 0, 0, tzinfo=UTC)


@needs_tzdb
def test_load_tz_names_tzdata_when_missing():
    """X6 文案：缺 tzdb 点名 `pip install tzdata`，不静默回落 UTC。"""
    with pytest.raises(ValueError, match="tzdata"):
        cp.load_tz("Mars/Olympus_Mons")


def test_load_tz_rejects_empty_name():
    with pytest.raises(ValueError, match="非空时区名"):
        cp.load_tz("   ")


def test_now_utc_is_aware():
    assert cp.now_utc().tzinfo is not None

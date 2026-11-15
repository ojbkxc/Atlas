"""pf-11：`slots_between` 周期公式对照测（公式计数 == 逐 slot 计数）。

判据（任务钉死）：
- 每个 spec×时间窗 cell：公式路径计数 == 原逐 slot 扫描计数 == 独立暴力枚举计数；
- 任一不一致 → 该档位必须按 `formula_fallback_reason` 点名回退 slots_between 原实现
  ——不许悄悄数错（=调度语义变更）；
- `atlas schedule status` 的「错过几轮」答案（skipped_slots）不许变：decide_slot
  端到端钉一条。

暴力枚举刻意不经过模块的等差数列区间算式：按墙钟分钟逐分步进、逐字段核对——公式的
风险面（开区间边界、AP 区间算式、DST 墙钟映射）全部落在对照里。原逐 slot 扫描
（`cp._slots_between_scan`）是 pf-11 保留的逐字实现，作第二基准。

口径依据：槽位以 `tzinfo=tz` 构造、锚点 `astimezone(tz)` 归一，Python「双方 aware 且
tzinfo 相同→比较墙钟字段」——计数恒等于墙钟网格点数，DST 只改写绝对瞬间映射。
"""
from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from atlas.scheduling import cron_parse as cp
from atlas.scheduling.cron_parse import formula_fallback_reason, parse_expr, slots_between
from atlas.scheduling.decision import decide_slot

UTC = timezone.utc


def _has_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, OSError, ValueError):
        return False


AMS = ZoneInfo("Europe/Amsterdam") if _has_tz("Europe/Amsterdam") else None
NY = ZoneInfo("America/New_York") if _has_tz("America/New_York") else None
needs_tzdb = pytest.mark.skipif(AMS is None,
                                reason="本机无 tzdb——X6：需 pip install tzdata 才能测 DST 跨窗")


def _spec(expr: str):
    return parse_expr(expr)


def _day_matches(spec, day) -> bool:
    """日匹配的独立重实现（POSIX vixie 语义）：两者都受限取 OR，任一为 `*` 取 AND。"""
    if day.month not in spec.month:
        return False
    in_dom = day.day in spec.dom
    in_dow = ((day.weekday() + 1) % 7) in spec.dow
    if spec.dom_star or spec.dow_star:
        return in_dom and in_dow
    return in_dom or in_dow


def _brute_count(spec, tz, since, until) -> int:
    """独立暴力口径：墙钟分钟逐分步进，数满足五字段 + 日匹配的整分时刻。"""
    w_s = since.astimezone(tz).replace(tzinfo=None)
    w_u = until.astimezone(tz).replace(tzinfo=None)
    t = w_s.replace(second=0, microsecond=0) + timedelta(minutes=1)
    count = 0
    while t < w_u:
        if t.minute in spec.minute and t.hour in spec.hour and _day_matches(spec, t):
            count += 1
        t += timedelta(minutes=1)
    return count


def _assert_cell(expr, tz, since, until, limit=100000):
    """一个 cell 的对照判据：公式 == 原扫描 == 暴力；适用性按点名判据核对。"""
    spec = _spec(expr)
    formula_count = slots_between(spec, tz, since, until, limit=limit)
    scan_count = cp._slots_between_scan(spec, tz, since, until, limit=limit)
    brute_count = _brute_count(spec, tz, since, until)
    assert formula_count == scan_count == brute_count, (
        f"spec={expr} tz={tz} since={since} until={until}: "
        f"公式={formula_count} 原扫描={scan_count} 暴力={brute_count}")
    return formula_count


# ── 7 天 catch-up 窗（性能案发现场）+ DST 跨窗 ───────────────────────

@pytest.mark.parametrize("expr, tz, since, until", [
    ("*/5 * * * *", UTC, datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 12, 0, 0, tzinfo=UTC)),          # 7 天整窗：2016 槽
    ("*/30 * * * *", UTC, datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 12, 0, 0, tzinfo=UTC)),          # 7 天：336 槽
    ("0 * * * *", UTC, datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 12, 0, 0, tzinfo=UTC)),          # 7 天整点：168 槽
    ("*/1 * * * *", UTC, datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 7, 0, 0, tzinfo=UTC)),           # */1 每分钟：2880 槽
    ("*/7 * * * *", UTC, datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 7, 0, 0, tzinfo=UTC)),           # 步长不整除 60 的绕回
    ("5-59/10 * * * *", UTC, datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 8, 0, 0, tzinfo=UTC)),           # 非零起点 AP
    ("*/30 1-8 * * *", UTC, datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 12, 0, 0, tzinfo=UTC)),          # 小时受限 AP
])
def test_formula_matches_scan_and_brute_utc_week(expr, tz, since, until):
    assert _assert_cell(expr, tz, since, until) > 0


@needs_tzdb
@pytest.mark.parametrize("expr, since, until", [
    ("*/5 * * * *", datetime(2026, 10, 22, 0, 0, tzinfo=AMS),
     datetime(2026, 10, 29, 0, 0, tzinfo=AMS)),          # 跨 10-25 回拨周
    ("*/5 * * * *", datetime(2026, 3, 26, 0, 0, tzinfo=AMS),
     datetime(2026, 4, 2, 0, 0, tzinfo=AMS)),            # 跨 03-29 缺口周
    ("0 * * * *", datetime(2026, 10, 22, 0, 0, tzinfo=AMS),
     datetime(2026, 10, 29, 0, 0, tzinfo=AMS)),          # 回拨周整点：墙钟计数不受影响
])
def test_formula_matches_scan_and_brute_dst_week(expr, since, until):
    assert _assert_cell(expr, AMS, since, until) > 0


@needs_tzdb
def test_formula_matches_scan_and_brute_ny_fall_back_week():
    # 2026-11-01 NY 02:00→01:00 回拨；墙钟网格计数与 UTC 同构
    _assert_cell("*/5 * * * *", NY, datetime(2026, 11, 1, 0, 0, tzinfo=NY),
                 datetime(2026, 11, 8, 0, 0, tzinfo=NY))


# ── DST 当日子窗：亚分边界、缺口/回拨小时 ───────────────────────────

@needs_tzdb
@pytest.mark.parametrize("expr, since, until", [
    # 回拨日上午：亚分 since + 跨重复小时
    ("*/5 * * * *", datetime(2026, 10, 25, 1, 30, 30, tzinfo=AMS),
     datetime(2026, 10, 25, 3, 30, 0, tzinfo=AMS)),
    # 缺口日上午：02:30 槽存在但 03:00 的绝对时刻更早——墙钟口径下次序不变
    ("0 * * * *", datetime(2026, 3, 29, 2, 30, tzinfo=AMS),
     datetime(2026, 3, 29, 3, 30, tzinfo=AMS)),
    # 整分 since / 亚分 until 的组合
    ("*/15 * * * *", datetime(2026, 10, 25, 0, 45, tzinfo=AMS),
     datetime(2026, 10, 25, 2, 14, 30, tzinfo=AMS)),
    # until 的分钟恰是网格点且带亚分量：02:15 < 02:15:30 该槽必须计入
    ("*/15 * * * *", datetime(2026, 10, 25, 0, 30, tzinfo=AMS),
     datetime(2026, 10, 25, 2, 15, 30, tzinfo=AMS)),
])
def test_formula_matches_scan_and_brute_dst_day_windows(expr, since, until):
    _assert_cell(expr, AMS, since, until)


@needs_tzdb
def test_formula_matches_scan_and_brute_ny_dst_day_window():
    _assert_cell("*/15 * * * *", NY, datetime(2026, 11, 1, 0, 45, 30, tzinfo=NY),
                 datetime(2026, 11, 1, 2, 15, 0, tzinfo=NY))


@needs_tzdb
def test_formula_matches_scan_and_brute_cross_tz_bounds():
    # since 在 UTC、until 在 AMS：锚点归一路径（astimezone）两侧都要对上
    _assert_cell("*/5 * * * *", AMS, datetime(2026, 10, 28, 22, 0, tzinfo=UTC),
                 datetime(2026, 10, 29, 12, 0, tzinfo=AMS))


# ── dom/dow 双受限（POSIX vixie OR）与稀疏日模式 ────────────────────

@pytest.mark.parametrize("expr, tz, since, until", [
    # dom/dow 都受限 → OR：1 号、15 号、每个周五都跑
    ("30 4 1,15 * 5", UTC, datetime(2026, 10, 20, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 20, 0, 0, tzinfo=UTC)),
    # 仅 dow 受限（每周一）
    ("0 0 * * 1", UTC, datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 16, 0, 0, tzinfo=UTC)),
    # 仅 dom 受限（每月 1 号）
    ("0 0 1 * *", UTC, datetime(2026, 10, 20, 0, 0, tzinfo=UTC),
     datetime(2026, 12, 2, 0, 0, tzinfo=UTC)),
    # 永不命中
    ("0 0 30 2 *", UTC, datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 11, 0, 0, tzinfo=UTC)),
    # 稀疏但窗内可达（2028 闰年 2-29）
    ("0 0 29 2 *", UTC, datetime(2028, 2, 20, 0, 0, tzinfo=UTC),
     datetime(2028, 3, 2, 0, 0, tzinfo=UTC)),
])
def test_formula_matches_scan_and_brute_restricted_days(expr, tz, since, until):
    _assert_cell(expr, tz, since, until)


# ── limit 护栏：公式与原扫描同样「到顶就停并如实只数到 limit」 ──────

def test_formula_respects_limit_identically():
    since = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    until = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)   # 开区间两端各 excluded：2015 槽
    spec = _spec("*/5 * * * *")
    assert slots_between(spec, UTC, since, until) == 1000          # 默认 limit
    assert cp._slots_between_scan(spec, UTC, since, until) == 1000
    assert slots_between(spec, UTC, since, until, limit=100000) == 2015
    assert cp._slots_between_scan(spec, UTC, since, until, limit=100000) == 2015


# ── 负向对照：不适用档位必须点名回退，不许悄悄数错 ─────────────────

@pytest.mark.parametrize("expr, since, until, reason_word", [
    # 分钟集合非单条等差数列（逗号列表）
    ("0-4,30 1 * * *", datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 12, 0, 0, tzinfo=UTC), "分钟"),
    ("0,15,45 * * * *", datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 8, 0, 0, tzinfo=UTC), "分钟"),
    # 小时集合非单条等差数列
    ("0 1-3,8 * * *", datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
     datetime(2026, 11, 8, 0, 0, tzinfo=UTC), "小时"),
])
def test_non_ap_grid_falls_back_and_is_named(expr, since, until, reason_word):
    spec = _spec(expr)
    reason = formula_fallback_reason(spec, UTC, since, until)
    assert reason is not None and reason_word in reason      # 点名，不静默
    # 回退路径的答案仍与原实现一致
    assert slots_between(spec, UTC, since, until) == cp._slots_between_scan(
        spec, UTC, since, until)


def test_window_over_horizon_falls_back_and_is_named():
    spec = _spec("0 * * * *")
    since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    until = datetime(2027, 3, 1, 0, 0, tzinfo=UTC)          # 424 天 > 366
    reason = formula_fallback_reason(spec, UTC, since, until)
    assert reason is not None and "horizon" in reason
    assert slots_between(spec, UTC, since, until) == cp._slots_between_scan(
        spec, UTC, since, until)


# ── 公式路径确实认领 catch-up 案发现场（不许悄悄整体回退） ─────────

def test_formula_claims_catch_up_window_and_restricted_days():
    assert formula_fallback_reason(_spec("*/5 * * * *"), UTC,
                                   datetime(2026, 11, 5, tzinfo=UTC),
                                   datetime(2026, 11, 12, tzinfo=UTC)) is None
    # dom/dow 受限档位公式同样认领（等价性由上面对照测证明，不靠回退）
    assert formula_fallback_reason(_spec("30 4 1,15 * 5"), UTC,
                                   datetime(2026, 10, 20, tzinfo=UTC),
                                   datetime(2026, 11, 20, tzinfo=UTC)) is None


# ── 回归钉：「错过几轮」答案经 decide_slot 端到端不变 ───────────────

def test_decide_slot_catch_up_skipped_unchanged():
    """表 5 口径端到端：*/5 窗 (04:30, 12:30) 错过 95 轮——公式/扫描/决策三者一致。

    now 取 12:33（非槽位整分）：due=12:30、age=3min>2min → catch_up；若取 12:30 则
    due 即 12:30（age=0 准点），构不成补跑场景。
    """
    now = datetime(2026, 11, 5, 12, 33, tzinfo=UTC)
    since = datetime(2026, 11, 5, 4, 30, tzinfo=UTC)
    task = {"id": "pf11", "schedule": {"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"}}
    decision = decide_slot(task, now, since)
    assert decision.action == "fire" and decision.trigger == "catch_up"
    assert decision.scheduled_for == datetime(2026, 11, 5, 12, 30, tzinfo=UTC)
    spec = _spec("*/5 * * * *")
    due = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)
    assert decision.skipped_slots == 95
    assert decision.skipped_slots == slots_between(spec, UTC, since, due)
    assert decision.skipped_slots == cp._slots_between_scan(spec, UTC, since, due)


def test_decide_slot_hourly_row5_answer_unchanged():
    """既有 row5 口径（05:00…11:00 七轮）经公式路径答案不变。"""
    now = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)
    since = datetime(2026, 11, 5, 4, 30, tzinfo=UTC)
    task = {"id": "pf11h", "schedule": {"kind": "cron", "expr": "0 * * * *", "tz": "UTC"}}
    decision = decide_slot(task, now, since)
    assert decision.skipped_slots == 7

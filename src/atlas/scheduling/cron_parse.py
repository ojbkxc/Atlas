"""5 段 cron 自写解析器（design_c1 §2）——纯 stdlib，不引 croniter/APScheduler。

口径分级（gap6 勘误 E2，评审通过项）：`croniter` 不在 AGENTS §4「浏览器驱动之外的运行时/
大型框架」字面对象内，不用它的真实理由是 **Atlas 既有先例=纯 stdlib**（Allure 直出、
`zoneinfo`），新增运行时依赖属团队评审项而非红线硬禁；`APScheduler` 自带常驻调度器/线程
模型才确触 §4。本文件实现不得把这条理由写反。

等值纪律（§2；pi `previousRuns(1, at+1000)` 是给 croner 秒级截断打的补丁，自写整分步进
没有截断面，但判据逐字照钉）：
- 槽位恒为**整分钟** tz-aware datetime（second/microsecond=0，fold=0 口径）。
- `due = prev_slot(now)` 判据 **`slot ≤ now`（右闭）**：now 恰落整点 → 该槽即 due（age=0，
  准点触发）；now = due − 0.5s → 未 due。
- 认领用半开区间 `(since, due]`（水位线 CAS，见 dispatch）；skipped 计数用开区间
  `(since, due)`（`slots_between`）。
- dom/dow 按 POSIX vixie 语义：**两者都受限时取 OR，任一为 `*` 取 AND**——naive AND 实现
  是错的（`30 4 1,15 * 5` 应「1 号、15 号、每个周五」都跑）。star 判据 = 该段以 `*` 开头
  （`*/n` 同视），与 cronie 的 star-bit 一致。

显式不支持就点名不可（M15 归因纪律，不装支持）：命名月/周（JAN/MON）、`a/n` 裸起点步进、
反向区间、6/7 段。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .constants import MIN_INTERVAL_MIN

# 字段序：分 时 日 月 周（POSIX）。星期额外允许字面量 7（归一为 0）。
FIELD_NAMES = ("分钟", "小时", "日", "月", "星期")
_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_ITEM = re.compile(r"\A(\d+)(?:-(\d+))?(?:/(\d+))?\Z")
_LETTERS = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class CronSpec:
    """解析产物：五个取值集合 + dom/dow 的 star 位（POSIX OR 语义判据）。"""

    expr: str
    minute: frozenset
    hour: frozenset
    dom: frozenset
    month: frozenset
    dow: frozenset
    dom_star: bool
    dow_star: bool


def _field_error(index: int, item: str, reason: str) -> ValueError:
    return ValueError(f"cron 第 {index + 1} 段（{FIELD_NAMES[index]}）{reason}：收到 {item!r}"
                      f"（支持 *、a、a-b、*/n、a-b/n 与逗号列表；命名缩写不支持）")


def _step_of(text: str, index: int, item: str) -> int:
    try:
        step = int(text)
    except ValueError:
        raise _field_error(index, item, "步长必须是正整数") from None
    if step <= 0:
        raise _field_error(index, item, "步长必须是正整数")
    return step


def _check_value(number: int, index: int, item: str) -> int:
    low, high = _BOUNDS[index]
    top = 7 if index == 4 else high   # 星期 7 是 0 的别名（POSIX 口径）
    if number < low or number > top:
        raise _field_error(index, item, f"取值越界（合法范围 {low}-{high}，星期 7 归一为 0）")
    return number


def _parse_item(item: str, index: int) -> set:
    low, high = _BOUNDS[index]
    if not item:
        raise _field_error(index, item, "逗号列表存在空项")
    if item == "*":
        return set(range(low, high + 1))
    if item.startswith("*/"):
        step = _step_of(item[2:], index, item)
        return {value for value in range(low, high + 1) if (value - low) % step == 0}
    match = _ITEM.match(item)
    if match is None:
        if _LETTERS.search(item):
            raise _field_error(index, item, "不支持命名值（只认数字；JAN/MON 等缩写名不实现）")
        if "/" in item:
            raise _field_error(index, item, "不支持 a/n（起点到域尾的步进）；请写 a-b/n 或 */n")
        raise _field_error(index, item, "无法解析")
    first = _check_value(int(match.group(1)), index, item)
    last_raw = match.group(2)
    step_raw = match.group(3)
    if last_raw is None:
        if step_raw is not None:
            raise _field_error(index, item, "不支持 a/n（起点到域尾的步进）；请写 a-b/n 或 */n")
        return {first}
    last = _check_value(int(last_raw), index, item)
    step = _step_of(step_raw, index, item) if step_raw else 1
    if last < first:
        # 反向/环绕区间各家 cron 语义不一，设计语法子集不含它——拒了比猜了诚实
        raise _field_error(index, item, "不支持反向区间（a 大于 b 的环绕语义不实现，不猜）")
    return {value for value in range(first, last + 1) if (value - first) % step == 0}


def _parse_field(text: str, index: int) -> tuple:
    """单段解析：返回 (取值集合, star 位)。star=该段以 `*` 开头（vixie star-bit 口径）。"""
    star = text.startswith("*")
    values: set = set()
    for item in text.split(","):
        values |= _parse_item(item, index)
    if not values:
        raise _field_error(index, text, "取值为空")
    if index == 4:
        values = {0 if value == 7 else value for value in values}
    return frozenset(values), star


def parse_expr(expr: str) -> CronSpec:
    """5 段表达式 → CronSpec；错误消息点名第几段、收到什么（§2）。"""
    if not isinstance(expr, str):
        raise ValueError(f"cron 表达式必须是字符串，收到 {type(expr).__name__}")
    parts = expr.split()
    if len(parts) < 5:
        raise ValueError(f"cron 表达式需要恰好 5 段（分 时 日 月 周），收到 {len(parts)} 段：{expr!r}")
    if len(parts) > 5:
        raise ValueError(f"cron 表达式只支持 5 段，收到 {len(parts)} 段（第 6 段起不支持）：{expr!r}")
    minute, _ = _parse_field(parts[0], 0)
    hour, _ = _parse_field(parts[1], 1)
    dom, dom_star = _parse_field(parts[2], 2)
    month, _ = _parse_field(parts[3], 3)
    dow, dow_star = _parse_field(parts[4], 4)
    return CronSpec(expr=expr, minute=minute, hour=hour, dom=dom, month=month,
                    dow=dow, dom_star=dom_star, dow_star=dow_star)


def load_tz(name: str) -> ZoneInfo:
    """时区加载唯一判据（X6）：Windows 无系统 tzdb 时点名 `pip install tzdata`，
    不静默回落 UTC——UTC 偏移会把巡检窗口整体错位。C6 的 `tz_data_status` 是体检，
    这里是加载点，两处口径一致、不留第二份判据。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"schedule.tz 必须是非空时区名，收到 {name!r}")
    try:
        return ZoneInfo(name.strip())
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"时区 {name!r} 无法加载——Windows 无系统 tzdb，先 pip install tzdata"
                         f"（不静默按 UTC 跑）") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"时区 {name!r} 无法加载：{type(exc).__name__}: {exc}") from exc


def _require_aware(moment: datetime, label: str) -> datetime:
    if not isinstance(moment, datetime):
        raise ValueError(f"{label} 必须是 datetime，收到 {type(moment).__name__}")
    if moment.tzinfo is None:
        raise ValueError(f"{label} 必须带 tzinfo（槽位与水位线按绝对时刻比较，不猜本地墙钟）")
    return moment


def _wall(moment: datetime, tz: ZoneInfo) -> datetime:
    """aware → tz 内的**墙钟** naive（保留 fold；仅作按天剪枝的扫描起点）。"""
    return moment.astimezone(tz).replace(tzinfo=None)


def _date_ok(spec: CronSpec, day: date) -> bool:
    """POSIX vixie 的 dom/dow 组合判据：任一为 `*` 取 AND，两者都受限取 OR。"""
    if day.month not in spec.month:
        return False
    in_dom = day.day in spec.dom
    # Python weekday(): Monday=0…Sunday=6；cron dow: Sunday=0…Saturday=6
    in_dow = ((day.weekday() + 1) % 7) in spec.dow
    if spec.dom_star or spec.dow_star:
        return in_dom and in_dow
    return in_dom or in_dow


def _slot(day: date, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=tz)


def next_slot(spec: CronSpec, tz: ZoneInfo, after: datetime,
              horizon_days: int = 366) -> "datetime | None":
    """严格大于 `after` 的下一个整分槽位（半开区间的左开侧）；窗内无槽 → None。

    比较一律先把入参落到 `tz` 的墙钟上：cron 匹配的是**当地墙钟字段**，DST 缺口/回拨段里
    绝对瞬间与墙钟会脱钩，跨 tz 直接比会挑错轮次。
    """
    after = _require_aware(after, "after")
    anchor = after.astimezone(tz)
    day = anchor.replace(tzinfo=None).date()
    end = day + timedelta(days=horizon_days)
    hours = sorted(spec.hour)
    minutes = sorted(spec.minute)
    while day <= end:
        if _date_ok(spec, day):
            for hour in hours:
                for minute in minutes:
                    candidate = _slot(day, hour, minute, tz)
                    if candidate > anchor:
                        return candidate
        day += timedelta(days=1)
    return None


def prev_slot(spec: CronSpec, tz: ZoneInfo, at: datetime,
              horizon_days: int = 366) -> "datetime | None":
    """`slot ≤ at`（右闭）的最新整分槽位——due 判据：now 恰落整点该槽即 due，age=0。"""
    at = _require_aware(at, "at")
    anchor = at.astimezone(tz)
    day = anchor.replace(tzinfo=None).date()
    floor = day - timedelta(days=horizon_days)
    hours = sorted(spec.hour, reverse=True)
    minutes = sorted(spec.minute, reverse=True)
    while day >= floor:
        if _date_ok(spec, day):
            for hour in hours:
                for minute in minutes:
                    candidate = _slot(day, hour, minute, tz)
                    if candidate <= anchor:
                        return candidate
        day -= timedelta(days=1)
    return None


def slots_between(spec: CronSpec, tz: ZoneInfo, since: datetime, until: datetime,
                  limit: int = 1000) -> int:
    """开区间 `(since, until)` 内槽位数——「错过几轮」的计数口径：不含 until 本身
    （那一轮由本次 tick 认领，不是错过的），也不含 since（已处理过）。
    `limit` 是病态表达式的护栏：到顶就停并如实只数到 limit（最小间隔闸已在入库时拦了
    常态亚分钟表达式，168 小时窗的整点表达式远小于默认值）。"""
    since = _require_aware(since, "since")
    until = _require_aware(until, "until")
    anchor = until.astimezone(tz)
    count = 0
    cursor = since
    while count < limit:
        candidate = next_slot(spec, tz, cursor)
        if candidate is None or not candidate < anchor:
            break
        count += 1
        cursor = candidate
    return count


def wall_age(tz: ZoneInfo, moment: datetime, slot: datetime) -> float:
    """槽位的**墙钟域**年龄（秒，可为负只说明入参在槽前，调用方按未 due 处理）。

    decide_slot 的 2min/7d 判据用的是这个数，而不是绝对瞬间差：cron 表达的是「当地几点」，
    DST 段里 02:30 这个 wall 时刻要么不存在要么发生两次，绝对差会给出与窗口意图不符的年龄。
    """
    return (_wall(moment, tz) - _wall(slot, tz)).total_seconds()


def _sample_slots(spec: CronSpec, tz: ZoneInfo, start: datetime, samples: int) -> list:
    if not isinstance(start, datetime):
        raise ValueError(f"from_ 必须是 datetime，收到 {type(start).__name__}")
    if start.tzinfo is None:
        raise ValueError("from_ 必须带 tzinfo")
    slots = []
    cursor = start
    for _ in range(samples):
        candidate = next_slot(spec, tz, cursor)
        if candidate is None:
            break
        slots.append(candidate)
        cursor = candidate
    return slots


def min_gap_minutes(spec: CronSpec, tz: ZoneInfo, from_: datetime,
                    samples: int = 100) -> "float | None":
    """采样 `samples` 个槽位的最小相邻间隙（分钟）；采不满两个或窗内无槽 → None。"""
    slots = _sample_slots(spec, tz, from_, samples)
    if len(slots) < 2:
        return None
    gaps = [(later - earlier).total_seconds() / 60.0
            for earlier, later in zip(slots, slots[1:])]
    return min(gaps)


def min_gap_ok(spec: CronSpec, tz: ZoneInfo, from_: datetime, samples: int = 100) -> bool:
    """最小触发间隔闸（防手滑 `* * * * *` 烧配额）：采 100 槽，任一相邻间隙 <5min 判否。
    窗内排不出两个槽的表达式（如 `0 0 30 2 *` 永不命中）没有可违反的间隙——「永不触发」
    由它自己暴露，不在这里误判成间隔问题。"""
    gap = min_gap_minutes(spec, tz, from_, samples)
    if gap is None:
        return True
    return gap >= MIN_INTERVAL_MIN


def now_utc() -> datetime:
    """调度域统一「现在」（UTC aware；dispatch 缺省入参）。"""
    return datetime.now(timezone.utc)

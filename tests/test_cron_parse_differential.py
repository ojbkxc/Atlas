"""cron 解析的**双层**守卫：冻结参考表（CI 恒跑）+ croniter 实时差分（装着才跑）。

## 为什么值得钉

调度核（C1）的 `cron_parse` 是**自写** 5 段解析 + `next_slot`/`slots_between`，而调度器按
「错过几轮 / 连败」做决策——**算错的槽位不会报错，只会让巡检晚跑或漏跑**（与「静默丢弃」
同族：结果看起来正常，只是少了一次）。这类偏差在真实环境里要几天才显形，必须离线钉住。

## 两层设计（2026-11-24：第一版只有差分，CI 没有 croniter → 整份守卫在 CI 恒 skip）

1. **冻结参考表**（`test_frozen_next_slot_table` / `test_frozen_slots_between_table`）：
   8 形态 × 4 锚点 + 8 个窗口槽数，值由 croniter 差分跑绿后固化在本文件。**不依赖第三方库**，
   所以 CI 与本机都真跑——调度算错的后果不能只靠「有 croniter 的机器顺手验一次」。
   表要改必须同时说明「为什么新值是对的」。
2. **实时差分**（`test_next_slot_matches_croniter` 等）：装了 croniter 才跑（`_croniter()`
   里 `importorskip`），用来发现「冻结表覆盖不到的新形态」；不新增依赖（未进 pyproject）。

另两条与差分互补：`test_limit_guardrail_is_如实_not_silent`（护栏返回 limit 而非静默错数）、
`test_guard_reports_missing_reference`（缺参考实现时 skip 且说清，不伪装通过）。

参考实现装法（本地验证用，**不入 pyproject**）：`pip install croniter` 到一个临时 venv。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from atlas.scheduling.cron_parse import load_tz, next_slot, parse_expr, slots_between

def _croniter():
    """惰性取参考实现：**只**给差分用；冻结表与护栏测不依赖它（CI 不装也能跑）。"""
    mod = pytest.importorskip(
        "croniter", reason="参考实现 croniter 未安装——差分跳过；冻结表与护栏测同文件仍会跑")
    return getattr(mod, "croniter", None) or __import__(
        "croniter.croniter", fromlist=["croniter"]).croniter

TZ = load_tz("Asia/Shanghai")
ANCHORS = [
    datetime(2026, 11, 24, 9, 3, tzinfo=TZ),
    datetime(2026, 11, 24, 23, 58, tzinfo=TZ),
    datetime(2026, 12, 31, 23, 59, tzinfo=TZ),
    datetime(2027, 2, 28, 12, 0, tzinfo=TZ),
]
EXPRS = [
    "*/5 * * * *",
    "0 * * * *",
    "30 8 * * *",
    "0 9 * * 1",
    "0 0 1 * *",
    "*/15 2-4 * * *",
    "5,35 * * * *",
    "0 0 1 * 1",
]


def _ref_next(expr: str, after: datetime) -> datetime:
    return _croniter()(expr, after).get_next(datetime)


def test_next_slot_matches_croniter():
    mismatches = []
    for expr in EXPRS:
        spec = parse_expr(expr)
        for anchor in ANCHORS:
            got = next_slot(spec, TZ, anchor)
            want = _ref_next(expr, anchor)
            if got is None or got.replace(second=0, microsecond=0) != want.replace(
                    second=0, microsecond=0):
                mismatches.append((expr, anchor.isoformat(), str(got), want.isoformat()))
    assert not mismatches, (
        "cron 下一次触发与参考实现不一致（调度会晚跑/漏跑，且不会报错）：\n"
        + "\n".join(f"  {e} @ {a}: got={g} want={w}" for e, a, g, w in mismatches))


def test_slots_between_counts_match():
    mismatches = []
    # `limit` 是**病态表达式护栏**（默认 1000：到顶就停并如实只数到 1000）——差分时把
    # 上限抬到远超窗口槽数，否则量到的是护栏而不是语义（2026-11-24 首跑就撞上：
    # `*/5` 一个月 = 8927 槽 > 1000，`got=1000 want=8927`）。护栏本身另由第 5 条钉住。
    limit = 100000
    for expr in EXPRS:
        spec = parse_expr(expr)
        since = datetime(2026, 11, 1, 0, 0, tzinfo=TZ)
        until = datetime(2026, 12, 2, 0, 0, tzinfo=TZ)
        got = slots_between(spec, TZ, since, until, limit=limit)
        # 参考侧：从 since 起迭代数到 until（不含 until）
        ref, cur, guard = 0, since, 0
        while guard < 20000:
            nxt = _ref_next(expr, cur)
            if nxt >= until:
                break
            if nxt > cur:
                ref += 1
            cur = nxt
            guard += 1
        if got != ref:
            mismatches.append((expr, got, ref))
    assert not mismatches, (
        "窗口内槽数与参考实现不一致：\n"
        + "\n".join(f"  {e}: got={g} want={w}" for e, g, w in mismatches))


def test_dom_dow_or_semantics_match():
    """`0 0 1 * 1`：标准 cron 是「每月 1 号 **或** 每周一」——不是 AND。"""
    spec = parse_expr("0 0 1 * 1")
    since = datetime(2026, 11, 1, 0, 0, tzinfo=TZ)
    until = datetime(2027, 1, 1, 0, 0, tzinfo=TZ)
    got = slots_between(spec, TZ, since, until)
    cur, ref, guard = since, 0, 0
    while guard < 5000:
        nxt = _ref_next("0 0 1 * 1", cur)
        if nxt >= until:
            break
        ref += 1
        cur = nxt
        guard += 1
    assert got == ref, f"dom+dow 双受限语义不一致：got={got} want={ref}"


def test_guard_reports_missing_reference():
    """参考实现缺失时 `_croniter()` 会 skip 且说清原因——此处确认「skip 不等于通过」。"""
    assert _croniter() is not None
    # 同时钉住：UTC 与本地 tz 都可用（跨 tz 比较不因 tz 差异误判）
    utc_next = next_slot(parse_expr("0 9 * * *"), timezone.utc,
                         datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc))
    local_next = next_slot(parse_expr("0 9 * * *"), TZ,
                           datetime(2026, 11, 24, 0, 0, tzinfo=TZ))
    assert utc_next is not None and local_next is not None
    assert utc_next.astimezone(timezone.utc).hour == 9
    assert local_next.astimezone(TZ).hour == 9


def test_limit_guardrail_is_如实_not_silent():
    """`limit` 护栏语义：槽数超上限时**如实返回 limit**（不静默给错数、不无限算）。

    这条与差分互补：差分要「不受护栏干扰的真语义」，这条要「护栏本身的行为符合文档」。
    调度侧「错过几轮」用它兜病态表达式——返回 1000 表示「至少 1000」，消费方按贴顶处理。
    """
    spec = parse_expr("*/5 * * * *")
    since = datetime(2026, 11, 1, 0, 0, tzinfo=TZ)
    until = datetime(2027, 1, 1, 0, 0, tzinfo=TZ)     # 两个月，槽数远超 1000
    capped = slots_between(spec, TZ, since, until, limit=1000)
    assert capped == 1000, f"护栏应如实返回 limit，实为 {capped}"
    full = slots_between(spec, TZ, since, until, limit=100000)
    assert full > 1000, "窗口槽数本该超过 1000（否则这条测失去前提）"


# ---- 冻结参考表（无 croniter 也能跑：CI 依赖面不变）----
# 取值来源：2026-11-24 用 croniter（pip 装在**本地** .venv311，未进 pyproject）跑
# tests/test_cron_parse_differential.py 的差分全绿后，逐格把期望值固化在此。
# 口径：`Asia/Shanghai`；`next_slot` 为「若于 anchor 时刻 tick，下次触发的墙钟时刻」；
# `slots_between` 窗口 (2026-11-01, 2026-12-02) 开区间、limit=100000。
# 冻结表的意义：差分需要第三方库（CI 没装 → skip），而调度算错的后果是**漏跑/晚跑且不报错**，
# 不能只靠「有 croniter 的机器顺手跑一次」。表变了必须同时说明「为什么新值是对的」。
_FROZEN_NEXT = {
    ("*/5 * * * *", "2026-11-24 09:03"): "2026-11-24 09:05",
    ("*/5 * * * *", "2026-11-24 23:58"): "2026-11-25 00:00",
    ("*/5 * * * *", "2026-12-31 23:59"): "2027-01-01 00:00",
    ("*/5 * * * *", "2027-02-28 12:00"): "2027-02-28 12:05",
    ("0 * * * *", "2026-11-24 09:03"): "2026-11-24 10:00",
    ("0 * * * *", "2026-11-24 23:58"): "2026-11-25 00:00",
    ("0 * * * *", "2026-12-31 23:59"): "2027-01-01 00:00",
    ("0 * * * *", "2027-02-28 12:00"): "2027-02-28 13:00",
    ("30 8 * * *", "2026-11-24 09:03"): "2026-11-25 08:30",
    ("30 8 * * *", "2026-11-24 23:58"): "2026-11-25 08:30",
    ("30 8 * * *", "2026-12-31 23:59"): "2027-01-01 08:30",
    ("30 8 * * *", "2027-02-28 12:00"): "2027-03-01 08:30",
    ("0 9 * * 1", "2026-11-24 09:03"): "2026-11-30 09:00",
    ("0 9 * * 1", "2026-11-24 23:58"): "2026-11-30 09:00",
    ("0 9 * * 1", "2026-12-31 23:59"): "2027-01-04 09:00",
    ("0 9 * * 1", "2027-02-28 12:00"): "2027-03-01 09:00",
    ("0 0 1 * *", "2026-11-24 09:03"): "2026-12-01 00:00",
    ("0 0 1 * *", "2026-11-24 23:58"): "2026-12-01 00:00",
    ("0 0 1 * *", "2026-12-31 23:59"): "2027-01-01 00:00",
    ("0 0 1 * *", "2027-02-28 12:00"): "2027-03-01 00:00",
    ("*/15 2-4 * * *", "2026-11-24 09:03"): "2026-11-25 02:00",
    ("*/15 2-4 * * *", "2026-11-24 23:58"): "2026-11-25 02:00",
    ("*/15 2-4 * * *", "2026-12-31 23:59"): "2027-01-01 02:00",
    ("*/15 2-4 * * *", "2027-02-28 12:00"): "2027-03-01 02:00",
    ("5,35 * * * *", "2026-11-24 09:03"): "2026-11-24 09:05",
    ("5,35 * * * *", "2026-11-24 23:58"): "2026-11-25 00:05",
    ("5,35 * * * *", "2026-12-31 23:59"): "2027-01-01 00:05",
    ("5,35 * * * *", "2027-02-28 12:00"): "2027-02-28 12:05",
    ("0 0 1 * 1", "2026-11-24 09:03"): "2026-11-30 00:00",
    ("0 0 1 * 1", "2026-11-24 23:58"): "2026-11-30 00:00",
    ("0 0 1 * 1", "2026-12-31 23:59"): "2027-01-01 00:00",
    ("0 0 1 * 1", "2027-02-28 12:00"): "2027-03-01 00:00",
}
_FROZEN_COUNTS = {
    "*/5 * * * *": 8927, "0 * * * *": 743, "30 8 * * *": 31, "0 9 * * 1": 5,
    "0 0 1 * *": 1, "*/15 2-4 * * *": 372, "5,35 * * * *": 1488, "0 0 1 * 1": 6,
}


def test_frozen_next_slot_table():
    """冻结表（无第三方依赖）：调度算错不会报错，只会漏跑/晚跑。"""
    bad = []
    for (expr, anchor_text), want in _FROZEN_NEXT.items():
        anchor = datetime.strptime(anchor_text, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        got = next_slot(parse_expr(expr), TZ, anchor)
        got_text = got.strftime("%Y-%m-%d %H:%M") if got else None
        if got_text != want:
            bad.append(f"  {expr} @ {anchor_text}: got={got_text} want={want}")
    assert not bad, "cron 下一次触发与冻结参考表不一致：\n" + "\n".join(bad)


def test_frozen_slots_between_table():
    since = datetime(2026, 11, 1, tzinfo=TZ)
    until = datetime(2026, 12, 2, tzinfo=TZ)
    bad = []
    for expr, want in _FROZEN_COUNTS.items():
        got = slots_between(parse_expr(expr), TZ, since, until, limit=100000)
        if got != want:
            bad.append(f"  {expr}: got={got} want={want}")
    assert not bad, "窗口槽数与冻结参考表不一致：\n" + "\n".join(bad)

"""DST 缺口语义锁定：`next_slot` 在不存在墙钟时刻返回**顺延瞬间**（不是漏跑）。

## 观察到的真实行为（2026-11-24 实测，`Europe/Amsterdam`，`30 2 * * *`）

2026-03-29 本地 02:00→03:00 前跳，**02:30 不存在**。`next_slot` 返回
`2026-03-29T02:30:00+01:00`——字面墙钟 02:30，绝对瞬间 `UTC 01:30`，而该瞬间的真实本地
墙钟是 **03:30 +02:00**。即返回值是「缺口里的幽灵时刻」，但它指向的绝对瞬间正好是
**顺延后的第一个有效触发点**（03:30），符合 cron 惯例（跳过不存在的本地时间）。

同一瞬间，两条判定路径的 age 口径**不同且各自正确**：

- `cron` 分支用 `wall_age`（墙钟差）：本地 03:40 相对本地 02:30 = 4200s
- `once` 分支用绝对差：`now - due` = 600s（且 `schedule.at` 必须带偏移，docstring
  明写「决策按绝对时刻比，不猜本地墙钟」）

差 3600s 是 DST 缺口本身，不是 bug——但**这条差异此前没有任何测或注释记录**，将来有人
「统一 age 口径」时会把 cron 的墙钟语义改坏（cron 表达的是当地几点）。

## 三条

1. `test_gap_slot_points_at_first_valid_instant`：缺口槽的真实瞬间 = 顺延后的本地 03:30；
2. `test_fold_day_has_two_distinct_instants`：回拨日 02:30 出现两次（fold 0/1），
   两个绝对瞬间都能被构造出来且相差 3600s；
3. `test_age_semantics_differ_by_design`：同一 due 下 `wall_age` 与绝对差相差恰为一个
   缺口小时——**钉住「两种口径并存是设计」**，防后人统一成一把尺。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from atlas.scheduling.cron_parse import load_tz, next_slot, parse_expr, wall_age

try:
    TZ = load_tz("Europe/Amsterdam")
    _has_tzdb = True
except Exception:
    TZ = None
    _has_tzdb = False

needs_tzdb = pytest.mark.skipif(not _has_tzdb, reason="本机无 tzdb（需 tzdata 才能测 DST）")


@needs_tzdb
def test_gap_slot_points_at_first_valid_instant():
    spec = parse_expr("30 2 * * *")
    cand = next_slot(spec, TZ, datetime(2026, 3, 29, 0, 0, tzinfo=TZ))
    assert cand is not None
    # 字面墙钟是缺口里的 02:30（cron 匹配墙钟字段）
    assert cand.strftime("%H:%M") == "02:30"
    # 绝对瞬间落在前跳之后
    assert cand.astimezone(timezone.utc) == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
    # **必须经 UTC 往返**再回本地：`cand.astimezone(TZ)` 目标是同一个 ZoneInfo 对象，
    # CPython 会走恒等短路直接返回自身（仍是 02:30 +0100），于是「墙钟→瞬间→墙钟」的
    # 自检看起来恒稳定——2026-11-24 我第一版断言就栽在这，写下的 03:30 期望拿到 02:30。
    instant_local = cand.astimezone(timezone.utc).astimezone(TZ)
    assert instant_local.strftime("%H:%M %z") == "03:30 +0200"


@needs_tzdb
def test_same_zone_astimezone_short_circuits():
    """钉住上一条注释里的坑：同一 ZoneInfo 的 `astimezone` 不做墙钟重算。

    这不是本仓缺陷，是 CPython 语义；但任何「墙钟→瞬间→墙钟」自检若直接用
    `astimezone(same_tz)`，会得到「恒稳定」的假绿。
    """
    cand = datetime(2026, 3, 29, 2, 30, tzinfo=TZ)
    assert cand.astimezone(TZ).strftime("%H:%M %z") == "02:30 +0100"      # 恒等短路
    assert cand.astimezone(timezone.utc).astimezone(TZ).strftime("%H:%M %z") == "03:30 +0200"


@needs_tzdb
def test_fold_day_has_two_distinct_instants():
    first = datetime(2026, 10, 25, 2, 30, tzinfo=TZ)                 # fold=0 → +02:00
    second = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=TZ)        # fold=1 → +01:00
    assert first.utcoffset() != second.utcoffset()
    delta = second.astimezone(timezone.utc) - first.astimezone(timezone.utc)
    assert delta.total_seconds() == 3600, delta


@needs_tzdb
def test_age_semantics_differ_by_design():
    """`wall_age`（cron 墙钟）与绝对差（once 绝对时刻）**故意不同**，差 = 缺口小时。"""
    due = datetime(2026, 3, 29, 2, 30, tzinfo=TZ)                    # 缺口幽灵时刻
    now = datetime(2026, 3, 29, 1, 40, tzinfo=timezone.utc)          # 真实本地 03:40
    wall = wall_age(TZ, now, due)
    absolute = (now - due).total_seconds()
    assert wall == 4200.0, wall
    assert absolute == 600.0, absolute
    assert wall - absolute == 3600.0, (
        "两条 age 口径的差应恰为一个缺口小时——若变了，先确认没把 cron 的墙钟语义"
        "改成绝对差（cron 表达的是「当地几点」，见 decision.py 的 wall_age 用法）")


@needs_tzdb
def test_decision_uses_wall_age_not_absolute_diff():
    """**决策层行为面**：缺口日必须按墙钟判桶（不是绝对差）。

    构造（能真正分离两桶）：`30 2 * * *` @ Europe/Amsterdam，now = UTC 01:31 = 本地 03:31。
    `prev_slot` 给出缺口幽灵槽 `02:30+01:00`（UTC 01:30）。

    - 绝对差 = 60s ≤ 2min → 若用绝对差，trigger 会是 **`schedule`（准点）**
    - 墙钟差 = 03:31 − 02:30 = 3660s > 2min → 用 `wall_age`，trigger 是 **`catch_up`**

    两桶**可区分**，所以这条测真能挡住「把 cron 分支换成绝对差」。
    （前一版我写成源码文本断言 —— 正是本仓明令禁止的 `inspect.getsource` 钉文本形态，
    当场改掉。）
    """
    from atlas.scheduling.decision import decide_slot

    task = {"id": "t", "schedule": {"kind": "cron", "expr": "30 2 * * *",
                                    "tz": "Europe/Amsterdam"}}
    now = datetime(2026, 3, 29, 1, 31, tzinfo=timezone.utc)      # 本地 03:31 +02:00
    decision = decide_slot(task, now, since=None)
    assert decision.trigger == "catch_up", (
        f"缺口日按墙钟差应落 catch_up（3660s），实为 {decision.trigger!r}——"
        f"若为 'schedule' 说明 decision 改用了绝对差（60s）")
    assert decision.scheduled_for is not None
    assert decision.scheduled_for.strftime("%H:%M") == "02:30"

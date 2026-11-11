"""调度核常量（design_c1 §1）——唯一数字来源，消费方只许 import 本模块。

数值口径逐条对位 pi 磁盘源码（gap6 §9.1 C1 行）：
- `ON_TIME_GRACE_S=120`     pi types.ts:12 ON_TIME_GRACE_MS=2min
- `CATCH_UP_WINDOW_S=7d`    pi types.ts:14 CATCH_UP_WINDOW_MS=7d
- `MAX_CONSECUTIVE_FAILURES=5` pi types.ts:4 连败 5 次自动暂停
- `MIN_INTERVAL_MIN=5`      pi types.ts:4 最小触发间隔（防手滑 `* * * * *` 烧配额）
- `LEASE_TTL_S=90`          pi lease.ts:10 LEASE_STALE_MS=90_000（S2：TTL 为权威判据）
- `HEARTBEAT_INTERVAL_S=30` pi scheduler.ts:26 同款节拍（派发期间续租）
"""
from __future__ import annotations

ON_TIME_GRACE_S = 120
CATCH_UP_WINDOW_S = 7 * 86400
MAX_CONSECUTIVE_FAILURES = 5
MIN_INTERVAL_MIN = 5
DEFAULT_MAX_DURATION_MIN = 30
LEASE_TTL_S = 90
HEARTBEAT_INTERVAL_S = 30

# 台账 schema 代次：读侧版本不符当场点名，不猜旧形态（与 #77「默认值只在键缺失时生效」同族）
SCHEMA_VERSION = 1

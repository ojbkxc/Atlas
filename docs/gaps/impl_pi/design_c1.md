# 设计 C1：调度核（波次 W2，最大项；按 Atlas 形态缩小）

> 上游：gap6 §3.1-C1、§4-C1、§9〔S1/S2〕（**C1 维持 P1；拆 C1a 语义核 / C1b pid 探针后置**）。
> 形态钉死：`atlas schedule run <id>` 是**被 OS cron/任务计划器调起的一次性命令**，Atlas 不造常驻守护进程、不自注册 OS 任务（触 Ask-first）。pi 侧论据全部对位磁盘源码（types.ts:4-14 / catch-up.ts:24 / cron.ts:64 / lease.ts:10-35 / task-input.ts:196-214 / scheduler.ts:176-180/348 / runner.ts:98/176）。
> 实施者：impl_sched（任务 #5；W2 内 `cli.py` 单主）。

## 1. 模块与新增文件

```
src/atlas/scheduling/__init__.py     # 导出 decide_slot / try_acquire ... 公开面
src/atlas/scheduling/constants.py    # ON_TIME_GRACE_S=120 / CATCH_UP_WINDOW_S=7*86400 /
                                     # MAX_CONSECUTIVE_FAILURES=5 / MIN_INTERVAL_MIN=5 /
                                     # DEFAULT_MAX_DURATION_MIN=30 / LEASE_TTL_S=90
src/atlas/scheduling/cron_parse.py   # 5 段自写解析器（纯 stdlib，不引 croniter——纯 stdlib 先例，非红线字面禁）
src/atlas/scheduling/decision.py     # decide_slot 纯函数（无 IO、无时钟参数注入）
src/atlas/scheduling/lease.py        # O_EXCL+TTL 租约（C1a 权威判据）+ pid_alive 接口占位（C1b）
src/atlas/scheduling/state.py        # 任务台账读写 / apply_patch（水位线重置规则）/ schedule_runs 追加
src/atlas/scheduling/dispatch.py     # 一次性 tick：体检→租约→决策→认领→派发→记录
tests/test_scheduling_cron.py / test_scheduling_decision.py / test_scheduling_lease.py /
tests/test_scheduling_state.py / test_scheduling_cli.py
```
状态文件全在 `reports/schedule/`（已被 gitignore——真实域名/账号名产物天然不入库，红线 #11/PRIVACY 同族）：
`tasks.json`（台账）、`schedule_runs.jsonl`（调度域索引，与 `runs.jsonl` **分离**：调度「错过几轮」不污染业务缺陷分布，运行索引隔离语义）、`lease/<task-id>.lock`。

## 2. cron_parse —— 5 段自写解析器 + 半开区间 + +1s 钉整点

```python
def parse_expr(expr: str) -> CronSpec        # ValueError 点名第几段、收到什么
def next_slot(spec, tz: ZoneInfo, after: datetime, horizon_days=366) -> datetime | None
def prev_slot(spec, tz, at: datetime) -> datetime | None      # ≤ at 的最新槽位
def slots_between(spec, tz, since: datetime, until: datetime, limit=1000) -> int  # (since, until) 开区间计数
def min_gap_ok(spec, tz, from_: datetime) -> bool             # 最小间隔闸：采 100 槽，任一小时隙 < 5min 判否
```
- 字段：分 0-59 / 时 0-23 / 日 1-31 / 月 1-12 / 周 0-6（**7 归一为 0**，0=周日，POSIX 口径）。
- 语法子集：`*`、`a`、`a-b`、`*/n`、`a-b/n`、逗号列表；**命名月/周（JAN/MON）显式拒绝并点名**（不支持就说不可，不装支持——M15 归因纪律）。恰 5 段，6/7 段拒。
- dom/dow：**两者都受限时按 POSIX vixie 语义取 OR**，任一为 `*` 取 AND；两 case 必测（这是自写器最易漂移的一条，croner/crontab 同语义，naive AND 实现是错）。
- 内部表示：槽位恒为**整分钟** tz-aware datetime（second/microsecond=0）。pi 的 `previousRuns(1, at+1000)` 是给 croner 秒级截断打的补丁；自写整分步进**无截断面**，但等值纪律照钉：
  - `due = prev_slot(now)` 判据为 **`slot ≤ now`（右闭）**：now 恰落整点 → 该槽即 due，age=0 → 准点触发；now=due−0.5s → 未 due → none。
  - 认领用半开区间 `(since, due]`；skipped 计数用开区间 `(since, due)`。
  - 等价于「+1s 钉整点」的效果由整分表示天然达成，**测试必须直接钉这三个等值 case**（整点/半秒后/整点前半秒）。
- 搜索实现：按天剪枝（日/月字段先筛，命中日再枚举分钟），最坏 366d 窗内 <52 万步、常态毫秒级；性能不是正确性口径，边界表才是。
- 时区：`zoneinfo.ZoneInfo(name)`；`ZoneInfoNotFoundError` → rc2 文案点名「Windows 无系统 tzdb，先 `pip install tzdata`」（X6；doctor `tz_data` 体检已由 C6 前置落好）。

## 3. decision —— `decide_slot` 纯函数与边界表（用户拍板口径）

```python
@dataclass(frozen=True)
class SlotDecision:
    action: str          # "none" | "fire" | "drop"
    trigger: str = ""    # "schedule" | "catch_up"（fire 时）
    scheduled_for: datetime | None = None
    skipped_slots: int = 0

def decide_slot(task: dict, now: datetime, since: datetime) -> SlotDecision
```
| # | 输入条件 | 输出 | 依据 |
|---|---|---|---|
| 1 | `schedule.kind == "manual"` | none | catch-up.ts:26 |
| 2 | once：`at > now` | none；创建/改配置前的槽不归它补 | task-input.ts:134 |
| 3 | once：`at ≤ now` | 按 age 走 4–6（trigger=catch_up 常态） | catch-up.ts:30-32 |
| 4 | cron/once age ≤ **2min** | fire, schedule, skipped=\|(since,due)\| | ON_TIME_GRACE_MS |
| 5 | **>2min 且 ≤7d** | fire, **catch_up，只补最近一轮**（更老的槽计入 skippedSlots 不重放——报告能答「这台机错过几轮」，不会重放烧配额） | catch-up.ts:16-48 |
| 6 | **>7d** | drop（水位线推进，记录 skipped+1） | CATCH_UP_WINDOW_MS |
| 7 | `due ≤ since`（已处理过/无槽） | none | catch-up.ts:39 |
| 8 | **改配置（cron/tz/flow/run_args 任一）或重新 enable** | `apply_patch` 重置 `last_scheduled_for=now` + 清 `consecutive_failures` + 删 `auto_paused_reason`；once 任务改 schedule 自动回 enabled（暂停于跑完的，非故障的） | task-input.ts:196-214（**人改配置的瞬间不回放历史**） |
| 9 | `consecutive_failures == 5` | dispatch 置 `enabled=false` 并写 `auto_paused_reason`（最后一条失败摘要），`schedule list/status` 醒目显示 AUTO-PAUSED + 原因 | scheduler.ts:348 |
| 10 | 计败判据：**timeout=计败；interrupted 不计败；业务 rc≠0 计败；defer/lease 占用/drop 均不计败** | 「环境挂一整晚」→ 5 败自动暂停留因，不每小时刷失败烧请求；手动 Ctrl-C/被杀属 CONTROL_FLOW（**#114 在调度域的第二实现，不立新规则**，gap6 §5 交叉验证表） | runner.ts:98 vs :176 |

## 4. lease —— O_EXCL + 心跳 TTL 为权威（S2 口径），pid 探针后置 C1b

```python
def try_acquire(lock_path: Path, token: str, pid: int, now: float, ttl_s: float = 90) -> bool
def heartbeat(lock_path: Path, token: str, now: float) -> bool          # 派发期间由守护线程每 30s 续（stdlib threading，随 with 结束停）
def read_lock(path) -> dict | None        # {token,pid,host,started_at,heartbeat_at}；坏 JSON/非映射 → None（视同无锁但记噪音行）
def release(path, token) -> None         # 仅 token 相符才删
def pid_alive(pid: int) -> bool | None    # C1a 恒返回 None（=不确定）；接口在此，实现在 C1b
```
- 抢锁：`os.open(path, O_CREAT|O_EXCL|O_WRONLY)` 成功→写 token 立即 `os.close`（X3：句柄不过夜，Windows 删除才不锁）；FileExistsError→读锁：
  - **心跳 ≤90s → 有人活着：不抢占，本轮 defer**（TTL 为权威判据，S2）；
  - 心跳 >TTL → 上一任崩了/孤儿：`unlink`（PermissionError 重试 1 次 100ms，仍败→**defer 退出 rc0**，绝不硬删——X3 Windows 差异）→ 重回抢；抢到后**回读锁文件确认 token 是自己的**（pi lease.ts:89 双检，防两个进程同瞬互踩）；接管成功额外落 `{"outcome":"lease_reclaimed","stale":{"pid":…,"host":…}}` 记录——pi `recoverOrphanedRuns` 收尾语义的 Atlas 形态（一次性进程没有「遗留 running 行」可封，孤儿证据由 reclaim 记录 + runs.jsonl 残行回答）。
  - C1b 启用后：心跳新鲜但 `pid_alive() is False`（探针**确定**死亡）→ 允许提前接管；`pid_alive() is None`（不确定）→ **按推迟记录，不判失败**（与 waiting≠failed 同族，S2 细则）。
- pid 探针（后置，设计只钉不实现）：Windows **禁** `os.kill(pid,0)`（=CTRL_C_EVENT 广播进进程组+组语义误杀健康持有者，gap6 §3.1）；用 ctypes `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`+`GetExitCodeProcess` 比对 `STILL_ACTIVE=259`——已知冲撞：真退出码 259 会判存活→方向是**永不接管→停摆**而非双跑，故仍维持「TTL 权威、探针只加速」；备选 `tasklist /FI "PID eq <pid>"`（argv，X2）。必配两个平台 case：「进程存在但非组主」「pid 已复用」。
- `STILL_ACTIVE` 与容器 pid 复用：单 pid 判据本就不可靠，**心跳+pid 双条件才允许抢占**是 pi 的正确设计，C1a 用 TTL 单独成立（崩溃/孤儿主场景），冻结场景由 `max_duration` abort 兜——S1 论据原文，实现不得私自把探针提进 MVP。

## 5. state —— 台账与调度索引 schema（字段级）

`reports/schedule/tasks.json`：
```json
{"schema_version": 1,
 "tasks": {"ics-patrol-eu": {
   "id": "ics-patrol-eu",
   "flow": "flows/patrol/eu.yaml",              // root 相对 POSIX 字符串（X1）
   "run_args": ["--account", "ops", "--ou", "EU"], // argv 透传；只准账号名，密码恒从 env（PRIVACY 同 session.json 口径）
   "schedule": {"kind": "cron", "expr": "*/30 1-8 * * *", "tz": "Europe/Amsterdam"},
   "max_duration_min": 30,
   "enabled": true, "writes_acknowledged": false,
   "created_at": "…", "updated_at": "…",
   "last_scheduled_for": null,
   "consecutive_failures": 0,
   "auto_paused_reason": null }}}
```
`reports/schedule/schedule_runs.jsonl` 每行（派发完成才追加，单行全事实）：
```json
{"run_key": "hex12", "task_id": "…", "trigger": "schedule|catch_up|manual",
 "scheduled_for": "…|null(manual)", "skipped_slots": 3,
 "started_at": "…", "ended_at": "…",
 "outcome": "succeeded|failed|aborted_time_limit|interrupted|defer_lease|dropped|lease_reclaimed",
 "counts_as_failure": false, "child_rc": 1, "child_command": "argv 摘要（不含值参）",
 "engine_run_id": "…|null"}
```
写侧：`tmp+os.replace`（X9）；追加前尾字节断行检测（X5，experience.py:64-77 同款）。读侧沿用 replace 回落逐行容错纪律（#112/#131——schedule_runs 也禁不起一条坏字节顶掉整表）。
once 任务被认领即 `enabled=false`（scheduler claimSlot 语义 :205）。

## 6. dispatch —— 一次性 tick 流程与护栏

```python
def tick(task_id: str, config, *, now: datetime | None = None, dry: bool = False) -> int
```
1. 台账读/校验（id 不存在 → rc2 点名）；`enabled=false` → rc0 静默（AUTO-PAUSED 的答复是 `status`，cron 不需要噪声）。
2. **只读护栏（Atlas 形态，不移植 pi 的 toolPreset）**：`add/edit` 时离线 `validate_flow` + registry 元数据扫 `read_only=False` 步骤——含写任务且无 `--i-accept-writes` → **拒绝入库**（产出侧强制确认，M14；`writes_acknowledged` 是人的决定记录）。运行期兜底第二层：生产 env 的写被 engine 既有 `validate_write` 拒（红线 #2/#13，engine 零改动）。
3. **调度器永不带 `--approve`**：argv 组装固定 `[sys.executable, "-m", "atlas", "run", flow, *run_args]`；装配处 `assert "approve" not in run_args and "--mode" not in run_args`（防手滑注入），**专项测钉死**（含 `atlas schedule add --run-arg --approve` 当场拒）。
4. 租约 try_acquire → 失败即 `defer_lease` 记录、**不推进水位线**、rc0（推迟≠跳过：OS cron 下一 tick 会再来；与 pi overlap-recordSkipped 推进水位的差异**有意**——pi 是 30s tick 常驻，Atlas 的 tick 就是 cron 本身，占着锁的上一任才是那轮的合法执行者）。
5. `decide_slot(task, now, since=last_scheduled_for or created_at)` → none: rc0；drop: 推进水位线+记录；fire: 
6. **CAS 认领**：重读 tasks.json 校验 `last_scheduled_for == since` 后写新值（另一进程先赢→本轮放弃，幂等退出 0）。
7. `subprocess.run(argv, cwd=root, timeout=max_duration_min*60, env={**os.environ, "PYTHONIOENCODING":"utf-8"})`（X2/X5/X7），心跳线程期间续租。
8. 终态映射（§3 表 10）：TimeoutExpired→`aborted_time_limit`（子进程已被 kill()——POSIX SIGKILL/Windows TerminateProcess，**不自己发信号**，X7）计败；KeyboardInterrupt（人 Ctrl-C 调度器）→ 子进程同组同收（不用 start_new_session）记 `interrupted` 不计败；rc≠0 计败归零通过。
9. 计败 ≥5 → 置 `enabled=false`+`auto_paused_reason`，stderr 一行 `AUTO-PAUSED: <id> after 5 consecutive failures (<摘要>)`（X4 ASCII）。
10. finally：`release(lock, token)`；台账/索引落盘失败不顶业务信号（#110/#130 同纪律）。

## 7. CLI 与 OS 接线（文档义务 X8）

子命令（cli.py W2 单主，parents=[global_args]）：
`schedule add|list|status|enable|disable|edit|remove|run <id> [--dry-run]|crontab <id> [--platform auto|linux|windows]`。
- rc 口径同全局：2 用户错误（坏 id/坏 cron/坏 tz/缺 tz）、0 一切调度结果（defer/none/drop 都是正常答复——cron 不看 rc 干活，人看 schedule_runs/status）、1 仅派发子进程失败。
- `status` 汇总「错过几轮」= `sum(skipped_slots)` 按任务分组——G2 交付答案。
- `crontab` 输出两形态（`--platform auto` 按 `sys.platform`）：
  - Linux：`*/30 * * * * cd /path/to/Atlas && python -m atlas schedule run ics-patrol-eu >> /path/to/Atlas/reports/schedule/cron.log 2>&1`
  - Windows：`schtasks /Create /SC MINUTE /MO 30 /TN atlas-ics-patrol-eu /TR "python D:\...\Atlas\src -m atlas schedule run …"` 形态由实现期以真实可用语法钉（`/TR` 引号转义是已知坑——**输出给用户复制的成品行，Atlas 绝不代跑 schtasks**，不自动注册，Ask-first 边界），并同步写 README「定时巡检接线」小节。
- 交付文档义务：README 新增小节含双平台接线 SOP、时钟漂移提示（调度按**本机墙钟**，NTP 漂移>grace 会自然表现为 catch-up，不额外校时）。

## 8. 测试清单（全离线、无真实 cron/浏览器/网络）

- decision 边界表 1–10 逐行参数化（含整点/半秒后/整点前半秒三 case、7d±1s、改配置重置、连败第 5 次暂停且 interrupted 序列不累加、timeout 序列累加）。
- cron_parse：各字段形态、7=0、names 拒、dom/dow OR 两 case、坏段点名段号、min_gap（`* * * * *` 拒）、整分步进幂等。
- lease：抢/续/释放、token 不符不删、stale 接管+reclaim 记录、**模拟 unlink 抛 PermissionError → defer**（X3 Windows 面）、并发双抢只一个赢（两 token 回读）、`pid_alive` 在 C1a 恒 None 且 dispatch 走「不确定→推迟」。
- state：schema 往返、once 认领自动停、CAS 双写只一赢、追加断行（残行样本）。
- dispatch：fake subprocess monkeypatch 全终态映射、argv 永不出现 approve（专项断言）、PYTHONIOENCODING 注入断言、心跳线程起停无泄漏（join 超时）。
- CLI：add/list/status/dry-run/crontab 双平台文案、rc 口径表（2/0/1 各至少 1 测）。
- 新增测下限 **≥45**；772 不回退。

## 9. 红线五查与不吸收项

`stdlib ✓（cron 自写、zoneinfo、threading；APScheduler 属常驻运行时确触 §4 Ask-first 禁；croniter 不用是「纯 stdlib 先例+评审项」不是字面红线——E2 口径不得写反）/ 断言 ✓（调度不碰判定）/ 知识写路径 ✓（只读 flow/写调度索引数据）/ 依赖 ✓ / 离线 ✓`；红线 #2/#13 双层护栏 §6.2/6.3；红线 #12：无使用场景的通知扇出/并发闸（C7）不进本设计。
**明确不做**：常驻 tick、OS 任务自注册、C1b 探针实现（接口+测试要求已钉，落地单列后续评审）、多任务并发闸（单实例单租约即 Atlas 形态的正确颗粒）。

## 10. 未验证/开放问题（C16）

- 自写 cron 与真实 crontab 语义的逐字对齐只测到设计列的 case；DST 半小时区（Australia/Lord_Howe）未列 case——交付标注「按 zoneinfo fold 语义，未实证」。
- Windows `/TR` 引号语法在实现期须用真实 schtasks 冒烟一次（文档义务的一部分，离线测替代不了）。
- 心跳线程 + subprocess.run 阻塞在 Windows 控制台 Ctrl-C 下的分发竞态（子进程已死、记录未写）→ 以 `aborted` 收尾的路径需真机复核一次，离线以 fake 钉行为不声称等价。
- 本机/CI runner 时钟漂移未评估（gap6 §7 原样继承，不在本设计关闭）。

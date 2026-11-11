# pi-agent-desktop 机制盘点（供 Atlas 吸收评估）

- 盘点人：pi_inventory（team atlas-absorb-pi-eval，任务 #1）
- 日期：2026-11-05
- 对象：`D:\GitHub\pi-agent-desktop`（ojbkxc fork，package 名仍为 `@agegr/pi-web` 0.10.0，上游 `abcwyc/pi-agent-desktop` / `agegr/pi-web` + `@earendil-works/pi-*` SDK 1.5.0）
- 视角：**只读盘点**。跨语言（TS/Next.js/Rust ↔ Python），因此这里只描述「设计模式与流程机制」，不谈代码复用。
- 纪律：本文件是唯一写入产物；未修改 pi-agent-desktop / Atlas 任何代码，未 commit/push，未跑浏览器或真机会话。

---

## 1. 顶层子系统全貌

| 子系统 | 职责 | 规模（源码行 / 同名 `.test.mjs` 行） | 备注 |
|---|---|---|---|
| `app/` | Next.js App Router：页面 + `app/api/**` HTTP 层（~40 个 route group：`agent/`、`sessions/`、`scheduled-tasks/`、`teams/`、`worktrees/`、`files/`、`cwd/`、`relay/`、`remote-access/`、`push/`、`auth/`、`mcp/`、`models*/`、`tools/`、`terminal/`、`updates/`、`web-auth/`、`desktop/`、`artifacts/`…） | 111 文件 / 9 012 行；测试 5 026 行 | route 层刻意薄：只做鉴权 + 校验 + 调 `lib/` |
| `lib/` | 服务端域逻辑主体：会话生命周期、扫描器、文件安全、MCP、模型、定时任务、relay、团队、worktree、更新 | 303 文件 / 54 542 行；测试 40 559 行 | 测试比约 0.74 : 1，纯函数优先 |
| `components/` | React UI（AppShell/SessionSidebar/ChatWindow/SettingsPanel + `scheduled/`、`tasks/`、`AgentTeams/`、`artifacts/`、`desktop/` 子域） | 119 文件 / 44 585 行；测试 14 018 行 | 结构性分叉集中在 3 个文件（见 §M11） |
| `hooks/` | 客户端会话状态机（`useAgentSession.ts`）、定时任务 store、worktree 动作、session automation | 35 文件 / 6 254 行；测试 2 214 行 | |
| `relay/` | 独立部署的中继服务器（Node + `ws`），按 agent id 转发密文帧 | 347 行 + 304 行测试 | Dockerfile + docker-compose（含 Caddy） |
| `relay-shell/` | 手机端「壳」：保 WS + Noise 会话、iframe 承载完整 UI、隧道 service worker | 682 行 | `npm run relay:shell` 构建到 `dist/`，部署在独立入口域名 |
| `src-tauri/` | Rust 桌面壳：updater、原生菜单/弹窗、托盘、窗口状态、文件保护、沙箱 srcdoc 放行、NSIS 覆盖安装钩子 | 13 文件 / 3 394 行 | `tauri.conf.json` + dev/linux/windows 三份覆盖；`capabilities/`、`permissions/` |
| `desktop/` | 打包后的 sidecar 启动器（`server-launcher.cjs`，捆绑 Node 起 Next 生产服务）+ `frontend/` | .cjs，未计入 TS 统计 | 关窗只是 `hide`，server 常驻 → 定时任务仍触发 |
| `scripts/` | 发布与治理工具链：`fork-ownership.*`、`measure-fork-drift.mjs`、`upstream-css-baseline.*`、`release-components.mjs`、`verify-release-components.mjs`、`write-component-versions.mjs`、`configure-updater-public-key.mjs`、`check-installed-deps.mjs`、`prepare-desktop.mjs`、`verify-packaged-*.mjs`、`lan-server.mjs`、`build-relay-shell.mjs` | 25 文件 / 2 231 行；测试 1 754 行 | 「治理即代码」密度最高的区域 |
| `e2e/` | 脚本式 Playwright 回归（无 jest/vitest，`node e2e/xxx.mjs` 自起服务） | 35 文件 / 4 132 行 | 见 §M19 |
| `docs/` | `adr/0001-0007`、`agents/*.md`（上游主题文档）、`fork-*.md`（本 fork 主题笔记，冲突时 fork 为准）、`agent-teams-design.md`、`scheduled-tasks-spec.md`、`ownership-boundaries.md`、`remote-relay.md`、`desktop-updates.md`、`worktrees.md`、`merge-history.md`、`fork-sync.zh-CN.md` | 21 + 13 篇 | AGENTS.md 的 Topic Notes 是索引入口 |
| `public/` | 预览运行时（内联 React/Sucrase/Tailwind，供沙箱 iframe）、`lane-diagram.html`、`sw.js` | 1 314 行 | |

**依赖要点（package.json）**：`@earendil-works/pi-{agent-core,ai,coding-agent,tui}@^1.5.0`（agent 运行时全部来自 SDK，本仓库不做推理循环）；`next@16.3.8` + `react@19`；`@tauri-apps/api@2.12` + 7 个 plugin（updater/autostart/dialog/notification/process/clipboard）；`croner@10`（cron）、`proper-lockfile@4.1.2`（跨进程文件锁）、`node-pty@1.2.0-beta`、`@xterm/xterm@6`；`pi-team` 与 `lsp-mcp` 以 git 依赖引入（`github.com/ojbkxc/*`）；`web-push`、`qrcode-generator`、`semver`、`typebox`（工具入参 schema）、`undici`、`js-yaml`、`mammoth`。
**脚本要点**：`test` = `node --experimental-strip-types --test --test-concurrency=4` 扫 8 个目录的 `*.test.mjs`；`pretest` 先跑 `check:deps`；`drift` / `release:manifest` / `release:verify` / `desktop:prepare` / `desktop:verify` / `check:branding` 各司一段治理；`test:e2e` 串行 14 个浏览器脚本。

---

## 2. 机制清单

> 每条含：**解决什么问题 / 关键实现位置 / 复杂度 / 对 Atlas 的意义**。复杂度：低（≈1 人日概念落地）、中（数百行 + 状态机）、高（跨层 + 回归纪律）。

### M1 会话浏览 / 恢复 / 两种「分支」

- **解决什么**：`~/.pi/agent/sessions/<encoded-cwd>/*.jsonl` 可增长到上万文件、单个 135 MB；既要「读一眼就走」不常驻内存，又要在**会话正在跑**时安全分叉。
- **关键位置**：
  - `AGENTS.md:59` File Map（子系统索引）+ `:239` "Two kinds of branching" + `:213` **Hard rules**（20+ 条，每条对应一次真实回归）；`docs/agents/sessions.md`（全文，尤其 L10-20）
  - `lib/rpc-manager.ts:2850 startRpcSession()`（重启后 `SessionManager.open` + 重附着）、`:2527 getRpcSession()`、`resolveSessionIdleTimeoutMs` `:233`
  - `lib/session-list-scanner.ts`（增量扫描：size/mtime 指纹，只解析新增/变更文件；`listAllSessions({allowStale:true})` 读上一代缓存）
  - `lib/idle-session-cap.ts:18 IDLE_SESSION_CAP_MAX_BYTES = 256 MB`、`:38 selectIdleSessionsToEvict()`（**按字节封顶而非按条数**，最近离开的一个永远保留）→ `lib/rpc-manager.ts:2412 enforceIdleSessionCap()`
  - fork：`hooks/useAgentSession.ts:2086`（`send("fork")`）；实现上**绝不**用 `AgentSession.fork()`，而是另开 `SessionManager` + `createBranchedSession()`，因此「源会话在跑时也能分叉」（`docs/agents/sessions.md` L10-13）
  - in-session branch：`navigate_tree` 在同一文件内切叶子，流式/压缩中被禁止（`docs/agents/sessions.md` L15-17），UI 侧 `components/BranchNavigator.tsx`
  - 树/族：`lib/session-tree.ts:8 buildSessionTree()`（fork 仍是根，只有 subagent 挂到父下）、`lib/session-family.ts`
  - 冷启动恢复：`lib/workspace-state.ts`（URL 参数优先，否则回落到上次会话/文件页签）
- **复杂度**：高（规则多为「回归换来的硬规矩」，AGENTS.md 里 20+ 条 Hard rules）。
- **对 Atlas 的意义**：Atlas 的用例执行记录/经验笔记同样是「一执行一文件」的追加型 jsonl 语料。可移植的是三件事：**按字节预算回收热会话（不是按条数）**、**增量扫描 + stale 读**、**「读浏览」与「跑执行」两条路径彻底分离（浏览不建会话）**。

### M2 AgentSession 生命周期与「关不掉」兜底

- **解决什么**：第三方扩展/tool 忽略 abort 信号时 Stop 无效；热重载导致模块级 Map 出现两份状态；worker 崩溃留下僵尸包装器。
- **关键位置**：`docs/agents/sessions.md` L3-9；`lib/rpc-manager.ts`：`globalThis.__piSessions`、`globalThis.__piStartLocks`（并发 `startRpcSession` 共享同一个启动 Promise）、`forceShutdownOnIdle`（首次 Stop 后按下限 10 分钟强制收尾，后续命令不得顺延该期限）、`resolveSessionShutdownDeadlineMs` `:257`（扩展 `session_shutdown` 只给 5 s，超时照常 dispose）。
- **复杂度**：中-高。
- **对 Atlas 的意义**：Atlas 的 dispatch/engine 步骤同样有「步骤挂住但无人收尾」的问题。可移植模式：**Stop 只表意，真正兜底的是「从首次 Stop 起算的硬 deadline 计时器」**，以及**共享态一律挂 `globalThis` 等价的进程级单例**（Python 侧 = 模块级 registry + 锁，而不是 per-call 对象）。

### M3 定时任务：租约 + 补跑 + 背压 + 自动暂停

- **解决什么**：本机到点自动开一个会话跑提示词；关窗（托盘）仍触发；开发服务器与桌面 App 同时读同一目录时**只允许一个进程派发**；睡眠/关机错过的时间点要补跑但不能重放全部。
- **关键位置**：设计 `docs/scheduled-tasks-spec.md`（356 行，D1-D7 + §11-15 实现/复查记录）、`docs/agents/scheduled-tasks.md`、`docs/adr/0007-scheduled-tasks.md`。实现 `lib/scheduled-tasks/`：
  - `scheduler.ts`：30 s tick（**不用 setTimeout 对齐下次时间**，避免睡眠漂移）、`claimSlot()`、并发上限 2、连续失败 5 次 `enabled=false`
  - `lease.ts:11 LEASE_STALE_MS=90_000`、`:28 isPidAlive()`（EPERM 视为存活）、内容 `{pid, startedAt, heartbeatAt}`，接管时把上一进程留下的 `running` 记录封成 `aborted`
  - `catch-up.ts:26 decideSlot()`：**纯函数**，≤2 min 算准点、7 天内补跑一次并把更早槽位计入 `skippedSlots`、超 7 天丢弃
  - `cron.ts`：只接受 5 段表达式、最小间隔 5 min；`previousCronRun()` 用「参考时间 +1 s」绕开 croner 整点不含等值点的坑（spec §11）
  - `runner.ts`：`startRpcSession("__sched__<runId>")` 直接复用普通会话创建链；写 `pi-web:scheduled-run` custom entry 作为「索引丢失后可重建」的真相源；`maxDurationMin` 到点 abort；手动 Stop 不计失败
  - `run-index.ts`：`sessionId → run` 内存索引，让运行会话默认**不出现在项目树**（避免每小时一条淹没分组）
  - `task-input.ts`：唯一校验入口；写类预设必须带 `acknowledgeUnattendedWrites`（服务端强制）
  - `run-error.ts`：错误以 `errorCode`/`errorParams` 传递，本地语言由 UI 拼句（超时/中断/停止/目录缺失）
  - 启动：`instrumentation-node.ts`（route 懒加载不能作为调度入口）；`PI_WEB_DISABLE_SCHEDULER=1` 可关
- **复杂度**：高（但核心 decideSlot/lease/store 是纯 stdlib + 文件锁，形态最轻）。
- **对 Atlas 的意义**：**这是本次盘点里性价比最高的一整块**。Atlas 的 JMeter 巡检（`D:\jmx`）+ 计划回归正好缺这层：租约防双跑、纯函数补跑决策、overlap 直接 skip 不排队、连续失败自动暂停并把暂停原因回显、运行与会话的双向索引。spec 的 D1-D7 决策表可以直接改写为 Atlas 的 ADR。
- **⚠ 移植陷阱（Windows，本盘点补记）**：`lease.ts:28 isPidAlive()` 用 `process.kill(pid, 0)` 作空探针，但 Windows 上 Python 的等价写法 `os.kill(pid, 0)` **不是探活** —— 本机实测 `signal.CTRL_C_EVENT == 0`，CPython 在 Windows 把 0/1 当控制台事件走 `GenerateConsoleCtrlEvent(evt, 进程组id)`，其余值走 `TerminateProcess`。即「向以该 pid 为组主的进程组广播 Ctrl+C」，可能连带打断调度器自己；且存活但非组主的 pid 同样抛异常 → 「异常=已死」的判定不成立，会把健康持有者误判为可抢占（正是租约要防的双跑）。替代：ctypes `OpenProcess`+`GetExitCodeProcess`（比 `STILL_ACTIVE=259`）、`tasklist /FI "PID eq <pid>"`、或 `psutil.pid_exists`。另：Atlas 当前无任何 pid/lease 实现（`grep os.kill|pid_exists|lease|psutil src/` 无命中），属新增代码，须设计期定形。心跳时效与 pid 判定**双条件**才可接管这一点应原样保留。

### M4 每浏览器一条事件流（Web Locks 选主 + BroadcastChannel）

- **解决什么**：HTTP/1.1 单域 ~6 连接，每标签页一条 SSE 会让多标签互相饿死。
- **关键位置**：`lib/scheduled-tasks/shared-stream.ts:1-11`（只有 leader 开 `EventSource`，事件经 BroadcastChannel 转发；leader 退出锁自动移交；两个 API 都缺时退回每标签一条）。
- **复杂度**：低-中（机制依赖浏览器 API，**思想**不依赖）。
- **对 Atlas 的意义**：Atlas 的报告塔/审核塔若有多个前端连接同一后端，同样需要「单一上游 + 本地广播」；Python 侧等价物 = 一条后台线程订阅 + fan-out 到各 SSE 客户端。

### M5 Agent 团队编排：接缝式设计（宿主不提供第二套 spawn）

- **解决什么**：多 agent 协作要在**不重写会话创建链**的前提下落地；团队扩展必须与宿主解耦，才能同时跑在 pi-agent-desktop（in-process）与 oh-my-pi（独立进程）两端。
- **关键位置**：`docs/agent-teams-design.md`（1 079 行：§1 四个借鉴源 PI-Desktop ADR0237 / cc-haha swarm / DeepSeek dsh-superagent / pi-team，§2 模块划分与 InlineExtension 注册，§3 spawn 六步链，§4 工具面与状态机，§5 落盘目录，§6 邮箱与扇出，§7 泳道 JSON，§8 上限，§10 Phase1-5，§11 对比结论与「共同天花板」）；
  - `lib/pi-team-host.ts:79 createSubagentTeamHost()` —— 把既有 `SubagentController` 适配成 `TeamHost`（`start/revive/get/steer/stop/wake/onFinish`）；**只依赖两处结构类型**：`globalThis.__piSessions` 与 controller 方法，不 import `rpc-manager`（避免环）；`:175 createUnavailableTeamHost()` 是降级桥（未启用内置子 agent 时 spawn 明确失败，但团队数据/邮箱仍可用）
  - `lib/pi-extension-builtins.ts:41 teamBuiltin()` —— 以 `builtin:` 名注册（默认可加载、`-builtin:` 可关、`replaceable: true` 允许用户扩展顶替），`session_start` 时绑定「哪个会话在跑团队工具」的 `ExtensionContext`；**只接线到普通会话**（chat-only / 子 agent / workspace chat 不加载）
  - 泳道投影：`lib/agent-teams-swimlane.ts`（纯投影，**不 import `node:fs`**，客户端与服务端可共用）、`lib/agent-teams-swimlane-store.ts`（服务端专用磁盘读）、`app/api/teams/**`、`components/AgentTeams/AgentTeamsView.tsx`、`public/lane-diagram.html`
- **复杂度**：高（跨 host / package / UI 三层）。
- **对 Atlas 的意义**：**接缝（seam）本身是最大可移植物**。Atlas 若引入多 worker 编排，应照此设计：`TeamHost` 只声明「派生/复活/取状态/转向/停止/唤醒/完成回调」七个动词，具体由「谁真正会起会话」提供；再加一个降级实现，让编排层的账本/工单/邮箱在「跑不动 worker」时仍然可用（Atlas 场景 = 真机不可用时仍能出编排计划与门禁报表）。

### M6 并发上限与可归因的拒绝消息

- **解决什么**：一次误操作把进程拖死；调用方分不清「我自己派多了」还是「被别人占满」。
- **关键位置**：`node_modules/pi-team/dist/constants.js`（源仓 `D:\GitHub\pi-team\src\constants.ts`）：`:109 DEFAULT_MAX_MEMBERS_PER_TEAM=12`、`:111 DEFAULT_MAX_GLOBAL_WORKERS=32`、`:125 TEAM_LIMIT_PREFIX='LIMIT_EXCEEDED'`、`:80 DEFAULT_MAX_UNREAD=8`、`:73 MAILBOX_CAP=200`，全部支持 env 覆盖（`PI_TEAM_MAX_MEMBERS` / `PI_TEAM_MAX_GLOBAL_WORKERS` / `PI_TEAM_MAX_UNREAD=0` 关闭 / `PI_TEAM_IDLE_TTL_MS`）；`teamTools.js:57 checkSpawnLimits()` 生成**指名是哪个上限、并给出处置建议**的拒绝文案（「拆成多个团队」vs「等其他团队收工或 team_delete」）；`teamTools.js:273-277`：**超限时名册仍然落盘，只是不派发**；全局计数**排除调用方自己**（`team_create` 先写名册再派发，否则新团队首次派发即自我判超限）。
- **复杂度**：低。
- **对 Atlas 的意义**：Atlas 的用例批量生成/并发执行门禁（记忆里的「门禁 770」类似物）可直接搬这套语义：**双层上限 + 拒绝消息说明命中的是哪层 + 部分成功要留账**。

### M7 人类优先槽 + 静默合并窗口

- **解决什么**：一串 agent 消息按防抖窗口把「人的插话」推到很后面 = 人被打断成本高、agent 唤醒次数也高。
- **关键位置**：`constants.js:136 isHumanPriorityEnabled()`（默认开，`PI_TEAM_HUMAN_PRIORITY=0` 关）、`:141 HUMAN_SENDER_NAME='human'`（与成员名同命名空间，用保留名防撞）；`teamBus.js:170-171` 派发处、`:306 #isHumanPriority()`（progress 类播报消息不算插话）、`:309 #scheduleWake()`（quiet window + max wait 双约束：`delay = min(quiet, maxWait-elapsed)`）、`:326 #flush()`（读未读 → 合并成一条 → `host.wake()` → 成功后才 `claimMailboxMessages` 标已读）。
- **复杂度**：低-中。
- **对 Atlas 的意义**：Atlas 的审核塔/自愈回路里，「人工介入指令」应当绕过批量合并窗口直接生效；同时把「注入成功才标已读」搬过去，避免自愈消息在 UI 掉线时静默丢失。

### M8 工单三段式：Mandate（不可变授权）/ Ticket（工作单元）/ Assignment（执行绑定）

- **解决什么**：把「允许做什么」「做什么」「谁在做」三件事分开记账；历史授权只能撤销不能改写；订阅同一工单的后续讨论能免点名扇出；交付需显式回执。
- **关键位置**：`node_modules/pi-team/dist/teamTickets.js`（415 行）：`:35 mandateSchema`、`:45 assignmentSchema`、`:57 ticketSchema`、`:118 withTicketLock()`、`:167 createTicket`、`:199 addMandate`（`revokes` 指向被撤销的旧 mandate）、`:231 updateMandate()` **故意留空**（不可变契约的实现方式就是没有更新函数）、`:234 addAssignment`、`:258 setAssignmentStatus`、`:285 endTicket`、`:298 setSubscribers`、`:315 postToThread`（返回 fanout 列表）、`:337 receiptSchema`、`:348 recordAck`、`:404 listUnacked`。工具面 `teamTools.js:791` `team_ticket`，11 个 operation（create/list/show/mandate/assign/progress/end/subscribe/post/ack/unacked）；落盘 `~/.pi/agent/teams/<team>/tickets/<id>.json`。
  - ⚠️ `docs/agent-teams-design.md` §10 Phase 4 状态表写「Ticket/Mandate/Assignment ⬜ 未实现」「Subscription 扇出 ⬜」「Read receipt ⚠️ 部分」——**该表已过期**，代码（`teamTickets.js` + `team_ticket` 工具）已全部落地。引用该文档时以磁盘代码为准。
- **复杂度**：中（文件 + 锁 + 状态机，无数据库）。
- **对 Atlas 的意义**：**与 Atlas 的「用例/审核」记账天然同构**。Atlas 完全可用同一模型描述：Mandate = 本次允许改哪些系统/环境（如 `domain=iservice`、SIT only、敏感词禁令）、Ticket = 一批待生成/待迁移用例、Assignment = 某次引擎执行绑定；「不可变 + revokes」对应 Atlas 的口径变更留痕（如签到中心那次「精简拍板被代码推翻」正是缺不可变 mandate 导致的口径漂移）。

### M9 团队计划审批：draft → review_pending → approve(幂等) → launch

- **解决什么**：agent 不得自授权拉起一堆 worker；人审阅名单+任务图后再由**服务端**启动；重复批准不能二次派发。
- **关键位置**：`teamTools.js:310-420`（`team_plan` 的 get/submit/update/**approve**/**launch**；提交前校验：每个任务恰好一个 owner、每个成员至少一个任务、依赖无环）；`teamPlanStore.js`（429 行：`approveTeamPlan(requestId)` 幂等、`claimPlanLaunch()/completePlanLaunch()/failPlanLaunch()`，`launch.status !== pending` 即返回「没有待派发的计划」；`expectedRevision` 乐观并发 + `incarnationId`）；`plan.json` 状态机见 `docs/agent-teams-design.md` §5.1、§10 Phase 4。
- **复杂度**：中-高。
- **对 Atlas 的意义**：Atlas 已有 review 审核塔，缺的正是「**审批快照 + 幂等 request_id + 认领式派发（claim）**」这三件套；`expectedRevision` 乐观并发可直接搬到 Atlas 的用例库并发编辑。

### M10 邮箱投递：落盘优先 + 认领 + 未读背压

- **解决什么**：不做 message bus / A2A / DAG 引擎，也要跨会话、跨重启可靠投递；防止对失联 worker 无限灌指令。
- **关键位置**：`pi-team` 的 `teammateMailbox`（`inboxes/<name>.json` 未读 + `.history.jsonl` 已读 append-only + `proper-lockfile` + Windows `EPERM/EACCES/EBUSY` rename 重试）；`teamBus.js:130-152`（**先落盘再判定**，写不进时区分「被锁住」与「未读已满被背压拒投」并给出不同文案）；`:154-181` 投递结局 `injected / buffered / woken / revived / failed`（`parked`/`aborted` 成员先 `#revive()`，复活失败则消息留在盘上等 drain）；`docs/agent-teams-design.md` §6.1-6.3（含 cc-haha 结构化协议消息表：permission/shutdown/plan_approval/task_assignment/idle_notification，`isStructuredProtocolMessage` 路由到 handler 而**不进 LLM 上下文**）。
- **复杂度**：中。
- **对 Atlas 的意义**：**结局枚举 + 先落库后执行**这两条是通用纪律；「结构化协议消息不作为原始上下文消费」对应 Atlas 的 DSL 控制帧不能混进业务日志/经验笔记。

### M11 Ownership 边界：fork 与上游的可见分叉账本

- **解决什么**：本仓是上游 fork。文本冲突是**安全**的（CI 会红）；危险的是**干净但语义错误**的合并（跨文件搬移 git 不跟踪 → 上游把已搬走的代码合并回原文件，出现两份实现，`tsc`/`lint`/`test` 全绿并自动发布）。
- **关键位置**：`docs/ownership-boundaries.md`（243 行）：三层归属表（SDK 靠 npm 精确版本、Web UI 靠 `git merge` tag、桌面壳 fork 自有）；**cosmetic vs structural** 区分与阈值（structural ≥250 high、≥30 medium，实测全仓 969 cosmetic / 1379 structural，3 个文件占 80%）；「改共享文件的三条规则」（只改样式 → `native-theme.css`；通用改进 → 提上游 PR；仅桌面 → `components/desktop/` + 单 import 挂载点）；「看起来像错误的刻意决定」（`package.json` 保持上游名以免每次合并冲突；`next.config.ts` 不派生 list；本地构建不注册 updater 以防接受更新载荷）；nightly sync 的**合并前先分类**（合并后已无法用普通 diff 还原入站变更集）。
  - 机读单一事实源：`scripts/fork-ownership.json`（risk/disposition/decisionLog/forbiddenMarkers）+ `scripts/fork-ownership.mjs` + `scripts/measure-fork-drift.mjs`（`npm run drift`）
  - 哨兵：`components/fork-extractions.test.mjs`（resurrection / erosion / forbiddenMarkers 三向，且**每条断言都用变异测试验证过会失败**）；`scripts/upstream-css-baseline.*`（`globals.css`/`settings.css` 与上游字节一致）
  - 治理流：`.github/workflows/component-updates.yml`（无重叠 → 直推 main 并发版；blocked 风险重叠 → 推 `sync/pi-web-<tag>` 开 PR，人合并才触发发版）
  - 相关：`docs/fork-sync.zh-CN.md`、`docs/merge-history.md`（每次合并采纳/拒绝的台账）、`docs/adr/0001-0007`
- **复杂度**：概念低、**纪律高**。
- **对 Atlas 的意义**：**与 Atlas 处境高度同构**（Atlas 正在吸收 LiteFlow/上游手册/legacy 用例桥接，记忆里有「legacy_case 桥接」「不 push 的本地 commit」等约束）。可直接搬的是：①`fork-ownership.json` 式「边界账本 + 风险等级 + disposition + decisionLog」；②`npm run drift` 式**量化**分叉（区分表面 diff 与结构 diff）；③sentinel 测试防「搬走的代码被合并回来变成两份」；④**合并前先分类**这一时序纪律。

### M12 Worktree 切换与项目分组

- **解决什么**：一个仓库多分支并行开发，会话仍按仓库聚在一起；Windows 上 git 输出 POSIX 路径导致比较恒不相等。
- **关键位置**：`lib/worktree.ts`：`:101 resolveProject`、`:190 listWorktrees`、`:231 findCurrentWorktreePath`、`:336 switchBranch`（本地分支直切，远端唯一同名时 `checkout -b --track`）、`:375 fetchRemote`（`git fetch --prune` + `GIT_TERMINAL_PROMPT=0` 快速失败）、`:384 addWorktree`（`<repoRoot>-worktrees/<sanitized-branch>`，已存在分支则复用）、`:438 removeWorktree`、`:459 worktreeRemovalRequiresForce`；`app/api/worktrees/route.ts`(+`fetch`)；`docs/worktrees.md`；`docs/agents/files-and-access.md` L4-6（`toNativePath()`/`samePath()`，路径段仍保留正斜杠；已在他处 checkout 的分支 → UI 跳到那个 worktree）；`docs/agents/fork-files-security.md` L11-21（**事件驱动不够**：外部切分支会失真 → 侧栏每 10 s 轮询 + 窗口聚焦刷新；脏 worktree 返回 `409 {dirty:true}` 再由 UI 升级 force；分支名禁止以 `-` 开头，**作为 argv 传递，绝不进 shell**）；UI `hooks/useProjectWorktreeActions.ts`、`components/ProjectContextMenu.tsx`。
- **复杂度**：中。
- **对 Atlas 的意义**：Atlas 的「同一用例库多分支/多环境并行」可搬：项目根解析 + worktree 分组 + 脏检查 409→force 两段式 + 分支名 argv 化（Python `subprocess` 列表参数同理）+ 「外部变更必须靠轮询兜底」的自知。

### M13 文件浏览的授权根清单（安全边界）

- **解决什么**：`/api/files` 一旦成为「通用文件浏览器」，等于把整台机器交给任一能访问端口的人；但会话/项目又要能浏览自己的目录与链接。
- **关键位置**：`lib/allowed-roots.ts:26 allowFileRoot()`（roots 存 `globalThis`，热重载存活）；`lib/path-security.ts isPathWithinRoots()` —— **唯一实现**，两侧都重新 resolve + case-fold（文档明令「keep it the only one」）；`lib/file-access.ts isFilePathAllowed()`（roots = 会话 cwd + 解析出的 project root + `~/pi-cwd-*` + 显式授权）；`isExistingPathWithinRoots()` + `hasParentDirectorySegment()`（含 `..` 一律拒绝；URL 解析会吞掉字面 `..`，路由再单独拒绝段内编码斜杠）；`lib/credential-paths.ts`（`~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.kube`、`~/.netrc`、`~/.git-credentials`、`~/.npmrc`、`~/.pi/agent/auth.json` 在**已授权根内部也**继续拒绝，且覆盖大小写变体；`.env` 等项目文件**故意不在**名单上）；`lib/linked-directory.ts checkLinkedDirectoryApproval()`（目录按**解析后**目标授权，绝不按字面路径；指向包含 root/home 的目标需两次点击确认，按钮先「上膛」再打印确切目标）；`lib/session-file-references-core.ts`（转录里出现过的路径可被**读**不可被**列**，且 MCP/codemode/第三方扩展的 toolResult 不具授权力——它们转达的是远端可控文本）；`lib/file-tree-visibility.ts`（列目录时只影响可见性不影响访问：一次 `git check-ignore --no-index --stdin` + 一次 `git ls-files`，5 s 超时，`-c core.fsmonitor=false`）；UNC 路径往返（`encodeFilePathForApi()` 把 `//host` 折进首段）。
- **复杂度**：中-高（价值主要在**规则清单**而非代码）。
- **对 Atlas 的意义**：Atlas 有 Web 侧报告/文件浏览与产物目录（记忆：Allure 必须走 `http.server` + 给 URL）。可移植：单一授权函数、**凭据名单在授权根内仍生效**、授权只走显式 API（`/api/cwd/validate`、worktrees）而非路径拼接、symlink 按解析目标授权、两段式危险确认、"转录里出现" 不等于 "可被远程工具结果提权"。

### M14 无人值守安全默认（预设 + 服务端确认 + 只读策略）

- **关键位置**：`lib/tool-presets.ts:25-28`（`PRESET_NONE/READ_ONLY=["read","grep","find","ls"]/DEFAULT/FULL` + `getPresetFromTools()` 反向识别）；`docs/scheduled-tasks-spec.md` D3 + §7（**默认只读**，理由：pi 无权限确认层，无人值守不会卡住也就没人把关；只读 MCP 策略随预设自动生效）；`lib/scheduled-tasks/task-input.ts`（写类预设要求 `acknowledgeUnattendedWrites`，服务端强制，防「只在前端弹窗」）；`lib/mcp-read-only-policy.ts`（只信任 server 自己声明 read-only 的 MCP 工具）；`lib/project-trust.ts` + `components/ProjectTrustDialog.tsx`（不受信任目录不加载其 `.pi/extensions`）；`lib/chat-workspace-policy.ts` + `lib/chat-only.ts`（聊天工作区服务端策略：禁 shell/文件工具、上下文文件关闭、每会话解锁 `pi-web:chat-unlock` 且网络暴露时不提供）；`lib/exact-system-prompt.ts`（系统提示只走这一条通道）。
- **复杂度**：低-中。
- **对 Atlas 的意义**：与 Atlas 的「敏感词零残留」「只修功能不修隐蔽性」同类：**默认最小权限 + 危险能力必须服务端确认 + 策略由服务端而非前端持有**。Atlas 的用例生成/自愈默认应落在只读档，写/执行需显式确认位并落审计。

### M15 端到端加密远程 Relay（无端口暴露的远程驾驶）

- **关键位置**：`docs/remote-relay.md`（193 行：威胁模型表逐方「可信什么/必须不能做什么」；架构 = 手机壳(origin E) + 隧道 SW + 哑中继(wss) + 桌面连接器；**代码与中继分离**，因为能改写页面代码的一方可以让任何加密失效；Noise `IK`/`IKpsk2` 全用 WebCrypto，两端同一模块，与 cacophony 向量逐字节对齐；配对四步含「首帧才是持有 psk 的证明」；连接器剥离客户端伪造头 + 加设备 id 与进程级密钥；`isLocalRequest()` 对远程请求恒 false；R1-R4 阶段表；**变异测试 34+25 个安全检查全部被杀死**，并记录了一个等价突变体为何排除）；实现 `lib/relay/{noise,frames,connector,store,mark,runtime,phone-client}.ts`、`relay/server.mjs` + `Dockerfile` + `docker-compose.yml`、`relay-shell/{shell.ts,sw.js}`、`app/api/relay/**`、`components/RelaySection.tsx`、`e2e/relay.mjs`；`lib/relay/mark.ts:1-30`（密钥放 `globalThis` 的 `Symbol.for`，**明确不放 `process.env`**，因为 agent 的 shell/终端/MCP 子进程都会继承，一条 `env` 就把密钥写进转录）。
- **复杂度**：极高。
- **对 Atlas 的意义**：不建议整体移植。值得抄的是**三条方法学**：①任何远程入口先写威胁模型表再动手；②安全机制用变异测试自证（逐条 revert 必须有测试变红）——Atlas 的敏感词/越权门禁可用同样办法；③密钥不进子进程继承面（Atlas 的 JMeter/巡检脚本传凭据同理，别塞进会进日志或子进程 env 的位置）。

### M16 请求真实性与登录限流

- **关键位置**：`lib/remote-access.ts` + `proxy.ts`（Host 由客户端可控 ⇒ **不证明任何东西**；Next 在缺失时用 socket 地址填 `x-forwarded-for` 并保留代理值 ⇒ 非回环首跳即被中继（`isRelayedFromAnotherMachine`）；tailnet 身份头白名单；拒绝 `Tailscale-Funnel-Request` 除非设了 Web 密码）；`lib/auth-throttle.ts`（**故意全局而非按 IP**：Next 16 route handler 拿不到 socket、`x-forwarded-for` 可伪造、服务只绑 127.0.0.1 单人用；失败指数翻倍 1 s→60 s，成功或空闲 5 min 复位，**复位窗口必须大于最大延迟**否则等完一次封禁就能重新起爆；`GET /api/web-auth` 不是未限流的口令预言机——Basic 在豁免之前先查；Basic 成功不重置计数）。
- **复杂度**：中。
- **对 Atlas 的意义**：Atlas 平台若有 Web 门禁/登录，这套「不可信头推理链 + 全局限流 + 不制造口令 oracle」是现成清单。

### M17 自动更新与三组件版本一致性

- **关键位置**：`docs/desktop-updates.md`；`lib/app-updates.ts:9-10`（**每周**最多检查一次最新 stable GitHub Release，重试间隔 6 h）、`:31 APP_UPDATE_PROJECTS`、`parseVersion/comparePrerelease`；`lib/desktop-updater.ts:26 installLatestDesktopRelease()`（`checking/downloading/installing` 三态进度，`@tauri-apps/plugin-updater` 校验签名后整包替换；**绝不替换已签名 App 内的 JS 或依赖**）；`app/api/updates/route.ts`；`components/AppUpdatesSection.tsx` + `UpdateReminder.tsx`（`SettingsPanel` 的 `onBusyChange` 守卫防止安装中被关掉）；`src-tauri/resources/component-versions.json` 记录 `pi-agent-desktop`/`pi`/`pi-web` 三者确切版本；`scripts/{release-components,resolve-component-releases,verify-release-components,write-component-versions,configure-updater-public-key,bump-pi-agent-desktop-version,update-homebrew-cask}.mjs`（`npm run release:manifest` / `release:verify`）；发版工作流先校验三方版本恰为最新 stable，Release 保持 draft 直到三平台产物与 manifest 齐备。
- **复杂度**：中。
- **对 Atlas 的意义**：**「组件版本 manifest + 发布前核验」最值得搬**。Atlas 依赖 Allure 2.39.0（纯 stdlib 直出）、引擎版本、上游手册快照版本；照此产出一份 `component-versions.json` + `verify` 脚本，可把「本机 Allure/引擎版本漂移」变成一次可检查的门禁，而不是运行期玄学。

### M18 任务面板：等待态与「需要人工」可见性

- **关键位置**：`lib/rpc-manager.ts:2817 getRpcTaskSnapshots()`；`app/api/agent/tasks/route.ts`；`components/tasks/{TasksView,TasksSidebarRow,tasks-helpers}.ts`；`docs/agents/fork-files-security.md` L40（快照 = 当前步骤 tool/compacting/retrying/thinking + 打开的阻塞对话框 + 最近结局；**最近结局跨 10 分钟空闲回收仍存活**（但不跨重启）；`createTasksPoller` 仅在 running 集变化且页面可见时每 3 s 刷新，隐藏页**一条都不发**）；定时任务侧 `run_attention` 通知（对话框挂到时间上限 → 改为通知「需要你的输入」并保持会话打开，见 `docs/scheduled-tasks-spec.md` §15）；`lib/browser-notifications.ts isBlockingExtensionUiRequest()`。
- **复杂度**：低-中。
- **对 Atlas 的意义**：Atlas 的 dispatch/自愈缺「现在卡在什么上、是否等人」的统一视图；「隐藏页不轮询」「结局快照生命周期与执行器回收解耦」两条策略可直接照搬。

### M19 脚本式 E2E：隔离 HOME + 假模型 + 自起服务 + 合成转录

- **关键位置**：`e2e/run.mjs:22-50`（`E2E_SERVER_MODE=dev|start` 双模式，断言「同一 checkout 不得有活跃 dev server」因 `.next/dev/lock` 共享；`mkdtempSync` 造 `PI_CODING_AGENT_DIR`，**启动前直接写合成 `.jsonl` 会话文件**，跑完即删；`appendFileSync` 模拟外部写入）；`e2e/README.md`（fixture 全部预置，**无需模型凭据与既有会话**；CI 一个 job 跑 lint/type/unit，另一 job 干净 checkout 构建后用 `next start` 跑同一批脚本）；`scripts/e2e-server-env.mjs`；`docs/scheduled-tasks-spec.md` §11（端到端用「隔离 agent 目录 + 本地 OpenAI 兼容假端点 + 真实 `next dev` + 真实 HTTP」验证，并把 `kill -9` 后的租约接管与 `running` 收尾列成验证项）。
- **复杂度**：中。
- **对 Atlas 的意义**：**Atlas 现在最缺的验证形态就是它**。Atlas 有 pytest 用例，但「不烧真实账号、不碰真机、把执行语料当 fixture 预置、同一脚本跑 dev 与 prod 两种服务模式、以及 SIGKILL 后状态收敛」这套做法可以把「真机取证 2-3 次」的成本大幅前移到离线。

### M20 会话自动化开关（压缩/重试/steering/followUp）

- **关键位置**：`hooks/useSessionAutomation.ts:1-30`（pi 0.86 的 `autoCompactionEnabled/autoRetryEnabled/steeringMode/followUpMode` 从 `get_state` 镜像，写回走 `sendAgentCommand`；**刻意从上游所有权的 `useAgentSession.ts` 里拆出来**以保持合并干净）；`docs/agents/fork-sessions.md`（retry-backoff 取消、`!command` 输出、stats）。
- **复杂度**：低。
- **对 Atlas 的意义**：把「重试/自愈/批量投递节奏」做成**运行时可查可改的会话属性**并单独成模块，而不是散在引擎里 —— 对 Atlas 自愈策略开关是同样形状。

### M21 其他值得一提的小机制

| 机制 | 位置 | 一句话价值 |
|---|---|---|
| 原子私有写 | `lib/atomic-file.ts writePrivateFileAtomicSync()` | 配置/账本写入统一「临时文件 + rename + 0600」 |
| 读不回写的损坏文件 | `docs/agents/scheduled-tasks.md` Data 段 | 解析失败**抛错而非当空**，防止坏文件被「无任务」覆盖 |
| 未知字段原样带回 | 同上 + `lib/subagent-settings.ts` 做法 | 与 CLI/其他进程共享目录时的共存纪律 |
| 同步 store 即原子 | `docs/agents/scheduled-tasks.md`（每个 store 函数 read-modify-write 不 `await`） | 进程内原语的极简选择 |
| 共享 SSE 单流选主 | M4 | 同 |
| 全局状态放 `globalThis` + 错误按 `name` 判而非 `instanceof` | `docs/scheduled-tasks-spec.md` D7、§11（实测：Turbopack 下 instrumentation 与 route 是两份模块实例，本该 409 的重叠变成 500） | 多打包实例/多模块图下的通用坑与解法 |
| 通知 `tag` 替换而非叠加；按会话共用 tag | `docs/scheduled-tasks-spec.md` §13、`docs/agents/fork-files-security.md` L39 | 同类状态最新值覆盖旧值 |
| 三语文案键一致性测试 | spec §12（新增 99 键，测试保证 en/zh-CN/zh-TW 一致） | i18n 回归门禁 |
| 品牌一致性 / 依赖安装核验 | `npm run check:branding`、`scripts/check-installed-deps.mjs`（`pretest`） | 发布前廉价门禁 |
| 沙箱与预览策略 | `docs/agents/fork-files-security.md` L5-9（同一 iframe 绝不同时给 `allow-scripts` 与 `allow-same-origin`；`HTML_PREVIEW_CSP`） | 产物预览的安全默认 |
| 上游合并哨兵（禁止标记） | `AGENTS.md` "Upstream merge sentinels" + `forbiddenMarkers`（例：项目选择器筛选框在一次改版后静默消失，导致 7 行上限外的项目不可达） | 「删掉的东西不许被合并复活」也需断言 |

---

## 3. 覆盖矩阵（任务点名项 → 章节）

| 任务要求 | 本文件位置 |
|---|---|
| 会话浏览 / 恢复 / 分支(fork) | M1（+ M2 生命周期兜底） |
| 定时任务 | M3（+ M4 事件流、M18 等待态、M10 落盘优先） |
| agent 团队编排：上限 / 人类优先槽 / 工单三段式 | M5（接缝）、M6（上限）、M7（人类优先槽）、M8（工单三段式）、M9（审批后自动 launch）、M10（邮箱/扇出/receipt） |
| ownership 边界 | M11 |
| worktree 切换 | M12 |
| 远程 relay | M15（+ M16 请求真实性） |
| 自动更新 / 版本检查 | M17 |
| 权限允许清单（文件浏览只允许授权目录） | M13（+ M14 无人值守默认） |

## 4. 给 gap6 评估阶段的提示（不构成结论）

1. **最像「即插即用」的四块**：M3 定时任务（纯函数决策核 + 租约 + 背压）、M8/M9 工单三段式与审批后认领派发、M6 双层上限与可归因拒绝消息、M11 ownership 边界账本 + drift + 哨兵。它们都不依赖 TS/React/Tauri 形态，只依赖「文件 + 锁 + 纯函数 + 状态机」。
2. **必须先补证据再评估的两块**：M13 文件授权根（Atlas 的 Web 暴露面与凭据现状未盘清，`atlas-state.md` 由另一 agent 负责）、M19 脚本式 E2E（要看 Atlas 现有 pytest 与真机配额的耦合度）。
3. **明确不建议整体吸收**：M15 relay（成本极高，只取方法学三条）、M1/M2 中与 pi SDK 绑定的会话对象模型（Atlas 无 in-process agent）。
4. **两处文档与代码不一致，引用时以代码为准**：`docs/agent-teams-design.md` §10 Phase 4 的「未实现」表已被 `pi-team/dist/teamTickets.js` 推翻；同文档 §8 正文的 8/32 已被 §8.4 的 12/32 修订。
5. **AGENTS.md 的 Hard rules 段**（`AGENTS.md` L? "Hard rules"，共 20+ 条，每条标注了它 undo 的那次真实回归）本身就是「机制背后为什么长成这样」的最短路径，评估取舍时应逐条读。

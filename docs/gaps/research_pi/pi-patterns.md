# pi-agent-desktop 机制调研：可移植设计模式提炼

> 状态：调研完成（只读精读，未改任何代码）
> 负责：pi_patterns ｜ 关联：`agent.py` / `failures.py` / `healing.py` / `review.py` / `experience.py` / `dsl.py` / `engine.py` / `cli.py` / JMeter 巡检
> 输入：`D:\GitHub\pi-agent-desktop\docs\` 下 9 份设计文档 + 实现锚点抽查（`pi-team/src/constants.ts`、`scripts/fork-ownership.json`）
> 用途：为 gap6（Atlas 吸收 pi-agent-desktop 机制评估）提供逐机制的「问题域→pi 解法→Atlas 类比→可移植性判定」明细表

---

## 0. TL;DR：结论先行

1. **与 Atlas 技术栈无关、纯流程/纪律层面可直接移植的模式共 15 个**（见 §9 汇总表），最高价值集中在 `scheduled-tasks-spec.md`（调度语义：补跑/租约/连败暂停/无人值守护栏）与 `ownership-boundaries.md`+`fork-sync.zh-CN.md`（边界治理与合并验收纪律）。
2. **`agent-teams-design.md` 的多 agent 编排本体（spawn/邮箱/泳道）对 Atlas 是"参考设计"而非"直接移植"**——Atlas 当前是单进程 CLI + 文件审核塔；但其中的**授权三段式、证据式验收、人类优先槽、上限策略**四件事可以脱离多 agent 语境单独落地。
3. **约三分之一内容（remote-relay 加密隧道、desktop-updates 签名分发、terminal 的 PTY 管理、WebView/Tauri 通知）是桌面/网络应用专属，明确不可移植**；但它们的**验证方法学**（威胁建模表、变异测试、失败集合子集对比、"未验证项必须显式列出"）可移植。
4. 一个值得注意的交叉信号：pi 的多个决策与 Atlas 既有红线**独立收敛到同一设计**（终止信号不计失败、原子应用拒绝半成品、证据验收、不伪装通过，见 §10）。这说明这些不是平台特殊性，而是通用工程纪律——Atlas 吸收时可作为"既有红线的第二实现"而非新规则。

---

## 1. Agent Teams（`agent-teams-design.md`）

**来源脉络**：该设计融合四个上游——PI-Desktop Session Orchestrator（ADR 0237）、cc-haha swarm、DeepSeek dsh-superagent（Mandate/Ticket/Assignment 领域模型）、pi-team 文件层扩展。评审时按子机制拆开看，可移植性差别很大。

### 1.1 Mandate / Ticket / Assignment 三段式授权

- **问题域**：agent 委托执行时，"允许做什么"（授权）、"做什么"（工作单元）、"谁来做/做了几次"（执行绑定）三件事混在一起，导致授权范围随执行漂移、事后无法追责。
- **pi 的解法**：
  - Mandate **不可变**，记录 Policy 版本、范围、强度、命中规则；只有人的 stop 或 Policy 变化才能撤销（新 Mandate 才能继续）。
  - Ticket 只有 Eval（判断环节）能创建；Ticket 状态 open↔ended 自定义，**不跟随 Executor**。
  - Assignment 独立状态机 pending→running→ended；一个 Ticket 可多次重派（重启后新 Assignment 带恢复提示）。
  - 边界条件：文档明确 worker wrapper/目录「建立默认值，**不是安全边界**」；落地进度诚实标注 Ticket/Mandate 实体**尚未实现**（Phase 4 ⬜），只有 `TaskAssignmentMessage` 消息类型。
- **Atlas 类比**：`agent.py` 的 `classify_risk` 是**执行时刻确定性推导**（生产写→HIGH），但"agent 决定本次目标可自动执行"这个授权本身只留痕、不成实体。三段式映射：Mandate=对某 goal/某风险档的一次性授权（带 policyVersion 与风险档上限），Ticket=flow/任务实例，Assignment=每次 run。价值点：**Policy（风险规则表）升级后，旧的自动执行授权应失效回人审**——防止"规则改严了，存量授权还在按旧口径自动跑"。
- **判定**：✅ 可移植（概念层，非代码层）。Atlas 单 agent 场景下可先只落 Mandate 一层（授权实体+版本失效），不必引入 Eval/Ticket 全套。

### 1.2 上限策略与拒绝语义

- **问题域**：并行派发无上限会失控（内存、provider 限流、成本），但上限一刀切又误伤正常并行。
- **pi 的解法**：每团队 12 / 全局 32（2026-11-05 复核从 8 调到 12，并写明**为什么原来的 8 失效了**——锚点已不存在）；拒绝消息前缀 `LIMIT_EXCEEDED` 且**区分是哪一个上限**（per-team vs global）；全局计数**排除调用方自己**（防止先落盘名册再派发时自我判超限）；超限时名册仍有效创建、只是不派发；worker 不能再 spawn worker（防递归）；未读 inbox 上限 8 条（防投递风暴）；**人类优先槽**：来自 `human` 的消息跳过静默合并窗口直接 flush，agent 间消息仍走防抖——防一串 agent 消息饿死人的插话。
- **Atlas 类比**：①无头批量执行的并发控制——上限值、拒绝消息分因、"批次登记成功但不派发"这三条可直接抄语义；②JMeter 巡检并发闸门同理；③人类优先槽对应 `engine.cdp_maybe_pause` 冻场与批量 run 中的人工中断：批量执行满载时，人工的"停这条/看这条"不该排队。`DISPATCH_QUIET_WINDOW_MS=2000` 防抖合并（pi-team constants 已核实）对应 Atlas 审核塔"同一坑多次出现合并成一条草稿"（`experience.py` 同 id 合并 seen，同款思想）。
- **判定**：✅ 上限分因拒绝、计数排除自身、满载时"登记有效/执行不派发"、人类优先槽——四条均可移植；具体数值需按 Atlas 资源模型重新定。

### 1.3 证据式交付验收（teamflow 防假交付）

- **问题域**：worker 报告"完成了"不等于完成——LLM 会假交付。
- **pi 的解法**：`team_result` 的 `evidence[]` 每项是「命令+退出码+断言数」；`ready=true` 但无证据→父会话追问或标「未验证」；result 文本必须有**显式结论行**（`结论：通过/不通过`），缺失挂起等人；QA 类 worker 走独立对抗探针。
- **Atlas 类比**：审核塔（`review.py`「列表带证据、批准即写知识」）与 `HealingProposal` 晋级目前证据是自由文本卡片。可强化为**验收证据三元组**：自愈提案晋级必须附「复现命令 + 退出码 + 断言数（或 Allure 统计）」且结论行显式——这把红线 #7「禁止为变绿而放宽断言」从"审的时候注意"升级为"没证据格式就不出现在可批准状态"。对 JMeter 巡检同理：504 类问题的"已恢复"结论必须挂当次探测命令与退出码。
- **判定**：✅ 高价值可移植，纯数据契约改造。

### 1.4 状态机与失败处理语义

- **pi 的解法**：worker 七态（queued/running/idle/completed/failed/aborted/interrupted），**重启恢复标 interrupted 并带恢复提示**；`cancel` 永不删 session（现场保留可 resume）；Report 五态 ready/blocked/**failed**/**waiting**/**dropped**；重试 2s 起指数退避至 1 分钟、3 次，只针对瞬时错误（408/429/5xx/refused/reset/timeout）；失败挂起时 CI/bot 消息记录不投递、人的消息可解锁；未 ack 消息在 Ticket 结束时回到 Eval（Read receipt：claim+report/turn_end 才算已读）。
- **Atlas 类比**：`failures.py` 已有 8 类 FailureClass + CONTROL_FLOW；pi 多出的两个终态值得对照——**waiting（需人工输入）**：`scheduled-tasks` 文档的 `run_attention` 事件（运行中弹扩展对话框→通知"需要你输入"而非等到超时）对应 Atlas CDP 冻场与审核塔积压场景，Atlas 无头批量遇到需人工步骤时应有"等待人"显态而非计失败；**dropped（丢弃不执行）**对应 dispatch 无主步骤（memory 记录过的"恒执行陷阱"）的反面选项。重试三分类（瞬时/永久/挂起）可直接做进 API 能力的重试表。"cancel 不删现场"与 Atlas「只有失败/debug/heal 才生成现场快照」互补：abort/interrupted 也应保现场。
- **判定**：✅ 状态语义可移植；实现载体（session .jsonl）是 pi 专属。

### 1.5 邮箱投递 / Subscription 扇出 / 泳道快照

- **pi 的解法**：文件层 inbox（proper-lockfile+rename、Windows EPERM/EACCES/EBUSY 重试）+identity-based claim 防双消费+已读 history append-only；Subscription 扇出=讨论线程新消息自动到达订阅 Ticket 的 worker、**不检查发送者**（免点名）；消息节流 maxChainHops=4/maxChainRoundTrips=2；泳道=服务端投影纯 JSON 快照（lanes/events/flows/taskAnchors），H5 只消费不碰内部状态。
- **Atlas 类比**：Atlas 无多 agent 邮箱需求（审核塔的 jsonl 队列已是单消费者简化版），**邮箱本体：条件可移植**——若未来巡检多机分片才有用。泳道快照的"服务端产 JSON 契约、前端只读"分层与 Atlas 纯 stdlib 直出 Allure 同构，`run-snapshot.json`（每步状态/耗时/失败类）可作对外仪表盘契约。**免点名扇出**的类比是"坑点命中不看域"——`match_pitfall(exc, domain)` 目前带域过滤，可评估跨域兜底命中（低优先）。
- **判定**：邮箱/扇出 ⚠️ 条件可移植（当前无场景）；JSON 快照契约 ✅ 可移植；H5 渲染 ❌ 桌面专属。

---

## 2. Scheduled Tasks（`scheduled-tasks-spec.md`）★ 与 Atlas 契合度最高

**问题域**：无人值守的周期任务：机器会睡眠、进程会双开、任务会连败、成本会失控、写操作没人把关。这份文档几乎是给 JMeter 巡检/定时回归写的。

### 2.1 补跑（catch-up）语义

- **pi 的解法**：tick 固定 30 秒（**不用 setTimeout 对准下次时间**，睡眠唤醒会让定时器漂移）；每次 tick 用 `previousRun(now)` 幂等判定，靠 `lastScheduledFor` 水位线防重；错过 ≤2 分钟正常触发；>2 分钟但 ≤7 天**补跑一次**并记 `skippedSlots`（丢掉的更早时间点数）；>7 天直接丢；**暂停/改调度/恢复时重置水位线**——暂停前的时间点不补跑（人改配置的瞬间不该回放历史）。once 任务触发后自动 enabled=false。
- **Atlas 类比**：巡检笔记本合盖、CI runner 重启后的错过周期处理，语义可整套照搬（cron 表达式+IANA 时区存储、预设只是 UI 糖=「一种存储格式一个解析路径」也值得 Atlas config 借鉴）。`skippedSlots` 让报告能回答"这台机错过几轮"。
- **判定**：✅ 直接可移植（纯逻辑，Python 等价实现无成本）。注意移植它附带的两个实测坑：croner `previousRuns()` 按整秒截断且不含参考点（Python cron 库同类 off-by-one 要用"+1 秒"或半开区间单测钉死：整点/半秒/整点前半秒三个 case 是 pi 的覆盖法）。

### 2.2 调度权租约（lease）

- **pi 的解法**：同一时刻只允许一个进程持有调度权——`scheduler.lock` 内容 `{pid, startedAt, heartbeatAt}`，心跳 90 秒内视为存活，持有者每 tick 刷新；非持有进程只提供读写 API 不调度；`kill -9` 后由下一个进程接管（E2E 验证过接管+遗留 running 记录收尾）。理由：开发服务器与桌面 App 同时跑会双触发。
- **Atlas 类比**：巡检脚本在本地调试与服务端定时同时启用 = 同一环境双跑单、双份配额。租约+心跳是标准解；"接管后先收尾上一任遗留的 running 记录"这条顺序值得注意——对应 Atlas recorder 的孤儿 run 处理。
- **判定**：✅ 直接可移植。

### 2.3 重叠与满载的两种语义

- **pi 的解法**：同一任务上次仍 running → 写 `skipped/overlap`，**不排队**；全局并发（2）已满 → **不判跳过、留到后续 tick**（"推迟"不是"跳过"），等待期出现更新时间点则旧点并入 skippedSlots；手动运行不受并发上限约束。
- **Atlas 类比**：巡检拥挤时把"没跑到"记成失败会污染缺陷分布；"推迟/跳过/失败"三态分离应进 `failures.py` 的口径（现 ENV_BLOCKED 更接近"环境不允许"，没有"推迟"）。手动旁路（`--force-run` 不吃闸门）符合调试直觉。
- **判定**：✅ 语义直接可移植。

### 2.4 连续失败自动暂停 + 失败计数口径

- **pi 的解法**：`consecutiveFailures` 达 5 → 自动 enabled=false 并在列表显示原因；**超时算失败，用户手动 Stop 不算，进程被杀留下的 Interrupted 也不算**；成功清零。
- **Atlas 类比**：这是 Atlas 红线 #114（CONTROL_FLOW 单一真相：终止信号不是业务失败）在调度域的同一实现——pi 独立收敛到相同判据，互证通用性。JMeter 巡检直接可移植：环境挂一整晚应"暂停并留原因"，而不是每小时刷一条失败+持续烧请求。
- **判定**：✅ 直接可移植，且应引用为 `failures.py` 既有红线的调度侧条款。

### 2.5 无人值守护栏组合

- **pi 的解法**：默认工具预设 read-only（无人值守+无确认层→不能带写能力）；选 default/full 必须带 `acknowledgeUnattendedWrites: true` 且**服务端强制校验**（防只在前端弹窗）；cron 相邻触发 ≥5 分钟（控 LLM 成本）；`maxDurationMin=30` 到点 abort；默认只读的另一理由是**提示注入**（任务读取网页/issue 外部内容）；创建任务的 cwd 走与新建会话相同的校验（"任务=可执行配置"，等同安全面）。
- **Atlas 类比**：与 `agent.py` 风险阈值同构（HIGH 无自动路径），补三个增量：①"确认字段服务端校验"→Atlas CLI 生产写确认应在代码路径断言（如 `config.validate_write`），不依赖交互提示存在——无头模式没有 TTY 可弹；②最小触发间隔→巡检 config 防手滑 `* * * * *`；③任务读取外部内容（Allure 结果、页面文本、issue）进 LLM 上下文的注入风险→对应 `allure_analysis.py`/经验库文本入库路径，外部文本只作为证据展示、不进入指令面。
- **判定**：✅ 组合原则可移植；read-only 预设的具体工具名单是 pi 概念，Atlas 对应物是 `read_only` Task metadata（已存在，缺"无人值守默认档"这一层开关）。

### 2.6 runs 索引与产物归类

- **pi 的解法**：会话↔任务关联**靠 runs 索引不靠扫 jsonl**（全量扫 entry 会拖慢增量扫描器）；索引丢失可由产物内 custom entry 重建；定时运行产生的会话默认**不进项目树**（防每小时一条淹没分组），但全局搜索可达。合并复查时发现的教训：删除任务+勾选删历史→几十个隐藏会话突然涌回、不勾→永久孤儿——最终"删任务一律连历史删、会话回归普通列表"。
- **Atlas 类比**：①索引与真相分离：Atlas reports ↔ flow ↔ pitfall 的关联可走同样模式（运行产物头部元数据=真相，聚合索引可丢可重建）；②批量产物隔离：定时/巡检 run 的报告目录应与人工 run 分池，否则 pass 率统计被周期噪声稀释；③"隐藏靠索引、删除留孤儿"→Atlas 任何"从列表过滤"的功能要同步回答"上游记录删了它去哪"。
- **判定**：✅ 模式可移植。

### 2.7 事件与通知

- **pi 的解法**：SSE 推 5 类事件，客户端不轮询；Web Locks 选领头标签持有唯一 EventSource（防 HTTP/1.1 6 连接上限）；通知文案由**纯函数** `describeRunNotification()` 生成并单测；重复通知同 tag 替换不叠加；用户手动 Stop 用常量 `STOPPED_BY_USER` 标记供客户端静默；"运行中弹需人工对话框"发 `run_attention`。
- **判定**：⚠️ 事件流/Web Locks/系统通知是浏览器桌面专属；**"通知文案纯函数化+单测"、"手动终止常量标记"（=CONTROL_FLOW 同族）、run_attention（等人态显式通知）** 三条模式可移植（Atlas CLI/报告侧的 run 结束摘要与"批量中被 Ctrl-C 的 run 不再报失败"）。

---

## 3. Ownership Boundaries + Fork Sync（`ownership-boundaries.md`、`fork-sync.zh-CN.md`）

**问题域**：长期吸收外部/遗留资产并保持自有改造不被侵蚀。Atlas 30 条用例迁入、legacy 桥、knowledge-migration 完全同域。

### 3.1 表层漂移 vs 结构漂移

- **pi 的解法**：**行数不是风险代理指标**——改样式产生的大 diff 是"表层漂移"，上游再改同一行会文本冲突、CI 大声失败（安全的）；真正危险的是**结构漂移**（重写 JSX、代码跨文件搬移）：**能干净合并而语义已双份**。清单 `fork-ownership.json` 机器可读、单一真相，CI 在 **merge 之前**对 incoming changeset 按清单分类（merge 后原始 changeset 再也拿不回来）；无重叠→自动推 main，命中 blocked 风险→开 review PR。实测上游一次 patch 就碰到 7 个 fork 也改过的文件，"约三分之一上游文件会撞上自有文件"是稳态不是暂时。
- **Atlas 类比**：迁移验收的分级判据——用例改名/格式调整=安全的大 diff；**公共库函数被复制回用例文件、桥接层行为被重新实现**=危险的干净合并。`case-migration-mapping.csv`+`legacy.py` 就是 Atlas 版 ownership manifest，缺的是"按结构风险分级 gate 自动晋级 vs 进塔人审"这层。
- **判定**：✅ 完全可移植（心智模型+清单机制）。

### 3.2 哨兵测试 + 变异验证

- **pi 的解法**：`fork-extractions.test.mjs` 双向断言——**resurrection**（搬走的代码在原文件复活：import 搬走的符号合法，redefine 是信号）与 **erosion**（自有改动被静默回退）；每条断言都用**变异注入验证过"失败模式真实存在"**（改了断言让它失效、确认测试会红）；断言失败时的纪律是"重放自有改动，不是删断言"；`forbiddenMarkers` 断言某段代码**保持不存在**（上游复活已删的 branding header）。
- **Atlas 类比**：迁入资产的守护——每条迁移桥加"复活/侵蚀"哨兵（如 `lib/pages.py` 的公共函数不得在迁入用例里重新定义），并做一次变异检查证明哨兵本身可失败。这是给 Atlas 772 条门禁测试补"测试的测试"纪律。"不通过删断言来变绿"=红线 #7 的测试侧表述。
- **判定**：✅ 直接可移植。

### 3.3 合并验收判据：失败集合子集对比（fork-sync 最有价值的一条）

- **pi 的解法**：Windows 全量测试不过时，不声称全绿——把整合代码与**干净上游副本**在相同 Node/依赖/权限下跑同一组测试：上游 48 失败，整合后 26 失败，且**整合后每个失败名 ∈ 上游失败集合**→判定"整合未引入新破损"；失败类型归因（符号链接权限/EBUSY/路径兼容）；同时拒绝用跳测试、降风险等级制造通过。其它纪律：逐文件处理冲突禁批量 ours/theirs；**"不要只处理 Git 标出的冲突，新文件同样需要检查"**；备份分支先存未提交改动、禁 `reset --hard`；双引号 glob 防 cmd 把单引号当文件名而**静默执行 0 项测试**。
- **Atlas 类比**：这就是迁移验收的判定规则模板：吸收 legacy 用例后回归失败数 ≤ 迁移前基线，且每个新失败必须能在基线找到对应，否则=迁移引入破损→阻断晋级。"静默 0 项测试"对 pytest 同样成立（错误 `-k`/路径过滤收集 0 条退出码可为 0 的组合），值得进巡检 SOP。"诚实记录未验证平台"与 EnvBlocked/不伪装通过同红线。
- **判定**：✅ 高价值可移植（纯流程）。

### 3.4 disposition + decisionLog

- **pi 的解法**：每条漂移标 `reduce`（可消除，须给出消除路径）或 `accepted`（复核后保留，明码标价"这是产品本身，清了等于删功能"）；`decisionLog` 追加带日期条目防后人重新争论；"看似错误实为刻意"清单（package.json 不改名避免每次同步冲突；配置列表不自动派生，fork 自有副本才派生）。
- **Atlas 类比**：Atlas 红线带编号（#7/#70/#92/#114…）已是 decisionLog 同族；增量为：技术债条目显式标 reduce/accepted，且"不能说出怎么消除的债就是 accepted（你选择了付代价）"这句可直接作 review 口径。AppShell 的"漂移即产品"类比：legacy 适配桥中"故意保持的不优雅"要标注理由，防止下一轮清理误删。
- **判定**：✅ 可移植。

---

## 4. Remote Relay（`remote-relay.md`）——本体不可移植，方法学可移植

**问题域**：不暴露端口地远程驾驶本机 agent，中继服务器不可信。

- **桌面/网络专属（❌）**：Noise IK/IKpsk2 握手、WebCrypto、配对 QR+一次性密钥、service worker 隧道、IndexedDB 设备密钥降级探测、PWA/Web Push、Docker 中继——Atlas 无对应场景（若 Atlas 未来暴露远程巡检 API 再回来看这节）。
- **可移植方法学（✅）**：
  1. **威胁建模表**（| 各方 | trusted with | must not be able to |）：JMeter 生产巡检直接可用——生产凭据、巡检服务器、被测环境各自"信任什么/绝不能能做什么"，504 排查类事故复盘用此表定位信任边界。
  2. **对安全护栏做变异测试**：独立对抗评审找 10 个弱点→逐个修复+加测试；再把**每个安全检查单独回退**，要求有测试变红（34+25 个变异体全部被杀，1 个等价变异体显式记录排除原因）。Atlas 的写护栏（`config.validate_write`/probes 建单门/RiskLevel 判级）是同类"安全检查"，适用同样验证：每条护栏单独失效一次，门禁必须红。
  3. **请求标记防伪造**：转发请求 strip 客户端可伪造头、加进程级随机 secret（从 `process.env` 挪到 `globalThis` symbol，因为 env 会被 agent 的 shell 继承）——若 Atlas 出 HTTP 巡检状态服务时适用；"密钥不放 env（子进程继承面）"这条对 Atlas 存凭据同样成立。
  4. **spike→E2E→对抗评审→分阶段**，且 **"Not yet verified" 显式成段**（真机 iPhone/生产部署未验）——Atlas 设计文档可复制此诚实性格式（scheduled-tasks 的 §10"开放问题与待验证项"同款）。
- **附带教训**：`next dev` 的 HMR WebSocket 会卡死隧道、production build 才通——移植为"巡检/自动化只跑生产构建路径，dev 模式产物不作为验收对象"。

---

## 5. Worktrees（`worktrees.md`）

- **机制**：同一项目多分支并行 checkout（`<repo>-worktrees/<branch>` 固定路径约定）；会话绑定创建时的 cwd 不漂移；删 worktree 若有未提交/未跟踪文件 Git 拒绝→**显式 force 动作单独提供**（明示丢弃代价）；已删 worktree 的旧会话仍留在项目下（历史可查）；分支已被占用时给出"切过去或先删"的可执行指引。
- **Atlas 类比**：①runlog 的 iserver 实验室**副本**思路可升级为 worktree——git 原生、不复制文件、天然可弃；②agent 模式若并行修多条用例，worktree 隔离防互相踩（对应 agent-teams §9.2 的清醒警告：目录隔离**不是安全边界**，防误改不防恶意）；③"删除保护=强制显式声明丢弃"与 Atlas 生产写确认同构。
- **判定**：✅ 工作流模式可移植（与 git 相关，跨语言）；侧栏 UI ❌ 桌面专属。

---

## 6. Web Profile / 日志（`web-profile.md`）

- **机制**：开发日志默认安静（保留启动/编译诊断与浏览器/服务器 warnings/errors，隐藏例行访问记录）；`--verbose`/环境变量分级；**verbose 访问日志跳过含 query string 的 URL——凭据不进日志**；响应体、工具输出、原始安装参数**任何模式都不落诊断日志**；未知 stdout/stderr 原样转发（不加行缓冲不吞）。
- **Atlas 类比**：JMeter 巡检的 URL 天然带 session token/ou/account 参数——"默认+verbose 都不得打印带 query 的请求行与响应体"应成为 `recorder.py`/`reporting.py` 的硬规则；失败快照里 header/参数脱敏同理（memory 的敏感词零残留已覆盖资产层，此条补日志层）。"未知输出原样转发"对应子进程 stdout 不做缓冲重排（调试现场时序即证据）。
- **判定**：✅ 可移植，成本低、价值高（P0 候选）。

---

## 7. Terminal（`terminal.md`）——本体专属，三个流语义可移植

**问题域**：PTY 生命周期、断线重连、输出流有界。

- **桌面专属（❌）**：node-pty 预编译二进制/源码重建修复、macOS spawn-helper 位修复、SIGTERM/SIGHUP→2s→SIGKILL 升级、终端 UI 恢复。
- **可移植语义（✅）**：
  1. **有界历史+游标续传+全量兜底**：输出带单调递增 offset，重连只补发缺失后缀；缓冲上限 128KiB，游标过旧→**显式 reset + 有界回放**而非无限追。类比 Atlas 无头批量的增量状态流、Allure 结果增量收集、巡检 tail 日志重连——都该有"追不上就明说并给边界内快照"的策略。
  2. **投递不确定不盲重试**："Failed input is not retried because its delivery may be ambiguous"——输入写失败不自动重试因为不知服务端是否收到。这是**写操作重试纪律**的表述：Atlas API 造单（wo_to_sale_order_save 类）遇到 502/超时不能盲目重发（双单风险），先探测后决策；与 `healing.retry_read_only`（非只读禁自动重试）同源，可补一条"只读可重试、写不可信通道先查再试"。
  3. **租约回收"创建了但从未接上"的资源**：PTY 创建给 120s 连接租约、订阅即续期、最后一个订阅者离开重新起租——专治孤儿资源。直接对应 memory 里 pi-agent-desktop `npm test` 挂死两小时的教训（relay 泄漏无 TTL），Atlas 的浏览器/子进程句柄应有同款"无订阅者 TTL 回收"；**预生成随机 ID 使创建幂等**（重复 effect 不双开）同理适用于造单类任务的幂等键（客户端预生成订单键，重试复用）。
  4. 输入序列化+相邻批量（远端不必每键一往返）、粘贴切分不切字符——对应批量 API 调用的合批语义（低优先）。

---

## 8. Desktop Updates（`desktop-updates.md`）

- **桌面专属（❌）**：Tauri updater 签名、Apple 公证/Authenticode、"每周最多查一次"、本地构建不注册 updater（防调试包接受更新载荷）。
- **可移植类比（⚠️ 概念层）**：
  1. **component-versions.json 钉三件套确切版本**：Atlas 报告/运行应固化「atlas 引擎版本 + 知识库版本 + flow md5」三元组保证可复现（dsl.py 已有 md5 缓存，缺"写进报告契约"一步）——排查"上周能跑今天不能"先看三元组差异。
  2. **draft Release 直到全平台产物+清单齐备才 publish**：与 dsl.py「任何一步不合法整体拒绝，绝不把半成品交给执行」同构——Allure 报告**原子发布**（半截报告被当完整报告消费是假信号），发布前完整性检查失败就整包不发布。
  3. **就地打补丁被明令禁止**（永远整包换）：Atlas 知识库/经验库晋级也是"全量受控变更+留痕"，不做运行时热改条目。

---

## 9. 汇总表：可移植模式 × Atlas 落点 × 优先级

| # | 模式 | pi 来源 | Atlas 落点（类比） | 判定 | 建议优先级 |
|---|---|---|---|---|---|
| P1 | 补跑语义（≤2min 正常/≤7d 补跑一次/skippedSlots/>7d 丢/改配置重置水位线） | scheduled §4.3 | JMeter 巡检、定时回归错过周期 | ✅ | **P0** |
| P2 | 调度权租约 {pid,heartbeat} + 接管后收尾遗留 running | scheduled D6/§11 | 本地+服务端双开巡检防双触发 | ✅ | **P0** |
| P3 | 连败 5 自动暂停+原因留痕；手动 Stop/进程被杀不计失败 | scheduled D7/§11 | failures.py CONTROL_FLOW 的调度侧条款 | ✅ | **P0** |
| P4 | 推迟≠跳过≠失败三态；重叠不排队；手动旁路闸门 | scheduled §4.3 | 巡检并发拥挤时的记录口径 | ✅ | P1 |
| P5 | 无人值守默认只读+写档服务端强制确认字段+最小间隔+超时 abort | scheduled D3/§4.5/§7 | agent.py/cli.py 无头路径（无 TTY 也可强制） | ✅ | **P0** |
| P6 | 证据式验收：命令+退出码+断言数+显式结论行，缺则挂起 | teams §4.2 team_result | review.py/healing.py 晋级格式硬约束 | ✅ | **P0** |
| P7 | 上限分因拒绝（LIMIT_EXCEEDED per-team vs global）+计数排除自身+满则登记有效不派发 | teams §8 | 无头批量/巡检并发闸 | ✅ | P1 |
| P8 | 人类优先槽：人的消息跳过合并窗口直插，agent 消息防抖 | teams §8/teamBus | 批量 run 中人工干预/CDP 冻场不排队 | ✅ | P1 |
| P9 | waiting/run_attention 显式"需人"态；dropped 终态 | teams §4.3 + scheduled §15 | 批量遇人工步骤：等待≠失败 | ✅ | P1 |
| P10 | Mandate：授权成实体、带 policyVersion，规则变严则存量授权失效 | teams §1.3 | agent.py 自动执行授权留痕升级 | ✅(概念) | P2 |
| P11 | 表层 vs 结构漂移分级 + merge 前分类 changeset gate 自动/人审 | ownership | 迁入资产晋级闸（mapping=manifest） | ✅ | P1 |
| P12 | 哨兵双向断言（复活/侵蚀）+ 变异注入验证断言本身 | ownership §sentinels | 迁移桥守护 + 772 门禁"测试的测试" | ✅ | **P0** |
| P13 | 失败集合子集对比判定合并成功；不跳测试/不降标准/新文件也查 | fork-sync | 迁移验收回归基线规则 | ✅ | **P0** |
| P14 | disposition reduce/accepted + decisionLog 防重议 | ownership | 技术债/红线注记 | ✅ | P2 |
| P15 | 威胁建模表（trusted with / must not be able to） | relay | 生产巡检凭据边界复盘模板 | ✅(表) | P1 |
| P16 | 安全检查逐条变异验证（单独回退必有一测红） | relay §mutation | validate_write/建单门/风险判级护栏自检 | ✅ | P1 |
| P17 | 日志默认降噪、query 串跳过、响应体永不入日志、未知输出原样转发 | web-profile | recorder/reporting 脱敏规则（巡检 URL 带 token） | ✅ | **P0** |
| P18 | 有界缓冲+单调游标续传+过旧全量 reset 兜底 | terminal 传输 | 批量增量状态流/报告流断线语义 | ✅(语义) | P2 |
| P19 | 投递不确定不盲重试；写操作先查再试；幂等预生成 ID | terminal 输入 | API 造单重试纪律（502 双单风险） | ✅ | P1 |
| P20 | 租约回收"创建未接上"资源（120s TTL，订阅续期） | terminal 生命周期 | 浏览器/子进程句柄防泄漏 | ✅(语义) | P2 |
| P21 | 报告三元组版本钉（引擎+知识库+flow md5）；全产物齐才发布 | updates | Allure 原子发布+可复现戳 | ✅(概念) | P2 |
| P22 | worktree 并行隔离（显式 force 删除；会话绑原 cwd；隔离≠安全边界） | worktrees/teams §9 | 实验室副本升级；agent 并行修用例 | ✅(流程) | P2 |
| P23 | 注意力经济学/最小概念（一个默认值胜过一个配置项）/简洁发言/诚实报告 | teams §1.3 哲学 | CLI 与 agent 输出纪律（少配置、少话、失败叫失败） | ✅(纪律) | P1 |
| P24 | "Not yet verified" 显式成段 + 开放问题清单 | relay/scheduled | 设计文档验收诚实性格式 | ✅(文档) | P2 |
| — | 多 agent spawn/邮箱 claim/Subscription 扇出/泳道 H5 渲染 | teams 本体 | 无对应场景 | ⚠️ 条件可移植 | 观望 |
| ✗ | Noise/E2E 加密/配对/PWA/Web Push/Tauri 签名/公证/updater/node-pty/WebView 托盘通知/tmux backend/Web Locks/SSE 连接数 | relay/updates/terminal/scheduled §15 | 桌面与浏览器应用专属 | ❌ 不可移植 | 不做 |

---

## 10. 交叉验证：pi 与 Atlas 独立收敛的同一条红线

这些模式 pi 与 Atlas 各自独立做出了同样的设计——说明是通用纪律而非桌面特性，Atlas 吸收时按"既有红线的第二个实现"处理即可，不需要新立规则：

| 纪律 | Atlas 现状 | pi 对应 |
|---|---|---|
| 终止信号不是业务失败 | `failures.py` CONTROL_FLOW 单一真相（#114） | 连败计数排除手动 Stop 与 kill 留下的 Interrupted |
| 半成品绝不交给执行 | `dsl.py` 候选校验→原子应用，整体拒绝 | relay 只在全部产物齐后 publish；先落库后执行 |
| 证据优先于措辞 | 审核塔"列表带证据、批准即写知识"、红线 #7 | 防假交付 evidence 三元组+显式结论行 |
| 不伪装通过 | EnvBlocked 独立终态；fork-sync"不跳测试制造绿灯" | 诚实报告哲学；失败集合子集对比不声称全绿 |
| 判据单一真相、改一漏一必漂移 | #92/#114 反复强调 | `isStructuredProtocolMessage` 单点路由；pending 判据收敛 |
| 无人/高风险不自动放行 | HIGH 无自动路径、自愈只修低风险资产 | read-only 默认预设、worker 不能 spawn worker |

---

## 11. 落地建议（供 gap6 方案文档直接引用）

1. **P0 六项**（P1/P2/P3/P5/P6/P12/P13/P17 中挑最小改动）全部是**语义/纪律移植**，不动 Atlas 架构：补跑+租约+连败暂停进巡检调度器设计；证据三元组进 review/healing 晋级字段；哨兵变异验证与失败子集对比进迁移验收 SOP；日志脱敏进 recorder 规则。
2. **P7-P9/P19（并发闸与重试语义）**建议合并为一个"巡检运行控制"设计（上限分因、推迟≠跳过、人类优先、写不盲重试），与既有 `waits.py`/超时阈值纪律（memory：3 分钟无日志=卡死按失败）对接。
3. **P10/P23（Mandate 与哲学层）**属演进方向，写进 agent 模式长期设计意图即可，不单独立项。
4. ❌ 项（加密隧道、桌面签名分发、PTY 管理、多 agent 编排本体）**在 gap6 里明确标注"不吸收"并给出理由**（无对应场景/桌面专属），防止后续被"看起来很先进"重新提出——这本身也是 fork-sync"declined 即永久 declined，重提需新证据"纪律的应用。

---

## 附：精读文件与实现锚点清单

| 文件 | 行数 | 关键节 |
|---|---|---|
| docs/agent-teams-design.md | 1079 | §1 四来源调研、§4.2 team_result 验收语义、§8 上限（2026-11-05 复核注）、§9 不做 process backend 的三条理由、§1.3 DeepSeek 哲学 |
| docs/scheduled-tasks-spec.md | 356 | §2 D1–D7、§4.3 调度循环、§11 实现记录（croner 截断坑/instanceof 坑）、§15 合并前复查（孤儿会话/Web Locks/run_attention） |
| docs/ownership-boundaries.md | 243 | 表层vs结构漂移、哨兵双向+变异、CI 分类 gate、disposition/decisionLog、"看似错误实为刻意" |
| docs/remote-relay.md | 193 | 威胁模型表、配对证明修正史、对抗评审+34/25 变异体、spike 发现（只跑 production build） |
| docs/worktrees.md | 84 | 路径约定、删除保护/force、会话绑 cwd |
| docs/web-profile.md | 47 | verbose 分级、query 串跳过、响应体永不记录 |
| docs/terminal.md | 64 | 租约 120s、单调 offset 续传、128KiB 有界、不盲重试、幂等 ID |
| docs/desktop-updates.md | 47 | component manifest、draft-until-complete、本地不注册 updater |
| docs/fork-sync.zh-CN.md | 102 | 失败集合子集对比、"新文件同样要查"、0 项测试静默坑 |
| 锚点：pi-team/src/constants.ts | — | DISPATCH_QUIET_WINDOW_MS=2000、TEAM_HOST_GLOBAL_KEY（跨端宿主桥降级） |
| 锚点：scripts/fork-ownership.json | — | riskModel.basis=structuralDrift（高 250/中 30）、autoPushPolicy、decisionLog |
| Atlas 对照：src/atlas/{agent,failures,healing,review,experience,dsl,engine}.py | — | 风险分级/终态口径/自愈闸门/审核塔/经验草稿/原子编译/CDP 冻场 |

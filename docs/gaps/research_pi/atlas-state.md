# Atlas 现状能力对照表（gap6 前置盘点）

> 目的：盘点 Atlas 已实现能力与「灰区」，防止把 Atlas 已有的东西当成 pi-agent-desktop 的新候选重复推荐。
> 作者：atlas_state（团队 atlas-absorb-pi-eval）· 日期：2026-11-05
> 纪律：本文件为唯一产出；src/ 与 tests/ 只读未改动；未 commit/push；未运行真机/浏览器会话。

## 0. 基线事实

- 位置 `D:\GitHub\Atlas`，Python src 布局（`src/atlas/`），包版本 `0.1.0`，知识驱动 UI/API 测试平台。
- **离线测试基线：当日全量实跑绿（本任务口径），全部改动未提交**。（计数只认 README 徽章 / AGENTS §2 四处，本文不留计数位）
- ⚠️ 文档事实漂移（2026-11-05 当时现场，保留为历史记录）：README badge 与「当前验证边界」、CLAUDE.md 仍写 **720 passed**，AGENTS.md §2 也写「当前基线 720 passed」。吸收改造（LiteFlow 编译纪律等）带来的 +52 条测试尚未回写这三份文档。gap6 推荐任何能力前，**先以 `pytest -q` 实测为准**，别引用 720 这个陈旧数字。
- 未提交工作区（10 项）：`CLAUDE.md`/`README.md`/`docs/gaps/gap5-allure.md`/`cli.py`/`dsl.py`/`engine.py`/`reporting.py` + 三个新测试文件（`test_dsl_atomic_compile`/`test_dsl_template_dispatch`/`test_reporting_runlog_fields`）。这些正是 LiteFlow 吸收改造（原子编译 / md5 / ^ 指针 / {{slot}} / dispatch / 死字段清理）。

## 1. AGENTS.md 红线清单（原样摘录要点）

**§1 Never（硬性禁止）**：
1. 禁止硬编码凭据或真实 `.env` 入库；密码只从环境读取（沿用 `DEMO_*` 键名）。
2. 禁止生产写操作（`prod-*` 建单/保存/提交/删除由引擎拒绝，不加绕过开关）。
3. 禁止自动创建无「非人工创建」备注的单；create 受配额限制并保留可回收信息；`validate_write` 无条件要求备注含标记。
4. 禁止静默修改 `legacy-suite`/`legacy-api` 原仓库；只经适配器复用，不回写。
5. 禁止把 toolbox 当执行底座（它是感知器官，不替代 Python+Playwright 兼容层）。
6. 禁止步骤内数据兜底（换客户/循环试备件/降数量/抄历史单）；数据缺失用 `EnvBlocked` 明确终止。
7. 禁止为变绿而放宽断言、吞异常、把失败改 SKIP；环境限制/产品缺陷/代码缺陷必须分开。
8. 禁止无界面截图开销进入 run 模式；成功步骤不默认截图，debug/heal/失败才取证。
9. 禁止把未验证的 API 当 UI 自动兜底；`auto` 依赖 Task 显式能力，不猜接口、不自动重放写请求。
10. 禁止无审核的知识/选择器/自愈写回；冷启动一律人审，未审核项只能写 `pending_review` 草稿。
11. 禁止把业务知识写入通用引擎；真实目录/类/键名差异走 `ATLAS_*` 映射；真实知识/选择器/BU 坐标写回目标是私有目录 `ATLAS_KNOWLEDGE_DIR`，批准永不写包内文件。
12. 禁止为「全量」造形式代码（未验证 API 建单、完整状态机、数百条强契约不得声称已完成）。
13. 禁止绕过 agent 风险阈值；`AtlasAgent` 默认只自动执行 `read_only`，提阈值或 `--approve` 必须是人的决定。
14. 禁止让 create 先建单再查配额；配额必须在调用建单脚本**之前**占用。
15. 禁止伪造跨平台验收；Linux 必须真实执行并归档 `LINUX_ACCEPTANCE_PASSED`；Dockerfile/compose/CI **未实测**，不得声称已验证。
16. 建单报文必须来自真实捕获（`scripts/capture_save_request.py`），不得凭字段名猜。

**§2 验证纪律**：每次改动至少 `pytest -q` + `compileall -q src` + `atlas doctor`；新测试不依赖真实网络/浏览器/凭据；真实 UI 回归须报告 PASSED/SKIPPED/FAILED；**离线测试通过 ≠ 能力可用**（曾离线全绿但实跑 `No module named 'lib'`、`open_doc_list() missing page_key`）；真实 `atlas run` 需先用 `ATLAS_SESSION_*`/`--bu` 接真实会话值。

**§3 架构不变量**：Task `requires/produces` 与 `perform()` 实际读写一致；`compose_for_goal` 同产出键多生产者显式报错不静默择一；Ability 构造注入，业务 Task 不起第二浏览器；Cookie 只烘焙到匹配域；知识写回先草稿后人审，agent 错误分类经人纠正后才可成新 pitfall。

**§4 Ask first**：任何生产写；修改外部公司项目/对外平台同步/创建测试计划或发送报告；向第三方 AI/云上传代码·知识·截图·接口·数据；自动批准自愈/降人审级别/改生产保护或自动建单配额；引入浏览器驱动之外的运行时（Rust/Node 等）、完整状态机或大型框架。

> 红线含义对 gap6 的直接约束：Atlas 已把「人在环审批 + 只提案不执行 + 风险分级 + 决策留痕」制度化（见 §2 与 agent/healing/review）。凡 pi 侧「自动批准、无人审、agent 自由执行」的机制，都要先对齐这套纪律再谈吸收。

## 2. Atlas 已有能力对照表

主题标注：**会话**=session/conversation 生命周期，**调度**=定时/队列/并发编排，**编排**=多任务/依赖/多 agent 组合，**权限**=审批/风险/护栏，**更新**=版本/缓存/失效。

| # | 能力域 | 现状（Atlas 已实现什么） | 实现位置 | 相邻 pi 主题 |
|---|---|---|---|---|
| A1 | DSL 编译 · 校验-原子应用 | `compile()` 先做零构造静态校验（形态/未注册 Task/channel/on_env_blocked/步骤 id 唯一/dispatch 目标与 default/模板槽位/assert 块）再构造 Task，再语义校验（requires/dispatch.on 引已声明 ctx 键）；任一不过整体拒绝、一个 Task 都不构造、绝不先跑数据供给；`run()` 校验挪到 provision 前。**LiteFlow 吸收来的三条编译纪律之一，未提交** | `dsl.py`（`validate_flow` 单一来源、`FlowReferenceError`、`compile`/`run`）+ `tests/test_dsl_atomic_compile.py` | 编排（结构校验前移=执行前拒绝，等价 pi 的 plan 校验） |
| A2 | DSL · md5 编译缓存 | `load()` 返回 `FlowDocument`（dict 子类带源文本/md5/节点定位表）；缓存键 = 源文本 md5 **+ 当前 steps 结构指纹 + registry 代次**三者都不变才复用；只存内存不落盘（落盘=造「磁盘产物≠当前代码」脏态）；解包 `{**flow}` 会丢形态，`overlay()` 保留 | `dsl.py` `FlowDocument`、`_document()`、进程内缓存 dict | 更新（缓存失效/脏态判定，pi 是 lock/产物版本对齐） |
| A3 | DSL · ^ 指针报错 | 未注册 Task、requires/dispatch.on 引不存在、dispatch 目标不存在、槽位未填 → `--> 文件:行:列`+源行+`^`（`yaml.compose` 节点标记）；内存构造无源文本退化为「步骤 N」粗定位，不假造行号 | `dsl.py` 节点定位表 + `FlowReferenceError.__str__` | 权限/诊断（可归因报错） |
| A4 | DSL · {{slot}} 模板 | `template`+`slots` 骨架文本级 `{{slot}}` 实例化；槽位值限单行标量（容器/多行挤歪行号 ^ 就指错）；没填完的骨架不产出可执行 flow（残留 `{{...}}` 编译期拒）；YAML1.1 裸 `on:` 归一、裸布尔分支键直接拒 | `dsl.py` `_materialize()` / `_check_slots()` | 编排（参数化复用同一真相流程） |
| A5 | DSL · dispatch 前向路由 | `dispatch:{on,to:{取值:步骤id\|[ids],default:必选}}`；读已声明 ctx 字段按取值走分支；未选中分支整步不执行不进 runs.jsonl；取值缺失按 `EnvBlocked` 终止不静默走 default；决策留 `ctx["_dispatch_routes"]` 并入路由节点 step 记录 `dispatch` 键（engine `_flush_routes` 游标搬运）；**删 lane 必须同时删那一步——无主步骤恒执行、编译零警告（有意取舍，预留 `dispatch.exclusive` 收紧）**；钉桩 `test_unclaimed_branch_step_runs_unconditionally` | `dsl.py` `_RouterSpec`/`_dispatch_map`/`_check_dispatch` + `engine.py` `_flush_routes` | 编排/调度（条件路由=工作流分叉，pi 的 lane/扇出） |
| A6 | engine 执行 · 通道与取证 | Task 执行策略；`channel:auto` 仅 Task 显式 `supports_api` 才走 API 失败降级 UI，只捕 `(NotImplementedError,RuntimeError)`（契约违规/网络故障照常传播）；降级原因经游标并入 step 落 runs.jsonl（`degraded_to_ui`）；写护栏在 `recorder.step` 内；控制流异常短路记 interrupted；`CDP_PAUSE_STEP` 坏值点名不静默；三处 `capture()` 经 `_record_shot` 写 `attachment`（root 相对）；自愈成功就地更正同一条 step 为 healed | `engine.py` `_perform`/`execute`/`_record_shot`/`_flush_routes` | 编排（运行期路由决策与取证落盘） |
| A7 | failures 分类学 | 8 类：`env_blocked/session/interaction/contract/data/assertion/interrupted/unknown`；`CONTROL_FLOW=(KeyboardInterrupt,SystemExit,GeneratorExit)` 单一真相（engine/healing/recorder 同源，先于关键词短路）；`exception_text`/`exception_message`/`classify_text` 单一真相（坏 `__str__` 回落类型名不裸崩）；`api_evidence` 对 data 形态零假设，`status` 归一化数字串 | `failures.py` | 诊断/编排（根因归类=pi 的失败可解释性） |
| A8 | healing 自愈准入 | 只读 Task + 命中**已审核知识**（`status=approved` 且有 remedy）才就地重跑；`match_pitfall(status="approved")` 过滤在「最长 trigger 择一」**之前**（pending 草稿不遮蔽 approved）；写操作永不自动重试；控制流异常一律拒绝自愈（#108，终止信号不是业务失败）；**只提案 + 知识命中才执行，绝不放宽断言** | `healing.py` + `knowledge.py` `match_pitfall` | 权限（自愈=自动修复的严格准入，对标 pi 自动批准红线） |
| A9 | experience 草稿→审核塔 | 失败草稿确定性 id+去重计数 `seen`，同坑复发不重复占塔；**唯一写路径 = pending_review**；`draft_from_text` 单一真相；追加前检测尾字节非换行先补断行（裸 append 黏残行会把新草稿合并成残行被读侧静默跳=#102 假成功）；坏 UTF-8 replace 回落，非映射行跳过；同 id 刷新最新 evidence 不抹已有 | `experience.py` | 会话/编排（证据累积=pi 的 trace/checkpoint 沉淀） |
| A10 | review 单一审核塔 | 所有产物（失败草稿/探索候选/BU 坐标 diff/code_review finding/gen 摘要）汇入**一个塔**；证据卡片；`--approve` 按 kind 分发（坑点→知识、selector_candidate→组件库、trace_step 拒之塔外、bu_overlay→私有 overlay）；`--reject --reason` 留痕不删；`--kind/--group/--template/--apply/--reject-kind/--stats`；决策落 `review_decisions.jsonl`；pending 判据收敛 `_is_pending` 单一真相；`code_review` 不可 `--approve`（只出报告不写知识）；只读库写闸门（未设 `ATLAS_KNOWLEDGE_DIR` 时 PermissionError 点名） | `review.py` | 权限（人在环审批闸） |
| A11 | reporting / runs.jsonl | runlog 风格 JSONL 运行记录：`RunRecorder.close()` 每 run 追加一行（`run_id/case/env/mode/status/started_at/finished_at/steps[]`）；step `ok="fail_class" not in item`、`dur_ms`；残行/非对象/缺主键/坏字节 replace 逐行跳过不顶整份；`summarize`/`export_runlog` 聚合；产物默认与 `config.reports_dir` 同源不赌 CWD | `recorder.py` + `reporting.py` | 会话（按 run 的执行史；**但只有一条扁平时间线，无按项目浏览/恢复会话的概念——见灰区 G1**） |
| A12 | CLI 子命令 | 22 个顶层子命令（详见 §4）；用户输入错误统一「一行 stderr + rc 2」，不裸 traceback；全局参数 `--root/--env/--mode/--headed` 用 parents 共享，子命令前后均合法 | `cli.py` | 编排/调度（统一确定性入口） |
| A13 | agent 模式 · 风险分级+审计 | `AtlasAgent` 按元数据规划→`classify_risk`（read_only/low/medium/high，生产写恒 HIGH）→`can_auto_run(risk<=max_auto_risk 默认 read_only)`→超阈值且无 `--approve` 抛 `ApprovalRequired`（rc3 决策信号）；每次决策写 `reports/agent_decisions.jsonl`（含拒绝、半途执行失败/中断与 `executed` 完成清单）；留盘尽力而为不顶业务信号（#110）；中断判据收敛 CONTROL_FLOW；reason 展示走 exception_message 单一真相 | `agent.py` + cli `agent --execute --approve --max-auto-risk` | 权限/编排（自动 vs 人审门，对标 pi 权限模式与审批后自动 launch） |
| A14 | JMeter 导入 · 巡检 | `scripts/import_jmeter.py`：`root.iter()` 递归抓 sampler（含 TransactionController 嵌套）→ DSL YAML；提 `HeaderManager` 全局鉴权头并入每个 api_call（缺 `isc-app` 回 BUG-920，迁移必须保头）；报文完整迁移（`postBodyRaw` 按 elementProp 分组取正文，不再 `[:500]` 腰斩）；`${VAR}`/`${__P()}` 原样保留+递归下钻点名供人审接 env；**只生成 YAML 供人审，绝不自动执行**；引擎侧 `CallEndpoint`/`CallApi.request` per-request headers 贯通，body 按 requests 语义分流（dict/list→json=，str/bytes→data=） | `scripts/import_jmeter.py` + `abilities.py` | 编排（巡检 flow 生成；**无定时任务规格——见灰区 G2**） |
| A15 | web-toolbox 感知集成 | `toolbox.py` 感知器官（看/录/重放，不作执行底座）：`learn_contracts`（200 抓包登记 minimal 存活级，不自动升 high；`entries` 优先/`items` 兼容真实 v1.1.4；`_unwrap_records` 单一真相不折叠错误形态为 []；分页闸门 total>页大小点名截断）；`console_errors`（合法空表如实 []，错误形态点名拒绝）；`save_har`（真实桥接纯文本错误不包成假 .har，缺 log 结构 dict 拒写坏档）；`replay`/`chain_replay` 无 src 消费方透传诚实不动 | `toolbox.py` | 会话/编排（浏览器抓包回流；附着真实浏览器不做注入伪装，符合用户红线） |
| A16 | doctor 门禁 | `atlas doctor` 输出 JSON 检查项：`python/root/env/env_prefix/base_url_configured/base_url_source/legacy_web/legacy_pages/legacy_pytest_ini/demo_api/knowledge_pitfalls/docker_ref_missing/docker_cmd_matches_compose/docker_assets_consistent/docker_unreadable`；rc0 需 legacy_pages+demo_api+docker_ok；坏资产 `_read_text_safe` 记 docker_unreadable 判不一致不崩诊断；**注意 doctor 无 `contracts_*` 键**（契约覆盖走 `contracts`/`contracts --validate`，与 doctor 分离） | `cli.py` doctor 分支 + `config.py` `check_legacy_paths` | 调度（环境体检门） |
| A17 | contracts 契约分级 | 三档 core/high/minimal（不按数百接口盲生强 schema）；构造期钉死形态（required 类型值/路径键写坏当场 ValueError）；同 `(method,path)` 两条不同定义显式点名不 last-wins 遮蔽（#128，等值重复放行给 learn_contracts 去重）；`contracts --validate` 只读结构自校验点名空 path/name「冒充覆盖率永不生效」rc2 不发真实请求；展示与运行时同源私有→包内逐文件回退 | `contracts.py` + cli | 权限（校验强度=断言护栏） |
| A18 | exploration 会话 | `atlas explore` 开非生产 CDP 会话给外部大脑接管；回车记 checkpoint（aria 快照落 artifacts/exploration/）；`--compile` trace→pending_review 草稿（trace 逐行容错，坏字节 replace 回落）；**探索永不直接成资产** | `explore.py` | 会话（探索式会话沉淀；与 pi 交互式接管语义同构但产物只进塔） |

## 3. 灰区：Atlas 已有「零件」但没有 pi 那种「形态/闭环」

这些是最容易被 gap6 误判为「Atlas 已具备、无需吸收」的——实际只是素材存在，缺 pi 的组织形态：

- **G1 · 按项目浏览/恢复会话**：Atlas 有 `runs.jsonl`（扁平时间线，一行一 run）和 `experience_drafts.jsonl`/`review_decisions.jsonl`，但**没有** pi 那种「以项目/会话为单位枚举历史、点开恢复上下文继续」的概念。runs 是审计流不是可恢复会话。→ 若要「会话历史浏览+恢复」，这是真空缺，不是重复。
- **G2 · 定时任务规格**：Atlas 有 JMeter 巡检、`scripts/import_jmeter.py`、无凭据离线门禁 `scripts/selfcheck.py`、Linux 远程验收脚本，但**没有任何 cron/schedule/once 的定时任务数据结构与调度器**（Desktop ScheduledTask 那种 `kind:once|cron`+时区+notification 规格在 Atlas 里完全不存在）。巡检靠人手动 `atlas run`。→ 「定时/计划任务」是真空缺。
- **G3 · 多 agent 团队编排 / 并发上限**：Atlas 有 `AtlasAgent`（单 agent 规划）、`compose_for_goal`（Task 依赖编排）、dispatch（flow 内路由）、plugin 缝（`resolve_plugin`）——但**没有**「多成员团队、每团队/全局并发上限（pi 的 8/32）、人类优先槽、扇出 ack/receipts、成员身份注入、审批后自动 launch」。`subagents.ts` 式 profile、teamBus 式总线全无。Atlas 的「编排」停留在**单 flow 内 Task 层**，不到**跨 agent/成员层**。→ 「团队协作/并发闸」是形态缺口。
- **G4 · 工单三段式 / Mandate-Revoke / Assignment 独立推进**：Atlas 只有 review 塔的「pending→approved/rejected」二态草稿，**没有**不可变 Mandate 只能 revoke、Assignment 按 id 独立推进、重 ack 幂等这类工单生命周期。→ 缺口。
- **G5 · 权限模式机（plan/acceptEdits/dontAsk/bypass 等）**：Atlas 的风险是 `read_only/low/medium/high` 单维分级 + 生产写恒 HIGH + `--approve` 人工闸，**没有** pi/Claude 那种「命名权限模式 + 每模式工具白名单 + 模式切换」。是硬编码阈值不是模式系统。→ 形态缺口（理念已有，机制不同形）。
- **G6 · 版本升级 / 组件版本清单 / lock 漂移防护**：Atlas 只有 `__version__="0.1.0"` 与文档事实（且已漂移，见 §0），**没有** `component-versions.json` 式组件版本清单、git 源依赖 lock 校验、`npm ci` 式装配一致性门。→ 更新主题的缺口。
- **G7 · 远程访问 / relay / 多端**：Atlas 是本地 CLI + Playwright，`Dockerfile`/compose/CI 明确**未实测**（红线 15）。pi 有 LAN 模式/隧道/Tailscale/relay。Atlas 无任何远程服务面。→ 缺口（但受红线「不引入浏览器驱动之外运行时/大型框架」「不对外平台同步」约束，需 Ask first）。
- **G8 · 跨端扩展接线（built-in extensions / marketplace / registerCommand）**：Atlas 有 `resolve_plugin()` 引擎级插件缝（`ATLAS_PLUGIN` 动态加载）——这是 Atlas 唯一「扩展」机制，但形态是**业务插件（Task/Probe/知识）**，不是 pi 那种「命令/工具/钩子跨端注册」。→ 概念部分重叠，别把 plugin 缝当扩展市场已有。
- **G9 · 状态栏 / UI 观测层**：Atlas 观测=JSON 输出 + Allure HTML + runs.jsonl；`--serve` 只**打印** allure serve 命令不自行拉起。没有常驻 UI/statusline/dashboard（Allure 报告是外部 allure CLI 打开，且纪律要求走 HTTP 打开非 file://）。→ 展示层缺口。

## 4. CLI 子命令全表（读 cli.py `_parser()`，22 顶层 + config 子命令）

全局参数（parents 共享，子命令前后均合法）：`--root` `--env {sit,uat-eu,uat-amer,uat-hk,prod-eu,prod-amer,prod-hk}` `--mode {run,debug,heal}` `--headed`。

| 子命令 | 作用 | 关键参数 |
|---|---|---|
| `doctor` | 检查依赖/配置/外部资产（不登录） | — |
| `init` | 新项目骨架（.atlas-root/.env/pyproject/flow/tests） | `name` `--system` `--module` `--atlas-path` `--interactive` `--out` |
| `config add` | 微服务配置/插件骨架生成 | `domain` `--mapping` `--out` |
| `tasks` | 输出核心 Task 元数据 | — |
| `compose` | 验证 Task 依赖拼装 | `--goal` |
| `run` | 执行声明式 DSL（真实登录） | `flow` `--account/--ou/--language/--division/--no-login/--create-quota/--bu` |
| `compile` | 离线编译校验 flow YAML（不登录） | `flow` |
| `legacy` | 原样跑 legacy-suite pytest | `node` `--keyword` `--dry-run` |
| `machine` | 校验状态机 YAML 输出目标路径（不执行） | `file` `--target` |
| `contracts` | 契约分级覆盖展示 | `--validate` |
| `selectors` | 查 Demo 选择器注册表 | `component` |
| `agent` | 规划/风险评估（默认只规划） | `--goal/--execute/--approve/--max-auto-risk` |
| `report` | Atlas JSONL→runlog 步骤格式 | `input` `output` |
| `summary` | 聚合运行记录（状态/失败类/最慢步骤） | `input`（默认 reports/runs.jsonl） |
| `allure` | runs.jsonl→Allure 2.39.0 结果集（纯 stdlib） | `input` `--out/--clean/--serve` |
| `allure-analyze` | 解析 Allure 结果集：分类+自愈提案/塔 | `results_dir` `--to-tower` `--format` |
| `knowledge` | 查询知识库 | `query` |
| `recycle` | 列自动创建的单据（不自动删） | `--input` `--cleanup` |
| `review` | 审核塔：队列→证据卡→批准/驳回/批量/统计 | `--stats/--approve/--remedy/--component/--element/--reject/--reject-kind/--template/--apply/--reason/--kind/--group` |
| `explore` | CDP 探索会话/trace 编译 | `--url/--account/--trace/--compile` |
| `overlay` | BU 坐标 diff 与进塔 | `file` `--diff` `--to-tower` |
| `gen` | 接口文档→测试代码（只生成不执行，红线 9） | `source` `--source-type/--out/--flow-id/--scenario/--level/--headers-file/--plugin/--naming/--check-only/--strict/--force/--to-tower/--with-pytest` |
| `review-code` | 代码质量评审（含评分 Allure 报告） | `paths` `--kind/--review-mode/--format/--out/--config/--fail-under/--baseline/--to-tower` |

## 5. 给 gap6_evaluator 的一句话结论

Atlas 在**单 flow 内的确定性编排 + 人在环审批 + 失败分类 + 自愈准入 + 证据沉淀**上已成体系且纪律严苛（16 条 Never）。真正**缺形态**的是：跨成员团队编排与并发闸（G3）、工单生命周期（G4）、命名权限模式（G5）、定时任务规格（G2）、按项目恢复会话（G1）、版本/装配一致性（G6）、远程访问（G7）。评估时把这几条当候选，其余别重复推荐；引用测试数一律以实测为准（文档 720 已陈旧）。任何触碰红线 4/11/13/15 或 §4 Ask first 的 pi 机制，先对齐 Atlas 人审纪律再落地。

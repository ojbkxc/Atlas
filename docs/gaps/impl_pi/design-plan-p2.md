# Atlas pi-absorb 二期实施总协调（design-plan-p2）：C12 + C9

> 状态：**施工级设计总纲**（team atlas-p1-followup · p2_design · 2026-11-06）
> 上游：`docs/gaps/gap6-pi-agent-desktop.md` §3.2-C12/C9（P2 转做触发条件已满足：无头批量遇人工步骤计错失败/写投递不确定重试面）+ §5 红线对照表 + §9〔E6/S3〕；一期 `design-plan.md`（W0–W4 已终验通过，基线 **1448 passed** 实测口径）。
> 下游：design_c12.md / design_c9.md + 任务 #3（评审）→ #4（实施 C12）→ #5（实施 C9）→ #6（真机五项）→ #7（终验）。
> 沿用一期全部纪律：AGENTS.md 16 Never（重点 #7 不放宽断言 / #9 不自动重放写 / #10 知识写路径唯一 / #11 引擎不含业务字面量 / #13 不绕审批 / #14 配额先占）、#108 控制流拒自愈、CONTROL_FLOW 单一真相、#92 判据不复制、X1–X9 跨平台矩阵、memory 教训（同文件并行撑破表格→**热点文件一波一主人串行**）。

---

## 1. 波次与实施顺序（严格串行，理由见表下）

| 波次 | 任务 | 内容 | 实施者 | 前置 |
|---|---|---|---|---|
| P2-W0 | #3 | 本三份文档的对抗评审（判据表/归属表/兼容表逐条打；勘误以「评审勘误注」追加） | p2_critique | #2 |
| P2-W5 | #4 | **C12 写不盲重试**（纯拦截：收窄行为，无新词汇） | impl_c12 | #3 |
| P2-W6 | #5 | **C9 waiting 显态**（加词汇：枚举扩员波及面大） | impl_c9 | #4 |
| P2-W6r | #6 | 真机五项合并清单（含 design_c12 §9 / design_c9 §9 真机栏；烧配额须用户授权、**前台串行**——memory 纪律） | real_ops | #5 |
| P2-W7 | #7 | 终验（门禁三连 + X 矩阵逐条 + 兼容表逐行复核 + 文档事实回写核对 + 归因链以复跑数为唯一口径） | p2_final_verifier | #5、#6 |

**串行（不并行）的拍板理由**：①热点文件 `failures.py` 与 `engine.py` 两项都要改（C12 加 `DeliveryUncertain`+classify 短路+`_perform` 降级闸；C9 加 WAITING/OPERATIONAL/AwaitingHuman+execute 分支）——同波双主违一期「一文件一波次一主人」铁律；②C9 的红线 #7 自证与兼容表**引用 C12 定稿的终态形态**（投递不确定=failed/interaction 非 waiting，design_c12 §7 末行 ↔ design_c9 §2 非触发清单），后行判先行；③先窄后宽：纯拦截项先落地，枚举扩员项在其稳定后再动，回归面逐波可归因（一期 W1→W2→W3 同款节奏）。

## 2. 文件归属表（热点文件显式归一）

| 文件 | W5（C12 · impl_c12） | W6（C9 · impl_c9） | 规则/备注 |
|---|---|---|---|
| `src/atlas/failures.py` | **impl_c12**（DeliveryUncertain、classify_failure isinstance 短路→INTERACTION；`_RULES` 一字不动） | **impl_c9**（WAITING、OPERATIONAL、is_operational、AwaitingHuman、短路次序扩；关键词表仍一字不动） | 跨波同主串行；两波共同铁律：**判据只增不搬**，#92 测钉 |
| `src/atlas/engine.py` | **impl_c12**（`_perform` 降级闸 §design_c12 3.2；`delivery_uncertain` 记录标记） | **impl_c9**（execute AwaitingHuman elif；heal/CONTROL_FLOW 分支逐字不动） | 跨波同主串行；既有测 :279-339「OSError/契约不降级」两组是**红线测**，改动不得翻红 |
| `src/atlas/cli.py` | **禁入**（C12 无 CLI 面：rc1=failed 现行为即正确；`atlas recycle` 原样 JSON dump 自动透 idem） | **impl_c9**（run 分支 rc3+`WAITING-HUMAN:`；summary 分支 attention 装配） | 单波单主不变 |
| `src/atlas/reporting.py` | **impl_c12**（仅 :136 注释扩「唯一重试源」口径 + 行为测钉——文本协调见 §3-1） | **impl_c9**（新纯函数 `run_attention`；`summarize/export_runlog` 本体零改动） | 跨波同主串行 |
| `src/atlas/delivery.py`（新） | **impl_c12** | 禁入 | C12 判据模块（对齐 credential_gate/versioning/boundaries 先例） |
| `src/atlas/abilities.py` | **impl_c12**（`request_seq` 插桩；净化/烘焙零触碰） | 禁入 | |
| `src/atlas/probes.py`、`src/atlas/creators.py` | **impl_c12**（token、quota 条目、`honors_idempotency`） | 禁入 | `#14` reserve 先占语义逐字不动 |
| `src/atlas/dsl.py`、`recorder.py`、`healing.py`、`review.py`、`experience.py`、`scheduling/lease.py` | 禁入 | **dsl→impl_c9**（waiting 终态腿 + `close(status, reason=)` 接线）；**recorder→impl_c9 最小面**（仅 close() 末位可选参 `reason`，缺省不写键——C6 version 参先例；step 分类链零改动）；**healing/review/experience 零改动**（判据继承，测钉）——任务书「healing 只读面不扩」的答案=C12/C9 都不碰 healing 源文件，只加回归断言 | |
| `src/atlas/allure_report.py`、`allure_analysis.py`、`scheduling/decision.py`、`scheduling/dispatch.py` | — | **impl_c9** | decision `_POLICY` 一行 + dispatch rc3 一支（defer 语义不混） |
| `tests/test_delivery_retry.py`（新） | **impl_c12** | 禁入 | |
| `src/atlas/plugins/demo/tasks.py`（PLAN2 补标） | **impl_c12**（仅 C12-A 选项 (a) 落盘时需要：`SelectCustomerTask.api_leg_side_effect_free = True` 声明位 + 注释） | 禁入 | 声明位是**能力声明归插件**（红线 #11），引擎只 `getattr(task, "api_leg_side_effect_free", False)` 读；(b) 路线则本行不存在 |
| `src/atlas/scheduling/state.py`（PLAN2 补标） | 禁入 | **实测零改动**（评审 §4：`:363` 只委托 `failure_policy`，无 outcome 词表闸 → dispatch 增 `"waiting"` 不需动本文件） | 若后续给 outcome 加词表校验，落点须回到本行并归 impl_c9 单主，不得实施期即兴越表 |
| `tests/test_waiting_operational.py`（新） | — | **impl_c9** | |
| `tests/test_privacy_drift.py` | **impl_c12**（字段枚举 +`idem`/`delivery_uncertain` 两行 + 净化对抗） | **impl_c9**（失败类清单措辞补 waiting 一行） | 跨波同文件**串行各写各段**，每段在本表列明即满足一期 §2「例外通道」 |
| `tests/test_state_reporting_cli.py` / `test_reporting_and_toolbox.py` / `test_recycle.py` / `test_probes.py` / `test_creators.py` | 默认禁改；**仅当**键全等/形态断言因增广必红时，按 §3 协调补丁由归属人增广（条数只增不减，禁放宽） | 同左（summary 键面） | 一期 C2 补丁 3 先例 |
| `tests/test_xplat_sentinels.py` | **本体禁改**（目录级递归自动入网新文件——一期纪律延续） | 同左 | |
| `tests/conftest.py` | 禁改（无例外通道） | 禁改 | |
| `README.md` / `CLAUDE.md` / `AGENTS.md` / `PRIVACY.md` 事实回写（红线 #8） | impl_c12 写 C12 段（重试门、token、rc 无新增） | impl_c9 写 C9 段（rc3 语义表、waiting 终态、attention） | 波内单主；`atlas run` rc 表 W6 加一行「3=等人」，W5 不动 rc 表 |
| `D:\jmx`、真机凭据、`reports/` audit 文件 | 全程不碰 | 全程不碰 | 一期终验 §4 同款 |

## 3. 协调补丁清单（跨归属/跨文件的定点小改，评审重点核对）

1. **reporting.py:136 注释扩**（W5，impl_c12）：「唯一真实重试源是 engine heal 模式」→ 补「+ 有界降级重执行（attempted=False 才可；design_c12 §3.2）」；同波新增行为测钉两源形态（heal→healed 键 / degrade→degraded_to_ui 键），注释与盘上行为**互为哨兵**。
2. **summary 键全等断言增广**（W6，impl_c9）：若 `test_state_reporting_cli.py`/`test_reporting_and_toolbox.py` 存在对 summarize 返回 dict 的精确键集断言，改为**包含式**并新增 attention 断言行（允许断言形态适配新键，**禁止**删旧计数断言——放宽=红线 #7）。
3. **PRIVACY/失败类清单三处措辞**（W5 两键、W6 一词，见归属表行）；对应哨兵测行同波落地（M11 纪律：每条文档红线配一条会红的测）。
4. **候选（非本波承诺）**：dispatch 子进程 rc130→interrupted 词表补支（design_c9 §10 第一条）——#3 评审拍板做/不做；做则归 impl_c9（dispatch 同主），配 fake rc130 测 + 「不计败」断言；不做则原文留档，后续波次不得无声扩面。
5. **atlas schedule crontab 文案**（X8 文档义务，W6）：README rc 表行 + stderr 提醒补「rc3=等人，不计连败」一句——判据仍只在 decision/dispatch，CLI 零复制。
6. **候选（非本波承诺）**：`EnvBlocked` 腿 reason 写侧接线（allure_report.py:107 读 `run.get("reason")` 历史上恒空=存量读空面）——C9 的 `close(reason=)` 通道天然可接，但属存量缺陷修复非 C9 本体；#3 评审拍板，做则 dsl EnvBlocked 分支一并传 reason + allure 文案测增广（归 impl_c9）。

## 4. 跨平台矩阵 X1–X9（继承一期 design-plan §4 原文，逐波适用性）

| 条款（继承编号+名） | W5（C12） | W6（C9） | 备注 |
|---|---|---|---|
| X1 pathlib/禁反斜杠 | delivery token 纯字符串无路径；probes 既有 Path 纪律不动 | 同左 | 哨兵目录级扫描自动入网 |
| X2 argv 禁 shell=True | creators stdin JSON 注入不改 argv 形态（既有 `[python, str(script)]` 合规） | 不触 subprocess | |
| X3 O_EXCL 差异 | 不触 | 不触（defer 语义原样，与 waiting 分区） | |
| X4 GBK ASCII | stderr/消息：「->」与 `delivery uncertain` 段；§design_c12 5.18 直断 | `WAITING-HUMAN:`/`TOWER-NOT-READ` 前缀 ASCII + reason 中文段 cp936 直断（memory 返回串哨兵扫不到的教训——**测直接断言整行可编**） | |
| X5 CRLF/断行 | recycle 条目加键不改写侧纪律 | runs/schedule_runs 值域扩不改写侧纪律 | |
| X6 zoneinfo | 不触 | 不触 | |
| X7 信号 | 不引信号；requests 无隐式重试断言替代「重试库漂移」防线 | rc3 是退出码非信号；不新增 `os.kill` 路径 | |
| X8 OS 接线文档 | 不新增接线面 | README rc 表 + schedule 提醒（§3-5） | 真机验证归 #6 |
| X9 os.replace 原子写 | 无新状态文件 | 无新状态文件（attention 纯读聚合） | |

## 5. 完成门禁（每波）

- `PYTHONPATH=src python -m pytest -q`：**当日全量实跑基线只增不减**（计数见 README 徽章 / AGENTS §2，本文不留计数位）；W5 新增 ≥24（design_c12 §5）、W6 新增 ≥22（design_c9 §7）；**归因链**：终验以每波复跑实测数为唯一口径，分项尾差不强拆（一期终验 §1 教训）。
- `python -m compileall -q src`；`atlas contracts --validate` ok；`atlas doctor` rc0（两项均不动 doctor 面——如实：新增判据不进 doctor，避免版本/边界项的既有 rc 语义被稀释，红线 #12）。
- X 矩阵相关测全绿；两份设计的红线五查逐条自证复核；**兼容表（design_c12 §6 / design_c9 §6）逐行对号**——预期「先红后绿」项仅两处且均为**验收项非回归**：test_allure_analysis:513（OPERATIONAL 落地即绿）、§3-2 命中时的 summary 键测。
- 离线全绿 ≠ 可用：真机栏（两份 §9）进任务 #6，须用户授权后前台串行；本波不声称任何真机结论。

## 6. 禁改清单（全程）

设计阶段：只写 `docs/gaps/impl_pi/` 新文件（本三份），src/tests 不碰、不 commit/push、不跑真机。实施阶段：不改 `tests/conftest.py` 与 `test_xplat_sentinels.py` 本体；不动 `CONTROL_FLOW` 三元组、`validate_write`、`--approve`/审批门、`on_env_blocked` 词表、`_RULES` 关键词表；不引入任何新第三方运行时依赖（重试框架/幂等库全禁——一期 E2 口径：纯 stdlib 先例+治理取舍，不得把理由写成红线禁止）；不碰 `D:\jmx` 与 reports/ audit 文件；等待态**不回溯**改写任何历史 run 记录。

## 7. 交接口径（给 #3 评审的靶子清单）

1. design_c12：§2 真值表「attempted 未知→True」保守面是否过宽（demo_api 只读任务被误拦降级？）——设计答：只读分支先行短路 True，保守项仅命中写任务，行为面 = 现状 heal 门同等严格度；§3.2 `DeliveryUncertain` 继承 `RuntimeError` 的自环风险（降级闸在 `except (NotImplementedError, RuntimeError)` 块内 raise 该子类——不会被同块再捕获，实现期测钉传播路径）；§6 兼容表「若无条目 shape 全等测」的实测核数。
2. design_c9：§2 生产者白名单是否封闭（评审可加项须给真实触发场景+反 #7 论证）；§3 recorder 零改动继承断言的源码扫描式测是否可维护；§6 兼容表「先红后绿」两项的验收/回归定性；rc3 与 agent 现行为并表是否造成 CI 误读（README rc 语义行是缓解）。
3. 本表 §2：cli.py W5 禁入的前提（`atlas recycle` 原样 JSON dump 透 idem——磁盘已核 cli.py:722）；reporting.py 跨波两主串行且 W6 不回头动 W5 注释行。
4. 勘误追加方式沿用一期：评审后在两份 design 文末与本文末加「评审勘误注（p2_critique · 日期 · 约束性）」，不重写主体。

---

## 评审勘误注（p2_critique · 2026-11-06 · 约束性；详见 design-review-p2.md）

- **PLAN1**：门禁基数 1448→**1603 passed**（磁盘实测，gap-fill 波已入库）；lead 转述的 1454 亦过期。W5/W6 复跑数=前波实测+新增下限；flaky/supervisor 新测群纳入每波全量复跑口径（两份设计兼容表未列，评审 §3）。
- **PLAN2**：归属表补两行——`scheduling/state.py`（实测无 outcome 词表闸，dispatch 增 "waiting" **不需**动它；若 §schema 注释落点在此则随 W6 单主）；`plugins/demo/tasks.py`（仅 C12-A 取选项 (a) 时入 W5）。cli.py W5 禁入前提核实成立（_recycle 原样 dumps=cli.py:878，非 :722）。
- **PLAN3**：候选两条裁决——**dispatch rc130：不做本波**（子进程 130 真实形态未取证，归 #6 后独立评估）；**EnvBlocked reason 写侧：不做本波**（接通会破 W6 兼容钉「env_blocked 行逐字节=旧形态」，收益为负；存量读空面留档）。§3-2 summary 键补丁**不触发**（磁盘无顶层键集全等测）。
- 波次串行三理由、禁改清单、X1–X9 继承、§7 四靶子处置均通过评审；design_c12 单点 REJECT（C12-A，传输可观测性面），design_c9 PASS-with-conditions——放行以两份评审勘误注落进设计文本为准（已追加文末）。

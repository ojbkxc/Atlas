# Atlas pi-absorb 二期设计对抗评审（P2-W0 动工闸门）

> 评审人：p2_critique · 2026-11-06 · 对象：design-plan-p2 + design_c12 + design_c9
> 方法：逐条对照 AGENTS.md 16 Never / 磁盘实测核对（行号+形态双核）/ 既有 1603 测试涟漪扫描。
> 本文档结论即放行依据；§7「必改清单」为**约束性裁决**，与三份设计冲突时以本节为准。
> 行文与勘误方式沿用一期 design-review.md（含 §8 勘误注落地确认）。

## 0. 磁盘基线重置（先于一切结论的事实纠正）

**实测基线 = 1603 passed**（`PYTHONPATH=src python -m pytest -q`，58s，2026-11-06 本评审亲跑；git 工作区=一期 pi-absorb+gap-fill 波次未提交态，`a65d4a1` 之上）。
- 设计三份写「基线 1448」、lead 转述「现 1454」——**两个数都过期**。1448 是 gap-fill 波（DevOps+60/Allure+12/pytest 产出+22/flaky+30/缝+3）之前的口径，CLAUDE.md 已写 1603。
- 这不是改个数字的事：gap-fill 波**真实改变了本二期设计的物理前提**——engine.py 长出了步骤级 flaky 重试腿与 CaseWatchdog，cli.py 长出了 `--case-timeout` 子进程看护（rc 124 + runs.jsonl flow 级 `status:"timeout"`）。两份设计对此**一字未提**。
- 波门禁统一改口径：W5/W6 完成门禁=「复跑数 ≥ 前波实测 + 新增下限」，起点 **1603**（design-plan §5 必改，见 §7-PLAN1）。

### 0.1 行号漂移表（设计引用 → 磁盘实测；实施者按右列施工）

| 设计引用 | 磁盘实际 | 备注 |
|---|---|---|
| engine._perform 捕获降级块 :269-284 | **engine.py:401-422**（except :411，ctx append :420-421） | 内容仍与设计一致，仅漂移 |
| 「:271 supports_api 判据一字不改」 | :406-408 | **且设计伪码漏了前置 :404-405 `Channel.API and not supports_generic_route` 直返守卫**（见 §7-C12B） |
| execute 失败路径「:126 之后」「:130 CONTROL_FLOW 旁」 | :217 / :221 | heal 腿「:141」→ :278；healed 就地更正「:169-172」→ :306-309 |
| （未引用）步骤级 flaky 重试腿 | **:161-167 `_retry_eligible` + :236-275**（`record["retried"]` :269） | 二期两份设计的最大盲区，见 §7-C12C/C9B |
| （未引用）supervisor rc124 + close("timeout") | cli.py:800-843（timeout 行 :838） | run 轨 rc 表现含 124，见 §7-C9D |
| cli ApprovalRequired rc3「:434-437」 | **:454-457** | rc3 已在盘属实；但 **`atlas gen` 的 rc3 是人无关失败**（:1104/1107/1121），「统一 rc3=等人」跨子命令不成立 |
| cli recycle dump「:722」 | **:846-878**（json.dumps :878） | 「原样 dump 自动透 idem」实质核实成立 |
| config.validate_write「:198-204」 | :232-238 | 「零改动」承诺不受影响 |
| allure_report reason 读侧「:107」、分支「:106-112/:128-133」 | :120、:119-132 | 读无人写=属实（env_blocked 恒走回落） |
| tasks.py「:185-186 发出后 raise」「:146 注释约定」 | :186-187、:147-148 | 属实；**但见 §1-C12A：该形态恰不被设计机制拦截** |
| reporting.py:136 | :136 ✓ 精确 | 其内部引用 engine.py:133-147/:161 已 stale；且口径已三处失真，见 §7-C12C |
| abilities.py:99 / probes.py:79·157·181-209·187-193 / failures.py:6-14 / core.py:28（read_only 缺省 True ✓）/ dsl.py:811·814·825 / allure_analysis.py:20-21 / decision.py:94 / dispatch.py:115 / recorder.close:119 | **全部命中** | 这两批文件引用质量好 |
| test 引用：test_allure_analysis:513 ✓、:523-551 ✓、test_evidence_and_knowledge:342-391 ✓、test_recycle:111-120 ✓、test_privacy_drift「:147-148」✓、test_plugin_and_legacy:102-126/:230-273/:279-339 ✓ | 全部命中 | 兼容表逐行判定按此复核（见 §3） |

## 1. 总裁决

| 文档 | 裁决 | 阻塞项 |
|---|---|---|
| design_c12.md | **REJECT（单点）**——落实 C12-A 勘误即视 PASS（一期 C2 先例：事实前提不实，修文档不推翻架构） | **C12-A**：`attempted` 在**自定义 `_perform_api` 传输面不可观测**，设计自我宣称封堵的实锤形态（tasks.py:186 demo_api 发出后 NIE）在其自身机制下**放行**；且 §5.9 把「demo_api 形态」误标为「无 CallApi 能力」——真实运行时 CallApi 在场、seq 不动、attempted=False、写降级照跑。修法与裁决见 §7-C12A |
| design_c9.md | **PASS-with-conditions** | 无阻塞；C9-A…E 五条（磁盘新事实吸收+措辞），勘误钉文末 |
| design-plan-p2.md | **PASS-with-conditions** | PLAN1 门禁基数 1603、PLAN2 归属表两行补标、PLAN3 候选两条裁决落地（本文已判，不做不声称） |

**放行建议**：#4（impl_c12）**暂不动工**——C12-A 是设计级修订（改 §2 真值表行 3 依据+§3.2 attempted 取值式+§7 自证措辞，文档级半天工作量，含拍板选项 a/b）；lead 拍板后即放。#5（impl_c9）本就压在 #4 后，条件随行。

## 2. 红线审查（16 Never 逐条）

### 2.1 C9「新终态洗绿面」——四道反机制**全部成立**（磁盘构造级复核）
1. **文本面焊死**：`_RULES`/`_DATA_ABSENT`（failures.py:33-53）一字不加是**结构性**保证——WAITING 不在关键词可达集内，外部文本恒造不出 waiting ✓（E6 残留风险主体面已闭）。
2. **类型短路次序**：CONTROL_FLOW 第一短路（classify_failure :136-137）实测在位不动；AwaitingHuman 第二、互斥、次序成测 ✓。
3. **双留不洗绿**：allure_report :119-125 blocked/interrupted「skipped+label+message」先例逐字核实；waiting 同构=**同族第二实现非新开闸**，E6 判定成立 ✓；「缺 label→broken」兜底 :131-132 不动=旧消费器读新行 fail-loud，方向正确 ✓。
4. **不回溯**：无任何 reclassify 工具面，fixture 回放测在列 ✓。
- 不进 failed ✓（status=waiting 自成一值，summary Counter 天然容纳）；classify 单一真相不复制 ✓（OPERATIONAL 一处定义、recorder 经 classify_failure 继承、allure 消费 label 不回判）。
- **⚠ 但输入面封闭有一处新漏洞是磁盘长出来的**：`waits.is_recoverable`（waits:54-62）**委托 classify_failure=INTERACTION**——AwaitingHuman→WAITING 恰好免疫步骤重试（类型短路救的，运气好但要钉死）；而 **C12 的 DeliveryUncertain→INTERACTION 对「值得重试」为真**——现被 `_retry_eligible` 的 read_only 前置（:165-166）挡住（DeliveryUncertain 只在写出现），**这条耦合没有任何测**。必改 §7-C12E。

### 2.2 C12「把写当只读自动重试」通路——**部分不成立，见 C12-A**
- 已被机制真堵的面：① CallApi 在场且 seq 变（业务 Task 调 actor.api 后 raise RuntimeError——**这才是设计真正堵住的现实形态**）✓；② CallApi 缺席（纯 UI actor 的测试形态）fail-closed ✓；③ api 通道无兜底腿 ✓；④ heal 腿维持最严（can_auto_heal :70-75 实测，「不给 heal 新增探测重试通道」承诺审查通过 ✓）。
- **未被堵且自相矛盾的面（C12-A）**：写任务+自定义 `_perform_api`+非 CallApi 传输（demo_api 类，tasks.py:170-187 实锤）→seq 不动→按「未发出」放行→**双执行照旧**。设计 §基线把这形态立为「要堵的真口子」、§5.9 又把它标成「无 CallApi 能力」——同一形态两句话互相拆台，红线 #9 在此面仍是「注释约定无机制」。§2 真值表行 3 的依据「证明一个请求都没发出」在不可观测传输上**不成立**。
- #14 关系：reserve 先占零改动 ✓（probes:76-80 实测）；但「合成闭环」表述不实，见 C12-D。
- **知识写路径**：零新增 ✓——delivery.py 纯函数、idem 进的是运行时台账（recycle）非知识库、PRIVACY 行=红线 #8 事实回写义务。
- **依赖**：stdlib(hashlib/json) ✓；「不引重试库」磁盘核实：src 全仓无 `Retry/HTTPAdapter/max_retries`（本评审 grep=0 命中）——测钉建议保留但意义降级：真正防线是 `_retry_eligible` 的 read_only 闸（已在盘），§5.17 测不得写成「requests 无隐式重试⇒无第二执行」，见 C12-C。
- `--approve`/validate_write：确零触碰面 ✓（grep 全仓 validate_write 仅 config/engine/probes 既有三消费点）。

## 3. 既有测兼容逐文件判定（喂真数据、不放宽）

| 文件 | 判定 | 依据 |
|---|---|---|
| test_plugin_and_legacy:102-126 | 绿 | fake `TaskMetadata` 缺省 `read_only=True`（core.py:28 实测），走「读→放行」行，降级留痕键不动 |
| :230-273 heal 重试+降级 | 绿 | read_only=True + CallApi 缺席→attempted=True 但读短路在前 ✓ |
| :279-339 OSError/契约 | 绿·红线测 | 捕获集不变；ContractViolation=AssertionError、requests 异常=OSError 系，均不入 (NIE,RuntimeError) ✓ |
| **flaky/supervisor 新测群（gap-fill +30+，未列入兼容表）** | **需 W5/W6 复跑覆盖** | 步骤重试腿读原异常后 `exc=retry_exc` 再进诊断链——DeliveryUncertain/AwaitingHuman 与 `retried` 键的交互从未被两份设计评估；以每波全量复跑为唯一门禁即可兜住，条款补进 §6 完成门禁 |
| test_allure_analysis:513 遍历桶覆盖 | **先红后绿=验收项，定性成立** | 实测断言「非 unknown 枚举全被桶覆盖」——W6 同波 DEFAULT_BUCKETS←OPERATIONAL 后自绿 ✓；正是探测器用途 |
| :523-551 四桶分布 | 绿 | waiting→skipped 不进 failed/broken 桶（analyze 只计 failed/broken），样本集不含 waiting ✓ |
| test_evidence_and_knowledge:342-391 | 绿 | 关键词矩阵零改；`ApprovalRequired!=ENV_BLOCKED` 断言保留、追加 `!=WAITING` 为增广 ✓（喂真数据非放宽） |
| test_recycle:111-120 | 绿 | '?' 降级面只读 document，idem 是顶层可选键 ✓ |
| test_privacy_drift | 补丁后绿 | 实测该测**正向枚举** PRIVACY 字段（degraded_to_ui/dispatch/attachment）——W5 加两键 W6 加一词必须同波改 PRIVACY+本测，M11 在位 ✓ |
| test_state_reporting_cli / test_reporting_and_toolbox | 绿，**§3-2 协调补丁不触发** | 实测两处均为 `result["status"]==…`/`fail_classes==…` 分键全等（:38/:39/:412），无顶层键集全等断言；attention 新键不碰 summarize 即零涟漪 ✓（设计「若存在」的保留句可删，免生枝） |
| test_session_and_recorder | 绿 | close 末位默认参=C6 version 先例同款（recorder:73-74 实测形态一致） |
| test_scheduling_* rc 表 | 绿 | decision.py:94-101 `_POLICY` 实测无键集全等风险；dispatch rc 映射 :115 一支纯增 |

## 4. 归属表与双写检查

- **同波无同文件双主** ✓：W5{delivery/abilities/engine/failures/probes/creators/reporting 注释}∩W6{failures/engine/cli/dsl/recorder/reporting/allure×2/decision/dispatch+新测×2}=空（跨波 serial 已钉）。
- cli.py W5 禁入前提**实质成立**：`_recycle` 原样 `_json.dumps(records)`（:878 实测）idem 自然透传；行号 722→878 改引。
- **补标两行（PLAN2）**：① `src/atlas/scheduling/state.py`——若 outcome 词表注释/校验在场（实测 :363 只委托 failure_policy，**无词表闸**→dispatch 增 "waiting" **无需**动 state.py；但 §3 表若声称「schema 注释同步」落点在 state，实施期按磁盘定），W6 归属注记补一行防实现期即兴越表；② `src/atlas/plugins/demo/tasks.py`——仅当 C12-A 取选项 (a) 时入 W5 表。
- conftest.py/`test_xplat_sentinels.py` 本体/CONTROL_FLOW 三元组/`_RULES`/validate_write/--approve/on_env_blocked 禁改清单**逐条与磁盘现状无冲突** ✓。
- 归属表 §2 reporting.py 行：W5「仅注释行」现须扩为「注释重写为三源口径」（C12-C），仍属同文件同波单主，无双写。

## 5. 离线可钉性裁决

- C12 §5 19 组全可离线断言、≥24 下限可达；**追加必钉**：真值表加「自定义传输+写+发出后 raise→拦」反例测（C12-A 修复的回归钉）、`supports_generic_route` 守卫保形测（C12-B）、step_retry>0×写任务 DeliveryUncertain→perform 计数=1（C12-E）、quota=1 双触发（C12-D）、canonical 失败让位既有 #61 报错口径（C12-F）。
- C9 §7 15 组全可离线、≥22 可达；追加：step_retry>0×AwaitingHuman→perform 恰 1（C9-B）、supervisor 透传子 rc3 测（C9-D）、`status:"timeout"` 行在 run_attention/OPERATIONAL 下归类钉死（C9-E）。
- 「无真机才能验」项全部已在两份 §9 隔离出栏，离线栏未声称——审查通过；token 净化对抗（16hex≠≥32hex 判据）经 `_HEX_RUN/_UUID/_B64_RUN` 源码复核成立，`atlas_idem`/`idem` 不命中凭据词根亦复核 ✓。
- recorder 源码扫描式测（不含 "waiting" 字面量）：可维护性判**通过**——单行 assert、与 C6 version 参同形，先例在场。

## 6. 跨平台 X1–X9 继承核对

X1/X2/X3/X5/X6/X9 两份设计与磁盘形态一致，无冲突 ✓。
- **X4**：新串（`WAITING-HUMAN:`/`TOWER-NOT-READ`/投递中文消息/`atlas_idem`）ASCII 前缀+中文可 cp936 ✓；两设计都写「整行直断」正对 memory 返回串教训 ✓。
- **X7/X8 修正（C9-D）**：run 轨 rc 集现含 **124**（supervisor）与 **130**；`atlas gen` 已有 rc3=生成结构失败——「rc3=等人」**只能限定在 run/agent/dispatch-子进程面**；README rc 表按子命令分表，dispatch rc3→waiting 映射不受 gen 污染（子进程恒 `atlas run`）✓。Ctrl-C 语义仍 CONTROL_FLOW ✓。
- X8 候选两条（§7-PLAN3）：dispatch rc130→interrupted **判：不做本波**（子进程 130 真实形态未取得，#6 取证后再立）；EnvBlocked reason 写侧 **判：不做本波**（接它会破 W6 §7.8「env_blocked 行逐字节=旧形态」全等钉，兼容收益为负；留档 §10，后波独立评估）。

## 7. 必改清单（约束性；勘误注须按本节落三份文末）

- **C12-A（唯一阻塞）**：§2 真值表行 3 依据改写为「CallApi 通用 POST 面可证未发出」；`_perform_via_api` 自定义 `_perform_api` 腿（`supports_api and hasattr(task,"_perform_api")`——engine.py:426-427 现成分支判据，非猜）对写任务按 **attempted=True fail-closed**。修复二选一并写死：
  - **(a) 推荐**：显式能力声明（如 `api_leg_side_effect_free=True` 类标志，命名归实施）放行「读语义 API 腿+写元数据」任务（select_customer tasks.py:243-247 磁盘 read_only=**False**）；tasks.py 入 W5 归属表。
  - **(b)** 接受 select_customer 形态降级被拦的回归并在 §6/§7 如实声明（违反 #115「不翻正常路径」的风险由 lead 显式背书才可选）。
  - 同时纠正 §5.9 用例口径：「demo_api 传输（CallApi 在场但 seq 不动）」与「CallApi 缺席」是**两个**形态各配各测；§7 #9 自证措辞同步。
- **C12-B**：§3.2 伪码补 engine.py:404-405 `supports_generic_route` 前置守卫（否则 API 通道直返 Task 的既有语义被重写翻错）；healed 更正「:169-172」→:306-309，「:141」→:278。
- **C12-C**：协调补丁 1 重写——盘上重试源现为**三**（heal / **step_retry `retried` 键（engine:236-275，设计未评估的新腿）** / 有界降级），注释与行为测按三源钉；顺带如实上报 gap-fill 存量 dead-read：`export_runlog`（reporting:143）读 `step["retries"]` 而 engine 写 `retried`→flaky 重试导出恒 0——**非 W5 本体**，列新缺口给 lead，不得顺手混改。
- **C12-D**：§3.4「同 run 内任何自动再触发被配额拦」**不实**（默认 quota=3，二次 supply 占第二格放行）；改措辞为「现引擎无自动二次 supply 通路（测钉 supply 无重试调用面）+ quota 满才 EnvBlocked」；§5.14 测改 `create_quota=1`。
- **C12-E**：新增耦合钉：`_retry_eligible`/`is_recoverable` 对 `DeliveryUncertain` **类型级拒绝**（现仅靠 read_only 前置偶然挡住，classify→INTERACTION 语义相反）；配测 step_retry>0 时写任务 DeliveryUncertain → perform 恰 1 次。
- **C12-F**：token 计算时点与 creators 既有「where 不可 JSON 序列化点名」（#61）撞序——canonical 失败**让位**（idem 留空+原报错口径不变）或并入其点名，配测钉信息归属。
- **C9-A**：两份设计全部 engine/cli/config/allure_report 行号按 §0.1 表重标；基线口径 1448→**1603**。
- **C9-B**：§3 engine 行补「elif 置于 CONTROL_FLOW 后、**步骤重试块前**」并配 perform 计数测（现盘 :236 起即 retry 块，位置不钉死则 AwaitingHuman reason 文案含「timeout」类词可被 is_recoverable 判可重试）。
- **C9-C**：agent 轨/批量轨对 AwaitingHuman 的行为未声明（agent 循环泛捕获→记「执行失败」）——§10 增「agent 轨 waiting 口径待评估，本波不声称」；DeliveryUncertain 同条。
- **C9-D**：rc 语义措辞限定「run/agent 面 rc3=等人」；README rc 表按子命令（gen rc3 另义）；X 表 X7/X8 行同步。
- **C9-E**：run_attention/OPERATIONAL 对 flow 级 `status:"timeout"`（supervisor 新终态）归类显式声明——建议**不入家族**、attention 单列或不提，§10 留「timeout 家族归属由 lead 拍板」，禁 W6 无声扩面。
- **PLAN1/2/3**：门禁基数 1603+flaky/supervisor 新测群入复跑口径；state.py/tasks.py 两行归属注记；候选两条按 §6 判词落「不做本波」原文（后续波不得无声拾取）。

## 8. 交接口径

- impl_c12：C12-A 拍板（推荐 a）后 #4 放行；B–F 与正文同波落，勘误注已钉文末。
- impl_c9：条件 A–E 随行；「先红后绿」两定性维持（test_allure_analysis:513=验收项 ✓、§3-2 补丁确认不触发 → 该红灯数从兼容表预期里**删除**，磁盘已核）。
- 真机栏/候选两条：维持不做不声称。

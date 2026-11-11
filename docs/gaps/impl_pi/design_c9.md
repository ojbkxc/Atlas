# 设计 C9：waiting ≠ failed 显态（operational 终态，波次 W6）

> 上游：gap6 §3.2-C9（run_attention：批量遇人工步骤/审核塔积压→「等人」而非计失败；红线列 `✓·⚠·✓·✓·✓`，⚠=分类判据误标面）+ §9〔补充 S3〕〔勘误 E6〕。
> **E6 原文口径（判定已钉死，本设计照此执行）**：`allure_report.py:6-9` 已是「operational/环境终态→skipped + 独立 label + 不洗绿」的既有实现（env_blocked→skipped+`atlas.blocked=true`、interrupted→skipped+`atlas.interrupted=true`、unknown→broken，注释原文「不进 failed 也不静默绿」），新终态照此映射即**同族第二实现**，不构成红线 #7 的「把失败改 SKIP」；残留风险只在**分类判据误标**，与终态命名无关——§2 生产者白名单+输入侧封闭就是冲这一条去的。
> 磁盘基线（2026-11-06 实测）：`failures.py:6-14` 恰 8 类、无 waiting/deferred（gap6 §9.1 C9 行复核成立）；`scheduling/lease.py`/design_c1 §4 已有「不确定按**推迟**记录，与 waiting≠failed 同族」的引用——S2 细则当时悬空的「同族」由本设计显态化。
> 实施者：impl_c9（任务 #5；W6 单主）。基线=W5 落地后的 pytest 实测数（≥1448），不回退。

## 1. 终态与判据（failures.py，单一真相的家）

```python
class FailureClass(str, Enum):
    ...                                # 既有 8 类零重排零改名（枚举扩员：追加 WAITING = "waiting"）
    WAITING = "waiting"                # 显态第 9 类：操作未完成，因在等人——非缺陷、非中断

# 操作/环境终态家族成员的唯一判据（此前各消费点各写 2 个字符串，#92 改一漏一的温床）
OPERATIONAL = frozenset({FailureClass.ENV_BLOCKED.value,
                         FailureClass.INTERRUPTED.value,
                         FailureClass.WAITING.value})

def is_operational(value: object) -> bool:
    """run 状态/step fail_class 是否属「不进 failed 缺陷分布」家族。非 str/未知值 False（不猜）。"""

class AwaitingHuman(Exception):
    """等待人工的显式信号（与 EnvBlocked 同族位：操作终态，不是代码缺陷）。
    __init__ 强制非空消息：裸「waiting」无痕=新型静默（#70/#86 同族），必须点名在等什么。"""
    def __init__(self, reason: str):
        if not str(reason).strip():
            raise ValueError("AwaitingHuman 必须带非空原因（在等什么、需要谁）")
        super().__init__(reason)
```

## 2. classify 判据收敛（任务书「输入输出单一真相」的答案）

**输入侧——waiting 只从类型来，关键词永不产出：**
- `classify_failure` 增 isinstance 短路，次序钉死：`None→UNKNOWN` → `CONTROL_FLOW→INTERRUPTED`（原样第一，#108/#114 不动）→ **`AwaitingHuman→WAITING`** → `DeliveryUncertain→INTERACTION`（C12/W5 已在位）→ `classify_text(_classify_input)` 原链。
- `classify_text` 的 `_RULES`/`_DATA_ABSENT` **一字不加**：外部文本（SCS Allure 结果、历史日志）出现 "waiting/等待人工/approval required" 一律维持其原归类（unknown/session/contract…），**永不产出 waiting**——判据面把「把失败误标成等人」的最大入口焊死（E6 残留风险的主体面）。既有 8 类关键词矩阵（test_evidence_and_knowledge.py:342-391、test_allure_analysis.py:73-102）全量保持。
- 特判回归：`classify_failure(ApprovalRequired(...)) != WAITING`（agent 审批门是 rc3 决策信号，非 run 终态——见 §4 非触发清单；补断言入新测，既有 `!= ENV_BLOCKED` 断言不动）。

**输出侧——家族成员判据只此一处：**

| 消费点 | 现状 | C9 后 | 性质 |
|---|---|---|---|
| `allure_analysis.DEFAULT_BUCKETS["environment_blocked"]`（allure_analysis.py:21 内联 frozenset 两份字面量） | 硬编码 `{"env_blocked","interrupted"}` | `frozenset(OPERATIONAL)`（键名不变——桶键是对外口径，改名破消费者；语义=「操作/环境族桶」写进注释） | 收敛；既有 `test_default_buckets_cover_all_classes`（test_allure_analysis.py:513-521 遍历枚举）从「加枚举必红」变「自动纳入」——这是收益不是兼容负担 |
| `allure_report._status_from_run/_step_status`（:106-112/:128-133 逐类分支） | 逐类 if | 逐类 if **保留**（每类 label 键不同：blocked/interrupted/waiting），新增 waiting 分支；「是否非缺陷」的判断不在此层复制 | 不收敛的理由：分支各带独立 label 形态，收敛成循环反而藏键——用测 513 同款「遍历枚举必全有分支」防漏 |
| `reporting.run_attention`（新函数，§3 表 reporting 行） | 无 | 家族计数走 `is_operational` | 唯一新消费点 |
| `scheduling/decision._POLICY` | 调度域 outcome 词表（与 FailureClass 不同 namespace） | 加 `"waiting": "keep"` 一行 | 引用同族纪律，**不合并词表**（两套词汇合并会把调度 defer/lease 语义灌进业务分类，反向 #92）；design_c1 §3 表 10 的 keep/reset 分界原样 |
| engine/dsl 终态字符串字面量 | `"env_blocked"`/`"interrupted"` 裸写多处（dsl.py:811/814） | 本波只保证 waiting 新增走 `FailureClass.WAITING.value`（**判据/落盘取值单一出处**）；存量裸串不重构（红线 #12：无 bug 不搬家，测试钉「waiting 字面量 src 计数=枚举+映射处各 1」防散写） | 钉桩而非扩面 |

**生产者白名单（收敛「谁能写 waiting」——未来新增生产者必须进本附录 + 配测，否则评审拦）：**
1. 业务 Task 在 `perform()` 内显式 `raise AwaitingHuman("…")`（唯一 run 内终态生产者；如真实 UAT 验证码/人工审批步骤插件）。
2. `reporting.run_attention` 读面聚合（显示等人，不产终态）。
3. 无第三条。**显式非触发清单**（今日读成别的态的一律不动，防 scope 蔓延）：配额用完（probes.py:79 EnvBlocked）→ env_blocked；find_only 无数据 → env_blocked；生产写护栏拒绝（config.validate_write）→ env_blocked；dispatch 取值缺失 → env_blocked；`agent` 审批门 → rc3 决策留痕不进 runs.jsonl；legacy/pytest skip → 既有映射；CDP 冻场非交互 EOF 放行（engine.py:50-53）→ **不是**生产者（debug 形态零变化，红线 #10/§10 冻场纪律不动）。

## 3. 接线（文件级，签名级；recorder 仅 close 可选参，分类链零改动）

| 文件 | 改动 | 说明 |
|---|---|---|
| `failures.py` | §1 三件 + §2 classify_failure 短路 | W6 单主（W5 同文件的 DeliveryUncertain 已在其先） |
| `engine.py` | execute 失败路径（:130 CONTROL_FLOW 分支旁）**并列新增** elif：`isinstance(exc, AwaitingHuman)` → `results.append(StepResult(name, False, FailureClass.WAITING.value))`；**不走** heal/`evidence.classify`/坑点匹配/journal 草稿/session 取证；原样重抛 | 与 #114 面 B 同型：remedy 无从修复「等人」、给等待现场拍取证照是纯噪音；**差别一处**：CONTROL_FLOW 是宿主终止（dsl 记 interrupted 并继续上抛退出），AwaitingHuman 是业务终态（dsl 记 waiting 后由 CLI 转 rc3，进程收尾正常走 __exit__/recorder）。分支顺序钉测：先 CONTROL_FLOW 后 AwaitingHuman（两者互斥，顺序只钉语义声明） |
| `recorder.py` | **close() 末位可选参 `reason: str = ""`**——非空才写 run 记录顶层 `reason` 键（缺省不写=存量形态零涟漪，**C6 `version` 参同款先例** design_c6 §2.2）；`step()` 分类链**零改动** | run 级 reason 是红线 #7「message 双留」的落盘面（现 allure_report.py:107 已在读 `run.get("reason")`——写侧接上即复用，读侧零新逻辑）。step 的 `fail_class="waiting"` 由 `classify_failure` 单点继承自动产出（#112/#126 防线自动覆盖：定性崩回落 unknown 不假绿）；**测钉判据继承面**：step 端到端断言 + recorder 源码除 close 签名外不含 "waiting" 字面量 |
| `dsl.py` | run() 加 `except AwaitingHuman as exc:` 分支（置于 CONTROL_FLOW 之后、EnvBlocked 之前）：`status = FailureClass.WAITING.value`；`run_reason = exception_brief(exc)[:200]`（局部变量，仅本分支非空）；**raise 原样**；finally 的 `engine.recorder.close(status)` → `close(status, reason=run_reason)`——其余终态 reason 恒空串=**不写键**（env_blocked/failed/passed 行逐字节不变；env_blocked 侧读已有 `run.get("reason")` 的写侧本波不并接，留 §10） | 无 flow 级 skip 策略、无新 DSL 键（红线 #12：waiting 永远 stop——让 flow「跳过等人继续跑」就是静默绿通道，红线 #7 反面，**有意不提供**）；`on_env_blocked: skip` 语义不动 |
| `cli.py` | ① run 分支 `with DemoRuntime(...)` 外包 `except failures.AwaitingHuman as exc:` → stderr 一行 `WAITING-HUMAN: <brief[:200]>`（X4 ASCII 前缀）+ **rc 3**；② summary 分支调 `run_attention(summarize(...), ReviewQueue(config.reports_dir).stats())` 并入输出 `attention` 键；③ scheduling 接线见下行 | rc3 语义表统一：**rc 3 = 等人信号**（agent ApprovalRequired 现走同值，cli.py:434-437 已实测在盘）；浏览器/回收清单/留痕照常收尾（__exit__ 在 except 之前语义完好）。rc 0/1/2/130 既有口径零变化 |
| `scheduling/decision.py` | `_POLICY` 加 `"waiting": "keep"` | 连败不+1 也不清零（与 interrupted/defer 同族措辞写注释：CONTROL_FLOW #114 调度域第三实现——等人≠环境挂≠成功）；未知 outcome 仍点名 ValueError（既有测不动） |
| `scheduling/dispatch.py` | `_run_child`（:115-116）rc 映射加一支：`child_rc == 3 → outcome "waiting"`（**置于** rc==0/≠0 判定之内、failed 之前）；`counts_as_failure` 自动 False | `schedule_runs.jsonl` outcome 枚举行扩（schema 注释同步）；水位线**照常推进**（本轮认领过就是过了——与 defer_lease「不推进」区分：defer=没执行，waiting=执行到等人步骤；两语义不得混，测各钉一条） |
| `allure_report.py` | `_status_from_run`：`status=="waiting"` 或 `(failed ∧ fail_class=="waiting")` → `skipped` + `atlas.fail_class=waiting` + `atlas.waiting=true` + statusDetails.message **保留原文**；`_step_status` 同族一支 | 双留（label+message）照 :107-112 blocked/interrupted 先例逐字对齐；「缺 label → broken」兜底分支**不动**：旧版 allure 消费器读新 waiting 行 = broken（fail-loud 方向，非洗绿方向——写进兼容表） |
| `allure_analysis.py` | §2 表第一行（DEFAULT_BUCKETS←OPERATIONAL） | `parse_allure_result` label 直采逻辑不动→自产 waiting 往返可用；外部文本不产 waiting（输入侧焊死）；桶计数面 waiting→skipped 不进桶（既有四桶分布测数字不变） |
| `reporting.py` | 新纯函数（聚合唯一位）： | `def run_attention(summary: dict, tower_stats: dict | None = None) -> dict:` 返回 `{"waiting": int, "operational": int, "tower_pending": int | None, "notes": [str]}`——waiting 取 `summary["status"]["waiting"]`；operational=家族 status 求和（走 is_operational）；`tower_stats=None` → `notes:["TOWER-NOT-READ"]` **不冒充 0**（#64 诚实面：没读过就说没读过）；`summarize()` 本体零改动（status Counter 天然容纳新值，#115） |
| `healing.py` / `review.py` / `experience.py` | 零改动 | waiting 步骤**不进坑点草稿**（engine 分支已拦，journal 计数=0 专项测）——审核塔不积「等人」噪音；review --stats 的 pending 数由 run_attention 消费，生产者侧不动 |

## 4. 红线 #7 重点自证（「把失败改 SKIP」的四道反机制，逐条成测）

1. **判据不猜**：关键词表零扩（`classify_text("waiting for human")/("等待人工")/("approval required")` 逐一断言 **≠ WAITING**——落 unknown/contract 原族）；waiting 只能由 `AwaitingHuman` 类型短路产生（输入面封闭）。
2. **生产者封闭白名单**（§2）：非触发清单逐项钉——生产写护栏/配额/dispatch 缺值/find_only 缺数**保持 env_blocked 原归类**（各引既有测 + 新增「不得变 waiting」断言）；`ApprovalRequired != WAITING`。
3. **双留痕**：runs.jsonl（`fail_class="waiting"` + flow `status="waiting"` + 顶层 `reason` 净化摘要）、Allure `skipped + atlas.fail_class=waiting + atlas.waiting=true + statusDetails.message`（读 `run.get("reason")`，缺省回落 "awaiting human intervention"——与 :107 env_blocked 先例逐字同构）、rc3 stderr `WAITING-HUMAN:` 行——任一面缺失即测红；reason 中文段可 cp936 直断（X4 返回串纪律）。**不存在**「改记 passed/静默移除」的任何路径：failed 分类表 8→9 类原样可读出、summary `fail_classes` 照常含 `waiting` 计数（缺陷分布里如实可见，「等人」不许伪装成「没发生」）。
4. **不回溯**：waiting 只向前生效；历史 runs.jsonl 里读成 failed 的永远保持 failed（无任何 reclassify 工具；兼容表 §6 fixture 测钉）。

**残留风险声明（E6 原文口径）**：生产者误标（把真该 failed 的步骤 raise 成 AwaitingHuman）= 人/插件侧纪律，工具面由 `review-code` GREEN-* 家族与塔审兜——本设计不声称代码面消灭了该风险。

## 5. Schema 增量

```jsonc
// runs.jsonl：无新增字段，只有值域扩展 + waiting 分支才出现的可选顶层键
{"status": "waiting", "reason": "UAT 登录触发手机验证码，需人工完成", "steps": [{..., "fail_class": "waiting"}]}
// summary 输出新键
"attention": {"waiting": 2, "operational": 5, "tower_pending": 19, "notes": []}
```

## 6. 既有 8 类分类测·前后兼容表（零翻红口径）

| 既有测点 | 断言内容 | C9 后预期 | 判定 |
|---|---|---|---|
| test_allure_analysis.py:513-521 `test_default_buckets_cover_all_classes` | 遍历 FailureClass 全被桶覆盖 | **改 DEFAULT_BUCKETS 前必红**（新增枚举值）→ 同波 §2 行 1 落地后绿 | **验收项非回归**；本设计把它当「漏网消费点探测器」用——正是要它红 |
| test_allure_analysis.py:523-551 四桶分布、:553 custom buckets | 四键计数 | waiting→skipped 不进桶；键名不动 | 绿 |
| test_evidence_and_knowledge.py:342-391/356-360/371-375/382-391 | 8 类关键词矩阵、KI=interrupted、ApprovalRequired≠ENV_BLOCKED、DATA 优先断言 | 关键词表零改；短路次序未动；**追加**≠WAITING 一条（新增测，不删旧断言） | 绿 |
| test_allure_report.py:113-149/155-190 | env_blocked/interrupted→skipped+label、unknown/无类→broken、缺省兜底 | 分支纯增列 | 绿 |
| test_session_and_recorder.py（close/step 形态断言） | run 记录键集、step 分类链 | close 新增**末位默认参**不写键；分类链零改 | 绿（C6 version 参先例同款；waiting 行为新测增广） |
| test_heal_and_dsl_extensions.py（interrupted 终态、heal 门） | CONTROL_FLOW 语义 | 第一短路不动 | 绿 |
| test_scheduling_decision.py 表 10 | outcome 词表 | 键增广向后兼容；「未知 outcome 点名」测保留 | 绿 |
| test_scheduling_cli.py rc 口径（2/0/1/130） | rc 表 | 新增 0 答复内 waiting 子型；业务 run rc3 只进 dispatch 映射，schedule 自身 rc 表不动 | 绿 |
| test_state_reporting_cli / test_reporting_and_toolbox | summary schema | 顶层新键 attention；若存在精确键全等断言 → 实现期列**协调补丁**（断言形态增广，条数只增不减，见 design-plan-p2 §3） | 绿/补丁后绿 |
| test_privacy_drift.py 字段枚举（:147-148） | steps 实含字段与文档一致 | waiting 仅扩 fail_class **取值**非新字段；PRIVACY facts 行「失败类清单」补 waiting 一词（W6 归 impl_c9） | 补丁后绿 |
| test_xplat_sentinels.py | 目录级 X1/X4 扫描 | 新文件自动入网；新字符串 ASCII/中文可 cp936 | 绿（本体禁改） |
| 旧 runs.jsonl fixture 回放（含 8 类样本文件） | summary/allure 输出 | 无 waiting 行的文件逐字节同前 | 专项测钉 |

## 7. 测试清单（新 `tests/test_waiting_operational.py`；下限 **≥22**）

1. 枚举扩员：9 值、`WAITING=="waiting"`、既有 8 值/序零变（防重排）。2. OPERATIONAL 恰三员 + `is_operational` 表（含非 str/None/未知串 False）。
3. `AwaitingHuman("")/("  ")` ValueError 点名；合法消息原样可取。
4. classify：`AwaitingHuman→WAITING`、KI 先序仍 `INTERRUPTED`、`DeliveryUncertain` 仍 `INTERACTION`、`ApprovalRequired≠WAITING`。
5. classify_text 反制造表（§4.1 四样本）。
6. recorder 继承面：端到端 step `fail_class="waiting"` + recorder 源码无 "waiting" 字面量。
7. engine 分支：journal 草稿计数 0、capture 未调、StepResult(ok=False,"waiting")、原异常重抛；CONTROL_FLOW 分支逐字不动（对照测）。
8. dsl：flow status="waiting"、run 记录带 `reason` 净化摘要、其余终态 close 不写 reason 键（passed/env_blocked 行逐字节=旧形态 #115 全等断言）、`on_env_blocked:skip` 旧 flow 行为不变。
9. cli run：rc3 + stderr `WAITING-HUMAN:`（整行 cp936 直断，X4 返回串纪律）；passed/failed flow rc 不变。
10. allure run 级+step 级映射（label 双留+message）；未知状态仍 broken。
11. allure_analysis：自产 label waiting 往返；外部文本样本永不 waiting；桶覆盖测绿（OPERATIONAL 派生）。
12. reporting.run_attention 表：waiting 计数、operational 家族和、tower None→notes `TOWER-NOT-READ`、坏 shape 输入不抛（判据函数不得先死，#89 家族）。
13. scheduling：`failure_policy("waiting")=="keep"`；`counts_as_failure False`；dispatch fake rc3→outcome waiting 且水位线推进（对照 defer 不推进）；连败序列 timeout/waiting/timeout=2 不=3。
14. 兼容 fixture：无 waiting 旧 jsonl 回放逐字段等值（§6 末行）。
15. PRIVACY/CLAUDE facts 行含 waiting（文档哨兵扩列，同 #92 家族防漂移）。

**落地注（impl_c9 · W6 收口 · 实测 99 项，本波未 commit）**

- 第 6 项「recorder 源码无 "waiting" 字面量」实现为 **AST 常量级**扫描，不是原文子串：注释里解释这条判据时必然要提到
  这个词，按原文扫会把**文档纪律绑成判据**（写清理由的注释反而让测红）。判定口径=「不许出现取值为 `waiting` 的
  字符串常量」，这正是 #92 要防的东西（散写的取值，而不是散写的说明文字）。
- 第 7/9 项（engine 分支、cli rc3）依赖 W5 的 engine/cli 收口，实测两者共存：短路次序
  None→CONTROL_FLOW→**AwaitingHuman**→DeliveryUncertain→classify_text，另加**共现分类测**（投递不确定不借
  「等人」洗成非缺陷）。engine elif 位置按 C9-B 钉在 flaky 重试块之前，配 `perform` 恰 1 次的计数测。
- 第 4 项补 dispatch 侧：`tick` 对 outcome `waiting` **返回 rc 0**（lead 拍板），与 `failed`→1 双向成测；
  水位线推进 vs `defer_lease` 不推进各一条。
- 新增交叉面（原清单未列）：`harvest: true` 的 flow 停在等人处**不得**固化成 `flow_candidate`（钉「跑绿才固化」
  那道闸，防止把没跑完的序列当可复用资产进塔）。
- **测自身可信度加固**（本波自查抓到并修掉，写在这里防后续波次退回恒真）：
  ①「不该产生草稿 / 不该出现散写」这类**负向断言必须配正向对照**——原先读审核队列用了塔**读侧**文件名
  `pending_review.jsonl`，而 `ExperienceJournal.append_draft` 实际落 `experience_drafts.jsonl`，
  `not exists() or ...` 于是恒真，「waiting 不产坑点草稿」一度什么都没测；
  ②**扫描面自证**：src 全域字面量扫描加 `scanned > 50` + 「已知合法命中（`failures.py` 的枚举值本身）必须在
  命中表里」——`rglob` 落空时 `hits={}` 会让白名单断言照样通过，那是同一类假绿。
  与 W5 的扫描测共用一把尺，但**自证清单按各测的依赖面定，不互抄**（C12 的违规落点是 `abilities.py`，
  C9 是 `failures.py`）；期望零命中、盘上无合法锚点可指时，第三层改用**合成正样本**自证（W5 形态）。
- 计数：第 7 项下限 ≥22，实测 99 项；先红后绿只 `test_allure_analysis::test_default_buckets_cover_all_classes`
  一处（已做「退回旧桶定义必红」的实证），§6 的 summary 键协调补丁经磁盘核对**不触发**。

## 8. 跨平台条款（X1–X9，继承 design-plan §4）

X4：新 stderr/notes 全 ASCII 前缀（`WAITING-HUMAN:`/`TOWER-NOT-READ`）+ reason 中文段可 cp936（§7.9 直断）。X7：rc3 是**退出码**不是信号——不新增任何 `os.kill`/信号路径；Ctrl-C 语义仍走 CONTROL_FLOW。X5：runs.jsonl/schedule_runs 键增广不改写追加/断行纪律；X1/X2/X3/X6/X8/X9：不触（无新文件、无 subprocess 新用法、无 tz；X8 文档面：README rc 表补一行归 W6 事实回写）。

## 9. 真机不可观测行为与钉桩（先例同 C1 §9）

| 不可观测面 | 离线钉桩 | 真机栏（任务 #6 real_ops；本波不做不声称） |
|---|---|---|
| 「批量遇人工步骤」真实形态（UAT 验证码/OTP/人工审批插件 raise AwaitingHuman） | fake Task raise → §7.7-9 全链（step/flow/cli rc）机制性钉死 | SIT 免验证码（memory 实测）→ **现势不适用，如实留白**；UAT/PT 场景待有真实插件再验 |
| 审核塔积压下 `attention.tower_pending` 真值 | tmp 塔造 pending 行 → stats 断言 | **〔#6 交叉核对 2026-11-06 闭合〕** 本机同刻实测：`atlas summary` 的 `attention`=`{"waiting":0,"operational":8,"tower_pending":19,"notes":[]}`，`atlas review --stats` 的 pending=19 ——**两读面同值、notes 空（=塔真读过，不冒充 0）**，与离线钉桩口径一致；waiting 计数 0 与「无生产者」事实相符（不许读成「等人机制坏了」，而是当前没有等人工步骤）。 |
| OS cron 收 child rc3 的实况（任务计划器/cronie 透传退出码） | dispatch fake 映射 | **〔#6 真机侧回报 2026-11-06〕部分闭合，一项仍未取证**：① rc 透传异常**未出现**（派发子进程 failed→tick rc1、interrupted→rc130、defer/none→rc0 实测各对），`schedule_runs.jsonl` 行形态已实采，含 waiting 分支依赖的 `outcome`/`counts_as_failure` 双键——**回退方案（outcome 词表改读 status 而非 rc）确认无需启用**；② 本轮**没有产生 waiting 行**（现仓库无 `AwaitingHuman` 生产者，与 §2 白名单一致）；③ 「子进程 rc3 **且同刻到点超时**」的混合形态**未被观察到=仍未取证**，真机侧只遇到「自然退出」与「Ctrl-C 组播切（child_rc=null→interrupted）」两态。**这条不许在终验里记为通过**，留作后续有真实人工步骤插件时的观察项。<br>**〔后续波次更正 2026-11-06，lead 放行「都做」波〕** ③ 的定性写错了，按三态口径改为两格：<br>· **真机侧=结构性不可达**（不是「待取证」）——全仓 `raise AwaitingHuman` 零生产者，`src/atlas/plugins/` 只有 demo，无人工步骤 Task；这一格真机要等**外部/私有插件**，在本仓永远取不到证，继续挂「待取证」只会让后续波次反复复查一个不可能出现的现场。<br>· **语义侧=离线已钉**——该格依赖的两个前置条件本是两条独立事实（看门狗 fired 与任务自己抛错），可同时成立：`tests/test_waiting_operational.py::test_watchdog_timeout_recorded_alongside_winning_terminal`（waiting / interaction / unknown 三类终态各钉「`case_timeout` 与终态同时在」）+ 负向对照 `test_no_case_timeout_flag_when_watchdog_not_fired`（没触发就不许冒标记，防上一条恒真）。顺带修掉一个真实缺陷：`case_timeout` 旧实现**只在 CONTROL_FLOW 分支里补**，所以 waiting/失败一旦赢过 KI，超时证据就被赢的那个终态静默吞掉——现提到失败路径顶部，标记与终态正交。 |
| CDP 冻场真人在场语义 | 不产 waiting（§2 白名单封闭）→ 源码断言 cdp 块无 AwaitingHuman | debug 人工流按现行为，无需验 |

## 10. 未验证/开放问题（C16）

- **〔W6 落地定稿·评审 C9-E〕flow 级 `status:"timeout"` 的家族归属=挂起，本波不处置**：supervisor
  （`atlas run --case-timeout` → `recorder.close("timeout")`）造的这条新终态**不进** `failures.OPERATIONAL`，
  `reporting.run_attention` 的 waiting / operational 两桶**都不计它**，Allure 侧仍按未知状态走 `broken`
  （方向是 fail-loud，不是把它读成 skipped）。现状已在 `tests/test_waiting_operational.py` 三处钉死
  （`("timeout", False)` 判据表行、3 行 timeout 不进 operational、未知状态仍 broken）。
  **〔后续波次拍板落地 2026-11-06，lead 放行「都做」波〕归属定为「不入家族 + 单独列示」**：
  ① 仍**不进** `OPERATIONAL`——家族三条描述「人/环境把结果挡住了」，超时是「跑到一半被掐了」，
  混进去会把「看护配置太紧」这种工程问题读成环境阻塞；② 但给它**独立可见位**：
  `run_attention` 新增顶层键 `timeout`，非 0 时 notes 追加 `TIMEOUT-COUNT=N（看护掐表，查
  --case-timeout 与用例耗时）`；终态字面量收敛为 `failures.FLOW_STATUS_TIMEOUT` 单点
  （生产方 cli 收尾腿与消费方 reporting 计数同值，#92）；Allure 侧**不改**，仍 broken
  （fail-loud 方向不变，本波只解决「在 attention 块里彻底隐身」）。
  注意上表 #6 真机实采的 `attention` 值是**加键前**形态（`{"waiting":0,"operational":8,
  "tower_pending":19,"notes":[]}`），现形态多一个 `timeout` 键——历史读数以当轮记录为准，
  别拿新 schema 去回头要求旧台账。
  测：`test_run_attention_timeout_visible_but_not_operational`（正向：只给 timeout 时
  `timeout=4 / operational=0`；零超时时不许冒出 TIMEOUT-COUNT 噪音）。
  **归属由 lead 后续波次拍板**——W6 无声不扩面；将来若并入家族，必须同波改 OPERATIONAL +
  attention + allure 分支 + 兼容表，不许只改一处（#92）。
- **〔W6 落地定稿·评审 C9-C〕agent 轨/批量轨对 AwaitingHuman 不声称**：`AtlasAgent` 执行循环是泛捕获，
  遇到 `AwaitingHuman` 仍按既有形态记「执行失败」进 `reports/agent_decisions.jsonl`——**不产生 waiting
  终态、不转 rc3**，本波未接、未验证、不声称；waiting 口径待评估归后续独立波次（红线 #12 不给
  没真实需求的通道加投机功能）。`DeliveryUncertain`（W5）同条。
- **〔W6 落地定稿·评审 C9-D〕rc3 语义适用范围**：「3=等人」只在 `run` / `agent --execute`（审批门）/
  `dispatch` 子进程面成立；`atlas gen` 的 rc3 是结构自检失败（cli :1104/1107/1121），`atlas run` 另因
  supervisor 多出 rc **124**。README 退出码已按**子命令分表**。supervisor 对子进程 rc3 原样透传
  （`proc.wait`）已补测钉死。
- **〔W6 落地新增·lead 拍板〕调度 tick 的 rc 口径**：`waiting → 0`（与 defer/none/drop 同答复）。
  `rc 1` 在 OS cron/schtasks 侧的既有读法是「这条巡检挂了」，报 1 等于把等人**洗红**并可能触发
  AUTO-PAUSED——恰好停掉正需要人来看的任务；业务终态由 run 轨的 rc3 表达，调度层只答「这轮正常跑完」。
  双向测：rc3→outcome waiting→tick 0（水位线照常推进）/ rc1→failed→tick 1。
- dispatch 现把子进程 rc130（Ctrl-C 杀子进程）计 failed（非 0 即败）——属 CONTROL_FLOW 调度域的既有**漏网面**（design_c1 表 10 只钉了父侧 interrupted），C9 不顺手扩修（判据面独立、需先取证子进程退出码真实形态）；**评审已判：不做本波**（design-plan-p2 §3-4 原文维持），后续波次不得无声拾取。
  **〔已清偿 2026-11-06，lead 放行「都做」波〕** 取证不必等真机：`_spawn` 不加 `start_new_session`（§6.8）⇒ 人 Ctrl-C 时子进程同组同收，Windows 退出码即 130，这条由**源码机制 + 真机已实测到的 interrupted 形态**共同坐实，不需再造现场。落地：dispatch rc 表补 `124→aborted_time_limit`、`130→interrupted`（次序 0→3→124→130→其余，两类都必须在 failed 之前）；`tick` 返回集把 interrupted 归 0——台账对它 `counts_as_failure=false`，若 OS 侧仍返 1 就出现「同一轮在 schedule_runs 说不算失败、在计划器说这轮挂了」的自相矛盾；count 类 reason 改读 `child_rc`（旧实现一律写「timeout after Xmin」，rc=124 的台账会谎报成派发窗口超时）。测：`test_tick_child_rc_table_maps_interrupted_and_timeout` 三码值参数化（含 failed 双向对照，起点连败=4 以便 reason 经 AUTO-PAUSED 可读回——台账**没有** last_failure_reason 字段，不存在的字段不能当证据）。
- `EnvBlocked` 腿的 reason 写侧（allure_report 读了却历史上无人写，恒走 "environment blocked" 回落）——与 waiting 的 `close(reason=)` 通道同构可顺手接，但那是**存量缺陷修复非 C9 本体**；**评审已判：不做本波**——接通会破 W6 兼容钉「env_blocked 行逐字节=旧形态」，收益为负。waiting 侧已把该通道跑通（`close(status, reason=)` 末位可选参，非空才写键），存量读空面留档待独立评估。
  **〔已清偿 2026-11-06，lead 放行「都做」波〕** 拍板取「接通」：批量巡检里几十条同名
  `env_blocked` 行**分不清是缺价格表还是缺字典值**，根因当时只活在 `actor.ctx["_env_blocked"]`
  里、跑完即丢，处置动作（补数据 vs 找环境）只能回现场重看——收益不为负，兼容钉按正当路径更新。
  形态与 waiting 腿**逐字同构**（`exception_brief` 净化刀 + 截 200，别忘净化：接通写侧若不脱敏
  等于把 `Cookie=xxx` 明文写进一个新落盘字段，比不写更糟）；`on_env_blocked: skip` 腿同样写
  ——跳过型恰是批量巡检里最多、最需要知道为什么被跳的那一类。改的钉：
  `test_dsl_other_terminals_do_not_write_reason` → `test_dsl_env_blocked_writes_reason_passed_still_does_not`
  （同测内自带正向对照：跑 ok 任务仍**不许**有 reason，否则「env_blocked 有 reason」可能只是
  「什么都写 reason」的假象）+ 净化截断测 + skip 策略测。
- `waiting` 的恢复语义（人处置后怎么「继续」）= 重跑整条 flow（Atlas 声明式可重跑形态，gap6 C23 排除理由原文），**不做**断点续跑；若未来出需求，走新评估非本设计延伸。
- run_attention 目前只在 `atlas summary` 出；`atlas schedule status` 是否并显等人计数——展示面合并留待 W6 终验听用户口（红线 #12 不投机）。

---

## 评审勘误注（p2_critique · 2026-11-06 · 约束性；详见 design-review-p2.md，冲突以评审为准）

- **C9-A**：行号重标（基线=磁盘 gap-fill 波后形态，1603 passed 实测）：engine 失败路径 :217、CONTROL_FLOW 分支 :221；cli ApprovalRequired rc3=**454-457**；allure_report reason 读=**:120**、分支 :119-132。recorder.close :119 / dsl :811/814/825 / allure_analysis :20-21 / decision :94 / dispatch :115 经核**准确**。
- **C9-B**：engine elif 位置钉死：CONTROL_FLOW 之后、**步骤级 flaky 重试块（:236-275，设计盲区）之前**——否则 reason 文案含「timeout」类词的 AwaitingHuman 可被 `is_recoverable`（委托 classify=INTERACTION）放进重试腿；配测 step_retry>0 × AwaitingHuman → perform 恰 1 次。
- **C9-C**：agent 轨/批量轨对 AwaitingHuman 的行为未声明（agent 泛捕获→记「执行失败」）——§10 补「agent 轨 waiting 口径待评估，本波不声称」。
- **C9-D**：「rc3 统一等人」**跨子命令不成立**：磁盘 `atlas gen` rc3=结构/发射失败（cli:1104/1107/1121），run 轨还新增 rc **124**（--case-timeout supervisor）。措辞限定「run/agent/dispatch 子进程面」；README rc 表按子命令分表。supervisor 对子 rc3 原样透传（proc.wait）经核成立，补透传测。
- **C9-E**：supervisor 已造出 flow 级 `status:"timeout"`（close("timeout")，cli:838）新终态——OPERATIONAL 家族**不含**它（本波不扩面），run_attention 对 timeout 行不计 operational 单列声明；§10 留「timeout 家族归属由 lead 拍板」，禁 W6 无声处置。
- 兼容表修订：§6「§3-2 summary 键测」**判定不触发**（实测两测均为分键全等 test_state_reporting_cli:38/39、test_reporting_and_toolbox:412，无顶层键集全等）——预期红灯数从 2 减 1；flaky/supervisor 新测群（+30 项）未列兼容表，以每波全量复跑覆盖。
- 红线审查通过（构造级）：文本面焊死 ✓（_RULES 无 WAITING 可达）、类型短路次序 ✓、双留不洗绿 ✓（blocked/interrupted 先例 :119-125 核实）、不回溯 ✓、生产者封闭与 ApprovalRequired≠WAITING 特判 ✓、recorder 零改动继承面 ✓、`atlas.waiting` label 走既有通道无新写路径 ✓。候选两条评审已判**不做本波**（§10 两条原文维持）。

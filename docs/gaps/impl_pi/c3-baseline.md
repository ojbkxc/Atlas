# C3 基线与门禁交叉关系（迁移验收三件套交付，design_c3 §5/§6）

> owner: impl_migrate · 2026-11-06。纯离线交付：哨兵测 `tests/test_migration_sentinels.py`
> （12 规则+变异记录表）、闸门 `scripts/migration_gate.py` + `tests/test_migration_gate.py`。

## 1. 三张门的交叉关系（互补，离线绿不可互相顶替，更不可顶替真机）

| 门 | 量 | 不覆盖 |
|---|---|---|
| `atlas review-code --baseline`（已存在） | 源码断言密度/弱化差分 | 测试**失败集合**子集 |
| `scripts/migration_gate.py`（本项） | 失败集合 ⊆ 基线、静默空跑巡检点 | 断言是否被悄悄放宽（源码面） |
| 真机取证（纪律，非脚本） | fake/真实 schema 漂移（`No module named 'lib'`、`page_key` 教训） | —— |

三者关系：review-code 看「源码面怎么改」，migration_gate 看「结果面红没红新增」，
真机看「离线全绿也测不出的接线漂移」。任何一栏缺失不得宣布迁移成功（§6）。

## 2. 基线来源事实（gap6 §9 E4 勘误已落地）

- 磁盘**没有** `case-migration-mapping.csv`；实为 gitignore 的
  `docs/case-migration-mapping.md`——那是 30 条 TC→flow 的**映射清单**，
  **不是** pytest 失败集合基线，且哨兵不读它（CI 上不存在，design_c3 §10）。
- 失败集合基线是它之外的**新建产物**：`reports/migration/failures-baseline.json`
  （`python scripts/migration_gate.py --record-baseline` 生成；reports/ 已
  gitignore——测试名含内部用例号，敏感口径与映射表同级）。
- 首录基线的失败集合以磁盘实态为准（本波录时 0 失败）；「⊆」判据在空基线下
  即「不得引入任何新失败」——这恰是迁移窗口要的口径，不需要人为放宽。

## 3. 哨兵规则与锚点核定记录（2026-11-06 逐条 grep 磁盘）

12 条规则全部按盘钉死；与设计候选锚的差异（§10「锚点以盘为准」的核定结果）：

| 规则 | 设计候选 | 磁盘核定 |
|---|---|---|
| doc-stale-count-720 | 字面 `720` | README:409 的「720→772」是增幅**历史叙事**、合法；marker 收紧为当前值声称形态 `720 passed`/`tests-720`（现口径 1327） |
| legacy-seeding-not-reforked-flows | `test_data`/`assert_steps` 等 | tc01 flow **注释行**含 `extract_steps:`（源用例出处说明）——扫描按行剥 `#` 注释后判结构面（降敏方向：注释里的锚字样不算重定义） |
| scheduling-cron-impl-single | `croniter\|APScheduler` | cron_parse.py 的**论证注释**提及这两个词；marker 收紧为 import 形态（`import/from croniter`、`import/from apscheduler`），依据=「同一能力两份实现」防重定义（impl_sched C1 交接） |
| engine-no-shell-true | `shell=True` | src/atlas 磁盘 0 命中；`scripts/linux_acceptance.py:37` 原**预存违例**已**清偿**（argv 化，2026-11-06 #1 小修；scope 曾限 src/atlas，现全仓 0 命中，哨兵保留防回归） |
| ki-tuple-single-truth | 内联双元组 | engine/healing/recorder 磁盘 0 命中；failures.py 的三元组定义是单一真相**本体**，不在 forbidden scope |
| （C1 交接约束） | —— | scheduling/ 是**新内核不是迁移桥**，不进 resurrection scope；仅以依赖口径入 forbidden-marker |

## 4. 变异注入自检记录（手动一次性；全表在 `tests/test_migration_sentinels.py`）

- 纪律：每条规则故意 revert 锚点 → 对应哨兵测必须红 → 恢复。记录=
  `MUTATION_RECORDS` 表（rule/手法/期望红的测试/日期/by），**无记录=哨兵视同
  不存在**（M11），`test_mutation_records_complete` 元测试钉表格完整性，
  `test_injected_rule_goes_red` 在每次全门禁重放 tmp 副本注入（等效 revert，
  **不碰 W1/W2 未提交的工作区**——直接 revert 真文件会毁队友未提交改动）。
- 禁 mutmut/cosmic-ray：治理取舍=门禁可复现性（gap6 E2 同款），非红线字面禁。
- 失败处置纪律：哨兵红=「重放自有改造」不是删断言；resurrection 报
  「像被删不像被搬」先 `git log -S <锚> -- <scope>` 找去向再决定恢复。

## 5. 本波顺带拆掉的 review-code 预存红灯（team-lead W3 追加，判据上移）

- `atlas review-code tests/` 旧实现 `rglob("*")` 收 `__pycache__/*.pyc` + 严格
  utf-8 读 → 裸崩 UnicodeDecodeError（migration_gate 的 `--baseline` 同读链必踩）。
  修=目录扫描只收 .py/.yaml/.yml/.json + 读侧 `errors="replace"`（#131 纪律），
  显式点名文件不受扩展名过滤（坏字节也 replace 落 MAINT-006 不崩）。
- SAFE-001 豁免判据（唯一豁免面）：URL 形态且 host 为 **RFC 保留域**
  （example 家族/.test/.invalid/localhost/回环，urlsplit 归一小写）。**没有**
  目录级豁免通道；字面秘密赋值（password=/token=/api_key=/secret=）恒 critical
  ——C4 放进 tests/ 的字面凭据样本必须继续吃红（红线 #7 反馈环）。混合行
  （保留域+真实 host）保守判红。判据行为双向钉：`tests/test_review_code_readsafe.py`。

## 6. 真机耦合条款（§6 双栏结论模板——每次大迁移验收必填，缺一栏不得宣布成功）

| 栏 | 内容 | 本次（2026-11-06 波次 C） |
|---|---|---|
| 离线子集 | `python scripts/migration_gate.py` 的 GATE-OK/GATE-RED + collected 数 | 见 §7 实数 |
| 真机终态 | `atlas legacy --keyword` 分批回归（真机执行人 real_ops，2026-11-06 15:02–15:29，单人串行前台） | **已补**——见 §9。本轮实数 **5 passed / 11 skipped / 1 failed**（vs 历史基线 13/4）：偏差全部归因 SIT 主数据漂移（备件价格 SPMM-150、PT 字典 90DI 缺失）+ 已知税率 NPE（SPM-653），**非引擎/迁移缺陷、非代码回归**；未刷绿、未以重试掩盖，重试仅用于链路确认（0 次重跑抖动）。 |

## 7. 巡检检查点清单（gate 已内建，人审照单核对）

1. `collected 0 / 2 deselected`（rc0 或 rc5）→ `GATE-SKIP-EMPTY` rc1——静默空跑
   绝不绿（fork-sync 案例同款 SOP 检查点）。
2. 基线 JSON 坏/形态不对 → 点名「先重录基线」，不静默空比（静默空比=放行一切）。
3. 超时（默认 3600s）→ GATE-RED「挂死按失败处理」，不出子集结论。
4. 无基线 → rc2 指引 `--record-baseline`。
5. 全绿 → 明打「子集成立≠迁移成功，真机取证另跑」——绿灯自带提醒，防把
   离线判据读成迁移验收完成。
6. 哨兵 scope 指空 → 规则级红（「scope 无文件=哨兵失效」），不给静默假绿。

## 8. 已知债（不归零、不掩饰）

- `scripts/linux_acceptance.py:37` `shell=True`：~~预存违例，scope 外~~ **已清偿**（argv 化，2026-11-06 #1 小修——
  GATE 串已拆成逐条 subprocess.run，按原登记修法完成）。
- review-code 对 tests/ 的 SAFE-001 残留（非保留域 fixture host 如 `https://x/y`、
  字面凭据样本）：**判据正确、刺眼是对的**——要清零请 fixture 迁移到
  `*.example`/localhost 保留域，而不是放宽判据。

## 9. 真机回归记录（2026-11-06，任务 #6 第⑤项，最终树 1774 基线上执行，real_ops 单人串行前台）

命令形态：`PYTHONPATH=src python -m atlas legacy --keyword "<带下划线前缀的 -k 表达式>"`（五批，先 TC1 探路再下游，规避 `_tc1_ok` 联锁全 SKIP 的已知坑；`-k` 一律 `test_tcN_` 防子串误匹配）。

| 批次 | -k 表达式 | 结果实数 | 归因 |
|---|---|---|---|
| 1 | `test_tc1_` | **1 skipped**（198s） | 环境：备件 15827160012727 无价格主数据，建单被 **SPMM-150** 后端拦截（历史为 passed——SIT 价格表漂移） |
| 2 | `test_tc2_ or … or test_tc6_` | **5 skipped**（11s，未烧登录） | 联锁：`_tc1_ok` 缺失（前置依赖 TC1 未成功） |
| 3 | `test_tc7_ or test_tc8_ or test_tc9_` | **3 skipped**（3s） | 联锁：TC7 同上，TC8/9 依赖 `_tc7_ok` |
| 4 | `test_tc10_`；`test_tc11_ or test_tc12_ or test_tc13_` | 1 passed；**1 failed + 1 passed + 1 skipped**（171s + 351s） | TC10/12 passed；TC11 skipped=**SPM-653 税率 NPE**（已知产品缺陷，memory 在册）；**TC13 FAILED**：Payment Term 下拉可见选项只剩 `120DF`，目标 `90DI` 已从 SIT PT 字典消失——断言消息自证 UI 链路正常、主数据漂移；确定性数据问题，不适用抖动重试，如实记 failed |
| 5 | `test_tc14_ or … or test_tc17_` | **3 passed + 1 skipped**（530s） | TC15/16/17 passed；TC14 skipped=同款税率 NPE（SPM-653） |

- 合计：**5 passed / 11 skipped / 1 failed**（TC18 不在 17 条基线内，五批 -k 均未命中）。
- 基线对比：历史「13 passed / 4 skipped」的 4 个 skip 即 TC5 税率/TC11 税率/TC13 PT 锁死/TC15 税率（README 11-01 批次表）；本轮 TC2–9 共 8 个新增 skip 全部归因 **TC1 价格主数据漂移的联锁放大**（不吃登录、纯前置判定）。**TC13 形态从历史 skip 变为 failed**：SIT PT 字典已只剩 `120DF`（`90DI` 消失），断言不再被「PT 锁死」分支拦住而是真实撞墙——同一条主数据漂移链上的形态升级，非代码回归；三条根因（价格表/PT 字典/税率）都在 SIT 侧数据。
- 验收方动作建议（非本波改动）：SIT 补 备件价格/90DI PT/税率主数据后重跑批次 1–3 可回到 13/4 形态；TC13 的 `90DI→120DF` 处置属业务侧决定（改测试数据或等主数据回补），**不得由引擎侧改判**。
- **TC13 归因注记（team-lead 2026-11-06 拍板）**：failed 根因=SIT PT 字典中 `90DI` 缺失（可见选项仅剩 `120DF`）；主数据回补 `90DI` 后本条**应转绿**——复现判据=PT 字典恢复该键值，测试代码内不加任何绕行分支。基线口径自此为「形态基线」（当前 5/11/1 + 逐条归因表），非固定 13/4 数。

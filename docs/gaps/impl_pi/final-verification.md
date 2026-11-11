# 终验报告 #8 — pi-absorb 六项 P1 吸收终验（2026-11-06，pi_final_verifier）

> 全程只读核验 + 沙箱离线跑测（`D:/tmp/fv_*`），未 commit/push、未真机、未改写任何 audit 文件。
> 判定基线：任务书五条 + team-lead 追加四项（①override CLI 实测 ②SAFE-001 边界 ③xplat 哨兵本体禁改 ④review-code pycache/基线可比）+ tz 口径纠正。

## 1. 门禁四件套（实数）

| 门 | 实数 | 判定 |
|---|---|---|
| `pytest -q` | **1448 passed / 0 failed / 23.5s**（间隔复跑两次一致，第二次晚于队友最后一次文件改动 04:34） | ✅ 基线 772 只增不减 |
| `compileall src scripts examples` | exit 0 | ✅ |
| `atlas contracts --validate` | ok:true, total 7 (core1/high5/minimal1), problems [] | ✅ |
| `atlas doctor` | rc0；`version_triple`{atlas 0.1.0, knowledge sha1=3fe009108963 source=private files=5, tz_data ok=true Asia/Shanghai}；`boundary_ledger` present/11 regions/0 problems/`boundary_assets_consistent=true`/accepted_debts=[legacy-case-bridge] | ✅ |

**归因链（以最终复跑 1448 为唯一口径）**：772 → 1074（W1: C2/C4/C6+跨平台哨兵）→ 1327（W2: C1 调度核）→ **1448**（W3: C3+C5+收口）。W3 名义新增文件实测 C3=31+18+23=72、C5=31、xplat 自动入网 +6（146→152）=109，1327→1448 净增 121 的分项尾差（约 +12）落在 C1 波次 04:31–04:34 的收口改动上，无破坏性含义，但交接单口径请后续以复跑数为准。

## 2. 双平台静态扫描（逐命中判定）

工具面：`tests/test_xplat_sentinels.py` 152 passed（目录级递归 src+scripts，后续文件自动入网；X1 反斜杠/X4 试编 GBK 含返回串面）+ 人工 grep 交叉：

| 检查 | 命中 | 判定 |
|---|---|---|
| 反斜杠硬编码路径（src/scripts 字符串字面量） | 0 | ✅ |
| `shell=True` | 仅 `scripts/linux_acceptance.py:37`（**预存违例**，c3-baseline §8 已标价，scope 限 src/atlas，哨兵在案不放过） | ✅ 可接受（登记债） |
| 命令拼接未 argv 化 | subprocess 全部 list 形态（dispatch/migration_gate/legacy/scaffold/creators/selfcheck）；`guard_run_args` 拦 `--approve/--mode` 为**显式 raise**（不依赖 assert，C1-C） | ✅ |
| POSIX 信号假设 | `os.kill`/`signal.*`/WNOHANG 在实现代码 0 命中（仅 lease.py 注释里「禁用」论证）；超时全靠 `subprocess.run(timeout)` | ✅ |
| GBK 不安全中文 print | 哨兵全绿；`migration_gate.py` 的 U+2286 打印字面量被哨兵抓红→已 ASCII 化「<=」（哨兵对新文件生效的实证）；review.py 返回串 `<->` 由 test_c2_verification 直测 cp936 | ✅ |
| CRLF/换行假设 | X5 断行纪律（experience/state/migration_gate 尾字节检测+replace 回落），读侧统一 errors="replace"，无整文件换行符假设 | ✅ |
| 文件 IO 编码 | 新模块 open/read_text/write_text 全部显式 encoding（抽查命中两处均系续行误报） | ✅ |

## 3. 端到端离线证据抽查

| 项 | 实测 | 判定 |
|---|---|---|
| **C1** decide_slot 边界表逐条 | 11/11 过：manual→none、once 未到点/≤2min schedule/≤7d catch_up 只补最近一轮/>7d drop/due≤since none、整点 age=0 右闭、整点前 0.5s 不误触发、表10 计败（timeout/rc≠0 计败，interrupted/defer/drop keep 不计败）。注：cron「>7d drop」须稀疏表达式（年expr）方可构造，小时expr 下 age<1h 恒成立——首跑 1 条 MISMATCH 系我的驱动样本错误，非实现缺陷 | ✅ |
| **C2**（追加① CLI 实测） | 沙箱（.atlas-root+私有 kb）：缺三元组批准→rc2 点名四键指引；`--allow-unverified` 无 reason→rc2 拒；带 reason→rc0 且**双留痕**（decisions 行 `unverified_override+override_reason`、草稿行 `approved_despite_unverified`、stderr ASCII `UNVERIFIED-override:`）；VERIFIED 草稿批准不受扰、无 despite 键 | ✅ |
| **C4** 字面 token 被闸/占位符放行 | 真实 CLI 跑 import_jmeter：字面 UUID `b5360b2f-…` →`${ATLAS_HDR_1}`，值在 out.yaml/stdout/stderr **0 出现**；`${cust_code}` 照常 WARN 放行待人审；无 -o 拒打全文只出摘要 rc0 | ✅ |
| **C6** version_triple 出现 | doctor 输出含三元组（source=private+files=5+tz_data）；recorder/allure/summary 面由 test_versioning/test_allure_report 绿测钉 | ✅ |
| **C5** 坏账本不崩 | 坏字节/坏 JSON/顶层 list/反斜杠路径/accepted 无 log/同特异度重叠——load_ledger/validate_ledger 全部点名返回不抛；classify gate rc2；真账本 0 problems，resolve_region 特异度正确（dsl.py→absorbed、scheduling→engine-owned、未登记→None） | ✅ |
| **C3** gate rc 口径 | 无基线→rc2；录基线（1失败）→rc0；子集成立→rc0（自带「子集≠迁移成功，真机另跑」提示行）；**超集→rc1 NEW 逐条点名**；坏基线→rc2「先重录」；空跑录基线→rc1 GATE-SKIP-EMPTY | ✅ |
| **追加②** SAFE-001 豁免边界 | 判据只认 RFC 保留域（example 家族/.test/.invalid/localhost/回环，urlsplit 归一，反斜杠截断保守），混合行判红；**无任何目录级豁免通道**（代码注释+双向钉测 test_review_code_readsafe 23 测在位）；字面秘密赋值永不豁免 | ✅ |
| **追加④** review-code tests/（含 __pycache__） | 58 文件不崩，critical 55（SAFE-001 52 条=「刺眼是对的」）；`--baseline <HEAD 版 .py>` 差分链路可跑，baseline_notes 为空=断言密度无降（与 4 个改动测试文件 assert 计数 98→110/41→69/30→31/104→106 交叉一致） | ✅ |
| **追加③** xplat 哨兵本体 | mtime 03:08 早于全部 W3 文件（04:25–04:34），C3/C5 双方交接均自证未改本体、计数增长归因「自动入网」；证据受限：文件 untracked，git diff 不可核，采 mtime+证词+计数口径三对齐 | ✅（带限定） |
| **tz 口径** | 单点判定+两处消费成立：`cron_parse.load_tz()` 执行侧加载即抛、点名 `pip install tzdata` 不静默 UTC；`versioning.tz_data_status()` doctor 体检不抛。**未合并、不可再合并**（合并=doctor 在缺 tzdata 机器崩掉整条诊断，#64 反面） | ✅ |

## 4. 红线复核

- **零新运行时依赖**：pyproject/requirements 对 HEAD 零 diff；新模块（versioning/boundaries/credential_gate/scheduling/*、scripts 两 gate）import 全 stdlib（zoneinfo/hashlib/threading/fnmatch/secrets…），croniter/APScheduler 0 命中。✅
- **调度器永不带 --approve**：argv 固定 `[sys.executable,-m,atlas,run,flow,*run_args]`，`guard_run_args` 显式 raise（`python -O` 剥 assert 也拦得住）；`-k "approve or run_arg"` 13 测专项绿。✅
- **知识写路径零新增**：新模块 0 处调 save_reviewed/save_component_selector/save_overlay/_writable_root；boundaries `--to-tower` 仅复用 ExperienceJournal pending_review，且 boundary_review 在 approve 点名拒绝。✅
- **断言未放宽**：既有测试文件 diff +291/-5，删除行 0 条 assert（仅 fixture seed 行被 C2 补丁3 改写补三元组）；review-code 基线差分空 notes。✅
- **audit 文件未被动过**（mtime/行数记录为证，本终验未改写）：agent_decisions.jsonl 3行/11-01、runs.jsonl 51行/11-05、recycle_list.jsonl 6行/11-05、explore_* 11-04——本波未触碰。review_decisions.jsonl 原 14 行/11-04，仅因 §5-2 清塔由 team-lead 走 CLI reject **追加** 3 行至 17 行/11-06 04:53（追加留痕是正当决策动作，非改写既有行）。包内 `plugins/demo/knowledge/` 对 HEAD 零 diff。✅
- **仓库 reports/ 两处本波生成物**（非 audit）：`reports/migration/failures-baseline.json`（C3 基线，gitignore 设计位）、`reports/review-code-results/`（终验跑 review-code 时默认 out 落盘，可再生工件）。

## 5. 打回项（均已闭环，2026-11-06 04:55 复核实证）

1. **README.md:6 徽章陈旧**：`tests-1327%20passed` 与正文/AGENTS/CLAUDE 的 1448 不一致。→ 打回 **impl_bounds**（W3 文档单主）。**已修复并复核**：徽章现为 `tests-1448%20passed` + `alt="1448 passed"`；README 余下两处「1327」仅存于 `1074→1327→1448` 历史归因箭头行（刻意留底、非现状声明，与 doc-stale 哨兵口径一致）。✅ 闭环
2. **live 审核塔混入探索探针草稿 3 条**：`reports/experience_drafts.jsonl` 04:13:06 混入 `aaa919a656d3`(trace_step, url=file:///D:/tmp/probe.html)、`1f52715010fc`(selector_candidate, button:Probe Save)、`6aae7590ce3c`(selector_candidate, textbox:Probe Customer)。→ 路由 **team-lead**。**已清塔并复核**：三条全部 `atlas review --reject <id> --reason "终验#8：本地探索探针草稿（file:///D:/tmp/probe.html 系），非真实候选，清塔留痕"` 驳回；决策留痕 14→17 行（三行 reject 在位，mtime 04:53:43）、草稿行零删除、三条状态均为 rejected、无 pending 残留（stats total 36/pending 19）。观察注记：三条草稿在队列中**各存在两份重复行**（04:13 混入时即双份，两份均已 rejected，不影响清塔结论）——重复登记旁路了 `append_draft` 的同 id seen 合并，指向探索侧直写路径；**非本波引入缺陷**（explore.py 既有面），留此备案供后续波次留意。✅ 闭环

## 6. 遗留风险与未验证项（如实呈报）

- **OS 接线真机项（一组，待用户授权，不阻塞离线终验）**：①Windows `schtasks /TR` 引号语法冒烟；②心跳线程+subprocess.run 阻塞下控制台 Ctrl-C 竞态收尾复核；③真实 crontab/schtasks 起进程的 env 继承链（ATLAS_KNOWLEDGE_DIR 等，现仅 base 注入测模拟）；④DST 半小时区 fold 语义实证。C3 真机双栏模板真机栏空缺（纯离线交付如实标）。
- C6「上周能跑今天炸」真实案例回放未做（验收动作非已验证）；C5 Jaccard 0.8 真实重构误报率未测、账本 by 身份不校验、merge 前分类无 git 钩子强制（红线 #15 不扩面，流程义务）；C3 已知债 linux_acceptance.py:37 shell=True 与 tests/ 52 条 SAFE-001 刺眼残留（清零姿势=保留域化，非放宽判据）。
- 归因尾差 +12 分项口径见 §1；xplat 本体证据受限见 §3 追加③。

## 7. 终验结论

**六项 P1（C1–C6）实施验收：通过（离线口径）**。两个打回项均已于当日闭环复核（徽章 1 行修 + 清塔 reject 留痕 14→17 行实证）；余 OS 接线真机项待用户授权（§6）。真机/生产侧结论在本波不成立也不声称。

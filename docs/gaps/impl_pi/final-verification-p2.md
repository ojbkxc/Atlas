# 终验报告 P2 #7 — pi-absorb W5/W6（C12/C9）+ 小修 + 真机五项终验（2026-11-06，p2_final_verifier）

> 全程只读核验 + 沙箱离线实跑（`D:/tmp/fv2_p2/*`），未 commit/push、未真机写操作、未改写任何 audit 文件。
> 判定基线：任务书七条 + lead 追加口径（PYTHONIOENCODING 门禁形态、恒真断言专项、healing 核法、真机栏勘误）。

## 1. 门禁四件套（实数）

| 门 | 实数 | 判定 |
|---|---|---|
| `PYTHONIOENCODING=utf-8 PYTHONPATH=src python -m pytest -q` | **1777 passed / 0 failed**（两次复跑 50.96s / 53.76s 一致，第二次晚于全部队友最后一次改动） | ✅ 基线只增不减：1448→1603（gap-fill 落库线，非本波）→**1777**（W5 48 + W6 99 + 小修 7 + 存量文件内追加） |
| `compileall -q src scripts examples` | rc0 | ✅ |
| `atlas contracts --validate` | ok:true, total 7（core1/high5/minimal1）, problems [] , rc0 | ✅ |
| `atlas doctor` | rc0；version_triple{atlas 0.1.0, knowledge sha1=6ef3927b4b96 private files=5, tz ok}；boundary_ledger 11 regions / 0 problems / accepted_debts=[legacy-case-bridge] | ✅ |
| X 哨兵 `test_xplat_sentinels.py` | **158 collected / 158 passed**（本体对 HEAD 零 diff，新文件自动入网 152→158） | ✅ |

## 2. C12 写不盲重试（W5）

- **判据唯一真相**：`delivery.safe_to_auto_reexecute` 定义 delivery.py:70，全仓 grep 唯一消费方=engine.py:469（abilities.py:52 仅注释）。test_delivery_retry 48 项、privacy_drift 16 项全绿。
- **写路径零自动重试通路（实测）**：三条通路逐一核盘——①步骤级 flaky 重试 engine:165 `read_only` 前置硬闸；②heal healing.py:72 read_only∧非API 双门；③auto 降级 engine:457-471 attempted 三分支 fail-closed（自定义腿/无插桩面恒按已发出计），不放行即抛 `DeliveryUncertain` 不重跑 UI。定向 7 测（engine/降级/拒绝组）过。
- **classify 短路次序落位**：CONTROL_FLOW→AwaitingHuman(:194)→DeliveryUncertain(:200→INTERACTION)→classify_text，`_RULES` 一字未加；step 记录 `delivery_uncertain` 补键 :259 原位。
- **只读行为不变**：test_plugin_and_legacy/allure_analysis/state_reporting_cli 定向 96 过；#108/#14 存量钉（healing 控制流、probes/creators/recycle 先占语义）定向 76 过；全量 0 failed 为终判——**存量零翻红**。
- **healing 核法（叠态口径）**：diff 内 delivery/uncertain/attempted/idem 语义 grep=0（+15 行属一期 C2 evidence 字段层）；waits.py diff 零 W5/W6 语义（属 #133）；conftest/xplat 本体 diff 空。
- **§9 范围勘误采纳**：三项真机栏（502 窗口/requests 真机复测/idem 追单）**均未做未声称**；②非缺口（离线四层自证在位：合成正样本:558 + `len(scanned)>50`:566 + 依赖面 + 全域零命中），①③待用户授权。

## 3. C9 waiting 显态（W6）

- **全链沙箱实跑**（非引用测转述）：FunctionalTask raise AwaitingHuman → `FlowCompiler.run` → runs.jsonl 行 `status="waiting"` + 顶层 `reason` 原文 + step `fail_class="waiting", ok=false`；`atlas summary`：`status.waiting=1`、**`fail_classes.waiting=1` 照常可见（不伪装没发生）**、`attention={waiting:1, operational:1, tower_pending:19, notes:[]}`（19=`review --stats` 同值，塔真读过）；`atlas allure`：result `skipped` + `atlas.fail_class=waiting` + `atlas.waiting=true` + `statusDetails.message` **逐字节=reason 原文**（断言 True）——label+message 双留、不可读成洗绿 ✅。
- rc3/`WAITING-HUMAN:` 行/supervisor 透传 3 项定向过；exit code 按子命令分表（README 443-451，gen rc3 另义不并表）；dispatch waiting→tick 0 + decision keep + 水位线推进定向 2 过；真机交叉核对 attention={0,8,19} 与 #6 台账一致。
- **红线 #7 四道反机制**抽查成测在位：判据不猜（6 条外部文本反制造表全 ≠WAITING）、生产者封闭（非触发清单逐项）、双留不洗绿（§上）、不回溯（存量 fixture 回放等值 + 旧行无 atlas.waiting label，定向 2 过）。
- **恒真断言专项（lead 追加）**：三处修复全在位——①队列文件名已改 `experience_drafts.jsonl`（4 命中）+ 双正向对照（ordinary_failure 产草稿 / harvest 跑绿产 flow_candidate）；②C12 扫描 `len(scanned)>50` + 合成 positive.py；③C9 AST 扫描自证 `"failures.py" in hits`。99 项全绿。

## 4. 小修（#1）

- append_draft 跨决定态幂等（同 id 命中不追加第二条、决定态不回翻）+ reject/approve×3 共 4 处 `_is_pending` 过滤落位，新增 7 测（recompile 探针行/reregister 合并/reject-only/3×approve-only）定向 6 项复跑过。
- linux_acceptance.py argv 化（GATE_STEPS 列表 + subprocess.run(argv)，&& 短路/标记打印语义对齐）；**全仓 shell=True 代码零命中**；7 项引用测过。
- **清塔口径（lead 裁 B）**：清塔即终态；时间线以 review_decisions.jsonl 为权威（11-04 approve_selector ×2 → 11-06 reject 双留痕完整）；04:53 驳回系对合成探针的纠正非误伤；probe_page 知识侧缺失=11-04 私有库重写历史事实，无需补——本报告不写「误伤已恢复」。

## 5. 真机项（#6）

- **schtasks 独立实查**：`/Query` 全表 318 行、atlas 命中 0——注册残留已清 ✅。
- c3-baseline.md §6 真机栏已填（5 passed/11 skipped/1 failed，SPMM-150/90DI/SPM-653 三归因、TC13 形态基线 lead 拍板入文）；README 11-06 复跑行 + CLAUDE FAQ 三方数字一致 ✅。
- README W2①②③⑥ 边界段与 real_ops 台账逐条对合：/TR 256 上限取证、Last Task Result 取证纪律、mintty 无真 console 环境事实、aborted 未复现、DST fold 同槽不双跑/缺口 catch_up/语义偏差记档、env 三态 7887bc61f421 ✅。
- **不得记通过项**：「子进程 rc3×同刻到点超时」混合形态真机未取证（design_c9 §9 明写）；waiting 真机形态无生产者如实留白。

## 6. 红线复核

- 知识写路径**本波零新增**：save_task_entry 调用属 gap-fill 落库线叠态（非 W5/W6 diff 语义）；W5/W6 改动文件 grep 无新 save_*/_writable_root 通路。
- 零新运行时依赖：pyproject/requirements 对 HEAD 零 diff。
- 断言零放宽：tests/ diff 删除行 **0 条 assert**（7 删除行均 import/fixture 种子），新增 assert 行 +136；新文件 26 个（本波新增 test_delivery_retry/test_waiting_operational）。
- audit jsonl 未被动过：review_decisions 17 行/`review_decisions mtime 04:53`、experience_drafts 36 行/04:53、agent_decisions 3 行/11-01、runs 51 行/11-05、recycle_list 6 行/11-05——W5/W6/#1/#6 全程行数与 mtime 未变（04:53 为上一轮终验清塔正当留痕之后）。`reports/runs.jsonl.bak` 系预存备份件，非审计行。

## 7. 打回项（文档数字收口——按 AGENTS:50-51 分工归 lead）

1. **README.md:6 徽章仍 `tests-1448%20passed`**（正文 498 已 1777）——上一轮同类缺陷复发，请收口为 1777。
2. **AGENTS.md:46「当前基线：1603 passed」**措辞过期（下文链已写 1777 收口）——建议基线句改终数。
3. **CLAUDE.md:190 FAQ「离线验证 1603 passed」**过期——应 1777（同段真机 5/11/1 行已就位）。
4. **shell=True 预存违例登记过时**（债已由 #1 清偿）：c3-baseline.md:37/:84、test_migration_sentinels.py docstring :22/:129 四处标注应改「已清偿（argv 化，2026-11-06 #1）」——一行改动，归 lead 或 small_fix。
5. C12/C9 事实入模块表已核**为真**（README 37/48/306/432-441、AGENTS 87、CLAUDE delivery 行+C9 段、PRIVACY 20/24），无第 5 打回。

## 8. 遗留登记项（不夹带，如实呈报）

- C12：§9 三项真机待授权；**C12-E**（DeliveryUncertain 偶然被 read_only 前置挡住，是否升判据级拒绝）归 lead；`step["retries"]`/export_runlog dead-read 独立缺口。
- C9：`status:"timeout"` 家族归属、agent/批量轨 AwaitingHuman、rc130/`env_blocked` reason 写侧、waiting 真机形态 + rc3×timeout 混合取证——均「不做本波」原文维持。
- 真机：TC13 处置（改数据 vs 等主数据回补）业务侧拍板；SIT 价格主数据若长期缺失，17 条基线可复现性需在验收口径显式降级。
- 流程事故记录：impl_c9 曾以过时缓存覆盖 impl_c12 在途 hunk（无提交代码损失，重落互核）；impl_c12「healing diff 应为空」参照口径误述已当轮更正（正确核法=叠态下「diff 内零本波语义」）。

## 9. 终验结论

**W5（C12）/ W6（C9）/ 小修（#1）/ 真机五项（#6）：通过（离线口径）**。门禁 1777/0、四件套 rc0、X 哨兵 158；判据唯一性、写不盲重试、waiting 全链双留不洗绿、幂等/argv 化、真机归档一致性均经独立实跑/实查复核。五个文档收口打回项（§7）为 lead 权限的 1 行级修正，不构成代码缺陷；遗留项按 §8 不声称、不夹带。真机/生产侧边界结论一律不在本判内成立。

# Atlas pi-absorb 设计对抗评审（W0 动工闸门）

> 评审人：pi_critique · 2026-11-06 · 对象：design-plan + design_c1/c2/c3/c4/c5/c6（745 行）
> 方法：逐条对照 AGENTS.md 16 Never / gap6 §9 勘误（E1–E9/S1–S2 已读原文）/ 磁盘行号实测 / 既有 772 测试涟漪扫描。
> 本文档结论即放行依据；§6「归属表改判」为**约束性裁决**，与 design-plan §2 冲突时以本节为准。

## 1. 总裁决

| 文档 | 裁决 | 阻塞项 |
|---|---|---|
| design-plan.md | **PASS-with-conditions** | 归属表 5 处漏标（§6 已改判，不阻塞动工）；X4 扫描纪律 1 条澄清 |
| design_c4.md | **PASS-with-conditions** | C4-A/C4-B 两条**必改**（证据完整性 + 定性等值），其余可边做边落 |
| design_c2.md | **REJECT（单点）** | 唯一必改：存量测试涟漪未申报——与「既有文件零触碰」自相矛盾，且波门禁 772 必破。修法见 §3.2，勘误已钉到文末；**该条落实后本评审即视 C2 为 PASS，lead 可直接放行 #4** |
| design_c1.md | **PASS-with-conditions** | C1-A crontab 示例自相矛盾（必修文案），其余 5 条为边界钉桩 |
| design_c6.md | **PASS** | 无必改；1 条备注 |
| design_c3.md | **PASS-with-conditions** | C3-A 空 scope 哨兵失效、C3-B gate 无超时 |
| design_c5.md | **PASS-with-conditions** | C5-A 判据跨目录 import 形态（W3 前必修） |

**放行建议**：#3（C4+C6）按条件即刻可动；#4（C2）按 §3.2 修订落实后动（修订是文档级 10 分钟工作量，不动设计主体）；#5/#6/#7 随波次。

## 2. 跨平台矩阵 X1–X9 逐条核

| # | 结论 | 攻击点与裁决 |
|---|---|---|
| X1 | ✓ 成立 | pathlib + as_posix 口径完整；C6 两平台同指纹专项测已钉（pi_design 重点①④自查通过） |
| X2 | ✓ 成立 | C1/C3 argv 全列表；C5 零 subprocess（git 由人跑）是正确收缩 |
| X3 | ⚠ 部分成立 | 「创建即关句柄、删败→推迟、回读 token 双检」三条都对。**漏钉：NFS/同步盘（OneDrive/坚果云）上 O_EXCL 语义不可靠**（NFSv2 非原子、占位符文件可致双机各建各的锁）→ C1-D 条款：README/`schedule add` 文案标注「调度目录须本地文件系统，同步盘/NFS 共享不在支持范围」；§10 未验证项补一条。Windows 句柄/杀软面设计已覆盖 |
| X4 | ✓ 方法对、清单松 | 实测 src+scripts 含非 GBK 字符的 print 仅 `import_jmeter.py` 两处（:181 `⚠` 真崩点、:188 `→` **GBK 可编不崩**）。设计测法「encode("gbk") 试编」为准即无误报；文首禁字符清单里的 `→ █` 实际 GBK 可编，仅属风格禁止项，不得当红灯依据（W1 顺手把 :188 改 `->` 即可，不是必须）。**全量目录级扫描成立的前提**：sentinel 扫目录不扫文件清单，后续波次新文件自动入网、无需改 sentinel 文件（§6-D 钉归属） |
| X5 | ✓ 成立 | 尾字节断行判据 experience.py:71-77 实证存在；读侧 splitlines 纪律正确 |
| X6 | ✓ 成立 | Windows 无 tzdb→点名 tzdata、不静默回落 UTC，对 |
| X7 | ✓ 成立（重点复核项） | 「不自己发信号」统一 subprocess.run(timeout) 对；`os.kill(pid,0)` Windows 非探活（memory 实证过）→ 设计恒 None+推迟语义，**方向是停摆不是双跑**，与 gap6 S2「TTL 权威」一致，判据没写反；tasklist 备选 argv 化、只在 Linux 不需要、Windows 分支留 C1b，成立 |
| X8 | ⚠ 示例必修 | 见 C1-A：expr `*/30 1-8 * * *` 打出的 crontab 行却是 `*/30 * * * *`（丢了 1-8 时段）——两种语义混在一行示例里。不自动注册 OS 任务、Ask-first 边界本体**正确**（不违 §4） |
| X9 | ✓ 成立 | replace 重试+整轮放弃点名，无半写 |

**「只在 Windows 成立」的设计：无。** 「只在 Linux 成立」的隐含假设：X3 同步盘（上述补条款）、X8 crontab tz 字段（cronie 不解析 task 的 Europe/Amsterdam——tick 按**本机墙钟**，跨 tz 机 catch-up 只会迟到不会早发，语义可接受，但 README 必须明写这句，防止用户当「按时区精确触发」用）。

## 3. 红线冲突审查（16 Never 逐条过）

- **#2/#13/#14 生产写与配额**：C1 dispatch 复用 engine 既有 validate_write 双层护栏 ✓；调度只跑 run 轨不碰 agent 风险阈 ✓。
- **#7 不放宽断言**：C4 净化「只擦秘密不擦失败词」的方向承诺成立，但等值性哨兵**不足够**（pi_design 重点③）——见 C4-B。C2 闸只加格式约束不碰判定 ✓。C3 gate 禁跳测试/白名单直接不给 ✓。
- **#8 取证完整性**：截图面零触碰 ✓；但 `sanitize_url` 整段抹 query 把**业务诊断参数（docNo/page/ou）连同 fragment 一起销毁**，与 #85「不得无痕销毁追责证据」相抵——见 C4-A。**这条是本评审最实的红线冲突，必须修**。
- **#9/#10 无审核知识写回**：C2 闸不新增写路径、override 走人审显式决定+留痕，符合 §7 拍板「阻断晋级、人可 override」；**批量 --apply 有意无 override 通道=不留自动旁路**，正是 C1 反模式（自动写旁路）的防线 ✓。**结论：C2 不成死锁**（engine 来源草稿经补丁 1 自带三元组可批；explore/healing 来源缺三元组时人可 override——死锁判定不成立，friction 是拍板本意）。C1 **永不 --approve**：argv 组装+`add --run-arg --approve` 当场拒+专项测，三道都钉了，旁路关死 ✓（pi_design 重点①协调时序本身也成立：补丁 1 不依赖 C2 代码、补丁 2 缺省=不允许、不阻塞 #3）。
- **#11 引擎不含业务字面量**：C4 名单是通用 HTTP 凭据词表 ✓；C1 台账 run_args 是用户数据非引擎字面量 ✓；C5 账本入 git 全是路径/决策 ✓；C6 指纹纯数据 ✓。
- **#15 不伪造跨平台**：各设计 §10 把真机项列成「待用户按 SOP/未验证」，无一处声称已验 ✓。依赖口径（E2）C1 §9/C3 §9 **两处都写对了「纯 stdlib 先例+治理取舍，不是红线字面禁」**，没有写反 ✓。croniter/mutmut/psutil/APScheduler 零引入 ✓。

## 4. 逐份条款

### 4.1 C4（PASS-with-conditions）
- **C4-A（必改）**：`sanitize_url` 语义从「query/fragment 整段 `<redacted>`」改为「**query 内逐键命中才 redact，非敏感键值保留**（fragment 整抹可保留）」；runtime.py:175 与文本净化用同一把逐键刀。理由：整段抹 query 销毁业务取证参数（#85 追责证据面），且把 classify 漂移面从「敏感键值」放大到「全部 query 值」。582 复现测(b)不受影响。
- **C4-B（必改）**：等值性不能只靠 ≥8 样本参数化——构造对抗样本 ≥2 条：关键词唯一出现在敏感 k=v 值内（`?code=401`、`token=...timeout...`）。预期结论：**定性输入必须用净化前原文**（classify_failure 入口拿 raw，落盘三路径拿 sanitized——`_exception_display` 单一真相保持，分类侧在 failures 内部先分类后净化，仍是一把刀）。若不做这个切分，净化会把 SESSION(401/cookie/session 关键词，实测 `_RULES:39`) 翻成 unknown，即「脱敏损归因」，比泄露更隐蔽。
- **C4-C**：unresolved placeholders 的 stderr 清单 `val[:80]` 会原样打印 `${__P(name,DEFAULT)}` 的 **DEFAULT 字面段**（JMeter 语法把回退值嵌在括号里，正是 :176-190 现有暴露面之一）→ 清单值段过 `looks_literal_secret`/redact 后再打，键名保留。
- **C4-D**：`tests/test_privacy_drift.py(b)` 的 `${ATLAS_1}` 应为 `${ATLAS_HDR_1}`（与 `placeholder_for` 签名对齐），笔误。
- 其余审查通过：单一实现（credential_gate/一处净化）、闸在产出侧（M14）、无 -o 拒打全文 rc0、存量测不翻的前提（实测 tests/ 无 ticket=/token= fixture 样本，涟漪为零）成立。

### 4.2 C2（REJECT·单点，修订后即 PASS）
- **C2-A（必改，唯一）**：「tests/test_c2_verification.py 新 + 既有文件零触碰」为假。实测磁盘 approve 调用点：`test_review_queue.py` 8 处（含 `_seed` 造的 pitfall/selector_candidate 草稿**均无 verification**，闸一上必 ValueError）、`test_evidence_and_knowledge.py` 2 处、`test_overlay_pipeline.py` 1 处、`test_review_code.py` 1 处（拒绝断言，不受影响）→ ≥9 条存量测试翻红，直接破「772 不回退」波门禁，且按 plan §2 规矩这些文件「默认禁改」。**修法（设计勘误，不动主体）**：design_c2 §3.3 增「补丁 3：seed fixture 补 evidence.verification」，§6 措辞改「新文件 + 上述 3 个既有测试文件的 _seed/fixture 行」；§2 归属表增行：`tests/test_review_queue.py / test_evidence_and_knowledge.py / test_overlay_pipeline.py`（仅 fixture 增 verification 键，**断言本体一条不放宽**——用真三元组喂闸，不是绕闸）。勘误已钉文末，实施者 impl_tower。
- 条件条款：
  - **C2-B**：补丁 1 的 `assertions` 计数 `op=="assert"` 落盘前须实测核对——recorder 现存 op 值以 `step()` 调用点为准（dsl assert 块执行时的 op 字符串实盘未必是 "assert"）；§8 已承认计数可能为 0，条件是把**真实 op 值核后写进补丁文本**，0 口径保留。
  - **C2-C**：复现命令文案 `atlas run <flow> --env <env>` 与全局 `--env` 位置（cli.py:25 全局 parents）实测兼容（run 后置位已修复过），落地测一条文案合法性即可。
  - **C2-D**：`verdict=fail 合法`、`override 必须带 reason`、`批量无 override` 三条与 §7 拍板逐字一致，审查通过。

### 4.3 C1（PASS-with-conditions）
- **C1-A（必修）**：§7 Linux 示例把 `*/30 1-8 * * *` 打成 `*/30 * * * *`——若 OS 真按示例行 tick，**catch-up 会在 1-8 窗外补发**（14:05 tick→due=08:30→age 在 7d 内→fire），破坏用户窗口意图；若按 expr 原样入 crontab，则 catch-up 延迟到窗内下一 tick。两种语义各有所得，**设计必须二选一并改示例**：评审裁决取「Linux 行=expr 逐字复制（窗外语义保真，延迟 catch-up 写进 README）；Windows 行=schtasks /SC MINUTE /MO <expr 最小间隔>（窗外 catch-up 与 pi 常驻 tick 同语义，README 明写差异）」。`crontab --platform` 双形态文案测随之钉。
- **C1-B（必测）**：DST 两个钉桩 case（Europe/Amsterdam 春季缺口 02:30 不双发、秋季回拨 `(since,due]`+水位线不重放）——X6 已声明 fold=0 口径，缺 case 就是缺防线；实测跑不通不阻塞交付，按 §10 标注「fold 语义未实证」继承即可。
- **C1-C**：§6.3 的 `assert` 护栏改**显式 raise ValueError**——`python -O` 剥 assert，安全闸不许依赖它（这是「永不 --approve」红线的实现强度问题）。
- **C1-D**：X3 同步盘条款（§2 表）+ README 本机墙钟/tz 差异一句。
- **C1-E**：dispatch 子进程 env 必须保证 `atlas` 可导入——实测 src 布局下 `[sys.executable,"-m","atlas"]` 在无 install/无 PYTHONPATH 的环境直接起不来（§6.8 只注入了 PYTHONIOENCODING）。条款：env 合并 `PYTHONPATH=str(root/"src")`（或等价路径注入），专项测断言子 env 含 PYTHONPATH。
- **C1-F**：§6.6 CAS 是辅助防线，**互斥权威是租约**（CAS 的 check-write 之间非原子）；注释一句话钉住，防止后人拿 CAS 当唯一闸。
- 通过项（重点②复核）：自写 5 段器语法子集+names 显式拒（M15 对）、7=0 POSIX 口径、**dom/dow 双受限取 OR、任一 `*` 取 AND** 判据正确（naive AND 才是错）、整分表示天然免 +1s 补丁但等值三 case 照钉、半开 `(since,due]`/`(since,due)` 水位线口径自洽、边界表 10 行与 gap6 拍板逐字对得上、timeout 计败/interrupted 不计败（CONTROL_FLOW 第二实现，不立新规则）成立。

### 4.4 C6（PASS）
- 磁盘核对全中：`dsl.py:121 md5`、`dsl.py:757` 唯一 RunRecorder 创建点、`allure_report.py:283` parameters 恒 env、`recorder.steps` 公有、`experience` evidence 通道、`kb.root` 公开属性、`__init__.__version__` ✓。
- 备注 C6-A：`git autocrlf` 字节漂移注记正确；网盘指纹噪音口径「有噪音可解释」= §10 标注，非缺陷，审查通过。X1 as_posix 排序两平台同值专项测在列，重点④通过。

### 4.5 C3（PASS-with-conditions）
- **C3-A（必改）**：哨兵 **scope 匹配 0 文件 = 红并点名 `scope_empty`**。实测 `.gitignore` 屏蔽 `/flows/iservice/`——resurrection 规则首条 scope 在干净 clone 上恒空，哨兵静默假绿，恰是它自己要防的形态。gitignored scope 若有意保留（仅本地生效），须在 rule 上显式 `local_only: true` 并在双栏结论里标注。
- **C3-B（必改）**：`run_pytest` 的 subprocess.run **加 timeout（默认 30min，`--timeout-min`）**，TimeoutExpired→rc1 点名「pytest 挂死」——memory 双教训（pi npm test 挂 2 小时、3 分钟无日志按失败处理），gate 无超时=门禁本身可被挂死拖穿。
- **C3-C**：`tests/test_migration_gate.py` 漏标归属（§6-B 已补，impl_migrate）。
- 通过项：E4 口径没写反（映射清单≠失败基线、diff_baseline 互补不覆盖，磁盘复核属实）；变异不用 mutmut 的理由=治理可复现性（E2 口径正确）；`720` 禁 marker 建议锚成「720 passed」减误报；meta-test「无 mutation_record=视同不存在」是 M11 正解，日期列须非空可解析。

### 4.6 C5（PASS-with-conditions）
- **C5-A（W3 前必修）**：「判据唯一实现在 scripts/boundary_classify.py，doctor 复用同一函数」不成立——`src/atlas` 不能 import `scripts/`（非包、不在 PYTHONPATH=src 下；硬塞 sys.path 是 #92 之外的新脆弱面）。裁决：判据核（load/校验/classify）**上移到 `src/atlas/boundaries.py`**（纯通用代码，账本数据留在 docs/，不违 #11），`scripts/boundary_classify.py` 与 doctor 都是薄消费方；归属表 §6-A 已补该新文件（impl_bounds）。
- **C5-B**：`boundary_assets_consistent`「paths 至少命中一条磁盘路径」对 `flows/iservice/`、`knowledge/` 这类 gitignored region 在 CI/干净 clone 恒假——条款：region 加 `"optional": true`（全路径缺失→warn 不 problem，不并 rc）。
- 通过项：三值枚举/最长特异度/同特异度冲突非法、accepted 无 rationale 非法（C17 债标价精神）、boundary_review 走既有 pending 塔+approve 点名拒绝（与 code_review 同族，不新开写路径 #9/#10 ✓）、engine 运行时永不解析账本 ✓、merge 前分类是义务、Jaccard 误报代价=多一次人审（可接受，与 C3 互为兜底）。

## 5. 离线可测性裁决

- 六份设计测试下限合计 ≥115（18+14+45+12+14+12），全 fake/离线、参数化边界表、无「真机才能验」被偷偷算进门禁。
- 真机项已按 gap6 先例处置为**离线钉桩+待授权标注**：C4 Playwright 文案完备性（§8）、C1 schtasks 冒烟/心跳竞态（§10）、C6 真实 runs 回放（§7）、C3 双栏纪律（§6 本来就要求真机另跑且离线栏不得单独宣布成功）——**均不阻塞动工**，终验 #8 按矩阵逐条勾。
- 离线新风险仅 C3-B（gate 自身可被挂死）已给修法。

## 6. 归属表改判（约束性，design-plan §2 以本节为准）

| 项 | 改判 |
|---|---|
| A | `src/atlas/boundaries.py`（新，C5 判据核上移）→ **impl_bounds**（W3） |
| B | `tests/test_migration_gate.py`（新）→ **impl_migrate**（W3）；`tests/test_versioning.py`→impl_report（W1）；`tests/test_failures_sanitize.py`→impl_report（W1）——均系 plan §2 漏标、无跨主冲突 |
| C | `src/atlas/scaffold.py`（serve 文案一处）→ **impl_report**（W1，plan §2 漏标）；既有测试 fixture 三件（C2-A 列明者）→ **impl_tower**（W1，仅限补 evidence.verification 行） |
| D | `tests/test_xplat_sentinels.py`（新）→ **impl_report 建（W1），此后各波禁改**——扫描必须目录级递归 src/+scripts/（新文件自动入网），波间涟漪归各波主人「改代码保绿」，不改哨兵本体 |
| E | cli.py 波内单主、recorder.py W2/W3 禁入、`impl_migrate 全程禁入 cli.py`：核对无冲突；W1 内 report（engine/cli 补丁）与 tower（review/healing/allure_analysis）**文件集实测不相交**，重点④复查未发现漏标触碰（除本表 A–D 四处新增文件的归属补记） |

## 7. 终验（#8）追加核对项

X3 同步盘条款落文案 / X8 双平台示例与 expr 一致性 / C4-A·B 净化-归因切分对抗样本 / C2-A fixture 仅加数据不放宽断言 / C1-C 无 `assert` 依赖 / C1-E 子 env PYTHONPATH / C3-A scope_empty 红 / C3-B gate timeout / C5-A import 形态。

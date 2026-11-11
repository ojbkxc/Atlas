# Gap11：兼容旧仓的快脚本输出——有界语义等待垫片 + 截图抑制 + 兼容布局（设计）

> 状态：scout 设计稿 **v2**（2026-11-09；v1→v2 同步 G11-1/G11-4 与盘上实现六处契约漂移，逐处标来源）；**2026-11-10 01:5x 依真机 A/B 结论按 lead 明令收紧 §A 硬判据 2 并撤回产能结论，见 G 节抬头与 G.6**；**2026-11-10 09:5x 第三轮 A/B + 8 组档位探针后按 #36 把等待垫片撤出 `--fast` 默认置位（改显式 opt-in，截图抑制保留默认），见 G.7**。
> 波次命题（原样保留，便于对照）：Atlas **超过并兼容** UI AUTO TEST——产出旧仓可直接吃的脚本，且执行更快。
> **本波终态：「兼容」这一半有离线判据与产物字节锁步支撑；「更快」这一半未成立**——A/B 三轮都不出数（第一轮垫片空转=没测；第二轮真实运行但失败点前移=行为变更；第三轮同配置一轮全过、一轮两组前移，且 8 组档位探针证明没有「有收益且不改行为」的组合），故**不得引用本命题作收益主张**，只当机制设计读。
> 立论基线（R1 实测）：`legacy_case` 桥接整条委托旧 `run_case_py`，**继承旧代码全部固定睡眠**，桥接轨不快。
> 因此可动的面只剩一处：**旧仓代码之外的运行期机制**——本档 A（等待垫片）与 B（成功截图抑制）即此两点；除此之外不找别处。**（机制面 ≠ 收益面：这两处能改，不等于改了就一定更快且不改变行为——本轮正是被这一条判下来的。）**
> 边界纪律：不修改 legacy 原仓（红线 #4，全部机制在产物/垫片侧）；「超过」只认 E 的三个可度量项，且**每项都有前置闸**（见 E 段与各条口径陷阱）。

## A0. 盘面事实复核表（scout+emit-impl 双复核，逐条 G4 名引用；行号仅当日备查）

| 项 | 盘上实值（已核） | 判定 |
|---|---|---|
| `lib/pages.py` 固定睡眠 | 61 处字面 `wait_for_timeout` = **153,800ms**；分函数降序：`open_draft_detail` 32000、`open_doc_list` 21900、`select_customer` 21500、`select_source_order` 21000、`ensure_warehouse` 14300、`click_new` 12500、`fill_return_reason` 8000、`find_usable_source_order` 6000、`mount_other_header` 5500、`select_pt` 4800、`open_pt_dropdown` 2300、`wait_save_result` 2000、`click_save` 2000 | ✓（scout AST 求和 + emit-impl 复算一致） |
| `lib/login.py` | 16 处 = **22,800ms**；分函数：`do_login` 9000 / `switch_to_ou` 5000 / `switch_to_lang` 4500 / `do_logout` 2000 / `close_all_menus` 1300 / `_hover_menu_li` 1000 | ✓ |
| `lib/runner.py` | 固定睡眠 0 处 | ✓ |
| conftest hook 全表 | `pytest_addoption`（--headless/--headed）、`pytest_configure`（注销 playwright 插件）、`plan_ctx`（module，持久化 `_load_plan_ctx`/`_save_plan_ctx`）、`_case_watchdog`（**scope=function（AST 缺省值）**、autouse，CASE_TIMEOUT 默认 600）、`sole_page`（module，`channel="chrome"`，headless 默认 True，`page._headless` 置位）、`page`/`context`/`get_page`（module；get_page=登录实查权威+异账号先 `_do_logout_impl`）、**`browser`（session——其余 fixture 全 module，scope 面对产物契约见 C.5）** | ✓，emit-impl 修正②并入 |
| `setup_logging` | **定义在 `lib/runner.py`，conftest 以别名 `_setup_logging` 导入并调用**（`from lib.runner import setup_logging as _setup_logging`）——v1 写「conftest 提供」系名位不精确，按 emit-impl 修正①更正 | ✓（修正后） |
| runner 驱动面 | `run_case_py(yaml_path, case_id, page, ctx, *, data_override, only_steps, skip_precondition, skip_postcondition)` 签名逐字对；`load_yaml_case`/`load_test_data`/`skip_step`/`StepSkipped`（别名 `_SkipSignal`）；`_call_step_fn` 内成功 `step{N}_result`、失败 `step{N}_FAIL` 均 `_do_screenshot`=allure.attach(page.screenshot())；失败另有 `_save_fail_screenshot` **字节直写文件不经 attach**；`_cdp_maybe_pause`/`CdpPauseInterrupt`；yaml 骨架键含 `_enabled`/`_test_data_ref` | ✓（emit-impl 复验吻合面照录） |
| page 方法面 | 恰 15 法：evaluate/fill/get_by_role/goto/keyboard/locator/mouse/reload/screenshot/url/wait_for_function/wait_for_load_state/wait_for_selector/wait_for_timeout/wait_for_url；`page._headless` 仅赋值面在位，**`_compat_click` 消费者当前仓零命中**（风险 R-3） | ✓ |
| 旧仓 pytest.ini 依赖面 | `testpaths=testcases`、`python_classes=Test*`、`markers` 注册表（含 order）、`addopts: -v --tb=short --strict-markers -p no:randomly -p no:playwright`；**requirements.txt 含 `pytest-order>=1.1`**（emit-impl 修正③并入：`--strict-markers` 在场=产物引入未注册 marker 即红；「产物能直接跑」的依赖面必须列 pytest-order） | ✓（修正后） |
| 用例布局 | 双轨：prod 套件 class+`@pytest.mark.order(N)`（`TestProdAmRegressionPlan`）；sit 套件目录式 `NN_<case>/`（step_N+yaml+md，目录内无 test_*.py）。兼容产物取前者 | ✓（v1 已记） |
| 多遮罩教训 | `wait_loading`：`querySelectorAll('.el-loading-mask')` 全枚举 + display/rect 判可见，docstring 记 2026-10-30「首遮罩隐、次遮罩挡按钮误返回」 | ✓ |

**账本结论（可省上限）**：固定睡眠 176.6s 静态上界（A 目标面）；成功截图 attach/张数（B 目标面，编码耗时边界见 B.2）。

---

## A. 有界语义等待垫片（bounded settle shim）协议

**挂点（G11-4 改）**：**Page 类级补丁**（`fastplugin.py` 形态）——覆盖旧 conftest 自建页；v1 的「实例包装」作废（实例面漏掉旧 conftest 内 `ctx.new_page()` 产出的页，类级才闭合）。垫片真源=`src/atlas/legacy_fast.py`+`src/atlas/fastplugin.py`（C.4）。

**env 与档位（G11-4 统一命名，v1 的 `ATLAS_FAST_SETTLE` 作废）**：
- 开关 **`ATLAS_FAST_WAITS`**：`1/on/true`→`mask` 档；`mask`/`xhr`/`mutation` 直选档位；**未置位=默认关（逐字节旧行为，硬约束）**；未知值→WARN 并按关。**默认不开**是裁决点：默认开会改变旧仓裸跑语义，不可接受。
- **档位语义=递加非互斥**（lead 判 #17 改造）：档名 `mask`/`xhr`/`mutation`，**基础条件恒含**——
  - `mask`：`document.readyState==='complete'` ∧ 无任何可见 `.el-loading-mask`（判据 JS 照抄 `wait_loading` 现形，多遮罩教训为基线）；
  - `xhr`：并「在途请求=0」（页内计数 hook；hook 导航后丢失/未装→自动降 `mask` + WARN，单侧测钉）；
  - `mutation`：并 MutationObserver 静默窗 `quiet_ms`（受预算约束）。
- `ATLAS_FAST_SETTLE_ANCHOR`（G11-1）：非空时要求该选择器可见——覆盖「complete 但应用未起」空档；**选择器值只来自 env**（旧仓建议 `.app-main`，红线 #11 模板零业务字面量）。
- `ATLAS_FAST_MIN_MS`（G11-1）：有界下限，默认 `min(400, requested)`；`min_ms>requested` 按 requested 截断——**下限永不顶破「绝不更长」**。

**硬判据（写死，不可谈判）**：
1. **绝不更长**：总耗时 ≤ 原 ms（预算制；永不静默→恰 ms 返回，假时钟双向边界测）。
2. **只按「忙→静默」的转变提前（#33 收紧；原「判据成立即可提前」口径作废）**：提前返回的**唯一许可**=本次等待窗口内**确实观测到过「忙」态**（**只有三个忙信号**：可见遮罩 `busy-mask` / xhr 在途 `busy-xhr` / DOM 真变化 `busy-mutation`），随后转为静默。**readyState 未完成与锚点未出现属「门」，不算忙**——它们只让本次求值不放行，永远给不出提前许可（把「门没挡住」读成「忙过了」正是本轮 P0 的病灶本体；码表见判据 2 之下的「忙」词汇表）。条件公式（G11-1+G11-4）仍是必要条件但**不再充分**：
   `quiet ⇔ readyState==='complete' ∧ 无可见 mask ∧ [xhr/mutation 递加] ∧ [锚点可见（配置时）]`，且 `actual_ms ≥ min_ms`、恒 `≤ requested`。
   **整段从未观测到忙 → 按原 `requested` 睡满（fail-safe，绝不提前；该笔在 stats 里记 `fallback=no-busy-observed`）**——「一上来就 quiet」≠「已静默」，那是**没发生过可观测的忙**，语义上等同于「应用还没开始做事」，提前返回就是把失败点前移。真机依据（2026-11-10 01:35–01:45，`reports/gap11_ab_20261109/SUMMARY2.md`）：旧口径下 `mask` 把**无遮罩守护**的登录/导航段等待压到下限（`6000ms→400ms`、`5000ms→533ms`），`fallbacks` 恒 0=探针从不判「未就绪」（**这是收紧前的形态；#33 后 fail-safe 睡满另有 `no_busy_observed` 计数，`fallbacks=0` 不再等于「垫片没插手」，读法见本节 stats 段与 G.6 陷阱④**），失败点自基线的 step 7 **前移**：fast 组四次运行出现**三个**不同终点（r1②=step 1 语言值空、r1③=step 7、r2②=r2③=step 3 客户名称 label 未找到），而**基线两轮恒 step 7**。同码同档给出不同失败点=**时序非确定性**，是行为变更的二次实证——也因此「这一轮失败点没前移」**不能**当作放行依据。

   **「忙」的词汇表（#33 已落地，形态见 `legacy_fast.py` 的 `_CODE_*` / `_classify_verdict` / `build_condition`）**：探针每次返回一个**码**，三类语义——
   - **busy（算忙，取得「可以走」的资格）**：`busy-mask` / `busy-xhr` / `busy-mutation`——**只有这三种**。readyState 未完成（`not-ready`）与锚点未出现（`not-anchored`）**是「门」不是「忙」**：它们让本次求值不放行，但**永远不构成提前许可**（这正是本轮 P0 的病灶：旧口径把「门没挡住」当成「忙过了」）；`no-hook`/`no-activity` 同属门（档位降级/MutationObserver 还没见过变化）。
   - **quiet（静默）**：仅当 `busy_seen` 已置位时才放行提前——**「见过忙」≠「可以走」，只是拿到走的资格**；仍受 `min_ms` 与预算约束。
   - **fail-closed**：**非字符串返回值（含旧的布尔协议 `True`/`False`）一律按门处理**，不再意味着可以提前——布尔表达不了「曾忙碌」这件事。宁可睡满预算（慢），不放过「压根没活动」（错）。这条不是兼容破口，是判据本体。

3. **probe 异常→回退睡满剩余预算**（`fallbacks` 计数；不猜页面状态）。导航瞬态（执行上下文销毁）=「未静默」继续等；不可恢复抛错=按预算睡完保原语义——不吞不改。
4. **轮询实现（最终态；来源=support-impl 落地实现 + xcheck 判据测钉死，lead 22:3x 核盘——G11-4 中间态「浏览器侧 wait_for_function(polling=100)」作废）**：进程侧 `page.evaluate(condition)` + `clk.sleep_ms(min(_TICK_MS, remaining))` 轮询，**`_TICK_MS=25ms`** 步长、末拍 sleep 取 `min(tick, remaining)`；**不使用 `wait_for_function`（xcheck 测明文禁止：被叫到即 AssertionError；预算边界不可控）、不使用 `wait_for_timeout`（递归禁令不变）**。页侧求值自身耗时不可退，故可核不变量=**「垫片补睡之和 ≤ 预算」**；`actual_ms` 如实记 `max_ms`（墙钟观测值），**不虚报节省**。**取得提前资格**（本笔观测到忙→随后静默，判据 2）之后，补足 `min_ms` 同走进程侧 sleep（有界，`min_ms ≤ requested` 恒成立）；从未见忙的笔不补足、直接睡满。

**stats（G11-4 改形态；#33 后两类计数语义分立）**：`ATLAS_FAST_WAITS_STATS` 指定路径，**逐调用 jsonl**：`{caller_file, caller_line, caller_func, requested_ms, actual_ms, probe, fallback}`（**七键面一个不加一个不减**——fail-safe 与真回退共用 `fallback` 单字段，`;` 连接）+ 收尾一行 summary（`calls/saved_ms/fallbacks`，**saved 为负照记不夹带修饰**）。E 度量 3 从该 jsonl 派算（Σrequested vs Σactual）。**读法（写死，别混）**：`fallbacks`=**真回退**笔数（探针错误 / 无 evaluate / 不可恢复 / 档位降级），旧证据口径「`fallbacks=0` ⇒ 探针从没出错」**继续成立**；`no_busy_observed`=**fail-safe 睡满**的笔数（判据 2 的正常路径，**不是故障**），收紧后这一条会单独上升。两者由 `FastWaitStats.fallback_count` / `no_busy_count` 分开算（`aggregate()` 两个键都在，`no-busy-observed` **不**混进 `fallbacks`）；`by_stage` 按**实际生效**档位归集，**note 永不进 by_stage 的键**（把 fail-safe 当档位统计=造出一个不存在的档位）。**预期副作用**：收紧后 `saved_ms` 会比 #33 前那轮低——**那是正确性不是退步**，把判据放宽回去做数字=把失败点前移换回来（同 G.5 第 7 条）。

**导航后等待的语义边界（G11-1 原因段，#33 后升为一般情形）**：旧仓大量 `page.reload(...)` 后立刻 `wait_for_timeout(5000/8000)`（`open_doc_list`/`open_draft_detail`）——此刻无 mask、骨架未渲染，**纯 mask 探针会 0ms 放行=应用未启动即继续=失败点前移，是行为变更不是加速**。防线次序在 #33 后是：**第一道=「从未观测到忙就绝不提前」（fail-safe，覆盖一切「无遮罩守护的等待」，不只 reload）**；readyState∧min_ms∧anchor 三层是第二道（判据见 D.1.9，风险见 R-1/A-B 硬规则）。真机把这条从「reload 特例」放大成了通则：**登录/语言切换/OU 沉降那几段等待本来就没有遮罩**，原口径照样放行。

## B. 成功截图抑制协议

**B.1（G11-4 更名）**：env **`ATLAS_FAST_NO_STEP_SHOTS`**（复数，与实现/CLI/测试三处一致；v1 名 `ATLAS_FAST_SUPPRESS_SHOT` 作废）：抑制器替换 `allure.attach`，name 匹配 `step\d+_result` → 丢弃+计数；`step\d+_FAIL` 与其余名透传。缺省关=旧行为。
**为什么不是改旧仓**：红线 #4；抑制器在 fastplugin/垫片层，可逆、不动旧代码。
**失败证据三重（逐条有测）**：①FAIL attach 名不匹配正则；②`_save_fail_screenshot` 字节落盘不经 attach（A0 实证）；③allure 缺席路径旧代码本就短路，抑制器同样不装——逐字不变（正对照）。
**B.2 诚实边界（不吹，v1 保留）**：attach 抑制省**报告体积与 IO**，省不掉 `page.screenshot()` PNG 编码耗时（求值在 attach 之前，调用点无成功/失败标签，按栈猜=拒）。B.3：stats 分记「被抑制 attach 张数」与「截图总次数（含耗时）」；「编码归零」不在本波声明，列旧仓侧评审项。

## C. 兼容输出布局契约

`atlas export-pytest <flow> [--fast] [--force] [--compat none|legacy-suite]`（`--compat` 缺省 `none`=gap9b 现形态逐字不变；**CLI 接线 lead 已核**：`atlas legacy --dry-run --fast` 显示 `-p atlas.fastplugin` + **绝对路径** PYTHONPATH + 三 env unset 态）：

- **C.1 产物树**（落 Atlas 侧导出目录，**绝不写旧仓**）：
```
<out>/
├── testcases/<suite>/test_<suite>.py   # class Test<Suite>Plan + @pytest.mark.order(N)
├── conftest.py                          # 旧仓同名 fixture 面 + 垫片/抑制器装配（经 -p atlas.fastplugin 注入）
├── pytest.ini                           # 必须自带：testpaths/testclasses=Test*/markers 注册(order)/--strict-markers/-p no:playwright/-p no:randomly（emit-impl 修正③：strict-markers 在场=未注册 marker 即红）
└── README.md                            # 依赖面显式列 pytest-order>=1.1（否则「纯净旧仓直接跑」判据为假）
```
- **C.2 双轨腿**：旧 case 在位→桥接腿 importlib 载旧 `run_case_py` 整条委托（ctx 播种唯一真相留旧代码）；**桥接腿+垫片=R1 之解**（睡眠在旧代码里，类级补丁直接命中）。新 flow→原生腿（page_object/legacy_pages，gap9b B-2 不动）。
- **C.3 import 面红线**：产物**零 import atlas**；旧 `lib` 只经 `ATLAS_LEGACY_WEB` 定位（缺→EnvBlocked 点名，不猜）。**判据分工引 emit-impl 已交付约束**（`emit_compat.py` docstring 单点声明）：文本哨兵**只认 import 语句行**——vendored 垫片源 docstring 里「绝不 import atlas」纪律叙述不算违规；两条判据各管各面，不互抄。
- **C.4 vendor 真源（G11-4 落位，v1「generator 下新文件」作废）**：真文件=`src/atlas/legacy_fast.py` + `src/atlas/fastplugin.py`；生成期**按真文件字节 vendor + provenance（相对路径+sha256 内嵌产物）**；判据测=产物字节==真文件字节（锁步+对抗样本：改一手抄必红）。
- **C.5 fixture 兼容面**：同名 fixture 面按 A0 表逐一对名，**scope 面对齐写死**（emit-impl 修正②+xcheck AST 实提集：`browser`=session、**`_case_watchdog`=function（装饰器 autouse=True、scope 缺省即 function——产物若写成 module 就红）**、其余=module——scope 漂了 `sole_page` 重建义务就漂移）；`page._headless` 赋值保留（R-3 不做消费假设）；`get_page` 登录=页面实查权威、磁盘值只作记录、**指示器选择器来自 env**（#11，emit-impl 提醒）；`--headless/--headed` 同签名；`setup_logging` 引用名实（runner 定义、conftest 别名）不新造。

## D. 验收判据 + 风险登记

**D.1 离线（逐条带正反对照，F-23 尺；来源标注=G 编号为 lead 裁决件）**：
1. 垫片假时钟四态（**#33 收紧后重排：原先「探针一开口就说静默 ⇒ 远早返回」那一态整条删除**——「从未见过忙」不等于「已静默」，按判据 2 属 fail-safe 睡满；留着旧那一态会把读者引回 P0）：**① 先忙后静**→提前返回（下限 0 时 `actual=_TICK_MS`；这是**提前准入的唯一形态**，`test_p0_mask_appears_then_vanishes_returns_early_never_appears_full`）；**② 从未见忙**（含「一上来就静默」）→睡满 `requested` 且 `fallback=no-busy-observed`（`test_p0_never_busy_page_sleeps_out_requested_original_never_called`、三档版 `test_p0_never_busy_sleeps_out_requested_all_three_tiers`）；**③ 一直忙/永不静默**→恰 ms 返回（`actual==requested`，绝不多也绝不叠加原睡眠；`test_d1_1_never_quiet_returns_exactly_requested_not_longer`）；**④ probe 抛错**→回退剩余预算（`actual==requested`、`fallback=probe-error:*`，**这笔计 `fallbacks`、不计 `no_busy_observed`**；`test_d1_1_probe_error_falls_back_to_remaining_budget_only`）。**两个数分立（别读成一个）**：④这类真回退才计 `fallbacks`；②这类正常睡满只计 `no_busy_observed`，**②绝不进 `fallbacks`**（`test_p0_two_counters_are_semantic_not_arithmetic` 钉死，另见 §A stats 段与 G.5 第 7 条）。另两格不属于「垫片运行时的四态」但同批钉住：`ATLAS_FAST_WAITS` **未置位→逐字节旧行为**（G11-4 默认关硬判据）、**未知值→WARN 按关**；末拍边界另有一格 `test_d1_1_last_partial_tick_still_probes_before_sleeping`（**名字已随 #33 改过**，原名带 `quiet_page_returns_zero`，见 G.5 第 9 条）。
2. 档位递加：基础条件恒含（三档都含 mask+readyState），xhr/mutation 递加逐项核对（G11-4）；hook 未装→降 mask+WARN 单侧测。
3. 多遮罩复现：夹具真构造「首遮罩隐、次遮罩可见」→判未静默；全隐→静默（2026-10-30 教训回归）。
4. 抑制器三证据：`stepN_result` 丢+计数、`stepN_FAIL` 透传、落盘字节仍在；allure 缺席逐字不变；`ATLAS_FAST_NO_STEP_SHOTS` 未置位全透传。
5. 兼容布局：tmp 假旧仓 fixture→产物 collect-only 绿；缺 `ATLAS_LEGACY_WEB`→EnvBlocked 点名；import 面扫描按 C.3 分工（**语句行判据，docstring 纪律叙述豁免**——emit_compat 单点声明）；marker 全注册否则 strict-markers 红（新增，修正③）。
6. vendor 锁步：产物字节==`legacy_fast.py`/`fastplugin.py` 真文件字节 + provenance(sha256) 对得上；手抄注入对抗测必红（G11-4 形态）。
7. stats：逐调用 jsonl 字段齐全（caller_file/line/func/requested/actual/probe/fallback）；property 测任意序列 `Σactual ≤ Σrequested`；saved_ms 负值照记（形态按 G11-4）。
8. fixture 对拍：产物 conftest 名/scope/autouse 与 A0 表逐项相等（AST 扫装饰器参数，**browser=session 单独一格**，修正②）；类级补丁只换 `wait_for_timeout`，其余 14 方法+`_headless` 不动（正负各一）。
9. **导航后等待负向三态（来源 G11-1，lead 读实现发现的假阴性）**：(a) reload 后 readyState='loading' 零 mask →不得 0ms 放行（`min_ms ≤ actual ≤ requested`，默认下限 400）；(b) complete+零 mask 但锚点不可见→同样不提前；(c) **正向对照：先上膛一拍忙碌信号（遮罩出现→消失）且锚点可见 → 才可提前返回**；`requested<400` 时下限截断到 requested（双向边界）。**补一句防回退的定性：三项条件齐备（readyState complete ∧ 零遮罩 ∧ 锚点可见）本身不构成提前许可**——三项全是门、不是忙信号；**「三项齐备即放行」是 #33 之前的旧口径，已撤回**（A/B 实测的失败点前移正来自它）。**测面同步已跟上**：`test_d1_9_loading_document_is_not_quiet_even_with_no_masks` 的说明现写「readyState 是**门**，不是忙碌观测」，`test_d1_9_anchor_invisible_blocks_early_pass` 现要求「见过忙碌 + 锚点可见」**两条同时成立**才提前——**文档曾落后于这两条测**（同一份文档内部两个版本的事实），此条为按盘更正。
10. 补足实现口径（G11-4 补；#33 后措辞更正）：**拿到提前资格（本笔观测到忙→随后静默）后**，min_ms 补足走进程侧 sleep 且 `min_ms ≤ requested` 恒成立（假时钟钉总耗时不越界）；**从未见忙的笔不进这一步**（它们直接睡满 `requested`，见第 9 条 (c) 与本条上方的四态表）。**旧的「静默条件一成立就补足并放行」这种说法已弃用**——照旧读会让人以为只要 `complete ∧ 无 mask ∧ 锚点可见` 就补到下限即提前，那正是 P0。

**D.2 真机（R-G1 已跑三轮、均不作数，见 G.6/G.7；R-G2/R-G3 仍未跑，不预支）**：R-G1 同链路墙钟三态（off/mask/xhr，**失败点必须仍是原步，前移即判行为变更不算成绩**——G11-1 A/B 硬规则；本轮正是这条闸把它拦下来的）；R-G2 `page.screenshot()` 单张耗时占比实测；R-G3 mask 档 flake 率（连跑 3 次纪律）——**R-G3 与非确定性观测（同档四次三个终点、第三轮 r1/r2 翻转）是同一条闸的两端，重测时并入 #33 后的复测轮次，不要单独再来一轮**。

**D.3 风险登记**：
- **R-1（G11-1 前置说明并入；#33 收紧后补一句）**：mask 探针语义前提=「骨架已渲染、只是被遮罩挡着」；**导航/重载后的等待不属该前提**——纯 mask 会 0ms 放行致失败点前移；readyState+min_ms+anchor 三层兜底（D.1.9 负向测钉死）**在 #33 后降为第二道**，第一道是「整段从未观测到忙→按原值睡满」；数据敏感链路建议 mutation 档，量差由 R-G3 定，不口头保证。**本波真机实测：原口径致失败点前移（基线两轮恒 step 7，fast 组四次运行三个不同终点 step 1/step 3/step 7=时序非确定性），硬判据 2 已收紧为「只有观测到过忙再转静默才许提前；整段从未观测到忙→按原 `requested` 睡满（fail-safe）」；`mask` 档对无遮罩守护的等待不生效（等价旧行为），这是机制边界不是可调参数。证据 `reports/gap11_ab_20261109/`（不得删除，索引见本文 G.6）。**
- R-2 xhr hook 导航后丢失→自动降级（D.1.2）。
- R-3 `page._headless` 消费者失联：赋值必做、不做消费假设。
- R-4 抑制器不触 pytest hook，只在 allure 层；第三方在场行为由真机记录。
- R-5 「快」只主张 E 三项，文档/落地注不得写架构优势类结论。
- R-6（新增，G11-4 默认关派生）：`ATLAS_FAST_WAITS` 三态（未置位/`1`/档名）在 dry-run 输出与 stats 中必须可辨识——否则「以为开了其实没开」是新的静默形态。

## E. 「超过」的可度量定义（只认三项，其余拒收）

1. **同链路墙钟**：同 case 同失败点（或同通过点），裸跑 vs `mask` vs `xhr` 中位对比，带当日实跑时间戳（F-21/F-22）；**失败点前移=判行为变更，不入成绩**。
2. **成功截图张数**：allure-results `stepN_result` 条目数——抑制前 N/后 0；`_FAIL` 与落盘张数不变作反证。**主张范围限张数与 IO**（B.2 边界，编码耗时另列 R-G2）。
3. **固定睡眠实际发生量**：**从逐调用 jsonl 派算** Σrequested vs Σactual（静态 176.6s 仅理论上限；G11-4 形态）。

**三量有先后，不是三个并列口径（#33 后写死；lead 03:2x 二次更正：原第②关写成 `saved_ms`>0 是**选错了证据面**，现四处统一成下面这版）**：**重测三关（次序固定，前一关不过后面不作数）**：**①失败点与基线同一步**（真机）——前移即判行为变更，后面全不作数；**②装配生效 + 判据不是 no-op，两半缺一不可**：**2a 真机·装配生效证据**=`ATLAS_FAST_WAITS_STATS` 指定的 jsonl 存在且 `calls>0`，或收尾 summary 行出现且 `calls>0`（**这是第一轮 A/B 的原始判据**——那轮「既无 summary 行也无 stats 文件」= 垫片压根没装；没有这一关，整轮可以完全空转：垫片不装 → 行为与基线逐字相同 → 第①关恒过 → 看着全绿其实什么都没测。要 `calls>0` 而不是 `saved_ms>0`：**前者证明垫片确实拦到了等待，后者要求它必须省**，那是另一件事且可以合法为 0）；**2b 离线·判据正确性**=配对格全绿 `test_p0_mask_appears_then_vanishes_returns_early_never_appears_full` × `test_p0_never_busy_page_sleeps_out_requested_original_never_called`（三档版 `test_p0_never_busy_sleeps_out_requested_all_three_tiers`）——**真机观测区分不了「这条链路本来没机会」与「实现坏了（no-op）」，能区分的只有这些格**；**③才谈墙钟**（多轮/中位 + 同失败点）。**`saved_ms` 与 `no_busy_observed` 都是报告量**（写进 A/B 记录，看这条链上有多少等待真被忙信号守护过），**不作任何一关的通过条件**：真机 `saved_ms` 接近 0 是**合法结果**（被守护的等待本来就少），既不构成失败，也**不构成把判据放宽回去换数字的理由**——拿它当通过条件，等于逼人造数字，正是这条闸要防的反面。（**同文四处**：本条 E 段末、G.5 第 7 条两处、README「当前验证边界」的 gap11 条、CLAUDE FAQ 那条）**本波 E.1 三项都还没有一项拿到有效数**（成因各不相同，见 G.3 ③ 与 G.6 的五处口径陷阱）。

**闭合口径**：本文档 v2 已同步 G11-1/G11-4/emit-impl 三修正与盘上实现；E.1 判据表为 #17-#21 放行件基准，与派工件冲突处以本档标注来源的各条为准，存疑引 lead 裁。

---

## G. 落地注（2026-11-10 本波收口；scout 执笔非数字面，数字位归 lead）

> **正文改动面（如实登记，分六批列全，后批覆盖前批）**：本节最初承诺「只追加、正文零改动」——该承诺自 2026-11-10 01:5x 起由 lead **明令**解除。正文改动清单（**逐条列全，不含糊成「三处」**——把清单写漏本身就是 G.5 第 1 条要登记的那种漂移）：
> **批次一（01:5x，#33 判据收紧）**：①文档抬头命题段——「超过并兼容」原样保留作对照，但**「更快」那一半标注为未成立、不得引用**；②§A 硬判据 2——收紧为「只按『忙→静默』转变提前；整段从未观测到忙→按原 `requested` 睡满」，并补**「忙」的词汇表**（三个忙码 / 门码不算忙 / 非字符串 fail-closed）；③§A stats 段——`fallbacks`（真回退）与 `no_busy_observed`（fail-safe 睡满）**两类计数语义分立**、jsonl 七键不加不减、`saved_ms` 变低是正确性不是退步；④§A「导航后等待的语义边界」段——防线次序重排（fail-safe 升第一道，readyState/min_ms/anchor 降第二道）；⑤D.3 R-1——补本波实测句 + 指向 G.6。
> **批次二（02:1x–02:2x，lead 三次追加更正）**：⑥E 段末追加**「三量有先后」**三关（**当时的第二关写成 `saved_ms`>0 属选错证据面，已被批次六整条更正**——现四处同文，见 E 段末）；⑦G.3 ③ 的 #20b 条目改成**两轮各一句 + 三量成因分立**（墙钟=噪声吞掉且「短」是更早失败、张数=测了且成立但属边界判据、派算=省是真的但是语义差异），并同步「README 那三个显式占位已按 lead 明令删除」；⑧G.6 的角色表与口径陷阱改成**基线两轮恒 step 7 / fast 四次三终点**，新增 `fallbacks` 在 #33 之后的新读法；⑨G.1 新增 #33 收紧行（盘上形态 02:09，**实现未收口、配套判据测在 #34**）、G.3 ② 新增「收尾 summary 可见面缺 `no_busy_observed`」留白（**该条已被盘上推翻并改写，见下方批次四**）、纪律②补 02:2x 复扫的在途告警、纪律③补「本节数字只作论证、不作成绩位」；⑩§A 判据 2 的忙信号括号改成与词汇表一致（**锚点/readyState 明确划到「门」侧**，消除与「只有这三种」的自相矛盾）、D.2 抬头改成「R-G1 已跑两轮均不作数」；⑪PRIVACY 的 jsonl 短码词表与 summary 字段清单同步（`fallbacks` 与 `no_busy_observed` 分立的读法写进隐私面）；⑫G.5 追加第 8 条=**我自己那条「summary 缺 `no_busy_observed`」的查无结论在 02:2x 被盘上推翻**（第三次犯同族错，照实留档），G.3 ② 该条留白同步改写为已同面。
> **批次三（02:3x，lead 通知「#33 已落地」后按盘改写）**：⑬§A「忙」词汇表的锚点从「在途、未收口」改成「已落地」（`_CODE_*`/`_classify_verdict`/`build_condition`）；⑭G.1 #33 行同步为已落地并**按名列出七条 p0 判据格**；⑮G.3 ① 新增成对格闭合行、G.3 ③ 新增「xcheck 名下仍有红格（02:29→02:35 三次跑红数一直在动，**他正在就地修，故当时不登记条数、只登记状态与核对命令**）→ 门禁当时不可能全绿」；⑯G.3 ② 那条「summary 缺 `no_busy_observed`」的留白按盘改写为「两数同面」并挂 G.5 第 8 条自曝；⑰README 78/81/142/592 与 CLAUDE FAQ 380 里「待 #33 判据收紧落地后重测」改成「收紧已落地（02:15）但**还没重测**，新数字同样不存在」；⑱D.1 第 1 条「四态」按 #33 重排（删掉原先「探针一开口就说静默 ⇒ 远早返回」那一态，改成**先忙后静→提前 / 从未见忙→睡满+no-busy / 一直忙→恰 ms / probe 抛错→真回退**四态并点明两个数分立）；⑲**修我自己的死引用**：G.3 ① 里 D.1.1 那格改的两个 `test_d1_1_*` 名字盘上已不存在（#33 改名换语义），已换成 `test_p0_*` 现名，并把核对方法与这次翻车记进 G.5 第 9 条——**「提速结论已撤回」不动，落地不等于有成绩**。
> **批次四（02:5x，lead 按盘给的三条更正）**：⑳G.3 ③ 那条「xcheck 名下仍有红格」→ **改成「已闭合」**（他 02:4x 亲跑定向复跑全绿 + 全量门禁全绿 + 冻结窗成立），同时**按纪律①只写状态与命令、不转录条数与全量数**（计数唯一来源仍是 lead 落的四处）；㉑G.5 新增第 10 条登记这次「过程态快照→自己闭合」的走法，并把「快照只写形态」判为**有效写法**（若当时写死红数，02:4x 就成假事实）；㉒**去掉全部会漂的文件 mtime 与定向跑时刻**（G.1 #33 行三处、§A 词汇表一处、G.3 ①/② 各一处、G.5 第 8 条一处），一律改成「已落地 / 当日定向复跑全绿」的形态说法；**理由按 lead 口径记牢：文件时间戳每波必漂，写进过程文档就是下一波的假事实来源**（与 F-21 行号漂移、F-15 键数漂移同族，只是这次漂的是 mtime）；㉓G.1 #33 行结尾「xcheck 仍红、别说成全仓闭合」→「已由 #34 转绿」。
> **批次五（03:1x，lead 又抓到两条条文级旧口径，按盘改）**：㉓D.1 第 9 条 (c) 原写的是「正向对照=三项条件齐备即可提前返回」这一形态——**这是旧口径**，#33 后三项条件（readyState complete ∧ 零遮罩 ∧ 锚点可见）**全是门、不构成提前许可**；改成「先上膛一拍忙碌信号（遮罩出现→消失）且锚点可见 → 才可提前返回」，并点明「三项齐备即放行」已撤回。**盘上测面早已跟上**（`test_d1_9_loading_document_is_not_quiet_even_with_no_masks` 的说明写「readyState 是门不是忙碌观测」、`test_d1_9_anchor_invisible_blocks_early_pass` 要求「见过忙碌 + 锚点可见」两条同时成立）——**这次是文档落后于测试**，与 G.5 第 1 条同族但方向相反：第 1 条是「盘上对文本旧」的六处，这回又多两处，登记为**同一事故形态的第 7、8 处**；㉔D.1 第 10 条「静默条件一成立就补足 min_ms」同样歧义（会被读成「静默条件一成立就补足并放行」=P0），改成「拿到提前资格（本笔观测到忙→随后静默）后」并写明从未见忙的笔不进这一步；㉕§A 判据 4 里同一短语一并改；㉖按「**解释被删语义时不要把原句抄回解释里**」这条方法（G.5 第 9 条的延伸），把 (c)/10/批次三 里为说明而引用的旧措辞全部改写成描述式，让 lead 给的 grep 闸能真正全空——**能过闸的措辞才算改干净**。
> **批次六（03:2x，lead 抓到第②关选错证据面——四处统一同文）**：㉗三关的第②关原写「真机 `saved_ms` 总和 >0」，**这是判据设计缺陷**：真机 saved_ms 接近 0 既可能是 no-op、也可能是「这条链路本来没机会」，两者在真机观测里不可区分；而且把 saved_ms 设成通过条件会**反过来逼人放宽判据换数字**（正是这条闸要防的东西的镜像）。**现统一为**：①失败点与基线同一步 →（②**2a 真机装配生效 `calls>0`**〔jsonl 存在或 summary 行出现且 calls>0；这是第一轮空转的原始判据，缺了整轮可假绿〕**+ 2b 离线配对格全绿**〔no-op 的唯一鉴别面〕）→ ③才谈墙钟；`saved_ms`/`no_busy_observed` **降为报告量，不作任何关的通过条件**。㉘同批保持同文的四处：E 段末、G.5 第 7 条两处（隐性风险 / 重测前置）、G.6 陷阱④、G.1 #33 行，加 README 边界段与 CLAUDE FAQ 各一处；并清掉 G.1 里残留的「提前且 saved_ms>0」措辞与一句重复。**lead 原话「不是你的错，但现在要改对」照记**：这条疏忽写在我上一版的三关里，我按他的形态统一到四处同文并留档。**教训=「哪一关该看哪个证据面」本身是判据的一部分**：在场（装配生效）与有效（省了多少）是两件事，混在同一关里就会既误判正确实现、又放过空转轮次。已并入 [[feedback_flag_self_evidence]] 的同族条目。
> **仍未改的**：A0 表、B/C 段正文、D.1 十条判据条目本身、G11 裁决引用——**若后来人发现这些也有改动，那是未登记的改动**，按 G.5 第 1 条处置（纠正方向=补登记并说明由谁授权，不是默认合法）。改动来龙去脉见 G.5 第 7 条，证据面见 G.6。

> **本节三条纪律**（沿用 gap9 G 节已定通则，不新造）：
> ① **零计数位**——门禁全量数、测项数一律不写（**定向复跑也只写「全绿/有红」的形态，不写条数**）；计数唯一来源=README 顶部徽章 + README 边界段 + AGENTS §2 + CLAUDE FAQ 四处同数行，由 lead 落笔。本节只写「闭合/留白/待授权」。
> ② **引用测一律函数名**（G4 通则）——本波 `legacy_fast.py`/`emit_compat.py`/`test_legacy_fast*.py` 多次整体重写，行号每漂一次就制造一格假缺口；下列索引于 2026-11-10 00:24 当日 grep/AST 复核在位；**02:2x 复扫记录**：`src/atlas/legacy_fast.py`（02:09）与 `tests/test_legacy_fast.py`（02:20）当时**正在被 #33 的落地改动重写**——所以下表里与垫片直接相关的格名可能随实现漂移，**引用前先重新 grep 函数名**（在途瞬时态不等于遗留缺陷，也不等于索引仍有效，F-26/F-21 同纪律）。
> ③ **真机数值只落收口位**——**本节引用的任何秒/毫秒数字都只为论证「为什么这轮不出成绩」**（如 `6000→400`、基线跨批 93.0/107.6/177.6s），**不构成成绩位、不参与任何「省了多少」的结论**；数值一律以 `reports/gap11_ab_20261109/` 档案原文为准，文档与档案不一致时**以档案为准并回来改文档**。
> **③ 附（占位符处置）**：A/B 三量（墙钟 / jsonl 派算 / 截图张数）归 lead 落 README「当前验证边界」的 gap11 条目；**本节与那条都不留数值格、也不留占位符**（2026-11-10 02:1x 起该条的三个显式占位已按 lead 明令撤除、**不留新的占位记号**——占位会被读成「等个人来填数」，而当前结论是「无有效数、须先收紧再重测」），只写状态（没测 / 测了但不作数 / 待授权）——**不许用设计目标冒充实测，也不许把作废轮次的数字捡回来填坑**。

### G.1 盘上实现终态（当日读码复核，与设计文本的每一处差额）

| 面 | 盘上事实（当日核） | 与设计文本的关系 |
|---|---|---|
| 档位取值 | `legacy_fast._TIERS` 把简写归一为**累加档**：`xhr→("mask","xhr")`、`mutation→("mask","xhr","mutation")`；`_ON_VALUES=("1","on","true")→mask`；`_OFF_VALUES=("",0,off,false)`；未知值 **WARN + 按关**（`waits_gate`：「默认关的开关不因疑似拼写启用」） | 与 A 段「递加非互斥 + 未置位=默认关」一致；文本未写的是**简写归一表**这一实现形态 |
| 有界下限 | `_DEFAULT_MIN_MS=400`（`min_ms_gate` 坏值回落默认、不改语义）；mutation 静默窗 `_DEFAULT_QUIET_MS=200`（受预算截断） | G11-1 修法 2/3 落码，默认值逐字对条文 |
| 轮询实现 | `_TICK_MS=25` 进程侧 `page.evaluate(condition)` + `clock.sleep_ms(min(_TICK_MS, remaining))`，末拍取 `min(tick, remaining)`；`settle`/`install_fast_waits` 全路径**零 `wait_for_function`、零 `wait_for_timeout`** | A.4 终态（G11-4 中间态「浏览器侧 wait_for_function(polling=100)」作废）。防线=「垫片补睡之和 ≤ 预算」，`actual_ms` 如实记墙钟、不虚报节省 |
| 挂点 | `install_fast_waits(Page, …)` **类级**（`playwright.sync_api.Page`），旧 conftest 自建页与 `ctx.new_page()` 产出页一并命中；`uninstall(None)` 全量还原 | A 段「G11-4 改：类级补丁」落码（v1 实例包装作废）；page 其余 14 法与 `_headless` 赋值位不动（A0 表逐条列名，判据只换 `wait_for_timeout`） |
| **#33 收紧（已落地；判据测在 `tests/test_legacy_fast.py`，定向复跑全绿）** | 探针改为**返回码协议**：`busy-mask`/`busy-xhr`/`busy-mutation`=忙（取得资格）；`quiet`=静默且 `busy_seen` 已置位才提前；`not-ready`/`not-anchored`/`no-hook`/`no-activity`=**门**（挡放行但不构成提前许可）；**非字符串（旧布尔协议 `True`）一律按门 fail-closed**（`_classify_verdict`）。从未见忙→睡满并记 `fallback=no-busy-observed`（`_NOTE_NO_BUSY` 字样由 lead 定死，A/B 差分按它分组）。统计侧**两类计数语义分立**：`fallback_count`=真回退（**不含** no-busy）、`no_busy_count`=fail-safe 睡满，`aggregate()` 两个键都给；**jsonl 仍固定七键**（两类共用 `fallback` 字段，`;` 连接，`split_note` 单点拆分）；`by_stage` 键=实际生效档位，**note 永不进档位键** | **这是 A 段硬判据 2 收紧后的实现形态**（原「判据成立即可提前」口径作废）。配对判据格按名引用（不引行号）：`test_p0_never_busy_page_sleeps_out_requested_original_never_called` × `test_p0_mask_appears_then_vanishes_returns_early_never_appears_full`（**就是「从未见忙→睡满」与「先忙一拍→提前」的成对格（这一对是 no-op 的唯一鉴别面，见 E 段末三关 2b）**——缺任一条，「实现退化成 no-op」与「正确收紧」同答）、`test_p0_never_busy_sleeps_out_requested_all_three_tiers`（三档各覆盖）、`test_p0_busy_observation_is_per_call_not_sticky`（sticky 双向）、`test_p0_protocol_shape_verdicts_are_never_early`（门码不算忙）、`test_p0_early_exit_is_guarded_by_busy_seen_ast`（AST 级防线）、`test_p0_two_counters_are_semantic_not_arithmetic`（两数不得相加反推）。**注意两点**：①`settle` 的返回契约**没变**（仍是 `(actual_ms, note)` 二元组，G11-1 那条明写不变），变的是提前准入；②**本格登记的是「实现+判据测已落地」**，no-op 同答风险位已由上面的成对格堵住；xcheck 名下那批红格**已由 #34 转绿**（见 G.3 ③ 末条：只登记状态与核对命令） |
| 记账形态 | `FastWaitStats.record(caller_file/caller_line/caller_func/requested_ms/actual_ms/probe/fallback)` + `to_jsonl` + `aggregate`（`calls/original_ms_total/actual_ms_total/fallbacks/no_busy_observed/by_stage`）；**收尾 summary 由 `fastplugin.pytest_unconfigure` 打印**（含 `fallbacks` + `no_busy_observed` 两个分立计数、`floor_ms/anchor/tiers`，明写「0 省=开了没省」「actual>original 属违规」） | A 段 stats 落码。**盘上已无独立聚合文件写点**：#23 评审单批准的 B-07 `installed_targets`/B-08 `to_stats_json` 两符号已在 #24 删除批落地——**别再去文本里找「聚合 json 文件」形态，它不存在** |
| 装配面 | `fastplugin.pytest_configure`：`ATLAS_FAST_WAITS` 在场→`_install_waits`（注入失败 WARN，用例按原固定睡眠跑）；`ATLAS_FAST_NO_STEP_SHOTS` 在场→`_install_shots`（替换 `allure.attach`，`_STEP_RESULT` 命中才丢、其余透传；allure 缺席→WARN 不装） | B.1 复数名与实现/CLI/测三处一致；`_env_on` 未知值 WARN+False 与 `waits_gate` 同口径——**不在开关的方言也是开关**（R-6 三态可辨识的落码形态） |
| 兼容产物 | `emit_compat_package`：**拒绝先于任何写盘**；产物树 `testcases/<suite>/test_<suite>.py`（`Test<Suite>Plan` + `@pytest.mark.order(N)`）+ `conftest.py` + `pytest.ini` + `README.md` + **两条 vendored 文件**（`SHIM_SOURCE_FILES=("src/atlas/legacy_fast.py","src/atlas/fastplugin.py")`，provenance 头 `# vendored (gap11 #18 provenance): source=… sha256=…`）；ini 自带 `--strict-markers` + `markers: order` + `-p no:randomly` + `-p no:playwright`；fixture 面与 `CONTRACT_FIXTURES` 名单**等值**（等值即计数，不写死条数）（`browser=session`、`_case_watchdog` autouse、其余 module），**不新增旧仓没有的名字**（定位/委托/缺前置把手是模块级普通函数，与用例模块共享 `_compat_runtime_lines` 单一渲染源） | C.1/C.5 落码。**一处文本漂移要认**：C.1 的 conftest 行写「垫片/抑制器装配（经 `-p atlas.fastplugin` 注入）」，产物实际是**同目录 `import fastplugin` + conftest 内 `pytest_configure` 委托**（ini 也**不写** `-p atlas.fastplugin`）——因为 C.3 第一红线禁产物 import atlas，`-p` 前缀名在旧仓机器上无从解析。**盘上形态是对的、文本是旧的**；照文本去「修回 -p」就会把红线 #4 撞开 |
| import 面扫描 | `atlas_import_hits`（AST 语句行）+ `atlas_import_text_hits`（文本兜底；行首缩进不算语句、docstring 里的纪律叙述不算违规）合成 `is_atlas_free`；bytes 入参**严格 decode**（坏字节当场点名，不静默 0 命中） | C.3 判据分工落码。**如实标注**：这道闸的**消费点在判据测与 xcheck，不在 `emit_compat_package` 生成路径**（当日复核：生成期走的是 symbol 面 `compat_symbol_problems` + 语法自钉 + vendor 字节锁步）。要变成「生成期当场拒写」属**新增改动**，需 lead 裁——本波不声称已做 |
| CLI | `atlas legacy --fast`：`pytest_command` 前置 `-p atlas.fastplugin`；`fast_child_env` 把 PYTHONPATH **逐条绝对化 + 注入 Atlas 包根**（P0 真凶：相对 `src` 只在 cwd=Atlas 根有效，子进程 cwd 是旧仓 web 目录 → `import atlas` 撞空）；`FAST_ENV_SWITCHES` 全清单（唯一声明位在此，等值即计数），dry-run 逐键打**三态**标注（`(explicit)` / `(default --fast)` / `unset`，展示的是子进程实际 env 而非父 `os.environ`） | C 段 + lead 当日核盘（清单从派单口径的三项扩到实现面全集是收敛动作；**本文档不写条数**——等值即计数）。**不带 `--fast` 时命令行与环境逐字节不变**有专测；`--fast` 的**代置位**规则见 G.3b（#30/#31 修正，早期版本只注入不置位=空转） |
| 组合闸 | `compat_combo_problem` 是唯一判据位；CLI `_compat_gate` 只剩懒 import + 委托（旧内联版对 `"bogus"/""/"LEGACY-SUITE"` 放行、与生成器面分叉，八组合等值扫抓出后收口）；`--compat legacy-suite` 必配 `--fast`，未配对/未知形态 rc 2 且**零产物**；`--compat none`=gap9b 现形态逐字节不变 | C 段落码；help 里「需与 --fast 同用」字面被测钉（改措辞会红，README/CLAUDE 照此字面写） |
| 路径解析（P0 补格） | #27 修的正是「标准 `.env` 形态（`ATLAS_LEGACY_WEB` 相对值）下兼容包导不出」；解析判据与 `config.check_legacy_paths` **同源锁步**；`export-pytest` 与 `legacy` 两入口对同一 env 形态给同一答案；缺键点名两个 env 键 | **设计文本原本没有这一格**——真机前置检查暴露后补的判据，属本波新增防线 |

### G.2 跨波次同步义务与「刻意不复制」四条（来自 #23 冗余审计评审单，lead 批准入注）

这四条的用处是**挡住下一波把已定案的东西当冗余清掉**（原文与当日证据在 `docs/gaps/gap11-slimming-review.md` C-01/D-10/E-01/A-13 + D 档「近期已收敛，不可删」专章）：

1. **C-01 · 等待族天然两份，同步义务不是冗余**：引擎侧 `src/atlas/waits.py` 与产物模板 `emit_fast._FAST_SUPPORT_SOURCE` 里的 `wait_for_rows/wait_for_mask_gone/wait_for_vue_field/wait_for_url_change` 是两套代码，**这是红线 #4（产物零 import atlas）的直接后果**，不是复制失守。约束：**新增等待族时两处都要动，节拍常量不得各自漂移**（引擎侧历史用字面 `100`、模板侧是 `TICK_MS`；F-4 已清除模板侧退避乘算）。两侧各有一条锁步测在位：`test_transient_marks_lockstep_with_atlas_waits`、`test_d1_2_transients_marks_lockstep_with_waits`。
2. **D-10 · `fastplugin` 不复制 `credential_gate` 净化刀是决定，不是缺项**：当日复核 `fastplugin.py` import 面为纯 stdlib + `importlib.util`，`sanitize`/`credential_gate` 两词在 fastplugin 与 legacy_fast 内**零命中**。理由：垫片只处理毫秒与选择器，**产物侧根本没有「不可控文本落盘」这条面**；补一把刀反而开出第二份判据（#92 反面）。「别处有刀、这里没刀」读成缺项是错的。
3. **E-01 · 防复发的尺不在门禁里**：当日实测 `pyproject.toml`/CI/`*.cfg`/`*.ini` 内 ruff / flake8 / pylint **零匹配**——本波清掉的死 import 属人工扫删，**没有机器尺挡回归**。把 `python -m ruff check src --select F401,F841,F811,E402` 接进五项门禁属**新增门禁（实现改动）**，lead 记后续波次；本条只登记事实，不声称已接。
4. **A-13 · 代码评审草稿的去重键来自 `task` 字段，不是独立 `identity`**：`review_code` 里曾有算完不用的 `identity` 串；盘上事实=`experience.draft_from_text` 内部按 `task|exception_type|failure_class|pitfall_id` 算 id，而 `task=f"code_review: {file}:{line}"` **已含 file:line**，同规则不同行本就异 id。**处置=删那个变量 + 本条留注**；lead 此前发的「加 `identity_key` 参数」指令作废并撤回（真要改成 `file:line:rule_id` 键形态=改历史草稿 id，属另一波决策）。

### G.3 未闭合项三态表（闭合 / 留白 / 待授权）

**① 闭合（离线判据；「测在位且判据非恒真」不等于跑绿——跑绿只认 #20a/#25 门禁终验）**

| 设计条目 | 盘上判据测（当日复核在位） |
|---|---|
| D.1.1 假时钟四态 + **未置位（env 层）逐字节不变** + 未知值 WARN 按关 | `test_p0_never_busy_page_sleeps_out_requested_original_never_called`（**#33 后取代原 quiet→提前那格**）、`test_p0_mask_appears_then_vanishes_returns_early_never_appears_full`（成对正对照）、`test_d1_1_never_quiet_returns_exactly_requested_not_longer`、`test_d1_1_probe_error_falls_back_to_remaining_budget_only`、`test_d1_1_transient_error_keeps_waiting_inside_budget`、`test_d1_1_unrecoverable_sleeps_out_budget_then_propagates`、`test_d1_1_control_flow_error_propagates_immediately`、`test_d1_1_assertion_error_is_never_downgraded`、`test_d1_1_no_evaluate_falls_back_full_without_crash`、`test_d1_1_last_partial_tick_still_probes_before_sleeping`（**格名随 #33 改**）、`test_plugin_env_off_means_zero_behavior_change`、`test_plugin_env_on_warns_on_unknown_value`、`test_plugin_injection_failure_warns_not_crashes`、`test_zero_and_negative_timeouts_delegate_to_original`（xcheck：`test_x1_c1_budget_sweep_wallclock_never_exceeds_requested`、`test_x2_c1_page_cost_beyond_budget_adds_no_shim_sleep`、`test_x4_c1_probe_error_falls_back_original_exactly_once`、`test_x17_c7_gate_off_three_pages_all_take_original_wait`） |
| D.1.2 档位递加 + hook 未装降 mask+WARN | `test_d1_2_tier_composition_mask_always_present`、`test_d1_2_xhr_clause_requires_pending_zero_not_hook_absence`、`test_d1_2_hook_missing_degrades_to_mask_with_warn`、`test_d1_2_mutation_quiet_window_is_budget_bounded`、`test_d1_2_probe_alias_and_tier_gate_vocabulary`、`test_x18_c1_stats_record_effective_tier_after_degrade` |
| D.1.3 多遮罩全枚举（2026-10-30 教训回归） | `test_d1_3_mask_probe_judges_all_masks_not_first` |
| D.1.4 轮询不碰页侧计时器（递归禁令） | `test_d1_4_poll_path_never_touches_page_timers`、`test_x14_c1_probe_path_never_calls_wait_for_timeout`、`test_install_twice_never_recurses` |
| D.1.5 兼容布局 / EnvBlocked 点名 / marker 注册 / strict-markers | `test_compat_test_module_shape`、`test_compat_pytest_ini_criteria_surface`、`test_c5d_artifact_keeps_headless_assignment_and_marker_registration`、`test_compat_rejects_unsupported_legs_by_name`、`test_compat_symbol_check_names_absent_function`、`test_compat_readme_declares_deps_and_boundaries`、`test_business_selector_positive_control_legacy_repo`（**正向对照**：真旧仓文件必须被选择器判据命中，防恒真）、`test_compat_specs_classify_and_persist_keys`、`test_bridge_step_bad_shape_rejected`、`test_compat_package_deterministic`、`test_compat_package_refuses_nonempty_without_force` |
| D.1.6 vendor 字节锁步 + 手抄对抗 | `test_vendor_provenance_and_byte_equality`、`test_vendor_detects_stale_source`、`test_vendor_crlf_survives_byte_for_byte`、`test_vendor_bom_is_part_of_bytes`、`test_vendor_without_provenance_header_rejected`、`test_vendor_text_interface_rejected`、`test_vendor_missing_source_raises`、`test_shim_sources_are_atlas_free_and_present`、`test_vendored_real_shims_stay_atlas_free`、`test_c4d_vendor_lockstep_is_byte_level`、`test_vendor_standalone_load_exposes_contract_symbols`、`test_vendor_import_surface_is_stdlib_or_optional_only`、`test_vendor_no_business_selector_default` |
| D.1.7 stats 逐调用字段 + `Σactual≤Σrequested` property + saved 负照记 + 落盘尽力而为 | `test_d1_7_stats_jsonl_fields_and_caller_attribution`、`test_d1_7_stats_aggregate_shape_and_saved_never_negative`、`test_d1_7_actual_never_exceeds_requested_property`、`test_d1_7_stats_dump_failure_is_best_effort`、`test_plugin_stats_flush_on_unconfigure` |
| D.1.8 fixture 对拍 + 类级补丁只换 `wait_for_timeout` | `test_compat_conftest_fixture_contract_ast`、`test_compat_conftest_fixture_set_is_exactly_seven`、`test_c5a_old_repo_fixture_face_extracted_not_hardcoded`、`test_c5b_artifact_provides_whole_old_repo_fixture_face`、`test_c5c_artifact_adds_no_fixtures_beyond_old_repo`、`test_d1_8_only_wait_for_timeout_is_replaced`、`test_only_patched_method_restores_class_methods`、`test_plugin_class_level_patch_covers_conftest_made_pages`、`test_plugin_passes_tier_floor_and_anchor_to_shim` |
| D.1.9 导航后等待负向三态（G11-1 假阴性补格） | `test_d1_9_loading_document_is_not_quiet_even_with_no_masks`、`test_d1_9_anchor_invisible_blocks_early_pass`、`test_d1_9_min_ms_floor_applied_and_clamped_by_requested`、`test_d1_9_default_floor_is_400`、`test_x15_c7_loading_page_is_not_passed_early_via_env`、`test_x16_c7_anchor_invisible_floor_and_clamp_via_env`、`test_bad_anchor_falls_back_to_original_wait_not_early_pass`、`test_anchor_entering_js_is_json_escaped_not_concatenated`（锚点值来自 env 且**注入 JS 走 JSON 转义**——红线 #11 + 注入面）、`test_anchor_gate_reads_env_only` |
| D.1.10 min_ms 补足走进程侧 sleep 且 `min_ms ≤ requested` | `test_min_ms_gate_matrix`、`test_settle_and_install_argument_validation`、`test_x3_c1_class_level_multi_page_sequence_budget` |
| B 档截图抑制三证据 + 不洗绿 + 失败点不前移 | `test_plugin_shot_suppression_drops_only_step_result`、`test_x10_c3b_fail_screenshot_bytes_survive_suppression`、`test_x11_c3c_screenshot_call_count_identical_on_off`、`test_x12_c3_allure_absent_means_suppressor_not_installed`、`test_x5_c2_wait_then_assertion_failure_stays_failed_with_shim_on`、`test_x6_c2_probe_error_and_assertion_never_degraded`、`test_x7_c2_shim_on_off_terminal_state_identical`、`test_x9_c8_failed_step_never_moves_across_probe_modes`、`test_x13_c2_shim_pair_holds_no_skip_machinery`、`test_x8_c2_injection_failure_warns_and_case_still_runs_old_way` |
| C.3 产物不 import atlas（沙箱自证 + 判据自身可破） | `test_c4a_shim_pair_runs_in_sandbox_without_atlas`、`test_c4b_sandbox_blocker_bites_on_violation`、`test_c4c_shim_sources_scan_atlas_free_with_real_control`、`test_atlas_import_ast_scan_hits`、`test_atlas_import_ast_scan_no_false_positive`、`test_text_sentinel_scan_statement_vs_narrative`、`test_bytes_input_scans_strictly_not_silently_empty`、`test_syntax_error_propagates_not_false_clean`、`test_positive_control_real_repo_file_must_hit` |
| G11-2 非破坏差分（旧仓零字节改动） | `test_c6a_old_repo_source_face_unchanged_across_run`、`test_c6b_snapshot_diff_bites_on_injected_repo`（注入违规必须让差分咬人）、`test_c6c_gap11_modules_have_no_legacy_write_path`、`test_c6d_compat_export_leaves_legacy_web_untouched` |
| CLI 注入与环境面 | `test_pytest_command_fast_injects_plugin`、`test_pytest_command_without_fast_byte_identical`、`test_pytest_command_fast_combines_headed_and_keyword`、`test_fast_child_env_absolutizes_relative_pythonpath`、`test_fast_child_env_preserves_absolute_entries`、`test_fast_child_env_passes_switches_through`、`test_run_pytest_fast_wires_plugin_and_env`、`test_run_pytest_without_fast_env_unchanged`、`test_legacy_dry_run_fast_shows_injection_and_switches`、`test_legacy_dry_run_plain_shape_unchanged` |
| 组合闸唯一位 | `test_compat_combo_gate_single_point`、`test_compat_combo_problem_source_single_point`、`test_compat_gate_delegates_to_shared_judgment`、`test_compat_gate_rejects_unknown_shape_behaviorally`、`test_compat_gate_equals_generator_across_eight_combos`、`test_c9a_combo_gate_rejects_unpaired_form_with_named_message`、`test_c9b_two_entries_agree_on_combo_judge`、`test_c9c_refused_compat_forms_leave_no_half_artifact`、`test_compat_kwarg_passthrough_only_when_set`、`test_emit_pytest_package_compat_signature_lockstep` |
| **#33 收紧：判据 2「从未见忙→睡满」与「先忙一拍→提前」成对** | `test_p0_never_busy_page_sleeps_out_requested_original_never_called`、`test_p0_mask_appears_then_vanishes_returns_early_never_appears_full`（**与上一条成对，缺一即与 no-op 同答**）、`test_p0_never_busy_sleeps_out_requested_all_three_tiers`（三档各覆盖）、`test_p0_busy_observation_is_per_call_not_sticky`（sticky 双向）、`test_p0_protocol_shape_verdicts_are_never_early`（门码不算忙）、`test_p0_early_exit_is_guarded_by_busy_seen_ast`（AST 级防线：提前出口必被 `busy_seen` 护住）、`test_p0_two_counters_are_semantic_not_arithmetic`（`fallbacks`+`no_busy_observed` 不得相加反推 calls）、`test_d1_7_stats_aggregate_shape_and_saved_never_negative`（聚合六键含 `no_busy_observed`）——均在 `tests/test_legacy_fast.py`，**当日定向复跑全绿**（按纪律①只写形态不写条数；不是基线、不构成第五把尺） |
| 套件自身健康 | `test_xz_this_file_itself_parses`（两份 xcheck 各一条：文件级语法自钉——「一个 SyntaxError 挡全仓」事故形态的永久化防线） |

**② 留白（契约自记的结构性不做，带重启条件——不是缺陷）**

- **PNG 编码耗时不在本波主张面**（B.2/B.3）：`ATLAS_FAST_NO_STEP_SHOTS` 省的是 attach 体积与 IO，**省不掉 `page.screenshot()` 编码**（求值在 attach 之前、调用点无成功/失败标签，按栈猜=拒）。重启条件=旧仓侧评审「编码归零」方案；动旧仓即违红线 #4。
- **mutation 档量差与 flake 率**：数据敏感链路建议 mutation 档，但「mask 档 flake 率/耗时差」只能由 R-G3 真机定，**不口头保证**；`test_d1_2_mutation_quiet_window_is_budget_bounded` 只钉「受预算约束」这条不变量。
- **atlas-free 闸未接生成期硬拒**：`is_atlas_free` 现由判据测与 xcheck 消费（当日复核 `emit_compat_package` 不调它）。变成「生成期当场拒写」属**新增改动**，需 lead 裁；本波如实写「防线在测侧，不在产线侧」。
- **锚点建议值不进模板**：`ATLAS_FAST_SETTLE_ANCHOR` 的选择器只来自 env（红线 #11），旧仓建议 `.app-main` 只写文档——产物零业务字面量由 `test_vendor_no_business_selector_default` 钉。
- **summary 行的两个计数已同面（我这条留白开出去又收回来，时刻都记着）**：02:1x 我按当时读到的 `fastplugin` 原文写「summary 不打 `no_busy_observed`，该数只在 jsonl 里」，并把它登记成留白；**随后（同一分钟窗口）复扫发现 `fastplugin.py` 已经在 summary 行打 `no_busy_observed=`**——我那条「查无」在写下的当下就过期了（**同一个坑第三次犯**，见 G.5 第 8 条）。现按盘上终态写：**收尾 summary 同时给 `fallbacks`（真回退）与 `no_busy_observed`（fail-safe 睡满）两个整数**，可核面与执行面同面成立；PRIVACY 的 summary 字段清单与此一致。
- **兼容布局只此一种**：`--compat` 现仅 `none|legacy-suite`（prod 套件 class+`@pytest.mark.order` 轨）；旧仓目录式 `NN_<case>/` 的 sit 轨本波不产出，需求出现先改契约再动码。

**③ 已跑但不作数 / 仍待授权（两种都不是「通过」，也不许合并成一句）**

- **#20b 真机 A/B（三组串行：原样 / `--fast` / `--fast`+stats）——已跑过三轮，三轮都不出产数**：①**第一轮作废**：`--fast` 组日志里既无 summary 行也无 stats 文件，垫片当时根本没被激活（只注入不置位，见 G.5 第 6 条 P0）——那组数不是「更慢」也不是「更快」，是**没测**；②**第二轮垫片真的在跑，但结论是行为变更**：基线两轮恒 step 7，fast 组四次运行给出**三个**不同终点（step 1 / step 3 / step 7，其中两次自基线点前移），按 G11-1 A/B 硬规则「**失败点必须仍是原步**」直接判**不入成绩**（口径不是「没测」而是「测出来的差异是语义差异，不是产能差异」）；③**第三轮（#33 收紧后）同配置仍间歇前移**：r1 三组全 step 7、r2 两组 fast 同时前移 step 3——随后 8 组档位探针把处置空间量完（见 G.7），结论=**默认档没有任何可用组合，#36 把等待腿撤出默认置位**。三量的**成因各不相同，不许合并成一句**：**①墙钟**=测了，但基线跨批就在 93.0 / 107.6 / 177.6s 之间摆，**波动量级 ≥ 组间差**，且 fast 组「更短」是**更早失败**的结果（step 1/3 就停了，后面的等待根本没走到）→ 出不了产能结论；**②成功截图张数**（G11-2 非破坏差分，跑前源面快照、跑后 diff，**不删旧仓任何字节**）=**测了且判定成立**（每组新增 1 张 `step<N>_FAIL_*.png`、旧仓零删除零改动、allure-results 增 0 删 0），**但它是边界判据（没多拍、没动旧字节），本身不是产能数字**；**③固定睡眠实际发生量**（E.3 逐调用 jsonl 派算 Σrequested vs Σactual）=测出来了、省也是真的，**但省掉的是本该等的真等待=语义差异不是产能差异**。**README 边界段原来那三个显式占位已按 lead 明令删除**（02:1x，不留新的占位记号），改成上面这段事实——**不得用任何一轮实测值冒充成绩，也不得写成「更慢」**（那是把行为变更偷换成产能结论的另一面）。逐文件证据见 G.6/G.7，重跑条件见 G.5 第 7 条。
- **#29 兼容包真机实跑（SIT，需授权）**——本波只证到离线 collect-only 与沙箱自证（`test_c4a…`、`test_c6e…`）；「产物在旧仓机器上跑通了」这句在真机跑过之前不许出现。
- **xcheck 名下那批红格已闭合（#34 落地；本节只登记状态与核对命令，不登记分数）**：判据 2 收紧让 xcheck 名下两份文件的旧格前提失效（旧格拿「全程 quiet」当提前正对照、假页 `evaluate` 返裸 `True` 当静默，新协议下都归「从未见忙→睡满」）——**那是判据变更的连带返工，不是实现缺陷**。02:29–02:35 我三次实跑见到的是他**就地修的过程态**（红数在分钟尺度上抖动，所以当时故意只写形态不写条数）；**02:4x 终态：那批红全部转绿，全量门禁全绿、冻结窗 `NO_SOURCE_CHANGE_DURING_RUN` 成立**（读数与基线一律指回 lead 落的四处，**本节不转录、不另立第二处数字面**）。要复核就当场跑：`set PYTHONPATH=src && python -m pytest -q tests/test_legacy_fast_plugin_xcheck.py tests/test_legacy_compat_xcheck.py`。**「判据全绿」这句现在可以说，但含义仅限「离线判据在位且全量门禁全绿」，不含任何产能成绩**（提速结论仍撤回，见 G.6）。**留此条的作用**：它示范了「队友在飞时，快照只写形态 + 给核对命令」这个写法能挺过两轮状态翻转而不变成假事实。


- **R-G2 单张 `page.screenshot()` 耗时占比**——未测；留白那条「省不掉编码」目前只有机理推导，无量数据。
- **措辞红线（E 档/R-5，#33 后收紧）**：**本波不是「提速未取证」，而是「提速结论已撤回」**——任何「兼容包比旧套件快」「垫片提速 N%」「`--fast` 让旧套件更快」的表述（含暗示性的「拿掉等待与截图成本」句式）一律不得出现在 README/CLAUDE/AGENTS/落地注；文档只许保留**机制描述**（垫片替换等待的方式、抑制器丢哪一类 attach）并指向本节与 G.6。本波可主张的只有：「离线判据在位 + 产物字节锁步 + 不带 `--fast` 时逐字节不变」。「超过」只认 E 的三项，其余说法（架构更好/更现代）拒收。

### G.3b P0 之后新增的一格判据（默认置位，2026-11-10 01:0x–01:1x 盘上复核；**等待腿已于 #36 撤回，见 G.7**）

`--fast` 与 `--compat` 产物原先**只注入不置位**=垫片空转。修复面（`legacy.LegacyAdapter.FAST_DEFAULTS` + 产物 conftest `_FAST_DEFAULTS`）确立的口径是**两层不冲突**：

| 层 | 语义 | 当日盘上证据 |
|---|---|---|
| 垫片/插件本体 | `ATLAS_FAST_WAITS` **未置位=默认关**（裸跑旧仓语义不变，设计 A 段硬判据不动） | `legacy_fast.waits_gate`：`_OFF_VALUES` 含空串；`fastplugin._env_on` 同口径 |
| CLI `--fast` | **#36 起清单只剩 `NO_STEP_SHOTS=1`**；等待腿 `ATLAS_FAST_WAITS` **不再代置位**（显式 opt-in，未设时打 NOTE）。显式设置恒优先（含 `off`/`0`/`false` 退旧行为）；stats/min_ms/anchor 从不代置默认（红线 #12 不给未要求的旋钮） | `legacy.py` `FAST_DEFAULTS` 定义处（#36 改值）+ `fast_child_env` 循环 `if not env.get(key)` |
| 产物 conftest | 同规则、同两侧语义（清单同源 `_compat_fast_defaults`）；**不用 `os.environ.setdefault`**——`setdefault` 不覆盖空串 | `emit_compat` 渲染段注释自记该取舍 |
| dry-run 可核性 | 三态 `(explicit)` / `(default --fast)` / `unset`，展示的是**子进程实际拿到的 env**（旧版只看父 `os.environ` 会把「默认已开」显示成 unset） | `cli.py` 注释（#36 同步） |

**判据测的在位情况（#36 后重排，如实，别写成已闭）**：CLI 面——`test_fast_child_env_defaults_only_enable_shot_suppression`（**#36 替换原 `test_fast_child_env_defaults_flip_shim_on`**）、`test_fast_child_env_explicit_wins_including_off`、`test_fast_defaults_only_cover_named_keys`（现有断言含「`ATLAS_FAST_WAITS` 不得被默认」）、`test_fast_defaults_attribute_surface_is_stable`（清单=单元素 `NO_STEP_SHOTS`）、`test_run_pytest_without_fast_never_injects_defaults`、`test_run_pytest_fast_without_waits_env_prints_notice`（NOTE 三态：打 / 显式档位不打 / 无 `--fast` 不打）。产物面——`test_compat_conftest_sets_fast_defaults_before_delegation`、`test_compat_artifact_fast_defaults_align_cli_side`（跨面等值）、`test_compat_artifact_no_env_enables_shots_only`（**#36 替换原 `test_compat_artifact_installs_shim_with_no_env`**：截图抑制装、等待垫片**不**装）、`test_compat_artifact_empty_env_does_not_enable_waits`（替换原 `test_compat_artifact_empty_env_still_defaulted`）、`test_compat_artifact_explicit_waits_opt_in_still_works`（正向对照：显式 `mask` 仍能装）、`test_compat_probe_is_not_vacuous_when_install_leg_breaks`（差分自证，**#36 起需显式 opt-in 才有意义**）、`test_compat_artifact_respects_explicit_off`。**单元素清单的尾逗号是硬要求**：`_FAST_DEFAULTS` 生成器漏尾逗号会把 `(('k','v'))` 退化成 `('k','v')`，产物 conftest 的 `for _key, _default in` 当场 ValueError——#36 改单元素时由 `test_compat_stats_env_lands_jsonl_end_to_end` 当场抓到并已修。
**本节写作过程中我自己漂了一次，照实记**：01:12:27 我扫 `FAST_DEFAULTS` 在 `tests/**` 命中=0，写下「产物侧等值测是空头承诺」；01:14:04 emit-impl 落测后该结论即过期——**「查无」类结论只在扫的那一刻为真**（F-26 把时刻变成动作、F-21 行号漂移同族），故本条记到「测名在位 + 当日一次复跑」这一层：01:16 `tests/test_cli_legacy_fast.py + tests/test_emit_compat.py` 复跑**全绿**（同两条文件 01:15 那次复跑曾见红，是 emit-impl 落测的中间态——**在途瞬时态不是遗留缺陷**，F-26 口径）。**这是两条文件的定向复跑，按本节纪律①只写形态不写条数，且不是基线、不构成第五把尺**；全量基线只认 lead 落的四处同数行。另附一条真实在途事实供归因：`--fast` 与 `--compat` 的默认置位（#30/#31）与本节改写同窗发生，`emit_compat.py`/`legacy.py`/`cli.py` 的 mtime 都在 01:0x–01:1x，任何引用它们行号的旧文一律作废。



### G.4 消费命令（定稿形态，与 README 同源）

```bash
atlas export-pytest <flow.yaml> --fast --compat legacy-suite -o <受控目录>   # 旧仓可直接跑的包；产物不 import atlas；conftest 只代置位截图抑制（#36；等待腿要显式 env）
atlas legacy --fast --dry-run       # 注入形态可核：-p atlas.fastplugin + 绝对 PYTHONPATH + 每个开关标 (explicit)/(default --fast)/unset
atlas legacy --fast [node] [--headed] [--keyword "…"]   # 真实执行：只改注入与 env 透传，不改旧仓任何文件；等待腿未设时打 NOTE
atlas legacy --fast ... 且 ATLAS_FAST_WAITS=mask        # 显式 opt-in 等待垫片（#36 后的正确用法；未设=只截图抑制）
atlas legacy --fast ... 且 ATLAS_FAST_WAITS=off         # 显式退回旧行为——显式值（含 off）恒优先
```

dry-run 的开关行**必须是三态**而不是两态：只看父 `os.environ` 的旧写法会把「`--fast` 已代置位」显示成 `unset`，正是这格显示缺陷让「垫片空转」在 dry-run 里看起来正常（G11-4 R-6 的教训升级版：**可核面若与执行面取数不同源，可核本身就是假的**）。

运行期开关唯一清单=`FAST_ENV_SWITCHES` 全部键（唯一声明位 `LegacyAdapter.FAST_ENV_SWITCHES`，dry-run 逐键可辨识=R-6；**条数不写进本文档**——键清单加一个就漂一处（gap9 F-15 同款教训：等值即计数））：`ATLAS_FAST_WAITS`（默认关）、`ATLAS_FAST_MIN_MS`（默认 400）、`ATLAS_FAST_SETTLE_ANCHOR`（选择器只来自 env）、`ATLAS_FAST_NO_STEP_SHOTS`（成功截图抑制）、`ATLAS_FAST_WAITS_STATS`（逐调用 jsonl 路径）。**分工不合并**：快包自身旋钮的唯一清单在 `emit_fast.ENV_KEYS`（gap9 面），本波垫片开关的唯一清单在此——两处各自等值自证，任何一方不得并成一张表冒充对方。依赖面另含 **`pytest-order>=1.1`**——`--strict-markers` 在场时，产物用了未注册 marker 会直接红，故该依赖必须列进产物 README（否则「旧仓开箱即跑」判据为假）。

### G.5 过程记录（本波收口纪律，照实留档）

1. **设计文本落后盘面、由 lead 同步的六处**（A0/A.4/A 段/B.1/C.5/C.1 的 setup_logging 名位、`ATLAS_FAST_WAITS` 统一命名、进程侧轮询终态、复数 `NO_STEP_SHOTS`、`_case_watchdog`=function、类级补丁）已在 v2 逐处标注来源；**本节 G.1 再补两处 v2 之后发生的漂移**：①conftest 装配形态（文本 `-p atlas.fastplugin` vs 盘上 vendored import + 委托，见 C.1 行）②`FAST_ENV_SWITCHES` 从派单口径的三项扩到实现面全集（条数不入文档）。两处都是**盘上对、文本旧**，纠正方向是本文档，不是代码。
2. **审计面被翻案的一条（scout 自曝）**：#23 评审单初判 `installed_targets` 为「保留（vendor 锁步成本）」，被 `vendor_provenance`/`vendor_shim_source` 现读真文件算 sha256 的盘上事实推翻 → 改判可删，已由 #24 落地（见 G.1 记账形态行）。留此痕迹是因为它示范了「保留理由也要能被证据杀死」。
3. **口述条目按盘上核**：#23 曾收录「删 `scripts/*acceptance*` 中间产物」类历史派工，当日实测盘上无那些文件、且两个在档脚本逐行交集仅脚手架——该条作废落档（评审单 C-07 + H 节），协调者已认账。**引用任何「谁说要删什么」之前先核盘**。
4. **归属与假派工防线沿用 gap9 已立的 F-20/F-30.6**：本波出现冒充 scout 签名的增补件（内容判据识破），处置=凡「X 同意解除他人任务/先行落数」式确认语句都不可能是 X 本人，停手与派工只认 lead 直发原文；引用队友的话作派工前提前先向本人核一次。
5. **数字与窗**：本波四处基线由 lead 自述于 00:15:27–00:17:17 亲跑后落笔（冻结窗成立），scout 只写定性；**A/B 三量的数值位只在 README「当前验证边界」的 gap11 条目**，本节与 CLAUDE FAQ 都不设数值格——**多一处数值位就多一处忘回填的地方**，占位符是给人看的记号，不是尺。**#33 之后补充一句**：那三个占位**本轮不会被动**——两轮 A/B 都不出产数（作废 + 判为行为变更），「等出数回填」这个前提已不存在，占位的语义从「待回填」变为「无有效数，需先收紧再重测」（README 该条已同步这句，避免下波人拿作废轮次的数字来填坑）。

6. **本轮真机 A/B 作废的成因（lead 抓到，不是队友报的）**：`--fast` 与 `--compat` 产物**只注入不置位**——dry-run 五键全 `unset`、已跑的 ab2/ab3 日志里既无 summary 行也无 stats 文件，即**垫片从未运行**。所以那一轮 A/B 不构成「更慢」的证据，也不构成「更快」的证据，**它是一次没测**（三态表里已把该行标成作废）。修复=lead 派的 #30（`legacy.FAST_DEFAULTS`，`fast_child_env` 只补 unset/空值、显式含 `off` 恒优先）与 #31（产物 conftest 同规则，且明写不用 `os.environ.setdefault`——`setdefault` 不覆盖空串，`ATLAS_FAST_WAITS=` 这一格会与 CLI 面分叉）。**留下的一般教训两条**：①「开关默认关」在**本体层**与**入口层**是两件事，写文档时必须分层，否则「打了旗标却什么都没发生」只能靠真机撞出来；②可核面（dry-run 显示）必须与执行面同源——旧 dry-run 只读父 `os.environ`，把「已代置位」显示成 `unset`，等于**仪表盘本身在撒谎**；现在的三态标注 `(explicit)/(default --fast)/unset` 才是 R-6「以为开了其实没开」的真正防线。

7. **#33：收紧不是「加保险」，是判据方向错了（2026-11-10 01:35–01:5x，lead 定稿）**：第二轮 A/B（#30/#31 修复后，垫片真的在跑）出的不是「快多少」而是**失败点从 step 7 漂到 step 1/step 3**。根因在 stats 面钉死：登录/语言切换段 `requested_ms=6000→actual_ms=400`、导航段 `5000→533`，而 **`fallbacks` 恒 0**——探针**从来没有一次判过「未就绪」**。也就是说原硬判据 2「只在判据成立时提前」在语义上漏了一整类等待：**旧仓那些根本没有遮罩守护的等待**（登录、语言值写入、OU 沉降、`reload` 后起步段），它们在任何时刻都满足 `complete ∧ 无可见 mask`，于是被无脑压到下限。这不是「探针不够准」，是**把「无忙信号」当成了「已静默」**——前者是「没观察到」，后者是「观察到结束」，两者证据强度完全不同（判据设计通则：**一条只在「观察到结束」时才成立的提前许可，绝不能由「没观察到」满足**——同 D.1.9 那组负向测拒绝的「loading 页零 mask 就放行」是同一个错误的一般形态）。
   - **处置**：§A 硬判据 2 收紧为「**本次窗口内确实观测到过忙、随后转静默，才允许提前**；整段从未观测到忙 → 按原 `requested` 睡满（fail-safe）」；条件公式降为**必要非充分**；§A「导航后等待」段的防线次序重排（fail-safe 升第一道）；D.3 R-1 补本波实测句。**这三处是本轮正文唯一的改动面**，其余 A0/A–E 与 G11 裁决引用零改动（G 节抬头已如实登记，原先「正文零改动」那句已失真并更正）。
   - **同一轮里我自己漂了一次（照实记）**：01:2x 我据「装配腿 try/except→WARN 不改 rc」向 lead 断言「默认置位这条功能离线不可见，只有真机看得见」，并自建 #32 要补三格——01:22 复扫发现 emit-impl 已落**六格**整套（含 `test_compat_probe_is_not_vacuous_when_install_leg_breaks` 那条差分自证），#32 当场作废。**「查无」类结论只在扫的那一刻为真**（与 G.3b 那次同族，第三次犯同类）。
   - **收紧方向自带的隐性风险（判据设计约束，非盘上缺陷）**：新语义下「实现退化成彻底 no-op（每次睡满）」与「正确收紧」**全体同答**——所有 `actual == requested` 的格都绿、一分不省也没人报警。因此任何「从未见忙→睡满」的新格**必须**与「先忙一拍→提前」成对收录——**这对格就是 no-op 的唯一有效鉴别面**（`test_p0_mask_appears_then_vanishes_returns_early_never_appears_full` × `test_p0_never_busy_page_sleeps_out_requested_original_never_called`，三档版 `test_p0_never_busy_sleeps_out_requested_all_three_tiers`）。**本波我在这条上疏忽过一次（lead 抓到，03:2x 更正）**：我原先把三关的第②关写成「真机 `saved_ms`>0」——**那是选错证据面**：真机上 saved_ms 接近 0 可能是「实现被关成 no-op」，也可能是「这条链路本来就没有被遮罩/xhr/DOM 变化守护的等待」，**两者在真机观测里不可区分**；更要命的是，把 saved_ms 当通过条件**会反过来逼人放宽判据换数字**，恰好是本条要防的事情的镜像。改成：**真机面只验装配生效（stats jsonl 或 summary 行 `calls>0`，这是第一轮空转的原始判据，缺了它整轮可以假绿）+ 「不是 no-op」这一半完全交给离线配对格**；`saved_ms`/`no_busy_observed` 降级为**报告量**。落地测项 xcheck 侧已在 #34 处置。
   - **重测前置（写死，防止拿旧档案数字回填）**：G.6 那份档案是**默认档不安全**的证据，不是产能数据；#33 收紧落码后必须**重新跑完整 A/B**，且验收次序=**E 段末那三关的同文**（①失败点与基线同一步 → ②2a 真机装配生效 `calls>0` + 2b 离线配对格全绿 → ③才谈墙钟）；**第②关一律不得写成 `saved_ms`>0**（选错证据面，理由见上一条）。
8. **「查无」类结论第三次翻在我自己手上（scout 自曝，与 G.3b 的 01:12、xcheck 的 01:21 同族）**：02:1x 我读 `fastplugin.py` 原文，见 summary 行只打 `fallbacks` 不打 `no_busy_observed`，据此在 G.3 ② 登记了一条留白（「最能区分『正确收紧』与『退化成 no-op』的数不在可核面上」）；**随后复扫：`fastplugin.py` 的 summary 行已含 `no_busy_observed=`**——support-impl 在我写下那条留白的同一分钟窗口里就把它接上了。留白已按盘上终态改写。
   **这条的教训不是「别写留白」，而是留白的写法**：①**必须带时刻**（「02:1x 读原文未见」永远为真，「现在没有」会过期）；②**必须写清是哪个文件的哪个面**（文件+mtime 让人可复核，也让人知道该去重扫什么）；③**队友在飞时，任何「查无」在下笔前重扫一次**——本波我自己两次、xcheck 一次，三次全是同一个形态。

9. **我自己写的函数名索引，6 小时内就烂了两格（02:3x 全名扫描发现）**：G.3 ① 里 D.1.1 那行原本引 `test_d1_1_quiet_page_returns_early_original_never_called` 与 `test_d1_1_last_partial_tick_still_probes_quiet_page_returns_zero`——#33 把这两格**改名换语义**（前者拆成 `test_p0_never_busy_page_sleeps_out_requested_original_never_called` + `test_p0_mask_appears_then_vanishes_returns_early_never_appears_full`，后者去掉 `quiet_page_returns_zero` 尾巴），盘上已无此二名。**教训不是「别用函数名」，而是函数名索引必须在引用前跑一次全名核对**：本条已把核对方法写死=`grep` 文档内全部 `test_[A-Za-z0-9_]+` 与 `tests/**` 的 `def test_` 求差集（02:3x 跑过一次，死名 2 个，已修；同一次扫描把 `pytest_addoption`/`setup_logging` 这类**非测试符号**排除在外，否则会误报一片）。**判据一改，改名是必然不是例外**。

10. **一条「过程态快照」后来自己闭合了（02:4x，示范为什么快照要只写形态）**：G.3 ③ 那条 xcheck 红格我登记的是**「有红 + 三次跑的时刻与趋势 + 核对命令」，故意没写终态数**——因为我 02:29–02:35 看到的红数在他就地修 #34 的过程中一分钟一变。**02:4x 终态：那批红已全部转绿**（`tests/test_legacy_fast_plugin_xcheck.py` + `tests/test_legacy_compat_xcheck.py` 定向复跑全绿、全量门禁全绿、冻结窗 `NO_SOURCE_CHANGE_DURING_RUN` 成立）。**按本节纪律①，具体条数与全量数不进本文档**——计数唯一来源仍是 lead 落的四处（README 顶部徽章 / README 边界段 / AGENTS §2 / CLAUDE FAQ），这里只写状态与命令：`set PYTHONPATH=src && python -m pytest -q tests/test_legacy_fast_plugin_xcheck.py tests/test_legacy_compat_xcheck.py`。**这次「留白→闭合」的走法是对的**：如果当时我把红数写死，02:4x 就变成文档里的一条假事实（同 G.3 ② 那条 summary 留白、G.5 第 8 条同族，只是这回闭合方向是有利的）。**「判据全绿」这句话现在成立，但只覆盖离线判据面，不含任何产能成绩**——提速结论仍撤回（G.6），重测三关仍按 G.5 第 7 条那串前置走。

11. **登记旧口径时不要把被禁原句抄回说明里（03:2x 自我约束，与批次五 ㉖ 同条方法的延伸）**：本波我为了说明「改了哪句」，在 D.1、批次五、G.1 里几次把作废的措辞原文抄进解释句——语义没错，但**作废措辞是靠 grep 清点来验收的**，抄回去就永远点不空，等于给下一波留「看起来还有旧语义」的疑云。现统一改成**描述式**（「三项条件齐备即可提前那一形态」「静默条件一成立就补足那一说法」），**验收清单=本波作废措辞七条（lead 给的五条 + 我补的两条），**清单本身不写进本文与任何被扫文档面**——把被禁原句列出来当说明，文档就永远命中自己的闸；清单在 lead 的裁决记录与 scout 的收口备忘里，问一句即可取。**跑法**：对 README、CLAUDE、PRIVACY、AGENTS、本档五个面逐条 grep 那七条措辞，**命中必须全空**才算改干净。**被禁原句的原文只留在裁决记录与 diff 里，不进文档。**

### G.6 负面证据索引（真机 A/B 实测把「默认档」判成不安全——本节是本波最重要的反面留档）

**结论口径（lead 2026-11-10 01:5x 定稿，写死）**：基线两轮恒 **step 7**，而 `mask` 档默认垫片的 fast 组**四次运行出现三个不同终点（step 1 / step 3 / step 7，其中两次自 step 7 前移）**——属**行为变更**；**「`--fast` 比旧套件快」的结论在本波撤回**，README/CLAUDE/落地注一律不得出现提速表述。机制本身没被否定（saved 真实发生），被否定的是**默认档的安全性**——削掉的正是那些「没有遮罩守护、但页面确实还没好」的真等待。

**证据档案：`reports/gap11_ab_20261109/`（gitignore 内、本机不外发；不得删除、不得覆盖——这是「默认档不安全」的唯一原始凭证，也是 #33 收紧后回归对照的基准）**

| 文件 | 角色（时刻与终态按 `SUMMARY2.md` 逐字表核对，2026-11-10 02:0x 复扫） |
|---|---|
| `SUMMARY.md` | 第一轮（01:06，#30/#31 修复前）：`--fast` 组**垫片空转**（五键 unset、无 summary 行、无 stats 文件）——**整轮作废留档，不删不覆盖**；它是「注入≠置位」这类入口缺陷唯一的历史凭证 |
| `ab1_baseline.log` / `ab2_fast.log` / `ab3_fast_stats.log` | 第一轮的三组原始日志（基线 / `--fast` / `--fast`+stats，00:23–00:29）。**这三份不构成任何成绩**：②③两组当时根本没激活 |
| `ab1b_r1.log` / `ab1b_r2.log` | 第二轮**基线**（01:35:58→01:37:48、01:40:59→01:42:33）：失败点**恒 step 7**——本轮的对照基准 |
| `ab2b_r1.log` / `ab2b_r2.log` | 第二轮 `--fast`（01:38:00→01:39:09、01:42:43→01:43:55）：失败点**漂到 step 1（语言值实为空）/ step 3（客户名称 label 未找到）**——行为变更的直接证据 |
| `ab3b_r1.log` / `ab3b_r2.log` | 第二轮 `--fast`+stats（01:39:24→01:40:49、01:44:12→01:45:16）：终态分别是 **step 7 与 step 3**。**同一档四次跑出三个不同终点**（r1②=step 1 / r1③=step 7 / r2②=step 3 / r2③=step 3）=时序非确定性，是行为变更的二次实证，也是「失败点没前移」这类**单轮验收闸为什么不够**的原因 |
| `waits_stats_r1.jsonl` / `waits_stats_r2.jsonl` | **钉死根因的主证据面**（对应 r1③ / r2③）：逐调用 `requested_ms→actual_ms`——`sales_order_default_pt.py:237` 的 `6000→400`（压到 floor）、`:405` 的 `5000→533`；`fallbacks` **恒 0**=探针从头到尾没判过一次「未就绪」。saved 真实发生（r1③ 20265ms / r2③ 13362ms），**但省下来的正是无遮罩守护的真等待** |
| `SUMMARY2.md` | 第二轮定稿件（01:54）：六组逐字表 + **起跑闸现拍证据**（dry-run 两键标 `(default --fast)`、双查无并发、vendor 锁步 True）+ 噪声与异常如实位——**本波真机结论的权威件**，任何引用以此为准 |

**r3 已于 2026-11-10 08:26–08:49 跑完（第三轮，归档 `reports/gap11_ab_r3_20261110/SUMMARY3.md`）**：基线两轮恒 step 7；r1 三组全 step 7，**r2 两组 fast 同时前移到 step 3**（`客户查询/选中失败: 查询结果中无客户代码 M7026817 …`，表格停在未过滤默认行）——同码同配置一轮全过、一轮全挂，前移未归零。**「同档四次三个终点」不是历史个例，第三轮把「间歇」二字实锤了**。

**读这份档案时**要注意的**五**处口径陷阱：①静态上界 176.6s（`lib/pages.py` 61 处 153.8s + `lib/login.py` 16 处 22.8s）**不是成绩位**——用例提前失败只走到部分代码，Σrequested（r1③ 39600ms / r2③ 26600ms）天然小于上界，**别把差额读成节省**；②**墙钟这一量测了，但出不了结论**：同码同参的基线墙钟跨批就在 93.0 / 107.6 / 177.6s 之间摆，**波动量级 ≥ 组间差**，而 fast 组「看起来更短」（69s / 72s）恰恰是**更早失败**的结果不是产能——用例在 step 1/step 3 就停了，本来就没走到后面那些等待。故**单轮墙钟不可作判据**（要中位/多轮 + 同失败点，本波每组只有两轮，连「慢了多少」都不写，「69s vs 110s」这类对照读法一律拒收）；③**截图张数这一量测了且判定成立，但它不是产能数字**：该链是失败链，`step\d+_result` 命名的 attach 根本不存在，每组新增的是 `step<N>_FAIL_*.png`（每组 1 张、消失数 0、allure-results 增 0 删 0）——FAIL 腿不经抑制闸，与设计一致，不许读成「抑制器漏抑制」；而这组差分证明的是**边界**（没多拍、没动旧字节），把它写成「截图面省了 N 张」就是把边界判据当成绩；④**`fallbacks` 恒 0 才是根因位（这一条在 #33 之后要换着读）**：本轮（收紧前）只盯着「有 fallback 才叫探针失灵」的话，「探针永远说好了」这种失败形态在汇总里是**零信号**——没报警≠没问题。**收紧后 `fallbacks` 只数真回退、不含 `no_busy_observed`**，所以 `fallbacks=0` 从此**只证明探针没出错、证明不了垫片没插手**：读 A/B 时这两个数**只作报告量**；**别拿 `saved_ms≈0` 当 no-op 判据**——真机上它同样可能意味着「这条链路被忙信号守护过的等待本来就少」，与「实现坏了」不可区分；**no-op 的唯一鉴别面是离线配对格**（三关第 2b，见 E 段末与 G.5 第 7 条）。真机侧该看的是装配生效证据（jsonl 存在或 summary 行出现、且 `calls>0`）——**那也正是第一轮空转被抓出来的那一格**；⑤**rc 恒 1 是「用例失败」不是「工具跑挂」**，且失败终态一致这一点**不能**被反过来当成功绩（两组都失败≠两组等价，前移的失败点是不同的根因位置）。

### G.7 #36 裁决证据（2026-11-10 第三轮后档位探针矩阵——为什么等待腿被撤出默认）

**裁决（lead 授权主会话自决，2026-11-10 09:5x）**：`LegacyAdapter.FAST_DEFAULTS` 从 `(("ATLAS_FAST_WAITS","mask"), ("ATLAS_FAST_NO_STEP_SHOTS","1"))` 改为 **`(("ATLAS_FAST_NO_STEP_SHOTS","1"),)`**——`--fast` 与 `--compat` 产物仍注入插件、仍关成功截图，但**等待垫片不再默认开**，改为显式 `ATLAS_FAST_WAITS=mask|mask+xhr|mutation` opt-in（未设时 CLI 打一行 NOTE 点名，dry-run 显示 `unset`）。**理由不是「再审慎一点」，是四组真机数据把处置空间量完了**：

| 配置 | 真机样本 | 失败点 | saved_ms | 结论 |
|---|---|---|---|---|
| `mask`（默认下限 400） | 第三轮 r1 3/3 过；r2 2/2 前移 | **间歇前移** | 7923 / 11050 / 6255 / 6347 | 不可默认 |
| `mask+xhr`（400） | #1 step 7；#2/#3 step 3 → 1/3 过 | **间歇前移** | 1974 / 4552 / 1974 | 不可默认（加探针层没解决问题） |
| `mutation`（400） | 2/2 step 7 | 同点 | **0 / -1**（DOM 从不静默→永远睡满） | 安全=功能关掉 |
| `mask` + `ATLAS_FAST_MIN_MS=3000` | 3/3 step 7 | 同点 | 1022 / 1998 / 1996 | 治标：本质=更短的固定等待，收益在基线噪声（93–177s）内 |

**为什么不选其它三条**：(a) 默认改 `mask+xhr`/`mutation`——前者被自己的探针证据否掉（2/3 前移），后者等于把功能关掉还多付探针成本；(b) 抬 `min_ms` 到 3000——它不改变判据语义、只是把提前出口抬高，**同一类「遮罩消失≠数据就绪」的残余盲区仍然存在**，只是这次被 3s 余量盖住了；3 次没挂不构成「3s 在别的链路也够」的证明，而收益 1–2s 低于基线跨批噪声，按 E 段口径**不能当成绩**；(d) 接受默认 + 风险声明——违反 G11-1 硬规则的字面（失败点前移即行为变更），一个会**间歇**改变行为的档位不许以「默认 + 免责声明」发出去。**等待垫片本体、三档、fail-safe、下限、锚点一律保留**（显式 opt-in 的人仍可用），被撤的只有「默认替你开」这一条。

**证据档案：`reports/gap11_ab_r3_20261110/`（gitignore 内、不得删除、不得覆盖）**

| 文件 | 角色 |
|---|---|
| `SUMMARY3.md` | 第三轮六组逐字表 + 起跑闸现拍 + 根因两候选（遮罩消失≠Vue 渲染完成 / 忙信号归属不可判）——**第三轮权威件** |
| `r1_*.log` / `r2_*.log` 与 `.meta` | 六组原始日志与起止/rc（08:26–08:49，前台串行） |
| `waits_stats_r1.jsonl` / `waits_stats_r2.jsonl` | 第三轮逐调用统计（r1 saved 11050ms / r2 6347ms；`no_busy_observed` 11 / 2——#33 的 fail-safe 在干活） |
| `probe_xhr_*.log` / `probe_mutation_*.log` / `probe_floor_*.log` 与 `.meta` | 8 组档位探针（`mask+xhr` 3 组、`mutation` 2 组、`mask`+`MIN_MS=3000` 3 组）——同名 `.meta` 记 `START`/`TIER`/`RC`/`END`，summary 行含 `saved_ms`/`no_busy_observed`/`floor_ms` |

**同族教训（写进本文档而不是只留在消息里）**：**「同配置跑过一次」不是安全证据**——`mask` 在第三轮 r1 三组全过、r2 两组全挂；本轮据此把「单轮通过」从任何放行依据里彻底删掉。**任何声称「等待垫片安全」的结论，必须给出同配置多轮次的多点观测 + 失败点逐点对齐**，缺一不算。

## G.8 调用点规则（site rules，2026-11-10，显式 opt-in 的安全提速）

**动机**：通用档（mask/xhr/mutation）只能表达「页面此刻静不静」，表达不了「这次等待在等什么」——三轮 A/B 的前移全部发生在**数据依赖等待**上（点查询后的 5000ms 是等表格刷新、点菜单后的 3000ms 是等路由）。因此新增一层：给每个等待点（`caller_file` basename + 行号 + 可选函数名）声明它自己的就绪条件。

**机制**（`legacy_fast.py`，由 `ATLAS_FAST_SITE_RULES` 指向 JSON，未设置=行为与 #36 后完全一致）：

| kind | 语义 | 超时/失败行为 |
|---|---|---|
| `js` | 任意 DOM 条件（`page.evaluate` 求值） | 睡满剩余预算，`fallback=rule-timeout` |
| `url` | `location.href` 包含子串 | 同上 |
| `mask_gone` | 无可见 `.el-loading-mask` | 同上（**只适用于遮罩必然存在的点**；无遮罩点等于立即放行，见下方事故） |
| `mask_cycle` | 先等遮罩出现（`appear_ms` 窗口），再等消失 | 遮罩从未出现→睡满（请求未发出的窗口由固定等待兜） |
| `never` | 黑名单：禁止任何提前 | 逐字睡满 requested（与旧行为等价） |

**实现边界（每次真机事故换来的）**：
1. 规则路径用**进程侧 `evaluate` 轮询**，不用 `wait_for_function`——真机实测后者在页面导航中把 6s 预算拖成 9.7s（Playwright 导航后重新绑定帧），破坏「绝不更长」。
2. `mask_gone` 在「本来就没有遮罩」的等待上等于 6ms 放行——语言切换点由此把 step 1 打挂（`get_current_lang` 读到空串）。**没有遮罩的等待必须 `mask_cycle`/`js`/`never`，不许 `mask_gone`。**
3. 「元素出现 ≠ 应用可交互」：登录后 2000ms、hover 500ms、reload 后 6000ms 这类**初始化窗口**用 DOM 存在性判据会提前（后续 `Locator.evaluate` 30s 超时）→ 一律 `never`。
4. 规则只影响**命中点**；未命中点仍走通用档（`no-busy-observed` fail-safe 不变）。
5. **「睡满」必须走原始 `wait_for_timeout`，不许整块 `time.sleep`**：整块 sleep 不喂
   Playwright 同步 API 的消息泵——真机实测（规则档全量第 1 条）6s 整块睡后
   步骤内 `Locator.evaluate` 30s 超时（同一配置在单条 3 组里 1 挂 3 过＝间歇复现，
   一度被误判为「环境异常」）；基线的原生等待与通用档的 25ms tick+求值都不出这个问题。
   改为原始等待后 TC1 连续 3 组失败点全 = step 7。

**真机证据（2026-11-10 同链路 TC1，`reports/gap11_fast_safe_20261110/`，前台串行）**：

| 组 | 模式 | pytest 汇总 | 失败点 | saved_ms |
|---|---|---|---|---|
| baseline_1/2/3 | 基线 | 87.80 / 90.03 / 94.25s | 7 / 7 / 7 | — |
| rules_7 | 规则档 | 81.44s | **7** | 12762 |
| rules_8 | 规则档 | 77.66s | **7** | 11777 |
| rules_9 | 规则档 | 71.89s | 2（`TargetClosedError` 环境异常，垫片如实传播 `unrecoverable`） | 2644 |
| rules_10 | 规则档 | 122.51s | **7** | 12211 |
| rules_11 | 规则档 | 131.86s | **7** | 13721 |

同点组 4/5、`saved_ms` 11.8–13.7s（机制硬账）；墙钟受环境 ±30s 级噪声（rules_10/11 与 baseline_3 同时段均变慢），**单轮墙钟不作判据**。示例规则表：`examples/legacy_fast_rules_iservice.json`。

**结论与边界**：规则档为**显式 opt-in、不默认**（样本不足以证明「永不前移」，默认仍按 #36/G11-7）。TC1 只验证到 step 7（业务现状失败点），step 8+ 的大头等待（`open_doc_list`/`select_customer` 等）规则未验证；兼容包产物侧同一机制已接线但未真机（#29 待授权）。

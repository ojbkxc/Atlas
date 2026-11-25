# Atlas 验证历史（波次链逐条）

> 本文件承接 [AGENTS.md](../AGENTS.md) §2 的**过程叙事**：1448 → 3183 的逐波增量、
> 每波根因与守卫编号。AGENTS.md 只留活约束（Never / 验证纪律 / 架构不变量）与四处计数锚点
> ——2026-11-24 实测单文件 95KB 超注入预算被截断，§3 架构不变量与 §4/§5 直接被截掉，
> 那比缺历史危险得多。
>
> 读法：①「某波改了什么」→ 按计数号搜本文件；②「现在该怎么写」→ 回 AGENTS.md；
> ③「判据在哪个测里」→ 每条都点了守卫文件名。计数口径以 AGENTS.md §2 四处为准。

## 逐波链（至 3183）

（1448 → 1603「补 4 能力」→ 1777 pi-absorb W5/W6（C12 写不盲重试 +48、C9 `waiting` 显态 +99）→ 1797 后续波次 +20：C12-E 判据级拒绝、agent 轨两条腿、dispatch 子进程 rc 表、env_blocked reason 写侧、flow timeout 单列、export_runlog dead-read → 1959 gap9 快脚本产出波 +162 → **2149 gap11 旧仓兼容快包波 +190**：`legacy_fast` 有界语义等待垫片（readyState/min_ms/锚点三档 + 预算制回退）、`fastplugin` 注入（等待垫片 + 截图抑制 + 逐调用 stats）、`--compat legacy-suite` 兼容产物（零 import atlas / vendored 字节锁步 / 旧仓 fixture 面恰等 7 项）、跨切面判据 59 格、旧仓路径双形态（PROJECT+相对 WEB / 绝对 WEB）；同期删减审计 ruff 24→0 → **2171 默认档修复波 +22**：#30/#31 `--fast`/`--compat` 空转修复、#33 忙→静判断据 + fail-safe 睡满、#34 配对判据格、#35 产物 README 正误、#36 等待腿撤出 `--fast` 默认（截图抑制保留）→ **2180 调用点规则 +9**：`ATLAS_FAST_SITE_RULES` 按等待点语义条件放行（js/url/mask_gone/mask_cycle/never 五类、超时睡满、黑名单点禁提前），规则档仍为显式 opt-in → **2216 gap12 自主探索波 +36**：`llm.py` OpenAI 兼容客户端（env 门逐键点名、错误绝不吞成空串、注入 transport 离线可测）+ `autopilot.py` 自主循环（白名单动作 / `classify_risk` 风险门 / 生产闸早于开浏览器 / checkpoint+compile 沉淀 / 决策留痕）+ `explore --auto/--goal/--max-steps/--max-seconds` CLI 入口 → **2404 重构波「产物优先可移植包」+188**：定位改为 pytest 包生成器 + 验证器（2.2 产物 UI 腿可执行化、2.3 分片、2.4 trace 归档、2.5 浏览器矩阵、2.6 iservice 第二业务插件 + 插件缝、松绑、#13/#16/#17 三条红线修复）+ 本波 4 项清理净 +1 → **2428 迁移坑点审核与 URL 净化波 +24**：`scripts/pitfall_review.py` 迁移坑点导入审核塔 + 人工逐条就地启用（+17）+ `credential_gate._sanitize_fragment` URL fragment 分形态（路由型保留/非路由型整抹）+ explore 三个 URL 落点（checkpoint 落盘 / `_observe` 出境 / `compile_trace` 读侧）接线（+4）+ 新脚本入 X 哨兵网（+2）+ 上一笔 `cffd717` 未落数的 1 条漂移 → **2446 多环境接线与开箱可装波 +18**：`envs.json` 账号/OU 环境坐标接进会话默认链（+6）+ 空账号闸门经 CLI 三入口（run / agent --execute / explore）预检投递 rc 2（`resolve_account`/`require_account` 单点）+ `--env` choices 收敛到 `config.ENV_URL_KEYS` 单一真相（+4）+ `atlas init` 默认钉 Atlas 源码根使骨架开箱可装 + README「拿到先跑这三步」+ 随仓样例 `examples/api_sample.md` + docs↔样例漂移守卫（+8）→ **2535 通用知识包组合层波 +14**：`KnowledgeBase` 新增 `base_roots`（组合层）——`load_composed` 在影子层 `load()` 结果上按 key/条目并入通用知识包（顶层 dict 按 key、pitfalls 行按 `id` 去重、components 按组件名，靠前者赢），`pitfalls(domain)` 可见性判据 = `domain in scope or "generic" in scope`（通用包每行 scope 带数据层 `generic` 标记、对任意平台域可见；平台专有行不带、对别的域不可见），helper 单一来源 `plugin_api.common_knowledge_root()` 指 `src/atlas/plugins/_common/knowledge`（通用包 components 15 条 / pitfalls 82 条（2535 时 47 条，`ad988a9` 增补 gen-048~082 共 35 条）/ 接入清单 6 类，提炼依据 `docs/element-ui-generic-knowledge.md`），插件与 `config_add` 骨架三层接线（私有 > 平台 > 通用；`selectors()` 走 `load_composed`，tests/test_config_add.py +2；P1-3 实测 demo/iservice 两个内置插件已接 `base_roots`：`pitfalls('demo')` 30→112、`pitfalls('iservice')` 16→98，`selectors().names()` 7→22 / 14→28）；另 2446 收口后至本波起点间存量补测 +75（缺陷修复波「测试盲区」，此前未落数）→ **2593 wave3 AI 自主测试闭环波 +58**（`explore --auto` 判据四类 assert / 证据落 runs.jsonl / 坑点自动提炼进塔 / ob-1 骨架 env 契约）→ **2618 wave4 原子 IO 波 +25**（`atomic_io` 统一原子写 + 唯一安全追加器，12 文件接线）→ **2633 wave4 P1 批次 +15**（审核塔事务锁 / 读侧 `read_text_retry` / lazy import requests / `save_plan_ctx` 锁内合并 / `historyId` 来源维度 opt-in / trace 断行守卫）→ **2671 wave4 A 轴族可移植面 +38**（`ui_family` 族表 / uf-1 族前缀 DOM 存在性判据 / uf-2 运行时点名 / uf-3 族声明与 `save_component_selector` 顶层键保留）→ **2797 wave4 B/C 轴收尾波 +126**（ae-1~ae-4 CLI 统一渲染层 / ae-5 allure 结果集摘要 / ae-6 产物可读性评审 / ae-7 读者导航 / pf-4 waits tick 常量化 / pf-5 多 flow 一次登录 / pf-6 取证截图降质 opt-in / pf-9 allure-analyze 增量读 / pf-11 catch-up 槽位公式化 / ob-2 骨架 envs.json 补键；含 `atomic_write_bytes` 收口单点）→ **2805 wave4 ae-6 产物 README 改造 +8**（横幅去嵌套加粗 / 运行命令 `pytest -q` / 快慢两形态未接线语义分叉 / 仓库相对路径死链加诚实注记 / 双入口名 / env 旋钮按 `emit_fast.ENV_KEYS` 全量渲染 / 本包文件清单；样例 intent「requests 直调」失真文案一并修）→ **2949 wave5 backlog 收口波 +144**（gap14 待办 + ae-6 剩余留白逐条处置，表见 `docs/gaps/wave5-backlog.md`：en-1/en-3 插件回落 WARN+REQUIRE_PLUGIN / 草稿 identity 平台维度、en-2/ob-4 知识平台维度+族标签、en-4/5/7/13 envs 多入口 / .env 白名单（runtime/legacy+CLI 四处）/ 代理 / 登录协议预检、ob-5 旧仓预检按需化+跳过开关、en-8 runs 分流+读侧聚合、en-10/11/12/15 await/重放/raises/对账、en-9/en-14 双入口文档/exports root-anchored、ob-3/6/7/8 生成器 domain/产物 README/doctor 清单/首 bug 文档、uf-5~8 遮罩单点/样例族中立/playbook 族标签、ae-6 四产物可读性补齐、pf-3 语义等待真机取证、complex-flow 脚手架；ob-5 的 API 半面按实留白待插件 seam；
  → **2984 缺陷修复波 +35**（代码分析逐条核出的 12 项，均带回归测：P0 `ApiEvidence` 重放 handler 每步 append 从不清理 → 第二步失败重放第一步请求、归因错误（`env_blocked` 被读成 `interaction`），改单槽 `_step_replay` 每步替换 + 3 条回归；打包 wheel 补 `atlas.plugins.iservice`/`_common` package-data 与 `_common/__init__.py`（96→108 文件，装后 `contracts()` 不再抛「契约文件缺失」）；两内置插件 `knowledge()` 接 `base_roots`、`selectors()` 改 `load_composed`（pitfalls demo 30→112 / iservice 16→98，selectors 7→22 / 14→28）；runtime 按插件取 provider 与注册（iservice 不再走 demo 探针）、`session_defaults` 缺键点名插件与键；`runtime.build_registry()` 单点收敛使compile 预检与执行侧同源（含双向锁步测与行为面 rc2 证据）；ruff 8 处清零并入 CI 双 job；裸 pytest 4 文件收集失败修 `pythonpath=["src","."]`；哨兵打错命名空间两处（`test_gen` → `plugin_api`、`test_shot_quality` → `atomic_io.os`）；唯一失败用例 `from_root(None)` 回灌本机 `.env` 改 `tmp_path` 隔离；补 screenplay 门面 `__all__` 逐项核对测；影子层遮蔽两处组件读取改 `load_composed`（`emit_pytest._default_components`、`review_code` knowledge 模式——否则 15 个通用组件对生成器/评审不可见，F-10 声明位闸与 SAFE-*/MAINT-* 误判合法用例；`pitfall_review.py` 保持影子层：它是读私有→翻 status→整份写回私有库，用组合层会把通用条目复制进私有库污染资产）；
  → **3012 结构性守卫波 +28**（2026-11-16 第二波：把「写在注释里的纪律」换成会变红的判据，三处均带变异验证）：
  **①知识读层守卫**（`tests/test_knowledge_layer_guard.py`）——`load()` vs `load_composed()` 此前**类型上不可区分**，
  `ui_family` 只在通用包、由 `ui_family._table_path()` 直接按路径读，不是遮蔽面——不可强行要求 `load_composed`），
  **②入口输入预检序列单点**（`runtime.precheck_session_inputs` + `tests/test_preflight_single_point.py`）——
  `DemoRuntime.__enter__` 也委托它（direct-use 兜底）。守卫分两层：AST 认「会开启真实会话」的函数（按**能力**认——
  **③断言明细生成期闸**（`emit_pytest._reject_unknown_assert_kinds` + `tests/test_assert_kind_gate.py`）——
  （既不渲染也不留 TODO，生成物测试全绿而断言凭空消失，直接撞红线 #7）；UI 腿上带 `result_code` 同样静默丢弃
  （当前 45 个 flow 实测 0 例，但结构可达）。闸门挂在 `render_test_module` 入口（快慢公共收口）而**不在三个循环各补
  `else`**（逐处补 = 让新增 kind 依赖记得改三处，正是本仓被咬多次的形态），取值域 `ASSERT_KINDS` 与渲染循环消费集合
  **④验证计数守卫**（`tests/test_doc_verification_count.py`）——计数四处此前靠 7 个 commit 人肉同步且漏过多次；
  → **3022 CLI 结构治理波 +5**（同一波收尾：`main()` 473 → **102 行**，27 个 `if args.command ==`
  `_legacy`(40)、`_run`(87)、`_agent`(54)、`_summary`、`_allure`、`_allure_analyze`、`_machine`、
  `_contracts`、`_selectors`、`_tasks`、`_compose`、`_knowledge`、`_compile`；`init`/`config` 留在
  `main()` 顶部（它们在 AtlasConfig/plugin 装配之前就要 return，进表会强迫先装配不存在的项目根）。
  **行为零变化**：全量 3022 passed / 0 failed，与原 2984 相比净 +38 全部是新守卫，无一条既有用例被改判。
  → **3034 run 轨留痕修复 +5**（真机抓出→修→真机复验闭环）：
  `atlas run` rc 1 但 `reports/runs.jsonl` **一行都没有**——`RunRecorder` 由 `dsl.run()` 在会话
  **就绪之后**才构造，`__enter__` 抛错时 `with` 不调 `__exit__`、`run()` 从未被调用，recorder
  （写盘坏/函数级异常）双保险吞成一行 WARN，绝不顶掉真因。`AwaitingHuman` 显式跳过（走 C9 的
  （变异实证：新造 `entry_broken` 被点名）；④rc 不变、异常原样抛出；⑤写盘失败不替换真因
  （变异实证：把 `_record_entry_failure` 换成抛 OSError，真因 `TimeoutError` 仍胜出）。
  **真机复验**：同一 flow/环境/失败点，`runs.jsonl` 77→**78** 条、2026-11-16 由 0→**1** 条、
  → **3040 真机验证通过 + 虚构账号闸门 +11**（2026-11-16 第三波：把「真机未通过」这件事查到底）：
  **根因不是 SSO、不是网络**——未设 `ATLAS_PLUGIN` → `resolve_plugin()` 回落 demo → 其
  `session_defaults()` 给数据源里的虚构账号、`credential()` 拿**空密码** → 空密码提交真实
  `type=hidden` 不拦登录，排除；② 怀疑凭据失效 → 单独 import 旧仓 `login.py` 跑 `do_login`
  **成功**（25s 拿 ticket），排除；③ 怀疑选择器过期 → 与旧仓 `LOCATORS` 对比一致，排除；
  **真机结果**：`sp_order` 只读套件 **11/11 passed**（SIT，记录落 runs.jsonl 2026-11-17T02:52–03:10Z）。
  **新增闸门** `runtime.require_real_account_not_demo`（挂在 `precheck_session_inputs` 单点，
  `ATLAS_PLUGIN=demo` 放 / 密码已接管放 / 无密码来源链两条都认）。
  **同时守住字面量单一来源**：`demo_user` 等业务字面量仍只住 `knowledge/session.json`——闸门与
  `test_demo_session_defaults_are_single_source` 扫到）。
  **计数四处（README 顶部徽章、README「当前验证边界」、本条、CLAUDE FAQ）**由 lead 统一收口，
  （2026-11-09 波次内出现 16+ 次伪造件，两份冒充了队友的整份对账报告；其「盘上事实」往往为真——
  `http.server`、真 socket、子进程真请求——**localhost 也算网络**——不得进门禁。「真的发一次请求」半段恒为
  `ATLAS_SESSION_DIVISION` 覆盖为真实值（与 `ATLAS_ENV_PREFIX=ISERVICE_` 一并在环境配置里）；
  （显式 `ATLAS_SESSION_*` env 优先于 `--bu`）。
  **2026-11-12 起第三层来源接通**：`--env <名>` 会自动读私有 `envs.json` 的
  `environments.<env>.account/ou`（真实业务插件 `plugins/iservice` 消费；SIT/UAT-EU/PROD-AM/PROD-HK
  `iservice`、切勿使用测试账号」，故**不填** `zhangfj73`，要跑须显式 `--account` 并先补 OU）。优先级：
  `ATLAS_SESSION_*` > 私有 `session.json` > 私有 `envs.json` 环境坐标 > 包内结构默认。
  `check_legacy_paths` 三条 #103 预检同口径；`run`/`agent --execute`/`explore` 三个登录入口都调它），
  `DemoRuntime.__enter__` 仍调同一函数兜直接使用运行时的调用方（examples/脚本）。
  `credential("")` 命中 `ISERVICE_PWD_` 空值键，白烧会话且归因漂移 #103。
  → **3053 静默丢弃第四例 +19**（2026-11-17 结构审计：把「静默丢弃」当成一个族来扫）：
  `scheduling.apply_failure_policy` 有 `elif mode != "keep": raise`（**正确**，不是缺陷）；
  `review_code` 两处是展示层分支（有默认）；`emit_flow._build_step` 的 **body 分流是真缺陷**。
  `BodySpec.kind` 文档取值域 5 个（model.py:32-36），分流链只认 4 个且**无 else** → 实测塞
  `bogus_kind` 得：**不产 body、不报错、不进 manual_assertions**，`atlas gen` 出包 rc 0，
  `tests/test_body_kind_gate.py` 与 `model.BodySpec` docstring **逐项锁步**（新增 kind 必须两处同改）。
  （`none` 误伤 + 文档锁步 + 生产点越界，即「过严」也挡得住）。
  （含「闸门先于写盘」「别处不得另写第二份判据」源码扫描）；变异实证：删闸门 → 3 红。
  → **3057 rc 契约守卫 +4**（2026-11-17 同一波：README 的 rc 表此前是**零机器检查**的散文）：
  `tests/test_cli_rc_contract.py` 双向锁步：①表里声明的每个 rc，`cli.py` 必须真有产生它的
  `return <常量>` 路径（防「文档承诺了代码不产生的退出码」，消费方按它写分支永远等不到）；
  （解析不出去 = 守卫恒真）。双向变异实证：代码加 `return 7` → 精确报未声明；README 删掉 124 →
  → **3061 CI 门禁诚实性 +4**（2026-11-17：CI 配置层也有洗绿形态）：
  `.github/workflows/ci.yml` 的 Offline gate 里 `python -m atlas doctor || true` 把两件事
  `|| true` 一吞，后者**永远不会让 CI 红**（红线 #7 在 CI 配置层的形态；CI 配置此前零测试覆盖）。
  `boundary_assets_consistent` 必须为 true（这两项只看仓内资产，与真实项目无关，坏了就是
  **踩坑记录（值得留）**：第一版用 `subprocess.run(..., text=True)` —— Windows runner 上按
  `ensure_utf8_streams()` 只管子进程自己的 stdout，父进程怎么读是另一件事。改成按字节读 +
  `atlas doctor || true`（**跳过注释行**——第一版按纯文本扫，把解释它为什么坏的注释自己判红了）；
  （否则裸 clone 恒红）。变异实证：加回 `|| true` → 精确红；把 `legacy_pages` 塞进 required → 红。
  `boundary_assets_consistent=false` → CI 会红（已还原）。
  → **3067 快包 setup 期无痕修复 +6**（2026-11-17 收口 gap9 F-33.4 ①）：
  `atlas scheduling` 的巡检靠 runs.jsonl 判上一条状态 → 缺席被读成「不存在」（#106/#86）。
  `_runlog_status_reason` 同一把定性刀（不按文本猜，F-5/红线 #7），落盘失败只 WARN 不顶真因。
  （`atlas.reporting.summarize` 实测可读，`status.env_blocked==1`、`runs==1`）。
  **双向变异实证**：①删掉补行分支 → 模块级那格 0 行（原缺陷精确复现）+ 读侧同构测红；
  → **3070 docstring 失效路标守卫 +3**（2026-11-18：注释指向不存在的实现）
  `failures.py:285` 写「dsl `_assert_reconcile`」→ 真实是类 `_ReconcileAssert` 的 `.run`；
  `legacy_fast.py:641` 写「传 `_RULE_FALSE_JS`」→ 本模块无此常量（只有 `_RULE_KINDS`/
  `_RULE_MASK_*_JS`）；`runtime.py:278` 写「`_precheck_session_inputs`」→ 真名
  `precheck_session_inputs`（**这条是我 11-17 亲手收敛预检序列时漏改的**，说明这类漂移
  `_save_fail_screenshot`）与产物侧符号（`_atlas_fast`/`_fast_runlog`/`_write_gate`），
  （名字搬进本仓后必须从 `_EXTERNAL` 删，否则掩盖真检查）；②核心判据（失效引用点名
  → **3073 文档模块表补全 +3**（2026-11-18 同一波：地图缺格）
  `Claude.md` 的模块表是接手者的**第一张地图**，但此前漏了 **14 个模块**
  （77 个中的 18%），且不乏核心件：`ui_family.py`（族表/唯一取值口）、`llm.py`
  （autopilot 的决策面）、`generator/emit_compat.py`（vendored 垫片唯一写点）、
  `cli_render.py`（机读 JSON 唯一出口）、`plugins/iservice/plugin.py`（第二个真实业务域
  （本仓对 `generator/`/`scheduling/` 用后者：一行概述 + 逐个点名，判据不能只认独立行）；
  → **3085 产物 docstring 安全性 +12**（2026-11-23：真机抓出的**产物侧**缺陷，不是本仓源码）
  **现象**：`atlas gen examples/api_sample.md --with-pytest --fast` 出包 rc 0，但产物
  `atlas_pages.py` 在 `-W error::SyntaxWarning`（CI 常规档）下**直接 SyntaxError**。
  **根因**：通用知识包 `components.json` 的 notes 里写着正则 `/确\s*定/`，渲染层把它原样插进
  `来源 flow：{flow_id}`、compat 模块 `flow {flow_id}` 与 `{s.intent}` 也是裸插值
  （`flow_id` 来自文件名/标题，含反斜杠即炸）。
  **修法两层**：①判据单点 `dsl_utils.docstring_text`（转义反斜杠与双引号，`__doc__` 取值
  `-W error::SyntaxWarning` 编译全部产物源码，坏产物点名文件拒绝、零残留。为此两条入口都改成
  （`_atlas_fast.py`/`conftest.py`）洁净 + `write_fast_artifacts` 同样在 `mkdir` 之前拒绝坏模板——
  → **3089 探针身份与快底座护栏 +4**（2026-11-23 同一轮分析：`atlas gen` 写出的**探针产物**）
  **缺陷**：`probes/<名>.candidate.py` 的类名/entity 名此前在 `generator/__init__.py` 里就地拼
  `slugify(摘要或文件名, fallback="GeneratedProbe") + "Probe"`，三种形态都不报错：
  （兜底词本身已带后缀）；② 中文里夹的 ASCII 残渣（`订单 \s 查询`）→ 只剩 `s` →
  `class sProbe(DataProbe)`：既不是业务名也不是兜底名；③ **致命那条**：`slugify` 保留 `-`，
  `class get-web-example-customer-codeProbe(DataProbe):` —— `atlas gen` rc 0 写出一个**语法错**
  **修法**：判据单点 `emit_probe.probe_entity_name`（摘要 → 文件名 stem → 兜底，取第一个够长的
  `_pascal`/`_ident_slug` 收敛成对 `dsl_utils.pascal_identifier`/`identifier_slug` 的别名
  （同一把刀，不留第二份实现，调用面与既有契约测不变）。同轮把写盘前严档自检扩到
  `emit_fast.write_fast_artifacts`（`_atlas_fast.py`/`conftest.py` 在 `mkdir` 之前检查）。
  `GetWebExampleCustomerCodeProbe.candidate.py` 类名合法，6 个 `.py` 全部严档编译通过。
  → **3095 生成↔导出断言闭环 +6**（2026-11-23 同一轮分析：**同一产品的两半对不起来**）
  **缺陷**：`emit_flow` 给每个 api 步骤写 `save_as: resp_1_get-web-example-customer-code`
  `# TODO(人审): 待接断言（人审）：…`，产物看起来完整、rc 0，那一步实际没有存活断言。
  （运行时不受影响：DSL 的 `_lookup` 按 `.` 切段索引 ctx，`-` 键能解析；坏的只是「生成链 →
  **修法两端都收**：①根因侧 `emit_flow._save_as_name` 的 `{slug}` 改用
  `dsl_utils.identifier_slug`——生成的 ctx 键是合法标识符（`resp_1_get_web_example_customer_code`），
  **已经生成过**的 hyphen 键 flow 不会因这次改动丢断言。
  `emit_pytest_from_flow` → 产物无「待接断言」TODO、每步都有 `assert result.ok` ②`_save_as_name`
  `emit_flow._save_as_name` 保留末道防线给直调 API 的用户。
  `save_as: resp_1_get_web_example_customer_code` 且断言行随之改形；再 `atlas export-pytest`
  `--naming "s{n}_{slug}"` → rc 0 且落点 `s1_get_web_example_customer_code`。
  → **3100 flow 级断言导出覆盖 +5**（2026-11-23 同一轮分析：**flow 级断言被整块吞掉**）
  **缺陷**：`assert:` 块支持两形态——文本表达式与 **Task 形态**（`- task: assert_api` +
  `operation/args/expect`，`dsl` 静态段两种都校验）。旧 `_flow_asserts` 对每条 `str(raw)` 后按
  `save_as` 归属：Task 形态被 `str()` 成 `"{'task': 'assert_api', …}"` 永不匹配 → **丢弃**；
  `atlas export-pytest` rc 0、产物看起来完整，而 flow 里的 API 断言块（含 `expect`）一个字都不剩
  **修法**：判据单点 `emit_pytest.flow_assert_notes`（`_flow_level_assert_notes` 的对外名）把
  `_render_test_module_fast(..., flow_notes=)` 挂在**模块头**——挂某一步会随该腿形态一起消失
  （慢模式 UI 腿是 skip 骨架，不渲染断言注释，试过：`fast=False` 快照当场漂移，故改模块头）。
  `flow_notes` 缺省 None ⇒ 旧输出逐字节不变（#115 零涟漪）。
  `document_no` 与 `assert_api`（改前两者都没有）。
  `# TODO(人审): 待接断言：document_no != ""` 与 `…flow 级断言块 task=assert_api operation='sale_order_list' expect={…}`。
  → **3104 api_ms 逐步归因 +4**（2026-11-23 同一轮分析：**跨步粘性状态**）
  **缺陷**：`CallApi.last_timing_ms` 只有**真发请求**时才更新（粘性值），旧 engine 在每步结束时
  （`assert_api` 读 ctx、自定义 `_perform_api` 走自己的传输、写闸拦停后的步骤）会把
  **上一步的接口耗时**记到自己头上——`runs.jsonl` 声称「这一步接口耗时 120ms」而它一个请求
  **修法**：归零单一入口 `CallApi.reset_timing()`（`abilities.py` 里 `last_timing_ms` 赋值点
  `_perform` **之前**调它（`tests/test_step_api_timing_attribution.py` 第③条钉住「engine 不得
  → **3108 生成期字面凭据抹除 +4**（2026-11-23 同一轮分析的**红线 #1 输入面**）
  **缺陷**：`atlas gen` 的产物是**要交给别人的文件**，但旧实现只闸 headers（C4①）。文档作者在
  `flows/<id>.yaml` / `contracts/*.json` / `probes/*.py` / `pytest/test_*.py` / `report.json`
  （实测 `flows/get-web-example-customer-code-access_token-tok123.yaml`，同一份包里 7 处命中）。
  **修法两层**：①`generate()` 在 `parse_source()` 之后、任何产出之前调
  `_redact_endpoint_secrets`——`path`/`base` 走 `credential_gate.sanitize_url`、`summary` 走
  `sanitize_text`（判据单点，生成器不留敏感键名单），抹了什么**如实**进 `manual_assertions`
  （人要能区分「文档本来写的就是 `<redacted>`」与「这里原本有个字面凭据」）；②
  `sanitize_url` 补 **userinfo（`user:pass@`）整段 redact**——那是经典凭据形态却**没有敏感
  `…-access_token-redacted.yaml`，`report.json` 的 manual 段写明「已占位为 `<redacted>`」。
  → **3113 JMeter 迁移凭据闸 +5**（2026-11-23 同一轮分析：**另一条外部输入链**）
  **缺陷**：`scripts/import_jmeter.py` 只给 **HeaderManager** 过闸（C4①），`HTTPSampler.path`
  `path: /web/example/customer/code?access_token=TOK123` 与
  `path: https://ops:S3cretPwd@example.com/api/x?page=1` 逐字进了 YAML 与 `-o` 文件，
  `atlas gen` 同族、不同链）。
  **修法**：`apply_credential_gate(flow)` 在 `yaml.dump` **之前**（同一确认位）对每步
  `path` 走 `credential_gate.sanitize_url`、`intent`（sampler 名）走 `sanitize_text`；
  **终端回显**（`item["path"][:110]` 直接打含 token 的 path）→ `sanitize_url`；写盘那份
  **故意保留原样**（该脚本用途就是把真实报文交人审建模板），并留注记说明两者为何不同。
  `WARN: 2 处 path/intent 含字面凭据，已在写盘前占位为 <redacted>（只报位置不报值）：step1.path, step2.path`。
  → **3118 review-code 吞异常分流 +5**（2026-11-23：**产品自身的噪声缺陷**，不是洗绿）
  **现象**：`atlas review-code src` 出 `total_findings=47 / critical_findings=27 / avg_score=94 /
  （`session.__exit__` 关闭失败不得顶掉原始异常、`agent._record` 留痕写失败不得顶掉执行真因、
  `cli._record_entry_failure` 同理）——本仓纪律正是「吞异常必须有理由」，理由就写在注释里。
  `except Exception: pass`）反而看不出来。
  **修法**：新增规则 `GREEN-001-DECLARED`（severity **low**，rationale「已写理由，需人审确认
  `finding_count`（不放行也不消失）。刻意只认「有没有写理由」，**不猜成不成立**（人审的事）。
  （已声明理由被打回 critical），①④⑤ 保持绿；恢复后 sha256 一致。
  `session.py`/`agent.py`/`plugins/demo/legacy_case.py` 由 59(F) 升到 99(A)，
  → **3126 review-code SAFE-001 噪声面 +8**（2026-11-23 同族续：**文档面不该按代码面判**）
  **现象**：`SAFE-001`（硬编码凭据/URL，critical）逐行正则、不分「代码 / 注释 / docstring」。
  ``代理形态 `http://user:pass@host:port` ``、`credential_gate.py` 注释里的
  ``# `https://user:pass@host/x`：userinfo 是经典凭据形态…``、产物 README 渲染的
  `export ATLAS_FAST_PASSWORD='<密码>'` / `export ATLAS_BASE_URL='https://<host>'`
  （`<…>` 占位比 3 字符阈值长，正则照样命中）。16 个 critical 里 6 个是这类噪声。
  **修法两层**：①`_strip_placeholder_segments` 先抹 `<…>`/`${…}` 段再判（**抹段不是整行跳过**：
  `SAFE-001-COMMENT`（severity **low**、仍记一条进报告）；docstring 行号由
  `_docstring_lines`（AST 遍历模块/类/函数的首语句 Constant 区间）给，**不靠猜三引号文本**。
  **一处自曝的测试缺陷（值得留）**：第一条占位测最初写成裸 shell 行
  `export ATLAS_FAST_PASSWORD='<密码>'`，该行不是合法 Python → 扫描器先撞 `MAINT-006` 语法错、
  （`HINT = "export ATLAS_FAST_PASSWORD='<密码>'"`）后才真正区分新旧；
  → **3134 裸吞留痕 + 聚合 grade 修复 +8**（2026-11-23：把上一条登记的 10 处**逐条核完并处置**）
  **裸吞分类**：10 处里两类——
  `_record_decision` 同族（决策 jsonl 少一行，「为什么走了这条动作」正是这份文件的唯一用途）。
  **同轮修复聚合 grade 初始化缺陷**：`max_grade = "F"` 起步而 F 的 rank 已是最大值 4，
  `>` 比较永不成立 → `summary.grade` **恒为 F**（实测 critical_findings=0、avg_score=99 的报告
  →F），变异实证：改回 `"F"` → 2 条精确红。
  → **3140 ASSERT-006 文档噪声档 +6**（2026-11-23 续：**评审器被自己的文档打了低分**）
  **现象**：`ASSERT-006`（注释掉的断言，high）只看行首 `# assert`。但规则文档本身大量用这种
  `# assert len(x) > 0: len() is the call…`）。`review_code.py` 自身 13 条 findings 里 **8 条**
  **修法**：判据单点 `_is_commented_assert_code`——`# assert` 后的尾巴命中任一即按「讲解」处理
  （不产 finding）：①中文/英文讲解词（是/形如/is the/the call…）②多条 assert 连写（真被注释的
  `pytest.approx(`、省略号）。真被注释的断言代码（`# assert resp["x"] == "ACTIVE"`）不受影响
  （尾巴是代码形态）。**规则侧顺带自洽**：源码里三处真讲解注释补了「形态示例」尾注，让判据在
  （改前 8 条）。变异实证：判据换成恒 True → ②③④⑥ 精确红（①保持绿，正是它的职责）。
  （吞异常有理由/注释里的凭据形态提醒），0 critical、0 误伤。
  → **3145 MAINT-001 适用面 +5**（2026-11-23 同族第三例：**规则打了不该打的代码**）
  **现象**：`MAINT-001`（硬 sleep，high）建议「改用 `waits.py` 语义等待」——那是**测试代码**的
  `atomic_io` 的 `time.sleep(_READ_RETRY_DELAY_S)`（读重试退避）、`dsl` 的
  `time.sleep(interval_ms / 1000.0)`（`await: poll` 间隔）、`explore` 的命令轮询、
  `legacy_fast` 的等待垫片——**这四个正是语义等待的实现本体**，报「请改用 waits.py」等于
  **修法**：判据单点 `_is_test_module(path)`（文件名 `test_*.py`/`*_test.py`，**或**任一路径段
  `_PytestAstScanner` 加 `is_test` 门（默认 True = 不知道时按测试口径报，fail-visible），
  → ①④⑤ 精确红，②③ 保持绿（正是它们该有的行为）；恢复后 sha256 一致。
  `grade A` 保持；剩余 27 条全是 low 档真留痕（含 1 条 GREEN-004 的 `# type: ignore`
  → **3159 产物侧缺陷族手册 & 文档索引守卫 +3**（2026-11-23：把 13 波沉淀成可复用手册）
  **手册** [docs/lessons-artifact-side.md](docs/lessons-artifact-side.md)：本会话 13 波全是
  **产物侧**缺陷（在 Atlas 写给别人的文件里，不在本仓源码），共同点「离线全量绿 / rc 0 /
  （测不到自己写出去的东西 / 两端各写一半 / 默认档不报错）、症状→族→判据单点→守卫的 13 行
  （`first-bug`/`migration-*` 4 份/`legacy-adapter-setup`/`knowledge-migration`/`dual-entry-flows`）
  **守卫** `tests/test_reader_guide_index.py` 3 条：①`docs/*.md` 每份必须进索引
  → **3183 时间戳口径复核（无缺陷，仅留结论）**（2026-11-24 同一轮分析的收口）
  **runs.jsonl 与快包底座是 UTC**（`recorder.py:61/100`、`emit_fast.py:491/560` 都
  `datetime.now(timezone.utc).isoformat()` → `+00:00`），**调度核全链 UTC**
  （`state.py`/`dispatch.py`/`cron_parse.py` 一律 `timezone.utc`）。
  （`cli_render.py:336`）与「同一文件内字符串排序」，**没有任何跨机比较或 age 计算**
  （`allure_report.py:80` 的 `fromisoformat` 只读 runs.jsonl 的 UTC 形态）。
  → **3183 editable 安装指向守卫 +3**（2026-11-24：**环境被另一个 clone 悄悄接管**）
  **本机实测**：为复现 CI bare-clone，我在 `D:\tmp\atlas-r34\clone` 里跑了一次
  `pip install -e ".[dev]"`——**全局 Python 的 `atlas-test-platform` 可编辑安装从此指向那个
  **修法两层**：①`test_allure_e2e_offline` 在子进程前查 `find_spec("atlas")`，缺则
  `pytest.skip` 并说清「本机未安装（CI 走 `pip install -e .` 后不触发）」——与既有的
  `import atlas` 解析路径必须落在**本仓** `src/atlas`（不是别的 clone）、`PYTHONPATH=src`
  **守卫自身两处缺陷当场修掉（留档）**：①子进程命令写成
  `-c "import atlas, print(...)"` —— Python 里那是**语法错误**（`import a, b` 要求 b 是模块名），
  `import atlas → D:\GitHub\Atlas\src\atlas\__init__.py`，`test_allure_e2e_offline` 由红转绿
  （PYTHONPATH 有/无两种口径都跑过）。
  → **3180 路径大小写可移植性 +3**（2026-11-24 CI linux 3.13 **第四例同族翻车**）
  **现象**：`tests/test_doc_module_table.py` 写 `DOC = REPO_ROOT / "Claude.md"`，磁盘是
  `CLAUDE.md`——Windows 文件系统大小写不敏感 → 本地 3177 全绿；ubuntu runner 区分大小写 →
  `FileNotFoundError: '/home/runner/work/Atlas/Atlas/Claude.md'`，3 条守卫当场红
  （CI 日志实数 `3 failed, 3163 passed`；凭据从 Windows 凭据管理器取，`gh` 装本地二进制
  `D:\tmp\gh`，未走 winget/未新建 token）。
  **同族根因（四次一次比一次隐）**：判据的输入只对出题人那台机器成立——BOM、bare clone 缺
  **修法**：`DOC` 改 `CLAUDE.md`；新增 `tests/test_portable_path_case.py` 3 条——
  （不一致点名「写的名 → 真实名」）②正向对照（合成 `Claude.md` 必须被抓）③下限（扫到 ≥1 处
  **两处自曝的守卫缺陷（都当场修掉，值得留）**：①第一版按「任何 STRING token 的行」排除
  **不红**；改成只排除**多行**字符串（`start[0] != end[0]`）后变异精确红。②下限原写「≥20 个
  → **3177 产物 first-run 可执行性 +4**（2026-11-23：新用户拿到产物的**第一个动作**钉住）
  **零守卫**——`docs/first-bug.md` 钉的是「拿到 Atlas」的最短路，`test_portable_sample_export`
  → ④精确红。
  → **3173 审核塔 triage 材料落点守卫 +4**（2026-11-23：53 条待审草稿的处置清单）
  （`?ticket=ST-…`）与单号按钮——`reports/` 在 .gitignore 里故不会被提交，但挪到
  `docs/` 就全进库；正确落点是 `D:\tmp` 本地留档（AGENTS「留证放 D:\tmp 并说明」条款），
  → **3169 JSONL 写侧 BOM 零残留 +4**（2026-11-23 续波：读侧钉死后的**写侧收口**）
  **产物交给别人**，消费方未必都用 `utf-8-sig` 读——`pytest.ini`/`pyproject.toml` 的解析器、
  `examples/`、`flows/`、根目录文本）——**零 BOM**（`write_text(encoding="utf-8")` 从不产
  （含 tests/scripts/knowledge/examples/flows/根）②源码不得有 `encoding="utf-8-sig"` 的
  **写**调用（读侧两处正当：`read_jsonl_text`/`config` 的 BOM 探测）③写侧不得用 `utf-16` 系
  （PS5.1 重定向默认会产 UTF-16；按**调用形态**区分——同一行 encoding 字面量与
  `examples\bom_probe_tmp.json`；删除后恢复全绿（探针已删净）。
  → **3165 JSONL 读侧 BOM 口径 +6**（2026-11-23：**审核塔 2 条草稿只看得到 1 条**）
  **缺陷**：Windows 上「记事本旧版另存为 UTF-8」与 PowerShell 5.1 `Set-Content -Encoding UTF8`
  `read_text_retry(path, encoding="utf-8")`——首行于是变成 `\ufeff{"id":…}`，`json.loads` 抛
  `JSONDecodeError`，被各自「残行跳过」分支**静默吞掉**。实测复现：带 BOM 的
  `experience_drafts.jsonl` 让审核塔只看到 2 条草稿里的 1 条（不是崩，是**那条草稿从此不存在**）；
  `runs.jsonl` 同形态则让 summary/allure/report 少一整条 run 而三处都「看起来正常」。
  **修法**：判据单点 `atomic_io.read_jsonl_text`——`utf-8-sig`（无 BOM 时与 `utf-8` 逐字节
  `try/except UnicodeDecodeError` **五份复制**（本身即 #92 坏味道）一并收敛到这一处。
  `encoding="utf-8"` 读 JSONL 的旧形态。
  （`utf-8-sig` + 先看 BOM 字节）——同一现实第二次长在本仓，这次钉死在读侧单点。
  **①读侧容错（7 条）**：`summarize`/`export_runlog`/`allure` 三条读侧历史上有 #131/#112 两波修
  `UnicodeDecodeError` 裸崩，承诺是「一条坏行不顶掉整份」。这轮把**六类坏形态**真跑一遍
  （腰斩 JSON、合法 JSON 非对象、缺 `run_id`、`steps:[null,"x",{…}]`、`dur_ms:"slow"`/`api_ms:null`、
  （不洗绿）/ 空文件不崩 / `steps` 混入坏元素时好元素仍被计。
  **②无头默认（4 条）**：项目口径「调试与测试一律无头」。默认值属「一行改动悄悄反转全项目
  `ATLAS_HEADED=1` 才开有头（`0`/`true`/`yes`/`2`/空串一律无头）；`session` 的
  `launch_kwargs` 必须吃 `config.headless`；`--headed` 默认 `argparse.SUPPRESS`（不给时保持
  `"headless": False,` → 精确红。**踩坑记录**：第一版测拿去 `AtlasConfig(tmp_path)` 直构，
  **真机复验**：同一条 `gen --with-pytest --fast` 重新出包，5 个 `.py` 全部
  `py_compile -W error::SyntaxWarning` 通过（改前该包 `atlas_pages.py` 红）。

### §2 续行（原样搬迁，第一轮遗漏部分）

  加组合层那波一半消费点读新层一半读旧层，被遮蔽的通用条目对后者不报错只是不存在（wave6 修掉的两处即此形态，
  当时 2984 全绿）。判据从**数据目录**算「会被遮蔽的名字集合」（通用包有 ∩ 平台包也有 = `components`/`domains`/`pitfalls`；
  AST 扫全仓 `src/`+`scripts/` 的读点，写路径白名单逐条登记且有**活性检查**（调用点改名后白名单条目会变红，
  防「只增不删的守卫腐化」）；变异实证：`review_code`/`emit_pytest` 改回影子层各被精确点名，白名单塞死条目被点名。
  此前 `check_demo_api`/`require_account` 等**判据函数**各自单点，但**调用序列**被抄 5 份（run/agent/多flow/explore/
  explore_auto + `__enter__`），`_explore` 与 `_explore_auto` 漏了 base_url 与 DemoApi 两条：实测 `atlas run` 给
  rc 2 + 一行，`atlas explore` 给 rc 1 + **16 行裸 traceback**（同族「抄漏一条」第 3 例）。收敛为单序列后
  直接 `DemoRuntime(...)` **或**调 `explore.start(...)`；第一版只认前者，实测漏了 `_explore`，这正是守卫自己留洞的形态）
  + 入口清单双向校验 + 行为面对 4 个入口真跑 `main()` 断言 rc2/一行/无 traceback；变异实证：单从序列里删掉 base_url
  两条 → 4 个入口全红且点名「缺哪个 URL 键」消失，只删 `_explore` 的调用 → 结构面+行为面同时红。
  另修 `_explore` 生产闸必须**早于**预检（否则缺 URL 的生产项目收到「缺 DEMO_URL_PROD_EU」而不是「探索禁止连生产」，
  把越界拒绝降级成配置提示）。
  三个断言渲染循环（快/慢 × api/ui）都是 `if/elif` **无 else**，实测塞未知 kind 时快慢两渲染器都**静默丢弃**
  由源码扫描锁步。
  现四条锚定正则取数并断言四值相等 + collect 数等于声明基线（本波首次跑即抓出 3012 vs 2984 的真实漂移）。
  全量 **3034 passed / 0 failed**，ruff `F401,F841,F811,E402` 全绿。
  收敛为一张 `_DISPATCH` 表 + 24 个独立 handler）：
  机械抽取（脚本按 AST 剪切粘贴分支主体，**不改一个字符的判据**，rc 表达式原样）：`_doctor`(86→独立)、
  分派表按**调用签名**分四组（`handler(args)` / `(args, config)` / `(args, plugin)` / `(args, config, plugin)`），
  不用 `**kwargs` 或反射签名——参数传错要在表处当场 TypeError，不被动态派发吞成运行期错误。
  守卫 `tests/test_cli_dispatch.py`（5 条，均带变异验证）：①每个 parser 子命令必须被覆盖（表或显式分支），
  不然静默落 `return 2` ——「rc 2 且无输出」是最难查的失败形态；②表里无死条目；③表登记参数与 handler
  形参**逐位相等**（变异实证：`run` 少登记 `plugin` 被精确点名）；④`main()` ≤160 行（挡住「又堆回去」）；
  ⑤`_` 函数无死代码。变异实证：从表里删 `schedule` → ①精确报出缺失命令。
  真机实测（SIT 登录卡 SSO、顶栏 `.app-topbar-obj.el-dropdown-selfdefine` 60s 超时）发现
  从未存在。后果：`summary`/`allure`/`report` 全看不到这次运行（无痕族 #106/#86 的 run 轨形态；
  gap9 F-33.4 ① 登记的是 pytest 产物侧同族，那条腿产物零 import atlas，无法复用本函数，仍未动）。
  修法：`_run` 把「会话进入 / flow 执行」分两层捕获，入口失败由 `cli._record_entry_failure`
  补写**与正常行同构**的记录（字段集取自 `RunRecorder.close`、`fail_class` 只取 `FailureClass`
  既有成员、`status=failed` 不洗绿、`steps` 零伪造）再**原样 re-raise** 真因；留痕自身失败
  rc 3 腿，避免把「等人」错记成失败）。
  守卫 `tests/test_run_entry_failure_record.py`（5 条）：①真的多一行且非 passed；②字段集与
  正常行**逐键相等**（多键/少键都红——读侧不认的孤儿行比不留痕更坏）；③`fail_class ∈ FailureClass`
  rc 仍 **1**、`atlas summary` 的 `failed 24→25` / `interaction 1→2` 确认读侧真消费。
  证据归档 `reports/guard_wave_20261116/`（含改前基线 / 修复前 / 修复后三份日志 + SUMMARY）。
  SIT SSO → 被拒 → 停在登录表单 → 45s 报「未见登录指示器」→ rc 1，**报错里没有一个字提 demo**。
  排除路径（如实留档，含弯路）：① 怀疑 SSO 改版/验证码 → 只读探测 dump body，captcha 字段是
  ④ 打印 `resolve_account()`/`credential()` → 命中。
  连跑观察：**SSO 侧会节流**——5 分钟内 11 次登录时第 5 个起连续 rc 1（全为 `session.__enter__`），
  间隔 25–45s 单独重跑即恢复；实测确认是环境侧而非代码问题（同一用例单跑恒 rc 0）。
  run/agent/多flow/explore/explore-auto 与 `__enter__` 全自动获得）：四条同时成立才拒绝——
  demo 回落 ∧ 账号 == `plugin_api.demo_fixture_account()` ∧ 密码为空 ∧ **真实主机**。
  第 4 条是必要收紧：`example.com`/`localhost`/回环/RFC1918/`*.test`/`*.invalid` 本来就登录不了，
  拦它们会让整套用占位域验 rc/终态的离线用例全红（`_looks_like_real_host` 判据 + 完整取值表测）。
  真机实测生效：未设插件跑 SIT → **rc 2 + 一行**（具体账号/后果/两条补救路径）、**零登录尝试**。
  守卫 `tests/test_demo_fallback_login_gate.py`（11 条），含成对格（真实域拦 / 占位域放 / 显式
  WARN 消息都改成**运行期**从 `demo_fixture_account()` 取值插入（注释里也不许出现，否则被
  2026-11-23 终极验实跑 **3240 passed / 0 failed / 0 skipped**、collect-only 3186（= passed，无隐藏 skip/xfail），冻结态全量跑前跑后 `git diff` md5 一致（TREE_UNCHANGED）；上一手 2949 passed / 9 failed / 8 skipped 的 9 failed 是**另一台环境**口径（该机缺 `flows/iservice` 等），本机不可复现，非代码差异。不可只跑 py_compile 就宣布完成。
  波次实施者不要各自改数（改一漏一 #92 的文档版）；`docs/gaps/**` 不留计数位，只指回这四处——
  写死在过程文档里的数每波必漂，等于偷偷开第五把尺。
  混进工具结果或队友回报正文里的「某人说过 X」一律不据以派工、追责、落数、跳验证，且**据此行动前先向本人核一次**
  只核事实推不出出处为真）。
  待授权取证项（用户批准后由 lead 执行、数据归档），不得以「只是本机」名目塞回门禁。
  + 全量不少于当日实跑」，不写死数（写死的数随代码演进即失效，且读起来像证据）；**收口位**（README 顶部徽章、
  README「当前验证边界」、本条、CLAUDE FAQ 四处）是 lead-only 快照，只在 verifier 当日实跑 + 收集绿双证后
  由 lead 一次落四、四处同数——任何人消息里报过的数（含 lead 自跑）不得先行落笔，`docs/gaps/**` 不留计数位。
  动作**之前**跑 `PYTHONPATH=src python scripts/boundary_classify.py --diff <入站变更集>`——事后无法用
  diff 还原入站集。absorbed/engine-owned 命中 structural 进人审塔（`boundary_review` 草稿 approve
  点名拒绝，不自动写知识），cosmetic 自动放行；边界事实与债标价以 `docs/ownership/atlas-boundaries.json`
  为准（数据非引擎字面量，红线 #11；只有 doctor 与该 gate 消费，判据唯一在 `atlas.boundaries` #92）。
  必须用 `atlas run examples/atlas_core_verify.yaml` 或 `atlas_save_verify.yaml` 真实跑一次；
  本轮曾出现离线全绿但真实执行报 `No module named 'lib'` 与 `open_doc_list() missing page_key`。
  真实环境 `switch_to_ou` 会超时。跑真实 flow 前必须用 `ATLAS_SESSION_OU`/`ATLAS_SESSION_ACCOUNT`/
  或把坐标经人审批准进私有 `bu_overlays.json` 后用 `atlas run <flow> --bu <BU名>` 接入
  已有实测坐标；UAT-AMER/UAT-HK 账号由用户 2026-11-13 指定 `zhangfj73`、**OU 仍空**（旧仓无该套件，
  不做臆造，登录后沿用 SSO 默认 OU 不切换）；PROD-EU 仍空——旧仓 `.env` 明确「生产环境必须搭配
  账号仍为空时**在开浏览器/登录前**点名拒绝：CLI 在 `with DemoRuntime(...)` 之前预检并投递
  「一行 stderr + rc 2」（判据与消息单点在 `runtime.require_account`，与 base_url / `check_demo_api` /
  修复前该闸门只在 `__enter__` 判定，ValueError 穿透 `main()` 变 ~20 行 traceback——
  闸门自己成了 #103 想消灭的「失败点漂到不该漂的地方」。旧行为是拿空串起 Chromium 走 SSO，
  示例 flow 里的虚构客户号（如 `CUST0000001`）在真实系统查无属数据事实，不伪装 PASSED。
  用 AST 扫全仓「elif 链无 else 且判据含 kind/type/status」的可疑分支，得 5 处，逐个人核：
  生成的 flow 看起来完整、实际少一整段请求体。修法：`emit_flow.BODY_KINDS` 取值域 + 分流**之前**
  点名拒绝（不在链尾补 else 猜默认），`none` 显式放行（= 无请求体，语义正确）；两处清单由
  13 条守卫，双向变异验证：删闸门 → 6 红（静默丢弃复现）；把 `none` 从取值域删掉 → 3 红
  顺带收口上一轮遗留：`--compat legacy-suite` 的 flow 级 `assert` 此前**静默丢弃**（同族第三例），
  现 `compat_specs_from_flow` 点名拒绝并给两条补救路径，`tests/test_compat_assert_gate.py` 6 条
  实测 34 个 compat-eligible flow 当前 0 个带 flow 级 assert，**无存量数据受影响**。
  README 有一张 8 行的「退出码按子命令读，不跨命令并表」表，是给 CI / `atlas scheduling` 巡检 /
  批处理脚本用的**契约**——但它们全都按这些数字写分支，而表本身改了代码也不会红。现
  ②代码里出现的 rc 常量必须全部在表里声明（防「悄悄新增退出码」——消费方会把它当未知失败，
  更糟的是 `rc != 2` 于是"重试"一条输入错误的命令）；③`rc 3` 两义（run 的等人 / gen 的结构自检
  失败）README 明写「不并表」，本测钉住那条说明与两处来源标记都在；④表解析有正向对照下限
  报「表只解析出 5 个」+「124 未声明」双红。**注意表嵌在列表项里（行首两个空格缩进）**——
  第一版按行首 `|` 匹配一行都取不到，测试当场红出来（这正是正向对照下限的用处）。
  混成一件——「裸 clone 上 rc 1 是预期的」与「doctor 自己崩了 / 仓内资产真漂移」。
  修法：改为读 `--format json` 逐项判定——`docker_assets_consistent` /
  真漂移）；`legacy_pages` / `demo_api` 允许 false 但**打印出来**（允许但不隐身）；
  另加「doctor 必须产出合法 JSON」一条（诊断入口自己崩 = 直接失败）。
  GBK 解码 doctor 的 UTF-8 JSON 直接 `UnicodeDecodeError`。`atlas` 侧的
  显式 `decode("utf-8","replace")`，并由测试钉死（禁止对 doctor 用 `text=True`）。
  守卫 `tests/test_ci_gate_honesty.py`（4 条，双向变异验证）：①可执行行里不得有
  ②必须读 JSON 且断言两项资产；③显式 utf-8 解码；④clone-dependent 两项不得混进 `required`
  本地实测该检查**能真失败**：往边界账本注入一个 `src/nonexistent_dir/` 区域 →
  文档原话「fixture 期抛 EnvBlocked 不留 runs.jsonl 行」——实测**只对一半**：
  test 体内抛 ✅留行、**函数级** fixture 抛 ✅留行（autouse `_fast_runlog` 先于显式请求的
  fixture 建立）、**模块级** fixture 抛（`sole_page`）❌**runs.jsonl 文件都不存在**。
  根因：`sole_page` 是 `scope="module"`，pytest 在**任何函数级 fixture 之前**建它；它抛
  EnvBlocked（缺 playwright / 缺浏览器 / attach 选页失败）时 `_fast_runlog` 从未进 setup，
  收尾一刀没走。后果：`atlas summary`/`allure`/`report` 看不到这些用例，
  修法：`pytest_runtest_makereport` 在 `call.when=="setup"` ∧ `item._fast_runlog_entered`
  未置位时补一行；标记由 `_fast_runlog` 进场时置位（正常路径不重复落行）。状态词复用
  产物零 import atlas → 该行由模板自带的 `RunWriter` 写，与正常行同构
  守卫 `tests/test_fast_setup_failure_row.py`（**6 条，真产真跑 pytest 子进程**——makereport
  是钩子，只有真跑 collection+setup 才触发；这正是该缺陷能漏到现在的原因之一）。
  ②丢掉进场标记 → **函数级 fixture 双记 2 行**（比不留痕更难查的形态）。两条都红。
  本仓注释风格是「用名字指向唯一实现」（#92 在语言层的体现），前提是**名字必须还在**。
  名字一旦因重命名失效，注释就变成假路标：读者 grep 不到，会以为代码被删了，而不是
  「注释旧了」——比没有注释更坏。实测扫出 3 处真失效并修正：
  连刚改过的人都漏，正是它需要机器检查的理由）。
  判据：只查 `` `_xxx` `` 形式的**私有**标识符（公有名噪音太大：内建异常 / 外部 fsync /
  JSON 键 / 业务任务名全会误报），且名字须以字母数字结尾（排除 `_FAST_`/`_specs_` 这类
  前缀片段）。白名单逐条写理由，两类合法外部引用：旧仓符号（`_case_watchdog`/
  另加三条「非符号引用」（ctx 键 `_data` / attach 名字前缀 `_FAIL` / 目录名 `_common`）。
  守卫 `tests/test_docstring_symbol_refs.py` 3 条：①覆盖率下限 + 白名单条目不得过期
  文件:行:符号）；③白名单每条必须写理由（防空理由豁免）。
  变异实证：把修好的名字改回失效形态 → 精确点名 `runtime.py:273`；白名单塞空理由 → 红。
  =插件缝的证明）。后果具体：新人想找「族判定在哪」，翻到 `ui_family.py`，回头查地图
  却没有这一行——只能怀疑自己看错了版本。**地图缺格比地图画错更常见、更难发现。**
  已补齐 14 行（`__main__` / `cli_render` / `fastplugin` / `generator/{dsl_utils,
  emit_compat,markdown,model,plaintext}` / `llm` / `plugins/{demo/{legacy_case,plugin},
  iservice/plugin}` / `screenplay` / `ui_family`），每行写职责 + 判据所在（不写空话）。
  守卫 `tests/test_doc_module_table.py` 3 条：①覆盖率下限（表 ≥40 行、模块 ≥60）；
  ②每个模块文件名必须出现在表里——允许两种覆盖形态：独立行 **或** 目录汇总行内嵌名
  ③反向：表里独立行点的文件必须真存在（死条目比缺条目更误导）。
  变异实证：删掉 `ui_family.py` 那行 → 精确点名缺它；加一行 `no_such_module.py` → 死条目红。
  类 docstring —— `\s` 是**非法转义**：默认档只报「被忽略的 SyntaxWarning」，于是产物
  「生成成功 / 结构齐全 / 一 import 就炸」；而生成期自检只 `ast.parse` 本仓源码，从不编译
  自己写出去的文件（与「只生成不执行」#9 同一条缝）。**同族另两处**：用例模块
  逐字不变，不做内容改写）——emit_pytest 6 处（notes/意图/path/task/element）+ emit_compat
  3 处（flow_id/intent）插值改走它；②**写盘前严档自检** `dsl_utils.artifact_compile_problems`
  ——`emit_pytest_package` 与 `emit_compat_package` 在 `mkdir` **之前**按
  「先渲染完再自检、最后写盘」，不再依赖「知识数据恰好干净」。
  守卫 `tests/test_artifact_docstring_safety.py` 10 条：①真知识包快慢两形态警告洁净（含
  「数据里确实还带反斜杠」的正向前提）②notes 语义保真（反斜杠/双引号/尾反斜杠，`__doc__`
  逐字相等）③正向对照（未转义必须报红，否则 `warnings` 过滤器恒真）④用例模块快慢×API/UI
  四格 ⑤源码面插值必须过 helper + 扫描下限 ⑥单点函数语义 ⑦⑧写盘前闸两入口（monkeypatch
  转义恒等 → 拒绝且目录不建）⑨单点判据两向对照 ⑩本仓 `src/`+`tests/`+`scripts/` 全量按同一
  严档编译干净（写这条时当场抓到守卫文件自己的说明文字里有裸反斜杠）⑪⑫`emit_fast` 两份底座模板
  三处产物写入口（包/compat/快底座）自此同一口径。
  三处既有 `_assert_compiles`（`test_emit_pytest`/`test_emit_compat`/`test_emit_fast`）同步收紧为
  「警告当错误」口径——否则整族缺陷在这三份辅助函数下恒绿（它们此前只 `ast.parse`+裸 `compile`）。
  ① 中文标题（本仓主线文档语言）→ 非 ASCII 全被抹掉 → 落兜底 → `GeneratedProbeProbe`
  英文摘要出的 `get-web-example-customer-codeProbe` **不是合法标识符**，产物里就是
  的 .py，与上一波「生成期不校验自己写出去的文件」是同一条缝。
  ASCII slug，再经 `dsl_utils.pascal_identifier` 转 Pascal 加 `Probe`）；`emit_pytest` 的
  守卫 `tests/test_probe_skeleton.py` 4 条：①取值表（英文/中文/残渣/引号/反斜杠/空 → 合法
  标识符 + 确定性）②骨架严档可编译且 `create()` 恒 `NotImplementedError`（红线 3/12/16）
  ③源码面单点 + **正向对照**（旧公式产出的名字必须仍是非法标识符，否则守卫前提消失）
  ④端到端 `generate()` 跑中英两份文档，探针文件可严档编译、类名合法。
  变异实证：`__init__.py` 改回旧公式 → ③④ 精确红（端到端那条在 `compile()` 处 SyntaxError，
  正是旧产物形态），恢复后文件 sha256 一致。真机复验：同一条 `gen --with-pytest --fast` 出包，
  + flow 级断言 `resp_1_get-web-example-customer-code.ok == true`；而 `emit_pytest` 解析这些
  断言的正则键位是 `(\w+)`——`\w` 不含 `-`，于是**导出读不懂生成器刚写的断言**：降级成
  导出链」这一跳，所以离线全绿、只有把两条链拼起来才现形。）
  与 `\w+` 一致；②兼容侧 `_FLOW_OK_RE`/`_FLOW_RC_RE` 键位放宽到 `[A-Za-z_][\w-]*`——用户仓库里
  守卫 `tests/test_generated_assert_roundtrip.py` 4 条：①真跑 `generate()` 取 flow 再
  取值表（空格/连字符/中文/引号/反斜杠 → 恒为合法标识符，模板 `{n}/{slug}` 仍生效）
  ③手工构造 hyphen 键 flow（模拟存量产物）→ 断言仍解析成真断言 ④**正向对照**：真正认不出的
  形态（`<save_as>.data.total > 3`）仍必须降级成 TODO（放宽键位 ≠ 什么都认）
  ⑤坏 `--naming` 模板（`{oops}`/`{0}`/含 `-`/数字开头）→ `FlowEmitError` 点名 `--naming`
  ⑥CLI 面坏模板 → **rc 2 + 一行 stderr、无 traceback**（输入错误不漂成程序崩溃）。
  同轮收口 `--naming` 预检：`GenOptions.validate()`（与 `--fast` 组合判据同一把闸）先点名，
  变异实证：正则改回 `(\w+)` + 命名改回 `slugify` → ①②③ 精确红（④⑤⑥ 保持绿，正是它们该有的
  行为）；恢复后两份文件 sha256 一致。真机复验：`atlas gen examples/api_sample.md` →
  该 flow → 产物**零 TODO**、两步各有 `assert result.ok`；`--naming "{oops}"` → rc 2 一行，
  引用非步骤落点的文本断言（`document_no != ""`）或写错键的 → 同样丢弃。后果：
  ——「断言消失」比「断言失败」更危险（它看起来验证过）。实测 `examples/return_from_so.yaml`
  导出后 `document_no != ""` 与 `assert_api` 块双双消失。
  不归属任何步骤的断言收成待接 TODO，由 `render_test_module(..., flow_notes=)` /
  守卫 `tests/test_flow_assert_export_coverage.py` 5 条：①Task 形态 → TODO 含 task/operation/expect
  ②非步骤落点/写错键的文本 → TODO ③**正向对照**：能归属的断言不得被误标 TODO ④同一条 flow 级
  断言只出一次（不随步骤数翻倍）⑤真跑 `examples/return_from_so.yaml` 导出，产物里能搜到
  变异实证：`flow_assert_notes` 改成恒返回 `[]` → ①②④⑤ 精确红，③保持绿；恢复后 sha256 一致。
  真机复验：`export-pytest examples/return_from_so.yaml` 产物出现两行
  直接 `record["api_ms"] = actor.api.last_timing_ms`。于是「API 通道但本步没发请求」的步骤
  都没发，`atlas summary`/`report` 的耗时视图随之错位且零报错（与 P0「重放 handler 跨步残留」
  同族：跨步粘性状态）。
  恒为 3：`__init__` 初始化 / `reset_timing` 归零 / `request` 实测更新）+ engine 在
  直接赋值」）。归零只发生在 API/AUTO 通道且 `CallApi` 在位时——无 ability 的路径行为零变化。
  守卫 4 条：①两步（有请求/无请求）→ 第二步 `api_ms` 必须 0（改前是第一步的耗时）
  ②有请求那步记到真实耗时（>0）③源码面单点（engine 无直接赋值 + abilities 赋值点恰 3 处，
  新增第四点即漂移）④**正向对照**：无 `CallApi` ability 时 `api_ms` 仍为 0。
  变异实证：把归零块换成 `if False:` → ①③ 精确红（①报「没发请求的步骤继承了上一步的
  接口耗时」），②④ 保持绿；恢复后 sha256 一致。
  摘要/路径里贴一个带凭据的示例 URL（极常见）时，值会原样进
  / 两份 README，**连文件名都带 token**
  键名**，逐键 redact 抓不到（实测 `ops:S3cretPwd@example.com` 原样逃逸）。
  守卫 `tests/test_generator_secret_redaction.py` 4 条：①真跑 `generate()` 出整包，扫**每个**产物
  文件**与文件名** → 秘文零命中 + `manual_assertions` 点名「已占位」+ flow id 不带 token
  ②**正向对照**：正常文档不被改写、不产抹除痕迹（业务参数 `?page=1` 必须留着，否则毁可用性）
  ③`sanitize_url` 自身（userinfo 整段抹、无凭据 URL 逐字节不变、业务 query 键保留）
  ④源码面：生成器只调 `credential_gate` 两个函数、不自带敏感键名单（AST 去 docstring 后扫）。
  变异实证：`_redact_endpoint_secrets` 调用换成 `[]` → ①红并逐条点名被污染的文件（含文件名
  里的 token）；关掉 userinfo 分支 → ③红。两处恢复后 sha256 一致。
  真机复验：同一份「带凭据的文档」重新出包 → 7 处命中降为 **0**，flow 文件名变成
  没有第二道刀。实测同一个 .jmx 里：
  而 stderr 还报「0 处字面凭据头」——迁移器把凭据搬进了新仓库（红线 #1，与上一波
  告警只报**位置**（`step1.path`）不报值。顺手收口 `scripts/capture_save_request.py` 的
  守卫 `tests/test_import_jmeter_secret_gate.py` 5 条：①`-o` 文件秘文零命中 + `<redacted>` +
  业务 query 保留 ②**正向对照**：正常 path 逐字节不变、无告警 ③告警只报位置（stdout/stderr
  都不含原值）④源码面：只调 `credential_gate` 两函数、AST 去 docstring 后无敏感键名单
  ⑤采集脚本回显必须走 `sanitize_url`（并禁回旧形态）。
  变异实证：`redacted = apply_credential_gate(flow)` 换成 `[]` → ①精确红（秘文重新落盘），
  其余四条保持绿；恢复后 sha256 一致。真机复验：同一份 leaky .jmx 重新导入 → 命中 2→**0**，
  YAML 里是 `?access_token=<redacted>` 与 `https://<redacted>@example.com/…`，stderr 一行
  grade=F`。逐条核对：27 个 critical 里大半是**有理由的收尾吞异常**
  一律 critical 的后果不是严厉而是**噪声**：grade 恒 F，真正的裸吞（`review_code.py` 自己那两处
  理由成立」）。判据 `_declared_swallow_reason`：同行 / `except` 上一行 / `pass` 行注释里有
  ≥6 个非空字符才算；理由文本**不进报告**（不把业务注释抄出去）；仍**记一条**并计入
  守卫 `tests/test_review_code_declared_swallow.py` 5 条：①裸吞仍 critical 且分数上限 59
  ②同行注释 → DECLARED / low / 分数 ≥90 且 finding 仍计 1 条 ③上一行注释同样算
  ④**正向对照**：`# x` 这种敷衍注释不算（防「写俩字就过关」）⑤真处理（记录日志）不产 GREEN-001。
  变异实证：`_declared_swallow_reason` 里 `len(comment) >= 6` 换成 `if False:` → ②③ 精确红
  真机复验：同一棵树重跑 `review-code src` → `critical 27→16`、`avg 94→96`；
  而 `review_code.py`/`runtime.py`/`cli.py` 里的**裸吞**仍停在 critical（分流不是放宽）。
  本仓里**教用户怎么填 env** 的那些行因此自己吃 critical：`config.py` 模块 docstring 的
  同一行还有真值时照旧命中，守卫第 6 条钉住）；②注释行与 **docstring 行**降
  守卫 `tests/test_review_code_safe001_noise.py` 8 条：注释 URL / docstring URL → low 非 critical；
  占位密码与占位 URL 默认值 → 不产 finding；**正向对照**：真字面 `password='hunter2'` 与
  非保留域真 URL 仍 critical；**抹段不豁免同行的真值**；只有注释档 finding 时分数仍 ≥90。
  变异实证：`is_comment` 恒 False → 3 条红；`probe_line = line` → 2 条红（占位两条）。
  根本没走到行级判据，**改了 mutate 也不红**（空转绿灯）。改成用合法 Python 承载同一形态
  这条正好印证「守卫必须能区分新旧，跑绿不算证据」。
  真机复验：同一棵树重跑 `review-code src` → `critical 16→10`、`avg 96→98`；
  剩余 10 条**全是 GREEN-001 真裸吞**（autopilot×2 / cli×1 / review_code×2 / runtime×2 /
  session×3），如实登记、**未**为了让分数好看去补理由注释（那是洗绿换皮）。
  ①**证据链裸吞**（autopilot×2）：`_wire_events` 的 `page.on(...)` 抛错纯 pass——后果是一整趟
  自主探索跑完**没有任何请求/响应快照**而终端零提示（「为什么证据是空的」事后无迹可循）；
  两处改为 `except Exception as exc:` + **WARN 一行 + `credential_gate.sanitize_text`**（异常
  文本可能带 URL/参数，红线 #2 的终端面），不中止流程（证据是旁路面，不能顶掉动作结果）。
  守卫 `tests/test_autopilot_evidence_warn.py` 4 条（含「WARN 文本必须过净化刀」与
  「正常路径零输出」正向对照），变异实证：把 WARN 分支改坏 → 2 条精确红。
  ②**真收尾吞**（cli×1 / runtime×2 / session×3 / review_code×2）：行为**零变化**，只补上「为什么
  可以吞」的理由注释（归入 `GREEN-001-DECLARED`——「重载失败由下方 wait_for_selector 重判」
  「收尾失败不得顶掉原始入口异常」「单条重放器坏掉跳过试下一条」）。这是把上一波判据的
  「需人审确认理由成立」逐条兑现，**不是**为了让分数好看而补字（行为面一个字节没动）。
  照样写 `grade: F`——聚合等级是报告第一行结论，恒 F 等于把结论删掉）。改 `"A"` 起步后
  0-critical 报告给 `grade: C`（最差文件 review_code.py 74 分如实反映，不再恒 F 也不再虚高）。
  守卫 `tests/test_review_code_summary_grade.py` 4 条（最差文件等级/全净→A/单文件一致/真回退
  真机复验：`review-code src` → `critical 10→0`、`avg 99`、`grade F→C`；`review_code.py` 自身
  74 分（13 条 findings，含历史 SAFE-001-COMMENT/DECLARED 档）如实保留，未调阈值凑 A。
  写法——每个规则的 rationale 注释都以 `# assert …` 开头（`# assert True, assert 1 是恒真断言`、
  是这种文档噪声，文件被自己的文档判成 74 分/C；整树 `grade C` 也由它拖出。
  断言一行只有一条，枚举才连写）③冒号后 ≥3 词的解释段④AST/伪代码术语（`Compare(`、
  自身文件上也成立（这不是躲扫——判据表测试钉死三种讲解形态必须为 False）。
  守卫 `tests/test_review_code_assert006_noise.py` 6 条：①真被注释的断言仍判 high
  ②③④三种讲解形态不产 ⑤取值表 9 行（真代码 4 True / 讲解 5 False）⑥自扫 ASSERT-006 ≤2 条
  真机复验：`review-code src` → `total_findings 40→34`、`avg 100`、**grade C→A**——
  评审器不再被自己的 rationale 注释拖分；剩余 34 条全是 low/declared 档的**真留痕**
  纪律。但 `detect_kind` 把所有 `.py` 都当 `pytest` 扫，引擎自己的轮询实现被逐条判 high：
  要求引擎不要实现等待（自指噪声，与 ASSERT-006/SAFE-001 两波同族：**规则的适用面没写清**）。
  等于 `tests`；**刻意不用「含 test 子串」**——`contrast.py` 这类会误中）；
  只作用于 `time.sleep`/`sleep` 两处 MAINT-001 落点，其余规则面不受影响。
  守卫 `tests/test_review_code_maint001_scope.py` 5 条：①引擎 `.py` 的 sleep 不报 ②③`test_*.py`
  与 `tests/` 下任意文件仍报（**正面对照：规则没被关掉**）④`contrast.py` 不算测试（防子串误判）
  ⑤整树 `src/` 扫完 MAINT-001 恰 0 条（改前 4 条）。变异实证：`if self.is_test:` 换 `if True:`
  真机复验：`review-code src` → `total_findings 34→27`、**MAINT-001 4→0**、`avg 100`、
  与 1 条 MAINT-003 写死数据，均为有意保留）。
  产物看起来完整，只有把生成→消费两端拼起来或用严档编译产物才现形」。手册给出：病因三条
  排查表、修法模式四条（判据单点收敛 / 写盘前严档自检 / 变异验证 / **适用面写清**）、
  四条排查命令。**同轮补 `docs/reader-guide.md` 索引**：实测 index 表漏了 **8 份**文档
  —— AGENTS §5 写了维护义务但此前**零机器检查**（与「CLAUDE 模块表漏 14 个模块」同族：
  地图缺格比画错更难发现）。
  ②索引条目必须指向真文件（死条目更误导）③正向对照下限（≥6 行/≥6 份）。
  变异实证：删掉 `docs/first-bug.md` 那一行 → ①精确点名 `first-bug.md`；恢复后 sha256 一致。
  真机复验：补完索引后 `reader-guide` 覆盖 `docs/` 全部 20 份（含 `gaps/` 与 `ownership/`
  的目录级条目）。
  顺着「结论绑来源」族扫 `strftime`/`datetime.now` 全仓，看时间戳是否也「只对本机成立」：
  九处 `created_at`/`decided_at`（experience×2 / explore / knowledge / overlay / review×4）
  是**本地时间无 offset** 的 `%Y-%m-%dT%H:%M:%S`，但消费面只有「显示」
  结论：**不是缺陷**——同机同 tz 下排序恒成立；跨机合并 runs 的场景不存在（runs.jsonl 由
  单次 run 追加，`ATLAS_RUNS_DIR` 是隔离逃生舱而非 merge）。因此**不**统一格式：
  改它要动 `test_overlay_pipeline.py:222` 那条 `^\d{4}-…T\d{2}:\d{2}:\d{2}$` 断言（契约面），
  收益为零、风险为正。**边界登记**：将来若真做「多机 runs 合并」或「草稿 age 告警」，
  这九处必须同步改成带 offset 的 ISO，且改之前先翻本条。
  clone**。随后 clone 按纪律删掉 → `import atlas` 的 `__editable__` 钩子指向已不存在的目录 →
  一批「子进程跑骨架、靠 site-packages 里的 atlas」的用例（`test_allure_e2e_offline`）
  报 `ModuleNotFoundError: No module named 'atlas'`，**看起来像代码回归，实为环境被接管**。
  这是「结论绑来源」纪律的**环境版**：判据/测试看到的是**哪个 atlas**？人记不住自己上周
  在哪个目录装过 editable，必须机器检查。
  「浏览器崩溃 skip」同一环境语义（autonomous-execution 纪律：环境限制 skip、
  真实失败仍 assert False）；②新增 `tests/test_editable_install_scope.py` 3 条：
  不依赖安装可用、失败信息给可执行修复（`cd <repo> && pip install -e .`）。
  守卫恒红；改成分号后绿。②首版没剥 `PYTHONPATH`，pytest 会话带的 `src` 让子进程 import 的是
  源码树而非「已安装包」——剥掉才是这条守卫要验的面（两条测各验一层：安装面 / 源码面）。
  真机复验：`cd D:\GitHub\Atlas && pip install -e ".[dev]"` 恢复后
  sensitive 文件、`ROOT/.env` 喂饱环境、tzdata 有无、**大小写**。Windows 上「本地跑一遍」
  永远抓不到大小写问题，必须机器检查。
  ①扫 `tests/`+`src/`+`scripts/` 里 `*_ROOT / "literal"` 形态，逐条与磁盘真实名精确比对
  + 根目录 `CLAUDE.md`/`README.md`/`AGENTS.md` 必须精确大小写可解析）。
  docstring，把 `DOC = REPO_ROOT / "CLAUDE.md"` 整行跳过了——变异测（把 CLAUDE→Claude 改回去）
  字面量」，实测本仓只 1 处该形态，会逼人凑样本——改「≥1 处 + 根目录关键文件精确可解析」。
  Atlas 的首选交付物是产物包（AGENTS §0），但「新人拿到 `pytest/` 目录第一步 collect」此前
  锁的是 import 面，而「第一个动作就挂」的回归（pytest.ini/conftest/BASE_URL 断链）只有到
  用户手里才现形。守卫 `tests/test_artifact_first_run.py` 4 条：①产物在**剥掉 PYTHONPATH
  的子进程**里 `pytest --collect-only` rc 0 且收集数 ≥1（快慢两形态）——模拟没装 Atlas 的
  新人机器 ②README「本包文件清单」列的每个文件都真在（死清单=新人扑空）③产物里实际出现
  的 `${VAR}` 占位符必须都被 README env 段点到（少一个=第一步就撞未解析占位符）
  ④零 `import atlas`（可移植性主声明防回潮）。变异实证：产物 import 行改成 `import atlas`
  生成了 `reports/review_triage_20261123.md`（53 条待审的分类处置建议：trace_step×6
  可直接驳回〔SSO ticket 快照/示例草稿〕、pitfall×21 需补证据三元组或业务判断、
  selector_candidate×26 需「按钮属于哪个业务组件」的接线判断〔红线 #11 引擎不猜〕、
  migrated×6 是旧仓迁移工序的尾巴）。**落点自查**：这份材料引用真实票据形态
  已移出并说明。钉成守卫 `tests/test_triage_material_location.py` 4 条：①tracked 文件里
  不得有 triage 命名模式 ②`.gitignore` 必须含 `reports/` 行 ③`git check-ignore
  reports/runs.jsonl` 单独一层（行在但规则被 `!` 反例/子规则覆盖时，纯文本判据漏）
  ④reports/ 下无 tracked 文件（矛盾自检）。变异实证：删 `.gitignore` 的 `reports/`
  行 → ②当场红。
  上一波钉了**读侧**（`read_jsonl_text` 用 `utf-8-sig`），但读侧兜底不能成为写侧借口：
  用户编辑器都可能踩 BOM。本仓扫过一遍（`src/`+`scripts/` 全部 .py、`knowledge/`、
  BOM）。钉成守卫 `tests/test_repo_no_bom.py` 4 条：①全仓文本文件逐个查前 3 字节
  write/open(a/w) 同现才算写侧命中，`config` 的「encoding = 局部变量」读侧探测形态放行）
  ④正向对照下限（≥200 文件、≥20 个 write_text 调用）。
  变异实证：在 `examples/` 植一个真 BOM 文件（`\xef\xbb\xbf{}`）→ ①精确点名
  都写 BOM。五个 JSONL 读侧（`experience._read_queue` / `review.rows` / `reporting`〔含
  summarize/export/allure〕/ `review_code` 基线 / `scheduling.state.read_runs`）此前一律
  等价，存量行为零变化）+ 瞬态锁重试 + 坏字节回落 replace。五个读侧的
  守卫 `tests/test_jsonl_bom_first_row.py` 6 条：①review 队列看到 BOM 首条 ②summarize 不丢
  BOM 首 run ③experience journal ④调度索引 ⑤**BOM 与中段坏字节共存**（首行仍在、坏行照旧
  计 skipped——证明 `utf-8-sig` 没有吃掉 replace 回落）⑥源码面：五读侧都走单点、无裸
  变异实证：`_BOM_SAFE_ENCODING` 改回 `"utf-8"` → ①②③④ 精确红（BOM 首条重新消失），
  ⑤⑥ 保持绿；恢复后 sha256 一致。`config.py` 早就为 `.env` 付过同一笔学费
  UTF-8 多字节腰斩），确认承诺成立——于是钉成 `tests/test_runs_read_side_tolerance.py`：
  rc 0 / 零 traceback / 合法行仍在 / `skipped_malformed_lines` 计数准确 / **坏行不进 passed**
  变异实证：`summarize` 的 `skipped += 1` 改成 `pass` → 计数测精确红（1≠2）。
  真机行为、而离线测看不出来」的位置，故钉住：`AtlasConfig.headless=True` 默认；只有**精确**
  子命令默认）。守卫 `tests/test_headless_default.py`，变异实证：把 env 表达式换成
  被 pytest mixer 的 `channel="chrome"` 与仓库根真 `.env` 干扰，且 `inspect.getsource`
  在插件环境下抛 `OSError`——改成「自造 `.atlas-root`+空 `.env` 的项目根 + `from_root`」才稳。
  真机复验：`atlas gen examples/api_sample.md` → `save_as: resp_1_get_web_example_customer_code`
  且断言行随之改形；再 `atlas export-pytest` 该 flow → 产物**零 TODO**、两步各有 `assert result.ok`。
  变异实证：pages 那处改回 `{notes}` → ①②精确红（两种形态都现形：`\s` 非法转义、尾反斜杠
  逃逸闭合引号）；compat 的 `flow_id` 未转义 → 源码面守卫当场点名 `emit_compat.py:839`。

## 3240 → 3257（2026-11-24，JSON 对象读侧 BOM 单点）

**触发**：`overlay` 读 BU 坐标 JSON 用 `read_text(encoding="utf-8")`，Windows 上 PowerShell /
记事本写出的带 BOM 文件直接 `Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)` → rc 2。
与 3159 已修的 JSONL 读侧 BOM 同一现实，但 **JSON 对象这条读路径整条被漏掉**。

**根因（比缺陷本身重要）**：BOM 是**合法 UTF-8 字节**（`EF BB BF`），裸 `encoding="utf-8"`
读它**不抛** `UnicodeDecodeError`，只是把 `\ufeff` 留在文本首字符，然后 `json.loads` 才抛
`JSONDecodeError`。这一层之隔让所有「我兜了 `UnicodeDecodeError`」的写法**一行都没拦到**——
看起来处理了 BOM，实际兜底分支永不触发。这也是为什么逐点排查容易漏：崩的地方和声明兜底的地方不是同一处。

**全仓实际 12 处（不是 8 处）**，后果分两族：

| 族 | 站点 | BOM 下行为 |
|---|---|---|
| 裸崩 | `overlay.load_overlays` / `contracts.ContractRegistry.load` / `review_code.load_config` / `plugins/demo/creators.build_payload` | rc 2；契约链被 `JSONDecodeError` 顶掉，包内回退都走不到 |
| **静默降级** | `knowledge._read_mapping` → `None` / `ui_family.load_family_table` → `{}` / `allure_analysis` result → `skipped += 1` / `scheduling.lease.read_lock` → `None` / `devops_sync.load_mcp_servers` → `DevopsSyncError` / `legacy_fast.load_site_rules` → `[]` + 一行 WARN / `boundaries.load_ledger` / `scheduling.state` 台账 / `devops_sync` allure 结果 | 报告照常产出、退出码正常，只有结论悄悄变错 |

第二族才是重点：`read_lock` 返回 `None` 的语义是**「视同无锁」**——一个带 BOM 的锁文件
**反而把并发闸关掉了**（没锁文件时调用方还会报，有 BOM 锁文件时静默当无锁）。
`knowledge`/`ui_family` 同形态：知识命中凭空变少、族表静默换成内置副本，没有任何报错。
与 `--fail-under -1`（评分 0 也 rc 0）、`--max-steps -1`（零迭代却报「步数用尽」）同一族：
**输入没校验，功能静默失效**。

**修法**：判据单点 `atomic_io.read_json_file(path)`（= `read_text_retry` + `utf-8-sig` + `json.loads`）。
与 JSONL 侧 `read_jsonl_text` 同取向，但**刻意不做** `errors="replace"` 回落：JSONL 一行坏字节
只毁一行且损失可见（残行跳过并计数）；JSON **对象**用 replace 会让坏字节变成合法 `U+FFFD`
并照样通过 `json.loads`，于是错误数据被当正确配置消费——JSON 是契约文件，必须精确或大声失败。
`legacy_fast.py` 例外：它明确禁用相对 import（无包上下文 standalone 加载），内联同口径 `utf-8-sig`。
顺带把 `explore.compile_trace` / `probes` 两处 JSONL 裸读收回 `read_jsonl_text`（消除重复 try/except）；
`generator/emit_compat` 的 `_load_plan_ctx` 产物字面量同改 `utf-8-sig`（否则生成出去的包同缺陷）。

**守卫**：`tests/test_json_bom_read_sites.py` 17 条——单点正向吃 BOM / 坏字节**必须抛**（正向对照，
不许 replace 混过）/ 12 个加载器逐个喂**真 BOM 文件**并断言解析结果等值（不是「不抛就算过」）/
源码面零裸写法 `json.loads(...read_text(encoding="utf-8"))` / 正则**自证**（拿已知坏样本喂正则必须命中，
防「正则改坏 = 全仓恒绿」）。全部 17 条在改动前均红（overlay 崩溃、knowledge 返回 None、read_lock 返回 None…）。

**顺带修两条既有守卫的缺陷**（都不放宽判据）：
1. `tests/test_repo_no_bom.py::test_source_never_writes_bom_encoding`——其正则把
   `open(p, "r", encoding="utf-8-sig")` 的**读**判成写侧违规（该测 docstring 自己写着「读侧允许」），
   会禁掉正确的修法。改为按**调用形态**判（`write_text`/`write_bytes` 命中即违规；`open` 还须
   mode 含 `w`/`a`/`x`/`+`），与同文件 `test_source_avoids_utf16_writes` 的既有口径一致，
   并新增 `test_bom_write_guard_still_catches_writes` 做**判据自证**（三写形态必抓、两读形态必放）。
2. `tests/test_doc_verification_count.py::test_collect_count_matches_declared_when_repo_is_clean`——
   原判据 `collected == declared` 只在「仓内零 skip」时成立。本机装有 allure 后，
   `test_gen.py` 的「无 allure 形态」用例因**环境无法构造**而条件跳过（有 allure 就构造不出没 allure），
   于是 `collected = passed + skipped` 恒差 1，旧写法会把一个**环境事实**报成「加了测试没落数」。
   改为同类比同类：`passed == declared`（skip 数写进失败消息）。判据强度未降——仍然秒级
   `--collect-only`，仍然把「新增用例没改文档」变成红。

**验证**：`PYTHONPATH=src python -m pytest -q` → **3257 passed / 0 failed / 1 skipped**
（`--collect-only` 3258）。1 skipped 即上述 `test_gen.py` 的条件跳过（本机装 allure；CI 无 allure 会实跑）。
新增 18 条测试、净 passed +17，差值正是那条从「跑」转「跳过」的既有用例。

## 3257 → 3280（2026-11-25，同族缺陷根治续波）

本波由团队并行推进（auditor 出清单 / implementer 落地 / lead 收口），主题是**把上一波的
「输入未校验 → 功能静默失效」族扫干净**，而不是再修单个点。

### ① BOM 单点的漏点：`cli.py` 两处 JSONL 读侧

上一波把「读 JSON **对象**文件」收敛到 `atomic_io.read_json_file`，但 **JSONL 读侧**还有两处
裸 `utf-8` 漏在 `cli.py` 里：

| 站点 | 读什么 | BOM 下后果 |
|---|---|---|
| `_recycle`（`cli.py:1505`） | 回收清单 `recycle_list.jsonl` | 首行 `\ufeff{...}` 解析失败 → `skipped += 1` → **一条已真实产生的单号从清单里消失**，rc 仍 0 |
| `_review --apply`（`cli.py:1640`） | 审批决策文件 | 首行落 `broken` 分支 → **一条已批准的决策凭空变成「格式错误」** |

**为什么之前没被发现**：守卫 `tests/test_jsonl_bom_first_row.py::test_jsonl_readers_use_single_point`
是**白名单式**的——只核 5 个具名文件（experience/review/reporting/review_code/scheduling.state）。
新增读侧不会自己进名单，这是白名单守卫的**结构性**缺陷，不是某次疏忽。

**修法**：两处改走 `read_jsonl_text`（`utf-8-sig` + 锁重试 + 坏字节 replace 回落）。
**补扫描式守卫**：`tests/test_json_bom_read_sites.py::test_jsonl_readers_go_through_single_point`
按**形态**认而不是按文件名认——`_JSONL_SHAPE` = 「`read_text(encoding="utf-8")` 之后不远处
出现 `json.loads(line)`」。配自证测（坏形态必命中、正形态必放行）与 `_recycle` 的 BOM 行为测
（断言输出里真有那条单号，不是「不抛异常」）+ 坏字节正向对照（合法行不被连带丢掉）。

变异实证：把 `_recycle` 还原成裸 `utf-8` → 守卫精确点名 `src/atlas/cli.py:1505`；恢复后 21 绿。

### ② `scaffold.py` 的 TOML 读侧 BOM

`detect_atlas_source_root()`（`scaffold.py:56`）靠 `pyproject.toml` 的 `project.name` 判断
「这个目录是不是 Atlas 源码根」，读法 `tomllib.loads(read_text(encoding="utf-8"))`：

```
>>> tomllib.loads('\ufeff[project]\nname="x"\n')
TOMLDecodeError: Invalid statement (at line 1, column 1)
```

后果链是**静默降级**，不是崩：

```
pyproject.toml 带 BOM → tomllib 抛 → except Exception: continue（吞掉）
  → detect_atlas_source_root() 返回 None
  → atlas init 不把依赖钉成 atlas-test-platform @ file:///...
  → 生成的骨架 pip install -e . 装不上
  → README「拿到先跑这三步」第 3 步失效，全程 rc 0 零报错
```

**修法**：`read_text_retry(candidate, encoding="utf-8-sig")`。**保留** `except Exception: continue`
——坏 pyproject 确实不是「Atlas 根」的证据，这个兜底是对的；要修的是编码，不是吞异常。
改错方向会把静默降级变成裸崩。

**对照排除**：YAML 侧**不是**缺陷，PyYAML 自己吃 BOM（实测 `yaml.safe_load('\ufeffa: 1')`
→ `{'a': 1}`），所以 `state_machine.py:151` / `dsl.py:274` / `generator/__init__.py:99` /
`devops_sync.py:877` 四处有意不动。

守卫 3 条（`tests/test_init_scaffold.py`）：BOM 正向（断言返回 `tmp_path` 本身，不是「不抛」）/
非 Atlas 名反向对照（防把闸收成「找到 pyproject 就算」）/ BOM + 坏 TOML 仍不算证据。

### ③ 参数值域闸三处

| 站点 | 缺陷 | 危害 |
|---|---|---|
| `autopilot._validate_wait` | `min_rows: -1` → `count >= -1` 第一拍恒真，「等表格就绪」变「立刻就当它好了」；`timeout_ms: 0` 被 `int(x or 10000)` 的 `or` 当空值静默换成 10000 | 假绿 / 静默改语义 |
| `review_code._validate_weights` | 负权重让「该维度缺陷越多 → 维度分越低 → ×负权重 → 总分越高」，评分方向整个反转 | 假绿（越糟越容易过闸） |
| `review_code._validate_severity_deductions` | 负扣分 = 缺陷变加分（实测一条 high 让该维度 130 分、总分 106、grade A）；非映射原来被 `if isinstance(sd, dict)` **静默整块跳过**（用户以为改过扣分，一个键都没生效） | 假绿 / 静默降级 |

三处均按已修先例的形态：入口校验、`ValueError`/`ActionRejected` 点名原值与后果、
测试四件套（负例 / 零例 / bool 混入 / 正向对照）。`test_review_code_config_ranges` 9 条 +
`test_autopilot_wait_range` 7 条。

### ④ 顺带修 `CLAUDE.md` 自身一处拼接损坏

上一波脚本替换把链尾写成 `3183 上一收口位 + 33183 上一收口位 + 3`（重复拼接）。
已修为 `3240 上一收口位 + 17（同波）；3183 上一收口位 + 3`。

### ⑤ 如实留白

`reports/parameter-audit-2026-11-24.md` 另登记 9 条**未修**，按危害排序：

- **A-01（假绿，最该修）**：`review_code.py` 的 `avg_score = round(total/len)` 之后才比阈值
  ——真均值 79.6 显示 80 → `80 < 80` 为假 → `--fail-under 80` 放行。`round` 方向恒定有利，
  属判据本身放宽。
- **A-02（假绿）**：`--review-mode knowledge` 下 `except Exception: registry = None` 无 WARN
  无标记，选择器核对整段不执行（三处以 `registry is not None` 为闸），报告仍自称 `mode: knowledge`。
- **B-01**：`--baseline` 读失败 `except OSError: pass` → 差分 finding 全丢且 `baseline_notes` 为空。
- A-03 `--create-quota -1` 绕过 `CreateQuota.__init__` 的值域闸，输入错误被伪装成
  `EnvBlocked`（OPERATIONAL 族，不进 failed 分布）；A-04 `llm.complete(max_tokens=0)` 被
  `if max_tokens:` 静默丢弃；A-05 `explore_auto(max_failures=0)` 无闸；A-06 `ATLAS_HEADED`
  绕过 `_flag_env` 单点；A-07 `state_machine.run(max_steps=0)` 消息指向「循环/守卫恒假」；
  A-08 `review_code(fail_under=)` 值域闸只在 CLI 面，API 直调 `-1` 门禁永不生效。

### 验证

```
PYTHONPATH=src python -m pytest -q                → 3280 passed / 0 failed / 1 skipped
PYTHONPATH=src python -m pytest -q --collect-only → 3281 collected
PYTHONPATH=src python -m compileall -q src        → rc 0
PYTHONPATH=src python -m atlas doctor             → 正常
```

增量构成（3257 → 3280 = **+23**，逐文件核过、非凑数）：
`test_autopilot_wait_range.py` +7、`test_review_code_config_ranges.py` +9、
`test_json_bom_read_sites.py` 17→21（+4：扫描守卫/自证/两条 `_recycle` 行为测）、
`test_init_scaffold.py` +3。1 skipped 仍是 `test_gen.py` 的「无 allure 形态」条件跳过。

**两条被改的既有守卫均做过变异实证，未触犯「为变绿放宽断言」红线**：

- `test_repo_no_bom.py::test_source_never_writes_bom_encoding`（改为按调用形态判读写）：
  植入 `open(p,"w",encoding="utf-8-sig")` 写样本 → 精确点名 `_tmp_mutation_probe.py:2`，
  同文件的读样本 `open(p,"r",encoding="utf-8-sig")` **未被点名**（mode 判据有效）；删除后 5 绿。
- `test_doc_verification_count.py::test_collect_count_matches_declared_when_repo_is_clean`
  （改为 `collected - conditional_skips == declared`）：把 AGENTS 基线改成 3256 → 该测与
  `test_declared_test_counts_agree_across_all_sites` **同时精确红**；还原后绿。

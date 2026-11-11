# 评审单：全仓冗余/臃肿审计（只审不改）

> 状态：**v1.1** — v1 取证于 2026-11-09 23:09–23:21 当日实跑；**23:2x lead 逐条批复后，A/B/C/D/E 五张表各加末列「lead 裁定（23:2x 批复）」**——只加列，**我的原判定一字未改**（scout 怎么判、lead 怎么裁，两栏并留，将来审计可比对；我改判的 B-07 亦保留改判理由）。裁定列不构成新证据，取证时刻沿用 v1。
> 任务 #23，lead 14:53 派工、23:0x 追加硬要求（按盘上原文自证 / 单列「近期已收敛，不可删」/ 与 support-impl 预检表交叉核对）——**三条均已照办**：每条带当日时刻+命令+输出行、D 档专章 10 条、F 档为逐条比对结果（含**我改判 1 条**：B-07）。
> **本档零代码改动**：src/tests/scripts 一字未动，只产出判据。落 #24 之前，一切「删/合并」都不生效。
> 复核基线：cli.py mtime `23:03:48`、emit_compat.py `23:00:07`、emit_pytest.py `22:24:14`、legacy_fast.py `22:27:18`、fastplugin.py `22:07:16`、test_emit_compat.py `23:10:29`、test_cli_legacy_fast.py `23:04:23`——**全部取证都在这些时刻之后重跑**，早前红单与 lead 早间 ruff 输出（26 条/21 行）**一律未照抄**（当日 src 实跑 24 条=21 F401+3 F841，见 §0 对账）。

## 0. 口径（三档判据，先定尺再看东西）

| 档 | 定义 | 证据门槛 |
|---|---|---|
| **建议删** | 零消费硬证：src 内除定义行外零引用（含模块内自用），且非文档承诺的对外 API | 全仓 AST 顶层符号扫描（`tree.body` 真定义 + 词法引用计数）+ 文档命中数 |
| **建议合并** | 同一判据/同一产物形态存在两份**且都能证实在生产路径上被走到**（否则是「一份在用 + 一份死重」归入删档） | 两份各自的当日消费行号 |
| **建议保留不动** | 看着像冗余但各有不可替代的理由（分域运行、字节锁步、对外 API 面、夹具各异、待接线的已承诺能力） | 逐条给「为什么删了会坏」+ 当日引用面 |

- **消费方定义（lead 硬规则）**：只在 tests 出现**不算**消费方。据此 `scripts/*.py` 与 `examples/*.py` 算真消费（它们是文档里让用户跑的入口），tests 不算。本档凡 tests-only 的条目一律写明「tests=N（不计消费）」并单独判「诊断面 / 死重」。

**与 support-impl 只读预检表的数字对账（23:17 交叉核对，两边独立跑，无冲突）**：他 `ruff 0.17.2 check src scripts tests --select F401,F841` 报 **src F401 21 条 + 全仓 F841 7 条**；我 `--select F401,F841,F811,E402` 只扫 src 报 **24 条**。差的正好是口径：`21 F401 + 3 F841（markdown:188 / review_code:301 / review_code:1200）= 24`，另 `7-3=4` 条 F841 在 tests（我原扫 src 未覆盖，已自证补进 A-15）。**lead 早间那份「26 条/21 行」与两把当日尺都不符，按漂移作废。**

**取证命令可复现**（当日时刻见各档表头）：

- ruff：`python -m ruff check src --select F401,F841,F811,E402 --output-format concise`
- 零消费扫描：`python - <<'PY'`（ast 解析 `src/**/*.py` 顶层 `FunctionDef/AsyncFunctionDef/ClassDef` → 全仓 src+tests+scripts+examples 词法引用计数，排除定义行；601 个顶层符号）
- 文档承诺面：`docs/**/*.md` + `CLAUDE.md`/`README.md`/`AGENTS.md` 逐名命中计数

---

## A. 未用 import / 未用局部（当日 ruff 24 条，全部我这边逐条 AST 复核）

取证时刻：ruff `23:09:02`（24 条，18 fixable）；AST 复核 `23:09:27`。

| ID | 证据（当日命令+输出行） | 影响面 | 处置建议 | 风险 | 本波做？ | lead 裁定（23:2x 批复） |
|---|---|---|---|---|---|---|
| A-01 | `cli.py:1686:38 F401 .scheduling.state`；AST 复核：`_schedule_crontab L1676-1759: import@[1686] Name 引用=[]`（同文件 1423/1526/1663 三处同名 import **都有用**，只有这处空） | 1 行 | **建议删**（只删 1686 这一行 import，别动其余三处） | 零：函数体内无该名引用，删后语义不变 | ✅ 本波可做 | **批准本波**（只删 cli.py:1686 那一行，其余三处同名 import 不动） |
| A-02 | `config_add.py:19:59 F401 .config.env_url_keys`；同函数用的 `ENV_PREFIX_DEFAULT`/`ENV_URL_SUFFIXES` 在 import 行左侧 | 1 token | **建议删**（行内去名） | 零 | ✅ 本波可做 | **批准本波** |
| A-03 | `scaffold.py:14 re`、`:15 shutil`、`:18 env_url_keys` 三条 F401；AST 复核（scaffold mtime 17:24:37）：`Name 节点含 re=False shutil=False 属性访问=无`。早先用词法 grep 扫 `re.` 与 `shutil.` 的 8 处命中全是产物文案串 `run_allure.py` 的**假命中** | 3 token/2 行 | **建议删** | 零 | ✅ 本波可做 | **批准本波** |
| A-04 | `generator/__init__.py:13 typing.Any`、`emit_probe.py:7 Any`、`sources.py:7 Any`、`tower.py:9 Any`、`knowledge.py:7 Any` 五条 F401（`from __future__ import annotations` 在场，注解里的 `Any` 属字符串域，AST 不计） | 5 行 | **建议删** | 零 | ✅ 本波可做 | **批准本波** |
| A-05 **（单列：疑似门面 re-export）** | `generator/__init__.py:19 FlowEmitError`、`:22 ChecklistError` 两条 F401，ruff 自带提示「consider removing, adding to `__all__`, or using a redundant alias」；当日复核：`含 __all__ = False`；经门面 import 这两个名的文件=**无**；经门面 import `GenOptions`/`generate` 的有（cli.py、tests 两处）——即门面**整体是活的**，只有这两个错类型是死的 | 2 行（对外 API 语义） | **保留待 lead 定门面 API 面**：要么补 `__all__` 认门面（则改测钉「门面可取」），要么只删这两名（`Any` 走 A-04）。**不属死代码**，属「API 决策」 | 删=可能砍掉未来 `from atlas.generator import FlowEmitError` 的接法；补 `__all__`=新增对外契约。两者都不该 scout 定 | ❌ 留待后续（lead 批） | **lead 裁 ②：补 `__all__`**（`["GenOptions","generate","FlowEmitError","ChecklistError"]`）——rc 归因是用户可见契约，补 `__all__` 让死 import 变显式契约，比删更符合「顶级项目」。**不删** |
| A-06 | `checklist.py:5:43 F401 .dsl_utils.has_placeholder`；当日复核 `has_placeholder` 全仓命中只有：dsl_utils 定义行 + 函数内自递归 + checklist 这条死 import | 1 token + 连带 B-03 | **建议删**（先删 import；函数本体见 B-03） | 零 | ✅ 本波可做 | **批准本波** |
| A-07 | `emit_compat.py:227:43 F401 .emit_pytest._pascal`；AST 复核（emit_compat mtime 23:00:07）：`compat_specs_from_flow@L220: _pascal 引用=[] _ident_slug 引用行=[294]`，另一处 `render_compat_test_module@L659: _pascal 引用行=[667]` **在用** | 1 token | **建议删**（只删 L227 行内的 `_pascal`，L666 那处别动）。**执行归口**：emit_compat 是 emit-impl 的文件（support-impl 已声明不碰他人文件），#24 派工时点到他名下 | 零；`_pascal` 符号本身在 emit_pytest 有大量真消费（docs 引用 12 处），**删的只是这一处多余 import，不是删符号** | ✅ 本波可做（派 emit-impl） | **批准，但归口+排期改**：派 emit-impl，**等 #27 落定后再动**（不在同一文件的在途 hunk 上叠删） |
| A-08 | `emit_flow.py:7:30 F401 .model.CONFIDENCE_HIGH`、`plaintext.py:12:21 CONFIDENCE_HIGH`、`:12:38 CONFIDENCE_MEDIUM`；复核：plaintext L12 同行 `CONFIDENCE_LOW` **在用**（markdown/plaintext 分级判据），高/中两档是残留 | 3 token | **建议删** | 零 | ✅ 本波可做 | **批准本波** |
| A-09 | `plaintext.py:7:8 F401 json`、`:13:45 F401 .markdown._match_close`；复核：`_match_close` 定义在 markdown.py:88、**markdown.py:63 真用**，plaintext 的 import 是死的；plaintext 内 `\bjson\.` 零命中 | 2 token | **建议删** | 零 | ✅ 本波可做 | **批准本波** |
| A-10 | `openapi.py:3:8 F401 copy` | 1 行 | **建议删** | 零 | ✅ 本波可做 | **批准本波** |
| A-11 | `review_code.py:25:8 F401 time`；复核：`\btime\.` 三处命中全在字符串/注释里（L121 规则文案、L480 docstring、L487 注释），真调用零 | 1 行 | **建议删** | 零 | ✅ 本波可做 | **批准本波** |
| A-12 | `review_code.py:301:5 F841 deductions`；当日读原文：L301 `deductions = config.get("severity_deductions", SEVERITY_DEDUCTIONS)` 之后**再未出现**，同一判据在 L228 已单点消费并烤进 `f["deduction"]`，L312 只做 `sum(f["deduction"])` | 1 行 | **建议删**（留着就是「第二份 severity_deductions 读法」，#92 温床） | 零：删后 L228 那份是唯一真相 | ✅ 本波可做 | **批准本波** |
| A-13 | `review_code.py:1200:9 F841 identity`；当日读原文：L1200 造了 `file:line:rule_id` 串，`journal.draft_from_text(...)` **不接收**该参数；experience.py:24 内部自算 identity（`task` + `exception_type` + `failure_class` + `pitfall_id` 四段拼接）——即**去重已经发生**，只是键形态与 L1200 不同 | 1 行（+1 条落地注需求） | **建议删**（去重不缺它）；若 lead 想钉「按 file:line:rule_id 去重」，那是**改键形态=改历史草稿 id**，属另一波决策，不在删减面 | 低：删=纯净化；不删只是噪音 | ✅ 本波可做（连同 #22 落地注一句：去重键来自 task 而非 file:line） | **批准删**（lead 改判后确认：experience.py:24 已按 task 段算 id、`task` 已含 file:line → 去重本就发生，变量多余）。lead 此前发 support-impl 的「加 `identity_key` 参数」指令**作废并撤回**；#22 落地注一句「去重键来自 task 而非独立 identity 字段」 |
| A-14 **（F841 里的「假死代码」）** | `markdown.py:188:13 F841 low`；当日读原文：L188 `low = (preceding or "").lower()` 后，L189/L191 两个分支都拿 **`preceding` 原串**匹配 `"响应"/"返回"/"response"`（小写字面量）——文档里写 `Response:`/`RESPONSE` 时判不进响应腿 | 1 行 + 潜在行为缺口 | **建议保留不动 + 登记缺陷**：删 `low` 会把「本应大小写不敏感」的缺口坐实成静默；改用它=改文档嗅探行为（既有 markdown 夹具测可能翻）。二者都不是冗余处置，是**缺陷处置** | 中：动它属行为变更，须带夹具测 | ❌ 留后续缺陷单（非 #24 面） | **不动**（采纳）：确认为真 bug，**已派 emit-impl 修语义**，不属删减面 |
| A-15 **（tests 侧 F841，原扫 src 未覆盖，23:17 自证）** | `tests/test_boundaries.py:274 path`（函数 `test_classify_main_refuses_broken_ledger` 内 Load 引用=零）、`test_gen.py:534 p`（`test_gen_fast_help_mentions_pairing`，零）、`test_waits.py:60 kinds`（`test_wait_for_rows_settles_before_counting`，零）、`test_legacy_fast_plugin_xcheck.py:267 never`（`test_x3_c1_class_level_multi_page_sequence_budget`，零；原文 `never = Page(scenario="never")` 造了不用） | 4 行（测内） | **建议删**（前 3 条）；第 4 条 **先问作者**：xcheck 里造 `never` 场景的 page 不调用，可能是「本该断言永不静默路径却漏接线」——与 A-14 同类（意图未兑现），不是纯冗余 | 零（前 3 条）；第 4 条若属漏接线，删行会**掩盖判据缺口** | ⚠️ 前 3 条本波可做；第 4 条留 lead 问 #21 作者 | 前 3 条**批准本波**（派各测作者）；**第 4 条（xcheck `never`）不派删**——lead 转 xcheck 自判是否漏接线 |

**A 档小计**：建议删 13 条 src 项（覆盖 24 条中的 21 条）+ tests 侧 3 条；1 条门面单列待裁（2 条 ruff 项）；1 条登记缺陷不动（markdown `low`）；1 条 tests F841 待作者确认（xcheck `never`）。

---

## B. 零消费符号（全仓 AST 扫描：601 个顶层符号中 18 个 src 外零消费）

取证时刻：`23:09:27`（含 examples 面）。按 tests 引用数升序判「死重」还是「诊断面/对外面」。

| ID | 证据（当日命令+输出行） | 影响面 | 处置建议 | 风险 | 本波做？ | lead 裁定（23:2x 批复） |
|---|---|---|---|---|---|---|
| B-01 | `src/atlas/generator/emit_probe.py::emit_requires  tests=0 examples/scripts=0`（唯一两条真零消费之一，当日核体量 **L41-43 共 3 行**）；docs 命中=0、CLAUDE/README/AGENTS 命中=无 | 3 行 | **建议删**（gap3 的 requires 直出后来由 emit_flow 承担，函数无人接、无人测、文档无名） | 低：删前跑一次 `python -m compileall -q src` + collect-only | ✅ 本波可做 | **批准本波** |
| B-02 | `src/atlas/scheduling/state.py::update_task  tests=0 scripts=0`（另一条真零消费）；docs 命中=0。C1 实际写路径是 `apply_patch`+`save_doc`（当日读 dispatch 面确认在位） | 1 个函数 | **建议删**（E.1 判据「C1 台账写路径唯一」反而因它留两个入口而变软） | 低 | ✅ 本波可做 | **批准本波** |
| B-03 | `src/atlas/generator/dsl_utils.py::has_placeholder`——src 内除定义与自递归外唯一引用是 A-06 那条死 import；tests=0、docs=0 | 1 个函数（A-06 删后即为零消费） | **建议删**（与 A-06 同批）；`scan_placeholders` 是**在用**的占位符扫描单点，别一起收 | 低 | ✅ 本波可做 | **批准本波** |
| B-04 | `waits.py::wait_for_dialog` 与 `wait_for_dialog_gone`：`tests=3 / src=0 / scripts=0`；当日核文档承诺面：CLAUDE.md 与 README.md 的 `waits` 行**只列 rows/mask_gone/vue_field/url_change**，`wait_for_dialog 提及=0`，docs/gaps 命中=0 | 2 个函数 + 2 条测 | **建议合并→收口到「要么接线要么删」**：它们不在任何契约/文档面上，纯 tests-only；但 `waits` 是通用语义等待**门面**，dialog 属常见族——留给 lead 判「API 面」还是「死重」，我倾向**后续波次删（含删那两条测）**，本波不动（waits 属 gap9 已收口资产） | 低-中：删=减两处用户可见 API；不删=留着未声明的 API 面 | ❌ 留待后续（lead 裁 API 面） | **不动（lead 改判为保留）**：`waits` 是语义等待公开门面，dialog 属常见族；**文档没写是文档欠账，不是 API 该删**（本档「tests-only+文档零承诺」两证属实，但结论被裁为补文档而非删码） |
| B-05 | `devops_sync.py::resolve_mcp_url L294-306`：src 内仅 `resolve_mcp_server`（L226-277，真在用的那份）+ 一层 `.url` 透传；tests=4、src 消费=0 | 13 行包装 | **建议保留不动**（**已收敛的兼容包装**，不是两份判据：当日读原文确认它只 `return resolve_mcp_server(...).url`，零复制规则）。若要删，删的是包装+其 4 处测，不是「合并两份逻辑」 | 低 | ❌ 留待后续（纯 API 面收缩） | **不动**（采纳） |
| B-06 | `devops_sync.py::format_sync_summary L1222+`：`tests=1 / src=0`；当日读 cli `_devops_sync`：L1344-1350 **另手拼**了一份 `atlas devops-sync [mode] + 用例数/测试计划` 摘要，字段是 format_sync_summary 的子集（缺 root/suite/state key/目录/执行人） | **两份输出形态**，其中库那份无人吃 | **建议合并**（本波不做）：CLI text 腿改调 `format_sync_summary`，或明确删库那份只留 CLI 文案——现状态是「库里有个更完整却没人用的格式化器」，典型 #106 断链同族 | 中：动 CLI 输出是用户可见变更，需测钉 ASCII/cp936（format_sync_summary 的 docstring 正是为 GBK 教训写的） | ❌ 留待后续（与 #22 文案收口同波） | **留后续**（采纳） |
| B-07 | `legacy_fast.py::installed_targets L660-662`（当日读原文：3 行，`return [t for t, _, _ in _INSTALLED.values()]`）：src=0、fastplugin=0、docs=0；还原面走 `uninstall(None) L635-657`，**不经它**；唯一提及是 `test_legacy_fast.py:1194-1202` 的 **vendored 契约名册断言**（把 10 个符号列进 `hasattr` 名单） | 3 行 + 名册 1 个名字 | **改判：可删**（与文件所有人 support-impl 的预检表一致）。我初版写「保留：vendor 字节锁步成本」**经复核不成立**——`vendor_shim_source`/`vendor_provenance`（emit_compat L102-148，当日读原文）是**生成期现读真文件字节算 sha256**，符号增减自动跟随，没有手工基线要维护 | 低：删符号**必须同时摘名册里的名字**（那才是它唯一"契约"来源），否则 `hasattr` 断言当场红 | ❌ 排 #24 批（不在本波）：legacy_fast 是 vendor 真源且 #21 在飞（当日 mtime 22:27:18） | **批准删，排 #27/emit-impl 收工后**（避免与在途 hunk 撞）；执行时同步摘 `test_legacy_fast.py:1201` 名册里的名字 |
| B-08 | `legacy_fast.py:284 FastWaitStats.to_stats_json`：fastplugin 只吃 `to_jsonl`(L122)+`aggregate`(L124)，`to_stats_json` src=0、tests=2（L791/797 **真行为断言**：坏目录返回 0、好路径返回 1，非恒真） | 第二形态 stats 落盘 | **待 lead 裁（排期）**：本档作者= gap11 设计作者，**设计 v2（2026-11-09 同步 G11-4）已把 stats 形态钉成「`ATLAS_FAST_WAITS_STATS` 逐调用 jsonl + 收尾一行 summary」**、§E 度量 3 明写「从逐调用 jsonl 派算」→ 单文件聚合形态是**被 v2 取代的旧形态**（support-impl 引的「§A 写 `reports/settle_stats.json`」是 v1 文本）。故 **①删（`aggregate()` 保留，fastplugin 收尾行真在用）与设计现行形态一致**；②留的唯一理由是 R-G1 真机想要一份人类可读汇总——那属**新增需求**，要接须同时改设计文档 | 低-中：改 vendor 字节自动跟随（见 B-07 复核，无手工基线） | ❌ 待 lead 裁（我倾向 ①删，随 #24） | **lead 裁：删**（与 B-07 同批）。依据=本档 B-08 自证「设计 v2 已把 stats 钉成 jsonl+summary、§A 的 `settle_stats.json` 是 v1 作废文本」——**这条排期争议按本档结论收，不按我上轮说法** |
| B-09 | `emit_pytest.py::emit_pytest_module L841-851 / emit_pytest_from_flow L854-859`：src=0（包路径走 `emit_pytest_package L902-986`），tests=13/15；docs/gaps 与顶层文档命中=0；当日读原文：两者都是 `render_test_module(specs,...)` 之上的**薄入口**（内部调用零复制，`_specs_from_endpoints`/`_specs_from_flow` 各一处） | 对外 API 面（gap7 形态2 单模块产物） | **建议保留不动**：不是重复实现，是「包 vs 单文件」两种产物形态的两个入口，测钉的是**确定性文本**契约；删=废掉单模块产物面 | 中：API 收缩需 lead 认 | ❌ 留待后续（lead 裁 API 面） | **不动**（采纳；未列入批准删，API 面维持） |
| B-10 | `toolbox.py::WebToolboxSensor L36`：`tests=46 / src=0 / scripts=0`；当日核：`src`(排除 toolbox.py) 内 `sensor=0 处`、`WebToolbox=0 处`，cli.py 子命令清单**无** toolbox/bridge 入口 | **已承诺未接线**能力（CLAUDE.md 有整行「toolbox 感知器官」） | **建议保留不动**——这是 #106/#86「模块存在但没接」家族，属**接线缺口**不是臃肿；删了等于把 #105/#120/#140 三轮真实取证修出来的判据面清零 | 高：删=能力回退 | ❌ 不动；登记到接线债（lead 排期） | **不动**（采纳）；接线债单列，lead 排期 |
| B-11 | `boundaries.py::parse_unified_diff tests=8 scripts=1`、`classify_change tests=7 scripts=1`、`boundary_draft_args tests=1 scripts=1` | C5 判据面 | **建议保留不动**：scripts=1 是 `scripts/boundary_classify.py` 真消费（当日核实在位），门面无 src 内调用是**设计**（CLI/脚本是薄消费方） | — | ❌ 不动 | **不动**（采纳） |
| B-12 | `creators.py::make_scripted_creator tests=9 scripts=2`、`plugins/demo/creators.py::make_sales_order_creator tests=3 scripts=2`、`credential_gate.py::mask_placeholder_value tests=2 scripts=2`、`emit_compat.py::is_atlas_free tests=7 src=0`、`vendor_is_current tests=12 src=0` | 对外/产物闸面 | **建议保留不动**：前三个有 scripts/examples 真消费；后两个是 gap11 C.3/C.4 **产物闸判据**（#18 当日 23:00 还在改，正是它的消费方接线期） | — | ❌ 不动 | **不动**（采纳） |

**B 档小计**：建议删 4（B-01/02/03 当日实跑真零消费 + B-07 改判可删）；建议合并 1（B-06）；其余 7 组给的是「为什么删了会坏」（B-04 API 面待裁、B-05 已收敛包装、B-08 待 #20 定、B-09 产物形态面、B-10 未接线的已承诺能力、B-11/B-12 脚本与闸门面）。

---

## C. 重复实现 / 模板 / 夹具（含「看着像重复其实不是」的反证）

| ID | 证据 | 影响面 | 处置建议 | 风险 | 本波做？ | lead 裁定（23:2x 批复） |
|---|---|---|---|---|---|---|
| C-01 | `emit_fast.py` 的**产物底座模板文本** `_FAST_SUPPORT_SOURCE`（AST 取串 `20888` 字符）内含 `wait_for_rows/wait_for_mask_gone/wait_for_vue_field/wait_for_url_change`，与 `src/atlas/waits.py`（L141/L177/L182/…）**同名同语义两份**；当日逐行对比差异：waits 轮询节拍是字面 `100`（L162 `timeout_ms // 100`）、模板侧是常量 `TICK_MS=100`（emit_fast L89，docstring 明写「F-4：轮询节拍用裸 TICK_MS——退避乘算会穿过扫描尺」） | 约 60 行业义 ×2 | **建议保留不动（分域，删不掉）**：红线 #4/#2 要求产物**零 import atlas**，waits 在引擎侧、模板在产物侧，天然两份；两份**已经过 #105/#133 两轮同步修**（attached 判据、`_is_transient` 分流同款），漂移史在测里。**注记给 #22 落地注**：新加等待族时两处都要动，且节拍常量不得各自漂移 | 高：强行「合并成一把」=产物反向依赖 atlas，破红线 | ❌ 不动（登记同步义务） | **不动**（采纳）+ **写入 #22 落地注**：新加等待族两处都要动、节拍常量不得各自漂移——同步义务不是冗余 |
| C-02 | 同名担忧反证：`emit_compat.render_compat_conftest`（235 有效行）vs `emit_fast._FAST_CONFTEST_SOURCE`（399 行）——当日 AST 提取两串做**逐行交集**：相同行 **1 行**（只是 `"""`），相似度 `0%` | — | **建议保留不动 + 记入「不可删」**：fixture 名单重合（同名 `sole_page/get_page/plan_ctx/browser/context/page/_case_watchdog/pytest_addoption/pytest_configure`）是**契约要求**（gap11 C.5 与旧仓逐一对名），实现体完全不是一份东西。照名单同名判重复会写出一条假删项 | — | ❌ 不动 | **不动**（采纳） |
| C-03 | `el-loading-mask` 字面量当日命中：`waits.py:142/177`（默认参 + 注释）、`emit_fast.py:422/445/446`（模板默认参 + 注释）、`legacy_fast.py:31/63`（垫片 JS 拼装）；tests 另有 6 处 | 三处运行域 | **建议保留不动**：三个运行域各持一份是刻意的（引擎 / 产物 / 垫片 JS）。`legacy_fast` 那份是**唯一有「全枚举判可见」纪律**的（多遮罩 2026-10-30 教训），另两份是 `state="hidden"` 委托 Playwright——形态不同不是复制 | 中：合并成常量=让垫片依赖引擎面，违 gap11 C.3 | ❌ 不动 | **不动**（采纳） |
| C-04 | `scripts/import_jmeter.py` L131 `if "${" in value:` 自己扫占位符，`generator/dsl_utils.py` L14 `_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")` 是第二把刀；dsl_utils **docstring 自证**：「本包内先独立实现；『单一真相』待后续 P3 收敛……届时让 import_jmeter.py 改为 import 这里」；当日核 `import_jmeter.py` 内 `atlas.generator` 的 import = **无（P3 未收敛）** | 占位符/ body 分流两套判据 | **建议合并（P3 既有计划，不在本波）**：判据上移 dsl_utils、脚本改 import，行为零变化由既有 import_jmeter 测钉 | 中：脚本是用户面，改 import 需保 `PYTHONPATH=src` 口径 | ❌ 留待后续（照 docstring 自记的 P3） | **留后续**（P3 既有计划，采纳） |
| C-05 | 测试私有 helper 同名扫描（`23:13:42`）：170 个模块级 `_` 开头 helper 中同名 ≥3 处只有 **5 个名字**（`_seed`/`_stub_cli`/`_flow`/`_assert_compiles`/`_clean_env`），且**每组的三份实现字节互不相同**（「完全相同版本数=3」=三份各异） | 0 份真复制 | **建议保留不动**：同名不同体=各自夹具语义（例：`_assert_compiles` 在 emit_compat 测里过 compat 闸、在 emit_fast 测里过 fast 闸）。抽公共 conftest 反而会把「夹具真构造」（F-23-B）抹平成一份 | 低但无收益 | ❌ 不动 | **不动**（采纳） |
| C-06 | 假 page 类分布（当日文件 mtime 一并记）：`test_emit_fast.py`(19:41:48) 8 个假类、`test_fast_artifact_executable.py`(19:11:16) 5 个、`test_legacy_fast.py`(22:26:42) 4 个、`test_legacy_fast_plugin_xcheck.py`(22:47:16) 4 个、`test_legacy_compat_xcheck.py`(22:59:10) 1 个（**且那 1 个在子进程源码串里**，读原文 L107-138 是 subprocess 执行的独立脚本，无法 import 宿主夹具） | 夹具分散 | **建议保留不动**：xcheck 面要的是「产物/垫片在干净进程里自证」，宿主夹具正是它必须绕开的东西；`test_legacy_fast.py:909` 与 `:377` 两处 FakePage 也不同体（前者记账 `sleeps`、后者 `screenshot_calls`+三态 `SCENARIO`） | 中：合并夹具=判据变松（#21 的对抗面会被夹具吸收） | ❌ 不动 | **不动**（采纳） |
| C-07 | `scripts/linux_acceptance.py`(63 有效行) vs `linux_acceptance_remote.py`(93 有效行)：`23:18:32` **逐行交集实跑 = 9 行**，且全是脚手架（`"""`/`from __future__`/`import json`/`import sys`/`from pathlib import Path`/`ROOT = Path(__file__).resolve().parents[1]`/`def main() -> int:`/`if __name__ == "__main__":`）；职责当日读头部：本机五项门禁 vs 远端打包上传（`ATLAS_BROWSER_EXECUTABLE`）。文档引用双向非零：`CLAUDE.md:164`、`AGENTS.md:30`、`impl_pi/c3-baseline.md:37/:84`、`final-verification-p2.md:35` | 非重叠 | **建议保留不动（两文件都不可删、也不建议合并）**：合并=挪走 `linux_acceptance.py:37`，而 c3-baseline 有两条在案记录**按行号**把它当「已清偿债」锚点，挪一次就漂空。**另**：lead 早前「删 lite/remote/docker 中间产物」的条目在盘上不存在（`scripts/*acceptance*` 实跑只有这两个文件），照抄会成「删不存在的东西」 | 低 | ❌ 不动 | **不动**（采纳；lead 早前 lite/remote/docker 条目按本档作废） |
| C-08 | 体量观察（**只报事实，本档不判删**）：`cli.py 104.1K/1763 行`、`dsl.py 74.6K`、`review_code.py 59.3K`、`devops_sync.py 55.8K`、`emit_pytest.py 53.6K`、`emit_fast.py 52.4K`、`emit_compat.py 47.9K`；tests `72 files/29589 行`（> src `73 files/21641 行`）、docs/gaps `30 md/6513 行`（gap9 主档 68.3K + lead-rulings 67.8K） | 阅读成本 | **建议保留不动**：本仓测多于码是 F-23 纪律的**结果**不是病；`cli.py` 唯一可议的是 27 个子命令挤一个 `main` 分派（当日子命令清单实测），但那是**重构**（要动 #20a 门禁），不属删减波 | 高：动分派面=大改，与本波冲突 | ❌ 留待后续（若要拆，另立波次） | **不动**（采纳；重构另议） |

---

## D. 近期已收敛，**不可删**（lead 要求单列，本节与「建议删」同等重要）

| ID | 看起来像什么 | 当日盘上原文（时刻） | 为什么已经是修好的形态 | lead 裁定 |
|---|---|---|---|---|
| D-01 | 「`cli.py:1187 _compat_gate` 与 `emit_compat.compat_combo_problem` 各存一份组合判据」（xcheck 早前 c9b 红单原话） | `23:09:27` 读 cli.py L1187-1197：函数体只有 `from .generator.emit_compat import compat_combo_problem` → `problem = compat_combo_problem(compat, fast)` → `if problem: raise ValueError(problem)` | **已收敛**：CLI 侧只剩懒 import + 委托，唯一判据位在 emit_compat（docstring 自记 lead c9b 裁决：旧内联版对 `"bogus"/""/"LEGACY-SUITE"` 放行与生成器面分叉，八组合等值扫抓出后收口）。照旧红单收录=写出一条「删一个已经不存在的东西」 | **采纳为不可删**（lead 举的实例，按盘上原文钉死） |
| D-02 | 「`_pascal` 在 emit_compat 被 import 两次，重复」 | 同刻 AST：`compat_specs_from_flow@L220` 的 `_pascal` 引用 `=[]`（死），`render_compat_test_module@L659` 的 `_pascal` 引用 `[667]`（活） | 两处 import 中**只有一处**该删（A-07）。函数本身在 emit_pytest 内被大量真消费（L411/721/726/791/796/890/894）——`_pascal` 是类名单一真相，**不是**待合并面 | **采纳**（A-07 只删 L227 那处 import） |
| D-03 | 「`emit_fast.py` 与 `generator/__init__.py` 都写底座模板」 | `23:12:02`：AST 取串证明模板常量只在 emit_fast（`_FAST_SUPPORT_SOURCE 20888` / `_FAST_CONFTEST_SOURCE 15168`）；`__init__.py` 只有 `from .emit_fast ...` 消费 + 注释「底座模板判据单一来源=atlas.generator.emit_fast，本文件零模板文本」 | **已收敛**（gap9 C.2 那次收口的结果），且 emit_fast 的测钉「__init__ 内不得出现模板文本」这一反证向判据 | **采纳** |
| D-04 | 「`resolve_mcp_url` 与 `resolve_mcp_server` 是两份 MCP 选址判据」 | `23:12:17` AST：L294-306 体内**只有** `return resolve_mcp_server(...).url` | 已收敛成一层 `.url` 透传（兼容旧签名），规则一份。**若** lead 要收 API 面，那是删包装（B-05），不是「合并两份判据」 | **采纳**（关联 B-05 不动） |
| D-05 | 「fastplugin 与 legacy_fast 各写一份 stats」 | `23:11:24`：`to_jsonl` 在 legacy_fast:222/278 定义、fastplugin:122 调用；`aggregate` 在 legacy_fast:246/289/293 定义、fastplugin:124 调用 | **已收敛**：落盘与汇总的**算法**都在 legacy_fast（vendor 单一真源），插件只做「叫谁」；唯一悬着的是 `to_stats_json` 这第二形态（B-08），不是两份刀 | **采纳**（关联 B-08 已裁删） |
| D-06 | 「`emit_compat` 的 import 面闸是 `is_atlas_free`/`atlas_import_hits`/`atlas_import_text_hits` 三份」 | `23:13:24`：`is_atlas_free L97-99` 体内调用= `['atlas_import_hits','atlas_import_text_hits']`，两个原语在全仓 src 的**唯一**引用就是 L99 | 一份组合 + 两个原语（AST 语句行判据 / 文本兜底判据，正是 gap11 C.3 的「两条判据各管各面，不互抄」）。**唯一缺口**是 `is_atlas_free` 在 src 暂无消费行（#18 接线期）——**不是**冗余 | **采纳**；`is_atlas_free` 生产接线属 #18 在飞，不算冗余 |
| D-07 | 「`legacy.py`/`runtime.py`/`cli.py` 三处各自校验 legacy 路径」 | CLAUDE.md #138 行 + 当日盘上：判据与消息单点在 `config.check_legacy_paths`，三方是委托 | 已收敛（#138），别再报「三份校验」 | **采纳** |
| D-08 | 「兼容 conftest 与快包 conftest 同名 fixture=复制」（=C-02 的正面表述） | 逐行交集 1/235 行 | 同名是**契约对齐**（A0 表逐一对名），实现两份不同东西 | **采纳** |
| D-09 **（lead 已在 #17 裁决 4 记过一笔，本节再钉一次）** | 「`legacy_fast._TRANSIENT_MARKS` 是 `waits._TRANSIENT_MARKS` 的复制=重复实现，该合并」 | `23:18:32` 当日读原文：`waits.py:42` 与 `legacy_fast.py:137` 两份字面量逐字相同 `("execution context was destroyed", "navigation", "detached")`，各自在同文件 L52 / L344 被 `any(mark in text …)` 消费；legacy_fast L135 注释自证「与 `atlas/waits.py::_TRANSIENT_MARKS` **同源**，vendored 文件不得 import atlas」；锁步测**两条**在位：`test_legacy_fast.py:533 test_d1_2_transients_marks_lockstep_with_waits` + `test_emit_fast.py:414 test_transient_marks_lockstep_with_atlas_waits` | **不可删也不可合并**：红线 #4 下产物/vendored 面零 import atlas，复制是必然代价；**锁步相等测就是唯一可接受形态**（改一处必红另一处）。审计单若把它列进删减，就是把已裁决项重新摊开 | **采纳：不可删也不可合并**（#17 裁决 4 再钉一次） |
| D-10 | 「`fastplugin`/`legacy_fast` 少了 `credential_gate` 净化刀=缺项/该补一份」 | `23:18:32`：`fastplugin.py` 的 import 面实为 `['from __future__ …','import importlib.util','import os','import re','import sys','from pathlib import Path']`——**零 atlas import**；关键词 `sanitize` 与 `credential_gate` 在 fastplugin 与 legacy_fast 内命中数 **0 / 0** | **不是缺项，是刻意的「不复制」决定**：产物侧不落不可控文本（垫片只处理毫秒与选择器），所以没有需要净化刀的落盘面；补一把刀反而违反红线 #92（第二份判据）。同 D-09 属「已定案的两面」，别只看见一份看见两份就当冗余 | **采纳**+写入 #22 落地注：不复制净化刀是刻意决定 |

---

## E. 防复发（不是删项，是给 #24/#22 的收口动作）

| ID | 证据 | 建议 | 本波做？ | lead 裁定 |
|---|---|---|---|---|
| E-01 | `23:0x` 查 pyproject.toml / CI / *.cfg / *.ini / *.toml 内 ruff / flake8 / pylint / F401 四个关键字 = **无匹配**，而当日 ruff 实跑出 24 条——**门禁里没有这把尺** | 把 `python -m ruff check src --select F401,F841,F811,E402` 接进五项门禁（AGENTS §2 收口面），否则 A 档删完还会再长回来。**注意**：这是新增门禁（属实现），须 lead 排期，不在 #24 删除批里 | ❌ 留后续（新增门禁=改动，非删减） | **lead：观察属实，但不属本波**（新增门禁=实现改动）→ 记为后续波次，写进本档即可 |
| E-02 | A-14 的 `low`、A-13 的 `identity` 说明 F841 里混着「未兑现意图」 | #24 执行时对**每条 F841** 先读分支原文再删（本档已逐条标好哪条能删哪条不能），不许 `--fix` 一把梭（ruff 自己也标了 3 条 unsafe） | ✅ 写进 #24 执行纪律 | **采纳**，写进 #24 执行纪律 |

---

## F. 与 support-impl 只读预检表的交叉核对（表 23:1x 已到，本节为逐条比对结果）

support-impl 给**消费方计数**，我给**判定**。数字层面无冲突（见 §0 对账）。判定层面：**5 条一致、1 条我改判、3 条待 lead 裁**。

| 结果 | 条目 | 说明 |
|---|---|---|
| ✅ 一致（可删） | A-01…A-12 全部 src 侧 F401 + A-12 `deductions`（他亦自证 :228 真消费、:274-277 校验覆盖、:258 进自述 → :301 纯冗余） | 两边独立跑同一把尺，结论同 |
| ✅ 一致（归 lead 裁，非冗余） | A-05 `FlowEmitError`/`ChecklistError` 门面（他补的硬证：`__init__.py` **无 `__all__`**，真实消费方全走子模块路径 cli.py:1241/1242、test_gen.py:15） | 两选项：删 / 补 `__all__` 转正公开 API |
| ✅ 一致（不可删） | A-14 `markdown:188 low`、A-13 `review_code:1200 identity` 属**语义疑点**；C-07 两个 `linux_acceptance*` 不可删不可合并；D-09 `_TRANSIENT_MARKS` 锁步形态、D-10 `fastplugin` 不复制净化刀 | 他原话：「审计单若把它当重复实现列删减，请标不可删」——已照办，见 D-09/D-10 |
| 🔄 **我改判** | B-07 `installed_targets`：初版我写「保留（vendor 锁步成本）」，复核 `vendor_shim_source`/`vendor_provenance` 现读真文件算 sha256 → 成本论不成立，**与所有人一致改为可删**（排 #24 批，须同时摘名册断言 L1201 的名字） | 已在本档标注改判理由，不留两版本 |
| ⚠️ 待 lead 裁 1 | **B-08 `to_stats_json`**：他给的选项 ①删（`aggregate()` 保留，fastplugin 收尾行真在用）②产物侧接路径；我说「#20 若需人类可读汇总它就是那把刀」——**同一件事的两种排期**，需 lead 拍「现在删」还是「#20 前先留着」 | 双方证据互不矛盾 |
| ⚠️ 待 lead 裁 2 | **B-04 `wait_for_dialog`/`wait_for_dialog_gone`**（他表未列=他只扫 F401/F841；我扫的是零消费符号）：文档零承诺、tests-only | 删 or 认作 waits 公开面 |
| ⚠️ 待 lead 裁 3 | **B-09 `emit_pytest_module`/`emit_pytest_from_flow`**（tests 13/15、src 0、docs 0）：是 gap7 形态2「单文件产物」入口，删=收 API 面 | 单文件产物是否仍对外 |
| ➕ 新增（两人都不动，只登记） | **B-10 `WebToolboxSensor`（tests 46、src/scripts/CLI 子命令面全零）**——「模块存在但没接」家族（#106/#86 同族），CLAUDE.md 有整行承诺 | 属**接线债**，不入删减批 |


---

## G. 汇总（供 lead 逐条批）

- **建议删（本波可做，零行为变更，16 个动作点）**：A-01、A-02、A-03（3 token）、A-04（5 行）、A-06、A-07（**派 emit-impl**，只摘 import 名不删符号）、A-08（3 token）、A-09（2 token）、A-10、A-11、A-12、A-13、A-15 前 3 条（tests 侧）、B-01、B-02、B-03。
- **建议删（排 #24 后续批，本波不做）**：B-07 `installed_targets`（须同步摘 `test_legacy_fast.py:1201` 名册里的名字）、B-04 `wait_for_dialog*`（待 API 面裁定）、A-15 第 4 条 `never`（先问 #21 作者是否漏接线）。
- **建议合并（留后续）**：B-06（CLI 摘要腿 vs `format_sync_summary`，两份输出形态二选一）、C-04（P3：占位符/body 分流判据上移 dsl_utils，脚本改 import，dsl_utils docstring 自记）。
- **建议保留不动（说明为何像冗余而不可删）**：C-01（引擎侧 waits vs 产物模板，红线 #4 天然两份，只登记同步义务）、C-02/D-08（同名 fixture 逐行交集 1/235）、C-03（mask 字面量三分域）、C-05/C-06（测内同名 helper 三份各异、xcheck 子进程夹具不可共享）、C-07（两个 acceptance 非重叠 + 行号锚点）、C-08（体量只报事实）、B-05/D-04（`.url` 透传包装）、B-09（单文件产物 API 面）、B-10（**toolbox 未接线债**）、B-11/B-12（scripts 真消费 + gap11 产物闸）、A-05（门面待裁）、D-01…D-03、D-05…D-07（近期已收敛专章）、D-09（`_TRANSIENT_MARKS` **不可删也不可合并**，锁步测是唯一形态）、D-10（fastplugin **不复制**净化刀是决定不是缺项）。
- **缺陷登记（不属删减面，另单）**：A-14 markdown `low` 大小写敏感漏判（本应不敏感）、A-13 去重键与 `identity` 意图不一致（#22 落地注一句）、B-06 双摘要、B-10 toolbox 承诺未接线、A-15 第 4 条 `never` 疑似漏接线。
- **执行纪律（#24）**：每批删除后当日三件套（`python -m compileall -q src` → `pytest --collect-only -q` 绿 → 全量数 ≥ 删除前当日实跑值）；F841 逐条先读分支再动；**不得**用 `ruff --fix` 批量落刀（3 条 unsafe，且会把 A-14/A-15-4 的「意图未兑现」一并抹掉）；他人文件按归口派工（emit_compat→emit-impl、legacy_fast/fastplugin→support-impl、tests 侧→各测作者）。
- **防复发（E 档，属新增门禁不属删减）**：E-01 当日实测 `pyproject.toml`/CI/`*.cfg`/`*.ini` 内 ruff / flake8 / pylint **零匹配**=这把尺不在门禁里，A 档删完必再长回来；建议把 `python -m ruff check src --select F401,F841,F811,E402` 接进五项门禁（AGENTS §2 收口面），需 lead 排期。

---

## H. lead 23:2x 批复的**执行口径**（转述，供 #24 直接照做；判定原文见各表末列）

1. **批准本波执行（16 个动作点）**：A-01、A-02、A-03、A-04、A-06、A-08、A-09、A-10、A-11、A-12、A-13、A-15 前 3 条、B-01、B-02、B-03。**两个前置条件**：①#27/#18 收工；②support-impl 那批删减完成。
2. **A-05 已裁走「补 `__all__` 转正」**，不删——`generator/__init__.py` 加 `__all__ = ["GenOptions", "generate", "FlowEmitError", "ChecklistError"]`（rc 归因是用户可见契约）。
3. **A-07 归口 + 排期**：派 **emit-impl**，且**等 #27 落定后再动**（不在同一文件的在途 hunk 上叠删）。
4. **B-07 / B-08 批准删，但排 #27 收工后同批**：B-07 须同步摘 `test_legacy_fast.py:1201` 名册里的名字；B-08 的裁删依据正是本档 B-08 自证的「设计 v2 已把 stats 钉成 jsonl + summary，§A 的 `settle_stats.json` 是 v1 作废文本」。
5. **A-13 附带撤回**：lead 此前发给 support-impl 的「加 `identity_key` 参数」指令**作废**（去重已由 `experience.py:24` 的 task 段完成），#22 落地注补一句「去重键来自 task 而非独立 identity 字段」。
6. **A-14 转缺陷单**：`markdown:188 low` 是真 bug，已派 **emit-impl 修语义**，不属删减面。**A-15 第 4 条 `never`** 转 xcheck 自判（造了不用是否漏接线）。
7. **B-04 改判为保留**：`waits` 是语义等待公开门面，dialog 属常见族——**文档没写是文档欠账，不是 API 该删**（本档两证属实，结论翻向补文档）。
8. **#22 落地注素材（lead 点名两条）**：C-01（引擎 `waits.py` 与产物模板天然两份，新加等待族两处都要动、节拍常量不得各自漂移=同步义务）+ D-10（`fastplugin` 不复制净化刀是刻意决定，不是缺项）。
9. **C-07 条目作废**：lead 承认「删 lite/remote/docker 中间产物」是旧计划书转述、未核盘（口述漂移），盘上无此文件——本档按当日原文纠正生效。
10. **E-01 记为后续波次**：接门禁属实现改动，不入 #24 删除批。


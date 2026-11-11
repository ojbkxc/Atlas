# Gap12：LLM 全自动自主探索（把「脑」装进 Atlas）

> 状态：W1/W2 已落地（离线判据 E.1–E.9 + CLI 入口，见 §G 落地注）；W3 真机待授权。
> 目标：让 `atlas explore` 走「大模型全自动撒手」路线——**目标进去、用例草稿出来，全程无人**；
> 并把 Atlas 整体按 **Agent**（目标驱动、自主决策、闭环自愈）的形态收敛，而不是靠人写规则/脚本驱动。
> 前置：`docs/gaps/gap9-fast-pytest-runner.md`（探索产物→快包链路）、`docs/gaps/gap6-*`（pi 吸收评估，已含「准入不自建 LLM」结论——本波**推翻该结论**，用户明确要求内置）。

---

## A. 缺口与事实（带行号取证）

| # | 事实 | 位置 | 缺口 |
|---|---|---|---|
| A.1 | 探索会话「**脑在外面**」：Atlas 只开 CDP 端口 + 打印 `playwright-cli attach` 命令，实际点击靠外部接管 | `src/atlas/explore.py:1-5`、`:141-143` | 用户要求「全自动撒手」= 脑必须在里面；现在停一次要人回车一次 |
| A.2 | checkpoint 靠**外部写命令**触发：console 回车或 cmd 文件空行 | `explore.py:147-170`、`:55-82` | 无人在场时 0 checkpoint、trace 为空 |
| A.3 | 已有可复用的「观察」面：`page.aria_snapshot()` + `extract_candidates()`（role+name 候选，限 20） | `explore.py:85-105`、`:179-199` | 观察面已够喂给 LLM，缺的是「谁来决策」 |
| A.4 | 已有可复用的「沉淀」面：trace → `pending_review` 草稿（step + selector_candidate） | `explore.py:216-269` | 沉淀链路完整，**不得绕过 review 塔**（红线 #10） |
| A.5 | `AtlasAgent` 有完整的**风险分级/审批/留痕**，但自述「不做自由探索式点击」 | `src/atlas/agent.py:18-46`、`:70-74`、`:93-153` | 护栏齐备可直接复用；缺的是「LLM 决策」这一层 |
| A.6 | 全仓**无任何 LLM 接入**（grep `openai/anthropic/llm` 仅有无关命中） | `src/atlas/**` | 需新建 LLM 客户端（含缺席 fail-loud、离线可测） |
| A.7 | CLI `explore` 现状：`--url/--account/--trace/--compile`，**无 `--auto`/`--goal`** | `src/atlas/cli.py:178-182` | 缺入口 |

**结论**：A.3/A.4/A.5 是现成底座，本波补三件——**LLM 客户端（A.6）**、**自主决策循环（A.1/A.2）**、**入口（A.7）**。

---

## B. 目标形态（Agent 闭环）

```
goal ──► [观察] aria_snapshot + candidates + url + 历史
           │
           ▼
        [决策] LLM（结构化动作，白名单）
           │
           ▼
        [护栏] 非生产闸 → 风险分级 → 超阈值须 approved（否则停）
           │
           ▼
        [执行] 仅白名单动作（click/fill/press/goto/assert/wait/record/done）
           │
           ▼
        [留痕] 每步 checkpoint + 每条决策落 journal
           │
           └──► 未 done 且未触终止条件 → 回到[观察]
                done/上限 ──► [沉淀] compile_trace → pending_review 草稿
```

**终止条件（任一）**：LLM 返回 `done`；步数达 `max_steps`；墙钟达 `max_seconds`；连续失败达 `max_failures`；护栏拦截；Ctrl-C（留痕后原样传播）。

---

## C. 接口契约表（跨人引用只认本表）

| 契约 | 形态 | 缺席/异常行为 |
|---|---|---|
| `llm.LLMConfig.from_env()` | 读 `ATLAS_LLM_BASE_URL` / `ATLAS_LLM_API_KEY` / `ATLAS_LLM_MODEL`（缺失即抛 `LLMNotConfigured`，**逐键点名**） | 不静默降级、不 fallback 到假数据 |
| `llm.LLMClient.complete(messages, *, temperature, max_tokens, timeout)` | POST `{base_url}/chat/completions`（OpenAI 兼容），返回 `content: str` | 超时/HTTP≥400/畸形响应 → `LLMError`（带状态码与响应前 200 字），**绝不吞成空串** |
| `llm.LLMClient` 实现面 | **仅标准库** `urllib.request`（不新增运行时依赖） | — |
| `autopilot.Action` | `{"action": <枚举>, "role"/"name"/"value"/"url"/"note"}`；枚举=白名单 `click/fill/press/goto/assert/wait/record/done` | 枚举外 → `ActionRejected`（计数不执行） |
| `autopilot.explore_auto(config, goal, *, llm, page=None, max_steps, max_seconds, max_failures, approved=False) -> AutoReport` | 返回 `{steps, actions, rejected, stopped_reason, trace, drafts}` | 生产环境 → `PermissionError`（复用 `explore.start` 同款闸） |
| 决策留痕 | 每步 `Decision`（goal/tasks/risk/approved/reason/executed）写 journal | 与 `AtlasAgent._record` 同源（#92 不落第二把尺） |
| 沉淀 | 结束调 `explore.compile_trace(trace, journal)` | 产物是原矿，**永不直接成资产**（红线 #10） |

**LLM 提示词契约（定稿，实施时逐字用）**：system 段固定「你是在**非生产**环境探索 UI 的自动化 Agent；只许从给定候选里选下一步；只许输出 white-list 动作的 JSON；遇到写/提交类动作输出 `assert` 或 `done` 并要求人工」；user 段给 `goal` + 当前 `url` + `extract_candidates()` 候选 + 最近 N 步历史。**响应必须是单行 JSON**，解析失败按「非法动作」计（不重试篡改提示词）。

---

## D. 文件所有权表

| 文件 | 归属 | 说明 |
|---|---|---|
| `src/atlas/llm.py`（新） | W1 | LLM 客户端；仅标准库；env 门 |
| `tests/test_llm.py`（新） | W1 | 离线：env 缺席点三键、HTTP 错误传播、正常解析（fake transport 注入） |
| `src/atlas/autopilot.py`（新） | W2 | 自主循环；复用 `explore.extract_candidates`/`_record_checkpoint`/`compile_trace` 与 `agent.classify_risk` |
| `tests/test_autopilot.py`（新） | W2 | 离线：fake LLM 驱动全链（见 E） |
| `src/atlas/cli.py` | W2 | 只加 `explore --auto/--goal/--max-steps/--max-seconds`，不碰既有分支 |
| `src/atlas/explore.py` | W2（最小改） | 仅把 `_record_checkpoint` 提为可复用（签名不变）；`start()` 行为零变化 |

---

## E. 验收标准（全部离线可测；真机项单列）

| # | 判据 | 形态 |
|---|---|---|
| E.1 | LLM 缺席 fail-loud | 三 env 全空 → `LLMNotConfigured` 消息含三个键名；只缺一个 → 点该键 |
| E.2 | HTTP 错误不吞 | 注入 500 / 超时 → `LLMError` 含状态码；**断言不会返回空串冒充成功** |
| E.3 | 正常解析 | 注入 OpenAI 形状响应 → `content` 逐字返回 |
| E.4 | 动作白名单 | fake LLM 给 `{"action":"eval","js":"..."}` → `ActionRejected`、不执行、计数 +1（**绝不允许 LLM 直接跑任意 JS**） |
| E.5 | 风险门 | fake LLM 给 `{"action":"click","name":"Save"}` 且 `approved=False` → 停于 `ApprovalRequired`，**决策已留痕**（成功/失败两路都落 journal，同 `agent.run`） |
| E.6 | 全链闭环（正向对照） | fake LLM 给 `click→fill→record→done` → 4 步全执行、`record` 落 checkpoint、`done` 终止、trace 可 `compile_trace` 出 `pending_review` 草稿 |
| E.7 | 终止条件 | 步数上限 / 连续失败上限 各一格，`stopped_reason` 点名 |
| E.8 | 生产闸 | `config.is_production=True` → `PermissionError`（与 `explore.start` 逐字同款消息） |
| E.9 | 留痕不吞异常 | 执行中抛 `KeyboardInterrupt` → journal 有记录、异常原样传播（复用 `agent.run` 的 `CONTROL_FLOW` 纪律） |
| R.1 | 真机（需授权 + **无头**） | 接真 LLM 探索一个只读列表页，产出 ≥1 个 `pending_review` 草稿；失败点/写操作一律停 |

**计数纪律**：新增测项数由实施者申报，lead 一次落四处基线（README 徽章/边界段、AGENTS §2、CLAUDE FAQ）。

---

## F. 分期落地

- **W1**：`llm.py` + `test_llm.py`（E.1–E.3）。零真机、零网络（fake transport）。**已落地**。
- **W2**：`autopilot.py` + `test_autopilot.py` + CLI 入口（E.4–E.9）。**已落地**（见 §G）。
- **W3（待授权）**：R.1 真机（无头），并评估把同一循环接给 `atlas gen`（目标→用例）与 `healing`（失败自愈），完成「项目即 Agent」的收敛。

**红线（本波新增，实施者不得放宽）**：
1. LLM **只输出白名单动作**，任何「执行任意 JS / 拼 SQL / 直接改文件」的提议一律 `ActionRejected`；
2. 写/提交类动作走 `classify_risk`，超 `max_auto_risk` 必须 `approved`，**默认不下发**；
3. 探索产物**只进 `pending_review`**，进知识/组件库必须走 `atlas review`（#10）；
4. LLM 通道**只读**页面，探索会话仍禁生产（`config.is_production` 闸复用）；
5. 真机跑一律**无头**（`--headless` 缺省），且不在用户使用时段连跑。

---

## G. 落地注（W1/W2，2026-11-10）

**交付面**：`src/atlas/llm.py`、`src/atlas/autopilot.py`（新）+ `src/atlas/cli.py`（`explore --auto/--goal/--max-steps/--max-seconds`）+ `src/atlas/explore.py`（一处常量提升）+ 两个新测试文件（`test_llm.py` 11 项、`test_autopilot.py` 21 项））。离线全量 **2216 passed**、0 skipped，`--collect-only` 同值。

**实现期发现并处置的四点**（都是「设计文本没写到、不做就会真坏」的一类）：

- **G.1 零 checkpoint 终态要落空档再编译**：审批拦截 / 连续失败 / 一上来就 `done` 这三种终态都不会写 trace 文件，而 `compile_trace` 是**读文件**编译 → `FileNotFoundError` 顶掉整个探索结论（`report` 已算完却拿不到 `drafts`）。现返回前若 trace 不存在则落空档：**「没有证据」也要是可编译的诚实结论**，不是异常。
- **G.2 `failures` 必须落在 `AutoReport` 上**：局部计数器只用于比阈值，报告面恒 0 = 上层看不见失败数（`max_failures` 停了却报「0 次失败」自相矛盾）。现统一读写 `report.failures`。
- **G.3 生产闸早于开浏览器**：设计只说「复用 `explore.start` 同款闸」，但 `explore_auto` 的闸在函数第一行、CLI 却要先起 `DemoRuntime` 拿 page——等于生产环境照样白烧一次登录+打开浏览器才被拒。CLI 侧显式前移该闸，并**把消息字面量收敛成 `explore.PRODUCTION_REFUSED` 单一来源**（原先两处各抄一份，改一漏一 #92）。
- **G.4 决策留痕走同一通道**：CLI 传 `AtlasAgent(journal=reports/agent_decisions.jsonl)`，与 `agent.run` 同源，不新开第二个留痕文件；`_record_decision` 沿用「写失败不顶真因」纪律。一分一毫不新造：**没有 agent 就不落盘**（设计 E.9 原文），不静默假装记过。

**边界（如实）**：`record`/`assert` 是语义留痕动作，无副作用，其价值由 checkpoint 与决策行承载；`_observe` 对 `aria_snapshot` 异常降级为空快照（候选为空则该步 LLM 拿到空候选表——它应当输出 `done`，而不是被框架猜）。**W3 未做**：真机 R.1（接真 LLM 探索只读页）需授权，且要无头、不在用户使用时段跑。

---

## H. 收缝：ARIA 候选「产得出、入不了库」（gap13，2026-11-10）

gap12 的观察面（`explore.extract_candidates` → `compile_trace`）产出的候选本就是 **ARIA role+name**，
但组件库只收 CSS 字符串、批准入口也只收 `--remedy "<CSS>"`——于是候选要入库必须由人手抄成 CSS，
抄错就是第二份漂移知识。本波把这条缝闭合成**一条腿**：

**交付面**：`src/atlas/locators.py`（新，定位描述**单一解析点**）、`knowledge.py`（`save_component_selector`
收两形态 + `SelectorRegistry.resolve/describe`）、`review.py`（`approve_to_selector` 收描述、`template_rows`
预填 role/name、`apply` 组装）、`cli.py`（`--role/--name` 与 `--remedy` JSON 对象）、`generator/emit_pytest.py`
（页对象两形态渲染）、`autopilot.py`（`_locator` 委托同一判据）、`waits.py`（只加一段「为何仍用 CSS」的说明）。
测试：`tests/test_locators.py`（新）+ 四处既有文件的判据补充。

**H.1 形态真源只有一处（#92）**：`parse` / `normalize` / `canonical` / `resolve` 全在 `locators.py`；
消费方全部委托，不留第二份形态判断。**产物侧的唯一例外**是生成的 pytest 包零 `import atlas`（gap9 硬约束），
所以生成期把**规范化 IR** 固化进页对象、产物内联一层同构分支——分支集合由 `KINDS` 派生，不是第二把尺。

**H.2 实现期发现的三点**（都是「不这么做就会真坏」）：

- **落盘形态必须是能再 `parse` 回来的用户形态**：首版把 IR（带内部 `kind` 标签）直接写进 `components.json`，
  结果是存了一份**自己解析不了**的知识——读回时 `kind` 被判未知键当场点名，笔迹上像坏数据。
  现 CSS 恒落字符串（含手写 `{"css": ...}` 归一）、结构化只落 `{"role":..,"name":..}` 形态。**怎么坏**：
  验收测 `test_normalize_round_trips` 与 `test_kind_tag_is_not_a_valid_input_shape` 钉住往返。
- **留痕不能裸用原值**：`approve_to_selector` 旧写法 `selector[:120]` 收到 dict 直接 `TypeError`——
  批准**已经落库**却崩在留痕，等于写回事实无痕（#110/#85 同族）。现走 `locators.canonical`。
  **怎么坏**：`test_approve_aria_candidate_stores_description_and_resolves` 断言决策行里是 `role=button[name="Save"]`。
- **纯 CSS 组件必须逐字节不变**：`locator()` 方法体只在**该组件含结构化描述时**才多出 ARIA 分支，
  否则与改造前逐字同形——`_FAST_OFF_BASELINE` 的 sha 快照（E.1.2）是这条的硬防线，`test_pages_pure_css_output_is_byte_identical`
  再补一条可见证据。

**H.3 一处披露的行为收紧**：`role` 的 `name` 走 **`exact=True`**。候选的 name 是 aria snapshot 的**无障碍名原文**，
精确匹配才叫「原样回放」；Playwright 默认的子串匹配会让 `name="保存"` 同时命中「保存并提交」——
**点错比找不到更坏**（找不到可见、点错静默）。影响面：`autopilot` 的 click/fill 从「子串」变「精确」。

**H.4 边界（如实）**：ARIA 名不是万能——身份模糊的通用 combobox 仍应留在 CSS（真机塔 2026-11-04 已有此判例：
`textbox:Select` 被驳回，理由是组件库已有 `.el-select input` 且 aria 名身份模糊）；`.el-table`/`.el-loading-mask`
这类框架结构类不适用（`waits.py` 仍用 CSS，原因写在模块 docstring）。**ARIA 优先由组件库的人审内容决定，
引擎与生成器都不猜**——本波没有做「自动把 CSS 升级成 ARIA」的任何改写。

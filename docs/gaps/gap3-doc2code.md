# Gap 3：`atlas gen`（接口文档 → Atlas 测试代码生成助手）

> 状态：**已落地**（src/atlas/generator/，`atlas gen`）。对应手册 2.3 `scs-api-test-generator`「接口测试代码生成助手，从接口文档到可执行测试代码的完整生成」。
> 目标读者：Atlas 维护者；落地前先评审本方案。
> 约束来源：`AGENTS.md`（红线，冲突以它为准）、`CLAUDE.md`、`README.md`、`scripts/import_jmeter.py`（纪律模板）。
> 报告口径（team-lead）：项目测试报告统一 **Allure 2.39.0**；`atlas gen` 生成的 flow 经 `atlas run` 执行后，运行记录必须能汇入 Allure 2.39.0 结果集。

---

## 0. 一句话目标

从**接口文档**（Markdown / Swagger 2 / OpenAPI 3 / 纯文本接口说明）离线生成一套**可被人审、可被 `atlas compile` 校验、可被 `atlas run` 执行**的 Atlas 资产：

```
DSL flow YAML  +  契约候选（自动分级 core/high/minimal）  +  探针候选（requires 块 + skeleton）  +  最小 Task 注册结论
```

生成的产物全部进 `atlas review` 审核塔（`pending_review`），**只生成 YAML 供人审，绝不自动执行**——这是 `scripts/import_jmeter.py` 已经验证过的纪律，`atlas gen` 完整照搬。

---

## 1. 调研结论（心智模型，构建方案的事实基础）

### 1.1 DSL flow 的完整结构（`src/atlas/dsl.py`）

`FlowCompiler.load()` 只要求顶层是映射且 `steps` 是列表；`validate_flow()` 是**结构校验的单一来源**，`atlas compile` 与 `atlas run` 登录前共用：

| 键 | 位置 | 语义 | 生成含义 |
|---|---|---|---|
| `id` | 顶层 | 用例 id，落 `RunRecorder(case=…)`、Allure 的 `name/fullName/historyId` | **必须稳定且唯一**，生成期由文档标识确定，不用随机值 |
| `plugin` | 顶层 | 业务插件名（示例 `demo`） | 生成候选默认 `demo`，可 `--plugin` 覆盖 |
| `requires` | 顶层 | 数据供给块 → `DataProvider.supply` | 探针生成落点（见 §5） |
| `on_env_blocked` | 顶层/步骤 | `skip`/`stop`，只接受这两个值 | 读场景默认 `skip`，写场景默认 `stop` |
| `steps[]` | 顶层 | 每项映射，必须有 `task` | 接口 → step（见 §4） |
| `assert[]` | 顶层 | 字符串表达式 / `{task: …}` 内联 Task / `{key: value}` 键值断言 | 响应字段 → assert（见 §4.3） |

步骤级可透传字段：`task`、`channel`、`intent`、`requires`、`read_only`、`on_env_blocked`、`id`，其余键（`path/method/body/headers/save_as/domain/...`）原样作为 Task 构造参数。**注意：`id` 被 `FlowCompiler.compile` 从参数集里排除**——它只是给人看的步骤标识，不会传给 Task；因此 Allure 的步骤名要靠 `intent` + `save_as` 体现，不能指望 step `id`。

### 1.2 Task 注册表如何工作（`TaskRegistry` / `DemoRuntime.registry()` / `cli._full_registry()`）

- `TaskRegistry.register(name, factory)`；`compile()` 对每步 `registry.create(step["task"], **args)`，未注册即 `KeyError("Task 未注册：…")`。
- `runtime.registry()` 与 `cli._full_registry()` **同源**：`DemoPlugin.register_tasks` + 5 个通用 Task——
  `api_call`(`CallEndpoint`)、`ui_action`(`UiAction`)、`demo_api`(`DemoApiOperation`)、`assert_api`(`AssertApi`)、`legacy_step`(`LegacyStepTask`)。
- **关键结论**：`api_call` 是通用 HTTP 步骤，`CallEndpoint(path, method, body, save_as, headers, domain, intent, read_only)` 可以承载任意文档端点。**新增一个文档接口通常不需要注册任何新 Task**——这正是「最小 Task 注册」的实现基础（见 §6）。
- `TaskMetadata` 在构造期钉死 `requires/produces` 必须是 `tuple[str,...]`，且 `write_produces` 强制「声明键 = perform 返回 dict 的键」。生成器只要沿用 `save_as → {save_as: response}` 语义即自兑现。

### 1.3 `api_call` 的真实发送语义（`tasks.py` / `abilities.py`）

- 签名：`CallEndpoint(path, *, method="POST", body=None, save_as="response", domain="api", intent=…, requires=(), read_only=True, headers=None)`。
- **没有 query 旋钮** → 文档里的 query/路径参数必须**拼进 `path`**（`path` 以 `http(s)://` 开头时按绝对 URL 发，否则拼 `base_url`）。
- **body 分流（红线级）**：`dict/list`→`json=`；`str/bytes`→`data=` 原样；其余类型发送前 `TypeError`。所以生成器产出 YAML 时必须区分：
  - JSON 请求体 → 生成 **YAML 映射/列表**（加载后即 dict/list → `json=`）；
  - form-urlencoded / 纯文本体 → 生成 **字符串**（→ `data=`）；
  - 不可混淆：把 form 串写成 dict 会被 requests 当 JSON 发，后端回假 400。
- `headers=None` 用会话默认头；传入则按 requests 语义**合并（本次覆盖同名会话头）**。ICS 后端缺 `isc-app` 业务识别头 **回 BUG-920**（`CLAUDE.md` FAQ「数百条接口/ JMeter 迁移」）——生成器必须把所有"全局鉴权/业务识别头"并入**每一个** `api_call` step（照搬 `import_jmeter.py:extract_samplers` 收集 `HeaderManager` 的做法）。

### 1.4 契约三档的真实实现（`contracts.py`）

| 档 | `Contract.validate()` 实际做什么 | 生成策略 |
|---|---|---|
| `minimal` | `ok` 为真 且 `data is not None` | **默认档**（文档新增接口一律 minimal） |
| `high` | 额外要求 `data` 是对象**且**含 `resultCode`（通用规则） | 文档明确响应体是对象且带 `resultCode` 时升级 |
| `core` | 额外 `resultCode == 期望值` + `required` 逐字段类型校验（`object/array/string/number/boolean/any`，bool≠number） | **只在文档显式标注"业务关键/强契约"或 `x-atlas-level: core` 时生成**；永不自动批量升级 |

- `_put` 对同 `(method,path)` **不同定义**显式冲突（#128）——生成候选**不能直接并入运行时注册表**；等值重复才幂等放行。
- `required` 路径支持点路径与**数字下标**（`items.0.sku`）——契约侧能表达的、DSL assert 侧表达不了的，正好互补（见 §4.3 的限制）。

### 1.5 `compose_for_goal` 的拼装语义（`composition.py`）

- 按 `produces` 反向递归选 Task，再拓扑排序；同一产出键多生产者**显式报错**。
- 对生成器的含义：多接口链路场景（§3 场景 B）里，**每个 `api_call` 的 `save_as` 必须全局唯一**（`resp_1/resp_2…` 或按操作名派生），否则下游 `requires` 会被误判成「多生产者同键」，compile/agent 直接失败。生成期就要做冲突检测。

### 1.6 探针现状（`probes.py`）

- `DataRequirement.from_dict` 只接受键：`entity / where / strategy / as / on_env_blocked`；`strategy ∈ {find, find_only, create, find_or_create}`。
- `DataProbe` 抽象要求 `find()`（零副作用）与 `create()`；`create` 未实现即 `NotImplementedError`，**禁止 UI 自动兜底造数**。
- `ScriptedCreator` 只能接**已被人工验证过**的脚本；`supply()` 强制备注含「非人工创建」+ 有效单号。
- **结论**：生成器只能产出 `requires` 声明 + **只实现 `find()` 的 skeleton**，`create()` 一律 `NotImplementedError`（红线 12「不造形式代码」、红线 3「不自动创建无备注单据」）。

### 1.7 报告产出与 Allure 汇入点（`recorder.py` / `reporting.py` / Gap 5）

- 执行链产出 `reports/runs.jsonl`：一行一 run（`run_id/case/env/mode/status/started_at/finished_at/steps[]`），步骤含 `step/op/ok/dur_ms/api_ms/fail_class/healed/degraded_to_ui`。
- `reporting.summarize/export_runlog` 已具备 #112/#131 的容错读侧纪律（`errors="replace"` + 逐行跳过）。
- **Gap 5（`docs/gaps/gap5-allure.md`）已定义**：`atlas allure reports/runs.jsonl --out reports/allure-results` 纯 stdlib 直出 Allure 2.39.0 结果集（`*-result.json`），并按 `case → name/historyId`、`status/fail_class → status/label` 映射。
- **结论**：`atlas gen` 不需要自己实现 Allure 写出；它只需保证**生成的 flow 带稳定 `id`、步骤带语义化 `intent`**，让 Gap 5 的 emit 天然产出可读的 Allure 树（§8）。

### 1.8 `import_jmeter.py` 的纪律与局限（最重要的参照物）

**必须照搬的纪律（逐条）**
1. **只生成 YAML 供人审，绝不自动执行**——`main()` 只写文件/打印，不做任何网络请求。
2. **HeaderManager 全局鉴权头并入每个 sampler**——`extract_samplers` 先扫全树收头，再逐 sampler 附上；注释明说「缺 `isc-app` 回 BUG-920」。
3. **报文完整迁移**：`postBodyRaw` 是 bool 不是 body；不 `[:500]` 截断；多参数按 `elementProp` 分组结构化解析，跳过外层 `Arguments` 容器避免重复拼接；扁平 `Argument.value` 兜底。
4. **body 分流**：能 `json.loads` 的生成对象，否则保留字符串（→ `data=`）。
5. **占位符必须点名**：`unresolved_placeholders` 递归下钻 `path/body/headers`，逐条告警「保留原样待人审接 env」——否则当字面量发出得一堆假 400，误判成 Atlas 缺陷。
6. **输入边界友好报错**：文件不存在 → 一行 stderr + rc 1；无 `-o` 打印到 stdout。

**它的局限（= `atlas gen` 的差异化价值）**
- 只能吃**已录制/已存在的流量**（.jmx），对**还没有流量、只有文档**的新接口无能为力。
- 不懂**类型与契约**：只能从报文实例反推，不能产出 `required` 字段类型、`core/high/minimal` 分级。
- 不产出**探针/数据供给**（`requires`）、不产出**断言优先级建议**、不产出**场景分类**。
- 不产出**Allure 元数据建议**（用例名/严重度/链路分组）。

> 分工一句话：**JMeter 导入 = 存量巡检流量迁移（保真优先）；`atlas gen` = 新接口从文档起测（结构与契约优先）。** 两者可以叠加：先 `gen` 生成骨架，再用真实抓包（`toolbox` / `import_jmeter`）校正报文与头。

### 1.9 手册「四种适用场景」——原文不可得，本方案给出**显式假设**

技术核查结果：`D:\Users\ex_mazy16\Videos\接口自动化测试Skill使用手册_files\404730792673280.html` 正文是富文本协作文档，章节标题（"2.3 scs-api-test-generator / 功能简介 / 触发关键词 / **四种适用场景** / 核心执行流程（场景 A 为例）/ 使用示例 / 注意事项"）可抽取，但**正文段落与截图未被导出为可读文本**（抽取到的只有目录、表情与编辑器 UI 噪声；配图是无关图片）。因此本节明确标注为**合理推断**，落地前须回查手册原文校正：

| 场景 | 假设定义 | 判断依据（接口测试常见场景 + 手册措辞） | 生成产物 |
|---|---|---|---|
| **A：单接口新增用例** | 给一个接口文档片段（1 个端点），生成一条可执行的读接口用例 | 手册「核心执行流程（**场景 A** 为例）」暗示 A 是最小/最常见入口 | 单 flow（`api_call` ×1 + assert + minimal 契约候选） |
| **B：多接口业务链路** | 按业务流程串联多个接口（查询→断言→详情→断言），验证数据一致性 | 接口自动化最常见的"链路/场景"诉求 | 单 flow 多 step，`save_as` 唯一、插值串联、`requires` 供数 |
| **C：存量接口批量补全/回归补充** | 拿 Swagger/OpenAPI 或接口清单**批量**生成骨架，补齐回归覆盖 | 手册强调"从接口文档到可执行测试代码的**完整**生成"，批量是核心价值 | 一接口一 flow（或一文件多 flow，见 §7.3），批量契约候选 |
| **D：写操作/造数前置场景** | 文档含保存/提交/删除类写接口，生成**骨架但强制人工过** | 手册"安全规范"在 2.4 执行助手里反复出现；写接口是高风险场景 | 单 flow，`read_only: false`、`on_env_blocked: stop`、**不生成 creator**、显式"禁止自动执行"横幅 |

> ⚠️ 若手册原文的四种场景与上表不同（例如是"单接口/流程/边界异常/存量迁移"），只需替换 §3 的场景表与 §7 的 `--scenario` 枚举，**生成引擎与红线约束不变**。

---

## 2. 总体设计

### 2.1 模块划分（新增代码，遵循「引擎不含业务」红线 11）

```
src/atlas/generator/
├── __init__.py          # generate(source, out_dir, options) -> GenResult（纯函数，无网络/无副作用除写文件）
├── sources.py           # 来源嗅探 + 统一内部模型（不依赖具体文档格式）
├── openapi.py           # OpenAPI3 / Swagger2 解析（$ref 解引用、requestBody、responses schema）
├── markdown.py          # Markdown 接口说明解析（标题/代码栅栏/参数表）
├── plaintext.py         # 纯文本接口说明解析（METHOD /path + 参数/样例正则）
├── model.py             # Endpoint / Param / BodySpec / ResponseSpec / Scenario 数据类
├── emit_flow.py         # Endpoint(s) -> flow dict（DSL）
├── emit_contract.py     # ResponseSpec -> Contract 候选（自动分级）
├── emit_probe.py        # requires 块 + DataProbe skeleton（只 find）
├── emit_pytest.py       # 可选：pytest + allure 适配（--with-pytest）
├── checklist.py         # 编译自检（复用 FlowCompiler.validate_flow）、占位符扫描、冲突检测
└── tower.py             # 把生成摘要登记为 pending_review 草稿（复用 ExperienceJournal）
```

- **不进 `src/atlas/engine.py`**：生成器是工具，不是执行引擎；不向执行链注入任何业务字面量。
- 与 `scripts/import_jmeter.py` 共享的纯函数（占位符递归扫描、body 分流判定、头合并）建议抽到
  `src/atlas/generator/dsl_utils.py`，并让 `import_jmeter.py` 改为 import 它（**保持行为零变化**，回归测钉死）。
  若不想动现有脚本，也可先在新包内复制，标注「单一真相待收敛」，再在 P3 收敛（与 Gap 5 §4.2 同手法）。

### 2.2 数据流

```
 文档(source) ──嗅探──▶ Parser ──▶ Endpoint[] ──▶ ┌─ emit_flow      → flows/<id>.yaml
                                                    ├─ emit_contract  → contracts/<id>.candidate.json
                                                    ├─ emit_probe     → probes/<id>.candidate.py
                                                    ├─ emit_pytest    → tests_generated/test_<id>.py (可选)
                                                    └─ checklist      → gen_report.json + compile 结果
                                                                          │
                                                        全部 pending_review ──▶ atlas review 塔
```

**生成阶段零网络、零浏览器、零登录、零执行。** 违者即违反「只生成 YAML 供人审，绝不自动执行」。

---

## 3. 文档解析策略（多来源）

### 3.0 来源嗅探（`sources.py`）

按**内容**判定，不只看扩展名（接口文档常被存成 `.txt`/`.md`/`.json`）：

```
读入文本（UTF-8 严格 → 失败回落 errors="replace"，与 #112/#131 读侧纪律一致）
├─ JSON 可解析 / YAML 顶层含 "openapi"  → openapi.py（OpenAPI 3.x）
├─ YAML/JSON 顶层含 "swagger": "2.0"    → openapi.py（Swagger 2，转换 paths→同模型）
├─ 含 "# " 标题 + ``` 代码栅栏 或参数表格  → markdown.py
├─ 含 (GET|POST|PUT|PATCH|DELETE)\s+/\S+ → plaintext.py
└─ 其它 → 明确 ValueError 点名「无法识别文档格式，请用 --source 指定」
```

`--source {auto,openapi,swagger,markdown,text}` 可强制指定；解析失败给**一行 stderr + rc 2**（复用 `cli._user_error` 口径）。

### 3.1 OpenAPI 3 / Swagger 2（`openapi.py`）

| 文档元素 | 内部模型 | 落 DSL 的规则 |
|---|---|---|
| `servers[0].url` / `basePath` | `Endpoint.base` | 只作为**提示**写进报告，不写进 flow（flow 用会话 `base_url`）；`--base-url-hint` 可覆盖 |
| `paths./x/{id}` + `get/post/...` | `Endpoint(method, path)` | `path` 保留 `{id}` 占位 → 转成 `${...}` 变量或留待人工（§3.4） |
| `parameters`（path/query/header） | `Param(in, name, required, schema, example)` | path/query 拼进 `path`；**header 参数并入步骤 `headers`**；required 参数缺失 → 报告"待填" |
| `requestBody.content["application/json"].schema` | `BodySpec(kind="json", example)` | 由 `example/examples/schema` 递归造**示例体**（见 §3.4 造值纪律）→ YAML 映射 |
| `application/x-www-form-urlencoded` | `BodySpec(kind="form", pairs)` | 生成 `a=1&b=2` 字符串（→ `data=`） |
| `text/plain` / `multipart` | `BodySpec(kind="text"/"unsupported")` | text → 字符串；multipart → **不生成 body**，报告点名手工处理（不伪造文件上传） |
| `responses.200.content.*.schema` | `ResponseSpec(required, types, sample)` | 契约候选（§4.4）；`required` 数组 → `Contract.required` 类型映射 |
| `securitySchemes` / 全局 `security` | `HeaderSpec[]` | 生成 `${ISC_APP}` 一类占位头，**不写真实值** |
| 扩展 `x-atlas-level: core/high` | 分级覆盖 | 仅此可把契约升到 core |
| `x-atlas-scenario: a|b|c|d`、`x-atlas-flow: <id>` | 场景/分组 | 决定输出形态（§3 场景） |
| `x-atlas-save-as` | 产出键 | 链路串联时可人为指定下游引用键 |

`$ref` 解引用：本地 `#/components/schemas/X` 与 `#/definitions/X` 必须支持；**循环引用检测**，命中即截断到 `{}` 并在报告里点名（不得无限递归/爆栈）。

### 3.2 Markdown（`markdown.py`）

支持两种常见写法，先按"标题切分接口块"再块内解析：

1. **标题即接口**：`## POST /api/order/save` 或 `### 保存订单` + 块内 `- **Method**: POST` / `- **URL**: /api/...`
2. **代码栅栏优先**：
   - ` ```http ` / ` ```bash ` 中的 `curl -X POST 'URL' -H 'k: v' -d '{...}'` → 解析 method/url/headers/body（**这是最可靠的结构**）
   - ` ```json ` 紧跟"请求参数/响应示例"字样 → 上下文判定请求体/响应体
3. **参数表**：`| 参数 | 类型 | 必填 | 说明 |` → `Param[]`；"必填=是"缺值 → 报告"待填"
4. **响应示例**：块内是 JSON 对象 → 推断 `required`（顶层键 + 一层类型）与 `resultCode` 是否存在

不做 OCR、不解析图片里的接口（诚实边界：图片文档要求人工转文本或提供 OpenAPI）。

### 3.3 纯文本接口说明（`plaintext.py`）

面向"群里发的一段接口说明 / Excel 导出成 txt"：

- 端点行正则：`(?i)\b(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)`
- 参数行正则：`^\s*[-*]?\s*([A-Za-z_]\w*)\s*[:：]\s*(\S+)?\s*(必填|required)?`
- 样例 JSON 块：找第一个成对 `{...}` / `[...]`，`json.loads` 成功即视为样例（请求或响应由相邻关键词"请求/响应/返回"决定）
- **置信度标注**：纯文本解析出的每个字段带 `confidence: low|medium`，报告里单列「低置信度项，必须人工确认」，绝不把猜测冒充确定。

### 3.4 取值与占位符纪律（贯穿所有来源）

- **绝不把文档示例值当真实业务数据静默使用**：示例值（如 `"string"`、`0`、`"2024-01-01"`）生成后统一在报告里列为"示例值，需替换为真实数据"，`requires` 优先用探针供数（红线 6：禁止步骤内数据兜底）。
- 路径/查询参数占位：`{id}` → `${path_id}`（DSL 插值），并在报告里列 `path_id` 必须由 `requires`/上一步 `save_as` 提供；**不猜值**。
- 敏感/鉴权值一律 `${ENV_VAR}` 占位（照搬 `import_jmeter.unresolved_placeholders` 的告警机制），生成后跑一次占位符扫描并逐条打印。
- 生成的 `save_as` 全局唯一（`resp_<n>_<op_slug>`），并在 `checklist` 里校验与已有产出键不冲突（§1.5）。

---

## 4. DSL 映射规则（接口 → step，字段 → assert）

### 4.1 端点 → `api_call` step

最小映射（读场景，场景 A）：

```yaml
- task: api_call
  path: /web/demo-sales/saleOrder/getList4Demo
  method: POST
  save_as: resp_1_sale_order_list
  domain: demo
  intent: 查询销售订单列表（文档：2.3 订单列表）
  headers:
    isc-app: ${ISC_APP_HEADER}        # 全局鉴权/业务识别头并入每个 step（BUG-920 防线）
  body:                                # dict/list → json=（YAML 映射）
    divisionCode: ${division_code}
    __page: 1
    __pagesize: 10
```

规则表：

| 文档元素 | DSL 落点 | 备注 |
|---|---|---|
| method | `method`（大写） | 缺省 `POST`（`CallEndpoint` 默认值一致） |
| path + query | `path`（query 用 `?a=1&b=2` 拼在 path 上） | **`api_call` 无 query 旋钮**（§1.3） |
| JSON body | `body` 为 YAML 映射/列表 | 加载即 dict/list → `json=` |
| form body | `body` 为字符串 `a=1&b=2` | → `data=` 原样 |
| header 参数 / 全局鉴权头 | `headers`（合并） | 每步都带 |
| 操作名/摘要 | `intent` | Allure 可读性靠它 |
| 响应保存 | `save_as`（全局唯一） | 下游插值/断言引用 |
| 写操作（save/submit/delete…） | `read_only: false` + `on_env_blocked: stop` | 场景 D，见 §3 |
| 文档标注 `x-atlas-channel: demo_api` | `task: demo_api` + `operation` | 仅当该操作已在 `DemoPlugin.allowed_api_operations` 白名单；否则报告"需人工加白名单"（§6） |

### 4.2 多个端点 → 链路（场景 B）

- 每个端点一个 step，`save_as` 唯一。
- **串联**：下游 step 的 path/body 中引用上游 `save_as` 的字段，用 DSL 插值 `${resp_1_sale_order_list.data.0.orderNo}`——**注意**：`dsl._lookup` 对 list 不支持数字下标（`getattr(list,'0')` 失败），所以链路引用必须走**被调用方文档能给出路径的 dict 字段**，或由人工把上游响应改成"取首条"的写法（见 §4.3 限制与缓解）。
- `checklist` 校验：所有 `${...}` 路径在被引用前确有生产 step（或 `requires`），否则报告"悬空引用"（不自动修）。

### 4.3 响应字段 → assert 块（能力与**硬限制**必须讲清）

DSL assert 支持三种形态（§1.1）。生成器按下列优先级生成：

1. **表达式断言**（首选，可读性最好）：
   - `resp_1_sale_order_list.ok == true`（存活）
   - `resp_1_sale_order_list.data.resultCode == "BUG-000"`（业务码，文档给了具体期望值才生成；否则只生成"存在性"由契约兜底）
   - `resp_1_xxx.data.total >= 0`（数字边界，仅在文档写明语义时）
2. **键值断言**（`{key: expected}`）：用于常量回显校验，如 `resp_1_save.data.orderNo: ${resp_2_list.data.0.orderNo}`（**同样受下标限制**）。
3. **`assert_api` 内联 Task**：仅当该查询已由业务插件包装为 DemoApi 白名单操作；生成器**不凭空创建** operation。

> **硬限制（必须写进报告，不掩盖）**：`dsl._lookup` 对 `list` 只走 `getattr`，**不支持 `.0.` 数字下标**；`assert` 的表达式与 `${}` 插值都受影响。缓解：
> - 列表断言交给**契约**（`Contract.required` 支持 `items.0.sku`，`contracts.py` 的 `validate` 有列表下标分支）；
> - 或生成 `assert_api`（`tasks._dig` 支持数字下标）——但受白名单约束；
> - 其余列表项断言在报告里列成 `manual_assertions[]`，**不伪造"已生成断言"**（红线 12：不造形式代码）。
>
> 该限制本方案**不在生成器里绕过**（不改 `dsl.py`）；如后续要支持，属独立的引擎增强提案，不在 Gap 3 范围。

### 4.4 响应 → 契约候选（自动分级）

`emit_contract.py` 产出与 `Contract` dataclass 同构的条目（`name/method/path/level/required/result_code`），放进**候选文件**而不是运行时注册表：

```json
{
  "source": "openapi:petstore.yaml#POST /pet",
  "generated_at": "（由调用方传入的时间戳，生成器内部不取 Date.now）",
  "policy": "候选：默认 minimal；high 需响应为对象且含 resultCode；core 仅文档显式标注",
  "contracts": [
    {"name": "保存销售订单", "method": "POST", "path": "/web/demo-sales/saleOrder/saveSalesOrder",
     "level": "minimal"}
  ]
}
```

**分级决策（保守、可解释）**

| 条件 | 档 |
|---|---|
| 默认（任何文档端点） | `minimal` |
| 响应 schema 明确为 `type: object` 且已知含 `resultCode`（文档示例/ schema 属性） | `high` |
| 文档显式 `x-atlas-level: core` **或** Markdown/纯文本显式"业务关键/强校验"标注 | `core`（附 `result_code` 与 `required`） |
| 响应是真数组（`type: array`）/ 形态不明 | **锁定 `minimal`**（`high` 要求 data 是对象，会对数组型列表接口误判——`toolbox.learn_contracts` 同教训） |
| 同一 `(method,path)` 已存在且**定义不同**的候选 | 报冲突并**不覆盖**（#128），报告点名 |

`required` 生成规则（仅 core）：取 schema `required` 数组，逐项映射 `object/array/string/number/boolean`；未知/`oneOf/anyOf` 落 `any`（`any` 恒通过，等于不校验——报告里单列"弱项"，不粉饰）。**绝不批量把所有字段塞进 core**（红线 12：389 条强契约不得声称已完成）。

---

## 5. 探针生成策略（`emit_probe.py`）

### 5.1 生成什么

1. **`requires` 块**（写进 flow 顶层）——把"文档里出现过的业务实体"转成数据条件：

```yaml
requires:
  sales_order:
    entity: SalesOrder
    where:
      status: [1003, 1006]      # 从文档枚举/示例推断，低置信度项标 TODO
    strategy: find_only         # 读场景默认 find_only（禁止造数，红线 6/FAQ）
    as: sales_order
    on_env_blocked: skip
```

2. **DataProbe skeleton**（候选 `.py`，供人审后放进业务插件）：

```python
# 生成候选：只实现 find()（零副作用查询）；create() 一律 NotImplementedError。
# 不要在此伪造建单——建单能力必须来自真实捕获的已验证脚本（红线 3/12/16）。
from atlas.probes import DataProbe

class SalesOrderProbe(DataProbe):
    entity = "SalesOrder"

    def __init__(self, demo_api, division_code: str):
        self.api, self.division = demo_api, division_code

    def find(self, where):
        # TODO(人审)：确认该查询已在 DemoPlugin.allowed_api_operations 白名单
        # resp = self.api.sale_order_list(division_code=self.division, __page=1, __pagesize=200)
        # 沿文档给出的字段路径筛选 where；查不到返回 None
        raise NotImplementedError("生成候选：请接已审核的只读查询实现")

    def create(self, where):
        raise NotImplementedError("生成候选不提供建单；请接已验证 creator（ScriptedCreator）")
```

### 5.2 策略选择规则

| 场景 | 默认 strategy | 理由 |
|---|---|---|
| A 单接口读 | `find_only` | 零副作用；缺数据即 `EnvBlocked`，不自动造数 |
| B 链路 | 上游实体 `find_only`；若链路明确"新建→查询→改" | 上游 `find_or_create` **仅当**业务插件已有经审 creator；否则保持 `find_only` 并在报告说明 |
| C 批量 | `find_only` | 批量场景绝不批量造数 |
| D 写操作 | 需要的前置主数据 `find_only`；**本流程自身写操作不转探针** | 写是被测行为，不是供数 |

**永不生成**：`creator`、`create` 实现、配额绕过、生产写路径（红线 1/3/12/14）。

### 5.3 与 `DataProvider` 的接线说明

生成报告最后给一段**人审操作指引**（不改源码）：

```
1) 把 probes/<id>.candidate.py 的实现补完，放进业务插件 probes.py；
2) make_xxx_provider 里注册 CallableProbe(entity, finder)；
3) 确认 entity 名与 requires.entity 一致；
4) atlas compile flows/<id>.yaml  →  atlas run flows/<id>.yaml
```

---

## 6. 最小 Task 注册（结论：默认零新增）

| 情形 | 结论 | 生成产物 |
|---|---|---|
| 文档端点是普通 HTTP 接口 | **零新增**：`api_call` 已覆盖 | flow YAML |
| 端点已在 `DemoPlugin.allowed_api_operations` 白名单 | 可选 `demo_api` step（更强语义/断言能力） | flow YAML（`task: demo_api`） |
| 端点需走 `demo_api` 但**未加白名单** | **不自动改插件**（红线 11：业务知识不进引擎/不自动白名单） | 报告输出「需人工在 `plugins/{domain}/plugin.py` 的 `allowed_api_operations` 增加 `<op>`」+ 候选 diff 片段 |
| 文档描述的是复合业务动作（无单一 HTTP 端点） | 不生成 | 报告点名"无法映射为单一接口，建议人工建复合 Task"（不凭空造 Task） |

生成器**不会**修改 `runtime.registry()` / `DemoPlugin.register_tasks` / `allowed_api_operations`——只输出候选建议。这既满足「最小 Task 注册」，又守住「禁止为了全量造形式代码」（红线 12）。

---

## 7. CLI 设计

### 7.1 命令形态

```
atlas gen <source> [options]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `<source>` (位置) | — | 文档路径（.md/.json/.yaml/.txt/…） |
| `--source-type` | `auto` | `auto/openapi/swagger/markdown/text` |
| `-o/--out` | `exports/gen/<flow_id>/` | 产物目录（含 `flows/ contracts/ probes/ report.json README.md`） |
| `--flow-id` | 由文档标题/路径派生（slug） | 可用 `{endpoint}` 模板为批量场景生成多个 id |
| `--scenario` | `auto` | `a/b/c/d/auto`；auto 按端点数量、是否写、是否标注 `x-atlas-scenario` 判定 |
| `--level` | `auto` | `auto/minimal/high`；`core` **必须**文档显式标注（不接受命令行强升 core） |
| `--headers-file` | 无 | JSON/YAML 全局头（鉴权/业务识别头），**值写成 `${ENV}` 占位** |
| `--plugin` | `demo` | 写进 flow 的 `plugin` 字段 |
| `--division` | 无 | **未实现**：常见的 `divisionCode` 参数（仅作为示例值写入并标注待替换） |
| `--naming` | `resp_{n}_{slug}` | `save_as` 命名模板（冲突检查仍强制） |
| `--with-pytest` | 关 | **未实现**（P4 可选）：额外生成 `tests_generated/test_<id>.py`（用 allure-pytest；需额外依赖） |
| `--to-tower` | 关 | 把生成摘要登记为 `pending_review` 草稿（kind=`trace_step`，§9） |
| `--check-only` | 关 | 只解析+编译自检，不写任何文件（CI 门禁用） |
| `--strict` | 关 | 低置信度项/占位符未接 env/待填必填参数一律 **rc 2**，否则只警告 |
| `--force` | 关 | 覆盖已存在的同 id 产物（默认拒绝并点名，防覆盖人审过的文件） |

**永远不存在 `--execute` / `--run` 参数**——生成即止。

### 7.2 退出码

| rc | 含义 |
|---|---|
| 0 | 生成成功；`report.json` 里 `compile.ok=true` |
| 2 | 用户输入问题（文档不存在/格式无法识别/`--strict` 下有未决项）；复用 `cli._user_error` |
| 3 | 结构自检失败（生成的 YAML `validate_flow` 不通过——生成器缺陷，需修生成器，不交付半成品） |

### 7.3 输出目录与批量（场景 C）

```
exports/gen/<flow_id>/
├── flows/<flow_id>.yaml
├── flows/index.yaml             # 批量：列出所有 flow id + 端点摘要（不是可执行 flow，不叫 steps）
├── contracts/<flow_id>.candidate.json
├── probes/<flow_id>.candidate.py
├── tests_generated/test_<flow_id>.py   # 仅 --with-pytest
├── report.json                 # 解析置信度/编译结果/占位符/待填/弱项/冲突/Allure 建议
└── README.md                   # 人审与执行指引，含「未执行、待人工过鉴权值与写操作」横幅
```

批量时**一接口一 flow**（便于单独 `atlas run`/Allure 单独用例）；`index.yaml` 只做索引。单链路（场景 B）才多 step 合一个 flow。

### 7.4 `report.json` 骨架（人审的 30 秒材料）

```json
{
  "source": "…/order-api.md",
  "source_type": "markdown",
  "flow_id": "order-api-save",
  "scenario": "d",
  "endpoints": [{"method":"POST","path":"/api/order/save","summary":"保存订单"}],
  "compile": {"ok": true, "tasks": ["api_call"], "asserts": 2},
  "contracts": [{"path":"/api/order/save","level":"minimal"}],
  "probes": [{"entity":"SalesOrder","strategy":"find_only"}],
  "unresolved_placeholders": [{"step":1,"where":"header:isc-app","value":"${ISC_APP_HEADER}"}],
  "low_confidence": [{"where":"body.qty","reason":"纯文本推断类型 unknown"}],
  "manual_assertions": [{"where":"resp_1.data.0.orderNo","reason":"DSL 不支持列表下标断言"}],
  "conflicts": [],
  "allure": {"name":"保存订单","history_id":"sha1(env|flow_id)","labels":["epic:demo","feature:order-api"]},
  "next": ["人审 report.json", "atlas compile …", "atlas run …（鉴权值/写操作人工确认）", "atlas review"]
}
```

> `history_id` 建议值由生成器给出（`sha1(env|flow_id)`，与 Gap 5 §3.3 一致）；生成器内部**不取当前时间**（可复现，见 §10）。

---

## 8. Allure 2.39.0 汇入方式

### 8.1 主路径（默认，零新依赖）：DSL → runs.jsonl → Gap 5 emit

```
atlas run flows/<id>.yaml                     # 引擎写 reports/runs.jsonl（含每步 ok/dur_ms/fail_class）
atlas allure reports/runs.jsonl \
            --out reports/allure-results      # Gap 5 的 emit_allure（纯 stdlib，直出 Allure 2.39.0 result.json）
allure serve reports/allure-results
```

`atlas gen` 的职责只是**给 emit 提供可用的元数据**：

| 生成约定 | Allure 中的效果 |
|---|---|
| `id: <flow_id>` 稳定 | `name`/`fullName`/`historyId` 稳定，趋势图可跨运行聚合 |
| 每步 `intent: <中文业务描述>` | 步骤名可读（而非 `CallEndpoint`） |
| `channel: api` / `demo_api` | 步骤分组（label `atlas.channel=api`，由 emit 侧派生） |
| 场景 C 一接口一 flow | 一个接口 = 一个 Allure test case，失败定位到接口 |
| 场景 B 一链路一 flow | 链路整体一个 test case，steps 即接口顺序 |

### 8.2 为什么不在生成器里直接写 Allure

- Allure 结果集的 `status` 必须来自**真实执行**（`ok/fail_class`）；生成阶段只有"结构"，没有结果——生成器写 Allure 就是伪造报告（红线 12/15 同族）。
- 写出逻辑已由 Gap 5 收敛（`allure_report.py`），Gap 3 重复实现会造成两份映射漂移（#92 教训）。

### 8.3 `--with-pytest` 可选适配（显式开启）

生成一个薄壳，让 pytest 驱动同一份 DSL，并让 allure-pytest 采集：

```python
# tests_generated/test_order_api_save.py（生成模板，人审后可改）
import allure, pytest
from atlas.config import AtlasConfig
from atlas.dsl import FlowCompiler
from atlas.runtime import DemoRuntime

FLOW = "flows/order-api-save.yaml"

@allure.epic("demo"); @allure.feature("order-api")
@allure.story("保存订单"); @allure.severity(allure.severity_level.BLOCKER)
def test_order_api_save():
    config = AtlasConfig.from_root(env="sit", mode="run")
    with DemoRuntime(config) as runtime:
        flow = FlowCompiler(runtime.registry()).load(FLOW)
        with allure.step("执行 DSL flow"):
            FlowCompiler(runtime.registry(), runtime.provider).run(
                {"engine": config, **flow}, runtime.actor)
```

- 优点：与 Gap 5 的 `--via-pytest` 同一路线，生态一致；Allure 步骤/附件由插件产出。
- 代价：**引入 `allure-pytest` 运行时依赖**（AGENTS 红线 4「引入新运行时/大型框架依赖先问」）——因此：
  - 默认关闭；
  - `--with-pytest` 时若检测不到 `allure` 模块，**不生成**并给 rc 2 + 明确提示，不静默降级；
  - 文档标注"需额外安装 allure-pytest + allure CLI"。
- 该壳仍是"生成物"，**不自动执行**。

### 8.4 状态语义（不抹平）

生成器不参与状态映射；沿用 Gap 5 §3.5：`failed` 恒 `failed`，`env_blocked/interrupted` → `skipped` + `atlas.fail_class` label（不伪装 PASSED）。**生成器绝不生成"失败即跳过"的 `on_env_blocked: skip` 来掩盖写场景**——写场景强制 `stop`。

---

## 9. 与 `atlas review` 审核塔的接入

### 9.1 复用现有 kind，不新开塔

`review.py` 的批准分发已有 `trace_step` 分支，语义恰好是**"步骤素材不是知识：人工写成 flow YAML 后 `atlas compile`，或 reject"**——这与"生成器产物需人工接手"完全吻合。

因此 `--to-tower` 时：

```python
journal.draft(task=f"gen:{flow_id}", exc=<占位/生成摘要>, kind="trace_step",
              evidence={"source": str(doc), "flow": str(flow_yaml),
                        "contracts_candidate": str(contracts_json),
                        "probes_candidate": str(probes_py),
                        "compile_ok": True, "scenario": "d"})
```

- 草稿 `status="pending_review"`，进同一个 `experience_drafts.jsonl`；
- `atlas review` 列表可见，`--reject-kind trace_step --reason …` 可批量清；
- **不可 `--approve`**（`trace_step` 批准会报错，正是设计意图：生成物不是知识，不能一点批准就入库）。
- 同 id 复用 `append_draft` 的 seen 合并 / 证据刷新（#118），避免重复占塔。

> ⚠️ 注意：`ExperienceJournal.draft` 的 `exc` 参数用于生成 id/fail_class。生成器不能伪造异常。落地实现时给 `draft` 增加一个**只读旁路**（如 `draft_summary(kind=…, identity=…, evidence=…)`），或直接构造行字典并走 `append_draft`（后者已校验 `status` 与去重）。**不修改 `draft(exc)` 的现有语义**，避免影响失败路径（红线级：失败登记不能被生成器改动）。

### 9.2 契约候选的处理（不新增写路径）

| 方案 | 做法 | 结论 |
|---|---|---|
| A（推荐，P1） | 契约候选落 `contracts/<id>.candidate.json`，**人工**决定是否并入 `ATLAS_KNOWLEDGE_DIR/contracts/core.json` | 零新写路径，符合红线 9/10 |
| B（P3 可选） | review 塔新增 `contract_candidate` kind + `approve_to_contract()` 写私有契约库 | 需扩展 `review.py` + CLI choices；仍只写私有目录 |

P1 采用 A（与 `import_jmeter.py`「只生成供人审」同姿态）；B 作为后续增强，届时要补「等值去重/冲突拒绝」与回归测（#128）。

### 9.3 生成物的可追溯

`report.json` 与塔草稿的 `evidence` 互相指向（文档路径 ↔ 产物路径），满足"任何人拿到塔里的条目能 30 秒判断来源"。生成器**不写任何知识/选择器/契约运行时库**。

---

## 10. 如何遵守红线（逐条映射）

| 红线 | 本方案的做法 | 落点 |
|---|---|---|
| #1 无凭据入库 | 生成头/凭据一律 `${ENV}` 占位；`--headers-file` 文档明确"值写占位"；占位符扫描并告警 | §3.4, §7.1 |
| #2 生产禁写 | 生成器不执行；写场景 `read_only:false`，真运行时由 `config.validate_write` 拦截；生成报告加"生产禁用"横幅 | §3 场景 D |
| #3 无「非人工创建」不建单 | **不生成 creator**；`requires` 默认 `find_only`；skeleton `create()` 抛 `NotImplementedError` | §5.1/5.2 |
| #4 新运行时先问 | 默认纯 stdlib；`--with-pytest` 显式开启且检测依赖，缺失即 rc 2 | §8.3 |
| #5 不把 toolbox 当底座 | 生成器不调用 toolbox 执行；可读其抓包**作为人审参考**（P3） | §1.8 |
| #6 禁止步骤内数据兜底 | 只生成 `requires` 声明与"待填"清单；示例值全部标注"需替换" | §3.4 |
| #7 不放宽断言/不吞异常/不改 SKIP | 写场景 `on_env_blocked: stop`；列表断言无法表达就列 `manual_assertions`，不伪造；断言失败不生成任何"跳过"路径 | §4.3, §8.4 |
| #8 run 模式无截图开销 | 生成器不产生截图/取证逻辑 | — |
| #9 未验证 API 不当 UI 兜底 | 只生成 `api_call`/`demo_api` 显式步骤，不生成 `channel: auto` | §4.1 |
| #10 无审核不写回知识 | 产物全部 `pending_review`；契约候选不自动入库 | §9 |
| #11 引擎不含业务 | 生成器独立包 `src/atlas/generator/`，无业务字面量；示例值来自文档；不自动改插件白名单 | §2.1, §6 |
| #12 不为全量造形式代码 | 默认 minimal；core 需文档显式；Task 注册默认零新增；探针只 find；弱项单列不粉饰 | §4.4, §5, §6 |
| #13 不绕过 agent 阈值 | 生成器不调用 agent；产物由人/flow 显式执行 | — |
| #14 配额先占后建 | 不生成建单 | §5.2 |
| #15 不伪造跨平台验收 | 生成报告不宣称"已验证"；只有 `compile.ok` 与离线解析事实 | §7.2 |
| #16 建单报文来自真实捕获 | 绝不猜建单报文；文档示例体只在读场景作请求体，写场景报告点名"报文须来自 `capture_save_request.py` 真实捕获" | §3.4, §5 |

**其它既定纪律（照搬 `import_jmeter.py`）**
- 只生成 YAML 供人审，绝不自动执行；
- 全局鉴权头并入每个 `api_call`（BUG-920）；
- body 分流：`dict/list → json=`，`str/bytes → data=`；
- 占位符保留 + 递归点名；
- 输入错误 `一行 stderr + rc 2`，不裸 traceback。

---

## 11. 测试计划（离线，不依赖真实网络/浏览器/凭据）

新增 `tests/test_generator_sources.py` / `test_generator_emit.py` / `test_generator_cli.py`。

**解析层**
1. OpenAPI 3：两个端点 + `$ref` 组件 → 正确 method/path/body/response；`$ref` 循环 → 截断并报告，不爆栈。
2. Swagger 2：`basePath + paths` 归一；formData → form 字符串。
3. Markdown：`## POST /x` 标题 + ```http curl``` + 参数表 + 响应 JSON → 端点/参数/响应齐备。
4. Markdown：只有标题无样例 → 端点识别成功，`low_confidence` 非空。
5. 纯文本：`POST /api/a` + 参数行 + 样例 JSON → 模型正确且置信度标 low。
6. 无法识别格式 → `ValueError` 点名，CLI rc 2（不裸崩）。
7. 非 UTF-8/坏字节文档 → `errors="replace"` 读入并成功解析或明确报错（#131 纪律）。

**生成层**
8. 场景 A：单读接口 → `api_call` 一步，`save_as` 唯一，headers 合并，`atlas compile` 通过。
9. body 分流：JSON schema → YAML dict；form → 字符串；断言生成后 YAML 加载类型正确。
10. 场景 B：两接口串联 → 插值引用上游产出且无悬空引用报告；重复 `save_as` → 冲突报错。
11. 场景 C：批量 5 接口 → 5 个 flow + `index.yaml`；任一 flow compile 失败 → rc 3 且**不交付**该半成品。
12. 场景 D：`save` 端点 → `read_only: false` + `on_env_blocked: stop`；**探针/flow 中无任何 creator**（测试断言源码文本不含 `def create(` 的实现体）。
13. 契约分级：默认 minimal；对象含 resultCode → high；`x-atlas-level: core` → core 且 `required` 类型映射正确；数组响应**不**升 high；`--level` 不能强升 core。
14. 同一 `(method,path)` 冲突候选 → 报告 `conflicts` 非空，不覆盖。
15. 占位符：头里 `${ISC_APP_HEADER}` → `report.unresolved_placeholders` 命中且 CLI stderr 打印。
16. 列表断言：响应为数组时 → 生成 `manual_assertions[]`，**不**生成必然失效的 `.0.` 路径断言。
17. 全局头：`--headers-file` 的每个头出现在**每个** `api_call` step（BUG-920 回归测）。
18. 可复现：同输入两次生成 → 产物字节一致（无时间/随机）。

**接入层**
19. `--to-tower` → `experience_drafts.jsonl` 出现 kind=`trace_step`、status=`pending_review` 的行；`atlas review` 可列出；`--approve` 该 id → 报错（trace_step 不批准）。
20. `--check-only` → 不写文件；坏 flow → rc 3；合法 → rc 0。
21. `--with-pytest` 且无 allure → rc 2 + 明确提示，不生成半截文件。
22. `atlas gen … && atlas compile <产物>` 端到端 rc 0（离线）；`atlas run` 不在测试中执行（无凭据/浏览器）。

**门禁**：`PYTHONPATH=src python -m pytest -q`（现有 533 passed 基线不回归）+ `compileall -q src` + `atlas doctor`（不改变现有行为）。

---

## 12. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P1（必需）** | `model.py` + `sources.py` + `openapi.py` + `emit_flow.py` + `emit_contract.py` + `checklist.py` + `atlas gen` CLI（`--check-only/--strict`）；场景 A/C | 测试 1/2/6/8/9/11/13/14/16/17/18/20/22 绿 |
| **P2（必需）** | `markdown.py` + `plaintext.py` + `emit_probe.py`（只 find）+ 场景 B/D | 测试 3/4/5/10/12/15 绿 |
| **P3（必需）** | `tower.py`（复用 `trace_step` 进塔）+ 报告人审指引 + 文档同步 | 测试 19 绿；README/CLAUDE/AGENTS 更新新增命令与边界 |
| **P4（可选）** | `--with-pytest`（与 Gap 5 `--via-pytest` 同路线）+ `emit_pytest.py` | 测试 21 绿；依赖缺失时诚实失败 |
| **P5（可选）** | review 塔 `contract_candidate` kind + `approve_to_contract`（写私有契约库） | 冲突/等值去重回归测（#128） |
| **P6（可选）** | 复用 toolbox 抓包校正生成骨架（头/boday 真值），仍只生成 | 需人审；不自动执行 |

> 依赖关系：P1 独立；P2 依赖 P1 的模型；P3 依赖 P1 产物路径；P4 与 Gap 5 的 `--via-pytest` 决策绑定（同一依赖问题，一起问）；P5/P6 需单独立项评审。

**文档同步（AGENTS §5）**：落地后更新 `README.md`（`atlas gen` 用法与"只生成不执行"边界）、`CLAUDE.md`（常用命令 + 关键模块表加 `generator/`）、`AGENTS.md`（若新增验证门禁）；**不得声称已用真实接口验证**，除非确有真实文档跑通并归档输出。

---

## 13. 待确认 / 风险

1. **手册"四种适用场景"原文缺失**：本方案 §1.9 是推断（含证据与替换方式）。落地前需回查手册 2.3 正文；若定义不同，仅替换 §3 场景表与 `--scenario` 枚举。
2. **DSL 列表下标断言不支持**：`dsl._lookup` 对 list 无数字下标，生成器选择"报告 + 契约兜底 + `assert_api`"而非改引擎。若产品要求"任意列表项断言"，属独立引擎增强（可能触及 assert 语义，需回归）。
3. **契约候选入库路径**：P1 只落文件供人审；若要一键进塔需新增 `contract_candidate` kind（P5，涉及 `review.py` 写路径扩展，红线 9/10 敏感区，需评审）。
4. **`--with-pytest` 依赖**：`allure-pytest` + Allure CLI 是新增运行时依赖（红线 4），必须显式确认；默认关闭。
5. **文档质量决定上限**：纯文本/无样例文档解析置信度低，本方案以"低置信度单列 + `--strict` 失败"诚实处理，不追求"任何文档都能一键生成"——那只会制造形式代码（红线 12）。
6. **写场景的请求报文**：文档示例体不能当建单模板（红线 16）。生成器对写端点只给骨架与"报文须来自真实捕获"提示，不做任何猜测填充。
7. **批量规模与审核塔吞吐**：场景 C 可能一次生成数百 flow；`--to-tower` 应支持 `--summary-only`（只登记一条汇总草稿而非每接口一条）以免把塔刷屏；落地时按需实现。

---

## 14. 一句话总结

`atlas gen` 把**文档结构**翻译成 Atlas **可编译、可执行、可汇入 Allure 2.39.0** 的资产，但把**取值、鉴权、写操作、建单、契约升档、知识入库**全部留在人这一侧——生成的每一份 flow 都进审核塔，`atlas compile` 必须绿，`atlas run` 必须由人确认后手动发起。它与 `import_jmeter.py` 的分工是：**JMeter 迁流量保真，`gen` 从文档起测控结构**。

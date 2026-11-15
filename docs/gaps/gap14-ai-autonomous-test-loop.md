# gap14：AI 自主功能测试闭环（wave3 落地记录）

> 本波主轴（2026-11-14 lead 拍板）：用户要求「可以调用 AI 去跑，就像人工测网站那样，
> 然后经验自己提炼出来」。现有 `atlas explore --auto` 已能观察→白名单动作→护栏→
> checkpoint→草稿，**缺的是三块**：判据动作实做、证据与缺陷落盘、坑点自动提炼。
> 本波补齐这三块 + 修复接入轴实测报回的真实缺陷 ob-1。

## 1. 本波做了什么（代码改动逐项）

### 1.1 判据动作实做（`src/atlas/autopilot.py`）

`_execute` 的 `assert` 从 `pass` 改为真正执行四类判据，通过返回 detail、失败抛
`AssertionError`——**归类走 `failures.classify_failure` 单一真相**（经 `recorder.step`
内单点继承），本模块零判词：

| kind | 判据 | 形态 |
|---|---|---|
| `visible` | 元素存在且可见 | `role+name`（走 `locators.resolve` 精确匹配）或 `css` |
| `text` | 文案匹配 | 页面快照 `contains`，或 `role+name` 元素 `inner_text` |
| `api` | 接口状态码与结构 | `status`；`json_path`+`type` / `json_path`+`equals`；可选 `url_contains` 定位响应 |
| `compare` | 跨模块值比对（改前值 vs 改后值） | `record(key,value)` 存改前值 → `compare` 按 `locator` 重读 `count`/`text` 与 `op=eq/neq` 比；或 `locator_a`+`locator_b` 双位置比 |

- 接口响应路径打点复用 `tasks._dig`（单一来源，坏路径点名整条路径）；
- 定位全部走 `locators.resolve`（与组件库/产物同一把尺）；
- `parse_action` 增 assert 形态判据（kind 白名单、各 kind 必填字段、status 必须 int、
  op/read 枚举）——坏形态 parse 期点名，不进执行面猜语义；
- `record` 带 `key` 时存改前值（value 必须标量，容器/None 点名拒绝）；
- 系统提示词更新：写操作确认改用 `record`（trace 动作），assert 四类判据形态写进提示词。

### 1.2 证据与缺陷落盘（runs.jsonl）

- `explore_auto` 全程写 `config.reports_dir / "runs.jsonl"`（复用 `RunRecorder`，
  `mode="explore-auto"`，`case=goal`）；每个执行步一条 step 记录；
- **失败步骤带证据**：`evidence.requests`（method/url/post_data）+ `evidence.responses`
  （url/status/content_type/body）+ `evidence.snapshot`（页面快照）+ `evidence.values`
  （已记录值）——playbook §1.1「没反应类结论必须附请求实参」的可执行形态；
- 请求/响应观察经 `page.on("request"/"response")` **只读**取证（不发任何新请求），
  handler 抛错不顶循环（取证尽力而为，与 session 取证同纪律）；
- **红线 #2**：url 过 `sanitize_url`、post_data/body/快照过 `sanitize_text`——凭据不落盘；
- run 终态：正常收尾 passed/failed（看失败数）；崩溃路径 finally 落盘
  （控制流→interrupted、其余→failed，原因经 `exception_brief` 净化）——半途而废的 run 也是审计事实；
- `atlas allure` / `allure-analyze` / `atlas summary` 直接可吃（标准 runs.jsonl 形态，有消费面测钉）。

### 1.3 坑点自动提炼（只进塔不写知识）

- 失败 → 先查 `match_pitfall(status="approved")`（自愈准入同语义）：命中已审核坑点
  **不重复提炼**，步骤留痕点名命中 id；
- 未命中 → LLM 从证据出 `trigger/symptom/root_cause/remedy`（严格 JSON，四字段非空、
  未知键点名拒绝，摘要过净化刀）→ `journal.draft_from_text` 造 `pending_review` 草稿；
- 草稿 evidence 带 `review.verification_problems` 四元组
  （`command=atlas explore --auto --goal <goal>`、`exit_code=1`、`assertions=本次评估判据数`、
  `verdict="fail"`）——机器产证据正常过晋级闸，但**只进塔**（红线 #9/#10）；
- LLM 摘要调用失败/形态坏：**点名跳过**（步骤 note 记录原因），绝不拿坏摘要冒充证据、
  绝不静默吞掉；失败本身照常进 runs.jsonl（提炼是富化，不是失败的载体）。

### 1.4 护栏沿用且零放松

白名单动作（无 `eval`/JS/SQL/文件操作）、`classify_risk` 风险门（默认 `max_auto_risk=LOW`，
Save/Submit/Confirm 类停）、生产闸早于开浏览器、出境 URL 过 `sanitize_url`、
请求/响应观察只读不发新请求。

### 1.5 ob-1 修复（`src/atlas/config_add.py`）

`atlas config add` 生成的骨架 `session_defaults` 补 `env` 形参并照平台插件口径接
`envs.json` 环境坐标（`environments[env].account/ou`，只在 `session.json` 为空时兜底）。
旧骨架没有 `env` 形参 → `atlas run` 在账号预检处裸 `TypeError` traceback（rc 1），
**生成的骨架跑不起来**。回归测钉「改前必崩/改后不崩」与三层优先级（session.json 优先）。

### 1.6 测试（全部离线：fake LLM + fake page，零网络零浏览器）

- `tests/test_autopilot_assert.py`（新增 55 条）：四类判据 pass/fail 正反对照、parse 形态判据、
  record 存值与 compare、失败归类单点、runs.jsonl 证据落盘（含净化刀）、坑点提炼
  （未命中才提炼/命中不提炼/坏摘要点名跳过/LLM 错跳过）、崩溃路径落盘、allure/summary 消费面；
- `tests/test_config_add.py`（+2 条）：ob-1 回归（env 契约 + 坐标接线 + 三层优先级）。

## 2. 本波没做什么（明确清单）

- **族/栈标签**（components.json 15 条 + 族相关 pitfalls 打 `stack`/`family` 标签，
  可见性统一进 `KnowledgeBase` 复用 `generic` 判据）——lead 定的 P1，本波未做，见待办 ob-4；
- **`ATLAS_UI_FAMILY` 族开关 / families.json / families.py**——lead 已撤销（投机功能）；
- 三份侦察提案（uifamily / onboard / engine）的其余条目——逐条进 §4 待办，未实现；
- 多租户隔离 / 审计链 / 交付脱敏 / 权限角色——lead 已撤销（一次只跑一个平台）；
- 真机验证（真实浏览器 + 真实 LLM 端点）——本波只到离线判据面，见 §5 验证边界。

## 3. 设计要点（判据单一来源，不留第二把尺）

- 失败归类：`recorder.step` 内 `classify_failure` 单点，autopilot 零判词；
- 定位：`locators.resolve` 唯一执行判据（autopilot 的 click/fill/visible/compare 同一把尺）；
- 接口路径打点：`tasks._dig` 单一来源；
- 坑点提炼闸：`review.verification_problems`（不新开第二把闸）；
- 匹配门：`knowledge.match_pitfall(status="approved")`（自愈准入同语义，不留第二份名单）；
- 净化：`credential_gate.sanitize_url/sanitize_text` 单点（url/post_data/body/摘要/原因全覆盖）；
- 会话默认值：`runtime.resolve_account` / `DemoRuntime.__init__` 的 `session_defaults(env=...)`
  契约单点，骨架与平台插件同契约。

## 4. 后续待办（按「找 bug 产能 + 经验沉淀」直接贡献重排）

> **状态（2026-11-16，wave5 收口）**：下表条目已全部处置——**en-6 / ob-2 / uf-1 / uf-2 / uf-3**
> 在更早波次交付；其余 en-*/ob-*/uf-*/complex-flow 由 wave5 backlog 收口波完成，
> 逐条处置与提交见 [`wave5-backlog.md`](./wave5-backlog.md)。下表保留原始分析与排期理由，
> 「建议波次」列是当时的规划而不是现状。**ob-5 的 API 半面**（`attach_demo` 无条件装配，
> 无 API 适配器仍需假造目录）如实留白，需插件 API seam，见 wave5-backlog §4。
>
> 排序标准（lead 拍板）：**P0 实用**=换平台时会**静默失效**的经验/引擎假设（照着经验做、
> 结论假绿或假红）与「这次踩过、下次还得重新踩」的坑；**P1 实用**=把出产能的方法论从
> 「人读文档」变成「可被 `atlas` 消费的机制/资产」；**P2**=其它（形式化/自动化程度/体验类）。
> 每条给：条目 id、一句话机制、为什么本波不做、建议波次。

### P0 实用（换平台静默失效面）

| id | 一句话机制 | 为什么本波不做 | 建议波次 |
|---|---|---|---|
| en-1 | `ATLAS_PLUGIN` 缺失时静默回落 demo 插件：flow 只用通用 Task 时拿虚构知识/契约跑真实系统（假红/假绿） | 改 `resolve_plugin` 的默认策略属引擎行为变更，需独立回归面 | wave3+1 |
| en-2 | 私有知识根单值 + 组件库条目无平台维度：换平台后上一平台已批准选择器静默生效（撞名实证，点错元素） | 需要「平台维度标签 + 读路径过滤」整套数据迁移，超出本波 | wave3+1 |
| en-3 | 审核塔草稿 id 无平台维度：跨平台同任务名+同异常草稿合并，证据互相顶掉 | 草稿 identity 加域参 + review 展示面联动，需独立波 | wave3+1 |
| en-6 | Allure `historyId=sha1(env|case)` 无平台维度：两平台同环境同用例名趋势线静默合并 | 掺平台段会断存量趋势线，属口径变更需 lead 拍板 | wave3+1 |
| en-8 | runs.jsonl 单文件：多平台混写一份，summary 聚合互相污染（`ATLAS_RUNS_DIR` 只隔一半） | 升级 `ATLAS_RUNS_DIR` 语义=落盘位置变更，需新键名或版本警示 | wave3+2 |
| ob-5 | 无旧仓平台被迫假造 legacy/API 目录：doctor 显示 legacy_pages=true 的假绿 | 预检按需化（compile 期判定）+ 跳过开关，属引擎装配链变更 | wave3+1 |
| ob-3 | `gen --plugin X` 的 step.domain 写死 demo：平台专有坑点对自动生成 flow 永不匹配（假绿） | 生成器 domain 接线 + 断言，小改动但需独立回归 | wave3+1 |
| uf-1 | waits 默认选择器（`.el-table`/`.el-loading-mask`/`.el-dialog`）换框架族后 mask 静默秒回（hidden 恒满足→假绿/假红） | **已完成（wave4 A 轴）**——见 `docs/gaps/wave4-ui-family.md`：遮罩缺席时先由族**前缀 DOM 存在性**判据确认页面属本族，缺席点名 `UiFamilyMismatch` | — |
| uf-2 | waits 的 `__vue__` 是 Vue2 运行时假设：换 Vue3 假红 + 归因漂移 | **已完成（wave4 A 轴）**——见同上：哨兵字符串分流，无 `__vue__`/`data` 非数组点名 `RuntimeError`，合法空表不变 | — |
| uf-3 | `components.json` 的 `login.submit_generic`「button, .el-button」换族后命中任意按钮（静默点错，全库唯一） | **已完成（wave4 A 轴）**——见同上：去裸 `button` 兜底 + 顶层 `family` 声明 + `save_component_selector` 保留非 components 顶层键 + doctor/生成期闸 | — |

### P1 实用（方法论资产化：人读文档 → atlas 可消费机制）

| id | 一句话机制 | 为什么本波不做 | 建议波次 |
|---|---|---|---|
| ob-2 | 骨架 envs.json 补 account/ou 占位键（ob-1 已接代码路径，数据层未补）；报错文案与三层口径对齐 | 数据层占位键 + 文案核对，小改动 | wave3+1 |
| ob-4 | components.json 15 条 + 族相关 pitfalls 打 `stack`/`family` 标签，可见性统一进 `KnowledgeBase`（复用 `generic` 判据） | lead 定的 P1，本波未做 | wave3+1 |
| ob-6 | 产物 README 模板内嵌 ISC 业务头字面量 / 凭据回落硬编码 DEMO_*：非 ISC 平台拿到不适用头 | 模板去业务字面量 + AST 扫描测，属产物面治理 | wave3+2 |
| ob-7 | doctor 不覆盖 playbook §9 八项；`_common/domains.json` 接入清单零消费方 | checklist → doctor 子项/命令入口，独立波 | wave3+2 |
| ob-8 | 「骨架→第一个 bug」最短路径无文档串（含每步命令与预期 rc） | 纯文档 + 端到端冒烟，随 ob-1/ob-5 修复后做 | wave3+1 |
| en-4 | env 词表引擎固定 7 个 + 单 base_url：多入口平台报错点名不存在的键（归因错位） | envs.json 多入口形态（opt-in）+ base_url 选择，数据驱动改造 | wave3+2 |
| en-5 | 旧仓资产无条件预检 + `load_dotenv(legacy)` 把旧仓 .env 灌进进程环境（跨平台残留污染） | 预检按需化（同 ob-5）+ env 白名单隔离 | wave3+1 |
| en-7 | 浏览器启动无代理钩子：需走代理的入口报网络层错误，归因漂到站点/网络 | `ATLAS_BROWSER_PROXY` opt-in 键，独立小波 | wave3+2 |
| en-9 | 单会话单页单 base_url：同系统双入口跨入口业务流无承接位 | 双 context 或「两条 flow + 共享 plan_ctx」口径固化，先文档后代码 | wave3+2 |
| en-10 | 外部事件等待无机制（提交后等回调/验证码/跨系统生效） | flow 级 `await: {kind: poll, ...}` opt-in，超时落 waiting | wave3+2 |
| en-11 | `api_evidence` 重放定性有消费侧无生产侧（死接线）：真实 run 里 rich 分类恒不触发 | 平台插件注册重放 handler 或引擎内置只读 GET 重放，接线波 | wave3+1 |
| en-12 | assert 块无 `raises` 负向断言：状态机非法路径/权限强制力这类「期望被拒」用例表达不了 | assert `raises` 形态（opt-in）+ 与 heal/降级链交互钉测 | wave3+1 |
| en-15 | 跨模块对账无引擎机制（UI 列表 vs API total 一致性核对只有手动步骤） | assert 对账模式（两侧 save_as + 字段映射 + 值级 diff） | wave3+2 |
| complex-flow | gen 只出 API 腿；写腿默认 env_blocked 无示范；dispatch/state_machine 无脚手架示例 | 复杂业务流承接的脚手架资产（示例 flow + 写腿示范），随 gen 能力波 | wave3+2 |

### P2（形式化/自动化程度/体验类）

| id | 一句话机制 | 为什么本波不做 | 建议波次 |
|---|---|---|---|
| en-13 | 登录协议焊死四入口点（do_login/switch_to_ou/switch_to_lang/LOCATORS）：新平台缺函数时 AttributeError 归因漂 | 装配期 getattr 逐点检查 + 点名，小改动 | wave3+2 |
| en-14 | exports 产物目录 CWD 相对：换平台同名 flow 互相覆盖 / 产物散落 | root-anchored 或按插件分子目录 + provenance 覆盖检查 | wave3+2 |
| uf-4 | 其余 `.el-*` 选择器换族=找不到（报错但归因漂移） | 随族标签（ob-4）一起治理 | wave4 |
| uf-5 | emit_fast 模板同源副本需随 waits 机械同步 | 锁步测已有，随族机制重议 | wave4 |
| uf-6 | legacy_fast 掩码探针写死 `.el-loading-mask` | 随族机制重议 | wave4 |
| uf-7 | review_code 正则样例含 `.el-*` | 文案级，随族机制重议 | wave4 |
| uf-8 | playbook §2 无机器可读族标签 | 随族机制重议 | wave4 |

## 5. 验证边界（如实）

- **离线判据已测**：四类判据、证据落盘、净化刀、坑点提炼、护栏、崩溃路径、消费面
  （allure/summary）全部有 fake 测钉（`tests/test_autopilot_assert.py` 55 条）；
- **真机未测**：真实浏览器 + 真实 LLM 端点的端到端探索未跑（无凭据/网络约束）。
  真实页面上 `page.on("request"/"response")` 事件面、aria snapshot 的判据可用性、
  LLM 摘要质量需真机冒烟（`atlas explore --auto --goal ...` 一次）确认；
- ob-1 修复的回归测覆盖「改前必崩/改后不崩」与三层优先级，未做真实 `atlas run` 冒烟
  （需真实插件环境）。

## 6. 默认路径逐字节不变（硬约束核）

不设任何新开关、不改 `atlas run` / `waits` / 产物生成路径：
- `explore_auto` 的新行为只在 `explore --auto` 轨（本波需求本身）；
- `config_add` 生成的骨架签名变化是**修复**（旧骨架跑不起来），非行为放宽；
- 全量 pytest 失败数仍为既有 9 个（环境失败），新增测试全部离线可跑。

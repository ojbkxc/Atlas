# C4 暴露面盘点：Atlas 落盘/分发链路上的凭据与敏感 URL 留痕

- 盘点人：pi_inventory（应 gap6_evaluator 请求，关闭 gap6 §7 的 C4「未盘清」项）
- 日期：2026-11-05
- 范围：`abilities.py` 请求留痕是否打印带 query 的 URL；`engine._record_shot` / Allure 附件是否含敏感 header；`recorder`/`reporting` 落盘字段有无凭据泄漏面。已按要求扩到同族面（迁移/生成/探索/留痕），因为这三条的泄漏链都经过它们。
- 方式：**纯静态只读**。未跑真实 SIT 会话、未 commit、未改任何代码。凡「依赖库行为」的推断在下文显式标 `[推断]`。
- 判据基准：`PRIVACY.md` 自述红线 + `AGENTS.md` 红线（#92/#102 家族：承诺与实现漂移）。

---

## 0. 结论（一句话 + 分级）

**三条被点名的面本身干净；真正的口子在「异常文本落盘」与「迁移工具把鉴权头原样写进 YAML」两处，全仓无任何脱敏实现。**

| 级别 | 面 | 状态 |
|---|---|---|
| 高危（净新增/既有，均无防线） | `scripts/import_jmeter.py` 与 `atlas gen --headers-file`：把 `.jmx`/OpenAPI 里的**字面鉴权头值**原样写进 flow YAML，输出默认 **stdout** | **确认缺口** |
| 中危 | 异常文本 → `reports/experience_drafts.jsonl` / `agent_decisions.jsonl` / `runs.jsonl(degraded.reason)`：URL 常随异常消息进入 | **确认落盘面**，是否含 token 取决于 `[推断]` |
| 中危 | 报告分发放大：`artifacts/snapshots/*.png` 进 Allure，而 Allure 按既定做法经 `http.server` 端口对外可看 | **确认链路**，内容风险为业务数据非凭据 |
| 低危 | `atlas explore` trace 落 `page.url`；debug 侧车 JSON 落 `page.url` | 确认，均在 gitignore 目录内 |
| 无 | 被点名的三条：`CallApi.request` 不打印 URL；`_record_shot` 只写 PNG 路径；Allure 不导出 ctx/params/headers | **确认干净（实现符合承诺）** |

---

## 1. 被点名面逐项核验

### 1.1 `abilities.py`：请求留痕会不会打印带 query 的 URL

- `CallApi.request()`（`abilities.py:99-150`）：签名含 `query: dict` 与 `headers: dict`，`self.session.request(..., params=query, headers=headers)` 发出（`:124-125`）。**函数体内无任何 `print`/`log`**；全仓 `grep -rn "print(" src/atlas/plugins/demo/*.py` 亦为空。→ 「打印 URL」这一条不成立。
- 但 URL 确实被**结构化保留**：`ApiResponse` 两份构造都写 `url=response.url`（`:144`、`:147`）。`response.url` 是 requests 拼好 `params` 并跟随重定向后的**最终完整 URL（含 query）**。它随 `last_response` 返回，并被 `CallEndpoint.perform`（`tasks.py:19-20`）整包塞进 `actor.ctx[save_as]`。
- ctx 是否落盘？`RunRecorder` 只序列化 `self.steps`（`recorder.py:47-49`），**不碰 ctx** —— 与 `PRIVACY.md`「业务上下文 `actor.ctx` 不会由 RunRecorder 自动序列化」**一致**。
- 真正的出口是**异常文本**（见 §2）。

### 1.2 `engine._record_shot` / Allure 附件是否含敏感 header

- `_record_shot`（`engine.py:191-205`）只做一件事：把 `session.capture()` 返回的 **Path** 转成相对 `config.root` 的字符串写进 `record["attachment"]`；`shot is None` 直接不写键。→ **不含 header、不含 URL、不含响应体**。
- `capture()`（`session.py:139-142`）→ `snapshot()`（`:72-95`）只 `page.screenshot(path=...png)`。
- Allure 侧 `_step_to_allure`（`allure_report.py:143-176`）读取的 step 字段仅 `step/ok/fail_class/dur_ms/attachment(s)`；`statusDetails.message` 由 `_step_status`（`:120-140`）**按模板拼固定文案**（`"step '<name>' failed: <fail_class>"`），**不含异常原文**；`parameters` 恒为 `[]`。
- `_write_attachment_safe`（`:196-204`）→ `write_attachment`（`:206-234`）按后缀复制字节；附件缺失/坏字节降级为 `text/plain` 占位。→ **附件内容 = 截图字节**。
- **净结论**：被点名的「engine→Allure 附件含敏感 header」**不成立**，此路无 header。风险仅剩截图像素里可见的页面内容（见 §3.2）。
- 一个值得记的形状偏差：`allure_report` 读 `step["attachments"]`（复数，`:166`）与 `ref.get("label")/ref.get("type")`，而**引擎唯一的生产者** `_record_shot` 只写单数 `attachment` 且值为**裸字符串路径**。→ 复数分支与 label/type 目前只被测试消费（无生产方）。这是 Atlas 自己反复修的 #139 同族（「消费侧就绪但没接」），不是安全问题，但说明「Allure 附件面」的契约尚未收敛，做 C4 加固时别在两个键上各修一份。

### 1.3 `recorder.py` / `reporting.py` 落盘字段

`recorder.py:47-49` 的 run 级记录：`run_id / case / env / mode / status / started_at / finished_at / steps`；step 级（`:22`、`:38-41`）：`step / op / dur_ms / ok`，失败再补 `fail_class`。engine 在同一个 dict 上追加：

| 键 | 写入点 | 内容 | 含敏感文本？ |
|---|---|---|---|
| `api_ms` | `engine.py:107` | 浮点耗时 | 否 |
| `attachment` | `engine.py:191-205` | PNG 路径字符串 | 否 |
| `fail_class` / `healed` / `ok` | `recorder.py:33`、`engine.py:119,169-171` | 枚举名/布尔 | 否 |
| **`degraded_to_ui[]`** | `engine.py:251-252` + `_flush_degradations`（`:207-218`） | `{"task":..,"reason":str(exc)[:200]}` | **是 —— 异常原文前 200 字符** |
| **`dispatch[]`** | `engine.py:220-234`（`_flush_routes`） | 路由决策条目（`dsl.py _RouterTask` 写入的**业务取值**与去向） | **是 —— 业务字段值，非凭据** |

→ `PRIVACY.md` 那句「运行记录**默认只保存用例名、环境、步骤名、耗时与失败类**」对现状已不完整（`degraded_to_ui` 带异常原文、`dispatch` 带业务取值、`attachment` 带路径）。方向上两者都在 gitignore 的 `reports/` 内，但**承诺与实现的口径漂移**本身就是 Atlas 的 #92/#102 家族，C4 若落地应把这条口径改写或把 `reason` 收敛为「异常类型名 + 不含 URL 的摘要」。
- `reporting.py`（据 `CLAUDE.md` 条目 + 实测读侧纪律）只做**聚合**：残行/非对象/缺主键跳过，坏字节 `errors="replace"` 回落；`#131` 明确「截断前合法行照常聚合，含坏字节的字符串**如实以替换字符统计**（不静默丢证据也不静默修证据）」——即**逐字保留原文本**。所以 `reporting` 不新增泄漏，但也不会脱敏：`summary`/`report` 若回显 `reason`，等于把同一份异常原文再广播一次。
- **落盘位置的 gitignore 覆盖**（实测 `.gitignore`）：`reports/`、`artifacts/`、`*.har`、`*.jtl`、`*.png`、`*.storage_state.json`、`/knowledge/`、`/flows/iservice/` 均已忽略；`git ls-files | grep -E "^(reports|artifacts)/"` 为空 → **现状无已入库的敏感产物**。但 `flows/` 本身**不在忽略列表**（只忽略 `/flows/iservice/`），这是 §2.1 成立的前提。

---

## 2. 已确认的泄漏面（按可操作性排序）

### 2.1 【高】迁移/生成工具把字面鉴权头写进 YAML

- `scripts/import_jmeter.py`：
  - `:28-44` 收集**全部** HeaderManager 的 `name/value`（注释里点明「巡检请求缺 isc-app 这类业务识别头后端即回 BUG-920」——也就是说真实 `.jmx` 里正是**生产巡检的鉴权头集合**）；
  - `:152-153` `step["headers"] = dict(s["headers"])` —— **逐字照抄**；
  - `:115-140` 唯一的检查是 `unresolved_placeholders()`，判据是字符串里有没有 `${`，**只找未解析变量，不认字面密钥**；
  - `:176-190` 无 `-o` 时 `print(yaml_text)` → **整份含鉴权头的 YAML 进终端/CI 日志**；有 `-o` 时按用户给的路径写文件，而 `CLAUDE.md` 的推荐用法是 `-o flow.yaml`，`flows/` 未被 gitignore（见 §1.3）。
- `atlas gen`（Gap3）同形态：`cli.py:164 --headers-file` → `generator/emit_flow.py:38-44`，`headers = dict(global_headers)` 再并入 OpenAPI 参数示例值，`if headers: step["headers"] = headers` —— 同样逐字入 YAML。
- **对照 pi 侧机制**：pi 的处理是**名单化拒绝**——`lib/credential-paths.ts` 把 `~/.ssh`、`~/.aws`、`~/.pi/agent/auth.json` 等即使在授权根内也一律拒绝读写（`docs/agents/fork-files-security.md` L25-26），且这类判据**只有单一实现**。Atlas 现在一个都没有。
- **可直接搬的最小动作**（不需要新依赖）：迁移器对 header 键名做名单匹配（`authorization`/`cookie`/`token`/`isc-app`/`x-*-key` 等），字面值一律替换成 `${ATLAS_HDR_<n>}` 占位并在 stderr 点名「第 N 步 header X 为字面凭据，已占位，值请放 .env」；无 `-o` 时默认拒打到 stdout（改为写文件 + 打印摘要）。这与 Atlas 既有纪律同形（`probes`/`creators` 的「非空标量才算单号」「拒绝把残缺页登记成全量」都是**产出闸门**而非事后告警）。

### 2.2 【中】异常原文落盘 → URL 带 query 进审计文件

三条持久化路径都写入异常的**原文**：

1. `experience_drafts.jsonl`（根 = `config.reports_dir`，`cli.py:801,884`）：`ExperienceJournal.draft()` → `draft_from_text(text=exception_text(exc), ...)`，`text` 存进 `evidence.text`（`experience.py:36-43`、`:20-33`），注释明确「**人审需要看原文**」。engine 失败路径每次都会登记（`engine.py:181-183`）。
2. `agent_decisions.jsonl`（`cli.py:361`）：失败/Ctrl-C 路径的 `reason` 内插 `exception_message(exc)`（`agent.py:126`）。
3. `runs.jsonl` 的 `degraded_to_ui[].reason` = `str(exc)[:200]`（`engine.py:252`）。

异常原文里会不会有 token？两处**代码级**证据 + 一处 `[推断]`：
- 框架自己就把 URL 塞进异常：`runtime.py:175` `raise RuntimeError(f"顶栏指示器 {indicator} reload 2 次后仍未出现，URL={page.url}")` —— 登录后的 URL（SSO 回调常带 `?ticket=`/`?code=`/`?state=`）会被**原样持久化**进上面 1/2 两份文件。这是确定行为，不依赖任何库。
- `abilities.py:124` 不包 try：`requests` 的 `ConnectionError`/`Timeout` 消息形如 `HTTPSConnectionPool(host=..., port=443): Max retries exceeded with url: /api/x?token=...`，**含 path+query**；`CallApi.request` 抛出后同样进 1/2/3。
- `[推断，未实测]` Playwright 导航超时的报错文案含 `navigating to "<url>"`（带 query）与请求日志。Atlas 大量用 `page.goto`/`wait_for_url`（`waits.py:130-136`）。核实方式（无需真机）：单测里让 fake page 抛 `TimeoutError("<playwright 文案样本>")`，断言草稿 `evidence.text` 是否含 query。
- 注意 ICS 巡检现实（记忆：`D:\jmx` 生产巡检）：**JMeter 习惯把 token/appKey 放 query**，所以「query 里可能有凭据」在这些工程里不是假设。
- **对照 pi 侧机制**：pi 的 `lib/relay/mark.ts:1-16` 为「秘密绝不进转录」专门把进程级密钥从 `process.env` 挪到 `globalThis` 的 `Symbol.for`，理由写得很直白——子进程会继承 env，一条 `env` 就把密钥写进转录；`models-config-redaction.ts` 对配置文件里的字面 key 做**读取即脱敏**。Atlas 的 `config.credential()`（`config.py:187-196`）方向是对的（返回 `(env键名, 值)`，报错只点名键名，实测 `credential()` 唯一调用点 `runtime.py:88-89` 不把值插进消息），**但异常原文通道上没有任何等价的 redaction**。
- **最小动作**：`failures.exception_text/exception_message`（已经是「展示文本单一真相」，#127/#129）内加一道纯函数净化：剥掉 `?` 之后的 query、把 `(?i)(token|pwd|password|secret|cookie|authorization|jsessionid)=[^&\s"']+` 替成 `<redacted>`。改一处即覆盖三条落盘路径（正是 Atlas 自己要的「单一真相，杜绝改一漏一」）。

### 2.3 【中】报告分发放大：Allure 是给人看的

- 附件内容 = 截图 PNG（`session.py:83`）。PNG 里可能含**客户号/单据号/姓名/内网页面**——`PRIVACY.md` 已自觉写明「截图可能包含客户、单号、用户姓名或内部页面，分享前先脱敏并取得授权」，但**代码里没有任何脱敏步骤**，默认 `run` 模式零截图（红线 #5）算是缓解，`debug/heal` 与失败路径必拍（`engine.py:124,173,185`）。
- 放大点在既定工作方式：Allure 报告必须走 `python -m http.server 8765` 打开（记忆 `feedback_allure_http_serve`，否则 `file://` 数据永远转圈）。→ **`artifacts/snapshots/*.png` 的字节会经一个无鉴权端口对外可读**，同一网段任何人都能翻。这不是 Atlas 代码缺陷，是**分发方式**与敏感产物叠加出的暴露面，C4 应显式处置（`--bind 127.0.0.1` 写进 SOP，或报告目录与站点目录隔离）。
- `atlas allure --out` 可指到任意路径，`*.png` 的 gitignore 只在仓库内生效。

### 2.4 【低】探索会话与 debug 侧车落 URL

- `explore.py:38` `print(f"探索会话已就绪（account=..., url={page.url}）")`；`:53-58` 每个 checkpoint 往 trace 写 `{"url": page.url, "title": ..., "snapshot_file": ...}`，aria snapshot 落 `artifacts/exploration/`。trace 默认 `reports/explore_<ts>.jsonl`（`:29`），`--trace` 可指到任意路径（`cli.py:144`）。
- `session.py:88-94`：`mode == "debug"` 时除 PNG 外再写同名 `.json` 侧车，内含 `"url": self.page.url` 与 `state`。**注意**：失败路径也走它（`capture(step, False)` → `snapshot`），所以「debug 下失败一次 = URL 落盘一次」。默认 `run` 模式不写（`mode != "debug"`）。
- 两者均在 gitignore 目录内，且 explore 明确拒绝生产（`explore.py:21-22`）→ 低危。列为 C4 处置清单而非缺口。

### 2.5 已确认**不构成**泄漏面的点（避免 verifier 重复调查）

- `toolbox.save_har`：写 `.har`（含全部请求/响应头）——`*.har` 已 gitignore，且 `toolbox.py:127` 文档诚实自述「导出抓包前必须确认不含生产凭据；**本方法不做脱敏，由使用者负责**」。→ 有意边界，非漂移。
- `toolbox.learn_contracts`（`:52-93`）：只取 `method` + `path` 建 `Contract(name, method, path, level="minimal")`，**不存响应体/头**；`contracts.ContractRegistry.add`（`contracts.py:125`）**无写盘**，学到的一律只在内存。→ 干净。
- `bake_from_playwright`（`abilities.py:158-191`）：Cookie 值只 `session.cookies.set(...)`，**从不打印/落盘**；且域判据是双重闸门（host 全等 或「可注册父域」，裸 TLD 与二级公共后缀名单拒绝），域名归一下写。→ 这是 Atlas 现在**唯一一处**成形的凭据边界实现，值得当作 C4 加固的参照系。
- 密码读取面：`config.credential()`（`config.py:187-196`）键名派生自账号，报错只点名键；`atlas doctor` 不回显凭据值（grep 无命中）。
- 全仓 `grep -rn "redact|脱敏|mask|REDACT"` 在 `src/` 与 `scripts/` 内**只命中 `waits.py` 的 loading mask 与 `toolbox.py` 那句免责**——即「无任何脱敏实现」是**穷尽检查**后的结论，不是抽样印象。

---

## 3. C4 落地建议（映射到 pi 侧机制，只给口径不写码）

| C4 需要补的 | 取自 pi 的哪条 | 关键约束 |
|---|---|---|
| 迁移器 header 闸（字面凭据→占位） | M13 凭据名单（`credential-paths.ts`）+ M14「服务端强制确认」 | 名单匹配**只允许一个实现**；确认位必须在产出侧，不能只在前端/文档 |
| 异常文本净化 | M13 单一路径判定函数 + M15「秘密不进子进程 env/转录」 | 收进 `failures.exception_text/exception_message` 单一真相，覆盖三条落盘路径 |
| `degraded_to_ui.reason` / `dispatch` 口径 | M10「结构化协议消息不作为原始上下文消费」 | 控制流/路由留痕可以是数据，但不能顺手带原文；改 `PRIVACY.md` 那句已漂移的承诺 |
| 附件/报告分发边界 | M13 授权根 + M16 请求真实性 | Allure 只绑 127.0.0.1，或产物目录与可分享目录分离 |
| 「承诺 vs 实现」防漂移 | **M11 哨兵**（`fork-extractions.test.mjs`，resurrection/erosion 双向，每条断言都经变异验证） | 每条 PRIVACY 红线配一条会红的测试；不加测试的红线等于没写 |
| 危险操作两段式确认 | M13 链接目录二次上膛（`linked-directory.ts`） | `--force` 类参数只在第一次被拒后才出现 |

**优先级建议**：§2.1（迁移器闸）> §2.2（异常净化）> §2.3（分发口径）。前两条是**净新增代码极小**（一个名单集合 + 一个纯函数 + 若干测），而暴露的是生产巡检凭据；第三条是 SOP，不需要改码。

---

## 4. 我没有证据的点（留给 verifier / 需要授权才能验）

1. `[推断]` Playwright 导航超时文案是否总带完整 query（§2.2）——离线可用 fake page 抛样本文案钉；真机确认需真实会话。
2. 真实 `.jmx` 巡检文件里鉴权头是否含**字面** token（还是全用 `${__P()}` 变量）——需读 `D:\jmx`，本次盘点范围只到 Atlas 仓，未越界。这一条决定 §2.1 是「已经泄过」还是「随时会泄」，**建议下一步先查它**。
3. `ATLAS_KNOWLEDGE_DIR` 私有库所在磁盘是否也在共享/备份面上（草稿若在私有库则暴露半径更大）——实测草稿根是 `config.reports_dir`，与知识目录不同源，但 `atlas review --template` 导出的决策文件路径由用户给，未审。
4. 真实运行产物（现存的 `reports/`、`artifacts/`）里是否**已经**含 token —— 未 grep 磁盘产物（避免把真实凭据读进上下文），应由人在本地自查一次。

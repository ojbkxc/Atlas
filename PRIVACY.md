# PRIVACY.md — Atlas 隐私与数据边界

## 原则

Atlas 默认在本机执行，不内置云端 LLM、遥测、账号服务或自动上传功能。代码库不应包含真实凭据、Cookie、业务单据数据或员工个人信息。

## 凭据与会话

- 密码只来自 `.env` 或进程环境变量；`.env` 已 gitignore，`.env.example` 只能放空值和示例键名。
- 沿用 `DEMO_USERNAME` / `DEMO_PASSWORD` / `DEMO_PWD_*`，不为新框架另发明凭据存储。
- Playwright storage state、Cookie、HAR、抓包导出可能含会话秘密，均不得提交。
- Cookie 仅同步到匹配的 Demo 目标域；不要记录 Cookie 值或将它们发送给无关域/第三方。
- 日志和异常不得主动打印密码、Cookie、Authorization、完整会话文件。

## 运行产物

`artifacts/`、`reports/`、截图、JSONL、HAR、JTL 均默认 gitignore。截图可能包含客户、单号、用户姓名或内部页面，分享前先脱敏并取得授权。

Atlas 运行记录（`reports/runs.jsonl`）默认保存：用例名、环境、模式、步骤名、耗时、失败类
（`env_blocked/session/interaction/contract/data/assertion/interrupted/unknown/waiting`），
以及每条 step 的取证附件路径（`attachment`，仅 debug/heal/失败）。步骤记录里还有几类**实打实
会落盘**的业务字段，分享 runs.jsonl 前必须知道：

- `reason`（run 记录**顶层**，`status: "waiting"` 与 `"env_blocked"` 两类行出现）：这条跑停下来的
  原因摘要，内容是 `failures.exception_brief` 的**净化后**文本（截 200 字符，与其它落盘文本同一把
  凭据刀），来自业务 Task 显式声明的「在等什么、需要谁」或「被什么环境条件挡住」，不是原始报文。
  `env_blocked` 侧 2026-11-06 才接通写侧（此前该腿只写内存 ctx，键不出现在 run 行里）——
  接通时最容易忘的是净化：根因文案常带被掐断的回调 URL，不脱敏等于把凭据写进一个新落盘字段，
  防线是 `test_dsl_env_blocked_reason_is_sanitized_and_capped`（值遮蔽、键名保留、≤200）。
  其余终态（passed/failed/interrupted/timeout）**仍不写这个键**——旧行形态逐字节不变；
  `atlas summary` 的 `attention` 块与 Allure 的 `statusDetails.message` 会把它带到报告面，
  所以分享报告等于分享这条业务文案，与其它落盘文本同规则处理；

- `degraded_to_ui`：`channel: auto` 降级到 UI 的原因摘要，内容是**净化后的异常消息**
  （见下「异常与 URL 净化」），不是原始报文；
- `dispatch`：路由节点的取值与去向（`{{slot}}`/`dispatch.on` 读到的业务值）——路由审计必需，
  因此它是业务数据，不是纯元数据；
- `delivery_uncertain`（C12）：布尔标记，仅当写任务的 API 尝试**已发出请求**而被拦下
  「自动降级重跑」时出现（值是判定结果本身，不含报文/凭据；随附的 `degraded_to_ui`
  不存在，因为降级被拒绝）。它总是与 `ok: false, fail_class: "interaction"` 同现——
  投递不确定的失败保持 failed，不借任何终态洗绿；
- `idem`（C12，在 `recycle_list.jsonl` 条目上，非 runs.jsonl）：16 位 hex 幂等关联把手，
  由 `sha1(entity|scope|where)` 截断派生——**值来自业务查询条件本身**（客户号、状态码等），
  不是凭据；刻意取 16 位（< 净化刀的 ≥32-hex 判据）以保证它能活着穿过文本净化被人工
  检索。where 里若含凭据形态的值，那本来就不该写进数据供给条件（进净化面的是异常文本，
  不是这里的哈希输入）。

业务上下文 `actor.ctx` **不会**由 `RunRecorder` 自动序列化；`version`（版本三元组：引擎版本 +
知识库内容 sha1 指纹 + flow 摘要）只含版本号与哈希，不含知识内容与凭据。新增埋点不得将完整
请求体、响应体或业务对象默认写入日志。

**快模式产物（`atlas gen --with-pytest --fast` / `atlas export-pytest --fast`）跑起来后新增的落盘面**——产物目录
`<out>/reports/` 下（可用 `ATLAS_RUNS_DIR` 改道），与 Atlas 运行产物同等对待：

- `runs.jsonl`：行 schema 兼容 Atlas——用例名（`<module>::<func>`）、`env`（`ATLAS_FAST_ENV`）、步骤名、
  耗时、失败类、run 级终态；**run 级 `reason` 只取产物框架自产的点名文案**（缺配置键名/未接线组件元素名/
  写闸与看门狗文案），非空才写键、截 200——`str(exc)` 原文、HTTP 正文、URL query 与服务端返回文本**一律
  不落**（产物侧不复制 `credential_gate` 第二把净化刀，「根本不写不可控文本」是更强边界；净化等值性按
  设计记为留白，见 `docs/gaps/gap9-fast-pytest-runner.md` 落地注 F-5）。断言失败的消息走 step 名
  （以 `assert ` 开头），不进 reason。
- `plan_ctx.json`：跨批上下文，**只传标量**（客户号/单号类业务值）；容器/长文本被标量闸拒落——WARN 点名
  键名、**值不回显**。这些标量是真实业务数据，分享前按运行产物脱敏。
- `reports/artifacts/*.png`：仅失败步骤截图（成功不截图纪律与红线 #8 同源），路径以**绝对路径**写进记录——
  拷贝/分享 runs.jsonl 时截图引用会指向原机器路径，属预期行为，外发前打包或脱敏。
- 产物**自身源码**（`test_*.py`/`atlas_pages.py`/`conftest.py`）：模板零业务字面量（登录指示器、URL 匹配
  子串、凭据全走 env，有哨兵测钉住）；但 flow 导出产物会把 flow 里的步骤参数原样带进生成码（真实 URL、
  账号名、单号可能在内），页对象库从**当时生效的知识库**派生真实选择器/组件名——私有库
  （`ATLAS_KNOWLEDGE_DIR`）接入时产物含真实内部选择器（形如 `PASSWORD = '#password'` 的派生常量是
  **密码输入框的 CSS 选择器，不是凭据**，但自家评审器会按字面命中——对产物取证必须声明库源现值，
  带库/回落库两种产物不同）；账号密码永不进产物文件，运行期才从 env 读。`exports/` **不在 .gitignore**：
  快包放受控目录、勿提交，与 `flows/` 产物同口径。
- attach 通道（`ATLAS_FAST_CDP` + `ATLAS_FAST_INTERACTIVE=1`）执行发生在**人自己正在用的浏览器会话**里：
  页内容由脚本操作、失败截图取自人的现用页——只在人显式在场声明后使用，prod 环境恒拒绝并回落 launch，
  调度器 argv/env/行为三层保证无人值守永不经 attach；前提是人核对过那个标签页即目标账号（产物不替人
  核账号，账号错配的责任在接线的人身上）。

**旧仓兼容快脚本（gap11：`atlas legacy --fast` / `atlas export-pytest --fast --compat legacy-suite`）新增的落盘面**——
只有一处，且**不含页面内容**：

- `ATLAS_FAST_WAITS_STATS` 指定的路径（**该键本身无默认值——`--fast` 也不会替你写它**，未置位就不产生任何文件）：逐调用 jsonl，字段固定七键
  `caller_file`/`caller_line`/`caller_func`/`requested_ms`/`actual_ms`/`probe`/`fallback`。这些是**代码坐标与毫秒数**：
  来自旧仓源码的**本机绝对/相对路径 + 函数名 + 行数**（分享出去等于暴露内部目录结构与函数命名，按源码同等对待）、
  生效档位名（`mask`/`mask+xhr`/`mask+xhr+mutation`）、以及**固定词表内的短码**：真回退原因（`probe-error:*`、`no evaluate`、`unrecoverable:*`、`hook-degraded:*`）与 #33 后表示「本次从头到尾没见过忙信号、按原值睡满」的 `no-busy-observed`（两者共用 `fallback` 一个字段、`;` 连接，**词表是枚举值不是自由文本**）。**永不包含**：页面 DOM 文本、URL、
  表单值、凭据、截图字节——垫片在页侧求值的是 `document.readyState`/遮罩可见性/在途计数/DOM 活动这类**状态判据**（#33 后探针返回的是**枚举短码**而非布尔，码表是固定词表、同样不含页面内容），
  **判据求值结果本身不落盘**（落盘的只有最终归到 `probe`/`fallback` 两个字段里的档位名与短码）。落盘失败只 WARN 且**不回显路径或值**（尽力而为，不顶掉用例结果）。
- 收尾 summary 打在终端（非文件）：`calls/original_ms/actual_ms/saved_ms/fallbacks/no_busy_observed/floor_ms/anchor/tiers`——**`fallbacks` 与 `no_busy_observed` 是两个语义分立的数**：前者只数**真回退**（probe 异常 / 无 evaluate / 不可恢复 / 档位降级），**不含** `no-busy-observed`；后者=「本次从头到尾没见过忙信号，按原值睡满」的笔数，是**判据的正常路径不是故障**。两者都只可能是**整数计数**，不含任何页面内容。所以读报告的规矩是：`fallbacks=0` 只说明**探针没出错**，**是否发生了 fail-safe 睡满要看 `no_busy_observed`**——把两个数相加当「异常数」、或把后者读成故障，都是误读（同一口径见上条 jsonl 字段说明）。
  其中 `anchor=` 后面是 `ATLAS_FAST_SETTLE_ANCHOR` 的**选择器字符串本身**（如旧仓建议的 `.app-main`），
  属内部实现细节；**录屏/贴终端输出到工单时按内部选择器同等对待**（值只来自 env，模板与产物码里零业务字面量）。
- 兼容包**不产 `runs.jsonl`**：桥接腿整条委托旧 `run_case_py`，报告与失败截图仍由旧 runner 自产（旧仓侧行为，
  Atlas 不改也不新增）；`--compat` 产物目录里的 `reports/plan_ctx.json` 只收**白名单标量**（`_logged_in_account`
  =账号名 + 步骤派生的 `_tcN_ok` 类标记），容器/长文本拒落、WARN 不回显值——账号名是业务数据，分享前脱敏。
- vendored 垫片两份（`legacy_fast.py`/`fastplugin.py`）按**真文件字节**拷进产物，头部 provenance 只含 Atlas 仓内
  相对路径与 sha256，不含提交信息、不含凭据；产物源码零 `import atlas`，选择器/指示器/凭据一律运行期从 env 读。

## 字面凭据闸与异常净化

- **迁移/生成产出的敏感头闸**：`scripts/import_jmeter.py` 与 `atlas gen` 产出的 flow YAML 中，
  命中通用凭据词表（Authorization/Cookie/token/password/secret/api-key/sessionid/ticket 等）
  且值是**字面量**的鉴权头，一律替换成 `${ATLAS_HDR_<n>}` 占位；映射清单只报键名，
  凭据值不进 YAML、stdout、stderr。已写成 `${VAR}`/`${__P()}` 的外部化值不重复告警。
  迁移产物只有在 `-o` 写文件时才打全文；无 `-o` 且闸命中时只打摘要——终端回滚缓冲与
  CI 日志不是凭据的存放处。`flows/` 不在 gitignore，产物请放受控目录。
- **异常与 URL 净化**：落盘的异常文本（经验草稿 `evidence.text`、agent 留痕 `reason`、
  step 的 `degraded_to_ui.reason`）与框架自己写进异常的 `page.url`，都经同一把净化刀处理：
  URL 的 query **逐键**命中才 `<redacted>`（`docNo`/`page`/`ou` 等业务取证参数保留），
  fragment 整抹；`敏感键=值`、JSON 里的 `"token": "…"`、`Cookie:`/`Authorization:` 行的值段
  遮蔽。失败归因（`classify_failure`）读的是**净化前原文**——否则关键词恰好只在敏感值里
  （`?code=401`）时，脱敏会把会话问题翻成「无法归因」，比泄露更隐蔽。
- **边界**：净化不覆盖请求体/响应体里的裸高熵串（那是 body 面，完整报文本就默认不写日志）。
  `atlas explore` 的 trace 与 HAR 导出仍按运行产物同等对待：含真实 URL/文案，不提交。

## 报告分发

`atlas allure` 的结果集与生成的报告目录可能含真实 URL、单号与截图。**本机查看一律只绑回环地址**：
`python -m http.server 8765 --bind 127.0.0.1 --directory <报告目录>`（`file://` 直接打开 Allure
页面会一直转圈，必须走 HTTP，但不得监听 `0.0.0.0` 把报告开放到局域网）。`allure serve` /
`atlas allure --serve` 的提示文案同样按此口径。对外分享前先人工脱敏并取得授权。

## 外部项目与服务

- `legacy-suite`、`legacy-api` 通过本机路径适配，不自动上传、不自动修改。
- `toolbox` 的抓包、重放和导出可能含敏感信息，只应在用户授权的浏览器会话和目标环境使用。
- 不把项目代码、知识、截图、接口响应或凭据自动发送到第三方 AI。未来若接入 LLM，必须显式配置、最小化字段、脱敏并让人确认。
- 对生产环境只读；对其他环境的写操作仍需遵守公司授权和业务影响控制。

## 知识库与跨公司携带

- 引擎可携带；公司业务知识、账号、域名、内部路由与客户信息不一定可携带。
- 离职/换公司或向外部开源前，移除私有插件和内部知识，不要以“知识数据化”为由带走前公司的机密。
- 仓库自带的 `plugins/demo/knowledge` 是虚构化的示例数据；真实内部系统的行为与缺陷经验属于公司资产，必须放在私有插件里，不得提交。
- `knowledge/session.json` 只是 Demo 测试账号名/OU/division 的默认值（无密码）；密码仍只来自 `.env`。真实业务账号与组织编码不得写入仓库。
- 对接真实业务时的会话值（`ATLAS_SESSION_ACCOUNT/OU/DIVISION/LANGUAGE`）与项目映射键（`ATLAS_ENV_PREFIX`、`ATLAS_LEGACY_WEB` 等）都只写本机 `.env`，仓库只保留虚构默认；`atlas explore` 的 trace 可能含真实 URL/界面文案，按运行产物同等对待，不提交。
- **调度台账（C1）**：`reports/schedule/tasks.json` 与 `schedule_runs.jsonl` 在 gitignore 的 `reports/` 下，`run_args` 允许出现**账号名/OU 等非凭据值**（密码恒从 `.env`，调度器拼的 argv 永不带 `--approve`/`--mode`），`child_command` 只记 flag 形态不记值参——但账号名与 flow 路径仍是内部信息，分享/贴工单前按运行产物脱敏。调度目录必须在本地文件系统：同步盘/NFS 上 O_EXCL 锁不可靠（双跑风险），不属支持形态。
- 新坑草稿必须经人工审核，剔除客户编号、账号、Cookie、个人信息后再共享。

- 私有知识目录（`ATLAS_KNOWLEDGE_DIR`）与 BU overlay 文件（`atlas overlay` 的输入及批准后的
  `bu_overlays.json`）承载真实内部坐标与选择器：只放本机/受控盘，不得提交、不得进备份共享；
  `--template`/mapping 文件、`review_decisions.jsonl` 含真实任务名与坐标值，外发前同样脱敏。

## 数据创建与清理

自动创建的单据必须有「非人工创建」标记，以便与人工数据区分、检索和回收。create 配额只降低意外造数风险，不替代业务授权和清理流程。

## 本次真实运行产生的数据

2026-11-01 的真实 SIT 验证（含旧项目兼容回归）在 SIT 环境创建了若干带「非人工创建」备注的测试单据，
例如销售订单 `SO-DEMO-0003` / `SO-DEMO-0004` / `SO-DEMO-0005`、报价单 1 张、退货单更新 1 张。
这些是本轮验收产生的测试数据，不是产品数据；清理责任仍在执行环境管理方。
Atlas 探针只做只读查询，`action=find` 未创建任何数据。

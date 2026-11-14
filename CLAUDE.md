# CLAUDE.md — Atlas 开发者速览

完整行为约束见 [AGENTS.md](./AGENTS.md)，冲突时以 AGENTS.md 为准。

## 项目概览

Python 3.11+ / Playwright(sync) / requests / PyYAML / pytest。**Atlas 定位 = 可移植 pytest 脚本包的生成器 + 验证器：产物优先、平台可选**（红线：不为变绿放宽断言；凭据不落盘）。Atlas 用 Screenplay 统一 UI 与 API，用业务插件承载知识，用旧项目适配器保护已有资产。

- 当前目录：`D:\GitHub\Atlas`
- 包：`src/atlas`
- 业务插件：`src/atlas/plugins/demo`
- 外部资产：`../legacy-suite`、`../legacy-api`，只读复用
- 业务会话默认值（账号/OU/语言/division）分层来源：显式 `ATLAS_SESSION_*` env > 私有 `session.json`
  > 私有 `envs.json` 的环境坐标（`environments.<env>.account/ou`，仅真实业务插件消费）> 包内
  `plugins/demo/knowledge/session.json` 结构默认（仅账号名，无密码）。空账号在开浏览器前拒（`runtime.require_account`）。

## 常用命令

```bash
python -m pip install -e ".[dev]"
python -m playwright install chromium
atlas doctor
atlas tasks
atlas compose --goal document_no
atlas legacy --dry-run
atlas legacy                         # 在旧项目目录真实执行
atlas legacy --fast [--dry-run]      # 注入 atlas.fastplugin 并默认置位 NO_STEP_SHOTS=1（截图抑制）；等待垫片是显式 opt-in（#36）：要提速自设 ATLAS_FAST_WAITS=mask|mask+xhr|mutation，不自设时打一行 NOTE；不带 --fast 时命令行与环境逐字节不变
atlas compile examples/return_from_so.yaml   # 离线校验 flow YAML（不登录）
atlas run <flow.yaml> --harvest       # 跑绿后动作序列固化为 flow_candidate 草稿进塔（等价 flow 级 harvest: true）
atlas review                         # 审核塔：待审草稿证据卡片
atlas review --kind selector_candidate  # 按类清塔
atlas review --group                 # 聚类视图（批量类紧凑表）
atlas review --kind <kind> --template mapping.jsonl  # 导出决策模板，人填后 --apply 批量批准
atlas review --reject-kind trace_step --reason "…"   # 整类驳回（原因必填）
atlas review --stats                 # 队列吞吐
atlas library                        # 公共任务库：已审批可复用动作序列（只读；写路径只有 review 批准 flow_candidate）
atlas library <名>                   # 单条详情（steps/assert 原文，可作 flow 骨架复用；未知名 rc2 点名现有条目）
atlas review --approve <id> --remedy "…"                    # 批准（默认不再强制证据三元组——重构波松绑，风险见 AGENTS §3）
atlas review --approve <id> --component c --element e --role button --name "保存"  # ARIA 候选直接入库（gap13，免手抄 CSS）
atlas review --approve <id> --require-evidence              # 显式开 C2 证据闸：缺三元组则拒批准（旧硬保证，opt-in）
atlas review --approve <id> --require-evidence --allow-unverified --reason "…"  # 开闸下人工豁免（必须留痕；批量 --apply 同受 --require-evidence 约束）
atlas review-code tests/             # 代码质量评审：扫描 pytest/DSL/契约，输出含评分 Allure 报告
atlas review-code tests/ --review-mode knowledge  # 知识库增强（仍离线）
atlas review-code tests/ --format json --fail-under 75  # CI 门禁
atlas allure reports/runs.jsonl --out reports/allure-results  # runs.jsonl → Allure 结果集
atlas allure-analyze reports/allure-results                  # 解析 Allure 结果集：分类汇总
atlas allure-analyze reports/allure-results --to-tower        # 未命中项登记 pending_review 进审核塔
atlas explore                        # 非生产 CDP 探索会话（外部大脑接管）
atlas explore --compile <trace.jsonl>  # trace → pending_review 草稿
atlas explore --auto --goal "…"      # LLM 全自动探索（gap12）：目标进去草稿出来；须配 ATLAS_LLM_* 三键；写类动作默认被审批门拦停
atlas init <name> --system <s> --module <m>  # 生成新项目骨架
atlas config add <domain>             # 生成插件骨架或映射片段
atlas gen <doc> [--scenario a|b|c|d] [--strict] [--check-only] [--to-tower] [--with-pytest [--fast]]  # 文档→测试代码（只生成不执行；--fast 须配 --with-pytest，单用 rc 2）
atlas export-pytest <flow.yaml> [-o DIR] [--fast] [--force] [--parallel N]  # 已存在的 flow→可独立运行 pytest 包（只生成不执行；rc 0/2、3 不使用；--fast UI 腿需 page_object/legacy_pages 声明位；--parallel>1 须配 --fast，运行需 pytest-xdist，缺依赖运行期点名不静默降级）
atlas export-pytest <flow.yaml> --fast --compat legacy-suite  # 旧仓可直接吃的布局（class+@pytest.mark.order、产物零 import atlas、垫片按字节 vendored；--compat 须配 --fast，未配对/未知值 rc 2 且零产物）
# 产物运行期旋钮（产物侧 env，全量以 _atlas_fast.ENV_KEYS 为准）：ATLAS_BROWSER_CHANNEL=chromium|chrome|firefox|webkit（T5 浏览器矩阵，缺省 chromium）；
#   ATLAS_FAST_PARALLEL>1 分片；ATLAS_FAST_TRACE=1 + ATLAS_FAST_TRACE_ACCEPT_SECRETS=1 双确认才开失败腿 trace（缺第二键只 WARN 不开，zip 含密码勿外发，红线 #2 显式例外）
atlas schedule add <id> --flow <yaml> --cron '*/30 1-8 * * *' --tz Europe/Amsterdam  # 登记调度任务（默认只读档）
atlas schedule run <id>               # 跑一轮 tick——给 OS cron / schtasks 调起，Atlas 不做常驻进程
atlas schedule run <id> --dry-run     # 只体检+决策，不抢租约不派发
atlas schedule status                 # 错过几轮 / 连败 / AUTO-PAUSED 汇总
atlas schedule crontab <id> --platform linux|windows  # 打印接线成品行（绝不代注册 OS 任务）
PYTHONPATH=src python scripts/boundary_classify.py --diff incoming.diff [--to-tower]  # C5：吸收/merge 前给入站变更集分级（cosmetic 放行/structural 进塔）
pytest -q

# CDP 冻场调试：步骤 3 前冻住，另开终端 playwright-cli attach --cdp=http://localhost:9222
CDP_DEBUG=1 CDP_PAUSE_STEP=3 atlas run <flow.yaml> --mode debug

# 未安装时的 Windows bash 门禁
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m compileall -q src
```

PowerShell 设置模块路径：`$env:PYTHONPATH="src"`。

## 关键模块

| 文件 | 用途 |
|---|---|
| `allure_report.py` | Allure 2.39.0 结果集产出（Gap5 §3 方案 A 纯 stdlib 直出，零新依赖）：`emit_allure` 把 runs.jsonl 转 `allure-results/atlas-*-result.json`；§3.5 状态映射——passed→passed、failed→failed+`atlas.fail_class` label、env_blocked→skipped+`atlas.blocked=true`、interrupted→skipped+`atlas.interrupted=true`、unknown→broken（红线 #7：env/操作终态映射 skipped 但带 label，不洗绿不进 failed）；historyId=sha1(env|case) 稳定跨运行聚合、uuid 每份独立；读侧复用 reporting 容错纪律（#131/#112：坏字节 replace 回落、残行/非对象跳过计数）；clean 只删 atlas-* 前缀不误删用户附件；附件缺失/坏字节降级 text/plain 占位（#126/#64：报告可以有噪音不能丢整份）。#139 生产侧接线：`attachment` 键此前无任何生产方——engine 三处 `capture()` 返回值全丢弃，真实 runs.jsonl 永远 0 附件（消费侧就绪但没接=断链，#106/#86 同族）；现 engine `_record_shot` 把截图按 root 相对写进 step 记录（run 成功 None 不写键，红线 #8 零涟漪），CLI allure 显式传 `attachments_dir=config.root`（旧回落 runs_path.parent 把 root 相对引用假报「无法读取」占位）。C6：`parameters` 从恒 `[{env}]` 扩为 run 记录带 `version` 时追加 `atlas_version`/`knowledge_sha1#source`/`flow_md5#kind` 三键（`unreadable` 的 sha1 是空串仍显式写 `-#unreadable`，不静默丢结论；缺项就不写该项，不填假摘要冒充），**labels/historyId 不掺版本**——掺了会把同一用例的跨版本趋势断成多条线，恰好看不见漂移；无 version 的旧行为逐字不变 C9：§3.5 状态映射追加同族第二实现——`status=="waiting"` 或 `(failed ∧ fail_class=="waiting")` → `skipped` + `atlas.fail_class=waiting` + `atlas.waiting=true` + `statusDetails.message` 保留 run 顶层 `reason` 原文（缺省回落 `awaiting human intervention`，与 blocked/interrupted 先例逐字同构）；`_step_status` 同族一支。**不构成红线 #7 的「把失败改 SKIP」**：那是既有「操作/环境终态→skipped+label+不洗绿」的同族扩展，「是否非缺陷」的判断不在本层复制；「缺 label → broken」兜底分支不动（旧版消费器读新 waiting 行 = broken，方向是 fail-loud 不是洗绿）；supervisor 的 `status:"timeout"` 仍走未知→broken，本波不给 skipped 待遇 |
| `allure_analysis.py` | Allure 结果集解析（Gap5 §4 汇入侧）：`load_allure_results` 读 `*-result.json`（坏字节 replace 回落、坏 JSON/非对象跳过计数 #131/#112，绝不因一条坏结果整份崩）；`parse_allure_result` 带 `atlas.fail_class` label 直接采用（自产往返），否则 `classify_text(f"{message} {trace}")` 归类（外部 SCS 结果）；`analyze_allure` 汇总总数/通过/失败/跳过 + fail_class 分布 + 四桶（§6.3 可配置分组不写死引擎——红线 #11）+ 命中坑点 + 自愈提案；`to_tower=ExperienceJournal` 时未命中项登记 pending_review 草稿进既有审核塔（不新开塔、不新增知识写路径——红线 #9/#10）；自愈提案只提案不执行，控制流异常（interrupted）一律拒自愈（#108）；`--to-tower` 走 ExperienceJournal，`--format text` 人读版，rc2 用户错误。C2：`--to-tower` 的未命中草稿带 `evidence.verification`（`build_allure_verification`：verdict 由 Allure status 派生 passed→pass、其余→fail，exit_code 同向 0/1，assertions=result `steps` 计数，command=`atlas allure-analyze <目录>`——可重跑的是**解析**命令，原结果集能否重跑真实用例未证，design_c2 §8 明写「来源: allure 分析」让人自判证据强度）——这类草稿因此无需 override 即可过晋级闸；仍只登记 pending_review，不新增知识写路径 C9：`DEFAULT_BUCKETS["environment_blocked"]` 从手抄两个字面量改为 `frozenset(OPERATIONAL)` 派生——桶**键名**是对外口径不改名，语义=「操作/环境族桶」写进注释；家族成员判据自此只有一处（此前每消费点各抄一份，加第三个成员必漏一处，而漏的那处恰好把「等人」读进缺陷桶=#92 温床）。收益立现：`test_default_buckets_cover_all_classes`（遍历枚举）从「加枚举必红」变「自动纳入」——那是验收项而非回归。外部 SCS 结果仍走 `classify_text`，永不产出 waiting（输入侧焊死） |
| `credential_gate.py` | C4 凭据泄漏面**唯一判据实现**（消费方只许调它，禁复制第二份名单/第二把净化刀 #92/M13）：`SENSITIVE_HEADER_TOKENS`/`SENSITIVE_QUERY_KEYS` 是**通用 HTTP 凭据词表**不是业务头清单（红线 #11——`isc-axios-sessionid` 靠 "sessionid" 子串命中，业务名不进名单）；`header_is_sensitive`=键名命中名单 ∨（值呈字面秘密 ∧ 键名有凭据语义），**已外部化值恒放行**（`${VAR}`/`${__P()}` 是人审接 env 的位，不重复告警）；`looks_literal_secret` 只认 UUID/JWT/≥32 hex/base64 形态，中文文案、纯数字流水号、带空格句子恒 False（不猜）；`gate_flow_headers` 把 flow steps[*].headers 的字面凭据换成 `${ATLAS_HDR_<n>}`，同一 (键,值) 全 flow 复用同一序号（582 个重复头只需接一个 env 键），返回映射清单**不回显值**；`sanitize_url` **逐键命中才 redact**（评审必改 C4-A：整段抹 query 会销毁 `docNo/page/ou` 这类追责证据，与 #85 相抵），fragment **分形态**（`_sanitize_fragment`：`/` 开头的路由型 fragment 保留路由段、只逐键 redact 其 query、路由段内像字面秘密的段逐段遮蔽——ICS 是 hash 路由 SPA，旧「fragment 整抹」把页面坐标这条取证证据一起抹掉且让 `explore:step:<url>` identity 全坍缩；非路由型 `#access_token=…` 维持整抹），相对 URL 同样处理；消费方：`runtime` 异常里的 `page.url`、`explore._record_checkpoint`（trace 落盘）与 `compile_trace`（读侧同防线，旧 trace 二次净化）、`autopilot._observe`（**出境**：url 拼进给 LLM 的 user 消息——真实 SIT 第一轮实测带 `?ticket=ST-…`，不过刀即凭据发往外部端点）；`sanitize_text` 四道刀（URL→k=v→JSON `"键":"值"`→`名单键: 值` 行，值段抹到行尾——「Bearer 前缀+短 token」逐段判熵会漏，只擦半截比不擦更坏），**幂等**且永不抛（坏 `__str__` 回落空串 #89）；`mask_placeholder_value` 管 `${__P(name,DEFAULT)}` 的回退段（C4-C：旧迁移清单 `val[:80]` 把 DEFAULT 字面值打回 stderr） |
| `versioning.py` | C6 版本三元组**唯一计算点**（纯读，零新写路径）：`knowledge_fingerprint` 对私有目录（有效→`source=private`）或包内 `plugins/demo/knowledge/`（`builtin`）内全部 `*.json` 做两级 sha1（逐文件字节摘要→`as_posix()` 键排序拼接→sha1[:12]），**键用 POSIX 且排序**——反斜杠进键则同一库两台机两个指纹，跨机对比前提当场失效（X1 专项测）；目录存在但读崩 → `{sha1:"", source:"unreadable", error}` 不裸崩也不静默（#64，doctor `_read_text_safe` 先例）；`flow_digest` 有源文本用 dsl 现成 md5（`kind=source`），内存 dict 走派生 sha1（`kind=derived`，**剔除 `engine` 注入键**——AtlasConfig 的 str() 带内存地址，进了摘要就每次 run 变一个值）；用 sha1 不用内置 hash（PYTHONHASHSEED，knowledge.draft_pitfall 同款先例）；知识库版本**不用 git sha 冒充**（私有库是人手改的，git 盖不住未提交改动）；`private_knowledge_root` 取源顺序 actor.knowledge.root → resolve_plugin()（惰性 import 避 dsl↔plugin 环）→ `ATLAS_KNOWLEDGE_DIR`，不留第二份判据；`tz_data_status` 是 X6 前置体检，缺 tzdb 点名 `pip install tzdata` 不静默回落 UTC |
| `boundaries.py` | C5 边界账本**判据核**（评审 C5-A：判据上移到 src，`scripts/boundary_classify.py` 与 doctor 都是薄消费方，不留第二份校验 #92；账本数据在 `docs/ownership/atlas-boundaries.json`，引擎运行时永不解析——红线 #11 的机器形态）。`load_ledger` 返回 (账本, 问题清单)：缺失/坏字节（replace 回落 #112/#131）/坏 JSON/顶层非映射一律点名绝不裸崩；`validate_ledger` 钉 region 三值枚举（engine-owned/absorbed/private-knowledge）、paths 拒反斜杠/绝对/`..`（X1）、**accepted 无 decision_log=非法**（C17 债标价：说不清怎么消除的债不算 accepted）、risk 等级**不是枚举**——由账本 thresholds（30/250 是数据不写死代码）从 structural 行数推（gap6 §9 C5 注记）；跨区同特异度认领同一路径=账本非法点名（同区域内重叠无害）。`resolve_region` 最长非通配前缀/更特异 glob 优先（glob 分段 fnmatch：`*` 不跨段、`**` 跨段，X1 两台机同一答案）；`classify_change` 判序 style_paths 恒 cosmetic→跨文件搬移（A 删行集与 B 加行集归一化 Jaccard≥0.8，或 rename/copy 对）恒 structural→行数阈值；verdict ASCII（X4）：REVIEW-TOWER（absorbed/engine-owned 命中 structural）/AUTO/HINT（未登记，先补账）/DATA-REGION（私有知识漂移不是引擎边界问题）。`boundary_draft_args` 只造 kind=boundary_review 的 pending_review 参数——复用既有塔与 `_atomic_write`（红线 #9/#10），三元组位留空=天然 unverified、晋级走 C2 闸，且该 kind 在 `review.approve` 点名拒绝（trace_step/code_review 同族）；`ledger_disk_coverage`：optional 区域全缺只 warn 不并 rc（评审 C5-B）。X2：零 subprocess，git 命令由人跑 |
| `actor.py` | Actor、Ability 注入、`attempts_to`、ctx（产出写入统一走 `core.write_produces`：produces 声明与 perform 实写不符当场 KeyError 点名声明键与实际键——旧兜底 `key in result else result` 把谎报 Task 的**整个 dict** 静默塞进 ctx，下游把 dict 当单号，烂数据漂到远处才炸；engine 两条执行路径与 skip 步骤同一契约，skip 不写产出。#124 三面探针 sound：`using` 缺失点名类型名+保留 cause（#90 口径）；`can` 同类重注入 last-wins 无消费方触发（session.actor() 每类型恰一个），非可达缺陷不强改（红线 #12）；`attempts_to` 半途崩不回滚已完成产出——前序写 Task 真实发生过，抹产出=销毁追责证据（#85/#110 同族），补测钉死顺序执行/原异常传播/ctx 部分保留三契约，防将来改整体回滚静默抹证据） |
| `core.py` | Task 协议与强制元数据（`__post_init__` 落地 `tuple[str]` 契约：`produces=('value')` 漏尾逗号逐字符漂移当场构造期点名「带尾逗号」，非 tuple/空元素同拒；`write_produces` 产出写入唯一真相——声明键不在 perform 返回 dict 里立即 KeyError，谎报不再静默把整个 dict 塞进 ctx。CallEndpoint/DemoApiOperation/LegacyStepTask 改为自兑现 {save_as: 结果}（ctx 形态不变），封装类均 `tuple()` 转换 DSL list 安全。#125：`FunctionalTask` 的 action 非可调用（字符串/None）构造期当场 TypeError 点名类型+值+intent——旧实现一路放行到 `perform()` 才裸 `'str' object is not callable`，不提 Task 名归因漂成引擎缺陷（#119 面B/#91 同口径）；lambda/bound method 合法路径零涟漪。两级门面 `atlas`/`atlas.screenplay` 的 `__all__` 与真实符号逐项核对 sound（缺失=[]），防 `import *` 静默缺符号） |
| `abilities.py` | Playwright page、REST、Cookie（烘焙只认精确 host 与「可注册」父域：裸 TLD（com）与二级公共后缀（com.cn/co.uk 等精选表，#141 面A——旧判据 `"." in domain` 只挡前者，domain=com.cn 对 host=ics.example.com.cn 命中即凭据发向任意 *.com.cn 他注册域，违 §3 红线）均拒绝；域名比较/存储归一下写（#141 面B——真实 SSO 大写 `Domain=EXAMPLE.COM` 旧被大小写敏感静默丢弃，auth 失败只见 401 不见根因；无关大写域仍拒不是无脑放行）防跨站泄露）、DemoApi 适配（模块路径缺失/类名不存在均点名 `ATLAS_DEMO_API_PROJECT/_FILE/_CLASS` 与环境变量——旧实现裸 AttributeError「module has no attribute」用户不知改哪个键；spec_file 缺失是外部类 `_load_spec` 语义，诚实不越界。#122 面A：类存在但不符合客户端协议——`ATLAS_DEMO_API_CLASS` 手滑填成同模块 Service/页面类（如 IcsService 而非 IcsApi），旧实现到 `api._load_spec(...)` 才裸 `AttributeError 'no attribute _load_spec'`，不提 env 键冒充框架缺陷（#103 延伸：类存在≠可用；#63 注册≠可用同族）——现装配前 `callable(_load_spec)` 检查，ImportError 点名模块+类名+ATLAS_DEMO_API_CLASS，符合协议的类正常装配不翻错）、会话失效识别（#122 面B：auth_expired 旧判据只看 `Content-Type: text/html`，异常网关/mock 回 200+整页 HTML 不带该头时判 ok=True，登录页伪装成业务响应（#102 同族假绿）；现判据收敛「头或正文像 HTML」，正文判据只在 JSON 解析失败落 raw 时求值——首版无条件读 `.text` 让只带 json() 的 fake 崩出 AttributeError，收敛进 raw 分支既拦坏形态又零涟漪，Content-Type 原判据保留不误伤合法 JSON，#115 教训）。会话保活（尽力而为：回调抛错不顶掉已成功响应，返回 False 留后续重试；#141 面C：旧检测只认 auth_expired 标记——裸 401+JSON 网关应答（ICS 过期会话最常见形态）不带标记，宣称的「api 401 自动重登」永不触发、会话死亡不复活；现 401 状态码为权威判据，403/500/200 不误触发）。`request` body 按 requests 语义分流：`dict/list`→`json=`、`str/bytes`→`data=` 原样（JMeter form 串产物一律 `json=` 会被包成带引号字符串发出得假 400），其余类型发送前点名拒绝 |
| `composition.py` | `compose` 与 `compose_for_goal` 依赖编排（多生产者同产出键显式报错点名，不再 dict last-wins 静默选一个——遮蔽可行路径或把读目标翻成写操作；同名重复注册按同一产出方去重不误判冲突——但去重比对 frozen metadata **等值**（#136：旧只比 name，两份清单各定义同名不同定义（read_only True/False）的产出方时，写那份被静默丢弃=多生产者歧义从去重后门溜进来、agent 按幸存那份评风险，与 #128 contracts 同形态；现等值放行/不等值点名差异字段）；`compose` 本体对同一 Task 实例按身份去重——重复清单曾把写 Task 排两遍=副作用真跑两次而配额只记一次；等值不同实例保留，多生产者冲突仍归 for_goal 点名。#107 十三组对抗探针审计 sound：单 Task 自环/部分自环/goal 在环内均显式报「循环」、provided 可打断环、菱形共享上游只排一次、goal 已在 provided 返回空计划（agent no-op）——关键边界已有回归测钉死防漂移） |
| `config.py` | `.env`、环境键、生产防线、写操作校验（`load_dotenv` 按 BOM 字节判编码：UTF-8/UTF-8-BOM(记事本)/UTF-16(PowerShell 5 默认)都正常解析——不能「utf-8 试失败再试 utf-16」，UTF-16LE 纯 ASCII 内容恰是合法 UTF-8 会解出夹 `\x00` 垃圾键；BOM 旧实现静默把 `DEMO_USERNAME` 变 `\ufeffDEMO_USERNAME` 永不命中=「未配置凭据」查不到根因。解码坏/键名含空格=点名文件的 ValueError，绝不静默跳过；CLI 入口 `from_root` 接 `rc 2` 口径(#79/#80)，旧实现 PowerShell 写坏 .env 后所有 atlas 命令裸 traceback。#138：`check_legacy_paths`（模块级判据+消息单一真相）——旧项目根/web 子目录缺失点名 `ATLAS_LEGACY_PROJECT`/`ATLAS_LEGACY_WEB`，run 轨（runtime/CLI 预检）与 pytest 轨（`legacy.check_exec_dir`）三方委托共用一套消息，杜绝复制后改一漏一漂移（#92/#97）；通过路径零涟漪（#115））|
| `session.py` | 单浏览器、run/debug/heal、快照（取证为尽力而为：截图/求值异常降级返回 None，绝不把已通过步骤翻成失败；`__exit__` 逐个 try 关闭 context/browser/_pw——浏览器已崩时 context.close() 抛错不再跳过后续导致进程泄漏，且在用例异常传播中不掩埋原失败，仅在无在用例异常时才把清理错误抛出。#123：多清理错误不再互掩——三环节（context/浏览器/驱动）连环崩时旧末行 `raise errors[0]` 只报第一个，`_pw.stop` 失败=Playwright node 驱动进程正在泄漏却无从得知（#85 清理链无痕同红线，与 #121 面D 同构）；现单错原样抛（#82 口径不翻错）、多错聚合 ExceptionGroup 全点名，有用例异常仍让位真因。#111：`__enter__` 半启动失败同样不留孤儿——start() 成功后 launch/new_context/new_page 任一崩，`with` 语义不触发 `__exit__`，入口逐个尽力关闭驱动与半开浏览器再原样抛真因（DemoRuntime:70 在赋值 self.session 前抛、外层清理够不着；探针实锤每次 executable 指错泄漏一个 node 驱动进程）。`ApiEvidence.classify` 同口径尽力而为：坏重放 handler（抛错/非映射/None）跳过试下一条，绝不裸抛——在 engine except 块里抛会把原用例异常顶掉并清空 results/草稿/取证整条证据链，定性器不得先于被定性对象死） |
| `engine.py` | Task 执行策略、auto 通道路由、契约、heal、实证、CDP 冻场钩子（自愈成功后就地更正 recorder 同一条 step 为 healed+通过并补成功取证，运行记录与 results/flow 终态一致——否则 `atlas summary` 会把已自愈的 passed run 误计出 phantom 失败类；自愈成功归因用 `match_pitfall(status="approved")`，记驱动自愈的那条知识而非更长的 pending 草稿；失败路径仍无过滤，给人审看最长匹配。auto 降级链：`_perform` 只捕 `(NotImplementedError, RuntimeError)`——契约违规（AssertionError 系）与网络故障（OSError 系）照常传播不被降级掩盖；降级原因经游标并入 step record 落 `runs.jsonl`（旧实现只写 ctx 内部键 `_auto_degraded_to_ui` 零消费方，「API 坏→退 UI→ok=True」步骤事后无痕=假阴性，#106）。#114 三面：写护栏 `validate_write` 必须在 `recorder.step` **之内**——旧实现在 with 外执行，生产拦截写步骤时 `PermissionError` 于 step 进入前抛出，`runs.jsonl` 零痕迹、results 空（拦截无痕也是审计链断点，与 #85 同红线），现归 env_blocked（策略阻挡非代码缺陷，红线 #7）且 PermissionError 仍原样传播；控制流异常短路于失败路径起点——如实记 interrupted 进 results 后原样重抛，不走 heal/`evidence.classify`（防 stale 重放器误定性 KI）/坑点匹配/journal 草稿/session 取证（remedy 无从修复中断、Ctrl-C 不该被拍现场照，纯噪音），判据收敛 `failures.CONTROL_FLOW` 单一真相（#108/#92/#97 同源）；CDP 坏 `CDP_PAUSE_STEP`（`abc`/`TC9:`/`1.5`）旧实现 `int()` ValueError 被静默吞掉直接放行——调试者以为冻场等 attach，步骤跑过案发现场错过（#102/#105 假阴性同族），现点名坏值警告一行后照常放行（非交互零阻塞红线不变）。#139：三处 `session.capture()` 返回值经 `_record_shot` 并入 step 记录 `attachment`（root 相对、根外回落绝对）——旧实现全丢弃，取证截图落盘却 runs.jsonl/Allure 无痕（#106×#86 断链同族）；控制流短路分支保持不取证（Ctrl-C 不拍现场照语义不变）。C4②：`degraded_to_ui.reason` 旧实现是裸 `str(exc)[:200]`（绕过单一真相、净化进不来的死角），现走 `failures.exception_brief`——选 brief 而非 exception_message 是因为该字段既有取值形态就是裸消息体，加类型名会改 runs.jsonl 已落盘形态并翻存量断言（设计 C4 §4「形态不变」）。C2 协调补丁 1：失败路径 `journal.draft` 补传 `evidence.verification` 四元组（command/exit_code/assertions/verdict=fail）；**核盘结论**：recorder 的 op 字面值是 `task.metadata.channel.value`（ui/api/auto/hybrid），**不存在 `op=="assert"`**，照设计文本硬取恒 0——实盘口径改为统计 step 名以 `assert` 开头的已执行步骤（`dsl._run_asserts` 的 dict 形态走 engine.execute，字符串表达式不落 step），统计不到就如实 0（0 恰是 GREEN 反模式的可见信号） C9：失败路径在 CONTROL_FLOW 分支之后、**步骤级 flaky 重试块之前**并列新增 `isinstance(exc, AwaitingHuman)` elif——记 `StepResult(ok=False, FailureClass.WAITING.value)` 后原样重抛，不走 heal/`evidence.classify`/坑点匹配/journal 草稿/session 取证（remedy 无从修复「等人」，给等待现场拍照是纯噪音，与 #114 面 B 同型；差别一处：CONTROL_FLOW 是宿主终止，AwaitingHuman 是业务终态、由 CLI 转 rc3 且收尾照常走 `__exit__`）。位置是评审必改 C9-B 钉死的硬约束：落在重试块之后，reason 文案带「timeout」的 AwaitingHuman 会被 `waits.is_recoverable` 判可重试（classify→INTERACTION 那条腿按文本走），于是等人被就地重跑若干遍而人永远等不来；回归测 `test_engine_awaiting_human_never_retried`（step_retry>0 × perform 恰 1 次、不留 `retried` 键） **后续波次（2026-11-06）**：①`case_timeout` 标记从 CONTROL_FLOW 分支**提到失败路径顶部**——看门狗 fired 与本步抛错是两条独立事实（KI 是投递给主线程的，任务可以先自己抛），旧实现只在 KI 腿补标记，于是 waiting/失败一旦赢过 KI，超时证据被赢的那个终态静默吞掉；现标记与终态正交，三类终态各钉「两个事实同时在」+ 负向对照「没触发就不许冒标记」。②修一条会误导定位的过时注释（flaky 重试块位置曾写死行号，重排后指向的是 AwaitingHuman 分支本身）。C12 主路径真机取证见 design_c12 §10：真 socket 撞 502 → DeliveryUncertain、accept=1、UI 降级=0（不重发） |
| `recorder.py` | 零依赖 JSONL 运行记录（`default=str` 兜底不因异常字段丢整轮证据；`_closed` 仅在落盘后置位，写失败可重试不谎报。#126 两面：① `step()` 的 `classify_failure` 在 except 块里崩（业务异常 `__str__` 自身抛错——引用未初始化属性真实可达）时旧实现让新异常顶掉真因，且 fail_class 没赋上 → finally 判 `ok="fail_class" not in item` 把失败步骤记成 **ok=True 假绿**（定性器先于被定性对象死 #89 + 坏形态伪装成功 #102 同族）；现定性失败诚实回落 unknown、真因原样传播。② `close` 的 `default=str` 假设 str() 全函数可用——`__str__` 抛错时整轮落不了盘（违背 #64 初衷）、finally 顶用例真因、`_closed` 不置位重试永远崩；现永不抛兜底落 `<unrepresentable 类型>` 占位，坏字段有噪音但整轮证据必落盘）。C6：新增末位可选参 `version`——run 记录多一个 `version` 键（引擎版本/知识指纹/flow 摘要），**缺省 None 不写键**（存量 runs.jsonl 形态零涟漪 #115，旧测不翻）；versioning 不在 recorder 内 import，三元组由调用方（dsl.run）算好传入，零依赖纪律不破 C9：`close()` 末位新增可选参 `reason=""`——非空才写 run 记录顶层 `reason` 键（缺省不写=存量形态零涟漪，C6 `version` 参同款先例），把历史上 `allure_report` 读了却无人写的 `run.get("reason")` 那条线在 waiting 侧接上；`step()` 分类链**零改动**，`fail_class="waiting"` 由 `classify_failure` 单点继承自动产出（定性崩回落 unknown 的 #126 防线天然覆盖），源码内除该参数名外不含任何 "waiting" 字面量（AST 扫描测钉死） |
| `delivery.py` | C12 投递判据**单一真相**（一道闸一个小模块，对齐 credential_gate/versioning/boundaries 先例，#92）：`safe_to_auto_reexecute(read_only, attempted, exc)` 是「同一操作被自动第二次执行是否安全」的唯一判据——控制流委托 `failures.CONTROL_FLOW` 单点（不留第四份 tuple，#108/#114）、只读恒安全（现状降级/heal 语义逐字不变 #115）、写任务仅在**可证未发出**时安全；唯一消费方=engine 的 auto 降级闸（healing 不改用、不新增重试通道）。CallApi 在场且 `request_seq` 变动才可证「已发出」；自定义 `_perform_api` 腿走插件自有传输（demo_api 类，seq 恒不动 ≠ 未发出）→ 对未声明的写任务按已发出 **fail-closed**，读语义 API 腿要放行须 Task **显式声明** `api_leg_side_effect_free=True`（能力声明归插件，红线 #9/#11，引擎不猜）。`idempotency_token(entity, where, scope)`：sha1 截 **16 hex**——刻意 < 净化刀 `looks_literal_secret` 的 ≥32-hex 判据，关联把手必须活着落盘（对抗样本进测）；`canonical_json` 键序无关/UTF-8/无空格，非 str 键与坏序列化形态点名 ValueError 不猜，且在 supply/creators 侧**让位** #61 的键路径点名（C12-F 撞序裁决：idem 留空，原报错口径不变）。`IDEM_WHERE_KEY="atlas_idem"` 是机制键名唯一出处（红线 #11）；纯 stdlib(hashlib/json)、零新依赖、不引重试库/幂等框架 |
| `legacy.py` | 原项目 pytest 和 `step_N` 双轨兼容（`check_exec_dir` 预检旧项目根/web 子目录：缺失点名对应 `ATLAS_LEGACY_PROJECT`/`ATLAS_LEGACY_WEB` 键，不再让 subprocess 抛 OS 级 `NotADirectoryError` 冒充框架缺陷；#138：判据与消息委托 `config.check_legacy_paths` 单一真相——run 轨（runtime/CLI 预检）与 pytest 轨共用一套消息，杜绝两处复制后改一漏一漂移（#92/#97），通过路径逐字等价零涟漪（#115）；`--dry-run` 文档语义「只检查路径」现为真——旧实现空目录照样 rc0 打印命令=假绿预检。#119：`from_module` 探测零命中且模块 import lib 时，裸 `ModuleNotFoundError: No module named 'lib'` 补归因重抛（点名 ATLAS_LEGACY_WEB 与探测起点；探测命中却缺 lib=真缺、坏别的模块原样传播不揽责——#115 教训：不 import lib 的纯本地模块任意目录仍合法，不在加载前拒绝）；step_N 非可调用（`step_3="字符串"`）构造期当场 TypeError 点名实际类型，不再假成功推迟到 perform） |
| `dsl.py` | 线性 YAML DSL、变量插值、assert 块、数据供给（Ctrl-C/SystemExit 记 `interrupted` 而非 `failed`，与 `env_blocked` 同属操作/环境终态，不污染 pass/fail。#129：flow 终态判据曾内联硬编码 `(KeyboardInterrupt, SystemExit)`——`CONTROL_FLOW`（#114）的第 5 处漏网副本，生成器关闭（GeneratorExit）被记 `status=failed` 污染缺陷分布，现收敛 `failures.CONTROL_FLOW` 单一真相，agent 同一缺陷一并修。`validate_flow` 是结构校验单一来源：步骤非映射点名序号与形态、未注册 Task、assert 块、requires 供给块（策略坏点名是哪个键——from_dict 报错不带键名，多条件 flow 人回头猜）、flow 级 on_env_blocked——`atlas compile` 与 `atlas run` 登录前共用，registry 与 `DemoRuntime.registry()` 同源，compile 绿=run 不再登录+开浏览器后才结构裸崩。#113：assert 表达式操作数分形态——路径形态（`word.word`）解析失败点名不静默回落字符串（打错的路径曾被当互不相等字面量恒真假绿）；`true/false/none/null` 识别为 bool/None 字面量（`ok == true` 不再假阴性拒绝正确断言），显式带引号仍按字符串；requires 两条落点同键（同 `as`）执行前与 validate_flow 同口径点名，不静默 last-wins 覆盖（#84 多生产者同红线）。**吸收自 LiteFlow 的三条编译纪律**：① **候选校验→原子应用**——`compile()` 先做零构造的静态校验（形态/未注册 Task/channel/on_env_blocked/步骤 id 唯一/dispatch 目标与 default/模板槽位/assert 块），全过之后才构造 Task 列表，再做一次依赖元数据的语义校验（步骤级 `requires`、`dispatch.on` 必须引用**已声明**的 ctx 键：flow requires 落点 ∪ 上游 produces ∪ save_as），任一不过整体拒绝、缓存里也不留半成品；`run()` 把校验挪到 `provision` **之前**（旧实现先供给数据再 compile——`requires` 会占建单配额甚至真实建单，然后才因第 3 步 Task 名写错而炸，同红线 #14）；多处问题一次报全（首条带定位 + 其余列清单）。② **md5 编译缓存**——`load()` 返回 `FlowDocument`（dict 子类，带源文本/md5/节点定位表），缓存键 = 源文本 md5 + **当前 steps 结构指纹** + registry 代次，三者都没变才复用已编译 Task 列表（只认 md5 不够：flow 是可变 dict，load 后被脚本改一步就会拿旧结果盖新内容——改掉的 `dispatch.on` 静默不生效，比慢更糟；`overlay()` 派生副本保留形态，CLI run 用 `{**flow}` 解包会把形态丢掉）；查询排在静态校验之后、语义校验每次都跑；只存内存不落盘；内存构造的 flow 无源文本 → 不进缓存、不给行号（不假造）。③ **^ 指针报错**——未定义引用类（Task 名、步骤 id、`requires`/`dispatch.on` 落点、`{{slot}}` 槽位）报错带 `--> 文件:行:列` + 源行 + `^`（`yaml.compose` 节点标记），引用类异常是 `FlowReferenceError`（同时继承 ValueError 与 KeyError 以接住既有捕获口径，`__str__` 必须覆盖否则 KeyError 的 repr 会把多行诊断转义成乱码）。新增 DSL 键：`id`（步骤命名，重复即拒）、`dispatch: {on, to:{取值: 步骤id|[ids], default: 必选}}`（路由，目标须在本步之后且只被一个路由认领，未选中分支整步不执行不进 runs.jsonl，取值运行时缺失按 `EnvBlocked` 终止不静默走 default，决策留 `ctx["_dispatch_routes"]` 并并入路由节点的 runs.jsonl step 记录 `dispatch` 键（engine `_flush_routes` 游标搬运，#106 同族补网）；`on_env_blocked` 语义不变，dispatch 是其推荐升级；**删 lane 必须同时删/挪走那一步**——路由只过滤「被 `to`/`default` 认领」的步骤，无主步骤恒执行且编译零警告（E4e 实测暴露的作者陷阱，写操作时静默放大执行面）；不在编译期拒绝是有意的取舍（判「这步本该属于本路由」要猜分支边界，启发式会误报合法落空步骤，违「不猜」红线），收紧的预留方案是显式 `dispatch.exclusive`，等有真实多 lane flow 再上（红线 #12 不给零使用的键加投机功能），现行语义由 `tests/test_dsl_template_dispatch.py::test_unclaimed_branch_step_runs_unconditionally` 钉桩）、`template`+`slots`（骨架文本级 `{{slot}}` 实例化，槽位值限单行标量以免行号漂移，**没填完的骨架不产出可执行 flow**——把 `{{customer}}` 当客户号发出去就是假数据假通过；实例文件只准带 template/slots，YAML 1.1 把裸 `on:` 解析成布尔键已归一，裸布尔的分支取值键直接拒）。包装器链（channel/guard/router）的 `${...}` 插值改为逐层透传：旧实现把真实 Task 关在 `FunctionalTask` 闭包里，插值按属性遍历碰不到——「写了 channel 的步骤 + 参数含 ${var}」参数原样字面量进 perform（静默不生效）；非引擎路径同族漏防：`attempts_to` 对被 skip 的步骤仍写产出（None 塞进声明键），现按引擎口径「skip 不写产出」撤回）。C6：`run()` 建 RunRecorder 时传入 `versioning.version_triple(...)`——三元组在**唯一创建点**接线（flow 是当前参数：文件加载的 `FlowDocument` 出 `kind=source` 用现成 md5，内存 dict 出 `kind=derived`），CLI run 的 `overlay()` 保形态所以仍报 source，`{**flow}` 解包会悄悄降级成 derived（该防复发点由 `tests/test_versioning.py` 钉住） C9：`run()` 加 `except AwaitingHuman` 腿（置于 CONTROL_FLOW 之后、EnvBlocked 之前）→ `status=FailureClass.WAITING.value`（取值走枚举不散写字面量）+ run 级 `reason=exception_brief(exc)[:200]`（净化刀 C4②）后原样重抛；finally 改 `recorder.close(status, reason=run_reason)`——**只有 waiting 分支的 reason 非空**，其余终态传空串即不写键（env_blocked/failed/passed/interrupted 行逐字节=旧形态 #115）。有意**不提供**「跳过等人继续跑」的 flow 键：那等于静默绿通道（红线 #7 反面），waiting 永远 stop；`on_env_blocked: skip` 语义不动。落库线（2026-11-06）：flow 级 `harvest: true`（编译期只收布尔，`harvest: "yes"` 即拒）+ `_harvest_passed_flow`——`run()` finally 在 `recorder.close` **之后**、仅 `status=="passed"` 时把动作序列原文（steps/assert，`${var}` 占位不改写）+ `engine._verification(True)` 成功三元组 + run_id/env 固化为 `flow_candidate` 草稿（`journal.draft_flow`，id=sha1("harvest:flow:"+flow摘要)→同 flow 重跑只增 seen 刷新证据、flow 改了才新行）；失败/中断/env_blocked/waiting 一律不固化，固化写盘失败只 WARN 绝不翻 run 终态，无 journal 如实跳过（红线 #9/#10 只进塔不写知识） **后续波次（2026-11-06）**：`EnvBlocked` 腿接上 run 级 reason 写侧（原「不做本波」放行）——形态与 waiting 腿逐字同构（`exception_brief` 净化刀+截 200，接通时最容易忘净化：根因文案常带被掐断的回调 URL，不脱敏等于把凭据写进一个新落盘字段），`on_env_blocked: skip` 腿同样写（跳过型恰是批量巡检里最需要知道为什么被跳的一类）。动机：几十条同名 `env_blocked` 行分不清「缺价格表」还是「缺字典值」，此前根因只活在 `ctx["_env_blocked"]` 里跑完即丢。非空 reason 的终态自此是 waiting+env_blocked 两条，passed/failed/interrupted/timeout 仍不写键 **gap9b 声明位**：flow step 新键 `page_object`（已批准组件名）/ `legacy_pages`（旧套件函数名）——**形态判据落 compile 静态段（`_check_static`，validate_flow 委托它故 compile/run/export-pytest 自动同尺，不留第二把校验 #92）**：非空标量、两键互斥（同带点名序号拒——合一单键再按值分支=引擎猜语义 #11）、点分形态拒并解释「模块位由产物常量 `LEGACY_PAGES_MODULE=lib.pages` 给出」；坏形态整体拒绝、一个 Task 都不构造（原子应用纪律）。语义分层：compile 管**形态**、生成期闸管**成员资格**（`_pascal(声明值)` ∈ 实际类名集——components 只在生成期存在，dsl 拿不到，天然两层），引擎执行路径不解释这两个键（产物专用，读不残缺） |
| `probes.py` | find/create 策略、环境与配额防线、回收清单（读写回收清单逐行容错：残行/非对象跳过不顶掉整表；配额在 create **前**占用；`from_dict` 先验形态——raw 是字符串曾被逐字符误报「未知字段」、null 裸崩 TypeError、缺 entity 裸 KeyError，现点名「必须是映射/缺少 entity」与 #77 同口径；裸 creator 返回非 dict 在建单后、remark 检查前就 TypeError 点名实际类型，坏形态不进单号补记。#117：`find_document_number` 单号判据单一真相（非空标量才算；`{"orderNo": ""}`/None/bool/容器不算），`supply` 补单号闸门——生产主路径 find_or_create 旧只查备注不查单号，建了单没回单号静默成功、回收清单无单号 `atlas recycle` 显示 '?' 永远找不回真实单据（#86/#102 同族）；建单已发生记录保留、报错给原值供人工追回，与备注缺失同策略。#132：`save_for_recycle` 读旧清单补字节面防线——中断腰斩尾行时 `read_text` 裸崩，**读崩=合并写回不执行，本次新造的单号随崩溃一起丢**（单据已真实产生却再无人知道单号，#86 回收闭环被一条历史坏字节掏空、红线 3「保留可回收信息」失效；#112 读侧同防线漏网第 5 处）） |
| `failures.py` | 失败分类与 API 实证（`api_evidence` 对 `data` 形态零假设：ICS 列表接口 data 常为数组、网关错误常为字符串、失败常为 null——旧实现对非 dict 直接 `.get` 让定性器裸崩，现场证据全丢；data=null 时定性文本回落 error，不再被字符串 `"None"` 挤掉真实根因。`status` 同纪律归一化：HAR/JMX 录制常存字符串 `"500"`，`"500">=500` 裸崩（`prove_api` 无 try 顶掉原异常）、`"401" in {401,403}` 恒假漏判会话——`_status_code` 把数字串转 int、bool/非数值归 None 走文本定性回落，不猜不崩。#114：`CONTROL_FLOW=(KeyboardInterrupt,SystemExit,GeneratorExit)` 单一真相（engine/healing 直连判据，recorder 经 `classify_failure` 单点继承，杜绝改一漏一漂移，#92/#97 同红线）；`FailureClass.INTERRUPTED` 单列终止信号——`classify_failure` 先于关键词短路，KI 不再落 unknown 进 summary 缺陷分布、类型名恰好含 "timeout" 等子串也不误判；ENV_BLOCKED 词组追加「生产环境禁止」——写护栏拒绝是环境策略阻挡（红线 #7）归 env_blocked，伪装成待诊断缺陷也是假阴性同族 #102/#105。#127：`exception_text(exc)` 是异常定性文本的单一真相（`f"{类型名} {exc}"` 此前 classify_failure 与 knowledge.match_pitfall 各写一份，#92 改一漏一）——业务异常 `__str__` 自身崩（引用未初始化属性，#126 面A 同形态）时 f-string 在 except 块/自愈准入路径当场炸顶掉原用例异常（定性器先于被定性对象死 #89）；现坏 `__str__` 回落类型名继续定性，绝不裸抛。#129：展示形态也收敛——`exception_message(exc)`（`"{类型名}: {消息}"`，留痕/reason 用）与 `exception_text` 同源 `_exception_display`（安全提取消息体，坏 `__str__` 回落空串），此前 agent 手拼第三份 f-string 副本在 except 块炸掉真因+留痕（#89+#85）；消息含 `": "` 时定性文本不丢冒号（派生 `replace(": ")` 版本被否——#115 归因修复不得把正常路径翻成错误）。Gap5 P2：`classify_text(text)` 抽取为文本分类单一真相——classify_failure 委托它（控制流短路保留），Allure 解析共用同一套关键词匹配（防 #92 漂移）。C4② 净化落点（评审必改 C4-B）：`exception_text`/`exception_message`/新增 `exception_brief` 三个**落盘形态**都经 `credential_gate.sanitize_text` 派生自同一个 `_exception_display`（一条刀覆盖 journal 草稿 evidence.text、agent 留痕 reason、engine degraded reason 三条路径），而 **`classify_failure` 改读新增的 `_classify_input`（净化前原文）**——顺序反了会把 `?code=401`/`token=…timeout…` 这类「关键词只在敏感值里」的样本从 SESSION/INTERACTION 翻成 unknown，脱敏损归因比泄露更隐蔽；对抗样本与 10 条 benign 等值表在 `tests/test_failures_sanitize.py` 钉死）  C9「waiting≠failed」显态（design_c9）：枚举追加第 9 类 `WAITING="waiting"`（既有 8 类的值与序零变动），并给出**操作/环境终态家族的唯一判据** `OPERATIONAL={env_blocked,interrupted,waiting}` + `is_operational()`（非 str/未知值恒 False 不猜；FailureClass 成员先取 `.value`——str 混入枚举的 hash 口径 3.11 按名、3.12+ 按值，裸 membership 在部分版本恒 False 会把「等人」读成缺陷）；新异常 `AwaitingHuman` 强制非空原因（裸一条 waiting 落盘=新型静默 #70/#86，必须点名在等什么、需要谁）。`classify_failure` 短路次序钉死：None→CONTROL_FLOW→**AwaitingHuman**→（C12 的 DeliveryUncertain 位）→classify_text——**只看类型**，`_RULES`/`_DATA_ABSENT` 一字不加，所以外部文本里的 "waiting/等待人工/approval required" 永不产出 waiting（把真失败误标成等人=红线 #7 的洗绿入口，本波焊死）。消费点（allure 桶、`run_attention`、调度 `waiting=keep`）一律 import 判据，不留第二份成员清单（#92） **后续波次**：`NEVER_AUTO_RETRY=(AwaitingHuman, DeliveryUncertain)` 类型级永不重试名单单点（C12-E 放行，判据消费方只有 `waits.is_recoverable` 一处）+ `FLOW_STATUS_TIMEOUT="timeout"` flow 级终态字面量单点（生产方 cli 收尾腿与消费方 reporting 计数同值）；DU 的 classify 归因面**不变**（仍 INTERACTION、仍保持 failed）——类型闸只收紧重试面，不动归因面 |
| `locators.py` | UI 定位描述**单一解析点**（gap13）：组件库 `selectors` 两形态——CSS 字符串（存量，逐字节兼容）或结构化描述 `{"role":..,"name":..}`/`{"label":..}`/`{"testid":..}`。`parse`（形态判据，坏形态/未知键/多定位键/空值一律点名 `LocatorSpecError`）、`normalize`（落盘用户形态：CSS 恒为字符串、结构化不带内部 `kind` 标签——存 IR 就等于存一份**自己解析不了**的知识）、`canonical`（留痕文本；旧写法 `selector[:120]` 收到 dict 直接 TypeError，批准会崩在留痕上）、`resolve`（唯一 `→locator`：role/label/testid 走 `get_by_*`，CSS 走 `page.locator`；role 的 name **精确匹配**——候选 name 就是无障碍名原文，子串匹配会点错「保存并提交」，「点错」比「找不到」更坏）、`from_text`（CLI/模板文本边界：`{` 开头必须是合法 JSON，坏花括号不许被当 CSS 字面量静默入库）。消费方：`knowledge.SelectorRegistry.resolve/describe`、`save_component_selector`（落盘前校验、零半状态）、`review.approve_to_selector`（`--remedy` 支持 JSON 对象 / `--role/--name` 糖，同一判据）、`autopilot._locator`、`emit_pytest` 页对象——**不留第二把尺**（#92）。ARIA 优先由组件库**人审内容**决定，引擎与生成器都不猜；框架结构类（`.el-table`/`.el-loading-mask`）仍在 `waits.py` 用 CSS（判据对象不同，原因写在模块 docstring）。产物侧例外：生成的 pytest 包**零 import atlas**（gap9 硬约束），故生成期把规范化 IR 固化进页对象并在产物内联一层同构分支——分支集合由 `KINDS` 派生，形态真源仍只有一处 |
| `knowledge.py` | 知识命中、选择器注册表、人审写回（`load` 逐文件回退：私有目录文件写坏/空/坏字节 → 降级到包内默认而非抛错顶掉整个子系统；`match_pitfall(status=)` 过滤在「最长 trigger 择一」**之前**——自愈准入选 approved 集，pending 草稿不得靠更长 trigger 遮蔽同异常的 approved 坑点；诊断调用不传 status 保持最长最具体原义。#127 六面：私有库是**人可手改**的，旧闸门只拦坏字节，放过「合法 JSON 但形态不对」全部穿透消费点崩——顶层数组/字符串 `.get` AttributeError（现视同损坏回退下一来源）、坑点行 `scope:null`/`trigger:null`（键在值 null 默认不生效 #77）坏行逐条跳过（#65 同纪律）、`components`/`overlays` 键值坏让批准半路崩塔清不动（#73 场景；现坏形态当空重建、copy-on-write 不丢回退好数据）、`SelectorRegistry.get` 对 `selectors:null` 裸 TypeError 逃逸友好归因（坏条目当未注册统一拒，#63 存在≠可用延伸）、`match_pitfall` 的 f-string 遇异常 `__str__` 崩——#126 只守 recorder 调用点、engine/healing 直调漏防（改一漏一 #92），收敛 `failures.exception_text` 单一真相，坏 `__str__` 回落类型名（类型名本就是 draft_pitfall 的 trigger 形态）；兼容面 #115 全钉：好行照常命中不翻错。#135 红线 #11 代码化：`root=None`=只读库类型契约（未设 `ATLAS_KNOWLEDGE_DIR` 时 `DemoPlugin.knowledge()` 返回它——旧实现拿包内目录当写根，review --approve 一批准就把业务坑点写进 git 资产，探针实锤包内 pitfalls.json 30→31；判据不靠路径包含启发式——Windows resolve/符号链接可绕过且「显式构造」与「忘设 env」无法从路径区分）；`_writable_root()` 闸门在 save_reviewed/save_component_selector/save_overlay 三条唯一写路径**写盘前**抛 PermissionError 点名 ATLAS_KNOWLEDGE_DIR（批准先写知识后改队列，闸门最前置=零半状态）；读侧逐文件回退包内虚构默认照常「读不残缺」）。Gap5 P2：`match_pitfall_from_text(text, domain, status)` 抽取为文本匹配单一真相——match_pitfall 委托它，Allure 分析共用。落库线（2026-11-06）：新增 `save_task_entry`（`flow_candidate` 的唯一入库路径）——私有根 `task_library.json` 存跨流程可复用动作序列（{"tasks":[...]}），按 name 唯一、同名再批准=更新（overwritten 显式回传，save_component_selector 的 element 语义同款）；steps 非空映射列表才收（空序列入库=假资产点名拒绝，#102 同红线）；tasks 键值坏形态当空库重建（#127 面4/面5 同族）；写闸门与 save_reviewed 同源 `_writable_root`（红线 #11） |
| `contracts.py` | 契约三档：core/high/minimal（required 形态在 `__post_init__` 构造期钉死——类型值写成数组/对象、路径键非字符串时当场 ValueError 点名；旧实现留到 validate() 才 unhashable TypeError/AttributeError，定性器先于被诊断报文死，与 #89/#94 同红线。`load` 把「JSON 合法但形态不对」的契约文件（顶层数组、条目非映射、拼错键）转可定位 ValueError 点名文件与条目序号，接进 `DemoPlugin` 的损坏回退——手改坏私有契约不再穿捕获范围顶掉整条断言链。#128：同 `(method,path)` 两条**不同定义**契约不再 dict last-wins 静默择一（core 被后写的 high 遮蔽=强校验冒充覆盖率却永不生效，`--validate` 自述红线 #7/#12、与 #84 多生产者同键同红线）——`_put` 按 dataclass `__eq__` 区分：等值重复放行（`toolbox.learn_contracts` 轮询去重依赖覆盖语义 #102，拒绝=把正常路径翻成错误 #115），不等值显式 ValueError 点名两条名字+等级（load 同路径，私有冲突文件回退包内非静默用弱版）|
| `healing.py` | 知识驱动自愈（只读+已审核知识；准入用 `match_pitfall(status="approved")` **在择一前过滤**，trigger 更长的 pending 草稿不再遮蔽同异常的 approved 坑点——否则正当自愈被静默压制。#108：控制流异常（KeyboardInterrupt/SystemExit/GeneratorExit）一律拒绝自愈——终止信号不是业务失败，remedy 无从「修复」它们；探针实锤 approved 的 KI trigger 行曾放行 Ctrl-C 就地重试（engine `except BaseException` 本就捕 KI，旧实现只靠 trigger 恰不命中侥幸挡住，零防线），违反 #70 interrupted 终态口径）。C2 提案层：`HealingProposal` 加可选 `evidence` 字段（缺省 None，旧 4 参构造零涟漪 #115），`__post_init__` **复用 `review.verification_problems`** 做构造期形态校验——坏形态当场 ValueError 点名缺失键，evidence 本体非映射同拒（#77）；healing→review 单向 import 不成环。`propose/can_auto_heal` 行为零改：三元组门控的是「晋级」，不新增「执行」——自愈仍只认 approved 知识 + 只读 Task（红线 #9/#10），控制流拒绝（#108）不动 |
| `state_machine.py` | 复杂单据按需状态机、守卫与确定性迁移（声明但未注册的守卫显式报错，绝不静默判假走 fallback 分支；`from_dict` 校验 states 必须是映射、迁移必须是列表，畸形 YAML 给可定位 ValueError 不再 AttributeError/TypeError 裸崩。#116：`terminal:` 键在值 null 不再 `set(None)` 裸 TypeError（默认值只在键缺失时生效，#77 同红线；cli.machine 只捕 ValueError/YAMLError，TypeError 漏网整条命令 traceback 崩违反 #79/#80）——null 视同未填回落自动推导，非列表点名；迁移 to/task 必须非空标量——旧 `str()` 把 null 兜成字面量 `'None'` 成「合法任务名」，`atlas machine --target` 输出 `["None"]` 假绿（坏形态伪装合法结果 #113 同红线）；guard 显式 null 回落「无守卫」（不再是假守卫 `'None'`），复合形态点名；守卫注册了但值不可调用点名守卫名与类型，不再裸 TypeError「not callable」无归因（#63 延伸：注册≠可用） |
| `agent.py` | 规划、风险分级、审批、决策留痕（半途失败/Ctrl-C 也落痕：`executed` 完成清单+止步 Task，写失败记「执行失败」、中断记「interrupted」与 DSL 终态口径一致；审批门已放行的写 Task 追责不断链；#110：留痕落盘尽力而为收敛三处调用点——#85 只护失败路径，成功路径（写副作用已发生）与审批门 raise 前裸崩曾把 Decision 返回/rc3 决策信号顶成 FileExistsError；成功路径同落 `executed`，「跑完全部」与「一个没跑」的行不再同形。#129 两面：① 中断判据收敛 `failures.CONTROL_FLOW` 单一真相——旧执行循环内联硬编码 `(KeyboardInterrupt, SystemExit)` 漏 GeneratorExit（#114 声明 engine/healing/recorder 同源的单一真相，agent 是漏改第 4 处，#92 改一漏一），生成器关闭被记「执行失败」与 `classify_failure(GeneratorExit)=interrupted` 自相矛盾；② 留痕器先于被定性对象死——reason 手拼 f-string `{exc}`（#127 只收敛定性侧，展示侧第三份副本），业务异常 `__str__` 自身崩（#126 面A 真实可达）时在 except 块炸：探针实锤真因被顶掉 + 整条留痕随赋值丢（连内存 decisions 都没 append，已执行写 Task 副作用无痕 #85）；现收敛 `failures.exception_message` 单一真相，坏 `__str__` 回落类型名。 **后续波次**：同一把泛捕获曾把 `AwaitingHuman` 与 `DeliveryUncertain` 都记成「执行失败」（文档此前只声明了前者，实查两类都吞）——现分流四类 outcome：interrupted / 等待人工 / 投递不确定（写明需人工核实，避免操作者直接重放整段计划=写双跑）/ 执行失败；outcome 只进 reason 文案，Decision 无该字段故不构成 schema 变更 |dsl.py flow 终态判据同款硬编码（第 5 处漏网，status=failed 污染缺陷分布）一并收敛） |
| `creators.py` | 已验证建单脚本接入与「非人工创建」护栏（#117：单号判据收敛 `probes.find_document_number` 单一真相——`require_audit_tag` 旧 `key in result` 把 `{"orderNo": ""}`/`{"orderNo": None}` 也算「有单号」放行（键在即算 #102 同族），且与 supply 两份判据各写各的（#92/#97 改一漏一）；非空标量才算单号，bool→"True" 假单号与容器形态拒绝、数字单号接受。`make_scripted_creator` 的 where 含不可 JSON 序列化值（YAML 日期对象）不再裸 TypeError——点名脚本+整条键路径（嵌套下钻 `where.createdAfter`/`dates.0`，#61 口径）+类型+转换建议） |
| `experience.py` | 失败草稿（确定性 id+去重计数）、人审提升正式知识（#112：队列尾行被中断腰斩出非法 UTF-8 时读侧 replace 回落——好草稿不再被一条坏字节整队拖死，engine 失败路径的登记器不再在 except 块里顶掉原用例异常（#100/#109 红线）；追加前检测尾字节非换行先补断行——裸 append 黏在无尾换行残行后会把整条合法新草稿合并成「残行」被读侧静默跳掉：登记成功、无异常、草稿永久不可见（#102 假成功同族）。#118：同 id 命中增 seen 时同步刷新最新 evidence——bu_overlay 坐标文件修正后 id 不变证据变（id 按「键+两侧BU」定），旧只计数不刷证据，审核塔停过期值、人审基于旧证据批准错误坐标（#102/#105 同族）；缺省 evidence 键不抹已有证据（engine 失败路径登记不带 evidence，零漂移）。#130 面C：`_read_queue` 与 review.rows 同防线补形态面——合法 JSON 非映射行（`[1,2]`/`null`/字符串）逐行跳过不整队崩；该登记器在 engine 失败路径的 except 块里，崩了顶用例异常（#100））。Gap5 P2：`draft_from_text(*, task, text, failure_class, ...)` 抽取为文本造草稿单一真相——draft 委托它，Allure 分析共用；text 存进 evidence 供人审核阅。C2：草稿的 `evidence.verification` 三元组走**既有 evidence 通道**——`draft`/`draft_from_text` 签名零改动（不为此新开第二个入参，也不改 experience 的写路径），#118「同 id 再登记刷新 evidence」的语义天然继承：三元组随证据一起更新，不会停在旧值（engine 侧的自动三元组由 impl_report 的协调补丁 1 在 engine.py 传入）。落库线（2026-11-06）：新增 `draft_flow(*, case, digest, evidence)`——跑绿的 flow → `flow_candidate` pending_review 草稿（failure_class="success" 是草稿侧标签，explore 的 "exploration" 同款先例）；identity="harvest:flow:<flow摘要>"，同 flow 重跑复用 append_draft 幂等只增 seen |
| `review.py` | 审核吞吐入口：队列/证据卡片/批准(按 kind 分发)/驳回/整类批量/模板/统计/决策留痕（`resolve` 拒绝空/空白引用——空串非通配，否则唯一 pending 时空模板行会被静默批准。#109：`apply` 的「合法 JSON 但非映射」决策行点名拒绝不顶整批；`approve_to_pitfall` 空 `exception_type` 回落非空 trigger——键存在值为空串时旧 `.get(key, default)` 不兜底，批准的坑点 trigger 为空被 #59 永久拒匹配=死资产假成功；#112：`rows()` 与 `_read_queue` 同防线——坏 UTF-8 字节 replace 回落读入，残行自然跳过，好草稿不再整队不可见。#130 四面：①`_decide` 决策留痕尽力而为——批准顺序「知识写回→队列改写→留痕」中留痕 OSError 不再把已达成且不可重放的业务事实顶成 traceback（#110 agent._record 同纪律）；②pending 判据收敛 `_is_pending` 单一真相——列表/`resolve(dict)` 的 `==PENDING` 与 stats/reject_kind 的 `get(status,PENDING)` 三套并存时 legacy 无 status 行「列表看不见、stats 报有、批量驳回能驳」跨入口矛盾（#92 改一漏一），显式 approved/rejected 的重放防线不松动；③`rows()` 补形态面——合法 JSON 非映射行跳过不整塔崩；④`approve_to_overlay` 的 `evidence` 键在值 null 用 `or {}` 回落（#77，证据卡片面早已防、批准路径漏防），坏形态走友好 ValueError 不再 AttributeError。Gap4：`code_review` kind 不可 `--approve`（点名「不自动写知识」）——review-code 的 finding 进塔后人审仍走既有流程，不新增知识写路径）。C2 证据式验收三元组（design_c2，红线 #7 的硬化形态）：模块级 `verification_problems()` 是**唯一判据**——纯函数，evidence 为 None/list/str 等坏形态给「问题清单」而绝不抛（#77/#130 家族：判据函数不得先于被判定对象死），四键 command/exit_code/assertions/verdict，`verdict="fail"` 合法（坑点草稿证明的是「失败真实复现过」，闸不要求必须绿——否则就是逼人造绿）；`_verification_of()` 收敛 evidence 坏形态提取，`_is_verified`/闸/证据卡三处共用（#92 不留第二份判据）。**唯一强制点**是 `approve()` 入口：三个可批准 kind（pitfall/selector_candidate/bu_overlay）缺三元组 → ValueError 带 `--allow-unverified` 指引（不可批准 ≠ 删除：照常列出、可 reject）；人可显式 override 但**必须带 reason**（空即拒），放行时双留痕——决策行 `unverified_override`+`override_reason` 与草稿行 `approved_despite_unverified`，stderr 警示行 ASCII 前缀 `UNVERIFIED-override:`（X4）；trace_step/code_review 维持原「不可批准」拒绝，闸不加第二扇门。`evidence_card` 行首 `[VERIFIED]/[UNVERIFIED]` + 缺失清单；坐标对比箭头旧 `↔`（U+2194）不可 cp936 编码——它是**返回串**，print 字面量哨兵扫不到，已改 ASCII `<->`，防线是 `test_c2_verification.py` 直接断言整卡可 cp936。批量 `--apply` **有意无 override 通道**（决策模板里没这个位）：override 必须是单条显式决定，批量放行 unverified 等于没闸（红线 #13：机器永不自动放行）。gap13：`approve_to_selector(selector=…)` 收 CSS 字符串或结构化描述（形态判据在 `locators`），留痕改走 `locators.canonical`（旧 `selector[:120]` 遇 dict 直接 TypeError——批准已落库却崩在留痕=写回无痕）；`template_rows` 的 selector_candidate 行带 `role`/`name`（**用候选原值预填**），`apply` 里 `selector` 显式优先、缺省才用 `role`/`name` 组装（人写 CSS 就是显式选择，不被预填盖掉）。落库线（2026-11-06）：`flow_candidate` 入 `GATED_KINDS` 成第四个可批准 kind——`approve_to_task_library` 走 `KnowledgeBase.save_task_entry` 落私有 `task_library.json`（按名唯一、同名再批准=更新回传 overwritten；`--remedy` 可选=库名，缺省回落草稿 case 名；决策留痕 action=approve_flow；镜像纪律只翻 pending 行）；evidence_card 加 flow_candidate 分支（cp936 安全断言在 test_harvest_and_task_library 钉）、grouped_view 批量类与 CLI `--kind`/`--reject-kind` choices 同步扩 |
| `review_code.py` | Gap4 代码质量评审：`review_code()` 主入口——扫描 pytest(AST)/DSL flow YAML/契约 JSON，5 维度权重计分（断言 .25/安全 .20/可维护 .20/覆盖 .20/重复 .15），critical 硬顶 ≤59。31 条反模式规则库（ASSERT-*/GREEN-*/SAFE-*/MAINT-*/COV-*/DUP-*），红线 #7 专项侦测「为变绿放宽断言」三种互补手法：静态反模式（`GREEN-*`+`ASSERT-*`）、基线差分（`--baseline` 对比断言计数/弱化）、运行史交叉（`runs.jsonl` failed→passed+断言弱化→critical）。两模式：static（默认，纯 stdlib 离线）、knowledge（+Atlas 知识库增强）；ai 未实现（CLI 拒绝 `--review-mode ai`）。报告走 Allure 2.39.0 风格（复用 Gap5 emit_allure 格式），含总分/维度分/问题清单（file:line+归因）/改进建议。CLI rc 0 通过/1 门禁失败/2 用户错误。边界：审源码、只出报告、永不写知识；`--to-tower` 只造 `pending_review` 草稿（`kind=code_review`） |
| `explore.py` | 非生产探索会话（CDP 留给外部大脑）、trace→pending_review 编译（trace 逐行容错：残行/非对象计入 malformed_lines 不顶掉整份编译；#109：trace/snapshot 被中断腰斩出非法 UTF-8 字节时回落 replace 读——旧实现 read_text 裸 UnicodeDecodeError 崩在读文件本身，#68 的逐行容错一行没轮到；截断前的完整候选仍是有效原矿）；2026-11-04 真实 SIT 冒烟已过 |
| `autopilot.py` | LLM 自主探索循环（gap12 + wave3 gap14）：观察→白名单动作→护栏→执行→留痕→沉淀。**判据动作实做**：`assert` 四类（`visible` 元素可见 / `text` 文案匹配 / `api` 接口状态码与结构（`status`/`json_path`+`type`/`equals`，可选 `url_contains`）/ `compare` 跨模块值比对（`record(key,value)` 存改前值 → `locator` 重读 `count`/`text` 与 `op=eq/neq` 比，或 `locator_a`+`locator_b` 双位置）），失败抛 `AssertionError`、归类走 `failures.classify_failure` 单一真相（经 recorder.step 单点继承），本模块零判词；定位全走 `locators.resolve`、接口路径打点走 `tasks._dig`（单一来源）。**证据与缺陷落盘**：全程写 runs.jsonl（`RunRecorder`，mode=explore-auto），失败步骤带请求实参（method/url/post_data 过净化刀）+ 响应快照 + 页面快照（playbook §1.1「没反应类结论必须附请求实参」的可执行形态；请求/响应观察经 `page.on` **只读**取证，不发新请求）；崩溃路径 finally 落盘（控制流→interrupted、其余→failed，原因过 `exception_brief`）。**坑点自动提炼**：未命中 `match_pitfall(status="approved")` 的失败 → LLM 从证据出 trigger/symptom/root_cause/remedy（严格 JSON 四字段，坏形态点名拒绝不静默回落）→ `journal.draft_from_text` 造 pending_review 草稿、evidence 带 `review.verification_problems` 四元组（command/exit_code/assertions/verdict=fail）——**只进塔不写知识**（红线 #9/#10）；LLM 摘要调用失败/形态坏：点名跳过（步骤 note 记录），失败本身照常落盘。护栏零放松：白名单（无 eval/JS/SQL/文件操作）、`classify_risk` 风险门（默认 max_auto_risk=LOW）、生产闸早于开浏览器、出境 URL 过 `sanitize_url` |
| `overlay.py` | BU 坐标双轨：两组坐标逐键 diff → `bu_overlay` pending_review 草稿进塔；批准落私有 `bu_overlays.json`。#118：坐标文件修正后（如 ou B→C）草稿 id 不变（按「键+两侧BU」定、seen 聚合防重复占塔）——旧实现同 id 命中只增 seen 不刷证据，审核塔停过期值，人审按 --remedy 批准的「目标BU真实值」基于旧证据落成错误坐标；现同 id 再登记刷新最新 evidence（缺省键不抹已有证据——engine 失败路径不带 evidence，零漂移；pitfall 同 id 等值覆盖无害）。复核面 sound：相同值不进 diff、缺失/null 同为「无坐标」、未知 BU 点名清单、顶层混合键不可达（JSON 键恒字符串） |
| `devops_sync.py` | DevOps 平台同步（`atlas devops-sync`，UI AUTO TEST `devops_sync.py` 的 Atlas 移植）：读 `<root>/devops-sync.yaml`（system_id/system_name/exec_user/testcase_directory/testplan_title_template）→ 读 Atlas 产出的 Allure 结果集 → 按 title 去重（同 title 取最新 start）→ 构造四步 payload（resolve_test_directory_by_path / import_testcases_to_library / create_test_plan / batch_relate_test_plan）→ 走 JSON-RPC over HTTP 调 MCP（**服务器名可配置**：devops-sync.yaml 的 `mcp_server` > env `ATLAS_DEVOPS_MCP_SERVER` > mcp.json 唯一 `devops-mcp*`；多候选要求显式指定**不静默择一**；不硬编码旧脚本的 `devops-mcp-agile`）→ 状态写 `devops-sync-state.yaml`（测试计划去重）。**默认 dry-run 只算不传**（对外平台同步属 AGENTS §4 Ask-first），`--execute` 才真实调用；真实调用失败（401/403/400/网络/非 JSON）给点名错误不裸抛。`extract_title` 兼容两类产物：显式 `atlas.case_title` label > 像标题的中文 `subSuite`（UI AUTO TEST 约定）> `description` > `name`（Atlas 约定，`subSuite` 是 env 时回落——否则整个 suite 被去重合并成 1 条=数据丢失）。零新运行时依赖（requests 现有），业务字面量只在配置/测试 fixture（红线 #11） |
| `runtime.py` | 一次登录、Cookie、DemoApi 白名单、探针/契约装配（`contracts()` 委托 `DemoPlugin.contracts()` 私有→包内逐文件回退，真实项目 `ATLAS_KNOWLEDGE_DIR` 的 `contracts/core.json` 生效；两级都缺显式报错不静默空表；`atlas contracts` 展示与运行时校验共用同一来源，不再漂移；`check_demo_api` fail-fast 预检在**浏览器启动前**拒绝坏 DemoApi 路径——旧实现事件序列 browser_opened→baked→closed，真实 SIT 登录+Cookie 烘焙都烧完了才撞 attach_demo 缺文件，白烧会话还留半截现场（与 #90/#97 同红线）；#138 同位补 `config.check_legacy_paths()`——run 轨旧实现只预检 DemoApi 路径，`ATLAS_LEGACY_WEB` 指错要等 `_load(login.py)` 才撞裸 FileNotFoundError（真实浏览器已起、SIT 登录已烧、消息不点名配置键，#103 白烧会话×#90 归因漂移同族；pytest 轨的 check_exec_dir 早就点名，run 轨是漏网面），现三方（CLI run/agent --execute 预检 + `__enter__` + `legacy.check_exec_dir` 委托）共用单一真相）；空账号同族补预检：`resolve_account`（解析单点）+ `require_account`（判据/消息单点）被 run / agent --execute / explore 三入口在 `with` 之前调用 → 一行 stderr + rc 2，修复前该 ValueError 从 `__enter__` 穿透 `main()` 变 ~20 行 traceback；`__enter__` 在浏览器已开之后任何环节失败（登录/OU 超时/attach_demo 缺文件）都先关闭已开会话再原样抛真因——`with` 语义下 `__enter__` 抛错不会触发 `__exit__`，否则每次失败泄漏一个 Chromium+驱动进程；`__exit__` 把本次 quota 建过的单落盘 `reports/recycle_list.jsonl` 闭环回收链——旧实现 `save_for_recycle` 从未接入执行链，真实 SIT 建单只进内存退出即丢；落盘失败无在用例异常才上报，有则让位真因。#121：`LegacyStepTask` 的 step 判据前移构造期——YAML 1.1 的 `step: yes/on/true` 解析成 bool，旧 `int(step)` 静默变 step_1 执行不相干步骤（#116 假合法同族），`3.7` 静默截断；现点名 bool/小数/非整字符串/step_0，引号 '3' 与整值 3.0 合法放行。DemoApi 白名单从引擎装配行收敛 `DemoPlugin.allowed_api_operations` 单一真相（红线 11 引擎不含业务字面量，回归测钉死引擎源码不含操作名）。`__exit__` 双清理错误（回收落盘失败+会话关闭失败）不再互掩——逐个 try 收集、单错误原样抛、双错误聚合 ExceptionGroup 全点名，有用例异常仍让位真因（#85 清理链全留痕）。spec_file 不预检是 #103 有意边界：外部类 `_load_spec` 存活级降级，Atlas 不越界）。C4②：顶栏超时异常里的 `URL={page.url}` 改 `sanitize_url(page.url)`——框架自己把 URL 写进异常是确定行为，SSO 回调 query 常带 ticket/code，这条不净化则 runs.jsonl/草稿/终端三处同时泄 |
| `reporting.py` | runlog 格式导出、运行聚合（残行/非对象/缺主键逐行跳过；`steps` 非列表或含 `null`/非对象步、非数值 `dur_ms` 也跳过或归零——绝不因一条坏行顶掉整份报告。#131：「腰斩残行」防线补字节面——旧 `with path.open(encoding="utf-8")` 整迭代在中断腰斩的多字节字符处抛 `UnicodeDecodeError`，docstring 承诺的「任意 JSONL 不整体失败」与 #112 注释「读侧必须同防线」（experience/review/explore 当时都修了）对 reporting 两函数永不兑现；CLI summary/report 因 `UnicodeDecodeError` 是 `ValueError` 子类被 `_user_error` 兜住不崩，但把含有效行的文件整体拒掉且误归因成「用户输入错误」（中断写坏的盘不是用户输入错误）。现 `errors="replace"` 回落：残行 JSON 必败自然跳过、截断前合法行照常聚合、骨架完整仅字符串含坏字节的行如实以替换字符统计（不静默丢证据也不静默修证据）——与三方读侧同纪律）。C6 读侧：新增 `_version_shapes` 与 `summarize()` 的 `versions` 块（三维度去重计数 + `drifted` 点名同一份 JSONL 里出现过多个值的维度 + `runs_without_version` 计数）——三元组只有落盘没有读侧，「上周 vs 本周」还是得人工 diff；旧记录无 version 键如实计入runs_without_version 不冒充「已核对」，version 形态坏（非映射/空串）按缺键处理不崩聚合 C9：新纯函数 `run_attention(summary, tower_stats=None)`——「等人/操作族」计数的**唯一聚合位**（家族求和走 `failures.is_operational`，不留第二份名单），返回 `{waiting, operational, tower_pending, notes}`；`tower_stats=None` → `tower_pending=None` + notes `TOWER-NOT-READ`（#64 诚实面：没查过就说没查过），坏 shape 输入不抛（判据/聚合函数不得先于被聚合对象死 #89 家族）。`summarize()` 本体零改动：status Counter 天然容纳新终态取值，`fail_classes` 里 waiting 照常可见（不许伪装成「没发生」）。flow 级 `status:"timeout"` 既不进 waiting 也不进 operational——它不属 OPERATIONAL 家族（评审 C9-E），归属挂起待后续波次拍板 **后续波次拍板落地**：归属=「不入家族 + 单独列示」——`run_attention` 顶层加 `timeout` 计数键，非 0 时 notes 追加 `TIMEOUT-COUNT=N（看护掐表，查 --case-timeout 与用例耗时）`；Allure 侧不改仍 broken（fail-loud 方向不变，本波只解决「在 attention 块里彻底隐身」）。同波修 `export_runlog` dead-read：读侧只认 `retries` 而全仓零生产方写它、真实键是 `retried`（engine flaky 腿），flaky 重试条数在 runlog 恒 0——现三级回落 `retries`(外部显式)→`retried`(本引擎)→healed 推导，导出 schema 字段名不改；端到端测钉「生产方键真实落盘 + 旧键确实不存在」防恒真 |
| `toolbox.py` | toolbox 感知器官、抓包/重放/导出（`learn_contracts` 把 200 抓包登记为 minimal 存活级，不自动升 high——high 要求 data 为对象会误判数组型列表接口；分级升级仍走人审。真实 web-toolbox 的 `net_list` 权威键名是 `entries`（扩展 v1.1.4 实测），不是离线 fake 的 `items`——旧实现只认 items 会让真实抓包静默登记 0 条；现 entries 优先/items 兼容/裸列表兼容，计数按 registry 净增不按条目数（轮询同路径去重不虚报）。#120：`console_read` 真实形态是 `{total,filtered,entries}`（bridge-client v1.1.4，与 net_list 同族键名），#105 照离线 fake 只认 items——真实错误行被点名「无 items」当场拒绝，把正常路径翻成错误（#115 教训反向面）；现 entries 优先/items 兼容，键名口径与 learn_contracts 收敛单一纪律。`learn_contracts` 补分页闸门：真实 net_list 默认 limit=50、total 报全量，旧实现拿一页登记一页零告警——部分结果伪装全量（#102 静默 0 条同族假绿），现 total>页大小点名终止。`save_har` 按真实桥接返回形态分流：过滤无匹配返回**纯文本**「错误: 没有条目」（非 dict，v1.1.4 实测）——旧实现无条件 `json.dumps` 包成字符串字面量写成假 .har 坏档（保存成功、真因埋进引号）；现 str 先试解析、坏文本点名原文拒写，None/异常类型点名拒绝。`console_errors` 同纪律：真实桥接 `console_read` 该 build 返回纯文本「错误: 未知方法」——旧实现把非 list 折叠成 `[]`（读成「前端无错误」的假阴性），现合法 `{"items":[]}`/裸 list 如实返回、错误形态点名拒绝；`replay`/`chain_replay` 在 src 无消费方（`ApiEvidence` 用业务方自有 handler）透传诚实不动。**#140 错误形态防线收敛**：learn_contracts 旧把 bridge 错误 dict/纯文本/None 折叠成 `[]` 静默登记 0 条（旧测甚至把 bug 钉成规格「不崩不登记==0」——已翻转），同形态 console_errors 在 #105 却点名拒绝——两函数各修各的漂移（#120 只收敛了成功键名，#92 错误面漏网）；现共用模块级 `_unwrap_records(payload, source)` 单一真相（list 原样、dict entries→items is None 回落、坏形态点名；合法空表如实 []，#115 不翻正常路径），分页闸门顺带修归因——`{"total":231}` 无 entries 旧误报「分页截断」，现解包先行点名「无 entries/items」真因；`save_har` 补 #104 漏掉的错误 dict 后门：缺 log 结构的 dict 点名拒写（合法 {log:…} 照常）。**接线债（保留不删）**：`WebToolboxSensor`/`export_jmx` 为**外部 web-toolbox 桥接适配器**——仅供外部/工具侧消费，src 无内部调用方，当前未接线；tests 当公开 API 用，非死代码（模块承诺的感知器官能力，保留待接线） |
| `tasks.py` | 纯 API Task、通用 UI Task、assert_api（#115：`AssertApi` 的 `save_as` 是真旋钮——`__init__` 存 `self.save_as`、perform 按声明键返回 `{save_as: True}`，与同文件 CallEndpoint 同守 #92 produces 自兑现；旧实现把 save_as 塞进 produces 却从不存实例属性、perform 硬编码 `api_assertion`，DSL 透传自定义 save_as 到 write_produces 才 KeyError，半成品旋钮。`_dig` 路径打错四种形态（dict 缺键/list 越界/非数字索引/标量中间节点）统一收敛成带整条 path 与失败段的 AssertionError（含「不存在」归 data），杜绝旧裸抛 KeyError/IndexError/ValueError 落 unknown/unknown/contract 的归因漂移——#58/#61/#113 同红线） |
| `cli.py` | 命令行入口（`contracts` 展示与校验同源；`contracts --validate` 只读结构自校验，点名空 path/name「冒充覆盖率却永不生效」的契约与无法解析的契约文件，rc 2，不发真实请求；用户输入错误统一「一行 stderr + rc 2」——文件路径类 machine/summary/report/compile/run（run 的 flow load 在登录前失败）、legacy（旧项目根/web 子目录缺失点名 ATLAS_LEGACY_* 键）、规划类 compose/agent（不可拼装 goal 的 `FlowCompositionError`）、运行入口配置类 run/agent --execute（缺环境 URL/DemoApi 路径坏在 `with DemoRuntime` **之前**预检点名 `DEMO_URL_*`/`ATLAS_DEMO_API_PROJECT`，旧实现合法 flow+缺 URL 裸 traceback）均不再抛裸 traceback，与 overlay/review/explore 同口径。#138：同两处预检补 `config.check_legacy_paths()`——`ATLAS_LEGACY_WEB`/`ATLAS_LEGACY_PROJECT` 指错也在浏览器/登录前 rc 2 点名键（run 轨此前完全不预检 legacy 路径，撞点漂到 `_load(login.py)` 的裸 FileNotFoundError）。#132：`_recycle` 读回收清单的调用点其实**不在** try 包裹内，且 #71 残行计数防线不含腰斩字节——文档长期声称的「与 overlay/review/recycle/explore 同口径」对 recycle 从未为真（一条写坏的历史腰斩尾行裸崩 `UnicodeDecodeError`、连要回收的单都列不出）；现补 `errors="replace"` 回落，坏行落 skipped 计数、好行照常列出，读侧断言方为真）。#134：`run --bu` 的 apply_bu 调用旧只捕 KeyError——BU 值形态非映射的 AttributeError 漏网变裸 traceback（违反用户输入 rc2 口径），现 catch 扩 `(KeyError, ValueError)`（`tests/conftest.py` 同轮新增：load_dotenv setdefault 把 cwd 真实 .env/tmp legacy .env 灌进进程环境、monkeypatch 不回滚未知键，配好本机 .env 后 pytest 顺序相关必红 4 个——会话级清 ATLAS_*/DEMO_*/ISERVICE_* 前缀基线 + 函数级快照恢复，产品代码零改动）（`doctor` 读 Dockerfile/compose 走 `_read_text_safe`，present-but-unreadable（非 UTF-8/权限）记 `docker_unreadable`+判不一致，绝不因一个坏资产文件崩掉整个诊断）。#135：`review --apply` 决策文件 `read_text` 是 **#112 读侧同防线第 8 漏网点**——中断腰斩尾行裸 `UnicodeDecodeError` traceback 崩在读文件本身（探针实锤：合法批准行+腰斩尾字节=半途崩整批零落账），现 `errors="replace"` 回落，残行落 failed 点名、截断前合法批准照常执行；`review --approve/--apply` 分支 catch 扩 `PermissionError`→rc2（只读库写闸门 #135 的传播路径——判据只在 `KnowledgeBase._writable_root` 一处，CLI 不复制第二份 #92）。C6：`doctor` 增 `version_triple`（atlas 版本 + 知识库指纹 private/builtin/unreadable + `tz_data` X6 体检），纯展示项不动 rc；unreadable 如实报不静默。C4①：`_gen` 分支打占位映射（WARN 前缀，值永不进终端）。C4③：`atlas allure --serve` 的提示补 `--bind 127.0.0.1` 口径。C2 补丁 2：`review --require-evidence`（store_true，重构波松绑后为 opt-in）与 `--allow-unverified`（store_true）接线并透传 `require_evidence/allow_unverified/override_reason`（复用 `--reason` 做留痕依据）；单条与批量 `--apply` 同受 `--require-evidence` 约束，批量仍无 override 通道。C1：`schedule` 子命令组（add/list/status/enable/disable/edit/remove/run/crontab，`parents=[global_args]` 全局参数前后位均可）；`_schedule` 的 rc 口径=2 用户错误/0 一切调度结果/1 仅派发子进程失败/130 人 Ctrl-C 调度器（KeyboardInterrupt 在 CLI 收尾，记账已由 dispatch 完成）；add/edit 过 `_schedule_writes_gate`（含写步骤无 `--i-accept-writes` → rc2 并点名写步骤，判据在 scheduling.state，不在 CLI 复制）；`crontab` 双形态成品行按评审 C1-A 裁决（Linux=expr 逐字、Windows=/SC MINUTE /MO 最小间隔 + `/TR` 内层 `\"` 转义），stderr 附墙钟/同步盘/冒烟三条提醒，**Atlas 绝不代跑注册**；doctor 不动（W2 单主约束）。C5：doctor 分支增 `boundary_ledger`（present/regions/problems/warnings/uncovered_regions/accepted_debts）与 `boundary_assets_consistent`——账本「存在但坏」=漂移并入 rc1（与 docker 资产同纪律 #134），文件缺失=如实 `present:false` **不改 rc**（新波次前的历史分支/精简克隆不被账本卡死；没核对过就不输出 consistent 键，不冒充核对过）；判据全在 `atlas.boundaries`，CLI 零复制（#92/#11） C9：`run` 分支的 `with DemoRuntime(...)` 外多包一条 `except failures.AwaitingHuman` → stderr 一行 ASCII 前缀 `WAITING-HUMAN: <brief[:200]>`（X4：整行可 cp936，中文段走净化刀）+ **rc 3**；`__exit__` 在本 except 之前照常跑完（浏览器关闭/回收清单/决策留痕不因这条腿丢失）。退出码「3=等人」的口径**只限 run/agent/dispatch 子进程面**——`atlas gen` 的 rc3 是结构自检失败（两义不并表，README 按子命令分表），supervisor 对子进程 rc3 原样透传（`proc.wait`，专项测钉）。`summary` 分支增 `attention` 键 = `run_attention(summarize(...), ReviewQueue(...).stats())`（塔真读过才给数，没读记 `TOWER-NOT-READ` 不冒充 0）。agent 轨对 AwaitingHuman 未声明（泛捕获仍记「执行失败」），本波不声称已接 **后续波次**：agent 轨补齐两条显式终态腿（此前 `AwaitingHuman` 一路穿透到顶层泛捕获，journal 记「执行失败」、rc1——run 轨早就有的 rc3 在 agent 轨是漏网第二条腿）：`AwaitingHuman` → stderr `WAITING-HUMAN:` + **rc3**（与本分支既有 ApprovalRequired 同值，口径仍限 run/agent/dispatch 子进程面）；`DeliveryUncertain` → stderr `DELIVERY-UNCERTAIN:` + **仍 rc1**（红线 #7 不洗绿，但终端措辞必须与一般失败区分——看到「执行失败」的直觉动作是重跑，这里正确动作是先核实数据）。如实标注差异：agent 轨不经 dsl/RunRecorder，不产 runs.jsonl 的 waiting 行。supervisor 收尾腿的 `close("timeout")` 改引 `failures.FLOW_STATUS_TIMEOUT` 单点 **gap9/gap9b**：gen parser 补 `--fast`（help 字面含「须配 --with-pytest」，有 `test_gen_fast_help_mentions_pairing` 钉文案，改措辞会红——文档引用照抄现有字面）；配对判据**唯一在 `GenOptions.validate`**（CLI 不复制第二条 if，#92；单用 `--fast` 走 SourceError→一行 stderr+rc2，不隐式打开 `--with-pytest`——静默改行为会把用户显式决定偷换成默认）。新子命令 `atlas export-pytest <flow.yaml> [-o DIR] [--fast] [--force]`：校验先 `validate_flow`（其第一步即 compile 静态段——声明位形态闸与 compile/run 同尺，A.5 挂载点收益），结构坏**连产物目录都不创建**（拒绝先于 mkdir，不留半成品假象）；rc 0 导出（stdout 文件清单）/ 2 用户错误（flow 不存在、结构拒绝、目录非空无 force、`--fast` 闸门拒绝）/ **3 不使用**——生成期不执行产物，无「等人」终态可映射（与 gen 的 rc3=结构自检失败两义并立，README rc 分表已按子命令分列）；确定性=同 flow 重复导出内容相等 |
| `waits.py` | 语义等待：表格行数、mask 消失、Vue 字段、URL 变化（表格先等 attached 再等 loading mask 落定才读 vm.data——空表是业务事实 `min_rows=0` 返回 0 不恒超时；真实 Chrome 取证空 `.el-table` 高度 0 判 hidden，fake page 掩盖不了；轮询用 tick 计数不依赖 `evaluate("Date.now()")`，导航期执行上下文销毁抛错容忍重试不裸崩。#133：「容忍重试」不吞不可恢复错误——旧 `except Exception` 无差别重试把 Target closed/JS 语法错/业务 JS 自抛 TimeoutError 当导航瞬态烧满窗口谎报「未达行数/无值」（框架故障伪装成等待契约失败 #102，会话已死还空烧 10 拍）；`_is_transient` 按 `failures.exception_text` 单一真相匹配 Playwright 官方瞬态文案（context destroyed/navigation/detached）分流，瞬态照常穿越 reload、其余立即传播；控制流非 Exception 不受影响（#114 红线）。timeout_ms<=0 透传层假设被真实 Playwright 证伪（0=无超时文档语义、负值归 1），未据此改码、留取证口径） **C12-E 后续波次**：`is_recoverable` 在**任何文本判据之前**按 `failures.NEVER_AUTO_RETRY` 类型级拒重试。两个案发点：①旧防线靠引擎的 `read_only` 元数据门，而 C12-A 已论证「元数据/seq 证明不了没发出」——连副作用是否存在都要 fail-closed 猜的东西不能反过来当放行依据；②`_is_transient` 求值早于 `classify_failure`，reason/cause 文案带 "navigation"/"detached" 的 AwaitingHuman 会被判可重试（引擎分支次序只挡外层一次，`waits.retry(retry_on=...)` 循环内二次抛出仍漏）。归因面不受影响 |
| `plugin_api.py` | 业务插件解析单一入口（引擎级缝）：`resolve_plugin()` 未设 ATLAS_PLUGIN 时返回 DemoPlugin（与旧硬编码 `DemoPlugin()` 完全等价，含 make_provider 语义），设 ATLAS_PLUGIN=<domain> 时先查 registry 再尝试动态加载（`_load_external_plugin`：路径形态按文件加载、模块名按 `atlas.plugins.<name>` / `<name>` 依次 import，取模块级 PLUGIN）；domain→类映射由 `register_plugin` 登记（默认仅 demo），引擎不含业务字面量；ATLAS_PLUGIN 指到未注册 domain 且动态加载失败抛 ValueError，CLI 接 _user_error rc 2；cli/runtime 的 `DemoPlugin()` 调用点机械替换为 `resolve_plugin()`，零业务语义变化 |
| `scaffold.py` | Gap1 `atlas init` 项目初始化向导：`init_project()` 生成 .atlas-root/.env(值全空)/.env.example/.gitignore/pyproject.toml(不含 allure-pytest)/flows/smoke.yaml(通用 api_call 离线 compile 可过)/tests/test_smoke.py(纯 assert 3 用例产 runs.jsonl)/scripts/run_allure.py(test→emit→generate/serve/open)/README.md；.atlas-root 最后落盘防半成品假象；冲突防护（已有 .atlas-root/非空目录默认拒绝）；--interactive 才读 stdin（CI 安全）。C4③：`run_allure.py serve/open` 与 README 的分发段补「自建 HTTP 必须 `--bind 127.0.0.1`」口径（纯文案，子命令参数不动）——结果集/报告含真实 URL、单号与截图，别把报告开到 0.0.0.0。**gap9 文案层**：init 生成 README 增「快模式」命令示例段（`atlas gen <doc> --with-pytest --fast` + F9 依赖栈说明 + 「为何 init 骨架不带快底座」注——本骨架生成时无组件知识库，选择器/登录指示器未经人审，此时写 `_atlas_fast.py`+`conftest.py` 即红线 #12 的形式代码；后续波次若要骨架资产，**唯一通道=调 `emit_fast.write_fast_artifacts`，禁把模板文本复制进 scaffold.py**（#92，与产物测名单从 `_CREDENTIAL_TABLE_NAMES` 派生同理——不留第二份）。本波只动文案、不调 emit_fast、不产模板产物；**开箱可装（本波）**：`detect_atlas_source_root()` 默认探测当前 Atlas 源码根（向上找声明 `name=atlas-test-platform` 的 pyproject.toml）并钉成 `atlas-test-platform @ file:///…`——旧默认写裸索引名，而 Atlas 是源码分发（README 第一步就是 `pip install -e .`），新用户接手骨架第一步就 "No matching distribution found"（`--atlas-path` 在文档里还写成「可选」）；装成 site-packages 时探测不到 → 如实回落索引名 + CLI stderr `WARN:` 两条补救路径 + 生成 README「依赖前置」段（不静默给一个装不上的骨架）；`atlas_dependency` 键如实回传（mode=file/index）。守护测在 `tests/test_init_scaffold.py`（合成布局判据 + 反向对照）|
| `config_add.py` | Gap2 `atlas config add` 微服务配置生成器：`add_config()` 流程二生成 plugins/<domain>/{plugin,tasks,probes}.py + knowledge/*.json 空骨架（pitfalls:[] / contracts:[] 不伪造覆盖率）+ .env.<domain>.example；流程一 --mapping 只出 ATLAS_* 映射片段（零代码）；骨架 Task perform 诚实抛 NotImplementedError；allowed_api_operations 空集；domain 校验 ^[a-z][a-z0-9_]*$ |
| `generator/` | Gap3 `atlas gen` 文档→测试代码生成器（只生成不执行，红线 #9）：`sources.py` 按内容嗅探 OpenAPI3/Swagger2/Markdown/纯文本（UTF-8 严格→replace 回落 #131）；`openapi.py` $ref 解引用+循环检测截断；`emit_flow.py` api_call step（path+query 拼 path、body 分流 dict/list→json= str→data=、全局 isc-app 头并入每步 BUG-920、save_as 全局唯一 compose_for_goal 同键多生产者硬报错、写场景 read_only:false+on_env_blocked:stop）；`emit_contract.py` 契约自动分级（默认 minimal、对象含 resultCode→high、数组锁死 minimal、core 仅文档 x-atlas-level 显式标注、--level 不能强升 core、同键不同定义冲突不覆盖 #128）；`emit_probe.py` 只 find() skeleton、create() 恒 NotImplementedError（红线 3/12/16）；`checklist.py` 复用 FlowCompiler.validate_flow + 占位符递归扫描 + 悬空引用检测；`emit_flow.py` 的 flow 装配末尾过 C4① 字面敏感头闸（判据在 `credential_gate`，生成器不复制名单），文档/`--headers-file` 里手写的字面鉴权值只剩 `${ATLAS_HDR_<n>}` 占位，`cli._gen` 汇总提示只报键名；`tower.py` trace_step pending_review 草稿（append_draft 不伪造异常）。列表下标断言 DSL _lookup 不支持 → 列 manual_assertions 不伪造。Allure 不自写（生成阶段无结果=伪造），主路径 atlas run→runs.jsonl→atlas allure。退出码 0/2/3 |
| `generator/emit_pytest.py` | 回归代码产出（`atlas gen --with-pytest`）：把归一化模型（`Endpoint[]`）或 flow 生成**可独立运行**的 pytest 代码包（`test_<id>.py` + `atlas_pages.py` 页对象库〔派生自组件库 components.json〕+ `_atlas_support.py` 自包含底座〔仅标准库，可脱离 Atlas 运行〕+ README）。形态评审见 `docs/gaps/gap7-regression-code-eval.md`：推荐「pytest 用例 + 共享页对象库（形态2）⊕ 自包含底座（形态3）」，**弃用「每用例自带 step_N 零复用」（形态1，N 份漂移代码）**。红线：#9 只生成不执行；#7 只断言文档明示项（数组下标列 TODO 不放宽）；#4 底座仅 stdlib，UI step 以 `pytest.mark.skip` 占位显式声明 Playwright 依赖；未解析 `${VAR}` 运行期显式报错（绝不把占位符当字面量发）。产出确定性、拒绝覆盖。**快形态（gap9/gap9b）**：`fast=True` 的 `render_test_module`/`emit_pages_module`/`emit_pytest_package` 输出与改造前**逐字节相等**（快照测钉，存量零涟漪）；fast 渲染 UI 腿出**真函数体**（skip/`NotImplementedError` 通路已删，E.1.2 慢包保留反证成对钉住）——页对象类名唯一来源是显式声明位 `page_object`（gap9b #12 删除 `_pascal(spec.task)` 猜名支：Task 名 `select_customer` 与组件名 `customer_selection` 两侧永不相等，靠猜=引擎猜业务 #11），委托腿按 `legacy_pages` 渲染 `legacy_page_call("<fn>", page, …)`（模块位=产物常量 `lib.pages`，点分值在 compile 静态段拒）；两道闸分工不留第二把尺——**形态**归 compile 静态段（dsl），**成员资格**（类名 ∈ `emit_pages_module` 实际类名集）归生成期，无组件库含 UI 腿（F-9）/对不上（F-10）/缺声明位（B-2）都在**写盘前**点名拒绝、目录无残留（先写文件让人撞=假可用 #9）；纯 API 源照常放行（正向对照成对）。`login` 骨架两义分立且次序固定（先查 env 再判接线）：缺配置→`EnvBlocked("缺配置：<键>")`、未接线→`NotWired("登录未接线：<组件/元素>")`，禁 or 文案（「补个键」与「找人接线」是两种下一步，压成一句让值班的人猜）；产物 runs.jsonl 接线走 `_fast_runlog`（一 test 一行、assert 步名以 `assert ` 开头），API 腿结果按 `save_as` 写 plan_ctx、`requires` 经 `require_ctx` 前置点名 |
| `generator/emit_fast.py` | **快模式底座模板唯一来源**（gap9，F-6/#92 在文档面的执行形态）：`_FAST_SUPPORT_SOURCE`/`_FAST_CONFTEST_SOURCE` 两段模板文本与 `write_fast_artifacts`（唯一写点，冲突无 force 点名拒、确定性无时间戳）——消费方（`emit_pytest.py` 渲染层、`cli.py`、`scaffold.py` 文案、判据测）**只许 import 这些符号，不得内嵌第二份模板文本或手抄值表**（复制后改一漏一，产物形态与判据各自演化、只在真机以「同一条失败两处分类不同」暴露）。判据单点全在本模块：`ENV_KEYS`（产物运行期旋钮**唯一清单**，AST 自证测与模板实读键集合双向等值——死旋钮与漏读键都红；「N 键」计数措辞已废，等值即计数）；`session_channel(env)`（attach 三条件唯一判据位：CDP 非空 ∧ `ATLAS_FAST_INTERACTIVE=="1"` 在场声明 ∧ 非 prod-，八向真值表测；conftest 分流腿与 login 腿都经它取道——fixture 内不许另写条件，「判据在位没人消费」同族 #106/#139）；`safe_persist`（plan_ctx 只收非空标量，容器/bool/长文本拒、WARN 不回显值，`SCALAR_MAX_LEN=500` 与 `REASON_MAX_LEN=200` 由既有测显式钉不等——巧合性防线的教训见落地注 F-27.3）；`framework_reason`+`REASON_MAX_LEN`（产物 run 级 reason **只取框架自产点名文案**、非空才写、截 200——产物不 import atlas，**不复制第二把 `credential_gate` 净化刀**，「根本不落不可控文本」是更强边界，等值性留白见落地注 F-5）；`NotWired(EnvBlocked)` 类型级未接线归因（继承既有 env_blocked 一支、零新 fail_class，裸 `NotImplementedError` 不复用为该语义——文本猜未接线=给真缺陷开洗成 skipped 的门 #7 反面）；禁词形态判据（产物用例层 `wait_for_timeout`/`pytest.skip`/`time.sleep`=0，底座内实参恰为裸 `TICK_MS`——乘算与字面毫秒皆红，对抗样本注入测）；锁步测把 `TICK_MS`/`_TRANSIENT_MARKS`/`RUN_STATUS_VOCAB`/`FAIL_CLASS_MAP`/PROD 前缀钉回 `waits`/`failures`/`config` 源文本值（从源提取比对、非手抄常量）。**「快」的宣称边界=实测已部分交付、产能结论未交付**：R1/R6 已真机实测（数与解读见 README 边界段与归档 `reports/r1r6_20261109/SUMMARY.md`——桥接轨**不快**、净增约 8s；会话层 attach 比 launch 省约 23s/模块；两道闸真机成立）；**接线后的同链路 UI 产能仍未测，需 R2**——「快模式已验证比旧套件快」不得出现 |
| `scheduling/` | C1 调度核（`atlas schedule`，design_c1）：**形态钉死=被 OS cron/schtasks 调起的一次性薄命令，无常驻进程、不代注册 OS 任务**。`constants.py` 数值唯一来源（准点 120s/补跑窗 7d/连败 5/最小间隔 5min/租约 TTL 90s）；`cron_parse.py` 5 段自写解析器（纯 stdlib 不引 croniter/APScheduler——E2 口径：croniter 不触 §4 字面，不用它是「纯 stdlib 先例+评审项」，理由不得写成红线禁止；dom/dow 双受限按 POSIX vixie 取 OR、任一 `*` 取 AND；names 显式拒并点名段号；槽位恒整分，due=prev_slot 右闭 `slot ≤ now`、认领 `(since,due]`、计数 `(since,due)`——「+1s 钉整点」等价；`wall_age()` 年龄取墙钟域，DST 段绝对差会翻窗口判定；时区加载点 `load_tz` 与 C6 `tz_data_status` 同口径，缺 tzdb 点名 pip install tzdata 不静默 UTC）；`decision.py` `decide_slot` 纯函数（表 1–7：manual/未到点 none、≤2min schedule、>2min 且 ≤7d catch_up **只补最近一轮**余者计 skipped_slots、>7d drop 推进水位线、due≤since none；表 10 计败判据 `failure_policy()`：timeout/rc≠0 计败、interrupted/defer/drop 不计败——CONTROL_FLOW #114 调度域第二实现，「不计败」分 keep（不动连败）与 reset（成功归零），不把 Ctrl-C 读成成功）；`lease.py` O_EXCL+TTL 权威（S2：心跳 ≤90s 不抢占 defer；过期接管 unlink PermissionError 重试 1 次仍败→defer rc0 **绝不硬删**；抢到回读 token 双检；`pid_alive()` 是 C1b 占位恒 None——Windows 禁 os.kill(pid,0)=CTRL_C_EVENT 组广播误杀，探针只做提前接管加速，不确定按推迟不判失败，实现不得私自提进 MVP）；`state.py` 台账 `tasks.json`（水位线/连败/auto_paused_reason；`apply_patch` 表 8：cron/tz/flow/run_args 任一变更或重新 enable → 重置水位线+清连败——人改配置的瞬间不回放历史；比对按**归一后存储形态**，同值重提交不算改）+ 调度索引 `schedule_runs.jsonl`（与业务 runs.jsonl 分离，「错过几轮」不污染缺陷分布；`engine_run_id` 恒 null=不猜跨域关联；X5 断行/X9 原子 replace/逐行容错三纪律）；`dispatch.py` 一次性 tick（enabled=false 静默 rc0；CAS 认领会先读台账——**互斥权威是租约，CAS 只是辅助防线 C1-F**；首轮 CAS expected=None 而决策 since=created_at，两者混用会把没跑过的任务误判成被抢；argv 固定 `[sys.executable,-m,atlas,run,flow,*run_args]`，`guard_run_args` **显式 raise** 拦 `--approve`/`--mode`（C1-C：python -O 剥 assert，安全闸不依赖它）；子 env 注入 `PYTHONPATH=<root>/src`（C1-E：src 布局+干净 cron env 下 `python -m atlas` 否则起不来）+ `PYTHONIOENCODING=utf-8`；超时只靠 `subprocess.run(timeout)` 不自己发信号 X7；心跳 daemon 线程 30s 续租、join 超时收尾；defer 不推进水位线=推迟≠跳过（与 pi overlap 推进水位的有意差异：Atlas 的 tick 就是 cron）；台账/索引写失败不顶业务信号 #110/#130，finally 必放锁）；CLI rc：2 用户错误 / 0 一切调度结果（defer/none/drop 都是正常答复，cron 不看 rc 干活）/ 1 仅派发子进程失败 / 130 人 Ctrl-C 调度器。**默认只读档**：add/edit 离线 validate_flow 扫 `read_only=False` 步骤，含写无 `--i-accept-writes` 拒入库（M14）；生产写第二层仍由 engine `validate_write` 兜（engine 零改动）。crontab 双形态（评审 C1-A 裁决）：Linux=expr 逐字复制（窗外不补发，错过的由窗内下一 tick catch-up）/ Windows=`/SC MINUTE /MO <最小间隔>`（窗外也 tick，与 pi 常驻同语义），两形态差异写进 stderr 与 README；`/TR` 内层引号 `\"` 转义；输出成品行绝不代跑（Ask-first）。状态文件全在 `reports/schedule/`（gitignore 内，真实域名/账号名不入库）。C1b（ctypes 探针）不做，接口+契约测在位。**cron 最小 env 缺口收口**（impl_report 实测风险 + team-lead 拍板 1+2）：cron/schtasks 父 env 不继承交互式 shell export 的 `ATLAS_KNOWLEDGE_DIR` → 无人值守会静默回退包内虚构知识跑真实 flow（读侧逐文件回退本就合法、engine/dsl 不报错，故必须主动防）；处置只在展示/WARN 面加、**判据不新建**（唯一取源仍是 `versioning.private_knowledge_root`，覆盖 .env/plugin/env）：`schedule add`/`edit` 当场把当刻观测记进台账 `knowledge_source` 字段（builtin 时 stderr 一行 ASCII `WARN: knowledge_source=builtin`），`crontab` 成品行内嵌解析到的 `ATLAS_KNOWLEDGE_DIR`（无则放占位 `__FILL_ABSOLUTE_PATH_TO_PRIVATE_KB__` + stderr 提醒，绝不留空被忽略）；`dispatch.child_env` 继承 os.environ 把该键带给派发子进程（调度器进程经 from_root→load_dotenv 已从 .env 拿到，故写 .env 是最稳出路）。拒处置 3（defer 需「哪个是真实业务库」新判据=红线 #12 味道） C9：`decision._POLICY` 加 `"waiting": "keep"`——等人不 +1 连败（否则 AUTO-PAUSED 恰好停掉「等人要人看」的巡检），也不清零（前面真失败不许被一次等人抹掉）；这是 CONTROL_FLOW #114 在调度域的第三实现（等人≠环境挂≠成功），词表**不与** `FailureClass` 合并（两套词汇各有语义，合并=反向 #92）。`dispatch` 的 rc 映射加一支 `child_rc == 3 → outcome "waiting"`（置于 0/非 0 判定之内、failed 之前），`counts_as_failure` 经 `failure_policy` 自动 False；`tick` 对 waiting **返回 0**（rc1 在 OS cron 侧的既有读法是「这条巡检挂了」，报 1 等于把等人洗红——业务终态由 run 轨的 rc3 表达，调度层只答「这一轮正常跑完」）；水位线**照常推进**（本轮认领过就是过了），与 `defer_lease`「没执行所以不推进」两语义不得混，各配一条测 **后续波次**：dispatch 子进程 rc 表补两格（次序 0→3→124→130→其余，两类都必须在 failed 之前）——`124→aborted_time_limit`（用例被自身看护掐表）、`130→interrupted`（`_spawn` 不加 `start_new_session` ⇒ 人 Ctrl-C 时子进程同组同收，旧表把它落 else 洗成 failed 并计败，等于把「操作者收工」记成「这轮巡检坏了」，连败被无端 +1 攒够就 AUTO-PAUSED）。`tick` 返回集把 interrupted 归 0：台账对它已是 `counts_as_failure=false`，OS 侧再返 1 就自相矛盾（调度器**自身**被 Ctrl-C 那条腿不走这里，它 propagate 重抛）。124/130 复用既有 outcome 名不新造词——台账 `child_rc` 已记码值，能区分「谁掐的表」。count 类 reason 改读 `child_rc`（旧实现一律写「timeout after Xmin」，rc=124 的台账会谎报成派发窗口超时）。rc130 成立的依据是**构造证据**：`build_command` 钉死子命令是 `atlas run` + `cli.main` 顶层 `except KeyboardInterrupt: return 130`；本机复现 Ctrl-C 打死子进程未成功（`CREATE_NEW_PROCESS_GROUP` 默认禁用该组 CTRL_C_EVENT，AllocConsole 同组广播两次仍打不到），故 OS 硬杀码 `0xC000013A` **未取证也不猜**、不加分支 **gap9b attach 不进无人值守（F-12/F-13 对称三件套，每面有真实泄漏面支撑、非投机）**：①argv 面——新独立闸 `guard_fast_cdp(run_args)`（与 `guard_run_args` **分离=判据对象不同**：一个管审批旗标、一个管会话通道残留，合写会让键名单点测含糊），`run_args` 中出现键名单点常量 `FAST_CDP_ENV_KEY` 字面或 `--cdp*` 近似旗标 → **显式 raise** 点名（沿 C1-C：安全闸不靠会被 `python -O` 剥掉的 assert）；`build_command` argv 恒 `atlas run <flow>` 形态不变；②env 面——`child_env` 继承 `os.environ` 是**第二个泄漏面**（调度机 shell 里设过 `ATLAS_FAST_CDP` 会直达无人值守子进程，只堵 argv 漏一半），显式 pop 三键 `ATLAS_FAST_CDP`/`ATLAS_FAST_INTERACTIVE`/`ATLAS_FAST_ALLOW_WRITES`（第三键 scout 提议、lead 裁采纳：**调度 env 携写放行=把「人显式批准写」从审批面挪到环境残留面，违 #13——与「argv 永不带 --approve」是同一道闸的两半**；正向对照：无害键照常保留、`PYTHONPATH/PYTHONIOENCODING` 注入不变）；③**行为面（三件套里最关键）**——测「pop 剥离 `ATLAS_FAST_ALLOW_WRITES` 后写步骤仍呈 env_blocked、reason 点名写闸，**不得变 passed、不得消失**」：剥离放行≠放行成功，若 pop 被实现成「写腿跟着消失」，pop 自己就成了第二条把写悄悄跑掉的入口（#7/#13 反面）。现行 dispatch argv 恒 `atlas run`（快包产物不经调度派发），三件套防的是 shell 残留 env 渗透 |
| `plugins/demo/tasks.py` | 10 个核心 Task，委托原 `pages.py`；auto 路由子类。#134：`apply_bu` BU 值形态非映射（手滑存成数组，#127 同族）旧 `.items()` 裸 AttributeError 且 CLI 只捕 KeyError 漏网 traceback——现点名「形态非映射」ValueError（CLI catch 扩 `(KeyError, ValueError)`）；空坐标判据旧 `value in (None, "")` 漏空容器——`[]`/`{}` 被 str() 成 `"[]"` 写进 `ATLAS_SESSION_*`，真实执行切 OU 拿 `"[]"` 必超时（坏形态伪装合法坐标 #77/#102；#117 单号「非空标量才算」同纪律），现 `_is_coordinate` 只认非空标量（bool/容器/空白 ignored），标量数字照常 applied（#115 不翻正常路径）。#137：`_session_default`（auto API 快路径 division 兜底）旧自己 `json.loads` 直读**包内** session.json，绕开 KnowledgeBase 私有→包内回退——设 ATLAS_KNOWLEDGE_DIR 后与 CLI/运行时的 `session_defaults()` 分叉拿虚构值查错客户（红线 #11 被引擎侧旁路击穿，两份「唯一来源」漂移 #92）；现委托 `DemoPlugin.session_defaults()` 收敛单一真相，缺键友好点名，既有字面量扫描测当场抓到注释里的示例字面量（防线生效） |
| `plugins/demo/knowledge/` | 30 条坑点、组件、业务、环境、契约 |

## 开发规范

1. 引擎不含具体业务；业务内容放插件。
2. 新增 Task 必须保证元数据与实际 ctx 读写一致，并补目标拼装测试。
3. 不机械包装全部页面函数；只有业务流确实复用才新增 Task。
4. 不写假 creator、假 API 实证或“看起来完整”的未验证状态机。
5. 默认 `run` 零成功截图；排障才 `debug/heal`。
6. 原始 `legacy-suite` 和 `legacy-api` 不修改；外部路径通过 `.env` 配置。
7. 生产写默认只读（唯一放行通道 `ATLAS_ALLOW_PROD_WRITE=1`：默认关=逐字节不变，打开时 engine 打一行 ASCII 警告并把 `prod_write_allowed` 记入 runs.jsonl，开关值只认精确 `1`、不 strip，写坏按关处理并点名）；自动单据必须带「非人工创建」。
8. 每次收工同步 README/AGENTS/CLAUDE/PRIVACY 受影响的事实。
9. 探索/自愈/生成的产物一律 `pending_review` 进 `atlas review` 塔；除 `--approve` 外不存在知识写路径，critical 档无自动晋级。
10. 冻场（CDP_PAUSE_STEP）只在 debug 语义下使用；run/CI 不设环境变量即零开销，禁止在流水线里开 CDP 端口。

## 常见问题

### Q: 为什么旧用例不直接拷到 Atlas？
A: 拷贝会形成两份漂移代码，并破坏旧项目的依赖、登录态和验证基线。`LegacyAdapter` 在原目录调用 pytest；新增能力通过 `LegacyStep` 渐进接入。

### Q: `channel: auto` 是否一定 API 优先？
A: 它是意图声明，只有 Task 明确实现 API 操作才启用路由。未验证接口不能自动取代真实 UI，更不会自动重放写操作。

### Q: 为什么找不到数据不自动创建？
A: `find_only` 明确禁止造数；`find_or_create` 也必须由业务插件提供受审核 creator。当前 Demo 默认只实现探测，未伪造建单能力。

### Q: agent 如何拼装流程？
A: 读取 `atlas tasks` 的 `requires/produces`，`compose_for_goal(tasks, goal)` 递归选择必要 Task，再拓扑排序；缺依赖/循环会明确失败。同一产出键有两个不同生产者也明确失败（引擎无法判定副作用等价，静默选一个要么遮蔽可行路径、要么把读目标翻成写操作）——注册表去重后再接入。

### Q: 如何真实运行混用？
A: `DemoRuntime` 统一登录、烘焙 Cookie、装配白名单 DemoApi、契约、知识和探针。
`python examples/hybrid_readonly.py` 已实测 UI 会话与 API 只读查询同时可用。
`atlas run <flow.yaml>` 可注入数据供给；默认 create 配额 0。

### Q: agent 能自己决定跑什么、能不能跑吗？
A: 能规划与评估，但不能自行越权。`AtlasAgent` 按 `requires/produces` 规划，再按业务影响分级：
`read_only` 可自动执行，`low/medium/high` 需 `approved=True`；生产写恒为 `high`。
每次决策写入 `reports/agent_decisions.jsonl`（含被拒绝的原因；半途执行失败/中断也留痕并带 `executed` 完成清单——审批门放行后已执行的写 Task 绝不能无痕；成功决策同落 `executed`，读痕人能确认哪些写 Task 真实发生过——#110）。留痕盘写坏（目录位被占/权限）不顶业务信号：落盘尽力而为，内存 `decisions` 不丢、成功返回/`ApprovalRequired` 照常。可审计。

### Q: create（造数）真的能用吗？
A: 能用，且已真实跑通。SIT DEMO 的建单实现是 `atlas/plugins/demo/creators.py`：
报文模板来自**真实 UI 保存请求捕获**（`scripts/capture_save_request.py`），不是猜字段。
`examples/api_create_then_ui_verify.py` 实测 API 建出 `SO-DEMO-0001`，UI 列表查到该单、PT 一致。
护栏：生产禁写、配额在调用前占用、结果必须含「非人工创建」与单号、run 退出时建过的单自动落盘 `reports/recycle_list.jsonl` 供 `atlas recycle` 回收。
若换 BU/环境，用 `make_scripted_creator` 挂接各自的已验证脚本。

### Q: Linux 验证到哪一步了？
A: **已实跑通过**（原生 venv 方式）。Debian 12 / x86_64 / Python 3.11.2 / 系统 Chromium 156：
`110 passed` + `COMPILE_OK` + `Playwright local page: PASSED` + `LINUX_ACCEPTANCE_PASSED`，
归档在 `reports/linux_acceptance.json`，脚本 `scripts/linux_acceptance_remote.py`。
关键点：`ATLAS_BROWSER_EXECUTABLE` 让 Atlas 用系统浏览器，绕开 Playwright CDN 不可达。

**Docker 路径未跑通**：那台服务器访问 Docker Hub 不通，回退本地基础镜像后构建超时。
`Dockerfile` / `docker-compose.yml` / CI 属于「已提供、未实测」，不要声称已验证。

Linux 实跑还抓到 3 个仅 Windows 能过的测试缺陷（硬编码 `../legacy-api`、依赖本机 Docker 状态），已修。

### Q: 数百条接口的契约怎么处理？
A: 不逐条写强 schema。三档：`core`（完整字段类型校验）、`high`（通用规则 resultCode）、
`minimal`（只判存活）。当前 Demo 为 `core 1 / high 5 / minimal 1`，`atlas contracts` 可查。
新增接口默认落 `minimal`，只有业务关键接口才升级到 `core`。

### Q: JMeter 巡检工程怎么迁到 Atlas？
A: `python scripts/import_jmeter.py 巡检.jmx -o flow.yaml --flow-id ics-patrol`。用 `root.iter()` 递归抓
`HTTPSamplerProxy`（含 TransactionController 嵌套），并提取 `HeaderManager` 全局鉴权头并入每个 `api_call` step——
**ICS 后端缺 `isc-app` 业务识别头会回 BUG-920**，所以迁移必须保头。引擎侧 `CallEndpoint(headers=...)` 与
`CallApi.request(headers=...)` 已贯通（None 用会话默认头，传入按 requests 语义合并）。
`${VAR}`/`${__P()}` 变量原样保留但会递归下钻 body/path/header 逐条告警，供人审接 env。
**报文完整迁移**：真实工程「Body of a sampler」（`postBodyRaw=true`）按 elementProp 分组取正文
（旧实现 bool 当 body 当场崩、`[:500]` 腰斩大报文，巡检发残缺报文得假 400 误判成框架缺陷）；
多参数逐条拼接不互相覆盖、外层 Arguments 容器节点不重复计；扁平 `Argument.value` 精简形态兜底。
**发送端 body 分流**（`CallApi.request`）：`dict/list` 走 `json=`，`str/bytes` 走 `data=` 原样——非 raw 多参数
迁移产物是 form 串，一律 `json=` 会被 requests 包成带引号 JSON 字符串字面量发出得假 400；其余类型发送前点名拒绝。
**只生成 YAML 供人审，绝不自动执行**：生产写与鉴权值必须人工过一遍再 `atlas run`。
**字面敏感头闸（C4①）**：HeaderManager 里的字面凭据值（Authorization/Cookie/`isc-axios-sessionid`+UUID 等）在写 YAML 前统一换成 `${ATLAS_HDR_<n>}`，映射清单只报键名——凭据值不进 YAML/stdout/stderr；`${VAR}`/`${__P()}` 已外部化的值不重复告警。未解析占位符清单的值段也先净化（`${__P(name,字面默认值)}` 的 DEFAULT 段旧实现被 `val[:80]` 原样打回 stderr）。**无 `-o` 且闸命中时拒打全文**，只出摘要与接线指引；零命中时 stdout 行为逐字不变。告警标记 ASCII 化（`WARN:`）——旧 `⚠` 在 GBK 控制台直接崩掉迁移。

### Q: 测试已经覆盖真实 SIT 吗？
A: 离线验证 **2535 passed**（2446 上一收口位 + 89：本波「通用知识包组合层」+14（`base_roots`/`load_composed`/`generic` 可见性，tests/test_evidence_and_knowledge.py +12 + 插件与 config_add 骨架三层接线 tests/test_config_add.py +2）+ 2446 收口后存量补测 +75（缺陷修复波「测试盲区」，此前未落数）；2428 上一收口位 + 18：`envs.json` 账号/OU 环境坐标接进会话默认链
6 条 + 空账号闸门经 CLI 三入口（run / agent --execute / explore）预检投递 rc 2（判据单点
`runtime.require_account`）+ `--env` choices 收敛到 `config.ENV_URL_KEYS` 单一真相 + `atlas init`
默认钉 Atlas 源码根（骨架开箱可装）/ README「拿到先跑这三步」/ 随仓样例 `examples/api_sample.md`
/ docs↔样例漂移守卫；再上游为 2404 迁移坑点审核与 URL 净化波 + 24：迁移坑点审核工具 `scripts/pitfall_review.py` 17 条 + URL
净化判据与探索链路（落盘/出境/读侧）4 条 + 新脚本入哨兵网 2 条 + 上一笔 cffd717 未落数的 1 条漂移；
更上游为 2216 gap12 自主探索波收口 + 重构波「产物优先可移植包」+188：2.2 产物 UI 腿可执行化、2.3 分片、2.4 trace 归档、2.5 浏览器矩阵、2.6 iservice 第二业务插件 + 插件缝、松绑（生产写降级为 env 开关 + C2 证据闸 opt-in）、#13 产物凭据闸、#16 无渲染分支点名真因、#17 login 骨架去 base_url + 凭据帧归零，以及 4 项清理（B-01/B-02/B-03 零消费符号删除、3 处 #92 双刀收敛）净 +1；上游各段：W5/W6 收口 1777 + 后续波次 20 项 + gap9 快脚本产出波 162 项 + gap11 旧仓兼容快包波 190 项：`legacy_fast` 有界语义等待垫片（readyState/min_ms/锚点、预算制回退、绝不延长）、`fastplugin` 注入（等待垫片 + 截图抑制 + 逐调用 stats jsonl）、`--compat legacy-suite` 兼容产物（零 import atlas / vendored 字节锁步 provenance / 旧仓 fixture 面恰等七件套 / 标准 `.env` 相对 WEB 双形态）、跨切面判据 59 格 + 默认档修复波 22 项：#30/#31 `--fast`/`--compat` 空转修复、#33 忙→静判断据与 fail-safe 睡满、#34 配对判据格、#35 产物 README 正误、#36 等待腿撤出 `--fast` 默认（截图抑制保留）+ 调用点规则 9 项：`ATLAS_FAST_SITE_RULES` 按等待点语义条件放行（js/url/mask_gone/mask_cycle/never 五类，超时睡满，黑名单点禁提前），规则档显式 opt-in + gap12 自主探索波 36 项：`llm.py` OpenAI 兼容客户端（三 env 键缺席逐键点名、HTTP/连接/畸形响应绝不吞成空串、注入 transport 离线可测）+ `autopilot.py` 自主探索循环（白名单动作 + `classify_risk` 风险门 + 生产闸早于开浏览器 + checkpoint/compile 沉淀 + 决策留痕）+ `explore --auto/--goal` CLI 入口；同期删减审计 ruff 24→0 且全量不减项；计数唯一来源见 README 顶部徽章与 AGENTS §2）。真实 17 条已用 `atlas legacy --keyword` 分批跑完：
**13 passed / 4 skipped / 0 failed**（TC1 / TC2~6 / TC7~9 / TC10~13 / TC14~17 五批），
skip 全为已知环境或产品限制。2026-11-06 最终树复跑为 5 passed / 11 skipped / 1 failed——
偏差全部归因 SIT 主数据漂移（价格表/PT 字典/税率），非代码回归，逐批记录在
`docs/gaps/impl_pi/c3-baseline.md` §9。

### Q: 离线全绿就代表 Atlas 能用吗？
A: 不代表。本轮真实执行抓到两个离线发现不了的缺陷：
① 加载 `pages.py` 未把 `legacy-web/` 放进 `sys.path` → `No module named 'lib'`；
② 核心 Task 没带 `page_key` 等必需默认参数 → `open_doc_list() missing 1 required positional argument`。
2026-11-04 真机冒烟又抓两个：③ `atlas run x.yaml --mode debug` 文档口径被 argparse 拒收（全局参数只认子命令前位）；
④ `explore` 的 `✓` 标记在 GBK 控制台崩掉 `start()`（已 ASCII 化 + 直调路径补 UTF-8 兜底）。
同日 waits.py 真实 Chrome 冒烟再抓：⑤ `wait_for_rows` 用默认 `visible` 等表格，空结果时 `.el-table` 高度为 0 判 hidden，
「合法空表」第一步就超时——离线 fake page 的 `wait_for_selector` 是空实现，永远发现不了，改 `attached`（行数据由 vm 轮询判定）。
⑥ `learn_contracts` 读真实抓包用 `{"items": ...}`（离线 fake 形态），但 web-toolbox 扩展 v1.1.4 的 `net_list` 权威键名是
`entries`（bridge 工具实收 231 条抓包取证）——真实接线走 fallback 拿到 dict 本身、`isinstance(list)` 恒假、**静默登记 0 条契约**。
fake 与真实 schema 漂移时离线全绿也测不出，感知器官「模块存在但没接」又一例。改读 entries 优先（items 兼容旧 fake/裸列表），
计数按 registry 净增不按条目数（轮询同路径去重不虚报）。
⑦ `save_har` 无条件 `json.dumps(export_har())`：真实桥接**过滤无匹配时返回纯文本**「错误: 过滤后没有条目」（bridge 实测），
离线 fake 只返回 dict 永远测不出——错误文本被包成字符串字面量写进 .har，文件「保存成功」实为坏档，真因埋进引号。
同族：工具文档口径「返回 JSON 文本」（str）时二次编码顶层成字符串而非 `{log:...}`。现 str 先试解析、坏文本点名原文拒写坏档。
⑧ `console_errors` 把桥接异常形态折叠成空表：真实 `console_read` 该 build 返回**纯文本**「错误: 未知方法」（v1.1.4 实测），
旧实现非 list 一律 `return []`——排障时调查者读成「前端无错误」，实为感知器官坏了的假阴性。现合法 `{"items":[]}`/裸 list
如实返回，错误文本/无 items 的 dict/None 点名拒绝。（`replay`/`chain_replay` 在 src 无消费方——`ApiEvidence` 用业务方
自有的单接口重放 handler，透传诚实不动；桥接不支持时返回文本错误被透传给调用方，无假阴性消费链。）
⑨ #120 又两例 fake/真实 schema 漂移：`console_read` 真实权威键名是 `entries`（bridge-client
`{total, filtered, entries}`，与 net_list 同族），#105 修复时照 fake 只认 `items`——真实错误行被
点名「无 items」当场拒绝，**把正常路径翻成错误**（#115 教训反向面）；`net_list` 真实默认 `limit=50`
分页而 `total` 报全量（231 条实测），`learn_contracts` 拿一页登记一页零告警——部分结果伪装全量。
现 entries 优先/items 兼容、total>页大小点名「分页截断」拒绝残缺登记。fake 与真实 schema 漂移时
离线全绿也测不出，感知器官接线必须逐项对照 bridge 源码。
所以改完必须跑 `atlas run examples/atlas_core_verify.yaml` 与 `atlas_save_verify.yaml`。

### Q: 分批回归踩过什么坑？
A: 两个必踩点：
① `-k tc1` 会子串命中 `tc10~tc17`，必须写 `test_tc1_` 这种带下划线前缀；
② 删 `plan_ctx.json` 后 TC2~TC17 缺 `_tc1_ok` 会全部 SKIP，因此必须先跑 TC1 再跑其余批次。

### Q: 接口返回 HTML、假 400、5xx 这类接口问题怎么判读？
A: 见 `docs/api-pitfalls.md`（接口测试常见问题判读手册）。核心速记：接口 200 但 body 是整页
HTML=会话过期重定向到登录页（`abilities.py` auth_expired 检测，头或正文像 HTML 都拦）；form 串
走 `json=` 得假 400（发送语义错非接口坏）；状态码归因表在 `failures.py` `api_evidence`
（401/403→SESSION、≥500→ENV_BLOCKED、契约词→CONTRACT、查无→DATA）。

### Q: heal 模式会自己改代码吗？
A: 不会。只有「只读 Task + 命中已审核知识（`status=approved` 且有 remedy）」才就地重跑一次；
写操作永不自动重试，未审核知识也不触发，失败仍原样抛出。
`match_pitfall` 有数据完整性防线：**`trigger` 为空的坑点永不匹配**（否则 `"" in text` 恒真，
一条手写残缺知识会命中所有异常、触发对无关失败的重跑）。自定义知识库每条 pitfall 必须带非空 `trigger`。
自愈准入走 `match_pitfall(status="approved")`——**过滤在「最长 trigger 择一」之前**：一条 trigger 更长的
pending 草稿不会遮蔽同一异常上已审核的坑点（否则正当自愈被静默压制）；诊断/失败归因仍取最长匹配供人审。
控制流异常（KeyboardInterrupt/SystemExit/GeneratorExit）一律拒绝自愈（#108）——终止信号不是业务失败，
即使知识里有人审批准的 `KeyboardInterrupt` trigger 行，Ctrl-C 也照常传播，绝不就地重试吞掉中断。

### Q: 探针为什么扫描多页？
A: 销售单列表默认第一页几乎全是草稿 `1001`，只查第一页会误判“无来源单”。
`SalesOrderProbe` 有界分页（默认 200/页、最多 10 页），未命中短页即停止。
另：绕过 `DemoApi.__init__` 时必须补 `STATIC_HEADERS`，否则接口回 `BUG-920 app is null`。

### Q: 「上周能跑今天炸」怎么定位是版本还是知识漂移？
A: 看三元组，不靠回忆。每次 run 记录与 Allure parameters 都带 `引擎版本 + 知识库内容 sha1 指纹
（private/builtin/unreadable）+ flow 摘要（文件 md5=source / 内存派生=derived）`；
`atlas summary` 的 `versions.drifted` 直接点名「这份记录里哪个维度出现过多个值」，
`atlas doctor` 的 `version_triple` 报本机当前值（含 `tz_data` 体检，Windows 缺 tzdb 时点名
`pip install tzdata`，不静默按 UTC 跑）。知识库版本取**目录内容指纹**而不是 git sha——
私有库是人手改的，未提交改动 git 盖不住。已知口径（不是缺陷）：`git autocrlf` 改变文件字节时
指纹随字节变，跨机对比以 `source`+`files` 计数为辅；网盘/同步盘的字节抖动会带来指纹噪音。

### Q: 定时巡检怎么接 OS 调度器？Atlas 有自己的守护进程吗？
A: **没有守护进程**——`atlas schedule run <id>` 是被 crontab / schtasks 调起的一次性薄命令，
跑一轮就退出。登记任务后 `atlas schedule crontab <id> --platform linux|windows` 打印成品行
（Linux=expr 逐字复制进 crontab；Windows=`schtasks /SC MINUTE /MO <最小间隔>`，`/TR` 内层引号
已 `\"` 转义），Atlas **绝不代注册 OS 任务**（那是 Ask-first 边界）。睡眠/关机错过的轮次：
≤2min 准点、>2min 且 ≤7d 补跑**最近一轮**（`schedule status` 答「错过几轮」）、>7d 丢弃、
**改配置当场重置水位线**（不回放历史）。连败 5 次自动停用并留原因——timeout/rc≠0 计败，
Ctrl-C 不计败（CONTROL_FLOW 调度域第二实现）、**rc3=等人同样不计败**
（outcome `waiting` 走 `keep`：不 +1——否则一次人工介入就把「等人正需要人来看」的巡检
AUTO-PAUSED 停掉；也不清零——前面真失败攒下的连败不许被一次等人抹掉；`schedule run` 对
waiting 答 rc 0，水位线**照常推进**，与 defer「没执行所以不推进」是两种语义，各钉一条测）。同机双开撞轮次靠 O_EXCL+心跳 TTL 租约互斥，
**占用中是推迟不是跳过**（不推进水位线，OS 下一 tick 还会来）。两条硬提醒：cronie/schtasks
只按**本机墙钟**调起、不认任务里的 `tz`（tz 只用于 Atlas 侧窗口/水位线判定），NTP 漂移 >2min
自然表现为 catch-up；`reports/schedule/` 必须在**本地文件系统**，同步盘/NFS 上 O_EXCL 不可靠会双跑。
默认只读档：含写步骤的 flow 要显式 `--i-accept-writes` 才入库，且调度器拼的 argv **永不带
`--approve`**——写档 flow 的批准永远是人的决定（红线 #13）。详见 README「定时巡检接线」。

### Q: 吸收外部资产/合并前该做什么？边界账本坏了会怎样？
A: **吸收/merge 动作之前**跑 `PYTHONPATH=src python scripts/boundary_classify.py --diff <入站变更集>`
（事后无法用 diff 还原入站集——这是义务不是可选项）。分类轴 cosmetic/structural：cosmetic 自动放行
（`AUTO`）；absorbed/engine-owned 命中 structural 进 `atlas review` 人审塔——`boundary_review` 草稿
**approve 点名拒绝**（trace_step/code_review 同族，不自动写知识），处置方式是人工改判账本条目或
decision_log 后重跑分类。边界事实与债标价在 `docs/ownership/atlas-boundaries.json`（**数据不是引擎
字面量**，红线 #11：引擎运行时永不解析；doctor 与分类 gate 两个薄消费方共用 `atlas.boundaries` 一把
判据尺）。`atlas doctor` 口径：账本存在但坏 → rc1（漂移）；文件缺失 → 如实 `present:false` **不改
rc**；`optional` 区域（私有知识/迁移产物，gitignore 排除）全缺只 warn。accepted 债必须带
decision_log 说明何时为何付代价——「看似错误实为刻意」的决定靠尾部决策防被下一轮干净合并带回来。
详见 README「资产吸收与边界账本」。

### Q: 迁移/生成的产物里为什么看不到鉴权值了？
A: 那是 C4① 字面敏感头闸在工作：字面量凭据换成 `${ATLAS_HDR_<n>}` 占位（DSL 既有 `${...}` 插值承接），
人审把值放 `.env` 后照常可跑；清单只报「第几步哪个头 → 哪个 env 键」，值永不进 YAML/stdout/stderr。
占位没填就跑会被 compile/运行期的未解析占位符防线点名，不会被当字面量发出去。
同理，落盘的异常文本与框架写进异常的 `page.url` 已过净化刀（URL query 逐键 redact、业务参数保留），
而**失败归类读的是净化前原文**——否则 `?code=401` 会被脱敏翻成 unknown。

### Q: 仓库是通用 demo，怎么接回公司真实项目？
A: 用 `ATLAS_*` 映射覆盖默认值，不改引擎代码：`ATLAS_ENV_PREFIX`（键前缀）、
`ATLAS_LEGACY_PROJECT` / `ATLAS_LEGACY_WEB`（旧项目根与 web 子目录）、
`ATLAS_LEGACY_TEST_NODE`、`ATLAS_DEMO_API_PROJECT` / `_FILE` / `_CLASS` / `_SPEC`、
`ATLAS_APP_HEADER_KEY` / `_VALUE`（业务识别头）。`atlas doctor` 会回显
`env_prefix` / `legacy_web` 并逐项校验；默认值即虚构 demo，不改也能跑离线。

### Q: 调试怎么接旧项目习惯（attach 接管）？
A: 同 legacy-suite 协议：`CDP_DEBUG=1 CDP_PAUSE_STEP="9"`（或 `"TC2:9"`）+ `--mode debug` 跑，
步骤前冻住，另开终端 `playwright-cli attach --cdp=http://localhost:9222`。
差别：旧项目 Enter 后中断退出，Atlas 是**人工干预完继续跑完**；非交互环境 EOF 自动放行，CI 永不挂起。
2026-11-04 真机冒烟已过：冻住时 CDP `/json` 可见真实页面、Enter 后继续；同轮修复了
`atlas run x.yaml --mode debug` 全局参数只认子命令前位置的 argparse 缺陷（回归测试钉死）。

### Q: 失败草稿和探索候选谁来审？
A: 都汇进一个塔：`atlas review` 列证据卡片（30 秒能判断）。`--approve` 按 kind 分发：
坑点进知识（`--remedy "…"`）、`selector_candidate` 进组件库
（`--component <组件> --element <元素> --remedy "<选择器>"`，直接写回 `components.json`；
gap13：定位可以是 CSS 字符串，也可以是结构化描述——ARIA 候选直接
`--role button --name "保存"`（等价 `--remedy '{"role":"button","name":"保存"}'`），**不必手抄成 CSS**；
role 走精确匹配；形态判据在 `atlas.locators`，坏形态在批准时点名、绝不静默当成 CSS 字面量）、
`trace_step` 拒之塔外（人工写成 flow YAML 后 `atlas compile`，或 reject）。`bu_overlay`（坐标差异）批准时 `--remedy` 填目标 BU 真实值，写私有 overlay 库。
`flow_candidate`（跑绿固化的动作序列，落库线）批准时 `--remedy` 可选填库名（缺省用 case 名），写私有公共任务库 `task_library.json`，`atlas library` 可查。
`--reject --reason` 留痕不删，`--stats` 看 pending/approved/rejected 与积压分类。
C2 证据闸（**重构波松绑**）：默认**不再强制**可批准 kind 带 `evidence.verification` 四元组——
缺证据草稿照常批准（**本轮有意的行为变更，风险自担**）。想要旧的硬保证（缺证据一律拒批）
加 `--require-evidence`；开闸下人工豁免仍走 `--allow-unverified --reason`（必留痕），
批量 `--apply` 同受 `--require-evidence` 约束。闸本体（`verification_problems` + override）未删。
批量清塔：`--kind` 过滤 + `--group` 聚类视图（批量类压成按证据源分组的紧凑表，坑点仍全卡片）；`--template` 导出可编辑决策文件（人填组件/元素/选择器或 remedy）、
`--apply` 执行——批量批准仍逐条走 approve 分发与留痕，没填的行失败点名且 exit 2，不是自动批准；
决策文件里被编辑成非映射的行（数组/null，合法 JSON 但形态不对）同样点名不顶整批（#109）；
`--reject-kind <kind> --reason` 整类驳回。
同 task+异常类型+分类的草稿自动合并计 `seen`，同坑复发不重复占塔；
正式坑点 id 用内容 sha1（不是内置 hash），重复批准只替换不堆条目。
自愈准入对控制流异常免疫（#108）：KeyboardInterrupt/SystemExit/GeneratorExit 一律不重试——
即使人审误批准了一条 trigger 恰为 `KeyboardInterrupt` 的知识行，Ctrl-C 也照常传播，不被自愈吞掉。

### Q: 跑绿的用例怎么沉淀成可复用资产？（落库线）
A: 两条腿，都只进塔不直接写知识（红线 #9/#10）：
① **自动固化**：flow 声明 `harvest: true`（或 `atlas run --harvest`）且**整条跑绿**后，
动作序列原文（steps/assert，含 `${var}` 占位不改写）+ C2 成功三元组（`verdict="pass"`、
`exit_code=0`，与失败路径同一把 `engine._verification` 刀）自动固化为 `flow_candidate`
草稿进审核塔；失败/中断/env_blocked/waiting 一律不固化，固化写盘失败只 WARN 不翻 run 终态。
同一 flow 重跑只增 `seen` 刷新证据，flow 改了才出新 id 新行。
② **公共任务库**：批准 `flow_candidate`（`atlas review --approve <id> [--remedy "<库名>"]`）
写私有知识根 `task_library.json`（按名唯一，同名再批准=更新）；`atlas library` 列表 /
`atlas library <名>` 查看条目（steps/assert 原文可作 flow 骨架复用）。无证据的手工草稿
过不了同一把三元组闸（红线 #13）。

### Q: Atlas 产出的脚本能不能像旧套件那样快跑？怎么做到、边界在哪？
A: **机制已对齐并离线钉死；真机面已有实测（用户拍板只跑 R1/R6），产能结论仍未到**——**R1**（真实 SIT，
同一条 TC1 写用例）：Atlas 桥接轨墙钟 **147s** vs 旧套件进程墙钟 139s，**净增约 8s（≈6%）——桥接轨不比
旧套件快**；机制清楚：`legacy_case` 委托旧 `step_N` 本体、**继承全部固定睡眠**，语义等待的收益在这条路
拿不到（且两者同点同因失败于 step 7，时长只到同一失败点，写成完整链路产能对比即为失真）。**R6**（会话层，
各两次取样）：launch 29.75/31.49s vs attach 6.18/8.28s——**会话获取成本差约 23s/模块**；在场闸与
`ATTACH_MATCH` 点名选页两道闸真机成立。数据与复现命令：`reports/r1r6_20261109/SUMMARY.md`（gitignore 内，
本机证据不外发）。**接线后的同链路 UI 产能仍未测**（UI 腿恒 `NotWired`，待 R2 授权）——「快模式已验证比
旧套件快」这句是假的。

怎么做到（旧套件三大耗时来源：每用例重复登录、每步截图、固定 sleep 合计约 154s/套件——见
`docs/gaps/gap9-fast-pytest-runner.md` §A 取证）：
- **单浏览器/单登录**：产物 conftest `sole_page`（module scope）一次 launch，`get_page` 复用页面；
  登录判定以**页面指示器实查**为权威，plan_ctx 里的 `_logged_in_account` 只记录不放行（进程重启后
  浏览器全新，磁盘标记必须被实查否决）。
- **语义等待替代固定 sleep**：底座带 `wait_for_rows`/`wait_for_mask_gone`/`wait_for_vue_field`/
  `wait_for_url_change`（`waits.py` 同源冻结派生 + 锁步测防漂移）；产物用例层禁词判据按**实参形态**
  （`wait_for_timeout` 只许裸 `TICK_MS` 小 tick，乘算/字面毫秒/`time.sleep` 皆红且有对抗样本测），
  不按调用计数——判据强度取决于它拒绝什么。
- **成功不截图**：只有失败腿取证（旧 `runner.py` 成功也截图是要规避的反模式，非要抄的资产）。
- **跨批上下文 + 看门狗 + 写闸**：`plan_ctx.json` 白名单标量、缺前置=EnvBlocked 点名不静默跳；
  用例看门狗超时写 `status:"timeout"` 独立终态（不洗绿）；写腿默认拦停记 env_blocked。
- **报告链**：产物写 Atlas 兼容 `runs.jsonl`（run 级 reason 只取框架文案），`atlas allure` 直接可吃；
  依赖栈 `pytest + playwright`（playwright 是既有依赖，能力本体就是浏览器驱动；不引 allure-pytest/
  pytest-playwright/pytest-timeout/requests）。
- **UI 腿可执行（2.2 本波）**：组件库可选键 `actions` 直出可执行页对象方法 `do_<元素>()`；无 `page_object`/
  `legacy_pages` 声明时按组件库 `actions` 的 `task` 字段 auto-wire（引擎不猜业务命名）；老知识（无 `actions`）
  产物逐字节不变，未接线动作仍 `NotWired`→env_blocked。
- **分片（2.3 本波）**：生成期 `--parallel N`（只写进产物 README 用法）/ 运行期 `ATLAS_FAST_PARALLEL>1` 走
  pytest-xdist——**缺 xdist 运行期点名报错、绝不静默降级为单进程**（档位>1 须配 `--fast`）。
- **失败腿 trace 归档（2.4 本波，红线 #2 显式例外）**：`ATLAS_FAST_TRACE`（默认关=成功腿零开销）**+**
  `ATLAS_FAST_TRACE_ACCEPT_SECRETS` **双确认**才开启——仅设前者只打 `WARN: TRACE-ACK-REQUIRED` 不开；
  开启后失败腿落 `reports/traces/<run_id>.zip`，路径与 `trace_secrets_ack:true` 写进 `runs.jsonl`；**zip 含密码，勿外发/勿入库**。
- **浏览器矩阵（2.5 本波）**：`ATLAS_BROWSER_CHANNEL=chromium|chrome|firefox|webkit`（缺省 chromium；chrome→系统 Chrome channel）。

边界（谁在什么面说了算）：`atlas gen --fast` 的文档源恒 API 腿——**快 UI 腿只经
`atlas export-pytest <flow.yaml> --fast` + 显式声明位可达**（`page_object` 已批准组件名 /
`legacy_pages` 旧函数名，互斥；形态判据与 `atlas compile`/`run` 同源一把尺，引擎不猜业务命名）。
未接线的 UI 动作运行期抛 `NotWired` 归 env_blocked 点名组件/元素——不假绿、不混进缺陷分布，
两种下一步（「补 env 键」vs「找人接线」）在 reason 里分立。CDP attach 会话复用（比 launch 再快一档）
需人在场三条件齐（见「关键模块」emit_fast 行），且**attach 的前提是人在场且该会话即目标账号；
产物不替人核账号，账号错配的责任在接线的人身上**（约束说明，非缺陷）。离线全绿≠能力可用：
真机面：R1/R6 已测（见上），R2/R3/R4/R5/R7 待授权由 lead 执行归档；产物机需装 bundled chromium
（`python -m playwright install`）或显式设 `ATLAS_BROWSER_EXECUTABLE`，否则 launch 通道开箱即失败
（旧套件靠 `channel="chrome"` 复用系统 Chrome，产物侧尚无此旋钮——加旋钮待下一波裁，F-33.4.2）。
产物含真实 URL/flow 内容，
`exports/` 已在 `.gitignore`（产物含真实 URL/账号名/私有库派生选择器，误入库面已封；仍建议放受控目录）。偏离与三态全表见落地注 G 节。

### Q: 想让旧套件「一行不改旧仓代码」就变快——gap11 的机制是什么？现在能做到哪一步？
A: **先说结论（本波口径）**：机制在位且离线判据闭合，但**「比旧套件快」这个结论已在真机 A/B 后被撤回**——
真机三轮（六组）+ 8 组档位探针的结论是**等待垫片没有任何一档同时做到「失败点不移」与「有可当成绩的收益」**：`mask`/`mask+xhr` 会**间歇**前移（第三轮 r1 三组全 step 7、r2 两组同时前移到 step 3，同码同配置一轮全过一轮全挂），
`mutation` 安全但 `saved≈0`（等于没提速），`mask`+`min_ms=3000` 3/3 同点但省下的 1–2s 落在基线跨批噪声（93–177s）内、且本质是「更短的固定等待」。按 G11-1 硬规则（前移即判行为变更），
**#36 裁决：等待腿从 `--fast` 的默认置位里撤出，改为显式 opt-in；截图抑制保留默认**（它不改变任何等待，G11-2 非破坏差分已判成立）。
所以这条目前只能当作**机制**介绍，不能当作**收益**推荐；证据档案 `reports/gap11_ab_20261109/`（前两轮）与 `reports/gap11_ab_r3_20261110/`（第三轮 + 探针矩阵，`SUMMARY3.md`）**不得删除**，索引见落地注 G.6/G.7。

立论基线（R1 实测证否）：**桥接轨不快**——`legacy_case` 整条委托旧 `run_case_py`，继承旧代码全部固定睡眠。
剩下唯一可动的面只有**旧仓代码之外、运行期替换「等待」与「成功截图」的实现方式**，即 gap11 的两把机制（红线 #4：旧仓零改动）：

- **有界语义等待垫片**（显式 `ATLAS_FAST_WAITS=mask|mask+xhr|mutation` + `atlas legacy --fast` → `-p atlas.fastplugin`；类级替换 `playwright.sync_api.Page.wait_for_timeout`）：
  固定睡眠换成「`readyState==='complete'` ∧ 无可见 `.el-loading-mask`（**全枚举**，不是只看第一个）∧ 可选递加档
  （xhr 在途=0、mutation 静默窗）∧ 可选锚点可见」的**进程侧轮询**（`_TICK_MS=25`，不用 `wait_for_function`、
  不再用 `wait_for_timeout` 以免递归）。**硬判据（#33 收紧后）不可谈判**：绝不延长（预算制，整段从未观测到忙
  就恰好睡满原 ms，该笔在 stats 记 `fallback=no-busy-observed`）、**只有「本次窗口内确实观测到过忙→再转静默」
  才允许提前返回**——上面那组条件只是**必要非充分**（旧口径把「没有遮罩可等」读成「页面已静默」，这就是
  失败点前移的根因）。**「忙」只有三个码**：`busy-mask` / `busy-xhr` / `busy-mutation`；`not-ready`（readyState
  没完）与 `not-anchored`（锚点没出现）**是「门」不是「忙」**——挡住放行但永远给不出提前许可，`no-hook`/`no-activity`
  同属门；**非字符串返回值（含旧布尔协议 `True`）一律按门 fail-closed**，因为布尔表达不了「曾忙碌」——
  宁可睡满（慢）也不放过「压根没活动」（错）。probe 异常回退睡满剩余预算并计数。`ATLAS_FAST_WAITS` 在**垫片本体侧默认关**（env 未置位=逐字节旧行为；
  未知值 WARN 后仍按关——「默认关的开关不因疑似拼写启用」），**`atlas legacy --fast` / `--compat` 产物也不再代置位它（#36）**：
  默认清单只剩 `ATLAS_FAST_NO_STEP_SHOTS=1`；未设等待档时 `--fast` 会打一行 NOTE 点名，dry-run 三态显示 `unset`。
  要提速必须**显式设档位**并自己过三关（见下）。`ATLAS_FAST_MIN_MS`（默认 400）与
  `ATLAS_FAST_SETTLE_ANCHOR` 是第二道兜底；**第一道是上面那条 fail-safe**——登录/语言切换/OU 沉降这些段落
  本来就没有遮罩，只靠兜底层挡不住（真机已证）。
- **成功截图抑制**（`ATLAS_FAST_NO_STEP_SHOTS`，`--fast` 默认置位）：替换 `allure.attach`，名字匹配 `step<N>_result` 才丢+计数，
  `step<N>_FAIL` 与其余名透传；失败另有字节直写落盘不经 attach，故失败证据三重不减。诚实边界：省的是
  **attach 体积与 IO**，省不掉 `page.screenshot()` 的 PNG 编码耗时。
- **记账**：`ATLAS_FAST_WAITS_STATS` 逐调用 jsonl（`caller_file/line/func`、`requested_ms/actual_ms`、`probe`、
  `fallback` 七键**不加不减**，两类事实共用 `fallback` 单字段、`;` 连接）；**它同时是判据面不是成绩面**——
  第二轮 A/B 就是靠它把根因钉死的（`6000→400`、`5000→533`、且当时 `fallbacks` 恒 0）。
  **收紧后 `fallbacks` 与 `no_busy_observed` 是两个语义分立的数**：前者=**真回退**（探针错误 / 无 evaluate /
  不可恢复 / 档位降级），「`fallbacks=0` ⇒ 探针从没出错」这条旧读法**继续成立**；后者=**fail-safe 睡满**的笔数，
  是判据 2 的正常路径**不是故障**，收紧后它会单独上升——**别把它读成问题，也别把两者相加当「异常数」**。
  `by_stage` 只按实际生效档位归集，**note 不进档位键**。「固定睡眠实际少睡了多久」只从这份 jsonl 派算 Σrequested vs Σactual，
  **静态求和只是理论上限**，`saved_ms` 为负照记不修饰；**省下的毫秒里混着本该等的真等待，所以它不构成产能结论**。
- **旧仓可直接吃的布局**：`atlas export-pytest <flow> --fast --compat legacy-suite`（`class Test<Suite>Plan` +
  `@pytest.mark.order(N)` + 自带 `pytest.ini`（`--strict-markers`/注册 `order`/`-p no:playwright/no:randomly`）
  + 同名七件 fixture 面 + **按字节 vendored 的垫片两份**（provenance 头带 sha256，源改了不重生成 → 判据测必红））。
  两条第一红线：**产物零 `import atlas`**（旧仓机器没装 atlas；AST 语句行 + 文本双扫描 + 沙箱自证），
  **绝不写旧仓字节**（非破坏差分=跑前源面快照、跑后 diff 必须为空）。`--compat` 必须配 `--fast`，否则 rc 2 **零产物**。

边界（如实，别读成「已验证更快」）：① **改动面只在运行期垫片**，旧代码内部逻辑一概不碰；垫片能触达的只有
「等待」与「成功截图」两处调用点，**这两处省下多少目前不成立为成绩**；② **A/B 已跑三轮 + 8 组档位探针、三量均无「可当成绩」的数**
（第一轮 fast 组垫片空转=**没测**；第二轮真实运行：墙钟=测了但基线跨批噪声量级 ≥ 组间差、
截图张数=测了且判定成立但它是**边界判据不是产能数字**、jsonl 派算=省是真的但**削掉的是真等待=语义差异**；第三轮同一轮内 r1 全过、r2 两组前移，直接证明**同配置不稳定**），README「当前验证边界」的 gap11 条目因此**不设占位符**——本 FAQ 同样不设数值位；兼容包端到端
仍待 #29 授权；③ **语义等待的 flake 面已从风险变实证**：无遮罩守护的等待被旧口径当可提前，收紧后
「从未见忙→睡满」；`mask` 档在「遮罩消失 ≠ DOM 数据就绪」的数据敏感链路上仍有残余盲区（第三轮实测），档位差与连跑稳定性只能真机定，不口头保证；④ 生成期尚未接 atlas-free 硬拒（这道闸现由判据测
与 xcheck 消费，要变成「生成期当场拒写」属新增改动，待裁）。**重测三关（次序固定，前一关不过后面不作数）**：**①失败点与基线同一步**（真机）——前移即判行为变更，后面全不作数；**②装配生效 + 判据不是 no-op，两半缺一不可**：**2a 真机·装配生效证据**=`ATLAS_FAST_WAITS_STATS` 指定的 jsonl 存在且 `calls>0`，或收尾 summary 行出现且 `calls>0`（**这是第一轮 A/B 的原始判据**——那轮「既无 summary 行也无 stats 文件」= 垫片压根没装；没有这一关，整轮可以完全空转：垫片不装 → 行为与基线逐字相同 → 第①关恒过 → 看着全绿其实什么都没测。要 `calls>0` 而不是 `saved_ms>0`：**前者证明垫片确实拦到了等待，后者要求它必须省**，那是另一件事且可以合法为 0）；**2b 离线·判据正确性**=配对格全绿 `test_p0_mask_appears_then_vanishes_returns_early_never_appears_full` × `test_p0_never_busy_page_sleeps_out_requested_original_never_called`（三档版 `test_p0_never_busy_sleeps_out_requested_all_three_tiers`）——**真机观测区分不了「这条链路本来没机会」与「实现坏了（no-op）」，能区分的只有这些格**；**③才谈墙钟**（多轮/中位 + 同失败点）。**`saved_ms` 与 `no_busy_observed` 都是报告量**（写进 A/B 记录，看这条链上有多少等待真被忙信号守护过），**不作任何一关的通过条件**：真机 `saved_ms` 接近 0 是**合法结果**（被守护的等待本来就少），既不构成失败，也**不构成把判据放宽回去换数字的理由**——拿它当通过条件，等于逼人造数字，正是这条闸要防的反面。完整三态表与逐格测索引（一律函数名）在
`docs/gaps/gap11-legacy-fast-compat.md`「G. 落地注」；跨波次同步义务与「刻意不复制」四条见同节 G.2。

### Q: 真实内部知识/BU坐标放哪？仓库怎么保持干净？
A: 设 `ATLAS_KNOWLEDGE_DIR` 指到仓库外目录：读逐文件回退包内虚构默认（fallback），
人审批准（组件选择器、坑点、`bu_overlay` 坐标）只写私有目录——包内文件零改动。
首个批准会把合并后的整份文件写进私有（copy-on-write），此后私有版本遮蔽包内同名文件；
同步包内虚构默认变更需手动删私有对应文件。真实 BU-B 坐标到位后用 `atlas overlay <file> --diff BU-A BU-B --to-tower` 进塔。
已批准的会话坐标用 `atlas run <flow> --bu <BU名>` 接入执行链（`ATLAS_SESSION_*` 显式 env 优先；非会话键只做数据参考不进执行路径）。

### Q: 新增第二个公司的业务怎么做？
A: 新建 `plugins/{domain}`，提供 Task、Probe、知识库与必要驱动适配；Actor、DSL、执行、记录、分类引擎无需随业务重写。

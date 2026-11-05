# CLAUDE.md — Atlas 开发者速览

完整行为约束见 [AGENTS.md](./AGENTS.md)，冲突时以 AGENTS.md 为准。

## 项目概览

Python 3.11+ / Playwright(sync) / requests / PyYAML / pytest。Atlas 用 Screenplay 统一 UI 与 API，用业务插件承载知识，用旧项目适配器保护已有资产。

- 当前目录：`D:\GitHub\Atlas`
- 包：`src/atlas`
- 业务插件：`src/atlas/plugins/demo`
- 外部资产：`../legacy-suite`、`../legacy-api`，只读复用
- 业务会话默认值（账号/OU/语言/division）唯一来源：`plugins/demo/knowledge/session.json`（仅账号名，无密码）

## 常用命令

```bash
python -m pip install -e ".[dev]"
python -m playwright install chromium
atlas doctor
atlas tasks
atlas compose --goal document_no
atlas legacy --dry-run
atlas legacy                         # 在旧项目目录真实执行
atlas compile examples/return_from_so.yaml   # 离线校验 flow YAML（不登录）
atlas review                         # 审核塔：待审草稿证据卡片
atlas review --kind selector_candidate  # 按类清塔
atlas review --group                 # 聚类视图（批量类紧凑表）
atlas review --kind <kind> --template mapping.jsonl  # 导出决策模板，人填后 --apply 批量批准
atlas review --reject-kind trace_step --reason "…"   # 整类驳回（原因必填）
atlas review --stats                 # 队列吞吐
atlas review-code tests/             # 代码质量评审：扫描 pytest/DSL/契约，输出含评分 Allure 报告
atlas review-code tests/ --review-mode knowledge  # 知识库增强（仍离线）
atlas review-code tests/ --format json --fail-under 75  # CI 门禁
atlas allure reports/runs.jsonl --out reports/allure-results  # runs.jsonl → Allure 结果集
atlas allure-analyze reports/allure-results                  # 解析 Allure 结果集：分类汇总
atlas allure-analyze reports/allure-results --to-tower        # 未命中项登记 pending_review 进审核塔
atlas explore                        # 非生产 CDP 探索会话（外部大脑接管）
atlas explore --compile <trace.jsonl>  # trace → pending_review 草稿
atlas init <name> --system <s> --module <m>  # 生成新项目骨架
atlas config add <domain>             # 生成插件骨架或映射片段
atlas gen <doc> [--scenario a|b|c|d] [--strict] [--check-only] [--to-tower]  # 文档→测试代码（只生成不执行）
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
| `allure_report.py` | Allure 2.39.0 结果集产出（Gap5 §3 方案 A 纯 stdlib 直出，零新依赖）：`emit_allure` 把 runs.jsonl 转 `allure-results/atlas-*-result.json`；§3.5 状态映射——passed→passed、failed→failed+`atlas.fail_class` label、env_blocked→skipped+`atlas.blocked=true`、interrupted→skipped+`atlas.interrupted=true`、unknown→broken（红线 #7：env/操作终态映射 skipped 但带 label，不洗绿不进 failed）；historyId=sha1(env|case) 稳定跨运行聚合、uuid 每份独立；读侧复用 reporting 容错纪律（#131/#112：坏字节 replace 回落、残行/非对象跳过计数）；clean 只删 atlas-* 前缀不误删用户附件；附件缺失/坏字节降级 text/plain 占位（#126/#64：报告可以有噪音不能丢整份）。#139 生产侧接线：`attachment` 键此前无任何生产方——engine 三处 `capture()` 返回值全丢弃，真实 runs.jsonl 永远 0 附件（消费侧就绪但没接=断链，#106/#86 同族）；现 engine `_record_shot` 把截图按 root 相对写进 step 记录（run 成功 None 不写键，红线 #8 零涟漪），CLI allure 显式传 `attachments_dir=config.root`（旧回落 runs_path.parent 把 root 相对引用假报「无法读取」占位） |
| `allure_analysis.py` | Allure 结果集解析（Gap5 §4 汇入侧）：`load_allure_results` 读 `*-result.json`（坏字节 replace 回落、坏 JSON/非对象跳过计数 #131/#112，绝不因一条坏结果整份崩）；`parse_allure_result` 带 `atlas.fail_class` label 直接采用（自产往返），否则 `classify_text(f"{message} {trace}")` 归类（外部 SCS 结果）；`analyze_allure` 汇总总数/通过/失败/跳过 + fail_class 分布 + 四桶（§6.3 可配置分组不写死引擎——红线 #11）+ 命中坑点 + 自愈提案；`to_tower=ExperienceJournal` 时未命中项登记 pending_review 草稿进既有审核塔（不新开塔、不新增知识写路径——红线 #9/#10）；自愈提案只提案不执行，控制流异常（interrupted）一律拒自愈（#108）；`--to-tower` 走 ExperienceJournal，`--format text` 人读版，rc2 用户错误 |
| `actor.py` | Actor、Ability 注入、`attempts_to`、ctx（产出写入统一走 `core.write_produces`：produces 声明与 perform 实写不符当场 KeyError 点名声明键与实际键——旧兜底 `key in result else result` 把谎报 Task 的**整个 dict** 静默塞进 ctx，下游把 dict 当单号，烂数据漂到远处才炸；engine 两条执行路径与 skip 步骤同一契约，skip 不写产出。#124 三面探针 sound：`using` 缺失点名类型名+保留 cause（#90 口径）；`can` 同类重注入 last-wins 无消费方触发（session.actor() 每类型恰一个），非可达缺陷不强改（红线 #12）；`attempts_to` 半途崩不回滚已完成产出——前序写 Task 真实发生过，抹产出=销毁追责证据（#85/#110 同族），补测钉死顺序执行/原异常传播/ctx 部分保留三契约，防将来改整体回滚静默抹证据） |
| `core.py` | Task 协议与强制元数据（`__post_init__` 落地 `tuple[str]` 契约：`produces=('value')` 漏尾逗号逐字符漂移当场构造期点名「带尾逗号」，非 tuple/空元素同拒；`write_produces` 产出写入唯一真相——声明键不在 perform 返回 dict 里立即 KeyError，谎报不再静默把整个 dict 塞进 ctx。CallEndpoint/DemoApiOperation/LegacyStepTask 改为自兑现 {save_as: 结果}（ctx 形态不变），封装类均 `tuple()` 转换 DSL list 安全。#125：`FunctionalTask` 的 action 非可调用（字符串/None）构造期当场 TypeError 点名类型+值+intent——旧实现一路放行到 `perform()` 才裸 `'str' object is not callable`，不提 Task 名归因漂成引擎缺陷（#119 面B/#91 同口径）；lambda/bound method 合法路径零涟漪。两级门面 `atlas`/`atlas.screenplay` 的 `__all__` 与真实符号逐项核对 sound（缺失=[]），防 `import *` 静默缺符号） |
| `abilities.py` | Playwright page、REST、Cookie（烘焙只认精确 host 与「可注册」父域：裸 TLD（com）与二级公共后缀（com.cn/co.uk 等精选表，#141 面A——旧判据 `"." in domain` 只挡前者，domain=com.cn 对 host=ics.example.com.cn 命中即凭据发向任意 *.com.cn 他注册域，违 §3 红线）均拒绝；域名比较/存储归一下写（#141 面B——真实 SSO 大写 `Domain=EXAMPLE.COM` 旧被大小写敏感静默丢弃，auth 失败只见 401 不见根因；无关大写域仍拒不是无脑放行）防跨站泄露）、DemoApi 适配（模块路径缺失/类名不存在均点名 `ATLAS_DEMO_API_PROJECT/_FILE/_CLASS` 与环境变量——旧实现裸 AttributeError「module has no attribute」用户不知改哪个键；spec_file 缺失是外部类 `_load_spec` 语义，诚实不越界。#122 面A：类存在但不符合客户端协议——`ATLAS_DEMO_API_CLASS` 手滑填成同模块 Service/页面类（如 IcsService 而非 IcsApi），旧实现到 `api._load_spec(...)` 才裸 `AttributeError 'no attribute _load_spec'`，不提 env 键冒充框架缺陷（#103 延伸：类存在≠可用；#63 注册≠可用同族）——现装配前 `callable(_load_spec)` 检查，ImportError 点名模块+类名+ATLAS_DEMO_API_CLASS，符合协议的类正常装配不翻错）、会话失效识别（#122 面B：auth_expired 旧判据只看 `Content-Type: text/html`，异常网关/mock 回 200+整页 HTML 不带该头时判 ok=True，登录页伪装成业务响应（#102 同族假绿）；现判据收敛「头或正文像 HTML」，正文判据只在 JSON 解析失败落 raw 时求值——首版无条件读 `.text` 让只带 json() 的 fake 崩出 AttributeError，收敛进 raw 分支既拦坏形态又零涟漪，Content-Type 原判据保留不误伤合法 JSON，#115 教训）。会话保活（尽力而为：回调抛错不顶掉已成功响应，返回 False 留后续重试；#141 面C：旧检测只认 auth_expired 标记——裸 401+JSON 网关应答（ICS 过期会话最常见形态）不带标记，宣称的「api 401 自动重登」永不触发、会话死亡不复活；现 401 状态码为权威判据，403/500/200 不误触发）。`request` body 按 requests 语义分流：`dict/list`→`json=`、`str/bytes`→`data=` 原样（JMeter form 串产物一律 `json=` 会被包成带引号字符串发出得假 400），其余类型发送前点名拒绝 |
| `composition.py` | `compose` 与 `compose_for_goal` 依赖编排（多生产者同产出键显式报错点名，不再 dict last-wins 静默选一个——遮蔽可行路径或把读目标翻成写操作；同名重复注册按同一产出方去重不误判冲突——但去重比对 frozen metadata **等值**（#136：旧只比 name，两份清单各定义同名不同定义（read_only True/False）的产出方时，写那份被静默丢弃=多生产者歧义从去重后门溜进来、agent 按幸存那份评风险，与 #128 contracts 同形态；现等值放行/不等值点名差异字段）；`compose` 本体对同一 Task 实例按身份去重——重复清单曾把写 Task 排两遍=副作用真跑两次而配额只记一次；等值不同实例保留，多生产者冲突仍归 for_goal 点名。#107 十三组对抗探针审计 sound：单 Task 自环/部分自环/goal 在环内均显式报「循环」、provided 可打断环、菱形共享上游只排一次、goal 已在 provided 返回空计划（agent no-op）——关键边界已有回归测钉死防漂移） |
| `config.py` | `.env`、环境键、生产防线、写操作校验（`load_dotenv` 按 BOM 字节判编码：UTF-8/UTF-8-BOM(记事本)/UTF-16(PowerShell 5 默认)都正常解析——不能「utf-8 试失败再试 utf-16」，UTF-16LE 纯 ASCII 内容恰是合法 UTF-8 会解出夹 `\x00` 垃圾键；BOM 旧实现静默把 `DEMO_USERNAME` 变 `\ufeffDEMO_USERNAME` 永不命中=「未配置凭据」查不到根因。解码坏/键名含空格=点名文件的 ValueError，绝不静默跳过；CLI 入口 `from_root` 接 `rc 2` 口径(#79/#80)，旧实现 PowerShell 写坏 .env 后所有 atlas 命令裸 traceback。#138：`check_legacy_paths`（模块级判据+消息单一真相）——旧项目根/web 子目录缺失点名 `ATLAS_LEGACY_PROJECT`/`ATLAS_LEGACY_WEB`，run 轨（runtime/CLI 预检）与 pytest 轨（`legacy.check_exec_dir`）三方委托共用一套消息，杜绝复制后改一漏一漂移（#92/#97）；通过路径零涟漪（#115））|
| `session.py` | 单浏览器、run/debug/heal、快照（取证为尽力而为：截图/求值异常降级返回 None，绝不把已通过步骤翻成失败；`__exit__` 逐个 try 关闭 context/browser/_pw——浏览器已崩时 context.close() 抛错不再跳过后续导致进程泄漏，且在用例异常传播中不掩埋原失败，仅在无在用例异常时才把清理错误抛出。#123：多清理错误不再互掩——三环节（context/浏览器/驱动）连环崩时旧末行 `raise errors[0]` 只报第一个，`_pw.stop` 失败=Playwright node 驱动进程正在泄漏却无从得知（#85 清理链无痕同红线，与 #121 面D 同构）；现单错原样抛（#82 口径不翻错）、多错聚合 ExceptionGroup 全点名，有用例异常仍让位真因。#111：`__enter__` 半启动失败同样不留孤儿——start() 成功后 launch/new_context/new_page 任一崩，`with` 语义不触发 `__exit__`，入口逐个尽力关闭驱动与半开浏览器再原样抛真因（DemoRuntime:70 在赋值 self.session 前抛、外层清理够不着；探针实锤每次 executable 指错泄漏一个 node 驱动进程）。`ApiEvidence.classify` 同口径尽力而为：坏重放 handler（抛错/非映射/None）跳过试下一条，绝不裸抛——在 engine except 块里抛会把原用例异常顶掉并清空 results/草稿/取证整条证据链，定性器不得先于被定性对象死） |
| `engine.py` | Task 执行策略、auto 通道路由、契约、heal、实证、CDP 冻场钩子（自愈成功后就地更正 recorder 同一条 step 为 healed+通过并补成功取证，运行记录与 results/flow 终态一致——否则 `atlas summary` 会把已自愈的 passed run 误计出 phantom 失败类；自愈成功归因用 `match_pitfall(status="approved")`，记驱动自愈的那条知识而非更长的 pending 草稿；失败路径仍无过滤，给人审看最长匹配。auto 降级链：`_perform` 只捕 `(NotImplementedError, RuntimeError)`——契约违规（AssertionError 系）与网络故障（OSError 系）照常传播不被降级掩盖；降级原因经游标并入 step record 落 `runs.jsonl`（旧实现只写 ctx 内部键 `_auto_degraded_to_ui` 零消费方，「API 坏→退 UI→ok=True」步骤事后无痕=假阴性，#106）。#114 三面：写护栏 `validate_write` 必须在 `recorder.step` **之内**——旧实现在 with 外执行，生产拦截写步骤时 `PermissionError` 于 step 进入前抛出，`runs.jsonl` 零痕迹、results 空（拦截无痕也是审计链断点，与 #85 同红线），现归 env_blocked（策略阻挡非代码缺陷，红线 #7）且 PermissionError 仍原样传播；控制流异常短路于失败路径起点——如实记 interrupted 进 results 后原样重抛，不走 heal/`evidence.classify`（防 stale 重放器误定性 KI）/坑点匹配/journal 草稿/session 取证（remedy 无从修复中断、Ctrl-C 不该被拍现场照，纯噪音），判据收敛 `failures.CONTROL_FLOW` 单一真相（#108/#92/#97 同源）；CDP 坏 `CDP_PAUSE_STEP`（`abc`/`TC9:`/`1.5`）旧实现 `int()` ValueError 被静默吞掉直接放行——调试者以为冻场等 attach，步骤跑过案发现场错过（#102/#105 假阴性同族），现点名坏值警告一行后照常放行（非交互零阻塞红线不变）。#139：三处 `session.capture()` 返回值经 `_record_shot` 并入 step 记录 `attachment`（root 相对、根外回落绝对）——旧实现全丢弃，取证截图落盘却 runs.jsonl/Allure 无痕（#106×#86 断链同族）；控制流短路分支保持不取证（Ctrl-C 不拍现场照语义不变） |
| `recorder.py` | 零依赖 JSONL 运行记录（`default=str` 兜底不因异常字段丢整轮证据；`_closed` 仅在落盘后置位，写失败可重试不谎报。#126 两面：① `step()` 的 `classify_failure` 在 except 块里崩（业务异常 `__str__` 自身抛错——引用未初始化属性真实可达）时旧实现让新异常顶掉真因，且 fail_class 没赋上 → finally 判 `ok="fail_class" not in item` 把失败步骤记成 **ok=True 假绿**（定性器先于被定性对象死 #89 + 坏形态伪装成功 #102 同族）；现定性失败诚实回落 unknown、真因原样传播。② `close` 的 `default=str` 假设 str() 全函数可用——`__str__` 抛错时整轮落不了盘（违背 #64 初衷）、finally 顶用例真因、`_closed` 不置位重试永远崩；现永不抛兜底落 `<unrepresentable 类型>` 占位，坏字段有噪音但整轮证据必落盘） |
| `legacy.py` | 原项目 pytest 和 `step_N` 双轨兼容（`check_exec_dir` 预检旧项目根/web 子目录：缺失点名对应 `ATLAS_LEGACY_PROJECT`/`ATLAS_LEGACY_WEB` 键，不再让 subprocess 抛 OS 级 `NotADirectoryError` 冒充框架缺陷；#138：判据与消息委托 `config.check_legacy_paths` 单一真相——run 轨（runtime/CLI 预检）与 pytest 轨共用一套消息，杜绝两处复制后改一漏一漂移（#92/#97），通过路径逐字等价零涟漪（#115）；`--dry-run` 文档语义「只检查路径」现为真——旧实现空目录照样 rc0 打印命令=假绿预检。#119：`from_module` 探测零命中且模块 import lib 时，裸 `ModuleNotFoundError: No module named 'lib'` 补归因重抛（点名 ATLAS_LEGACY_WEB 与探测起点；探测命中却缺 lib=真缺、坏别的模块原样传播不揽责——#115 教训：不 import lib 的纯本地模块任意目录仍合法，不在加载前拒绝）；step_N 非可调用（`step_3="字符串"`）构造期当场 TypeError 点名实际类型，不再假成功推迟到 perform） |
| `dsl.py` | 线性 YAML DSL、变量插值、assert 块、数据供给（Ctrl-C/SystemExit 记 `interrupted` 而非 `failed`，与 `env_blocked` 同属操作/环境终态，不污染 pass/fail。#129：flow 终态判据曾内联硬编码 `(KeyboardInterrupt, SystemExit)`——`CONTROL_FLOW`（#114）的第 5 处漏网副本，生成器关闭（GeneratorExit）被记 `status=failed` 污染缺陷分布，现收敛 `failures.CONTROL_FLOW` 单一真相，agent 同一缺陷一并修。`validate_flow` 是结构校验单一来源：步骤非映射点名序号与形态、未注册 Task、assert 块、requires 供给块（策略坏点名是哪个键——from_dict 报错不带键名，多条件 flow 人回头猜）、flow 级 on_env_blocked——`atlas compile` 与 `atlas run` 登录前共用，registry 与 `DemoRuntime.registry()` 同源，compile 绿=run 不再登录+开浏览器后才结构裸崩。#113：assert 表达式操作数分形态——路径形态（`word.word`）解析失败点名不静默回落字符串（打错的路径曾被当互不相等字面量恒真假绿）；`true/false/none/null` 识别为 bool/None 字面量（`ok == true` 不再假阴性拒绝正确断言），显式带引号仍按字符串；requires 两条落点同键（同 `as`）执行前与 validate_flow 同口径点名，不静默 last-wins 覆盖（#84 多生产者同红线）） |
| `probes.py` | find/create 策略、环境与配额防线、回收清单（读写回收清单逐行容错：残行/非对象跳过不顶掉整表；配额在 create **前**占用；`from_dict` 先验形态——raw 是字符串曾被逐字符误报「未知字段」、null 裸崩 TypeError、缺 entity 裸 KeyError，现点名「必须是映射/缺少 entity」与 #77 同口径；裸 creator 返回非 dict 在建单后、remark 检查前就 TypeError 点名实际类型，坏形态不进单号补记。#117：`find_document_number` 单号判据单一真相（非空标量才算；`{"orderNo": ""}`/None/bool/容器不算），`supply` 补单号闸门——生产主路径 find_or_create 旧只查备注不查单号，建了单没回单号静默成功、回收清单无单号 `atlas recycle` 显示 '?' 永远找不回真实单据（#86/#102 同族）；建单已发生记录保留、报错给原值供人工追回，与备注缺失同策略。#132：`save_for_recycle` 读旧清单补字节面防线——中断腰斩尾行时 `read_text` 裸崩，**读崩=合并写回不执行，本次新造的单号随崩溃一起丢**（单据已真实产生却再无人知道单号，#86 回收闭环被一条历史坏字节掏空、红线 3「保留可回收信息」失效；#112 读侧同防线漏网第 5 处）） |
| `failures.py` | 失败分类与 API 实证（`api_evidence` 对 `data` 形态零假设：ICS 列表接口 data 常为数组、网关错误常为字符串、失败常为 null——旧实现对非 dict 直接 `.get` 让定性器裸崩，现场证据全丢；data=null 时定性文本回落 error，不再被字符串 `"None"` 挤掉真实根因。`status` 同纪律归一化：HAR/JMX 录制常存字符串 `"500"`，`"500">=500` 裸崩（`prove_api` 无 try 顶掉原异常）、`"401" in {401,403}` 恒假漏判会话——`_status_code` 把数字串转 int、bool/非数值归 None 走文本定性回落，不猜不崩。#114：`CONTROL_FLOW=(KeyboardInterrupt,SystemExit,GeneratorExit)` 单一真相（engine/healing 直连判据，recorder 经 `classify_failure` 单点继承，杜绝改一漏一漂移，#92/#97 同红线）；`FailureClass.INTERRUPTED` 单列终止信号——`classify_failure` 先于关键词短路，KI 不再落 unknown 进 summary 缺陷分布、类型名恰好含 "timeout" 等子串也不误判；ENV_BLOCKED 词组追加「生产环境禁止」——写护栏拒绝是环境策略阻挡（红线 #7）归 env_blocked，伪装成待诊断缺陷也是假阴性同族 #102/#105。#127：`exception_text(exc)` 是异常定性文本的单一真相（`f"{类型名} {exc}"` 此前 classify_failure 与 knowledge.match_pitfall 各写一份，#92 改一漏一）——业务异常 `__str__` 自身崩（引用未初始化属性，#126 面A 同形态）时 f-string 在 except 块/自愈准入路径当场炸顶掉原用例异常（定性器先于被定性对象死 #89）；现坏 `__str__` 回落类型名继续定性，绝不裸抛。#129：展示形态也收敛——`exception_message(exc)`（`"{类型名}: {消息}"`，留痕/reason 用）与 `exception_text` 同源 `_exception_display`（安全提取消息体，坏 `__str__` 回落空串），此前 agent 手拼第三份 f-string 副本在 except 块炸掉真因+留痕（#89+#85）；消息含 `": "` 时定性文本不丢冒号（派生 `replace(": ")` 版本被否——#115 归因修复不得把正常路径翻成错误）。Gap5 P2：`classify_text(text)` 抽取为文本分类单一真相——classify_failure 委托它（控制流短路保留），Allure 解析共用同一套关键词匹配（防 #92 漂移）） |
| `knowledge.py` | 知识命中、选择器注册表、人审写回（`load` 逐文件回退：私有目录文件写坏/空/坏字节 → 降级到包内默认而非抛错顶掉整个子系统；`match_pitfall(status=)` 过滤在「最长 trigger 择一」**之前**——自愈准入选 approved 集，pending 草稿不得靠更长 trigger 遮蔽同异常的 approved 坑点；诊断调用不传 status 保持最长最具体原义。#127 六面：私有库是**人可手改**的，旧闸门只拦坏字节，放过「合法 JSON 但形态不对」全部穿透消费点崩——顶层数组/字符串 `.get` AttributeError（现视同损坏回退下一来源）、坑点行 `scope:null`/`trigger:null`（键在值 null 默认不生效 #77）坏行逐条跳过（#65 同纪律）、`components`/`overlays` 键值坏让批准半路崩塔清不动（#73 场景；现坏形态当空重建、copy-on-write 不丢回退好数据）、`SelectorRegistry.get` 对 `selectors:null` 裸 TypeError 逃逸友好归因（坏条目当未注册统一拒，#63 存在≠可用延伸）、`match_pitfall` 的 f-string 遇异常 `__str__` 崩——#126 只守 recorder 调用点、engine/healing 直调漏防（改一漏一 #92），收敛 `failures.exception_text` 单一真相，坏 `__str__` 回落类型名（类型名本就是 draft_pitfall 的 trigger 形态）；兼容面 #115 全钉：好行照常命中不翻错。#135 红线 #11 代码化：`root=None`=只读库类型契约（未设 `ATLAS_KNOWLEDGE_DIR` 时 `DemoPlugin.knowledge()` 返回它——旧实现拿包内目录当写根，review --approve 一批准就把业务坑点写进 git 资产，探针实锤包内 pitfalls.json 30→31；判据不靠路径包含启发式——Windows resolve/符号链接可绕过且「显式构造」与「忘设 env」无法从路径区分）；`_writable_root()` 闸门在 save_reviewed/save_component_selector/save_overlay 三条唯一写路径**写盘前**抛 PermissionError 点名 ATLAS_KNOWLEDGE_DIR（批准先写知识后改队列，闸门最前置=零半状态）；读侧逐文件回退包内虚构默认照常「读不残缺」）。Gap5 P2：`match_pitfall_from_text(text, domain, status)` 抽取为文本匹配单一真相——match_pitfall 委托它，Allure 分析共用 |
| `contracts.py` | 契约三档：core/high/minimal（required 形态在 `__post_init__` 构造期钉死——类型值写成数组/对象、路径键非字符串时当场 ValueError 点名；旧实现留到 validate() 才 unhashable TypeError/AttributeError，定性器先于被诊断报文死，与 #89/#94 同红线。`load` 把「JSON 合法但形态不对」的契约文件（顶层数组、条目非映射、拼错键）转可定位 ValueError 点名文件与条目序号，接进 `DemoPlugin` 的损坏回退——手改坏私有契约不再穿捕获范围顶掉整条断言链。#128：同 `(method,path)` 两条**不同定义**契约不再 dict last-wins 静默择一（core 被后写的 high 遮蔽=强校验冒充覆盖率却永不生效，`--validate` 自述红线 #7/#12、与 #84 多生产者同键同红线）——`_put` 按 dataclass `__eq__` 区分：等值重复放行（`toolbox.learn_contracts` 轮询去重依赖覆盖语义 #102，拒绝=把正常路径翻成错误 #115），不等值显式 ValueError 点名两条名字+等级（load 同路径，私有冲突文件回退包内非静默用弱版）|
| `healing.py` | 知识驱动自愈（只读+已审核知识；准入用 `match_pitfall(status="approved")` **在择一前过滤**，trigger 更长的 pending 草稿不再遮蔽同异常的 approved 坑点——否则正当自愈被静默压制。#108：控制流异常（KeyboardInterrupt/SystemExit/GeneratorExit）一律拒绝自愈——终止信号不是业务失败，remedy 无从「修复」它们；探针实锤 approved 的 KI trigger 行曾放行 Ctrl-C 就地重试（engine `except BaseException` 本就捕 KI，旧实现只靠 trigger 恰不命中侥幸挡住，零防线），违反 #70 interrupted 终态口径） |
| `state_machine.py` | 复杂单据按需状态机、守卫与确定性迁移（声明但未注册的守卫显式报错，绝不静默判假走 fallback 分支；`from_dict` 校验 states 必须是映射、迁移必须是列表，畸形 YAML 给可定位 ValueError 不再 AttributeError/TypeError 裸崩。#116：`terminal:` 键在值 null 不再 `set(None)` 裸 TypeError（默认值只在键缺失时生效，#77 同红线；cli.machine 只捕 ValueError/YAMLError，TypeError 漏网整条命令 traceback 崩违反 #79/#80）——null 视同未填回落自动推导，非列表点名；迁移 to/task 必须非空标量——旧 `str()` 把 null 兜成字面量 `'None'` 成「合法任务名」，`atlas machine --target` 输出 `["None"]` 假绿（坏形态伪装合法结果 #113 同红线）；guard 显式 null 回落「无守卫」（不再是假守卫 `'None'`），复合形态点名；守卫注册了但值不可调用点名守卫名与类型，不再裸 TypeError「not callable」无归因（#63 延伸：注册≠可用） |
| `agent.py` | 规划、风险分级、审批、决策留痕（半途失败/Ctrl-C 也落痕：`executed` 完成清单+止步 Task，写失败记「执行失败」、中断记「interrupted」与 DSL 终态口径一致；审批门已放行的写 Task 追责不断链；#110：留痕落盘尽力而为收敛三处调用点——#85 只护失败路径，成功路径（写副作用已发生）与审批门 raise 前裸崩曾把 Decision 返回/rc3 决策信号顶成 FileExistsError；成功路径同落 `executed`，「跑完全部」与「一个没跑」的行不再同形。#129 两面：① 中断判据收敛 `failures.CONTROL_FLOW` 单一真相——旧执行循环内联硬编码 `(KeyboardInterrupt, SystemExit)` 漏 GeneratorExit（#114 声明 engine/healing/recorder 同源的单一真相，agent 是漏改第 4 处，#92 改一漏一），生成器关闭被记「执行失败」与 `classify_failure(GeneratorExit)=interrupted` 自相矛盾；② 留痕器先于被定性对象死——reason 手拼 f-string `{exc}`（#127 只收敛定性侧，展示侧第三份副本），业务异常 `__str__` 自身崩（#126 面A 真实可达）时在 except 块炸：探针实锤真因被顶掉 + 整条留痕随赋值丢（连内存 decisions 都没 append，已执行写 Task 副作用无痕 #85）；现收敛 `failures.exception_message` 单一真相，坏 `__str__` 回落类型名。dsl.py flow 终态判据同款硬编码（第 5 处漏网，status=failed 污染缺陷分布）一并收敛） |
| `creators.py` | 已验证建单脚本接入与「非人工创建」护栏（#117：单号判据收敛 `probes.find_document_number` 单一真相——`require_audit_tag` 旧 `key in result` 把 `{"orderNo": ""}`/`{"orderNo": None}` 也算「有单号」放行（键在即算 #102 同族），且与 supply 两份判据各写各的（#92/#97 改一漏一）；非空标量才算单号，bool→"True" 假单号与容器形态拒绝、数字单号接受。`make_scripted_creator` 的 where 含不可 JSON 序列化值（YAML 日期对象）不再裸 TypeError——点名脚本+整条键路径（嵌套下钻 `where.createdAfter`/`dates.0`，#61 口径）+类型+转换建议） |
| `experience.py` | 失败草稿（确定性 id+去重计数）、人审提升正式知识（#112：队列尾行被中断腰斩出非法 UTF-8 时读侧 replace 回落——好草稿不再被一条坏字节整队拖死，engine 失败路径的登记器不再在 except 块里顶掉原用例异常（#100/#109 红线）；追加前检测尾字节非换行先补断行——裸 append 黏在无尾换行残行后会把整条合法新草稿合并成「残行」被读侧静默跳掉：登记成功、无异常、草稿永久不可见（#102 假成功同族）。#118：同 id 命中增 seen 时同步刷新最新 evidence——bu_overlay 坐标文件修正后 id 不变证据变（id 按「键+两侧BU」定），旧只计数不刷证据，审核塔停过期值、人审基于旧证据批准错误坐标（#102/#105 同族）；缺省 evidence 键不抹已有证据（engine 失败路径登记不带 evidence，零漂移）。#130 面C：`_read_queue` 与 review.rows 同防线补形态面——合法 JSON 非映射行（`[1,2]`/`null`/字符串）逐行跳过不整队崩；该登记器在 engine 失败路径的 except 块里，崩了顶用例异常（#100））。Gap5 P2：`draft_from_text(*, task, text, failure_class, ...)` 抽取为文本造草稿单一真相——draft 委托它，Allure 分析共用；text 存进 evidence 供人审核阅 |
| `review.py` | 审核吞吐入口：队列/证据卡片/批准(按 kind 分发)/驳回/整类批量/模板/统计/决策留痕（`resolve` 拒绝空/空白引用——空串非通配，否则唯一 pending 时空模板行会被静默批准。#109：`apply` 的「合法 JSON 但非映射」决策行点名拒绝不顶整批；`approve_to_pitfall` 空 `exception_type` 回落非空 trigger——键存在值为空串时旧 `.get(key, default)` 不兜底，批准的坑点 trigger 为空被 #59 永久拒匹配=死资产假成功；#112：`rows()` 与 `_read_queue` 同防线——坏 UTF-8 字节 replace 回落读入，残行自然跳过，好草稿不再整队不可见。#130 四面：①`_decide` 决策留痕尽力而为——批准顺序「知识写回→队列改写→留痕」中留痕 OSError 不再把已达成且不可重放的业务事实顶成 traceback（#110 agent._record 同纪律）；②pending 判据收敛 `_is_pending` 单一真相——列表/`resolve(dict)` 的 `==PENDING` 与 stats/reject_kind 的 `get(status,PENDING)` 三套并存时 legacy 无 status 行「列表看不见、stats 报有、批量驳回能驳」跨入口矛盾（#92 改一漏一），显式 approved/rejected 的重放防线不松动；③`rows()` 补形态面——合法 JSON 非映射行跳过不整塔崩；④`approve_to_overlay` 的 `evidence` 键在值 null 用 `or {}` 回落（#77，证据卡片面早已防、批准路径漏防），坏形态走友好 ValueError 不再 AttributeError。Gap4：`code_review` kind 不可 `--approve`（点名「不自动写知识」）——review-code 的 finding 进塔后人审仍走既有流程，不新增知识写路径） |
| `review_code.py` | Gap4 代码质量评审：`review_code()` 主入口——扫描 pytest(AST)/DSL flow YAML/契约 JSON，5 维度权重计分（断言 .25/安全 .20/可维护 .20/覆盖 .20/重复 .15），critical 硬顶 ≤59。31 条反模式规则库（ASSERT-*/GREEN-*/SAFE-*/MAINT-*/COV-*/DUP-*），红线 #7 专项侦测「为变绿放宽断言」三种互补手法：静态反模式（`GREEN-*`+`ASSERT-*`）、基线差分（`--baseline` 对比断言计数/弱化）、运行史交叉（`runs.jsonl` failed→passed+断言弱化→critical）。两模式：static（默认，纯 stdlib 离线）、knowledge（+Atlas 知识库增强）；ai 未实现（CLI 拒绝 `--review-mode ai`）。报告走 Allure 2.39.0 风格（复用 Gap5 emit_allure 格式），含总分/维度分/问题清单（file:line+归因）/改进建议。CLI rc 0 通过/1 门禁失败/2 用户错误。边界：审源码、只出报告、永不写知识；`--to-tower` 只造 `pending_review` 草稿（`kind=code_review`） |
| `explore.py` | 非生产探索会话（CDP 留给外部大脑）、trace→pending_review 编译（trace 逐行容错：残行/非对象计入 malformed_lines 不顶掉整份编译；#109：trace/snapshot 被中断腰斩出非法 UTF-8 字节时回落 replace 读——旧实现 read_text 裸 UnicodeDecodeError 崩在读文件本身，#68 的逐行容错一行没轮到；截断前的完整候选仍是有效原矿）；2026-11-04 真实 SIT 冒烟已过 |
| `overlay.py` | BU 坐标双轨：两组坐标逐键 diff → `bu_overlay` pending_review 草稿进塔；批准落私有 `bu_overlays.json`。#118：坐标文件修正后（如 ou B→C）草稿 id 不变（按「键+两侧BU」定、seen 聚合防重复占塔）——旧实现同 id 命中只增 seen 不刷证据，审核塔停过期值，人审按 --remedy 批准的「目标BU真实值」基于旧证据落成错误坐标；现同 id 再登记刷新最新 evidence（缺省键不抹已有证据——engine 失败路径不带 evidence，零漂移；pitfall 同 id 等值覆盖无害）。复核面 sound：相同值不进 diff、缺失/null 同为「无坐标」、未知 BU 点名清单、顶层混合键不可达（JSON 键恒字符串） |
| `runtime.py` | 一次登录、Cookie、DemoApi 白名单、探针/契约装配（`contracts()` 委托 `DemoPlugin.contracts()` 私有→包内逐文件回退，真实项目 `ATLAS_KNOWLEDGE_DIR` 的 `contracts/core.json` 生效；两级都缺显式报错不静默空表；`atlas contracts` 展示与运行时校验共用同一来源，不再漂移；`check_demo_api` fail-fast 预检在**浏览器启动前**拒绝坏 DemoApi 路径——旧实现事件序列 browser_opened→baked→closed，真实 SIT 登录+Cookie 烘焙都烧完了才撞 attach_demo 缺文件，白烧会话还留半截现场（与 #90/#97 同红线）；#138 同位补 `config.check_legacy_paths()`——run 轨旧实现只预检 DemoApi 路径，`ATLAS_LEGACY_WEB` 指错要等 `_load(login.py)` 才撞裸 FileNotFoundError（真实浏览器已起、SIT 登录已烧、消息不点名配置键，#103 白烧会话×#90 归因漂移同族；pytest 轨的 check_exec_dir 早就点名，run 轨是漏网面），现三方（CLI run/agent --execute 预检 + `__enter__` + `legacy.check_exec_dir` 委托）共用单一真相）；`__enter__` 在浏览器已开之后任何环节失败（登录/OU 超时/attach_demo 缺文件）都先关闭已开会话再原样抛真因——`with` 语义下 `__enter__` 抛错不会触发 `__exit__`，否则每次失败泄漏一个 Chromium+驱动进程；`__exit__` 把本次 quota 建过的单落盘 `reports/recycle_list.jsonl` 闭环回收链——旧实现 `save_for_recycle` 从未接入执行链，真实 SIT 建单只进内存退出即丢；落盘失败无在用例异常才上报，有则让位真因。#121：`LegacyStepTask` 的 step 判据前移构造期——YAML 1.1 的 `step: yes/on/true` 解析成 bool，旧 `int(step)` 静默变 step_1 执行不相干步骤（#116 假合法同族），`3.7` 静默截断；现点名 bool/小数/非整字符串/step_0，引号 '3' 与整值 3.0 合法放行。DemoApi 白名单从引擎装配行收敛 `DemoPlugin.allowed_api_operations` 单一真相（红线 11 引擎不含业务字面量，回归测钉死引擎源码不含操作名）。`__exit__` 双清理错误（回收落盘失败+会话关闭失败）不再互掩——逐个 try 收集、单错误原样抛、双错误聚合 ExceptionGroup 全点名，有用例异常仍让位真因（#85 清理链全留痕）。spec_file 不预检是 #103 有意边界：外部类 `_load_spec` 存活级降级，Atlas 不越界） |
| `reporting.py` | runlog 格式导出、运行聚合（残行/非对象/缺主键逐行跳过；`steps` 非列表或含 `null`/非对象步、非数值 `dur_ms` 也跳过或归零——绝不因一条坏行顶掉整份报告。#131：「腰斩残行」防线补字节面——旧 `with path.open(encoding="utf-8")` 整迭代在中断腰斩的多字节字符处抛 `UnicodeDecodeError`，docstring 承诺的「任意 JSONL 不整体失败」与 #112 注释「读侧必须同防线」（experience/review/explore 当时都修了）对 reporting 两函数永不兑现；CLI summary/report 因 `UnicodeDecodeError` 是 `ValueError` 子类被 `_user_error` 兜住不崩，但把含有效行的文件整体拒掉且误归因成「用户输入错误」（中断写坏的盘不是用户输入错误）。现 `errors="replace"` 回落：残行 JSON 必败自然跳过、截断前合法行照常聚合、骨架完整仅字符串含坏字节的行如实以替换字符统计（不静默丢证据也不静默修证据）——与三方读侧同纪律） |
| `toolbox.py` | toolbox 感知器官、抓包/重放/导出（`learn_contracts` 把 200 抓包登记为 minimal 存活级，不自动升 high——high 要求 data 为对象会误判数组型列表接口；分级升级仍走人审。真实 web-toolbox 的 `net_list` 权威键名是 `entries`（扩展 v1.1.4 实测），不是离线 fake 的 `items`——旧实现只认 items 会让真实抓包静默登记 0 条；现 entries 优先/items 兼容/裸列表兼容，计数按 registry 净增不按条目数（轮询同路径去重不虚报）。#120：`console_read` 真实形态是 `{total,filtered,entries}`（bridge-client v1.1.4，与 net_list 同族键名），#105 照离线 fake 只认 items——真实错误行被点名「无 items」当场拒绝，把正常路径翻成错误（#115 教训反向面）；现 entries 优先/items 兼容，键名口径与 learn_contracts 收敛单一纪律。`learn_contracts` 补分页闸门：真实 net_list 默认 limit=50、total 报全量，旧实现拿一页登记一页零告警——部分结果伪装全量（#102 静默 0 条同族假绿），现 total>页大小点名终止。`save_har` 按真实桥接返回形态分流：过滤无匹配返回**纯文本**「错误: 没有条目」（非 dict，v1.1.4 实测）——旧实现无条件 `json.dumps` 包成字符串字面量写成假 .har 坏档（保存成功、真因埋进引号）；现 str 先试解析、坏文本点名原文拒写，None/异常类型点名拒绝。`console_errors` 同纪律：真实桥接 `console_read` 该 build 返回纯文本「错误: 未知方法」——旧实现把非 list 折叠成 `[]`（读成「前端无错误」的假阴性），现合法 `{"items":[]}`/裸 list 如实返回、错误形态点名拒绝；`replay`/`chain_replay` 在 src 无消费方（`ApiEvidence` 用业务方自有 handler）透传诚实不动。**#140 错误形态防线收敛**：learn_contracts 旧把 bridge 错误 dict/纯文本/None 折叠成 `[]` 静默登记 0 条（旧测甚至把 bug 钉成规格「不崩不登记==0」——已翻转），同形态 console_errors 在 #105 却点名拒绝——两函数各修各的漂移（#120 只收敛了成功键名，#92 错误面漏网）；现共用模块级 `_unwrap_records(payload, source)` 单一真相（list 原样、dict entries→items is None 回落、坏形态点名；合法空表如实 []，#115 不翻正常路径），分页闸门顺带修归因——`{"total":231}` 无 entries 旧误报「分页截断」，现解包先行点名「无 entries/items」真因；`save_har` 补 #104 漏掉的错误 dict 后门：缺 log 结构的 dict 点名拒写（合法 {log:…} 照常） |
| `tasks.py` | 纯 API Task、通用 UI Task、assert_api（#115：`AssertApi` 的 `save_as` 是真旋钮——`__init__` 存 `self.save_as`、perform 按声明键返回 `{save_as: True}`，与同文件 CallEndpoint 同守 #92 produces 自兑现；旧实现把 save_as 塞进 produces 却从不存实例属性、perform 硬编码 `api_assertion`，DSL 透传自定义 save_as 到 write_produces 才 KeyError，半成品旋钮。`_dig` 路径打错四种形态（dict 缺键/list 越界/非数字索引/标量中间节点）统一收敛成带整条 path 与失败段的 AssertionError（含「不存在」归 data），杜绝旧裸抛 KeyError/IndexError/ValueError 落 unknown/unknown/contract 的归因漂移——#58/#61/#113 同红线） |
| `cli.py` | 命令行入口（`contracts` 展示与校验同源；`contracts --validate` 只读结构自校验，点名空 path/name「冒充覆盖率却永不生效」的契约与无法解析的契约文件，rc 2，不发真实请求；用户输入错误统一「一行 stderr + rc 2」——文件路径类 machine/summary/report/compile/run（run 的 flow load 在登录前失败）、legacy（旧项目根/web 子目录缺失点名 ATLAS_LEGACY_* 键）、规划类 compose/agent（不可拼装 goal 的 `FlowCompositionError`）、运行入口配置类 run/agent --execute（缺环境 URL/DemoApi 路径坏在 `with DemoRuntime` **之前**预检点名 `DEMO_URL_*`/`ATLAS_DEMO_API_PROJECT`，旧实现合法 flow+缺 URL 裸 traceback）均不再抛裸 traceback，与 overlay/review/explore 同口径。#138：同两处预检补 `config.check_legacy_paths()`——`ATLAS_LEGACY_WEB`/`ATLAS_LEGACY_PROJECT` 指错也在浏览器/登录前 rc 2 点名键（run 轨此前完全不预检 legacy 路径，撞点漂到 `_load(login.py)` 的裸 FileNotFoundError）。#132：`_recycle` 读回收清单的调用点其实**不在** try 包裹内，且 #71 残行计数防线不含腰斩字节——文档长期声称的「与 overlay/review/recycle/explore 同口径」对 recycle 从未为真（一条写坏的历史腰斩尾行裸崩 `UnicodeDecodeError`、连要回收的单都列不出）；现补 `errors="replace"` 回落，坏行落 skipped 计数、好行照常列出，读侧断言方为真）。#134：`run --bu` 的 apply_bu 调用旧只捕 KeyError——BU 值形态非映射的 AttributeError 漏网变裸 traceback（违反用户输入 rc2 口径），现 catch 扩 `(KeyError, ValueError)`（`tests/conftest.py` 同轮新增：load_dotenv setdefault 把 cwd 真实 .env/tmp legacy .env 灌进进程环境、monkeypatch 不回滚未知键，配好本机 .env 后 pytest 顺序相关必红 4 个——会话级清 ATLAS_*/DEMO_*/ISERVICE_* 前缀基线 + 函数级快照恢复，产品代码零改动）（`doctor` 读 Dockerfile/compose 走 `_read_text_safe`，present-but-unreadable（非 UTF-8/权限）记 `docker_unreadable`+判不一致，绝不因一个坏资产文件崩掉整个诊断）。#135：`review --apply` 决策文件 `read_text` 是 **#112 读侧同防线第 8 漏网点**——中断腰斩尾行裸 `UnicodeDecodeError` traceback 崩在读文件本身（探针实锤：合法批准行+腰斩尾字节=半途崩整批零落账），现 `errors="replace"` 回落，残行落 failed 点名、截断前合法批准照常执行；`review --approve/--apply` 分支 catch 扩 `PermissionError`→rc2（只读库写闸门 #135 的传播路径——判据只在 `KnowledgeBase._writable_root` 一处，CLI 不复制第二份 #92） |
| `waits.py` | 语义等待：表格行数、mask 消失、Vue 字段、URL 变化（表格先等 attached 再等 loading mask 落定才读 vm.data——空表是业务事实 `min_rows=0` 返回 0 不恒超时；真实 Chrome 取证空 `.el-table` 高度 0 判 hidden，fake page 掩盖不了；轮询用 tick 计数不依赖 `evaluate("Date.now()")`，导航期执行上下文销毁抛错容忍重试不裸崩。#133：「容忍重试」不吞不可恢复错误——旧 `except Exception` 无差别重试把 Target closed/JS 语法错/业务 JS 自抛 TimeoutError 当导航瞬态烧满窗口谎报「未达行数/无值」（框架故障伪装成等待契约失败 #102，会话已死还空烧 10 拍）；`_is_transient` 按 `failures.exception_text` 单一真相匹配 Playwright 官方瞬态文案（context destroyed/navigation/detached）分流，瞬态照常穿越 reload、其余立即传播；控制流非 Exception 不受影响（#114 红线）。timeout_ms<=0 透传层假设被真实 Playwright 证伪（0=无超时文档语义、负值归 1），未据此改码、留取证口径） |
| `plugin_api.py` | 业务插件解析单一入口（引擎级缝）：`resolve_plugin()` 未设 ATLAS_PLUGIN 时返回 DemoPlugin（与旧硬编码 `DemoPlugin()` 完全等价，含 make_provider 语义），设 ATLAS_PLUGIN=<domain> 时先查 registry 再尝试动态加载（`_load_external_plugin`：路径形态按文件加载、模块名按 `atlas.plugins.<name>` / `<name>` 依次 import，取模块级 PLUGIN）；domain→类映射由 `register_plugin` 登记（默认仅 demo），引擎不含业务字面量；ATLAS_PLUGIN 指到未注册 domain 且动态加载失败抛 ValueError，CLI 接 _user_error rc 2；cli/runtime 的 `DemoPlugin()` 调用点机械替换为 `resolve_plugin()`，零业务语义变化 |
| `scaffold.py` | Gap1 `atlas init` 项目初始化向导：`init_project()` 生成 .atlas-root/.env(值全空)/.env.example/.gitignore/pyproject.toml(不含 allure-pytest)/flows/smoke.yaml(通用 api_call 离线 compile 可过)/tests/test_smoke.py(纯 assert 3 用例产 runs.jsonl)/scripts/run_allure.py(test→emit→generate/serve/open)/README.md；.atlas-root 最后落盘防半成品假象；冲突防护（已有 .atlas-root/非空目录默认拒绝）；--interactive 才读 stdin（CI 安全） |
| `config_add.py` | Gap2 `atlas config add` 微服务配置生成器：`add_config()` 流程二生成 plugins/<domain>/{plugin,tasks,probes}.py + knowledge/*.json 空骨架（pitfalls:[] / contracts:[] 不伪造覆盖率）+ .env.<domain>.example；流程一 --mapping 只出 ATLAS_* 映射片段（零代码）；骨架 Task perform 诚实抛 NotImplementedError；allowed_api_operations 空集；domain 校验 ^[a-z][a-z0-9_]*$ |
| `generator/` | Gap3 `atlas gen` 文档→测试代码生成器（只生成不执行，红线 #9）：`sources.py` 按内容嗅探 OpenAPI3/Swagger2/Markdown/纯文本（UTF-8 严格→replace 回落 #131）；`openapi.py` $ref 解引用+循环检测截断；`emit_flow.py` api_call step（path+query 拼 path、body 分流 dict/list→json= str→data=、全局 isc-app 头并入每步 BUG-920、save_as 全局唯一 compose_for_goal 同键多生产者硬报错、写场景 read_only:false+on_env_blocked:stop）；`emit_contract.py` 契约自动分级（默认 minimal、对象含 resultCode→high、数组锁死 minimal、core 仅文档 x-atlas-level 显式标注、--level 不能强升 core、同键不同定义冲突不覆盖 #128）；`emit_probe.py` 只 find() skeleton、create() 恒 NotImplementedError（红线 3/12/16）；`checklist.py` 复用 FlowCompiler.validate_flow + 占位符递归扫描 + 悬空引用检测；`tower.py` trace_step pending_review 草稿（append_draft 不伪造异常）。列表下标断言 DSL _lookup 不支持 → 列 manual_assertions 不伪造。Allure 不自写（生成阶段无结果=伪造），主路径 atlas run→runs.jsonl→atlas allure。退出码 0/2/3 |
| `plugins/demo/tasks.py` | 10 个核心 Task，委托原 `pages.py`；auto 路由子类。#134：`apply_bu` BU 值形态非映射（手滑存成数组，#127 同族）旧 `.items()` 裸 AttributeError 且 CLI 只捕 KeyError 漏网 traceback——现点名「形态非映射」ValueError（CLI catch 扩 `(KeyError, ValueError)`）；空坐标判据旧 `value in (None, "")` 漏空容器——`[]`/`{}` 被 str() 成 `"[]"` 写进 `ATLAS_SESSION_*`，真实执行切 OU 拿 `"[]"` 必超时（坏形态伪装合法坐标 #77/#102；#117 单号「非空标量才算」同纪律），现 `_is_coordinate` 只认非空标量（bool/容器/空白 ignored），标量数字照常 applied（#115 不翻正常路径）。#137：`_session_default`（auto API 快路径 division 兜底）旧自己 `json.loads` 直读**包内** session.json，绕开 KnowledgeBase 私有→包内回退——设 ATLAS_KNOWLEDGE_DIR 后与 CLI/运行时的 `session_defaults()` 分叉拿虚构值查错客户（红线 #11 被引擎侧旁路击穿，两份「唯一来源」漂移 #92）；现委托 `DemoPlugin.session_defaults()` 收敛单一真相，缺键友好点名，既有字面量扫描测当场抓到注释里的示例字面量（防线生效） |
| `plugins/demo/knowledge/` | 30 条坑点、组件、业务、环境、契约 |

## 开发规范

1. 引擎不含具体业务；业务内容放插件。
2. 新增 Task 必须保证元数据与实际 ctx 读写一致，并补目标拼装测试。
3. 不机械包装全部页面函数；只有业务流确实复用才新增 Task。
4. 不写假 creator、假 API 实证或“看起来完整”的未验证状态机。
5. 默认 `run` 零成功截图；排障才 `debug/heal`。
6. 原始 `legacy-suite` 和 `legacy-api` 不修改；外部路径通过 `.env` 配置。
7. 生产只读、自动单据必须带「非人工创建」。
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

### Q: 测试已经覆盖真实 SIT 吗？
A: 离线验证 **720 passed**。真实 17 条已用 `atlas legacy --keyword` 分批跑完：
**13 passed / 4 skipped / 0 failed**（TC1 / TC2~6 / TC7~9 / TC10~13 / TC14~17 五批），
skip 全为已知环境或产品限制。

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
（`--component <组件> --element <元素> --remedy "<选择器>"`，直接写回 `components.json`）、
`trace_step` 拒之塔外（人工写成 flow YAML 后 `atlas compile`，或 reject）。`bu_overlay`（坐标差异）批准时 `--remedy` 填目标 BU 真实值，写私有 overlay 库。
`--reject --reason` 留痕不删，`--stats` 看 pending/approved/rejected 与积压分类。
批量清塔：`--kind` 过滤 + `--group` 聚类视图（批量类压成按证据源分组的紧凑表，坑点仍全卡片）；`--template` 导出可编辑决策文件（人填组件/元素/选择器或 remedy）、
`--apply` 执行——批量批准仍逐条走 approve 分发与留痕，没填的行失败点名且 exit 2，不是自动批准；
决策文件里被编辑成非映射的行（数组/null，合法 JSON 但形态不对）同样点名不顶整批（#109）；
`--reject-kind <kind> --reason` 整类驳回。
同 task+异常类型+分类的草稿自动合并计 `seen`，同坑复发不重复占塔；
正式坑点 id 用内容 sha1（不是内置 hash），重复批准只替换不堆条目。
自愈准入对控制流异常免疫（#108）：KeyboardInterrupt/SystemExit/GeneratorExit 一律不重试——
即使人审误批准了一条 trigger 恰为 `KeyboardInterrupt` 的知识行，Ctrl-C 也照常传播，不被自愈吞掉。

### Q: 真实内部知识/BU坐标放哪？仓库怎么保持干净？
A: 设 `ATLAS_KNOWLEDGE_DIR` 指到仓库外目录：读逐文件回退包内虚构默认（fallback），
人审批准（组件选择器、坑点、`bu_overlay` 坐标）只写私有目录——包内文件零改动。
首个批准会把合并后的整份文件写进私有（copy-on-write），此后私有版本遮蔽包内同名文件；
同步包内虚构默认变更需手动删私有对应文件。真实 BU-B 坐标到位后用 `atlas overlay <file> --diff BU-A BU-B --to-tower` 进塔。
已批准的会话坐标用 `atlas run <flow> --bu <BU名>` 接入执行链（`ATLAS_SESSION_*` 显式 env 优先；非会话键只做数据参考不进执行路径）。

### Q: 新增第二个公司的业务怎么做？
A: 新建 `plugins/{domain}`，提供 Task、Probe、知识库与必要驱动适配；Actor、DSL、执行、记录、分类引擎无需随业务重写。

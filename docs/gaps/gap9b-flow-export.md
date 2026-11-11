# Gap9b：flow→快 pytest 包入口 + 声明位 + CDP 复用会话（契约增补）

> 状态：scout 定稿（v2，已吸收 lead 两条落点钉与 emit-impl 渲染面四条），待 lead 认可广播。
> 本文件是 gap9 契约的**增量**：`gap9-fast-pytest-runner.md` 与 `gap9-lead-rulings.md` 的 F-1…F-11 为准，
> 本文只新增未覆盖三面（flow 入口 / 声明位 / attach 通道），且与 #3 已落地实现**对齐不另造**。
> 用户指定形态（原话）：「方案：pytest + lib/pages.py（UI AUTO TEST 模式）+ 复用 so_cancel/util.py（CDP 会话）」。
> 本契约对应 F-11 待拍板两件事（入口+声明位）+ lead 追加第三件事（attach 复用会话）；任务 #11/#12 按本表实现。

---

## A. 事实取证（原文引用 + 行号；含 #3 已落地既成形态）

### A.1 attach 通道原型（实跑资产）
`reports/mach_exec_20261106/so_cancel/util.py`：
- `:10` `CDP = "http://localhost:9305"`（端口硬编码——**不收编**该形态，本波端口/域名只走 env）。
- `:16-23` `get_page()`：`connect_over_cdp(CDP)` → `contexts[0]` → 按 `"icssit.midea.com" in p.url` 选页，兜底 `ctx.pages[0]`（:23）。零 launch、零登录。
- `:26-31` `new_page()`：同连接自开新页。
- `:94-95` `wait_quiet` 是 `time.sleep`（原库不受产物判据约束；产物模板受，E.1.3/F-3 不放过）。
- `:99-119` `api()`：`page.evaluate` 页内 `fetch(credentials:'include')`，headers 含业务字面量（`isc-app`/`x-gr-orgcode`/`mas_remote_user`）——**绕过 `abilities.py` auth_expired 判据与 `credential_gate` 净化刀**（两刀都在 Atlas 会话层，页内 fetch 不经过）。本契约**不采页内 fetch 通道**（见 E-2 留白）。

### A.2 Atlas 全仓 CDP 方向是反的（已复验）
- `engine.py:82-114` `cdp_maybe_pause`、`session.py:25-32`：自己起浏览器开 `--remote-debugging-port` 等外部 attach；`emit_fast.py:453-469` 现版 `launch_kwargs` 纯 launch。
- 复验：`grep -rn connect_over_cdp src/ scripts/` = **0 命中**（2026-11-09）——全仓无 attach 出站生产路径。

### A.3 生产判据与调度**两个**泄漏面（lead 钉，双向要堵）
- `config.py:144-145` `is_production: env.startswith("prod-")`——引擎生产判据原文。
- 面一 **argv**：`scheduling/dispatch.py:31` `build_command` 的 argv 全部来自 `task["run_args"]` 且过 `guard_run_args` 显式 raise 闸（`python -O` 剥不掉，C1-C 纪律）。
- 面二 **env**：`scheduling/dispatch.py:38-46` `child_env()` = `dict(os.environ ...)` **全量继承**——调度机 shell 里设过 `ATLAS_FAST_CDP` 会直达无人值守子进程。**只堵 argv 漏一半**，三件套判据见 B-3.5。

### A.4 legacy 委托腿**既成形态**（#3 已落地，本契约以此为基线）
`emit_pytest.py`（fast 页对象库模板，:588-660）：
- `:616-617` `LEGACY_WEB_ENV_KEY = "ATLAS_LEGACY_WEB"`；`LEGACY_PAGES_MODULE = "lib.pages"`——**模块位固定在产物常量**。
- `:620-637` 模块级 `legacy_page_call(func_name, *args, **kwargs)`：env 缺→`EnvBlocked("缺配置：ATLAS_LEGACY_WEB 未设置…")`（:624-625）；sys.path 注入（:626-627，`plugins/demo/tasks.py:100-102` 「No module named 'lib'」真实教训同纪律）；import 失败→EnvBlocked 点名键（:631-632）；函数缺/不可调用→`EnvBlocked("缺配置：旧套件 lib.pages 无可调用函数 <名>（函数名须人审核对，不猜）")`（:635-636）。
- `:604` 腿二用法=**人审**在 `action()` 内写 `legacy_page_call("<旧函数名>", self.page, ...)`；`:660` `__all__` 含 `legacy_page_call`。
- 引擎侧委托先例：`plugins/demo/tasks.py:98-116`（spec_from_file_location + page-first 调用）。
- **推论（对我 v1 稿的自我修正）**：既有形态的声明粒度是**函数名**（模块由产物常量 `lib.pages` 给出）。v1 稿的 `<module.fn>` 值形态与既成不符——v2 起声明值=裸函数名，点名形态拒绝并解释（E-1）。

### A.5 校验挂载点（lead 钉一，已核实现状支持）
`dsl.py:692-704` `validate_flow` **本体第一步就是 `self.compile(flow)`**（docstring：「结构校验的单一来源（compile 与 run 共用）」），compile 管线第一段=零构造静态校验（`_check_static`，:353-375「候选校验→原子应用」纪律）。⇒ 新键形态判据写进 **compile 的静态校验段**，则 `atlas compile`/`atlas run`/`atlas export-pytest` 导出/未来任何入口**自动同判据**，不留第二份（#92）。v1 稿「validate_flow 另写一份」的措辞作废（E-3）。

---

## B. 契约条款

### B-1 入口：新子命令 `atlas export-pytest <flow.yaml>`（lead 裁③：不用 `atlas pytest`；不做 `gen --flow`）

```
atlas export-pytest <flow.yaml> [-o DIR] [--fast] [--force]
```
- 命名（lead 裁决 2026-11-09）：`pytest` 与可执行命令同名会在三处生歧义（README/CLAUDE 命令表两行都叫 pytest、shell 补全与 CI 脚本里 `atlas pytest -q` 像漏前缀、新人 copy-paste 最易混）；`export-pytest` 与仓内 `review-code`/`devops-sync` 动宾先例同构，未实现期换名零成本。
- 语义=「flow 导出 pytest 包」。拒 `gen --flow` 的理由（非口味）：gen 的 source 是接口文档（endpoints/生成 flow 恒 `is_api=True`——F-11 原文），flow 混入毒化 `--source-type` 嗅探，且 gen 的 rc3=结构自检失败与「等人」面两义不并表。
- 校验：**导出前先过 compile 静态段**（即 `validate_flow`/`compile` 同源，A.5）；不过 → rc 2 点名（带 `^` 指针诊断既有形态）。
- rc：`0` 导出成功（stdout 文件清单）；`2` 用户错误（flow 不存在/结构拒绝/目录非空无 `--force`/`--fast` 闸门拒绝）；`3` **本入口不使用**——生成期绝不执行产物（红线 #9），无「等人」终态可映射，README 子命令分表注明。
- 缺省形态（无 `--fast`）= gap7 现状逐字（API 真调用 + UI skip 骨架；E.1.2 快照延伸到本入口）；`--fast`=gap9 F-1…F-10 + 本文件增量。`--force` 语义=`emit_pytest_package` 现闸。
- 确定性：无时间戳/随机；同 flow 重复导出内容相等。

### B-2 声明位：`page_object` / `legacy_pages` 两键并列（A.4 既成为基线）

flow step 新键（fast 消费、通用校验见下）：

| 键 | 取值形态 | 校验（compile 静态段，单点） | 消费（导出/渲染） |
|---|---|---|---|
| `page_object` | **已批准组件名**的非空标量（`components` 顶层键之一；容器/空串/None/bool→拒，#77 形态同族；**点名步骤序号与值，整体不产出**——A.5 原子应用纪律） | 形态判据仅此一处 | 类名=`_pascal(page_object 值)`；调用形态=`<Class>(page).action(...)`（**保持 #3 已落地单方法基线**，方法级拆分不在本波——不留两形态） |
| `legacy_pages` | **旧套件函数名**的非空标量（点分形态 `a.b` 当场拒，消息解释「模块位由产物常量 `LEGACY_PAGES_MODULE=lib.pages` 给出，声明只写函数名」——A.4 既成基线；多模块需求见 E-4 记账） | 同上 | 渲染调用 `legacy_page_call("<函数名>", page, ...)`（atlas_pages 模块级函数，A.4；**不在 `_atlas_fast` 再造第二个委托函数**） |

- **互斥**：一步同带两键 → compile 静态段拒绝点名序号（#84 多来源同红线；「合一单键再按值形态分支」=引擎猜语义，违 #11——并列两键的结论）。
- **两道闸分工，判据零复制**（emit-impl 意见 3，措辞钉死）：**第一道**=compile 静态段管**形态**（非空标量/互斥/拒点分）；**第二道**=生成期闸门管**成员资格**（`_pascal(page_object) ∈ emit_pages_module 实际类名集`——components 只在生成期存在，dsl 拿不到，天然分层）。UI 步放行 ⇔ `page_object` 良形且命中类名集，**或** `legacy_pages` 良形；两者皆无/双有/坏形 → `PytestEmitError` 点名步骤+声明值+已知组件清单（消息精确串归 emit-impl 文件、由 `tests/test_emit_pytest.py` 钉，本契约只钉语义与「拒绝在写盘前、无残留」——F-10③ 现闸位）。
- **UI 步无声明 + `--fast`** → 生成期拒绝点名「缺声明位」。F-9「无组件库」闸保留（纯 API flow 照常出包——正向放行，F-10① 同源）。
- **废除猜名支**：`emit_pytest.py:346` `cls = _pascal(spec.task or spec.slug)` 与 `:818-836` 「task 对齐组件名」闸门消息随之翻新（「缺声明位」语义）；这是 F-10「引擎不猜」的收口步，非新裁。`tests/test_emit_pytest.py:549/563/576` 三条测**同步翻新语义、不删测**（旧通路不许留）。
- **委托腿落盘面**（A.1 fetch 绕过面的收口，产物侧新判据单点）：`_atlas_fast.safe_persist(key, value) -> bool`——非空标量（str/int/float；bool/容器/长文本→False+WARN 一行，键名点名、**值不回显**）才准写入 plan_ctx（`#117` 单号非空标量同纪律）。runs.jsonl reason 框架文案 only 的 F-5 边界不动。产物 API 腿恒走 `_atlas_support.call_api`（urllib），**legacy 委托 ≠ 页内 fetch 通道**。
- **import 面纪律**（emit-impl 意见 4）：产物 test 模块保持**无条件** `from _atlas_fast import fast_case[, require_ctx]`（+ 声明位对应 `import atlas_pages` 无条件）；attach/launch 通道差异**全部收在底座与 conftest 内**，渲染层零条件 import。

### B-3 会话通道：同一 fixture、判据一处、attach/launch 二选一

`_atlas_fast` 新纯函数（唯一判据位，conftest 只调不复制）：

```
session_channel(env: dict) -> "attach" | "launch"
```
- `"attach"` ⇔ `env["ATLAS_FAST_CDP"]` 非空 **且** `env["ATLAS_FAST_INTERACTIVE"] == "1"` **且** `env["ATLAS_FAST_ENV"]` 不以 `"prod-"` 开头；否则 `"launch"`。
1. **attach 行为**：`connect_over_cdp(ATLAS_FAST_CDP)` → `contexts[0]` → 按 `ATLAS_FAST_ATTACH_MATCH`（URL 子串，业务域名只来自 env，模板零默认；未设→`EnvBlocked("attach 需 ATLAS_FAST_ATTACH_MATCH 点名目标页")`）选页。**不采 util.py:23 `ctx.pages[0]` 兜底**——拿人的无关页面开火比失败更坏。
2. **指示器实查仍权威**（F1 口径不破，emit-impl 提醒的语义修正）：attach 支取的是「**已登录** page」，非「跳过登录检查」；实查不在场→`EnvBlocked("attach 会话未登录/已过期：请先人工登录（attach 模式不在人的浏览器里执行登录动作）")`。**attach 永不发登录动作**（在人现用会话上写副作用=越权，#13 同理）。
3. **生命周期**：teardown 只断连（`browser.close()` 对 connect 对象=disconnect，不关人的浏览器；fake browser 测钉 `contexts[0].close` 未被调、人的 page 未被关）；产物自 `new_page()` 的页自关。
4. **attach 恒禁生产**：前缀判据与 `config.py:145` 逐字等值，**锁步测**从 `config.py` 源文本提取比对（不比手抄常量，F-3 锁步同族）。prod+CDP 同场 → 走 launch + WARN `ATTACH-DISALLOWED-IN-PROD`（ASCII，X4）；**不给强制开关**（红线 #2 面）。
5. **无人值守回落=对称三件套**（lead 钉二 + 裁①定稿；三件各管一面、互不替代）：
   - **argv 面（既成形态，lead 更正）**：独立函数 **`guard_fast_cdp(run_args)`**（cli-impl 已交付形态）——与 `guard_run_args` **分离是有意的**：两把闸判据对象不同（`guard_run_args` 管审批旗标 `--approve`/`--mode`，`guard_fast_cdp` 管会话通道残留），合写会让「键名单点」测含糊。argv 中出现 `FAST_CDP_ENV_KEY`（键名单点常量 `= "ATLAS_FAST_CDP"`）字面或 `--cdp` 近似旗标 → **显式 raise** ValueError 点名（沿 C1-C，不靠 assert）；`build_command` argv 恒 `[sys.executable, -m, atlas, run, flow, *run_args]`（:31）不变。
   - **env 面（显式剔除）**：`child_env()`（:38-46 全量继承面）**pop 三键**：`ATLAS_FAST_CDP`、`ATLAS_FAST_INTERACTIVE`、`ATLAS_FAST_ALLOW_WRITES`（第三键 lead 已采纳：调度 env 携写放行=把「人显式批准写」从审批面挪到环境残留面，违 #13——与「argv 永不带 --approve」是同一道闸的两半）；测：父 env 三键全注入→输出无；`PYTHONPATH`/`PYTHONIOENCODING` 注入行为不变（正向对照）。
   - **行为面（三件套里最关键，lead 钉）**：pop 之后**写腿必须仍显式拦停**——测「child_env 剥离 `ATLAS_FAST_ALLOW_WRITES` 后写步骤呈 `env_blocked`（reason 点名写闸）且绝不静默变绿」。**剥离放行不等于放行成功**：若 pop 被实现成「写腿跟着消失/跳过」，pop 就成了第二个「把写悄悄跑掉」的入口（红线 #7/#13 反面）。
   - **语义闸（在场声明）**：`ATLAS_FAST_INTERACTIVE=1` 只能来自人的显式环境，缺它 CDP 再对也 launch——判据在 `session_channel` 一处（不复制）。
   - **事实注**：现行 dispatch argv 恒 `atlas run`（产物不经调度），三件套防「shell 残留 env 渗透」+ 把「剥离≠放行」钉进行为测，非投机功能（红线 #12 自查过：每面有当前真实泄漏面 A.3 支撑）。
6. **业务字面量条款**：`icssit.midea.com`/`9305`/`OSIT00067349`/`zhangfj73`/`isc-app:iservice` **零进**模板/引擎/测断言体（#11）；哨兵扫描测 + 正向对照（fixture 自造值注入可通）。

### B-4 验收（离线十条 + 真机两项）

离线（全进 pytest，零网络零浏览器，F-1 纪律）：
1. 入口三态：合法 flow rc0+清单；坏 flow rc2 点名；冲突无 `--force` rc2；输出无 rc3 路径（对照）。
2. `session_channel` 真值表测（CDP×INTERACTIVE×prod 八向全组合，逐格对条文）。
3. fake browser 双向对照：①两 env 齐→调 `connect_over_cdp` **不调** `launch`；②缺任一→调 `launch` **不调** `connect_over_cdp`；③attach teardown 未关 `contexts[0]`；④MATCH 未设→EnvBlocked 点名、无 `pages[0]` 兜底行为。
4. 声明位五向：①`page_object` 步类名 ∈ 实际生成类名集（真值源单点，F-10② 测翻新）；②`legacy_pages` 步渲染 `legacy_page_call("<fn>", page, ...)` 字面量；③双键同带→compile 静态段拒绝点名序号（且 `atlas compile`/`atlas run` 同红——**同源测**，证 A.5 挂载点收益）；④UI 步无声明+fast→「缺声明位」拒绝、目录无残留、`_pascal(spec.task` 猜名支源码级不存在；⑤纯 API flow `--fast` 照常 rc0（正向放行）。
5. 点分值拒绝：`legacy_pages: a.b` → 静态段拒+消息含「模块位由产物常量给出」。
6. 调度对称三件套：argv 面 `guard_fast_cdp` raise 测（键名单点 `FAST_CDP_ENV_KEY`）；env 面 child_env 三键 pop 测（含正向对照）；**行为面**（lead 已派 support-impl 出测）：pop 剥离 `ATLAS_FAST_ALLOW_WRITES` 后写腿呈 `env_blocked`+reason 点名、**不得变 passed**——「剥离≠放行」，无静默变绿、无写腿消失（裁①关键件）。
7. 委托落盘：`safe_persist` 标量放行/容器拒+WARN 不回显；reason 仍框架文案 only（F-5 复用）。
8. 哨兵：业务字面量=0 命中 + env 注入正向对照。
9. 锁步：prod 前缀与 `config.py:145` 源文本等值测。
10. gap9 E.1 全表不回归 + 门禁五项（AGENTS §2）；基线计数 lead 收口。

真机（需用户授权，进 R 序列）：
- **R6**：同真实用例三形态墙钟对比——attach 复用会话（零 launch 零登录）vs launch+login（F1/F2）vs 旧套件 sole_page 基准；含 attach 实查失效场景 EnvBlocked 点名证据；数据 `docs/gaps/impl_pi/`，lead 经用户批准执行。
- **R7**：`legacy_pages` 委托腿真实旧仓（`ATLAS_LEGACY_WEB`）跑通 ≥1 条，核「No module named 'lib'」教训在产物委托不复现。

---

## C. 接口契约表（增量；跨人只认本表；与 #3 既成逐条对齐）

| 面 | 新增符号/键 | 归属 | 契约 |
|---|---|---|---|
| CLI | 子命令 `atlas export-pytest <flow.yaml> [-o Path] [--fast] [--force]` | cli-impl（#11） | 注册进子命令表（`global_args` parents 同款前后位均可）；校验先 compile 静态段→后导出；rc 0/2、3 不使用；`_user_error` 口径 |
| DSL | flow step 键 `page_object` / `legacy_pages` | cli-impl（#11，落 `FlowCompiler._check_static`） | **形态判据单点=compile 静态校验段**（validate_flow:692 委托 compile，入口自动同源）；非空标量、拒点分、互斥；坏形态点名步骤序号+值，整体不产出（原子应用纪律） |
| 生成闸门 | `emit_pytest_package` 第二道成员资格闸 | emit-impl（#12） | UI 步放行 ⇔ `page_object` 命中实际类名集 **或** `legacy_pages` 良形（第一道已保形态）；拒绝=「缺声明位/页对象 <值> 无对应已批准组件；已知组件：<清单>」语义，串以测钉死；**拒绝先于 mkdir**（F-10③ 现位复用）；**删除** `_pascal(spec.task or spec.slug)` 猜名支（:346/:818-836 翻新） |
| StepSpec | 新字段 `page_object: str = ""`、`legacy_pages: str = ""`（**两独立字段**，不共值域） | emit-impl | 默认空=零涟漪 #115；`_specs_from_flow` 只搬运不重复校验（形态归静态段）；两义拆文本=违 F-2「按文本猜」，故字段必须分立 |
| 页对象 | `legacy_page_call` / `login` / action 骨架 | **已落地（#3），本波零改动** | A.4 既成为准：模块级委托函数、`LEGACY_PAGES_MODULE="lib.pages"`、EnvBlocked 三段文案；渲染产 `legacy_page_call("<fn>", page, ...)` 调用**不新建**第二个委托符号 |
| 产物底座 | `_atlas_fast.session_channel(env) -> str`；`safe_persist(key, value) -> bool`；ENV_KEYS 增补 `ATLAS_FAST_CDP: None / ATLAS_FAST_INTERACTIVE: "0" / ATLAS_FAST_ATTACH_MATCH: None` | support-impl | session_channel=B-3 三条件**唯一判据位**；attach 选页/断连/无兜底/不发登录动作全在 conftest 支路内（fixture 名零新增，FAST_FIXTURE_NAMES 不动）；指示器键**复用** `ATLAS_FAST_LOGIN_INDICATOR` 不新开 |
| conftest 支 | `get_page` 内 `session_channel` 单点分支；attach=「取已登录 page」语义（实查权威不破）；teardown 只断连 | support-impl | B-3.1-3 全行为；prod WARN 行 ASCII 前缀（X4） |
| 调度 | `scheduling/dispatch.py`：独立函数 **`guard_fast_cdp(run_args)`**（键名单点 `FAST_CDP_ENV_KEY="ATLAS_FAST_CDP"`，argv 面显式 raise；与 `guard_run_args` 分离=有意，判据对象不同）；`child_env()` pop 三键（CDP/INTERACTIVE/ALLOW_WRITES）；**行为面契约义务**：pop 后写腿仍 `env_blocked` 点名、**不得变 passed**（测由 support-impl 出，lead 已派） | **cli-impl**（lead 已扩其独占清单；测放 `tests/test_dsl_page_object.py`——lead 指定） | B-3.5 对称三件套逐条测（guard_fast_cdp raise/三键 pop/**剥离≠放行**行为测/在场闸/正向对照 `PYTHONPATH` 不变）；第三键 lead 已裁采纳；本行按 cli-impl 既成交付改写（契约追既成，同 `legacy_page_call` 先例） |
| import 面 | 产物 test 模块**无条件** import（`from _atlas_fast import fast_case[, require_ctx]`；atlas_pages 无条件 import） | emit-impl | 通道差异零进渲染层（emit-impl 意见 4 定稿化）；条件 import=第二形态，拒 |
| README/CLAUDE | `atlas export-pytest` 行 + 「flow→快包已通」措辞 | docs-closer 起草、lead 落笔 | F-11 的「不得声称覆盖 UI 回归」禁令在 #11+#12 落地**且 R7 未跑前**仍限「UI 腿已可产包、真机未验」形态（红线 #15） |

## D. 文件所有权增补（gap9 D 表之外）

| 文件 | 独占 |
|---|---|
| `docs/gaps/gap9b-flow-export.md` | scout（定稿后转 lead，规矩同 F-6） |
| `src/atlas/cli.py`（export-pytest 子命令）+ `src/atlas/dsl.py`（静态段新键）+ `src/atlas/scheduling/dispatch.py`（三件套）+ `tests/test_dsl_page_object.py` | cli-impl（#11，lead 已定） |
| `src/atlas/generator/emit_pytest.py`（闸门翻新/删猜名支/StepSpec 双字段/无条件 import）+ `tests/test_emit_pytest.py`（F-10 旧三测按新语义翻新不删——现位 `test_fast_gate_missing_declaration_rejected_by_name` 等，G4 纪律引函数名） | emit-impl（#12） |
| `src/atlas/generator/emit_fast.py`（session_channel/safe_persist/ENV_KEYS/conftest 支）+ `tests/test_emit_fast.py` | support-impl |
| 跨切判据（真值表/双向对照/三件套调度测的复核位） | xcheck（`tests/test_fast_artifact_executable.py`） |
| 基线计数/裁决 | lead |

## E. 冲突与偏离记账（点名 lead 裁）

1. **`legacy_pages` 值形态 v1→v2**：v1 稿 `<module.fn>`；既成形态（A.4）模块固定 `lib.pages`、声明只写函数名。**v2 采既成**，点分形态在静态段拒绝并解释。不一致即此，已按「以现实现为基线」自裁归位；若 lead 要多模块，走 E-4。
2. **页内 fetch 不采 + 留白**：产物 API 腿恒 urllib/call_api；`legacy_page_call` 委托若落入旧函数内含 fetch 的（如 util.py:103 原型），响应文本经返回值进 ctx——`safe_persist` 标量白名单拦容器/长文本落盘，F-5 拦 reason 面；**auth_expired 等值判据本波不实现**（不复制第二把刀，F-5 同族）。闭合条件：真需求出现时先立判据再实现，不许直接加刀。
3. **校验落点措辞修正**：lead 钉一正确——`validate_flow` 本体即委托 compile（`dsl.py:695-697` docstring 原文），本契约全面改为「compile 静态校验段单点」，v1 稿「validate_flow 另写一份」作废。
4. **多模块委托（`LEGACY_PAGES_MODULE` 可变）**：本波不做（#12）；需求出现先改契约再动码。
5. **child_env pop 第三键（ALLOW_WRITES）为我增补**：**lead 已裁采纳**（2026-11-09 裁①），并要求写成**对称三件套**（argv 面 `--approve`/`--mode` 拒既有+CDP 旗标追加 / env 面 pop CDP+INTERACTIVE+ALLOW_WRITES / **行为面测「pop 后写腿仍 env_blocked 点名、不静默变绿」——剥离放行≠放行成功**）。B-3.5/C 表/B-4 已按裁同步。
6. **方法级页对象骨架（每 task 一方法）推迟**：**lead 已裁同意**（裁②）——本波保持 #3 单 `action()` 基线，不留两形态；重启条件=第一条真需要多方法的 flow 落地时判据测同批上（推迟+理由+重启条件记账法保留）。#12 若想做，发消息给 lead 扩契约。
7. **子命令名**：**lead 已裁改 `atlas export-pytest`**（裁③）——`pytest` 与可执行命令同名在三处生歧义（README/CLAUDE 两行同名、shell 补全/CI 里 `atlas pytest -q` 像漏前缀、新人 copy-paste 混淆），`export-pytest` 与 `review-code`/`devops-sync` 动宾先例同构，未实现期换名仅改契约。参数集不变（`[-o DIR] [--fast] [--force]`），B-1/C 表已同步。

## F. #8 对账证据表（scout，2026-11-09；供 #7 落地注引用为证据索引）

> **表头口径（防误读，显式声明）**：本表是**判据对账**——实查方式=当日 grep/读码/读测体判据逻辑，**未复跑测试**。「闭合」一律指「测在位且判据非恒真（按 F-23 (A)断言文案 (B)夹具真构造 (C)放行对照 三判据读测体核实）」，**不指「今日跑绿」**；跑绿与基线数只认 verifier #6 当日输出。测的引用形态=文件::函数名（G4 纪律，不引测试行号）；实现行号为报告当日对照值，引用前请重定位（F-21）。

### F.1 闭合格（条文有 + 测在位 + 非恒真，实查过）

| 格 | 条文出处 | 测（文件::函数名） | 实查方式 |
|---|---|---|---|
| ⓿ 符号存在（33/33） | 本文件 C 表全行 + gap9 C.1/C.2 | 存在性即 grep 结果：`session_channel`/`safe_persist`/`framework_reason`/`REASON_MAX_LEN`/`FAIL_CLASS_MAP`/`classify_fail_class`/`NotWired`/`RUN_STATUS_VOCAB`/`TICK_MS`/`_TRANSIENT_MARKS`/`launch_kwargs`/`fast_case`/`RunWriter`/`write_fast_artifacts`/`guard_fast_cdp`/`FAST_CDP_ENV_KEY`/export-pytest/cli 段/dsl 静态段（cli.py/dsl.py/dispatch.py 命中） | grep 逐符号计数（非零）+读定义 |
| ⓬ INTERACTIVE 三向 | 本文件 B-3 :80 三条件 + C 表 ENV_KEYS 行 | `tests/test_emit_fast.py::test_env_knobs_manifest_covers_every_template_read`（set 差集双向+死旋钮注入必红，docstring 带 B-3 落地时的历史红证）；`::test_session_channel_truth_table_eight_way`（三条件八向逐格）；`tests/test_dsl_page_object.py::test_child_env_strips_three_keys_from_base`/`::test_child_env_strips_keys_from_process_env` | grep :198 实读 + pop :44-45 + 读测体 |
| attach 四态选页（F-23 ABC 全中） | 本文件 B-3.1（MATCH 点名/无兜底） | `tests/test_emit_fast.py::test_attach_match_named_page_selection_zero_fallback`：0 命中断页数+匹配值+「pages[0]」字样、多命中夹具真放 2 页断「不猜」、**唯一命中身份对照+别人的页未碰** | 读测体+夹具构造核 |
| attach 在场闸/prod 回落 | 本文件 B-3.2/B-3.4 | `::test_attach_requires_present_declaration_launch_otherwise`；`::test_attach_prod_falls_back_to_launch_with_warn`（含 sit 正向对照+browser.close 生命周期差异；WARN 三要素正文断言现由 G3 补落，lead 核到 `::test_prod_cdp_warn_three_elements_f27`） | 读测体 |
| 位置兜底 AST 禁令 | 本文件 B-3.1「绝不回落」 | `::test_no_positional_fallback_in_conftest`（Subscript 节点扫=0 + detector 注入正对照） | 读测体 |
| 指示器单点复用 | 本文件 B-3.2/F-1 口径 | `::test_channel_judgment_single_site_indicator_authority_shared` | 读测体 |
| E.1.2 fast=False 零涟漪 | gap9 E.1.2 | `tests/test_emit_pytest.py::test_fast_off_snapshot_byte_identical`+`::test_fast_off_skip_skeleton_still_present`+`::test_nonfast_package_readme_and_files_unchanged` | 读测体 |
| E.1.3 禁词/形态+对抗 | gap9 E.1.3 + F-3/F-4 | `tests/test_emit_fast.py::test_tick_form_judgment_rejects_injected_multiply`（注入 `TICK_MS * 2` 验红）+`::test_no_fixed_sleep_channel_anywhere`；`tests/test_fast_artifact_executable.py::test_no_fixed_sleep_in_artifacts`/`::test_waiters_use_tick_only_behaviorally` | 读测体 |
| E.1.4 锁步 | gap9 E.1.4 | `test_transient_marks_lockstep_with_atlas_waits`/`test_tick_ms_lockstep_with_waits_source`/`test_run_status_vocab_lockstep_with_failures`/`test_fail_class_values_are_failure_class_members`/`test_session_launch_args_lockstep`/`test_prod_prefix_lockstep_with_config_py`（源文本提取比对，非手抄） | 读测体 |
| E.1.5 往返无洗绿 | gap9 E.1.5 + F-5/F-7 | `tests/test_emit_fast.py::test_allure_roundtrip_no_greenwashing`；`tests/test_fast_artifact_executable.py::test_runlog_writer_row_shape_and_allure_roundtrip`/`::test_runlog_reason_boundary_f5`；`test_fast_artifact_executable.py::test_playwright_absent_records_env_blocked_not_green` | 读测体；tests/ 内无 http.server/真请求核 F-1 ✓ |
| E.1.7 watchdog | gap9 E.1.7 + F-6 | `test_watchdog_writes_timeout_line_and_cannot_be_overwritten`（假时钟）+`test_watchdog_fake_clock_timeout_row_never_flips` | 读测体 |
| E.1.8 写闸/strip 仍拦停 | gap9 E.1.8 + F-13 行为面 | `test_write_gate_blocks_body_reaches_runlog`/`test_stripped_allow_writes_still_blocks_never_passes`/`test_write_gate_blocks_by_op_not_text` | 读测体 |
| E.1.9 依赖面零新运行时 | gap9 E.1.9 + F-9 结论 | `test_no_new_runtime_deps_in_generated_module`/`test_artifact_import_surface_is_bounded`/`test_conftest_atlas_fast_imports_cover_usage`/`test_import_surface_selfcontained` | 读测体 |
| E.1.11/F-2 未接线归因 | gap9 F8 注记 + F-2 | `test_notwired_is_envblocked_subclass`/`test_notwired_lands_env_blocked_with_positive_control`/`test_notimplemented_error_is_not_unwired`/`test_fast_artifacts_zero_nie_slow_control_has_it`（快包 0 裸 NIE+慢包保留反证，F-23 C 型双向） | 读测体 |
| F-9 拒绝+纯 API 放行 | gap9 F-9（lead 裁决） | `test_fast_gate_ui_without_components_rejected_by_name`/`test_fast_gate_pure_api_without_components_passes`/`test_fast_package_pure_api_without_components_writes` | 读测体 |
| F-10 猜名支删除/类名集同源/无残留 | gap9 F-10 + 本文件 B-2 | `test_guess_branch_removed_source_level`（源码级扫 `_pascal(spec.task` 形态=0）/`test_fast_gate_matches_pages_module_class_set`/`test_fast_gate_unknown_component_rejected_by_name`/`test_fast_package_gate_rejects_before_any_write`/`test_fast_package_missing_declaration_rejected_no_residue` | grep+读测体 |
| 声明位形态互斥/点分拒/同源 | 本文件 B-2（两键并列） | `tests/test_dsl_page_object.py::test_page_object_valid_scalar_compiles`/`::test_two_decl_keys_mutually_exclusive_named`/`::test_legacy_pages_dotted_rejected_explains_module_constant`/`::test_static_gate_same_source_across_compile_and_cli`/`::test_flows_without_decl_keys_untouched`（零涟漪正向） | 读测体 |
| 入口三态/确定性 | 本文件 B-1 | `test_export_pytest_emits_and_lists_files`/`::test_export_pytest_invalid_flow_rc2_no_dir_created`/`::test_export_pytest_missing_file_rc2_not_rc3`/`::test_export_pytest_nonempty_dir_requires_force`/`::test_export_pytest_deterministic` | 读测体 |
| 调度三件套 | 本文件 B-3.5 | `test_fast_cdp_key_name_single_source`/`::test_build_command_rejects_fast_cdp_smuggled_in_run_args`/`::test_build_command_rejects_cdp_flag_approximation`/`::test_build_command_zero_ripple_for_normal_run_args`（放行对照）/`::test_guard_uses_explicit_raise_not_assert`/`::test_fast_env_keys_judgment_not_duplicated_across_scheduling`/`::test_schedule_add_smuggled_cdp_rc2_and_not_registered` | 读测体 |
| fast_kw 时序侥幸格 | lead G4 前置点名 | `tests/test_gen.py::test_emit_pytest_package_fast_kwarg_signature`（inspect 签名缺省 False 钉死） | 读测体 |
| 哨兵/名册 import-only | 本文件 B-3.6 + F-15/F-18 | `test_no_business_literals_in_templates`/`test_business_sentinel_scan_zero_hits`/`test_no_business_literals_in_artifacts`/`test_consumers_must_import_credential_tables_not_copy_them` | 读测体 |
| ⓭ 板面样本 | F-21/F-22 | #14 红→绿留档于 `test_env_knobs_manifest_covers_every_template_read` docstring；#5 自钉 `tests/test_fast_artifact_executable.py::test_this_file_itself_parses`（:564 SyntaxError 事故形态永久化） | 读文件头+测体 |

### F.2 缺格终态（对账时发现，后续归属已闭环）
- **G1**（SCALAR_MAX_LEN 显式对照断言缺）：真缺确认（lead 复验 `SCALAR_MAX_LEN` 测内 0 命中、名单不含）→ 已立 **#15**（support-impl），不挂本表闭合。
- **G2/G3**：对账后已落——`test_plan_ctx_dict_cross_batch_warn_then_envblocked_f27`（行为链端到端）/`test_prod_cdp_warn_three_elements_f27`（WARN 三要素正文），归 ⓬/F-23(A) 闭合面。
- **G4**：采纳为 lead 引用纪律（测一律函数名，本表即按此写）。

### F.3 本表未覆盖（落地注勿引本表为据）
R5/R6/R7 真机执行记录（待授权，未查到跑过的证据）；`atlas review-code` 快包实跑结果（`::test_review_code_gate_on_fast_case_layer` 测体逐行未读，勿引）；任何基线数字（归 #6 当日输出；知识源绑 ATLAS_KNOWLEDGE_DIR 声明义务见知识源纪律——产物行级命中随私有库回落态变，结论必须绑来源）。

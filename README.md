# Atlas

<p align="center">
  <a href="https://github.com/ojbkxc/Atlas"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="#当前验证边界"><img src="https://img.shields.io/badge/tests-720%20passed-brightgreen" alt="720 passed"></a>
  <img src="https://img.shields.io/badge/engine-Playwright-45edaa" alt="Playwright">
  <img src="https://img.shields.io/badge/self--healing-human--reviewed-orange" alt="Human-reviewed healing">
</p>

> **Knowledge-driven UI/API test automation — heals with human review, never with weaker assertions.**
>
> 可携带、知识驱动的 UI/API 测试自动化平台。不为变绿而放宽断言，失败有实证、造数有护栏、生产写操作恒需人工批准。

**为什么是 Atlas：**

- **UI/API 同流混跑**：`BrowseTheWeb` + `CallApi` 双 Ability，浏览器 Cookie 烘焙给 API 共享会话；`channel: auto` 按 Task 显式能力路由，不猜接口。
- **知识驱动 + 人审自愈**：坑点、选择器、契约是数据而不是引擎里的 if/else；知识命中与修复提案必须人审，**绝不放宽断言**。
- **旧用例零改写**：`LegacyAdapter` 原样运行既有 pytest 套件，不复制、不改写、不替换原引擎，支持增量迁移。
- **生产安全护栏**：生产禁写、create 配额前置占用、自动建单强制「非人工创建」标记、Agent 风险分级超阈值必须人工批准。

**引擎与业务解耦，知识即数据**：Demo 只是第一个业务插件，更换业务时替换插件和知识库，不重写 Actor、驱动与执行底座。

## 已实现

- **Screenplay Actor**：`BrowseTheWeb(page)` + `CallApi(session)` 双 Ability；同一业务流 UI/API 混用，浏览器 Cookie 可同步给 API。
- **AI 可拼装 Task**：每个 Task 强制声明 `domain`、`intent`、`requires`、`produces`、`channel`；按目标递归选依赖并拓扑排序。同一产出键有多个生产者时显式报错点名，不静默择一。
- **双轨资产**：自己的 YAML DSL；`LegacyAdapter` 直接运行同事 `legacy-suite` 原项目，不复制、不改写其 17 条用例。
- **三种模式**：`run` 全速（不在每步截图）、`debug` 慢速/快照、`heal` 失败现场。
- **CDP 冻场接管**：`CDP_DEBUG=1 CDP_PAUSE_STEP=n atlas run` 在指定步骤前冻住，另开终端 `playwright-cli attach --cdp=…` 人工接管调试，按 Enter 继续跑完——与 legacy-suite 调试习惯同构。
- **审核吞吐入口**：`atlas review` 把失败草稿/探索候选汇入一个证据卡片队列；批准按 kind 分发——坑点进知识（`--remedy`）、选择器候选进组件库（`--component/--element`）、trace 步骤拒之塔外；`--reject` 留原因不删记录，`--stats` 看每周吞吐；决策落 `review_decisions.jsonl`。
- **探索会话与沉淀**：`atlas explore` 开非生产 CDP 会话给外部大脑（playwright-cli/Claude 类）接管；回车记证据 checkpoint，`atlas explore --compile` 把 trace 编译成 pending_review 草稿——探索永不直接成资产。
- **真实项目对接映射**：`ATLAS_ENV_PREFIX`/`ATLAS_LEGACY_WEB`/`ATLAS_DEMO_API_*`/`ATLAS_APP_HEADER_*` 一组开关把仓库自带的虚构 demo 映射到真实旧项目，引擎零业务字面量。
- **探测式供给**：`find_only` / `find_or_create`，优先复用数据；生产禁写、自动建单必须带「非人工创建」、create 配额。
- **失败实证与分类**：`env_blocked/session/interaction/contract/data/assertion`；支持精确 API 重放辅助判别。
- **知识与自愈**：30 条首批 Demo 结构化坑点；知识命中、修复提案、知识写回与自愈均要求人审，绝不放宽断言。
- **契约分级**：核心字段强校验、高频通用规则弱校验、最小响应校验；不按 数百条接口盲目生成强 schema。`atlas contracts` 展示与运行时校验共用同一私有→包内回退来源；契约定义在构造期钉死形态（required 类型值/路径键写坏当场点名，配合逐文件回退——手改坏的私有契约 JSON 按「文件损坏」降级包内默认，不再让一个手误连坐整条断言链）；`atlas contracts --validate` 只读结构自校验，点名空 path/name「在清单里冒充覆盖率、运行时却静默跳过、永不生效」的契约与无法解析的契约文件（不发真实请求，有问题 rc=2）。
- **外部资产适配**：原 `DemoApi` 接口语义、`toolbox` 感知/重放、runlog 风格 JSONL 记录。
- **真实运行装配**：`DemoRuntime` 一次登录、复用旧项目 `.env`、Cookie 共享、白名单 DemoApi、契约与探针统一注入。
- **复杂业务与进化**：按需状态机、经验草稿、人审提升知识、API 实证归类、runlog 格式导出。
- **Agent 决策层**：`AtlasAgent` 按元数据规划 → 风险分级（read_only/low/medium/high）→ 超阈值必须人工批准 → 决策留痕 `reports/agent_decisions.jsonl`（含拒绝、半途执行失败/中断与 `executed` 完成清单）。生产写操作恒为 HIGH。
- **create 可插拔**：`ScriptedCreator` / `make_scripted_creator` 挂接**已验证**的建单脚本；配额在调用前占用，结果必须带「非人工创建」与单号。
- **跨平台**：纯 Python、Playwright、`pathlib`；Windows/Linux 使用同一套代码。
- **DSL assert 块**：`assert:` 支持表达式断言（`pt_echo == source_order.pt`、`count >= 5`、`document_no != ""`，六种比较运算符）+ 内联 Task 断言（`task: assert_api`）+ 键值断言；compile 提前验证 task 引用。结构校验（步骤形态/未注册 Task/assert 块/requires 数据供给块/flow 级 on_env_blocked）统一在 `validate_flow` 单一来源：`atlas compile` 与 `atlas run` 共用——坏 flow 登录**前**即 rc 2，不再先起真实浏览器+走真实 SIT 登录才在 compile/provision 裸崩。
- **运行入口配置预检**：`atlas run`/`agent --execute` 在启动浏览器**之前**检查环境 URL 与 DemoApi 适配路径（`runtime.check_demo_api`），缺失点名 `DEMO_URL_*`/`ATLAS_DEMO_API_PROJECT` 一行 stderr + rc 2。旧实现合法 flow+缺 URL 裸 traceback，`ATLAS_DEMO_API_PROJECT` 指错时浏览器已开、真实 SIT 登录+Cookie 烘焙都烧完才撞 attach_demo 缺文件（白烧会话、留半截现场）。
- **channel=auto API 路由**：`auto` Task 有 `supports_api=True` 时引擎优先调 Task 自己的 `_perform_api`，失败降级 UI。降级只捕 `(NotImplementedError, RuntimeError)`——契约违规与网络故障照常传播，不被降级掩盖；降级原因随 step 记录落 `runs.jsonl`（`degraded_to_ui` 字段），事后审计能区分「UI 本来就该走」与「API 坏了才退 UI」。
- **语义等待**：`waits.py` 提供 `wait_for_rows`/`wait_for_mask_gone`/`wait_for_vue_field`/`wait_for_url_change`，替代硬 sleep；表格按「attached → loading mask 落定 → vm.data」判定，合法空表（无数据业务事实）返回 0 不误报超时，导航期执行上下文销毁容忍重试。
- **会话保活**：`CallApi.set_keepalive(callback)` 检测 `auth_expired` 自动重新烘焙 Cookie，支持间隔保活。
- **造单回收**：run 退出时把本次配额内建过的单自动落盘 `reports/recycle_list.jsonl`（默认读取路径与其同源，不再赌 CWD）；`atlas recycle` 列出供人工回收，不自动删除。
- **JMeter 导入**：`scripts/import_jmeter.py` 把 .jmx 工程转 Atlas DSL YAML，供生产巡检。
- **私有知识目录**：`ATLAS_KNOWLEDGE_DIR` 指到仓库外——读逐文件回退包内虚构默认，人审批准（组件/坑点/坐标）只写私有目录，真实业务知识零入库。
- **BU 坐标双轨（离线管线）**：`atlas overlay` 对两组业务坐标做逐键 diff，差异生成 `bu_overlay` 草稿进审核塔；批准才落私有 overlay 库。租户=坐标不是分支；真实 BU-B 双跑仍待会话值。
- **项目初始化向导**：`atlas init` 一键生成含 `.atlas-root/.env/pyproject.toml/flows/smoke.yaml/tests/test_smoke.py/scripts/run_allure.py` 的新项目骨架；`.env` 值全空无凭据、pyproject 不含 allure-pytest（报告走 `atlas allure` 纯 stdlib 直出 Allure 2.39.0）。
- **微服务配置生成器**：`atlas config add <domain>` 生成 `plugins/<domain>/` 插件骨架（plugin/tasks/probes + knowledge 空骨架），Task `perform` 诚实抛 `NotImplementedError`；`--mapping` 只出 ATLAS_* 映射片段（零代码）。外部插件经 `resolve_plugin()` 缝动态加载，不碰引擎代码。
- **代码质量评审**：`atlas review-code` 对 pytest 用例 / DSL flow YAML / 契约 JSON 做质量评审，输出含评分的 Allure 2.39.0 风格报告。5 维度权重计分（断言强度 .25 / 安全 .20 / 可维护 .20 / 覆盖 .20 / 重复 .15），28 条反模式规则（ASSERT-*/GREEN-*/SAFE-*/MAINT-*/COV-*/DUP-*），critical 硬顶 ≤59。核心价值：反向侦测「为变绿而放宽断言」（弱断言、假绿、生产写风险），三种互补手法（静态反模式 / 基线差分 / 运行史交叉）。static/knowledge 模式纯 stdlib 离线，ai 模式默认关闭不上传代码。rc 0 通过 / 1 门禁失败 / 2 用户错误。与 `atlas review`（审核塔）边界：review 审运行产物、批准写知识；review-code 审源码、只出报告、永不写知识。

## 快速开始

```bash
cd Atlas
python -m venv .venv
# Windows: .venv/Scripts/python -m pip install -e ".[dev]"
# Linux:   .venv/bin/python   -m pip install -e ".[dev]"
python -m pip install -e ".[dev]"
python -m playwright install chromium
cp .env.example .env              # Windows 可复制为 .env，不提交真实凭据
atlas doctor
atlas tasks
atlas compose --goal document_no
pytest -q
```

未安装包时也可在项目根运行：

```bash
PYTHONPATH=src python -m atlas doctor       # Windows bash；PowerShell 用 $env:PYTHONPATH="src"
PYTHONPATH=src python -m pytest -q
```

## 原样运行同事的 17 条用例

```bash
# 只检查路径和命令，不执行
atlas legacy --dry-run

# 在 ../legacy-suite/legacy-web 下原样执行；沿用其 .env 与 conftest
atlas legacy

# 单条 TC（示例）
atlas legacy "testcases/demo_sit_regression/test_demo_sit_regression.py::TestDemoSitRegressionPlan::test_tc1_sales_order_default_pt"
```

Atlas 的兼容层**不替换同事引擎**，因此结果、浏览器生命周期、执行速度和原有 skip 语义不被偷偷改变。`LegacyStep` 也可将旧 `step_N(page, ctx)` 包成 Screenplay Task，用于增量迁移。

## 新建 Atlas 项目

```bash
# 生成新项目骨架（含 .atlas-root/.env/pyproject/flows/tests/scripts/README）
atlas init myproj --system ics --module so

# 可选：把 Atlas 仓库钉成 file:// 依赖
atlas init myproj --system ics --module so --atlas-path /path/to/Atlas

# 进入项目、建虚拟环境、装依赖、跑冒烟
cd myproj
python -m venv .venv
.venv/Scripts/python -m pip install -e .
python -m playwright install chromium
cp .env.example .env  # 填真实 URL 和凭据
atlas doctor
pytest -q

# Allure 报告：pytest → atlas allure → allure serve
python scripts/run_allure.py test    # 跑 pytest，产 reports/runs.jsonl
python scripts/run_allure.py emit    # runs.jsonl → Allure 结果集
python scripts/run_allure.py serve   # 临时服务并浏览器打开

# 添加微服务/系统配置
atlas config add acme                # 生成 plugins/acme/ 插件骨架
atlas config add acme --mapping      # 只出 ATLAS_* 映射片段（零代码）
```

生成的骨架 `.env` 值全空（无凭据）；`pyproject.toml` 不含 `allure-pytest`（报告统一走 `atlas allure` 纯 stdlib 直出 Allure 2.39.0）。骨架 Task `perform` 诚实抛 `NotImplementedError`，契约/坑点全空（不伪造覆盖率）。

# 接口文档 → 测试代码生成（只生成不执行）
atlas gen order-api.md                # Markdown/OpenAPI/Swagger/纯文本 → flow YAML + 契约候选 + 探针骨架
atlas gen pet.yaml --scenario b      # 场景：a单接口/b链路/c批量/d写操作前置
atlas gen api.md --strict            # 低置信度/占位符未接 env → rc 2（CI 门禁）
atlas gen api.md --check-only        # 只解析+编译自检，不写文件
atlas gen api.md --to-tower          # 生成摘要登记 pending_review 进审核塔（trace_step，不可 --approve）
```

`atlas gen` 把接口文档离线翻译成可被 `atlas compile` 校验、可被 `atlas run` 执行的 Atlas 资产（flow YAML + 契约候选 + 探针骨架 + report.json），**只生成不执行**（红线 #9）。产物全部 `pending_review` 进 `atlas review` 塔，`trace_step` 不可 `--approve`（人工写成 flow YAML 后 compile）。与 `scripts/import_jmeter.py` 的分工：JMeter 迁流量保真，`gen` 从文档起测控结构。契约候选落独立 `.candidate.json` 不进运行时注册表，人审后并入私有知识目录。写场景（save/submit/delete）强制 `read_only: false` + `on_env_blocked: stop`；探针只实现 `find()`，`create()` 恒 `NotImplementedError`（红线 3/12/16）。退出码：0 成功 / 2 输入问题 / 3 结构自检失败。

## 项目结构

```text
src/atlas/
├── actor.py / core.py / abilities.py    # Screenplay 与双通道
├── session.py / engine.py / recorder.py # 浏览器、策略、记录、快照
├── config.py / failures.py             # .env、生产防线、分类学
├── dsl.py / state_machine.py           # 线性流、按需状态机与变量插值
├── probes.py / contracts.py            # 数据供给与契约
├── knowledge.py / healing.py           # 知识命中与人审自愈
├── experience.py / reporting.py        # 经验草稿、人审提升、runlog 导出
├── runtime.py                          # 真实登录、API、探针、契约统一装配
├── agent.py                            # 规划、风险分级、审批、决策留痕
├── creators.py                         # 已验证建单脚本的接入契约与护栏
├── legacy.py / toolbox.py / cli.py     # 旧项目、感知器官、入口
├── generator/                           # Gap3 文档→代码生成（只生成不执行）
│   ├── sources.py / openapi.py / markdown.py / plaintext.py  # 多来源嗅探与解析
│   ├── emit_flow.py / emit_contract.py / emit_probe.py       # DSL/契约/探针映射
│   └── checklist.py / tower.py          # 编译自检、审核塔登记
└── plugins/demo/                        # 可替换的 Demo 业务插件
    ├── tasks.py                        # 10 个核心 Task，委托 pages.py
    ├── probes.py                       # SalesOrder / Customer 探测
    └── knowledge/                      # 组件/业务/环境/30 条坑点/契约/会话默认值
```

## 真实混用与 DSL

```bash
# UI 登录建立会话，DemoApi 只读查询；不造数、不输出 Cookie
python examples/hybrid_readonly.py

# 运行 DSL，真实注入探针、契约、知识和执行记录；自动建单默认配额 0
atlas run examples/sales_order.yaml --account demo_user

# 复杂单据状态机：先校验结构/目标路径，再决定是否执行
atlas machine examples/so_change_states.yaml --target approved

# 知识检索
atlas selectors                # 组件清单与缺选择器提示
atlas selectors payment_terms  # 双模型与选择器
atlas knowledge BUG-653

# Agent：默认只规划；执行需风险允许或 --approve
atlas agent --goal document_no
atlas agent --goal document_no --execute --approve

# 离线编译校验 flow YAML（不登录、不执行）
atlas compile examples/return_from_so.yaml

# 运行记录：聚合与转换
atlas summary reports/runs.jsonl
atlas report reports/runs.jsonl reports/runlog-steps.jsonl

# Allure 2.39.0 结果集：runs.jsonl → allure-results/*-result.json（纯 stdlib，不拉浏览器）
# 状态映射：passed→passed；failed→failed+atlas.fail_class；env_blocked/interrupted→skipped+label（不洗绿）；unknown→broken
atlas allure reports/runs.jsonl --out reports/allure-results
atlas allure reports/runs.jsonl --serve   # 生成后打印 allure serve 命令（不自行拉起）

# Allure 结果集解析（汇入侧）：分类汇总 + 自愈提案/审核塔草稿
# 自产结果用 atlas.fail_class label 直接采用；外部 SCS 结果经 classify_text(message+trace) 归类
atlas allure-analyze reports/allure-results                        # JSON 汇总
atlas allure-analyze reports/allure-results --format text          # 人读版
atlas allure-analyze reports/allure-results --to-tower              # 未命中项登记 pending_review 进审核塔

# 造单回收：列出自动创建的单据（不自动删除）
atlas recycle
atlas recycle --input reports/recycle_list.jsonl --cleanup

# JMeter 工程转 Atlas DSL
python scripts/import_jmeter.py patrol.jmx -o examples/patrol.yaml

# BU 坐标双轨：diff 只读；--to-tower 逐键进塔人审；批准：atlas review --approve <id> --remedy "<目标BU真实值>"

# 已批准坐标接进执行链：ATLAS_SESSION_* 显式 env 优先于 --bu
atlas run examples/atlas_core_verify.yaml --bu BU-A
atlas overlay bu_overlays.json --diff BU-A "BU-B (sample)"
atlas overlay bu_overlays.json --diff BU-A "BU-B (sample)" --to-tower

# CDP 冻场：步骤 3 前冻住，另开终端 playwright-cli attach --cdp=http://localhost:9222
CDP_DEBUG=1 CDP_PAUSE_STEP=3 atlas run examples/sales_order.yaml --mode debug

# 审核塔：看待审草稿（证据卡片），批准按 kind 分发：坑点进知识 / 选择器候选进组件库
atlas review
atlas review --approve a1b2c3 --remedy "点保存前先等 .app-main 出现"
atlas review --approve d4e5f6 --component order_header --element save \
    --remedy ".app-main button:has(i.demoicon--save)"
atlas review --reject d4e5f6 --reason "已有坑点覆盖"
atlas review --kind selector_candidate   # 塔堆高了按类清
atlas review --group                     # 聚类视图：批量类压表、坑点留全卡
atlas review --kind selector_candidate --template mapping.jsonl  # 导出决策模板，人填后…
atlas review --apply mapping.jsonl       # 批量批准：仍逐条分发留痕，空行失败点名
atlas review --reject-kind trace_step --reason "步骤素材不进塔"   # 一类驳回
atlas review --stats

# 代码质量评审：扫描 pytest/DSL/契约，输出含评分的 Allure 2.39.0 风格报告
atlas review-code tests/                          # 默认 static + allure
atlas review-code tests/ --review-mode knowledge  # 知识库增强（仍离线）
atlas review-code tests/ --format json --fail-under 75   # CI 门禁
atlas review-code tests/ --baseline reports/runs.jsonl    # 交叉运行史侦测"变绿放宽"
atlas review-code tests/ --to-tower               # findings 进审核塔（kind=code_review）
atlas review-code tests/ --review-mode ai         # AI 增强（默认关，需显式配置 brain）

# 探索会话（非生产）：外部大脑接管浏览器，回车记 checkpoint，结束后编译成草稿
atlas explore --account demo_user
atlas explore --compile reports/explore_20261103_101500.jsonl
```

### DSL 扩展字段

| 字段 | 作用 |
|---|---|
| `channel: ui/api/auto` | 覆盖 Task 的通道意图（`auto` 需 Task 显式支持 API） |
| `on_env_blocked: skip/stop` | 环境阻塞时跳过该步（记入 `ctx["_steps_skipped"]`）或终止；默认 `stop` |
| `requires.<key>.on_env_blocked` | 数据供给失败时跳过整条数据条件，而不是伪装成“找到了” |
| `task: assert_api` | UI 之后按接口断言真实落库值，支持 `data.0.paymentTerms` 点路径 |
| `task: legacy_step` | 把旧 `step_N` 接进 Atlas 流程，不改旧代码 |
| `task: legacy_case` | 把**整条**旧用例（yaml 骨架 + 同目录 `step_N`）委托旧 `run_case_py`：ctx 播种、断言、抽取、runlog、SKIP 语义全部沿用旧实现；SKIP 落 `env_blocked`，`write: true` 在 prod 被写护栏拦截。见 `docs/case-migration-mapping.md` |

`DemoRuntime` 优先使用 Atlas `.env`，缺失键再继承旧项目 `.env`，不覆盖已有环境变量、不复制凭据。白名单只读操作通过 `demo_api` Task 调用；禁止 YAML 任意访问 Python 对象。

## Screenplay 最小示例

```python
from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.composition import compose_for_goal

open_page = FunctionalTask(
    TaskMetadata(domain="demo", intent="打开页面", produces=("page_ready",)),
    lambda actor: {"page_ready": True}, "open_page")
read_data = FunctionalTask(
    TaskMetadata(domain="demo", intent="读取数据", requires=("page_ready",), produces=("data",)),
    lambda actor: {"data": {"value": 1}}, "read_data")

actor = Actor()
for task in compose_for_goal([read_data, open_page], "data"):
    actor.attempts_to(task)
assert actor.ctx["data"]["value"] == 1
```

`channel: auto` 是**路由意图**，不是无限制兜底。只有 Task 明确实现并启用 API 操作时才走 API；不会把未验证的 UI 业务按钮随意替换为接口调用。

## 数据与失败口径

- DSL 的 `requires` 声明业务条件，不写死历史单号。
- `find_only` 找不到时明确 `EnvBlocked`；不擅自造数。
- `find_or_create` 的 create 必须由业务插件显式实现；当前 Demo 探针的默认 creator 为空，**不伪造已跑通的 API 建单**。
- UI 失败需要 API 实证时，由业务流提供精确重放器；引擎不猜测接口，尤其不自动重放写接口。重放是尽力而为的诊断：单条重放器抛错/返回坏形态（非映射）时跳过试下一条，全坏回落 `classify_failure`——定性器绝不先于被定性对象死，不把原用例异常顶掉、不丢 results/经验草稿/现场取证。
- 已知环境限制不伪装成 PASSED，也不通过修改断言变绿。

## 配置

完全沿用 `legacy-suite` 的 `DEMO_USERNAME`、`DEMO_PASSWORD`、`DEMO_PWD_{账号大写}` 和七个 `DEMO_URL_*` 键名。默认从 Atlas 根读取 `.env`；旧用例仍从旧项目根读取自己的 `.env`。`.env` 支持 UTF-8 / UTF-8 BOM（记事本）/ UTF-16（PowerShell 5 默认 `Set-Content` 编码）；无法解码或键名含空格时报错点名文件，绝不静默跳过——`.env 静默不加载 = 凭据凭空消失`，比崩溃更难排查。

键名前缀默认 `DEMO_`（对应仓库自带的虚构示例）。指向真实旧项目时用 `ATLAS_ENV_PREFIX` **整体**切换（如 `ISERVICE_`），URL 与凭据键一起换，不会只换一半。

对接真实资产时按需覆盖（默认值即虚构 demo，不改也能跑离线）：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `ATLAS_ENV_PREFIX` | `DEMO_` | 凭据/URL 键前缀 |
| `ATLAS_LEGACY_PROJECT` | `../legacy-suite` | 旧 UI 项目根 |
| `ATLAS_LEGACY_WEB` | `legacy-web` | 旧项目内 web 子目录（旧版可能是 `iservice`） |
| `ATLAS_LEGACY_TEST_NODE` | `testcases/demo_sit_regression/test_demo_sit_regression.py` | `atlas legacy` 默认节点 |
| `ATLAS_DEMO_API_PROJECT` | `../legacy-api` | 旧 API 项目根 |
| `ATLAS_DEMO_API_FILE` | `demo_api.py` | API 客户端文件名 |
| `ATLAS_DEMO_API_CLASS` | `DemoApi` | API 客户端类名 |
| `ATLAS_DEMO_API_SPEC` | `demo_endpoints.json` | 接口目录文件名 |
| `ATLAS_APP_HEADER_KEY` / `VALUE` | `demo-app` / `legacy-web` | 后端校验的业务识别头 |
| `ATLAS_RUNLOG_HOME` | `../runlog` | runlog 目录 |
| `ATLAS_TOOLBOX_PROJECT` | `../toolbox` | 感知器官目录 |
| `ATLAS_BROWSER_CHANNEL` | `chrome` | 浏览器通道 |
| `ATLAS_KNOWLEDGE_DIR` | 空（只读库） | 私有知识目录：读逐文件回退包内虚构默认，人审批准写只落这里；未设置时写回被拒绝并点名本变量（包内资产只读，红线 #11） |

## 当前验证边界

- 离线测试：**720 passed**，Python 3.13；编译、CLI、外部资产路径发现已通过。
  （#136/#137 轮实测时环境已接入真实 `.env`：`base_url_source=legacy.env`、`demo_api=true`、
  `knowledge_pitfalls=104`，doctor 因此 rc0——虚构默认下 rc1 的旧前提仅适用于裸仓库。
  634→637 为 #140 自身补的 3 条感知器官错误形态回归测；637→668 中 #141 自身补 3 条
  Cookie 烘焙/保活回归测，其余 +28 为并发队友自有增量，如实归因。）
- **Cookie 烘焙域判据与 401 保活盲区对抗探针（#141）**：探针实锤三面可达缺陷——
  ① `bake_from_playwright` 旧判据 `"." in domain` 只挡裸 TLD（com），挡不住二级公共后缀：
  cookie `domain=com.cn` 对 host=`ics.example.com.cn` 命中并带前导点存入，凭据会发向任意
  他企业 `*.com.cn` 注册域，违 §3「Cookie 只烘焙到匹配域」；现精选公共后缀表拒绝
  （defense-in-depth：真实浏览器按 PSL 不给这种 cookie，插件/异常桥接给出时绝不照收），
  合法父域 `example.com.cn` 照常烘焙（#115）。② 域名比较大小写敏感——真实 SSO 大写
  `Domain=EXAMPLE.COM` 永不命中被静默丢弃，用户只见 401 不见根因；现归一下写比较与存储，
  无关大写域（`.EVIL.COM`）仍拒，不是无脑放行。③ keepalive 只认 `auth_expired` 标记，
  而该标记仅在 200+HTML 登录页伪装时置位——裸 401+JSON（ICS 过期会话最常见形态）
  永不重烘焙，宣称的「api 401 自动重登」对真实形态不成立；现 401 状态码为权威判据，
  403/500/200 不误触发。回归测 3 条（后缀拒绝+合法不误伤、大小写双向、401 触发矩阵）。
- **toolbox 感知器官错误形态折叠假绿对抗探针（#140）**：探针实锤可达缺陷——
  `learn_contracts` 把 bridge 错误形态（`{"status":"error",…}` dict、纯文本「错误: …」、
  None）全部折叠成 `[]` 静默返回 0：调查者读成「没有可登记的接口」，实为感知器官坏了
  （#102 静默假绿），且同一形态 `console_errors` 早在 #105 就点名拒绝——两函数防线强度
  漂移，#120 当年只收敛了成功键名、错误面各修各的（改一漏一 #92）；旧测试甚至把缺陷
  钉成规格（「bridge 错误 dict 不崩不登记 == 0」），本轮翻转该断言并注释归因。现判据
  收敛模块级 `_unwrap_records` 单一真相：合法形态如实放行（空抓包 `{"entries":[]}` 仍
  诚实 0，#115 不翻正常路径），坏形态点名 RuntimeError 带原文。顺带两面：`{"total":231}`
  缺 entries 键旧误报「分页截断」（真因是键缺失，#90 归因错位），现解包先行点名；
  `save_har` 补 #104 漏掉的**错误 dict 后门**——缺 HAR 1.2 `log` 结构的 dict 拒写坏档
  （旧实现类型检查全过照常落盘，下游 DevTools/Postman 打开才炸）。回归测 3 条 + 翻转 1 条。
- **Allure 取证截图断链对抗探针（#139）**：探针实锤可达缺陷——`engine.py` 三处
  `session.capture()` 返回的截图 Path **全被丢弃**，`runs.jsonl` 里 `attachment` 键永不存在；
  `allure_report` 消费侧读该键却没有任何生产方（#106 降级留痕零消费方×#86 回收断链同族）——
  失败现场截图真实落盘 `artifacts/snapshots/`，Allure 报告附件却永远 0 条、证据无声消失。
  第二面：CLI `atlas allure` 不传 `attachments_dir`，消费侧回落 `runs_path.parent`（reports/）
  做基准，root 相对引用解析错位——文件明明在盘上却假报「无法读取」生成占位（#102 伪装同族）。
  修复：engine 新增 `_record_shot`，成功/自愈/失败三处把 capture 返回值按 root 相对写进
  step 记录（根外回落绝对；run 成功 None 不写键——红线 #8 零开销与 #115 零涟漪双守）；
  CLI 显式传 `attachments_dir=config.root`。回归测 4 条（失败取证入记录、run 成功零涟漪/
  debug 成功记、engine→allure 闭环真实 png、CLI root 基准 rc0）。
- **run 轨旧项目路径预检缺失对抗探针（#138）**：探针实锤可达缺陷——pytest 轨的
  `legacy.check_exec_dir` 早在 #90 就点名 `ATLAS_LEGACY_*`，但 **run 轨**（`DemoRuntime.__enter__`
  与 CLI run/agent --execute 预检）只查 DemoApi 路径，从不检查旧项目根/web 子目录：
  `ATLAS_LEGACY_WEB` 手滑（legacy-web vs iservice）要等 `_load(legacy_web/lib/login.py)`
  执行时才撞裸 `FileNotFoundError`——那时真实 Chromium 已起、SIT 登录已烧、还可能留半截现场
  （#103 白烧会话同族），且消息不点名任何配置键（#90 归因漂移）。现判据收敛
  `config.check_legacy_paths` 模块级单一真相（#92 杜绝两份消息漂移），run 轨
  `__enter__`/CLI 两处预检/pytest 轨委托三方共用：浏览器启动前拒绝并点名
  `ATLAS_LEGACY_PROJECT`/`ATLAS_LEGACY_WEB`。回归测 5 条（两分支点名、正确路径零涟漪 #115、
  两轨消息逐字同源、runtime entered==0、CLI rc2 不开浏览器）。
- **会话默认值双源漂移对抗探针（#137）**：`plugins/demo/tasks.py` 的 `_session_default`
  （auto API 快路径 division 兜底，select_customer/read_pt 调用）旧自己 `json.loads` **直读包内**
  `session.json`，绕开 KnowledgeBase 私有→包内回退——探针实锤设 `ATLAS_KNOWLEDGE_DIR` 指真实
  业务后两条链分叉：CLI/运行时（`session_defaults()`）拿私有真实 division，API 快路径拿虚构
  默认查错客户。两份「唯一来源」漂移（#92 改一漏一），红线 #11「真实值只在私有目录」被引擎侧
  旁路击穿；私有 session 手滑缺键时还会崩裸 KeyError。现委托 `DemoPlugin.session_defaults()`
  收敛单一真相，缺键点名「补齐文件或显式设 env」。既有 `test_demo_session_defaults_are_single_source`
  的「.py 不得硬编码业务字面量」扫描当场抓到注释里的示例字面量（防线本身生效，注释改用泛称）。
  回归测 2 条（私有目录同源 + 缺键点名）钉死两面。
- **compose_for_goal 同名去重后门对抗探针（#136）**：探针实锤可达缺陷——同名去重旧判据
  **只比 name**：两份插件清单各定义一个**同名但不同定义**（read_only True/False）的产出方，
  写那份被 `continue` 静默丢弃，多生产者歧义（#84 同红线）从去重后门溜进来，agent 只按幸存
  的读档评估风险（读伪装成唯一生产者，写操作被无声抹除或反向：幸存者是写时风险档被悄悄推高）。
  与 #128 contracts「同 (method,path) 不同定义 last-wins」完全同形态。现同名命中比对 frozen
  `TaskMetadata` 结构等值：等值=拼接清单去重照常放行（#115 正常路径零涟漪，lambda 实例不同
  不碍事），不等值显式 FlowCompositionError 点名差异字段（intent/read_only/…）。回归测 2 条
  钉死两面。
- **review --apply 决策文件字节面 + 只读知识库写闸门（#135）**：探针实锤两缺陷。① 第 8 读点收口：
  `review --apply` 的决策文件 `read_text` 无字节面防线——中断腰斩尾行裸 `UnicodeDecodeError` traceback
  崩在读文件本身（#112 读侧同防线漏网；探针喂合法批准行+腰斩尾字节实锤，半途崩=一条坏字节让
  整批批准零落账）。现 `errors="replace"` 回落：残行 JSON 必败落 failed 点名、截断前合法批准照常执行。
  ② AGENTS 红线 #11「批准永不写包内文件」代码化：未设 `ATLAS_KNOWLEDGE_DIR` 时 `DemoPlugin.knowledge()`
  旧直接返回以**包内目录为写根**的库——本轮验证探针自己踩实：review --approve 一批准就把坑点写进
  git 资产（包内 pitfalls.json 30→31、doctor 当场变红；已 `git checkout` 还原）。判据不用路径包含
  启发式（Windows resolve/符号链接可绕过，且「显式构造」与「忘设 env」无法从路径区分）——
  `KnowledgeBase(root=None, fallback_root=包内)` 类型契约即只读库，`_writable_root()` 闸门在
  save_reviewed/save_component_selector/save_overlay 三条唯一写路径**写盘前**抛 PermissionError
  点名 ATLAS_KNOWLEDGE_DIR（批准先写知识后改队列，闸门最前置=零半状态）；CLI approve/apply 分支
  catch PermissionError→rc2 一行 stderr。读侧回退包内虚构默认照常（读不残缺）。overlay/compile
  两读点复核 sound。回归测：2 条既有契约测试更新为只读契约断言 + 写闸门点名测 + 腰斩决策文件
  存活测 + `tests/test_conftest_isolation.py` 2 条钉死 #134 隔离器（泄漏测试内可见/跨测试不可见，
  防隔离器被删后静默漂移）。
- **apply_bu 坐标形态对抗探针 + 测试环境隔离（#134）**：三面。① `plugins/demo/tasks.py`
  `apply_bu` 面A：顶层 overlays 由 `load_overlays` 保证是 dict，但**某个 BU 的值**手滑存成
  数组/字符串（合法 JSON 但形态不对，#127 同族）时旧 `overlays[bu].items()` 裸
  AttributeError——CLI `run --bu` 只捕 KeyError，漏网变裸 traceback 违反用户输入 rc2 口径；
  现点名「坐标形态非映射」抛 ValueError，CLI catch 扩到 `(KeyError, ValueError)`。
  面B/C：旧空值判据 `value in (None, "")` 漏了空容器——`[]`/`{}` 被 `str()` 成字面量
  `"[]"`/`"{}"` 写进 `ATLAS_SESSION_*`，真实执行 switch_to_ou 拿 `"[]"` 必超时（坏形态伪装
  合法坐标，#77 键在值空不算；单号判据 #117「非空标量才算」同纪律收敛）。现 `_is_coordinate`
  只认非空标量（bool/容器/空白一律 ignored），标量数字照常 applied（#115：归因修复不翻正常路径，
  dict 正常路径回归测零涟漪）。② 读侧收口升级为**行为取证**（上一轮仅 grep 静态核对）：腰斩尾行
  字节灌进全部 7 个 append-family JSONL 消费点（reporting×2、experience、review、
  explore.compile_trace、probes.save_for_recycle、cli._recycle）——7/7 全存活，无第 7 处漏网；
  agent journal 确认写侧无读消费方。toolbox `replay`/`chain_replay` 消费面复核：sensor 纯透传
  零形态假设、`ApiEvidence` 用业务方自有 handler——此前「透传诚实不动」结论经复核维持 sound。
  ③ 门禁暴露测试基建真实缺陷（配好本机 `.env` 后 `pytest` 必红 4 个，实锤非猜测）：
  `load_dotenv` 用 `os.environ.setdefault` 写进程环境（产品语义正确），但 `from_root()` 无 root
  时加载 **cwd=仓库根的真实 `.env`**（用户按 `.env.example` 接真实项目的标准姿势）、doctor 装配
  `load_dotenv(legacy_project)` 把 tmp legacy/.env 的 `ISERVICE_URL_SIT` 等灌进进程——monkeypatch
  只回滚自己显式 setenv 的键，对 setdefault 灌入的未知键无能为力，测试间顺序相关污染。新增
  `tests/conftest.py` autouse 隔离：会话起点清全部 `ATLAS_*/DEMO_*/ISERVICE_*` 前缀键（确定基线）+
  函数级快照恢复。产品代码零改动。回归测 2 条钉死 apply_bu 形态/空容器两面。
- **waits.py 轮询边界对抗探针（#133）**：实锤可达——三处轮询 `except Exception` 无差别重试，
  把**永远不可能自愈**的抛错当导航瞬态烧满超时窗口后谎报「未达 N 行/字段无值/URL 未变化」：
  页面/上下文已关闭（Target closed）、evaluate 内 JS 语法错、业务 JS 自抛的 TimeoutError
  （类型名恰含 "timeout" 但文本不含瞬态签名，走文本匹配不受 `classify_failure` 关键词误判
  同族影响 #114 镜像面）全部被吞——框架故障伪装成业务性等待失败（#102），探针实测会话已死
  仍空烧 10 拍。修复：`_is_transient` 复用 `failures.exception_text` 单一真相（#127/#129
  纪律）按 Playwright 官方瞬态文案（context destroyed/navigation/detached）分流——瞬态照常
  重试穿越 reload，其余立即传播带归因；控制流不受影响（不是 Exception，#114 红线不变）。
  诚实边界：探针初版假设「timeout_ms<=0 会撞底层报错」**被真实 Playwright 证伪**（0=无超时
  文档语义、负值驱动归 1）——未据此改代码，docstring 留取证口径；`wait_for_selector` 直传
  调用保持透传。回归测 4 条钉死（Target closed 立即传播不烧窗口、业务超时不被吞、
  frame-detached 照常重试、URL 读崩传播）+既有 12 条全过零涟漪。
- **reporting.py 报告聚合对抗探针（#131）**：`summarize`/`export_runlog` 对「runs.jsonl 尾行被中断腰斩出非法 UTF-8」整读崩——`with path.open(encoding="utf-8")` 迭代在坏字节处抛 `UnicodeDecodeError`，直接调用方（库消费链）崩；CLI summary/report 因 `UnicodeDecodeError` 是 `ValueError` 子类被 `_user_error` 兜住不崩，**但把含有效行的文件整体拒掉且误归因成「用户输入错误」**（中断写坏的盘不是用户输入错误，运维被引向查自己的命令）。#112 注释「两处读侧必须同防线」当时只覆盖了 experience/review/explore 三处读侧，reporting 两函数是漏网的第 4 处（#92 改一漏一），且本模块 docstring 自己承诺「两个聚合器对任意 JSONL 都不得整体失败」永不兑现。修复：先严格读保真、撞坏字节回落 `errors="replace"`，残行 JSON 解析必败自然落既有跳过分支——截断前的合法行照常聚合，骨架完整仅字符串值含坏字节的行如实以替换字符统计（不静默丢证据也不静默修证据）。回归测 2 条钉死（尾行腰斩不顶整读、中列坏字节如实计数）。
- **回收链坏字节防线（#132，#131 引出读侧总扫描）**：同族再抓两处实锤——① `probes.save_for_recycle` 先读旧清单合并再写回，尾行腰斩坏字节时 `read_text` 裸崩，**读崩=合并写回不执行，本次新造的单号随崩溃一起丢**（单据已真实产生却再无人知道单号，#86 回收闭环被一条历史坏字节掏空，红线 3「保留可回收信息」失效）；② CLI `_recycle` 调用点不在 try 包裹内，同坏字节变裸 traceback——#71 残行计数承诺（「一个坏行不能让人连要回收的单都列不出」）在字节面同样永不兑现，CLI rc2 友好口径（#79/#80）够不着。两处补 `errors="replace"` 回落，与 reporting/experience/review/explore 五方同纪律（#112「读侧同防线」至此覆盖全部 JSONL 消费点，第 5/6 处漏网收口）。回归测 2 条钉死（新单不随读崩丢失、CLI 列出好行 rc0）。
- **review.py 审核塔对抗探针（#130）**：四面实锤、修复后全绿。①批准信号被留痕顶掉：
  调用顺序「知识写回→队列改写→决策留痕」中 `_decide` 裸 open——留痕路径被占/权限坏时
  OSError 把 CLI 翻成 traceback，而两个业务事实**已达成且不可重放**（resolve 只认 pending），
  审核者以为没批准、重试被拒、知识已入库却无人知晓（#110 agent._record 同纪律的漏网处）；
  现落盘尽力而为只兜 OSError。②pending 判据三套（#92 改一漏一）：列表/`resolve(dict)` 用
  `==PENDING`、stats/reject_kind 用 `get(status,PENDING)`——无 status 的 legacy 行列表看不见、
  stats 却报 pending、批量驳回能驳回「看不见的行」，CLI 列表报「审核队列清空」而 --stats
  报 pending>0 跨入口矛盾，rows() 注释承诺的「历史积压照样可审」对 legacy 行永不兑现；收敛 `_is_pending`
  单一真相（未被显式批准/驳回的都算待审），批准重放防线不松动（显式 approved/rejected 仍拒）。
  ③合法 JSON 非映射行顶读队列：`rows()`/`_read_queue` 只捕 JSONDecodeError，`[1,2]`/`null`
  整行 `.get` AttributeError 整塔崩——#112 注释「两处读侧必须同防线」当时只修字节面，
  且 _read_queue 崩在 engine 失败路径登记器里顶用例异常（#100）；两处同纪律跳过非对象行。
  ④批准坐标键在值 null：`approve_to_overlay` 用 `row.get("evidence", {})`——`evidence: null`
  行（#77 键在值 null 默认不生效）在批准半路 AttributeError，证据卡片面早已用 `or {}` 防线，
  批准路径漏防；现回落 `{}` 走「批准坐标需要目标 BU、键与值」友好归因。
  回归测 5 条钉死（批准存活、legacy 行三入口同真相、非映射行不顶队列、resolve(dict) 判据同源、
  null evidence 不崩塔）。
- **agent.py 决策留痕对抗探针（#129）**：三面实锤、修复后全绿。①中断判据漂移：执行循环
  内联硬编码 `(KeyboardInterrupt, SystemExit)`，而 `failures.CONTROL_FLOW`（#114 声明
  engine/healing/recorder 同源的单一真相）含 `GeneratorExit`——agent 是漏改的第 4 处（#92
  改一漏一），生成器侧关闭被记「执行失败」，与 `classify_failure(GeneratorExit)=interrupted`
  自相矛盾；dsl.py 的 flow 终态判据同款硬编码（第 5 处漏网，`status=failed` 污染缺陷分布）
  一并收敛 `CONTROL_FLOW`。②留痕器先于被定性对象死：reason 的 f-string 手拼 `{exc}`（#127
  只收敛了定性侧 `exception_text`，展示侧是第三份副本），业务异常 `__str__` 自身崩（#126 面A
  真实可达）时在 except 块里当场炸——探针实锤双重损失：**用例真因被顶掉** + **整条留痕随
  赋值一起丢**（连内存 decisions 都没 append），已执行写 Task 的真实副作用从此无痕（#85+#89
  同红线）。修复：`failures.exception_message` 为展示形态单一真相（坏 `__str__` 回落裸类型名，
  定性/展示同源 `_exception_display`）；agent/dsl 各 import 收敛，回归测三面钉死（GeneratorExit
  留痕 interrupted、Nasty 真因传播+executed 可追责、flow status=interrupted）。复核 sound：
  CLI 兜底 print 显示的只可能是框架自身异常（用户输入错误/YAML/未注册 Task，消息体是
  框架字面量）；`str(exc)` 展示点（dsl EnvBlocked 回落、engine 降级原因）消费的消息体由引擎
  在 raise 时构造，不二次消费业务异常对象——非可达缺陷不强改（红线 #12）。
- **contracts.py 契约注册对抗探针（#128）**：一面实锤、修复后全绿——同 `(method, path)` 两条
  不同定义旧实现 dict **last-wins 静默择一**：core 在前 high 在后（人手编辑契约文件复制行忘删）
  时强校验被弱校验遮蔽，「冒充覆盖率却永不生效」正是 `--validate` 自述红线 #7/#12，与 #84
  「多生产者同产出键静默选择」完全同红线（引擎无法判定两条契约谁作准=校验强度取决于条目顺序）。
  修复：`_put` 同键**不等值定义**显式 ValueError 点名两条名字+等级（load 也走此路径，私有
  core.json 有冲突行→`DemoPlugin.contracts` 既有 `except ValueError` 自动回退包内，非静默用弱版）。
  **等值重复必须放行**：`toolbox.learn_contracts` 靠同键覆盖对轮询重复抓包去重（#102 净增计数
  语义），拒绝它=把正常路径翻成错误（#115 教训）——判别按 dataclass `__eq__` 区分「同义/等值登记
  (放行)」与「同键不同校验强度(点名)」。旧测 `test_registry_add_replaces_existing_contract` 钉的
  正是缺陷形态本身（断言静默择强），随修复一并更正并补冲突/等值/文件三条回归测。
- **knowledge.py 知识库对抗探针（#127）**：六面实锤、修复后全绿——私有知识目录是**人可手改**
  的，#73 的「读不残缺」闸门只拦坏字节/坏 JSON，放过**合法 JSON 但形态不对**，全部穿透到
  消费点当场崩，顶掉 engine 失败路径/自愈准入/批准写回/doctor/run --bu：
  ① `load` 顶层手滑成数组/字符串→ `.get` AttributeError——现顶层非映射视同损坏回退下一来源；
  ② 坑点行 `scope: null`（键在值 null 默认不生效 #77）→ `in None` TypeError——坏行逐条跳过
  （#65 reporting 同纪律）；③ 行 `trigger: null` → `.strip()` AttributeError——非字符串 trigger
  不参与匹配；④ `components` 键值坏 + `selectors: null` → 批准半路崩、塔清不动（#73 场景
  同族），`SelectorRegistry.get` 更以裸 TypeError 逃逸「选择器未注册」友好归因——坏条目当
  未注册统一拒绝（#63 存在≠可用延伸），批准坏形态当空重建且 copy-on-write 不丢回退好数据；
  ⑤ `overlays` 键值坏/BU 子映射坏 → `save_overlay`/`load_overlays` 同族修复；⑥ `match_pitfall`
  的 `f"{类型名} {exc}"` 在异常 `__str__` 自身崩时炸（#126 面A 同形态）——#126 只守了 recorder
  一个调用点，engine:168/healing:23 直调路径漏防（**改一漏一必漂移 #92**）；收敛单一真相
  `failures.exception_text`，坏 `__str__` 回落类型名（类型名本就是 trigger 的主要形态，
  `draft_pitfall` 产的 trigger 就是 `type(exc).__name__`）。兼容面全钉：好行照常命中、不翻错
  （#115 教训）。
- **recorder.py 运行记录对抗探针（#126）**：两面实锤、修复后全绿——
  ① **定性器崩顶真因+失败记成成功**：业务插件自定义异常的 `__str__` 自身抛错（引用未初始化
  属性——真实可达雷区）时，`classify_failure` 在 `except` 块里崩：新异常顶掉真因（During
  handling 链），且 fail_class 没赋上 → finally `ok = "fail_class" not in item` 把失败步骤记成
  **ok=True 假绿**（定性器先于被定性对象死 #89 + 坏形态伪装成功 #102，recorder 层同族）。
  修复：定性失败诚实回落 `unknown`，真因原样传播，失败步骤绝不记成功。
  ② **`default=str` 兜底自身崩**：close 用 `json.dumps(default=str)` 假设 str() 全函数可用——
  异常对象 `__str__` 抛错时整轮记录落不了盘（违背 #64「不得让整轮证据静默丢失」初衷），且
  close 在 flow finally 里顶掉用例真因、`_closed` 不置位重试永远崩。修复：永不抛的序列化兜底
  （str 失败落 `<unrepresentable 类型>` 占位），坏对象有噪音但整轮证据照常落盘。面C（磁盘满/
  权限写失败）sound：#64 有意边界「写失败不谎报已关闭、显式可重试」，环境故障应暴露不是吞掉。
- **core.py FunctionalTask 对抗探针（#125）**：一面实锤、两面复核 sound、修复后全绿——
  ① **非可调用 action 构造期假成功**：`FunctionalTask(meta, "pages.read_pt")`（字符串/None）
  旧实现一路放行，直到 `perform()` 才裸 `TypeError: 'str' object is not callable`——不提 Task
  名与 intent，归因漂成引擎缺陷（与 #119 面B step_N、#91 元数据契约同口径：坏形态前移构造期）。
  修复：`__post_init__` callable 检查，TypeError 点名实际类型+值+intent；lambda/bound method
  合法路径零涟漪（DSL 三处 bound-lambda 全绿）。② `atlas.screenplay`/`atlas.__init__` 两级
  门面 `__all__` 与真实符号逐项核对 sound（缺失=[]，无超发）——门面名字漂移会让
  `import *` 静默缺符号，探针当场验证导出完整性。
- **actor.py 执行主体对抗探针（#124）**：三面复核 sound、无代码改动、契约钉死防漂移——
  ① `using` 缺失能力归因点名类型名且保留 cause（既有测已钉，#90 口径）；② `can` 同类重注入
  last-wins：当前代码库无消费方触发（session.actor() 与全部测试每类型恰注入一个），非可达
  缺陷，不强改（红线 #12）；③ `attempts_to` 半途崩**不回滚已完成产出**——前序写 Task 真实
  发生过，抹掉其产出=销毁追责证据（#85/#110 半途副作用可追责在 actor 层的对应面），补回归测
  钉死顺序执行、原异常传播、ctx 部分值保留三契约（多任务形态现无消费方，仿 #107 复核先例
  防将来改成整体回滚静默抹证据）。
- **session.py 会话层对抗探针（#123）**：一面实锤、修复后全绿——
  ① **多清理错误互掩**：`__exit__` 逐个 try 收集 context/browser/_pw 三环节异常，旧末行
  `raise errors[0]` 只报第一个——浏览器挂起被杀后三环节常连环崩，`_pw.stop` 失败意味着
  Playwright node 驱动进程正在泄漏，运维却只看到第一条 context 错误，其余静默（#85 清理链
  无痕同红线；与 #121 面D runtime.__exit__ 完全同构）。修复：单错原样抛（#82 既有口径不翻错）、
  多错聚合 ExceptionGroup 全点名；有在用例异常仍让位真因（#82 分工不变）。其余面复核 sound：
  snapshot/observe 取证降级（#66）、`__enter__` 半启动不留孤儿（#111）、`ApiEvidence.classify`
  定性器不裸抛（#100）均已有回归测钉死。
- **abilities.py 能力层对抗探针（#122）**：两面实锤、修复后全绿——
  ① **类存在但不符合客户端协议裸崩无归因**：真实项目模块常同时有 `IcsApi` 与 `IcsService`，
  `ATLAS_DEMO_API_CLASS` 手滑填成后者时，旧实现 `api._load_spec(...)` 才裸
  `AttributeError: 'IcsService' object has no attribute '_load_spec'`——不提 env 键、冒充框架
  缺陷（#103 类名不存在同口径延伸：类存在≠可用，#63 注册≠可用同族）。修复：装配前
  `callable(_load_spec)` 检查，ImportError 点名模块路径 + 类名 + ATLAS_DEMO_API_CLASS 线索。
  ② **无 Content-Type 的 HTML 登录页伪装成功**：旧 auth_expired 判据只看
  `Content-Type: text/html`，异常网关/mock 回 200+整页 HTML 不带该头时判 ok=True——登录页
  伪装成业务响应，下游拿 HTML 当数据烂到远处才炸（#102 静默形态同族假绿）。修复：JSON 解析
  失败落 raw 且正文以 `<!doctype html`/`<html` 开头也判会话失效；Content-Type 原判据保留，
  合法 JSON 零误伤（#115 教训：不把正常路径翻错——首版正文判据无条件读 .text 让 5 个只带
  json() 的 fake 崩出 AttributeError，收敛进 raw 分支后既拦坏形态又零涟漪）。
- **runtime.py 装配层对抗探针（#121）**：三面实锤、一面复核 sound、修复后全绿——
  ① **YAML 布尔静默改写步骤号**：`step: yes/on/true` 在 YAML 1.1 解析成 bool True，
  `LegacyStepTask` 旧 `int(step)` 把它静默变成 **step_1**——执行了与「是」毫不相干的步骤，
  坏形态伪装合法结果（#116 str(null)→'None' 同族）；`step: 3.7` 静默截断成 step_3。
  修复：`_validate_step` 构造期点名 bool/小数/非整字符串/step_0；兼容面（#115 教训）：
  `step: '3'` 引号字符串与 `3.0` 整值浮点是合法形态放行。
  ② **DemoApi 白名单硬编码在引擎**：`runtime.__enter__` 装配行写死 `customer_detail` 等
  业务操作名——换业务必须改引擎，违反 AGENTS 红线 11。修复：收敛 `DemoPlugin.allowed_api_operations`
  单一真相，回归测钉死引擎源码不再含操作名字面量（防改回漂移）；消费者语义不变。
  ③ **退出双清理错误互掩**：回收落盘失败 + 会话关闭失败同时发生时，旧实现 session.__exit__
  抛错直接外传，`recycle_error` 永远够不着 raise——运维只看到「浏览器关不上」，不知道回收清单
  也没写进去（#85 拦截无痕同红线）。修复：逐个 try 收集，单错误原样抛（#86 两测口径不变），
  双错误聚合 ExceptionGroup 全点名；有在用例异常时仍让位真因（#82）。
  ④ 复核面 sound 如实入档：`spec_file` 缺失不预检是 #103 已记录的**有意边界**——真实
  IcsApi._load_spec 对缺失路径返回 `{"endpoints": []}` 存活级降级，Atlas 不越界替外部类加检查。
- **toolbox.py 感知器官深查对抗探针（#120）**：两面实锤、修复后全绿——
  ① **真实桥接 console_read 键名漂移**：真实 `console.read` 返回 `{total, filtered, entries}`
  （bridge-client.js v1.1.4，与 net_list 同族键名），离线 fake 用 `items`——#105 修复时照 fake
  只认 items，真实错误行被点名「无 items 列表」当场拒绝：**把正常路径翻成错误**（#115 教训反向面；
  #102 键名漂移同族）。现 entries 优先、items 兼容，键名口径与 learn_contracts 收敛单一纪律。
  ② **net_list 分页截断静默登记**：真实桥接默认 `limit=50`、`total` 报全量（231 条实测）——旧实现
  拿一页登记一页零告警：部分结果伪装全量完成（#102 静默 0 条同族假绿），用户以为「契约都学了」，
  远处接口缺契约静默不校验。现 `total > len(entries)` 点名「分页截断」终止，不带 total 的旧形态
  零漂移。#102 的既有测 fixture（total:231/entries:5）正是截断形态，改完整页保原意（键名识别），
  截断面单立新测。
  ③ 透传面复核 sound：`replay/export_jmx/export_python/snapshot/get_request` 无 src 消费方透传
  （与 #105 注记同口径）；`learn_contracts` 对 slim 投影（method/path/status 均在）零误伤。
- **legacy.py 旧步骤加载对抗探针（#119）**：两面实锤、修复后全绿——
  ① **探测失败裸崩无归因**：步骤模块 `from lib.pages import …` 但放错目录（祖先无
  `lib/`+`conftest.py`），旧实现裸抛 `ModuleNotFoundError: No module named 'lib'`——用户分不清
  「探测没命中」还是「真缺 lib」、该把模块放哪（真实缺陷①「No module named lib」的残留面：
  修了「放进 path」没修「探测失败归因」，#90 点名配置键同口径）。修复：撞 ModuleNotFoundError
  且缺的是 `lib` 顶层模块、且探测零命中时，补归因重抛 ImportError（点名 `ATLAS_LEGACY_WEB`
  与探测起点）；探测命中却仍缺 lib=旧项目真缺、坏的是别的模块——原样传播不揽责。**兼容面**
  （#115 教训：归因修复不得把正常路径翻成错误）：不 import lib 的纯本地步骤模块在任意目录仍合法，
  不在加载前拒绝。
  ② **非可调用 step_N 构造期假成功**：模块里 `step_3 = "字符串"` 时旧实现照样包装成 LegacyStep，
  perform 才 `TypeError: 'str' object is not callable`——构造期假成功把错误推迟到真实执行链
  （#94 坏 creator 在建单前当场点名同口径）。修复：`callable()` 检查前移到 `from_module`，
  当场 TypeError 点名实际类型。
- **overlay.py BU 坐标双轨对抗探针（#118）**：面1 实锤修复、其余面复核 sound——
  ① **坐标修正后塔内证据过期**：bu_overlay 草稿 id 按「键+两侧 BU」定（同键同 BU 对
  视为同条待审项、seen 聚合），坐标文件修正后（如 BU-B 的 ou B→C）id 不变而证据变——
  旧实现 `append_draft` 同 id 命中只增 seen、**不更新 evidence**：审核塔停在过期值 B，
  人审按 `--remedy "<目标BU真实值>"` 批准时基于旧证据落成**错误坐标**（坏形态伪装
  有效结果，#102/#105 同族；探针实锤两轮 to_tower 后塔内仍显示 B）。修复：同 id 再登记
  刷新最新证据（带 evidence 才刷，缺省不得抹掉已有证据——engine 失败路径的 pitfall
  登记不带 evidence 键，行为零漂移；pitfall 同 id 证据同值，等值覆盖无害）。
  ② 复核面 sound 如实入档：值相同的键绝不进 diff；单侧缺失/值为 null 同为「无坐标」
  语义，审核者看 base/other 任一为 None 即可判断；不存在的 BU 点名现有清单。顶层混合
  键 sorted 崩溃经核实**不可达**（JSON 键恒字符串）。
- **creators.py 建单脚本接入对抗探针（#117）**：三面实锤、修复后全绿——
  ① **无单号建单静默成功、回收链断**：生产主路径 `find_or_create` 走 `DataProvider.supply`，
  它只查「非人工创建」备注**不查单号**——脚本真实建了单却没回单号时静默返回成功，回收清单落
  `{"remark": …}` 无单号记录，`atlas recycle` 显示 orderNo '?'，真实建出的单永远找不回
  （与 #86 落盘断链、#102 静默成功同族）。而 `require_audit_tag`（examples 直调路径）查单号却用
  `key in result`——`{"orderNo": ""}`/`{"orderNo": None}` 也算「有单号」放行（键在即算）。两份判据
  各写各的重演「改一漏一必漂移」（#92/#97）。修复：抽 `find_document_number` 单一真相（非空标量
  才算单号，bool→"True" 假单号与容器形态拒绝，数字单号接受），supply 补单号闸门（建单已发生
  记录保留、报错给原值供人工追回，与备注缺失同策略），`require_audit_tag` 改同源。
  ② **where 含不可序列化值裸崩无归因**：YAML 日期对象进 where → 旧实现 `json.dumps` 裸
  TypeError「Object of type date is not JSON serializable」，不提脚本路径、不提哪个键。修复：
  点名脚本 + 整条键路径（嵌套 dict/list 下钻，`where.createdAfter`/`dates.0`）+ 类型 + 转换建议
  （#61/#103 可归因红线）。
  ③ 既有 `test_create_quota_exceeded` 的 fixture 正是「无单号建单」形态被旧测试固化——补合规
  单号后测其本意（配额闸门）不受影响；真实 demo creator 自有非空单号闸门，零涟漪。
- **state_machine.py 按需状态机对抗探针（#116）**：三面实锤、修复后全绿——
  ① **`terminal:` 忘填值整条命令崩**：YAML 写了 `terminal:` 但值为 null（键在、值 null），
  旧实现 `raw.get("terminal", [])` 返回 **None**（默认值只在键缺失时生效，#77 同红线），
  `set(None)` 裸 `TypeError`——而 `atlas machine` 只捕 `(OSError, ValueError, yaml.YAMLError)`
  （cli.py:285），TypeError 漏网 → 整条命令 traceback 裸崩（违反 #79/#80 rc2 口径）。
  修复：null 视同未填回落自动推导终态；非列表标量点名「terminal 必须是列表」。CLI e2e 测钉死 rc0。
  ② **`task:` 空值被兜成字面量 `'None'` 假绿**：旧实现 `str(item["to"])` 不验形态——迁移的
  to/task/guard 写成 null、空串、dict/list 全被 `str()` 兜成字符串：`task:` 忘填 → `'None'` 成为
  「合法任务名」，`atlas machine --target` 输出 `["None"]` 假绿（坏形态伪装合法结果，#113 同红线）。
  修复：to/task 必须是非空标量（null/空串/复合形态构造期点名状态名+字段+实际形态）；guard 显式
  null 回落「无守卫」（与缺省同义，不再是假守卫 `'None'`），复合形态点名；guard=缺省/字符串语义零漂移。
  ③ **守卫注册了但值不可调用裸崩**：`guards={"ok": "字符串"}` 旧实现 `guards[...](actor)` 裸
  TypeError「'str' object is not callable」——不带守卫名与类型归因。修复：`callable()` 显式检查，
  RuntimeError 点名守卫名与实际类型（#63 声明未注册显式同族延伸：注册≠可用）。
- **tasks.py assert_api 对抗探针（#115）**：两面实锤、修复后全绿——
  ① **save_as 假旋钮**：`AssertApi.__init__` 把 `save_as` 塞进 `produces=(save_as,)` 却从不存成
  实例属性，`perform` 硬编码返回 `{"api_assertion": True}`。DSL step 透传 `save_as`
  （dsl.py:75 排除集不含它），`save_as="my_key"` 到 write_produces 才 KeyError 点破「声明键与实写不符」——
  半成品旋钮与同文件 CallEndpoint（save_as 真兑现）漂移，违反 #92 produces 自兑现契约（探针实锤：
  `produces=('my_key',)` 而 perform 返回 `['api_assertion']`）。修复：`__init__` 补存 `self.save_as`、
  perform 按声明键返回（默认值 `api_assertion` 行为零回归）。
  ② **_dig 路径打错归因漂移**：expect 路径写错的四种形态——dict 缺键 `KeyError`、list 越界 `IndexError`、
  非数字索引 `int()` ValueError、标量中间节点带路径 AssertionError——前三种**裸抛 Python 原生异常**，
  不带路径上下文，classify 分别落 `unknown / unknown / contract`：同为「用户把断言路径打错」却落进
  完全不同的缺陷类（atlas summary 缺陷分布无法归因，且打错路径被误定性成契约违规，违反 #58「查无归 data」、
  #61/#113 路径点名同红线）。修复：四种形态全部收敛成带整条 `path` 与失败段（字段名/索引/类型）的
  `AssertionError`，文本含「不存在」稳定归 `data`；合法路径取值行为零回归。修复自身教训：首版用
  `part.isdigit()` 判索引，把合法负索引 `data.-1`（旧 `value[int(part)]` 支持，取末行）误拒成「不是索引」——
  归因修复绝不能把正常路径翻成错误，改 try/int + try/下标兼容负索引并越界点名（越界/负越界仍归 data）。
  三测钉死：save_as 真兑现（含默认值零回归）、四类打错路径归因统一 data、合法路径含负索引不回归。
- **engine.py 执行策略对抗探针（#114）**：三面实锤、修复后全绿——
  ① **写拦截审计无痕**：`execute` 的写护栏 `validate_write` 曾开在 `with recorder.step` **外**，生产环境
  拦截写步骤时 `PermissionError` 在 step 进入前抛出——`runs.jsonl` 零痕迹、`results` 空，审计者看不出
  「谁在生产试图跑写步骤被拦」也是审计链断点（与 #85 中断留痕同红线）。挪进 step 上下文第一行，classify
  把「生产环境禁止」归 env_blocked（策略阻挡非代码缺陷，红线 #7），PermissionError 仍原样传播。
  ② **中断污染缺陷分布**：控制流异常（KeyboardInterrupt/SystemExit/GeneratorExit）曾双重污染——recorder
  把 KI 的 step 记成 `fail_class="unknown"` 进 `atlas summary` 缺陷分布（无法归因的「未知失败」），engine
  又把 Ctrl-C 登记进待审坑点草稿塔（remedy 无从修复中断，纯噪音）。`failures.py` 新增 `CONTROL_FLOW` 单一
  真相 + `FailureClass.INTERRUPTED`，`classify_failure` 先于关键词短路；engine 失败路径短路：如实记
  interrupted 进 `results`，不走 heal/evidence.classify/坑点匹配/journal 草稿/session 取证后原样重抛；
  healing 内联三元组收敛同源（#108/#92/#97 同红线）。③ **CDP 坏配置静默失效**：`CDP_PAUSE_STEP=abc`/
  `TC9:`/`1.5` 旧实现 `int()` 抛 ValueError 被 except 静默吞掉直接放行——调试者以为会冻场等 attach，步骤
  实际跑过，案发现场错过（#102/#105 假阴性同族：配置错误本身就该点名）。现点名坏值警告一行后照常放行
  （非交互零阻塞红线不变：绝不因此挂起）。classify 面回归测钉死护栏短语精确匹配，`ApprovalRequired`
  （同为 PermissionError）不被误吞成 env_blocked。
- **dsl.py assert/数据供给对抗探针（#113）**：四面实锤、修复后全绿——
  ① **断言假绿**：`_resolve_operand` 对任何解析失败的操作数无差别回落成字符串字面量，探针实锤
  `source_order.ptx != source_order.ptx2`（两个打错的路径）被当成 `'…' != '…'` 恒真通过——写错的
  断言比没有断言更危险（声称验证过而什么都没验）。路径形态（`word.word`）解析失败改为点名
  AssertionError；无点单词（`NET30`）、数字（`3.14`）、带引号字面量（`'A.B'`，剥离引号）兼容面逐一测保留。
  ② **bool/None 假阴性**：`ok == true` 里 `true` 被当字符串 `'true'`，ctx 真为 `True` 反而断言失败，
  正确断言被拒把人逼向删断言——`true/false/none/null` 识别为 bool/None 字面量；显式 `'true'` 仍是字符串
  （True != 'true'），语义不混淆。③ **requires 静默覆盖**：两条供给 `as` 同指一键旧实现 last-wins，
  SalesOrder 数据被 PurchaseOrder 整条遮蔽、下游读 PO9 当 SO1 继续跑（#84 多生产者同红线）——
  `_check_supply_targets` 执行前点名两条条目。④ 落点冲突是结构错误，`validate_flow`（atlas compile /
  run 登录前）同口径拦下，兑现 #97「compile 绿=run 不再登录一半炸」。校验收敛单一来源，不留改一漏一。
- **经验队列坏字节对抗探针（#112）**：中断把 `experience_drafts.jsonl` 尾行腰斩在字节上后——
  ① `ExperienceJournal._read_queue` 与 `ReviewQueue.rows` 裸 `read_text` 抛 `UnicodeDecodeError`：
  审核塔整队不可见（一条坏字节拖死所有好草稿），且 engine 失败路径的 `journal.append_draft` 是裸调
  （engine.py:153-155），登记器崩在 `except` 块里把**原用例异常**顶掉、results/取证链全断（#100 定性器
  红线、#109 trace 本体同族）。两处读侧收敛同款 `errors="replace"` 回落——残行 JSON 解析必败自然落进
  既有「跳过残行」分支。② 读侧修好暴露**写侧**新缺陷：`append_draft` 裸 append 黏在无换行尾的残行
  之后，整条合法新草稿与前一行合并成一个「残行」，被读侧静默跳掉——登记成功、返回路径、无异常，
  草稿却永久不可见（#102 静默丢审核素材同族假成功，探针实锤）。追加前检测尾字节非 `\n\r` 先补断行，
  保留 O(1) 追加与并发写语义（不改成全量重写——那会把 seen 合并的读-改-写竞态窗口扩大到整队）。
  三面探针 + engine 原异常传播回归测钉死。
- **session.py 生命周期对抗探针（#111）**：`BrowserSession.__enter__` 在 `start()` 成功后
  `launch`/`new_context`/`new_page` 任一失败（executable 指错、浏览器初始化中崩、进程组权限）——
  `with` 语义下入口异常不触发 `__exit__`，Playwright node 驱动与半开 Chromium 进程双双泄漏，
  每次失败累积孤儿进程（探针实锤）。`DemoRuntime`（runtime.py:70）在 `self.session` 赋值前就抛，
  #84/#97 的外层清理链同样够不着——半启动失败面漏在 BrowserSession 本体。修复：入口 `try/except`
  逐个尽力关闭（context→browser→stop 驱动，各自吞清理错误），原始真因原样抛出。探针同时
  核查 `CallApi(base_url=None)` 面：`config.base_url` 恒返字符串（env 缺配即 `""`），无 `None`
  消费路径——如实判 sound 不动，不臆造修复。#100 审计口径：`ApiEvidence.classify` handler 链
  （None/非映射/抛错逐条跳过、只捕 Exception）与 `capture` 尽力而为降级此前已有实锤测钉死。
- **agent 留痕落盘边界（#110）**：探针实锤 `_record` 三个调用点防线不对称——#85 只护了执行失败路径
  （caller 包 try），**成功路径**（任务全部真实执行、写副作用已发生后）与**审批门 raise 前**裸调
  `mkdir/open`：journal 目录位被占成文件/权限坏时，成功返回被 `FileExistsError` 顶掉——caller 拿不到
  Decision 与 executed 清单，「全部成功」翻成基础设施异常；`ApprovalRequired` 的 rc3 决策信号同被顶成
  裸 traceback，「拒绝执行」翻成「框架坏了」。修复：`_record` 落盘收敛为 OSError 尽力而为（内存
  decisions 先行 append 不丢），只兜盘/路径类故障，非 OSError 是真 bug 不吞；成功路径补
  `executed=list(completed)`——旧留空让「跑完全部」与「一个没跑」的 journal 行同形，读痕人无法
  确认哪些写 Task 真实发生过（#85 追责口径对成功路径同样成立）。
- **review/explore 边界探针（#109）**：四路实锤、全部探针复现后修复——
  ① `ReviewQueue.apply` 决策文件里「合法 JSON 但非映射」的行（人编辑改成数组/null）裸 `AttributeError`
  顶掉整批：前面已批准的写回不回滚、后面合法行全不跑、CLI 拿不到 failed 清单（docstring 承诺
  「单行失败不拦其余」名存实亡）——库层点名拒绝 + CLI 读循环同口径，rc2 兑现。
  ② `approve_to_pitfall` 对探索草稿（`exception_type=""`）用 `row.get(key, default)` 兜底——只在键缺失时
  生效，空串原样透出 → 批准的坑点 trigger 为空，被 #59 完整性防线永久拒绝匹配：批准「成功」、留痕齐全，
  知识却永不生效（死资产假成功，与 #102 静默 0 条同族）。空串与缺失一律回落非空默认。
  ③ `compile_trace` 的 `read_text` 裸 `UnicodeDecodeError` 顶掉整份编译——Ctrl-C 把 trace 末行多字节字符
  腰斩在字节上即触发，#68 的逐行容错根本没轮到（崩在读文件本身）；坏字节回落 replace 读，残尾行自然
  落进 malformed_lines 计数。④ checkpoint 快照同样可被腰斩：修复前顶编译，修复后截断前的完整候选仍是
  有效原矿，替换字符不冒充候选。
- **healing 自愈吞中断修复（#108）**：探针实锤可达路径——`draft_pitfall` 的 trigger 恰是异常类名，
  人审批准（`review --template/--apply`）一条含 `KeyboardInterrupt` 的草稿后，approved + remedy +
  `trigger in text` 命中 → 旧实现 `can_auto_heal` 放行 Ctrl-C 就地重试，**操作者中断信号被自愈吞掉继续执行**，
  违反 #70 的 `interrupted` 终态口径与 `dsl.py` 既有语义。engine 的 `except BaseException` 本就捕 KI，
  此前只靠 trigger 恰不命中侥幸挡住，无任何防线。修复：控制流异常（`KeyboardInterrupt`/`SystemExit`/`GeneratorExit`）
  一律拒绝自愈——它们是终止信号，不是业务失败，remedy 知识无从「修复」它们。
- **composition 边界审计（#107）**：十三组对抗探针（自环/部分自环/goal 在环内/provided 打断环/菱形共享
  DAG/同名重复注册/等值不同实例/goal 已就绪 no-op）全符合预期语义——模块 sound，无掩蔽型缺陷。
  钉死三条此前无测的关键 sound 边界防漂移：单 Task 自环必须显式报「循环」（防环检测被放宽成
  「已访问就跳过」而返回残缺计划）、菱形共享上游只排一次（防身份去重被改成值去重导致双副作用漂移）、
  goal 已在 provided 时返回空计划（agent 消费方据此 no-op，防「目标已就绪」被翻成失败）。
- **engine auto 降级链审计（#106）**：核实 `_perform` 的 `except (NotImplementedError, RuntimeError)`——契约违规（`ContractViolation` 属 `AssertionError` 系）与网络故障（`ConnectionError` 属 `OSError` 系）**不在**捕获集，真实 API 故障不被 auto 静默降级到 UI（回归测钉死：放宽到 `Exception` 会抓到「API 坏却被 UI 翻绿」的掩蔽）。确证可达缺陷：降级原因此前只写 ctx 内部键 `_auto_degraded_to_ui`（全仓零消费方），步骤在 `runs.jsonl` 里 `ok=True` 零痕迹——「API 坏→退 UI→通过」与「UI 本来就该走」事后无从区分（#102 静默 0 条/#105 假阴性同族：事件伪装成不存在）。修复后经游标把降级条目并入 step 记录落盘（heal 重试与失败路径同口径），不改路由语义。
- **waits.py 真实浏览器冒烟（2026-11-04，本机 Chrome via `executable_path`）**：`wait_for_rows`（异步落定读 vm.data=3）、
  `wait_for_vue_field`（NET30）、`wait_for_url_change`（捕获 hash 导航）、`wait_for_rows` 合法空表 `min_rows=0` 返回 0——
  四场景全过。当场抓到离线 fake page 掩盖不了的真实缺陷：空 `.el-table` 高度为 0 被 Playwright 判 hidden，
  默认等 `visible` 会让「合法空表」第一步就超时（改 `attached`，行数据交给 vm 轮询）。
- **真实 SIT 对接验证（2026-11-03，`ATLAS_*` 映射层 + `ATLAS_SESSION_*` 覆盖，接真实 `UI AUTO TEST`/`iserver`）**：
  - `atlas legacy --keyword test_tc1_` → 退出码 0：兼容层把 `atlas legacy` 完整接到真实 `iservice` 目录、
    `test_sit_bu_a_regression` 节点，pytest 真实登录并执行到 TC1（结果 skipped：备件无价格主数据，环境限制，非框架缺陷）。
  - `atlas run examples/atlas_core_verify.yaml` → **登录 / OU 切换（真实环境 OU）/ 语言 / 导航 /
    `select_customer` 全部接通真实系统**；最终失败于虚构示例客户号 `CUST0000001` 查无结果——示例数据在真实系统不存在，
    属数据事实，**不伪装 PASSED**。修复点：session 默认值原为纯虚构，真实 `switch_to_ou` 必然超时，
    新增 `ATLAS_SESSION_*` 整体覆盖后接通。
- **CDP 冻场真实冒烟（2026-11-04，真实 SIT，端口 9333 避让本机 Chrome）**：
  `CDP_DEBUG=1 CDP_PAUSE_STEP=2 atlas run examples/atlas_core_verify.yaml --mode debug` 在第 2 步前冻住，
  CDP `/json` 实测可见真实页面 target（"统一认证平台"），喂 Enter 后流程继续跑到已知失败点——冻场/放行语义真机成立。
  冒烟当场抓到并修复一个真实缺陷：`atlas run <flow> --mode debug`（文档口径）被 argparse 拒收
  （`--mode` 此前只认子命令前位置）；全局参数现在子命令前后均合法（回归测试钉死）。
  剩余薄口：`playwright-cli attach` 的交互式接管 UX 仍需真人 tty 会话确认，端口与页面可见性已实证。
- **explore 真实会话冒烟（2026-11-04，真实 SIT，端口 9444）**：脚本化 console（回车×2→stop）走完
  真实登录（ticket 落地）→ 2 个 checkpoint（103 行真实 aria 快照落 `artifacts/exploration/`）→
  trace 编译进塔：7 条 pending（1 pitfall / 1 trace_step[同 URL 合并 seen=2] / 5 selector_candidate[跨 checkpoint 去重]）。
  冒烟抓到并修复：`✓` 标记在 GBK 控制台崩掉 `start()`（ASCII 化）；外部大脑直调 `start()` 补 UTF-8 兜底
  （`ensure_utf8_streams` 共享）。交互式接管由外部大脑在真人 tty 完成，本冒烟验证 Atlas 侧留痕/沉淀管线。
- **塔真实带写入全流程（2026-11-04，私有知识目录 `D:/tmp/atlas_kb_real`）**：
  `file://` 探针页进真实 explore 管道 → 2 条 selector 候选（值可验证：`button#probe-save` 等真实 fixture DOM）
  → 驱动脚本批准 1 条 + **CLI 批准 1 条**（写落私有 components.json，包内文件 `probe_page` 零出现）→
  trace_step 驳回留原因 → `atlas selectors probe_page` 读回私有+回退合并视图 → `review_decisions.jsonl`
  含 `approve_selector`/`reject` 双动作。业务真实草稿仍留人工处置（当前塔状态见下条 `--bu` 取证记录）。
- **BU overlay 离线管线（2026-11-04，样本数据）**：diff/单侧缺失/塔草稿/批准落私有 `bu_overlays.json`/CLI 共 7 条离线测试通过。
- **`--bu` 执行链接入（2026-11-04，离线 5 测 → 真实 SIT 端到端）**：
  `atlas run --bu` 把已批准坐标写入会话链（非会话键与空值不接、显式 env 优先）。
  真实取证：BU-A 三坐标经 `--template` 导出→人填→`--apply` 批准进私有 `bu_overlays.json`
  （applied=3、failed=0）；`atlas run examples/atlas_core_verify.yaml --bu BU-A`（环境**无** `ATLAS_SESSION_*`）
  回显 `BU=BU-A 坐标生效 {account/division/ou}，未接执行路径: []`，`runs.jsonl` 记录
  `open_doc_list`/`click_new` 真实通过、`select_customer` 败于虚构客户号「查询无结果」断言——
  与 `ATLAS_SESSION_*` 显式 env 跑同签名，属数据事实不伪绿；该真实失败如设计生成 1 条新断言草稿进塔 pending 人审
  （写路径仍只有人审）。过程抓到 SIT 偶发导航迟缓（一次挂起 >8 分钟杀掉、一次 60s 导航超时，第三次跑通），
  属环境现象非 `--bu` 链路缺陷。
  真实塔按既有政策逐条处置（2026-11-04，均 `review --reject --reason` 走 CLI 留痕，无一伪造批准）：
  噪声/数据事实/trace 素材共 8 行 reject；本轮又清掉最后 4 行 pending——`button:张2407`（名含登录用户名+图标字体空白，非稳定选择器）、
  `textbox:Select`（Element 通用 combobox，组件库已用 `.el-select input` CSS 覆盖，按 aria 名入库=重复且身份模糊）、
  `trace_step`（按设计拒之塔外，需人工写成 flow YAML 再 compile）、`select_customer` 断言（虚构客户号查无，2026-11-03 归档数据事实）。
  塔现 **pending 0 / approved 5 / rejected 9**，全部为读证据后的可审计人工判断；真实业务价值行已全部落定，
  新草稿随后续 run 继续进塔。
  样本 BU 名带 `(sample)` 标注——**管道成立≠真实 BU-B 已双跑**，真实 BU-B 坐标仍需会话值。
- 真实 SIT 兼容回归（`atlas legacy --keyword ...` 分批，2026-11-01）：
  **17 条全覆盖，13 passed / 4 skipped / 0 failed**。

  | 批次 | 结果 |
  |---|---|
  | TC1 | 1 passed |
  | TC2~TC6 | 4 passed / 1 skipped（TC5 税率） |
  | TC7~TC9 | 3 passed |
  | TC10~TC13 | 2 passed / 2 skipped（TC11 税率、TC13 PT 锁死） |
  | TC14~TC17 | 3 passed / 1 skipped（TC15 税率） |

- **Atlas 自身执行路径（不经旧 pytest，真实 SIT）**：
  - `examples/atlas_core_verify.yaml`：5 个只读 Task 全部 `ok:true`，两轮 `passed`。
  - `examples/atlas_save_verify.yaml`：9 个 Task（含 `click_save` / `wait_save_result`）全部 `ok:true`。
- **真实 API 造数 → UI 验证（`examples/api_create_then_ui_verify.py`）**：
  ```
  {"step": "api_create", "orderNo": "SO-DEMO-0001", "remark": "API造数UI验证非人工创建", "paymentTerms": "NET30"}
  {"step": "ui_verify",  "ok": true, "orderNo": "SO-DEMO-0001", "pt": "NET30", "customer": "CUST0000001"}
  ```
  建单报文由**真实 UI 保存请求捕获**（`scripts/capture_save_request.py` → `scripts/templates/sale_order_save_template.json`），
  不是猜字段；`atlas/plugins/demo/creators.py` 为进程内实现，复用已登录会话。
- **UI 操作 + API 断言（`examples/ui_then_api_assert.py`）**：UI 建 `SO-DEMO-0002`，API 查得 `paymentTerms=NET30`。
- **Agent 真实执行**：`--goal doc_list_ready --execute` → `reason:auto`；
  `--goal save_result --execute` 无批准被拒；`--approve` 后跑完 7 个 Task，`reason:human_approved`。
- **跨平台（Windows / Linux 均已实跑）**：
  - Windows：上述全部证据。
  - Linux（Debian 12 / x86_64 / Python 3.11.2 / 系统 Chromium 156），**原生 venv 方式实跑**：
    ```
    ### PYTEST     110 passed
    ### COMPILE    COMPILE_OK
    ### BROWSER    Playwright local page: PASSED
    LINUX_ACCEPTANCE_PASSED
    ```
    归档：`reports/linux_acceptance.json`。执行脚本 `scripts/linux_acceptance_remote.py`。
  - 关键实现：`ATLAS_BROWSER_EXECUTABLE` 支持系统浏览器，绕开 Playwright 浏览器 CDN 不可达。
  - **未验证**：Docker 路径未跑通。该服务器访问 Docker Hub 不通，回退本地基础镜像后镜像构建超时，
    因此 `Dockerfile` / `docker-compose.yml` / CI 属于**已提供但未实测**，请勿当作已验证。
  - 本轮 Linux 实跑还抓到 3 个「仅 Windows 能过」的测试缺陷（硬编码 `../legacy-api` 路径等），已修复。
- **契约分级（已界定范围并实现三档）**：`core` 完整字段类型校验、`high` 通用规则、`minimal` 存活校验。
  当前 Demo 覆盖 `core 1 / high 5 / minimal 1`，`atlas contracts` 可查。
  **不逐条为 数百条接口写强 schema**：维护成本高于收益，其余接口落 `minimal`。
- **JMeter `.jmx` 转 Atlas DSL（`scripts/import_jmeter.py`，2026-11-04）**：`root.iter()` 递归抓 sampler（含
  TransactionController 嵌套）；提取 `HeaderManager` 全局鉴权头并入每个 `api_call` step——
  **缺 `isc-app` 业务识别头 ICS 后端即回 BUG-920**，迁移不丢头才能真巡检。引擎侧 `CallEndpoint`/
  `CallApi.request` 已贯通 per-request `headers`（None 沿用会话默认，传入按 requests 语义合并）。
  **发送端 body 分流**：`dict/list` 走 `json=`、`str/bytes` 走 `data=` 原样——非 raw 多参数迁移产物
  是 form 串，一律 `json=` 会被包成带引号的 JSON 字符串字面量发出得假 400（#98 修好提取，发送语义
  同样要对）；其余类型在发送前点名拒绝，不让序列化器裸崩冒充框架缺陷。
  `${VAR}`/`${__P()}` 占位符不改语义、原样保留，但递归下钻 body/path/header 逐条 stderr 点名，供人审接 env
  （当字面量发只会得假 400，误判成框架缺陷）。**报文完整迁移**：真实工程「Body of a sampler」
  （`postBodyRaw=true`）按 elementProp 分组取正文——旧实现把 bool 当 body 当场崩溃、`[:500]`
  又静默腰斩大报文，巡检发残缺报文得假 400；多参数表单逐参数拼接不互相覆盖、外层 Arguments
  容器节点不重复计；精简扁平 `Argument.value` 形态兜底原样取。离线 17 测覆盖嵌套头提取/头贯通/
  占位符下钻/DSL 编译接住/大报文完整/raw+表单+扁平三形态/**JMX→DSL→引擎端到端真发出 `isc-app` 头**
  （迁移产物确认可跑，不只是字段自洽）。
  **只生成 YAML 供人审，不自动执行**——生产写与鉴权值必须人过一遍。

详见 [AGENTS.md](AGENTS.md)、[CLAUDE.md](CLAUDE.md)、[PRIVACY.md](PRIVACY.md)。

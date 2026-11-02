# Atlas

<p align="center">
  <a href="https://github.com/ojbkxc/Atlas"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="#当前验证边界"><img src="https://img.shields.io/badge/tests-279%20passed-brightgreen" alt="279 passed"></a>
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
- **AI 可拼装 Task**：每个 Task 强制声明 `domain`、`intent`、`requires`、`produces`、`channel`；按目标递归选依赖并拓扑排序。
- **双轨资产**：自己的 YAML DSL；`LegacyAdapter` 直接运行同事 `legacy-suite` 原项目，不复制、不改写其 17 条用例。
- **三种模式**：`run` 全速（不在每步截图）、`debug` 慢速/快照、`heal` 失败现场。
- **探测式供给**：`find_only` / `find_or_create`，优先复用数据；生产禁写、自动建单必须带「非人工创建」、create 配额。
- **失败实证与分类**：`env_blocked/session/interaction/contract/data/assertion`；支持精确 API 重放辅助判别。
- **知识与自愈**：30 条首批 Demo 结构化坑点；知识命中、修复提案、知识写回与自愈均要求人审，绝不放宽断言。
- **契约分级**：核心字段强校验、高频通用规则弱校验、最小响应校验；不按 数百条接口盲目生成强 schema。
- **外部资产适配**：原 `DemoApi` 接口语义、`toolbox` 感知/重放、runlog 风格 JSONL 记录。
- **真实运行装配**：`DemoRuntime` 一次登录、复用旧项目 `.env`、Cookie 共享、白名单 DemoApi、契约与探针统一注入。
- **复杂业务与进化**：按需状态机、经验草稿、人审提升知识、API 实证归类、runlog 格式导出。
- **Agent 决策层**：`AtlasAgent` 按元数据规划 → 风险分级（read_only/low/medium/high）→ 超阈值必须人工批准 → 决策留痕 `reports/agent_decisions.jsonl`。生产写操作恒为 HIGH。
- **create 可插拔**：`ScriptedCreator` / `make_scripted_creator` 挂接**已验证**的建单脚本；配额在调用前占用，结果必须带「非人工创建」与单号。
- **跨平台**：纯 Python、Playwright、`pathlib`；Windows/Linux 使用同一套代码。
- **DSL assert 块**：`assert:` 支持表达式断言（`pt_echo == source_order.pt`、`count >= 5`、`document_no != ""`，六种比较运算符）+ 内联 Task 断言（`task: assert_api`）+ 键值断言；compile 时提前验证 task 引用。
- **channel=auto API 路由**：`auto` Task 有 `supports_api=True` 时引擎优先调 Task 自己的 `_perform_api`，失败降级 UI（不静默吞掉）。
- **语义等待**：`waits.py` 提供 `wait_for_rows`/`wait_for_mask_gone`/`wait_for_vue_field`/`wait_for_url_change`，替代硬 sleep。
- **会话保活**：`CallApi.set_keepalive(callback)` 检测 `auth_expired` 自动重新烘焙 Cookie，支持间隔保活。
- **造单回收**：`atlas recycle` 列出自动创建的单据供人工回收，不自动删除。
- **JMeter 导入**：`scripts/import_jmeter.py` 把 .jmx 工程转 Atlas DSL YAML，供生产巡检。

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

# 造单回收：列出自动创建的单据（不自动删除）
atlas recycle
atlas recycle --input reports/recycle_list.jsonl --cleanup

# JMeter 工程转 Atlas DSL
python scripts/import_jmeter.py patrol.jmx -o examples/patrol.yaml
```

### DSL 扩展字段

| 字段 | 作用 |
|---|---|
| `channel: ui/api/auto` | 覆盖 Task 的通道意图（`auto` 需 Task 显式支持 API） |
| `on_env_blocked: skip/stop` | 环境阻塞时跳过该步（记入 `ctx["_steps_skipped"]`）或终止；默认 `stop` |
| `requires.<key>.on_env_blocked` | 数据供给失败时跳过整条数据条件，而不是伪装成“找到了” |
| `task: assert_api` | UI 之后按接口断言真实落库值，支持 `data.0.paymentTerms` 点路径 |
| `task: legacy_step` | 把旧 `step_N` 接进 Atlas 流程，不改旧代码 |

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
- UI 失败需要 API 实证时，由业务流提供精确重放器；引擎不猜测接口，尤其不自动重放写接口。
- 已知环境限制不伪装成 PASSED，也不通过修改断言变绿。

## 配置

完全沿用 `legacy-suite` 的 `DEMO_USERNAME`、`DEMO_PASSWORD`、`DEMO_PWD_{账号大写}` 和七个 `DEMO_URL_*` 键名。默认从 Atlas 根读取 `.env`；旧用例仍从旧项目根读取自己的 `.env`。

可选路径：

| 变量 | 默认值 |
|---|---|
| `ATLAS_LEGACY_PROJECT` | `../legacy-suite` |
| `ATLAS_DEMO_API_PROJECT` | `../legacy-api` |
| `ATLAS_RUNLOG_HOME` | `../runlog` |
| `ATLAS_TOOLBOX_PROJECT` | `../toolbox` |
| `ATLAS_BROWSER_CHANNEL` | `chrome` |

## 当前验证边界

- 离线测试：**279 passed**，Python 3.13；编译、CLI、外部资产路径发现已通过。
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

详见 [AGENTS.md](AGENTS.md)、[CLAUDE.md](CLAUDE.md)、[PRIVACY.md](PRIVACY.md)。

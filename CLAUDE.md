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
pytest -q

# 未安装时的 Windows bash 门禁
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m compileall -q src
```

PowerShell 设置模块路径：`$env:PYTHONPATH="src"`。

## 关键模块

| 文件 | 用途 |
|---|---|
| `actor.py` | Actor、Ability 注入、`attempts_to`、ctx |
| `core.py` | Task 协议与强制元数据 |
| `abilities.py` | Playwright page、REST、Cookie、DemoApi 适配、会话保活 |
| `composition.py` | `compose` 与 `compose_for_goal` 依赖编排 |
| `config.py` | `.env`、环境键、生产防线、写操作校验 |
| `session.py` | 单浏览器、run/debug/heal、快照 |
| `engine.py` | Task 执行策略、auto 通道路由、契约、heal、实证 |
| `recorder.py` | 零依赖 JSONL 运行记录 |
| `legacy.py` | 原项目 pytest 和 `step_N` 双轨兼容 |
| `dsl.py` | 线性 YAML DSL、变量插值、assert 块、数据供给 |
| `probes.py` | find/create 策略、环境与配额防线、回收清单 |
| `failures.py` | 失败分类与 API 实证 |
| `knowledge.py` | 知识命中、选择器注册表、人审写回 |
| `contracts.py` | 契约三档：core/high/minimal |
| `healing.py` | 知识驱动自愈（只读+已审核知识） |
| `state_machine.py` | 复杂单据按需状态机、守卫与确定性迁移 |
| `agent.py` | 规划、风险分级、审批、决策留痕 |
| `creators.py` | 已验证建单脚本接入与「非人工创建」护栏 |
| `experience.py` | 失败草稿、人审提升正式知识 |
| `runtime.py` | 一次登录、Cookie、DemoApi 白名单、探针/契约装配 |
| `reporting.py` | runlog 格式导出、运行聚合 |
| `toolbox.py` | toolbox 感知器官、抓包/重放/导出 |
| `tasks.py` | 纯 API Task、通用 UI Task、assert_api |
| `cli.py` | 命令行入口 |
| `waits.py` | 语义等待：表格行数、mask 消失、Vue 字段、URL 变化 |
| `plugins/demo/tasks.py` | 10 个核心 Task，委托原 `pages.py`；auto 路由子类 |
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

## 常见问题

### Q: 为什么旧用例不直接拷到 Atlas？
A: 拷贝会形成两份漂移代码，并破坏旧项目的依赖、登录态和验证基线。`LegacyAdapter` 在原目录调用 pytest；新增能力通过 `LegacyStep` 渐进接入。

### Q: `channel: auto` 是否一定 API 优先？
A: 它是意图声明，只有 Task 明确实现 API 操作才启用路由。未验证接口不能自动取代真实 UI，更不会自动重放写操作。

### Q: 为什么找不到数据不自动创建？
A: `find_only` 明确禁止造数；`find_or_create` 也必须由业务插件提供受审核 creator。当前 Demo 默认只实现探测，未伪造建单能力。

### Q: agent 如何拼装流程？
A: 读取 `atlas tasks` 的 `requires/produces`，`compose_for_goal(tasks, goal)` 递归选择必要 Task，再拓扑排序；缺依赖/循环会明确失败。

### Q: 如何真实运行混用？
A: `DemoRuntime` 统一登录、烘焙 Cookie、装配白名单 DemoApi、契约、知识和探针。
`python examples/hybrid_readonly.py` 已实测 UI 会话与 API 只读查询同时可用。
`atlas run <flow.yaml>` 可注入数据供给；默认 create 配额 0。

### Q: agent 能自己决定跑什么、能不能跑吗？
A: 能规划与评估，但不能自行越权。`AtlasAgent` 按 `requires/produces` 规划，再按业务影响分级：
`read_only` 可自动执行，`low/medium/high` 需 `approved=True`；生产写恒为 `high`。
每次决策写入 `reports/agent_decisions.jsonl`（含被拒绝的原因），可审计。

### Q: create（造数）真的能用吗？
A: 能用，且已真实跑通。SIT DEMO 的建单实现是 `atlas/plugins/demo/creators.py`：
报文模板来自**真实 UI 保存请求捕获**（`scripts/capture_save_request.py`），不是猜字段。
`examples/api_create_then_ui_verify.py` 实测 API 建出 `SO-DEMO-0001`，UI 列表查到该单、PT 一致。
护栏：生产禁写、配额在调用前占用、结果必须含「非人工创建」与单号。
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

### Q: 测试已经覆盖真实 SIT 吗？
A: 离线验证 **279 passed**。真实 17 条已用 `atlas legacy --keyword` 分批跑完：
**13 passed / 4 skipped / 0 failed**（TC1 / TC2~6 / TC7~9 / TC10~13 / TC14~17 五批），
skip 全为已知环境或产品限制。

### Q: 离线全绿就代表 Atlas 能用吗？
A: 不代表。本轮真实执行抓到两个离线发现不了的缺陷：
① 加载 `pages.py` 未把 `legacy-web/` 放进 `sys.path` → `No module named 'lib'`；
② 核心 Task 没带 `page_key` 等必需默认参数 → `open_doc_list() missing 1 required positional argument`。
所以改完必须跑 `atlas run examples/atlas_core_verify.yaml` 与 `atlas_save_verify.yaml`。

### Q: 分批回归踩过什么坑？
A: 两个必踩点：
① `-k tc1` 会子串命中 `tc10~tc17`，必须写 `test_tc1_` 这种带下划线前缀；
② 删 `plan_ctx.json` 后 TC2~TC17 缺 `_tc1_ok` 会全部 SKIP，因此必须先跑 TC1 再跑其余批次。

### Q: heal 模式会自己改代码吗？
A: 不会。只有「只读 Task + 命中已审核知识（`status=approved` 且有 remedy）」才就地重跑一次；
写操作永不自动重试，未审核知识也不触发，失败仍原样抛出。

### Q: 探针为什么扫描多页？
A: 销售单列表默认第一页几乎全是草稿 `1001`，只查第一页会误判“无来源单”。
`SalesOrderProbe` 有界分页（默认 200/页、最多 10 页），未命中短页即停止。
另：绕过 `DemoApi.__init__` 时必须补 `STATIC_HEADERS`，否则接口回 `BUG-920 app is null`。

### Q: 新增第二个公司的业务怎么做？
A: 新建 `plugins/{domain}`，提供 Task、Probe、知识库与必要驱动适配；Actor、DSL、执行、记录、分类引擎无需随业务重写。

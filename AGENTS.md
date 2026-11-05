# AGENTS.md — Atlas AI 编码代理约束

## 0. 定位

Atlas 是可携带的 UI/API 测试自动化平台，不是某个公司的专用脚本集合。业务在插件中，引擎不读取具体客户、单号或业务选择器。

- 核心：Screenplay Actor + `BrowseTheWeb` + `CallApi`
- 资产：线性 YAML DSL + 旧 `legacy-suite` 双轨
- 知识：`plugins/{业务}/knowledge/`，是数据而不是散落在引擎里的 if/else
- 感知：toolbox 负责看/录/重放；Playwright/DemoApi 负责确定性执行
- 智能：元数据拼装、知识命中、人审自愈；不是让 agent 自由点击取代回归

## 1. Never（硬性禁止）

1. **禁止硬编码凭据或真实 `.env` 入库**。沿用 `DEMO_*` 键名，密码只能从环境读取。
2. **禁止生产写操作**。`prod-*` 的建单、保存、提交、删除由引擎拒绝；不要加绕过开关。
3. **禁止自动创建无「非人工创建」备注的单据**；create 要受配额限制并保留可回收信息。写操作门 `validate_write` **无条件要求备注含标记**，空/缺备注本身即违规（不得用 `remark=""` 绕过）。
4. **禁止静默修改 `legacy-suite` 或 `legacy-api` 原仓库**。它们通过适配器复用，当前 Atlas 的实现不得向原仓库写文件。
5. **禁止把 toolbox 当执行底座**。它是感知器官；不能用其替代 Python+Playwright 兼容层。
6. **禁止步骤内数据兜底**：换客户、循环试备件、降数量、抄历史单都不允许。数据缺失用 `EnvBlocked` 明确终止。
7. **禁止为变绿而放宽断言、吞异常、把失败改 SKIP**。环境限制、产品缺陷、代码缺陷必须分开。
8. **禁止无界面截图开销进入 run 模式**。成功步骤不默认截图；debug/heal/失败才取证。
9. **禁止把未验证的 API 当 UI 自动兜底**。`auto` 必须依靠 Task 显式能力，不猜接口、不自动重放写请求。
10. **禁止无审核的知识/选择器/自愈写回**。冷启动一律人审；未审核项只能写 `pending_review` 草稿。
11. **禁止把业务知识写入通用引擎**。客户、PT、Demo 路由、Vue 双模型属于 Demo 插件；与真实旧项目的目录名/类名/键名差异必须走 `ATLAS_*` 映射（`config.py`），不得把真实字面量写回引擎。真实业务知识/选择器/BU坐标的写回目标是私有目录（`ATLAS_KNOWLEDGE_DIR`，读按文件回退包内虚构默认），批准永不写包内文件。
12. **禁止为了“全量”造形式代码**：未验证的 API 建单、完整状态机、389 条强契约不得声称已完成。
13. **禁止绕过 agent 风险阈值**。`AtlasAgent` 默认只自动执行 `read_only`；提阈值或 `--approve` 必须是人的决定，不能由脚本自己传。
14. **禁止让 create 先建单再查配额**。配额必须在调用建单脚本**之前**占用，否则超额时单据已经真实产生。
15. **禁止伪造跨平台验收**。Linux 必须真实执行并归档 `LINUX_ACCEPTANCE_PASSED` 输出到
    `reports/linux_acceptance.json` 才能声称跨平台完成。当前**已验证的是原生 venv 路径**；
    `Dockerfile` / compose / CI **未实测**，不得声称已验证。远程验收只写 `/tmp`，跑完清理，
    不碰业务目录与系统包（若安装了系统包，必须在交付说明中明确列出及回退方式）。
16. **建单报文必须来自真实捕获**，不得凭字段名猜。模板更新走
    `scripts/capture_save_request.py` → `scripts/templates/*.json`，并核对 `remark` 含「非人工创建」。

## 2. 验证纪律

每次改动至少执行：

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m compileall -q src
PYTHONPATH=src python -m atlas doctor
```

- 当前基线：**720 passed**；不可只跑 py_compile 就宣布完成。
- 新测试不得依赖真实网络、浏览器或凭据；外部能力用窄接口 fake。
- 改动旧兼容入口时先 `--dry-run` 核对 cwd/命令，再考虑真实回归。
- 真实 UI 回归必须在有环境配置时执行并报告 PASSED/SKIPPED/FAILED 数量；不能用离线测试冒充实跑。
- 选择器变更至少连跑 3 次；同坑重复出现时优先更新待审核知识，不重复盲试。
- **离线测试通过 ≠ 能力可用**。凡涉及旧项目加载、`pages.py` 参数、Task 产出语义的改动，
  必须用 `atlas run examples/atlas_core_verify.yaml` 或 `atlas_save_verify.yaml` 真实跑一次；
  本轮曾出现离线全绿但真实执行报 `No module named 'lib'` 与 `open_doc_list() missing page_key`。
- **真实 `atlas run` 需先接真实会话值**：仓库 `session.json` 是虚构默认（`DEMO System Admin` 等），
  真实环境 `switch_to_ou` 会超时。跑真实 flow 前必须用 `ATLAS_SESSION_OU`/`ATLAS_SESSION_ACCOUNT`/
  `ATLAS_SESSION_DIVISION` 覆盖为真实值（与 `ATLAS_ENV_PREFIX=ISERVICE_` 一并在环境配置里）；
  或把坐标经人审批准进私有 `bu_overlays.json` 后用 `atlas run <flow> --bu <BU名>` 接入
  （显式 `ATLAS_SESSION_*` env 优先于 `--bu`）。
  示例 flow 里的虚构客户号（如 `CUST0000001`）在真实系统查无属数据事实，不伪装 PASSED。

## 3. 架构不变量

- Task 的 `requires/produces` 必须与 `perform()` 实际读写一致；产出写入 `actor.ctx`。
- `compose_for_goal` 只选择目标所需的依赖，不能执行无关 Task。同一产出键有两个不同生产者时显式报错点名，绝不静默择一
  （引擎无法判定副作用等价；last-wins 会遮蔽可行路径或把只读目标翻成写操作，风险档随之漂移）。
- Actor 的 Ability 由构造注入；业务 Task 不自行启动第二个浏览器。
- UI 和 API 共用会话，但 Cookie 只烘焙到匹配域，禁止把 Cookie 发向无关域。
- 旧用例默认走原 pytest/conftest，确保执行速度与 skip 语义；`LegacyStep` 用于渐进迁移。
- 分批回归必须用 `atlas legacy --keyword "test_tc7_ or test_tc8_"`；**`-k` 是子串匹配**，
  写 `tc1` 会命中 `tc10~tc17`，务必带下划线前缀。
- 删 `plan_ctx.json` 后 TC2~TC17 会因缺少 `_tc1_ok` 直接 SKIP；分批回归必须先跑 TC1。
- 知识写回先草稿后人审；agent 的错误分类经人纠正后才可以成为新 pitfall。
- 新增外部接口要可适配、可离线测试，不复制整套公司项目到 Atlas。

## 4. Ask first（先问再做）

- 任何生产写操作；UAT 的非只写流程也要先确认业务影响。
- 修改外部公司项目、对外平台同步、创建测试计划或发送报告。
- 向第三方 AI/云服务上传代码、知识、截图、接口或业务数据。
- 自动批准自愈、降低人审级别、改变生产保护或自动建单配额策略。
- 引入浏览器驱动之外的运行时（Rust/Node 等）、完整状态机或大型框架依赖。

## 5. 文档维护

改动行为后同步相关文档：

- `README.md`：用户命令、能力、验证边界
- `AGENTS.md`：AI 硬规则、不变量、验证门禁
- `CLAUDE.md`：开发者速览和常见问题
- `PRIVACY.md`：数据/凭据/报告/外部服务边界

只更新受影响的事实，不伪造验证结论。Demo 新坑写入插件知识库的**待审核草稿**，不要堆到通用引擎。

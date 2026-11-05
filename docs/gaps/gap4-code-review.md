# Gap 4：测试代码质量评审（`atlas review-code`）方案

> 状态：已落地（src/atlas/review_code.py）
> 负责：gap-review ｜ 关联：`cli.py` / `dsl.py` / `contracts.py` / `knowledge.py` / `review.py` / `reporting.py` / `failures.py`
> 对应手册 2.6 `scs-test-code-review`（测试代码质量评审工具，生成含评分的专业评审报告）
> 前置：`docs/gaps/gap5-allure.md`（评审报告走 Allure 2.39.0 风格）

---

## 0. 一句话目标

新增 **`atlas review-code`**：对 **pytest 用例 / DSL flow YAML / 契约定义** 做**质量评审**，输出**含评分的专业报告**（Allure 2.39.0 风格）。核心价值：**能反向发现"为变绿而放宽断言"的反模式**（弱断言、假绿、生产写风险），呼应红线 #7「禁止为变绿而放宽断言、吞异常、把失败改 SKIP」。

---

## 1. 与现有 `atlas review`（审核塔）的边界（最重要）

**两者名字像，审的东西完全不同，必须显式区分——否则命名冲突会误导使用者与后来实现者。**

| 维度 | `atlas review`（审核塔，**已有**） | `atlas review-code`（**新增**） |
|---|---|---|
| 审什么 | **运行产物**：失败草稿、坑点候选、选择器候选、BU 坐标差异、trace 步骤 | **测试代码质量**：pytest 用例、DSL flow YAML、契约 JSON |
| 输入 | `reports/experience_drafts.jsonl`（运行时产生） | 源码文件/目录（人指定或 CI 变更集） |
| 触发 | run 失败后自动进塔 | 按需 / CI / PR review |
| 单元 | **证据卡片**（一次运行事件，30 秒判断） | **finding**（一处代码问题，带 `file:line` + rule id） |
| 输出 | 批准/驳回 → 写回知识 | **评分报告**（Allure 2.39.0 风格）+ 机器可读 JSON |
| 写知识 | **是**（人审 `--approve`，唯一写路径） | **否**（永不写知识、永不批准） |
| 队列 | 复用 `experience_drafts.jsonl` | 默认**不写**塔（报告即产物）；可选 `--to-tower` 进同一塔（见 §9） |
| 不变量 | critical 档无自动晋级 | 只评审、不改代码、不放宽断言 |

**命名/命令空间决策**：
- 新命令 `atlas review-code`（子命令），**不得**做成 `atlas review --code`（会把两件事塞进同一入口，语义互相污染）。
- 若将来有更多"评审类"命令，统一 `review-*` 前缀（如 `review-code`）。
- 文档/帮助文本里 `atlas review` 一律称"审核塔（运行产物）"，`review-code` 称"代码质量评审"。

**是否共享 queue 格式**：默认**否**。评审产出是"报告"，不是"待审草稿"——两者生命周期不同（草稿是运行事实，finding 是静态意见）。仅当用户显式 `--to-tower` 时，把 **`kind: "code_review"`** 的条目写进**同一个** `experience_drafts.jsonl`（复用 `ExperienceJournal`），让评审意见也能在同一个塔里被人审分诊；该 kind 与既有 `pitfall/selector_candidate/trace_step/bu_overlay` **不冲突**，`review.approve` 对它报"需人工按规则处置"（不自动写知识）。

---

## 2. 评审对象与形态

| 类型 | 文件 | 结构依据 | 可复用的既有校验 |
|---|---|---|---|
| **pytest 用例** | `tests/**/*.py`、legacy 用例 | Python AST + 断言节点 | 无（新写 AST 规则扫描） |
| **DSL flow** | `examples/*.yaml`、`*.flow.yaml` | 线性 DSL：`steps[]` / `assert[]` / `requires` / `on_env_blocked` | `dsl.FlowCompiler.validate_flow`（结构）＋ `_check_supply_targets` |
| **契约** | `contracts/*.json`、私有 `core.json` | `{contracts:[{name,method,path,level,required,result_code}]}` | `contracts.ContractRegistry.load` ＋ `cli._validate_contracts` 的判据（空 path/name） |

`--kind auto` 按扩展名/内容自动识别；显式 `--kind pytest|dsl|contract` 可覆盖。

---

## 3. 评审模式（至少两种）

### 3.1 模式 A：静态规则扫描（`--review-mode static`，默认）

- **纯规则、离线、确定性、零网络、零 AI**：同一输入永远同一评分。
- 实现：规则包（§5）对目标做 AST/Markdown/YAML/JSON 扫描，产出 finding 列表。
- 适用：CI 门禁、PR 评审、提交前自检——**必须能独立工作**（不依赖任何 LLM）。

### 3.2 模式 B：知识增强评审（`--review-mode knowledge`）

- 在 A 之上叠加 **Atlas 自有知识库**（**仍离线**）：
  - `knowledge.pitfalls()`（30 条坑点本身就是**反模式词典**：如 `demo-024`「业务按钮 JS click 无反应」、`demo-002`「不要改业务断言」）——命中即作为**加权证据/引用**。
  - `knowledge.selectors()/components.json`：用例里硬编码的选择器若**未在组件注册表**，判可维护性问题（应走注册表）。
  - `contracts`：接口调用若**无对应契约**，判覆盖度问题。
- 适用：既想要确定性又想结合团队沉淀；报告里能引用"命中了哪条已审坑点"。

### 3.3 模式 C：AI/语义增强评审（`--review-mode ai`）— **未实现**

- **当前未实现**：CLI 拒绝 `--review-mode ai`（argparse rc 2，不静默降级到 static）。
- 设计意图（待 P4 立项）：在 B 之上加 **可选** 的 LLM"外脑"做**语义判断**——断言是否真在验证业务意图？flow 是否覆盖了 intent？
- **默认关闭**，且**永不自动向第三方上传代码**（AGENTS 红线 #4「向第三方 AI/云上传代码/知识/接口数据要先问」＋ `PRIVACY.md`）。启用需显式配置 brain（用户自己的 provider/本地模型），报告注明"由 AI 增强，结果非确定性"。
- **不可作为唯一门禁**：CI 门禁恒以 `--review-mode static|knowledge` 为准；AI 模式只产出建议性 finding（label `atlas.ai_advisory=true`）。

---

## 4. 评分体系（可解释 / 可配置 / 不机械凑分）

### 4.1 维度与权重（默认，全部可配置）

| 维度 key | 名称 | 默认权重 | 说明 |
|---|---|---|---|
| `assertion` | **断言强度** | **0.25** | 断言是否真验证业务值；弱断言/恒真断言/仅成功码（权重最高，绑定红线 #7） |
| `safety` | **安全性** | 0.20 | 凭据/生产写/非人工创建备注/未验证 API 兜底 |
| `maintainability` | **可维护性** | 0.20 | 语义等待 vs sleep、选择器注册表、魔法值/写死数据 |
| `coverage` | **覆盖度** | 0.20 | 有写无验、契约缺失/坏形态、分支/终态未覆盖 |
| `duplication` | 重复度/一致性 | 0.15 | 重复步骤块、命名、断言重复 |

> Σ 权重 = 1.00（启动校验；不等于 1 报配置错误 rc 2）。权重经 `reviewcode.json` 覆盖。

### 4.2 计分算法

```
每维度得分 score_d = max(0, 100 - Σ(该维度下 finding 的 deduction))
总  分  total   = round(Σ(weight_d × score_d))
```

- `deduction = severity_deductions[severity]`（默认 `critical=25, high=15, medium=8, low=3`），可配置。
- **不机械凑分**：deduction 只来自**具体 finding**（`file:line` + rule id + 证据片段），无 finding 即满分 100；没有"基础分""印象分"。
- **critical 硬顶**：出现任一 `critical` finding（弱断言/假绿/生产写，即触碰红线）时 `total = min(total, critical_cap)`（默认 59 → 强制不及格档），防止"问题很多但平均分还行"的假象。

### 4.3 等级与门禁

```
A ≥ 90   B ≥ 80   C ≥ 70   D ≥ 60   F < 60
--fail-under N：total < N 或存在 critical finding → 退出码 1（门禁失败）
```

### 4.4 可解释输出（每 finding 必备字段）

```json
{"rule_id":"ASSERT-002","category":"断言强度","severity":"high","dimension":"assertion",
 "file":"tests/test_x.py","line":42,"snippet":"assert resp is not None",
 "rationale":"仅判非 None，未验证任何业务字段；接口返回空对象也会通过",
 "suggestion":"断言 resp['data']['paymentTerms'] == 'NET30'","deduction":15}
```

---

## 5. 反模式规则库（核心：弱断言 / 假绿 / 生产写 / 变绿放宽）

规则按"归因类"分组，每条**主绑定一个维度**、带 severity。规则用 `--config` 可逐条开关。

### 5.1 断言强度 `ASSERT-*`（dimension: assertion）

| rule_id | 反模式 | 侦测 | severity |
|---|---|---|---|
| ASSERT-001 | 恒真/无断言 | `assert True/1/...`、`assert x`（x 为字面量真）、空 `assert` | critical |
| ASSERT-002 | 弱断言 | 仅 `is not None` / `isinstance` / `len(x)>0` / `== True` | high |
| ASSERT-003 | 仅成功码 | pytest 只断 `status_code==200`；DSL `expect` 只断 `resultCode: BUG-000` | medium |
| ASSERT-004 | 路径恒真（DSL） | assert 操作数是两个**打错的路径**（形如 `word.word` 但不在任何 step 的 `produces` 里）→ 恒真假绿（复刻 `#113` 面A 形态） | critical |
| ASSERT-005 | 非空即过（DSL） | 仅有 `document_no != ""` 这类非空断言，无值相等断言 | medium |
| ASSERT-006 | 断言被注释/停用 | `# assert`、`pytest.mark.xfail` 无 reason、`@pytest.mark.skip` 无 reason | high |
| ASSERT-007 | 用容差掩盖 | `pytest.approx`/`abs(a-b)<ε` 掩盖本应精确相等的值 | medium |

### 5.2 假绿 / 吞异常 `GREEN-*`（dimension: assertion）

| rule_id | 反模式 | 侦测 | severity |
|---|---|---|---|
| GREEN-001 | 吞异常 | `except Exception: pass` / `except: pass` / 裸 except 后无 re-raise | critical |
| GREEN-002 | 断言被 try 包住后继续 | `try: assert ... except: continue/pass` | critical |
| GREEN-003 | **失败改 SKIP** | diff/基线中：原断言位置新增 `pytest.skip(...)`，或断言被删而同用例此前为 failed | critical |
| GREEN-004 | 掩盖告警 | `# type: ignore` / `# noqa` / `pytest.warns(None)` 滥用 | low |
| GREEN-005 | 断言对象是 mock | 被断言的变量由 mock 固定返回，验证的不是被测物 | high |
| GREEN-006 | 断言降级为打印 | diff 中 `assert` → `print/log` | critical |

### 5.3 生产写 / 安全 `SAFE-*`（dimension: safety）

| rule_id | 反模式 | 侦测 | severity |
|---|---|---|---|
| SAFE-001 | 硬编码凭据/URL | 明文密码/token/真实 URL 字面量（红线 #1） | critical |
| SAFE-002 | 生产写步骤 | DSL 写 task + `env: prod-*`；或调用建单/保存/提交而目标环境为生产 | critical |
| SAFE-003 | 无「非人工创建」 | creator/DSL 建单路径 `remark` 缺失或为空（红线 #3） | critical |
| SAFE-004 | 配额后置/无单号 | create 配额在调用后占用；creator 无单号校验 | high |
| SAFE-005 | 未验证 API 兜底 | DSL `channel: auto` 用了无显式 API 能力的写操作（红线 #9） | high |

### 5.4 可维护性 `MAINT-*`（dimension: maintainability）

| rule_id | 反模式 | 侦测 | severity |
|---|---|---|---|
| MAINT-001 | 硬 sleep | `time.sleep(...)` 而非 `waits.py` 语义等待 | high |
| MAINT-002 | 硬编码选择器 | 选择器字面量**不在** `components.json` 注册表 | medium |
| MAINT-003 | 写死数据 | 写死历史单号/客户号（`CUST0000001` 类），应走 `requires`/`find_*` | medium |
| MAINT-004 | 写死环境值 | 硬编码 URL 路径/环境键，应走 `ATLAS_*`/`.env` | medium |
| MAINT-005 | 步骤内数据兜底 | 换客户/降数量/抄历史单凑过（红线 #6） | critical |
| MAINT-006 | DSL 结构坏 | 缺 `task`/引用未注册 task/步骤非映射/供给落点冲突（复用 `validate_flow`） | high |

### 5.5 覆盖度 `COV-*`（dimension: coverage）

| rule_id | 反模式 | 侦测 | severity |
|---|---|---|---|
| COV-001 | 有写无验 | step 声明了 `produces` 但 `assert` 块无任何消费该产出键的断言 | high |
| COV-002 | 契约缺失/坏 | 接口调用无契约；契约空 `path`/空 `name`（复用 `_validate_contracts` 判据）；契约文件形态坏 | medium |
| COV-003 | 分支未覆盖 | 状态机 `terminal`/`guard` 未在用例中触及 | medium |
| COV-004 | 仅 happy path | 无失败/边界/空数据场景 | low |

### 5.6 重复度 `DUP-*`（dimension: duplication）

| rule_id | 反模式 | 侦测 | severity |
|---|---|---|---|
| DUP-001 | 重复步骤块 | 连续/多处相同 step 序列（可抽任务/子流） | low |
| DUP-002 | 命名不一致 | step `id` 重复/缺失、命名风格混用 | low |
| DUP-003 | 断言重复 | 同断言在多处复制而无抽取 | low |

> **规则与 `FailureClass` 正交**：规则归因（§6）描述**代码问题性质**；`FailureClass` 描述**运行失败性质**。"变绿放宽断言"的侦测跨两者（见 §7）。

---

## 6. 问题归因分类（category）

finding 的 `category`（人读）与 `dimension`（算分）解耦，保证报告可读性与评分权重独立可调：

```
断言强度 ASSERT-*  → dimension assertion
假绿/吞异常 GREEN-* → dimension assertion
生产安全 SAFE-*    → dimension safety
可维护性 MAINT-*   → dimension maintainability
覆盖度  COV-*      → dimension coverage
重复一致 DUP-*     → dimension duplication
```

---

## 7. "为变绿而放宽断言"的专项侦测（红线 #7 的核心落点）

三种互补手法，静态模式可做全部：

1. **静态反模式**（同一次扫描内）：GREEN-001/002/003/006 + ASSERT-001/002 + MAINT-005——单就从代码形状就能判"这条断言形同虚设 / 把失败藏起来了"。
2. **基线差分**（`--baseline`）：给定上一版用例或 git ref，
   - 断言被删除/注释（`assert` 计数下降）；
   - `assert` → `skip`/`print`（GREEN-003/006）；
   - 断言比较被弱化（`== v` → `!= ""`、`is not None`）。
3. **运行史交叉**（`--baseline <runs.jsonl>`，接 Gap 5）：
   - 某 `case` 此前 `status=failed` 且 `fail_class=assertion`，**现在 `passed`**，而其断言文本在同一变更中被弱化/删除 → 判 **critical**："疑似为变绿放宽断言"。
   - 反向也报：`env_blocked` 被改成 `passed` 且无环境变化证据。

> 该侦测**只报告不修改**；结论写入报告并（可选 `--to-tower`）进审核塔由人裁定——**永不自动改代码、永不自动批准**。

---

## 8. 报告格式（Allure 2.39.0 风格）

复用 Gap 5 的 `allure_report.emit_allure` 基础设施，产出**真正的 Allure 2.39.0 结果集**，而非自绘 HTML：

### 8.1 `--format allure`（默认）

映射：**被评审文件 = 一个 Allure test result**；**每个维度 = 一个 step**；**每条 finding = 维度 step 下的子 step**；`atlas.score` 作为 label，severity 用 Allure 原生 `severity` label。

```
allure-results/
├── <uuid>-result.json          # 每个被评审文件一条
│   name = "review-code: tests/test_x.py"
│   status = passed | failed     # failed = 有 critical/high finding
│   labels = { suite:"review-code", severity:<最高severity>,
│              atlas.score:"72", atlas.grade:"C", atlas.mode:"static",
│              atlas.ai_advisory:"false" }
│   steps = [
│     { name:"断言强度 40/100", steps:[ {name:"ASSERT-002", status:"failed",
│         statusDetails:{message:"weak assert", trace:"L42 ..."}, ...} ] },
│     { name:"安全性 100/100", steps:[] }, ... ]
│   attachments = [ {name:"review-report.md", source:"<uuid>-attachment.md",
│                    type:"text/markdown"} ]
├── <uuid>-attachment.md         # 完整报告：总分/维度表/问题清单/改进建议
└── <uuid>-container.json        # 维度↔finding 的层级容器
```

- 总分/维度分：写入 label（`atlas.score`/`atlas.grade`）+ Markdown 附件表格。
- 问题清单：`file:line + rule_id + category + severity + rationale + suggestion`。
- `allure serve reports/review-code-results` 直接可视化，与测试报告同体系（满足"报告统一 Allure 2.39.0"）。

### 8.2 `--format json`

机器可读：`{total, grade, dimensions:{...}, findings:[...], critical_count, config_used}`，供 CI 门禁与看板。

### 8.3 `--format text`

人读摘要（总分 + 维度分 + Top finding + 建议），适合终端/PR 评论。

---

## 9. CLI 设计

```bash
atlas review-code <path...>                         # 默认：static + allure，输出 reports/review-code-results
atlas review-code tests/ examples/return_from_so.yaml contracts/core.json
atlas review-code tests/ --kind pytest --review-mode knowledge
atlas review-code sln/ --format json --fail-under 75      # CI 门禁：<75 退出码 1
atlas review-code tests/ --baseline reports/runs.jsonl    # 交叉运行史侦测"变绿放宽"
# atlas review-code tests/ --changed-only        # 仅评审 git 变更文件（PR 场景）— 未实现（P4 可选）
atlas review-code tests/ --to-tower                       # 可选：finding 作为 kind=code_review 进审核塔
atlas review-code --config reviewcode.json
```

参数：

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `path...` | 文件/目录 | 必填 | 评审目标 |
| `--kind` | `auto/pytest/dsl/contract` | `auto` | 目标类型；auto 按扩展名/内容识别 |
| `--review-mode` | `static/knowledge` | `static` | 评审模式（§3）；ai 未实现，CLI 拒绝 |
| `--format` | `allure/json/text` | `allure` | 输出格式 |
| `--out` | 目录 | `reports/review-code-results` | Allure 结果集输出目录 |
| `--config` | 文件 | 无 | 权重/阈值/规则开关 |
| `--fail-under` | int | 无 | 总分阈值，低于即门禁失败 |
| `--baseline` | 文件 | 无 | 基线（上一版用例或 `runs.jsonl`）做差分 |
| `--changed-only` | flag | off | 仅评审 git 变更文件 — **未实现**（P4 可选） |
| `--to-tower` | flag | off | finding 进审核塔（`kind=code_review`） |

**退出码**（沿用现有约定）：
- `0` 通过（无 finding 或总分 ≥ 阈值且无 critical）。
- `1` 有问题 / 低于阈值 / 有 critical finding（与 `doctor` 的 rc1「报不一致」同族）。
- `2` 用户/输入错误（路径坏、配置权重和≠1、YAML/JSON 坏）——复用 `cli._user_error`（`#79/#80`）。

**铁律**：`review-code` **不改代码、不写知识、不批准、不放宽断言**；`--to-tower` 也只造 `pending_review` 草稿，人审仍走 `atlas review`。

---

## 10. 与现有模块的复用

| 模块 | 复用点 |
|---|---|
| `dsl.FlowCompiler.validate_flow` | DSL 结构校验（步骤形态/task 引用/assert/requires/落点冲突）直接调用，避免重写 |
| `dsl._check_supply_targets` / `DataRequirement.from_dict` | requires 供给落点冲突与形态校验 |
| `contracts.ContractRegistry.load` + `cli._validate_contracts` | 契约加载 + 空 path/name「冒充覆盖率永不生效」判据 |
| `knowledge.pitfalls()/selectors()/contracts()` | 知识增强模式的证据与引用（坑点=反模式词典） |
| `failures` | 报告里若引用运行史，用 `FailureClass` 归类失败（与 Gap 5 `classify_text` 同源） |
| `reporting` 读侧纪律 | 读 `runs.jsonl` 基线复用容错读法（`#112/#131`），坏行跳过计数 |
| `review.ReviewQueue` / `experience.ExperienceJournal` | `--to-tower` 写草稿；`kind=code_review` 与既有 kind 不冲突 |
| `allure_report`（Gap 5） | `--format allure` 复用结果集落盘与附件写入 |
| `cli._user_error` | rc 2 友好报错 |
| `config` | `reports_dir` 同源，不赌 CWD |

---

## 11. 红线遵守清单

- **不放宽断言**：本工具**只会**把"放宽断言"标为问题；自身无任何改断言/改代码路径。
- **不写知识**：无 `--approve` 语义；`--to-tower` 也只进 `pending_review`（红线 #9/#10）。
- **不改外部资产**：只读扫描；不写 `legacy-suite`/`legacy-api`（红线 #4）。
- **不上传数据**：`static/knowledge` 全离线；`ai` 模式默认关闭，启用需用户显式配置且文档标注（红线 #4 + PRIVACY）。
- **不引运行时依赖**：规则扫描用 stdlib（`ast`/`json`/`yaml`）；AI 模式外脑可插拔、非默认。
- **离线可测**：全流程不依赖网络/浏览器/凭据（AGENTS §2）。
- **报告统一 Allure 2.39.0**：与 Gap 5 同体系。

---

## 12. 测试计划（离线，`tests/test_review_code.py`）

**规则与计分**
1. 弱断言 fixture（`assert x is not None`）→ 命中 ASSERT-002，维度 assertion 扣分正确。
2. 恒真断言（`assert True`）→ ASSERT-001，critical，总分被 `critical_cap` 顶到 ≤59。
3. DSL `assert: [document_no != ""]` → ASSERT-005；两个打错路径 `a.x != a.y` → ASSERT-004（复刻 `#113`）。
4. `except Exception: pass` → GREEN-001 critical。
5. 建单无备注 → SAFE-003 critical；`time.sleep` → MAINT-001；硬编码选择器不在注册表 → MAINT-002。
6. 契约空 path/name → COV-002（复用 `_validate_contracts` 判据）。
7. 无 finding → total=100，grade A。
8. 权重和 ≠ 1 → 配置错误 rc 2。
9. 计分确定性：同输入两次扫描同分。

**变绿放宽专项**
10. diff 中 `assert` → `pytest.skip` → GREEN-003 critical。
11. `--baseline runs.jsonl`：case 曾 `failed(assertion)` 现 `passed` 且断言被弱化 → 报"疑似放宽断言" critical。
12. 断言被注释掉 → ASSERT-006。

**报告与 CLI**
13. `--format allure` → 产出可解析的 `*-result.json`，label 含 `atlas.score`/`atlas.grade`，Markdown 附件含维度表与问题清单。
14. `--format json` → 结构与 §8.2 一致。
15. CLI：`--fail-under 90` 且总分 72 → rc 1；坏路径/坏 config → rc 2；全绿 → rc 0。
16. `--to-tower`：findings 进 `experience_drafts.jsonl` 且 `kind=code_review`、`status=pending_review`；`atlas review` 能看到但 `approve` 报"需人工按规则处置"。
17. 读 `--baseline` 时坏字节/坏行 → 跳过计数不崩（`#131`）。
18. `--review-mode ai` 未实现 → CLI 拒绝（argparse rc 2，不静默降级到 static）。

门禁：`PYTHONPATH=src python -m pytest -q`（533 passed 基线不回归）＋ `compileall`。

---

## 13. 分阶段实施

1. **P1**：`review_code.py` 骨架 + 规则引擎 + `static` 模式 + 计分 + `--format json/text` + §12 测 1–9。
2. **P2**：`knowledge` 模式（接坑点/组件/契约）+ 变绿放宽专项（差分/运行史）+ 测 10–12。
3. **P3**：`--format allure`（复用 Gap 5 基础设施）+ CLI 门禁/`--to-tower` + 测 13–18。
4. **P4（可选）**：`--review-mode ai`（可插拔外脑，默认关闭）+ `--changed-only` git 集成（两者均未实现）。

文档同步（AGENTS §5）：落地后更新 `README.md`（新命令）、`CLAUDE.md`（关键模块表加 `review_code.py` 并强调与 `review.py` 边界）、`AGENTS.md`（若加验证门禁）。

---

## 14. 待确认 / 风险

- **规则误报**：静态规则必然有假阳性（如合法的 `is not None`）——所有规则可 `--config` 逐条开关，且报告用 severity 分级，门禁只看 critically 与阈值，避免"规则治理变成新负担"。
- **`--changed-only` 的 git 依赖**（未实现）：需在仓库内运行；离线无 git 时退化为全量。
- **AI 模式合规**：涉及上传代码是 AGENTS #4 的 "Ask first" 项——默认关闭且不自动启用，文档显著标注。
- **评分口径主观性**：权重与 severity 扣分默认值需团队校准一次（`reviewcode.json` 版本化），避免"分数不可比"。
- **与 `atlas review` 的学习曲线**：两命令名字近似，帮助文本与文档必须反复强调边界（本方案 §1）。

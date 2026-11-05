# Gap 5：Allure 2.39.0 报告集成方案

> 状态：已落地（src/atlas/allure_report.py + allure_analysis.py）
> 负责：gap-review ｜ 关联：`reporting.py` / `failures.py` / `healing.py` / `review.py` / `experience.py` / `engine.py` / `cli.py`
> 目标读者：Atlas 维护者；实现前先评审本方案。

---

## 0. 一句话目标

把 Atlas 的运行结果（`reports/runs.jsonl`）**产出**为 **Allure 2.39.0** 结果集（`allure-results/*-result.json`），并让失败分析能够**解析** Allure 2.39.0 结果 JSON，接入 Atlas 现有的 `failures` 分类学、`healing` 自愈准入与 `review` 审核塔——对应手册 2.5 `scs-failed-tests-analysis`（失败用例分析与自动修复助手，解析 Allure 结果并分类处理）。

**双向**：
1. **产出（emit）**：Atlas 结果 → Allure 2.39.0 结果集 → `allure serve` 可视化。
2. **汇入（parse）**：Allure 2.39.0 结果 JSON → Atlas 失败分类 → 自愈提案 / 审核塔草稿。

---

## 1. 现状（必须先讲清楚的既有事实）

### 1.1 运行记录形态（`recorder.py` → `runs.jsonl`）

`RunRecorder.close()` 每次运行追加一行 JSON，形态：

```json
{
  "run_id": "<uuid4 hex>",
  "case": "flow id（flow.get('id','flow')）",
  "env": "sit",
  "mode": "run|debug|heal",
  "status": "passed|failed|env_blocked|interrupted",
  "started_at": "<ISO8601 UTC>",
  "finished_at": "<ISO8601 UTC>",
  "steps": [
    {"step":"open_doc_list","op":"ui","ok":true,"dur_ms":101.2,
     "api_ms": 0, "healed": true, "degraded_to_ui": [...]}
  ]
}
```

步骤级字段（现状）：
- `step` 名称、`op`（`channel` 值：`ui`/`api`/`auto`）、`ok`（bool）、`dur_ms`。
- 失败时带 `fail_class`（`failures.FailureClass` 的 `.value`）。
- 可选：`api_ms`、`healed`（自愈成功就地更正）、`degraded_to_ui`（auto 降级留痕，`#106`）。

### 1.2 现有报告出口（`reporting.py`）

| 函数 | 输入 | 输出 |
|---|---|---|
| `summarize(path)` | `runs.jsonl` | `{runs, cases, status{}, fail_classes{}, skipped_malformed_lines, slowest_steps[]}` |
| `export_runlog(in, out)` | `runs.jsonl` | runlog-steps 格式 JSONL（`run_id, case, env, step, op, ok, dur_ms, api_ms, wait_ms, ui_ms, retries, reloads, fail_class`） |

两个函数都已实现 `UnicodeDecodeError → errors="replace"` 回落（`#131`）与逐行容错（残行/非对象/缺主键跳过）。**新模块必须复用这套读侧纪律**，不得另起一套裸读。

### 1.3 分类学（`failures.py`）——Allure 状态映射的唯一依据

```
FailureClass = env_blocked | session | interaction | contract | data | assertion | interrupted | unknown
CONTROL_FLOW = (KeyboardInterrupt, SystemExit, GeneratorExit)  # → interrupted
```

- 分类单一真相是 `classify_failure(exc)`，其文本提取收敛于 `exception_text(exc)`（`#127`）。
- **不变量**：`env_blocked`（环境/产品限制/生产写护栏）、`interrupted`（操作者终止信号）**从设计上就不是缺陷**，与 `assertion/contract/interaction/data` 这些真实失败严格分开（红线 #7）。
- 该不变量直接决定 Allure 状态映射（见 §4），**不能在报告层被抹平**。

### 1.4 手册口径

`.tmp_handbook.txt`：`scs-failed-tests-analysis`＝"失败用例分析与自动修复助手，解析 Allure 结果并分类处理"，配"四大失败类型"。手册正文（该章节）在被抽取的文本里只剩目录与表情噪声，**未给出四大类型的确切定义**——本方案 §6.3 给出一个基于 Atlas 现有分类学的 4 桶映射，并显式标注"口径待与手册确认"。

---

## 2. Allure 2.39.0 数据模型（实现基线）

Allure **2.x** 的结果不是单一的"报告文件"，而是若干 JSON + 附件组成的结果集目录（默认 `allure-results/`），由 `allure generate` / `allure serve` 渲染成 HTML。2.39.0 沿用 2.x 模型，关键文件：

### 2.1 测试结果 `{uuid}-result.json`

```json
{
  "uuid": "<uuid>",
  "historyId": "<stable id，跨运行聚合历史用>",
  "testCaseId": "<可选>",
  "name": "用例名（显示）",
  "fullName": "完整路径（分组用）",
  "status": "passed | failed | broken | skipped | unknown",
  "statusDetails": {
    "known": false, "muted": false, "flaky": false,
    "message": "失败消息",
    "trace": "堆栈/上下文"
  },
  "stage": "finished",
  "start": 1730611200000,
  "stop": 1730611201500,
  "labels": [
    {"name": "suite", "value": "..."},
    {"name": "host", "value": "..."},
    {"name": "severity", "value": "critical|normal|minor|trivial"},
    {"name": "atlas.fail_class", "value": "assertion"}
  ],
  "parameters": [{"name": "env", "value": "sit"}],
  "steps": [
    {"name":"open_doc_list","status":"passed","stage":"finished",
     "start": ..., "stop": ...,
     "steps": [], "attachments": [], "parameters": [], "statusDetails": {}}
  ],
  "attachments": [{"name":"失败截图","source":"<uuid>-attachment.png","type":"image/png"}],
  "links": []
}
```

### 2.2 步骤容器 `{uuid}-container.json`（可选但推荐）

```json
{"uuid":"<container uuid>","children":["<step-组 uuid>"],
 "befores":[],"afters":[],"start":...,"stop":...}
```

当步骤本身需要挂附件/子步骤时用容器表达，避免混进 result 顶层。Atlas 的 step 直接做成 result 的 `steps[]` 即可；仅当失败现场（截图/证据）需要挂到某个 step 上时才生成容器。

### 2.3 关键约束

- 时间戳为 **epoch 毫秒**（`start`/`stop`）。
- `historyId` 决定趋势图聚合，**必须稳定**（同一用例跨运行相同）。
- `status` 五值：`passed/failed/broken/skipped/unknown`；**没有独立的 "failed-but-env"**——这正是 §4 要处理映射的地方。
- 结果集是**目录**聚合，不是单文件；`allure generate` 读整个目录。

---

## 3. 产出方案（emit：Atlas → Allure 2.39.0）

### 3.1 方案选型

| 方案 | 做法 | 优点 | 代价 |
|---|---|---|---|
| **A. 直出 Result JSON（推荐，默认）** | 新模块 `allure_report.py` 纯 stdlib 生成 `*-result.json` / `*-attachment` / 可选 container | 零新依赖（契合 AGENTS 红线 #4「引入浏览器驱动之外的运行时/大型框架依赖要先问」）；完全离线；Atlas 结果形态本就在 `runs.jsonl`，无需跑 pytest；可测试 | 需自己维护 Allure schema 映射 |
| **B. allure-pytest 适配（可选增强）** | 用 pytest 包装执行 DSL flow，挂 `allure-pytest` 插件自动产出 | 复用官方适配器 | 引入新运行依赖（`allure-pytest`）＋ `allure` CLI；把非 pytest 的 Atlas 执行塞进 pytest 模型，语义错位；违背"不复制/不改写"的既定姿态 |

**结论**：**A 为默认与必须实现**；B 作为可选适配（`atlas allure --via-pytest`），**未实现**（P4 可选），仅在用户显式配置时启用，且文档标注"需额外安装 allure-pytest/allure CLI"。

### 3.2 模块设计 `src/atlas/allure_report.py`

```python
ALLURE_RESULTS_DIR = "allure-results"

def emit_allure(runs_path: Path, out_dir: Path, *,
                clean: bool = True,
                attachments_dir: Path | None = None) -> dict:
    """把 runs.jsonl 转成 Allure 2.39.0 结果集目录（纯 stdlib）。

    - clean=True 时先清空 out_dir 下 Atlas 产的文件（仅删本工具命名前缀，不误删用户附件）。
    - 读侧复用 reporting 的容错纪律：#112/#131 —— UnicodeDecodeError → errors='replace'，
      残行/非对象行跳过并计数，绝不因一条坏行整份失败。
    - 返回 {"results": n, "steps": m, "attachments": k, "skipped_malformed_lines": s, "out": str}。
    """

def _run_to_result(run: dict, out_dir: Path, ...) -> dict:
    """单条 run → 一个 Allure result dict。

    映射：run_id→uuid/historyId（dict 化），case→name/fullName，status→status（§4），
    steps[]→steps[]，env→parameter+label，fail_class→label atlas.fail_class。
    """

def _status_from_run(run: dict) -> tuple[str, dict]:
    """Atlas status + 步骤级 fail_class → (allure_status, statusDetails)，见 §4。"""

def _step_to_allure(step: dict) -> dict: ...

def write_attachment(out_dir: Path, source_path: Path, label: str,
                     media_type: str) -> dict:
    """把截图/证据文件复制进结果集并返回 Allure attachment 条目。

    #126/#64 纪律：源文件读失败/坏字节不得顶掉整份报告——附件缺失时降级为
    一条 text/plain 说明附件，绝不裸崩（报告可以有噪音，不能丢整份）。
    """
```

### 3.3 稳定 `historyId`

`historyId` 用 `sha1(f"{env}|{case}")`（与 `knowledge.draft_pitfall` 用 sha1 而非内置 `hash()` 同理，防 `PYTHONHASHSEED` 漂移），保证同一 `case` 跨运行聚合进 Allure 趋势图。`uuid` 每份结果独立（`uuid.uuid4().hex`）。

### 3.4 CLI

```bash
atlas allure reports/runs.jsonl --out reports/allure-results          # 默认直出
atlas allure reports/runs.jsonl --clean                               # 先清空结果集（默认即清，--clean 为 no-op flag）
atlas allure reports/runs.jsonl --serve                               # 生成后打印 allure serve 命令（不自行拉起）
# atlas allure --via-pytest examples/atlas_core_verify.yaml           # 未实现（P4 可选）：经 allure-pytest（需额外依赖）
```

- 默认输出目录：`<root>/reports/allure-results`（与 `config.reports_dir` 同源，不赌 CWD）。
- 入口错误（文件/路径/配置）复用 `cli._user_error`：一行 stderr + **rc 2**（`#79/#80` 口径）。
- 不自行拉起浏览器、不访问网络、不引入运行时新依赖（除非显式 `--via-pytest`）。

### 3.5 状态映射表（§4 与本节互为引用）

| Atlas run/step | Allure `status` | 附加 label | statusDetails.message | 语义 |
|---|---|---|---|---|
| `passed` | `passed` | — | — | 通过 |
| `failed` 且 `fail_class ∈ {assertion, contract, data, interaction, session}` | `failed` | `atlas.fail_class=<类>` | 步骤失败消息 | **真实失败**，进 Allure 失败列表 |
| `env_blocked` | `skipped` | `atlas.fail_class=env_blocked`、`atlas.blocked=true` | 环境/产品/护栏原因 | 环境终态；**如实可见不抹平** |
| `interrupted` | `skipped` | `atlas.fail_class=interrupted`、`atlas.interrupted=true` | "operation interrupted" | 终止信号，非缺陷 |
| `unknown` | `broken` | `atlas.fail_class=unknown` | 消息/trace | 无法归因，按"异常"呈现 |
| 步骤被 `on_env_blocked: skip` 跳过 | `skipped` | `atlas.fail_class=env_blocked` | "步骤被环境跳过" | 明确不是 PASSED |

**防呆说明（回应红线 #7"禁止把失败改 SKIP"）**：本映射**不是**把断言/产品失败洗成跳过。Atlas 在 `engine`/`reporting` 层早就把 `env_blocked`、`interrupted` 与真实 `failed` 分开（`#114`）；映射只是**忠实翻译既有区分**，且用 label + message 双重保留 `fail_class`，任何过滤/审阅都能一眼区分"环境挡的"与"真绿"。`status=failed` 的仍全部落 `failed`，不被吞。**报告层绝不新增"降级为通过"的路径。**

---

## 4. 汇入方案（parse：Allure 2.39.0 → Atlas 分类）

对应手册 `scs-failed-tests-analysis`。目标是：拿一份 **Allure 2.39.0 结果集**（Atlas 自产的，或同事 SCS 框架产的），把失败用例**解析→分类→给修复提案/进塔**。

### 4.1 模块设计（同 `allure_report.py` 或新增 `allure_analysis.py`）

```python
@dataclass
class AllureFinding:
    name: str; full_name: str; status: str
    message: str; trace: str
    labels: dict[str, str]
    fail_class: str            # Atlas FailureClass.value
    evidence: dict             # {attachments:[...], env, case, ...}

def load_allure_results(results_dir: Path) -> list[AllureFinding]:
    """读结果集目录的所有 *-result.json。

    #131/#112 读侧纪律：单文件坏字节 replace 回落、单文件坏 JSON/非对象跳过并计数，
    绝不因一条坏结果整份分析崩（这正是"感知器官坏了却读成无失败"的假阴性温床）。
    """

def parse_allure_result(result: dict) -> AllureFinding:
    """单条 Allure result → 分类结果。若带 atlas.fail_class label 直接采用（自产往返）；
    否则从头回的 message/trace 文本经 failures.classify_text() 归类（外部 SCS 结果）。"""

def analyze_allure(results_dir: Path, *, knowledge=None, to_tower=None) -> dict:
    """汇总：总数/通过/失败/跳过 + 按 fail_class 分布 + 四桶（§6.3）+ 命中坑点 + 自愈提案。
    to_tower=ExperienceJournal(...) 时把未命中项登记为 pending_review 草稿（进同一审核塔）。
    """
```

### 4.2 `failures.classify_text` —— 单一真相重构（关键）

现有 `classify_failure(exc)` = 取 `exception_text(exc)` → 按 `_RULES`/`_DATA_ABSENT` 关键词归类。Allure 解析拿到的是**文本**（`statusDetails.message`），不是异常对象。若在分析模块里**另写一套关键词匹配**，就重演 `#92/#127` 的"改一漏一必漂移"。

**设计**：在 `failures.py` 抽取

```python
def classify_text(text: str) -> FailureClass:
    """异常文本 → FailureClass 的单一真相；classify_failure 与 Allure 解析共用。
    classify_failure(exc) = _classify_dispatch(exception_text(exc)) 的内部实现下沉到此处。
    控制流判定仍由 classify_failure 在调用 classify_text 之前用 isinstance(CONTROL_FLOW) 短路。"""
```

- `classify_failure` 改成"控制流短路 + `classify_text(exception_text(exc))`"。
- Allure 解析调用 `classify_text(f"{message} {trace}".lower())`。
- 回归测钉死两条路径对同一文本给同一类。

### 4.3 与 `healing` / `review` 的衔接

```python
# 对每个 failed/broken finding：
hit = knowledge.match_pitfall_from_text(finding.message, status="approved")  # 需新增文本版（见下）
if hit and hit.get("remedy"):
    proposal = {"type": "heal_proposal", "case": finding.name,
                "pitfall_id": hit["id"], "remedy": hit["remedy"]}
else:
    journal.append_draft(journal.draft_from_text(...))   # 未命中 → pending_review 草稿进塔
```

需要两个小的、**面向文本**的适配（保持单一真相）：
- `knowledge.match_pitfall_from_text(text, domain="", status=None)`：现有 `match_pitfall(exc)` 内部逻辑是 `exception_text(exc)` + 关键词匹配——抽出文本版，`match_pitfall` 委托它（同 §4.2 的收敛手法）。
- `ExperienceJournal.draft_from_text(*, task, text, failure_class, ...)`：从文本造草稿，`draft(exc)` 委托它。

**铁律不变**：
- 自愈提案**只提案不执行**；执行仍走 `healer.can_auto_heal`（只读 Task + `status="approved"` 命中 + 非控制流，`#108`）。Allure 分析**不得**成为自动改代码/放宽断言的旁路。
- 未命中一律 `pending_review` 草稿进**同一个** `atlas review` 塔（kind=`pitfall`）——不新开塔、不新增知识写路径（红线 #9/#10）。

### 4.4 CLI

```bash
atlas allure-analyze reports/allure-results                 # 只读分析，打印分类汇总（JSON）
atlas allure-analyze reports/allure-results --to-tower      # 未命中项登记 pending_review 进审核塔
atlas allure-analyze reports/allure-results --format text   # 人读版
```

- 复用 `cli._user_error`（rc 2）；`--to-tower` 仍走 `ExperienceJournal`，人审仍走 `atlas review`。

---

## 5. 与现有模块的衔接点（汇总）

| 模块 | 衔接 | 纪律 |
|---|---|---|
| `reporting.py` | 新模块读 `runs.jsonl` 复用其容错读法；`summarize` 供 CLI/报告摘要 | 不重写一套裸读；坏行计数上报（`#112/#131`） |
| `failures.py` | 新增 `classify_text` 单一真相；`classify_failure` 委托它 | 不动 `_RULES`/`_DATA_ABSENT` 语义；控制流短路保留 |
| `knowledge.py` | 新增 `match_pitfall_from_text`；`match_pitfall(exc)` 委托 | 自愈准入仍 `status="approved"`；空 trigger 永不匹配（`#59`） |
| `experience.py` | 新增 `draft_from_text`；`draft(exc)` 委托 | 只造 `pending_review`；同 id 合并 seen（`#118` 证据刷新保留） |
| `healing.py` | 消费 Allure 命中 → 提案（不执行） | 控制流一律拒（`#108`） |
| `review.py` | Allure 草稿走既有塔；`evidence_card` 可加 `来源=allure` 行 | 不新增 kind 冲突；approve 分发不变 |
| `engine.py` | 失败时可选落 Allure 附件（截图/证据）到结果集 | 成功步骤不默认截图（红线 #8），只在 debug/heal/失败取证 |
| `cli.py` | 新增 `allure`、`allure-analyze` 子命令 | rc 2 用户错误；默认输出 `reports/` |

---

## 6. 失败分类口径

### 6.1 映射关系

```
Allure status=passed            → 无 finding
Allure status=failed/broken     → 走 classify_text(message+trace) 或 atlas.fail_class label
Allure status=skipped           → 读 atlas.fail_class label 区分 env_blocked / interrupted / 用户 skip
```

### 6.2 优先级

沿用 `classify_failure` 的既有优先级（**不得改动**）：控制流短路 → env/session/contract（前三类）→ data 精确信号 → assertion/interaction → unknown。

### 6.3 四大失败类型映射（口径待与手册确认）

手册 `scs-failed-tests-analysis` 提"四大失败类型"但抽取文本未给出定义。基于 Atlas 现有 8 类，建议聚成 4 桶（**可配置分组**，不写死进引擎——红线 #11）：

| 桶（建议名） | 归入的 Atlas class | 处置建议 |
|---|---|---|
| **环境/阻塞** | `env_blocked`, `interrupted` | 报环境/产品；不伪装 PASSED；生产护栏拒绝如实标注 |
| **数据问题** | `data` | 换真实数据或 `find_or_create`；**禁止**步骤内兜底（红线 #6） |
| **用例/契约问题** | `assertion`, `contract` | 核对断言是否过严/契约是否失配；**禁止**为变绿放宽断言（红线 #7） |
| **交互/会话问题** | `interaction`, `session` | 定位/等待/会话 Cookie；命中已审知识可提案自愈 |
| （兜底）未知 | `unknown` | 交人审，登记草稿 |

> ⚠️ 该 4 桶是**分析视图**，不是对 `FailureClass` 的改动；分组表放插件知识/配置（`ATLAS_*`/私有目录），引擎不含业务口径。

---

## 7. 红线遵守清单

- **不放宽断言**：报告层与解析层都只做"翻译/归类"，无任何改断言路径；`assertion` 失败恒落 Allure `failed`。
- **不掩盖终态**：`env_blocked`/`interrupted` 映射为 `skipped` 但携带 `atlas.fail_class` label + message；不新增"降级为通过"。
- **不新开知识写路径**：Allure 分析产出只进 `pending_review` 草稿 → 既有 `atlas review` 塔（红线 #9/#10）。
- **不引入运行时依赖**：默认纯 stdlib；`allure-pytest` 需显式开关并文档标注（AGENTS 红线 #4）。
- **离线可测**：全流程不依赖真实网络/浏览器/凭据（AGENTS §2）。
- **生产安全**：不因报告集成放宽 `validate_write`、不自动建单、不绕过 agent 风险阈值。

---

## 8. 测试计划（离线，`tests/test_allure_report.py` + `tests/test_allure_analysis.py`）

**产出侧**
1. 单 run（passed）→ 1 个 `*-result.json`，`status=passed`，steps 数一致，`historyId` 稳定。
2. 混合 run：断言失败步骤 → `status=failed` + `atlas.fail_class=assertion`。
3. `env_blocked` run → `status=skipped` 且**带** `atlas.blocked=true` label（**绝不进 failed 也不静默绿**）。
4. `interrupted` run → `status=skipped` + `atlas.interrupted=true`。
5. `unknown` → `status=broken`。
6. `historyId` 跨两次运行同 case 相同（趋势聚合）。
7. `clean=True` 只清 Atlas 前缀文件，不误删用户放在目录里的附件。
8. 附件源文件缺失/坏字节 → 生成占位附件，不崩。
9. 读侧容错：`runs.jsonl` 尾行腰斩坏字节 → 不整读崩，合法 run 照常产出（复用 `#131` 纪律）。
10. CLI e2e：`atlas allure <tmp>/runs.jsonl --out <tmp>/allure-results` rc0；坏路径 rc2。

**汇入侧**
11. 解析自产结果集 → `fail_class` 与原始一致（往返一致）。
12. 外部 Allure 结果（仅 message/trace）→ `classify_text` 正确归类（与 `classify_failure` 同文本同结果，防 `#92` 漂移）。
13. `--to-tower`：未命中项进 `experience_drafts.jsonl` 且 `status=pending_review`；命中项产出 heal 提案但**不执行**。
14. 控制流：一条 `KeyboardInterrupt` 文本的 Allure 结果**不得**产出可自愈提案（`#108`）。
15. 结果集里混入坏 JSON / 非对象 / 腰斩文件 → 跳过计数，好结果照常分析（`#112/#131`）。
16. 空结果集 → 返回空汇总而非崩。

门禁：`PYTHONPATH=src python -m pytest -q`（现有 533 passed 基线不回归）＋ `compileall`。

---

## 9. 分阶段实施

1. **P1（必需）**：`allure_report.emit_allure` + `atlas allure` CLI + §8 测试 1–10。
2. **P2（必需）**：`failures.classify_text` / `knowledge.match_pitfall_from_text` / `experience.draft_from_text` 三处单一真相收敛 + 回归测（先收敛再加消费点）。
3. **P3（必需）**：`allure_analysis` 解析 + `atlas allure-analyze` + `--to-tower` + §8 测试 11–16。
4. **P4（可选）**：`--via-pytest`（`allure-pytest` 适配），文档标注额外依赖。
5. **P5（可选）**：engine 失败取证落 Allure 附件。

> 文档同步（AGENTS §5）：落地后更新 `README.md`（新命令）、`CLAUDE.md`（关键模块表加 `allure_report.py`）、`AGENTS.md`（若新增验证门禁）。

---

## 10. 待确认 / 风险

- **四大失败类型确切定义**：需回查手册 2.5 原文（当前抽取文本缺失）。本方案给出映射草案，落地前以手册为准。
- **`--via-pytest` 依赖**：`allure-pytest` + `allure` CLI 属新增运行时——按 AGENTS 红线 #4，需先问、默认关闭。
- **`historyId` 业务标识**：`env|case` 是否够稳（同 case 多 BU？）——若 BU 只经坐标不分支（租户=坐标），则 `env|case` 足够；否则加入 `bu` 维度。
- **附件体积**：失败截图入结果集会让目录膨胀——默认只对 `debug/heal/失败` 取证（红线 #8），`run` 模式不产附件。

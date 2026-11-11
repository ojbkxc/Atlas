# Gap 6（Allure 兼容）：产出侧对齐 UI AUTO TEST + 消费侧评审

> 状态：产出侧已落地（`src/atlas/allure_report.py` + `tests/test_allure_report.py`）；
> 消费侧结论：**不新建能力，只补评审结论（已可用的部分保留，不做扩张）**。
> 负责：allure-dev（team atlas-4caps）｜关联：`allure_report.py` / `allure_analysis.py` / `cli.py`
> 目标读者：Atlas 维护者。

---

## 0. 一句话结论

1. **产出侧**：Atlas 直出的结果集在「失败截图渲染 / 步骤树 / label / 结果集组成部分（categories /
   environment / executor）」四类与 UI AUTO TEST 有差距，已全部补齐，且全部为**附加 + 可选加入**，
   存量行为逐字不变（`align=False` / `metadata=False` 逃生舱）。
2. **消费侧**：Atlas **已经有能力** 解析 UI AUTO TEST（allure-pytest）产出的 `*-result.json`
   （`allure_analysis.load_allure_results` / `parse_allure_result` / `analyze_allure` 本就按外部结果集
   设计，`atlas.fail_class` label 缺失时回落文本归类）。**再新增能力没有性价比**——不为它做新
   parser/新 CLI 分支；保持现状 + 已知口径在文档里讲清楚即可。

---

## 1. 取证基线（真实产物，不是推测）

参考对象：`D:\GitHub\UI AUTO TEST\iservice\reports\`

### 1.1 allure-pytest 产出的 `allure-results/*-result.json`（17 个真实文件）

| 字段 | 出现率 | 说明 |
|---|---|---|
| `name` / `status` / `start` / `stop` / `uuid` / `historyId` / `testCaseId` / `fullName` | 17/17 | 恒有 |
| `description` | 17/17 | 用例说明（中文业务描述），Atlas 旧直出**没有** |
| `titlePath` | 17/17 | `["testcases","sit_cvt_regression","test_sit_cvt_regression.py","TestSitCvtRegressionPlan"]`，Atlas 旧直出**没有** |
| `labels` | 17/17 | 见下表 |
| `statusDetails` | 12/17 | 仅失败/跳过有 |
| `steps` + `attachments` | 9/17 | 步骤树 + 步骤级截图 |

**label 全集（17/17 恒有）**：`severity` / `parentSuite` / `suite` / `subSuite` / `host` / `thread` /
`framework` / `language` / `package`。

**步骤级附件真实形态**（`runner.py` 第 300–360 行 `allure.attach(page.screenshot(), attachment_type=PNG)`）：

```json
{"name": "step1_result",
 "source": "00f0ff9b-...-attachment.png",
 "type": "image/png"}
```

即 **步骤级** `attachments[]`、`type=image/png`、`source` 指向同目录已落盘的图片字节。

### 1.2 UI AUTO TEST 报告目录 `allure-report/`（CLI 渲染）

- `data/categories.json` = `{"children": []}`（未配置分类）；`widgets/categories.json` = 0 项。
- `widgets/environment.json` = `[]`（无 `environment.properties`）；`widgets/executors.json` = `[]`
  （无 `executor.json`）。
- `data/test-cases/*.json` 含 `description` / `titlePath` 派生的层级。

> 关键判断：**UI AUTO TEST 自己也没配 categories/environment/executor**（组件空）。
> 「对齐 UI AUTO TEST」不等于「照抄它的空组件」——Atlas 的 categories 是本项目独有的价值
> （按 `fail_class` 分桶，红线 #7 非缺陷终态单列），environment 从 runs 派生 `env`，都不是多余的。

### 1.3 Atlas 旧直出（修复前）差距

| 维度 | Atlas 旧 | UI AUTO TEST / Allure 期望 | 缺口性质 |
|---|---|---|---|
| 失败截图 | step/runs 记录 `attachment` → 复制进结果集，`type` 按后缀推（含 image/png），**源缺失降级 text/plain 占位** | 步骤级 `attachments[]` + `image/png` + `source` 落盘 | **已闭环**（#139 已接线，Gap6 补「可渲染」回归测试） |
| 步骤树 | `name/status/stage/start/stop/steps/parameters/attachments/statusDetails/labels` | 同 | **已保真**（Gap6 补时序/不重叠断言） |
| label | 只有 `suite=atlas` + `severity` + `atlas.*` | `parentSuite/suite/subSuite/package/framework/language/severity` | **差 5 个 label** |
| 结果字段 | 无 `description` / `titlePath` | 恒有 | **差 2 个字段** |
| categories.json | 无 | Allure 可选组件（UI AUTO TEST 也没配） | 缺失 |
| environment.properties | 无 | Allure 可选组件 | 缺失 |
| executor.json | 无 | Allure 可选组件 | 缺失（且无自然来源） |

---

## 2. 产出侧修复说明（已落地，全部在 `allure_report.py`）

### 2.1 新函数（纯 stdlib，零新依赖）

| 函数 | 作用 |
|---|---|
| `_result_labels(env, status, extra, *, align)` | run 级 label 组装。`align=True` 追加 `parentSuite=Atlas / subSuite=<env> / package=atlas.<env> / framework=atlas / language=python`；**`suite=atlas` 与 `severity` 值不变**（零涟漪）。`host/thread` 是机器相关（hostname/线程名），**不伪造**——离线确定性输出不掺机器标识，需宿主标识的走 `executor=`。 |
| `default_categories()` | `fail_class → Allure categories.json` 分类。`messageRegex` 锚在**自产**的 `run failed: <fail_class>`（见 `_status_from_run`），不靠宽松词猜；`skipped` 单列「环境阻断/中断（非缺陷）」桶，绝不与真实缺陷混（红线 #7）。 |
| `write_categories(out_dir, categories)` | 写 `categories.json`（Allure 约定名，非 `atlas-*`，`clean` 不动它）。 |
| `write_environment_properties(out_dir, mapping)` | 写 `environment.properties`（`k=v` 行；值内换行折空格，空值键跳过）。 |
| `write_executor(out_dir, executor)` | 写 `executor.json`（无自然来源，纯 opt-in）。 |

### 2.2 `_run_to_result(..., *, align=True)`

- result.json 追加 `description`（取 run 的 `description`，缺失诚实留空，不伪造业务说明）与
  `titlePath`（`["Atlas", env]`）。
- **既有键（uuid/historyId/testCaseId/name/fullName/status/statusDetails/stage/start/stop/
  parameters/steps/attachments/links）一个不改一个不删。**
- `align=False` → 逐字旧形态（只 `suite+severity`、无 description/titlePath）。

### 2.3 `emit_allure(...)` 新增可选关键字（**全部有默认、向后兼容**）

```python
emit_allure(runs_path, out_dir, *, clean=True, attachments_dir=None,
            align=True, metadata=True, categories=None,
            environment=None, executor=None) -> dict
```

- `align` 透传 `_run_to_result`。
- `metadata=True`（默认）时：
  - `categories` 缺省 → `default_categories()`；传 `[]` → 写空数组（不静默塞默认）。
  - `environment` 缺省 → 从各 run 的 `env` 去重排序派生 `{"env": "sit,uat"}`；传 `{}` → 跳过。
  - `executor` 非空才写 `executor.json`（不伪造）。
- 返回新增 `metadata_files`（本次实际写的元文件名清单）——**追加键，旧键不变**。
- `metadata=False` → 只产 result.json（旧目录除 `atlas-*` 外零改动）。

### 2.4 零涟漪证据

- `align` / `metadata` 默认值让**正常路径多产 3 个附加文件 + 若干附加字段**，但：
  - 现有 47 条 `test_allure_report.py` 测试全绿（含 C6 三条只在 `parameters`/`labels` 上的断言，
    新 label 是追加、不抢占 `suite/severity/atlas.*`）。
  - `test_versioning.py`（直接调 `_run_to_result`）与 `test_allure_analysis.py`（emit→parse 往返）
    全绿：往返时 `atlas.fail_class` label 与 statusDetails 优先级不变。
  - `align=False` / `metadata=False` 提供逐字回退。

### 2.5 新增回归测试（`tests/test_allure_report.py`）

| 测试 | 钉什么 |
|---|---|
| `test_aligned_result_matches_ui_auto_test_label_set` | label 覆盖 UI AUTO TEST 确定性子集，`suite/severity` 值不变，`host/thread` 不伪造 |
| `test_aligned_result_carries_description_and_title_path` | description/titlePath 附加；无 description 诚实留空 |
| `test_align_false_preserves_legacy_result_shape` | 逃生舱：逐字旧 result.json |
| `test_step_level_png_attachment_renders_as_image` | 步骤级 `image/png` + 真实字节落盘（不降级占位） |
| `test_step_tree_has_name_status_and_timing` | 步骤 name/status/start/stop 保真、按累计时长推进不重叠 |
| `test_metadata_writes_categories_and_environment` | categories + environment 落盘，env 去重排序 |
| `test_default_categories_match_atlas_fail_class_messages` | 分类 messageRegex 命中正确桶（断言/契约、数据、会话、broken、skipped） |
| `test_categories_override_and_empty` | `categories=` 覆盖、`[]` 写空 |
| `test_metadata_false_writes_no_extra_files` | 逃生舱：不写附加文件 |
| `test_executor_json_is_opt_in` | executor 不传不写、传了才写 |
| `test_environment_properties_escapes_newlines` | 换行折空格、空值跳过 |
| `test_write_categories_and_executor_standalone` | 独立写函数可用 |

---

## 3. 消费侧评审（Atlas 读 UI AUTO TEST 的 allure-results）

### 3.1 已有能力（先确认，不重复造）

`allure_analysis.py` 已实现完整汇入链：

- `load_allure_results(dir)`：读目录所有 `*-result.json`，坏字节 replace、坏 JSON/非对象跳过计数。
- `parse_allure_result(result)`：**label 有 `atlas.fail_class` 直接用（自产往返）；否则回落
  `failures.classify_text(message+trace)`**——这正是为「外部工具（含 UI AUTO TEST）产出」设计的路径。
- `analyze_allure(...)`：status 计数 + fail_class 分布 + 四桶 + 坑点匹配 + 自愈提案 + `--to-tower` 草稿。
- CLI：`atlas allure-analyze <dir> [--to-tower] [--format text]`。

> 结论：**消费 UI AUTO TEST 结果集不需要新 parser**。`parse_allure_result` 对 UI AUTO TEST 的真实
> `statusDetails`（`Skipped: ...` / `AssertionError ...`）走文本归类，能进 `FailureClass`。

### 3.2 真实需求场景评估

| 场景 | 价值 | 现能力够不够 | 新增成本 |
|---|---|---|---|
| **迁移期混合报告**（旧 suite allure-pytest + Atlas 同目录/同报告对比） | 中高：迁移验收期需要「旧用例结果 vs Atlas 结果」并列看 | **够**：两个结果集目录分别 `atlas allure-analyze`，汇总可比 | 0 |
| **跨工具趋势聚合**（同一用例 allure-pytest 与 Atlas 两套 historyId 合并趋势） | 低-中：两工具 `historyId` 生成规则不同（Atlas=sha1(env\|case)，allure-pytest=方法全名 md5），无法自然合并到 Allure 趋势 `historyId` | 不强求：Allure 趋势按 historyId 分组，跨工具本就不是同一 test identity | 高（要伪造映射，违背「不猜」） |
| **失败的统一分类/进塔**（UI AUTO TEST 失败也进 Atlas 审核塔） | 中：`--to-tower` 已支持外部结果 | **够**：草稿带 C2 verification 三元组（command=`atlas allure-analyze <dir>`，verdict 由 status 派生） | 0 |
| **在 CI 里「先收旧报告再分析」** | 低：多一步人工/脚本触发 | 够（CLI 已有） | 0 |

### 3.3 明确结论：**不做扩张，保持现状**

- **做**：不新增任何消费侧代码。`allure_analysis.load_allure_results / parse_allure_result /
  analyze_allure / atlas allure-analyze` 原样即能读 UI AUTO TEST 结果集（label 缺失回落文本归类，
  这正是「外部结果」设计）。
- **不做**：
  1. **不新增 `--from-ui-auto-test` 之类的专用分支**——无专用 schema，UI AUTO TEST 就是标准
     allure-pytest 结果集，加专用开关等于为某个工具名做耦合（红线 #11 同族：引擎不含具体业务/工具名）。
  2. **不做跨工具 historyId 映射**——两工具 test identity 规则不同，硬映射=伪造身份，且趋势图语义
     被污染（红线：不猜）。
  3. **不新建第二个 parser**——`allure_analysis._load` 已是唯一读侧入口（#92 单一真相）。
- **已知口径（写清楚免得误判）**：
  - UI AUTO TEST 的 `steps[].attachments` 在 Atlas 消费侧**不进 evidence**：`parse_allure_result`
    只收 result 级 `attachments[]`（步骤级附件当前不采集）。若将来要「按步骤定位旧用例失败截图」，
    这是唯一值得单独做的增量——但当前无真实需求方，**按红线 #12 不给零使用需求加投机功能**，
    列入遗留项。
  - env 提取：`parse_allure_result` 从 `parameters` 找 `name=="env"`；allure-pytest 不产 env
    parameter，故外部结果 `evidence["env"]` 为空——这不影响分类，只影响汇总里的 env 展示。

### 3.4 如果将来要做的触发条件（写清楚，避免反复讨论）

做「步骤级附件采集」，当且仅当出现：**(a)** 有人真要在 Atlas 里按 UI AUTO TEST 的步骤号定位截图，
且 **(b)** 现有「整条结果只带 result 级附件」不足。届时增量应扩 `parse_allure_result` 的 evidence
（把 `steps[].attachments` 递归展平），而不是新开路径。

---

## 4. 遗留项 / 已知边界

1. **host/thread 不伪造**：hostname/线程名机器相关，离线确定性输出不掺；需宿主标识请用
   `emit_allure(executor={...})` 写入 executor.json（Allure Executors 组件）。
2. **`categories.json` / `environment.properties` / `executor.json` 是附加产物**：UI AUTO TEST 自己
   也没配（报告里组件为空）。Atlas 补默认值（categories 按 fail_class、environment 派生 env），
   但**渲染前请确认 Allure CLI 版本 2.39.0**（旧版对 categories messageRegex 格式略有差异）。
3. **`suite=atlas` 值未改**：对齐只追加 `parentSuite/subSuite/package/framework/language`，不改历史
   `suite`——避免存量 Allure 目录/历史趋势里 `suite` 分组断线。若将来要并入 UI AUTO TEST 的
   `parentSuite/suite/subSuite` 真实值（如 `testcases.xxx / test_xxx / TestClass`），应通过
   runs.jsonl 侧提供来源字段，而不是引擎里硬编码。
4. **消费侧步骤级附件未采集**（见 3.3 已知口径 + 3.4 触发条件）。
5. **`docs/gaps/` 编号**：`gap6-pi-agent-desktop.md` 占用 Gap6，`gap7-regression-code-eval.md` 占用
   Gap7，故本 Allure 兼容波次最终落为 **Gap8**（lead 已改名为 `gap8-allure-compat.md`）。
# wave4 B 轴 ae-6：产物可读性逐文件评审（只审不改）

> 提案口径：「后续波次补：逐文件评审产物可读性」。
> 状态：**评审波——零代码改动**，全部建议留待 lead 排期后实施。
> 评审对象：`src/atlas/generator/emit_pytest.py`（1416 行）与 `src/atlas/generator/emit_fast.py`（1508 行）的**产物侧质量**——页对象库模板、`_atlas_support.py` / `_atlas_fast.py` 底座模板文本（`_SUPPORT_SOURCE` / `_FAST_SUPPORT_SOURCE` / `_FAST_CONFTEST_SOURCE`）、产物 README 模板的注释与结构。
> 取证方式：① 源码精读（模板正文 + 渲染函数）；② **实生成一份快包**（`examples/atlas_portable_smoke.yaml` → `atlas export-pytest` 同路径 API 直调，零凭据、离线、只生成不执行）后目检 6 个产物文件；③ 样例文案与产物口径交叉核对。生成物评审后即删（`exports/` 在 gitignore 内）。
> 行号基准：HEAD `a4feb3c`（2026-11-15）。模板被锁步测钉字节级，行号漂移风险低，但复核时仍以当日源码为准。

## 产物文件 → 模板出处对照

| 产物文件 | 模板出处 | 评审评级 |
|---|---|---|
| `test_<id>.py` | `emit_pytest.py` `render_test_module` / `_render_api_test*` / `_render_ui_test*`（304–576） | **好** |
| `_atlas_support.py` | `emit_pytest.py` `_SUPPORT_SOURCE`（583–687） | **好** |
| `atlas_pages.py` | `emit_pytest.py` `emit_pages_module` 及 helpers（700–1113） | **一般** |
| `_atlas_fast.py` | `emit_fast.py` `_FAST_SUPPORT_SOURCE`（41–964） | **一般**（双面） |
| `conftest.py` | `emit_fast.py` `_FAST_CONFTEST_SOURCE`（971–1427） | **好** |
| `README.md` | `emit_pytest.py` `_build_readme`（1332–1416） | **差**（问题最集中） |

---

## 1. test_<id>.py —— 好

**好的方面**：

- 每个用例 docstring 自带定位信息：快形态带 `[METHOD path]`（`emit_pytest.py:311`）或 `[task=…]`（`emit_pytest.py:446`），慢形态同构（`emit_pytest.py:351`、`emit_pytest.py:482`）。
- 写操作前置标签 `[写操作-需人工确认]` 进 docstring（`emit_pytest.py:307`、`emit_pytest.py:350`），读产物的人第一眼知道哪条会真发写。
- 存活断言消息三要素（`status=`/`err=`/`body[:200]`）带上下文（`emit_pytest.py:325-326`、`emit_pytest.py:361-362`）；`TODO(人审)` 显式留位不伪装完成（`emit_pytest.py:338`、`emit_pytest.py:373`）。
- 快形态 step 名 `"assert ok"`/`"assert resultCode"` 与 `allure-analyze` 的断言计数口径对齐（`emit_pytest.py:324`、`emit_pytest.py:330`）；`require_ctx` 前置缺失显式终止（`emit_pytest.py:313-314`、`emit_pytest.py:448-449`）。
- 快形态模块 docstring 主动声明写闸语义（`emit_pytest.py:505-506`）。

**具体建议（轻微）**：

- 快形态模块 docstring 第 7 行单句超 200 字符不折行（`emit_pytest.py:510-514`，渲染进产物即一行超长）；慢形态（`emit_pytest.py:551-557`）更短。建议拆成列表（渲染层代价：产物字节变化，需同步快照测）。

## 2. _atlas_support.py —— 好（基线）

**好的方面**：仅标准库 + 红线 #4 声明（`emit_pytest.py:585`）；`${VAR}` 未解析显式报错（`emit_pytest.py:622-639`）；缺配置走 `EnvBlocked` 点名、绝不漏 urllib 裸 `ValueError`（`emit_pytest.py:651-657`）；body 分流注释与 DSL 同口径（`emit_pytest.py:644`）。

**具体建议（轻微）**：

- 网络层异常被 `except Exception` 吸收为 `StepResult(ok=False, …)`（`emit_pytest.py:680-681`）——语义正确（存活断言会归因），但产物读者看到大捕会疑惑「为什么不抛」。`call_api` docstring（`emit_pytest.py:644`）只讲了 body 分流，未注这条路径。建议补一句「网络层故障不裸崩，由存活断言归因（err 字段带类型名）」。

## 3. atlas_pages.py —— 一般

**好的方面**：类 docstring 带组件库 `notes`（`emit_pytest.py:1039`、`emit_pytest.py:1095`，实测渲染为组件用途说明）；`locator()` 未知名点名已知集合（`emit_pytest.py:909-911`）；`NotWired` 消息点名组件与元素（`emit_pytest.py:842`）；选择器常量 + `SELECTORS` 映射双形态清晰（`emit_pytest.py:886-894`）。

**具体建议**：

1. **`models` 键只以注释形态进产物，无任何消费方说明（差）**：`emit_pytest.py:1043` / `emit_pytest.py:1099` 把组件库 `models` 渲染成 `# models: {…}` 单行注释——数据进了产物，但产物读者无法判断：改操作代码要不要跟着改 models？models 谁消费？（实测：demo 组件库带 6 处 models 注释，全部裸注释。）建议后续波二选一：docstring 声明「models 是给接线人的参考，当前无引擎消费方」，或直接不渲染该键。
2. **产物内无「形态标记」（一般）**：同一模板出快/慢两种形态（`emit_pytest.py:1061-1113`），产物文件自身没有一行标记自己是哪种——读者拿到 `atlas_pages.py` 需靠 `NotWired` vs `NotImplementedError` 反推（`emit_pytest.py:825-842` vs `emit_pytest.py:1109-1110`）。建议模块 docstring 首行加形态标记（生成器渲染一行常量即可）。
3. **`__all__` 单行超长（一般）**：`emit_pytest.py:1030` / `emit_pytest.py:1086`——实测 demo 组件库渲染后快形态 `__all__` 行 164 字符、慢形态更短，均不折行。建议折行（`_py_literal` 对列表已输出单行，可生成时按行宽折）。
4. **`locator()` 两分支同构复制（一般）**：CSS 纯组件与含结构化描述两分支各有一份 KeyError 消息（`emit_pytest.py:909-911` 与 `emit_pytest.py:921-923` 字面相同）。生成器内这是有意的分支（不引 `atlas.locators` 到产物），可接受；但产物文本层面读者会看到两份同构实现，改一处时不知道还有一处。建议在产物分支里加一行注释「两形态同判据，勿只改其一」。

## 4. _atlas_fast.py —— 一般（双面：治理可追溯 vs 产物读者噪音）

**好的方面**：每个判据函数三段式 docstring（行为 + 判据 + 失败面），如 `session_channel`（`emit_fast.py:196-203`）、`trace_enabled`（`emit_fast.py:358-367`）、`safe_persist`（`emit_fast.py:821-829`）；锁步引用精确到上游文件与测（`emit_fast.py:44-46`、`emit_fast.py:95-102`）；常量命名自带语义（`TICK_MS`/`RUN_STATUS_VOCAB`/`SCALAR_MAX_LEN`）。

**具体建议**：

1. **治理注记密度过高且面向维护者而非产物读者（差）**：产物模板 924 行里 `#92`/`F-x`/`锁步`/`红线 #x` 类内部治理引用 40+ 处（抽样：`emit_fast.py:53-56`、`emit_fast.py:79-83`、`emit_fast.py:139-149`、`emit_fast.py:215-219`、`emit_fast.py:778-786`、`emit_fast.py:826-829`）。接线人（产物的真实读者）真正需要的操作导引——「我要改等待/取值判据改哪里、改完会怎样、红线边界在哪」——被治理注记淹没。建议后续波把注释分两层：产物读者视角的说明保留，治理注记（锁步测名、#92 编号、F-x 裁决号）收敛进模块 docstring 的一张对照表。
2. **分隔线两种风格并存且部分块无导航（一般）**：块分隔线两种写法混用——`# ──`（Unicode 双线）在 `emit_fast.py:79-80`、`emit_fast.py:180`、`emit_fast.py:224`，`# ---`（ASCII 三线）在 `emit_fast.py:283-285`、`emit_fast.py:343-345`、`emit_fast.py:541-543`、`emit_fast.py:722-728`、`emit_fast.py:806-808`、`emit_fast.py:944-946`；且终态词汇（79–105）、会话通道（180–221）等块内部再无子导航。924 行无目录性注释。建议统一分隔线风格并加一节「本文件结构」注释。
3. **runs.jsonl 键清单缺位（一般）**：`RunWriter` docstring 讲了 schema 对齐纪律（`emit_fast.py:433-443`），但产物读者最常用的「跑完看哪些键、值是什么形态」没有快照（一行 JSON 示例即够）。建议在模块 docstring 附一行示例运行行。

## 5. conftest.py —— 好（全产物最佳文档）

**好的方面**：顶部 docstring 覆盖全部机制 + 全量 env 旋钮表 + 两义分立 + 红线（`emit_fast.py:971-1008`，实测渲染 34 行）；fixture 命名自解释（`sole_page`/`get_page`/`plan_ctx`/`_fast_runlog`/`_case_watchdog`/`_write_gate`，清单在 `emit_fast.py:1440-1441`）；每个 fixture docstring 带失败面与下一步分立（`emit_fast.py:1262-1268`）。

**具体建议（轻微）**：

1. **`__` 前缀过滤约定只活在代码里（一般）**：`plan_ctx` fixture 收尾按 `__` 前缀过滤私有键（`emit_fast.py:1251`），但 fixture docstring（`emit_fast.py:1246-1248`）未提；而底座 `_logged_in_account` 用单下划线（`emit_fast.py:1311`）会落盘。产物读者想存私有键时无从得知这个约定。建议补一句注释。
2. **`sole_page` 单函数 100+ 行（一般）**：attach/launch 双通道 + 清理链交织（`emit_fast.py:1123-1225`），局部缩写变量（`browser_ch`/`browser_fam`，`emit_fast.py:1157-1158`）在长函数里降低可扫读性。建议后续波拆 `_launch_channel()` / `_attach_channel()` 两个私有函数（行为不变，纯可读性重构）。
3. **docstring 指向产物内不存在的符号（差）**：conftest 顶部 docstring 写「运行期 env 旋钮（全量以 `_atlas_fast.ENV_KEYS` 为准…）」（`emit_fast.py:996`，实测渲染进产物 `conftest.py` 第 26 行），但 `ENV_KEYS` 是**生成器侧**常量（`emit_fast.py:1449-1468`），产物 `_atlas_fast.py` 里没有这个符号（已实测 grep 产物零命中）。读者照这句话去找 `_atlas_fast.ENV_KEYS` 会落空。建议：产物内改为「全量清单见本文件顶部列表（生成器侧自证测见 emit_fast.ENV_KEYS）」。

## 6. 产物 README.md —— 差（本轮问题最集中，按严重度排序）

**好的方面**：人审指引段是全文最佳实践（`emit_pytest.py:1373-1375`，三步带红线引用）；快模式机制段与并行段的代价边界写得诚实（`emit_pytest.py:1382-1387`、`emit_pytest.py:1411-1414`）；横幅传达「只生成不执行」。

**具体建议（按修复优先级）**：

1. **横幅嵌套加粗破裂（差）**：`_README_BANNER` 文案自带内层 `**只生成不执行**`（`emit_pytest.py:45-48`），外层又用 `> **横幅：{_README_BANNER}**` 包裹（`emit_pytest.py:1343`）——生成物第 5 行渲染破裂（实测：`> **横幅：本产物由 … 生成，**只生成不执行**：鉴权值…**`）。建议 banner 文案去内层 `**`，或外层不加粗。
2. **运行命令与意图不符（差）**：`emit_pytest.py:1356` 渲染 `pytest _atlas_support.py --pyargs -q  # 见 test_*.py`——`--pyargs` 配 .py 文件路径语义可疑，跑支持模块不是跑用例，注释把读者丢给文件名清单。建议改 `pytest -q`（或 `pytest test_<id>.py`）。
3. **两形态共用段在快包内失真（差）**：「未接线时以 skip 占位」（`emit_pytest.py:1353`）与「操作骨架为 NotImplementedError」（`emit_pytest.py:1366-1367`）是慢形态语义；快形态 UI 腿是 `NotWired`→env_blocked（`emit_pytest.py:1061-1113`、`emit_fast.py:112-124`）。同一 README 段两形态共用，快包读者读到的是反的。建议这两段按 `fast` 参数分叉渲染。
4. **指向仓库相对路径的死链（差）**：形态说明段写「评审结论见 `docs/gaps/gap7-regression-code-eval.md`」（`emit_pytest.py:1345`）——产物承诺可移植/脱离 Atlas 运行，产物机上该相对路径不存在。建议改为「见 Atlas 仓库 docs/gaps/…（不在产物机上可忽略）」或移除。
5. **入口名漏 `export-pytest`（一般）**：标题「# atlas gen pytest 产物（候选）」（`emit_pytest.py:1338`）与横幅（`emit_pytest.py:45-48`）只提 `atlas gen --with-pytest`；而 flow→包的主路径是 `atlas export-pytest`（`emit_pytest.py:1222-1231` docstring 自述两入口）。产物由 export-pytest 生成时自称「atlas gen 产物」失真。建议标题按来源写或列双入口。
6. **env 旋钮清单缺漏 + 指引空洞（一般）**：快模式段列 10 键（`emit_pytest.py:1391-1396`），对比 `ENV_KEYS` 全量 20 键（`emit_fast.py:1449-1468`）——attach 三键（`ATLAS_FAST_CDP`/`_INTERACTIVE`/`_ATTACH_MATCH`，gap9b 主打能力）与 `ATLAS_BROWSER_CHANNEL` 均不在列；且「详见底座」（`emit_pytest.py:1391`）指向的 `_atlas_fast.ENV_KEYS` 在产物里不存在（见第 5 节第 3 条）。建议：README 补全 20 键或复用 conftest docstring 的全量表。
7. **快包无文件清单段（一般）**：快包含 6 个文件（`test_*.py`/`_atlas_support.py`/`atlas_pages.py`/`_atlas_fast.py`/`conftest.py`/`README.md`，`emit_pytest.py:1321-1328`、`emit_fast.py:1479-1482`），README 未列——读者不知 `conftest.py`/`_atlas_fast.py` 是哪来的、各自管什么。建议补一节「本包文件清单」（一行一文件 + 一句话）。

## 7. 附带发现：样例文案与产物口径不一致（非生成器缺陷）

`examples/atlas_portable_smoke.yaml:21` 的 intent 写「产物里以 **requests** 直调」，但产物底座仅标准库（urllib，`emit_pytest.py:585`，红线 #4 禁引 requests）——产物 docstring 忠实渲染了这句失真文案（实测 `test_atlas_portable_smoke.py:19/21`），与产物 README「零新增运行时」口径自相矛盾。属样例文案过时，建议后续波改样例 intent（生成器无责）。

---

## 汇总

| 产物文件 | 评级 | 问题数 | 最高优先级 |
|---|---|---|---|
| `test_<id>.py` | 好 | 1（轻微） | P2 |
| `_atlas_support.py` | 好 | 1（轻微） | P2 |
| `atlas_pages.py` | 一般 | 4 | P2（models 注释数据） |
| `_atlas_fast.py` | 一般（双面） | 3 | P1（治理注记分层） |
| `conftest.py` | 好 | 3（含 1 差） | P1（ENV_KEYS 指向不存在符号） |
| `README.md` | 差 | 7 | P0（横幅/命令/两形态失真） |

**总体判断**：底座与 conftest 的判据文档质量高（红线与失败面写透）；问题集中在**产物 README**（读者第一接触面，7 条中 4 条是渲染即失真）与**治理注记的受众错配**（产物读者读到维护者注记）。本轮零代码改动，全部建议留待 lead 排期；P0 四条改动面都在 `_build_readme` 单函数内，实施成本小。

---

## 实施记录（2026-11-15，ae-6 改造，用户排期）

范围：README 7 条（四条 P0 + 三条 P1/P2）+ §7 样例文案。`_build_readme` / `_README_BANNER`
改写，`examples/atlas_portable_smoke.yaml` intent 修文案（"以 requests 直调" → "以标准库
urllib 直调，零第三方依赖"）。

| 条 | 处置 |
|---|---|
| P0-1 横幅嵌套加粗 | banner 文案去内层 `**`，外层改 `> **横幅**：…`（结构保留、不再嵌套） |
| P0-2 运行命令与意图不符 | `pytest _atlas_support.py --pyargs -q` → `pytest -q`（快/慢同） |
| P0-3 两形态共用段失真 | 运行段与页对象库段按 `fast` 分叉：慢=skip 占位/NotImplementedError，快=NotWired→env_blocked |
| P0-4 仓库相对路径死链 | 不再放小节标题；正文点名「Atlas 仓库 docs/gaps/gap7-…，产物机上没有该文件，读不到可忽略」 |
| P1-5 入口名漏 export-pytest | 标题改 `# Atlas pytest 产物（候选）`；横幅双入口点名 |
| P1-6 env 旋钮缺漏 + 死指针 | 保留常用项注记；新增「全量键清单」由生成器遍历 `emit_fast.ENV_KEYS` 渲染（键数 `len()` 实时算，不写死）；删除指向产物内不存在符号 `_atlas_fast.ENV_KEYS` 的表述 |
| P2-7 无文件清单 | 新增「## 本包文件」（快包含 `_atlas_fast.py`/`conftest.py`/运行期 `reports/runs.jsonl`；慢包不列快包文件；无组件库不列页对象库） |

判据测（`tests/test_emit_pytest.py` 七格）：横幅 `**` 恰 2 个；无 `--pyargs`；快慢分叉互斥；
死链注记在位；双入口名；遍历 `ENV_KEYS` 全键在位且键数同源；文件清单按形态分列。
`test_portable_sample_export.py` 补「`以 requests 直调` 不许回产物」。
快照表 `_FAST_OFF_BASELINE` 仅 `readme_pages`/`readme_nopages` 两项更新（其余 10 项未动，
理由=README 是两形态共同的可读性改造，lead 落笔）。

**未做（仍然留白）**：`_atlas_fast.py` 治理注记分层（P1）、conftest `ENV_KEYS` 死指针与
`sole_page` 拆分（P1）、`atlas_pages.py` 的 models 注释/形态标记（P2）、`_atlas_support.py`
网络层大捕注记（P2）——均不属 ae-6 的 README 面，留后续波次。

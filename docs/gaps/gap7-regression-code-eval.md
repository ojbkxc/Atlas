# Gap7：回归代码产出能力与形态评审

> 状态：评审完成，推荐形态已实现（`src/atlas/generator/emit_pytest.py`）。
> 关联：Gap3（`--with-pytest` 原标 P4 可选）本波次落地；Gap5（Allure）产出侧不重复实现。

## 0. 问题

用户要求 Atlas 也能产出 UI AUTO TEST 那种回归代码，并明确要评审
**哪种形态更好、更适合、执行起来更快**。候选三形态：

| # | 形态 | 组成 |
|---|---|---|
| 1 | pytest 用例为主 | 每用例 = `yaml`(数据) + `py`(`step_N` 实现) + `md`(坑点) 三件套 + `test_*.py` 计划文件 |
| 2 | pytest + pages.py 全套 | 形态 1 ＋ `lib/pages.py`（跨用例复用选择器/操作）＋ `lib/runner.py`（执行引擎） |
| 3 | 可脱离 Atlas 运行 | 反生成可独立跑的 pytest+pages 代码包（不装 Atlas 也能跑） |

参照实现：`D:\GitHub\UI AUTO TEST\iservice\`
—— `lib/pages.py`(1610 行页面对象库) / `lib/runner.py`(481 行执行引擎) /
`testcases/sit_cvt_regression/01_sales_order_default_pt/sales_order_default_pt.py`(14 步，逐步 `page.evaluate` + 固定 sleep)。

## 1. 三形态对比

| 维度 | 形态 1（pytest-only） | 形态 2（+pages.py） | 形态 3（可脱离） |
|---|---|---|---|
| **生成速度** | 快（只出 py+yaml+md） | 快（多一个共享库） | **最慢**（须 vendor 整个底座：runner/pages/login/conftest） |
| **单次运行速度** | 取决于写法；易复制慢写法 | **更快**（共享语义等待，可收口每步截图策略） | 同形态 2（自带底座） |
| **维护成本** | **最高**（每个用例各写一遍页面操作 → N 份漂移代码） | **低**（改一处选择器/等待，全用例受益） | 中（底座要跟着上游演进，可能滞后） |
| **Atlas 知识/组件复用** | 差（复制选择器，脱钩 components.json） | **最好**（页对象库从 `components.json` 派生，单一来源不漂移） | 中（生成那一刻复用，之后脱钩） |
| **脱离 Atlas 可移植** | 差（仍依赖 lib） | 中（依赖 Atlas 运行时，除非自包含） | **最好**（不装 Atlas 也能跑） |
| **红线契合** | 差（违「不拷贝旧用例、避免两份漂移代码」FAQ） | 好 | 好（要求底座自包含，顺带满足 #4） |

### 1.1「执行起来更快」专项论据（本评审重点）

运行速度**不主要由形态决定，而由实现模式决定**；但形态 2/3 才**支撑得起**快模式，形态 1 天然漂回慢模式。真正的耗时来源（对照 UI AUTO TEST 实测）：

1. **每步截图**：`runner.py` 的 `_call_step_inner` 成功/失败都 `page.screenshot()` → PNG 编码 + Allure 附加，是纯开销。Atlas 口径是「默认 `run` 零成功截图，排障才 debug/heal」（红线 #5）。共享底座可把截图策略收口成「只在失败时截」。
2. **固定 `page.wait_for_timeout(N)`**：UI AUTO TEST 用例大量 `wait_for_timeout(2000/3000/6000)`，保存后还 `30×2s = 60s` 轮询。这是墙钟主导项。Atlas 已有**语义等待** `waits.py`（等条件成立而非睡死）。共享页对象层把语义等待放进 `pages`，是**唯一能让每个用例都快**的结构；形态 1 会把 `sleep` 复制到每个用例。
3. **选择器/等待代码重复**：形态 1 下每用例各写一份，改一次要动 N 处（漏改即 flaky）。形态 2 一处生效。
4. **复用浏览器会话/并行**：三种形态同为 pytest，都能上 `pytest-xdist` 按用例并行；形态 3 因为自带底座反而更易在 CI 里独立并行（不抢 Atlas 的 venv）。

**结论**：单次运行「更快」靠的是①收口截图策略 ②语义等待替代固定 sleep ③选择器单点维护——**这三条只有形态 2/3 的结构承载得住**。生成速度快慢在三种形态间差异可忽略（都是离线模板拼接）。

## 2. 推荐

**主推「形态 2 ⊕ 形态 3 底座」的组合**（即以形态 2 为主干，用形态 3 的自包含要求约束底座），**不采用形态 1**：

- **主干 = 形态 2**：pytest 用例 + 共享页对象库。页对象库**从 Atlas 组件库 `components.json` 派生**（不是复制），复用 Atlas 已有的实测选择器与坑点，满足「知识资产单一来源不漂移」。
- **底座 = 形态 3 的自包含要求**：共享底座只用**标准库**（`urllib/json/os/re`），生成的 API 用例**可脱离 Atlas 运行**——顺带满足形态 3 的可移植性，且天然契合红线 #4（不引入新运行时依赖）。
- **弃用形态 1**：与 Atlas「不拷贝旧用例、避免两份漂移代码」（CLAUDE FAQ）直接冲突；维护成本最高、最易漂回慢写法。

**理由归纳**：形态 2 在「维护成本 / Atlas 复用度」上碾压，且是让「语义等待 + 截图收口」得以生效的结构前提（=更快）；形态 3 的底座自包含让产物能脱离 Atlas 跑（可移植），且不违 #4。两者叠加不冲突，产出「从 components.json 派生的 pytest 用例 + 自包含底座」。

## 3. 实现（`generator/emit_pytest.py`，只生成不执行）

输入：`generator/model.py` 的 `Endpoint[]` 或 flow dict。输出一份可独立运行的候选代码包：

```
exports/<flow_id>_pytest/
├── test_<flow_id>.py       # 用例：每 step 一个 test 函数（API→真调用；UI→skip 占位）
├── atlas_pages.py          # 页对象库：从 components.json 派生（选择器常量 + locator + action 骨架）
├── _atlas_support.py       # 自包含底座（仅标准库；urllib 发请求；占位符解析）
└── README.md               # 只生成不执行横幅 + 人审指引
```

关键纪律（对齐红线）：

- **#9 只生成不执行**：模块只返回源码文本 / 写文件，绝不 import 被执行、绝不发请求。
- **#7 不放宽断言**：只断言文档明示项（存活 + 文档给出的 `resultCode`）；数组/下标断言列 `TODO(人审)`，**绝不**改成恒真。
- **#4 不引入运行时依赖**：底座只用标准库；UI step 显式声明 Playwright 依赖并以 `pytest.mark.skip` 占位（不假装通过）。
- **占位符防线**：未解析的 `${VAR}` 在运行期**显式报错**，绝不把占位符当字面量发出去（假请求/假通过防线，与 Atlas DSL 未解析占位符告警同口径）。
- **复用组件库**：`atlas_pages.py` 的选择器直接来自 `components.json`（同一键值，不复制第二份）。
- 生成**确定性**（无时间戳/随机），可离线复现。

## 4. 边界与后续（不在本波次）

- **CLI 接线**（`atlas gen --with-pytest`）由 lead 统一做；本模块不碰 `cli.py`。
- **Allure**：生成阶段无执行结果，**不自写 Allure**（Gap5 §8.2 同结论；红线：生成器写报告=伪造）。若后续要 pytest→Allure，走「人工装 `allure-pytest`」的显式依赖通道（红线 #4 需先问）。
- **UI step 的页对象实现**：本波次只出选择器就绪的骨架（`NotImplementedError`），具体操作须人审从 UI AUTO TEST 的 `pages.py` 迁移并接 Atlas `components.json`。
- **反向（形态 3 完整版）**：若真要把 UI AUTO TEST 整套反生成/迁移进 Atlas，需将 `runner.py`+`pages.py`+`login.py` 作为 vendor 底座纳入，且逐条过「不拷贝旧用例」红线评审——本波次不做。

## 5. 验收

- `tests/test_emit_pytest.py`：生成产物 `ast.parse`/`compile` 全绿；结构断言（断言不放宽、无新依赖、无 Atlas 运行时 import、占位符保留、写操作标记、UI skip 骨架）；自包含底座真 import 验证占位符解析；文件包产物与拒绝覆盖。
- 命令：`PYTHONPATH=src python -m pytest tests/test_emit_pytest.py -q`。

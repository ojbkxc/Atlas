# Atlas 质量台账

> **这份文件是「整个项目挑不出毛病」的验收分母。** 它把每一个已知质量问题逐条编号，
> 给出**判定**（真缺陷 / 设计留白 / 文档噪声 / 环境未实测）与**处置**（修 / 不做+理由），
> 让「已处置」成为可查的事实而不是口头承诺。
>
> **口径（2026-11-26 与用户确认）**：现有自动化判据全绿 + 清空已知留白，
> 每条要么修、要么明确登记「不做+理由」。**允许改门禁/判据，但不得放宽**——任何放宽视为违反本目标。
>
> **一条必须讲明的事实**：`atlas review-code src` 在本台账收口后**仍会报约 26 条 low**。
> 那不是「没修完」——`GREEN-001-DECLARED`（带理由注释的收尾吞异常）与 `SAFE-001-COMMENT`
> （docstring 里的凭据**形态**讲解）正是项目为「真信号不被噪声淹没」而专门降的档。
> 让它们归零只有一条路：放宽规则，而那是本目标明令禁止的。所以验收是
> **每条都能在台账里查到处置**，不是报告显示 0 条。

## 复跑（不靠目测）

```bash
cd /d/GitHub/Atlas
PYTHONPATH=src .venv311/Scripts/python scripts/quality_inventory.py
# → reports/quality-inventory.md（逐条明细 + 代码上下文）
# → F=27 D=21 L=2 total=50
```

台账条目与实测条目的对账由 `tests/test_quality_ledger.py` 钉住：编号齐全性、
file:line 仍可解析、D 项的标记仍在——任何一侧漂移都会变红。

> **路径约定**：F 项与 D 项的 `file:line` 均相对 `src/atlas/`（如 `agent.py:145` ⇒ `src/atlas/agent.py`）；
> L 项无路径（它们是“机制缺失”，不是某个行）。测试按此约定解析，不得两边各写一套。

## 计数对账（2026-11-26 实测）

| 类 | 数量 | 来源 |
|---|---|---|
| F 项（review-code findings） | **27** | `atlas review-code src --format json` |
| D 项（TODO/FIXME/XXX/待补/留白 grep 命中） | **21** | `src/atlas/**/*.py`（其中 **1 条为误匹配**，见 D21） |
| L 项（已知留白） | **2** | allure 降级标记 / Docker·compose·CI 未实测 |
| **合计** | **50** | — |

**判定分布**：

| 判定 | 数量 | 含义 |
|---|---|---|
| 真缺陷（需动手） | **4** | F05 / F06 / F12（缺理由注释）+ F21（medium，写死样例客户号） |
| 设计留白 / 产品特性 | **20** | D 项除 D21 外全部 |
| 文档噪声 | **6** | SAFE-001-COMMENT（F22–F27） |
| 合法类型抑制 | **1** | F20（GREEN-004） |
| 理由已成立（登记引用） | **16** | GREEN-001-DECLARED（F01–F19 中除 F05/F06/F12） |
| 非标记（grep 误匹配） | **1** | D21（`\uXXXX`） |
| 环境未实测 | **1** | L02（本机无 docker） |
| 真缺口（机制缺失） | **1** | L01（allure 降级标记未进容器级结果） |
| **合计** | **50** | 4+20+6+1+16+1+1+1 = **50**（与 F27+D21+L2 对得上） |

---

## 一、F 项：review-code findings（27 条）

`GREEN-001-DECLARED` 的判定依据：该规则要求「同行 / 上一行 / **body 首行**」有 ≥6 字符的理由注释，
本表逐条把那条注释抄出来——**理由成立就登记引用它**，不成立或缺失才动手。

### GREEN-001-DECLARED（19 条，均为「收尾吞异常」）

| 编号 | 位置 | 判定 | 处置 | 证据（理由注释原文） |
|---|---|---|---|---|
| F01 | `agent.py:145` | 理由成立 | 不做 | body 首行：`留痕写失败不得顶掉执行真因；内存 decisions 仍保留` |
| F02 | `autopilot.py:276` | 理由成立 | 不做 | body 首行：`取证尽力而为：坏事件对象不顶循环` |
| F03 | `autopilot.py:297` | 理由成立 | 不做 | body 首行：`取证尽力而为：坏响应对象不顶循环` |
| F04 | `cli.py:689` | 理由成立 | 不做 | 同行：`留痕自身崩不得顶掉真因（用户下一句必须问「登录为什么挂」）` |
| **F05** | `cli.py:1291` | **理由成立但缺注释** | **修（补注释）** | 无；上下文为进程树杀兜底，1294 行 `proc.kill()  # 兜底：直接子进程本体（树杀失败的最后一手）` |
| **F06** | `cli.py:1295` | **理由成立但缺注释** | **修（补注释）** | 无；同 F05，`proc.kill()` 的最后一手兜底 |
| F07 | `legacy_fast.py:528` | 理由成立 | 不做 | body 首行：`导航级注入尽力而为；当前文档还有 evaluate 兜底` |
| F08 | `plugins/demo/legacy_case.py:39` | 理由成立 | 不做 | 同行：`# pragma: no cover - pytest 是 Atlas 硬依赖，这里只做防御` |
| F09 | `review_code.py:815` | 理由成立 | 不做 | 同行：`注册表读坏=判不了，按「未注册」返回 False（本函数只服务 MAINT-002 提示）` |
| F10 | `review_code.py:1054` | 理由成立 | 不做 | 同行：`单个断言 unparse 不了就跳过（源码里已有 SyntaxError 兜底，逐条降级）` |
| F11 | `runtime.py:476` | 理由成立 | 不做 | body 首行：`关闭失败不得顶掉原始入口异常（login/OU/attach 的真因更重要）` |
| **F12** | `runtime.py:517` | **理由成立但缺注释** | **修（补注释）** | 无；上下文为顶栏探测失败 → 519–527 走重载重试（`重载失败由下方 wait_for_selector 重判`） |
| F13 | `runtime.py:522` | 理由成立 | 不做 | 同行：`重载失败由下方 wait_for_selector 重判（本轮不进等待即为失败证据）` |
| F14 | `runtime.py:527` | 理由成立 | 不做 | 同行：`本轮没等到 → 下一轮重载再看（两轮都失败由下方统一报真因）` |
| F15 | `scaffold.py:66` | 理由成立 | 不做 | 上溯 2 行：TOML 读侧注释块（`坏 pyproject 确实不是「Atlas 根」的证据，要修的是编码不是吞异常`） |
| F16 | `session.py:116` | 理由成立 | 不做 | 同行：`收尾失败不得顶掉原始入口异常（真因优先，见下方 raise）` |
| F17 | `session.py:120` | 理由成立 | 不做 | 同行：`同上：浏览器进程停不掉也不改写入侵失败的真因` |
| F18 | `session.py:156` | 理由成立 | 不做 | body 首行：`PNG 已落盘，侧车状态失败不影响取证主体` |
| F19 | `session.py:258` | 理由成立 | 不做 | 同行：`单条重放器坏掉就跳过试下一条（定性器不得先于被定性对象死，见 docstring）` |

**F05 / F06 / F12 为什么不改成抛异常**：这三处都是**收尾路径**（杀进程 / 探测顶栏），
失败时目标本身已达成或已由后续步骤重判；改成抛异常会把「收尾失败」顶成「主流程失败」，
正是红线 #7 反向要防的「把已成立的结论弄错」。缺的只是**把理由写进代码**，让规则可核。

### GREEN-004（1 条）

| 编号 | 位置 | 判定 | 处置 | 证据 |
|---|---|---|---|---|
| F20 | `actor.py:23` | 合法类型抑制 | 不做 | `# type: ignore[return-value]`：`abilities` 是 `dict[Type[Ability], Ability]`，泛型无法表达「键类型 → 值类型」的对应；运行时由下一行 `except KeyError` 兜底并点名 `Actor 未配置 X`。删掉它会让 mypy 报错，而运行时行为零变化。 |

### MAINT-003（1 条，**唯一 medium**）

| 编号 | 位置 | 判定 | 处置 | 证据 |
|---|---|---|---|---|
| **F21** | `plugins/demo/tasks.py:255` | **真缺陷** | **修** | `SelectCustomerTask(..., defaults={"code": "CUST0000001", "code_full": "CUST00000010001"})`——写死样例客户号。flow 不提供 customer 时**静默**用这个客户，属 AGENTS §1.6「禁止步骤内数据兜底」同族。项目已有正确路径：同文件 `overlay_session()` / `_session_default()`（第 15–20、68–80 行）从 `DemoPlugin.session_defaults()` 取值。 |

### SAFE-001-COMMENT（6 条，全部为文档噪声）

| 编号 | 位置 | 判定 | 处置 | 证据 |
|---|---|---|---|---|
| F22 | `config.py:312` | 文档噪声 | 不做 | docstring 讲代理**形态**：`` 形态 `http://host:port` 或 `http://user:pass@host:port` ``——`user:pass` 是占位符不是凭据 |
| F23 | `credential_gate.py:80` | 文档噪声 | 不做 | 注释讲正则口径（`名: 值` 行、键名类排除哪些字符） |
| F24 | `credential_gate.py:215` | 文档噪声 | 不做 | 注释讲 userinfo 凭据**形态**为何要整段 redact |
| F25 | `generator/__init__.py:147` | 文档噪声 | 不做 | 注释讲红线 #1 输入面历史（旧实现只闸 headers） |
| F26 | `review_code.py:231` | 文档噪声 | 不做 | 注释讲占位符「抹段而非整行跳过」的理由 |
| F27 | `review_code.py:695` | 文档噪声 | 不做 | docstring 讲 `SAFE-001-COMMENT` 这个档位为何存在 |

**6 条的共同判定依据**：全部出现在**不可执行**的注释或 docstring 里，内容是「教用户怎么填」
或「解释判据由来」，**没有任何一条是真凭据**（逐条核过：出现的都是 `user:pass` / `<password>`
形态的占位）。这正是 `SAFE-001-COMMENT` 档位存在的目的——否则「文档教用户接 env」的那一行
自己吃 critical，噪声盖住真信号（该档位的由来见 F27 引用的 docstring）。

---

## 二、D 项：TODO / 留白 grep 命中（21 条）

**关键判别：删 TODO ≠ 清留白。** 这批里绝大多数是**文档化的设计留白或产品特性**，
删掉会破坏既有承诺（尤其 `emit_pytest` 的 `TODO(人审)`——那是「认不出就留 TODO 供人审补，
绝不改成恒真或静默丢弃」的实现本体，见红线 #7）。

| 编号 | 位置 | 判定 | 处置 | 理由 |
|---|---|---|---|---|
| D01 | `config_add.py:211` | 设计留白 | 不做 | 骨架的 `TODO(人工)`：`接真实页面库/接口。未实现就抛，绝不返回假成功（红线 7/12）`——**故意不实现**，让新接入者必须自己填，防「生成即假绿」 |
| D02 | `config_add.py:250` | 设计留白 | 不做 | 同上：`接真实只读查询。没有就返回 None（诚实），不要伪造` |
| D03 | `generator/emit_fast.py:1041` | 显式留白 | 不做 | `F-11 留白（本波**不实现**，别顺手加）`——已显式登记的设计决策 |
| D04 | `generator/emit_flow.py:33` | 文档描述 | 不做 | 描述「认不出就降级成 TODO」的行为，非待办 |
| D05 | `generator/emit_probe.py:58` | 产品特性 | 不做 | **生成到产物里的字符串**：产物里留 `# TODO(人审)：确认该查询已在白名单` |
| D06 | `generator/emit_pytest.py:15` | 文档描述 | 不做 | 模块 docstring：`DSL/生成器不支持的断言列 TODO 供人审补，绝不改成恒真` |
| D07 | `generator/emit_pytest.py:70` | 文档描述 | 不做 | 断言形态说明：`kind="note" 只留 TODO` |
| D08 | `generator/emit_pytest.py:78` | 文档描述 | 不做 | 说明为何不许静默丢弃 |
| D09 | `generator/emit_pytest.py:283` | 文档描述 | 不做 | flow 级断言归属规则说明 |
| D10 | `generator/emit_pytest.py:319` | 文档描述 | 不做 | Task 形态断言 → 人读摘要 |
| D11 | `generator/emit_pytest.py:329` | 文档描述 | 不做 | 未归属条目 → 待接 TODO 文案 |
| D12 | `generator/emit_pytest.py:336` | 文档描述 | 不做 | 「认不出就留 TODO」与 `note` 同呈现路径 |
| D13 | `generator/emit_pytest.py:439` | 产品特性 | 不做 | 生成 `# TODO(人审): {a.note}` 到产物 |
| D14 | `generator/emit_pytest.py:475` | 产品特性 | 不做 | 同上 |
| D15 | `generator/emit_pytest.py:572` | 产品特性 | 不做 | 同上 |
| D16 | `generator/emit_pytest.py:634` | 产品特性 | 不做 | 生成 flow 级 `# TODO(人审)` |
| D17 | `generator/emit_pytest.py:654` | 文档描述 | 不做 | `flow_notes` 参数说明 |
| D18 | `generator/emit_pytest.py:683` | 产品特性 | 不做 | 同 D16 另一渲染入口 |
| D19 | `generator/emit_pytest.py:1587` | 产品文案 | 不做 | 产物 README：`TODO(人审)` 处按业务补断言（**勿放宽**） |
| D20 | `generator/emit_pytest.py:1626` | 产品文案 | 不做 | 产物 README 红线警示 |
| **D21** | `legacy_fast.py:496` | **非标记（误匹配）** | 不做 | 命中原因是注释里的 `\uXXXX`（JS 转义说明），不是待办标记。**不改代码**，只在此登记以免下次清点再被误判 |

**为什么 D13/D14/D15/D16/D18 是「产品特性」而不是「我们的债」**：`emit_pytest` 的产品契约是
「认不出就留 `TODO(人审)`，绝不改成恒真、也绝不静默丢弃」（红线 #7 的双向承诺）。
这些字符串是**生成给用户看的**，删掉它们等于把「有断言没实现」藏起来——那是把红线反过来违反。

---

## 三、L 项：已知留白（2 条）

| 编号 | 项 | 判定 | 处置 | 证据 / 理由 |
|---|---|---|---|---|
| **L01** | allure 降级标记（`emit_allure_review` 无容器概念） | 真缺口 | **修** | 上一波 A-02 只把 `knowledge_degraded` 写进 `summary` 与 text 格式；`emit_allure_review(file_results, out_dir)` 每个文件一个 result、没有容器级结果，**只看 allure 的 CI 看不到「选择器核对未执行」**。已在上一波 commit 里显式留白，本台账收口。 |
| **L02** | Docker / compose / CI 未实测 | 环境未实测 | **不做（登记）** | 实测 `docker --version` → `command not found`；本机无 Docker，无法真实构建镜像或跑 compose。**绝不伪造通过**（AGENTS §1.15：必须真实执行并归档才能声称跨平台完成）。复现命令：`docker build -t atlas . && docker compose up --abort-on-container-exit`。CI 部分改为**本地核对判据一致性**（读 `.github/workflows/*.yml` 与本地命令逐条比对），不声称「CI 已绿」。 |

---

## 四、要动手的清单（4 + 1 = 5 条）

（以下为**清单引用**，不重复台账条目；条目的完整记录在上三节。）

- **F21** `tasks.py:255` 写死样例客户号 → 改走 `overlay_session` / `_session_default` —— 归属 T2
- **F05** `cli.py:1291` 补理由注释（不改行为） —— 归属 T3
- **F06** `cli.py:1295` 补理由注释（不改行为） —— 归属 T3
- **F12** `runtime.py:517` 补理由注释（不改行为） —— 归属 T3
- **L01** `emit_allure_review` 加容器级结果承载降级事实 —— 归属 T5

**其余 45 条**：登记为「不做 + 理由」，理由均在上表可核（引用代码注释原文 / AGENTS 条款 / 实测输出）。

---

## 五、门禁不放宽的自证

本目标允许改门禁但**禁止放宽**。收口时逐条核这些「只能更严或不变」的点：

| 判据 | 收口前 | 收口要求 |
|---|---|---|
| `ruff check --select F401,F841,F811,E402` | All checks passed | 规则列表**不得减少**；`--select` 只能加不能减 |
| `review-code` critical/high/medium | 0 / 0 / 1 | 必须 0 / 0 / **0** |
| `review-code` 规则档位 | `GREEN-001-DECLARED` / `SAFE-001-COMMENT` 存在 | 档位定义**不得删**（删了就是放宽） |
| `--fail-under` 类值域闸 | 0..100 | **不得**放宽到允许负值 |
| `pytest -q` | 0 failed | **不得**有 skip 化、xfail 化、断言放宽 |

# gap11 波次裁决（lead 独占；与 gap9-lead-rulings.md 同规则：只有 lead 落笔）

主题：**Atlas 输出「UI AUTO TEST 旧仓可直接跑」的 pytest 包，并跑得比旧套件快**。
机制不是 Atlas 引擎（R1 已实测证否：桥接轨继承旧 `step_N` 固定睡眠、净增约 8s），
而是**运行期垫片**：`Page.wait_for_timeout` → 有界语义等待；每步成功截图抑制。

## G11-1 · 垫片语义洞：reload/goto 后的等待不得靠纯 mask 探针（lead 读实现时发现，2026-11-09）

**洞的形态**：旧仓大量 `page.reload(...)` 后立刻 `wait_for_timeout(5000/8000)`
（`lib/pages.py` 的 `open_doc_list`、`open_draft_detail` 都是）。那一刻页面尚未渲染骨架，
**没有任何 `.el-loading-mask`** → 纯 mask 探针立刻为真 → 垫片 0ms 放行 → 应用未启动即继续
→ 用例**比原版更早失败**（失败点前移）。这是"更快"与"行为变了"的分界线。

**裁（三条全做）**：
1. mask 探针必须并上 `document.readyState === 'complete'`；
2. 加有界下限 `min_ms`（env `ATLAS_FAST_MIN_MS`，默认 `min(400, requested)`；
   `min_ms > requested` 按 requested 截断——**绝不延长**这条总边界不变）；
3. 可选应用就绪锚点 `ATLAS_FAST_SETTLE_ANCHOR`（非空则要求该选择器可见；旧仓建议 `.app-main`）。

**为什么不能只靠 readyState**：`readyState==='complete'` 之后 Vue/Element 应用仍可能未挂载，
旧的 5000/8000ms 等待里包含"应用启动"语义，不在 mask 探针的能力面内——必须靠 min_ms 与锚点兜底。
**风险登记**：这条写进 gap11 设计 E.1 与落地注风险条，不许只写在消息里。

**A/B 判定点（新增）**：失败点必须仍是 step 7；**前移即判行为变更**，不许当成绩。

## G11-2 · 截图计数：非破坏差分，不物理清空旧仓（verifier 提议、lead 采纳）

问：要数 `stepN_result` 截图张数，是否清空旧仓 `iservice/reports/allure-results`？
裁：**不清空**。旧仓属只读资产，"清空"＝删历史证据，与「原始 legacy-suite 不修改」冲突。
等价方案：跑前拍 `path|size|mtime` 清单 → 跑后取「新增文件 ∩ `step\d+_result*`」，报告里必须
写明「本轮为差分计数，未删除旧仓任何字节」。

**更正（2026-11-09 21:5x，verifier 现场取证）**：本条原第二证「旧仓 `git status --porcelain`
前后比对」**执行不了**——旧仓 `D:\GitHub\UI AUTO TEST` 不是 git 仓（`git rev-parse
--is-inside-work-tree` → `fatal: not a git repository`），porcelain 无载体。第二证改为双快照：
① **源面**：全树 `find ... -not -path "*/reports/*" -printf "%p|%s|%T@\n" | sort` 跑前/跑后
diff **必须为空**（未删除与未修改双面）；② **生成面**：`iservice/reports/` 下只允许新增
（旧 runner 自产物），旧文件消失数=0、size/mtime 零变动，新增逐条列出。Atlas 仓侧
`git status --short reports/` 是另一回事（门禁第 7 步），两处不许混。可变自产物
（`pytest_latest.log`/`plan_ctx.json`）被旧 runner 自身重写属预期，如实标注不入违规。

## G11-3 · 顺序与数字归属（沿用 gap9 口径）

- 顺序：**#16 设计 → #17 垫片 / #18 兼容产物 / #19 CLI → #21 跨切面判据 → #20 门禁 + 真机 A/B → #22 文档**。
  #21 不可跳：它的判据测属于门禁套件的一部分，缺它出数等于拿不完整的套件报绿。
- 数字：**只由 verifier 交 lead**，lead 一次落四处（README:6 / README 边界 / AGENTS:46 / CLAUDE FAQ）；
  `docs/gaps/**` 零计数位（gap9 的 `__LEAD_LANDS__` 占位设计已作废，勿重犯）。
- 真机 A/B：三组串行前台（原样 / `--fast` / `--fast`+stats），跑前双查无他人会话；更慢也如实报。

## G11-4 · 兼容产物的两条硬约束（lead 派单时定，防止走上"复制业务代码"的岔路）

1. **产物绝不 import atlas**（旧仓机器没装 atlas）。只允许 pytest/可选 allure + 经
   `ATLAS_LEGACY_WEB` 定位的旧 `lib`。
2. **垫片源码按真文件字节 vendor**（`src/atlas/legacy_fast.py` / `fastplugin.py`），
   带 provenance（相对路径 + sha256）；禁止在 emit 层手抄第二份逻辑（#92/#15 同源）。
   源文件改了而不重新生成产物 → 离线测必须红。

## G11-5 · 「超过」的可度量定义（写进设计与文档的唯一口径）

只认三项，其它一律不算：
① 同链路墙钟（同失败点或同通过点）；② 成功截图张数；③ 固定睡眠实际发生量（垫片 stats 记账）。
"架构更好/更现代"这类不可测说法不得作为结论。

## G11-6 · 等待垫片撤出 `--fast` 默认置位（2026-11-10，真机三轮 + 8 组档位探针后，lead 授权主会话自决）

**背景**：#30/#31 曾把「`--fast` 打了就生效」等同于「CLI/产物代置位 `ATLAS_FAST_WAITS=mask`」。
#33 收紧了判据（只有「本次窗口内观测到忙→转静默」才许提前），但第三轮 A/B（08:26–08:49）证明
**收紧仍不够**：基线恒 step 7，r1 三组全 step 7，**r2 两组 fast 同时前移到 step 3**——同码同配置
一轮全过、一轮全挂。随后 8 组档位探针（同档案 `probe_*.log`）把处置空间量完：

| 配置 | 样本 | 失败点 | saved_ms | 判定 |
|---|---|---|---|---|
| `mask`（下限 400） | r1 3/3 过 + r2 2/2 前移 | 间歇前移 | 6.2–11.1s | 不可默认 |
| `mask+xhr`（400） | 1/3 同点 | 间歇前移 | 2.0–4.6s | 不可默认 |
| `mutation`（400） | 2/2 同点 | 同点 | **0 / -1** | 安全=功能关掉 |
| `mask`+`MIN_MS=3000` | 3/3 同点 | 同点 | 1.0–2.0s（< 基线噪声 93–177s） | 治标，不构成收益 |

**裁**：
1. **`LegacyAdapter.FAST_DEFAULTS` 只保留 `ATLAS_FAST_NO_STEP_SHOTS=1`**——`--fast` 与
   `--compat` 产物（同源单点）**不再代置位 `ATLAS_FAST_WAITS`**；等待垫片改为**显式 opt-in**。
   未设等待档时 `--fast` 打一行 NOTE 点名（#30 的「以为开了其实没开」教训反向用一次：可核面必须出声）。
2. **不动的东西**：垫片本体默认仍关、三档、fail-safe、`min_ms`（400）、锚点、stats 面、
   显式设置恒优先（含 `off`）——全保留。撤的只有「默认替你开」这一条。
3. **为什么不选另外四条**：默认改 `mask+xhr` 被 1/3 样本否掉；`mutation` 等于关功能还多付探针成本；
   抬 `min_ms=3000` 不改变「遮罩消失≠数据就绪」的残余盲区（只是被更长的固定余量盖住），
   且 3/3 不构成「3s 在别的链路也够」的证明、收益低于噪声不能当成绩；「默认 + 风险声明」直接违反
   G11-1 的字面（失败点前移即行为变更）——会间歇改变行为的档位不许以默认形态发出。
4. **三关口径不变**：只对被**显式启用**的等待腿适用（①失败点同一步 → ②2a `calls>0` + 2b 离线配对格
   → ③才谈墙钟）；`saved_ms`/`no_busy_observed` 仍是报告量，不作任何一关的通过条件。
5. **证据**：`reports/gap11_ab_r3_20261110/`（`SUMMARY3.md` + 六组日志 + 两份 jsonl + 8 组探针 log/meta），
   **不得删除、不得覆盖**；落地注条目见 `docs/gaps/gap11-legacy-fast-compat.md` G.7。
6. **一般教训（写死）**：「同配置跑过一次」不是安全证据——`mask` 第三轮 r1 3/3 过、r2 2/2 挂。
   任何「等待垫片安全」的结论必须给同配置多轮多点观测 + 失败点逐点对齐，缺一不算。

## G11-7 · 调用点规则（site rules）批准为「显式 opt-in 的安全提速」，不改变默认

1. **机制**：`ATLAS_FAST_SITE_RULES` 指向 JSON，按等待点（文件名 + 行号 + 可选函数）声明就绪
   条件，五类：`js` / `url` / `mask_gone` / `mask_cycle` / `never`。命中按条件放行，**条件超时
   一律睡满剩余预算**；未命中点仍走通用档。规则路径用进程侧 `evaluate` 轮询（**不得**用
   `wait_for_function`——导航中预算会失控，真机实测 6s→9.7s）。
2. **默认不动**：`--fast` 仍只默认截图抑制；规则档必须显式给 `ATLAS_FAST_WAITS` +
   `ATLAS_FAST_SITE_RULES`。理由同 G11-6：样本不足以证明「永不前移」。
3. **真机证据**（2026-11-10 TC1，`reports/gap11_fast_safe_20261110/`）：同点组 4/5
   （失败点均 step 7，与基线一致），1 组 `TargetClosedError` 环境异常（垫片如实传播
   `unrecoverable`，不是提前）；`saved_ms` 11.8–13.7s。墙钟受 ±30s 级环境噪声，不作判据。
4. **规则编写纪律（事故换来的）**：`mask_gone` 只可用于「遮罩必然存在」的点——
   无遮罩点等于立即放行（语言切换 3000→6ms 打挂 step 1）；「元素出现 ≠ 应用可交互」
   （登录后/hover/reload 初始化窗口一律 `never`）；业务条件优先于间接信号
   （等「查询结果出现目标客户代码」而不是等遮罩循环）。
5. **边界**：TC1 只验证到 step 7；step 8+ 未验证；兼容包产物侧同机制未真机（#29 待授权）。

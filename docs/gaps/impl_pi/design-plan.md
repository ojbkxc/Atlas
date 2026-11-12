# Atlas pi-absorb 实施总协调（design-plan）

> 状态：**施工级设计总纲**（team atlas-absorb-pi-impl · pi_design · 2026-11-06）
> 上游：`docs/gaps/gap6-pi-agent-desktop.md`（已批准 6 项 P1，含 §9 终验勘误 E1–E9/S1–S4）
> 下游：design_c4 / design_c2 / design_c6 / design_c1 / design_c3 / design_c5 + 实施任务 #3–#8
> 用户拍板（约束本文件）：**顺序 C4→C2→C6→C1，C3/C5 随后；没做过的先研究讨论分析再评审，评审通过才动工；硬约束 Windows+Linux 双平台兼容。**

---

## 1. 波次与实施顺序

| 波次 | 任务 | 内容 | 实施者 | 前置 |
|---|---|---|---|---|
| W0 | #2 | 设计对抗评审（本 7 份文档为评审对象，评审通过才开 W1） | pi_critique | #1 |
| W1 | #3 + #4 | **C4（先）→ C2 → C6**；#3=C4+C6 串行同一人，#4=C2 并行 | impl_report（#3）、impl_tower（#4） | W0 |
| W2 | #5 | C1 调度核（最大项；C1a 全量 + C1b 只定接口不实现） | impl_sched | #3、#4 |
| W3 | #6、#7 | C5 边界账本 ∥ C3 迁移三件套（文件集不相交，可并行） | impl_bounds、impl_migrate | W2 |
| W4 | #8 | 终验（离线门禁三连 + 双平台条款逐条复核 + 文档事实回写） | pi_final_verifier | #6、#7 |

- 波内顺序按拍板 **C4→C2→C6**：#3 先落 C4（纯契约/产出闸，改动最小、回收最快），#4 与之并行（文件集不同，见 §2）；#3 的 C6 收尾。
- **开工前置研究已满足**：6 项均有 gap6 §3/§4/§9 + `research_pi/` 证据背书；唯二「没做过」的净新增形态是 C1 的 cron 自写解析器与 O_EXCL 租约、C5 的 diff 分类器——设计内已给出**先讨论分析→评审→动工**的边界表与探针口径（C1b 显式后置，设计期不定 Windows 探针实现细节，只定接口与测试要求）。

## 2. 文件归属表（热点文件显式归一，一文件一波次一主人）

| 文件 | W1（C4+C2+C6） | W2（C1） | W3（C5∥C3） | 规则 |
|---|---|---|---|---|
| `src/atlas/cli.py` | **impl_report**（doctor `version_triple`、gen headers 闸文案） | **impl_sched**（`schedule` 子命令） | **impl_bounds**（doctor `boundary_ledger` 项）；impl_migrate 禁入 | 同波并行者禁改；协调补丁见 §3 |
| `src/atlas/recorder.py` | **impl_report**（C6 version 键） | 禁入 | 禁入 | 零依赖纪律保持 |
| `src/atlas/engine.py` | **impl_report**（C4② reason 收敛 + C2 协调补丁1） | 禁入 | 禁入 | |
| `src/atlas/failures.py` | **impl_report**（C4② 净化） | 只读复用 | 只读复用 | 单一真相，禁第二份判据 |
| `src/atlas/runtime.py`、`src/atlas/generator/emit_flow.py`、`src/atlas/allure_report.py`、`src/atlas/dsl.py`、`scripts/import_jmeter.py`、`src/atlas/versioning.py`(新)、`src/atlas/credential_gate.py`(新)、`PRIVACY.md`、`tests/test_import_jmeter.py`、`tests/test_allure_report.py`、`tests/test_privacy_drift.py`(新) | **impl_report** | — | — | experience.py **零改动**（C2 三元组走 `evidence.verification` 既有通道） |
| `src/atlas/review.py`、`src/atlas/healing.py`、`src/atlas/allure_analysis.py`、`tests/test_c2_verification.py`(新) | **impl_tower**（C2） | — | `review.py` 一行改动（boundary_review 拒绝）归 **impl_bounds** | W1/W3 不同波，无同波互踩 |
| `src/atlas/scheduling/*`（新包）、`tests/test_scheduling_*.py`（新） | — | **impl_sched** | — | |
| `docs/ownership/atlas-boundaries.json`(新)、`scripts/boundary_classify.py`(新)、`tests/test_boundaries.py`(新) | — | — | **impl_bounds** | |
| `scripts/migration_gate.py`(新)、`tests/test_migration_sentinels.py`(新)、`docs/gaps/impl_pi/c3-baseline.md` | — | — | **impl_migrate** | |
| `CLAUDE.md`/`README.md`/`AGENTS.md` 事实回写（红线 #8） | W1 收尾由 **impl_report** 统一合并（impl_tower 把待写条目消息交它） | impl_sched | W3 各自主人各写各段（波内单主） | 防同文件并行撑破（memory 教训） |
| `tests/conftest.py`、其余既有测试 | 任何波次**默认禁改**；确需改：在 design 的「协调补丁」列明后才可由归属人执行 | | | |

## 3. 协调补丁（跨归属的定点小改，评审重点）

1. **engine.py → journal 传三元组**（C2 数据源，engine 归 impl_report）：engine 失败路径 `journal.draft(...)` 补传 `evidence={"verification": {...}}`（补丁文本见 **design_c2 §3.3 补丁 1**），由 impl_report 在 C4② 同文件改动中一并落。
2. **cli.py `--allow-unverified`**（C2 override 入口，cli 归 impl_report）：impl_tower 完成后向 impl_report 发「cli 补丁就绪」消息，由 impl_report 落 **design_c2 §3.3 补丁 2** 的接线。**W1 完成判定 = 补丁 1、2 均落地**；未落地前 C2 的 review.py 判据完整可用（flag 缺省=不允许），不阻塞 #3。
3. recorder.py 的 `version` 参数（C6）由 dsl.py 传入——两文件同主，无协调成本。

## 4. 跨平台兼容矩阵（硬约束，逐条为验收项，终验 #8 逐条打勾）

| # | 条款 | 细则 | 波及设计 | 验收测试 |
|---|---|---|---|---|
| X1 | pathlib，禁反斜杠硬编码 | 新代码路径一律 `pathlib.Path` / POSIX 相对字符串进 JSON；源码禁 `"\\\\"` 字面量（哨兵扫描） | 全部 | `tests/test_xplat_sentinels.py` 静态扫新文件 |
| X2 | subprocess 必 argv，禁 shell=True | C1 派发 `subprocess.run([sys.executable,"-m","atlas","run",...])`；C3/C5 同理；参数名（flow 路径/case id）必 argv 传递不入 shell（memory 坑：分支名/路径转义） | C1、C3、C5 | 各设计单测断言命令为 list |
| X3 | O_EXCL 锁语义差异 | 创建冲突两平台同抛 `FileExistsError`；差异在**删**：Windows 句柄未关/杀软瞬锁 → `unlink` 可 `PermissionError`。纪律：创建即关句柄（`os.close` 紧跟）；接管删除失败→重试 1 次（100ms）仍失败按「推迟≠跳过」退出 rc0，绝不硬删；抢到后**回读文件确认 token 是自己的**（pi lease.ts 双检式，防两个进程同时接管互踩） | C1 | `test_scheduling_lease.py` 含「占用中删失败→推迟」case |
| X4 | GBK 控制台 ASCII 化 | 新增面向终端的 print **禁** `✓ ✗ ⚠ → █` 等非 GBK 字符（explore.py:60 真机崩例、memory PT 教训）；中文本身可 GBK 编码不受影响。import_jmeter.py:181 现用 `⚠`——**W1 改 `WARN:`**（同文件同主）；新文档口径同步 | C4、C1、C3、C5 | `test_xplat_sentinels.py`：对 src/+scripts/ 的 `print(` 字面量做 `encode("gbk")` 试编 |
| X5 | CRLF/换行追加纪律 | JSONL 追加前检测尾字节非 `\n`/`\r` 先补断行（experience.py:64-77 先例）；C1 状态追加、C3 基线落盘同纪律；读侧 `read_text + splitlines` 天然兼容 CRLF，**禁**逐字节 `== b"\n"` 单判；`text=True` 子进程统一 `encoding="utf-8", errors="replace"` 且子 env 加 `PYTHONIOENCODING=utf-8` | C1、C3 | 追加断行单测（模拟残行） |
| X6 | zoneinfo | tz 用 stdlib `zoneinfo`；**Windows 无系统 tzdb** → `ZoneInfoNotFoundError` 时点名「`pip install tzdata`」，不硬依赖、不静默回落 UTC；DST 口径见 design_c1 §5（+1s/半开区间钉整点、fold=0） | C1 | 缺失 tz 报错文案测（monkeypatch 无 tzdata 路径） |
| X7 | SIGTERM 仅 POSIX | 超时终止统一 `subprocess.run(timeout=…)`（POSIX SIGKILL / Windows TerminateProcess，语义=强杀不留记录）——**不自己发信号、不用 `os.kill(pid,0)` 探活**；pid 探针仅 C1b（接口预留：`pid_alive(pid)->bool|None`；Windows 禁 `os.kill(pid,0)`=CTRL_C_EVENT 广播+组语义误杀（gap6 §9 S2），须 ctypes `OpenProcess`+`GetExitCodeProcess!=259` 或 `tasklist /FI`，不确定→**推迟记录不判失败**）；手动 Ctrl-C（KeyboardInterrupt）记 interrupted **不计连败**（CONTROL_FLOW 单一真相 #114 第二实现） | C1 | C1b 只测接口契约与「None=不确定按推迟」分流 |
| X8 | cron 驱动方式（文档义务） | Atlas **不做常驻进程、不自动注册 OS 任务**（触 Ask-first）。`atlas schedule crontab --id <id>` 按 `sys.platform` 打印可直接粘贴的接线命令：Linux `crontab` 行 / Windows `schtasks /Create` 行；README「定时巡检」小节 + design_c1 §7 双平台 SOP 为交付物，终验核对 | C1 | 输出文案含两形态各 1 测 |
| X9 | os.replace 原子改写 | 状态文件 tmp+`os.replace`（Windows 允许覆盖已存在目标，但目标被打开时可 `PermissionError`：重试 1 次→仍败则整轮放弃并点名，不做半写） | C1、C2、C5 | 占用文件模拟测 |

## 5. 完成门禁（每波）

- `PYTHONPATH=src python -m pytest -q`：**当日全量实跑基线不回退**（计数只认 README 徽章 / AGENTS §2 四处，本文不留计数位），各波新增测试数在对应设计 §8 标注下限；
- `python -m compileall -q src`；`atlas doctor`（W1 后含 `version_triple`，W3 后含 `boundary_ledger`）；
- 矩阵 X1–X9 相关测试全绿；红线五查逐条在设计内自证（见各 §9）；
- **离线全绿 ≠ 可用**（AGENTS §2）：C1 的 OS 接线、C4 真 .jmx 迁移产物只增**离线测**，真机/真文件验证留给用户按 SOP 执行，各设计 §10 列明未验证项。

## 6. 禁改清单（全程）

设计阶段（本任务）：只写 `docs/gaps/impl_pi/`，不碰 src/tests，不 commit/push，不跑真机。实施阶段：不改 `tests/conftest.py`（除 §3 列明）、不动 `D:\jmx`、不引入任何新第三方运行时依赖（`croniter/APScheduler/psutil/mutmut` 全禁——gap6 E2 口径：这是纯 stdlib 先例+治理取舍，不是红线字面禁止，评审时不得把理由写反）。

---

## 评审勘误注（pi_critique · 2026-11-06 · 全文见 design-review.md）

- **§2 归属表以 design-review.md §6 的改判为准**（A boundaries.py→impl_bounds；B test_migration_gate/test_versioning/test_failures_sanitize 补标；C scaffold.py 与 C2 勘误列明的 3 个既有测试 fixture 文件补标；D test_xplat_sentinels.py 归 impl_report 建、目录级扫描、此后各波禁改）。
- **C2 判定 REJECT（单点）**：按 design_c2 文末勘误补「补丁 3」后视同放行；C4 两条必改（sanitize 逐键 redact 不整段抹 query；定性输入=净化前原文+对抗样本）；C1 必修 X8 示例矛盾与 `-O` 剥 assert 两处；C3 空 scope=红、gate 加 timeout；C5 判据上移 src/atlas/boundaries.py。X4 实测 src+scripts 非 GBK print 崩溃点仅 `import_jmeter.py:181`（:188 的 `→` GBK 可编，属风格项）。

# wave4：落盘写入统一原子 + 复用实测安全追加器（落地记录）

> lead 裁定本批只做一条线：并发轴 cc-1（六条无锁追加路径）与可靠性轴 rb-1/3/5/7/8
> 指向同一根因——**落盘写入缺原子性/统一加锁实现**。一个模式：新 `src/atlas/atomic_io.py`
> **收敛先例**（此前 `devops_sync`/`emit_fast`/`lease`/`state` 各有一份 tmp+replace），消费方只许调（#92）。默认路径逐字节不变（修复本身除外）。

## 1. 做了什么

### 1.1 新模块 `src/atlas/atomic_io.py`（收敛先例）

- `append_line_locked(path, payload)`：**原样从 `recorder._append_line_locked` 移来**——
  进程内 `threading.Lock` 串行 + 跨进程文件锁（Windows `msvcrt` / POSIX `flock`）内单次
  整段写，`O_APPEND` 保证写位置恒在 EOF。recorder 改 import 别名，调用点不变。
  **分水岭判据**：裸 CRT 文本模式 `open("a").write()` 丢 36%、`os.open(O_APPEND)+os.write`
  单次原子写零丢失——**单次原子写是分水岭，锁是纵深防御**（防读-改-写交错，非逐条原子性）。
  变异取证（verify3）：移除进程内锁/文件锁后线程测试仍过、2KB payload 1600/1600 无丢——
  锁非丢行根因。进程内锁的**真实角色**（不是「不丢行」）：把同进程线程**先串行、再进
  文件锁**——移除后线程测试仍过（同进程互斥由文件锁的阻塞重试给），但 8 线程×50 追加实测
  7.1s vs 0.43s（约 16×，重试粒度粗）；判据
  `test_inproc_lock_serializes_threads_before_file_lock` 钉死（记录 `_lock_exclusive` 内并发
  进入数，持有进程内锁恒为 1，**移除 `_INPROC_LOCK` 即红**）——保留而非删除。
- `append_text_locked(path, line, *, leading_break=False)`：单行文本追加薄封装，换行用
  `os.linesep`——与旧 `open("a", encoding="utf-8").write(line+"\n")` **逐字节一致**
  （Windows CRLF / POSIX LF），只是加了锁；`leading_break` 承接 #112 尾行补断行守卫。
- `atomic_write_text(path, text)`：整份重写 tmp + `fsync` + `os.replace`（写中崩不留半截）。
- `read_text_retry(path)`：读侧容忍 Windows 字节区间锁造成的**瞬态** `PermissionError`
  （见 §2 耦合发现）。只重试 PermissionError、耗尽仍抛，绝不吞。
- `update_text_locked(path, transform)`：**锁内读-改-写**（唯一实现，w4-3）——同一把锁
  （进程内 `_INPROC_LOCK` + 跨进程文件锁）内完成「读全文 → `transform(old_text)` → 写回」，
  读经**同一个 fd**、在锁内进行（不再有「锁外读撞写锁」这个面）。写回保 append 语义：纯
  尾部追加时只写尾段（单次原子写），否则整份原地覆写（`lseek 0`+写+`ftruncate`+`fsync`）；
  持锁下不能用 tmp+replace（Windows 上目标文件存在任何打开 fd 时报 WinError 5，锁本身要 fd）。

### 1.2 cc-1 / rb-7：五条无锁追加路径接唯一追加器

| 文件 | 原形态 | 现 |
|---|---|---|
| `experience.py` `append_draft` | `open("a")` 逐行写（同 id 合并为锁外读+锁内写） | `update_text_locked`（锁内读-改-写；换行 os.linesep 保字节） |
| `agent.py` `_record` | `journal.open("a")` | `append_text_locked` |
| `review.py` `_decide` | `DECISIONS_FILE.open("a")` | `append_text_locked` |
| `scheduling/state.py` `append_run` | `path.open("a")` | `append_text_locked`（承接 X5 断行守卫） |
| `explore.py` `_record_checkpoint` | `trace.open("a")` | `append_text_locked` |

**第 6 条 `legacy_fast.py:371` 未接（详见 §3 待办 w4-1）**：它是产物侧 **vendored
字节锁零-atlas 垫片**（模块 docstring 明写「旧仓运行时绝不 import atlas 包，本模块禁用
相对 import」，`emit_compat.SHIM_SOURCE_FILES` 按字节 vendor + provenance sha256 锁步），
**结构上不能 import `atlas.atomic_io`**；且它 `_dump` 是**单次 `open("a")` 写整块多行**
（cc-1 复现里「一次 open 写多行丢 0%」），不在丢行类。改它会让 vendor 字节锁与零-atlas
判据同时变红。故不动、记待办。

### 1.3 rb-1 / rb-3 / rb-8：整份写原子化（tmp + os.replace）

- rb-1：`knowledge.py` 四处唯一知识写路径（`save_reviewed` / `save_component_selector` /
  `save_overlay` / `save_task_entry`）由 `write_text` 直写改 `atomic_write_text`——写中崩不再
  截断已批准资产、不再 rc0 假成功。
- rb-3：`probes.py` `save_for_recycle` 回收清单由 `open("w")` 改 `atomic_write_text`。
- rb-8：`session.py:90` storage_state 侧车由 `write_text` 改 `atomic_write_text`。
- 另把 `experience._atomic_write`（审核队列状态改写）委托 `atomic_write_text`——同款模式
  收敛一处（#92），字节形态不变。`state._atomic_write_json` 保留（自带 Windows 目标被占用
  的 PermissionError 重试，行为特异，非本批目标）。

### 1.4 rb-5：flow YAML 坏字节点名

`dsl.FlowCompiler.load` 读侧：`UnicodeDecodeError`（腰斩/写坏）→ 点名文件与字节位的
`ValueError`（旧实现裸 UnicodeDecodeError 直冒，不点名文件）。**有意不 replace**：flow 是
**要执行的真相**，悄悄 replace 修好=跑一份被篡改的流程，比报错更坏（红线 #12 不猜）；
与 JSONL 日志侧 #112 的 replace 回落不同（日志对付「部分证据仍有用」）。好文件逐字节不变。

## 2. 落地中发现的耦合（必须一并处理，否则是新回归）

新锁加在**目标文件头部 1 字节**（与 recorder 同区间=同一把锁）。**Windows 的 `msvcrt`
字节区间锁会挡住并发读**——写锁持有期间，从字节 0 起读整文件的读侧报
`PermissionError [Errno 13]`（POSIX `flock` 是咨询锁不挡读，无此问题）。这是 recon
**cc-2** 记录的形态；读-改-写路径（`experience.append_draft` 先 `_read_queue` 再追加）
落地后**首次实测复现**（`tests/test_atomic_io.py::test_experience_append_draft_no_loss_under_threads`
在加锁前一度失败，`_read_queue` 撞锁）。

处置：加 `atomic_io.read_text_retry`（锁持有是**一次整行写的毫秒级瞬态**，重试 3×50ms 即过；
只重试 `PermissionError`、耗尽仍抛不吞），用于**并发读写同一 JSONL 的三处读侧**：
`experience._read_queue` / `review.rows`（读审核队列）/ `scheduling.state.read_runs`。
**未纳入** `reporting`/`allure_report` 读 `runs.jsonl`（cc-2 的原始目标面）——runs.jsonl
的锁是本批之前的既有形态，属 cc-2 独立范围，本批不扩。

### 2.1 w4-3 真竞态（verify3 复现，非抖动）：读-改-写必须整体进锁

`experience.append_draft` 的「同 id 合并」是**天然读-改-写**。首版只把**写**放进锁、
**读仍是锁外的 `_read_queue`**（靠 `read_text_retry` 兜）——6 线程竞争下读侧撞写锁、
`read_text_retry` 的 3×50ms 预算耗尽 → `PermissionError` 逃出 → **工作线程死亡、丢 19 行**
（循环 150 次实测 10 次失败 = 6.7%；`test_experience_append_draft_no_loss_under_threads`
一度被归因成「时序抖动、非本波引入」——两处均误，实为 w4-3 本项未做）。

处置：新增 `update_text_locked`（§1.1），`append_draft` 的「读全文 → 同 id 合并/追加 →
写回」整体进**同一把锁**——读经**同一个 fd**、在锁内进行，不再有「锁外读撞写锁」这个面。
**不是**「调大读重试预算」：重试预算治标不治本（撞锁是常态，预算只是拖长撞的窗口），
串行化才是修复。判据：`test_experience_append_draft_same_id_merge_under_lock`（8 线程并发
登记同一 id → 恰一条行、`seen==8` 无丢更新）+ 既有 `..._no_loss_under_threads` 原样保留
（不弱化）；50× 循环全绿。

## 3. 后续待办（本批未做）

- **w4-1** `legacy_fast.py` 追加加锁：机制=给 vendored 垫片内联一份锁（或改其 `_dump` 写
  tmp+replace）；为何不做=零-atlas 结构上不能 import 共享实现、单次多行 open 实测 0% 丢、
  改它破 vendor 锁步；建议波次=产物体层专项（需同步 vendor 重生成）。
- **w4-2** cc-2 全量读侧容忍：机制=`reporting.summarize`/`export_runlog`、`allure_report`
  读 `runs.jsonl` 加 `read_text_retry`；为何不做=属 cc-2 独立面、runs.jsonl 锁是既有形态
  非本批引入；建议波次=wave4+1。
- ~~**w4-3** cc-3 审核塔同 id 并发登记竞态（seen 计数丢）~~ **本批已做（§2.1）**：机制=
  `append_draft` 的读-改-写整体进 `update_text_locked`。原「为何不做」不成立——它是真竞态
  （6.7% 丢行），属本批「落盘写入原子性」线内。
- **w4-4** cc-4 审核塔批准态双写竞态（人审与知识写回并发）：机制=批准链整体加锁；建议 wave4+2。
- **w4-5** cc-5 plan_ctx 并发保存丢键：机制=`save_plan_ctx` 读盘合并 + 锁内写；建议 wave4+1。
- **w4-6** cc-6/cc-7/cc-8/cc-9/cc-10（historyId 平台维度 / lease CAS 原子 / exports 路径 /
  xdist 分片门控）：建议 wave4+2。
- **w4-7** rb-2 explore trace 追加补断行守卫（本批只加了锁，未加 `leading_break`）：机制=
  `_record_checkpoint` 追加前照 state X5 检尾字节；建议 wave4+1。
- **w4-8** rb-4 契约文件坏字节静默影子回退点名；**w4-9** rb-6 CDP 冻场被杀进程泄漏文档/信号
  处置：建议 wave4+1 / P2。

## 4. 字节形态与默认路径

- `append_text_locked` / `atomic_write_text` 均与旧文本模式写**逐字节一致**（有 `test_atomic_io.py`
  的 byte-parity 与 write_text parity 两条测钉）。
- runs.jsonl 仍走 recorder 的裸 `\n`（既有 LF 约定），recorder 行为逐字节不变。
- 未设任何新开关；不碰 `snalai/`、不碰四处测试计数、引擎不含业务字面量、凭据不落盘。

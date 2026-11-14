"""落盘写入的**原子/加锁实现单点**（wave4 并发轴 cc-1 + 可靠性轴 rb-1/3/8，#92 收敛先例）。

两条路径，一处实现，消费方只许调（不留第二把尺）：

- `append_line_locked(path, payload)`：**追加**一段字节到 JSONL——进程内 `threading.Lock`
  串行 + 跨进程排他文件锁（Windows `msvcrt` / POSIX `flock`）内**单次整行写**，`O_APPEND`
  保证写位置恒在 EOF。本实现原封取自 `recorder._append_line_locked`（runs.jsonl 是全仓
  唯一实测安全的追加器：裸 CRT 文本模式 `open("a").write()` 对照丢 36%、`os.open(O_APPEND)
  +os.write` 单次原子写零丢失——**单次原子写是唯一分水岭；锁是纵深防御**（防读-改-写
  交错，非逐条原子性），见 `docs/gaps/gap14-*` 与 cc-1 复现）。六条无锁追加路径
  （experience/agent/review/scheduling.state/explore）统一改走它。
- `append_text_locked(path, line, *, leading_break=False)`：单行文本追加的薄封装——
  payload 用 `os.linesep` 拼换行，与旧 `path.open("a", encoding="utf-8").write(line+"\\n")`
  的文本模式换行翻译**逐字节一致**（Windows CRLF / POSIX LF），只是加了锁；`leading_break`
  承接 #112 尾行补断行守卫（残行会把新记录黏成一条坏行）。runs.jsonl **不**走它：recorder
  传裸 `\\n`（既有约定，字节形态跨平台一致）。
- `atomic_write_text(path, text, *, encoding="utf-8")`：**整份重写**——写临时文件 + `fsync`
  + `os.replace` 原子替换。写中崩**不留半截文件**，读者永远只见旧份或新份（审核批准的唯一
  知识写路径 rb-1、回收清单 rb-3、storage_state rb-8 全改走它）。文本模式写，与旧
  `Path.write_text` / `open("w", encoding="utf-8")` 同换行翻译（默认路径逐字节不变）。
- `read_text_retry(path)`：读侧容忍 Windows 字节区间锁造成的**瞬态** `PermissionError`
  （cc-1 读侧配套，recon cc-2 面）。`append_line_locked` 锁在目标文件头部 1 字节，Windows
  上写锁会挡住并发读——并发读写同一 JSONL 的读侧（experience._read_queue / review.rows /
  scheduling.state.read_runs）用它重试；只重试 `PermissionError`、耗尽仍抛，绝不吞。
- `update_text_locked(path, transform)`：**锁内读-改-写**——同一把锁（进程内 + 跨进程）
  内完成「读全文 → `transform(old_text)` → 写回」。承接 w4-3：`append_draft` 的
  「同 id 合并」是天然 RMW，旧实现「锁外 `_read_queue` + 锁内写」在 6 线程竞争下读侧
  撞写锁、`read_text_retry` 150ms 预算耗尽 → `PermissionError` 逃出 → **工作线程死亡丢行**
  （verify3 实测 6.7% 失败）。读也必须进锁：本函数全程持锁，读与写同一临界区。
  写回**保 append 语义**：新文是旧文的「前插无变 + 尾部追加」时只写尾部（单次原子写，
  承接「单次原子写是分水岭」）；否则整份原地覆写（`lseek 0` + 写 + `ftruncate` + `fsync`）。

锁区间固定为**文件头部 1 字节**：互斥要求所有写入方用同一绝对区间（与 recorder 同区间=
同一把锁）；`O_APPEND` 下写恒落 EOF，与锁区间互不干扰。读侧**不取锁**——整行在锁内单次
写完，读侧最坏只见「新行还没出现」，且各读侧本就有残行容错（`update_text_locked` 的读
发生在锁内、用**同一个 fd**，故不受 Windows 读挡锁影响）。Windows 上纯读侧（无 fd 锁）
撞锁属另一轴（cc-2），本模块不改那部分读侧。
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

# 进程内串行：先经进程内 Lock 串行、再进跨进程文件锁。真机实测（verify3 变异）：
# 同进程不同 fd 的 msvcrt.locking 并发是**阻塞重试**（不是直接 EDEADLK），但重试粒度粗——
# 8 线程 × 50 追加：去掉进程内锁耗 7.1s，保留仅 0.43s（约 16×）；同 fd **重入**才 EDEADLK。
# 进程内锁把同进程竞争挡在文件锁之外（快且确定），跨进程互斥仍由文件锁给。
# 判据：tests/test_atomic_io.py::test_inproc_lock_serializes_threads_before_file_lock
# （移除 _INPROC_LOCK 即红）。
_INPROC_LOCK = threading.Lock()

try:
    import msvcrt

    def _lock_exclusive(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _release_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
except ImportError:   # 非 Windows
    import fcntl

    def _lock_exclusive(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _release_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def append_line_locked(path: Path, payload: bytes) -> None:
    """进程内串行 + 跨进程排他锁，锁内整段一次追加；O_APPEND 保证写位置恒在 EOF。

    `payload` 由调用方给全字节（含换行）：本函数用 `O_BINARY` 关掉 CRT 的 `\\n→\\r\\n`
    翻译，原样落盘——换行形态归调用方（recorder 传裸 `\\n`；文本路径经
    `append_text_locked` 传 `os.linesep`）。
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o644)
    try:
        with _INPROC_LOCK:
            _lock_exclusive(fd)
            try:
                view = memoryview(payload)
                while view:                 # os.write 不承诺一次写全，短写循环补齐（仍在锁内）
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                try:
                    _release_lock(fd)
                except OSError:
                    pass   # 解锁失败不顶掉已落盘的证据；锁随 fd 关闭必然释放
    finally:
        os.close(fd)


def append_text_locked(path: Path, line: str, *, leading_break: bool = False) -> None:
    """单行 JSONL 文本原子追加（换行 `os.linesep`，与旧文本模式写逐字节一致）。

    `leading_break=True` 先在行前补一个换行（承接 #112：上一轮腰斩留下无换行尾的残行时，
    新记录独占一行必被读回，不被读侧的「跳过残行」连坐）。
    """
    text = ("\n" if leading_break else "") + line + "\n"
    append_line_locked(Path(path), text.replace("\n", os.linesep).encode("utf-8"))


def update_text_locked(path: Path, transform, *, encoding: str = "utf-8") -> None:
    """**锁内读-改-写**（唯一实现，w4-3）：同一把锁内完成「读全文 → transform → 写回」。

    `transform(old_text: str) -> str | None`：返回 None = 不改（不落盘）；返回 str = 新全文。

    **为什么读也必须进锁**（verify3 实测真竞态，非抖动）：`append_draft` 的同 id 合并是
    天然读-改-写；旧实现「锁外 `_read_queue` + 锁内写」在 6 线程竞争下读侧撞写锁、
    `read_text_retry` 3×50ms 预算耗尽 → `PermissionError` 逃出 → **工作线程死亡、丢行**
    （循环 150 次实测 10 次失败 = 6.7%）。读与写必须在同一临界区内——本函数全程持锁
    （同进程 `_INPROC_LOCK` + 跨进程文件锁都成立）。

    **写回保 append 语义**：新文是旧文的「前插不变 + 尾部追加」（`new.startswith(old)`）时
    只把尾段单次原子追加（承接「单次原子写是分水岭」）；否则整份原地覆写
    （`lseek 0` + 写 + `ftruncate` + `fsync`）。持锁下**不能**用 tmp+replace——Windows 上
    目标文件存在任何打开 fd 时 `os.replace` 报 WinError 5（真机实测），而锁本身需要一个
    打开 fd。读经**同一个 fd**、在锁内进行，故不受 Windows「写锁挡并发读」影响。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o644)
    try:
        with _INPROC_LOCK:
            _lock_exclusive(fd)
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                chunks = []
                while True:                       # 读全文（锁内、同一个 fd）
                    block = os.read(fd, 1 << 16)
                    if not block:
                        break
                    chunks.append(block)
                old_bytes = b"".join(chunks)
                try:
                    old_text = old_bytes.decode(encoding)
                except UnicodeDecodeError:        # #112：腰斩坏字节 replace 读入，残行自然跳过
                    old_text = old_bytes.decode(encoding, errors="replace")
                new_text = transform(old_text)
                if new_text is None:
                    return
                new_bytes = new_text.encode(encoding)
                if new_bytes[:len(old_bytes)] == old_bytes:
                    view = memoryview(new_bytes[len(old_bytes):])   # 纯追加：单次原子写尾段
                    os.lseek(fd, len(old_bytes), os.SEEK_SET)
                else:                                                # 整份原地覆写
                    view = memoryview(new_bytes)
                    os.lseek(fd, 0, os.SEEK_SET)
                while view:                       # os.write 不承诺一次写全，短写循环补齐（仍在锁内）
                    view = view[os.write(fd, view):]
                os.ftruncate(fd, len(new_bytes))
                os.fsync(fd)
            finally:
                try:
                    _release_lock(fd)
                except OSError:
                    pass   # 解锁失败不顶掉已落盘的证据；锁随 fd 关闭必然释放
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """整份文本原子写：tmp + fsync + `os.replace`（写中崩不留半截文件）。

    失败清理：`os.replace` 或 fsync 抛错时，已落盘的 `.tmp` 残档**尽力 unlink**——
    不留脏 tmp 误导后续「看目录判断成功」的消费方（#92 不留半成品假象）。unlink 本身
    失败（文件被占/权限）不顶掉原始异常（原始异常才是真因，清理是尽力而为）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding=encoding) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass   # 清理失败不顶掉原始异常（真因优先）
        raise


_READ_RETRY_ATTEMPTS = 3
_READ_RETRY_DELAY_S = 0.05


def read_text_retry(path: Path, *, encoding: str = "utf-8", errors: str | None = None) -> str:
    """读文本，容忍 Windows 字节区间锁造成的**瞬态** `PermissionError`（cc-1 读侧配套）。

    为什么需要：`append_line_locked` 把锁加在**目标文件头部 1 字节**（与 recorder 同区间
    = 同一把锁）。Windows 的 `msvcrt` 字节区间锁会挡住其他进程对该字节区的**读取**，
    而读侧从字节 0 起读整文件——锁持有期间并发读报 `PermissionError`（POSIX `flock` 是
    咨询锁不挡读，无此问题）。锁持有是**一次整行写的毫秒级瞬态**，撞锁重试即过。

    取向纪律（不吞）：只重试 `PermissionError`，其余异常原样传播；重试耗尽仍抛（极端长
    锁持有不静默吞，如实报错）。POSIX 上恒一次成功、零行为变化。仅三处并发读写同一
    JSONL 的读侧（experience._read_queue / review.rows / scheduling.state.read_runs）用它。
    """
    path = Path(path)
    for attempt in range(_READ_RETRY_ATTEMPTS):
        try:
            return path.read_text(encoding=encoding, errors=errors)
        except PermissionError:
            if attempt == _READ_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_READ_RETRY_DELAY_S)
    raise AssertionError("unreachable")   # for 循环内必 return 或 raise

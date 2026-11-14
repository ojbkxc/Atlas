"""wave4 并发轴 cc-1 + 可靠性轴 rb-1/3/8：落盘写入统一原子 + 实测安全追加器的判据面。

离线：无网络无浏览器；跨进程用同解释器子进程（本地进程，非网络）。
判据面：单次原子写无丢行（线程 + 跨进程）、锁获取次数钉死（防多写实现）、
变异取证（拆成两次写必丢完整记录——证明单次原子写有牙）、锁纵深防御（互斥语义）、
短写补齐、字节形态不变、原子写中断不留半截（负向对照 + 故障注入 + tmp 清理）、
非原子对照（证明判据有牙）、读侧容忍瞬态锁冲突。

**分水岭判据**：裸 CRT 文本模式 `open("a").write()` 丢 36%、`os.open(O_APPEND)+os.write`
单次原子写零丢失。锁是纵深防御（防读-改-写交错），非丢行根因——变异取证已证
（移除锁后线程测试仍过、2KB payload 1600/1600 无丢）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from atlas.atomic_io import append_line_locked, append_text_locked, atomic_write_text

_SRC = Path(__file__).resolve().parents[1] / "src"

# 子进程体：用同一把锁（文件头 1 字节）各追加 N 行——裸 open("a") 的对照在 cc-1 复现里丢 36%。
_APPEND_ONE = (
    "import sys\n"
    "from atlas.atomic_io import append_text_locked\n"
    "p, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])\n"
    "for i in range(n):\n"
    "    append_text_locked(p, tag + '|' + str(i))\n"
)


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ===================== 锁定追加：无丢行 =====================

def test_append_no_loss_under_threads(tmp_path):
    """8 线程各追加 40 行 → 恰 320 行、无重复/交错（单次原子写是分水岭）。"""
    path = tmp_path / "c.jsonl"

    def worker(tag: int) -> None:
        for i in range(40):
            append_text_locked(path, json.dumps({"tag": tag, "i": i}))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = [json.loads(line) for line in _lines(path)]
    assert len(rows) == 8 * 40                                   # 无丢行
    assert len({(r["tag"], r["i"]) for r in rows}) == 8 * 40     # 无重复、无交错残行


def test_append_no_loss_across_processes(tmp_path):
    """跨进程（cc-1 根因面）：4 进程各 25 行 → 恰 100 行，零丢（单次原子写是分水岭）。"""
    path = tmp_path / "x.jsonl"
    env = {**os.environ, "PYTHONPATH": str(_SRC)}
    procs = [subprocess.Popen([sys.executable, "-c", _APPEND_ONE, str(path), f"P{i}", "25"],
                              env=env) for i in range(4)]
    for proc in procs:
        proc.wait()
    assert all(proc.returncode == 0 for proc in procs)
    lines = _lines(path)
    assert len(lines) == 100
    assert len(set(lines)) == 100


def test_append_byte_parity_with_text_mode(tmp_path):
    """逐字节不变：落盘字节 == 旧 `open("a", encoding="utf-8").write(line+"\\n")`。"""
    line = '{"k": "值"}'
    path_new = tmp_path / "new.jsonl"
    append_text_locked(path_new, line)
    path_old = tmp_path / "old.jsonl"
    with path_old.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    assert path_new.read_bytes() == path_old.read_bytes()


def test_append_leading_break_keeps_record_separate(tmp_path):
    """#112 守卫：残行（无尾换行）时补断行，新记录独占一行必被读回。"""
    path = tmp_path / "q.jsonl"
    path.write_bytes(b'{"a":1}')
    append_text_locked(path, '{"a":2}', leading_break=True)
    assert _lines(path) == ['{"a":1}', '{"a":2}']


def test_append_short_write_loop_completes(tmp_path, monkeypatch):
    """变异取证：强制 `os.write` 每次只写 1 字节（短写）——短写循环仍须写全，否则丢字节。"""
    import atlas.atomic_io as aio

    real_write = os.write

    def one_byte(fd, data):
        return real_write(fd, bytes(data[:1]))

    monkeypatch.setattr(aio.os, "write", one_byte)
    path = tmp_path / "s.jsonl"
    append_text_locked(path, '{"long": "payload-abcdefghij"}')
    assert json.loads(_lines(path)[0])["long"] == "payload-abcdefghij"
    assert path.read_text(encoding="utf-8").endswith("}\n") or path.read_text().endswith("}\r\n")


def test_append_one_lock_acquisition_per_record(tmp_path, monkeypatch):
    """钉死形态：每次 `append_line_locked` 恰好 1 次锁获取——防未来改成多次锁获取的多写实现。

    单次原子写的保证 = 1 次锁获取 + O_APPEND 定位 + 锁内短写循环补齐。若未来有人把
    一条记录拆成多次锁获取（每次锁内写一半），本测立刻红——锁获取次数是形态判据，
    不是「不丢行」判据（丢不丢行由 `test_mutation_split_write_loses_records` 钉）。
    """
    import atlas.atomic_io as aio

    path = tmp_path / "lockcount.jsonl"
    lock_calls = {"n": 0}
    real_lock = aio._lock_exclusive

    def counting_lock(fd):
        lock_calls["n"] += 1
        return real_lock(fd)

    monkeypatch.setattr(aio, "_lock_exclusive", counting_lock)
    for i in range(5):
        append_text_locked(path, json.dumps({"i": i}))
    assert lock_calls["n"] == 5, f"5 次追加应恰好 5 次锁获取，实际 {lock_calls['n']}"


def test_mutation_split_write_loses_records(tmp_path):
    """变异取证（负向对照）：一条记录拆成两次独立 `append_line_locked` → 并发下交错丢完整记录。

    两线程各拆不同记录（`{"a":1}` / `{"b":2}`）为前半 + 后半，Barrier(2) 对齐两半起点——
    若两条记录的前半都先落盘，文件变成 `{"a":{"b":1}\n2}\n`（或 `{"b":{"a":2}\n1}\n`），
    按行 split 后**无一条完整记录可读回**。这证明「单次原子写」判据有牙：拆成两次锁内写
    必丢，锁内一次写必不丢。
    """
    import threading as th

    path = tmp_path / "mut.jsonl"
    # 两条不同记录，各拆两半——避免相同半段意外拼回完整记录
    r1_h1, r1_h2 = b'{"a":', b'1}\n'
    r2_h1, r2_h2 = b'{"b":', b'2}\n'
    barrier = th.Barrier(2)

    def split_worker(h1, h2):
        barrier.wait()                          # 对齐：两线程同时起跑
        append_line_locked(path, h1)            # 前半
        barrier.wait()                          # 对齐：两线程都写完前半
        append_line_locked(path, h2)            # 后半

    t1 = th.Thread(target=split_worker, args=(r1_h1, r1_h2))
    t2 = th.Thread(target=split_worker, args=(r2_h1, r2_h2))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    raw = path.read_bytes()
    lines = [l for l in raw.decode("utf-8").splitlines() if l.strip()]
    # 按行 split 后无一条完整记录——前半+前半黏成一行，后半+后半各成残行
    assert '{"a":1}' not in lines, f"拆成两次写必丢完整记录——若在则判据无牙: {lines}"
    assert '{"b":2}' not in lines, f"拆成两次写必丢完整记录——若在则判据无牙: {lines}"
    # 反向对照：若两条记录各写一次（不拆），两行都在——证明本测不是恒假
    path2 = tmp_path / "mut_ok.jsonl"
    append_text_locked(path2, '{"a":1}')
    append_text_locked(path2, '{"b":2}')
    ok_lines = _lines(path2)
    assert '{"a":1}' in ok_lines and '{"b":2}' in ok_lines


def test_inproc_lock_blocks_concurrent_append(tmp_path, monkeypatch):
    """锁的纵深防御：t1 持锁期间 t2 的追加被阻塞（互斥语义，非「不丢行」）。

    注入慢 `os.write`（Event 控制放行）使 t1 在锁内悬挂，t2 的追加必须阻塞到 t1 释放。
    本测钉的是锁的**互斥语义**——变异取证已证锁非丢行根因，但锁防的是读-改-写交错。
    """
    import threading as th
    import atlas.atomic_io as aio

    path = tmp_path / "block.jsonl"
    t1_holds_lock = th.Event()
    t1_release = th.Event()

    real_write = os.write

    def slow_write_for_t1(fd, data):
        if not t1_holds_lock.is_set():
            t1_holds_lock.set()
            t1_release.wait(timeout=5)          # t1 在锁内悬挂
        return real_write(fd, data)

    # 只让 t1 的第一次 write 慢——用 call count 区分
    write_count = {"n": 0}

    def counted_slow_write(fd, data):
        write_count["n"] += 1
        if write_count["n"] == 1:
            return slow_write_for_t1(fd, data)
        return real_write(fd, data)

    monkeypatch.setattr(aio.os, "write", counted_slow_write)

    t1 = th.Thread(target=append_text_locked, args=(path, '{"t1":1}'))
    t1.start()
    assert t1_holds_lock.wait(timeout=5), "t1 应进入慢写悬挂"

    t2_done = th.Event()

    def t2_worker():
        append_text_locked(path, '{"t2":1}')
        t2_done.set()

    t2 = th.Thread(target=t2_worker)
    t2.start()
    # t2 应被锁阻塞——给它一点时间，断言它还没完成
    import time
    time.sleep(0.2)
    assert not t2_done.is_set(), "t2 应被锁阻塞（t1 持锁期间）"
    assert t2.is_alive(), "t2 应仍存活（阻塞在锁上）"

    t1_release.set()                            # 放行 t1
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert t2_done.is_set(), "t1 释放后 t2 应完成"


def test_inproc_lock_serializes_threads_before_file_lock(tmp_path, monkeypatch):
    """钉死进程内锁的真实角色：同进程多线程**先经内存锁串行、再进跨进程文件锁**。

    verify3 变异「移除 _INPROC_LOCK」下线程测试仍过——互斥其实由文件锁给（msvcrt
    LK_LOCK 阻塞重试）。故进程内锁不是「不丢行」的成因，它挡的是**同进程线程同时阻塞
    在文件锁上重试**（真机实测 8 线程×50 追加：去掉 7.1s、保留 0.43s，约 16×；重试粒度
    粗，极端竞争会耗尽重试抛 EDEADLK——那时才成真丢行）。本测是它的**形态判据**：记录
    `_lock_exclusive` 内的并发进入数——持有进程内锁时恒为 1（后来者被挡在 `_INPROC_LOCK`
    之外），移除 `_INPROC_LOCK` 后可观测到 >1（多线程同时进入并在文件锁上阻塞重试）。
    **移除 `with _INPROC_LOCK` 即红**——这是「保留它」而非「删掉它」的依据。
    """
    import threading as th
    import time
    import atlas.atomic_io as aio

    path = tmp_path / "serialize.jsonl"
    real_lock_exclusive = aio._lock_exclusive
    state = {"now": 0, "max": 0}
    guard = th.Lock()

    def observing_lock(fd):
        with guard:                             # 只护计数，本身不加序列化
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        try:
            time.sleep(0.05)                    # 拉长临界区，放大「谁与谁同时在锁内」的窗口
            return real_lock_exclusive(fd)
        finally:
            with guard:
                state["now"] -= 1

    monkeypatch.setattr(aio, "_lock_exclusive", observing_lock)

    barrier = th.Barrier(6)

    def worker(i):
        barrier.wait()                          # 六线程同时起跑，清掉「错峰进入」的偶然性
        append_text_locked(path, json.dumps({"i": i}))

    threads = [th.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(_lines(path)) == 6
    assert state["max"] == 1, (
        f"`_lock_exclusive` 内并发进入数应为 1（进程内锁把同进程线程挡在文件锁之外），"
        f"实测 {state['max']}——移除 _INPROC_LOCK 会让多线程同时进入文件锁阻塞重试")


# ===================== 原子写：不留半截 =====================

def test_atomic_write_text_writes_normally(tmp_path):
    path = tmp_path / "d.json"
    atomic_write_text(path, '{"a": 1}')
    assert path.read_text(encoding="utf-8") == '{"a": 1}'
    assert not (tmp_path / "d.json.tmp").exists()                # tmp 已被 replace 消费


def test_atomic_write_text_byte_parity_with_write_text(tmp_path):
    """逐字节不变：atomic_write_text 输出 == `Path.write_text`（同换行翻译）。"""
    text = json.dumps({"k": "值", "n": 1}, ensure_ascii=False, indent=2)
    p_atomic = tmp_path / "a.json"
    p_plain = tmp_path / "b.json"
    atomic_write_text(p_atomic, text)
    p_plain.write_text(text, encoding="utf-8")
    assert p_atomic.read_bytes() == p_plain.read_bytes()


def test_atomic_write_text_keeps_old_on_replace_failure(tmp_path, monkeypatch):
    """负向对照：替换失败 → 目标保持旧内容（绝不半截/空文件）。"""
    import atlas.atomic_io as aio

    path = tmp_path / "assets.json"
    path.write_text('{"assets": ["approved-1"]}', encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace boom")

    monkeypatch.setattr(aio.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(path, '{"assets": ["approved-2"]}')
    assert path.read_text(encoding="utf-8") == '{"assets": ["approved-1"]}'   # 旧资产完好


def test_atomic_write_text_keeps_old_on_fsync_failure(tmp_path, monkeypatch):
    """故障注入（写中 fsync 崩）：目标从未被触碰（写全在 tmp 上），半写不落目标。"""
    import atlas.atomic_io as aio

    path = tmp_path / "assets.json"
    path.write_text("OLD", encoding="utf-8")

    def boom(fd):
        raise OSError("fsync boom")

    monkeypatch.setattr(aio.os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_text(path, "NEW")
    assert path.read_text(encoding="utf-8") == "OLD"


def test_atomic_write_text_cleans_tmp_on_replace_failure(tmp_path, monkeypatch):
    """tmp 清理：`os.replace` 失败后 `.tmp` 残档被 unlink——不留脏 tmp 误导消费方。"""
    import atlas.atomic_io as aio

    path = tmp_path / "clean.json"
    path.write_text("OLD", encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace boom")

    monkeypatch.setattr(aio.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(path, "NEW")
    assert not (tmp_path / "clean.json.tmp").exists(), "replace 失败后 tmp 应被清理"


def test_atomic_write_text_cleans_tmp_on_fsync_failure(tmp_path, monkeypatch):
    """tmp 清理：fsync 失败后 `.tmp` 残档被 unlink——写中崩不留半成品假象。"""
    import atlas.atomic_io as aio

    path = tmp_path / "clean2.json"
    path.write_text("OLD", encoding="utf-8")

    def boom(fd):
        raise OSError("fsync boom")

    monkeypatch.setattr(aio.os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_text(path, "NEW")
    assert not (tmp_path / "clean2.json.tmp").exists(), "fsync 失败后 tmp 应被清理"


def test_negative_control_nonatomic_write_destroys_old_on_interrupt(tmp_path):
    """反向对照（给上面两条原子测配牙）：非原子 `open("w")` 先 O_TRUNC 抹旧内容，
    写中中断 → 旧资产不可恢复。原子写要消灭的恰是这一形态，故此判据不是恒真。"""
    path = tmp_path / "assets.json"
    path.write_text('{"assets": ["approved-1"]}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        with path.open("w", encoding="utf-8") as stream:      # O_TRUNC：旧内容当场没
            stream.write("{")
            raise RuntimeError("interrupted mid-write")
    assert path.read_text(encoding="utf-8") == "{"           # 旧资产没了 —— 非原子的坏形态


# ===================== 接线面：真实写入路径走同一把刀 =====================

def test_experience_append_draft_no_loss_under_threads(tmp_path):
    """cc-1 集成：ExperienceJournal.append_draft 并发登记不同 id → 无丢行（审核塔素材不消失）。"""
    from atlas.experience import ExperienceJournal

    journal = ExperienceJournal(tmp_path / "exp")

    def worker(tag: int) -> None:
        for i in range(20):
            journal.append_draft({"id": f"{tag}-{i}", "status": "pending_review", "task": "t",
                                  "exception_type": "", "failure_class": "x",
                                  "pitfall_id": "", "kind": "pitfall"})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_lines(tmp_path / "exp" / "experience_drafts.jsonl")) == 6 * 20


def test_experience_append_draft_same_id_merge_under_lock(tmp_path):
    """w4-3 根因面：同 id 合并是天然读-改-写——读-改-写整体进锁，8 线程并发登记同一 id
    恰得**一条**行且 seen==8（无丢更新、无丢行）。

    旧实现「锁外 `_read_queue` + 锁内写」在竞争下两条腿都会坏：读侧撞写锁 →
    `read_text_retry` 预算耗尽 → `PermissionError` 逃出 → 工作线程死亡丢行；
    或两个线程都读到 seen==1 各自写回 seen==2 → 丢更新。verify3 循环 150 次实测 10 次
    失败（6.7%）。本测是修复后**行为判据**（旧实现下 flaky 红）。
    """
    from atlas.experience import ExperienceJournal
    import threading as th

    journal = ExperienceJournal(tmp_path / "exp")
    n = 8
    barrier = th.Barrier(n)

    def worker():
        barrier.wait()                          # 八线程同时登记同一 id，逼出读-改-写交错
        journal.append_draft({"id": "same", "status": "pending_review", "task": "t",
                              "exception_type": "", "failure_class": "x",
                              "pitfall_id": "", "kind": "pitfall"})

    threads = [th.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = [json.loads(line) for line in _lines(tmp_path / "exp" / "experience_drafts.jsonl")]
    assert len(rows) == 1, f"同 id 并发登记只应有一条行，实得 {len(rows)}"
    assert rows[0]["seen"] == n, f"seen 应为 {n}（无丢更新），实得 {rows[0].get('seen')}"


def test_experience_append_draft_byte_parity_with_plain_text(tmp_path):
    """追加路径逐字节不变：`append_draft` 落盘 == 旧文本模式 `json+os.linesep` 直写
    （默认路径零涟漪——换行仍 os.linesep，不被锁内裸字节写改成 LF）。"""
    from atlas.experience import ExperienceJournal

    journal = ExperienceJournal(tmp_path / "exp")
    draft = {"id": "x1", "status": "pending_review", "task": "保存单据",
             "exception_type": "", "failure_class": "x", "pitfall_id": "", "kind": "pitfall"}
    journal.append_draft(dict(draft))
    got = (tmp_path / "exp" / "experience_drafts.jsonl").read_bytes()
    expected = (json.dumps(draft, ensure_ascii=False) + os.linesep).encode("utf-8")
    assert got == expected


def test_knowledge_save_reviewed_atomic_survives_failure(tmp_path, monkeypatch):
    """rb-1 集成：批准写回在替换失败时不丢已批准资产（旧内容完好，命令可重试）。"""
    import atlas.atomic_io as aio
    from atlas.knowledge import KnowledgeBase

    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [{"id": "p1"}], approved=True)
    before = (tmp_path / "pitfalls.json").read_bytes()

    def boom(src, dst):
        raise OSError("replace boom")

    monkeypatch.setattr(aio.os, "replace", boom)
    with pytest.raises(OSError):
        kb.save_reviewed("pitfalls", [{"id": "p2"}], approved=True)
    assert (tmp_path / "pitfalls.json").read_bytes() == before     # 旧资产完好、无静默丢失


# ===================== 读侧容忍（cc-1 读侧配套）：只容忍瞬态锁冲突 =====================

def test_read_text_retry_tolerates_transient_permission_error(tmp_path, monkeypatch):
    """前两次 PermissionError（瞬态锁冲突）、第三次成功 → 读到内容。"""
    import atlas.atomic_io as aio

    path = tmp_path / "r.jsonl"
    path.write_text("line\n", encoding="utf-8")
    real_read = Path.read_text
    calls = {"n": 0}

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "locked")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr(aio, "_READ_RETRY_DELAY_S", 0.0)
    assert aio.read_text_retry(path) == "line\n"
    assert calls["n"] == 3


def test_read_text_retry_propagates_non_permission_error(tmp_path, monkeypatch):
    """负向对照：非 PermissionError 原样传播（绝不吞）。"""
    import atlas.atomic_io as aio

    def boom(self, *a, **k):
        raise ValueError("not a lock issue")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(ValueError):
        aio.read_text_retry(tmp_path / "x")


def test_read_text_retry_reraises_after_exhaustion(tmp_path, monkeypatch):
    """重试耗尽仍抛 PermissionError（极端长锁持有不静默吞、不返回空串冒充成功）。"""
    import atlas.atomic_io as aio

    monkeypatch.setattr(aio, "_READ_RETRY_DELAY_S", 0.0)

    def always_locked(self, *a, **k):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(Path, "read_text", always_locked)
    with pytest.raises(PermissionError):
        aio.read_text_retry(tmp_path / "x")


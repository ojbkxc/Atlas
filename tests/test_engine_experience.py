import json
import pytest
from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.config import AtlasConfig
from atlas.engine import ExecutionEngine
from atlas.experience import ExperienceJournal
from atlas.review import ReviewQueue
from atlas.session import ApiEvidence
from atlas.recorder import RunRecorder


def test_failure_uses_api_evidence_and_journal(tmp_path):
    evidence = ApiEvidence()
    evidence.register(lambda: {"ok": False, "status": 500})
    engine = ExecutionEngine(AtlasConfig(tmp_path), evidence=evidence,
                             journal=ExperienceJournal(tmp_path / "reports"))
    task = FunctionalTask(TaskMetadata("demo", "query"), lambda a: (_ for _ in ()).throw(TimeoutError()), "query")
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)
    assert engine.results[0].failure_class == "env_blocked"
    drafts = (tmp_path / "reports/experience_drafts.jsonl").read_text(encoding="utf-8")
    assert "pending_review" in drafts


def test_evidence_bad_handler_does_not_mask_original_failure(tmp_path):
    """重放是尽力而为的诊断：handler() 重放的正是刚失败的接口，自己再抛错完全可能。
    旧实现在 engine 的 except 块里让 classify 裸抛 → 原 TimeoutError 被 ConnectionError
    顶掉（During handling 链），且 results 不追加、经验草稿不留痕、取证不执行，
    整条失败证据链被一个坏重放器清空。定性器绝不能先于被定性对象死（#89 同红线）。"""
    def broken():
        raise ConnectionError("网关 502")

    evidence = ApiEvidence()
    evidence.register(broken)                                  # 第一条坏
    evidence.register(lambda: {"ok": False, "status": 500})    # 第二条正常定性
    engine = ExecutionEngine(AtlasConfig(tmp_path), evidence=evidence,
                             journal=ExperienceJournal(tmp_path / "reports"))
    task = FunctionalTask(TaskMetadata("demo", "query"),
                          lambda a: (_ for _ in ()).throw(TimeoutError()), "query")
    with pytest.raises(TimeoutError):   # 原异常必须原样传播，不被定性器顶掉
        engine.execute(Actor(), task)
    assert engine.results[0].failure_class == "env_blocked"    # 坏 handler 跳过后仍取到定性
    drafts = (tmp_path / "reports/experience_drafts.jsonl").read_text(encoding="utf-8")
    assert "pending_review" in drafts                          # 证据链不断：草稿仍留痕


def test_evidence_all_handlers_bad_falls_back(tmp_path):
    """全坏（抛错 / 非映射 replay / None）→ classify 返回 None，engine 回落 classify_failure，
    绝不因诊断链崩掉把原异常顶掉。非映射 replay 也跳过（api_evidence 只吃 dict）。"""
    evidence = ApiEvidence()
    evidence.register(lambda: (_ for _ in ()).throw(ConnectionError("x")))  # 抛错
    evidence.register(lambda: [1, 2, 3])                                    # 非映射形态
    evidence.register(lambda: None)                                         # 无重放
    assert evidence.classify(TimeoutError("boom")) is None                   # 全坏不抛，返回 None
    engine = ExecutionEngine(AtlasConfig(tmp_path), evidence=evidence,
                             journal=ExperienceJournal(tmp_path / "reports"))
    task = FunctionalTask(TaskMetadata("demo", "query"),
                          lambda a: (_ for _ in ()).throw(TimeoutError()), "query")
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), task)
    # classify 返回 None → engine 回落 classify_failure(TimeoutError)=interaction
    assert engine.results[0].failure_class == "interaction"


def _torn_queue(tmp_path):
    """复刻真实腰斩：append 写 UTF-8 中文中途 Ctrl-C——好行完整、尾行断在字节上。
    旧 read_text 裸 UnicodeDecodeError，一条坏字节拖死整队好行。"""
    path = tmp_path / "experience_drafts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"id": "a1", "task": "保存单据", "exception_type": "TimeoutError",
                       "failure_class": "interaction", "status": "pending_review",
                       "created_at": "x"}, ensure_ascii=False)
    torn = '{"id":"b2","task":"读取'.encode("utf-8")[:20]   # 多字节汉字腰斩出非法序列
    path.write_bytes(good.encode("utf-8") + b"\n" + torn)
    return path


def test_engine_failure_with_torn_queue_propagates_original_exc(tmp_path):
    """#112：engine 失败路径的 journal.append_draft 是裸调（engine.py:153-155）。
    队列尾行坏字节时旧 read_text 裸 UnicodeDecodeError 崩在 except 块里——
    原用例异常被顶掉（#100 定性器红线）且 results/草稿/取证链全断。
    修复后：TimeoutError 原样传播，新草稿照常落队。"""
    reports = tmp_path / "reports"
    _torn_queue(reports)
    engine = ExecutionEngine(AtlasConfig(tmp_path), journal=ExperienceJournal(reports))
    task = FunctionalTask(TaskMetadata("demo", "query"),
                          lambda a: (_ for _ in ()).throw(TimeoutError()), "query")
    with pytest.raises(TimeoutError):        # 不是 UnicodeDecodeError：真因不被登记器顶掉
        engine.execute(Actor(), task)
    assert engine.results and engine.results[0].failure_class   # 结果链完整
    rows = ReviewQueue(reports).pending()
    assert len(rows) == 2                                # 好行可见 + 新草稿已登记
    assert {"a1"} <= {r["id"] for r in rows}


def test_review_queue_survives_torn_utf8_tail(tmp_path):
    """#112：审核塔读侧与 _read_queue 同防线——坏字节 replace 读入后残行 JSON 解析
    必败、自然落进既有「跳过残行」分支，好草稿不再被一条坏字节整队拖死。"""
    _torn_queue(tmp_path)
    queue = ReviewQueue(tmp_path)
    pending = queue.pending()
    assert [r["id"] for r in pending] == ["a1"]                 # 腰斩前完整行照常可审
    assert pending[0]["task"] == "保存单据"
    assert queue.stats()["pending"] == 1


def test_seen_merge_survives_torn_utf8_tail(tmp_path):
    """#112：同坑复发合并走 _read_queue——坏尾行还在文件里时合并照旧生效，
    原子重写顺带清掉残字节；修复前这一步直接 UnicodeDecodeError 崩登记。"""
    path = _torn_queue(tmp_path)
    journal = ExperienceJournal(tmp_path)
    a1 = json.loads(path.read_bytes().split(b"\n", 1)[0].decode("utf-8"))
    journal.append_draft(dict(a1))         # 同 id pending 复发：读侧必须扛住尾行坏字节
    merged = ReviewQueue(tmp_path).rows()
    assert len(merged) == 1 and merged[0]["seen"] == 2


def test_recorder_close_is_idempotent(tmp_path):
    recorder = RunRecorder("case", "sit", tmp_path / "runs.jsonl")
    recorder.close()
    recorder.close()
    assert len((tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 1

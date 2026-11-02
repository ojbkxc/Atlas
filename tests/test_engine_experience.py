import pytest
from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.config import AtlasConfig
from atlas.engine import ExecutionEngine
from atlas.experience import ExperienceJournal
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


def test_recorder_close_is_idempotent(tmp_path):
    recorder = RunRecorder("case", "sit", tmp_path / "runs.jsonl")
    recorder.close()
    recorder.close()
    assert len((tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 1

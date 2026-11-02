"""执行经验先草稿、后人审；第二次同坑从正式库直接命中。"""
from __future__ import annotations
import json
from pathlib import Path
from .failures import classify_failure


class ExperienceJournal:
    def __init__(self, root: Path):
        self.root = root

    def draft(self, *, task: str, exc: BaseException, pitfall_id: str = "") -> dict:
        return {"task": task, "exception_type": type(exc).__name__,
                "failure_class": classify_failure(exc).value, "pitfall_id": pitfall_id,
                "status": "pending_review"}

    def append_draft(self, experience: dict) -> Path:
        if experience.get("status") != "pending_review":
            raise ValueError("经验日志只接受 pending_review 草稿")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "experience_drafts.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(experience, ensure_ascii=False) + "\n")
        return path

    def approve_pitfall(self, knowledge, draft: dict, *, trigger: str, symptom: str,
                        root_cause: str, remedy: str, scope: list[str], source: str,
                        approved: bool = False) -> dict:
        if not approved:
            raise PermissionError("只有人工审核后才能把经验提升为知识")
        pitfall = knowledge.draft_pitfall(RuntimeError(trigger), symptom=symptom,
            root_cause=root_cause, remedy=remedy, scope=scope, source=source)
        pitfall.update(id="reviewed-" + pitfall["id"].removeprefix("draft-"),
                       trigger=trigger, status="approved")
        rows = knowledge.pitfalls()
        rows.append(pitfall)
        knowledge.save_reviewed("pitfalls", rows, approved=True)
        return pitfall

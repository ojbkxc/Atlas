"""把生成摘要登记为 pending_review 草稿（kind=trace_step，复用 ExperienceJournal）。

不修改 draft(exc) 现有语义（红线级：失败登记不能被生成器改动）。直接构造行字典
走 append_draft（后者已校验 status 与去重）。trace_step 不可 --approve（review.py
设计意图：生成物不是知识，人工写成 flow YAML 后 atlas compile）。
"""
from __future__ import annotations
import hashlib
from typing import Any


def to_tower(journal, *, task: str, flow_id: str, evidence: dict) -> dict:
    """登记 trace_step pending_review 草稿。"""
    identity = f"gen:{flow_id}"
    row_id = hashlib.sha1(identity.encode()).hexdigest()[:12]
    row = {
        "id": row_id,
        "task": task,
        "exception_type": "",
        "failure_class": "generated",
        "pitfall_id": "",
        "kind": "trace_step",
        "status": "pending_review",
        "evidence": evidence,
    }
    journal.append_draft(row)
    return row

"""BU overlay：租户=坐标，不是分支。两组业务坐标的 diff 走审核塔，批准才落成坐标。

口径纪律：真实双跑需要另一 BU 的会话值；本模块的离线管线可先用**明确标注为样本**的
坐标文件跑通（管道成立≠真实 BU 已验证）。未经人审的坐标差异只是 pending_review 原矿。
"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path

KIND = "bu_overlay"


def load_overlays(path: Path | str) -> dict[str, dict]:
    """文件形态：{"BU-A": {"ou": "...", "division": "..."}, "BU-B(sample)": {...}}。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError('overlay 文件口径：{"BU名": {坐标键: 值, ...}}')
    return data


def diff_overlays(overlays: dict[str, dict], base_bu: str, other_bu: str) -> list[dict]:
    """值不同或单侧缺失都输出；缺失坐标也是事实，不许静默吞。"""
    if base_bu not in overlays or other_bu not in overlays:
        raise KeyError(f"BU 不存在：{base_bu!r}/{other_bu!r}，现有：{sorted(overlays)}")
    a, b = overlays[base_bu], overlays[other_bu]
    return [{"key": key, "base": a.get(key), "other": b.get(key)}
            for key in sorted(set(a) | set(b)) if a.get(key) != b.get(key)]


def overlay_draft(journal, base_bu: str, other_bu: str, diff: dict) -> dict:
    row = {"id": hashlib.sha1(f"overlay:{diff['key']}:{base_bu}:{other_bu}"
                              .encode("utf-8")).hexdigest()[:12],
           "task": f"overlay:{diff['key']}", "kind": KIND, "exception_type": "",
           "failure_class": "bu_overlay", "pitfall_id": "", "status": "pending_review",
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "evidence": {"base_bu": base_bu, "other_bu": other_bu,
                        "base_value": diff["base"], "other_value": diff["other"]}}
    journal.append_draft(row)
    return row


def to_tower(journal, path: Path | str, base_bu: str, other_bu: str) -> dict:
    """diff → 逐键 pending_review 草稿进塔；批准仍是 atlas review 的人工决定。"""
    diffs = diff_overlays(load_overlays(path), base_bu, other_bu)
    drafts = [overlay_draft(journal, base_bu, other_bu, d) for d in diffs]
    return {"base": base_bu, "other": other_bu, "diffs": len(diffs),
            "draft_ids": [d["id"] for d in drafts]}

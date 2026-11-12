"""pitfall_review 脚本：迁移坑点 → 审核塔导入 / 人工就地启用。

离线、无网络：知识库与队列都在 tmp 目录，绝不碰真实私有库。
"""
import json

import pytest

from scripts.pitfall_review import (
    BLOCKED_IDS, decision_row, draft_row, is_blocked, load_pending,
    run_enable, run_import, run_report)
from atlas.knowledge import KnowledgeBase
from atlas.review import ReviewQueue


def _entry(pid="isvc-001", *, status="pending_review", severity="medium"):
    return {"id": pid, "trigger": "challengeCode", "symptom": "登录卡住",
            "root_cause": "SSO 无验证码", "remedy": "SIT 全自动 do_login",
            "scope": ["iservice", "login", "sit"], "severity": severity,
            "source": "SYSTEM.md §1", "status": status}


def _write_kb(tmp_path, entries):
    root = tmp_path / "kb"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pitfalls.json").write_text(
        json.dumps({"pitfalls": entries}, ensure_ascii=False), encoding="utf-8")
    return KnowledgeBase(root), root


# --- 纯函数 ---------------------------------------------------------------

def test_load_pending_filters_by_status_and_bad_rows():
    data = {"pitfalls": [_entry("a"), _entry("b", status="approved"),
                         _entry("c", status="rejected"), "not-a-dict", 42]}
    assert [r["id"] for r in load_pending(data)] == ["a"]


def test_load_pending_rejects_bad_toplevel():
    with pytest.raises(ValueError):
        load_pending(["not", "a", "dict"])
    with pytest.raises(ValueError):
        load_pending({"pitfalls": "not-a-list"})


def test_draft_row_maps_fields_for_review_card():
    row = draft_row(_entry())
    assert row["id"] == "isvc-001"
    assert row["task"] == "iservice"            # 误走 review --approve 时兜 scope 的域标签
    assert row["exception_type"] == "challengeCode"
    assert row["kind"] == "pitfall"
    assert row["status"] == "pending_review"
    ev = row["evidence"]
    assert ev["migrated_pitfall_id"] == "isvc-001"
    assert "SIT 全自动 do_login" in ev["text"]   # remedy 进证据文本供人审
    assert ev["scope"] == ["iservice", "login", "sit"]
    assert ev["blocked_no_auto_heal"] is False


def test_draft_row_marks_blocked_entries():
    row = draft_row(_entry("isvc-063"))
    assert row["evidence"]["blocked_no_auto_heal"] is True
    assert row["evidence"]["blocked_note"]


def test_draft_row_survives_bad_shapes():
    # scope 非列表 / 字段 None / 数字：归空或字符串，绝不裸崩
    row = draft_row({"id": "isvc-999", "scope": None, "trigger": None,
                     "symptom": 5, "remedy": None})
    assert row["id"] == "isvc-999"
    assert row["task"] == "iservice"            # scope 缺 → 回落默认域
    assert row["exception_type"] == ""
    assert row["evidence"]["scope"] == []


def test_decision_row_shape_and_blocked_flag():
    row = decision_row(_entry())
    assert row == {"id": "isvc-001", "task": "iservice", "kind": "pitfall",
                   "remedy": "SIT 全自动 do_login", "reason": ""}
    assert is_blocked("isvc-063") and "isvc-063" in BLOCKED_IDS


# --- report ---------------------------------------------------------------

def test_run_report_counts_and_blocked_list(tmp_path, capsys):
    kb, _ = _write_kb(tmp_path, [_entry("isvc-001"), _entry("isvc-002", severity="high"),
                                 _entry("isvc-063"), _entry("x-1", status="approved")])
    summary = run_report(kb, as_json=True)
    assert summary["pending_total"] == 3
    assert summary["by_severity"] == {"high": 1, "medium": 2}
    assert summary["blocked"] == ["isvc-063"]
    assert set(summary["ids"]) == {"isvc-001", "isvc-002", "isvc-063"}


# --- import ---------------------------------------------------------------

def test_run_import_writes_queue_and_decisions(tmp_path, capsys):
    kb, _ = _write_kb(tmp_path, [_entry("isvc-001"), _entry("isvc-063")])
    reports = tmp_path / "reports"
    result = run_import(kb, reports)
    assert result["created"] == 2 and result["skipped"] == 0
    queue_ids = {r.get("id") for r in ReviewQueue(reports).rows()}
    assert queue_ids == {"isvc-001", "isvc-063"}
    # 决策文件排除阻塞条目（6 条不预填 remedy）
    decisions = (reports / "pitfall_review_decisions.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert [json.loads(d)["id"] for d in decisions] == ["isvc-001"]


def test_run_import_is_idempotent(tmp_path):
    kb, _ = _write_kb(tmp_path, [_entry("isvc-001")])
    reports = tmp_path / "reports"
    run_import(kb, reports)
    again = run_import(kb, reports)
    assert again["created"] == 0 and again["skipped"] == 1
    rows = ReviewQueue(reports).rows()
    assert len([r for r in rows if r.get("id") == "isvc-001"]) == 1


def test_run_import_dry_run_writes_nothing(tmp_path):
    kb, _ = _write_kb(tmp_path, [_entry("isvc-001")])
    reports = tmp_path / "reports"
    result = run_import(kb, reports, dry_run=True)
    assert result["created"] == 1
    assert not (reports / "experience_drafts.jsonl").exists()
    assert not (reports / "pitfall_review_decisions.jsonl").exists()


# --- enable（就地翻 status）-----------------------------------------------

def _approved_ids(root):
    rows = json.loads((root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"]
    return {r["id"] for r in rows if r["status"] == "approved"}


def test_run_enable_flips_status_in_place(tmp_path, capsys):
    kb, root = _write_kb(tmp_path, [_entry("isvc-001"), _entry("isvc-002")])
    reports = tmp_path / "reports"
    run_import(kb, reports)
    result = run_enable(kb, reports, ["isvc-001"], reason="2026-11-12 人审：与实测一致")
    assert result["enabled"] == ["isvc-001"]
    assert _approved_ids(root) == {"isvc-001"}
    # 根因/scope 保真（不是 review --approve 那条回落路径）
    entry = [r for r in json.loads((root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"]
             if r["id"] == "isvc-001"][0]
    assert entry["root_cause"] == "SSO 无验证码"
    assert entry["scope"] == ["iservice", "login", "sit"]
    assert entry["review_reason"] == "2026-11-12 人审：与实测一致"
    # 塔行镜像为 approved
    row = [r for r in ReviewQueue(reports).rows() if r.get("id") == "isvc-001"][0]
    assert row["status"] == "approved" and row["pitfall_ref"] == "isvc-001"


def test_run_enable_refuses_blocked_and_unknown(tmp_path, capsys):
    kb, root = _write_kb(tmp_path, [_entry("isvc-001"), _entry("isvc-063")])
    reports = tmp_path / "reports"
    run_import(kb, reports)
    result = run_enable(kb, reports, ["isvc-063", "nope-9", "isvc-001"],
                        reason="batch")
    assert result["enabled"] == ["isvc-001"]                    # 阻塞/未知被拒，其余照常
    reasons = {r["id"]: r["reason"] for r in result["refused"]}
    assert "环境/产品阻塞" in reasons["isvc-063"]
    assert "未找到" in reasons["nope-9"]
    assert _approved_ids(root) == {"isvc-001"}


def test_run_enable_force_blocked(tmp_path, capsys):
    kb, root = _write_kb(tmp_path, [_entry("isvc-063")])
    reports = tmp_path / "reports"
    run_import(kb, reports)
    result = run_enable(kb, reports, ["isvc-063"], reason="管理员已确认可自愈",
                        force_blocked=True)
    assert result["enabled"] == ["isvc-063"]
    assert _approved_ids(root) == {"isvc-063"}


def test_run_enable_requires_reason(tmp_path):
    kb, _ = _write_kb(tmp_path, [_entry("isvc-001")])
    with pytest.raises(ValueError):
        run_enable(kb, tmp_path / "reports", ["isvc-001"], reason="   ")


def test_run_enable_is_idempotent(tmp_path, capsys):
    kb, root = _write_kb(tmp_path, [_entry("isvc-001")])
    reports = tmp_path / "reports"
    run_enable(kb, reports, ["isvc-001"], reason="first")
    result = run_enable(kb, reports, ["isvc-001"], reason="second")
    assert result["enabled"] == []
    assert "已是 approved" in result["refused"][0]["reason"]
    entry = json.loads((root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"][0]
    assert entry["review_reason"] == "first"     # 二次不覆盖首次留痕


def test_run_enable_dry_run_writes_nothing(tmp_path, capsys):
    kb, root = _write_kb(tmp_path, [_entry("isvc-001")])
    result = run_enable(kb, tmp_path / "reports", ["isvc-001"],
                        reason="preview", dry_run=True)
    assert result["enabled"] == ["isvc-001"]
    assert _approved_ids(root) == set()


def test_run_enable_without_private_kb_refuses(tmp_path):
    # 只读库（root=None）：enable 必须点名拒绝，绝不写包内/虚拟库
    kb = KnowledgeBase(None)
    with pytest.raises(ValueError):
        run_enable(kb, tmp_path / "reports", ["isvc-001"], reason="x")

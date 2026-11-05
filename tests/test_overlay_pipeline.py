"""BU overlay 离线管线：diff → 塔 → 人审批准落私有坐标库。

样本 BU 名一律带 (sample) 后缀——管道验证，非真实 BU 双跑证据。
"""
import json
import pytest
from atlas.experience import ExperienceJournal
from atlas.knowledge import KnowledgeBase
from atlas.overlay import diff_overlays, load_overlays, to_tower
from atlas.review import ReviewQueue

SAMPLE = {
    "BU-A": {"ou": "BU-A System Admin", "division": "DIV-BU-A-001", "customer_pool": "CUST0000001"},
    "BU-B (sample)": {"ou": "BU-B Admin", "division": "DIV-BU-A-001", "pt_default": "NET45"},
}


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "overlays.json"
    path.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_overlays_shape_validation(tmp_path):
    with pytest.raises(ValueError, match="口径"):
        (tmp_path / "bad.json").write_text('[1, 2]', encoding="utf-8")
        load_overlays(tmp_path / "bad.json")


def test_diff_includes_value_change_and_one_sided_keys(sample_file):
    diffs = diff_overlays(load_overlays(sample_file), "BU-A", "BU-B (sample)")
    by_key = {d["key"]: d for d in diffs}
    assert by_key["ou"]["base"] == "BU-A System Admin" and by_key["ou"]["other"] == "BU-B Admin"
    assert by_key["customer_pool"]["other"] is None   # 单侧缺失也是事实
    assert by_key["pt_default"]["base"] is None
    assert "division" not in by_key                    # 相同的键不进 diff


def test_diff_unknown_bu_friendly_error(sample_file):
    with pytest.raises(KeyError, match="BU-B"):
        diff_overlays(load_overlays(sample_file), "BU-A", "BU-B")


def test_to_tower_drafts_are_pending_review(sample_file, tmp_path):
    journal = ExperienceJournal(tmp_path)
    result = to_tower(journal, sample_file, "BU-A", "BU-B (sample)")
    assert result["diffs"] == 3 and len(result["draft_ids"]) == 3
    queue = ReviewQueue(tmp_path)
    rows = queue.pending("bu_overlay")
    assert len(rows) == 3
    assert all(r["status"] == "pending_review" for r in rows)
    assert "BU-B (sample)" in json.dumps(rows, ensure_ascii=False)  # 样本标注随行进塔


def test_approve_overlay_writes_private_store_and_logs(tmp_path):
    journal = ExperienceJournal(tmp_path)
    sample = json.dumps(SAMPLE)
    path = tmp_path / "ov.json"
    path.write_text(sample, encoding="utf-8")
    to_tower(journal, path, "BU-A", "BU-B (sample)")
    queue = ReviewQueue(tmp_path)
    row = next(r for r in queue.pending("bu_overlay") if r["task"] == "overlay:ou")
    kb_root = tmp_path / "kb"
    record = queue.approve(KnowledgeBase(kb_root), row["id"], remedy="BU-B Admin")
    assert record["bu"] == "BU-B (sample)" and record["key"] == "ou"
    saved = json.loads((kb_root / "bu_overlays.json").read_text(encoding="utf-8"))
    assert saved["overlays"]["BU-B (sample)"]["ou"] == "BU-B Admin"
    decided = next(r for r in queue.rows() if r["id"] == row["id"])
    assert decided["overlay_ref"] == "BU-B (sample).ou"
    assert '"approve_overlay"' in (tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8")


def test_overlay_card_shows_coordinate_pair(tmp_path):
    from atlas.review import evidence_card
    journal = ExperienceJournal(tmp_path)
    path = tmp_path / "ov.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    to_tower(journal, path, "BU-A", "BU-B (sample)")
    queue = ReviewQueue(tmp_path)
    card = evidence_card(next(r for r in queue.pending("bu_overlay")
                              if r["task"] == "overlay:pt_default"))
    assert "坐标 :" in card and "NET45" in card and "--approve" in card


def test_overlay_cli_diff_and_to_tower(tmp_path, capsys):
    from atlas.cli import main
    path = tmp_path / "ov.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    rc = main(["--root", str(tmp_path), "overlay", str(path),
               "--diff", "BU-A", "BU-B (sample)"])
    assert rc == 0
    assert len(json.loads(capsys.readouterr().out)["diffs"]) == 3
    rc = main(["--root", str(tmp_path), "overlay", str(path),
               "--diff", "BU-A", "NOPE"])
    assert rc == 2  # 不存在的 BU：友好报错不写塔
    rc = main(["--root", str(tmp_path), "overlay", str(path),
               "--diff", "BU-A", "BU-B (sample)", "--to-tower"])
    assert rc == 0
    assert "atlas review --kind bu_overlay" in json.loads(capsys.readouterr().out)["next"]
    assert len(ReviewQueue(tmp_path / "reports").pending("bu_overlay")) == 3


def test_review_kind_choices_include_bu_overlay():
    """CLI choices 曾漏 bu_overlay 导致 --template 报 invalid choice（真机当场抓到），钉死。"""
    from atlas.cli import _parser
    args = _parser().parse_args(["review", "--kind", "bu_overlay"])
    assert args.kind == "bu_overlay"
    args = _parser().parse_args(["review", "--reject-kind", "bu_overlay", "--reason", "x"])
    assert args.reject_kind == "bu_overlay"
    import pytest
    with pytest.raises(SystemExit):
        _parser().parse_args(["review", "--kind", "bogus_kind"])


def test_to_tower_refreshes_stale_evidence_when_value_corrected(tmp_path):
    """#118：bu_overlay 的 id 按「键+两侧 BU」定——坐标文件修正后（BU-B 的 ou B→C）
    id 不变而证据变。旧实现同 id 命中只增 seen、不更新 evidence：审核塔停在过期值 B，
    人审按 --remedy 批准的「目标BU真实值」基于旧证据落成错误坐标（#102/#105 坏形态
    伪装有效结果同族）。修复后同 id 再登记刷新最新证据、seen 聚合不重复占塔。"""
    from atlas.review import ReviewQueue
    journal = ExperienceJournal(tmp_path)
    path = tmp_path / "ov.json"
    path.write_text(json.dumps({"BU-A": {"ou": "A"}, "BU-B": {"ou": "B"}}), encoding="utf-8")
    first = to_tower(journal, path, "BU-A", "BU-B")
    # 坐标修正：BU-B 的 ou 是 C 不是 B
    path.write_text(json.dumps({"BU-A": {"ou": "A"}, "BU-B": {"ou": "C"}}), encoding="utf-8")
    second = to_tower(journal, path, "BU-A", "BU-B")
    assert first["draft_ids"] == second["draft_ids"]      # 同键同 BU 对仍是一条待审项
    rows = ReviewQueue(tmp_path).pending("bu_overlay")
    assert len(rows) == 1, "不重复占审核负担（seen 聚合语义保持）"
    assert rows[0]["seen"] == 2                           # 复登记计数不丢
    assert rows[0]["evidence"]["other_value"] == "C", "塔内证据必须是最新坐标，不是过期的 B"


def test_append_draft_refreshes_evidence_only_when_provided(tmp_path):
    """#118 兼容面：无 evidence 的草稿（engine 失败路径 pitfall 登记）同 id 重登只增 seen，
    不得用缺省键把已有 evidence 抹掉；有 evidence 时等值覆盖无害（同异常派生）。"""
    journal = ExperienceJournal(tmp_path)
    row_a = {"id": "x1", "task": "t", "status": "pending_review",
             "evidence": {"k": "v1"}}
    journal.append_draft(row_a)
    journal.append_draft({"id": "x1", "task": "t", "status": "pending_review"})  # 不带 evidence
    queue = json.loads((tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8"))
    assert queue["evidence"] == {"k": "v1"}, "缺省 evidence 不得抹掉已有证据"
    assert queue["seen"] == 2
    journal.append_draft({"id": "x1", "task": "t", "status": "pending_review",
                          "evidence": {"k": "v2"}})
    queue = json.loads((tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8"))
    assert queue["evidence"] == {"k": "v2"} and queue["seen"] == 3


def test_diff_sound_boundary_no_false_mutation(sample_file, tmp_path):
    """#118 复核面（sound 如实入档）：值相同的键不进 diff；单侧缺失输出 None——
    「缺失」与「值为 null」在坐标语义上同为「无坐标」，审核者看 base/other 是否任一为
    None 即可判断，不制造第二份噪音；不存在的 BU 点名现有清单（友好报错）。"""
    overlays = load_overlays(sample_file)
    diffs = diff_overlays(overlays, "BU-A", "BU-B (sample)")
    assert all(d["base"] != d["other"] for d in diffs), "值相同的键绝不进 diff"
    with pytest.raises(KeyError, match="现有"):
        diff_overlays(overlays, "BU-A", "GHOST")


def test_bu_overlay_template_carries_task_context(tmp_path):
    """坐标模板行必须带 overlay:<键> 上下文——人填 remedy 才知道哪行填哪个坐标。"""
    from atlas.review import ReviewQueue
    journal = ExperienceJournal(tmp_path)
    path = tmp_path / "ov.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    to_tower(journal, path, "BU-A", "BU-B (sample)")
    rows = ReviewQueue(tmp_path).template_rows("bu_overlay")
    assert all(r["task"].startswith("overlay:") and r["remedy"] == "" for r in rows)

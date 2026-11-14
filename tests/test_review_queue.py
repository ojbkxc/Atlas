"""审核塔：队列、去重、证据卡片、批准入知识/组件库、驳回留痕、吞吐统计。"""
import hashlib
import json
import pytest
from atlas.experience import ExperienceJournal
from atlas.knowledge import KnowledgeBase
from atlas.review import ReviewQueue, evidence_card


def _seed(tmp_path):
    """塞两类草稿：一条 pitfall、一条 selector_candidate。

    C2（design_c2 勘误补丁 3）：seed 草稿携带 evidence.verification 真三元组喂晋级闸。
    verdict=fail 合法——坑点/候选草稿的证据是「失败被真实复现过」，闸只查完备形态不要求
    必须绿（红线 #7 本意）。**只喂闸，不放宽任何断言**：各测的期望值一字未改。
    """
    journal = ExperienceJournal(tmp_path)
    verification = {"command": "python -m pytest tests/test_review_queue.py -k tc3",
                    "exit_code": 1, "assertions": 12, "verdict": "fail"}
    p = journal.append_draft(journal.draft(task="click_save", exc=TimeoutError("save hung"),
                                           pitfall_id="demo-014",
                                           evidence={"verification": verification}))
    journal.append_draft({"id": "sel12345678ab", "task": "Save 按钮", "kind": "selector_candidate",
                          "role": "button", "name": "Save", "failure_class": "exploration",
                          "status": "pending_review", "created_at": "x",
                          "evidence": {"verification": verification}})
    return p, journal


def test_draft_has_deterministic_id_and_dedup(tmp_path):
    journal = ExperienceJournal(tmp_path)
    d1 = journal.draft(task="click_save", exc=TimeoutError())
    d2 = journal.draft(task="click_save", exc=TimeoutError("different message"))
    assert d1["id"] == d2["id"]  # 同 task+异常类型+分类 → 同 id，与消息文本无关
    path = journal.append_draft(d1)
    journal.append_draft(d2)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["seen"] == 2  # 同坑复发不增加审核负担


def test_reregister_same_id_merges_into_decided_row(tmp_path):
    """#118 去重纪律跨决定态（终验#8 探针重复行事故）：同 id 再登记恰一条。

    旧实现合并判据带 status == "pending_review"——已批准/已驳回行不挡追加，
    二次登记直写旁路式地出第二条同 id 行（磁盘实锤三条探针草稿各两份），且
    reject/approve 的按 id 全行翻转随重复行污染历史决定状态。现决定态行照常
    聚合：只增 seen、刷新 evidence（#118 兼容面：缺省 evidence 键不抹已有证据），
    status 不回翻——批准/驳回是终态记录，复登记不得伪装成新的待审素材。
    """
    journal = ExperienceJournal(tmp_path)
    queue_path = tmp_path / "experience_drafts.jsonl"
    for decided_status in ("rejected", "approved"):
        base = {"id": f"m-{decided_status}", "task": "t", "status": "pending_review",
                "evidence": {"snapshot": "v1"}}
        journal.append_draft(base)
        queue = ReviewQueue(tmp_path)
        rows = queue.rows()
        target = next(r for r in rows if r["id"] == base["id"])
        target["status"] = decided_status                       # 人审处置落定
        queue._rewrite(rows)
        journal.append_draft({**base, "status": "pending_review",
                              "evidence": {"snapshot": "v2"}})  # 同候选再登记
        rows = [json.loads(l) for l in
                queue_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        hits = [r for r in rows if r["id"] == base["id"]]
        assert len(hits) == 1, f"{decided_status} 行不挡幂等：绝不追加第二条"
        assert hits[0]["status"] == decided_status, "决定态不回翻成 pending"
        assert hits[0]["seen"] == 2 and hits[0]["evidence"] == {"snapshot": "v2"}
    # 决定态聚合不进 pending 列表——复登记不制造第二条审核负担
    assert [r["id"] for r in ReviewQueue(tmp_path).pending()] == []


def test_reject_only_flips_pending_not_decided_history(tmp_path):
    """防回退（终验#8 清塔误伤）：同 id 一行 approved 一行 pending，reject 只翻 pending。

    旧 reject 按 id 翻转全部同 id 行：驳回同名 pending 重复行时，把先前已批准的
    历史行也静默改写成 rejected（磁盘实锤：11-04 approve_selector 两条在 11-06
    清塔后 decided_at/status 全被覆盖）。决定态是终态记录，不可被后续同名操作
    回翻——与 append_draft「决定态不回翻」、reject_kind 的 _is_pending 过滤同纪律，
    单条 reject 是同族漏网。重复行形态由手工构造（append_draft 幂等修复后新文件
    不会再出同 id 重复行，legacy 存量仍需这条防线）。
    """
    row = {"id": "dup1111111", "task": "Save 按钮", "kind": "selector_candidate",
           "role": "button", "name": "Save", "failure_class": "exploration",
           "created_at": "2026-11-04T01:23:19"}
    approved = {**row, "status": "approved", "component_ref": "probe_page.save",
                "decided_at": "2026-11-04T01:23:19"}
    pending = {**row, "status": "pending_review"}       # 重复直写产物：同 id 新行
    (tmp_path / "experience_drafts.jsonl").write_text(
        json.dumps(approved, ensure_ascii=False) + "\n"
        + json.dumps(pending, ensure_ascii=False) + "\n", encoding="utf-8")
    queue = ReviewQueue(tmp_path)
    queue.reject("dup1111111", reason="probe 素材")
    rows = queue.rows()
    old = next(r for r in rows if "component_ref" in r)
    new = next(r for r in rows if r.get("reject_reason") == "probe 素材")
    assert old["status"] == "approved", "已批准历史行纹丝不动（不被同名驳回回翻改写）"
    assert old["decided_at"] == "2026-11-04T01:23:19" and "reject_reason" not in old
    assert new["status"] == "rejected"                 # pending 行正常被驳回
    assert queue.stats()["approved"] == 1 and queue.stats()["rejected"] == 1


_VERIF = {"command": "python -m pytest tests/test_review_queue.py -k dup",
          "exit_code": 1, "assertions": 3, "verdict": "fail"}


def _dup_pair(tmp_path, pending_row: dict) -> str:
    """手工构造同 id 两行：先驳回的历史行（驳回留痕在案）+ 直写产物 pending 行。

    append_draft 幂等修复后新文件不会再出同 id 重复行；此形态专测 legacy 存量
    上的批准动作——与 test_reject_only_flips_pending_not_decided_history 互为镜像。
    """
    old = {**pending_row, "status": "rejected", "reject_reason": "先前驳回",
           "decided_at": "2026-11-04T00:00:00"}
    (tmp_path / "experience_drafts.jsonl").write_text(
        json.dumps(old, ensure_ascii=False) + "\n"
        + json.dumps(pending_row, ensure_ascii=False) + "\n", encoding="utf-8")
    return pending_row["id"]


def _split_history(queue):
    """返回 (被驳回保留的历史行, 被批准翻转的 pending 行)。"""
    rows = queue.rows()
    old = next(r for r in rows if r["status"] == "rejected")
    new = next(r for r in rows if r["status"] == "approved")
    return old, new


def test_approve_pitfall_only_flips_pending_not_rejected_history(tmp_path):
    """③镜像防回退：同 id rejected+pending 两行，批准坑点只翻 pending——
    已驳回历史行的 reject_reason/decided_at 不得被同名批准静默回翻改写。"""
    rid = _dup_pair(tmp_path, {
        "id": "ap-pitfall001", "task": "click_save", "kind": "pitfall",
        "exception_type": "TimeoutError", "failure_class": "timeout", "pitfall_id": "",
        "status": "pending_review", "created_at": "x",
        "evidence": {"verification": _VERIF}})
    queue, kb = ReviewQueue(tmp_path), _kb(tmp_path)
    queue.approve(kb, rid, remedy="点保存前先等 .app-main 出现")
    old, new = _split_history(queue)
    assert old["reject_reason"] == "先前驳回" and old["decided_at"] == "2026-11-04T00:00:00"
    assert "pitfall_ref" not in old, "驳回历史行不得被批注批准痕迹"
    assert new["pitfall_ref"], "pending 行正常批准入知识"
    assert queue.stats()["approved"] == 1 and queue.stats()["rejected"] == 1


def test_approve_selector_only_flips_pending_not_rejected_history(tmp_path):
    """③镜像防回退：selector_candidate 批准只翻 pending；知识侧照常写入（批准语义不变）。"""
    rid = _dup_pair(tmp_path, {
        "id": "ap-sel000001", "task": "button:Save", "kind": "selector_candidate",
        "role": "button", "name": "Save", "failure_class": "exploration",
        "status": "pending_review", "created_at": "x",
        "evidence": {"verification": _VERIF}})
    queue, kb = ReviewQueue(tmp_path), _kb(tmp_path)
    queue.approve(kb, rid, remedy="button.demo-save", component="order_header",
                  element="save_new")
    old, new = _split_history(queue)
    assert old["reject_reason"] == "先前驳回" and old["decided_at"] == "2026-11-04T00:00:00"
    assert "selector_ref" not in old, "驳回历史行不回翻也不得被挂批准引用"
    assert new["selector_ref"] == "order_header.save_new"
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save_new"] == "button.demo-save"


def test_approve_overlay_only_flips_pending_not_rejected_history(tmp_path):
    """③镜像防回退：bu_overlay 批准只翻 pending；坐标照常写 overlay 库。"""
    rid = _dup_pair(tmp_path, {
        "id": "ap-ovl000001", "task": "overlay:ou", "kind": "bu_overlay",
        "failure_class": "bu_overlay", "status": "pending_review", "created_at": "x",
        "evidence": {"other_bu": "BU-B", "key": "ou", "base_value": "A",
                     "other_value": "B", "verification": _VERIF}})
    queue, kb = ReviewQueue(tmp_path), _kb(tmp_path)
    queue.approve(kb, rid, remedy="C")
    old, new = _split_history(queue)
    assert old["reject_reason"] == "先前驳回" and old["decided_at"] == "2026-11-04T00:00:00"
    assert "overlay_ref" not in old
    assert new["overlay_ref"] == "BU-B.ou"
    saved = json.loads((kb.root / "bu_overlays.json").read_text(encoding="utf-8"))
    assert "C" in json.dumps(saved, ensure_ascii=False)


def test_resolve_by_prefix_and_approve_writes_knowledge(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    pending = queue.pending()
    assert {r["kind"] for r in pending} == {"pitfall", "selector_candidate"}
    row = queue.resolve(pending[0]["id"][:4])  # 前缀定位
    assert row["status"] == "pending_review"
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    (kb_root / "pitfalls.json").write_text(json.dumps({"pitfalls": []}), encoding="utf-8")
    pitfall = queue.approve_to_pitfall(KnowledgeBase(kb_root), row,
                                       remedy="点保存前先等 .app-main 出现")
    assert pitfall["status"] == "approved" and pitfall["id"].startswith("reviewed-")
    saved = json.loads((kb_root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"]
    assert saved and saved[0]["remedy"] == "点保存前先等 .app-main 出现"
    # 队列状态同步迁移 + 决策留痕
    assert queue.stats()["approved"] == 1 and queue.stats()["pending"] == 1
    decisions = (tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert '"approve"' in decisions


def test_approve_requires_remedy(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    row = queue.pending()[0]
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    (kb_root / "pitfalls.json").write_text(json.dumps({"pitfalls": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="remedy"):
        queue.approve_to_pitfall(KnowledgeBase(kb_root), row, remedy="")


def test_reject_keeps_audit_trail_and_clears_pending(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    target = queue.pending()[0]
    queue.reject(target["id"], reason="已有坑点覆盖")
    assert queue.stats()["pending"] == 1 and queue.stats()["rejected"] == 1
    text = (tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8")
    assert "已有坑点覆盖" in text  # 驳回不删记录，保留可回溯
    decisions = (tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert '"reject"' in decisions


def test_stats_buckets_pending_by_kind_and_class(tmp_path):
    _seed(tmp_path)
    stats = ReviewQueue(tmp_path).stats()
    assert stats["pending_by_kind"] == {"pitfall": 1, "selector_candidate": 1}
    assert stats["pending"] == 2


def test_corrupted_line_does_not_eat_the_queue(tmp_path):
    _seed(tmp_path)
    path = tmp_path / "experience_drafts.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{half-written\n", encoding="utf-8")
    assert len(ReviewQueue(tmp_path).pending()) == 2


def test_legacy_drafts_without_id_are_reviewable(tmp_path):
    """改名前的历史草稿没有 id 字段：补算后可定位可批准，历史积压不烂在队列里。"""
    (tmp_path / "experience_drafts.jsonl").write_text(json.dumps(
        {"task": "click_save", "exception_type": "TimeoutError",
         "failure_class": "interaction", "pitfall_id": "",
         "status": "pending_review"}) + "\n", encoding="utf-8")
    queue = ReviewQueue(tmp_path)
    row = queue.pending()[0]
    assert row["id"] and len(row["id"]) == 12
    assert queue.resolve(row["id"][:6])["task"] == "click_save"


def test_evidence_card_mentions_reproduce_and_approve(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    card = evidence_card(queue.pending()[0])
    assert "atlas run" in card and "--approve" in card and "demo-014" in card
    # 候选行 exception_type 为空：不拼空括号
    sel = next(r for r in queue.pending() if r["kind"] == "selector_candidate")
    assert "（）" not in evidence_card(sel)


def _kb(tmp_path):
    kb_root = tmp_path / "kb"
    kb_root.mkdir(exist_ok=True)
    (kb_root / "pitfalls.json").write_text(json.dumps({"pitfalls": []}), encoding="utf-8")
    return KnowledgeBase(kb_root)


def test_approve_dispatches_selector_candidate_into_components(tmp_path):
    """证据卡片让审 selector_candidate，批准必须进组件库而不是坑点知识。"""
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    kb = _kb(tmp_path)
    sel_id = next(r["id"] for r in queue.pending() if r["kind"] == "selector_candidate")
    record = queue.approve(kb, sel_id, remedy=".app-main button:has(i.demoicon--save)",
                           component="order_header", element="save_new")
    assert record["status"] == "approved" and record["component"] == "order_header"
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save_new"] == \
        ".app-main button:has(i.demoicon--save)"
    row = next(r for r in queue.rows() if r["id"] == sel_id)
    assert row["status"] == "approved" and row["selector_ref"] == "order_header.save_new"
    decisions = (tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert '"approve_selector"' in decisions


def test_approve_selector_requires_component_and_element(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    sel_id = next(r["id"] for r in queue.pending() if r["kind"] == "selector_candidate")
    with pytest.raises(ValueError, match="--component"):
        queue.approve(_kb(tmp_path), sel_id, remedy=".x")


def test_approve_rejects_trace_step_with_guidance(tmp_path):
    """trace 步骤素材不进知识塔：批准必须报错并指路 flow YAML / reject。"""
    journal = ExperienceJournal(tmp_path)
    journal.append_draft({"id": "tr1234567890", "task": "http://demo/list",
                          "kind": "trace_step", "checkpoint": 1,
                          "failure_class": "exploration", "status": "pending_review",
                          "created_at": "x"})
    queue = ReviewQueue(tmp_path)
    with pytest.raises(ValueError, match="flow YAML"):
        queue.approve(_kb(tmp_path), "tr1234567890", remedy="任意")
    card = evidence_card(queue.pending()[0])
    assert "--reject" in card


def test_approve_selector_rejects_non_selector_kind(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    p_id = next(r["id"] for r in queue.pending() if r["kind"] == "pitfall")
    with pytest.raises(ValueError, match="selector_candidate"):
        queue.approve_to_selector(_kb(tmp_path), p_id, component="a", element="b",
                                  selector=".x")


def test_pitfall_id_is_deterministic_across_instances(tmp_path):
    """id 必须用 sha1 而不是内置 hash()：PYTHONHASHSEED 会让跨进程 id 漂移。"""
    kb = _kb(tmp_path)
    d1 = kb.draft_pitfall(RuntimeError("BUG-653"), symptom="tax", root_cause="missing",
                          remedy="fix", scope=["demo"], source="test")
    d2 = KnowledgeBase(tmp_path).draft_pitfall(
        RuntimeError("BUG-653"), symptom="tax", root_cause="missing",
        remedy="fix", scope=["demo"], source="test")
    assert d1["id"] == d2["id"]
    expected = "draft-" + hashlib.sha1(
        "|".join(["tax", "missing", "fix", "demo"]).encode("utf-8")).hexdigest()[:12]
    assert d1["id"] == expected


def test_reapprove_same_content_does_not_duplicate(tmp_path):
    """同内容重复批准：id 相同，正式库替换而非堆第二条。"""
    journal, kb = ExperienceJournal(tmp_path), _kb(tmp_path)
    draft = journal.draft(task="save", exc=RuntimeError("BUG-653"))
    for source in ("first", "second"):
        journal.approve_pitfall(kb, draft, trigger="BUG-653", symptom="tax",
                                root_cause="missing", remedy="fix", scope=["demo"],
                                source=source, approved=True)
    saved = json.loads((kb.root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"]
    assert len(saved) == 1 and saved[0]["source"] == "second"


# ---- CLI 端到端：approve 分发与 kind 过滤走真实参数解析 ----

class _StubPlugin:
    """把包内知识库指到 tmp：CLI e2e 不污染 demo/knowledge 的真实文件。"""
    def __init__(self, kb):
        self._kb = kb
    def knowledge(self):
        return self._kb


def _stub_cli(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    (kb.root / "components.json").write_text(json.dumps(
        {"components": {"order_header": {"selectors": {}}}}), encoding="utf-8")
    monkeypatch.setattr("atlas.cli.resolve_plugin", lambda: _StubPlugin(kb))
    return kb


def test_cli_approve_selector_writes_components(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", "sel12345678ab",
               "--component", "order_header", "--element", "save",
               "--remedy", "button:has(i.demoicon--save)"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"approved": "order_header.save", "kind": "selector_candidate",
                   "task": "Save 按钮"}
    saved = json.loads((tmp_path / "kb" / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save"] == \
        "button:has(i.demoicon--save)"


def test_cli_approve_pitfall_via_cli(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    p_id = next(r["id"] for r in ReviewQueue(reports).pending() if r["kind"] == "pitfall")
    rc = main(["--root", str(tmp_path), "review", "--approve", p_id[:6],
               "--remedy", "点保存前先等 .app-main 出现"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "pitfall" and out["approved"].startswith("reviewed-")


def test_cli_approve_trace_step_exits_with_guidance(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    ExperienceJournal(reports).append_draft(
        {"id": "tr1234567890", "task": "https://sit/x", "kind": "trace_step",
         "checkpoint": 1, "failure_class": "exploration",
         "status": "pending_review", "created_at": "x"})
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", "tr1234567890",
               "--remedy", "任意"])
    assert rc == 2
    assert "flow YAML" in capsys.readouterr().err


def test_cli_approve_unknown_id_friendly_error(tmp_path, monkeypatch, capsys):
    """id 打错不给 traceback：报错友好、退出码 2。"""
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", "zzzz", "--remedy", "x"])
    assert rc == 2
    assert "pending" in capsys.readouterr().err


def test_cli_review_kind_filter(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--kind", "selector_candidate"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "sel12345678ab" in captured.out
    pitfall_ids = [r["id"] for r in ReviewQueue(reports).pending()
                   if r["kind"] == "pitfall"]
    assert all(pid not in captured.out for pid in pitfall_ids)
    assert '"pitfall": 1' in captured.err and '"selector_candidate": 1' in captured.err


# ---- 批量清塔：一类驳回 / 决策模板 / 人填后批量批准 ----

def test_reject_kind_clears_class_with_audit(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    count = queue.reject_kind("selector_candidate", reason="示例探索产物暂不入库")
    assert count == 1
    stats = queue.stats()
    assert stats["pending_by_kind"] == {"pitfall": 1} and stats["rejected"] == 1
    decisions = (tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert '"batch": "selector_candidate"' in decisions  # 逐条留痕且可溯源到批量决定


def test_reject_kind_requires_reason(tmp_path):
    _seed(tmp_path)
    with pytest.raises(ValueError, match="原因"):
        ReviewQueue(tmp_path).reject_kind("selector_candidate", reason="")


def test_template_rows_shape_per_kind(tmp_path):
    _seed(tmp_path)
    ExperienceJournal(tmp_path).append_draft(
        {"id": "tr1234567890", "task": "https://sit/x", "kind": "trace_step",
         "checkpoint": 1, "failure_class": "exploration",
         "status": "pending_review", "created_at": "x"})
    queue = ReviewQueue(tmp_path)
    sel_rows = queue.template_rows("selector_candidate")
    # 模板必须带 task 上下文：只有 id 的模板人无法辨认（真机 --apply 暴露的缺口）
    # gap13：selector_candidate 行另带 role/name（用候选原值预填，免手抄成 CSS）
    assert sel_rows == [{"id": "sel12345678ab", "task": "Save 按钮",
                         "kind": "selector_candidate",
                         "component": "", "element": "", "selector": "",
                         "role": "button", "name": "Save"}]
    pit_rows = queue.template_rows("pitfall")
    assert set(pit_rows[0]) == {"id", "task", "kind", "remedy"}


def test_apply_batch_approves_mixed_kinds(tmp_path):
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    p_id = next(r["id"] for r in queue.pending() if r["kind"] == "pitfall")
    kb = _kb(tmp_path)
    (kb.root / "components.json").write_text(json.dumps(
        {"components": {"order_header": {"selectors": {}}}}), encoding="utf-8")
    result = queue.apply(kb, [
        {"id": p_id, "kind": "pitfall", "remedy": "点保存前先等 .app-main 出现"},
        {"id": "sel12345678ab", "kind": "selector_candidate", "component": "order_header",
         "element": "save", "selector": "button:has(i.demoicon--save)"},
        {"id": "nope", "kind": "pitfall", "remedy": "x"},
    ])
    assert result["applied"] == 2 and len(result["failed"]) == 1
    assert result["failed"][0]["id"] == "nope"
    assert queue.stats()["pending"] == 0
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save"]


def test_apply_non_object_row_is_named_not_batch_killer(tmp_path):
    """#109：人编辑决策文件把整行改成 JSON 数组/null——合法 JSON 但非映射。
    旧实现 row.get 裸 AttributeError 顶掉整批：前面已批准的写回不回滚、后面合法行全不跑、
    CLI 拿不到 failed 清单。docstring 承诺「单行失败不拦其余」必须为真。"""
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    kb = _kb(tmp_path)
    (kb.root / "components.json").write_text(json.dumps(
        {"components": {"order_header": {"selectors": {}}}}), encoding="utf-8")
    result = queue.apply(kb, [
        [],                                                     # 非对象：点名
        {"id": "sel12345678ab", "kind": "selector_candidate", "component": "order_header",
         "element": "save", "selector": "button:x"},            # 坏行之后的合法行仍要跑
    ])
    assert result["applied"] == 1 and len(result["failed"]) == 1
    assert "必须是映射" in result["failed"][0]["error"] and "list" in result["failed"][0]["error"]


def test_approve_exploration_draft_never_yields_empty_trigger(tmp_path):
    """#109：探索类草稿 exception_type=""（compile_trace 原样写入）——旧实现
    row.get("exception_type", "runtime_error") 只在键缺失时兜底，空串原样透出 →
    批准的坑点 trigger 为空，被 #59 完整性防线永久拒绝匹配：批准「成功」、留痕齐全，
    知识却永不生效（死资产假成功）。空串必须与缺失同样回落非空默认。"""
    (tmp_path / "experience_drafts.jsonl").write_text(json.dumps(
        {"id": "pitt00000001", "task": "click_save", "exception_type": "",
         "failure_class": "exploration", "status": "pending_review"}) + "\n",
        encoding="utf-8")
    queue = ReviewQueue(tmp_path)
    kb = _kb(tmp_path)
    pitfall = queue.approve_to_pitfall(kb, "pitt00000001", remedy="刷新后重试")
    assert pitfall["trigger"] == "runtime_error"          # 空串不再原样进正式知识
    # 死资产解除：trigger 非空的坑点可被正常命中（旧实现空 trigger 被 #59 永久拒绝）
    hit = kb.match_pitfall(RuntimeError("runtime_error boom"), "click_save")
    assert hit and hit["id"] == pitfall["id"]
    saved = json.loads((kb.root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"]
    assert saved[0]["trigger"] == "runtime_error"


def test_resolve_empty_ref_is_not_a_wildcard(tmp_path):
    """空引用不得当作通配：str.startswith("") 恒真会命中全部 pending，
    塔里恰好只剩一条时就会把空模板行默认批准掉。空/纯空白引用必须显式拒绝。"""
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    for bad in ("", "   ", "\t"):
        with pytest.raises(KeyError, match="不能为空"):
            queue.resolve(bad)


def test_apply_blank_id_row_is_named_failure_not_silent_approve(tmp_path):
    """--apply 唯一剩一条 pending 时，id 留空的行必须失败点名（批量批准≠自动批准红线）。
    修复前 resolve("") 命中那条唯一 pending 并静默 approve 之——组件库被偷偷写入、pending 清零。"""
    ExperienceJournal(tmp_path).append_draft(
        {"id": "lone000000ab", "task": "Save 按钮", "kind": "selector_candidate",
         "role": "button", "name": "Save", "failure_class": "exploration",
         "status": "pending_review", "created_at": "x"})
    queue = ReviewQueue(tmp_path)
    assert len(queue.pending()) == 1                     # 只剩一条：正是通配命中的危险场景
    kb = _kb(tmp_path)
    (kb.root / "components.json").write_text(json.dumps(
        {"components": {"order_header": {"selectors": {}}}}), encoding="utf-8")
    result = queue.apply(kb, [{"id": "", "kind": "selector_candidate",
                               "component": "order_header", "element": "save",
                               "selector": "button:has(i.x)"}])
    assert result["applied"] == 0 and len(result["failed"]) == 1
    assert "引用" in result["failed"][0]["error"]
    assert queue.stats()["pending"] == 1                  # 那条 pending 没被偷偷批准
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"] == {}   # 组件库未被污染


def test_cli_reject_kind_and_apply(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    # 模板导出 → 人填 → apply，全程 CLI
    template = tmp_path / "mapping.jsonl"
    rc = main(["--root", str(tmp_path), "review", "--kind", "selector_candidate",
               "--template", str(template)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 1
    row = json.loads(template.read_text(encoding="utf-8").splitlines()[0])
    row.update(component="order_header", element="save", selector="button:x")
    template.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    rc = main(["--root", str(tmp_path), "review", "--apply", str(template)])
    assert rc == 0 and json.loads(capsys.readouterr().out)["applied"] == 1
    rc = main(["--root", str(tmp_path), "review", "--reject-kind", "pitfall",
               "--reason", "示例数据事实，不入知识"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"rejected_batch": "pitfall", "count": 1}
    assert ReviewQueue(reports).stats()["pending"] == 0


def test_cli_apply_reports_failures_not_green(tmp_path, monkeypatch, capsys):
    """没填完的模板不能被当成全绿：exit 2 且点名失败行。"""
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    template = tmp_path / "mapping.jsonl"
    template.write_text(json.dumps({"id": "sel12345678ab", "kind": "selector_candidate",
                                    "component": "", "element": "", "selector": ""}) + "\n",
                        encoding="utf-8")
    rc = main(["--root", str(tmp_path), "review", "--apply", str(template)])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] == 0 and "--component" in out["failed"][0]["error"]


def test_cli_apply_survives_truncated_utf8_decision_file(tmp_path, monkeypatch, capsys):
    """#135（#112 读侧同防线第 8 处收口）：决策文件是人可手改的输入边界，尾行被中断腰斩成
    非法 UTF-8 时旧 `read_text(encoding="utf-8")` 整读抛 UnicodeDecodeError——探针实锤（
    cli.py 无 try 包裹）裸 traceback 违反 rc2 口径，且崩在逐行之前，首行本该落地的批准
    一条都没发生（半途崩：审核者无从知道塔动了没有）。现严格读保真、坏字节回落 replace：
    残行 JSON 必败落既有 malformed 计数，截断前的合法行照常批准。"""
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    decision = tmp_path / "decisions.jsonl"
    decision.write_bytes(
        json.dumps({"id": "sel12345678ab", "kind": "selector_candidate",
                    "component": "order_header", "element": "save",
                    "selector": "button:x"}, ensure_ascii=False).encode("utf-8") + b"\n"
        + b'{"id": "x", "remedy": "\xe4\xbd'      # 腰斩的「你」前两字节
    )
    rc = main(["--root", str(tmp_path), "review", "--apply", str(decision)])
    assert rc == 2                                 # 坏尾行计入 failed，不是假绿 rc0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] == 1                     # 崩点前的合法首行照常批准（半途崩收口）
    assert out["failed"] and out["failed"][-1]["error"] == "malformed JSON"
    stats = ReviewQueue(reports).stats()
    assert stats["pending"] == 1                     # _seed 两条：selector 已批，pitfall 仍在塔


# ---- 聚类视图：批量类一屏可决，坑点仍看全卡 ----

def test_grouped_view_clusters_by_source_and_keeps_pitfall_cards(tmp_path):
    from atlas.review import grouped_view
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    rows = queue.pending()
    view = grouped_view(rows)
    # 坑点保留完整证据卡片
    assert "复现 : atlas run" in view
    # 选择器候选压成紧凑表：一行一条 + 批量指引，不再 7 行卡片
    assert "selector_candidate ×1  源:" in view
    assert "sel12345678ab" in view and "--template mapping.jsonl" in view
    assert view.count("快照 :") == 0  # 候选压成紧凑表后不再重复打印卡片里的快照行
    assert "task : click_save" in view  # pitfall 仍走全卡片
    # 空队列
    assert grouped_view([]) == "审核队列清空。"


def test_grouped_view_groups_multiple_sources(tmp_path):
    from atlas.review import grouped_view
    journal = ExperienceJournal(tmp_path)
    for i, src in enumerate(["a/001.aria.yaml", "b/001.aria.yaml"]):
        journal.append_draft({"id": f"sel{i}2345678ab", "task": f"button:X{i}",
                              "kind": "selector_candidate", "role": "button", "name": f"X{i}",
                              "failure_class": "exploration", "status": "pending_review",
                              "created_at": "x", "evidence": {"snapshot_file": src}})
    view = grouped_view(ReviewQueue(tmp_path).pending())
    assert "源: a/001.aria.yaml" in view and "源: b/001.aria.yaml" in view
    assert view.count("selector_candidate ×1") == 2


def test_cli_review_group_end_to_end(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--group"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "selector_candidate ×1" in captured.out
    assert '"selector_candidate": 1' in captured.err


def _seed_cli_reports(tmp_path, row):
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    ExperienceJournal(reports).append_draft(row)


def test_cli_apply_non_object_line_named_failure_rc2(tmp_path, monkeypatch, capsys):
    """#109 CLI 外层：决策文件里混入合法 JSON 但非映射的行（人编辑改成 null/数组）。
    旧实现透传进 queue.apply 后 row.get 裸 AttributeError——CLI 层崩溃无 rc、无 failed 清单。
    修复后：读循环就点名「行 N 必须是映射」，rc 2，与 malformed JSON 同口径。"""
    from atlas.cli import main
    reports = tmp_path / "reports"
    _seed(reports)
    _stub_cli(tmp_path, monkeypatch)
    template = tmp_path / "mapping.jsonl"
    template.write_text("null\n" + json.dumps(
        {"id": "sel12345678ab", "kind": "selector_candidate",
         "component": "", "element": "", "selector": ""}) + "\n", encoding="utf-8")
    rc = main(["--root", str(tmp_path), "review", "--apply", str(template)])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] == 0
    errors = [f["error"] for f in out["failed"]]
    assert any("必须是映射" in e for e in errors)


def test_cli_review_approve_selector_via_component_flags(tmp_path, capsys, monkeypatch):
    """CLI 全链路：--approve <id> --component --element --remedy 写回**私有**组件库。

    #135 契约更新：旧实现靠覆盖 `knowledge_root` 属性把包内根引到 tmp（写根=knowledge_root），
    正是「批准写包内 git 资产」的旧行为形态。新模型下真实 DemoPlugin 的写根来自
    ATLAS_KNOWLEDGE_DIR（private_root）——本测试用 setenv 指到 tmp 模拟「已配私有目录」，
    覆盖的是真实接线（env 读取）而非 knowledge() 返回值，比旧 stub 更接近真实用户路径。"""
    from atlas.plugins.demo import DemoPlugin
    _seed_cli_reports(tmp_path, {"id": "sel12345678ab", "task": "Save 按钮",
                                 "kind": "selector_candidate", "role": "button",
                                 "name": "Save", "failure_class": "exploration",
                                 "status": "pending_review", "created_at": "x",
                                 # C2 补丁 3：喂晋级闸的真三元组（候选的证据=失败/探索复现）
                                 "evidence": {"verification": {
                                     "command": "python -m pytest tests/test_review_queue.py -k sel",
                                     "exit_code": 1, "assertions": 3, "verdict": "fail"}}})
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(kb_root))   # 真实用户配私有目录的路径
    assert DemoPlugin().knowledge().root == kb_root            # 写根落私有，非包内
    from atlas.cli import main
    rc = main(["--root", str(tmp_path), "review", "--approve", "sel1",
               "--component", "order_header", "--element", "save_new",
               "--remedy", ".app-main button:has(i.demoicon--save)"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["approved"] == "order_header.save_new"
    assert out["kind"] == "selector_candidate"
    saved = json.loads((kb_root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save_new"] == \
        ".app-main button:has(i.demoicon--save)"


def _seed_cli_selector_reports(tmp_path):
    """CLI 轨的 selector_candidate 草稿（带候选 role/name 与晋级闸三元组）。"""
    _seed_cli_reports(tmp_path, {"id": "sel12345678ab", "task": "Save 按钮",
                                 "kind": "selector_candidate", "role": "button",
                                 "name": "Save", "failure_class": "exploration",
                                 "status": "pending_review", "created_at": "x",
                                 "evidence": {"verification": {
                                     "command": "python -m pytest -k sel",
                                     "exit_code": 1, "assertions": 3, "verdict": "fail"}}})


def test_cli_review_approve_with_role_name_flags(tmp_path, capsys, monkeypatch):
    """gap13 CLI 全链路：--role/--name 直接批准 ARIA 候选，人不必手抄 CSS。"""
    from atlas.cli import main
    _seed_cli_selector_reports(tmp_path)
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(kb_root))
    rc = main(["--root", str(tmp_path), "review", "--approve", "sel1",
               "--component", "order_header", "--element", "save_btn",
               "--role", "button", "--name", "Save"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["approved"] == "order_header.save_btn"
    saved = json.loads((kb_root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save_btn"] == {
        "role": "button", "name": "Save"}


def test_cli_review_approve_with_json_remedy(tmp_path, capsys, monkeypatch):
    """--remedy 直接吃 JSON 对象：与 --role/--name 落到同一判据（同库同形态）。"""
    from atlas.cli import main
    _seed_cli_selector_reports(tmp_path)
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(kb_root))
    rc = main(["--root", str(tmp_path), "review", "--approve", "sel1",
               "--component", "c", "--element", "e",
               "--remedy", '{"label": "客户编码"}'])
    assert rc == 0
    saved = json.loads((kb_root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["c"]["selectors"]["e"] == {"label": "客户编码"}


def test_cli_review_approve_malformed_json_remedy_is_named_rc2(tmp_path, capsys, monkeypatch):
    """写坏花括号不许被当成 CSS 字面量静默入库——那是一份永远定位不到的'知识'。"""
    from atlas.cli import main
    _seed_cli_selector_reports(tmp_path)
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(kb_root))
    rc = main(["--root", str(tmp_path), "review", "--approve", "sel1",
               "--component", "c", "--element", "e", "--remedy", '{"role": "button"'])
    assert rc == 2
    assert "合法 JSON" in capsys.readouterr().err
    assert not (kb_root / "components.json").exists()


def test_cli_review_approve_remedy_and_role_conflict_rc2(tmp_path, capsys, monkeypatch):
    """两种意图并存不猜谁赢：点名 rc2，不写库。"""
    from atlas.cli import main
    _seed_cli_selector_reports(tmp_path)
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(kb_root))
    rc = main(["--root", str(tmp_path), "review", "--approve", "sel1",
               "--component", "c", "--element", "e", "--remedy", ".x",
               "--role", "button", "--name", "Save"])
    assert rc == 2
    assert "不能同时给" in capsys.readouterr().err


def test_cli_review_approve_trace_step_exits_two(tmp_path, capsys):
    _seed_cli_reports(tmp_path, {"id": "tr1234567890", "task": "https://sit.example/list",
                                 "kind": "trace_step", "checkpoint": 1,
                                 "failure_class": "exploration", "status": "pending_review",
                                 "created_at": "x"})
    from atlas.cli import main
    rc = main(["--root", str(tmp_path), "review", "--approve", "tr1", "--remedy", "x"])
    assert rc == 2
    assert "flow YAML" in capsys.readouterr().err


# ---- #130：留痕尽力而为 / pending 判据单一真相 / 非映射行同防线 ----

def test_approve_survives_unwritable_decision_journal(tmp_path):
    """#130 面A：批准顺序是「知识写回→队列改写→决策留痕」，前两个业务事实已达成且
    resolve 只认 pending（批准后不可重放）——留痕路径被目录占用时旧实现裸 OSError
    把 CLI 翻成 traceback：审核者以为没批准成功，重试被拒「没有 pending」，批准成功
    的信号永久丢失（知识实际已入库无人知晓）。与 #110 agent._record 同纪律：落盘
    尽力而为，业务语义不顶。"""
    journal = ExperienceJournal(tmp_path)
    journal.append_draft(journal.draft(task="click_save", exc=TimeoutError("hang")))
    queue = ReviewQueue(tmp_path)
    kb = _kb(tmp_path)
    (tmp_path / "review_decisions.jsonl").mkdir()   # 留痕位置被目录占（清理误操作/权限）
    pid = queue.pending()[0]["id"]
    pitfall = queue.approve_to_pitfall(kb, pid, remedy="等 .app-main")   # 修复前：PermissionError
    assert pitfall["status"] == "approved"
    assert queue.stats()["approved"] == 1 and len(queue.pending()) == 0
    saved = json.loads((kb.root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"]
    assert saved and saved[0]["remedy"] == "等 .app-main"      # 知识确实入库，批准可确认


def test_legacy_draft_without_status_is_pending_everywhere(tmp_path):
    """#130 面B：pending 判据三套旧口径（列表 `==PENDING` vs stats/reject_kind
    `get(status,PENDING)`）——无 status 的 legacy 行列表看不见、stats 却报 pending、
    批量驳回能驳回「看不见的行」，CLI 同屏矛盾「审核队列清空」+ pending:1。
    rows() 注释承诺「历史积压照样可审」，收敛 _is_pending 后三入口同真相：
    未被显式批准/驳回的都算待审，legacy 行可列表、可 resolve、可批准。"""
    (tmp_path / "experience_drafts.jsonl").write_text(json.dumps(
        {"id": "legacy000001", "task": "click_save", "exception_type": "TimeoutError",
         "failure_class": "interaction"}) + "\n", encoding="utf-8")
    queue = ReviewQueue(tmp_path)
    assert len(queue.pending()) == 1                      # 修复前实锤：0（与 stats 矛盾）
    assert queue.stats()["pending"] == 1
    row = queue.resolve("legacy000001")                   # 修复前：KeyError「没有 pending」
    assert row["task"] == "click_save"
    kb = _kb(tmp_path)
    pitfall = queue.approve_to_pitfall(kb, "legacy000001", remedy="重试前先刷新")
    assert pitfall["status"] == "approved"
    assert queue.stats()["pending"] == 0 and queue.stats()["approved"] == 1


def test_legal_json_non_object_line_skipped_not_queue_killer(tmp_path):
    """#130 面C：合法 JSON 但非映射的行（人手滑 `[1,2]`、半截编辑器保存成 `null`）——
    旧实现只捕 JSONDecodeError，`row.get` 裸 AttributeError 顶掉整塔（读崩与坏字节
    同红线，#112 注释「两处读侧必须同防线」当时只修了字节面）。同族洞在
    ExperienceJournal._read_queue：append_draft（engine 失败路径的登记器）崩在
    except 块里会把原用例异常顶掉（#100 定性器红线）。两处读侧同纪律跳过非对象行。"""
    journal = ExperienceJournal(tmp_path)
    journal.append_draft(journal.draft(task="good", exc=RuntimeError("x")))
    path = tmp_path / "experience_drafts.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '[{"a": 1}]\nnull\n"字符串"\n',
                    encoding="utf-8")
    queue = ReviewQueue(tmp_path)
    rows = queue.rows()                                   # 修复前实锤：AttributeError
    assert len(rows) == 1 and rows[0]["task"] == "good"   # 好行照常可见
    # 登记器存活：同 id 再登记走 seen 增计而不是崩
    journal.append_draft(journal.draft(task="good", exc=RuntimeError("x")))
    assert queue.rows()[0]["seen"] == 2
    # 后续一次状态改写（原子重写）顺带把坏行规范化清掉——不伪装成合法草稿
    queue.reject(queue.rows()[0]["id"], reason="数据事实")
    assert len(queue.rows()) == 1


def test_resolve_dict_ref_uses_same_pending_truth(tmp_path):
    """#130 面B 兼容面：resolve(dict) 对显式 approved/rejected 仍拒绝（批准重放防线
    不松动），对无 status 的 legacy dict 放行——与列表/统计/批量同一判据。"""
    queue = ReviewQueue(tmp_path)
    _seed(tmp_path)
    row = queue.pending()[0]
    assert queue.resolve(row) is row
    for bad_status in ("approved", "rejected"):
        with pytest.raises(KeyError, match="不在 pending"):
            queue.resolve({**row, "status": bad_status})
    legacy = {"id": "l1", "task": "t"}                    # 无 status 与列表判据同口径
    assert queue.resolve(legacy)["id"] == "l1"


def test_approve_overlay_null_evidence_not_crash(tmp_path):
    """#130 面D：bu_overlay 草稿 evidence 键存在但为 null（旧版/手改坏）——
    row.get("evidence", {}) 默认只在键缺失时生效（#77），批准当场 AttributeError
    塔清不动（#73 场景）。与 evidence_card 的 `or {}` 同口径回落空 dict，让缺 other_bu
    走既有的「需要目标 BU、键与值」友好 ValueError，而非裸崩。"""
    (tmp_path / "experience_drafts.jsonl").write_text(json.dumps(
        {"id": "ovl000000001", "task": "overlay:ou", "kind": "bu_overlay",
         "failure_class": "bu_overlay", "status": "pending_review", "evidence": None}) + "\n",
        encoding="utf-8")
    queue = ReviewQueue(tmp_path)
    kb = _kb(tmp_path)
    with pytest.raises(ValueError, match="目标 BU"):   # 不再是 AttributeError
        queue.approve_to_overlay(kb, "ovl000000001", remedy="BU-B值")


# ---------- gap13：ARIA 候选直接入库（不再手抄 CSS） ----------

class _FakePage:
    def __init__(self):
        self.calls = []

    def get_by_role(self, role, name=None, **kw):
        self.calls.append((role, name, kw))
        return ("role", role, name)

    def locator(self, css):
        self.calls.append(css)
        return ("css", css)


def _sel_id(queue):
    return next(r["id"] for r in queue.pending() if r["kind"] == "selector_candidate")


def test_approve_aria_candidate_stores_description_and_resolves(tmp_path):
    """闭合 gap12 的缝：探索产出的 role+name 候选原样入库，且能解析回 locator。

    旧行为：批准只收 CSS 字符串，人必须把无障碍名手抄成 CSS——抄错=第二份漂移知识。
    """
    from atlas.knowledge import SelectorRegistry
    _seed(tmp_path)
    queue, kb = ReviewQueue(tmp_path), _kb(tmp_path)
    record = queue.approve(kb, _sel_id(queue), remedy={"role": "button", "name": "Save"},
                           component="order_header", element="save_btn")
    assert record["selector"] == {"role": "button", "name": "Save"}   # 落盘用户形态（可再解析）
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save_btn"] == {
        "role": "button", "name": "Save"}
    reg = SelectorRegistry(saved)
    page = _FakePage()
    assert reg.resolve(page, "order_header", "save_btn") == ("role", "button", "Save")
    assert page.calls[0] == ("button", "Save", {"exact": True})   # 精确匹配，不误点「Save and close」
    # 留痕用 canonical 文本（dict 不可切片：旧写法 selector[:120] 会 TypeError）
    line = json.loads((tmp_path / "review_decisions.jsonl")
                      .read_text(encoding="utf-8").splitlines()[0])
    assert line["selector"] == 'role=button[name="Save"]'   # dict 不可切片：留痕走 canonical


def test_approve_rejects_malformed_description_before_writing(tmp_path):
    """坏形态在**落盘前**点名：知识文件里绝不出现跑不了的定位描述。"""
    from atlas import locators
    _seed(tmp_path)
    queue, kb = ReviewQueue(tmp_path), _kb(tmp_path)
    with pytest.raises(locators.LocatorSpecError) as ei:
        queue.approve(kb, _sel_id(queue), remedy={"role": "button", "css": ".x"},
                      component="c", element="e")
    assert "多个定位键" in str(ei.value)
    assert not (kb.root / "components.json").exists()   # 零半状态：连文件都不建


def test_save_component_selector_normalizes_css_description(tmp_path):
    """{"css": ...} 归一成字符串：存量形态仍是字符串，产物/读侧零涟漪。"""
    kb = _kb(tmp_path)
    out = kb.save_component_selector("c", "e", {"css": ".btn"}, approved=True, source="test")
    assert out["selector"] == ".btn"
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["c"]["selectors"]["e"] == ".btn"


def test_template_prefills_role_name_and_apply_uses_it(tmp_path):
    """模板预填候选的 role/name：人只决定写进哪个组件元素，--apply 即入库。"""
    _seed(tmp_path)
    queue, kb = ReviewQueue(tmp_path), _kb(tmp_path)
    row = next(r for r in queue.template_rows("selector_candidate"))
    assert row["role"] == "button" and row["name"] == "Save" and row["selector"] == ""

    # 未填 component/element 的行仍失败并点名（批量批准 ≠ 自动批准）
    result = queue.apply(kb, [row])
    assert result["applied"] == 0 and result["failed"]
    assert not (kb.root / "components.json").exists() or \
        json.loads((kb.root / "components.json").read_text(encoding="utf-8"))["components"] == {}

    # 填上组件/元素：role/name 照原值入库
    result = queue.apply(kb, [{**row, "component": "order_header", "element": "save_btn"}])
    assert result["applied"] == 1 and not result["failed"]
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["order_header"]["selectors"]["save_btn"]["role"] == "button"


def test_apply_selector_column_wins_over_prefilled_role(tmp_path):
    """人显式写了 CSS 就是显式选择：不被预填的 role/name 盖掉。"""
    _seed(tmp_path)
    queue, kb = ReviewQueue(tmp_path), _kb(tmp_path)
    row = next(r for r in queue.template_rows("selector_candidate"))
    result = queue.apply(kb, [{**row, "component": "c", "element": "e",
                               "selector": ".app-main button:has(i)"}])
    assert result["applied"] == 1
    saved = json.loads((kb.root / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["c"]["selectors"]["e"] == ".app-main button:has(i)"


# ---------- cc-4：并发批准的事务锁（跨进程真并发） ----------

_CC4_CHILD = (
    "import sys, json\n"
    "from pathlib import Path\n"
    "from atlas.knowledge import KnowledgeBase\n"
    "from atlas.review import ReviewQueue\n"
    "queue = ReviewQueue(Path(sys.argv[1]))\n"
    "kb = KnowledgeBase(Path(sys.argv[1]) / 'kb')\n"
    "try:\n"
    "    queue.approve_to_pitfall(kb, sys.argv[2], remedy=sys.argv[3])\n"
    "    print('applied')\n"
    "except (KeyError, ValueError) as exc:\n"
    "    print('rejected:', type(exc).__name__)\n"
)


def test_concurrent_approve_produces_one_decision_per_knowledge_row(tmp_path):
    """cc-4 根因面：两个进程同时批准同一条 pending 草稿——修复前可产出「2 条 approve
    决策对应 1 行知识」（两次 resolve 都看到 pending、各自写知识、各自翻转队列）。
    修复后塔锁把「resolve → 写知识 → 翻队列 → 留痕」整条事务串行化：**恰好一个进程成功**，
    另一个在第一进程之后 resolve 已看到的 approved 行 → 按既有 _is_pending 口径点名拒绝。
    判据：决策留痕行数 == 知识行数 == 1（不是 2 对 1）。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    pitfall_id = queue.pending(kind="pitfall")[0]["id"]
    kb_root = tmp_path / "kb"
    kb_root.mkdir(exist_ok=True)
    (kb_root / "pitfalls.json").write_text(json.dumps({"pitfalls": []}), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    procs = [subprocess.Popen([sys.executable, "-c", _CC4_CHILD, str(tmp_path), pitfall_id,
                               f"remedy-{i}"], env=env, stdout=subprocess.PIPE, text=True)
             for i in range(2)]
    outputs = [(p.wait(), p.stdout.read().strip()) for p in procs]
    assert all(rc == 0 for rc, _ in outputs), outputs          # 两侧都是「预期待定」退出
    applied = [out for _, out in outputs if out == "applied"]
    assert len(applied) == 1, f"并发批准同一草稿应恰好一个成功，实得 {outputs}"
    decisions = [json.loads(l) for l in (tmp_path / "review_decisions.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
    knowledge_rows = json.loads((tmp_path / "kb" / "pitfalls.json")
                                .read_text(encoding="utf-8"))["pitfalls"]
    assert len(decisions) == 1, f"决策留痕应恰 1 行（不是 2 行对 1 行知识）：{decisions}"
    assert len(knowledge_rows) == 1
    assert queue.stats()["approved"] == 1                      # 队列态与知识一致


def test_same_process_approve_is_reentrant_not_self_deadlock(tmp_path):
    """cc-4 负向对照面：同进程 `approve` → `approve_to_pitfall` 两层都要锁——塔锁必须
    同线程重入直通（跨 fd 嵌套取同一路径第二把锁在 Windows 上是阻塞重试 = 自己挂死）。
    本测跑通即证「重入语义在」；挂死会被 pytest 超时/本文件跑不完暴露。"""
    _seed(tmp_path)
    queue = ReviewQueue(tmp_path)
    kb = _kb(tmp_path)
    pitfall_id = queue.pending(kind="pitfall")[0]["id"]
    queue.approve(kb, pitfall_id, remedy="等 .app-main")        # 走 approve 分发（两层锁）
    assert queue.stats()["approved"] == 1
    assert json.loads((tmp_path / "kb" / "pitfalls.json")
                      .read_text(encoding="utf-8"))["pitfalls"]

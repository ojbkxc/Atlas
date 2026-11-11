"""C2 证据式验收三元组（design_c2）：新晋级草稿必须带「复现命令/退出码/断言数/显式结论行」。

钉死四件事：缺三元组不可 approve；可显式 override 但必须留痕（绝不死锁，红线 #7 是
硬化不是绕毁）；存量已批准条目向后兼容不翻错；三元组透传进决策留痕。
闸只查完备与形态，**不要求 verdict 必须绿**——坑点草稿记 fail 合法（证明失败真实复现过）。
"""
import json
import pytest
from atlas.allure_analysis import AllureFinding, build_allure_verification
from atlas.experience import ExperienceJournal
from atlas.healing import HealingProposal
from atlas.knowledge import KnowledgeBase
from atlas.review import (GATED_KINDS, VERIFICATION_KEYS, ReviewQueue, _is_verified,
                          evidence_card, verification_problems)

VER = {"command": "python -m pytest tests -k tc3", "exit_code": 1,
       "assertions": 12, "verdict": "fail"}


def _kb(tmp_path):
    kb_root = tmp_path / "kb"
    kb_root.mkdir(exist_ok=True)
    (kb_root / "pitfalls.json").write_text(json.dumps({"pitfalls": []}), encoding="utf-8")
    return KnowledgeBase(kb_root)


def _seed(tmp_path, row_id, *, kind="pitfall", verified=True, evidence=None):
    """塞一条 pending_review 草稿；verified=False 不带三元组（应被闸拦）。"""
    ev = dict(evidence or {})
    if verified:
        ev["verification"] = dict(VER)
    row = {"id": row_id, "task": "overlay:ou" if kind == "bu_overlay" else f"task-{row_id}",
           "kind": kind, "exception_type": "TimeoutError",
           "failure_class": "assertion", "status": "pending_review",
           "created_at": "x", "evidence": ev}
    ExperienceJournal(tmp_path).append_draft(row)
    return row


# ---- §6-1 判据函数：完备/缺失/坏形态全给问题清单，绝不抛 ----

def test_complete_triplet_is_verified():
    assert verification_problems(dict(VER)) == []
    assert VERIFICATION_KEYS == ("command", "exit_code", "assertions", "verdict")
    # 落库线（2026-11-06）：flow_candidate（跑绿固化的动作序列）入闸——dsl 侧自动带
    # verdict="pass" 的成功三元组，与失败草稿同一把闸；手工造的无证据草稿照样拦。
    assert set(GATED_KINDS) == {"pitfall", "selector_candidate", "bu_overlay",
                                "flow_candidate"}


def test_verdict_fail_is_legal_not_only_green():
    """红线 #7 本意：闸查完备与形态，不要求「必须绿」——否则逼人伪造 pass。"""
    assert verification_problems({**VER, "verdict": "pass"}) == []
    assert verification_problems({**VER, "verdict": "fail"}) == []


@pytest.mark.parametrize("mutate,bad_key", [
    ({"command": ""}, "command"), ({"command": "   "}, "command"),
    ({"command": None}, "command"), ({"command": 42}, "command"),
    ({"exit_code": True}, "exit_code"), ({"exit_code": False}, "exit_code"),
    ({"exit_code": "0"}, "exit_code"), ({"exit_code": None}, "exit_code"),
    ({"assertions": -1}, "assertions"), ({"assertions": True}, "assertions"),
    ({"assertions": 3.5}, "assertions"),
    ({"verdict": "PASS"}, "verdict"), ({"verdict": "passed"}, "verdict"),
    ({"verdict": None}, "verdict"),
])
def test_malformed_triplet_lists_named_problem(mutate, bad_key):
    problems = verification_problems({**VER, **mutate})
    assert problems and any(bad_key in p for p in problems)


@pytest.mark.parametrize("missing_key", list(VERIFICATION_KEYS))
def test_each_key_missing_is_a_problem(missing_key):
    v = dict(VER)
    v.pop(missing_key)
    problems = verification_problems(v)
    assert any(missing_key in p for p in problems)


@pytest.mark.parametrize("bad", [None, [1, 2], "字符串", 42, {"verification": None}])
def test_non_mapping_never_raises(bad):
    """#77/#130 家族：判据函数不得先于被判定对象死——坏形态给问题项不是 AttributeError。"""
    problems = verification_problems(bad)
    assert problems and isinstance(problems, list) and all(isinstance(p, str) for p in problems)


def test_is_verified_consistent_with_problems():
    assert _is_verified({"evidence": {"verification": dict(VER)}}) is True
    assert _is_verified({"evidence": {}}) is False
    assert _is_verified({}) is False                    # 无 evidence 键
    assert _is_verified({"evidence": None}) is False    # #77 键在值 null
    assert _is_verified({"evidence": "坏形态"}) is False


# ---- §6-2 闸拦新草稿（pitfall/selector/overlay 三 kind），文案给 override 指路 ----

@pytest.mark.parametrize("kind", ["pitfall", "selector_candidate", "bu_overlay"])
def test_gate_blocks_unverified_for_all_three_kinds(tmp_path, kind):
    _seed(tmp_path, f"g-{kind}", kind=kind, verified=False,
          evidence={"other_bu": "BU-B"} if kind == "bu_overlay" else None)
    queue = ReviewQueue(tmp_path)
    with pytest.raises(ValueError) as ei:
        queue.approve(_kb(tmp_path), f"g-{kind}", remedy="修复",
                      component="c", element="e")
    msg = str(ei.value)
    assert "unverified 不可晋级" in msg
    assert "--allow-unverified" in msg and "--reason" in msg   # 指路而非死锁
    assert queue.pending()                                     # 不可批准 ≠ 删除：照常可见
    assert queue.stats()["approved"] == 0                      # 一条都没进去


def test_verified_triplet_approves_normally_no_marks(tmp_path, capsys):
    _seed(tmp_path, "v1", verified=True)
    queue = ReviewQueue(tmp_path)
    queue.approve(_kb(tmp_path), "v1", remedy="修复")
    row = next(r for r in queue.rows() if r["id"] == "v1")
    assert row["status"] == "approved"
    assert "approved_despite_unverified" not in row            # 正常批准不打 override 标
    decision = json.loads((tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8"))
    assert "unverified_override" not in decision
    assert capsys.readouterr().err == ""                       # 无警示噪音


# ---- §6-3 override：带原因放行 + 双留痕 + stderr 警示；无原因拒 ----

def test_override_approves_with_full_trace(tmp_path, capsys):
    _seed(tmp_path, "ov1", verified=False)
    queue = ReviewQueue(tmp_path)
    queue.approve(_kb(tmp_path), "ov1", remedy="修复",
                  allow_unverified=True, override_reason="真机已复现确认，三元组在工单附件")
    row = next(r for r in queue.rows() if r["id"] == "ov1")
    assert row["status"] == "approved" and row["approved_despite_unverified"] is True
    # 三元组/override 透传进决策留痕（--reason 原文入库）
    decision = json.loads((tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8"))
    assert decision["unverified_override"] is True
    assert decision["override_reason"] == "真机已复现确认，三元组在工单附件"
    err = capsys.readouterr().err
    assert "UNVERIFIED-override: ov1" in err
    # X4：警示前缀必须 GBK 可编（真机 PT 控制台教训）
    assert err.splitlines()[0].split(":")[0].encode("gbk")


@pytest.mark.parametrize("bad_reason", ["", "   ", "\t"])
def test_override_without_reason_is_refused(tmp_path, bad_reason):
    """override 是人的显式决定且必留痕（红线 #13）——机器永不自动放行。"""
    _seed(tmp_path, "ov2", verified=False)
    queue = ReviewQueue(tmp_path)
    with pytest.raises(ValueError, match="必须留原因"):
        queue.approve(_kb(tmp_path), "ov2", remedy="修复",
                      allow_unverified=True, override_reason=bad_reason)
    assert queue.stats()["pending"] == 1 and queue.stats()["approved"] == 0


def test_allow_unverified_on_verified_row_needs_no_reason(tmp_path, capsys):
    """allow_unverified 只在真缺三元组时才触发 override 语义；已验证行带 flag 是无害空转
    ——不得要求原因、不得打 override 标（判据只有一把刀，#92）。"""
    _seed(tmp_path, "noop1", verified=True)
    queue = ReviewQueue(tmp_path)
    queue.approve(_kb(tmp_path), "noop1", remedy="修复", allow_unverified=True)
    row = next(r for r in queue.rows() if r["id"] == "noop1")
    assert row["status"] == "approved" and "approved_despite_unverified" not in row
    assert capsys.readouterr().err == ""


def test_gate_does_not_add_second_door_for_unapprovable_kinds(tmp_path):
    """trace_step/code_review 维持原「不可批准」拒绝——闸不加第二扇门（design §3.1）。"""
    for kind, hint in (("trace_step", "flow YAML"), ("code_review", "不自动写知识")):
        ExperienceJournal(tmp_path).append_draft(
            {"id": f"x-{kind}", "task": "t", "kind": kind, "checkpoint": 1,
             "failure_class": "exploration", "status": "pending_review", "created_at": "x"})
        queue = ReviewQueue(tmp_path)
        with pytest.raises(ValueError) as ei:
            queue.approve(_kb(tmp_path), f"x-{kind}", remedy="任意")
        assert hint in str(ei.value)
        assert "unverified" not in str(ei.value)


# ---- §6-4 向后兼容：存量 approved 不复查、legacy 补 id 行为不动 ----

def test_existing_decided_rows_have_zero_ripple(tmp_path):
    """闸只作用于「批准」动作：无 verification 的已批/已驳存量行照常列出、统计、不翻错。"""
    (tmp_path / "experience_drafts.jsonl").write_text("\n".join([
        json.dumps({"id": "old-ok", "task": "t1", "kind": "pitfall", "status": "approved",
                    "pitfall_ref": "reviewed-x"}),                    # 旧模型批准的，无三元组
        json.dumps({"id": "old-no", "task": "t2", "status": "rejected",
                    "reject_reason": "数据事实"}),
        json.dumps({"task": "t3", "exception_type": "TimeoutError",
                    "failure_class": "interaction"}),                  # legacy 无 id 无 status
    ]) + "\n", encoding="utf-8")
    queue = ReviewQueue(tmp_path)
    stats = queue.stats()
    assert stats["approved"] == 1 and stats["rejected"] == 1 and stats["pending"] == 1
    legacy = queue.pending()[0]
    assert len(legacy["id"]) == 12          # rows() 补 id 归一逻辑不动
    with pytest.raises(KeyError):
        queue.resolve("old-ok")             # 批准重放防线不松动


# ---- §6-5 批量 --apply：unverified 进 failed 点名；决策行不配 override 通道 ----

def test_apply_batch_names_unverified_and_cannot_be_backdoored(tmp_path):
    _seed(tmp_path, "b-ok", verified=True)
    _seed(tmp_path, "b-bad", verified=False)
    queue = ReviewQueue(tmp_path)
    kb = _kb(tmp_path)
    # 决策模板没有 --allow-unverified 通道（有意）：批量放行 unverified 等于没闸。
    # 就算人往决策 JSON 里塞同名字段，apply 也不透传——override 必须单条显式决定。
    result = queue.apply(kb, [
        {"id": "b-ok", "remedy": "修复一"},
        {"id": "b-bad", "remedy": "修复二", "allow_unverified": True, "override_reason": "旁路"},
    ])
    assert result["applied"] == 1
    failed = {f["id"]: f["error"] for f in result["failed"]}
    assert "b-bad" in failed and "--allow-unverified" in failed["b-bad"]
    assert next(r for r in queue.rows() if r["id"] == "b-bad")["status"] == "pending_review"


# ---- §6-6 healing：提案层构造期形态校验，合法/缺省零涟漪 ----

def test_healing_proposal_bad_verification_raises_at_construction():
    with pytest.raises(ValueError, match="形态不完备"):
        HealingProposal("t", "TimeoutError", "r", "p1",
                        evidence={"verification": {"command": "pytest x"}})
    with pytest.raises(ValueError) as ei:
        HealingProposal("t", "TimeoutError", "r", "p1",
                        evidence={"verification": {"command": "pytest x",
                                                   "exit_code": 1, "assertions": 2}})
    assert "verdict" in str(ei.value)           # 点名缺失键
    with pytest.raises(ValueError):
        HealingProposal("t", "x", "r", "p", evidence={"verification": "坏形态"})
    with pytest.raises(ValueError, match="必须是映射"):
        HealingProposal("t", "x", "r", "p", evidence=[1, 2])   # evidence 本体坏形态也不裸崩


def test_healing_proposal_optional_field_is_backward_compatible():
    """旧 4 参数构造不动（propose() 行为不变）；合法三元组照常携带。"""
    legacy = HealingProposal("t", "TimeoutError", "r", "p1")
    assert legacy.evidence is None and legacy.status == "pending_review"
    ok = HealingProposal("t", "TimeoutError", "r", "p1",
                         evidence={"verification": dict(VER)})
    assert ok.evidence["verification"]["verdict"] == "fail"


# ---- §6-7 allure-analyze 造的草稿带三元组（verdict 由 Allure status 派生）----

def _finding(status, assertions=4):
    return AllureFinding(name="tc3", full_name="tests::tc3", status=status,
                         message="boom", trace="", labels={}, fail_class="assertion",
                         assertions=assertions)


def test_allure_verification_derives_from_status():
    v = build_allure_verification(_finding("passed"), "reports/allure-results")
    assert v == {"command": "atlas allure-analyze reports/allure-results",
                 "exit_code": 0, "assertions": 4, "verdict": "pass"}
    for st in ("failed", "broken", "skipped", "unknown"):
        v = build_allure_verification(_finding(st), "d")
        assert v["verdict"] == "fail" and v["exit_code"] == 1, st
    assert verification_problems(v) == []      # 派生结果必须过判据


def test_allure_to_tower_draft_is_approvable(tmp_path):
    """端到端：Allure 结果集 → --to-tower 草稿 → 直接过晋级闸（三元组已带）。"""
    from atlas.allure_analysis import analyze_allure
    res_dir = tmp_path / "allure-results"
    res_dir.mkdir()
    (res_dir / "one-result.json").write_text(json.dumps({
        "name": "tc_order", "fullName": "tests::tc_order", "status": "failed",
        "steps": [{"name": f"s{i}"} for i in range(3)],
        "statusDetails": {"message": "AssertionError boom", "trace": "t"},
    }), encoding="utf-8")
    journal = ExperienceJournal(tmp_path)
    analyze_allure(res_dir, to_tower=journal)
    queue = ReviewQueue(tmp_path)
    row = queue.pending()[0]
    assert row["evidence"]["verification"]["assertions"] == 3   # steps 计数口径
    queue.approve(_kb(tmp_path), row["id"], remedy="修复")      # 不 override 直接批
    assert queue.stats()["approved"] == 1


# ---- §6-8 证据卡看得见缺什么 ----

def test_evidence_card_badges_verified_and_lists_missing(tmp_path):
    _seed(tmp_path, "c-bad", verified=False)
    _seed(tmp_path, "c-ok", verified=True)
    queue = ReviewQueue(tmp_path)
    bad = next(r for r in queue.pending() if r["id"] == "c-bad")
    ok = next(r for r in queue.pending() if r["id"] == "c-ok")
    card_bad = evidence_card(bad)
    assert "[UNVERIFIED]" in card_bad
    assert "command" in card_bad and "verdict" in card_bad      # 缺失键清单
    assert "--allow-unverified" in card_bad                     # 指路
    card_ok = evidence_card(ok)
    assert "[VERIFIED]" in card_ok and "[UNVERIFIED]" not in card_ok


def test_evidence_card_no_badge_for_unapprovable_kinds(tmp_path):
    """trace_step 本就不可批准，不标注 VERIFIED/UNVERIFIED——防止读成「补三元组就能批」。"""
    ExperienceJournal(tmp_path).append_draft(
        {"id": "tr-c", "task": "http://demo/list", "kind": "trace_step", "checkpoint": 1,
         "failure_class": "exploration", "status": "pending_review", "created_at": "x"})
    card = evidence_card(ReviewQueue(tmp_path).pending()[0])
    assert "[UNVERIFIED]" not in card and "[VERIFIED]" not in card
    assert "--reject" in card


# ---- C2-C 复现命令文案合法性：`atlas run <flow> --env <env>` 后置形态可解析 ----

def test_run_env_trailing_command_text_is_parseable():
    """补丁 1 的 command 文案形态（design_c2 §2/§8）：run 后置 --env 与全局 parents 兼容
    （2026-11-04 修过的 argparse 缺陷，回归钉死防止文案给人一条解析不了的复现命令）。"""
    from atlas.cli import _parser
    args = _parser().parse_args(["run", "flows/x.yaml", "--env", "sit"])
    assert args.command == "run" and args.env == "sit"


# ---- override 三 kind 全覆盖 + 留痕落盘往返（JSONL 序列化不丢标记） ----

@pytest.mark.parametrize("kind", ["pitfall", "selector_candidate", "bu_overlay"])
def test_override_works_for_every_gated_kind(tmp_path, kind, capsys):
    _seed(tmp_path, f"k-{kind}", kind=kind, verified=False,
          evidence={"other_bu": "BU-B"} if kind == "bu_overlay" else None)
    queue = ReviewQueue(tmp_path)
    queue.approve(_kb(tmp_path), f"k-{kind}", remedy="真值/选择器/修复",
                  component="c", element="e", allow_unverified=True,
                  override_reason="人审点名豁免")
    rows_path = tmp_path / "experience_drafts.jsonl"
    on_disk = [json.loads(l) for l in rows_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    row = next(r for r in on_disk if r["id"] == f"k-{kind}")
    assert row["status"] == "approved" and row["approved_despite_unverified"] is True
    decision = json.loads((tmp_path / "review_decisions.jsonl").read_text(encoding="utf-8"))
    assert decision["unverified_override"] is True and decision["override_reason"] == "人审点名豁免"
    assert "UNVERIFIED-override: k-" in capsys.readouterr().err


# ---- X4：证据卡是返回串不是 print 字面量，test_xplat_sentinels 扫不到，必须直接断言 ----

def test_evidence_card_return_string_is_cp936_encodable():
    """bu_overlay 卡片旧用 `↔`(U+2194) 不可 cp936 编码——真机 GBK 控制台 `atlas review`
    print 到它会崩（explore.py:60 ✓ 崩例同族）。返回串形态哨兵（只扫 print 字面量）够不着，
    防线就在这条：所有 kind 的卡片整体必须能编进 cp936。坐标/坑点/候选/trace 全覆盖，
    将来任何人再往返回串里塞非 GBK 字形（箭头/对勾/emoji）当场红。"""
    rows = {
        "bu_overlay": {"id": "ov", "task": "overlay:ou", "kind": "bu_overlay",
                       "failure_class": "bu_overlay", "status": "pending_review",
                       "created_at": "x",
                       "evidence": {"base_bu": "BU-甲", "base_value": "OU0",
                                    "other_bu": "BU-乙", "other_value": "OU9"}},
        "pitfall": {"id": "pf", "task": "点保存", "kind": "pitfall",
                    "exception_type": "TimeoutError", "failure_class": "interaction",
                    "status": "pending_review", "created_at": "x",
                    "evidence": {"url": "https://sit/ics/列表", "text": "断言失败：金额不符"}},
        "selector_candidate": {"id": "sc", "task": "保存按钮", "kind": "selector_candidate",
                               "role": "按钮", "name": "Save", "failure_class": "exploration",
                               "status": "pending_review", "created_at": "x",
                               "evidence": {"snapshot_file": "菜单/保存.aria.yaml"}},
        "trace_step": {"id": "ts", "task": "http://demo/列表", "kind": "trace_step",
                       "checkpoint": 1, "failure_class": "exploration",
                       "status": "pending_review", "created_at": "x"},
        "code_review": {"id": "cr", "task": "GREEN-01 断言放宽", "kind": "code_review",
                        "failure_class": "code_review", "status": "pending_review",
                        "created_at": "x"},
    }
    for kind, row in rows.items():
        card = evidence_card(row)
        try:
            card.encode("cp936")          # 中文本身可编，禁的是非 GBK 字形
        except UnicodeEncodeError as exc:
            pytest.fail(f"{kind} 卡片含不可 cp936 编码字符（X4 GBK 控制台崩因）："
                        f"{card[exc.start:exc.end]!r}")
    # 正面钉住修复形态：bu_overlay 行用 ASCII `<->`，不再回退 `↔`
    ov_card = evidence_card(rows["bu_overlay"])
    assert "<->" in ov_card and "↔" not in ov_card


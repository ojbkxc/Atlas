"""Gap5 P3：Allure 结果集解析（§8 测试 11–16）+ P2 单一真相回归测。

覆盖：
- P2 回归：classify_text ↔ classify_failure 同文本同结果（防 #92 漂移）
- P2 回归：match_pitfall_from_text ↔ match_pitfall 同文本同结果
- P2 回归：draft_from_text ↔ draft 同结果
- §8 测试 11：往返一致（自产结果 fail_class 与原始一致）
- §8 测试 12：外部 Allure 结果 classify_text 归类（与 classify_failure 同文本同结果）
- §8 测试 13：--to-tower 未命中进塔 + 命中产出 heal 提案不执行
- §8 测试 14：控制流 interrupted 不得产出 heal 提案（#108）
- §8 测试 15：坏 JSON / 非对象 / 腰斩文件跳过计数
- §8 测试 16：空结果集返回空汇总
"""
import hashlib
import json

import pytest

from atlas.allure_report import emit_allure
from atlas.allure_analysis import (
    DEFAULT_BUCKETS,
    analyze_allure,
    format_text,
    load_allure_results,
    parse_allure_result,
)
from atlas.experience import ExperienceJournal
from atlas.failures import (
    FailureClass,
    classify_failure,
    classify_text,
    exception_text,
)
from atlas.knowledge import KnowledgeBase


# ── helpers ──────────────────────────────────────────────────────────

def _write_runs(path, runs):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in runs) + "\n",
                    encoding="utf-8")


def _write_result(out_dir, result):
    """写一个 *-result.json（不经 emit，直接造外部/测试结果）。"""
    uid = hashlib.sha1(json.dumps(result, sort_keys=True).encode()).hexdigest()[:16]
    path = out_dir / f"test-{uid}-result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _make_knowledge_with_pitfall(tmp_path):
    """造一个含 approved 坑点的 KnowledgeBase（trigger='timeout'）。"""
    kb_root = tmp_path / "knowledge"
    kb_root.mkdir()
    (kb_root / "pitfalls.json").write_text(json.dumps({
        "pitfalls": [{
            "id": "approved-timeout", "trigger": "timeout", "symptom": "timeout",
            "root_cause": "element not visible", "remedy": "wait longer",
            "scope": [], "severity": "medium", "source": "test",
            "failure_class": "interaction", "status": "approved",
        }]
    }, ensure_ascii=False), encoding="utf-8")
    return KnowledgeBase(None, kb_root)


# ── P2 回归：classify_text ↔ classify_failure ─────────────────────

class TestClassifyTextSingleTruth:
    """同一文本经 classify_failure 与 classify_text 给同一类（防 #92 漂移）。"""

    @pytest.mark.parametrize("exc,expected", [
        (AssertionError("expected 5 but got 3"), FailureClass.ASSERTION),
        (RuntimeError("401 unauthorized"), FailureClass.SESSION),
        (RuntimeError("timeout waiting for element"), FailureClass.INTERACTION),
        (RuntimeError("not found: document number"), FailureClass.DATA),
        (RuntimeError("contractviolation: invalid payload"), FailureClass.CONTRACT),
        (RuntimeError("环境阻塞：产品设计限制"), FailureClass.ENV_BLOCKED),
        (RuntimeError("some weird error"), FailureClass.UNKNOWN),
    ])
    def test_same_text_same_class(self, exc, expected):
        text = exception_text(exc)
        assert classify_text(text) == classify_failure(exc)
        assert classify_text(text) == expected

    def test_control_flow_still_short_circuits_in_classify_failure(self):
        """控制流短路保留：KeyboardInterrupt 经 classify_failure 给 interrupted，
        classify_text（无控制流判断）给 unknown——两者不同，但这是设计意图。"""
        exc = KeyboardInterrupt()
        assert classify_failure(exc) == FailureClass.INTERRUPTED
        # classify_text 不含控制流判断，KI 文本归类为 unknown（无关键词匹配）
        assert classify_text(exception_text(exc)) == FailureClass.UNKNOWN

    def test_classify_text_handles_uppercase(self):
        """classify_text 内部 lower()：传入任意大小写文本都能正确匹配。"""
        assert classify_text("ASSERTIONERROR: expected 5") == FailureClass.ASSERTION
        assert classify_text("401 Unauthorized") == FailureClass.SESSION
        assert classify_text("Timeout waiting") == FailureClass.INTERACTION

    def test_classify_text_empty_string(self):
        """空文本 → unknown（不崩）。"""
        assert classify_text("") == FailureClass.UNKNOWN


# ── P2 回归：match_pitfall_from_text ↔ match_pitfall ─────────────

class TestMatchPitfallFromTextSingleTruth:
    """match_pitfall_from_text 与 match_pitfall 同文本同结果（防 #92 漂移）。"""

    def test_same_text_same_hit(self, tmp_path):
        kb = _make_knowledge_with_pitfall(tmp_path)
        exc = RuntimeError("timeout waiting for element")
        text = exception_text(exc)

        hit_from_exc = kb.match_pitfall(exc, status="approved")
        hit_from_text = kb.match_pitfall_from_text(text, status="approved")

        assert hit_from_exc is not None
        assert hit_from_text is not None
        assert hit_from_exc["id"] == hit_from_text["id"]
        assert hit_from_exc["remedy"] == hit_from_text["remedy"]

    def test_empty_trigger_never_matches(self, tmp_path):
        """空 trigger 永不匹配（#59）：`"" in text` 恒真，一条缺 trigger 的知识不命中。"""
        kb_root = tmp_path / "knowledge"
        kb_root.mkdir()
        (kb_root / "pitfalls.json").write_text(json.dumps({
            "pitfalls": [{
                "id": "bad-empty-trigger", "trigger": "", "symptom": "x",
                "root_cause": "x", "remedy": "x", "scope": [], "severity": "medium",
                "source": "test", "failure_class": "interaction", "status": "approved",
            }]
        }, ensure_ascii=False), encoding="utf-8")
        kb = KnowledgeBase(None, kb_root)
        hit = kb.match_pitfall_from_text("any text at all", status="approved")
        assert hit is None


# ── P2 回归：draft_from_text ↔ draft ─────────────────────────────

class TestDraftFromTextSingleTruth:
    """draft_from_text 与 draft 同结果（draft 委托 draft_from_text）。"""

    def test_same_identity_same_row(self, tmp_path):
        journal = ExperienceJournal(tmp_path / "reports")
        exc = RuntimeError("timeout waiting for element")
        failure_class = classify_failure(exc).value

        row_from_exc = journal.draft(task="tc1", exc=exc)
        row_from_text = journal.draft_from_text(
            task="tc1", text=exception_text(exc),
            failure_class=failure_class, exception_type=type(exc).__name__,
        )

        assert row_from_exc["id"] == row_from_text["id"]
        assert row_from_exc["task"] == row_from_text["task"]
        assert row_from_exc["failure_class"] == row_from_text["failure_class"]
        assert row_from_exc["exception_type"] == row_from_text["exception_type"]
        assert row_from_exc["status"] == "pending_review"
        assert row_from_text["status"] == "pending_review"

    def test_draft_stores_text_in_evidence(self, tmp_path):
        """draft 委托 draft_from_text 后，evidence 含 text（exception_text 原文）。"""
        journal = ExperienceJournal(tmp_path / "reports")
        exc = RuntimeError("timeout waiting for element")
        row = journal.draft(task="tc1", exc=exc)
        assert "evidence" in row
        assert "text" in row["evidence"]


# ── §8 测试 11：往返一致 ─────────────────────────────────────────

class TestRoundTrip:
    """解析自产结果集 → fail_class 与原始一致（atlas.fail_class label 直接采用）。"""

    def test_self_produced_assertion_failure_round_trips(self, tmp_path):
        runs = tmp_path / "runs.jsonl"
        _write_runs(runs, [{
            "run_id": "r1", "case": "tc_assert", "env": "sit", "status": "failed",
            "started_at": "2026-11-04T10:00:00+00:00",
            "finished_at": "2026-11-04T10:00:01+00:00",
            "steps": [{"step": "save", "op": "ui", "ok": False,
                       "fail_class": "assertion", "dur_ms": 20}],
        }])
        out = tmp_path / "allure-results"
        emit_allure(runs, out)

        result = analyze_allure(out)
        assert result["failed"] == 1
        assert result["fail_class_distribution"]["assertion"] == 1

    def test_self_produced_env_blocked_round_trips_as_skipped(self, tmp_path):
        """env_blocked 自产结果：status=skipped，label 直接采用，不进 failed 分布。"""
        runs = tmp_path / "runs.jsonl"
        _write_runs(runs, [{
            "run_id": "r1", "case": "tc_env", "env": "sit", "status": "env_blocked",
            "reason": "生产环境禁止建单",
            "started_at": "2026-11-04T10:00:00+00:00",
            "finished_at": "2026-11-04T10:00:01+00:00",
            "steps": [{"step": "write", "op": "ui", "ok": False,
                       "fail_class": "env_blocked", "dur_ms": 5}],
        }])
        out = tmp_path / "allure-results"
        emit_allure(runs, out)

        result = analyze_allure(out)
        assert result["skipped"] == 1
        assert result["failed"] == 0
        # env_blocked 不进 fail_class 分布（只统计 failed/broken）
        assert "env_blocked" not in result["fail_class_distribution"]


# ── §8 测试 12：外部 Allure 结果 classify_text 归类 ──────────────

class TestExternalAllureClassification:
    """无 atlas.fail_class label 的外部结果 → classify_text(message+trace) 归类。"""

    def test_external_assertion_classified(self, tmp_path):
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        _write_result(results_dir, {
            "uuid": "ext-1", "name": "ext_assert",
            "fullName": "scs.sit.ext_assert", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5 but got 3",
                               "trace": "at test.py:42"},
            "labels": [{"name": "suite", "value": "scs"}],
            "parameters": [], "steps": [], "attachments": [],
        })
        result = analyze_allure(results_dir)
        assert result["failed"] == 1
        assert result["fail_class_distribution"]["assertion"] == 1

    def test_external_same_text_same_class_as_classify_failure(self, tmp_path):
        """防 #92 漂移：外部结果的 classify_text 归类与 classify_failure 同文本同结果。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        message = "Timeout waiting for element to be visible"
        _write_result(results_dir, {
            "uuid": "ext-2", "name": "ext_timeout", "status": "failed",
            "statusDetails": {"message": message, "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })
        result = analyze_allure(results_dir)
        expected = classify_failure(RuntimeError(message))
        assert expected == FailureClass.INTERACTION
        assert result["fail_class_distribution"]["interaction"] == 1

    def test_parse_allure_result_label_takes_priority(self):
        """atlas.fail_class label 直接采用——即使文本也能归到别的类。"""
        result = {
            "uuid": "x", "name": "n", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5", "trace": ""},
            "labels": [{"name": "atlas.fail_class", "value": "data"}],
            "parameters": [], "steps": [], "attachments": [],
        }
        finding = parse_allure_result(result)
        assert finding.fail_class == "data"   # label 优先，不走 classify_text


# ── §8 测试 13：--to-tower ────────────────────────────────────────

class TestToTower:
    """未命中项 → pending_review 草稿进塔；命中项 → heal 提案但不执行。"""

    def test_unmatched_goes_to_tower_as_pending_review(self, tmp_path):
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        kb = _make_knowledge_with_pitfall(tmp_path)

        _write_result(results_dir, {
            "uuid": "miss-1", "name": "tc_miss", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5 but got 3", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })

        journal = ExperienceJournal(tmp_path / "reports")
        result = analyze_allure(results_dir, knowledge=kb, to_tower=journal)

        assert len(result["tower_drafts"]) == 1
        drafts_path = tmp_path / "reports" / "experience_drafts.jsonl"
        assert drafts_path.exists()
        drafts = [json.loads(line) for line in
                  drafts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(drafts) == 1
        assert drafts[0]["status"] == "pending_review"
        assert drafts[0]["task"] == "tc_miss"
        assert drafts[0]["failure_class"] == "assertion"

    def test_matched_produces_heal_proposal_not_tower_draft(self, tmp_path):
        """命中项：heal 提案，不进塔。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        kb = _make_knowledge_with_pitfall(tmp_path)

        _write_result(results_dir, {
            "uuid": "hit-1", "name": "tc_hit", "status": "failed",
            "statusDetails": {"message": "Timeout waiting for element", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })

        journal = ExperienceJournal(tmp_path / "reports")
        result = analyze_allure(results_dir, knowledge=kb, to_tower=journal)

        assert len(result["heal_proposals"]) == 1
        assert result["heal_proposals"][0]["case"] == "tc_hit"
        assert result["heal_proposals"][0]["pitfall_id"] == "approved-timeout"
        assert len(result["tower_drafts"]) == 0
        # 塔文件不存在（没有未命中项）
        assert not (tmp_path / "reports" / "experience_drafts.jsonl").exists()

    def test_heal_proposal_does_not_execute(self, tmp_path):
        """自愈提案只提案不执行——不写 knowledge、不改队列（红线 #9/#10）。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        kb = _make_knowledge_with_pitfall(tmp_path)

        _write_result(results_dir, {
            "uuid": "hit-1", "name": "tc_hit", "status": "failed",
            "statusDetails": {"message": "Timeout waiting for element", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })

        journal = ExperienceJournal(tmp_path / "reports")
        before_pitfalls = len(kb.pitfalls())
        result = analyze_allure(results_dir, knowledge=kb, to_tower=journal)

        assert len(result["heal_proposals"]) == 1
        # 知识库未变（提案不执行）
        assert len(kb.pitfalls()) == before_pitfalls

    def test_to_tower_draft_has_allure_source_evidence(self, tmp_path):
        """塔草稿的 evidence 含 source=allure 和 text（供人审核阅）。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        kb = _make_knowledge_with_pitfall(tmp_path)

        _write_result(results_dir, {
            "uuid": "miss-1", "name": "tc_miss", "status": "failed",
            "statusDetails": {"message": "AssertionError: xyzzy", "trace": "at x.py:1"},
            "labels": [], "parameters": [{"name": "env", "value": "sit"}],
            "steps": [], "attachments": [],
        })

        journal = ExperienceJournal(tmp_path / "reports")
        analyze_allure(results_dir, knowledge=kb, to_tower=journal)

        drafts_path = tmp_path / "reports" / "experience_drafts.jsonl"
        drafts = [json.loads(line) for line in
                  drafts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(drafts) == 1
        ev = drafts[0].get("evidence", {})
        assert ev.get("source") == "allure"
        assert ev.get("env") == "sit"
        assert "text" in ev   # message+trace 原文

    def test_evidence_card_shows_allure_source(self, tmp_path):
        """evidence_card 显示 allure 来源与现场文本（供人审 30 秒判断）。"""
        from atlas.review import evidence_card
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        kb = _make_knowledge_with_pitfall(tmp_path)

        _write_result(results_dir, {
            "uuid": "miss-1", "name": "tc_miss", "status": "failed",
            "statusDetails": {"message": "AssertionError: xyzzy", "trace": "at x.py:1"},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })

        journal = ExperienceJournal(tmp_path / "reports")
        analyze_allure(results_dir, knowledge=kb, to_tower=journal)

        drafts_path = tmp_path / "reports" / "experience_drafts.jsonl"
        drafts = [json.loads(line) for line in
                  drafts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        card = evidence_card(drafts[0])
        assert "allure" in card
        assert "现场文本" in card


# ── §8 测试 14：控制流免疫自愈（#108）────────────────────────────

class TestControlFlowImmunity:
    """interrupted 的 Allure 结果不得产出 heal 提案（#108）。"""

    def test_interrupted_no_heal_proposal(self, tmp_path):
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        kb = _make_knowledge_with_pitfall(tmp_path)

        _write_result(results_dir, {
            "uuid": "int-1", "name": "tc_int", "status": "skipped",
            "statusDetails": {"message": "operation interrupted", "trace": ""},
            "labels": [{"name": "atlas.fail_class", "value": "interrupted"},
                       {"name": "atlas.interrupted", "value": "true"}],
            "parameters": [], "steps": [], "attachments": [],
        })

        journal = ExperienceJournal(tmp_path / "reports")
        result = analyze_allure(results_dir, knowledge=kb, to_tower=journal)

        assert result["skipped"] == 1
        assert len(result["heal_proposals"]) == 0
        assert len(result["tower_drafts"]) == 0

    def test_interrupted_not_in_fail_class_distribution(self, tmp_path):
        """interrupted 是 skipped 终态，不进 failed/broken 的 fail_class 分布。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        kb = _make_knowledge_with_pitfall(tmp_path)

        _write_result(results_dir, {
            "uuid": "int-1", "name": "tc_int", "status": "skipped",
            "statusDetails": {"message": "operation interrupted", "trace": ""},
            "labels": [{"name": "atlas.fail_class", "value": "interrupted"}],
            "parameters": [], "steps": [], "attachments": [],
        })

        result = analyze_allure(results_dir, knowledge=kb)
        assert "interrupted" not in result["fail_class_distribution"]
        # 但四桶统计里有（environment_blocked 桶含 interrupted）
        # 注意：四桶只统计 failed/broken，skipped 的 interrupted 不进桶
        assert result["buckets"]["environment_blocked"] == 0


# ── §8 测试 15：坏文件跳过计数 ──────────────────────────────────

class TestBadFileTolerance:
    """坏 JSON / 非对象 / 腰斩文件 → 跳过计数，好结果照常分析（#112/#131）。"""

    def test_bad_json_skipped_not_crash(self, tmp_path):
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()

        _write_result(results_dir, {
            "uuid": "good-1", "name": "tc_good", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5 but got 3", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })
        (results_dir / "atlas-bad-json-result.json").write_text(
            "{not valid json", encoding="utf-8")
        (results_dir / "atlas-nonobj-result.json").write_text(
            "[1,2,3]", encoding="utf-8")
        (results_dir / "atlas-truncated-result.json").write_bytes(
            json.dumps({"uuid": "x", "name": "trunc"}).encode("utf-8")[:20])

        result = analyze_allure(results_dir)
        assert result["total"] == 1
        assert result["failed"] == 1
        assert result["skipped_malformed_files"] == 3

    def test_bad_utf8_bytes_skipped(self, tmp_path):
        """坏字节文件（非法 UTF-8）→ replace 回落后 JSON 解析失败，跳过计数。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()

        _write_result(results_dir, {
            "uuid": "good-1", "name": "tc_good", "status": "passed",
            "statusDetails": {}, "labels": [], "parameters": [],
            "steps": [], "attachments": [],
        })
        # 非法 UTF-8 字节在 JSON 字符串外（替换后裸字符破坏 JSON 结构）
        (results_dir / "atlas-badbytes-result.json").write_bytes(
            b'{"uuid": \xff, "name": "bad"}')

        result = analyze_allure(results_dir)
        assert result["total"] == 1
        assert result["skipped_malformed_files"] == 1

    def test_load_allure_results_returns_findings(self, tmp_path):
        """load_allure_results 返回 list[AllureFinding]（坏文件跳过）。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        _write_result(results_dir, {
            "uuid": "g1", "name": "tc1", "status": "failed",
            "statusDetails": {"message": "AssertionError: x", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })
        (results_dir / "bad-result.json").write_text("not json", encoding="utf-8")

        findings = load_allure_results(results_dir)
        assert len(findings) == 1
        assert findings[0].name == "tc1"


# ── §8 测试 16：空结果集 ────────────────────────────────────────

class TestEmptyResults:
    """空结果集 → 返回空汇总而非崩。"""

    def test_empty_dir_returns_empty_summary(self, tmp_path):
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        result = analyze_allure(results_dir)
        assert result["total"] == 0
        assert result["passed"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["skipped_malformed_files"] == 0
        assert result["heal_proposals"] == []
        assert result["tower_drafts"] == []

    def test_nonexistent_dir_raises(self, tmp_path):
        """不存在的目录 → FileNotFoundError（CLI 接 _user_error rc 2）。"""
        with pytest.raises(FileNotFoundError):
            analyze_allure(tmp_path / "nope")


# ── 四桶映射 ─────────────────────────────────────────────────────

class TestBucketMapping:
    """§6.3 四桶映射：env_blocked/interrupted → environment_blocked 等。"""

    def test_default_buckets_cover_all_classes(self):
        """DEFAULT_BUCKETS 覆盖所有非 unknown 的 FailureClass。"""
        covered = set()
        for classes in DEFAULT_BUCKETS.values():
            covered |= classes
        for cls in FailureClass:
            if cls == FailureClass.UNKNOWN:
                continue
            assert cls.value in covered, f"{cls.value} 未被任何桶覆盖"

    def test_bucket_distribution(self, tmp_path):
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()

        cases = [
            ("env-1", "env_blocked", "skipped", "生产环境禁止建单"),
            ("data-1", "data", "failed", "查询无结果"),
            ("assert-1", "assertion", "failed", "AssertionError: expected 5"),
            ("contract-1", "contract", "failed", "contractviolation: invalid"),
            ("inter-1", "interaction", "failed", "timeout waiting"),
            ("sess-1", "session", "failed", "401 unauthorized"),
            ("unk-1", "unknown", "broken", "weird error"),
        ]
        for uid, fc, status, msg in cases:
            _write_result(results_dir, {
                "uuid": uid, "name": uid, "status": status,
                "statusDetails": {"message": msg, "trace": ""},
                "labels": [{"name": "atlas.fail_class", "value": fc}],
                "parameters": [], "steps": [], "attachments": [],
            })

        result = analyze_allure(results_dir)
        buckets = result["buckets"]
        # env_blocked 是 skipped，不进桶（只统计 failed/broken）
        assert buckets["environment_blocked"] == 0
        assert buckets["data_issue"] == 1
        assert buckets["assertion_contract"] == 2
        assert buckets["interaction_session"] == 2
        assert buckets["unknown"] == 1

    def test_custom_buckets_override(self, tmp_path):
        """buckets 参数可覆盖 DEFAULT_BUCKETS（§6.3 可配置分组）。"""
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        _write_result(results_dir, {
            "uuid": "a1", "name": "tc1", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5", "trace": ""},
            "labels": [{"name": "atlas.fail_class", "value": "assertion"}],
            "parameters": [], "steps": [], "attachments": [],
        })
        custom = {"all_in_one": frozenset({"assertion", "data", "interaction", "session"})}
        result = analyze_allure(results_dir, buckets=custom)
        assert result["buckets"]["all_in_one"] == 1
        assert result["buckets"]["unknown"] == 0


# ── format_text ─────────────────────────────────────────────────

class TestFormatText:
    def test_text_format_contains_summary(self, tmp_path):
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        _write_result(results_dir, {
            "uuid": "c1", "name": "tc1", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5 but got 3", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })
        result = analyze_allure(results_dir)
        text = format_text(result)
        assert "Allure 分析" in text
        assert "总计 1" in text
        assert "assertion" in text


# ── CLI ─────────────────────────────────────────────────────────

class TestCLI:
    def test_cli_rc0_json(self, tmp_path, capsys):
        from atlas.cli import main
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        _write_result(results_dir, {
            "uuid": "c1", "name": "tc1", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5 but got 3", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })
        rc = main(["--root", str(tmp_path), "allure-analyze", str(results_dir)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["failed"] == 1

    def test_cli_format_text(self, tmp_path, capsys):
        from atlas.cli import main
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        _write_result(results_dir, {
            "uuid": "c1", "name": "tc1", "status": "failed",
            "statusDetails": {"message": "AssertionError: expected 5 but got 3", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })
        rc = main(["--root", str(tmp_path), "allure-analyze", str(results_dir),
                   "--format", "text"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Allure 分析" in out
        assert "总计 1" in out

    def test_cli_bad_dir_rc2(self, tmp_path, capsys):
        from atlas.cli import main
        rc = main(["--root", str(tmp_path), "allure-analyze", str(tmp_path / "nope")])
        assert rc == 2
        err = capsys.readouterr().err
        assert "FileNotFoundError" in err

    def test_cli_to_tower_registers_drafts(self, tmp_path, capsys):
        """--to-tower：未命中项进塔（用 demo 知识库，消息不匹配任何 trigger）。"""
        from atlas.cli import main
        results_dir = tmp_path / "allure-results"
        results_dir.mkdir()
        _write_result(results_dir, {
            "uuid": "c1", "name": "tc_unknown", "status": "failed",
            "statusDetails": {"message": "AssertionError: xyzzy unique failure 99999", "trace": ""},
            "labels": [], "parameters": [], "steps": [], "attachments": [],
        })
        rc = main(["--root", str(tmp_path), "allure-analyze", str(results_dir),
                   "--to-tower"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert len(out["tower_drafts"]) == 1
        drafts_path = tmp_path / "reports" / "experience_drafts.jsonl"
        assert drafts_path.exists()
        drafts = [json.loads(line) for line in
                  drafts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(drafts) == 1
        assert drafts[0]["status"] == "pending_review"

"""review-code：测试代码质量评审的测试套件。

覆盖：规则扫描、计分、基线差分、运行史交叉、Allure 报告、CLI 门禁、--to-tower。
"""
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from atlas.review_code import (
    review_code, load_config, score_findings, scan_pytest_file,
    scan_dsl_flow, scan_contract_file, diff_baseline, cross_check_runs,
    emit_allure_review, findings_to_tower, format_text, format_json,
    DEFAULT_WEIGHTS, CRITICAL_CAP,
)


# ── fixtures ─────────────────────────────────────────────────────────────

def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def _default_config():
    return load_config(None)


# ── 1. 弱断言 → ASSERT-002 ───────────────────────────────────────────────

def test_weak_assertion_detected(tmp_path):
    f = _write(tmp_path, "test_weak.py", """
        def test_something():
            resp = do_call()
            assert resp is not None
    """)
    findings = scan_pytest_file(f, _default_config())
    assert any(f["rule_id"] == "ASSERT-002" for f in findings)
    assert any(f["dimension"] == "assertion" for f in findings)


# ── 2. 恒真断言 → ASSERT-001, critical, critical cap ────────────────────

def test_tautological_assertion_is_critical_and_capped(tmp_path):
    f = _write(tmp_path, "test_true.py", """
        def test_something():
            assert True
    """)
    findings = scan_pytest_file(f, _default_config())
    assert any(f["rule_id"] == "ASSERT-001" and f["severity"] == "critical"
               for f in findings)
    score = score_findings(findings, _default_config())
    assert score["total"] <= CRITICAL_CAP


# ── 3. DSL assert 弱断言 → ASSERT-005, ASSERT-004 ────────────────────────

def test_dsl_non_empty_assertion(tmp_path):
    flow = {
        "steps": [{"task": "api_call", "save_as": "doc", "read_only": True}],
        "assert": ["document_no != \"\""],
    }
    f = _write(tmp_path, "test.yaml", yaml.dump(flow))
    findings = scan_dsl_flow(f, flow, _default_config())
    assert any(f["rule_id"] == "ASSERT-005" for f in findings)


def test_dsl_path_tautology(tmp_path):
    flow = {
        "steps": [{"task": "api_call", "save_as": "doc", "read_only": True}],
        "assert": ["source_order.ptx != source_order.ptx2"],
    }
    f = _write(tmp_path, "test.yaml", yaml.dump(flow))
    findings = scan_dsl_flow(f, flow, _default_config())
    assert any(f["rule_id"] == "ASSERT-004" for f in findings)


# ── 4. except Exception: pass → GREEN-001 ────────────────────────────────

def test_swallow_exception(tmp_path):
    f = _write(tmp_path, "test_swallow.py", """
        def test_something():
            try:
                do_call()
            except Exception:
                pass
    """)
    findings = scan_pytest_file(f, _default_config())
    assert any(f["rule_id"] == "GREEN-001" and f["severity"] == "critical"
               for f in findings)


# ── 5. SAFE-003 / MAINT-001 / MAINT-002 ──────────────────────────────────

def test_time_sleep_detected(tmp_path):
    f = _write(tmp_path, "test_sleep.py", """
        import time
        def test_something():
            time.sleep(5)
    """)
    findings = scan_pytest_file(f, _default_config())
    assert any(f["rule_id"] == "MAINT-001" for f in findings)


def test_hardcoded_credential(tmp_path):
    f = _write(tmp_path, "test_cred.py", """
        def test_something():
            password = "admin123"
            url = "https://sit.example.com/api"
    """)
    findings = scan_pytest_file(f, _default_config())
    assert any(f["rule_id"] == "SAFE-001" for f in findings)


# ── 6. 契约空 path/name → COV-002 ────────────────────────────────────────

def test_contract_empty_path(tmp_path):
    f = _write(tmp_path, "core.json", json.dumps({
        "contracts": [
            {"name": "test", "method": "POST", "path": "", "level": "minimal"},
        ]
    }))
    findings = scan_contract_file(f, _default_config())
    assert any(f["rule_id"] == "COV-002" for f in findings)


def test_contract_empty_name(tmp_path):
    f = _write(tmp_path, "core.json", json.dumps({
        "contracts": [
            {"name": "", "method": "POST", "path": "/api/test", "level": "minimal"},
        ]
    }))
    findings = scan_contract_file(f, _default_config())
    assert any(f["rule_id"] == "COV-002" for f in findings)


# ── 7. 无 finding → total=100, grade A ──────────────────────────────────

def test_no_findings_full_score(tmp_path):
    f = _write(tmp_path, "test_good.py", """
        def test_something():
            resp = do_call()
            assert resp["data"]["status"] == "ACTIVE"
            assert resp["data"]["paymentTerms"] == "NET30"
    """)
    findings = scan_pytest_file(f, _default_config())
    assert findings == []
    score = score_findings(findings, _default_config())
    assert score["total"] == 100
    assert score["grade"] == "A"


# ── 8. 权重和 ≠ 1 → 配置错误 ────────────────────────────────────────────

def test_weights_not_summing_to_one(tmp_path):
    cfg = _write(tmp_path, "reviewcode.json", json.dumps({
        "weights": {"assertion": 0.5, "safety": 0.2, "maintainability": 0.2,
                     "coverage": 0.2, "duplication": 0.15},
    }))
    with pytest.raises(ValueError, match="权重和"):
        load_config(cfg)


# ── 9. 计分确定性 ────────────────────────────────────────────────────────

def test_scoring_deterministic(tmp_path):
    f = _write(tmp_path, "test_det.py", """
        def test_something():
            assert True
            assert resp is not None
    """)
    config = _default_config()
    findings1 = scan_pytest_file(f, config)
    findings2 = scan_pytest_file(f, config)
    s1 = score_findings(findings1, config)
    s2 = score_findings(findings2, config)
    assert s1["total"] == s2["total"]
    assert s1["grade"] == s2["grade"]


# ── 10. diff: assert → pytest.skip → GREEN-003 ───────────────────────────

def test_baseline_diff_assert_to_skip(tmp_path):
    current = _write(tmp_path, "current.py", """
        import pytest
        def test_something():
            resp = do_call()
            pytest.skip("skipped")
    """)
    baseline = _write(tmp_path, "baseline.py", """
        def test_something():
            resp = do_call()
            assert resp["data"]["status"] == "ACTIVE"
    """)
    cur_source = current.read_text(encoding="utf-8")
    base_source = baseline.read_text(encoding="utf-8")
    findings = diff_baseline(cur_source, base_source, "current.py", _default_config())
    assert any(f["rule_id"] == "GREEN-003" for f in findings)


# ── 11. --baseline runs.jsonl 交叉侦测 ────────────────────────────────────

def test_cross_check_runs_history(tmp_path):
    runs = _write(tmp_path, "runs.jsonl", "\n".join([
        json.dumps({"case": "test_x", "status": "failed",
                     "steps": [{"fail_class": "assertion"}]}),
        json.dumps({"case": "test_y", "status": "passed",
                     "steps": [{"ok": True}]}),
    ]))
    # 当前有弱断言 finding
    weak_findings = [{"rule_id": "ASSERT-002", "severity": "high",
                      "dimension": "assertion"}]
    cross = cross_check_runs(runs, weak_findings, _default_config())
    assert any(f["rule_id"] == "GREEN-003" for f in cross)


# ── 12. 断言被注释 → ASSERT-006 ──────────────────────────────────────────

def test_commented_assertion(tmp_path):
    f = _write(tmp_path, "test_comment.py", """
        def test_something():
            resp = do_call()
            # assert resp["data"]["status"] == "ACTIVE"
            pass
    """)
    findings = scan_pytest_file(f, _default_config())
    assert any(f["rule_id"] == "ASSERT-006" for f in findings)


# ── 13. --format allure → 产出可解析 *-result.json ───────────────────────

def test_allure_format_produces_results(tmp_path):
    f = _write(tmp_path, "test_allure.py", """
        def test_something():
            assert True
    """)
    config = _default_config()
    findings = scan_pytest_file(f, config)
    score = score_findings(findings, config)
    file_results = [{
        "file": str(f), "kind": "pytest", "findings": findings,
        "score": score, "mode": "static", "baseline_notes": [],
    }]
    out = tmp_path / "allure-out"
    result = emit_allure_review(file_results, out)
    assert result["results"] == 1
    result_files = list(out.glob("atlas-*-result.json"))
    assert len(result_files) == 1
    data = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert "atlas.score" in [l["name"] for l in data["labels"]]
    assert "atlas.grade" in [l["name"] for l in data["labels"]]


# ── 14. --format json → 结构正确 ──────────────────────────────────────────

def test_json_format_structure(tmp_path):
    f = _write(tmp_path, "test_json.py", """
        def test_something():
            assert resp is not None
    """)
    result = review_code([str(f)], format="json",
                        out=tmp_path / "out",
                        reports_dir=tmp_path / "reports")
    assert "results" in result
    assert "summary" in result
    assert "rc" in result
    assert result["results"][0]["score"]["total"] < 100


# ── 15. CLI 门禁 ──────────────────────────────────────────────────────────

def test_cli_fail_under_gate(tmp_path, capsys):
    from atlas.cli import main
    f = _write(tmp_path, "test_gate.py", """
        def test_something():
            assert True
    """)
    rc = main(["--root", str(tmp_path), "review-code", str(f),
               "--format", "json", "--fail-under", "90"])
    assert rc == 1  # critical → 门禁失败


def test_cli_bad_path_rc2(tmp_path, capsys):
    from atlas.cli import main
    rc = main(["--root", str(tmp_path), "review-code", str(tmp_path / "nonexistent.py"),
               "--format", "json"])
    assert rc == 2  # 用户输入错误


def test_cli_clean_file_rc0(tmp_path, capsys):
    from atlas.cli import main
    f = _write(tmp_path, "test_clean.py", """
        def test_something():
            resp = do_call()
            assert resp["data"]["status"] == "ACTIVE"
    """)
    rc = main(["--root", str(tmp_path), "review-code", str(f),
               "--format", "json"])
    assert rc == 0  # 全绿


# ── 16. --to-tower ───────────────────────────────────────────────────────

def test_to_tower_writes_code_review_drafts(tmp_path):
    f = _write(tmp_path, "test_tower.py", """
        def test_something():
            assert True
    """)
    config = _default_config()
    findings = scan_pytest_file(f, config)
    reports = tmp_path / "reports"
    count = findings_to_tower(findings, reports)
    assert count >= 1
    # 验证草稿在队列中
    from atlas.review import ReviewQueue
    queue = ReviewQueue(reports)
    rows = queue.pending()
    code_review_rows = [r for r in rows if r.get("kind") == "code_review"]
    assert len(code_review_rows) >= 1
    assert all(r["status"] == "pending_review" for r in code_review_rows)


def test_code_review_approve_raises(tmp_path):
    """code_review 类型的草稿不可批准（不写知识）。"""
    from atlas.review import ReviewQueue
    from atlas.experience import ExperienceJournal
    reports = tmp_path / "reports"
    journal = ExperienceJournal(reports)
    journal.append_draft({
        "id": "cr12345678ab",
        "task": "code_review: test.py:1",
        "kind": "code_review",
        "status": "pending_review",
        "failure_class": "code_review",
        "exception_type": "ASSERT-001",
        "created_at": "x",
    })
    queue = ReviewQueue(reports)
    with pytest.raises(ValueError, match="不自动写知识"):
        queue.approve(None, "cr12345678ab", remedy="x")


# ── 17. 基线坏字节容错 ────────────────────────────────────────────────────

def test_baseline_bad_bytes_skipped(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_bytes(
        json.dumps({"case": "test_x", "status": "failed",
                     "steps": [{"fail_class": "assertion"}]}).encode("utf-8")
        + b"\n" + b'{"broken": \xe4\xbd'
    )
    weak_findings = [{"rule_id": "ASSERT-002", "severity": "high",
                      "dimension": "assertion"}]
    # 不应崩溃
    cross = cross_check_runs(runs, weak_findings, _default_config())
    assert isinstance(cross, list)


# ── 18. --review-mode ai 未实现 → CLI 拒绝 ───────────────────────────────────

def test_ai_mode_rejected(tmp_path, capsys):
    """ai 模式未实现：CLI 拒绝 --review-mode ai（argparse sys.exit 2），不静默降级到 static。"""
    from atlas.cli import main
    f = _write(tmp_path, "test_ai.py", """
        def test_something():
            assert resp is not None
    """)
    with pytest.raises(SystemExit) as exc:
        main(["--root", str(tmp_path), "review-code", str(f),
              "--review-mode", "ai", "--format", "json"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err or "ai" in err


# ── 额外：DSL 生产写风险 → SAFE-002 ───────────────────────────────────────

def test_dsl_production_write_risk(tmp_path):
    flow = {
        "steps": [{"task": "create_order", "read_only": False,
                    "channel": "auto"}],
        "engine": {"env": "prod-eu"},
    }
    f = _write(tmp_path, "test.yaml", yaml.dump(flow))
    findings = scan_dsl_flow(f, flow, _default_config())
    assert any(f["rule_id"] == "SAFE-002" for f in findings)


# ── 额外：text 格式报告 ───────────────────────────────────────────────────

def test_text_format_report(tmp_path):
    f = _write(tmp_path, "test_text.py", """
        def test_something():
            assert resp is not None
    """)
    result = review_code([str(f)], format="text",
                        out=tmp_path / "out",
                        reports_dir=tmp_path / "reports")
    assert "_text" in result
    assert "总分" in result["_text"]

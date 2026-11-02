import json
import pytest
from atlas.cli import main
from atlas.reporting import summarize
from atlas.recorder import RunRecorder
from atlas.state_machine import StateMachine


def test_state_machine_loads_yaml(tmp_path):
    path = tmp_path / "sm.yaml"
    path.write_text("initial: a\nstates:\n  a:\n    - {to: b, task: go}\n  b: []\n", encoding="utf-8")
    machine = StateMachine.load(path)
    assert machine.path("b") == ["go"]
    assert "b" in machine.terminal


def test_state_machine_rejects_unknown_target_state(tmp_path):
    path = tmp_path / "sm.yaml"
    path.write_text("initial: a\nstates:\n  a:\n    - {to: b, task: go}\n  b: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        StateMachine.load(path).path("c")


def test_summary_reports_failures_and_slowest_steps(tmp_path):
    path = tmp_path / "runs.jsonl"
    ok = RunRecorder("caseA", "sit", path)
    with ok.step("1") as s:
        s["api_ms"] = 5
    ok.close("passed")
    bad = RunRecorder("caseB", "sit", path)
    with pytest.raises(TimeoutError):
        with bad.step("2"):
            raise TimeoutError()
    bad.close("failed")
    result = summarize(path)
    assert result["runs"] == 2
    assert result["cases"] == 2
    assert result["status"] == {"passed": 1, "failed": 1}
    assert result["fail_classes"] == {"interaction": 1}
    assert result["slowest_steps"][0]["case"] in {"caseA", "caseB"}


def test_summary_missing_file_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError):
        summarize(tmp_path / "missing.jsonl")


def test_cli_machine_and_selectors(tmp_path, capsys):
    sm = tmp_path / "sm.yaml"
    sm.write_text("initial: a\nstates:\n  a:\n    - {to: b, task: go}\n  b: []\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "machine", str(sm), "--target", "b"]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == ["go"]
    assert main(["--root", str(tmp_path), "selectors"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "payment_terms" in out["components"]


def test_cli_tasks_outputs_full_catalog(tmp_path, capsys):
    """atlas tasks 输出所有核心 Task 的元数据，供 agent 拼装。"""
    assert main(["--root", str(tmp_path), "tasks"]) == 0
    tasks = json.loads(capsys.readouterr().out)
    names = {t["name"] for t in tasks}
    assert {"open_doc_list", "click_new", "select_customer", "click_save", "wait_save_result"} <= names
    # 元数据完整性
    for t in tasks:
        assert t["domain"] == "demo"
        assert t["channel"] in {"ui", "api", "auto"}


def test_cli_compose_topologically_orders_for_goal(tmp_path, capsys):
    """atlas compose --goal document_no 按依赖排序输出 Task 名。"""
    assert main(["--root", str(tmp_path), "compose", "--goal", "document_no"]) == 0
    composed = capsys.readouterr().out.strip().splitlines()
    assert "open_doc_list" in composed
    assert "click_save" in composed
    assert composed.index("open_doc_list") < composed.index("click_save")
    assert composed.index("click_save") < composed.index("wait_save_result")


def test_cli_contracts_reports_level_distribution(tmp_path, capsys):
    """atlas contracts 输出契约分级覆盖情况。"""
    assert main(["--root", str(tmp_path), "contracts"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["levels"]["core"] >= 1
    assert out["levels"]["high"] >= 1
    assert out["total"] >= 7


def test_cli_knowledge_queries_pitfalls(tmp_path, capsys):
    """atlas knowledge 输出坑点知识库。"""
    assert main(["--root", str(tmp_path), "knowledge"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) >= 30
    assert all(r.get("status") == "approved" for r in rows)


def test_cli_agent_propose_plans_without_executing(tmp_path, capsys):
    """atlas agent --goal 只规划不执行，输出风险与 Task 清单。"""
    assert main(["--root", str(tmp_path), "agent", "--goal", "document_no"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["goal"] == "document_no"
    assert "risk" in out
    assert "auto_allowed" in out
    assert len(out["tasks"]) >= 7
    assert all("channel" in t and "read_only" in t for t in out["tasks"])


def test_cli_recycle_empty_list(tmp_path, capsys):
    """atlas recycle 无回收清单时输出空列表。"""
    assert main(["--root", str(tmp_path), "recycle",
                 "--input", str(tmp_path / "nonexistent.jsonl")]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_doctor_reports_environment(tmp_path, capsys):
    """atlas doctor 输出环境与资产检查信息。"""
    main(["--root", str(tmp_path), "doctor"])
    out = json.loads(capsys.readouterr().out)
    assert "python" in out and "env" in out
    assert "legacy_pages" in out and "demo_api" in out
    assert out["knowledge_pitfalls"] == 30


def test_cli_legacy_dry_run_outputs_command(tmp_path, capsys):
    """atlas legacy --dry-run 输出 pytest 命令与 cwd，不执行。"""
    assert main(["--root", str(tmp_path), "legacy", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "pytest" in out
    assert "cwd=" in out


def test_cli_report_exports_runlog_format(tmp_path):
    """atlas report 将 JSONL 转为 runlog 步骤格式。"""
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "run_id": "r1", "case": "test", "env": "sit",
        "steps": [{"step": "go", "op": "action", "ok": True, "dur_ms": 10}]
    }) + "\n", encoding="utf-8")
    output = tmp_path / "runlog.jsonl"
    assert main(["--root", str(tmp_path), "report", str(runs), str(output)]) == 0
    line = json.loads(output.read_text(encoding="utf-8").strip())
    assert line["run_id"] == "r1"
    assert line["case"] == "test"
    assert line["step"] == "go"
    assert line["ok"] is True


def test_cli_tasks_output_is_utf8_decodable():
    """Windows 控制台默认 GBK，CLI 中文 intent 曾乱码；main 强制 stdout UTF-8。"""
    import io, sys
    from pathlib import Path
    raw = io.BytesIO()
    buf = io.TextIOWrapper(raw, encoding="gbk")  # 模拟 Windows GBK 控制台
    old = sys.stdout
    sys.stdout = buf
    try:
        assert main(["--root", str(Path(".")), "tasks"]) == 0
    finally:
        buf.flush()
        sys.stdout = old
        buf.detach()  # 只解绑，不关底层 BytesIO
    text = raw.getvalue().decode("utf-8")
    assert "打开业务单据列表" in text  # 中文 intent 正常而非乱码


def test_cli_compile_validates_flow_offline(tmp_path, capsys):
    """atlas compile 离线校验 flow：任务引用、assert 块早失败，不登录不执行。"""
    flow = tmp_path / "f.yaml"
    flow.write_text(
        "steps:\n  - {task: open_doc_list}\n"
        "assert:\n  - document_no != ''\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(flow)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tasks"] == ["open_doc_list"]
    assert out["asserts"] == 1


def test_cli_compile_rejects_unknown_task(tmp_path):
    import pytest
    flow = tmp_path / "bad.yaml"
    flow.write_text("steps:\n  - {task: no_such_task}\n", encoding="utf-8")
    with pytest.raises(KeyError):
        main(["--root", str(tmp_path), "compile", str(flow)])


def test_summary_tolerates_malformed_jsonl_lines(tmp_path):
    """崩溃时 JSONL 可能写了一半的行；summarize 跳过并计数，不静默也不崩溃。"""
    import json as _json
    p = tmp_path / "broken.jsonl"
    p.write_text(
        _json.dumps({"run_id": "r1", "case": "c1", "status": "passed", "steps": []}) + "\n"
        + "NOT_JSON\n"
        + _json.dumps({"run_id": "r2", "case": "c1", "status": "failed", "steps": []}) + "\n",
        encoding="utf-8")
    result = summarize(p)
    assert result["runs"] == 2
    assert result["skipped_malformed_lines"] == 1
    assert result["status"] == {"passed": 1, "failed": 1}


def test_recorder_close_is_fsynced(tmp_path):
    """close() 必须 fsync：进程崩溃时 JSONL 不丢记录、不产生残行。"""
    import os
    from unittest.mock import patch
    from atlas.recorder import RunRecorder
    p = tmp_path / "runs.jsonl"
    rec = RunRecorder("c1", "sit", p)
    with rec.step("s1"):
        pass
    with patch("os.fsync", wraps=os.fsync) as spy:
        rec.close()
    assert spy.called  # close 内部真实调用了 fsync
    assert p.exists() and rec.close() == p  # 幂等不重复写


def test_cli_doctor_reports_base_url_source(tmp_path, monkeypatch, capsys):
    """base_url 口径必须与 runtime 一致：Atlas .env 缺失时继承旧项目 .env。

    背景：本机 Atlas 无 .env 时 doctor 曾报 false，但 atlas run 实际能连
    （继承 legacy .env），误导诊断。unset/legacy.env/atlas.env 三种来源如实上报。
    """
    monkeypatch.delenv("DEMO_URL_SIT", raising=False)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["base_url_source"] == "unset"
    assert checks["base_url_configured"] is False

    (legacy / ".env").write_text("DEMO_URL_SIT=https://legacy.example\n", encoding="utf-8")
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["base_url_source"] == "legacy.env"
    assert checks["base_url_configured"] is True

    monkeypatch.delenv("DEMO_URL_SIT", raising=False)
    (tmp_path / ".env").write_text("DEMO_URL_SIT=https://atlas.example\n", encoding="utf-8")
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["base_url_source"] == "atlas.env"


def test_cli_doctor_checks_docker_compose_consistency(tmp_path, capsys):
    """doctor 校验 Docker/compose 资产一致性：python/python3 漂移时报非零。"""
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    # 无 Docker 资产的 tmp root 视为一致
    assert checks["docker_assets_consistent"] is True

    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.12-slim\nRUN python3 -m pytest -q\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  x:\n    command: sh -c \"python -m pytest -q\"\n", encoding="utf-8")
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["docker_cmd_matches_compose"] is False
    assert checks["docker_assets_consistent"] is False

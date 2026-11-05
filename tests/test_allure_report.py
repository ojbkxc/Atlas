"""Gap5 P1：Allure 2.39.0 结果集产出（§8 测试 1–10）+ resolve_plugin 引擎缝零回归。

映射口径见 docs/gaps/gap5-allure.md §3.5：env_blocked/interrupted 映射 skipped 但带
label（红线 #7：不洗绿、不进 failed）；unknown → broken；historyId=sha1(env|case) 稳定。
"""
import hashlib
import json

import pytest

from atlas.allure_report import emit_allure, write_attachment
from atlas.recorder import RunRecorder


def _write_runs(path, runs):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in runs) + "\n",
                    encoding="utf-8")


def _read_results(out_dir):
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(out_dir.glob("atlas-*-result.json"))]


def _labels(result):
    return {label["name"]: label["value"] for label in result["labels"]}


# ---- §8 测试 1：单 run（passed）→ 1 个 result.json，status=passed，historyId 稳定 ----

def test_single_passed_run_emits_one_result(tmp_path):
    runs = tmp_path / "runs.jsonl"
    recorder = RunRecorder("tc1", "sit", runs)
    with recorder.step("open_doc_list", "ui") as step:
        step["api_ms"] = 0
    recorder.close("passed")
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out)
    assert summary["results"] == 1 and summary["skipped_malformed_lines"] == 0
    results = _read_results(out)
    assert len(results) == 1
    result = results[0]
    assert result["status"] == "passed"
    assert result["name"] == "tc1"
    assert result["fullName"] == "atlas.sit.tc1"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["name"] == "open_doc_list"
    assert result["steps"][0]["status"] == "passed"
    assert result["start"] and result["stop"] >= result["start"]   # ISO → epoch 毫秒
    history_id = result["historyId"]
    emit_allure(runs, out)   # 再跑一次
    assert _read_results(out)[0]["historyId"] == history_id        # 稳定


# ---- §8 测试 2：混合 run：断言失败 → status=failed + atlas.fail_class=assertion ----

def test_failed_run_maps_to_failed_with_assertion_label(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_assert", "env": "sit", "status": "failed",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "save", "op": "ui", "ok": False, "fail_class": "assertion",
                   "dur_ms": 20}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "failed"
    assert _labels(result)["atlas.fail_class"] == "assertion"
    assert result["steps"][0]["status"] == "failed"
    assert _labels(result["steps"][0])["atlas.fail_class"] == "assertion"


def test_env_skipped_step_does_not_mask_real_assertion_failure(tmp_path):
    """防呆：run 记 failed 时，被环境跳过的步骤（env_blocked，不 raise）不得顶掉
    后面真实断言失败的归因——否则 failed run 被错映射成 skipped（假绿同族）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_mix", "env": "sit", "status": "failed",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "probe", "op": "ui", "ok": False, "fail_class": "env_blocked", "dur_ms": 5},
                  {"step": "save", "op": "ui", "ok": False, "fail_class": "assertion", "dur_ms": 20}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "failed"                       # 真实断言失败不被 env-skip 顶掉
    assert _labels(result)["atlas.fail_class"] == "assertion"


def test_passed_run_with_env_skipped_step_keeps_run_passed(tmp_path):
    """run 记 passed 但含被环境跳过的步骤：run 级 passed，步骤级 skipped+env_blocked label
    （§3.5 行 6——步骤被跳过明确不是 PASSED，但整体 run 终态仍是 passed）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_skip_flow", "env": "sit", "status": "passed",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "ok_step", "op": "ui", "ok": True, "dur_ms": 10},
                  {"step": "skipped_step", "op": "ui", "ok": False,
                   "fail_class": "env_blocked", "dur_ms": 1}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "passed"
    assert result["steps"][1]["status"] == "skipped"
    assert _labels(result["steps"][1])["atlas.fail_class"] == "env_blocked"


# ---- §8 测试 3：env_blocked → skipped + atlas.blocked=true（绝不进 failed 也不静默绿）----

def test_env_blocked_maps_to_skipped_with_blocked_label(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_env", "env": "sit", "status": "env_blocked",
        "reason": "生产环境禁止建单",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "write", "op": "ui", "ok": False, "fail_class": "env_blocked",
                   "dur_ms": 5}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "skipped"          # 不进 failed
    assert result["status"] != "passed"           # 不静默绿
    labels = _labels(result)
    assert labels["atlas.fail_class"] == "env_blocked"
    assert labels["atlas.blocked"] == "true"
    assert "生产环境禁止建单" in result["statusDetails"]["message"]


# ---- §8 测试 4：interrupted → skipped + atlas.interrupted=true ----

def test_interrupted_maps_to_skipped_with_interrupted_label(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_int", "env": "sit", "status": "interrupted",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "slow", "op": "ui", "ok": False, "fail_class": "interrupted",
                   "dur_ms": 5}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "skipped"
    labels = _labels(result)
    assert labels["atlas.fail_class"] == "interrupted"
    assert labels["atlas.interrupted"] == "true"
    assert result["status"] != "failed" and result["status"] != "passed"


# ---- §8 测试 5：unknown → broken ----

def test_unknown_fail_class_maps_to_broken(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_unk", "env": "sit", "status": "failed",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "weird", "op": "ui", "ok": False, "fail_class": "unknown",
                   "dur_ms": 5}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "broken"
    assert _labels(result)["atlas.fail_class"] == "unknown"


def test_missing_run_status_maps_to_broken(tmp_path):
    """run 状态缺失/未知：broken 不伪装（假阴性温床：感知器官坏了却读成无失败）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "tc_nostatus", "env": "sit", "steps": []}])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "broken"
    assert _labels(result)["atlas.fail_class"] == "unknown"


def test_failed_run_without_fail_class_maps_to_broken(tmp_path):
    """run 记 failed 但步骤无 fail_class（无法归因）：broken，不猜也不静默绿。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_nofc", "env": "sit", "status": "failed",
        "steps": [{"step": "x", "op": "ui", "ok": False, "dur_ms": 5}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["status"] == "broken"
    assert _labels(result)["atlas.fail_class"] == "unknown"


# ---- §8 测试 6：historyId 跨两次运行同 case 相同（趋势聚合）----

def test_history_id_stable_across_runs_and_matches_sha1(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [
        {"run_id": "r1", "case": "tcX", "env": "sit", "status": "passed", "steps": []},
        {"run_id": "r2", "case": "tcX", "env": "sit", "status": "passed", "steps": []},
    ])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    results = _read_results(out)
    assert len(results) == 2
    assert results[0]["historyId"] == results[1]["historyId"]
    assert results[0]["historyId"] == hashlib.sha1(b"sit|tcX").hexdigest()
    assert results[0]["uuid"] != results[1]["uuid"]     # uuid 每份独立


# ---- §8 测试 7：clean=True 只清 Atlas 前缀文件，不误删用户附件 ----

def test_clean_only_removes_atlas_prefixed_files(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "c", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    out.mkdir()
    user_file = out / "user-evidence.png"
    user_file.write_bytes(b"\x89PNG user data")
    emit_allure(runs, out, clean=True)
    assert user_file.exists()                           # 用户附件不误删
    assert user_file.read_bytes() == b"\x89PNG user data"
    first = {p.name for p in out.glob("atlas-*-result.json")}
    assert len(first) == 1
    emit_allure(runs, out, clean=True)                  # 再跑：旧 Atlas 结果被清
    second = {p.name for p in out.glob("atlas-*-result.json")}
    assert len(second) == 1
    assert first.isdisjoint(second)                     # 旧 Atlas 文件清掉了
    assert user_file.exists()


# ---- §8 测试 8：附件源文件缺失/坏字节 → 生成占位附件，不崩 ----

def test_missing_attachment_source_yields_placeholder(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_att", "env": "sit", "status": "failed",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "save", "op": "ui", "ok": False, "fail_class": "assertion",
                   "dur_ms": 10, "attachment": "missing_shot.png"}],
        "attachments": [{"path": "gone.txt", "label": "证据"}],
    }])
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out, attachments_dir=tmp_path / "evidence")
    assert summary["results"] == 1
    result = _read_results(out)[0]
    assert result["attachments"][0]["type"] == "text/plain"      # 占位降级
    placeholder = out / result["attachments"][0]["source"]
    assert "gone.txt" in placeholder.read_text(encoding="utf-8")  # 占位说明带源路径
    assert result["steps"][0]["attachments"][0]["type"] == "text/plain"
    assert list(out.glob("atlas-*-attachment.txt"))               # 占位文件落盘


def test_attachment_bad_bytes_yields_placeholder(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "bad.txt").write_bytes(b"\xff\xfe\x00 invalid utf8 \x80")
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_badatt", "env": "sit", "status": "failed",
        "started_at": "2026-11-04T10:00:00+00:00", "finished_at": "2026-11-04T10:00:01+00:00",
        "steps": [{"step": "save", "op": "ui", "ok": False, "fail_class": "assertion",
                   "dur_ms": 10, "attachment": {"path": "bad.txt", "label": "坏证据"}}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out, attachments_dir=evidence)
    result = _read_results(out)[0]
    assert result["steps"][0]["attachments"][0]["type"] == "text/plain"


def test_write_attachment_good_file_copied_with_type(tmp_path):
    out = tmp_path / "allure-results"
    out.mkdir()
    source = tmp_path / "shot.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    entry = write_attachment(out, source, "失败截图")
    assert entry["type"] == "image/png"
    copied = out / entry["source"]
    assert copied.read_bytes() == source.read_bytes()


def test_write_attachment_missing_source_placeholder(tmp_path):
    out = tmp_path / "allure-results"
    out.mkdir()
    entry = write_attachment(out, tmp_path / "nope.png", "缺失截图")
    assert entry["type"] == "text/plain"
    assert (out / entry["source"]).exists()


# ---- §8 测试 9：读侧容错：尾行腰斩坏字节 → 不整读崩，合法 run 照常产出 ----

def test_truncated_utf8_tail_does_not_break_emit(tmp_path):
    runs = tmp_path / "runs.jsonl"
    good = {"run_id": "r1", "case": "TC1 中文", "env": "sit", "status": "passed",
            "steps": [{"step": "go", "op": "ui", "ok": True, "dur_ms": 10}]}
    runs.write_bytes(
        json.dumps(good, ensure_ascii=False).encode("utf-8") + b"\n"
        + b'{"run_id": "r2", "case": "\xe4\xbd'   # 腰斩多字节：只写了「你」的前两字节
    )
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out)
    assert summary["results"] == 1                    # 截断前的 r1 照常产出
    assert summary["skipped_malformed_lines"] == 1    # 腰斩行计数上报
    result = _read_results(out)[0]
    assert result["status"] == "passed" and result["name"] == "TC1 中文"


def test_emit_survives_non_object_lines(tmp_path):
    """null/裸数字/残行都计入 skipped，好行照常产出（与 reporting 同容错口径）。"""
    runs = tmp_path / "runs.jsonl"
    good = {"run_id": "r1", "case": "A", "env": "sit", "status": "passed", "steps": []}
    runs.write_text(
        json.dumps(good) + "\n" + "null\n123\n{坏残行\n", encoding="utf-8")
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out)
    assert summary["results"] == 1
    assert summary["skipped_malformed_lines"] == 3


def test_emit_survives_malformed_steps(tmp_path):
    """steps:[null]/字符串步、非数值 dur_ms 不得顶掉整份结果（与 reporting 同口径）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "A", "env": "sit", "status": "passed",
        "steps": [None, "junk", {"step": "ok", "dur_ms": 5},
                  {"step": "weird", "dur_ms": "abc"}],
    }])
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out)
    assert summary["results"] == 1 and summary["steps"] == 2
    result = _read_results(out)[0]
    assert [s["name"] for s in result["steps"]] == ["ok", "weird"]


# ---- §8 测试 10：CLI e2e：rc0；坏路径 rc2 ----

def test_cli_allure_e2e_rc0(tmp_path, capsys):
    from atlas.cli import main
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "c", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    rc = main(["--root", str(tmp_path), "allure", str(runs), "--out", str(out)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["results"] == 1
    assert list(out.glob("atlas-*-result.json"))


def test_cli_allure_defaults_to_reports_dir(tmp_path, capsys):
    """缺省输入 <root>/reports/runs.jsonl、输出 <root>/reports/allure-results（不赌 CWD）。"""
    from atlas.cli import main
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_runs(reports / "runs.jsonl",
                [{"run_id": "r1", "case": "c", "env": "sit", "status": "passed", "steps": []}])
    rc = main(["--root", str(tmp_path), "allure"])
    assert rc == 0
    assert list((reports / "allure-results").glob("atlas-*-result.json"))


def test_cli_allure_bad_path_rc2(tmp_path, capsys):
    from atlas.cli import main
    rc = main(["--root", str(tmp_path), "allure", str(tmp_path / "nope.jsonl"),
               "--out", str(tmp_path / "out")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "FileNotFoundError" in err          # 一行 stderr + rc 2（_user_error 口径）


def test_cli_allure_serve_prints_command_not_launch(tmp_path, capsys):
    from atlas.cli import main
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "c", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    rc = main(["--root", str(tmp_path), "allure", str(runs), "--out", str(out), "--serve"])
    assert rc == 0
    err = capsys.readouterr().err
    assert f"allure serve {out}" in err        # 只打印命令，不自行拉起


# ---- resolve_plugin 引擎缝零回归 ----

def test_resolve_plugin_defaults_to_demo(monkeypatch):
    """未设 ATLAS_PLUGIN：resolve_plugin() 返回 DemoPlugin 实例，行为与旧 DemoPlugin() 一致。"""
    from atlas.plugin_api import resolve_plugin
    from atlas.plugins.demo import DemoPlugin
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    assert isinstance(resolve_plugin(), DemoPlugin)


def test_resolve_plugin_explicit_demo(monkeypatch):
    from atlas.plugin_api import resolve_plugin
    from atlas.plugins.demo import DemoPlugin
    monkeypatch.setenv("ATLAS_PLUGIN", "demo")
    assert isinstance(resolve_plugin(), DemoPlugin)


def test_resolve_plugin_unknown_domain_raises(monkeypatch):
    from atlas.plugin_api import resolve_plugin
    monkeypatch.setenv("ATLAS_PLUGIN", "nope")
    with pytest.raises(ValueError, match="未知业务插件"):
        resolve_plugin()


def test_resolve_plugin_seam_cli_commands_unchanged(tmp_path, monkeypatch, capsys):
    """缝零回归：knowledge/contracts 等装配插件的命令行为不变（未设 ATLAS_PLUGIN）。"""
    from atlas.cli import main
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    assert main(["--root", str(tmp_path), "knowledge"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) >= 30
    assert main(["--root", str(tmp_path), "contracts"]) == 0
    assert main(["--root", str(tmp_path), "tasks"]) == 0


def test_resolve_plugin_seam_cli_rejects_unknown_domain(tmp_path, monkeypatch, capsys):
    """ATLAS_PLUGIN 指到未注册 domain：CLI 一行 stderr + rc 2（用户输入口径）。"""
    from atlas.cli import main
    monkeypatch.setenv("ATLAS_PLUGIN", "nope")
    assert main(["--root", str(tmp_path), "tasks"]) == 2
    assert "未知业务插件" in capsys.readouterr().err


# ---- #139：取证截图 → runs.jsonl → Allure 结果集全链（生产侧接线断链回归）----

def _engine_runs(tmp_path, tag, mode, fail):
    """走真实 engine+recorder 链产 runs.jsonl（FakeSession 落真实 png），返回 (step记录, runs路径)。"""
    import json as _json
    from atlas.config import AtlasConfig
    from atlas.core import Channel, TaskMetadata
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder
    from atlas import Actor, FunctionalTask

    shots = tmp_path / "artifacts" / "snapshots"

    class FakeSession:
        def capture(self, step, success):
            if success and mode == "run":
                return None          # 红线 #8：run 模式成功步骤零截图开销
            shots.mkdir(parents=True, exist_ok=True)
            path = shots / f"{step}.png"
            path.write_bytes(b"png-bytes")
            return path

    runs = tmp_path / "reports" / f"runs_{tag}.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    config = AtlasConfig(tmp_path, env="sit", mode=mode)
    recorder = RunRecorder(f"case_{tag}", "sit", runs)
    engine = ExecutionEngine(config, recorder=recorder, session=FakeSession())
    task = FunctionalTask(TaskMetadata("demo", "取证链", channel=Channel.UI, read_only=True),
                          (lambda actor: (_ for _ in ()).throw(AssertionError("boom"))) if fail
                          else (lambda actor: {"v": 1}), tag)
    try:
        engine.execute(Actor(), task)
    except BaseException:
        pass
    recorder.close("failed" if fail else "passed")
    line = _json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
    return line["steps"][0], runs


def test_engine_failure_shot_recorded_root_relative(tmp_path):
    """#138 同族断链（产出→落盘→消费）：capture() 截图真实落盘，但旧实现三处调用全
    丢弃返回值——runs.jsonl 里 attachment 键永不存在，atlas allure 附件永远 0 条，
    失败现场在报告里无声消失（截图存在≠截图可追溯）。修复后失败取证按 root 相对
    路径写进 step 记录（runs.jsonl 可携，换机不悬空）。"""
    step, runs = _engine_runs(tmp_path, "fail", "run", fail=True)
    assert step.get("attachment") is not None
    assert (tmp_path / step["attachment"]).is_file()   # root 相对引用可解析
    assert "artifacts" in step["attachment"]


def test_engine_run_success_records_no_attachment(tmp_path):
    """#115 零涟漪 + 红线 #8：run 模式成功步骤 capture 返回 None——记录里不得出现
    attachment 键（更不得存 null 假合法形态，#77 同族）；debug 成功取证则必须记。"""
    step_ok, _ = _engine_runs(tmp_path, "ok", "run", fail=False)
    assert "attachment" not in step_ok
    step_dbg, _ = _engine_runs(tmp_path, "dbg", "debug", fail=False)
    assert (tmp_path / step_dbg["attachment"]).is_file()


def test_engine_recorded_shot_copied_into_allure_results(tmp_path):
    """闭环：engine 记录的 root 相对附件经 emit_allure(attachments_dir=root) 复制为
    真实 png 进结果集——不是「源缺失」占位文本（文件明明在盘上却假报无法读取，
    #102 坏形态伪装同族）。"""
    _, runs = _engine_runs(tmp_path, "chain", "run", fail=True)
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out, clean=True, attachments_dir=tmp_path)
    assert summary["attachments"] == 1
    assert list(out.glob("atlas-*-attachment.png"))
    assert not list(out.glob("atlas-*-attachment.txt"))


def test_cli_allure_uses_project_root_attachment_base(tmp_path, monkeypatch, capsys):
    """#139 第二面：CLI `atlas allure` 旧实现不传 attachments_dir——消费侧回落
    runs_path.parent（reports/）做基准，engine 写的 `artifacts/snapshots/*.png`
    相对引用解析成 reports/artifacts/... 假报「无法读取」生成占位（证据在盘上却
    永远读不到）。修复后 CLI 显式传 config.root。"""
    import json
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    shot = tmp_path / "artifacts" / "snapshots" / "x.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"png")
    run = {"run_id": "r", "case": "c", "env": "sit", "status": "failed",
           "steps": [{"step": "s", "ok": False, "dur_ms": 1,
                      "fail_class": "assertion", "attachment": "artifacts/snapshots/x.png"}]}
    runs = tmp_path / "reports" / "runs.jsonl"
    runs.parent.mkdir(exist_ok=True)
    runs.write_text(json.dumps(run) + "\n", encoding="utf-8")
    from atlas.cli import main
    assert main(["--root", str(tmp_path), "allure"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["attachments"] == 1
    out = tmp_path / "reports" / "allure-results"
    assert list(out.glob("atlas-*-attachment.png"))      # 真实复制，非占位

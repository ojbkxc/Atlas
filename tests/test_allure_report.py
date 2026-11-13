"""Gap5 P1：Allure 2.39.0 结果集产出（§8 测试 1–10）+ resolve_plugin 引擎缝零回归。

映射口径见 docs/gaps/gap5-allure.md §3.5：env_blocked/interrupted 映射 skipped 但带
label（红线 #7：不洗绿、不进 failed）；unknown → broken；historyId=sha1(env|case) 稳定。
"""
import hashlib
import json

import pytest

from atlas.allure_report import (
    default_categories, emit_allure, write_attachment, write_categories,
    write_environment_properties, write_executor,
)
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


# ---- --clean/--no-clean：no-op 已消除（默认即清，开关真的能关） ----

def test_cli_allure_clean_parser_toggle():
    """`--clean` 曾恒等于写死 clean=True（args.clean 从不被读 = no-op）。

    现 `--clean/--no-clean` 是真开关：默认 True（默认即清），`--no-clean` 能关。
    """
    from atlas.cli import _parser
    p = _parser()
    assert p.parse_args(["--root", ".", "allure"]).clean is True
    assert p.parse_args(["--root", ".", "allure", "--clean"]).clean is True
    assert p.parse_args(["--root", ".", "allure", "--no-clean"]).clean is False


def test_cli_allure_no_clean_is_effective_not_noop(tmp_path, capsys):
    """用可观测行为钉死 args.clean 真正影响 emit_allure 的 clean 实参。

    预置一个陈旧 atlas-* 结果文件：缺省 / --clean → 被清（clean=True）；
    --no-clean → 保留（clean=False）。旧实现恒 clean=True，第三条断言当场变红。
    """
    from atlas.cli import main
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "c", "env": "sit", "status": "passed", "steps": []}])

    def run(*extra, out_name):
        out = tmp_path / out_name
        out.mkdir()
        stale = out / "atlas-0000-stale-result.json"
        stale.write_text("{}", encoding="utf-8")
        rc = main(["--root", str(tmp_path), "allure", str(runs), "--out", str(out), *extra])
        capsys.readouterr()
        return rc, stale.exists()

    assert run(out_name="a") == (0, False)                 # 缺省 = clean=True（历史行为）
    assert run("--clean", out_name="b") == (0, False)      # 显式 --clean = True
    assert run("--no-clean", out_name="c") == (0, True)    # --no-clean = False（真正可关）


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


# ---- C6：版本三元组进 parameters（design_c6 §5.5）-----------------------

def test_old_records_without_version_keep_env_only_parameters(tmp_path):
    """存量结果集行为逐字不变：没有 version 键 → parameters 恒 [{env}]（#115 零涟漪）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "tc_noversion", "env": "sit",
                        "status": "passed", "steps": [],
                        "started_at": "2026-11-06T00:00:00+00:00",
                        "finished_at": "2026-11-06T00:00:01+00:00"}])
    emit_allure(runs, tmp_path / "allure-results")
    result = _read_results(tmp_path / "allure-results")[0]
    assert result["parameters"] == [{"name": "env", "value": "sit"}]


def test_version_triple_lands_as_three_parameters_with_calibre_suffix(tmp_path):
    """三元组进 parameters，值带 #source/#kind 口径后缀；labels/historyId 不掺版本。

    historyId 掺版本会把同一用例的跨版本趋势断成多条线——恰恰看不见漂移（设计 §2.4）。
    """
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_versioned", "env": "sit", "status": "passed",
        "steps": [], "started_at": "2026-11-06T00:00:00+00:00",
        "finished_at": "2026-11-06T00:00:01+00:00",
        "version": {"atlas": "0.1.0",
                    "knowledge": {"sha1": "1a2b3c4d5e6f", "source": "private", "files": 30},
                    "flow": {"md5": "9f8e7d", "kind": "source"}}}])
    emit_allure(runs, tmp_path / "allure-results")
    result = _read_results(tmp_path / "allure-results")[0]
    params = {p["name"]: p["value"] for p in result["parameters"]}
    assert params["atlas_version"] == "0.1.0"
    assert params["knowledge_sha1"] == "1a2b3c4d5e6f#private"
    assert params["flow_md5"] == "9f8e7d#source"
    assert params["env"] == "sit"
    assert not [l for l in result["labels"] if "version" in l["name"] or "sha1" in l["name"]]
    history_id = result["historyId"]
    # 同 case 换知识指纹（历史版本行）→ historyId 不变，趋势仍然连得上
    _write_runs(runs, [{
        "run_id": "r2", "case": "tc_versioned", "env": "sit", "status": "passed",
        "steps": [], "started_at": "2026-11-05T00:00:00+00:00",
        "finished_at": "2026-11-05T00:00:01+00:00",
        "version": {"atlas": "0.1.0",
                    "knowledge": {"sha1": "000000000000", "source": "builtin", "files": 7},
                    "flow": {"md5": "9f8e7d", "kind": "derived"}}}])
    emit_allure(runs, tmp_path / "allure-results")
    assert _read_results(tmp_path / "allure-results")[0]["historyId"] == history_id


def test_partial_version_shape_does_not_crash_result(tmp_path):
    """坏形态（version 是 list、knowledge 缺键）：结果集照常出，只少了缺的那几项。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [
        {"run_id": "r1", "case": "tc_bad1", "env": "sit", "status": "passed", "steps": [],
         "version": ["不是映射"]},
        {"run_id": "r2", "case": "tc_bad2", "env": "sit", "status": "passed", "steps": [],
         "version": {"atlas": "0.1.0", "knowledge": None, "flow": {"md5": "", "kind": ""}}},
    ])
    summary = emit_allure(runs, tmp_path / "allure-results")
    assert summary["results"] == 2
    results = _read_results(tmp_path / "allure-results")
    by_case = {r["name"]: r["parameters"] for r in results}
    assert by_case["tc_bad1"] == [{"name": "env", "value": "sit"}]
    assert {p["name"] for p in by_case["tc_bad2"]} == {"env", "atlas_version"}


def test_unreadable_fingerprint_is_visible_not_silent(tmp_path):
    """source=unreadable 时 sha1 是空串——Allure 里仍要看得见「读崩」这个结论。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "tc_unreadable", "env": "sit", "status": "passed",
        "steps": [], "started_at": "2026-11-06T00:00:00+00:00",
        "finished_at": "2026-11-06T00:00:01+00:00",
        "version": {"atlas": "0.1.0",
                    "knowledge": {"sha1": "", "source": "unreadable", "error": "PermissionError"},
                    "flow": None}}])
    emit_allure(runs, tmp_path / "allure-results")
    params = {p["name"]: p["value"] for p in _read_results(tmp_path / "allure-results")[0]["parameters"]}
    assert params["knowledge_sha1"] == "-#unreadable"
    assert "flow_md5" not in params, "flow=None 是「没算」，不冒充一个空摘要"


# ---- Gap6：产出侧对齐 UI AUTO TEST（label / 字段面 / 结果集组成部分）----
#
# UI AUTO TEST 用 allure-pytest 产出 result.json，字段面固定含 parentSuite/suite/subSuite/
# package/framework/language/severity label + description + titlePath（见
# D:/GitHub/UI AUTO TEST/iservice/reports/allure-results/*-result.json 真实取证）。
# Atlas 直出对齐其中确定性可复现的子集，host/thread 机器相关不伪造。


def test_aligned_result_matches_ui_auto_test_label_set(tmp_path):
    """对齐后 label 集覆盖 UI AUTO TEST 用到的确定性项；既有 suite/severity 值不变。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "TC1", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    labels = _labels(_read_results(out)[0])
    assert labels["parentSuite"] == "Atlas"
    assert labels["suite"] == "atlas"            # 历史值不变（零涟漪）
    assert labels["subSuite"] == "sit"
    assert labels["package"] == "atlas.sit"
    assert labels["framework"] == "atlas"
    assert labels["language"] == "python"
    assert labels["severity"] == "normal"
    # host/thread 机器相关——不伪造（离线确定性）
    assert "host" not in labels and "thread" not in labels


def test_aligned_result_carries_description_and_title_path(tmp_path):
    """UI AUTO TEST 的 result.json 恒带 description/titlePath（附加，不改既有键）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "TC1", "env": "sit", "status": "passed",
                        "description": "创建销售订单验 PT 落库", "steps": []}])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    assert result["description"] == "创建销售订单验 PT 落库"
    assert result["titlePath"] == ["Atlas", "sit"]
    # 无 description 时诚实留空，不伪造业务说明
    runs2 = tmp_path / "runs2.jsonl"
    _write_runs(runs2, [{"run_id": "r2", "case": "TC2", "env": "sit", "status": "passed", "steps": []}])
    out2 = tmp_path / "allure-results2"
    emit_allure(runs2, out2)
    assert _read_results(out2)[0]["description"] == ""


def test_align_false_preserves_legacy_result_shape(tmp_path):
    """#115 零涟漪逃生舱：align=False 退回逐字旧 result.json（只 suite+severity / 无新键）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "TC1", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    emit_allure(runs, out, align=False)
    result = _read_results(out)[0]
    assert result["labels"] == [{"name": "suite", "value": "atlas"},
                                {"name": "severity", "value": "normal"}]
    assert "description" not in result and "titlePath" not in result


def test_step_level_png_attachment_renders_as_image(tmp_path):
    """UI AUTO TEST 的失败截图走 step 级 attachment（type=image/png + source 落盘）。
    Atlas 同链：step 记录里的附件引用 → 结果集图片附件条目 + 真实字节落盘。"""
    evidence = tmp_path / "artifacts" / "snapshots"
    evidence.mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\n real-capture-bytes"
    (evidence / "step3_FAIL.png").write_bytes(png)
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "TC_fail", "env": "sit", "status": "failed",
        "started_at": "2026-11-06T00:00:00+00:00", "finished_at": "2026-11-06T00:00:02+00:00",
        "steps": [{"step": "save", "op": "ui", "ok": False, "fail_class": "assertion",
                   "dur_ms": 120,
                   "attachment": {"path": "artifacts/snapshots/step3_FAIL.png",
                                  "label": "step3_FAIL", "type": "image/png"}}],
    }])
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out, attachments_dir=tmp_path)
    assert summary["attachments"] == 1
    step = _read_results(out)[0]["steps"][0]
    att = step["attachments"][0]
    assert att["type"] == "image/png"            # Allure 渲染成图片而非占位文本
    assert att["name"] == "step3_FAIL"
    copied = out / att["source"]
    assert copied.read_bytes() == png            # 真实字节（非「源缺失」占位）


def test_step_tree_has_name_status_and_timing(tmp_path):
    """步骤树保真：name/status/start/stop 齐全，且 start/stop 落在 run 区间内、按时长推进。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "TC", "env": "sit", "status": "passed",
        "started_at": "2026-11-06T00:00:00+00:00", "finished_at": "2026-11-06T00:00:10+00:00",
        "steps": [{"step": "open", "op": "ui", "ok": True, "dur_ms": 1000},
                  {"step": "save", "op": "ui", "ok": True, "dur_ms": 2500}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = _read_results(out)[0]
    s0, s1 = result["steps"]
    assert [s["name"] for s in (s0, s1)] == ["open", "save"]
    assert all(s["status"] == "passed" and s["stage"] == "finished" for s in (s0, s1))
    assert s0["start"] <= s0["stop"] and s1["start"] >= s0["stop"]   # 顺序推进、不重叠
    assert result["start"] <= s0["start"]

    def _ms(x):
        from datetime import datetime
        return int(datetime.fromisoformat(x).timestamp() * 1000)
    assert abs((s0["stop"] - s0["start"]) - 1000) <= 1
    assert result["start"] == _ms("2026-11-06T00:00:00+00:00")


def test_metadata_writes_categories_and_environment(tmp_path):
    """结果集组成部分补齐：categories.json（fail_class 分类）+ environment.properties
    （env 从各 run 去重派生）。UI AUTO TEST 的 results 目录两者皆缺、组件渲染为空。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [
        {"run_id": "r1", "case": "A", "env": "sit", "status": "passed", "steps": []},
        {"run_id": "r2", "case": "B", "env": "uat", "status": "passed", "steps": []},
    ])
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out)
    assert set(summary["metadata_files"]) == {"categories.json", "environment.properties"}
    cats = json.loads((out / "categories.json").read_text(encoding="utf-8"))
    assert isinstance(cats, list) and cats
    assert all("name" in c and "matchedStatuses" in c for c in cats)
    env = (out / "environment.properties").read_text(encoding="utf-8")
    assert "env=sit,uat" in env                  # 去重排序派生，确定性
    assert summary["out"] == str(out)


def test_default_categories_match_atlas_fail_class_messages(tmp_path):
    """分类的 messageRegex 锚在 Atlas 自产的 `run failed: <fail_class>`，命中正确桶；
    非缺陷终态（skipped）单列一桶，绝不与真实缺陷混桶（红线 #7）。"""
    import re
    cats = {c["name"]: c for c in default_categories()}
    def bucket(msg, status):
        for name, c in cats.items():
            if status not in c["matchedStatuses"]:
                continue
            rx = c.get("messageRegex")
            if rx is None or re.search(rx, msg):
                return name
        return None
    assert bucket("run failed: assertion", "failed") == "断言/契约缺陷"
    assert bucket("run failed: contract", "failed") == "断言/契约缺陷"
    assert bucket("run failed: data", "failed") == "数据缺陷"
    assert bucket("run failed: session", "failed") == "交互/会话缺陷"
    assert bucket("run failed: unknown", "broken") == "执行缺陷（未归类）"
    assert bucket("生产环境禁止建单", "skipped") == "环境阻断/中断（非缺陷）"


def test_categories_override_and_empty(tmp_path):
    """categories= 可覆盖默认；categories=[] 写空数组（不静默塞默认）。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "A", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    emit_allure(runs, out, categories=[{"name": "自定义", "matchedStatuses": ["failed"]}])
    assert json.loads((out / "categories.json").read_text(encoding="utf-8")) == \
        [{"name": "自定义", "matchedStatuses": ["failed"]}]
    emit_allure(runs, out, categories=[])
    assert json.loads((out / "categories.json").read_text(encoding="utf-8")) == []


def test_metadata_false_writes_no_extra_files(tmp_path):
    """#115 零涟漪逃生舱：metadata=False 只产 result.json，不写任何附加文件。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "A", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    summary = emit_allure(runs, out, metadata=False)
    assert summary["metadata_files"] == []
    assert not (out / "categories.json").exists()
    assert not (out / "environment.properties").exists()
    assert not (out / "executor.json").exists()


def test_executor_json_is_opt_in(tmp_path):
    """executor.json 无自然来源——不传就不写（不伪造宿主标识）；传了才落盘。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{"run_id": "r1", "case": "A", "env": "sit", "status": "passed", "steps": []}])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    assert not (out / "executor.json").exists()
    emit_allure(runs, out, executor={"name": "Atlas", "type": "atlas", "host": "ci-01"})
    execu = json.loads((out / "executor.json").read_text(encoding="utf-8"))
    assert execu["name"] == "Atlas" and execu["host"] == "ci-01"


def test_environment_properties_escapes_newlines(tmp_path):
    """properties 是行格式：值内换行折成空格，不把一条拆成两条读成假内容。"""
    out = tmp_path
    write_environment_properties(out, {"env": "sit", "note": "line1\nline2", "empty": None,
                                       "weird=key": "v"})
    text = (out / "environment.properties").read_text(encoding="utf-8")
    assert text.splitlines() == ["env=sit", "note=line1 line2", "weird_key=v"]


def test_write_categories_and_executor_standalone(tmp_path):
    """独立写函数可直接调用（不依赖 emit_allure），返回落盘路径。"""
    p1 = write_categories(tmp_path, [{"name": "x"}])
    p2 = write_executor(tmp_path, {"name": "Atlas"})
    assert p1.name == "categories.json" and p1.exists()
    assert p2.name == "executor.json" and json.loads(p2.read_text(encoding="utf-8"))["name"] == "Atlas"


"""B 轴 ae-1~ae-4：CLI 渲染层（cli_render）的锁步与人读视图验收。

锁步（ae-1）：默认档与 `--format json` 的机读输出 = 旧散写
`print(json.dumps(x, ensure_ascii=False, indent=2))` 逐字节等值——dump_json/dump_line
是序列化参数漂移的探测器（indent/ensure_ascii/键序变一格就红）。

人读（ae-2/ae-3）：`--format text` 出「汇总行/verdict + 分组 + 每组 hints」，
且 rc 与机读档逐命令相等（呈现层零判据）。

ae-4 rc2 文案：点名「改哪个 env 键/哪个文件」的文案核对。
"""
import json

import pytest

from atlas import cli_render
from atlas.cli import main


# ── 锁步：dump_json / dump_line 逐字节等值 ───────────────────────


def test_dump_json_byte_identical_to_legacy(capsys):
    payload = {"a": 1, "b": "中文", "c": [1, {"d": None}]}
    cli_render.dump_json(payload)
    out = capsys.readouterr().out
    assert out == json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def test_dump_line_byte_identical_to_legacy(capsys):
    payload = {"approved": "x", "task": "y"}
    cli_render.dump_line(payload)
    out = capsys.readouterr().out
    assert out == json.dumps(payload, ensure_ascii=False) + "\n"


def test_dump_json_stdout_default_and_no_noise(tmp_path, capsys):
    """默认档（无 --format）必须仍能被 json.loads 整读——存量消费方的契约。"""
    assert main(["--root", str(tmp_path), "tasks"]) == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)                       # 整读不炸 = 无夹带
    assert isinstance(parsed, list) and parsed


@pytest.mark.parametrize("argv,expect_rc", [
    (["doctor"], (0, 1)),
    (["tasks"], (0,)),
    (["contracts"], (0,)),
    (["selectors"], (0,)),
    (["library"], (0,)),
])
def test_format_json_flag_identical_to_default(tmp_path, capsys, argv, expect_rc):
    """`--format json` 与默认档逐字节等值（opt-in 参数面零行为差异）。"""
    rc = main(["--root", str(tmp_path), *argv])
    assert rc in expect_rc
    default_out = capsys.readouterr().out
    rc2 = main(["--root", str(tmp_path), *argv, "--format", "json"])
    assert rc2 in expect_rc
    json_out = capsys.readouterr().out
    assert default_out == json_out


# ── ae-2：doctor 人读视图 ────────────────────────────────────────


def test_doctor_text_has_verdict_and_groups(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "doctor", "--format", "text"]) == 1
    text = capsys.readouterr().out
    first = text.splitlines()[0]
    assert first.startswith("KNOWLEDGE=") and "asset-" in first and "boundary-" in first
    for group in ("[环境]", "[知识]", "[资产]", "[边界]", "[版本]"):
        assert group in text, f"缺分组 {group}"


def test_doctor_text_hints_name_env_keys(tmp_path, capsys):
    """每组末尾 hints 点名「改哪个 env 键/哪个文件」（ae-4 口径）。"""
    assert main(["--root", str(tmp_path), "doctor", "--format", "text"]) == 1
    text = capsys.readouterr().out
    assert "ATLAS_LEGACY_PROJECT" in text        # legacy-pages 缺失 hint
    assert "ATLAS_DEMO_API_PROJECT" in text      # demo-api 缺失 hint
    assert "DEMO_URL_SIT" in text                # base_url 未配置 hint
    assert "ATLAS_KNOWLEDGE_DIR" in text         # knowledge=builtin hint


def test_doctor_text_rc_matches_json_mode(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "doctor"]) == 1
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "doctor", "--format", "text"]) == 1


# ── ae-3：汇总行 + 分组列出 ─────────────────────────────────────


def test_tasks_text_summary_and_groups(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "tasks", "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("tasks: 共")
    # 分组键由实际任务派生（ui/auto 两通道，api 不在本 demo catalog——按实际断言）
    for ch in ("[ui]", "[auto]"):
        assert ch in text
    assert "hints:" in text


def test_contracts_text_summary_and_levels(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "contracts", "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("contracts: 共")
    assert "[core]" in text and "[minimal]" in text


def test_library_text_summary(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "library", "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("library:")


def test_selectors_text_summary(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "selectors", "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("selectors:")


def test_summary_text_summary_and_attention(tmp_path, capsys):
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({"run_id": "r1", "case": "c1", "status": "passed",
                                "steps": []}) + "\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "summary", str(runs), "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("summary: runs=1")
    assert "attention:" in text and "waiting=" in text


def test_review_stats_text(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "review", "--stats", "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("review: pending=0")


def test_schedule_status_text_and_rc(tmp_path, capsys, monkeypatch):
    """schedule status 人读视图 + rc 与机读档相等（呈现层不动判据）。"""
    (tmp_path / ".atlas-root").write_text("", encoding="utf-8")
    from atlas.scheduling import state as st
    doc = st.load_doc(st.tasks_path(tmp_path))
    doc["tasks"]["patrol"] = {"id": "patrol", "flow": "f.yaml", "run_args": [],
                              "schedule": {"kind": "cron", "expr": "0 * * * *", "tz": "UTC"},
                              "max_duration_min": 30, "enabled": True,
                              "writes_acknowledged": False, "created_at": "2026-11-01T00:00:00+00:00",
                              "updated_at": "2026-11-01T00:00:00+00:00",
                              "last_scheduled_for": None, "consecutive_failures": 0,
                              "auto_paused_reason": None}
    st.save_doc(st.tasks_path(tmp_path), doc)
    assert main(["--root", str(tmp_path), "schedule", "status"]) == 0
    json_rc = json.loads(capsys.readouterr().out)
    assert isinstance(json_rc, list) and json_rc[0]["id"] == "patrol"
    assert main(["--root", str(tmp_path), "schedule", "status", "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert "schedule status: 共 1 个任务" in text and "patrol" in text

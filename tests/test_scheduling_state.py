"""C1 台账/schema/入库判据（design_c1 §5 + §3 表 8/9 + X1/X5/X9 纪律）。

`decide_slot` 只看台账里的数，本文件钉的是「数怎么进台账、怎么被改、怎么被写坏后仍读得回来」。
"""
import inspect
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas.core import FunctionalTask, TaskMetadata
from atlas.dsl import TaskRegistry
from atlas.scheduling import state as st
from atlas.scheduling.constants import SCHEMA_VERSION

UTC = timezone.utc
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "atlas"
    (base / "flows").mkdir(parents=True)
    (base / "flows" / "patrol.yaml").write_text(
        "id: patrol\nsteps:\n  - task: ro_step\n", encoding="utf-8")
    return base


def _registry():
    registry = TaskRegistry()
    ro = FunctionalTask(TaskMetadata(domain="test", intent="只读查询", read_only=True),
                        action=lambda actor: None)
    write = FunctionalTask(TaskMetadata(domain="test", intent="保存单据", read_only=False),
                          action=lambda actor: None)
    registry.register("ro_step", lambda **kwargs: ro)
    registry.register("write_step", lambda **kwargs: write)
    return registry


def _cron_schedule(expr="*/30 1-8 * * *", tz="Europe/Amsterdam"):
    return {"kind": "cron", "expr": expr, "tz": tz}


# ── 1. 建表与往返 ────────────────────────────────────────────────────

def test_build_task_writes_full_schema(root):
    task = st.build_task(task_id="ics-patrol-eu", flow="flows/patrol.yaml",
                         schedule=_cron_schedule(), root=root,
                         run_args=["--account", "ops"], now=NOW)
    assert task["id"] == "ics-patrol-eu"
    assert task["enabled"] is True and task["writes_acknowledged"] is False
    assert task["last_scheduled_for"] is None and task["consecutive_failures"] == 0
    assert task["auto_paused_reason"] is None
    assert task["created_at"] == task["updated_at"] == NOW.isoformat()
    assert task["max_duration_min"] == 30


def test_save_load_roundtrip_is_stable(root):
    path = st.tasks_path(root)
    task = st.build_task(task_id="a1", flow="flows/patrol.yaml",
                         schedule={"kind": "manual"}, root=root, now=NOW)
    doc = {"schema_version": SCHEMA_VERSION, "tasks": {"a1": task}}
    st.save_doc(path, doc)
    assert st.load_doc(path) == doc


def test_load_missing_file_is_empty_ledger(root):
    assert st.load_doc(st.tasks_path(root)) == {"schema_version": SCHEMA_VERSION, "tasks": {}}


def test_load_corrupt_json_names_the_file(root):
    path = st.tasks_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{坏掉的 JSON", encoding="utf-8")
    with pytest.raises(ValueError, match="tasks.json"):
        st.load_doc(path)


def test_load_non_mapping_is_named(root):
    path = st.tasks_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="必须是映射"):
        st.load_doc(path)


def test_wrong_schema_version_is_refused_not_guessed(root):
    path = st.tasks_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 99, "tasks": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        st.load_doc(path)


def test_get_task_unknown_id_points_at_list(root):
    path = st.tasks_path(root)
    with pytest.raises(ValueError, match="schedule list"):
        st.get_task({"tasks": {}}, "nope", source=path)


# ── 2. 字段级校验 ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_id", ["", "a b", "a/b", "x" * 70, "-lead", "中文"])
def test_bad_ids_rejected(bad_id, root):
    with pytest.raises(ValueError, match="任务 id"):
        st.build_task(task_id=bad_id, flow="flows/patrol.yaml",
                      schedule={"kind": "manual"}, root=root, now=NOW)


def test_id_shape_accepts_real_examples(root):
    for good in ("ics-patrol-eu", "a1", "TWO.Up_1"):
        task = st.build_task(task_id=good, flow="flows/patrol.yaml",
                             schedule={"kind": "manual"}, root=root, now=NOW)
        assert task["id"] == good


def test_flow_stored_as_posix_relative_string(root):
    """X1：台账里存 POSIX 相对串——反斜杠进 JSON 就等于同一份台账两台机两种读法。"""
    task = st.build_task(task_id="a1", flow=str(root / "flows" / "patrol.yaml"),
                         schedule={"kind": "manual"}, root=root, now=NOW)
    assert task["flow"] == "flows/patrol.yaml"
    assert "\\" not in task["flow"]
    assert st.resolve_flow(root, task["flow"]) == (root / "flows" / "patrol.yaml")


def test_flow_outside_root_stays_absolute_posix(tmp_path):
    outside = (tmp_path / "elsewhere" / "f.yaml")
    outside.parent.mkdir(parents=True)
    outside.write_text("id: f\nsteps: []\n", encoding="utf-8")
    stored = st.flow_for_ledger(tmp_path / "root", outside)
    assert Path(stored).is_absolute() and "\\" not in stored


def test_max_duration_must_be_positive_int(root):
    for bad in (0, -1, "30", True, 1.5):
        with pytest.raises(ValueError, match="max_duration_min"):
            st.validate_max_duration(bad)
    assert st.validate_max_duration(15) == 15
    assert st.validate_max_duration(None) == 30


# ── 3. schedule 校验（X6 文案 + 最小间隔）────────────────────────────

def test_cron_requires_tz():
    with pytest.raises(ValueError, match="schedule.tz"):
        st.validate_schedule({"kind": "cron", "expr": "0 * * * *"})


def test_bad_tz_names_tzdata():
    with pytest.raises(ValueError, match="tzdata"):
        st.validate_schedule({"kind": "cron", "expr": "0 * * * *", "tz": "Mars/Phobos"})


def test_sub_5_minute_expression_rejected_at_admission():
    with pytest.raises(ValueError, match="5 分钟"):
        st.validate_schedule({"kind": "cron", "expr": "* * * * *", "tz": "UTC"})


def test_unknown_kind_and_shape_rejected():
    with pytest.raises(ValueError, match="kind"):
        st.validate_schedule({"kind": "weekly", "expr": "0 * * * *", "tz": "UTC"})
    with pytest.raises(ValueError, match="必须是映射"):
        st.validate_schedule("0 * * * *")


def test_once_at_accepts_z_and_normalizes_to_offset():
    spec = st.validate_schedule({"kind": "once", "at": "2026-11-05T09:00:00Z"})
    assert spec["at"].endswith("+00:00")
    parsed = datetime.fromisoformat(spec["at"])
    assert parsed == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


def test_once_naive_at_requires_tz():
    with pytest.raises(ValueError, match="schedule.tz"):
        st.validate_schedule({"kind": "once", "at": "2026-11-05T09:00:00"})
    spec = st.validate_schedule({"kind": "once", "at": "2026-11-05T09:00:00", "tz": "UTC"})
    assert spec["at"].endswith("+00:00")


def test_once_bad_at_named():
    with pytest.raises(ValueError, match="schedule.at"):
        st.validate_schedule({"kind": "once", "at": "明天早上"})
    with pytest.raises(ValueError, match="schedule.at"):
        st.validate_schedule({"kind": "once"})


def test_manual_needs_no_fields():
    assert st.validate_schedule({"kind": "manual"}) == {"kind": "manual"}


# ── 4. 红线 #13 的 argv 闸（C1-C：显式 raise，不依赖 assert）─────────

@pytest.mark.parametrize("bad, fragment", [
    ("--approve", "永不允许 --approve"),
    ("--approve=1", "--approve"),
    ("--mode", "不带 --mode"),
    ("--mode=debug", "--mode"),
])
def test_guard_rejects_approve_and_mode(bad, fragment):
    with pytest.raises(ValueError) as excinfo:
        st.guard_run_args(["--account", "ops", bad])
    assert fragment in str(excinfo.value)


def test_guard_passes_ordinary_run_args_through_validation():
    assert st.validate_run_args(["--account", "ops", "--ou", "EU"]) == \
        ["--account", "ops", "--ou", "EU"]
    assert st.validate_run_args(None) == []
    with pytest.raises(ValueError, match="字符串列表"):
        st.validate_run_args("--account ops")


def test_approve_gate_raises_instead_of_asserting():
    """C1-C：`python -O` 会剥掉 assert——安全闸不许用它，这里直接钉源码形态。"""
    source = inspect.getsource(st.guard_run_args)
    assert "raise ValueError" in source
    assert "assert " not in source


def test_build_task_rejects_approve_in_run_args(root):
    with pytest.raises(ValueError, match="--approve"):
        st.build_task(task_id="a1", flow="flows/patrol.yaml", schedule={"kind": "manual"},
                      root=root, run_args=["--approve"], now=NOW)


# ── 5. 表 8：apply_patch 的水位线重置规则 ────────────────────────────

def _seed_task(root, **overrides):
    task = st.build_task(task_id="a1", flow="flows/patrol.yaml",
                         schedule=_cron_schedule(), root=root, now=NOW)
    task.update({"last_scheduled_for": (NOW - timedelta(hours=3)).isoformat(),
                 "consecutive_failures": 4,
                 "auto_paused_reason": "rc=1"})
    task.update(overrides)
    return task


def test_changing_cron_resets_watermark_failures_and_reason(root):
    task = _seed_task(root)
    updated = st.apply_patch(task, {"schedule": _cron_schedule(expr="0 * * * *")},
                             root=root, now=NOW + timedelta(minutes=5))
    assert updated["last_scheduled_for"] == (NOW + timedelta(minutes=5)).isoformat()
    assert updated["consecutive_failures"] == 0
    assert updated["auto_paused_reason"] is None


def test_changing_flow_or_run_args_resets(root):
    task = _seed_task(root)
    changed_flow = st.apply_patch(task, {"flow": "flows/other.yaml"}, root=root, now=NOW)
    assert changed_flow["last_scheduled_for"] == NOW.isoformat()
    task2 = _seed_task(root)
    changed_args = st.apply_patch(task2, {"run_args": ["--account", "ops2"]}, root=root, now=NOW)
    assert changed_args["last_scheduled_for"] == NOW.isoformat()


def test_changing_tz_alone_resets(root):
    task = _seed_task(root)
    updated = st.apply_patch(task, {"schedule": _cron_schedule(tz="Europe/Berlin")},
                             root=root, now=NOW)
    assert updated["last_scheduled_for"] == NOW.isoformat()


def test_re_enable_resets_but_disable_does_not(root):
    task = _seed_task(root, enabled=False)
    enabled = st.apply_patch(task, {"enabled": True}, root=root, now=NOW)
    assert enabled["consecutive_failures"] == 0 and enabled["enabled"] is True
    assert enabled["last_scheduled_for"] == NOW.isoformat()
    disabled = st.apply_patch(_seed_task(root), {"enabled": False}, root=root, now=NOW)
    assert disabled["enabled"] is False
    assert disabled["last_scheduled_for"] == (NOW - timedelta(hours=3)).isoformat()   # 保留水位线
    assert disabled["consecutive_failures"] == 4


def test_unchanged_schedule_does_not_reset(root):
    """同一份配置原样再提交不算「改了配置」——否则每次 edit 都把欠账清零，口径反了。"""
    task = _seed_task(root)
    same = st.apply_patch(task, {"schedule": _cron_schedule()}, root=root, now=NOW)
    assert same["last_scheduled_for"] == task["last_scheduled_for"]
    assert same["consecutive_failures"] == 4


def test_max_duration_change_does_not_reset_watermark(root):
    task = _seed_task(root)
    updated = st.apply_patch(task, {"max_duration_min": 15}, root=root, now=NOW)
    assert updated["max_duration_min"] == 15
    assert updated["last_scheduled_for"] == task["last_scheduled_for"]


def test_once_schedule_edit_re_enables(root):
    """暂停于跑完的 once（不是故障的）：改了 at 就自动回 active。"""
    task = st.build_task(task_id="once1", flow="flows/patrol.yaml",
                         schedule={"kind": "once", "at": "2026-11-05T09:00:00Z"},
                         root=root, now=NOW)
    task["enabled"] = False
    updated = st.apply_patch(task, {"schedule": {"kind": "once", "at": "2026-11-06T09:00:00Z"}},
                             root=root, now=NOW)
    assert updated["enabled"] is True
    assert updated["consecutive_failures"] == 0


# ── 6. CAS / 认领 / 连败 ─────────────────────────────────────────────

def test_claim_slot_matches_and_mismatches(root):
    path = st.tasks_path(root)
    task = _seed_task(root)
    st.save_doc(path, {"schema_version": SCHEMA_VERSION, "tasks": {"a1": task}})
    old = task["last_scheduled_for"]
    assert st.claim_slot(path, "a1", old, NOW.isoformat()) is True
    assert st.load_doc(path)["tasks"]["a1"]["last_scheduled_for"] == NOW.isoformat()
    # 第二次拿同一个 expected 再认：已被前一次改写 → 输掉，值不变
    assert st.claim_slot(path, "a1", old, "2027-01-01T00:00:00+00:00") is False
    assert st.load_doc(path)["tasks"]["a1"]["last_scheduled_for"] == NOW.isoformat()


def test_claim_once_done_disables(root):
    path = st.tasks_path(root)
    task = st.build_task(task_id="once1", flow="flows/patrol.yaml",
                         schedule={"kind": "once", "at": "2026-11-05T09:00:00Z"},
                         root=root, now=NOW)
    st.save_doc(path, {"schema_version": SCHEMA_VERSION, "tasks": {"once1": task}})
    st.claim_once_done(path, "once1")
    assert st.load_doc(path)["tasks"]["once1"]["enabled"] is False


def test_failure_count_fifth_pause_with_reason(root):
    path = st.tasks_path(root)
    task = _seed_task(root, consecutive_failures=0, last_scheduled_for=None,
                      auto_paused_reason=None)
    st.save_doc(path, {"schema_version": SCHEMA_VERSION, "tasks": {"a1": task}})
    for index in range(1, 5):
        updated = st.apply_failure_policy(path, "a1", "count", reason=f"rc=1 #{index}")
        assert updated["consecutive_failures"] == index
        assert updated["enabled"] is True and updated["auto_paused_reason"] is None
    fifth = st.apply_failure_policy(path, "a1", "count", reason="rc=2 环境挂了一整晚")
    assert fifth["consecutive_failures"] == 5
    assert fifth["enabled"] is False
    assert "rc=2" in fifth["auto_paused_reason"]          # 表 9：留最后一条失败摘要


def test_failure_policy_reset_keep_and_unknown(root):
    path = st.tasks_path(root)
    task = _seed_task(root, consecutive_failures=3)
    st.save_doc(path, {"schema_version": SCHEMA_VERSION, "tasks": {"a1": task}})
    assert st.apply_failure_policy(path, "a1", "reset")["consecutive_failures"] == 0
    st.apply_failure_policy(path, "a1", "count", reason="x")
    st.apply_failure_policy(path, "a1", "count", reason="x")
    st.apply_failure_policy(path, "a1", "count", reason="x")
    kept = st.apply_failure_policy(path, "a1", "keep")
    assert kept["consecutive_failures"] == 3          # interrupted/defer 不动连败
    with pytest.raises(ValueError, match="未知连败策略"):
        st.apply_failure_policy(path, "a1", "explode")


def test_auto_pause_reason_is_bounded(root):
    path = st.tasks_path(root)
    task = _seed_task(root, consecutive_failures=4)
    st.save_doc(path, {"schema_version": SCHEMA_VERSION, "tasks": {"a1": task}})
    updated = st.apply_failure_policy(path, "a1", "count", reason="长" * 500)
    assert len(updated["auto_paused_reason"]) <= 200


# ── 7. 调度索引（X5 追加纪律 + 读侧容错）─────────────────────────────

def test_append_run_breaks_truncated_last_line(root):
    path = st.runs_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"task_id": "a", "skipped_slots": 9')      # 中断腰斩的残行
    st.append_run(path, {"task_id": "a", "skipped_slots": 1, "outcome": "dropped"})
    rows = st.read_runs(path)
    assert rows == [{"task_id": "a", "skipped_slots": 1, "outcome": "dropped"}]
    assert len(path.read_bytes().splitlines()) == 2               # 残行没把新记录黏住


def test_read_runs_survives_bad_bytes_and_non_mapping(root):
    """#112/#131 同防线：一条坏字节不许顶掉整表（否则 status 只会报「读不出」）。"""
    path = st.runs_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"task_id": "a", "skipped_slots": 2})
    truncated = b'{"task_id": "b", "note": "\xff\xfe'      # 腰斩在多字节边界上（JSON 必败）
    path.write_bytes(good.encode() + b"\n" + truncated + b"\n"
                     + b"[1, 2]\n" + b"null\n" + good.encode() + b"\n")
    rows = st.read_runs(path)
    assert [row["task_id"] for row in rows] == ["a", "a"]


def test_skipped_totals_groups_by_task(root):
    path = st.runs_path(root)
    st.append_run(path, {"task_id": "a", "skipped_slots": 3})
    st.append_run(path, {"task_id": "a", "skipped_slots": 2})
    st.append_run(path, {"task_id": "b", "skipped_slots": 5})
    st.append_run(path, {"task_id": "b"})                       # 形态缺字段=不计入，不冒充 0
    st.append_run(path, {"task_id": "c", "skipped_slots": "很多"})
    assert st.skipped_totals(path) == {"a": 5, "b": 5}


def test_skipped_totals_on_missing_file_is_empty(root):
    assert st.skipped_totals(st.runs_path(root)) == {}


# ── 8. X9：原子改写 ──────────────────────────────────────────────────

def test_save_doc_retries_once_on_permission_error(root, monkeypatch):
    path = st.tasks_path(root)
    real_replace = os.replace
    attempts = []

    def flaky(src, dst):
        attempts.append(dst)
        if len(attempts) == 1:
            raise PermissionError("目标被打开")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    st.save_doc(path, {"schema_version": SCHEMA_VERSION, "tasks": {}})
    assert len(attempts) == 2 and path.exists()


def test_save_doc_gives_up_naming_instead_of_half_writing(root, monkeypatch):
    path = st.tasks_path(root)

    def always_locked(src, dst):
        raise PermissionError("一直被占")

    monkeypatch.setattr(os, "replace", always_locked)
    monkeypatch.setattr(st.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError):
        st.save_doc(path, {"schema_version": SCHEMA_VERSION, "tasks": {}})
    assert not path.exists()                                     # 没有半写
    assert not list(path.parent.glob("*.tmp"))                   # tmp 也收干净了


# ── 9. 入库闸（§6.2 只读护栏）────────────────────────────────────────

def test_admission_scan_reports_write_steps_only(root):
    (root / "flows" / "mixed.yaml").write_text(
        "id: mixed\nsteps:\n  - task: ro_step\n  - task: write_step\n", encoding="utf-8")
    task = st.build_task(task_id="m", flow="flows/mixed.yaml", schedule={"kind": "manual"},
                         root=root, now=NOW)
    problems = st.admission_problems(task, root=root, registry=_registry())
    assert problems["flow_ok"] is True
    assert problems["write_steps"] == ["FunctionalTask"]      # 只报含写的 Task


def test_admission_pure_read_flow_has_no_write_steps(root):
    task = st.build_task(task_id="r", flow="flows/patrol.yaml", schedule={"kind": "manual"},
                         root=root, now=NOW)
    assert st.admission_problems(task, root=root, registry=_registry())["write_steps"] == []


def test_admission_missing_flow_is_named(root):
    task = {"flow": "flows/ghost.yaml"}
    with pytest.raises(ValueError, match="不存在"):
        st.admission_problems(task, root=root, registry=_registry())


def test_admission_broken_flow_surfaces_structural_error(root):
    (root / "flows" / "broken.yaml").write_text("id: b\nsteps:\n  - task: unregistered\n",
                                                encoding="utf-8")
    task = {"flow": "flows/broken.yaml"}
    with pytest.raises(ValueError, match="unregistered"):
        st.admission_problems(task, root=root, registry=_registry())


# ── 10. ISO 序列化 ───────────────────────────────────────────────────

def test_iso_and_parse_iso_roundtrip():
    assert st.iso(NOW) == NOW.isoformat()
    assert st.iso(None) is None
    assert st.parse_iso(None, "x") is None
    assert st.parse_iso("", "x") is None
    assert st.parse_iso("2026-11-05T09:00:00Z", "x") == datetime(2026, 11, 5, 9, tzinfo=UTC)
    with pytest.raises(ValueError, match="不是合法 ISO"):
        st.parse_iso("昨天", "水位线")
    with pytest.raises(ValueError, match="必须是 ISO"):
        st.parse_iso(12345, "水位线")


def test_schedule_dir_paths_are_under_reports(root):
    """状态文件全在 reports/schedule/（gitignore 内）——真实域名/账号名天然不入库。"""
    assert st.schedule_dir(root) == root / "reports" / "schedule"
    assert st.tasks_path(root).is_relative_to(root / "reports")
    assert st.runs_path(root).name == "schedule_runs.jsonl"
    assert st.lease_path(root, "a1").parent == st.lease_dir(root)


# ── cron env 缺口观测（判据复用 versioning，不新建第二份可用性判定）────

def test_current_knowledge_source_builtin_without_env(monkeypatch):
    """无 ATLAS_KNOWLEDGE_DIR：观测=builtin（无人值守会跑包内虚构知识，add 当场据此 WARN）。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    assert st.current_knowledge_source() == "builtin"


def test_current_knowledge_source_private_with_env(monkeypatch, tmp_path):
    """私有库目录已配（含 *.json）：观测=private。判据走 versioning，这里只验词汇表。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "pitfalls.json").write_text('{"pitfalls": []}', encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(kb))
    assert st.current_knowledge_source() == "private"


def test_build_task_observations_knowledge_source(root, monkeypatch):
    """build_task 把观测写进入账字段（人在 add 那一刻在场，值取当刻 env）。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    task = st.build_task(task_id="a1", flow="flows/patrol.yaml", schedule={"kind": "manual"},
                         root=root, now=NOW)
    assert task["knowledge_source"] == "builtin"


def test_apply_patch_refreshes_knowledge_source(root, monkeypatch, tmp_path):
    """edit（人在场）刷新观测：先 builtin，配上私有库后 edit → private；观测刷新不动水位线。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    task = st.build_task(task_id="a1", flow="flows/patrol.yaml",
                         schedule=_cron_schedule(), root=root, now=NOW)
    assert task["knowledge_source"] == "builtin"
    kb = tmp_path / "kb2"
    kb.mkdir()
    (kb / "pitfalls.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(kb))
    updated = st.apply_patch(task, {"max_duration_min": 20}, root=root, now=NOW)
    assert updated["knowledge_source"] == "private"
    assert updated["last_scheduled_for"] == task["last_scheduled_for"]  # 非配置重置键，不动水位线

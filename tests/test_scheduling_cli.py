"""C1 dispatch/CLI（design_c1 §6/§7/§8）。

- 派发全终态用 fake subprocess 钉死（离线，不真起进程、不发信号）；
- argv 永不出现 approve、子 env 必带 PYTHONPATH/PYTHONIOENCODING（C1-C/E、X2/X5/X7）；
- 心跳线程起停无泄漏；租约占用=推迟不推进水位线；接管留 reclaim 记录；
- CLI rc 口径 2/0/1；crontab 双平台文案按评审 C1-A 裁决钉（Linux=expr 逐字，Windows=/MO 最小间隔）。
"""
import contextlib
import io
import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas import cli
from atlas.cli import main
from atlas.core import FunctionalTask, TaskMetadata
from atlas.dsl import TaskRegistry
from atlas.scheduling import dispatch, lease
from atlas.scheduling import state as st

UTC = timezone.utc
WRITE_FLOW_YAML = "id: mixed\nsteps:\n  - task: write_step\n"
READ_FLOW_YAML = "id: patrol\nsteps:\n  - task: ro_step\n"
THREE_THIRTY = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)   # 每小时表达式下 due=03:00，age=30min


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "atlas"
    base.mkdir()
    (base / ".atlas-root").write_text("", encoding="utf-8")
    (base / "src").mkdir()                       # C1-E：注入 PYTHONPATH 的前提
    flows = base / "flows"
    flows.mkdir()
    (flows / "patrol.yaml").write_text(READ_FLOW_YAML, encoding="utf-8")
    (flows / "mixed.yaml").write_text(WRITE_FLOW_YAML, encoding="utf-8")
    return base


@pytest.fixture
def registry():
    read = FunctionalTask(TaskMetadata(domain="test", intent="只读", read_only=True),
                          action=lambda actor: None)
    write = FunctionalTask(TaskMetadata(domain="test", intent="保存", read_only=False),
                          action=lambda actor: None)
    local = TaskRegistry()
    local.register("ro_step", lambda **kwargs: read)
    local.register("write_step", lambda **kwargs: write)
    return local


def _cfg(root):
    """dispatch.tick 只要 root；不做真 AtlasConfig 装配（离线测不碰 .env/plugin）。"""
    return SimpleNamespace(root=root)


def _seed(root, *, task_id="patrol", flow="flows/patrol.yaml", schedule=None,
          created_at=None, watermark=None, failures=0, enabled=True,
          run_args=None, max_duration_min=30, writes_acknowledged=False,
          auto_paused_reason=None):
    """直接写台账（时间可控），不走 CLI add。"""
    schedule = schedule or {"kind": "cron", "expr": "0 * * * *", "tz": "UTC"}
    created_at = created_at or datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    task = {
        "id": task_id, "flow": flow, "run_args": list(run_args or []),
        "schedule": schedule, "max_duration_min": max_duration_min,
        "enabled": enabled, "writes_acknowledged": writes_acknowledged,
        "created_at": created_at.isoformat(), "updated_at": created_at.isoformat(),
        "last_scheduled_for": watermark.isoformat() if watermark else None,
        "consecutive_failures": failures, "auto_paused_reason": auto_paused_reason,
    }
    path = st.tasks_path(root)
    doc = st.load_doc(path)
    doc["tasks"][task_id] = task
    st.save_doc(path, doc)
    return task


class FakeSpawn:
    """记录 argv/cwd/timeout/env 并返回/抛出指定终态。"""

    def __init__(self, returncode=0, raises=None, delay=0.0):
        self.returncode = returncode
        self.raises = raises
        self.delay = delay
        self.calls = []

    def __call__(self, command, *, cwd, timeout, env):
        if self.delay:
            import time
            time.sleep(self.delay)
        self.calls.append({"command": list(command), "cwd": str(cwd),
                           "timeout": timeout, "env": dict(env)})
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(returncode=self.returncode)


@pytest.fixture
def spawn(monkeypatch):
    installed = []

    def install(**kwargs):
        fake = FakeSpawn(**kwargs)
        installed.append(fake)
        monkeypatch.setattr(dispatch, "_spawn", fake)
        return fake

    install.last = lambda: installed[-1] if installed else FakeSpawn()
    return install


def _runs(root):
    return st.read_runs(st.runs_path(root))


def _cli(root, *argv):
    return main(["--root", str(root), "schedule", *argv])


def _capture(root, *argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        main(["--root", str(root), "schedule", *argv])
    return buffer.getvalue()


# ── add / list / status / enable / disable / edit / remove ───────────

def test_add_writes_ledger_and_previews_next(root, registry, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "ics-patrol-eu", "--flow", "flows/patrol.yaml",
                "--cron", "*/30 1-8 * * *", "--tz", "Europe/Amsterdam") == 0
    task = st.load_doc(st.tasks_path(root))["tasks"]["ics-patrol-eu"]
    assert task["schedule"] == {"kind": "cron", "expr": "*/30 1-8 * * *",
                                "tz": "Europe/Amsterdam"}
    assert task["flow"] == "flows/patrol.yaml"
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_slot"].startswith("20")
    assert payload["wiring"] == "atlas schedule crontab ics-patrol-eu"


def test_add_once_and_manual_shapes(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "m1", "--flow", "flows/patrol.yaml", "--manual") == 0
    assert _cli(root, "add", "o1", "--flow", "flows/patrol.yaml",
                "--at", "2026-12-01T09:00:00Z") == 0
    doc = st.load_doc(st.tasks_path(root))["tasks"]
    assert doc["m1"]["schedule"]["kind"] == "manual"
    assert doc["o1"]["schedule"]["at"].endswith("+00:00")


def test_add_two_schedule_kinds_is_user_error(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "x", "--flow", "flows/patrol.yaml", "--manual",
                "--at", "2026-12-01T09:00:00Z") == 2


def test_add_no_schedule_kind_is_user_error(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "x", "--flow", "flows/patrol.yaml") == 2


def test_add_duplicate_id_is_user_error(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "a1", "--flow", "flows/patrol.yaml", "--manual") == 0
    assert _cli(root, "add", "a1", "--flow", "flows/patrol.yaml", "--manual") == 2


def test_add_missing_flow_is_user_error(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "a1", "--flow", "flows/ghost.yaml", "--manual") == 2


def test_add_broken_flow_structure_is_user_error(root, registry, monkeypatch):
    """入库前先过 validate_flow（与 atlas compile 同源），半成品不落账。"""
    (root / "flows" / "bad.yaml").write_text("id: bad\nsteps:\n  - task: unregistered\n",
                                             encoding="utf-8")
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "a1", "--flow", "flows/bad.yaml", "--manual") == 2
    assert "a1" not in st.load_doc(st.tasks_path(root))["tasks"]


def test_add_bad_tz_names_tzdata(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "a1", "--flow", "flows/patrol.yaml",
                "--cron", "0 * * * *", "--tz", "Mars/Phobos") == 2


def test_add_rejects_sub_5_minute_cron(root, registry, monkeypatch):
    """最小触发间隔防手滑：`* * * * *` 在无人值守下就是烧配额。"""
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "a1", "--flow", "flows/patrol.yaml",
                "--cron", "* * * * *", "--tz", "UTC") == 2


def test_add_rejects_approve_as_run_arg_value(root, registry, monkeypatch):
    """`add --run-arg --approve` 当场拒（红线 #13 的 CLI 面）。"""
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "a1", "--flow", "flows/patrol.yaml", "--cron", "0 * * * *",
                "--tz", "UTC", "--run-arg=--approve") == 2


def test_add_rejects_mode_as_run_arg(root, registry, monkeypatch):
    """`--mode` 同闸：debug/heal 带 CDP 冻场/自愈语义，不属于无人值守。"""
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "a1", "--flow", "flows/patrol.yaml", "--cron", "0 * * * *",
                "--tz", "UTC", "--run-arg=--mode", "--run-arg=debug") == 2


def test_add_write_flow_requires_ack(root, registry, monkeypatch, capsys):
    """§6.2 产出侧强制确认（M14）：含写步骤且没人的确认 → 拒绝入库。"""
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "w1", "--flow", "flows/mixed.yaml", "--manual") == 2
    assert "写步骤" in capsys.readouterr().err
    assert "w1" not in st.load_doc(st.tasks_path(root))["tasks"]     # 半成品不落账


def test_add_write_flow_with_ack_records_decision(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "w1", "--flow", "flows/mixed.yaml", "--manual",
                "--i-accept-writes") == 0
    assert st.load_doc(st.tasks_path(root))["tasks"]["w1"]["writes_acknowledged"] is True


def test_list_empty_ledger_is_zero_and_hint(root, capsys):
    assert _cli(root, "list") == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == [] and "schedule add" in out.err


def test_list_shows_seeded_tasks(root):
    _seed(root, task_id="a1")
    _seed(root, task_id="b1", schedule={"kind": "manual"}, enabled=False)
    assert _cli(root, "list") == 0
    rows = json.loads(_capture(root, "list"))
    assert [row["id"] for row in rows] == ["a1", "b1"]
    assert rows[1]["kind"] == "manual" and rows[1]["enabled"] is False


def test_status_totals_missed_rounds_per_task(root):
    """G2 交付答案：`status` 的 skipped_total = 该任务所有调度记录 skipped_slots 之和。"""
    _seed(root, task_id="a1")
    runs = st.runs_path(root)
    st.append_run(runs, {"task_id": "a1", "skipped_slots": 3, "outcome": "failed"})
    st.append_run(runs, {"task_id": "a1", "skipped_slots": 2, "outcome": "dropped"})
    st.append_run(runs, {"task_id": "b1", "skipped_slots": 7, "outcome": "dropped"})
    entry = {row["id"]: row for row in json.loads(_capture(root, "status"))}["a1"]
    assert entry["skipped_total"] == 5
    assert entry["outcomes"] == {"failed": 1, "dropped": 1}
    assert entry["next_slot"].startswith("20")


def test_status_shows_auto_paused_with_reason(root, capsys):
    _seed(root, task_id="a1", failures=5, enabled=False, auto_paused_reason="rc=1")
    assert _cli(root, "status", "a1") == 0
    err = capsys.readouterr().err
    assert "AUTO-PAUSED: a1" in err and "rc=1" in err
    assert json.loads(_capture(root, "status", "a1"))[0]["auto_paused_reason"] == "rc=1"


def test_status_unknown_id_is_user_error(root):
    _seed(root)
    assert _cli(root, "status", "ghost") == 2


def test_enable_resets_and_disable_keeps_watermark(root):
    moment = datetime(2026, 11, 5, 6, 0, tzinfo=UTC)
    _seed(root, task_id="a1", failures=5, enabled=False, watermark=moment)
    assert _cli(root, "enable", "a1") == 0
    task = st.load_doc(st.tasks_path(root))["tasks"]["a1"]
    assert task["enabled"] is True and task["consecutive_failures"] == 0
    assert task["auto_paused_reason"] is None
    enabled_watermark = task["last_scheduled_for"]      # enable 重置到新时刻（表 8）
    assert _cli(root, "disable", "a1") == 0
    after = st.load_doc(st.tasks_path(root))["tasks"]["a1"]
    assert after["enabled"] is False
    assert after["last_scheduled_for"] == enabled_watermark   # 停用不动水位线
    assert datetime.fromisoformat(after["last_scheduled_for"]) > moment


def test_disable_then_enable_is_the_reset_pair(root):
    """停用（保留欠账）→ 启用（重置欠账）：人显式重启等于给了新起点（表 8）。"""
    _seed(root, task_id="a1", watermark=datetime(2026, 1, 1, tzinfo=UTC), failures=2)
    assert _cli(root, "disable", "a1") == 0
    assert _cli(root, "enable", "a1") == 0
    task = st.load_doc(st.tasks_path(root))["tasks"]["a1"]
    assert task["consecutive_failures"] == 0
    assert datetime.fromisoformat(task["last_scheduled_for"]) > datetime(2026, 1, 1, tzinfo=UTC)


def test_remove_drops_ledger_keeps_history(root):
    _seed(root, task_id="a1")
    st.append_run(st.runs_path(root), {"task_id": "a1", "outcome": "succeeded", "skipped_slots": 0})
    assert _cli(root, "remove", "a1") == 0
    assert "a1" not in st.load_doc(st.tasks_path(root))["tasks"]
    assert len(st.read_runs(st.runs_path(root))) == 1                 # 历史不跟着删
    assert _cli(root, "remove", "a1") == 2


def test_edit_cron_resets_watermark(root, registry, monkeypatch):
    """表 8：人改配置的瞬间不回放历史。"""
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    old = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    _seed(root, task_id="a1", watermark=old, failures=3)
    assert _cli(root, "edit", "a1", "--cron", "30 * * * *", "--tz", "UTC") == 0
    task = st.load_doc(st.tasks_path(root))["tasks"]["a1"]
    assert task["schedule"]["expr"] == "30 * * * *"
    assert task["consecutive_failures"] == 0
    assert datetime.fromisoformat(task["last_scheduled_for"]) > old


def test_edit_run_args_whole_group_replace(root, registry, monkeypatch):
    """`--run-arg` 整组替换（不给=不动）；值参用 `=` 形态传（argparse 不把 `--ou` 当裸选项）。"""
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    _seed(root, task_id="a1", run_args=["--account", "old"])
    assert _cli(root, "edit", "a1", "--run-arg=--ou", "--run-arg=EU") == 0
    assert st.load_doc(st.tasks_path(root))["tasks"]["a1"]["run_args"] == ["--ou", "EU"]


def test_edit_without_fields_is_user_error(root):
    _seed(root)
    assert _cli(root, "edit", "a1") == 2


def test_edit_flow_revalidates_writes(root, registry, monkeypatch):
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    _seed(root, task_id="a1")
    assert _cli(root, "edit", "a1", "--flow", "flows/mixed.yaml") == 2
    assert _cli(root, "edit", "a1", "--flow", "flows/mixed.yaml", "--i-accept-writes") == 0


def test_dry_run_plans_without_side_effects(root, spawn, capsys):
    fake = spawn()
    _seed(root)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY, dry=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["action"] == "fire"
    assert payload["command"][:4] == [payload["command"][0], "-m", "atlas", "run"]
    assert fake.calls == []                                           # 没派发
    assert not st.lease_path(root, "patrol").exists()                 # 没抢租约
    assert st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["last_scheduled_for"] is None
    assert _runs(root) == []                                          # 没记


def test_cli_dry_run_flag_reaches_dispatch(root, spawn, capsys):
    _seed(root)
    assert _cli(root, "run", "patrol", "--dry-run") == 0
    assert spawn().calls == [] and "dry_run" in capsys.readouterr().out


# ── tick：全终态映射（fake subprocess）───────────────────────────────

def test_tick_cron_fires_records_and_advances(root, spawn):
    fake = spawn(returncode=0)
    _seed(root)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert len(fake.calls) == 1
    rows = _runs(root)
    assert len(rows) == 1 and rows[0]["outcome"] == "succeeded"
    assert rows[0]["trigger"] == "catch_up" and rows[0]["skipped_slots"] == 2
    assert rows[0]["scheduled_for"] == "2026-11-05T03:00:00+00:00"
    assert rows[0]["counts_as_failure"] is False
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["last_scheduled_for"] == "2026-11-05T03:00:00+00:00"
    assert not st.lease_path(root, "patrol").exists()                 # 收尾放锁


def test_tick_on_time_round_fires_at_exact_hour(root, spawn):
    spawn(returncode=0)
    _seed(root, watermark=datetime(2026, 11, 5, 2, 0, tzinfo=UTC))
    assert dispatch.tick("patrol", _cfg(root),
                        now=datetime(2026, 11, 5, 3, 0, tzinfo=UTC)) == 0
    assert _runs(root)[0]["trigger"] == "schedule"


def test_tick_child_failure_counts_and_returns_one(root, spawn):
    spawn(returncode=1)
    _seed(root)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 1
    rows = _runs(root)
    assert rows[0]["outcome"] == "failed" and rows[0]["counts_as_failure"] is True
    assert rows[0]["child_rc"] == 1
    assert st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["consecutive_failures"] == 1


def test_tick_timeout_is_failure_and_never_self_signals(root, spawn, monkeypatch):
    """X7：超时只由 subprocess 自己 kill，调度器**不补发信号**。"""
    def forbidden(*args, **kwargs):
        raise AssertionError("调度器不得自己发信号（X7）")

    monkeypatch.setattr(dispatch.os, "kill", forbidden, raising=False)
    spawn(raises=subprocess.TimeoutExpired(cmd=["atlas"], timeout=1))
    _seed(root, max_duration_min=15)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 1
    rows = _runs(root)
    assert rows[0]["outcome"] == "aborted_time_limit"
    assert rows[0]["counts_as_failure"] is True
    assert rows[0]["child_rc"] is None
    assert spawn.last().calls[0]["timeout"] == 15 * 60


def test_tick_keyboard_interrupt_is_not_a_failure(root, spawn):
    """CONTROL_FLOW 在调度域的第二实现（#114）：记 interrupted、不计败、照常传播。"""
    spawn(raises=KeyboardInterrupt())
    _seed(root, failures=2)
    with pytest.raises(KeyboardInterrupt):
        dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)
    rows = _runs(root)
    assert rows[0]["outcome"] == "interrupted" and rows[0]["counts_as_failure"] is False
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["consecutive_failures"] == 2          # 不动连败，也不清零
    assert not st.lease_path(root, "patrol").exists()  # finally 仍放了锁


def test_tick_cli_maps_interrupt_to_130(root, spawn):
    spawn(raises=KeyboardInterrupt())
    _seed(root)
    assert _cli(root, "run", "patrol") == 130


def test_tick_fifth_consecutive_failure_auto_pauses(root, spawn, capsys):
    """表 9：环境挂一整晚 → 停在有原因的 enabled=false，而不是每小时刷失败。"""
    spawn(returncode=1)
    _seed(root, failures=4)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 1
    err = capsys.readouterr().err
    assert "AUTO-PAUSED: patrol after 5 consecutive failures" in err
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["enabled"] is False and task["auto_paused_reason"] == "rc=1"


def test_tick_interrupt_sequence_never_accumulates(root, spawn):
    """连败计数对 interrupted 免疫：整晚 Ctrl-C 也不会把任务停掉（与 timeout 相反）。"""
    spawn(raises=KeyboardInterrupt())
    _seed(root)
    for minute in range(1, 6):
        with pytest.raises(KeyboardInterrupt):
            dispatch.tick("patrol", _cfg(root),
                         now=datetime(2026, 11, 5, minute, 30, tzinfo=UTC))
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["consecutive_failures"] == 0 and task["enabled"] is True


def test_tick_timeout_sequence_pauses_at_five(root, spawn):
    spawn(raises=subprocess.TimeoutExpired(cmd=["atlas"], timeout=1))
    _seed(root)
    for minute in range(1, 6):
        assert dispatch.tick("patrol", _cfg(root),
                            now=datetime(2026, 11, 5, minute, 30, tzinfo=UTC)) == 1
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["enabled"] is False and "timeout" in task["auto_paused_reason"]


def test_success_resets_the_streak(root, spawn):
    spawn(returncode=1)
    _seed(root, failures=4)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 1
    assert st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["enabled"] is False
    # 人工 enable 后成功一轮 → 计数归零
    assert _cli(root, "enable", "patrol") == 0
    spawn(returncode=0)
    assert dispatch.tick("patrol", _cfg(root),
                        now=datetime(2026, 11, 5, 9, 30, tzinfo=UTC)) == 0
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["consecutive_failures"] == 0 and task["enabled"] is True


def test_tick_disabled_task_is_silent_zero(root, spawn):
    fake = spawn()
    _seed(root, enabled=False)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert fake.calls == [] and _runs(root) == []


def test_tick_manual_task_does_nothing(root, spawn):
    fake = spawn()
    _seed(root, schedule={"kind": "manual"})
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert fake.calls == [] and _runs(root) == []


def test_tick_once_task_disables_after_claim(root, spawn):
    fake = spawn(returncode=0)
    _seed(root, schedule={"kind": "once", "at": "2026-11-05T01:00:00+00:00"})
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["enabled"] is False                      # §5：once 认领即停
    assert len(fake.calls) == 1
    assert _runs(root)[0]["outcome"] == "succeeded"


def test_tick_once_in_future_does_not_dispatch(root, spawn):
    fake = spawn(returncode=0)
    _seed(root, schedule={"kind": "once", "at": "2026-11-06T09:00:00+00:00"})
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert fake.calls == []
    assert st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["enabled"] is True


def test_tick_beyond_window_drops_without_dispatch(root, spawn):
    """表 6：>7d 丢弃——水位线推进、记一笔、不派发、不计败、rc0。"""
    fake = spawn()
    _seed(root, schedule={"kind": "cron", "expr": "0 0 1 * *", "tz": "UTC"},
          watermark=datetime(2026, 1, 1, tzinfo=UTC))
    assert dispatch.tick("patrol", _cfg(root),
                        now=datetime(2026, 11, 8, 12, 0, tzinfo=UTC)) == 0
    assert fake.calls == []
    rows = _runs(root)
    assert rows[0]["outcome"] == "dropped" and rows[0]["skipped_slots"] == 1
    assert rows[0]["counts_as_failure"] is False
    assert st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["last_scheduled_for"] \
        == "2026-11-01T00:00:00+00:00"


def test_tick_fresh_lease_defers_without_moving_watermark(root, spawn):
    """§6.4 推迟≠跳过：占着锁的上一任才是那轮的合法执行者，水位线不动，rc0。"""
    fake = spawn()
    _seed(root)
    lock = st.lease_path(root, "patrol")
    lock.parent.mkdir(parents=True, exist_ok=True)
    assert lease.try_acquire(lock, "holder", 999, __import__("time").time()) is True
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert fake.calls == []
    rows = _runs(root)
    assert rows[0]["outcome"] == "defer_lease" and rows[0]["counts_as_failure"] is False
    assert rows[0]["scheduled_for"] is None
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["last_scheduled_for"] is None                      # 水位线纹丝不动
    assert lease.read_lock(lock)["token"] == "holder"              # 别人的锁不碰


def test_tick_defer_then_next_round_runs(root, spawn):
    """推迟≠跳过：锁释放后，下一轮 OS tick 把同一轮补上（不丢轮）。"""
    fake = spawn(returncode=0)
    _seed(root)
    lock = st.lease_path(root, "patrol")
    lock.parent.mkdir(parents=True, exist_ok=True)
    import time as _time
    holder = _time.time()
    lease.try_acquire(lock, "holder", 1, holder)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    lease.release(lock, "holder")
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert len(fake.calls) == 1
    outcomes = [row["outcome"] for row in _runs(root)]
    assert outcomes == ["defer_lease", "succeeded"]
    assert st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["last_scheduled_for"] \
        == "2026-11-05T03:00:00+00:00"


def test_tick_reclaims_stale_lease_and_records_it(root, spawn):
    """孤儿锁接管：额外落 lease_reclaimed 记录（含上一任 pid/host），本轮照常跑。"""
    fake = spawn(returncode=0)
    _seed(root)
    lock = st.lease_path(root, "patrol")
    lock.parent.mkdir(parents=True, exist_ok=True)
    assert lease.try_acquire(lock, "orphan", 4321, 1.0) is True       # 心跳早就过期
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    rows = _runs(root)
    assert [row["outcome"] for row in rows] == ["lease_reclaimed", "succeeded"]
    assert rows[0]["stale"]["pid"] == 4321 and "host" in rows[0]["stale"]
    assert len(fake.calls) == 1


def test_tick_corrupt_lease_warns_and_takes_over(root, spawn, capsys):
    lock = st.lease_path(root, "patrol")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("{半截", encoding="utf-8")
    _seed(root)
    spawn(returncode=0)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert "WARN" in capsys.readouterr().err              # 噪音行如实记，不静默
    assert _runs(root)[-1]["outcome"] == "succeeded"


def test_tick_loses_cas_race_and_stays_quiet(root, spawn, monkeypatch):
    """CAS 输掉=另一进程先认领本轮：幂等退出 0，不双跑也不双记（互斥权威仍是租约，C1-F）。"""
    fake = spawn()
    _seed(root)
    monkeypatch.setattr(st, "claim_slot", lambda *args, **kwargs: False)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert fake.calls == [] and _runs(root) == []


def test_tick_missing_flow_file_is_user_error(root, spawn):
    _seed(root, flow="flows/ghost.yaml")
    spawn()
    with pytest.raises(ValueError, match="不存在"):
        dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)


def test_tick_unknown_id_is_user_error(root):
    with pytest.raises(ValueError, match="不存在"):
        dispatch.tick("ghost", _cfg(root), now=THREE_THIRTY)


def test_tick_releases_lock_even_when_ledger_write_fails(root, spawn, monkeypatch):
    """#110/#130 同纪律：台账落盘炸不顶业务信号，锁必须放掉（否则下次永远 defer）。"""
    spawn(returncode=0)
    _seed(root)
    real_claim = st.claim_slot

    def claim_then_break(path, task_id, expected, new_value):
        real_claim(path, task_id, expected, new_value)
        monkeypatch.setattr(st, "save_doc",
                            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("盘满")))
        return True

    monkeypatch.setattr(st, "claim_slot", claim_then_break)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert not st.lease_path(root, "patrol").exists()


# ── argv / env（红线 #13/#2 的实现强度）──────────────────────────────

def test_argv_never_contains_approve_or_mode(root, spawn):
    """§6.3 专项测：调度器只拼 `atlas run <flow>`，永不带 --approve/--mode。"""
    _seed(root, run_args=["--account", "ops", "--ou", "EU"])
    command = dispatch.build_command(
        st.load_doc(st.tasks_path(root))["tasks"]["patrol"], root)
    assert command[:4] == [command[0], "-m", "atlas", "run"]
    assert command[4] == "flows/patrol.yaml"
    text = " ".join(command)
    assert "approve" not in text and "--mode" not in text
    spawn(returncode=0)
    dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)
    assert "approve" not in " ".join(spawn.last().calls[0]["command"])


def test_build_command_rejects_approve_at_assembly(root):
    """装配处也要 raise（不靠 assert，C1-C）：台账被手改坏也得拒。"""
    with pytest.raises(ValueError, match="--approve"):
        dispatch.build_command({"flow": "flows/patrol.yaml", "run_args": ["--approve"]}, root)


def test_child_env_injects_pythonpath_and_io_encoding(root):
    """C1-E：src 布局下 OS cron 的干净 env 里 `python -m atlas` 必须可导入。"""
    env = dispatch.child_env(root, base={"PATH": "/bin"})
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert str(root / "src") in env["PYTHONPATH"]
    merged = dispatch.child_env(root, base={"PYTHONPATH": "/elsewhere"})
    assert "/elsewhere" in merged["PYTHONPATH"] and str(root / "src") in merged["PYTHONPATH"]


def test_child_env_skips_pythonpath_when_no_src_layout(tmp_path):
    env = dispatch.child_env(tmp_path, base={})
    assert "PYTHONPATH" not in env            # 真装成包时不注入假路径
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_child_env_preserves_knowledge_dir_from_base(root, monkeypatch):
    """派发子进程必须继承 ATLAS_KNOWLEDGE_DIR——否则无人值守静默跑包内虚构知识。

    判据不新建：调度器进程走 AtlasConfig.from_root→load_dotenv(.env) 已把该键 setdefault 进
    os.environ，child_env 继承 os.environ 就带走它。这里用 base 显式注入验证继承链不断。
    """
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(root / "private-kb"))
    env = dispatch.child_env(root)
    assert env["ATLAS_KNOWLEDGE_DIR"] == str(root / "private-kb")
    # 调度器已把键带进自身 env（cron 场景）时，子进程也拿得到（不依赖 shell export 的继承）


def test_tick_passes_cwd_timeout_env(root, spawn):
    _seed(root, max_duration_min=7)
    spawn(returncode=0)
    dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)
    call = spawn.last().calls[0]
    assert call["cwd"] == str(root)
    assert call["timeout"] == 7 * 60
    assert call["env"]["PYTHONIOENCODING"] == "utf-8"


def test_child_command_digest_drops_values(root, spawn):
    """台账里只留 argv 形态（flag），不留值参——账号值不进调度索引。"""
    _seed(root, run_args=["--account", "secret-ou-value"])
    spawn(returncode=0)
    dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)
    digest = _runs(root)[0]["child_command"]
    assert "--account" in digest and "secret-ou-value" not in digest


def test_schedule_run_line_has_full_schema(root, spawn):
    _seed(root)
    spawn(returncode=0)
    dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)
    row = _runs(root)[0]
    assert set(row) == {"run_key", "task_id", "trigger", "scheduled_for", "skipped_slots",
                        "started_at", "ended_at", "outcome", "counts_as_failure", "child_rc",
                        "child_command", "engine_run_id"}
    assert row["engine_run_id"] is None      # 跨域关联不猜（并发时必错），留人按 started_at 对照
    assert len(row["run_key"]) == 12


def test_run_key_is_unique_per_line(root, spawn):
    spawn(returncode=1)
    _seed(root)
    for hour in (1, 2, 3):
        dispatch.tick("patrol", _cfg(root), now=datetime(2026, 11, 5, hour, 30, tzinfo=UTC))
    keys = [row["run_key"] for row in _runs(root)]
    assert len(keys) == 3 and len(set(keys)) == 3


# ── 心跳线程（起停无泄漏）────────────────────────────────────────────

def test_heartbeat_renews_lock_and_thread_stops(root, spawn, monkeypatch):
    calls = []
    monkeypatch.setattr(lease, "heartbeat",
                        lambda path, token, now: (calls.append(token), True)[1])
    monkeypatch.setattr(dispatch, "HEARTBEAT_INTERVAL_S", 0.01)
    fake = spawn(returncode=0, delay=0.06)
    _seed(root)
    assert dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY) == 0
    assert calls, "派发窗口内必须续租"
    assert calls[0] == calls[-1]                                   # 全程同一个 token
    assert [item for item in threading.enumerate()
            if item.name.startswith("atlas-lease")] == []          # join 超时兜底，不留线程
    assert len(fake.calls) == 1


def test_heartbeat_stops_even_when_child_raises(root, spawn, monkeypatch):
    monkeypatch.setattr(dispatch, "HEARTBEAT_INTERVAL_S", 0.01)
    spawn(raises=subprocess.TimeoutExpired(cmd=["atlas"], timeout=1))
    _seed(root)
    dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)
    assert [item for item in threading.enumerate() if item.name.startswith("atlas-lease")] == []


def test_heartbeat_carries_our_token(root, spawn, monkeypatch):
    seen = {}

    def spy(path, token, now):
        seen["token"] = token
        seen["path"] = Path(path)
        return True

    monkeypatch.setattr(lease, "heartbeat", spy)
    monkeypatch.setattr(dispatch, "HEARTBEAT_INTERVAL_S", 0.01)
    spawn(returncode=0, delay=0.05)
    _seed(root)
    dispatch.tick("patrol", _cfg(root), now=THREE_THIRTY)
    assert seen["path"] == st.lease_path(root, "patrol")
    assert len(seen["token"]) == 16                                # token 来自 secrets，不猜 pid


# ── crontab 双平台文案（X8 + 评审 C1-A/C1-D）────────────────────────

def test_crontab_linux_copies_expr_verbatim(root):
    """C1-A 裁决：Linux 行 = expr 逐字复制（窗外语义保真，窗外错过由窗内下一 tick 补）。"""
    _seed(root, schedule={"kind": "cron", "expr": "*/30 1-8 * * *", "tz": "Europe/Amsterdam"})
    line = _capture(root, "crontab", "patrol", "--platform", "linux").strip().splitlines()[0]
    assert line.startswith("*/30 1-8 * * * cd ")
    assert "schedule run patrol" in line and "reports/schedule/cron.log 2>&1" in line
    assert "PYTHONPATH=" in line                                   # C1-E 同款接线要求
    assert "*/30 * * * *" not in line                              # 不许把 1-8 时段丢了


def test_crontab_linux_notes_wall_clock_and_tz_semantics(root, capsys):
    _seed(root, schedule={"kind": "cron", "expr": "0 * * * *", "tz": "Europe/Amsterdam"})
    assert _cli(root, "crontab", "patrol", "--platform", "linux") == 0
    err = capsys.readouterr().err
    assert "墙钟" in err and "cronie" in err
    assert "本地文件系统" in err                                    # C1-D 同步盘条款
    assert "NTP" in err


def test_crontab_windows_uses_schtasks_minute_multiplier(root):
    """C1-A 裁决：Windows 行 = /SC MINUTE /MO <expr 最小间隔>（窗外 catch-up 与 pi 常驻同语义）。"""
    _seed(root, schedule={"kind": "cron", "expr": "*/30 1-8 * * *", "tz": "Europe/Amsterdam"})
    line = _capture(root, "crontab", "patrol", "--platform", "windows").strip().splitlines()[0]
    assert line.startswith("schtasks /Create /SC MINUTE /MO 30 /TN atlas-patrol /TR ")
    assert "schedule run patrol" in line
    assert '\\"PYTHONPATH=' in line                                # 内层引号转义，不撞 /TR 外层


def test_crontab_windows_notes_smoke_obligation(root, capsys):
    _seed(root, schedule={"kind": "cron", "expr": "0 * * * *", "tz": "UTC"})
    assert _cli(root, "crontab", "patrol", "--platform", "windows") == 0
    err = capsys.readouterr().err
    assert "schtasks" in err and "冒烟" in err                      # §10 真机冒烟义务写进输出


def test_crontab_windows_rejects_intervals_beyond_limit(root, capsys):
    """>1439 分钟的周期超出 /SC MINUTE 表达力：给指引，不硬造一条跑不动的成品行。"""
    _seed(root, schedule={"kind": "cron", "expr": "0 0 * * 1", "tz": "UTC"})   # 每周一次
    assert _cli(root, "crontab", "patrol", "--platform", "windows") == 2
    assert "/SC DAILY/WEEKLY" in capsys.readouterr().err


def test_crontab_manual_and_once_have_no_os_wiring(root, capsys):
    _seed(root, task_id="m1", schedule={"kind": "manual"})
    assert _cli(root, "crontab", "m1", "--platform", "linux") == 2
    _seed(root, task_id="o1", schedule={"kind": "once", "at": "2026-12-01T00:00:00+00:00"})
    assert _cli(root, "crontab", "o1", "--platform", "linux") == 2
    err = capsys.readouterr().err
    assert "只跑一次" in err and "schedule run m1" in err


def test_crontab_auto_platform_follows_host(root, monkeypatch):
    _seed(root)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert "schtasks" in _capture(root, "crontab", "patrol")
    monkeypatch.setattr(cli.sys, "platform", "linux")
    out = _capture(root, "crontab", "patrol")
    assert "schtasks" not in out and out.strip().splitlines()[0].startswith("0 * * * * cd ")


def test_crontab_does_not_register_anything(root, spawn):
    """Atlas 绝不代跑 schtasks/crontab（Ask-first 边界）：全程零 subprocess。"""
    fake = spawn(returncode=0)
    _seed(root)
    _capture(root, "crontab", "patrol", "--platform", "windows")
    _capture(root, "crontab", "patrol", "--platform", "linux")
    assert fake.calls == []


# ── rc 口径表（2 用户错误 / 0 调度结果 / 1 派发失败）─────────────────

def test_rc_two_for_unknown_task_id(root, spawn):
    spawn()
    assert _cli(root, "run", "ghost") == 2


def test_rc_zero_for_manual_and_deferred(root, spawn):
    """defer / none / dropped 都是正常答复：cron 不看 rc 干活。"""
    _seed(root, task_id="m", schedule={"kind": "manual"})
    spawn(returncode=0)
    assert _cli(root, "run", "m") == 0


def test_rc_one_only_for_child_failure(root, spawn):
    spawn(returncode=1)
    _seed(root)
    assert _cli(root, "run", "patrol") == 1


def test_schedule_state_files_live_under_reports(root):
    """状态文件全在 reports/schedule/：真实域名/账号名产物天然不入库（红线 #11/PRIVACY 同族）。"""
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "reports/" in gitignore
    _seed(root)
    assert st.schedule_dir(root) == root / "reports" / "schedule"


def test_parser_accepts_global_args_on_both_sides(root):
    """全局参数位置纪律（真机冒烟抓过的 argparse 坑）：--root 在子命令前后都得认。"""
    assert main(["schedule", "list", "--root", str(root)]) == 0
    assert main(["--root", str(root), "schedule", "list"]) == 0


# ── cron env 缺口收口（impl_report 实测风险 + team-lead 拍板 1+2）────────
# 根因：cron/schtasks 的父 env 不继承交互式 shell export 的 ATLAS_KNOWLEDGE_DIR，
# 无人值守轮次会静默回退包内虚构知识——回退本身合法（"读不残缺"），所以要「入库即可见 + 成品行带键」。
# 判据只在 versioning.private_knowledge_root 一处，这里全部走它，不新建第二份可用性判定。

@pytest.fixture
def private_kb(tmp_path):
    kb = tmp_path / "real-kb"
    kb.mkdir()
    (kb / "pitfalls.json").write_text('{"pitfalls": []}', encoding="utf-8")
    return kb


def test_add_records_builtin_source_and_warns(root, registry, monkeypatch, capsys):
    """无 ATLAS_KNOWLEDGE_DIR：entry 记 knowledge_source=builtin 且当场 ASCII WARN 点名风险。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "p", "--flow", "flows/patrol.yaml", "--manual") == 0
    err = capsys.readouterr().err
    assert "knowledge_source=builtin" in err and "ATLAS_KNOWLEDGE_DIR" in err
    task = st.load_doc(st.tasks_path(root))["tasks"]["p"]
    assert task["knowledge_source"] == "builtin"     # 入库时就可见，不等人事后翻 drifted


def test_add_with_private_kb_no_warn_and_records_private(root, registry, monkeypatch, capsys,
                                                          private_kb):
    """私有库已配（.env 或 env 均可）：无 builtin WARN，entry 记 private。"""
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(private_kb))
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    assert _cli(root, "add", "p", "--flow", "flows/patrol.yaml", "--manual") == 0
    assert "knowledge_source=builtin" not in capsys.readouterr().err
    assert st.load_doc(st.tasks_path(root))["tasks"]["p"]["knowledge_source"] == "private"


def test_edit_refreshes_knowledge_source_observation(root, registry, monkeypatch, private_kb):
    """改配置（人在场）刷新观测：先 builtin，配上私有库再 edit → private。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    monkeypatch.setattr(cli, "_full_registry", lambda: registry)
    _cli(root, "add", "p", "--flow", "flows/patrol.yaml", "--cron", "0 * * * *", "--tz", "UTC")
    assert st.load_doc(st.tasks_path(root))["tasks"]["p"]["knowledge_source"] == "builtin"
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(private_kb))
    _cli(root, "edit", "p", "--max-duration-min", "15")
    assert st.load_doc(st.tasks_path(root))["tasks"]["p"]["knowledge_source"] == "private"


def test_crontab_linux_carries_knowledge_dir_key_placeholder(root, monkeypatch):
    """未设私有库：Linux 成品行仍带 ATLAS_KNOWLEDGE_DIR 键（占位串，不静默留空）。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    _seed(root, schedule={"kind": "cron", "expr": "0 * * * *", "tz": "UTC"})
    out = _capture(root, "crontab", "patrol", "--platform", "linux")
    line = out.splitlines()[-1]          # 成品命令行（stderr 提醒走 _cli，_capture 只吞 stdout）
    assert "ATLAS_KNOWLEDGE_DIR=" in line and "__FILL_ABSOLUTE_PATH_TO_PRIVATE_KB__" in line


def test_crontab_linux_uses_real_kb_when_set(root, monkeypatch, private_kb):
    """已配私有库：成品行内嵌真实绝对路径，不再有占位串。"""
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(private_kb))
    _seed(root, schedule={"kind": "cron", "expr": "0 * * * *", "tz": "UTC"})
    line = _capture(root, "crontab", "patrol", "--platform", "linux").splitlines()[-1]
    assert "ATLAS_KNOWLEDGE_DIR=" in line and "__FILL" not in line


def test_crontab_unset_warns_on_stderr(root, monkeypatch, capsys):
    """未设私有库时 crontab 必须 stderr 出声（不留空被忽略），点名占位与 .env 两条出路。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    _seed(root, schedule={"kind": "cron", "expr": "0 * * * *", "tz": "UTC"})
    assert _cli(root, "crontab", "patrol", "--platform", "linux") == 0
    err = capsys.readouterr().err
    assert "ATLAS_KNOWLEDGE_DIR" in err and "__FILL_ABSOLUTE_PATH_TO_PRIVATE_KB__" in err


def test_crontab_windows_carries_knowledge_dir_set_clause(root, monkeypatch):
    """Windows 形态 /TR 里也带 set ATLAS_KNOWLEDGE_DIR（转义内层引号，同 PYTHONPATH 纪律）。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    _seed(root, schedule={"kind": "cron", "expr": "0 * * * *", "tz": "UTC"})
    line = _capture(root, "crontab", "patrol", "--platform", "windows").splitlines()[-1]
    assert 'ATLAS_KNOWLEDGE_DIR=' in line and '\\"ATLAS_KNOWLEDGE_DIR=' in line

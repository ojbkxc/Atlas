"""`schedule run --dry-run` 对**停用任务**必须给出计划（含 `enabled: false`）。

## 挡的是什么（2026-11-24 实测）

`dispatch.tick` 旧实现把停用判定放在 dry 分支**之前**：

```python
if not task.get("enabled", True):
    return 0            # 零输出
moment = ...
if dry: print(json...)
```

于是 `atlas schedule run <id> --dry-run` 对一个 AUTO-PAUSED 的任务**什么都不打印、rc 0**——
人分不清「任务被我停用了」和「这轮还没到期」（后者也是 rc 0 + action=none）。而
AUTO-PAUSED 的真实排查路径恰恰是「先看 dry-run 计划，再翻 tasks.json」，第一步就哑了。

## 修法

把停用判定移到 dry 分支**之后**，并在计划 JSON 里加 `enabled` 字段；实际 tick 语义
逐字不变（仍 return 0、不派发、不记台账、不抢租约）。

## 五条

1. `test_disabled_dry_run_prints_plan`：停用任务 dry-run 必须打印 JSON（改前零输出）；
2. `test_disabled_dry_run_marks_enabled_false`：计划里 `enabled` 为 false（**可判别**：
   与「未到期」的差异在字段上，不在「有没有输出」这种脆弱信号上）；
3. `test_enabled_dry_run_still_says_enabled_true`：正向对照——启用任务仍是 `enabled: true`；
4. `test_disabled_real_tick_stays_silent`：**实际 tick 语义不变**——停用任务非 dry 仍零输出、
   rc 0、不派发、不记台账（这条钉住「只改诊断面」）；
5. `test_dry_run_still_has_no_side_effects`：停用与启用两种 dry-run 都不抢租约、不写
   `schedule_runs.jsonl`、不动水位线。
"""
from __future__ import annotations

import json

import pytest

from atlas.config import AtlasConfig
from atlas.scheduling import dispatch
from atlas.scheduling import state as st


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".atlas-root").write_text("", encoding="utf-8")
    flow = tmp_path / "flow.yaml"
    flow.write_text("id: f\nsteps: []\n", encoding="utf-8")
    reports = tmp_path / "reports" / "schedule"
    reports.mkdir(parents=True, exist_ok=True)
    moment = "2026-11-05T00:00:00+00:00"
    doc = {"schema_version": st.SCHEMA_VERSION, "tasks": {"paused": {
        "id": "paused", "enabled": False, "flow": str(flow),
        "schedule": {"kind": "cron", "expr": "30 1-8 * * *", "tz": "Europe/Amsterdam"},
        "created_at": moment, "last_scheduled_for": None,
        "consecutive_failures": 5, "auto_paused_reason": "rc=1",
        "writes_acknowledged": False,
    }, "live": {
        "id": "live", "enabled": True, "flow": str(flow),
        "schedule": {"kind": "cron", "expr": "30 1-8 * * *", "tz": "Europe/Amsterdam"},
        "created_at": moment, "last_scheduled_for": None,
        "consecutive_failures": 0, "auto_paused_reason": None,
        "writes_acknowledged": False,
    }}}
    (reports / "tasks.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


def _cfg(root) -> AtlasConfig:
    return AtlasConfig(root=root, env="sit", mode="run")


def _plan(capsys) -> dict:
    out = capsys.readouterr().out
    assert out.strip(), "dry-run 什么都没打印（停用与未到期无法区分）"
    return json.loads(out)


def test_disabled_dry_run_prints_plan(root, capsys):
    assert dispatch.tick("paused", _cfg(root), dry=True) == 0
    plan = _plan(capsys)
    assert plan["dry_run"] is True and plan["task_id"] == "paused"


def test_disabled_dry_run_marks_enabled_false(root, capsys):
    dispatch.tick("paused", _cfg(root), dry=True)
    plan = _plan(capsys)
    assert plan["enabled"] is False, (
        f"停用任务在计划里没标出来，人无法与「未到期」区分：{plan}")


def test_enabled_dry_run_still_says_enabled_true(root, capsys):
    dispatch.tick("live", _cfg(root), dry=True)
    plan = _plan(capsys)
    assert plan["enabled"] is True


def test_disabled_real_tick_stays_silent(root, capsys, monkeypatch):
    """实际 tick 语义不变：停用 → 零输出、rc 0、不派发、不记台账。"""
    calls = []
    monkeypatch.setattr(dispatch, "_run_with_heartbeat",
                        lambda *a, **k: calls.append(a) or ("succeeded", 0, None, None, None))
    assert dispatch.tick("paused", _cfg(root)) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "", f"停用任务的真实 tick 不该有输出：{captured.out!r}"
    assert calls == [], "停用任务被派发了"
    runs = root / "reports" / "schedule" / "schedule_runs.jsonl"
    assert not runs.exists() or runs.read_text(encoding="utf-8").strip() == ""


def test_dry_run_still_has_no_side_effects(root, capsys):
    for task_id in ("paused", "live"):
        dispatch.tick(task_id, _cfg(root), dry=True)
        capsys.readouterr()
        assert not st.lease_path(root, task_id).exists(), f"{task_id}: dry-run 抢了租约"
    doc = st.load_doc(st.tasks_path(root))
    assert all(t["last_scheduled_for"] is None for t in doc["tasks"].values()), \
        "dry-run 动了水位线"
    runs = root / "reports" / "schedule" / "schedule_runs.jsonl"
    assert not runs.exists(), "dry-run 记了台账"

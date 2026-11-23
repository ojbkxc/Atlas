"""`api_ms` 的**逐步归因**守卫：不许把上一步的接口耗时记到这一步头上。

## 挡的是什么（分析出的跨步粘性状态缺陷）

`CallApi.last_timing_ms` 只有**真发请求**时才更新，是粘性值。旧 `engine` 在每步结束时直接
`record["api_ms"] = actor.api.last_timing_ms`，于是：

- 第 1 步 `api_call` 发了一次请求（假设 120ms）→ `api_ms=120`；
- 第 2 步是**API 通道但本步没发请求**的步骤（`assert_api` 读 ctx、自定义 `_perform_api` 走
  自己的传输、写闸拦停后的步骤…）→ `api_ms` 仍是 **120**。

`runs.jsonl` 里那一行会声称「这一步的接口耗时 120ms」，而这一步一个请求都没发；
`atlas summary` / `atlas report` 的接口耗时视图随之错位，且没有任何报错——审计者看不出
「这个数不是这步的」。与 P0「重放 handler 跨步残留」同族：**跨步粘性状态**。

## 修法

`CallApi.reset_timing()`（归零单一入口）+ engine 在 `perform` **之前**归零：谁采样谁归零，
不留第二处直接改属性。

## 四条

1. `test_api_ms_is_zero_when_step_makes_no_request`：两步（有请求 / 无请求）→ 第二步 `api_ms` 必须是 0
   （改前它是第一步的耗时）；
2. `test_api_ms_records_actual_request_time`：有请求那步必须记到真实耗时（>0）；
3. `test_reset_timing_is_single_entry`：源码面——`last_timing_ms =` 只在 `__init__` 与
   `reset_timing` 出现，engine 不得直接赋值（防「第二个归零点」漂移，@92）；
4. `test_api_leg_without_ability_keeps_zero`：没有 CallApi ability 时 `api_ms` 保持 0（正向对照，
   旧行为本来就对，不能被这次改动带歪）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from atlas import Actor, Channel, FunctionalTask, TaskMetadata
from atlas.abilities import CallApi
from atlas.config import AtlasConfig
from atlas.engine import ExecutionEngine
from atlas.recorder import RunRecorder
from atlas.dsl import TaskRegistry

_REPO = Path(__file__).resolve().parents[1]


class _FakeResponse:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    text = "{}"
    url = "http://api.example.invalid/api/thing"
    reason = "OK"

    @property
    def ok(self) -> bool:
        return True

    def json(self):
        return {"resultCode": "BUG-000", "ok": True}


class _FakeHttp:
    """最小 requests.Session 替身：只回一份 JSON（零网络）。"""

    def __init__(self):
        self.headers = {}
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return _FakeResponse()


def _call_task() -> FunctionalTask:
    return FunctionalTask(TaskMetadata("demo", "read", produces=("thing",), channel=Channel.API),
                          lambda actor: {"thing": actor.api.get("/api/thing")}, "call_thing")


def _no_call_task() -> FunctionalTask:
    """API 通道但**不发请求**（读 ctx / 纯断言型步骤的真实形态）。"""
    return FunctionalTask(TaskMetadata("demo", "read", channel=Channel.API),
                          lambda actor: {"checked": True}, "assert_thing")


def _run(tmp_path, tasks: list, *, api: bool = True) -> dict:
    registry = TaskRegistry()
    for task in tasks:
        registry.register(task.name, lambda _t=task, **_: _t)
    runs = tmp_path / "runs.jsonl"
    recorder = RunRecorder("timing", "sit", runs)
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=recorder, case_id="timing")
    abilities = [CallApi("http://api.example.invalid", session=_FakeHttp())] if api else []
    actor = Actor(abilities=abilities)
    for task in tasks:
        engine.execute(actor, task)
    recorder.close("passed")
    rows = [json.loads(line) for line in recorder.path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    # 一行 run 记录内含 steps[]（与真实 runs.jsonl 同构）
    by_step = {}
    for row in rows:
        for step in row.get("steps", []):
            by_step[step.get("step")] = step
    return by_step


def test_api_ms_is_zero_when_step_makes_no_request(tmp_path):
    rows = _run(tmp_path, [_call_task(), _no_call_task()])
    assert rows["call_thing"]["api_ms"] > 0, "有请求的步骤应记到真实耗时"
    assert rows["assert_thing"]["api_ms"] == 0, (
        f"没发请求的步骤继承了上一步的接口耗时：api_ms={rows['assert_thing']['api_ms']}")


def test_api_ms_records_actual_request_time(tmp_path):
    rows = _run(tmp_path, [_call_task()])
    assert isinstance(rows["call_thing"]["api_ms"], (int, float))
    assert rows["call_thing"]["api_ms"] > 0


def test_reset_timing_is_single_entry():
    """归零点单点：engine 不得直接改 `last_timing_ms`，只许调 `reset_timing()`。"""
    engine_src = (_REPO / "src" / "atlas" / "engine.py").read_text(encoding="utf-8")
    assert "reset_timing()" in engine_src, "engine 没有调归零入口"
    assert not re.search(r"last_timing_ms\s*=(?!=)", engine_src), \
        "engine 直接赋值了 last_timing_ms（第二归零点）"
    abilities_src = (_REPO / "src" / "atlas" / "abilities.py").read_text(encoding="utf-8")
    assigns = re.findall(r"self\.last_timing_ms\s*=", abilities_src)
    assert len(assigns) == 3, (
        f"abilities.py 里 last_timing_ms 赋值点应恰为 3（__init__ 初始化 / reset_timing 归零 / "
        f"request 实测更新），实为 {len(assigns)}——新增第四个点说明有第二条采样通道")


def test_api_leg_without_ability_keeps_zero(tmp_path):
    """没有 CallApi ability 时 API 通道步骤仍要留 api_ms=0（正向对照：旧行为本来就对）。"""
    rows = _run(tmp_path, [_no_call_task()], api=False)
    assert rows["assert_thing"].get("api_ms", 0) == 0, (
        f"无 CallApi 时 api_ms 应为 0，实为 {rows['assert_thing'].get('api_ms')}")

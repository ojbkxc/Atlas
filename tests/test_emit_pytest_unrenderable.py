"""T8-A2：导出器对「无渲染分支」的核心 Task（assert_api/demo_api）点名真因。

背景：`_specs_from_flow` 旧把非 `api_call` 的步一律当 UI 步 → `assert_api` 被误报「UI 步缺
声明位」（指向假因：补声明位也救不了，导出器对它根本没有渲染分支）。本波改为在唯一判据点
（`_UNRENDERABLE_TASKS` + `_reject_unrenderable_task`）点名真因 + 替代路径。

判据对：含不可渲染 Task → 点名该 Task 且**不含**「缺声明位」；真缺声明位的纯 UI flow →
**仍报**「缺声明位」（两条不互相吞）。离线、零网络、零执行。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from atlas.cli import main
from atlas.generator.emit_pytest import (
    PytestEmitError, emit_pytest_from_flow, emit_pytest_package,
    _specs_from_flow,
    _UNRENDERABLE_TASKS, _reject_unrenderable_task,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = str(ROOT / "examples" / "atlas_portable_smoke.yaml")


def _flow(*steps):
    return {"id": "u", "steps": list(steps)}


# ---------------------------------------------------------------------------
# ① 不可渲染 Task：点名真因，绝不误报缺声明位
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task", ["assert_api", "demo_api"])
def test_unrenderable_task_names_real_cause(task):
    flow = _flow({"task": task, "id": "s1", "intent": "断言/调用"})
    with pytest.raises(PytestEmitError) as ei:
        emit_pytest_from_flow(flow, fast=True)
    msg = str(ei.value)
    assert task in msg                     # 点名真因 Task
    assert "缺声明位" not in msg           # 消灭的形态：不再伪装成缺失声明位
    assert "api_call" in msg               # 给出替代路径
    assert "无法渲染" in msg and "不支持" in msg


def test_assert_api_step_index_and_slug_reported():
    flow = _flow({"task": "api_call", "method": "GET", "path": "/a", "save_as": "r"},
                 {"task": "assert_api", "id": "chk", "intent": "校验", "operation": "x"})
    with pytest.raises(PytestEmitError) as ei:
        _specs_from_flow(flow)
    msg = str(ei.value)
    assert "第 2 步" in msg and "chk" in msg and "assert_api" in msg


def test_genuine_missing_declaration_still_reports_missing():
    """正向对照（两判据不互相吞）：纯 UI 步无声明位 → **仍**报「缺声明位」（对的真因）。"""
    flow = _flow({"task": "open_doc_list", "id": "s1", "intent": "打开列表"})
    with pytest.raises(PytestEmitError) as ei:
        emit_pytest_from_flow(flow, fast=True)
    assert "缺声明位" in str(ei.value)


def test_run_order_when_both_present_assert_api_wins_named():
    """同一 flow 既有未声明 UI 步又有 assert_api → 报 assert_api（真因），不因先撞 UI 步而漂移。"""
    flow = _flow({"task": "open_doc_list", "id": "s1", "intent": "打开"},
                 {"task": "assert_api", "id": "s2", "intent": "校验"})
    with pytest.raises(PytestEmitError) as ei:
        emit_pytest_from_flow(flow, fast=True)
    msg = str(ei.value)
    assert "assert_api" in msg and "缺声明位" not in msg


# ---------------------------------------------------------------------------
# ② 判据单点：全导出入口同一处点名（不留第二份名单）
# ---------------------------------------------------------------------------

def test_all_export_entrypoints_share_single_judgment():
    """同一 flow 经不同导出入口 → 报**同一**消息（判定可渲染的只此一处）。"""
    flow = _flow({"task": "assert_api", "id": "s1", "intent": "断言"})
    msgs = []
    for call in (
        lambda: emit_pytest_from_flow(flow, fast=True),
        lambda: emit_pytest_from_flow(flow, fast=False),
        lambda: _specs_from_flow(flow),
    ):
        with pytest.raises(PytestEmitError) as ei:
            call()
        msgs.append(str(ei.value))
    assert len(set(msgs)) == 1, msgs


def test_unrenderable_set_is_the_named_core_tasks():
    """名单钉死：恰为两个 Channel.API 核心 Task（编辑时若扩容/缩容必须显式复评）。"""
    assert set(_UNRENDERABLE_TASKS) == {"assert_api", "demo_api"}


def test_reject_helper_noop_for_renderable_task():
    """判据函数对可渲染 Task 不抛（防空转恒拒）：api_call / 任意 UI 任务名原样放行。"""
    for name in ("api_call", "open_doc_list", "ui_action", "legacy_step", ""):
        _reject_unrenderable_task(name, 1, "s")  # 不抛即通过


def test_single_point_source_scan():
    """源码级：不存在第二份不可渲染名单（只此一处 dict + 一处调用点）。"""
    import atlas.generator.emit_pytest as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert src.count("_UNRENDERABLE_TASKS") == 2      # 定义 + _reject 读取，各一次
    assert src.count("_reject_unrenderable_task(") == 2  # 定义 + _specs_from_flow 调用，各一次


# ---------------------------------------------------------------------------
# ③ 零残留 + 既有行为不变
# ---------------------------------------------------------------------------

def test_package_rejects_before_write_no_residue(tmp_path):
    out = tmp_path / "pkg"
    flow = _flow({"task": "assert_api", "id": "s1", "intent": "断言"})
    with pytest.raises(PytestEmitError) as ei:
        emit_pytest_package(out_dir=out, flow=flow, components={"components": {}}, fast=True)
    assert "assert_api" in str(ei.value)
    assert not out.exists() or not any(out.iterdir())   # 拒绝先于 mkdir


def test_api_only_flow_unaffected():
    """api_call 正常渲染路径零变化：快/非快都能出模块、可编译。"""
    flow = _flow({"task": "api_call", "method": "GET", "path": "/p", "save_as": "r"})
    for fast in (False, True):
        src = emit_pytest_from_flow(flow, fast=fast)
        ast.parse(src)
        assert "def test_01_" in src


def test_sample_flow_still_exports_fast(tmp_path):
    """既有可跑样例仍 rc0（本波不动它）：回归保护。"""
    out = tmp_path / "pkg"
    assert main(["export-pytest", SAMPLE, "--fast", "-o", str(out), "--force"]) == 0
    assert any(p.name.startswith("test_") for p in out.rglob("*") if p.is_file())

"""ob-5 补齐：旧仓预检按需化 + 显式跳过开关；en-5 CLI 侧白名单加载。

背景（gap14 ob-5）：旧实现 `check_legacy_paths` 在 run/agent 轨**无条件**执行——
`--no-login` 且 flow 不含 legacy 任务时仍要求旧项目目录存在，无旧仓平台被迫假造目录；
`cli.py` 四处预检还调未过滤的 `load_dotenv(legacy)`，把旧仓 .env 整份灌进进程环境
（en-5 的跨平台残留污染在主路径上没封口）。

判据边界（本文件钉死）：
- 「需要旧仓」= 登录轨（登录模块在旧仓）∨ flow 含 `needs_legacy_project` 任务；
- 跳过开关只跳过 **fail-fast 预检**，不改变真实调用语义——真需要旧仓时使用点自己报错；
- 默认路径（`login=True` 或 `legacy_required=None`）逐字节旧行为。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import atlas.runtime as rt
from atlas.config import AtlasConfig


def _plain_task():
    """非 legacy 任务的最小替身（flow_needs_legacy 只问类属性）。"""
    return type("PlainTask", (), {})()


def _legacy_step_task():
    return rt.LegacyStepTask("case_x.py", 1)


def _legacy_case_task():
    from atlas.plugins.demo.legacy_case import LegacyCaseTask
    return LegacyCaseTask("case_x.yaml", "TC1")


def test_legacy_required_truth_table():
    """ob-5 判据三态：登录轨恒 True；--no-login 只看任务标记。"""
    assert rt.legacy_required_for(no_login=False, tasks=[]) is True
    assert rt.legacy_required_for(no_login=False, tasks=[_plain_task()]) is True
    assert rt.legacy_required_for(no_login=True, tasks=[]) is False
    assert rt.legacy_required_for(no_login=True, tasks=[_plain_task()]) is False
    assert rt.legacy_required_for(no_login=True, tasks=[_plain_task(), _legacy_step_task()]) is True
    assert rt.legacy_required_for(no_login=True, tasks=[_legacy_case_task()]) is True


def test_legacy_task_classes_declare_marker():
    """标记是任务类自己的声明（新增 legacy 族任务只改类一处，不写清单）。"""
    from atlas.plugins.demo.legacy_case import LegacyCaseTask
    assert rt.LegacyStepTask.needs_legacy_project is True
    assert LegacyCaseTask.needs_legacy_project is True
    assert not getattr(_plain_task(), "needs_legacy_project", False)


def test_check_legacy_required_skips_when_not_required(tmp_path, monkeypatch):
    """required=False → 缺旧仓目录也不报（旧实现无条件假红）。"""
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "missing-legacy"))
    config = AtlasConfig(tmp_path)
    rt.check_legacy_required(config, required=False)   # 不抛


def test_check_legacy_required_raises_when_required(tmp_path, monkeypatch):
    """required=True → 旧行为原样：缺目录在预检处点名（浏览器启动前）。"""
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "missing-legacy"))
    config = AtlasConfig(tmp_path)
    with pytest.raises(FileNotFoundError, match="旧项目根不存在"):
        rt.check_legacy_required(config, required=True)


def test_skip_switch_skips_and_warns(tmp_path, monkeypatch, capsys):
    """ATLAS_SKIP_LEGACY_CHECK=1 显式跳过：只跳过预检，打一行 ASCII WARN。"""
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "missing-legacy"))
    monkeypatch.setenv("ATLAS_SKIP_LEGACY_CHECK", "1")
    config = AtlasConfig(tmp_path)
    rt.check_legacy_required(config, required=True)   # 不抛
    assert "WARN: ATLAS_SKIP_LEGACY_CHECK=1" in capsys.readouterr().err


def test_skip_switch_bad_value_treated_off(tmp_path, monkeypatch):
    """开关坏值按关（_flag_env 单点口径）：预检照常执行，不静默放行。"""
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "missing-legacy"))
    monkeypatch.setenv("ATLAS_SKIP_LEGACY_CHECK", "1 ")   # 带尾空格=坏值
    config = AtlasConfig(tmp_path)
    with pytest.raises(FileNotFoundError, match="旧项目根不存在"):
        rt.check_legacy_required(config, required=True)


class _FakeSession:
    """拿到浏览器打开事件即停：本测只关心预检是否放行到会话层。"""
    entered = 0
    closed = 0

    def __init__(self, config):
        self.page = None

    def __enter__(self):
        type(self).entered += 1
        return self

    def actor(self, name):
        raise RuntimeError("停在这里：预检已通过")

    def __exit__(self, *args):
        type(self).closed += 1


def _prepare_runtime_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "BrowserSession", _FakeSession)
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path))
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "missing-legacy"))
    return AtlasConfig(tmp_path, env="sit")


def test_runtime_enter_skips_legacy_precheck_when_not_required(tmp_path, monkeypatch):
    """DemoRuntime(legacy_required=False)：缺旧仓目录仍进入会话层（不再假红）。"""
    _FakeSession.entered = _FakeSession.closed = 0
    config = _prepare_runtime_inputs(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="停在这里"):
        with rt.DemoRuntime(config, login=False, legacy_required=False):
            pass
    assert _FakeSession.entered == 1 and _FakeSession.closed == 1


def test_runtime_enter_default_still_checks_legacy(tmp_path, monkeypatch):
    """缺省 legacy_required=None = 旧行为：预检先于浏览器启动拒绝（零涟漪）。"""
    _FakeSession.entered = _FakeSession.closed = 0
    config = _prepare_runtime_inputs(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError, match="旧项目根不存在"):
        with rt.DemoRuntime(config, login=False):
            pass
    assert _FakeSession.entered == 0   # fail-fast：浏览器未启动


def test_cli_uses_whitelisted_legacy_env_loader():
    """en-5 CLI 侧封口：cli.py 不再有未过滤的 legacy .env 整份加载。"""
    src = (Path(__file__).resolve().parent.parent / "src" / "atlas" / "cli.py").read_text(
        encoding="utf-8")
    assert "load_dotenv(config.legacy_project)" not in src
    assert "load_dotenv(legacy)" not in src
    # 正向对照：白名单加载器仍在四处预检在位（改回未过滤会同时命中上面的负向断言）。
    assert src.count("load_legacy_dotenv(config.legacy_project)") == 3
    assert src.count("load_legacy_dotenv(legacy)") == 1

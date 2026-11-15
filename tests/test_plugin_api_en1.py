"""en-1：`ATLAS_PLUGIN` 缺失时回落 demo 插件不再沉默。

三条判据：
1. 未设 ATLAS_PLUGIN → stderr 一行 ASCII WARN，**同一进程只打一次**（resolve_plugin
   会被 en-2/en-3 身份解析反复调用，逐次打印会把 stderr 变瀑布）；
2. 显式 `ATLAS_PLUGIN=demo` 是人的决定 → 不 WARN、不拒（与「回落」两义分立）；
3. `ATLAS_REQUIRE_PLUGIN=1`（只认精确 "1"）→ 无 ATLAS_PLUGIN 直接 ValueError 点名；
   坏值（`"1 "` / `"yess"`）按关处理并点名原值 repr（`config._flag_env` 单点）。
"""
from __future__ import annotations

import pytest

from atlas import plugin_api
from atlas.plugin_api import REQUIRE_PLUGIN_ENV_KEY, resolve_plugin


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """WARN 只打一次是进程级旗标——每格测前重置，避免顺序相关。"""
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    monkeypatch.delenv(REQUIRE_PLUGIN_ENV_KEY, raising=False)
    plugin_api._reset_fallback_warn()
    yield
    plugin_api._reset_fallback_warn()


def test_fallback_warns_once(capsys):
    resolve_plugin()
    resolve_plugin()
    err = capsys.readouterr().err
    assert err.count("WARN: plugin=demo (fallback; set ATLAS_PLUGIN=<domain>)") == 1
    # ASCII 前缀可 cp936（X4：终端不因一行 WARN 崩）
    assert "WARN:" in err


def test_explicit_demo_does_not_warn(capsys, monkeypatch):
    monkeypatch.setenv("ATLAS_PLUGIN", "demo")
    assert resolve_plugin().name == "demo"
    assert capsys.readouterr().err == ""


def test_require_plugin_rejects_fallback(capsys, monkeypatch):
    monkeypatch.setenv(REQUIRE_PLUGIN_ENV_KEY, "1")
    with pytest.raises(ValueError, match="ATLAS_REQUIRE_PLUGIN=1"):
        resolve_plugin()
    # 拒绝先于 WARN：既拒就不该再提示「可以设 ATLAS_PLUGIN」
    assert capsys.readouterr().err == ""


def test_require_plugin_ok_when_plugin_set(monkeypatch):
    monkeypatch.setenv(REQUIRE_PLUGIN_ENV_KEY, "1")
    monkeypatch.setenv("ATLAS_PLUGIN", "demo")
    assert resolve_plugin().name == "demo"


@pytest.mark.parametrize("bad", ["1 ", "yess", "true", "0", ""])
def test_require_plugin_bad_values_treated_as_off(bad, capsys, monkeypatch):
    """只认精确 "1"（与 config._flag_env 同一判据）；坏值按关处理并点名 repr，不静默。"""
    monkeypatch.setenv(REQUIRE_PLUGIN_ENV_KEY, bad)
    assert resolve_plugin().name == "demo"          # 不拒
    err = capsys.readouterr().err
    if bad not in ("", "0"):
        assert f"{REQUIRE_PLUGIN_ENV_KEY}={bad!r}" in err and "按关处理" in err

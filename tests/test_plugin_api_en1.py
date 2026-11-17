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


def test_fallback_warning_names_the_concrete_consequence(capsys):
    """回落 WARN 必须点名**后果**，不只是「发生了回落」（2026-11-16 真机教训）。

    真机踩点：真实项目未设 `ATLAS_PLUGIN` → 回落 demo → `session_defaults()` 给出数据源里
    那个虚构账号 + 空密码 → 拿它去 SSO 登录 → 被拒 → 停在登录表单 → 45s 后报
    「未见登录指示器」→ `atlas run` rc 1。

    **排障者看到的是「登录超时」，看不到「你在用 demo 的虚构账号」**——那行 WARN 当时
    被淹没在一堆其他 WARN 里，而且措辞只说 fallback。这条测试钉住：同一行（仍只打一次）
    必须把后果写出来，否则下一个人还会照同样的路踩。
    """
    from atlas.plugin_api import demo_fixture_account
    expected = demo_fixture_account()   # 从数据源取，不在测试里写死业务字面量
    resolve_plugin()
    err = capsys.readouterr().err
    line = next(ln for ln in err.splitlines() if "plugin=demo (fallback" in ln)
    assert expected and expected in line, (
        f"警告必须点名虚构账号的具体值（否则读者不知道后果是什么）：{line!r}")
    assert "登录" in line or "login" in line.lower(), (
        f"警告必须点明「对真实环境会以登录失败收场」这个具体后果：{line!r}")
    assert "ATLAS_PLUGIN" in line, f"警告必须给出补救动作：{line!r}"
    # 仍是一行（不破坏「只打一次、不打瀑布」的初衷）
    assert err.count("plugin=demo (fallback") == 1


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

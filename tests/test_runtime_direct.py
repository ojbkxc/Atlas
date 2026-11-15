"""runtime 纯函数判据直测（盲区补齐轮）：resolve_account 会话账号解析单点。

高层（DemoRuntime.__init__）在 tests/ 无直测（浏览器装配整体被隔离），账号闸门
只测到 require_account 一侧——resolve_account 的解析次序与坏形态回落在此逐分支钉死：
显式 account > 传入 defaults > 插件 session_defaults(env)；非映射 defaults / 空账号
一律回落空串，不抛。消费方口径见 runtime.resolve_account docstring（#92 单一解析点）。
"""

from atlas import runtime
from atlas.config import AtlasConfig


class _StubPlugin:
    """resolve_plugin 替身：记录收到的 env，返回预置会话默认值。"""

    def __init__(self, defaults):
        self.defaults = defaults
        self.env_seen = None

    def session_defaults(self, env=""):
        self.env_seen = env
        return self.defaults


def _config(tmp_path, env="uat-eu"):
    return AtlasConfig(root=tmp_path, env=env)


def _no_plugin(monkeypatch):
    """插件被咨询即炸：用于钉住「显式参数/传入 defaults 短路，不碰插件」。"""
    def _boom():
        raise AssertionError("不该咨询插件 session_defaults（短路次序被破坏）")
    monkeypatch.setattr(runtime, "resolve_plugin", _boom)


def test_explicit_account_short_circuits_before_plugin(tmp_path, monkeypatch):
    _no_plugin(monkeypatch)
    assert runtime.resolve_account(_config(tmp_path), "uat-user") == "uat-user"


def test_explicit_empty_account_falls_through_to_defaults(tmp_path, monkeypatch):
    _no_plugin(monkeypatch)
    assert runtime.resolve_account(_config(tmp_path), "",
                                   defaults={"account": "from-defaults"}) == "from-defaults"
    assert runtime.resolve_account(_config(tmp_path), None,
                                   defaults={"account": "from-defaults"}) == "from-defaults"


def test_defaults_dict_used_without_plugin(tmp_path, monkeypatch):
    _no_plugin(monkeypatch)
    assert runtime.resolve_account(_config(tmp_path), None,
                                   defaults={"account": "from-defaults"}) == "from-defaults"


def test_defaults_without_account_falls_to_empty_string(tmp_path, monkeypatch):
    _no_plugin(monkeypatch)
    assert runtime.resolve_account(_config(tmp_path), None, defaults={}) == ""
    assert runtime.resolve_account(_config(tmp_path), None,
                                   defaults={"account": None}) == ""
    assert runtime.resolve_account(_config(tmp_path), None,
                                   defaults={"account": ""}) == ""


def test_defaults_non_dict_never_crashes(tmp_path, monkeypatch):
    """defaults 形态坏（非映射）：isinstance 守卫回落空串，不裸 AttributeError。"""
    _no_plugin(monkeypatch)
    assert runtime.resolve_account(_config(tmp_path), None, defaults=["坏形态"]) == ""
    assert runtime.resolve_account(_config(tmp_path), None, defaults="坏形态字符串") == ""


def test_defaults_none_consults_plugin_with_env(tmp_path, monkeypatch):
    """defaults=None 时才咨询插件，且 env 必须原样透传（--env uat-eu 带出该环境坐标）。"""
    stub = _StubPlugin({"account": "plugin-acct"})
    monkeypatch.setattr(runtime, "resolve_plugin", lambda: stub)
    out = runtime.resolve_account(_config(tmp_path, env="uat-eu"))
    assert out == "plugin-acct"
    assert stub.env_seen == "uat-eu"


def test_defaults_none_plugin_empty_or_bad_falls_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "resolve_plugin", lambda: _StubPlugin({}))
    assert runtime.resolve_account(_config(tmp_path)) == ""
    # 插件返回非映射（坏形态）同样回落空串，不抛
    monkeypatch.setattr(runtime, "resolve_plugin", lambda: _StubPlugin(None))
    assert runtime.resolve_account(_config(tmp_path)) == ""


def test_explicit_account_wins_over_plugin_defaults(tmp_path, monkeypatch):
    """显式 account 优先于插件默认值：两层同时在场时取显式（命令行覆盖知识库口径）。"""
    stub = _StubPlugin({"account": "plugin-acct"})
    monkeypatch.setattr(runtime, "resolve_plugin", lambda: stub)
    assert runtime.resolve_account(_config(tmp_path), "cli-acct") == "cli-acct"
    assert stub.env_seen is None, "显式账号在场时插件根本不该被咨询"


# ---- en-13：登录协议四入口装配期预检 ----

def test_check_login_protocol_accepts_conforming_module():
    """符合协议的登录模块零涟漪（含「可选入口缺失」的合法单入口平台）。"""
    import types
    from atlas.runtime import check_login_protocol
    mod = types.SimpleNamespace(do_login=lambda *a, **k: None)
    check_login_protocol(mod)                       # 可选三项全缺：合法
    mod2 = types.SimpleNamespace(do_login=lambda *a, **k: None,
                                 switch_to_ou=lambda *a, **k: None,
                                 switch_to_lang=lambda *a, **k: None,
                                 LOCATORS={"login_indicator": "#x"})
    check_login_protocol(mod2)                      # 四项齐：合法


def test_check_login_protocol_names_missing_required_entry():
    """缺必填 do_login：装配期点名文件与入口名，不让 AttributeError 漂到登录链深处。"""
    import types
    import pytest as _pytest
    from atlas.runtime import check_login_protocol
    with _pytest.raises(ValueError, match=r"login\.py.*do_login（缺失，必填）"):
        check_login_protocol(types.SimpleNamespace(), where="legacy-web/lib/login.py")


def test_check_login_protocol_rejects_bad_shapes():
    """存在但不可调用 / LOCATORS 非 dict：点名形态（#63 存在≠可用的同族）。"""
    import types
    import pytest as _pytest
    from atlas.runtime import check_login_protocol
    with _pytest.raises(ValueError, match="do_login（应为可调用函数，实际 str）"):
        check_login_protocol(types.SimpleNamespace(do_login="not-callable"))
    with _pytest.raises(ValueError, match="LOCATORS（形态应为 dict，实际 list）"):
        check_login_protocol(types.SimpleNamespace(do_login=lambda: None,
                                                   LOCATORS=["x"]))


def test_check_login_protocol_optional_entries_may_be_bad_but_callable_checked():
    """可选入口：缺失放行，存在但不可调用仍点名（守卫会调用它，坏形态到调用点才炸）。"""
    import types
    import pytest as _pytest
    from atlas.runtime import check_login_protocol
    with _pytest.raises(ValueError, match="switch_to_ou（应为可调用函数"):
        check_login_protocol(types.SimpleNamespace(do_login=lambda: None,
                                                   switch_to_ou=42))

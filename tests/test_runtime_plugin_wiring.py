"""P2-1/P2-2：runtime 装配按插件取 provider / registry，插件 defaults 缺键点名。

背景（修复前均已实测）：
- `runtime.__enter__` 无条件调 `make_demo_provider`——`ATLAS_PLUGIN=iservice` 时数据供给
  仍是 demo 的 SalesOrder/Customer 探针，`IservicePlugin.make_provider()` 成了只被测试
  断言「存在」的死方法；`registry()` 恒注册 `demo_api`，iservice 路径也看得到 demo 任务。
- `defaults["ou"]` 直取：第三方插件只实现最小形态 `{"account": ...}` 时抛裸
  `KeyError: 'ou'`（消息不含插件名；同模块 `resolve_account` 对非映射 defaults 反而有
  守卫——两套宽严）。

本文件钉住修复后的判据：
① iservice 装配路径的 provider 来自 `plugin.make_provider`（demo 工厂哨兵不被触发）；
② `registry()` 按插件注册（demo 有 `demo_api`；iservice 没有，通用 Task 仍在）；
③ 插件 `session_defaults` 缺键/坏形态 → 明确 RuntimeError 点名插件与键；
④ demo 行为零变化（provider 仍是 SalesOrder+Customer、registry 仍有 `demo_api`）。

装配走替身会话（无浏览器/网络）：`BrowserSession` 换成返回真实 `Actor` 的假会话，
`actor.api` 用只实现 runtime 消费面的假 CallApi（bake/attach_demo/set_keepalive）。
"""
from __future__ import annotations

import types

import pytest

from atlas.abilities import BrowseTheWeb, CallApi
from atlas.actor import Actor
from atlas.config import AtlasConfig


# ── 装配替身：只实现 runtime.__enter__ 的消费面 ───────────────────────────────

class _FakeApi:
    """假 CallApi：runtime 装配只用到 bake_from_playwright / attach_demo / set_keepalive。"""

    def __init__(self):
        self.keepalive = None

    def bake_from_playwright(self, context):
        return None

    def attach_demo(self, root, **kwargs):
        return types.SimpleNamespace(atlas_allowed_operations=set())

    def set_keepalive(self, callback):
        self.keepalive = callback


class _FakeWeb:
    """假 BrowseTheWeb：`actor.bake_session()` 要读 `page.context`。"""

    def __init__(self):
        self.page = types.SimpleNamespace(context=object())


class _FakeSession:
    """假 BrowserSession：actor() 返回带假 API/页面能力的真实 Actor。"""

    def __init__(self, config):
        self.page = None

    def __enter__(self):
        return self

    def actor(self, name):
        actor = Actor(name)
        actor.abilities[CallApi] = _FakeApi()
        actor.abilities[BrowseTheWeb] = _FakeWeb()
        return actor

    def __exit__(self, *exc):
        return False


def _project(tmp_path, monkeypatch):
    """最小可装配项目：DemoApi 预检通过 + sit 环境 URL（不进浏览器、不发请求）。"""
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path))
    return AtlasConfig(tmp_path, env="sit")


def _assemble(config, monkeypatch, **runtime_kwargs):
    """走真实 DemoRuntime.__enter__ 装配（会话层替身），返回进入后的 runtime。"""
    import atlas.runtime as rt
    monkeypatch.setattr(rt, "BrowserSession", _FakeSession)
    runtime = rt.DemoRuntime(config, login=False, legacy_required=False, **runtime_kwargs)
    return runtime.__enter__()


# ── ① iservice 装配路径不碰 demo provider ────────────────────────────────────

def test_iservice_assembly_uses_plugin_provider_not_demo(tmp_path, monkeypatch):
    """ATLAS_PLUGIN=iservice：provider 必须来自 IservicePlugin.make_provider。

    demo 入口哨兵打在两处：demo 探针工厂（`DemoPlugin.make_provider` 的惰性 import 目标）
    与 runtime 模块属性（防「重新直接持有 demo 工厂」的回归形态）。iservice 路径触达任一
    哨兵都说明装配又硬编码回 demo 业务。
    """
    import atlas.plugins.demo.probes as demo_probes
    import atlas.runtime as rt

    monkeypatch.setenv("ATLAS_PLUGIN", "iservice")
    calls = []

    def _demo_provider_boom(*args, **kwargs):
        calls.append("make_demo_provider")
        raise AssertionError("iservice 装配路径不得调用 demo provider")

    monkeypatch.setattr(demo_probes, "make_demo_provider", _demo_provider_boom)
    monkeypatch.setattr(rt, "make_demo_provider", _demo_provider_boom, raising=False)

    config = _project(tmp_path, monkeypatch)
    runtime = _assemble(config, monkeypatch)
    try:
        assert calls == [], "iservice 装配路径调用了 demo provider 工厂"
        # 探针实体是 iservice 自己的（demo 的两个是 SalesOrder / Customer）。
        assert set(runtime.provider.probes) == {"IserviceEntity"}
        # registry 按插件注册：demo_api 不在 iservice 路径；通用 Task 与 iservice Task 在位。
        names = set(runtime.registry().names())
        assert "demo_api" not in names
        assert {"api_call", "ui_action", "assert_api",
                "legacy_step", "legacy_case"} <= names
        assert {"open_doc_list", "click_save", "fill_return_reason"} <= names
    finally:
        runtime.__exit__(None, None, None)


# ── ② registry 按插件注册（demo 路径零变化） ─────────────────────────────────

def test_demo_registry_and_provider_unchanged(tmp_path, monkeypatch):
    """缺省 demo 插件：`demo_api` 照常注册，provider 仍是 SalesOrder+Customer 两个探针。

    这是「demo 行为逐字节不变」的正向对照——按插件注册的改动不得缩 demo 的装配面。
    """
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    config = _project(tmp_path, monkeypatch)

    # registry() 不依赖 __enter__，直接构造实例断言（无需会话替身）。
    names = set(_runtime_class()(config, login=False).registry().names())
    assert "demo_api" in names
    assert {"api_call", "ui_action", "assert_api", "legacy_step", "legacy_case"} <= names

    runtime = _assemble(config, monkeypatch)
    try:
        assert set(runtime.provider.probes) == {"SalesOrder", "Customer"}
    finally:
        runtime.__exit__(None, None, None)


def _runtime_class():
    import atlas.runtime as rt
    return rt.DemoRuntime


# ── ③ 插件 defaults 缺键/坏形态：点名插件与键 ────────────────────────────────

class _AccountOnlyPlugin:
    """第三方插件最小形态：只返回 account（config_add 骨架之外的野生插件）。"""

    name = "thirdparty"
    allowed_api_operations = frozenset()

    def session_defaults(self, env: str = ""):
        return {"account": "acct-third"}


class _BadShapePlugin:
    name = "badshape"
    allowed_api_operations = frozenset()

    def session_defaults(self, env: str = ""):
        return ["account"]   # 合法返回值形态承诺是映射；列表是坏形态


def test_plugin_defaults_missing_key_names_plugin_and_key(tmp_path, monkeypatch):
    """第三方插件 {"account": ...}：明确 RuntimeError 点名插件与缺键，不是裸 KeyError。"""
    import atlas.runtime as rt
    monkeypatch.setattr(rt, "resolve_plugin", lambda: _AccountOnlyPlugin())
    with pytest.raises(RuntimeError, match=r"插件 thirdparty 的 session_defaults 缺少 'ou'"):
        rt.DemoRuntime(AtlasConfig(tmp_path), login=False)


def test_plugin_defaults_non_mapping_names_plugin_and_shape(tmp_path, monkeypatch):
    """defaults 非映射（list/None/str）：同样点名插件与形态，不裸 TypeError/AttributeError。"""
    import atlas.runtime as rt
    monkeypatch.setattr(rt, "resolve_plugin", lambda: _BadShapePlugin())
    with pytest.raises(RuntimeError,
                       match=r"插件 badshape 的 session_defaults 返回 list 而非映射"):
        rt.DemoRuntime(AtlasConfig(tmp_path), login=False)


def test_explicit_coordinates_do_not_require_plugin_keys(tmp_path, monkeypatch):
    """显式 ou/division_code/language 时不向插件索取这三个键（#115：不翻正常路径）。

    缺键守卫只在**真正要回落插件默认值**的键上触发；account 仍可从最小 defaults 解析。
    """
    import atlas.runtime as rt
    monkeypatch.setattr(rt, "resolve_plugin", lambda: _AccountOnlyPlugin())
    runtime = rt.DemoRuntime(AtlasConfig(tmp_path), login=False,
                             ou="OU-X", division_code="DIV-X", language="English US")
    assert runtime.account == "acct-third"
    assert (runtime.ou, runtime.division_code, runtime.language) == (
        "OU-X", "DIV-X", "English US")


def test_demo_session_defaults_keys_all_present(tmp_path, monkeypatch):
    """demo 包内 session.json 四键齐——构造路径不受新守卫影响（存量测试全绿的结构前提）。"""
    import atlas.runtime as rt
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    runtime = rt.DemoRuntime(AtlasConfig(tmp_path), login=False)
    assert runtime.ou == "DEMO System Admin"
    assert runtime.division_code == "DIV-DEMO-0001"
    assert runtime.language == "English US"


# ── ④ 预检侧 ≡ 执行侧（P2-2 同源锁步） ───────────────────────────────────────

def test_cli_full_registry_equals_runtime_registry(monkeypatch):
    """`cli._full_registry()`（compile/export-pytest/schedule 的 validate_flow）必须与
    `runtime.build_registry()`（真实执行 registry）**同一批名字**。

    两处各写一份时的真实裂缝：P2-2 把 `demo_api` 改成按插件注册后，预检侧仍无条件注册，
    iservice 下含 `demo_api` 的 flow 过得了 `atlas compile`、执行期才撞「未注册的 Task」
    ——「compile 绿 ⇒ run 绿」这条承诺当场失效。本测双向断言（既防预检多注册、也防
    执行侧多注册），是对称锁步而非单侧快照。
    """
    from atlas.cli import _full_registry
    from atlas.runtime import build_registry

    for plugin in ("demo", "iservice"):
        monkeypatch.setenv("ATLAS_PLUGIN", plugin)
        preflight = set(_full_registry().names())
        execution = set(build_registry().names())
        assert preflight == execution, (
            f"ATLAS_PLUGIN={plugin}：预检 registry 与执行 registry 不同源；"
            f"仅预检有={sorted(preflight - execution)}，仅执行有={sorted(execution - preflight)}")


def test_iservice_preflight_rejects_demo_api_flow(monkeypatch, tmp_path):
    """iservice 下含 `demo_api` 步骤的 flow 必须在 **compile 期**就被拒（不是执行期才炸）。

    这是上一测的行为面证据：只断言名字集相同不够，还要证明差异真的体现在用户可见的
    `atlas compile` 结果上（判据在位没人消费 = #106 同族）。
    """
    import yaml
    from atlas.cli import main

    monkeypatch.setenv("ATLAS_PLUGIN", "iservice")
    flow_path = tmp_path / "uses_demo_api.yaml"
    flow_path.write_text(yaml.safe_dump({
        "steps": [{"task": "demo_api", "operation": "anything"}]}, allow_unicode=True),
        encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(flow_path)]) == 2

"""generator/emit_pytest（Gap7 回归代码产出 + Gap9 快模式）测试。

离线、零网络、零执行。用 ast.parse/compile 校验生成产物的语法与关键结构；
对「自包含底座」额外做一次真实 import 验证占位符解析行为（底座仅标准库，执行安全）。
快模式（gap9）判据：fast=False 逐字节快照（E.1.2 零涟漪）＋ fast=True 结构/禁词断言。
lead 裁决 F-1/R5：起服务/子进程真请求的测**不进本文件**（localhost socket 也算网络）。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from atlas.generator.model import Endpoint, BodySpec, ResponseSpec, Param
from atlas.generator.emit_pytest import (
    PAGES_MODULE_NAME, SUPPORT_MODULE_NAME, PytestEmitError,
    emit_pages_module, emit_pytest_module, emit_pytest_from_flow,
    emit_pytest_package, emit_support_module, render_test_module,
    _specs_from_endpoints, _specs_from_flow, StepSpec, AssertSpec, _build_readme,
)


# ---- 取样模型（不依赖解析层）----

def _read_endpoint() -> Endpoint:
    return Endpoint(
        method="GET", path="/api/order/list", summary="查询订单列表",
        params=[Param(where="query", name="divisionCode", example="DIV001")],
        response=ResponseSpec(is_object=True, has_result_code=True,
                              sample={"resultCode": "BUG-000"}),
    )


def _write_endpoint() -> Endpoint:
    return Endpoint(
        method="POST", path="/api/order/save", summary="保存订单",
        params=[Param(where="header", name="isc-app", example="${ISC_APP}")],
        body=BodySpec(kind="json", example_obj={"a": 1}),
        response=ResponseSpec(is_object=True, has_result_code=True,
                              sample={"resultCode": "SPM-612"}),
    )


def _array_endpoint() -> Endpoint:
    return Endpoint(method="GET", path="/api/list", summary="列表",
                    response=ResponseSpec(is_array=True, sample=[{"a": 1}]))


# ---- 语法正确性：所有生成模块必须 compile 通过 ----


def _assert_compiles(source: str):
    ast.parse(source)          # 语法树
    compile(source, "<gen>", "exec")   # 可编译


def test_endpoint_module_compiles():
    src = emit_pytest_module([_read_endpoint(), _write_endpoint()])
    _assert_compiles(src)
    assert "def test_01_" in src and "def test_02_" in src


def test_flow_module_compiles():
    flow = {"id": "order-api", "steps": [
        {"task": "api_call", "id": "step_1", "method": "GET", "path": "/api/a",
         "save_as": "resp_1_a", "headers": {"isc-app": "${ISC_APP}"},
         "intent": "查询A"},
    ], "assert": ["resp_1_a.ok == true"]}
    src = emit_pytest_from_flow(flow)
    _assert_compiles(src)
    assert "def test_01_" in src


def test_support_and_pages_compile():
    _assert_compiles(emit_support_module())
    _assert_compiles(emit_pages_module({"components": {"x": {"selectors": {"a": ".css"}}}}))


# ---- 结构断言：断言不放宽（#7），依赖不新增（#4）----


def test_read_assert_only_documented():
    src = emit_pytest_module([_read_endpoint()])
    # 存活断言存在
    assert "assert result.ok" in src
    # 文档给了 resultCode → 生成等值断言（非放宽）
    assert 'result.data.get("resultCode") ==' in src
    assert "BUG-000" in src


def test_array_response_no_fabricated_index_assert():
    src = emit_pytest_module([_array_endpoint()])
    # 不伪造下标断言；列 TODO 供人审
    assert "TODO(人审)" in src
    assert ".data.0." not in src
    # 只有存活断言（不把数组断言硬编码成恒真）
    assert src.count("assert result.ok") == 1


def test_write_step_marked():
    src = emit_pytest_module([_write_endpoint()])
    assert "[写操作-需人工确认]" in src


def test_no_new_runtime_deps_in_generated_module():
    for src in (emit_pytest_module([_read_endpoint()]),
                emit_support_module(), emit_pages_module({"components": {}})):
        assert "import allure" not in src
        assert "import requests" not in src
        assert "import playwright" not in src
        assert "from playwright" not in src


def test_no_atlas_runtime_import_in_generated_module():
    # 生成的用例与底座不得 import Atlas 运行时（可脱离 Atlas 运行）
    for src in (emit_pytest_module([_read_endpoint()]), emit_support_module()):
        assert "import atlas" not in src
        assert "from atlas" not in src


def test_placeholder_preserved_not_inlined():
    src = emit_pytest_module([_read_endpoint()], global_headers={"isc-app": "${ISC_APP}"})
    assert "${ISC_APP}" in src  # 占位符原样保留，待人审接 env


def test_base_url_from_env():
    src = emit_pytest_module([_read_endpoint()], base_url="https://api.example.com")
    assert 'os.environ.get("ATLAS_BASE_URL"' in src
    assert "https://api.example.com" in src


# ---- UI step：骨架以 skip 占位，不假装通过 ----


def test_ui_step_skip_skeleton():
    flow = {"id": "ui", "steps": [
        {"task": "open_doc_list", "id": "step_1", "intent": "打开列表",
         "read_only": True},
    ]}
    src = emit_pytest_from_flow(flow)
    _assert_compiles(src)
    assert "import pytest" in src
    assert "pytest.mark.skip" in src
    assert "NotImplementedError" in src


# ---- 页对象库：从 components.json 派生 ----


def _demo_components() -> dict:
    path = (Path(__file__).resolve().parents[1]
            / "src" / "atlas" / "plugins" / "demo" / "knowledge" / "components.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_pages_module_derived_from_components():
    comps = _demo_components()
    src = emit_pages_module(comps)
    _assert_compiles(src)
    # 组件 → 类；选择器 → 常量（复用 Atlas 实测选择器）
    assert "class CustomerSelection" in src
    assert "class PaymentTerms" in src
    sel = comps["components"]["customer_selection"]["selectors"]
    assert repr(sel["dialog"]) in src  # 选择器值原样复用
    # 操作骨架明确失败，不假装通过
    assert "NotImplementedError" in src


def test_pages_module_handles_bare_and_bad_shape():
    # 裸 {"x": {...}} 与坏形态都不裸崩
    assert "class X" in emit_pages_module({"x": {"selectors": {"a": ".css"}}})
    assert emit_pages_module(None).strip()  # 空库仍产出合法模块
    _assert_compiles(emit_pages_module({"components": {"bad": "not-a-map"}}))


# ---- 自包含底座：占位符解析行为（真 import，仅标准库）----


def _load_support(tmp_path: Path):
    p = tmp_path / "_gen_support.py"
    p.write_text(emit_support_module(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_gen_support_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_support_resolves_and_raises(tmp_path):
    mod = _load_support(tmp_path)
    assert mod.resolve_placeholders("${X}", {"X": "1"}) == "1"
    assert mod.resolve_placeholders({"a": "${X}", "b": [1, "${Y}"]},
                                    {"X": "1", "Y": "2"}) == {"a": "1", "b": [1, "2"]}
    # 未解析占位符显式报错，绝不静默当字面量发出
    with pytest.raises(KeyError):
        mod.resolve_placeholders("${MISSING}", {})


def test_support_body_split(tmp_path):
    mod = _load_support(tmp_path)
    # body 分流：dict/list → json=；str → data=（同 Atlas DSL 口径）
    assert isinstance(mod.StepResult(True, 200, {}, ""), mod.StepResult)
    # 不支持的 body 类型：发送前点名拒绝（不静默）。base_url 用绝对地址——#17 后缺配置
    # 在 URL 判据处先抛 EnvBlocked（本测只验 body 分流，故给合法 base_url）。
    with pytest.raises(TypeError):
        mod.call_api(method="POST", path="/x", base_url="https://h", body=object())


def test_support_call_api_missing_base_url_is_env_blocked(tmp_path):
    """#17 T9-O2 判据（唯一落点=_SUPPORT_SOURCE::call_api 入口）：缺配置→EnvBlocked 点名键，
    绝不漏 urllib 裸 `ValueError: unknown url type`（归因漂移：操作者把「少配一个键」误读成产物坏了）。"""
    mod = _load_support(tmp_path)
    for bad in ("", None, "   ", "/relative/x", "web/api", "ftp://h"):
        with pytest.raises(mod.EnvBlocked) as ei:
            mod.call_api(method="GET", path="/web/api/x", base_url=bad)
        msg = str(ei.value)
        assert "ATLAS_BASE_URL" in msg and "缺配置" in msg   # 点名缺哪个 env 键 + 家族措辞
        assert "unknown url type" not in msg


def test_support_call_api_with_base_url_reaches_transport(tmp_path, monkeypatch):
    """正向对照：给了绝对 http(s) base_url → 不进 EnvBlocked 分支，真走到 urllib（假 transport，不碰网络）。"""
    mod = _load_support(tmp_path)
    import urllib.request as _u

    class _Resp:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = []

    def _fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(_u, "urlopen", _fake_urlopen)
    result = mod.call_api(method="GET", path="/p", base_url="https://host")
    assert result.ok is True and result.status == 200 and result.data == {"ok": True}
    assert calls == ["https://host/p"]                       # urljoin 语义不变（相对 path 拼接照旧）


def test_support_envblocked_identity_with_atlas_fast(tmp_path, monkeypatch):
    """#17 复核（lead 点名）：快包并存时 call_api 的 EnvBlocked 必须是 `_atlas_fast.EnvBlocked`
    **同一类对象**（否则 fast runlog/conftest 的 isinstance 认不出 env_blocked）；缺席时自备本地类。"""
    import types as _types
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    sys.modules.pop("_atlas_fast", None)
    local = _load_support(local_dir)                         # 无 _atlas_fast → 本地类

    class _FakeEnvBlocked(RuntimeError):
        pass

    fake = _types.ModuleType("_atlas_fast")
    fake.EnvBlocked = _FakeEnvBlocked
    monkeypatch.setitem(sys.modules, "_atlas_fast", fake)    # 注入即用即弃（teardown 自动撤销）
    fast_dir = tmp_path / "fast"
    fast_dir.mkdir()
    fastform = _load_support(fast_dir)                       # from _atlas_fast import EnvBlocked → fake
    assert fastform.EnvBlocked is fake.EnvBlocked            # 同一类对象（快形态）
    assert local.EnvBlocked is not fake.EnvBlocked           # 本地类不同（慢形态自备，两形态不混用）


# ---- 文件包产物 ----


def test_package_writes_files(tmp_path):
    out = tmp_path / "exports" / "order_pytest"
    written = emit_pytest_package(out_dir=out, endpoints=[_read_endpoint(), _write_endpoint()],
                                  flow_id="order-api", components={"components": {}})
    names = {Path(p).name for p in written}
    assert "test_order_api.py" in names
    assert f"{SUPPORT_MODULE_NAME}.py" in names
    assert "README.md" in names
    # 每个 .py 产物都语法正确
    for p in written:
        if p.endswith(".py"):
            _assert_compiles(Path(p).read_text(encoding="utf-8"))
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "只生成不执行" in readme


def test_package_emits_pages_when_components_present(tmp_path):
    out = tmp_path / "exports" / "p"
    written = emit_pytest_package(out_dir=out, endpoints=[_read_endpoint()],
                                  components=_demo_components())
    names = {Path(p).name for p in written}
    assert f"{PAGES_MODULE_NAME}.py" in names


def test_package_refuses_nonempty_without_force(tmp_path):
    out = tmp_path / "p"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(PytestEmitError):
        emit_pytest_package(out_dir=out, endpoints=[_read_endpoint()],
                            components={"components": {}})
    # force=True 放行
    emit_pytest_package(out_dir=out, endpoints=[_read_endpoint()],
                        components={"components": {}}, force=True)


def test_package_requires_input(tmp_path):
    with pytest.raises(PytestEmitError):
        emit_pytest_package(out_dir=tmp_path / "p")


def test_generation_deterministic():
    eps = [_read_endpoint(), _write_endpoint()]
    assert emit_pytest_module(eps) == emit_pytest_module(eps)
    comps = _demo_components()
    assert emit_pages_module(comps) == emit_pages_module(comps)


def test_package_from_flow(tmp_path):
    flow = {"id": "ui-reg", "steps": [
        {"task": "api_call", "id": "s1", "method": "GET", "path": "/api/a",
         "save_as": "resp_1_a", "intent": "查询"},
    ], "assert": []}
    out = tmp_path / "p"
    written = emit_pytest_package(out_dir=out, flow=flow, components={"components": {}})
    assert any(Path(p).name == "test_ui_reg.py" for p in written)


# ---- 内部渲染函数直测 ----


def test_render_test_module_missing_case():
    specs = [StepSpec(index=1, slug="op", intent="x", task="api_call",
                      method="GET", path="/a", save_as="resp_1_a",
                      asserts=[AssertSpec("ok")], is_api=True)]
    src = render_test_module(specs, flow_id="f", base_url="")
    _assert_compiles(src)
    assert "def test_01_op()" in src


# ===========================================================================
# Gap9 快模式（fast=True）：零涟漪快照 + 真渲染结构判据 + F-9 闸门
# 离线判据 only（lead F-1/R5）：本文件不起服务、不起子进程真请求。
# ===========================================================================


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# E.1.2 零涟漪快照：fast=False 输出与快模式改造前**逐字节**一致的基线。
# 动 fast=False 渲染形态必须同步改此表且经 lead 签字（#115）；fast 分支改动不得触碰这些哈希。
# 2026-11-11 T9-O2（lead 签字，理由：错误路径点名终态；正常路径零涟漪——仅多一次 startswith 判断）：
# `support` 哈希更新，diff 仅三处——①docstring 加一行；②模块级 try-import `_atlas_fast.EnvBlocked`；
# ③`call_api` 入口加一次 `startswith(("http://","https://"))` 判断。正常路径（base_url 非空 http(s)）
# 逐字节行为不变；其余 11 项哈希未动（本改动与 fast 分支、其它渲染面无涉）。
# API 腿模块哈希项（lead 裁定 1 点名）：ep_single / ep_multi / flow_api_only / flow_mixed /
# render_spec_direct / render_empty —— 覆盖 render_test_module 与 _render_api_test 的 fast=False
# 缺省透传路径（headers 有/无、body 有/无、note 断言、混合 flow 内 API 腿均在列）。
_FAST_OFF_BASELINE = {
    "support": "1fed045a64cdc83e13ad88aeb38f869aff6dba139b6a99380095d3a4d0481d98",
    "ep_single": "4be2db1dca53e163704700f77393f595e6f727e93393a3272e5593c04ac6672e",
    "ep_multi": "9a1f4aa69c8ce7587070aa2cc4f9316c8a672a0f96879859ef1206d233752582",
    "flow_mixed": "94bd0d1e432ce01ede894090f85d78f49a3248ad263a7f31bec2b31e224a7675",
    "flow_api_only": "a5b0108f43213d42b39e988af52753168a1d4dc555a2b7c7b9beb6a4060f6673",
    "render_empty": "5a619e41b93539ce6816a6c7e4b7956896748d72d5f8330ea4641b1546e8440d",
    "render_spec_direct": "55d15ec1285d64b66e3aff53ff62dcbe690c2e8690cd6293965c3e1719585f31",
    "pages_full": "a727ec05c13c95c7d535117e43d0dc5eb7b3a09d555d2dd1a3308533e42d233f",
    "pages_none": "9849353d27ad36e4c0166fab5000116d75712d900cd8edee8c5206b38ad6e98a",
    "pages_bad": "9849353d27ad36e4c0166fab5000116d75712d900cd8edee8c5206b38ad6e98a",
    "readme_pages": "465e65dbe00377386b178a122c1f93e65183002bef5de604541b84958ed49f80",
    "readme_nopages": "edc672a5a09068543eb3192554603015ccf67a7d1d472a23c523fec3597d7172",
}


def _baseline_inputs() -> dict:
    """快照输入：不读包内 components.json（那是对队友在途编辑面的耦合），用固定夹具。"""
    comps_fixed = {"components": {
        "customer_selection": {"notes": "客户选择弹窗",
                               "selectors": {"dialog": ".dlg", "confirm": ".ok"},
                               "models": {"vm": "dialog"}},
        "payment_terms": {"selectors": {"table": ".el-table"}}}}
    flow_mixed = {"id": "mixed-flow", "steps": [
        {"task": "api_call", "id": "s1", "method": "GET", "path": "/api/a",
         "save_as": "resp_1_a", "headers": {"isc-app": "${ISC_APP}"}, "intent": "查询A"},
        {"task": "open_doc_list", "id": "s2", "intent": "打开列表", "read_only": True},
        {"task": "api_call", "id": "s3", "method": "POST", "path": "/api/save",
         "save_as": "resp_3_s", "body": {"x": "y"}, "read_only": False, "intent": "保存"},
    ], "assert": ["resp_1_a.ok == true", 'resp_1_a.data.resultCode == "BUG-000"',
                  "resp_3_s.data.total > 0"]}
    return {
        "support": emit_support_module(),
        "ep_single": emit_pytest_module([_read_endpoint()]),
        "ep_multi": emit_pytest_module(
            [_read_endpoint(), _write_endpoint(), _array_endpoint()],
            base_url="https://api.example.com", global_headers={"isc-app": "${ISC_APP}"}),
        "flow_mixed": emit_pytest_from_flow(flow_mixed, base_url="https://x"),
        "flow_api_only": emit_pytest_from_flow({"id": "api-only", "steps": [
            {"task": "api_call", "method": "GET", "path": "/p", "save_as": "r"}]}),
        "render_empty": render_test_module([], flow_id="e", base_url=""),
        "render_spec_direct": render_test_module(
            [StepSpec(1, "op", "x", "api_call", method="GET", path="/a", save_as="r",
                      asserts=[AssertSpec("ok")], is_api=True)], flow_id="f", base_url="u"),
        "pages_full": emit_pages_module(comps_fixed),
        "pages_none": emit_pages_module(None),
        "pages_bad": emit_pages_module({"components": {"bad": "not-a-map"}}),
        "readme_pages": _build_readme("fid", [StepSpec(1, "a", "i", "api_call",
                                                       is_api=True)], True),
        "readme_nopages": _build_readme("", [StepSpec(1, "a", "i", "t", is_api=False)], False),
    }


def test_fast_off_snapshot_byte_identical():
    """E.1.2 硬约束：不加 fast 时全部渲染输出逐字节=改造前（正向+负向同表钉死）。"""
    got = {k: _sha(v) for k, v in _baseline_inputs().items()}
    drift = [k for k in _FAST_OFF_BASELINE if got.get(k) != _FAST_OFF_BASELINE[k]]
    assert not drift, f"fast=False 输出漂移（违 #115 零涟漪）：{drift}"
    assert set(got) == set(_FAST_OFF_BASELINE)  # 快照面本身不许悄悄缩


def test_fast_off_skip_skeleton_still_present():
    """对照项：fast=False 的 UI 腿**仍**是 skip+NotImplementedError（快照含它，别顺手统一）。"""
    src = emit_pytest_from_flow({"id": "u", "steps": [
        {"task": "open_doc_list", "id": "s1", "intent": "打开列表"}]})
    assert "pytest.mark.skip" in src and "raise NotImplementedError" in src
    assert "NotImplementedError" in emit_pages_module({"components": {"x": {}}})


# ---- StepSpec.requires 与 flow 解析 ----


def test_step_spec_requires_default_empty():
    s = StepSpec(1, "a", "i", "api_call")
    assert s.requires == ()  # 存量形态零涟漪（#115）


def test_step_spec_declared_keys_default_empty():
    """gap9b B-2：两独立字段默认空串=未声明；不共值域（两义合一单键=按文本猜，违 F-2）。"""
    s = StepSpec(1, "a", "i", "open_doc_list", is_api=False)
    assert s.page_object == "" and s.legacy_pages == ""
    # 位置构造（存量调用形态）不受新字段影响：新字段在尾部带默认
    s2 = StepSpec(2, "b", "i2", "api_call", "GET", "/x")
    assert s2.page_object == "" and s2.legacy_pages == "" and s2.requires == ()


def test_declared_keys_zero_ripple_slow_shape():
    """声明位两键对 fast=False 零涟漪：同 flow 带/不带声明键的旧形态输出**逐字节相等**
    （StepSpec 注释点名的守场测之一；另一面是 _FAST_OFF_BASELINE 12 项哈希不变）。"""
    f_bare = {"id": "z", "steps": [
        {"task": "api_call", "method": "GET", "path": "/a", "save_as": "ra"},
        {"task": "open_doc_list", "intent": "打开"}]}
    f_decl = {"id": "z", "steps": [
        {"task": "api_call", "method": "GET", "path": "/a", "save_as": "ra",
         "page_object": "ghost_widget"},
        {"task": "open_doc_list", "intent": "打开", "page_object": "open_doc_list"}]}
    assert emit_pytest_from_flow(f_bare) == emit_pytest_from_flow(f_decl)


def test_specs_from_flow_carries_declared_keys_transport_only():
    """`_specs_from_flow` 只搬运：dict 原样带上不当场炸（形态第一道在 compile 静态段，
    生成期最后防线 _fast_ui_gates/_ui_leg 才点名——两层各守各位，#92 不复制判据）。"""
    specs = _specs_from_flow({"id": "z", "steps": [
        {"task": "pick", "intent": "选", "page_object": {"nested": 1}},
        {"task": "cancel", "intent": "撤", "legacy_pages": "demo_legacy_fn"},
        {"task": "plain", "intent": "普"}]})
    assert specs[0].page_object == {"nested": 1}  # 原样，不折叠不 str 化
    assert specs[1].legacy_pages == "demo_legacy_fn"
    assert specs[2].page_object == "" and specs[2].legacy_pages == ""


def test_flow_requires_parsed_to_tuple():
    specs = _specs_from_flow({"id": "x", "steps": [
        {"task": "api_call", "method": "GET", "path": "/a", "requires": ["k1", "k2"]},
        {"task": "api_call", "method": "GET", "path": "/b"}]})
    assert specs[0].requires == ("k1", "k2")
    assert specs[1].requires == ()  # 缺键=None→()


@pytest.mark.parametrize("bad", ["abc", {"k": 1}, 5, ["ok", ""], ["ok", None], ["ok", {"n": 1}]],
                         ids=["str", "dict", "int", "empty-item", "null-item", "map-item"])
def test_flow_requires_bad_shape_rejected(bad):
    """坏形态点名步骤序号拒绝，与 validate_flow 同口径（不静默、半收）。"""
    with pytest.raises(PytestEmitError) as ei:
        _specs_from_flow({"id": "x", "steps": [
            {"task": "api_call", "method": "GET", "path": "/a"},
            {"task": "api_call", "method": "GET", "path": "/b", "requires": bad}]})
    assert "步骤 2" in str(ei.value)


# ---- 快模式渲染结构（契约 C.2）----


def _stubbed_emit_fast():
    """emit_fast 契约模块：真文件在位用真的；并行开发窗口（文件不存在）注入契约值桩。

    桩只在 `src/atlas/generator/emit_fast.py` **不存在**时注入——文件在但 import 失败
    必须原样炸（坏模块不得被桩掩盖成好，#102 同族假绿防线）。
    """
    import atlas.generator as g
    real = Path(g.__file__).parent / "emit_fast.py"
    if real.exists():
        import atlas.generator.emit_fast as m
        return m
    m = sys.modules.get("atlas.generator.emit_fast")
    if m is not None:
        return m
    m = types.ModuleType("atlas.generator.emit_fast")
    m.FAST_SUPPORT_MODULE_NAME = "_atlas_fast"  # 契约 C.1 钉死值

    class FastEmitError(ValueError):
        pass

    def write_fast_artifacts(out_dir, *, force=False):
        base = Path(out_dir) / "_atlas_fast.py"
        base.write_text("# 桩底座（契约 C.1 占位，仅测试可见）\n", encoding="utf-8")
        conf = Path(out_dir) / "conftest.py"
        conf.write_text("# 桩 conftest\n", encoding="utf-8")
        return [str(base), str(conf)]

    m.FastEmitError = FastEmitError
    m.write_fast_artifacts = write_fast_artifacts
    sys.modules["atlas.generator.emit_fast"] = m
    return m


_FAST_FLOW = {"id": "fast-flow", "steps": [
    {"task": "api_call", "id": "s1", "method": "GET", "path": "/api/a", "save_as": "resp_1_a",
     "requires": ["pre_no"], "headers": {"isc-app": "${ISC_APP}"}, "body": None,
     "intent": "查询A"},
    {"task": "open_doc_list", "id": "s2", "intent": "打开列表", "read_only": True,
     "requires": ["resp_1_a"], "page_object": "open_doc_list"},
    {"task": "save_step", "id": "s3", "intent": "保存", "read_only": False,
     "page_object": "open_doc_list"},
    {"task": "cancel_via_legacy", "id": "s4", "intent": "委托旧函数",
     "legacy_pages": "demo_legacy_fn"},
], "assert": ["resp_1_a.ok == true", 'resp_1_a.data.resultCode == "BUG-000"',
              "resp_1_a.data.total > 3"]}

_FAST_COMPS = {"components": {"open_doc_list": {"selectors": {"table": ".el-table"}},
                              "customer_selection": {"selectors": {"dialog": ".dlg"}}}}


def test_fast_render_structure_contract():
    """fast=True 渲染体形态=C.2 钉死：fast_case 装饰、fixture 签名、require_ctx 前置、
    plan_ctx 产出写回、runlog step（assert 步名以 "assert " 开头）——且非 skip 非裸 NotImplementedError。"""
    _stubbed_emit_fast()
    src = emit_pytest_from_flow(_FAST_FLOW, base_url="https://api.demo", fast=True)
    _assert_compiles(src)
    assert "from _atlas_fast import fast_case, require_ctx" in src
    assert f"from {SUPPORT_MODULE_NAME} import call_api" in src
    # API 腿
    assert '@fast_case(op="api", intent=\'查询A\')' in src
    assert "def test_01_s1(plan_ctx, _fast_runlog):" in src
    assert "require_ctx(plan_ctx, 'pre_no')" in src
    assert "with _fast_runlog.step('GET /api/a'):" in src
    assert 'with _fast_runlog.step("assert ok"):' in src      # assert 步名前缀口径
    assert 'with _fast_runlog.step("assert resultCode"):' in src
    assert "plan_ctx['resp_1_a'] = result.data" in src          # F5 产出写 ctx
    # UI 腿（真渲染：get_page 单浏览器 + 声明位 page_object → atlas_pages 类，非猜 Task 名）
    assert '@fast_case(op="ui"' in src
    assert "def test_02_s2(get_page, plan_ctx, _fast_runlog):" in src
    assert "require_ctx(plan_ctx, 'resp_1_a')" in src
    assert "page = get_page()" in src
    assert f"import {PAGES_MODULE_NAME}" in src
    assert f"{PAGES_MODULE_NAME}.OpenDocList(page).action()" in src
    # legacy 委托腿（B-2：值语义=函数名 only，产 legacy_page_call 字面量；无类引用）
    assert f"{PAGES_MODULE_NAME}.legacy_page_call('demo_legacy_fn', page)" in src
    s4 = src[src.index("def test_04_s4"):]
    assert "OpenDocList(" not in s4 and ".action()" not in s4  # 两腿各走各位，不混形态
    # 写腿（F8：op="write"，由 conftest 写闸拦停）
    assert '@fast_case(op="write"' in src
    # 断言不放宽：note 仍留 TODO（#7）
    assert "# TODO(人审):" in src


def test_fast_module_imports_minimal():
    """无 requires 的模块不 import require_ctx（不留未用 import——产物也过 review-code 门禁）。"""
    _stubbed_emit_fast()
    src = emit_pytest_from_flow({"id": "a", "steps": [
        {"task": "api_call", "method": "GET", "path": "/p", "save_as": "r"}]}, fast=True)
    _assert_compiles(src)
    assert "from _atlas_fast import fast_case\n" in src
    assert "require_ctx" not in src


def test_fast_artifacts_forbidden_words():
    """E.1.3 本层判据：test 模块与 atlas_pages（fast 版）零睡死/裸跳；
    fast 页对象与用例**不含** raise NotImplementedError（F-2：未接线归 NotWired）。"""
    _stubbed_emit_fast()
    src = emit_pytest_from_flow(_FAST_FLOW, base_url="", fast=True)
    pages = emit_pages_module(_FAST_COMPS, fast=True)
    for text, name in ((src, "test module"), (pages, "atlas_pages")):
        assert "wait_for_timeout" not in text, name
        assert "time.sleep(" not in text, name
        assert "pytest.skip(" not in text, name
        assert "raise NotImplementedError" not in text, name
    assert "pytest.mark.skip" not in src
    # 对照（防恒真）：同一判据对 fast=False 输入不成立——旧形态确实含 skip/NotImplementedError
    old_src = emit_pytest_from_flow(_FAST_FLOW, base_url="")
    assert "pytest.mark.skip" in old_src and "raise NotImplementedError" in old_src


def test_fast_pages_two_honest_legs_and_login():
    """C.2/F-7：fast 版 atlas_pages 出 login 骨架（先查 env→EnvBlocked，再判接线→NotWired，
    次序固定、无 or 文案）+ 诚实腿二 legacy_page_call（位来自 env，零业务字面量）。"""
    _stubbed_emit_fast()
    src = emit_pages_module(_FAST_COMPS, fast=True)
    _assert_compiles(src)
    assert "def login(page, *, base_url, account, password):" in src
    assert "def legacy_page_call(func_name, *args, **kwargs):" in src
    assert 'from _atlas_fast import EnvBlocked, NotWired' in src
    assert 'LEGACY_WEB_ENV_KEY = "ATLAS_LEGACY_WEB"' in src  # 调用位来自配置，不硬编码路径
    assert "raise NotWired(" in src
    assert "未接线：OpenDocList.action" in src  # 消息点名类名+元素（F-2）
    # F-7 两义分立：login 里 EnvBlocked（缺配置）检查在 NotWired（未接线）之前
    body = src[src.index("def login("):src.index("__all__")]
    assert body.index("EnvBlocked(") < body.index("NotWired(")
    assert "缺配置：" in body and "登录未接线：" in body
    assert "未接线或未配置" not in src  # 禁 or 混合文案（F-7）


def test_fast_login_message_drops_base_url_and_zeroes_frame_locals():
    """红线②（L-HOST）静态面：fast 版 login 骨架的「登录未接线」消息**不再拼 base_url**——
    真内网域名会经 framework_reason 落 runs.jsonl 的 run 级 reason；同时（L-PW）三个 raise
    分支都包在 try/finally 内，finally 把 password/base_url 归零。"""
    _stubbed_emit_fast()
    src = emit_pages_module(_FAST_COMPS, fast=True)
    body = src[src.index("def login("):src.index("__all__")]
    # L-HOST：消息末尾到「后补真操作）」为止，无 base_url 拼接、无「；入口 」分隔
    assert "登录未接线：atlas_pages.login（登录表单/提交按钮元素须人审接入组件库后补真操作）" in src
    _msg_line = next(l for l in body.splitlines() if "登录未接线" in l)
    assert "base_url" not in _msg_line and "repr(" not in _msg_line and "入口" not in _msg_line
    assert "repr(str(base_url))" not in body
    assert "；入口 " not in body
    # L-PW：三处 raise 全在 try 内，finally 归零
    assert "try:" in body and "finally:" in body
    assert body.index("finally:") > body.index("NotWired(")   # raise 都在 finally 之前
    assert body.count("EnvBlocked(") == 2 and body.count("NotWired(") == 1
    assert "password = None" in body and "base_url = None" in body


@pytest.mark.parametrize("env_indicator,expect_exc", [
    (None, "EnvBlocked"),          # 缺指示器 → 第二个 EnvBlocked 分支
    ("#login-ok", "NotWired"),     # 指示器在场 → 落到 NotWired（未接线）分支
])
def test_fast_login_no_host_no_credential_in_traceback(tmp_path, monkeypatch,
                                                       env_indicator, expect_exc):
    """红线②（L-HOST+L-PW）行为面：真执行 fast 版 login——① 不含内网主机的消息；
    ② 本机 pytest 9 默认打印抛出帧 f_locals，故 login 帧的 password/base_url 必须已归零
    （否则明文凭据进 stdout）。account 非秘密不在归零面内。"""
    af = _load_real_fast_base(tmp_path)
    monkeypatch.setitem(sys.modules, "_atlas_fast", af)
    if env_indicator is None:
        monkeypatch.delenv("ATLAS_FAST_LOGIN_INDICATOR", raising=False)
    else:
        monkeypatch.setenv("ATLAS_FAST_LOGIN_INDICATOR", env_indicator)
    pages = _load_pages(tmp_path, _FAST_COMPS, fast=True)

    host = "https://internal-sensitive-host.example.internal"
    secret = "SUPER-SECRET-PW"
    exc_type = getattr(pages, expect_exc)
    with pytest.raises(exc_type) as ei:
        pages.login(None, base_url=host, account="u", password=secret)

    # ① 消息面：无主机名/协议（L-HOST），且未接线分支点名「登录未接线」
    msg = str(ei.value)
    assert host not in msg and "http" not in msg and "internal-sensitive-host" not in msg
    if expect_exc == "NotWired":
        assert "登录未接线" in msg

    # ② 帧面：唯一 login 帧的 f_locals 已归零，秘密/主机不出现在其 repr 中
    login_frames = []
    tb = ei.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name == "login":
            login_frames.append(tb.tb_frame.f_locals)
        tb = tb.tb_next
    assert login_frames, "traceback 应含 login 帧（pytest 9 会打印它的 f_locals）"
    for floc in login_frames:
        assert floc.get("password") is None
        assert floc.get("base_url") is None
        rendered = repr(floc)
        assert secret not in rendered and "internal-sensitive-host" not in rendered


def test_fast_pages_bad_shape_still_safe():
    """坏形态/空库的 fast 版也不裸崩（与 SelectorRegistry 同纪律）。"""
    _stubbed_emit_fast()
    _assert_compiles(emit_pages_module(None, fast=True))
    _assert_compiles(emit_pages_module({"components": {"bad": "not-a-map"}}, fast=True))
    assert "class OpenDocList" in emit_pages_module({"open_doc_list": {}}, fast=True)


def test_fast_render_deterministic():
    _stubbed_emit_fast()
    a = emit_pytest_from_flow(_FAST_FLOW, base_url="u", fast=True)
    b = emit_pytest_from_flow(_FAST_FLOW, base_url="u", fast=True)
    assert a == b
    pa = emit_pages_module(_FAST_COMPS, fast=True)
    assert pa == emit_pages_module(_FAST_COMPS, fast=True)


def test_fast_artifact_plan_ctx_persistence_is_conftest_side():
    """前提自证（lead 裁定 #4；gap9 F5 + 契约 C.1）：渲染层 fast 产物只改**内存** plan_ctx，
    **绝不自己调** save_plan_ctx/load_plan_ctx——跨批持久化（fixture teardown 落盘 + 白名单键
    过滤）的义务在 conftest 一处（support-impl #2 责任面）。产物层自写=第二条落盘路径，
    绕开白名单键过滤还违 #92；support-impl 若漏实现，xcheck 会撞上「跨批读断链」，
    本测把责任边界钉成显式判据而不是静默两可。
    """
    _stubbed_emit_fast()
    src = emit_pytest_from_flow(_FAST_FLOW, base_url="", fast=True)
    assert "save_plan_ctx" not in src and "load_plan_ctx" not in src
    assert "plan_ctx['resp_1_a'] = result.data" in src  # 只内存写，持久化归 conftest
    pages = emit_pages_module(_FAST_COMPS, fast=True)
    assert "save_plan_ctx" not in pages and "load_plan_ctx" not in pages


# ---- F-9 生成期闸门（lead 裁决：拒产引用未知类的半成品）----


def test_fast_gate_ui_without_components_rejected_by_name():
    from atlas.generator.emit_pytest import _fast_ui_gates
    specs = _specs_from_flow(_FAST_FLOW)
    with pytest.raises(PytestEmitError) as ei:
        _fast_ui_gates(specs, {})
    msg = str(ei.value)
    assert "--fast" in msg and "selector_candidate" in msg and "UI" in msg


def test_fast_gate_unknown_component_rejected_by_name():
    """F-10/B-2 第二道闸：page_object 已声明但组件库无此类 → 点名声明值+已知组件清单；
    近形名也不放行（成员资格=精确集合判定，引擎不猜，#11）。"""
    from atlas.generator.emit_pytest import _fast_ui_gates
    specs = _specs_from_flow({"id": "g", "steps": [
        {"task": "pick", "intent": "选", "page_object": "ghost_widget"}]})
    with pytest.raises(PytestEmitError) as ei:
        _fast_ui_gates(specs, {"customer_selection": {}})
    msg = str(ei.value)
    assert "页对象 ghost_widget 无对应已批准组件" in msg  # 契约 C 表语义串
    assert "customer_selection" in msg  # 已知组件清单点名，让人知道补哪块知识
    # 近形名（差一个复数 s）同样拒绝——有相似度就放行=把「猜」焊进生成器
    specs2 = _specs_from_flow({"id": "g", "steps": [
        {"task": "pick", "intent": "选", "page_object": "open_doc_lists"}]})
    with pytest.raises(PytestEmitError):
        _fast_ui_gates(specs2, {"open_doc_list": {}})


def test_fast_gate_missing_declaration_rejected_by_name():
    """缺声明位（gap9b B-2/F-10 收口）：UI 步不带 page_object/legacy_pages → 点名拒绝。
    猜名支已删——过去 task=open_doc_list 恰好命中组件名的通路不复存在（语义翻新不删测）。"""
    from atlas.generator.emit_pytest import _fast_ui_gates
    specs = _specs_from_flow({"id": "g", "steps": [
        {"task": "open_doc_list", "id": "s1", "intent": "打开"}]})  # task 恰=组件名也不行
    with pytest.raises(PytestEmitError) as ei:
        _fast_ui_gates(specs, {"open_doc_list": {}})
    assert "缺声明位" in str(ei.value)
    # 正向对照：同一步补上声明位 → 过闸（防恒拒空转）
    ok = _specs_from_flow({"id": "g", "steps": [
        {"task": "open_doc_list", "id": "s1", "intent": "打开",
         "page_object": "open_doc_list"}]})
    _fast_ui_gates(ok, {"open_doc_list": {}})


def test_fast_gate_legacy_only_step_passes():
    """legacy 委托腿良形即放行（成员资格只判 page_object——lib.pages 在旧仓，无类可对）。
    正向对照：函数名为空 → 按缺声明位拒。"""
    from atlas.generator.emit_pytest import _fast_ui_gates
    specs = _specs_from_flow({"id": "g", "steps": [
        {"task": "cancel", "intent": "撤单", "legacy_pages": "demo_legacy_fn"}]})
    _fast_ui_gates(specs, {"some_component": {}})  # atlas_pages 仍需产出（委托函数在其内）
    empty = _specs_from_flow({"id": "g", "steps": [
        {"task": "cancel", "intent": "撤单", "legacy_pages": "  "}]})
    with pytest.raises(PytestEmitError) as ei:
        _fast_ui_gates(empty, {"some_component": {}})
    assert "缺声明位" in str(ei.value)


@pytest.mark.parametrize("bad_val", [{"nested": 1}, True, 42, ["open_doc_list"]],
                         ids=["dict", "bool", "int", "list"])
def test_fast_gate_bad_shape_declared_value_rejected(bad_val):
    """程序化直调绕过 compile 静态段时，生成期最后防线按「坏形态/缺失」点名原值
    （不裸崩不静默；证据值进消息，同 #117 非空标量纪律）。"""
    from atlas.generator.emit_pytest import _fast_ui_gates
    specs = _specs_from_flow({"id": "g", "steps": [
        {"task": "pick", "intent": "选", "page_object": bad_val}]})
    with pytest.raises(PytestEmitError) as ei:
        _fast_ui_gates(specs, {"open_doc_list": {}})
    assert "形态" in str(ei.value) or "缺声明位" in str(ei.value)


def test_fast_gate_both_keys_rejected():
    """互斥两键同带 → 生成期最后防线点名（第一道在 compile 静态段，判据同源不复制 #92）。"""
    from atlas.generator.emit_pytest import _fast_ui_gates
    specs = _specs_from_flow({"id": "g", "steps": [
        {"task": "pick", "intent": "选", "page_object": "open_doc_list",
         "legacy_pages": "demo_legacy_fn"}]})
    with pytest.raises(PytestEmitError) as ei:
        _fast_ui_gates(specs, {"open_doc_list": {}})
    assert "同带" in str(ei.value)


def test_direct_render_missing_declaration_fails_loud():
    """直调渲染入口（不经 package 闸）同样当场 PytestEmitError——缺声明位不留
    「渲染出运行期必炸引用」的旁路（红线 #9 不假装可用）。"""
    _stubbed_emit_fast()
    specs = _specs_from_flow({"id": "g", "steps": [
        {"task": "open_doc_list", "intent": "打开"}]})
    with pytest.raises(PytestEmitError) as ei:
        render_test_module(specs, flow_id="g", base_url="", fast=True)
    assert "缺声明位" in str(ei.value)


def test_guess_branch_removed_source_level():
    """B-4.4④ 源码级判据：`_pascal(spec.task` 猜名支与「task 对齐组件名」消息已不存在
    （只扫源码文本判「通路消失」，行为面由缺声明位测守）。"""
    import atlas.generator.emit_pytest as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "_pascal(spec.task" not in src
    assert "对齐组件名" not in src


def test_fast_gate_matches_pages_module_class_set():
    """F-10 判据的集合语义：闸门认的类名=emit_pages_module 实际生成的类名（一个真值源）。"""
    from atlas.generator.emit_pytest import _fast_ui_gates
    import re
    comps = {"open_doc_list": {"selectors": {"a": ".x"}}, "customer_selection": {}}
    src = emit_pages_module(comps, fast=True)
    rendered = set(re.findall(r"^class (\w+):", src, flags=re.M))
    assert rendered == {"OpenDocList", "CustomerSelection"}
    ok_specs = _specs_from_flow({"id": "f", "steps": [
        {"task": "open", "intent": "打开", "page_object": "open_doc_list"}]})
    _fast_ui_gates(ok_specs, comps)  # 声明位命中 → 不抛（正向）
    # 正向对照的另一面：同 fixture 换成未声明步 → 必抛（防空转恒过）
    silent = _specs_from_flow({"id": "f", "steps": [{"task": "open", "intent": "打开"}]})
    with pytest.raises(PytestEmitError):
        _fast_ui_gates(silent, comps)


def test_fast_package_missing_declaration_rejected_no_residue(tmp_path):
    """B-4.4④ 包级：UI 步无声明 + fast → 「缺声明位」拒绝、目录无残留（拒绝先于 mkdir）。"""
    _stubbed_emit_fast()
    out = tmp_path / "p"
    demo_like = {"components": {"customer_selection": {"selectors": {"dialog": ".dlg"}},
                                "payment_terms": {"selectors": {"table": ".t"}}}}
    flow_sel = {"id": "sel", "steps": [
        {"task": "select_customer", "id": "s1", "intent": "选客户"}]}
    with pytest.raises(PytestEmitError) as ei:
        emit_pytest_package(out_dir=out, flow=flow_sel, components=demo_like, fast=True)
    assert "缺声明位" in str(ei.value)
    assert not out.exists() or not any(out.iterdir())


def test_fast_package_declared_mismatch_rejected_no_residue(tmp_path):
    """包级第二面：声明了但组件库无此类 → 点名拒绝、零残留（与缺声明位分语义两测）。"""
    _stubbed_emit_fast()
    out = tmp_path / "p"
    flow_ghost = {"id": "gh", "steps": [
        {"task": "pick", "id": "s1", "intent": "选", "page_object": "ghost_widget"}]}
    with pytest.raises(PytestEmitError) as ei:
        emit_pytest_package(out_dir=out, flow=flow_ghost,
                            components={"components": {"customer_selection": {}}}, fast=True)
    assert "页对象 ghost_widget 无对应已批准组件" in str(ei.value)
    assert not out.exists() or not any(out.iterdir())


def test_fast_gate_pure_api_without_components_passes():
    """正向对照（防闸门杀能用路径，#115）：纯 API 源无组件库照常过闸。"""
    from atlas.generator.emit_pytest import _fast_ui_gates
    api_only = _specs_from_flow({"id": "a", "steps": [
        {"task": "api_call", "method": "GET", "path": "/p", "save_as": "r"}]})
    _fast_ui_gates(api_only, {})  # 不抛即通过


def test_fast_package_gate_rejects_before_any_write(tmp_path):
    """闸门命中时不残留半成品：拒绝发生在写盘前（目录都不得出现）。"""
    _stubbed_emit_fast()
    out = tmp_path / "p"
    with pytest.raises(PytestEmitError):
        emit_pytest_package(out_dir=out, flow=_FAST_FLOW,
                                  components={"components": {}}, fast=True)
    assert not out.exists() or not any(out.iterdir())


def test_fast_package_pure_api_without_components_writes(tmp_path):
    """正向对照：无组件库 + 纯 API 源 + fast → 照常产出（不被闸门误伤）。"""
    _stubbed_emit_fast()
    out = tmp_path / "p"
    written = emit_pytest_package(out_dir=out, endpoints=[_read_endpoint()],
                                  flow_id="api-fast", components={"components": {}},
                                  fast=True)
    fast_mod = _stubbed_emit_fast()
    assert f"{fast_mod.FAST_SUPPORT_MODULE_NAME}.py" in {Path(p).name for p in written}
    src = (out / "test_api_fast.py").read_text(encoding="utf-8")
    assert "@fast_case(op=" in src
    assert not (out / f"{PAGES_MODULE_NAME}.py").exists()  # 无组件库仍不出页对象库
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "快模式" in readme and "atlas allure" in readme


def test_fast_package_with_ui_and_components_writes_full_set(tmp_path):
    _stubbed_emit_fast()
    out = tmp_path / "p"
    written = emit_pytest_package(out_dir=out, flow=_FAST_FLOW, components=_FAST_COMPS,
                                  fast=True)
    names = {Path(p).name for p in written}
    assert {f"test_fast_flow.py", f"{PAGES_MODULE_NAME}.py",
            f"{SUPPORT_MODULE_NAME}.py", "_atlas_fast.py", "conftest.py",
            "README.md"} <= names
    for p in written:
        if p.endswith(".py"):
            _assert_compiles(Path(p).read_text(encoding="utf-8"))


def test_nonfast_package_readme_and_files_unchanged(tmp_path):
    """fast=False 包形态零涟漪：README/文件集=历史形态（无快模式段、无底座/conftest）。"""
    out = tmp_path / "p"
    written = emit_pytest_package(out_dir=out, endpoints=[_read_endpoint()],
                                  flow_id="slow", components={"components": {}})
    assert "_atlas_fast.py" not in {Path(p).name for p in written}
    assert "conftest.py" not in {Path(p).name for p in written}
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "快模式" not in readme
    assert _sha(readme) == _sha(_build_readme("slow",
        _specs_from_endpoints([_read_endpoint()]), False))


# ---- 归因面判据（lead 裁决 A/F-2）：未接线=env_blocked 家族、真缺陷不被洗 ----
# 依赖 support-impl 的真实 `_atlas_fast` 底座（C.1）；并行窗口内如实 skip（不假绿计通过）。


def _load_real_fast_base(tmp_path):
    import atlas.generator as g
    if not (Path(g.__file__).parent / "emit_fast.py").exists():
        pytest.skip("emit_fast 底座未落地（C.1 并行开发窗口）——归因判据测待集成")
    if "atlas.generator.emit_fast" in sys.modules and not hasattr(
            sys.modules["atlas.generator.emit_fast"], "emit_fast_support_module"):
        pytest.skip("当前进程加载的是契约桩，真实底座测请单跑或集成后跑")
    from atlas.generator.emit_fast import emit_fast_support_module
    p = tmp_path / "_atlas_fast_under_test.py"
    p.write_text(emit_fast_support_module(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_atlas_fast_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def test_notwired_attr_env_blocked_with_positive_controls(tmp_path):
    """未接线（NotWired⊂EnvBlocked，类型级判据在底座单点）→ 行归 env_blocked；
    同测正向对照：真 AssertionError 归 failed/assertion、裸 NotImplementedError **不得**
    落进 env_blocked——否则「资产没长出来」这条判据就成了给缺陷洗绿的门（恒真防线）。"""
    af = _load_real_fast_base(tmp_path)
    assert issubclass(af.NotWired, af.EnvBlocked)
    runs = tmp_path / "runs.jsonl"

    w = af.RunWriter("m::t_notwired", "local", path=runs)
    with pytest.raises(af.NotWired):
        with w.step("ui open_doc_list"):
            raise af.NotWired("未接线：OpenDocList.action（元素：table），需人审接入")
    w.close("env_blocked", reason="未接线：OpenDocList.action（元素：table），需人审接入")

    w2 = af.RunWriter("m::t_assert", "local", path=runs)
    with pytest.raises(AssertionError):
        with w2.step("assert ok"):
            assert False, "值不符"
    w2.close("failed")

    w3 = af.RunWriter("m::t_nyi", "local", path=runs)
    with pytest.raises(NotImplementedError):
        with w3.step("action real-bug"):
            raise NotImplementedError("真代码缺陷，非资产缺口")
    w3.close("failed")

    rows = {r.get("case"): r for r in _run_rows(runs)}
    assert set(rows) == {"m::t_notwired", "m::t_assert", "m::t_nyi"}
    nw, asr, nyi = rows["m::t_notwired"], rows["m::t_assert"], rows["m::t_nyi"]
    # 未接线：run 终态 + step 归因都走 env_blocked 家族
    assert nw["status"] == "env_blocked"
    assert nw.get("reason", "").startswith("未接线：OpenDocList.action")  # F-5 点名文案落盘
    assert (nw["steps"][0].get("fail_class") or "") == "env_blocked"
    # 正向对照 A：断言失败仍是 failed（不被家族归因吞掉），step 归 assertion
    assert asr["status"] == "failed"
    assert (asr["steps"][0].get("fail_class") or "") == "assertion"
    # 正向对照 B：裸 NotImplementedError 不得被洗进 env_blocked（F-2 的拒绝面）
    assert nyi["status"] == "failed"
    assert (nyi["steps"][0].get("fail_class") or "") != "env_blocked"


# ---- gap13：页对象两形态（CSS 字符串 / 结构化定位描述） ----


def _load_pages(tmp_path: Path, components: dict, *, fast: bool = False):
    p = tmp_path / "atlas_pages_under_test.py"
    p.write_text(emit_pages_module(components, fast=fast), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_pages_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _RecPage:
    """只记调用的假 page（产物侧真执行 locator() 取用，不起浏览器）。"""

    def __init__(self):
        self.calls = []

    def locator(self, css):
        self.calls.append(("locator", css))
        return ("css", css)

    def get_by_role(self, role, name=None, **kw):
        self.calls.append(("get_by_role", role, name, kw))
        return ("role", role, name)

    def get_by_label(self, text, **kw):
        self.calls.append(("get_by_label", text, kw))
        return ("label", text)

    def get_by_test_id(self, testid):
        self.calls.append(("get_by_test_id", testid))
        return ("testid", testid)


def test_pages_structured_description_emits_aria_locator(tmp_path):
    """结构化描述 → 产物 locator 走 ARIA（gap13 C 主判据）；产物仍零 import。"""
    comps = {"components": {"order_header": {"selectors": {
        "save": {"role": "button", "name": "保存"},
        "grid": {"label": "明细表"},
        "row": {"testid": "order-row-1"},
        "legacy": ".app-main .btn",
    }}}}
    src = emit_pages_module(comps)
    _assert_compiles(src)
    assert "import atlas" not in src and "import atlas" not in src
    assert "get_by_role" in src and "get_by_test_id" in src

    mod = _load_pages(tmp_path, comps)
    page = _RecPage()
    cls = mod.OrderHeader
    assert cls(page).locator("save") == ("role", "button", "保存")
    assert cls(page).locator("grid") == ("label", "明细表")
    assert cls(page).locator("row") == ("testid", "order-row-1")
    assert cls(page).locator("legacy") == ("css", ".app-main .btn")   # CSS 形态仍走 locator()
    assert page.calls[0] == ("get_by_role", "button", "保存", {"exact": True})


def test_pages_pure_css_output_is_byte_identical(tmp_path):
    """纯 CSS 组件：locator() 方法体与改造前逐字同形（存量产物零涟漪的可见证据）。"""
    src = emit_pages_module({"components": {"x": {"selectors": {"a": ".css"}}}})
    old_body = ('    def locator(self, element):\n'
                '        """按元素名取 locator；未知名点名（不静默 None）。"""\n'
                '        sel = self.SELECTORS.get(element)\n'
                '        if sel is None:\n'
                '            raise KeyError('
                'f"{type(self).__name__} 无元素 {element!r}；已知：{sorted(self.SELECTORS)}")\n'
                '        return self.page.locator(sel)\n')
    assert old_body in src
    assert "kind" not in src and "get_by_role" not in src


def test_pages_unknown_element_still_named(tmp_path):
    mod = _load_pages(tmp_path, {"components": {"x": {"selectors": {
        "a": {"role": "button", "name": "保存"}}}}})
    with pytest.raises(KeyError, match="无元素"):
        mod.X(_RecPage()).locator("nope")


def test_pages_bad_description_refuses_to_emit(tmp_path):
    """手改坏描述：生成期点名拒绝整份导出，不产出「一点就炸」的页对象。"""
    from atlas import locators
    with pytest.raises(ValueError, match="定位描述形态坏"):
        emit_pages_module({"components": {"x": {"selectors": {"a": {"role": "button",
                                                                   "css": ".x"}}}}})
    # 与 locators 同一把尺：坏形态的判据不在生成器里复制一份
    with pytest.raises(locators.LocatorSpecError):
        locators.parse({"role": ""})


@pytest.mark.parametrize("fast", [False, True])
def test_pages_both_renderers_share_aria_branch(tmp_path, fast, monkeypatch):
    """fast 与非 fast 两条渲染腿共用一处 helper：形态不许各写各的（#92）。"""
    if fast:   # fast 页对象 import _atlas_fast：把底座写成真文件并挂上 sys.path
        af = _stubbed_emit_fast()
        (tmp_path / "_atlas_fast.py").write_text(af.emit_fast_support_module(), encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
    comps = {"components": {"c": {"selectors": {"save": {"role": "button", "name": "保存"}}}}}
    src = emit_pages_module(comps, fast=fast)
    _assert_compiles(src)
    mod = _load_pages(tmp_path, comps, fast=fast)
    assert mod.C(_RecPage()).locator("save") == ("role", "button", "保存")

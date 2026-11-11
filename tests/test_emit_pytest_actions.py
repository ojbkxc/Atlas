"""emit_pytest 产物 UI 腿「可执行化」判据（gap9 动作语义 + auto-wire + 向后兼容）。

范围（2.2）：组件库 `actions`（元素→动作 kind/value/task）直出可执行页对象方法、无声明时按
组件库数据 auto-wire（task→组件动作）、真未接线仍 NotWired⊂EnvBlocked、老知识产物逐字节不变。

离线纪律（lead F-1/R5）：零网络、零真浏览器。执行面只用**窄接口 fake page**（只实现被测
动作真正触碰的方法，多点一个就 AttributeError——防「产物碰了没声明的 page 能力」蒙混）。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import types

import pytest

from atlas.generator.emit_pytest import (
    PAGES_MODULE_NAME, SUPPORT_MODULE_NAME, PytestEmitError,
    emit_pages_module, emit_pytest_from_flow, emit_pytest_package,
    render_test_module, _specs_from_flow, _ui_leg, _task_action_wiring,
    _components_map, _fast_ui_gates,
)


# ---------------------------------------------------------------------------
# 窄接口 fake page：只实现「被测动作真会调用」的方法
# ---------------------------------------------------------------------------

class _FakeLocator:
    """只暴露 click/fill/select_option/press；其余属性访问=测试失败（不静默）。"""

    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def click(self, **_kw):
        self.page.calls.append(("click", self.selector))

    def fill(self, value):
        self.page.calls.append(("fill", self.selector, value))

    def select_option(self, value):
        self.page.calls.append(("select_option", self.selector, value))

    def press(self, value):
        self.page.calls.append(("press", self.selector, value))


class _FakePage:
    """窄接口：只有 locator()；每个 locator 记录被调用的动作与实参。"""

    def __init__(self):
        self.calls = []

    def locator(self, selector):
        return _FakeLocator(self, selector)


# _atlas_fast 桩（真实底座在 emit_fast；这里只取产物页对象依赖的两个异常类型）
_FAST_STUB_SRC = '''
class EnvBlocked(RuntimeError):
    pass


class NotWired(EnvBlocked):
    def __init__(self, reason):
        if not str(reason).strip():
            raise ValueError("NotWired 必须点名未接线的组件/元素")
        super().__init__(reason)
'''


def _stub_fast(monkeypatch):
    stub = types.ModuleType("_atlas_fast")
    ns: dict = {}
    exec(_FAST_STUB_SRC, ns)
    stub.EnvBlocked = ns["EnvBlocked"]
    stub.NotWired = ns["NotWired"]
    # setitem 保证测试后恢复（无原值则删除），不与其它测试串扰
    monkeypatch.setitem(sys.modules, "_atlas_fast", stub)
    return stub


def _load_generated_pages(tmp_path, monkeypatch, components, *, fast=True):
    """把生成的 atlas_pages 源码落到磁盘并 exec 回来（产物零 import atlas，仅依赖 _atlas_fast）。"""
    stub = _stub_fast(monkeypatch)
    src = emit_pages_module(components, fast=fast)
    ast.parse(src)  # 语法自检（产物必须可编译）
    path = tmp_path / "gen_pages_under_test.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("gen_pages_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, stub


# ---------------------------------------------------------------------------
# ① 组件库 actions → 可执行 UI 操作（fake page 验证点击/填充真的被调用）
# ---------------------------------------------------------------------------

_ACTION_COMPS = {"components": {"dialog_cmp": {
    "selectors": {"search": "button.search", "name": "input.name",
                  "qty": "input.qty", "submit": "button.ok"},
    "actions": {
        "search": {"kind": "click"},
        "name": {"kind": "fill", "value": "ACME"},
        "qty": {"kind": "fill"},                 # 无绑定 → 方法收 value
        "submit": {"kind": "press", "value": "Enter"},
    },
}}}


def test_action_methods_execute_click_and_fill(tmp_path, monkeypatch):
    """窄接口 fake page 实跑：click/fill(绑定值)/fill(参数)/press 真的落到 locator 上。"""
    mod, _ = _load_generated_pages(tmp_path, monkeypatch, _ACTION_COMPS)
    cls = mod.DialogCmp
    page = _FakePage()
    obj = cls(page)
    obj.do_search()
    obj.do_name()
    obj.do_qty("7")
    obj.do_submit()
    assert page.calls == [
        ("click", "button.search"),
        ("fill", "input.name", "ACME"),
        ("fill", "input.qty", "7"),
        ("press", "button.ok", "Enter"),
    ]


def test_select_method_executes(tmp_path, monkeypatch):
    comps = {"components": {"dd": {"selectors": {"opt": ".sel"},
                                   "actions": {"opt": {"kind": "select", "value": "DIV01"}}}}}
    mod, _ = _load_generated_pages(tmp_path, monkeypatch, comps)
    page = _FakePage()
    mod.Dd(page).do_opt()
    assert page.calls == [("select_option", ".sel", "DIV01")]


def test_single_autonomous_action_drives_action(tmp_path, monkeypatch):
    """单个「可自主执行」动作 → 组件级 action() 直出该动作（声明位 page_object 路径也可跑）。"""
    comps = {"components": {"one": {"selectors": {"go": "button.go"},
                                    "actions": {"go": {"kind": "click"}}}}}
    mod, _ = _load_generated_pages(tmp_path, monkeypatch, comps)
    page = _FakePage()
    mod.One(page).action()
    assert page.calls == [("click", "button.go")]


def test_single_needs_value_action_stays_notwired(tmp_path, monkeypatch):
    """单动作但需外部值 → action() 仍 NotWired（点不到值通道，不假装可跑），do_<元素>(v) 可用。"""
    comps = {"components": {"one": {"selectors": {"q": "input.q"},
                                    "actions": {"q": {"kind": "fill"}}}}}
    mod, stub = _load_generated_pages(tmp_path, monkeypatch, comps)
    page = _FakePage()
    obj = mod.One(page)
    with pytest.raises(stub.NotWired):
        obj.action()
    obj.do_q("42")
    assert page.calls == [("fill", "input.q", "42")]


def test_multiple_actions_action_notwired(tmp_path, monkeypatch):
    """多动作 → action() 不猜顺序，NotWired 点名；各 do_<元素>() 仍可单独跑。"""
    comps = {"components": {"m": {"selectors": {"a": ".a", "b": ".b"},
                                  "actions": {"a": {"kind": "click"}, "b": {"kind": "click"}}}}}
    mod, stub = _load_generated_pages(tmp_path, monkeypatch, comps)
    obj = mod.M(_FakePage())
    with pytest.raises(stub.NotWired):
        obj.action()


# ---------------------------------------------------------------------------
# ② 无声明时按组件库数据 auto-wire（task → 组件动作；引擎不硬编码业务名）
# ---------------------------------------------------------------------------

_WIRE_COMPS = {"components": {"cust_sel": {
    "selectors": {"search": "button.s", "confirm": ".ok"},
    "actions": {"search": {"kind": "click", "task": "search_customer"},
                "confirm": {"kind": "click", "task": "confirm_customer"}},
}, "terms": {
    "selectors": {"qty": "input.q"},
    "actions": {"qty": {"kind": "fill", "value": "3", "task": "set_qty"}},
}}}


def test_auto_wire_renders_executable_method():
    """无 page_object/legacy_pages 声明，但 task 在组件库 actions 数据里认领 → 渲染 do_<元素>()。"""
    flow = {"id": "aw", "steps": [{"task": "search_customer", "id": "s1", "intent": "搜客户"}]}
    src = emit_pytest_from_flow(flow, fast=True, components=_WIRE_COMPS)
    ast.parse(src)
    assert f"{PAGES_MODULE_NAME}.CustSel(page).do_search()" in src
    assert "auto-wire" in src
    # 未命中其它任务的组件不串台
    assert ".do_confirm()" not in src


def test_auto_wire_accounts_bound_value_at_generation():
    flow = {"id": "aw", "steps": [{"task": "set_qty", "id": "s1", "intent": "数量"}]}
    src = emit_pytest_from_flow(flow, fast=True, components=_WIRE_COMPS)
    assert f"{PAGES_MODULE_NAME}.Terms(page).do_qty()" in src


def test_auto_wired_method_executes_with_fake_page(tmp_path, monkeypatch):
    mod, _ = _load_generated_pages(tmp_path, monkeypatch, _WIRE_COMPS)
    page = _FakePage()
    mod.CustSel(page).do_confirm()
    assert page.calls == [("click", ".ok")]


def test_auto_wire_gate_passes_via_package(tmp_path):
    """包级端到端：无声明 + 有 auto-wire 数据 → 过闸、写盘、test 模块含可执行调用。"""
    out = tmp_path / "pkg"
    flow = {"id": "awpkg", "steps": [{"task": "search_customer", "id": "s1", "intent": "搜客户"}]}
    written = emit_pytest_package(out_dir=out, flow=flow, components=_WIRE_COMPS, fast=True)
    names = {p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in written}
    assert f"test_{'awpkg'}.py" in names
    src = (out / "test_awpkg.py").read_text(encoding="utf-8")
    assert f"{PAGES_MODULE_NAME}.CustSel(page).do_search()" in src


def test_auto_wire_ambiguous_task_rejected():
    """同一 task 被两个组件动作认领 → 拒绝（不猜归属）。"""
    comps = {"components": {
        "a": {"selectors": {"x": ".x"}, "actions": {"x": {"kind": "click", "task": "t"}}},
        "b": {"selectors": {"y": ".y"}, "actions": {"y": {"kind": "click", "task": "t"}}},
    }}
    with pytest.raises(PytestEmitError) as ei:
        _task_action_wiring(_components_map(comps))
    assert "多个组件动作认领" in str(ei.value)


def test_auto_wire_needs_value_task_rejected():
    """认领 task 的动作需外部值（未绑定）→ auto-wire 无值通道，拒绝（不生成运行期必炸调用）。"""
    comps = {"components": {"a": {"selectors": {"x": "input.x"},
                                  "actions": {"x": {"kind": "fill", "task": "t"}}}}}
    with pytest.raises(PytestEmitError) as ei:
        _task_action_wiring(_components_map(comps))
    assert "未绑定 value" in str(ei.value)


def test_auto_wire_declaration_wins_over_data():
    """声明位与 auto-wire 数据同在 → 声明优先（声明位是人显式决定，不被数据盖掉）。"""
    flow = {"id": "w", "steps": [
        {"task": "search_customer", "id": "s1", "intent": "搜", "page_object": "cust_sel"}]}
    src = emit_pytest_from_flow(flow, fast=True, components=_WIRE_COMPS)
    assert f"{PAGES_MODULE_NAME}.CustSel(page).action()" in src
    assert ".do_search()" not in src  # 走声明位 action()，不走 auto-wire


def test_auto_wire_unknown_task_still_missing_declaration():
    """无声明且 task 不在数据 → 仍旧「缺声明位」拒绝（不静默放过）。"""
    flow = {"id": "z", "steps": [{"task": "unknown_task", "id": "s1", "intent": "未知"}]}
    with pytest.raises(PytestEmitError) as ei:
        render_test_module(_specs_from_flow(flow), fast=True,
                           components_map=_components_map(_WIRE_COMPS))
    assert "缺声明位" in str(ei.value)


# ---------------------------------------------------------------------------
# ③ 真未接线仍 NotWired ⊂ EnvBlocked（不伪装 skipped/passed）
# ---------------------------------------------------------------------------

def test_unwired_component_action_raises_notwired(tmp_path, monkeypatch):
    """有选择器、无 actions → action() 抛 NotWired（EnvBlocked 子类），消息点名类名与元素。"""
    comps = {"components": {"bare": {"selectors": {"btn": "button.b"}}}}
    mod, stub = _load_generated_pages(tmp_path, monkeypatch, comps)
    obj = mod.Bare(_FakePage())
    with pytest.raises(stub.NotWired) as ei:
        obj.action()
    msg = str(ei.value)
    assert "Bare.action" in msg and "btn" in msg
    assert issubclass(stub.NotWired, stub.EnvBlocked)


def test_real_fast_base_notwired_is_envblocked():
    """真实底座（emit_fast）里 NotWired 仍是 EnvBlocked 子类且拒空原因（契约不漂移）。"""
    from atlas.generator.emit_fast import emit_fast_support_module
    ns: dict = {}
    exec(emit_fast_support_module(), ns)
    assert issubclass(ns["NotWired"], ns["EnvBlocked"])
    with pytest.raises(ValueError):
        ns["NotWired"]("   ")


# ---------------------------------------------------------------------------
# ④ 向后兼容：老知识（无 actions）产物逐字节不变（#115 零涟漪）
# ---------------------------------------------------------------------------

# 下列哈希=改造前（commit 0399723）的输出指纹；任何对「无 actions」路径的偷改都会命中红。
_LEGACY_PAGES_HASHES = {
    "fast_noactions": "5236ab9b1875b9e79aa056d8457ca1bc35a7691b57fe3a68aa767c11fd9a47b5",
    "fast_single": "7059b5b9dbc5219bb6d731be059337dc65e9eb7b6b345fdc3020ecd4beb191fd",
    "nonfast_noactions": "da3e581b21c286bfff701d5915288dbc86cbbbd982d176ada467f0eb65088d7a",
}


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_legacy_knowledge_pages_byte_identical():
    """老 components.json（不写新键）→ fast/非 fast 页对象输出逐字节=改造前。"""
    comps = {"components": {
        "open_doc_list": {"selectors": {"table": ".el-table"}},
        "customer_selection": {"selectors": {"dialog": ".dlg"}}}}
    single = {"components": {"one": {"selectors": {"btn": "button.ok"},
                                     "models": {"m": "x"}, "notes": "n"}}}
    got = {
        "fast_noactions": _sha(emit_pages_module(comps, fast=True)),
        "fast_single": _sha(emit_pages_module(single, fast=True)),
        "nonfast_noactions": _sha(emit_pages_module(comps, fast=False)),
    }
    assert got == _LEGACY_PAGES_HASHES


def _class_block(src: str, name: str) -> str:
    head = f"class {name}:"
    i = src.index(head)
    j = src.find("\nclass ", i + 1)
    return src[i:j if j != -1 else len(src)]


def test_adding_actions_key_does_not_change_other_components():
    """同库中「A 加 actions」不得改动「B（无 actions）」的类块字节（各组件独立）。"""
    base = {"components": {"b": {"selectors": {"x": ".x"}}}}
    with_a = {"components": {"a": {"selectors": {"y": ".y"},
                                   "actions": {"y": {"kind": "click"}}},
                             "b": {"selectors": {"x": ".x"}}}}
    sb = _class_block(emit_pages_module(base, fast=True), "B")
    sa = _class_block(emit_pages_module(with_a, fast=True), "B")
    assert sa == sb


# ---------------------------------------------------------------------------
# 形态判据（坏形态生成期点名，不留到运行期）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("act,needle", [
    ({"kind": "tab"}, "kind 非法"),
    ({"kind": "click", "value": "x"}, "click 不接受 value"),
    ({"kind": "click", "oops": 1}, "未知键"),
    ("not-a-map", "必须是映射"),
    ({"kind": "fill", "value": [1]}, "value 必须是字符串/数字"),
])
def test_bad_action_shape_rejected(act, needle):
    comps = {"components": {"c": {"selectors": {"go": ".go"}, "actions": {"go": act}}}}
    with pytest.raises(PytestEmitError) as ei:
        _task_action_wiring(_components_map(comps))
    assert needle in str(ei.value)


def test_action_element_must_exist_in_selectors():
    comps = {"components": {"c": {"selectors": {"go": ".go"},
                                  "actions": {"ghost": {"kind": "click"}}}}}
    with pytest.raises(PytestEmitError) as ei:
        _task_action_wiring(_components_map(comps))
    assert "不在 selectors" in str(ei.value)


def test_actions_non_mapping_rejected():
    comps = {"components": {"c": {"selectors": {"go": ".go"}, "actions": ["x"]}}}
    with pytest.raises(PytestEmitError) as ei:
        emit_pages_module(comps, fast=True)
    assert "actions 必须是映射" in str(ei.value)


# ---------------------------------------------------------------------------
# 零 import atlas（产物红线）；auto-wire 不改声明位/MISSING 语义
# ---------------------------------------------------------------------------

def test_artifacts_zero_atlas_import():
    pages = emit_pages_module(_WIRE_COMPS, fast=True)
    flow = {"id": "z", "steps": [{"task": "search_customer", "id": "s1", "intent": "搜"}]}
    test_mod = emit_pytest_from_flow(flow, fast=True, components=_WIRE_COMPS)
    for text in (pages, test_mod):
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] != "atlas" for a in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "atlas"


def test_ui_leg_signature_three_forms():
    """_ui_leg 三形态返回（声明两形态 + auto 三元素形态），互斥/缺失/坏形点名不回归。"""
    wiring = {"t": ("cust_sel", "search")}
    decl = _specs_from_flow({"id": "z", "steps": [
        {"task": "t", "intent": "x", "page_object": "cust_sel"}]})[0]
    assert _ui_leg(decl, wiring) == ("page_object", "cust_sel", None)
    auto = _specs_from_flow({"id": "z", "steps": [{"task": "t", "intent": "x"}]})[0]
    assert _ui_leg(auto, wiring) == ("auto", "cust_sel", "search")
    legacy = _specs_from_flow({"id": "z", "steps": [
        {"task": "t", "intent": "x", "legacy_pages": "fn"}]})[0]
    assert _ui_leg(legacy, wiring) == ("legacy_pages", "fn", None)


def test_fast_gate_allows_auto_wired_step():
    """_fast_ui_gates：auto-wire 命中即放行（不再当缺声明位拒），未命中仍拒。"""
    specs_ok = _specs_from_flow({"id": "g", "steps": [
        {"task": "search_customer", "id": "s1", "intent": "搜"}]})
    _fast_ui_gates(specs_ok, _components_map(_WIRE_COMPS))  # 不抛即通过
    specs_bad = _specs_from_flow({"id": "g", "steps": [
        {"task": "nope", "id": "s1", "intent": "x"}]})
    with pytest.raises(PytestEmitError):
        _fast_ui_gates(specs_bad, _components_map(_WIRE_COMPS))


def test_support_module_name_constant_unused_here():
    """哨兵：本文件不触碰自包含底座（确认 SUPPORT_MODULE_NAME 常量存在，防误删 import）。"""
    assert SUPPORT_MODULE_NAME == "_atlas_support"

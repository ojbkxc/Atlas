"""A：定位描述单一解析点（`locators.py`）的离线判据。

判据面：两种形态（CSS 字符串 / 结构化描述）的解析、**坏形态一律点名**（绝不静默把 dict 当
CSS 字面量用）、canonical 可读文本、resolve 的取用分派、以及 SelectorRegistry 的取用与
向后兼容（`get()` 仍返回原值）。零网络零浏览器：page 用窄 fake。
"""
from __future__ import annotations

import pytest

from atlas import locators
from atlas.knowledge import SelectorRegistry


class FakePage:
    """只记调用，不碰浏览器。"""

    def __init__(self):
        self.calls: list = []

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


# ===================== 解析：两形态 =====================

def test_str_is_css():
    assert locators.parse(".btn-primary") == {"kind": "css", "css": ".btn-primary"}


def test_role_with_and_without_name():
    assert locators.parse({"role": "button", "name": "保存"}) == {
        "kind": "role", "role": "button", "name": "保存"}
    assert locators.parse({"role": "dialog"}) == {"kind": "role", "role": "dialog"}


def test_label_testid_css_forms():
    assert locators.parse({"label": "客户编码"}) == {"kind": "label", "label": "客户编码"}
    assert locators.parse({"testid": "save-btn"}) == {"kind": "testid", "testid": "save-btn"}
    assert locators.parse({"css": "#app > table"}) == {"kind": "css", "css": "#app > table"}


# ===================== 坏形态一律点名（不静默回落） =====================

@pytest.mark.parametrize("bad", [None, 5, True, ["role"], ("role",)])
def test_non_str_non_dict_is_rejected(bad):
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.parse(bad)
    assert type(bad).__name__ in str(ei.value)


def test_empty_dict_is_rejected():
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.parse({})
    assert "至少要给一个" in str(ei.value)


def test_unknown_key_is_rejected_not_ignored():
    """打错的键最危险：静默忽略会拿另一个键的值当定位条件用。"""
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.parse({"role": "button", "nmae": "保存"})
    assert "nmae" in str(ei.value)


def test_multiple_kinds_are_rejected():
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.parse({"role": "button", "css": ".btn"})
    assert "多个定位键" in str(ei.value)


def test_name_key_null_is_rejected():
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.parse({"role": "button", "name": None})
    assert "null" in str(ei.value)


@pytest.mark.parametrize("bad", [{"role": ""}, {"role": "   "}, {"role": 5},
                                 {"label": ""}, {"testid": None}, {"css": ""},
                                 {"role": "button", "name": ""}])
def test_blank_or_wrong_typed_values_are_rejected(bad):
    with pytest.raises(locators.LocatorSpecError):
        locators.parse(bad)


@pytest.mark.parametrize("bad", [{"css": ".x", "name": "y"}, {"label": "a", "name": "b"},
                                 {"testid": "t", "name": "n"}])
def test_name_only_pairs_with_role(bad):
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.parse(bad)
    assert "name 只配 role" in str(ei.value) or "不接受 name" in str(ei.value)


def test_blank_css_string_is_rejected():
    with pytest.raises(locators.LocatorSpecError):
        locators.parse("   ")


# ===================== normalize：落盘形态（可再解析回来） =====================

def test_normalize_css_always_string():
    """手写 {"css": ...} 归一成字符串：存量形态与产物老分支都零涟漪。"""
    assert locators.normalize(".btn") == ".btn"
    assert locators.normalize({"css": ".btn"}) == ".btn"


def test_normalize_strips_internal_kind_tag():
    """存进去的必须是能再 parse 回来的形态——IR 带 kind 键，存 IR = 存一份自己解析不了的知识。"""
    assert locators.normalize({"role": "button", "name": "保存"}) == {"role": "button", "name": "保存"}
    assert locators.normalize({"label": "客户"}) == {"label": "客户"}


def test_kind_tag_is_not_a_valid_input_shape():
    """带内部 kind 标签的映射按未知键点名——文件形态只有用户形态一种。"""
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.parse({"kind": "role", "role": "button"})
    assert "kind" in str(ei.value)


def test_normalize_round_trips():
    for spec in (".btn", {"role": "button", "name": "保存"}, {"label": "客户"},
                 {"testid": "save"}, {"role": "dialog"}):
        assert locators.parse(locators.normalize(spec)) == locators.parse(spec)


# ===================== from_text：CLI/模板文本输入边界 =====================

def test_from_text_css_passthrough():
    assert locators.from_text(".a > b") == ".a > b"
    assert locators.from_text("") == ""


def test_from_text_json_object():
    assert locators.from_text('{"role": "button", "name": "保存"}') == {
        "role": "button", "name": "保存"}


def test_from_text_role_flags():
    assert locators.from_text("", role="button", name="保存") == {"role": "button", "name": "保存"}
    assert locators.from_text("", role="dialog") == {"role": "dialog"}


def test_from_text_malformed_json_is_named_not_treated_as_css():
    """以 { 开头就必须是合法 JSON：否则人会以为在写结构化描述，实则存了一份坏 CSS。"""
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.from_text('{"role": "button"')
    assert "合法 JSON" in str(ei.value)


def test_from_text_bracket_css_is_not_mistaken_for_json():
    """CSS 属性选择器就是以 [ 开头的，不能被当成 JSON——只有 { 才要求是合法 JSON。"""
    assert locators.from_text('[data-testid="save"]') == '[data-testid="save"]'
    with pytest.raises(locators.LocatorSpecError):   # 而 { 开头就必须是合法 JSON
        locators.from_text('{ single brace css }')


def test_from_text_remedy_and_role_conflict_is_named():
    with pytest.raises(locators.LocatorSpecError) as ei:
        locators.from_text(".x", role="button")
    assert "不能同时给" in str(ei.value)


# ===================== canonical：留痕文本（dict 不可切片） =====================

def test_canonical_forms():
    assert locators.canonical(".a > b") == "css=.a > b"
    assert locators.canonical({"role": "button", "name": "保存"}) == 'role=button[name="保存"]'
    assert locators.canonical({"role": "dialog"}) == "role=dialog"
    assert locators.canonical({"label": "客户"}) == "label=客户"
    assert locators.canonical({"testid": "save"}) == "testid=save"


def test_canonical_makes_dict_slice_safe():
    """真实踩点：批准路径旧写法 `selector[:120]` 收到 dict 直接 TypeError。"""
    text = locators.canonical({"role": "button", "name": "保存"})
    assert text[:120] == text and isinstance(text, str)


# ===================== resolve：唯一执行判据 =====================

def test_resolve_dispatches_each_kind():
    page = FakePage()
    assert locators.resolve(page, ".btn") == ("css", ".btn")
    assert locators.resolve(page, {"role": "button", "name": "保存"}) == ("role", "button", "保存")
    assert locators.resolve(page, {"label": "客户"}) == ("label", "客户")
    assert locators.resolve(page, {"testid": "save"}) == ("testid", "save")
    assert [c[0] for c in page.calls] == ["locator", "get_by_role", "get_by_label", "get_by_test_id"]


def test_role_name_uses_exact_match():
    """子串匹配会点错「保存并提交」：候选的 name 是无障碍名原文，必须精确匹配。"""
    page = FakePage()
    locators.resolve(page, {"role": "button", "name": "保存"})
    assert page.calls[0] == ("get_by_role", "button", "保存", {"exact": True})


def test_resolve_without_name_omits_name():
    page = FakePage()
    locators.resolve(page, {"role": "dialog"})
    assert page.calls[0] == ("get_by_role", "dialog", None, {})


# ===================== SelectorRegistry：取用 + 向后兼容 =====================

def _registry():
    return SelectorRegistry({"components": {"销售单": {"selectors": {
        "查询": {"role": "button", "name": "查询"},
        "表格": ".el-table",
    }}}})


def test_registry_get_returns_raw_value_unchanged():
    reg = _registry()
    assert reg.get("销售单", "查询") == {"role": "button", "name": "查询"}   # 原值，不预先解析
    assert reg.get("销售单", "表格") == ".el-table"                          # 老调用方零影响


def test_registry_resolve_and_describe_round_trip():
    reg = _registry()
    page = FakePage()
    assert reg.resolve(page, "销售单", "查询") == ("role", "button", "查询")
    assert reg.resolve(page, "销售单", "表格") == ("css", ".el-table")
    assert reg.describe("销售单", "查询") == 'role=button[name="查询"]'
    assert reg.describe("销售单", "表格") == "css=.el-table"


def test_registry_unregistered_still_names_key():
    with pytest.raises(KeyError) as ei:
        _registry().resolve(FakePage(), "销售单", "不存在")
    assert "选择器未注册" in str(ei.value)


def test_registry_stored_junk_is_named_at_use_time():
    """手改私有库写出坏形态：取用时点名，绝不静默当 CSS 字面量用。"""
    reg = SelectorRegistry({"components": {"坏": {"selectors": {"b": {"role": ""}}}}})
    with pytest.raises(locators.LocatorSpecError):
        reg.resolve(FakePage(), "坏", "b")

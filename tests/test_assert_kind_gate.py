"""断言明细闸：未知/腿型不相容的 `AssertSpec.kind` 必须在**生成期**点名拒绝。

## 为什么需要

`AssertSpec.kind` 的文档取值域是 `ok` / `result_code` / `note`，但三个渲染循环
（快 API / 慢 API / UI）都是 `if kind == ... elif kind == ...`——**没有 else**。

实测（2026-11-16）：

    >>> StepSpec(..., asserts=[AssertSpec("ok"), AssertSpec("http_status", expected="418")])
    >>> "\n".join(_render_api_test(spec))     # 418 不出现
    >>> "\n".join(_render_api_test_fast(spec))# 418 也不出现
    >>> "TODO" in slow                        # False —— 连 TODO 都没留

即：往断言列表塞一个渲染器不认识的 kind，**断言凭空消失，测试全绿**。这不是「支持
不了一种断言」，而是「静默降级成不加断言」——直接撞 AGENTS 红线 #7「不放宽断言」。

同族第二形态：`result_code` 只能由 API 腿渲染（UI 步骤不起请求、不产出 `result.data`）。
`_flow_asserts` 按 `save_as` 把 flow 级 assert 归到步骤上，若那个 `save_as` 落在 UI
步骤，slow renderer 的 `elif kind == "note"` 也不匹配 → 同样静默丢弃。当前 45 个 flow
里恰好没有这种组合（2026-11-16 实测 0 例），但结构上完全可达。

## 判据

闸门是 `emit_pytest._reject_unknown_assert_kinds`，唯一挂在 `render_test_module` 入口
（快慢两形态的公共收口），**不在三个渲染循环里各补一个 else**——逐处补 else 等于让
「新增一种 kind」依赖作者记得改三处，正是本仓被咬过多次的「改一漏一」。

本文件钉三件事：
1. 未知 kind 被拒（不是被丢弃）；
2. UI 腿上的 `result_code` 被拒（不是被丢弃）；
3. 合法 kind 照常放行（含 UI 腿的 `note`）——否则闸门会把正常路径翻成错误。
"""

from __future__ import annotations

import pytest

from atlas.generator.emit_pytest import (
    ASSERT_KINDS,
    AssertSpec,
    PytestEmitError,
    StepSpec,
    _render_api_test,
    _render_api_test_fast,
    _reject_unknown_assert_kinds,
    render_test_module,
)


def _api_spec(asserts, index: int = 1) -> StepSpec:
    return StepSpec(index=index, slug=f"api_{index}", intent="intent", task="api_call",
                    method="GET", path="/x", asserts=list(asserts), is_api=True)


def _ui_spec(asserts, index: int = 1) -> StepSpec:
    return StepSpec(index=index, slug=f"ui_{index}", intent="intent", task="ui_action",
                    asserts=list(asserts), is_api=False, page_object="login")


# ---------------------------------------------------------------------------
# 1. 未知 kind：拒绝而不是静默丢弃
# ---------------------------------------------------------------------------

def test_unknown_assert_kind_is_rejected_not_dropped():
    """未知 kind → PytestEmitError，且消息点名 step 与 kind。

    这条是那个实测缺陷的直接回归钉：修复前 `_render_api_test(spec)` 正常返回、
    断言文字消失，无任何信号。
    """
    spec = _api_spec([AssertSpec("ok"), AssertSpec("http_status", expected="418")])
    with pytest.raises(PytestEmitError) as ei:
        _reject_unknown_assert_kinds([spec])
    msg = str(ei.value)
    assert "http_status" in msg and "api_1" in msg, f"必须点名 kind 与步骤：{msg}"
    # 消息要指出「会被静默丢弃」这个后果，否则读者会以为只是不支持
    assert "静默" in msg or "红线" in msg, f"消息必须说明后果：{msg}"


def test_rejection_happens_before_any_rendering():
    """闸门在 `render_test_module` 入口：快慢两形态都不会产出半成品。"""
    spec = _api_spec([AssertSpec("bogus_kind")])
    for fast in (False, True):
        with pytest.raises(PytestEmitError):
            render_test_module([spec], flow_id="f", fast=fast)


def test_renderers_would_have_dropped_it_silently():
    """反向对照：证明闸门防的是真事——直接调渲染器时该断言确实不出现。

    若哪天某个渲染器开始对未知 kind 抛错（而不是静默丢），这条会红，届时把
    闸门扩大/说明即可；在闸门存在的前提下，它保证「断言文字消失」是可复现的事实
    而不是纸面推论。
    """
    spec = _api_spec([AssertSpec("ok"), AssertSpec("http_status", expected="418")])
    slow = "\n".join(_render_api_test(spec, fast=False))
    fast = "\n".join(_render_api_test_fast(spec))
    assert "418" not in slow, "慢渲染器现在会渲染未知 kind？那闸门可收窄"
    assert "418" not in fast, "快渲染器现在会渲染未知 kind？那闸门可收窄"


# ---------------------------------------------------------------------------
# 2. 腿型不相容：UI 腿不许带 result_code
# ---------------------------------------------------------------------------

def test_result_code_on_ui_leg_is_rejected():
    """UI 步骤不产出 result.data，`result_code` 断言无法渲染 → 点名拒绝。

    静默丢弃的后果尤其坏：用户以为「这条 UI 步骤还校验了 resultCode」，实际没有。
    """
    spec = _ui_spec([AssertSpec("ok"), AssertSpec("result_code", expected="0")])
    with pytest.raises(PytestEmitError) as ei:
        _reject_unknown_assert_kinds([spec])
    msg = str(ei.value)
    assert "UI" in msg and "result_code" in msg, f"必须点名腿型与 kind：{msg}"
    # 消息要给出可操作的两条补救路径
    assert "api_call" in msg or "note" in msg, f"必须给补救路径：{msg}"


def test_api_leg_still_accepts_result_code():
    """正向对照：API 腿的 `result_code` 照常放行（否则闸门误伤正常路径）。"""
    spec = _api_spec([AssertSpec("ok"), AssertSpec("result_code", expected="0")])
    _reject_unknown_assert_kinds([spec])   # 不抛
    rendered = "\n".join(_render_api_test(spec, fast=False))
    # 只断言「断言真的被渲染」——引号形态由 `_py_literal`（repr）决定，不在此钉死。
    assert "resultCode" in rendered and "== '0'" in rendered, (
        f"合法断言必须真的被渲染：{rendered}")


def test_ui_leg_accepts_note():
    """正向对照：UI 腿的 `note` 合法（渲染成 TODO 留人审）。"""
    spec = _ui_spec([AssertSpec("ok"), AssertSpec("note", note="待人审补 UI 断言")])
    _reject_unknown_assert_kinds([spec])   # 不抛


def test_ok_kind_alone_is_valid_on_both_legs():
    """`ok` 是渲染器无条件产出的存活断言，两条腿都必须放行。

    这条防「闸门把 ASSERT_KINDS 写漏一个」——`_flow_asserts` 恒造 `AssertSpec("ok")`，
    漏登记会让**所有** flow 生成失败，是本闸门最可能的自伤形态。
    """
    for spec in (_api_spec([AssertSpec("ok")]), _ui_spec([AssertSpec("ok")])):
        _reject_unknown_assert_kinds([spec])


# ---------------------------------------------------------------------------
# 3. 取值域声明本身
# ---------------------------------------------------------------------------

def test_assert_kinds_matches_renderers():
    """`ASSERT_KINDS` 必须恰是三个断言渲染循环认得的 kind 集合。

    渲染循环认得 = 出现在 `if a.kind ==` / `elif a.kind ==` 里的字面量（**变量名限定为
    `a`**：同文件里 `body.kind == "json"/"form"/"text"` 是请求体形态判定，与本闸门无关，
    不限定变量名会把它们误扫进来）。用源码扫描而不是手抄清单：新增渲染分支却忘了更新
    ASSERT_KINDS 会在这里红，反之亦然。

    （`ok` 例外：它由 `assert result.ok` 无条件产出，渲染循环不消费，但仍是合法 kind。）
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "atlas" / "generator" / "emit_pytest.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    consumed: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and len(node.comparators) == 1):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "kind"):
            continue
        # 只认断言循环里的 `a.kind`（请求体形态判定用的是 `body.kind`，不在本判据面）
        if not (isinstance(left.value, ast.Name) and left.value.id == "a"):
            continue
        comp = node.comparators[0]
        if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
            consumed.add(comp.value)
    assert consumed == {"result_code", "note"}, f"断言渲染循环实际消费的 kind 变了：{sorted(consumed)}"
    assert consumed | {"ok"} == set(ASSERT_KINDS), (
        f"ASSERT_KINDS={sorted(ASSERT_KINDS)} 与「渲染消费 ∪ {{ok}}」不符")

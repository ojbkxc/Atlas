"""生成 → 导出 闭环守卫：`atlas gen` 自己写的 flow 级断言，`atlas export-pytest` 必须读得懂。

## 挡的是什么（分析出的真实缺陷：同一产品的两半对不起来）

`emit_flow` 会给每个 api 步骤写两条 flow 级断言：

    save_as: resp_1_get-web-example-customer-code
    assert:
      - resp_1_get-web-example-customer-code.ok == true

而 `emit_pytest` 解析这些断言用的正则键位是 `(\\w+)`——`\\w` 不含 `-`，于是**读不懂自己
刚写的断言**：导出把这条断言降级成 `# TODO(人审): 待接断言（人审）：…`。产物看起来完整、
rc 0，但那一步**实际上没有存活断言**（`AssertSpec("ok")` 只在 `_flow_asserts` 里预置，
真解析到 `resultCode` 那条也一起被降级）。这就是「生成链自己制造的断言，导出链丢掉」——
与「只在真机上以『产物行为与测里钉的不一样』暴露」同族的静默降级。

## 修法两端都收

1. **根因侧**：`_save_as_name` 的 `{slug}` 改用 `dsl_utils.identifier_slug`——生成出来的
   ctx 键是合法标识符（`resp_1_get_web_example_customer_code`），与 `\\w+` 一致；
2. **兼容侧**：`_FLOW_OK_RE`/`_FLOW_RC_RE` 的键位放宽到 `[A-Za-z_][\\w-]*`——用户仓库里
   **已经生成过**的 hyphen 键 flow 不会因为这次改动而丢断言（旧产物照常可用）。

## 四条

1. `test_generated_flow_assert_roundtrip`：真跑 `generate()` 取 flow，再 `emit_pytest_from_flow`
   → 产物里没有「待接断言」TODO，且每步都有 `assert result.ok`。
2. `test_save_as_is_identifier_safe`：取值表（空格/连字符/中文/引号/反斜杠）下 `_save_as_name`
   恒为 `^[A-Za-z_]\\w*$`，且模板 `{n}/{slug}` 仍生效。
3. `test_legacy_hyphenated_save_as_still_parsed`：手工构造 hyphen 键 flow（模拟**存量**产物）
   → 断言仍被解析成真断言（不是 TODO）。
4. `test_unrecognized_assert_still_goes_to_note`：正向对照——真正认不出的断言形态仍然降级为
   TODO（放宽键位不等于「什么都认」，否则这条守卫会掩盖真丢弃）。
"""
from __future__ import annotations

import re

import pytest
import yaml

from atlas.generator import GenOptions, generate
from atlas.generator.emit_flow import _save_as_name
from atlas.generator.emit_pytest import _specs_from_flow, emit_pytest_from_flow
from atlas.generator.model import Endpoint

_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _doc(tmp_path, name="api.md"):
    path = tmp_path / name
    path.write_text(
        "# Get web example customer code\n\n"
        "## GET /web/example/customer/code\n\n"
        "| 参数 | 说明 |\n|---|---|\n| code | 客户编码 |\n",
        encoding="utf-8")
    return path


def test_generated_flow_assert_roundtrip(tmp_path):
    generate(_doc(tmp_path), tmp_path / "out", GenOptions(scenario="a"))
    flows = list((tmp_path / "out" / "flows").glob("*.yaml"))
    assert len(flows) == 1
    flow = yaml.safe_load(flows[0].read_text(encoding="utf-8"))

    src = emit_pytest_from_flow(flow)
    assert "待接断言（人审）" not in src, (
        "导出器读不懂生成器刚写的断言（降级成 TODO）——\n"
        + "\n".join(line for line in src.splitlines() if "TODO" in line))
    api_steps = sum(1 for s in flow["steps"] if s.get("task") == "api_call")
    assert src.count("assert result.ok") == api_steps, "每步都应有存活断言"
    assert list((tmp_path / "out" / "probes").glob("*.candidate.py")), "探针产物应一并写出"


def test_save_as_is_identifier_safe():
    cases = ["Get web example customer code", "查询订单列表", 'cmd"weird', "订单 \\s 查询",
             "a b c"]
    for summary in cases:
        ep = Endpoint(method="GET", path="/api/x", summary=summary)
        name = _save_as_name(2, ep)
        assert _IDENT.match(name), f"摘要 {summary!r} → save_as {name!r} 不是合法标识符"
        assert name.startswith("resp_2_")
    # 模板仍生效（下游可自定义 {n}/{slug}）
    ep = Endpoint(method="GET", path="/api/x", summary="sales order")
    assert _save_as_name(7, ep, "s{n}_{slug}") == "s7_sales_order"


def test_legacy_hyphenated_save_as_still_parsed():
    """存量 flow（旧生成器产物）里的 hyphen 键断言必须照样解析（兼容面不能因根因修复而丢）。"""
    key = "resp_1_get-web-example-customer-code"
    flow = {
        "id": "legacy-hyphen",
        "steps": [{"task": "api_call", "method": "GET", "path": "/api/x", "save_as": key}],
        "assert": [f"{key}.ok == true", f'{key}.data.resultCode == "BUG-000"'],
    }
    specs = _specs_from_flow(flow)
    kinds = [a.kind for a in specs[0].asserts]
    assert "result_code" in kinds, f"hyphen 键的 resultCode 断言被降级了：{kinds}"
    assert not [a for a in specs[0].asserts if a.kind == "note"], (
        "hyphen 键断言被降级成 TODO：" + str([a.note for a in specs[0].asserts if a.kind == "note"]))


def test_unrecognized_assert_still_goes_to_note():
    """正向对照：真正认不出的形态（不只是键位字符问题）仍必须降级成 TODO，不假装解析。"""
    key = "resp_1_order"
    flow = {
        "id": "unknown-form",
        "steps": [{"task": "api_call", "method": "GET", "path": "/api/x", "save_as": key}],
        "assert": [f"{key}.data.total > 3"],
    }
    specs = _specs_from_flow(flow)
    notes = [a.note for a in specs[0].asserts if a.kind == "note"]
    assert notes and "待接断言" in notes[0], f"未识别形态没有进 TODO：{specs[0].asserts}"


# ---- 5/6. `--naming` 是用户输入：坏模板必须是一行 + rc 2，不许裸 traceback ----

def test_bad_naming_template_is_user_error():
    from atlas.generator.emit_flow import FlowEmitError

    ep = Endpoint(method="GET", path="/api/x", summary="sales order")
    # 好模板照常
    assert _save_as_name(1, ep, "s{n}_{slug}") == "s1_sales_order"
    assert _save_as_name(1, ep) == "resp_1_sales_order"
    # 坏模板：未知占位符 / 位置参数 / 产出非标识符（含 `-`、数字开头）
    for bad in ("{oops}", "{0}_{slug}", "resp-{n}-{slug}", "{n}_{slug}_x-y", "1{n}_{slug}"):
        with pytest.raises(FlowEmitError) as exc:
            _save_as_name(1, ep, bad)
        msg = str(exc.value)
        assert "--naming" in msg, f"模板 {bad!r} 的报错没点名 --naming：{msg}"


def test_cli_bad_naming_is_one_line_rc2(tmp_path, capsys):
    """CLI 面：坏模板 → rc 2 + 一行 stderr（含 `--naming`），**不得**出现 Traceback。"""
    from atlas.cli import main

    doc = tmp_path / "api.md"
    doc.write_text("# 查询\n\n## GET /api/x\n\n| 参数 | 说明 |\n|---|---|\n| a | b |\n",
                   encoding="utf-8")
    rc = main(["gen", str(doc), "--naming", "{oops}", "-o", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert rc == 2, f"坏模板应 rc 2（输入错误），实际 {rc}"
    assert "Traceback" not in captured.err, captured.err
    assert "--naming" in captured.err, captured.err
    assert len([ln for ln in captured.err.splitlines() if ln.strip()]) <= 2, captured.err

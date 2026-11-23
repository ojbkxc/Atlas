"""flow 级断言的导出覆盖守卫：**认不出也必须留痕**，不许静默消失。

## 挡的是什么（分析出的真实缺陷：flow 级断言被整块吞掉）

`assert:` 块在 Atlas 里支持两种形态（`dsl` 静态段两种都校验）：

```yaml
assert:
  - document_no != ""                 # ① 文本表达式
  - task: assert_api                  # ② Task 形态（operation/args/expect）
    operation: sale_order_list
    expect: {resultCode: BUG-000}
```

`emit_pytest._flow_asserts` 旧实现对每条 `str(raw)` 后按 `save_as` 归属：
- ② 被 `str()` 成 `"{'task': 'assert_api', …}"`，永远不含 `save_as` → **丢弃**；
- ① 若引用的是非步骤落点（`document_no`）或压根写错键 → 同样 **丢弃**。

结果是 `atlas export-pytest` rc 0、产物看起来完整，而 flow 里的 API 断言块（含 `expect`）
在产物里一个字都不剩——「断言消失」比「断言失败」更危险（它看起来验证过）。实测：
`examples/return_from_so.yaml` 导出后 `document_no != ""` 与 `assert_api` 块双双消失。

## 修法

`emit_pytest.flow_assert_notes`（判据单点）收「不归属任何步骤」的断言成待接 TODO，由渲染层挂在
**模块头**（`render_test_module(..., flow_notes=...)`）——挂某一步会随该腿的形态丢失：慢模式
UI 腿是 skip 骨架、不渲染断言注释。`flow_notes` 缺省 None ⇒ 旧输出逐字节不变（#115 零涟漪）。

## 五条

1. `test_task_form_assert_surfaces_as_todo`：Task 形态 → TODO 里能看到 task/operation/expect；
2. `test_orphan_text_assert_surfaces_as_todo`：引用非步骤落点的文本断言 → TODO；
3. `test_attributed_assert_has_no_spurious_todo`：能归属的断言不得被误标 TODO（正向对照）；
4. `test_notes_are_deduplicated_per_flow`：同一条 flow 级断言只出一次，不随步骤数翻倍；
5. `test_real_example_flow_keeps_its_assert_blocks`：真跑 `examples/return_from_so.yaml` 的
   导出路径，产物里必须能搜到 `document_no` 与 `assert_api`（改前两者都没有）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from atlas.generator.emit_pytest import (
    _specs_from_flow,
    emit_pytest_from_flow,
    flow_assert_notes,
)

_REPO = Path(__file__).resolve().parents[1]


def _notes(flow) -> list[str]:
    return flow_assert_notes(flow, _specs_from_flow(flow))


def _flow(asserts, *, save_as="resp_1_order", steps=None):
    return {
        "id": "assert-coverage",
        "steps": steps or [{"task": "api_call", "method": "GET", "path": "/api/order",
                            "save_as": save_as}],
        "assert": asserts,
    }


def test_task_form_assert_surfaces_as_todo():
    joined = " ".join(_notes(_flow([
        {"task": "assert_api", "operation": "sale_order_list",
         "expect": {"resultCode": "BUG-000"}},
    ])))
    assert "assert_api" in joined, "Task 形态断言被静默丢弃（既没渲染也没留 TODO）"
    assert "sale_order_list" in joined and "BUG-000" in joined, joined


def test_orphan_text_assert_surfaces_as_todo():
    joined = " ".join(_notes(_flow(['document_no != ""', "resp_9_typo.ok == true"])))
    assert "document_no" in joined, f"非步骤落点的文本断言被丢弃：{joined!r}"
    assert "resp_9_typo" in joined, f"写错键的断言被丢弃：{joined!r}"


def test_attributed_assert_has_no_spurious_todo():
    flow = _flow(['resp_1_order.ok == true', 'resp_1_order.data.resultCode == "BUG-000"'])
    assert _notes(flow) == [], f"能归属的断言被误标 TODO：{_notes(flow)}"
    kinds = [a.kind for a in _specs_from_flow(flow)[0].asserts]
    assert kinds[0] == "ok" and "result_code" in kinds


def test_notes_are_deduplicated_per_flow():
    flow = _flow(
        ['document_no != ""'],
        steps=[{"task": "api_call", "method": "GET", "path": f"/api/{i}",
                "save_as": f"resp_{i}_{i}"} for i in (1, 2, 3)],
    )
    notes = _notes(flow)
    assert sum("document_no" in n for n in notes) == 1, f"重复挂载：{notes}"


def test_real_example_flow_keeps_its_assert_blocks():
    flow = yaml.safe_load((_REPO / "examples" / "return_from_so.yaml").read_text(encoding="utf-8"))
    src = emit_pytest_from_flow(flow)
    assert "document_no" in src, "示例 flow 的文本断言在产物里消失了"
    assert "assert_api" in src and "sale_order_list" in src, "示例 flow 的 Task 形态断言块消失了"
    assert "# TODO(人审): 待接断言" in src

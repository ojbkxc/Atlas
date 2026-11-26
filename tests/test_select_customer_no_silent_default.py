"""`select_customer` 不许静默兜底客户编号（MAINT-003 medium 的收口守卫）。

## 挡的是什么（2026-11-26 台账 F21）

`core_tasks()` 曾给 `SelectCustomerTask` 塞了
`defaults={"code": "CUST0000001", "code_full": "CUST00000010001"}`。
后果链是**静默查错客户**，不是崩：

```
flow 的 select_customer 步没给 code/code_full
  → 静默默认值 CUST0000001 生效
  → 拿虚构样例客户去查真实系统
  → payment_terms 取自错客户的 collectPayments
  → 后续 PT 断言、金额、保存链全部建立在这个错客户上
  → 全程无报错，结论是错的
```

这与 `demo-025` 坑点（「盲选第一行导致客户与 PT 断言全部失真」）同族——都是**用替身数据
掩盖「没给数据」**。AGENTS §1.6：数据缺失必须用 `EnvBlocked` 明确终止，禁止步骤内兜底。

## 两类的处置相反（这是本波最容易修反的地方）

- **结构旋钮**（`page_key`/`wh`/`target`）：缺了 pages.py 只会抛裸 `TypeError`，
  看得出是「调用形态不对」，给默认值正当——**保留**。
- **业务数据**（客户编号）：缺了就静默错，必须明确终止——**去掉默认值 + EnvBlocked**。

所以本守卫同时钉两个方向：①业务数据没有静默默认值；②结构旋钮的默认值**还在**
（防止有人把整块 defaults 一起删掉，那是另一个方向的回归）。

## 四条

1. `test_select_customer_has_no_silent_customer_default`：`create("select_customer")` 的
   params 里**没有** `code`/`code_full`；
2. `test_structural_defaults_are_kept`：`page_key`/`wh` 仍在（反方向回归）；
3. `test_missing_customer_code_raises_env_blocked`：不给编号 → `EnvBlocked`，消息点名
   `code`/`code_full` 与「§1.6」，**不是** TypeError；
4. `test_explicit_customer_code_passes_the_gate`：显式给了就不拦（正向对照，防闸收过头）。
"""
from __future__ import annotations

import pytest

from atlas.core import Channel, TaskMetadata
from atlas.failures import EnvBlocked
from atlas.plugins.demo import DemoPlugin
from atlas.plugins.demo.tasks import SelectCustomerTask
from atlas.dsl import TaskRegistry


def _select_task() -> SelectCustomerTask:
    meta = TaskMetadata(domain="demo", intent="选择客户并等待异步回填",
                        requires=("new_document_ready",), produces=("customer",),
                        channel=Channel.AUTO, api_operation="customer_detail",
                        ui_fallback="select_customer", read_only=False,
                        tags=("select_customer",))
    return SelectCustomerTask(meta, None)


# ── 1：业务数据没有静默默认值 ──────────────────────────────────────────────

def test_select_customer_has_no_silent_customer_default():
    registry = TaskRegistry()
    DemoPlugin().register_tasks(registry)
    params = registry.create("select_customer").params
    assert "code" not in params, f"客户编号不该有静默默认值，实际 params={params}"
    assert "code_full" not in params, f"客户全码不该有静默默认值，实际 params={params}"


# ── 2：结构旋钮的默认值必须还在（反方向回归）──────────────────────────────

def test_structural_defaults_are_kept():
    """防「把整块 defaults 一起删掉」——那样 pages.py 缺位置参只会裸 TypeError。"""
    registry = TaskRegistry()
    DemoPlugin().register_tasks(registry)
    assert registry.create("open_doc_list").params["page_key"]
    assert registry.create("click_new").params["page_key"]
    assert registry.create("ensure_warehouse").params["wh"]


# ── 3：缺编号 → EnvBlocked（不是 TypeError）──────────────────────────────

def test_missing_customer_code_raises_env_blocked():
    """UI 腿与 API 腿都要点名，而不是掉进 pages.py 的裸 TypeError。

    `perform` 的第一个动作就是判据——所以这里传一个哑 actor 即可（进不到用它）。
    """
    task = _select_task()
    with pytest.raises(EnvBlocked) as exc:
        task.perform(actor=None)
    msg = str(exc.value)
    assert "code" in msg and "code_full" in msg, f"必须点名缺哪两个键：{msg}"
    assert "1.6" in msg, "必须指向 AGENTS §1.6（禁止步骤内数据兜底），否则读者只会当成参数没传"


def test_missing_customer_code_blocks_api_leg_too():
    """API 腿同口径：不许拿 None 去 api.customer_detail。"""
    task = _select_task()
    with pytest.raises(EnvBlocked):
        task._perform_api(actor=None)


# ── 4：显式给了就放行（正向对照）──────────────────────────────────────────

@pytest.mark.parametrize("params", [{"code": "C1"}, {"code_full": "C1-0001"},
                                    {"code": "C1", "code_full": "C1-0001"}])
def test_explicit_customer_code_passes_the_gate(params):
    task = _select_task()
    task.params = {**task.params, **params}
    assert task._require_customer_code() == (params.get("code") or params.get("code_full"))
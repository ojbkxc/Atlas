"""`autopilot` wait 动作的数值旋钮值域闸：`min_rows: -1` 让「等表格就绪」第一拍即真。

## 挡的是什么（2026-11-24 实测，改前全部静默通过）

`parse_action` 只管动作名/必填键/assert 形态，`wait` 的数字**一个都没校验**，
它们在 `_execute` 里被就地 `int(...)` 成形：

```
{"action":"wait","target":"rows","min_rows":-1}
    → waits.wait_for_rows 的判据是 `count >= min_rows`：-1 让第一拍恒真，
      「表格刷新完」变成「立刻就当它好了」——后续 assert 在空表上跑，假绿

{"action":"wait","target":"rows","timeout_ms":0}
    → `int(action.get("timeout_ms") or 10000)` 的 `or` 把 0 当空值 → 静默换成 10000ms
      （模型/人写 0 想说「不等」，实际等了 10 秒）

{"action":"wait","target":"mask_gone","timeout_ms":-5000}
    → 预算为负，第一拍就 TimeoutError（报错文本会说「等 -5000ms 超时」，无从下手）

{"action":"wait","ms":0}          → 旧行为兜底路径同样被 `or 1000` 静默换值
{"action":"wait","target":"rows","min_rows":5.7}   → `int()` 静默截成 5
{"min_rows": true}                → bool 是 int 子类，静默变 1
```

与 `--fail-under -1`（`tests/test_review_code_fail_under_range.py`）、autopilot
`--max-steps 0`（`tests/test_autopilot_limit_range.py`）同族：**参数值域不校验 →
功能静默失效**。这里更阴：旋钮不是人写的命令行，而是模型输出，写坏时它自己都不知道。

## 修法与边界

`parse_action` 里对 `wait` 走 `_validate_wait`：`timeout_ms`/`ms` 正整数、`min_rows`
非负整数（bool/浮点一律不算数），抛 `ActionRejected`（解析期 rc 路径，与 assert 形态
同口径）。**不**在执行面替模型猜语义。

保留的合法边界（不能收过头）：`min_rows: 0` 是 waits 的正式契约（只等落定、如实返回
行数——「无来源单」这类业务事实不是超时），必须照旧通过。

## 七条

1. `test_negative_min_rows_rejected`：`-1` → ActionRejected（改前第一拍即真）；
2. `test_zero_min_rows_still_accepted`：`0` 合法（**正向对照**，waits 契约）；
3. `test_zero_and_negative_timeout_ms_rejected`：`0` 与 `-5000` → 拒（改前 0 静默变 10000）；
4. `test_legacy_ms_zero_rejected`：`ms: 0` → 拒（改前静默变 1000）；
5. `test_float_and_bool_rejected`：`5.7` / `true` → 拒（改前截断/变 1）；
6. `test_valid_waits_still_parse`：四种 target 的正常数字与 `ms` 照旧解析（**正向对照**）；
7. `test_message_names_field_value_and_consequence`：报错点名字段+原值+后果。
"""
from __future__ import annotations

import pytest

from atlas import autopilot


def test_negative_min_rows_rejected():
    with pytest.raises(autopilot.ActionRejected) as exc:
        autopilot.parse_action('{"action":"wait","target":"rows","min_rows":-1}')
    assert "min_rows" in str(exc.value) and "-1" in str(exc.value)


def test_zero_min_rows_still_accepted():
    """正向对照：`min_rows: 0` 是 waits 的正式契约（只等落定，合法空表不算超时）。"""
    act = autopilot.parse_action('{"action":"wait","target":"rows","min_rows":0}')
    assert act["min_rows"] == 0, "min_rows=0 必须原样保留（不能静默改成 1 / 拒掉）"


def test_zero_and_negative_timeout_ms_rejected():
    for bad in (0, -5000):
        with pytest.raises(autopilot.ActionRejected) as exc:
            autopilot.parse_action(
                '{"action":"wait","target":"mask_gone","timeout_ms":%d}' % bad)
        msg = str(exc.value)
        assert "timeout_ms" in msg and str(bad) in msg, msg


def test_legacy_ms_zero_rejected():
    """旧行为兜底路径（无 target 的固定睡眠）同样不许 0：`or 1000` 会静默换值。"""
    with pytest.raises(autopilot.ActionRejected) as exc:
        autopilot.parse_action('{"action":"wait","ms":0}')
    assert "ms" in str(exc.value)


def test_float_and_bool_rejected():
    for payload in ('{"action":"wait","target":"rows","min_rows":5.7}',
                    '{"action":"wait","target":"rows","min_rows":true}',
                    '{"action":"wait","target":"rows","timeout_ms":true}'):
        with pytest.raises(autopilot.ActionRejected):
            autopilot.parse_action(payload)


def test_valid_waits_still_parse():
    """正向对照：正常数字逐字保留（闸只碰坏形态，不改好形态）。"""
    cases = [
        ('{"action":"wait","target":"rows","table":".data-table","min_rows":5,"timeout_ms":8000}',
         {"action": "wait", "target": "rows", "table": ".data-table",
          "min_rows": 5, "timeout_ms": 8000}),
        ('{"action":"wait","target":"mask_gone","timeout_ms":3000,"family":"element-ui"}',
         {"action": "wait", "target": "mask_gone", "timeout_ms": 3000,
          "family": "element-ui"}),
        ('{"action":"wait","target":"url_change","timeout_ms":7000}',
         {"action": "wait", "target": "url_change", "timeout_ms": 7000}),
        ('{"action":"wait","ms":250}', {"action": "wait", "ms": 250}),
        ('{"action":"wait"}', {"action": "wait"}),
    ]
    for payload, expected in cases:
        assert autopilot.parse_action(payload) == expected, payload


def test_message_names_field_value_and_consequence():
    """报错要能指导修复：点名字段与原值，并说清「为什么这不是风格问题」。"""
    with pytest.raises(autopilot.ActionRejected) as exc:
        autopilot.parse_action('{"action":"wait","target":"rows","min_rows":-3}')
    msg = str(exc.value)
    assert "-3" in msg, msg
    assert "恒真" in msg or "就绪" in msg, f"消息要说清后果（否则只会改数字猜）：{msg}"

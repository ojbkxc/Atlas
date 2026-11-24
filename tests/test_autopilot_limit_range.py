"""`explore --auto` 的上限参数值域闸：负上限会把功能**静默关掉**。

## 挡的是什么（2026-11-24 实测）

`--max-steps` / `--max-seconds` 只声明了 `type=int/float`，负数被静默接受：

```python
while index < max_steps:          # max_steps=-1 → 一次都不跑
    if clock() - t0 > max_seconds:  # max_seconds=-10 → 恒真，立刻 break
```

跑完 `report.stopped_reason` 分别是 `"max_steps"` / `"max_seconds"`——读起来像
「步数用尽 / 时间到了」，**实际是什么都没做**。自主探索的价值全在「真的走几步并留证」，
负上限把这条路静默关掉、还给出一个体面的终止原因，属于误导性成功。

与 `review-code --fail-under -1`（同族、同日修）同一形态：**参数值域不校验 → 功能静默失效**。

## 修法与边界

`explore_auto` 入口判 `max_steps >= 1`（int 且非 bool）、`max_seconds > 0`，越界
`ValueError` 点名原值与后果；CLI 侧走既有的「一行 stderr + rc 2」通道（`_explore_auto`
的 ValueError 出口）。**0 也拒**：`explore --auto` 的目标就是走几步，0 步等于空转。

## 五条

1. `test_negative_max_steps_rejected`：`-1` → ValueError（改前：零迭代 + 假终止原因）；
2. `test_zero_max_steps_rejected`：`0` 同样拒（空转也会产出误导性报告）；
3. `test_negative_max_seconds_rejected`：`-10` → ValueError；
4. `test_positive_limits_still_work`：`max_steps=1` / `max_seconds=1.0` 正常跑（**正向对照**，
   不能把闸收过头；这条用真 autopilot 跑一轮离线 fake LLM）；
5. `test_bool_is_not_accepted_as_int`：`True` 不算合法步数（`isinstance(True, int)` 为真，
   旧式 `type=int` 参数化会把 bool 当 1 混过去——显式排除）。
"""
from __future__ import annotations

import pytest

from atlas.autopilot import explore_auto
from atlas.config import AtlasConfig


def _call(tmp_path, **overrides):
    """只验入口闸：给最小假对象，闸在碰页面/LLM 之前就该触发。"""
    config = AtlasConfig(root=tmp_path, env="sit", mode="run")
    kwargs = {"goal": "g", "llm": None, "page": None,
              "trace": tmp_path / "t.zip", "journal": None}
    kwargs.update(overrides)
    return explore_auto(config, **kwargs)


def test_negative_max_steps_rejected(tmp_path):
    with pytest.raises(ValueError) as exc:
        _call(tmp_path, max_steps=-1)
    assert "max-steps" in str(exc.value) and "-1" in str(exc.value)


def test_zero_max_steps_rejected(tmp_path):
    with pytest.raises(ValueError) as exc:
        _call(tmp_path, max_steps=0)
    assert "max-steps" in str(exc.value)


def test_negative_max_seconds_rejected(tmp_path):
    with pytest.raises(ValueError) as exc:
        _call(tmp_path, max_steps=1, max_seconds=-10)
    assert "max-seconds" in str(exc.value) and "-10" in str(exc.value)


def test_positive_limits_still_work(tmp_path):
    """正向对照：合法上限不被闸拦住（用同款 fake 跑一轮）。"""
    class FakeLLM:
        def complete(self, messages):
            return '{"action":"done"}'

    class FakePage:
        url = "https://example.invalid/"

        def on(self, *a, **k):
            return None

    report = explore_auto(AtlasConfig(root=tmp_path, env="sit", mode="run"), "g",
                          llm=FakeLLM(), page=FakePage(), trace=tmp_path / "t.zip",
                          journal=None, max_steps=1, max_seconds=5.0)
    assert report.stopped_reason in ("done", "max_steps", "max_seconds"), report


def test_bool_is_not_accepted_as_int(tmp_path):
    with pytest.raises(ValueError):
        _call(tmp_path, max_steps=True)

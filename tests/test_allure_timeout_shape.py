"""`run.status=timeout` 结果集的**形态守卫**：broken 且「步骤全 passed」是有意形态。

## 这条测挡什么（2026-11-24 实测确认，不是缺陷但极易被误读成缺陷）

看门狗超时（`--case-timeout`）时，`cli._supervise_case` 整树强杀子进程、写
`run.status=timeout`。此时 `steps` 里通常是**全部 ok=True**（用例真的把每一步都跑完了，
只是整体没在窗口内收尾）。Allure 结果于是长这样：

```
c4 -> broken | steps: 1 | step statuses: ['passed']
```

读的人第一反应是「矛盾：步骤全过、结果却 broken」。**这是对的**：
Allure 的 broken = 该测试没有产出可判定的结论，而看门狗强杀正是这种情况；
步骤级 passed 记录的是「这些步骤确实成功了」——两条都是事实，不互相否定。

§3.5 映射表与三处既有测（`test_waiting_operational.py` 的
`test_allure_unknown_status_still_broken[timeout]`、`test_emit_fast.py` 的
「看门狗 timeout → broken（fail-loud）」、`test_fast_artifact_executable.py` 的
「掐表≠操作族终态」）都钉住了 run 级 = broken。这轮我一度想改成
`failed/interaction`，被那三处当场挡回（见 AGENTS 活约束）。

**本文件不新增行为断言**，只把「步骤全 passed + run broken」这个具体形态连同解释固定下来：
将来有人「顺手修掉这个矛盾」时，会先在这里读到为什么不能修。

## 三条

1. `test_timeout_run_is_broken_with_passing_steps`：run 级 broken、步骤全 passed（形态本身）；
2. `test_timeout_run_is_not_passed`：**红线 #7 正向对照**——不得因为步骤全过就升成 passed；
3. `test_result_carries_fail_class_label`：label 里 `atlas.fail_class` 存在（可归因到具体类，
   不是裸 broken 无信息）。
"""
from __future__ import annotations

import json

from atlas.allure_report import emit_allure

_RUN = {
    "run_id": "timeout-1", "case": "slow-case", "env": "sit", "status": "timeout",
    "reason": "case 超时（--case-timeout 600s）：执行子进程已整树强杀",
    "steps": [{"step": "open_doc_list", "ok": True, "dur_ms": 590000},
              {"step": "click_new", "ok": True, "dur_ms": 9000}],
}


def _result(tmp_path) -> dict:
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps(_RUN, ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "allure"
    emit_allure(runs, out, clean=True)
    files = sorted(out.glob("atlas-*-result.json"))
    assert files, "没产出 result.json"
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_timeout_run_is_broken_with_passing_steps(tmp_path):
    result = _result(tmp_path)
    assert result["status"] == "broken", result["status"]
    step_statuses = [s["status"] for s in (result.get("steps") or [])]
    assert step_statuses == ["passed", "passed"], (
        f"本测的前提是「步骤全过但 run 超时」这一形态，实际步骤：{step_statuses}")


def test_timeout_run_is_not_passed(tmp_path):
    """红线 #7 正向对照：步骤全过 ≠ 用例通过（看门狗强杀 = 没有可判定结论）。"""
    assert _result(tmp_path)["status"] != "passed"


def test_result_carries_fail_class_label(tmp_path):
    labels = {item["name"]: item["value"] for item in _result(tmp_path)["labels"]}
    assert "atlas.fail_class" in labels, f"缺 fail_class label（裸 broken 无归因）：{labels}"

"""`reviewcode.json` 数值旋钮的**值域闸**：写错一个符号就能让评分与结论静默反转。

## 挡的是什么（2026-11-24 实测，改前全部静默通过）

`load_config` 只校验了「权重和 = 1」，其余旋钮 `update` 进默认值就直接用：

```
{"severity_deductions": {"high": -30}}
    → 一条 high finding 让该维度得 130 分、总分 106、grade=A
    （扣分变加分；配 --fail-under 90 就是「越糟越容易过闸」）

{"severity_deductions": {"high": "30"}}
    → 计分处 sum(f["deduction"] ...) 炸成 TypeError（裸崩溃，不指名哪个键写错）

{"critical_cap": 100}  {"critical_cap": -1}
    → 硬顶被写成量纲外的数：负数产出负分，>100 与不存在等价

{"weights": {"assertion": -0.25, "safety": 0.65, ...}}   # 和仍为 1
    → 该维度「缺陷越多 → 维度分越低 → ×负权重 → 总分越高」，评分方向反转：
      实测全净维度得 75，塞满 assertion 缺陷反而 100

{"severity_deductions": []} / {"critical_cap": true}
    → 非映射静默跳过（用户以为改过，实际一个键都没生效）/ bool 是 int 子类，True=1 悄悄生效
```

与 `--fail-under -1`（同日修，`tests/test_review_code_fail_under_range.py`）同族：
**参数值域不校验 → 门禁/评分静默失效**。错的是人的手，痛的是三个月后以为门禁在跑的人。

## 修法与边界

`load_config` 入口逐项点名：`weights` 每项 0..1、`severity_deductions` 每项非负、
`critical_cap` 0..100，bool 不算数（`True` 当权重就是 1 分）。越界抛 `ValueError`，
CLI 侧既是 `_user_error`（rc 2 + 一行），与「配置文件不存在/权重和不为 1」同一口径；
**不**悄悄取默认值（那正是本次要堵的形态）。

刻意保留了合法边界：扣分 0（某级不罚）、cap 0（有 critical 就是 0 分）、权重 0
（配平后的某维度不参与）都照常工作——值域不能收过头。

## 九条

1. `test_negative_severity_deduction_is_user_error`：`-30` → rc 2（改前总分 106 / A）；
2. `test_non_numeric_deduction_is_user_error`：`"30"` → rc 2 且点名键（改前 TypeError 裸崩）；
3. `test_negative_weight_is_user_error`：负权重 → rc 2（改前评分方向反转仍给 A）；
4. `test_critical_cap_out_of_range_is_user_error`：`-1` / `101` → rc 2；
5. `test_bool_smuggled_values_rejected`：`True` 混进权重/扣分/cap → rc 2；
6. `test_non_mapping_deductions_rejected`：`[]` → rc 2（改前静默当空气）；
7. `test_zero_boundaries_still_work`：扣分 0 / cap 0 / 权重 0 合法（**正向对照**）；
8. `test_valid_config_still_scores_the_same`：正常配置的分数逐字不变（**正向对照**：
   一条 high=15 扣分、safety 权重 0.2 → 总分 97/A，闸不该碰计分本体）；
9. `test_message_names_value_and_consequence`：报错点名原值 + 为什么（否则人只会改数字猜）。
"""
from __future__ import annotations

import json
from pathlib import Path

from atlas import review_code as rc


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".atlas-root").write_text("", encoding="utf-8")
    (tmp_path / "test_a.py").write_text(
        "def test_ok():\n    assert compute() == 3\n", encoding="utf-8")
    return tmp_path


def _config(tmp_path: Path, payload: dict, name: str = "reviewcode.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(project: Path, config: Path, capsys) -> tuple[int, str]:
    from atlas.cli import main
    rc_ = main(["--root", str(project), "review-code", str(project),
                "--config", str(config), "--format", "json"])
    return rc_, capsys.readouterr().err


def test_negative_severity_deduction_is_user_error(tmp_path, capsys):
    project = _project(tmp_path)
    rc_, err = _run(project, _config(tmp_path, {"severity_deductions": {"high": -30}}), capsys)
    assert rc_ == 2, (
        f"负扣分必须 rc 2（改前总分 106 / grade A = 缺陷变加分），实为 {rc_}")
    assert "severity_deductions" in err and "-30" in err, err


def test_non_numeric_deduction_is_user_error(tmp_path, capsys):
    """改前：`"30"` 一路走到计分处炸 TypeError，报错里看不出是哪个键。"""
    project = _project(tmp_path)
    rc_, err = _run(project, _config(tmp_path, {"severity_deductions": {"high": "30"}}), capsys)
    assert rc_ == 2, f"非数值扣分必须 rc 2（改前 TypeError 裸崩），实为 {rc_}"
    assert "severity_deductions" in err and "'high'" in err, err


def test_negative_weight_is_user_error(tmp_path, capsys):
    """改前：负权重配平后「缺陷越多总分越高」（实测全净 75、塞满缺陷 100）。"""
    weights = {"assertion": -0.25, "safety": 0.65,
               "maintainability": 0.20, "coverage": 0.20, "duplication": 0.20}
    assert abs(sum(weights.values()) - 1.0) < 1e-9, "本用例要的是「和仍为 1」的负权重"
    project = _project(tmp_path)
    rc_, err = _run(project, _config(tmp_path, {"weights": weights}), capsys)
    assert rc_ == 2, f"负权重必须 rc 2（改前它静默反转评分方向），实为 {rc_}"
    assert "weights" in err and "assertion" in err, err


def test_critical_cap_out_of_range_is_user_error(tmp_path, capsys):
    project = _project(tmp_path)
    for bad in (-1, 101):
        rc_, err = _run(project, _config(tmp_path, {"critical_cap": bad}), capsys)
        assert rc_ == 2, f"critical_cap={bad} 必须 rc 2，实为 {rc_}"
        assert "critical_cap" in err and str(bad) in err, err


def test_bool_smuggled_values_rejected(tmp_path, capsys):
    """bool 是 int 的子类：`True` 当扣分静默变 1、当权重静默变满值——必须当「写错」。"""
    project = _project(tmp_path)
    payloads = [
        {"weights": {"safety": True}},
        {"severity_deductions": {"high": True}},
        {"critical_cap": True},
    ]
    for payload in payloads:
        rc_, err = _run(project, _config(tmp_path, payload), capsys)
        assert rc_ == 2, f"{payload} 里的 bool 必须 rc 2，实为 {rc_}"
        assert "True" in err, f"报错要点名原值：{err}"


def test_non_mapping_deductions_rejected(tmp_path, capsys):
    """改前 `if isinstance(sd, dict)` 把整块配置当空气：用户以为改过，实际全是内置值。"""
    project = _project(tmp_path)
    rc_, err = _run(project, _config(tmp_path, {"severity_deductions": []}), capsys)
    assert rc_ == 2, f"非映射扣分必须 rc 2（改前静默忽略），实为 {rc_}"
    assert "severity_deductions" in err and "list" in err, err


def test_zero_boundaries_still_work(tmp_path, capsys):
    """正向对照：合法边界不能被值域闸误伤。

    扣分 0（该级不罚）、cap 0（有 critical 就 0 分）、权重 0（配平后不参与）都合法。
    """
    project = _project(tmp_path)
    ok_cases = [
        {"severity_deductions": {"high": 0}},
        {"critical_cap": 0},
        {"weights": {"assertion": 0.0, "safety": 0.45,
                     "maintainability": 0.20, "coverage": 0.20, "duplication": 0.15}},
    ]
    for payload in ok_cases:
        rc_, err = _run(project, _config(tmp_path, payload), capsys)
        assert rc_ in (0, 1), f"{payload} 是合法边界，不该被判成用户错误（rc {rc_}）：{err[:200]}"


def test_valid_config_still_scores_the_same(tmp_path):
    """正向对照：闸只碰入口，不改计分本体（一条 high=15、safety 权重 0.2 → 97/A）。"""
    cfg = rc.load_config(_config(tmp_path, {"severity_deductions": {"high": 15}}))
    findings = [{"dimension": "safety", "severity": "high", "deduction": 15, "rule_id": "R1"}]
    score = rc.score_findings(findings, cfg)
    assert score["total"] == 97, f"合法配置的分数不该变（期望 97，实得 {score['total']}）"
    assert score["grade"] == "A"


def test_message_names_value_and_consequence(tmp_path, capsys):
    """报错要能指导修复：点名键与原值，并说清「为什么这不是风格问题」。"""
    project = _project(tmp_path)
    _, err = _run(project, _config(tmp_path, {"severity_deductions": {"critical": -50}}), capsys)
    assert "-50" in err, err
    assert "加减" in err or "加分" in err, f"消息要说清后果：{err}"

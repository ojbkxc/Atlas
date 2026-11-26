"""**门禁/判据不得放宽**的守卫——「挑不出毛病」的防伪层。

## 挡的是什么

本目标明令「允许改门禁/判据，但不得放宽」。这条禁令最容易在两种场合被默默违反：

1. **CI 的 ruff 判据缩水**：把 `--select F401,F841,F811,E402` 里删掉几条，CI 立刻变绿，
   而「变绿」不是「问题修了」。
2. **review-code 的规则降档**：把 critical 改成 medium，`rc` 判据就不再触发——报告看着
   分数没变，闸门已经关了。

这两种都**不会让任何既有测试变红**（它们改的是「什么算问题」而不是「代码对不对」），
所以必须单独钉住。否则下一波加了 new finding、有人顺手降档，历史结论就成了假的。

## 判据

- **只许更严或不变**：ruff `--select` 列表的集合必须是 `{F401,F841,F811,E402}` 的**超集**；
  review-code 的 critical/high 规则**不得降档**（严重度 rank 只许升）。
- **必需 doctor 检查不得移除**：`docker_assets_consistent` / `boundary_assets_consistent`
  是 CI 在裸 clone 上唯一能验的仓库内资产判据。
- **不得新增声明式 skip/xfail**：无条件 `mark.skip` / `mark.xfail` 是「永久跳过」，
  它让整条用例静默不跑——这正是红线 #7 里「把失败改 SKIP」的形态。当前冻结值为 **0**。
  条件跳过（`mark.skipif`，如按平台/版本）是合法的，但**冻结基线 = 6**：涨了必须显式决策
  （多一条条件跳就多一个「在别的环境里没人跑」的可能）。真要加必须同步改这里的基线并在
  commit 里写明理由。

> 扫描时**排除本文件自身**：本守卫的说明文字里不可避免会提到这些标记名（写过一次，
> 结果把自己扫成了违规）。自证条款用别的写法验证，不靠字面量。

## 五条

1. `test_ci_ruff_select_list_not_reduced`：两处 `--select` 都是那 4 条的超集；
2. `test_ci_required_doctor_checks_present`：两个必需资产检查仍在；
3. `test_review_code_gate_severities_not_downgraded`：18 条 critical/high 规则严重度不得降；
4. `test_no_declarative_skip_or_xfail`：声明式 skip/xfail 计数仍为 0；
5. `test_guard_can_still_catch_loosening`：**自证**——把判据喂缩水的样本，必须点名
   （防守卫因正则写坏而恒绿）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from atlas.review_code import RULES

_REPO = Path(__file__).resolve().parents[1]
_CI = _REPO / ".github" / "workflows" / "ci.yml"

_REQUIRED_SELECT = {"F401", "F841", "F811", "E402"}
_REQUIRED_DOCTOR = ("docker_assets_consistent", "boundary_assets_consistent")

# 冻结快照：这些规则是门禁的牙齿，严重度只许升不许降
_FROZEN_GATE_RULES = {
    "ASSERT-001": "critical", "ASSERT-004": "critical", "GREEN-001": "critical",
    "GREEN-002": "critical", "GREEN-003": "critical", "GREEN-006": "critical",
    "MAINT-005": "critical", "SAFE-001": "critical", "SAFE-002": "critical",
    "SAFE-003": "critical",
    "ASSERT-002": "high", "ASSERT-006": "high", "COV-001": "high", "GREEN-005": "high",
    "MAINT-001": "high", "MAINT-006": "high", "SAFE-004": "high", "SAFE-005": "high",
}
_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# 冻结基线：无条件跳过 = 0；条件跳过（skipif）= 现有 6 条，涨了要显式决策
_SKIP_BASELINE = {"skip": 0, "xfail": 0, "skipif": 6}
_SELF = Path(__file__).resolve()


def _select_lists(text: str) -> list[set[str]]:
    """从 CI 文本里抽出每处 `ruff check … --select A,B,C` 的规则集合。"""
    out = []
    for m in re.finditer(r"ruff check[^\n]*--select\s+([A-Za-z0-9,]+)", text):
        out.append({c.strip() for c in m.group(1).split(",") if c.strip()})
    return out


def _loosened_selects(text: str) -> list[str]:
    problems = []
    for i, got in enumerate(_select_lists(text), 1):
        missing = _REQUIRED_SELECT - got
        if missing:
            problems.append(f"第 {i} 处 --select 少了 {sorted(missing)}（现有 {sorted(got)}）")
    return problems


# ── 1：CI ruff 判据不得缩水 ───────────────────────────────────────────────

def test_ci_ruff_select_list_not_reduced():
    problems = _loosened_selects(_CI.read_text(encoding="utf-8"))
    assert not problems, (
        "CI 的 ruff 判据被收缩了——CI 变绿不等于问题修了：\n" + "\n".join(problems))


# ── 2：必需 doctor 检查不得移除 ───────────────────────────────────────────

def test_ci_required_doctor_checks_present():
    text = _CI.read_text(encoding="utf-8")
    missing = [k for k in _REQUIRED_DOCTOR if k not in text]
    assert not missing, (
        f"CI 移除了裸 clone 上唯一可验的仓库内资产判据：{missing}"
        f"（删掉它们等于让 docker/boundary 漂移再也不会被 CI 抓到）")
    # 正向对照：required 元组必须还在，且是**作为 required** 出现的
    assert re.search(r"required\s*=\s*\([^)]*docker_assets_consistent", text), \
        "docker_assets_consistent 不在 required 元组里了（可能被挪去做只打印不判定）"


# ── 3：review-code 门禁规则不得降档 ───────────────────────────────────────

def test_review_code_gate_severities_not_downgraded():
    downgraded = []
    for rule, frozen in _FROZEN_GATE_RULES.items():
        assert rule in RULES, f"门禁规则 {rule} 被整个删掉了（比降档更彻底）"
        current = RULES[rule][2]
        if _RANK.get(current, -1) < _RANK[frozen]:
            downgraded.append(f"{rule}: {frozen} → {current}")
    assert not downgraded, (
        "review-code 门禁规则被降档（rc 判据会因此不再触发，报告看着分数没变而闸门已关）：\n"
        + "\n".join(downgraded))


# ── 4：不得新增声明式 skip/xfail ──────────────────────────────────────────

def test_no_declarative_skip_or_xfail():
    hits: dict[str, list[str]] = {"skip": [], "xfail": [], "skipif": []}
    for path in sorted((_REPO / "tests").rglob("*.py")):
        if path.resolve() == _SELF:
            continue                      # 本文件的说明文字会提到这些标记名
        text = path.read_text(encoding="utf-8")
        for kind in hits:
            for m in re.finditer(rf"pytest\.mark\.{kind}\s*\(", text):
                line = text.count("\n", 0, m.start()) + 1
                hits[kind].append(f"{path.relative_to(_REPO)}:{line}")
    problems = []
    for kind, baseline in _SKIP_BASELINE.items():
        if len(hits[kind]) > baseline:
            problems.append(f"{kind}: {baseline} → {len(hits[kind])} 条  {hits[kind]}")
    assert not problems, (
        "新增了跳过标记——无条件 skip/xfail 是红线 #7「把失败改 SKIP」的形态；"
        "条件 skipif 涨了也应是一次显式决策。若确有必要，同步修改本守卫的 "
        "_SKIP_BASELINE 并在 commit 里写明理由：\n" + "\n".join(problems))


# ── 5：守卫自证 ──────────────────────────────────────────────────────────

def test_guard_can_still_catch_loosening():
    """喂缩水样本，判据必须点名——否则上面几条的「全绿」没有意义。"""
    loosened = "run: python -m ruff check src --select F401,F841"
    assert _loosened_selects(loosened), "缩水的 --select 未被检出"
    strict = "run: python -m ruff check src --select F401,F841,F811,E402,F821"
    assert not _loosened_selects(strict), "加严（超集）被误判成缩水"

    # 降档也要能检出
    sample = dict(_FROZEN_GATE_RULES)
    sample["SAFE-001"] = "high"       # 人为降一档
    assert _RANK[sample["SAFE-001"]] < _RANK["critical"], "rank 比较本身要有区分度"


def test_frozen_snapshot_matches_reality():
    """冻结表不得与现状漂移：现状必须**等于或严于**冻结值，且冻结的规则都还在。

    这条与第 3 条的区别：第 3 条只查「没降」，这条额外要求冻结表本身是活的
    （若有人把某规则升到 critical，本表应随之更新，否则下次真降档会被旧基线放过）。
    """
    assert len(_FROZEN_GATE_RULES) == 18, "冻结表条数变了——请确认是有意调整"
    for rule, sev in _FROZEN_GATE_RULES.items():
        assert RULES[rule][2] == sev, (
            f"{rule} 现状 {RULES[rule][2]} 与冻结 {sev} 不一致。"
            f"若是有意调整严重度，请同步更新本守卫的冻结表（这是显式决策点）")
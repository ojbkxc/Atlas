"""验证计数守卫：README/AGENTS/CLAUDE 里「当前 passed 数」必须互相一致。

## 为什么需要

这份仓库把「当前验证基线」写在**四个位置**（README 徽章 / README「当前验证边界」/
AGENTS §2 / CLAUDE FAQ）。git log 里有 **7 个 commit 专门在做「四处落数」**
（`docs: 四处落数 2446`、`docs(wave4): 修三处计数残留`、`docs: 四处计数落 2404 + 修
AGENTS §2「三处/四处」措辞矛盾` …）——每次改代码都要人肉同步四处，且已经漏过不止一次。

`AGENTS.md:75` 自己把这条列为纪律：「波次实施者不要各自改数（改一漏一 #92 的文档版）」。
**但纪律写在文档里不会变红。** 这条测试把它变成会变红的东西。

## 判据

取「文档里声明为当前基线」的 passed 数（按各自固定前缀匹配，不做全文扫数字——正文
里满是历史波次的 +N 与行号，全文扫必然假阳性），要求**四处完全相等**。

守卫**不**校验「这个数是否等于真实 collect 数」——那需要跑 collect（慢，且 CI 上
收集数会随平台变化）。真实性由 lead 按 AGENTS 的「verifier 当日实跑 + 收集绿双证」
流程保证；本条只保证**四个位置说同一句话**。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 每个位置一条**锚定**正则（锚定前缀是刻意的：放宽成全文档扫数字会把历史波次的
# `+35`、行号、年份全扫进来，假阳性多到不可用）。
_SITES = {
    "README 徽章": (REPO_ROOT / "README.md",
                 re.compile(r"badge/tests-(\d+)%20passed")),
    "README 当前验证边界": (REPO_ROOT / "README.md",
                      re.compile(r"离线测试：\*\*(\d+) passed / 0 failed\*\*")),
    "AGENTS §2 基线": (REPO_ROOT / "AGENTS.md",
                    re.compile(r"当前基线：\*\*(\d+) passed\*\*")),
    "CLAUDE FAQ": (REPO_ROOT / "CLAUDE.md",
                re.compile(r"离线验证 \*\*(\d+) passed / 0 failed\*\*")),
}


def _declared_counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for label, (path, pattern) in _SITES.items():
        text = path.read_text(encoding="utf-8")
        matches = pattern.findall(text)
        assert matches, (
            f"{label}：在 {path.name} 里找不到声明基线（正则 {pattern.pattern!r}）。"
            f"多半是那句话被改写/换行了——改了文案请同步改本条守卫的锚定正则，"
            f"不然这条守卫会安静地失去覆盖。")
        values = {int(m) for m in matches}
        assert len(values) == 1, f"{label} 内部自相矛盾：同一文件里出现多个值 {sorted(values)}"
        out[label] = values.pop()
    return out


def test_declared_test_counts_agree_across_all_sites():
    """四个位置声明的 passed 数必须完全相同。

    红的时候先看清「哪个位置旧了」——不要四个一起改成你想要的值再宣布一致，
    那样只是把不一致藏起来。真实数以 `PYTHONPATH=src python -m pytest -q` 为准。
    """
    counts = _declared_counts()
    distinct = set(counts.values())
    assert len(distinct) == 1, (
        "「当前验证基线」在四处说了不同的话（改一漏一 #92 的文档版）：\n  "
        + "\n  ".join(f"{label}: {n} passed" for label, n in counts.items())
        + "\n真实数以 `PYTHONPATH=src python -m pytest -q` 为准，四处必须同数。")


def test_count_guard_is_not_vacuous():
    """正向对照：四条锚定正则都必须真命中，否则上面的「四值相等」可能是空集相等。"""
    counts = _declared_counts()
    assert len(counts) == len(_SITES) == 4
    assert all(isinstance(v, int) and v > 0 for v in counts.values()), counts


def test_collect_count_matches_declared_when_repo_is_clean():
    """当前仓库的 collect 数应等于声明基线——只在能秒级收集时才跑。

    这是**弱**校验：`--collect-only` 只证明「用例条数」，不证明它们全绿（那要跑全量）。
    但它能抓住最常见的漂移形态：加了测试却没落数。收集失败（环境缺依赖）时跳过，
    不把「收集不了」伪装成通过。
    """
    import subprocess
    import sys

    declared = _declared_counts()["AGENTS §2 基线"]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    if proc.returncode not in (0, 5):
        pytest.skip(f"collect 失败，跳过（不把失败伪装成通过）：{proc.stderr[-300:]}")
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if not m:
        pytest.skip(f"collect 输出里没找到条数：{proc.stdout[-300:]}")
    collected = int(m.group(1))
    assert collected == declared, (
        f"collect 到 {collected} 条，但文档声明基线 {declared}——"
        f"改了测试就该同数（四处一起改；本守卫会帮你确认它们一致）")

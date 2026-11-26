"""质量台账 ↔ 实测 的对账守卫（`docs/quality-ledger.md`）。

## 挡的是什么

`docs/quality-ledger.md` 是本项目「每一项已知质量问题都有处置」的唯一分母。台账的价值
**完全依赖它与代码实况不漂移**：一旦有人改了代码却没更新台账，或台账写了不存在的
位置，它就退化成一份手写愿望清单——看着齐全，实际全不可信。

所以把「台账条目数 == 实测项数」变成会变红的东西。**不靠目测**（这是台账验收契约原文）。

## 为什么这里不直接跑 review-code

跑一次 `atlas review-code src` 要 ~30s，且依赖子进程编码环境（Windows 下 `text=True`
会因 GBK 解码把 stdout 变成 None——见 `scripts/quality_inventory.py` 里的注释）。放进
每次 `pytest -q` 太贵。因此本文件钉的是**漂移的低成本征兆**：

1. 编号齐全（F01–F27 / D01–D21 / L01–L02，且无重复、无缺号）；
2. F 项与 D 项的 `file:line` 仍能解析（文件存在、行号在范围内）；
3. D 项的位置仍带待办标记（D21 已登记为「非标记」，反向断言它**不**该带）；
4. 台账表头声明的分母（27/21/2）与解析出的行数一致——只改表头不改表、或反之，都会红。

**完整清点**（含 review-code 真实条数）走
`PYTHONPATH=src python scripts/quality_inventory.py`——它是台账的**产出方**，不是测试。

## 六条

1. `test_ledger_ids_are_complete_and_unique`：F01–F27、D01–D21、L01–L02 无缺号无重复；
2. `test_ledger_declares_matching_denominator`：表头声明的 27/21/2 与解析行数一致；
3. `test_finding_locations_still_resolve`：每条 F 的 file:line 可解析；
4. `test_todo_locations_still_carry_a_marker`：每条 D（除 D21）位置仍含标记；
5. `test_d21_is_registered_as_false_positive`：D21 的位置确实**不**含真标记（它是 `\\uXXXX`）；
6. `test_ledger_can_still_catch_drift`：**自证**——把一条位置改成不存在的行号，
   校验函数必须点名（防守卫因正则写坏而恒绿）。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LEDGER = _REPO / "docs" / "quality-ledger.md"
_SRC = _REPO / "src" / "atlas"

# 表格行形态：| F01 | `agent.py:145` | …  /  | **F05** | `cli.py:1291` | …
# 只认**表格行**（行首 `|`），散文与清单里的同号引用不算条目——否则「四、要动手清单」
# 会把 F21/F05 再算一遍，编号唯一性就假红了。
_LOC_ROW = re.compile(r"^\|\s*\**([FD]\d{2})\**\s*\|\s*`([^`]+):(\d+)`", re.M)
# L 项无路径（它们是「机制缺失」不是某一行），只认表格首列
_L_ROW = re.compile(r"^\|\s*\**L(\d{2})\**\s*\|", re.M)
_MARKER = re.compile(r"TODO|FIXME|XXX|待补|留白")


def _loc_rows() -> list[tuple[str, str, int]]:
    return [(m.group(1), m.group(2), int(m.group(3)))
            for m in _LOC_ROW.finditer(_LEDGER.read_text(encoding="utf-8"))]


def _l_ids() -> list[str]:
    return [f"L{m.group(1)}" for m in _L_ROW.finditer(_LEDGER.read_text(encoding="utf-8"))]


def _problems(rows: list[tuple[str, str, int]]) -> list[str]:
    """逐条核 file:line 可解析 + D 项标记仍在。返回问题描述列表（空=全好）。"""
    out: list[str] = []
    for entry, path, line in rows:
        full = _SRC / path
        if not full.is_file():
            out.append(f"{entry}: 文件不存在 src/atlas/{path}")
            continue
        lines = full.read_text(encoding="utf-8").splitlines()
        if line > len(lines):
            out.append(f"{entry}: src/atlas/{path}:{line} 超出行数 {len(lines)}")
            continue
        if entry.startswith("D") and entry != "D21":
            window = "\n".join(lines[max(0, line - 3):line + 2])
            if not _MARKER.search(window):
                out.append(f"{entry}: src/atlas/{path}:{line} 附近已无待办标记"
                           f"（台账说这里有，实测没有）")
    return out


# ── 1：编号齐全且唯一 ────────────────────────────────────────────────────

def test_ledger_ids_are_complete_and_unique():
    ids = [r[0] for r in _loc_rows()] + _l_ids()
    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, f"台账编号有重复：{dup}"

    for prefix, n in (("F", 27), ("D", 21), ("L", 2)):
        got = sorted(i for i in ids if i.startswith(prefix))
        want = [f"{prefix}{k:02d}" for k in range(1, n + 1)]
        assert got == want, (
            f"{prefix} 项编号不全：缺 {sorted(set(want) - set(got))}，"
            f"多 {sorted(set(got) - set(want))}")


# ── 2：表头分母与实际行数一致 ────────────────────────────────────────────

def test_ledger_declares_matching_denominator():
    text = _LEDGER.read_text(encoding="utf-8")
    rows = _loc_rows()
    counts = {
        "F": sum(1 for r in rows if r[0].startswith("F")),
        "D": sum(1 for r in rows if r[0].startswith("D")),
        "L": len(_l_ids()),
    }
    assert counts == {"F": 27, "D": 21, "L": 2}, f"解析到 {counts}，与台账声明的 27/21/2 不符"
    for token in ("**27**", "**21**", "**2**"):
        assert token in text, f"台账表头缺分母声明 {token}"


# ── 3/4：位置仍可解析、标记仍在 ──────────────────────────────────────────

def test_finding_locations_still_resolve():
    bad = [p for p in _problems([r for r in _loc_rows() if r[0].startswith("F")])
           if p.startswith("F")]
    assert not bad, "F 项位置已不可解析（代码挪了但台账没跟）：\n" + "\n".join(bad)


def test_todo_locations_still_carry_a_marker():
    bad = [p for p in _problems([r for r in _loc_rows() if r[0].startswith("D")])
           if p.startswith("D")]
    assert not bad, "D 项位置已无待办标记（要么标记被删、要么台账没跟）：\n" + "\n".join(bad)


# ── 5：D21 的反向断言 ────────────────────────────────────────────────────

def test_d21_is_registered_as_false_positive():
    """D21 登记为「`\\uXXXX` 误匹配」——它的位置**不该**含真待办标记。

    反向断言的意义：若哪天 D21 那行真的被改成 TODO，说明「误匹配」的登记过期了，
    台账必须更新（否则以后清点会继续把它当噪声忽略）。
    """
    row = next(r for r in _loc_rows() if r[0] == "D21")
    _, path, line = row
    lines = (_SRC / path).read_text(encoding="utf-8").splitlines()
    window = "\n".join(lines[max(0, line - 3):line + 2])
    assert "TODO" not in window and "FIXME" not in window, (
        f"D21 登记为误匹配，但 src/atlas/{path}:{line} 附近出现了真 TODO/FIXME——"
        f"台账需要更新")


# ── 6：守卫自证 ──────────────────────────────────────────────────────────

def test_ledger_can_still_catch_drift():
    """把一条位置改成不存在的行号，校验函数必须点名——否则上面的「全绿」没有意义。"""
    real = _loc_rows()
    assert real, "台账一条都没解析出来，正则已失效"
    entry, path, _ = next(r for r in real if r[0].startswith("F"))
    problems = _problems([(entry, path, 999_999)])
    assert problems and problems[0].startswith(entry), f"漂移未被检出：{problems}"

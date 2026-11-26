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


def _loc_rows_with_text() -> list[tuple[str, str, int, str]]:
    """带整行原文的条目（需要用处置列判断状态时用）。"""
    out = []
    for ln in _LEDGER.read_text(encoding="utf-8").splitlines():
        m = _LOC_ROW.match(ln)
        if m:
            out.append((m.group(1), m.group(2), int(m.group(3)), ln))
    return out


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


def test_every_row_declares_judgment_and_disposition():
    """每条 F/D/L 的「判定」与「处置」两列必须非空，且判定落在受控词汇内。

    这条守的是目标的**字面要求**：「每一项已知质量问题都有**明确处置**」。
    一条只有位置、没写判定或处置的行，就是「知道了但没决定」——正是台账要消灭的状态。
    另外把判定词表收在测试里：新造一个同义词（比如「以后再看」）应该失败，
    因为那等于把「不做+理由」偷渡成「待定」。
    """
    allowed = {
        # F 类
        "真缺陷", "理由成立", "理由成立但缺注释", "文档噪声", "合法类型抑制",
        # D 类
        "设计留白", "显式留白", "文档描述", "产品特性", "产品文案", "非标记（grep 误匹配）",
        "非标记",
        # L 类
        "真缺口", "环境未实测",
    }
    rows = []
    for ln in _LEDGER.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("|"):
            continue
        m = re.match(r"^\|\s*\**([FDL]\d{2})\**\s*\|", ln)
        if not m:
            continue
        cells = [c.strip().strip("*") for c in ln.strip().strip("|").split("|")]
        # 只认**带位置的**条目：F/D 的第 2 列必须是 `path:line`。散文里的同号引用
        # （如台账「双向点明抽查」表 `| D01 | 代码侧理由… |`）第 2 列是说明文字，不是
        # 条目——不排除它们会被当成「列数不足的坏行」（实测踩到过一次）。
        if m.group(1)[0] in "FD" and ":" not in cells[1]:
            continue
        rows.append((m.group(1), cells))
    assert len(rows) == 50, f"只解析到 {len(rows)} 条台账行，应为 50"

    problems = []
    for entry, cells in rows:
        if len(cells) < 5:
            problems.append(f"{entry}: 列数不足（{len(cells)}，需要 编号|位置|判定|处置|证据）")
            continue
        # L 表多一列（编号|项|判定|处置|证据/理由），判定仍在第 3、处置第 4
        judgment, disposition = cells[2], cells[3]
        if not judgment:
            problems.append(f"{entry}: 判定列为空")
        elif not any(a in judgment for a in allowed):
            problems.append(f"{entry}: 判定 {judgment!r} 不在受控词汇表内")
        if not disposition:
            problems.append(f"{entry}: 处置列为空（知道了但没决定）")
    assert not problems, "台账条目未给明确处置：\n" + "\n".join(problems)


def test_patched_rows_carry_rationale():
    """标 `已补` 的条目，其位置附近**必须**有紧邻的理由注释。

    为什么单钉这条：`已补` 不进对账恒等式（它不消除 finding，只降低「理由不可核」），
    于是它成了最容易退化成一词空话的状态——「我补了」不可核就等于没补。这里把
    「补了」变成可核事实：注释必须在 `except` 行上溯 3 行或 body 首行内。
    """
    patched = [r for r in _loc_rows_with_text() if "已补" in r[3]]
    if not patched:
        pytest.skip("台账当前没有已补条目（F05/F06/F12 收口后才会出现）")
    offenders = []
    for entry, path, line, _ in patched:
        lines = (_SRC / path).read_text(encoding="utf-8").splitlines()
        window = lines[max(0, line - 4):min(len(lines), line + 2)]
        if not any(ln.lstrip().startswith("#") for ln in window):
            offenders.append(f"{entry}: src/atlas/{path}:{line} 附近无理由注释")
    assert not offenders, (
        "台账标了已补但代码里找不到紧邻的理由注释（「已补」退化成空话）：\n"
        + "\n".join(offenders))


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

"""兼容形态不许**静默丢弃** flow 级 assert（2026-11-17 结构审计抓出）。

## 缺陷形态

同一份 flow 在三个出口的断言语义曾经不一致：

| 出口 | flow 级 `assert` |
|---|---|
| `atlas run` | `dsl._run_asserts` 真的执行 ✅ |
| `atlas export-pytest` / `gen` | `emit_pytest._flow_asserts` 转成 `AssertSpec` ✅ |
| `--compat legacy-suite` | **完全没被带走，且不报错** ❌ |

`validate_flow` 只校验 assert 的**形态与引用**，不知道「这个产物形态用不上它」——
所以 `atlas compile` 绿、`atlas run` 校验、兼容包出包也 rc 0，**断言凭空消失**。
这是本仓反复咬到的形态（断言静默消失 = 红线 #7 的输入面；同族见 wave6 的
「影子层遮蔽」与 `AssertSpec` 未知 kind 静默丢弃）。

实测（2026-11-17）：34 个 compat-eligible flow 里当前 0 个带 flow 级 assert，
即**没有存量数据受影响**——但结构上完全可达（一条 `assert:` 加进任何 legacy_case flow 即命中）。

## 处置选择

**点名拒绝**，不实现平行断言渲染。理由：兼容形态的断言语义由旧套件自己承担是设计承诺
（C.2「整条委托旧 run_case_py，断言零改写」），在这里补一套渲染层等于造第二把尺（#92），
且与「产物不 import atlas」的自包含纪律冲突。这与同函数对 `api_call` / `page_object` 的
既有立场完全一致（「此处拒绝而非造第二套委托」）。

## 判据

1. 带 flow 级 assert 的兼容出包 → `CompatEmitError`，消息点名两条补救路径；
2. 不带 assert 的照常放行（否则闸门把正常路径翻成错误，#115）；
3. 闸门**先于任何写盘**（拒绝时产物目录不得被创建——与 F-9 闸同纪律）；
4. CLI 与程序化入口都经此闸（同函数，判据零复制）。
"""

from __future__ import annotations

import pytest

from atlas.generator.emit_compat import CompatEmitError, compat_specs_from_flow
from atlas.generator.emit_pytest import emit_pytest_package


def _flow_with_assert() -> dict:
    """一条合规的兼容 flow + 一条 flow 级 assert。"""
    return {
        "id": "probe-with-assert",
        "assert": ["resp_1.resultCode == '0'"],
        "steps": [
            {"task": "legacy_case",
             "module": "suite/01_case/case.yaml",
             "case_id": "TC1", "write": False, "skip_postcondition": True},
        ],
    }


def _flow_without_assert() -> dict:
    return {
        "id": "probe-plain",
        "steps": [
            {"task": "legacy_case",
             "module": "suite/01_case/case.yaml",
             "case_id": "TC1", "write": False, "skip_postcondition": True},
        ],
    }


def test_flow_level_assert_is_rejected_not_silently_dropped():
    """核心判据：带 assert 的兼容出包必须点名拒绝，而不是无声丢弃。"""
    with pytest.raises(CompatEmitError) as ei:
        compat_specs_from_flow(_flow_with_assert())
    msg = str(ei.value)
    assert "assert" in msg.lower(), f"消息必须点名词是 assert 块的问题：{msg}"
    # 必须说清后果（读者要能理解为什么这不是"暂时不支持"）
    assert "丢弃" in msg or "消失" in msg, f"消息必须说明旧行为是静默丢弃：{msg}"
    # 必须给出两条可操作路径
    assert "legacy" in msg or "旧套件" in msg, f"必须给出「写进旧套件」这条路：{msg}"
    assert "compat none" in msg, f"必须给出「换 --compat none」这条路：{msg}"


def test_plain_compat_flow_is_unaffected():
    """正向对照：不带 assert 的合法兼容 flow 照常出包（不然闸门误伤正常路径）。"""
    steps = compat_specs_from_flow(_flow_without_assert())
    assert len(steps) == 1 and steps[0].case_id == "TC1"


def test_empty_assert_list_is_allowed():
    """`assert: []` 是「显式声明没有断言」，不是「有断言被丢」——必须放行。

    这条防闸门写成 `if "assert" in flow`（键在即拒），那会把空列表这种明确表态也拒掉。
    """
    flow = _flow_without_assert()
    flow["assert"] = []
    steps = compat_specs_from_flow(flow)
    assert len(steps) == 1


def test_rejection_happens_before_any_write(tmp_path):
    """闸门先于写盘：拒绝时产物目录不得被创建（零残留，与 F-9 闸同纪律）。

    注：`--compat legacy-suite` 按组合闸要求必须与 `--fast` 同用（`compat_combo_problem`），
    不传 fast 会先撞组合闸而不是本闸门——所以这里两个都传，保证测的是**断言闸**那条路径。
    """
    out = tmp_path / "pkg"
    with pytest.raises(CompatEmitError):
        emit_pytest_package(out_dir=out, flow=_flow_with_assert(),
                            compat="legacy-suite", fast=True)
    assert not out.exists(), "拒绝必须发生在 mkdir 之前——不得留半成品目录"


def test_rejection_message_reaches_the_cli_path(tmp_path):
    """CLI 与程序化入口共用同一闸（判据零复制）——这里验程序化入口那条。

    CLI 侧走的是 `_export_pytest` → `emit_pytest_package(compat=...)`，与本测同一条路径；
    因此本测通过即 CLI 也拒。**不重复跑 CLI**：那会把同一条判据验两遍，而真正的风险
    （有人另写一套 if）由源码扫描面覆盖——见下一条。
    """
    with pytest.raises((CompatEmitError, ValueError)) as ei:
        emit_pytest_package(out_dir=tmp_path / "pkg2", flow=_flow_with_assert(),
                            compat="legacy-suite", fast=True)
    assert "assert" in str(ei.value).lower()


def test_no_second_assert_gate_outside_emit_compat():
    """结构面：不许在别处另写一份「flow assert + compat」判断（#92 改一漏一）。

    唯一判据位是 `emit_compat.compat_specs_from_flow`。若有人在 cli 或 emit_pytest 里
    另写一条 `if ... assert ... compat`，就会与这里分叉——本测扫源码抓那种形态。
    """
    import pathlib
    import re

    repo = pathlib.Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for rel in ("src/atlas/cli.py", "src/atlas/generator/emit_pytest.py"):
        path = repo / rel
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if "assert" in low and "compat" in low and re.search(r"\b(if|raise)\b", low):
                # 允许注释（说明性文字），只抓可执行形态
                if line.lstrip().startswith("#"):
                    continue
                offenders.append(f"{rel}:{i} {line.strip()[:90]}")
    assert not offenders, (
        "发现第二份「flow assert × compat」判断（判据应只在 emit_compat 一处）：\n  "
        + "\n  ".join(offenders))

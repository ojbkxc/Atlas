"""`BodySpec.kind` 取值域闸：未知值必须在生成期点名拒绝，不得静默丢弃请求体。

## 缺陷形态（2026-11-17 结构审计抓出）

`emit_flow._build_step` 的 body 分流是一条 `if/elif` 链，只认 `json` / `form` / `text` /
`unsupported`。而 `model.BodySpec.kind` 的文档取值域有 **5** 个值（多一个 `none`）。
链尾没有 `else`，于是：

| 塞进去的 kind | 旧行为 |
|---|---|
| `json` / `form` / `text` | 产 body ✅ |
| `unsupported` | 不产 body，但**写进 manual_assertions**（明确告知需人工）✅ |
| `none` | 不产 body —— **语义正确**（就是"无请求体"）✅ |
| **未知值（如 `bogus_kind`）** | **不产 body、不报错、不进 manual** ❌ |

实测：`_build_step(1, Endpoint(body=BodySpec(kind="bogus_kind")), ...)` 返回一个
完全正常的 step，`"body" not in step`，`manual == []`。同一份文档 `atlas gen` 出包 rc 0，
**生成的 flow 看起来完整，实际少了一整段请求体**。

这是本仓反复咬到的「静默丢弃」族第四例（前三例：`AssertSpec` 未知 kind 被渲染器丢、
compat 腿的 flow 级 assert 被丢、知识影子层遮蔽）。共同点是**判据域没有闸门，
未覆盖的值走了 fall-through，而 fall-through 的语义恰好是"什么都不做"**。

## 判据

1. 未知 kind → `FlowEmitError`，消息点名端点、取值域与后果；
2. 5 个合法值全部照常（含 `none` 不产 body、`unsupported` 进 manual）——否则闸门误伤；
3. 闸门在**分流之前**（不能等 json 分支跑完才判）；
4. `BODY_KINDS` 与 `model.BodySpec` 的 docstring 取值域**逐项相等**（新增 kind 必须两处同改）。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from atlas.generator.emit_flow import BODY_KINDS, FlowEmitError, _build_step
from atlas.generator.model import BodySpec, Endpoint

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _step(kind: str, **body_kw) -> tuple[dict, list[str]]:
    ep = Endpoint(method="POST", path="/x", body=BodySpec(kind=kind, **body_kw))
    return _build_step(1, ep, {}, "resp_{n}_{slug}", True)


# ---------------------------------------------------------------------------
# 1. 未知 kind 被拒（而不是静默丢弃）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bogus", ["bogus_kind", "", "JSON", "multipart", "raw"])
def test_unknown_body_kind_is_rejected(bogus):
    """未知 kind → 生成期点名拒绝。

    参数化覆盖几个最容易出现的形态：拼错、空串、**大小写不符**（`JSON` 不是 `json`——
    这条特别重要，因为从文档解析器传进来的值若做过 `upper()` 就会命中）。
    """
    with pytest.raises(FlowEmitError) as ei:
        _step(bogus)
    msg = str(ei.value)
    assert "kind" in msg and repr(bogus) in msg, f"必须点名具体 kind 值：{msg}"


def test_rejection_message_explains_the_silent_drop():
    """消息必须说清后果（"静默丢弃整个请求体"），否则读者会以为是"暂时不支持"。"""
    with pytest.raises(FlowEmitError) as ei:
        _step("bogus_kind")
    msg = str(ei.value)
    assert "静默" in msg or "丢弃" in msg, f"必须说明旧行为是静默丢弃：{msg}"
    assert "请求体" in msg, f"必须点名丢的是什么：{msg}"
    # 必须指出补救动作是"两处同改"，而不只是"改这里"
    assert "model.BodySpec" in msg, f"必须指向取值域的另一处：{msg}"


# ---------------------------------------------------------------------------
# 2. 五个合法值照常（闸门不许误伤）
# ---------------------------------------------------------------------------

def test_none_kind_produces_no_body_and_is_not_rejected():
    """`none` = 「无请求体」，是合法值：不产 body 键，也不报错。

    这条防闸门写成 `if kind not in {"json","form","text","unsupported"}` ——
    那会把 `none` 判成未知值，让**所有无请求体的端点**生成失败（GET 查询类全灭）。
    """
    step, manual = _step("none")
    assert "body" not in step
    assert manual == []


def test_json_kind_still_builds_body():
    step, _ = _step("json", example_obj={"a": 1})
    assert step["body"] == {"a": 1}


def test_form_kind_still_builds_body():
    step, _ = _step("form", pairs=[("a", "1"), ("b", "2")])
    assert step["body"] == "a=1&b=2"


def test_text_kind_still_builds_body():
    step, _ = _step("text", text="raw payload")
    assert step["body"] == "raw payload"


def test_unsupported_kind_still_goes_to_manual():
    """`unsupported` 不产 body，但**必须进 manual_assertions**（明确告知人需处理）。

    这是「诚实缺省」的样板：不做事，但说出来。闸门不该把它变成错误。
    """
    step, manual = _step("unsupported")
    assert "body" not in step
    assert manual and "multipart" in manual[0]


# ---------------------------------------------------------------------------
# 3. 取值域与 model 文档锁步
# ---------------------------------------------------------------------------

def test_body_kinds_matches_model_docstring():
    """`BODY_KINDS` 必须恰是 `model.BodySpec` docstring 里声明的取值域。

    为什么锁 docstring 而不是加一个枚举：`BodySpec.kind` 是 `str`（不是 Enum），
    文档串是它唯一成文的取值域声明。新增 kind 时若只改一处，本测会红——
    红的方向是「拒绝新 kind」（闸门已有），而不是「静默丢弃」（旧行为）——
    这正是我们要的失败方向。
    """
    src = (REPO_ROOT / "src" / "atlas" / "generator" / "model.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = ast.get_docstring(
        next(n for n in ast.walk(tree)
             if isinstance(n, ast.ClassDef) and n.name == "BodySpec"))
    assert doc, "model.BodySpec 没有 docstring——取值域声明丢了"
    # docstring 形如：kind: "json" → ... / "form" → ... / "text" → ... / ...
    declared = set(re.findall(r'"([a-z][a-z0-9_]*)"\s*→', doc))
    declared |= set(re.findall(r'^kind:\s*str\s*=\s*"([a-z][a-z0-9_]*)"', doc, re.M))
    # `kind: str = "none"` 那一行的默认值也要算进去（它可能不在 → 列表里）
    assert declared, f"没能从 docstring 解析出取值域，正则需随文档格式更新：{doc!r}"
    assert declared == set(BODY_KINDS), (
        f"取值域两处不一致：model.BodySpec 声明 {sorted(declared)}，"
        f"emit_flow.BODY_KINDS 是 {sorted(BODY_KINDS)}——新增 kind 必须两处同改")


def test_every_producer_uses_a_declared_kind():
    """全仓所有 `BodySpec(kind=...)` 字面量都必须在取值域内（正向对照：闸门不空转）。

    扫 `src/atlas/generator/*.py`：任何 `kind="xxx"` 的生产点若写了取值域外的值，
    它自己就会在运行时撞闸门——但那是**用户那边**才炸。这里提前在离线期抓。
    """
    offenders: list[str] = []
    for path in (REPO_ROOT / "src" / "atlas" / "generator").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'BodySpec\(\s*kind\s*=\s*"([^"]+)"', text):
            if m.group(1) not in BODY_KINDS:
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line} kind={m.group(1)!r}")
    assert not offenders, f"生产点写了取值域外的 kind：{offenders}"
    # 正向对照：扫描确实命中了一批生产点（否则"无越界"是恒真）
    total = sum(len(re.findall(r'BodySpec\(\s*kind\s*=\s*"', p.read_text(encoding="utf-8")))
                for p in (REPO_ROOT / "src" / "atlas" / "generator").glob("*.py"))
    assert total >= 5, f"扫描命中过少（{total}）——正则可能已失效"
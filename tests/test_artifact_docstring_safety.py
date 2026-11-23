"""产物 docstring 安全性守卫：外部文本进产物 docstring 前必须过 `dsl_utils.docstring_text`。

## 这条守卫挡的是什么（真机抓出的产物侧缺陷）

通用知识包 `src/atlas/plugins/_common/knowledge/components.json` 里 dialog 组件的 `notes`
写着正则 `/确\\s*定/`。`atlas gen examples/api_sample.md --with-pytest --fast` 把 notes 原样插进
产物 `atlas_pages.py` 的类 docstring 后，该文件里的 `\\s` 是**非法转义**：

- Python 3.11/3.12 只报 `SyntaxWarning`（默认被忽略，产物「看起来能跑」）；
- CI 常规档 `-W error::SyntaxWarning`（本仓与多数 lint/测试流水线）直接 **SyntaxError**，
  更严格的解释器把非法转义升格成错误后是**硬失败**。

最坏的一点不是崩溃本身，而是**谁也看不见**：生成期自检只编译本仓 `src/`，不编译自己写出去的
文件；产物「生成 rc 0 / 结构齐全 / 一 import 就炸」，与「只生成不执行」(#9) 是同一条缝。
判据单点在 `atlas.generator.dsl_utils.docstring_text`（emit_pytest / emit_compat 共用）：
转义反斜杠与双引号，`__doc__` 取值逐字不变，其余字符原样。

## 十二条

1. `test_real_common_knowledge_pages_warning_clean`：**真实知识包**（就含 `/确\\s*定/`）→ 快/慢
   两形态页对象产物警告洁净可编译（旧实现精确复现红，见下「变异实证」）；
2. `test_notes_roundtrip_and_warning_clean`：反斜杠/双引号/尾部反斜杠三类 notes 都安全，且
   **docstring 取值与原文逐字相等**（不是「改成安全但变了内容」）；
3. `test_discriminator_detects_raw_backslash_docstring`：正向对照——同一判据面对**未转义**的
   docstring 必须报红（否则本文件的 `warnings` 过滤器恒真，守卫空转）；
4. `test_test_module_docstrings_warning_clean`：用例模块 docstring（`intent`/`path`/`task` 来自
   文档）同样安全，快慢 × API/UI 四格。
5. `test_generator_docstrings_route_through_helper`：源码面收敛——`generator/*.py` 里凡把自由文本
   （notes/intent/path/task/element/flow_id）插进 `\"\"\"` 的行都必须包 `docstring_text(`，防「新加
   一个插值点又直接 f-string 拼」（#92 的漂移形态）；带正向对照下限（扫到的行数不能是 0）。
6. `test_docstring_text_unit_semantics`：单点函数本身的两条性质（`None`→空串、中文/单引号不动）。
7. `test_pytest_package_refuses_warning_dirty_artifact`：**写盘前闸**——把转义换回恒等（= 老形态
   插值）后生成必须点名拒绝，且目标目录不创建（零残留）。
8. `test_compat_package_refuses_warning_dirty_artifact`：同一条闸在 compat 入口（`flow_id` 带
   反斜杠）同样拒绝、同样零残留。
9. `test_artifact_compile_problems_discriminates`：单点判据两向对照（好源码空清单 / 坏源码点名文件）。
10. `test_repo_sources_compile_warning_clean`：本仓 `src/`＋`tests/`＋`scripts/` 全部按同一严档编译
    干净（写本文件时当场踩过一次：说明文字里的裸反斜杠让守卫自己编不过）。
11. `test_fast_artifacts_warning_clean`：`emit_fast` 的两份底座模板（`_atlas_fast.py`/`conftest.py`）
    同样洁净。
12. `test_write_fast_artifacts_refuses_warning_dirty_template`：模板被改成带非法转义的插值时，
    写盘前闸在 `mkdir` 之前拒绝（三处产物写入口同一口径）。

变异实证（2026-11-23，本机实跑）：把 pages 模块的 `docstring_text(notes)` 改回 `{notes}` →
第 1、2 两条精确红（两种形态都现形：`invalid escape sequence` 的点名 / 尾反斜杠逃逸闭合引号），
改回即绿（文件 sha256 与改前一致）。第 5 条在落地当天就抓到一处真漏网：`emit_compat.py` 的
`flow {flow_id}` 裸插值（`flow_id` 来自文件名/标题）。另：三处既有 `_assert_compiles`
（`test_emit_pytest` / `test_emit_compat` / `test_emit_fast`）同步改成「警告当错误」口径，
否则整族缺陷在这三份辅助函数下恒绿。
"""
from __future__ import annotations

import ast
import json
import re
import warnings
from pathlib import Path

import pytest

from atlas.generator.dsl_utils import docstring_text
from atlas.generator.model import Endpoint
from atlas.generator.emit_pytest import (
    StepSpec,
    emit_pages_module,
    render_test_module,
)
from atlas.plugin_api import common_knowledge_root

_REPO = Path(__file__).resolve().parents[1]
_GENERATOR_DIR = _REPO / "src" / "atlas" / "generator"


def _assert_warning_clean(source: str, name: str) -> None:
    """与 CI 常规档同口径：编译期任何 SyntaxWarning 都当失败（非法转义在这里现形）。"""
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        compile(source, name, "exec")


def _class_docstring(source: str, class_name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"产物里没有类 {class_name}")


# ---- 1. 真实知识包：这就是线上触发该缺陷的那份数据 ----

def test_real_common_knowledge_pages_warning_clean():
    comps = json.loads((Path(common_knowledge_root()) / "components.json")
                       .read_text(encoding="utf-8"))
    # 前提核查：数据里确实带反斜杠正则（否则这条测就退化成「空库也绿」的空转）
    raw = json.dumps(comps, ensure_ascii=False)
    assert "\\\\s" in raw or "\\s" in raw, "通用知识包不再含反斜杠 notes——本测的正向前提消失，需换样本"

    for fast in (False, True):
        source = emit_pages_module(comps, fast=fast)
        _assert_warning_clean(source, f"<pages fast={fast}>")
        # 转义后的形态：源码里有 `\\s`（Python 字面量两层），解码后 docstring 仍是一个反斜杠
        assert "\\\\s" in source, f"fast={fast}：反斜杠没有被转义（原缺陷形态）"


# ---- 2. 语义保真：转义不改内容 ----

def test_notes_roundtrip_and_warning_clean():
    notes = '正则 /确\\s*定/；引号 "半角"；尾部反斜杠 \\'
    comps = {"components": {"dialog": {"notes": notes, "selectors": {"ok": ".el-dialog__footer"}}}}
    for fast in (False, True):
        source = emit_pages_module(comps, fast=fast)
        _assert_warning_clean(source, f"<pages notes fast={fast}>")
        doc = _class_docstring(source, "Dialog")
        assert notes in doc, f"fast={fast}：docstring 取值被改动（应为逐字保真）\n实际：{doc!r}"


# ---- 3. 正向对照：判据必须能报红 ----

def test_discriminator_detects_raw_backslash_docstring():
    raw_source = 'class X:\n    """未转义 /确\\s*定/"""\n'
    # CPython 实际形态：默认只警告；`simplefilter("error")`/CI 档下编译直接抛 SyntaxError
    # （实测 3.13.16 就是 SyntaxError），两形态都算「判据报红」。
    with pytest.raises((SyntaxWarning, SyntaxError)):
        _assert_warning_clean(raw_source, "<raw>")
    # 同一份文本过 helper 后必须绿（成对证明：红不是环境噪声，绿不是判据失效）。
    # 反斜杠先算好再拼——f-string 花括号里写转义在 3.11 是语法错误（README 支持 3.11+）。
    escaped = docstring_text("\\s")
    safe_source = 'class X:\n    """未转义 /确' + escaped + '*定/"""\n'
    _assert_warning_clean(safe_source, "<safe>")


# ---- 4. 用例模块 docstring：intent/path/task 也是外部文本 ----

def test_test_module_docstrings_warning_clean():
    api = StepSpec(index=1, slug="order_list", intent="查询订单\\s列表",
                   task="", method="GET", path="/api/order/\\d+/list")
    ui = StepSpec(index=2, slug="open_dialog", intent="打开弹窗\\s并校验",
                  task="open_dialog\\s", is_api=False, page_object="dialog")
    for fast in (False, True):
        source = render_test_module([api, ui], flow_id="dir\\s-flow", fast=fast)
        _assert_warning_clean(source, f"<test module fast={fast}>")
        assert "\\\\s" in source, f"fast={fast}：用例 docstring 的反斜杠没有转义"


# ---- 5. 源码面收敛：插值点必须走 helper（防新漂移） ----

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_FREE_TEXT_TOKENS = ("notes", ".intent", ".path", ".task", "element", "flow_id")


def test_generator_docstrings_route_through_helper():
    scanned = 0
    offenders: list[str] = []
    for path in sorted(_GENERATOR_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if '"""' not in line or not stripped.startswith("f"):
                continue
            scanned += 1
            for expr in _PLACEHOLDER.findall(line):
                if not any(token in expr for token in _FREE_TEXT_TOKENS):
                    continue
                if "docstring_text(" not in expr:
                    offenders.append(f"{path.name}:{lineno}: {{{expr}}}")
    # 正向对照下限：一个都扫不到 = 判据失效（换写法/换目录都会到这里）
    assert scanned >= 5, f"只扫到 {scanned} 行 f-string docstring，判据可能失效"
    assert not offenders, (
        "以下插值点把自由文本直接拼进 docstring（反斜杠 = 非法转义，产物一 import 就炸）：\n"
        + "\n".join(offenders))


# ---- 6. 单点函数自身语义 ----

def test_docstring_text_unit_semantics():
    assert docstring_text(None) == ""
    assert docstring_text("") == ""
    assert docstring_text("纯中文/单引号'不转义'") == "纯中文/单引号'不转义'"
    assert docstring_text("\\s") == "\\\\s"
    assert docstring_text('say "hi"') == 'say \\"hi\\"'


# ---- 7/8. 写盘前闸：转义一旦失效，生成必须拒绝（而不是写出坏产物） ----

def test_pytest_package_refuses_warning_dirty_artifact(tmp_path, monkeypatch):
    """把转义换成恒等（= 老形态插值）后，产物里有非法转义 → 生成期点名拒绝、零残留。

    这条测的是**闸本体**（`dsl_utils.artifact_compile_problems`）而不是转义函数：
    将来渲染层把 `docstring_text` 从某个新插值点漏掉时，产物不能落盘。
    """
    import atlas.generator.emit_pytest as emit_pytest
    from atlas.generator.emit_pytest import PytestEmitError, emit_pytest_package

    monkeypatch.setattr(emit_pytest, "docstring_text", lambda value: str(value or ""))
    comps = {"components": {"dialog": {"notes": "用正则 /确\\s*定/ 点按钮"}}}
    out = tmp_path / "pkg"
    with pytest.raises(PytestEmitError) as exc:
        emit_pytest_package(out_dir=out, endpoints=[Endpoint(method="GET", path="/api/x",
                                                             summary="查询")],
                            components=comps)
    assert "无法安全编译" in str(exc.value)
    assert "atlas_pages.py" in str(exc.value), "问题清单必须点名到具体产物文件"
    assert not out.exists(), "拒绝必须发生在 mkdir 之前（零残留）"


def test_compat_package_refuses_warning_dirty_artifact(tmp_path, monkeypatch):
    import atlas.generator.emit_compat as emit_compat
    from atlas.generator.emit_compat import CompatEmitError, emit_compat_package

    monkeypatch.setattr(emit_compat, "docstring_text", lambda value: str(value or ""))
    flow = {"id": "compat\\s-suite", "steps": [
        {"task": "legacy_case", "module": "legacy/01_case/x.yaml", "case_id": "TC1",
         "case_intent": "TC1 查询", "write": False, "skip_postcondition": True},
    ]}
    out = tmp_path / "compat"
    with pytest.raises(CompatEmitError) as exc:
        emit_compat_package(out_dir=out, flow=flow)
    assert "无法安全编译" in str(exc.value)
    assert not out.exists(), "拒绝必须发生在 mkdir 之前（零残留）"


# ---- 9. 单点判据自身的两向对照 ----

def test_artifact_compile_problems_discriminates():
    from atlas.generator.dsl_utils import artifact_compile_problem, artifact_compile_problems

    assert artifact_compile_problems([("good.py", "x = 1\n")]) == []
    bad = artifact_compile_problems([("bad.py", 'x = "\\s"\n'), ("good.py", "x = 1\n")])
    assert len(bad) == 1 and "bad.py" in bad[0] and "invalid escape" in bad[0]
    assert artifact_compile_problem("x = 1\n", "ok.py") is None


# ---- 10. 本仓源码同样按严档编译（这条守卫自己就踩过一次） ----

def test_repo_sources_compile_warning_clean():
    """`src/`＋`tests/`＋`scripts/` 全部按 CI 严档编译干净。
    写本文件时就现世报了一次：第 38 行的说明文字里写了裸 `\\s`，整份守卫自己
    `-W error::SyntaxWarning` 编不过（默认档只警告，跑测试完全看不出来）。
    """
    from atlas.generator.dsl_utils import artifact_compile_problem

    files = sorted(list((_REPO / "src").rglob("*.py"))
                   + list((_REPO / "tests").rglob("*.py"))
                   + list((_REPO / "scripts").rglob("*.py")))
    problems = [p for p in (artifact_compile_problem(path.read_text(encoding="utf-8"),
                                                    str(path.relative_to(_REPO)))
                            for path in files) if p]
    assert len(files) >= 150, f"只扫到 {len(files)} 个 .py，判据可能失效"
    assert not problems, "以下源文件按 CI 严档编不过（非法转义）：\n" + "\n".join(problems)


# ---- 11/12. 快模式底座（emit_fast）同口径：模板洁净 + 写盘前闸 ----

def test_fast_artifacts_warning_clean():
    from atlas.generator.emit_fast import emit_fast_conftest_module, emit_fast_support_module

    for name, source in (("_atlas_fast.py", emit_fast_support_module()),
                         ("conftest.py", emit_fast_conftest_module())):
        _assert_warning_clean(source, f"<fast {name}>")
        assert "生成候选" in source, f"{name}：模板内容可疑（拿错产物？）"


def test_write_fast_artifacts_refuses_warning_dirty_template(tmp_path, monkeypatch):
    """模板一旦被改成带非法转义的插值，`write_fast_artifacts` 必须在 mkdir 之前拒绝。"""
    import atlas.generator.emit_fast as emit_fast
    from atlas.generator.emit_fast import FastEmitError, write_fast_artifacts

    monkeypatch.setattr(emit_fast, "_FAST_CONFTEST_SOURCE", 'import os\n\nREGEX = "\\s"\n')
    out = tmp_path / "fast"
    with pytest.raises(FastEmitError) as exc:
        write_fast_artifacts(out)
    assert "无法安全编译" in str(exc.value)
    assert "conftest.py" in str(exc.value)
    assert not out.exists(), "拒绝必须发生在 mkdir 之前（零残留）"

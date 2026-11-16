"""回归代码产出：从 Atlas 模型 / flow YAML 生成 pytest 回归用例（只生成不执行）。

形态推荐见 ``docs/gaps/gap7-regression-code-eval.md``（评审结论）：

- **推荐形态 2 ⊕ 形态 3 底座**：以「pytest 用例 + 共享页对象库」为主干
  （形态 2），页对象库从 Atlas 组件库 ``components.json`` 派生（复用 Atlas 知识资产），
  共享底座 ``_atlas_support.py`` 自包含（仅标准库，可脱离 Atlas 运行，顺带满足形态 3）。
- **不采用形态 1**（每用例自带 ``step_N``、零复用）：与 Atlas「不拷贝旧用例、避免两份
  漂移代码」（FAQ）冲突，维护成本最高且执行更慢（重复的固定 sleep / 每步截图）。

红线（AGENTS/CLAUDE）：

- #9 只生成不执行：本模块一律返回源码文本或写文件，**绝不 import 被执行、绝不发请求**。
- #7 不放宽断言：只断言文档明示项（存活 + 文档给出的 ``resultCode``）；数组下标等
  DSL/生成器不支持的断言列 ``TODO`` 供人审补，绝不改成恒真。
- #4 不引入新运行时依赖：共享底座只用标准库（``urllib``/``json``/``os``/``re``）；
  生成的 UI 骨架显式声明 Playwright 依赖，缺依赖即明确失败，不静默降级、不假装通过。

数据流：``Endpoint[]``（generator/model.py）或 flow dict → ``StepSpec[]`` → pytest 源码
      ＋ ``atlas_pages.py``（组件库派生）＋ ``_atlas_support.py``（自包含底座）。

快模式（gap9，``fast=True``）：UI 腿从「恒 skip」升级为真渲染——单浏览器/单登录
（``get_page``）＋ 跨批前置（``plan_ctx``/``require_ctx``）＋ Atlas 兼容 runs.jsonl
（``_fast_runlog``）；底座/夹具模板来自 ``generator/emit_fast``（依赖方向单向：
emit_pytest → emit_fast，只 import 其契约公开符号——``write_fast_artifacts``/
``FastEmitError``/``FAST_SUPPORT_MODULE_NAME``/``ENV_KEYS``，不引用 ``_FAST_*`` 模板常量）。
**``fast=False``（默认）的用例/页对象/底座输出与改造前逐字节相同**——快照测在
``tests/test_emit_pytest.py`` 钉死；产物 README 是 ae-6 有意的两形态共同可读性改造
（见 ``docs/gaps/wave4-artifact-readability.md`` §6，不承诺逐字节历史形态）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import locators
from ..credential_gate import gate_flow_headers
from .model import Endpoint
from .dsl_utils import slugify, collect_path_query, form_pairs_to_string
from .emit_flow import _save_as_name

# 生成的模块文件名（与测试模块同目录，直接 import，不需要包结构）。
PAGES_MODULE_NAME = "atlas_pages"
SUPPORT_MODULE_NAME = "_atlas_support"

# 横幅文案**不含内层加粗**：渲染时整体放在一行引用里（`> **横幅**：…`），内层再出现
# `**…**` 会让 Markdown 嵌套加粗解析破裂（ae-6 P0-1，实测产物第 5 行渲染成断句）。
# 双入口都点名：flow→包的主路径是 `atlas export-pytest`，文档→包才是 `atlas gen --with-pytest`；
# 只写 gen 会让 export-pytest 产物自称错来源（ae-6 一般-5）。
_README_BANNER = (
    "本产物由 `atlas export-pytest`（flow → 包）或 `atlas gen --with-pytest`（文档 → 包）生成，"
    "只生成不执行：鉴权值、写操作、建单须人工确认后手动运行。"
)


class PytestEmitError(ValueError):
    """生成 pytest 回归代码时的结构问题（如模块名非法、目标目录冲突）。"""


# ---------------------------------------------------------------------------
# StepSpec：端点/flow step 归一后的可渲染形态
# ---------------------------------------------------------------------------


@dataclass
class AssertSpec:
    """一条断言。kind="ok" 存活；kind="result_code" 文档给值；kind="note" 只留 TODO。"""
    kind: str
    expected: str = ""
    note: str = ""


@dataclass
class StepSpec:
    """一步（api_call 或 UI 任务）渲染成一个测试函数所需的全部信息。"""
    index: int
    slug: str
    intent: str
    task: str
    method: str = ""
    path: str = ""
    save_as: str = ""
    headers: dict = field(default_factory=dict)
    body: Any = None
    asserts: list[AssertSpec] = field(default_factory=list)
    is_write: bool = False
    is_api: bool = True
    # gap9 快模式：执行前必须已存在于 plan_ctx 的前置键（缺=EnvBlocked，不静默跳）。
    # 默认空元组：存量形态零涟漪（#115，fast=False 渲染完全无视本字段）。
    requires: tuple = ()
    # gap9b B-2 声明位（两独立字段，**不共值域**——page_object=已批准组件名、
    # legacy_pages=旧套件函数名；合一单键再按值形态分支=引擎猜语义，违 #11/F-2）。
    # 默认空串=存量形态零涟漪（#115）；fast=False 渲染一行不变——守该零涟漪的是
    # _FAST_OFF_BASELINE 全部 12 项哈希（构造路径不变）+ test_declared_keys_zero_ripple_slow_shape
    # （含声明位 flow 与不含声明位 flow 的 fast=False 输出逐字节相等）。删任一即失去「新字段
    # 渗入旧形态」的防线。形态判据单点=compile 静态段（A.5/B-2），本层只搬运不重复校验。
    page_object: str = ""
    legacy_pages: str = ""


# ---------------------------------------------------------------------------
# Python 源码字面量（生成物里内联请求体）
# ---------------------------------------------------------------------------


def _py_literal(value: Any) -> str:
    """把 JSON 友好的值渲染成合法 Python 字面量（str 用 repr，保留 unicode 可读）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, dict):
        inner = ", ".join(f"{_py_literal(k)}: {_py_literal(v)}" for k, v in value.items())
        return "{" + inner + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_py_literal(v) for v in value) + "]"
    # 非 JSON 友好（理论不可达）：repr 兜底，字形仍合法
    return repr(value)


def _py_all_lines(names: list[str], *, width: int = 80) -> list[str]:
    """`__all__` 折行渲染（ae-6：单行超长 → 按行宽折，语义不变）。

    一行放得下（`__all__ = [...]` ≤ width）就保持单行；放不下才逐项折行（首行 `__all__ = [`，
    末行 `]`），中间每项缩进 4 格。折行是纯可读性改造，产物的 `__all__` 取值逐字不变。
    """
    single = f"__all__ = [{', '.join(_py_literal(n) for n in names)}]"
    if len(single) <= width:
        return [single]
    out = ["__all__ = ["]
    for name in names:
        out.append(f"    {_py_literal(name)},")
    out.append("]")
    return out


def _ident_slug(text: str, *, fallback: str = "step") -> str:
    """slugify 后转成合法标识符片段（非字母数字 → _）。"""
    s = slugify(text, fallback=fallback).replace("-", "_")
    s = re.sub(r"[^0-9a-zA-Z_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = f"s_{s}" if s else fallback
    return s


# ---------------------------------------------------------------------------
# Endpoint[] → StepSpec[]
# ---------------------------------------------------------------------------


def _endpoint_body(ep: Endpoint) -> Any:
    """body 分流（与 emit_flow 同口径）：json→dict/list，form/text→str，其余 None。"""
    body = ep.body
    if body.kind == "json":
        if body.example_obj is None:
            return {p.name: p.example for p in ep.params if p.where == "body"}
        obj = dict(body.example_obj) if isinstance(body.example_obj, dict) else body.example_obj
        if isinstance(obj, dict):
            for p in ep.params:
                if p.where == "body":
                    obj.setdefault(p.name, p.example)
        return obj
    if body.kind == "form":
        return form_pairs_to_string(body.pairs)
    if body.kind == "text":
        return body.text
    return None  # none / unsupported（unsupported 由调用方列 manual）


def _endpoint_asserts(ep: Endpoint) -> list[AssertSpec]:
    """只断言文档明示项：存活 + 文档给出具体 resultCode；数组不伪造下标断言（#7）。"""
    specs = [AssertSpec("ok")]
    resp = ep.response
    if resp.is_object and resp.has_result_code:
        sample = resp.sample if isinstance(resp.sample, dict) else {}
        rc = sample.get("resultCode")
        if rc is not None:
            specs.append(AssertSpec("result_code", expected=str(rc)))
    if resp.is_array:
        specs.append(AssertSpec("note", note=(
            "响应为数组，列表项断言请补充（勿放宽）："
            "建议校验 items 数量或按业务键匹配，不伪造下标")))
    return specs


def _specs_from_endpoints(endpoints: list[Endpoint], *, global_headers: dict | None = None,
                          naming: str = "resp_{n}_{slug}") -> list[StepSpec]:
    specs: list[StepSpec] = []
    for i, ep in enumerate(endpoints, 1):
        path_params = [p for p in ep.params if p.where in ("path", "query")]
        headers = dict(global_headers or {})
        for p in ep.params:
            if p.where == "header":
                headers[p.name] = str(p.example) if p.example is not None else ""
        specs.append(StepSpec(
            index=i,
            slug=_ident_slug(f"{ep.summary} {ep.path}", fallback="op"),
            intent=ep.summary or f"调用 {ep.method} {ep.path}",
            task="api_call",
            method=ep.method,
            path=collect_path_query(ep.path, path_params),
            save_as=ep.save_as_override or _save_as_name(i, ep, naming),
            headers=headers,
            body=_endpoint_body(ep),
            asserts=_endpoint_asserts(ep),
            is_write=ep.is_write,
            is_api=True,
        ))
    return specs


# ---------------------------------------------------------------------------
# flow dict → StepSpec[]
# ---------------------------------------------------------------------------

_FLOW_OK_RE = re.compile(r"^\s*(\w+)\.ok\s*==\s*true\s*$")
_FLOW_RC_RE = re.compile(r'^\s*(\w+)\.data\.resultCode\s*==\s*["\'](.+?)["\']\s*$')

# 导出器「无渲染分支」的核心 Task 名单（**判据单点**，#92 只此一处；红线 #11：这是**框架**
# 词汇、非业务名——与既有的 `api_call` 字面量同类）。为什么恰是这两个：导出器只有两条渲染腿——
# ① API 腿只认 `api_call` 的 method/path 形态；② UI 腿只认声明位（page_object/legacy_pages）或
# auto-wire。`assert_api`（API 断言）与 `demo_api`（DemoApi 调用）都是 Channel.API 的核心 Task，
# 其 step 形态是 operation/args[/expect]，两条腿都吃不下——**且补 UI 声明位也救不了**（它们是
# API 语义、不是 UI 动作）。故必须点名真因，绝不伪装成「缺声明位」（那会让人花时间补声明位却
# 仍 rc2，归因漂移）。真正支持它们渲染=新增渲染分支，属功能扩展（T11 已知缺口，留给用户拍板）。
_UNRENDERABLE_TASKS = {
    "assert_api": "API 断言 Task（operation/args/expect），导出器没有断言渲染分支",
    "demo_api": "DemoApi 调用 Task（operation/args），导出器 API 腿只认 api_call 的 method/path",
}


def _reject_unrenderable_task(task: str, index: int, slug: str) -> None:
    """无渲染分支的核心 Task → 点名真因 + 替代路径（**判据单点**，全导出入口共用一处）。"""
    why = _UNRENDERABLE_TASKS.get(task)
    if why is None:
        return
    raise PytestEmitError(
        f"导出器暂不支持该 Task：第 {index} 步「{slug}」(task={task}) 无法渲染为 pytest 产物——"
        f"{why}。替代路径：改用 api_call 承载该 HTTP 调用、断言写 flow 级 `assert:`；补 "
        f"page_object/legacy_pages 无效（该步是 API 语义，不是 UI 动作）。")


def _flow_asserts(flow: dict, save_as: str) -> list[AssertSpec]:
    """把 flow 级 assert 文本按 save_as 归属到该步（能解析的转真断言，其余留 TODO）。"""
    specs: list[AssertSpec] = [AssertSpec("ok")]
    for raw in flow.get("assert") or []:
        text = str(raw)
        m_rc = _FLOW_RC_RE.match(text)
        m_ok = _FLOW_OK_RE.match(text)
        if m_rc and m_rc.group(1) == save_as:
            specs.append(AssertSpec("result_code", expected=m_rc.group(2)))
        elif m_ok and m_ok.group(1) == save_as:
            pass  # 存活断言已在 specs[0]
        elif save_as and save_as in text:
            specs.append(AssertSpec("note", note=f"待接断言（人审）：{text}"))
    return specs


def _parse_requires(raw: Any, i: int) -> tuple:
    """flow step 的 requires → tuple[str]；缺省/None→()；坏形态点名步骤序号拒绝（不静默）。

    与 ``dsl.validate_flow`` 同口径：结构问题当场点名，绝不 last-wins/半收（#84 同族）。
    """
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise PytestEmitError(
            f"flow 步骤 {i} 的 requires 必须是字符串列表"
            f"（当前 {type(raw).__name__}：{raw!r}）")
    keys: list[str] = []
    for j, k in enumerate(raw, 1):
        if not isinstance(k, str) or not k.strip():
            raise PytestEmitError(
                f"flow 步骤 {i} 的 requires 第 {j} 项不是非空字符串：{k!r}")
        keys.append(k)
    return tuple(keys)


def _specs_from_flow(flow: dict, *, naming: str = "resp_{n}_{slug}") -> list[StepSpec]:
    specs: list[StepSpec] = []
    for i, step in enumerate(flow.get("steps") or [], 1):
        task = str(step.get("task") or "")
        save_as = str(step.get("save_as") or "")
        intent = str(step.get("intent") or step.get("id") or task or f"step_{i}")
        slug = _ident_slug(step.get("id") or intent, fallback=f"step_{i}")
        # 无渲染分支的核心 Task（assert_api/demo_api）点名真因——**不**伪装成「缺声明位」（#16）。
        _reject_unrenderable_task(task, i, slug)
        is_api = task == "api_call"
        specs.append(StepSpec(
            index=i,
            slug=slug,
            intent=intent,
            task=task,
            method=str(step.get("method") or ""),
            path=str(step.get("path") or ""),
            save_as=save_as,
            headers=dict(step.get("headers") or {}),
            body=step.get("body"),
            asserts=_flow_asserts(flow, save_as),
            is_write=(step.get("read_only") is False),
            is_api=is_api,
            requires=_parse_requires(step.get("requires"), i),
            # 声明位只搬运（形态归 compile 静态段单点，B-2/A.5——本层不留第二把形态尺）；
            # `or ""` 仅折叠 falsy 缺省（None/缺键→空串=未声明）；非空坏形态（dict/bool…）
            # 原样带上，由生成期最后防线 _ui_leg 点名拒绝（直调入口绕过静态段时不静默）。
            page_object=step.get("page_object") or "",
            legacy_pages=step.get("legacy_pages") or "",
        ))
    return specs


# ---------------------------------------------------------------------------
# 渲染 pytest 测试模块
# ---------------------------------------------------------------------------


def _fast_base_module() -> str:
    """快模式底座模块名——唯一来源是 emit_fast 的契约公开符号（不留第二份字面量，#92）。

    惰性 import：fast=False 路径绝不触碰 emit_fast（并行开发期/精简克隆零依赖，#115）。
    """
    from .emit_fast import FAST_SUPPORT_MODULE_NAME
    return FAST_SUPPORT_MODULE_NAME


def _render_api_test_fast(spec: StepSpec) -> list[str]:
    """快模式 API 腿：RunWriter 步进 + plan_ctx 前置检查/产出写回（非 skip、显式终态）。"""
    op = "write" if spec.is_write else "api"
    tag = "[写操作-需人工确认] " if spec.is_write else ""
    lines = [
        f'@fast_case(op="{op}", intent={_py_literal(spec.intent)})',
        f"def test_{spec.index:02d}_{spec.slug}(plan_ctx, _fast_runlog):",
        f'    """{tag}{spec.intent}  [{spec.method} {spec.path}]"""',
    ]
    for k in spec.requires:
        lines.append(f"    require_ctx(plan_ctx, {_py_literal(k)})")
    lines.append(f"    with _fast_runlog.step({_py_literal(f'{spec.method} {spec.path}')}):")
    lines.append("        result = call_api(")
    lines.append(f'            method="{spec.method}",')
    lines.append(f"            path={_py_literal(spec.path)},")
    lines.append("            base_url=BASE_URL,")
    if spec.headers:
        lines.append(f"            headers={_py_literal(spec.headers)},")
    lines.append(f"            body={_py_literal(spec.body)},")
    lines.append("        )")
    lines.append('    with _fast_runlog.step("assert ok"):')
    lines.append('        assert result.ok, (f"存活断言失败：status={result.status} "')
    lines.append('                           f"err={result.error} body={result.text[:200]!r}")')
    for a in spec.asserts:
        if a.kind == "result_code":
            lits = _py_literal(a.expected)
            lines.append('    with _fast_runlog.step("assert resultCode"):')
            lines.append(
                f'        assert result.data is not None, "响应非 JSON：无法校验 resultCode（勿放宽断言）"')
            lines.append(
                f'        assert result.data.get("resultCode") == {lits}, (')
            lines.append(
                f'            f"resultCode 不符：期望 {lits}，实际 {{result.data.get(\'resultCode\')!r}}")')
        elif a.kind == "note":
            lines.append(f"    # TODO(人审): {a.note}")
    if spec.save_as:
        lines.append(f"    plan_ctx[{_py_literal(spec.save_as)}] = result.data")
    lines.append("")
    lines.append("")
    return lines


def _render_api_test(spec: StepSpec, *, fast: bool = False) -> list[str]:
    if fast:
        return _render_api_test_fast(spec)
    lines = [f"def test_{spec.index:02d}_{spec.slug}():"]
    tag = "[写操作-需人工确认] " if spec.is_write else ""
    lines.append(f'    """{tag}{spec.intent}  [{spec.method} {spec.path}]"""')
    lines.append("    result = call_api(")
    lines.append(f'        method="{spec.method}",')
    lines.append(f"        path={_py_literal(spec.path)},")
    lines.append("        base_url=BASE_URL,")
    if spec.headers:
        lines.append(f"        headers={_py_literal(spec.headers)},")
    lines.append(f"        body={_py_literal(spec.body)},")
    lines.append("    )")
    lines.append(
        '    assert result.ok, (f"存活断言失败：status={result.status} "'
        'f"err={result.error} body={result.text[:200]!r}")')
    for a in spec.asserts:
        if a.kind == "result_code":
            lits = _py_literal(a.expected)
            lines.append(
                f'    assert result.data is not None, "响应非 JSON：无法校验 resultCode（勿放宽断言）"')
            lines.append(
                f"    assert result.data.get(\"resultCode\") == {lits}, (")
            lines.append(
                f'        f"resultCode 不符：期望 {lits}，实际 {{result.data.get(\'resultCode\')!r}}")')
        elif a.kind == "note":
            lines.append(f"    # TODO(人审): {a.note}")
    lines.append("")
    lines.append("")
    return lines


def _ui_leg(spec: StepSpec, wiring: dict | None = None) -> tuple[str, str, str | None]:
    """声明位消费单点（渲染层与生成期闸门共用，#92 不留第二份判据）。

    返回三形态之一：
    - ``("page_object", 组件名, None)``（声明位命中）；
    - ``("legacy_pages", 函数名, None)``（声明位命中）；
    - ``("auto", 组件名, 元素)``（**无声明**时按组件库数据 auto-wire：任务名 → 组件动作）。

    `wiring` 由 ``_task_action_wiring`` 从组件库派生（缺省 None=无 auto-wire 数据源）。
    缺失声明位且任务不在 wiring → ``PytestEmitError`` 点名（历史「按 Task 名猜组件」通路已随
    F-10/gap9b B-2 收口删除——声明位归人、auto-wire 归数据，引擎都不猜，红线 #11）。形态判据
    第一道在 compile 静态段，本函数是程序化直调入口的最后防线。

    两闸对「坏形」的分工（契约 C 表 B-2 行属**第二道闸**）：静态段查「值长什么样」
    （非空标量矩阵/互斥/点分+模块位消息），本层查「能不能消费」（在不在、是不是 str 或
    wiring 命中）——面正交、消息不互抄，#92 不违。绕过 compile 的直调面（agent/library/内存
    flow）不经 dsl，本层不拦即 fail-open 出坏产物。
    """
    po, lp = spec.page_object, spec.legacy_pages
    # 「缺失」只认 None/空串/纯空白（未声明）；False/0/空容器等 falsy 坏形态**不折叠**进缺失
    # ——留给下方 isinstance 分支按「坏形态」点名原值（证据不丢，同 #117「非空标量才算」纪律）。
    po_missing = po is None or (isinstance(po, str) and not po.strip())
    lp_missing = lp is None or (isinstance(lp, str) and not lp.strip())
    if not po_missing and not lp_missing:
        raise PytestEmitError(
            f"UI 步 {spec.index}「{spec.slug}」同带 page_object={po!r} 与 "
            f"legacy_pages={lp!r}（互斥两键——形态判据单点在 compile 静态段，本处为生成期最后防线）")
    if not po_missing or not lp_missing:
        val = lp if po_missing else po
        if not isinstance(val, str):
            raise PytestEmitError(
                f"UI 步 {spec.index}「{spec.slug}」声明位形态不合法：需非空字符串"
                f"（当前 {type(val).__name__}: {val!r}）——形态判据单点在 compile 静态段，"
                f"本处为生成期最后防线。")
        if po_missing:
            return ("legacy_pages", val.strip(), None)
        return ("page_object", val.strip(), None)
    # 两声明位皆缺 → auto-wire：任务名在组件库动作的 task 数据里认领到唯一 (组件, 元素)。
    wiring = wiring or {}
    hit = wiring.get(spec.task) if isinstance(spec.task, str) else None
    if hit is not None:
        comp, element = hit
        return ("auto", str(comp), str(element))
    raise PytestEmitError(
        f"UI 步 {spec.index}「{spec.slug}」（Task {spec.task or spec.slug}）缺声明位："
        f"需显式声明 page_object（已批准组件名）或 legacy_pages（旧套件函数名），"
        f"或在组件库 actions 里为该 Task 登记动作（auto-wire）——引擎不猜 Task→组件/旧函数"
        f"映射（gap9b B-2/F-10，红线 #11）。")


def _render_ui_test_fast(spec: StepSpec, wiring: dict | None = None) -> list[str]:
    """快模式 UI 腿真渲染（gap9b B-2 + gap9 动作语义）。

    - ``page_object`` → ``atlas_pages.<Pascal(组件名)>(page).action()``（声明位基线）；
    - ``auto``（无声明，任务在组件库 actions 的 task 数据里认领）→
      ``atlas_pages.<Pascal(组件名)>(page).do_<元素>()``（**可执行**直出，值已绑定/click）；
    - ``legacy_pages`` → ``atlas_pages.legacy_page_call("<函数名>", page)``（模块位由产物
      常量 ``LEGACY_PAGES_MODULE=lib.pages`` 给出，A.4 既成——不新建第二个委托符号）。
    显式失败链（绝不静默放过）：类在而 ``action()`` 未接线 → 产物底座 ``NotWired``
    （``EnvBlocked`` 子类，归因判据唯一位在 ``_atlas_fast``，本渲染层不复制——#92/F-2）；
    旧函数缺失/不可调用 → ``legacy_page_call`` 的 ``EnvBlocked("缺配置：…")``。
    """
    kind, name, element = _ui_leg(spec, wiring)
    op = "write" if spec.is_write else "ui"
    lines = [
        f'@fast_case(op="{op}", intent={_py_literal(spec.intent)})',
        f"def test_{spec.index:02d}_{spec.slug}(get_page, plan_ctx, _fast_runlog):",
        f'    """{spec.intent}  [task={spec.task}]"""',
    ]
    for k in spec.requires:
        lines.append(f"    require_ctx(plan_ctx, {_py_literal(k)})")
    lines.append("    page = get_page()")
    if kind == "page_object":
        cls = _pascal(name)
        lines.append(f"    import {PAGES_MODULE_NAME}  # 声明位 page_object={name!r} → 页对象类 {cls}（人审接线后真操作）")
        lines.append(f'    with _fast_runlog.step({_py_literal(f"ui {spec.task or spec.slug}")}):')
        lines.append(f"        {PAGES_MODULE_NAME}.{cls}(page).action()")
    elif kind == "auto":
        cls = _pascal(name)
        method = _action_method(element)
        lines.append(
            f"    import {PAGES_MODULE_NAME}  # auto-wire：组件库 actions 认领 task={spec.task!r}"
            f" → 组件 {name} 元素 {element}（引擎不硬编码业务名）")
        lines.append(f'    with _fast_runlog.step({_py_literal(f"ui {spec.task or spec.slug}")}):')
        lines.append(f"        {PAGES_MODULE_NAME}.{cls}(page).{method}()")
    else:
        lines.append(f"    import {PAGES_MODULE_NAME}  # 声明位 legacy_pages={name!r} → 只读委托（模块位=产物常量 lib.pages）")
        lines.append(f'    with _fast_runlog.step({_py_literal(f"ui {spec.task or spec.slug}")}):')
        lines.append(f"        {PAGES_MODULE_NAME}.legacy_page_call({_py_literal(name)}, page)")
    for a in spec.asserts:
        if a.kind == "note":
            lines.append(f"    # TODO(人审): {a.note}")
    lines.append("")
    lines.append("")
    return lines


def _render_ui_test(spec: StepSpec, *, fast: bool = False) -> list[str]:
    if fast:
        return _render_ui_test_fast(spec)
    lines = ["@pytest.mark.skip(",
             '    reason="生成候选：UI step 需人审接入 atlas_pages（依赖 Playwright）")',
             f"def test_{spec.index:02d}_{spec.slug}():",
             f'    """{spec.intent}  [task={spec.task}]"""',
             '    raise NotImplementedError(',
             '        "生成候选：请接入 atlas_pages 页对象后移除本 skip（见其模块 docstring）")',
             "",
             ""]
    return lines


def _render_test_module_fast(specs: list[StepSpec], *, flow_id: str = "",
                             base_url: str = "", wiring: dict | None = None) -> str:
    """快模式模块渲染（gap9 C.2）。禁词纪律：产物内不出现睡死/裸跳过类调用（E.1.3）。"""
    base_mod = _fast_base_module()
    has_ui = any(not s.is_api for s in specs)
    imports_fast = ["fast_case"]
    if any(s.requires for s in specs):
        imports_fast.append("require_ctx")
    lines: list[str] = [
        '"""生成候选（generator/emit_pytest）：pytest 回归用例（快模式 fast）。',
        "",
        f"来源 flow：{flow_id or '(未命名)'}。**只生成不执行**——鉴权值/写操作须人工确认后手动运行。",
        "断言只覆盖文档明示项（存活 + 文档给出的 resultCode），未放宽（AGENTS 红线 #7）。",
        "快模式底座（conftest 提供）：get_page 单浏览器/单登录；plan_ctx 跨批前置"
        "（缺前置显式终止，不静默跳过）；_fast_runlog 记 reports/runs.jsonl（Atlas 兼容行形态）。",
        "写操作步骤默认被写闸拦停（env_blocked 呈报，策略阻挡非缺陷）；"
        "ATLAS_FAST_ALLOW_WRITES=1 才执行（红线 #2/#13）。",
    ]
    if has_ui:
        lines += [
            "UI step 三路真渲染：page_object（已批准组件名）→ atlas_pages 页对象 .action()；",
            "无声明时按组件库 actions 数据 auto-wire（task→组件动作）→ .do_<元素>()（可执行，"
            "引擎不硬编码业务名）；",
            "legacy_pages（旧套件函数名）→ legacy_page_call 只读委托；",
            "操作未接线时产物显式终态（NotWired→env_blocked），不假装通过也不静默跳过"
            "（gap9b B-2 + gap9 动作语义）。",
        ]
    lines += [
        '"""',
        "from __future__ import annotations",
        "",
        "import os",
        "",
        f"from {base_mod} import {', '.join(imports_fast)}",
        f"from {SUPPORT_MODULE_NAME} import call_api",
        "",
        f'BASE_URL = os.environ.get("ATLAS_BASE_URL", {_py_literal(base_url)})',
        "",
        "",
    ]
    for spec in specs:
        lines += (_render_api_test_fast(spec) if spec.is_api
                  else _render_ui_test_fast(spec, wiring))
    return "\n".join(lines).rstrip() + "\n"


def render_test_module(specs: list[StepSpec], *, flow_id: str = "",
                       base_url: str = "", fast: bool = False,
                       components_map: dict | None = None) -> str:
    """把 StepSpec[] 渲染成一个 pytest 测试模块源码文本。

    ``fast=False``（默认）输出与快模式改造前**逐字节相同**（E.1.2 快照测钉死，#115）；
    ``fast=True`` 走快模式渲染（单浏览器 fixture/plan_ctx/runs.jsonl，见 gap9 C.2）。

    ``components_map``（仅 fast 用）：组件库映射（``_components_map`` 形态），用于从数据派生
    auto-wire 映射（task→组件动作）——非 fast 完全无视它（旧形态零涟漪）。
    """
    if fast:
        wiring = _task_action_wiring(components_map) if components_map else None
        return _render_test_module_fast(specs, flow_id=flow_id, base_url=base_url,
                                        wiring=wiring)
    has_ui = any(not s.is_api for s in specs)
    lines: list[str] = [
        '"""生成候选（generator/emit_pytest）：pytest 回归用例。',
        "",
        f"来源 flow：{flow_id or '(未命名)'}。**只生成不执行**——鉴权值/写操作须人工确认后手动运行。",
        "断言只覆盖文档明示项（存活 + 文档给出的 resultCode），未放宽（AGENTS 红线 #7）。",
    ]
    if has_ui:
        lines.append("UI step 以 pytest.mark.skip 占位，需人审接入 atlas_pages（勿当通过）。")
    lines += [
        '"""',
        "from __future__ import annotations",
        "",
        "import os",
        "",
    ]
    if has_ui:
        lines.append("import pytest")
        lines.append("")
    lines.append(f"from {SUPPORT_MODULE_NAME} import call_api")
    lines.append("")
    lines.append(
        f'BASE_URL = os.environ.get("ATLAS_BASE_URL", {_py_literal(base_url)})')
    lines.append("")
    lines.append("")
    for spec in specs:
        lines += _render_api_test(spec) if spec.is_api else _render_ui_test(spec)
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 共享底座（自包含，仅标准库 —— 可脱离 Atlas 运行）
# ---------------------------------------------------------------------------

_SUPPORT_SOURCE = '''"""生成候选（generator/emit_pytest）：回归用例共享底座（自包含，可脱离 Atlas 运行）。

依赖：仅标准库（urllib/json/os/re）。**不引入 requests/playwright/allure**（AGENTS 红线 #4）。
未解析的 ``${VAR}`` 占位符一律显式报错——绝不把占位符当字面量发出去（假请求/假通过防线）。
缺配置（base_url 空/非绝对 URL）走 ``EnvBlocked`` 点名终态，绝不漏 urllib 裸 ValueError（归因漂移）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

try:  # 快包并存时复用 _atlas_fast.EnvBlocked → 同一类对象（fast runlog 的 isinstance 才认 env_blocked）
    from _atlas_fast import EnvBlocked
except ImportError:  # 慢形态/脱离 Atlas 独立运行：自备同义类（★不得与 fast 形态混用，两形态各自独立判定）
    class EnvBlocked(RuntimeError):
        """环境/配置阻塞终态（本模块自包含定义；快包并存时复用 _atlas_fast.EnvBlocked）。"""


_PLACEHOLDER = re.compile(r"\\$\\{([^}]+)\\}")


class StepResult:
    """一次 HTTP 调用的结果（存活断言只看 ok/status，业务断言读 data）。"""

    def __init__(self, ok, status, data, text, error=""):
        self.ok = ok
        self.status = status
        self.data = data
        self.text = text
        self.error = error

    def __repr__(self):
        return (f"StepResult(ok={self.ok!r}, status={self.status!r}, "
                f"error={self.error!r}, data={self.data!r})")


def resolve_placeholders(value, env=None):
    """递归解析 ``${VAR}``（env 优先，回落 os.environ）；未解析命中即抛（不静默）。"""
    env = os.environ if env is None else env

    def _sub(match):
        key = match.group(1)
        if key in env:
            return str(env[key])
        raise KeyError(
            f"未解析占位符 ${{{key}}}：请先接 .env/环境变量（占位符绝不当字面量发出）")

    if isinstance(value, str):
        return _PLACEHOLDER.sub(_sub, value)
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, env) for v in value]
    return value


def call_api(*, method, path, base_url="", headers=None, body=None, query=None,
             timeout=30, env=None):
    """发一次 HTTP 请求。body 分流：dict/list→JSON，str/bytes→原样（与 Atlas DSL 同口径）。

    网络层异常（连接/超时/DNS 等）**不裸崩**——被收进 `StepResult(ok=False, err=…)`，
    由产物侧的存活断言归因（err 带异常类型名，如 `URLError`/`TimeoutError`），
    调用方据此判失败而非让整个用例崩掉。
    """
    method = (method or "GET").upper()
    url = (base_url or "") + str(resolve_placeholders(path, env))
    if query:
        pairs = resolve_placeholders(query, env)
        sep = "&" if "?" in url else "?"
        url += sep + "&".join(f"{k}={v}" for k, v in pairs.items())
    if not url.strip().lower().startswith(("http://", "https://")):
        # 判据单点（本函数入口，快/慢两形态共用）：缺配置→点名终态；绝不把 空/相对 URL 漏给
        # urllib 抛裸 ValueError: unknown url type（归因漂移：操作者把「少配一个键」误读成产物坏了）。
        raise EnvBlocked(
            "缺配置：ATLAS_BASE_URL（API 腿需要它；当前为空或非绝对 URL）——请在 .env/环境把 "
            "ATLAS_BASE_URL 设为绝对地址（以 http 或 https 开头、含主机名的完整 URL），"
            "或改产物 BASE_URL 默认值")
    hdrs = {k: str(v) for k, v in resolve_placeholders(headers or {}, env).items()}
    data = None
    if body is not None:
        b = resolve_placeholders(body, env)
        if isinstance(b, (dict, list)):
            data = json.dumps(b).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif isinstance(b, str):
            data = b.encode("utf-8")
        elif isinstance(b, (bytes, bytearray)):
            data = bytes(b)
        else:
            raise TypeError(
                f"body 类型不支持：{type(b).__name__}（dict/list→json=，str/bytes→data=）")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要读正文供断言
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except Exception as exc:  # 网络/连接类：记为失败，不裸崩
        return StepResult(False, 0, None, "", f"{type(exc).__name__}: {exc}")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    return StepResult(200 <= status < 300, status, parsed, raw)
'''


def emit_support_module() -> str:
    """共享底座源码（自包含，仅标准库）。"""
    return _SUPPORT_SOURCE


# ---------------------------------------------------------------------------
# 页对象库（从 Atlas 组件库 components.json 派生）
# ---------------------------------------------------------------------------


def _pascal(name: str) -> str:
    parts = re.split(r"[^0-9a-zA-Z]+", name)
    out = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return out or "Component"


def _const(name: str) -> str:
    c = re.sub(r"[^0-9a-zA-Z]+", "_", name).upper().strip("_")
    return c if c and not c[0].isdigit() else f"S_{c}" if c else "SEL"


# ---------------------------------------------------------------------------
# 组件库「动作语义」（gap9 产物 UI 腿可执行化）：元素 → 动作（kind + 可选 value/task）
# ---------------------------------------------------------------------------
# 新知识键 `actions`（组件级，元素 → 动作描述）是**增量**：
#   老 components.json 不写照常生成（该组件只出选择器+NotWired action），逐字节不变（#115）。
#   {"<元素>": {"kind": "click"|"fill"|"select"|"press", "value"?: <标量>, "task"?: "<任务名>"}}
# - kind=click 不接受 value（点了就是点了，值无意义——静默忽略=坏形态伪装合法，拒）。
# - fill/select/press 的 value 可选：给了=**绑定**（方法无参），不给=**参数**（方法收 value）。
# - task 可选：该动作认领的 flow 任务名（auto-wire 的**唯一**数据源；引擎不硬编码业务名 #11）。
_ACTION_KINDS = ("click", "fill", "select", "press")
# kind → Playwright locator 方法名（click 单独走，不需要值）
_ACTION_VALUE_METHOD = {"fill": "fill", "select": "select_option", "press": "press"}


def _action_method(element: str) -> str:
    """动作元素 → 产物页对象里可执行方法名（do_<slug>）。命名与元素一一对应（不猜语义）。"""
    return "do_" + _ident_slug(str(element), fallback="act")


def _normalized_actions(comp_name: str, spec: dict) -> dict:
    """组件的 `actions` → 规范形态 `{元素: {"kind":..., "value"?:...}}`；坏形态点名拒绝。

    生成期判据（能生成期判的不留到运行期）：未知 kind / 元素不在 selectors / click 带 value /
    value 形态坏 / 未知键——一律 `PytestEmitError` 点名组件+元素，绝不静默降级成「不可接线」。
    """
    raw = spec.get("actions")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PytestEmitError(
            f"组件 {comp_name} 的 actions 必须是映射（元素→动作），实为 {type(raw).__name__}")
    selectors = spec.get("selectors") if isinstance(spec.get("selectors"), dict) else {}
    out: dict = {}
    for element, act in raw.items():
        if not isinstance(act, dict):
            raise PytestEmitError(
                f"组件 {comp_name} 的元素 {element!r} 的 action 必须是映射"
                f"（kind/value/task），实为 {type(act).__name__}: {act!r}")
        unknown = sorted(k for k in act if k not in ("kind", "value", "task"))
        if unknown:
            raise PytestEmitError(
                f"组件 {comp_name} 的元素 {element!r} 的动作有未知键 {unknown}；只认 kind/value/task")
        kind = act.get("kind")
        if kind not in _ACTION_KINDS:
            raise PytestEmitError(
                f"组件 {comp_name} 的元素 {element!r} 的动作 kind 非法：{kind!r}"
                f"（只认 {list(_ACTION_KINDS)}）")
        if element not in selectors:
            raise PytestEmitError(
                f"组件 {comp_name} 的动作元素 {element!r} 不在 selectors 中"
                f"（已知：{sorted(selectors)}）——动作必须落在已声明的元素上，不猜")
        entry: dict = {"kind": kind}
        if "value" in act:
            if kind == "click":
                raise PytestEmitError(
                    f"组件 {comp_name} 的元素 {element!r}：click 不接受 value（点了无需值）")
            val = act["value"]
            if isinstance(val, bool) or val is None or not isinstance(val, (str, int, float)):
                raise PytestEmitError(
                    f"组件 {comp_name} 的元素 {element!r} 的 value 必须是字符串/数字（可省略），"
                    f"实为 {type(val).__name__}: {val!r}")
            entry["value"] = val
        out[element] = entry
    return out


def _task_action_wiring(comps_map: dict) -> dict:
    """组件库 → auto-wire 映射 `{任务名: (组件名, 元素)}`；歧义/坏形态点名拒绝。

    数据来自动作的 `task` 字段（**唯一**来源，引擎不含业务名 #11）。同一任务被多个动作认领
    → 拒绝（不猜工期先后/归属）；认领动作不是「可自主执行」（非 click 且未绑定 value）→ 拒绝
    （auto-wire 的调用点无值通道，绑定值必须先由数据给出——否则生成一个运行期必炸的调用）。
    """
    wiring: dict = {}
    for comp, spec in comps_map.items():
        if not isinstance(spec, dict):
            continue
        actions = _normalized_actions(comp, spec)   # 形态/kind/元素存在性先验（与渲染同一把尺）
        raw = spec.get("actions") or {}
        for element, entry in actions.items():
            task = raw[element].get("task")
            if task is None:
                continue
            if not isinstance(task, str) or not task.strip():
                raise PytestEmitError(
                    f"组件 {comp} 的元素 {element!r} 的动作 task 必须是非空字符串，实为 {task!r}")
            task = task.strip()
            if entry["kind"] != "click" and "value" not in entry:
                raise PytestEmitError(
                    f"任务 {task!r} 认领的动作（组件 {comp} 元素 {element!r}）为 "
                    f"{entry['kind']} 但未绑定 value：auto-wire 无值通道，"
                    f"请在组件库补 value 或显式声明 page_object")
            if task in wiring and wiring[task] != (comp, element):
                raise PytestEmitError(
                    f"任务 {task!r} 被多个组件动作认领：{wiring[task]} 与 {(comp, element)}——"
                    f"auto-wire 不猜（请在组件库把 task 收敛到唯一）")
            wiring[task] = (comp, element)
    return wiring


def _action_entry_lines(cls: str, elements: str, actions: dict) -> list[str]:
    """组件类 `action()` 方法体：按数据接线（0/多/需参 一律 NotWired 点名，不假装可跑）。"""
    if len(actions) == 1:
        element, entry = next(iter(actions.items()))
        method = _action_method(element)
        if entry["kind"] == "click" or "value" in entry:
            return [
                "    def action(self, *args, **kwargs):",
                f'        """组件级动作：唯一声明动作「{element}」（自组件库 actions 直出）。"""',
                f"        self.{method}()",
            ]
        return [
            "    def action(self, *args, **kwargs):",
            '        """组件级动作：唯一声明动作需外部值——请调用 do_<元素>(value)。"""',
            "        raise NotWired(",
            f'            "未接线：{cls}.action 需要参数（元素：{elements}）——'
            f'请改用 self.{method}(value)（值由数据/调用方绑定）")',
        ]
    if len(actions) > 1:
        return [
            "    def action(self, *args, **kwargs):",
            '        """组件级动作：声明了多个动作——请显式调用 do_<元素>()（不猜顺序）。"""',
            "        raise NotWired(",
            f'            "未接线：{cls}.action 有多个候选动作（元素：{elements}）——'
            f'请改用 self.do_<元素>()，或经 auto-wire 由组件库 task 认领")',
        ]
    # 无 actions：存量形态（老知识零涟漪）——逐字节不变。
    return [
        "    def action(self, *args, **kwargs):",
        '        """业务操作骨架——两条诚实腿任选其一接线（见模块 docstring）。"""',
        "        raise NotWired(",
        f'            "未接线：{cls}.action（元素：{elements}），需人审接入 Playwright 操作或 legacy_page_call 委托")',
    ]


def _action_method_lines(actions: dict) -> list[str]:
    """每个已声明动作出一个可执行方法 `do_<元素>`（click；或 bound→无参 / 无 bound→收 value）。"""
    out: list[str] = []
    for i, (element, entry) in enumerate(actions.items()):
        if i:
            out.append("")
        method = _action_method(element)
        kind = entry["kind"]
        loc = f'self.locator({_py_literal(str(element))})'
        if kind == "click":
            out.append(f"    def {method}(self):")
            out.append(f'        """自组件库 actions 直出：click（元素 {element}）。"""')
            out.append(f"        {loc}.click()")
            continue
        getter = _ACTION_VALUE_METHOD[kind]
        if "value" in entry:
            out.append(f"    def {method}(self):")
            out.append(f'        """自组件库 actions 直出：{kind}（元素 {element}，值已绑定）。"""')
            out.append(f"        {loc}.{getter}({_py_literal(entry['value'])})")
        else:
            out.append(f"    def {method}(self, value):")
            out.append(f'        """自组件库 actions 直出：{kind}（元素 {element}，值由调用方绑定）。"""')
            out.append(f"        {loc}.{getter}(value)")
    return out


def _selector_value(sel, *, where: str):
    """元素选择器 → 产物里的字面量值（CSS 字符串原样；结构化描述转 IR 并**生成期**校验）。

    坏描述当场点名整份导出，而不是把一份跑不了的定位写进产物——产物是给人审后直接跑的，
    「生成了但一点就炸」比拒绝生成更坏（与 gap9b 两道闸同纪律：能生成期判的不留到运行期）。
    """
    if isinstance(sel, str):
        return sel
    try:
        return locators.parse(sel)
    except locators.LocatorSpecError as exc:
        raise ValueError(f"{where} 的定位描述形态坏：{exc}") from exc


def _selector_member_lines(selectors: dict, *, where: str) -> list[str]:
    """元素常量 + SELECTORS 映射（纯 CSS 组件与改造前逐字节同形，fast/非 fast 共用一处）。"""
    out = [f"    {_const(elem)} = {_py_literal(_selector_value(sel, where=where))}"
           for elem, sel in selectors.items()]
    out.append("")
    out.append("    SELECTORS = {")
    out.extend(f'        {_py_literal(str(elem))}: {_const(elem)},' for elem in selectors)
    out.append("    }")
    return out


def _locator_method_lines(selectors: dict) -> list[str]:
    """`locator()` 方法体：全 CSS 组件**逐字节等于改造前**；含结构化描述才多出 ARIA 分支。

    分支集合由 `locators.KINDS` 的 IR 派生（生成期已规范化），产物侧零 import：它是派生物，
    不是第二把尺——形态的真源仍是组件库 + `atlas.locators`。
    """
    if not any(not isinstance(sel, str) for sel in selectors.values()):
        return [
            "    def locator(self, element):",
            '        """按元素名取 locator；未知名点名（不静默 None）。"""',
            "        sel = self.SELECTORS.get(element)",
            "        if sel is None:",
            '            raise KeyError('
            'f"{type(self).__name__} 无元素 {element!r}；已知：{sorted(self.SELECTORS)}")',
            "        return self.page.locator(sel)",
            "",
        ]
    return [
        "    def locator(self, element):",
        '        """按元素名取 locator；未知名点名（不静默 None）。\n'
        '        两形态（gap13）：CSS 字符串，或 IR 映射 '
        '{"kind": "role", "role": .., "name": ..}——ARIA 优先由组件库人审内容决定。\n'
        '        判据同构：两分支的未知名 KeyError 消息逐字相同，改判据时两处一起改（勿只改其一）。"""',
        "        spec = self.SELECTORS.get(element)",
        "        if spec is None:",
        '            raise KeyError('
        'f"{type(self).__name__} 无元素 {element!r}；已知：{sorted(self.SELECTORS)}")',
        "        if isinstance(spec, str):",
        "            return self.page.locator(spec)",
        '        kind = spec.get("kind")',
        '        if kind == "role":',
        '            name = spec.get("name")',
        "            if name is None:",
        '                return self.page.get_by_role(spec["role"])',
        '            return self.page.get_by_role(spec["role"], name=name, exact=True)',
        '        if kind == "label":',
        '            return self.page.get_by_label(spec["label"])',
        '        if kind == "testid":',
        '            return self.page.get_by_test_id(spec["testid"])',
        "        raise ValueError(",
        '            f"{type(self).__name__} 元素 {element!r} 的定位描述形态未知：{spec!r}")',
        "",
    ]


def _components_map(components: dict | None) -> dict:
    """接受 {"components": {...}} 或裸 {...}；坏形态当空（不裸崩，与 SelectorRegistry 同纪律）。"""
    if not isinstance(components, dict):
        return {}
    raw = components.get("components", components)
    if not isinstance(raw, dict):
        return {}
    return {name: spec for name, spec in raw.items() if isinstance(spec, dict)}


def _emit_pages_module_fast(comps: dict) -> str:
    """快模式页对象库（gap9 C.2 + lead 裁决 A/F-2/F-7）。

    两条诚实腿：腿一=人审在本文件写真实 Playwright 操作（选择器常量已派生）；
    腿二=``legacy_page_call`` 只读委托旧套件页面函数——位置/函数名全部来自配置，
    本模板零业务字面量（红线 #11），绝不写旧仓（红线 #4）。
    未接线显式终态：配置缺失→``EnvBlocked("缺配置：…")``；动作未接线→
    ``NotWired("…未接线…")``（``_atlas_fast`` 单点定义，归因走继承，本层不复制判据 #92）。
    """
    base_mod = _fast_base_module()
    lines: list[str] = [
        '"""生成候选（generator/emit_pytest）：页对象库（快模式），派生自 Atlas 组件库 components.json。',
        "",
        "形态标记：**快模式**（未接线 UI 动作抛 NotWired→env_blocked，见下）。",
        "复用 Atlas 实测选择器（单一来源仍是 components.json；本文件是只读派生，人审后可改）。",
        f"依赖：Playwright 的 page 对象（``__init__(page)``）＋同目录底座 ``{base_mod}``。",
        "两条诚实腿（未接线一律显式终态，绝不静默放过/假装通过，红线 #9/#7）：",
        "  腿一：人审把真实 Playwright 操作写进各 action()（组件类内选择器已就绪）；",
        "  腿二：action() 内 return legacy_page_call(\"<旧函数名>\", self.page, ...) 只读委托",
        "        旧套件——调用位与函数名一律来自配置/env，本文件不含业务字面量（红线 #11），",
        "        绝不写旧仓（红线 #4）。",
        '"""',
        "from __future__ import annotations",
        "",
        "import importlib",
        "import os",
        "import sys",
        "",
        f"from {base_mod} import EnvBlocked, NotWired",
        "",
        f'LEGACY_WEB_ENV_KEY = "ATLAS_LEGACY_WEB"',
        'LEGACY_PAGES_MODULE = "lib.pages"',
        "",
        "",
        "def legacy_page_call(func_name, *args, **kwargs):",
        '    """诚实腿二（可选）：只读委托旧套件页面函数。旧项目位来自 env，函数名由人审提供（不猜）。"""',
        '    web = os.environ.get(LEGACY_WEB_ENV_KEY, "").strip()',
        "    if not web:",
        "        raise EnvBlocked(",
        '            f"缺配置：{LEGACY_WEB_ENV_KEY} 未设置（旧项目 web 子目录绝对路径，只读复用）")',
        "    if web not in sys.path:",
        "        sys.path.insert(0, web)",
        "    try:",
        "        mod = importlib.import_module(LEGACY_PAGES_MODULE)",
        "    except ImportError as exc:",
        "        raise EnvBlocked(",
        '            f"缺配置：{LEGACY_WEB_ENV_KEY} 指向的目录下无法 import {LEGACY_PAGES_MODULE}（{exc}）") from exc',
        "    fn = getattr(mod, str(func_name), None)",
        "    if fn is None or not callable(fn):",
        "        raise EnvBlocked(",
        '            f"缺配置：旧套件 {LEGACY_PAGES_MODULE} 无可调用函数 {func_name!r}（函数名须人审核对，不猜）")',
        "    return fn(*args, **kwargs)",
        "",
        "",
        "def login(page, *, base_url, account, password):",
        '    """登录骨架（快模式）：先查 env 再判接线（F-7 两义分立，禁止 or 文案）。',
        "",
        "    接线后请改为真操作：实查登录态指示器为权威（磁盘记录只作参考）；未登录时",
        "    navigate 登录页、用组件库选择器填表提交、再等指示器出现。绝不假绿（红线 #7）。",
        '    """',
        "    try:",
        "        if not account or not password:",
        "            raise EnvBlocked(",
        '                "缺配置：登录凭据未提供（ATLAS_FAST_ACCOUNT→DEMO_USERNAME / "',
        '                "ATLAS_FAST_PASSWORD→DEMO_PASSWORD，凭据只从 env 读，红线 #1）")',
        '        indicator = os.environ.get("ATLAS_FAST_LOGIN_INDICATOR", "").strip()',
        "        if not indicator:",
        "            raise EnvBlocked(",
        '                "缺配置：ATLAS_FAST_LOGIN_INDICATOR 未设置（登录态指示器选择器须人审提供，模板不猜）")',
        "        raise NotWired(",
        '            "登录未接线：atlas_pages.login（登录表单/提交按钮元素须人审接入组件库后补真操作）")',
        "    finally:",
        "        # 凭据归零（红线②/L-PW）：本机 pytest 默认打印抛出帧的 f_locals——不清零则",
        "        # password/base_url 明文进 stdout。这里只守**模板自身**的 raise 帧（用户接线代码不在内）。",
        "        password = None",
        "        base_url = None",
        "",
        "",
    ]
    class_names = [_pascal(name) for name in comps]
    lines.extend(_py_all_lines(['login', 'legacy_page_call', *class_names]))
    lines.append("")
    lines.append("")
    for name, spec in comps.items():
        cls = _pascal(name)
        notes = str(spec.get("notes") or "").strip()
        selectors = spec.get("selectors") if isinstance(spec.get("selectors"), dict) else {}
        models = spec.get("models") if isinstance(spec.get("models"), dict) else {}
        lines.append(f"class {cls}:")
        lines.append(f'    """Atlas 组件 {name}。{notes}"""')
        if selectors:
            lines.extend(_selector_member_lines(selectors, where=f"组件 {name}"))
        if models:
            lines.append(f"    # models: {_py_literal(models)}"
                         "（当前无引擎消费方，仅供接线参考）")
        lines.append("")
        lines.append("    def __init__(self, page):")
        lines.append("        self.page = page")
        lines.append("")
        if selectors:
            lines.extend(_locator_method_lines(selectors))
        elements = "/".join(str(e) for e in selectors) if selectors else "（组件库暂无该组件元素）"
        actions = _normalized_actions(name, spec)
        lines.extend(_action_entry_lines(cls, elements, actions))
        if actions:
            lines.append("")
            lines.extend(_action_method_lines(actions))
        lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def emit_pages_module(components: dict | None, *, fast: bool = False) -> str:
    """从 Atlas 组件库派生页对象库源码文本（形态 2 的共享页对象层）。

    每个组件生成一个类：选择器常量（复用 components.json 实测选择器）＋ locator 取用器
    ＋ 操作骨架（``NotImplementedError``，绝不假装通过）。依赖 Playwright 的 page 对象，
    在模块 docstring 显式声明。

    ``fast=True``（gap9）：操作骨架改 ``NotWired``（未接线归 env_blocked 的类型级判据在
    底座 ``_atlas_fast`` 单点，本层只负责抛对位置，F-2），并额外产出 ``login`` 骨架与
    诚实腿二 ``legacy_page_call``。``fast=False`` 输出与改造前逐字节相同（E.1.2 快照）。
    """
    comps = _components_map(components)
    if fast:
        return _emit_pages_module_fast(comps)
    lines: list[str] = [
        '"""生成候选（generator/emit_pytest）：页对象库，派生自 Atlas 组件库 components.json。',
        "",
        "形态标记：**慢模式**（未接线 UI 动作为 NotImplementedError 骨架，见下）。",
        "复用 Atlas 实测选择器（单一来源仍是 components.json；本文件是只读派生，人审后可改）。",
        "依赖：Playwright 的 page 对象（``__init__(page)``）。不含任何 Atlas 运行时 import。",
        "操作骨架一律 NotImplementedError —— 未接线即明确失败，绝不静默放过（红线 #9/#7）。",
        '"""',
        "from __future__ import annotations",
        "",
    ]
    class_names = [_pascal(name) for name in comps]
    lines.extend(_py_all_lines(class_names))
    lines.append("")
    lines.append("")
    for name, spec in comps.items():
        cls = _pascal(name)
        notes = str(spec.get("notes") or "").strip()
        selectors = spec.get("selectors") if isinstance(spec.get("selectors"), dict) else {}
        models = spec.get("models") if isinstance(spec.get("models"), dict) else {}
        lines.append(f"class {cls}:")
        lines.append(f'    """Atlas 组件 {name}。{notes}"""')
        if selectors:
            lines.extend(_selector_member_lines(selectors, where=f"组件 {name}"))
        if models:
            lines.append(f"    # models: {_py_literal(models)}"
                         "（当前无引擎消费方，仅供接线参考）")
        lines.append("")
        lines.append("    def __init__(self, page):")
        lines.append("        self.page = page")
        lines.append("")
        if selectors:
            lines.extend(_locator_method_lines(selectors))
        lines.append("    def action(self, *args, **kwargs):")
        lines.append('        """业务操作骨架：需人审接入真实操作（见 docstring / notes）。"""')
        lines.append(
            f'        raise NotImplementedError('
            f'"生成候选：{cls} 的业务操作需人审接入（选择器已就绪）")')
        lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def emit_pytest_module(endpoints: list[Endpoint], *, flow_id: str = "",
                       base_url: str = "", global_headers: dict | None = None,
                       naming: str = "resp_{n}_{slug}",
                       fast: bool = False) -> str:
    """从归一化模型（Endpoint[]）生成 pytest 测试模块源码文本（只生成不执行）。

    仅测试/兼容用：单文件源码形态入口；主路径 ``emit_pytest_package`` 内联同逻辑。
    """
    specs = _specs_from_endpoints(endpoints, global_headers=global_headers, naming=naming)
    if not flow_id and endpoints:
        flow_id = slugify(f"{endpoints[0].summary} {endpoints[0].path}", fallback="flow")
    if not base_url and endpoints:
        base_url = endpoints[0].base or ""
    return render_test_module(specs, flow_id=flow_id, base_url=base_url, fast=fast)


def emit_pytest_from_flow(flow: dict, *, base_url: str = "",
                          fast: bool = False, components: dict | None = None) -> str:
    """从 flow dict 生成 pytest 测试模块源码文本（api_call→真调用；UI→fast 真渲染/skip 骨架）。

    仅测试/兼容用：单文件源码形态入口；主路径 ``emit_pytest_package`` 内联同逻辑。

    ``components``（仅 fast 有意义）：组件库（``{"components": {...}}`` 或裸映射），供 fast 的
    auto-wire 从数据派生「任务名→组件动作」。缺省 None=无 auto-wire 数据源（UI 步仍须声明位）。
    """
    specs = _specs_from_flow(flow)
    comps_map = _components_map(components) if components is not None else None
    return render_test_module(specs, flow_id=str(flow.get("id") or ""), base_url=base_url,
                              fast=fast, components_map=comps_map)


def _default_components() -> dict:
    """惰性取组件库（走 plugin 缝，不在本模块硬编码业务字面量，红线 #11）。失败回 {}。

    必须走 `load_composed`（组合层）而非 `load`（影子层）：影子层会被平台包自带的
    `components.json` **整份遮蔽**通用组件库——15 个跨平台组件（login/table/dialog/
    pagination/…）对生成器不可见，产物页对象库缺这些类，`--fast` 声明位闸（F-10）
    会把「用通用组件的合法 flow」判成「类名不存在」拒绝。实测：影子 7 条 vs 组合 22 条。
    """
    try:
        from ..plugin_api import resolve_plugin
        return resolve_plugin().knowledge().load_composed("components")
    except Exception:
        return {}


def _fast_ui_gates(specs: list[StepSpec], comps_map: dict) -> None:
    """F-9/F-10 生成期第二道闸（gap9b B-2 两道闸分工）：声明位必须在页对象库里兑现。

    第一道=compile 静态段管**形态**（非空标量/互斥/拒点分——cli-impl #11，dsl 落点）；
    本闸管**成员资格**（``_pascal(page_object)`` ∈ ``emit_pages_module(components)``
    实际派生的类名集合——components 只在生成期存在，dsl 拿不到，天然分层，判据零复制）。
    UI 步放行 ⇔ page_object 良形且命中类名集，**或** legacy_pages 良形（委托腿不需要
    组件类——lib.pages 在旧仓，运行期由 legacy_page_call 的 EnvBlocked 三分支兜）。
    缺失/双有/坏形态经 _ui_leg 消费单点点名拒绝；**拒绝先于 mkdir、零残留**（F-10③）。
    纯 API 源零 UI 腿——无组件库也照常放行（#115：闸门不得把能用路径翻成错误）。
    """
    ui = [s for s in specs if not s.is_api]
    if not ui:
        return
    if not comps_map:
        raise PytestEmitError(
            "--fast 的 UI 腿需要组件知识：当前无组件库，atlas_pages.py 不会产出，"
            "UI 用例在 pytest 收集期即引用失败。请先经 `atlas review` 批准 "
            "selector_candidate 补组件库，或纯 API 源照常用 --fast。")
    known = {_pascal(name) for name in comps_map}
    wiring = _task_action_wiring(comps_map)   # auto-wire 数据源（坏形态在此点名）
    for s in ui:
        kind, name, element = _ui_leg(s, wiring)  # 缺失/双有/坏形态在此点名（与渲染同一消费单点）
        if kind == "page_object":
            cls = _pascal(name)
            if cls not in known:
                raise PytestEmitError(
                    f"页对象 {name} 无对应已批准组件（UI 步 {s.index}「{s.slug}」引用类 {cls}）；"
                    f"已知组件：{sorted(comps_map)}。先经 `atlas review --approve` 批准组件或改对"
                    f"声明位——引擎不做相似度猜测（gap9b B-2/F-10，红线 #11）。")
        elif kind == "auto":
            cls = _pascal(name)   # 组件来自组件库数据，必已存在；仍显式对一次（防数据/渲染漂移）
            if cls not in known:
                raise PytestEmitError(
                    f"auto-wire 派生组件 {name}（类 {cls}）不在页对象库中（UI 步 {s.index}"
                    f"「{s.slug}」）；已知组件：{sorted(comps_map)}——组件库数据与渲染不一致。")


def _gate_spec_headers(specs: list[StepSpec]) -> list[tuple[int, str, str]]:
    """C4① 字面敏感头闸（红线 #2：凭据不落盘）——**判据唯一实现在 `credential_gate`**，
    此处只调不复制名单/净化规则（#92，与 emit_flow 同一把刀）。

    把 `specs[*].headers` 里手写的字面凭据（`Authorization`/`Cookie`/`*-sessionid` +
    字面 UUID/JWT…）就地换成 `${ATLAS_HDR_n}` 占位，返回 `(序号, 头名, env 键)` 映射
    （**不回显值**——值永不进任何输出通道）。

    两条产物入口都必须过此闸，否则字面凭据逐字进产物 `test_*.py`（=凭据落盘，红线）：
    - `export-pytest` 直接吃用户的 `flow.yaml`，步骤 headers 常是字面鉴权值；
    - `gen --with-pytest` 的 **pytest 半产物**从 `endpoints + global_headers` 出——YAML 那半
      已在 emit_flow 过闸，这半之前没有人闸（t8-verify 实锤：同次 gen，YAML 净化了、
      pytest 半产物仍含字面 token）。
    `${VAR}`/`${__P()}` 这类**已外部化**的值 `header_is_sensitive` 恒放行、原样进产物，
    运行期未接线由产物侧 `resolve_placeholders` 报 `KeyError`（正向对照不丢）。

    借用 flow 形状载体让同一把刀就地替换：`spec.headers` 是各自独立的 dict（`_specs_*` 里
    `dict(...)` 构造），替换在该对象上可见、不回写调用方的 flow dict。
    """
    carrier = {"steps": [{"headers": spec.headers} for spec in specs]}
    return gate_flow_headers(carrier)


def emit_pytest_package(*, out_dir: Path, endpoints: list[Endpoint] | None = None,
                        flow: dict | None = None, flow_id: str = "",
                        base_url: str = "", global_headers: dict | None = None,
                        components: dict | None = None, naming: str = "resp_{n}_{slug}",
                        force: bool = False, fast: bool = False,
                        compat: str = "none", parallel: int = 0) -> list[str]:
    """写出一份可独立运行的 pytest 回归代码包（只生成不执行），返回写入的文件路径。

    产物：``test_<id>.py`` ＋ ``atlas_pages.py``（有组件库时）＋ ``_atlas_support.py``
    ＋ ``README.md``。``components=None`` 时惰性取 Atlas 组件库；取不到则不出页对象库。

    ``fast=True``（gap9 C.2）：用例/页对象走快模式渲染，并追加 ``emit_fast.write_fast_artifacts``
    写 ``_atlas_fast.py`` ＋ ``conftest.py``（依赖方向单向 emit_pytest→emit_fast，只用契约
    公开符号）。冲突异常统一转 ``ValueError`` 消息（PytestEmitError/FastEmitError 皆子类型，
    契约表 C.2 钉死）。

    ``compat="legacy-suite"``（gap11 C.1-C.5）：整包切换为旧仓兼容快包（双轨腿 + vendored
    垫片），委托 ``emit_compat.emit_compat_package``——组合判据复用 **emit_compat 单点**
    ``compat_combo_problem``（CLI 面与本面同一份 if，#92/lead P0；本入口绕过 CLI 也拒），
    且拒绝发生在本函数任何写盘之前（out_dir 不得创建，xcheck C9① 格）。
    """
    # 组合闸：判据唯一位在 emit_compat.compat_combo_problem——此处只调用不重写（#92）。
    from .emit_compat import compat_combo_problem
    _combo = compat_combo_problem(compat, fast)
    if _combo:
        raise PytestEmitError(_combo)
    # 并行分片参数（T3）：N<=1=单进程；N>1 仅快模式（产物分片依赖快包底座与 xdist）——拒绝
    # 发生在任何写盘之前（out_dir 不得创建，与 F-9 闸同纪律）。
    try:
        parallel = int(parallel)
    except (TypeError, ValueError):
        raise PytestEmitError(f"--parallel 需整数：{parallel!r}（0/1=单进程，>1 需 pytest-xdist）")
    if parallel < 0:
        raise PytestEmitError(f"--parallel={parallel} 不合法：0/1=单进程，>1 需 pytest-xdist")
    if parallel > 1 and not fast:
        raise PytestEmitError("--parallel>1 仅快模式（--fast）支持：产物分片依赖快包底座")
    if compat == "legacy-suite":
        from .emit_compat import emit_compat_package
        return emit_compat_package(out_dir=out_dir, flow=flow, flow_id=flow_id,
                                   force=force)
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        raise PytestEmitError(f"目标目录非空：{out_dir}（加 force=True 覆盖/继续）")
    if endpoints is None and flow is None:
        raise PytestEmitError("必须提供 endpoints 或 flow 之一")
    if flow is not None:
        specs = _specs_from_flow(flow, naming=naming)
        flow_id = flow_id or str(flow.get("id") or "")
    else:
        specs = _specs_from_endpoints(endpoints or [], global_headers=global_headers,
                                      naming=naming)
        if not flow_id and endpoints:
            flow_id = slugify(f"{endpoints[0].summary} {endpoints[0].path}", fallback="flow")
        if not base_url and endpoints:
            base_url = endpoints[0].base or ""
    # C4① 字面敏感头闸（红线 #2）：两条入口（flow / endpoints+global_headers）渲染前都过此闸，
    # 字面凭据换占位——否则 export-pytest / gen --with-pytest 的字面鉴权值逐字落产物（凭据落盘）。
    _gate_spec_headers(specs)
    mod_id = _ident_slug(flow_id or "regression", fallback="regression")

    comps = components if components is not None else _default_components()
    comps_map = _components_map(comps)
    # F-9 闸门必须在任何写盘之前——拒绝不得残留半成品（正向对照测钉「目录都别建」）。
    if fast:
        # uf-3：声明族 vs 选择器前缀（换族接入错配）——判据唯一在 atlas.ui_family（#92）。
        # 生成期闸：只在组件库**显式声明族**且与选择器前缀冲突时拒（未声明族不猜，避免把
        # 「通用包经 base_roots 带入 element-ui 族」误报）。拒绝先于 mkdir、零残留。
        from ..ui_family import family_prefix_problems
        _fam_problems = family_prefix_problems(comps)
        if _fam_problems:
            raise PytestEmitError(
                "组件库声明族与选择器前缀不符（换族接入错配）：" + "；".join(_fam_problems)
                + "——请修正 components.json 的 family 声明或选择器后再生成")
        _fast_ui_gates(specs, comps_map)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    test_path = out_dir / f"test_{mod_id}.py"
    test_path.write_text(
        render_test_module(specs, flow_id=flow_id, base_url=base_url, fast=fast,
                           components_map=comps_map),
        encoding="utf-8")
    written.append(str(test_path))

    support_path = out_dir / f"{SUPPORT_MODULE_NAME}.py"
    support_path.write_text(emit_support_module(), encoding="utf-8")
    written.append(str(support_path))

    if comps_map:
        pages_path = out_dir / f"{PAGES_MODULE_NAME}.py"
        pages_path.write_text(emit_pages_module(comps, fast=fast), encoding="utf-8")
        written.append(str(pages_path))

    readme_path = out_dir / "README.md"
    readme_path.write_text(_build_readme(flow_id, specs, bool(comps_map),
                                         fast=fast, parallel=parallel),
                           encoding="utf-8")
    written.append(str(readme_path))

    if fast:
        # 契约 C.2：只 import emit_fast 的公开符号（write_fast_artifacts / FastEmitError），
        # 不引用 _FAST_* 模板常量；冲突异常统一转 ValueError 消息。
        from .emit_fast import FastEmitError, write_fast_artifacts
        try:
            written += write_fast_artifacts(out_dir, force=force)
        except (PytestEmitError, FastEmitError) as exc:
            raise ValueError(str(exc)) from exc
    return written


def _readme_header_placeholders(specs: list[StepSpec]) -> list[str]:
    """从步骤头里提取 `${VAR}` 占位符名（ob-6：README 不再内嵌业务字面量）。

    旧 README 硬编码 `export ISC_APP_HEADER=…`——这是某平台的业务识别头名，非 ISC 平台
    拿到一份「要填一个自己根本不用的头」的说明（产物模板把平台知识泄漏给所有人）。改为
    从**实际步骤头**推导：产物里真有什么占位符，README 就提示填什么；一个都没有就不出这行。
    保持确定性（排序去重），不猜业务语义。
    """
    import re
    names: set[str] = set()
    for spec in specs:
        for value in (spec.headers or {}).values():
            names.update(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(value)))
    return sorted(names)


def _build_readme(flow_id: str, specs: list[StepSpec], has_pages: bool,
                  *, fast: bool = False, parallel: int = 0) -> str:
    """产物 README（读者第一接触面）。

    ae-6 七条（评审 `docs/gaps/wave4-artifact-readability.md` §6）：
    横幅不带内层加粗（外层 `> **横幅**：` 保结构）、运行命令真跑用例（`pytest -q`）、
    快/慢两形态各自渲染未接线语义（慢=skip 占位 / 快=NotWired→env_blocked）、
    仓库内文档链接注明「产物机上无此文件」、双入口名、env 全量键从 `emit_fast.ENV_KEYS`
    唯一清单渲染（不手抄、不写死键数）、补本包文件清单。
    ob-6：运行段的头占位符从 `specs[*].headers` 推导（`_readme_header_placeholders`），
    不在模板里硬编码 `ISC_APP_HEADER`——非 ISC 平台不再被塞一个不适用的头名。
    """
    n_api = sum(1 for s in specs if s.is_api)
    n_ui = len(specs) - n_api
    n_write = sum(1 for s in specs if s.is_write)
    mod_id = _ident_slug(flow_id or "regression", fallback="regression")
    lines = [
        "# Atlas pytest 产物（候选）",
        "",
        f"来源 flow：`{flow_id or '(未命名)'}`；步骤 {len(specs)}（API {n_api} / UI {n_ui}，"
        f"其中写操作 {n_write}）。",
        "",
        f"> **横幅**：{_README_BANNER}",
        "",
        "## 形态说明",
        "",
        "- 主干：pytest 用例 + 共享页对象库（形态 2）；页对象库派生自 Atlas 组件库 components.json。",
        "- 底座 `_atlas_support.py` 自包含（仅标准库）——**可脱离 Atlas 运行**（形态 3 的可移植性）。",
        "- 形态评审结论在 Atlas 仓库 `docs/gaps/gap7-regression-code-eval.md`；"
        "本产物可脱离 Atlas 运行，产物机上没有该文件，读不到可忽略。",
        "",
        "## 本包文件",
        "",
        f"- `test_{mod_id}.py` — 生成的 pytest 用例（写操作步骤带 `[写操作-需人工确认]` 标）。",
        f"- `{SUPPORT_MODULE_NAME}.py` — 自包含底座（仅标准库：base_url/占位符/调接口/存活断言）。",
    ]
    if has_pages:
        lines.append(
            f"- `{PAGES_MODULE_NAME}.py` — 页对象库（派生自 Atlas 组件库实测选择器）。")
    lines.append("- `README.md` — 本文件。")
    if fast:
        from .emit_fast import FAST_SUPPORT_MODULE_NAME
        lines += [
            f"- `{FAST_SUPPORT_MODULE_NAME}.py` — 快模式底座（语义等待/会话通道/看门狗工具/"
            "Atlas 兼容 runlog）。",
            "- `conftest.py` — 快模式 fixtures（sole_page/get_page/plan_ctx/_case_watchdog/"
            "_fast_runlog/_write_gate）。",
            "- `reports/runs.jsonl` — 运行期生成（`_fast_runlog` 写，Atlas 兼容行形态；非初始文件）。",
        ]
    lines += [
        "",
        "## 运行",
        "",
        "```bash",
    ]
    lines.append("# 依赖：pytest + playwright（零新增运行时依赖）" if fast
                 else "# 依赖：仅 pytest（UI step 另需 playwright，未接线时以 skip 占位）")
    lines.append("export ATLAS_BASE_URL='https://<host>'      # 或改 BASE_URL 默认值")
    # ob-6：头占位符按产物实际用到的 `${VAR}` 渲染（无业务字面量）。
    for placeholder in _readme_header_placeholders(specs):
        lines.append(f"export {placeholder}='<按环境填写>'      # 步骤头占位符 ${{{placeholder}}}")
    lines.append("pytest -q")
    lines += ["```", ""]
    if fast:
        lines.append(
            "UI step 未接线时运行期显式终态 `NotWired` → runs.jsonl 记 env_blocked（点名组件/元素，"
            "**不是跳过也不是通过**），详见下「快模式」与「页对象库」。")
    else:
        lines.append("未接线的 UI step 以 skip 占位（**不是通过**，须人审接线后方可跑）。")
    lines += [
        "",
        "未解析的 `${VAR}` 占位符会**显式报错**（绝不把占位符当字面量发出）。",
    ]
    if has_pages:
        lines += ["", "## 页对象库", ""]
        if fast:
            lines.append(
                f"`{PAGES_MODULE_NAME}.py` 复用 Atlas 组件库实测选择器；UI 动作未接线时产物显式终态"
                "（`NotWired`→env_blocked 并点名组件/元素）。接线两条诚实腿任选：在 "
                f"`{PAGES_MODULE_NAME}.py` 写真 Playwright 操作，或 `legacy_page_call` 只读委托旧套件"
                "（位置/函数名来自配置，勿在产物硬编码业务字面量）。")
        else:
            lines.append(
                f"`{PAGES_MODULE_NAME}.py` 复用 Atlas 组件库实测选择器；操作骨架为 "
                "NotImplementedError，须人审接入后方可用于 UI step。")
    lines += [
        "",
        "## 人审指引",
        "",
        "1. 审断言：仅覆盖文档明示项；`TODO(人审)` 处按业务补断言（**勿放宽**）。",
        "2. 写操作（`[写操作-需人工确认]`）执行前确认环境只读保护与「非人工创建」备注。",
        "3. 产物进 `atlas review` 审核塔（trace_step，不自动写知识）。",
    ]
    if fast:
        from .emit_fast import ENV_KEYS
        env_lines = [
            (f"  - `{key}`（默认 `{default}`）" if default is not None
             else f"  - `{key}`（无默认）")
            for key, default in ENV_KEYS.items()
        ]
        lines += [
            "",
            "## 快模式（fast，gap9）",
            "",
            "- 依赖栈：`pytest + playwright`（均为 Atlas 既有声明依赖/测试基建，零新增运行时）；",
            "  **禁用** allure-pytest / requests / pytest-playwright / pytest-timeout（conftest 已注销后者、",
            "  看门狗自研）。",
            "- 机制：`get_page` 单浏览器/单登录（登录态以页面指示器实查为权威）；`plan_ctx` 跨批前置",
            "  （缺键显式终止，不静默跳）；语义等待在底座 `_atlas_fast`（轮询 tick，无固定睡眠）；",
            "  成功不截图，仅失败留证；`_fast_runlog` 写 `reports/runs.jsonl`（Atlas 兼容行形态）。",
            "- 消费：",
            "  `pytest <本目录> -q`；",
            "  `atlas allure <本目录>/reports/runs.jsonl --out <本目录>/reports/allure-results`。",
            "- env 旋钮常用项：`ATLAS_BASE_URL`（API 腿基址，回落 `ATLAS_FAST_BASE_URL`）、",
            "  `ATLAS_FAST_LOGIN_INDICATOR`（必填指示器选择器，缺→env_blocked）、",
            "  `ATLAS_FAST_ACCOUNT`/`ATLAS_FAST_PASSWORD`（回落 `DEMO_USERNAME`/`DEMO_PASSWORD`——",
            "  这是 demo/旧项目兼容回落位，自有平台请直接设 `ATLAS_FAST_*` 主键；凭据只从 env 读）、",
            "  `CASE_TIMEOUT`（看门狗秒数，默认 600）、`ATLAS_FAST_ALLOW_WRITES=1`（写步骤放行，默认拦停）、",
            "  `ATLAS_FAST_CDP`+`ATLAS_FAST_INTERACTIVE=1`+`ATLAS_FAST_ATTACH_MATCH`（attach 三条件，",
            "  见 `conftest.py` 顶部）、`ATLAS_BROWSER_CHANNEL`（chromium|chrome|firefox|webkit，缺省 chromium）、",
            "  `ATLAS_RUNS_DIR`、`ATLAS_BROWSER_EXECUTABLE`（系统浏览器逃生舱）、`CDP_DEBUG=1`（强制有头）、",
            "  `ATLAS_LEGACY_WEB`（诚实腿二：旧套件只读委托位）。",
            f"- 全量键清单（{len(ENV_KEYS)} 键，生成器从 `emit_fast.ENV_KEYS` 唯一清单渲染；"
            "产物内实际读取点在 `_atlas_fast.py`；另有外键 `ATLAS_BASE_URL` 与步骤头占位符"
            "（见「运行」段按本包实际用到的 `${VAR}` 列出））：",
            *env_lines,
            "- `ATLAS_FAST_TRACE`/`ATLAS_FAST_TRACE_ACCEPT_SECRETS` 双确认才开失败腿 trace"
            "（zip 含密码，勿外发/勿入库，红线 #2 显式例外）。",
            "- 红线警示：只生成不执行（#9）；断言不放宽（#7，`TODO(人审)` 不转恒真）；",
            "  生产禁写第二层仍由 Atlas engine 兜底，本产物 README 不豁免人工确认。",
        ]
    if fast and parallel > 1:
        lines += [
            "",
            f"## 并行分片（--parallel {parallel}，T3）",
            "",
            f"- 运行：`ATLAS_FAST_PARALLEL={parallel} pytest -n {parallel} -q`。",
            "- 依赖：**pytest-xdist**（`pip install pytest-xdist`）——底座在 `pytest_configure` 校验；",
            "  声明 `ATLAS_FAST_PARALLEL>1` 却缺 xdist 会**点名报错**，绝不静默单进程（缺依赖不降级）。",
            "- 代价与边界：`sole_page` 是 module scope——每个 worker 首次触达某模块即各自 launch+login，",
            f"  故 {parallel} worker ≈ 最多 {parallel} 只浏览器/{parallel} 次登录（内存与句柄线性上升）；",
            "  跨批 `plan_ctx` 在并行下**不可靠**（worker 无固定顺序、同一文件并发读写）——",
            "  仅适用于彼此独立的批次；有前置依赖的批次请单进程跑。",
        ]
    return "\n".join(lines) + "\n"
    if fast and parallel > 1:
        lines += [
            "",
            f"## 并行分片（--parallel {parallel}，T3）",
            "",
            f"- 运行：`ATLAS_FAST_PARALLEL={parallel} pytest -n {parallel} -q`。",
            "- 依赖：**pytest-xdist**（`pip install pytest-xdist`）——底座在 `pytest_configure` 校验；",
            "  声明 `ATLAS_FAST_PARALLEL>1` 却缺 xdist 会**点名报错**，绝不静默单进程（缺依赖不降级）。",
            "- 代价与边界：`sole_page` 是 module scope——每个 worker 首次触达某模块即各自 launch+login，",
            f"  故 {parallel} worker ≈ 最多 {parallel} 只浏览器/{parallel} 次登录（内存与句柄线性上升）；",
            "  跨批 `plan_ctx` 在并行下**不可靠**（worker 无固定顺序、同一文件并发读写）——",
            "  仅适用于彼此独立的批次；有前置依赖的批次请单进程跑。",
        ]
    return "\n".join(lines) + "\n"

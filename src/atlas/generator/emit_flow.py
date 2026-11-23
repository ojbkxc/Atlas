"""Endpoint → flow dict（DSL）映射：api_call step、body 分流、头合并、断言。"""
from __future__ import annotations
import re
from typing import Any

from ..credential_gate import gate_flow_headers
from .model import Endpoint
from .dsl_utils import (slugify, collect_path_query, form_pairs_to_string,
                        body_to_yaml_value, identifier_slug)


class FlowEmitError(ValueError):
    """生成 flow 时的结构问题（如 save_as 冲突，--strict 下 rc 3）。"""


# `BodySpec.kind` 的**唯一取值域**（与 `model.BodySpec` 的 docstring 逐项对应）。
# 为什么要在这里再写一遍：`emit_flow._build_step` 的分流链只认其中四个，
# 未知值会被静默丢弃（见 _build_step 里的取值域闸注释）。两份清单由
# `tests/test_body_kind_gate.py` 锁步——**新增 kind 必须同时改 model 文档与这里**，
# 否则闸门会把它当成未知值拒绝（这是有意的：宁可生成期红，不可产物少一段请求体）。
BODY_KINDS = frozenset({"json", "form", "text", "unsupported", "none"})


_SAVE_AS_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _save_as_name(index: int, ep: Endpoint, template: str = "resp_{n}_{slug}") -> str:
    """save_as 全局唯一（compose_for_goal 同键多生产者硬报错，§1.5）。

    `{slug}` 取**标识符形态**（`identifier_slug`）：`save_as` 是 ctx 键、也是 flow 级断言
    左操作数（`<save_as>.ok == true`）。旧实现用 `slugify` 保留 `-`，于是
    `resp_1_get-web-example-order.ok == true` 这种断言在导出器里被 `_FLOW_OK_RE`（`\\w+`）
    认不出，**降级成 TODO**——Atlas 自己发的断言，自己的另一半读不懂（见
    `tests/test_generated_assert_roundtrip.py`）。解析侧同时兼容历史 hyphen 键，两端都不丢。

    `--naming` 模板是**用户输入**：坏模板（未知占位符 `{oops}` / 位置参数 `{0}` / 产出非标识符）
    旧实现直接 `KeyError`/`IndexError` 裸 traceback + rc 1——「输入错误」被漂成「程序崩了」
    （#103 同族）。此处点名成 `FlowEmitError`（ValueError 家族 → CLI 一行 stderr + rc 2）。
    """
    slug = identifier_slug(ep.summary or ep.path, fallback="op")
    try:
        name = template.format(n=index, slug=slug)
    except (KeyError, IndexError, ValueError) as exc:
        raise FlowEmitError(
            f"--naming 模板无法套用：{template!r}（{type(exc).__name__}: {exc}）——"
            f"只支持 {{n}}（步骤序号）与 {{slug}}（摘要/路径归一）两个占位符，"
            f"例如 resp_{{n}}_{{slug}}") from exc
    if not _SAVE_AS_IDENT.match(name):
        raise FlowEmitError(
            f"--naming 生成的数据落点不是合法标识符：{name!r}（模板 {template!r}）——"
            f"save_as 既是 ctx 键、也是 flow 级断言左操作数（<save_as>.ok == true），"
            f"只许字母/数字/下划线且不以数字开头；示例 resp_{{n}}_{{slug}}")
    return name


def _resolve_plugin_domain(plugin: str) -> str:
    """ob-3：step.domain 的唯一取值口——`--plugin X` 生成期把 X 的**插件注册 domain** 接进
    step（平台专有坑点对自动生成 flow 才能 match：domain 是 pitfalls 的 scope 维度，
    写死 demo = 换平台后平台专有坑点永不命中，假绿）。

    判据：显式参数按 `plugin_api.register_plugin` 的注册表解析（demo 默认恒可用）；
    未注册名允许**动态加载面**接住（`plugin_api._load_external_plugin` 同款路径——
    ATLAS_PLUGIN 已支持模块/文件形态的外部插件），全部失败点名已注册清单（#90 口径），
    绝不静默回落 demo（回落=把 A 平台的坑点知识标成 demo 的，比报错更隐蔽）。
    生成期解析成功后回读 `resolve_plugin().name` 断言与显式参数一致——插件类名/内部
    name 与注册 domain 不一致时点名（生成产物进真实执行链前把身份对齐）。
    """
    import os as _os
    from ..plugin_api import resolve_plugin
    if not isinstance(plugin, str) or not plugin.strip():
        raise FlowEmitError(
            f"--plugin 必须是非空插件名，收到 {plugin!r}（已注册面见 atlas plugin_api）")
    plugin = plugin.strip()
    previous = _os.environ.get("ATLAS_PLUGIN")
    try:
        _os.environ["ATLAS_PLUGIN"] = plugin
        instance = resolve_plugin()
    except ValueError as exc:
        raise FlowEmitError(str(exc)) from exc
    finally:
        if previous is None:
            _os.environ.pop("ATLAS_PLUGIN", None)
        else:
            _os.environ["ATLAS_PLUGIN"] = previous
    name = getattr(instance, "name", "")
    if name != plugin:
        raise FlowEmitError(
            f"插件 {plugin!r} 的注册名与实例 name 不一致（实例 name={name!r}）——"
            f"step.domain 会进坑点匹配 scope，身份必须逐字对齐")
    return name


def _build_step(index: int, ep: Endpoint, global_headers: dict,
                template: str, read_only_default: bool,
                domain: str = "demo") -> tuple[dict, list[str]]:
    """构造单个 api_call step + manual_assertions（列表下标断言限制）。

    domain 缺省 demo 与旧实现逐字等价（#115：直接调用方零涟漪）；emit_flow 入口
    经 `_resolve_plugin_domain` 解析后传入（ob-3）。
    """
    manual: list[str] = []
    save_as = ep.save_as_override or _save_as_name(index, ep, template)
    # path + query 拼进 path（api_call 无 query 旋钮）；body 参数合并进 body 对象
    path_params = [p for p in ep.params if p.where in ("path", "query")]
    path = collect_path_query(ep.path, path_params)
    step: dict[str, Any] = {
        "task": "api_call",
        "path": path,
        "method": ep.method,
        "save_as": save_as,
        "domain": domain,
        "intent": ep.summary or f"调用 {ep.method} {ep.path}",
    }
    # 全局鉴权头并入每个 step（BUG-920 防线，isc-app 业务识别头）
    headers = dict(global_headers)
    # 步骤级 header 参数并入（覆盖同名全局头）
    for p in ep.params:
        if p.where == "header":
            headers[p.name] = str(p.example) if p.example is not None else ""
    if headers:
        step["headers"] = headers
    # body 分流
    body = ep.body
    body_params = {p.name: p.example for p in ep.params if p.where == "body"}
    # 取值域闸（2026-11-17 结构审计）：`BodySpec.kind` 的文档取值域是 5 个
    # （model.py:32-36：json / form / text / unsupported / none），但下面这条链只认前四个。
    # 实测：塞一个未知 kind（如 "bogus_kind"）→ **既不产 body、也不报错、也不进 manual**
    # ——请求体凭空消失，生成的 flow 看起来完全正常（同一份文档 `atlas gen` 出包 rc 0）。
    # 这是「静默丢弃」形态的又一例（同族：`AssertSpec` 未知 kind、compat 腿的 flow assert、
    # 影子层遮蔽）。故在**分流之前**点名拒绝，而不是在链尾补 else 猜个默认行为。
    #
    # `none` 是合法值：语义就是「无请求体」，不产 body 键是对的，故显式放行。
    # 判据从 `model.BodySpec` 的 docstring 取值域抄一次不算漂移——它由
    # `tests/test_body_kind_gate.py` 与 model 的文档逐项锁步（新增 kind 必须同时改两处）。
    if body.kind not in BODY_KINDS:
        raise FlowEmitError(
            f"{ep.method} {ep.path}: 请求体 kind={body.kind!r} 不在取值域 "
            f"{sorted(BODY_KINDS)}——旧实现会**静默丢弃整个请求体**（不产 body、不报错、"
            f"不进 manual_assertions），生成出来的 flow 看起来完整但少了一段。"
            f"请在 model.BodySpec 与 emit_flow 的分流链里同时落地新 kind，或改用既有值。")
    if body.kind == "json":
        if body.example_obj is None:
            obj = dict(body_params)  # 空映射 + 参数表字段
            step["body"] = obj  # 报告标 low_confidence
        else:
            obj = dict(body.example_obj) if isinstance(body.example_obj, dict) else body.example_obj
            # 参数表字段合并进 body（参数表是文档明示的字段，非猜测）
            if isinstance(obj, dict):
                for k, v in body_params.items():
                    obj.setdefault(k, v)
            step["body"] = body_to_yaml_value(obj)
    elif body.kind == "form":
        step["body"] = form_pairs_to_string(body.pairs)  # str → data=
    elif body.kind == "text":
        step["body"] = body.text  # str → data=
    elif body.kind == "unsupported":
        manual.append(f"{save_as} body: multipart/文件上传需人工处理，未生成 body")
    # 读/写场景
    if ep.is_write:
        step["read_only"] = False
        step["on_env_blocked"] = "stop"
    else:
        step["read_only"] = read_only_default
        step["on_env_blocked"] = "skip"
    # 断言（§4.3）：表达式优先；列表下标不伪造，列 manual_assertions
    asserts: list[Any] = []
    asserts.append(f"{save_as}.ok == true")  # 存活断言
    resp = ep.response
    if resp.is_object and resp.has_result_code:
        # 业务码断言：文档给了具体期望值才生成；否则只生成存在性由契约兜底
        sample = resp.sample if isinstance(resp.sample, dict) else {}
        rc = sample.get("resultCode")
        if rc is not None:
            asserts.append(f'{save_as}.data.resultCode == "{rc}"')
    # 列表型响应：DSL _lookup 不支持数字下标，列 manual_assertions，不伪造
    if resp.is_array:
        manual.append(
            f"{save_as}.data.0.*：响应为数组，DSL 插值不支持数字下标，"
            f"列表项断言请用契约 Contract.required（items.0.x）或 assert_api")
    step["id"] = f"step_{index}"  # 给人看的标识（不传给 Task）
    return step, manual


def _build_requires(endpoints: list[Endpoint], scenario: str) -> dict:
    """requires 块：把文档里出现过的业务实体转成数据条件（只 find_only）。

    场景 A/B/C 默认 find_only（零副作用，红线 6/FAQ）；场景 D 前置主数据也 find_only。
    本流程自身写操作不转探针（写是被测行为，不是供数）。
    """
    requires: dict[str, Any] = {}
    # 从 endpoint summary/path 提取实体名（保守：只取 slug 化的前缀）
    entities: list[str] = []
    for ep in endpoints:
        # 提取 summary 里可能的实体词（如 "销售订单" → SalesOrder 占位）
        text = ep.summary or ""
        # 简单启发：取中文实体或路径首段
        entity = _guess_entity(text, ep.path)
        if entity and entity not in entities:
            entities.append(entity)
    for entity in entities[:2]:  # 最多两个前置实体，避免过度生成
        requires[entity] = {
            "entity": entity,
            "where": {},
            "strategy": "find_only",
            "as": entity,
            "on_env_blocked": "skip" if scenario != "d" else "stop",
        }
    return requires


def _guess_entity(summary: str, path: str) -> str:
    """从 summary/path 保守推断实体名（不猜值，只给占位名）。

    跳过泛化前缀（api/web/v1/http），只在路径含明确实体段或 summary 含
    可识别英文实体词时返回名；否则返回 ""（不伪造 requires）。
    """
    GENERIC = {"api", "web", "v1", "v2", "http", "rest", "service"}
    m = path.strip("/").split("/")
    for seg in m:
        seg = seg.split("${")[0]
        if seg and seg.lower() not in GENERIC and re.search(r"[A-Za-z]", seg):
            return f"Entity_{slugify(seg, fallback='Entity')}"
    # summary 里的英文实体词
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]+", summary or ""):
        if word.lower() not in GENERIC and len(word) > 2:
            return f"Entity_{slugify(word, fallback='Entity')}"
    return ""


def emit_flow(endpoints: list[Endpoint], *, scenario: str = "auto",
              plugin: str = "demo", flow_id: str = "",
              global_headers: dict | None = None,
              naming: str = "resp_{n}_{slug}",
              division: str = "") -> tuple[dict, list[str], list[dict]]:
    """生成 flow dict + manual_assertions + low_confidence。

    返回 (flow_dict, manual_assertions, low_confidence_items)。
    场景 B 多接口链路：单 flow 多 step，save_as 唯一、插值串联。
    场景 C 批量：调用方按 endpoint 拆成多个 flow（本函数只处理传入的 endpoints）。
    """
    global_headers = global_headers or {}
    # ob-3：domain 与插件身份对齐（解析一次给全部 step 用；坏插件名在生成期点名，
    # 不让产物带着假 domain 进真实执行链）。默认 plugin="demo" 路径逐字节不变。
    domain = _resolve_plugin_domain(plugin)
    # 场景判定
    if scenario == "auto":
        scenario = _detect_scenario(endpoints)
    flow_id = flow_id or slugify(
        endpoints[0].summary or endpoints[0].path if endpoints else "gen",
        fallback="gen")
    steps: list[dict] = []
    all_manual: list[str] = []
    all_low: list[dict] = []
    # save_as 唯一性冲突检测（§1.5）
    seen_save_as: set[str] = set()
    for index, ep in enumerate(endpoints, 1):
        save_as = ep.save_as_override or _save_as_name(index, ep, naming)
        if save_as in seen_save_as:
            raise FlowEmitError(
                f"save_as 冲突：{save_as} 在 {index} 步重复——"
                f"compose_for_goal 同键多生产者会硬报错，必须全局唯一")
        seen_save_as.add(save_as)
        step, manual = _build_step(index, ep, global_headers, naming,
                                   read_only_default=True, domain=domain)
        steps.append(step)
        all_manual.extend(manual)
        all_low.extend(ep.low_confidence())
    flow = {"id": flow_id, "plugin": plugin, "steps": steps}
    # C4① 字面敏感头闸（design_c4 §2.3，判据唯一实现在 credential_gate，不在这里复制名单）：
    # 文档/头文件里手写的字面凭据（Authorization/Cookie/…-sessionid + 字面 UUID/JWT）
    # 换成 ${ATLAS_HDR_<n>} 占位——生成的 YAML 是要人审、可能进 git/工单系统的产物，
    # 照抄字面鉴权值等于把凭据泄漏面搬进新仓库。占位由 DSL 既有 `${...}` 插值承接，
    # checklist 的未解析占位符告警照常点名（人审一步不缺）。
    gate_flow_headers(flow)
    # requires 块（场景 B/D 优先）
    if scenario in ("b", "d"):
        requires = _build_requires(endpoints, scenario)
        if requires:
            flow["requires"] = requires
    # flow 级 on_env_blocked
    flow["on_env_blocked"] = "stop" if scenario == "d" else "skip"
    # assert 块
    if steps:
        save_as_0 = steps[0].get("save_as", "response")
        asserts = [f"{save_as_0}.ok == true"]
        flow["assert"] = asserts
    return flow, all_manual, all_low


def _detect_scenario(endpoints: list[Endpoint]) -> str:
    """auto 场景判定：单接口 A；多接口链路 B；批量 C；写操作 D。"""
    if any(ep.scenario_override for ep in endpoints):
        return endpoints[0].scenario_override
    if any(ep.is_write for ep in endpoints):
        return "d"
    if len(endpoints) == 1:
        return "a"
    return "b"

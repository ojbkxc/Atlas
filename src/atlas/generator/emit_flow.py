"""Endpoint → flow dict（DSL）映射：api_call step、body 分流、头合并、断言。"""
from __future__ import annotations
import re
from typing import Any

from .model import Endpoint, CONFIDENCE_HIGH
from .dsl_utils import (slugify, collect_path_query, form_pairs_to_string,
                        body_to_yaml_value)


class FlowEmitError(ValueError):
    """生成 flow 时的结构问题（如 save_as 冲突，--strict 下 rc 3）。"""


def _save_as_name(index: int, ep: Endpoint, template: str = "resp_{n}_{slug}") -> str:
    """save_as 全局唯一（compose_for_goal 同键多生产者硬报错，§1.5）。"""
    slug = slugify(ep.summary or ep.path, fallback="op")
    return template.format(n=index, slug=slug)


def _build_step(index: int, ep: Endpoint, global_headers: dict,
                template: str, read_only_default: bool) -> tuple[dict, list[str]]:
    """构造单个 api_call step + manual_assertions（列表下标断言限制）。"""
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
        "domain": "demo",
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
                                   read_only_default=True)
        steps.append(step)
        all_manual.extend(manual)
        all_low.extend(ep.low_confidence())
    flow = {"id": flow_id, "plugin": plugin, "steps": steps}
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

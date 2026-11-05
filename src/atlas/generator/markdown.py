"""Markdown 接口说明解析：标题切块 + http curl 栅栏 + 参数表 + 响应 JSON。"""
from __future__ import annotations
import json
import re
from typing import Any

from .model import (Endpoint, Param, BodySpec, ResponseSpec,
                    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)

_METHOD_PATH = re.compile(r"(?i)\b(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)")
_CURL = re.compile(r"curl\s+(-X\s+)?(GET|POST|PUT|PATCH|DELETE)?\s*"
                   r"(?:'([^']+)'|\"([^\"]+)\"|(\S+))", re.IGNORECASE)
_HEADER_LINE = re.compile(r"-H\s+'([^:]+):\s*([^']+)'|-H\s+\"([^:]+):\s*([^\"]+)\"",
                          re.IGNORECASE)
_TITLE_METHOD = re.compile(r"(?i)^#{1,6}\s+(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)")
_JSON_FENCE = re.compile(r"```(json|http|bash|shell)?\s*\n(.*?)```", re.DOTALL)


def _extract_method_path(block: str) -> tuple[str, str] | None:
    """从代码块/标题里取 METHOD + path。"""
    m = _TITLE_METHOD.search(block)
    if m:
        return m.group(1).upper(), m.group(2)
    m = _METHOD_PATH.search(block)
    if m:
        return m.group(1).upper(), m.group(2)
    # curl 形态
    curl = _CURL.search(block)
    if curl:
        method = (curl.group(2) or "GET").upper()
        url = curl.group(3) or curl.group(4) or curl.group(5) or ""
        # 去掉 scheme+host，只留 path+query
        path = re.sub(r"^https?://[^/]+", "", url)
        return method, path
    return None


def _extract_curl_headers(block: str) -> list[tuple[str, str]]:
    out = []
    for m in _HEADER_LINE.finditer(block):
        if m.group(1):
            out.append((m.group(1).strip(), m.group(2).strip()))
        else:
            out.append((m.group(3).strip(), m.group(4).strip()))
    return out


def _extract_json_blocks(block: str) -> list[Any]:
    """提取块内所有可解析的 JSON 值（对象/数组）。"""
    out = []
    for m in _JSON_FENCE.finditer(block):
        body = m.group(2).strip()
        try:
            out.append(json.loads(body))
        except json.JSONDecodeError:
            pass
    # 兜底：块内第一个成对 {...} 或 [...]
    if not out:
        for opener, closer in (("{", "}"), ("[", "]")):
            start = block.find(opener)
            if start < 0:
                continue
            end = _match_close(block, start, opener, closer)
            if end > start:
                try:
                    out.append(json.loads(block[start:end + 1]))
                except json.JSONDecodeError:
                    pass
    return out


def _extract_json_with_context(block: str) -> list[tuple[Any, str]]:
    """提取 JSON 块并返回 (sample, preceding_line) 用于请求/响应分类。"""
    out: list[tuple[Any, str]] = []
    for m in _JSON_FENCE.finditer(block):
        body = m.group(2).strip()
        try:
            sample = json.loads(body)
        except json.JSONDecodeError:
            continue
        # preceding non-empty line before the fence
        pre = block[:m.start()].rstrip().splitlines()
        preceding = pre[-1].strip() if pre else ""
        out.append((sample, preceding))
    return out


def _match_close(text: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _parse_param_table(lines: list[str]) -> list[Param]:
    """解析 Markdown 表格 | 参数 | 类型 | 必填 | 说明 |。"""
    params: list[Param] = []
    header_seen = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header_seen:
            # 表头行：判断是否像参数表
            if any("参数" in c or "名称" in c or "name" in c.lower() for c in cells):
                header_seen = True
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue  # 分隔行
        if len(cells) < 3:
            continue
        name = cells[0]
        required = "是" in cells[2] or "required" in cells[2].lower()
        if name and not name.startswith("-"):
            params.append(Param(where="query", name=name, required=required,
                                confidence=CONFIDENCE_MEDIUM))
    return params


def _parse_response_json(samples: list[Any]) -> ResponseSpec:
    """从块内 JSON 推断响应规格。"""
    spec = ResponseSpec()
    for sample in samples:
        if isinstance(sample, dict):
            spec.is_object = True
            spec.sample = sample
            spec.has_result_code = "resultCode" in sample
            # 一层 required 类型
            for k, v in sample.items():
                if isinstance(v, dict):
                    spec.required_paths[k] = "object"
                elif isinstance(v, list):
                    spec.required_paths[k] = "array"
                elif isinstance(v, bool):
                    spec.required_paths[k] = "boolean"
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    spec.required_paths[k] = "number"
                elif isinstance(v, str):
                    spec.required_paths[k] = "string"
                break  # 只取第一个对象样例的一层
        elif isinstance(sample, list):
            spec.is_array = True
            spec.sample = sample
    return spec


def parse_markdown(text: str) -> list[Endpoint]:
    """解析 Markdown 接口文档 → Endpoint 列表。"""
    # 按标题切块
    blocks = re.split(r"\n(?=#{1,6}\s)", text)
    endpoints: list[Endpoint] = []
    for block in blocks:
        mp = _extract_method_path(block)
        if not mp:
            continue
        method, path = mp
        ep = Endpoint(method=method, path=path, summary=_title_summary(block))
        # 头
        headers = _extract_curl_headers(block)
        if headers:
            ep.params.extend(Param(where="header", name=k, example=v,
                                     confidence=CONFIDENCE_HIGH) for k, v in headers)
        # 参数表
        table_params = _parse_param_table(block.splitlines())
        # JSON 块：请求体 or 响应体（按每个栅栏前的上下文行分类，不整块判定）
        req_json = None
        resp_spec = ResponseSpec()
        for sample, preceding in _extract_json_with_context(block):
            low = (preceding or "").lower()
            if any(k in preceding for k in ("响应", "返回", "response")):
                resp_spec = _parse_response_json([sample]) or resp_spec
            elif any(k in preceding for k in ("请求", "参数", "request", "body")):
                req_json = sample if req_json is None else req_json
            else:
                # 无上下文：第一个对象当请求，数组当响应
                if isinstance(sample, list):
                    resp_spec = _parse_response_json([sample])
                elif req_json is None:
                    req_json = sample
        if req_json is not None:
            ep.body = BodySpec(kind="json", example_obj=req_json,
                               confidence=CONFIDENCE_HIGH)
            # 有 JSON 请求体时，参数表条目是 body 字段（合并进 body），不是 query
            for p in table_params:
                p.where = "body"
        else:
            if table_params:
                # 无 JSON 样例但有参数表 → form 字符串（→ data=）
                pairs = [(p.name, str(p.example or "")) for p in table_params]
                ep.body = BodySpec(kind="form", pairs=pairs, confidence=CONFIDENCE_MEDIUM)
            ep.params.extend(table_params)
        if resp_spec.sample is not None or resp_spec.is_object or resp_spec.is_array:
            ep.response = resp_spec
        # 无样例 → 低置信度
        if ep.body.kind == "none" and not ep.response.sample:
            ep.confidence = CONFIDENCE_LOW
            ep.low_confidence_notes.append("Markdown 块无请求/响应样例，字段需人工确认")
        endpoints.append(ep)
    return endpoints


def _title_summary(block: str) -> str:
    m = re.search(r"^#{1,6}\s+(.+)", block)
    if m:
        # 去掉标题里的 METHOD path
        title = _TITLE_METHOD.sub("", m.group(1)).strip()
        return title or m.group(1)
    return ""

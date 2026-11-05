"""纯文本接口说明解析：METHOD+path 正则 + 参数行 + 样例 JSON。

面向「群里发的一段接口说明 / Excel 导出成 txt」。每个字段带置信度标注，
低置信度项单列报告，绝不把猜测冒充确定。
"""
from __future__ import annotations
import json
import re
from typing import Any

from .model import (Endpoint, Param, BodySpec, ResponseSpec,
                    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)
from .markdown import _extract_json_blocks, _match_close

_METHOD_PATH = re.compile(r"(?i)\b(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)")
_PARAM_LINE = re.compile(
    r"^\s*[-*]?\s*([A-Za-z_]\w*)\s*[:：]\s*(\S+)?\s*(必填|required)?\s*$",
    re.IGNORECASE)


def parse_plaintext(text: str) -> list[Endpoint]:
    """解析纯文本接口说明 → Endpoint 列表（置信度标 low）。"""
    endpoints: list[Endpoint] = []
    # 按端点行切块
    chunks: list[tuple[str, str]] = []
    for m in _METHOD_PATH.finditer(text):
        chunks.append((m.group(1).upper(), m.group(2)))
    for method, path in chunks:
        start = text.find(path)
        # 块延伸到下一个端点行或文末
        next_match = _METHOD_PATH.search(text, start + len(path))
        end = next_match.start() if next_match else len(text)
        block = text[start:end]
        ep = Endpoint(method=method, path=path,
                      summary=f"纯文本端点 {method} {path}")
        # 参数行
        for line in block.splitlines():
            pm = _PARAM_LINE.match(line)
            if pm:
                name = pm.group(1)
                value = pm.group(2) or ""
                required = bool(pm.group(3))
                ep.params.append(Param(where="query", name=name, required=required,
                                       example=value, confidence=CONFIDENCE_LOW))
        # 样例 JSON（请求或响应由相邻关键词决定）
        samples = _extract_json_blocks(block)
        req_json = None
        resp_spec = ResponseSpec()
        for sample in samples:
            if "响应" in block or "返回" in block or "response" in block.lower():
                resp_spec = _parse_resp(sample)
            else:
                req_json = sample if req_json is None else req_json
        if req_json is not None:
            ep.body = BodySpec(kind="json", example_obj=req_json,
                               confidence=CONFIDENCE_LOW)
        if resp_spec.sample is not None:
            ep.response = resp_spec
        # 纯文本一律标低置信度
        ep.confidence = CONFIDENCE_LOW
        if not samples:
            ep.low_confidence_notes.append("纯文本无样例 JSON，字段需人工确认")
        endpoints.append(ep)
    return endpoints


def _parse_resp(sample: Any) -> ResponseSpec:
    spec = ResponseSpec(confidence=CONFIDENCE_LOW)
    if isinstance(sample, dict):
        spec.is_object = True
        spec.sample = sample
        spec.has_result_code = "resultCode" in sample
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
            break
    elif isinstance(sample, list):
        spec.is_array = True
        spec.sample = sample
    return spec

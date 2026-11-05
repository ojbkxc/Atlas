"""OpenAPI 3 / Swagger 2 解析：$ref 解引用 + 循环检测，归一到 Endpoint 模型。"""
from __future__ import annotations
import copy
from typing import Any

from .model import (Endpoint, Param, BodySpec, ResponseSpec,
                    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)


class OpenAPIParseError(ValueError):
    """文档结构无法解析（用户输入问题，CLI 接 rc 2）。"""


def _deref(node: Any, root: dict, seen: set[str]) -> Any:
    """本地 $ref 解引用；循环引用命中即截断到 {} 并返回哨兵标记。

    支持 #/components/schemas/X（OAS3）与 #/definitions/X（Swagger2）。
    不做远端 URL $ref（超出离线范围，报告点名）。
    """
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            # 远端/未知 $ref：保留原样（报告点名人工处理），不伪造解引用
            return {"__unresolved_ref__": ref}
        if ref in seen:
            return {"__cyclic__": ref}
        seen = seen | {ref}
        target = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                return {"__missing_ref__": ref}
            target = target[part]
        return _deref(target, root, seen)
    return {key: _deref(v, root, seen) for key, v in node.items()}


def _deref_schema(schema: dict, root: dict) -> dict:
    """对 schema 做完整 $ref 解引用（独立 seen，避免跨字段误判循环）。"""
    return _deref(schema, root, set()) or {}


def _make_example(schema: dict, root: dict, depth: int = 0) -> Any:
    """从 schema 递归造示例值（不猜真实业务数据，示例值标注需替换）。

    造值纪律（§3.4）：只造结构合法的示例（string→"string"、number→0、
    boolean→false、array→[item]、object→{field:example}），绝不填真实业务值。
    oneOf/anyOf/循环 → 返回 None（由调用方标 low_confidence）。
    """
    if depth > 6:
        return None
    if not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        schema = _deref_schema(schema, root)
    if "__cyclic__" in schema or "__missing_ref__" in schema:
        return None
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    # Swagger2 的 enum 取首项作示例
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    typ = schema.get("type")
    if typ == "string":
        fmt = schema.get("format", "")
        if fmt == "date":
            return "2024-01-01"
        return "string"
    if typ == "number" or typ == "integer":
        return 0
    if typ == "boolean":
        return False
    if typ == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            item = _make_example(items, root, depth + 1)
            return [item] if item is not None else []
        return []
    if typ == "object" or "properties" in schema:
        props = schema.get("properties", {})
        out = {}
        for name, sub in props.items():
            val = _make_example(sub, root, depth + 1)
            out[name] = val
        return out
    if "oneOf" in schema or "anyOf" in schema:
        choices = schema.get("oneOf") or schema.get("anyOf")
        if choices:
            return _make_example(choices[0], root, depth + 1)
        return None
    return None


def _schema_required_paths(schema: dict, root: dict) -> dict[str, str]:
    """core 档 required 字段类型映射（§4.4）。取 schema.required 数组逐项映射。"""
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        schema = _deref_schema(schema, root)
    if "__cyclic__" in schema or "__missing_ref__" in schema:
        return {}
    type_map = {"object": "object", "array": "array", "string": "string",
                "number": "number", "integer": "number", "boolean": "boolean"}
    required = schema.get("required", [])
    props = schema.get("properties", {})
    paths: dict[str, str] = {}
    for name in required:
        if not isinstance(name, str):
            continue
        sub = props.get(name, {})
        if isinstance(sub, dict):
            typ = sub.get("type", "any")
            paths[name] = type_map.get(typ, "any")
        else:
            paths[name] = "any"
    return paths


def _parse_response(code: str, op: dict, root: dict) -> ResponseSpec:
    """解析 responses 里的一个响应码 → ResponseSpec。"""
    spec = ResponseSpec()
    if code != "200" and not code.startswith("2"):
        # 非 2xx 默认不作为契约来源（业务成功响应在 2xx）
        return spec
    content = (op.get("responses", {}).get(code, {}) or {}).get("content", {})
    if not content:
        # Swagger2：直接 schema（无 content 层）
        old = (op.get("responses", {}).get(code, {}) or {}).get("schema")
        if isinstance(old, dict):
            schema = _deref_schema(old, root)
            sample = _make_example(schema, root)
            spec.is_object = schema.get("type") == "object" or "properties" in schema
            spec.is_array = schema.get("type") == "array"
            spec.sample = sample
            spec.required_paths = _schema_required_paths(schema, root)
            spec.has_result_code = bool(sample and isinstance(sample, dict)
                                        and "resultCode" in sample)
        return spec
    # 取第一个 media type 的 schema
    for media, media_obj in content.items():
        if not isinstance(media_obj, dict):
            continue
        schema = media_obj.get("schema")
        if not isinstance(schema, dict):
            continue
        schema = _deref_schema(schema, root)
        sample = _make_example(schema, root)
        spec.is_object = schema.get("type") == "object" or "properties" in schema
        spec.is_array = schema.get("type") == "array"
        spec.sample = sample
        spec.required_paths = _schema_required_paths(schema, root)
        spec.has_result_code = bool(sample and isinstance(sample, dict)
                                    and "resultCode" in sample)
        break
    return spec


def _parse_params(params: list, root: dict) -> list[Param]:
    """解析 parameters 列表（path/query/header）。"""
    out: list[Param] = []
    for raw in params or []:
        if not isinstance(raw, dict):
            continue
        where = raw.get("in", "query")
        name = raw.get("name", "")
        if not name:
            continue
        schema = raw.get("schema") if isinstance(raw.get("schema"), dict) else raw
        schema = _deref_schema(schema, root) if schema else {}
        example = raw.get("example")
        if example is None and isinstance(schema, dict):
            example = _make_example(schema, root)
        out.append(Param(where=where, name=name, required=bool(raw.get("required")),
                         schema=schema, example=example,
                         confidence=CONFIDENCE_HIGH))
    return out


def _parse_body(op: dict, root: dict) -> BodySpec:
    """解析 requestBody → BodySpec。"""
    rb = op.get("requestBody")
    if not isinstance(rb, dict):
        return BodySpec(kind="none")
    rb = _deref(rb, root, set())
    content = rb.get("content", {})
    if not isinstance(content, dict) or not content:
        return BodySpec(kind="none")
    for media, media_obj in content.items():
        if not isinstance(media_obj, dict):
            continue
        if media == "application/json":
            schema = media_obj.get("schema")
            example = media_obj.get("example")
            if example is None and isinstance(schema, dict):
                example = _make_example(schema, root)
            return BodySpec(kind="json", example_obj=example, schema=schema,
                            confidence=CONFIDENCE_HIGH if example is not None else CONFIDENCE_LOW)
        if media == "application/x-www-form-urlencoded":
            pairs: list[tuple[str, str]] = []
            props = (media_obj.get("schema") or {}).get("properties", {})
            if isinstance(props, dict):
                for name, sub in props.items():
                    val = sub.get("example", "") if isinstance(sub, dict) else ""
                    pairs.append((name, str(val)))
            return BodySpec(kind="form", pairs=pairs, confidence=CONFIDENCE_MEDIUM)
        if media == "text/plain":
            return BodySpec(kind="text", text="", confidence=CONFIDENCE_LOW)
        if media == "multipart/form-data":
            return BodySpec(kind="unsupported", confidence=CONFIDENCE_HIGH)
    return BodySpec(kind="none")


def parse_openapi(doc: dict, *, source_label: str = "openapi") -> list[Endpoint]:
    """解析 OpenAPI3/Swagger2 文档 → Endpoint 列表。"""
    if not isinstance(doc, dict):
        raise OpenAPIParseError(f"{source_label}：文档顶层必须是映射")
    # Swagger2 的 swagger: "2.0"；OAS3 的 openapi: "3.x"
    paths = doc.get("paths", {})
    if not isinstance(paths, dict):
        raise OpenAPIParseError(f"{source_label}：paths 必须是映射")
    base = ""
    if isinstance(doc.get("servers"), list) and doc["servers"]:
        base = (doc["servers"][0].get("url") or "") if isinstance(doc["servers"][0], dict) else ""
    if not base:
        base = doc.get("basePath", "") or ""

    endpoints: list[Endpoint] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_item = _deref(path_item, doc, set())
        # Swagger2 参数可挂在 path 级（被 method 继承）
        path_params = path_item.get("parameters", [])
        for method in ("get", "post", "put", "patch", "delete"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            op = _deref(op, doc, set())
            params = _parse_params(path_params + op.get("parameters", []), doc)
            body = _parse_body(op, doc)
            # Swagger2 formData：参数在 parameters 里（in=formData），不在 requestBody
            if body.kind == "none":
                form_params = [p for p in params if p.where == "formData"]
                if form_params:
                    pairs = [(p.name, str(p.example or "")) for p in form_params]
                    body = BodySpec(kind="form", pairs=pairs, confidence=CONFIDENCE_MEDIUM)
                    # 把这些参数从 params 里移除（它们已并入 body）
                    params = [p for p in params if p.where != "formData"]
            response = _parse_response("200", op, doc)
            ep = Endpoint(
                method=method.upper(), path=path,
                summary=op.get("summary", "") or op.get("operationId", ""),
                params=params, body=body, response=response, base=base,
            )
            # x-atlas-* 扩展标注
            x = op.get("x-atlas-level") or path_item.get("x-atlas-level") or ""
            ep.level_override = str(x)
            xs = op.get("x-atlas-scenario") or path_item.get("x-atlas-scenario") or ""
            ep.scenario_override = str(xs)
            ep.save_as_override = str(op.get("x-atlas-save-as") or "")
            ep.flow_override = str(op.get("x-atlas-flow") or "")
            # $ref 未解/循环 → 低置信度标注
            if body.kind == "json" and body.example_obj is None:
                ep.low_confidence_notes.append("请求体 $ref 循环或缺样例，已生成空映射")
                ep.confidence = CONFIDENCE_LOW
            if any("__unresolved_ref__" in str(p.schema) for p in params):
                ep.low_confidence_notes.append("参数含未解引用 $ref")
                ep.confidence = CONFIDENCE_LOW
            endpoints.append(ep)
    return endpoints

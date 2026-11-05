"""来源嗅探与调度：按内容判定文档格式，归一到 Endpoint 列表。

纪律（照搬 import_jmeter / #131）：UTF-8 严格读 → 失败回落 errors="replace"。
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json
import re

import yaml

from .model import Endpoint
from .openapi import parse_openapi, OpenAPIParseError
from .markdown import parse_markdown
from .plaintext import parse_plaintext


class SourceError(ValueError):
    """文档无法识别/解析（用户输入问题，CLI 接 rc 2）。"""


def read_text(path: Path) -> str:
    """读文档文本：UTF-8 严格 → 失败回落 errors="replace"（#131 纪律）。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def sniff_source_type(text: str, declared: str = "auto") -> str:
    """按内容判定来源类型（不只看扩展名）。"""
    if declared != "auto":
        return declared
    # 先尝试 YAML/JSON 解析（yaml.safe_dump 默认按键排序，openapi 不一定在开头）
    try:
        doc = yaml.safe_load(text)
        if isinstance(doc, dict):
            if "openapi" in doc:
                return "openapi"
            if str(doc.get("swagger", "")).startswith("2"):
                return "swagger"
    except (yaml.YAMLError, json.JSONDecodeError):
        pass
    if re.search(r"^#{1,6}\s", text, re.MULTILINE) or "```" in text:
        return "markdown"
    if re_search_method(text):
        return "text"
    return "unknown"


def re_search_method(text: str) -> bool:
    import re
    return bool(re.search(r"(?i)\b(GET|POST|PUT|PATCH|DELETE)\s+/\S+", text))


def parse_source(path: Path, declared: str = "auto") -> tuple[list[Endpoint], str]:
    """读文档 + 嗅探 + 解析 → (endpoints, source_type)。"""
    if not path.exists():
        raise SourceError(f"文档不存在：{path}")
    text = read_text(path)
    if not text.strip():
        raise SourceError(f"文档为空：{path}")
    stype = sniff_source_type(text, declared)
    if stype == "unknown":
        raise SourceError(
            f"无法识别文档格式：{path}。请用 --source-type 指定 "
            "openapi/swagger/markdown/text")
    try:
        if stype in ("openapi", "swagger"):
            doc = yaml.safe_load(text)
            endpoints = parse_openapi(doc, source_label=stype)
        elif stype == "markdown":
            endpoints = parse_markdown(text)
        elif stype == "text":
            endpoints = parse_plaintext(text)
        else:
            raise SourceError(f"未知 source-type：{stype}")
    except OpenAPIParseError as exc:
        raise SourceError(str(exc)) from exc
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise SourceError(f"文档解析失败：{type(exc).__name__}: {exc}") from exc
    if not endpoints:
        raise SourceError(f"文档 {path.name} 未解析出任何端点")
    return endpoints, stype

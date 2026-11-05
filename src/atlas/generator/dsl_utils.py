"""DSL 生成共享纯函数：slug、占位符扫描、body 分流判定。

与 scripts/import_jmeter.py 共享的纪律（照搬）：
- 占位符 `${...}` 递归下钻 path/body/headers，逐条告警，保留原样待人审接 env
- body 分流：dict/list → json=；str/bytes → data= 原样（不可混淆，否则后端假 400）

本包内先独立实现；「单一真相」待后续 P3 收敛（与 Gap5 §4.2 同手法），届时让
import_jmeter.py 改为 import 这里，回归测钉死行为零变化。
"""
from __future__ import annotations
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def slugify(text: str, *, fallback: str = "flow") -> str:
    """把文档标题/路径派生成稳定 flow id（无随机、无时间）。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip()).strip("-").lower()
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned or fallback


def has_placeholder(value: Any) -> bool:
    """递归判断任意结构是否含 `${...}` 占位符。"""
    if isinstance(value, str):
        return bool(_PLACEHOLDER.search(value))
    if isinstance(value, dict):
        return any(has_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(has_placeholder(v) for v in value)
    return False


def scan_placeholders(value: Any, where: str, index: int,
                      found: list[dict]) -> None:
    """递归收集占位符命中项，写入 found（原地累积）。"""
    if isinstance(value, str):
        if _PLACEHOLDER.search(value):
            found.append({"step": index, "where": where, "value": value})
    elif isinstance(value, dict):
        for k, v in value.items():
            scan_placeholders(v, f"{where}.{k}", index, found)
    elif isinstance(value, list):
        for pos, v in enumerate(value):
            scan_placeholders(v, f"{where}[{pos}]", index, found)


def body_to_yaml_value(body: Any) -> Any:
    """把 BodySpec/dict/list/str 转成写入 flow step body 的值。

    dict/list 原样（YAML 加载即 dict/list → json=）；str/bytes 原样（→ data=）。
    这是 import_jmeter.to_atlas_dsl 的 json.loads 兜底同口径。
    """
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, str):
        return body
    return body


def form_pairs_to_string(pairs: list[tuple[str, str]]) -> str:
    """form 参数拼成 a=1&b=2 字符串（→ data= 原样）。"""
    return "&".join(f"{k}={v}" for k, v in pairs)


def collect_path_query(path: str, params: list) -> str:
    """path + query 参数拼进 path（api_call 无 query 旋钮，照搬 §4.1）。

    params 是 Param 列表（where in path/query）。path 里的 {x} 占位保留为 ${x}。
    """
    # path 占位 {id} → ${id}（DSL 插值），不猜值
    rendered = re.sub(r"\{([^}]+)\}", r"${_\1}", path)
    query = [p for p in params if p.where == "query"]
    if query:
        rendered += "?" + "&".join(
            f"{p.name}={p.example if p.example is not None else ''}" for p in query)
    return rendered

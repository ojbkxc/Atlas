"""编译自检：复用 FlowCompiler.validate_flow、占位符扫描、悬空引用检测。"""
from __future__ import annotations
from typing import Any

from .dsl_utils import scan_placeholders, has_placeholder


class ChecklistError(Exception):
    """结构自检失败（生成器缺陷，rc 3，不交付半成品）。"""


def check_flow(flow: dict, registry) -> dict[str, Any]:
    """对生成的 flow 做结构自检（validate_flow + 占位符 + 悬空引用）。

    返回 report dict；validate_flow 抛异常 → ChecklistError（rc 3）。
    """
    from ..dsl import FlowCompiler
    try:
        compiler = FlowCompiler(registry)
        tasks = compiler.validate_flow(flow)
    except Exception as exc:
        raise ChecklistError(f"flow 结构自检失败：{type(exc).__name__}: {exc}") from exc
    # 占位符扫描（递归 path/body/headers）
    placeholders: list[dict] = []
    for index, step in enumerate(flow.get("steps", []), 1):
        for key in ("path", "method"):
            scan_placeholders(step.get(key), key, index, placeholders)
        scan_placeholders(step.get("body"), "body", index, placeholders)
        for hname, hval in (step.get("headers") or {}).items():
            scan_placeholders(hval, f"header:{hname}", index, placeholders)
    # 悬空引用检测：${...} 引用的产出键必须在 requires 或上游 save_as
    save_as_keys = {step.get("save_as") for step in flow.get("steps", [])}
    requires_keys = set((flow.get("requires") or {}).keys())
    dangling = _check_dangling(flow, save_as_keys | requires_keys)
    return {
        "ok": True,
        "tasks": [t.name for t in tasks],
        "asserts": len(flow.get("assert") or []),
        "placeholders": placeholders,
        "dangling": dangling,
    }


def _check_dangling(flow: dict, known_keys: set[str]) -> list[dict]:
    """检测 ${key} 引用是否在被引用前确有生产（save_as 或 requires）。"""
    import re
    dangling = []
    pat = re.compile(r"\$\{([^}.]+)(?:\.|\})")
    for index, step in enumerate(flow.get("steps", []), 1):
        for key in ("path", "body"):
            text = str(step.get(key, ""))
            for m in pat.finditer(text):
                ref = m.group(1)
                # 排除 ${ENV_VAR} 类环境变量（大写）与 ${_...} 路径占位
                if ref.isupper() or ref.startswith("_"):
                    continue
                if ref not in known_keys:
                    dangling.append({
                        "step": index, "where": key, "ref": ref,
                        "reason": f"${{{ref}}} 无对应 save_as 或 requires 产出（悬空引用）",
                    })
    return dangling


def scan_placeholders_only(flow: dict) -> list[dict]:
    """只扫描占位符（check-only 用）。"""
    found = []
    for index, step in enumerate(flow.get("steps", []), 1):
        for key in ("path", "method"):
            scan_placeholders(step.get(key), key, index, found)
        scan_placeholders(step.get("body"), "body", index, found)
        for hname, hval in (step.get("headers") or {}).items():
            scan_placeholders(hval, f"header:{hname}", index, found)
    return found

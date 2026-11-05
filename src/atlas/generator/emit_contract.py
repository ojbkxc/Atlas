"""响应 → 契约候选（自动分级 core/high/minimal）。

分级决策（保守、可解释，§4.4）：
- 默认 minimal
- 响应对象含 resultCode → high
- 响应是数组 → 锁死 minimal（high 要求 data 是对象，会误判数组型列表接口）
- core 仅文档显式标注 x-atlas-level: core 或显式"业务关键/强校验"
- --level 不能强升 core

候选落独立 .candidate.json，不进运行时注册表，人审后并入私有知识目录。
"""
from __future__ import annotations
from typing import Any

from .model import Endpoint


def grade_contract(ep: Endpoint, level_mode: str = "auto") -> dict[str, Any]:
    """给单个端点自动分级，返回契约候选条目（与 Contract dataclass 同构）。"""
    resp = ep.response
    # 数组响应锁死 minimal
    if resp.is_array:
        level = "minimal"
    elif ep.level_override == "core":
        level = "core"
    elif ep.level_override == "high":
        level = "high"
    elif resp.is_object and resp.has_result_code:
        level = "high"
    else:
        level = "minimal"
    # --level 不能强升 core（红线 #12：core 需文档显式标注）
    if level_mode == "core" and ep.level_override != "core":
        level = "high" if (resp.is_object and resp.has_result_code) else "minimal"
    elif level_mode == "high" and level == "minimal":
        # --level high 只能把「对象含 resultCode」的升到 high，不能把数组升 high
        if not resp.is_array and resp.is_object and resp.has_result_code:
            level = "high"
    contract = {
        "name": ep.summary or f"{ep.method} {ep.path}",
        "method": ep.method,
        "path": ep.path,
        "level": level,
    }
    # core 档才填 required（§4.4：绝不批量把所有字段塞进 core）
    if level == "core":
        contract["required"] = dict(resp.required_paths)
        sample = resp.sample if isinstance(resp.sample, dict) else {}
        if "resultCode" in sample:
            contract["result_code"] = str(sample["resultCode"])
    return contract


def emit_contracts(endpoints: list[Endpoint], *, level_mode: str = "auto",
                   source_label: str = "") -> dict[str, Any]:
    """产出契约候选文件内容（不进运行时注册表）。"""
    contracts = []
    conflicts: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    for ep in endpoints:
        entry = grade_contract(ep, level_mode)
        key = (entry["method"], entry["path"])
        if key in seen and seen[key] != entry:
            # #128：同 (method,path) 不同定义冲突，不覆盖
            conflicts.append({
                "method": entry["method"], "path": entry["path"],
                "existing": seen[key], "new": entry,
                "reason": "同键不同定义——静默择一会让校验强度取决于条目顺序",
            })
            continue
        seen[key] = entry
        contracts.append(entry)
    return {
        "source": source_label,
        "policy": "候选：默认 minimal；high 需响应为对象且含 resultCode；"
                  "core 仅文档显式标注；数组响应锁死 minimal",
        "contracts": contracts,
        "conflicts": conflicts,
    }

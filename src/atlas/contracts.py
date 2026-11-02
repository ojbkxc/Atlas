"""契约分级：界定范围 + 可执行的三级实现。

范围界定（不做 389 条全量强 schema）：
  - core   ：业务关键接口，完整字段类型校验（约 20 条）
  - high   ：高频读接口，通用规则 + 关键字段存在（约 80 条）
  - minimal：其余接口，只判 HTTP 成功与响应可解析

每个等级都有**可执行**的校验逻辑与明确失败信息，不是声明式摆设。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
from typing import Any

from .failures import ContractViolation

LEVELS = {"minimal": 0, "high": 1, "core": 2}
# 兼容旧命名
_ALIASES = {"weak": "high", "strong": "core"}
_TYPE_MAP = {"object": dict, "array": list, "string": str,
             "number": (int, float), "boolean": bool, "any": object}


@dataclass
class Contract:
    name: str
    method: str = "POST"
    path: str = ""
    level: str = "minimal"
    required: dict[str, str] = field(default_factory=dict)
    result_code: str | None = None

    def __post_init__(self) -> None:
        self.level = _ALIASES.get(self.level, self.level)
        if self.level not in LEVELS:
            raise ValueError(f"未知契约等级：{self.level!r}，只支持 {sorted(LEVELS)}")

    def validate(self, response: dict) -> None:
        if not self.path:
            return
        # minimal：只要求请求成功且响应可解析
        if not response.get("ok"):
            raise ContractViolation(f"{self.name} HTTP/接口失败：{response.get('status')}")
        data = response.get("data")
        if self.level == "minimal":
            if data is None:
                raise ContractViolation(f"{self.name} 响应缺少 data")
            return

        if not isinstance(data, dict):
            raise ContractViolation(f"{self.name} data 应为对象，实际 {type(data).__name__}")
        # high：通用规则（所有业务接口都应满足），不绑定具体字段
        if "resultCode" not in data:
            raise ContractViolation(f"{self.name} 响应缺少 resultCode（通用规则）")
        if self.level == "high":
            return

        # core：完整字段类型校验
        if self.result_code is not None and data.get("resultCode") != self.result_code:
            raise ContractViolation(
                f"{self.name} resultCode={data.get('resultCode')!r}，期望 {self.result_code!r}")
        for path, expected_type in self.required.items():
            value: Any = data
            for key in path.split("."):
                if isinstance(value, list):
                    try:
                        value = value[int(key)]
                    except (ValueError, IndexError):
                        raise ContractViolation(f"{self.name} data.{path} 下标越界") from None
                    continue
                if not isinstance(value, dict) or key not in value:
                    raise ContractViolation(f"{self.name} 缺少契约字段 data.{path}")
                value = value[key]
            if expected_type not in _TYPE_MAP:
                raise ValueError(f"{self.name} 未知类型 {expected_type!r}")
            expected = _TYPE_MAP[expected_type]
            if expected is not object and not isinstance(value, expected):
                raise ContractViolation(f"{self.name} data.{path} 类型应为 {expected_type}")


class ContractRegistry:
    def __init__(self, contracts: list[Contract] = ()):
        self.contracts = {(c.method.upper(), c.path): c for c in contracts}

    def add(self, contract: Contract) -> None:
        self.contracts[(contract.method.upper(), contract.path)] = contract

    def validate(self, method: str, path: str, response: dict) -> None:
        contract = self.contracts.get((method.upper(), path))
        if contract:
            contract.validate(response)

    def stats(self) -> dict:
        counts: dict[str, int] = {}
        for contract in self.contracts.values():
            counts[contract.level] = counts.get(contract.level, 0) + 1
        return counts

    @classmethod
    def load(cls, path: Path) -> "ContractRegistry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Contract(**item) for item in raw.get("contracts", [])])

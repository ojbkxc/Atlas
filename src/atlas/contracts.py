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
        # required 的键值在构造期就钉死形态，不留到 validate()：
        # 私有契约 JSON（ATLAS_KNOWLEDGE_DIR）是人手编辑的资产（AGENTS #11 明说「手改坏」场景），
        # 类型值写成数组/对象（`required: {"items": ["string"]}`，JSON 合法）时，旧实现到
        # validate() 才 `expected_type not in _TYPE_MAP`——unhashable 当场 TypeError、tuple 键
        # `.split` AttributeError：定性器先于被诊断报文死（与 #89/#94 同一红线）。
        # 提前到构造期，配合 load/plugin 的损坏回退，坏定义等同「文件写坏」→ 降级包内默认，
        # 而不是在建真实响应时崩掉整条断言链。
        for path, expected_type in (self.required or {}).items():
            if not isinstance(path, str) or not path.strip():
                raise ValueError(f"{self.name} 契约字段路径必须是非空字符串，收到 {path!r}")
            if not isinstance(expected_type, str) or expected_type not in _TYPE_MAP:
                raise ValueError(f"{self.name} 未知类型 {expected_type!r}（路径 {path!r}），"
                                 f"只支持 {sorted(_TYPE_MAP)}")

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
                # 构造期已拦截；保留作直接 mutate 场景的最后防线
                raise ValueError(f"{self.name} 未知类型 {expected_type!r}")
            expected = _TYPE_MAP[expected_type]
            if expected is not object and not isinstance(value, expected):
                raise ContractViolation(f"{self.name} data.{path} 类型应为 {expected_type}")
            # bool 是 int 子类：JSON 里 boolean 与 number 是不同类型。
            # 不加这条，数值字段返回 true/false 会被 (int, float) 误判通过，
            # 让 core 档「完整字段类型校验」漏掉序列化缺陷。
            if expected_type == "number" and isinstance(value, bool):
                raise ContractViolation(f"{self.name} data.{path} 类型应为 number，实际 boolean")


class ContractRegistry:
    def __init__(self, contracts: list[Contract] = ()):
        self.contracts = {}
        for contract in contracts:
            self._put(contract)

    def _put(self, contract: Contract) -> None:
        # #128：同 (method, path) 两条契约旧实现 dict last-wins 静默择一——与 #84
        # 「多生产者同产出键静默选择」完全同红线：core 在前 high 在后（手滑复制行没删干净）
        # 时强校验被弱校验遮蔽，「冒充覆盖率却永不生效」正是 --validate 自述的缺陷形态。
        # 但**等值重复必须放行**：toolbox.learn_contracts 靠同键覆盖对轮询重复抓包去重
        # （#102 净增计数语义），拒绝它=把正常路径翻成错误（#115 教训）。判别收敛
        # composition #93 同纪律：同键且契约逐字段相等=幂等登记；不等=定义冲突显式点名，
        # 引擎无法判定两条不同契约谁作准（校验强度取决于条目顺序）。ValueError 让
        # plugin.contracts 的损坏回退自动接住（私有文件有冲突行→回退包内）。
        key = (contract.method.upper(), contract.path)
        existing = self.contracts.get(key)
        if existing is not None and existing != contract:
            raise ValueError(
                f"契约冲突注册 {contract.method.upper()} {contract.path}："
                f"「{existing.name}」({existing.level}) 与「{contract.name}」({contract.level}) "
                f"同键但定义不同——静默择一会让校验强度取决于条目顺序，删掉一条或改 path")
        self.contracts[key] = contract

    def add(self, contract: Contract) -> None:
        self._put(contract)

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
        # 契约文件接真实项目走 ATLAS_KNOWLEDGE_DIR 私有覆盖（AGENTS #11），是人手编辑的资产。
        # 「JSON 合法但形态不对」的写法（顶层数组=忘包一层、条目是字符串、拼错键）旧实现
        # 裸崩 AttributeError/TypeError——且这些异常不在 DemoPlugin.contracts 捕获的
        # (JSONDecodeError, UnicodeDecodeError) 里，直接顶掉整条契约链（连包内回退都没走到），
        # 断言层整体死亡。统一转成可定位 ValueError：plugin 侧据此回退或点名，与 #73 损坏
        # 降级策略同口径。
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"契约文件 {path.name} 顶层必须是映射（含 contracts 列表），"
                             f"收到 {type(raw).__name__}")
        items = raw.get("contracts", [])
        if not isinstance(items, list):
            raise ValueError(f"契约文件 {path.name} 的 contracts 必须是列表，"
                             f"收到 {type(items).__name__}")
        contracts = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"契约文件 {path.name} 第 {i} 条必须是映射，"
                                 f"收到 {type(item).__name__}：{item!r}")
            try:
                contracts.append(Contract(**item))
            except TypeError as exc:
                # **展开的 TypeError（拼错键/缺必填）指不出是哪条；补定位信息再上抛
                raise ValueError(f"契约文件 {path.name} 第 {i} 条定义错误：{exc}") from None
        return cls(contracts)

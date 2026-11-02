"""声明式数据条件与 find_or_create，复用已有数据优先于造数。"""
from __future__ import annotations
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
from .config import AtlasConfig
from .failures import EnvBlocked

@dataclass
class DataRequirement:
    entity: str
    where: dict[str, Any] = field(default_factory=dict)
    strategy: str = "find_or_create"
    as_: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "DataRequirement":
        known = {"entity", "where", "strategy", "as", "on_env_blocked"}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"未知数据供给字段：{sorted(unknown)}")
        strategy = raw.get("strategy", "find_or_create")
        if strategy not in {"find", "find_only", "create", "find_or_create"}:
            raise ValueError(f"未知供给策略：{strategy}")
        policy = raw.get("on_env_blocked", "stop")
        if policy not in {"skip", "stop"}:
            raise ValueError("on_env_blocked 只支持 skip/stop")
        return cls(entity=raw["entity"], where=raw.get("where", {}), strategy=strategy,
                   as_=raw.get("as", ""))

@dataclass
class ProbeResult:
    entity: str
    data: dict[str, Any]
    action: str

class CreateQuota:
    def __init__(self, limit: int = 3):
        if limit < 0:
            raise ValueError("create 配额不能为负数")
        self.limit = limit
        self.created: list[dict] = []

    def reserve(self, entity: str, document: dict) -> None:
        """占用一个建单名额；调用方必须先 reserve 再真正建单。"""
        if len(self.created) >= self.limit:
            raise EnvBlocked(f"本次运行 create 配额已用完（{self.limit}），请复用数据或调整配额")
        self.created.append({"entity": entity, "document": document})

    def record_document(self, entity: str, document: dict) -> None:
        """建单完成后补记真实单号，供回收；不额外占名额。"""
        for item in reversed(self.created):
            if item["entity"] == entity:
                item["document"] = document
                return
        self.created.append({"entity": entity, "document": document})

    def save_for_recycle(self, path) -> None:
        """把本次造的单号写入回收清单，供 atlas recycle 命令读取。"""
        from pathlib import Path
        import json
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        records.extend(self.created)
        with path.open("w", encoding="utf-8") as stream:
            for item in records:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")

class DataProbe(ABC):
    """每种业务对象一个探针；find 是零副作用的 API 查询。"""
    entity: str

    @abstractmethod
    def find(self, where: dict[str, Any]) -> dict[str, Any] | None:
        pass

    @abstractmethod
    def create(self, where: dict[str, Any]) -> dict[str, Any]:
        pass

class CallableProbe(DataProbe):
    def __init__(self, entity: str, finder: Callable, creator: Callable | None = None):
        self.entity, self.finder, self.creator = entity, finder, creator

    def find(self, where: dict[str, Any]) -> dict | None:
        return self.finder(where)

    def create(self, where: dict[str, Any]) -> dict:
        if not self.creator:
            raise NotImplementedError(f"{self.entity} 尚未实现 create，不允许用 UI 自动兜底造数")
        return self.creator(where)


class ScriptedCreator:
    """把“已验证的建单脚本”接成 create 实现。

    为什么不是内置建单接口：不同 BU/环境的建单端点和必填字段差异极大
    （如 legacy-api 的 TEKA 路径只适用欧洲 UAT，客户与 divisionCode 硬编码）。
    框架不去猜接口，而是要求提供**已被人工验证过**的脚本函数。

    用法：
        creator = ScriptedCreator(create_fn, required_env=("DEMO_USERNAME",))
        probe = CallableProbe("SalesOrder", finder, creator)
    """
    def __init__(self, create_fn: Callable[[dict], dict], *, required_env: tuple[str, ...] = ()):
        self.create_fn = create_fn
        self.required_env = required_env

    def __call__(self, where: dict[str, Any]) -> dict:
        missing = [key for key in self.required_env if not os.environ.get(key)]
        if missing:
            raise EnvBlocked(f"建单脚本缺少环境变量：{', '.join(missing)}")
        result = self.create_fn(dict(where))
        if not isinstance(result, dict):
            raise TypeError("create 脚本必须返回 dict（至少含 remark 与单号）")
        return result


class DataProvider:
    def __init__(self, config: AtlasConfig, probes: list[DataProbe] | None = None,
                 create_quota: int = 3):
        self.config = config
        self.probes = {probe.entity: probe for probe in probes or []}
        self.quota = CreateQuota(create_quota)

    def register(self, probe: DataProbe) -> None:
        self.probes[probe.entity] = probe

    def supply(self, requirement: DataRequirement | dict) -> ProbeResult:
        if isinstance(requirement, dict):
            requirement = DataRequirement.from_dict(requirement)
        probe = self.probes.get(requirement.entity)
        if not probe:
            raise ValueError(f"未注册 DataProbe：{requirement.entity}")
        if requirement.strategy != "create":
            found = probe.find(requirement.where)
            if found is not None:
                return ProbeResult(requirement.entity, found, "find")
            if requirement.strategy == "find_only":
                raise EnvBlocked(f"环境缺少符合条件的 {requirement.entity}：{requirement.where}；"
                                 "find_only 禁止造数，请准备主数据或调整条件")
        if self.config.is_production:
            raise PermissionError("生产环境禁止 create，请改用 find_only")
        # 配额必须在调用建单脚本**之前**检查，否则超额时已经真实建了一单。
        self.quota.reserve(requirement.entity, {"where": requirement.where})
        created = probe.create(requirement.where)
        self.quota.record_document(requirement.entity, created)
        remark = str(created.get("remark", ""))
        if "非人工创建" not in remark:
            # 建单已发生，配额记录保留，便于按单号回收。
            raise ValueError(f"{requirement.entity} create 结果缺少「非人工创建」备注：{created!r}")
        return ProbeResult(requirement.entity, created, "create")

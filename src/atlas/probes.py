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
        # YAML 数据供给块的用户输入边界：requires 写成字符串会被逐字符当成「未知字段」
        # 报错（方向完全误导），写成 null 直接 TypeError——与 state_machine.from_dict(#77)
        # 同口径先验形态。entity 缺失是裸 KeyError，必须点名是哪个键。
        if not isinstance(raw, dict):
            raise ValueError(f"数据供给必须是映射，收到 {type(raw).__name__}：{raw!r}")
        entity = raw.get("entity")
        if not isinstance(entity, str) or not entity.strip():
            raise ValueError(f"数据供给缺少 entity（非空字符串），收到：{entity!r}")
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
        return cls(entity=entity, where=raw.get("where", {}), strategy=strategy,
                   as_=raw.get("as", ""))

@dataclass
class ProbeResult:
    entity: str
    data: dict[str, Any]
    action: str

# 建单结果里可作「单号」的键（回收链靠它定位真实单据）。
DOCUMENT_NUMBER_KEYS = ("orderNo", "documentNo", "quotationNo", "returnNo")

def find_document_number(result: dict) -> str:
    """取第一个「非空」单号；缺失/空串/纯空白/None/复合形态返回 ""。
    #117 单一真相：`require_audit_tag`（creators.py）与 `DataProvider.supply`（本文件）
    过去各写一份单号判据且都不严——require 用 `key in result`（`{"orderNo": ""}`、
    `{"orderNo": None}` 也算「有单号」放行），supply 干脆不查单号。生产主路径
    find_or_create 走 supply：脚本真实建了单却不回单号时，supply 静默返回成功、
    回收清单落 {"remark": ...} 无单号，atlas recycle 显示 orderNo '?'——真实建出的单
    永远找不回（回收链静默断，与 #86 落盘断链、#102 静默成功同族）。键名集合也归一处。"""
    for key in DOCUMENT_NUMBER_KEYS:
        value = result.get(key)
        if isinstance(value, str):
            if value.strip():
                return value.strip()
        elif value is not None and not isinstance(value, (dict, list, set, tuple, bool)):
            # 数字单号等标量按字符串接受；bool/容器不算单号（True→"True" 是假单号）。
            text = str(value).strip()
            if text:
                return text
    return ""

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
            # #132：与 reporting/#112 同防线——上次运行中断可把尾行腰斩在多字节字符上，
            # 旧 read_text 裸 UnicodeDecodeError：读崩=合并写回不执行，**本次新造的
            # 单号随崩溃一起丢**（#86 回收闭环被一条历史坏字节掏空，比列不出更糟——
            # 单据已真实产生却再无人知道单号）。replace 读入，残行落既有跳过分支。
            try:
                raw = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw = path.read_text(encoding="utf-8", errors="replace")
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue   # 残行不顶掉整份回收清单：一条写坏的历史记录不该让本次造单信息也存不下去
                if isinstance(parsed, dict):
                    records.append(parsed)
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
        # 建单刚发生就先验形态：裸 creator（不包 ScriptedCreator 的直传函数）返回非 dict 时，
        # 旧实现要到 remark 检查处 created.get 才 AttributeError——根因（返回值形态）被
        # 伪装成字符串崩溃。提到 record_document 之前：脏形态不进配额记录，错误点名实际
        # 类型与值。诚实边界：若脚本真实建了单却返回坏形态，单号无从捕获、回收链本就
        # 断在脚本侧——这正是必须拒绝坏 creator 的原因，报错里给出原值供人工追回。
        if not isinstance(created, dict):
            raise TypeError(f"{requirement.entity} create 必须返回 dict（至少含 remark 与单号），"
                            f"收到 {type(created).__name__}：{created!r}")
        self.quota.record_document(requirement.entity, created)
        remark = str(created.get("remark", ""))
        if "非人工创建" not in remark:
            # 建单已发生，配额记录保留，便于按单号回收。
            raise ValueError(f"{requirement.entity} create 结果缺少「非人工创建」备注：{created!r}")
        # #117 面1C：单号闸门必须与 require_audit_tag 同源（find_document_number）——
        # 过去 supply 只查备注不查单号：脚本真实建了单但没回单号时静默成功，回收清单
        # 落无单号记录，atlas recycle 显示 '?'，真实建出的单永远找不回（回收链静默断）。
        # 建单已发生 → 记录保留、错误给出原值供人工按备注/时间追回（与 remark 缺失同策略）。
        if not find_document_number(created):
            raise ValueError(
                f"{requirement.entity} create 结果缺少有效单号"
                f"（需 {'/'.join(DOCUMENT_NUMBER_KEYS)} 之一非空，回收链依赖它）：{created!r}")
        return ProbeResult(requirement.entity, created, "create")

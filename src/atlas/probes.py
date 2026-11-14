"""声明式数据条件与 find_or_create，复用已有数据优先于造数。"""
from __future__ import annotations
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
from .config import AtlasConfig, PROD_WRITE_WARNING
from . import delivery
from .atomic_io import atomic_write_text
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

    def reserve(self, entity: str, document: dict, idem: str = "") -> None:
        """占用一个建单名额；调用方必须先 reserve 再真正建单。

        `idem`（C12/design_c12 §3.4）：幂等关联把手——**给了才写键**（缺省空串=不写，
        与 C6 version 参「缺省不写=存量条目形态零涟漪」同先例，#115）。条目读侧
        （`item.get("idem")`）对无键旧行天然容错（#112）。同 (entity, where) 跨 run
        同 token：投递不确定失败后，它是下次 find/人工追单的关联把手。
        """
        if len(self.created) >= self.limit:
            raise EnvBlocked(f"本次运行 create 配额已用完（{self.limit}），请复用数据或调整配额")
        item = {"entity": entity, "document": document}
        if idem:
            item["idem"] = idem
        self.created.append(item)

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
        # wave4 rb-3：open("w") 截断重写非原子——写中崩把本次新造单号随崩溃一起丢
        #（#86 回收闭环同族：单据已真实产生却再无人知道单号）。改原子写（tmp+os.replace）。
        atomic_write_text(path, "".join(json.dumps(item, ensure_ascii=False) + "\n"
                                        for item in records))

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

    C12 幂等键到脚本面（design_c12 §3.5，红线 #11：引擎只传递，脚本侧去重归业务）：
    `honors_idempotency=True` 是**人的声明**——「本脚本兑现 atlas_idem 键」。缺省
    False：进 stdin 的 JSON **逐字节形态与旧版一致**（不往脚本不认识的键塞东西，
    红线 #12 不给未验证能力加功能面），token 仅落配额/回收台账。True：where 里
    注入 `{IDEM_WHERE_KEY: token}`；`idem_entity` 供与台账 token 同面对齐（缺省空串
    = where-only 把手，设计未给 entity 通路，接线时可显式传）。canonical 失败让位
    #61 报错（C12-F）：where 里坏值交给脚本侧 json.dumps 的点名，不另立第二处报错。
    """
    def __init__(self, create_fn: Callable[[dict], dict], *, required_env: tuple[str, ...] = (),
                 honors_idempotency: bool = False, idem_entity: str = ""):
        self.create_fn = create_fn
        self.required_env = required_env
        self.honors_idempotency = honors_idempotency
        self.idem_entity = idem_entity

    def _with_idem(self, where: dict[str, Any]) -> dict[str, Any]:
        """声明兑现时把幂等键注入 where（进 stdin 前最后一站）。"""
        if not self.honors_idempotency:
            return where
        try:
            token = delivery.idempotency_token(self.idem_entity, where)
        except ValueError:
            return dict(where)   # C12-F：坏 where 形态让位 creators #61 的键路径点名
        return {**where, delivery.IDEM_WHERE_KEY: token}

    def __call__(self, where: dict[str, Any]) -> dict:
        missing = [key for key in self.required_env if not os.environ.get(key)]
        if missing:
            raise EnvBlocked(f"建单脚本缺少环境变量：{', '.join(missing)}")
        result = self.create_fn(dict(self._with_idem(where)))
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
        if self.config.is_production and not getattr(self.config, "allow_prod_write", False):
            raise PermissionError("生产环境禁止 create，请改用 find_only")
        if getattr(self.config, "prod_write_allowed", False):
            # 显式放行生产写（ATLAS_ALLOW_PROD_WRITE=1）：与 engine 写护栏同一判据位
            # （config.prod_write_allowed），警告文案同一常量（#92）。诚实边界：create 供数
            # 不是 runs.jsonl 的 step，故此处只有 stderr 警告；run 记录的留痕由 flow 里的
            # 写 step（engine.validate_write 路径）承担。
            print(PROD_WRITE_WARNING, file=sys.stderr, flush=True)
        # C12（design_c12 §3.4）：幂等关联把手在**占额之前**算——reserve 之后 create 若抛，
        # 台账行已带 token，回收清单即证据。「同条件同 token」是下次 find/人工追单的把手。
        # C12-F（评审撞序裁决）：canonical 失败让位 creators #61 的「哪个键什么类型」点名——
        # 这里 idem 留空继续走，绝不抢那条更可归因的报错；配测钉归属。
        # 与 #14 的关系（gap6「同源取增量」落点）：#14 保证「超额不建单」（调用前占用），
        # C12 保证「失败不盲重发」；**现引擎没有任何自动二次 supply 通路**（provision 每
        # 需求恰调一次，dsl 侧无重试腿——测钉），若业务方手工重触发 supply，配额满即
        # EnvBlocked 拦在 create 之前。配额先占语义零改动，engine validate_write 零改动。
        try:
            idem = delivery.idempotency_token(requirement.entity, requirement.where)
        except ValueError:
            idem = ""
        # 配额必须在调用建单脚本**之前**检查，否则超额时已经真实建了一单。
        self.quota.reserve(requirement.entity, {"where": requirement.where}, idem=idem)
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

"""投递判据单一真相（C12，design_c12 §1）：写不盲重试 + 幂等关联把手。

一道闸一个小模块（对齐 `credential_gate`/`versioning`/`boundaries` 先例，#92 不留第二份
判据）。消费方只许调本模块：

- `safe_to_auto_reexecute`：**「同一操作被自动第二次执行是否安全」的唯一判据**。
  engine 的 auto 降级闸消费它；healing 维持既有门（read_only ∧ approved 知识 ∧
  控制流拒绝已是最严形态），**不改用本函数、不新增重试通道**（design_c12 §3.6）。
- `canonical_json` / `idempotency_token`：确定性关联把手——同 (entity, scope, where)
  跨 run 同 token，供 find 复用与人工追单核对。**只加数据面，不新增任何写路径**
  （红线 #9/#10）。

纯 stdlib（hashlib/json），零新依赖；离线可测，不触网络。
红线 #9 自证：读写分流判据**只取显式 `TaskMetadata.read_only`**（既有显式声明能力），
绝不从 intent/channel/api_operation 动词猜——猜即是判据错面，把写当只读自动重试。
"""
from __future__ import annotations
import hashlib
import json

from .failures import CONTROL_FLOW

IDEM_WHERE_KEY = "atlas_idem"   # 通用机制键名唯一出处（红线 #11：非业务字面量）
TOKEN_HEX_LEN = 16              # sha1 截断长度：刻意 **< 净化刀判据**——
                                # `credential_gate.looks_literal_secret` 只认 ≥32 hex/
                                # UUID/base64，16-hex 段不被 `sanitize_text` 吞掉，
                                # 关联把手必须活着落盘（design_c12 §4，对抗样本进测）。


def _reject_nonstring_keys(value: object, path: str = "where") -> None:
    """JSON 语义下键 `1` 与 `"1"` 是两个值——静默 str() 转换会把不同条件算成同一
    把手（比不把手更坏：假等价）。点名坏键路径，不猜（与 probes.from_dict #77 同口径）。"""
    if isinstance(value, dict):
        for key, sub in value.items():
            if not isinstance(key, str):
                raise ValueError(f"幂等 token 的 where 键必须是非空 str，收到 {type(key).__name__}"
                                 f"（{key!r}），路径 {path}")
            _reject_nonstring_keys(sub, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, sub in enumerate(value):
            _reject_nonstring_keys(sub, f"{path}[{index}]")


def canonical_json(value: object) -> str:
    """排序键 + UTF-8 + 无空格的稳定序列化——**where 键序不得影响 token**（YAML 载入的
    dict 键序随作者改行漂移，键序入摘要就等于把手随排版变）。

    坏形态点名 `ValueError`，不猜：非 str 键（上面下钻点名路径）、不可 JSON 序列化值
    （date/Path/集合/自定义对象）都拒。调用方**必须**把本函数当可失败面处理：
    失败时让位业务侧既有报错口径（design_c12 §3.4 与 creators #61 的撞序裁决，
    C12-F——idem 留空继续走，不许用本模块的 ValueError 抢掉「哪个键什么类型」的点名）。
    """
    _reject_nonstring_keys(value)
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"幂等 token 需要 JSON 稳定可序列化的 where，收到坏形态：{exc}") from exc


def idempotency_token(entity: str, where: dict, scope: str = "") -> str:
    """sha1(canonical([entity, scope, where]))[:16 hex]。

    确定性 ⇒ 跨 run 同条件同 token——下次 find 复用或人工追单时，台账 `idem` 就是
    关联把手。`scope` 由调用方注入（如 flow id），Atlas 不猜作用域（缺省空段）。
    """
    payload = canonical_json([str(entity), str(scope), where])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:TOKEN_HEX_LEN]


def safe_to_auto_reexecute(*, read_only: bool, attempted: bool, exc: BaseException) -> bool:
    """「同一操作被自动第二次执行是否安全」的唯一判据（真值表 design_c12 §2，逐行成测）：

    | read_only | attempted（请求已开始发出） | 异常       | 结果    |
    |-----------|------------------------------|------------|---------|
    | 任意      | 任意                          | 控制流     | False   | #108：终止信号不重跑；CONTROL_FLOW 单一真相在本判据内委托，不留第四份 tuple
    | True      | 任意                          | 非控制流   | True    | 只读无副作用，重试安全（现状 heal/降级语义不变，#115）
    | False     | False（可证一个请求都没发出） | 非控制流   | True    | 未发出=无投递，重跑等价首次执行。**依据限定 CallApi 通用 POST 面可证**（评审 C12-A：自定义 `_perform_api` 腿走插件自有传输，seq 不动 ≠ 未发出）
    | False     | True（已发出，结果未知）      | 非控制流   | False   | gap6 C12 主红线：**绝不把写当只读自动重试（#9）**

    `attempted` 未知（传输面无插桩）时调用方**必须按 True 传**——fail-closed：
    无法证明没发出，就视为已发出。
    """
    if isinstance(exc, CONTROL_FLOW):
        return False
    if read_only:
        return True
    return not attempted

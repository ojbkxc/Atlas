from __future__ import annotations
from enum import Enum

class FailureClass(str, Enum):
    ENV_BLOCKED = "env_blocked"
    SESSION = "session"
    INTERACTION = "interaction"
    CONTRACT = "contract"
    DATA = "data"
    ASSERTION = "assertion"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"

# 控制流异常单一真相（#114）：操作者/宿主的终止信号，不是业务失败。
# recorder/engine/healing 三处判据必须同源——旧实现 healing.py 内联硬编码同款三元组、
# engine 失败路径根本不过问控制流，「改一漏一必漂移」（与 #92/#97 同红线）：
# healing 已拒 KI 自愈（#108），但 recorder 仍把 KI 的 step 记成 fail_class="unknown"
# 进 atlas summary 缺陷分布、engine 又把 Ctrl-C 登记成待审坑点草稿（remedy 无从修复中断）——
# 终止信号被当业务缺陷污染 pass/fail 真相（违反 #70 interrupted 终态口径）。
CONTROL_FLOW = (KeyboardInterrupt, SystemExit, GeneratorExit)

class EnvBlocked(Exception):
    """环境限制是独立终态，不伪装成通过，也不伪装成代码失败。"""

class ContractViolation(AssertionError):
    pass

class FlowCompositionError(ValueError):
    pass

_RULES = (
    (FailureClass.ENV_BLOCKED, ("envblocked", "stepskipped", "bug-653", "default tax rate",
                               "bug-999", "nullpointerexception", "环境阻塞", "产品设计限制",
                               # 生产写护栏拒绝（config.validate_write / probes 建单门）：从 flow 视角是
                               # 「环境（prod）禁止此操作」，属环境策略阻挡，非代码缺陷——归 env_blocked
                               # 而非 unknown，否则 atlas summary 缺陷分布里冒出一类无法归因的
                               # 「未知失败」，把确定性护栏拒绝伪装成待诊断缺陷（#114 面A）。
                               "生产环境禁止")),
    (FailureClass.SESSION, ("401", "403", "unauthorized", "auth_expired", "signin", "cookie", "session")),
    (FailureClass.CONTRACT, ("contractviolation", "bug-", "cannot be empty", "required", "invalid")),
    (FailureClass.ASSERTION, ("assertionerror", "mismatch", "expected", "unexpected", "断言")),
    (FailureClass.INTERACTION, ("timeout", "not visible", "click", "locator", "intercept", "element")),
    (FailureClass.DATA, ("not found", "no such", "no data", "empty", "找不到")),
)

# 精确"数据缺失"信号：ICS/中文系统真实措辞。必须在通用 ASSERTION 之前判定——
# `AssertionError: 客户查询/选中失败: 查询无结果` 的根因是「换真实客户号」(data)，
# 不是「断言太严」(assertion)；误分类会误导 heal/塔把它当测试 bug。但 ENV/SESSION/CONTRACT
# 更具体的根因仍优先（401 就是 session，哪怕消息也写了「无结果」）。
_DATA_ABSENT = ("查询无结果", "查无", "无结果", "无记录", "结果为空", "无数据",
                "不存在", "没有数据", "no result", "not exist", "no record")

def _exception_display(exc: BaseException) -> str:
    """异常消息体的单一真相提取：`__str__`/`__format__` 自身崩（业务插件真实雷区，
    #126 面A 形态）时回落空串，绝不裸抛。exception_message（展示）与 exception_text
    （定性）都从它派生——#129 前 agent 手拼第三份 f-string 副本，正是「改一漏一」
    漏掉的那一处（#92/#127 同红线）。"""
    try:
        return f"{exc}"
    except Exception:
        return ""

def exception_message(exc: BaseException) -> str:
    """留痕展示形态（#129）：`"{类型名}: {消息}"`——classify_failure 只抽小写子串定性，
    但 agent/recorder 的 reason/evidence 需要保留原文与类型名。坏 __str__ 回落裸类型名
    （消息体为空时不留悬空冒号）——类型名仍是有效归因信号，定性器绝不先于被定性对象死
    （#89），更不在 except 块里炸掉「已执行写 Task 的留痕」（#85）。"""
    msg = _exception_display(exc)
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__

def exception_text(exc: BaseException) -> str:
    """异常定性文本（小写）：`"{类型名} {消息}"`——classify_failure 与
    knowledge.match_pitfall 共用，此前两处各写一份 f-string（#92 改一漏一），#127 收敛
    为单一真相；坏 `__str__` 回落类型名形态（trigger 多为 `type(exc).__name__`，
    draft_pitfall 产的行就是这么写的），继续定性，绝不裸抛。"""
    return f"{type(exc).__name__} {_exception_display(exc)}".lower()

def classify_text(text: str) -> FailureClass:
    """异常文本 → FailureClass 的单一真相；classify_failure 与 Allure 解析共用。

    classify_failure(exc) = 控制流短路 + classify_text(exception_text(exc))。
    控制流判定（isinstance CONTROL_FLOW）由 classify_failure 在调用本函数之前短路，
    本函数只管关键词匹配——Allure 解析拿到的是文本（message/trace），没有异常对象。
    内部 lower() 保证调用方传入任意大小写文本都能正确匹配（_RULES/_DATA_ABSENT 全小写）。
    """
    lowered = text.lower()
    # 环境/会话/契约是更具体的根因，先按原序判它们
    for kind, words in _RULES[:3]:
        if any(word in lowered for word in words):
            return kind
    # 数据缺失精确信号优先于通用断言（断言包着「查无」其实是数据问题）
    if any(word in lowered for word in _DATA_ABSENT):
        return FailureClass.DATA
    return next((kind for kind, words in _RULES[3:] if any(word in lowered for word in words)),
                FailureClass.UNKNOWN)

def classify_failure(exc: BaseException | None) -> FailureClass:
    if exc is None:
        return FailureClass.UNKNOWN
    # 控制流终止信号单列 interrupted——不落入 unknown（否则 recorder.step 把 Ctrl-C 记成
    # 「无法归因的未知缺陷」进 atlas summary 缺陷分布，违反 #70 与 dsl flow 级 interrupted
    # 同口径；也先于关键词匹配，KI 的类型名恰好含无关子串时不得被误判）。
    if isinstance(exc, CONTROL_FLOW):
        return FailureClass.INTERRUPTED
    return classify_text(exception_text(exc))

def _status_code(value: object) -> int | None:
    """归一化重放状态码：HAR/JMX/录制抓包常把 status 存成字符串 "500"，
    直接 `"500" >= 500` 让定性器 TypeError 裸崩（prove_api 无 try 时顶掉原用例异常），
    `"401" in {401, 403}` 恒 False 让会话证据静默漏判、定性翻转。与 data 形态零假设
    （#89）同一纪律：status 形态同样由重放来源决定。非数值形态（None/""/"OK"）归 None，
    让上层走既有的「无状态码」回落（文本定性），不猜也不崩。"""
    if isinstance(value, bool):  # bool 是 int 子类，True/False 不是状态码
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None

def api_evidence(original: BaseException, replay: dict) -> FailureClass:
    """UI 失败时重放同接口：API 正常归交互；API 失败优先识别会话/环境/契约。"""
    if replay.get("ok"):
        return FailureClass.INTERACTION
    status = _status_code(replay.get("status"))
    # data 形态由被重放的接口决定，不假设是 dict：ICS 列表接口 data 常为数组、
    # 网关错误常为字符串、失败时常为 null。对非 dict 直接 .get 会让「失败定性」
    # 本身裸崩 AttributeError——定性器比被定性对象先死，现场证据全部丢失。
    data = replay.get("data")
    if status in {401, 403} or (isinstance(data, dict) and data.get("auth_expired")):
        return FailureClass.SESSION
    # data 键存在但为 null 时 dict.get 返回 None（默认值只在键缺失时生效），
    # str(None)="none" 会把 error 里的真实根因挤出定性文本。
    evidence = RuntimeError(str(data if data is not None else replay.get("error", "")))
    if status and status >= 500:
        return FailureClass.ENV_BLOCKED
    classified = classify_failure(evidence)
    return classified if classified != FailureClass.UNKNOWN else FailureClass.ENV_BLOCKED

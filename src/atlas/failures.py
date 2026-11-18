from __future__ import annotations
from enum import Enum

from .credential_gate import sanitize_text

class FailureClass(str, Enum):
    ENV_BLOCKED = "env_blocked"
    SESSION = "session"
    INTERACTION = "interaction"
    CONTRACT = "contract"
    DATA = "data"
    ASSERTION = "assertion"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"
    # C9 显态第 9 类（追加，既有 8 类的值与序零变动——枚举重排会把已落盘的取值口径掀翻）：
    # 操作没完成，因为在**等人**（UAT 验证码、人工审批步骤）。既不是缺陷，也不是中断：
    # 中断=宿主把跑终止了（没人同意继续），等人=跑停在一个人需要处置的位置上。
    WAITING = "waiting"

# 操作/环境终态家族的唯一判据（design_c9 §1）。为什么必须有它：此前每个消费点各自写
# 「哪几个状态不算缺陷」的那两个字符串字面量（allure_analysis 的桶、summary 的计数、
# 调度侧的计败表各抄一份），C9 加第三个成员时就是 #92「改一漏一」的温床——漏一处的后果
# 恰好是红线 #7 的反面：`waiting` 被读进 failed 缺陷分布，或被静默当成没发生。
# 家族语义：不进 failed、不静默绿，独立 label/独立计数可见。
OPERATIONAL = frozenset({FailureClass.ENV_BLOCKED.value,
                         FailureClass.INTERRUPTED.value,
                         FailureClass.WAITING.value})


def is_operational(value: object) -> bool:
    """run 状态或 step `fail_class` 是否属「不进 failed 缺陷分布」家族。

    非 str、未知取值一律 False——**不猜**（把读不懂的状态默认为非缺陷就是洗绿，
    与 #102/#105 假阴性同族；判据函数也不得先于输入死，#89 同族）。
    FailureClass 成员先取 `.value`：str 混入枚举的 hash 口径跨 Python 版本不一致
    （3.11 按成员名、3.12+ 按值），直接拿成员做 frozenset  membership 在部分版本恒 False，
    于是「等人」被读成缺陷——这正是本函数要消灭的那类漂移。"""
    if isinstance(value, FailureClass):
        value = value.value
    return isinstance(value, str) and value in OPERATIONAL


# flow 级「看护超时」终态（supervisor 强杀后由 cli 的收尾腿补记，父进程另返 rc124）。
# **刻意不入 OPERATIONAL**（design_c9 评审必改 C9-E + lead 拍板）：家族三成员描述的是
# 「人/环境把结果挡住了」，而超时是「跑到一半被系统掐了」——它既不是等人、也不是环境
# 策略，混进家族会让「看护配置太紧」这种工程问题被读成环境阻塞。但也不能落进 failed
# 缺陷分布（那是真缺陷的桶），所以给它**独立可见位**：`run_attention` 的 `timeout` 键
# 单独列示（2026-11-06 落地，此前只有「不入家族」的声明与负向钉，没有任何正向落点，
# 结果超时的 run 在 attention 块里彻底隐身）。
# 常量而非散写字面量：生产方（cli 收尾）与消费方（reporting 计数）必须同值（#92）。
FLOW_STATUS_TIMEOUT = "timeout"

# 控制流异常单一真相（#114）：操作者/宿主的终止信号，不是业务失败。
# recorder/engine/healing 三处判据必须同源——旧实现 healing.py 内联硬编码同款三元组、
# engine 失败路径根本不过问控制流，「改一漏一必漂移」（与 #92/#97 同红线）：
# healing 已拒 KI 自愈（#108），但 recorder 仍把 KI 的 step 记成 fail_class="unknown"
# 进 atlas summary 缺陷分布、engine 又把 Ctrl-C 登记成待审坑点草稿（remedy 无从修复中断）——
# 终止信号被当业务缺陷污染 pass/fail 真相（违反 #70 interrupted 终态口径）。
CONTROL_FLOW = (KeyboardInterrupt, SystemExit, GeneratorExit)

class EnvBlocked(Exception):
    """环境限制是独立终态，不伪装成通过，也不伪装成代码失败。"""

class AwaitingHuman(Exception):
    """等待人工的显式信号（design_c9 §1）：与 EnvBlocked 同族位——操作终态，不是代码缺陷。

    `__init__` 强制非空原因：裸一条「waiting」落进 runs.jsonl 等于新型静默（#70/#86 同族——
    事后看不出在等什么、该找谁），所以消息必须点名在等什么、需要谁。基类选 `Exception`
    而**不是** `RuntimeError`：engine 的 auto 降级链捕 `(NotImplementedError, RuntimeError)`，
    继承 RuntimeError 会让「等人」被降级重跑一次（等的是人，不是重试）。
    """
    def __init__(self, reason: str):
        if not str(reason).strip():
            raise ValueError("AwaitingHuman 必须带非空原因（在等什么、需要谁）")
        super().__init__(reason)

class ContractViolation(AssertionError):
    pass

class DeliveryUncertain(RuntimeError):
    """C12（design_c12 §3.3）：请求已发出、投递结果未知——写任务降级重跑的拦截终态。

    不是策略阻挡（≠`EnvBlocked`：护栏拒的是「不该发」，这里已经发了），也不是代码缺陷
    归因（≠契约/断言/交互缺陷：没有任何「错」可修，能做的只有人工核实）。
    继承 `RuntimeError` 是有意：任何仍按 `RuntimeError` 处理的旧消费方不被翻成裸崩
    （#115 不翻正常路径），新消费方按 `isinstance` 精判（classify_failure/engine 降级闸/
    `_retry_eligible`）。红线 #7：该失败**保持 failed**——不借任何终态洗绿。
    """

# C12-E（design_c12 §5 遗留项，2026-11-06 放行）：**类型级**永不自动重试名单。
# 判据必须来自类型，不能来自元数据或文本——这两者正是本波自己论证过会撒谎的东西：
# ① `read_only` 元数据：C12-A 拍板的原话就是「自定义 API 腿不经 CallApi，seq 不动 ≠ 未
#    发出」，既然连副作用是否存在都要 fail-closed 猜，就更不能拿它当「可以放心重跑」的依据
#    （业务插件可对读语义 Task 抛 DeliveryUncertain，元数据门形同虚设）；
# ② 文本：`waits.is_recoverable` 的 `_is_transient` 跑在 `classify_failure` **之前**，
#    reason/cause 文案里出现 "navigation"/"detached" 就会先被判可重试——一条写着
#    「等待人工确认，前序 navigation 中断」的 AwaitingHuman 会被就地重跑若干遍，而重试
#    永远等不来人（engine.py 的分支次序只挡住了引擎那条腿，`waits.retry(retry_on=...)`
#    循环内二次抛出时仍会漏）。
# 与 CONTROL_FLOW 同为「先于任何文本判据」的短路位；消费方只有一处（waits.is_recoverable），
# 不在引擎里再抄一份名单（#92）。
NEVER_AUTO_RETRY = (AwaitingHuman, DeliveryUncertain)

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
    （定性/落盘）都从它派生——#129 前 agent 手拼第三份 f-string 副本，正是「改一漏一」
    漏掉的那一处（#92/#127 同红线）。

    C4② 落点：这里返回的是**净化前原文**。净化（`credential_gate.sanitize_text`）只加在
    派生出的落盘形态上，一条刀覆盖三条路径；**定性侧不吃净化文本**——见 `_classify_input`
    （评审必改 C4-B：`?code=401` 这类「关键词只在敏感值里」的样本，若先净化再定性会把
    SESSION 翻成 unknown，脱敏反而损归因）。
    """
    try:
        return f"{exc}"
    except Exception:
        return ""

def _classify_input(exc: BaseException) -> str:
    """定性输入（小写原文）：`"{类型名} {消息}"`——**不净化**。

    与 `exception_text` 唯一的区别就是这一刀：落盘要遮蔽，归因要保真。两者同源
    `_exception_display`，不是两份判据（#92）。
    """
    return f"{type(exc).__name__} {_exception_display(exc)}".lower()

def exception_brief(exc: BaseException) -> str:
    """落盘摘要形态（净化后、**不含类型名**）：engine 的 `degraded_to_ui.reason` 既有内容
    就是 `str(exc)`，加类型名会改变 runs.jsonl 已落盘的取值形态（存量断言逐字对不上，
    设计 C4 §4 也明写「形态不变」）——所以这里给净化后的裸消息体。
    与 exception_message/exception_text 同源 `_exception_display` + 同一把净化刀（#92）。"""
    return sanitize_text(_exception_display(exc))

def exception_message(exc: BaseException) -> str:
    """留痕展示形态（#129）：`"{类型名}: {消息}"`，消息体已净化（C4②）——
    classify_failure 只抽小写子串定性，但 agent/recorder 的 reason/evidence 需要保留原文
    与类型名。坏 __str__ 回落裸类型名（消息体为空时不留悬空冒号）——类型名仍是有效归因
    信号，定性器绝不先于被定性对象死（#89），更不在 except 块里炸掉「已执行写 Task 的
    留痕」（#85）。"""
    msg = exception_brief(exc)
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__

def exception_text(exc: BaseException) -> str:
    """异常文本（小写、**净化后**）：三条落盘路径与知识匹配的共用形态（#127 收敛自两处
    各写一份 f-string；C4② 在此单点加净化刀）。坏 `__str__` 回落类型名形态（trigger 多为
    `type(exc).__name__`，draft_pitfall 产的行就是这么写的），继续定性，绝不裸抛。

    消费方分工（评审 C4-B）：
    - `classify_failure` 用 `_classify_input`（原文）——归因优先；
    - journal 草稿 `evidence.text`（experience.draft）、坑点 trigger 匹配
      （knowledge.match_pitfall）、waits 瞬态判定读这里——落盘/展示优先。
      trigger 多为异常类名与词组，净化只动 `键=值` 与 URL query，不动词序与类名；
      「关键词恰好只在敏感值里」的对抗样本由定性侧原文兜住（见 test_failures_sanitize）。
    """
    return sanitize_text(_classify_input(exc))

def classify_text(text: str) -> FailureClass:
    """异常文本 → FailureClass 的单一真相；classify_failure 与 Allure 解析共用。

    classify_failure(exc) = 控制流短路 + classify_text(_classify_input(exc))——
    **定性吃原文，落盘吃净化文本**（C4-B：反序会把 `?code=401` 一类样本的 SESSION 翻成
    unknown，脱敏损归因）。
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
    # C9 显态短路（design_c9 §2 钉死的次序）：**只看类型，永不看关键词**——
    # 「waiting/等待人工/approval required」出现在外部文本里（SCS Allure 结果、历史日志）
    # 一律维持原归类，绝不产出 waiting。这是红线 #7 的输入面：把一次真失败误标成「等人」
    # 就是洗绿的另一条路，而文本恰好是唯一能被外部数据喂进来的入口。
    # 次序在 CONTROL_FLOW 之后（终止信号优先：人按 Ctrl-C 打断的正是等人工的那一步，
    # 语义仍是 interrupted）；C12 的 DeliveryUncertain→INTERACTION 短路位在本行之后、
    # 下面 classify_text 委托之前（两波各自独立追加，互不改写对方的判据）。
    if isinstance(exc, AwaitingHuman):
        return FailureClass.WAITING
    # C12 投递不确定短路（design_c12 §3.3）：只看类型——`_RULES` 关键词表一字不动，
    # 文本侧永不产出该归类（外部文本不猜投递状态，与 C9「关键词不造假 waiting」同纪律）。
    # 归 INTERACTION：网络交互故障族；**不进** ENV_BLOCKED（不是策略阻挡）**不落**
    # unknown（污染缺陷分布，#114 面 A 同红线）。
    if isinstance(exc, DeliveryUncertain):
        return FailureClass.INTERACTION
    return classify_text(_classify_input(exc))

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

def raise_type(name: object) -> type:
    """en-12：把 assert raises 的 `type` 字段解析成**内置异常类**——唯一解析点。

    只认 `builtins` 里的真实异常类（ValueError / AssertionError / TimeoutError / …），
    自定义名/非异常/容器/非字符串一律 ValueError 点名拒绝（构造期，不是跑到一半才炸）。
    刻意**不**认业务插件自定义异常：跨插件名冲突会静默匹配错类（#92 不留第二份判据——
    谁要扩展谁在 dsl 层显式传类对象，本函数只服务 YAML 文本边界）。

    返回的是 `type[BaseException]`；DSL 的 `_RaisesTask` 用 `isinstance(exc, cls)` 判命中，
    因此子类也算（TimeoutError 命中 raises: OSError 是合法写法，与 Python except 语义一致）。
    """
    import builtins
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"assert raises 的 type 必须是非空字符串（内置异常名），收到 {name!r}")
    candidate = getattr(builtins, name.strip(), None)
    if not (isinstance(candidate, type) and issubclass(candidate, BaseException)):
        raise ValueError(f"assert raises 的 type {name!r} 不是内置异常名——只支持 builtins 里的"
                         f"异常类（ValueError / AssertionError / TimeoutError / …），业务自定义"
                         f"异常不在白名单（引擎不猜跨插件名）")
    return candidate


class ReconciliationMismatch(AssertionError):
    """en-15：跨模块对账失败——两侧值不一致（或路径不可解析）。

    继承 AssertionError：`atlas summary` 与 review 的现有断言桶直接可吃（不新增 fail_class，
    不翻存量断言）。语义上是**数据不一致**（DATA 桶更贴切），但既有 classify_failure 的
    ASSERTION 规则先命中 "expected" 关键词——保持 ASSERTION 落桶，与 `assert a == b` 表达式
    失败同桶，人审时按消息里的 reconcile 前缀辨认（不为此新开第 10 类，红线 #12：不给零
    消费方加投机分类）。
    """


def reconcile_diff(left: object, right: object, *, fields: list[str] | None = None,
                   left_label: str = "left", right_label: str = "right") -> dict:
    """en-15 对账 diff：只读、无副作用，失败返回带两侧值与路径的清单（不抛）。

    `fields` 为空 → 整值相等（`left == right`）——字典/列表整体比，不等时返回单条路径 ""；
    `fields` 非空 → 逐字段下钻（点路径，复用 `tasks._dig` 的路径判据语义但不抛——任一侧
    路径不可解析记为 `<missing>` 并算不等），只对列出的字段做值级 diff，不做模糊匹配。

    返回 `{"equal": bool, "mismatches": [{"path", "left", "right"}]}`；调用方（dsl
    `_ReconcileAssert.run`）拿 mismatches 拼异常消息（两侧值全带，方便人比对）。
    值展示用 repr 截 200 字符（防大响应淹没消息）——**展示截断，判据不截断**。
    """
    mismatches: list[dict] = []
    if not fields:
        equal = left == right
        if not equal:
            mismatches.append({"path": "", "left": left, "right": right})
        return {"equal": equal, "mismatches": mismatches}
    for path in fields:
        if not isinstance(path, str) or not path.strip():
            mismatches.append({"path": str(path), "left": "<bad-path>",
                               "right": "<bad-path>"})
            continue
        lv = _dig_or_missing(left, path)
        rv = _dig_or_missing(right, path)
        if lv != rv:
            mismatches.append({"path": path, "left": lv, "right": rv})
    return {"equal": not mismatches, "mismatches": mismatches}


def _dig_or_missing(value: object, path: str) -> object:
    """对账下钻：路径不可解析返回 `<missing>` 哨兵（不抛）——任一侧缺字段=不等，不是崩。"""
    missing = "__atlas_missing__"   # 与 waits 的 "__atlas_*" 哨兵同族（字符串化安全）
    cur = value
    for part in str(path).split("."):
        try:
            if isinstance(cur, dict):
                cur = cur[part]
            elif isinstance(cur, (list, tuple)):
                cur = cur[int(part)]
            else:
                return missing
        except (KeyError, IndexError, ValueError, TypeError):
            return missing
    return cur


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

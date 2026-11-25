"""gap12 W2 + wave3：LLM 自主探索循环——把「脑」装进 Atlas（`docs/gaps/gap12-llm-autonomous-explore.md`）。

闭环：观察（aria snapshot → 候选元素）→ 决策（LLM 输出**白名单动作**）→ 护栏（生产闸 /
风险分级 / 审批）→ 执行 → 留痕 → 直到 done/上限 → 沉淀（trace → pending_review 草稿）。

wave3 补的三块（`docs/gaps/gap14-ai-autonomous-test-loop.md`）：
- **判据动作实做**：`assert` 四类判据（元素可见 / 文案匹配 / 接口状态码与结构 /
  跨模块值比对）真正执行并得出 pass/fail——失败抛 `AssertionError`，归类走
  `failures.classify_failure` **单一真相**，本模块不写第二套判词。
- **证据与缺陷落盘**：全程写 runs.jsonl（复用 `recorder`），失败步骤带**请求实参**
  （method/url/post_data，过净化刀）+ 响应快照 + 页面快照——「没反应类结论必须附请求实参」
  （playbook §1）的可执行形态；`atlas allure` / `allure-analyze` 直接可吃（标准 runs.jsonl 形态）。
- **坑点自动提炼**：未命中已有 approved 坑点（`match_pitfall(status="approved")` 语义）的失败
  → LLM 从证据出 trigger/symptom/root_cause/remedy（严格 JSON，坏形态点名拒绝不静默回落）
  → `journal.draft_from_text` 造 pending_review 草稿，evidence 带 `review.verification_problems`
  四元组——**只进塔不写知识**（红线 #9/#10）。

复用单一来源，不留第二把尺：
- 观察/记录/沉淀 → `explore.extract_candidates` / `explore._record_checkpoint` / `explore.compile_trace`
- 定位 → `locators.resolve`（autopilot 的 click/fill/visible/compare 同一把定位判据）
- 接口响应路径打点 → `tasks._dig`（路径判据单一来源，不复制第二份）
- 风险分级 → `agent.classify_risk`（本模块只把动作映射成它的输入，不另写判据）
- 失败归类 → `failures.classify_failure`（recorder.step 内单点继承，本模块零判词）
- 决策留痕 → `agent.Decision` + `AtlasAgent._record`（调用方注入 agent；缺席则不落盘但不静默）

安全红线（设计 §F）：LLM 只许输出白名单动作，**绝不能直接执行任意 JS/SQL/文件操作**；
写/提交类动作按 `classify_risk` 拦在审批门下（默认 `max_auto_risk=LOW`，即普通点击可自动、
Save/Submit/Confirm 类停）。请求/响应观察只**读**页面事件（取证），不发任何新请求。
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import Decision, RiskLevel, classify_risk
from .core import TaskMetadata
from .credential_gate import sanitize_text, sanitize_url
from .explore import PRODUCTION_REFUSED, _record_checkpoint, compile_trace, extract_candidates
from .failures import CONTROL_FLOW, classify_failure, exception_brief, exception_text
from .llm import LLMError
from . import locators
from .recorder import RunRecorder
from .tasks import _dig

# 白名单：LLM 只能从中选。任何白名单外的动作（尤其执行 JS/任意代码）一律拒绝。
ACTION_WHITELIST = ("click", "fill", "press", "goto", "assert", "wait", "record", "done")
# 无副作用的动作（记录/断言/等待/导航）：只读
_READONLY_KINDS = ("assert", "wait", "record", "goto", "done")
# assert 的判据形态（parse_action 与执行共用同一名单）
ASSERT_KINDS = ("visible", "text", "api", "compare")

# 证据面容量（取证是尽力而为的旁路面，绝不让证据膨胀拖垮循环）：
# 请求/响应各留最近 N 条；post_data 与响应体截断；快照进证据截断。
_MAX_EVIDENCE_HISTORY = 50
_MAX_POST_DATA_CHARS = 2000
_MAX_RESPONSE_BODY_CHARS = 8000
_MAX_SNAPSHOT_IN_EVIDENCE = 4000
_MAX_SUMMARY_FIELD_CHARS = 500


class ActionRejected(ValueError):
    """LLM 提议的动作不在白名单 / 形态坏 —— 拒绝执行并计数（绝不「尽力解释后照跑」）。"""


class SummaryRejected(ValueError):
    """LLM 失败摘要形态坏（非 JSON / 缺字段 / 未知键）——点名拒绝，绝不拿坏摘要冒充证据。"""


@dataclass
class _ActionTask:
    """`classify_risk` 的鸭子类型输入（它只读 `.name` / `.metadata`）。"""
    name: str
    metadata: TaskMetadata


@dataclass
class ExploreContext:
    """循环内的证据态：请求/响应观察 + 页面快照 + record 存的值（改前值）。"""
    snapshot: str = ""
    requests: list = field(default_factory=list)   # {method, url, post_data}（已净化）
    responses: list = field(default_factory=list)  # {url, status, content_type, body}（已净化）
    values: dict = field(default_factory=dict)     # record(key, value) 存的改前值

    def record_request(self, method: str, url: str, post_data: str) -> None:
        self.requests.append({"method": method, "url": url, "post_data": post_data})
        del self.requests[:-_MAX_EVIDENCE_HISTORY]

    def record_response(self, url: str, status, content_type: str, body: str) -> None:
        self.responses.append({"url": url, "status": status,
                               "content_type": content_type, "body": body})
        del self.responses[:-_MAX_EVIDENCE_HISTORY]


@dataclass
class AutoReport:
    steps: list = field(default_factory=list)
    rejected: int = 0
    failures: int = 0
    asserts: int = 0          # 本次 run 实际评估的判据条数（pass+fail，草稿三元组 assertions 用）
    pitfall_drafts: int = 0   # 提炼进塔的坑点草稿条数
    stopped_reason: str = ""
    trace: Path | None = None
    drafts: dict = field(default_factory=dict)


def parse_action(text: str) -> dict:
    """LLM 回复 → 动作 dict。单行 JSON、对象、白名单内；否则 `ActionRejected`。"""
    raw = (text or "").strip()
    if raw.startswith("```"):                     # 容忍 ```json … ``` 包裹
        raw = raw.strip("`")
        if raw[:4].lower() == "json":
            raw = raw[4:]
        raw = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ActionRejected(f"动作不是合法 JSON：{raw[:120]!r}") from exc
    if not isinstance(obj, dict):
        raise ActionRejected(f"动作必须是 JSON 对象，收到 {type(obj).__name__}")
    kind = obj.get("action")
    if kind not in ACTION_WHITELIST:
        raise ActionRejected(f"动作 {kind!r} 不在白名单 {ACTION_WHITELIST}")
    if kind in ("click", "fill") and not (obj.get("role") and obj.get("name")):
        raise ActionRejected(f"{kind} 必须带 role+name（只许从候选里选）")
    if kind == "goto" and not obj.get("url"):
        raise ActionRejected("goto 必须带 url")
    if kind == "assert":
        _validate_assert(obj)
    if kind == "wait":
        _validate_wait(obj)
    if kind == "record" and obj.get("key") is not None:
        value = obj.get("value")
        if value is None or isinstance(value, (dict, list)):
            raise ActionRejected("record 带 key 时 value 必须是标量（改前值，供 compare 用）")
    return obj


def _validate_wait_int(field: str, value: Any, *, allow_zero: bool) -> None:
    """wait 旋钮的数值判据（2026-11-24 实测缺口，与 `--fail-under -1` / autopilot
    `--max-steps` 同族：**参数值域不校验 → 功能静默失效**）。

    改前的三个形态（都在 `_execute` 里静默成形，没有任何输出提示）：

    - `min_rows: -1` → `count >= -1` 第一拍恒真，「等表格就绪」变成「立刻就当它好了」
      （假绿）；
    - `timeout_ms: 0` → `int(action.get("timeout_ms") or 10000)` 的 `or` 把 0 当空值，
      静默换成 10000（想「不等」，实际等了 10s）；负数则第一拍就 TimeoutError；
    - `ms: 0`（旧行为兼底路径）同理；`5.7` 被 `int()` 静默截成 5。

    bool 一律不算数（`True` 是 `int` 子类，会静默变 1）。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        kind = "非负整数" if allow_zero else "正整数"
        raise ActionRejected(
            f"wait.{field} 必须是{kind}（收到 {value!r}）——"
            f"它们是毫秒预算/行数下限，写错不会报错，只会静默改变等待语义")
    if allow_zero and value < 0:
        raise ActionRejected(
            f"wait.{field} 必须是非负整数（收到 {value!r}）——"
            f"负数会让「行数 ≥ min_rows」第一拍恒真：表格还没数据就判就绪（假绿）")
    if not allow_zero and value <= 0:
        raise ActionRejected(
            f"wait.{field} 必须是正整数毫秒（收到 {value!r}）——"
            f"0 会被当成空值静默换成默认预算，负数则第一拍就超时"
            f"（想「不等」就别发这个动作）")


def _validate_wait(obj: dict) -> None:
    """wait 动作的数值旋钮在 parse 期点名（与 assert 形态判据同口径：
    坏形态绝不进执行面猜语义）。"""
    if "timeout_ms" in obj:
        _validate_wait_int("timeout_ms", obj["timeout_ms"], allow_zero=False)
    if "ms" in obj:
        _validate_wait_int("ms", obj["ms"], allow_zero=False)
    if "min_rows" in obj:
        _validate_wait_int("min_rows", obj["min_rows"], allow_zero=True)


def _validate_assert(obj: dict) -> None:
    """assert 的形态判据（parse 期点名，坏形态绝不进执行面猜语义）。"""
    a_kind = obj.get("kind")
    if a_kind not in ASSERT_KINDS:
        raise ActionRejected(f"assert 的 kind 必须是 {ASSERT_KINDS} 之一，实为 {a_kind!r}")
    if a_kind == "visible":
        if not ((obj.get("role") and obj.get("name")) or obj.get("css")):
            raise ActionRejected("assert visible 需要 role+name 或 css")
    elif a_kind == "text":
        if not (isinstance(obj.get("contains"), str) and obj["contains"].strip()):
            raise ActionRejected("assert text 需要非空 contains")
    elif a_kind == "api":
        status, path = obj.get("status"), obj.get("json_path")
        if status is None and not (isinstance(path, str) and path.strip()):
            raise ActionRejected("assert api 需要 status 或 json_path 之一")
        if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
            raise ActionRejected(f"assert api 的 status 必须是整数，实为 {status!r}")
        if path is not None and not (isinstance(path, str) and path.strip()):
            raise ActionRejected("assert api 的 json_path 必须是非空字符串")
        if path is not None and not (obj.get("type") or obj.get("equals") is not None):
            raise ActionRejected("assert api 的 json_path 需要配 type 或 equals")
    else:  # compare
        key, la, lb = obj.get("key"), obj.get("locator_a"), obj.get("locator_b")
        if not (key or la):
            raise ActionRejected("assert compare 需要 key（比对已记录值）或 locator_a（双位置比对）")
        if key and not obj.get("locator"):
            raise ActionRejected("assert compare 带 key 时需要 locator 重读当前值")
        if la and not lb:
            raise ActionRejected("assert compare 给了 locator_a 必须同时给 locator_b")
        op = obj.get("op", "neq")
        if op not in ("eq", "neq"):
            raise ActionRejected(f"assert compare 的 op 必须是 eq/neq，实为 {op!r}")
        read = obj.get("read", "count")
        if read not in ("count", "text"):
            raise ActionRejected(f"assert compare 的 read 必须是 count/text，实为 {read!r}")


def _risk_of(action: dict, *, production: bool) -> RiskLevel:
    """把动作映射成 `classify_risk` 的输入——判据本身归 agent（#92 不复制）。

    交互动作（click/fill/press）按「可能写」交给 `classify_risk`：名字带
    save/submit/confirm/... → MEDIUM（默认被审批门拦），否则 LOW（可自动）。
    """
    kind = action["action"]
    read_only = kind in _READONLY_KINDS
    name = str(action.get("name") or action.get("url") or kind)
    meta = TaskMetadata(domain="explore", intent=str(action.get("note") or kind),
                        read_only=read_only)
    return classify_risk(_ActionTask(name, meta), production=production)


def _observe(page) -> dict:
    """页面观察：aria snapshot + 候选 + URL。

    `url` 过 `sanitize_url`（红线 #2）——这个值**出境**：`_build_messages` 把它拼进
    user 消息发给 LLM（真实部署是第三方/中转端点）。真实 SIT 实测第一轮 checkpoint 的
    URL 带 SSO 票据 `?ticket=ST-…`，不过刀就是**把凭据发到外部服务**（比落盘更严重，
    无法回收）。净化是逐键的，LLM 需要的路由/页面坐标照常保留。
    """
    try:
        snapshot = page.aria_snapshot()
    except Exception:
        snapshot = ""
    return {"url": sanitize_url(page.url), "snapshot": snapshot,
            "candidates": extract_candidates(snapshot)}


def _wire_events(page, ctx: ExploreContext) -> None:
    """注册请求/响应观察事件（**只读**取证面：读页面已发生的事件，不发任何新请求）。

    handler 抛错绝不顶循环（取证是尽力而为的旁路面——与 session 取证同纪律）；
    FakePage/不支持事件 API 的页面：证据面降级，循环行为不变。
    """
    if not hasattr(page, "on"):
        return
    try:
        page.on("request", lambda req: _on_request(ctx, req))
        page.on("response", lambda resp: _on_response(ctx, resp))
    except Exception as exc:  # 证据挂载失败不终止探索，但**必须留痕**——否则跑完才发现「一条
        # 请求证据都没有」而日志里看不出原因（旧实现 pass，纯静默）
        print(f"WARN: 探索证据挂载失败（后续步骤将没有请求/响应快照）："
              f"{type(exc).__name__}: {sanitize_text(exc)}", file=sys.stderr)


def _on_request(ctx: ExploreContext, req) -> None:
    """请求事件 → 证据（method/url/post_data 全过净化刀；post_data 截断）。"""
    try:
        post = req.post_data
        if isinstance(post, bytes):
            post = post.decode("utf-8", "replace")
        ctx.record_request(str(getattr(req, "method", "")),
                           sanitize_url(str(req.url)),
                           sanitize_text(str(post or ""))[:_MAX_POST_DATA_CHARS])
    except Exception:
        pass  # 取证尽力而为：坏事件对象不顶循环


def _on_response(ctx: ExploreContext, resp) -> None:
    """响应事件 → 证据（url/status/content_type/body；body 只对 JSON/文本形态读，截断）。"""
    try:
        url = sanitize_url(str(resp.url))
        status = resp.status
        try:
            ctype = str(resp.headers().get("content-type", ""))
        except Exception:
            ctype = ""
        body = ""
        if "json" in ctype.lower() or not ctype:
            try:
                body = resp.text()
            except Exception:
                body = ""   # 无体/导航响应：状态码本身已是有效证据
        ctx.record_response(url, status, ctype,
                            sanitize_text(body)[:_MAX_RESPONSE_BODY_CHARS])
    except Exception:
        pass  # 取证尽力而为：坏响应对象不顶循环


_SYSTEM_PROMPT = (
    "你是在**非生产**环境探索 UI 的自动化 Agent。"
    "只许从给定候选元素里选下一步；只许输出**单行 JSON**，不要任何解释文字。"
    '允许的动作：{"action":"click|fill|press|goto|assert|wait|record|done",'
    '"role":...,"name":...,"value":...,"url":...,"ms":...}。'
    "判据动作 assert 的 kind 四选一：\n"
    '- {"action":"assert","kind":"visible","role":...,"name":...}（元素存在且可见），'
    '或 {"action":"assert","kind":"visible","css":"..."}\n'
    '- {"action":"assert","kind":"text","contains":"..."}（页面文案），'
    '或 {"action":"assert","kind":"text","role":...,"name":...,"contains":"..."}（元素文案）\n'
    '- {"action":"assert","kind":"api","status":200}（最近接口响应状态码）；'
    '{"action":"assert","kind":"api","json_path":"data.code","equals":0}（接口结构）；'
    '{"action":"assert","kind":"api","json_path":"data.list","type":"array"}；'
    '可选 "url_contains":"/api/" 指定响应\n'
    '- {"action":"assert","kind":"compare","key":"total","locator":"css选择器","op":"neq"}'
    '（改前值 vs 改后值：先 record 存 key，再重读当前值比对）；'
    '双位置比对 {"action":"assert","kind":"compare","locator_a":"...","locator_b":"...","op":"eq"}\n'
    "跨模块值比对的改前值先存：{\"action\":\"record\",\"key\":\"total\",\"value\":\"3\"}。"
    "遇到保存/提交/确认/删除类按钮（Save/Submit/Confirm/Delete 等），"
    '不要点击，改输出 {"action":"record","note":"需要人工确认写操作"}。'
    "探索完成时输出 {\"action\":\"done\"}。"
)

_SUMMARY_SYSTEM_PROMPT = (
    "你是测试经验提炼器。根据给定失败证据，输出**单行 JSON**（不要解释文字）："
    '{"trigger":"...","symptom":"...","root_cause":"...","remedy":"..."}。'
    "trigger=一句话触发条件；symptom=观察到的现象；root_cause=最可能根因；"
    "remedy=可执行修复/规避。四字段都必须非空字符串，不得编造证据里没有的事实。"
)


def _build_messages(goal: str, obs: dict, history: list) -> list:
    recent = history[-6:]
    user = (f"目标：{goal}\n当前 URL：{obs['url']}\n"
            f"候选元素：{json.dumps(obs['candidates'], ensure_ascii=False)}\n"
            f"最近步骤：{json.dumps(recent, ensure_ascii=False)}")
    return [{"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user}]


def _locator(page, action: dict):
    """click/fill 的 locator：委托 `locators.resolve`（与组件库/产物同一把定位判据）。

    role 的 `name` 走精确匹配（`locators.resolve` 判据）——候选的 name 就是无障碍名原文，
    子串匹配会点错「保存并提交」这类同前缀按钮；点错比找不到更坏（找不到可见，点错静默）。
    """
    role, name = action.get("role"), action.get("name")
    if role and name:
        return locators.resolve(page, {"role": role, "name": name})
    raise ActionRejected("click/fill 必须带 role+name")


def _spec_of(action: dict):
    """assert visible/compare 的定位描述（role+name 或 css）——坏形态 parse 期已拒。"""
    if action.get("role") and action.get("name"):
        return {"role": action["role"], "name": action["name"]}
    return action["css"]


def _read_locator_value(page, spec, read: str):
    """按定位描述重读当前值（count=匹配元素数 / text=首个元素文本）。"""
    loc = locators.resolve(page, spec)
    if read == "text":
        if loc.count() == 0:
            raise AssertionError(
                f"断言失败（值比对）：定位 {locators.canonical(spec)} 没有匹配元素")
        return loc.first.inner_text().strip()
    return loc.count()


def _values_equal(a, b) -> bool:
    """值比对的归一判据：同值直接真；数值形（"12" vs 12）按数值比；其余按字符串比。"""
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _type_matches(value, expected: str) -> bool:
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False   # parse 期已限枚举，落不到这里


def _api_target(ctx: ExploreContext, url_contains):
    """api 判据的目标响应：给了 url_contains 找最近一条匹配，否则最近一条。"""
    if url_contains:
        for row in reversed(ctx.responses):
            if url_contains in str(row.get("url", "")):
                return row
        return None
    return ctx.responses[-1] if ctx.responses else None


def _assert_visible(page, action: dict) -> dict:
    spec = _spec_of(action)
    where = locators.canonical(spec)
    loc = locators.resolve(page, spec)
    count = loc.count()
    if not (count and loc.first.is_visible()):
        raise AssertionError(f"断言失败（元素可见）：期望 {where} 可见，实际 count={count}")
    return {"kind": "visible", "locator": where, "count": count}


def _assert_text(page, action: dict, ctx: ExploreContext) -> dict:
    contains = action["contains"]
    role, name = action.get("role"), action.get("name")
    if role and name:
        spec = {"role": role, "name": name}
        loc = locators.resolve(page, spec)
        if loc.count() == 0:
            raise AssertionError(
                f"断言失败（文案匹配）：元素 {locators.canonical(spec)} 不存在")
        text = loc.first.inner_text()
        where = f"元素 {locators.canonical(spec)} 文本"
    else:
        try:
            text = page.aria_snapshot()
        except Exception:
            text = ctx.snapshot   # aria 快照失败：用最近观察的快照（证据降级，不猜）
        where = "页面快照"
    if contains not in text:
        raise AssertionError(f"断言失败（文案匹配）：{where} 不含 {contains!r}")
    return {"kind": "text", "where": where, "contains": contains}


def _assert_api(ctx: ExploreContext, action: dict) -> dict:
    target = _api_target(ctx, action.get("url_contains"))
    if target is None:
        raise AssertionError("断言失败（接口响应）：本次会话没有观察到任何响应")
    detail = {"kind": "api", "url": target["url"], "status": target["status"]}
    if "status" in action:
        if target["status"] != action["status"]:
            raise AssertionError(
                f"断言失败（接口状态码）：{target['url']} 期望 HTTP {action['status']}，"
                f"实际 {target['status']}")
        detail["status_ok"] = True
    if action.get("json_path"):
        body = target.get("body") or ""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise AssertionError(
                f"断言失败（接口结构）：{target['url']} 响应体不是合法 JSON：{body[:120]!r}")
        # 路径打点走 tasks._dig 单一真相（坏路径当场点名整条路径，不复制第二份判据）
        value = _dig(data, action["json_path"])
        detail["json_path"] = action["json_path"]
        if "type" in action:
            if not _type_matches(value, action["type"]):
                raise AssertionError(
                    f"断言失败（接口结构）：{action['json_path']} 期望类型 {action['type']}，"
                    f"实际 {type(value).__name__}")
            detail["type"] = action["type"]
        if "equals" in action:
            if not _values_equal(value, action["equals"]):
                raise AssertionError(
                    f"断言失败（接口结构）：{action['json_path']} 期望 {action['equals']!r}，"
                    f"实际 {value!r}")
            detail["equals"] = action["equals"]
    return detail


def _assert_compare(page, action: dict, ctx: ExploreContext) -> dict:
    read, op = action.get("read", "count"), action.get("op", "neq")
    if action.get("key"):
        if action["key"] not in ctx.values:
            raise AssertionError(
                f"断言失败（值比对）：key {action['key']!r} 未记录（先 record 存改前值）")
        recorded = ctx.values[action["key"]]
        spec = action["locator"]
        current = _read_locator_value(page, spec, read)
        where = locators.canonical(spec)
        same = _values_equal(recorded, current)
        if (op == "eq") != same:
            raise AssertionError(
                f"断言失败（值比对）：key={action['key']!r} 记录值 {recorded!r} 与当前重读 "
                f"{current!r}（{where}）期望{'相等' if op == 'eq' else '不相等'}")
        return {"kind": "compare", "key": action["key"], "recorded": str(recorded),
                "current": str(current), "where": where, "op": op}
    a_spec, b_spec = action["locator_a"], action["locator_b"]
    a = _read_locator_value(page, a_spec, read)
    b = _read_locator_value(page, b_spec, read)
    same = _values_equal(a, b)
    if (op == "eq") != same:
        raise AssertionError(
            f"断言失败（值比对）：{locators.canonical(a_spec)}={a!r} 与 "
            f"{locators.canonical(b_spec)}={b!r} 期望{'相等' if op == 'eq' else '不相等'}")
    return {"kind": "compare", "a": str(a), "b": str(b), "op": op}


def _execute_assert(page, action: dict, ctx: ExploreContext) -> dict:
    """assert 判据执行：通过返回 detail，失败抛 `AssertionError`（归类走 classify_failure）。"""
    kind = action["kind"]
    if kind == "visible":
        return _assert_visible(page, action)
    if kind == "text":
        return _assert_text(page, action, ctx)
    if kind == "api":
        return _assert_api(ctx, action)
    return _assert_compare(page, action, ctx)


def _execute(page, action: dict, ctx: ExploreContext) -> dict:
    """只执行白名单动作（构造期已校验）；写操作到不了这里（审批门先拦）。

    通过返回可落盘的 detail（进 runs.jsonl 步骤记录）；判据失败抛 `AssertionError`。
    """
    kind = action["action"]
    if kind == "click":
        spec = {"role": action["role"], "name": action["name"]}
        _locator(page, action).click(timeout=15000)
        return {"action": kind, "locator": locators.canonical(spec)}
    if kind == "fill":
        spec = {"role": action["role"], "name": action["name"]}
        _locator(page, action).fill(str(action.get("value") or ""))
        return {"action": kind, "locator": locators.canonical(spec)}
    if kind == "press":
        page.keyboard.press(str(action.get("key") or "Enter"))
        return {"action": kind, "key": str(action.get("key") or "Enter")}
    if kind == "goto":
        page.goto(str(action["url"]), wait_until="domcontentloaded", timeout=60000)
        return {"action": kind, "url": sanitize_url(str(action["url"]))}
    if kind == "wait":
        # pf-3：语义等待优先——按 action 声明的 `target` 走 waits.py 的单一判据
        # （不再无脑睡 1000ms）；缺省无 target 时回落旧的固定 ms（LLM 未指明等什么时
        # 保留旧行为=逐字节不变，opt-in 边界）。
        #   target="mask_gone" → waits.wait_for_mask_gone（等 loading 遮罩消失）
        #   target="rows"      → waits.wait_for_rows（表格 vm.data 落定；min_rows 缺省 1）
        #   target="vue_field" → waits.wait_for_vue_field（Vue 字段就绪）
        #   target="url_change"→ waits.wait_for_url_change（导航完成）
        # ms 仅在回落路径生效；语义路径用 waits 各函数自身的 timeout_ms（缺省由它们定）。
        target = action.get("target")
        timeout_ms = int(action.get("timeout_ms") or 10000)
        if target == "mask_gone":
            from atlas import waits
            waits.wait_for_mask_gone(page, timeout_ms=timeout_ms,
                                     family=action.get("family"))
            return {"action": kind, "target": target, "timeout_ms": timeout_ms}
        if target == "rows":
            from atlas import waits
            waits.wait_for_rows(page, table_selector=str(action.get("table") or ".el-table"),
                                min_rows=int(action.get("min_rows", 1)),
                                timeout_ms=timeout_ms, family=action.get("family"))
            return {"action": kind, "target": target, "timeout_ms": timeout_ms,
                    "table": str(action.get("table") or ".el-table")}
        if target == "vue_field":
            from atlas import waits
            waits.wait_for_vue_field(page, str(action["vm_path"]), timeout_ms=timeout_ms)
            return {"action": kind, "target": target, "vm_path": str(action["vm_path"]),
                    "timeout_ms": timeout_ms}
        if target == "url_change":
            from atlas import waits
            waits.wait_for_url_change(page, timeout_ms=timeout_ms)
            return {"action": kind, "target": target, "timeout_ms": timeout_ms}
        if target is not None:
            raise ActionRejected(
                f"wait.target 只支持 mask_gone|rows|vue_field|url_change，收到 {target!r}")
        ms = int(action.get("ms") or 1000)
        page.wait_for_timeout(ms)
        return {"action": kind, "ms": ms}
    if kind == "record":
        key = action.get("key")
        if key is not None:
            ctx.values[str(key)] = action["value"]   # 改前值：供 compare 跨步骤比对
        return {"action": kind, "note": str(action.get("note") or "")}
    if kind == "assert":
        return _execute_assert(page, action, ctx)
    raise ActionRejected(f"不可执行的动作 {kind!r}")   # done 不会走到这里（循环提前 break）


def _step_name(action: dict) -> str:
    """runs.jsonl 步骤名：`动作:对象`（URL 过净化刀——步骤名落盘，凭据不落盘）。"""
    kind = action["action"]
    where = action.get("name") or action.get("url") or action.get("kind") or ""
    if kind == "goto":
        where = sanitize_url(str(where))
    return f"{kind}:{where}".rstrip(":")


def _failure_evidence(ctx: ExploreContext, action: dict, exc=None) -> dict:
    """失败步骤的证据面：请求实参（playbook §1「没反应类结论必须附请求实参」）+
    响应快照 + 页面快照 + 已记录值。全部已净化、已截断——红线 #2 凭据不落盘。
    """
    evidence = {
        "requests": [dict(row) for row in ctx.requests[-5:]],
        "responses": [dict(row) for row in ctx.responses[-5:]],
        "snapshot": ctx.snapshot[:_MAX_SNAPSHOT_IN_EVIDENCE],
        "values": {str(k): str(v) for k, v in ctx.values.items()},
    }
    if exc is not None:
        evidence["error"] = exception_text(exc)   # 净化后的单一真相形态
        if action.get("action") == "assert":
            evidence["assert_kind"] = action.get("kind")
    return evidence


def _parse_summary(text: str) -> dict:
    """LLM 失败摘要 → 四字段 dict；坏形态点名 `SummaryRejected`（绝不拿坏摘要冒充证据）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw[:4].lower() == "json":
            raw = raw[4:]
        raw = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SummaryRejected(f"摘要不是合法 JSON：{raw[:120]!r}") from exc
    if not isinstance(obj, dict):
        raise SummaryRejected(f"摘要必须是 JSON 对象，收到 {type(obj).__name__}")
    missing = [k for k in ("trigger", "symptom", "root_cause", "remedy")
               if not (isinstance(obj.get(k), str) and obj[k].strip())]
    if missing:
        raise SummaryRejected(f"摘要缺字段或为空：{missing}（需 trigger/symptom/root_cause/remedy）")
    extra = sorted(k for k in obj if k not in ("trigger", "symptom", "root_cause", "remedy"))
    if extra:
        raise SummaryRejected(f"摘要有未知键 {extra}（只认四字段）")
    return {k: sanitize_text(obj[k].strip())[:_MAX_SUMMARY_FIELD_CHARS]
            for k in ("trigger", "symptom", "root_cause", "remedy")}


def _llm_summary(llm, exc, action: dict, ctx: ExploreContext) -> tuple[dict | None, str]:
    """失败 → LLM 摘要（从证据出）。返回 (摘要或 None, 跳过原因或空串)。

    LLM 调用失败/摘要形态坏都**点名记录**在跳过原因里（绝不静默吞掉），
    失败本身照常进 runs.jsonl——提炼是富化，不是失败的载体。
    """
    prompt = json.dumps(_failure_evidence(ctx, action, exc), ensure_ascii=False)[:6000]
    try:
        text = llm.complete([{"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                             {"role": "user", "content": f"失败证据：{prompt}"}])
    except LLMError as err:
        return None, f"坑点提炼跳过（LLM 摘要调用失败：{exception_brief(err)}）"
    try:
        return _parse_summary(text), ""
    except SummaryRejected as err:
        return None, f"坑点提炼跳过（LLM 摘要形态坏：{err}）"


def _extract_pitfall(llm, knowledge, journal, exc, action: dict, ctx: ExploreContext,
                     goal: str, report: AutoReport) -> tuple[dict | None, str]:
    """失败 → pending_review 坑点草稿（未命中已审核坑点才提炼）。

    - 命中 approved 坑点（`match_pitfall(status="approved")` 自愈准入同语义）：不重复提炼；
    - 未命中：LLM 从证据出四字段 → `journal.draft_from_text` 造草稿，evidence 带
      `review.verification_problems` 四元组（command/exit_code/assertions/verdict=fail）——
      机器产证据正常过闸，但**只进塔不写知识**（红线 #9/#10）。
    """
    if knowledge is not None:
        matched = knowledge.match_pitfall(exc, status="approved")
        if matched:
            return None, f"命中已审核坑点 {matched.get('id')}，不重复提炼"
    summary, skip = _llm_summary(llm, exc, action, ctx)
    if summary is None:
        return None, skip
    task = f"{action.get('action')}:{action.get('name') or action.get('kind') or ''}".strip(":")
    row = journal.draft_from_text(
        task=task,
        text=json.dumps(summary, ensure_ascii=False),
        failure_class=classify_failure(exc).value,
        exception_type=type(exc).__name__,
        evidence={"summary": summary,
                  "verification": {"command": f"atlas explore --auto --goal {goal}",
                                   "exit_code": 1, "assertions": report.asserts,
                                   "verdict": "fail"},
                  **_failure_evidence(ctx, action, exc)})
    journal.append_draft(row)
    return row, ""


def _run_status(crashed, report: AutoReport) -> str:
    """run 终态：正常收尾看失败数；崩溃路径控制流→interrupted，其余→failed（不洗绿）。"""
    if crashed is not None:
        return "interrupted" if isinstance(crashed, CONTROL_FLOW) else "failed"
    return "failed" if report.failures else "passed"


def _run_reason(crashed, report: AutoReport) -> str:
    if crashed is not None:
        return exception_brief(crashed)[:200]
    return (f"stopped={report.stopped_reason} failures={report.failures} "
            f"rejected={report.rejected}")


def explore_auto(config, goal: str, *, llm, page, trace: Path, journal,
                 shots: Path | None = None, max_steps: int = 30,
                 max_seconds: float = 600.0, max_failures: int = 3,
                 approved: bool = False,
                 max_auto_risk: RiskLevel = RiskLevel.LOW,
                 agent=None, clock=time.monotonic, knowledge=None) -> AutoReport:
    """目标驱动自主探索：目标进去，checkpoint + `pending_review` 草稿出来（全程无人）。

    终止条件（任一）：LLM 返回 `done` / 步数上限 / 墙钟上限 / 连续失败上限 / 审批拦截。
    生产环境直接 `PermissionError`（与 `explore.start` 同款闸与消息）。

    wave3：全程写 runs.jsonl（`config.reports_dir / "runs.jsonl"`，mode=explore-auto）——
    每个执行步一条 step 记录，失败步带证据（请求实参/响应/快照）；`knowledge` 可选注入
    （命中 approved 坑点的失败不重复提炼）。
    """
    if config.is_production:
        raise PermissionError(PRODUCTION_REFUSED)
    # 上限参数的值域闸（2026-11-24 实测）：`--max-steps -1` 会让 `while index < -1`
    # **一次都不跑**，report 却给出 `stopped_reason="max_steps"`——读起来像「步数用尽」，
    # 实际是**什么都没做**；`--max-seconds -10` 同理（`clock()-t0 > -10` 恒真）。
    # 负上限不是「更严格」，是**把功能静默关掉**，与 `--fail-under -1` 同一族。
    # 0 也拒：explore --auto 的目标就是「走几步」，0 步等于空转，同样只会产出误导性结论。
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
        raise ValueError(
            f"--max-steps 必须是正整数（收到 {max_steps!r}）——0 或负数会让循环一次都不跑，"
            f"却仍报 stopped_reason=max_steps（看起来像「步数用尽」，实际什么都没做）")
    if not isinstance(max_seconds, (int, float)) or isinstance(max_seconds, bool) \
            or not max_seconds > 0:
        raise ValueError(
            f"--max-seconds 必须是正数（收到 {max_seconds!r}）——0 或负数会让墙钟闸恒真、"
            f"循环一次都不跑，却仍报 stopped_reason=max_seconds")
    trace = Path(trace)
    trace.parent.mkdir(parents=True, exist_ok=True)
    shots = Path(shots) if shots else (config.artifacts_dir / "exploration" / trace.stem)
    shots.mkdir(parents=True, exist_ok=True)
    report = AutoReport(trace=trace)
    recorder = RunRecorder(goal, getattr(config, "env", ""),
                           Path(config.reports_dir) / "runs.jsonl", "explore-auto")
    ctx = ExploreContext()
    _wire_events(page, ctx)
    t0 = clock()
    index = 0
    crashed: BaseException | None = None
    try:
        while index < max_steps:
            if clock() - t0 > max_seconds:
                report.stopped_reason = "max_seconds"
                break
            obs = _observe(page)
            ctx.snapshot = obs["snapshot"]
            text = llm.complete(_build_messages(goal, obs, report.steps))
            try:
                action = parse_action(text)
            except ActionRejected as exc:
                index += 1
                report.rejected += 1
                report.failures += 1
                report.steps.append({"index": index, "action": "invalid",
                                     "ok": False, "note": str(exc)})
                _record_decision(agent, goal, "invalid", RiskLevel.READ_ONLY, False, str(exc))
                if report.failures >= max_failures:
                    report.stopped_reason = "max_failures"
                    break
                continue
            kind = action["action"]
            if kind == "done":
                report.stopped_reason = "done"
                break
            risk = _risk_of(action, production=config.is_production)
            if risk.order > max_auto_risk.order and not approved:
                index += 1
                report.steps.append({"index": index, "action": kind, "ok": False,
                                     "note": f"审批拦截 risk={risk.value}（{action.get('name')}）"})
                _record_decision(agent, goal, kind, risk, False, "审批拦截")
                report.stopped_reason = "approval_required"
                break
            if kind == "assert":
                report.asserts += 1     # 判据评估计数（pass+fail 都算，草稿三元组用）
            index += 1
            try:
                with recorder.step(_step_name(action), op=kind) as item:
                    try:
                        detail = _execute(page, action, ctx)
                    except Exception as exc:
                        # 失败步证据落在同一 step 记录里（请求实参/响应/快照）——
                        # 「没反应类结论必须附请求实参」（playbook §1）的可执行形态。
                        item["evidence"] = _failure_evidence(ctx, action, exc)
                        item["error"] = exception_text(exc)
                        raise
                    item["detail"] = detail
            except ActionRejected as exc:
                report.rejected += 1
                report.failures += 1
                report.steps.append({"index": index, "action": kind, "ok": False, "note": str(exc)})
                if report.failures >= max_failures:
                    report.stopped_reason = "max_failures"
                    break
                continue
            except Exception as exc:
                report.failures += 1
                draft, skip = _extract_pitfall(llm, knowledge, journal, exc, action, ctx,
                                               goal, report)
                if draft is not None:
                    report.pitfall_drafts += 1
                note = f"{type(exc).__name__}: {exc}"
                if skip:
                    note += f"；{skip}"
                report.steps.append({"index": index, "action": kind, "ok": False, "note": note})
                if report.failures >= max_failures:
                    report.stopped_reason = "max_failures"
                    break
                continue
            _record_checkpoint(page, trace, shots, index)
            report.steps.append({"index": index, "action": kind, "ok": True,
                                 "name": action.get("name") or action.get("url") or ""})
            _record_decision(agent, goal, kind, risk, True, "auto")
        else:
            report.stopped_reason = "max_steps"
    except BaseException as exc:
        crashed = exc
        raise
    finally:
        # 崩溃路径也落盘（interrupted/failed + 原因）：半途而废的 run 也是审计事实。
        recorder.close(_run_status(crashed, report), reason=_run_reason(crashed, report))
    # 零 checkpoint 的终态（审批拦截 / 连续失败 / 一上来就 done）不会建 trace 文件，
    # 而 compile_trace 是读文件编译——先落空档，保证「没有证据」也是可编译的诚实结论。
    if not trace.exists():
        trace.write_text("", encoding="utf-8")
    report.drafts = compile_trace(trace, journal)
    return report


def _record_decision(agent, goal: str, action: str, risk: RiskLevel,
                     approved: bool, reason: str) -> None:
    """决策留痕：有 agent 就落它（单一留痕通道）；写失败不顶真因（与 agent.run 同纪律）。"""
    if agent is None:
        return
    try:
        agent._record(Decision(goal, [action], risk.value, approved, reason=reason,
                               executed=[action] if approved else []))
    except Exception as exc:  # 决策留痕失败不得顶掉动作结果，但**必须留痕**（旧实现 pass：
        # reports/agent_decisions.jsonl 少一行而终端一个字没有，事后无法解释「为什么走了这条」）
        print(f"WARN: 决策留痕失败（agent_decisions.jsonl 少了这一条）："
              f"{type(exc).__name__}: {sanitize_text(exc)}", file=sys.stderr)

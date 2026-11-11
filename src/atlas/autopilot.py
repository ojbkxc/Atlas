"""gap12 W2：LLM 自主探索循环——把「脑」装进 Atlas（`docs/gaps/gap12-llm-autonomous-explore.md`）。

闭环：观察（aria snapshot → 候选元素）→ 决策（LLM 输出**白名单动作**）→ 护栏（生产闸 /
风险分级 / 审批）→ 执行 → 留痕 → 直到 done/上限 → 沉淀（trace → pending_review 草稿）。

复用单一来源，不留第二把尺：
- 观察/记录/沉淀 → `explore.extract_candidates` / `explore._record_checkpoint` / `explore.compile_trace`
- 风险分级 → `agent.classify_risk`（本模块只把动作映射成它的输入，不另写判据）
- 决策留痕 → `agent.Decision` + `AtlasAgent._record`（调用方注入 agent；缺席则不落盘但不静默）

安全红线（设计 §F）：LLM 只许输出白名单动作，**绝不能直接执行任意 JS/SQL/文件操作**；
写/提交类动作按 `classify_risk` 拦在审批门下（默认 `max_auto_risk=LOW`，即普通点击可自动、
Save/Submit/Confirm 类停）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .agent import Decision, RiskLevel, classify_risk
from .core import TaskMetadata
from .explore import PRODUCTION_REFUSED, _record_checkpoint, compile_trace, extract_candidates
from . import locators

# 白名单：LLM 只能从中选。任何白名单外的动作（尤其执行 JS/任意代码）一律拒绝。
ACTION_WHITELIST = ("click", "fill", "press", "goto", "assert", "wait", "record", "done")
# 无副作用的动作（记录/断言/等待/导航）：只读
_READONLY_KINDS = ("assert", "wait", "record", "goto", "done")


class ActionRejected(ValueError):
    """LLM 提议的动作不在白名单 / 形态坏 —— 拒绝执行并计数（绝不「尽力解释后照跑」）。"""


@dataclass
class _ActionTask:
    """`classify_risk` 的鸭子类型输入（它只读 `.name` / `.metadata`）。"""
    name: str
    metadata: TaskMetadata


@dataclass
class AutoReport:
    steps: list = field(default_factory=list)
    rejected: int = 0
    failures: int = 0
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
    return obj


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
    try:
        snapshot = page.aria_snapshot()
    except Exception:
        snapshot = ""
    return {"url": page.url, "snapshot": snapshot,
            "candidates": extract_candidates(snapshot)}


_SYSTEM_PROMPT = (
    "你是在**非生产**环境探索 UI 的自动化 Agent。"
    "只许从给定候选元素里选下一步；只许输出**单行 JSON**，不要任何解释文字。"
    '允许的动作：{"action":"click|fill|press|goto|assert|wait|record|done",'
    '"role":...,"name":...,"value":...,"url":...,"ms":...}。'
    "遇到保存/提交/确认/删除类按钮（Save/Submit/Confirm/Delete 等），"
    '不要点击，改输出 {"action":"assert","note":"需要人工确认写操作"}。'
    "探索完成时输出 {\"action\":\"done\"}。"
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


def _execute(page, action: dict) -> None:
    """只执行白名单动作（构造期已校验）；写操作到不了这里（审批门先拦）。"""
    kind = action["action"]
    if kind == "click":
        _locator(page, action).click(timeout=15000)
    elif kind == "fill":
        _locator(page, action).fill(str(action.get("value") or ""))
    elif kind == "press":
        page.keyboard.press(str(action.get("key") or "Enter"))
    elif kind == "goto":
        page.goto(str(action["url"]), wait_until="domcontentloaded", timeout=60000)
    elif kind == "wait":
        page.wait_for_timeout(int(action.get("ms") or 1000))
    elif kind in ("assert", "record"):
        pass          # 语义留痕动作：无副作用，证据由 checkpoint 承载
    else:             # done 不会走到这里（循环提前 break）
        raise ActionRejected(f"不可执行的动作 {kind!r}")


def explore_auto(config, goal: str, *, llm, page, trace: Path, journal,
                 shots: Path | None = None, max_steps: int = 30,
                 max_seconds: float = 600.0, max_failures: int = 3,
                 approved: bool = False,
                 max_auto_risk: RiskLevel = RiskLevel.LOW,
                 agent=None, clock=time.monotonic) -> AutoReport:
    """目标驱动自主探索：目标进去，checkpoint + `pending_review` 草稿出来（全程无人）。

    终止条件（任一）：LLM 返回 `done` / 步数上限 / 墙钟上限 / 连续失败上限 / 审批拦截。
    生产环境直接 `PermissionError`（与 `explore.start` 同款闸与消息）。
    """
    if config.is_production:
        raise PermissionError(PRODUCTION_REFUSED)
    trace = Path(trace)
    trace.parent.mkdir(parents=True, exist_ok=True)
    shots = Path(shots) if shots else (config.artifacts_dir / "exploration" / trace.stem)
    shots.mkdir(parents=True, exist_ok=True)
    report = AutoReport(trace=trace)
    t0 = clock()
    index = 0
    while index < max_steps:
        if clock() - t0 > max_seconds:
            report.stopped_reason = "max_seconds"
            break
        obs = _observe(page)
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
        index += 1
        try:
            _execute(page, action)
        except ActionRejected as exc:
            report.rejected += 1
            report.failures += 1
            report.steps.append({"index": index, "action": kind, "ok": False, "note": str(exc)})
            if report.failures >= max_failures:
                report.stopped_reason = "max_failures"
                break
            continue
        except Exception as exc:      # 元素不存在/超时等：计一次失败，继续或达上限停
            report.failures += 1
            report.steps.append({"index": index, "action": kind, "ok": False,
                                 "note": f"{type(exc).__name__}: {exc}"})
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
    except Exception:
        pass

"""C9「waiting ≠ failed」显态（design_c9 §7 测试清单 15 组，下限 ≥22）。

覆盖新终态的全链：产生（Task 抛 AwaitingHuman）→ 分类（failures 单点）→ 落盘
（runs.jsonl step/flow 两级 + 顶层 reason）→ 聚合（summary status/fail_classes +
attention 新桶）→ 展示（Allure skipped+label+message 双留、rc3 退出码、调度 outcome）。

红线 #7 的四道反机制在这里逐条成测（§4）：关键词永不产 waiting、类型短路次序、
label+message 双留不可读成洗绿、历史数据不回溯。

离线纪律：不碰浏览器、不碰 D:\\jmx、不烧真实会话；fake Task 与 fake subprocess；
CLI 用临时 root 与假 DemoRuntime。X4 返回串纪律：新 stderr 整行断言 cp936 可编。
"""
from __future__ import annotations
import ast
import contextlib
import io
import json
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas import Actor, FunctionalTask, TaskMetadata, waits
from atlas.agent import ApprovalRequired
from atlas.allure_analysis import DEFAULT_BUCKETS, analyze_allure, parse_allure_result
from atlas.allure_report import _status_from_run, _step_status, emit_allure
from atlas.cli import main
from atlas.config import AtlasConfig
from atlas.dsl import FlowCompiler, TaskRegistry
from atlas.engine import ExecutionEngine
from atlas.experience import ExperienceJournal
from atlas.failures import (CONTROL_FLOW, AwaitingHuman, DeliveryUncertain, EnvBlocked,
                            FailureClass, OPERATIONAL, classify_failure, classify_text,
                            is_operational)
from atlas.recorder import RunRecorder
from atlas.reporting import run_attention, summarize
from atlas.scheduling import decision, dispatch, lease
from atlas.scheduling import state as st

UTC = timezone.utc
WAIT_REASON = "UAT 登录触发手机验证码，需人工完成"
THREE_THIRTY = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)     # 每小时表达式：due=03:00，age=30min


def _waiting_task(name: str = "wait_human", calls: dict | None = None) -> FunctionalTask:
    """唯一的生产者形态：业务 Task 在 perform 内显式 raise AwaitingHuman（§2 白名单第 1 条）。"""
    def _perform(actor):
        if calls is not None:
            calls["n"] = calls.get("n", 0) + 1
        raise AwaitingHuman(WAIT_REASON)
    return FunctionalTask(TaskMetadata("demo", name), _perform, name)


def _boom(exc: BaseException, name: str = "boom") -> FunctionalTask:
    return FunctionalTask(TaskMetadata("demo", name),
                          lambda actor: (_ for _ in ()).throw(exc), name)


# ── §7.1 枚举扩员：追加不改序 ─────────────────────────────────────────────

def test_failure_class_has_nine_values_and_legacy_order_is_untouched():
    """既有 8 类的**取值与次序**零变动：枚举重排会掀翻已落盘 runs.jsonl 的口径
    （读侧按键取值不按下标，但外部看板按值分流），waiting 只能是追加的第 9 个。"""
    values = [cls.value for cls in FailureClass]
    assert values[:8] == ["env_blocked", "session", "interaction", "contract",
                          "data", "assertion", "interrupted", "unknown"]
    assert values[-1] == "waiting" and FailureClass.WAITING.value == "waiting"
    assert len(values) == 9


# ── §7.2 家族判据：恰三员 + 不猜 ──────────────────────────────────────────

def test_operational_family_is_exactly_three_members():
    assert set(OPERATIONAL) == {"env_blocked", "interrupted", "waiting"}


@pytest.mark.parametrize("value,expected", [
    ("env_blocked", True), ("interrupted", True), ("waiting", True),
    ("failed", False), ("passed", False), ("timeout", False),   # supervisor 新终态**不入家族**
    ("", False), ("WAITING", False), ("Waiting", False),
    (None, False), (0, False), (True, False), ([], False), ({"waiting": 1}, False),
])
def test_is_operational_table_never_guesses(value, expected):
    """非 str/未知取值恒 False：把读不懂的状态默认成「非缺陷」就是洗绿（#102 同族）。
    大小写敏感也是判据的一部分——runs.jsonl 的取值形态由引擎写，外部混进来的变体不该认。"""
    assert is_operational(value) is expected


def test_is_operational_accepts_enum_members_not_only_raw_strings():
    """FailureClass 成员也必须判对：str 混入枚举的 hash 口径跨 Python 版本不一致
    （3.11 按成员名、3.12+ 按值），裸 membership 在部分版本恒 False，
    「等人」就会在那台机器上被读成缺陷。"""
    assert is_operational(FailureClass.WAITING) is True
    assert is_operational(FailureClass.ENV_BLOCKED) is True
    assert is_operational(FailureClass.ASSERTION) is False


# ── §7.3 AwaitingHuman 必须点名在等什么 ──────────────────────────────────

@pytest.mark.parametrize("bad_reason", ["", "   ", "\n"])
def test_awaiting_human_requires_nonempty_reason(bad_reason):
    """裸一条「waiting」落盘=新型静默（#70/#86 同族）：事后看不出在等什么、该找谁。"""
    with pytest.raises(ValueError, match="非空原因"):
        AwaitingHuman(bad_reason)


def test_awaiting_human_message_is_retrievable_verbatim():
    exc = AwaitingHuman(WAIT_REASON)
    assert str(exc) == WAIT_REASON
    assert not isinstance(exc, CONTROL_FLOW) and not isinstance(exc, EnvBlocked)


# ── §7.4 短路次序（输入面）：类型说了算 ───────────────────────────────────

def test_classify_failure_maps_awaiting_human_by_type():
    assert classify_failure(AwaitingHuman(WAIT_REASON)) is FailureClass.WAITING


def test_control_flow_short_circuit_still_first():
    """KI/SystemExit/GeneratorExit 仍优先——人 Ctrl-C 打断的正是等人工那一步时，
    语义仍是 interrupted，不许被 waiting 顶掉（次序是 §2 钉死的）。"""
    for exc in (KeyboardInterrupt(), SystemExit(), GeneratorExit()):
        assert classify_failure(exc) is FailureClass.INTERRUPTED


def test_delivery_uncertain_still_interaction_after_waiting_branch():
    """两波短路共存（W5 的 C12 + W6 的 C9）：投递不确定保持 INTERACTION/failed 族，
    **不**因为 C9 在它前面插了一条就翻成 waiting——C12 的终态语义是「已发出、待核实」，
    借「等人」洗成非缺陷就是红线 #7（design_c12 §7 末行的对偶断言）。"""
    exc = DeliveryUncertain("请求已发出，投递结果未知")
    assert classify_failure(exc) is FailureClass.INTERACTION


def test_approval_required_is_not_waiting():
    """agent 审批门是 rc3 决策信号、不是 run 终态（§2 非触发清单）。既有
    `!= ENV_BLOCKED` 断言在 test_evidence_and_knowledge.py 原位不动，这里**追加**新面。"""
    assert classify_failure(ApprovalRequired("生产环境写操作需人工批准")) is not FailureClass.WAITING


def test_reason_wording_cannot_flip_the_class():
    """等人工的措辞里带 timeout 类词也不得翻成 interaction——判据只看类型。"""
    assert classify_failure(AwaitingHuman("等待人工审批，页面 timeout 未跳转")) is FailureClass.WAITING
    assert waits.is_recoverable(AwaitingHuman("等待人工审批")) is False


# ── §7.5 反制造表：外部文本永不产出 waiting（红线 #7 的最大入口） ─────────

@pytest.mark.parametrize("text", [
    "waiting for human approval", "等待人工处理", "approval required",
    "awaiting human intervention", "waiting", "需要人工审核",
])
def test_classify_text_never_invents_waiting(text):
    """_RULES/_DATA_ABSENT 一字不加（§2 输入侧封闭）：SCS Allure 结果、历史日志这类
    外部文本里出现「waiting」字样一律维持原归类，绝不被读成「等人所以不是缺陷」。"""
    assert classify_text(text) is not FailureClass.WAITING


def test_legacy_keyword_matrix_classes_are_unchanged():
    """喂真数据而非放宽：既有 8 类的关键词归因一例不许因为 C9 而翻类。"""
    assert classify_text("生产环境禁止建单") is FailureClass.ENV_BLOCKED
    assert classify_text("401 unauthorized") is FailureClass.SESSION
    assert classify_text("查询无结果") is FailureClass.DATA
    assert classify_text("contractviolation: required field") is FailureClass.CONTRACT
    assert classify_text("AssertionError: expected 5") is FailureClass.ASSERTION
    assert classify_text("timeout waiting for element") is FailureClass.INTERACTION
    assert classify_text("totally unrelated text") is FailureClass.UNKNOWN


# ── §7.6 recorder 继承面：分类链零改动 ───────────────────────────────────

def test_step_record_inherits_waiting_from_classify_single_source(tmp_path):
    """step 的 fail_class="waiting" 由 recorder.step 调 classify_failure 单点继承
    （#112/#126 防线自动覆盖：定性崩才回落 unknown，不假绿）。"""
    recorder = RunRecorder("c9-step", "sit", tmp_path / "runs.jsonl")
    with pytest.raises(AwaitingHuman):
        with recorder.step("wait_human", "ui"):
            raise AwaitingHuman(WAIT_REASON)
    recorder.close("waiting")
    step = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8"))["steps"][0]
    assert step["fail_class"] == "waiting" and step["ok"] is False


def test_recorder_has_no_waiting_string_literal():
    """AST 扫字面量（评审 §5 判定可维护）：recorder 里出现 "waiting" **字符串常量**就是
    第二份判据（#92）——取值只能来自 failures 一处。注释里提到这个词是允许的，
    所以扫的是常量而不是原文，否则文档纪律会反过来绑架判据。"""
    source = Path(__file__).resolve().parents[1] / "src" / "atlas" / "recorder.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    literals = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not [text for text in literals if "waiting" in text.lower()]


# ── §7.8 dsl 终态腿 + run 级 reason ──────────────────────────────────────

def _flow(steps: list[dict], tmp_path: Path, on_env_blocked: str | None = None) -> dict:
    flow = {"id": "c9-flow", "engine": AtlasConfig(tmp_path), "steps": steps}
    if on_env_blocked is not None:
        flow["on_env_blocked"] = on_env_blocked
    return flow


def _last_run(tmp_path: Path) -> dict:
    line = (tmp_path / "reports" / "runs.jsonl").read_text(encoding="utf-8").strip()
    return json.loads(line.splitlines()[-1])


def test_dsl_flow_terminal_status_is_waiting_with_reason(tmp_path):
    registry = TaskRegistry()
    registry.register("wait_human", lambda **kw: _waiting_task())
    with pytest.raises(AwaitingHuman):
        FlowCompiler(registry).run(_flow([{"task": "wait_human"}], tmp_path), Actor())
    record = _last_run(tmp_path)
    assert record["status"] == "waiting"
    assert record["reason"] == WAIT_REASON                      # 净化后原文（本条无凭据形态）
    assert record["steps"][0]["fail_class"] == "waiting"
    assert record["steps"][0]["ok"] is False


def test_dsl_reason_is_sanitized_and_capped(tmp_path):
    """reason 走 exception_brief（C4② 同一把刀）+ 截 200：run 记录不得带上字面凭据，
    超长报文也不许淹没审计行。"""
    token = "d" * 40
    registry = TaskRegistry()
    registry.register("wait_human", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "wait_human"),
        lambda actor: (_ for _ in ()).throw(
            AwaitingHuman(f"人工审批 isc-axios-sessionid={token} " + "长" * 400)),
        "wait_human"))
    with pytest.raises(AwaitingHuman):
        FlowCompiler(registry).run(_flow([{"task": "wait_human"}], tmp_path), Actor())
    reason = _last_run(tmp_path)["reason"]
    assert token not in reason                                  # 值被净化刀遮蔽
    assert "isc-axios-sessionid" in reason                      # 键名留着（可追责）
    assert len(reason) <= 200


@pytest.mark.parametrize("status", ["passed", "failed", "env_blocked", "interrupted"])
def test_close_writes_no_reason_key_for_other_terminals(tmp_path, status):
    """C6 version 参同款先例：缺省不写键=存量行零涟漪。这几类终态的 runs.jsonl 行
    键集合必须与 C9 之前一致（不含 reason）。"""
    path = tmp_path / "runs.jsonl"
    RunRecorder("shape", "sit", path).close(status)
    text = path.read_text(encoding="utf-8")
    assert '"reason"' not in text
    assert set(json.loads(text)) == {"run_id", "case", "env", "mode", "status",
                                     "started_at", "finished_at", "steps"}


def test_close_reason_empty_string_writes_nothing(tmp_path):
    """显式传空串与不传**同形态**：dsl 的非 waiting 分支正是靠这条不写键。"""
    path = tmp_path / "runs.jsonl"
    RunRecorder("shape", "sit", path).close("waiting", reason="")
    assert "reason" not in json.loads(path.read_text(encoding="utf-8"))


def test_dsl_env_blocked_writes_reason_passed_still_does_not(tmp_path):
    """flow 级 reason 口径（2026-11-06 更新：env_blocked 写侧已接通，原「不做本波」放行）。

    · env_blocked **必须**带 reason——批量巡检里几十条同名终态要靠它分辨「缺价格表」还是
      「缺字典值」，只留一个 env_blocked 字符串等于把根因丢在内存里；
    · passed/failed/interrupted 仍不含该键（存量行逐字节不变，见下方正向对照）；
    · 负向对照同测内自带：同一 registry 跑 ok 任务必须**没有** reason，
      否则本测的「env_blocked 有 reason」可能只是「什么都写 reason」的假象。
    """
    registry = TaskRegistry()
    registry.register("boom", lambda **kw: _boom(EnvBlocked("没有可用单据")))
    registry.register("ok", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "ok", produces=("done",)), lambda actor: {"done": True}, "ok"))
    with pytest.raises(EnvBlocked):
        FlowCompiler(registry).run(_flow([{"task": "boom"}], tmp_path), Actor())
    blocked = _last_run(tmp_path)
    assert blocked["status"] == "env_blocked"
    assert blocked.get("reason") == "没有可用单据", \
        "根因必须可在 runs.jsonl 直接读出（形态与 waiting 腿同款：exception_brief 裸消息体，不带类型名）"
    FlowCompiler(registry).run(_flow([{"task": "ok"}], tmp_path), Actor())
    passed = _last_run(tmp_path)
    assert passed["status"] == "passed" and "reason" not in passed


def test_dsl_env_blocked_reason_is_sanitized_and_capped(tmp_path):
    """env_blocked 的 reason 与 waiting 同一把净化刀（C4②）：凭据值遮蔽、截 200。
    接通写侧若忘了净化，等于把「Cookie=xxx」明文写进新的落盘字段——比不写更糟。"""
    token = "isc-axios-sessionid-0f1e2d3c4b5a6978"
    registry = TaskRegistry()
    registry.register("boom", lambda **kw: _boom(EnvBlocked(
        f"SIT 缺主数据，请求头 isc-axios-sessionid={token} " + "长" * 400)))
    with pytest.raises(EnvBlocked):
        FlowCompiler(registry).run(_flow([{"task": "boom"}], tmp_path), Actor())
    reason = _last_run(tmp_path)["reason"]
    assert token not in reason and "isc-axios-sessionid" in reason
    assert len(reason) <= 200


def test_dsl_env_blocked_skip_policy_also_writes_reason(tmp_path):
    """`on_env_blocked: skip` 腿不重抛，但同样要留根因——跳过型 env_blocked 恰恰是
    「批量巡检里最多、最需要知道为什么被跳」的那一类。"""
    registry = TaskRegistry()
    registry.register("boom", lambda **kw: _boom(EnvBlocked("备件无价格 SPMM-150")))
    registry.register("ok", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "ok", produces=("done",)), lambda actor: {"done": True}, "ok"))
    FlowCompiler(registry).run(_flow([{"task": "boom"}], tmp_path, on_env_blocked="skip"),
                               Actor())
    row = _last_run(tmp_path)
    assert row["status"] == "env_blocked" and "SPMM-150" in row["reason"]


def test_on_env_blocked_skip_semantics_unchanged(tmp_path):
    """§3 dsl 行「on_env_blocked: skip 语义不动」：跳过策略与 waiting 互不相干。"""
    registry = TaskRegistry()
    registry.register("boom", lambda **kw: _boom(EnvBlocked("无数据")))
    registry.register("ok", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "ok", produces=("done",)), lambda actor: {"done": True}, "ok"))
    actor = Actor()
    FlowCompiler(registry).run({"engine": AtlasConfig(tmp_path),
                                "steps": [{"task": "boom", "on_env_blocked": "skip"},
                                          {"task": "ok"}]}, actor)
    assert actor.ctx["done"] is True and actor.ctx["_steps_skipped"] == ["boom"]


def test_waiting_has_no_skip_policy_by_design(tmp_path):
    """红线 #7 反面（§3 dsl 行）：waiting 永远 stop——**不存在**「跳过等人继续跑」的 DSL 键，
    有那种键就是静默绿通道。这里钉住 on_env_blocked: skip 救不了 waiting。"""
    registry = TaskRegistry()
    registry.register("wait_human", lambda **kw: _waiting_task())
    with pytest.raises(AwaitingHuman):
        FlowCompiler(registry).run({"engine": AtlasConfig(tmp_path),
                                    "steps": [{"task": "wait_human",
                                               "on_env_blocked": "skip"}]}, Actor())


# ── §7.7 engine 分支（与 #114 面 B 同型；评审必改 C9-B 的落位） ──────────

def _engine(tmp_path, journal=None, session=None, healer=None, recorder=True):
    return ExecutionEngine(AtlasConfig(tmp_path),
                           recorder=RunRecorder("c9-engine", "sit", tmp_path / "runs.jsonl")
                           if recorder else None,
                           journal=journal, session=session, healer=healer)


def test_engine_records_waiting_and_reraises_without_diagnosis_chain(tmp_path):
    """journal 草稿计数 0 + capture 未调 + StepResult(ok=False,"waiting") + 原异常重抛。
    remedy 无从修复「等人」，给等待现场拍取证照是纯噪音（§3 engine 行）。"""
    reports = tmp_path / "reports"
    journal = ExperienceJournal(reports)
    captured: list = []
    session = SimpleNamespace(capture=lambda name, ok: captured.append((name, ok)) or None)
    engine = _engine(tmp_path, journal=journal, session=session)
    with pytest.raises(AwaitingHuman):
        engine.execute(Actor(), _waiting_task())
    (result,) = engine.results
    assert result.ok is False and result.failure_class == "waiting"
    assert captured == []
    queue = reports / "experience_drafts.jsonl"
    assert not queue.exists() or not [line for line in
                                      queue.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ordinary_failure_does_register_a_draft_while_waiting_does_not(tmp_path):
    """正向对照（喂真数据，不让上面那条「草稿计数 0」变成恒真）：同一套 engine+journal
    装配下，普通业务失败**确实**登记 pending_review 草稿——证明 waiting 的 0 是被
    CONTROL_FLOW 同型的短路拦住的，而不是登记器压根没接上。"""
    reports = tmp_path / "reports"
    engine = _engine(tmp_path, journal=ExperienceJournal(reports))
    with pytest.raises(RuntimeError):
        engine.execute(Actor(), _boom(RuntimeError("接口返回空")))
    rows = [json.loads(line) for line in
            (reports / "experience_drafts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert rows and rows[0].get("status") == "pending_review"


def test_engine_waiting_step_row_shape(tmp_path):
    """steps 行的 fail_class 仍走 classify 单点；results 的取值走枚举（取值单一出处）。"""
    engine = _engine(tmp_path)
    with pytest.raises(AwaitingHuman):
        engine.execute(Actor(), _waiting_task())
    step = engine.recorder.steps[0]
    assert step["fail_class"] == "waiting" and step["ok"] is False


def test_engine_control_flow_branch_untouched_alongside_waiting(tmp_path, monkeypatch):
    """对照测：CONTROL_FLOW 分支逐字不动（interrupted + case_timeout 标记面）仍在原位，
    waiting 是**并列新增**的 elif，不是改写它。"""
    monkeypatch.setenv("ATLAS_CASE_TIMEOUT", "1000")
    engine = _engine(tmp_path)
    engine._watchdog.fired = True
    with pytest.raises(KeyboardInterrupt):
        engine.execute(Actor(), _boom(KeyboardInterrupt(), "halt"))
    assert engine.results[0].failure_class == "interrupted"
    assert engine.recorder.steps[0].get("case_timeout") is True


def test_engine_awaiting_human_never_retried(tmp_path, monkeypatch):
    """评审必改 C9-B 的回归钉：elif 必须在步骤级 flaky 重试块**之前**。
    位置错一处会怎样：reason 文案带「timeout」的 AwaitingHuman 先被 is_recoverable 判可重试
    （classify→INTERACTION 那条腿按文本走），于是等人被就地重跑若干遍、run 记录多出
    retried 噪音，而重试永远等不来人。"""
    monkeypatch.setenv("ATLAS_STEP_RETRY", "3")
    calls = {"n": 0}
    engine = _engine(tmp_path)

    def wait(actor):
        calls["n"] += 1
        raise AwaitingHuman("等待人工审批（页面 timeout 未跳转）")
    task = FunctionalTask(TaskMetadata("demo", "wait_human", produces=("v",)), wait, "wait_human")
    with pytest.raises(AwaitingHuman):
        engine.execute(Actor(), task)
    assert calls["n"] == 1                                      # perform 恰 1 次
    assert "retried" not in engine.recorder.steps[0]


def test_engine_waiting_does_not_enter_heal_gate(tmp_path, monkeypatch):
    """heal 模式也不许对等人重跑：准入函数根本不该被调用（与 #108 同纪律）。"""
    monkeypatch.setenv("ATLAS_MODE", "heal")

    class BoomHealer:
        def can_auto_heal(self, task, exc):
            raise AssertionError("waiting 不得进入 heal 准入")
    engine = _engine(tmp_path, healer=BoomHealer())
    with pytest.raises(AwaitingHuman):
        engine.execute(Actor(), _waiting_task())


def test_engine_waiting_does_not_touch_evidence_classifier(tmp_path):
    """`evidence.classify` 不许被调用：API 重放器对着「等人」现场重放一次接口是纯噪音，
    且定性结果会把 waiting 洗成 interaction（红线 #7 的输入面）。"""
    class BoomEvidence:
        def classify(self, exc):
            raise AssertionError("waiting 不得走实证定性")
    engine = ExecutionEngine(AtlasConfig(tmp_path), evidence=BoomEvidence(),
                             recorder=RunRecorder("c9-ev", "sit", tmp_path / "runs.jsonl"))
    with pytest.raises(AwaitingHuman):
        engine.execute(Actor(), _waiting_task())


# ── §7.9 CLI run rc3 + stderr（X4 返回串纪律） ───────────────────────────

class _FakeRuntime:
    """假 DemoRuntime：不建浏览器、不登录，只把 registry/actor/provider 交给 FlowCompiler。
    记录 __exit__ 次数——rc3 这条腿必须照常走收尾（浏览器/回收清单/留痕）。"""

    def __init__(self, config, registry, **kwargs):
        self.config, self._registry, self.actor = config, registry, Actor()
        self.provider = None
        self.exits: list = []

    def registry(self):
        return self._registry

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exits.append(exc)
        return False


@pytest.fixture
def cli_run(tmp_path, monkeypatch):
    """build(registry, task) -> (rc, stdout, stderr, exits)。run 入口的三道预检
    （URL/DemoApi/legacy 路径）全部满足，但运行时是假的——不烧真实会话。"""
    from atlas import cli as cli_module

    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    legacy = tmp_path / "legacy"
    (legacy / "web").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path))
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "web")
    box: dict = {}

    def factory(config, **kwargs):
        runtime = _FakeRuntime(config, box["registry"])
        box["runtime"] = runtime
        return runtime

    monkeypatch.setattr(cli_module, "DemoRuntime", factory)

    def build(registry, task="wait_human"):
        box["registry"] = registry
        monkeypatch.setattr(cli_module, "_full_registry", lambda: registry)
        flow = tmp_path / f"{task}.yaml"
        flow.write_text(f"id: {task}\nsteps:\n  - task: {task}\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli_module.main(["--root", str(tmp_path), "run", str(flow)])
        return rc, out.getvalue(), err.getvalue(), box["runtime"].exits
    return build


def _registry_with(*tasks):
    registry = TaskRegistry()
    for name, task in tasks:
        registry.register(name, lambda _t=task, **kw: _t)
    return registry


def test_cli_run_returns_rc3_and_prints_waiting_line(cli_run, tmp_path):
    rc, _out, err, exits = cli_run(_registry_with(("wait_human", _waiting_task())))
    assert rc == 3                                          # 等人信号，不是失败
    assert "WAITING-HUMAN:" in err and WAIT_REASON in err
    assert len(exits) == 1                                  # __exit__ 照常收尾
    assert _last_run(tmp_path)["status"] == "waiting"


def test_cli_run_waiting_stderr_is_cp936_encodable(cli_run):
    """X4 返回串纪律（memory 教训：print 字面量哨兵扫不到返回串，必须直接断言整行可编）。"""
    _rc, _out, err, _exits = cli_run(_registry_with(("wait_human", _waiting_task())))
    err.encode("cp936")                                     # 不抛 = 中文控制台不会崩掉退出码


def test_cli_run_passed_rc_unchanged(cli_run):
    registry = _registry_with(("ok", FunctionalTask(
        TaskMetadata("demo", "ok", produces=("v",)), lambda actor: {"v": 1}, "ok")))
    rc, _out, err, _exits = cli_run(registry, "ok")
    assert rc == 0 and "WAITING-HUMAN" not in err


def test_cli_run_failure_shape_unchanged(cli_run):
    """真实失败仍按原形态传播（进程退出码 1 由解释器给出），C9 不许把 failed 洗成
    waiting 的 rc3，也不许给它套一条 WAITING-HUMAN 行——只有 AwaitingHuman 走新腿。"""
    registry = _registry_with(("boom", _boom(RuntimeError("接口 500"))))
    with pytest.raises(RuntimeError):
        cli_run(registry, "boom")


def test_supervisor_passes_child_rc3_through(tmp_path):
    """评审必改 C9-D：supervisor 对子进程 rc3 原样透传（proc.wait），
    `atlas run --case-timeout` 包一层后等人信号不许变成 1 或 0。"""
    config = AtlasConfig.__new__(AtlasConfig)
    object.__setattr__(config, "root", tmp_path)
    object.__setattr__(config, "env", "sit")
    object.__setattr__(config, "mode", "run")
    from atlas.cli import _supervise_case
    rc = _supervise_case([sys.executable, "-c", "import sys; sys.exit(3)"],
                         config, {"id": "c9"}, 60)
    assert rc == 3
    assert not (tmp_path / "reports" / "runs.jsonl").exists()   # 透传而已，不补记终态行


# ── §7.10 Allure 映射：label+message 双留 ────────────────────────────────

def test_allure_run_level_waiting_maps_to_skipped_with_label_and_message():
    status, details, labels = _status_from_run(
        {"status": "waiting", "reason": WAIT_REASON, "steps": [{"fail_class": "waiting"}]})
    assert status == "skipped"                               # 与 blocked/interrupted 同族
    assert details["message"] == WAIT_REASON                 # 原文保留（不洗绿的另一半）
    assert labels == {"atlas.fail_class": "waiting", "atlas.waiting": "true"}


def test_allure_run_level_waiting_without_reason_falls_back():
    """缺省回落固定文案——与 env_blocked 侧先例同构；绝不回落空串
    （空 message 的 skipped 读起来就是「没发生」）。"""
    status, details, _labels = _status_from_run({"status": "waiting", "steps": []})
    assert status == "skipped" and details["message"] == "awaiting human intervention"


def test_allure_failed_run_with_waiting_step_class_is_also_skipped():
    """(failed ∧ fail_class=waiting) 一支：run 记 failed 而步骤是 waiting 时报告面仍按
    等人处置（读侧不回判、不重抄判据）。"""
    status, _d, labels = _status_from_run({"status": "failed", "steps": [{"fail_class": "waiting"}]})
    assert status == "skipped" and labels["atlas.waiting"] == "true"


def test_allure_step_level_waiting_branch():
    status, details, labels = _step_status(False, "waiting", "wait_human")
    assert status == "skipped"
    assert labels["atlas.fail_class"] == "waiting"
    assert details["message"]                                  # 非空：步骤级也留得下「在等什么」


@pytest.mark.parametrize("bad_status", ["nonsense", "timeout", None])
def test_allure_unknown_status_still_broken(bad_status):
    """「缺 label/未知状态 → broken」兜底分支不动：旧版 allure 消费器读新 waiting 行的
    另一面是未知仍 fail-loud——顺手把未知读成 skipped 才是真洗绿。"""
    status, _d, labels = _status_from_run({"status": bad_status, "steps": []})
    assert status == "broken" and labels["atlas.fail_class"] == "unknown"


def test_emit_allure_end_to_end_waiting_keeps_both_traces(tmp_path):
    runs = tmp_path / "runs.jsonl"
    recorder = RunRecorder("tc-wait", "sit", runs)
    with pytest.raises(AwaitingHuman):
        with recorder.step("wait_human", "ui"):
            raise AwaitingHuman(WAIT_REASON)
    recorder.close("waiting", reason=WAIT_REASON)
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    result = json.loads(sorted(out.glob("atlas-*-result.json"))[0].read_text(encoding="utf-8"))
    labels = {item["name"]: item["value"] for item in result["labels"]}
    assert result["status"] == "skipped"
    assert labels["atlas.fail_class"] == "waiting" and labels["atlas.waiting"] == "true"
    assert result["statusDetails"]["message"] == WAIT_REASON
    assert result["steps"][0]["status"] == "skipped"


# ── §7.11 allure_analysis：自产往返可用、外部文本永不 waiting、桶派生 ────

def test_bucket_environment_blocked_is_derived_from_operational_family():
    """§2 表第一行：家族成员判据只此一处。手抄清单漏一处=「等人」被读进缺陷桶。
    这条同时是 test_allure_analysis.py::test_default_buckets_cover_all_classes 从
    「加枚举必红」变「自动纳入」的那半边的落地证据（验收项，非回归）。"""
    assert DEFAULT_BUCKETS["environment_blocked"] == frozenset(OPERATIONAL)
    assert "waiting" in DEFAULT_BUCKETS["environment_blocked"]
    assert frozenset().union(*DEFAULT_BUCKETS.values()) >= {c.value for c in FailureClass
                                                            if c != FailureClass.UNKNOWN}


def test_self_produced_waiting_label_round_trips():
    finding = parse_allure_result({
        "uuid": "w1", "name": "tc", "fullName": "atlas.sit.tc", "status": "skipped",
        "statusDetails": {"message": WAIT_REASON, "trace": ""},
        "labels": [{"name": "atlas.fail_class", "value": "waiting"}],
        "start": 1, "stop": 2, "parameters": [], "steps": [], "attachments": []})
    assert finding.fail_class == "waiting"


def test_external_text_never_parsed_as_waiting():
    """外部 SCS 结果没有 atlas.fail_class label → 走 classify_text，永远拿不到 waiting。"""
    for message in ("waiting for human", "等待人工", "approval required"):
        finding = parse_allure_result({
            "uuid": "x", "name": "tc", "fullName": "x", "status": "failed",
            "statusDetails": {"message": message, "trace": ""},
            "labels": [], "start": 1, "stop": 2, "parameters": [], "steps": [],
            "attachments": []})
        assert finding.fail_class != "waiting"


def test_waiting_skipped_result_does_not_enter_failed_buckets(tmp_path):
    results_dir = tmp_path / "allure-results"
    results_dir.mkdir()
    payload = {"uuid": "w9", "name": "tc", "fullName": "atlas.sit.tc", "status": "skipped",
               "statusDetails": {"message": WAIT_REASON, "trace": ""},
               "labels": [{"name": "atlas.fail_class", "value": "waiting"}],
               "start": 1, "stop": 2, "parameters": [], "steps": [], "attachments": []}
    (results_dir / "atlas-w9-result.json").write_text(json.dumps(payload, ensure_ascii=False),
                                                      encoding="utf-8")
    report = analyze_allure(results_dir)
    assert report["failed"] == 0 and report["skipped"] == 1
    assert report["buckets"]["environment_blocked"] == 0     # skipped 不进桶（既有四桶数字不变）


# ── §7.12 run_attention 表 ───────────────────────────────────────────────

def test_run_attention_counts_waiting_and_operational_family():
    summary = {"status": {"waiting": 2, "env_blocked": 1, "interrupted": 2,
                          "timeout": 3, "failed": 7, "passed": 10}}
    attention = run_attention(summary, {"pending": 19})
    assert attention == {"waiting": 2, "operational": 5, "timeout": 3, "tower_pending": 19,
                         "notes": ["TIMEOUT-COUNT=3（看护掐表，查 --case-timeout 与用例耗时）"]}
    # 3 行 timeout（supervisor 造的 flow 级新终态）**不在** operational 里——评审 C9-E 声明面；
    # 但自 2026-11-06 起有独立可见键（此前只有负向钉，超时的 run 在本块里彻底隐身）。
    assert attention["operational"] == 5, "timeout 不得被并入操作/环境族（那会把掐表读成环境阻塞）"


def test_run_attention_timeout_visible_but_not_operational():
    """正向对照自证（防「键存在」式恒真）：只有 timeout 的 summary 必须让 timeout 非 0
    且 operational 为 0——两个方向同时成立才叫「单独列示、不入家族」。"""
    only_timeout = run_attention({"status": {"timeout": 4, "passed": 1}}, {"pending": 0})
    assert only_timeout["timeout"] == 4
    assert only_timeout["operational"] == 0
    assert only_timeout["waiting"] == 0
    assert any(n.startswith("TIMEOUT-COUNT=4") for n in only_timeout["notes"])
    # 零超时时不注水：notes 里不该出现 TIMEOUT-COUNT（否则处置提示变成噪音）
    none_timeout = run_attention({"status": {"passed": 1}}, {"pending": 0})
    assert none_timeout["timeout"] == 0 and none_timeout["notes"] == []


def test_run_attention_without_tower_read_does_not_impersonate_zero():
    """#64 诚实面：没读过塔就说没读过，不填 0 冒充「塔是空的」。"""
    attention = run_attention({"status": {"passed": 1}})
    assert attention["tower_pending"] is None
    assert attention["notes"] == ["TOWER-NOT-READ"]


@pytest.mark.parametrize("bad_summary", [{}, {"status": "不是映射"}, {"status": None},
                                         None, {"status": {"waiting": "3"}},
                                         {"status": {"waiting": True}},
                                         {"status": {"waiting": None}}])
def test_run_attention_never_dies_on_bad_shape(bad_summary):
    """判据/聚合函数不得先于被判定对象死（#89 家族）：坏形态给 0 不抛，不静默丢整块结论。"""
    attention = run_attention(bad_summary, {"pending": 0})
    assert attention["waiting"] == 0 and attention["operational"] == 0
    assert attention["tower_pending"] == 0


def test_run_attention_tower_bad_shape_is_zero_not_crash():
    assert run_attention({"status": {}}, {"nope": 1})["tower_pending"] == 0
    assert run_attention({"status": {}}, "塔读崩了")["tower_pending"] == 0


def test_summarize_counts_waiting_without_changing_legacy_keys(tmp_path):
    """§3 表 reporting 行「summarize 本体零改动」：status Counter 天然容纳新值，
    缺陷分布里 waiting 如实可见（不许伪装成「没发生」）。"""
    path = tmp_path / "runs.jsonl"
    rows = [{"run_id": "a", "case": "tc1", "env": "sit", "status": "waiting",
             "steps": [{"step": "wait_human", "fail_class": "waiting", "dur_ms": 1}]},
            {"run_id": "b", "case": "tc2", "env": "sit", "status": "passed", "steps": []},
            {"run_id": "c", "case": "tc3", "env": "sit", "status": "waiting",
             "steps": [{"step": "wait_human", "fail_class": "waiting", "dur_ms": 2}]}]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    summary = summarize(path)
    assert summary["status"]["waiting"] == 2
    assert summary["fail_classes"] == {"waiting": 2}
    assert summary["runs"] == 3 and summary["cases"] == 3


def test_cli_summary_adds_attention_key(tmp_path, capsys):
    """CLI 装配点（§3 cli 行②）：塔真读过 → notes 不含 TOWER-NOT-READ。"""
    runs = tmp_path / "reports" / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    runs.write_text(json.dumps({"run_id": "a", "case": "tc1", "env": "sit",
                                "status": "waiting",
                                "steps": [{"step": "w", "fail_class": "waiting",
                                           "dur_ms": 1}]}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    assert main(["--root", str(tmp_path), "summary", str(runs)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["attention"]["waiting"] == 1 and out["attention"]["operational"] == 1
    assert out["attention"]["tower_pending"] == 0 and out["attention"]["notes"] == []
    assert out["status"]["waiting"] == 1                      # 既有键形态不动（只增新键）


# ── §7.13 调度域：waiting=keep、不计败、水位线照常推进 ───────────────────

def test_scheduling_policy_waiting_is_keep():
    assert decision.failure_policy("waiting") == "keep"
    assert decision.counts_as_failure("waiting") is False


def test_waiting_neither_counts_nor_resets_the_streak(tmp_path):
    """连败序列 timeout/waiting/timeout = 2 不 = 3，也不被 waiting 清零。"""
    path = tmp_path / "tasks.json"
    st.save_doc(path, {"schema_version": st.SCHEMA_VERSION,
                       "tasks": {"p": {"id": "p", "consecutive_failures": 1,
                                       "enabled": True}}})
    st.apply_failure_policy(path, "p", decision.failure_policy("waiting"))
    assert st.load_doc(path)["tasks"]["p"]["consecutive_failures"] == 1
    st.apply_failure_policy(path, "p", "count")
    assert st.load_doc(path)["tasks"]["p"]["consecutive_failures"] == 2


def _sched_root(tmp_path):
    base = tmp_path / "atlas"
    (base / "flows").mkdir(parents=True)
    (base / ".atlas-root").write_text("", encoding="utf-8")
    (base / "flows" / "patrol.yaml").write_text("id: patrol\nsteps:\n  - task: ro\n",
                                                 encoding="utf-8")
    return base


def _seed_task(root, *, watermark=None, **over):
    task = {"id": "patrol", "flow": "flows/patrol.yaml", "run_args": [],
            "schedule": {"kind": "cron", "expr": "0 * * * *", "tz": "UTC"},
            "max_duration_min": 30, "enabled": True, "writes_acknowledged": False,
            "created_at": datetime(2026, 11, 5, 0, 0, tzinfo=UTC).isoformat(),
            "updated_at": datetime(2026, 11, 5, 0, 0, tzinfo=UTC).isoformat(),
            "last_scheduled_for": watermark.isoformat() if watermark else None,
            "consecutive_failures": 0, "auto_paused_reason": None}
    task.update(over)
    doc = st.load_doc(st.tasks_path(root))
    doc["tasks"]["patrol"] = task
    st.save_doc(st.tasks_path(root), doc)


def test_tick_child_rc3_maps_to_waiting_and_returns_zero(tmp_path, monkeypatch):
    """dispatch rc3→outcome "waiting"；**tick 返回 0**（lead 拍板：rc1=「派发子进程失败」
    会把等人洗红），水位线照常推进（本轮认领过就是过了）。"""
    root = _sched_root(tmp_path)
    _seed_task(root)
    monkeypatch.setattr(dispatch, "_spawn",
                        lambda command, **kw: SimpleNamespace(returncode=3))
    assert dispatch.tick("patrol", SimpleNamespace(root=root), now=THREE_THIRTY) == 0
    rows = st.read_runs(st.runs_path(root))
    assert rows[0]["outcome"] == "waiting" and rows[0]["counts_as_failure"] is False
    assert rows[0]["child_rc"] == 3
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["last_scheduled_for"] == "2026-11-05T03:00:00+00:00"    # 推进
    assert task["consecutive_failures"] == 0
    assert not st.lease_path(root, "patrol").exists()                   # finally 仍放了锁


def test_tick_child_failure_still_returns_one(tmp_path, monkeypatch):
    """双向钉：failed 仍返回 1——waiting 的 0 不是把整张 rc 表放宽（红线 #7）。"""
    root = _sched_root(tmp_path)
    _seed_task(root)
    monkeypatch.setattr(dispatch, "_spawn",
                        lambda command, **kw: SimpleNamespace(returncode=1))
    assert dispatch.tick("patrol", SimpleNamespace(root=root), now=THREE_THIRTY) == 1
    rows = st.read_runs(st.runs_path(root))
    assert rows[0]["outcome"] == "failed" and rows[0]["counts_as_failure"] is True
    assert st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["consecutive_failures"] == 1


@pytest.mark.parametrize("child_rc,expected_outcome,expected_tick,expect_count", [
    # 子进程 rc 语义表（run 面）：130=人被 Ctrl-C 组播中止、124=用例被自身看护掐表。
    # 旧表两者都落 else→failed：Ctrl-C 被记成「这轮巡检坏了」并 +1 连败（#114 漏网面），
    # 用例超时则丢了 aborted_time_limit 的名字与「rc=124」的真实 reason。
    (130, "interrupted", 0, False),
    (124, "aborted_time_limit", 1, True),
    (7, "failed", 1, True),
])
def test_tick_child_rc_table_maps_interrupted_and_timeout(tmp_path, monkeypatch, child_rc,
                                                          expected_outcome, expected_tick,
                                                          expect_count):
    """起点连败=4：count 类必须顶到 5 触发 AUTO-PAUSED，reason 才会落进台账可读回
    （state.apply_failure_policy 只在置停时写 auto_paused_reason，没有独立的
    last_failure_reason 字段——不存在的字段不能拿来当证据）。"""
    root = _sched_root(tmp_path)
    _seed_task(root, consecutive_failures=4)
    monkeypatch.setattr(dispatch, "_spawn",
                        lambda command, **kw: SimpleNamespace(returncode=child_rc))
    assert dispatch.tick("patrol", SimpleNamespace(root=root), now=THREE_THIRTY) == expected_tick
    row = st.read_runs(st.runs_path(root))[0]
    assert row["outcome"] == expected_outcome
    assert row["counts_as_failure"] is expect_count
    assert row["child_rc"] == child_rc, "码值必须留在台账里（词表复用既有名，靠它区分谁掐的表）"
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    if expect_count:
        assert task["consecutive_failures"] == 5 and task["enabled"] is False
        assert f"rc={child_rc}" in (task.get("auto_paused_reason") or ""), \
            "count 类 reason 必须带真实码值，不许一律写成派发窗口超时"
    else:
        assert task["consecutive_failures"] == 4, "interrupted 走 keep：不 +1 也不抹掉前账"
        assert task.get("enabled", True) is not False


def test_defer_lease_still_does_not_advance_watermark(tmp_path, monkeypatch):
    """两语义不得混：defer=没执行（不推进、不派发），waiting=执行到等人步骤（推进）。"""
    root = _sched_root(tmp_path)
    _seed_task(root, watermark=datetime(2026, 11, 5, 2, 0, tzinfo=UTC))
    lock = st.lease_path(root, "patrol")
    lock.parent.mkdir(parents=True, exist_ok=True)
    # 用**真实当下**做租约时间戳（既有 test_scheduling_cli 同款），否则 fixture 时间相对
    # 本机时钟早已过期，try_acquire 会走过期接管而不是占用推迟。
    assert lease.try_acquire(lock, "holder", 999, _time.time()) is True

    def forbidden(*args, **kwargs):
        raise AssertionError("租约占用时不得派发")
    monkeypatch.setattr(dispatch, "_spawn", forbidden)
    assert dispatch.tick("patrol", SimpleNamespace(root=root), now=THREE_THIRTY) == 0
    task = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]
    assert task["last_scheduled_for"] == "2026-11-05T02:00:00+00:00"     # 没推进
    assert st.read_runs(st.runs_path(root))[-1]["outcome"] == "defer_lease"


# ── §7.14 兼容：历史数据不回溯 ───────────────────────────────────────────

LEGACY_FIXTURE_ROWS = [
    {"run_id": "r1", "case": "tc_old_pass", "env": "sit", "mode": "run", "status": "passed",
     "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:05+00:00",
     "steps": [{"step": "open", "op": "ui", "ok": True, "dur_ms": 5}]},
    {"run_id": "r2", "case": "tc_old_fail", "env": "sit", "mode": "run", "status": "failed",
     "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:07+00:00",
     "steps": [{"step": "assert", "op": "ui", "ok": False, "dur_ms": 7,
                 "fail_class": "assertion"}]},
    {"run_id": "r3", "case": "tc_old_env", "env": "sit", "mode": "run", "status": "env_blocked",
     "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:03+00:00",
     "steps": [{"step": "find", "op": "api", "ok": False, "dur_ms": 3,
                 "fail_class": "env_blocked"}]},
    {"run_id": "r4", "case": "tc_old_interrupt", "env": "sit", "mode": "run",
     "status": "interrupted", "started_at": "2026-01-01T00:00:00+00:00",
     "finished_at": "2026-01-01T00:00:02+00:00",
     "steps": [{"step": "click", "op": "ui", "ok": False, "dur_ms": 2,
                 "fail_class": "interrupted"}]},
]


@pytest.fixture
def legacy_runs(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in LEGACY_FIXTURE_ROWS)
                    + "\n", encoding="utf-8")
    return path


def test_legacy_runs_jsonl_replays_identically(legacy_runs):
    """§6 末行：没有任何 waiting 行的历史文件，聚合结果逐键等值——C9 只向前生效，
    历史里读成 failed 的永远保持 failed（无 reclassify 工具面，§4.4）。"""
    summary = summarize(legacy_runs)
    assert summary["status"] == {"passed": 1, "failed": 1, "env_blocked": 1, "interrupted": 1}
    assert summary["fail_classes"] == {"assertion": 1, "env_blocked": 1, "interrupted": 1}
    assert summary["runs"] == 4 and summary["skipped_malformed_lines"] == 0
    assert "waiting" not in summary["status"] and "waiting" not in summary["fail_classes"]


def test_legacy_rows_allure_mapping_is_stable(legacy_runs, tmp_path):
    """同一批旧行的 Allure 映射与 C9 之前一致：label 集合不含 atlas.waiting，
    旧状态仍走原分支（读侧没有回判、也没被家族判据顺手改写成 skipped）。"""
    out = tmp_path / "allure-results"
    emit_allure(legacy_runs, out)
    results = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted(out.glob("atlas-*-result.json"))]
    mapping = {r["name"]: (r["status"], {l["name"]: l["value"] for l in r["labels"]})
               for r in results}
    assert mapping["tc_old_pass"][0] == "passed"
    assert mapping["tc_old_fail"][0] == "failed"
    assert mapping["tc_old_env"][0] == "skipped" and "atlas.blocked" in mapping["tc_old_env"][1]
    assert mapping["tc_old_interrupt"][0] == "skipped"
    for _name, (_status, labels) in mapping.items():
        assert "atlas.waiting" not in labels


def test_legacy_rows_runlog_export_shape_unchanged(legacy_runs, tmp_path):
    """export_runlog 的导出行不因 C9 变形态（waiting 只扩 fail_class **取值**，
    不新增 step 字段——PRIVACY 口径 §6 那一行）。"""
    from atlas.reporting import export_runlog
    target = export_runlog(legacy_runs, tmp_path / "out.jsonl")
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4
    assert set(rows[0]) == {"run_id", "case", "env", "step", "op", "ok", "dur_ms",
                            "api_ms", "retries", "fail_class"}


# ── §7.15 文档事实回写的哨兵（红线 #8，同 #92 家族防漂移） ───────────────

def test_harvest_does_not_capture_a_waiting_run(tmp_path):
    """C9 × 落库线（harvest）的交叉面：`harvest: true` 的 flow 停在等人处时**不得**把动作序列
    固化成 flow_candidate 草稿——等人不是「跑绿」，固化它等于把一条没跑完的序列当可复用资产
    送进塔（红线 #7 的另一面：坏形态伪装成成果）。dsl 的闸是 `status == "passed"`，waiting
    分支重抛后根本到不了那行，这里钉住不被后续改动放宽。"""
    reports = tmp_path / "reports"
    registry = TaskRegistry()
    registry.register("wait_human", lambda **kw: _waiting_task())
    actor = Actor()
    actor.journal = ExperienceJournal(reports)
    flow = {"id": "c9-harvest", "harvest": True, "engine": AtlasConfig(tmp_path),
            "steps": [{"task": "wait_human"}]}
    with pytest.raises(AwaitingHuman):
        FlowCompiler(registry).run(flow, actor)
    queue = reports / "experience_drafts.jsonl"
    kinds = [json.loads(line).get("kind") for line in
             (queue.read_text(encoding="utf-8").splitlines() if queue.exists() else [])
             if line.strip()]
    assert "flow_candidate" not in kinds
    assert _last_run(tmp_path)["status"] == "waiting"


def test_harvest_positive_control_still_captures_a_passed_run(tmp_path):
    """反向对照（喂真数据，不让上面那条测变成恒真）：同一条 flow 真跑绿时确实固化出
    flow_candidate——证明「waiting 不固化」是被判据拦住的，不是路径压根没接。"""
    reports = tmp_path / "reports"
    registry = TaskRegistry()
    registry.register("ok", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "ok", produces=("v",)), lambda actor: {"v": 1}, "ok"))
    actor = Actor()
    actor.journal = ExperienceJournal(reports)
    FlowCompiler(registry).run({"id": "c9-harvest-ok", "harvest": True,
                                "engine": AtlasConfig(tmp_path), "steps": [{"task": "ok"}]},
                               actor)
    rows = [json.loads(line) for line in
            (reports / "experience_drafts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert [row for row in rows if row.get("kind") == "flow_candidate"]


def test_cdp_freeze_block_is_not_a_waiting_producer():
    """§9 第 4 行的离线钉桩：CDP 冻场的非交互 EOF 放行**不是** waiting 生产者。
    冻场语义是 debug 形态的「人工接管后继续跑完」，步骤照常执行；把它读成「等人」会
    让 debug 会话凭空多出 waiting 终态与 rc3（红线 #10 冻场纪律 + 红线 #12 不投机）。"""
    import inspect
    from atlas.engine import cdp_maybe_pause
    body = inspect.getsource(cdp_maybe_pause)
    assert "AwaitingHuman" not in body and "waiting" not in body.lower()
    assert "EOFError" in body          # 非交互 EOF 仍是「放行继续」，不是终止也不是等人


@pytest.mark.parametrize("doc_name,clause", [
    ("README.md", "waiting"),
    ("README.md", "WAITING-HUMAN"),
    ("README.md", "attention"),
    ("CLAUDE.md", "waiting"),
    ("AGENTS.md", "waiting"),
    ("PRIVACY.md", "waiting"),
])
def test_docs_declare_the_new_terminal(doc_name, clause):
    """文档哨兵（M11：每条文档红线配一条会红的测）：新终态没写进事实条目=文档说谎。
    runs.jsonl 的取值域、退出码表、summary 键面三处各自有断言，缺一条就红一条。"""
    text = (Path(__file__).resolve().parents[1] / doc_name).read_text(encoding="utf-8")
    assert clause in text, f"{doc_name} 缺 {clause!r}——C9 事实未回写"


def test_src_has_no_stray_waiting_literals_outside_single_source():
    """§2 表最后一行的钉桩：`"waiting"` 字面量在 src 里只许出现在**判据与映射处**
    （failures 的枚举值与 OPERATIONAL、allure_report/decision/dispatch 的映射分支、
    dsl 走枚举取值）。散写一处就是 #92 的改一漏一温床。"""
    root = Path(__file__).resolve().parents[1] / "src" / "atlas"
    hits: dict[str, int] = {}
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == "waiting":
                    key = path.relative_to(root).as_posix()   # X1：键用 POSIX，反斜杠跨机不同答案
                    hits[key] = hits.get(key, 0) + 1
    # 空集断言的真空防线（impl_c12 同族教训）：rglob 路径落空时 hits={} 同样「通过」，
    # 那条白名单断言就成了恒真——先钉扫描面本身有效，路径坏掉当场红，而不是静默假绿。
    # 最硬的一条自证不是「文件存在」，而是**扫描必须抓到已知合法的那处命中**：
    # failures.py 里 `WAITING = "waiting"` 是这条枚举值本身，扫不到它就是扫描器坏了。
    assert scanned > 50, f"只扫到 {scanned} 个 src 文件，扫描根路径已失效"
    assert "failures.py" in hits, "扫描器连 failures 的枚举值都没抓到=测本身失效"
    # reporting.py 的那处是 `attention` 输出**键**（§5 schema 的对外协议位，与 allure 的
    # label 键同级），不是第二份判据；其余全是判据/映射处。dsl 与 recorder 必须 0——
    # 它们的取值走 FailureClass.WAITING.value / classify 单点继承。
    assert set(hits) <= {"failures.py", "allure_report.py", "reporting.py",
                         "scheduling/decision.py", "scheduling/dispatch.py", "engine.py"}, hits
    assert hits.get("recorder.py", 0) == 0 and hits.get("dsl.py", 0) == 0


# ── §9 那格取证：rc3 与「同刻超时」可以两真（离线构造，2026-11-06）────────────
# 原登记为「真机待取证」。实查结论：本仓**零** AwaitingHuman 生产者（`raise AwaitingHuman`
# 只出现在测试 fake 里），真机侧要等外部/私有插件，属**结构性不可达**；但这一格的语义
# 不需要真机——看门狗 fired 与任务自己抛错是两条独立事实，离线即可同时成立。
# 旧实现把 case_timeout 标记只写在 CONTROL_FLOW 分支里，于是「等待/投递不确定/失败」
# 一旦赢过 KI，超时证据就被静默吞掉。下面三类终态各自钉住「两个事实都在」。

@pytest.mark.parametrize("exc_factory,expected_fail_class", [
    (lambda: AwaitingHuman(WAIT_REASON), "waiting"),
    (lambda: DeliveryUncertain("API 已发出，网关无响应"), "interaction"),
    (lambda: RuntimeError("SIT 抖动"), "unknown"),
])
def test_watchdog_timeout_recorded_alongside_winning_terminal(tmp_path, monkeypatch,
                                                              exc_factory,
                                                              expected_fail_class):
    """终态赢的是谁由异常类型决定，`case_timeout` 与之正交：两者必须同时落盘。"""
    from atlas.recorder import RunRecorder
    monkeypatch.setenv("ATLAS_CASE_TIMEOUT", "1000")
    rec = RunRecorder("mixed", "sit", tmp_path / "runs.jsonl", "run")
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    assert engine._watchdog is not None
    engine._watchdog.fired = True
    def boom(actor):
        raise exc_factory()
    task = FunctionalTask(TaskMetadata("demo", "boom"), boom, "boom")
    with pytest.raises(BaseException):
        engine.execute(Actor(), task)
    engine.stop_case_guard()
    step = rec.steps[0]
    assert step["fail_class"] == expected_fail_class, "终态归因不得被超时标记改写"
    assert step.get("case_timeout") is True, "同刻超时的事实必须留下（此前只有 KI 腿留）"


def test_no_case_timeout_flag_when_watchdog_not_fired(tmp_path, monkeypatch):
    """负向对照（防上一测恒真）：没触发看门狗时**不许**冒出 case_timeout——
    否则该标记变成无条件写，「同刻超时」的取证价值归零。"""
    from atlas.recorder import RunRecorder
    monkeypatch.setenv("ATLAS_CASE_TIMEOUT", "1000")
    rec = RunRecorder("clean", "sit", tmp_path / "runs.jsonl", "run")
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    assert engine._watchdog is not None and engine._watchdog.fired is False
    def boom(actor):
        raise AwaitingHuman(WAIT_REASON)
    task = FunctionalTask(TaskMetadata("demo", "boom"), boom, "boom")
    with pytest.raises(AwaitingHuman):
        engine.execute(Actor(), task)
    engine.stop_case_guard()
    assert rec.steps[0]["fail_class"] == "waiting"
    assert "case_timeout" not in rec.steps[0]

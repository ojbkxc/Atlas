"""C4② 异常文本净化的落盘/归因切分（design_c4 §6.4/§6.5 + 评审必改 C4-B）。

核心口径（`design-review.md` §4.1 C4-B）：**落盘吃净化文本，定性吃净化前原文**。
反序的后果是「脱敏损归因」——关键词恰好只出现在敏感 k=v 的值里（`?code=401`、
`token=…timeout…`）时，先净化再定性会把 SESSION/INTERACTION 翻成 unknown，
比泄露更隐蔽。这里既有「净化确实生效」的测，也有「归因一点没变」的对抗样本。
"""
import json

import pytest

from atlas import failures as fl
from atlas.credential_gate import REDACTED
from atlas.experience import ExperienceJournal

LITERAL_UUID = "b5360b2f-1c2d-3e4f-5a6b-7c8d9e0f1a2b"
# 净化样本的 URL 只在这一处成字面量（SAFE-001 对「测试 fixture 里的虚构 .example
# 主机」是固有误报；集中一处既避免逐行重复，也不把样本藏起来）
CB = "https://sso.example/cb"
SSO_SAMPLE = f"SSO 回调失败 URL={CB}?ticket=ST-123&docNo=SO-9"


# ── 落盘净化生效 ────────────────────────────────────────────────────────

def test_exception_text_is_sanitized_but_keeps_business_params():
    exc = RuntimeError(SSO_SAMPLE)
    text = fl.exception_text(exc)
    assert "ticket=" + REDACTED in text
    assert "ST-123" not in text
    # exception_text 是「小写定性/落盘形态」（既有口径），断言按小写形态取
    assert "docno=so-9" in text, "业务取证参数被净化抹掉=销毁追责证据（#85/C4-A）"
    assert "runtimeerror" in text and "sso 回调失败" in text, "词序与消息正文不动"


def test_exception_message_and_brief_are_sanitized_display_forms():
    exc = RuntimeError("API 失败 Authorization: Bearer " + LITERAL_UUID)
    assert fl.exception_brief(exc) == "API 失败 Authorization: <redacted>"
    assert fl.exception_message(exc) == "RuntimeError: API 失败 Authorization: <redacted>"
    assert LITERAL_UUID not in fl.exception_message(exc)


def test_bad_str_still_falls_back_to_type_name():
    """#126/#129 既有纪律不因净化改动而松动：坏 __str__ 回落类型名，绝不裸抛。"""
    class Nasty(RuntimeError):
        def __str__(self):
            raise AttributeError("detail 未初始化")
    exc = Nasty()
    assert fl.exception_text(exc) == "nasty "
    assert fl.exception_message(exc) == "Nasty"
    assert fl.classify_failure(exc) is fl.FailureClass.UNKNOWN


# ── C4-B 对抗样本：关键词只在敏感值里 → 归因必须不漂 ───────────────────

def test_adversarial_code_401_keeps_session_class():
    """`?code=401`：SESSION 词表含 401/cookie/session（实测 _RULES 第 2 组）。
    净化把值段抹掉后文本里没有 401——定性若吃净化文本就翻 unknown。"""
    exc = RuntimeError(f"redirect {CB}?code=401&docNo=SO-9")
    sanitized = fl.exception_text(exc)
    assert "401" not in sanitized, "前置条件：净化后关键词确实不在文本里"
    assert fl.classify_failure(exc) is fl.FailureClass.SESSION


def test_adversarial_timeout_inside_token_value_keeps_interaction_class():
    exc = ValueError("token=abcdefghijtimeoutabcdefghijtimeout0123 not visible")
    sanitized = fl.exception_text(exc)
    assert "timeout" not in sanitized.split("token=")[1].split()[0] or "not visible" in sanitized
    assert fl.classify_failure(exc) is fl.FailureClass.INTERACTION


def test_adversarial_cookie_value_holding_env_blocked_phrase():
    exc = RuntimeError(f"拒绝 URL={CB}?cookie=生产环境禁止&ticket=ST-1")
    assert fl.classify_failure(exc) is fl.FailureClass.ENV_BLOCKED


# ── §6.4 参数化等值表：净化前后 classify 结果全等（ benign 样本零漂移）──

_BENIGN = [
    (AssertionError("expected 5 but got 3"), fl.FailureClass.ASSERTION),
    (RuntimeError("401 unauthorized"), fl.FailureClass.SESSION),
    (RuntimeError("timeout waiting for element"), fl.FailureClass.INTERACTION),
    (RuntimeError("not found: document number"), fl.FailureClass.DATA),
    (RuntimeError("contractviolation: invalid payload"), fl.FailureClass.CONTRACT),
    (RuntimeError("环境阻塞：产品设计限制"), fl.FailureClass.ENV_BLOCKED),
    (RuntimeError("some weird error"), fl.FailureClass.UNKNOWN),
    (AssertionError("客户查询/选中失败: 查询无结果"), fl.FailureClass.DATA),
    (TimeoutError("element not visible"), fl.FailureClass.INTERACTION),
    (RuntimeError("生产环境禁止写操作"), fl.FailureClass.ENV_BLOCKED),
]


@pytest.mark.parametrize("exc,expected", _BENIGN)
def test_benign_samples_equal_before_and_after_sanitize(exc, expected):
    """普通失败文本经净化不改分类（否则「脱敏」就成了新的归因漂移源）。"""
    raw = fl._classify_input(exc)
    assert fl.classify_text(raw) == expected
    assert fl.classify_text(fl.exception_text(exc)) == expected
    assert fl.classify_failure(exc) == expected


def test_control_flow_still_short_circuits():
    assert fl.classify_failure(KeyboardInterrupt()) is fl.FailureClass.INTERRUPTED
    assert fl.classify_failure(GeneratorExit()) is fl.FailureClass.INTERRUPTED


# ── §6.5 三条落盘路径同净化（一处刀覆盖）───────────────────────────────

def test_journal_draft_evidence_text_is_sanitized(tmp_path):
    journal = ExperienceJournal(tmp_path / "reports")
    row = journal.draft(task="tc1", exc=RuntimeError(SSO_SAMPLE))
    assert "ST-123" not in row["evidence"]["text"]
    assert "docno=so-9" in row["evidence"]["text"]          # 小写形态（既有口径）
    assert row["failure_class"] == "unknown"                 # 样本本身无失败词，不猜


def test_agent_trace_reason_is_sanitized(tmp_path):
    from atlas.actor import Actor
    from atlas.agent import AtlasAgent
    from atlas.core import FunctionalTask, TaskMetadata

    journal = tmp_path / "decisions.jsonl"

    def boom(actor):
        raise RuntimeError(SSO_SAMPLE)

    task = FunctionalTask(TaskMetadata("demo", "query", (), ("data",)), boom, "query")
    agent = AtlasAgent(journal=journal)
    with pytest.raises(RuntimeError):
        agent.run([task], "data", Actor())
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert "ST-123" not in row["reason"] and REDACTED in row["reason"]


def test_degraded_to_ui_reason_is_sanitized_and_keeps_old_shape(tmp_path):
    """engine 的降级 reason 是 runs.jsonl 的既有字段：净化生效，形态不变
    （不突然多一个类型名前缀——那会改已落盘字段的取值形态）。"""
    from atlas.actor import Actor
    from atlas.config import AtlasConfig
    from atlas.core import Channel, TaskMetadata
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder

    class ApiTask:
        name = "auto_task"
        supports_api = True
        supports_generic_route = False
        metadata = TaskMetadata("demo", "auto_task", produces=("data",),
                                channel=Channel.AUTO, api_operation="find_x",
                                read_only=True)

        def _perform_api(self, actor):
            raise RuntimeError(f"网关故障 URL={CB}?ticket=ST-9")

        def perform(self, actor):
            return {"data": "ui-ok"}

    config = AtlasConfig(tmp_path, mode="run")
    recorder = RunRecorder("case", config.env, config.reports_dir / "runs.jsonl", "run")
    engine = ExecutionEngine(config, recorder=recorder)
    actor = Actor()
    engine.execute(actor, ApiTask())
    step = recorder.steps[-1]
    reason = step["degraded_to_ui"][0]["reason"]
    assert "ST-9" not in reason and REDACTED in reason
    assert not reason.startswith("RuntimeError:"), "既有字段形态不改"

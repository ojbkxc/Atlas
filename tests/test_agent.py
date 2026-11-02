import json
import pytest
from atlas import Actor, Channel, FunctionalTask, TaskMetadata
from atlas.agent import ApprovalRequired, AtlasAgent, RiskLevel, classify_risk


def task(name, requires=(), produces=(), read_only=True, channel=Channel.UI):
    return FunctionalTask(
        TaskMetadata("demo", name, tuple(requires), tuple(produces), channel, read_only=read_only),
        lambda actor: {produces[0]: name} if produces else None, name)


def test_read_only_task_is_lowest_risk():
    assert classify_risk(task("query", read_only=True)) is RiskLevel.READ_ONLY


def test_write_with_save_semantics_is_medium():
    assert classify_risk(task("click_save", read_only=False)) is RiskLevel.MEDIUM


def test_plain_write_is_low():
    assert classify_risk(task("fill_remark", read_only=False)) is RiskLevel.LOW


def test_production_write_is_always_high():
    assert classify_risk(task("fill_remark", read_only=False), production=True) is RiskLevel.HIGH


def test_agent_plans_only_required_tasks():
    tasks = [task("open", produces=("page",)),
             task("read", ("page",), ("data",), read_only=True),
             task("unrelated", produces=("other",), read_only=False)]
    agent = AtlasAgent()
    proposal = agent.propose(tasks, "data")
    assert [t["name"] for t in proposal["tasks"]] == ["open", "read"]
    assert proposal["auto_allowed"] is True


def test_agent_blocks_write_without_approval(tmp_path):
    tasks = [task("click_save", produces=("saved",), read_only=False)]
    agent = AtlasAgent(journal=tmp_path / "decisions.jsonl")
    with pytest.raises(ApprovalRequired):
        agent.run(tasks, "saved", Actor())
    assert agent.decisions[0].approved is False
    assert "需人工批准" in agent.decisions[0].reason


def test_agent_runs_after_approval_and_journals(tmp_path):
    journal = tmp_path / "decisions.jsonl"
    tasks = [task("click_save", produces=("saved",), read_only=False)]
    agent = AtlasAgent(journal=journal)
    actor = Actor()
    decision = agent.run(tasks, "saved", actor, approved=True)
    assert decision.approved is True and actor.ctx["saved"] == "click_save"
    assert json.loads(journal.read_text(encoding="utf-8").strip())["approved"] is True


def test_agent_auto_runs_read_only_goal():
    tasks = [task("query", produces=("data",), read_only=True)]
    agent = AtlasAgent()
    assert agent.run(tasks, "data", Actor()).approved is True


def test_max_auto_risk_raises_threshold():
    tasks = [task("fill", produces=("x",), read_only=False)]
    agent = AtlasAgent(max_auto_risk=RiskLevel.LOW)
    assert agent.run(tasks, "x", Actor()).approved is True


def test_missing_dependency_is_reported_by_agent():
    with pytest.raises(Exception):
        AtlasAgent().propose([task("save", ("customer",), ("doc",), read_only=False)], "doc")


def test_agent_assess_returns_max_risk_of_tasks():
    """assess 返回所有 Task 中的最高风险。"""
    tasks = [task("read", produces=("d",), read_only=True),
             task("save", produces=("s",), read_only=False)]
    agent = AtlasAgent()
    assert agent.assess(tasks) == RiskLevel.MEDIUM  # save 语义是 MEDIUM


def test_agent_can_auto_run_respects_threshold():
    """can_auto_run 按阈值判断是否可自动执行。"""
    agent = AtlasAgent(max_auto_risk=RiskLevel.LOW)
    assert agent.can_auto_run(RiskLevel.READ_ONLY) is True
    assert agent.can_auto_run(RiskLevel.LOW) is True
    assert agent.can_auto_run(RiskLevel.MEDIUM) is False
    assert agent.can_auto_run(RiskLevel.HIGH) is False


def test_decision_as_dict_contains_all_fields():
    """Decision.as_dict 包含所有可审计字段。"""
    from atlas.agent import Decision
    d = Decision("goal", ["t1", "t2"], "medium", True, "human_approved", ["bad"])
    assert d.as_dict() == {"goal": "goal", "tasks": ["t1", "t2"], "risk": "medium",
                           "approved": True, "reason": "human_approved", "rejected": ["bad"]}


def test_agent_production_write_is_always_high():
    """生产环境下任何写操作都是 HIGH 风险，不论 Task 名。"""
    tasks = [task("fill", produces=("x",), read_only=False)]
    agent = AtlasAgent(production=True)
    assert agent.assess(tasks) == RiskLevel.HIGH


def test_agent_production_read_only_is_safe():
    """生产环境下只读操作仍然是 READ_ONLY。"""
    tasks = [task("query", produces=("d",), read_only=True)]
    agent = AtlasAgent(production=True)
    assert agent.assess(tasks) == RiskLevel.READ_ONLY


def test_agent_propose_includes_channel_and_read_only():
    """propose 输出每个 Task 的 channel 与 read_only，供人审。"""
    tasks = [task("read", produces=("d",), read_only=True),
             task("save", produces=("s",), read_only=False)]
    proposal = AtlasAgent().propose(tasks, "s")
    for t in proposal["tasks"]:
        assert "channel" in t and "read_only" in t and "intent" in t

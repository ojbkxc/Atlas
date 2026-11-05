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
    d = Decision("goal", ["t1", "t2"], "medium", True, "human_approved", ["bad"], ["t1"])
    assert d.as_dict() == {"goal": "goal", "tasks": ["t1", "t2"], "risk": "medium",
                           "approved": True, "reason": "human_approved", "rejected": ["bad"],
                           "executed": ["t1"]}


def test_agent_journal_records_midway_failure_with_completed(tmp_path):
    """执行循环半途抛错：审批门已放行、已执行的写 Task 真实改过系统，
    journal 必须留痕（完成清单 + 止步 Task + 真因），且异常原样重抛不吞。"""
    journal = tmp_path / "decisions.jsonl"
    agent = AtlasAgent(max_auto_risk=RiskLevel.LOW, journal=journal)
    def boom(actor):
        raise RuntimeError("SIT 抖动")
    t1 = task("fill_remark", produces=("x",), read_only=False)
    t2 = FunctionalTask(TaskMetadata("demo", "wait_no", ("x",), ("doc",)), boom, "wait_no")
    with pytest.raises(RuntimeError, match="SIT 抖动"):
        agent.run([t1, t2], "doc", Actor())
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert row["executed"] == ["fill_remark"]
    assert "执行失败" in row["reason"] and "wait_no" in row["reason"]
    assert "SIT 抖动" in row["reason"]


def test_agent_keyboard_interrupt_journaled_as_interrupted(tmp_path):
    """Ctrl-C 不是执行失败：记 interrupted（与 DSL 中断终态口径一致）并原样重抛。"""
    journal = tmp_path / "decisions.jsonl"
    agent = AtlasAgent(journal=journal)
    def interrupt(actor):
        raise KeyboardInterrupt
    t = FunctionalTask(TaskMetadata("demo", "query", (), ("data",)), interrupt, "query")
    with pytest.raises(KeyboardInterrupt):
        agent.run([t], "data", Actor())
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert "interrupted" in row["reason"] and "执行失败" not in row["reason"]
    assert row["executed"] == []


def test_agent_journal_write_failure_does_not_bury_execution_error(tmp_path):
    """留痕文件写不进（父路径是文件）：不得顶掉执行真因；内存 decisions 仍保留记录。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    agent = AtlasAgent(journal=blocker / "decisions.jsonl")
    def boom(actor):
        raise RuntimeError("真因")
    t = FunctionalTask(TaskMetadata("demo", "query", (), ("data",)), boom, "query")
    with pytest.raises(RuntimeError, match="真因"):
        agent.run([t], "data", Actor())
    assert agent.decisions and agent.decisions[-1].executed == []


def test_agent_success_survives_bad_journal(tmp_path):
    """#110：成功路径的 _record 落盘失败不得顶掉成功返回——旧实现 #85 只护了失败路径，
    成功路径裸调 mkdir/open：全部写 Task 已真实执行（副作用已发生），FileExistsError
    却把 Decision 返回翻成基础设施异常——caller 拿不到 executed 清单，成功信号被掩埋
    （追责断链与 #85 同红线，只是发生在「全部成功」而非「半途失败」）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    agent = AtlasAgent(journal=blocker / "decisions.jsonl")
    calls = {"n": 0}
    def real_write(actor):
        calls["n"] += 1
        return {"doc": "SO-1"}
    t = FunctionalTask(TaskMetadata("demo", "save_order", produces=("doc",), read_only=False),
                       real_write, "save_order")
    decision = agent.run([t], "doc", Actor(), approved=True)   # 不抛
    assert decision.approved and decision.executed == ["save_order"]
    assert calls["n"] == 1                                      # 写只执行一遍，不因留痕故障重跑
    assert agent.decisions[-1] is decision                     # 内存留痕不丢


def test_agent_approval_gate_survives_bad_journal(tmp_path):
    """#110：审批门 raise ApprovalRequired 前的 _record 同样不得裸崩——
    rc3 决策信号（CLI 据此提示「需人工批准」）被 FileExistsError 顶成裸 traceback，
    「拒绝执行」翻成「框架坏了」。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    agent = AtlasAgent(journal=blocker / "decisions.jsonl")
    t = task("delete_doc", produces=("v",), read_only=False)
    with pytest.raises(ApprovalRequired):
        agent.run([t], "v", Actor())
    assert agent.decisions[-1].approved is False


def test_agent_success_journal_records_executed_not_empty(tmp_path):
    """#110：成功路径 journal 行必须带 executed 完成清单——旧实现留空，
    「跑完全部」与「一个没跑」的行同形（tasks 只是计划），读痕人无法确认哪些写
    Task 真实发生过；半途失败测（#85）有 executed 而成功测断言 == [] 的口径必须统一。"""
    journal = tmp_path / "decisions.jsonl"
    agent = AtlasAgent(journal=journal)
    t1 = task("create_doc", produces=("doc",), read_only=False)
    t2 = task("save_doc", ("doc",), ("saved",), read_only=False)
    decision = agent.run([t1, t2], "saved", Actor(), approved=True)
    assert decision.executed == ["create_doc", "save_doc"]
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert row["executed"] == ["create_doc", "save_doc"]      # 落盘行同口径


def test_agent_generator_exit_journaled_as_interrupted(tmp_path):
    """#129 面A：GeneratorExit 也是终止信号，留痕必须记 interrupted——旧实现内联硬编码
    (KeyboardInterrupt, SystemExit) 三元组，而 failures.CONTROL_FLOW（#114 声明
    engine/healing/recorder 同源的单一真相）含 GeneratorExit；agent 是漏改的第 4 处
    （#92 改一漏一必漂移），生成器侧关闭的跑被记成「执行失败」，终态口径与
    classify_failure(GeneratorExit)=interrupted 自相矛盾。"""
    journal = tmp_path / "decisions.jsonl"
    agent = AtlasAgent(journal=journal)
    def gen_exit(actor):
        raise GeneratorExit
    t = FunctionalTask(TaskMetadata("demo", "query", (), ("data",)), gen_exit, "query")
    with pytest.raises(GeneratorExit):
        agent.run([t], "data", Actor())
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert "interrupted" in row["reason"] and "执行失败" not in row["reason"]


def test_agent_broken_str_preserves_real_cause_and_trace(tmp_path):
    """#129 面B：业务异常 __str__ 自身崩（#126 面A 同款真实雷区）时执行循环的
    f-string `{exc}` 在 except 块里当场炸——旧实现双重损失：①用例真因 Nasty 被
    AttributeError 顶掉（#89 定性器先于被定性对象死）；②整条 Decision 随赋值失败
    一起丢——已执行写 Task（副作用真实发生）的 executed 清单无痕（#85 红线，
    比 journal 落盘失败更糟：连内存留痕都没 append）。修复后展示文本收敛
    failures.exception_message 单一真相：真因原样传播、留痕仍在、坏 __str__ 回落类型名。"""
    journal = tmp_path / "decisions.jsonl"

    class Nasty(RuntimeError):
        def __str__(self):
            raise AttributeError("detail 未初始化")

    calls = []
    def real_write(actor):
        calls.append("create_doc")
        return {"doc": "SO-1"}
    def nasty(actor):
        raise Nasty()
    t1 = FunctionalTask(TaskMetadata("demo", "create_doc", (), ("doc",)), real_write, "create_doc")
    t2 = FunctionalTask(TaskMetadata("demo", "audit_it", ("doc",), ("saved",)), nasty, "audit_it")
    agent = AtlasAgent(max_auto_risk=RiskLevel.LOW, journal=journal)
    with pytest.raises(Nasty):                          # 修复前实锤：抛的是 AttributeError
        agent.run([t1, t2], "saved", Actor())
    assert calls == ["create_doc"]                      # 写只执行一遍，留痕崩不引发重跑
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert row["executed"] == ["create_doc"]            # 已执行写 Task 可追责
    assert "止步于 audit_it" in row["reason"]
    assert "Nasty" in row["reason"]                     # 回落类型名，仍是有效归因信号
    assert agent.decisions[-1].executed == ["create_doc"]


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

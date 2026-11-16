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


def test_agent_awaiting_human_journaled_as_waiting_not_failure(tmp_path):
    """C9 补齐：agent 轨的「等人」曾被泛捕获记成「执行失败」——journal 里看不出
    该去找人还是查 bug。现按类型分流，且异常仍原样重抛（不吞、不降级成通过）。"""
    from atlas.failures import AwaitingHuman
    journal = tmp_path / "decisions.jsonl"
    agent = AtlasAgent(journal=journal)

    def need_human(actor):
        raise AwaitingHuman("等待审批人在 OA 里点通过")
    t = FunctionalTask(TaskMetadata("demo", "approve", (), ("doc",)), need_human, "approve")
    with pytest.raises(AwaitingHuman):
        agent.run([t], "doc", Actor())
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert "等待人工" in row["reason"] and "执行失败" not in row["reason"]
    assert "等待审批人在 OA 里点通过" in row["reason"], "在等什么必须可读出，不留裸终态"


def test_agent_delivery_uncertain_journaled_not_as_plain_failure(tmp_path):
    """C12 补齐：DU 记成「执行失败」会诱导操作者重放整段计划=写双跑。
    reason 必须点名「需人工核实」，且**仍不是 approved**（红线 #7：不借文案洗绿）。"""
    from atlas.failures import DeliveryUncertain
    journal = tmp_path / "decisions.jsonl"
    agent = AtlasAgent(max_auto_risk=RiskLevel.LOW, journal=journal)

    def uncertain(actor):
        raise DeliveryUncertain("API 已发出，网关无响应")
    t = FunctionalTask(TaskMetadata("demo", "save", (), ("doc",)), uncertain, "save")
    with pytest.raises(DeliveryUncertain):
        agent.run([t], "doc", Actor())
    row = json.loads(journal.read_text(encoding="utf-8").strip())
    assert "投递不确定" in row["reason"] and "人工核实" in row["reason"]
    assert "执行失败" not in row["reason"]
    assert row["approved"] is True   # 审批门当时确实放行了——留痕语义不变，只改终态文案


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


# ── CLI agent 轨显式终态（C9/C12 补齐：此前两类异常都穿透到泛捕获，rc1+「执行失败」）──

def _agent_cli(tmp_path, monkeypatch, raiser, goal="doc"):
    """跑 `atlas agent --execute`：替掉 registry/运行时，只验 rc 与 stderr 终态口径。"""
    import contextlib
    import io
    from atlas import cli as cli_module
    from atlas import runtime as runtime_mod
    monkeypatch.setenv("DEMO_URL_SIT", "https://sit.example.com")
    # 预检里的 DemoApi 文件/旧项目路径存在性与本测要验的「终态腿」无关：
    # 前者替成直通，后者按 #103 fail-fast 要求造出真目录（不造就得等登录才撞错）
    # 哨兵打在 `atlas.runtime.check_demo_api`（判据所在模块），**不是** `atlas.cli`：
    # 预检序列已收敛到 `runtime.precheck_session_inputs`（2026-11-16），cli 只 import
    # 了那一个序列函数、不再持有 `check_demo_api` 绑定——打在 cli 上会 AttributeError
    # （改造后首次全量实跑即红，正是这类哨兵该有的表现）。
    monkeypatch.setattr(runtime_mod, "check_demo_api", lambda _config: None)
    legacy = tmp_path / "legacy-suite"
    (legacy / "legacy-web").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    boom = FunctionalTask(TaskMetadata("demo", "approve", (), (goal,)), raiser, "approve")
    monkeypatch.setattr(cli_module, "core_tasks", lambda: [boom])

    class _RT:
        def __init__(self, *a, **k):
            self.actor = Actor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli_module, "DemoRuntime", _RT)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli_module.main(["--root", str(tmp_path), "agent", "--goal", goal,
                              "--execute", "--max-auto-risk", "high"])
    return rc, err.getvalue()


def test_cli_agent_awaiting_human_returns_rc3_with_waiting_line(tmp_path, monkeypatch):
    from atlas.failures import AwaitingHuman

    def raiser(actor):
        raise AwaitingHuman("等待审批人在 OA 里点通过")
    rc, err = _agent_cli(tmp_path, monkeypatch, raiser)
    assert rc == 3, "等人=rc3（与 run 轨、审批门同值），不是 rc1 失败"
    assert "WAITING-HUMAN:" in err and "OA" in err
    err.encode("cp936")   # X4：中文段在 GBK 控制台不许崩掉退出码


def test_cli_agent_delivery_uncertain_keeps_rc1_but_names_its_own_action(tmp_path, monkeypatch):
    from atlas.failures import DeliveryUncertain

    def raiser(actor):
        raise DeliveryUncertain("API 已发出，网关无响应")
    rc, err = _agent_cli(tmp_path, monkeypatch, raiser)
    assert rc == 1, "红线 #7：投递不确定仍是失败，不借新文案洗成非失败"
    assert "DELIVERY-UNCERTAIN:" in err and "网关无响应" in err
    assert "执行失败" not in err, "终端不再把 DU 混进一般失败的措辞"
    err.encode("cp936")


def test_cli_agent_ordinary_failure_escapes_without_new_markers(tmp_path, monkeypatch):
    """负向对照（防上面两测恒真）：普通异常**不得**冒出 WAITING-HUMAN/DELIVERY-UNCERTAIN。

    同时如实钉住既有形态——agent 轨没有兜底 `except Exception`，普通异常穿出 `main()`
    由进程顶层处理（与 run 轨同口径：真失败不被任何显式终态腿收编）。
    """
    import contextlib
    import io
    from atlas import cli as cli_module
    from atlas import runtime as runtime_mod
    monkeypatch.setenv("DEMO_URL_SIT", "https://sit.example.com")
    # 哨兵打在 `atlas.runtime.check_demo_api`（判据所在模块），**不是** `atlas.cli`：
    # 预检序列已收敛到 `runtime.precheck_session_inputs`（2026-11-16），cli 只 import
    # 了那一个序列函数、不再持有 `check_demo_api` 绑定——打在 cli 上会 AttributeError
    # （改造后首次全量实跑即红，正是这类哨兵该有的表现）。
    monkeypatch.setattr(runtime_mod, "check_demo_api", lambda _config: None)
    legacy = tmp_path / "legacy-suite"
    (legacy / "legacy-web").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    boom = FunctionalTask(TaskMetadata("demo", "approve", (), ("doc",)),
                          lambda actor: (_ for _ in ()).throw(RuntimeError("SIT 抖动")),
                          "approve")
    monkeypatch.setattr(cli_module, "core_tasks", lambda: [boom])

    class _RT:
        def __init__(self, *a, **k):
            self.actor = Actor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli_module, "DemoRuntime", _RT)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
            pytest.raises(RuntimeError, match="SIT 抖动"):
        cli_module.main(["--root", str(tmp_path), "agent", "--goal", "doc",
                         "--execute", "--max-auto-risk", "high"])
    assert "WAITING-HUMAN" not in err.getvalue() and "DELIVERY-UNCERTAIN" not in err.getvalue()

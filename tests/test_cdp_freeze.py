"""CDP 冻场钩子：与 legacy-suite 的 CDP_PAUSE_STEP 语义对齐，Atlas 版按 Enter 继续。"""
from atlas.engine import ExecutionEngine, cdp_maybe_pause


def test_global_flags_parse_after_subcommand():
    """文档口径 `atlas run x.yaml --mode debug` 曾被 argparse 拒收（真机冒烟抓到）：
    全局参数必须子命令前后都合法，且子位值覆盖全局位。"""
    from atlas.cli import _parser
    p = _parser()
    a = p.parse_args(["--mode", "debug", "run", "flow.yaml"])
    assert a.mode == "debug"
    b = p.parse_args(["run", "flow.yaml", "--mode", "heal"])
    assert b.mode == "heal"
    c = p.parse_args(["run", "flow.yaml", "--mode", "debug", "--root", ".", "--headed"])
    assert c.mode == "debug" and str(c.root) == "." and c.headed is True
    d = p.parse_args(["review", "--kind", "pitfall"])  # 不带全局参数也不炸
    assert d.command == "review" and d.kind == "pitfall" and not hasattr(d, "root")
    e = p.parse_args(["legacy", "--dry-run", "--headed"])  # --headed 曾与全局冲突
    assert e.headed is True and e.dry_run is True


def test_disabled_by_default(monkeypatch, capsys):
    monkeypatch.delenv("CDP_DEBUG", raising=False)
    cdp_maybe_pause("flow", 1, "open_doc_list")  # 不冻、不打印、不等待
    assert capsys.readouterr().out == ""


def test_pause_only_when_index_reaches(monkeypatch, capsys):
    monkeypatch.setenv("CDP_DEBUG", "1")
    monkeypatch.setenv("CDP_PAUSE_STEP", "3")
    cdp_maybe_pause("flow", 2, "open_doc_list")  # 未到 3：不冻
    assert capsys.readouterr().out == ""
    monkeypatch.setattr("builtins.input", lambda *a: "")
    cdp_maybe_pause("flow", 3, "click_save")
    out = capsys.readouterr().out
    assert "CDP接管点" in out and "click_save" in out
    assert "playwright-cli attach" in out and "9222" in out


def test_case_filter_matches_substring_case_insensitive(monkeypatch, capsys):
    monkeypatch.setenv("CDP_DEBUG", "1")
    monkeypatch.setenv("CDP_PAUSE_STEP", "TC2:5")
    monkeypatch.setattr("builtins.input", lambda *a: "")
    cdp_maybe_pause("save_flow_TC1", 9, "click_save")  # case 不含 TC2：放行
    assert capsys.readouterr().out == ""
    cdp_maybe_pause("save_flow_tc2", 5, "wait_save_result")  # 含 TC2（大小写不敏感）：冻
    assert "CDP接管点" in capsys.readouterr().out


def test_non_interactive_never_hangs(monkeypatch):
    """CI/管道场景 input 抛 EOFError：必须放行而不是卡死。"""
    monkeypatch.setenv("CDP_DEBUG", "1")
    monkeypatch.setenv("CDP_PAUSE_STEP", "1")
    def raise_eof(*a):
        raise EOFError()
    monkeypatch.setattr("builtins.input", raise_eof)
    cdp_maybe_pause("flow", 1, "click_save")  # 不抛出即通过


def test_malformed_step_spec_warns_but_never_freezes(monkeypatch, capsys):
    """畸形 CDP_PAUSE_STEP（非整数/空序号/纯冒号）绝不能 int() 崩流程。
    #114 面C：旧实现静默放行——调试者以为会等 attach，步骤直接跑过，案发现场错过
    （配置错误本身就该点名，与 #102/#105 假阴性同族）；但非交互零阻塞红线不变：
    警告一行后照常放行，绝不因此挂起。"""
    monkeypatch.setenv("CDP_DEBUG", "1")
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("不该等 Enter")))
    for bad in ("abc", "TC9:", ":", "1.5"):
        monkeypatch.setenv("CDP_PAUSE_STEP", bad)
        cdp_maybe_pause("TC9_flow", 999, "click_save")  # 即使序号足够也不冻（step_str 无效）
        out = capsys.readouterr().out
        assert "CDP警告" in out and bad in out   # 点名具体坏值
        assert "CDP接管点" not in out            # 但绝不冻场


def test_engine_freezes_before_perform(tmp_path, monkeypatch):
    """冻场必须在步骤执行前：挂掉后就没有案发现场了。"""
    from atlas.actor import Actor
    from atlas.core import Channel, FunctionalTask, TaskMetadata
    monkeypatch.setenv("CDP_DEBUG", "1")
    monkeypatch.setenv("CDP_PAUSE_STEP", "1")
    order = []
    def fake_input(*a):
        order.append("pause")
        return ""
    monkeypatch.setattr("builtins.input", fake_input)
    task = FunctionalTask(
        TaskMetadata(domain="demo", intent="探针", produces=("x",),
                     channel=Channel.UI, read_only=True),
        lambda actor: order.append("perform") or {"x": 1}, "probe_task")
    engine = ExecutionEngine(__import__("atlas.config", fromlist=["AtlasConfig"]).AtlasConfig(tmp_path),
                             case_id="flow")
    engine.execute(Actor(), task)
    assert order == ["pause", "perform"]

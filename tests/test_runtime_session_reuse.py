"""pf-5 会话复用测试：`atlas run` 多 flow 一次登录串行跑完 + 单 flow 路径锁步。

四条验收线（对应任务 pf-5 的自验要求）：
① 多 flow 编排语义——一次 Chromium launch + 一次登录（整批一个 DemoRuntime，单个
  with 包住全部 flow）、每 flow 独立 runs.jsonl 记录（行形态与单 flow 逐字节一致）、
  失败即停并点名剩余 flow、AwaitingHuman 走既有 rc 3 通道、跨 flow 不共享变量产出；
② 变异取证锚点——`test_multi_flow_one_login_serial_*` 断言「runtime 实例数 == 1 ∧
  __enter__ == 1 ∧ __exit__ == 1」：把「一次登录复用」改坏（每 flow 重开 runtime/
  重新登录）该组测必红（本波已实做变异：cli._run_multi_flows 循环内逐 flow 重开
  DemoRuntime → 本文件 4 条测红 → 还原后全绿）；
③ 负向对照——env/account 声明冲突的多 flow 必须 rc 2 点名拒绝（不开浏览器、不登录、
  runs.jsonl 零落盘），绝不静默用错环境/账号跑真实系统；
④ 单 flow 锁步——单 flow 参数路径与历史行为逐字节一致：同样的 DemoRuntime 构造参数、
  同样的调用序列、runs.jsonl 恰一行且键序逐键一致（与 HEAD 对照由 git show 佐证：
  run 分支单 flow 代码一字未动，仅 nargs 列表还原为单个 Path）。

替身口径：DemoRuntime 被替身替换（CLI 分支只消费 registry()/provider/actor/
start_flow/生命周期），flow 执行走真实 dsl.run + 真实 RunRecorder + 真实
ExecutionEngine——runs.jsonl 的行是真落盘的，不是替身编的。
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

from atlas import cli as cli_module
from atlas.actor import Actor
from atlas.cli import _case_timeout_child_argv, _parser, main
from atlas.config import AtlasConfig
from atlas.core import FunctionalTask, TaskMetadata
from atlas.dsl import TaskRegistry
from atlas.failures import AwaitingHuman
from atlas.runtime import resolve_account

# recorder.close 的 record 字面量键序（passed 行：reason 空=不写键、version 三元组在位）
_RUN_LINE_KEYS = ["run_id", "case", "env", "mode", "status", "started_at",
                  "finished_at", "steps", "version"]


# 替身任务集：验证（cli._full_registry）与执行（runtime.registry()）必须同一批名字，
# 否则 flow 在预检期就撞「未注册的 Task」。真实 _full_registry 的全部注册保留——
# 空 steps 的 flow 照常走真实校验，任务步只在替身集上扩展。
_REAL_FULL_REGISTRY = cli_module._full_registry


def _stub_registry() -> TaskRegistry:
    registry = _REAL_FULL_REGISTRY()
    registry.register("make_v", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "make_v", produces=("v",)),
        lambda actor: {"v": 1}, "make_v"))
    # guard_no_v：flow 2 用它证明 flow 1 的产出键没泄漏进自己的 ctx（隔离证据）
    registry.register("guard_no_v", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "guard_no_v"),
        lambda actor: (_ for _ in ()).throw(AssertionError("ctx 泄漏：flow 1 的 v 还在"))
        if "v" in actor.ctx else {}, "guard_no_v"))
    registry.register("boom", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "boom"),
        lambda actor: (_ for _ in ()).throw(RuntimeError("boom")), "boom"))
    registry.register("wait_human", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "wait_human"),
        lambda actor: (_ for _ in ()).throw(AwaitingHuman("需要人来确认中间步骤")),
        "wait_human"))
    return registry


class _FakeRuntime:
    """替身 DemoRuntime：记录构造参数与生命周期事件；registry/provider/actor/start_flow
    与真实 DemoRuntime 的消费面同形。flow 执行交给真实 dsl.run。"""

    instances: list = []

    def __init__(self, config, *, login=True, account=None, ou=None, division_code=None,
                 create_quota=0, language=None):
        self.config = config
        self.kwargs = {"login": login, "account": account, "ou": ou,
                       "division_code": division_code, "create_quota": create_quota,
                       "language": language}
        self.enter_count = 0
        self.exit_count = 0
        self._actor = Actor("fake")
        self.exit_seen_run_lines = None   # __exit__ 时 runs.jsonl 已落行数（时序证据）
        _FakeRuntime.instances.append(self)

    def registry(self) -> TaskRegistry:
        return _stub_registry()

    @property
    def provider(self):
        return None

    @property
    def actor(self):
        return self._actor

    def start_flow(self) -> None:
        self._actor.ctx.clear()   # 与真实 DemoRuntime.start_flow 同语义

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, *exc):
        self.exit_count += 1
        runs = self.config.reports_dir / "runs.jsonl"
        self.exit_seen_run_lines = (len(runs.read_text(encoding="utf-8").splitlines())
                                    if runs.exists() else 0)
        return False


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """run 分支预检的最小离线环境：base_url/DemoApi 路径/legacy 路径全指 tmp_path
    虚构资产（预检只做存在性/形态检查，不发起请求）；DemoRuntime 换成替身。"""
    (tmp_path / ".atlas-root").write_text("", encoding="utf-8")
    api_root = tmp_path / "api"
    (api_root / "scripts").mkdir(parents=True)
    (api_root / "scripts" / "demo_api.py").write_text("# stub\n", encoding="utf-8")
    legacy = tmp_path / "legacy"
    (legacy / "legacy-web").mkdir(parents=True)
    monkeypatch.setenv("DEMO_URL_SIT", "http://demo.test/sit")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api_root))
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setattr(cli_module, "DemoRuntime", _FakeRuntime)
    monkeypatch.setattr(cli_module, "_full_registry", _stub_registry)
    _FakeRuntime.instances = []
    return tmp_path


def _write_flow(root: Path, name: str, **extra) -> Path:
    flow = {"id": name, "steps": []}
    flow.update(extra)
    path = root / f"{name}.yaml"
    path.write_text(yaml.safe_dump(flow, allow_unicode=True), encoding="utf-8")
    return path


def _runs(root: Path) -> list:
    text = (root / "reports" / "runs.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _effective_account(root: Path) -> str:
    return resolve_account(AtlasConfig.from_root(root), None)


# ── ④ 单 flow 锁步：行为与历史逐字节一致 ─────────────────────────────────────

def test_single_flow_lockstep_shape_and_lifecycle(run_env):
    """单 flow 参数：同一构造参数、一次进入/退出、恰一行 runs.jsonl、键序逐键一致。"""
    f1 = _write_flow(run_env, "only")
    rc = main(["--root", str(run_env), "run", str(f1)])
    assert rc == 0
    assert len(_FakeRuntime.instances) == 1
    runtime = _FakeRuntime.instances[0]
    assert runtime.kwargs == {"login": True, "account": None, "ou": None,
                              "division_code": None, "create_quota": 0, "language": None}
    assert runtime.enter_count == 1 and runtime.exit_count == 1
    records = _runs(run_env)
    assert len(records) == 1
    record = records[0]
    assert record["case"] == "only"
    assert record["env"] == "sit" and record["mode"] == "run" and record["status"] == "passed"
    assert list(record.keys()) == _RUN_LINE_KEYS, "runs.jsonl 行形态锁步（键序=recorder 落盘序）"
    assert record["version"]["atlas"] and record["version"]["knowledge"]
    assert record["version"]["flow"]["kind"] == "source"


def test_single_flow_declaration_keys_still_ignored(run_env):
    """单 flow 带 env/account 声明键：与历史一致照常跑（声明闸只管多 flow 面）。

    这是「默认路径逐字节不变」红线的负向探针——若单 flow 突然开始校验声明，
    这条测当场红。"""
    f1 = _write_flow(run_env, "legacy_single", env="uat-eu", account="someone_else")
    rc = main(["--root", str(run_env), "run", str(f1)])
    assert rc == 0
    assert len(_FakeRuntime.instances) == 1 and _FakeRuntime.instances[0].enter_count == 1
    assert _runs(run_env)[0]["status"] == "passed"


def test_case_timeout_child_argv_single_flow_unchanged(run_env):
    """--case-timeout 的子进程 argv（单 flow）逐字不变：`atlas run <flow>` 形态，
    仅 --case-timeout 被剔除（防监督递归）。多 flow 与 --case-timeout 在入口互斥，
    故该构造器永远只见单 flow 形态。"""
    args = _parser().parse_args(
        ["run", "f.yaml", "--case-timeout", "30", "--account", "ops"])
    args.flow = args.flow[0]   # 与 run 分支的列表还原同一步（构造器自身不做这件事）
    argv = _case_timeout_child_argv(args, Path("/r"))
    assert argv[:5] == [sys.executable, "-m", "atlas", "run", "f.yaml"]
    assert "--case-timeout" not in argv


# ── ① 多 flow 编排：一次登录、独立记录、退出时序 ─────────────────────────────

def test_multi_flow_one_login_serial_records_exit_last(run_env, capsys):
    """三条 flow：一个 runtime、一次进入/退出、三条独立 runs.jsonl 记录（按传入序、
    行形态与单 flow 逐键一致），浏览器在全部 flow 落盘**之后**才关闭。"""
    f1 = _write_flow(run_env, "alpha")
    f2 = _write_flow(run_env, "beta")
    f3 = _write_flow(run_env, "gamma")
    rc = main(["--root", str(run_env), "run", str(f1), str(f2), str(f3)])
    assert rc == 0
    assert len(_FakeRuntime.instances) == 1, "整个批次一个 DemoRuntime（=一次 launch）"
    runtime = _FakeRuntime.instances[0]
    assert runtime.enter_count == 1, "一次登录（变异取证锚点：每 flow 重开 runtime 必红）"
    assert runtime.exit_count == 1, "会话在全部 flow 结束后统一关闭（恰一次）"
    assert runtime.kwargs["login"] is True, "登录判定沿用既有 should_login 口径"
    records = _runs(run_env)
    assert [r["case"] for r in records] == ["alpha", "beta", "gamma"]
    assert all(list(r.keys()) == _RUN_LINE_KEYS for r in records), "行形态与单 flow 逐键一致"
    assert runtime.exit_seen_run_lines == 3, "__exit__ 时三条记录都已落盘（退出在全部 flow 后）"
    err = capsys.readouterr().err
    assert "flow 1/3" in err and "flow 3/3" in err, "串行进度逐条点名"


def test_multi_flow_ctx_not_shared_across_flows(run_env):
    """跨 flow 不共享变量产出：flow 1 产出 v，flow 2 的守卫步证明 ctx 已重置——
    泄漏即 flow 2 失败（静默拿上一条的 {{doc_no}} 跑真实系统是 #7 同红线）。"""
    f1 = _write_flow(run_env, "producer", steps=[{"task": "make_v"}])
    f2 = _write_flow(run_env, "guard", steps=[{"task": "guard_no_v"}])
    rc = main(["--root", str(run_env), "run", str(f1), str(f2)])
    assert rc == 0
    records = _runs(run_env)
    assert [r["case"] for r in records] == ["producer", "guard"]
    assert records[0]["steps"][0]["ok"] is True
    assert records[1]["steps"][0]["ok"] is True, "flow 2 看不到 flow 1 的产出键"


def test_multi_flow_no_login_skips_account_gate(run_env):
    """--no-login 多 flow：不登录（should_login=False）、不查空账号闸门，照常串行。"""
    f1 = _write_flow(run_env, "n1")
    f2 = _write_flow(run_env, "n2")
    rc = main(["--root", str(run_env), "run", str(f1), str(f2), "--no-login"])
    assert rc == 0
    runtime = _FakeRuntime.instances[0]
    assert runtime.kwargs["login"] is False
    assert runtime.enter_count == 1 and len(_runs(run_env)) == 2


# ── ③ 负向对照：声明冲突必须点名拒绝，不开浏览器不落盘 ────────────────────────

def test_multi_flow_env_conflict_between_flows_named_rejection(run_env, capsys):
    f1 = _write_flow(run_env, "a_sit", env="sit")
    f2 = _write_flow(run_env, "b_uat", env="uat-eu")
    rc = main(["--root", str(run_env), "run", str(f1), str(f2)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "b_uat" in err and "uat-eu" in err and "sit" in err, "点名 flow 与两侧值"
    assert _FakeRuntime.instances == [], "拒绝在开浏览器/登录之前"
    assert not (run_env / "reports" / "runs.jsonl").exists(), "零落盘"


def test_multi_flow_env_conflict_with_cli_env_named_rejection(run_env, capsys):
    """两条 flow 声明同一个 env，但与命令行实际 env 不一致——同样点名拒绝
    （一次登录只对应一个环境，声明与实际不符=静默错环境）。"""
    f1 = _write_flow(run_env, "a_uat", env="uat-eu")
    f2 = _write_flow(run_env, "b_uat", env="uat-eu")
    rc = main(["--root", str(run_env), "run", str(f1), str(f2)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "a_uat" in err and "uat-eu" in err and "sit" in err
    assert _FakeRuntime.instances == []


def test_multi_flow_account_conflict_named_rejection(run_env, capsys):
    effective = _effective_account(run_env)
    f1 = _write_flow(run_env, "a_ok", account=effective)
    f2 = _write_flow(run_env, "b_other", account=effective + "-x")
    rc = main(["--root", str(run_env), "run", str(f1), str(f2)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "b_other" in err and effective + "-x" in err and effective in err
    assert _FakeRuntime.instances == []


def test_multi_flow_declaration_shape_rejected(run_env, capsys):
    """坏形态声明（数字/布尔/容器/空串）当场点名——不猜、不静默忽略。"""
    for bad in (123, True, ["sit"], ""):
        _FakeRuntime.instances = []
        f1 = _write_flow(run_env, "a_bad", env=bad)
        f2 = _write_flow(run_env, "b_plain")
        rc = main(["--root", str(run_env), "run", str(f1), str(f2)])
        assert rc == 2, f"坏形态 {bad!r} 必须拒绝"
        err = capsys.readouterr().err
        assert "a_bad" in err, f"坏形态 {bad!r} 必须点名 flow"
        assert _FakeRuntime.instances == []


def test_multi_flow_matching_declarations_pass(run_env):
    """正向对照：声明与实际一致（env=sit、account=解析结果）的多 flow 照常跑——
    闸拦的是冲突，不是声明本身。"""
    effective = _effective_account(run_env)
    f1 = _write_flow(run_env, "p1", env="sit", account=effective)
    f2 = _write_flow(run_env, "p2", env="sit", account=effective)
    rc = main(["--root", str(run_env), "run", str(f1), str(f2)])
    assert rc == 0
    assert len(_runs(run_env)) == 2


def test_multi_flow_case_timeout_rejected(run_env, capsys):
    """--case-timeout 与多 flow 互斥：per-case 看护按每条 flow 一个子进程实现，
    塞一个子进程会把整批一起强杀——点名拒绝，不静默降级成批次级超时。"""
    f1 = _write_flow(run_env, "t1")
    f2 = _write_flow(run_env, "t2")
    rc = main(["--root", str(run_env), "run", str(f1), str(f2), "--case-timeout", "60"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--case-timeout" in err
    assert _FakeRuntime.instances == []


# ── ① 失败即停：剩余 flow 点名、会话仍统一关闭、终态口径同单 flow ─────────────

def test_multi_flow_stops_on_first_failure_names_pending(run_env, capsys):
    """flow 2 执行失败：立即停止、flow 3 未执行并在 stderr 点名、原异常原样传播、
    失败 flow 照常留 failed 记录、会话仍恰好关闭一次（回收链不断）。"""
    f1 = _write_flow(run_env, "ok_first")
    f2 = _write_flow(run_env, "bad_middle", steps=[{"task": "boom"}])
    f3 = _write_flow(run_env, "never")
    with pytest.raises(RuntimeError):
        main(["--root", str(run_env), "run", str(f1), str(f2), str(f3)])
    records = _runs(run_env)
    assert [r["case"] for r in records] == ["ok_first", "bad_middle"]
    assert records[0]["status"] == "passed" and records[1]["status"] == "failed"
    runtime = _FakeRuntime.instances[0]
    assert runtime.exit_count == 1, "失败后 __exit__ 恰一次（浏览器/回收链照常收尾）"
    assert runtime.exit_seen_run_lines == 2, "退出时已落两条记录"
    err = capsys.readouterr().err
    assert "never" in err, "未执行的 flow 必须点名（不静默少跑）"


def test_multi_flow_awaiting_human_rc3(run_env, capsys):
    """flow 2 等人：停止批次、走既有 WAITING-HUMAN / rc 3 通道（等人不是失败）、
    waiting 行照常落盘（带 reason）、flow 3 未执行点名。"""
    f1 = _write_flow(run_env, "pre_ok")
    f2 = _write_flow(run_env, "needs_human", steps=[{"task": "wait_human"}])
    f3 = _write_flow(run_env, "after")
    rc = main(["--root", str(run_env), "run", str(f1), str(f2), str(f3)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "WAITING-HUMAN" in err and "after" in err
    records = _runs(run_env)
    assert [r["case"] for r in records] == ["pre_ok", "needs_human"]
    assert records[1]["status"] == "waiting" and records[1].get("reason")
    assert _FakeRuntime.instances[0].exit_count == 1


def test_multi_flow_broken_flow_file_rejected_before_browser(run_env, capsys):
    """第二条 flow 结构坏：全部 flow 先 load+validate（登录/开浏览器之前 fail fast），
    rc 2 点名坏文件——绝不「跑完第一条才发现第二条编译不过」。"""
    f1 = _write_flow(run_env, "good_one")
    bad = run_env / "bad.yaml"
    bad.write_text(yaml.safe_dump({"id": "bad", "steps": [{"task": "no_such_task"}]}),
                   encoding="utf-8")
    f3 = _write_flow(run_env, "good_three")
    rc = main(["--root", str(run_env), "run", str(f1), str(bad), str(f3)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no_such_task" in err or "bad" in err
    assert _FakeRuntime.instances == []
    assert not (run_env / "reports" / "runs.jsonl").exists()

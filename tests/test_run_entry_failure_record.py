"""run 轨入口失败留痕：`DemoRuntime.__enter__` 挂掉时也必须留下一条 runs.jsonl。

## 这条修的是什么（真机抓出，2026-11-16）

`atlas run` 在 SIT 真机上失败（SSO 登录卡住、顶栏选择器 60s 超时）→ rc 1，
而 `reports/runs.jsonl` **一行都没有**。改动前（git stash）与改动后两次运行均 0 条新记录，
所以是既有缺陷，不是那波重构引入的。

成因：`RunRecorder` 由 `dsl.FlowCompiler.run()` 在**会话就绪之后**才构造。
`__enter__` 抛错时 `with` 不调 `__exit__`、`run()` 从未被调用、recorder 从未存在。

后果（具体）：`atlas summary` / `atlas allure` / `atlas report` 全看不到这次运行。
无人值守（CI/调度器）场景下，"跑过而且挂了"这件事只存在于会被轮转掉的终端日志里——
而 `atlas scheduling` 的巡检恰恰靠 runs.jsonl 判定上一条的状态。

本项目自己已登记同族问题：gap9 F-33.4 ① 的「fixture 期 EnvBlocked 不留 runs.jsonl 行」
（pytest 产物侧）。本文件修的是 **run 轨**那个更宽的形态。

## 判据（三条）

1. 记录**真的落盘**（文件里多一行，`case` 是我们跑的那个 flow）；
2. 记录**与正常行同构**——字段集取自 `RunRecorder.close()`、`fail_class` 取值域取自
   `FailureClass`。这防「读侧把这条当外来物」：`status` 不认识则计数漏，
   `fail_class` 不认识则缺陷分布多出一个假类别（比不留痕更坏——那是有痕但错）。
3. **不改行为**：异常原样 re-raise（rc 由既有腿决定），且不洗绿（不是 `passed`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas import cli
from atlas.failures import FailureClass


def _project(tmp_path: Path) -> tuple[Path, Path]:
    """最小可跑项目：有 .atlas-root / 一条 API flow / 旧仓 + DemoApi 目录（过预检用）。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".atlas-root").write_text("", encoding="utf-8")
    flow = root / "flow.yaml"
    flow.write_text("id: entry-probe\nsteps:\n  - task: api_call\n    method: GET\n"
                    "    path: /x\n", encoding="utf-8")
    legacy = tmp_path / "legacy"
    (legacy / "legacy-web").mkdir(parents=True)
    api = tmp_path / "api"
    (api / "scripts").mkdir(parents=True)
    (api / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    return root, flow


def _env(monkeypatch, tmp_path, legacy_api: tuple[Path, Path]) -> None:
    legacy, api = legacy_api
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    monkeypatch.setenv("DEMO_URL_SIT", "https://sit.example/")
    monkeypatch.setenv("ATLAS_SESSION_ACCOUNT", "someone")
    monkeypatch.setenv("ATLAS_PLUGIN", "demo")   # 显式声明，去掉回落 WARN 噪音


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_entry_failure_writes_a_run_record(tmp_path, monkeypatch, capsys):
    """核心：`__enter__` 抛错 → runs.jsonl 仍多一行，且 status 不是 passed。"""
    root, flow = _project(tmp_path)
    _env(monkeypatch, tmp_path, (tmp_path / "legacy", tmp_path / "api"))

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            raise TimeoutError("Page.wait_for_selector: Timeout 60000ms exceeded")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli, "DemoRuntime", _Boom)
    runs = root / "reports" / "runs.jsonl"

    with pytest.raises(TimeoutError):
        cli.main(["--root", str(root), "run", str(flow)])

    rows = _rows(runs)
    assert len(rows) == 1, f"入口失败必须留下一条记录；实得 {len(rows)} 条（stderr={capsys.readouterr().err!r}）"
    rec = rows[0]
    assert rec["case"] == "entry-probe" or "flow.yaml" in rec["case"]
    assert rec["status"] == "failed"
    assert rec["status"] != "passed", "环境阻塞/超时绝不能被洗成 passed（红线 #7）"


def test_entry_failure_record_has_the_same_shape_as_a_normal_row(tmp_path, monkeypatch):
    """形态同构：字段集与 `RunRecorder.close()` 一致，`fail_class` 在 `FailureClass` 域内。

    这条是「有痕但读侧不认」的防线。若哪天 close() 加了必需键而本函数没跟，
    读侧就会把它当残缺行——那种坏法比不留痕更难察觉。
    """
    from atlas.recorder import RunRecorder

    # 正常行的字段集（拿真 recorder 落一条空的作基准）
    normal_rec = RunRecorder("probe", "sit", tmp_path / "baseline.jsonl", "run")
    normal_rec.close(status="passed")
    normal_keys = set(_rows(tmp_path / "baseline.jsonl")[0])

    root, flow = _project(tmp_path)
    _env(monkeypatch, tmp_path, (tmp_path / "legacy", tmp_path / "api"))

    class _Boom:
        def __init__(self, *a, **kw): pass
        def __enter__(self):
            raise PermissionError("DETACHED: 登录被拒")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli, "DemoRuntime", _Boom)
    with pytest.raises(PermissionError):
        cli.main(["--root", str(root), "run", str(flow)])

    rec = _rows(root / "reports" / "runs.jsonl")[0]
    missing = normal_keys - set(rec)
    assert not missing, f"入口失败行缺少正常行有的键（读侧会当残缺行）：{sorted(missing)}"
    extra = set(rec) - normal_keys
    assert not extra, f"入口失败行多出正常行没有的键（读侧不认识）：{sorted(extra)}"
    # fail_class 必须是既有词汇，不得新造
    legal = {c.value for c in FailureClass}
    for step in rec["steps"]:
        if "fail_class" in step:
            assert step["fail_class"] in legal, (
                f"fail_class={step['fail_class']!r} 不在 FailureClass 取值域 {sorted(legal)}")


def test_entry_failure_does_not_change_rc_or_swallow_the_exception(tmp_path, monkeypatch):
    """不改行为：异常原样 re-raise（rc 由既有腿决定），不被留痕逻辑吞成 rc 0。"""
    root, flow = _project(tmp_path)
    _env(monkeypatch, tmp_path, (tmp_path / "legacy", tmp_path / "api"))

    class _Boom:
        def __init__(self, *a, **kw): pass
        def __enter__(self):
            raise TimeoutError("SSO 未通过")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli, "DemoRuntime", _Boom)
    with pytest.raises(TimeoutError, match="SSO 未通过"):
        cli.main(["--root", str(root), "run", str(flow)])


def test_awaiting_human_in_enter_does_not_double_write(tmp_path, monkeypatch):
    """`AwaitingHuman` 走 C9 显态腿（rc 3），**不**由本函数落盘——避免重复行。

    这是「留痕」与既有「等人不是失败」口径的交界：等人已在 dsl 侧有 waiting 行语义，
    入口处再补一条 failed 会把它错记成失败。
    """
    from atlas.failures import AwaitingHuman

    root, flow = _project(tmp_path)
    _env(monkeypatch, tmp_path, (tmp_path / "legacy", tmp_path / "api"))

    class _Human:
        def __init__(self, *a, **kw): pass
        def __enter__(self):
            raise AwaitingHuman("等待审批人在 OA 里点通过")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli, "DemoRuntime", _Human)
    rc = cli.main(["--root", str(root), "run", str(flow)])
    assert rc == 3, "等人在 run 轨是 rc 3（既有口径），不能被留痕逻辑改掉"
    rows = _rows(root / "reports" / "runs.jsonl")
    assert rows == [], f"等人不该在本层落 failed 行；实得 {rows}"


def test_write_failure_does_not_replace_the_real_cause(tmp_path, monkeypatch, capsys):
    """落盘自身失败（reports 目录不可写）只打 WARN，真因照旧抛出。

    真因优先：磁盘满/权限坏不该把「登录为什么挂」替换成「写文件为什么挂」。
    """
    root, flow = _project(tmp_path)
    _env(monkeypatch, tmp_path, (tmp_path / "legacy", tmp_path / "api"))

    class _Boom:
        def __init__(self, *a, **kw): pass
        def __enter__(self):
            raise TimeoutError("真因：登录超时")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli, "DemoRuntime", _Boom)

    def _explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "_record_entry_failure", _explode)
    with pytest.raises(TimeoutError, match="真因：登录超时"):
        cli.main(["--root", str(root), "run", str(flow)])
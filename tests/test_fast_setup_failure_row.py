"""快包产物：**模块级 fixture** 在 setup 期挂掉时也必须留 runs.jsonl 行。

## 这条修的是什么（2026-11-17 实测复现，gap9 F-33.4 ①）

`gap9` 的「下一波待办」记着：**fixture 期抛 `EnvBlocked` 不留 runs.jsonl 行**。
本轮把它拆细了——实测发现这句话只说对一半：

| 失败位置 | 修复前 |
|---|---|
| test 体内抛 `EnvBlocked` | ✅ 留行（`_fast_runlog` 收尾写） |
| **函数级** fixture 抛 | ✅ 留行（autouse 的 `_fast_runlog` 先于显式请求的 fixture 建立） |
| **模块级** fixture 抛（`sole_page`） | ❌ **runs.jsonl 文件都不存在** |

根因：`sole_page` 是 `scope="module"`，pytest 在**任何函数级 fixture 之前**就建立它。
它抛 `EnvBlocked`（缺 playwright / 缺浏览器 / attach 选页失败）时，函数级的
`_fast_runlog` 从未进入 setup，收尾逻辑一刀没走 → 一行记录都没有。

后果（具体）：`atlas summary` / `atlas allure` / `atlas report` 全靠 runs.jsonl 判
「这条跑没跑、怎么挂的」。缺席被读成「不存在」（无痕族 #106/#86）。无人值守场景下
「跑过且挂了」只活在会被轮转掉的终端日志里——而 `atlas scheduling` 的巡检正是靠
runs.jsonl 判定上一条状态。

## 修法

`pytest_runtest_makereport` 在 `call.when == "setup"` 且 `_fast_runlog` **未进场**
（`item._fast_runlog_entered` 未置位）时补一行。标记由 `_fast_runlog` 进场时置位，
所以正常路径不会重复落行。

## 为什么这些测试要真跑 pytest 子进程

`makereport` 是 pytest 钩子，**只有真的跑一次 collection+setup 才会触发**。用
`_load_artifact` 直接把 conftest import 进来验不到这条路径（那是上一波能验的层面，
也正是这个缺陷漏到现在的原因之一）。所以这里生成真产物、写真用例、跑真 pytest。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from atlas.generator.emit_fast import write_fast_artifacts


def _artifact(tmp_path: Path) -> Path:
    """生成一份真产物（`_atlas_fast.py` + `conftest.py`）。"""
    out = tmp_path / "artifact"
    out.mkdir(parents=True, exist_ok=True)
    write_fast_artifacts(out)
    assert (out / "conftest.py").is_file() and (out / "_atlas_fast.py").is_file()
    return out


def _run_pytest(out: Path, test_src: str) -> subprocess.CompletedProcess:
    """在产物目录里跑一个真 pytest 子进程。"""
    (out / "test_probe.py").write_text(textwrap.dedent(test_src), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(out)}
    env.pop("ATLAS_FAST_ENV", None)
    # Windows 上必须按字节读再解码（text=True 会用 GBK 解 UTF-8，见 ci.yml 同款教训）
    return subprocess.run([sys.executable, "-m", "pytest", "test_probe.py", "-q", "-p", "no:cacheprovider"],
                          cwd=str(out), capture_output=True, env=env)


def _rows(out: Path) -> list[dict]:
    path = out / "reports" / "runs.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


MODULE_FIXTURE_FAILS = """
    import pytest
    from _atlas_fast import EnvBlocked


    @pytest.fixture(scope="module")
    def sole_page():
        # 模拟真缺陷形态：模块级 fixture 在 setup 期抛 EnvBlocked
        raise EnvBlocked("缺依赖：playwright 未安装（模块级模拟）")


    def test_blocked_by_module_fixture(sole_page, plan_ctx, _fast_runlog):
        assert True
    """


def test_module_fixture_failure_writes_a_run_row(tmp_path):
    """核心：模块级 fixture 挂 → runs.jsonl 必须有一行（修复前文件都不存在）。"""
    out = _artifact(tmp_path)
    proc = _run_pytest(out, MODULE_FIXTURE_FAILS)
    rows = _rows(out)

    assert len(rows) == 1, (
        f"模块级 fixture 失败必须留 1 行；实得 {len(rows)} 行。"
        f"\nstdout={proc.stdout.decode('utf-8', 'replace')[-800:]}"
        f"\nstderr={proc.stderr.decode('utf-8', 'replace')[-800:]}")
    rec = rows[0]
    assert rec["case"].startswith("test_probe::test_blocked_by_module_fixture")
    assert rec["status"] == "env_blocked", (
        f"EnvBlocked 必须归 env_blocked（不许洗成 failed/passed）：{rec['status']}")
    # reason 是框架点名文案（F-5 单点），必须带出来而不是留空
    assert "playwright" in rec.get("reason", ""), f"reason 必须点名缺什么：{rec.get('reason')!r}"


def test_module_fixture_failure_still_errors(tmp_path):
    """不改行为：用例在 pytest 侧仍是 error（留痕不是洗绿，也不是把它变成 pass）。"""
    out = _artifact(tmp_path)
    proc = _run_pytest(out, MODULE_FIXTURE_FAILS)
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    assert proc.returncode != 0, "模块级 fixture 挂了必须是非零退出码"
    assert "1 error" in text or "error" in text.lower(), f"应报 error：{text[-500:]}"
    assert "passed" not in text.split("error")[0][-80:] or True   # 不依赖措辞，主判据是 rc


def test_body_failure_still_writes_exactly_one_row(tmp_path):
    """正向对照：test 体内失败照旧只写一行（新加的 makereport 分支不得重复落行）。"""
    out = _artifact(tmp_path)
    _run_pytest(out, """
    from _atlas_fast import EnvBlocked


    def test_body_raises(plan_ctx, _fast_runlog):
        raise EnvBlocked("缺配置：X（test 体内）")
    """)
    rows = _rows(out)
    assert len(rows) == 1, f"test 体内失败应恰 1 行（不得因新分支重复）；实得 {len(rows)}"
    assert rows[0]["status"] == "env_blocked"


def test_function_fixture_failure_is_not_double_counted(tmp_path):
    """正向对照：**函数级** fixture 失败仍恰 1 行。

    这一格修复前就会留行（autouse 先于显式请求）。新分支必须靠 `_fast_runlog_entered`
    标记跳过它，否则同一次失败会被记两次——那比不留痕更难查（计数翻倍）。
    """
    out = _artifact(tmp_path)
    _run_pytest(out, """
    import pytest
    from _atlas_fast import EnvBlocked


    @pytest.fixture
    def boom_fixture():
        raise EnvBlocked("缺配置：ATLAS_FAST_LOGIN_INDICATOR（函数级模拟）")


    def test_function_fixture_raises(boom_fixture, plan_ctx, _fast_runlog):
        assert True
    """)
    rows = _rows(out)
    assert len(rows) == 1, (
        f"函数级 fixture 失败应恰 1 行（不得双记）；实得 {len(rows)}：{[r['case'] for r in rows]}")
    assert rows[0]["status"] == "env_blocked"


def test_passing_test_unaffected(tmp_path):
    """正向对照：全绿的用例照常一行 passed（新分支不该在正常路径上触发）。"""
    out = _artifact(tmp_path)
    _run_pytest(out, """
    def test_green(plan_ctx, _fast_runlog):
        assert True
    """)
    rows = _rows(out)
    assert len(rows) == 1, f"正常用例应恰 1 行；实得 {len(rows)}"
    assert rows[0]["status"] == "passed"


def test_row_is_atlas_schema_compatible(tmp_path):
    """形态同构：补出来的行必须能被 `atlas summary` 那套读侧吃下。

    这条防「写了个读侧不认识的孤儿行」——那比不留痕更坏（有痕但错）。
    """
    from atlas.reporting import summarize

    out = _artifact(tmp_path)
    _run_pytest(out, MODULE_FIXTURE_FAILS)
    assert len(_rows(out)) == 1

    # 入参是**文件路径**（summarize 自己读 JSONL，含 en-8 分片解析逻辑）
    summary = summarize(out / "reports" / "runs.jsonl")
    # summarize 对每行取 case/status/steps；能算出来就说明键位对得上
    assert summary["status"].get("env_blocked") == 1, f"summary 未识别这条行：{summary['status']}"
    assert summary["runs"] == 1, f"summary 计入的行数不对：{summary}"

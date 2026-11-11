"""执行层补短板回归：per-case 超时（--case-timeout）与 runs.jsonl 进程安全写入。

全离线：超时用真子进程真时钟验证看护语义（毫秒级 sleep 而非 mock 时钟——被测对象
就是「到点杀进程」本身，mock 掉 Popen 就等于没测）；并发写用多进程真实互斥验证行级
完整性（交错残行只有真并发才暴露）。
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from atlas.cli import _case_timeout_child_argv, _kill_process_tree, _supervise_case
from atlas.config import AtlasConfig
from atlas.recorder import RunRecorder


# ── 短板1：per-case 超时 ─────────────────────────────────────────────────────

def _make_config(tmp_path: Path) -> AtlasConfig:
    # frozen dataclass：直接用 object.__setattr__ 绕过（测试替身，仅消费 root/env/mode）
    config = AtlasConfig.__new__(AtlasConfig)          # 跳过 __post_init__ 的 env 白名单
    object.__setattr__(config, "root", tmp_path)
    object.__setattr__(config, "env", "sit")
    object.__setattr__(config, "mode", "run")
    return config


class _Args:
    """run 子命令参数的离线替身：只带 _case_timeout_child_argv 消费的键。"""
    flow = "flow.yaml"
    account = None
    ou = None
    language = None
    division = None
    bu = ""
    no_login = True
    create_quota = 0


def test_case_timeout_child_argv_omits_case_timeout_flag():
    """核心不变量：子进程 argv 里绝不能再现 --case-timeout（会递归派生孙进程），
    其余 run 相关参数逐项透传。"""
    args = _Args()
    args.account = "tester"
    args.bu = "BU-A"
    args.create_quota = 2
    argv = _case_timeout_child_argv(args, Path("/proj"))
    text = " ".join(argv)
    assert "--case-timeout" not in text
    assert "flow.yaml" in argv
    assert "--root" in argv and str(Path("/proj")) in argv
    assert "--account" in argv and "tester" in argv
    assert "--bu" in argv and "BU-A" in argv
    assert "--create-quota" in argv and "2" in argv
    assert "--no-login" in argv
    assert argv[0] == sys.executable and "-m" in argv and "atlas" in argv
    assert "run" in argv


def test_supervise_case_normal_exit_passes_child_rc_through(tmp_path):
    """子进程正常结束：父进程原样透传退出码，不补记任何 runs.jsonl 行（零涟漪）。"""
    config = _make_config(tmp_path)
    rc = _supervise_case([sys.executable, "-c", "import sys; sys.exit(7)"],
                         config, {"id": "case_ok"}, 60)
    assert rc == 7
    assert not (tmp_path / "reports" / "runs.jsonl").exists()


def test_supervise_case_times_out_records_timeout_and_kills(tmp_path):
    """超时主链路：到点整树强杀 + runs.jsonl 补记 timeout 终态 + rc 124（不是 failed，
    对齐外部 timeout 语义）。子进程故意忽略 SIGTERM 形态的体面退出请求（Windows 无
    SIGTERM，这里用无限 sleep 卡住，模拟 2026-11-05 的 567s Page crashed 卡死）。"""
    child = [sys.executable, "-c", "import time; time.sleep(600)"]
    config = _make_config(tmp_path)
    started = time.monotonic()
    rc = _supervise_case(child, config, {"id": "case_stuck"}, 2)
    elapsed = time.monotonic() - started
    assert rc == 124                                    # 超时是 timeout，不是 failed(1)
    assert elapsed < 30                                 # 不等子进程自己睡满 600s
    runs = tmp_path / "reports" / "runs.jsonl"
    assert runs.is_file()
    record = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
    assert record["status"] == "timeout"                # 终态：TIMEOUT
    assert record["case"] == "case_stuck"
    assert record["mode"] == "run"
    assert record["steps"] == []                        # 子进程被打断，无步骤证据——如实留空


def test_supervise_case_timeout_row_goes_to_atlas_runs_dir(tmp_path, monkeypatch):
    """ATLAS_RUNS_DIR 并发生效面：超时补记也分流到独立目录（并发进程各写各的）。"""
    monkeypatch.setenv("ATLAS_RUNS_DIR", "runs-worker1")
    config = _make_config(tmp_path)
    rc = _supervise_case([sys.executable, "-c", "import time; time.sleep(600)"],
                         config, {"id": "case_stuck"}, 1)
    assert rc == 124
    assert (tmp_path / "reports" / "runs-worker1" / "runs.jsonl").is_file()
    assert not (tmp_path / "reports" / "runs.jsonl").exists()


def test_supervise_case_timeout_honors_env_override_root(tmp_path, monkeypatch):
    """超时补记落在 ATLAS_ROOT 指向的 root（AtlasConfig.from_root 解析口径），不赌 CWD。
    直接走 _supervise_case + 手工 config，等价模拟 CLI 侧 config.reports_dir 的取值。"""
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "proj"
    (nested / "reports").mkdir(parents=True)
    config = _make_config(nested)
    rc = _supervise_case([sys.executable, "-c", "import time; time.sleep(600)"],
                         config, {"id": "c"}, 1)
    assert rc == 124
    assert (nested / "reports" / "runs.jsonl").is_file()


def test_run_parser_accepts_case_timeout():
    from atlas.cli import _parser
    args = _parser().parse_args(["run", "flow.yaml", "--case-timeout", "600"])
    assert args.case_timeout == 600
    default = _parser().parse_args(["run", "flow.yaml"])
    assert default.case_timeout is None                 # 缺省不限=兼容


def test_run_rejects_nonpositive_case_timeout(tmp_path, capsys):
    """--case-timeout 0/负数是输入错误：rc 2 一行 stderr，不开浏览器不派子进程。"""
    from atlas.cli import main
    flow = tmp_path / "flow.yaml"
    flow.write_text("steps:\n  - task: noop\n", encoding="utf-8")
    rc = main(["run", str(flow), "--case-timeout", "0",
               "--root", str(tmp_path)])
    assert rc == 2
    assert "case-timeout" in capsys.readouterr().err


# ── 短板2：runs.jsonl 进程安全 ──────────────────────────────────────────────

def test_recorder_close_locks_and_appends_whole_line(tmp_path):
    """单进程基准不翻错：close 仍整行追加、status/键形态与旧实现一致。"""
    recorder = RunRecorder("case", "sit", tmp_path / "runs.jsonl")
    recorder.close("passed")
    lines = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["status"] == "passed" and record["case"] == "case"


def test_recorder_close_multiprocess_appends_never_interleave(tmp_path):
    """多进程真实并发：N 个进程各自跑 M 条 close，终文件必须行行合法 JSON 且条数精确
    ——裸 open("a") 在 Windows 上多进程交错会产出残行（read 侧 json.loads 必炸）。
    子进程内再各开一个线程锁内写（文件锁的互斥边界就是进程+线程）。"""
    procs = 4
    per_proc = 25
    script = (
        "import json, sys, threading\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, r'%s')\n"
        "from atlas.recorder import RunRecorder\n"
        "path = Path(sys.argv[1])\n"
        "def write(_i):\n"
        "    RunRecorder('case', 'sit', path).close('passed')\n"
        "threads = [threading.Thread(target=write, args=(i,)) for i in range(%d)]\n"
        "[t.start() for t in threads]\n"
        "[t.join() for t in threads]\n"
    ) % (str(Path(__file__).resolve().parent.parent / "src"), per_proc)
    procs_list = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path / "runs.jsonl")],
                                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                  for _ in range(procs)]
    codes = [p.wait(timeout=120) for p in procs_list]
    assert codes == [0] * procs, "子进程自身失败（导入/异常）会让本测失去并发意义"
    lines = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == procs * per_proc               # 一条不少
    statuses = {json.loads(line)["status"] for line in lines}   # 行行可解析且合法
    assert statuses == {"passed"}


def test_recorder_runs_dir_env_redirects_writes(tmp_path, monkeypatch):
    """ATLAS_RUNS_DIR：绝对/相对路径都分流写独立目录；未设置时行为逐字不变。"""
    target = tmp_path / "worker-a"
    monkeypatch.setenv("ATLAS_RUNS_DIR", str(target))
    RunRecorder("case", "sit", tmp_path / "reports" / "runs.jsonl").close("passed")
    assert (target / "runs.jsonl").is_file()
    assert not (tmp_path / "reports" / "runs.jsonl").exists()

    monkeypatch.setenv("ATLAS_RUNS_DIR", "worker-b")     # 相对值按传入 path 的父目录解析
    RunRecorder("case", "sit", tmp_path / "reports" / "runs.jsonl").close("passed")
    assert (tmp_path / "reports" / "worker-b" / "runs.jsonl").is_file()

    monkeypatch.delenv("ATLAS_RUNS_DIR")
    RunRecorder("case", "sit", tmp_path / "reports" / "runs.jsonl").close("passed")
    assert (tmp_path / "reports" / "runs.jsonl").is_file()   # 未设置=旧行为


def test_recorder_blank_runs_dir_env_ignored(tmp_path, monkeypatch):
    """ATLAS_RUNS_DIR 置空串/纯空白视同未设置（不把空值解析成 cwd 当目录）。"""
    monkeypatch.setenv("ATLAS_RUNS_DIR", "   ")
    RunRecorder("case", "sit", tmp_path / "reports" / "runs.jsonl").close("passed")
    assert (tmp_path / "reports" / "runs.jsonl").is_file()


def test_recorder_close_write_failure_still_retryable(tmp_path):
    """既有契约不回退：目标路径是目录 → open 失败 OSError 且 _closed 不置位（可重试），
    文件锁不改变「写失败不谎报已关闭」语义。"""
    runs = tmp_path / "runs.jsonl"
    runs.mkdir()
    recorder = RunRecorder("case", "sit", runs)
    with recorder.step("s"):
        pass
    with pytest.raises(OSError):
        recorder.close()
    assert recorder._closed is False


def test_kill_process_tree_survives_dead_process():
    """杀已退出/不存在的进程不得抛：清理失败吞掉，绝不顶掉超时主链路的记账。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    _kill_process_tree(proc)                            # 不抛即通过

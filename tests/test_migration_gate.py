"""C3 迁移验收三件套 · migration_gate 行为测（design_c3 §8.3/§8.4；纯离线）。

覆盖：解析三形态（FAILED/ERROR/collected，含 CRLF）、compare 三态、静默空跑 rc1、
无基线 rc2、坏基线点名、X2 argv 纪律（值永不进 shell、无 shell=True）、超时报红、
端到端 mini pytest 树（3 测 1 红：录基线→破坏→点名→恢复→rc0）。
禁跳测试/降标准制造绿灯——这里连 runner 造假路径都只用于**离线打桩**，判据本身零放宽。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import migration_gate as mg  # noqa: E402

# ── 1. 解析（§8.3）─────────────────────────────────────────────────────────

SAMPLE_LF = """\
...
collected 40 items

tests/test_a.py .F                                                       [100%]
tests/test_b.py E
FAILED tests/test_a.py::test_red - AssertionError: boom
FAILED tests/test_a.py::test_red2 - ValueError: x
ERROR tests/test_b.py::test_broken - CollectionError
3 failed, 36 passed, 1 error in 5.02s
"""

SAMPLE_CRLF = SAMPLE_LF.replace("\n", "\r\n")


def test_parse_failed_error_and_collected():
    failures, collected = mg.parse_pytest_output(SAMPLE_LF)
    assert failures == {"tests/test_a.py::test_red", "tests/test_a.py::test_red2",
                        "tests/test_b.py::test_broken"}
    assert collected == 40


def test_parse_crlf_sample():
    failures, collected = mg.parse_pytest_output(SAMPLE_CRLF)
    assert "tests/test_a.py::test_red" in failures
    assert collected == 40


def test_parse_collected_zero():
    failures, collected = mg.parse_pytest_output(
        "collected 0 items\nno tests ran in 0.01s\n")
    assert failures == set() and collected == 0


def test_parse_no_collected_line_is_none():
    """pytest 内部崩（无 collected 行）→ None，调用侧不得当成 0 静默放过。"""
    failures, collected = mg.parse_pytest_output("INTERNAL ERROR\n")
    assert collected is None


# ── 2. compare 三态（§8.3）─────────────────────────────────────────────────

def test_compare_subset_ok():
    r = mg.compare({"a::1", "a::2"}, {"a::1"})
    assert r["subset_ok"] and r["new_failures"] == [] and r["resolved"] == ["a::2"]


def test_compare_new_failure_blocks():
    r = mg.compare({"a::1"}, {"a::1", "b::2"})
    assert not r["subset_ok"] and r["new_failures"] == ["b::2"]


def test_compare_empty_to_empty_still_subset():
    assert mg.compare(set(), set())["subset_ok"]


# ── 3. X2 argv 纪律：命令为 list、无 shell=True、env 钉 utf-8 ──────────────

def test_run_pytest_uses_argv_list_no_shell(monkeypatch):
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return subprocess.CompletedProcess(argv, 0, "collected 1 items\n", "")

    monkeypatch.setattr(mg.subprocess, "run", fake_run)
    failures, collected, rc = mg.run_pytest(Path("C:/fake/root"), ["-k", "not slow"])
    assert isinstance(captured["argv"], list)
    assert "shell" not in captured["kw"] or captured["kw"]["shell"] is False
    assert captured["kw"]["encoding"] == "utf-8"
    assert captured["kw"]["errors"] == "replace"
    assert captured["kw"]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "-k" in captured["argv"] and "not slow" in captured["argv"]
    assert captured["kw"]["timeout"]  # 无超时可被挂死（C3-B 教训）——必在位
    assert collected == 1 and rc == 0


# ── 4. rc 口径：空跑/无基线/坏基线/超时（§8.3）────────────────────────────

def _stub_runner(collected=100, failures=()):
    def runner(root, extra, timeout):
        return set(failures), collected, 1 if failures else 0
    return runner


def test_collected_zero_is_red_not_green(tmp_path, capsys):
    mg.write_baseline(tmp_path / "bl.json", {"a::1"}, 10, tmp_path)
    rc = mg.main(["--root", str(tmp_path), "--baseline", str(tmp_path / "bl.json")],
                 runner=_stub_runner(collected=0))
    out = capsys.readouterr().out
    assert rc == 1 and "GATE-SKIP-EMPTY" in out and "静默空跑" in out


def test_missing_baseline_is_rc2_with_guidance(tmp_path, capsys):
    rc = mg.main(["--root", str(tmp_path)], runner=_stub_runner())
    out = capsys.readouterr().out
    assert rc == 2 and "GATE-RED" in out and "--record-baseline" in out


def test_bad_baseline_json_named_not_silently_empty(tmp_path, capsys):
    p = tmp_path / "bl.json"
    p.write_text("{ not json", encoding="utf-8")
    rc = mg.main(["--root", str(tmp_path), "--baseline", str(p)],
                 runner=_stub_runner())
    out = capsys.readouterr().out
    assert rc == 2 and "先重录基线" in out


def test_bad_baseline_shape_named(tmp_path):
    p = tmp_path / "bl.json"
    p.write_text(json.dumps({"failures": "not-a-list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="形态"):
        mg.load_baseline(p)


def test_pytest_crash_rc_named_not_trusted(tmp_path, capsys):
    mg.write_baseline(tmp_path / "bl.json", set(), 10, tmp_path)

    def runner(root, extra, timeout):
        return set(), None, 3  # 内部错误：无 collected 行
    rc = mg.main(["--root", str(tmp_path), "--baseline", str(tmp_path / "bl.json")],
                 runner=runner)
    out = capsys.readouterr().out
    assert rc == 1 and "GATE-RED" in out


def test_timeout_is_red(tmp_path, capsys):
    def runner(root, extra, timeout):
        raise subprocess.TimeoutExpired(["pytest"], 1)
    mg.write_baseline(tmp_path / "bl.json", set(), 10, tmp_path)
    rc = mg.main(["--root", str(tmp_path), "--baseline", str(tmp_path / "bl.json")],
                 runner=runner)
    out = capsys.readouterr().out
    assert rc == 1 and "挂死" in out


# ── 5. 基线写盘纪律（X5：尾断行 + 原子；敏感目录 reports/ 已 gitignore）────

def test_baseline_shape_and_trailing_newline(tmp_path):
    p = tmp_path / "reports" / "migration" / "f.json"
    mg.write_baseline(p, {"b::2", "a::1"}, 42, tmp_path)
    raw = p.read_text(encoding="utf-8")
    assert raw.endswith("\n"), "X5：尾断行，append 不黏残行"
    data = json.loads(raw)
    assert data["failures"] == ["a::1", "b::2"]  # 排序稳定、可 diff
    assert data["count"] == 2 and data["collected"] == 42
    assert {"generated_at", "platform", "python"} <= set(data)
    assert not list(p.parent.glob("*.tmp"))


# ── 6. 端到端（§8.4：tmp 迷你 pytest 树，真 subprocess、真解析）────────────

_TREE = '''\
def test_green1():
    assert True

def test_green2():
    assert True

def test_known_red():
    assert False, "迁移前已知红（基线内）"
'''

_BROKEN = _TREE.replace("def test_green2():\n    assert True",
                        "def test_green2():\n    assert False")


def _mini_tree(root: Path, text: str) -> None:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_mini.py").write_text(text, encoding="utf-8")


def test_end_to_end_subset_lifecycle(tmp_path, capsys):
    _mini_tree(tmp_path, _TREE)
    # ① 录基线：1 红
    rc = mg.main(["--root", str(tmp_path), "--record-baseline"])
    assert rc == 0
    bl = tmp_path / "reports" / "migration" / "failures-baseline.json"
    assert json.loads(bl.read_text(encoding="utf-8"))["failures"] == \
        ["tests/test_mini.py::test_known_red"]
    # ② 破坏 1 绿 → 新增失败点名 rc1（禁「跳过它」——gate 没有 skip 通道）
    _mini_tree(tmp_path, _BROKEN)
    capsys.readouterr()
    rc = mg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1 and "GATE-RED" in out
    assert "tests/test_mini.py::test_green2" in out  # 逐条点名
    # ③ 恢复 → rc0，且明打「子集成立≠迁移成功」
    _mini_tree(tmp_path, _TREE)
    capsys.readouterr()
    rc = mg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "GATE-OK" in out and "真机取证另跑" in out


def test_end_to_end_resolved_is_listed_not_blocked(tmp_path, capsys):
    """基线里的失败变绿 = 改善：列出、放行（减少≠破损）。"""
    _mini_tree(tmp_path, _BROKEN)  # 2 红
    rc = mg.main(["--root", str(tmp_path), "--record-baseline"])
    assert rc == 0
    _mini_tree(tmp_path, _TREE)  # 修掉 green2 → 1 红（⊆ 2 红基线）
    capsys.readouterr()
    rc = mg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "RESOLVED" in out


def test_end_to_end_empty_tree_silent_run_red(tmp_path, capsys):
    """真跑 `-k` 打错的静默空跑（pytest rc=5 形态）→ GATE-SKIP-EMPTY rc1。"""
    _mini_tree(tmp_path, _TREE)
    mg.write_baseline(tmp_path / "reports" / "migration" / "failures-baseline.json",
                      set(), 3, tmp_path)
    rc = mg.main(["--root", str(tmp_path),
                  "--pytest-arg=-k", "--pytest-arg=nonexistent_selector_xyz"])
    out = capsys.readouterr().out
    assert rc == 1 and "GATE-SKIP-EMPTY" in out

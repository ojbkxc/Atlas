"""gap11 #19：`export-pytest --compat` 与 `legacy --fast` 的 CLI 面测（离线，零子进程）。

并行在途两件（沿 #4/#11 已确认的处置先例）：
- fastplugin（support-impl #17）未落地：注入的模块名钉在 `LegacyAdapter.FAST_PLUGIN_MODULE`
  常量单点，测试用「常量 + 字面」双锁——形状漂移字面断言当场红，返工只改一行；
  本文件不 import atlas.fastplugin（命令组装与真实可 import 是两件事，离线测只核前者）。
- compat 渲染（emit-impl #18）未落地：CLI 非默认值才带 kw 透传（fast_kw 先例），
  compat=none 的存量调用形态逐字不变；#18 落地后签名锁步测自动激活。
"""
from __future__ import annotations
import os
import subprocess as sp
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.cli import main
from atlas.config import AtlasConfig
from atlas.legacy import LegacyAdapter

FLOW_YAML = ("id: export_demo\nsteps:\n"
             "  - task: api_call\n    path: /api/list\n    method: GET\n"
             "    save_as: resp_1_list\n")
OLD_NODE_DEFAULT = "testcases/demo_sit_regression/test_demo_sit_regression.py"


@pytest.fixture
def legacy_adapter(tmp_path, monkeypatch):
    # 真实项目形态：web 子目录叫 iservice（旧坑的原现场）
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    monkeypatch.delenv("ATLAS_LEGACY_TEST_NODE", raising=False)
    project = tmp_path / "legacy-suite"
    (project / "iservice").mkdir(parents=True)
    return LegacyAdapter(AtlasConfig(tmp_path), project)


def _atlas_import_root() -> str:
    import atlas
    return str(Path(atlas.__file__).resolve().parent.parent)


# ===================== 1. pytest_command：注入形态与零涟漪 =====================

def test_pytest_command_without_fast_byte_identical(legacy_adapter):
    # 口径③：不给 --fast，命令拼装逐字节不变（与改造前的字面形态直接对表）
    assert legacy_adapter.pytest_command() == [
        sys.executable, "-m", "pytest", OLD_NODE_DEFAULT, "-v", "-s", "--headless"]


def test_pytest_command_fast_injects_plugin(legacy_adapter):
    plain = legacy_adapter.pytest_command()
    fast = legacy_adapter.pytest_command(fast=True)
    assert fast[:5] == [sys.executable, "-m", "pytest", "-p", "atlas.fastplugin"]
    assert fast[5:] == plain[3:]   # 除 -p 前插外逐字不变；-p 在收集参数前（插件早注册）


def test_pytest_command_fast_combines_headed_and_keyword(legacy_adapter):
    # 口径③：--fast 与 --headed/--keyword/node 自由组合，无互斥
    cmd = legacy_adapter.pytest_command(node="tc.py", headed=True,
                                        keyword="test_tc7_", fast=True)
    assert cmd == [sys.executable, "-m", "pytest", "-p", "atlas.fastplugin",
                   "tc.py", "-v", "-s", "--headed", "-k", "test_tc7_"]


# ===================== 2. fast_child_env：PYTHONPATH 绝对化 + 开关透传 =====================

def test_fast_child_env_absolutizes_relative_pythonpath(legacy_adapter):
    # 旧坑本体：`PYTHONPATH=src` 在子进程 cwd=iservice 下解析成不存在的目录。
    # 判据=相对项全部转绝对，且 atlas import 根在场（fastplugin 住在 atlas 包里）。
    env = legacy_adapter.fast_child_env(base={"PYTHONPATH": "src:lib"})
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts, "PYTHONPATH 不应为空"
    assert all(Path(p).is_absolute() for p in parts), parts
    assert _atlas_import_root() in parts


def test_fast_child_env_preserves_absolute_entries(legacy_adapter, tmp_path):
    keep = str(tmp_path / "already-absolute")
    env = legacy_adapter.fast_child_env(base={"PYTHONPATH": keep})
    assert keep in env["PYTHONPATH"].split(os.pathsep)


def test_fast_child_env_passes_switches_through(legacy_adapter):
    # env 开关透传：FAST_ENV_SWITCHES 清单键必须原样可达子进程（base 由元组程序化
    # 构造——G11-1 增补键时零改测自动覆盖，清单漂了这里自己长出来）
    base = {key: f"v-{i}" for i, key in enumerate(LegacyAdapter.FAST_ENV_SWITCHES)}
    base["PATH"] = "keep-me"
    env = legacy_adapter.fast_child_env(base=base)
    for key in LegacyAdapter.FAST_ENV_SWITCHES:
        assert env[key] == base[key], key
    assert env["PATH"] == "keep-me"   # 只加不改写其他继承项


# ===================== 2b. #36：--fast 默认只开截图抑制（等待垫片=显式 opt-in） =====================

def test_fast_child_env_defaults_only_enable_shot_suppression(legacy_adapter):
    # #30 曾代置位 ATLAS_FAST_WAITS=mask；#36（2026-11-10 真机三轮 A/B + 8 组档位探针）
    # 撤回等待腿的默认置位：mask/mask+xhr 间歇前移失败点、mutation 零节省。
    # 截图抑制保留默认（G11-2 非破坏差分已判成立，且它不改变任何等待）。
    for empty in ({}, {"ATLAS_FAST_WAITS": "", "ATLAS_FAST_NO_STEP_SHOTS": ""}):
        env = legacy_adapter.fast_child_env(base=dict(empty))
        assert not env.get("ATLAS_FAST_WAITS"), \
            "等待垫片不得默认置位（#36：会间歇改变行为）"
        assert env["ATLAS_FAST_NO_STEP_SHOTS"] == "1"


def test_fast_child_env_explicit_wins_including_off(legacy_adapter):
    # 显式恒优先（含设成 off/0）：默认只补 unset，不覆盖人的决定（#115 不静默改行为）
    env = legacy_adapter.fast_child_env(base={"ATLAS_FAST_WAITS": "off",
                                              "ATLAS_FAST_NO_STEP_SHOTS": "0"})
    assert env["ATLAS_FAST_WAITS"] == "off"
    assert env["ATLAS_FAST_NO_STEP_SHOTS"] == "0"


def test_fast_defaults_only_cover_named_keys(legacy_adapter):
    # 不默认 WAITS/STATS/MIN_MS/ANCHOR：等待腿是显式 opt-in（#36）；stats 随 waits 开
    # 自然产出，调参键留人自决（红线 #12）。
    env = legacy_adapter.fast_child_env(base={})
    for key in ("ATLAS_FAST_WAITS", "ATLAS_FAST_WAITS_STATS", "ATLAS_FAST_MIN_MS",
                "ATLAS_FAST_SETTLE_ANCHOR"):
        assert not env.get(key), key


def test_fast_defaults_attribute_surface_is_stable():
    # #31 交界契约：产物侧（emit_compat._compat_fast_defaults）在生成期**读本属性**插值，
    # 名字/形态/值是跨文件契约面——我侧重构若改名或换函数，这格先红（对方 fail-loud
    # 点名之外再加一把自家门）。规则本身（unset/空值补、显式含 off 优先）由行为格钉，
    # 本格只钉「公开面形状」；改值=合法演进（产物随重生成带上），改名=需通报 emit-impl。
    # #36：清单只剩截图抑制——等待垫片不得回潮成默认（那会把间歇前移的档位发给使用者）。
    assert LegacyAdapter.FAST_DEFAULTS == (("ATLAS_FAST_NO_STEP_SHOTS", "1"),)


def test_run_pytest_without_fast_never_injects_defaults(legacy_adapter, monkeypatch):
    # 承诺的负极：不带 --fast，连默认开关都不得出现（逐字节=旧行为的 env 面）
    for key in LegacyAdapter.FAST_ENV_SWITCHES:
        monkeypatch.delenv(key, raising=False)
    calls = _capture_spawn(monkeypatch)
    legacy_adapter.run_pytest()
    for key in ("ATLAS_FAST_WAITS", "ATLAS_FAST_NO_STEP_SHOTS"):
        assert key not in calls["env"], key


# ===================== 3. run_pytest：fast 腿接线（fake spawn，不真起进程） =====================

def _capture_spawn(monkeypatch, returncode=0):
    calls: dict = {}

    def fake_run(command, *, cwd, env):
        calls["command"] = list(command)
        calls["cwd"] = str(cwd)
        calls["env"] = dict(env)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(sp, "run", fake_run)   # run_pytest 函数内 import 的同一模块对象
    return calls


def test_run_pytest_fast_wires_plugin_and_env(legacy_adapter, monkeypatch):
    calls = _capture_spawn(monkeypatch)
    monkeypatch.setenv("ATLAS_FAST_WAITS", "bounded")
    assert legacy_adapter.run_pytest(fast=True) == 0
    assert calls["command"][:5] == [sys.executable, "-m", "pytest", "-p",
                                    "atlas.fastplugin"]
    parts = calls["env"]["PYTHONPATH"].split(os.pathsep)
    assert all(Path(p).is_absolute() for p in parts if p)
    assert calls["env"]["ATLAS_FAST_WAITS"] == "bounded"   # 开关经 os.environ 透传
    assert calls["cwd"].endswith("iservice")


def test_run_pytest_without_fast_env_unchanged(legacy_adapter, monkeypatch):
    # 口径③：fast=False 连 env 都不动（PYTHONPATH 原样、无 atlas 根注入）
    monkeypatch.setenv("PYTHONPATH", "src")
    calls = _capture_spawn(monkeypatch)
    assert legacy_adapter.run_pytest() == 0
    assert "-p" not in calls["command"]
    assert calls["env"].get("PYTHONPATH") == "src"   # 未绝对化 = 逐字节继承旧行为


def test_run_pytest_fast_without_waits_env_prints_notice(legacy_adapter, monkeypatch,
                                                         capsys):
    """#36：--fast 默认只开截图抑制——等待腿关着时必须**出声**（#30 的教训反向用一次：
    「以为开了其实没开」要靠可核面拦，不靠人记）。显式设了档位就不打这条 NOTE。"""
    for key in ("ATLAS_FAST_WAITS",):
        monkeypatch.delenv(key, raising=False)
    _capture_spawn(monkeypatch)
    assert legacy_adapter.run_pytest(fast=True) == 0
    out = capsys.readouterr().out
    assert "NOTE: 等待垫片未启用" in out
    assert "ATLAS_FAST_WAITS=mask|mask+xhr|mutation" in out
    # 正向对照：显式 opt-in 时不打 NOTE（不是恒真文案）
    monkeypatch.setenv("ATLAS_FAST_WAITS", "mask")
    _capture_spawn(monkeypatch)
    assert legacy_adapter.run_pytest(fast=True) == 0
    assert "NOTE: 等待垫片未启用" not in capsys.readouterr().out
    # 负向对照：不带 --fast 不打 NOTE
    monkeypatch.delenv("ATLAS_FAST_WAITS", raising=False)
    _capture_spawn(monkeypatch)
    assert legacy_adapter.run_pytest() == 0
    assert "NOTE: 等待垫片未启用" not in capsys.readouterr().out


# ===================== 4. CLI dry-run 可核性 =====================

@pytest.fixture
def legacy_cli(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-root").write_text("", encoding="utf-8")
    project = tmp_path / "legacy-suite"
    (project / "iservice").mkdir(parents=True)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(project))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    return repo


def _out(capsys) -> str:
    return capsys.readouterr().out


def test_legacy_dry_run_plain_shape_unchanged(legacy_cli, capsys):
    assert main(["--root", str(legacy_cli), "legacy", "--dry-run"]) == 0
    out = _out(capsys).splitlines()
    assert len(out) == 2 and "-p" not in out[0]      # 旧输出形态：命令 + cwd 两行
    assert out[1].startswith("cwd=")


def test_legacy_dry_run_fast_shows_injection_and_switches(legacy_cli, capsys, monkeypatch):
    monkeypatch.setenv("ATLAS_FAST_WAITS", "bounded")
    assert main(["--root", str(legacy_cli), "legacy", "--dry-run", "--fast"]) == 0
    lines = _out(capsys).splitlines()
    assert "-p atlas.fastplugin" in lines[0]           # 注入后的完整命令可核
    env_line = next(l for l in lines if l.startswith("PYTHONPATH="))
    parts = env_line[len("PYTHONPATH="):].split(os.pathsep)
    assert all(Path(p).is_absolute() for p in parts if p)
    assert _atlas_import_root() in parts               # 展示的就是绝对化后的值
    joined = "\n".join(lines)
    # 三态可核：explicit 原样点名值 / default 点名「--fast 置位」/ unset 显式 unset
    # ——旧「set/unset」二态会把默认已开显示成 unset，用户看不出到底开没开
    assert "ATLAS_FAST_WAITS=bounded (explicit)" in joined
    assert "ATLAS_FAST_NO_STEP_SHOTS=1 (default --fast)" in joined
    assert "ATLAS_FAST_WAITS_STATS=unset" in joined
    # #36：等待腿不再代置位——未设 env 时 dry-run 必须显示 unset（不是 (default --fast)）
    monkeypatch.delenv("ATLAS_FAST_WAITS", raising=False)
    assert main(["--root", str(legacy_cli), "legacy", "--dry-run", "--fast"]) == 0
    joined2 = "\n".join(_out(capsys).splitlines())
    assert "ATLAS_FAST_WAITS=unset" in joined2
    assert "ATLAS_FAST_WAITS=mask (default --fast)" not in joined2


# ===================== 5. export-pytest --compat =====================

@pytest.fixture
def flow_file(tmp_path):
    p = tmp_path / "flow.yaml"
    p.write_text(FLOW_YAML, encoding="utf-8")
    return p


def test_compat_without_fast_rc2_and_no_output(flow_file, tmp_path, capsys):
    out = tmp_path / "pkg"
    assert main(["export-pytest", str(flow_file), "-o", str(out),
                 "--compat", "legacy-suite"]) == 2
    err = capsys.readouterr().err
    assert "--compat" in err and "--fast" in err   # 一行点名组合矛盾
    assert "Traceback" not in err                  # rc2 口径，不裸 traceback
    assert not out.exists()                        # 不隐式改行为，也不出半成品


def test_compat_gate_delegates_to_shared_judgment():
    # c9b 裁决：组合判据唯一位在 `emit_compat.compat_combo_problem`——CLI 闸**委托**它，
    # 自己零条件逻辑。钉「委托」而非钉文本：源码含判据位调用、不含内联组合判断的
    # 标志字面（`not fast`/`!= "none"`）——旧测钉 inline if 文本正是让两面各自绿互盲的帮凶。
    import inspect
    from atlas.cli import _compat_gate, _export_pytest
    gate_src = inspect.getsource(_compat_gate)
    assert "compat_combo_problem(" in gate_src
    assert "not fast" not in gate_src and '!= "none"' not in gate_src
    assert inspect.getsource(_export_pytest).count("_compat_gate(") == 1


def test_compat_gate_rejects_unknown_shape_behaviorally():
    # 行为等值的主证：未知形态过去被 CLI 内联闸放行、被生成器面拒——委托后必须抛
    # （分叉修好前这格红）。argparse choices 挡命令行挡不住程序化直调，判据不能只活在闸后。
    import pytest as _pytest
    from atlas.cli import _compat_gate
    for bad in ("bogus", "", "LEGACY-SUITE", "legacy_suite"):
        with _pytest.raises(ValueError) as ei:
            _compat_gate(bad, True)
        assert "未知 --compat" in str(ei.value)


@pytest.mark.parametrize("compat", ["none", "legacy-suite", "bogus", ""])
@pytest.mark.parametrize("fast", [False, True])
def test_compat_gate_equals_generator_across_eight_combos(compat, fast):
    # 跨入口等值（xcheck 八组合扫的常驻化）：CLI 闸与生成器判据对同一组合的
    # 放行/拒绝与消息必须逐格一致——两绿互盲的形态从此有测钉死。
    from atlas.cli import _compat_gate
    from atlas.generator.emit_compat import compat_combo_problem
    problem = compat_combo_problem(compat, fast)
    if problem:
        with pytest.raises(ValueError) as ei:
            _compat_gate(compat, fast)
        assert str(ei.value) == problem
    else:
        _compat_gate(compat, fast)   # 不抛即等值放行


def test_compat_kwarg_passthrough_only_when_set(flow_file, tmp_path, monkeypatch):
    import atlas.generator.emit_pytest as ep
    calls: list[dict] = []

    def _stub(**kw):
        calls.append(kw)
        return [str(Path(kw["out_dir"]) / "test_stub.py")]

    monkeypatch.setattr(ep, "emit_pytest_package", _stub)
    assert main(["export-pytest", str(flow_file), "-o", str(tmp_path / "a"),
                 "--fast", "--compat", "legacy-suite"]) == 0
    assert calls[-1].get("compat") == "legacy-suite"
    assert calls[-1].get("fast") is True
    # none 缺省：kw 不带（存量调用形态逐字不变 #115，emit 侧缺省 none 语义等价）
    assert main(["export-pytest", str(flow_file), "-o", str(tmp_path / "b"),
                 "--fast"]) == 0
    assert "compat" not in calls[-1]
    assert main(["export-pytest", str(flow_file), "-o", str(tmp_path / "c")]) == 0
    assert "compat" not in calls[-1] and calls[-1].get("fast") is False


def test_compat_invalid_value_rc2(flow_file):
    # choices 面：非法值由 argparse 拒——退出码 2 与 CLI rc 口径一致
    with pytest.raises(SystemExit) as ei:
        main(["export-pytest", str(flow_file), "--compat", "jmeter"])
    assert ei.value.code == 2


def test_compat_help_mentions_pairing_literal(flow_file, monkeypatch, capsys):
    # 任务钉的字面：help 须出现 `--compat legacy-suite`
    monkeypatch.setenv("COLUMNS", "300")
    with pytest.raises(SystemExit):
        main(["export-pytest", "--help"])
    assert "--compat legacy-suite" in _out(capsys)


def test_emit_pytest_package_compat_signature_lockstep():
    # #18 落地后自动激活：emit 侧 compat 参存在且缺省 "none"（CLI 条件透传的等价前提）
    import inspect
    from atlas.generator.emit_pytest import emit_pytest_package
    params = inspect.signature(emit_pytest_package).parameters
    if "compat" not in params:
        pytest.skip("待 #18 落地后激活（skip≠通过，#8 对账会点名此格）")
    assert params["compat"].default == "none"

import pytest
from atlas.legacy import LegacyStep


def test_from_module_reports_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        LegacyStep.from_module(tmp_path / "nope.py", 1)


def test_from_module_reports_missing_step(tmp_path):
    case = tmp_path / "case_x.py"
    case.write_text("def step_1(page, ctx):\n    return 1\n", encoding="utf-8")
    with pytest.raises(AttributeError, match="step_2"):
        LegacyStep.from_module(case, 2)


def test_from_module_loads_step_and_keeps_page_ctx_signature(tmp_path):
    case = tmp_path / "case_y.py"
    case.write_text(
        "def step_1(page, ctx):\n"
        "    ctx['seen'] = page\n"
        "    return 'ok'\n", encoding="utf-8")
    step = LegacyStep.from_module(case, 1)

    class FakeActor:
        page = "PAGE"
        ctx = {}

    assert step.perform(FakeActor()) == "ok"
    assert step.name.endswith("step_1")


def test_legacy_adapter_case_builds_paths(tmp_path):
    """LegacyAdapter.case 构建项目根、模块根与用例 ID。"""
    from atlas.legacy import LegacyAdapter
    from atlas.config import AtlasConfig
    config = AtlasConfig(tmp_path)
    adapter = LegacyAdapter(config, project_root=tmp_path)
    case = adapter.case("testcases/test.yaml", "tc1")
    assert case.project_root == tmp_path.resolve()
    assert case.case_id == "tc1"
    assert case.module_root == tmp_path.resolve() / "legacy-web"


def test_legacy_adapter_rejects_path_outside_project(tmp_path):
    """用例路径必须在项目内，防止路径穿越。"""
    from atlas.legacy import LegacyAdapter
    from atlas.config import AtlasConfig
    config = AtlasConfig(tmp_path)
    adapter = LegacyAdapter(config, project_root=tmp_path)
    with pytest.raises(ValueError, match="项目内"):
        adapter.case("../outside.yaml", "tc1")


def test_legacy_adapter_pytest_command_builds_correct_args(tmp_path):
    """pytest_command 生成正确的 pytest 调用参数。"""
    from atlas.legacy import LegacyAdapter
    from atlas.config import AtlasConfig
    config = AtlasConfig(tmp_path)
    adapter = LegacyAdapter(config, project_root=tmp_path)
    cmd = adapter.pytest_command("test.py", headed=True, keyword="test_tc1_")
    assert "pytest" in cmd
    assert "test.py" in cmd
    assert "--headed" in cmd
    assert "-k" in cmd
    assert "test_tc1_" in cmd
    # 无 keyword 时不加 -k
    cmd2 = adapter.pytest_command("test.py", headed=False)
    assert "-k" not in cmd2
    assert "--headless" in cmd2


def test_legacy_adapter_run_pytest_returns_exit_code(tmp_path, monkeypatch):
    """run_pytest 执行子进程并返回退出码。"""
    from atlas.legacy import LegacyAdapter
    from atlas.config import AtlasConfig
    import subprocess
    config = AtlasConfig(tmp_path)
    adapter = LegacyAdapter(config, project_root=tmp_path)
    (tmp_path / "legacy-web").mkdir()
    # mock subprocess.run 避免真实 pytest 执行
    captured = {}
    class FakeResult:
        returncode = 0
    def fake_run(cmd, cwd, env):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return FakeResult()
    monkeypatch.setattr(subprocess, "run", fake_run)
    code = adapter.run_pytest("test_x.py", headed=True, keyword="test_tc1_")
    assert code == 0
    assert "pytest" in captured["cmd"]
    assert "test_x.py" in captured["cmd"]
    assert "--headed" in captured["cmd"]
    assert captured["cwd"] == tmp_path.resolve() / "legacy-web"


def test_run_pytest_reports_missing_dirs_locatable(tmp_path):
    """真实漂移：ATLAS_LEGACY_PROJECT 指错 / ATLAS_LEGACY_WEB 与旧版目录名不符
    （legacy-web vs iservice）。修复前 subprocess.run(cwd=不存在) 抛 OS 级
    `NotADirectoryError: [WinError 267]`——零配置线索，用户不知道该改哪个键。
    必须点名缺哪个目录、对应哪个环境变量。"""
    from atlas.legacy import LegacyAdapter
    from atlas.config import AtlasConfig
    config = AtlasConfig(tmp_path)
    # 根不存在
    adapter = LegacyAdapter(config, project_root=tmp_path / "no_legacy")
    with pytest.raises(FileNotFoundError, match="ATLAS_LEGACY_PROJECT"):
        adapter.run_pytest()
    # 根存在但 web 子目录漂移
    root = tmp_path / "legacy"; root.mkdir()
    adapter2 = LegacyAdapter(config, project_root=root)
    with pytest.raises(FileNotFoundError, match="ATLAS_LEGACY_WEB"):
        adapter2.run_pytest()
    (root / "legacy-web").mkdir()
    adapter2.check_exec_dir()                       # 目录到位后不误伤
    assert adapter2.exec_dir == root.resolve() / "legacy-web"


def test_cli_legacy_missing_project_rc2_not_traceback(tmp_path, monkeypatch, capsys):
    """与 overlay/recycle/review 同口径：路径类输入错误 → 一行 stderr + rc 2。
    修复前 `atlas legacy --dry-run` 对不存在的目录照样打印命令返回 0（假绿预检），
    非 dry-run 直接裸 traceback。dry-run 文档语义是「只检查路径和命令」，检查必须为真。"""
    import os
    from atlas.cli import main
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "missing_legacy"))
    assert main(["--root", str(tmp_path), "legacy", "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "旧项目根不存在" in err and "Traceback" not in err
    assert main(["--root", str(tmp_path), "legacy"]) == 2   # 非 dry-run 同样友好
    # 目录到位后 dry-run 恢复 rc 0，且打印的命令与 cwd 同源
    (tmp_path / "legacy" / "legacy-web").mkdir(parents=True)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "legacy"))
    assert main(["--root", str(tmp_path), "legacy", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "pytest" in out and f"cwd={tmp_path / 'legacy' / 'legacy-web'}" in out


def test_from_module_probe_miss_gets_attributed_import_error(tmp_path):
    """#119 面A：模块 import lib 但祖先无 lib/+conftest.py（放错目录）——旧实现裸抛
    ModuleNotFoundError: No module named 'lib'，用户分不清「探测没命中」还是「真缺 lib」。
    必须点名探测语义与 ATLAS_LEGACY_WEB 线索。兼容面（#115 教训）：不 import lib 的
    纯本地模块同目录仍合法；坏的是别的模块时原样传播不揽责。"""
    stray = tmp_path / "stray"
    stray.mkdir()
    bad = stray / "mod_lib.py"
    bad.write_text("from lib.pages import open_doc_list\n\ndef step_1(page, ctx):\n    return 1\n",
                   encoding="utf-8")
    with pytest.raises(ImportError, match="旧项目 web 根") as caught:
        LegacyStep.from_module(bad, 1)
    assert str(stray) in str(caught.value)          # 探测起点如实入文案
    assert isinstance(caught.value.__cause__, ModuleNotFoundError)

    plain = stray / "mod_plain.py"                   # 同目录不 import lib——合法，不得翻成错误
    plain.write_text("def step_1(page, ctx):\n    return 'ok'\n", encoding="utf-8")
    assert LegacyStep.from_module(plain, 1).name.endswith("step_1")

    other = stray / "mod_other.py"                  # 坏的是别的模块——原样抛不揽责
    other.write_text("import totally_missing_mod_xyz\n\ndef step_1(page, ctx):\n    return 1\n",
                     encoding="utf-8")
    with pytest.raises(ModuleNotFoundError, match="totally_missing_mod_xyz"):
        LegacyStep.from_module(other, 1)


def test_from_module_rejects_non_callable_step_at_construction(tmp_path):
    """#119 面B：step_N 存在但不可调用（`step_3 = "字符串"`）——旧实现构造期假成功，
    perform 时才 TypeError，把错误推迟到真实执行链。必须在 from_module 当场点名类型；
    可调用 step_N 正常放行（对照不误伤）。"""
    case = tmp_path / "mod_str_step.py"
    case.write_text("step_3 = '我字符串不是函数'\n", encoding="utf-8")
    with pytest.raises(TypeError, match="必须是可调用") as caught:
        LegacyStep.from_module(case, 3)
    assert "str" in str(caught.value)                # 实际类型入文案，供人定位

    good = tmp_path / "mod_fn_step.py"
    good.write_text("def step_3(page, ctx):\n    return 7\n", encoding="utf-8")
    step = LegacyStep.from_module(good, 3)

    class FakeActor:
        page = "PAGE"
        ctx = {}

    assert step.perform(FakeActor()) == 7

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

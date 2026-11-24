"""Gap1 `atlas init` 测试（§7.1）。

离线、无网络/浏览器/凭据；生成项目骨架的文件结构、pyproject 依赖口径、
env 无凭据、compile 离线通过、冲突防护、无业务字面量。
"""
import json
import re
from pathlib import Path
import pytest

from atlas.cli import main


# ---- §7.1 测试 1：生成文件清单 ----

def test_init_generates_all_files(tmp_path, capsys):
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    proj = Path(out["created"])
    for f in [".atlas-root", ".env", ".env.example", ".gitignore",
              "pyproject.toml", "README.md", "flows/smoke.yaml",
              "tests/test_smoke.py", "scripts/run_allure.py"]:
        assert (proj / f).is_file(), f"缺少 {f}"
    # tests/__init__.py 也在
    assert (proj / "tests" / "__init__.py").is_file()


# ---- §7.1 测试 2/11：pyproject 不含 allure-pytest / --alluredir ----

def test_pyproject_no_allure_pytest(tmp_path, capsys):
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    text = (tmp_path / "myproj" / "pyproject.toml").read_text(encoding="utf-8")
    assert "pytest>=8" in text
    assert "atlas-test-platform" in text
    assert "allure-pytest" not in text
    assert "--alluredir" not in text
    assert "playwright" in text
    assert "requests" in text
    assert "PyYAML" in text


# ---- §7.1 测试 3：.env 值全空无凭据 ----

def test_env_no_credentials(tmp_path, capsys):
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    proj = tmp_path / "myproj"
    for fname in [".env", ".env.example"]:
        text = (proj / fname).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            _key, value = line.split("=", 1)
            value = value.strip()
            assert value == "", f"{fname} 的 {_key} 有非空值：{value!r}"
        # 正则扫 PASSWORD= 后非空
        assert not re.search(r"PASSWORD=\S", text), f"{fname} 含非空凭据"


# ---- §7.1 测试 4：生成 .py 全部 compileall 通过 ----

def test_generated_py_compileall(tmp_path, capsys):
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    proj = tmp_path / "myproj"
    import py_compile
    for py_file in proj.rglob("*.py"):
        py_compile.compile(str(py_file), doraise=True)


# ---- §7.1 测试 5：atlas compile flows/smoke.yaml → rc 0 ----

def test_smoke_yaml_compiles(tmp_path, capsys):
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    capsys.readouterr()  # 清空 init 输出
    proj = tmp_path / "myproj"
    rc = main(["--root", str(proj), "compile", str(proj / "flows" / "smoke.yaml")])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "CallEndpoint" in out["tasks"]


# ---- §7.1 测试 6：resolve_plugin 默认仍 DemoPlugin（零回归）----

def test_resolve_plugin_defaults_to_demo(monkeypatch):
    from atlas.plugin_api import resolve_plugin
    from atlas.plugins.demo import DemoPlugin
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    assert isinstance(resolve_plugin(), DemoPlugin)


# ---- §7.1 测试 7：冲突防护 ----

def test_conflict_existing_atlas_root(tmp_path, capsys):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / ".atlas-root").write_text('atlas-root = "."\n', encoding="utf-8")
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "已是 Atlas 项目" in err


def test_conflict_non_empty_dir(tmp_path, capsys):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "somefile.txt").write_text("hello", encoding="utf-8")
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "非空" in err


# ---- §7.1 测试 8：--interactive 才读 stdin ----

def test_interactive_reads_stdin(tmp_path, monkeypatch):
    # 默认不读 stdin（CI 安全）
    rc = main(["init", "proj1", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    # --interactive 读 stdin（喂空行接受默认）
    monkeypatch.setattr("builtins.input", lambda *a: "")
    rc = main(["init", "proj2", "--system", "ics", "--module", "so",
               "--interactive", "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "proj2" / ".atlas-root").is_file()


# ---- §7.1 测试 9：无业务字面量 ----

def test_no_business_literals(tmp_path, capsys):
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    proj = tmp_path / "myproj"
    for f in proj.rglob("*"):
        if f.is_file() and f.suffix in {".py", ".yaml", ".toml", ".md", ".example", ""}:
            text = f.read_text(encoding="utf-8", errors="replace")
            assert "CUST0000001" not in text, f"{f} 含 demo 业务字面量"


# ---- §7.1 测试 10（条件）：Allure 端到端 ----
# 需 Playwright/浏览器；环境不可用时 skip，不伪装通过。

def test_allure_e2e_offline(tmp_path, capsys):
    """pytest -q → atlas allure → 至少 1 个 *-result.json。
    浏览器不可用时 skip（autonomous-execution 纪律：不伪装通过）。"""
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    proj = tmp_path / "myproj"

    # 先检查 Playwright 是否可用
    try:
        from atlas.session import BrowserSession
        from atlas.config import AtlasConfig
        config = AtlasConfig(root=proj, env="sit", mode="run")
        with BrowserSession(config) as session:
            session.page.set_content("<title>x</title>")
    except Exception as exc:
        pytest.skip(f"Playwright/浏览器不可用：{exc}")

    # 跑 pytest -q（产 runs.jsonl）
    import subprocess, sys, os
    # 骨架的 tests/ 是**独立项目**（pyproject 里 `atlas-test-platform @ file://…`），子进程
    # 靠 site-packages 里已装的 atlas；本机若没装（或 editable 指向别的 clone），失败形态
    # 与浏览器崩溃同属**环境限制**，按 autonomous-execution 纪律 skip 并说清（不伪装通过）。
    # 注意：这里只放行「本机没装/装歪」，不掩盖 rc≠0 的真实失败（下面仍 assert False）。
    import importlib.util
    spec = importlib.util.find_spec("atlas")
    if spec is None:
        pytest.skip("本机未安装 atlas（骨架子进程需要它）；CI 走 `pip install -e .` 后不触发")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          cwd=str(proj), capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(proj)})
    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        # 浏览器崩溃特征（沙箱环境敏感，#flaky）：TargetClosedError/playwright 错误
        # autonomous-execution 纪律：环境限制 skip，不伪装通过；真实失败仍 fail
        if any(k in output for k in ("TargetClosedError", "playwright._impl._errors",
                                      "browser has crashed", "Browser closed")):
            pytest.skip(f"subprocess pytest 浏览器崩溃（环境敏感）：{output[-300:]}")
        assert False, f"subprocess pytest 失败：\n{output}"
    runs = proj / "reports" / "runs.jsonl"
    assert runs.is_file()

    # atlas allure → Allure 结果集
    out_dir = tmp_path / "allure-out"
    rc3 = main(["--root", str(proj), "allure", str(runs), "--out", str(out_dir)])
    assert rc3 == 0
    results = list(out_dir.glob("atlas-*-result.json"))
    assert len(results) >= 1
    data = json.loads(results[0].read_text(encoding="utf-8"))
    assert data["status"] == "passed"


# ---- --atlas-path file:// URI ----

def test_atlas_path_file_uri(tmp_path, capsys):
    atlas_path = Path(__file__).parent.parent
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--atlas-path", str(atlas_path), "--out", str(tmp_path)])
    assert rc == 0
    text = (tmp_path / "myproj" / "pyproject.toml").read_text(encoding="utf-8")
    assert "file:///" in text
    assert "atlas-test-platform @" in text


# ---- .atlas-root 最后落盘（防半成品假象）----

def test_atlas_root_written_last(tmp_path, capsys):
    """.atlas-root 在所有其他文件之后落盘——中途失败不留「半成品却像完整项目」。"""
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    # .atlas-root 内容正确
    text = (tmp_path / "myproj" / ".atlas-root").read_text(encoding="utf-8")
    assert text == 'atlas-root = "."\n'


# ---- 开箱可装：默认自动探测 Atlas 源码根，钉成 file:// 依赖 ----
#
# 为什么值得钉死：生成项目写 `dependencies=["atlas-test-platform"]`，而 Atlas 是源码分发
# （README 第一步 `pip install -e .`）——裸索引名解析不到，新用户接手骨架第一步就
# "No matching distribution found"，而 `--atlas-path` 在文档里写成「可选」。

def test_detect_atlas_source_root_synthetic(tmp_path):
    """探测判据：向上找**声明该分发名**的 pyproject.toml；只靠目录形状不算数。"""
    from atlas.scaffold import detect_atlas_source_root
    src = tmp_path / "src"
    (src / "atlas").mkdir(parents=True)
    (src / "atlas" / "__init__.py").write_text("", encoding="utf-8")
    marker = src / "atlas" / "__init__.py"

    # 未放 pyproject → 探测不到
    assert detect_atlas_source_root(marker) is None

    # 放了但名字不是本分发 → 不认（防把同名目录当 Atlas）
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "some-other-pkg"\n', encoding="utf-8")
    assert detect_atlas_source_root(marker) is None

    # 名字对上 → 返回源码根
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "atlas-test-platform"\nversion = "0.1.0"\n', encoding="utf-8")
    assert detect_atlas_source_root(marker) == tmp_path.resolve()

    # 坏 pyproject（非 TOML）不当证据，也不裸崩
    (tmp_path / "pyproject.toml").write_text("this is not toml [", encoding="utf-8")
    assert detect_atlas_source_root(marker) is None


def test_default_init_pins_detected_source(tmp_path, capsys):
    """默认 `atlas init`（不带 --atlas-path）应把探测到的源码根钉成 file:// 依赖；
    探测不到时如实回落为索引名 + mode=index（由 CLI 打 WARN）。"""
    from atlas.scaffold import detect_atlas_source_root
    detected = detect_atlas_source_root()
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    text = (tmp_path / "myproj" / "pyproject.toml").read_text(encoding="utf-8")
    dep = out["atlas_dependency"]
    if detected is not None:
        assert dep["mode"] == "file" and dep["path"] == str(detected)
        assert "atlas-test-platform @ " in text and "file:///" in text
        assert Path(detected).as_uri() in text
    else:
        assert dep["mode"] == "index" and dep["path"] is None
        assert re.search(r'"atlas-test-platform",', text)


def test_index_fallback_warns_and_notes(tmp_path, monkeypatch, capsys):
    """探测不到源码根时：骨架照旧生成（不失败），但 CLI 打 WARN、README 写「依赖前置」，
    不静默给一个可能装不上的骨架。"""
    import atlas.scaffold as scaffold
    monkeypatch.setattr(scaffold, "detect_atlas_source_root", lambda *a, **k: None)
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["atlas_dependency"] == {"mode": "index", "path": None}
    assert "WARN:" in captured.err and "atlas-test-platform" in captured.err
    assert "--atlas-path" in captured.err
    proj = tmp_path / "myproj"
    assert re.search(r'"atlas-test-platform",',
                     (proj / "pyproject.toml").read_text(encoding="utf-8"))
    assert "依赖前置" in (proj / "README.md").read_text(encoding="utf-8")


def test_pinned_dependency_points_at_real_source_root(tmp_path, capsys):
    """被钉进去的 file:// 路径必须真的存在且是 Atlas 源码根（不是猜出来的路径）。"""
    from atlas.scaffold import detect_atlas_source_root
    detected = detect_atlas_source_root()
    if detected is None:
        pytest.skip("当前 Atlas 非源码布局（未探测到源码根）")
    rc = main(["init", "myproj", "--system", "ics", "--module", "so",
               "--out", str(tmp_path)])
    assert rc == 0
    text = (tmp_path / "myproj" / "pyproject.toml").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "atlas-test-platform" in l)
    url = line.split("@", 1)[1].strip().rstrip(",").strip('"')
    assert url.startswith("file:///")
    from urllib.request import url2pathname
    from urllib.parse import urlparse
    path = Path(url2pathname(urlparse(url).path))
    assert (path / "pyproject.toml").is_file()
    assert 'name = "atlas-test-platform"' in (path / "pyproject.toml").read_text(encoding="utf-8")


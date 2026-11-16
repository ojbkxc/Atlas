"""打包缺口回归（P1-2）：wheel 必须带上插件知识资产与通用知识包。

背景（实测）：`[tool.setuptools.package-data]` 只声明了 `atlas.plugins.demo`，于是
`python -m build --wheel` 出来的 wheel 只有 7 个 demo JSON——`atlas/plugins/iservice/knowledge/`
缺失、`atlas/plugins/_common/` 连 `__init__.py` 都没有（不是 package，find 不收录）。
装完后果：`IservicePlugin.contracts()` 抛 ValueError("契约文件缺失")、`session_defaults()`
返回空 dict、`common_knowledge_root()` 指向不存在目录（通用包 82 条坑点静默不可见）。

两条腿，互补：
- **静态**（恒跑）：pyproject 的 package-data 声明 + `_common/__init__.py` 在树里——
  抓「有人删了声明」这种 build 也测不出的回归，且零依赖、零耗时。
- **真实构建**（有构建器时跑）：真打 wheel 用 zipfile 断言，抓「声明写了但 find 不收录」
  这类只有构建期才暴露的形态（`_common` 缺 `__init__.py` 正是这一类）。
  构建器优先级：`python -m build` → `python -m pip wheel`（PEP 517 隔离构建）。
  两者都不可用时 `skip` 并**打印原因**（不静默假绿）；`ATLAS_SKIP_WHEEL_BUILD=1`
  可显式跳过（本地频繁迭代/离线时用，CI 门禁不要设）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

# wheel 里必须存在的插件资产（相对 wheel 根的 POSIX 路径片段）。
REQUIRED_ISERVICE_JSON = (
    "atlas/plugins/iservice/knowledge/contracts/core.json",
    "atlas/plugins/iservice/knowledge/pitfalls.json",
    "atlas/plugins/iservice/knowledge/session.json",
)
REQUIRED_COMMON = (
    "atlas/plugins/_common/__init__.py",
    "atlas/plugins/_common/knowledge/pitfalls.json",
    "atlas/plugins/_common/knowledge/components.json",
    "atlas/plugins/_common/knowledge/ui_family.json",
)


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


# ── 静态腿：声明与目录形态（恒跑） ───────────────────────────────────────────

def test_package_data_declares_iservice_knowledge():
    """iservice 的知识 JSON 必须显式声明——漏声明时 wheel 只有 .py，装完契约断言层直接崩。"""
    data = _load_pyproject()["tool"]["setuptools"]["package-data"]
    assert "atlas.plugins.iservice" in data, f"package-data 缺 iservice 声明：{sorted(data)}"
    globs = data["atlas.plugins.iservice"]
    assert "knowledge/*.json" in globs
    assert "knowledge/contracts/*.json" in globs


def test_package_data_declares_common_knowledge():
    """通用知识包（跨平台 82 条坑点/组件族/接入清单）同样必须进 wheel。"""
    data = _load_pyproject()["tool"]["setuptools"]["package-data"]
    assert "atlas.plugins._common" in data, f"package-data 缺 _common 声明：{sorted(data)}"
    assert "knowledge/*.json" in data["atlas.plugins._common"]


def test_common_is_a_real_package():
    """`_common` 必须有 `__init__.py`：否则 packages.find 不收录它，package-data 写了也白写。

    这正是修复前的形态（目录里只有 knowledge/，不是 package）——静态腿能当场抓住。
    """
    assert (ROOT / "src" / "atlas" / "plugins" / "_common" / "__init__.py").is_file()


def test_packages_find_does_not_exclude_common():
    """`[tool.setuptools.packages.find]` 若加了 exclude，不能把 `_common` 挡掉。"""
    find = _load_pyproject()["tool"]["setuptools"].get("packages", {}).get("find", {})
    excludes = find.get("exclude") or []
    assert not any(pattern.strip("*") in ("_common", "atlas.plugins._common")
                   for pattern in excludes), f"_common 被 find.exclude 挡住：{excludes}"


def test_pytest_pythonpath_makes_bare_pytest_work():
    """裸 `pytest`（不带 `python -m`）不把 cwd 放进 sys.path：`from scripts.xxx import`
    在 4 个测试文件收集期 ImportError。配置须同时覆盖 src 与仓库根。"""
    opts = _load_pyproject()["tool"]["pytest"]["ini_options"]
    pythonpath = opts.get("pythonpath")
    assert pythonpath, "缺 [tool.pytest.ini_options] pythonpath"
    assert "src" in pythonpath and "." in pythonpath, pythonpath


# ── 真实构建腿：真打 wheel 再看包里有什么 ────────────────────────────────────

def _probe(cmd: list[str]) -> tuple[bool, str]:
    """跑一条探测命令；可用=True/原因文本。探测失败绝不抛（不可用是合法分支）。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode == 0 and proc.stdout.strip():
        return True, ""
    return False, f"rc={proc.returncode} {proc.stderr.strip()[:200]}"


def _builder_available() -> tuple[str, str]:
    """返回 (构建器, 不可用原因)。`python -m build` 优先（仓库根有 `build/` 目录，
    靠 returncode 判真假——`-m build` 撞上同名目录会直接失败，不会误判为可用）。"""
    ok, why_build = _probe([sys.executable, "-m", "build", "--version"])
    if ok:
        return "build", ""
    ok, why_pip = _probe([sys.executable, "-m", "pip", "wheel", "--help"])
    if ok:
        return "pip", ""
    return "", f"python -m build：{why_build}；python -m pip wheel：{why_pip}"


@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory) -> Path:
    if os.environ.get("ATLAS_SKIP_WHEEL_BUILD") == "1":
        pytest.skip("ATLAS_SKIP_WHEEL_BUILD=1：显式跳过真实构建（静态腿仍跑）")
    builder, reason = _builder_available()
    if not builder:
        pytest.skip(f"无可用构建器，退化为静态腿：{reason}")
    out = tmp_path_factory.mktemp("wheel")
    if builder == "build":
        cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(ROOT)]
    else:
        # PEP 517 隔离构建；--no-deps 只打本包（依赖由 [project.dependencies] 表达）。
        cmd = [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-w", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    wheels = sorted(out.glob("*.whl"))
    assert proc.returncode == 0 and wheels, (
        f"wheel 构建失败（builder={builder}）：\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return wheels[0]


def test_wheel_contains_iservice_contracts(wheel_path):
    """装完 `IservicePlugin.contracts()` 不抛「契约文件缺失」——契约 JSON 必须在包里。"""
    with zipfile.ZipFile(wheel_path) as zf:
        names = set(zf.namelist())
    missing = [n for n in REQUIRED_ISERVICE_JSON if n not in names]
    assert not missing, f"wheel 缺 iservice 资产：{missing}\n现有插件文件：{sorted(n for n in names if '/plugins/' in n)}"


def test_wheel_contains_common_package(wheel_path):
    """`_common/__init__.py` + 通用知识包 JSON 在包里——否则 common_knowledge_root() 指向空气。"""
    with zipfile.ZipFile(wheel_path) as zf:
        names = set(zf.namelist())
    missing = [n for n in REQUIRED_COMMON if n not in names]
    assert not missing, f"wheel 缺通用知识包：{missing}"

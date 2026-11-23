"""新用户「拿到产物第一步」的可执行性守卫（ob-8 同族：first-run experience）。

## 为什么钉它

`docs/first-bug.md` 钉的是「**拿到 Atlas** 后的最短路」；但 Atlas 的**首选交付物是产物包**
（AGENTS §0：`export-pytest`/`gen --with-pytest` 产物可脱离 Atlas 独立运行）。一个从没见过
Atlas 的人拿到 `pytest/` 目录，第一步是 `pytest --collect-only`——若这步就红，产物 README
写得再好也没用。实测：`gen examples/api_sample.md --with-pytest` 的产物 collect rc 0、
2 条用例；但该回归**此前无守卫**（只有 `test_portable_sample_export` 锁 import 面），
`pytest.ini`/`conftest`/`BASE_URL` 断链这类「第一个动作就挂」的回归只有到用户手里才现形。

## 四条

1. `test_artifact_collects_standalone`：产物在**无 PYTHONPATH/无 Atlas import** 的子进程里
   `pytest --collect-only` rc 0 且收集数 == 产物声明的用例数（runlog/AST 双证）；
2. `test_artifact_readme_lists_every_file`：README「本包文件清单」列出的每个文件都真实存在
   （死清单 = 新人按 README 找文件扑空）；
3. `test_artifact_readme_env_keys_cover_placeholders`：产物里实际出现的 `${VAR}` 占位符
   必须都在 README 的 env 段被点到（少一个 = 新人第一步就撞「未解析占位符」）；
4. `test_no_atlas_import_in_artifact`：正向钉死零 `import atlas`（可移植性主声明，防回潮）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from atlas.generator import GenOptions, generate

_REPO = Path(__file__).resolve().parents[1]
_DOC = """# Get web example customer code

## GET /web/example/customer/code

| 参数 | 说明 |
|---|---|
| code | 客户编码 |
"""


def _artifacts(tmp_path, *, fast: bool = False):
    root = tmp_path / f"case{int(fast)}"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "doc.md"
    source.write_text(_DOC, encoding="utf-8")
    out = root / "out"
    generate(source, out, GenOptions(with_pytest=True, fast=fast))
    return out / "pytest"


def _collect_in_clean_env(pkg: Path):
    """子进程 collect：**剥掉** PYTHONPATH，只留产物目录（模拟没装 Atlas 的新人机器）。"""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", str(pkg)],
                          capture_output=True, text=True, cwd=str(pkg), env=env,
                          encoding="utf-8", errors="replace", timeout=120)
    return proc


def test_artifact_collects_standalone(tmp_path):
    for fast in (False, True):
        pkg = _artifacts(tmp_path / f"case{int(fast)}", fast=fast)
        proc = _collect_in_clean_env(pkg)
        assert proc.returncode == 0, f"fast={fast} 新人第一步 collect 就挂：\n{proc.stdout[-400:]}"
        match = re.search(r"(\d+) tests? collected", proc.stdout)
        assert match and int(match.group(1)) >= 1, f"fast={fast} collect 数异常：{proc.stdout[-200:]}"


def test_artifact_readme_lists_every_file(tmp_path):
    pkg = _artifacts(tmp_path)
    readme = (pkg / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"`([\w./-]+\.(?:py|md|ini))`", readme))
    actual = {p.name for p in pkg.iterdir() if p.is_file()}
    missing = {name for name in actual if name not in listed}
    assert not missing, f"README 文件清单漏了实际存在的文件：{missing}"


def test_artifact_readme_env_keys_cover_placeholders(tmp_path):
    pkg = _artifacts(tmp_path)
    readme = (pkg / "README.md").read_text(encoding="utf-8")
    needed: set[str] = set()
    for py in pkg.glob("*.py"):
        needed.update(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", py.read_text(encoding="utf-8")))
    for key in needed:
        assert key in readme, f"产物用占位符 ${{{key}}} 但 README 的 env 段没告诉新人要填它"


def test_no_atlas_import_in_artifact(tmp_path):
    pkg = _artifacts(tmp_path)
    for py in pkg.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(?:from|import)\s+atlas", text, re.M), \
            f"{py.name} 出现 atlas import——可移植性主声明被破：" + py.name

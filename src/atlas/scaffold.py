"""通用项目脚手架生成器；模板是数据，引擎不含业务字面量（红线 11）。

Gap1 `atlas init` 的实现：生成 .atlas-root/.env/.env.example/.gitignore/pyproject.toml/
flows/smoke.yaml/tests/test_smoke.py/scripts/run_allure.py/README.md。

红线遵守：
- #1 禁凭据入库：.env 值全空，只留键名与占位注释
- #11 引擎零业务字面量：模板用占位符，生成器不内联客户名
- #12 禁形式代码：smoke.yaml 明标「需真实 env」；不伪造 legacy 目录
- 不引入运行时依赖：pyproject 不含 allure-pytest（报告走 atlas allure）
- --interactive 默认关闭（CI 安全）
"""
from __future__ import annotations
import re
import shutil
from pathlib import Path

from .config import ENV_PREFIX_DEFAULT, ENV_URL_SUFFIXES, env_url_keys


class ScaffoldError(Exception):
    """用户输入/目标冲突 → CLI 走 rc 2（与 cli._user_error 同口径）。"""


def _render(text: str, ctx: dict) -> str:
    """极简变量替换（{{key}}）；不用 jinja 以免引入大型框架依赖（AGENTS §4 Ask first）。"""
    for key, value in ctx.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def _write(dest: Path, rel: str, content: str) -> str:
    """写单个文件；返回相对路径。冲突默认拒绝（--force 由调用方控制）。"""
    dst = dest / rel
    if dst.exists():
        raise ScaffoldError(f"目标已存在：{dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    return rel


# ---- pyproject.toml ----
_PYPROJECT = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{{name}}"
version = "0.1.0"
description = "{{system}} / {{module}} 测试自动化项目（Atlas 生成）"
requires-python = ">=3.11"
dependencies = [
  "atlas-test-platform{{atlas_dep_suffix}}",
  "playwright>=1.45",
  "requests>=2.31",
  "PyYAML>=6.0",
  "pytest>=8",
  # 报告统一走 `atlas allure`（纯 stdlib），不引入 pytest 插件依赖。
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["."]
include = ["flows*", "scripts*"]
"""

# ---- flows/smoke.yaml ----
_SMOKE_YAML = """\
# 最小冒烟 flow：仅结构示例。运行需在 .env 配好 base_url（{{env_prefix}}URL_SIT）与已登录会话。
# 离线校验：atlas compile flows/smoke.yaml
id: smoke
steps:
  - task: api_call
    path: /health
    method: GET
    save_as: health
assert:
  - health is not null
"""

# ---- tests/test_smoke.py ----
_TEST_SMOKE = '''\
"""骨架冒烟：验证 Playwright 底座 + 产出 runs.jsonl（供 `atlas allure` 生成 Allure 结果）。
不访问真实业务系统、不需凭据。Allure 结果由 `atlas allure` 直出（gap5 方案 A），
本文件不依赖 allure-pytest。"""
from pathlib import Path

from atlas.config import AtlasConfig
from atlas.core import FunctionalTask, TaskMetadata
from atlas.dsl import FlowCompiler, TaskRegistry
from atlas.session import BrowserSession


def test_browser_baseline():
    """Playwright 执行底座（自包含公开页面，模型同 examples/smoke_browser.py）。"""
    config = AtlasConfig.from_root(env="sit", mode="run")
    with BrowserSession(config) as session:
        session.page.set_content("<title>Atlas smoke</title><button id='ok'>OK</button>")
        session.page.locator("#ok").click()
        assert session.page.title() == "Atlas smoke"


def _local_page_task():
    def _run(actor):
        actor.page.set_content("<title>Atlas engine smoke</title>")
        return {"ok": True}
    return FunctionalTask(
        TaskMetadata(domain="smoke", intent="本地页面执行（产出 runs.jsonl）", produces=("ok",)),
        _run, "local_page_smoke")


def test_engine_emits_runs_jsonl():
    """跑一个最小 Atlas flow（引擎在本地页面执行）→ 写 reports/runs.jsonl，
    供 `atlas allure reports/runs.jsonl --out reports/allure-results` 直出 Allure 结果。"""
    config = AtlasConfig.from_root(env="sit", mode="run")
    registry = TaskRegistry()
    registry.register("local_page_smoke", lambda **_: _local_page_task())
    with BrowserSession(config) as session:
        actor = session.actor("smoke")
        flow = {"id": "smoke", "engine": config, "steps": [{"task": "local_page_smoke"}]}
        result = FlowCompiler(registry).run(flow, actor)
    assert result["status"] == "passed"
    assert (config.reports_dir / "runs.jsonl").is_file()


def test_smoke_flow_compiles():
    """flows/smoke.yaml 离线结构校验（不登录、不执行），与 `atlas compile` 同源。"""
    from atlas.cli import _full_registry
    flow = FlowCompiler(TaskRegistry()).load(Path("flows/smoke.yaml"))
    FlowCompiler(_full_registry()).validate_flow(flow)
'''

# ---- scripts/run_allure.py ----
_RUN_ALLURE = '''\
"""pytest（产 runs.jsonl）→ atlas allure（产 Allure 结果集）→ allure serve/open。"""
import argparse
import subprocess
import sys
from pathlib import Path

RUNS = Path("reports/runs.jsonl")
RESULTS = Path("reports/allure-results")
REPORT = Path("reports/allure-report")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["test", "emit", "generate", "serve", "open"])
    ap.add_argument("--allure", default="allure", help="allure CLI 可执行文件名")
    ap.add_argument("-k", default="")
    args = ap.parse_args()

    if args.mode == "test":
        cmd = [sys.executable, "-m", "pytest", "-q"]
        if args.k:
            cmd += ["-k", args.k]
        return subprocess.call(cmd)
    if args.mode == "emit":
        return subprocess.call([sys.executable, "-m", "atlas", "allure", str(RUNS),
                                "--out", str(RESULTS), "--clean"])
    if not RESULTS.exists():
        print(f"没有结果集 {RESULTS}，先 `python scripts/run_allure.py test` 再 `emit`",
              file=sys.stderr)
        return 2
    if args.mode == "generate":
        return subprocess.call([args.allure, "generate", str(RESULTS),
                                "-o", str(REPORT), "--clean"])
    if args.mode == "serve":
        return subprocess.call([args.allure, "serve", str(RESULTS)])
    return subprocess.call([args.allure, "open", str(REPORT)])


if __name__ == "__main__":
    raise SystemExit(main())
'''

# ---- README.md ----
_README = """\
# {{name}}

{{system}} / {{module}} 测试自动化项目（Atlas 生成）。

## 快速开始

1. 建虚拟环境并装依赖：
   ```bash
   python -m venv .venv
   .venv/Scripts/python -m pip install -e .   # Windows
   # 或 Linux: .venv/bin/python -m pip install -e .
   ```

2. 装浏览器：
   ```bash
   python -m playwright install chromium
   ```

3. 配置环境：
   ```bash
   cp .env.example .env
   # 编辑 .env，填入真实的 URL 和凭据
   ```

4. 验证：
   ```bash
   atlas doctor
   pytest -q
   ```

## Allure 报告

```bash
# 方式一：用封装脚本
python scripts/run_allure.py test        # 跑 pytest，产 reports/runs.jsonl
python scripts/run_allure.py emit        # runs.jsonl → Allure 结果集
python scripts/run_allure.py serve       # 临时服务并浏览器打开

# 方式二：直连命令
pytest -q
atlas allure reports/runs.jsonl --out reports/allure-results --clean
allure serve reports/allure-results
```

## 添加系统/微服务

```bash
atlas config add <domain>            # 生成插件骨架
atlas config add <domain> --mapping  # 只出 ATLAS_* 映射片段
```

## 诚实说明

- 新项目未接旧项目时 `atlas doctor` 会 rc 1（`legacy_pages`/`demo_api` 为 False），
  属预期行为。接了 `ATLAS_LEGACY_*` / `ATLAS_DEMO_API_*` 后转绿。
- 生成的 `flows/smoke.yaml` 是结构示例，真实运行需在 `.env` 配好 `{{env_prefix}}URL_SIT`
  与会话凭据。
- 骨架的 `tests/test_smoke.py` 前两条需要 Playwright+Chromium；未装浏览器时会失败，
  这是诚实行为——不伪装通过。离线可跑第三条（`test_smoke_flow_compiles`）。
"""

# ---- .gitignore ----
_GITIGNORE = """\
.env
.venv/
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.coverage
htmlcov/
build/
dist/
reports/
artifacts/
*.har
*.jtl
*.png
*.storage_state.json
.DS_Store
# Allure 结果/报告
reports/allure-results/
reports/allure-report/
"""


def _build_env_example(prefix: str) -> str:
    """生成 .env.example 内容：复用 config.py 的键名，值全空（红线 1：无凭据）。"""
    lines = [
        f"# 复制为 .env；真实凭据不得提交。键前缀：{prefix}",
        f"{prefix}USERNAME=",
        f"{prefix}PASSWORD=",
        f"{prefix}PROD_USERNAME=",
        f"{prefix}PROD_PASSWORD=",
    ]
    for _env_name, suffix in sorted(ENV_URL_SUFFIXES.items()):
        lines.append(f"{prefix}{suffix}=")
    lines.extend([
        "",
        "# 可选：插件外部资产路径",
        "# ATLAS_LEGACY_PROJECT=../legacy-suite",
        "# ATLAS_DEMO_API_PROJECT=../legacy-api",
        "# ATLAS_BROWSER_CHANNEL=chrome",
        "",
        "# 可选：私有知识目录",
        "# ATLAS_KNOWLEDGE_DIR=",
        "",
        "# 可选：对接真实旧项目时的映射",
        f"# ATLAS_ENV_PREFIX={prefix}",
        "# ATLAS_LEGACY_WEB=legacy-web",
        "# ATLAS_DEMO_API_FILE=demo_api.py",
        "# ATLAS_DEMO_API_CLASS=DemoApi",
        "# ATLAS_DEMO_API_SPEC=demo_endpoints.json",
        "# ATLAS_APP_HEADER_KEY=demo-app",
        "# ATLAS_APP_HEADER_VALUE=legacy-web",
        "",
        "# 可选：真实业务会话默认值覆盖",
        "# ATLAS_SESSION_ACCOUNT=",
        "# ATLAS_SESSION_OU=",
        "# ATLAS_SESSION_DIVISION=",
        "# ATLAS_SESSION_LANGUAGE=English US",
    ])
    return "\n".join(lines) + "\n"


def init_project(name, system, module, *, atlas_path=None, interactive=False,
                 out_dir=Path(".")) -> dict:
    """生成 Atlas 项目骨架。

    参数：
      name        项目名（写进 pyproject/README）
      system      系统名（描述性标签）
      module      模块名（描述性标签）
      atlas_path  可选：Atlas 仓库路径，钉成 file:// 依赖
      interactive 仅 True 时读 stdin（CI 绝不阻塞）
      out_dir     输出目录（默认当前目录）

    返回 dict：{"created", "files", "next"}

    冲突防护：目标已有 .atlas-root/非空目录默认拒绝（ScaffoldError → CLI rc 2）。
    .atlas-root 最后落盘（防半成品假象）。
    """
    dest = (Path(out_dir) / name).resolve()

    # 冲突防护：已是 Atlas 项目 / 非空目录默认拒绝
    if (dest / ".atlas-root").exists():
        raise ScaffoldError(f"{dest} 已是 Atlas 项目（含 .atlas-root）")
    if dest.exists() and any(dest.iterdir()):
        raise ScaffoldError(f"{dest} 非空；请指定空目录或先清空")

    env_prefix = ENV_PREFIX_DEFAULT

    # --interactive 才读 stdin（CI 安全）
    if interactive:
        try:
            answer = input(f"凭据/URL 键前缀（默认 {env_prefix}，直接回车接受）：").strip()
            if answer:
                env_prefix = answer.upper().rstrip("_") + "_"
        except (EOFError, OSError):
            pass  # 非交互环境不阻塞

    # atlas-test-platform 依赖行
    if atlas_path:
        atlas_uri = Path(atlas_path).resolve().as_uri()
        atlas_dep_suffix = f" @ {atlas_uri}"
    else:
        atlas_dep_suffix = ""

    ctx = {
        "name": name,
        "system": system,
        "module": module,
        "env_prefix": env_prefix,
        "atlas_dep_suffix": atlas_dep_suffix,
    }

    dest.mkdir(parents=True, exist_ok=True)
    files = []

    # .env.example（值全空，无凭据——红线 1）
    env_example = _build_env_example(env_prefix)
    files.append(_write(dest, ".env.example", env_example))

    # .env（由 .env.example 复制，值全空）
    files.append(_write(dest, ".env", env_example))

    # .gitignore
    files.append(_write(dest, ".gitignore", _render(_GITIGNORE, ctx)))

    # pyproject.toml（不含 allure-pytest——方案 A）
    files.append(_write(dest, "pyproject.toml", _render(_PYPROJECT, ctx)))

    # README.md
    files.append(_write(dest, "README.md", _render(_README, ctx)))

    # flows/smoke.yaml（通用 api_call，离线 atlas compile 可过）
    files.append(_write(dest, "flows/smoke.yaml", _render(_SMOKE_YAML, ctx)))

    # tests/__init__.py
    files.append(_write(dest, "tests/__init__.py", ""))

    # tests/test_smoke.py
    files.append(_write(dest, "tests/test_smoke.py", _TEST_SMOKE))

    # scripts/run_allure.py
    files.append(_write(dest, "scripts/run_allure.py", _RUN_ALLURE))

    # .atlas-root 最后写：中途失败不留「半成品却像完整项目」的假象
    files.append(_write(dest, ".atlas-root", 'atlas-root = "."\n'))

    return {
        "created": str(dest),
        "files": files,
        "next": [
            f"cd {dest}",
            "python -m venv .venv",
            ".venv/Scripts/python -m pip install -e .",
            "python -m playwright install chromium",
            "cp .env.example .env  # 填真实值",
            "atlas doctor",
            "pytest -q",
            "atlas allure reports/runs.jsonl --out reports/allure-results --clean",
            "allure serve reports/allure-results",
        ],
    }

"""Linux 验收前置自检：在没有 Linux 主机时明确报告阻塞，而不是静默跳过。

用途：CI 或本地运行 `python scripts/linux_acceptance.py`，
有 Docker 就构建并运行容器；没有就输出**可复现的阻塞证据**与替代路径。
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ("python -m pytest -q && "
        "python -m compileall -q src scripts examples && "
        "python examples/smoke_browser.py && "
        "echo LINUX_ACCEPTANCE_PASSED")


def probe() -> dict:
    return {
        "platform": sys.platform,
        "docker": shutil.which("docker"),
        "docker_compose": bool(shutil.which("docker")),
        "wsl": shutil.which("wsl"),
        "dockerfile": (ROOT / "Dockerfile").is_file(),
        "compose_file": (ROOT / "docker-compose.yml").is_file(),
        "ci_workflow": (ROOT / ".github/workflows/ci.yml").is_file(),
    }


def main() -> int:
    info = probe()
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if info["platform"].startswith("linux"):
        print("已在 Linux 上运行，执行离线门禁：")
        return subprocess.run(GATE, shell=True, cwd=ROOT).returncode
    if info["docker"]:
        print("检测到 Docker，构建并运行 Linux 验收容器：")
        build = subprocess.run(["docker", "compose", "build"], cwd=ROOT)
        if build.returncode != 0:
            return build.returncode
        return subprocess.run(["docker", "compose", "up", "--abort-on-container-exit"], cwd=ROOT).returncode
    print(
        "阻塞（非代码缺陷）：本机无 Docker、无 WSL 发行版，无法执行 Linux 实跑。\n"
        "可复现路径（任选其一）：\n"
        "  1) 有 Docker 的机器: docker compose up --build\n"
        "  2) GitHub Actions:  推送后由 .github/workflows/ci.yml 的 linux job 自动执行\n"
        "  3) 任意 Linux 主机:  pip install -e '.[dev]' && python -m playwright install --with-deps chromium\n"
        "                       python -m pytest -q && python examples/smoke_browser.py",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

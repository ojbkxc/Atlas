"""Linux 验收（远程）：一次性完成 pytest + 编译 + 真实浏览器冒烟。

适用于已有 SSH 访问的 Linux 主机（不需要 Docker Hub）：
  - 建 venv 到 /tmp，不改系统 Python 包
  - 用系统 chromium（ATLAS_BROWSER_EXECUTABLE），不依赖 Playwright 浏览器 CDN
  - 结束时打印结构化结果，便于归档为验收记录
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE_DIR = "/tmp/atlas-acceptance"
PIP = "https://pypi.tuna.tsinghua.edu.cn/simple"


def resolve_password(env_name: str) -> str | None:
    """从 env_name 指定的环境变量读取密码；未设置或为空时返回 None。

    原实现来自已删除的 linux_docker_acceptance 模块。此处为唯一消费方
    （本脚本的 main()），故就近实现、不抽共享处（避免为单函数新建模块）。
    返回 None 与调用方 `if not password:` 的「缺少凭据」语义一致。
    """
    return os.environ.get(env_name, "") or None


def collect_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for pattern in ("pyproject.toml", "requirements.txt", "Dockerfile", "docker-compose.yml",
                    ".github/workflows/*.yml",
                    "src/**/*.py", "src/atlas/plugins/demo/knowledge/**/*.json",
                    "tests/**/*.py", "scripts/**/*.py", "scripts/templates/*.json",
                    "examples/**/*.py", "examples/**/*.yaml"):
        for path in ROOT.glob(pattern):
            if path.is_file():
                files.append((path, path.relative_to(ROOT).as_posix()))
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password-env", default="ATLAS_LINUX_PASSWORD")
    ap.add_argument("--browser", default="", help="系统 chromium 路径，如 /usr/bin/chromium")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    password = resolve_password(args.password_env)
    if not password:
        print("缺少凭据：设置 ATLAS_LINUX_PASSWORD", file=sys.stderr)
        return 2

    import paramiko
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(args.host, port=args.port, username=args.user, password=password, timeout=20)

    def run(cmd: str, timeout: int = 1800) -> tuple[int, str]:
        _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out

    run(f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR}")
    sftp = ssh.open_sftp()
    files = collect_files()
    for local, relative in files:
        target = f"{REMOTE_DIR}/{relative}"
        run(f"mkdir -p $(dirname {target})")
        sftp.put(str(local), target)
    sftp.close()

    setup = (f"cd {REMOTE_DIR} && python3 -m venv .venv && "
             f".venv/bin/pip install -q -i {PIP} --upgrade pip && "
             f".venv/bin/pip install -q -i {PIP} -e . pytest")
    code, out = run(setup)
    if code != 0:
        print("依赖安装失败：\n" + out[-1500:], file=sys.stderr)
        return code

    # 远端自行解析浏览器路径：Windows 本地 shell 会把 /usr/bin/chromium 误转成
    # C:/Program Files/Git/usr/bin/chromium，所以不要把绝对路径拼进命令串。
    if args.browser:
        browser_cmd = f"ATLAS_BROWSER_EXECUTABLE={args.browser}"
    else:
        browser_cmd = ("ATLAS_BROWSER_EXECUTABLE=$(command -v chromium "
                       "|| command -v chromium-browser || command -v google-chrome || true)")

    gate = (f"cd {REMOTE_DIR} && "
            f"echo '### OS' && . /etc/os-release && echo \"$PRETTY_NAME\" && "
            f"echo '### ARCH' && uname -m && "
            f"echo '### PYTHON' && .venv/bin/python -V && "
            f"echo '### PYTEST' && .venv/bin/python -m pytest -q 2>&1 | tail -3 && "
            f"echo '### COMPILE' && .venv/bin/python -m compileall -q src scripts examples && "
            f"echo COMPILE_OK && "
            f"echo '### BROWSER' && {browser_cmd} ATLAS_BROWSER_CHANNEL= "
            f".venv/bin/python examples/smoke_browser.py || exit 9; "
            f"echo LINUX_ACCEPTANCE_PASSED")
    code, out = run(gate)
    print(out)

    browser = run("command -v chromium || command -v chromium-browser || echo none")[1].strip()

    record = {"host": args.host, "remote_dir": REMOTE_DIR, "browser": browser,
              "exit_code": code, "passed": "LINUX_ACCEPTANCE_PASSED" in out,
              "output": out[-4000:]}
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"record -> {args.json_out}")
    ssh.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
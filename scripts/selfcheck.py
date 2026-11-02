"""Atlas 无凭据离线门禁：pytest + 编译 + CLI + 外部资产发现。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for command in ([sys.executable, "-m", "pytest", "-q"],
                [sys.executable, "-m", "compileall", "-q", "src"],
                [sys.executable, "-m", "atlas", "--root", str(ROOT), "doctor"],
                [sys.executable, "-m", "atlas", "--root", str(ROOT), "compose", "--goal", "document_no"]):
    subprocess.run(command, cwd=ROOT, check=True)

"""create 脚本接入契约：脚本自己负责真实建单，Atlas 只做护栏。"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

from .probes import ScriptedCreator


def make_scripted_creator(script: Path, *, python: str | None = None,
                          required_env: tuple[str, ...] = (),
                          timeout: int = 300) -> ScriptedCreator:
    """把“已验证的建单脚本”包装成 creator。

    约定：脚本接收 `where` 的 JSON（stdin），stdout 最后一行输出 JSON：
        {"remark": "...非人工创建", "orderNo": "SP..."}
    Atlas 不解析脚本内部实现，也不猜接口；只校验返回结构与备注标记。
    """
    script = Path(script)
    if not script.is_file():
        raise FileNotFoundError(f"建单脚本不存在：{script}")

    def _run(where: dict) -> dict:
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        proc = subprocess.run(
            [python or sys.executable, str(script)],
            input=json.dumps(where, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"建单脚本失败 rc={proc.returncode}: {proc.stderr.strip()[:500]}")
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("建单脚本没有输出结果 JSON")
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"建单脚本最后一行不是合法 JSON：{lines[-1][:200]}") from exc

    return ScriptedCreator(_run, required_env=required_env)


def require_audit_tag(result: dict) -> dict:
    """建单结果必须带「非人工创建」，否则拒绝入库。"""
    remark = str(result.get("remark", ""))
    if "非人工创建" not in remark:
        raise ValueError(f"建单结果缺少「非人工创建」备注：{result!r}")
    if not any(key in result for key in ("orderNo", "documentNo", "quotationNo", "returnNo")):
        raise ValueError(f"建单结果缺少单号字段：{result!r}")
    return result

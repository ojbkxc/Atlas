"""create 脚本接入契约：脚本自己负责真实建单，Atlas 只做护栏。"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

from .probes import DOCUMENT_NUMBER_KEYS, ScriptedCreator, find_document_number


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
        # #117 面3：目录路径/不存在路径统一 FileNotFoundError 点名「建单脚本」——
        # Path("some_dir").is_file() 为假走本分支，但裸 TypeError（如 where 带 date，面2）
        # 会让「传错路径」与「数据坏形态」两类用户错误混在一起无法归因。
        raise FileNotFoundError(f"建单脚本不存在：{script}")

    def _run(where: dict) -> dict:
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        try:
            payload = json.dumps(where, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            # #117 面2：where 含不可 JSON 序列化的值（YAML 日期对象、Path、集合……）——
            # 旧实现裸 TypeError「Object of type date is not JSON serializable」：不提脚本
            # 路径、不提哪个键，用户面对一行内部序列化报错无从下手（#61/#103 可归因红线：
            # 点名哪个键是什么类型、该改成什么）。
            bad = _first_unserializable_key(where)
            detail = f"（键 {bad[0]!r} 是 {bad[1]}）" if bad else ""
            raise TypeError(
                f"建单脚本 {script} 的 where 含无法 JSON 序列化的值{detail}：{exc}。"
                "YAML 的日期/对象请转成字符串（如 '2026-11-01'）再传入") from exc
        proc = subprocess.run(
            [python or sys.executable, str(script)],
            input=payload,
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


def _first_unserializable_key(where, prefix=""):
    """点名 where 里第一个不可 JSON 序列化的键与类型，嵌套 dict/list 下钻出整条路径
    （#61 可归因口径：`createdAfter` 藏在 where.a.b 里也要报全路径，不停在顶层 dict）。"""
    if isinstance(where, dict):
        items = where.items()
    elif isinstance(where, (list, tuple)):
        items = list(enumerate(where))
    else:
        return None
    for key, value in items:
        label = f"{prefix}.{key}" if prefix else str(key)
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            # 顶层 dict/list 本身序列化失败，可能是其内部某标量不可序列化——下钻一层，
            # 找到真正的坏叶子；下钻不到（如循环引用）就用当前层兜底。
            deeper = _first_unserializable_key(value, label) if isinstance(value, (dict, list, tuple)) else None
            return deeper or (label, type(value).__name__)
    return None


def require_audit_tag(result: dict) -> dict:
    """建单结果必须带「非人工创建」，否则拒绝入库。"""
    remark = str(result.get("remark", ""))
    if "非人工创建" not in remark:
        raise ValueError(f"建单结果缺少「非人工创建」备注：{result!r}")
    # #117 面1：单号判据收敛 probes.find_document_number 单一真相——旧实现 `key in result`
    # 把 {"orderNo": ""}/{"orderNo": None} 也算「有单号」放行，回收清单落空单号记录，
    # atlas recycle 找不回真实单据（#102 键在即算假成功同族）；supply 与 require 两份判据
    # 各写各的也重演「改一漏一必漂移」（#92/#97 同红线）。
    if not find_document_number(result):
        raise ValueError(
            f"建单结果缺少有效单号（需 {'/'.join(DOCUMENT_NUMBER_KEYS)} 之一非空）：{result!r}")
    return result

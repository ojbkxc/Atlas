"""把 Atlas 运行记录转换为外部 runlog 报告格式；不依赖其安装。"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path


def summarize(path: Path) -> dict:
    """按用例聚合：状态计数、失败类分布、最慢步骤。

    只读 JSONL，缺失文件明确报错，不静默返回空报告冒充“全部通过”。
    """
    if not path.exists():
        raise FileNotFoundError(f"运行记录不存在：{path}")
    status, fail_classes, slowest = Counter(), Counter(), []
    cases, runs, skipped = set(), 0, 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1  # 崩溃时可能写了一半的行；计数上报而非静默
                continue
            runs += 1
            cases.add(run.get("case", ""))
            status[run.get("status", "unknown")] += 1
            for step in run.get("steps", []):
                if step.get("fail_class"):
                    fail_classes[step["fail_class"]] += 1
                slowest.append((step.get("dur_ms", 0), run.get("case", ""), step.get("step", "")))
    slowest.sort(reverse=True)
    return {"runs": runs, "cases": len(cases), "status": dict(status),
            "fail_classes": dict(fail_classes),
            "skipped_malformed_lines": skipped,
            "slowest_steps": [{"dur_ms": d, "case": c, "step": s} for d, c, s in slowest[:10]]}

def export_runlog(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open(encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                continue  # 崩溃残行：导出报告时跳过，summarize 会给出计数
            for step in run.get("steps", []):
                target.write(json.dumps({"run_id": run["run_id"], "case": run["case"],
                    "env": run.get("env", ""), **{key: step.get(key, 0) for key in
                    ("step", "op", "ok", "dur_ms", "api_ms", "wait_ms", "ui_ms", "retries", "reloads")},
                    "fail_class": step.get("fail_class")}, ensure_ascii=False) + "\n")
    return output_path

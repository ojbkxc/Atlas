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
    # #131：与 review.rows/_read_queue/compile_trace 同防线（#112/#109 纪律）——录盘
    # 中断可把多字节字符腰斩在字节上，旧 path.open(encoding="utf-8") 迭代整文件裸
    # UnicodeDecodeError：atlas summary 一行 stderr + rc2 口径失效（CLI 只捕 OSError/
    # ValueError）变裸 traceback，而 docstring 承诺「不静默返回空报告」的残行计数防线
    # 一行没轮到。先严格读保真，撞坏字节回落 errors="replace"：残行 JSON 解析必败，
    # 自然落进既有 skipped 计数分支，合法行照常聚合。
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            run = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1  # 崩溃时可能写了一半的行；计数上报而非静默
            continue
        if not isinstance(run, dict):
            # 合法 JSON 但非对象（裸串/数字/null）：与残行同等计入，不崩整份聚合。
            # 与 export_runlog 的容错保持一致——两个聚合器对任意 JSONL 都不得整体失败。
            skipped += 1
            continue
        runs += 1
        cases.add(run.get("case", ""))
        status[run.get("status", "unknown")] += 1
        steps = run.get("steps", [])
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue   # steps:[null] 等外部/手改坏元素不得顶掉整份聚合（同 run 级容错口径）
            if step.get("fail_class"):
                fail_classes[step["fail_class"]] += 1
            dur = step.get("dur_ms", 0)
            if not isinstance(dur, (int, float)) or isinstance(dur, bool):
                dur = 0   # 非数值 dur_ms 不得让后续 slowest.sort 抛 str/int 比较错
            slowest.append((dur, run.get("case", ""), step.get("step", "")))
    slowest.sort(reverse=True)
    return {"runs": runs, "cases": len(cases), "status": dict(status),
            "fail_classes": dict(fail_classes),
            "skipped_malformed_lines": skipped,
            "slowest_steps": [{"dur_ms": d, "case": c, "step": s} for d, c, s in slowest[:10]]}

def export_runlog(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # #131：同 summarize——一条腰斩坏字节曾让整份导出裸 UnicodeDecodeError，
    # CLI 的 rc2 友好口径（只捕 OSError/ValueError）失效变 traceback；
    # replace 读入后残行 JSON 解析必败，自然落进既有「跳过残行」分支。
    try:
        raw = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = input_path.read_text(encoding="utf-8", errors="replace")
    with output_path.open("w", encoding="utf-8") as target:
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                continue  # 崩溃残行
            # 合法 JSON 但不是对象（裸串/数字/null），或缺外部 runlog 强制主键 run_id：跳过该行。
            # input 来自 CLI 用户传入，可能是外部/过滤后的 jsonl；一条坏行绝不能顶掉整份报告。
            if not isinstance(run, dict) or "run_id" not in run:
                continue
            run_id = run["run_id"]
            steps = run.get("steps", [])
            for step in steps if isinstance(steps, list) else []:
                if not isinstance(step, dict):
                    continue   # steps:[null] 等坏元素跳过，不顶掉整份报告（同 run 级容错口径）
                target.write(json.dumps({"run_id": run_id, "case": run.get("case", ""),
                    "env": run.get("env", ""), **{key: step.get(key, 0) for key in
                    ("step", "op", "ok", "dur_ms", "api_ms", "wait_ms", "ui_ms", "retries", "reloads")},
                    "fail_class": step.get("fail_class")}, ensure_ascii=False) + "\n")
    return output_path

"""质量台账的实测清点器（只读、可复跑）。

为什么入库：`docs/quality-ledger.md` 的验收契约是「台账条目数 == 实测项数，不靠目测」。
清点逻辑必须**可复跑**，否则台账与实测会各自漂移，台账就退化成一份手写愿望清单。
产出落 `reports/`（gitignored，产物不入库）：

    python scripts/quality_inventory.py
    # → reports/quality-inventory.md   逐条明细（含代码上下文）
    # → reports/_rc.json              review-code 原始 JSON（供二次分析）

清点三类：
  1. `atlas review-code src` 的全部 finding（F 项）
  2. `src/atlas` 里 TODO/FIXME/XXX/待补/留白 的 grep 命中（D 项）
  3. 两处已知留白（L 项）：allure 降级标记、Docker·compose·CI 未实测
     —— 这两项脚本只能核「存在性」（比如 docker 命令是否可用），判定由人写在台账里。

注意：`XXX` 会误命中 JS 转义说明里的 `\\uXXXX`，本脚本**不**替人判断，
只如实列出命中并标注疑似误匹配，判定留给台账（见 D21）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "atlas"
REPORTS = REPO / "reports"
RC_JSON = REPORTS / "_rc.json"
OUT = REPORTS / "quality-inventory.md"
BS = chr(92)


def _norm(p: str) -> str:
    return p.replace(BS, "/")


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _near_comment(lines: list[str], line: int, back: int = 3) -> tuple[int, str]:
    """向上找最近的整行注释。注意 GREEN-001 的理由也常在 **body 首行**（except 之后），
    故调用方还要单独看下一行——这里只管向上。"""
    for d in range(0, back + 1):
        idx = line - 1 - d
        if 0 <= idx < len(lines) and lines[idx].lstrip().startswith("#"):
            return d, lines[idx].strip()
    return 0, ""


def run_review_code() -> dict:
    """跑一次 review-code（static），缓存 JSON。失败则复用旧缓存并告警。"""
    REPORTS.mkdir(exist_ok=True)
    env = {**__import__("os").environ, "PYTHONPATH": str(REPO / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "atlas", "review-code", "src", "--format", "json"],
        cwd=REPO, capture_output=True, env=env,
        # 必须显式 UTF-8：Windows 下 `text=True` 默认用 GBK 解码，子进程输出里
        # 的 UTF-8 中文会让 reader 线程抛 UnicodeDecodeError → `proc.stdout` 直接变 None
        # （实测：rc=0 但 stdout is None）。不是子进程的错，是本进程的解码口径。
        encoding="utf-8", errors="replace")
    if proc.returncode == 0 and (proc.stdout or "").strip():
        RC_JSON.write_text(proc.stdout, encoding="utf-8")
    elif RC_JSON.is_file():
        print(f"WARN: review-code rc={proc.returncode}，复用旧缓存 {RC_JSON}", file=sys.stderr)
    else:
        raise SystemExit(f"review-code 失败且无缓存：rc={proc.returncode}\n{proc.stderr[-500:]}")
    return json.loads(RC_JSON.read_text(encoding="utf-8"))


def findings(data: dict) -> list[dict]:
    rows = [{"rule": f["rule_id"], "sev": f["severity"],
             "file": _norm(f["file"]), "line": f["line"],
             "snippet": f["snippet"].strip(), "rationale": f["rationale"]}
            for r in data["results"] for f in r["findings"]]
    rows.sort(key=lambda x: (x["rule"], x["file"], x["line"]))
    return rows


def todo_hits() -> list[dict]:
    pat = re.compile(r"TODO|FIXME|XXX|待补|留白")
    rows = []
    for path in sorted(SRC.rglob("*.py")):
        for i, text in enumerate(_lines(path), 1):
            if pat.search(text):
                rows.append({"file": _norm(str(path.relative_to(REPO))),
                             "line": i, "text": text.strip(),
                             # `\uXXXX` 之类是转义说明，不是待办标记——只标注，不替人删
                             "suspect": bool(re.search(r"\\uX{4}", text))})
    return rows


def whitespace_probes() -> list[dict]:
    """两处已知留白的**存在性**核实（判定仍由台账写）。"""
    text = (REPO / "src" / "atlas" / "review_code.py").read_text(encoding="utf-8")
    allure_marker = "knowledge_degraded" in text and "emit_allure_review" in text
    return [
        {"id": "L01", "name": "allure 降级标记（emit_allure_review 无容器概念）",
         "probe": f"review_code.py 同时含 knowledge_degraded 与 emit_allure_review: {allure_marker}"},
        {"id": "L02", "name": "Docker / compose / CI 未实测",
         "probe": f"docker 可执行: {shutil.which('docker') is not None}"},
    ]


def main() -> None:
    data = run_review_code()
    f_rows = findings(data)
    t_rows = todo_hits()
    w_rows = whitespace_probes()
    total = len(f_rows) + len(t_rows) + len(w_rows)

    out = [f"# 质量台账实测清点\n\n- review-code summary: `{json.dumps(data['summary'], ensure_ascii=False)}`\n",
           f"- F 项（findings）: **{len(f_rows)}**\n",
           f"- D 项（TODO/留白 grep 命中）: **{len(t_rows)}**（其中疑似误匹配 "
           f"{sum(1 for t in t_rows if t['suspect'])} 条）\n",
           f"- L 项（已知留白）: **{len(w_rows)}**\n",
           f"- **合计 {total}**\n",
           "\n---\n\n## F 项：review-code findings\n"]
    for i, f in enumerate(f_rows, 1):
        lines = _lines(REPO / f["file"])
        body_first = lines[f["line"]].strip() if f["line"] < len(lines) else ""
        d, c = _near_comment(lines, f["line"])
        out.append(f"\n### F{i:02d} `{f['rule']}` {f['sev']} — {f['file']}:{f['line']}\n")
        out.append(f"- code: `{f['snippet'][:140]}`\n")
        out.append(f"- 上溯注释(距 {d}): {c[:160] if c else '无'}\n")
        out.append(f"- body 首行: `{body_first[:160]}`\n")

    out.append("\n---\n\n## D 项：TODO / 留白 grep 命中\n")
    for i, t in enumerate(t_rows, 1):
        flag = "  ⚠️ 疑似误匹配" if t["suspect"] else ""
        out.append(f"\n- **D{i:02d}** `{t['file']}:{t['line']}`{flag}\n  ```{t['text'][:180]}```\n")

    out.append("\n---\n\n## L 项：已知留白存在性探测\n")
    for w in w_rows:
        out.append(f"\n- **{w['id']}** {w['name']}\n  - probe: `{w['probe']}`\n")

    OUT.write_text("".join(out), encoding="utf-8")
    print(f"F={len(f_rows)} D={len(t_rows)} L={len(w_rows)} total={total} -> {OUT}")


if __name__ == "__main__":
    main()

"""从已审核的项目文档提炼知识草稿；不自动写正式知识库。"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def extract(markdown: Path, source: str = "") -> list[dict]:
    rows = []
    for line in markdown.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- **") or "：**" not in line:
            continue
        title, body = line[4:].split("：**", 1)
        symptom = title.strip()
        rows.append({"id": "draft-" + hashlib.sha1(f"{source}:{symptom}".encode()).hexdigest()[:12],
                     "trigger": symptom, "symptom": symptom,
                     "root_cause": body.strip(), "remedy": "请人工审核并补充可执行修复配方",
                     "scope": ["demo"], "severity": "medium", "source": source or markdown.name,
                     "status": "pending_review"})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps({"pitfalls": extract(args.markdown)}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

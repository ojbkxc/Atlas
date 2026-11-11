"""入站变更集分类 gate（C5）——merge/吸收动作**之前**跑，事后无法用 diff 还原入站集。

薄消费方：判据唯一实现在 `atlas.boundaries`（评审 C5-A——src/atlas 不能 import
scripts/，doctor 与本脚本都只调判据核，不留第二份校验逻辑 #92）。

用法（git 由人执行，本脚本零 subprocess——X2，Windows shell 差异不借道）：

    git diff --cached > changes.diff
    PYTHONPATH=src python scripts/boundary_classify.py --diff changes.diff
    git diff main..feature | PYTHONPATH=src python scripts/boundary_classify.py

    --to-tower 时 REVIEW-TOWER 项经 ExperienceJournal 登记 kind=boundary_review
    pending_review 草稿进**既有**审核塔（红线 #9/#10：不新开塔、不新增知识写路径；
    三元组位留空=天然 unverified，晋级照走 C2 闸）。

rc：0=无 structural；1=有 structural（absorbed/engine-owned 命中则进人审塔）；
2=用法错/账本缺失或坏（没有可信账本就没有分类，如实拒绝不猜）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atlas import boundaries
from atlas.experience import ExperienceJournal


def main(argv=None) -> int:
    args_list = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(prog="boundary_classify",
                                     description="merge/吸收前对入站变更集按边界账本分级")
    parser.add_argument("--diff", type=Path, default=None,
                        help="unified diff 文件；缺省从 stdin 读（git 命令由人跑）")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="仓库根（账本定位用），默认当前目录")
    parser.add_argument("--ledger", type=Path, default=None,
                        help=f"账本路径覆盖，默认 <root>/{boundaries.LEDGER_REL_PATH}")
    parser.add_argument("--to-tower", action="store_true",
                        help="REVIEW-TOWER 项登记 boundary_review 草稿进既有审核塔")
    parser.add_argument("--reports", type=Path, default=None,
                        help="审核塔目录（默认 <root>/reports，与 atlas review 同队列）")
    ns = parser.parse_args(args_list)

    ledger_file = ns.ledger or boundaries.ledger_path(ns.root)
    # X5：diff 文件可能带坏字节/中文（quotepath），replace 回落读入，坏形态点名不崩
    try:
        if ns.diff is not None:
            text = ns.diff.read_text(encoding="utf-8", errors="replace")
        else:
            text = sys.stdin.read()
    except OSError as exc:
        print(f"用法错误：diff 读不进来: {exc}", file=sys.stderr)
        return 2

    ledger, problems = boundaries.load_ledger(ledger_file)
    if problems:
        for p in problems:
            print(f"账本: {p}", file=sys.stderr)
        return 2

    origin = str(ns.diff) if ns.diff else "stdin"
    files = boundaries.parse_unified_diff(text)
    if not files:
        print("diff 为空或未识别出文件段——没有入站变更集，无需分类", file=sys.stderr)
        return 0

    items = boundaries.classify_change(ledger, files)
    print(json.dumps(items, ensure_ascii=False, indent=2))

    tower = [i for i in items if i["verdict"] == "REVIEW-TOWER"]
    structural = [i for i in items if i["class"] == "structural"]
    hints = [i for i in items if i["verdict"] == "HINT"]
    print(f"verdict: {len(tower)} REVIEW-TOWER / {len(structural) - len(tower)} "
          f"structural(塔外) / {sum(1 for i in items if i['verdict'] == 'AUTO')} AUTO / "
          f"{len(hints)} HINT(补账)"
          + ("；路径未登记项先补 docs/ownership/atlas-boundaries.json 再吸收" if hints else ""),
          file=sys.stderr)

    if ns.to_tower and tower:
        journal = ExperienceJournal(ns.reports or (ns.root / "reports"))
        for item in tower:
            journal.append_draft(journal.draft_from_text(**boundaries.boundary_draft_args(item, origin)))
        print(f"已登记 {len(tower)} 条 boundary_review 草稿（pending_review；"
              "不自动写知识，approve 该 kind 被点名拒绝）", file=sys.stderr)

    return 1 if structural else 0


if __name__ == "__main__":
    sys.exit(main())

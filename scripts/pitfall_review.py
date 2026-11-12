"""迁移坑点 → 审核塔：导入、汇总、人工逐条启用（in-place）。

背景（为什么不是直接 `atlas review --approve <坑点id>`）
------------------------------------------------------
迁移进私有库的 119 条坑点在 `pitfalls.json` 里是 `status=pending_review`——这是
**知识库条目状态**，与审核塔队列行是两套机制（`docs/knowledge-migration.md` §4）。
本工具的 import 把它们推进塔，只是让 `atlas review` 能看见、能读 30 秒证据卡片；
但 review 的 pitfall 批准路径 `experience.approve_pitfall` 会**新建**一条
`reviewed-<sha1>` 条目，其 trigger/symptom/root_cause/scope 只能取队列行字段的回落值
（CLI 的 `--approve`/`--apply` 不暴露这四个旋钮）：

* `scope` 回落成 `[row["task"]]` → 域标签丢失 → `match_pitfall(exc, domain=...)` 的域过滤
  不命中 → 自愈永不触发（dead asset 假成功，正是 `#102` 一类静默失效）；
* `root_cause`/`symptom` 被回落成 `task`/`failure_class`；
* 且原 `isvc-*` pending 条目不会被替换 → 同一坑点两条（一条 approved、一条 pending）。

所以迁移条目的人审批准走**就地翻 status**（`docs/knowledge-migration.md` §5.3 列出的路径），
写回经 `KnowledgeBase.save_reviewed`——与 `approve_pitfall` 同一条知识写路径、同一把
`_writable_root` 闸门（AGENTS 红线 #11），不是裸写文件。

纪律
----
* 本工具**不自动批准**：`import`/`report` 只写审核塔队列（`pending_review`），知识库零改动；
  `enable` 必须由人显式点名 id 并给 `--reason`。
* `isvc-039/040/063/064/065/095` 默认拒绝启用：源文明确要求 SKIP 或报管理员补主数据，
  批准成「可自动重试」就等于制造假绿（`AGENTS.md` Never#6 / `knowledge-migration.md` §5.4）。

用法
----
    python scripts/pitfall_review.py report [--json]
    python scripts/pitfall_review.py import [--dry-run]
    python scripts/pitfall_review.py enable --ids isvc-001 isvc-002 --reason "……"
    python scripts/pitfall_review.py enable --decisions reports/pitfall_review_decisions.jsonl \
        --reason "……" [--force-blocked] [--dry-run]
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

# 源文要求 SKIP/报管理员的阻塞条目（knowledge-migration.md §5.4）——默认拒绝启用。
BLOCKED_IDS = ("isvc-039", "isvc-040", "isvc-063", "isvc-064", "isvc-065", "isvc-095")
BLOCKED_NOTE = ("环境/产品阻塞：源文要求 SKIP 或报管理员补主数据，放宽 remedy 或批准成"
                "可自动重试 = 制造假绿（AGENTS Never#6 / knowledge-migration §5.4）")
DECISIONS_NAME = "pitfall_review_decisions.jsonl"


def _as_str(value) -> str:
    """坏形态（None/数字/容器）归空串，绝不把 None 拼进文本或当 id。"""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _scope_of(entry: dict) -> list:
    scope = entry.get("scope")
    return scope if isinstance(scope, list) else []


def is_blocked(pitfall_id: str) -> bool:
    return pitfall_id in BLOCKED_IDS


def load_pending(pitfalls: dict) -> list[dict]:
    """pitfalls.json 顶层映射 `{pitfalls: [...]}` → `status=pending_review` 的行。

    坏形态（顶层非映射 / pitfalls 键非列表 / 行非映射）点名拒绝，绝不静默当成空库
    （#127 家族：坏形态伪装「没有待审条目」，人以为塔是空的）。"""
    if not isinstance(pitfalls, dict) or not isinstance(pitfalls.get("pitfalls"), list):
        raise ValueError("pitfalls.json 顶层必须是 {pitfalls: [...]} 映射")
    return [row for row in pitfalls["pitfalls"]
            if isinstance(row, dict) and row.get("status") == "pending_review"]


def draft_row(entry: dict) -> dict:
    """知识条目 → 审核塔 pending_review 行（**行 id = 知识条目 id**，人审认这个引用）。

    `task` 取域标签 `"iservice"` 而不是条目 id 是有意的兜底：万一有人误走
    `atlas review --approve`，`approve_to_pitfall` 用 `task` 兜 `scope`，域标签在则
    新建的 approved 条目至少**能被域过滤命中**（不至于成 dead asset）；正确路径是本工具的
    `enable`（就地翻 status，零新建、零重复、字段全保真），见模块 docstring。"""
    pid = _as_str(entry.get("id"))
    scope = _scope_of(entry)
    domain = _as_str(scope[0]) if scope else "iservice"
    trigger = _as_str(entry.get("trigger"))
    symptom = _as_str(entry.get("symptom"))
    root_cause = _as_str(entry.get("root_cause"))
    remedy = _as_str(entry.get("remedy"))
    source = _as_str(entry.get("source"))
    severity = _as_str(entry.get("severity"))
    text = "\n".join(part for part in (
        f"symptom: {symptom}", f"root_cause: {root_cause}",
        f"remedy: {remedy}", f"source: {source}",
        f"scope: {scope}", f"severity: {severity}") if part.split(": ", 1)[-1].strip())
    return {
        "id": pid,
        "task": domain,
        "exception_type": trigger,
        "failure_class": "migrated",
        "pitfall_id": "",
        "kind": "pitfall",
        "status": "pending_review",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "evidence": {
            "text": text,
            "migrated_pitfall_id": pid,
            "trigger": trigger,
            "severity": severity,
            "scope": scope,
            "source": source,
            "suggested_remedy": remedy,
            "blocked_no_auto_heal": is_blocked(pid),
            "blocked_note": BLOCKED_NOTE if is_blocked(pid) else "",
        },
    }


def decision_row(entry: dict) -> dict:
    """人可编辑的决策行（预填原 remedy，人改写后供 `enable --decisions` 执行）。

    形态与 `atlas review --template` 对齐（id/task/kind/remedy），外加 `reason` 位：
    逐条留痕的依据（缺省回落命令行 `--reason`）。"""
    return {"id": _as_str(entry.get("id")), "task": "iservice", "kind": "pitfall",
            "remedy": _as_str(entry.get("remedy")), "reason": ""}


def _private_pitfalls_path(knowledge) -> Path | None:
    root = getattr(knowledge, "root", None)
    return (Path(root) / "pitfalls.json") if root is not None else None


def run_report(knowledge, *, as_json: bool = False) -> dict:
    """只读汇总：pending 总数、按 severity 分布、阻塞清单。知识库与审核塔都不动。"""
    pending = load_pending(knowledge.load("pitfalls"))
    by_sev: dict[str, int] = {}
    prefixes: dict[str, int] = {}
    for entry in pending:
        by_sev[_as_str(entry.get("severity")) or "?"] = by_sev.get(_as_str(entry.get("severity")) or "?", 0) + 1
        prefix = _as_str(entry.get("id")).split("-", 1)[0] or "?"
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    blocked = [e.get("id") for e in pending if is_blocked(_as_str(e.get("id")))]
    summary = {"pending_total": len(pending), "by_severity": dict(sorted(by_sev.items())),
               "by_prefix": dict(sorted(prefixes.items())),
               "blocked": blocked, "blocked_note": BLOCKED_NOTE,
               "knowledge_root": str(getattr(knowledge, "root", "") or ""),
               "ids": [_as_str(e.get("id")) for e in pending]}
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"pending 条目：{summary['pending_total']}  "
              f"（{summary['by_prefix']}）  severity={summary['by_severity']}")
        print(f"知识库写根：{summary['knowledge_root'] or '<只读：未设 ATLAS_KNOWLEDGE_DIR>'}")
        print(f"阻塞条目（默认不可 enable）：{blocked or '无'}")
    return summary


def run_import(knowledge, reports_dir: Path, *, dry_run: bool = False) -> dict:
    """把 pending 坑点导入审核塔队列（幂等）+ 导出预填决策文件。知识库零改动。"""
    from atlas.experience import ExperienceJournal
    from atlas.review import ReviewQueue
    pending = load_pending(knowledge.load("pitfalls"))
    journal = ExperienceJournal(reports_dir)
    queue = ReviewQueue(reports_dir)
    existing = {row.get("id") for row in queue.rows()}
    created, skipped = [], []
    for entry in pending:
        row = draft_row(entry)
        if row["id"] in existing:
            skipped.append(row["id"])       # 幂等：已在塔（任意状态）不重复入队、不涨 seen
            continue
        if not dry_run:
            journal.append_draft(row)
        created.append(row["id"])
    decisions = [decision_row(e) for e in pending if not is_blocked(_as_str(e.get("id")))]
    decisions_path = reports_dir / DECISIONS_NAME
    if not dry_run:
        reports_dir.mkdir(parents=True, exist_ok=True)
        decisions_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in decisions),
            encoding="utf-8")
    result = {"pending_total": len(pending), "created": len(created), "skipped": len(skipped),
              "created_ids": created, "skipped_ids": skipped,
              "decisions_file": str(decisions_path), "decisions_rows": len(decisions),
              "blocked": sorted(e.get("id") for e in pending if is_blocked(_as_str(e.get("id")))),
              "dry_run": dry_run}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if created:
        print("\n下一步（人审，不自动批准）：atlas review --kind pitfall "
              "# 读证据卡片 → 逐条 enable：\n"
              "  python scripts/pitfall_review.py enable --ids <id>... --reason \"<依据>\"",
              file=sys.stderr)
    return result


def _enable_plan(rows: list[dict], ids: list[str], *, force_blocked: bool) -> tuple[list[dict], list[dict]]:
    """返回 (可启用条目, 拒绝清单)。判据集中一处，绝不因坏形态静默跳过。"""
    index = {row.get("id"): row for row in rows if isinstance(row, dict)}
    plan, refusals = [], []
    for pid in ids:
        entry = index.get(pid)
        if entry is None:
            refusals.append({"id": pid, "reason": "知识库中未找到该 id"})
        elif entry.get("status") == "approved":
            refusals.append({"id": pid, "reason": "已是 approved（幂等，不重复写入）"})
        elif is_blocked(pid) and not force_blocked:
            refusals.append({"id": pid, "reason": BLOCKED_NOTE})
        else:
            plan.append(entry)
    return plan, refusals


def run_enable(knowledge, reports_dir: Path, ids: list[str], *, reason: str,
               force_blocked: bool = False, dry_run: bool = False) -> dict:
    """人工批准：把指定条目 `status` 就地翻成 approved 并写回私有库（+ 镜像审核塔行）。

    写回走 `KnowledgeBase.save_reviewed`（`approve_pitfall` 同一条写路径）。`--reason`
    必填：无痕的批准等于没闸（红线 #13）。"""
    from atlas.review import APPROVED, PENDING, ReviewQueue, _is_pending
    ids = [i for i in dict.fromkeys(ids) if i]          # 去重保序
    if not ids:
        raise ValueError("enable 需要至少一个 --ids（或在 --decisions 文件里给 id）")
    if not reason.strip():
        raise ValueError('enable 必须留原因：--reason "<凭什么批准这条知识>"')
    private_path = _private_pitfalls_path(knowledge)
    if private_path is None or not private_path.exists():
        raise ValueError(f"私有知识库未就绪（{private_path}）——enable 只写 ATLAS_KNOWLEDGE_DIR 指向的目录")
    data = knowledge.load("pitfalls")
    rows = data["pitfalls"]
    plan, refusals = _enable_plan(rows, ids, force_blocked=force_blocked)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    if not dry_run and plan:
        for entry in plan:
            entry["status"] = APPROVED
            entry["reviewed_at"] = stamp
            entry["review_reason"] = reason
        knowledge.save_reviewed("pitfalls", rows, approved=True)
        # 镜像纪律：塔行同 id 翻 approved（只翻 pending 行，已驳回历史不回翻——与
        # review.approve 的镜像同口径）。复用塔自己的 _rewrite/_decide 单点，不复制第二份。
        queue = ReviewQueue(reports_dir)
        qrows = queue.rows()
        applied = {entry.get("id") for entry in plan}
        changed = 0
        for row in qrows:
            if row.get("id") in applied and _is_pending(row):
                row["status"] = APPROVED
                row["pitfall_ref"] = row["id"]
                row["decided_at"] = stamp
                changed += 1
        if changed:      # 塔里没有对应行（未先 import）就不凭空建一份空队列文件
            queue._rewrite(qrows)
        for pid in applied:
            queue._decide({"action": "approve_knowledge", "id": pid, "pitfall": pid,
                           "remedy": reason[:120], "at": stamp})
    result = {"enabled": [e.get("id") for e in plan], "refused": refusals,
              "reason": reason, "force_blocked": force_blocked, "dry_run": dry_run,
              "knowledge_file": str(private_path)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if plan and not dry_run:
        print(f"已启用 {len(plan)} 条（{PENDING} → {APPROVED}）。自愈准入现在能看到它们："
              f"match_pitfall(status=\"approved\")。", file=sys.stderr)
    return result


def _load_decision_ids(path: Path) -> list[str]:
    """读人填的决策文件：只取非空 id（坏行/坏 JSON 点名拒绝，不静默跳过整批）。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    ids = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{lineno} 决策行必须是映射，实为 {type(row).__name__}")
        pid = _as_str(row.get("id")).strip()
        if pid:
            ids.append(pid)
    return ids


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["report", "import", "enable"])
    parser.add_argument("--ids", nargs="+", default=[], metavar="ID", help="enable：要启用的坑点 id")
    parser.add_argument("--decisions", type=Path, default=None, metavar="FILE",
                        help=f"enable：决策文件（默认 {DECISIONS_NAME}），逐行取 id")
    parser.add_argument("--reason", default="", help="enable：批准依据（必填，逐条留痕）")
    parser.add_argument("--force-blocked", action="store_true",
                        help="enable：连阻塞条目一起启用（不建议；先读 knowledge-migration §5.4）")
    parser.add_argument("--dry-run", action="store_true", help="只算不写")
    parser.add_argument("--json", action="store_true", help="report：输出 JSON")
    parser.add_argument("--root", type=Path, default=None, help="Atlas 项目根（默认当前目录向上找 .atlas-root）")
    return parser


def main(argv: list[str] | None = None) -> int:
    from atlas.config import AtlasConfig
    from atlas.plugin_api import resolve_plugin
    args = _build_parser().parse_args(argv)
    try:
        config = AtlasConfig.from_root(args.root)
        knowledge = resolve_plugin().knowledge()
        reports_dir = config.reports_dir
        if args.mode == "report":
            run_report(knowledge, as_json=args.json)
        elif args.mode == "import":
            run_import(knowledge, reports_dir, dry_run=args.dry_run)
        else:
            ids = list(args.ids)
            if args.decisions is not None:
                ids += _load_decision_ids(args.decisions)
            run_enable(knowledge, reports_dir, ids, reason=args.reason,
                       force_blocked=args.force_blocked, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

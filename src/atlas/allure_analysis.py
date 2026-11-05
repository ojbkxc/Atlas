"""Allure 2.39.0 结果集解析（汇入侧）：Allure → Atlas 分类 → 自愈提案 / 审核塔草稿。

对应 Gap5 §4（汇入方案）、§6（失败分类口径）、§6.3（四大失败类型 4 桶映射）。

纪律：
- 解析层只做翻译/归类，无改断言路径（红线 #7）。
- 不新增知识写路径：产出只进 pending_review 草稿 → 既有 atlas review 塔（红线 #9/#10）。
- 控制流异常（interrupted）一律拒自愈（#108）。
- 读侧容错：坏字节/坏 JSON/腰斩不整份崩（#131/#112）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from .failures import FailureClass, classify_text

# §6.3 四大失败类型映射（可配置分组，不写死引擎——红线 #11）。
# 默认映射基于 Atlas 现有 8 类聚成 4 桶 + 兜底 unknown。
# 分组表可被调用方覆盖（传 buckets=...），引擎不含业务口径。
DEFAULT_BUCKETS = {
    "environment_blocked": frozenset({"env_blocked", "interrupted"}),
    "data_issue": frozenset({"data"}),
    "assertion_contract": frozenset({"assertion", "contract"}),
    "interaction_session": frozenset({"interaction", "session"}),
}


@dataclass
class AllureFinding:
    """单条 Allure result 的解析结果。"""
    name: str
    full_name: str
    status: str           # Allure status: passed/failed/broken/skipped/unknown
    message: str
    trace: str
    labels: dict[str, str]
    fail_class: str       # Atlas FailureClass.value；passed 为空串
    evidence: dict = field(default_factory=dict)


def _bucket_of(fail_class: str, buckets: dict[str, frozenset]) -> str:
    """fail_class → 桶名；未命中任何桶归 unknown。"""
    for name, classes in buckets.items():
        if fail_class in classes:
            return name
    return "unknown"


def parse_allure_result(result: dict) -> AllureFinding:
    """单条 Allure result → 分类结果。

    若带 atlas.fail_class label 直接采用（自产往返，P1 emit 已保证）；
    否则从头回的 message/trace 文本经 failures.classify_text() 归类（外部 SCS 结果）。
    """
    status = str(result.get("status", "unknown") or "unknown")
    # labels 形态容错：[{"name":k,"value":v}] 或坏条目跳过
    raw_labels = result.get("labels")
    labels: dict[str, str] = {}
    if isinstance(raw_labels, list):
        for label in raw_labels:
            if isinstance(label, dict):
                name = label.get("name")
                value = label.get("value")
                if name is not None:
                    labels[str(name)] = str(value)
    status_details = result.get("statusDetails")
    if not isinstance(status_details, dict):
        status_details = {}
    message = str(status_details.get("message", "") or "")
    trace = str(status_details.get("trace", "") or "")

    # atlas.fail_class label 直接采用（自产往返）
    label_fc = labels.get("atlas.fail_class")
    if label_fc and label_fc in {cls.value for cls in FailureClass}:
        fail_class = label_fc
    elif status == "passed":
        fail_class = ""
    else:
        # failed/broken/skipped/unknown：从头回的 message/trace 文本归类
        fail_class = classify_text(f"{message} {trace}").value

    # evidence：附件清单 + env
    evidence: dict = {}
    raw_att = result.get("attachments")
    if isinstance(raw_att, list):
        att_list = []
        for att in raw_att:
            if isinstance(att, dict):
                att_list.append({"name": str(att.get("name", "")),
                                  "source": str(att.get("source", ""))})
        if att_list:
            evidence["attachments"] = att_list
    raw_params = result.get("parameters")
    if isinstance(raw_params, list):
        for p in raw_params:
            if isinstance(p, dict) and p.get("name") == "env":
                evidence["env"] = str(p.get("value", ""))
                break
    evidence["full_name"] = str(result.get("fullName", ""))

    return AllureFinding(
        name=str(result.get("name", "")),
        full_name=str(result.get("fullName", "")),
        status=status,
        message=message,
        trace=trace,
        labels=labels,
        fail_class=fail_class,
        evidence=evidence,
    )


def _load(results_dir: Path) -> tuple[list[AllureFinding], int]:
    """读结果集目录的所有 *-result.json，返回 (findings, skipped_malformed_files)。

    #131/#112 读侧纪律：单文件坏字节 replace 回落、单文件坏 JSON/非对象跳过并计数，
    绝不因一条坏结果整份分析崩（这正是"感知器官坏了却读成无失败"的假阴性温床）。
    """
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        raise FileNotFoundError(f"结果集目录不存在：{results_dir}")
    findings: list[AllureFinding] = []
    skipped = 0
    for fpath in sorted(results_dir.glob("*-result.json")):
        # 先严格读保真；撞坏字节回落 errors="replace"——残行 JSON 解析必败自然落 skipped 计数
        try:
            raw = fpath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
        except OSError:
            skipped += 1
            continue
        try:
            result = json_loads(raw)
        except Exception:
            skipped += 1
            continue
        if not isinstance(result, dict):
            skipped += 1
            continue
        findings.append(parse_allure_result(result))
    return findings, skipped


def json_loads(raw: str):
    """json.loads 的薄包装（单独抽出来方便测试注入坏形态）。"""
    import json
    return json.loads(raw)


def load_allure_results(results_dir: Path) -> list[AllureFinding]:
    """读结果集目录的所有 *-result.json → list[AllureFinding]。

    坏文件跳过并计数（通过 stderr 警告），绝不因一条坏结果整份分析崩。
    目录不存在 → FileNotFoundError（CLI 接 _user_error rc 2）。
    """
    findings, skipped = _load(results_dir)
    if skipped:
        import sys
        print(f"警告：{results_dir} 有 {skipped} 个 *-result.json 无法解析（坏字节/坏 JSON/非对象），已跳过",
              file=sys.stderr)
    return findings


def analyze_allure(results_dir: Path, *, knowledge=None, to_tower=None,
                   buckets: dict[str, frozenset] | None = None) -> dict:
    """汇总 Allure 结果集：总数/通过/失败/跳过 + fail_class 分布 + 四桶 + 命中坑点 + 自愈提案。

    - knowledge=KnowledgeBase 时对 failed/broken finding 做坑点匹配（status="approved" 准入）。
    - to_tower=ExperienceJournal 时未命中项登记 pending_review 草稿进既有审核塔（不新开塔、
      不新增知识写路径——红线 #9/#10）。
    - 自愈提案只提案不执行；执行仍走 healer.can_auto_heal（只读 Task + status="approved" +
      非控制流 #108）。控制流异常（interrupted）一律拒自愈。
    - buckets 可覆盖 DEFAULT_BUCKETS（§6.3 可配置分组，不写死引擎——红线 #11）。
    """
    findings, skipped = _load(results_dir)
    bucket_map = buckets if buckets is not None else DEFAULT_BUCKETS

    # Allure status 计数
    counts = {"passed": 0, "failed": 0, "broken": 0, "skipped": 0, "unknown": 0}
    for f in findings:
        if f.status in counts:
            counts[f.status] += 1
        else:
            counts["unknown"] += 1

    # fail_class 分布（仅 failed/broken——真实失败才进缺陷分布）
    fc_dist: dict[str, int] = {}
    # 四桶分布（仅 failed/broken）
    bucket_counts = {name: 0 for name in bucket_map}
    bucket_counts["unknown"] = 0

    hit_pitfalls: list[dict] = []
    heal_proposals: list[dict] = []
    tower_drafts: list[str] = []

    for f in findings:
        if f.status not in ("failed", "broken"):
            continue
        if not f.fail_class:
            continue
        # fail_class 分布
        fc_dist[f.fail_class] = fc_dist.get(f.fail_class, 0) + 1
        # 四桶
        bucket = _bucket_of(f.fail_class, bucket_map)
        bucket_counts[bucket] += 1

        # 控制流异常免疫自愈（#108）：interrupted 一律不产出 heal 提案
        if f.fail_class == "interrupted":
            continue

        text = f"{f.message} {f.trace}"
        # 坑点匹配（自愈准入 status="approved"）
        hit = None
        if knowledge is not None:
            hit = knowledge.match_pitfall_from_text(text, status="approved")
        if hit and hit.get("remedy"):
            hit_pitfalls.append({
                "name": f.name, "full_name": f.full_name,
                "fail_class": f.fail_class,
                "pitfall_id": hit["id"], "remedy": hit["remedy"],
            })
            heal_proposals.append({
                "type": "heal_proposal", "case": f.name,
                "full_name": f.full_name, "fail_class": f.fail_class,
                "pitfall_id": hit["id"], "remedy": hit["remedy"],
            })
        elif to_tower is not None:
            # 未命中 → pending_review 草稿进既有审核塔（不新开塔、不新增知识写路径）
            draft = to_tower.draft_from_text(
                task=f.name or f.full_name,
                text=text,
                failure_class=f.fail_class,
                evidence={"source": "allure", **f.evidence},
            )
            to_tower.append_draft(draft)
            tower_drafts.append(draft["id"])

    return {
        "results_dir": str(results_dir),
        "total": len(findings),
        "passed": counts["passed"],
        "failed": counts["failed"],
        "broken": counts["broken"],
        "skipped": counts["skipped"],
        "unknown": counts["unknown"],
        "skipped_malformed_files": skipped,
        "fail_class_distribution": fc_dist,
        "buckets": bucket_counts,
        "hit_pitfalls": hit_pitfalls,
        "heal_proposals": heal_proposals,
        "tower_drafts": tower_drafts,
    }


def format_text(result: dict) -> str:
    """人读版输出（--format text）。"""
    lines = [f"Allure 分析：{result['results_dir']}"]
    lines.append(
        f"总计 {result['total']}：passed {result['passed']} / "
        f"failed {result['failed']} / broken {result['broken']} / "
        f"skipped {result['skipped']} / unknown {result['unknown']}"
    )
    if result["skipped_malformed_files"]:
        lines.append(f"坏文件跳过 {result['skipped_malformed_files']}")

    if result["fail_class_distribution"]:
        lines.append("")
        lines.append("失败分类分布：")
        for fc, count in sorted(result["fail_class_distribution"].items()):
            lines.append(f"  {fc}: {count}")

    if result["buckets"]:
        lines.append("")
        lines.append("四桶分布：")
        for bucket, count in sorted(result["buckets"].items()):
            lines.append(f"  {bucket}: {count}")

    if result["hit_pitfalls"]:
        lines.append("")
        lines.append(f"命中坑点（{len(result['hit_pitfalls'])}）：")
        for hp in result["hit_pitfalls"]:
            lines.append(
                f"  - {hp['name']} ({hp['fail_class']}) → "
                f"pitfall {hp['pitfall_id']}: {hp['remedy']}"
            )

    if result["heal_proposals"]:
        lines.append("")
        lines.append(f"自愈提案（{len(result['heal_proposals'])}，只提案不执行）：")
        for hp in result["heal_proposals"]:
            lines.append(
                f"  - {hp['case']}: 应用 pitfall {hp['pitfall_id']} 的 remedy"
            )

    if result["tower_drafts"]:
        lines.append("")
        lines.append(f"审核塔草稿（{len(result['tower_drafts'])}，待 atlas review 人审）：")
        for did in result["tower_drafts"]:
            lines.append(f"  - {did}")

    if not any(result[k] for k in ("hit_pitfalls", "heal_proposals", "tower_drafts")):
        lines.append("")
        lines.append("无命中坑点、无自愈提案、无塔草稿。")

    return "\n".join(lines)

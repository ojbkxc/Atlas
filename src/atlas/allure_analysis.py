"""Allure 2.39.0 结果集解析（汇入侧）：Allure → Atlas 分类 → 自愈提案 / 审核塔草稿。

对应 Gap5 §4（汇入方案）、§6（失败分类口径）、§6.3（四大失败类型 4 桶映射）。

纪律：
- 解析层只做翻译/归类，无改断言路径（红线 #7）。
- 不新增知识写路径：产出只进 pending_review 草稿 → 既有 atlas review 塔（红线 #9/#10）。
- 控制流异常（interrupted）一律拒自愈（#108）。
- 读侧容错：坏字节/坏 JSON/腰斩不整份崩（#131/#112）。

pf-9（wave4 C 轴增量读）：结果集只追加不改写（Allure 自产即如此），读侧按
(mtime_ns, size) 指纹跳过未变 result——大套件（数百 run）每次全量重解析是报告侧最大
单点（warm 3.8s/202 results）。指纹缓存落 `atlas-analyze-fingerprint.json`（atlas- 前缀，
clean 随结果文件同生命周期）。语义约束：新落盘/指纹变更的 result 必读（负向对照钉死）；
坏文件（解析失败）**不写指纹**——每次计数 skipped，容错纪律不绕过；fail_class 判据一字
不动（增量只是跳过重复解析，findings 内容同源）；指纹缓存坏/缺失 → WARN 一行 + 全量解析
（缺失是首次运行常态，坏缓存当无缓存，点名不静默）。
"""
from __future__ import annotations
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .atomic_io import atomic_write_text, read_json_text, read_text_retry
from .failures import FailureClass, OPERATIONAL, classify_text

# 增量读指纹缓存文件名（pf-9）：atlas- 前缀 → emit_allure 的 clean 随结果文件同生命周期；
# 刻意不落 *-result.json 形态——解析侧只认该形态，缓存不会被误当一条结果。
_FINGERPRINT_FILENAME = "atlas-analyze-fingerprint.json"

# §6.3 四大失败类型映射（可配置分组，不写死引擎——红线 #11）。
# 默认映射基于 Atlas 现有失败类聚成 4 桶 + 兜底 unknown。
# 分组表可被调用方覆盖（传 buckets=...），引擎不含业务口径。
DEFAULT_BUCKETS = {
    # 桶**键名**是对外口径，C9 加成员时不改名（改名会破既有消费者与外部看板）。
    # 成员清单不再在这里手抄：从 `failures.OPERATIONAL` 派生——家族判据只有一处（#92）。
    # 手抄的历史代价：本行曾写着两个字面量，C9 加第三个成员若漏改这里，「等人」就会被
    # 读进缺陷桶（把非缺陷洗成缺陷=红线 #7 的反方向，同样是失真）。
    # 语义=「操作/环境族桶」：env_blocked（策略挡住）/interrupted（宿主终止）/
    # waiting（在等人处置）——三者都不是产品缺陷。
    "environment_blocked": frozenset(OPERATIONAL),
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
    assertions: int = 0   # C2：Allure steps 计数（design_c2 §2「断言数……或 Allure steps 计数」）


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

    # C2 三元组的 assertions 来源：Allure steps 计数（design_c2 §2 口径，来源见 command 语义）。
    # 形态容错同 #131：steps 非列表当 0，不猜不崩。
    raw_steps = result.get("steps")
    assertions = len(raw_steps) if isinstance(raw_steps, list) else 0

    return AllureFinding(
        name=str(result.get("name", "")),
        full_name=str(result.get("fullName", "")),
        status=status,
        message=message,
        trace=trace,
        labels=labels,
        fail_class=fail_class,
        evidence=evidence,
        assertions=assertions,
    )


def _fingerprint_path(results_dir: Path) -> Path:
    return Path(results_dir) / _FINGERPRINT_FILENAME


def _read_fingerprint(results_dir: Path) -> dict:
    """读增量指纹缓存 {文件名: [mtime_ns, size]}；缺失（首次运行常态）/坏 JSON/坏形态
    → 空表 + WARN 一行（点名不静默，坏缓存当无缓存全量解析）。"""
    fp = _fingerprint_path(results_dir)
    if not fp.exists():
        return {}
    try:
        # BOM 安全（单点口径 `utf-8-sig`）：指纹文件带 BOM 时裸 utf-8 会让 `json_loads`
        # 失败 → 缓存直接失效回「本次全量解析」，且只打一行 stderr，看上去像没毛病。
        raw = read_json_text(fp)
    except OSError:
        print(f"警告：{fp} 读取失败，本次全量解析", file=sys.stderr)
        return {}
    try:
        data = json_loads(raw)
    except Exception:
        print(f"警告：{fp} 坏 JSON，本次全量解析", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"警告：{fp} 形态非映射，本次全量解析", file=sys.stderr)
        return {}
    return data


def _file_sig(fpath: Path) -> list:
    """文件指纹 [mtime_ns, size]——Allure 结果集只追加不改写，这两个字段足以判定「未变」。"""
    st = fpath.stat()
    return [st.st_mtime_ns, st.st_size]


def _write_fingerprint(results_dir: Path, touched: dict) -> None:
    """指纹缓存落盘（原子写，wave4 纪律；写失败不顶分析——WARN 一行，下次全量解析）。

    缓存形态 {文件名: {"sig": [mtime_ns, size], "finding": <AllureFinding 的 JSON 形态>}}——
    跳过未变文件时反序列化回填 findings（否则汇总归零，那是「跳过解析」被读成「结果消失」）。
    `touched` 必须含**未变条目**（缓存命中时原样保留）+ 新解析条目——只写新解析条目会把
    未变条目清空，下次运行全量重读，增量形同虚设（warm 重复分析正是本特性的存在理由）。
    """
    try:
        atomic_write_text(_fingerprint_path(results_dir),
                          json.dumps(touched, sort_keys=True, ensure_ascii=False))
    except OSError as exc:
        print(f"警告：指纹缓存写盘失败（{exc}），下次全量解析", file=sys.stderr)


def _load(results_dir: Path, *, incremental: bool = True) -> tuple[list[AllureFinding], int, int]:
    """读结果集目录的所有 *-result.json，返回 (findings, skipped_malformed_files, skipped_unchanged)。

    #131/#112 读侧纪律：单文件坏字节 replace 回落、单文件坏 JSON/非对象跳过并计数，
    绝不因一条坏结果整份分析崩（这正是"感知器官坏了却读成无失败"的假阴性温床）。

    pf-9 增量（incremental=True）：指纹未变的 result 跳过解析（skipped_unchanged 计数）；
    新落盘/mtime 或 size 变更的必读（负向对照钉死）；坏文件（解析失败）不写指纹——每次
    全量读并计数 skipped，容错纪律不被增量绕过；findings 内容与全量路径逐字同源
    （parse_allure_result 单一入口），fail_class 判据一字不动（红线 #7）。
    """
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        raise FileNotFoundError(f"结果集目录不存在：{results_dir}")
    findings: list[AllureFinding] = []
    skipped = 0
    unchanged = 0
    fp_cache = _read_fingerprint(results_dir) if incremental else {}
    touched: dict = {}
    for fpath in sorted(results_dir.glob("*-result.json")):
        if incremental:
            sig = _file_sig(fpath)
            cached = fp_cache.get(fpath.name)
            if isinstance(cached, dict) and cached.get("sig") == sig:
                finding = cached.get("finding")
                if isinstance(finding, dict):
                    try:
                        findings.append(AllureFinding(**finding))
                    except TypeError:
                        pass   # 缓存条目形态坏 → 落到下方全量解析（自愈，不点名刷屏）
                    else:
                        unchanged += 1
                        # 未变条目原样保留——只写新解析条目会把未变条目清空，
                        # 下次运行全量重读，增量形同虚设（warm 重复分析是本特性的存在理由）
                        touched[fpath.name] = cached
                        continue
        # 先严格读保真；撞坏字节回落 errors="replace"——残行 JSON 解析必败自然落 skipped 计数
        # BOM 口径走单点 `read_json_text`（`utf-8-sig`）：BOM 是合法 UTF-8，裸 utf-8 不报解码错，
        # 而是把 `\ufeff` 留在首字符 → `json.loads` 失败 → 整份 result 文件被计 skipped **静默丢弃**。
        try:
            raw = read_json_text(fpath)
        except UnicodeDecodeError:
            try:
                raw = read_text_retry(fpath, encoding="utf-8-sig", errors="replace")
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
        finding = parse_allure_result(result)
        findings.append(finding)
        if incremental:
            touched[fpath.name] = {"sig": sig, "finding": asdict(finding)}
    if incremental:
        _write_fingerprint(results_dir, touched)
    return findings, skipped, unchanged


def json_loads(raw: str):
    """json.loads 的薄包装（单独抽出来方便测试注入坏形态）。"""
    import json
    return json.loads(raw)


def load_allure_results(results_dir: Path, *, incremental: bool = True) -> list[AllureFinding]:
    """读结果集目录的所有 *-result.json → list[AllureFinding]。

    坏文件跳过并计数（通过 stderr 警告），绝不因一条坏结果整份分析崩。
    目录不存在 → FileNotFoundError（CLI 接 _user_error rc 2）。
    pf-9：incremental=True（默认）跳过指纹未变的 result（见 _load）。
    """
    findings, skipped, _unchanged = _load(results_dir, incremental=incremental)
    if skipped:
        print(f"警告：{results_dir} 有 {skipped} 个 *-result.json 无法解析（坏字节/坏 JSON/非对象），已跳过",
              file=sys.stderr)
    return findings


def build_allure_verification(finding: AllureFinding, results_dir: Path | str) -> dict:
    """C2（design_c2 §6-7）：Allure finding → evidence.verification 三元组。

    verdict 由 Allure status 派生：passed→pass，其余（failed/broken/skipped/unknown）→fail；
    exit_code 同向（0/1）。command 是可重跑的解析命令——原结果集能否重跑真实用例未证
    （design_c2 §8），来源标注 allure 分析（evidence.source），人审自行判断证据强度。"""
    return {
        "command": f"atlas allure-analyze {results_dir}",
        "exit_code": 0 if finding.status == "passed" else 1,
        "assertions": finding.assertions,
        "verdict": "pass" if finding.status == "passed" else "fail",
    }


def analyze_allure(results_dir: Path, *, knowledge=None, to_tower=None,
                   buckets: dict[str, frozenset] | None = None,
                   incremental: bool = True) -> dict:
    """汇总 Allure 结果集：总数/通过/失败/跳过 + fail_class 分布 + 四桶 + 命中坑点 + 自愈提案。

    - knowledge=KnowledgeBase 时对 failed/broken finding 做坑点匹配（status="approved" 准入）。
    - to_tower=ExperienceJournal 时未命中项登记 pending_review 草稿进既有审核塔（不新开塔、
      不新增知识写路径——红线 #9/#10）。
    - 自愈提案只提案不执行；执行仍走 healer.can_auto_heal（只读 Task + status="approved" +
      非控制流 #108）。控制流异常（interrupted）一律拒自愈。
    - buckets 可覆盖 DEFAULT_BUCKETS（§6.3 可配置分组，不写死引擎——红线 #11）。
    - pf-9：incremental=True（默认）读侧按指纹跳过未变 result（见 _load），汇总结果与
      全量路径等价（findings 同源）；skipped_unchanged 键如实呈现本次跳过数。
    """
    findings, skipped, unchanged = _load(results_dir, incremental=incremental)
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
            # C2：草稿携带 verification 三元组（build_allure_verification，见其 docstring）
            draft = to_tower.draft_from_text(
                task=f.name or f.full_name,
                text=text,
                failure_class=f.fail_class,
                evidence={"source": "allure", **f.evidence,
                          "verification": build_allure_verification(f, results_dir)},
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
        "skipped_unchanged": unchanged,
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
    if result["skipped_unchanged"]:
        lines.append(f"增量跳过未变 result {result['skipped_unchanged']}（pf-9）")

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

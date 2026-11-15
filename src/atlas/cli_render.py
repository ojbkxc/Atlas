"""CLI 统一渲染层（wave4 B 轴 ae-1~ae-4）：机读 JSON 的唯一出口 + 人读文本视图。

为什么单开一个模块：cli.py 曾有 19+ 处散写的 `print(json.dumps(...))`——机读形态
（ensure_ascii / indent / 键序）散在各命令分支，改一处漏一处（#92 同族），而
「汇总行 + verdict + 分组 + hints」的人读面更是没有统一骨架。本模块收敛两件事：

1. **机读出口**：`dump_json`（indent=2）/`dump_line`（单行紧凑）与旧散写
   `print(json.dumps(..., ensure_ascii=False, indent=2))` 逐字节等值
   （tests/test_cli_render.py 锁步钉死）。CLI 的 JSON print 收敛到这里，语义零变化——
   只动出口，不动任何键、序、判据与 rc。
2. **人读视图**：各命令 `--format text`（显式 opt-in）输出「一行汇总/verdict +
   分组列出 + 每组末尾 hints」。**默认档仍是机读 JSON 逐字节不变**——存量 27+ 处
   测试与外部消费方 json.loads(stdout)（tests/test_state_reporting_cli.py 等），
   且本波成员只准动 cli.py / cli_render.py / tests/test_cli_render.py 三个文件，
   翻默认=大面积翻红违「不新增失败」；人读默认化留给后续波次拍板。

边界：本模块不做任何判定——verdict 只是 CLI 已算出的 checks/payload 事实的**呈现**
（判据唯一实现在 doctor 分支/boundaries/versioning 等，这里零复制 #92）；
渲染器对缺键/坏形态回落不抛——渲染层不得先于被渲染对象死（#89 家族）；
hints 只点「改哪个 env 键 / 哪个文件」（ae-4 文案口径），不出现凭据值。
"""
from __future__ import annotations

import json
import sys

__all__ = ["dump_json", "dump_line", "doctor_text", "tasks_text", "contracts_text",
           "contracts_validate_text", "library_text", "selectors_text", "summary_text",
           "review_stats_text", "schedule_status_text"]


def dump_json(payload, *, stream=None) -> None:
    """机读 JSON（indent=2, ensure_ascii=False）——cli.py 全部多行 JSON print 的唯一出口。

    与旧散写 `print(json.dumps(x, ensure_ascii=False, indent=2))` 逐字节等值；
    任何序列化参数漂移（indent/ensure_ascii/键序）都会被 tests/test_cli_render.py 的
    锁步测当场钉红。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream or sys.stdout)


def dump_line(payload, *, stream=None) -> None:
    """机读 JSON（单行紧凑）——决策回执等短 payload 的唯一出口（旧 ensure_ascii=False 无 indent 形态）。"""
    print(json.dumps(payload, ensure_ascii=False), file=stream or sys.stdout)


def _counts(counter) -> str:
    """计数 dict → 'a=1, b=2'（键排序，呈现稳定；空 → ''）。"""
    return ", ".join(f"{k}={v}" for k, v in sorted((counter or {}).items()))


def _fmt_counts(prefix: str, counter) -> str:
    """计数 dict → 'prefix: a=1, b=2'；无计数回落 '-'（不冒充空分布）。"""
    counted = _counts(counter)
    return f"{prefix} {counted}" if counted else f"{prefix} -"


# ── ae-2：doctor 人读视图 ─────────────────────────────────────────


def _doctor_asset_bad(checks: dict) -> list[str]:
    """doctor 资产事实的呈现面清单（只读 checks 已算好的布尔，判据在 doctor 分支）。"""
    bad = []
    if not checks.get("legacy_pages"):
        bad.append("legacy-pages")
    if not checks.get("demo_api"):
        bad.append("demo-api")
    if checks.get("docker_unreadable"):
        bad.append("dockerfile-unreadable")
    if checks.get("docker_ref_missing"):
        bad.append(f"docker-ref:{checks['docker_ref_missing']}")
    if checks.get("docker_cmd_matches_compose") is False:
        bad.append("compose-cmd")
    if checks.get("docker_assets_consistent") is False and not bad:
        bad.append("docker")
    return bad


def _doctor_boundary_state(checks: dict) -> tuple[str, dict]:
    """boundary verdict 令牌与账本 dict（判据在 boundaries.py/doctor 分支，这里只归纳）。"""
    ledger = checks.get("boundary_ledger") if isinstance(checks.get("boundary_ledger"), dict) else {}
    hard = len(ledger.get("problems") or []) + len(ledger.get("uncovered_regions") or [])
    warns = len(ledger.get("warnings") or [])
    if not ledger.get("present"):
        return "boundary-missing", ledger
    if hard:
        return f"boundary-bad({hard})", ledger
    if warns:
        return f"boundary-warn({warns})", ledger
    return "boundary-ok", ledger


def _doctor_hints(checks: dict, ledger: dict) -> dict[str, list[str]]:
    """每组末尾 hints：只点「改哪个 env 键 / 哪个文件」（ae-4 同口径），不回显任何值。"""
    hints: dict[str, list[str]] = {}
    env_key = f"{checks.get('env_prefix', '')}URL_{str(checks.get('env', 'sit')).upper()}"
    if not checks.get("legacy_pages"):
        hints.setdefault("环境", []).append(
            "legacy-pages 缺失：核对 ATLAS_LEGACY_PROJECT / ATLAS_LEGACY_WEB 指向的旧项目"
            "是否有 <web>/lib/pages.py 与 pytest.ini")
    if not checks.get("demo_api"):
        hints.setdefault("环境", []).append(
            "demo-api 缺失：核对 ATLAS_DEMO_API_PROJECT / ATLAS_DEMO_API_FILE"
            "（旧项目 scripts 目录下的适配模块）")
    if checks.get("base_url_source", "unset") == "unset":
        hints.setdefault("环境", []).append(
            f"base_url 未配置：把 {env_key}=<URL> 写进项目 .env（Atlas .env 缺失时继承旧项目 .env）")
    kb = (checks.get("version_triple") or {}).get("knowledge") or {}
    kb_source = kb.get("source", "unknown")
    if kb_source == "builtin":
        hints.setdefault("知识", []).append(
            "knowledge=builtin：设 ATLAS_KNOWLEDGE_DIR 指向私有知识库"
            "（当前跑的是包内虚构知识，批准写路径也写不进包内）")
    elif kb_source == "unreadable":
        hints.setdefault("知识", []).append(
            f"knowledge=unreadable：{kb.get('error', '读取失败')}——检查 ATLAS_KNOWLEDGE_DIR 指向目录")
    tz = (checks.get("version_triple") or {}).get("tz_data") or {}
    if tz and tz.get("ok") is False:
        hints.setdefault("知识", []).append(
            f"tz_data 缺失：{tz.get('hint') or 'pip install tzdata'}（不静默按 UTC 跑）")
    if checks.get("docker_unreadable"):
        hints.setdefault("资产", []).append(
            f"Dockerfile 读不出（权限/编码）：{checks.get('docker_unreadable')}")
    if checks.get("docker_ref_missing"):
        hints.setdefault("资产", []).append(
            f"Dockerfile 引用了不存在的 {checks.get('docker_ref_missing')}——"
            "补齐该文件或更新 Dockerfile")
    if checks.get("docker_cmd_matches_compose") is False:
        hints.setdefault("资产", []).append(
            "Dockerfile 与 docker-compose.yml 的 python/python3 命令不一致——"
            "统一两边的 pytest 调用命令")
    if not ledger.get("present"):
        hints.setdefault("边界", []).append(
            f"无边界账本（{checks.get('boundary_ledger', {}).get('path', 'docs/ownership/atlas-boundaries.json')}"
            "）——历史分支/精简克隆属正常；吸收/merge 前先跑 "
            "PYTHONPATH=src python scripts/boundary_classify.py --diff <变更集>")
    else:
        if ledger.get("problems"):
            hints.setdefault("边界", []).append(
                "账本存在但非法（problems 见上）——修 docs/ownership/atlas-boundaries.json"
                "（accepted 债必须带 decision_log）")
        if ledger.get("uncovered_regions"):
            hints.setdefault("边界", []).append(
                "磁盘存在但账本未登记的区域（uncovered_regions 见上）——补账本条目")
        if ledger.get("warnings"):
            hints.setdefault("边界", []).append(
                "账本警告见上（optional 区域全缺属正常提示）")
    problems = (checks.get("ui_family") or {}).get("prefix_problems")
    if problems:
        hints.setdefault("版本", []).append(
            f"ui_family 前缀不一致 {len(problems)} 处（展示项不并 rc）——"
            "核对组件库 selectors 前缀与声明族（atlas selectors）")
    return hints


def doctor_text(checks: dict) -> str:
    """`atlas doctor --format text`：顶部一行 verdict + 环境/知识/资产/边界/版本五组 + 每组末尾 hints。

    verdict 形态（ae-2 任务例）：`KNOWLEDGE=builtin asset-bad(2) boundary-missing base-url=unset`。
    全部数据来自 CLI 已算好的 checks dict；缺键回落 '?'/'unknown'，不抛（渲染层不死先）。"""
    vt = checks.get("version_triple") if isinstance(checks.get("version_triple"), dict) else {}
    kb = vt.get("knowledge") if isinstance(vt.get("knowledge"), dict) else {}
    tz = vt.get("tz_data") if isinstance(vt.get("tz_data"), dict) else {}
    kb_source = kb.get("source", "unknown")

    asset_bad = _doctor_asset_bad(checks)
    asset = "asset-ok" if not asset_bad else f"asset-bad({len(asset_bad)})"
    boundary, ledger = _doctor_boundary_state(checks)
    base_url = str(checks.get("base_url_source", "unset"))

    lines = [f"KNOWLEDGE={kb_source} {asset} {boundary} base-url={base_url}", ""]

    # ── 环境 ──
    env_key = f"{checks.get('env_prefix', '')}URL_{str(checks.get('env', 'sit')).upper()}"
    lines.append("[环境]")
    lines.append(f"  python={checks.get('python', '?')}  env={checks.get('env', '?')}"
                 f"  env_prefix={checks.get('env_prefix', '?')}")
    lines.append(f"  root={checks.get('root', '?')}")
    lines.append(f"  base_url={base_url}（键 {env_key}）  legacy_web={checks.get('legacy_web', '?')}")
    lines.append(f"  legacy_pages={'ok' if checks.get('legacy_pages') else '缺失'}"
                 f"  legacy_pytest_ini={'ok' if checks.get('legacy_pytest_ini') else '缺失'}"
                 f"  demo_api={'ok' if checks.get('demo_api') else '缺失'}")
    lines += _hints_lines(_doctor_hints(checks, ledger).get("环境"))
    lines.append("")

    # ── 知识 ──
    lines.append("[知识]")
    lines.append(f"  pitfalls={checks.get('knowledge_pitfalls', '?')}"
                 f"  knowledge={kb_source} sha1={kb.get('sha1', '?')}"
                 f"（files={kb.get('files', '?')}）")
    if tz:
        lines.append(f"  tz_data={'ok' if tz.get('ok') else '缺失'}"
                     f"（{tz.get('name') or '未知时区'}）")
    lines += _hints_lines(_doctor_hints(checks, ledger).get("知识"))
    lines.append("")

    # ── 资产 ──
    lines.append("[资产]")
    lines.append(f"  docker={'ok' if checks.get('docker_assets_consistent') else '不一致'}"
                 f"  compose_cmd={'一致' if checks.get('docker_cmd_matches_compose', True) else '漂移'}")
    lines += _hints_lines(_doctor_hints(checks, ledger).get("资产"))
    lines.append("")

    # ── 边界 ──
    lines.append("[边界]")
    if not ledger.get("present"):
        lines.append(f"  ledger=缺失（{ledger.get('path', 'docs/ownership/atlas-boundaries.json')}）")
    else:
        lines.append(f"  ledger=present regions={ledger.get('regions', 0)}"
                     f"  problems={len(ledger.get('problems') or [])}"
                     f"  warnings={len(ledger.get('warnings') or [])}"
                     f"  uncovered={len(ledger.get('uncovered_regions') or [])}")
        for problem in ledger.get("problems") or []:
            lines.append(f"    PROBLEM: {problem}")
        for warning in ledger.get("warnings") or []:
            lines.append(f"    WARN: {warning}")
        for uncovered in ledger.get("uncovered_regions") or []:
            lines.append(f"    UNCOVERED: {uncovered}")
        for debt in ledger.get("accepted_debts") or []:
            tail = (debt or {}).get("decision_log_tail") or {}
            lines.append(f"    债 {debt.get('id', '?')}（{tail.get('decision', '?')}"
                         f" {tail.get('date', '?')} by {tail.get('by', '?')}）")
    lines += _hints_lines(_doctor_hints(checks, ledger).get("边界"))
    lines.append("")

    # ── 版本 ──
    ui_family = checks.get("ui_family") if isinstance(checks.get("ui_family"), dict) else {}
    lines.append("[版本]")
    lines.append(f"  atlas={vt.get('atlas', '?')}"
                 f"  ui_family declared={ui_family.get('declared', '?')}"
                 f" resolved={ui_family.get('resolved', '?')}"
                 f" prefix_problems={len(ui_family.get('prefix_problems') or [])}")
    lines += _hints_lines(_doctor_hints(checks, ledger).get("版本"))
    return "\n".join(lines)


def _hints_lines(hints) -> list[str]:
    """一组 hints → 缩进行（无 hints → 空列表，组末尾不冒充空提示）。"""
    if not hints:
        return []
    return ["  hints:"] + [f"  - {hint}" for hint in hints]


# ── ae-3：汇总行 + 分组列出 ───────────────────────────────────────


def tasks_text(rows) -> str:
    """`atlas tasks --format text`：汇总行（计数+通道分布+读写分布）+ 按通道分组列出。"""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    by_channel: dict[str, list[dict]] = {}
    for r in rows:
        by_channel.setdefault(str(r.get("channel", "?")), []).append(r)
    reads = sum(1 for r in rows if r.get("read_only"))
    chans = " ".join(f"{ch}={len(ts)}" for ch, ts in sorted(by_channel.items()))
    lines = [f"tasks: 共 {len(rows)} 个（{chans or '-'}） 只读={reads} 写={len(rows) - reads}", ""]
    for ch, ts in sorted(by_channel.items()):
        lines.append(f"[{ch}]")
        for r in ts:
            produces = ",".join(r.get("produces") or [])
            lines.append(f"  {r.get('name', '?')}  {r.get('intent', '')}"
                         + ("" if r.get("read_only") else "  [写]")
                         + (f"  produces={produces}" if produces else ""))
        lines.append("")
    lines.append("hints:")
    lines.append("- 拼装到目标：atlas compose --goal <产出键>；规划与审批：atlas agent --goal <产出键>")
    return "\n".join(lines)


def contracts_text(report: dict) -> str:
    """`atlas contracts --format text`：汇总行（总数+分级分布）+ 按等级分组列出。"""
    levels = report.get("levels") if isinstance(report.get("levels"), dict) else {}
    contracts = [c for c in (report.get("contracts") or []) if isinstance(c, dict)]
    by_level: dict[str, list[dict]] = {}
    for c in contracts:
        by_level.setdefault(str(c.get("level", "?")), []).append(c)
    ordered = [lv for lv in ("core", "high", "minimal") if lv in by_level] + \
              sorted(k for k in by_level if k not in ("core", "high", "minimal"))
    lines = [f"contracts: 共 {report.get('total', len(contracts))} 条"
             f"（{_counts(levels) or '-'}）", ""]
    for lv in ordered:
        lines.append(f"[{lv}]")
        for c in by_level[lv]:
            lines.append(f"  {c.get('method', '?')} {c.get('path', '') or '(空 path)'}  {c.get('name', '')}")
        lines.append("")
    lines.append("hints:")
    lines.append("- 结构自校验（空 path/name 冒充覆盖率的条目会被点名）：atlas contracts --validate")
    return "\n".join(lines)


def contracts_validate_text(report: dict) -> str:
    """`atlas contracts --validate --format text`：verdict 行 + problems 点名清单 + hints。"""
    problems = [p for p in (report.get("problems") or []) if isinstance(p, str)]
    lines = [f"contracts --validate: {'ok' if report.get('ok') else f'problems({len(problems)})'}"
             f"（total={report.get('total', 0)}）", ""]
    for p in problems:
        lines.append(f"  PROBLEM: {p}")
    lines.append("")
    lines.append("hints:")
    lines.append("- 修对应契约文件：私有库设 ATLAS_KNOWLEDGE_DIR 指到，"
                 "contracts/*.json（未设则改包内 plugins/demo/knowledge/contracts/）")
    return "\n".join(lines)


def library_text(payload: dict) -> str:
    """`atlas library --format text`：总数汇总行 + 条目一行一个（单条详情仍是机读 JSON）。"""
    tasks = [t for t in (payload.get("tasks") or []) if isinstance(t, dict)]
    if not tasks:
        return ("library: 空库（0 条）——flow 声明 harvest: true 跑绿后"
                "经 atlas review --approve <id> 批准入库")
    lines = [f"library: 共 {payload.get('total', len(tasks))} 条"]
    for t in tasks:
        lines.append(f"  {t.get('name', '')}  steps={t.get('steps', 0)}"
                     f"  source={t.get('source', '') or '-'}"
                     f"  created_at={t.get('created_at', '') or '-'}")
    lines.append("")
    lines.append("hints:")
    lines.append("- 复用：atlas library <名> 看 steps/assert 原文作 flow 骨架（机读 JSON）")
    return "\n".join(lines)


def selectors_text(payload: dict) -> str:
    """`atlas selectors --format text`（列表形态）：组件计数 + 缺选择器点名。"""
    comps = [c for c in (payload.get("components") or []) if isinstance(c, str)]
    missing = [m for m in (payload.get("without_selectors") or []) if isinstance(m, str)]
    lines = [f"selectors: 组件 {len(comps)} 个，缺选择器 {len(missing)} 个", ""]
    for c in comps:
        lines.append(f"  {c}" + ("  缺" if c in missing else ""))
    lines.append("")
    lines.append("hints:")
    lines.append("- 缺选择器的组件只有知识说明、不可直接定位：经 explore/审塔补 selector_candidate"
                 "（批准走 atlas review --approve，写路径唯一）")
    return "\n".join(lines)


def _attention_kv(attention: dict) -> str:
    """attention 块的「键=值」行——**键从协议 dict 迭代取**（不把 "waiting" 等字面量
    抄进本模块：src 的等待类字面量哨兵只许出现在判据/映射处，散写一处=#92 温床；
    迭代还有个正收益：run_attention 将来加键，文本视图自动跟上）。
    notes 单独渲染（它是要逐条列出的列表）；None 值如实显示 '-'（塔没读=不冒充 0）。"""
    parts = []
    for key, value in attention.items():
        if key == "notes":
            continue
        parts.append(f"{key}={value if value is not None else '-'}")
    return " ".join(parts) or "-"


def summary_text(result: dict) -> str:
    """`atlas summary --format text`：一行汇总 + 失败类 + attention 键值 + 版本漂移。"""
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    attention = result.get("attention") if isinstance(result.get("attention"), dict) else {}
    versions = result.get("versions") if isinstance(result.get("versions"), dict) else {}
    drifted = versions.get("drifted") or []
    lines = [f"summary: runs={result.get('runs', 0)} cases={result.get('cases', 0)}"
             f" | {_counts(status) or '-'}",
             _fmt_counts("fail-classes:", result.get("fail_classes")),
             f"attention: {_attention_kv(attention)}"]
    for note in attention.get("notes") or []:
        lines.append(f"  note: {note}")
    if drifted:
        lines.append(f"versions: 漂移维度 {', '.join(str(d) for d in drifted)}"
                     "——同一维度出现多个值（知识被手改/引擎换版/flow 变更）")
    if versions.get("runs_without_version"):
        lines.append(f"versions: {versions['runs_without_version']} 条 run 未带版本三元组（旧记录）")
    if result.get("skipped_malformed_lines"):
        lines.append(f"skipped_malformed_lines={result['skipped_malformed_lines']}（残行已跳过）")
    lines.append("")
    lines.append("hints:")
    lines.append("- 全量分布与最慢步骤：--format json；等人/操作族看 attention 行找人，"
                 "timeout 查 --case-timeout 与用例耗时")
    return "\n".join(lines)


def review_stats_text(stats: dict) -> str:
    """`atlas review --stats --format text`：塔的吞吐汇总行 + 分类分布 + 清塔指路。"""
    lines = [f"review: pending={stats.get('pending', 0)} approved={stats.get('approved', 0)}"
             f" rejected={stats.get('rejected', 0)}（total={stats.get('total', 0)}）",
             _fmt_counts("pending_by_kind:", stats.get("pending_by_kind")),
             _fmt_counts("pending_by_class:", stats.get("pending_by_class")),
             "",
             "hints:",
             "- 看证据卡片：atlas review；批准：atlas review --approve <id> --remedy \"...\"；"
             "整类驳回：atlas review --reject-kind <kind> --reason \"...\""]
    return "\n".join(lines)


def schedule_status_text(rows) -> str:
    """`atlas schedule status --format text`：任务汇总行 + 每任务一行（错过/连败/下一槽）。"""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    paused = [r for r in rows if not r.get("enabled", True)]
    lines = [f"schedule status: 共 {len(rows)} 个任务"
             f"（enabled={len(rows) - len(paused)} paused={len(paused)}）"]
    if not rows:
        lines.append("  台账为空")
    for r in rows:
        if r.get("kind") == "cron":
            sched_bits = [f"cron {r.get('cron')}", f"tz={r.get('tz')}"]
        elif r.get("kind") == "once":
            sched_bits = [f"once @{r.get('at')}"]
        else:
            sched_bits = [str(r.get("kind"))]
        state = "paused" if not r.get("enabled", True) else "enabled"
        outcomes = r.get("outcomes") if isinstance(r.get("outcomes"), dict) else {}
        line = (f"  {r.get('id', '?')}  {' '.join(sched_bits)}  {state}"
                f"  连败={r.get('consecutive_failures', 0)}"
                f"  累计错过={r.get('skipped_total', 0)}"
                f"  next={r.get('next_slot') or '-'}")
        if outcomes:
            line += f"  outcomes: {_counts(outcomes)}"
        lines.append(line)
        if r.get("auto_paused_reason"):
            lines.append(f"    AUTO-PAUSED：{r['auto_paused_reason']}"
                         f"（恢复：atlas schedule enable {r.get('id', '?')}）")
    lines.append("")
    lines.append("hints:")
    lines.append("- 接线：atlas schedule crontab <id>（绝不代注册 OS 任务）；手动跑一轮：atlas schedule run <id>")
    return "\n".join(lines)

"""把 Atlas runs.jsonl 转换为 Allure 2.39.0 结果集（纯 stdlib，零新依赖）。

对应 Gap5 §3（方案 A 纯 stdlib 直出）与 §3.5 状态映射表：

- passed                        → passed
- failed（fail_class ∈ assertion/contract/data/interaction/session）→ failed + atlas.fail_class label
- env_blocked                   → skipped + atlas.fail_class=env_blocked、atlas.blocked=true（不进 failed 也不静默绿）
- interrupted                   → skipped + atlas.fail_class=interrupted、atlas.interrupted=true
- unknown                       → broken + atlas.fail_class=unknown

读侧复用 reporting 容错纪律（#131/#112）：UnicodeDecodeError → errors="replace"，
残行/非对象行跳过并计数，绝不因一条坏行整份失败。historyId=sha1(env|case) 稳定
（跨运行聚合趋势）；uuid 每份结果独立。clean 只删 Atlas 命名前缀（atlas-*）文件，
不误删用户附件。附件源缺失/坏字节降级为 text/plain 占位附件，不顶掉整份报告。

Gap6（产出侧对齐 UI AUTO TEST）：UI AUTO TEST 用 allure-pytest 适配器产出的 result.json
字段面（parentSuite/suite/subSuite/package/framework/language label、description、
titlePath）与 Allure 结果集的可选组成部分（categories.json / environment.properties /
executor.json）在本模块补齐——见 `_result_labels` / `default_categories` /
`write_environment_properties` / `write_executor`。全部为**附加**（不改/不删既有 label 与
键），`align=False`/`metadata=False` 可退回逐字旧形态（#115 零涟漪逃生舱）。
"""
from __future__ import annotations
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Allure 结果集目录名（与 `atlas allure` 默认输出、devops-sync 的读取子路径同值）。
# 单点定义：消费方 import 这里，不各自手抄字面量（#92 改一漏一）。
ALLURE_RESULTS_DIR = "allure-results"
_ATTACHMENT_PREFIX = "atlas-"   # Atlas 产物命名前缀；clean 只清这个

# UI AUTO TEST（allure-pytest）产出的 label 名：severity/parentSuite/suite/subSuite/host/
# thread/framework/language/package。Atlas 默认对齐其中**确定性可复现**的子集；host/thread
# 依赖运行机器（hostname / 线程名），离线确定性输出不掺机器标识——留空由调用方经
# `executor=` 注入，不猜（红线：不写假数据）。
_ALIGN_SUITE = "atlas"     # 对齐后 suite 值保持历史值不变（零涟漪）

# 真实失败类（§3.5 行 2）：env_blocked/interrupted/unknown 从设计上不是缺陷，单独映射
_REAL_FAIL_CLASSES = frozenset({"assertion", "contract", "data", "interaction", "session"})

_SUFFIX_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".txt": "text/plain", ".log": "text/plain", ".text": "text/plain",
    ".json": "application/json", ".har": "application/json",
    ".csv": "text/csv", ".html": "text/html", ".xml": "application/xml",
}


def _details(message: str, trace: str = "") -> dict:
    """Allure statusDetails 统一形态（§2.1）。"""
    return {"known": False, "muted": False, "flaky": False, "message": message, "trace": trace}


def _parse_iso_ms(value) -> int | None:
    """ISO8601 → epoch 毫秒；形态坏/缺失返回 None（调用方回落，不猜不崩）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _clean_atlas_files(out_dir: Path) -> None:
    """只删 Atlas 命名前缀（atlas-*）的文件，不误删用户附件/其他工具产物。

    删不掉的旧文件（Windows 文件锁等）不顶掉整份生成——残留旧结果只是噪音，
    崩溃则整份报告丢失（#64 同红线：报告可以有噪音，不能丢整份）。
    """
    for path in out_dir.iterdir():
        if path.is_file() and path.name.startswith(_ATTACHMENT_PREFIX):
            try:
                path.unlink()
            except OSError:
                pass


def _run_fail_class(run: dict) -> str | None:
    """run 内失败步骤的 fail_class：优先真实失败类（assertion/contract/data/...），
    否则回落到第一个带 fail_class 的步骤（env_blocked/interrupted/unknown）。

    为什么优先真实类：run 记 failed 时，被环境跳过的步骤（env_blocked）不 raise，
    真正抛出的步骤才是归因对象——若按出现顺序取第一个 fail_class，一条
    env-skipped 步骤会顶掉后面的真实断言失败，把 failed run 错映射成 skipped。
    """
    steps = run.get("steps", [])
    if not isinstance(steps, list):
        return None
    fallback = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        fail_class = step.get("fail_class")
        if not fail_class:
            continue
        fail_class = str(fail_class)
        if fallback is None:
            fallback = fail_class
        if fail_class in _REAL_FAIL_CLASSES:
            return fail_class
    return fallback


def _status_from_run(run: dict) -> tuple[str, dict, dict]:
    """Atlas run → (allure_status, statusDetails, extra_labels)，见 §3.5 映射表。

    run 级 status 是主判据；fail_class 仅在 run=failed 时决定归类与 label。
    红线 #7：env_blocked/interrupted 映射 skipped 但带 label，绝不洗绿、绝不进 failed。
    """
    status = run.get("status")
    fail_class = _run_fail_class(run)
    if status == "passed":
        return "passed", _details(""), {}
    if status == "env_blocked" or (status == "failed" and fail_class == "env_blocked"):
        message = run.get("reason") or run.get("message") or "environment blocked"
        return "skipped", _details(str(message)), {
            "atlas.fail_class": "env_blocked", "atlas.blocked": "true"}
    if status == "interrupted" or (status == "failed" and fail_class == "interrupted"):
        return "skipped", _details("operation interrupted"), {
            "atlas.fail_class": "interrupted", "atlas.interrupted": "true"}
    # C9「等人」终态（design_c9 §3 allure 行）：与上两行**逐字同构**的家族第二实现——
    # skipped + 独立 label + message 保留原文。红线 #7 的洗绿质疑在这里的答案是形态本身：
    # 报告上它既不在 failed 也不在 passed，而是带 `atlas.waiting=true` 的 skipped，
    # message 还留着「在等什么」的原文（run 顶层 reason，缺省回落固定文案）。
    # 逐类分支保留而不收敛成循环：每类 label 键不同（blocked/interrupted/waiting），
    # 循环会把键名藏进数据里——防漏由 test_default_buckets_cover_all_classes 同族的
    # 「遍历枚举必全有分支」测承担（评审 §2.1-3）。
    if status == "waiting" or (status == "failed" and fail_class == "waiting"):
        message = run.get("reason") or run.get("message") or "awaiting human intervention"
        return "skipped", _details(str(message)), {
            "atlas.fail_class": "waiting", "atlas.waiting": "true"}
    if status == "failed":
        if fail_class in (None, "unknown"):
            return "broken", _details(f"run failed: {fail_class or 'unknown'}"), {
                "atlas.fail_class": "unknown"}
        return "failed", _details(f"run failed: {fail_class}"), {"atlas.fail_class": fail_class}
    # 缺失/未知 run 状态：broken 不伪装（「感知器官坏了却读成无失败」的假阴性温床）
    return "broken", _details(f"unknown run status: {status!r}"), {"atlas.fail_class": "unknown"}


def _step_status(ok, fail_class, name: str) -> tuple[str, dict, dict]:
    """Atlas 步骤 → (allure_status, statusDetails, extra_labels)，§3.5 行 6：
    被环境跳过的步骤明确记 skipped+atlas.fail_class=env_blocked，绝不伪装成 PASSED。
    """
    if ok is True and not fail_class:
        return "passed", _details(""), {}
    if fail_class == "env_blocked":
        return "skipped", _details(f"step '{name}' skipped by environment"), {
            "atlas.fail_class": "env_blocked"}
    if fail_class == "interrupted":
        return "skipped", _details("operation interrupted"), {
            "atlas.fail_class": "interrupted", "atlas.interrupted": "true"}
    # C9 同族一支（design_c9 §3 allure_report 行）：等人步骤不是缺陷也不是中断——skipped +
    # 独立 label + 非空 message。message 留固定文案是**下限**：步骤记录里没有 reason 字段
    # （run 顶层才有），run 级映射那支会带上「在等什么」的原文，这里绝不回落空串——
    # 空 message 的 skipped 读起来就是「这步没发生」，正是红线 #7 的反面。
    if fail_class == "waiting":
        return "skipped", _details("awaiting human intervention"), {
            "atlas.fail_class": "waiting", "atlas.waiting": "true"}
    if fail_class == "unknown":
        return "broken", _details(f"step '{name}' failed: unknown"), {"atlas.fail_class": "unknown"}
    if fail_class:
        return "failed", _details(f"step '{name}' failed: {fail_class}"), {
            "atlas.fail_class": str(fail_class)}
    # ok=False 且无 fail_class：无法归因 → broken，不静默绿（与 run 级同纪律）
    return "broken", _details(f"step '{name}' failed without fail_class"), {
        "atlas.fail_class": "unknown"}


def _step_to_allure(step: dict, run_start_ms: int, offset_ms: float, out_dir: Path,
                    attachments_dir: Path | None, runs_path: Path) -> tuple[dict, float]:
    """单条 step record → Allure step dict；返回 (step, 新 offset)。

    步骤没有绝对时刻（recorder 只记 dur_ms），从 run 起点按累计时长推算——
    离线确定性输出，不依赖系统时钟。
    """
    name = str(step.get("step", ""))
    ok = step.get("ok")
    fail_class = step.get("fail_class")
    dur = step.get("dur_ms", 0)
    if not isinstance(dur, (int, float)) or isinstance(dur, bool):
        dur = 0
    dur = max(0.0, float(dur))
    start_ms = run_start_ms + int(offset_ms)
    stop_ms = start_ms + int(dur)
    status, details, labels = _step_status(ok, fail_class, name)
    step_attachments = []
    for ref in _iter_attachment_refs(step.get("attachment")):
        entry = _write_attachment_safe(out_dir, ref, attachments_dir, runs_path)
        if entry:
            step_attachments.append(entry)
    for ref in _iter_attachment_refs(step.get("attachments")):
        entry = _write_attachment_safe(out_dir, ref, attachments_dir, runs_path)
        if entry:
            step_attachments.append(entry)
    return {
        "name": name, "status": status, "stage": "finished",
        "start": start_ms, "stop": stop_ms,
        "steps": [], "attachments": step_attachments, "parameters": [],
        "statusDetails": details,
        "labels": [{"name": k, "value": v} for k, v in labels.items()],
    }, offset_ms + dur


def _iter_attachment_refs(raw):
    """附件引用形态：字符串（路径）或映射 {path, label, type}；坏形态跳过不崩。"""
    if raw is None:
        return
    if isinstance(raw, (str, Path)):
        yield {"path": str(raw)}
    elif isinstance(raw, dict):
        path = raw.get("path") or raw.get("source")
        if path:
            yield {"path": str(path),
                   "label": str(raw.get("label") or Path(str(path)).name),
                   "type": raw.get("type")}
    elif isinstance(raw, list):
        for item in raw:
            yield from _iter_attachment_refs(item)


def _write_attachment_safe(out_dir: Path, ref: dict, attachments_dir: Path | None,
                           runs_path: Path) -> dict | None:
    """解析附件引用并落盘；返回 Allure attachment 条目（占位降级见 write_attachment）。"""
    source = Path(ref["path"])
    if not source.is_absolute():
        base = attachments_dir if attachments_dir is not None else runs_path.parent
        source = Path(base) / source
    return write_attachment(out_dir, source, ref.get("label") or source.name, ref.get("type"))


def write_attachment(out_dir: Path, source_path: Path, label: str,
                     media_type: str | None = None) -> dict:
    """把截图/证据文件复制进结果集并返回 Allure attachment 条目。

    #126/#64 纪律：源文件读失败/坏字节不得顶掉整份报告——附件缺失时降级为一条
    text/plain 说明附件，绝不裸崩（报告可以有噪音，不能丢整份）。
    """
    source_path = Path(source_path)
    suffix = source_path.suffix.lower()
    if not suffix:
        suffix = {v: k for k, v in _SUFFIX_TYPES.items()}.get(
            media_type or "", ".txt")
    ext = suffix if suffix.startswith(".") else "." + suffix
    target = out_dir / f"{_ATTACHMENT_PREFIX}{uuid.uuid4().hex}-attachment{ext}"
    if media_type is None:
        media_type = _SUFFIX_TYPES.get(ext, "application/octet-stream")
    try:
        data = source_path.read_bytes()
        # 文本类附件校验 UTF-8：坏字节（非法编码）视为坏附件走占位，不把乱码当证据
        if media_type.startswith("text/") or media_type in ("application/json", "application/xml"):
            data.decode("utf-8")
        target.write_bytes(data)
    except (OSError, UnicodeDecodeError, ValueError):
        placeholder = out_dir / f"{_ATTACHMENT_PREFIX}{uuid.uuid4().hex}-attachment.txt"
        placeholder.write_text(
            f"[atlas] 附件 {label} 无法读取（源文件缺失或含坏字节）：{source_path}\n",
            encoding="utf-8")
        return {"name": label, "source": placeholder.name, "type": "text/plain"}
    return {"name": label, "source": target.name, "type": media_type}


def _version_parameters(run: dict, env: str) -> list[dict]:
    """Allure parameters：env 恒有；run 记录带 `version`（C6）时追加三键。

    坏形态（version 非映射、knowledge/flow 缺失或不是 dict）只添 env 一条，绝不崩整份
    结果集（#126/#131「报告可以有噪音不能丢整份」同纪律）。值里的 `#source`/`#kind`
    后缀是口径的一部分：`…#builtin` 与 `…#private` 说明读的是哪套知识、flow 摘要有无源文本。
    """
    params = [{"name": "env", "value": env}]
    version = run.get("version")
    if not isinstance(version, dict):
        return params
    if version.get("atlas"):
        params.append({"name": "atlas_version", "value": str(version["atlas"])})
    knowledge = version.get("knowledge")
    if isinstance(knowledge, dict) and (knowledge.get("sha1") or knowledge.get("source")):
        # unreadable 形态的 sha1 是空串，但 source 本身就是结论——不能因为「没指纹」就把
        # 这一项悄悄删掉（设计要求：unreadable 必须显式可见，不静默）。
        sha1 = str(knowledge.get("sha1") or "-")
        source = str(knowledge.get("source") or "")
        params.append({"name": "knowledge_sha1",
                       "value": f"{sha1}#{source}" if source else sha1})
    flow = version.get("flow")
    if isinstance(flow, dict) and (flow.get("md5") or flow.get("kind")):
        md5 = str(flow.get("md5") or "-")
        kind = str(flow.get("kind") or "")
        params.append({"name": "flow_md5", "value": f"{md5}#{kind}" if kind else md5})
    return params


def _result_labels(env: str, status: str, extra: dict, *, align: bool) -> list[dict]:
    """run 级 Allure labels。

    align=False → 逐字旧形态（suite=atlas + severity，#115 零涟漪逃生舱）。
    align=True  → 追加 UI AUTO TEST 使用的确定性 label（parentSuite/subSuite/package/
    framework/language），既有 suite/severity 值不变（仅新增，不改不删）。
    host/thread 不在此处伪造（机器相关，破坏离线确定性）——由 `executor=` 注入。
    """
    severity = "critical" if status in ("failed", "broken") else "normal"
    if not align:
        labels = [{"name": "suite", "value": _ALIGN_SUITE},
                  {"name": "severity", "value": severity}]
    else:
        labels = [
            {"name": "parentSuite", "value": "Atlas"},
            {"name": "suite", "value": _ALIGN_SUITE},
            {"name": "subSuite", "value": env or "default"},
            {"name": "package", "value": f"atlas.{env}" if env else "atlas"},
            {"name": "framework", "value": "atlas"},
            {"name": "language", "value": "python"},
            {"name": "severity", "value": severity},
        ]
    labels.extend({"name": k, "value": v} for k, v in extra.items())
    return labels


def _run_to_result(run: dict, out_dir: Path, attachments_dir: Path | None,
                   runs_path: Path, *, align: bool = True) -> dict:
    """单条 run → 一个 Allure result dict（§3.2）。

    historyId=sha1(env|case) 稳定（同 case 跨运行聚合趋势图）；uuid 每份独立。
    align=True（默认）追加 description/titlePath 两个 UI AUTO TEST 恒有的字段（附加，
    不改既有键）；align=False 退回逐字旧形态。
    """
    env = str(run.get("env", ""))
    case = str(run.get("case", "flow"))
    history_id = hashlib.sha1(f"{env}|{case}".encode("utf-8")).hexdigest()
    uid = uuid.uuid4().hex
    status, details, labels = _status_from_run(run)
    run_start = _parse_iso_ms(run.get("started_at")) or 0
    run_stop = _parse_iso_ms(run.get("finished_at"))
    if run_stop is None:
        run_stop = run_start
    steps_allure = []
    offset = 0.0
    steps = run.get("steps", [])
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue   # steps:[null] 等坏元素跳过，不顶掉整份结果（与 reporting 同口径）
            converted, offset = _step_to_allure(step, run_start, offset, out_dir,
                                               attachments_dir, runs_path)
            steps_allure.append(converted)
    result_attachments = []
    for ref in _iter_attachment_refs(run.get("attachments")):
        entry = _write_attachment_safe(out_dir, ref, attachments_dir, runs_path)
        if entry:
            result_attachments.append(entry)
    label_list = _result_labels(env, status, labels, align=align)
    result = {
        "uuid": uid,
        "historyId": history_id,
        "testCaseId": history_id,
        "name": case,
        "fullName": f"atlas.{env}.{case}",
        "status": status,
        "statusDetails": details,
        "stage": "finished",
        "start": run_start,
        "stop": run_stop,
        "labels": label_list,
        # C6 版本三元组进 parameters（仅当 run 记录带 version 键——旧结果集行为逐字不变）。
        # labels/historyId 不掺版本：Allure 的趋势聚合按 historyId 归组，把版本塞进 label
        # 会让「同一用例换知识后」断成两条趋势线，反而看不见漂移。值里的 #source/#kind 后缀
        # 是口径本身的一部分：`1a2b3c#builtin` 与 `#private` 说明读的是哪套知识，不是内容。
        "parameters": _version_parameters(run, env),
        "steps": steps_allure,
        "attachments": result_attachments,
        "links": [],
    }
    if align:
        # UI AUTO TEST 的 result.json 恒带 description/titlePath（allure-pytest 产出）。
        # 空描述诚实留空，不伪造业务说明；titlePath 供树/面包屑定位（顶层产品名 + env）。
        result["description"] = str(run.get("description") or "")
        result["titlePath"] = ["Atlas", env] if env else ["Atlas"]
    return result


def _write_result(out_dir: Path, result: dict) -> Path:
    path = out_dir / f"{_ATTACHMENT_PREFIX}{result['uuid']}-result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---- Allure 结果集可选组成部分（UI AUTO TEST 通过 allure CLI 场景配置，Atlas 直出补齐）----

def default_categories() -> list[dict]:
    """Atlas fail_class → Allure categories.json（分类 tab）。

    messageRegex 锚在 Atlas **自产**的 statusDetails.message（`run failed: <fail_class>`，
    见 `_status_from_run`），不靠宽松词猜；status 匹配走 matchedStatuses。未命中任何条的
    结果留在 Allure「无分类」桶，不伪造归属（不猜红线）。非缺陷终态（env_blocked/
    interrupted 映射 skipped）单列一桶，绝不与真实缺陷混在一起（红线 #7）。
    """
    return [
        {"name": "断言/契约缺陷", "matchedStatuses": ["failed"],
         "messageRegex": ".*run failed: (assertion|contract).*"},
        {"name": "数据缺陷", "matchedStatuses": ["failed"],
         "messageRegex": ".*run failed: data.*"},
        {"name": "交互/会话缺陷", "matchedStatuses": ["failed"],
         "messageRegex": ".*run failed: (interaction|session).*"},
        {"name": "执行缺陷（未归类）", "matchedStatuses": ["broken"]},
        {"name": "环境阻断/中断（非缺陷）", "matchedStatuses": ["skipped"]},
    ]


def write_categories(out_dir: Path, categories: list) -> Path:
    """写 categories.json（Allure 约定文件名，非 atlas-* 前缀故 clean 不动它）。"""
    out_dir = Path(out_dir)
    path = out_dir / "categories.json"
    path.write_text(json.dumps(categories, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_environment_properties(out_dir: Path, mapping: dict) -> Path:
    """写 environment.properties（Allure Environment 组件，key=value 行）。

    键名去 `=`/换行、值内换行折成空格——properties 是行格式，裸换行会把一条拆成两条把
    报告读成假内容；空值键跳过，不写 `k=` 半条。
    """
    out_dir = Path(out_dir)
    path = out_dir / "environment.properties"
    lines = []
    for key, value in mapping.items():
        if value is None:
            continue
        clean_key = str(key).replace("=", "_").replace("\n", " ").replace("\r", " ").strip()
        if not clean_key:
            continue
        clean_val = str(value).replace("\n", " ").replace("\r", " ")
        lines.append(f"{clean_key}={clean_val}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def write_executor(out_dir: Path, executor: dict) -> Path:
    """写 executor.json（Allure Executors 组件）。executor 无自然来源，纯 opt-in。"""
    out_dir = Path(out_dir)
    path = out_dir / "executor.json"
    path.write_text(json.dumps(executor, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def emit_allure(runs_path: Path, out_dir: Path, *, clean: bool = True,
                attachments_dir: Path | None = None, align: bool = True,
                metadata: bool = True, categories: list | None = None,
                environment: dict | None = None, executor: dict | None = None) -> dict:
    """把 runs.jsonl 转成 Allure 2.39.0 结果集目录（纯 stdlib）。

    - clean=True 时先清空 out_dir 下 Atlas 产的文件（仅删 atlas-* 前缀，不误删用户附件）。
    - 读侧复用 reporting 的容错纪律：#112/#131 —— UnicodeDecodeError → errors='replace'，
      残行/非对象行跳过并计数，绝不因一条坏行整份失败。
    - align=True（默认）：result.json 追加 UI AUTO TEST 恒有的 label（parentSuite/subSuite/
      package/framework/language）与 description/titlePath——**纯附加**，既有 label/键值不变；
      align=False 退回逐字旧 result.json（#115 零涟漪逃生舱）。
    - metadata=True（默认）：另写 categories.json（`default_categories`，可用 `categories=`
      覆盖；`categories=[]` 写空）与 environment.properties（`environment=None` 时从各 run 的
      env 去重派生，`environment={}` 跳过）；`executor=` 非空才写 executor.json（宿主标识无
      自然来源，不伪造）。三个文件均为 Allure 约定名，均**附加**——不写就不产出，旧目录内容
      除 atlas-* 外零改动。
    - 返回 {"results","steps","attachments","skipped_malformed_lines","out","metadata_files"}。
    """
    runs_path = Path(runs_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        _clean_atlas_files(out_dir)
    # #131：与 reporting/review/experience 同防线——腰斩多字节尾行先严格读保真，
    # 撞坏字节回落 errors="replace"：残行 JSON 解析必败自然落 skipped 计数，合法行照常产出。
    try:
        raw = runs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = runs_path.read_text(encoding="utf-8", errors="replace")
    results = steps = attachments = skipped = 0
    envs: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            run = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1   # 崩溃残行：计数上报而非静默
            continue
        if not isinstance(run, dict):
            # 合法 JSON 但非对象（裸串/数字/null）：与残行同等计入，不崩整份生成
            skipped += 1
            continue
        results += 1
        result = _run_to_result(run, out_dir, attachments_dir, runs_path, align=align)
        _write_result(out_dir, result)
        env = str(run.get("env", ""))
        if env:
            envs.add(env)
        steps += len(result["steps"])
        attachments += len(result["attachments"]) + sum(len(s["attachments"]) for s in result["steps"])
    metadata_files: list[str] = []
    if metadata:
        cats = default_categories() if categories is None else categories
        if cats is not None:
            write_categories(out_dir, cats)
            metadata_files.append("categories.json")
        env_map = environment
        if env_map is None:
            env_map = {"env": ",".join(sorted(envs))} if envs else {}
        if env_map:
            write_environment_properties(out_dir, env_map)
            metadata_files.append("environment.properties")
        if executor:
            write_executor(out_dir, executor)
            metadata_files.append("executor.json")
    return {"results": results, "steps": steps, "attachments": attachments,
            "skipped_malformed_lines": skipped, "out": str(out_dir),
            "metadata_files": metadata_files}

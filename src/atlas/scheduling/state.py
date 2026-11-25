"""调度台账与调度索引（design_c1 §5）——字段级 schema 的单一实现。

文件全在 `reports/schedule/`（`reports/` 已 gitignore：真实域名/账号名产物天然不入库，
红线 #11/PRIVACY 同族）：

- `tasks.json` 台账：`{"schema_version": 1, "tasks": {id: {...}}}`。
- `schedule_runs.jsonl` **调度域索引**：与业务 `runs.jsonl` 分离——调度「错过几轮」不污染
  业务缺陷分布（运行索引隔离语义）。
- `lease/<task-id>.lock` 租约（见 lease.py）。

写侧纪律：`tmp + os.replace`（X9，Windows 允许覆盖已存在目标，目标被打开时可
PermissionError → 重试 1 次仍败则整轮放弃并点名，不做半写）；JSONL 追加前检测尾字节非
换行先补断行（X5，`experience.py:64-77` 同款先例）。读侧沿用逐行容错纪律（#112/#131：
schedule_runs 也禁不起一条坏字节顶掉整表）。

ISO 序列化/解析只在本模块一处实现（#92 改一漏一）：水位线、槽位、`once.at` 全走
`iso()/parse_iso()`，dispatch 与 decision 都 import 这两个函数，不留第二份判据。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from ..atomic_io import append_text_locked, read_json_text, read_jsonl_text
from .constants import (DEFAULT_MAX_DURATION_MIN, MAX_CONSECUTIVE_FAILURES,
                        SCHEMA_VERSION)
from .cron_parse import load_tz, min_gap_ok, parse_expr

_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SCHEDULE_KINDS = ("cron", "once", "manual")
# 改配置即重置水位线的字段集（§3 表 8：cron/tz/flow/run_args 任一；「人改配置的瞬间不回放历史」）
_RESET_KEYS = ("flow", "run_args", "schedule")


# ── 路径与序列化 ──────────────────────────────────────────────────────

def schedule_dir(root: Path) -> Path:
    return Path(root) / "reports" / "schedule"


def tasks_path(root: Path) -> Path:
    return schedule_dir(root) / "tasks.json"


def runs_path(root: Path) -> Path:
    return schedule_dir(root) / "schedule_runs.jsonl"


def lease_dir(root: Path) -> Path:
    return schedule_dir(root) / "lease"


def lease_path(root: Path, task_id: str) -> Path:
    return lease_dir(root) / f"{task_id}.lock"


def iso(moment: "datetime | None") -> "str | None":
    """台账里的时刻一律 tz-aware ISO 字符串；None 如实存 null（缺什么写什么，不塞假值）。"""
    return moment.isoformat() if isinstance(moment, datetime) else None


def parse_iso(text: object, label: str) -> "datetime | None":
    """ISO 字符串 → aware datetime；None/空 → None；形态坏 → ValueError 点名键位。"""
    if text is None or text == "":
        return None
    if not isinstance(text, str):
        raise ValueError(f"{label} 必须是 ISO 字符串或 null，收到 {type(text).__name__}")
    normalized = text.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} 不是合法 ISO 时间：{text!r}") from exc
    return moment


def resolve_flow(root: Path, stored: str) -> Path:
    """台账里的 flow 串 → 绝对路径：相对串按项目根解析（X1 存 POSIX 字符串）。"""
    path = Path(stored)
    return path if path.is_absolute() else Path(root) / path


def flow_for_ledger(root: Path, flow: "str | Path") -> str:
    """flow 路径 → 台账存储串：根内存 root 相对 POSIX，根外存绝对 POSIX（X1：反斜杠不进 JSON）。"""
    path = Path(flow)
    if not path.is_absolute():
        path = (Path(root) / path).resolve()
    root = Path(root).resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


# ── 校验与入库形态 ────────────────────────────────────────────────────

def current_knowledge_source() -> str:
    """无人值守这一轮会读到哪套知识：private / builtin / unreadable。

    **判据复用 `versioning`（知识取源单一真相），这里不新建任何可用性判定**——只是把
    `private_knowledge_root()` 的现值喂给 `knowledge_fingerprint()` 读 `source`。
    为什么调度域要看这个：crontab / schtasks 的父 env 通常**不含**交互式 shell 里 export 的
    `ATLAS_KNOWLEDGE_DIR`（cron 只给 HOME/PATH），读侧「逐文件回退包内默认」是既有的
    「读不残缺」语义（不是缺陷），于是无人值守轮次可能**静默**拿包内虚构坑点/契约/会话默认
    跑真实 flow。engine/dsl 都不报错（回退本就合法），所以必须在**入库时就让它可见**
    （add/edit 当场 WARN + 记进台账），而不是等事后从 `versions.drifted` 才发现。
    """
    from .. import versioning   # 惰性：与 versioning 的 plugin_api 惰性 import 同族，避环

    return versioning.knowledge_fingerprint(versioning.private_knowledge_root())["source"]


def guard_run_args(run_args: list) -> None:
    """调度器永不带 `--approve`（红线 #13/#2，§6.3）。

    C1-C 评审必改：这里必须是**显式 raise**，不能是 `assert`——`python -O` 会剥掉 assert，
    安全闸不许依赖它。`--mode` 同闸：debug/heal 档带 CDP 冻场/自愈语义，不是无人值守该有的形态。
    """
    for item in run_args:
        text = str(item)
        if text == "--approve" or text.startswith("--approve="):
            raise ValueError("调度派发永不允许 --approve（红线 #13：写档 flow 必须人显式批准；"
                             "atlas schedule 只会拼 `atlas run <flow>`）")
        if text == "--mode" or text.startswith("--mode="):
            raise ValueError("调度派发不带 --mode（debug/heal 是人工现场语义，不属于无人值守）")


def validate_schedule(sched: object) -> dict:
    """schedule 字段校验并归一：kind ∈ cron/once/manual；cron 段语法/时区/最小间隔当场点名。"""
    if not isinstance(sched, dict):
        raise ValueError(f"schedule 必须是映射，收到 {type(sched).__name__}")
    kind = sched.get("kind")
    if kind not in _SCHEDULE_KINDS:
        raise ValueError(f"schedule.kind 只支持 {'/'.join(_SCHEDULE_KINDS)}，收到 {kind!r}")
    out = {"kind": kind}
    tz_name = sched.get("tz")
    if kind == "cron":
        expr = sched.get("expr")
        spec = parse_expr(expr if isinstance(expr, str) else "")
        if not isinstance(tz_name, str) or not tz_name.strip():
            raise ValueError("cron 任务必须带 schedule.tz（时区缺失不猜本机墙钟——"
                             "窗口与 catch-up 全按时区判）")
        tz = load_tz(tz_name)
        if not min_gap_ok(spec, tz, datetime.now(timezone.utc)):
            raise ValueError(f"cron 最小触发间隔不足 5 分钟（{expr!r} 的相邻间隙 <5min）——"
                             "无人值守的亚分钟周期会烧配额，防手滑闸拒绝")
        out["expr"] = spec.expr
        out["tz"] = tz_name.strip()
    elif kind == "once":
        at = parse_iso(sched.get("at"), "schedule.at")
        if at is None:
            raise ValueError("once 任务必须有 schedule.at（ISO 时间）")
        if at.tzinfo is None:
            if not isinstance(tz_name, str) or not tz_name.strip():
                raise ValueError("once 的 at 不带偏移时必须给 schedule.tz（不猜本地墙钟）")
            at = at.replace(tzinfo=load_tz(tz_name))
        out["at"] = iso(at)
        if isinstance(tz_name, str) and tz_name.strip():
            load_tz(tz_name)   # 存下来之前先验证可加载（X6 文案在此生效）
            out["tz"] = tz_name.strip()
    return out


def validate_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not _ID_RE.match(task_id):
        raise ValueError(f"任务 id 只允许字母/数字开头，后接字母数字 . _ -（≤64 字符），收到 {task_id!r}")
    return task_id


def validate_run_args(run_args: object) -> list:
    if run_args is None:
        return []
    if not isinstance(run_args, list) or not all(isinstance(item, str) for item in run_args):
        raise ValueError(f"run_args 必须是字符串列表（argv 透传，X2 不进 shell），收到 {run_args!r}")
    guard_run_args(run_args)
    return list(run_args)


def validate_max_duration(value: object) -> int:
    if value is None:
        return DEFAULT_MAX_DURATION_MIN
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"max_duration_min 必须是正整数分钟，收到 {value!r}")
    return value


def build_task(*, task_id: str, flow: str, schedule: dict, root: Path,
               run_args: list | None = None, max_duration_min: int | None = None,
               writes_acknowledged: bool = False,
               now: "datetime | None" = None) -> dict:
    """新建台账条目：所有入库判据集中在这一个函数（add 与 edit 复用，不留第二份，#92）。"""
    now = now or datetime.now(timezone.utc)
    task = {
        "id": validate_id(task_id),
        "flow": flow_for_ledger(root, flow),
        "run_args": validate_run_args(run_args),
        "schedule": validate_schedule(schedule),
        "max_duration_min": validate_max_duration(max_duration_min),
        "enabled": True,
        "writes_acknowledged": bool(writes_acknowledged),
        "created_at": iso(now),
        "updated_at": iso(now),
        "last_scheduled_for": None,
        "consecutive_failures": 0,
        "auto_paused_reason": None,
        # 入库那一刻私有知识库是否在场（观测数据，非判定）：builtin=无人值守将跑包内虚构知识
        "knowledge_source": current_knowledge_source(),
    }
    return task


def admission_problems(task: dict, *, root: Path, registry) -> dict:
    """§6.2 只读护栏（产出侧强制确认，M14）：离线 validate_flow + 元数据扫 read_only=False。

    返回 `{"flow_ok": bool, "write_steps": [Task 名]}`；flow 结构性问题由 validate_flow
    抛 ValueError（CLI 接成 rc 2），写步骤不抛——由调用方按 `--i-accept-writes` 决定拒入。
    """
    from ..dsl import FlowCompiler, TaskRegistry   # 惰性：调度包不在 dsl 的 import 闭包里

    path = resolve_flow(root, task["flow"])
    if not path.is_file():
        raise ValueError(f"flow 文件不存在：{path}")
    flow = FlowCompiler(TaskRegistry()).load(path)
    tasks = FlowCompiler(registry).validate_flow(flow)
    writes = [t.name for t in tasks if not t.metadata.read_only]
    return {"flow_ok": True, "write_steps": writes}


# ── 台账读写 ─────────────────────────────────────────────────────────

def load_doc(path: Path) -> dict:
    """台账读侧：缺文件=空表；JSON 坏/schema 版本不符点名文件（不是 traceback）。"""
    path = Path(path)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "tasks": {}}
    try:
        # BOM 安全（单点口径 `utf-8-sig`）：台账是人手可读可改的文件，带 BOM 时裸 utf-8
        # 会让 `json.loads` 报错，而下面的分支会把**合法台账**说成「JSON 无法解析」。
        raw = read_json_text(path)
    except UnicodeDecodeError as exc:
        raise ValueError(f"台账 {path} 不是 UTF-8（中断写坏？删除后重新 schedule add）：{exc}") from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"台账 {path} JSON 无法解析：{exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"台账 {path} 必须是映射，收到 {type(doc).__name__}")
    version = doc.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"台账 {path} schema_version={version!r}，本版本支持 "
                         f"{SCHEMA_VERSION}——不猜旧形态，请人工迁移")
    tasks = doc.get("tasks")
    if not isinstance(tasks, dict):
        raise ValueError(f"台账 {path} 的 tasks 必须是映射，收到 {type(tasks).__name__}")
    return doc


def _atomic_write_json(path: Path, doc: dict) -> None:
    """X9：tmp+os.replace；Windows 目标被打开时 PermissionError 重试 1 次（100ms）仍败→点名放弃，
    绝不留半写（台账是半份=一半任务按旧配置跑）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(doc, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in (0, 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def save_doc(path: Path, doc: dict) -> None:
    _atomic_write_json(path, doc)


def get_task(doc: dict, task_id: str, *, source: Path) -> dict:
    task = doc.get("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        raise ValueError(f"调度任务 {task_id!r} 不存在于 {source}；可用：atlas schedule list")
    return task


def claim_slot(path: Path, task_id: str, expected: "str | None", new_value: str) -> bool:
    """水位线 CAS（§6.6）。

    **C1-F 评审条款：CAS 只是辅助防线，互斥权威是租约**——这里的「重读→校验→写回」两步之间
    并非原子（同步盘/NFS 上 O_EXCL 语义也不可靠）。不要把 CAS 当唯一闸：它防的是同机双进程
    在租约缝隙里的重复认领，抢到返回 True，被抢返 False 由调用方幂等退出 0。
    """
    doc = load_doc(path)
    task = get_task(doc, task_id, source=Path(path))
    if task.get("last_scheduled_for") != expected:
        return False
    task["last_scheduled_for"] = new_value
    task["updated_at"] = iso(datetime.now(timezone.utc))
    save_doc(path, doc)
    return True


def apply_patch(task: dict, patch: dict, *, root: Path,
                now: "datetime | None" = None) -> dict:
    """§3 表 8：改配置（cron/tz/flow/run_args 任一）或重新 enable → 重置水位线/连败/原因。

    拍板口径：「人改配置的瞬间不回放历史」——改完配置就 catch-up 等于把旧配置的欠账算在新
    配置头上。once 任务改 schedule 自动回 enabled（暂停于跑完的，不是故障的）。
    """
    now = now or datetime.now(timezone.utc)
    merged = dict(task)
    normalized = dict(patch)
    # 先归一再比对：改没改配置要按**存储形态**判（同一 flow 传 Path 与传台账串不是「改了」；
    # schedule 少写一个归一字段也不算改了）——否则每次 edit 都重置水位线，「不回放历史」反了。
    if "schedule" in normalized:
        normalized["schedule"] = validate_schedule(normalized["schedule"])
    if "run_args" in normalized:
        normalized["run_args"] = validate_run_args(normalized["run_args"])
    if "flow" in normalized:
        normalized["flow"] = flow_for_ledger(root, normalized["flow"])
    if "max_duration_min" in normalized:
        normalized["max_duration_min"] = validate_max_duration(normalized["max_duration_min"])
    for key in ("flow", "run_args", "schedule", "max_duration_min",
                "writes_acknowledged", "enabled"):
        if key in normalized:
            merged[key] = normalized[key]
    re_enabled = bool(normalized.get("enabled")) and not task.get("enabled")
    config_changed = any(
        key in normalized and normalized[key] != task.get(key) for key in _RESET_KEYS)
    if merged.get("schedule", {}).get("kind") == "once" and "schedule" in normalized:
        # once 跑完自动 enabled=false（§5 claimSlot 语义）；改 schedule=换新的一次任务，自动回 active
        merged["enabled"] = True
    if config_changed or re_enabled:
        merged["last_scheduled_for"] = iso(now)
        merged["consecutive_failures"] = 0
        merged["auto_paused_reason"] = None
    # 每次人工编辑都刷新「此刻私有知识库是否在场」的观测（编辑=人在场的时刻，比 add 更该准）
    merged["knowledge_source"] = current_knowledge_source()
    merged["updated_at"] = iso(now)
    return merged


def apply_failure_policy(path: Path, task_id: str, mode: str, *,
                         reason: str = "", now: "datetime | None" = None) -> dict:
    """§3 表 9/10 的台账侧落笔。mode 由 `decision.failure_policy(outcome)` 给出（判据只在那一处）：

    - `count`：timeout / 业务 rc≠0 → 连败 +1，到 5 置 `enabled=false` + `auto_paused_reason`
      （最后一条失败摘要）——「环境挂一整晚」停在一个有原因的任务上，而不是每小时刷失败烧请求。
    - `reset`：成功 → 连败归零（通过打断连败）。
    - `keep`：interrupted / defer / dropped → 不动计数。人 Ctrl-C 与「本轮没跑到」既不是失败
      也不是成功，重置会把故障前的真实连败抹掉，累加又违反 CONTROL_FLOW（#114 第二实现）。
    """
    doc = load_doc(path)
    task = get_task(doc, task_id, source=Path(path))
    if mode == "count":
        failures = int(task.get("consecutive_failures") or 0) + 1
        task["consecutive_failures"] = failures
        if failures >= MAX_CONSECUTIVE_FAILURES:
            task["enabled"] = False
            task["auto_paused_reason"] = (reason or "连续失败")[:200]
    elif mode == "reset":
        task["consecutive_failures"] = 0
    elif mode != "keep":
        raise ValueError(f"未知连败策略 {mode!r}（只支持 count/reset/keep）")
    task["updated_at"] = iso(now or datetime.now(timezone.utc))
    save_doc(path, doc)
    return task


def claim_once_done(path: Path, task_id: str, *, now: "datetime | None" = None) -> None:
    """once 任务被认领即 enabled=false（§5 scheduler claimSlot 语义 :205）——跑完不再重复触发。"""
    doc = load_doc(path)
    task = get_task(doc, task_id, source=Path(path))
    task["enabled"] = False
    task["updated_at"] = iso(now or datetime.now(timezone.utc))
    save_doc(path, doc)


# ── 调度索引（schedule_runs.jsonl）───────────────────────────────────

def new_run_key() -> str:
    return secrets.token_hex(6)


def append_run(path: Path, line: dict) -> None:
    """派发完成才追加，单行全事实。X5：尾字节非换行先补断行（残行会把新记录黏成一条坏行，
    读侧「跳过残行」跳掉的正是这条完整记录——#112 同族静默丢账）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_break = path.exists() and path.stat().st_size > 0
    if needs_break:
        with path.open("rb") as stream:
            stream.seek(-1, os.SEEK_END)
            needs_break = stream.read(1) not in (b"\n", b"\r")
    # wave4 cc-1：索引追加裸 open("a") 并发丢行（本机实测 17%）——统一走原子追加器
    # （换行 os.linesep 保旧字节形态；leading_break 承接 X5 尾行补断行守卫）。
    append_text_locked(path, json.dumps(line, ensure_ascii=False, sort_keys=True),
                       leading_break=needs_break)


def read_runs(path: Path) -> list:
    """读侧逐行容错（#112/#131 同款）：坏字节 replace 回落、残行/非映射跳过，绝不一条坏行顶掉整表。"""
    path = Path(path)
    if not path.exists():
        return []
    # cc-1 读侧配套：索引由 append_text_locked 加锁写，Windows 并发读撞锁报
    # PermissionError（瞬态）——read_jsonl_text 内部重试即过；BOM 口径同单点
    # （带 BOM 的首条索引行过去会被 json.loads 拒成残行而静默丢失）。
    raw = read_jsonl_text(path)
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def skipped_totals(path: Path) -> dict:
    """「错过几轮」= sum(skipped_slots) 按任务分组（§7 G2 交付答案）。"""
    totals: dict = {}
    for row in read_runs(path):
        task_id = row.get("task_id")
        value = row.get("skipped_slots")
        if not isinstance(task_id, str):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue   # 形态坏如实跳过，不冒充 0 也不顶掉其余任务
        totals[task_id] = totals.get(task_id, 0) + int(value)
    return totals

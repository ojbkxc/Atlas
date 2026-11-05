"""探索会话：Atlas 开带 CDP 端口的已登录会话，外部大脑经 playwright-cli attach 操作。

分工即架构：脑在外面（任何更强的都行），Atlas 管三件事——
① 环境（非生产 + 打印 attach 命令）；② 留痕（每次回车一个证据 checkpoint）；
③ 沉淀（trace 编译成 pending_review 草稿，进 review 塔，永不直接成资产）。
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

# 可操作元素才值得成为选择器候选：其余（generic/text/grid）噪声大
_ACTION_ROLES = {"button", "textbox", "combobox", "checkbox", "radio", "switch", "searchbox"}
_MAX_CANDIDATES_PER_CHECKPOINT = 20


def start(config, *, account: str | None = None, url: str | None = None,
          trace: Path | None = None, console=input) -> Path:
    """登录 → 冻住会话 → 用户回车=抓一个 checkpoint；stop/EOF 结束。返回 trace 路径。"""
    if config.is_production:
        raise PermissionError("探索会话禁止连接生产环境：探索者会真实点击，越界代价不可回收")
    from .cli import ensure_utf8_streams  # 懒加载避免循环：外部大脑直调 start 也要 UTF-8 兜底
    ensure_utf8_streams()
    import os
    os.environ.setdefault("CDP_DEBUG", "1")   # session.py 据此开 CDP 端口
    port = os.environ.get("CDP_PORT", "9222")
    from .runtime import DemoRuntime
    trace = Path(trace or (config.reports_dir / f"explore_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"))
    trace.parent.mkdir(parents=True, exist_ok=True)
    shots = config.artifacts_dir / "exploration" / trace.stem
    shots.mkdir(parents=True, exist_ok=True)
    runtime = DemoRuntime(config, login=True, account=account).__enter__()
    try:
        page = runtime.actor.page
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print(f"探索会话已就绪（account={runtime.account}, url={page.url}）\n"
              f"另开终端接管：playwright-cli attach --cdp=http://localhost:{port}\n"
              f"接管操作后回到这里：回车=记录一个证据 checkpoint；输入 stop=结束会话", flush=True)
        index = 0
        while True:
            answer = console("checkpoint> ")
            if answer.strip().lower() in {"stop", "quit", "exit"}:
                break
            index += 1
            snap_name = f"{index:03d}.aria.yaml"
            snapshot = ""
            try:
                snapshot = page.aria_snapshot()
            except Exception:
                snapshot = ""  # 老版本浏览器/页面异常：URL checkpoint 仍是有效证据
            (shots / snap_name).write_text(snapshot, encoding="utf-8")
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "checkpoint": index,
                   "url": page.url, "title": page.title(),
                   "snapshot_file": str(shots / snap_name),
                   "snapshot_lines": len(snapshot.splitlines()) if snapshot else 0}
            with trace.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            # 纯 ASCII 标记：GBK 控制台写 "✓" 会 UnicodeEncodeError 崩掉实会话
            #（真机冒烟抓到；中文日志 GBK 可编码，不受影响）
            print(f"  OK checkpoint #{index} -> {shots / snap_name}", flush=True)
    finally:
        runtime.__exit__(None, None, None)
    return trace


_SNAP_LINE = re.compile(r"^\s*-\s*(\w+)(?:\s+\"([^\"]*)\")?")


def extract_candidates(snapshot_text: str) -> list[dict]:
    """从 aria snapshot 提取可操作元素候选（role+name），去重限量。"""
    found, seen = [], set()
    for line in snapshot_text.splitlines():
        m = _SNAP_LINE.match(line)
        if not m:
            continue
        role, name = m.group(1), (m.group(2) or "").strip()
        if role not in _ACTION_ROLES or not name:
            continue
        # 分页箭头等纯符号/单字符名无审核价值（真塔实测：>/</空 混入候选）
        if len(name) < 2 or all(not ch.isalnum() and not '一' <= ch <= '鿿' for ch in name):
            continue
        key = (role, name)
        if key in seen:
            continue
        seen.add(key)
        found.append({"role": role, "name": name})
        if len(found) >= _MAX_CANDIDATES_PER_CHECKPOINT:
            break
    return found


def _read_snapshot(snap_file: Path, summary: dict) -> str:
    """快照尽力读：坏字节/IO 错误点名计数（unreadable_snapshots），不顶掉整份编译，
    也绝不静默吞成空文本冒充「页面没有可操作元素」。"""
    try:
        try:
            return snap_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 中断腰斩的多字节字符：替换字符读入，能解析出的候选仍是有效原矿
            return snap_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        summary["unreadable_snapshots"] = summary.get("unreadable_snapshots", 0) + 1
        return ""


def compile_trace(trace_path: Path, journal) -> dict:
    """trace → pending_review 草稿：步骤序列进 task_candidate，元素进 selector_candidate。

    产物只是原矿：全部 status=pending_review，错了无害；进知识/组件库必须走 atlas review。
    """
    rows = []
    summary = {"checkpoints": 0, "steps": 0, "selector_candidates": 0, "malformed_lines": 0}
    # #109：交互会话 Ctrl-C 可把最后一行多字节字符腰斩在字节上（非法 UTF-8 尾序列）——
    # 旧实现 read_text 裸 UnicodeDecodeError 顶掉整份编译，#68 的逐行容错一行没轮到。
    # 先按严格 UTF-8 读保真；撞坏字节回落 errors="replace"：残尾行替换字符下必然 JSON 失败，
    # 自然落进 malformed_lines 计数，前面的合法 checkpoint 照常编译。
    path = Path(trace_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            summary["malformed_lines"] += 1   # 探索是交互会话，无 fsync 可能留下崩溃/中断残行：跳过并计数
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            summary["malformed_lines"] += 1   # 合法 JSON 但非对象（裸串/数字/null）同样计入，不整体崩
    summary["checkpoints"] = len(rows)
    existing_selector_keys = set()
    for row in rows:
        step = {"kind": "trace_step", "url": row.get("url", ""),
                "checkpoint": row.get("checkpoint"), "evidence": {
                    "url": row.get("url", ""), "snapshot_file": row.get("snapshot_file", "")}}
        journal.append_draft(_candidate_draft(journal, f"explore:step:{row.get('url', '')}", step))
        summary["steps"] += 1
        snap_file = Path(row.get("snapshot_file", "")) if row.get("snapshot_file") else None
        snapshot_text = ""
        if snap_file and snap_file.is_file():
            # #109：checkpoint 写快照同样可被中断腰斩。旧实现裸 UnicodeDecodeError/OSError
            # 顶掉整份编译；即便加了 try，静默跳过也会让候选凭空消失冒充「页面没有可操作元素」。
            snapshot_text = _read_snapshot(snap_file, summary)
        if snapshot_text:
            for cand in extract_candidates(snapshot_text):
                key = (cand["role"], cand["name"])
                if key in existing_selector_keys:
                    continue
                existing_selector_keys.add(key)
                journal.append_draft(_candidate_draft(
                    journal, f"explore:selector:{key[0]}:{key[1]}",
                    {"kind": "selector_candidate", "role": key[0], "name": key[1],
                     "evidence": {"url": row.get("url", ""), "snapshot_file": str(snap_file)}}))
                summary["selector_candidates"] += 1
    return summary


def _candidate_draft(journal, identity: str, fields: dict) -> dict:
    """借 journal 的落盘通道，但不套 pitfall 字段——kind 不同、id 由内容决定。"""
    import hashlib
    row = {"id": hashlib.sha1(identity.encode()).hexdigest()[:12],
           "task": fields.get("url") or f"{fields.get('role')}:{fields.get('name')}",
           "exception_type": "", "failure_class": "exploration",
           "pitfall_id": "", "status": "pending_review",
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    row.update(fields)
    return row

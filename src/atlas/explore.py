"""探索会话：Atlas 开带 CDP 端口的已登录会话，外部大脑经 playwright-cli attach 操作。

分工即架构：脑在外面（任何更强的都行），Atlas 管三件事——
① 环境（非生产 + 打印 attach 命令）；② 留痕（每次回车一个证据 checkpoint）；
③ 沉淀（trace 编译成 pending_review 草稿，进 review 塔，永不直接成资产）。

cmd 文件协议（无头/后台会话通道，2026-11-05 MACH 全链路实测驱动）
----------------------------------------------------------------
Windows 后台进程的 stdin 管道不可用：console=input 立刻 EOF/lost sys.stdin，
交互回车通道失效 → 0 checkpoint、trace 为空、--compile 没输入。cmd 文件用
「外部驱动写文件、会话轮询消费」替代 stdin。

开启方式（二选一，显式参数优先；cli.py 零改动）：
    start(..., cmd_file=Path("explore.cmd"))
    环境变量 ATLAS_EXPLORE_CMD_FILE=<路径>

命令格式（UTF-8 文本，按行解释）：
    - 空行（strip 后为空的行）＝回车＝请求落 1 个 checkpoint；N 个空行＝N 个请求
    - 任一行 strip 后为 stop/quit/exit（大小写不敏感）＝结束会话
      （优先于同文件的 checkpoint 标记：结束比多落一个证据更要紧）
    - 其他非空行：stderr 点名（ASCII WARN: 前缀）后随本轮一起清空，
      不冒充命令执行，也不无限残留反复重读

消费语义：
    - 会话启动时清空 cmd 文件——上次会话残留的 stop 不许串场杀死新会话
    - 每轮循环先于 console 轮询一次；处理完本轮命令即把文件清空（写回 ""），
      同一条命令绝不二次执行
    - 文件不存在/读取失败＝本轮无命令，继续等（外部驱动可能还没建文件）
    - console 与 cmd 文件两通道可共存：cmd 文件里已到达的命令先处理
      （文件内容＝先到者）；console 阻塞等待期间写入的命令，等 console
      返回后的下一轮才被消费

仅 cmd 文件模式下的一处偏离（无头必需；不传 cmd_file 时行为与旧版逐字一致）：
    console 读取抛异常（无头 stdin 立刻 EOF / RuntimeError: lost sys.stdin）
    不再顶掉会话，视为「console 暂无输入」，睡 _CMD_POLL_INTERVAL 后继续轮询
    cmd 文件；KeyboardInterrupt 等 BaseException 不进 except Exception，
    照常传播（控制流异常纪律不变）。
"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

# 可操作元素才值得成为选择器候选：其余（generic/text/grid）噪声大
_ACTION_ROLES = {"button", "textbox", "combobox", "checkbox", "radio", "switch", "searchbox"}
_MAX_CANDIDATES_PER_CHECKPOINT = 20

# cmd 文件通道（协议见模块 docstring）
_CMD_STOP_WORDS = {"stop", "quit", "exit"}
_CMD_POLL_INTERVAL = 0.5   # 无头轮询间隔（秒）：外部驱动写命令到被消费的延迟上界


def _consume_cmd_file(cmd_path: Path) -> tuple[int, bool]:
    """读一轮 cmd 文件并消费：返回 (checkpoint 请求数, 是否要求结束会话)。

    文件不存在/读失败＝本轮无命令（命令还没到达，继续等，不是吞证据）；
    消费后整文件清空（写回 ""），同一条命令绝不二次执行。未知命令 stderr
    点名后随本轮清空，不冒充命令执行。
    """
    try:
        raw = cmd_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, False
    if raw == "":
        return 0, False   # 空文件＝无命令，不值得每轮重写
    lines = raw.splitlines()
    if any(ln.strip().lower() in _CMD_STOP_WORDS for ln in lines):
        cmd_path.write_text("", encoding="utf-8")
        return 0, True
    markers = sum(1 for ln in lines if not ln.strip())
    unknown = [ln.strip() for ln in lines
               if ln.strip() and ln.strip().lower() not in _CMD_STOP_WORDS]
    cmd_path.write_text("", encoding="utf-8")
    if unknown:
        # 显示段强制 ASCII（backslashreplace）：cmd 文件可能出现任意字节，
        # GBK 控制台直接 print 非 GBK 字符会把实会话崩掉（✓ 标记同款教训）
        shown = " | ".join(
            ln.encode("ascii", "backslashreplace").decode("ascii") for ln in unknown[:3])
        print(f"WARN: cmd 文件未知命令已忽略并清空: {shown}", file=sys.stderr, flush=True)
    return markers, False


def _record_checkpoint(page, trace: Path, shots: Path, index: int) -> None:
    """落一个证据 checkpoint：aria snapshot + URL + 标题追加进 trace jsonl。

    行格式是 explore --compile 的解析契约，字段与顺序不得擅改。
    """
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


PRODUCTION_REFUSED = "探索会话禁止连接生产环境：探索者会真实点击，越界代价不可回收"


def start(config, *, account: str | None = None, url: str | None = None,
          trace: Path | None = None, console=input,
          cmd_file: Path | None = None) -> Path:
    """登录 → 冻住会话 → 回车/cmd 文件空行=抓一个 checkpoint；stop/quit/exit=结束。

    返回 trace 路径。无头/后台会话传 cmd_file（或设 ATLAS_EXPLORE_CMD_FILE）开启
    文件通道，协议见模块 docstring；不传 cmd_file 时与交互版行为逐字一致
    （console 的 EOF 等异常照旧传播）。
    """
    if config.is_production:
        raise PermissionError(PRODUCTION_REFUSED)
    from .cli import ensure_utf8_streams  # 懒加载避免循环：外部大脑直调 start 也要 UTF-8 兜底
    ensure_utf8_streams()
    import os
    os.environ.setdefault("CDP_DEBUG", "1")   # session.py 据此开 CDP 端口
    port = os.environ.get("CDP_PORT", "9222")
    if cmd_file is None:   # 显式参数优先；缺省回落环境变量（cli.py 零改动接入无头通道）
        env_cmd = os.environ.get("ATLAS_EXPLORE_CMD_FILE", "").strip()
        cmd_file = Path(env_cmd) if env_cmd else None
    cmd_path = Path(cmd_file) if cmd_file else None
    from .runtime import DemoRuntime
    trace = Path(trace or (config.reports_dir / f"explore_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"))
    trace.parent.mkdir(parents=True, exist_ok=True)
    shots = config.artifacts_dir / "exploration" / trace.stem
    shots.mkdir(parents=True, exist_ok=True)
    if cmd_path is not None:
        cmd_path.parent.mkdir(parents=True, exist_ok=True)
        cmd_path.write_text("", encoding="utf-8")  # 启动清空：上次会话残留 stop 不许串场杀死新会话
    runtime = DemoRuntime(config, login=True, account=account).__enter__()
    try:
        page = runtime.actor.page
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print(f"探索会话已就绪（account={runtime.account}, url={page.url}）\n"
              f"另开终端接管：playwright-cli attach --cdp=http://localhost:{port}\n"
              f"接管操作后回到这里：回车=记录一个证据 checkpoint；输入 stop=结束会话", flush=True)
        if cmd_path is not None:
            print(f"无头通道：cmd 文件 {cmd_path}（空行=checkpoint；stop/quit/exit=结束）", flush=True)
        index = 0
        while True:
            if cmd_path is not None:
                markers, stop = _consume_cmd_file(cmd_path)
                if stop:
                    break
                for _ in range(markers):
                    index += 1
                    _record_checkpoint(page, trace, shots, index)
                if markers:
                    continue  # cmd 通道已到达的命令先处理完（先到者），下轮再轮询/等 console
            try:
                answer = console("checkpoint> ")
            except Exception:
                if cmd_path is None:
                    raise  # 交互模式行为零变化：EOF 等异常照旧传播
                # 无头 stdin 不可用（EOF / RuntimeError: lost sys.stdin）不是会话终点：
                # console 暂无输入，睡一个轮询间隔继续等 cmd 文件命令。
                # 只捕 Exception：Ctrl-C 等 BaseException 不进这里，照常传播（控制流异常纪律）。
                time.sleep(_CMD_POLL_INTERVAL)
                continue
            if answer.strip().lower() in {"stop", "quit", "exit"}:
                break
            index += 1
            _record_checkpoint(page, trace, shots, index)
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

"""一次性 tick 编排（design_c1 §6）：体检 → 租约 → 决策 → 认领 → 派发 → 记录。

形态钉死：本模块**不是守护进程**——`atlas schedule run <id>` 被 OS cron / Windows 任务
计划器调起，跑一轮就退出。常驻 tick 不移植（pi 是 30s tick 常驻，Atlas 的 tick 就是 cron
本身；占着锁的上一任才是那一轮的合法执行者，所以「租约占用」在这里记 defer 而**不推进
水位线**——下一轮 OS tick 还会再来，与 pi overlap 时 recordSkipped 推进水位是有意差异）。

派发只允许 `atlas run <flow>` 语义（红线 #2/#13 的双层护栏之一）：argv 固定
`[sys.executable, "-m", "atlas", "run", flow, *run_args]`，装配处对 `--approve`/`--mode`
**显式 raise**（C1-C：`python -O` 剥 assert，安全闸不许依赖 assert）；`ATLAS_FAST_CDP`
双闸同形态（argv 夹带点名拒绝 + 环境继承面剔除，见 guard_fast_cdp/child_env）；
生产写另有 engine 既有 `validate_write` 兜底（第二层，engine 零改动）。
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import lease, state
from .constants import DEFAULT_MAX_DURATION_MIN, HEARTBEAT_INTERVAL_S, LEASE_TTL_S
from .cron_parse import now_utc
from .decision import decide_slot, failure_policy


def build_command(task: dict, root: Path) -> list:
    """派发 argv（X2：全列表，不进 shell，参数名不靠引号）。"""
    run_args = list(task.get("run_args") or [])
    state.guard_run_args(run_args)   # 显式 raise ValueError，不是 assert（C1-C）
    guard_fast_cdp(run_args)
    return [sys.executable, "-m", "atlas", "run", str(task["flow"]), *run_args]


# 产物 attach/写放行旋钮的键名出处（gap9b B-3.5：调度 env 不得携这三键——
# attach 依赖人登录的浏览器；写放行是「人显式批准」的动作，不许从审批面挪到环境残留面，
# 与「argv 永不带 --approve」同一道红线 #13 的两半）。
FAST_CDP_ENV_KEY = "ATLAS_FAST_CDP"
FAST_ENV_KEYS_STRIPPED = (FAST_CDP_ENV_KEY, "ATLAS_FAST_INTERACTIVE",
                          "ATLAS_FAST_ALLOW_WRITES")


def guard_fast_cdp(run_args: list) -> None:
    """调度派发**永不**把 attach 通道写进 argv（lead 裁决 2026-11-09，判据形态沿
    `state.guard_run_args` 先例——显式 raise，`python -O` 剥 assert 时闸不倒；
    两闸分离是契约 B-3.5 定稿：guard_run_args 管审批旗标，本闸管会话通道残留）。

    无人值守不得依赖人登录的浏览器：argv 面出现该键只能是台账 run_args 夹带
    （人配置=显式意图，当场点名拒绝，不静默丢弃人的配置）。环境继承面另由
    `child_env` 剔除处理——两面合起来才是完整判据（只钉 argv 防不住 shell export
    经 os.environ 漏进子进程的案发路径）。
    """
    for item in run_args:
        text = str(item)
        if FAST_CDP_ENV_KEY in text or text.startswith("--cdp"):
            # 键名夹带（KEY=… / 裸引用）与旗标近似形态同拒：判据不猜携带形态
            raise ValueError(
                f"调度派发禁用 attach 通道：run_args 不得夹带 {FAST_CDP_ENV_KEY}"
                "（无人值守不得依赖人登录的浏览器；attach 只属于人工现场）")


def child_env(root: Path, base: "dict | None" = None) -> dict:
    """子进程环境（C1-E + X5）。

    C1-E 实测缺陷：src 布局下 `[sys.executable, "-m", "atlas"]` 在「未安装 + 无 PYTHONPATH」
    的环境直接起不来（`No module named atlas`），OS cron 的 env 比交互 shell 更干净。
    判据是 `<root>/src` 存在才注入（真装成包时不需要，注入空目录也无害，但不留假路径）；
    `PYTHONIOENCODING=utf-8` 让子进程中文输出不撞 GBK 控制台（X5）。
    """
    env = dict(os.environ if base is None else base)
    # attach/写放行三键在环境继承面的剔除（与 guard_fast_cdp 同一条裁决的两半，
    # gap9b B-3.5）：os.environ 里的残留是人 shell 的环境，不是调度配置，人不该为
    # 「碰巧 export 过」被拒跑——但无人值守子进程**绝不**继承它们：attach 依赖人登录
    # 的浏览器；ALLOW_WRITES 被剥离后写腿在产物侧仍走默认拦截（env_blocked 点名，
    # 剥离放行≠放行成功——行为面测归 support-impl，C 表钉死）。
    for key in FAST_ENV_KEYS_STRIPPED:
        env.pop(key, None)
    src = Path(root) / "src"
    if src.is_dir():
        parts = [item for item in str(env.get("PYTHONPATH", "")).split(os.pathsep) if item]
        resolved = str(src)
        if resolved not in parts:
            parts.insert(0, resolved)
        env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _argv_digest(command: list) -> str:
    """台账里的命令摘要：**只留 flag，不留值参**（run_args 允许账号名，值不进调度索引）。"""
    flow = str(command[4]) if len(command) > 4 else ""
    flags = [item for item in command[5:] if str(item).startswith("-")]
    return " ".join([f"atlas run {flow}"] + flags)


def _spawn(command: list, *, cwd: Path, timeout: float, env: dict):
    """subprocess 的唯一接线点：超时由 `subprocess.run(timeout=…)` 终止（POSIX SIGKILL /
    Windows TerminateProcess）——**不自己发信号、不用 os.kill 探活**（X7）；不加
    start_new_session，人 Ctrl-C 调度器时子进程同组同收（§6.8）。"""
    return subprocess.run(command, cwd=str(cwd), timeout=timeout, env=env)


def _record(root: Path, *, task_id: str, trigger: str, scheduled_for: "str | None",
            skipped: int, outcome: str, started: "datetime | None" = None,
            ended: "datetime | None" = None, child_rc=None,
            command_digest: "str | None" = None, stale: "dict | None" = None) -> None:
    """调度索引落盘（单行全事实）。

    台账/索引写失败**不顶业务信号**（#110/#130 同纪律）：本轮真实发生过，rc 与 stderr 照常。
    `engine_run_id` 恒 null 是有意口径：runs.jsonl 与 schedule_runs 分离，跨域关联要人按
    started_at 对照去读——不去猜「哪条业务记录是我这轮写的」（并发时猜必错）。
    """
    line = {"run_key": state.new_run_key(), "task_id": task_id, "trigger": trigger,
            "scheduled_for": scheduled_for, "skipped_slots": int(skipped),
            "started_at": state.iso(started), "ended_at": state.iso(ended),
            "outcome": outcome, "counts_as_failure": failure_policy(outcome) == "count",
            "child_rc": child_rc, "child_command": command_digest, "engine_run_id": None}
    if stale is not None:
        line["stale"] = stale
    try:
        state.append_run(state.runs_path(root), line)
    except OSError as exc:
        print(f"WARN: 调度索引写入失败（不影响本轮结果）：{exc}", file=sys.stderr)


def _run_with_heartbeat(root: Path, task_id: str, command: list, lock: Path, token: str,
                        timeout_min: int) -> tuple:
    """派发窗口：心跳线程随 with 结束停（stdlib threading，daemon + join 超时，不留孤儿线程）。

    返回 `(outcome, child_rc, started, ended, propagate)`——`propagate` 只在人 Ctrl-C 时带
    原始 KeyboardInterrupt（记账后由 tick 原样重抛，保留 traceback，不吞终止信号 #114）。
    """
    stop = threading.Event()

    def _loop() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_S):
            lease.heartbeat(lock, token, time.time())

    thread = threading.Thread(target=_loop, name=f"atlas-lease-{task_id}", daemon=True)
    thread.start()
    started = datetime.now(timezone.utc)
    outcome = child_rc = propagate = None
    try:
        try:
            result = _spawn(command, cwd=root, timeout=timeout_min * 60, env=child_env(root))
            child_rc = result.returncode
            # 派发只跑 `atlas run`（build_command 钉死），所以 rc 语义表就是 **run 面**那张：
            # 0=成功、3=等人（C9 显态，run/agent/dispatch 子进程面同值——`atlas gen` 的 rc3
            # 是结构自检失败，两义不并表，评审必改 C9-D）、124=用例被自身看护掐表、
            # 130=子进程收到 Ctrl-C 组播、其余非 0=失败。
            # 次序钉死：0 → 3 → 124 → 130 → 其余（waiting/interrupted 都必须在 failed 之前，
            # 否则等人与中断会被洗成失败并推高连败）。
            # 124/130 归到**既有** outcome 名而不是新造词：台账 `child_rc` 已经记着具体码值，
            # 「谁掐的表」（子进程自身看护 vs 派发窗口）在那里分辨得开，词表膨胀反而会让
            # decision._POLICY 与下游读台账的人多维护一套同义词（#92 反向）。
            # 130→interrupted 是本轮补的实缺口：_spawn 不加 start_new_session（§6.8），人按
            # Ctrl-C 时子进程同组同收，Windows 下退出码就是 130——旧表把它落进 else 洗成
            # failed 并 `counts_as_failure=true`，等于把「操作者收工」记成「这轮巡检坏了」，
            # 连败计数被无端 +1，攒够就把任务 AUTO-PAUSED（#114 CONTROL_FLOW 在调度域的漏网面）。
            if child_rc == 0:
                outcome = "succeeded"
            elif child_rc == 3:
                outcome = "waiting"
            elif child_rc == 124:
                outcome = "aborted_time_limit"
            elif child_rc == 130:
                outcome = "interrupted"
            else:
                outcome = "failed"
        except subprocess.TimeoutExpired:
            # 子进程已被 subprocess 内部 kill；这里绝不补发信号（X7）
            outcome, child_rc = "aborted_time_limit", None
        except KeyboardInterrupt as exc:
            # 人 Ctrl-C 调度器：CONTROL_FLOW（#114）在调度域的第二实现——记 interrupted，不计败
            outcome, child_rc, propagate = "interrupted", None, exc
    finally:
        stop.set()
        thread.join(timeout=5.0)
    ended = datetime.now(timezone.utc)
    return outcome, child_rc, started, ended, propagate


def tick(task_id: str, config, *, now: "datetime | None" = None, dry: bool = False) -> int:
    """跑一轮调度。返回码口径（§7）：2 用户错误 / 0 一切调度结果（defer、none、drop 都是
    正常答复——cron 不看 rc 干活，人看 `schedule_runs` 与 `status`）/ 1 仅派发子进程失败。"""
    root = Path(config.root)
    tasks_file = state.tasks_path(root)
    doc = state.load_doc(tasks_file)
    task = state.get_task(doc, task_id, source=tasks_file)
    # 停用判定在 dry 分支**之后**（2026-11-24 实测修正）：`--dry-run` 是**诊断面**，
    # 停用任务跑它旧实现静默 return 0、零输出——人分不清「任务停用了」与「这轮没到期」，
    # 只能去翻 tasks.json 才知道自己为什么看不到计划（AUTO-PAUSED 的真实排查路径）。
    # 实际 tick 语义逐字不变（下面仍 return 0，不派发、不记台账）。
    disabled = not task.get("enabled", True)
    moment = now or now_utc()
    flow_file = state.resolve_flow(root, task.get("flow"))
    if not flow_file.is_file():
        raise ValueError(f"任务 {task_id!r} 的 flow 文件不存在：{flow_file}")
    command = build_command(task, root)
    # 水位线：CAS 用**台账原值**（没水位线就是 None），决策用 `since`——首轮以 created_at
    # 兜底。把 created_at 当 CAS 的 expected 会让「还没跑过」的任务永远认不领（stored=None
    # ≠ expected=created_at），本轮被误判成「别人先抢了」而静默跳过。
    watermark_raw = task.get("last_scheduled_for")
    since = state.parse_iso(watermark_raw or task.get("created_at"),
                            "last_scheduled_for/created_at")

    if dry:
        decision = decide_slot(task, moment, since)
        print(json.dumps({
            "task_id": task_id, "dry_run": True, "action": decision.action,
            "trigger": decision.trigger, "scheduled_for": state.iso(decision.scheduled_for),
            "skipped_slots": decision.skipped_slots, "now": state.iso(moment),
            "watermark": watermark_raw, "command": command,
            # 停用态必须显式出现在计划里：否则「disabled」与「这轮没到期」在 dry-run 输出上
            # 无法区分（2026-11-24 实测：停用任务旧实现零输出、rc 0）。
            "enabled": not disabled,
            "max_duration_min": int(task.get("max_duration_min") or DEFAULT_MAX_DURATION_MIN),
            "lease": str(state.lease_path(root, task_id))},
            ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if disabled:
        # AUTO-PAUSED 的答复是 `atlas schedule status`，OS cron 这一路不需要噪声——
        # 只有 dry-run（诊断面）才值得把停用态说出来。
        return 0

    lock = state.lease_path(root, task_id)
    token = secrets.token_hex(8)
    # 只为「接管了谁」的记录读一次旧锁；抢占判据只在 lease.try_acquire 一处（#92 不留第二份）
    previous = lease.read_lock(lock)
    if previous is None and lock.exists():
        print(f"WARN: 租约文件内容无法解析，按过期接管处理：{lock}", file=sys.stderr)
    if not lease.try_acquire(lock, token, os.getpid(), time.time(), ttl_s=LEASE_TTL_S):
        _record(root, task_id=task_id, trigger="schedule", scheduled_for=None, skipped=0,
                outcome="defer_lease")   # 推迟≠跳过：不推进水位线，OS cron 下一 tick 还会来
        return 0
    if previous is not None and previous.get("token") != token:
        # pi recoverOrphanedRuns 的 Atlas 形态：一次性进程没有「遗留 running 行」可封，
        # 孤儿证据由这条 reclaim 记录 + runs.jsonl 残行回答。
        _record(root, task_id=task_id, trigger="schedule", scheduled_for=None, skipped=0,
                outcome="lease_reclaimed",
                stale={"pid": previous.get("pid"), "host": previous.get("host")})
    try:
        decision = decide_slot(task, moment, since)
        if decision.action == "none":
            return 0
        due_iso = state.iso(decision.scheduled_for)
        # CAS 认领：重读台账校验水位线没被人动过才写新值。C1-F——CAS 只是辅助防线，**互斥权威
        # 是租约**（这里的读-校验-写并非原子，同步盘/NFS 上更不可靠）；不要把 CAS 当唯一闸。
        if not state.claim_slot(tasks_file, task_id, watermark_raw, due_iso):
            return 0   # 另一进程先认领了这一轮：幂等退出 0，不双跑也不双记
        if decision.action == "drop":
            # 表 6：>7d 丢弃——水位线推进（否则每轮重判同一笔旧账），记录 skipped+1，不计败
            _record(root, task_id=task_id, trigger="schedule", scheduled_for=due_iso,
                    skipped=decision.skipped_slots, outcome="dropped")
            return 0
        if task.get("schedule", {}).get("kind") == "once":
            state.claim_once_done(tasks_file, task_id)   # §5 claimSlot：once 认领即停
        timeout_min = int(task.get("max_duration_min") or DEFAULT_MAX_DURATION_MIN)
        outcome, child_rc, started, ended, propagate = _run_with_heartbeat(
            root, task_id, command, lock, token, timeout_min)
        _record(root, task_id=task_id, trigger=decision.trigger or "schedule",
                scheduled_for=due_iso, skipped=decision.skipped_slots, outcome=outcome,
                started=started, ended=ended, child_rc=child_rc,
                command_digest=_argv_digest(command))
        mode = failure_policy(outcome)
        reason = ""
        if mode == "count":
            # child_rc 有值就报码值（failed 与「子进程自身看护掐表 rc=124」都要能看出来）；
            # 只有派发窗口超时那类 child_rc=None 才写「timeout after Xmin」——那是唯一
            # 真的是派发侧超时的情形，旧实现把所有 count 类都写成它，rc=124 的台账会
            # 谎报「跑了 30 分钟被派发器杀」，而事实可能是用例 60 秒就自己超时了。
            reason = (f"rc={child_rc}" if child_rc else f"timeout after {timeout_min}min")
        try:
            updated = state.apply_failure_policy(tasks_file, task_id, mode, reason=reason)
        except OSError as exc:
            print(f"WARN: 连败计数写入失败（不影响本轮结果）：{exc}", file=sys.stderr)
            updated = {}
        if mode == "count" and updated.get("auto_paused_reason"):
            print(f"AUTO-PAUSED: {task_id} after 5 consecutive failures "
                  f"({updated['auto_paused_reason']})", file=sys.stderr)
        if propagate is not None:
            raise propagate            # 记账完成后原样重抛人的终止信号（保留 traceback，#114 不吞）
        # rc 口径（§7）：0=一切**调度层面正常答复**（defer/none/drop/succeeded/waiting/
        # 子进程被人的 Ctrl-C 中止 interrupted），1=派发子进程真失败。C9 的 waiting 归 0：
        # `rc 1` 在 OS cron/任务计划器侧的既有读法「这条巡检挂了」，把「等人」报成 1 等于
        # 洗红——与 AUTO-PAUSED 同一条链会被计进连败并把任务停掉（等人恰恰需要人来看）。
        # interrupted 同判：台账里它 `counts_as_failure=false`、连败走 keep，若 tick 返 1
        # 就出现「同一轮在 schedule_runs 说不算失败、在 OS 侧说这轮挂了」的自相矛盾口径
        # （调度器**自身**被 Ctrl-C 那条腿不走这里——它 propagate 重抛，rc 由顶层给 130）。
        # 业务终态由 run 轨自己用 rc 3/1 表达，调度层只负责「这一轮我正常答复了」。
        return 0 if outcome in ("succeeded", "waiting", "interrupted") else 1
    finally:
        lease.release(lock, token)

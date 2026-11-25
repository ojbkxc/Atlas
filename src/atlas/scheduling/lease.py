"""O_EXCL 原子锁 + 心跳 TTL 租约（design_c1 §4，gap6 补充 S1/S2 口径）。

判据分层（S2 细则，实现不得越线）：
- **心跳 TTL 是权威判据**：崩溃/孤儿主场景（C1a 的全部实盘需求）靠 `heartbeat_at` 超时即可
  判定，跨平台、零平台专属代码。
- **pid 探针只作「提前接管」加速项**，接口在 `pid_alive()`，实现留 C1b。Windows 禁
  `os.kill(pid, 0)`——signal 0 实为 `GenerateConsoleCtrlEvent` 向**进程组**广播 Ctrl+C
  （可能打断调度器自己），其余取值走 TerminateProcess；它既误杀「活着但非组主」的健康持有者，
  又漏判——双跑恰是租约要防的（gap6 §3.1）。`os.pid_exists` 在 3.13 实测**不存在**（3.15+
  才有，Atlas 声明 3.11+），所以探针必自写。`STILL_ACTIVE(259)` 与真实退出码 259 冲撞，
  误判方向是「判存活 → 永不接管 → 停摆」而不是双跑——因此不确定（探针返 None）时**按推迟
  记录，不判失败**（与 waiting≠failed 同族）。

X3 跨平台差异（创建冲突两平台同抛 FileExistsError，差异在**删**）：
- 创建即关句柄（`os.close` 紧跟 `os.write`），句柄不过夜，Windows 删除才不被自己的句柄锁住。
- 接管时 `unlink` 撞 PermissionError（句柄未关/杀软瞬锁）→ 重试 1 次（100ms），仍败按
  「推迟≠跳过」退出（rc0），**绝不硬删**。
- 抢到后**回读锁文件确认 token 是自己的**（pi lease.ts:89 双检，防两个进程同瞬互踩；同步盘/NFS
  上 O_EXCL 语义本身不可靠，这层只降低概率，真正的互斥仍要求调度目录在本地文件系统——README 有写明）。
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from .constants import LEASE_TTL_S
from ..atomic_io import read_json_text


def _payload(token: str, pid: int, now: float) -> dict:
    return {"token": token, "pid": pid, "host": socket.gethostname(),
            "started_at": now, "heartbeat_at": now}


def _dump(path: Path, payload: dict) -> None:
    """锁内容改写走 tmp+os.replace（X9）；被瞬锁重试 1 次仍败 → 抛，由调用方按「续租失败」处理。"""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
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


def _number(value: object) -> "float | None":
    """时间戳字段归一：bool 不是数（True→1.0 会把心跳读成 1970），非数值形态 → None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def read_lock(path: "str | Path") -> "dict | None":
    """锁文件读侧：坏 JSON/非映射/读崩一律 None（视同无锁），噪音由调用方如实记录。"""
    path = Path(path)
    try:
        # BOM 安全（单点口径 `utf-8-sig`）。这里是**最危险的一处**：坏 JSON 一律返回 None
        # =「视同无锁」，而带 BOM 的锁文件在裸 utf-8 下就是一个坏 JSON——于是锁文件的
        # **存在性反而让并发闸失效**（没锁文件时调用方可能还会报，有 BOM 锁文件反而静默
        # 当无锁），噪音只靠调用方如实记录。
        raw = read_json_text(path)
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _create_new(path: Path, token: str, pid: int, now: float) -> bool:
    """O_EXCL 原子创建；成功即写 payload 并**立刻关句柄**（X3）。

    `FileExistsError` 是「有人占着」的正常答复；其余 OSError（路径被目录占住、权限、盘故障）
    方向一致——**拿不到锁就推迟**，绝不硬删占着的东西（宁可停摆不能双跑，与 S2 同向）。
    """
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(fd, json.dumps(_payload(token, pid, now),
                                ensure_ascii=False, sort_keys=True).encode("utf-8"))
    finally:
        os.close(fd)
    return True


def _verify_owner(path: Path, token: str) -> bool:
    """回读双检（pi lease.ts:89）：确认锁里的 token 是自己的才算拿到。"""
    payload = read_lock(path)
    return payload is not None and payload.get("token") == token


def _remove(path: Path) -> bool:
    """接管前删旧锁：PermissionError 重试 1 次（100ms）仍败 → False（推迟退出，绝不硬删）。"""
    for attempt in (0, 1):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt:
                return False
            time.sleep(0.1)
        except OSError:
            return False
    return False


def pid_alive(pid: int) -> "bool | None":
    """pid 存活探针接口占位（C1b 实现，design_c1 §4）——**C1a 恒返回 None（=不确定）**。

    C1b 落地约束（设计只钉不实现，红线 #12 不给零使用投机功能，这里也不写探测代码）：
    - Windows：ctypes `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` +
      `GetExitCodeProcess` 比对 `STILL_ACTIVE=259`（真退出码恰为 259 时判存活 → 方向是停摆
      不是双跑，与 TTL 权威口径一致）；备选 `tasklist /FI "PID eq <pid>"`（argv，X2）。
      **禁用** `os.kill(pid, 0)`（CTRL_C_EVENT 广播 + 组语义误杀）。
    - 必配平台 case：「进程存在但非组主」「pid 已复用」（容器/pid 回收）。
    - 探针不确定 → 调用方按推迟记录，不判失败。
    """
    return None


def try_acquire(lock_path: "str | Path", token: str, pid: int, now: float,
                ttl_s: float = LEASE_TTL_S) -> bool:
    """抢租约。True=本轮归你；False=**推迟**（有人活着、删不动、或双检没通过）。

    `now` 是 unix 秒（float），由调用方注入——离线测不赌真实时钟。
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _create_new(path, token, pid, now):
        return _verify_owner(path, token)
    payload = read_lock(path)
    if payload is not None:
        heartbeat = _number(payload.get("heartbeat_at"))
        if heartbeat is None:
            heartbeat = _number(payload.get("started_at"))   # 心跳缺字段回落创建时间（读侧容错）
        if heartbeat is not None and now - heartbeat <= ttl_s:
            # 心跳新鲜 = 有人活着：TTL 为权威判据（S2），默认不抢占、本轮 defer。
            # 唯一例外是 C1b 探针「确定死亡」(is False) 的提前接管加速；C1a 探针恒 None
            # （不确定）→ 按推迟记录，不判失败。
            holder = _number(payload.get("pid"))
            holder_pid = int(holder) if holder is not None else 0
            if pid_alive(holder_pid) is not False:
                return False
    # 心跳过期、或锁内容是坏的（None）：上一任崩了/孤儿/写坏 → 接管
    if not _remove(path):
        return False
    if not _create_new(path, token, pid, now):
        return False
    return _verify_owner(path, token)


def heartbeat(lock_path: "str | Path", token: str, now: float) -> bool:
    """派发期间续租（dispatch 的守护线程按 HEARTBEAT_INTERVAL_S 调）。token 不符绝不改写。"""
    path = Path(lock_path)
    payload = read_lock(path)
    if payload is None or payload.get("token") != token:
        return False
    payload["heartbeat_at"] = now
    try:
        _dump(path, payload)
    except OSError:
        return False   # 续租没续上如实报 False：本轮继续跑，租约过期后由 TTL/接管语义兜底
    return True


def release(lock_path: "str | Path", token: str) -> None:
    """仅 token 相符才删（别人的租约不碰）。删除失败（Windows 瞬锁/杀软）不顶业务信号：
    留下的锁会在 TTL 后被正常接管路径清掉，孤儿锁不会永久挡路。"""
    path = Path(lock_path)
    payload = read_lock(path)
    if payload is None or payload.get("token") != token:
        return
    _remove(path)

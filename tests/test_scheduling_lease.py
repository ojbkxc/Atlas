"""C1 租约（design_c1 §4 + X3 + gap6 S1/S2 口径）。

覆盖：抢/续/释放、token 不符不删、stale 接管、**占用中删失败→推迟**（Windows 面）、
并发双抢只一个赢、回读 token 双检、`pid_alive` 在 C1a 恒 None（不确定→推迟）以及
C1b 契约分流（探针确定死亡才允许提前接管）。全程离线：时间由参数注入。
"""
import json
import os
import socket
from pathlib import Path

import pytest

from atlas.scheduling import lease
from atlas.scheduling.constants import LEASE_TTL_S


@pytest.fixture
def lock(tmp_path):
    return tmp_path / "lease" / "patrol.lock"


def _fresh(path, token="tokA", pid=4242, now=1000.0):
    assert lease.try_acquire(path, token, pid, now) is True
    return now


# ── 抢 / 读 / 释放 ──────────────────────────────────────────────────

def test_acquire_writes_full_payload_and_creates_parent(lock):
    assert lease.try_acquire(lock, "tokA", 4242, 1000.0) is True
    payload = lease.read_lock(lock)
    assert payload["token"] == "tokA"
    assert payload["pid"] == 4242
    assert payload["host"] == socket.gethostname()
    assert payload["started_at"] == 1000.0 and payload["heartbeat_at"] == 1000.0


def test_handle_does_not_outlive_the_call(lock):
    """X3：创建即关句柄——否则 Windows 后面删自己的锁都会失败（FileExistsError→rename 测）。"""
    _fresh(lock)
    moved = lock.with_name("moved.lock")
    os.replace(lock, moved)          # 句柄没过夜才改得动
    assert moved.exists()


def test_second_acquire_defers_while_heartbeat_fresh(lock):
    _fresh(lock)
    assert lease.try_acquire(lock, "tokB", 999, 1000.0 + LEASE_TTL_S - 1) is False
    assert lease.read_lock(lock)["token"] == "tokA"     # 不抢占 = 不动别人的锁


@pytest.mark.parametrize("elapsed, winner", [
    (LEASE_TTL_S, False),           # 心跳 ≤90s：有人活着（边界含）
    (LEASE_TTL_S + 0.001, True),    # 刚过 TTL：上一任崩了/孤儿 → 接管
])
def test_ttl_is_the_authority_at_the_boundary(lock, elapsed, winner):
    _fresh(lock, now=1000.0)
    assert lease.try_acquire(lock, "tokB", 7, 1000.0 + elapsed) is winner


def test_stale_takeover_replaces_token(lock):
    _fresh(lock, now=1000.0)
    assert lease.try_acquire(lock, "tokB", 7, 1000.0 + LEASE_TTL_S * 2) is True
    assert lease.read_lock(lock)["token"] == "tokB"


def test_heartbeat_refreshes_only_for_owner(lock):
    _fresh(lock, now=1000.0)
    assert lease.heartbeat(lock, "tokA", 1080.0) is True
    assert lease.read_lock(lock)["heartbeat_at"] == 1080.0
    assert lease.heartbeat(lock, "someone-else", 1090.0) is False
    assert lease.read_lock(lock)["heartbeat_at"] == 1080.0   # token 不符绝不改写


def test_heartbeat_on_missing_lock_is_false(lock):
    assert lease.heartbeat(lock, "tokA", 1.0) is False


def test_heartbeat_io_error_reports_false_instead_of_crashing(lock, monkeypatch):
    """续租写不动就如实 False：本轮继续跑，租约过期由 TTL/接管语义兜底。"""
    _fresh(lock)

    def boom(path, payload):
        raise OSError("disk busy")

    monkeypatch.setattr(lease, "_dump", boom)
    assert lease.heartbeat(lock, "tokA", 2000.0) is False


def test_release_only_matching_token(lock):
    _fresh(lock)
    lease.release(lock, "wrong-token")
    assert lock.exists()
    lease.release(lock, "tokA")
    assert not lock.exists()


def test_release_missing_lock_is_not_an_error(lock):
    lease.release(lock, "tokA")   # 幂等收尾不抛（finally 里的清理不能顶业务信号）


def test_release_leaves_foreign_lock_untouched(lock):
    _fresh(lock, token="tokA")
    lease.release(lock, "tokB")
    assert lease.read_lock(lock)["token"] == "tokA"


# ── 读侧容错（#112/#131 同纪律）──────────────────────────────────────

@pytest.mark.parametrize("content", ["{not json", "[]", '"just-a-string"', ""])
def test_read_lock_bad_shapes_return_none(lock, content):
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(content, encoding="utf-8")
    assert lease.read_lock(lock) is None


def test_read_lock_missing_file_is_none(lock):
    assert lease.read_lock(lock) is None


def test_corrupt_lock_is_treated_as_stale_and_reclaimed(lock):
    """坏 JSON → 视同无锁（§4）：接管路径照样能拿到，不留死锁。"""
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("半截字节", encoding="utf-8")
    assert lease.try_acquire(lock, "tokB", 5, 5000.0) is True
    assert lease.read_lock(lock)["token"] == "tokB"


def test_non_numeric_heartbeat_falls_back_to_started_at(lock):
    """心跳字段形态坏不顶判据：回落创建时间，两者都不数值才当过期处理。"""
    lock.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"token": "a", "pid": 1, "host": "h",
                          "started_at": 1000.0, "heartbeat_at": "昨天"})
    lock.write_text(content, encoding="utf-8")
    assert lease.try_acquire(lock, "b", 2, 1001.0) is False    # 回落 started_at=1000 → 1s 内仍算活着
    lock.write_text(content, encoding="utf-8")
    assert lease.try_acquire(lock, "b", 2, 2000.0) is True     # started_at 也过期 → 接管


# ── X3：Windows 差异面（删不动→推迟，绝不硬删）───────────────────────

def test_unremovable_lock_defers_without_deleting(tmp_path):
    """占用删不动 → 本轮推迟（rc0 语义由 dispatch 落），而且**绝不硬删**。

    形态取「锁位置被目录占住」：两平台的 unlink 都会失败，比 monkeypatch 更接近
    真实故障（同步盘占位/杀软瞬锁），且不依赖具体错误码。
    """
    occupied = tmp_path / "lease" / "patrol.lock"
    occupied.mkdir(parents=True)
    assert lease.try_acquire(occupied, "tokB", 3, 1000.0) is False
    assert occupied.is_dir()                                    # 没被 rm -rf 式硬闯


def test_permission_error_then_retry_succeeds(lock, monkeypatch):
    """删失败重试 1 次（100ms）——第一次 PermissionError、第二次成功 → 接管成立。"""
    _fresh(lock, token="tokA", now=0.0)
    real_unlink = Path.unlink
    attempts = []

    def flaky(self, *args, **kwargs):
        attempts.append(self)
        if len(attempts) == 1:
            raise PermissionError("被杀软瞬锁")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    monkeypatch.setattr(lease.time, "sleep", lambda seconds: None)   # 不真等 100ms
    assert lease.try_acquire(lock, "tokB", 9, 10_000.0) is True
    assert len(attempts) == 2


def test_permission_error_persistent_defers(lock, monkeypatch):
    _fresh(lock, token="tokA", now=0.0)

    def always_locked(self, *args, **kwargs):
        raise PermissionError("一直被占")

    monkeypatch.setattr(Path, "unlink", always_locked)
    monkeypatch.setattr(lease.time, "sleep", lambda seconds: None)
    assert lease.try_acquire(lock, "tokB", 9, 10_000.0) is False
    assert lease.read_lock(lock)["token"] == "tokA"     # 别人的锁还在，等 TTL 再收


def test_takeover_readback_mismatch_defers_and_keeps_foreign_lock(lock, monkeypatch):
    """回读双检（pi lease.ts:89）：抢到后 token 不是自己的 → defer，且不删对方的锁。"""
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"token": "old", "pid": 1, "host": "h",
                                "started_at": 0.0, "heartbeat_at": 0.0}), encoding="utf-8")
    original = lease._create_new
    calls = []

    def racing(path, token, pid, now):
        calls.append(token)
        if len(calls) == 1:
            return original(path, token, pid, now)     # 正常：已存在 → False（进入接管判定）
        # 抢占窗口：删掉旧锁后、写入自己 token 前，另一进程先落了它的锁（同步盘/NFS 缝隙）
        original(path, "other-process", pid, now)
        return True

    monkeypatch.setattr(lease, "_create_new", racing)
    assert lease.try_acquire(lock, "mine", 10, 10_000.0) is False
    assert len(calls) == 2
    assert lease.read_lock(lock)["token"] == "other-process"


# ── 双抢与生命周期 ──────────────────────────────────────────────────

def test_double_grab_single_winner_then_release_frees(lock):
    assert lease.try_acquire(lock, "A", 1, 1000.0) is True
    assert lease.try_acquire(lock, "B", 2, 1000.5) is False
    lease.release(lock, "A")
    assert lease.try_acquire(lock, "B", 2, 1000.6) is True      # 同一时刻谁先抢到谁跑


def test_custom_ttl_is_respected(lock):
    _fresh(lock, now=0.0)
    assert lease.try_acquire(lock, "B", 1, 5.0, ttl_s=10) is False
    assert lease.try_acquire(lock, "B", 1, 20.0, ttl_s=10) is True


# ── pid 探针：C1a 恒不确定；C1b 契约分流（design_c1 §4/§8）───────────

def test_pid_alive_is_uncertain_in_c1a():
    """C1a 不实现探针：恒 None。实现被留在 C1b，接口签名在此钉住。"""
    assert lease.pid_alive(os.getpid()) is None
    assert lease.pid_alive(999999) is None


def test_fresh_heartbeat_with_uncertain_probe_defers(lock, monkeypatch):
    """S2 细则：心跳新鲜 + 探针不确定 → 按**推迟**处理，不判失败、不抢占。"""
    _fresh(lock, now=1000.0)
    seen = []

    def uncertain(pid):
        seen.append(pid)
        return None

    monkeypatch.setattr(lease, "pid_alive", uncertain)
    assert lease.try_acquire(lock, "B", 2, 1010.0) is False
    assert seen == [4242]                                  # 探针确实被问过（接口在用）
    assert lease.read_lock(lock)["token"] == "tokA"


@pytest.mark.parametrize("probe, winner", [
    (False, True),      # 探针**确定**死亡：C1b 允许提前接管（加速项）
    (True, False),      # 探针确认存活：绝不抢占
])
def test_c1b_contract_definitive_probe_gates_early_takeover(lock, monkeypatch, probe, winner):
    _fresh(lock, now=1000.0)
    monkeypatch.setattr(lease, "pid_alive", lambda pid: probe)
    assert lease.try_acquire(lock, "B", 2, 1010.0) is winner

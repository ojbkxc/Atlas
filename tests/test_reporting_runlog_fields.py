"""runlog 导出字段口径钉死（2026-11-05 审计，两批；2026-11-06 修 dead-read 追加）。

retries 有三个真实来源（缺一即恒 0 假统计）：
① heal 就地重跑恰好一次 → engine 写 `healed`=True → 推导 1（engine.py:133-147/:161）；
② 步骤级 flaky 重试（config.step_retry>0）→ engine 写 **`retried`**=实际次数
   （engine.py:294）——2026-11-06 之前本导出只读 `retries`，而全仓零生产方写这个键，
   ②在 runlog 里恒 0，且旧注释把这形态登记成「已知遗留」继续原样读；现按
   `retries`(外部显式) → `retried`(本引擎) → healed 推导 三级回落，导出 schema 字段名不变；
③ 有界 auto 降级重执行 → `degraded_to_ui` 留痕（不计入 retries，语义是通道切换不是重试）。
reloads / wait_ms / ui_ms：全仓无生产方——字段从导出 schema 删除。api_ms 有真实
生产方（engine.py:102），保留在 schema 里。
"""
import json

from atlas.recorder import RunRecorder
from atlas.reporting import export_runlog


def _rows(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").strip().splitlines()]


def _export(tmp_path, steps, name="atlas.jsonl"):
    runs = tmp_path / name
    runs.write_text(json.dumps({"run_id": "r1", "case": "A", "env": "sit",
                                "status": "passed", "steps": steps}) + "\n",
                    encoding="utf-8")
    return _rows(export_runlog(runs, tmp_path / "out.jsonl"))


def test_retries_derives_from_healed_flag(tmp_path):
    """healed=True 的步骤（自愈恰好重跑过一次）导出 retries=1；未自愈步骤如实 0。"""
    rows = _export(tmp_path, [
        {"step": "go", "op": "ui", "ok": True, "dur_ms": 10, "healed": True},
        {"step": "save", "op": "api", "ok": True, "dur_ms": 5},
    ])
    assert [r["retries"] for r in rows] == [1, 0]


def test_healed_false_or_absent_not_counted(tmp_path):
    """healed 为 False/null/0 都不算重试——只有真重跑成功（engine 只在成功路径写 True）
    才计 1；坏形态不猜不放大（与「自愈重试失败不留 healed 痕迹，如实回落 0」边界一致）。"""
    rows = _export(tmp_path, [
        {"step": "a", "healed": False},
        {"step": "b", "healed": None},
        {"step": "c", "healed": 0},
    ])
    assert [r["retries"] for r in rows] == [0, 0, 0]


def test_explicit_retries_key_passthrough_not_overridden(tmp_path):
    """外部/手改 jsonl 已显式带 retries 键时原样透传（兼容旧透传行为，不覆盖他方生产方）；
    即便同一步还带 healed，显式值优先——推导只补空位，不篡改已有数据。"""
    rows = _export(tmp_path, [
        {"step": "a", "retries": 3},
        {"step": "b", "retries": 2, "healed": True},
    ])
    assert [r["retries"] for r in rows] == [3, 2]


def test_reloads_field_removed_from_export(tmp_path):
    """reloads 全仓无生产方（runtime._wait_topbar 的 reload 从不落步骤记录），
    导出 schema 不再出现该键——即使输入行里带着外部 reloads 值也不得继续伪造消费面。"""
    rows = _export(tmp_path, [{"step": "go", "op": "ui", "ok": True, "dur_ms": 7,
                               "reloads": 5}])
    assert "reloads" not in rows[0]


def test_wait_ms_ui_ms_removed_from_export(tmp_path):
    """wait_ms/ui_ms 与 reloads 同形态死字段（recorder 的 step 记录从无这两个键，
    耗时分解零生产方）：导出 schema 删除，输入行带外部值也不透传——
    留着就是恒 0 假统计（红线 #7 同族），要统计先接生产方。"""
    rows = _export(tmp_path, [{"step": "go", "op": "ui", "ok": True, "dur_ms": 7,
                               "wait_ms": 120, "ui_ms": 340}])
    assert "wait_ms" not in rows[0]
    assert "ui_ms" not in rows[0]
    # api_ms 有真实生产方（engine.py:102），不在删除之列
    assert "api_ms" in rows[0]


def test_export_schema_keys_stable_except_dead_fields(tmp_path):
    """删死字段（reloads/wait_ms/ui_ms）之外的导出主键一个不少（外部 runlog 消费方兼容面）：
    固定 schema 逐键核对，防止未来把有生产方的键也顺手「审计」掉。"""
    rows = _export(tmp_path, [{"step": "go", "op": "ui", "ok": True, "dur_ms": 7}])
    assert set(rows[0]) == {"run_id", "case", "env", "step", "op", "ok",
                            "dur_ms", "api_ms", "retries", "fail_class"}


def test_recorder_roundtrip_healed_survives_into_export(tmp_path):
    """契约来源核验（#106/#139 同族：消费侧接通前必须确认生产侧真实落盘）：
    RunRecorder 与 engine 共享同一条 step dict，就地补 healed（engine.py:161 协作口径）
    后 runs.jsonl 里键真实存在，导出端到端 retries=1——不是库函数单测里的纸面键。"""
    recorder = RunRecorder("case", "sit", tmp_path / "atlas.jsonl")
    with recorder.step("1") as step:
        step["healed"] = True
        step["ok"] = True
    recorder.close()
    raw = [json.loads(line) for line in
           (tmp_path / "atlas.jsonl").read_text(encoding="utf-8").splitlines()]
    assert raw[0]["steps"][0].get("healed") is True      # 生产侧真的落盘
    rows = _rows(export_runlog(tmp_path / "atlas.jsonl", tmp_path / "out.jsonl"))
    assert rows[0]["retries"] == 1


# ── dead-read 修复：②flaky 重试的真实键 `retried` 必须被读到 ────────────

def test_retried_key_exported_not_zero(tmp_path):
    """生产方键名 `retried`（engine.py:294）导出为真实次数；旧读侧只认 `retries`
    → 恒 0。同一条数据里两个键分别给值，可自证读的是哪一个（防恒真）。"""
    rows = _export(tmp_path, [
        {"step": "a", "retried": 3},
        {"step": "b", "retried": 1, "healed": True},   # ②优先于①推导，不被 0/1 覆盖
        {"step": "c"},                                  # 无任何重试键 → 如实 0
    ])
    assert [r["retries"] for r in rows] == [3, 1, 0]


def test_explicit_retries_still_wins_over_retried(tmp_path):
    """外部/手改 jsonl 的显式 `retries` 仍原样透传（兼容面不因修复而翻）。"""
    rows = _export(tmp_path, [{"step": "a", "retries": 7, "retried": 2}])
    assert rows[0]["retries"] == 7


def test_engine_flaky_retry_lands_in_runlog_end_to_end(tmp_path, monkeypatch):
    """端到端：真引擎跑一次 flaky 重试 → runs.jsonl 有 `retried` → 导出 `retries` 非 0。
    这条是「消费侧接通」的唯一硬证据：库函数单测里的纸面键证不了生产方真的落盘
    （#106/#139 同族契约核验）。"""
    from atlas import Actor, FunctionalTask, TaskMetadata
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine
    monkeypatch.setenv("ATLAS_STEP_RETRY", "2")
    monkeypatch.setenv("ATLAS_STEP_RETRY_INTERVAL_MS", "0")
    rec = RunRecorder("flaky", "sit", tmp_path / "atlas.jsonl")
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    calls = {"n": 0}

    def flaky(actor):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("元素未出现")
        return {"v": 1}

    task = FunctionalTask(TaskMetadata("demo", "read", produces=("v",)), flaky, "read")
    assert engine.execute(Actor(), task).ok is True
    rec.close()
    raw = [json.loads(line) for line in
           (tmp_path / "atlas.jsonl").read_text(encoding="utf-8").splitlines()]
    step = raw[0]["steps"][0]
    assert step.get("retried") == 1, "前提：生产方键真实落盘（否则本测恒真）"
    assert "retries" not in step, "前提：生产方从不写 retries 键（旧读侧恒 0 的根因）"
    rows = _rows(export_runlog(tmp_path / "atlas.jsonl", tmp_path / "out.jsonl"))
    assert rows[0]["retries"] == 1

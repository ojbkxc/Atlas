"""重构波松绑的开关面判据（T7）：保两条红线，其余降级为显式开关。

覆盖任务书验收五项：
① 默认行为零变化——`ATLAS_ALLOW_PROD_WRITE` 未设时生产写仍被拒（`validate_write`）。
② 开关打开才放行且带警告 + 留痕——`=1` 时写步骤放行、stderr 一行 ASCII 警告、runs.jsonl
   步骤带 `prod_write_allowed`。
③ 开关值写坏（`=yess`）仍按关处理、不静默生效（点名后按关）。
④ 凭据红线仍拦得住——字面 token 在产物路径（`gate_flow_headers`）被换成占位、在落盘文本
   （`sanitize_text`）被净化，开关不旁路。
⑤ 断言红线不可被开关绕过——失败分类/控制流/永不重试名单一字不动。

另钉：放行生产写**不**豁免「非人工创建」标记（松绑 #2 不牵连红线 #3）。
全部离线，不碰真实网络/浏览器/凭据。
"""
import json

import pytest

from atlas import Actor
from atlas.config import AtlasConfig, PROD_WRITE_ENV_KEY, PROD_WRITE_WARNING
from atlas.core import FunctionalTask, TaskMetadata
from atlas.engine import ExecutionEngine
from atlas.recorder import RunRecorder


# ── 判据位：生产写护栏 ────────────────────────────────────────────────

def _prod_engine(tmp_path, name="prod-guard"):
    rec = RunRecorder(name, "prod-hk", tmp_path / "runs.jsonl", "run")
    return ExecutionEngine(AtlasConfig(tmp_path, env="prod-hk"), recorder=rec), rec


def _save_task(calls):
    def save(actor):
        calls["n"] += 1
        return None
    return FunctionalTask(TaskMetadata("demo", "save", read_only=False), save, "save")


# ① 默认行为零变化
def test_default_prod_write_still_blocked(tmp_path, monkeypatch):
    monkeypatch.delenv(PROD_WRITE_ENV_KEY, raising=False)
    cfg = AtlasConfig(tmp_path, env="prod-hk")
    assert cfg.allow_prod_write is False and cfg.prod_write_allowed is False
    with pytest.raises(PermissionError):
        cfg.validate_write("非人工创建")


def test_default_prod_write_block_leaves_same_trace(tmp_path, monkeypatch):
    """默认关时引擎行为与放行前逐字节一致：拦在 perform 前、step 记 env_blocked、
    且**不**带 prod_write_allowed 键（零涟漪）。"""
    monkeypatch.delenv(PROD_WRITE_ENV_KEY, raising=False)
    engine, rec = _prod_engine(tmp_path)
    calls = {"n": 0}
    with pytest.raises(PermissionError):
        engine.execute(Actor(), _save_task(calls))
    assert calls["n"] == 0
    assert rec.steps[0]["ok"] is False and rec.steps[0]["fail_class"] == "env_blocked"
    assert "prod_write_allowed" not in rec.steps[0]
    rec.close("failed")


# ② 开关打开才放行 + 警告 + 留痕
def test_allow_prod_write_permits_warns_and_audits(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PROD_WRITE_ENV_KEY, "1")
    engine, rec = _prod_engine(tmp_path)
    calls = {"n": 0}
    res = engine.execute(Actor(), _save_task(calls))
    assert res.ok is True and calls["n"] == 1                   # perform 真跑了
    err = capsys.readouterr().err
    assert err.splitlines() and err.splitlines()[0].startswith("WARN:")
    assert PROD_WRITE_ENV_KEY in err
    err.encode("cp936")                                         # X4：GBK 控制台不崩
    rec.close("passed")
    run = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8"))
    step = run["steps"][0]
    assert step["prod_write_allowed"] is True                   # 可审计留痕
    assert step["ok"] is True


def test_prod_write_warning_text_is_cp936_encodable():
    PROD_WRITE_WARNING.encode("cp936")


def test_allow_prod_write_does_not_exempt_remark(tmp_path, monkeypatch):
    """松绑 #2（生产禁写）不牵连红线 #3（非人工创建标记）：放行生产写仍要求备注带标记。"""
    monkeypatch.setenv(PROD_WRITE_ENV_KEY, "1")
    cfg = AtlasConfig(tmp_path, env="prod-hk")
    with pytest.raises(ValueError, match="非人工创建"):
        cfg.validate_write("")                                  # 空备注仍拒
    cfg.validate_write("测试单非人工创建")                       # 带标记才放行


# ③ 坏开关值按关处理、不静默生效
@pytest.mark.parametrize("bad", ["yess", "true", "on", "0", "yes", "01", "TRUE"])
def test_broken_switch_value_is_off_not_silently_on(tmp_path, monkeypatch, capsys, bad):
    monkeypatch.setenv(PROD_WRITE_ENV_KEY, bad)
    cfg = AtlasConfig(tmp_path, env="prod-hk")
    assert cfg.allow_prod_write is False                        # 坏值绝不静默当开
    with pytest.raises(PermissionError):
        cfg.validate_write("非人工创建")
    assert PROD_WRITE_ENV_KEY in capsys.readouterr().err       # 坏值点名


def test_whitespace_around_one_activates(tmp_path, monkeypatch):
    """`ATLAS_ALLOW_PROD_WRITE= 1 ` 去空白后是合法 '1'——放行（与 _int_env strip 口径一致）。"""
    monkeypatch.setenv(PROD_WRITE_ENV_KEY, " 1 ")
    assert AtlasConfig(tmp_path, env="prod-hk").allow_prod_write is True


# ④ 凭据红线仍拦得住（开关不旁路）
def test_credential_red_line_survives_switch(monkeypatch):
    from atlas import credential_gate as cg
    monkeypatch.setenv(PROD_WRITE_ENV_KEY, "1")                 # 打开生产写开关
    flow = {"steps": [{"task": "api_call",
                       "headers": {"Authorization": "Bearer 0123456789abcdef0123456789abcdef"}}]}
    mapping = cg.gate_flow_headers(flow)
    assert mapping == [(1, "Authorization", "ATLAS_HDR_1")]     # 字面凭据被占位
    assert "0123456789abcdef" not in json.dumps(flow)           # 值不落盘
    assert cg.sanitize_text("Authorization: Bearer 0123456789abcdef0123456789abcdef") \
        == "Authorization: <redacted>"                          # 落盘文本净化
    assert cg.looks_literal_secret("0123456789abcdef0123456789abcdef") is True


# ⑤ 断言红线不可被开关绕过
def test_assertion_red_line_survives_switch(monkeypatch):
    monkeypatch.setenv(PROD_WRITE_ENV_KEY, "1")
    from atlas.failures import (CONTROL_FLOW, AwaitingHuman, DeliveryUncertain,
                                FailureClass, classify_failure)
    from atlas import waits
    assert classify_failure(AssertionError("assert 3 == 4")) == FailureClass.ASSERTION
    assert classify_failure(KeyboardInterrupt()) == FailureClass.INTERRUPTED
    assert KeyboardInterrupt in CONTROL_FLOW
    assert waits.is_recoverable(AssertionError("x")) is False    # 断言失败永不重试
    assert waits.is_recoverable(AwaitingHuman("等人")) is False
    assert waits.is_recoverable(DeliveryUncertain("投递不确定")) is False


def test_assertion_failure_step_still_fails_with_switch_on(tmp_path, monkeypatch):
    """开关打开时，断言型 Task 抛错仍记 failed（不因任何开关洗绿）。"""
    monkeypatch.setenv(PROD_WRITE_ENV_KEY, "1")
    rec = RunRecorder("assert-guard", "prod-hk", tmp_path / "runs.jsonl", "run")
    engine = ExecutionEngine(AtlasConfig(tmp_path, env="prod-hk"), recorder=rec)

    def boom(actor):
        raise AssertionError("金额不符")

    task = FunctionalTask(TaskMetadata("demo", "check", read_only=True), boom, "check")
    with pytest.raises(AssertionError):
        engine.execute(Actor(), task)
    assert rec.steps[0]["ok"] is False
    assert rec.steps[0]["fail_class"] in {"assertion", "contract"}
    assert engine.results[0].ok is False
    rec.close("failed")
    run = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8"))
    assert run["status"] == "failed"                             # 绝不伪装 pass

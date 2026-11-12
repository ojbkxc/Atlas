"""C2 协调补丁的 **接线面**（design-plan §3：补丁 1 落 engine.py、补丁 2 落 cli.py，两文件 W1 归 impl_report）。

- 补丁 2（CLI）：`review --require-evidence` 存在且真透传——闸开时缺三元组一律
  rc 2 带指路文案；人显式豁免必须配 `--reason`（留痕），批量 `--apply` 同受该开关约束。
  重构波松绑：该闸**默认不生效**（`--require-evidence` 是显式 opt-in），下面的拦测一律带上它。
- 补丁 1（engine）：失败路径登记的草稿自带 `evidence.verification`，且这份自动三元组**必须过
  review 的晋级闸**——评审判定「engine 来源草稿不死锁」的前提就是这一条，所以它得有测。

review.py 的闸与 override 语义本体由 impl_tower 实现并在 `test_c2_verification.py` 测过 API 面。
"""
import json

import pytest

from atlas.experience import ExperienceJournal
from atlas.knowledge import KnowledgeBase
from atlas.review import ReviewQueue, verification_problems


class _StubPlugin:
    def __init__(self, kb):
        self._kb = kb

    def knowledge(self):
        return self._kb


def _stub_cli(tmp_path, monkeypatch):
    """CLI e2e 的知识写回指向 tmp——包内 demo 资产是只读的（红线 #11）。"""
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    (kb_root / "pitfalls.json").write_text(json.dumps({"pitfalls": []}), encoding="utf-8")
    (kb_root / "components.json").write_text(json.dumps({"components": {}}), encoding="utf-8")
    kb = KnowledgeBase(kb_root)
    monkeypatch.setattr("atlas.cli.resolve_plugin", lambda: _StubPlugin(kb))
    return kb


def _seed_unverified(tmp_path):
    """塞一条**没有** evidence.verification 的 pitfall 草稿（存量 pending 的真实形态）。"""
    journal = ExperienceJournal(tmp_path / "reports")
    journal.append_draft(journal.draft(task="click_save", exc=TimeoutError("save hung"),
                                       pitfall_id="demo-014"))
    return next(r["id"] for r in ReviewQueue(tmp_path / "reports").pending()
                if r["kind"] == "pitfall")


def test_parser_accepts_allow_unverified_flag(tmp_path):
    """flag 必须真的存在——漏接线时 argparse 直接 exit 2，这条测先于行为红。"""
    from atlas.cli import _parser
    args = _parser().parse_args(["review", "--approve", "abc", "--allow-unverified",
                                 "--reason", "依据"])
    assert args.allow_unverified is True and args.reason == "依据"


def test_parser_accepts_require_evidence_flag(tmp_path):
    """C2 松绑：`--require-evidence` 显式 opt-in 开关必须真存在且默认关。"""
    from atlas.cli import _parser
    default = _parser().parse_args(["review", "--approve", "abc"])
    assert default.require_evidence is False          # 默认不强制（松绑后的默认）
    on = _parser().parse_args(["review", "--approve", "abc", "--require-evidence"])
    assert on.require_evidence is True


def test_cli_blocks_unverified_draft_with_guidance(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    draft_id = _seed_unverified(tmp_path)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", draft_id,
               "--remedy", "点保存前先等 .app-main 出现", "--require-evidence"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--allow-unverified" in err and "--reason" in err, "拦截必须指路，不是死锁"
    assert ReviewQueue(tmp_path / "reports").stats()["pending"] == 1


def test_cli_default_relaxes_evidence_gate(tmp_path, monkeypatch, capsys):
    """松绑后的默认：不带 --require-evidence 时，缺三元组的草稿**照常批准**（rc 0）。
    这是有意的行为变更——想要旧的硬保证必须显式 `--require-evidence`。"""
    from atlas.cli import main
    draft_id = _seed_unverified(tmp_path)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", draft_id,
               "--remedy", "点保存前先等 .app-main 出现"])
    assert rc == 0, capsys.readouterr().err
    assert ReviewQueue(tmp_path / "reports").stats()["approved"] == 1
    decisions = (tmp_path / "reports" / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert "unverified_override" not in decisions, "默认放行不算 override，不该打豁免标"


def test_cli_override_approves_and_marks_both_records(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    draft_id = _seed_unverified(tmp_path)
    kb = _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", draft_id,
               "--remedy", "点保存前先等 .app-main 出现", "--require-evidence",
               "--allow-unverified", "--reason", "真机已复现，三元组在工单附件里"])
    assert rc == 0, "override 是人的显式决定，必须可用（gap6 §7 拍板：不死锁）"
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "pitfall"
    saved = json.loads((kb.root / "pitfalls.json").read_text(encoding="utf-8"))["pitfalls"]
    assert saved and saved[0]["status"] == "approved"
    row = next(r for r in ReviewQueue(tmp_path / "reports").rows() if r["id"] == draft_id)
    assert row["status"] == "approved" and row.get("approved_despite_unverified") is True
    decisions = (tmp_path / "reports" / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert '"unverified_override": true' in decisions
    assert "真机已复现" in decisions, "豁免理由必须进留痕，否则事后无从追责"


def test_cli_override_without_reason_is_rc2(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    draft_id = _seed_unverified(tmp_path)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", draft_id,
               "--remedy", "x", "--require-evidence", "--allow-unverified"])
    assert rc == 2 and "必须留原因" in capsys.readouterr().err
    assert ReviewQueue(tmp_path / "reports").stats()["pending"] == 1, "半批准状态不得存在"


def test_batch_apply_channel_has_no_override_route(tmp_path, monkeypatch, capsys):
    """批量通道有意不给豁免：决策模板里没有 --allow-unverified 的透传位（design_c2 §7），
    且批量同受 `--require-evidence` 约束——砸进决策行的 allow_unverified 字段不被透传。"""
    from atlas.cli import main
    draft_id = _seed_unverified(tmp_path)
    _stub_cli(tmp_path, monkeypatch)
    decision = tmp_path / "decision.jsonl"
    decision.write_text(json.dumps({"id": draft_id, "remedy": "等 .app-main",
                                   "allow_unverified": True,
                                   "override_reason": "想旁路"}) + "\n", encoding="utf-8")
    rc = main(["--root", str(tmp_path), "review", "--apply", str(decision), "--require-evidence"])
    assert rc == 2
    captured = capsys.readouterr()
    assert draft_id[:6] in captured.out + captured.err, "失败行必须点名，不是静默跳过"
    assert ReviewQueue(tmp_path / "reports").stats()["pending"] == 1, "批量通道放行 unverified=没闸"


# ── 补丁 1（engine 侧自动三元组）：必须喂得动 review 的晋级闸 ────────────

def _engine_failure_draft(tmp_path):
    from atlas import Actor
    from atlas.config import AtlasConfig
    from atlas.core import Channel, TaskMetadata
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder

    class BoomTask:
        name = "query_x"
        metadata = TaskMetadata("demo", "query_x", produces=("v",),
                                channel=Channel.API, read_only=True)

        def perform(self, actor):
            raise TimeoutError("element not visible")

    config = AtlasConfig(tmp_path, mode="run")
    recorder = RunRecorder("c2-case", config.env, config.reports_dir / "runs.jsonl", "run")
    journal = ExperienceJournal(config.reports_dir)
    engine = ExecutionEngine(config, recorder=recorder, journal=journal,
                             case_id="c2-case")
    with pytest.raises(TimeoutError):
        engine.execute(Actor(), BoomTask())
    recorder.close("failed")
    rows = [json.loads(l) for l in
            (config.reports_dir / "experience_drafts.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    return config, rows[-1]


def test_engine_failure_draft_carries_complete_verification(tmp_path):
    """engine 自动三元组的形态必须完备（四键、类型对、verdict=fail）——缺键等于白给。"""
    config, row = _engine_failure_draft(tmp_path)
    verification = row["evidence"]["verification"]
    assert verification_problems(verification) == [], verification
    assert verification["command"] == f"atlas run c2-case --env {config.env}"
    assert verification["exit_code"] == 1 and verification["verdict"] == "fail"
    assert isinstance(verification["assertions"], int) and not isinstance(
        verification["assertions"], bool)


def test_engine_sourced_draft_is_approvable_without_override(tmp_path, monkeypatch, capsys):
    """评审 §4.3「不死锁」判定的前提：engine 来源草稿经补丁 1 自带可批三元组。

    反过来说，如果补丁 1 的形态喂不进闸，engine 生成的每一条坑点都只能靠 override 批准——
    那是把「机器产的证据」永久降级成人工豁免，正是要防的形态。
    """
    from atlas.cli import main

    _config, row = _engine_failure_draft(tmp_path)
    _stub_cli(tmp_path, monkeypatch)
    rc = main(["--root", str(tmp_path), "review", "--approve", row["id"][:8],
               "--remedy", "点保存前先等 .app-main 出现"])
    assert rc == 0, capsys.readouterr().err
    stats = ReviewQueue(tmp_path / "reports").stats()
    assert stats["approved"] == 1 and stats["pending"] == 0
    decisions = (tmp_path / "reports" / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert "unverified_override" not in decisions, "自动三元组就该正常晋级，不记豁免"


def test_assertions_count_uses_disk_verified_op_shape(tmp_path):
    """C2-B 核盘结论：recorder 的 op 字面值是 channel（api/ui/auto/hybrid），**不存在
    `op=="assert"`**——所以计数按「assert 块执行的 Task 名」口径；字符串表达式断言不落 step，
    统计不到就如实 0（0 是 GREEN 反模式的可见信号，不编数）。"""
    from atlas import Actor
    from atlas.config import AtlasConfig
    from atlas.core import Channel, FunctionalTask, TaskMetadata
    from atlas.engine import ExecutionEngine
    from atlas.recorder import RunRecorder

    config = AtlasConfig(tmp_path, mode="run")
    recorder = RunRecorder("c2-count", config.env, config.reports_dir / "runs.jsonl", "run")
    engine = ExecutionEngine(config, recorder=recorder)
    actor = Actor()
    for name in ("assert_api", "assert_pt_echo", "open_doc_list"):
        task = FunctionalTask(TaskMetadata("demo", name, produces=("v",),
                                           channel=Channel.API, read_only=True),
                              lambda a: {"v": 1}, name)
        engine.execute(actor, task)
    ops = {step["op"] for step in recorder.steps}
    assert "assert" not in ops and ops == {"api"}, f"实盘 op 形态：{ops}"
    assert engine._verification()["verification"]["assertions"] == 2

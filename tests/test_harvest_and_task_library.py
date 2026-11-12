"""落库改进线：跑成功→自动固化通路 + 公共任务库。

两个不足的补齐面（2026-11-06）：
1. 失败草稿通路早已存在（engine.execute 失败路径 journal.draft），跑绿后结果就散了——
   flow 级 harvest:true（或 CLI run --harvest）跑绿后由 dsl._harvest_passed_flow 把
   动作序列 + C2 成功三元组固化为 flow_candidate pending_review 草稿进审核塔；
2. 跨流程可复用的动作序列没有共享存储——批准 flow_candidate 写私有 task_library.json
   （KnowledgeBase.save_task_entry，按名唯一），读侧 atlas library 只读查询。

红线对齐：只进塔不写知识（#9/#10）；三元组闸对 flow_candidate 同样生效、verdict 由
真实终态派生（#7 双向：没绿不许装绿，绿了也不许装红）；固化失败绝不翻掉已通过的 run。
"""
import json

import pytest

from atlas import Actor, FunctionalTask, TaskMetadata
from atlas.config import AtlasConfig
from atlas.dsl import FlowCompiler, TaskRegistry
from atlas.engine import ExecutionEngine
from atlas.experience import ExperienceJournal
from atlas.knowledge import KnowledgeBase
from atlas.recorder import RunRecorder
from atlas.review import (ReviewQueue, evidence_card, grouped_view,
                          verification_problems)


def _registry():
    registry = TaskRegistry()
    registry.register("set_val", lambda: FunctionalTask(
        TaskMetadata("demo", "set", produces=("v",)), lambda a: {"v": 1}, "set_val"))
    registry.register("boom", lambda: FunctionalTask(
        TaskMetadata("demo", "boom"), lambda a: (_ for _ in ()).throw(TimeoutError("x")),
        "boom"))
    return registry


def _flow(tmp_path, **extra):
    flow = {"id": "tc_harvest", "engine": AtlasConfig(tmp_path),
            "steps": [{"task": "set_val", "channel": "auto"}],
            "assert": ["v == 1"]}
    flow.update(extra)
    return flow


def _run(tmp_path, flow):
    actor = Actor()
    actor.journal = ExperienceJournal(tmp_path / "reports")
    return FlowCompiler(_registry()).run(flow, actor), actor


# ── 通路 1：跑成功 → flow_candidate 草稿 ─────────────────────────────────────

def test_passed_flow_harvests_flow_candidate_draft(tmp_path):
    result, _ = _run(tmp_path, _flow(tmp_path, harvest=True))
    assert result["status"] == "passed"
    rows = ReviewQueue(tmp_path / "reports").pending("flow_candidate")
    assert len(rows) == 1
    row = rows[0]
    assert row["task"] == "tc_harvest" and row["status"] == "pending_review"
    assert row["failure_class"] == "success" and row["kind"] == "flow_candidate"
    ev = row["evidence"]
    # C2 成功三元组：真实终态派生，必须能过晋级闸
    assert verification_problems(ev["verification"]) == []
    assert ev["verification"]["verdict"] == "pass"
    assert ev["verification"]["exit_code"] == 0
    # 动作序列原文：DSL 步骤与 assert 原样（${var} 占位不改写），不是 runtime 步骤名
    assert ev["steps"] == [{"task": "set_val", "channel": "auto"}]
    assert ev["assert"] == ["v == 1"]
    assert ev["env"] == AtlasConfig(tmp_path).env and ev["run_id"]


def test_harvest_rerun_merges_seen_and_refreshes_evidence(tmp_path):
    flow = _flow(tmp_path, harvest=True)
    _run(tmp_path, flow)
    _run(tmp_path, flow)
    rows = ReviewQueue(tmp_path / "reports").pending("flow_candidate")
    assert len(rows) == 1, "同一份 flow 重跑只增 seen，不堆重复审核负担"
    assert rows[0]["seen"] == 2


def test_changed_flow_gets_new_draft_identity(tmp_path):
    _run(tmp_path, _flow(tmp_path, harvest=True))
    changed = _flow(tmp_path, harvest=True)
    changed["steps"] = [{"task": "set_val", "channel": "api"}]
    _run(tmp_path, changed)
    rows = ReviewQueue(tmp_path / "reports").pending("flow_candidate")
    assert len(rows) == 2, "flow 内容变了=新资产=新 id 新行（旧草稿不被冒充）"
    assert len({r["id"] for r in rows}) == 2


def test_failed_run_does_not_harvest(tmp_path):
    flow = {"id": "tc_boom", "harvest": True, "engine": AtlasConfig(tmp_path),
            "steps": [{"task": "set_val"}, {"task": "boom"}]}
    with pytest.raises(TimeoutError):
        _run(tmp_path, flow)
    assert ReviewQueue(tmp_path / "reports").pending("flow_candidate") == []
    # assert 失败同样不固化：assert 在终态判定之内
    flow2 = {"id": "tc_assert", "harvest": True, "engine": AtlasConfig(tmp_path),
             "steps": [{"task": "set_val"}], "assert": ["v == 999"]}
    with pytest.raises(AssertionError):
        _run(tmp_path, flow2)
    assert ReviewQueue(tmp_path / "reports").pending("flow_candidate") == []


def test_harvest_defaults_off(tmp_path):
    result, _ = _run(tmp_path, _flow(tmp_path))
    assert result["status"] == "passed"
    assert ReviewQueue(tmp_path / "reports").rows() == [], "未声明 harvest 零涟漪"


def test_harvest_write_failure_never_flips_passed_run(tmp_path, capsys):
    class BoomJournal:
        def draft_flow(self, **kwargs):
            return {"id": "x", "status": "pending_review"}

        def append_draft(self, row):
            raise OSError("disk full")

    actor = Actor()
    actor.journal = BoomJournal()
    result = FlowCompiler(_registry()).run(_flow(tmp_path, harvest=True), actor)
    assert result["status"] == "passed", "固化尽力而为：绝不把已通过的 run 翻成失败"
    assert "WARN" in capsys.readouterr().err, "不静默：WARN 留痕"


def test_harvest_without_journal_skips_silently(tmp_path):
    actor = Actor()   # 纯 DSL 装配没接落盘通道
    result = FlowCompiler(_registry()).run(_flow(tmp_path, harvest=True), actor)
    assert result["status"] == "passed"
    assert not (tmp_path / "reports" / "experience_drafts.jsonl").exists()


def test_compile_rejects_non_bool_harvest():
    with pytest.raises(ValueError, match="harvest"):
        FlowCompiler(TaskRegistry()).compile({"steps": [], "harvest": "yes"})
    FlowCompiler(TaskRegistry()).compile({"steps": [], "harvest": True})   # 合法值放行


def test_engine_verification_passed_shape(tmp_path):
    """成功三元组与失败三元组同一把刀（engine._verification）：verdict/exit_code 由
    真实终态派生，绝不反着写（红线 #7 双向）。"""
    config = AtlasConfig(tmp_path, mode="run")
    recorder = RunRecorder("harvest-case", config.env, tmp_path / "runs.jsonl", "run")
    engine = ExecutionEngine(config, recorder=recorder, case_id="harvest-case")
    ok = engine._verification(True)["verification"]
    bad = engine._verification(False)["verification"]
    assert ok["verdict"] == "pass" and ok["exit_code"] == 0
    assert bad["verdict"] == "fail" and bad["exit_code"] == 1
    assert ok["command"] == bad["command"] == f"atlas run harvest-case --env {config.env}"


# ── 通路 2：flow_candidate 批准 → 公共任务库 ────────────────────────────────

def _seed_flow_candidate(tmp_path, *, case="tc_harvest", verified=True, steps=None,
                         digest_md5="abc123"):
    journal = ExperienceJournal(tmp_path / "reports")
    evidence = {"steps": steps if steps is not None else [{"task": "set_val", "channel": "auto"}],
                "assert": ["v == 1"], "run_id": "r-123", "env": "sit"}
    if verified:
        evidence["verification"] = {"command": f"atlas run {case} --env sit",
                                    "exit_code": 0, "assertions": 1, "verdict": "pass"}
    journal.append_draft(journal.draft_flow(
        case=case, digest={"md5": digest_md5, "kind": "derived"}, evidence=evidence))
    queue = ReviewQueue(tmp_path / "reports")
    return queue, queue.pending("flow_candidate")[0]["id"]


def test_approve_flow_candidate_writes_task_library(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")
    queue, draft_id = _seed_flow_candidate(tmp_path)
    record = queue.approve(kb, draft_id, remedy="")
    assert record["name"] == "tc_harvest" and record["status"] == "approved"
    assert record["overwritten"] is False
    saved = json.loads((tmp_path / "kb" / "task_library.json").read_text(encoding="utf-8"))
    assert len(saved["tasks"]) == 1
    entry = saved["tasks"][0]
    assert entry["name"] == "tc_harvest"
    assert entry["steps"] == [{"task": "set_val", "channel": "auto"}]
    assert entry["assert"] == ["v == 1"] and entry["source_run"] == "r-123"
    assert entry["status"] == "approved"
    # 塔翻 approved + 决策留痕
    assert queue.pending("flow_candidate") == [] and queue.stats()["approved"] == 1
    decisions = (tmp_path / "reports" / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert "approve_flow" in decisions and "tc_harvest" in decisions


def test_approve_with_remedy_renames_library_entry(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")
    queue, draft_id = _seed_flow_candidate(tmp_path)
    record = queue.approve(kb, draft_id, remedy="开票动作序列")
    assert record["name"] == "开票动作序列"
    saved = json.loads((tmp_path / "kb" / "task_library.json").read_text(encoding="utf-8"))
    assert saved["tasks"][0]["name"] == "开票动作序列"


def test_reapprove_same_name_overwrites_single_entry(tmp_path):
    """库按名字唯一：同名再批准是更新不是堆叠（组件选择器同纪律）。"""
    kb = KnowledgeBase(tmp_path / "kb")
    queue, first = _seed_flow_candidate(tmp_path)
    queue.approve(kb, first, remedy="")
    journal = ExperienceJournal(tmp_path / "reports")
    journal.append_draft(journal.draft_flow(
        case="tc_harvest", digest={"md5": "def456", "kind": "derived"},
        evidence={"steps": [{"task": "new_step"}],
                  "verification": {"command": "c", "exit_code": 0, "assertions": 0,
                                   "verdict": "pass"}}))
    second = queue.pending("flow_candidate")[0]["id"]
    record = queue.approve(kb, second, remedy="")
    assert record["overwritten"] is True
    saved = json.loads((tmp_path / "kb" / "task_library.json").read_text(encoding="utf-8"))
    assert len(saved["tasks"]) == 1
    assert saved["tasks"][0]["steps"] == [{"task": "new_step"}], "新批准的序列胜出"


def test_unverified_flow_candidate_blocked_with_guidance(tmp_path):
    """C2 松绑（本轮有意变更默认）后，旧硬保证搬到**显式开闸**上：`require_evidence=True`
    时缺三元组仍一律拒且消息点名 `--require-evidence`/`--allow-unverified` 指引。"""
    kb = KnowledgeBase(tmp_path / "kb")
    queue, draft_id = _seed_flow_candidate(tmp_path, verified=False)
    with pytest.raises(ValueError) as ei:
        queue.approve(kb, draft_id, remedy="", require_evidence=True)
    msg = str(ei.value)
    assert "--allow-unverified" in msg and "--require-evidence" in msg, "拦批准要给指路，不是死锁"
    assert queue.stats()["pending"] == 1, "拦批准不删草稿：照常列出、可 reject"


def test_unverified_flow_candidate_default_now_approves(tmp_path):
    """反向对照：松绑后的默认（不开闸）同一草稿**能**批准，且**不打** override 标
    ——证明「默认不强制」是真的，不是靠不写标记蒙过去。"""
    kb = KnowledgeBase(tmp_path / "kb")
    queue, draft_id = _seed_flow_candidate(tmp_path, verified=False)
    queue.approve(kb, draft_id, remedy="")                     # 不传 require_evidence
    row = next(r for r in queue.rows() if r["id"] == draft_id)
    assert row["status"] == "approved"
    assert "approved_despite_unverified" not in row            # 默认放行≠override
    decisions = (tmp_path / "reports" / "review_decisions.jsonl").read_text(encoding="utf-8")
    assert "unverified_override" not in decisions


def test_unverified_override_still_requires_reason(tmp_path):
    """开闸（require_evidence=True）时 override 仍必须留原因——旧闸语义逐字保留。"""
    kb = KnowledgeBase(tmp_path / "kb")
    queue, draft_id = _seed_flow_candidate(tmp_path, verified=False)
    with pytest.raises(ValueError, match="原因"):
        queue.approve(kb, draft_id, remedy="", allow_unverified=True, require_evidence=True)


def test_readonly_knowledge_rejects_task_library_write(tmp_path):
    """#135 同闸：未设 ATLAS_KNOWLEDGE_DIR（root=None）时批准不得落包内资产。"""
    queue, draft_id = _seed_flow_candidate(tmp_path)
    with pytest.raises(PermissionError):
        queue.approve(KnowledgeBase(None), draft_id, remedy="")


def test_save_task_entry_validates_shape(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")
    with pytest.raises(PermissionError):
        kb.save_task_entry({"name": "x", "steps": [{"task": "y"}]})
    for bad in ({}, {"name": "", "steps": [{"task": "y"}]},
                {"name": "x"}, {"name": "x", "steps": []},
                {"name": "x", "steps": ["not-a-dict"]}):
        with pytest.raises(ValueError):
            kb.save_task_entry(bad, approved=True)


def test_save_task_entry_tolerates_corrupted_existing_file(tmp_path):
    """#127 面4/面5 同族：既有 tasks 键值坏形态当空库重建，批准必须能走通——
    半路崩=塔清不动（批准是唯一写路径）。"""
    root = tmp_path / "kb"
    root.mkdir()
    (root / "task_library.json").write_text('{"tasks": {"a": 1}}', encoding="utf-8")
    kb = KnowledgeBase(root)
    kb.save_task_entry({"name": "tc", "steps": [{"task": "x"}]}, approved=True)
    data = json.loads((root / "task_library.json").read_text(encoding="utf-8"))
    assert [t["name"] for t in data["tasks"]] == ["tc"]


# ── 塔面：证据卡 / 聚类 / 模板 ───────────────────────────────────────────────

def test_evidence_card_is_cp936_safe_and_shows_recipe(tmp_path):
    queue, draft_id = _seed_flow_candidate(tmp_path)
    row = queue.resolve(draft_id)
    card = evidence_card(row)
    card.encode("cp936")   # GBK 控制台红线（X4 同款防线）
    assert "[VERIFIED]" in card and "flow_candidate" in card
    assert "--approve" in card and "task_library" in card


def test_grouped_view_clusters_flow_candidates_by_case(tmp_path):
    _seed_flow_candidate(tmp_path)
    _seed_flow_candidate(tmp_path, case="tc_other", digest_md5="def456")
    view = grouped_view(ReviewQueue(tmp_path / "reports").pending("flow_candidate"))
    # 聚类按证据源（flow_candidate 无快照/URL → 回落 case 名）：不同 case 各成堆
    assert "flow_candidate ×1" in view
    assert "tc_harvest" in view and "tc_other" in view
    assert "--kind flow_candidate" in view, "批量指引点名本类"


def test_template_rows_cover_flow_candidate(tmp_path):
    queue, draft_id = _seed_flow_candidate(tmp_path)
    rows = queue.template_rows("flow_candidate")
    assert rows == [{"id": draft_id, "task": "tc_harvest", "kind": "flow_candidate",
                     "remedy": ""}]
    # 人不改直接 --apply：remedy 空 → 库名回落 case 名，逐条批准留痕（不是自动批准——
    # --apply 本身是人的显式动作）
    result = queue.apply(KnowledgeBase(tmp_path / "kb"), rows)
    assert result["applied"] == 1 and result["failed"] == []


# ── CLI 面 ──────────────────────────────────────────────────────────────────

class _StubPlugin:
    def __init__(self, kb):
        self._kb = kb

    def knowledge(self):
        return self._kb


def test_parser_accepts_new_surface():
    from atlas.cli import _parser
    args = _parser().parse_args(["run", "f.yaml", "--harvest"])
    assert args.harvest is True
    args = _parser().parse_args(["review", "--reject-kind", "flow_candidate", "--reason", "x"])
    assert args.reject_kind == "flow_candidate"
    args = _parser().parse_args(["review", "--kind", "flow_candidate"])
    assert args.kind == "flow_candidate"
    args = _parser().parse_args(["library"])
    assert args.name == ""
    args = _parser().parse_args(["library", "tc_a"])
    assert args.name == "tc_a"


def _stub_cli(tmp_path, monkeypatch, kb_root=None):
    kb_root = kb_root or (tmp_path / "kb")
    kb_root.mkdir(exist_ok=True)
    kb = KnowledgeBase(kb_root)
    monkeypatch.setattr("atlas.cli.resolve_plugin", lambda: _StubPlugin(kb))
    return kb


def test_cli_library_lists_shows_and_names_unknown(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    (kb_root / "task_library.json").write_text(json.dumps(
        {"tasks": [{"name": "tc_a", "steps": [{"task": "x"}], "source": "review",
                    "created_at": "t"}]}, ensure_ascii=False), encoding="utf-8")
    _stub_cli(tmp_path, monkeypatch, kb_root)
    assert main(["--root", str(tmp_path), "library"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1 and out["tasks"][0]["name"] == "tc_a"
    assert main(["--root", str(tmp_path), "library", "tc_a"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "tc_a"
    assert main(["--root", str(tmp_path), "library", "nope"]) == 2
    err = capsys.readouterr().err
    assert "空库" not in err and "tc_a" in err, "点名现有条目，不是含糊失败"


def test_cli_library_empty_store_reports_zero(tmp_path, monkeypatch, capsys):
    from atlas.cli import main
    _stub_cli(tmp_path, monkeypatch)
    assert main(["--root", str(tmp_path), "library"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0, "空库如实报 0，不伪造条目"


def test_cli_end_to_end_harvest_draft_to_library(tmp_path, monkeypatch, capsys):
    """全链路：跑绿固化的草稿 → CLI 批准 → CLI 查库。"""
    from atlas.cli import main
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    _stub_cli(tmp_path, monkeypatch, kb_root)
    _queue, draft_id = _seed_flow_candidate(tmp_path)
    assert main(["--root", str(tmp_path), "review", "--approve", draft_id[:8]]) == 0
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "library", "tc_harvest"]) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["steps"] == [{"task": "set_val", "channel": "auto"}]

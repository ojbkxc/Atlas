"""C5 边界账本离线回归（design_c5 §6 测试清单）。

三组事实分别钉死：
- 账本 schema/裁决判据（validate_ledger/resolve_region 是 #92 唯一实现，doctor 与
  分类 gate 共用）；
- 读侧容错（#112/#131 同纪律：坏字节/坏形态点名不裸崩）；
- doctor 的 rc 口径（存在但坏=漂移 rc1；缺失=如实 present:false **不改 rc**——
  防止把账本做成新全局门）。
"""
import json
from pathlib import Path

import pytest

from atlas import boundaries as B
from atlas.cli import main as cli_main
from atlas.review import ReviewQueue
from scripts.boundary_classify import main as classify_main

ROOT = Path(__file__).resolve().parent.parent


# ── 造账本的测试夹具 ────────────────────────────────────────────────────────────

def _region(rid, rtype="engine-owned", paths=(), **kw):
    reg = {"id": rid, "region": rtype, "paths": list(paths),
           "disposition": kw.pop("disposition", ""), "decision_log": kw.pop("decision_log", [])}
    reg.update(kw)
    return reg


def _ledger(*regions):
    return {"schema_version": 1,
            "risk_model": {"basis": "structural_drift",
                           "thresholds": {"structural_medium": 30, "structural_high": 250}},
            "regions": list(regions)}


def _write_ledger(root: Path, data) -> Path:
    path = B.ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _diff(fname, added, removed=0):
    """造 unified diff 文本段：added 为逐行内容，removed 为行数。"""
    lines = [f"diff --git a/{fname} b/{fname}", "index 111..222 100644",
             f"--- a/{fname}", f"+++ b/{fname}",
             f"@@ -1,{removed} +1,{len(added)} @@"]
    lines += [f"-gone {i}" for i in range(removed)]
    lines += [f"+{a}" for a in added]
    return "\n".join(lines) + "\n"


# ── 账本本体合法（验收「好账本通过」）+ 现状如实归类 ──────────────────────────

def test_real_repo_ledger_is_valid():
    data, problems = B.load_ledger(B.ledger_path(ROOT))
    assert problems == []
    assert len(data.get("regions") or []) >= 11   # 三区都有条目，不是空壳


def test_real_ledger_classifies_key_assets_as_facts_on_disk():
    data, _ = B.load_ledger(B.ledger_path(ROOT))
    expect = {
        "src/atlas/versioning.py": "engine-core",
        "src/atlas/dsl.py": "liteflow-compile-semantics",          # 最长非通配前缀压过 engine-core
        "src/atlas/scheduling/dispatch.py": "scheduling-kernel",   # 主干，不标 optional
        "src/atlas/plugins/demo/legacy_case.py": "legacy-case-bridge",  # 压过 demo 子树
        "src/atlas/plugins/demo/tasks.py": "demo-business-assets",
        "scripts/import_jmeter.py": "jmx-import-semantics",        # 压过 repo-tooling 的 glob
        "flows/iservice/sit_cvt_regression/x.yaml": "private-knowledge",
        "knowledge/ui-auto-test/pitfalls.json": "private-knowledge",
        "docs/gaps/impl_pi/design_c5.md": "markdown-docs",
        "README.md": "markdown-docs",
        "tests/test_boundaries.py": "test-suite",
    }
    for path, region_id in expect.items():
        assert B.resolve_region(data, path) == region_id, path
    assert B.resolve_region(data, "nonexistent/x.txt") is None     # 未登记≠硬塞归类


# ── schema 校验逐条点名（坏账本不崩、不猜） ────────────────────────────────────

def test_bad_region_enum_named():
    problems = B.validate_ledger(_ledger(_region("a", "fork-owned", ["x.py"])))
    assert any("三值枚举" in p for p in problems)


def test_backslash_and_nonrelative_paths_named():
    bs = chr(92)
    problems = B.validate_ledger(_ledger(_region(
        "a", paths=[f"src{bs}atlas{bs}x.py", "/abs/x.py", "../escape/x.py", "C:/x.py"])))
    joined = " ".join(problems)
    assert "反斜杠" in joined and "仓库相对" in joined and '".." 逃逸' in joined


def test_accepted_without_debt_price_is_illegal():
    problems = B.validate_ledger(_ledger(
        _region("a", "absorbed", ["x.py"], disposition="accepted")))
    assert any("decision_log 为空" in p for p in problems)
    ok = _region("a", "absorbed", ["x.py"], disposition="accepted",
                 decision_log=[{"date": "2026-11-04", "decision": "accept-debt",
                                "rationale": "消除=重写执行面，暂付代价", "by": "case-porter"}])
    assert B.validate_ledger(_ledger(ok)) == []


def test_decision_log_field_and_date_checks():
    bad = _region("a", "absorbed", ["x.py"], disposition="accepted", decision_log=[
        {"date": "2026-13-40", "decision": "retain", "rationale": "r"}])   # 缺 by + 坏日期
    problems = B.validate_ledger(_ledger(bad))
    assert any("缺 by" in p for p in problems)
    assert any("YYYY-MM-DD" in p for p in problems)


def test_schema_version_and_risk_model_named():
    data = _ledger(_region("a", paths=["x.py"]))
    data["schema_version"] = 2
    assert any("schema_version=2" in p for p in B.validate_ledger(data))
    data = _ledger(_region("a", paths=["x.py"]))
    data["schema_version"] = True
    assert any("必须是整数" in p for p in B.validate_ledger(data))
    data = _ledger(_region("a", paths=["x.py"]))
    data["risk_model"]["thresholds"] = {"structural_medium": 250, "structural_high": 30}
    assert any("medium" in p for p in B.validate_ledger(data))


def test_same_specificity_overlap_across_regions_is_illegal():
    data = _ledger(_region("g1", "engine-owned", ["src/*/*.py"]),
                   _region("g2", "absorbed", ["src/*/test_*.py"]))
    problems = B.validate_ledger(data)
    assert any("重叠冲突" in p for p in problems)
    # 同区认领同一空间无害（裁决结果本来就同一个 region）
    same_region = _ledger(_region("g1", "engine-owned", ["src/*/*.py", "src/atlas/generator/*.py"]))
    assert B.validate_ledger(same_region) == []


def test_subtree_vs_glob_conflict_resolves_by_specificity():
    data = _ledger(_region("dir", "private-knowledge", ["src/atlas/"], optional=True),
                   _region("file", "absorbed", ["src/atlas/x.py"]))
    assert B.validate_ledger(data) == []           # 可重叠但特异度不同 → 合法
    assert B.resolve_region(data, "src/atlas/x.py") == "file"
    assert B.resolve_region(data, "src/atlas/y.py") == "dir"


# ── path_matches：X1 glob 分段语义直测（盲区补齐轮）───────────────────────────
# resolve_region 的间接覆盖只采样了仓库账本里的真实 pattern；分段语义的机器形态
# （* 不跨段 / ** 跨段 / 尾斜杠子树 / ? 单字符 / 反斜杠归一）在此逐条钉死——
# 跨平台条款（两台机器同一答案）靠这些纯字符串断言成立，不触文件系统。

def test_path_matches_literal_and_star_seg_semantics():
    assert B.path_matches("src/atlas/core.py", "src/atlas/core.py")
    assert not B.path_matches("src/atlas/core.py", "src/atlas/dsl.py")
    assert B.path_matches("src/*.py", "src/core.py")
    assert not B.path_matches("src/*.py", "src/generator/x.py"), "* 不跨段"


def test_path_matches_double_star_and_subtree():
    assert B.path_matches("src/**/*.py", "src/generator/emit.py")
    assert B.path_matches("src/**/*.py", "src/core.py"), "** 可匹配零个整段"
    assert B.path_matches("**/*.py", "a/b/c.py")
    assert B.path_matches("flows/iservice/", "flows/iservice/x.yaml"), "尾斜杠=子树"


def test_path_matches_question_mark_and_backslash_normalization():
    assert B.path_matches("src/?.py", "src/a.py")
    assert not B.path_matches("src/?.py", "src/ab.py")
    # X1：Windows 形态反斜杠路径先归一再匹配（同一 pattern 两台机器同一答案）
    bs = chr(92)
    assert B.path_matches("src/atlas/*.py", f"src{bs}atlas{bs}core.py")


def test_path_matches_empty_shapes():
    assert B.path_matches("", "")
    assert not B.path_matches("", "x.py")
    assert not B.path_matches("x.py", "")


# ── region_by_id：按 id 查区域直测（盲区补齐轮）─────────────────────────────

def test_region_by_id_lookup_returns_entry_and_miss_is_none():
    data = _ledger(_region("engine-core", paths=["src/atlas/*.py"]),
                   _region("private", "private-knowledge", ["flows/iservice/"], optional=True))
    reg = B.region_by_id(data, "private")
    assert reg is data["regions"][1], "返回 region dict 本体（调用方读 disposition/decision_log）"
    assert B.region_by_id(data, "no-such-region") is None


def test_region_by_id_bad_inputs_never_crash():
    data = _ledger(_region("engine-core", paths=["src/atlas/*.py"]))
    assert B.region_by_id(data, None) is None
    assert B.region_by_id(data, "") is None
    assert B.region_by_id({}, "engine-core") is None, "无 regions 键"
    # 非映射条目跳过、不裸崩
    weird = {"regions": [None, "x", {"id": "r1"}, {"id": None}]}
    assert B.region_by_id(weird, "r1") == {"id": "r1"}
    assert B.region_by_id(weird, None) is None


# ── 读侧容错（#112/#131 同纪律） ───────────────────────────────────────────────

def test_load_missing_ledger_names_it_without_crash(tmp_path):
    data, problems = B.load_ledger(B.ledger_path(tmp_path))
    assert data == {} and any("账本文件缺失" in p for p in problems)


def test_load_broken_bytes_and_shape_names_without_crash(tmp_path):
    path = B.ledger_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"schema_version": 1, "regions": [{"id": "a\x22, \xe4\xbd')  # 腰斩多字节
    data, problems = B.load_ledger(path)
    assert data == {} and problems          # 点名，不抛 UnicodeDecodeError
    path.write_text("[1, 2]", encoding="utf-8")
    data, problems = B.load_ledger(path)
    assert data == {} and any("顶层必须是映射" in p for p in problems)


# ── classify：变更集分级（style/阈值/搬移/未登记） ─────────────────────────────

LEDGER = _ledger(
    _region("engine-core", "engine-owned", ["src/atlas/*.py"]),
    _region("absorbed-bridge", "absorbed", ["src/atlas/plugins/demo/legacy_case.py"],
            disposition="accepted",
            decision_log=[{"date": "2026-11-04", "decision": "accept-debt",
                           "rationale": "重写执行面暂付代价", "by": "case-porter"}]),
    _region("private", "private-knowledge", ["flows/iservice/"], optional=True),
    _region("docs", "engine-owned", ["docs/**/*.md"], style_paths=True),
)


def test_style_paths_region_is_always_cosmetic():
    files = B.parse_unified_diff(_diff("docs/guide.md", [f"doc line {i}" for i in range(400)]))
    items = B.classify_change(LEDGER, files)
    assert items[0]["class"] == "cosmetic" and items[0]["verdict"] == "AUTO"
    assert items[0]["reason"] == "style_paths"          # 大 diff 也不误报 structural


@pytest.mark.parametrize("n_add,rm,expect_cls,expect_risk", [
    (29, 0, "cosmetic", "low"),
    (30, 0, "structural", "medium"),
    (249, 0, "structural", "medium"),
    (250, 0, "structural", "high"),
])
def test_churn_thresholds_come_from_ledger(n_add, rm, expect_cls, expect_risk):
    files = B.parse_unified_diff(_diff("src/atlas/engine.py", [f"l{i}" for i in range(n_add)], rm))
    item = B.classify_change(LEDGER, files)[0]
    assert item["class"] == expect_cls and item["risk"] == expect_risk
    assert item["churn"] == n_add + rm
    if expect_cls == "structural":
        assert item["verdict"] == "REVIEW-TOWER"        # engine-owned 命中结构漂移必进塔


def test_cross_file_move_detected_as_structural():
    payload = [f"moved statement {i}" for i in range(40)]
    # 手写：a 删 40 行，b 加同一批 40 行 —— Jaccard=1
    a = "diff --git a/src/atlas/a.py b/src/atlas/a.py\n--- a/src/atlas/a.py\n+++ b/src/atlas/a.py\n"
    a += "".join(f"-{line}\n" for line in payload)
    b = "diff --git a/src/atlas/b.py b/src/atlas/b.py\n--- a/src/atlas/b.py\n+++ b/src/atlas/b.py\n"
    b += "".join(f"+{line}\n" for line in payload)
    items = {i["file"]: i for i in B.classify_change(LEDGER, B.parse_unified_diff(a + b))}
    assert items["src/atlas/a.py"]["reason"] == items["src/atlas/b.py"]["reason"] == "cross_file_move"
    assert items["src/atlas/b.py"]["class"] == "structural"     # 40 行 < medium 阈值也判 structural
    assert items["src/atlas/b.py"]["verdict"] == "REVIEW-TOWER"


def test_rename_marks_moved_and_keeps_style_override():
    text = ("diff --git a/src/atlas/old.py b/src/atlas/new.py\n"
            "similarity index 100%\nrename from src/atlas/old.py\nrename to src/atlas/new.py\n"
            "diff --git a/docs/a.md b/docs/b.md\nrename from docs/a.md\nrename to docs/b.md\n")
    items = {i["file"]: i for i in B.classify_change(LEDGER, B.parse_unified_diff(text))}
    assert items["src/atlas/new.py"]["class"] == "structural"   # 纯改名也是跨文件搬移
    assert items["docs/b.md"]["class"] == "cosmetic"            # style_paths 优先级更高


def test_private_knowledge_structural_does_not_enter_tower():
    files = B.parse_unified_diff(_diff("flows/iservice/flow.yaml", [f"y{i}" for i in range(40)]))
    item = B.classify_change(LEDGER, files)[0]
    assert item["class"] == "structural" and item["verdict"] == "DATA-REGION"


def test_unregistered_path_gives_hint():
    files = B.parse_unified_diff(_diff("tools/newborn.py", [f"z{i}" for i in range(40)]))
    item = B.classify_change(LEDGER, files)[0]
    assert item["region"] is None and item["verdict"] == "HINT"


def test_verdict_output_carries_decision_log_tail():
    files = B.parse_unified_diff(_diff("src/atlas/plugins/demo/legacy_case.py", [f"q{i}" for i in range(31)]))
    item = B.classify_change(LEDGER, files)[0]
    assert item["disposition_hint"] == "accepted"
    assert item["decision_log_tail"]["by"] == "case-porter"     # 重议防御的可见性


def test_parse_diff_strips_ab_prefix_and_devnull():
    text = ("diff --git a/x/y.py b/x/y.py\n--- a/x/y.py\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-deleted\n"
            "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+made\n")
    files = B.parse_unified_diff(text)
    assert files["x/y.py"]["removed"] == ["deleted"] and not files["x/y.py"]["added"]
    assert files["new.py"]["added"] == ["made"]


# ── 分类 gate 的 rc 口径 + 进塔联动（复用既有塔，approve 点名拒绝） ────────────

def _repo_with_ledger(tmp_path):
    _write_ledger(tmp_path, LEDGER)
    return tmp_path


def test_classify_main_rcs(tmp_path, capsys):
    root = _repo_with_ledger(tmp_path)
    d = tmp_path / "clean.diff"
    d.write_text(_diff("docs/x.md", ["small change"]), encoding="utf-8")
    assert classify_main(["--diff", str(d), "--root", str(root)]) == 0   # 无 structural
    d.write_text(_diff("src/atlas/engine.py", [f"l{i}" for i in range(40)]), encoding="utf-8")
    assert classify_main(["--diff", str(d), "--root", str(root)]) == 1   # 有 structural
    # diff 文件读不到=用法错 rc2；而「读得进但认不出文件段」是空变更集，rc0 如实汇报
    assert classify_main(["--diff", str(tmp_path / "nope.diff"), "--root", str(root)]) == 2
    d.write_text("not a diff at all\njust prose\n", encoding="utf-8")
    assert classify_main(["--diff", str(d), "--root", str(root)]) == 0


def test_classify_main_refuses_broken_ledger(tmp_path, capsys):
    _write_ledger(tmp_path, '{"schema_version": 999}')
    d = tmp_path / "s.diff"
    d.write_text(_diff("docs/x.md", ["ok"]), encoding="utf-8")
    assert classify_main(["--diff", str(d), "--root", str(tmp_path)]) == 2
    assert "schema_version" in capsys.readouterr().err       # 账本坏必点名，不静默放行


def test_classify_main_to_tower_uses_existing_queue(tmp_path, capsys):
    root = _repo_with_ledger(tmp_path)
    d = tmp_path / "s.diff"
    d.write_text(_diff("src/atlas/plugins/demo/legacy_case.py", [f"p{i}" for i in range(40)]),
                 encoding="utf-8")
    assert classify_main(["--diff", str(d), "--root", str(root), "--to-tower"]) == 1
    queue = ReviewQueue(root / "reports")
    rows = [r for r in queue.rows() if r.get("kind") == "boundary_review"]
    assert len(rows) == 1
    assert rows[0]["status"] == "pending_review"             # 只进 pending，不新增知识写路径
    with pytest.raises(ValueError) as exc:
        queue.approve(object(), rows[0]["id"], remedy="r")
    msg = str(exc.value)
    assert "boundary_review" in msg and "不自动写知识" in msg  # approve 点名拒绝（trace_step/code_review 同族）
    # 三元组位留空=unverified 语义由 kind 拒绝承接，不该漂成「补三元组就能批」
    assert "证据三元组" not in msg


def test_boundary_draft_args_leave_verification_empty():
    item = {"file": "src/atlas/x.py", "region": "engine-core", "class": "structural",
            "risk": "medium", "churn": 40, "reason": "churn_threshold",
            "disposition_hint": "", "decision_log_tail": None, "verdict": "REVIEW-TOWER"}
    kwargs = B.boundary_draft_args(item, "artifacts/x.diff")
    assert kwargs["kind"] == "boundary_review"
    assert "verification" not in kwargs["evidence"]           # 留空 → C2 闸天然自洽


# ── doctor：present/broken 两态与 rc 行为（缺失不改 rc 必须测） ─────────────────

def _ready_root(tmp_path, monkeypatch):
    """给 doctor 造一个其余项全绿的最小仓库（legacy/api 资产齐），rc 只看账本变量。"""
    legacy = tmp_path / "legacy-suite"
    (legacy / "legacy-web" / "lib").mkdir(parents=True)
    (legacy / "legacy-web" / "lib" / "pages.py").write_text("", encoding="utf-8")
    (legacy / "legacy-web" / "pytest.ini").write_text("", encoding="utf-8")
    api = tmp_path / "legacy-api"
    (api / "scripts").mkdir(parents=True)
    (api / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    return tmp_path


def _doctor(tmp_path, capsys):
    rc = cli_main(["--root", str(tmp_path), "doctor"])
    return rc, json.loads(capsys.readouterr().out)


def test_doctor_missing_ledger_honest_and_rc_unchanged(tmp_path, monkeypatch, capsys):
    root = _ready_root(tmp_path, monkeypatch)
    rc, checks = _doctor(root, capsys)
    assert rc == 0                                            # 历史分支/精简克隆不被账本卡死
    assert checks["boundary_ledger"] == {"present": False, "path": B.LEDGER_REL_PATH}
    assert "boundary_assets_consistent" not in checks         # 没核对过就不冒充核对过


def test_doctor_broken_ledger_is_drift_rc1(tmp_path, monkeypatch, capsys):
    root = _ready_root(tmp_path, monkeypatch)
    _write_ledger(root, '{"schema_version": 1, "regions": ')   # 腰斩 JSON
    rc, checks = _doctor(root, capsys)
    assert rc == 1 and checks["boundary_ledger"]["present"] is True
    assert checks["boundary_ledger"]["problems"] and not checks["boundary_assets_consistent"]


def test_doctor_valid_ledger_all_covered_rc0(tmp_path, monkeypatch, capsys):
    root = _ready_root(tmp_path, monkeypatch)
    (root / "src" / "atlas").mkdir(parents=True)
    (root / "src" / "atlas" / "core.py").write_text("", encoding="utf-8")
    (root / "src" / "atlas" / "bridge.py").write_text("", encoding="utf-8")
    ledger = _ledger(_region("engine-core", paths=["src/atlas/*.py"]),
                     _region("private", "private-knowledge", ["flows/iservice/"], optional=True),
                     _region("bridge", "absorbed", ["src/atlas/bridge.py"], disposition="accepted",
                             decision_log=[{"date": "2026-11-04", "decision": "accept-debt",
                                            "rationale": "r", "by": "b"}]))
    _write_ledger(root, ledger)
    rc, checks = _doctor(root, capsys)
    assert rc == 0 and checks["boundary_assets_consistent"] is True
    assert checks["boundary_ledger"]["regions"] == 3
    assert checks["boundary_ledger"]["accepted_debts"][0]["id"] == "bridge"   # 债清单可见
    # optional 全缺 → 只 warn 不 problem 不计 rc（评审 C5-B）
    assert any("optional" in w for w in checks["boundary_ledger"]["warnings"])


def test_doctor_uncovered_required_region_is_inconsistent(tmp_path, monkeypatch, capsys):
    root = _ready_root(tmp_path, monkeypatch)
    _write_ledger(root, _ledger(_region("ghost", paths=["nowhere/*.py"])))
    rc, checks = _doctor(root, capsys)
    assert rc == 1
    assert checks["boundary_ledger"]["uncovered_regions"] == ["ghost"]
    assert not checks["boundary_assets_consistent"]


def test_doctor_on_real_repo_stays_green():
    """仓库自带账本必须当场过一致性——账本自己漂移就红（门禁 772 之后的「账本不回退」）。"""
    data, problems = B.load_ledger(B.ledger_path(ROOT))
    assert problems == []
    warnings, uncovered = B.ledger_disk_coverage(ROOT, data)
    assert uncovered == []

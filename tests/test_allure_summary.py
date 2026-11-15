"""wave4 B 轴 ae-5：emit_allure 结果集人读摘要（atlas-summary.json）。

判据（任务书 ae-5）：
- 摘要 = total/passed/failed/broken/skipped + fail_class 分布，读的是 §3.5 映射后
  **已落盘** result 的状态与 `atlas.fail_class` label——env_blocked/interrupted/waiting
  落 skipped 且在分布里显形，绝不洗绿（红线 #7）。
- 既有结果文件形态逐字节不变：摘要只新增文件，result/attachment 生成路径零改动
  （与 metadata=False 逃生舱的 result 内容逐字段一致，仅 uuid 每份独立之差）。
- 负向对照：坏 runs.jsonl（腰斩坏字节/全残行/空文件）摘要照常生成并如实携带
  skipped_malformed_lines，绝不崩。
- 确定性：无 uuid/时间戳，同输入两次 emit 摘要逐字节相等；clean 随结果文件同生命周期。
"""
import json

from atlas.allure_report import _SUMMARY_FILENAME, emit_allure
from atlas.failures import OPERATIONAL

_RUNNING = "2026-11-15T00:00:00+00:00"
_FINISHED = "2026-11-15T00:00:01+00:00"


def _write_runs(path, runs):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in runs) + "\n",
                    encoding="utf-8")


def _run(run_id, case, status=None, fail_class=None, reason=None):
    run = {"run_id": run_id, "case": case, "env": "sit",
           "started_at": _RUNNING, "finished_at": _FINISHED}
    if status is not None:
        run["status"] = status
    if reason is not None:
        run["reason"] = reason
    steps = []
    if fail_class is not None:
        steps.append({"step": f"{case}_step", "op": "ui", "ok": False,
                      "fail_class": fail_class, "dur_ms": 5})
    run["steps"] = steps
    return run


def _read_summary(out_dir):
    return json.loads((out_dir / _SUMMARY_FILENAME).read_text(encoding="utf-8"))


def _read_results(out_dir):
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(out_dir.glob("atlas-*-result.json"))]


# ---- 摘要计数与分布：8 条混合 run，四态守恒 + 操作族占位显形 --------------------

def test_summary_counts_and_distribution_mixed_runs(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [
        _run("r1", "ok1", status="passed"),
        _run("r2", "ok2", status="passed"),
        _run("r3", "assert_fail", status="failed", fail_class="assertion"),
        _run("r4", "unknown_fail", status="failed"),        # 无 fail_class → broken
        _run("r5", "no_status"),                            # 状态缺失 → broken（不伪装）
        _run("r6", "env_blocked", status="env_blocked", reason="生产环境禁止建单"),
        _run("r7", "interrupted", status="interrupted"),
        _run("r8", "waiting", status="waiting", reason="等审批"),
    ])
    out = tmp_path / "allure-results"
    emitted = emit_allure(runs, out)
    assert emitted["summary_file"] == _SUMMARY_FILENAME
    summary = _read_summary(out)
    assert summary["total"] == 8
    assert summary["passed"] == 2
    assert summary["failed"] == 1
    assert summary["broken"] == 2
    assert summary["skipped"] == 3
    assert summary["fail_class_distribution"] == {
        "assertion": 1, "unknown": 2,
        "env_blocked": 1, "interrupted": 1, "waiting": 1}
    assert summary["skipped_malformed_lines"] == 0
    # 守恒：四态之和 == total（少一块就是丢数据，不是摘要）
    assert summary["passed"] + summary["failed"] + summary["broken"] \
        + summary["skipped"] == summary["total"]
    # 操作/环境族（家族判据唯一来源 failures.OPERATIONAL，不留第二份名单 #92）恰占满
    # skipped 位——「谁占了 skipped」显形，是 §3.5「skipped ⟺ 操作/环境终态」的可执行形态
    dist = summary["fail_class_distribution"]
    operational = {k: v for k, v in dist.items() if k in OPERATIONAL}
    assert sum(operational.values()) == summary["skipped"]


def test_summary_filename_is_atlas_prefixed_not_result_glob(tmp_path):
    """atlas- 前缀 → clean 生命周期；不匹配 *-result.json → 解析侧不会误读。"""
    assert _SUMMARY_FILENAME.startswith("atlas-")
    assert not _SUMMARY_FILENAME.endswith("-result.json")
    assert not _SUMMARY_FILENAME.endswith("-attachment.json")
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [_run("r1", "a", status="passed")])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    assert (out / _SUMMARY_FILENAME).exists()
    result_names = [p.name for p in out.glob("atlas-*-result.json")]
    assert _SUMMARY_FILENAME not in result_names          # 摘要不混进结果命名空间
    assert len(result_names) == 1


# ---- §3.5 沿用：操作/环境终态在摘要里是 skipped，绝不洗绿（红线 #7）------------

def test_operational_statuses_never_washed_into_failed_or_passed(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [
        _run("r1", "blocked", status="env_blocked", reason="缺价格表"),
        _run("r2", "waiting", status="waiting", reason="等人"),
        _run("r3", "interrupted", status="interrupted"),
    ])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    summary = _read_summary(out)
    assert summary["skipped"] == 3
    assert summary["passed"] == 0 and summary["failed"] == 0 and summary["broken"] == 0
    assert summary["fail_class_distribution"] == {
        "env_blocked": 1, "interrupted": 1, "waiting": 1}
    assert "assertion" not in summary["fail_class_distribution"]   # 不冒充缺陷


def test_summary_distribution_reads_result_label_not_step_noise(tmp_path):
    """分布读 result 级 label（映射归因唯一）：failed run 里混一条 env_blocked 步骤不改
    分布——真实断言失败胜出（_run_fail_class 语义在摘要侧镜像），env_blocked 的分布条目
    只来自 run 级映射。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [{
        "run_id": "r1", "case": "mix", "env": "sit",
        "started_at": _RUNNING, "finished_at": _FINISHED, "status": "failed",
        "steps": [{"step": "probe", "op": "ui", "ok": False,
                   "fail_class": "env_blocked", "dur_ms": 5},
                  {"step": "save", "op": "ui", "ok": False,
                   "fail_class": "assertion", "dur_ms": 20}],
    }])
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    summary = _read_summary(out)
    assert summary["failed"] == 1 and summary["skipped"] == 0
    assert summary["fail_class_distribution"] == {"assertion": 1}


# ---- 既有结果文件形态逐字节不变：摘要只新增文件 -------------------------------

def test_result_files_identical_regardless_of_summary(tmp_path):
    """metadata=True（写摘要）与 metadata=False（#115 逃生舱）产出的 result 文件除 uuid
    （每份独立）外逐字段一致——摘要功能不得漏进 result 生成路径。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [
        _run("r1", "ok", status="passed"),
        _run("r2", "boom", status="failed", fail_class="data"),
        _run("r3", "blocked", status="env_blocked", reason="x"),
    ])

    def canonical(out_dir):
        out = []
        for result in _read_results(out_dir):
            result.pop("uuid")        # uuid 每份独立（既有口径）；其余字段必须逐字节一致
            out.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return sorted(out)            # 文件名带随机 uuid → glob 序不稳定，比对须与顺序无关

    out_with = tmp_path / "with-summary"
    out_without = tmp_path / "without-summary"
    emit_allure(runs, out_with, metadata=True)
    emit_allure(runs, out_without, metadata=False)
    assert canonical(out_with) == canonical(out_without)
    assert len(list(out_with.glob("atlas-*-result.json"))) == 3


def test_summary_independent_of_align_escape_hatch(tmp_path):
    """align=False（result.json 逐字旧形态逃生舱）下摘要照常产出且内容一致——摘要读
    状态与 fail_class label，不依赖 align 附加的 label。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [_run("r1", "blocked", status="env_blocked", reason="x"),
                       _run("r2", "boom", status="failed", fail_class="session")])
    out_old = tmp_path / "old-shape"
    out_new = tmp_path / "aligned"
    emit_allure(runs, out_old, align=False)
    emit_allure(runs, out_new, align=True)
    assert _read_summary(out_old) == _read_summary(out_new)
    assert _read_summary(out_old)["skipped"] == 1
    assert _read_summary(out_old)["fail_class_distribution"] == {"session": 1, "env_blocked": 1}


# ---- clean 生命周期：atlas- 前缀 → 陈旧摘要随结果文件一起清 --------------------

def test_clean_regenerates_summary_not_stale(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [_run("r1", "a", status="passed"),
                       _run("r2", "b", status="failed", fail_class="contract")])
    out = tmp_path / "allure-results"
    out.mkdir()
    (out / _SUMMARY_FILENAME).write_text("陈旧垃圾", encoding="utf-8")
    emit_allure(runs, out, clean=True)
    summary = _read_summary(out)
    assert summary["total"] == 2 and summary["failed"] == 1
    # 换一份更小的 runs 重跑：陈旧计数不得残留
    _write_runs(runs, [_run("r3", "c", status="passed")])
    emit_allure(runs, out, clean=True)
    summary = _read_summary(out)
    assert summary["total"] == 1 and summary["failed"] == 0
    assert summary["fail_class_distribution"] == {}


# ---- 负向对照：坏 runs.jsonl 摘要照常生成（不许崩），坏行如实计数 --------------

def test_truncated_tail_still_produces_summary(tmp_path):
    runs = tmp_path / "runs.jsonl"
    good = _run("r1", "ok", status="passed")
    runs.write_bytes(
        json.dumps(good, ensure_ascii=False).encode("utf-8") + b"\n"
        + b'{"run_id": "r2", "case": "\xe4\xbd')      # 腰斩多字节尾行
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    summary = _read_summary(out)
    assert summary["total"] == 1 and summary["passed"] == 1
    assert summary["skipped_malformed_lines"] == 1    # 腰斩行如实入账，不冒充没发生
    # total 只数合法 run，坏行单列——两者之和恰为文件行数（口径不混）
    assert summary["total"] + summary["skipped_malformed_lines"] == 2


def test_all_malformed_lines_still_produce_summary(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("null\n123\n{坏残行\n", encoding="utf-8")
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    summary = _read_summary(out)
    assert summary["total"] == 0
    assert summary["passed"] == 0 and summary["failed"] == 0 \
        and summary["broken"] == 0 and summary["skipped"] == 0
    assert summary["fail_class_distribution"] == {}
    assert summary["skipped_malformed_lines"] == 3


def test_empty_runs_file_still_produces_summary(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("", encoding="utf-8")
    out = tmp_path / "allure-results"
    emit_allure(runs, out)
    summary = _read_summary(out)
    assert summary["total"] == 0 and summary["skipped_malformed_lines"] == 0


# ---- 逃生舱一致：metadata=False 不写摘要 ---------------------------------------

def test_metadata_false_writes_no_summary_file(tmp_path):
    """既有钉死测（test_metadata_false_writes_no_extra_files）docstring「不写任何附加
    文件」必须保持为真——摘要与 categories/environment/executor 同属逃生舱覆盖面。"""
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [_run("r1", "a", status="passed")])
    out = tmp_path / "allure-results"
    emitted = emit_allure(runs, out, metadata=False)
    assert not (out / _SUMMARY_FILENAME).exists()
    assert emitted["summary_file"] is None


# ---- 确定性：无 uuid/时间戳，同输入两次 emit 摘要逐字节相等 ---------------------

def test_summary_deterministic_across_emits(tmp_path):
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [_run("r1", "a", status="failed", fail_class="session"),
                       _run("r2", "b", status="waiting", reason="x")])
    out1 = tmp_path / "o1"
    out2 = tmp_path / "o2"
    emit_allure(runs, out1)
    emit_allure(runs, out2)
    assert (out1 / _SUMMARY_FILENAME).read_bytes() == (out2 / _SUMMARY_FILENAME).read_bytes()


# ---- CLI 接线：atlas allure 全链产出摘要（不只 API 层）-------------------------

def test_cli_allure_produces_summary_file(tmp_path, capsys):
    from atlas.cli import main
    runs = tmp_path / "runs.jsonl"
    _write_runs(runs, [_run("r1", "c", status="env_blocked", reason="生产环境禁止建单")])
    out = tmp_path / "allure-results"
    assert main(["--root", str(tmp_path), "allure", str(runs), "--out", str(out)]) == 0
    capsys.readouterr()
    summary = _read_summary(out)
    assert summary["skipped"] == 1 and summary["passed"] == 0
    assert summary["fail_class_distribution"] == {"env_blocked": 1}

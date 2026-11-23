"""`runs.jsonl` **读侧容错**守卫：任意坏输入都不得让聚合/导出整份失败。

## 为什么钉它（2026-11-23 实测复核）

`summarize` / `export_runlog` / `allure` 三条读侧各自承诺「一条坏行不顶掉整份」，历史上有
#131/#112 两波修过 `UnicodeDecodeError` 裸崩。这轮把**六类坏形态**真跑了一遍，确认承诺成立，
于是把它钉成守卫——**已兑现的承诺也要防回退**（`_read_inputs_text` 若哪天改回严格读，
或某处 `continue` 被写成 `raise`，只有这类测会红）。

坏形态与实测口径：

| 输入 | 语义 |
|---|---|
| 合法 run | 正常计 1 条 |
| 腰斩 JSON（`{"run_id": "r1", "case` ） | 残行，计 `skipped_malformed_lines` |
| 合法 JSON 但非对象（`[1,2,3]`） | 同上 |
| 缺 `run_id` | report 跳过（runlog 需要主键），allure 照常映射 |
| `steps: [null, "x", {...}]` | 坏元素跳过，不顶掉其它元素 |
| `dur_ms: "slow"` / `api_ms: null` | 非数值按 0，不让 `slowest.sort` 抛错 |
| UTF-8 多字节腰斩 | `errors="replace"` 读入后落残行分支 |

## 六条

1. `test_report_tolerates_all_malformed_inputs`：rc 0 + 零 traceback + 合法行仍在；
2. `test_allure_tolerates_and_counts`：rc 0 + `skipped_malformed_lines` 计数与实际坏行数一致；
3. `test_allure_does_not_invent_passed`：坏行不进 passed（不洗绿）；
4. `test_empty_input_is_not_a_crash`：空文件 → rc 0、0 条结果；
5. `test_truncated_utf8_does_not_raise`：多字节腰斩 → 不裸 `UnicodeDecodeError`；
6. `test_bad_step_elements_do_not_hide_good_ones`：`steps` 混入坏元素时好元素仍被计。
"""
from __future__ import annotations

import json
from pathlib import Path

from atlas.allure_report import emit_allure
from atlas.reporting import export_runlog, summarize

_GOOD = {"run_id": "r1", "case": "c1", "env": "sit", "status": "passed",
         "steps": [{"step": "s1", "op": "api", "ok": True, "dur_ms": 10, "api_ms": 3}]}


def _write(tmp_path, name: str, payload: str) -> Path:
    path = tmp_path / name
    path.write_text(payload, encoding="utf-8")
    return path


def _mixed_payload() -> str:
    return "\n".join([
        json.dumps(_GOOD),
        json.dumps(_GOOD)[:40],                     # 腰斩
        "[1, 2, 3]",                                # 非对象
        json.dumps({"case": "c2", "steps": []}),    # 缺 run_id
        json.dumps({"run_id": "r2", "case": "c3", "status": "failed",
                    "steps": [None, "x", {"ok": True}]}),
        json.dumps({"run_id": "r3", "case": "c4", "status": "failed",
                    "steps": [{"step": "s", "dur_ms": "slow", "api_ms": None}]}),
        "",
    ])


def test_report_tolerates_all_malformed_inputs(tmp_path):
    source = _write(tmp_path, "mixed.jsonl", _mixed_payload())
    out = export_runlog(source, tmp_path / "runlog.txt")
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "合法行没有被导出"
    rows = [json.loads(ln) for ln in lines]
    assert any(row["case"] == "c1" for row in rows), "坏行把合法行挤掉了"


def test_allure_tolerates_and_counts(tmp_path):
    source = _write(tmp_path, "mixed.jsonl", _mixed_payload())
    result = emit_allure(source, tmp_path / "results", clean=False)
    summary = json.loads((tmp_path / "results" / "atlas-summary.json").read_text(encoding="utf-8"))
    assert summary["skipped_malformed_lines"] == 2, summary
    assert summary["total"] >= 1, summary
    assert result is not None


def test_allure_does_not_invent_passed(tmp_path):
    source = _write(tmp_path, "junk.jsonl",
                    "\n".join([json.dumps(_GOOD)[:30], "[1,2]", "null", ""]))
    emit_allure(source, tmp_path / "results", clean=False)
    summary = json.loads((tmp_path / "results" / "atlas-summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] == 0, f"坏行被算成通过（洗绿）：{summary}"
    assert summary["skipped_malformed_lines"] == 3, summary


def test_empty_input_is_not_a_crash(tmp_path):
    source = _write(tmp_path, "empty.jsonl", "")
    out = export_runlog(source, tmp_path / "runlog.txt")
    assert out.read_text(encoding="utf-8") == ""
    emit_allure(source, tmp_path / "results", clean=False)
    summary = json.loads((tmp_path / "results" / "atlas-summary.json").read_text(encoding="utf-8"))
    assert summary["total"] == 0, summary


def test_truncated_utf8_does_not_raise(tmp_path):
    raw = json.dumps({"run_id": "r9", "case": "中文用例", "status": "passed",
                      "steps": []}, ensure_ascii=False).encode("utf-8")
    source = tmp_path / "truncated.jsonl"
    source.write_bytes(raw[:-2] + b"\n" + json.dumps(_GOOD).encode() + b"\n")
    # 不抛 UnicodeDecodeError（#131 防线）
    assert summarize(source)["runs"] >= 1
    export_runlog(source, tmp_path / "runlog.txt")


def test_bad_step_elements_do_not_hide_good_ones(tmp_path):
    payload = json.dumps({"run_id": "r5", "case": "c5", "status": "failed",
                          "steps": [None, {"step": "good", "ok": False, "dur_ms": 5},
                                    "junk", {"step": "good2", "ok": False, "dur_ms": 1}]})
    source = _write(tmp_path, "steps.jsonl", payload)
    exported = export_runlog(source, tmp_path / "runlog.txt").read_text(encoding="utf-8")
    steps = [json.loads(ln)["step"] for ln in exported.splitlines() if ln.strip()]
    assert steps == ["good", "good2"], steps


def test_summarize_counts_malformed(tmp_path):
    source = _write(tmp_path, "mixed.jsonl", _mixed_payload())
    stats = summarize(source)
    assert stats["skipped_malformed_lines"] == 2, stats
    assert stats["runs"] >= 1 and "passed" in stats["status"], stats

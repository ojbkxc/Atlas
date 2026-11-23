"""JSONL 读侧的 **BOM 口径**守卫：带 BOM 的文件首行不许静默消失。

## 挡的是什么（2026-11-23 实测：审核塔 2 条草稿只看得到 1 条）

Windows 上「记事本旧版另存为 UTF-8」与 PowerShell 5.1 `Set-Content -Encoding UTF8` 都写 BOM。
五个 JSONL 读侧（`experience`/`review`/`reporting`/`review_code`/`scheduling.state`）此前一律
`read_text_retry(path, encoding="utf-8")`——首行于是变成 `\ufeff{"id":…}`，`json.loads` 抛
`JSONDecodeError`，被各自「残行跳过」分支**静默吞掉**：

- `experience_drafts.jsonl` 带 BOM → 审核塔少一条草稿（**那条草稿从此不存在**）；
- `runs.jsonl` 带 BOM → `summary`/`allure`/`report` 少一整条 run，三处都「看起来正常」；
- `schedule_runs.jsonl` 带 BOM → 水位线读不到最后一条，错过判据失真。

不崩、不报错、不少一行以外的一切都正常——正是「静默丢失」族。

## 修法

判据单点 `atomic_io.read_jsonl_text`：`utf-8-sig`（无 BOM 时与 `utf-8` 逐字节等价）+
瞬态锁重试 + 坏字节回落 replace。五个读侧的 `try/except UnicodeDecodeError` 五份复制
（本身即 #92 坏味道）一并收敛到这一处。

## 六条

1. `test_review_queue_sees_bom_first_row`：带 BOM 的草稿文件 → 队列看到全部条目（改前 2 条
   只见 1 条，实测复现）；
2. `test_summarize_counts_bom_first_run`：带 BOM 的 runs.jsonl → run 数不丢；
3. `test_experience_journal_reads_bom`：带 BOM 的草稿 → journal 读到首条；
4. `test_scheduler_runs_reads_bom`：带 BOM 的调度索引 → 行数不丢；
5. `test_bom_and_bad_bytes_coexist`：BOM + 中段坏字节 → 首行仍在、坏行照旧计 skipped；
6. `test_jsonl_readers_use_single_point`：源码面——五个读侧都调 `read_jsonl_text`，不再各写
   `read_text_retry(path, encoding="utf-8")`（防第五、六份复制长回来）。
"""
from __future__ import annotations

import json
from pathlib import Path

from atlas.experience import ExperienceJournal
from atlas.reporting import summarize
from atlas.review import ReviewQueue
from atlas.scheduling.state import read_runs

_REPO = Path(__file__).resolve().parents[1]

_DRAFTS = [
    {"id": "bom01", "kind": "pitfall", "task": "t1", "failure_class": "interaction"},
    {"id": "bom02", "kind": "pitfall", "task": "t2", "failure_class": "env_blocked"},
]
_RUNS = [
    {"run_id": "br1", "case": "c1", "env": "sit", "status": "passed", "steps": []},
    {"run_id": "br2", "case": "c2", "env": "sit", "status": "failed", "steps": []},
]


def _write_bom(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))


def test_review_queue_sees_bom_first_row(tmp_path):
    _write_bom(tmp_path / "reports" / "experience_drafts.jsonl", _DRAFTS)
    queue = ReviewQueue(tmp_path / "reports")
    ids = [r["id"] for r in queue.rows()]
    assert ids == ["bom01", "bom02"], f"BOM 首条被静默吞掉：{ids}"


def test_summarize_counts_bom_first_run(tmp_path):
    _write_bom(tmp_path / "runs.jsonl", _RUNS)
    stats = summarize(tmp_path / "runs.jsonl")
    assert stats["runs"] == 2, stats
    assert stats["skipped_malformed_lines"] == 0, stats


def test_experience_journal_reads_bom(tmp_path):
    _write_bom(tmp_path / "experience_drafts.jsonl", _DRAFTS)
    rows = ExperienceJournal._read_queue(tmp_path / "experience_drafts.jsonl")
    ids = [getattr(r, "id", r.get("id") if isinstance(r, dict) else "?") for r in rows]
    assert len(rows) == 2, f"journal 丢了 BOM 首条：{ids}"


def test_scheduler_runs_reads_bom(tmp_path):
    _write_bom(tmp_path / "schedule_runs.jsonl",
               [{"task_id": "t1", "status": "passed", "at": "2026-11-23T10:00:00Z"},
                {"task_id": "t2", "status": "failed", "at": "2026-11-23T11:00:00Z"}])
    rows = read_runs(tmp_path / "schedule_runs.jsonl")
    assert len(rows) == 2, f"调度索引丢了 BOM 首条：{rows}"


def test_bom_and_bad_bytes_coexist(tmp_path):
    """BOM（首行）与坏字节（中段）共存：首行仍在，坏行照旧进 skipped 计数。"""
    good = "\n".join(json.dumps(r) for r in _RUNS)
    path = tmp_path / "runs.jsonl"
    path.write_bytes(b"\xef\xbb\xbf" + good.encode("utf-8")
                     + b"\n\xff\xfe broken\n" + json.dumps(_RUNS[0]).encode() + b"\n")
    stats = summarize(path)
    assert stats["runs"] >= 2, f"BOM 首行被坏字节回落吞掉：{stats}"
    assert stats["skipped_malformed_lines"] >= 1, stats


def test_jsonl_readers_use_single_point():
    sites = {
        "experience.py": "src/atlas/experience.py",
        "review.py": "src/atlas/review.py",
        "reporting.py": "src/atlas/reporting.py",
        "review_code.py": "src/atlas/review_code.py",
        "scheduling/state.py": "src/atlas/scheduling/state.py",
    }
    offenders = []
    for name, rel in sites.items():
        text = (_REPO / rel).read_text(encoding="utf-8")
        if "read_jsonl_text(" not in text:
            offenders.append(f"{name}: 未走 BOM 安全单点")
        if 'read_text_retry(path, encoding="utf-8")' in text \
                or 'read_text_retry(runs_path, encoding="utf-8")' in text \
                or 'read_text_retry(self.path, encoding="utf-8")' in text:
            offenders.append(f"{name}: 仍有裸 utf-8 读 JSONL 的旧形态")
    assert not offenders, "\n".join(offenders)

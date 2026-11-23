"""`MAINT-001`（硬 sleep）的**适用面**守卫：只在测试代码里算违规。

## 挡的是什么（2026-11-23 实测）

`MAINT-001` 的建议是「改用 waits.py 语义等待」——那是**测试代码**该遵守的纪律。但
`detect_kind` 把所有 `.py` 都当 `pytest` 扫，于是引擎自己的轮询实现被判 **high**：
`atomic_io` 的 `time.sleep(_READ_RETRY_DELAY_S)`（读重试退避）、`dsl` 的
`time.sleep(interval_ms / 1000.0)`（`await: poll` 间隔）、`explore` 的命令轮询、
`legacy_fast` 的等待垫片——**这几个正是语义等待的实现本体**，报「请改用 waits.py」等于要求
引擎不要实现等待（自指噪声，与 ASSERT-006 那波同类）。

## 判据（`_is_test_module`，单点）

文件名 `test_*.py` / `*_test.py`，**或**任一路径段等于 `tests`。刻意不用「含 test 子串」
（`contrast.py` 会误中）。

## 五条

1. `test_engine_sleep_not_flagged`：`src/atlas/*.py` 里的 `time.sleep` → 不产 MAINT-001；
2. `test_test_file_sleep_still_flagged`：`test_*.py` 里的 → 仍产（正面对照，规则没被关掉）；
3. `test_tests_dir_sleep_flagged`：`tests/` 下任意名字的 `.py` → 仍产；
4. `test_substring_test_name_not_treated_as_test`：`contrast.py` → 不算测试（不产）；
5. `test_repo_has_no_maint001_on_engine`：整树 `src/` 扫完 0 条 MAINT-001（改前 4 条）。
"""
from __future__ import annotations

from pathlib import Path

from atlas.review_code import _is_test_module, load_config, scan_pytest_file

_REPO = Path(__file__).resolve().parents[1]
_SLEEP = "import time\n\n\ndef f():\n    time.sleep(0.5)\n"


def _config() -> dict:
    return load_config(None)


def _scan(tmp_path, rel: str, body: str = _SLEEP) -> list[dict]:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return scan_pytest_file(path, _config())


def _maint001(findings) -> list[dict]:
    return [f for f in findings if f["rule_id"] == "MAINT-001"]


def test_engine_sleep_not_flagged(tmp_path):
    assert not _maint001(_scan(tmp_path, "src/atlas/atomic_io.py")), \
        "引擎的轮询实现被要求「改用 waits.py」（自指噪声）"


def test_test_file_sleep_still_flagged(tmp_path):
    findings = _maint001(_scan(tmp_path, "test_flow.py"))
    assert findings, "测试里的硬 sleep 不再被报——规则被关掉了"
    assert findings[0]["severity"] == "high"


def test_tests_dir_sleep_flagged(tmp_path):
    assert _maint001(_scan(tmp_path, "tests/custom_name.py")), \
        "tests 目录下任意文件的硬 sleep 应仍被报"


def test_substring_test_name_not_treated_as_test(tmp_path):
    assert not _maint001(_scan(tmp_path, "contrast.py")), \
        "含 test 子串的文件被误判成测试代码"
    path = tmp_path / "contrast.py"
    assert _is_test_module(path) is False


def test_repo_has_no_maint001_on_engine():
    hits = []
    for path in sorted((_REPO / "src").rglob("*.py")):
        hits += [f for f in scan_pytest_file(path, _config()) if f["rule_id"] == "MAINT-001"]
    assert not hits, f"引擎源码里仍有 MAINT-001：{[(f['file'], f['line']) for f in hits]}"

"""`review-code` 聚合结论守卫：`summary.grade` 必须真的反映「最差文件」。

## 挡的是什么（2026-11-23 实测）

`summary` 取「最差文件等级」，起点写的是 `max_grade = "F"`，而 F 的 rank 已是最大值 4 ——
`>` 比较永远不成立，聚合 grade **恒为 F**：

```
files=102  total_findings=45  critical_findings=0  avg_score=99  grade=F
```

0 个 critical、平均 99 分的报告仍写 `grade: F`。聚合等级是报告第一行的结论，恒 F 等于把结论删掉
（也会让「分数回升」这件事在任何一次改动里都看不见）。

## 四条

1. `test_summary_grade_is_worst_file_grade`：A 与 C 两个文件 → 聚合 C（最差），不是 F/A；
2. `test_summary_grade_all_clean_is_a`：全 A → 聚合 A（旧实现给 F）；
3. `test_summary_grade_tracks_regression`：把 C 换成 F → 聚合 F（不是恒 A，也不是恒 F）；
4. `test_summary_matches_files_when_single`：单文件时聚合等级 == 该文件等级。
"""
from __future__ import annotations

from atlas.review_code import review_code


def _write(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _clean(name="clean.py"):
    return "def test_ok():\n    assert compute() == 3\n"


def _low(tmp_path, name: str):
    """只有 low finding（GREEN-004 的 noqa 不算；用 SAFE-001-COMMENT 形态：注释里的 URL）。"""
    return _write(tmp_path, name, "# 说明 https://user:pass@intranet.corp/x\ndef f():\n    return 1\n")


def _bad(tmp_path, name: str):
    """critical finding（裸吞异常）。"""
    return _write(tmp_path, name, "def f():\n    try:\n        g()\n    except Exception:\n        pass\n")


def test_summary_grade_is_worst_file_grade(tmp_path):
    _write(tmp_path, "a_clean.py", _clean())
    _low(tmp_path, "b_low.py")
    _bad(tmp_path, "c_bad.py")
    result = review_code([str(tmp_path)], mode="static")
    grades = {fr["file"].split("\\")[-1].split("/")[-1]: fr["score"]["grade"]
              for fr in result["results"]}
    assert grades["a_clean.py"] == "A", grades
    worst = max(grades.values(), key=lambda g: {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}[g])
    assert result["summary"]["grade"] == worst, (result["summary"], grades)


def test_summary_grade_all_clean_is_a(tmp_path):
    _write(tmp_path, "a_clean.py", _clean())
    _write(tmp_path, "b_clean.py", "def test_x():\n    assert ok() is False\n")
    result = review_code([str(tmp_path)], mode="static")
    assert result["summary"]["grade"] == "A", result["summary"]


def test_summary_grade_tracks_regression(tmp_path):
    _write(tmp_path, "a_clean.py", _clean())
    _bad(tmp_path, "b_bad.py")
    result = review_code([str(tmp_path)], mode="static")
    assert result["summary"]["grade"] == "F", result["summary"]
    assert result["summary"]["critical_findings"] >= 1


def test_summary_matches_files_when_single(tmp_path):
    path = _low(tmp_path, "only.py")
    result = review_code([str(path)], mode="static")
    assert result["summary"]["grade"] == result["results"][0]["score"]["grade"], result["summary"]

"""`atlas review-code` 的「吞异常」判据：**有理由**与**裸吞**必须分开。

## 挡的是什么（产品自身的噪声缺陷，2026-11-23 实测）

`atlas review-code src` 旧口径把 `except Exception: pass` 一律记 critical。本仓的真实写法里，
收尾路径的吞异常**都带理由注释**（「关闭失败不得顶掉原始异常」「留痕写失败不得顶掉执行真因」），
于是：

```
files=102  total_findings=47  critical_findings=27  avg_score=94  grade=F
```

27 个 critical 里大半是**有理由的收尾吞异常**，整份报告 grade F —— 真实信号被噪声淹没
（与红线 #7 相反的另一端：不是洗绿，而是把有纪律的写法判成缺陷）。

## 修法（不放行也不消失）

`GREEN-001-DECLARED`（severity **low**）：同行 / 上一行 / body 首行注释写了理由（≥6 非空字符）
才走这一档；仍然**记一条**并进报告，理由文本不进报告（避免把业务注释抄出去）。
判据刻意保守——只认「有没有写理由」，**不猜理由成不成立**（那是人审的事）。

改后同一棵树：`critical_findings 27 → 16`，`avg_score 94 → 96`，`review_code.py` 自己的两处
**裸吞**仍是 critical（说明这不是放宽，是分流）。

## 五条

1. `test_bare_swallow_stays_critical`：裸 `except Exception: pass` → GREEN-001 / critical / 分数上限 59；
2. `test_declared_swallow_is_low_not_critical`：同行注释 → GREEN-001-DECLARED / low / 分数 ≥90；
3. `test_reason_on_previous_line_counts`：上一行注释同样算已声明；
4. `test_short_comment_is_not_a_loophole`：`# x` 这种敷衍注释不算（正向对照，防「写俩字就过关」）；
5. `test_handled_exception_has_no_green001`：真处理（记录日志）根本不产 GREEN-001。
"""
from __future__ import annotations

from atlas.review_code import load_config, scan_pytest_file, score_findings


def _config() -> dict:
    return load_config(None)


def _scan(tmp_path, body: str, name="mod.py") -> list[dict]:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return scan_pytest_file(path, _config())


def _rules(findings) -> list[str]:
    return [f["rule_id"] for f in findings]


def test_bare_swallow_stays_critical(tmp_path):
    findings = _scan(tmp_path, "def f():\n    try:\n        g()\n    except Exception:\n        pass\n")
    assert "GREEN-001" in _rules(findings), _rules(findings)
    graded = score_findings(findings, _config())
    assert graded["total"] <= 59, graded


def test_declared_swallow_is_low_not_critical(tmp_path):
    findings = _scan(tmp_path, "def f():\n    try:\n        g()\n    except Exception:\n"
                               "        pass  # 关闭失败不得顶掉原始异常\n")
    assert "GREEN-001-DECLARED" in _rules(findings), _rules(findings)
    assert "GREEN-001" not in _rules(findings), "已声明理由仍被记成裸吞"
    finding = next(f for f in findings if f["rule_id"] == "GREEN-001-DECLARED")
    assert finding["severity"] == "low"
    graded = score_findings(findings, _config())
    assert graded["total"] >= 90, graded
    # 仍然留痕：不是消失
    assert graded["finding_count"] == 1


def test_reason_on_previous_line_counts(tmp_path):
    findings = _scan(tmp_path, "def f():\n    try:\n        g()\n"
                               "    # 收尾失败不得顶掉真正原因\n"
                               "    except Exception:\n        pass\n")
    assert "GREEN-001-DECLARED" in _rules(findings), _rules(findings)


def test_short_comment_is_not_a_loophole(tmp_path):
    findings = _scan(tmp_path, "def f():\n    try:\n        g()\n    except Exception:\n"
                               "        pass  # x\n")
    assert "GREEN-001" in _rules(findings), f"敷衍注释不该过关：{_rules(findings)}"
    assert "GREEN-001-DECLARED" not in _rules(findings)


def test_handled_exception_has_no_green001(tmp_path):
    findings = _scan(tmp_path, "def f():\n    try:\n        g()\n    except Exception as exc:\n"
                               "        print('warn', exc)\n")
    assert not [r for r in _rules(findings) if r.startswith("GREEN-001")], _rules(findings)

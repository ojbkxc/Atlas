"""`ASSERT-006`（注释掉的断言）的**文档噪声档**守卫。

## 挡的是什么（`review_code.py` 自身 13 条 findings 里的 8 条）

`ASSERT-006` 的正则只看行首 `# assert`。但**规则文档本身**大量使用这种写法：
`# assert True, assert 1 是恒真断言`、`# assert x is not None / assert x is None`
（每个规则的 rationale 注释都以 `# assert …` 开头）。旧实现全判 **high**，
`review_code.py` 因此 74 分 / C——评审器被自己的文档打了低分，噪声盖住真信号。

## 判据（`_is_commented_assert_code`，单点）

`# assert` 后的尾巴满足以下**任一**即按「讲解」处理（不产 ASSERT-006）：
①含中文讲解词（是/均/这类/都是/就是/说明/形如/写法/口径/或…）；
②多条 assert 连写（`# assert A, assert B`）——真被注释的断言一行只有一条，枚举才连写；
③`A == B or assert C` 这类 or/not 连接的讲解。

真被注释掉的断言（`# assert resp["x"] == "ACTIVE"`）不受影响——尾巴是代码形态。

## 五条

1. `test_real_commented_assert_still_high`：被注释的断言代码 → ASSERT-006 照旧；
2. `test_rule_doc_comment_not_flagged` ×3：三种讲解形态（枚举/含「是」/or 连接）→ 不产；
3. `test_self_scan_not_dominated_by_doc_noise`：`review_code.py` 自扫 findings ≤ 2 条
   ASSERT-006（改前 8 条）。
"""
from __future__ import annotations

from pathlib import Path

from atlas.review_code import (
    _is_commented_assert_code,
    load_config,
    scan_pytest_file,
)

_REPO = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return load_config(None)


def _scan(tmp_path, body: str) -> list[dict]:
    path = tmp_path / "mod.py"
    path.write_text(body, encoding="utf-8")
    return scan_pytest_file(path, _config())


def test_real_commented_assert_still_high(tmp_path):
    findings = _scan(tmp_path, "def test_x():\n"
                               "    resp = do_call()\n"
                               '    # assert resp["data"]["status"] == "ACTIVE"\n'
                               "    pass\n")
    assert any(f["rule_id"] == "ASSERT-006" for f in findings), \
        [f["rule_id"] for f in findings]


def test_enumerated_doc_comment_not_flagged(tmp_path):
    findings = _scan(tmp_path, '# assert True, assert 1, assert "x", assert [1,2]\n'
                               "# assert x is not None / assert x is None\n"
                               "x = 1\n")
    assert not any(f["rule_id"] == "ASSERT-006" for f in findings), \
        [f["rule_id"] for f in findings]


def test_doc_hint_comment_not_flagged(tmp_path):
    findings = _scan(tmp_path, "# assert len(x) > 0: len() is the call, > 0 is the comparison\n"
                               "x = 1\n")
    assert not any(f["rule_id"] == "ASSERT-006" for f in findings)


def test_or_connected_doc_comment_not_flagged(tmp_path):
    findings = _scan(tmp_path, "# assert x == pytest.approx(y) or assert x == approx(y)\n"
                               "x = 1\n")
    assert not any(f["rule_id"] == "ASSERT-006" for f in findings)


def test_discriminator_table():
    cases = [
        ('    # assert resp["data"]["status"] == "ACTIVE"', True),
        ("# assert isinstance(x, T)", True),
        ("# assert len(x) > 0", True),
        ("# assert abs(a-b) < 0.01", True),
        ("# assert True, assert 1, assert \"x\", assert [1,2]", False),
        ("# assert x is not None / assert x is None", False),
        ("# assert len(x) > 0: len() is the call, > 0 is the comparison wrapping it", False),
        ("# assert x == pytest.approx(y) or assert x == approx(y)", False),
        ("# assert  # 断言本体为空", False),
    ]
    for text, expected in cases:
        got = _is_commented_assert_code(text)
        assert got == expected, f"{text!r} -> {got}（期望 {expected}）"


def test_self_scan_not_dominated_by_doc_noise():
    path = _REPO / "src" / "atlas" / "review_code.py"
    findings = scan_pytest_file(path, _config())
    hits = [f for f in findings if f["rule_id"] == "ASSERT-006"]
    assert len(hits) <= 2, f"评审器自扫 ASSERT-006 又被文档噪声占据：{len(hits)} 条"

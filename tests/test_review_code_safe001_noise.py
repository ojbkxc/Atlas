"""`SAFE-001` 的**噪声面**守卫：注释/文档里的凭据「形态」不该判 critical，占位符不算硬编码。

## 挡的是什么（`atlas review-code src` 实测，2026-11-23）

`SAFE-001`（硬编码凭据/URL）是 critical 规则。旧实现逐行正则、不分「代码 / 注释 / docstring」，
于是本仓里**教用户怎么填 env** 的那些行自己吃了 critical：

- `config.py` 模块 docstring：``代理形态 `http://user:pass@host:port`…``；
- `credential_gate.py` 注释：``# `https://user:pass@host/x`：userinfo 是经典凭据形态…``；
- `emit_pytest.py` 产物 README 渲染：``export ATLAS_FAST_PASSWORD='<密码>'``、
  ``export ATLAS_BASE_URL='https://<host>'``（`<…>` 占位比 3 字符阈值长，照样命中）。

16 个 critical 里 6 个是这类**文档噪声**，把整份报告压成 grade F，真正的裸吞被淹没。

## 修法（减噪声不减判据）

1. `<…>` / `${…}` **填写说明占位**先整段抹掉再判（抹段不是整行跳过：同一行有真值仍命中）；
2. 注释行与 **docstring** 行降 `SAFE-001-COMMENT`（severity **low**，仍记一条并进报告）——
   docstring 行号由 `_docstring_lines`（AST）给，不靠猜三引号文本。

## 八条

1. `test_comment_url_is_low_not_critical`；2. `test_docstring_url_is_low_not_critical`；
3. `test_placeholder_secret_is_not_hardcoded`；4. `test_placeholder_url_default_is_not_hardcoded`；
5. `test_real_literal_secret_stays_critical`（正向对照）；
6. `test_placeholder_does_not_mask_real_value_on_same_line`（抹段不等于整行豁免）；
7. `test_real_url_literal_stays_critical`（正向对照，非保留域）；
8. `test_comment_only_findings_keep_score_high`（low 档不能把文件压到 F）。
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


def test_comment_url_is_low_not_critical(tmp_path):
    findings = _scan(tmp_path, "# 形态示例 http://user:pass@intranet.corp:8080/x\n"
                               "x = 1\n")
    assert "SAFE-001-COMMENT" in _rules(findings), _rules(findings)
    assert "SAFE-001" not in _rules(findings), "注释里的形态仍被判成硬编码凭据"
    item = next(f for f in findings if f["rule_id"] == "SAFE-001-COMMENT")
    assert item["severity"] == "low"


def test_docstring_url_is_low_not_critical(tmp_path):
    findings = _scan(tmp_path, '"""模块说明：代理形态 `http://user:pass@intranet.corp/x`。"""\n'
                               "x = 1\n")
    assert "SAFE-001-COMMENT" in _rules(findings), _rules(findings)
    assert "SAFE-001" not in _rules(findings), "docstring 里的形态仍被判成 critical"


def test_placeholder_secret_is_not_hardcoded(tmp_path):
    """产物 README 里那行 `export ATLAS_FAST_PASSWORD='<密码>'` 的形态（用**合法 Python**
    承载——裸 shell 行会先撞 MAINT-006 语法错，守卫就变成了「空转绿灯」）。"""
    findings = _scan(tmp_path, 'HINT = "export ATLAS_FAST_PASSWORD=\'<密码>\'"\n')
    assert not [r for r in _rules(findings) if r.startswith("SAFE-001")], _rules(findings)


def test_placeholder_url_default_is_not_hardcoded(tmp_path):
    findings = _scan(tmp_path, 'BASE_URL = os.environ.get("ATLAS_BASE_URL", "https://<host>")\n')
    assert not [r for r in _rules(findings) if r.startswith("SAFE-001")], _rules(findings)


def test_real_literal_secret_stays_critical(tmp_path):
    findings = _scan(tmp_path, "password = 'hunter2'\n")
    assert "SAFE-001" in _rules(findings), _rules(findings)
    item = next(f for f in findings if f["rule_id"] == "SAFE-001")
    assert item["severity"] == "critical"


def test_placeholder_does_not_mask_real_value_on_same_line(tmp_path):
    findings = _scan(tmp_path, "password='hunter2'; other='<密码>'\n")
    assert "SAFE-001" in _rules(findings), "同行的真值被占位豁免连带放过了"


def test_real_url_literal_stays_critical(tmp_path):
    findings = _scan(tmp_path, 'BASE = "https://intranet.corp.example.org/api"\n')
    # `.example.org` 属于保留域 → 应豁免；换成真域名再验一次
    assert "SAFE-001" not in _rules(findings), _rules(findings)
    findings = _scan(tmp_path, 'BASE = "https://intranet.corp.localdomain/api"\n', "m2.py")
    assert "SAFE-001" in _rules(findings), _rules(findings)


def test_comment_only_findings_keep_score_high(tmp_path):
    findings = _scan(tmp_path, "# 说明 https://user:pass@intranet.corp/x\n"
                               '"""另一处 docstring 说明 http://user:pass@intranet.corp/y"""\n'
                               "def f():\n    return 1\n")
    graded = score_findings(findings, _config())
    assert all(f["severity"] == "low" for f in findings if f["rule_id"].startswith("SAFE-001"))
    assert graded["total"] >= 90, graded

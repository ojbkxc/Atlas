"""生成期**字面凭据**抹除守卫（红线 #1「凭据绝不落盘」的输入面）。

## 挡的是什么（实测抓到的泄漏）

`atlas gen` 的产物是**要交给别人的文件**（flow YAML / pytest 包 / report.json / 探针骨架）。
文档作者在摘要或路径里贴一个带凭据的示例 URL 是极常见动作：

```markdown
# 查询客户 https://ops:S3cretPwd@example.com/api 与 ?token=TOK123
## GET /web/example/customer/code?access_token=TOK123
```

旧实现只闸 **headers**（C4①），这些值于是原样进了：`flows/<id>.yaml`、
`contracts/*.json`、`probes/*.py`、`pytest/test_*.py`、`report.json`、两份 README——
**连文件名都带 token**（`flows/get-web-example-customer-code-access_token-tok123.yaml`）。
产物是分发包，等于把字面凭据发了出去。

## 修法（判据单点，不复制名单）

`generate()` 在 `parse_source()` 之后、**任何产出之前**调 `_redact_endpoint_secrets`：
`path`/`base` 走 `credential_gate.sanitize_url`，`summary` 走 `sanitize_text`；抹了什么如实进
`manual_assertions`（人要知道「这里原本有个字面凭据，请改用 env」）。
`credential_gate.sanitize_url` 同时补上 **userinfo（`user:pass@`）整段 redact**——那是经典
凭据形态且**没有敏感键名**，逐键 redact 抓不到（实测 `ops:S3cretPwd@example.com` 原样逃逸）。

## 四条

1. `test_document_literal_credentials_do_not_reach_artifacts`：真跑 `generate()` 出整包，
   扫描**每个**产物文件与文件名 → 秘文零命中；`manual_assertions` 里点名「已占位」。
2. `test_benign_document_is_untouched`：正常文档不产红acted 痕迹（正向对照：不能把
   `?page=1&division=DIV` 这类业务参数也抹了——那会毁掉可用产物）。
3. `test_sanitize_url_redacts_userinfo`：单点函数自身（userinfo 整段抹、无凭据 URL 逐字节不变）。
4. `test_redaction_reuses_credential_gate`：源码面——生成器只调 `credential_gate` 的两个函数，
   不得自带敏感键名单（#92 不留第二把刀）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

from atlas.credential_gate import REDACTED, sanitize_url
from atlas.generator import GenOptions, generate

_REPO = Path(__file__).resolve().parents[1]
_SECRETS = ("S3cretPwd", "TOK123")

_SECRET_DOC = """# 查询客户 https://ops:S3cretPwd@example.com/api 与 ?token=TOK123

## GET /web/example/customer/code?access_token=TOK123

| 参数 | 说明 |
|---|---|
| code | 客户编码 |
"""

_BENIGN_DOC = """# 查询客户编码

## GET /web/example/customer/code

| 参数 | 说明 |
|---|---|
| division | 事业部 |
"""


def _generate(tmp_path, text: str, name: str = "doc.md"):
    source = tmp_path / name
    source.write_text(text, encoding="utf-8")
    out = tmp_path / "out"
    result = generate(source, out, GenOptions(scenario="a"))
    return result, out


def test_document_literal_credentials_do_not_reach_artifacts(tmp_path):
    result, out = _generate(tmp_path, _SECRET_DOC)
    leaked: list[str] = []
    for path in out.rglob("*"):
        rel = str(path.relative_to(out))
        for secret in _SECRETS:
            if secret in rel:
                leaked.append(f"{rel}（文件名）")
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                if secret in text:
                    leaked.append(f"{rel}（内容）")
    assert not leaked, "字面凭据进了产物（红线 #1）：\n" + "\n".join(sorted(set(leaked)))
    joined = " ".join(result.manual_assertions)
    assert "已占位" in joined and "<redacted>" in joined, f"抹除没有留痕：{result.manual_assertions}"
    # flow id 也不得带 token（文件名已由 path 派生）
    flows = list((out / "flows").glob("*.yaml"))
    assert flows and all(s not in flows[0].name for s in _SECRETS), flows[0].name
    assert REDACTED in flows[0].name or "redacted" in flows[0].name


def test_benign_document_is_untouched(tmp_path):
    result, out = _generate(tmp_path, _BENIGN_DOC, "benign.md")
    assert not [n for n in result.manual_assertions if "已占位" in n], result.manual_assertions
    flow = yaml.safe_load(next((out / "flows").glob("*.yaml")).read_text(encoding="utf-8"))
    path = next(s.get("path") for s in flow["steps"] if s.get("task") == "api_call")
    assert path == "/web/example/customer/code", f"业务参数被误抹：{path}"
    assert "redacted" not in next((out / "flows").glob("*.yaml")).name


def test_sanitize_url_redacts_userinfo():
    assert sanitize_url("https://ops:S3cretPwd@example.com/api") == f"https://{REDACTED}@example.com/api"
    # 无 userinfo / 无 query 的 URL 逐字节不变（不能把正常 URL 改写）
    for keep in ("https://example.com/api/x", "/web/a/b", "http://127.0.0.1:8080/p?page=1"):
        assert sanitize_url(keep) == keep
    # query 敏感键仍照旧逐键抹，业务键保留
    assert sanitize_url("/x?page=1&access_token=abc") == f"/x?page=1&access_token={REDACTED}"


def test_redaction_reuses_credential_gate():
    text = (_REPO / "src" / "atlas" / "generator" / "__init__.py").read_text(encoding="utf-8")
    assert "from ..credential_gate import sanitize_text, sanitize_url" in text
    assert "_redact_endpoint_secrets" in text
    # 不得在生成器里另写敏感键名单（判据单点在 credential_gate.query_key_is_sensitive）。
    # 只看**代码**：docstring 里必然引用实测文件名（`…access_token-tok123.yaml`），
    # 扫全文会把「解释缺陷的注释」自己判红——这正是本仓 CI 门禁曾踩过的形态。
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    code = ast.unparse(tree)
    # 判据是「不得自带敏感键**名单**」，不是「不许出现 secret 这个词」——函数名
    # `_redact_endpoint_secrets` 自己就带。故只看双引号字符串字面量里的键名。
    literals = {node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    for name in ("access_token", "password", "secret", "authorization", "token"):
        hits = [value for value in literals if value.strip().lower() == name]
        assert not hits, f"生成器自带敏感键名单 {name!r}（应走 credential_gate 单点）"
    assert "query_key_is_sensitive" not in code, "生成器复制了敏感键判据（应调 credential_gate）"

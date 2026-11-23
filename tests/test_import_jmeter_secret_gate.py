"""JMeter 迁移的 **URL/文本面凭据闸**守卫（红线 #1，round-7 实测缺陷）。

## 挡的是什么

`scripts/import_jmeter.py` 旧实现只给 **HeaderManager** 过闸（C4①）。`HTTPSampler.path`
里的字面凭据没有第二道刀，于是实测：

```
path: /web/example/customer/code?access_token=TOK123
path: https://ops:S3cretPwd@example.com/api/x?page=1
```

逐字进了 YAML 与 `-o` 文件，而 stderr 还在报「0 处字面凭据头」——迁移器把凭据搬了家
（产物是要进新仓库的）。判据单点仍是 `atlas.credential_gate`：`sanitize_url`（query 逐键
redact + userinfo 整段 redact）、`sanitize_text`（intent 来自 sampler 名）。

## 四条

1. `test_path_credentials_gated_before_write`：`-o` 文件里秘文零命中且出现 `<redacted>`；
2. `test_benign_paths_untouched`：正常 path 逐字节不变（正向对照：业务 query 参数不得被抹）；
3. `test_gate_notes_never_leak_values`：stderr/stdout 只报位置（step 号/字段），不含原值；
4. `test_gate_reuses_credential_gate`：源码面——迁移器只调 `credential_gate` 的两个净化函数，
   不自带敏感键名单。
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "import_jmeter.py"
_SECRETS = ("TOK123", "S3cretPwd")

_SPEC = importlib.util.spec_from_file_location("import_jmeter_under_test", _SCRIPT)
jmeter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(jmeter)


def _jmx(tmp_path, paths: list[str], name="p.jmx") -> Path:
    samplers = "\n".join(
        f"""      <HTTPSamplerProxy testname="采样{index}">
        <stringProp name="HTTPSampler.path">{path.replace("&", "&amp;")}</stringProp>
        <stringProp name="HTTPSampler.method">GET</stringProp>
      </HTTPSamplerProxy>
      <hashTree/>"""
        for index, path in enumerate(paths, 1))
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="plan"/>
    <hashTree>
{samplers}
    </hashTree>
  </hashTree>
</jmeterTestPlan>
"""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def argv(monkeypatch):
    def _set(*args):
        monkeypatch.setattr("sys.argv", ["import_jmeter.py", *args])
    return _set


def test_path_credentials_gated_before_write(tmp_path, argv, capsys):
    jmx = _jmx(tmp_path, ["/web/x/code?access_token=TOK123",
                          "https://ops:S3cretPwd@example.com/api/x?page=1"])
    out = tmp_path / "flow.yaml"
    argv(str(jmx), "-o", str(out))
    assert jmeter.main() == 0
    text = out.read_text(encoding="utf-8")
    for secret in _SECRETS:
        assert secret not in text, f"{secret!r} 落进了导入产物（红线 #1）"
    assert "<redacted>" in text
    assert "page=1" in text, "业务 query 参数不该被抹掉"
    captured = capsys.readouterr()
    assert "写盘前占位" in captured.err, captured.err


def test_benign_paths_untouched(tmp_path, argv, capsys):
    jmx = _jmx(tmp_path, ["/web/x/code?page=1&division=DIV01"])
    out = tmp_path / "flow.yaml"
    argv(str(jmx), "-o", str(out))
    assert jmeter.main() == 0
    text = out.read_text(encoding="utf-8")
    assert "path: /web/x/code?page=1&division=DIV01" in text
    assert "<redacted>" not in text
    assert "写盘前占位" not in capsys.readouterr().err


def test_gate_notes_never_leak_values(tmp_path, argv, capsys):
    jmx = _jmx(tmp_path, ["/web/x/code?access_token=TOK123"])
    out = tmp_path / "flow.yaml"
    argv(str(jmx), "-o", str(out))
    assert jmeter.main() == 0
    captured = capsys.readouterr()
    both = captured.out + captured.err
    for secret in _SECRETS:
        assert secret not in both, f"净化告警把原值打进了终端：{secret!r}"


def test_gate_reuses_credential_gate():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "sanitize_url" in text and "sanitize_text" in text
    assert "def apply_credential_gate" in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    literals = {node.value.strip().lower() for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    for name in ("access_token", "password", "token", "secret"):
        assert name not in literals, f"迁移器自带敏感键名单 {name!r}（应走 credential_gate 单点）"


def test_capture_script_echoes_sanitized_path():
    """采集脚本的**终端回显**也要过净化刀（写盘那份保留原样，用途不同）。"""
    text = (_REPO / "scripts" / "capture_save_request.py").read_text(encoding="utf-8")
    assert "sanitize_url(" in text, "采集回显没有走 credential_gate.sanitize_url"
    assert 'print(" ", item["path"][:110]' not in text, "回显又直接打印了原始 path"

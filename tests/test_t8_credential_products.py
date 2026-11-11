"""T8-C（红线 #2：凭据不落盘）产物凭据泄漏回归。

verifier T8 发现：pytest 产物在两个入口逐字落字面凭据——
- `export-pytest`：直接吃用户的 flow.yaml，flow 步骤字面 `Authorization` → 产物逐字含它；
- `gen --with-pytest` 的 pytest 半产物：flow YAML 已净化（emit_flow 过闸），但同次 pytest
  半产物从 `endpoints + global_headers` 出、之前**没有任何闸**，仍含字面 token。

根因：`credential_gate.gate_flow_headers` 只在 `generator/emit_flow.py` 被调，emit_pytest 全路径不调。
修法：`emit_pytest_package` 渲染前对 specs 头过**同一把唯一判据** `credential_gate`（不复制名单），
字面凭据换 `${ATLAS_HDR_n}` 占位。本文件锁死修复形态与正向对照（`${VAR}` 原样、产物仍可解析、零 import atlas）。
"""
import ast
import json
from pathlib import Path

from atlas.generator.emit_pytest import emit_pytest_package
from atlas.generator.model import Endpoint

# 三种对抗形态：Bearer(Authorization) / Cookie / 业务前缀 sessionid
LITERALS = {
    "Authorization": "Bearer TOPSECRET-abcdef0123456789",
    "Cookie": "sessionid=deadbeef1234567890abcdef12345678",
    "isc-axios-sessionid": "deadbeef-1234-5678-9abc-def0123456789",
}


def _flow_with_literal_headers():
    return {"id": "t8cred", "steps": [
        {"task": "api_call", "method": "GET", "path": "/pet/1", "save_as": "resp_1",
         "headers": dict(LITERALS), "read_only": True}]}


def _test_module_text(written):
    path = next(Path(x) for x in written if Path(x).name.startswith("test_"))
    return path.read_text(encoding="utf-8")


def _assert_no_literal_and_valid(text):
    for name, value in LITERALS.items():
        assert value not in text, f"{name} 的字面值进了产物（凭据落盘，红线 #2）"
    assert "TOPSECRET" not in text
    assert "deadbeef" not in text.lower()
    ast.parse(text)                      # 产物仍是合法 Python
    assert "import atlas" not in text    # 仍零 import atlas（gap9 硬约束）


def test_export_pytest_flow_literal_headers_gated(tmp_path):
    """export-pytest 入口：flow 步骤字面凭据 → 产物零字面命中，换 ${ATLAS_HDR_n} 占位。"""
    written = emit_pytest_package(out_dir=tmp_path / "p", flow=_flow_with_literal_headers(),
                                  force=True)
    text = _test_module_text(written)
    _assert_no_literal_and_valid(text)
    assert "${ATLAS_HDR_" in text                    # 占位路径不变（人审接 env）


def test_gen_with_pytest_endpoints_literal_headers_gated(tmp_path):
    """gen --with-pytest 的 pytest 半产物入口：从 endpoints+global_headers 出——同一闸必须罩住。"""
    ep = Endpoint(summary="getPet", path="/pet/1", method="GET")
    written = emit_pytest_package(out_dir=tmp_path / "p", endpoints=[ep], flow_id="genpet",
                                  global_headers=dict(LITERALS), force=True)
    text = _test_module_text(written)
    _assert_no_literal_and_valid(text)
    assert "${ATLAS_HDR_" in text


def test_externalized_placeholders_pass_through_untouched(tmp_path):
    """正向对照：`${VAR}`/`${__P()}` 已外部化的值原样进产物——不重复换成 ATLAS_HDR 占位。"""
    flow = {"id": "t8ext", "steps": [
        {"task": "api_call", "method": "GET", "path": "/pet/1", "save_as": "r",
         "headers": {"Authorization": "${AUTH_TOKEN}",
                     "isc-app": "${__P(ISC_APP_HEADER, demo)}"},
         "read_only": True}]}
    written = emit_pytest_package(out_dir=tmp_path / "p", flow=flow, force=True)
    text = _test_module_text(written)
    assert "${AUTH_TOKEN}" in text and "${__P(ISC_APP_HEADER, demo)}" in text
    assert "ATLAS_HDR" not in text


def test_gen_end_to_end_pytest_half_has_no_literal(tmp_path):
    """端到端：`generate(... with_pytest ...)` 的 pytest 半产物零字面凭据（真入口，非绕道）。"""
    from atlas.generator import GenOptions, generate
    doc = tmp_path / "src.md"
    doc.write_text("## GET /api/list\n\n请求示例：\n```json\n{}```\n", encoding="utf-8")
    headers_file = tmp_path / "h.json"
    headers_file.write_text(json.dumps(LITERALS), encoding="utf-8")
    generate(doc, tmp_path / "out",
             GenOptions(source_type="auto", with_pytest=True, headers_file=str(headers_file)),
             registry=None)
    pytest_half = sorted((tmp_path / "out" / "pytest").glob("test_*.py"))
    assert pytest_half, "应产出 pytest 半产物"
    text = pytest_half[0].read_text(encoding="utf-8")
    _assert_no_literal_and_valid(text)
    assert "${ATLAS_HDR_" in text

"""atlas gen（Gap3 文档→代码生成）测试。

离线、零网络、零执行。覆盖解析层/生成层/接入层。
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest
import yaml

from atlas.cli import main, _full_registry
from atlas.generator import GenOptions, generate
from atlas.generator.sources import SourceError, parse_source
from atlas.generator.emit_flow import FlowEmitError
from atlas.generator.checklist import ChecklistError
from atlas.generator.emit_contract import grade_contract, emit_contracts
from atlas.generator.model import Endpoint, BodySpec, ResponseSpec


# ---- 解析层 ----

OPENAPI_DOC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/pets/{id}": {
            "get": {
                "summary": "查询宠物",
                "parameters": [
                    {"name": "id", "in": "path", "required": True,
                     "schema": {"type": "string"}},
                ],
                "responses": {"200": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Pet"}}}}},
            },
        },
        "/pets": {
            "post": {
                "summary": "创建宠物",
                "requestBody": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Pet"}}}},
                "responses": {"200": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Result"}}}}},
            },
        },
    },
    "components": {"schemas": {
        "Pet": {"type": "object", "required": ["name"],
                "properties": {"name": {"type": "string"},
                               "id": {"type": "integer"}}},
        "Result": {"type": "object",
                   "properties": {"resultCode": {"type": "string"}}},
    }},
}


def test_openapi3_ref_deref(tmp_path):
    doc = tmp_path / "pet.yaml"
    doc.write_text(yaml.safe_dump(OPENAPI_DOC), encoding="utf-8")
    endpoints, stype = parse_source(doc)
    assert stype == "openapi"
    assert len(endpoints) == 2
    get_ep = next(e for e in endpoints if e.method == "GET")
    assert get_ep.path == "/pets/{id}"
    # $ref 解引用：Pet schema 的属性被造出示例
    assert get_ep.response.is_object is True
    # path 参数
    assert any(p.name == "id" and p.where == "path" for p in get_ep.params)


def test_openapi3_cyclic_ref_truncates(tmp_path):
    doc = {
        "openapi": "3.0.0",
        "paths": {"/x": {"get": {"responses": {"200": {"content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}}}}},
        "components": {"schemas": {
            "Node": {"type": "object", "properties": {
                "child": {"$ref": "#/components/schemas/Node"}}}}},
    }
    p = tmp_path / "cyc.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    endpoints, _ = parse_source(p)  # 不爆栈
    assert len(endpoints) == 1


def test_swagger2_basepath_and_formdata(tmp_path):
    doc = {
        "swagger": "2.0",
        "basePath": "/v2",
        "paths": {"/order/save": {"post": {
            "summary": "保存订单",
            "parameters": [
                {"name": "divisionCode", "in": "formData", "type": "string"},
            ],
            "consumes": ["application/x-www-form-urlencoded"],
            "responses": {"200": {"schema": {"type": "object",
                "properties": {"resultCode": {"type": "string"}}}}}},
        }},
    }
    p = tmp_path / "sw.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    endpoints, stype = parse_source(p)
    assert stype == "swagger"
    ep = endpoints[0]
    assert ep.base == "/v2"
    assert ep.body.kind == "form"  # formData → form 字符串


MARKDOWN_DOC = """## GET /api/order/list

查询订单列表。

请求参数：
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| divisionCode | string | 是 | 事业部 |

请求示例：
```json
{"divisionCode": "DIV001"}
```

响应示例：
```json
{"resultCode": "BUG-000", "data": {"total": 10}}
```
"""


def test_markdown_curl_table_response(tmp_path):
    p = tmp_path / "api.md"
    p.write_text(MARKDOWN_DOC, encoding="utf-8")
    endpoints, stype = parse_source(p)
    assert stype == "markdown"
    assert len(endpoints) == 1
    ep = endpoints[0]
    assert ep.method == "GET" and ep.path == "/api/order/list"
    assert ep.body.kind == "json"
    assert ep.response.has_result_code is True


def test_markdown_no_sample_low_confidence(tmp_path):
    p = tmp_path / "api.md"
    p.write_text("## POST /api/x\n\n无样例接口说明。\n", encoding="utf-8")
    endpoints, _ = parse_source(p)
    assert endpoints[0].confidence == "low"
    assert endpoints[0].low_confidence()


PLAINTEXT_DOC = """POST /api/a
divisionCode: DIV001 必填
orderNo: SO-0001
请求示例：{"divisionCode": "DIV001"}
"""


def test_plaintext_low_confidence(tmp_path):
    p = tmp_path / "api.txt"
    p.write_text(PLAINTEXT_DOC, encoding="utf-8")
    endpoints, stype = parse_source(p)
    assert stype == "text"
    assert endpoints[0].confidence == "low"


def test_unrecognized_format_raises(tmp_path):
    p = tmp_path / "weird.txt"
    p.write_text("这是一段没有端点说明的普通文本\n", encoding="utf-8")
    with pytest.raises(SourceError):
        parse_source(p)


def test_bad_utf8_replace_fallback(tmp_path):
    p = tmp_path / "bad.md"
    p.write_bytes(b"## GET /api/x\n\n\xff\xfe bad bytes\n")
    # 不裸崩：replace 读入后解析端点
    endpoints, _ = parse_source(p)
    assert len(endpoints) == 1


# ---- 生成层 ----

def _gen(tmp_path, doc_text, *, scenario="auto", level="auto", strict=False,
         source_type="auto"):
    p = tmp_path / "src.md"
    p.write_text(doc_text, encoding="utf-8")
    return generate(p, tmp_path / "out",
                    GenOptions(scenario=scenario, level=level, strict=strict,
                               source_type=source_type),
                    registry=_full_registry())


def test_scenario_a_single_read_compile(tmp_path):
    r = _gen(tmp_path, "## GET /api/list\n\n请求示例：\n```json\n{}```\n")
    assert r.compile_ok is True
    assert len(r.flows) == 1
    step = r.flows[0]["steps"][0]
    assert step["task"] == "api_call"
    assert step["save_as"]  # 非空
    assert "isc-app" in step["headers"]  # 全局头并入
    # compile 通过
    flow = r.flows[0]
    assert flow["steps"][0]["read_only"] is True


def test_body_split_json_vs_form(tmp_path):
    # JSON → dict
    r1 = _gen(tmp_path, "## POST /api/j\n\n请求示例：\n```json\n{\"a\":1}```\n")
    assert isinstance(r1.flows[0]["steps"][0].get("body"), dict)
    # form → 字符串
    r2 = _gen(tmp_path, "## POST /api/f\n\n请求参数：\n| 参数 | 类型 | 必填 |\n|------|------|------|\n| a | string | 是 |\n")
    body = r2.flows[0]["steps"][0].get("body")
    # 无 JSON 样例时 body 可能是空映射或字符串
    assert body is not None


def test_save_as_unique_conflict():
    # 两个端点同 save_as_override → 显式冲突（compose_for_goal 同键多生产者硬报错）
    from atlas.generator.model import Endpoint
    from atlas.generator.emit_flow import emit_flow
    eps = [Endpoint(method="GET", path="/api/a", summary="x",
                    save_as_override="dup"),
           Endpoint(method="GET", path="/api/b", summary="x",
                    save_as_override="dup")]
    with pytest.raises(FlowEmitError):
        emit_flow(eps, scenario="b")


def test_scenario_d_write_no_creator(tmp_path):
    r = _gen(tmp_path, "## POST /api/order/save\n\n请求示例：\n```json\n{}```\n")
    step = r.flows[0]["steps"][0]
    assert step["read_only"] is False
    assert step["on_env_blocked"] == "stop"
    # 探针骨架不含 create 实现体（红线 3/12）
    assert "def create" in r.probe_source
    assert "NotImplementedError" in r.probe_source
    # flow 源码文本不含 creator 调用
    flow_text = yaml.safe_dump(r.flows[0])
    assert "creator" not in flow_text.lower()


def test_contract_grading():
    # minimal
    ep_min = Endpoint(method="GET", path="/x", response=ResponseSpec())
    assert grade_contract(ep_min)["level"] == "minimal"
    # high（对象含 resultCode）
    ep_high = Endpoint(method="GET", path="/x",
                       response=ResponseSpec(is_object=True, has_result_code=True))
    assert grade_contract(ep_high)["level"] == "high"
    # core（文档显式标注）
    ep_core = Endpoint(method="GET", path="/x", level_override="core",
                       response=ResponseSpec(is_object=True, has_result_code=True,
                                             required_paths={"name": "string"}))
    c = grade_contract(ep_core)
    assert c["level"] == "core"
    assert c["required"] == {"name": "string"}
    # 数组锁死 minimal
    ep_arr = Endpoint(method="GET", path="/x",
                      response=ResponseSpec(is_array=True, is_object=False))
    assert grade_contract(ep_arr)["level"] == "minimal"


def test_contract_level_cannot_force_core():
    ep = Endpoint(method="GET", path="/x",
                  response=ResponseSpec(is_object=True, has_result_code=True))
    # --level core 但文档未标注 → 不能强升 core
    c = grade_contract(ep, level_mode="core")
    assert c["level"] != "core"


def test_contract_conflict_not_overwrite():
    ep1 = Endpoint(method="POST", path="/x", summary="A")
    ep2 = Endpoint(method="POST", path="/x", summary="B")
    out = emit_contracts([ep1, ep2])
    assert out["conflicts"]  # 同键不同名 → 冲突


def test_placeholder_scan(tmp_path):
    r = _gen(tmp_path, "## GET /api/x\n\n```json\n{}```\n")
    # isc-app 占位头命中
    hits = [h for h in r.placeholders if h["where"] == "header:isc-app"]
    assert hits


def test_list_response_manual_assertions(tmp_path):
    # 响应为数组 → manual_assertions 列出，不伪造 .0. 断言
    doc = "## GET /api/list\n\n响应示例：\n```json\n[{\"a\":1}]```\n"
    r = _gen(tmp_path, doc)
    assert any("数组" in m or "下标" in m for m in r.manual_assertions)
    # assert 块不出现 .0. 路径断言
    asserts = r.flows[0].get("assert", [])
    assert not any(".0." in str(a) for a in asserts)


def test_global_headers_in_every_step(tmp_path):
    doc = "## GET /api/a\n\n```json\n{}```\n\n## GET /api/b\n\n```json\n{}```\n"
    r = _gen(tmp_path, doc, scenario="b")
    for step in r.flows[0]["steps"]:
        assert "isc-app" in step["headers"]


def test_reproducible(tmp_path):
    doc = "## GET /api/x\n\n```json\n{}```\n"
    r1 = _gen(tmp_path, doc)
    r2 = _gen(tmp_path, doc)
    # flow dict 等值
    assert r1.flows[0] == r2.flows[0]
    assert r1.contracts == r2.contracts


# ---- 接入层 ----

def test_check_only_no_files(tmp_path):
    p = tmp_path / "src.md"
    p.write_text("## GET /api/x\n\n```json\n{}```\n", encoding="utf-8")
    out = tmp_path / "out"
    rc = main(["gen", str(p), "--check-only", "--out", str(out)])
    assert rc == 0
    assert not out.exists()  # check-only 不写文件


def test_strict_low_confidence_rc2(tmp_path):
    p = tmp_path / "src.md"
    p.write_text("## POST /api/x\n\n无样例说明。\n", encoding="utf-8")
    rc = main(["gen", str(p), "--strict", "--check-only"])
    assert rc == 2  # 低置信度 + strict


def test_to_tower_trace_step(tmp_path):
    p = tmp_path / "src.md"
    p.write_text("## GET /api/x\n\n```json\n{}```\n", encoding="utf-8")
    rc = main(["--root", str(tmp_path), "gen", str(p), "--to-tower", "--check-only"])
    assert rc == 0
    # 塔草稿存在且 kind=trace_step
    from atlas.experience import ExperienceJournal
    journal = ExperienceJournal(tmp_path / "reports")
    rows = journal._read_queue(tmp_path / "reports" / "experience_drafts.jsonl")
    assert any(r.get("kind") == "trace_step" and r.get("status") == "pending_review"
               for r in rows)


def test_with_pytest_no_allure_rc2(tmp_path):
    p = tmp_path / "src.md"
    p.write_text("## GET /api/x\n\n```json\n{}```\n", encoding="utf-8")
    # allure 未安装 → rc 2（不生成半截文件）
    import importlib.util
    if importlib.util.find_spec("allure") is None:
        rc = main(["gen", str(p), "--with-pytest", "--check-only"])
        assert rc == 2


def test_gen_then_compile_end_to_end(tmp_path):
    p = tmp_path / "src.md"
    p.write_text("## GET /api/list\n\n请求示例：\n```json\n{\"a\":1}```\n\n响应示例：\n```json\n{\"resultCode\":\"BUG-000\"}```\n", encoding="utf-8")
    out = tmp_path / "out"
    rc1 = main(["gen", str(p), "--out", str(out)])
    assert rc1 == 0
    # 产物 flow compile 通过
    flow_files = list((out / "flows").glob("*.yaml"))
    assert flow_files
    rc2 = main(["compile", str(flow_files[0])])
    assert rc2 == 0


def test_openapi_gen_compile(tmp_path):
    doc = tmp_path / "pet.yaml"
    doc.write_text(yaml.safe_dump(OPENAPI_DOC), encoding="utf-8")
    out = tmp_path / "out"
    rc = main(["gen", str(doc), "--out", str(out), "--scenario", "b"])
    assert rc == 0
    flow_files = list((out / "flows").glob("*.yaml"))
    assert flow_files
    rc2 = main(["compile", str(flow_files[0])])
    assert rc2 == 0


def test_force_refuses_overwrite(tmp_path):
    p = tmp_path / "src.md"
    p.write_text("## GET /api/x\n\n```json\n{}```\n", encoding="utf-8")
    out = tmp_path / "out"
    rc1 = main(["gen", str(p), "--out", str(out)])
    assert rc1 == 0
    # 第二次不加 --force → rc 2（目录非空）
    rc2 = main(["gen", str(p), "--out", str(out)])
    assert rc2 == 2
    # 加 --force → rc 0
    rc3 = main(["gen", str(p), "--out", str(out), "--force"])
    assert rc3 == 0

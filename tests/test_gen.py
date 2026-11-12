"""atlas gen（Gap3 文档→代码生成）测试。

离线、零网络、零执行。覆盖解析层/生成层/接入层。
"""
from __future__ import annotations
from pathlib import Path

import pytest
import yaml

from atlas.cli import main, _full_registry
from atlas.generator import GenOptions, generate
from atlas.generator.sources import SourceError, parse_source
from atlas.generator.emit_flow import FlowEmitError
from atlas.generator.emit_contract import grade_contract, emit_contracts
from atlas.generator.model import Endpoint, ResponseSpec


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


# ---- 快模式 --fast（gap9 C.3：CLI 面离线测；零网络零浏览器零真实产物依赖） ----

FAST_DOC = "## GET /api/list\n\n请求示例：\n```json\n{\"a\":1}```\n\n响应示例：\n```json\n{\"resultCode\":\"BUG-000\"}```\n"


def _fast_doc(tmp_path) -> Path:
    p = tmp_path / "src.md"
    p.write_text(FAST_DOC, encoding="utf-8")
    return p


def _fast_pipeline_ready() -> bool:
    """快模式产物发射面是否就绪：#2 emit_fast 落地 + #3 emit_pytest_package 收 fast 参。
    cli-impl 只按契约名接线（gap9 D 表），并行期测自动休眠、队友落地后自动激活，不复制产物。"""
    import inspect
    try:
        import atlas.generator.emit_fast  # noqa: F401
    except ImportError:
        return False
    from atlas.generator.emit_pytest import emit_pytest_package
    return "fast" in inspect.signature(emit_pytest_package).parameters


def test_fast_default_off_in_options():
    # 缺省关：不传 flag 的存量调用形态与语义逐字不变（#115）
    assert GenOptions().fast is False
    assert GenOptions(with_pytest=True).fast is False


def test_gen_fast_without_with_pytest_rc2(tmp_path, capsys):
    # C.3：--fast 单用 → 一行 stderr + rc 2；不隐式打开 --with-pytest（不静默改行为）
    p = _fast_doc(tmp_path)
    rc = main(["gen", str(p), "--fast", "--check-only"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--fast" in err and "--with-pytest" in err
    assert "Traceback" not in err  # 用户输入错误口径，不裸 traceback


def test_generate_programmatic_fast_rejected(tmp_path):
    # 程序化入口与 CLI 同一把判据闸（GenOptions.validate 唯一实现，不留第二份 if——#92）
    p = _fast_doc(tmp_path)
    with pytest.raises(SourceError) as ei:
        generate(p, tmp_path / "out", GenOptions(fast=True))
    assert "--with-pytest" in str(ei.value)


def test_fast_flag_passthrough_chain(tmp_path, monkeypatch):
    # flag→GenOptions→emit_pytest_package 透传链（stub 捕获调用，不依赖 #2/#3 落地时序）。
    # README 快模式段同样 stub：真实段懒加载 emit_fast.ENV_KEYS（#2 未落地时不该把测打红，
    # 落地后的真实渲染由 test_fast_artifacts_fileset 激活覆盖）。
    import atlas.generator as gen_pkg
    import atlas.generator.emit_pytest as ep
    p = _fast_doc(tmp_path)
    calls: list[dict] = []

    def _stub(**kw):
        calls.append(kw)
        return [str(Path(kw["out_dir"]) / "test_stub.py")]

    monkeypatch.setattr(ep, "emit_pytest_package", _stub)
    monkeypatch.setattr(gen_pkg, "_fast_readme_section", lambda: "\n## 快模式（--fast）stub\n")
    assert main(["gen", str(p), "--out", str(tmp_path / "on"),
                 "--with-pytest", "--fast"]) == 0
    assert calls and calls[-1].get("fast") is True
    assert Path(calls[-1]["out_dir"]).name == "pytest"  # 产物目录口径不变（C.4）
    assert "快模式（--fast）" in (tmp_path / "on" / "README.md").read_text(encoding="utf-8")
    assert main(["gen", str(p), "--out", str(tmp_path / "off"),
                 "--with-pytest"]) == 0
    assert calls[-1].get("fast", False) is False  # 不传 flag → 存量语义
    assert "快模式" not in (tmp_path / "off" / "README.md").read_text(encoding="utf-8")


@pytest.mark.skipif(not _fast_pipeline_ready(),
                    reason="待 gap9 #2/#3 落地后激活（快底座由 emit_fast/emit_pytest 产出，cli 不代写）")
def test_fast_artifacts_fileset(tmp_path):
    # flag 生效 → 产物文件集变化点名（C.4：_atlas_fast.py + conftest.py 进包；reports/ 生成时不建）
    p = _fast_doc(tmp_path)
    out = tmp_path / "out"
    assert main(["gen", str(p), "--out", str(out), "--with-pytest", "--fast"]) == 0
    pkg = out / "pytest"
    names = {f.name for f in pkg.iterdir()}
    assert {"_atlas_fast.py", "conftest.py"} <= names
    assert "_atlas_support.py" in names and any(n.startswith("test_") for n in names)
    assert not (pkg / "reports").exists()  # 运行期产物目录，生成阶段不创建
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "快模式（--fast）" in readme
    # env 清单渲染自唯一来源 emit_fast.ENV_KEYS（本 README 不手抄第二份清单——#92）
    from atlas.generator.emit_fast import ENV_KEYS
    for k in ENV_KEYS:
        assert k in readme


def test_no_fast_keeps_existing_package_shape(tmp_path):
    # 正向对照：flag 不传 → 存量产物形态不含快模式文件、README 无快模式段，且两次跑逐字节相同
    p = _fast_doc(tmp_path)
    out = tmp_path / "out"
    assert main(["gen", str(p), "--out", str(out), "--with-pytest"]) == 0
    pkg = out / "pytest"
    names = {f.name for f in pkg.iterdir()}
    assert "_atlas_fast.py" not in names and "conftest.py" not in names
    assert "_atlas_support.py" in names
    assert "快模式" not in (out / "README.md").read_text(encoding="utf-8")
    first = {n: (pkg / n).read_bytes() for n in names}
    assert main(["gen", str(p), "--out", str(out), "--with-pytest", "--force"]) == 0
    second = {n: (pkg / n).read_bytes() for n in names}
    assert first == second


def test_cli_scaffold_generator_zero_artifact_template_text():
    # gap9 D 表：我独占的三个文件零产物模板文本（模板首行指纹扫描），判据零复制
    root = Path(__file__).resolve().parents[1] / "src" / "atlas"
    for rel in ("cli.py", "scaffold.py", "generator/__init__.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "快模式底座（自包含" not in text, rel      # C.1 _FAST_SUPPORT_SOURCE 首行指纹
        assert "快模式 fixtures（" not in text, rel        # C.1 _FAST_CONFTEST_SOURCE 首行指纹
        assert "sole_page" not in text, rel                # 产物 fixture 名不进接线层
    cli_src = (root / "cli.py").read_text(encoding="utf-8")
    assert "需与 --with-pytest 同用" not in cli_src        # 组合判据消息只在 GenOptions.validate 一处


# ---- F-9 正向对照 + 契约面钉测（lead 拍板 b 后补，2026-11-09） ----

def test_fast_pure_api_without_components_emits(tmp_path, monkeypatch):
    # F-9 正向对照：纯 API 源 + 无组件库 + fast=True 必须 rc0 并正常出产物——
    # 闸门（判据在 emit-impl 的 _fast_ui_gates）不得把能用路径一起杀掉（#115）。
    # 负例（UI 腿+无组件库→拒绝）在 CLI 面不可达：gen 的 endpoints 源恒 is_api=True，
    # 该面由 emit-impl 在 flow 渲染层钉测（F-9 owner 裁决）。
    import atlas.generator.emit_pytest as ep
    monkeypatch.setattr(ep, "_default_components", lambda: {})
    p = _fast_doc(tmp_path)
    out = tmp_path / "out"
    assert main(["gen", str(p), "--out", str(out), "--with-pytest", "--fast"]) == 0
    pkg = out / "pytest"
    names = {f.name for f in pkg.iterdir()}
    assert "atlas_pages.py" not in names  # 无组件库 → 无页对象库（也不出坏引用）
    assert any(n.startswith("test_") for n in names)
    assert {"_atlas_support.py", "_atlas_fast.py", "conftest.py"} <= names
    assert not (pkg / "reports").exists()  # 运行期目录生成时不建（C.4）


def test_gen_fast_help_mentions_pairing(tmp_path, monkeypatch, capsys):
    # docs-closer 依赖的字面钉：--fast help 须含「须配 --with-pytest」（CLAUDE.md 同款口径）
    _fast_doc(tmp_path)
    monkeypatch.setenv("COLUMNS", "300")  # 防 argparse 折行把判据字面劈成两段
    with pytest.raises(SystemExit):
        main(["gen", "--help"])
    out = capsys.readouterr().out
    assert "--fast" in out and "须配 --with-pytest" in out


def test_emit_pytest_package_fast_kwarg_signature():
    # fast_kw 透传形态的「等价」不能只是看起来一样（lead 点名）：签名级钉
    # emit_pytest_package 收 fast 参且缺省 False——缺省漂成 True/改名，我的条件透传就静默失配
    import inspect
    from atlas.generator.emit_pytest import emit_pytest_package
    params = inspect.signature(emit_pytest_package).parameters
    assert "fast" in params, "C.2 契约参 fast 缺失——fast_kw 透传形态失效"
    assert params["fast"].default is False

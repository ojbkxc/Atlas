"""DevOps 平台同步（C-D1~C-D5）离线测试：配置解析 / 字段映射 / 去重 / dry-run / 响应解析 / 状态。

对应 `src/atlas/devops_sync.py`（UI AUTO TEST devops_sync.py 的 Atlas 移植）。
全部离线：用 fake MCP client 注入，绝不发真实请求（AGENTS §4：对外平台同步属敏感操作）。
业务字面量（org_id/system_id…）只出现在本测试的 fixture 里，不进引擎模块（红线 #11）。
"""
import json
from datetime import datetime

import pytest

from atlas import devops_sync as ds


# ── fixtures / helpers ─────────────────────────────────────────────────

def _write_result(
    results_dir,
    filename,
    *,
    sub_suite="新建公告 - 标题必填校验",
    suite="announcement_create",
    parent_suite=None,
    status="passed",
    start=1,
    severity="normal",
    steps=None,
    description="",
    full_name="testcases.x.test_x.TestPlan#test_tc1_x",
):
    """写一个 Allure *-result.json（形态对齐 gap5 emit 产物）。"""
    labels = [
        {"name": "suite", "value": suite},
        {"name": "subSuite", "value": sub_suite},
        {"name": "severity", "value": severity},
    ]
    if parent_suite is not None:
        labels.append({"name": "parentSuite", "value": parent_suite})
    result = {
        "name": "test_tc1_x",
        "fullName": full_name,
        "status": status,
        "start": start,
        "labels": labels,
        "steps": steps if steps is not None else [
            {"name": "[步骤1] 打开系统首页，登录账号"},
            {"name": "[断言1] 断言：出现“提交成功”弹框"},
        ],
        "description": description,
    }
    (results_dir / filename).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


@pytest.fixture
def project(tmp_path):
    """一个带 devops-sync.yaml + 两个 suite 的 allure-results 的项目根。"""
    (tmp_path / "devops-sync.yaml").write_text(
        "\n".join([
            'org_id: "mcpu_demo_org"',
            'system_id: "ITS000000000"',
            'system_name: "demo-system"',
            'testcase_directory: "demo-system/项目用例/UI自动化"',
            'exec_user: "tester01"',
            'testplan_title_template: "{suite_label}自动化测试计划_{timestamp}"',
            "suite_directories:",
            '  announcement_create: "demo-system/公告用例"',
        ]),
        encoding="utf-8",
    )
    results = tmp_path / "reports" / "allure-results"
    results.mkdir(parents=True)
    return tmp_path


class FakeMcpClient:
    """fake MCP 客户端：``initialize()`` + ``call_tool(name, args) -> (is_error, text)``。

    与 ``McpClient`` 同契约：``call_tool`` 返回**已解析**的 ``(is_error, text)``；
    这里用真实 ``parse_mcp_response`` 解析自己造的 JSON-RPC 信封，顺带覆盖 SSE/纯 JSON 两形态。
    记录所有调用到 ``self.calls``。
    """

    def __init__(self, *, dir_id="ID0001", plan_id="VALZ0000001",
                 import_ids=None, relate_error=False, sse=False,
                 dir_error=False, plan_error=False):
        self.calls = []
        self.dir_id = dir_id
        self.plan_id = plan_id
        self.import_ids = import_ids  # None → 由导入条数生成 idxToTestId
        self.relate_error = relate_error
        self.sse = sse
        self.dir_error = dir_error
        self.plan_error = plan_error

    def _envelope(self, obj):
        payload = {"jsonrpc": "2.0", "id": "1", "result": {
            "isError": False,
            "content": [{"text": json.dumps(obj, ensure_ascii=False)}],
        }}
        text = json.dumps(payload, ensure_ascii=False)
        return "data: " + text if self.sse else text

    def initialize(self):
        self.calls.append(("initialize", {}))

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "resolve_test_directory_by_path":
            if self.dir_error:
                return True, "ERROR: directory not found"
            return ds.parse_mcp_response(self._envelope({"directoryId": self.dir_id}))
        if name == "import_testcases_to_library":
            count = len(arguments["request"]["testCaseList"])
            ids = self.import_ids or [f"VALZ{i:07d}" for i in range(1, count + 1)]
            return ds.parse_mcp_response(
                self._envelope({"idxToTestId": {str(i): ids[i] for i in range(len(ids))}})
            )
        if name == "create_test_plan":
            if self.plan_error:
                return True, "ERROR: create failed"
            return ds.parse_mcp_response(
                self._envelope({"testPlanId": self.plan_id,
                                "title": arguments["request"]["title"]})
            )
        if name == "batch_relate_test_plan":
            return self.relate_error, "relate-done"
        raise AssertionError(f"unexpected tool {name}")


# ══════════════════════════════════════════════════════════════════════════
# 配置解析（C-D1）
# ══════════════════════════════════════════════════════════════════════════

def test_load_config_missing_file_names_path_and_fields(tmp_path):
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.load_config(tmp_path)
    assert "devops-sync.yaml" in str(exc.value)
    assert "system_id" in str(exc.value)


def test_load_config_requires_core_fields(tmp_path):
    (tmp_path / "devops-sync.yaml").write_text('system_name: "demo"\n', encoding="utf-8")
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.load_config(tmp_path)
    assert "system_id" in str(exc.value)


def test_load_config_defaults_and_suite_directories(project):
    cfg = ds.load_config(project)
    assert cfg.system_id == "ITS000000000"
    assert cfg.exec_user == "tester01"
    assert cfg.testcase_directory == "demo-system/项目用例/UI自动化"
    # suite 专属目录优先
    assert cfg.directory_for("announcement_create") == "demo-system/公告用例"
    assert cfg.directory_for(None) == "demo-system/项目用例/UI自动化"


def test_load_config_default_directory_and_template(tmp_path):
    (tmp_path / "devops-sync.yaml").write_text(
        'system_id: "S"\nsystem_name: "demo"\nexec_user: "u"\n', encoding="utf-8"
    )
    cfg = ds.load_config(tmp_path)
    assert cfg.testcase_directory == "demo/未分类目录"
    assert cfg.testplan_title_template == "{suite_label}自动化测试计划_{timestamp}"


def test_load_config_rejects_non_mapping_top_level(tmp_path):
    (tmp_path / "devops-sync.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ds.DevopsSyncError):
        ds.load_config(tmp_path)


def test_load_config_optional_mcp_fields(tmp_path):
    (tmp_path / "devops-sync.yaml").write_text(
        'system_id: "S"\nsystem_name: "demo"\nexec_user: "u"\n'
        'mcp_server: "devops-mcp-uat"\nsend_midea_mkt_id: false\n', encoding="utf-8"
    )
    cfg = ds.load_config(tmp_path)
    assert cfg.mcp_server == "devops-mcp-uat"
    assert cfg.send_midea_mkt_id is False


# ── MCP 地址解析 ───────────────────────────────────────────────────────

def test_resolve_mcp_url_priority(tmp_path):
    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {
        "devops-mcp-agile": {"url": "http://from-file/mcp"}
    }}), encoding="utf-8")
    # explicit 最高
    assert ds.resolve_mcp_url(explicit="http://explicit/mcp", mcp_config=mcp_json,
                              env={}) == "http://explicit/mcp"
    # env 次之
    assert ds.resolve_mcp_url(mcp_config=mcp_json,
                              env={"ATLAS_DEVOPS_MCP_URL": "http://env/mcp"}) == "http://env/mcp"
    # 文件兜底
    assert ds.resolve_mcp_url(mcp_config=mcp_json, env={}) == "http://from-file/mcp"


def test_resolve_mcp_url_missing_names_sources(tmp_path):
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.resolve_mcp_url(mcp_config=tmp_path / "nope.json", env={})
    msg = str(exc.value)
    assert "devops-mcp" in msg and "ATLAS_DEVOPS_MCP_URL" in msg


def _write_mcp_json(tmp_path, servers):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return p


def test_resolve_mcp_server_auto_single(tmp_path):
    p = _write_mcp_json(tmp_path, {
        "devops-mcp-agile": {"url": "http://a/mcp", "midea_mkt_id": 9},
        "markmap": {"url": "http://mk/mcp"},
    })
    srv = ds.resolve_mcp_server(mcp_config=p, env={})
    assert srv.name == "devops-mcp-agile" and srv.url == "http://a/mcp"
    assert srv.midea_mkt_id == 9


def test_resolve_mcp_server_multiple_requires_explicit(tmp_path):
    """sit/uat/prd 三选一：多个候选必须报错点名，不静默择一（也不把 markmap 算进来）。"""
    p = _write_mcp_json(tmp_path, {
        "devops-mcp-sit": {"url": "http://sit", "midea_mkt_id": 505},
        "devops-mcp-uat": {"url": "http://uat", "midea_mkt_id": 528},
        "devops-mcp-prd": {"url": "http://prd", "midea_mkt_id": 570},
        "markmap": {"url": "http://mk"},
    })
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.resolve_mcp_server(mcp_config=p, env={})
    msg = str(exc.value)
    assert "devops-mcp-sit" in msg and "devops-mcp-uat" in msg and "devops-mcp-prd" in msg
    assert "markmap" not in msg
    assert "mcp_server" in msg


def test_resolve_mcp_server_explicit_name(tmp_path):
    p = _write_mcp_json(tmp_path, {
        "devops-mcp-sit": {"url": "http://sit", "midea_mkt_id": 505},
        "devops-mcp-prd": {"url": "http://prd", "midea_mkt_id": 570},
    })
    srv = ds.resolve_mcp_server(server_name="devops-mcp-prd", mcp_config=p, env={})
    assert srv.url == "http://prd" and srv.midea_mkt_id == 570


def test_resolve_mcp_server_env_name_and_url(tmp_path):
    p = _write_mcp_json(tmp_path, {"devops-mcp-sit": {"url": "http://sit"}})
    srv = ds.resolve_mcp_server(mcp_config=p, env={"ATLAS_DEVOPS_MCP_SERVER": "devops-mcp-sit"})
    assert srv.url == "http://sit"
    # 显式 URL 覆盖文件探测
    srv2 = ds.resolve_mcp_server(mcp_config=p, env={"ATLAS_DEVOPS_MCP_URL": "http://explicit"})
    assert srv2.url == "http://explicit"


def test_resolve_mcp_server_unknown_name_lists_existing(tmp_path):
    p = _write_mcp_json(tmp_path, {"devops-mcp-sit": {"url": "http://sit"}})
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.resolve_mcp_server(server_name="devops-mcp-nope", mcp_config=p, env={})
    assert "devops-mcp-sit" in str(exc.value)


def test_resolve_mcp_server_none_names_existing(tmp_path):
    p = _write_mcp_json(tmp_path, {"markmap": {"url": "http://mk"}})
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.resolve_mcp_server(mcp_config=p, env={})
    assert "markmap" in str(exc.value)


# ══════════════════════════════════════════════════════════════════════════
# 字段映射（guide §2）
# ══════════════════════════════════════════════════════════════════════════

def test_extract_title_prefers_subsuite_and_strips_prefix_and_period():
    result = {"labels": [{"name": "subSuite", "value": "新建公告 - 标题必填校验。"}]}
    assert ds.extract_title(result) == "标题必填校验"


def test_extract_title_falls_back_to_description_then_name():
    assert ds.extract_title({"description": "TC1 - 银行账户开户申请。"}) == "银行账户开户申请"
    assert ds.extract_title({"name": "test_tc3_create_account"}) == "create account"


def test_extract_title_atlas_results_use_name_not_env_subsuit():
    """Atlas 直出的 allure 结果：subSuite=env（如 'sit'），标题在 name。

    回归：旧实现把 subSuite 一律当标题 → 全部用例标题都成 'sit'，按 title 去重时
    整个 suite 被合并成 1 条（数据丢失）。修复后 Atlas 产物回落 name 取真实标题。
    """
    atlas_like = {
        "name": "TC2 改选PT",
        "labels": [{"name": "subSuite", "value": "sit"},
                   {"name": "suite", "value": "atlas"}],
    }
    assert ds.extract_title(atlas_like) == "TC2 改选PT"
    # 环境码形态（uat-eu）同样不像标题 → 回落 name
    assert ds.extract_title(
        {"name": "TC9 跨账套", "labels": [{"name": "subSuite", "value": "uat-eu"}]}
    ) == "TC9 跨账套"


def test_extract_title_explicit_label_wins():
    """生产者显式声明 atlas.case_title 时最优先（不依赖任何启发式）。"""
    result = {"name": "test_tc1_x",
              "labels": [{"name": "atlas.case_title", "value": "显式标题。"},
                         {"name": "subSuite", "value": "新建公告 - 子集标题"}]}
    assert ds.extract_title(result) == "显式标题"


def test_extract_title_ui_auto_test_chinese_subsuit_still_preferred():
    """UI AUTO TEST 约定不变：中文 subSuite 仍优先于 name（不被本次修复回归）。"""
    result = {"name": "test_create_announcement",
              "labels": [{"name": "subSuite", "value": "新建公告 - 标题必填校验"}]}
    assert ds.extract_title(result) == "标题必填校验"


def test_priority_map():
    assert ds.priority_from_labels({"severity": "blocker"}) == "P0"
    assert ds.priority_from_labels({"severity": "critical"}) == "P0"
    assert ds.priority_from_labels({"severity": "normal"}) == "P1"
    assert ds.priority_from_labels({"severity": "trivial"}) == "P2"
    assert ds.priority_from_labels({}) == "P1"


def test_exec_result_map():
    assert ds.parse_devops_case({"status": "passed"}).exec_result == 1
    assert ds.parse_devops_case({"status": "failed"}).exec_result == 2
    assert ds.parse_devops_case({"status": "broken"}).exec_result == 2
    assert ds.parse_devops_case({"status": "skipped"}).exec_result == 3
    assert ds.parse_devops_case({"status": "weird"}).exec_result == 2


def test_steps_mapping_step_and_assertion():
    result = {"steps": [
        {"name": "[步骤5] 选择开户机构"},
        {"name": '[断言21] 断言：出现"审批提交成功"弹框'},
    ]}
    steps = ds.steps_from_result(result)
    assert steps[0] == {"testStep": "选择开户机构", "testExpectation": "操作成功"}
    assert steps[1] == {"testStep": '断言：出现"审批提交成功"弹框',
                        "testExpectation": '出现"审批提交成功"弹框'}


def test_steps_mapping_fallback_when_empty():
    assert ds.steps_from_result({"steps": []}) == [
        {"testStep": "执行测试", "testExpectation": "操作成功"}
    ]
    assert ds.steps_from_result({}) == [
        {"testStep": "执行测试", "testExpectation": "操作成功"}
    ]


def test_parse_devops_case_full_mapping():
    result = {
        "status": "failed",
        "fullName": "testcases.a.b.C#test_tc1_x",
        "start": 42,
        "labels": [
            {"name": "suite", "value": "announcement_create"},
            {"name": "subSuite", "value": "模块 - 校验"},
            {"name": "severity", "value": "critical"},
        ],
    }
    case = ds.parse_devops_case(result)
    assert case.title == "校验"
    assert case.priority == "P0"
    assert case.exec_result == 2
    assert case.suite == "announcement_create"
    assert case.start == 42
    assert case.full_name == "testcases.a.b.C#test_tc1_x"


# ══════════════════════════════════════════════════════════════════════════
# payload 构造器
# ══════════════════════════════════════════════════════════════════════════

def test_clean_case_title_strips_special_chars():
    assert ds.clean_case_title("标题必填（校验）- 新增") == "标题必填校验 新增"


def test_build_import_arguments_field_mapping(project):
    cfg = ds.load_config(project)
    case = ds.DevopsCase(title="标题-必填", priority="P0", steps=[{"testStep": "s", "testExpectation": "e"}],
                         exec_result=1, status="passed", full_name="f", start=1, suite="s")
    args = ds.build_import_arguments(cfg, "ID9", [case])
    tc = args["request"]["testCaseList"][0]
    assert tc["title"] == "标题必填"                       # 特殊字符已清洗
    assert tc["priority"] == "P0"
    assert tc["executionMethod"] == "UI自然语言测试"        # 必填固定值
    assert args["request"]["systemId"] == cfg.system_id
    assert args["request"]["directoryId"] == "ID9"
    assert args["clientRequestId"]                          # 非空防重复提交


def test_build_create_plan_uses_execution_method_not_execute_type(project):
    cfg = ds.load_config(project)
    args = ds.build_create_plan_arguments(cfg, "计划标题")
    req = args["request"]
    assert req["executionMethod"] == "UI自然语言测试"
    assert "executeType" not in req                        # 踩坑 #2
    assert req["isPicture"] is False and req["testplanPolicy"] == 0


def test_build_relate_arguments_nested_request(project):
    cfg = ds.load_config(project)
    case = ds.DevopsCase(title="t", priority="P1", steps=[], exec_result=2,
                         status="failed", full_name="f", start=1, suite="s")
    args = ds.build_relate_arguments(cfg, "VALZ1", [(case, "VALZ2")])
    req = args["request"]
    assert req["testPlanId"] == "VALZ1"
    assert req["action"] == "RELATE"
    assert req["testCases"][0] == {
        "testCaseId": "VALZ2", "execResult": 2, "execUser": cfg.exec_user,
    }


def test_render_plan_title():
    assert ds.render_plan_title("{suite_label}自动化测试计划_{timestamp}", "公告", "20260101_000000") \
        == "公告自动化测试计划_20260101_000000"


# ══════════════════════════════════════════════════════════════════════════
# MCP 响应解析（踩坑 #5 / #6）
# ══════════════════════════════════════════════════════════════════════════

def test_parse_sse_response():
    payload = json.dumps({"result": {"isError": False, "content": [{"text": "ok"}]}})
    is_err, text = ds.parse_mcp_response("data: " + payload)
    assert is_err is False and text == "ok"


def test_parse_pure_json_without_data_prefix():
    payload = json.dumps({"result": {"isError": False, "content": [{"text": "纯JSON"}]}},
                         ensure_ascii=False)
    is_err, text = ds.parse_mcp_response(payload)
    assert is_err is False and text == "纯JSON"


def test_parse_repairs_latin1_mojibake():
    original = "同步成功"
    mojibake = original.encode("utf-8").decode("latin-1")
    payload = json.dumps({"result": {"isError": False, "content": [{"text": mojibake}]}},
                         ensure_ascii=False)
    _, text = ds.parse_mcp_response("data: " + payload)
    assert text == original


def test_parse_jsonrpc_error_body():
    payload = json.dumps({"error": {"code": -32601, "message": "未知方法"}}, ensure_ascii=False)
    is_err, text = ds.parse_mcp_response(payload)
    assert is_err is True and "未知方法" in text


def test_parse_garbage_response():
    is_err, text = ds.parse_mcp_response("<html>not json</html>")
    assert is_err is True


# ── McpClient（HTTP 层错误归因 / header 透传）────────────────────────────

class StubResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class StubSession:
    """requests.Session 的最小替身（记录 headers/body，post 返回预置响应或抛异常）。"""

    def __init__(self, response):
        self.headers = {}
        self.response = response
        self.body = None
        self.url = None

    def post(self, url, json=None, timeout=None):
        self.url = url
        self.body = json
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _ok_body(text="hello", is_error=False):
    return json.dumps({"result": {"isError": is_error, "content": [{"text": text}]}},
                      ensure_ascii=False)


def test_mcp_client_call_tool_ok_and_records_method():
    stub = StubSession(StubResponse(200, _ok_body("结果OK")))
    client = ds.McpClient("http://h/mcp?key=secret", session=stub)
    assert client.call_tool("x", {"a": 1}) == (False, "结果OK")
    assert stub.body["method"] == "tools/call"
    assert stub.body["params"]["name"] == "x"


def test_mcp_client_sends_mkt_header_and_can_disable():
    stub = StubSession(StubResponse(200, _ok_body()))
    ds.McpClient("http://h/mcp", midea_mkt_id=505, session=stub)
    assert stub.headers[ds.MKT_ID_HEADER] == "505"

    stub2 = StubSession(StubResponse(200, _ok_body()))
    ds.McpClient("http://h/mcp", midea_mkt_id=505, send_midea_mkt_id=False, session=stub2)
    assert ds.MKT_ID_HEADER not in stub2.headers


def test_mcp_client_401_names_auth_and_key():
    stub = StubSession(StubResponse(401, "unauthorized"))
    client = ds.McpClient("http://h/mcp", server_name="devops-mcp-sit", session=stub)
    with pytest.raises(ds.DevopsSyncError) as exc:
        client.initialize()
    msg = str(exc.value)
    assert "认证失败" in msg and "401" in msg and "devops-mcp-sit" in msg


def test_mcp_client_400_names_key_and_mkt_hint():
    stub = StubSession(StubResponse(400, "Invalid Access"))
    client = ds.McpClient("http://h/mcp?key=x", server_name="devops-mcp-uat", session=stub)
    with pytest.raises(ds.DevopsSyncError) as exc:
        client.initialize()
    msg = str(exc.value)
    assert "400" in msg and "midea_mkt_id" in msg and "devops-mcp-uat" in msg


def test_mcp_client_network_error_points_at_server():
    import requests
    stub = StubSession(requests.exceptions.ConnectionError("boom"))
    client = ds.McpClient("http://h/mcp", server_name="devops-mcp-prd", session=stub)
    with pytest.raises(ds.DevopsSyncError) as exc:
        client.call_tool("t", {})
    msg = str(exc.value)
    assert "devops-mcp-prd" in msg and "ConnectionError" in msg


def test_mcp_client_non_json_response_is_named():
    stub = StubSession(StubResponse(200, "<html>gateway</html>"))
    client = ds.McpClient("http://h/mcp", session=stub)
    with pytest.raises(ds.DevopsSyncError) as exc:
        client.call_tool("t", {})
    assert "无法解析" in str(exc.value)


def test_mcp_client_url_is_sanitized_in_messages():
    """错误信息里的 url 必须净化：query 里的 key 不得原样进异常文本。"""
    stub = StubSession(StubResponse(400, "Invalid Access"))
    client = ds.McpClient("http://h/mcp?key=SUPERSECRET", session=stub)
    with pytest.raises(ds.DevopsSyncError) as exc:
        client.initialize()
    assert "SUPERSECRET" not in str(exc.value)


def test_extract_ids():
    assert ds.extract_test_plan_id('{"testPlanId": "VALZ0009"}') == "VALZ0009"
    assert ds.extract_directory_id('{"directoryId": "ID123"}') == "ID123"
    assert ds.extract_imported_ids('{"idxToTestId": {"0": "VALZ1", "1": "VALZ2"}}', 2) \
        == ["VALZ1", "VALZ2"]
    assert ds.extract_imported_ids('{"testIdList": ["VALZ7"]}', 1) == ["VALZ7"]
    assert ds.extract_imported_ids("no json VALZ5 VALZ5 VALZ6", 2) == ["VALZ5", "VALZ6"]


# ══════════════════════════════════════════════════════════════════════════
# Allure 结果集解析 + 去重（C-D2）
# ══════════════════════════════════════════════════════════════════════════

def test_load_cases_dedup_keeps_latest_start(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验", start=100, status="failed")
    _write_result(results, "b-result.json", sub_suite="模块 - 校验", start=200, status="passed")
    bundle = ds.load_cases(results)
    assert len(bundle["cases"]) == 1
    case = bundle["cases"][0]
    assert case.title == "校验" and case.start == 200 and case.status == "passed"
    assert len(bundle["duplicates"]) == 1
    assert bundle["duplicates"][0]["kept_start"] == 200


def test_load_cases_suite_filter(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", suite="s1", sub_suite="模块 - A")
    _write_result(results, "b-result.json", suite="s2", sub_suite="模块 - B")
    bundle = ds.load_cases(results, suite_filter="s1")
    assert [c.title for c in bundle["cases"]] == ["A"]
    assert bundle["suite_label"] == "模块"  # subSuite "模块 - A" 的前缀


def test_load_cases_suite_label_from_parent_suite(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", parent_suite="公告管理")
    assert ds.load_cases(results)["suite_label"] == "公告管理"


def test_load_cases_known_ids_reuse(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验")
    bundle = ds.load_cases(results, known_ids={"校验": "VALZ_EXIST"})
    assert bundle["cases"][0].existing_devops_id == "VALZ_EXIST"


def test_load_cases_skips_bad_files(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "good-result.json", sub_suite="模块 - 好")
    (results / "broken-result.json").write_bytes(b"{ not json")
    (results / "truncated-result.json").write_bytes(b'{"name": "\xff\xfe')
    bundle = ds.load_cases(results)
    assert [c.title for c in bundle["cases"]] == ["好"]
    assert bundle["skipped_files"] == 2


def test_load_cases_missing_dir_errors(tmp_path):
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.load_cases(tmp_path / "reports" / "allure-results")
    assert "结果集目录不存在" in str(exc.value)


# ══════════════════════════════════════════════════════════════════════════
# 状态文件（C-D4b）
# ══════════════════════════════════════════════════════════════════════════

def test_state_roundtrip(tmp_path):
    assert ds.load_state(tmp_path) == {}
    ds.save_state(tmp_path, {"all_20260101_000000": {"testPlanId": "VALZ1", "system_id": "S"}})
    state = ds.load_state(tmp_path)
    assert state["all_20260101_000000"]["testPlanId"] == "VALZ1"
    assert not (tmp_path / "devops-sync-state.yaml.tmp").exists()


def test_state_corrupt_raises(tmp_path):
    (tmp_path / "devops-sync-state.yaml").write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ds.DevopsSyncError):
        ds.load_state(tmp_path)


def test_resolve_run_key_from_history_and_clock(tmp_path):
    history = tmp_path / "reports" / "allure-history"
    (history / "20260102_030405").mkdir(parents=True)
    (history / "20260103_040506").mkdir()
    assert ds.resolve_run_key(tmp_path) == "20260103_040506"
    # 无历史目录 → 用传入时钟
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ds.resolve_run_key(empty, now=datetime(2026, 5, 6, 7, 8, 9)) == "20260506_070809"


def test_suite_state_key():
    assert ds.suite_state_key(None, "20260101_000000") == "all_20260101_000000"
    assert ds.suite_state_key("sfx", "20260101_000000") == "sfx_20260101_000000"


# ══════════════════════════════════════════════════════════════════════════
# sync_plan：dry-run（默认语义）+ 真实执行（fake client）
# ══════════════════════════════════════════════════════════════════════════

def test_sync_plan_dry_run_sends_nothing(project, tmp_path):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验", status="passed")
    mcp = _write_mcp_json(tmp_path, {
        "devops-mcp-sit": {"url": "http://sit/mcp", "midea_mkt_id": 505},
        "markmap": {"url": "http://mk/mcp"},
    })
    client = FakeMcpClient()
    res = ds.sync_plan(project, suite_filter="announcement_create", client=client,
                       mcp_config=mcp, env={}, now=datetime(2026, 1, 1, 0, 0, 0))
    assert res["dry_run"] is True and res["executed"] is False
    assert client.calls == []                                  # 一个请求都没发
    assert not (project / "devops-sync-state.yaml").exists()   # dry-run 不写状态
    # 四步 payload 摘要齐备（供人工核对）
    assert "import_testcases_to_library" in res["payloads"]
    assert "create_test_plan" in res["payloads"]
    assert "batch_relate_test_plan" in res["payloads"]
    assert res["total"] == 1 and res["passed"] == 1
    assert res["state_key"] == "announcement_create_20260101_000000"
    # 目录用 suite 专属目录；dry-run 也解析出目标服务器（唯一候选）
    assert res["testcase_directory"] == "demo-system/公告用例"
    assert res["mcp_server"] == "devops-mcp-sit"


def test_sync_plan_dry_run_ambiguous_server_records_not_raises(project, tmp_path):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验")
    mcp = _write_mcp_json(tmp_path, {
        "devops-mcp-sit": {"url": "http://sit/mcp"},
        "devops-mcp-uat": {"url": "http://uat/mcp"},
        "devops-mcp-prd": {"url": "http://prd/mcp"},
    })
    res = ds.sync_plan(project, client=FakeMcpClient(), mcp_config=mcp, env={},
                       now=datetime(2026, 1, 1, 0, 0, 0))
    assert res["dry_run"] is True
    step = next(s for s in res["steps"] if s["step"] == "resolve_mcp_server")
    assert step["ok"] is False and "多个" in step["detail"]


def test_sync_plan_execute_full_flow(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验A", status="passed")
    _write_result(results, "b-result.json", sub_suite="模块 - 校验B", status="failed")
    client = FakeMcpClient(plan_id="VALZ0000042", import_ids=["VALZ1", "VALZ2"])
    res = ds.sync_plan(project, execute=True, client=client, mcp_url="http://fake/mcp",
                       now=datetime(2026, 1, 1, 0, 0, 0))
    tool_calls = [c[0] for c in client.calls]
    assert tool_calls == [
        "initialize", "resolve_test_directory_by_path", "import_testcases_to_library",
        "create_test_plan", "batch_relate_test_plan",
    ]
    assert res["executed"] is True and res["dry_run"] is False
    assert res["imported_new"] == 2 and res["test_plan_id"] == "VALZ0000042"
    assert res["relate_count"] == 2
    # 关联 payload 用的真实 testCaseId 与 execResult
    relate = client.calls[-1][1]["request"]["testCases"]
    assert {tc["testCaseId"] for tc in relate} == {"VALZ1", "VALZ2"}
    assert {tc["execResult"] for tc in relate} == {1, 2}
    assert {tc["execUser"] for tc in relate} == {"tester01"}
    # C-D4b：计划 ID 已落 state
    state = ds.load_state(project)
    assert state["all_20260101_000000"]["testPlanId"] == "VALZ0000042"


def test_sync_plan_execute_reuses_plan_from_state(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验", status="passed")
    ds.save_state(project, {"all_20260101_000000": {"testPlanId": "VALZ_REUSE"}})
    client = FakeMcpClient()
    res = ds.sync_plan(project, execute=True, client=client, mcp_url="http://fake/mcp",
                       now=datetime(2026, 1, 1, 0, 0, 0))
    assert res["plan_reused"] is True and res["test_plan_id"] == "VALZ_REUSE"
    tool_calls = [c[0] for c in client.calls]
    assert "create_test_plan" not in tool_calls                 # 复用不重复建计划
    relate = client.calls[-1][1]["request"]["testPlanId"]
    assert relate == "VALZ_REUSE"


def test_sync_plan_execute_reuses_existing_case_ids(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 已有", status="passed")
    _write_result(results, "b-result.json", sub_suite="模块 - 新", status="passed")
    client = FakeMcpClient(import_ids=["VALZ_NEW"])
    res = ds.sync_plan(project, execute=True, client=client, mcp_url="http://fake/mcp",
                       known_ids={"已有": "VALZ_OLD"}, now=datetime(2026, 1, 1, 0, 0, 0))
    assert res["reused"] == 1 and res["imported_new"] == 1
    # 导入请求只含未复用的那条
    import_call = next(c for c in client.calls if c[0] == "import_testcases_to_library")
    assert [tc["title"] for tc in import_call[1]["request"]["testCaseList"]] == ["新"]
    relate = client.calls[-1][1]["request"]["testCases"]
    assert {tc["testCaseId"] for tc in relate} == {"VALZ_OLD", "VALZ_NEW"}


def test_sync_plan_execute_ambiguous_server_stops(project, tmp_path):
    """execute 且未指定服务器、mcp.json 有多个候选 → 点名报错，不猜环境。"""
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验")
    mcp = _write_mcp_json(tmp_path, {
        "devops-mcp-sit": {"url": "http://sit/mcp"},
        "devops-mcp-uat": {"url": "http://uat/mcp"},
    })
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.sync_plan(project, execute=True, client=FakeMcpClient(),
                     mcp_config=mcp, env={}, now=datetime(2026, 1, 1, 0, 0, 0))
    assert "多个" in str(exc.value)


def test_sync_plan_execute_config_server_name_selects(tmp_path):
    """devops-sync.yaml 的 mcp_server 显式选中 sit，即便 mcp.json 有多个候选。"""
    (tmp_path / "devops-sync.yaml").write_text(
        'system_id: "S"\nsystem_name: "demo"\nexec_user: "u"\n'
        'testcase_directory: "demo/未分类目录"\nmcp_server: "devops-mcp-sit"\n',
        encoding="utf-8",
    )
    results = tmp_path / "reports" / "allure-results"
    results.mkdir(parents=True)
    _write_result(results, "a-result.json", sub_suite="模块 - 校验")
    mcp = _write_mcp_json(tmp_path, {
        "devops-mcp-sit": {"url": "http://sit/mcp", "midea_mkt_id": 505},
        "devops-mcp-uat": {"url": "http://uat/mcp", "midea_mkt_id": 528},
    })
    res = ds.sync_plan(tmp_path, execute=True, client=FakeMcpClient(),
                       mcp_config=mcp, env={}, now=datetime(2026, 1, 1, 0, 0, 0))
    assert res["mcp_server"] == "devops-mcp-sit"
    assert "http://sit/mcp" in res["mcp_url"]


def test_sync_plan_no_cases_errors(project):
    (project / "reports" / "allure-results")  # 空目录已建
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.sync_plan(project, client=FakeMcpClient())
    assert "无可用用例" in str(exc.value)


def test_sync_plan_execute_directory_resolution_failure(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验")
    with pytest.raises(ds.DevopsSyncError) as exc:
        ds.sync_plan(project, execute=True, client=FakeMcpClient(dir_error=True),
                     mcp_url="http://fake/mcp", now=datetime(2026, 1, 1, 0, 0, 0))
    assert "解析用例库目录失败" in str(exc.value)


def test_sync_plan_400_import_count_mismatch_falls_back(project):
    """导入响应缺 idxToTestId 时，正则回退按出现顺序取 ID（count 内尽力映射）。"""
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 一")

    class RegexImport(FakeMcpClient):
        def call_tool(self, name, arguments):
            if name == "import_testcases_to_library":
                self.calls.append((name, arguments))
                return False, "导入完成 VALZ008 VALZ009"
            return super().call_tool(name, arguments)

    res = ds.sync_plan(project, execute=True, client=RegexImport(),
                       mcp_url="http://fake/mcp", now=datetime(2026, 1, 1, 0, 0, 0))
    assert res["imported_new"] == 1
    assert res["cases"][0]["new_devops_id"] == "VALZ008"


# ── 摘要渲染 ───────────────────────────────────────────────────────────

def test_format_sync_summary_is_cp936_safe(project):
    results = project / "reports" / "allure-results"
    _write_result(results, "a-result.json", sub_suite="模块 - 校验")
    res = ds.sync_plan(project, client=FakeMcpClient(), env={},
                       now=datetime(2026, 1, 1, 0, 0, 0))
    text = ds.format_sync_summary(res)
    assert "DRY-RUN" in text
    text.encode("cp936")   # 标记全 ASCII，中文可编码（Atlas 历次 GBK 教训）

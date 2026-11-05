"""JMeter 导入脚本测试：用最小 JMX 验证解析。"""
from scripts.import_jmeter import extract_samplers, to_atlas_dsl, unresolved_placeholders


MINIMAL_JMX = """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2">
  <hashTree>
    <HTTPSamplerProxy testname="查客户">
      <stringProp name="HTTPSampler.path">/web/legacy-web-mdm/orgCustomerInfo/findEntireCustDetails</stringProp>
      <stringProp name="HTTPSampler.method">POST</stringProp>
      <stringProp name="Argument.value">{"customerCode":"CUST0000001"}</stringProp>
    </HTTPSamplerProxy>
    <hashTree/>
    <HTTPSamplerProxy testname="查订单列表">
      <stringProp name="HTTPSampler.path">/web/demo-sales/saleOrder/getList4Demo</stringProp>
      <stringProp name="HTTPSampler.method">POST</stringProp>
    </HTTPSamplerProxy>
    <hashTree/>
  </hashTree>
</jmeterTestPlan>
"""


def test_extract_samplers(tmp_path):
    jmx = tmp_path / "test.jmx"
    jmx.write_text(MINIMAL_JMX, encoding="utf-8")
    samplers = extract_samplers(jmx)
    assert len(samplers) == 2
    assert samplers[0]["name"] == "查客户"
    assert samplers[0]["method"] == "POST"
    assert "findEntireCustDetails" in samplers[0]["path"]
    assert samplers[1]["name"] == "查订单列表"


def test_to_atlas_dsl():
    samplers = [
        {"name": "查客户", "method": "POST", "path": "/api/customer", "body": '{"code":"C1"}'},
        {"name": "查订单", "method": "GET", "path": "/api/orders", "body": ""},
    ]
    flow = to_atlas_dsl(samplers)
    assert flow["id"] == "jmeter-import"
    assert len(flow["steps"]) == 2
    assert flow["steps"][0]["task"] == "api_call"
    assert flow["steps"][0]["path"] == "/api/customer"
    assert flow["steps"][0]["body"] == {"code": "C1"}
    assert flow["steps"][1]["method"] == "GET"
    assert "body" not in flow["steps"][1]


def test_to_atlas_dsl_skips_empty_path():
    samplers = [{"name": "空", "method": "POST", "path": "", "body": ""}]
    flow = to_atlas_dsl(samplers)
    assert len(flow["steps"]) == 0


def test_to_atlas_dsl_preserves_flow_id():
    """flow_id 参数决定输出 flow 的 id。"""
    flow = to_atlas_dsl([], flow_id="custom-patrol")
    assert flow["id"] == "custom-patrol"


def test_to_atlas_dsl_handles_invalid_json_body():
    """body 不是合法 JSON 时保留原字符串。"""
    samplers = [{"name": "test", "method": "POST", "path": "/api", "body": "not json{"}]
    flow = to_atlas_dsl(samplers)
    assert flow["steps"][0]["body"] == "not json{"


def test_to_atlas_dsl_includes_domain_and_intent():
    """每个 step 包含 domain=jmeter 与 intent=采样器名。"""
    samplers = [{"name": "查客户", "method": "POST", "path": "/api/customer", "body": ""}]
    flow = to_atlas_dsl(samplers)
    assert flow["steps"][0]["domain"] == "jmeter"
    assert flow["steps"][0]["intent"] == "查客户"


# --- 鉴权头提取与占位符告警（服务 ICS isc-app 后端 BUG-920 真实痛点）---

NESTED_JMX = """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2">
  <hashTree>
    <TransactionController testname="巡检">
      <hashTree>
        <HTTPSamplerProxy testname="查客户">
          <stringProp name="HTTPSampler.path">/web/legacy-web-mdm/findEntireCustDetails</stringProp>
          <stringProp name="HTTPSampler.method">POST</stringProp>
          <stringProp name="Argument.value">{"customerCode":"${cust_code}"}</stringProp>
        </HTTPSamplerProxy>
        <hashTree/>
      </hashTree>
    </TransactionController>
    <hashTree>
      <HeaderManager testname="HTTP Header Manager">
        <collectionProp name="HeaderManager.headers">
          <elementProp name="" elementType="Header">
            <stringProp name="Header.name">isc-app</stringProp>
            <stringProp name="Header.value">iservice</stringProp>
          </elementProp>
          <elementProp name="" elementType="Header">
            <stringProp name="Header.name">Authorization</stringProp>
            <stringProp name="Header.value">Bearer ${tok}</stringProp>
          </elementProp>
        </collectionProp>
      </HeaderManager>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
"""


def test_extract_samplers_grabs_global_headers_and_nested_sampler(tmp_path):
    """HeaderManager 是 sampler 的兄弟节点、且 sampler 套在 TransactionController 内：
    root.iter 递归必须都抓到，否则迁移后 ICS 后端缺 isc-app 头回 BUG-920。"""
    jmx = tmp_path / "patrol.jmx"
    jmx.write_text(NESTED_JMX, encoding="utf-8")
    samplers = extract_samplers(jmx)
    assert len(samplers) == 1
    assert samplers[0]["headers"] == {"isc-app": "iservice", "Authorization": "Bearer ${tok}"}


def test_to_atlas_dsl_emits_headers_only_when_present():
    flow = to_atlas_dsl([
        {"name": "带token", "method": "POST", "path": "/a", "body": "",
         "headers": {"isc-app": "iservice"}},
        {"name": "无头", "method": "POST", "path": "/b", "body": ""},
    ])
    assert flow["steps"][0]["headers"] == {"isc-app": "iservice"}
    assert "headers" not in flow["steps"][1]


def test_unresolved_placeholders_names_vars():
    """${VAR} 保留原样（迁移不改语义），但必须点名，供人审接 env，避免当字面量发假 400。"""
    flow = to_atlas_dsl([{"name": "查", "method": "POST",
                          "path": "/web/${base}/find", "body": "{'code':'${cust}'}",
                          "headers": {"Authorization": "Bearer ${tok}"}}])
    found = unresolved_placeholders(flow)
    keys = {k for _, k, _ in found}
    assert {"path", "body", "header:Authorization"} <= keys


def test_unresolved_placeholders_recurses_parsed_body():
    """body 被 json.loads 成嵌套结构后，里面的 ${var} 也必须被下钻发现（巡检最常见形态）。"""
    flow = to_atlas_dsl([{"name": "查", "method": "POST", "path": "/api/x",
                          "body": '{"cust":"${cust_code}","rows":[{"pt":"${__P(pt)}"}]}'}])
    where = {k for _, k, _ in unresolved_placeholders(flow)}
    assert "body.cust" in where
    assert any(k.startswith("body.rows[") for k in where)


# --- body 完整迁移（巡检标准 JMX 形态三连缺陷：bool 当 body 崩溃、[:500] 截断、
#     Arguments 容器节点被当参数收集导致重复；扁平 Argument.value 形态兜底）---

RAW_BIG_JMX = """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2">
  <hashTree>
    <HTTPSamplerProxy testname="建单">
      <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
        <collectionProp name="Arguments.arguments">
          <elementProp elementType="HTTPArgument">
            <stringProp name="Argument.value">{BIG}</stringProp>
          </elementProp>
        </collectionProp>
      </elementProp>
      <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
      <stringProp name="HTTPSampler.path">/api/order/create</stringProp>
      <stringProp name="HTTPSampler.method">POST</stringProp>
    </HTTPSamplerProxy>
  </hashTree>
</jmeterTestPlan>
"""


def test_raw_body_large_payload_not_crashed_nor_truncated(tmp_path):
    """真实 JMeter「Body of a sampler」= postBodyRaw(boolProp true) + elementProp 正文。
    旧实现把 bool 当 body 当场 TypeError 崩掉整个迁移；[:500] 又静默腰斩大报文，
    json.loads 必败退回截断串——巡检发残缺报文得假 400 误判成 Atlas 缺陷。"""
    import json
    big = json.dumps({"remark": "非人工创建 巡检",
                      "lines": [{"sku": f"SKU{i}", "qty": 1} for i in range(40)]},
                     ensure_ascii=False)
    assert len(big) > 500
    jmx = tmp_path / "big.jmx"
    jmx.write_text(RAW_BIG_JMX.replace("{BIG}", big), encoding="utf-8")
    samplers = extract_samplers(jmx)
    assert samplers[0]["body"] == big
    step = to_atlas_dsl(samplers)["steps"][0]
    assert isinstance(step["body"], dict) and step["body"]["remark"] == "非人工创建 巡检"


def test_flat_argument_value_body_preserved(tmp_path):
    """MINIMAL_JMX 形态：Argument.value 直接挂 sampler 无 elementProp 包装。
    结构化解析后必须兜底取到，否则迁移产物静默丢正文。"""
    jmx = tmp_path / "flat.jmx"
    jmx.write_text(MINIMAL_JMX, encoding="utf-8")
    samplers = extract_samplers(jmx)
    assert samplers[0]["body"] == '{"customerCode":"CUST0000001"}'


def test_multi_params_structured_no_container_dupe(tmp_path):
    """多参数表单：逐 elementProp 分组拼 name=value；外层 Arguments 容器节点
    不得被当参数收集（旧结构化第一版把最后一条重复拼进去）。"""
    jmx_src = """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2">
  <hashTree>
    <HTTPSamplerProxy testname="查询">
      <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
        <collectionProp name="Arguments.arguments">
          <elementProp elementType="HTTPArgument">
            <stringProp name="Argument.name">custNo</stringProp>
            <stringProp name="Argument.value">CUST0000001</stringProp>
          </elementProp>
          <elementProp elementType="HTTPArgument">
            <stringProp name="Argument.name">page</stringProp>
            <stringProp name="Argument.value">${PAGE}</stringProp>
          </elementProp>
        </collectionProp>
      </elementProp>
      <boolProp name="HTTPSampler.postBodyRaw">false</boolProp>
      <stringProp name="HTTPSampler.path">/api/order/query</stringProp>
      <stringProp name="HTTPSampler.method">GET</stringProp>
    </HTTPSamplerProxy>
  </hashTree>
</jmeterTestPlan>
"""
    jmx = tmp_path / "multi.jmx"
    jmx.write_text(jmx_src, encoding="utf-8")
    samplers = extract_samplers(jmx)
    assert samplers[0]["body"] == "custNo=CUST0000001&page=${PAGE}"
    found = unresolved_placeholders(to_atlas_dsl(samplers))
    assert any(k == "body" and "${PAGE}" in v for _, k, v in found)


def test_unnamed_args_take_values_verbatim(tmp_path):
    """全是无名正文参数时不拼 key= 前缀（弄脏报文），value 原样。"""
    jmx_src = """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2">
  <hashTree>
    <HTTPSamplerProxy testname="最简">
      <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
        <collectionProp name="Arguments.arguments">
          <elementProp elementType="HTTPArgument">
            <stringProp name="Argument.value">plain-body-text</stringProp>
          </elementProp>
        </collectionProp>
      </elementProp>
      <stringProp name="HTTPSampler.path">/api/ping</stringProp>
    </HTTPSamplerProxy>
  </hashTree>
</jmeterTestPlan>
"""
    jmx = tmp_path / "unnamed.jmx"
    jmx.write_text(jmx_src, encoding="utf-8")
    samplers = extract_samplers(jmx)
    assert samplers[0]["body"] == "plain-body-text"
    assert samplers[0]["method"] == "POST"


def test_dsl_compiles_api_call_step_with_headers(tmp_path):
    """headers 不在 DSL step 黑名单里，应能透传进 CallEndpoint 构造（引擎真接住）。"""
    from atlas.dsl import FlowCompiler, TaskRegistry
    from atlas.tasks import CallEndpoint
    reg = TaskRegistry()
    reg.register("api_call", lambda **kw: CallEndpoint(**kw))
    flow = {"id": "patrol", "steps": [
        {"task": "api_call", "path": "/web/x", "method": "POST",
         "headers": {"isc-app": "iservice"}}]}
    tasks = FlowCompiler(reg).compile(flow)
    assert tasks[0].headers == {"isc-app": "iservice"}


def test_callapi_body_routing_form_json_bytes(tmp_path):
    """发送端与迁移端同一纪律：JMeter 非 raw 多参数产物是 form 串（str），走 json= 会被
    包成 JSON 字符串字面量发出（实测 b'\"custNo=...&page=2\"'），后端收脏数据得假 400。
    str/bytes 必须 data= 原样；dict 走 json= 不变；其余类型发送前点名拒绝。"""
    from atlas.abilities import CallApi

    sent = {}
    class FakeResp:
        status_code, ok = 200, True
        url = "http://ics/x"
        headers = {"Content-Type": "application/json"}
        def json(self): return {"ok": True}
    class FakeSession:
        headers = {}
        def request(self, method, url, **kw):
            sent.clear(); sent.update(kw); return FakeResp()

    api = CallApi("http://ics", session=FakeSession())
    # form 串走 data= 原样
    api.request("POST", "/x", body="custNo=CUST0000001&page=2")
    assert sent["data"] == "custNo=CUST0000001&page=2" and sent["json"] is None
    # dict 仍走 json=
    api.request("POST", "/x", body={"cust": "C1"})
    assert sent["json"] == {"cust": "C1"} and sent["data"] is None
    # bytes 原样不崩（旧实现 json=bytes 当场 TypeError）
    api.request("POST", "/x", body=b"raw-bytes")
    assert sent["data"] == b"raw-bytes" and sent["json"] is None
    # 坏类型：发送前点名，session 不收到任何调用
    sent.clear()
    try:
        api.request("POST", "/x", body=12345)
        raise AssertionError("int body 应被拒绝")
    except TypeError as exc:
        assert "不支持的请求体类型 int" in str(exc)
        assert not sent, "拒绝必须发生在发送之前"
    # body=None 零开销
    api.request("GET", "/x")
    assert sent["json"] is None and sent["data"] is None


def test_imported_patrol_flow_runs_end_to_end_with_auth_header(tmp_path):
    """闭环验证：JMX → DSL → FlowCompiler.run 过引擎，真实发出的请求带着 isc-app 头。
    只单测头字段不够——迁移产物必须能真跑通才谈得上'供人审'。"""
    import json
    from atlas.abilities import CallApi
    from atlas.actor import Actor
    from atlas.config import AtlasConfig
    from atlas.dsl import FlowCompiler, TaskRegistry
    from atlas.tasks import CallEndpoint

    captured = []
    class FakeResp:
        status_code, ok = 200, True
        url = "http://ics/web/x/findCust"
        headers = {"Content-Type": "application/json"}
        def json(self):
            return {"ok": True, "data": {"cust": "C1"}}
    class FakeSession:
        def __init__(self):
            self.headers = {"isc-app": "default"}
        def request(self, method, url, **kw):
            captured.append((method, url, kw.get("headers")))
            return FakeResp()

    samplers = [{"name": "查客户", "method": "POST",
                 "path": "/web/legacy-web-mdm/findCust",
                 "body": json.dumps({"cust": "C1"}),
                 "headers": {"isc-app": "iservice", "Authorization": "Bearer t"}}]
    flow = {"engine": AtlasConfig(tmp_path, mode="run"), **to_atlas_dsl(samplers)}
    reg = TaskRegistry()
    reg.register("api_call", lambda **kw: CallEndpoint(**kw))
    actor = Actor(abilities=[CallApi("http://ics", session=FakeSession())])
    result = FlowCompiler(reg).run(flow, actor)

    assert result["status"] == "passed"
    assert captured == [("POST", "http://ics/web/legacy-web-mdm/findCust",
                         {"isc-app": "iservice", "Authorization": "Bearer t"})]
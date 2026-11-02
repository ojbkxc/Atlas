"""JMeter 导入脚本测试：用最小 JMX 验证解析。"""
from scripts.import_jmeter import extract_samplers, to_atlas_dsl


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
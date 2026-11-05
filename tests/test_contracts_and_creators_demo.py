import sys
from pathlib import Path
import pytest
from atlas.contracts import Contract, ContractRegistry
from atlas.plugins.demo.creators import build_payload, extract_order_no, make_sales_order_creator

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts" / "templates" / "sale_order_save_template.json"


def test_core_contract_checks_types_and_index():
    c = Contract("列表", path="/l", level="core",
                 required={"data.0.orderNo": "string", "data.1.paymentTerms": "string"})
    c.validate({"ok": True, "data": {"resultCode": "BUG-000",
                                     "data": [{"orderNo": "SP1"}, {"paymentTerms": "NET30"}]}})
    with pytest.raises(AssertionError, match="类型应为 string"):
        c.validate({"ok": True, "data": {"resultCode": "BUG-000",
                                         "data": [{"orderNo": 1}, {"paymentTerms": "x"}]}})


def test_core_contract_validates_result_code():
    """core 级契约的 result_code 不匹配时必须报错。"""
    c = Contract("订单", path="/o", level="core", result_code="BUG-000")
    c.validate({"ok": True, "data": {"resultCode": "BUG-000"}})
    with pytest.raises(AssertionError, match="resultCode"):
        c.validate({"ok": True, "data": {"resultCode": "BUG-999"}})


def test_core_contract_missing_field_is_reported():
    """core 级契约缺必需字段时报告具体路径。"""
    c = Contract("客户", path="/c", level="core", required={"collectPayments": "array"})
    with pytest.raises(AssertionError, match="collectPayments"):
        c.validate({"ok": True, "data": {"resultCode": "BUG-000"}})


def test_core_contract_validates_number_and_boolean_types():
    """core 级契约支持 number（int/float）与 boolean 类型校验。"""
    c = Contract("数据", path="/d", level="core",
                 required={"count": "number", "active": "boolean"})
    c.validate({"ok": True, "data": {"resultCode": "BUG-000", "count": 5, "active": True}})
    c.validate({"ok": True, "data": {"resultCode": "BUG-000", "count": 3.14, "active": False}})
    with pytest.raises(AssertionError, match="类型应为 number"):
        c.validate({"ok": True, "data": {"resultCode": "BUG-000", "count": "5", "active": True}})
    with pytest.raises(AssertionError, match="类型应为 boolean"):
        c.validate({"ok": True, "data": {"resultCode": "BUG-000", "count": 5, "active": "yes"}})


def test_contract_with_empty_path_skips_validation():
    """空 path 的契约不校验，供未配置接口的占位。"""
    c = Contract("空", path="", level="core")
    c.validate({"ok": False})


def test_contract_unknown_type_is_rejected():
    """core 级契约的 required 类型必须已知——构造期当场点名（含路径），
    不留到 validate()：私有契约 JSON 是人手编辑的，类型值写成 ["string"]（JSON 合法）
    或路径键写成 tuple 时，旧实现到建真实响应才 unhashable TypeError / AttributeError，
    定性器先于被诊断报文死。提前到构造期让 DemoPlugin 按「文件写坏」回退包内默认。"""
    with pytest.raises(ValueError, match="未知类型"):
        Contract("数据", path="/d", level="core", required={"field": "bogus_type"})
    with pytest.raises(ValueError, match="未知类型.*items"):
        Contract("列表", path="/l", level="core", required={"items": ["string"]})   # JSON 合法的手误
    with pytest.raises(ValueError, match="未知类型"):
        Contract("对象", path="/o", level="core", required={"x": {"type": "string"}})
    with pytest.raises(ValueError, match="非空字符串"):
        Contract("键坏", path="/k", level="core", required={("a", "b"): "string"})
    with pytest.raises(ValueError, match="非空字符串"):
        Contract("键空白", path="/k", level="core", required={"  ": "string"})


def test_registry_validate_noop_without_matching_contract():
    """无匹配契约时 validate 不校验，供未配置接口跳过。"""
    reg = ContractRegistry([])
    reg.validate("POST", "/unknown", {"ok": False})


def test_registry_add_rejects_conflicting_definition_same_key():
    """#128：同 (method, path) 不同定义旧实现 dict last-wins 静默覆盖——本测此前钉的就是
    缺陷形态本身（minimal 被 core 顶掉恰好「变强」，反序则强校验被弱校验遮蔽，
    「冒充覆盖率却永不生效」正是 --validate 自述红线 #7/#12，与 #84 多生产者同键同红线）。
    现冲突显式 ValueError 点名两条；等值重复仍放行——toolbox.learn_contracts 轮询去重
    依赖同键覆盖语义（#102 净增计数），拒绝它=把正常路径翻成错误（#115 教训）。
    旧断言 `level == "core"`（静默择强）随缺陷一并更正。"""
    c1 = Contract("a", path="/a", level="minimal")
    reg = ContractRegistry([c1])
    with pytest.raises(ValueError, match="同键但定义不同"):
        reg.add(Contract("a", path="/a", level="core"))
    # 等值重复幂等放行（learn_contracts 对轮询同路径逐条 add 的真实形态）
    reg.add(Contract("a", path="/a", level="minimal"))
    reg.add(Contract("a", path="/a", level="minimal"))
    assert len(reg.contracts) == 1
    assert reg.contracts[("POST", "/a")].level == "minimal"


def test_contract_file_conflicting_rows_rejected(tmp_path):
    """#128：契约文件（人手编辑）复制行忘改=同键两条不同定义，旧实现 last-wins 静默择一，
    校验强度取决于条目顺序。load 现点名两条定义；ValueError 在 DemoPlugin.contracts 捕获
    的坏文件回退链里——私有 core.json 有冲突行不再「静默用弱版跑真实校验」，而是回退
    包内默认并在 atlas contracts 可见（与 #74/#95 损坏回退同链路，探针 /tmp/probe128b 面B）。"""
    import json as _json
    c = tmp_path / "core.json"
    c.write_text(_json.dumps({"contracts": [
        {"name": "强", "path": "/x", "level": "core", "required": {"a": "string"}},
        {"name": "弱", "path": "/x", "level": "high"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="同键但定义不同"):
        ContractRegistry.load(c)


def test_core_contract_validates_array_type():
    """core 级契约支持 array 类型校验。"""
    c = Contract("列表", path="/l", level="core", required={"items": "array"})
    c.validate({"ok": True, "data": {"resultCode": "BUG-000", "items": [1, 2, 3]}})
    with pytest.raises(AssertionError, match="类型应为 array"):
        c.validate({"ok": True, "data": {"resultCode": "BUG-000", "items": "not array"}})


def test_core_contract_any_type_accepts_anything():
    """any 类型不校验具体类型，只要求字段存在。"""
    c = Contract("任意", path="/a", level="core", required={"field": "any"})
    c.validate({"ok": True, "data": {"resultCode": "BUG-000", "field": 123}})
    c.validate({"ok": True, "data": {"resultCode": "BUG-000", "field": "str"}})
    c.validate({"ok": True, "data": {"resultCode": "BUG-000", "field": [1, 2]}})


def test_high_contract_requires_result_code_only():
    c = Contract("高频", path="/h", level="high")
    c.validate({"ok": True, "data": {"resultCode": "BUG-000", "anything": 1}})
    with pytest.raises(AssertionError, match="resultCode"):
        c.validate({"ok": True, "data": {"anything": 1}})


def test_minimal_contract_only_requires_success():
    c = Contract("最小", path="/m", level="minimal")
    c.validate({"ok": True, "data": {"whatever": True}})
    with pytest.raises(AssertionError, match="HTTP"):
        c.validate({"ok": False, "status": 500})


def test_legacy_level_aliases_still_work():
    assert Contract("x", path="/x", level="weak").level == "high"
    assert Contract("x", path="/x", level="strong").level == "core"


def test_registry_stats_reports_levels():
    reg = ContractRegistry([Contract("a", path="/a", level="core"),
                            Contract("b", path="/b", level="high"),
                            Contract("c", path="/c", level="minimal")])
    assert reg.stats() == {"core": 1, "high": 1, "minimal": 1}


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError, match="未知契约等级"):
        Contract("x", path="/x", level="bogus")


def test_contract_load_shape_errors_are_pointable(tmp_path):
    """契约文件是人手编辑的资产：JSON 合法但形态写坏（顶层数组忘包一层、条目是字符串、
    键拼错）旧实现裸崩 AttributeError/TypeError，且不在 DemoPlugin 捕获的解析异常里，
    直接顶掉整条契约链。必须转成可定位 ValueError——点名文件、条目序号与实际形态。"""
    import json as _json
    # 顶层是数组（忘写 {"contracts": [...]} 外层）
    p = tmp_path / "core.json"
    p.write_text(_json.dumps([{"name": "a", "path": "/a"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="顶层必须是映射"):
        ContractRegistry.load(p)
    # contracts 不是列表
    p.write_text(_json.dumps({"contracts": {"name": "a"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="contracts 必须是列表"):
        ContractRegistry.load(p)
    # 某一条目是字符串
    p.write_text(_json.dumps({"contracts": ["oops"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="第 0 条必须是映射"):
        ContractRegistry.load(p)
    # 键拼错（level_ ）：补条目定位而不是裸 unexpected keyword argument
    p.write_text(_json.dumps({"contracts": [{"name": "a", "path": "/a", "level_": "core"}]}),
                 encoding="utf-8")
    with pytest.raises(ValueError, match="第 0 条定义错误"):
        ContractRegistry.load(p)


def test_corrupt_private_contract_falls_back_not_kills_chain(tmp_path, monkeypatch):
    """端到端：私有目录契约文件「结构合法但契约定义坏」（required 类型值被写成数组）时，
    旧实现 TypeError 穿过 plugin 捕获范围，整个契约子系统连包内回退一起死——真实 run 的
    断言层被一个手误连坐。现按 #73 损坏降级策略回退包内默认。"""
    import json as _json
    priv = tmp_path / "kb"; (priv / "contracts").mkdir(parents=True)
    (priv / "contracts" / "core.json").write_text(_json.dumps({"contracts": [
        {"name": "real", "path": "/real", "level": "core", "required": {"items": ["string"]}}]}),
        encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(priv))
    from atlas.plugins.demo.plugin import DemoPlugin
    reg = DemoPlugin().contracts()                      # 不应抛
    assert reg.stats().get("core") == 1                 # 包内虚构默认仍在守门


def test_build_payload_forces_audit_tag():
    payload = build_payload(TEMPLATE, {"customer_code": "CUST0000001",
                                       "payment_terms": "NET30", "remark": "测试"})
    assert payload["saleOrder"]["remark"].endswith("非人工创建")
    assert payload["saleOrder"]["customerCorpNumber"] == "CUST0000001"
    assert payload["saleOrder"]["paymentTerms"] == "NET30"


def test_extract_order_no_handles_nested_and_flat():
    assert extract_order_no({"data": {"data": {"orderNo": "SP1"}}}) == "SP1"
    assert extract_order_no({"data": {"orderNo": "SP2"}}) == "SP2"
    assert extract_order_no({"data": {}}) == ""


def test_extract_order_no_handles_empty_and_missing():
    """extract_order_no 对空响应与缺 data 的响应都返回空串。"""
    assert extract_order_no({}) == ""
    assert extract_order_no({"data": None}) == ""


def test_build_payload_overrides_customer_and_pt():
    """build_payload 用传入参数覆盖模板的客户与 PT。"""
    payload = build_payload(TEMPLATE, {"customer_code": "CUST123",
                                       "payment_terms": "90DF", "remark": "测试"})
    assert payload["saleOrder"]["customerCorpNumber"] == "CUST123"
    assert payload["saleOrder"]["paymentTerms"] == "90DF"
    assert "测试" in payload["saleOrder"]["remark"]
    assert payload["saleOrder"]["remark"].endswith("非人工创建")

def test_creator_raises_when_no_order_no():
    class Api:
        def demo_call(self, *a, **kw):
            return {"status": 200, "ok": True, "data": {"resultCode": "BUG-999"}}
    create = make_sales_order_creator(Api(), TEMPLATE)
    with pytest.raises(RuntimeError, match="未返回单号"):
        create({"customer_code": "CUST0000001"})


def test_creator_returns_document_and_tag():
    class Api:
        def demo_call(self, method, path, body=None):
            assert "saveSalesOrder" in path
            assert body["saleOrder"]["remark"].endswith("非人工创建")
            return {"status": 200, "ok": True, "data": {"data": {"orderNo": "SP9"}}}
    result = make_sales_order_creator(Api(), TEMPLATE)({"customer_code": "CUST0000001"})
    assert result["orderNo"] == "SP9" and result["remark"].endswith("非人工创建")


def test_linux_acceptance_script_semantics_are_explicit(monkeypatch, tmp_path):
    """脚本的三种终态必须可区分：执行通过(0)、无法执行(2)、其它失败。

    不在测试里真跑容器或真实门禁——CI 与开发机环境差异很大。
    这里只验证「无法执行时不静默成功」这条核心不变量。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import linux_acceptance

    info = linux_acceptance.probe()
    assert set(info) >= {"platform", "docker", "wsl", "dockerfile", "compose_file", "ci_workflow"}

    # 无 Docker 且非 Linux → 必须返回 2 并给出可复现命令
    monkeypatch.setattr(linux_acceptance.sys, "platform", "win32")
    monkeypatch.setattr(linux_acceptance.shutil, "which", lambda name: None)
    assert linux_acceptance.main() == 2


def test_linux_acceptance_assets_exist():
    """验收资产必须随仓库分发；缺失就是真缺陷。"""
    assert (ROOT / "Dockerfile").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    assert (ROOT / ".github/workflows/ci.yml").is_file()
    assert (ROOT / "scripts/linux_acceptance.py").is_file()
    assert (ROOT / "scripts/linux_acceptance_remote.py").is_file()


def test_linux_acceptance_probe_reports_environment():
    """只验证探测逻辑本身，不在测试里真跑容器或依赖本机是否有 Docker。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import linux_acceptance
    info = linux_acceptance.probe()
    assert info["platform"] == sys.platform
    assert "docker" in info and "wsl" in info
    # 资产必须被识别，否则说明打包遗漏
    assert info["dockerfile"] is True
    assert info["compose_file"] is True
    assert info["ci_workflow"] is True

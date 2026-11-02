import pytest
from atlas.config import AtlasConfig
from atlas.creators import make_scripted_creator, require_audit_tag
from atlas.failures import EnvBlocked
from atlas.probes import CallableProbe, DataProvider, ScriptedCreator


def test_require_audit_tag_rejects_missing_marker():
    with pytest.raises(ValueError, match="非人工创建"):
        require_audit_tag({"orderNo": "SP1", "remark": "普通备注"})


def test_require_audit_tag_rejects_missing_document_number():
    with pytest.raises(ValueError, match="单号"):
        require_audit_tag({"remark": "非人工创建"})


def test_require_audit_tag_accepts_valid_result():
    result = {"orderNo": "SP1", "remark": "备件非人工创建"}
    assert require_audit_tag(result) is result


def test_scripted_creator_requires_env():
    creator = ScriptedCreator(lambda where: {"orderNo": "x", "remark": "非人工创建"},
                              required_env=("ATLAS_DEFINITELY_MISSING_ENV",))
    with pytest.raises(EnvBlocked, match="ATLAS_DEFINITELY_MISSING_ENV"):
        creator({})


def test_scripted_creator_parses_last_json_line(tmp_path):
    script = tmp_path / "make.py"
    script.write_text(
        "import json, sys\n"
        "where = json.load(sys.stdin)\n"
        "print('日志行')\n"
        "print(json.dumps({'orderNo': 'SP9', 'remark': '非人工创建'}, ensure_ascii=False))\n",
        encoding="utf-8")
    creator = make_scripted_creator(script)
    assert creator({"customer": "M1"})["orderNo"] == "SP9"


def test_scripted_creator_reports_nonzero_exit(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("import sys\nsys.stderr.write('boom')\nsys.exit(2)\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="rc=2"):
        make_scripted_creator(script)({})


def test_scripted_creator_rejects_non_json_output(tmp_path):
    script = tmp_path / "noise.py"
    script.write_text("print('not json')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="不是合法 JSON"):
        make_scripted_creator(script)({})


def test_provider_create_path_enforces_quota_and_tag(tmp_path):
    calls = []
    def create(where):
        calls.append(where)
        return {"orderNo": "SP1", "remark": "非人工创建"}
    probe = CallableProbe("SalesOrder", lambda where: None, create)
    provider = DataProvider(AtlasConfig(tmp_path), [probe], create_quota=1)
    assert provider.supply({"entity": "SalesOrder"}).action == "create"
    with pytest.raises(EnvBlocked, match="配额"):
        provider.supply({"entity": "SalesOrder"})
    assert len(calls) == 1


def test_provider_create_result_without_tag_is_rejected(tmp_path):
    probe = CallableProbe("SalesOrder", lambda where: None,
                          lambda where: {"orderNo": "SP1", "remark": "人工"})
    provider = DataProvider(AtlasConfig(tmp_path), [probe])
    with pytest.raises(ValueError, match="非人工创建"):
        provider.supply({"entity": "SalesOrder"})
    # 单据已建出，必须保留回收记录，不能当作没发生。
    assert len(provider.quota.created) == 1


def test_sale_order_save_template_has_required_fields():
    """建单报文模板必须含业务必需字段，否则 build_payload 会产出无效请求。"""
    from pathlib import Path
    template = Path(__file__).resolve().parents[1] / "scripts" / "templates" / "sale_order_save_template.json"
    import json
    data = json.loads(template.read_text(encoding="utf-8"))
    so = data["saleOrder"]
    for field in ("remark", "customerCorpNumber", "paymentTerms"):
        assert field in so, f"模板缺 saleOrder.{field}"
    assert "saleOrderDetails" in data, "模板缺 saleOrderDetails"


def test_scripted_creator_rejects_non_dict_result():
    """create 脚本必须返回 dict，否则报错。"""
    creator = ScriptedCreator(lambda where: "not a dict")
    with pytest.raises(TypeError, match="dict"):
        creator({})


def test_scripted_creator_passes_where_to_create_fn():
    """ScriptedCreator 把 where 传给 create_fn。"""
    received = []
    creator = ScriptedCreator(lambda where: received.append(where) or {"remark": "非人工创建", "orderNo": "SP1"})
    creator({"customer": "CUST0000001"})
    assert received[0] == {"customer": "CUST0000001"}

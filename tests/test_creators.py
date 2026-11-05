import pytest
from atlas.config import AtlasConfig
from atlas.creators import make_scripted_creator, require_audit_tag
from atlas.failures import EnvBlocked
from atlas.probes import CallableProbe, DataProvider, ScriptedCreator, find_document_number


def test_require_audit_tag_rejects_empty_or_none_document_number():
    """#117 面1：`{"orderNo": ""}` / `{"orderNo": None}` 被旧实现 `key in result` 放行
    （键在即算，#102 同族假成功），回收清单落空单号记录、atlas recycle 找不回真实单据。
    单号判据收敛 find_document_number：必须是「非空标量」。"""
    with pytest.raises(ValueError, match="单号"):
        require_audit_tag({"remark": "非人工创建", "orderNo": ""})
    with pytest.raises(ValueError, match="单号"):
        require_audit_tag({"remark": "非人工创建", "orderNo": None})
    with pytest.raises(ValueError, match="单号"):
        require_audit_tag({"remark": "非人工创建", "orderNo": "   "})  # 纯空白不算单号
    # 合规形态照旧通过
    assert require_audit_tag({"remark": "非人工创建", "orderNo": "SP1"})


def test_find_document_number_treats_bool_and_composite_as_absent():
    """#117 单一真相判据：bool→"True" 是假单号（回收按 'True' 找不到单），容器形态不算单号。
    数字单号按字符串接受。"""
    assert find_document_number({"orderNo": True}) == ""
    assert find_document_number({"orderNo": {"x": 1}}) == ""
    assert find_document_number({"orderNo": 123}) == "123"
    assert find_document_number({"remark": "x"}) == ""
    assert find_document_number({"documentNo": "  DOC9 "}) == "DOC9"  # 两侧空白剥除


def test_supply_rejects_created_document_without_number(tmp_path):
    """#117 面1C：supply（生产主路径 find_or_create）过去只查备注不查单号——脚本真实建了单
    却没回单号时**静默成功**，回收清单落无单号记录，atlas recycle 显示 '?'，真实建出的单
    永远找不回（回收链静默断，#86 落盘断链同族）。修复后单号闸门与 require_audit_tag 同源。"""
    probe = CallableProbe("SalesOrder", lambda w: None,
                          lambda w: {"remark": "测试非人工创建"})  # 有备注、无单号
    provider = DataProvider(AtlasConfig(tmp_path), [probe], create_quota=5)
    with pytest.raises(ValueError, match="缺少有效单号"):
        provider.supply({"entity": "SalesOrder"})
    # 建单已发生：回收记录必须保留（remark 缺失同策略），供人工按备注/时间追回
    assert len(provider.quota.created) == 1


def test_scripted_creator_where_unserializable_names_path(tmp_path):
    """#117 面2：where 含 YAML 日期对象 → 旧实现裸 TypeError「Object of type date is not
    JSON serializable」——不提脚本路径、不提哪个键，用户面对内部序列化报错无从下手
    （#61 可归因红线）。修复后点名脚本 + 整条键路径 + 类型 + 转换建议。"""
    import datetime
    script = tmp_path / "creator.py"
    script.write_text("import json, sys\njson.load(sys.stdin)\n"
                      "print('{\"remark\":\"s非人工创建\",\"orderNo\":\"SP1\"}')\n", encoding="utf-8")
    creator = make_scripted_creator(script)
    with pytest.raises(TypeError, match="建单脚本") as exc:
        creator({"where": {"createdAfter": datetime.date(2026, 11, 1)}})
    message = str(exc.value)
    assert "createdAfter" in message and "date" in message  # 嵌套路径下钻点名（#61）
    assert str(script) in message                          # 点名是哪个脚本
    with pytest.raises(TypeError, match="dates.0"):        # 列表内坏值也下钻
        creator({"dates": [datetime.date(2026, 1, 1)]})


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

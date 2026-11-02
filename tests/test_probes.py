import pytest
from atlas.config import AtlasConfig
from atlas.failures import EnvBlocked
from atlas.probes import CallableProbe, CreateQuota, DataProvider


def test_find_is_reused_without_create(tmp_path):
    calls = []
    probe = CallableProbe("SalesOrder", lambda where: {"orderNo": "SO1"},
                          lambda where: calls.append(where) or {"remark": "非人工创建"})
    provider = DataProvider(AtlasConfig(tmp_path), [probe])
    for _ in range(5):
        result = provider.supply({"entity": "SalesOrder"})
        assert result.action == "find"
    assert calls == []


def test_find_or_create_only_creates_once_when_data_appears(tmp_path):
    store = []
    def find(where):
        return store[0] if store else None
    def create(where):
        store.append({"orderNo": "SO1", "remark": "测试非人工创建"})
        return store[0]
    provider = DataProvider(AtlasConfig(tmp_path), [CallableProbe("SalesOrder", find, create)])
    actions = [provider.supply({"entity": "SalesOrder"}).action for _ in range(5)]
    assert actions == ["create", "find", "find", "find", "find"]
    assert len(provider.quota.created) == 1


def test_find_only_does_not_fall_back_to_create(tmp_path):
    probe = CallableProbe("SalesOrder", lambda where: None,
                          lambda where: pytest.fail("不应造数"))
    with pytest.raises(EnvBlocked, match="find_only"):
        DataProvider(AtlasConfig(tmp_path), [probe]).supply({"entity": "SalesOrder", "strategy": "find_only"})


def test_production_never_creates(tmp_path):
    provider = DataProvider(AtlasConfig(tmp_path, env="prod-hk"),
                            [CallableProbe("SalesOrder", lambda where: None)])
    with pytest.raises(PermissionError):
        provider.supply({"entity": "SalesOrder"})


def test_create_requires_audit_tag(tmp_path):
    provider = DataProvider(AtlasConfig(tmp_path), [CallableProbe("SalesOrder", lambda w: None,
                                                                 lambda w: {"remark": "人工"})])
    with pytest.raises(ValueError, match="非人工创建"):
        provider.supply({"entity": "SalesOrder"})
    # 建单已真实发生，配额记录必须保留，便于按单号回收。
    assert len(provider.quota.created) == 1


def test_create_quota_exceeded_raises_env_blocked(tmp_path):
    """配额用完后再 supply 必须抛 EnvBlocked，不允许超额造数。"""
    probe = CallableProbe("SalesOrder", lambda w: None, lambda w: {"remark": "测试非人工创建"})
    provider = DataProvider(AtlasConfig(tmp_path), [probe], create_quota=1)
    provider.supply({"entity": "SalesOrder"})
    with pytest.raises(EnvBlocked, match="配额"):
        provider.supply({"entity": "SalesOrder"})


def test_unknown_entity_raises_value_error(tmp_path):
    """未注册 DataProbe 的 entity 必须明确报错，不静默跳过。"""
    provider = DataProvider(AtlasConfig(tmp_path), [])
    with pytest.raises(ValueError, match="未注册"):
        provider.supply({"entity": "Unknown"})


def test_find_strategy_does_not_create_when_found(tmp_path):
    """strategy=find 时 find 成功就不造数。"""
    create_calls = []
    probe = CallableProbe("SalesOrder", lambda w: {"orderNo": "SO1"},
                          lambda w: create_calls.append(1) or {"remark": "非人工创建"})
    provider = DataProvider(AtlasConfig(tmp_path), [probe])
    result = provider.supply({"entity": "SalesOrder", "strategy": "find"})
    assert result.action == "find"
    assert create_calls == []


def test_create_quota_negative_limit_rejected():
    """配额不能为负数。1。"""
    with pytest.raises(ValueError, match="负数"):
        CreateQuota(-1)


def test_create_quota_record_document_updates_existing():
    """record_document 更新已有记录的真实单号。"""
    quota = CreateQuota(3)
    quota.reserve("SalesOrder", {"where": {"status": 1003}})
    quota.record_document("SalesOrder", {"orderNo": "SP1", "remark": "非人工创建"})
    assert quota.created[0]["document"]["orderNo"] == "SP1"


def test_data_requirement_rejects_unknown_field():
    """DataRequirement 拒绝未知字段，避免拼写错误静默通过。"""
    from atlas.probes import DataRequirement
    with pytest.raises(ValueError, match="未知数据供给字段"):
        DataRequirement.from_dict({"entity": "X", "bogus": 1})


def test_data_requirement_rejects_unknown_strategy():
    """DataRequirement 拒绝未知策略。"""
    from atlas.probes import DataRequirement
    with pytest.raises(ValueError, match="未知供给策略"):
        DataRequirement.from_dict({"entity": "X", "strategy": "bogus"})


def test_data_requirement_rejects_invalid_on_env_blocked():
    """DataRequirement 的 on_env_blocked 只支持 skip/stop。"""
    from atlas.probes import DataRequirement
    with pytest.raises(ValueError, match="on_env_blocked"):
        DataRequirement.from_dict({"entity": "X", "on_env_blocked": "bogus"})

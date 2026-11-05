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
    # fixture 给合规结果（含单号）——#117 面1C 后 supply 对无单号建单当场拒绝（回收链
    # 依赖单号），这里测的是「配额闸门」不是单号闸门，故 creator 必须返回可回收的合法单。
    probe = CallableProbe("SalesOrder", lambda w: None,
                          lambda w: {"remark": "测试非人工创建", "orderNo": "SP-Q"})
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


def test_data_requirement_from_dict_validates_shape_and_entity():
    """YAML 数据供给块的用户输入边界（与 state_machine.from_dict #77 同口径）：
    修复前 raw 写成字符串会被 set() 逐字符当成「未知数据供给字段：[' ','e',...]」
    报错——方向完全误导（字段没错，是整块写坏了）；写成 null 裸崩 TypeError；
    漏 entity 键裸崩 KeyError 'entity'。必须点名形态与缺失键。"""
    from atlas.probes import DataRequirement
    with pytest.raises(ValueError, match="必须是映射"):
        DataRequirement.from_dict("some string")
    with pytest.raises(ValueError, match="必须是映射"):
        DataRequirement.from_dict(None)
    with pytest.raises(ValueError, match="缺少 entity"):
        DataRequirement.from_dict({"where": {"customer": "X"}})
    with pytest.raises(ValueError, match="缺少 entity"):
        DataRequirement.from_dict({"entity": "   "})    # 空白字符串同样拒
    with pytest.raises(ValueError, match="缺少 entity"):
        DataRequirement.from_dict({"entity": 123})      # 数字实体名=未注册探针的远端怪错
    # 合法路径不受影响
    req = DataRequirement.from_dict({"entity": "X", "strategy": "find_only"})
    assert req.entity == "X" and req.strategy == "find_only"


def test_supply_rejects_non_dict_creator_result(tmp_path):
    """裸 creator（不包 ScriptedCreator 的直传函数）返回非 dict：旧实现建单后
    要到 remark 检查处 created.get 才 AttributeError——根因（返回值形态）伪装成
    字符串崩溃。必须点名实际类型与值；脏形态不得进 record_document 补记。
    诚实边界：reserve 已占的名额保留（建单确已发生，宁可多占不可漏计）。"""
    from atlas.config import AtlasConfig
    from atlas.probes import CallableProbe, DataProvider
    provider = DataProvider(AtlasConfig(tmp_path), create_quota=3)
    provider.register(CallableProbe("X", finder=lambda w: None, creator=lambda w: "NOT A DICT"))
    with pytest.raises(TypeError, match="必须返回 dict"):
        provider.supply({"entity": "X", "strategy": "create"})
    # 坏返回值没被当单号补记进回收记录
    assert provider.quota.created[0]["document"] == {"where": {}}


def test_save_for_recycle_survives_truncated_utf8_tail(tmp_path):
    """回收清单尾行被上次运行中断腰斩在字节上：旧实现 read_text 裸 UnicodeDecodeError，
    **读崩=合并写回不执行**——本次新造的单号随崩溃一起丢（#86 回收闭环被一条历史坏
    字节掏空，单据已真实产生却再无人知道单号，红线 3「保留可回收信息」失效）。
    #112 读侧同防线的漏网处。修复后残行落既有跳过分支，历史好行+本次新单都在。"""
    import json
    path = tmp_path / "recycle_list.jsonl"
    good = {"entity": "sales_order", "document": "SO-001"}
    path.write_bytes(json.dumps(good, ensure_ascii=False).encode("utf-8") + b"\n"
                     + b'{"entity": "\xe4\xbd')          # 截断的多字节，无尾换行
    quota = CreateQuota(3)
    quota.record_document("sales_order", "SO-002")
    quota.save_for_recycle(path)                          # 不抛（修复前 UnicodeDecodeError）
    records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["document"] for r in records] == ["SO-001", "SO-002"]   # 历史好行 + 本次新单

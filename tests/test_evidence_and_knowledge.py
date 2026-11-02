from atlas.abilities import CallApi
from atlas.contracts import Contract
from atlas.failures import FailureClass, api_evidence
from atlas.knowledge import KnowledgeBase
from atlas.plugins.demo import DemoPlugin


def test_ui_failure_with_healthy_api_is_interaction():
    assert api_evidence(TimeoutError("click"), {"ok": True}) == FailureClass.INTERACTION


def test_ui_failure_with_server_error_is_environment():
    assert api_evidence(TimeoutError(), {"ok": False, "status": 500}) == FailureClass.ENV_BLOCKED


def test_session_failure_is_not_misclassified():
    assert api_evidence(TimeoutError(), {"ok": False, "status": 401}) == FailureClass.SESSION


def test_strong_contract_finds_field_before_ui_timeout():
    contract = Contract("订单", path="/order", level="core", required={"data.list": "array"})
    try:
        contract.validate({"ok": True, "data": {"resultCode": "BUG-000", "data": {}}})
        assert False, "应发现契约缺失"
    except AssertionError as exc:
        assert "data.data.list" in str(exc)


def test_30_reviewed_demo_pitfalls_are_available():
    kb = DemoPlugin().knowledge()
    assert len(kb.pitfalls()) >= 30
    hit = kb.match_pitfall(RuntimeError("BUG-653 Didn't find default tax rate"), "demo")
    assert hit["id"] == "demo-001"


def test_knowledge_write_requires_review(tmp_path):
    import pytest
    with pytest.raises(PermissionError):
        KnowledgeBase(tmp_path).save_reviewed("pitfalls", [])


def test_demo_api_adapter_does_not_load_state_file(tmp_path):
    """不依赖本机是否存在 ../legacy-api；用最小假项目验证适配契约。

    真实 CI/Linux 上没有同级 legacy-api 仓库，硬编码相对路径会让测试仅在本机通过。
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "demo_api.py").write_text(
        "STATIC_HEADERS = {'demo-app': 'legacy-web', 'X-Requested-With': 'XMLHttpRequest'}\n"
        "class DemoApi:\n"
        "    def _load_spec(self, path):\n"
        "        return {'endpoints': []}\n",
        encoding="utf-8")
    (scripts / "demo_endpoints.json").write_text('{"endpoints": []}', encoding="utf-8")

    api = CallApi("https://example.invalid")
    adapted = api.attach_demo(tmp_path)
    assert adapted.sess is api.session
    # 绕过原 __init__ 时必须补齐 STATIC_HEADERS，否则后端回 BUG-920 app is null。
    assert api.session.headers.get("demo-app") == "legacy-web"
    assert "endpoints" in adapted.spec


def test_weak_contract_only_checks_generic_rule():
    from atlas.contracts import Contract
    weak = Contract("列表", path="/list", level="weak")
    weak.validate({"ok": True, "data": {"resultCode": "BUG-000", "data": []}})
    try:
        weak.validate({"ok": True, "data": {"data": []}})
        assert False, "weak 契约应要求 resultCode"
    except AssertionError as exc:
        assert "resultCode" in str(exc)


def test_unknown_contract_level_is_rejected():
    from atlas.contracts import Contract
    try:
        Contract("x", path="/x", level="bogus").validate({"ok": True, "data": {"resultCode": "A"}})
        assert False, "未知等级必须报错"
    except ValueError as exc:
        assert "未知契约等级" in str(exc)


def test_selector_registry_loads_all_components_with_selectors():
    """知识库的组件注册表必须完整：每个组件都有可定位的选择器。"""
    registry = DemoPlugin().selectors()
    assert len(registry.names()) >= 5
    # missing() 列出缺选择器的组件；知识库应保证全部可定位
    assert registry.missing() == []


def test_selector_registry_get_and_find():
    registry = DemoPlugin().selectors()
    component = registry.names()[0]
    spec = registry.find(component)
    assert "selectors" in spec
    # get 取具体元素选择器
    element = next(iter(spec["selectors"]))
    selector = registry.get(component, element)
    assert isinstance(selector, str) and selector


def test_selector_registry_raises_on_unknown():
    import pytest
    registry = DemoPlugin().selectors()
    with pytest.raises(KeyError, match="组件未注册"):
        registry.find("nonexistent_component")
    with pytest.raises(KeyError, match="选择器未注册"):
        registry.get(registry.names()[0], "nonexistent_element")


def test_knowledge_draft_pitfall_is_pending_review():
    """draft_pitfall 生成待审核草稿，不直接写正式知识库。"""
    from pathlib import Path
    kb = KnowledgeBase(Path(__file__).resolve().parents[1] / "src/atlas/plugins/demo/knowledge")
    draft = kb.draft_pitfall(RuntimeError("BUG-999"), symptom="测试坑点",
                             root_cause="根因", remedy="修复", scope=["demo"], source="test")
    assert draft["status"] == "pending_review"
    assert draft["id"].startswith("draft-")
    assert draft["trigger"] == "RuntimeError"
    assert draft["remedy"] == "修复"


def test_selector_registry_draft_update_is_pending_review():
    """draft_update 生成待审核的选择器更新草稿。"""
    registry = DemoPlugin().selectors()
    draft = registry.draft_update("payment_terms", "select", ".pt-selector")
    assert draft["status"] == "pending_review"
    assert draft["component"] == "payment_terms"
    assert draft["selector"] == ".pt-selector"


def test_classify_failure_categorizes_all_classes():
    """classify_failure 必须覆盖所有失败类别。"""
    from atlas.failures import classify_failure, FailureClass
    assert classify_failure(TimeoutError("element not visible")) == FailureClass.INTERACTION
    assert classify_failure(RuntimeError("401 unauthorized")) == FailureClass.SESSION
    assert classify_failure(RuntimeError("bug-999")) == FailureClass.ENV_BLOCKED
    assert classify_failure(AssertionError("mismatch")) == FailureClass.ASSERTION
    assert classify_failure(RuntimeError("not found")) == FailureClass.DATA
    assert classify_failure(None) == FailureClass.UNKNOWN


def test_api_evidence_classifies_5xx_as_env_blocked():
    """API 返回 5xx 归类为环境阻塞。"""
    from atlas.failures import api_evidence, FailureClass
    assert api_evidence(TimeoutError("x"), {"ok": False, "status": 500}) == FailureClass.ENV_BLOCKED
    assert api_evidence(TimeoutError("x"), {"ok": False, "status": 503}) == FailureClass.ENV_BLOCKED


def test_api_evidence_auth_expired_in_data_is_session():
    """响应 data 里 auth_expired=true 归类为会话失效。"""
    from atlas.failures import api_evidence, FailureClass
    assert api_evidence(TimeoutError("x"), {"ok": False, "status": 200,
                                             "data": {"auth_expired": True}}) == FailureClass.SESSION

from atlas.abilities import CallApi
from atlas.contracts import Contract
from atlas.failures import FailureClass, api_evidence
from atlas.knowledge import KnowledgeBase, SelectorRegistry
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


def test_empty_trigger_pitfall_never_matches(tmp_path):
    """数据完整性防线：缺/空 trigger 的坑点若参与匹配会命中所有异常（`"" in text` 恒真），
    can_auto_heal 据此对任意只读失败重跑无关修复。必须拒绝匹配。"""
    from atlas.knowledge import KnowledgeBase
    from atlas.healing import Healer
    from atlas.core import Channel, FunctionalTask, TaskMetadata
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [
        {"id": "bad", "trigger": "", "remedy": "无关修复", "scope": ["demo"],
         "status": "approved"},
        {"id": "missing"},  # 连 trigger 键都没有
    ], approved=True)
    for exc in (TimeoutError("element not visible"), RuntimeError("401 unauthorized"),
                ValueError("anything")):
        assert kb.match_pitfall(exc, "demo") is None
    task = FunctionalTask(TaskMetadata(domain="demo", intent="x", channel=Channel.UI,
                                       read_only=True), lambda a: None, "t")
    assert Healer(kb).can_auto_heal(task, RuntimeError("anything")) is False


def test_knowledge_write_requires_review(tmp_path):
    import pytest
    with pytest.raises(PermissionError):
        KnowledgeBase(tmp_path).save_reviewed("pitfalls", [])


def test_corrupt_private_knowledge_falls_back_to_package(tmp_path):
    """私有目录写坏的 JSON（截断/空）不得顶掉整个知识子系统：逐文件回退包内虚构默认（读不残缺）。
    修复前 load 见私有文件存在就 json.loads → 抛错，doctor/selectors/review --approve（本身要读库）/run 全崩，
    且塔因读不到库而清不动。"""
    import json
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "pitfalls.json").write_text(json.dumps({"pitfalls": [
        {"id": "p1", "trigger": "BUG-999", "remedy": "r", "scope": ["demo"]}]}), encoding="utf-8")
    priv = tmp_path / "priv"; priv.mkdir()
    (priv / "pitfalls.json").write_text('{"pitfalls": [{"trigger": "x", "rem', encoding="utf-8")  # 截断残 JSON
    kb = KnowledgeBase(priv, fallback_root=pkg)
    assert kb.pitfalls()[0]["id"] == "p1"                         # 不抛，回退到包内
    (priv / "pitfalls.json").write_text("", encoding="utf-8")     # 空文件同样回退
    assert kb.pitfalls()[0]["id"] == "p1"
    assert kb.match_pitfall(RuntimeError("BUG-999 boom"), "demo")["id"] == "p1"  # 知识子系统仍可用


def test_corrupt_knowledge_both_sources_returns_empty(tmp_path):
    """私有+包内都坏：返回空（调用点已 .get/or 兜底），不抛穿执行链。"""
    priv = tmp_path / "priv"; priv.mkdir()
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (priv / "components.json").write_text("{{{ broken", encoding="utf-8")
    (pkg / "components.json").write_text("}}} broken", encoding="utf-8")
    kb = KnowledgeBase(priv, fallback_root=pkg)
    assert kb.load("components") == {}
    assert kb.load_overlays() == {}                               # 下游 .get 不崩


def test_legal_json_wrong_shape_does_not_poison_read_paths(tmp_path):
    """#127 读路径：私有库是人可手改的，「合法 JSON 但形态不对」旧实现全部放过——
    顶层数组/坑点行 trigger·scope 为 null（键在值 null 默认不生效 #77）穿透到
    pitfalls/match_pitfall 消费点当场 AttributeError/TypeError，顶掉 engine 失败路径与
    自愈准入（定性器先于被定性对象死 #89）。修复后：load 顶层形态同坏字节回退；
    坏行逐条跳过（#65 reporting 同纪律）。兼容面（#115）：好行照常命中、不翻错。"""
    import json
    priv = tmp_path / "priv"; priv.mkdir()
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "pitfalls.json").write_text(json.dumps({"pitfalls": [
        {"id": "p1", "trigger": "TimeoutError", "remedy": "r", "scope": ["demo"],
         "status": "approved"}]}), encoding="utf-8")
    kb = KnowledgeBase(priv, fallback_root=pkg)

    # 面1：顶层合法 JSON 但是数组 → 视同损坏，回退包内默认（旧实现 return 列表，
    # pitfalls() 的 .get 当场 AttributeError）
    (priv / "pitfalls.json").write_text('[{"trigger": "x"}]', encoding="utf-8")
    assert kb.pitfalls()[0]["id"] == "p1"
    # 面2：行内 scope 键在值 null（domain 过滤 `in None` TypeError）→ 坏行跳过
    (priv / "pitfalls.json").write_text(json.dumps({"pitfalls": [
        {"id": "bad", "trigger": "TimeoutError", "scope": None},
        {"id": "p1", "trigger": "TimeoutError", "scope": ["demo"], "status": "approved"}]}),
        encoding="utf-8")
    kb2 = KnowledgeBase(priv)
    assert [r["id"] for r in kb2.pitfalls("demo")] == ["p1"]
    # 面3：trigger 键在值 null（None.strip AttributeError）→ 不参与匹配、max 的 key 也不炸
    (priv / "pitfalls.json").write_text(json.dumps({"pitfalls": [
        {"id": "null-trig", "trigger": None, "scope": ["demo"]},
        {"id": "p1", "trigger": "TimeoutError", "scope": ["demo"]}]}), encoding="utf-8")
    assert kb2.pitfalls()[0]["id"] == "null-trig"          # 坑点仍可读（列表原样）
    assert kb2.match_pitfall(TimeoutError("boom"), "demo")["id"] == "p1"  # 匹配跳过坏行

    # 面6：异常 __str__ 自身崩（业务插件真实雷区）——classify_failure 与 match_pitfall
    # 共用的定性文本收敛 exception_text 单一真相（#92），回落类型名继续定性，绝不裸抛
    class Nasty(Exception):
        def __str__(self): raise AttributeError("detail 未初始化")
    from atlas.failures import classify_failure, FailureClass
    assert classify_failure(Nasty()) is not None
    (priv / "pitfalls.json").write_text(json.dumps({"pitfalls": [
        {"id": "t-name", "trigger": "nasty", "scope": ["demo"]}]}), encoding="utf-8")
    assert kb2.match_pitfall(Nasty(), "demo")["id"] == "t-name"  # 类型名仍是有效定性信号


def test_approve_write_survives_hand_mangled_library(tmp_path):
    """#127 写路径：批准（review --approve 唯一入库路径）要读旧库合并写回——旧实现
    「components」「overlays」键值坏形态（手滑成列表/BU 子映射坏）直接 setdefault/下标
    AttributeError，批准半路崩、塔永远清不动（#73 场景同族）。修复后：坏形态当空重建、
    回退包内的好数据不丢；SelectorRegistry 读侧 selectors 键值 null 按未注册归因，
    不再 None[element] 裸 TypeError 逃逸友好报错（面4）。"""
    import json
    priv = tmp_path / "priv"; priv.mkdir()
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "components.json").write_text(json.dumps({"components": {
        "旧组件": {"selectors": {"a": ".x"}}}}), encoding="utf-8")
    (pkg / "bu_overlays.json").write_text(json.dumps({"overlays": {
        "BU-A": {"ou": "OU0"}}}), encoding="utf-8")
    kb = KnowledgeBase(priv, fallback_root=pkg)

    # 顶层合法 JSON 数组：批准回退包内 dict 合并写回，不崩、旧组件保留
    (priv / "components.json").write_text('[{"历史": "残留"}]', encoding="utf-8")
    out = kb.save_component_selector("新组件", "按钮", ".btn", approved=True, source="test")
    saved = json.loads((priv / "components.json").read_text(encoding="utf-8"))
    assert isinstance(saved, dict) and not out["overwritten"]
    assert saved["components"]["旧组件"]["selectors"]["a"] == ".x"    # copy-on-write 不丢好数据
    assert saved["components"]["新组件"]["selectors"]["按钮"] == ".btn"

    # "components" 键在值 null：当空库重建（#77 默认值不生效同族）
    (priv / "components.json").write_text('{"components": null}', encoding="utf-8")
    kb.save_component_selector("再来", "b", ".y", approved=True, source="test")
    saved = json.loads((priv / "components.json").read_text(encoding="utf-8"))
    assert saved["components"]["再来"]["selectors"]["b"] == ".y"

    # overlays 键值坏 + BU 子映射坏：批准坐标不再半路崩
    (priv / "bu_overlays.json").write_text('{"overlays": [1, 2]}', encoding="utf-8")
    kb.save_overlay("BU-B", "ou", "OU9", approved=True, source="test")
    assert kb.load_overlays()["BU-B"]["ou"] == "OU9"

    # SelectorRegistry 读侧归因：spec 非映射整条当未注册、selectors 键值 null 点名缺失
    reg = SelectorRegistry({"components": {"坏条目": "字符串",
                                           "销售单列表": {"selectors": None}}})
    import pytest
    with pytest.raises(KeyError, match="选择器未注册"):
        reg.get("销售单列表", "按钮")
    with pytest.raises(KeyError, match="组件未注册"):
        reg.find("坏条目")                                        # 坏条目不进 find/names
    assert reg.names() == ["销售单列表"]


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


def test_core_contract_number_field_rejects_boolean():
    """core 档 number 字段不能因 bool 是 int 子类而放行（isinstance(True,(int,float)) 恒真）。
    数值字段序列化出 true/false 属契约缺陷必须拒绝，含嵌套列表元素；
    boolean 字段仍拒绝 int 冒充，证明只收紧 number 一侧，不影响其它。"""
    import pytest
    from atlas.contracts import Contract
    from atlas.failures import ContractViolation
    c = Contract("订单", path="/order", level="core",
                 required={"qty": "number", "items.0.price": "number"})
    c.validate({"ok": True, "data": {"resultCode": "B", "qty": 3,
                                     "items": [{"price": 1}]}})            # int 放行
    c.validate({"ok": True, "data": {"resultCode": "B", "qty": 1.5,
                                     "items": [{"price": 2}]}})           # float 放行
    for bad in (True, False):
        with pytest.raises(ContractViolation, match="number"):
            c.validate({"ok": True, "data": {"resultCode": "B", "qty": bad,
                                             "items": [{"price": 1}]}})
    with pytest.raises(ContractViolation, match="number"):                # 列表元素同样受保护
        c.validate({"ok": True, "data": {"resultCode": "B", "qty": 3,
                                         "items": [{"price": True}]}})
    c2 = Contract("标志", path="/f", level="core", required={"active": "boolean"})
    with pytest.raises(ContractViolation, match="boolean"):               # 未被放宽
        c2.validate({"ok": True, "data": {"resultCode": "B", "active": 1}})


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


def test_private_knowledge_dir_overrides_with_fallback(tmp_path, monkeypatch):
    """ATLAS_KNOWLEDGE_DIR：读逐文件回退包内虚构默认，写只落私有目录——仓库零污染。"""
    priv = tmp_path / "private_kb"
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(priv))
    plugin = DemoPlugin()
    kb = plugin.knowledge()
    assert kb.root == priv
    assert len(kb.pitfalls()) > 0  # 包内 30 条经 fallback 仍可见
    kb.save_component_selector("probe_page", "save_button", "button#probe-save",
                               approved=True)
    assert (priv / "components.json").exists()
    pkg_components = (plugin.knowledge_root / "components.json").read_text(encoding="utf-8")
    assert "probe_page" not in pkg_components  # 包内文件一个字节没动


def test_default_knowledge_dir_unchanged_without_env(monkeypatch):
    """不设 ATLAS_KNOWLEDGE_DIR 时读行为与历史一致（回退包内虚构默认）——但写根现在是 None。

    #135 契约更新：旧断言 `root == knowledge_root`（"行为与历史完全一致"）钉的正是
    违反 AGENTS 红线 #11 的旧行为——批准 `--approve/--apply` 会经 root 写进包内 git 资产
    （探针实锤 pitfalls.json 30→31）。现 root=None=只读库：读照常回退、写当场点名要私有目录。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    plugin = DemoPlugin()
    assert plugin.knowledge().root is None                    # 无 env → 只读，批准不落包内
    assert plugin.knowledge().fallback_root == plugin.knowledge_root   # 读仍回退包内
    assert plugin.session_defaults().get("account")           # 只读链照常工作


def test_readonly_kb_save_reviewed_denied_names_env(tmp_path):
    """#135：只读库（root=None）的任何 save_* 当场 PermissionError 点名 ATLAS_KNOWLEDGE_DIR，
    绝不落盘、绝不返回成功——批准是唯一写路径，闸门必须堵住而非静默吞。"""
    import pytest
    kb = KnowledgeBase(None, fallback_root=tmp_path)   # 模拟未配私有目录、读回退包内
    with pytest.raises(PermissionError, match="ATLAS_KNOWLEDGE_DIR"):
        kb.save_reviewed("pitfalls", [{"id": "x"}], approved=True)
    # 读照常：回退到 fallback，不因写闸门而读不到
    (tmp_path / "pitfalls.json").write_text('{"pitfalls": [{"id": "r1"}]}', encoding="utf-8")
    assert kb.pitfalls()[0]["id"] == "r1"
    # save_overlay/save_component_selector 同闸门（批准写路径三兄弟一个都不能绕过）
    with pytest.raises(PermissionError, match="ATLAS_KNOWLEDGE_DIR"):
        kb.save_overlay("BU-A", "ou", "X", approved=True)
    with pytest.raises(PermissionError, match="ATLAS_KNOWLEDGE_DIR"):
        kb.save_component_selector("c", "e", "sel", approved=True)


def test_classify_failure_categorizes_all_classes():
    """classify_failure 必须覆盖所有失败类别。"""
    from atlas.failures import classify_failure, FailureClass
    assert classify_failure(TimeoutError("element not visible")) == FailureClass.INTERACTION
    assert classify_failure(RuntimeError("401 unauthorized")) == FailureClass.SESSION
    assert classify_failure(RuntimeError("bug-999")) == FailureClass.ENV_BLOCKED
    assert classify_failure(AssertionError("mismatch")) == FailureClass.ASSERTION
    assert classify_failure(RuntimeError("not found")) == FailureClass.DATA
    assert classify_failure(None) == FailureClass.UNKNOWN


def test_classify_control_flow_is_interrupted_not_unknown():
    """#114 面B：终止信号必须归 interrupted——旧实现 KI/SystemExit/GeneratorExit 全落
    unknown，atlas summary 缺陷分布冒出一类无法归因的「未知失败」，把操作者停手伪装成
    待诊断缺陷（违反 #70 interrupted 终态口径）。先于关键词匹配：类型名恰好含无关
    子串（如自造 KeyboardInterruptTimeoutError 含 'timeout'）时不得被误判成 interaction。"""
    from atlas.failures import classify_failure, FailureClass
    assert classify_failure(KeyboardInterrupt()) == FailureClass.INTERRUPTED
    assert classify_failure(SystemExit(1)) == FailureClass.INTERRUPTED
    assert classify_failure(GeneratorExit()) == FailureClass.INTERRUPTED
    assert classify_failure(TimeoutError("KeyboardInterrupt then timeout")) == FailureClass.INTERACTION


def test_classify_prod_write_guard_is_env_blocked():
    """#114 面A：生产写护栏拒绝归 env_blocked（策略阻挡，非代码缺陷，红线 #7）。
    旧实现 PermissionError("生产环境禁止…") 落 unknown——确定性护栏伪装成待诊断缺陷。
    对照：同为 PermissionError 的审批门 ApprovalRequired 消息不含护栏短语，绝不能被
    「生产环境禁止」误吞成 env_blocked（否则任何权限异常都被翻成环境阻挡，掩盖真实根因）。"""
    import pytest
    from atlas.agent import ApprovalRequired
    from atlas.config import AtlasConfig
    from atlas.failures import classify_failure, FailureClass
    with pytest.raises(PermissionError) as exc:
        AtlasConfig(__import__("pathlib").Path("."), env="prod-hk").validate_write("非人工创建")
    assert classify_failure(exc.value) == FailureClass.ENV_BLOCKED
    assert classify_failure(ApprovalRequired("风险 high 超过自动阈值")) != FailureClass.ENV_BLOCKED


def test_classify_chinese_data_absent_before_assertion():
    """真实回归：select_customer 抛 `AssertionError: 客户查询/选中失败: 查询无结果`，
    根因是数据缺失（换真实客户号），曾被误判成 assertion（当成测试太严）。ICS 中文「查无」
    必须在通用断言之前判成 data；但更具体的 ENV/SESSION/CONTRACT 根因仍优先。"""
    from atlas.failures import classify_failure, FailureClass
    assert classify_failure(AssertionError("客户查询/选中失败: 查询无结果")) == FailureClass.DATA
    assert classify_failure(AssertionError("订单不存在")) == FailureClass.DATA
    assert classify_failure(RuntimeError("无数据返回, 结果为空")) == FailureClass.DATA
    # 真正的断言不受影响
    assert classify_failure(AssertionError("expected NET30 got NET45")) == FailureClass.ASSERTION
    # 更具体根因优先：401 包着「查询无结果」仍是 session
    assert classify_failure(AssertionError("查询无结果 (401 unauthorized)")) == FailureClass.SESSION
    # 契约的 "cannot be empty" 不被 data 的泛 "empty" 抢走（CONTRACT 在数据缺失信号前判）
    assert classify_failure(RuntimeError("field cannot be empty")) == FailureClass.CONTRACT


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


def test_api_evidence_string_status_no_crash_no_misdetect():
    """HAR/JMX/录制抓包常把 status 存成字符串。旧实现 `"500" >= 500` 让定性器 TypeError 裸崩
    （engine.prove_api 无 try 时顶掉原用例异常），`"401" in {401,403}` 恒 False 让会话证据静默
    漏判、定性翻转。与 data 形态零假设（#89）同纪律：status 形态由重放来源决定，归一化不猜不崩。"""
    from atlas.failures import api_evidence, FailureClass
    exc = TimeoutError("element not visible")
    # 字符串数字：归一化后走正确定性（旧：500 崩、401 漏判后也崩）
    assert api_evidence(exc, {"ok": False, "status": "500", "data": "网关错误"}) == FailureClass.ENV_BLOCKED
    assert api_evidence(exc, {"ok": False, "status": "401", "data": {"e": 1}}) == FailureClass.SESSION
    # 非数值 status 归 None，走文本定性，不崩
    assert api_evidence(exc, {"ok": False, "status": "OK", "data": "订单不存在"}) == FailureClass.DATA
    # 无 data/error 文本：classify 得 UNKNOWN，按 #89 既有回落保守归 env_blocked
    assert api_evidence(exc, {"ok": False, "status": None}) == FailureClass.ENV_BLOCKED
    # 有 timeout 文本：走文本定性归 interaction
    assert api_evidence(exc, {"ok": False, "status": None, "data": "timeout 已超时"}) == FailureClass.INTERACTION
    # bool 是 int 子类但非状态码：True 不当 1，走文本定性
    assert api_evidence(exc, {"ok": False, "status": True, "data": "401 unauthorized"}) == FailureClass.SESSION


def test_task_metadata_rejects_str_as_tuple_and_list():
    """契约核心在构造期落地 tuple[str] 承诺。高频笔误：单元素漏尾逗号 produces=('value')
    实为 str——frozen 数据类不校验类型，一路静默到拼装才崩，且 compose_for_goal 的
    set(produces) 逐字符迭代成 {'v','a','l','u','e'}，反报「没有 Task 能产出：value」
    （作者明明写了 value，误导方向完全反）。requires 同样被拆成单字符永不满足。
    tags 参与 hash/eq，list 会在集合使用点裸崩。全部必须构造期点名。"""
    import pytest
    from atlas.core import TaskMetadata
    with pytest.raises(ValueError, match="produces.*tuple.*尾逗号"):
        TaskMetadata(domain="demo", intent="read", produces=("value"))
    with pytest.raises(ValueError, match="requires.*tuple"):
        TaskMetadata(domain="demo", intent="read", requires=("dep"))
    with pytest.raises(ValueError, match="tags.*tuple"):
        TaskMetadata(domain="demo", intent="read", tags=("legacy"))
    with pytest.raises(ValueError, match="list"):
        TaskMetadata(domain="demo", intent="read", produces=["value"])
    with pytest.raises(ValueError, match="非空 str"):
        TaskMetadata(domain="demo", intent="read", produces=("ok", ""))
    # 合法 tuple 一律放行——尤其带尾逗号单元素与 DSL 已经 tuple() 转换的路径
    TaskMetadata(domain="demo", intent="read", produces=("value",), requires=())
    TaskMetadata(domain="demo", intent="read", tags=("a", "b"))


def test_tuple_typo_no_longer_misleads_composition():
    """端到端钉死：写坏 produces 不再静默漂移后误导报错。修复前 compose_for_goal 报
    「没有 Task 能产出：value」让人以为缺 Task；现在根本构造不出这个 Task。"""
    import pytest
    from atlas.core import FunctionalTask, TaskMetadata
    from atlas.composition import compose_for_goal
    with pytest.raises(ValueError, match="produces"):
        bad = FunctionalTask(TaskMetadata(domain="demo", intent="read", produces=("value")),
                             lambda a: {}, "read")
        compose_for_goal([bad], "value")


def test_api_evidence_non_dict_data_never_crashes_classifier():
    """定性器不得比被定性对象先死。ICS 列表接口 data 常为数组（#75 实证）、
    网关错误常为字符串、失败响应常为 null——旧实现 `replay.get("data", {}).get(...)`
    假设 data 恒为 dict，对 list/str/None 直接 AttributeError 裸崩，失败现场证据全丢。
    data 为 null 时还要回落到 error 取真实根因（dict.get 对 None 不生效默认值，
    str(None)="none" 会把 error 挤出定性文本）。"""
    from atlas.failures import api_evidence, FailureClass
    exc = RuntimeError("click timeout")
    # 数组 data：不崩；无具体根因时按原语义落 ENV_BLOCKED 兜底
    assert api_evidence(exc, {"ok": False, "status": 200, "data": []}) == FailureClass.ENV_BLOCKED
    # 列表 data 里带嵌套对象：auth_expired 标志只在顶层 dict 才被认作会话信号，
    # 中性业务列表不得被误判，且整个定性过程不崩（旧实现此处 AttributeError 裸崩）
    assert api_evidence(exc, {"ok": False, "status": 200,
                              "data": [{"orderNo": "SO-1"}]}) == FailureClass.ENV_BLOCKED
    # 字符串 data + 5xx：先按状态码归环境，不崩
    assert api_evidence(exc, {"ok": False, "status": 502, "data": "Bad Gateway"}) == FailureClass.ENV_BLOCKED
    # 401 会话判定不依赖 data 形态
    assert api_evidence(exc, {"ok": False, "status": 401, "data": []}) == FailureClass.SESSION
    # data=null：定性文本取 error 而不是字符串 "None"
    assert api_evidence(exc, {"ok": False, "status": 400,
                               "data": None, "error": "field cannot be empty"}) == FailureClass.CONTRACT
    # dict data 原路径行为不变（BUG-653 仍归 ENV_BLOCKED）
    assert api_evidence(exc, {"ok": False, "status": 200,
                              "data": {"resultCode": "BUG-653"}}) == FailureClass.ENV_BLOCKED

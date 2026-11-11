"""任务 2.6 验收：第二个真实业务插件（iservice）——证明「换业务只换插件」。

离线、无网络/浏览器/凭据。四组验收对应任务书：
① resolve_plugin() 能解析新插件（ATLAS_PLUGIN=iservice）；
② 引擎源码不含该业务字面量（plugin_api 未被硬编码 + 非插件代码无 iservice 专属 token）；
③ 新插件组件库能被 SelectorRegistry 读、能被 emit_pages_module 派生页对象；
④ 新旧插件互不干扰（demo 照常解析、知识互不遮蔽）。

外加：Task 元数据 produces/requires 与实际 ctx 读写一致（跑通引擎的 write_produces 闸）；
契约分档；坑点 scope 命中 domain。测试跑在 conftest 的干净 env（ATLAS_* 已剥）下，
故读到的是包内默认知识而非本机私有 ATLAS_KNOWLEDGE_DIR。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from atlas.cli import main

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "src" / "atlas" / "plugins" / "iservice"

# 引擎（非插件代码）里绝不该出现的 iService 专属 token。刻意不列裸 "iservice"：
# config.py 注释、demo/legacy_case.py 的 TASK_DOMAIN 早已合法含有该词（历史迁移），
# 本测钉的是「本波新增的插件没漏进引擎」——用插件类名/包路径/专属模块/专属选择器。
BUSINESS_TOKENS = (
    "IservicePlugin",
    "plugins.iservice",
    "plugins/iservice",
    "iservice-spm",
    "iservice-tsp",
    "spare_parts_selection_dialog",
)


# ---- ① resolve_plugin 解析新插件 ----

def test_resolve_plugin_iservice(monkeypatch):
    monkeypatch.setenv("ATLAS_PLUGIN", "iservice")
    from atlas.plugin_api import resolve_plugin
    from atlas.plugins.iservice import IservicePlugin
    plugin = resolve_plugin()
    assert isinstance(plugin, IservicePlugin)
    assert plugin.name == "iservice"
    # 八契约成员齐（与 DemoPlugin 一致，可被 cli/runtime 无差别消费）。
    for member in ("name", "allowed_api_operations", "knowledge", "selectors", "contracts",
                   "session_defaults", "register_tasks", "make_provider"):
        assert hasattr(plugin, member), f"缺少契约成员 {member}"
    # 未接接口客户端：白名单如实留空（红线 #12，不伪造操作名）。
    assert plugin.allowed_api_operations == frozenset()


def test_cli_contracts_resolves_iservice_end_to_end(tmp_path, monkeypatch, capsys):
    """最强端到端：CLI `atlas contracts` 走真实入口 `resolve_plugin()` 取 iservice 契约。

    注：`atlas tasks`/`atlas compose` 是引擎里 demo 硬编码的既有耦合（cli.py 直接 import
    demo 的 core_tasks，非 resolve_plugin）——本波不去动引擎，故用真正走插件缝的
    `atlas contracts`/`atlas selectors` 做端到端。"""
    monkeypatch.setenv("ATLAS_PLUGIN", "iservice")
    assert main(["--root", str(tmp_path), "contracts"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 7
    assert {"core", "high", "minimal"} <= set(report["levels"])
    assert any("iservice-spm" in c["path"] for c in report["contracts"])


def test_full_registry_registers_iservice_tasks(monkeypatch):
    """引擎的 compile/run 共用 `_full_registry()` 也走 resolve_plugin 缝——Task 注册到位。"""
    monkeypatch.setenv("ATLAS_PLUGIN", "iservice")
    from atlas.cli import _full_registry
    names = set(_full_registry().names())
    assert {"open_doc_list", "click_save", "wait_save_result", "fill_return_reason"} <= names


# ---- ② 引擎源码不含该业务字面量 ----

def _engine_files():
    """引擎源码 = src/atlas 下除 src/atlas/plugins/**（插件是业务资产）外的全部 .py。"""
    out = []
    for p in (ROOT / "src" / "atlas").rglob("*.py"):
        if "plugins" in p.relative_to(ROOT / "src" / "atlas").parts:
            continue
        out.append(p)
    return out


def test_engine_source_has_no_iservice_business_tokens():
    assert _engine_files(), "扫描面为空=判据失效"   # 防静默假绿
    for path in _engine_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in BUSINESS_TOKENS:
            assert token not in text, f"引擎源码 {path.relative_to(ROOT)} 含业务字面量 {token!r}"


def test_plugin_api_registry_not_hardcoded_for_iservice():
    """引擎的插件注册表未被为接入 iservice 改一行——接入只靠插件包自注册/动态加载。"""
    src = (ROOT / "src" / "atlas" / "plugin_api.py").read_text(encoding="utf-8")
    assert "iservice" not in src.lower(), "plugin_api.py 不得硬编码业务 domain"


# ---- ③ 组件库可读、可派生页对象 ----

def _iservice_plugin(monkeypatch):
    monkeypatch.setenv("ATLAS_PLUGIN", "iservice")
    from atlas.plugin_api import resolve_plugin
    return resolve_plugin()


def test_selector_registry_reads_iservice_components(monkeypatch):
    from atlas.knowledge import SelectorRegistry
    plugin = _iservice_plugin(monkeypatch)
    registry = plugin.selectors()
    names = registry.names()
    assert isinstance(registry, SelectorRegistry)
    assert {"login", "navigation_menu", "sales_order_header", "spare_part_dialog"} <= set(names)
    # 结构化 ARIA（role+name）与 CSS 两形态都能取到原值，且 describe 不崩。
    assert registry.get("navigation_menu", "menu_spare_parts") == {
        "role": "menuitem", "name": "Spare Parts"}
    assert registry.get("login", "login_button") == "button.login-button:visible"
    for name in names:
        for element in registry.find(name).get("selectors", {}):
            assert isinstance(registry.describe(name, element), str)


def test_components_derive_page_objects(monkeypatch):
    from atlas.generator.emit_pytest import emit_pages_module
    plugin = _iservice_plugin(monkeypatch)
    src = emit_pages_module(plugin.knowledge().load("components"))
    classes = set(re.findall(r"^class (\w+):", src, flags=re.MULTILINE))
    assert {"Login", "NavigationMenu", "SalesOrderHeader", "SparePartDialog"} <= classes
    # 含结构化 ARIA → 页对象库派生 ARIA 分支（不是第二把尺，是 locators.KINDS 的派生物）。
    assert "get_by_role" in src
    # 只生成不执行：操作骨架一律显式失败，绝不假装通过（红线 #9/#7）。
    assert "NotImplementedError" in src


# ---- ④ 新旧插件互不干扰 ----

def test_demo_still_resolves_when_iservice_present(monkeypatch):
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    from atlas.plugin_api import resolve_plugin
    from atlas.plugins.demo import DemoPlugin
    assert isinstance(resolve_plugin(), DemoPlugin)


def test_two_plugins_have_independent_knowledge(monkeypatch):
    from atlas.plugins.demo import DemoPlugin
    from atlas.plugins.iservice import IservicePlugin
    demo, isvc = DemoPlugin(), IservicePlugin()
    assert demo.knowledge_root != isvc.knowledge_root
    # 业务 token 不串门：iservice 组件库独有 ou_switch/ccm_export，demo 独有 order_header。
    assert "ou_switch" in isvc.selectors().names()
    assert "ou_switch" not in demo.selectors().names()
    assert "order_header" in demo.selectors().names()
    assert "order_header" not in isvc.selectors().names()


# ---- Task 元数据与 ctx 读写一致 ----

def _fake_pages_module(tmp_path: Path, functions) -> Path:
    """写一个实现全部 task 函数的假页面库（返回 {}，让单产出折叠出 produces 键）。"""
    body = "\n".join(f"def {name}(page, **kwargs):\n    return {{}}\n" for name in functions)
    path = tmp_path / "pages.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_task_produces_match_ctx_writes(tmp_path):
    """每个 Task 经引擎执行后，声明 produces 的键必须真的写进 actor.ctx（write_produces 闸）。"""
    from atlas import Actor
    from atlas.abilities import BrowseTheWeb
    from atlas.config import AtlasConfig
    from atlas.engine import ExecutionEngine
    from atlas.plugins.iservice import core_tasks

    tasks = core_tasks()
    pages = _fake_pages_module(tmp_path, [t.name for t in tasks])
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    for task in core_tasks(pages):
        actor = Actor(abilities=[BrowseTheWeb(object())])
        result = engine.execute(actor, task)
        assert result.ok, f"{task.name} 执行失败：{result}"
        for key in task.metadata.produces:
            assert key in actor.ctx, f"{task.name} 声明 {key!r} 未写入 ctx"


def test_task_requires_graph_is_composable():
    """requires 图自洽：compose 到目标产出时，前置键都能被上游 produces 满足（无悬空依赖）。"""
    from atlas.composition import compose_for_goal
    from atlas.plugins.iservice import core_tasks

    tasks = core_tasks()
    # 目标 = 某个产出键 → 计划以「生产该键的 Task」收尾。
    assert [t.name for t in compose_for_goal(tasks, "save_result")][-1] == "click_save"
    assert [t.name for t in compose_for_goal(tasks, "document_no")][-1] == "wait_save_result"
    # 退货链目标同样可拼（source_order → return_reason_ready）。
    assert [t.name for t in compose_for_goal(tasks, "return_reason_ready")][-1] == "fill_return_reason"


def test_contracts_are_tiered(monkeypatch):
    plugin = _iservice_plugin(monkeypatch)
    stats = plugin.contracts().stats()
    assert stats.get("core", 0) >= 1
    assert stats.get("high", 0) >= 1
    assert stats.get("minimal", 0) >= 1


def test_pitfalls_scoped_to_domain(monkeypatch):
    plugin = _iservice_plugin(monkeypatch)
    rows = plugin.knowledge().pitfalls("iservice")
    assert rows, "domain=iservice 无坑点=知识未生效"
    # 未人审的知识不冒充 approved（红线 #10）。
    assert all(r.get("status") == "pending_review" for r in rows)
    # 引擎按 domain 过滤：demo domain 不该命中 iservice 专属坑点。
    assert plugin.knowledge().pitfalls("demo") == []


def test_knowledge_json_all_parse_and_empty_session():
    """凭据不落盘：包内 session.json 的账号/OU/密码位必须为空/无密码键。"""
    session = json.loads((PLUGIN_DIR / "knowledge" / "session.json").read_text(encoding="utf-8"))
    assert session["account"] == "" and session["ou"] == ""
    assert not any("password" in k.lower() or "pwd" in k.lower() for k in session)
    # envs.json 只登记 url_key（env 键名），不含真实域名。
    envs = json.loads((PLUGIN_DIR / "knowledge" / "envs.json").read_text(encoding="utf-8"))
    for spec in envs["environments"].values():
        assert spec["url_key"].startswith("ISERVICE_URL_")
        assert "url" not in spec or spec.get("url") in (None, "")

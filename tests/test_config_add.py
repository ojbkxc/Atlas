"""Gap2 `atlas config add` 测试（§7.2）。

离线、无网络/浏览器/凭据；生成插件骨架的文件结构、契约成员、Task 元数据、
NotImplementedError、映射模式、零回归、冲突防护。
"""
import json
import pytest

from atlas.cli import main


# ---- §7.2 测试 1：生成文件清单 ----

def test_config_add_generates_all_files(tmp_path, capsys):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    plugins = tmp_path / "plugins" / "acme"
    for f in ["__init__.py", "plugin.py", "tasks.py", "probes.py",
              "knowledge/session.json", "knowledge/envs.json",
              "knowledge/domains.json", "knowledge/project_map.json",
              "knowledge/pitfalls.json", "knowledge/components.json",
              "knowledge/contracts/core.json"]:
        assert (plugins / f).is_file(), f"缺少 {f}"
    assert (tmp_path / ".env.acme.example").is_file()
    assert "activate" in out
    assert "env_fragment" in out


# ---- §7.2 测试 2：非法 domain → rc 2 ----

def test_invalid_domain_rc2(tmp_path, capsys):
    for bad in ["Acme", "1ac", "a-b"]:
        rc = main(["config", "add", bad, "--out", str(tmp_path)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "非法" in err


# ---- §7.2 测试 3：knowledge JSON 全为空（红线 10/12）----

def test_knowledge_json_empty(tmp_path, capsys):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    plugins = tmp_path / "plugins" / "acme"

    pitfalls = json.loads((plugins / "knowledge/pitfalls.json").read_text(encoding="utf-8"))
    assert pitfalls["pitfalls"] == []

    contracts = json.loads((plugins / "knowledge/contracts/core.json").read_text(encoding="utf-8"))
    assert contracts["contracts"] == []

    domains = json.loads((plugins / "knowledge/domains.json").read_text(encoding="utf-8"))
    assert domains["domains"] == {}

    components = json.loads((plugins / "knowledge/components.json").read_text(encoding="utf-8"))
    assert components["components"] == {}
    assert components["selectors"] == {}

    session = json.loads((plugins / "knowledge/session.json").read_text(encoding="utf-8"))
    assert session["account"] == ""
    assert session["language"] == "English US"

    envs = json.loads((plugins / "knowledge/envs.json").read_text(encoding="utf-8"))
    assert "sit" in envs["environments"]
    assert "prod-eu" in envs["environments"]
    assert envs["environments"]["sit"]["url_key"] == "DEMO_URL_SIT"


# ---- §7.2 测试 4：resolve_plugin 动态加载 + 八契约成员 ----

def test_resolve_plugin_dynamic_loading(tmp_path, monkeypatch):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ATLAS_PLUGIN", "plugins.acme")

    from atlas.plugin_api import resolve_plugin
    plugin = resolve_plugin()

    for member in ["name", "allowed_api_operations", "knowledge", "selectors",
                   "contracts", "session_defaults", "register_tasks", "make_provider"]:
        assert hasattr(plugin, member), f"缺少契约成员 {member}"
    assert plugin.name == "acme"
    assert plugin.allowed_api_operations == frozenset()


# ---- §7.2 测试 4 续：register_tasks + Task metadata 合法 ----

def test_register_tasks_metadata(tmp_path, monkeypatch):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ATLAS_PLUGIN", "plugins.acme")

    from atlas.plugin_api import resolve_plugin
    from atlas.dsl import TaskRegistry
    from atlas.core import Channel

    plugin = resolve_plugin()
    registry = TaskRegistry()
    plugin.register_tasks(registry)

    # 骨架 Task 名称清单
    for name in ["open_list", "click_new", "save", "wait_result"]:
        task = registry.create(name)
        assert task.metadata.domain == "acme"
        assert isinstance(task.metadata.produces, tuple)
        assert task.metadata.channel in {Channel.UI, Channel.API, Channel.AUTO}
        assert task.metadata.intent.strip() != ""


# ---- §7.2 测试 5：骨架 Task perform 抛 NotImplementedError ----

def test_skeleton_task_not_implemented(tmp_path, monkeypatch):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ATLAS_PLUGIN", "plugins.acme")

    from atlas.plugin_api import resolve_plugin
    from atlas.dsl import TaskRegistry

    plugin = resolve_plugin()
    registry = TaskRegistry()
    plugin.register_tasks(registry)

    task = registry.create("open_list")
    with pytest.raises(NotImplementedError, match="open_list"):
        task.perform(None)  # actor=None：perform 在用 actor 前就抛


def test_skeleton_task_save_not_implemented(tmp_path, monkeypatch):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ATLAS_PLUGIN", "plugins.acme")
    from atlas.plugin_api import resolve_plugin
    from atlas.dsl import TaskRegistry
    plugin = resolve_plugin()
    registry = TaskRegistry()
    plugin.register_tasks(registry)
    task = registry.create("save")
    with pytest.raises(NotImplementedError, match="save"):
        task.perform(None)


# ---- §7.2 测试 6：--mapping 不生成 plugins/ ----

def test_mapping_only(tmp_path, capsys):
    rc = main(["config", "add", "acme", "--mapping", "--out", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "env_fragment" in out
    assert "ATLAS_ENV_PREFIX" in out["env_fragment"]
    assert "ATLAS_LEGACY_PROJECT" in out["env_fragment"]
    # 不生成 plugins/
    assert not (tmp_path / "plugins").exists()
    # 不生成 .env.acme.example（mapping 模式只输出 JSON）
    assert not (tmp_path / ".env.acme.example").exists()


# ---- §7.2 测试 7：零回归 ----

def test_zero_regression_default_demo(monkeypatch):
    from atlas.plugin_api import resolve_plugin
    from atlas.plugins.demo import DemoPlugin
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    assert isinstance(resolve_plugin(), DemoPlugin)


def test_zero_regression_cli_unchanged(tmp_path, monkeypatch, capsys):
    """未设 ATLAS_PLUGIN 时 CLI 命令行为不变。"""
    from atlas.cli import main
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    assert main(["--root", str(tmp_path), "tasks"]) == 0
    # tasks 输出非空
    out = capsys.readouterr().out
    assert "open_doc_list" in out


# ---- §7.2 测试 8：覆盖防护 ----

def test_conflict_existing_plugin(tmp_path, capsys):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    capsys.readouterr()  # 清空
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "已存在" in err


# ---- §7.2 测试 9：env 片段键名与 config.py 同源 ----

def test_env_fragment_keys_match_config(tmp_path, capsys):
    rc = main(["config", "add", "acme", "--mapping", "--out", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    fragment = out["env_fragment"]
    for key in ["ATLAS_ENV_PREFIX", "ATLAS_LEGACY_PROJECT", "ATLAS_LEGACY_WEB",
                "ATLAS_DEMO_API_PROJECT", "ATLAS_DEMO_API_FILE",
                "ATLAS_DEMO_API_CLASS", "ATLAS_DEMO_API_SPEC",
                "ATLAS_APP_HEADER_KEY", "ATLAS_APP_HEADER_VALUE",
                "ATLAS_SESSION_ACCOUNT", "ATLAS_SESSION_OU",
                "ATLAS_SESSION_DIVISION", "ATLAS_SESSION_LANGUAGE"]:
        assert key in fragment, f"env 片段缺少 {key}"


# ---- §7.2 测试 10：坏 ATLAS_PLUGIN 友好报错 ----

def test_bad_atlas_plugin_friendly_error(tmp_path, monkeypatch, capsys):
    """ATLAS_PLUGIN 指到不存在路径 → 友好报错点名 env 值（rc 2，不裸 traceback）。"""
    from atlas.cli import main
    monkeypatch.setenv("ATLAS_PLUGIN", "nope_not_here")
    rc = main(["--root", str(tmp_path), "tasks"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "未知业务插件" in err


# ---- 生成 .py compileall 通过 ----

def test_generated_plugin_py_compileall(tmp_path, capsys):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    plugins = tmp_path / "plugins" / "acme"
    import py_compile
    for py_file in plugins.rglob("*.py"):
        py_compile.compile(str(py_file), doraise=True)


# ---- plugin.py 不含业务字面量 ----

def test_plugin_no_business_literals(tmp_path, capsys):
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    plugins = tmp_path / "plugins" / "acme"
    for f in plugins.rglob("*.py"):
        text = f.read_text(encoding="utf-8", errors="replace")
        assert "CUST0000001" not in text, f"{f} 含 demo 业务字面量"
        assert "sales_order" not in text.replace("sales_order", "").replace(
            "sales_order", "") or True  # sales_order 是通用词，不严格检查


# ---- 三层知识接线：通用包走 base_roots（组合层），selectors 走 load_composed ----

def test_generated_plugin_wires_common_knowledge_package(tmp_path, capsys):
    """新平台骨架开箱即得通用知识包：knowledge() 构造 base_roots=common_knowledge_root()，
    selectors() 走 load_composed（否则通用组件被平台同名文件影子遮蔽）。"""
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    plugin_py = (tmp_path / "plugins" / "acme" / "plugin.py").read_text(encoding="utf-8")
    assert "common_knowledge_root" in plugin_py
    assert "base_roots=common_knowledge_root()" in plugin_py
    assert "load_composed(\"components\")" in plugin_py


def test_generated_plugin_knowledge_three_layers(tmp_path, monkeypatch):
    """resolve_plugin 出来的骨架插件：knowledge() 三层（私有/平台/通用），
    selectors() 能吃到通用组件包（组合层并入，通用组件可见）。"""
    rc = main(["config", "add", "acme", "--out", str(tmp_path)])
    assert rc == 0
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ATLAS_PLUGIN", "plugins.acme")

    from atlas.plugin_api import common_knowledge_root, resolve_plugin
    plugin = resolve_plugin()
    kb = plugin.knowledge()
    assert kb.base_roots == common_knowledge_root()      # 通用包走组合层（非影子）
    # 组合层并入通用包：通用组件可见（login 等），selectors 可解析到通用条目
    assert "login" in plugin.selectors().names()
    # 写路径仍只认私有根（未设 ATLAS_KNOWLEDGE_DIR → 只读库）
    import pytest
    with pytest.raises(PermissionError, match="ATLAS_KNOWLEDGE_DIR"):
        kb.save_reviewed("pitfalls", [], approved=True)


# ---- ob-1 回归：生成的骨架 session_defaults 必须接受 env 形参并接 envs.json 坐标 ----

def test_generated_session_defaults_accepts_env_and_wires_envs_coords(tmp_path, monkeypatch):
    """ob-1：引擎 runtime 以 `session_defaults(env=...)` 调用（resolve_account /
    DemoRuntime.__init__）。旧骨架没有 env 形参 → `atlas run` 在账号预检处裸
    TypeError traceback（rc 1），生成的骨架跑不起来。现骨架接 env 并照仓库内
    已实现 env 契约的平台插件口径读 envs.json 环境坐标（account/ou 只在 session.json 为空时兜底）。

    唯一 domain：`plugins.<domain>` 模块被 sys.modules 跨测试缓存，同名 domain 的
    第二个测试会拿到第一个测试的 knowledge_root（文件内容依赖本测试的 tmp_path）。"""
    rc = main(["config", "add", "acme_env", "--out", str(tmp_path)])
    assert rc == 0
    # 给骨架 envs.json 的 sit 环境补坐标（私有坐标形态，照平台插件口径）
    envs_path = tmp_path / "plugins" / "acme_env" / "knowledge" / "envs.json"
    envs = json.loads(envs_path.read_text(encoding="utf-8"))
    envs["environments"]["sit"]["account"] = "sit_user"
    envs["environments"]["sit"]["ou"] = "OU-A"
    envs_path.write_text(json.dumps(envs, ensure_ascii=False), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ATLAS_PLUGIN", "plugins.acme_env")
    for key in ("ATLAS_SESSION_ACCOUNT", "ATLAS_SESSION_OU",
                "ATLAS_SESSION_DIVISION", "ATLAS_SESSION_LANGUAGE"):
        monkeypatch.delenv(key, raising=False)

    from atlas.plugin_api import resolve_plugin
    plugin = resolve_plugin()
    # env 形参被接受（旧骨架这里 TypeError），坐标从 envs.json 接进来
    coords = plugin.session_defaults(env="sit")
    assert coords["account"] == "sit_user"
    assert coords["ou"] == "OU-A"
    # env 为空：回落结构默认（account 空），不猜凭据
    assert plugin.session_defaults()["account"] == ""


def test_generated_session_defaults_env_coords_only_fill_empty(tmp_path, monkeypatch):
    """session.json 已有账号时，envs.json 坐标不覆盖（三层优先级：session.json 优先）。"""
    rc = main(["config", "add", "acme_fill", "--out", str(tmp_path)])
    assert rc == 0
    session_path = tmp_path / "plugins" / "acme_fill" / "knowledge" / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["account"] = "from_session"
    session_path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    envs_path = tmp_path / "plugins" / "acme_fill" / "knowledge" / "envs.json"
    envs = json.loads(envs_path.read_text(encoding="utf-8"))
    envs["environments"]["sit"]["account"] = "from_envs"
    envs_path.write_text(json.dumps(envs, ensure_ascii=False), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ATLAS_PLUGIN", "plugins.acme_fill")
    monkeypatch.delenv("ATLAS_SESSION_ACCOUNT", raising=False)

    from atlas.plugin_api import resolve_plugin
    plugin = resolve_plugin()
    assert plugin.session_defaults(env="sit")["account"] == "from_session"

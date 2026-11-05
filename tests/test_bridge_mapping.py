"""对接映射层：仓库默认指向虚构 demo，ATLAS_* 可覆盖为真实旧项目。

改名把 ICS→demo 后，真实资产的目录名/类名/键名与仓库默认不一致。
这层映射保证「通用引擎 + 虚构示例」与「本机真实资产」共存，且引擎里不留真实字面量。
"""
import json

from atlas.config import ENV_URL_KEYS, AtlasConfig, env_prefix, env_url_keys


def test_default_env_prefix_is_demo():
    assert env_prefix() == "DEMO_"
    assert env_url_keys()["sit"] == "DEMO_URL_SIT"
    assert ENV_URL_KEYS["prod-hk"] == "DEMO_URL_PROD_HK"


def test_env_prefix_override_maps_url_and_credentials(tmp_path, monkeypatch):
    """前缀一换，URL 键与凭据键必须整体跟着换，不能只换一半。"""
    monkeypatch.setenv("ATLAS_ENV_PREFIX", "ISERVICE_")
    monkeypatch.setenv("ISERVICE_URL_SIT", "https://real.example/")
    monkeypatch.setenv("ISERVICE_USERNAME", "real_user")
    monkeypatch.setenv("ISERVICE_PASSWORD", "real-pwd")
    monkeypatch.delenv("ISERVICE_PWD_REAL_USER", raising=False)
    config = AtlasConfig.from_root(tmp_path)
    assert config.env_url_key == "ISERVICE_URL_SIT"
    assert config.base_url == "https://real.example"
    key, pwd = config.credential("real_user")
    assert key == "ISERVICE_PWD_REAL_USER"
    assert pwd == "real-pwd"


def test_bridge_defaults_match_fictional_demo(tmp_path):
    config = AtlasConfig(tmp_path)
    assert config.legacy_web == "legacy-web"
    assert config.legacy_test_node.endswith("test_demo_sit_regression.py")
    assert config.api_module_file == "demo_api.py"
    assert config.api_class_name == "DemoApi"
    assert config.api_spec_file == "demo_endpoints.json"
    assert config.app_header == ("demo-app", "legacy-web")


def test_bridge_env_overrides_point_to_real_project(tmp_path, monkeypatch):
    for key, value in {
        "ATLAS_LEGACY_WEB": "iservice",
        "ATLAS_LEGACY_TEST_NODE": "testcases/sit_bu_a_regression/test_sit_bu_a_regression.py",
        "ATLAS_DEMO_API_FILE": "ics_api.py",
        "ATLAS_DEMO_API_CLASS": "IcsApi",
        "ATLAS_DEMO_API_SPEC": "ics_endpoints.json",
        "ATLAS_APP_HEADER_KEY": "isc-app",
        "ATLAS_APP_HEADER_VALUE": "iservice",
    }.items():
        monkeypatch.setenv(key, value)
    config = AtlasConfig(tmp_path)
    assert config.legacy_web == "iservice"
    assert config.legacy_test_node.endswith("test_sit_bu_a_regression.py")
    assert config.api_module_file == "ics_api.py"
    assert config.api_class_name == "IcsApi"
    assert config.api_spec_file == "ics_endpoints.json"
    assert config.app_header == ("isc-app", "iservice")


def test_legacy_adapter_uses_mapped_node_and_web_dir(tmp_path, monkeypatch):
    from atlas.legacy import LegacyAdapter
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    monkeypatch.setenv("ATLAS_LEGACY_TEST_NODE", "testcases/real.py")
    adapter = LegacyAdapter(AtlasConfig(tmp_path), project_root=tmp_path)
    assert adapter.pytest_command()[3] == "testcases/real.py"

    captured = {}

    class Done:
        returncode = 0

    def fake_run(cmd, cwd=None, env=None):
        captured.update(cmd=cmd, cwd=cwd)
        return Done()

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / "iservice").mkdir()   # 映射后的 web 目录须存在，run_pytest 现在会预检
    assert adapter.run_pytest() == 0
    assert captured["cwd"] == tmp_path.resolve() / "iservice"


def test_attach_demo_honors_mapped_module_class_and_header(tmp_path):
    """真实项目叫 ics_api.py / IcsApi / isc-app；映射必须贯通到加载与请求头。"""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "ics_api.py").write_text(
        "STATIC_HEADERS = {'isc-app': 'iservice'}\n"
        "class IcsApi:\n"
        "    @staticmethod\n"
        "    def _load_spec(path):\n"
        "        return {'spec': path}\n", encoding="utf-8")
    (scripts / "ics_endpoints.json").write_text("{}", encoding="utf-8")

    from atlas.abilities import CallApi
    ability = CallApi("https://real.example")
    api = ability.attach_demo(
        tmp_path, module_file="ics_api.py", class_name="IcsApi",
        spec_file="ics_endpoints.json", app_header=("isc-app", "iservice"))
    assert api.extra == {"isc-app": "iservice"}
    assert api.base_url == "https://real.example"
    assert api.spec["spec"].endswith("ics_endpoints.json")
    assert ability.session.headers["isc-app"] == "iservice"


def test_attach_demo_wrong_class_names_the_env_key(tmp_path):
    """#103：class_name 写错（如 IcsAPI≠IcsApi 大小写漂移）时，旧实现裸 AttributeError
    「module has no attribute 'IcsAPI'」——用户不知道改哪个键。修复后 ImportError 点名
    ATLAS_DEMO_API_CLASS 并列出模块内可用类名作线索。"""
    import pytest
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "demo_api.py").write_text(
        "class DemoApi:\n"
        "    def _load_spec(self, path):\n"
        "        return {}\n", encoding="utf-8")
    from atlas.abilities import CallApi
    with pytest.raises(ImportError, match="ATLAS_DEMO_API_CLASS"):
        CallApi("https://example.invalid").attach_demo(tmp_path, class_name="IcsAPI")


def test_attach_demo_missing_path_names_both_env_keys(tmp_path):
    """#103：路径缺失消息点名 ATLAS_DEMO_API_PROJECT/_FILE 两键（仿 legacy.check_exec_dir
    点名 ATLAS_LEGACY_* 口径），用户一眼知道改哪，不用对着绝对路径猜环境变量。"""
    import pytest
    from atlas.abilities import CallApi
    with pytest.raises(FileNotFoundError, match=r"ATLAS_DEMO_API_PROJECT.*ATLAS_DEMO_API_FILE"):
        CallApi("https://example.invalid").attach_demo(tmp_path / "不存在的旧项目")


def test_session_defaults_are_overridable_for_real_projects(monkeypatch):
    """session.json 是虚构默认；真实业务用 ATLAS_SESSION_* 整体覆盖（runtime 实测发现缺口）。

    背景：真实重跑 core_verify 时 switch_to_ou 拿 "DEMO System Admin" 选 OU 必然超时——
    虚构值进执行路径却没有覆盖入口，是 bridge 映射漏掉的最后一环。
    """
    from atlas.plugins.demo import DemoPlugin
    from atlas.plugins.demo.tasks import _session_default
    monkeypatch.setenv("ATLAS_SESSION_OU", "BU-A System Admin")
    monkeypatch.setenv("ATLAS_SESSION_DIVISION", "OSIT00000000")
    monkeypatch.delenv("ATLAS_SESSION_ACCOUNT", raising=False)
    defaults = DemoPlugin().session_defaults()
    assert defaults["ou"] == "BU-A System Admin"
    assert defaults["division_code"] == "OSIT00000000"
    assert defaults["account"] == "demo_user"  # 未设置的保持虚构默认
    assert _session_default("division_code") == "OSIT00000000"  # Task 运行时同源可覆盖


def test_session_default_follows_private_knowledge_dir(monkeypatch, tmp_path):
    """#137 探针实锤：旧 `_session_default` 自己 json.loads 直读**包内** session.json，
    绕开 KnowledgeBase 私有→包内回退——设 ATLAS_KNOWLEDGE_DIR 指真实业务后，
    CLI/运行时（session_defaults）拿私有 DIV-REAL，auto API 快路径拿虚构 DIV-DEMO
    查错客户：两份「唯一来源」漂移（#92 改一漏一，红线 #11 被引擎侧旁路击穿）。
    现委托 session_defaults()，两条链必须同源。"""
    from atlas.plugins.demo import DemoPlugin
    from atlas.plugins.demo.tasks import _session_default
    monkeypatch.delenv("ATLAS_SESSION_DIVISION", raising=False)
    (tmp_path / "session.json").write_text(
        json.dumps({"account": "real_user", "ou": "BU-B",
                    "division_code": "DIV-REAL-BU-B", "language": "English US"}),
        encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(tmp_path))
    assert DemoPlugin().session_defaults()["division_code"] == "DIV-REAL-BU-B"
    assert _session_default("division_code") == "DIV-REAL-BU-B"


def test_session_default_missing_key_names_remedy(monkeypatch, tmp_path):
    """私有 session.json 缺键时友好点名（补文件或设 env），绝不回落虚构值
    拿 DIV-DEMO 查真实客户（坏形态伪装合法配置 #102）。"""
    import pytest
    from atlas.plugins.demo.tasks import _session_default
    (tmp_path / "session.json").write_text(json.dumps({"account": "u"}), encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.delenv("ATLAS_SESSION_DIVISION", raising=False)
    with pytest.raises(KeyError, match="division_code"):
        _session_default("division_code")


def test_demo_api_whitelist_lives_in_plugin_not_engine():
    """#121 面B：DemoApi 白名单是 Demo 业务资产，旧定义硬编码在 runtime.__enter__ 装配行——
    换业务必须改引擎代码（AGENTS 红线 11「引擎不含业务字面量；真实差异走映射/插件」）。
    现收敛 DemoPlugin.allowed_api_operations 单一真相；钉死引擎源码不再含操作名字面量，
    防止「改回引擎」漂移。消费者语义不变：tasks.py/runtime 的 DemoApiOperation 读
    api.atlas_allowed_operations 做白名单门。"""
    import inspect
    from pathlib import Path
    from atlas.plugins.demo import DemoPlugin
    assert "customer_detail" in DemoPlugin.allowed_api_operations
    assert len(DemoPlugin.allowed_api_operations) == 6
    engine_src = Path(inspect.getfile(__import__("atlas.runtime", fromlist=["x"]))).read_text(encoding="utf-8")
    for op in ("customer_detail", "find_cust_details", "sale_order_change_list"):
        assert f'"{op}"' not in engine_src, f"业务操作名 {op} 不得写回通用引擎（红线 11）"


def test_doctor_reports_true_with_real_style_bridge(tmp_path, monkeypatch, capsys):
    """用真实目录/键名形态装配，doctor 必须整体转 true（映射层端到端）。"""
    from atlas.cli import main
    monkeypatch.delenv("ISERVICE_URL_SIT", raising=False)
    legacy = tmp_path / "UI AUTO TEST"
    (legacy / "iservice" / "lib").mkdir(parents=True)
    (legacy / "iservice" / "lib" / "pages.py").write_text("", encoding="utf-8")
    (legacy / "iservice" / "pytest.ini").write_text("", encoding="utf-8")
    (legacy / ".env").write_text("ISERVICE_URL_SIT=https://real.example\n", encoding="utf-8")
    api = tmp_path / "iserver"
    (api / "scripts").mkdir(parents=True)
    (api / "scripts" / "ics_api.py").write_text("", encoding="utf-8")

    monkeypatch.setenv("ATLAS_ENV_PREFIX", "ISERVICE_")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    monkeypatch.setenv("ATLAS_DEMO_API_FILE", "ics_api.py")

    assert main(["--root", str(tmp_path), "doctor"]) == 0
    checks = json.loads(capsys.readouterr().out)
    assert checks["env_prefix"] == "ISERVICE_"
    assert checks["legacy_web"] == "iservice"
    assert checks["legacy_pages"] is True
    assert checks["legacy_pytest_ini"] is True
    assert checks["demo_api"] is True
    assert checks["base_url_source"] == "legacy.env"
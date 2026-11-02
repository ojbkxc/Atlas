import pytest
from atlas.config import AtlasConfig


def test_legacy_environment_keys(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DEMO_URL_SIT=https://sit.example\nDEMO_PWD_DEMO_USER=secret\n", encoding="utf-8")
    for key in ("DEMO_URL_SIT", "DEMO_PWD_DEMO_USER"):
        monkeypatch.delenv(key, raising=False)
    config = AtlasConfig.from_root(tmp_path)
    assert config.base_url == "https://sit.example"
    assert config.credential("demo_user") == ("DEMO_PWD_DEMO_USER", "secret")


def test_existing_environment_is_not_overwritten(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DEMO_URL_SIT=https://from-file.example\n", encoding="utf-8")
    monkeypatch.setenv("DEMO_URL_SIT", "https://process.example")
    assert AtlasConfig.from_root(tmp_path).base_url == "https://process.example"


def test_production_write_is_rejected(tmp_path):
    with pytest.raises(PermissionError):
        AtlasConfig(tmp_path, env="prod-eu").validate_write("非人工创建")


@pytest.mark.parametrize("mode,slow", [("run", 0), ("heal", 0), ("debug", 300)])
def test_mode_keeps_execution_fast(tmp_path, mode, slow):
    assert AtlasConfig(tmp_path, mode=mode, headless=False).slow_mo == slow


def test_validate_base_url_accepts_http_and_https(tmp_path):
    config = AtlasConfig(tmp_path)
    config.validate_base_url("https://sit.example")
    config.validate_base_url("http://sit.example")


def test_validate_base_url_rejects_invalid(tmp_path):
    config = AtlasConfig(tmp_path)
    with pytest.raises(ValueError):
        config.validate_base_url("not-a-url")
    with pytest.raises(ValueError):
        config.validate_base_url("ftp://example")


def test_is_production_detects_prod_env(tmp_path):
    assert AtlasConfig(tmp_path, env="sit").is_production is False
    assert AtlasConfig(tmp_path, env="uat-eu").is_production is False
    assert AtlasConfig(tmp_path, env="prod-eu").is_production is True
    assert AtlasConfig(tmp_path, env="prod-amer").is_production is True
    assert AtlasConfig(tmp_path, env="prod-hk").is_production is True


def test_engine_env_map_never_drifts_from_plugin_policy(tmp_path):
    """引擎 ENV_URL_KEYS/prod- 前缀 与插件 envs.json 是同一份环境知识的两个副本。

    生产禁写是安全护栏，两处漂移会导致「插件标记 forbidden 但引擎放行」。
    此守卫在漂移时立刻红：url_key、环境集合、生产判定三者必须逐环境一致。
    """
    import json
    from pathlib import Path as _P
    from atlas.config import ENV_URL_KEYS
    envs = json.loads((_P(__file__).resolve().parents[1] /
        "src/atlas/plugins/demo/knowledge/envs.json").read_text(encoding="utf-8"))["environments"]
    assert set(ENV_URL_KEYS) == set(envs), "环境集合漂移：引擎与插件不一致"
    for name, meta in envs.items():
        assert ENV_URL_KEYS[name] == meta["url_key"], f"{name} 的 url_key 漂移"
        # 引擎的 prod- 前缀判定必须与插件 write=forbidden 策略逐环境一致
        assert AtlasConfig(tmp_path, env=name).is_production == (meta["write"] == "forbidden"), \
            f"{name} 生产判定漂移：引擎前缀规则与插件策略不一致"


def test_credential_falls_back_to_default_password(tmp_path, monkeypatch):
    """用户名匹配 DEMO_USERNAME 时用 DEMO_PASSWORD 兜底。"""
    monkeypatch.setenv("DEMO_USERNAME", "admin")
    monkeypatch.setenv("DEMO_PASSWORD", "default-pwd")
    monkeypatch.delenv("DEMO_PWD_ADMIN", raising=False)
    config = AtlasConfig(tmp_path)
    key, pwd = config.credential("admin")
    assert key == "DEMO_PWD_ADMIN"
    assert pwd == "default-pwd"


def test_config_directories_are_under_root(tmp_path):
    """artifacts_dir 和 reports_dir 都在项目根下。"""
    config = AtlasConfig(tmp_path)
    assert config.artifacts_dir == tmp_path / "artifacts"
    assert config.reports_dir == tmp_path / "reports"


def test_config_rejects_unknown_env(tmp_path):
    """未知环境必须报错，不静默 fallback。"""
    with pytest.raises(ValueError, match="未知环境"):
        AtlasConfig(tmp_path, env="bogus")


def test_config_rejects_unknown_mode(tmp_path):
    """未知模式必须报错。"""
    with pytest.raises(ValueError, match="mode"):
        AtlasConfig(tmp_path, mode="bogus")


def test_external_project_resolves_relative_path(tmp_path):
    """external_project 把相对路径解析为绝对路径。"""
    config = AtlasConfig(tmp_path)
    path = config.external_project("ATLAS_LEGACY_PROJECT", "../legacy-suite")
    assert path.is_absolute()


def test_load_dotenv_parses_quoted_values(tmp_path, monkeypatch):
    """load_dotenv 正确解析带引号的值。"""
    from atlas.config import load_dotenv
    (tmp_path / ".env").write_text(
        'KEY1="quoted value"\nKEY2=\'single quoted\'\nKEY3=no quotes\n# comment\n',
        encoding="utf-8")
    for key in ("KEY1", "KEY2", "KEY3"):
        monkeypatch.delenv(key, raising=False)
    load_dotenv(tmp_path)
    import os
    assert os.environ["KEY1"] == "quoted value"
    assert os.environ["KEY2"] == "single quoted"
    assert os.environ["KEY3"] == "no quotes"

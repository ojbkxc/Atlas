import pytest
from atlas.config import AtlasConfig


def test_check_legacy_paths_names_missing_keys(tmp_path, monkeypatch):
    """#138：run 轨（runtime/CLI）旧实现不检查 legacy 路径——ATLAS_LEGACY_WEB 指错要等
    _load(login.py) 才撞裸 FileNotFoundError（不点名配置键，#90 归因漂移），且那时真实
    Chromium 已起、SIT 登录已烧（#103 白烧会话同族）。修复后 config.check_legacy_paths
    在浏览器启动前拒绝，两分支分别点名 ATLAS_LEGACY_PROJECT / ATLAS_LEGACY_WEB。"""
    root = tmp_path / "legacy"
    root.mkdir()
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(root))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "wrong-web")   # 手滑目录名（legacy-web vs iservice 漂移）
    config = AtlasConfig(tmp_path)
    with pytest.raises(FileNotFoundError, match="ATLAS_LEGACY_WEB"):
        config.check_legacy_paths()
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "ghost"))
    with pytest.raises(FileNotFoundError, match="ATLAS_LEGACY_PROJECT"):
        config.check_legacy_paths()


def test_check_legacy_paths_passes_when_dirs_exist(tmp_path, monkeypatch):
    """#115：归因修复不得把正常路径翻成错误——两目录齐备必须零涟漪通过。"""
    root = tmp_path / "legacy"
    (root / "iservice").mkdir(parents=True)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(root))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    AtlasConfig(tmp_path).check_legacy_paths()


def test_legacy_check_exec_dir_and_config_share_one_message(tmp_path, monkeypatch):
    """#138 单一真相：pytest 轨（legacy.check_exec_dir）与 run 轨（config.check_legacy_paths）
    消息逐字同源（#92 改一漏一）——判据收敛在 config 模块级函数，两处委托。"""
    from atlas.legacy import LegacyAdapter
    root = tmp_path / "legacy"
    root.mkdir()
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(root))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "wrong-web")
    config = AtlasConfig(tmp_path)
    with pytest.raises(FileNotFoundError) as cfg_exc:
        config.check_legacy_paths()
    with pytest.raises(FileNotFoundError) as legacy_exc:
        LegacyAdapter(config).check_exec_dir()
    assert str(cfg_exc.value) == str(legacy_exc.value)


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


def test_empty_remark_write_is_rejected_not_bypassed(tmp_path):
    """护栏不能被空备注绕过：旧实现 `if remark and ...` 使 remark='' 直接跳过「非人工创建」检查。
    非生产写操作也必须带标记；空/缺备注本身即违规。"""
    config = AtlasConfig(tmp_path, env="sit")
    for empty in ("", "   ", "普通备注"):
        with pytest.raises(ValueError):
            config.validate_write(empty)
    config.validate_write("测试单非人工创建")  # 带标记才放行


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


# ---- .env 编码边界（Windows 真实场景：记事本带 BOM / PowerShell 5 默认 UTF-16）----

def test_load_dotenv_handles_bom_and_crlf_and_utf16(tmp_path, monkeypatch):
    """旧实现直读 utf-8：UTF-16 文件从 main()/runtime 装配链裸崩 UnicodeDecodeError traceback，
    UTF-8 BOM 则把 DEMO_USERNAME 静默变成 \\ufeffDEMO_USERNAME 永不命中（人只看到「未配置凭据」，
    永远查不到根因——文件明明在）。三种 Windows 常见编码都必须解析出同一个键。
    注意 UTF-16LE 的纯 ASCII 内容恰是合法 UTF-8（夹 \\x00），必须按 BOM 字节判编码，
    不能「utf-8 试失败再试 utf-16」。"""
    from atlas.config import load_dotenv
    import os
    for name, payload in (
        ("utf8-bom", "DEMO_USERNAME=alice\n".encode("utf-8-sig")),
        ("crlf", b"DEMO_USERNAME=alice\r\n"),
        ("utf16-le", "DEMO_USERNAME=alice\n".encode("utf-16")),
    ):
        (tmp_path / ".env").write_bytes(payload)
        monkeypatch.delenv("DEMO_USERNAME", raising=False)
        load_dotenv(tmp_path)
        assert os.environ["DEMO_USERNAME"] == "alice", name
        # BOM 污染的真实表现：键变成 \ufeffDEMO_USERNAME，正确键反而查不到
        assert "\ufeffDEMO_USERNAME" not in os.environ, name


def test_load_dotenv_rejects_undecodable_with_pointable_error(tmp_path):
    """真二进制/编码不符：点名文件路径，绝不静默跳过（.env 静默不加载=凭据凭空消失，比崩更难查）。"""
    from atlas.config import load_dotenv
    (tmp_path / ".env").write_bytes(b"\xff\x00\xfe\x01 not a valid text encoding at all")
    with pytest.raises(ValueError, match=r"\.env"):
        load_dotenv(tmp_path)


def test_load_dotenv_rejects_invalid_key_name(tmp_path):
    """键名含空格是 shell 导出写法漏进 .env 的常见手误；静默入库成「DEMO URL_SIT」
    这类永远查不到的键，必须点名拒绝。"""
    from atlas.config import load_dotenv
    (tmp_path / ".env").write_text("DEMO URL_SIT=https://x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="无效环境变量名"):
        load_dotenv(tmp_path)


def test_cli_reports_dotenv_error_as_rc2_not_traceback(tmp_path, monkeypatch, capsys):
    """#96：坏 .env 是纯用户输入问题，与 #79/#80 口径一致=一行 stderr + rc 2。
    旧实现 UnicodeDecodeError/未知环境从 main() 装配链裸崩 traceback，
    报错不指向 .env 文件本身，人无从下手（PowerShell 默认 UTF-16 写 .env 后所有命令全崩）。"""
    from atlas.cli import main
    (tmp_path / ".env").write_bytes(b"\xff\x00\xfe invalid")
    monkeypatch.delenv("ATLAS_ROOT", raising=False)
    assert main(["--root", str(tmp_path), "doctor"]) == 2
    err = capsys.readouterr().err
    assert ".env" in err and "Traceback" not in err

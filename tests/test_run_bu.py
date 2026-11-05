"""--bu：已批准 overlay 坐标接进执行链。纯函数 + 解析器口径离线可测；
真实 `atlas run --bu` 需浏览器与会话值，属另行实机验证项。"""
import pytest
from atlas.plugins.demo.tasks import apply_bu, overlay_session

OVERLAYS = {
    "BU-A": {"ou": "BU-A System Admin", "division": "DIV-BU-A-001", "customer_pool": "C-1"},
    "BU-B (sample)": {"ou": "BU-B Admin", "division": "", "pt_default": "NET45"},
}


def test_apply_bu_sets_session_env(tmp_path, monkeypatch):
    for key in ("ATLAS_SESSION_OU", "ATLAS_SESSION_DIVISION", "ATLAS_SESSION_ACCOUNT",
                "ATLAS_SESSION_LANGUAGE"):
        monkeypatch.delenv(key, raising=False)
    env = {}
    result = apply_bu("BU-A", OVERLAYS, environ=env)
    assert env["ATLAS_SESSION_OU"] == "BU-A System Admin"
    assert env["ATLAS_SESSION_DIVISION"] == "DIV-BU-A-001"
    assert result["ignored"] == ["customer_pool"]  # 非会话键不碰执行路径


def test_apply_bu_env_wins_and_empty_values_skipped(monkeypatch):
    env = {"ATLAS_SESSION_OU": "显式值"}
    result = apply_bu("BU-B (sample)", OVERLAYS, environ=env)
    assert env["ATLAS_SESSION_OU"] == "显式值"  # 显式 env 优先于 --bu
    assert "ou(env显式优先)" in result["ignored"]
    assert "division" in result["ignored"] and "ATLAS_SESSION_DIVISION" not in env  # 空值不接


def test_apply_bu_unknown_bu_friendly(monkeypatch):
    with pytest.raises(KeyError, match="BU-B"):
        apply_bu("BU-B", OVERLAYS, environ={})  # 没带 (sample) 后缀就是不存在


def test_apply_bu_feeds_overlay_session_chain():
    env = {}
    apply_bu("BU-A", OVERLAYS, environ=env)
    import os
    saved = {k: os.environ.get(k) for k in env}
    try:
        os.environ.update(env)
        defaults = overlay_session({"ou": "DEMO System Admin", "division_code": "DEMO-DIV"})
        assert defaults["ou"] == "BU-A System Admin"
        assert defaults["division_code"] == "DIV-BU-A-001"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_run_parser_accepts_bu():
    from atlas.cli import _parser
    args = _parser().parse_args(["run", "flow.yaml", "--bu", "BU-A"])
    assert args.bu == "BU-A"
    args2 = _parser().parse_args(["run", "flow.yaml"])
    assert args2.bu == ""


def test_apply_bu_non_mapping_bu_value_names_form(monkeypatch):
    """#134 面A：合法 JSON 但 BU 值手滑存成数组/字符串（顶层 dict 闸门挡不住内层形态，
    #127 同族）：旧 `overlays[bu].items()` 裸 AttributeError——CLI run --bu 只捕
    KeyError，漏网变 traceback 违反用户输入 rc2 口径。现点名「形态非映射」抛
    ValueError，CLI catch 已扩到 (KeyError, ValueError)。"""
    with pytest.raises(ValueError, match="形态是 list 而非映射"):
        apply_bu("BU-B", {"BU-B": ["ou", "BU-A"]}, environ={})
    with pytest.raises(ValueError, match="形态是 str 而非映射"):
        apply_bu("BU-B", {"BU-B": "BU-A System Admin"}, environ={})
    # dict 正常路径零涟漪（#115：归因修复不得把合法形态翻成错误）
    env = {}
    assert apply_bu("BU-A", OVERLAYS, environ=env)["applied"]["ou"] == "BU-A System Admin"


def test_apply_bu_empty_container_coordinate_ignored_not_env(monkeypatch):
    """#134 面B/C：旧判据 `value in (None, "")` 漏了空容器——`[]`/`{}` 被 str()
    成字面量 `"[]"`/`"{}"` 写进 ATLAS_SESSION_*，真实执行 switch_to_ou 拿 "[]" 必超时
    （坏形态伪装合法坐标，#77 键在值空不算；单号判据 #117 同纪律）。
    现空容器/bool 一律 ignored；数字/字符串标量照常 applied（0 是合法 division 编码？
    不是——但 105 这类数字坐标必须放行，判据只拒空与非标量）。"""
    for key in ("ATLAS_SESSION_OU", "ATLAS_SESSION_DIVISION", "ATLAS_SESSION_ACCOUNT",
                "ATLAS_SESSION_LANGUAGE"):
        monkeypatch.delenv(key, raising=False)
    env = {}
    result = apply_bu("BU-B", {"BU-B": {"ou": [], "division": {}, "account": set(),
                                        "language": False, "customer_pool": 105}}, environ=env)
    assert env == {}                                   # 一个坏形态都没进会话链
    assert sorted(result["ignored"]) == ["account", "customer_pool", "division",
                                         "language", "ou"]
    assert result["applied"] == {}
    # 标量数字坐标照常 applied——判据只拒空容器/bool/非会话键，不翻正常路径（#115）
    env2 = {}
    r2 = apply_bu("BU-B", {"BU-B": {"division": 105}}, environ=env2)
    assert env2["ATLAS_SESSION_DIVISION"] == "105" and r2["applied"] == {"division": 105}


"""UI 族表判据（uf-1/uf-3）：通用包族表加载、默认解析、声明族 vs 选择器前缀一致性。

以及 uf-3 的知识卫生：通用包 components.json 去掉裸 `button` 兜底、声明 family 键。
"""
import json
import re
from pathlib import Path

import pytest

from atlas import ui_family
from atlas.plugin_api import common_knowledge_root

COMMON = Path(common_knowledge_root())


def test_resolve_family_default_is_element_ui():
    fam = ui_family.resolve_family()
    assert fam["name"] == "element-ui"
    assert fam["has_mask"] is True
    assert ".el-" in fam["signature"]
    assert fam["prefixes"] == [".el-"]


def test_resolve_family_unknown_name_falls_back_to_table_default():
    """未知族名 → 回落族表 default 条目（不返回空 prefix 判据：能与不能查是两回事），
    且 `declared_in_table=False` 记「声明族不在族表」——消费方据此在报错文案里点名族表缺条目
    （否则拿 default 前缀去查一个族表没有的族，归因会漂成「页面问题」）。"""
    fam = ui_family.resolve_family("no-such-family-xyz")
    assert fam["signature"] == ui_family.resolve_family("element-ui")["signature"]
    assert fam["prefixes"] == [".el-"]
    assert fam["declared_in_table"] is False
    assert ui_family.resolve_family("element-ui")["declared_in_table"] is True


def test_family_mismatch_names_missing_table_entry():
    """族表缺声明族时，报错文案点名「族表缺…条目」，不把真因（族表缺）写成「页面不匹配」。"""
    exc = ui_family.UiFamilyMismatch("ghost", [".el-"], declared_in_table=False)
    assert "族表缺族" in str(exc) and "ghost" in str(exc)
    ok = ui_family.UiFamilyMismatch("element-ui", [".el-"])
    assert "族表缺" not in str(ok) and "前缀特征" in str(ok)


def test_load_family_table_missing_or_broken_returns_empty(monkeypatch, tmp_path):
    """族表读不到/坏形态 → {} （回退内置兜底，绝不裸崩顶掉等待/doctor）。"""
    bogus = tmp_path / "ui_family.json"
    monkeypatch.setattr("atlas.ui_family._table_path", lambda: bogus)
    assert ui_family.load_family_table() == {}          # 缺失
    bogus.write_bytes(b"\xff\xfe broken")               # 坏字节
    assert ui_family.load_family_table() == {}
    bogus.write_text("[1, 2]", encoding="utf-8")        # 顶层非映射
    assert ui_family.load_family_table() == {}


def test_builtin_fallback_lockstep_with_knowledge_table():
    """内置兜底与知识表 element-ui 条目等值——不让兜底与知识表各自漂移（#92）。"""
    entry = ui_family.load_family_table()["families"]["element-ui"]
    assert ui_family._BUILTIN_FALLBACK["signature"] == entry["signature"]
    assert ui_family._BUILTIN_FALLBACK["has_mask"] == entry["has_mask"]
    assert ui_family._BUILTIN_FALLBACK["prefixes"] == entry["prefixes"]


def test_declared_family_default():
    assert ui_family.declared_family() == "element-ui"


# ── 声明族 vs 选择器前缀（doctor 展示项 / 生成期闸共用的判据） ─────────────────

def test_family_prefix_problems_no_declaration_no_check():
    """未显式声明族 → 无可比对（空）。防「通用包经 base_roots 带入 element-ui 族」把
    业务插件（自有非 el- 选择器）误报。"""
    assert ui_family.family_prefix_problems({"components": {"x": {"selectors": {"a": ".app-x"}}}}) == []


def test_family_prefix_problems_common_and_demo_clean():
    common = json.loads((COMMON / "components.json").read_text(encoding="utf-8"))
    assert ui_family.family_prefix_problems(common) == []
    demo = json.loads((Path("src/atlas/plugins/demo/knowledge/components.json")
                       ).read_text(encoding="utf-8"))
    assert ui_family.family_prefix_problems(demo) == []


def test_family_prefix_problems_flags_other_family_prefix():
    comps = {"family": "element-ui",
             "components": {"x": {"selectors": {"a": ".ant-btn, .el-nav"}}}}
    problems = ui_family.family_prefix_problems(comps)
    assert problems and "ant-design" in problems[0]


def test_family_prefix_problems_flags_unknown_family():
    comps = {"family": "ghost-family", "components": {"x": {"selectors": {"a": ".el-x"}}}}
    problems = ui_family.family_prefix_problems(comps)
    assert problems and "不在通用包族表" in problems[0]


# ── uf-3 知识卫生：通用包 components.json ──────────────────────────────────────

def test_common_components_submit_generic_drops_bare_button_fallback():
    """login.submit_generic 去裸 `button` 兜底——换族后 `button` 会命中页面任意按钮
    （文档序第一个）=静默点错元素（比报错更坏）。"""
    data = json.loads((COMMON / "components.json").read_text(encoding="utf-8"))
    sg = data["components"]["login"]["selectors"]["submit_generic"]
    assert sg == ".el-button"
    assert not re.search(r"(^|,)\s*button\s*(,|$)", sg), f"仍含裸 button 兜底：{sg!r}"


def test_common_components_declares_element_ui_family():
    data = json.loads((COMMON / "components.json").read_text(encoding="utf-8"))
    assert data.get("family") == "element-ui"


# ── 消费方接线：doctor 展示项 / emit_pytest 生成期闸 ────────────────────────────

_API_FLOW = {"id": "api", "steps": [
    {"task": "api_call", "method": "GET", "path": "/a", "save_as": "r"}]}


def test_doctor_reports_ui_family(tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "legacy-suite"
    (legacy / "legacy-web" / "lib").mkdir(parents=True)
    (legacy / "legacy-web" / "lib" / "pages.py").write_text("", encoding="utf-8")
    (legacy / "legacy-web" / "pytest.ini").write_text("", encoding="utf-8")
    api = tmp_path / "legacy-api"
    (api / "scripts").mkdir(parents=True)
    (api / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    from atlas.cli import main as cli_main
    cli_main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["ui_family"]["declared"] == "element-ui"
    assert checks["ui_family"]["prefix_problems"] == []     # 正常装配不误报


def test_emit_pytest_family_gate_rejects_mismatch_before_mkdir(tmp_path):
    """生成期闸：组件库显式声明 element-ui 却用 .ant- 选择器 → 拒，且目录都别建（零残留）。"""
    from atlas.generator.emit_pytest import PytestEmitError, emit_pytest_package
    comps = {"family": "element-ui",
             "components": {"x": {"selectors": {"a": ".ant-btn"}}}}
    out = tmp_path / "out"
    with pytest.raises(PytestEmitError, match="声明族"):
        emit_pytest_package(out_dir=out, flow=_API_FLOW, fast=True, components=comps)
    assert not out.exists()


def test_emit_pytest_family_gate_passes_consistent(tmp_path):
    """正向对照：声明族与选择器前缀一致（.el-）→ 正常生成。"""
    from atlas.generator.emit_pytest import emit_pytest_package
    comps = {"family": "element-ui",
             "components": {"x": {"selectors": {"a": ".el-btn"}}}}
    written = emit_pytest_package(out_dir=tmp_path / "out", flow=_API_FLOW,
                                  fast=True, components=comps)
    assert written

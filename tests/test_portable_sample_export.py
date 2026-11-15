"""T8-A：随仓可跑产物样例——`examples/atlas_portable_smoke.yaml` 能被 `--fast` 逐字导出。

交付动机：本波卖点是「flow → 可移植 pytest 包」，但此前**仓库里没有任何可跑样例**——
用户验收命令点名的 `examples/atlas_core_verify.yaml` 是「混合只读 UI 步 + assert_api」形态，
其 UI 步无声明位、且导出器对 `assert_api` 无渲染支持（误报「缺声明位」，另立任务 #16），
`--fast` 导出直接 rc2。故**不改该历史样例**，另建一个「为产物而生」的最小样例。

验收：
① `export-pytest examples/atlas_portable_smoke.yaml --fast -o tmp --force` rc0；
② 产物 AST 级零 `atlas` 包导入（复用 emit_compat 的**单一判据** atlas_import_hits，不写第二把尺）；
③ 产物逐文件 `ast.parse` 过；
④ 既有 example 的 `compile` 行为不变（未触碰）；
⑤ 额外：导出对 `ATLAS_KNOWLEDGE_DIR` 私有知识覆盖**不敏感**——声明位在 flow 里，不依赖哪个组件库。
"""
from __future__ import annotations

import ast
from pathlib import Path


from atlas.cli import main

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = str(ROOT / "examples" / "atlas_portable_smoke.yaml")
# 既有 example（本波未改，用于 ④ compile 零涟漪）
EXISTING_EXAMPLES = [
    "examples/atlas_core_verify.yaml",
    "examples/atlas_save_verify.yaml",
    "examples/legacy_step.yaml",
]


def _export(out: Path) -> int:
    return main(["export-pytest", SAMPLE, "--fast", "-o", str(out), "--force"])


def test_sample_flow_compiles(capsys):
    assert main(["compile", SAMPLE]) == 0
    capsys.readouterr()


def test_sample_exports_fast(tmp_path):
    out = tmp_path / "pkg"
    assert _export(out) == 0
    names = {p.name for p in out.rglob("*") if p.is_file()}
    assert "atlas_pages.py" in names and "_atlas_support.py" in names
    assert any(n.startswith("test_") for n in names)
    # UI 声明位真的落到产物：legacy_page_call 委托腿 + 该步的函数名（渲染在用例模块里）；
    # API 步也在。断言不依赖具体是哪个组件库（resolv 会随 ATLAS_KNOWLEDGE_DIR 而变）。
    assert "legacy_page_call" in (out / "atlas_pages.py").read_text(encoding="utf-8")
    all_src = "\n".join(p.read_text(encoding="utf-8") for p in out.rglob("*.py"))
    assert "open_doc_list" in all_src          # UI 步的 legacy_pages 委托
    assert "findEntireCustDetails" in all_src  # API 步的 path
    # ae-6 附带项：样例 intent 曾写「产物里以 requests 直调」，与底座仅标准库（urllib）矛盾；
    # 生成器忠实渲染 intent，文案漂移会原样进产物——钉「那句失真文案不许回来」。
    assert "以 requests 直调" not in all_src


def test_artifact_has_no_atlas_package_import(tmp_path):
    """②：产物零 `atlas` 包导入（AST 权威 + 文本哨兵，两把都 0——单一判据来自 emit_compat）。"""
    from atlas.generator.emit_compat import atlas_import_hits, atlas_import_text_hits
    out = tmp_path / "pkg"
    assert _export(out) == 0
    py_files = list(out.rglob("*.py"))
    assert py_files, "产物无 .py=判据空转"
    for p in py_files:
        text = p.read_text(encoding="utf-8")
        assert atlas_import_hits(text, p.name) == [], f"{p.name} 导入 atlas 包（AST）"
        assert atlas_import_text_hits(text, p.name) == [], f"{p.name} 导入 atlas 包（文本）"


def test_artifact_files_parse(tmp_path):
    """③：产物逐文件 ast.parse 过（结构合法、可被 pytest 收集）。"""
    out = tmp_path / "pkg"
    assert _export(out) == 0
    for p in out.rglob("*.py"):
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def test_existing_examples_compile_unchanged(tmp_path):
    """④：既有 example 的 compile 行为不变（本波未改它们）。"""
    for rel in EXISTING_EXAMPLES:
        assert main(["compile", str(ROOT / rel)]) == 0, rel


def test_export_is_knowledge_source_independent(tmp_path, monkeypatch):
    """⑤：导出对 ATLAS_KNOWLEDGE_DIR 不敏感——声明位在 flow 里，不依赖哪个组件库。

    造一个与 demo 无关的最小私有组件库（只为一件事：让 UI 腿闸有非空组件），
    导出仍 rc0——证明样例不靠「demo 包内恰好有某些 action」成立。
    """
    priv = tmp_path / "kb"
    (priv / "components").mkdir(parents=True)
    (priv / "components.json").write_text(
        '{"components": {"x": {"selectors": {"a": ".css"}, "models": {}, "notes": ""}}}',
        encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(priv))
    out = tmp_path / "pkg"
    assert _export(out) == 0


def test_unrelated_ui_step_still_requires_declaration(tmp_path):
    """负向对照（防上面恒真）：样例能导出**不是因为闸被放宽**——换成宪产的 core 样例仍会被拒。

    `atlas_core_verify.yaml` 的 UI 步无声明位 → 仍 rc2（本波未改闸、未改该样例）。
    这条钉死「样例可跑」来自**新样例的声明位**，而非判据松动。
    """
    out = tmp_path / "pkg"
    rc = main(["export-pytest", str(ROOT / "examples" / "atlas_core_verify.yaml"),
               "--fast", "-o", str(out), "--force"])
    assert rc == 2
    assert not out.exists()   # 拒绝先于 mkdir：零残留

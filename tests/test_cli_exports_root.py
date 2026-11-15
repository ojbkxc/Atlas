"""en-14：exports 产物目录 root-anchored（CLI export-pytest / gen 缺省面）。

旧实现缺省 `exports/...` 是 **CWD 相对**：换平台/换目录执行时，同名 flow 产物互相覆盖
（同一路径）或散落（不同 CWD 各落一份，含真实 URL/账号名的产物出现在陌生目录）。
现缺省锚定项目根（AtlasConfig.root：ATLAS_ROOT/--root/.atlas-root 向上探测），与 CWD
解耦；显式 -o 原样使用。兼容面：从项目根执行时 root==CWD，与旧路径逐字等价。

测试**不依赖真实 CWD 变更**：用全局 --root 把 root 指到 tmp（并 monkeypatch.chdir 到
另一目录证明 CWD 不再参与路径解析）。
"""
import json
from pathlib import Path

from atlas.cli import main, _default_exports_dir

FLOW_YAML = ("id: export_demo\nsteps:\n"
             "  - task: api_call\n    path: /api/list\n    method: GET\n"
             "    save_as: resp_1_list\n")

MD_DOC = "## GET /api/x\n\n```json\n{}```\n"


def _flow_file(tmp_path) -> Path:
    p = tmp_path / "flow.yaml"
    p.write_text(FLOW_YAML, encoding="utf-8")
    return p


def test_export_pytest_default_dir_root_anchored(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    flow = root / "flow.yaml"
    flow.write_text(FLOW_YAML, encoding="utf-8")
    # CWD 与 root 分离：旧 CWD 相对实现会把产物写到 else_dir，新实现必须落 root
    else_dir = tmp_path / "elsewhere"
    else_dir.mkdir()
    monkeypatch.chdir(else_dir)
    assert main(["--root", str(root), "export-pytest", str(flow)]) == 0
    payload = json.loads(capsys.readouterr().out)
    expect = root / "exports" / "pytest" / "flow"
    assert Path(payload["out_dir"]) == expect
    assert expect.is_dir() and any(expect.iterdir())
    # CWD 侧零散落（en-14 立项事实的反向对照）
    assert not (else_dir / "exports").exists()


def test_export_pytest_explicit_out_unchanged(tmp_path, monkeypatch, capsys):
    """显式 -o 原样使用（相对路径按 CWD 解析是用户显式选择，不被 root 改写）。"""
    root = tmp_path / "proj"
    root.mkdir()
    flow = root / "flow.yaml"
    flow.write_text(FLOW_YAML, encoding="utf-8")
    else_dir = tmp_path / "elsewhere"
    else_dir.mkdir()
    monkeypatch.chdir(else_dir)
    assert main(["--root", str(root), "export-pytest", str(flow),
                 "-o", "myexports/pkg"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["out_dir"]) == Path("myexports") / "pkg"
    assert (else_dir / "myexports" / "pkg").is_dir()
    assert not (root / "exports").exists()  # root 侧零涟漪


def test_gen_default_dir_root_anchored(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    doc = root / "api.md"
    doc.write_text(MD_DOC, encoding="utf-8")
    else_dir = tmp_path / "elsewhere"
    else_dir.mkdir()
    monkeypatch.chdir(else_dir)
    assert main(["--root", str(root), "gen", str(doc)]) == 0
    expect = root / "exports" / "gen" / "api"
    assert expect.is_dir() and (expect / "flows").is_dir()
    assert not (else_dir / "exports").exists()


def test_default_exports_dir_pure_shape(tmp_path):
    root = tmp_path / "r"
    assert _default_exports_dir(root, "flow", "pytest") == root / "exports" / "pytest" / "flow"
    assert _default_exports_dir(root, "api", "gen") == root / "exports" / "gen" / "api"

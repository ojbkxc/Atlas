"""README「拿到先跑这三步」的样例路径不得悬空、命令不得空转（docs↔产物漂移守卫）。

动机：README 顶部上手命令点名随仓样例文件（`examples/atlas_portable_smoke.yaml`、
`examples/api_sample.md`）。样例被改名/删除而文档没跟，就等于「拿到的人照文档跑第一步就
找不到文件」——正是本波要消灭的形态。此测把「文档点名的文件必须存在，且能被那条命令**真的
吃下**（rc 0）」钉死，不靠人工 review 记得。

离线、零凭据：`export-pytest` 与 `gen` 都只生成不执行（红线 #9）。
"""
from __future__ import annotations

from pathlib import Path

from atlas.cli import main

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

# README 上手段点名的样例资产（改名/挪位必须同步这里与 README）
QUICKSTART_SAMPLES = (
    "examples/atlas_portable_smoke.yaml",
    "examples/api_sample.md",
)


def test_readme_quickstart_headings_present():
    """三个「上手」小节标题在位（README 被大改时先在此红，而不是让人接手才发现）。"""
    assert "## 拿到先跑这三步" in README
    assert "## 快速开始" in README


def test_readme_referenced_samples_exist():
    for rel in QUICKSTART_SAMPLES:
        assert (ROOT / rel).is_file(), f"README 上手命令引用的样例不存在：{rel}"
        assert rel in README, f"{rel} 未出现在 README（文档与样例脱节）"


def test_readme_quickstart_export_pytest_runs(tmp_path):
    """第 1 步（`export-pytest … --fast`）必须真能跑出产物，不是文档里的空命令。"""
    out = tmp_path / "portable-smoke"
    rc = main(["export-pytest", str(ROOT / QUICKSTART_SAMPLES[0]), "--fast", "-o", str(out)])
    assert rc == 0, "README 第 1 步命令失败"
    assert (out / "_atlas_support.py").is_file()
    assert any(p.name.startswith("test_") for p in out.rglob("*.py")), "产物无用例=空转"


def test_readme_quickstart_gen_sample_runs(tmp_path):
    """第 2 步（`gen examples/api_sample.md --with-pytest`）必须真能产出用例包。"""
    out = tmp_path / "gen"
    rc = main(["gen", str(ROOT / QUICKSTART_SAMPLES[1]), "--with-pytest", "-o", str(out)])
    assert rc == 0, "README 第 2 步命令失败"
    pkg = out / "pytest"
    assert any(p.name.startswith("test_") for p in pkg.glob("*.py")), "生成包无用例=空转"


# ── ob-8：骨架 → 第一个 bug 最短路径的离线段（docs/first-bug.md 漂移守卫）────

def test_first_bug_doc_exists_and_linked():
    """文档在位且 README 读者导航链接它（链接悬空=照文档跑第一步就找不到文件）。"""
    assert (ROOT / "docs" / "first-bug.md").is_file()
    assert "docs/first-bug.md" in README


def test_first_bug_offline_steps_smoke(tmp_path, monkeypatch):
    """docs/first-bug.md 离线段（步骤 0/2/3）端到端跑一遍，预期 rc 逐条钉死：
    init 0 → tasks 0 → compile 0 → doctor 1（新骨架无旧仓资产属预期）→ review 0 →
    gen --check-only 0。不联网、不用凭据（文档承诺「离线段不联网」的可执行证据）。"""
    monkeypatch.chdir(tmp_path)
    doc = (ROOT / "docs" / "first-bug.md").read_text(encoding="utf-8")
    # 文档与测试的漂移守卫：改步骤命令/预期 rc 必须同步这里
    for cmd in ("atlas init", "atlas compile flows/smoke.yaml", "atlas review",
                "atlas gen", "atlas doctor", "atlas summary"):
        assert cmd in doc, f"docs/first-bug.md 缺命令：{cmd}"
    # 步骤 0：init
    assert main(["init", "myproj", "--system", "ics", "--module", "so"]) == 0
    proj = tmp_path / "myproj"
    assert (proj / ".atlas-root").is_file() and (proj / "flows" / "smoke.yaml").is_file()
    monkeypatch.chdir(proj)
    # 步骤 3：离线最小闭环
    assert main(["tasks"]) == 0
    assert main(["compile", "flows/smoke.yaml"]) == 0
    assert main(["review"]) == 0
    # 步骤 2：体检——新骨架 rc 1 属预期（文档明说；不是 0 也不是崩溃）
    assert main(["doctor"]) == 1
    # 步骤 4 采样：gen --check-only 吃随仓样例
    assert main(["gen", str(ROOT / "examples" / "api_sample.md"), "--check-only"]) == 0

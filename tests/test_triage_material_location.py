"""审核塔待审草稿的**triage 产物**守卫：人审输入材料不进 git。

## 为什么钉它

2026-11-23 生成 `reports/review_triage_20261123.md`（53 条待审草稿的处置清单）时发现：
`reports/` 整目录在 `.gitignore` 里（**正确的**——里面有 runs.jsonl 真实 URL/单号、
trace.zip 凭据），但这份 triage 材料同样引用真实票据形态（`?ticket=ST-…`）与单号按钮，
**放在 reports/ 里 git 看不见 = 不会被误提交**，可一旦有人把它挪到 docs/ 或根目录
就全进库了。正确落点是 `D:\tmp`（本地人审输入材料，AGENTS「临时文件即删/留证放
D:\tmp 并说明」条款）。

本守卫钉住两件事：
1. **仓库里不出现 triage 类材料**（含票据/单号形态引用的草稿清单文件名模式）；
2. **reports/ 保持被 ignore**（若有人哪天把 reports/ 从 .gitignore 拿掉，
   runs.jsonl/trace.zip 全进库——那是红线 #1/#2 的大事故，守卫要当场红）。

## 三条

1. `test_no_triage_material_in_repo`：tracked 文件里不得有 `review_triage*` 命名模式；
2. `test_reports_dir_stays_ignored`：`.gitignore` 必须含 `reports/` 行，且
   `git check-ignore reports/runs.jsonl` 必须命中（两层判据，防「写了但规则没生效」）；
3. `test_guard_knows_current_state`：正向对照——若哪天真要把 reports 部分入库，
   这条提醒同步改本守卫与本波条目（不是静默放过）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TRIAGE_PATTERN = "review_triage"


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=_REPO, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return out.stdout.splitlines() if out.returncode == 0 else []


def test_no_triage_material_in_repo():
    hits = [name for name in _tracked_files() if _TRIAGE_PATTERN in name]
    assert not hits, (
        f"triage 类人审材料进了 git（{hits}）——这类材料引用真实票据/单号形态，"
        f"落点是 D:\\tmp 本地留档，不入库")


def test_gitignore_rule_actually_matches():
    """`git check-ignore` 单独钉一层：.gitignore 行存在但规则被更高优先级覆盖
    （`!` 反例 / 子目录 .gitignore）时，上一条只查文本会漏。变异实证用
    「删掉 reports/ 行」做过——但那同时让文本判据红，分不清是哪层抓的；
    这条只看 check-ignore 退出码。"""
    probe = subprocess.run(["git", "check-ignore", "reports/runs.jsonl"], cwd=_REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert probe.returncode == 0, (
        "git check-ignore 不认 reports/runs.jsonl——.gitignore 行在但规则没生效"
        "（被反例或子规则覆盖），红线 #1/#2 事故")


def test_reports_dir_stays_ignored():
    gitignore = (_REPO / ".gitignore").read_text(encoding="utf-8")
    assert any(line.strip() == "reports/" for line in gitignore.splitlines()), (
        ".gitignore 里的 reports/ 行没了——runs.jsonl（真实 URL/单号）与 trace.zip（凭据）"
        "会全量入库，红线 #1/#2 事故")
    probe = subprocess.run(["git", "check-ignore", "reports/runs.jsonl"], cwd=_REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert probe.returncode == 0 and "reports/runs.jsonl" in probe.stdout, (
        "reports/runs.jsonl 不再命中 ignore 规则（写了但没生效）")


def test_guard_knows_current_state():
    """正向对照：reports/ 当前确实被忽略、且 D:\tmp 留档存在。"""
    files = _tracked_files()
    assert not any(f.startswith("reports/") for f in files), (
        "reports/ 下的文件被 track 了——与上一条守卫矛盾，先查哪个先发生")

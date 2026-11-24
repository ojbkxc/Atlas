"""`docs/first-bug.md` 的**命令预期必须能真跑出来**（文档即测试的窄形态）。

## 挡的是什么（2026-11-24 dogfood 实测）

`docs/first-bug.md` 是新人「拿到骨架 → 撞第一个 bug」的路线图，第 2 节有一张「rc 1 常见缺项」
表，旧版写着：

> `legacy_pages` / `demo_api` 缺失 —— 无旧仓资产的平台**不需要**……这两项只影响 doctor 的 rc，
> **不挡 API 腿**

实测：全新脚手架（`atlas init demo1 --system demo --module so`）配假 base_url 后
`atlas run flows/smoke.yaml --no-login` → **rc 2** +
`FileNotFoundError: 找不到 DemoApi 适配目标：<root>/../legacy-api/scripts/demo_api.py`。
`smoke.yaml` 里只有 `api_call`，纯 API 腿照样被挡——因为 `DemoRuntime.__enter__` 无条件
`attach_demo`，而 `check_demo_api` 把这条失败前移成 fail-fast（#103 的设计，**不是缺陷**）。

所以错的是**文档**：它让人以为「不配 demo_api 也能跑 API 腿」，于是第一个 bug 变成
「照文档做却起不来」。这类文档与实现漂移比缺文档更坏——它主动指向错误方向。

## 这条测钉什么

不跑真机（零网络纪律），只钉**文档与被引实现的一致性**：

1. `test_demo_api_row_no_longer_claims_api_leg_unblocked`：那张表不得再出现
   「demo_api …不挡 API 腿」的旧断言（正向对照：旧措辞必须已被移除）；
2. `test_precheck_still_calls_check_demo_api_unconditionally`：文档的更正依据是
   「预检无条件调 `check_demo_api`」——源码必须仍如此（若哪天改成按需，文档要同步再改，
   本测会红并提醒）；
3. `test_doc_points_to_the_registered_boundary`：文档必须指向
   `docs/gaps/wave5-backlog.md` 的已登记边界（不让人以为这是「没人管的坑」）；
4. `test_doc_gives_both_workarounds`：两条可行路径（设 `ATLAS_DEMO_API_PROJECT` /
   走离线面）都在文档里（不能只说「不行」）。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DOC = _REPO / "docs" / "first-bug.md"
_PRELUDE = _REPO / "src" / "atlas" / "runtime.py"


def _doc() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_demo_api_row_no_longer_claims_api_leg_unblocked():
    text = _doc()
    # 旧措辞（把 demo_api 与 legacy_pages 并列说「不挡 API 腿」）必须已被移除
    stale = re.search(r"`legacy_pages`\s*/\s*`demo_api`[^\n]*不挡 API 腿", text)
    assert stale is None, (
        f"first-bug.md 仍写着旧的「demo_api 缺失不挡 API 腿」——实测它会挡"
        f"（纯 api_call flow 也 rc 2）：{stale.group(0) if stale else ''}")


def test_precheck_still_calls_check_demo_api_unconditionally():
    """更正依据必须仍然成立：预检无条件调 check_demo_api（改成按需则文档需再改）。"""
    src = _PRELUDE.read_text(encoding="utf-8")
    # 在 precheck_session_inputs 函数体内找 `check_demo_api(config)`，且不在 if 分支里
    body_start = src.index("def precheck_session_inputs")
    body = src[body_start:body_start + 4000]
    call_lines = [ln for ln in body.splitlines() if "check_demo_api(config)" in ln]
    assert call_lines, "预检不再调用 check_demo_api——文档的更正依据失效，需同步复核 first-bug.md"
    for line in call_lines:
        assert line.strip() == "check_demo_api(config)", (
            f"check_demo_api 的调用形态变了（可能已按需化）：{line.strip()!r}——"
            f"若确实改成按需，请同步更新 docs/first-bug.md 的 demo_api 行与本测")


def test_doc_points_to_the_registered_boundary():
    text = _doc()
    assert "wave5-backlog.md" in text, (
        "更正必须指向已登记边界（wave5-backlog 的 attach_demo 行），否则读者以为这是没人管的坑")
    assert "attach_demo" in text, "要点名根因（attach_demo 无条件装配），不能只说「会挡」"


def test_doc_gives_both_workarounds():
    text = _doc()
    assert "ATLAS_DEMO_API_PROJECT" in text, "必须给出第一条可行路径（指向含 scripts/demo_api.py 的目录）"
    assert "atlas gen --with-pytest" in text or "atlas compile" in text, (
        "必须给出第二条路径（离线面：compile / gen --with-pytest），不能只说「不行」")

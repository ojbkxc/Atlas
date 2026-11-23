"""**无头默认**守卫：调试与测试一律 headless，有头只能由人显式打开。

## 为什么钉住它

项目口径：调试与测试一律用无头浏览器。Atlas 的实现是 `headless: bool = True` 默认值 +
`ATLAS_HEADED=1` 才翻成有头 + `--headed` 显式开关（`session.py` 把 `headless` 直接传进
`chromium.launch(headless=…)`）。

默认值属于「一行改动就悄悄反转全项目行为」的位置：把 `True` 改成 `False`，全仓离线测照样绿
（它们不碰浏览器），但所有真机跑都会弹窗、CI 起不了。这正是需要**会变红的判据**的地方。

## 四条

1. `test_default_config_is_headless`：默认 `headless is True`；
2. `test_headed_env_is_opt_in_and_strict`：只有精确 `ATLAS_HEADED=1` 才开有头，
   `0`/`true`/`yes`/`2`/空串一律仍无头（值域收紧，不吃宽松写法的坑）；
3. `test_launch_kwargs_follow_config`：`session` 把 config 的 headless 传给 `chromium.launch`；
4. `test_headed_flag_default_is_suppress`：`--headed` 默认 SUPPRESS（不给时不影响子命令默认）。
"""
from __future__ import annotations

import re
from pathlib import Path

from atlas.config import AtlasConfig

_REPO = Path(__file__).resolve().parents[1]


def _project(tmp_path) -> Path:
    """造一个**独立项目根**：`.atlas-root` + 空 `.env`，避免向上加载仓库根真 `.env`。"""
    (tmp_path / ".atlas-root").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    return tmp_path


def _clean_env(monkeypatch):
    monkeypatch.delenv("ATLAS_HEADED", raising=False)


def test_default_config_is_headless(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    assert AtlasConfig(root=_project(tmp_path)).headless is True, \
        "默认不是无头——全项目真机跑都会弹窗"


def test_headed_env_is_opt_in_and_strict(tmp_path, monkeypatch):
    root = _project(tmp_path)
    for value in ("0", "", "true", "yes", "2"):
        monkeypatch.setenv("ATLAS_HEADED", value)
        assert AtlasConfig.from_root(root).headless is True, \
            f"ATLAS_HEADED={value!r} 不该开有头"
    monkeypatch.setenv("ATLAS_HEADED", "1")
    assert AtlasConfig.from_root(root).headless is False, "ATLAS_HEADED=1 应开有头（显式 opt-in）"


def test_launch_kwargs_follow_config():
    source = (_REPO / "src" / "atlas" / "session.py").read_text(encoding="utf-8")
    assert 'launch_kwargs = {"headless": headless' in source, \
        "chromium.launch 没有吃到 config 的 headless"
    assert re.search(r"headless\s*=\s*self\.config\.headless", source), \
        "session 未从 config 取 headless（可能被写死）"


def test_headed_flag_default_is_suppress():
    source = (_REPO / "src" / "atlas" / "cli.py").read_text(encoding="utf-8")
    assert '"--headed", action="store_true", default=argparse.SUPPRESS' in source, \
        "--headed 的默认值变了：不给时必须保持子命令默认（无头）"

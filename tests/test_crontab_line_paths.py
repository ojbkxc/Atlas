"""`schedule crontab` 生成行必须**只注入真实存在的 PYTHONPATH**。

## 挡的是什么（2026-11-24 dogfood 实测）

`atlas schedule crontab` 给用户产出「可直接粘贴进 crontab / schtasks 的一行」。旧实现无条件写
`PYTHONPATH=<root>/src`：

```
schtasks /Create ... /TR "cmd /c cd /d <root> && set PYTHONPATH=<root>\\src && python -m atlas ..."
```

但 `<root>` 是**用户项目根**，脚手架项目里根本没有 `src/`（实测
`Test-Path <proj>\\src = False`）——Atlas 是 pip 装进环境里的，`python -m atlas` 本就不需要它。
于是这行把一个**不存在的目录**塞给用户，读的人要么以为「我必须得有个 src」（去建空目录），
要么以为「这行是别人机器的路径」（不敢粘贴）。属于「生成给别人的东西里带假信息」，
与产物侧那一族同源。

## 修法与边界

只在目录**确实存在**时注入（`<root>/src` 或 `<root>` 本身就含 `atlas/` 包时另说——这里只认
`src` 布局，那是唯一有实证的形态）。不存在就不注入：pip 安装下 `python -m atlas` 自足。
`ATLAS_KNOWLEDGE_DIR` 的注入不变（那条有真实理由：cron 父 env 不含用户 export 的键）。

## 四条

1. `test_no_pythonpath_when_src_absent`：脚手架式项目（无 `src/`）→ 生成行**不含** PYTHONPATH；
2. `test_pythonpath_injected_when_src_present`：真 src 布局 → 生成行**含**且指向该目录
   （正向对照：不能一删了之，源码树跑法必须继续可用）；
3. `test_knowledge_dir_always_injected`：两条路径都必须带 `ATLAS_KNOWLEDGE_DIR`（**这条不受
   本次修复影响**——cron 父 env 里没有用户 export 的键，不注入会静默回落包内虚构知识）；
4. `test_generated_line_paths_are_quoted`：路径带空格时仍被引号包住（Windows `/TR` 形态）；
"""
from __future__ import annotations

from pathlib import Path

from atlas import cli


def _task(root: Path, flow: Path) -> dict:
    return {
        "id": "c1", "enabled": True, "flow": str(flow),
        "schedule": {"kind": "cron", "expr": "*/30 1-8 * * *",
                     "tz": "Europe/Amsterdam"},
        "created_at": "2026-11-05T00:00:00+00:00", "last_scheduled_for": None,
        "consecutive_failures": 0, "write_ops": ["none"], "writes_acknowledged": True,
    }


def _line(root: Path, platform: str) -> str:
    from datetime import datetime, timezone

    from atlas.scheduling.cron_parse import load_tz, min_gap_minutes, parse_expr

    flow = root / "flow.yaml"
    flow.write_text("id: f\nsteps: []\n", encoding="utf-8")
    args = type("A", (), {"platform": platform})()
    return cli._schedule_crontab(
        args, _task(root, flow), root,
        datetime(2026, 11, 5, 3, 30, tzinfo=timezone.utc),
        parse_expr, load_tz, min_gap_minutes)


def test_no_pythonpath_when_src_absent(tmp_path):
    """脚手架式项目根（无 src/）→ 不要凭空生成一个不存在的 PYTHONPATH。"""
    for platform in ("linux", "windows"):
        line = _line(tmp_path, platform)
        assert "PYTHONPATH" not in line, (
            f"{platform}：{tmp_path}/src 不存在，生成行仍注入 PYTHONPATH（假的路径）：\n{line}")


def test_pythonpath_injected_when_src_present(tmp_path):
    """正向对照：真 src 布局必须继续注入（源码树跑法不能因这次修复消失）。"""
    (tmp_path / "src").mkdir()
    for platform in ("linux", "windows"):
        line = _line(tmp_path, platform)
        assert "PYTHONPATH" in line, f"{platform}：src 存在却没注入 PYTHONPATH：\n{line}"


def test_knowledge_dir_always_injected(tmp_path):
    """不受本次修复影响：私有知识库路径两条形态都必须带（否则静默回落包内虚构知识）。"""
    for platform in ("linux", "windows"):
        assert "ATLAS_KNOWLEDGE_DIR" in _line(tmp_path, platform)


def test_generated_line_paths_are_quoted(tmp_path):
    """带空格的根目录：Linux 用引号、Windows 用 set "K=V" 形态，粘贴后不至于被拆参。"""
    spaced = tmp_path / "with space"
    spaced.mkdir()
    (spaced / "src").mkdir()
    linux = _line(spaced, "linux")
    assert f'{spaced.as_posix()}' in linux
    assert 'ATLAS_KNOWLEDGE_DIR="' in linux
    win = _line(spaced, "windows")
    assert 'set \\"PYTHONPATH=' in win, f"Windows 内层引号形态变了：\n{win}"

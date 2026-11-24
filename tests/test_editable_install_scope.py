"""**editable 安装指向本仓**守卫：别让某个 clone 的 `pip install -e` 悄悄接管全局环境。

## 挡的是什么（2026-11-24 本机实际踩到）

我在 `D:\\tmp\\atlas-r34\\clone`（为复现 CI bare-clone 而建）里跑了一次
`pip install -e ".[dev]"`——**全局 Python 3.13 的 `atlas-test-platform` 可编辑安装**从此指向
那个 clone。随后 clone 按纪律删掉，于是：

- `import atlas` 从 site-packages 的 `__editable__` 钩子指向**已不存在的目录**；
- 一批「子进程跑骨架、靠 site-packages 里的 atlas」的用例（`test_allure_e2e_offline` 等）
  收到 `ModuleNotFoundError: No module named 'atlas'`——**看起来像代码回归，实际是环境被
  另一个 clone 接管**。

这正是本会话反复撞的「结论绑来源」纪律的环境版：判据/测试看到的是**哪个 atlas**？
路径必须显式绑到本仓，且**能被机器检查**（人记不住自己上周在哪个目录装过 editable）。

## 三条

1. `test_editable_install_points_at_this_repo`：`import atlas` 的解析路径必须落在本仓
   `src/atlas` 内（不是别的 clone、不是 site-packages 的常规副本）；
2. `test_source_tree_importable_without_install`：`src/` 直接可用（`PYTHONPATH=src` 口径），
   保证「不依赖任何安装」的路径始终成立；
3. `test_guard_knows_how_to_repair`：失败信息里给出**可执行修复**（在哪个目录重跑
   `pip install -e .`），不让人去猜。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_REPAIR = f'`cd {_REPO} && python -m pip install -e .`'


_LAST: dict[str, str] = {"stderr": ""}


def _last_child_stderr() -> str:
    return _LAST["stderr"][-600:] or "(empty)"


def _resolved_atlas() -> Path | None:
    # 显式剔除 PYTHONPATH：pytest 会话通常带 `PYTHONPATH=src`，子进程继承后 import 的是
    # **源码树**而不是「已安装的 editable 包」——两者要分开验（本测验安装；下一条验源码树）。
    env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [sys.executable, "-c", "import atlas; print(atlas.__file__)"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace")
    _LAST["stderr"] = out.stderr or ""
    if out.returncode != 0:
        return None
    return Path(out.stdout.strip())


def test_editable_install_points_at_this_repo():
    resolved = _resolved_atlas()
    assert resolved is not None, (
        f"全局 Python 里 `import atlas` 失败——editable 安装可能指向已删除的目录。"
        f"修复：{_REPAIR}\n诊断：{_last_child_stderr()}")
    expected = (_REPO / "src" / "atlas").resolve()
    assert str(resolved.resolve()).startswith(str(expected)), (
        f"`import atlas` 解析到 {resolved}，不是本仓 {expected}——"
        f"某个 clone 的 editable 安装接管了全局环境（曾实测：clone 删掉后子进程用例"
        f"收到 ModuleNotFoundError，看起来像代码回归）。修复：{_REPAIR}")


def test_source_tree_importable_without_install():
    """不依赖任何安装：`PYTHONPATH=src` 必须能 import（CI 与本仓测都靠这条）。"""
    env = {**__import__("os").environ, "PYTHONPATH": str(_REPO / "src")}
    out = subprocess.run([sys.executable, "-c", "import atlas; print(atlas.__file__)"],
                         capture_output=True, text=True, env=env,
                         encoding="utf-8", errors="replace")
    assert out.returncode == 0, out.stderr[-400:]
    assert str(_REPO / "src" / "atlas") in out.stdout, out.stdout


def test_guard_knows_how_to_repair():
    assert "pip install -e ." in _REPAIR and str(_REPO) in _REPAIR

"""`review-code --fail-under` 的**值域闸**：负数会让门禁永不生效（静默假绿）。

## 挡的是什么（2026-11-24 实测）

`--fail-under` 只声明了 `type=int`，于是：

```
--fail-under -1    → rc 0（avg_score < -1 恒假）→ 门禁**永远通过**，评分 0 也不拦
--fail-under 101   → rc 1（avg_score < 101 恒真）→ 每次都红，噪音
```

`-1` 这种形态最危险：CI 里写着 `--fail-under -1` 看起来「设了阈值」，实际是一句话把
质量门禁关掉了，而**没有任何输出提示这件事**。与「配置静默吞键」同族：错的是人的手，
痛的是三个月后以为门禁在跑的人。

## 修法与边界

`_review_code` 入口显式判 `0 <= fail_under <= 100`，越界走 `_user_error`（rc 2 + 一行），
与 `--fail-under abc` 的 argparse 报错同族（都是「输入不合法」）。刻意**不用**
argparse `choices=range(0, 101)`：它会把 101 个可选值全打进 usage 行。

## 五条

1. `test_negative_fail_under_is_user_error`：`-1` → rc 2（改前 rc 0，门禁被静默关掉）；
2. `test_over_100_fail_under_is_user_error`：`101` → rc 2（改前 rc 1，噪音）；
3. `test_boundary_values_still_work`：`0` 与 `100` 合法（**正向对照**：不能把值域收过头）；
4. `test_message_names_the_value_and_reason`：一行 stderr 起点名原值与为什么（不是裸 rc 2）；
5. `test_rc_contract_doc_still_matches`：README 的 rc 表仍写 0/1/2（值域闸不新增退出码）。
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".atlas-root").write_text("", encoding="utf-8")
    (tmp_path / "test_a.py").write_text(
        "def test_ok():\n    assert compute() == 3\n", encoding="utf-8")
    return tmp_path


def _run(project: Path, value: str, capsys) -> tuple[int, str]:
    from atlas.cli import main
    rc = main(["--root", str(project), "review-code", str(project),
               "--fail-under", value, "--format", "json"])
    return rc, capsys.readouterr().err


def test_negative_fail_under_is_user_error(tmp_path, capsys):
    rc, err = _run(_project(tmp_path), "-1", capsys)
    assert rc == 2, f"负数阈值必须 rc 2（改前 rc 0 = 门禁被静默关掉），实为 {rc}"
    assert "-1" in err


def test_over_100_fail_under_is_user_error(tmp_path, capsys):
    rc, err = _run(_project(tmp_path), "101", capsys)
    assert rc == 2, f"超过 100 的阈值必须 rc 2，实为 {rc}"
    assert "101" in err


def test_boundary_values_still_work(tmp_path, capsys):
    """正向对照：0 与 100 都是合法边界（值域不能收过头）。"""
    project = _project(tmp_path)
    rc0, err0 = _run(project, "0", capsys)
    assert rc0 in (0, 1), f"--fail-under 0 应被接受（实为 rc {rc0}）：{err0[:120]}"
    assert "0..100" not in err0
    rc100, err100 = _run(project, "100", capsys)
    assert rc100 in (0, 1), f"--fail-under 100 应被接受（实为 rc {rc100}）：{err100[:120]}"
    assert "0..100" not in err100


def test_message_names_the_value_and_reason(tmp_path, capsys):
    _, err = _run(_project(tmp_path), "-5", capsys)
    assert "0..100" in err, err
    assert "恒假" in err or "门禁" in err, f"消息要说清后果（否则人只会改数字猜）：{err}"


def test_rc_contract_doc_still_matches():
    """值域闸不新增退出码：README 对 review-code 的 rc 声明仍是 0/1/2。

    **别用 `"3" not in 行` 这种断言**（2026-11-24 自曝）：那一行里写着 `Allure 2.39.0`，
    结尾的 `3` 会把守卫判红——子串匹配在这里是错的尺子。改为**精确匹配那句 rc 声明**。
    """
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    clause = re.search(r"rc 0 通过 / 1 门禁失败 / 2 用户错误", readme)
    assert clause, "README 里 review-code 的 rc 声明句变了（0/1/2 三值口径）"
    # 值域闸只复用既有 2，不引入新码：整句里不许出现 3/124 这类别的命令的码
    assert not re.search(r"rc 0 通过 / 1 门禁失败 / 2 用户错误 / \d+", readme), \
        "review-code 的 rc 声明出现了第四种码"

"""CI 门禁不能把失败吞掉：`|| true` 与「诊断崩了也算过」都必须被挡。

## 这条修的是什么（2026-11-17 审计）

`.github/workflows/ci.yml` 原来的 Offline gate 里写着：

    python -m atlas doctor || true

`atlas doctor` 在**裸 clone 上本来就该 rc 1**（没有真实旧项目目录 / DemoApi 路径）。
所以 `|| true` 的本意是「这一格的 rc 我不管」。但它同时把**两件完全不同的事**吞成一件：

1. 「rc 1 是因为缺真实项目」——预期形态，CI 该过；
2. 「`atlas doctor` 自己崩了 / 产不出 JSON / 仓库内资产真漂移了」——**必须红**。

判据被吞之后，第 2 类永远不会让 CI 红。这与本仓红线 #7（不许把失败洗成通过）同族，
只是发生在 CI 配置层，而 CI 配置没有测试覆盖——所以它一直没被发现。

## 判据

1. `ci.yml` 里不得出现 `atlas doctor || true`（以及等价的 `|| :`）；
2. 必须有一条**读 JSON 逐项判定**的步骤（`--format json` + 显式 utf-8 解码）；
3. 该步骤必须要求 `docker_assets_consistent` / `boundary_assets_consistent` 为 true
   ——这两项只看仓库内资产，与真实项目无关，坏了就是真漂移；
4. 允许 `legacy_pages` / `demo_api` 为 false（裸 clone 的预期形态），但必须**打印**出来
   （「允许但不隐身」）。

## 为什么第 2 条要钉「显式 utf-8 解码」

本地实测踩到过：`subprocess.run(..., text=True)` 在 Windows 上按 GBK 解码 doctor 的
UTF-8 JSON → `UnicodeDecodeError` 直接崩。`atlas` 侧的 `ensure_utf8_streams()` 只管
**子进程自己的** stdout；父进程怎么读是另一件事。这条钉住父进程侧也要对齐。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _ci_text() -> str:
    assert CI_PATH.is_file(), "ci.yml 不存在——守卫失去对象"
    return CI_PATH.read_text(encoding="utf-8")


def test_ci_does_not_swallow_doctor_failure():
    """`atlas doctor || true` 必须消失（它把「预期 rc 1」与「真崩了」吞成一件）。

    只查**可执行行**：本文件与 ci.yml 的注释里都会**引用**那个坏写法来说明它为什么坏，
    按纯文本扫会把解释本身判红（第一版就踩了这个坑）。跳过 `#` 开头与 `run:` 块里的
    注释行，只看真正会被 shell 执行的内容。
    """
    text = _ci_text()
    offenders = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue          # YAML 注释：允许引用坏写法作说明
        if "atlas doctor" in stripped and ("|| true" in stripped or "|| :" in stripped):
            offenders.append(stripped)
    assert not offenders, (
        "CI 仍在吞掉 doctor 的退出码（可执行行）：\n  " + "\n  ".join(offenders) +
        "\n裸 clone 的 rc 1 是预期的，但『doctor 崩了』也必须红——"
        "请改成读 JSON 逐项判定（见 test_ci_doctor_check_reads_json_and_asserts_assets）")


def test_ci_doctor_check_reads_json_and_asserts_assets():
    """必须有一条读 JSON 的 doctor 步骤，且断言两项仓库内资产检查。"""
    text = _ci_text()
    assert '"doctor", "--format", "json"' in text or "doctor --format json" in text, (
        "CI 里没有以 JSON 形态调用 doctor 的步骤——无法逐项判定，只能整条吞或整条红")
    assert "docker_assets_consistent" in text, "CI 没有断言 docker 资产一致性"
    assert "boundary_assets_consistent" in text, "CI 没有断言边界账本一致性"


def test_ci_decodes_subprocess_output_explicitly():
    """父进程读 doctor 输出必须显式 utf-8 解码，不能用 `text=True`。

    本地实测：`text=True` 在 Windows runner 上按 GBK 解码 UTF-8 JSON →
    `UnicodeDecodeError`，整步崩掉（比不检查更糟：它看起来像资产检查失败）。
    """
    text = _ci_text()
    if "doctor" not in text or "subprocess.run" not in text:
        return   # 没有子进程调用就不适用
    # 找到 doctor 那段子进程调用，检查它没有用 text=True 且做了 decode
    assert 'decode("utf-8"' in text or "decode('utf-8'" in text, (
        "CI 读 doctor 输出时没有显式 utf-8 解码——Windows runner 会 GBK 解码 UTF-8 而崩")
    # 允许别处用 text=True（例如别的步骤），但 doctor 那条不许
    for i, line in enumerate(text.splitlines(), 1):
        if "doctor" in line and "subprocess.run" in line and "text=True" in line:
            raise AssertionError(f"ci.yml:{i} 对 doctor 用了 text=True（Windows 上会 GBK 崩）")


def test_ci_allows_clone_dependent_checks_to_be_false():
    """`legacy_pages` / `demo_api` 必须被允许为 false，且打印出来。

    若哪天有人把这两项也加进 required，CI 在裸 clone 上会恒红 —— 那时正确的反应
    不是「容忍恒红」，而是「这两项本来就不该要求」。这条把它写死。
    """
    text = _ci_text()
    assert "legacy_pages" in text and "demo_api" in text, (
        "CI 没有提到两项 clone-dependent 检查——它们要么被误加进 required（恒红），"
        "要么彻底隐身（读者不知道它们没验）")
    # 它们不该出现在 required 元组里
    for line in text.splitlines():
        if "required =" in line:
            assert "legacy_pages" not in line, (
                f"legacy_pages 被加进了 required —— 裸 clone 上它恒为 false，CI 会恒红：{line.strip()}")
            assert "demo_api" not in line, (
                f"demo_api 被加进了 required —— 同一问题：{line.strip()}")
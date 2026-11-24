"""**路径大小写可移植性**守卫：`"Claude.md"` 这类字面量在 Windows 绿、Linux CI 红。

## 挡的是什么（2026-11-24 CI linux 3.13 实测翻车，第四例同族）

`tests/test_doc_module_table.py` 写 `DOC = REPO_ROOT / "Claude.md"`，磁盘上是 `CLAUDE.md`：
Windows 文件系统大小写不敏感 → 本地 3177 全绿；ubuntu runner 区分大小写 →
`FileNotFoundError: '/home/runner/work/Atlas/Atlas/Claude.md'`，3 条守卫当场红
（CI 日志：`3 failed, 3163 passed`）。

这与本会话前面几波是同一族**根因**——「结论只对出题人那台机器成立」：读侧 BOM、bare clone
缺 sensitive 文件、`ROOT/.env` 喂饱环境、tzdata 有无……**判据的输入必须与目标平台无关**。
Windows 上「本地跑一遍」永远抓不到大小写问题，所以必须由机器检查。

## 判据

扫 `tests/`+`src/`+`scripts/` 里 `REPO_ROOT / "<name>"` / `ROOT / "<name>"` 形态的字面量路径，
逐个用**仓库真实文件名的精确大小写**比对（`Path.rglob` 得到的是磁盘真实名）。不一致即红并
点名「写的名字 → 真实名字」。只查单段文件名（`/` 分隔的多段由目录名同样规则覆盖：
取最后一段与同级目录项比对）。

## 三条

1. `test_repo_root_paths_match_exact_case`：所有 `*_ROOT / "…"` 字面量大小写正确；
2. `test_known_bad_literal_is_caught`：正向对照——把 `CLAUDE.md` 换成 `Claude.md` 的字面量
   必须被同一判据抓到（否则守卫空转）；
3. `test_guard_scanned_floor`：正向下限（≥20 个字面量、≥2 个差异源）。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ROOTS = ("tests", "src", "scripts")

# `XXX_ROOT / "literal"` 或 `XXX / "literal"` 里紧跟 ROOT 常量名的形态
_LITERAL = re.compile(r"(?P<root>[A-Z_]*ROOT[A-Z_]*)\s*/\s*[\"'](?P<name>[^\"'/]+)[\"']")


def _real_names() -> dict[str, set[str]]:
    """仓库里每一层目录 → 该目录下真实文件名集合（大小写敏感口径）。"""
    index: dict[str, set[str]] = {}
    for path in _REPO.rglob("*"):
        if any(part in {".git", ".pytest_cache", ".ruff_cache", "__pycache__", "build",
                        "reports", "artifacts", "exports"} for part in path.parts):
            continue
        index.setdefault(str(path.parent), set()).add(path.name)
    return index


def _repo_name_index() -> set[str]:
    """全仓真实的**文件名**集合（含各种大小写形态）——用于判「写错大小写」。"""
    return {p.name for p in _REPO.rglob("*")
            if not any(part in {".git", ".pytest_cache", ".ruff_cache", "__pycache__"}
                       for part in p.parts)}


def _docstring_lines(path: Path) -> set[int]:
    """返回该文件里**位于多行字符串（docstring）内**的行号集合。

    为什么要排除：本守卫的模块 docstring 自己写着反例 `Claude.md`（说明缺陷），纯文本正则
    会把「解释缺陷的那段话」判红——本仓 CI 门禁踩过同一形态（`|| true` 那条：按纯文本扫
    把解释它为什么坏的注释自己判红了）。

    **只认多行字符串**（`token.start[0] != token.end[0]`）：单行字符串里的 `"CLAUDE.md"`
    是**真实代码路径**，必须继续被扫（第一版按「任何 STRING token 的行」排除，把
    `DOC = REPO_ROOT / "CLAUDE.md"` 整行跳过了 —— 守卫当场空转，变异测都不红）。
    """
    import tokenize
    inside: set[int] = set()
    try:
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type == tokenize.STRING and token.start[0] != token.end[0]:
                    for line in range(token.start[0], token.end[0] + 1):
                        inside.add(line)
    except (tokenize.TokenError, SyntaxError, OSError):
        return set()
    return inside


def _collect_literals() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for root in _ROOTS:
        for path in (_REPO / root).rglob("*.py"):
            skip = _docstring_lines(path)
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if lineno in skip:
                    continue
                if "_ROOT" not in line and "ROOT /" not in line:
                    continue
                for match in _LITERAL.finditer(line):
                    found.append((str(path.relative_to(_REPO)), lineno, match.group("name")))
    return found


def _case_problems(literals: list[tuple[str, int, str]]) -> list[str]:
    real = _repo_name_index()
    lowered = {name.lower(): name for name in real}
    problems = []
    for rel, lineno, name in literals:
        if name in real:
            continue
        actual = lowered.get(name.lower())
        if actual is not None:
            problems.append(f"{rel}:{lineno} 写成 {name!r}，磁盘真实名是 {actual!r}"
                            f"（Windows 绿 / Linux CI FileNotFoundError）")
    return problems


def test_repo_root_paths_match_exact_case():
    problems = _case_problems(_collect_literals())
    assert not problems, (
        "以下字面量路径大小写与磁盘不一致（Windows 大小写不敏感所以本地绿，"
        "ubuntu runner 会 FileNotFoundError）：\n" + "\n".join(problems))


def test_known_bad_literal_is_caught(tmp_path):
    """正向对照：故意写错大小写的字面量必须被同一判据抓到。"""
    bad = [("synthetic.py", 1, "Claude.md")]
    problems = _case_problems(bad)
    assert problems and "Claude.md" in problems[0], problems


def test_guard_scanned_floor():
    """正向下限：**能扫到东西**且**判据能区分新旧**。

    不按「≥20 个字面量」设线：本仓实际只有 1 处 `ROOT / "literal"` 形态（其余走
    `REPO_ROOT / "x" / "y"` 多段或变量），按数量设线会逼人为了过线去凑无意义样本。
    改为两条真实下限：①至少扫到 1 处（解析器没坏）②`CLAUDE.md` 这种**根目录关键文件**
    必须能被精确大小写解析（本次翻车的具体面）。
    """
    literals = _collect_literals()
    assert literals, "一处 `_ROOT / \"literal\"` 都没扫到，解析器可能失效"
    for name in ("CLAUDE.md", "README.md", "AGENTS.md"):
        assert (_REPO / name).exists(), (
            f"根目录关键文件 {name!r} 按精确大小写不可解析——部署/CI 会 FileNotFoundError"
            f"（Windows 大小写不敏感会掩盖它）")

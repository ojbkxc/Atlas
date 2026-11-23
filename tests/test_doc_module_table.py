"""文档模块表必须覆盖 `src/atlas` 的每个模块——新模块不许「悄悄存在」。

## 为什么需要（2026-11-18 结构审计）

`Claude.md` 的模块表是接手者的**第一张地图**：换平台、加能力、查判据在哪，
都从这里进。但它此前漏了 **14 个模块**（77 个中的 18%），其中不乏核心件：

    ui_family.py          —— 族表，wait/生成/体检的唯一取值口
    llm.py                —— LLM 客户端（autopilot 的决策面）
    generator/emit_compat.py —— 兼容腿产物树（vendored 垫片的唯一写点）
    cli_render.py         —— 机读 JSON 的唯一出口
    plugins/iservice/plugin.py —— 第二个真实业务域（插件缝的证明）

后果具体：一个新人想找「族判定在哪」，按目录翻到 `ui_family.py`，回头查地图发现没有
这一行——只能怀疑自己是不是看错了版本。地图缺格比地图画错更常见，也更难发现。

## 判据

每个 `src/atlas/**/*.py`（除 `__init__.py`）的**文件名**必须出现在模块表的某一行里。
表里允许两种写法（都算覆盖）：

1. **独立行**：`| `ui_family.py` | ... |`
2. **目录汇总行内嵌名**：`| `generator/` | ... `sources.py` ... `openapi.py` ... |`
   （本仓对子目录用这种写法：一行概述 + 内嵌逐个点名）

## 刻意不做的事

- **不校验描述内容是否准确**——那要读代码判断语义，静态做不到；写错了只能靠人审。
- **不要求每行都长**——`__main__.py` 那种一行就够。判据只问「提到了没」。
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "Claude.md"
SRC = REPO_ROOT / "src" / "atlas"


def _module_files() -> list[str]:
    """`src/atlas` 下所有模块文件名（去重到裸文件名，表里按名字覆盖）。"""
    names: list[str] = []
    for p in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(p) or p.name == "__init__.py":
            continue
        names.append(p.name)
    return names


def _module_table_rows() -> str:
    """模块表那一段文本（所有以 `| ` 开头的表格行拼接）。"""
    text = DOC.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert rows, "Claude.md 里找不到模块表（以 `| \\`` 开头的行）——表被改写了？"
    return "\n".join(rows)


def test_doc_table_has_real_coverage():
    """正向对照：表必须解析出足够多的行，否则下面的「全覆盖」是恒真。"""
    rows = _module_table_rows()
    row_count = rows.count("\n") + 1
    assert row_count >= 40, f"模块表只有 {row_count} 行——表被删减或格式变了，守卫需同步"
    files = _module_files()
    assert len(files) >= 60, f"只扫到 {len(files)} 个模块文件——目录结构或扫描条件变了"


def test_every_module_appears_in_the_doc_table():
    """核心判据：每个模块文件名都必须在表里被提到（独立行或目录汇总行内嵌）。

    红的时候按提示补一行，或把它加到对应目录汇总行里。**不要**为了让测试变绿而删模块。
    """
    table = _module_table_rows()
    missing = [name for name in _module_files() if name not in table]
    # 去重（不同子目录可能有同名文件）
    missing = sorted(set(missing))
    assert not missing, (
        "下列模块在 `Claude.md` 的模块表里**完全没出现**（地图缺格）：\n  "
        + "\n  ".join(missing) +
        "\n补法：独立加一行 `| `<name>` | 一句话职责 + 判据所在 |`，"
        "或把它内嵌到对应目录汇总行（如 `| `generator/` | ... `model.py` ... |`）。")


def test_no_doc_row_names_a_nonexistent_module():
    """反向：表里独立行点的文件名必须真的存在。

    死条目比缺条目更误导——读者照它去找一个已被删/改名的文件。
    """
    existing = set(_module_files())
    text = DOC.read_text(encoding="utf-8")
    declared = set(re.findall(r"^\| `([A-Za-z0-9_/]+\.py)`", text, re.M))
    dead = sorted(name for name in declared if pathlib.PurePosixPath(name).name not in existing)
    assert not dead, (
        f"模块表里有 `src/atlas` 下不存在的文件（死条目）：{dead}——"
        "文件已删/改名的话请同时改表（目录汇总行也要跟着改）")

"""门面契约：`atlas.screenplay` / `atlas` 的 `__all__` 与真实符号逐项核对。

CLAUDE.md「关键模块」声称两级门面（`atlas` / `atlas.screenplay`）的 `__all__`
与真实符号逐项核对 sound，但仓库此前无测试守它——`__all__` 里写了一个未绑定的
名字（改导入时漏改、拼写漂移）不会让任何既有用例变红，`import *` 会静默缺符号。

**零引用不等于要删**：`atlas.screenplay` 全仓无内部消费方是事实，但它是公共门面
（对外 API 契约）——本文件只守「声明与符号一致」，不主张删除或新增符号。
"""
from __future__ import annotations

import atlas
import atlas.screenplay as screenplay


def _all_names(module) -> list[str]:
    return list(getattr(module, "__all__", []))


def _missing_symbols(module) -> list[str]:
    return [name for name in _all_names(module) if not hasattr(module, name)]


def test_screenplay_all_declared_and_each_symbol_resolves():
    """screenplay 的 `__all__` 非空，且每一项都能在模块上 getattr 到。"""
    names = _all_names(screenplay)
    assert names, "atlas.screenplay 必须声明 __all__（公共门面契约）"
    missing = _missing_symbols(screenplay)
    assert missing == [], f"atlas.screenplay.__all__ 声明了未绑定符号：{missing}"


def test_screenplay_all_names_unique_and_are_strings():
    """重复名会静默压掉公共面（同一名字占两格），非字符串名让 `import *` 直接崩。"""
    names = _all_names(screenplay)
    assert all(isinstance(n, str) for n in names), f"__all__ 含非字符串项：{names}"
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"atlas.screenplay.__all__ 有重复名：{dupes}"


def test_screenplay_star_import_exposes_every_declared_name():
    """端到端：`from atlas.screenplay import *` 后每个 `__all__` 名字都可用。"""
    ns: dict = {}
    exec("from atlas.screenplay import *", ns)          # 测试内自证 import * 的端到端形态
    missing = [n for n in _all_names(screenplay) if n not in ns]
    assert missing == [], f"import * 后缺符号：{missing}"


def test_atlas_package_all_symbols_resolve():
    """第二级门面 `atlas` 同口径：`__all__` 每项都能在包上 getattr 到。"""
    names = _all_names(atlas)
    assert names, "atlas 必须声明 __all__（公共门面契约）"
    missing = _missing_symbols(atlas)
    assert missing == [], f"atlas.__all__ 声明了未绑定符号：{missing}"
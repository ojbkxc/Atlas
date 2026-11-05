"""业务插件解析单一入口（引擎级缝）。

cli/runtime 里旧硬编码 `DemoPlugin()` 的机械替换点：未设 ATLAS_PLUGIN 时行为与旧实现
完全一致（含 make_provider 语义）；设 ATLAS_PLUGIN=<domain> 时加载对应插件。

引擎不含业务字面量——domain→类 的映射由插件注册（register_plugin），默认仅 demo
（引擎自带的虚构示例插件）。这是本轮唯一引擎改动，不得引入业务字面量。

Gap2 增强：未在 registry 注册的 domain 尝试动态加载——ATLAS_PLUGIN 含路径分隔符/
以 .py 结尾 → 按文件加载取模块级 PLUGIN；否则 → import atlas.plugins.<name> → import
<name>（外部项目把 PYTHONPATH 指到项目根后 `plugins.<domain>` 可被发现）。
加载成功返回实例；全部失败点名已注册清单与尝试过的路径（#90/#103 归因口径）。
"""
from __future__ import annotations
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from .plugins.demo import DemoPlugin

_REGISTRY: dict[str, type] = {}


def register_plugin(domain: str, cls: type) -> None:
    """插件包导入时登记自己（未来多插件扩展点；引擎自带 demo 已在下方默认注册）。"""
    _REGISTRY[domain] = cls


def _load_external_plugin(domain: str):
    """按 ATLAS_PLUGIN 取值动态加载外部插件模块，取模块级 PLUGIN。

    路径形态（含 / \\ 或 .py 结尾）→ 文件加载；模块名 → import atlas.plugins.<name>
    再退 import <name>。全部失败返回 None，由 resolve_plugin 统一点名。
    """
    path = Path(domain)
    if domain.endswith(".py") or "/" in domain or "\\" in domain:
        if not path.is_file():
            return None
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        spec.loader.exec_module(module)
    else:
        for candidate in (f"atlas.plugins.{domain}", domain):
            try:
                module = importlib.import_module(candidate)
                break
            except ModuleNotFoundError:
                continue
        else:
            return None
    return getattr(module, "PLUGIN", None) or getattr(module, "get_plugin", lambda: None)()


def resolve_plugin():
    """按 ATLAS_PLUGIN 解析业务插件实例；缺省/空 = demo（与旧 DemoPlugin() 完全等价）。

    ATLAS_PLUGIN 指到未注册 domain 时先尝试动态加载（Gap2 外部插件）；
    仍找不到抛 ValueError——CLI 入口接 _user_error（rc 2）。
    """
    domain = os.environ.get("ATLAS_PLUGIN", "").strip() or "demo"
    cls = _REGISTRY.get(domain)
    if cls is not None:
        return cls()
    plugin = _load_external_plugin(domain)
    if plugin is not None:
        return plugin
    raise ValueError(f"未知业务插件 {domain!r}（已注册：{', '.join(sorted(_REGISTRY))}；"
                     f"动态加载亦未找到）")


register_plugin("demo", DemoPlugin)

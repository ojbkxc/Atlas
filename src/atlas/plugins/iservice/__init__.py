"""iService 业务插件包。

导入即 `register_plugin`，使 `resolve_plugin()` 发现本插件；同时提供模块级 `PLUGIN`
供 `plugin_api._load_external_plugin`（ATLAS_PLUGIN=iservice 的动态加载路径）取用。
两条路径等价，引擎零改动（plugin_api.py 不含任何 iService 字面量）。
"""
from ...plugin_api import register_plugin
from .plugin import IservicePlugin
from .tasks import IserviceTasks, core_tasks

PLUGIN = IservicePlugin()
register_plugin("iservice", IservicePlugin)

__all__ = ["PLUGIN", "IservicePlugin", "IserviceTasks", "core_tasks"]

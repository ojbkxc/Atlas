"""Demo 是第一个业务插件，不是 Atlas 引擎本身。"""
from .tasks import DemoTasks, core_tasks
from .probes import CustomerProbe, SalesOrderProbe, make_demo_provider
from .plugin import DemoPlugin

__all__ = ["DemoPlugin", "DemoTasks", "core_tasks", "CustomerProbe", "SalesOrderProbe", "make_demo_provider"]

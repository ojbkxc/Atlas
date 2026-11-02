from pathlib import Path
from .tasks import core_tasks
from ...dsl import TaskRegistry
from ...knowledge import KnowledgeBase, SelectorRegistry

class DemoPlugin:
    name = "demo"

    def __init__(self):
        self.knowledge_root = Path(__file__).parent / "knowledge"

    def knowledge(self) -> KnowledgeBase:
        return KnowledgeBase(self.knowledge_root)

    def selectors(self) -> SelectorRegistry:
        return SelectorRegistry(self.knowledge().load("components"))

    def session_defaults(self) -> dict:
        """Demo 会话默认值（账号/OU/语言/division）的唯一数据源；密码永远不在这里。

        CLI 与 DemoRuntime 都从这里取，避免业务字面量散落在通用引擎里并多处漂移。
        """
        import json
        return json.loads((self.knowledge_root / "session.json").read_text(encoding="utf-8"))

    def register_tasks(self, registry: TaskRegistry, legacy_pages_path: Path | None = None) -> None:
        for task in core_tasks(legacy_pages_path):
            # 必须把默认参数一并带过去，否则 registry 构造出的新实例会丢 page_key 等必需参数。
            registry.register(task.name, lambda task=task, **kwargs: task.clone(**kwargs))

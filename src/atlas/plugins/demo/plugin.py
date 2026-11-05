from pathlib import Path
import json
import os
from .tasks import core_tasks
from ...contracts import ContractRegistry
from ...dsl import TaskRegistry
from ...knowledge import KnowledgeBase, SelectorRegistry

class DemoPlugin:
    name = "demo"
    # DemoApi 白名单是 Demo 业务资产（AGENTS 红线 11：引擎不携带业务操作名）。
    # #121 面B：旧定义硬编码在 runtime.__enter__——换业务必须改引擎。
    # 现收敛到插件单一真相，运行时装配引用；插件消费者（tasks.py 的
    # getattr(api, "atlas_allowed_operations", …)）检查语义不变。
    allowed_api_operations = frozenset({
        "customer_detail", "customer_list", "sale_order_list",
        "sale_order_change_list", "sale_order_return_list", "find_cust_details",
    })

    def __init__(self):
        self.knowledge_root = Path(__file__).parent / "knowledge"
        # 真实业务知识属于公司资产：ATLAS_KNOWLEDGE_DIR 指到私有目录后，
        # 读逐文件回退包内虚构默认，写（review 批准/组件回写）只落私有目录，仓库零污染。
        _priv = os.environ.get("ATLAS_KNOWLEDGE_DIR")
        self.private_root = Path(_priv) if _priv else None

    def knowledge(self) -> KnowledgeBase:
        if self.private_root:
            return KnowledgeBase(self.private_root, fallback_root=self.knowledge_root)
        # #135：未设 ATLAS_KNOWLEDGE_DIR 时返回**只读库**（root=None）——旧实现直接
        # KnowledgeBase(包内根) 让 review --approve/--apply 把业务批准写进 git 资产，
        # 违反 AGENTS 红线 #11「批准永不写包内文件」。读照常回退包内虚构默认，
        # 写路径当场点名要私有目录。
        return KnowledgeBase(None, fallback_root=self.knowledge_root)

    def selectors(self) -> SelectorRegistry:
        return SelectorRegistry(self.knowledge().load("components"))

    def contracts(self) -> ContractRegistry:
        """契约走与知识相同的 私有→包内 逐文件回退。真实项目用 ATLAS_KNOWLEDGE_DIR 覆盖
        contracts/core.json 时必须生效——直读包内会拿虚构 demo schema 校验真实报文。损坏/缺失逐级
        回退，两级皆无才报错（契约是断言层，静默空表会掩盖缺陷，违红线 #7）。CLI 与 DemoRuntime
        共用这一单一来源，杜绝「atlas contracts 展示的 ≠ 运行时实际校验的」漂移。"""
        candidates = []
        if self.private_root:
            candidates.append(self.private_root / "contracts" / "core.json")
        candidates.append(self.knowledge_root / "contracts" / "core.json")
        for path in candidates:
            if path.exists():
                try:
                    return ContractRegistry.load(path)
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    continue   # 私有文件写坏/形态不对/契约定义坏：跳过，回退下一来源
        raise ValueError(f"契约文件缺失或全部损坏，请检查 {' 或 '.join(str(p) for p in candidates)}")

    def session_defaults(self) -> dict:
        """Demo 会话默认值（账号/OU/语言/division）的唯一数据源；密码永远不在这里。

        CLI 与 DemoRuntime 都从这里取，避免业务字面量散落在通用引擎里并多处漂移。
        指向真实业务时用 ATLAS_SESSION_* 环境变量整体覆盖，仓库保持虚构默认。
        """
        from .tasks import overlay_session
        # KnowledgeBase.load 已按 私有→包内 逐文件回退，无需再直读包文件
        session = self.knowledge().load("session") or {}
        return overlay_session(session)

    def register_tasks(self, registry: TaskRegistry, legacy_pages_path: Path | None = None) -> None:
        for task in core_tasks(legacy_pages_path):
            # 必须把默认参数一并带过去，否则 registry 构造出的新实例会丢 page_key 等必需参数。
            registry.register(task.name, lambda task=task, **kwargs: task.clone(**kwargs))

from pathlib import Path
import json
import os
from .tasks import core_tasks
from ...contracts import ContractRegistry
from ...dsl import TaskRegistry
from ...knowledge import KnowledgeBase, SelectorRegistry
from ...probes import DataProvider

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
        # 通用知识包走**组合层**（base_roots），不走 fallback 影子层——影子语义会被平台
        # 自带的同名 pitfalls/components 整份挡住，通用包 82 条跨平台坑点对 demo 域
        # 永远不可见（P1-3 实测：接线前 pitfalls('demo') 只有包内 30 条）。
        # 惰性 import：plugin_api 顶层 import 了本插件包，模块级反向 import 会成环
        # （与 ui_family._table_path 同一处置）。
        from ...plugin_api import common_knowledge_root
        if self.private_root:
            return KnowledgeBase(self.private_root, fallback_root=self.knowledge_root,
                                 base_roots=common_knowledge_root())
        # #135：未设 ATLAS_KNOWLEDGE_DIR 时返回**只读库**（root=None）——旧实现直接
        # KnowledgeBase(包内根) 让 review --approve/--apply 把业务批准写进 git 资产，
        # 违反 AGENTS 红线 #11「批准永不写包内文件」。读照常回退包内虚构默认，
        # 写路径当场点名要私有目录。
        return KnowledgeBase(None, fallback_root=self.knowledge_root,
                             base_roots=common_knowledge_root())

    def selectors(self) -> SelectorRegistry:
        # 组合层读取：通用组件包（login/table/dialog/…15 个）与平台组件并入，
        # 同名组件平台胜出。用 load() 会因影子遮蔽丢掉整份通用组件库。
        return SelectorRegistry(self.knowledge().load_composed("components"))

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

    def session_defaults(self, env: str = "") -> dict:
        """Demo 会话默认值（账号/OU/语言/division）的唯一数据源；密码永远不在这里。

        CLI 与 DemoRuntime 都从这里取，避免业务字面量散落在通用引擎里并多处漂移。
        指向真实业务时用 ATLAS_SESSION_* 环境变量整体覆盖，仓库保持虚构默认。

        `env` 参数为跨插件统一契约（`plugins/iservice` 按环境取 `envs.json` 坐标）；
        demo 的环境坐标是虚构值且与 `session.json` 同源，不做第三层覆盖——传 env 时
        行为与不传逐字一致（#115：不把正常路径翻成改写）。
        """
        from .tasks import overlay_session
        # KnowledgeBase.load 已按 私有→包内 逐文件回退，无需再直读包文件
        session = self.knowledge().load("session") or {}
        return overlay_session(session)

    def tasks(self, legacy_pages_path: Path | None = None) -> list:
        """任务枚举单一入口（cli 的 catalog/compose/agent 走这里）。缺省 == core_tasks()。"""
        return core_tasks(legacy_pages_path)

    def register_tasks(self, registry: TaskRegistry, legacy_pages_path: Path | None = None) -> None:
        for task in self.tasks(legacy_pages_path):
            # 必须把默认参数一并带过去，否则 registry 构造出的新实例会丢 page_key 等必需参数。
            registry.register(task.name, lambda task=task, **kwargs: task.clone(**kwargs))

    def make_provider(self, config, api, division_code) -> DataProvider:
        """数据探针装配（runtime 按插件取 provider 的协议成员，与 iservice 同形）。

        内部委托现有 `make_demo_provider`，行为与旧 `runtime.__enter__` 直接调用逐字节
        等价（SalesOrder + Customer 两个探针）；惰性 import 保持插件内无新增模块级耦合。
        换业务只换本方法，不改 runtime（红线 11）。
        """
        from .probes import make_demo_provider
        return make_demo_provider(config, api, division_code)

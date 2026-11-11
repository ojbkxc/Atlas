"""iService（ICS 智能客户服务）业务插件：仓库内第二个**真实**业务插件。

存在意义（任务 2.6）：证明「换业务只换插件」——接入 iService 不改任何引擎代码
（`src/atlas` 下除本目录外零改动），只新增本包 + `ATLAS_PLUGIN=iservice`。
`resolve_plugin()` 经动态加载 `atlas.plugins.iservice` 取模块级 `PLUGIN` 解析本插件。

与 demo 插件的分工：
- `plugins/demo` 是**虚构示例**（业务字面量一律改名成 demo 词汇）。
- `plugins/iservice` 是**真实业务**：Task 名 / 组件选择器 / 契约路径 / 坑点取自仓库内
  既有可依据资料（`knowledge/ui-auto-test/` 迁移知识、`flows/iservice/`、
  `examples/legacy_fast_rules_iservice.json`、`reports/mach_*`、`artifacts/capture/`）。

红线（AGENTS/CLAUDE）：
- #11 引擎不含具体业务：业务字面量只在本插件的代码与 knowledge 里。
- 凭据不落盘：真实 URL/账号/密码/token 一律走 `${VAR}`/env（`ISERVICE_URL_*`、
  `ISERVICE_USERNAME`、`ISERVICE_PWD_<账号大写>`、`ATLAS_SESSION_*`），仓库只留键名。
- 不伪造：#10 无审核不写知识（knowledge/*.json 的坑点 status 一律 `pending_review`）；
  #9 只读 Task 才可自动跑；本插件未接 API 客户端，`allowed_api_operations` 如实留空。
"""
import json
import os
from pathlib import Path

from ...contracts import ContractRegistry
from ...dsl import TaskRegistry
from ...knowledge import KnowledgeBase, SelectorRegistry
from ...probes import DataProvider
from .probes import make_provider as _make_provider
from .tasks import core_tasks, overlay_session


class IservicePlugin:
    name = "iservice"
    # 如实留空：本插件只做 UI（委托旧页面库 lib/pages.py），仓库内无 iService 接口客户端
    # 可挂。声明「存在且可用」的 API 操作名必须有实现撑腰，否则就是伪造白名单（红线 #12）。
    # 接真实接口时在 plugins/iservice/ 下接客户端再逐项放开。
    allowed_api_operations = frozenset()

    def __init__(self):
        self.knowledge_root = Path(__file__).parent / "knowledge"
        # 真实业务知识属公司资产：ATLAS_KNOWLEDGE_DIR 指到私有目录后读逐文件回退本包默认，
        # 写（review 批准/组件回写）只落私有目录，仓库零污染。
        _priv = os.environ.get("ATLAS_KNOWLEDGE_DIR")
        self.private_root = Path(_priv) if _priv else None

    def knowledge(self) -> KnowledgeBase:
        if self.private_root:
            return KnowledgeBase(self.private_root, fallback_root=self.knowledge_root)
        # 未设 ATLAS_KNOWLEDGE_DIR＝只读库：批准永不写包内文件（红线 #11），写路径当场点名。
        return KnowledgeBase(None, fallback_root=self.knowledge_root)

    def selectors(self) -> SelectorRegistry:
        return SelectorRegistry(self.knowledge().load("components"))

    def contracts(self) -> ContractRegistry:
        """契约走与知识相同的 私有→包内 逐文件回退（契约是断言层，静默空表会掩盖缺陷，违红线 #7）。"""
        candidates = []
        if self.private_root:
            candidates.append(self.private_root / "contracts" / "core.json")
        candidates.append(self.knowledge_root / "contracts" / "core.json")
        for path in candidates:
            if path.exists():
                try:
                    return ContractRegistry.load(path)
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    continue   # 私有文件写坏/形态不对：跳过，回退包内默认
        raise ValueError(f"契约文件缺失或全部损坏，请检查 {' 或 '.join(str(p) for p in candidates)}")

    def session_defaults(self) -> dict:
        """iService 会话默认值（账号/OU/语言/division）唯一数据源；密码永不在这里。

        包内 session.json 是**结构默认**（账号/OU 留空，真实值走 ATLAS_SESSION_* env 或
        私有知识目录），避免真实账号落库（accounts 属私有信息）。语言固定 English US
        （iService 全环境强制，见 project_map.json）。
        """
        session = self.knowledge().load("session") or {}
        return overlay_session(session)

    def register_tasks(self, registry: TaskRegistry, legacy_pages_path: Path | None = None) -> None:
        for task in core_tasks(legacy_pages_path):
            # 必须把默认参数一并带过去，否则 registry 构造出的新实例会丢 page_key 等必需参数。
            registry.register(task.name, lambda task=task, **kwargs: task.clone(**kwargs))

    def make_provider(self, config, api, division_code) -> DataProvider:
        return _make_provider(config, api, division_code)

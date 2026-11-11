"""iService 核心 Task：元数据可独立用于流程拼装，实现委托旧页面库（lib/pages.py）。

Task 名 / requires / produces 取自仓库内既有可依据资料（iService 销售订单/退货/变更/报价
主流程的 ctx 契约，见 knowledge/ui-auto-test/domains.json、flows/iservice/、
examples/legacy_fast_rules_iservice.json 的页面函数名）。元数据必须与 perform 的实际
ctx 读写一致（AGENTS 开发规范 #2）：produces 的每个键由 perform 返回、requires 的每个键
由上游产出或 flow 供给（`atlas compile` / compose_for_goal 会校验）。

未接线的真实业务值一律留空占位（不伪造客户号/仓库码/PT 值），运行期由 flow 参数或
ATLAS_SESSION_* 提供 —— 造个「看起来像真的」的默认值比缺默认更坏（红线 #12/#7）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from ...core import Channel, Task, TaskMetadata

# session.json 是结构默认（账号/OU 留空）；真实值走 ATLAS_SESSION_* env（与 bridge 映射同精神：
# 引擎/仓库零真实字面量，真实值只在本机 .env 里）。
SESSION_ENV_KEYS = {"account": "ATLAS_SESSION_ACCOUNT", "ou": "ATLAS_SESSION_OU",
                    "division_code": "ATLAS_SESSION_DIVISION", "language": "ATLAS_SESSION_LANGUAGE"}


def overlay_session(defaults: dict) -> dict:
    for key, env in SESSION_ENV_KEYS.items():
        value = os.environ.get(env, "").strip()
        if value:
            defaults[key] = value
    return defaults


class Pages:
    """旧页面库加载器（与 demo 同款、各插件自持一份，不跨插件 import）。

    真实 iService 页面库在旧仓 `iservice/lib/pages.py`，经 ATLAS_LEGACY_PROJECT /
    ATLAS_LEGACY_WEB 指位。lib/pages.py 依赖 `from lib.runner import ...`，故先把 web
    子目录放进 sys.path，否则真实执行报 No module named 'lib'。
    """

    def __init__(self, path: Path | None = None):
        if path is None:
            from ...config import AtlasConfig
            config = AtlasConfig.from_root()
            path = (config.legacy_project / config.legacy_web / "lib" / "pages.py")
        self.path = Path(path)
        self._module = None

    def get(self):
        if self._module is None:
            root = self.path.parent.parent  # .../<legacy_web>
            if (root / "lib").is_dir() and str(root) not in sys.path:
                sys.path.insert(0, str(root))
            spec = importlib.util.spec_from_file_location("atlas_iservice_legacy_pages", self.path)
            if not spec or not spec.loader:
                raise ImportError(str(self.path))
            self._module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._module)
        return self._module

    def call(self, name: str, actor, *args, **kwargs) -> Any:
        function = getattr(self.get(), name)
        return function(actor.page, *args, **kwargs)


class IserviceTask(Task):
    function = ""
    parameters: tuple[str, ...] = ()
    # 业务默认值：agent/DSL 省略参数时用它，避免真实执行才暴露 TypeError。
    # 放在类属性而非实例，registry 重建实例（register_tasks 的 lambda）时才不会丢。
    defaults: dict = {}
    supports_api = False

    def __init__(self, metadata: TaskMetadata, pages_path: Path | None = None,
                 defaults: dict | None = None, **kwargs):
        self.metadata, self.pages_path = metadata, pages_path
        self.function = metadata.tags[0]
        self.defaults = {**type(self).defaults, **(defaults or {})}
        self.params = {**self.defaults, **kwargs}

    @property
    def name(self) -> str:
        return self.function

    def clone(self, **kwargs) -> IserviceTask:
        return type(self)(self.metadata, self.pages_path, defaults=self.defaults, **kwargs)

    def perform(self, actor) -> Any:
        result = Pages(self.pages_path).call(self.function, actor, **self.params)
        # 单产出时取业务主键，避免调用方拿到整个 dict 当单号用。
        if len(self.metadata.produces) == 1 and isinstance(result, dict):
            key = self.metadata.produces[0]
            for candidate in (key, "docNo", "orderNo", "no", "soNo", "rmNo"):
                if candidate in result:
                    return {key: result[candidate]}
            return {key: result}
        return result


def _task(function: str, intent: str, *, requires=(), produces=(), channel=Channel.UI,
          read_only=False, pages_path=None, defaults: dict | None = None) -> IserviceTask:
    return IserviceTask(
        TaskMetadata(domain="iservice", intent=intent, requires=tuple(requires),
                     produces=tuple(produces), channel=channel, read_only=read_only,
                     tags=(function,)),
        pages_path, defaults=defaults)


def core_tasks(pages_path: Path | None = None) -> list[IserviceTask]:
    """iService 主流程 Task。元数据 requires/produces 与 perform 的 ctx 读写一致。

    覆盖：销售订单建单链（open→new→customer→pt→warehouse→sales person→otherHeader→save→单号）
    + 来源单/退货原因两条退货链步骤。业务默认值只给结构性默认（page_key），真实业务值留空。
    """
    specs = [
        ("open_doc_list", "打开业务单据列表", (), ("doc_list_ready",), True,
         {"page_key": "sales_order"}),
        ("click_new", "进入新建单据页", ("doc_list_ready",), ("new_document_ready",), False,
         {"page_key": "sales_order"}),
        ("select_customer", "选择客户并等待异步回填", ("new_document_ready",), ("customer",), False,
         {"code": "", "code_full": ""}),
        ("read_pt", "读取 Payment Terms", ("customer",), ("payment_terms",), True, {}),
        ("select_pt", "改选 PT 并同步保存模型", ("payment_terms",), ("selected_payment_terms",),
         False, {"target": ""}),
        ("ensure_warehouse", "选择仓库并刷新校验状态", ("new_document_ready",), ("warehouse",),
         False, {"wh": ""}),
        ("ensure_sales_person", "选择销售员", ("customer",), ("sales_person",), False, {}),
        ("mount_other_header", "挂载扩展头，规避保存链缺陷", ("new_document_ready",),
         ("other_header_ready",), True, {}),
        ("click_save", "保存业务单据", ("customer", "warehouse", "sales_person",
                                        "other_header_ready"), ("save_result",), False, {}),
        ("wait_save_result", "等待保存结果并提取单号", ("save_result",), ("document_no",),
         True, {}),
        ("select_source_order", "选择来源单（退货/变更）", ("new_document_ready",),
         ("source_order",), True, {}),
        ("fill_return_reason", "填写退货明细 Return Reason", ("source_order",),
         ("return_reason_ready",), False, {}),
    ]
    return [_task(*spec[:2], requires=spec[2], produces=spec[3], read_only=spec[4],
                  defaults=spec[5], pages_path=pages_path)
            for spec in specs]


IserviceTasks = core_tasks

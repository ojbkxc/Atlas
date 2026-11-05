"""用例级桥接：把**整条**旧用例（yaml 骨架 + 同目录 step_N 实现）交给旧 `run_case_py` 执行。

为什么不逐步拆成 `legacy_step`（见 docs/migration-from-ui-auto-test.md §4 R3/R5）：
- `legacy_step` 只调 `fn(page, ctx)`，不播种 `ctx["_data"]/_base_url/_accounts`——旧用例第一步
  读 `ctx["_data"]["customer_code"]` 就 KeyError；
- 逐步拆会绕过旧 `run_case_py` 的 runlog 埋点、失败截图与「[断言失败] 步骤 N」措辞。

本类只做三件事：定位旧项目路径、把整条用例委托给旧 runner、把旧 runner 的 SKIP 信号翻译成
Atlas `EnvBlocked` 终态。**不重写任何 step_N、不改断言、不改旧仓任何文件**（红线 #4/#7）。

ctx 播种由旧 `run_case_py` 自己完成（`_data/_base_url/_accounts` 的唯一真相就在那里）；
本类不重复实现一份，避免两份播种逻辑漂移——这正是「不拷旧代码」的理由。
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

from ...core import Channel, Task, TaskMetadata
from ...failures import EnvBlocked

# 知识命中按 `domain in row["scope"]` 过滤（knowledge.py）。L2 私有库 104 条坑点 scope 以
# `iservice` 为权威词（demo 仅包内虚构默认），用 "iservice" 才能让人审通过的坑点在
# legacy_case 失败路径上真正可命中；沿用 LegacyStep 的 "legacy" 只有 4/104 条生效。
TASK_DOMAIN = "iservice"


def _skip_exceptions() -> tuple[type[BaseException], ...]:
    """旧 runner 的 SKIP 信号两种形态：`pytest.skip()` 的 `Skipped`（BaseException 子类，
    不在 `except Exception` 覆盖范围）与未包装的 `StepSkipped`（旧库异常）。"""
    kinds: list[type[BaseException]] = []
    try:
        import pytest

        kinds.append(pytest.skip.Exception)
    except Exception:  # pragma: no cover - pytest 是 Atlas 硬依赖，这里只做防御
        pass
    return tuple(kinds)


def _save_for_recycle(reports_dir: Path, entity: str, document_no: str) -> None:
    """把 legacy_case 真实建出的单号并入 Atlas 回收清单。

    红线 3「create 要受配额限制并保留可回收信息」：`runtime.__exit__` 只在
    `provider.quota.created` 非空时落盘，而桥接根本不经过 `provider.quota`（它把整条用例
    交给旧 `run_case_py`，建单发生在旧代码里）——不补这一步，SIT 主数据修好后真实建单成功，
    单号只存在于旧仓 runlog 的步骤文本里，`atlas recycle` 永远列不出来（#86 回收闭环断链、
    红线 3 失效）。复用 `CreateQuota.save_for_recycle` 的合并语义与坏字节防线，不复制一份写盘逻辑。
    """
    from ...probes import CreateQuota

    quota = CreateQuota(limit=1)
    quota.record_document(entity, {"orderNo": document_no, "source": "legacy_case"})
    quota.save_for_recycle(reports_dir / "recycle_list.jsonl")


def _load_runner(legacy_web_root: Path):
    """把旧项目 web 根放进 sys.path 后 import 旧 `lib.runner`（与 LegacyStep.from_module 同口径：
    旧用例/旧库都 `from lib.… import …`，不放进 path 会 No module named 'lib'）。"""
    web_root = str(legacy_web_root.resolve())
    if web_root not in sys.path:
        sys.path.insert(0, web_root)
    return importlib.import_module("lib.runner")


def _ensure_step_logging() -> None:
    """旧 runner 的 logger 默认只有 NullHandler（旧项目由 conftest 配 handler）。经 Atlas 执行时
    没人配置，逐步日志会全部静默——30 条用例跑起来「没有任何输出」无法排障。这里只在没有任何
    真实 handler 时补一个 stderr handler，不改旧仓、不改旧 runner。"""
    logger = logging.getLogger("ui_test")
    if any(not isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)


class LegacyCaseTask(Task):
    """一条旧用例 = 一个 Task；入参与 flow 形态见 §6.4。

    module   相对 `{legacy_web}/testcases/` 的 yaml 路径
             （如 `sit_bu_a_regression/01_sales_order_default_pt/sales_order_default_pt.yaml`）。
    case_id  用例 id（如 `TC1`），与旧 yaml `cases[].id` 对应。
    write    该用例是否会写业务单据（建单/保存/提交）。**默认 True（保守/失败安全）**：
             未显式声明的用例在 prod 环境会被 Atlas 写护栏拦成 env_blocked，而不是无声放行
             一次写操作。纯只读用例由 flow 显式声明 `write: false`——注意不能把这个旋钮
             命名成 `read_only`：`read_only` 是 DSL 编译器的保留键（Compile 会把它从 step
             参数里排除，见 dsl.py compile），写了也不会传进构造函数。
    skip_postcondition / skip_precondition  与旧 `run_case_py` 同名参数透传。
    save_as  可选；声明后按 `document_key`（默认 so_no）从 ctx 取单号自兑现产出。
    case_intent  人读的用例意图（写进 TaskMetadata.intent，供 `atlas tasks` 拼装挑选）。
             **不能叫 `intent`**：DSL 编译器的保留键里有 `intent`，写了会被从 step 参数中
             剔除、根本没传进构造函数（静默失效的死配置）。同理 `read_only`（保留）用
             `write`、`requires` 也不要指望透传。
    """

    def __init__(self, module: str, case_id: str, *, write: bool = True,
                 skip_postcondition: bool = False, skip_precondition: bool = False,
                 save_as: str = "", document_key: str = "so_no",
                 case_intent: str = ""):
        if not isinstance(module, str) or not module.strip():
            raise ValueError(f"legacy_case 的 module 必须是非空路径字符串，收到 {module!r}")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"legacy_case 的 case_id 必须是非空字符串，收到 {case_id!r}")
        if not isinstance(write, bool):
            raise ValueError(f"legacy_case 的 write 必须是布尔值，收到 {type(write).__name__}：{write!r}"
                             "（YAML 1.1 的 yes/no 会解析成布尔，合法；带引号的 'yes' 才是字符串）")
        for key, value in (("skip_postcondition", skip_postcondition),
                           ("skip_precondition", skip_precondition)):
            if not isinstance(value, bool):
                raise ValueError(f"legacy_case 的 {key} 必须是布尔值，收到 {value!r}")
        for key, value in (("save_as", save_as), ("document_key", document_key)):
            if not isinstance(value, str):
                raise ValueError(f"legacy_case 的 {key} 必须是字符串，收到 {value!r}")
        self.module = module.strip().replace("\\", "/")
        self.case_id = case_id.strip()
        self.write = write
        self.skip_postcondition = skip_postcondition
        self.skip_precondition = skip_precondition
        self.save_as, self.document_key = save_as, document_key
        self.metadata = TaskMetadata(
            domain=TASK_DOMAIN,
            intent=case_intent or f"旧用例 {self.module}#{self.case_id}",
            produces=(save_as,) if save_as else (),
            channel=Channel.UI,
            # dst 侧护栏唯一能否决写操作的地方：write=True 时 read_only=False，
            # prod 由 validate_write 拦成 env_blocked（classified 为环境策略阻挡，非缺陷）。
            read_only=not write,
            tags=("legacy", "case"),
        )

    @property
    def name(self) -> str:
        return f"legacy_case:{self.module}#{self.case_id}"

    # -- 路径 ---------------------------------------------------------------
    @staticmethod
    def _config():
        from ...config import AtlasConfig

        return AtlasConfig.from_root()

    def _paths(self) -> tuple[Path, Path]:
        config = self._config()
        web_root = config.legacy_project / config.legacy_web
        if not web_root.is_dir():
            raise FileNotFoundError(
                f"旧项目 web 子目录不存在：{web_root}"
                "（检查 ATLAS_LEGACY_PROJECT / ATLAS_LEGACY_WEB）")
        return config.legacy_project, web_root

    def yaml_path(self) -> Path:
        """module 相对 `{legacy_web}/testcases/`；拒绝跳出 testcases 根的路径。"""
        _, web_root = self._paths()
        testcases = (web_root / "testcases").resolve()
        target = (testcases / self.module).resolve()
        if target != testcases and testcases not in target.parents:
            raise ValueError(f"legacy_case 的 module 必须位于 {testcases} 之下，收到 {self.module!r}")
        if not target.is_file():
            raise FileNotFoundError(
                f"旧用例 yaml 不存在：{target}（检查 module 是否相对 {testcases}）")
        return target

    # -- 执行 ---------------------------------------------------------------
    def perform(self, actor) -> Any:
        _, web_root = self._paths()
        yaml_path = self.yaml_path()
        runner = _load_runner(web_root)
        _ensure_step_logging()
        ctx = actor.ctx
        try:
            runner.run_case_py(
                str(yaml_path), self.case_id, actor.page, ctx,
                skip_precondition=self.skip_precondition,
                skip_postcondition=self.skip_postcondition,
            )
        except _skip_exceptions() as exc:
            # 环境阻塞 / 依赖不满足：旧口径是 pytest.skip（不是失败）。Atlas 侧落 EnvBlocked，
            # 由 flow 的 `on_env_blocked: skip` 记成 env_blocked 终态，不污染 pass/fail（R7）。
            raise EnvBlocked(f"{self.case_id} SKIP：{exc}") from exc
        if self.save_as:
            document_no = ctx.get(self.document_key)
            # 只有真实拿到单号才进回收清单；None/空表示该用例没建成单（或源 yaml 声明的
            # 抽取键旧 .py 从不写，见 U5），绝不落一条无单号的假记录。
            if self.write and isinstance(document_no, str) and document_no.strip():
                try:
                    _save_for_recycle(self._config().reports_dir, self.name, document_no.strip())
                except OSError as exc:
                    # 回收落盘失败不得顶掉用例结果（与 runtime.__exit__ 同口径：清理链错误
                    # 让位真因），但必须留痕——静默丢单号正是红线 3 要防的。
                    logging.getLogger("atlas.legacy_case").warning(
                        "legacy_case 回收清单落盘失败（单号 %s 请人工登记）：%s", document_no, exc)
            return {self.save_as: document_no}
        return None

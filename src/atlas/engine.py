from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from .actor import Actor
from .config import AtlasConfig
from .contracts import ContractRegistry
from .core import Channel, Task, write_produces
from .abilities import CallApi
from .failures import CONTROL_FLOW, FailureClass, api_evidence, classify_failure
from .knowledge import KnowledgeBase
from .recorder import RunRecorder

def cdp_maybe_pause(case_id: str, step_index: int, task_name: str) -> None:
    """CDP 冻场钩子：步骤执行**前**冻住，供 playwright-cli attach 接管（对齐旧项目语义）。

    CDP_DEBUG=1 且 CDP_PAUSE_STEP 设置时生效；格式与 legacy-suite 一致：
      "3"        任何流程，序号 >= 3 冻
      "TC2:3"    case_id 含 TC2（不区分大小写）且序号 >= 3 冻
    旧项目按 Enter 中断退出；Atlas 语义是「人工干预后继续跑完」——
    Enter 放行下一步；非交互（EOF）直接放行，CI 永不因此挂起。
    run 模式不设置环境变量即零开销（两次 dict 查找）。
    """
    if os.environ.get("CDP_DEBUG", "").strip() != "1":
        return
    spec = os.environ.get("CDP_PAUSE_STEP", "").strip()
    if not spec:
        return
    case_filter, _, step_str = spec.rpartition(":")
    if case_filter and case_filter.lower() not in case_id.lower():
        return
    try:
        should_pause = step_index >= int(step_str)
    except ValueError:
        # #114 面C：坏配置（CDP_PAUSE_STEP=abc / "TC9:" / "1.5"）不得静默不冻——
        # 调试者以为会在步骤前等 attach，实际步骤直接跑过，案发现场永远错过；
        # 这与 #102/#105 假阴性同族：配置错误本身就该点名。但绝不因此挂起
        # （非交互零阻塞红线不变）：警告一行后照常放行。
        print(f"[CDP警告] CDP_PAUSE_STEP={spec!r} 无法解析（应为整数或 用例:整数，如 3 / TC2:3），本次不冻场", flush=True)
        return
    if not should_pause:
        return
    port = os.environ.get("CDP_PORT", "9222")
    print(f"\n{'=' * 70}\n[CDP接管点] #{step_index} {task_name}（case: {case_id}）\n"
          f"  浏览器保持开启，请在【新终端】执行：\n"
          f"    playwright-cli attach --cdp=http://localhost:{port}\n"
          f"  调试完成 -> 回本终端按 Enter 继续执行\n{'=' * 70}", flush=True)
    try:
        input()
    except EOFError:
        pass

class _null_step:
    def __enter__(self): return {}
    def __exit__(self, *args): return False

@dataclass
class StepResult:
    task: str
    ok: bool
    failure_class: str | None = None
    pitfall_id: str | None = None

class ExecutionEngine:
    """Task 执行策略：通道路由、契约、快照、实证与知识诊断。"""
    def __init__(self, config: AtlasConfig, *, contracts: ContractRegistry | None = None,
                 knowledge: KnowledgeBase | None = None, recorder: RunRecorder | None = None,
                 session=None, evidence=None, journal=None, healer=None, case_id: str = "flow"):
        self.config, self.contracts, self.knowledge, self.recorder = config, contracts, knowledge, recorder
        self.session, self.evidence, self.journal = session, evidence, journal
        self.healer = healer
        self.case_id = case_id
        self._step_index = 0
        self.results: list[StepResult] = []

    def execute(self, actor: Actor, task: Task) -> StepResult:
        # 冻场钩子必须在 perform 之前：步骤坏了就再也回不到案发现场。
        self._step_index += 1
        cdp_maybe_pause(self.case_id, self._step_index, task.name)
        context = self.recorder.step(task.name, task.metadata.channel.value) if self.recorder else _null_step()
        # 降级留痕游标：_perform 的 auto 降级原因只落在 ctx["_auto_degraded_to_ui"]，
        # 该键全仓除降级自身外零消费方——不并入 step record 就不落盘，runs.jsonl 里
        # 「API 坏→降级→UI 成功」步骤 ok=True 零痕迹，事后区分不出「UI 本来就该走」
        # 与「API 真的坏了」（#106，与 #102 静默 0 条/#105 假阴性同族红线）。
        degraded_cursor = len(actor.ctx.get("_auto_degraded_to_ui", []))
        # record 预置 None：__enter__ 失败时 with 从未绑定，
        # except 路径的 _flush_degradations 对非 dict 安全跳过，不留崩。
        record: Any = None
        try:
            with context as record:
                # #114 面A：写护栏必须在 recorder.step **开始之后**——旧实现在 with 外执行
                # validate_write，生产 PermissionError 抛出时 step 从未进入：runs.jsonl 零痕迹、
                # results 空——审计者看不出「谁在生产试图跑写步骤被拦」。拦截无痕也是审计链断点
                # （与 #85 中断留痕同红线）。classify 把「生产环境禁止」归 env_blocked：策略阻挡，
                # 不是代码缺陷（红线 #7：环境/操作与缺陷分开），summary 缺陷分布里如实可见。
                if not task.metadata.read_only:
                    self.config.validate_write(str(actor.ctx.get("remark", "非人工创建")))
                result = self._perform(actor, task)
                degraded_cursor = self._flush_degradations(actor, record, degraded_cursor)
                if task.metadata.channel in {Channel.API, Channel.AUTO} and CallApi in actor.abilities:
                    record["api_ms"] = actor.api.last_timing_ms
                # _GuardedTask skip 在 perform 期间追加 _steps_skipped，只能在 perform 后判断
                skipped = actor.ctx.get("_steps_skipped", [])[-1:] == [task.name]
                # 产出写入必须在 step 上下文**内**：谎报 produces（声明键不在返回 dict 里）
                # 是 Task 缺陷，必须被 recorder 记成失败步骤——若挪到 with 外，step 已按
                # perform 成功落 ok=True，引擎却走失败路径，重造 #81 的反向 phantom。
                # skip 步骤没真执行，不写产出：旧兜底把 None 静默塞进声明键，下游读到的
                # 「有值但为 None」比 KeyError 更难归因（test_dsl_step_skip 口径）。
                if not skipped:
                    write_produces(actor.ctx, task, result)
                if skipped:
                    # 被环境跳过的步骤不是通过：明确记 env_blocked，不伪装成 PASSED
                    record["fail_class"] = FailureClass.ENV_BLOCKED.value
            outcome = StepResult(task.name, True) if not skipped else StepResult(
                task.name, False, FailureClass.ENV_BLOCKED.value)
            # 被环境跳过的步骤不是通过：明确记 env_blocked，不伪装成 PASSED
            if self.session:
                self._record_shot(record, self.session.capture(task.name, True))
        except BaseException as exc:
            # 外层 _perform 可能「已降级但 UI 仍失败」才抛：成功路径的 flush 没跑到，
            # 降级条目滞留 ctx。失败步骤同样要留痕，否则 runs.jsonl 连「API 坏→退 UI 也失败」都查不到。
            degraded_cursor = self._flush_degradations(actor, record, degraded_cursor)
            if isinstance(exc, CONTROL_FLOW):
                # #114 面B：中断是终止信号不是业务失败（#70/#108 同口径）。
                # recorder 已按 classify 把 step 记成 interrupted（旧记 unknown——
                # atlas summary 缺陷分布冒出一类无法归因的「未知失败」）；
                # 这里如实进 results 后原样重抛。但绝不走下面的诊断链：
                # 坑点草稿登记器会把 Ctrl-C 收进待审塔（remedy 无从修复中断，纯噪音），
                # 取证截图给「操作者按了 Ctrl-C」拍现场照毫无意义。
                self.results.append(StepResult(task.name, False, FailureClass.INTERRUPTED.value))
                raise
            healed = False
            # heal 模式：只读 Task 且命中已审核知识才重跑一次；写操作绝不自动重试。
            if self.healer and self.config.mode == "heal" and self.healer.can_auto_heal(task, exc):
                try:
                    result = self._perform(actor, task)
                    # 重试也可能「API 坏→降级→UI 成功」：降级证据并入同一条 record（就地更正用）。
                    degraded_cursor = self._flush_degradations(actor, record, degraded_cursor)
                    # 与主路径同一契约：谎报 produces 不得让「自愈成功」静默写入脏 ctx——
                    # 此处抛错会被内层 except 捕获、healed=False，原异常继续走失败路径。
                    write_produces(actor.ctx, task, result)
                    healed = True
                except BaseException:
                    # 重试再次降级后仍失败：留痕再判 healed=False
                    degraded_cursor = self._flush_degradations(actor, record, degraded_cursor)
                    healed = False
            if healed:
                # 自愈成功后必须让运行记录与 results/flow 终态一致。第一次尝试失败时
                # recorder.step 的 finally 已把这条 step 写成 ok=False + fail_class，而重试成功
                # 旧实现不落任何成功记录：于是 atlas summary 会把一个「已自愈通过」的 run 按 step
                # 级 fail_class 误计出一个 phantom 失败类（reporting.py 的 status 取 flow 级、
                # fail_classes 取 step 级），且没有成功取证。这里就地更正同一条 record（recorder
                # 与引擎共享该 dict 对象，engine.py:86 的 skip 分支已用此协作口径）并补一次成功快照。
                # 归因必须取 approved：can_auto_heal 就是按已审核集放行的，若这里用无过滤匹配，
                # trigger 更长的 pending 草稿会顶掉真正的驱动知识，成功步骤被记错 pitfall_id（审计错位）。
                # 失败路径（下方）不做此过滤：诊断要呈现最长最具体匹配给人审，不受审核状态限制。
                pitfall = (self.knowledge.match_pitfall(exc, task.metadata.domain, status="approved")
                           if self.knowledge else None)
                if isinstance(record, dict):
                    record["healed"] = True
                    record["ok"] = True
                    record.pop("fail_class", None)
                if self.session:
                    self._record_shot(record, self.session.capture(task.name, True))
                self.results.append(StepResult(task.name, True,
                                               pitfall_id=pitfall.get("id") if pitfall else None))
                return self.results[-1]
            pitfall = self.knowledge.match_pitfall(exc, task.metadata.domain) if self.knowledge else None
            failure_class = self.evidence.classify(exc) if self.evidence else None
            outcome = StepResult(task.name, False, failure_class or classify_failure(exc).value,
                                 pitfall.get("id") if pitfall else None)
            if self.journal:
                self.journal.append_draft(self.journal.draft(task=task.name, exc=exc,
                                                             pitfall_id=outcome.pitfall_id or ""))
            if self.session:
                self._record_shot(record, self.session.capture(task.name, False))
            self.results.append(outcome)
            raise
        self.results.append(outcome)
        return outcome

    def _record_shot(self, record: Any, shot: Any) -> None:
        """#139：capture() 的截图路径并入 step 记录——旧实现三处调用全丢弃返回值，
        取证 PNG 真实落盘 artifacts/snapshots/ 却零引用进 runs.jsonl：atlas allure 的
        附件消费面（allure_report 读 step["attachment"]）永远拿 0 条，失败现场在
        Allure 报告里无声消失（#106 降级留痕零消费方×#86 回收断链同族：产出了没人记）。
        存相对项目根路径（runs.jsonl 可携，换机不悬空引用）；根外路径回落绝对形态
        （消费侧 is_absolute 直通）。run 模式成功步骤 capture 返回 None（红线 #8 零截图
        开销）→ 不写键，既有 runs.jsonl 形态零涟漪（#115）。"""
        if shot is None or not isinstance(record, dict):
            return
        path = Path(str(shot))
        try:
            record["attachment"] = str(path.relative_to(self.config.root))
        except ValueError:
            record["attachment"] = str(path)

    @staticmethod
    def _flush_degradations(actor: Actor, record: Any, cursor: int) -> int:
        """把 ctx 新增的 auto 降级条目并入本次 step 记录，返回新游标。

        recorder.step 全量序列化 record，降级事件据此落进 runs.jsonl——
        审计者事后能区分「UI 本来就该走」与「API 坏了才退 UI」。
        只搬运、绝不改变 _perform 的路由决策与 ctx 内容（既有降级语义零漂移）。
        """
        entries = actor.ctx.get("_auto_degraded_to_ui", [])
        if isinstance(record, dict) and len(entries) > cursor:
            record.setdefault("degraded_to_ui", []).extend(entries[cursor:])
        return len(entries)

    def _perform(self, actor: Actor, task: Task) -> Any:
        # auto 不偷偷绕过已实现的 UI 业务逻辑；只有 Task 明确提供 API 操作才可走 API。
        # 自定义 API Task 保留自己的请求/断言逻辑，不被通用 POST 覆盖。
        if task.metadata.channel == Channel.API and not getattr(task, "supports_generic_route", False):
            return task.perform(actor)
        if task.metadata.channel == Channel.API or (
                task.metadata.channel == Channel.AUTO and task.metadata.api_operation
                and getattr(task, "supports_api", False)):
            try:
                return self._perform_via_api(actor, task)
            except (NotImplementedError, RuntimeError) as exc:
                # API 路径未实现或失败：auto 降级 UI，api 直接抛
                if task.metadata.channel == Channel.API:
                    raise
                # 降级 UI 时不静默吞掉，记入 ctx 供调试
                actor.ctx.setdefault("_auto_degraded_to_ui", []).append(
                    {"task": task.name, "reason": str(exc)[:200]})
        return task.perform(actor)

    def _perform_via_api(self, actor: Actor, task: Task) -> Any:
        """优先走 Task 自己的 _perform_api；没有则用通用 POST。"""
        if getattr(task, "supports_api", False) and hasattr(task, "_perform_api"):
            return task._perform_api(actor)
        response = actor.api.post(task.metadata.api_operation, body=task.params)
        if self.contracts:
            self.contracts.validate("POST", task.metadata.api_operation, response)
        return response

    def prove_api(self, exc: BaseException, replay: Callable[[], dict]) -> FailureClass:
        """由调用方提供精确接口重放器，禁止引擎猜测写接口。"""
        return api_evidence(exc, replay())

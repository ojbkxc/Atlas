from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from .actor import Actor
from .config import AtlasConfig
from .contracts import ContractRegistry
from .core import Channel, Task
from .abilities import CallApi
from .failures import FailureClass, api_evidence, classify_failure
from .knowledge import KnowledgeBase
from .recorder import RunRecorder

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
                 session=None, evidence=None, journal=None, healer=None):
        self.config, self.contracts, self.knowledge, self.recorder = config, contracts, knowledge, recorder
        self.session, self.evidence, self.journal = session, evidence, journal
        self.healer = healer
        self.results: list[StepResult] = []

    def execute(self, actor: Actor, task: Task) -> StepResult:
        if not task.metadata.read_only:
            self.config.validate_write(str(actor.ctx.get("remark", "非人工创建")))
        context = self.recorder.step(task.name, task.metadata.channel.value) if self.recorder else _null_step()
        try:
            with context as record:
                result = self._perform(actor, task)
                if task.metadata.channel in {Channel.API, Channel.AUTO} and CallApi in actor.abilities:
                    record["api_ms"] = actor.api.last_timing_ms
                # _GuardedTask skip 在 perform 期间追加 _steps_skipped，只能在 perform 后判断
                if actor.ctx.get("_steps_skipped", [])[-1:] == [task.name]:
                    # 被环境跳过的步骤不是通过：明确记 env_blocked，不伪装成 PASSED
                    record["fail_class"] = FailureClass.ENV_BLOCKED.value
                    skipped = True
                else:
                    skipped = False
            for key in task.metadata.produces:
                actor.ctx[key] = result[key] if isinstance(result, dict) and key in result else result
            outcome = StepResult(task.name, True) if not skipped else StepResult(
                task.name, False, FailureClass.ENV_BLOCKED.value)
            # 被环境跳过的步骤不是通过：明确记 env_blocked，不伪装成 PASSED
            if self.session:
                self.session.capture(task.name, True)
        except BaseException as exc:
            healed = False
            # heal 模式：只读 Task 且命中已审核知识才重跑一次；写操作绝不自动重试。
            if self.healer and self.config.mode == "heal" and self.healer.can_auto_heal(task, exc):
                try:
                    result = self._perform(actor, task)
                    for key in task.metadata.produces:
                        actor.ctx[key] = result[key] if isinstance(result, dict) and key in result else result
                    healed = True
                except BaseException:
                    healed = False
            if healed:
                self.results.append(StepResult(task.name, True))
                return self.results[-1]
            pitfall = self.knowledge.match_pitfall(exc, task.metadata.domain) if self.knowledge else None
            failure_class = self.evidence.classify(exc) if self.evidence else None
            outcome = StepResult(task.name, False, failure_class or classify_failure(exc).value,
                                 pitfall.get("id") if pitfall else None)
            if self.journal:
                self.journal.append_draft(self.journal.draft(task=task.name, exc=exc,
                                                             pitfall_id=outcome.pitfall_id or ""))
            if self.session:
                self.session.capture(task.name, False)
            self.results.append(outcome)
            raise
        self.results.append(outcome)
        return outcome

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

class _null_step:
    def __enter__(self): return {}
    def __exit__(self, *args): return False

from __future__ import annotations
import contextlib
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from . import delivery, waits
from .actor import Actor
from .config import AtlasConfig, PROD_WRITE_WARNING
from .contracts import ContractRegistry
from .core import Channel, Task, write_produces
from .abilities import CallApi
from .failures import (CONTROL_FLOW, AwaitingHuman, DeliveryUncertain, FailureClass,
                       api_evidence, classify_failure, exception_brief)
from .knowledge import KnowledgeBase
from .recorder import RunRecorder

def _interrupt_main() -> None:
    """向主线程投递 KeyboardInterrupt（UI AUTO TEST `_case_watchdog` 同款机制）。

    卡死的 Playwright 同步调用不会自己返回；`_thread.interrupt_main` 让主线程在下一个
    字节码边界抛出 KeyboardInterrupt，engine 按 CONTROL_FLOW 记 interrupted 终态——
    「不会永远卡住」，且**绝不伪装 pass**（红线 #7）。零依赖（#4：只用 stdlib，不引
    pytest-timeout）。单独成函数以便离线测试注入替身。"""
    import _thread
    _thread.interrupt_main()


class CaseWatchdog:
    """用例级超时兜底（对齐 UI AUTO TEST 的 `_case_watchdog`）。

    后台 daemon 线程：超过 `timeout_seconds` 仍未结束 → 触发 `on_timeout`（默认
    `_interrupt_main`），让挂死的交互调用尽快抛出、engine 记 interrupted 终态。
    默认 `timeout<=0` **不启动**（零回归）。

    结束判据（二者之一即停，绝不误伤后续代码/下一个用例）：`is_done()` 返回真
    （recorder 已 close，dsl 在 finally 关闭）或 `stop()` 被显式调用。`on_timeout`
    可注入，供离线测试验证「到点触发 / 未到点不触发 / 已结束不触发」而不真的中断进程。

    边界（与 UI AUTO TEST 一致的固有语义，非缺陷）：`_thread.interrupt_main` 在**下一个
    字节码边界**投递 KeyboardInterrupt——Python 级挂起（步骤间、活跃循环）能被打断，但
    阻塞式 C 调用（如卡死的 Playwright 同步 dispatch）只在其返回字节码时才响应。看门狗是
    「尽力不永远卡住」的兜底，不是可抢占的硬杀；更强的整树强杀由 CLI 的 `--case-timeout`
    子进程超时层负责（两者互补，互不替代）。
    """
    def __init__(self, timeout_seconds: float, *, is_done: Callable[[], bool],
                 on_timeout: Callable[[], None] = _interrupt_main, tick: float = 0.5):
        self.timeout_seconds = float(timeout_seconds)
        self._is_done = is_done
        self._on_timeout = on_timeout
        self._tick = max(0.02, float(tick))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.fired = False

    def start(self) -> "CaseWatchdog":
        if self.timeout_seconds <= 0:
            return self
        self._thread = threading.Thread(target=self._run, name="atlas-case-watchdog",
                                        daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        waited = 0.0
        while waited < self.timeout_seconds:
            if self._stop.wait(self._tick):
                return
            waited += self._tick
            if self._is_done():
                return
        if self._stop.is_set() or self._is_done():
            return
        self.fired = True
        self._on_timeout()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

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
        # 用例级看门狗（默认关闭）：只在 recorder 在位（有明确的「用例结束」信号——
        # recorder.close 由 dsl.run 在 finally 调用）且 case_timeout>0 时启动。
        self._watchdog: CaseWatchdog | None = None
        timeout = getattr(config, "case_timeout", 0)
        if self.recorder is not None and timeout and timeout > 0:
            self._watchdog = CaseWatchdog(timeout, is_done=self._case_done).start()

    def _case_done(self) -> bool:
        """看门狗的「用例已结束」判据：recorder 关闭即 flow 跑完（dsl 在 finally close）。
        recorder 缺失时保守视为已完成（不误伤后续代码）。"""
        rec = self.recorder
        return rec is None or bool(getattr(rec, "_closed", False))

    def stop_case_guard(self) -> None:
        """显式停止看门狗（幂等）；正常路径由 recorder.close 触发自动停止。"""
        if self._watchdog is not None:
            self._watchdog.stop()

    def _retry_eligible(self, task: Task, exc: BaseException) -> bool:
        """步骤级重试准入：只读 Task + 可恢复交互类/导航瞬态失败。写操作绝不自动重试
        （副作用双跑），断言/业务/契约/会话/环境/控制流一律不重试——判据委托
        `waits.is_recoverable` 单一真相（#92），引擎不留第二份名单。"""
        if not task.metadata.read_only:
            return False
        return waits.is_recoverable(exc)

    def execute(self, actor: Actor, task: Task) -> StepResult:
        # 冻场钩子必须在 perform 之前：步骤坏了就再也回不到案发现场。
        self._step_index += 1
        cdp_maybe_pause(self.case_id, self._step_index, task.name)
        # en-11（死接线补生产侧）：真实 run 里 `ApiEvidence.classify` 的 rich 分类恒不触发——
        # 消费侧（api_evidence 的状态码/会话/环境判据）早已就绪，但**没有任何生产方**往
        # `_handlers` 里注册重放器。本处在步骤执行**前**为「显式只读 + API 通道」的 Task
        # 注册一个只读 GET 重放 handler：失败时 `evidence.classify` 会重放同接口拿实时证据，
        # 把会话失效/网关 5xx/契约违规真正接通（不再一律落 unknown 污染缺陷分布）。
        # 红线 #9：写任务**绝不**注册（`read_only` 元数据门在先）；GET 是只读语义，
        # 且 handler 本体 fail-safe——任何异常（网络/非 JSON/超时）都跳过、返回 None，
        # 由 ApiEvidence.classify 落到下一个 handler 或最终 None（回落 classify_failure）。
        # 定性器绝不能先于被定性对象死（#89 同红线）。
        # **每步单槽替换**（先清后注册）：旧实现每步 append 且从不清理，classify 按注册顺序
        # 首个命中——第二步失败会先重放第一步的接口（`/a` 成功 → interaction），真实环境 5xx
        # 被归因错位；长 flow 里 handler 无界增长、跨 flow 串味。清空放在注册之前且不依赖
        # Task 是否可重放：非 API/写任务/UI 任务也会清掉上一步留下的槽，跨 flow 同理
        # （新 flow 第一步即清），无需改 runtime.start_flow。
        self._reset_step_replay()
        self._register_readonly_replay(actor, task)
        context = self.recorder.step(task.name, task.metadata.channel.value) if self.recorder else _null_step()
        # 降级留痕游标：_perform 的 auto 降级原因只落在 ctx["_auto_degraded_to_ui"]，
        # 该键全仓除降级自身外零消费方——不并入 step record 就不落盘，runs.jsonl 里
        # 「API 坏→降级→UI 成功」步骤 ok=True 零痕迹，事后区分不出「UI 本来就该走」
        # 与「API 真的坏了」（#106，与 #102 静默 0 条/#105 假阴性同族红线）。
        degraded_cursor = len(actor.ctx.get("_auto_degraded_to_ui", []))
        # dispatch 留痕游标（#106 同族补网）：路由决策只落 ctx["_dispatch_routes"]，
        # 全仓零消费方写盘——runs.jsonl 里看得见 "dispatch:<gate>" 步骤却看不到取值与
        # 去向，「为何走此路径」事后无盘可查。与降级同一游标口径：perform 后搬运新增条目。
        routes_cursor = len(actor.ctx.get("_dispatch_routes", []))
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
                    # 重构波松绑：ATLAS_ALLOW_PROD_WRITE=1 显式放行生产写。放行不是静默的——
                    # stderr 打一行 ASCII 警告（cp936 可编），并把「这一步在生产被显式放行」
                    # 写进本 step record（可审计）。默认关时 `prod_write_allowed` 为假，
                    # 下面 validate_write 照旧抛 PermissionError，runs.jsonl 形态逐字节不变。
                    if self.config.prod_write_allowed:
                        print(PROD_WRITE_WARNING, file=sys.stderr, flush=True)
                        if isinstance(record, dict):
                            record["prod_write_allowed"] = True
                    self.config.validate_write(str(actor.ctx.get("remark", "非人工创建")))
                # 每步把「上一条请求耗时」归零，再让 perform 去更新它——`last_timing_ms` 是
                # **粘性**值（只有真发请求才写），旧实现直接抄进 `api_ms`：API 通道但本步没发
                # 请求的步骤（assert_api 读 ctx / 自定义 _perform_api 走自己传输）会继承上一步
                # 的接口耗时，runs.jsonl 里的 `api_ms` 记成别人家的数（跨步粘性状态，与 P0
                # 「重放 handler 跨步残留」同族）。归零入口单点在 CallApi.reset_timing。
                if task.metadata.channel in {Channel.API, Channel.AUTO} and CallApi in actor.abilities:
                    actor.api.reset_timing()
                result = self._perform(actor, task)
                degraded_cursor = self._flush_degradations(actor, record, degraded_cursor)
                routes_cursor = self._flush_routes(actor, record, routes_cursor)
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
            if self.session:
                self._record_shot(record, self.session.capture(task.name, True))
        except BaseException as exc:
            # 外层 _perform 可能「已降级但 UI 仍失败」才抛：成功路径的 flush 没跑到，
            # 降级条目滞留 ctx。失败步骤同样要留痕，否则 runs.jsonl 连「API 坏→退 UI 也失败」都查不到。
            degraded_cursor = self._flush_degradations(actor, record, degraded_cursor)
            # 「同刻超时」取证（design_c9 §9 那一格，2026-11-06 离线钉死）：看门狗已 fire
            # 与本步抛错**两件事可以同时为真**——KI 是投递给主线程的，任务在 KI 落地前先自己
            # 抛了 AwaitingHuman / DeliveryUncertain / 普通异常，捕获到的就是后者。旧实现只在
            # CONTROL_FLOW 分支内补这个标记，于是超时证据被**赢的那个终态静默吞掉**：台账只剩
            # waiting/failed，看不出「这一步其实也撞上了看护超时」，而这正是排查「为什么这条
            # 总在超时边缘」时最想要的一条信息。标记与终态正交，一律在分支之前打。
            if (isinstance(record, dict) and self._watchdog is not None
                    and self._watchdog.fired):
                record["case_timeout"] = True
            if isinstance(exc, CONTROL_FLOW):
                # #114 面B：中断是终止信号不是业务失败（#70/#108 同口径）。
                # recorder 已按 classify 把 step 记成 interrupted（旧记 unknown——
                # atlas summary 缺陷分布冒出一类无法归因的「未知失败」）；
                # 这里如实进 results 后原样重抛。但绝不走下面的诊断链：
                # 坑点草稿登记器会把 Ctrl-C 收进待审塔（remedy 无从修复中断，纯噪音），
                # 取证截图给「操作者按了 Ctrl-C」拍现场照毫无意义。
                # 「人按 Ctrl-C」与「看门狗超时中断」的区分由上面的 case_timeout 标记承担
                # （interrupted 终态不变——超时也不伪装 pass/failed）。
                self.results.append(StepResult(task.name, False, FailureClass.INTERRUPTED.value))
                raise
            if isinstance(exc, AwaitingHuman):
                # C9 显态（design_c9 §3 engine 行）：与 #114 面 B **同型**——如实进 results
                # 后原样重抛，不走 heal / `evidence.classify` / 坑点匹配 / journal 草稿 /
                # session 取证。理由同一条：remedy 无从「修复」等人，给等待现场拍取证照是纯噪音。
                # 差别一处：CONTROL_FLOW 是宿主终止（dsl 记 interrupted 并继续上抛退出），
                # AwaitingHuman 是业务终态（dsl 记 waiting 后由 CLI 转 rc3，进程收尾照常）。
                # 步骤的 fail_class="waiting" 由 recorder.step 的 classify_failure 单点继承
                # 自动产出，这里不裸写字符串（#92）。
                # **位置钉死（评审必改 C9-B）**：必须在 CONTROL_FLOW 之后、下面那个 flaky
                # 重试块（`retries = max(0, ...)` 起，gap-fill 波新增、两份设计都没评估过它）
                # 之**前**。
                # 落在重试块之后会怎样：reason 文案里带「timeout/超时」字样的 AwaitingHuman
                # 先被 `waits.is_recoverable` 判成可重试（classify→INTERACTION 那条腿按文本走），
                # 于是「等人」被就地重跑若干遍——等待现场被反复触发、run 记录多出 retried 噪音，
                # 而重试永远等不来人。回归测：test_waiting_operational.py::
                # test_engine_awaiting_human_never_retried（step_retry>0 × AwaitingHuman → perform 恰 1 次）。
                self.results.append(StepResult(task.name, False, FailureClass.WAITING.value))
                raise
            # C12（design_c12 §3.2）：投递不确定**不是控制流**——不短路诊断链，它照常走
            # 坑点草稿/取证/归因（真失败，红线 #7 保持 failed，不借任何终态洗绿）。这里只
            # 在失败路径就地补一个布尔键（判据来自类型而非字符串；与 healed 更正同协作口径
            # ——recorder 与引擎共享同一 dict，就地补键不新开 step），runs.jsonl 事后能
            # 分辨「写降级被 C12 拦」与一般 interaction 失败。写任务进不了下面的 flaky
            # 重试与 heal（read_only 门在先），故该标记落定即终态，不会被就地更正翻掉。
            if isinstance(exc, DeliveryUncertain) and isinstance(record, dict):
                record["delivery_uncertain"] = True
            # 步骤级 flaky 重试（默认关闭）：仅「只读 Task + 可恢复交互类失败」。
            # 写操作绝不自动重试（副作用双跑）；断言失败绝不重试（红线 #7：重试不是把
            # 失败放宽成通过）。判据与 waits.retry 的选择器同源（waits.is_recoverable）。
            retries = max(0, int(getattr(self.config, "step_retry", 0)))
            if retries and self._retry_eligible(task, exc):
                cursors = [degraded_cursor, routes_cursor]
                attempts_made = [0]

                def _one_attempt():
                    attempts_made[0] += 1
                    result = self._perform(actor, task)
                    # 与主路径同一契约：降级/路由决策并入同一条 record，产出严格写入。
                    cursors[0] = self._flush_degradations(actor, record, cursors[0])
                    cursors[1] = self._flush_routes(actor, record, cursors[1])
                    write_produces(actor.ctx, task, result)
                    return result

                try:
                    waits.retry(_one_attempt, attempts=retries,
                                interval_ms=getattr(self.config, "step_retry_interval_ms", 0),
                                retry_on=waits.is_recoverable)
                except BaseException as retry_exc:
                    degraded_cursor = self._flush_degradations(actor, record, cursors[0])
                    routes_cursor = self._flush_routes(actor, record, cursors[1])
                    if isinstance(retry_exc, CONTROL_FLOW):
                        self.results.append(StepResult(task.name, False,
                                                       FailureClass.INTERRUPTED.value))
                        raise
                    exc = retry_exc   # 用最后一次失败继续走诊断/草稿路径
                else:
                    # 重试成功：就地更正同一条 record（与 heal 成功同纪律）——第一次尝试
                    # recorder 已写 ok=False+fail_class，不更正就会让 atlas summary 冒出
                    # phantom 失败类；补 `retried` 计数保证「重试过」可审计（#106 教训）。
                    if isinstance(record, dict):
                        record["retried"] = attempts_made[0]
                        record["ok"] = True
                        record.pop("fail_class", None)
                    if self.session:
                        self._record_shot(record, self.session.capture(task.name, True))
                    self.results.append(StepResult(task.name, True))
                    return self.results[-1]
            healed = False
            # heal 模式：只读 Task 且命中已审核知识才重跑一次；写操作绝不自动重试。
            if self.healer and self.config.mode == "heal" and self.healer.can_auto_heal(task, exc):
                try:
                    result = self._perform(actor, task)
                    # 重试也可能「API 坏→降级→UI 成功」：降级证据并入同一条 record（就地更正用）。
                    degraded_cursor = self._flush_degradations(actor, record, degraded_cursor)
                    # 重试同样可能补上路由决策（第一次 perform 在 select 前抛错就没落条目）：
                    # 就地更正同一条 record，不新开步骤。
                    routes_cursor = self._flush_routes(actor, record, routes_cursor)
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
                                                             pitfall_id=outcome.pitfall_id or "",
                                                             evidence=self._verification()))
            if self.session:
                self._record_shot(record, self.session.capture(task.name, False))
            self.results.append(outcome)
            raise
        self.results.append(outcome)
        return outcome

    def _reset_step_replay(self) -> None:
        """清掉上一步留下的只读重放器（每步边界调用，单槽替换语义）。

        容忍任意 evidence 形态：`ApiEvidence` 标准实现有 `clear_replay`；测试/插件替身
        可能只有 `classify`（如 `test_engine_waiting_does_not_touch_evidence_classifier`
        的 BoomEvidence）或干脆是 None。**任何异常都吞掉**——本方法在 execute 的失败路径
        之前跑，抛错会把原用例异常顶掉（#89 同红线）。
        """
        if self.evidence is None:
            return
        clear = getattr(self.evidence, "clear_replay", None)
        if callable(clear):
            # 坏替身不得顶掉原用例异常（#89）——与 handler 本体的 fail-safe 同口径。
            with contextlib.suppress(Exception):
                clear()

    def _register_readonly_replay(self, actor: Actor, task: Task) -> None:
        """en-11：为「显式只读 + API 通道」的 Task 注册一个只读 GET 重放 handler。

        三重门（缺一不注册，红线 #9/#11）：
        - `task.metadata.read_only` 必须为真——写任务绝不重放（副作用双跑）；
        - `channel == Channel.API` 且 `api_operation` 非空——只有 API 通道有可重放的接口位；
        - `actor.api` 在场（CallApi Ability 已装配）——没有 API 驱动就无从重放。

        handler 契约（与 `ApiEvidence.classify` 对齐）：返回 dict（喂 `failures.api_evidence`）
        或 None（跳过，试下一个 handler / 最终回落 classify_failure）。**任何异常都吞掉返回
        None**——重放是「尽力而为的诊断」（#89：定性器绝不能先于被定性对象死，在 engine
        的 except 块里抛错会顶掉原用例异常、清空整条证据链）。

        只读性论证：GET 请求按 HTTP 语义无副作用；`CallApi.request` 本身不发写（method="GET"）；
        唯一的「写味」是 `ensure_session`（保活回调重新烘焙 Cookie）——那是会话维护、不是业务
        副作用，与任何正常 API 读同一张皮，不构成红线 #9 的「操作被自动第二次执行」。
        """
        if not task.metadata.read_only:
            return
        if task.metadata.channel != Channel.API or not task.metadata.api_operation:
            return
        if self.evidence is None:
            return
        # Ability 取用必须容忍三种形态，且**任何一条都不能抛**（本方法在 execute 的失败
        # 路径之前跑，抛错会把原用例异常顶掉、results 不追加——#89 同红线）：
        #   ① 标准装配：`actor.using(CallApi)`；② 测试/插件替身按自身类型注册（不是
        #   CallApi 的子类）；③ 完全没有 API Ability。`getattr(actor, "api", None)` 不够：
        #   Actor.api 属性在未注册时抛 **RuntimeError**（不是 AttributeError），getattr
        #   的 default 根本接不住。
        api = None
        try:
            api = actor.api
        except Exception:
            try:
                api = next((a for a in getattr(actor, "abilities", {}).values()
                            if hasattr(a, "get")), None)
            except Exception:
                api = None
        if api is None or not hasattr(api, "get"):
            return
        path = str(task.metadata.api_operation)
        # CallApi 在场；写死 GET（只读语义）。`params` 从 task.params 取（AssertApi 等既有
        # API Task 的查询参数位），没有就空查——CallApi.request(query=None) 沿用默认。
        params = getattr(task, "params", None)
        if not isinstance(params, dict):
            params = {}

        def _replay() -> dict | None:
            try:
                response = api.get(path, query=params)
            except Exception:
                return None   # 网络/超时/会话崩：跳过，不顶掉原异常（#89）
            if response is None or not isinstance(response, dict):
                return None
            # 归一成 api_evidence 期待的 {ok, status, data, error} 形态（ApiResponse 是 dict
            # 子类，逐键 copy 一份防下游改写污染会话缓存）。
            return {"ok": bool(response.get("ok")),
                    "status": response.get("status"),
                    "data": response.get("data"),
                    "error": response.get("error", "")}

        # 单槽替换（`set_replay`）优先；替身只有旧 `register` 时回落追加（兼容既有测试
        # 替身，标准实现走单槽——不累积）。
        setter = getattr(self.evidence, "set_replay", None)
        if callable(setter):
            setter(_replay)
        else:
            self.evidence.register(_replay)

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

    @staticmethod
    def _flush_routes(actor: Actor, record: Any, cursor: int) -> int:
        """把本步骤 perform 新增的 dispatch 路由决策并入 step 记录，返回新游标。

        决策原本只活在 ctx["_dispatch_routes"]（dsl.py _RouterTask），全仓无消费方写盘
        ——与 #106 的降级留痕同族断点：runs.jsonl 里 "dispatch:<gate>" 步骤 ok=True 却
        看不出走的哪条分支，事后审计「这轮为何走此路径」无盘可查。只搬运、绝不改变
        路由决策与 ctx 内容（dsl 侧语义零漂移）；未选中分支保持零记录（那是规格，
        见 test_dsl_template_dispatch 钉桩），留痕挂在路由节点自身的步骤上。
        """
        entries = actor.ctx.get("_dispatch_routes", [])
        if isinstance(record, dict) and len(entries) > cursor:
            record.setdefault("dispatch", []).extend(
                dict(item) for item in entries[cursor:])
        return len(entries)

    def _verification(self, passed: bool = False) -> dict:
        """C2 三元组唯一产出点：失败草稿与跑绿固化（落库线）共用同一把刀。

        失败路径（design_c2 §3.3，engine 归 impl_report 落）：失败草稿带
        「刚发生的失败复现」三元组，供审核塔的晋级闸（`review.verification_problems`）判用。
        成功路径（dsl._harvest_passed_flow 调用 passed=True）：跑绿的 flow_candidate 草稿
        带真实成功复现三元组——exit_code/verdict 由**真实终态派生**，绝不反着写
        （红线 #7 双向同一条：没绿不许装绿，绿了也不许装红）。

        诚实可得才填，拿不到的不猜：
        - `command`：本次 run 的真实复现命令（case_id 即 flow id / 兼容旧用例名形态）；
        - `exit_code`：失败路径走到这里就是 1；跑绿固化是 0（dsl 只在 status=="passed" 调）；
        - `assertions`：**核盘结论**（评审 C2-B 要求的实测项）——recorder 的 op 字面值是
          `task.metadata.channel.value`（ui/api/auto/hybrid），**不存在 `op=="assert"`**，
          照设计文本硬取恒 0 且无信息。实盘可得口径是「assert 块执行的 Task 名」：
          `dsl._run_asserts` 把 dict 形态的 assert 编译成 Task 走 engine.execute，step 名
          即该 Task 名（如 `assert_api`）；字符串表达式形态不落 step，统计不到就如实 0
          （0 恰是 GREEN 类反模式的可见信号，不编数、不放宽）；
        - `verdict`：失败路径恒 "fail"——绝不写 "pass"（红线 #7：没绿就不许装绿）。
        """
        steps = getattr(self.recorder, "steps", None) or []
        assertions = sum(1 for item in steps
                         if isinstance(item, dict)
                         and str(item.get("step", "")).lower().startswith("assert"))
        env = str(getattr(self.config, "env", "") or "")
        return {"verification": {
            "command": f"atlas run {self.case_id} --env {env}",
            "exit_code": 0 if passed else 1,
            "assertions": assertions,
            "verdict": "pass" if passed else "fail"}}

    def _perform(self, actor: Actor, task: Task) -> Any:
        # auto 不偷偷绕过已实现的 UI 业务逻辑；只有 Task 明确提供 API 操作才可走 API。
        # 自定义 API Task 保留自己的请求/断言逻辑，不被通用 POST 覆盖。
        if task.metadata.channel == Channel.API and not getattr(task, "supports_generic_route", False):
            return task.perform(actor)
        if task.metadata.channel == Channel.API or (
                task.metadata.channel == Channel.AUTO and task.metadata.api_operation
                and getattr(task, "supports_api", False)):
            # C12（design_c12 §3.2 + 评审必改 C12-A）：降级重跑前先问「API 腿是否可能
            # 已经把请求发出去了」。插桩面只有 CallApi 通用 POST 的 request_seq；
            # 自定义 `_perform_api` 腿（下方 `_perform_via_api` 的现成分支判据，
            # :426-427 同源）走插件自有传输（demo_api 等）——CallApi 在场而 seq 恒不动
            # ≠「未发出」（磁盘实锤：SelectCustomerTask read_only=False + tasks.py:186
            # 请求发出后才 raise NIE），故对写任务按 attempted=True fail-closed；
            # 读语义 API 腿由 Task **显式声明** `api_leg_side_effect_free`（选项 (a)，
            # 评审 C12-A 拍板；声明位归 Task/插件，引擎不猜——红线 #9/#11）。
            seq_before = actor.api.request_seq if CallApi in actor.abilities else None
            custom_leg = bool(getattr(task, "supports_api", False)
                              and hasattr(task, "_perform_api"))
            try:
                return self._perform_via_api(actor, task)
            except (NotImplementedError, RuntimeError) as exc:
                # API 路径未实现或失败：auto 降级 UI，api 直接抛
                if task.metadata.channel == Channel.API:
                    raise
                if custom_leg:
                    if getattr(task, "api_leg_side_effect_free", False):
                        attempted, leg_note = False, ""
                    else:
                        # fail-closed：声明缺省 = 传输面未知，写任务按「已发出」计
                        attempted = True
                        leg_note = "（自定义 API 腿，传输不经 CallApi，无法证明未发出）"
                elif seq_before is None:
                    # 无插桩面：无法证明没发出，就视为已发出（§2 fail-closed 分流）
                    attempted, leg_note = True, ""
                else:
                    attempted = actor.api.request_seq != seq_before
                    leg_note = "" if not attempted else f"（seq {seq_before}->{actor.api.request_seq}）"
                if not delivery.safe_to_auto_reexecute(
                        read_only=task.metadata.read_only, attempted=attempted, exc=exc):
                    raise DeliveryUncertain(
                        f"投递不确定：API 尝试已发出请求{leg_note}且任务非只读，"
                        f"禁止自动降级重跑；结果请人工核实后再决定是否重放："
                        f"cause={type(exc).__name__}") from exc   # 原异常作 __cause__ 保留（#90）
                # 降级 UI 时不静默吞掉，记入 ctx 供调试（C4②：reason 走 failures 单一真相
                # 的净化摘要——旧实现裸 `str(exc)` 是净化进不来的死角，API 失败文本常带
                # 回调 URL 的 ticket/code。用 exception_brief 而非 exception_message：
                # 该字段既有形态是裸消息体（runs.jsonl 已落盘的取值形态不改，设计 C4 §4
                # 「形态不变」），加类型名会翻新存量断言。
                actor.ctx.setdefault("_auto_degraded_to_ui", []).append(
                    {"task": task.name, "reason": exception_brief(exc)[:200]})
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

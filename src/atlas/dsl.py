"""线性 DSL 编译为 Task 序列；数据条件和 API/UI 混用留在资产层。"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Callable
import yaml
from .core import Channel, FunctionalTask, Task, TaskMetadata
from .failures import CONTROL_FLOW, EnvBlocked
from .probes import DataProvider, DataRequirement

_PATTERN = re.compile(r"\$\{([^}]+)\}")
_ASSERT_EXPR = re.compile(r"^(.*?)\s*(==|!=|>=|<=|>|<)\s*(.*)$")
# 路径形态操作数：字母/下划线开头且至少一个点（source_order.ptx）。
# "3.14" 数字开头不算、"NET30" 无点不算——都是合法字面量。
_PATH_OPERAND = re.compile(r"[A-Za-z_]\w*(?:\.\w+)+")
_ASSERT_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
               ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
               ">": lambda a, b: a > b, "<": lambda a, b: a < b}

def interpolate(value: Any, ctx: dict) -> Any:
    if isinstance(value, dict):
        return {key: interpolate(val, ctx) for key, val in value.items()}
    if isinstance(value, list):
        return [interpolate(item, ctx) for item in value]
    if not isinstance(value, str):
        return value
    match = _PATTERN.fullmatch(value)
    if match:
        return _lookup(ctx, match.group(1))
    return _PATTERN.sub(lambda m: str(_lookup(ctx, m.group(1))), value)

def _lookup(ctx: dict, path: str) -> Any:
    """按 '.' 逐段解析 ctx 路径。失败仍抛 KeyError：
    _resolve_operand 依赖 KeyError/AttributeError/TypeError 做「路径→字面量」回退，
    类型不能改。统一成带路径语境的 KeyError，使大型/导入 flow 里写错的插值表达式
    可归因——指出整条路径与卡住的段（裸 KeyError('pt') 在多步 flow 里无法定位是哪条）。"""
    value: Any = ctx
    for index, key in enumerate(path.split(".")):
        try:
            value = value[key] if isinstance(value, dict) else getattr(value, key)
        except (KeyError, AttributeError, TypeError) as exc:
            raise KeyError(
                f"插值路径 '{path}' 在第 {index + 1} 段 '{key}' 处无法解析：{exc}") from exc
    return value

class TaskRegistry:
    def __init__(self):
        self._tasks: dict[str, Callable[..., Task]] = {}

    def register(self, name: str, factory: Callable[..., Task]) -> None:
        self._tasks[name] = factory

    def create(self, name: str, **kwargs) -> Task:
        if name not in self._tasks:
            raise KeyError(f"Task 未注册：{name}")
        return self._tasks[name](**kwargs)

class FlowCompiler:
    def __init__(self, registry: TaskRegistry, provider: DataProvider | None = None):
        self.registry, self.provider = registry, provider

    def load(self, path: str | Path) -> dict:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
            if isinstance(raw, dict) and "initial" in raw and "states" in raw:
                raise ValueError(f"{path} 是状态机文件（initial/states），请用 atlas machine 执行")
            raise ValueError("DSL 至少需要 steps 列表")
        return raw

    def compile(self, flow: dict) -> list[Task]:
        tasks: list[Task] = []
        for index, step in enumerate(flow["steps"], 1):
            if "task" not in step:
                raise ValueError(f"步骤 {index} 缺少 task")
            args = {key: value for key, value in step.items()
                    if key not in {"task", "channel", "intent", "requires", "read_only",
                                   "on_env_blocked", "id"}}
            task = self.registry.create(step["task"], **args)
            metadata = task.metadata
            if "channel" in step:
                metadata = TaskMetadata(domain=metadata.domain, intent=step.get("intent", metadata.intent),
                    requires=metadata.requires, produces=metadata.produces,
                    channel=Channel(step["channel"]), ui_fallback=metadata.ui_fallback,
                    api_operation=metadata.api_operation, read_only=metadata.read_only, tags=metadata.tags)
                task = FunctionalTask(metadata, lambda actor, original=task: original.perform(actor), task.name)
            on_env_blocked = step.get("on_env_blocked")
            if on_env_blocked:
                if on_env_blocked not in {"skip", "stop"}:
                    raise ValueError(f"步骤 {index} on_env_blocked 只支持 skip/stop")
                task = _GuardedTask(task, on_env_blocked)
            tasks.append(task)
        self._validate_asserts(flow)
        return tasks

    def _validate_asserts(self, flow: dict) -> None:
        """提前验证 assert 块的 task 引用已注册且参数合法，早失败而非 run 时才暴露。"""
        for index, item in enumerate(flow.get("assert") or [], 1):
            if isinstance(item, dict) and "task" in item:
                try:
                    self.registry.create(item["task"], **{k: v for k, v in item.items() if k != "task"})
                except KeyError as exc:
                    raise KeyError(f"assert 块第 {index} 条引用未注册的 Task：{item['task']}") from exc

    @staticmethod
    def _check_supply_targets(requires: dict) -> None:
        """#113 面C：两条 requires 的落点键（as 或条目名）相同 → 显式报错点名两条条目。
        探针实锤旧实现静默 last-wins：order_a(SalesOrder) 的数据被 order_b(PurchaseOrder)
        整条覆盖，下游读到 PO9 以为是 SO1 继续跑——与 #84 多生产者同产出键同红线
        （引擎无法判定两份数据等价，静默择一把一条供给翻成另一条）。
        单一来源校验：provision 执行前与 validate_flow（atlas compile / run 登录前）共用，
        杜绝「compile 说能跑、provision 静默覆盖」（#97 口径）。"""
        seen: dict[str, str] = {}
        for name, raw in (requires or {}).items():
            if not isinstance(raw, dict):
                continue  # 非映射交给 from_dict 点名「必须是映射」
            target = str(raw.get("as") or "") or name
            if target in seen:
                raise ValueError(
                    f"requires.{name} 与 requires.{seen[target]} 的数据落点同为 {target!r}："
                    f"静默后者覆盖前者，下游读到的是被遮蔽的数据。改 as 区分落点或删除一条")
            seen[target] = name

    def provision(self, flow: dict, actor) -> dict[str, dict]:
        if not self.provider:
            if flow.get("requires"):
                raise RuntimeError("Flow 声明了 requires，但未配置 DataProvider")
            return {}
        self._check_supply_targets(flow.get("requires"))
        result = {}
        for name, raw in flow.get("requires", {}).items():
            self._check_data_policy(name, raw)
            requirement = DataRequirement.from_dict(raw)
            try:
                supplied = self.provider.supply(requirement)
            except EnvBlocked:
                if raw.get("on_env_blocked") != "skip":
                    raise
                actor.ctx.setdefault("_data_skipped", []).append(name)
                continue
            key = requirement.as_ or name
            actor.ctx[key] = supplied.data
            result[key] = supplied.data
            actor.ctx.setdefault("_data_actions", {})[key] = supplied.action
        return result

    @staticmethod
    def _check_data_policy(name: str, raw) -> None:
        # from_dict 已拒非法 on_env_blocked，但报错不含是哪个 requires 键——多条件 flow 里
        # 「on_env_blocked 只支持 skip/stop」不带名字，人还得回头猜哪条写错。这里点名补归因。
        # 非映射 raw 交给 from_dict 报「必须是映射」（这里 raw.get 会裸 AttributeError）。
        if not isinstance(raw, dict):
            return
        policy = raw.get("on_env_blocked", "stop")
        if policy not in {"skip", "stop"}:
            raise ValueError(f"requires.{name} 的 on_env_blocked 只支持 skip/stop，收到 {policy!r}")

    def validate_flow(self, flow: dict) -> list[Task]:
        """结构校验的单一来源（compile 与 run 共用，杜绝「compile 说能跑、run 登录一半炸」）：
        步骤 task 引用/形态、channel/on_env_blocked、assert 块、requires 数据供给块。
        全离线、零副作用。旧实现两处缺口：① `atlas compile` 注释声称「数据供给字段在
        compile 期就报错」，但 compile() 从不碰 requires——坏供给块要到 `atlas run` 登录+
        开浏览器后的 provision() 才炸（违反自家 fail-fast 文档口径）；② run 路径登录前只做
        load()，步骤缺 task/引用不存在的 Task/非映射步骤都登录+开浏览器后才裸 KeyError/
        AttributeError。"""
        steps = flow.get("steps", [])
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                raise ValueError(f"步骤 {index} 必须是映射，收到 {type(step).__name__}：{step!r}")
        flow_policy = flow.get("on_env_blocked", "stop")
        if flow_policy not in {"skip", "stop"}:
            raise ValueError(f"flow 级 on_env_blocked 只支持 skip/stop，得到 {flow_policy!r}")
        tasks = self.compile(flow)
        self._check_supply_targets(flow.get("requires"))   # #113 面C2：落点冲突登录前点名
        for name, raw in (flow.get("requires") or {}).items():
            self._check_data_policy(name, raw)
            DataRequirement.from_dict(raw)   # 复用 #94 的形态/entity 校验，登录前当场点名
        return tasks

    def run(self, flow: dict, actor) -> dict:
        engine = None
        self.provision(flow, actor)
        if flow.get("engine"):
            from .engine import ExecutionEngine
            from .recorder import RunRecorder
            config = flow["engine"]
            recorder = RunRecorder(flow.get("id", "flow"), config.env,
                                   config.reports_dir / "runs.jsonl", config.mode)
            engine = ExecutionEngine(config, recorder=recorder, session=getattr(actor, "session", None),
                                     contracts=getattr(actor, "contracts", None),
                                     knowledge=getattr(actor, "knowledge", None),
                                     healer=getattr(actor, "healer", None),
                                     journal=getattr(actor, "journal", None),
                                     evidence=getattr(actor, "evidence", None),
                                     case_id=flow.get("id", "flow"))
        flow_policy = flow.get("on_env_blocked", "stop")
        if flow_policy not in {"skip", "stop"}:
            raise ValueError(f"flow 级 on_env_blocked 只支持 skip/stop，得到 {flow_policy!r}")
        status = "passed"
        try:
            for task in self.compile(flow):
                bound = FunctionalTask(task.metadata,
                    lambda a, original=task: _perform_interpolated(original, a), task.name)
                if engine:
                    engine.execute(actor, bound)
                else:
                    actor.attempts_to(bound)
            self._run_asserts(flow, actor, engine)
        except CONTROL_FLOW:
            # 操作者 Ctrl-C / 显式退出 / 生成器关闭不是产品或用例失败：如实记 interrupted，
            # 避免 atlas summary 把「中断的跑」计进 failed 污染 pass/fail 真相（红线 #7 环境/操作
            # 与缺陷分开）。#129：判据收敛 failures.CONTROL_FLOW 单一真相——旧实现硬编码
            # (KeyboardInterrupt, SystemExit) 漏 GeneratorExit（生成器侧终止信号），与 agent 同一
            # 「改一漏一」缺陷（engine #114 已收敛，dsl/agent 是漏网两处，#92/#114 同红线）。
            # 仍原样向上抛，退出码/中断语义不变。
            status = "interrupted"
            raise
        except EnvBlocked as exc:
            status = "env_blocked"
            actor.ctx.setdefault("_env_blocked", str(exc))
            if flow_policy == "skip":
                actor.ctx.setdefault("_flow_skipped", True)
            else:
                raise
        except BaseException:
            status = "failed"
            raise
        finally:
            if engine:
                engine.recorder.close(status)
        return {"status": status, "steps": [r.task for r in engine.results] if engine else []}

    def _run_asserts(self, flow: dict, actor, engine=None) -> None:
        """执行 flow 的 assert 块：表达式断言 + 内联 Task 断言。

        方案 3.3 承诺的 assert 块支持两种形式：
          - 字符串 "pt_echo == source_order.pt"：两侧用 interpolate 解析后比较
          - dict 带 task 键：编译成 Task 执行（如 assert_api）
          - dict 不带 task 键：键值断言，逐条比较 ctx 值
        """
        asserts = flow.get("assert")
        if not asserts:
            return
        if not isinstance(asserts, list):
            raise ValueError("assert 块必须是列表")
        for index, item in enumerate(asserts, 1):
            if isinstance(item, str):
                self._assert_expression(item, actor, index)
            elif isinstance(item, dict):
                if "task" in item:
                    task = self.registry.create(item["task"], **{
                        k: v for k, v in item.items() if k != "task"})
                    bound = FunctionalTask(task.metadata,
                        lambda a, original=task: _perform_interpolated(original, a), task.name)
                    if engine:
                        engine.execute(actor, bound)
                    else:
                        actor.attempts_to(bound)
                else:
                    for key, expected in item.items():
                        actual = interpolate(f"${{{key}}}", actor.ctx)
                        expected = interpolate(expected, actor.ctx)
                        if actual != expected:
                            raise AssertionError(
                                f"assert 块第 {index} 条：{key} 期望 {expected!r}，实际 {actual!r}")
            else:
                raise ValueError(f"assert 块第 {index} 条只支持字符串或 dict")

    def _assert_expression(self, expr: str, actor, index: int) -> None:
        """解析 a OP b 表达式，两侧用 ctx 插值后比较。

        支持 ==、!=、>=、<=、>、<。两侧先尝试当 ctx 路径解析，失败则当字面量。
        """
        m = _ASSERT_EXPR.match(expr)
        if not m:
            raise ValueError(f"assert 块第 {index} 条表达式缺少比较运算符：{expr!r}")
        left_str, op, right_str = m.group(1).strip(), m.group(2), m.group(3).strip()
        left_value = self._resolve_operand(left_str, actor, index)
        right_value = self._resolve_operand(right_str, actor, index)
        try:
            ok = _ASSERT_OPS[op](left_value, right_value)
        except TypeError as exc:
            raise AssertionError(
                f"assert 块第 {index} 条：{left_str}({left_value!r}) {op} {right_str}({right_value!r}) 类型不可比较：{exc}")
        if not ok:
            raise AssertionError(
                f"assert 块第 {index} 条：{left_str}({left_value!r}) {op} {right_str}({right_value!r}) 不成立")

    @staticmethod
    def _resolve_operand(operand: str, actor, index: int):
        """先当 ctx 路径解析，失败则当字面量（数字转 int/float，否则字符串）。

        #113 面A：路径形态操作数（word.word）解析失败不得静默回落成字符串字面量——
        探针实锤 `source_order.ptx != source_order.ptx2` 两个打错的路径被当成互不相等的
        字符串比较，断言恒真通过（假绿零验证，写错的断言比没有断言更危险——它声称验证过
        而什么都没验）。字面量兼容面保留：无点单词（NET30/SO1）与数字（3.14 数字开头
        不匹配路径形态）照常回落。
        #113 面B：true/false/none/null 识别为 bool/None 字面量——YAML 作者自然写
        `ok == true`，旧实现 True 与字符串 'true' 比较，ctx 真为 True 反而断言失败
        （假阴性：正确断言被拒，人被迫改成删除该断言——比假绿更常被真实遭遇）。
        """
        if _PATH_OPERAND.fullmatch(operand):
            try:
                return _lookup(actor.ctx, operand)
            except KeyError as exc:
                raise AssertionError(
                    f"assert 块第 {index} 条：操作数 {operand!r} 形如 ctx 路径但无法解析——"
                    f"要么补全该步骤产出、要么改为带引号的字面量：{exc}") from exc
        try:
            return _lookup(actor.ctx, operand)
        except (KeyError, AttributeError, TypeError):
            pass
        # 带引号字面量（'NET30.PT' / "true"）：去掉包裹引号按字符串比较——
        # 报错口径「改为带引号的字面量」必须真能用，否则引号进值永不相等。
        if len(operand) >= 2 and operand[0] == operand[-1] and operand[0] in "\"'":
            return operand[1:-1]
        low = operand.lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("none", "null"):
            return None
        try:
            return int(operand)
        except ValueError:
            pass
        try:
            return float(operand)
        except ValueError:
            return operand

class _GuardedTask(Task):
    """把 on_env_blocked 策略从 YAML 编译进 Task，不改动业务 Task 本体。"""
    def __init__(self, inner: Task, policy: str):
        self.inner = inner
        self.policy = policy
        self.metadata = inner.metadata

    @property
    def name(self) -> str:
        return self.inner.name

    def perform(self, actor):
        try:
            return self.inner.perform(actor)
        except EnvBlocked:
            if self.policy == "skip":
                actor.ctx.setdefault("_steps_skipped", []).append(self.name)
                return None
            raise


def _perform_interpolated(task: Task, actor) -> Any:
    if hasattr(task, "__dict__"):
        original = dict(task.__dict__)
        try:
            for key, value in original.items():
                if key != "metadata":
                    setattr(task, key, interpolate(value, actor.ctx))
            return task.perform(actor)
        finally:
            for key, value in original.items():
                setattr(task, key, value)
    return task.perform(actor)

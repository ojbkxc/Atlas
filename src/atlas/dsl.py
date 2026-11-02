"""线性 DSL 编译为 Task 序列；数据条件和 API/UI 混用留在资产层。"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Callable
import yaml
from .core import Channel, FunctionalTask, Task, TaskMetadata
from .failures import EnvBlocked
from .probes import DataProvider, DataRequirement

_PATTERN = re.compile(r"\$\{([^}]+)\}")
_ASSERT_EXPR = re.compile(r"^(.*?)\s*(==|!=|>=|<=|>|<)\s*(.*)$")
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
    value: Any = ctx
    for key in path.split("."):
        value = value[key] if isinstance(value, dict) else getattr(value, key)
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

    def provision(self, flow: dict, actor) -> dict[str, dict]:
        if not self.provider:
            if flow.get("requires"):
                raise RuntimeError("Flow 声明了 requires，但未配置 DataProvider")
            return {}
        result = {}
        for name, raw in flow.get("requires", {}).items():
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
                                     evidence=getattr(actor, "evidence", None))
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
        """先当 ctx 路径解析，失败则当字面量（数字转 int/float，否则字符串）。"""
        try:
            return _lookup(actor.ctx, operand)
        except (KeyError, AttributeError, TypeError):
            pass
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

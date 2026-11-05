"""纯 API Task 与通用 UI Task：业务无关，供新插件复用。"""
from __future__ import annotations
from .core import Channel, Task, TaskMetadata

class CallEndpoint(Task):
    def __init__(self, path: str, *, method: str = "POST", body=None, save_as: str = "response",
                 domain: str = "api", intent: str = "调用接口", requires=(), read_only=True,
                 headers: dict | None = None):
        self.path, self.method, self.body = path, method.upper(), body
        self.headers = headers or None
        self.save_as = save_as
        self.metadata = TaskMetadata(domain=domain, intent=intent, requires=tuple(requires),
            produces=(save_as,), channel=Channel.API, api_operation=path, read_only=read_only)

    def perform(self, actor):
        # 按产出键包装返回：元数据声明与 perform 实写必须一致（write_produces 强制）。
        # 旧实现返回裸响应 dict、靠引擎兜底把「整响应」塞进 save_as 键——键对不上时
        # 静默漂移。ctx 最终形态不变（save_as → 整个响应），但契约由 Task 自己兑现。
        return {self.save_as: actor.api.request(self.method, self.path, body=self.body,
                                                headers=self.headers)}

class UiAction(Task):
    def __init__(self, action, *, domain: str, intent: str, requires=(), produces=(), read_only=False):
        self.action = action
        self.metadata = TaskMetadata(domain=domain, intent=intent, requires=tuple(requires),
            produces=tuple(produces), channel=Channel.UI, read_only=read_only)

    def perform(self, actor):
        return self.action(actor.page, actor.ctx)

class AssertApi(Task):
    """UI 操作后按接口断言真实落库值，绕开 DOM 与 Vue 双模型干扰。

    expect 的键支持点路径（如 data.0.paymentTerms），与接口响应结构对应。
    """
    def __init__(self, operation: str, args: dict | None = None, expect: dict | None = None,
                 save_as: str = "api_assertion", read_only: bool = True):
        if not expect:
            raise ValueError("assert_api 至少需要一个 expect 条件")
        self.operation, self.params, self.expect = operation, args or {}, expect
        # #115 面A：save_as 要能兑现就得先存成实例属性——旧 __init__ 只把它塞进
        # produces=(save_as,) 从不 self.save_as，perform 无从读它（旧硬编码 {"api_assertion":...}
        # 恰与默认值同名，掩盖了这个半成品旋钮）。
        self.save_as = save_as
        self.metadata = TaskMetadata(domain="demo", intent=f"API 断言 {operation}",
            produces=(save_as,), channel=Channel.API, api_operation=operation, read_only=read_only)

    @property
    def name(self) -> str:
        return f"assert_api:{self.operation}"

    def perform(self, actor):
        api = getattr(actor, "demo_api", None)
        if api is None:
            raise RuntimeError("assert_api 需要 DemoRuntime 装配 DemoApi")
        if self.operation not in getattr(api, "atlas_allowed_operations", set()):
            raise PermissionError(f"未声明允许的 DemoApi 只读操作：{self.operation}")
        response = getattr(api, self.operation)(**self.params)
        if not response.get("ok"):
            raise AssertionError(f"API 断言前置失败：{self.operation} HTTP {response.get('status')}")
        for path, expected in self.expect.items():
            actual = _dig(response.get("data", {}), path)
            if actual != expected:
                raise AssertionError(f"API 断言失败 {self.operation} {path}：期望 {expected!r}，实际 {actual!r}")
        # #115 面A：save_as 必须真兑现——旧实现 produces=(save_as,) 但 perform 硬编码返回
        # {"api_assertion": True}，save_as="my_key" 只改了声明不改进产出（假旋钮）：DSL step
        # 透传 save_as（dsl.py:75 排除集不含它），到 write_produces 才 KeyError 点破「声明键
        # 与实际键不符」——构造期给的可调参数与实写漂移，与同文件 CallEndpoint（save_as 真生效）
        # 不一致。改按声明键返回，produces 自兑现（#92 契约同源）。
        return {self.save_as: True}

def _dig(value, path: str):
    # #115 面B：路径打错必须当场点名整条路径与失败段，且四类失败（dict 缺键 / list 越界 /
    # 非数字索引 / 标量中间节点）归因统一——旧实现三种裸抛 Python 原生异常（KeyError/IndexError/
    # int() ValueError），不带路径上下文，classify 分别落 unknown/unknown/contract：同为「expect
    # 路径写错」却落进不同缺陷类，atlas summary 缺陷分布无法归因（#58「查无归 data」、#61/#113
    # 路径点名同红线）。全部收敛成带整条 path 与失败段 part 的 AssertionError（含「不存在」→ 稳定
    # 归 data：响应里没有该数据字段是数据形状事实，不是契约违规也不是无法归因）。
    parts = str(path).split(".")
    for i, part in enumerate(parts):
        prefix = ".".join(parts[:i])
        if isinstance(value, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                raise AssertionError(
                    f"API 断言路径不存在：{path}（段 {i} {part!r} 不是列表索引，{prefix or '根'} 是 {type(value).__name__}）") from None
            try:
                value = value[idx]
            except IndexError:
                raise AssertionError(
                    f"API 断言路径不存在：{path}（索引 {idx} 越界，{prefix or '根'} 长 {len(value)}）") from None
        elif isinstance(value, dict):
            if part not in value:
                raise AssertionError(
                    f"API 断言路径不存在：{path}（字段 {part!r} 缺失，{prefix or '根'} 是 dict）")
            value = value[part]
        else:
            raise AssertionError(
                f"API 断言路径不存在：{path}（段 {i} {part!r} 处是标量 {type(value).__name__}）")
    return value

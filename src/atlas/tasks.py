"""纯 API Task 与通用 UI Task：业务无关，供新插件复用。"""
from __future__ import annotations
from .core import Channel, Task, TaskMetadata

class CallEndpoint(Task):
    def __init__(self, path: str, *, method: str = "POST", body=None, save_as: str = "response",
                 domain: str = "api", intent: str = "调用接口", requires=(), read_only=True):
        self.path, self.method, self.body = path, method.upper(), body
        self.metadata = TaskMetadata(domain=domain, intent=intent, requires=tuple(requires),
            produces=(save_as,), channel=Channel.API, api_operation=path, read_only=read_only)

    def perform(self, actor):
        return actor.api.request(self.method, self.path, body=self.body)

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
        return {"api_assertion": True}

def _dig(value, path: str):
    for part in str(path).split("."):
        if isinstance(value, (list, tuple)):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise AssertionError(f"API 响应路径不存在：{path}")
    return value

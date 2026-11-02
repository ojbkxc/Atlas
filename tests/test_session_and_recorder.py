from atlas.abilities import CallApi
from atlas.actor import Actor
from atlas.recorder import RunRecorder


def test_api_bakes_only_matching_domain_cookies():
    class Context:
        def cookies(self):
            return [{"name": "a", "value": "secret", "domain": ".example.com"},
                    {"name": "b", "value": "other", "domain": ".third-party.com"}]
    api = CallApi("https://demo.example.com")
    api.bake_from_playwright(Context())
    assert api.session.cookies.get("a") == "secret"
    assert api.session.cookies.get("b") is None


def test_actor_missing_ability_is_explicit():
    import pytest
    with pytest.raises(RuntimeError, match="CallApi"):
        Actor().using(CallApi)


def test_run_recorder_records_no_business_payload(tmp_path):
    recorder = RunRecorder("demo", "sit", tmp_path / "runs.jsonl")
    with recorder.step("1"):
        pass
    path = recorder.close()
    assert '"ok": true' in path.read_text(encoding="utf-8")


def test_run_recorder_close_is_idempotent(tmp_path):
    """重复 close 不覆盖已有记录。"""
    import json
    recorder = RunRecorder("demo", "sit", tmp_path / "runs.jsonl")
    recorder.close("passed")
    recorder.close("failed")
    lines = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "passed"


def test_run_recorder_step_records_fail_class_on_exception(tmp_path):
    """step 上下文内抛异常时记录 fail_class。"""
    import json
    recorder = RunRecorder("demo", "sit", tmp_path / "runs.jsonl")
    try:
        with recorder.step("save"):
            raise TimeoutError("超时")
    except TimeoutError:
        pass
    recorder.close("failed")
    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8"))
    assert record["steps"][0]["ok"] is False
    assert record["steps"][0]["fail_class"] == "interaction"


def test_call_api_post_and_get_delegate_to_request(monkeypatch):
    """post/get 是 request 的快捷方法，透传 method。"""
    calls = []
    def mock_request(self, method, path, **kw):
        calls.append((method, path, kw))
        return {"ok": True}
    monkeypatch.setattr(CallApi, "request", mock_request)
    api = CallApi("http://test")
    api.post("/api/test", body={"k": "v"})
    assert calls[0] == ("POST", "/api/test", {"body": {"k": "v"}})
    api.get("/api/test")
    assert calls[1] == ("GET", "/api/test", {})


def test_actor_bake_session_delegates_to_api():
    """bake_session 从 page.context 取 Cookie 同步给 API。"""
    from atlas.abilities import BrowseTheWeb
    from atlas.actor import Actor
    class FakeContext:
        def cookies(self):
            return []
    class FakePage:
        context = FakeContext()
    actor = Actor(abilities=[BrowseTheWeb(FakePage()), CallApi("http://test")])
    actor.bake_session()  # 不抛即通过


def test_session_observe_returns_none_in_run_mode(tmp_path):
    """run 模式下 observe 不截图，返回 None。"""
    from atlas.session import BrowserSession
    from atlas.config import AtlasConfig
    config = AtlasConfig(tmp_path, mode="run")
    session = BrowserSession.__new__(BrowserSession)
    session.config = config
    session.page = None
    assert session.observe("step") is None


def test_keepalive_triggers_on_auth_expired():
    """会话保活：检测到 auth_expired 响应时自动触发回调重新烘焙 Cookie。"""
    api = CallApi("https://demo.example.com")
    triggered = []
    api.set_keepalive(lambda: triggered.append(True))
    # 模拟上一个响应是 auth_expired
    from atlas.abilities import ApiResponse
    api.last_response = ApiResponse(ok=False, data={"auth_expired": True})
    assert api.ensure_session() is True
    assert triggered == [True]


def test_keepalive_no_trigger_when_healthy():
    api = CallApi("https://demo.example.com")
    triggered = []
    api.set_keepalive(lambda: triggered.append(True))
    from atlas.abilities import ApiResponse
    api.last_response = ApiResponse(ok=True, data={})
    assert api.ensure_session() is False
    assert triggered == []


def test_keepalive_interval_triggers_proactively():
    """间隔保活：超过 interval 主动触发，不等 auth_expired。"""
    import time
    api = CallApi("https://demo.example.com")
    triggered = []
    api.set_keepalive(lambda: triggered.append(True), interval=0.01)
    time.sleep(0.02)
    assert api.ensure_session() is True
    assert triggered == [True]


def test_keepalive_without_callback_is_noop():
    api = CallApi("https://demo.example.com")
    assert api.ensure_session() is False


def test_demo_runtime_wait_topbar_fast_path_and_reload_path():
    """顶栏兜底：已渲染时零开销返回；未渲染时 reload 重试，2 次后仍不行明确报错。"""
    from atlas.runtime import DemoRuntime

    class FakePage:
        def __init__(self, fail_times):
            self.fail_times = fail_times
            self.reloads = 0
            self.url = "https://sit.example.com/app"
        def wait_for_selector(self, _sel, state=None, timeout=None):
            if self.fail_times > 0:
                self.fail_times -= 1
                raise TimeoutError("topbar not rendered")
        def reload(self, **_kw):
            self.reloads += 1

    class FakeLoginModule:
        LOCATORS = {"login_indicator": ".app-topbar"}

    # 快路径：顶栏已渲染，无 reload
    rt = DemoRuntime.__new__(DemoRuntime)
    rt.actor = None
    page_ok = FakePage(0)
    rt.actor = type("A", (), {"page": page_ok})()
    rt._wait_topbar(FakeLoginModule)
    assert page_ok.reloads == 0

    # reload 路径：初次超时 + reload 后一次超时 → 共 2 轮 reload 后出现
    page_slow = FakePage(2)
    rt.actor = type("A", (), {"page": page_slow})()
    rt._wait_topbar(FakeLoginModule)
    assert page_slow.reloads == 2

    # 彻底失败：明确报错而非静默继续
    import pytest
    page_dead = FakePage(99)
    rt.actor = type("A", (), {"page": page_dead})()
    with pytest.raises(RuntimeError, match="顶栏指示器"):
        rt._wait_topbar(FakeLoginModule)

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


def test_bake_rejects_bare_tld_but_keeps_localhost_and_parent():
    """安全边界：domain=com/org 这类裸 TLD 不得烘焙——否则会被存进 session 并在任意 *.com
    请求（含绝对 URL api_call/跨域保活）上发出，跨站泄露凭据。同时钉死合法用例不误伤：
    精确 host（含离线 localhost）、含点的父域照常烘焙；不可解析 base_url 宁漏不外泄。"""
    from atlas.abilities import CallApi
    class Ctx:
        def __init__(self, cookies): self._c = cookies
        def cookies(self): return self._c
    api = CallApi("https://app.example.com")
    api.bake_from_playwright(Ctx([
        {"name": "sso", "value": "secret", "domain": "com"},           # 裸 TLD → 必须拒
        {"name": "org", "value": "v", "domain": "example.com"},        # 合法父域 → 保留
        {"name": "host", "value": "v", "domain": "app.example.com"},  # 精确 host → 保留
        {"name": "evil", "value": "v", "domain": "evil.com"},          # 无关域 → 拒
    ]))
    assert {c.name for c in api.session.cookies} == {"org", "host"}    # sso/evil 未泄漏进 session

    lo = CallApi("http://localhost:8080")                               # 离线 host cookie 回归护栏
    lo.bake_from_playwright(Ctx([{"name": "l", "value": "x", "domain": "localhost"}]))
    assert {c.name for c in lo.session.cookies} == {"l"}

    bad = CallApi("")                                                   # 不可解析 base_url → 不烘焙
    bad.bake_from_playwright(Ctx([{"name": "a", "value": "v", "domain": "example.com"}]))
    assert list(bad.session.cookies) == []


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


def test_recorder_step_classifier_crash_never_falsifies_ok(tmp_path):
    """#126 面A：业务插件自定义异常的 __str__ 自身抛错（引用未初始化属性——真实可达雷区）
    时，classify_failure 在 except 块里崩：旧实现让 AttributeError 顶掉真因（During handling
    链），且 fail_class 没赋上 → finally `ok = "fail_class" not in item` 把失败步骤记成
    ok=True 假绿（定性器先于被定性对象死 #89 + 坏形态伪装成功 #102，recorder 层同族）。
    修复：定性失败诚实回落 unknown，真因原样传播，失败步骤绝不记成功。"""
    class Nasty(Exception):
        def __str__(self):
            return self.detail   # 从未赋值 → AttributeError

    recorder = RunRecorder("demo", "sit", tmp_path / "runs.jsonl")
    import pytest
    with pytest.raises(Nasty):     # 真因原样传播，不被定性器崩替换
        with recorder.step("下单"):
            raise Nasty()
    assert recorder.steps[0]["ok"] is False
    assert recorder.steps[0]["fail_class"] == "unknown"


def test_recorder_close_survives_unstrable_object(tmp_path):
    """#126 面B：default=str 兜底假设 str() 全函数可用——异常对象 __str__ 自身抛错时
    close 整轮崩、记录落不了盘（违背 #64「不得让整轮证据静默丢失」初衷），且在 flow
    finally 里顶掉用例真因、_closed 不置位重试永远崩。修复：永不抛的序列化兜底，
    坏对象落类型占位符，其余证据照常落盘。"""
    class Bad:
        def __str__(self): raise RuntimeError("str 也崩")
        def __repr__(self): raise RuntimeError("repr 也崩")

    recorder = RunRecorder("demo", "sit", tmp_path / "runs.jsonl")
    with recorder.step("查单") as item:
        item["evidence"] = Bad()       # yield item 公开语义：正文可放任意对象
    path = recorder.close()            # 旧实现这行直接 RuntimeError
    import json
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["steps"][0]["step"] == "查单"          # 整轮证据仍在
    assert "unrepresentable" in record["steps"][0]["evidence"]  # 坏对象落占位符
    assert record["steps"][0]["ok"] is True              # 成功步骤语义不翻错


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


def test_run_recorder_persists_exotic_step_field(tmp_path):
    """非 JSON 字段（Path/set/自定义对象）不得让整轮证据静默丢失：close 必须成功落盘。
    旧实现 json.dumps 在 finally 里抛错，既顶掉真实用例结果，又因 _closed 提前置位永久丢记录。"""
    import json
    from pathlib import Path
    recorder = RunRecorder("demo", "sit", tmp_path / "runs.jsonl")
    with recorder.step("s1") as item:
        item["payload"] = Path("/a/live")
        item["tags"] = {"x", "y"}
    recorder.close("failed")                                  # 不应抛异常
    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and len(record["steps"]) == 1
    assert isinstance(record["steps"][0]["payload"], str) and "live" in record["steps"][0]["payload"]


def test_run_recorder_close_not_latched_on_write_failure(tmp_path):
    """写失败时不得提前置 _closed：否则 close 抛出后再次调用谎报「已关闭」，证据永久丢失。"""
    import pytest
    runs = tmp_path / "runs.jsonl"
    runs.mkdir()                                              # 让目标路径变成目录 → open('a') 触发 OSError
    recorder = RunRecorder("demo", "sit", runs)
    with recorder.step("s"):
        pass
    with pytest.raises(OSError):
        recorder.close()
    assert recorder._closed is False                          # 修复前这里是 True（close 开头就置位）


def test_ensure_session_never_raises_on_keepalive_failure():
    """保活是尽力而为：回调（如长巡检里浏览器 context 已关闭 → cookies() 抛错）失败时
    ensure_session 必须返回 False 而非抛出，遵守自身契约「不抛异常——保活失败由后续请求暴露」。
    且失败不前进 _last_keepalive，留给后续请求重试；正常触发路径不被破坏。"""
    import time
    from atlas.abilities import CallApi

    def dying():
        raise RuntimeError("Target closed")

    api = CallApi("https://x")
    api.set_keepalive(dying, interval=1)
    api._last_keepalive = time.time() - 100                   # 强制间隔到期
    assert api.ensure_session() is False                      # 不抛，返回 False
    assert api._last_keepalive < time.time() - 50             # 未前进 → 可重试

    fired = []
    api2 = CallApi("https://x")
    api2.set_keepalive(lambda: fired.append(1), interval=1)
    api2._last_keepalive = time.time() - 100
    assert api2.ensure_session() is True and fired == [1]     # 正常保活仍触发


def test_request_keeps_good_response_when_keepalive_fails():
    """已拿到成功响应的请求，绝不能被末尾 ensure_session 的后台保活抛错拖成异常
    （request 在 last_response 存好之后才调 ensure_session，line 90）。"""
    import time
    from atlas.abilities import CallApi

    class FakeResp:
        status_code = 200
        ok = True
        url = "https://x/api/list"
        headers = {"Content-Type": "application/json"}
        def json(self):
            return {"resultCode": "BUG-000"}

    class FakeSession:
        headers = {}
        def request(self, *a, **k):
            return FakeResp()

    def dying():
        raise RuntimeError("context died mid-patrol")

    api = CallApi("https://x", session=FakeSession())
    api.set_keepalive(dying, interval=1)
    api._last_keepalive = time.time() - 100
    resp = api.request("GET", "/api/list")                    # 不应抛
    assert resp.ok is True and resp["data"]["resultCode"] == "BUG-000"


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


def test_call_api_request_forwards_headers_to_underlying_session():
    """核心：per-request headers 必须真交到 session.request（ICS 缺 isc-app 头即 BUG-920）。
    只证 kwarg 层不够——这条验的是 abilities.py 那行 session.request(..., headers=...) 落地。"""
    captured = {}
    class FakeResp:
        status_code = 200
        ok = True
        url = "http://test/web/x"
        headers = {"Content-Type": "application/json"}
        def json(self):
            return {"ok": True, "data": {}}
    class FakeSession:
        def __init__(self):
            self.headers = {}
        def request(self, method, url, **kw):
            captured.update(method=method, url=url, **kw)
            return FakeResp()
    api = CallApi("http://test", session=FakeSession())
    api.request("POST", "/web/x", body={"k": "v"}, headers={"isc-app": "iservice"})
    assert captured["headers"] == {"isc-app": "iservice"}
    assert captured["method"] == "POST"
    # 不传 headers 时透传 None，交给 requests 用会话默认头合并
    captured.clear()
    api.request("GET", "/web/y")
    assert captured["headers"] is None


def test_attach_demo_rejects_class_missing_client_protocol(tmp_path):
    """#122 面A：ATLAS_DEMO_API_CLASS 指到「存在但不符合客户端协议」的类（真实模块常
    同时有 IcsApi 与 IcsService）——旧实现到 `api._load_spec(...)` 才裸 AttributeError，
    不提 env 键、冒充框架缺陷（#103 类名不存在延伸：类存在≠可用，#63 注册≠可用同族）。
    现装配前点名 _load_spec 缺失与 ATLAS_DEMO_API_CLASS；符合协议的类正常装配不翻错。"""
    import pytest
    from atlas.abilities import CallApi
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "proj_api.py").write_text(
        "class IcsService:\n"
        "    def query(self): return 1\n\n"
        "class IcsApi:\n"
        "    def _load_spec(self, path): return {'endpoints': ['x']}\n", encoding="utf-8")
    api = CallApi("https://example.com")
    with pytest.raises(ImportError, match="ATLAS_DEMO_API_CLASS"):
        api.attach_demo(tmp_path, module_file="proj_api.py", class_name="IcsService")
    client = api.attach_demo(tmp_path, module_file="proj_api.py", class_name="IcsApi")
    assert client.spec == {"endpoints": ["x"]}


def test_html_login_page_without_content_type_is_auth_expired():
    """#122 面B：200 + 整页 HTML 但网关不带 Content-Type 头——旧实现只看头，判 ok=True
    把登录页伪装成成功（#102 同族假绿）；现 raw+HTML 文档头也拦。对照：合法 JSON 响应
    零误伤；Content-Type: text/html 原判据保留（不把正常路径翻错，#115 教训）。"""
    class NoCTHtmlResp:
        status_code = 200
        ok = True
        url = "https://x/sso/login"
        headers = {}
        text = "<!DOCTYPE html><html><body>登录</body></html>"
        def json(self):
            raise ValueError("no json")
    class S:
        headers = {}
        def request(self, *a, **k): return NoCTHtmlResp()
    r = CallApi("https://x", session=S()).get("/api/list")
    assert r.ok is False and r["data"]["auth_expired"] is True

    class JsonResp:
        status_code = 200
        ok = True
        url = "https://x/api/list"
        headers = {"Content-Type": "application/json"}
        def json(self):
            return {"ok": True, "data": {"rows": []}}
    class S2:
        headers = {}
        def request(self, *a, **k): return JsonResp()
    r2 = CallApi("https://x", session=S2()).get("/api/list")
    assert r2.ok is True                          # 合法 JSON 不误伤


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


def test_session_exit_closes_all_objects_even_when_context_close_raises(tmp_path):
    """#82：__exit__ 必须逐个尽力关闭——context.close() 抛错（浏览器已崩溃）时旧实现直接传播异常，
    跳过 browser.close() 与 _pw.stop() → 浏览器 + Playwright 驱动进程双双泄漏。且此时在用例异常
    上下文中，__exit__ 抛错还会掩埋真实失败。修复后：每个都尝试关闭；有在传播的用例异常时不再抛，
    让原异常照常上抛。"""
    from atlas.session import BrowserSession
    from atlas.config import AtlasConfig
    calls = {"browser": 0, "pw": 0}
    class BoomContext:
        def close(self): raise RuntimeError("context already dead")
    class RecBrowser:
        def close(self): calls["browser"] += 1
    class RecPW:
        def stop(self): calls["pw"] += 1
    s = BrowserSession.__new__(BrowserSession)
    s.config = AtlasConfig(tmp_path, mode="run")
    s.context, s.browser, s._pw = BoomContext(), RecBrowser(), RecPW()
    # 模拟有在传播的用例异常（exc_type=AssertionError）：__exit__ 不得抛（不掩埋原失败）
    assert s.__exit__(AssertionError, AssertionError("real test failure"), None) is None
    assert calls["browser"] == 1 and calls["pw"] == 1     # 关闭链没被 context 异常打断


def test_session_exit_surfaces_cleanup_error_when_no_in_block_exception(tmp_path):
    """#82：正常结束（无用例异常）时清理错误必须显式抛出，不静默泄漏。"""
    import pytest
    from atlas.session import BrowserSession
    from atlas.config import AtlasConfig
    class Boom:
        def close(self): raise RuntimeError("teardown failed")
    s = BrowserSession.__new__(BrowserSession)
    s.config = AtlasConfig(tmp_path, mode="run")
    s.context, s.browser, s._pw = Boom(), None, None
    with pytest.raises(RuntimeError, match="teardown failed"):
        s.__exit__(None, None, None)


def test_session_exit_clean_close_does_not_raise(tmp_path):
    """#82：全部关闭成功时无异常、返回 None（不抑制任何异常）。"""
    from atlas.session import BrowserSession
    from atlas.config import AtlasConfig
    calls = {"n": 0}
    class Ok:
        def close(self): calls["n"] += 1
        def stop(self): calls["n"] += 1
    s = BrowserSession.__new__(BrowserSession)
    s.config = AtlasConfig(tmp_path, mode="run")
    s.context, s.browser, s._pw = Ok(), Ok(), Ok()
    assert s.__exit__(None, None, None) is None
    assert calls["n"] == 3


def test_session_exit_multiple_cleanup_errors_all_surfaced(tmp_path):
    """#123 面1：三环节（context/browser/_pw）连环崩时旧实现 `raise errors[0]` 只报第一个——
    _pw.stop 失败意味着 Playwright node 驱动进程正在泄漏，运维却只看到 context 错误，其余静默
    （#85 清理链无痕同红线；与 #121 面D runtime.__exit__ 完全同构）。修复后：单错原样抛
    （#82 既有口径不翻错）、多错聚合 ExceptionGroup 全点名、有在用例异常仍让位真因（#82 分工）。"""
    import pytest
    from atlas.session import BrowserSession
    from atlas.config import AtlasConfig
    class BoomCtx:
        def close(self): raise OSError("context 关闭失败")
    class BoomBrowser:
        def close(self): raise OSError("浏览器进程失联，关不掉")
    class BoomPW:
        def stop(self): raise OSError("node 驱动 stop 失败——进程正泄漏")
    s = BrowserSession.__new__(BrowserSession)
    s.config = AtlasConfig(tmp_path, mode="run")
    s.context, s.browser, s._pw = BoomCtx(), BoomBrowser(), BoomPW()
    with pytest.raises(ExceptionGroup) as ei:
        s.__exit__(None, None, None)
    assert len(ei.value.exceptions) == 3
    msgs = [str(e) for e in ei.value.exceptions]
    assert any("node 驱动" in m for m in msgs)   # 驱动泄漏错误不再被静默
    assert any("浏览器进程失联" in m for m in msgs)

    # 单错必须原样抛（不裹 ExceptionGroup——#82 既有测试与用户 catch 口径不翻错）
    class Ok:
        def close(self): pass
    s2 = BrowserSession.__new__(BrowserSession)
    s2.config = AtlasConfig(tmp_path, mode="run")
    s2.context, s2.browser, s2._pw = Ok(), Ok(), BoomPW()
    with pytest.raises(OSError, match="node 驱动"):
        s2.__exit__(None, None, None)

    # 有在用例异常时多清理错误也不顶真因（#82 口径保持）
    assert s.__exit__(ValueError, ValueError("用例真因"), None) is None


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


def test_snapshot_evidence_failure_never_flips_verdict(tmp_path):
    """取证（截图/建目录/JS 求值）异常绝不改变用例判定：snapshot/observe 降级返回 None。
    旧实现直接抛，engine 把 capture 放在 try 内 → 已通过的步骤被误判成失败并 raise（红线：成功不得变失败）。"""
    from atlas.config import AtlasConfig
    from atlas.session import BrowserSession

    class DyingPage:
        url = "https://internal-host/x"
        def is_closed(self):
            return False
        def screenshot(self, **kw):
            raise RuntimeError("page crashed")
        def evaluate(self, js):
            raise RuntimeError("navigation raced")

    sess = BrowserSession(AtlasConfig(tmp_path, mode="run"))
    sess.page = DyingPage()
    assert sess.snapshot("step1") is None                 # 截图失败 → None，不抛

    dbg = BrowserSession(AtlasConfig(tmp_path, mode="debug"))
    dbg.page = DyingPage()
    assert dbg.observe("step1") is None                   # evaluate 崩不抛，降级到 snapshot 也 None
    assert dbg.capture("step1", True) is None             # 成功取证路径不因截图崩而抛
    assert dbg.capture("step1", False) is None            # 失败取证路径同理


def test_snapshot_writes_png_and_debug_sidecar_for_healthy_page(tmp_path):
    """健康页面仍照常产出取证（证明防御性改造没有吞掉正常路径）。"""
    from pathlib import Path
    from atlas.config import AtlasConfig
    from atlas.session import BrowserSession

    class OkPage:
        url = "https://internal-host/order"
        def is_closed(self):
            return False
        def screenshot(self, path=None, **kw):
            Path(path).write_bytes(b"\x89PNG")
        def evaluate(self, js):
            return {"component": "Order"}

    sess = BrowserSession(AtlasConfig(tmp_path, mode="debug"))
    sess.page = OkPage()
    p = sess.snapshot("s1")
    assert p and p.exists()
    assert p.with_suffix(".json").exists()                # debug 侧车状态一并写入
    assert sess.capture("s2", True) is not None           # 成功取证仍生成 PNG


# ---- 契约装配：私有 ATLAS_KNOWLEDGE_DIR 覆盖必须生效（接真实项目的关键链路）----

def _runtime_obj():
    from atlas.runtime import DemoRuntime
    return DemoRuntime.__new__(DemoRuntime)              # 跳过 __init__/登录；contracts() 不用实例字段


def test_contracts_honors_private_knowledge_dir(tmp_path, monkeypatch):
    """真实项目用 ATLAS_KNOWLEDGE_DIR 覆盖 contracts/core.json 必须生效；旧实现直读包内
    knowledge_root，私有契约被静默忽略→拿虚构 demo schema 校验真实报文。"""
    import json
    priv = tmp_path / "kb"; (priv / "contracts").mkdir(parents=True)
    (priv / "contracts" / "core.json").write_text(json.dumps({"contracts": [
        {"name": "priv-health", "method": "GET", "path": "/priv/health", "level": "minimal"}]}),
        encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(priv))
    reg = _runtime_obj().contracts()
    assert ("GET", "/priv/health") in reg.contracts
    assert reg.stats().get("minimal") == 1               # 私有整体覆盖该文件：包内 7 条不混入
    assert not any(p.endswith("findEntireCustDetails") for _m, p in reg.contracts)  # 包内 core 被替换


def test_contracts_falls_back_to_package_without_private_override(tmp_path, monkeypatch):
    """未设私有目录：照常加载包内虚构契约（core 1/high 5/minimal 1）。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    reg = _runtime_obj().contracts()
    assert reg.stats().get("core") == 1
    assert ("POST", "/web/legacy-web-mdm/orgCustomerInfo/findEntireCustDetails") in reg.contracts


def test_contracts_falls_back_to_package_when_private_corrupt(tmp_path, monkeypatch):
    """私有契约文件写坏（截断）：跳过回退包内默认，不抛、不静默空表。"""
    priv = tmp_path / "kb"; (priv / "contracts").mkdir(parents=True)
    (priv / "contracts" / "core.json").write_text('{"contracts": [{"name": "x", "lev', encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(priv))
    reg = _runtime_obj().contracts()                     # 不应抛
    assert reg.stats().get("core") == 1


def test_contracts_raises_when_no_contract_file_anywhere(tmp_path, monkeypatch):
    """两级都缺：显式报错，绝不静默返回空表关闭契约校验（契约是断言层，无声空表会掩盖缺陷）。
    解析逻辑现统一在 DemoPlugin.contracts()，用真实子类只改两个根路径来覆盖。"""
    import pytest
    import atlas.runtime as rt
    from atlas.plugins.demo.plugin import DemoPlugin
    empty = tmp_path / "empty"; empty.mkdir()
    class _NoKb(DemoPlugin):
        def __init__(self):
            self.knowledge_root = empty / "pkg"            # 不存在
            self.private_root = empty / "priv"             # 不存在
    monkeypatch.setattr(rt, "resolve_plugin", _NoKb)           # runtime.contracts() 委托 resolve_plugin()
    with pytest.raises(ValueError, match="契约文件"):
        _runtime_obj().contracts()


def test_actor_attempts_to_fail_fast_keeps_prior_produces(tmp_path):
    """#124 探针轮（sound 钉死）：attempts_to 顺序执行契约——半途崩时原异常必须传播（不吞、
    不改写成别的错），且**已完成 Task 的产出留在 ctx 不回滚**。这是「半途副作用可追责」
    （#85 agent 中断留痕 / #110 成功路径 executed 同族）在 actor 层的对应面：前序写 Task
    真实发生过，抹掉它的产出=销毁追责证据。将来若改成 try/finally 整体回滚，本测当场拦截。
    多任务形态现无消费方（agent/dsl/state_machine 全部逐任务单发），钉的是 API 契约防漂移
    （#107 composition 复核先例）。"""
    import pytest
    from atlas.core import Task, TaskMetadata
    order = []
    class FirstTask(Task):
        metadata = TaskMetadata(domain="d", intent="first", produces=("v1",))
        def perform(self, actor):
            order.append("first")
            return {"v1": "第一单"}
    class BoomTask(Task):
        metadata = TaskMetadata(domain="d", intent="boom", produces=("v2",))
        def perform(self, actor):
            order.append("boom")
            raise RuntimeError("第二步崩")
    actor = Actor("T", [])
    with pytest.raises(RuntimeError, match="第二步崩"):
        actor.attempts_to(FirstTask(), BoomTask())
    assert order == ["first", "boom"]          # 顺序执行，不跳过不重排
    assert actor.ctx == {"v1": "第一单"}        # 已完成产出保留——半途副作用追责证据不销毁


def test_bake_rejects_second_level_public_suffix():
    """#141 面A：旧判据 `"." in domain` 只挡裸 TLD（com），挡不住二级公共后缀——
    cookie domain=com.cn 对 host=ics.example.com.cn 命中 endswith(".com.cn") 被烘焙，
    凭据会发向任意他企业 *.com.cn 注册域，违「Cookie 只烘焙到匹配域」（AGENTS §3）。
    现精选公共后缀表拒绝；**合法**二级父域 example.com.cn（含可注册标签）必须照常烘焙
    ——拒绝面翻正常路径即违 #115。"""
    class Ctx:
        def __init__(self, cookies): self._c = cookies
        def cookies(self): return self._c
    api = CallApi("http://ics.example.com.cn")
    api.bake_from_playwright(Ctx([
        {"name": "suffix", "value": "secret", "domain": ".com.cn"},     # 公共后缀 → 拒
        {"name": "parent", "value": "p", "domain": ".example.com.cn"},    # 合法父域 → 保留
        {"name": "exact", "value": "e", "domain": "ics.example.com.cn"},  # 精确 host → 保留
        {"name": "co", "value": "x", "domain": "co.uk"},                # 异后缀表 → 拒（域不相干本就拒）
    ]))
    assert {c.name for c in api.session.cookies} == {"parent", "exact"}


def test_bake_cookie_domain_case_insensitive():
    """#141 面B：域名大小写不敏感（RFC 6265）——真实 SSO 常回大写 Domain 形态，
    旧实现拿原样串与小写 host 比较，永不命中：合法凭据静默丢弃，用户只见 401
    不见根因（fail-safe 方向的正确性洞）。现归一下写比较与存储。"""
    class Ctx:
        def __init__(self, cookies): self._c = cookies
        def cookies(self): return self._c
    api = CallApi("http://app.example.com")
    api.bake_from_playwright(Ctx([
        {"name": "upper", "value": "s3cret", "domain": ".EXAMPLE.COM"},
        {"name": "mixed", "value": "m", "domain": "APP.Example.Com"},
        {"name": "evil", "value": "e", "domain": ".EVIL.COM"},
    ]))
    assert {c.name for c in api.session.cookies} == {"upper", "mixed"}
    assert api.session.cookies.get("upper") == "s3cret"   # 无关大写域仍被拒，不是无脑放行


def test_keepalive_fires_on_plain_401_json():
    """#141 面C：README/CLAUDE 宣称「api 401 自动重登」，旧实现检测只认
    auth_expired 标记——该标记仅在 200+HTML 登录页伪装时置位；裸 401+JSON
    （ICS 过期会话最常见形态）need=False，会话死亡永不重烘焙。现 401 状态码
    为权威判据；403/500/200 不误触发（保守只补承诺面，#115）。"""
    class Resp:
        def __init__(self, status):
            self.status_code, self.ok = status, status < 400
            self.url, self.headers, self.text = "http://api/x", {}, ""
        def json(self): return {"code": "AUTH", "message": "unauthorized"}
    class FakeHttp:
        headers = {}
        def __init__(self, status):
            self._status = status
            class _Jar:
                def __init__(self): self._d = {}
                def set(self, n, v, **kw): self._d[n] = v
                def get(self, n, default=None): return self._d.get(n, default)
                def __iter__(self): return iter(())
            self.cookies = _Jar()
        def request(self, method, url, **kw): return Resp(self._status)
    for status, expect_fired in ((401, True), (403, False), (500, False), (200, False)):
        api = CallApi("http://api.example.com", session=FakeHttp(status))
        fired = []
        api.set_keepalive(lambda: fired.append(1), interval=0)
        api.request("GET", "/whoami")
        assert bool(fired) is expect_fired, f"status={status} fired={fired}"

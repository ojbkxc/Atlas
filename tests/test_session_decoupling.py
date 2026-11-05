from atlas.config import AtlasConfig
from atlas.session import BrowserSession


def test_browser_session_does_not_require_base_url(tmp_path, monkeypatch):
    """浏览器启动与目标站点解耦；缺 URL 不能阻止本地页面/离线冒烟。"""
    for key in ("DEMO_URL_SIT",):
        monkeypatch.delenv(key, raising=False)
    config = AtlasConfig(tmp_path)
    assert config.base_url == ""
    # 只断言不因 base_url 抛错：真正启动浏览器放到真实冒烟脚本里。
    assert BrowserSession(config).page is None


def test_system_browser_executable_is_configurable(monkeypatch, tmp_path):
    """Linux CI 常用 apt 装的 chromium；应能通过环境变量指定系统浏览器，
    从而绕开 Playwright 自带浏览器 CDN 不可达的问题。"""
    import atlas.session as session_module
    captured = {}

    class FakeChromium:
        def launch(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here")

    class FakePW:
        chromium = FakeChromium()

    class FakeStarter:
        def start(self):
            return FakePW()
        def stop(self):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeStarter())
    monkeypatch.setenv("ATLAS_BROWSER_EXECUTABLE", "/usr/bin/chromium")
    config = AtlasConfig(tmp_path)
    try:
        session_module.BrowserSession(config).__enter__()
    except RuntimeError:
        pass
    assert captured.get("executable_path") == "/usr/bin/chromium"
    assert captured.get("channel") is None


def test_enter_partial_failure_closes_driver_and_browser(tmp_path, monkeypatch):
    """#111：`with` 语义下 __enter__ 抛错不触发 __exit__——旧实现 start() 成功后
    launch/new_context/new_page 任一失败都泄漏 Playwright node 驱动（半开 Chromium 同漏）。
    DemoRuntime（runtime.py:70）在赋值 self.session 前抛，外层清理链也够不着——
    每次 executable 指错/浏览器初始化崩都累积一个孤儿进程（#97 同红线，但漏在
    BrowserSession 本体半启动面）。修复后：入口异常路径逐个尽力关闭，且原始真因原样抛出。"""
    import pytest
    import atlas.session as session_module
    events = {"stopped": 0, "closed": []}

    class FakeContext:
        def close(self): events["closed"].append("context")
        def new_page(self): raise RuntimeError("new_page 崩")
    class FakeBrowser:
        def close(self): events["closed"].append("browser")
        def new_context(self, **kw): return FakeContext()
        def launch(self, **kw): return self
    class FakePW:
        def __init__(self): self.chromium = FakeBrowser()
        def start(self): return self
        def stop(self): events["stopped"] += 1
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePW())
    config = AtlasConfig(tmp_path)
    with pytest.raises(RuntimeError, match="new_page 崩"):
        with session_module.BrowserSession(config):
            pass
    assert events["stopped"] == 1                    # 驱动被回收，不留孤儿进程
    assert events["closed"] == ["context", "browser"]  # 半开的 context/browser 逐个尝试关


def test_enter_launch_failure_stops_driver(tmp_path, monkeypatch):
    """#111 探针 A 实锤路径：launch 直接崩（executable 指错/CDN 不可达）时驱动必须 stop——
    真因仍原样抛出（清理失败绝不掩埋，close 链里连抛错也不换异常）。"""
    import pytest
    import atlas.session as session_module
    events = {"stopped": 0}

    class FakePW:
        class chromium:
            @staticmethod
            def launch(**kw): raise OSError("chrome executable not found")
        def start(self): return self
        def stop(self): events["stopped"] += 1
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePW())
    config = AtlasConfig(tmp_path)
    with pytest.raises(OSError, match="not found"):
        session_module.BrowserSession(config).__enter__()
    assert events["stopped"] == 1


def test_enter_cleanup_error_does_not_bury_original(tmp_path, monkeypatch):
    """#111：清理链自身崩（browser.close 抛）也不得顶掉入口真因——#97 口径
    「清理失败绝不掩埋原始异常」，close/stop 逐个 try 吞掉的是清理错误，不是真因。"""
    import pytest
    import atlas.session as session_module
    events = {"stopped": 0}
    class FakeBrowser:
        def close(self): raise RuntimeError("close 也坏了")
        def launch(self, **kw): return self
    class FakePW:
        def __init__(self): self.chromium = FakeBrowser()
        def start(self): return self
        def stop(self): events["stopped"] += 1
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePW())
    config = AtlasConfig(tmp_path)
    # new_context 真因是 AttributeError（FakeBrowser 没这方法）——清理全坏仍传播原始异常
    with pytest.raises(AttributeError):
        session_module.BrowserSession(config).__enter__()
    assert events["stopped"] == 1


def test_snapshot_filenames_do_not_collide(tmp_path):
    """同一 case 多轮重跑时快照带毫秒时间戳，不互相覆盖取证文件。"""
    import time
    from atlas.config import AtlasConfig
    from atlas.session import BrowserSession

    class FakePage:
        is_closed = lambda self: False
        url = "about:blank"
        def screenshot(self, path, **_kw):
            open(path, "wb").write(b"png")

    config = AtlasConfig(tmp_path)
    session = BrowserSession(config)
    session.page = FakePage()
    a = session.snapshot("step_fail")
    time.sleep(0.002)  # 保证时间戳不同
    b = session.snapshot("step_fail")
    assert a.exists() and b.exists() and a != b


def test_runtime_enter_failure_closes_opened_session(tmp_path, monkeypatch):
    """#84：`with DemoRuntime(...)` 的 __enter__ 在浏览器已开之后抛错时，Python 不会调用 __exit__
    → 旧实现泄漏 Chromium + Playwright 驱动（登录/OU 超时/attach_demo 缺文件等都可能触发，README 记录
    SIT 常见）。修复后入口异常路径必须关闭已开会话、且原样抛出真因（清理失败不顶掉原始异常）。"""
    import pytest
    import atlas.runtime as rt
    events = {"entered": 0, "closed": 0}

    class FakeSession:
        def __init__(self, config):
            self.page = None
        def __enter__(self):
            events["entered"] += 1
            return self
        def actor(self, name):
            raise RuntimeError("登录/装配在浏览器打开后失败")
        def __exit__(self, *args):
            events["closed"] += 1

    monkeypatch.setattr(rt, "BrowserSession", FakeSession)
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    # #103 预检在浏览器启动**之前**检查 DemoApi 模块路径——本测测的是 actor 步骤的
    # 泄漏路径，必须让它通过预检：造一个存在的假模块文件（不会被 exec，actor 先抛）。
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path))
    # 同口径的旧项目路径预检（check_legacy_paths）也在浏览器启动前：本测要走到 actor，
    # 必须让旧项目根/web 子目录存在，否则会先撞「旧项目根不存在」而不是 actor 的登录失败。
    legacy = tmp_path / "legacy"
    (legacy / "legacy-web").mkdir(parents=True)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    config = AtlasConfig(tmp_path, env="sit")
    with pytest.raises(RuntimeError, match="浏览器打开后失败"):
        with rt.DemoRuntime(config, login=False):
            pass
    assert events["entered"] == 1 and events["closed"] == 1   # 开了就必须关：入口失败不留孤儿浏览器/驱动


def test_runtime_enter_fails_before_browser_when_demo_api_missing(tmp_path, monkeypatch):
    """#103：ATLAS_DEMO_API_PROJECT 指错时，旧实现事件序列是 browser_opened→baked→closed——
    真实 SIT 登录已发生、Cookie 已烘焙，才撞 attach_demo 的 FileNotFoundError，白烧一次会话
    还可能在 SIT 留半截现场（违 #90/#97 fail-fast 口径）。修复后浏览器**不启动**（entered==0），
    且错误点名 ATLAS_DEMO_API_PROJECT/ATLAS_DEMO_API_FILE 键（仿 legacy.check_exec_dir）。"""
    import pytest
    import atlas.runtime as rt
    events = {"entered": 0, "closed": 0}

    class FakeSession:
        def __init__(self, config):
            self.page = None
        def __enter__(self):
            events["entered"] += 1
            return self
        def actor(self, name):
            raise AssertionError("预检必须先于浏览器启动失败，不该走到 actor")
        def __exit__(self, *args):
            events["closed"] += 1

    monkeypatch.setattr(rt, "BrowserSession", FakeSession)
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path / "不存在的旧项目"))
    config = AtlasConfig(tmp_path, env="sit")
    with pytest.raises(FileNotFoundError, match="ATLAS_DEMO_API_PROJECT"):
        with rt.DemoRuntime(config, login=False):
            pass
    assert events["entered"] == 0        # 浏览器根本没开：路径输入错误不烧真实会话
    assert events["closed"] == 0


def test_runtime_enter_fails_before_browser_when_legacy_web_missing(tmp_path, monkeypatch):
    """#138：ATLAS_LEGACY_WEB 指错（legacy-web vs iservice 目录名漂移）时，旧实现 run 轨
    与 pytest 轨不同——pytest 轨 check_exec_dir 早就点名 ATLAS_LEGACY_*，run 轨（本入口）
    完全不检查：真实事件序列 browser_opened→登录→才撞 _load(login.py) 的裸
    FileNotFoundError（不点名配置键 #90；白烧真实 SIT 会话 #103 同族）。
    修复后 config.check_legacy_paths 与 DemoApi 预检同位、浏览器启动前拒绝且点名键。"""
    import pytest
    import atlas.runtime as rt
    events = {"entered": 0, "closed": 0}

    class FakeSession:
        def __init__(self, config):
            self.page = None
        def __enter__(self):
            events["entered"] += 1
            return self
        def actor(self, name):
            raise AssertionError("legacy 预检必须先于浏览器启动失败，不该走到 actor")
        def __exit__(self, *args):
            events["closed"] += 1

    monkeypatch.setattr(rt, "BrowserSession", FakeSession)
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    # DemoApi 预检通过（同文件 :160 口径）：造存在的假模块文件，确保撞的是 legacy 预检
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path))
    legacy = tmp_path / "legacy"
    legacy.mkdir()   # 根存在，web 子目录缺失——撞 ATLAS_LEGACY_WEB 分支
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "wrong-web")
    config = AtlasConfig(tmp_path, env="sit")
    with pytest.raises(FileNotFoundError, match="ATLAS_LEGACY_WEB"):
        with rt.DemoRuntime(config, login=False):
            pass
    assert events["entered"] == 0        # 浏览器根本没开：路径输入错误不烧真实会话
    assert events["closed"] == 0

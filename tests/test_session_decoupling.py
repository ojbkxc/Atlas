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

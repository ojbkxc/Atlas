"""浏览器按运行模块复用；只有失败/debug/heal 才生成现场快照。"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime
from pathlib import Path
from .actor import Actor
from .abilities import BrowseTheWeb, CallApi
from .config import AtlasConfig

class BrowserSession:
    def __init__(self, config: AtlasConfig):
        self.config = config
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self) -> "BrowserSession":
        # 浏览器本身与目标站点解耦：本地页面/离线冒烟不需要 DEMO_URL_*。
        # base_url 的缺失与合法性只在真正登录/调用 API 的 Runtime 里校验。
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        debug_port = os.environ.get("CDP_DEBUG") == "1"
        args = ["--disable-gpu", "--disable-dev-shm-usage", "--disable-crash-reporter",
                "--disable-breakpad", "--no-first-run", "--disable-extensions"]
        headless = self.config.headless and not debug_port
        if not headless:
            args.append("--start-maximized")
        if debug_port:
            args.append("--remote-debugging-port=" + os.environ.get("CDP_PORT", "9222"))
        # 系统浏览器优先：Linux CI 常用 apt 装的 chromium，无法下载 Playwright 自带包时也能跑。
        executable = os.environ.get("ATLAS_BROWSER_EXECUTABLE", "").strip()
        launch_kwargs = {"headless": headless, "slow_mo": self.config.slow_mo, "args": args}
        if executable:
            launch_kwargs["executable_path"] = executable
            launch_kwargs["channel"] = None
        else:
            launch_kwargs["channel"] = self.config.channel or None
        self.browser = self._pw.chromium.launch(**launch_kwargs)
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080} if headless else None,
            accept_downloads=True)
        self.page = self.context.new_page()
        return self

    def actor(self, name: str = "Atlas") -> Actor:
        actor = Actor(name, [BrowseTheWeb(self.page), CallApi(self.config.base_url)])
        actor.session = self
        return actor

    def snapshot(self, name: str, state: dict | None = None) -> Path | None:
        if not self.page or self.page.is_closed():
            return None
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        folder = self.config.artifacts_dir / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        # 毫秒时间戳防覆盖：同一 case 多轮重跑时保留每轮失败取证
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = folder / f"{safe_name}_{stamp}.png"
        self.page.screenshot(path=str(path), full_page=False)
        if self.config.mode == "debug":
            path.with_suffix(".json").write_text(json.dumps(
                {"step": name, "url": self.page.url, "state": state or {}},
                ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        return path

    def observe(self, step: str) -> Path | None:
        if self.config.mode != "debug":
            return None
        state = self.page.evaluate("""() => {
            const root = document.querySelector('#app');
            const vm = root && root.__vue__;
            return vm ? {component: vm.$options.name || 'app'} : {};
        }""")
        return self.snapshot(step, state)

    def __exit__(self, exc_type, exc, tb) -> None:
        for obj in (self.context, self.browser):
            if obj:
                obj.close()
        if self._pw:
            self._pw.stop()

    def capture(self, step: str, success: bool) -> Path | None:
        if success:
            return self.observe(step)
        return self.snapshot(step + "_FAIL")

class ApiEvidence:
    """显式注册失败重放器，避免猜测接口造成额外副作用。"""
    def __init__(self):
        self._handlers = []

    def register(self, handler) -> None:
        self._handlers.append(handler)

    def classify(self, exc: BaseException) -> str | None:
        from .failures import api_evidence
        for handler in self._handlers:
            replay = handler()
            if replay is not None:
                return api_evidence(exc, replay).value
        return None

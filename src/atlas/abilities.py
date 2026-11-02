"""UI 与 API 是同一个 Actor 的两种 Ability，共享浏览器会话 Cookie。"""
from __future__ import annotations
import importlib.util
import time
from pathlib import Path
from typing import Any
import requests
from .core import Ability

class BrowseTheWeb(Ability):
    def __init__(self, page: Any):
        self.page = page

class ApiResponse(dict):
    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))

class CallApi(Ability):
    """独立、可测试的 REST 驱动；需要 Demo 语义时通过插件委托原 DemoApi。"""
    def __init__(self, base_url: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json, text/plain, */*",
                                    "X-Requested-With": "XMLHttpRequest"})
        self.last_response: ApiResponse | None = None
        self.last_timing_ms = 0.0
        # 会话保活：检测到 auth_expired 时触发回调重新烘焙 Cookie
        self._keepalive_callback = None
        self._keepalive_interval = 0  # 0 = 不主动轮询，只在响应触发时保活
        self._last_keepalive = time.time()

    def set_keepalive(self, callback, interval: float = 300.0) -> None:
        """注册保活回调：检测到会话失效或间隔到期时重新烘焙 Cookie。

        callback 通常是 DemoRuntime 里的重新登录+烘焙函数。
        interval=0 表示只在检测到 auth_expired 时保活，不主动轮询。
        """
        self._keepalive_callback = callback
        self._keepalive_interval = interval

    def ensure_session(self) -> bool:
        """检测当前会话是否需要保活，需要则触发回调。

        返回 True 表示触发了保活，False 表示无需保活。
        不抛异常——保活失败由后续真实请求暴露。
        """
        if not self._keepalive_callback:
            return False
        now = time.time()
        if self._keepalive_interval > 0 and now - self._last_keepalive >= self._keepalive_interval:
            self._keepalive_callback()
            self._last_keepalive = now
            return True
        # 响应触发的保活：检查上一个响应是否 auth_expired
        if self.last_response and not self.last_response.ok:
            data = self.last_response.get("data", {})
            if isinstance(data, dict) and data.get("auth_expired"):
                self._keepalive_callback()
                self._last_keepalive = now
                return True
        return False

    def request(self, method: str, path: str, *, body: Any = None,
                query: dict | None = None, timeout: float = 30.0) -> ApiResponse:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            if not self.base_url:
                raise ValueError("API base_url 为空，请在 .env 配置对应 DEMO_URL_*")
            url = self.base_url + "/" + path.lstrip("/")
        started = time.perf_counter()
        response = self.session.request(method, url, json=body, params=query, timeout=timeout)
        self.last_timing_ms = (time.perf_counter() - started) * 1000
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}
        if response.ok and response.headers.get("Content-Type", "").startswith("text/html"):
            self.last_response = ApiResponse(status=response.status_code, ok=False,
                url=response.url, data={"auth_expired": True, "message": "会话失效或被重定向到登录页"})
        else:
            self.last_response = ApiResponse(status=response.status_code, ok=response.ok,
                                            url=response.url, data=data)
        # 会话保活：检测到 auth_expired 自动触发回调重新烘焙
        self.ensure_session()
        return self.last_response

    def get(self, path: str, **kwargs) -> ApiResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> ApiResponse:
        return self.request("POST", path, **kwargs)

    def bake_from_playwright(self, context: Any) -> None:
        """只烘焙与当前目标域匹配的 Cookie，避免把第三方 Cookie 误发给业务域。"""
        from urllib.parse import urlparse
        host = urlparse(self.base_url).hostname or ""
        for cookie in context.cookies():
            domain = cookie.get("domain", "").lstrip(".")
            if domain and (host == domain or host.endswith("." + domain)):
                self.session.cookies.set(cookie["name"], cookie["value"],
                    domain=cookie.get("domain"), path=cookie.get("path", "/"),
                    secure=cookie.get("secure", False))

    def attach_demo(self, project_root: Path) -> Any:
        """通过适配器复用 legacy-api 的 数百条接口语义，不修改原仓库。"""
        path = project_root / "scripts" / "demo_api.py"
        if not path.is_file():
            raise FileNotFoundError(f"找不到 DemoApi 适配目标：{path}")
        spec = importlib.util.spec_from_file_location("atlas_external_demo_api", path)
        if not spec or not spec.loader:
            raise ImportError(f"无法加载 {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        api = module.DemoApi.__new__(module.DemoApi)
        api.base_url = self.base_url
        api.quiet = True
        api.sess = self.session
        # 绕过 __init__ 就会丢掉原客户端的静态业务头（demo-app 等），
        # 后端会回 BUG-920 No user login: app is null。这里显式补齐。
        static_headers = getattr(module, "STATIC_HEADERS", {})
        if static_headers:
            self.session.headers.update(static_headers)
        api.extra = {"demo-app": "legacy-web"}
        api.spec = api._load_spec(str(project_root / "scripts" / "demo_endpoints.json"))
        return api

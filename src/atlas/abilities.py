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

# 常用二级公共后缀（注册局域）：这些域本身不可注册，cookie domain 等于它们时代表
# 「任意 *.com.cn」级别的授权——烘焙即跨注册域泄露凭据（#141 面A）。精选常见 ccTLD
# 家族；完整 PSL 是大外部表（零新依赖红线），故为 defense-in-depth 而非穷尽列表。
# 新业务接入非列表后缀区域时，按 AGENTS 验证纪律连真实环境核验后增补，不凭空放宽。
_PUBLIC_SUFFIXES = frozenset({
    # 中国
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "mil.cn",
    # 英国
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk",
    # 日本
    "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
    # 澳洲 / 新西兰
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "net.nz", "org.nz",
    # 韩国
    "co.kr", "or.kr", "ne.kr", "ac.kr", "go.kr",
    # 东南亚
    "com.sg", "com.my", "com.hk", "org.hk", "net.hk", "gov.hk",
    "com.tw", "org.tw", "co.in", "co.id", "com.ph", "com.vn",
    # 拉美 / 非洲
    "com.br", "com.mx", "co.za", "com.ar", "com.co",
})

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

        返回 True 表示触发了保活，False 表示无需保活或保活失败。
        不抛异常——保活失败由后续真实请求暴露。
        """
        if not self._keepalive_callback:
            return False
        now = time.time()
        need = False
        if self._keepalive_interval > 0 and now - self._last_keepalive >= self._keepalive_interval:
            need = True
        elif self.last_response and not self.last_response.ok:
            data = self.last_response.get("data", {})
            # #141 面C：auth_expired 标记只在 200+HTML 登录页伪装形态下被置位
            # （request() 的 is_login_page 分支）——裸 401+JSON 网关应答（ICS 过期会话的
            # 最常见形态）不带该键：need=False，会话死亡后永不重烘焙，README/CLAUDE 宣称的
            # 「api 401 自动重登」对真实形态不成立（承诺与实现漂移，#92 同族）。
            # 状态码 401 是权威判据（非文本猜测）；200-JSON 含 "session" 字样不误触发
            # （检测从未看响应体关键词）。403 不纳入：语义是权限不足，重烘焙无济于事，
            # 保守只补承诺面（#115 不把正常路径翻成额外动作）。
            need = (isinstance(data, dict) and data.get("auth_expired")) \
                or self.last_response.get("status") == 401
        if not need:
            return False
        # 保活是尽力而为的后台维护：回调（如长时间巡检里浏览器 context 已关闭 → cookies() 抛错）
        # 失败绝不能顶掉调用方当前可能已成功的响应——request() 在拿到好响应后才调本方法，
        # 且本方法契约「不抛异常」。失败就返回 False，不更新 _last_keepalive，留给后续请求重试。
        try:
            self._keepalive_callback()
        except Exception:
            return False
        self._last_keepalive = now
        return True

    def request(self, method: str, path: str, *, body: Any = None,
                query: dict | None = None, timeout: float = 30.0,
                headers: dict | None = None) -> ApiResponse:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            if not self.base_url:
                raise ValueError("API base_url 为空，请在 .env 配置对应 DEMO_URL_*")
            url = self.base_url + "/" + path.lstrip("/")
        started = time.perf_counter()
        # headers=None 沿用会话默认头；传入则按 requests 语义合并（本次覆盖同名会话头）。
        # ICS 后端缺 isc-app 业务识别头即回 BUG-920，JMeter 迁移步骤靠这里补回鉴权头。
        # body 按 requests 自身语义分流：dict/list 走 json=（序列化+Content-Type）；
        # str/bytes 走 data= 原样发送——JMeter 非 raw 多参数迁移产物就是 form 串
        # （`custNo=1&page=2`），一律 json= 会被包成 JSON 字符串字面量 `"custNo=1&page=2"`
        # 发出，后端收脏数据得假 400（#98 修好报文提取，发送语义仍得对）；bytes 走 json=
        # 更是序列化器当场 TypeError，定性器先于被诊断报文死。其余类型点名拒绝，
        # 不让 requests 内部裸崩冒充框架缺陷。
        json_body = data_body = None
        if isinstance(body, (dict, list)):
            json_body = body
        elif isinstance(body, (str, bytes)):
            data_body = body
        elif body is not None:
            raise TypeError(f"不支持的请求体类型 {type(body).__name__}：{body!r:.80}")
        response = self.session.request(method, url, json=json_body, data=data_body,
                                         params=query, headers=headers, timeout=timeout)
        self.last_timing_ms = (time.perf_counter() - started) * 1000
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}
        # #122 面B：会话失效的常见形态不止「200 + Content-Type: text/html」——异常网关/
        # mock 服务器回登录页不带（或畸形大小写）Content-Type 头，旧实现只看头，正文整页
        # HTML 被判 ok=True：登录页伪装成功，下游拿 HTML 当数据烂到远处才炸（#102 键名
        # 漂移同族假绿）。判据收敛到「头或正文像 HTML」，两形态都拦；有 Content-Type 的
        # 原判据保留（不把正常路径翻错，#115 教训），正文判据只在 JSON 解析失败落 raw 时
        # 求值——那是唯一读 response.text 的路径，且真返回 HTML 片段的合法 JSON API 不存在，
        # 整页文档头才触发。
        is_login_page = response.headers.get("Content-Type", "").startswith("text/html")
        if not is_login_page and "raw" in data:
            head = response.text[:300].lstrip().lower()
            is_login_page = head.startswith("<!doctype html") or head.startswith("<html")
        if response.ok and is_login_page:
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
        if not host:
            return  # base_url 无法解析出主机：不烘焙（宁可漏烘焙也不外泄）
        for cookie in context.cookies():
            # #141 面B：域名按 RFC 大小写不敏感——urlparse().hostname 恒下写，旧实现拿
            # 原样 domain 比较，真实 SSO 常见大写 `Domain=EXAMPLE.COM` 形态永不命中：合法凭据
            # 被静默丢弃（fail-safe 方向但正确性洞——用户只见 401 不见根因）。归一下写
            # 再比较；requests jar 的 domain_match 本就下写化，存下写形态与判据一致。
            raw_domain = str(cookie.get("domain", "")).lstrip(".").lower()
            if not raw_domain:
                continue
            # 仅两类可烘焙：
            #   ① 与目标主机完全相同（host cookie，含离线用的 localhost）；
            #   ② 目标主机的合法父域，且父域「可注册」——既不是裸 TLD，也不是公共后缀
            #      （com.cn/co.uk 这类二级注册局域）。
            # ②的两道闸各自挡一类灾难：
            #   - 裸 TLD（com/org）：`"." not in` 挡——否则任意 *.com 请求都带上凭据，跨站泄露；
            #   - 二级公共后缀（com.cn/co.uk）：#141 面A 实锤旧判据 `"." in domain` 只挡了
            #     前者——domain=com.cn 对 host=ics.example.com.cn 命中 endswith(".com.cn")，
            #     该 cookie 会被发向**任意他企业 *.com.cn 注册域**，违「Cookie 只烘焙到
            #     匹配域」（AGENTS §3 红线）。完整 PSL 是外置大表（零新依赖红线），此处收敛
            #     常用二级公共后缀精选集——defense-in-depth：真实浏览器按 PSL 不会给出
            #     domain=com.cn 的 cookie，但插件/fake/异常桥接给出时绝不照单全收。
            matched = raw_domain == host or (
                "." in raw_domain
                and raw_domain not in _PUBLIC_SUFFIXES
                and host.endswith("." + raw_domain))
            if matched:
                self.session.cookies.set(cookie["name"], cookie["value"],
                    domain=raw_domain, path=cookie.get("path", "/"),
                    secure=cookie.get("secure", False))

    def attach_demo(self, project_root: Path, *, module_file: str = "demo_api.py",
                    class_name: str = "DemoApi", spec_file: str = "demo_endpoints.json",
                    app_header: tuple[str, str] = ("demo-app", "legacy-web")) -> Any:
        """通过适配器复用 legacy-api 的接口语义，不修改原仓库。

        默认值对应仓库自带的虚构 demo；指向真实项目时由 config 传入实际文件名/类名/头。
        spec_file 缺失是**外部客户端自己**的语义：真实 IcsApi._load_spec 对不存在路径
        返回 {"endpoints": []}（存活级降级），Atlas 不越界替外部类加检查——预检只覆盖
        Atlas 无法恢复的两处：模块文件缺失（fail-fast 在 runtime 层）与类名不存在。
        """
        path = project_root / "scripts" / module_file
        if not path.is_file():
            raise FileNotFoundError(
                f"找不到 DemoApi 适配目标：{path}"
                "（检查 ATLAS_DEMO_API_PROJECT / ATLAS_DEMO_API_FILE 是否指向旧项目的 scripts 目录）")
        spec = importlib.util.spec_from_file_location("atlas_external_demo_api", path)
        if not spec or not spec.loader:
            raise ImportError(f"无法加载 {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, class_name):
            # 裸 AttributeError「module has no attribute 'IcsAPI'」用户不知道改哪个键——
            # 点名 ATLAS_DEMO_API_CLASS 并列出模块内可用的类名作线索（仿 #90 点名 env 键口径）。
            classes = [n for n in dir(module)
                       if not n.startswith("_") and isinstance(getattr(module, n, None), type)]
            raise ImportError(
                f"模块 {path} 中不存在类 {class_name!r}（检查 ATLAS_DEMO_API_CLASS）；"
                f"模块内可用类：{classes[:10]}")
        api_class = getattr(module, class_name)
        # #122 面A：类存在但不符合 DemoApi 客户端协议（缺 _load_spec）——真实项目模块常同时
        # 有 IcsApi 与 IcsService，ATLAS_DEMO_API_CLASS 手滑填成后者时，旧实现到
        # `api._load_spec(...)` 才裸 AttributeError「'IcsService' object has no attribute
        # '_load_spec'」：不提 env 键、不提这是协议契约——冒充框架缺陷（#103 类名不存在
        # 同口径延伸：类存在≠可用，#63 注册≠可用同族）。装配前当场点名。
        if not callable(getattr(api_class, "_load_spec", None)):
            raise ImportError(
                f"{path} 的 {class_name!r} 缺少 _load_spec，不是 DemoApi 客户端类"
                "（检查 ATLAS_DEMO_API_CLASS 是否指向接口客户端而非同模块的 Service/页面类）")
        api = api_class.__new__(api_class)
        api.base_url = self.base_url
        api.quiet = True
        api.sess = self.session
        # 绕过 __init__ 就会丢掉原客户端的静态业务头（demo-app 等），
        # 后端会回 BUG-920 No user login: app is null。这里显式补齐。
        static_headers = getattr(module, "STATIC_HEADERS", {})
        if static_headers:
            self.session.headers.update(static_headers)
        api.extra = {app_header[0]: app_header[1]}
        api.spec = api._load_spec(str(project_root / "scripts" / spec_file))
        return api

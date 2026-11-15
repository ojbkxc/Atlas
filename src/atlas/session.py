"""浏览器按运行模块复用；只有失败/debug/heal 才生成现场快照。

pf-6 取证截图降质旋钮（ATLAS_SHOT_QUALITY，opt-in）：
- 未设/空串：PNG 保真——screenshot 实参与历史逐字一致（`path` + `full_page=False`），
  文件名 `.png` 后缀不变（默认路径逐字节不变的锁步口径）。
- `jpeg`：type="jpeg"（Playwright 默认质量），文件名后缀 .png → .jpg——后缀必须随内容
  变（附件媒体类型按后缀派生，.png 名装 JPEG 字节 = 假内容，#115 教训）。
- `jpeg,q=N`：附加 quality=N（0–100 整数）。
- 坏形态（未知格式/未知选项/quality 越界/非整数）→ ValueError 当场点名：旋钮是人显式
  opt-in 的，配置错了**必须**让运行失败而不是静默回落 PNG——「没降质」被当成配置生效
  是假配置（红线：坏形态点名不静默回落）。解析在 snapshot 的 try **之外**：取证的
  尽力而为 except 不得把旋钮配置错误吞成 None（吞掉=取证静默消失，比截图大更坏）。
- 失败腿截图不可省（取证红线 #8 的对偶面）：本旋钮只降编码保真度，绝不提供「不截图」
  通道——降质是「取证保真 vs 体积」的显式权衡，缺的是质量不是证据。
"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime
from pathlib import Path
from .actor import Actor
from .abilities import BrowseTheWeb, CallApi
from .atomic_io import atomic_write_text
from .config import AtlasConfig

# pf-6 降质旋钮 env 键名唯一出处（消费方只读这里，不留第二份字面量 #92）。
SHOT_QUALITY_ENV = "ATLAS_SHOT_QUALITY"


def _shot_quality() -> tuple[str, dict]:
    """ATLAS_SHOT_QUALITY 解析 → (文件名后缀, screenshot 实参)；未设/空串 → ("png", {})。

    只认 `jpeg` / `jpeg,q=N`（N 0–100 整数）——其余一律 ValueError 点名（坏形态不静默
    回落，见模块 docstring）。每次调用现解析：env 是进程级配置，一次字符串切分的代价
    远低于模块级缓存造成的「改了 env 不生效」陷阱。
    """
    raw = os.environ.get(SHOT_QUALITY_ENV, "").strip()
    if not raw:
        return "png", {}
    format_name, sep, rest = raw.partition(",")
    if format_name.strip().lower() != "jpeg":
        raise ValueError(
            f"{SHOT_QUALITY_ENV}={raw!r}：只认 `jpeg` / `jpeg,q=N`（N 0–100 整数）；"
            f"PNG 保真是缺省，要降质请显式指定 jpeg")
    kwargs = {"type": "jpeg"}
    if sep:   # 有逗号就必须带合法选项（"jpeg," 尾逗号是坏形态，不是「裸 jpeg"）
        for option in rest.split(","):
            key, sep, value = option.partition("=")
            if not sep or key.strip() != "q":
                raise ValueError(
                    f"{SHOT_QUALITY_ENV}={raw!r}：jpeg 的已知选项只有 `q=N`（质量 0–100），"
                    f"未知选项 {option!r}")
            try:
                quality = int(value.strip())
            except ValueError:
                raise ValueError(
                    f"{SHOT_QUALITY_ENV}={raw!r}：quality 必须是整数 0–100，实得 {value!r}")
            if not 0 <= quality <= 100:
                raise ValueError(
                    f"{SHOT_QUALITY_ENV}={raw!r}：quality 必须在 0–100，实得 {quality}")
            kwargs["quality"] = quality
    return "jpg", kwargs

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
        try:
            # en-7：`ATLAS_BROWSER_PROXY` 显式 opt-in 才追加——未设置时 kwargs 逐字节同旧实现
            # （属性在 launch 之前求值，坏形态 ValueError 于浏览器启动前点名，不烧会话）。
            proxy = self.config.browser_proxy
            if proxy:
                launch_kwargs["proxy"] = proxy
            self.browser = self._pw.chromium.launch(**launch_kwargs)
            self.context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080} if headless else None,
                accept_downloads=True)
            self.page = self.context.new_page()
        except BaseException:
            # #111：`with` 语义下 __enter__ 抛错不会触发 __exit__——start() 成功后
            # launch/new_context/new_page 任一失败（executable 指错、浏览器初始化中崩、
            # 进程组权限）都让 Playwright node 驱动与半开 Chromium 进程双双泄漏，每次失败
            # 累积一个孤儿进程（DemoRuntime.runtime.py:70 在赋值 self.session 前就抛，
            # 外层清理链同样够不着）。清理尽力而为且绝不顶掉原始真因（#97 同红线；
            # #82 的 __exit__ 分工是「有用例异常时不掩埋」，这里是「入口异常自身优先」）。
            for obj in (self.context, self.browser):
                if obj:
                    try:
                        obj.close()
                    except Exception:
                        pass
            try:
                self._pw.stop()
            except Exception:
                pass
            raise
        return self

    def actor(self, name: str = "Atlas") -> Actor:
        actor = Actor(name, [BrowseTheWeb(self.page), CallApi(self.config.base_url)])
        actor.session = self
        return actor

    def snapshot(self, name: str, state: dict | None = None) -> Path | None:
        if not self.page or self.page.is_closed():
            return None
        # pf-6：旋钮解析在 try **之外**——坏配置当场点名（ValueError），绝不被取证的
        # 尽力而为 except 吞成 None（吞掉=取证静默消失，比截图大更坏）。
        suffix, shot_kwargs = _shot_quality()
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        path = None
        try:
            folder = self.config.artifacts_dir / "snapshots"
            folder.mkdir(parents=True, exist_ok=True)
            # 毫秒时间戳防覆盖：同一 case 多轮重跑时保留每轮失败取证
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            path = folder / f"{safe_name}_{stamp}.{suffix}"
            # 未设旋钮时实参与历史逐字一致：screenshot(path=…, full_page=False)
            self.page.screenshot(path=str(path), full_page=False, **shot_kwargs)
        except Exception:
            # 取证（截图/建目录）失败绝不改变用例判定：步骤成败由 perform 独立决定。
            # engine 把 capture 放在 try 内，若这里抛出会把已通过的步骤误判成失败并 raise。
            return None
        if self.config.mode == "debug" and path is not None:
            try:
                # wave4 rb-8：storage_state 侧车直写非原子——写中崩留半残文件。改原子写。
                atomic_write_text(path.with_suffix(".json"), json.dumps(
                    {"step": name, "url": self.page.url, "state": state or {}},
                    ensure_ascii=False, default=str, indent=2))
            except Exception:
                pass  # PNG 已落盘，侧车状态失败不影响取证主体
        return path

    def observe(self, step: str) -> Path | None:
        if self.config.mode != "debug":
            return None
        try:
            state = self.page.evaluate("""() => {
                const root = document.querySelector('#app');
                const vm = root && root.__vue__;
                return vm ? {component: vm.$options.name || 'app'} : {};
            }""")
        except Exception:
            state = {}   # 取不到 Vue 现场仍继续，由 snapshot 决定降级返回 None，不中断判定
        return self.snapshot(step, state)

    def __exit__(self, exc_type, exc, tb) -> None:
        # 关闭是尽力而为的清理：浏览器/页面在失败现场常已崩溃（SIT 偶发挂起→被杀），
        # context.close() 抛错时**绝不能**跳过 browser.close() 与 _pw.stop()——否则浏览器与
        # Playwright 驱动进程双双泄漏。逐个 try 保证每个都尝试关闭。
        # 且 __exit__ 里抛新异常会替换/掩埋正在传播的用例异常（真实断言失败被 "Target closed" 顶掉）。
        # 因此只有在「没有正在传播的用例异常」(exc_type is None) 时才把清理错误抛出（避免静默泄漏），
        # 否则原样放行用例自身的异常，清理失败不喧宾夺主。
        errors: list[Exception] = []
        for obj in (self.context, self.browser):
            if obj:
                try:
                    obj.close()
                except Exception as e:
                    errors.append(e)
        if self._pw:
            try:
                self._pw.stop()
            except Exception as e:
                errors.append(e)
        if errors and exc_type is None:
            # #123 面1：多清理错误旧实现 `raise errors[0]` 只报第一个——context/browser/_pw
            # 三环节全崩（浏览器挂起被杀后 close 与驱动 stop 常连环失败）时，驱动进程正在
            # 泄漏却只剩第一条 OSError 可见，其余静默 = 清理链无痕（与 #85 审计链断点同红线；
            # 与 #121 面D runtime.__exit__ 完全同构）。单错原样抛（#82 既有口径不翻错），
            # 多错聚合 ExceptionGroup 全部点名；有在用例异常仍让位真因（#82 分工不变）。
            if len(errors) == 1:
                raise errors[0]
            raise ExceptionGroup("会话关闭清理失败（context/浏览器/驱动）", errors)

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
        """重放是尽力而为的诊断：handler() 重放的正是刚失败的接口（会话早断/网关挂），
        自己再抛错完全可能；replay 形态也归业务方决定，api_evidence 之外仍有崩点
        （如 handler 返回非映射、status 是字符串时内部比较 TypeError）。
        任何一条重放失败就跳过试下一条，全坏则返回 None 由 engine 回落 classify_failure——
        定性器绝不能先于被定性对象死（#89 同红线）：在 engine 的 except 块里抛错会把
        原用例异常顶掉（During handling 链），且 results 不追加、经验草稿不留痕、
        现场取证不执行——整条失败证据链被一个坏重放器清空。
        只捕 Exception：KeyboardInterrupt 等中断信号照常传播（与 ensure_session #69 同口径）。"""
        from .failures import api_evidence
        for handler in self._handlers:
            try:
                replay = handler()
                if replay is None:
                    continue
                if not isinstance(replay, dict):
                    continue
                return api_evidence(exc, replay).value
            except Exception:
                continue
        return None

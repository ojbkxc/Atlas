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
        try:
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
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        path = None
        try:
            folder = self.config.artifacts_dir / "snapshots"
            folder.mkdir(parents=True, exist_ok=True)
            # 毫秒时间戳防覆盖：同一 case 多轮重跑时保留每轮失败取证
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            path = folder / f"{safe_name}_{stamp}.png"
            self.page.screenshot(path=str(path), full_page=False)
        except Exception:
            # 取证（截图/建目录）失败绝不改变用例判定：步骤成败由 perform 独立决定。
            # engine 把 capture 放在 try 内，若这里抛出会把已通过的步骤误判成失败并 raise。
            return None
        if self.config.mode == "debug" and path is not None:
            try:
                path.with_suffix(".json").write_text(json.dumps(
                    {"step": name, "url": self.page.url, "state": state or {}},
                    ensure_ascii=False, default=str, indent=2), encoding="utf-8")
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

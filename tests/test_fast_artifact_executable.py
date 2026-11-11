"""gap9 xcheck 跨切面判据测：快模式产物**真能执行**（离线，任务 #5）。

判据出处：`docs/gaps/gap9-fast-pytest-runner.md` §E.1 + `docs/gaps/gap9-lead-rulings.md`
F-1/F-2/F-3/F-4/F-5/F-7 + lead 2026-11-09 增补三类（契约符号存在性 / attach 通道形态 /
dispatch pop 名单双向核）。本文件只加测不改 src；缺陷点名回报 lead（D 表所有权纪律）。

**F-1（裁决 1）**：E.1.5 拆半——本文件只做离线半段：假 step 记录喂**盘产物**自己的
`RunWriter` → `atlas.allure_report.emit_allure` 核状态映射。tests/ 内无 `http.server`、
无子进程真请求（localhost socket 也算网络，AGENTS §2）；真请求半段=E.2 R5。
`pytest --collect-only -q` 与 `compileall` 子进程合法（不起服务不发请求）。

与队友判据的分工（F-15：不留第二把尺）：support-impl 的锁步/形态测打在**模板字符串**上；
本文件打在**真实生成器写到 tmp 的盘产物字节**上——模板绿≠产物写过（#106/#139 先例）。
三张判据表（FAIL_CLASS_MAP / RUN_STATUS_VOCAB / REASON_MAX_LEN）一律经 import 的产物
模块属性读取，本文件不复制任何表值域。

恒真防线（任务判据 5）：每条负向断言同测自带正向对照或前提自证——断言「产物无 sleep」
同测证「语义等待 4 函数在位且 ticks 真按 TICK_MS 走」；断言「未接线→env_blocked」同测跑
真 AssertionError→failed 与裸 NotImplementedError→unknown（裁决 2 反向洗绿防线）；禁词
扫描各配对抗样本。

**盘面形态（lead 2026-11-09 复核，非旧在途态）**：契约 gap9b C:122 点名的
`session_channel(env)` / `safe_persist(key,value)` 单点、`ATLAS_FAST_INTERACTIVE` 在场闸、
`ATLAS_FAST_ATTACH_MATCH` 点名选页（0 命中/≥2 命中各自 EnvBlocked、`pages[0]`/`contexts[0]`
兜底已删）——均已落盘。本文件的符号存在性测与通道形态测按实盘契约钉；#14 尚未标完成
（lead：交割需绑当日全量实跑+collect 绿证，F-21）——若交割后形态再变，本文件测红即信号。
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

from atlas import waits as atlas_waits
from atlas.allure_report import emit_allure
from atlas.cli import main
from atlas.failures import FLOW_STATUS_TIMEOUT, FailureClass
from atlas.generator import GenOptions, generate
from atlas.generator import emit_fast
from atlas.generator.emit_fast import ENV_KEYS, FAST_FIXTURE_NAMES
from atlas.generator.emit_pytest import emit_pytest_package
from atlas.scheduling import dispatch

# ---------------------------------------------------------------------------
# 夹具：真实生成器管线产快包到 tmp（判据 1「不碰仓库目录」）
# ---------------------------------------------------------------------------

_DOC = {
    "openapi": "3.0.0",
    # RFC 2606 保留域——「无内网域字面量」判据的正向对照：文档 base 只准出现在用例层
    # （文档回声），框架文件必须 0 个 URL 字面量。
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/pets/{id}": {"get": {"summary": "查询宠物",
                               "parameters": [{"name": "id", "in": "path", "required": True,
                                               "schema": {"type": "string"}}],
                               "responses": {"200": {"content": {"application/json": {
                                   "schema": {"$ref": "#/components/schemas/Pet"}}}}}}},
        "/pets": {"post": {"summary": "创建宠物",
                           "requestBody": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Pet"}}}},
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Result"}}}}}}},
    },
    "components": {"schemas": {
        "Pet": {"type": "object", "required": ["name"],
                "properties": {"name": {"type": "string"}, "id": {"type": "integer"}}},
        "Result": {"type": "object", "properties": {"resultCode": {"type": "string"}}}}},
}

# 固定组件夹具（不读包内 components.json——队友在途编辑面，F-6 快照纪律）。
# 选择器值是**金丝雀**：只准出现在派生通道（atlas_pages.py），框架/用例层必须 0（判据 3-e）。
_CANARY = ".xchk-canary-sel-4f9k2x"
_COMPS = {"components": {"order_header": {"selectors": {"search": _CANARY}}}}

# UI 腿走 gap9b B-2 声明位形态（emit-impl #12 交接：按旧 task=组件名构造会收「缺声明位」拒）。
_FLOW = {
    "id": "xchk-flow",
    "steps": [
        {"id": "s1", "task": "api_call", "method": "GET", "path": "/api/order",
         "save_as": "resp_a", "read_only": True},
        {"id": "s2", "task": "open_header", "page_object": "order_header",
         "read_only": True},
    ],
    "assert": ["resp_a.ok == true"],
}

# 业务字面量黑名单（红线 #11 的跨切面核对；atlas_pages.py=知识派生合法通道，不在扫描面）
_BUSINESS_TOKENS = ("midea", "icssit", "isc-axios-sessionid", "SO-DEMO-0001",
                    "vendorCorpNumber", "demoicon--search", "Bearer ")


@pytest.fixture(scope="module")
def doc_path(tmp_path_factory):
    d = tmp_path_factory.mktemp("xchkdoc")
    (d / "pet.yaml").write_text(yaml.safe_dump(_DOC), encoding="utf-8")
    return d / "pet.yaml"


@pytest.fixture(scope="module")
def fast_pkg(doc_path, tmp_path_factory):
    """判据 1 主面：`generate()`（atlas gen 同一管线，with_pytest+fast）产出到 tmp。"""
    out = tmp_path_factory.mktemp("fastpkg")
    generate(doc_path, out_dir=out, options=GenOptions(with_pytest=True, fast=True))
    pkg = out / "pytest"
    assert (pkg / "conftest.py").is_file() and (pkg / "_atlas_fast.py").is_file()
    return pkg


@pytest.fixture(scope="module")
def slow_pkg(doc_path, tmp_path_factory):
    """fast=False 对照包（E.1.11 正向对照：旧形态 skip/NotImplementedError 仍在）。"""
    out = tmp_path_factory.mktemp("slowpkg")
    generate(doc_path, out_dir=out, options=GenOptions(with_pytest=True))
    return out / "pytest"


@pytest.fixture(scope="module")
def slow_flow_pkg(tmp_path_factory):
    out = tmp_path_factory.mktemp("slowflow")
    emit_pytest_package(out_dir=out, flow=_FLOW, components=_COMPS, fast=False)
    return out


@pytest.fixture(scope="module")
def flow_fast_pkg(tmp_path_factory):
    """含 UI 腿的快包（声明位渲染），判据 3 的用例层/页对象层扫描主对象。"""
    out = tmp_path_factory.mktemp("flowfast")
    emit_pytest_package(out_dir=out, flow=_FLOW, components=_COMPS, fast=True)
    return out


def _pkg_py(pkg: Path) -> list[Path]:
    return sorted(p for p in pkg.glob("*.py") if p.is_file())


def _read(pkg: Path, name: str) -> str:
    return (pkg / name).read_text(encoding="utf-8")


def _test_module(pkg: Path) -> Path:
    return next(p for p in _pkg_py(pkg) if p.name.startswith("test_"))


def _artifact_text(pkg: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _pkg_py(pkg))


def _clean_env(monkeypatch) -> None:
    for k in ("ATLAS_FAST_LOGIN_INDICATOR", "ATLAS_FAST_ACCOUNT", "ATLAS_FAST_PASSWORD",
              "ATLAS_FAST_BASE_URL", "ATLAS_FAST_CDP", "ATLAS_FAST_ENV",
              "ATLAS_FAST_ALLOW_WRITES", "ATLAS_RUNS_DIR", "CASE_TIMEOUT",
              "DEMO_USERNAME", "DEMO_PASSWORD", "ISC_APP_HEADER", "CDP_DEBUG",
              "ATLAS_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def _load_pkg(pkg: Path, monkeypatch, request) -> dict:
    """按**盘产物字节**加载底座/conftest（模板测过≠盘上写过——本测独立面）。"""
    _clean_env(monkeypatch)
    monkeypatch.syspath_prepend(str(pkg))
    importlib.invalidate_caches()
    tag = request.node.name.replace("[", "_").replace("]", "_")
    names = ["_atlas_fast", "atlas_pages", "_atlas_support"]
    mods = {}
    for n in names:
        if (pkg / f"{n}.py").exists():
            mods[n] = importlib.import_module(n)
    spec = importlib.util.spec_from_file_location(f"_xchk_conftest_{tag}", pkg / "conftest.py")
    conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conftest)
    mods["conftest"] = conftest

    def _cleanup():
        for name in [f"_xchk_conftest_{tag}"] + names:
            sys.modules.pop(name, None)
    request.addfinalizer(_cleanup)
    return mods


@pytest.fixture()
def art(fast_pkg, monkeypatch, request):
    m = _load_pkg(fast_pkg, monkeypatch, request)
    return types.SimpleNamespace(support=m["_atlas_fast"], conftest=m["conftest"],
                                 pages=m["atlas_pages"], api_sup=m["_atlas_support"],
                                 pkg=fast_pkg)


# ---------------------------------------------------------------------------
# 形态判据函数（F-3/F-4：实参形态，非计数；输入=盘产物文本）
# ---------------------------------------------------------------------------

def _wait_form_violations(src: str) -> list[str]:
    """违例清单：wait_for_timeout 实参非裸 TICK_MS / time.sleep / 字面毫秒等待调用。"""
    bad: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            name = fn.attr
            qual = f"{fn.value.id}." if isinstance(fn.value, ast.Name) else ""
        elif isinstance(fn, ast.Name):
            name, qual = fn.id, ""
        else:
            continue
        if name == "wait_for_timeout":
            if len(node.args) != 1 or not (isinstance(node.args[0], ast.Name)
                                           and node.args[0].id == "TICK_MS"):
                bad.append(f"L{node.lineno} wait_for_timeout 实参非裸 TICK_MS")
        elif name == "sleep" and qual == "time.":
            bad.append(f"L{node.lineno} time.sleep 睡眠通道（F-3 已否决）")
        elif name.startswith("wait") and node.args and \
                isinstance(node.args[0], ast.Constant) and \
                isinstance(node.args[0].value, (int, float)) and \
                not isinstance(node.args[0].value, bool):
            bad.append(f"L{node.lineno} 字面毫秒等待调用 {qual}{name}")
    return bad


_ENV_READ_RE = re.compile(
    r"""(?:environ|env|env_snapshot|os\.environ)(?:\.get\(\s*|__getitem__\(\s*|\[\s*)"""
    r"""["']([A-Za-z0-9_]+)["']""")


def _env_keys_read(*sources: str) -> set[str]:
    """模板里被真读的 env 键名（os.environ.get / env.get / environ[...] 诸形态，#14 后
    session_channel 走 env 映射参数，同判据）。"""
    keys = set()
    for src in sources:
        for m in _ENV_READ_RE.finditer(src):
            keys.add(m.group(1))
    return keys


class _FakePage:
    """假 page：行为面夹具（零浏览器零 socket，F-1 合规）。"""

    def __init__(self, indicator_hits=0):
        self.indicator_hits = indicator_hits
        self.locator_calls: list = []
        self.ticks: list = []
        self.shots: list = []

    def locator(self, sel):
        self.locator_calls.append(sel)
        page = self

        class _L:
            def count(_):
                return page.indicator_hits
        return _L()

    def is_closed(self):
        return False

    def screenshot(self, path=None, full_page=False):
        self.shots.append(path)

    def wait_for_selector(self, sel, state=None, timeout=None):
        return None

    def wait_for_timeout(self, ms):
        self.ticks.append(ms)


class _FakeConfig:
    def getoption(self, name, default=None):
        return False


class _FakeReq:
    def __init__(self):
        self.config = _FakeConfig()


class _FakeReqNode:
    def __init__(self, function):
        self.function = function


# ---------------------------------------------------------------------------
# 假 playwright（F-1 合规：不发请求、不触真驱动）
# ---------------------------------------------------------------------------

def _fake_page(url, context):
    return types.SimpleNamespace(url=url, context=context)


def _fake_playwright_modules(rec: dict, attach_pages=None):
    """假 playwright（零真驱动零 socket，F-1）。attach_pages=外部浏览器已开页集合。"""
    class _Page:
        pass

    class _Context:
        def __init__(self, pages=None):
            self.pages = pages if pages is not None else []
            self.external = pages is not None

        def new_page(self):
            return _Page()

        def close(self):
            rec["context_close"] += 1

    class _Browser:
        def __init__(self, contexts=None):
            self.contexts = contexts if contexts is not None else []

        def new_context(self, **kw):
            rec["context_kw"].append(kw)
            return _Context()

        def close(self):
            rec["browser_close"] += 1

    class _Chromium:
        def launch(self, **kw):
            rec["launch"].append(kw)
            return _Browser()

        def connect_over_cdp(self, endpoint):
            rec["cdp"].append(endpoint)
            urls = attach_pages if attach_pages is not None else ["http://ext/target"]
            contexts = []
            for u in urls:
                ctx = _Context()
                p = _fake_page(u, ctx)
                ctx.pages = [p]
                contexts.append(ctx)
            return _Browser(contexts=contexts)

    class _Pw:
        chromium = _Chromium()

        def stop(self):
            rec["stop"] += 1

    class _Launcher:
        def start(self):
            rec["pw_started"] += 1
            return _Pw()

    sub = types.ModuleType("playwright.sync_api")
    sub.sync_playwright = lambda: _Launcher()
    parent = types.ModuleType("playwright")
    parent.sync_api = sub
    return {"playwright": parent, "playwright.sync_api": sub}


def _drive_sole_page(conftest, monkeypatch, rec, attach_pages=None):
    mods = _fake_playwright_modules(rec, attach_pages)
    monkeypatch.setitem(sys.modules, "playwright", mods["playwright"])
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mods["playwright.sync_api"])
    gen = conftest.sole_page.__wrapped__(_FakeReq())
    page = next(gen)
    return gen, page


# ===========================================================================
# 判据 1 · 盘产物 compileall 全过（E.1.1；前提自证：文件面非空）
# ===========================================================================

def test_generated_fast_package_compileall(fast_pkg, flow_fast_pkg):
    for pkg in (fast_pkg, flow_fast_pkg):
        files = _pkg_py(pkg)
        assert len(files) >= 4, f"快包文件面不完整：{[p.name for p in files]}"
        r = subprocess.run([sys.executable, "-m", "compileall", "-q", str(pkg)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"compileall rc{r.returncode}\n{r.stdout}\n{r.stderr}"
        for p in files:  # compileall 可能吃旧 pyc 假绿；逐文件 ast.parse 兜真身，点名到文件
            try:
                ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                pytest.fail(f"盘产物逐文件 parse：{p} FAIL line {exc.lineno}：{exc.msg}")


# ===========================================================================
# 判据 2 · tmp 里 pytest --collect-only 成功收集（合法 pytest 模块，不发请求）
# ===========================================================================

def _subprocess_env() -> dict:
    return ({k: v for k, v in os.environ.items()
             if not k.startswith(("ATLAS_", "DEMO_", "ISC_", "CDP_", "CASE_"))}
            | {"PYTHONIOENCODING": "utf-8"})


def test_collect_only_succeeds_on_artifacts(fast_pkg, flow_fast_pkg):
    for pkg in (fast_pkg, flow_fast_pkg):
        r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                            "-p", "no:cacheprovider", str(pkg)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=_subprocess_env(),
                           cwd=str(pkg.parent))
        assert r.returncode == 0, (
            f"collect-only rc={r.returncode}（产物非法 pytest 模块/收集期 NameError）\n"
            f"--- stdout\n{r.stdout[-2500:]}\n--- stderr\n{r.stderr[-1500:]}")
        collected = [l for l in r.stdout.splitlines() if "::" in l]
        assert len(collected) >= 2, f"收集面恒真风险（0~1 条）：{r.stdout}"
    # UI 腿经声明位真渲染并进收集清单（fast 非 skip 占位的可执行性证据）
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                        "-p", "no:cacheprovider", str(flow_fast_pkg)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_subprocess_env(),
                       cwd=str(flow_fast_pkg.parent))
    assert any("s2" in l for l in r.stdout.splitlines()), f"UI 腿未进收集：\n{r.stdout}"


# ===========================================================================
# 判据 3(a) · 无固定 sleep（F-3/F-4 形态判据）+ 语义等待在位自证 + 对抗样本
# ===========================================================================

def test_no_fixed_sleep_in_artifacts(fast_pkg, flow_fast_pkg):
    support_src = _read(fast_pkg, "_atlas_fast.py")
    assert _wait_form_violations(support_src) == []  # 盘产物等待形态全合规（E.1.3 ①②③）
    for pkg in (fast_pkg, flow_fast_pkg):
        for p in _pkg_py(pkg):
            if p.name.startswith("test_") or p.name == "atlas_pages.py":
                src = p.read_text(encoding="utf-8")
                assert "wait_for_timeout" not in src, f"{p.name} 用例层固定睡眠"
                assert "pytest.skip(" not in src, f"{p.name} 裸 pytest.skip（F5 静默跳）"
                assert "time.sleep(" not in src, f"{p.name} sleep 通道"
    # 前提自证（否则「无 sleep」恒真）：4 个语义等待函数真实定义、TICK_MS=100 在位、
    # 底座确有裸 TICK_MS 节拍调用——扫描面在真内容上跑
    defined = {n.name for n in ast.parse(support_src).body
               if isinstance(n, ast.FunctionDef)}
    assert {"wait_for_rows", "wait_for_mask_gone", "wait_for_vue_field",
            "wait_for_url_change"} <= defined, "语义等待缺席=本扫描恒真假象（#139 断链同族）"
    assert support_src.count("page.wait_for_timeout(TICK_MS)") >= 3
    assert re.search(r"^TICK_MS = 100\b", support_src, re.M)
    # 对抗样本（F-4 义务）：同一把尺必须让注入形态红，且不误伤好形态
    assert _wait_form_violations("def f(page):\n    page.wait_for_timeout(TICK_MS * 2)\n")
    assert _wait_form_violations("def f(page):\n    page.wait_for_timeout(2000)\n")
    assert _wait_form_violations("import time\n\ndef f():\n    time.sleep(1)\n")
    assert _wait_form_violations("def f(page):\n    page.wait_for_timeout(TICK_MS)\n") == []


def test_waiters_use_tick_only_behaviorally(art):
    """行为面正向对照：真调 wait_for_rows 轮询达标——ticks 全等 TICK_MS（运行期形态）。"""
    seq = iter([0, 0, 3])
    page = _FakePage()
    page.evaluate = lambda js, arg=None: next(seq)
    count = art.support.wait_for_rows(page, min_rows=3, timeout_ms=1000)
    assert count == 3
    assert page.ticks and set(page.ticks) == {art.support.TICK_MS}


# ===========================================================================
# 判据 3(b) · 单浏览器/单登录 fixture 结构（E.1.6）+ 行为通道形态
# ===========================================================================

def test_conftest_single_browser_single_login(fast_pkg):
    src = _read(fast_pkg, "conftest.py")
    tree = ast.parse(src)
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    scopes = []
    for name, fn in fns.items():
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "fixture":
                kw = {k.arg: k.value for k in d.keywords}
                if "scope" in kw and isinstance(kw["scope"], ast.Constant):
                    scopes.append((name, kw["scope"].value))
    assert scopes == [("sole_page", "module")], f"module-scope fixture 位不唯一：{scopes}"
    assert src.count("chromium.launch(") == 1, "launch 调用位 ≠ 1（每用例新浏览器风险）"
    for fwd in ("def page(sole_page)", "def context(page)", "def browser(page)"):
        assert fwd in src, f"缺单浏览器转发夹具 {fwd}"
    assert src.count("login(page, base_url=") == 1, "单登录判据破（login 调用位≠1）"
    assert "只做记录" in src and "ATLAS_FAST_LOGIN_INDICATOR" in src
    # 正向对照（否则结构判据对空文件恒真）：9 个契约 fixture 全部真实定义
    missing = [n for n in FAST_FIXTURE_NAMES if n not in fns]
    assert missing == []
    assert len(src.splitlines()) > 100


def test_sole_page_channel_shapes(art, monkeypatch):
    """gap9b B-3 会话通道形态（假 playwright，零真驱动零 socket，F-1 合规）：
    launch 恰一次；attach 需 CDP∧INTERACTIVE=1∧非 prod；MATCH 点名选页零位置兜底
    （缺/0 命中/多命中各自 EnvBlocked）；prod 恒不进 attach；attach 收尾只断连。"""
    conf = art.conftest
    sup = art.support

    def fresh_rec():
        return {"launch": [], "cdp": [], "context_kw": [], "pw_started": 0, "stop": 0,
                "browser_close": 0, "context_close": 0}

    # ① 缺省 launch：唯一一次、无头参数组、收尾关自己开的东西
    rec = fresh_rec()
    gen, _page = _drive_sole_page(conf, monkeypatch, rec)
    with pytest.raises(StopIteration):
        gen.send(None)
    assert len(rec["launch"]) == 1 and rec["cdp"] == []
    assert rec["launch"][0]["headless"] is True
    assert rec["browser_close"] == 1 and rec["context_close"] == 1 and rec["stop"] == 1

    # ② CDP 在场但没在场声明（INTERACTIVE 未置 1）→ 仍 launch（F-16 有意的冗余闸）
    rec2 = fresh_rec()
    monkeypatch.setenv("ATLAS_FAST_CDP", "http://stub-cdp")
    gen, _page = _drive_sole_page(conf, monkeypatch, rec2)
    with pytest.raises(StopIteration):
        gen.send(None)
    assert rec2["cdp"] == [] and len(rec2["launch"]) == 1, "在场闸失守：CDP 残留即 attach"

    # ③ attach 全条件：CDP+INTERACTIVE=1+MATCH 唯一命中；收尾绝不关人的浏览器
    rec3 = fresh_rec()
    monkeypatch.setenv("ATLAS_FAST_INTERACTIVE", "1")
    monkeypatch.setenv("ATLAS_FAST_ATTACH_MATCH", "/target")
    gen, page = _drive_sole_page(conf, monkeypatch, rec3,
                                 attach_pages=["http://ext/target", "http://ext/other"])
    assert rec3["cdp"] == ["http://stub-cdp"] and rec3["launch"] == []
    assert page.url == "http://ext/target", "点名匹配失守（未选 MATCH 命中页）"
    with pytest.raises(StopIteration):
        gen.send(None)
    assert rec3["browser_close"] == 0 and rec3["context_close"] == 0
    assert rec3["stop"] == 1, "attach 收尾只断连（pw.stop）——必须仍然停驱动"

    # ④⑤⑥ 零位置兜底三态：MATCH 缺 / 0 命中 / 多命中——各自 EnvBlocked 点名，绝不 pages[0]
    for desc, pages, match in (("缺MATCH", ["http://ext/a"], None),
                               ("0命中", ["http://ext/a"], "/zzz"),
                               ("多命中", ["http://ext/x1", "http://ext/x2"], "/x")):
        recx = fresh_rec()
        if match is None:
            monkeypatch.delenv("ATLAS_FAST_ATTACH_MATCH", raising=False)
        else:
            monkeypatch.setenv("ATLAS_FAST_ATTACH_MATCH", match)
        with pytest.raises(sup.EnvBlocked, match="attach"):
            _drive_sole_page(conf, monkeypatch, recx, attach_pages=pages)

    # ⑦ prod 恒不进 attach（B-3.4：落 launch + WARN，不给强制开关——形态取实盘契约）
    monkeypatch.setenv("ATLAS_FAST_ENV", "prod-de")
    rec7 = fresh_rec()
    gen, _page = _drive_sole_page(conf, monkeypatch, rec7)
    with pytest.raises(StopIteration):
        gen.send(None)
    assert rec7["cdp"] == [], "attach 恒禁生产失守"
    assert len(rec7["launch"]) == 1

    # 静态面：通道判定发生在驱动 start 之前（判据先行，不先起驱动再猜通道）
    conf_src = _read(art.pkg, "conftest.py")
    assert conf_src.index("session_channel(env_snapshot)") < \
        conf_src.index("sync_playwright().start()")


def test_get_page_attach_never_performs_login(art, monkeypatch):
    """B-3.3：attach 会话未登录 → EnvBlocked 点名「人工登录」，绝不在人的浏览器里执行
    登录动作（借来的会话替他登录=凭据动到别人的现场，比失败更坏）。正向对照=launch
    通道缺凭据走 EnvBlocked(缺配置)（登录链仍归自己管的浏览器）。"""
    conf = art.conftest
    sup = art.support
    gp = conf.get_page.__wrapped__

    # attach 通道 + 指示器缺失 → EnvBlocked 点名人工登录
    monkeypatch.setenv("ATLAS_FAST_LOGIN_INDICATOR", ".user-info")
    monkeypatch.setenv("ATLAS_FAST_CDP", "http://stub-cdp")
    monkeypatch.setenv("ATLAS_FAST_INTERACTIVE", "1")
    with pytest.raises(sup.EnvBlocked, match="人工登录"):
        gp(_FakePage(0), {})()
    # 正向对照：launch 通道（无 CDP）同场景走凭据/登录链而非 attach 点名
    monkeypatch.delenv("ATLAS_FAST_CDP")
    with pytest.raises(sup.EnvBlocked, match="缺配置"):
        gp(_FakePage(0), {})()


def test_playwright_absent_records_env_blocked_not_green(art, monkeypatch):
    """E.1.5 尾句（离线可测，scout 更正 2）：playwright 缺席→sole_page 当场 EnvBlocked，
    不假绿、不在 collection 期 import 崩整场。sys.modules 投毒 None 使
    `from playwright.sync_api import …` 必 ImportError。正向对照=通道形态测里 launch 分支
    真会走到 start（缺席分支不是「整个 UI 腿没接」的伪装）。"""
    import _thread  # noqa: F401  确认 stdlib 命名空间与 conftest 的 import 一致
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(art.support.EnvBlocked, match="playwright"):
        next(art.conftest.sole_page.__wrapped__(_FakeReq()))


# ===========================================================================
# 判据 3(c) · 截图调用只出现在失败/取证路径（F4，静态+行为两面）
# ===========================================================================

def _call_sites(tree, attr: str) -> list:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def test_screenshot_only_in_failure_leg_static_and_dynamic(fast_pkg, art, tmp_path):
    tree = ast.parse(_read(fast_pkg, "_atlas_fast.py"))
    shots, caps = _call_sites(tree, "screenshot"), _call_sites(tree, "_capture")
    in_except = any(c in ast.walk(h) for h in ast.walk(tree)
                    if isinstance(h, ast.ExceptHandler) for c in caps)
    # 负向+前提自证同屏：screenshot 恰 1 处、_capture 调用位恰 1 处且在 except 腿内
    # （调用位=0 正是 #139/#106「消费侧就绪但没接」的断链形态，必须红）
    assert len(shots) == 1 and len(caps) == 1 and in_except
    for p in _pkg_py(fast_pkg):
        if p.name != "_atlas_fast.py":
            assert "screenshot(" not in p.read_text(encoding="utf-8"), p.name

    # 行为面：成功步零截图（红线 #8）；失败步取证 + attachment **绝对路径**（F4 消费判据）
    runs = tmp_path / "runs.jsonl"
    page = _FakePage()
    art.support.set_current_page(page)
    try:
        w = art.support.RunWriter("m::shotcase", "env", path=runs)
        with w.step("GET ok"):
            pass
        assert page.shots == [] and "attachment" not in w.steps[0]
        with pytest.raises(AssertionError):
            with w.step("assert bad"):
                assert False, "真失败"
        att = w.steps[1]["attachment"]
        assert Path(att).is_absolute(), f"相对附件路径会按 Atlas root 解析悬空（F4）：{att}"
        assert art.support.ARTIFACTS_DIR_NAME in att
        assert page.shots and Path(page.shots[0]).is_absolute()
    finally:
        art.support.set_current_page(None)


# ===========================================================================
# 判据 3(d) · Atlas 兼容 runlog 写手 + E.1.5 离线半段（RunWriter→emit_allure）
# ===========================================================================

_ATLAS_RUN_KEYS = {"run_id", "case", "env", "mode", "status", "started_at",
                   "finished_at", "steps"}


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _allure_by_case(out_dir: Path) -> dict:
    by = {}
    for f in out_dir.glob("atlas-*-result.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        labels = {l["name"]: l["value"] for l in d.get("labels", [])}
        by[d["name"]] = (d["status"], (d.get("statusDetails") or {}).get("message", ""),
                         labels)
    return by


def test_runlog_writer_row_shape_and_allure_roundtrip(art, tmp_path):
    """F7 + F-1 裁决 1 离线映射：终态逐条核 + 无洗绿分支 + F-5 reason 原文保留。

    正向对照同测（lead 裁决 2 硬要求）：真 AssertionError→failed、真成功→passed、
    裸 NotImplementedError→unknown/failed——都不许被 env_blocked/skipped 支洗掉。
    """
    sup = art.support
    runs = tmp_path / "runs.jsonl"

    # ① passed：行键面 = Atlas recorder；close 缺省**不写 reason 键**（C9 纪律逐字对齐）
    w = sup.RunWriter("m::pass", "xchk-env", path=runs)
    with w.step("GET /api/a"):
        pass
    w.close("passed")
    row = _rows(runs)[-1]
    assert set(row) == _ATLAS_RUN_KEYS, f"行键面与 Atlas recorder 不齐：{sorted(row)}"
    assert set(row["steps"][0]) == {"step", "op", "ok", "dur_ms"}
    assert "reason" not in row

    # ② failed + 真 AssertionError（正向对照之一）
    w = sup.RunWriter("m::assertion", "xchk-env", path=runs)
    with pytest.raises(AssertionError):
        with w.step("assert ok"):
            assert False, "resultCode 不符"
    assert w.steps[0]["fail_class"] == "assertion"
    w.close("failed")

    # ③ env_blocked：NotWired 未接线（裁决 2 类型级归因，不猜文本）
    w = sup.RunWriter("m::notwired", "xchk-env", path=runs)
    err = None
    try:
        with w.step("ui open_header"):
            raise sup.NotWired("未接线：OrderHeader.action（元素：search），需人审接入")
    except sup.EnvBlocked as e:
        err = e
    assert isinstance(err, sup.EnvBlocked) and issubclass(sup.NotWired, sup.EnvBlocked)
    assert w.steps[0]["fail_class"] == "env_blocked"
    w.close("env_blocked", reason=sup.framework_reason(err))
    assert _rows(runs)[-1]["reason"] == \
        "未接线：OrderHeader.action（元素：search），需人审接入"

    # ④ 对抗样本：env_blocked 但 reason 确为空——泛化回落**只在这条**才可读（F-5/scout2）
    w = sup.RunWriter("m::blocked-noreason", "xchk-env", path=runs)
    w.close("env_blocked")

    # ⑤ 裸 NotImplementedError：真缺陷不得被洗成 env_blocked（F-2 反面，正向对照之二）
    w = sup.RunWriter("m::nie", "xchk-env", path=runs)
    with pytest.raises(NotImplementedError):
        with w.step("ui broken"):
            raise NotImplementedError("代码缺陷")
    assert w.steps[0]["fail_class"] == "unknown"
    w.close("failed")

    # ⑥ watchdog 语义（F6③）：close 幂等——timeout 行落定后 close("passed") 不许翻绿
    w = sup.RunWriter("m::timeout", "xchk-env", path=runs)
    before = len(_rows(runs))
    w.close(FLOW_STATUS_TIMEOUT, reason="watchdog CASE_TIMEOUT=600s")
    w.close("passed")
    rows = _rows(runs)
    assert len(rows) == before + 1, "close 非幂等：超时行被追加/改写=可翻绿"
    assert [r for r in rows if r["case"] == "m::timeout"][0]["status"] == "timeout"

    # ⑦ waiting / ⑧ interrupted
    w = sup.RunWriter("m::waiting", "xchk-env", path=runs)
    w.close("waiting", reason="等待人工审批单号")
    w = sup.RunWriter("m::interrupted", "xchk-env", path=runs)
    w.close("interrupted")

    # ---- 消费侧：emit_allure 逐条核映射（产物=Atlas 兼容的第二实证面）----
    res = emit_allure(runs, tmp_path / "allure")
    rows = _rows(runs)
    assert res["results"] == len(rows) and res["skipped_malformed_lines"] == 0
    by = _allure_by_case(tmp_path / "allure")
    assert by["m::pass"][0] == "passed"                      # 真成功→passed
    assert by["m::assertion"][0] == "failed" and \
        by["m::assertion"][2]["atlas.fail_class"] == "assertion"   # 真失败→failed，不洗
    st, msg, labels = by["m::notwired"]
    assert st == "skipped" and labels["atlas.blocked"] == "true"
    assert msg.startswith("未接线：OrderHeader.action"), f"F-5 点名原文必须保留：{msg}"
    st2, msg2, _ = by["m::blocked-noreason"]
    assert st2 == "skipped" and "environment blocked" in msg2  # 空 reason 才允许泛化回落
    assert by["m::nie"][0] == "broken"  # 裸缺陷不被 skipped 化（fail-loud）
    assert by["m::timeout"][0] == "broken"  # 掐表≠操作族终态，绝不 skipped/passed
    assert by["m::waiting"][2].get("atlas.waiting") == "true"
    assert "等待人工审批单号" in by["m::waiting"][1], \
        f"F-23(A)：waiting 行 message 必须保留 reason 文案原文（不许空占位）"
    assert by["m::interrupted"][2].get("atlas.interrupted") == "true"
    assert sum(1 for t in by.values() if t[0] == "passed") == 1, "洗绿计数防线"

    # 结构面（F-15：经 import 的产物模块属性读，不复制表值域）
    sup2 = sup
    assert all(v != "passed" for _, v in sup2.FAIL_CLASS_MAP), "映射表出现 passed=洗绿入口"
    assert "skipped" not in sup2.RUN_STATUS_VOCAB
    assert {v for _, v in sup2.FAIL_CLASS_MAP} <= {f.value for f in FailureClass}
    assert set(sup2.RUN_STATUS_VOCAB) >= {f.value for f in FailureClass
                                          if f.value in ("env_blocked", "interrupted",
                                                          "waiting")}
    assert FLOW_STATUS_TIMEOUT in sup2.RUN_STATUS_VOCAB
    assert sup2._TRANSIENT_MARKS == atlas_waits._TRANSIENT_MARKS  # 盘产物对引擎真相
    long_msg = "缺配置：" + "长" * 300
    assert len(sup.framework_reason(sup.EnvBlocked(long_msg))) == sup.REASON_MAX_LEN == 200


def test_runlog_reason_boundary_f5(art):
    """F-5 三条：reason 只取框架点名文案；str(exc)/HTTP 正文/URL query 一律不落盘。"""
    sup = art.support
    assert sup.framework_reason(sup.EnvBlocked("缺配置：ATLAS_FAST_BASE_URL")) == \
        "缺配置：ATLAS_FAST_BASE_URL"
    assert sup.framework_reason(sup.AwaitingHuman("等人工审批")) == "等人工审批"
    # 对抗：外部形态文本（非框架类型）→ 空串（不写=强于写了再洗）
    assert sup.framework_reason(ValueError('HTTP 正文 {"token":"abc"} URL?code=401')) == ""
    st, reason = art.conftest._runlog_status_reason([("call", AssertionError("body…"))])
    assert (st, reason) == ("failed", "")  # 断言消息走 step 名，不进 run reason（F-5.3）
    st, reason = art.conftest._runlog_status_reason(
        [("call", sup.EnvBlocked("缺前置键 doc_no"))])
    assert st == "env_blocked" and reason == "缺前置键 doc_no"


# ===========================================================================
# 判据 3(e) · 无硬编码业务字面量（红线 #11；金丝雀证「值来自参数/派生通道」）
# ===========================================================================

def test_no_business_literals_in_artifacts(fast_pkg, flow_fast_pkg):
    # 负向：业务 token 在框架文件+用例层+README 全盘 0。
    # atlas_pages.py 是 components 知识库的**只读派生**（选择器合法通道，F-10 闸门管成员
    # 资格），不在本扫描面——金丝雀另测钉「选择器只出现在这一处」。
    for pkg in (fast_pkg, flow_fast_pkg):
        files = [p for p in _pkg_py(pkg) if p.name != "atlas_pages.py"]
        if (pkg / "README.md").exists():
            files.append(pkg / "README.md")
        for p in files:
            src = p.read_text(encoding="utf-8")
            low = src.lower()
            for tok in _BUSINESS_TOKENS:
                assert tok.lower() not in low, f"业务字面量 {tok!r} 落进 {p.name}"
        # 框架文件零 URL 字面量（内网域防线）；正向对照：文档 base 的 echo 只准出现在用例层
        for name in ("_atlas_fast.py", "conftest.py", "_atlas_support.py"):
            assert not re.search(r"https?://[^\s\"']+", _read(pkg, name)), name
    doc_tmod = _test_module(fast_pkg).read_text(encoding="utf-8")
    assert "api.example.com" in doc_tmod, \
        "正向对照失败：文档源 base 应在用例层以回声形态存在（扫描面是活的）"
    assert "api.example.com" not in _read(fast_pkg, "conftest.py")
    # 金丝雀：选择器值经参数→派生通道走，出现处恰 1（=0 即派生断链恒真，F-10 兑现面）
    pages_src = _read(flow_fast_pkg, "atlas_pages.py")
    assert pages_src.count(_CANARY) == 1, "金丝雀未派生进页对象库（通道断链）"
    for p in _pkg_py(flow_fast_pkg):
        if p.name != "atlas_pages.py":
            assert _CANARY not in p.read_text(encoding="utf-8"), \
                f"选择器值漏进 {p.name}（业务值进框架=红线 #11）"
    # 鉴权值走 ${VAR}：文档包用例层 isc-app 头必须是占位形态（C4① 口径承接）
    assert re.search(r"\$\{ISC_APP_HEADER\}", doc_tmod), "鉴权头未外部化为 ${VAR} 形态"


# ===========================================================================
# 判据 4 · 未解析占位符显式报错（绝不把 ${VAR} 当字面量发出）
# ===========================================================================

def test_unresolved_placeholder_raises_explicitly(art, monkeypatch):
    sup = art.api_sup
    with pytest.raises(KeyError, match="XCHK_MISSING_K"):
        sup.resolve_placeholders("/api/${XCHK_MISSING_K}", env={})
    # 正向对照：已解析值照常通过（闸门不误伤正常路径，#115）
    assert sup.resolve_placeholders("/api/${OK}", env={"OK": "42"}) == "/api/42"

    # call_api：占位未解析→抛错且 urlopen **一次没被调**（sentinel 替换发送层——
    # F-1 合规：不起服务不开 socket）
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("占位未解析绝不该走到发送层")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(KeyError):
        sup.call_api(method="GET", path="/pets/${XCHK_MISSING_K}", base_url="http://stub")
    assert calls == []
    with pytest.raises(KeyError):
        sup.call_api(method="POST", path="/pets", base_url="http://stub",
                     headers={"k": "${XCHK_H}"}, body={"n": "${XCHK_B}"})
    assert calls == []

    class _Resp:
        status = 200

        def read(self):
            return b'{"resultCode":"0"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def canned(req, timeout=None):
        calls.append(req)
        return _Resp()
    monkeypatch.setattr("urllib.request.urlopen", canned)
    out = sup.call_api(method="GET", path="/pets/${OK}", base_url="http://stub",
                       env={"OK": "7"})
    assert out.ok and out.data["resultCode"] == "0"
    assert len(calls) == 1  # 正向对照：解析齐了的请求**确实**走到发送层


# ===========================================================================
# 判据 5 专项：F-7 两义分立（raise 消息面）与登录权威链
# ===========================================================================

def _raise_messages(src: str) -> list[str]:
    """只取 raise 语句里的字符串常量——docstring 引用的「禁止 or 文案」字样不算违规。"""
    msgs: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Raise) and node.exc is not None:
            msgs.extend(c.value for c in ast.walk(node.exc)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str))
    return msgs


def test_login_two_meanings_f7_raise_form_and_order(art, monkeypatch):
    pages, sup = art.pages, art.support
    # 义一（配置缺失，先查 env）：凭据空 → EnvBlocked 点名 env 键，且**不是** NotWired
    with pytest.raises(sup.EnvBlocked, match="缺配置") as ei:
        pages.login(None, base_url="https://api.example.com", account="", password="")
    assert not isinstance(ei.value, sup.NotWired)  # 两义不得互串
    assert "ATLAS_FAST_ACCOUNT" in str(ei.value)
    monkeypatch.delenv("ATLAS_FAST_LOGIN_INDICATOR", raising=False)
    with pytest.raises(sup.EnvBlocked, match="ATLAS_FAST_LOGIN_INDICATOR"):
        pages.login(None, base_url="https://api.example.com", account="a", password="p")
    # 义二（动作未接线，env 齐后才判）：NotWired 点名元素/组件
    monkeypatch.setenv("ATLAS_FAST_LOGIN_INDICATOR", ".user-info")
    with pytest.raises(sup.NotWired, match="登录未接线"):
        pages.login(None, base_url="https://api.example.com", account="a", password="p")
    # or 文案=0：判 raise 消息（docstring 引述禁止语料不算违规）；两义文案都在位（非恒真）
    msgs = _raise_messages(_read(art.pkg, "conftest.py")) + \
        _raise_messages(_read(art.pkg, "atlas_pages.py"))
    joined = "|".join(msgs)
    assert "未接线或未配置" not in joined and "未配置或未接线" not in joined
    assert any("缺配置" in m for m in msgs) and any("登录未接线" in m for m in msgs)
    # 求值次序（先 env 后接线）：login 体内 EnvBlocked raise 行 < NotWired raise 行
    body = _read(art.pkg, "atlas_pages.py")
    body = body[body.index("def login"):]
    env_line = min(n.lineno for n in ast.walk(ast.parse(body))
                   if isinstance(n, ast.Raise) and "缺配置" in ast.dump(n))
    wire_line = min(n.lineno for n in ast.walk(ast.parse(body))
                    if isinstance(n, ast.Raise) and "未接线" in ast.dump(n))
    assert env_line < wire_line, "F-7 求值次序：先查 env 再判接线"


def test_get_page_indicator_authority_and_branches(art, monkeypatch):
    """F1 权威判据链：指示器实查放行；磁盘标记只记录；两义分立；假半登录不放行。"""
    sup, conf = art.support, art.conftest
    gp = conf.get_page.__wrapped__

    # 缺配置支：指示器未设 → EnvBlocked，先于任何页面实查（locator 零调用）
    page = _FakePage(indicator_hits=1)
    with pytest.raises(sup.EnvBlocked, match="ATLAS_FAST_LOGIN_INDICATOR"):
        gp(page, {})()
    assert page.locator_calls == []

    # 实查命中支：已登录复用页面，**不会走到 login**（真 atlas_pages.login 在场会抛
    # NotWired——返回了页面即证明未触登录；磁盘标记不参与放行判定）
    monkeypatch.setenv("ATLAS_FAST_LOGIN_INDICATOR", ".user-info")
    ret = gp(page, {"_logged_in_account": "someone"})()
    assert ret is page and page.locator_calls == [".user-info"]

    # 未登录+缺凭据 → EnvBlocked（义一）。磁盘标记挂着「u」也必须被实查否决（不放行）
    with pytest.raises(sup.EnvBlocked, match="缺配置"):
        gp(_FakePage(0), {"_logged_in_account": "u"})()

    # 未登录+env 齐 → 走真实 atlas_pages.login（未接线 → NotWired，义二）
    monkeypatch.setenv("ATLAS_FAST_ACCOUNT", "u")
    monkeypatch.setenv("ATLAS_FAST_PASSWORD", "s")
    monkeypatch.setenv("ATLAS_FAST_BASE_URL", "https://api.example.com")
    with pytest.raises(sup.NotWired, match="登录未接线"):
        gp(_FakePage(0), {})()

    # 假半登录防线：login「成功」返回但指示器仍无 → RuntimeError（failed 腿），不放行
    monkeypatch.setattr(art.pages, "login", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="指示器仍未出现"):
        gp(_FakePage(0), {})()

    # 正向对照：login 后指示器出现 → 放行且磁盘标记只作记录
    def _ok_login(page, *, base_url, account, password):
        page.indicator_hits = 1
    monkeypatch.setattr(art.pages, "login", _ok_login)
    p6 = _FakePage(0)
    ctx6 = {}
    assert gp(p6, ctx6)() is p6
    assert ctx6["_logged_in_account"] == "u"


# ===========================================================================
# E.1.11 / F-2：fast 产物 raise NotImplementedError=0（同测 fast=False 对照）
# ===========================================================================

def test_fast_artifacts_zero_nie_slow_control_has_it(fast_pkg, flow_fast_pkg,
                                                     slow_pkg, slow_flow_pkg):
    for pkg in (fast_pkg, flow_fast_pkg):
        blob = _artifact_text(pkg)
        assert "raise NotImplementedError" not in blob, \
            "快包残留裸 NotImplementedError（未接线必须走 NotWired，裁决 2）"
        assert "NotWired(" in blob, "快包未接线出口缺失（扫描对象=空壳则恒真）"
        assert not any("pytest.mark.skip" in p.read_text(encoding="utf-8")
                       for p in _pkg_py(pkg)), "快包 UI 腿不得 skip 占位"
    # 正向对照：同流程 fast=False 旧形态两处都在——判据不是「仓库根本没有这些串」
    assert "raise NotImplementedError" in _artifact_text(slow_pkg) + \
        _artifact_text(slow_flow_pkg)
    assert "pytest.mark.skip" in _artifact_text(slow_flow_pkg)


# ===========================================================================
# F5 跨批链（emit-impl #3 交接假设 1：渲染只写 plan_ctx[k]，落盘归 conftest teardown）
# ===========================================================================

def test_plan_ctx_cross_batch_persistence(art, tmp_path, monkeypatch):
    conf, sup = art.conftest, art.support
    plan = tmp_path / "plan_ctx.json"
    monkeypatch.setattr(conf, "PLAN_CTX_PATH", plan)
    gen = conf.plan_ctx.__wrapped__()
    ctx1 = next(gen)
    ctx1["doc_no"] = "SO-1"
    ctx1["_tc1_ok"] = "1"          # 前置标记用标量文本（safe_persist 闸：bool 拒落）
    ctx1["flag_true"] = True       # bool 刻意拒（#117 非空标量纪律）
    ctx1["huge"] = "响应正文" * 200  # 超长文本（疑似响应正文）拒落且值不回显
    ctx1["__internal"] = 1         # 双下划线内部键不得落盘
    with pytest.raises(StopIteration):
        next(gen)
    persisted = plan.read_text(encoding="utf-8")
    saved = json.loads(persisted)
    assert set(saved) == {"doc_no", "_tc1_ok"}, f"白名单/标量闸失效：{sorted(saved)}"
    assert "响应正文" not in persisted, "值回显防线破（不可控文本进跨批文件）"
    # 批 2（新进程语义=重新 load）：前一批写的键 require_ctx 能读到（分批跑测不丢结论）
    ctx2 = next(conf.plan_ctx.__wrapped__())
    assert sup.require_ctx(ctx2, "doc_no") == "SO-1"
    # 缺前置：EnvBlocked 点名键 + 补救路径（F5：绝不静默跳过）
    with pytest.raises(sup.EnvBlocked, match="缺前置键 doc_zzz"):
        sup.require_ctx(ctx2, "doc_zzz")
    with pytest.raises(sup.EnvBlocked, match="plan_ctx.json"):
        sup.require_ctx({}, "whatever")
    # 正向对照：坏 JSON 不崩链（空起点显式，而非半路裸崩）
    plan.write_text("{ 坏字节", encoding="utf-8")
    assert sup.load_plan_ctx(plan) == {}


def test_plan_ctx_dict_cross_batch_warn_then_envblocked_f27(art, tmp_path, monkeypatch,
                                                             capsys):
    """F-27 第 4 条（lead 裁：现状形态正确，渲染层不改）——dict 跨批的行为链整条钉死：

    `plan_ctx[k]=result.data` 直写 dict → save 时 safe_persist **点名键拒落 + WARN 不回显值**
    → 后继批 `require_ctx` 显式 EnvBlocked。坏形态=键被悄悄丢掉、后继读到空值仍通过
    （把「正文不落盘」实现成「结论不成立」）。与 F-23(C) 配对：**标量（str/int/float）
    跨批必须真能传过去**——否则闸门写宽了没人发现。
    """
    conf, sup = art.conftest, art.support
    plan = tmp_path / "plan_ctx.json"
    monkeypatch.setattr(conf, "PLAN_CTX_PATH", plan)
    sentinel = "XCHK-DICT-SENTINEL-9"
    gen = conf.plan_ctx.__wrapped__()
    ctx = next(gen)
    ctx["resp_a"] = {"orderNo": sentinel}   # dict（疑似响应正文）——必须被闸拦
    ctx["order_no"] = "SO-7"                # str 标量
    ctx["count"] = 12                       # int 标量
    ctx["rate"] = 0.5                       # float 标量
    with pytest.raises(StopIteration):
        next(gen)
    persisted = plan.read_text(encoding="utf-8")
    assert json.loads(persisted) == {"order_no": "SO-7", "count": 12, "rate": 0.5}, \
        f"F-23(C) 放行对照破：标量跨批必须真传过去——落盘实象 {persisted}"
    # WARN 点名键，且不回显值（sentinel 既不进文件也不进 WARN 行）
    warn_out = capsys.readouterr().out
    warn_lines = [l for l in warn_out.splitlines() if "resp_a" in l]
    assert warn_lines, "dict 被拒落必须 WARN 点名键（静默丢键=坏形态）"
    assert sentinel not in persisted, "正文值漏进 plan_ctx.json（A.1 收口面破）"
    assert sentinel not in warn_out, "WARN 回显了值（不可控文本进日志）"
    # 后继批：标量链没断；dict 键 require_ctx 显式 EnvBlocked 点名——绝不 None 过
    ctx2 = next(conf.plan_ctx.__wrapped__())
    assert sup.require_ctx(ctx2, "count") == 12
    with pytest.raises(sup.EnvBlocked, match="resp_a"):
        sup.require_ctx(ctx2, "resp_a")


# ===========================================================================
# E.1.7：watchdog 假时钟（timeout 行 + reason 点名 CASE_TIMEOUT；不写 skipped）
# ===========================================================================

def test_watchdog_fake_clock_timeout_row_never_flips(art, tmp_path, monkeypatch):
    import _thread
    conf, sup = art.conftest, art.support
    runs = tmp_path / "runs.jsonl"
    hits = []
    monkeypatch.setattr(_thread, "interrupt_main", lambda: hits.append(1))
    monkeypatch.setenv("CASE_TIMEOUT", "0.05")
    w = sup.RunWriter("m::wd", "xchk-env", path=runs)
    gen = conf._case_watchdog.__wrapped__(w)
    next(gen)
    time.sleep(0.4)
    with pytest.raises(StopIteration):
        gen.send(None)
    rows = _rows(runs)
    assert [r["status"] for r in rows] == ["timeout"], f"看门狗落行异常：{rows}"
    assert "CASE_TIMEOUT" in rows[0]["reason"]
    assert hits, "interrupt_main 未被调用——超时腿只写行不打断挂死调用=半成品防线"
    # close 幂等防线在盘产物生效：不许把 timeout 翻绿
    w.close("passed")
    assert [r["status"] for r in _rows(runs)] == ["timeout"]
    # 正向对照：CASE_TIMEOUT<=0 关闭看护，正常收尾不误写 timeout 行
    monkeypatch.setenv("CASE_TIMEOUT", "0")
    w2 = sup.RunWriter("m::off", "xchk-env", path=tmp_path / "runs2.jsonl")
    g2 = conf._case_watchdog.__wrapped__(w2)
    next(g2)
    with pytest.raises(StopIteration):
        g2.send(None)
    w2.close("passed")
    assert [r["status"] for r in _rows(tmp_path / "runs2.jsonl")] == ["passed"]


# ===========================================================================
# E.1.8：写腿默认闸停（按类型不看文本）；放行=显式 env
# ===========================================================================

def test_write_gate_blocks_by_op_not_text(art, monkeypatch):
    conf, sup = art.conftest, art.support

    def gate(op):
        req = _FakeReqNode(types.SimpleNamespace(__fast_case__={"op": op}))
        return conf._write_gate.__wrapped__(req, None)

    with pytest.raises(sup.EnvBlocked, match="ATLAS_FAST_ALLOW_WRITES"):
        next(gate("write"))  # 未置位=默认拦停
    next(gate("api"))  # 正向对照：读腿照常放行（闸门不误伤）
    monkeypatch.setenv("ATLAS_FAST_ALLOW_WRITES", "0")
    with pytest.raises(sup.EnvBlocked, match="写操作保护"):
        next(gate("write"))  # 显式 0 与未置位同语义（F-13）
    monkeypatch.setenv("ATLAS_FAST_ALLOW_WRITES", "1")
    next(gate("write"))  # 放行=人的显式 env 决定后 body 才可达


def _warn_missing_elements(line: str) -> list[str]:
    """F-27 §1 三要素核：①CDP 在场 ②prod- 前缀 ③改走 launch/自起浏览器**并**用凭据登录。
    意图被降级要说清降成了什么——③ 只说「落 launch」不说登录形态同样是要素缺失。"""
    e1 = "CDP" in line
    e2 = "prod-" in line
    e3 = ("launch" in line or "自起" in line) and ("凭据" in line or "登录" in line)
    return [n for n, ok in (("①已设 ATLAS_FAST_CDP", e1),
                            ("②ATLAS_FAST_ENV 为 prod- 前缀故拒 attach", e2),
                            ("③改走自起浏览器并用 env 凭据登录", e3)) if not ok]


def test_prod_cdp_warn_three_elements_f27(art, monkeypatch, capsys):
    """F-27 第 1 条硬要求：prod+CDP 维持 launch+WARN，但 WARN 三要素缺一即点名。

    行为面取证（非模板文本）：真实驱动 sole_page 到该分支，captured stdout 里那一条
    WARN 逐要素核。放行对照：同 CDP 非 prod 环境**不得**出这条 WARN（尺不误伤）。"""
    conf = art.conftest
    monkeypatch.setenv("ATLAS_FAST_CDP", "http://stub-cdp")
    monkeypatch.setenv("ATLAS_FAST_INTERACTIVE", "1")
    monkeypatch.setenv("ATLAS_FAST_ENV", "prod-de")
    rec = {"launch": [], "cdp": [], "context_kw": [], "pw_started": 0, "stop": 0,
           "browser_close": 0, "context_close": 0}
    gen, _page = _drive_sole_page(conf, monkeypatch, rec)
    with pytest.raises(StopIteration):
        gen.send(None)
    out = capsys.readouterr().out
    warn = [l for l in out.splitlines() if "ATTACH-DISALLOWED-IN-PROD" in l]
    assert len(warn) == 1, f"WARN 行应恰一（实得 {len(warn)}）：{out}"
    missing = _warn_missing_elements(warn[0])
    assert missing == [], f"F-27 §1 文案三要素缺失：{missing}——实际：{warn[0]}"

    # 放行对照：sit 环境同样带 CDP+INTERACTIVE → attach 正常走，绝不冒这条 WARN
    rec2 = {k: ([] if isinstance(v, list) else 0) for k, v in rec.items()}
    rec2["context_kw"] = []
    monkeypatch.setenv("ATLAS_FAST_ENV", "sit-1")
    monkeypatch.setenv("ATLAS_FAST_ATTACH_MATCH", "/target")
    gen2, _p = _drive_sole_page(conf, monkeypatch, rec2)
    with pytest.raises(StopIteration):
        gen2.send(None)
    assert rec2["cdp"] == ["http://stub-cdp"]  # 非 prod 才真借道
    out2 = capsys.readouterr().out
    assert "ATTACH-DISALLOWED-IN-PROD" not in out2, "WARN 在非 prod 也响=尺失真"

    # 对抗样本（F-23 尺的活性）：缺 ③ 后半句/缺 ② 的形态必须被点名
    assert _warn_missing_elements("WARN: CDP 在场但环境为 prod- 前缀，落 launch 通道") == \
        ["③改走自起浏览器并用 env 凭据登录"]
    assert "①已设 ATLAS_FAST_CDP" in _warn_missing_elements("环境为 prod- 前缀")


# ===========================================================================
# E.1.10 产物用例层门禁 + 扫描器活性对抗
# ===========================================================================

def test_review_code_gate_on_fast_case_layer(fast_pkg, tmp_path):
    """盘产物用例层过 atlas review-code --fail-under 75 rc0；掺哨兵必须 rc1（尺是活的）。"""
    import shutil
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    src = _test_module(fast_pkg)
    shutil.copy(src, scan_dir / src.name)
    rc = main(["review-code", str(scan_dir), "--format", "json",
               "--fail-under", "75", "--out", str(tmp_path / "rc-ok")])
    assert rc == 0, "快模式用例层未过质量门禁（E.1.10）"
    (scan_dir / "test_sentinel_adversary.py").write_text(
        "def test_sentinel():\n"
        "    try:\n"
        "        assert 1 == 2\n"
        "    except Exception:\n"
        "        pass\n", encoding="utf-8")
    rc2 = main(["review-code", str(scan_dir), "--format", "json",
                "--fail-under", "75", "--out", str(tmp_path / "rc-bad")])
    assert rc2 == 1, "掺入 GREEN-001 哨兵仍 rc0=扫描器对产物恒真（门禁判据失效）"


# ===========================================================================
# lead 增补类 1 · 契约点名的符号/键必须在代码里真存在（可重跑核对）
# ===========================================================================

def test_contract_named_symbols_landed_surface_exists(fast_pkg):
    """gap9 C.1/C.2 + gap9b B-3 点名的符号在**盘产物/渲染层**真实存在（grep 式逐名核）。

    这类缺陷不会让任何测变红：契约写着、代码没有、测对着代码写，三方都「通过」
    （lead 2026-11-09 实盘的 session_channel/safe_persist 教训——当时确实三处 grep 皆空；
    #14 已落盘，本测把这类「契约-代码-测试三方自洽空洞」做成常驻尺）。#14 交割若改名，
    本测红即信号。
    """
    from atlas.generator import emit_pytest
    for sym in ("FAST_SUPPORT_MODULE_NAME", "FastEmitError", "emit_fast_support_module",
                "emit_fast_conftest_module", "write_fast_artifacts", "FAST_FIXTURE_NAMES",
                "ENV_KEYS"):
        assert hasattr(emit_fast, sym), f"emit_fast 缺契约符号 {sym}"
    for sym in ("emit_pytest_package", "render_test_module", "emit_pages_module",
                "_specs_from_flow", "_pascal", "_fast_ui_gates", "PytestEmitError"):
        assert hasattr(emit_pytest, sym), f"emit_pytest 缺契约符号 {sym}"
    # 盘产物底座必须暴露 C.1「产物侧符号」+ gap9b B-3 单点——渲染层/测只认这些名
    support_src = _read(fast_pkg, "_atlas_fast.py")
    defined = {n.name for n in ast.parse(support_src).body
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assigns = {t.id for n in ast.parse(support_src).body if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}
    missing = [s for s in ("EnvBlocked", "NotWired", "AwaitingHuman", "RunWriter",
                           "fast_case", "require_ctx", "load_plan_ctx", "save_plan_ctx",
                           "safe_persist", "launch_kwargs", "classify_fail_class",
                           "framework_reason", "session_channel", "attach_denied_by_prod",
                           "wait_for_rows", "wait_for_mask_gone", "wait_for_vue_field",
                           "wait_for_url_change", "set_current_page", "current_page")
               if s not in defined]
    missing += [s for s in ("RUN_STATUS_VOCAB", "TICK_MS", "_TRANSIENT_MARKS",
                            "FAIL_CLASS_MAP", "REASON_MAX_LEN", "FLOW_STATUS_TIMEOUT",
                            "ARTIFACTS_DIR_NAME") if s not in assigns]
    assert missing == [], f"盘产物缺契约符号：{missing}"
    # conftest 引用的底座名字全部可解析（import 面断链=收集期 NameError，F-9 同族）
    conf_src = _read(fast_pkg, "conftest.py")
    for node in ast.walk(ast.parse(conf_src)):
        if isinstance(node, ast.ImportFrom) and node.module == "_atlas_fast":
            for a in node.names:
                assert a.name in defined or a.name in assigns, \
                    f"conftest 引用底座不存在的名：{a.name}"
    # attach 点名选页单点在 conftest（_attach_pick_page），且**代码里**零位置索引兜底。
    # 判据走 AST 而非裸文本：模板 docstring 明文把 `pages[0]` 当「弃用示例」写在文字里，
    # 文本扫会误伤——只有 ast.Subscript(pages/contexts[0]) 才是真正的位置取页兜底（B-3.1）。
    conf_defined = {n.name for n in ast.parse(conf_src).body
                    if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert "_attach_pick_page" in conf_defined
    pos_subscripts = []
    for node in ast.walk(ast.parse(conf_src)):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value == 0:
            base = node.value
            base_name = getattr(base, "attr", None) or getattr(base, "id", None)
            if base_name in ("pages", "contexts"):
                pos_subscripts.append(f"{base_name}[0]@L{node.lineno}")
    assert pos_subscripts == [], f"B-3.1 位置索引选页兜底复现：{pos_subscripts}"


def test_contract_env_keys_really_read_by_artifacts(fast_pkg, flow_fast_pkg):
    """C.1 ENV_KEYS 的键必须在盘产物里真被读（名单=旋钮，不是愿望清单）。"""
    for pkg in (fast_pkg, flow_fast_pkg):
        read = _env_keys_read(_read(pkg, "conftest.py"), _read(pkg, "_atlas_fast.py"))
        not_read = {k for k in ENV_KEYS if k not in read}
        assert not_read == set(), f"ENV_KEYS 声明了却无人读（旋钮空转，恒真判据温床）：{not_read}"


# ===========================================================================
# lead 增补类 3 · dispatch pop 名单 ⊆ 模板实读键（双向）
# ===========================================================================

_UNATTENDED_KNOBS = re.compile(r"^ATLAS_FAST_(CDP|INTERACTIVE|ALLOW_WRITES)")
# ATTACH_MATCH 有意不在危险面：无 CDP+INTERACTIVE 同场它不启动任何东西（B-3.5 pop 三键）。


def test_dispatch_strip_list_bidirectional_with_template(fast_pkg):
    """双向核：①模板实读的无人值守敏感键 ⊆ pop 名单（漏一个=attach/写放行带进 cron，
    这是危险方向）；②pop 名单里已在产物读取的键确实被产物使用（纵深≠已生效，
    ATLAS_FAST_INTERACTIVE 属 #13 在途——允许「列了暂未读」，禁止「读了没列」）。"""
    conf_src = _read(fast_pkg, "conftest.py") + _read(fast_pkg, "_atlas_fast.py")
    read = _env_keys_read(conf_src)
    dangerous_read = {k for k in read if _UNATTENDED_KNOBS.match(k)}
    missing_from_strip = dangerous_read - set(dispatch.FAST_ENV_KEYS_STRIPPED)
    assert missing_from_strip == set(), \
        f"模板实读但 pop 名单缺失（无人值守泄漏面）：{missing_from_strip}"
    assert dispatch.FAST_CDP_ENV_KEY in read, "CDP 通道键产物必须真读（名单锚点）"
    assert "ATLAS_FAST_ALLOW_WRITES" in read, "写闸产物必须真读该键（_write_gate 生效面）"
    # 纵深项按在途分态：名单可超前于实读（#13 补 INTERACTIVE 前这是有意余量，不算红）
    ahead = set(dispatch.FAST_ENV_KEYS_STRIPPED) - read
    assert ahead <= {"ATLAS_FAST_INTERACTIVE"}, f"超前项只许已登记的在途键：{ahead}"


def test_child_env_and_guard_actually_strip(fast_pkg):
    """机制面：child_env 真 pop 三键、guard_fast_cdp 真 raise（显式 raise 不靠 assert，C1-C）。"""
    base = {"PATH": os.environ.get("PATH", ""), "ATLAS_FAST_CDP": "http://x:9222",
            "ATLAS_FAST_INTERACTIVE": "1", "ATLAS_FAST_ALLOW_WRITES": "1",
            "DEMO_USERNAME": "keepme"}
    env = dispatch.child_env(fast_pkg.parents[1], base=base)
    for k in dispatch.FAST_ENV_KEYS_STRIPPED:
        assert k not in env, f"child_env 未剔除 {k}（cron 最小 env 漏面）"
    assert env.get("DEMO_USERNAME") == "keepme"  # 正向对照：只剥该剥的
    with pytest.raises(ValueError, match="ATLAS_FAST_CDP"):
        dispatch.guard_fast_cdp(["--root", "x", "ATLAS_FAST_CDP=http://y"])
    dispatch.guard_fast_cdp(["--case-timeout", "600"])  # 正向对照：干净 argv 放行


# ===========================================================================
# E.1.9 产物依赖面（盘产物 import 扫描；正向对照：playwright 真实在场=扫描面活着）
# ===========================================================================

_ALLOWED_ROOTS = {"__future__", "os", "re", "json", "threading", "time", "uuid",
                  "contextlib", "datetime", "pathlib", "msvcrt", "fcntl", "_thread",
                  "importlib", "sys", "urllib", "pytest", "playwright",
                  "_atlas_fast", "_atlas_support", "atlas_pages"}


def test_import_surface_selfcontained(fast_pkg, flow_fast_pkg):
    found: set[str] = set()
    for pkg in (fast_pkg, flow_fast_pkg):
        for p in _pkg_py(pkg):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    roots = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    roots = [(node.module or "").split(".")[0]] if node.level == 0 else []
                else:
                    continue
                for r in roots:
                    if r:
                        found.add(r)
                        assert r in _ALLOWED_ROOTS, f"{p.name} 依赖越界根 {r!r}（F9）"
        assert not re.search(r"^\s*(import atlas\b|from atlas\b[.\s])",
                             _artifact_text(pkg), re.M), "产物必须脱离 Atlas 可运行（形态 3）"
    assert "playwright" in found, "playwright 缺席=依赖扫描对空集恒真"
    assert "requests" not in found and "allure" not in found


# ===========================================================================
# 自哨兵（lead 2026-11-09 实盘坑）：文件级 SyntaxError 红的是全仓 collection，
# 不该等别人的扫描器来发现。本测能跑到=本文件可被 ast.parse。
# ===========================================================================

def test_this_file_itself_parses():
    src = Path(__file__).read_text(encoding="utf-8")
    ast.parse(src)
    # 同类坑面自查：对抗样本里的引号嵌套串全部真实可解析（不是漏网侥幸）
    for probe in ('ValueError(\'HTTP 正文 {"token":"abc"} URL?code=401\')',
                  'f\'未接线：{cls}.action（元素：{elements}）\''):
        ast.parse(probe)

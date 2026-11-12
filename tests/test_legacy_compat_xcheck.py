"""gap11 #21（xcheck 第二把尺·产物与旧仓面）：--compat 产物不 import atlas / fixture 契约 /
旧仓零改动 / compat 组合闸的单点与两入口一致。

出处：`docs/gaps/gap11-legacy-fast-compat.md` §C.1-C.5、§D.1.5/D.1.8、§E，
`docs/gaps/gap11-lead-rulings.md` G11-2（旧仓非 git 仓，双快照代 porcelain）/ G11-4
（字节面 vendoring）+ lead 22:0x 口径「C5 不豁免私有名，产物 helper 用普通函数」。
零网络零浏览器零凭据；所有「旧仓」侧写探针只打在 tmp 复刻目录上，真旧仓只读。

| 格 | 守的路径 |
|---|---|
| test_c4a / test_c4b | §C.3 产物**运行期**真的没有 atlas：垫片对拷到仓外、meta_path 拦掉 atlas 后仍能装垫片并记账（「文件里没那行 import」与「跑起来不会去 import」是两件事）；阻断器另有违规注入样本证其咬人。**#34 加的第二腿**：driver 的假页现按 #33 协议答状态码，`busy_then_quiet`（第一拍见忙→提前，`_TICK_MS ≤ actual < requested`，真时钟不写等号）与 `quiet_only`（从未见忙→睡满 + `no-busy-observed`）**成对**跑，两腿都要求 `atlas_in_sys is False`——单腿与「垫片退化成 no-op」同答，见 §G.5 第 7 条；行为面这一条与 `test_c4d` 的字节锁步互为搭档 |
| test_c4c | shipped 扫描器打在盘上源文（0 命中）+ atlas 侧真文件必命中（正对照，防恒真）——不另造第二把尺（F-15/#92） |
| test_c4d | G11-4 vendoring 锁步：sha 面 + **字节面**双核；篡改一行与换行风格 CRLF↔LF 都必须翻 False（文本读法两边同归一=假绿面）；无 provenance 头的手抄、str 输入一律点名 |
| test_c4e | §C.1 产物树形制 + 阻断沙箱 `--collect-only` rc0 且 collected>0（0 collected 不算绿）+ 逐文件 ast.parse（模板引号嵌套只有落盘后逐文件 parse 拦得住）+ 往产物里塞 `import atlas` 必须翻红。**本格按 `ATLAS_LEGACY_WEB` 两形态参数化**（`absolute-web` 绝对路径 / `relative-standard` 相对子目录名 `iservice` 配 `ATLAS_LEGACY_PROJECT`，#28）：产物品格在两条解析腿上必须等形制 |
| test_c5a | 旧仓 fixture 面基准侧（AST 实提，非手抄非文档表格）+ 提取器两侧非恒真 |
| test_c5b / test_c5c | §C.5 等值判据**拆两向**：缺失方向（产物必须给齐旧仓 7 项且 scope/autouse 逐项相等）与多余方向（产物不得有旧仓没有的 fixture——lead 口径不豁免，helper 应为普通函数）。分两格是为了红的时候一眼看出坏了哪一边。**两格共用 `compat_face` 参数化夹具（module scope，同样跑 `absolute-web`/`relative-standard` 两形态，#28）** |
| test_c6a / test_c6b / test_c6c / test_c6d | G11-2 旧仓零改动：源面双快照 + reports/ 只允许新增 + 注入自证（真旧仓绝不写）+ 只读静态面 + **行为面**（生成 compat 包时旧仓目录逐字节不动） |
| test_c9a / test_c9b / test_c9c | 派单第 7 条：组合闸的拒绝形态与消息点名；**两入口一致**（CLI `_compat_gate` vs 生成器 `compat_combo_problem`，#92 改一漏一的入口面）；写盘前拒绝且零残留（api_call / page_object / 函数名核验不中 三类 + 合法形态正对照，且 `ATLAS_LEGACY_WEB` 绝对/相对两形态都跑） |
| test_c6e / test_c6f / test_c6g / test_c6h | **#28（lead 批的 env 形态双腿）**：标准 `.env` 形态（`ATLAS_LEGACY_PROJECT`=绝对根 + `ATLAS_LEGACY_WEB`=相对子目录名 `iservice`）走 **CLI 主入口**导出成功；相对名形态下 `legacy_pages` 函数名核验**真跑**（不退化成「不验证」）；两入口在四种 env 形态下同结论同点名；缺键双点名 + 产物运行期 `EnvBlocked` 不裸 `FileNotFoundError` 不按 cwd 拼。#27 P0 就是从「只喂绝对路径」这条盲区漏出去的 |

依赖状态：#18/#27 已落地 → 全部格今天实跑，无 skip 位；旧仓路径缺席时只有 c5a/c6a 两格按「skip 必须写明原因、不得记成通过」处理。
**sys.path 纪律（emit-impl 实案）**：产物模板 `_legacy_sys_path()` 成功路径会 `sys.path.insert(0, web)`，
本进程 exec 产物会把假旧仓的 `lib/` 灌进全局 sys.path 并打红 `test_legacy_step_loading.py`
的「祖先无 lib」前提——本文件的产物运行期核对**全部走子进程**（`_resolve_in_child`）。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "atlas"
SHIM_FILE = SRC / "legacy_fast.py"
PLUGIN_FILE = SRC / "fastplugin.py"
OLD_REPO = Path(r"D:/GitHub/UI AUTO TEST")
OLD_CONFTEST = OLD_REPO / "iservice" / "conftest.py"

sys.path.insert(0, str(REPO / "src"))
from atlas import cli                                     # noqa: E402
from atlas.generator import emit_compat                    # noqa: E402
from atlas.generator.emit_pytest import emit_pytest_package  # noqa: E402

# 旧仓结构位（判据取自设计 C.2/C.3，不是实现里的私有名）
ARTIFACT_ROOT_FILES = ("conftest.py", "pytest.ini", "README.md",
                       "legacy_fast.py", "fastplugin.py")


# ---------------------------------------------------------------------------
# 公共机器
# ---------------------------------------------------------------------------


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ATLAS_", "DEMO_", "ISC_", "CDP_", "CASE_"))
           and k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


_SANDBOX_BLOCKER = '''
import sys


class _BlockAtlas:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] == "atlas":
            raise ImportError("sandbox: atlas blocked")
        return None


sys.meta_path.insert(0, _BlockAtlas())
'''

_SANDBOX_DRIVER = '''
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import blocker_check  # noqa: F401  导入即装 meta_path 拦截器

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


plugin = _load("fastplugin")
shim = plugin._load_shim()
PROBE_MODE = os.environ.get("XCHECK_PROBE_MODE", "busy_then_quiet")


class FakePage:
    """#33 协议：判据求值答**状态码**。裸布尔被实现侧 fail-close 成「不可评估」=睡满，
    所以 driver 必须自己演「先出一拍忙信号、再转静默」，否则本测读到的提前路径根本
    没被走过（沙箱装上≠沙箱快得对）。

    PROBE_MODE=busy_then_quiet（默认，唯一合法的提前形态）/ quiet_only（从未观测到忙，
    合法结局是睡满并记 no-busy-observed）——两腿共用同一份脚本，只换 env。
    """

    def __init__(self):
        self.sleeps = []
        self.ticks = 0

    def wait_for_timeout(self, ms=0):
        self.sleeps.append(ms)

    def evaluate(self, js):
        self.ticks += 1
        if PROBE_MODE == "quiet_only":
            return "quiet"
        return "busy-mask" if self.ticks == 1 else "quiet"


pw = types.ModuleType("playwright")
sub = types.ModuleType("playwright.sync_api")
sub.Page = FakePage
pw.sync_api = sub
sys.modules["playwright"] = pw
sys.modules["playwright.sync_api"] = sub

os.environ["ATLAS_FAST_WAITS"] = "1"
os.environ["ATLAS_FAST_MIN_MS"] = "0"
plugin.pytest_configure(types.SimpleNamespace())
p = FakePage()
p.wait_for_timeout(5000)
stats = plugin._STATE.get("stats")
print(json.dumps({
    "atlas_in_sys": "atlas" in sys.modules,
    "installed": plugin._STATE["waits_installed"],
    "sleeps": p.sleeps,
    "ticks": p.ticks,
    "mode": PROBE_MODE,
    "tick_ms": shim._TICK_MS,
    "actual": stats.calls[0]["actual_ms"] if stats and stats.calls else None,
    "requested": stats.calls[0]["requested_ms"] if stats and stats.calls else None,
    "fallback": stats.calls[0]["fallback"] if stats and stats.calls else None,
}))
'''


def _fake_legacy_web(tmp_path, *, fns=("_xcheck_open_list",)):
    """tmp 复刻的旧仓 web 子目录（真旧仓只读，写探针一律打在这里）。"""
    web = tmp_path / "fake-legacy-web"
    (web / "lib").mkdir(parents=True)
    body = "".join("def %s(page, ctx):\n    return %r\n" % (f, f) for f in fns)
    (web / "lib" / "pages.py").write_text(body or "pass\n", encoding="utf-8")
    (web / "lib" / "login.py").write_text("def do_login(a, b, c, d):\n    return None\n"
                                          "def do_logout(a):\n    return None\n",
                                          encoding="utf-8")
    (web / "lib" / "runner.py").write_text(
        "def load_yaml_case(p, cid):\n    return {'id': cid}\n"
        "def run_case_py(*a, **k):\n    return None\n"
        "def setup_logging(**k):\n    return None\n", encoding="utf-8")
    (web / "testcases").mkdir()
    return web


def _compat_flow(*, fn="_xcheck_open_list"):
    return {"id": "xcheck-compat-flow", "steps": [
        {"id": "s1", "task": "open_list", "legacy_pages": fn, "read_only": True},
        {"id": "s2", "task": "select_row", "legacy_pages": "_xcheck_pick_row",
         "read_only": True, "requires": ["doc_no"]},
    ], "assert": []}


def _generate_compat(tmp_path, *, web=None, flow=None, out_name="compat", **kw):
    out = tmp_path / out_name
    env_backup = os.environ.get("ATLAS_LEGACY_WEB")
    if web is not None:
        os.environ["ATLAS_LEGACY_WEB"] = str(web)
    try:
        written = emit_pytest_package(
            out_dir=out, flow=flow or _compat_flow(), components={},
            fast=True, compat="legacy-suite", **kw)
    finally:
        if env_backup is None:
            os.environ.pop("ATLAS_LEGACY_WEB", None)
        else:
            os.environ["ATLAS_LEGACY_WEB"] = env_backup
    return out, written


# ---------------------------------------------------------------------------
# §C.3 / G11-4
# ---------------------------------------------------------------------------


def test_c4a_shim_pair_runs_in_sandbox_without_atlas(tmp_path):
    """产物运行期红线：垫片对拷出仓外 + meta_path 拦掉 atlas，仍能装上并**真按判据 2 走**。

    判据读子进程 stdout 的 json（真模块行为），不读实现自报。装不上=红。
    **#34 加的第二腿**：driver 的假页现按 #33 协议答状态码，`busy_then_quiet`（第一拍见忙、
    第二拍落定 = 唯一合法的提前放行，`_TICK_MS ≤ actual < requested`，真时钟所以不写等号）
    与 `quiet_only`（从未见忙→睡满 + `no-busy-observed`）**成对**跑，两腿都要求
    `atlas_in_sys is False`——单腿与「垫片退化成 no-op」同答，见 §G.5 第 7 条；行为面这一条
    与 `test_c4d` 的字节锁步互为搭档（只比字节不比行为，就会出现「字节对、语义还是旧的」
    那种假绿）。恰 `_TICK_MS` 的等值形态由注入虚拟时钟的通道守（file1 x1/x3/x16），不在这重复。
    """
    pkg = tmp_path / "pair"
    pkg.mkdir()
    shutil.copyfile(SHIM_FILE, pkg / "legacy_fast.py")
    shutil.copyfile(PLUGIN_FILE, pkg / "fastplugin.py")
    (pkg / "blocker_check.py").write_text(_SANDBOX_BLOCKER, encoding="utf-8")
    (pkg / "drive.py").write_text(_SANDBOX_DRIVER, encoding="utf-8")
    for f in pkg.glob("*.py"):
        ast.parse(f.read_text(encoding="utf-8"))      # 坏档防线（wave1 教训常驻）

    def _drive(mode):
        env = _clean_env(PYTHONPATH=str(pkg), XCHECK_PROBE_MODE=mode)
        r = subprocess.run([sys.executable, "drive.py"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=env, cwd=str(pkg))
        assert r.returncode == 0, (mode, r.stdout[-600:], r.stderr[-600:])
        data = json.loads([l for l in r.stdout.splitlines()
                           if l.startswith("{")][-1])
        assert data["atlas_in_sys"] is False, (mode, data)   # 第一红线，两腿都得成立
        assert data["installed"] is True, (mode, data)
        assert data["sleeps"] == [], (mode, data)            # 永不叠原睡眠（判据 1）
        return data

    early = _drive("busy_then_quiet")
    assert early["requested"] == 5000, early
    # 恰一拍见忙、第二拍落定：这里**不**断等号——driver 用的是真 `_RealClock`，
    # `actual` 是实测流逝毫秒（25ms 睡眠 + 求值开销，本机实测 26）。「恰=_TICK_MS」
    # 那一格由注入虚拟时钟通道守（file1 test_x1 / test_x3 / test_x16），两边不重复。
    assert early["tick_ms"] <= early["actual"] < early["requested"], early
    assert early["ticks"] == 2, early                        # 忙一拍 + 静默一拍
    assert early["fallback"] == "", early                    # 见过忙→落定：不是回退也不是睡满

    full = _drive("quiet_only")
    assert full["requested"] == full["actual"] == 5000, full  # 从未见忙=睡满，与旧行为逐字等价
    assert full["fallback"] == "no-busy-observed", full       # fail-safe 面，不是故障
    assert full["ticks"] > 1, full                            # 确实在按拍问，不是没装上


def test_c4b_sandbox_blocker_bites_on_violation(tmp_path):
    """阻断沙箱非恒真：`import atlas` 的样本在同一沙箱里必须崩。

    没有这格，c4a/c4e 的「跑通了」可能只是拦截器没生效（沙箱假绿）。
    """
    pkg = tmp_path / "decoy"
    pkg.mkdir()
    (pkg / "blocker_check.py").write_text(_SANDBOX_BLOCKER, encoding="utf-8")
    (pkg / "violating.py").write_text(
        "import blocker_check  # noqa: F401\nimport atlas\nprint('SHOULD NOT REACH')\n",
        encoding="utf-8")
    env = _clean_env(PYTHONPATH=str(pkg))
    r = subprocess.run([sys.executable, "violating.py"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=str(pkg))
    assert r.returncode != 0
    assert "atlas blocked" in (r.stderr + r.stdout)
    assert "SHOULD NOT REACH" not in r.stdout


def test_c4c_shim_sources_scan_atlas_free_with_real_control():
    """§C.3 第一红线（源文面）：垫片两文件过 shipped 扫描器 0 命中。

    用 `emit_compat` 判据本体（AST 权威 + 文本哨兵双扫），不造第二把尺；正向对照=
    atlas 侧真文件必命中，证扫描器不是恒真。
    """
    for path in (SHIM_FILE, PLUGIN_FILE):
        src = path.read_text(encoding="utf-8")
        hits = emit_compat.is_atlas_free(src, path.name)
        assert hits == [], "%s 出现 atlas import：%s" % (path.name, hits)
    control = (SRC / "config_add.py").read_text(encoding="utf-8")
    assert emit_compat.is_atlas_free(control, "config_add.py"), "对照零命中：扫描器恒真"


@pytest.mark.parametrize("rel", emit_compat.SHIM_SOURCE_FILES)
def test_c4d_vendor_lockstep_is_byte_level(rel):
    """G11-4 vendoring 锁步三态：现源等值 True / 篡改一行 False / 换行风格 False。

    第三态是本格的全部价值：文本读法（read_text）会把 CRLF 与 LF 两边归一，哈希与
    正文双双「相等」→ 判据恒绿，恰好放过「换行被悄悄改写」。str 输入必须被拒收
    （两套接口混用会互相掩盖）。
    """
    vendored = emit_compat.vendor_shim_source(REPO, rel)
    source = (REPO / rel).read_bytes()
    assert emit_compat.vendor_is_current(vendored, source) is True
    rel_out, sha = emit_compat.vendor_provenance(vendored)
    assert rel_out == rel and sha == hashlib.sha256(source).hexdigest()

    body = emit_compat.vendor_body(vendored)
    head = vendored[:len(vendored) - len(body)]
    assert body == source
    assert emit_compat.vendor_is_current(head + body.replace(b"mask", b"MA5K", 1),
                                         source) is False
    if b"\r\n" in body:
        swapped, why = body.replace(b"\r\n", b"\n"), "CRLF→LF"
    else:
        swapped, why = body.replace(b"\n", b"\r\n"), "LF→CRLF"
    assert emit_compat.vendor_is_current(head + swapped, source) is False, \
        "%s 换行漂移没被抓到：判据退化成文本面" % why

    with pytest.raises(emit_compat.CompatEmitError):
        emit_compat.vendor_body(b"def f():\n    pass\n")            # 手抄混进产物通道
    with pytest.raises(emit_compat.CompatEmitError):
        emit_compat.vendor_provenance("# 没有头的伪造产物\n".encode("utf-8"))
    with pytest.raises(emit_compat.CompatEmitError):
        emit_compat.vendor_is_current(vendored, source.decode("utf-8"))  # str 拒收


@pytest.mark.parametrize("env_form", ["absolute-web", "relative-standard"])
def test_c4e_compat_artifact_shape_and_blocked_sandbox(tmp_path, monkeypatch, env_form):
    """§C.1/§D.1.5：产物树形制 + 阻断 atlas 的沙箱里 collect-only rc0 且 collected>0。

    **两种 env 形态各跑一遍**（#28 lead 第①条：判据不许只喂「测试方便的那种形态」）：
    `ATLAS_LEGACY_WEB` 给绝对路径（旧用法）与给相对子目录名 `iservice`（标准 .env 形态，
    配 `ATLAS_LEGACY_PROJECT`）——产物品格在两形态下必须等形制。
    五读同测：①C.1 文件面（conftest/pytest.ini/testcases/<suite>/test_*.py/vendored
    两垫片/README）②逐文件 ast.parse ③shipped 扫描器 0 命中 ④vendored 字节==源
    ⑤collect-only rc0 + 收集到用例 + 无 error。最后往产物塞一行 `import atlas`，
    同一沙箱必须翻红（证尺咬人，不是恒绿）。
    """
    if env_form == "absolute-web":
        proj, web = _fake_legacy_project(
            tmp_path, fns=("_xcheck_open_list", "_xcheck_pick_row"))
        web_val, proj_val = web, None
        child_kv = {"ATLAS_LEGACY_WEB": str(web)}
    else:
        proj, web = _fake_legacy_project(
            tmp_path, fns=("_xcheck_open_list", "_xcheck_pick_row"))
        web_val, proj_val = "iservice", proj
        child_kv = {"ATLAS_LEGACY_PROJECT": str(proj), "ATLAS_LEGACY_WEB": "iservice"}
    _set_legacy_env(monkeypatch, proj_val, web_val)
    out = tmp_path / ("compat-%s" % env_form)
    written = emit_pytest_package(out_dir=out, flow=_compat_flow(), components={},
                                  fast=True, compat="legacy-suite")
    names = {p.name for p in out.iterdir()}
    assert {"conftest.py", "pytest.ini", "README.md"} <= names, names
    assert {"legacy_fast.py", "fastplugin.py"} <= names, \
        "产物缺 vendored 垫片（改名=快形态悄悄退化成旧形态）：%s" % names
    tests = sorted((out / "testcases").rglob("test_*.py"))
    assert tests, "C.1 用例面缺失：testcases/<suite>/test_<suite>.py 不在"
    assert written

    py_files = sorted(out.rglob("*.py"))
    assert py_files
    for p in py_files:
        text = p.read_text(encoding="utf-8")
        ast.parse(text, filename=str(p))
        hits = emit_compat.is_atlas_free(text, p.name)
        assert hits == [], "%s: %s" % (p.name, hits)
    for rel in emit_compat.SHIM_SOURCE_FILES:
        blob = (out / Path(rel).name).read_bytes()
        assert emit_compat.vendor_is_current(blob, (REPO / rel).read_bytes()), \
            "%s 产物与源字节漂移（手抄或未重新生成）" % rel

    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(_SANDBOX_BLOCKER, encoding="utf-8")
    env = _clean_env(PYTHONPATH=os.pathsep.join([str(blocker), str(out)]),
                     **child_kv)
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q",
           "-p", "no:cacheprovider", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(out))
    ids = [l for l in r.stdout.splitlines() if "::" in l]
    assert r.returncode == 0, r.stdout[-1200:] + "\n" + r.stderr[-600:]
    assert ids, "collected 0 也返回 rc0：空收集不算可执行"
    assert "error" not in r.stdout.lower(), r.stdout[-800:]

    victim = tests[0]
    original = victim.read_text(encoding="utf-8")
    victim.write_text(original + "\nimport atlas\n", encoding="utf-8")
    r2 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=env, cwd=str(out))
    victim.write_text(original, encoding="utf-8")
    assert r2.returncode != 0 or "atlas blocked" in (r2.stderr + r2.stdout), \
        "沙箱没咬人：阻断器恒通过，前面所有绿都要重算"


# ---------------------------------------------------------------------------
# §C.5 fixture 契约（拆两向，红的时候能一眼看出坏了哪边）
# ---------------------------------------------------------------------------


def _extract_fixtures(source):
    """fixture 提取器（一把尺两头用）：{名: (scope, autouse)}。

    只认 `@pytest.fixture` / `@pytest.fixture(...)`；keywords 取 scope/autouse 字面值，
    表达式取不到标 "?"（不可核实显式可见，不猜）。
    """
    out = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            inner = dec.func if isinstance(dec, ast.Call) else dec
            if not (isinstance(inner, ast.Attribute) and inner.attr == "fixture"):
                continue
            scope, autouse = "function", False
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    try:
                        val = ast.literal_eval(kw.value)
                    except Exception:
                        val = "?"
                    if kw.arg == "scope":
                        scope = val
                    elif kw.arg == "autouse":
                        autouse = val
            out[node.name] = (scope, autouse)
    for k, v in out.items():                        # 提取器自证：形态坏就当场炸
        assert isinstance(k, str) and isinstance(v, tuple) and len(v) == 2, (k, v)
    return out


@pytest.fixture(scope="module", params=["absolute-web", "relative-standard"])
def compat_face(request, tmp_path_factory):
    """生成一次 compat 产物并返回 (产物 fixture 面, 旧仓 fixture 面, conftest 路径, 形态名)。

    **参数化成两形态**（emit-impl 23:4x 交接 + lead #28 第④⑤条）：`ATLAS_LEGACY_WEB`
    给绝对路径与给相对子目录名（配 `ATLAS_LEGACY_PROJECT`）是两条不同的解析腿，
    fixture 面对拍在两条腿上各跑一遍——只喂绝对路径就是 #27 P0 漏出去的那种盲区。
    旧仓缺席 → skip（原因写明，不得记成通过）；#18 缺席同理（判据=签名，不看文档）。
    """
    import inspect
    form = request.param
    if "compat" not in inspect.signature(emit_pytest_package).parameters:
        pytest.skip("#18 未落地：emit_pytest_package 无 compat 形参，盘上没有 "
                    "--compat legacy-suite 产物可对拍（skip≠通过）")
    if not OLD_CONFTEST.is_file():
        pytest.skip("旧仓 conftest 缺席：%s 不在——基准侧无从提取（skip≠通过）"
                    % OLD_CONFTEST)
    root = tmp_path_factory.mktemp("face-" + form)
    proj, web = _fake_legacy_project(root, fns=("_xcheck_open_list", "_xcheck_pick_row"))
    out = root / "pkg"
    saved = {k: os.environ.get(k) for k in ("ATLAS_LEGACY_PROJECT", "ATLAS_LEGACY_WEB")}
    try:
        if form == "absolute-web":
            os.environ.pop("ATLAS_LEGACY_PROJECT", None)
            os.environ["ATLAS_LEGACY_WEB"] = str(web)
        else:
            os.environ["ATLAS_LEGACY_PROJECT"] = str(proj)
            os.environ["ATLAS_LEGACY_WEB"] = "iservice"
        emit_pytest_package(out_dir=out, flow=_compat_flow(), components={},
                            fast=True, compat="legacy-suite")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    artifact = _extract_fixtures((out / "conftest.py").read_text(encoding="utf-8"))
    old = _extract_fixtures(OLD_CONFTEST.read_text(encoding="utf-8-sig"))
    assert old and artifact, "任一侧提取为空：本尺恒真"
    return artifact, old, out / "conftest.py", form


# ---------------------------------------------------------------------------
# #28：env 取值形态双腿（标准 .env 的相对子目录名 vs 绝对路径）
# 出处：#27 P0——compat 导出在标准 .env（ATLAS_LEGACY_PROJECT=绝对根 + ATLAS_LEGACY_WEB=iservice）
# 下直接被拒；我原 51 格只喂过绝对路径形态，那条腿从没被踩过（lead 23:1x 批的补格五条）。
# ---------------------------------------------------------------------------

# 注册名取自 TaskRegistry（validate_flow 只认已注册 Task，未注册会被结构闸先拒、
# 让路径形态判据变成「因为别的原因红」——所以这里用真注册名，别造词）
_COMPAT_TASK = "open_doc_list"


def _fake_legacy_project(tmp_path, *, fns=("_xcheck_open_list", "_xcheck_pick_row"),
                         web_name="iservice", make_web=True):
    """复刻 Atlas 标准 .env 形态的旧仓：PROJECT=绝对根，WEB=**相对子目录名**。

    只读判据用；写探针只打在这里，真旧仓不碰（红线 #4/G11-2）。
    """
    proj = tmp_path / "legacy-project"
    web = proj / web_name
    if make_web:
        (web / "lib" / "pages.py").parent.mkdir(parents=True)
        (web / "lib" / "pages.py").write_text(
            "".join("def %s(page, ctx):\n    return %r\n" % (f, f) for f in fns),
            encoding="utf-8")
        (web / "lib" / "login.py").write_text(
            "def do_login(*a): return None\ndef do_logout(*a): return None\n",
            encoding="utf-8")
        (web / "lib" / "runner.py").write_text(
            "def setup_logging(**k): return None\n"
            "def load_yaml_case(p, cid): return {'id': cid}\n"
            "def run_case_py(*a, **k): return None\n", encoding="utf-8")
        (web / "testcases").mkdir(parents=True, exist_ok=True)
    else:
        proj.mkdir(parents=True)
    return proj, web


def _flow_yaml(tmp_path, *, fn="_xcheck_open_list", name="c6e"):
    flow = {"id": "xcheck-env-form-flow", "steps": [
        {"id": "s1", "task": _COMPAT_TASK, "intent": "委托旧列表函数",
         "read_only": True, "legacy_pages": fn},
    ], "assert": []}
    import yaml
    p = tmp_path / ("flow_%s.yaml" % name)
    p.write_text(yaml.safe_dump(flow, allow_unicode=True), encoding="utf-8")
    return p


def _set_legacy_env(monkeypatch, proj=None, web=None):
    """显式 setenv（含空串），**不用 delenv**——本仓 .env 里 ATLAS_LEGACY_* 有真值，
    `load_dotenv` 是 setdefault 语义：键缺席时会被 .env 灌成真实旧仓路径，
    「缺配」形态就必须用空串占位（代码判据是 .strip() 空=未配）。
    """
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(proj) if proj else "")
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(web) if web else "")


# 产物侧运行时解析：必须走**子进程**——`_legacy_sys_path()` 成功路径会
# sys.path.insert(0, web)，在本进程 exec 产物会把假旧仓的 lib/ 灌进全局 sys.path，
# 打红 tests/test_legacy_step_loading.py::test_from_module_probe_miss_gets_attributed_import_error
# （它的前提是「祖先目录无 lib」）。同族污染已按 emit-impl 交接说明规避。
_RESOLVE_CHILD = '''
import importlib.util, json, os, sys
from pathlib import Path

HERE = Path(sys.argv[1])
# 产物 conftest 会 `import fastplugin`（同目录 vendored 垫片）——显式把产物目录放进
# 子进程 sys.path，好让「换 cwd」这一格测的是解析语义，不是模块可见性。
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("art_conf", HERE / "conftest.py")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as exc:
    print(json.dumps({"load_error": type(exc).__name__, "msg": str(exc)[:200]}))
    raise SystemExit(0)
try:
    resolved = str(mod._legacy_sys_path())
    print(json.dumps({"resolved": resolved, "in_sys_path": str(resolved) in sys.path,
                      "cwd": os.getcwd()}))
except BaseException as exc:
    print(json.dumps({"error_type": type(exc).__name__, "msg": str(exc)[:400]}))
'''


def _resolve_in_child(artifact_dir, env_extra=None, cwd=None):
    env = _clean_env()
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "-c", _RESOLVE_CHILD, str(artifact_dir)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(cwd or artifact_dir))
    line = [l for l in r.stdout.splitlines() if l.startswith("{")]
    assert line, "子进程没出 json：%r %r" % (r.stdout[-300:], r.stderr[-300:])
    return json.loads(line[-1])


def test_c6e_standard_env_form_exports_through_cli_main(tmp_path, monkeypatch):
    """补格①（lead 批的 c6e）：标准 `.env` 形态（PROJECT 绝对 + WEB 相对名）**走 CLI 主入口**导出成功。

    #27 P0 的形态恰恰是「只在真实链路（load_dotenv + validate_flow + 生成器）上出现」，
    直调 `emit_compat_package` 看不见。断四样：rc0、C.1 产物树齐（六件）、
    产物运行期解析串在子进程里落到 `PROJECT/WEB` 的绝对目录（不是 cwd 拼接）、
    且解析成功那侧确实把目录插进了**子进程自己的** sys.path（在产物里 import lib 的前提）。
    """
    proj, web = _fake_legacy_project(tmp_path)
    _set_legacy_env(monkeypatch, proj, "iservice")
    out = tmp_path / "pkg"
    rc = cli.main(["export-pytest", str(_flow_yaml(tmp_path)),
                   "-o", str(out), "--fast", "--compat", "legacy-suite"])
    assert rc == 0, rc
    names = {p.name for p in out.iterdir()}
    assert {"conftest.py", "pytest.ini", "README.md",
            "legacy_fast.py", "fastplugin.py"} <= names, names
    assert list((out / "testcases").rglob("test_*.py")), "用例面缺失"

    got = _resolve_in_child(out, {"ATLAS_LEGACY_PROJECT": str(proj),
                                  "ATLAS_LEGACY_WEB": "iservice"})
    assert "error_type" not in got and "load_error" not in got, got
    assert Path(got["resolved"]) == web.resolve(), got
    assert got["in_sys_path"] is True, got
    # cwd 无关性：换到别处跑同一条解析，结论必须一模一样（静默按 cwd 拼=配错被读成可用）
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    got2 = _resolve_in_child(out, {"ATLAS_LEGACY_PROJECT": str(proj),
                                   "ATLAS_LEGACY_WEB": "iservice"}, cwd=elsewhere)
    assert got2.get("resolved") == got["resolved"], (got, got2)
    assert Path(got2["resolved"]) != (elsewhere / "iservice").resolve(), got2


def test_c6f_relative_form_still_verifies_function_names(tmp_path, monkeypatch):
    """补格②（差分）：相对名形态下 `legacy_pages` 函数名核验**必须真跑**，不退成「不验证」。

    退化形态=生成期放行、运行期才 AttributeError——那是把「没查过」冒充「查过」的
    反面（#64 诚实面的对偶）。两态同测：假函数名 → CLI rc2 + 点名「不在旧 lib.pages
    顶层函数清单」+ **目录不建**；真函数名 → rc0（同目录名另一处，证拒绝不是恒拒）。
    """
    proj, _web = _fake_legacy_project(tmp_path)
    _set_legacy_env(monkeypatch, proj, "iservice")

    bad_out = tmp_path / "bad"
    rc = cli.main(["export-pytest", str(_flow_yaml(tmp_path, fn="no_such_fn_xyz",
                                                   name="bad")),
                   "-o", str(bad_out), "--fast", "--compat", "legacy-suite"])
    assert rc == 2, rc
    assert not bad_out.exists(), "核验不中却建了目录（半成品假可用）"
    good_out = tmp_path / "good"
    rc2 = cli.main(["export-pytest", str(_flow_yaml(tmp_path, name="good")),
                    "-o", str(good_out), "--fast", "--compat", "legacy-suite"])
    assert rc2 == 0 and good_out.is_dir()


def test_c6g_two_entries_agree_on_env_forms(monkeypatch, tmp_path):
    """补格③（c9b 姊妹）：同一 env 形态下 CLI 主入口与生成器直调**同结论**。

    四形态参数化（标准相对名 / 绝对 WEB / 相对无 PROJECT / 两键全空），逐格比
    「生成是否成功」与「拒绝消息里点名的键」——P0 就是两入口对 env 形态理解不一致
    的产物，一条腿绿不代表另一条腿可用（行为等值，不钉源码文本）。
    """
    cases = {
        "relative-standard": ("proj", "iservice"),
        "absolute-web": ("proj", "abs"),
        "relative-without-project": (None, "iservice"),
        "both-empty": (None, None),
    }
    for label, (want_proj, web_mode) in cases.items():
        proj, web = _fake_legacy_project(tmp_path / label)
        proj_val = proj if want_proj else None
        if web_mode == "abs":
            web_val = web
        elif web_mode == "iservice":
            web_val = "iservice"
        else:
            web_val = None
        _set_legacy_env(monkeypatch, proj_val, web_val)
        flow = _flow_yaml(tmp_path / label, name=label)

        out_cli = tmp_path / label / "cli"
        rc = cli.main(["export-pytest", str(flow), "-o", str(out_cli),
                       "--fast", "--compat", "legacy-suite"])
        direct_ok = None
        direct_msg = ""
        out_direct = tmp_path / label / "direct"
        import yaml as _yaml
        try:
            emit_pytest_package(out_dir=out_direct,
                                flow=_yaml.safe_load(flow.read_text(encoding="utf-8")),
                                components={}, fast=True, compat="legacy-suite")
            direct_ok = True
        except ValueError as exc:
            direct_ok, direct_msg = False, str(exc)
        assert (rc == 0) is direct_ok, \
            "%s：CLI rc=%s vs 直调成功=%s（消息=%s）两入口分叉" % (
                label, rc, direct_ok, direct_msg[:200])
        if not direct_ok:
            assert "ATLAS_LEGACY" in direct_msg, "%s：拒绝消息没点名配置键 %s" % (
                label, direct_msg[:200])
            assert not out_cli.exists() and not out_direct.exists(), \
                "%s：拒绝一侧留了目录" % label


def test_c6h_missing_keys_name_both_env_keys(monkeypatch, tmp_path):
    """补格⑤（lead 加条）：配错形态必须点名**两个键**，不裸 FileNotFoundError、不静默按 cwd 解析。

    生成期侧=`CompatEmitError(ValueError)`；产物运行期侧=模板内 `EnvBlocked(RuntimeError)`
    （类型按 emit-impl 交接的两侧口径判，不是我猜的）。四态：相对 WEB 缺 PROJECT、
    WEB 全空、产物侧两键全空、产物侧相对名缺 PROJECT（且 cwd 里恰好有个同名目录时
    也不得按 cwd 拼——静默拼 cwd 等于把配错读成可用）。
    """
    base = tmp_path
    proj, _web = _fake_legacy_project(base)
    flow = _flow_yaml(base, name="c6h")
    # ① 生成期：相对名 + 缺 PROJECT → 点名两键
    _set_legacy_env(monkeypatch, None, "iservice")
    out = tmp_path / "gen"
    with pytest.raises(ValueError) as got:
        import yaml as _yaml
        emit_pytest_package(out_dir=out, flow=_yaml.safe_load(flow.read_text(encoding="utf-8")),
                            components={}, fast=True, compat="legacy-suite")
    msg = str(got.value)
    assert "ATLAS_LEGACY_WEB" in msg and "ATLAS_LEGACY_PROJECT" in msg, msg
    assert not out.exists()
    # ② 生成期：WEB 全空 → 点名 WEB（并给出两种合法形态），不裸崩
    _set_legacy_env(monkeypatch, proj, None)
    out2 = tmp_path / "gen2"
    with pytest.raises(ValueError) as got2:
        emit_pytest_package(out_dir=out2, flow=_yaml.safe_load(flow.read_text(encoding="utf-8")),
                            components={}, fast=True, compat="legacy-suite")
    assert "ATLAS_LEGACY_WEB" in str(got2.value), got2.value
    # ③ 产物运行期：两键全空 → EnvBlocked 点名，且不是 FileNotFoundError/NotADirectoryError
    _set_legacy_env(monkeypatch, proj, "iservice")
    art = tmp_path / "artifact"
    rc = cli.main(["export-pytest", str(flow), "-o", str(art),
                   "--fast", "--compat", "legacy-suite"])
    assert rc == 0, rc
    data = _resolve_in_child(art, {"ATLAS_LEGACY_PROJECT": "", "ATLAS_LEGACY_WEB": ""})
    assert data.get("error_type") == "EnvBlocked", data
    assert "ATLAS_LEGACY" in data["msg"], data
    # ④ 产物运行期：相对名缺 PROJECT 也不能按 cwd 拼（静默拼 cwd=把配错读成可用）
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    data2 = _resolve_in_child(art, {"ATLAS_LEGACY_PROJECT": "",
                                    "ATLAS_LEGACY_WEB": "iservice"}, cwd=elsewhere)
    assert data2.get("error_type") == "EnvBlocked", data2
    assert "ATLAS_LEGACY_PROJECT" in data2["msg"], data2
    assert not (elsewhere / "iservice").exists() or data2.get("resolved") is None


def test_c5d_artifact_keeps_headless_assignment_and_marker_registration(tmp_path):
    """§C.5 兼容面两枚易漏钉：`page._headless` 赋值保留 + ini 判据面自足。

    ①R-3：旧仓 conftest 给页面对象置 `_headless`（当前全仓消费者零命中，但旧 step
    文件若 getattr 就会 AttributeError）——产物必须保留赋值，且**不做消费假设**。
    ②产物自带 pytest.ini：`--strict-markers` 在场时未注册 marker 是**收集期错误**，
    旧仓 ini 里有 `markers = order`，产物若漏注册就在旧仓机器上整场红（我这台机装了
    pytest-order 会掩盖它，所以判据打在 ini 文本上而不是只打 collect 结果）。
    正对照：collect-only 在阻断沙箱里 rc0（c4e 已钉），本格钉「不依赖本机插件在场」。
    """
    web = _fake_legacy_web(tmp_path, fns=("_xcheck_open_list", "_xcheck_pick_row"))
    out, _written = _generate_compat(tmp_path, web=web, out_name="faces")
    conftest = (out / "conftest.py").read_text(encoding="utf-8")
    assert "_headless" in conftest, "产物没保留 page._headless 赋值（R-3 失踪）"
    tree = ast.parse(conftest)
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               for t in n.targets
               if isinstance(t, ast.Attribute) and t.attr == "_headless"]
    assert len(assigns) == 1, "赋值位数漂移（应恰一处，在 sole_page 里）：%d" % len(assigns)

    ini = (out / "pytest.ini").read_text(encoding="utf-8")
    assert "--strict-markers" in ini, ini
    assert "order:" in ini, "marker 没注册：旧仓机器上 --strict-markers 会把收集整场打红"
    assert "-p no:playwright" in ini and "-p no:randomly" in ini, \
        "旧仓 ini 的两条防双浏览器/防乱序判据没进产物（次序语义会变）"

    # 业务选择器零进模板（红线 #11）：产物全文不得出现旧仓业务选择器字面量
    for p in sorted(out.rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        for mark in emit_compat.LEGACY_BUSINESS_SELECTOR_MARKS:
            assert mark not in text, "%s 出现旧仓业务选择器 %r（模板必须只经 env）" % (
                p.name, mark)
    # 对照：旧仓 conftest 实文必含其中至少一个（证禁词表不是空表、尺不是恒真）
    if OLD_CONFTEST.is_file():
        old_src = OLD_CONFTEST.read_text(encoding="utf-8-sig")
        assert any(m in old_src for m in emit_compat.LEGACY_BUSINESS_SELECTOR_MARKS), \
            "禁词表与旧仓实文脱钩：判据已失效，请更新 emit_compat 名单"


def test_c5a_old_repo_fixture_face_extracted_not_hardcoded():
    """§C.5 基准侧：旧仓 AST 实提非空、提取器两侧都不恒真。

    判据位不写死名单（F-24）：①旧仓提得出且 scope/autouse 三种实值形态在场；
    ②无 fixture 的源必须得空集（防「匹配到一切」）；③同名不同 scope 必须分辨得出
    （等值判据真在比内容，不是比键名）。
    """
    if not OLD_CONFTEST.is_file():
        pytest.skip("旧仓 conftest 缺席：%s 不在（CI 无该路径）" % OLD_CONFTEST)
    face = _extract_fixtures(OLD_CONFTEST.read_text(encoding="utf-8-sig"))
    assert face, "旧仓提取为空：本尺恒真"
    scopes = {v[0] for v in face.values()}
    assert "module" in scopes and "session" in scopes, face
    assert any(v[1] is True for v in face.values()), "没读到 autouse 形态：提取器漏 keywords"
    assert _extract_fixtures("def helper():\n    return 1\n") == {}
    a = _extract_fixtures("import pytest\n@pytest.fixture(scope='module')\ndef x():\n"
                          "    pass\n")
    b = _extract_fixtures("import pytest\n@pytest.fixture(scope='session')\ndef x():\n"
                          "    pass\n")
    assert a != b and a["x"][0] == "module" and b["x"][0] == "session"


def test_c5b_artifact_provides_whole_old_repo_fixture_face(compat_face):
    """§C.5 缺失方向（正判据）：旧仓每一项 fixture，产物都有且 scope/autouse 逐项相等。

    基准=旧仓 AST 实值（不是文档 A0 表格，也不是实现里的 CONTRACT_FIXTURES——两处
    都可能与旧仓漂）。browser=session、其余 module、watchdog autouse 全在这格核。
    """
    artifact, old, _path, form = compat_face
    missing = set(old) - set(artifact)
    assert not missing, "%s 形态下产物缺旧仓在用的 fixture：%s" % (form, sorted(missing))
    for name in sorted(old):
        assert artifact[name] == old[name], \
            "%s 形态下 %s scope/autouse 漂移：产物=%s 旧仓=%s" % (
                form, name, artifact[name], old[name])


def test_c5c_artifact_adds_no_fixtures_beyond_old_repo(compat_face):
    """§C.5 多余方向（lead 口径：不豁免私有名，helper 应为普通函数）。

    产物自造的 fixture 会改变旧仓 conftest 的语义面（同名遮蔽、session 级新把手），
    等值判据两侧都要管——只查缺失就是半个尺。红的时候点名清单，供 lead 裁决：
    要么实现把 helper 降级成普通函数/模块常量，要么把契约表正式扩面并说明为何。
    """
    artifact, old, path, form = compat_face
    extra = set(artifact) - set(old)
    assert not extra, (
        "%s 形态下产物 conftest 多出旧仓没有的 fixture：%s（%s）。lead 口径=不豁免私有名、"
        "helper 用普通函数不造私有 fixture；这些把手（如旧仓定位/委托类）可改成"
        "模块级函数或经既有 fixture 参数取用。若确要扩契约面，请 lead 改 C.5 表并"
        "说明与旧仓语义面的关系，不由测侧放行。" % (form, sorted(extra), path))


# ---------------------------------------------------------------------------
# G11-2 旧仓零改动
# ---------------------------------------------------------------------------

_SRC_SUFFIXES = (".py", ".yaml", ".yml", ".json", ".md", ".ini")
_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "reports", "screenshots",
                 "logs", "allure-results"}


def _manifest(root, *, base=None, exts=None):
    """源面清单：{POSIX 相对路径: (size, mtime_ns, sha256[:16])}。

    排除运行期目录（reports/screenshots/缓存）——真实跑测必然往那里写，混进源面
    就既假红又掩盖真信号。
    """
    base = Path(base or root)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for f in filenames:
            if exts is not None and not f.endswith(exts):
                continue
            if not f.endswith(_SRC_SUFFIXES):
                continue
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            except OSError:
                digest = "unreadable"
            out[p.relative_to(base).as_posix()] = (st.st_size, st.st_mtime_ns, digest)
    return out


def _reports_manifest(reports_dir):
    if not Path(reports_dir).is_dir():
        return {}
    out = {}
    for p in Path(reports_dir).rglob("*"):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(reports_dir).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.mark.skipif(not OLD_REPO.is_dir(),
                    reason="旧仓路径缺席：%s 不在——双快照无从比对（skip≠通过，"
                           "报告里如实报 skip）" % OLD_REPO)
def test_c6a_old_repo_source_face_unchanged_across_run():
    """G11-2：测试前后旧仓源面零改动，reports/ 只允许新增。

    旧仓不是 git 仓（实测 `git status` → fatal: not a git repository），判据走盘上
    双快照而非 porcelain。诚实边界：本格窗口=本文件的运行期，跨全仓收工核归 #20；
    本尺只跑只读探针，绝不写旧仓。
    """
    before = _manifest(OLD_REPO)
    reports_before = _reports_manifest(OLD_REPO / "iservice" / "reports")
    assert before, "旧仓源面清单为空：本尺恒真"
    if OLD_CONFTEST.is_file():
        _extract_fixtures(OLD_CONFTEST.read_text(encoding="utf-8-sig"))
    after = _manifest(OLD_REPO)
    changed = {k for k in set(before) & set(after) if before[k] != after[k]}
    vanished = set(before) - set(after)
    assert not changed, "旧仓源文件被改动：%s" % sorted(changed)
    assert not vanished, "旧仓源文件消失：%s" % sorted(vanished)
    reports_after = _reports_manifest(OLD_REPO / "iservice" / "reports")
    touched = {k for k in set(reports_before) & set(reports_after)
               if reports_before[k] != reports_after[k]}
    gone = set(reports_before) - set(reports_after)
    assert not touched, "reports/ 既有文件被改写：%s" % sorted(touched)
    assert not gone, "reports/ 既有文件消失：%s" % sorted(gone)


def test_c6b_snapshot_diff_bites_on_injected_repo(tmp_path):
    """双快照尺非恒真：对 tmp 复刻的小假仓做改写/删除/新增/稳定四注入，分别点名。

    绝不在真旧仓写探针。四态各自断言（合成一条就是恒真）：源面改写点名、删除进
    vanished、新增不影响既有判定、什么都没动时两次快照逐键相等（防「任何跑测都红」
    的假红面）。reports/ 面同样两态：新增放行、既有改写必须点名。
    """
    fake = tmp_path / "repo"
    (fake / "lib").mkdir(parents=True)
    (fake / "lib" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (fake / "conftest.py").write_text("y = 2\n", encoding="utf-8")
    reports = fake / "reports"
    reports.mkdir()
    (reports / "keep.md").write_text("v\n", encoding="utf-8")

    before = _manifest(fake)
    rep_before = _reports_manifest(reports)
    (fake / "lib" / "a.py").write_text("x = 999999999\n", encoding="utf-8")
    after = _manifest(fake)
    assert {k for k in set(before) & set(after) if before[k] != after[k]} == {"lib/a.py"}
    (fake / "conftest.py").unlink()
    after2 = _manifest(fake)
    assert "conftest.py" in set(after) - set(after2)
    (fake / "lib" / "new.py").write_text("z = 3\n", encoding="utf-8")
    after3 = _manifest(fake)
    assert {"lib/a.py", "lib/new.py"} <= set(after3)
    assert {k: v for k, v in after3.items() if k == "lib/a.py"} == \
        {k: v for k, v in after2.items() if k == "lib/a.py"}   # 既有项没被二次抖动
    # reports/ 面
    (reports / "run_new.log").write_text("run\n", encoding="utf-8")
    rep_after = _reports_manifest(reports)
    assert "run_new.log" in set(rep_after) - set(rep_before)
    assert not {k for k in set(rep_before) & set(rep_after)
                if rep_before[k] != rep_after[k]}, "新增被判成改写=假红"
    (reports / "keep.md").write_text("tampered much longer content\n", encoding="utf-8")
    rep_after2 = _reports_manifest(reports)
    assert {k for k in set(rep_before) & set(rep_after2)
            if rep_before[k] != rep_after2[k]} == {"keep.md"}
    # 稳定对照
    assert _manifest(fake) == _manifest(fake)
    assert _reports_manifest(reports) == _reports_manifest(reports)


def test_c6c_gap11_modules_have_no_legacy_write_path():
    """只读静态面：垫片两文件不提旧仓键；emit_compat 的写目标只能是产物目录。

    「产物生成侧绝不写旧仓」（C.1）在源文层的证据。判据形态=**写调用的参数里不许
    出现旧仓定位标识**（emit_compat 合法地会把 ATLAS_LEGACY_WEB 写进产物运行期代码，
    所以不能简单禁词；禁的是它作为写根）。正向对照=扫描器在真会写盘的
    emit_pytest/emit_compat 上必须收得到写调用，否则「零命中」是恒真。
    """
    LEGACY_MARKS = ("ATLAS_LEGACY_PROJECT", "ATLAS_LEGACY_WEB", "_LEGACY_WEB_ENV_KEY",
                    "legacy_project", "legacy_web", "_legacy_sys_path")

    def write_arg_hits(source, name):
        hits = []
        tree = ast.parse(source, filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            attr = getattr(f, "attr", None)
            is_write = (isinstance(f, ast.Attribute) and
                        attr in ("write_text", "write_bytes", "mkdir", "rmtree",
                                 "unlink", "remove")) or \
                (isinstance(f, ast.Name) and f.id == "open" and
                 any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                     and any(m in a.value for m in "wax+") for a in node.args[1:2]))
            if not is_write:
                continue
            for sub in ast.walk(node):
                label = None
                if isinstance(sub, ast.Name) and sub.id in LEGACY_MARKS:
                    label = sub.id
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value in ("ATLAS_LEGACY_PROJECT", "ATLAS_LEGACY_WEB"):
                    label = sub.value
                if label:
                    hits.append("%s:%d 写参数含旧仓定位 %s" % (name, node.lineno, label))
        return hits

    calls_seen = 0
    for path in (SHIM_FILE, PLUGIN_FILE, SRC / "generator" / "emit_compat.py",
                 SRC / "generator" / "emit_pytest.py"):
        src = path.read_text(encoding="utf-8")
        if path in (SHIM_FILE, PLUGIN_FILE):
            for key in ("ATLAS_LEGACY_PROJECT", "ATLAS_LEGACY_WEB", "legacy_project"):
                assert key not in src, "%s 提到旧仓键 %s（垫片不该知道旧仓布局）" % (
                    path.name, key)
        hits = write_arg_hits(src, path.name)
        assert hits == [], hits
        if path.suffix == ".py":
            calls_seen += len([n for n in ast.walk(ast.parse(src))
                               if isinstance(n, ast.Call)
                               and getattr(n.func, "attr", "") in
                               ("write_text", "write_bytes")])
    assert calls_seen, "全仓写调用为零：扫描器恒真"


def test_c6d_compat_export_leaves_legacy_web_untouched(tmp_path):
    """行为面：生成 compat 包时 `ATLAS_LEGACY_WEB` 指向的目录**逐字节不动**。

    这是红线 #4 的产物侧兑现——静态禁词之外，真跑一次生成流程再量一次。写探针只打
    在 tmp 复刻的假旧仓上（真旧仓只读，见 c6a）。正向对照：产物目录确实被写了，
    证明「旧仓没动」不是因为「什么都没生成」。
    """
    web = _fake_legacy_web(tmp_path, fns=("_xcheck_open_list", "_xcheck_pick_row"))
    before = _manifest(web)
    out, written = _generate_compat(tmp_path, web=web)
    after = _manifest(web)
    assert before == after, "生成流程写了旧仓：%s" % \
        sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert written and out.is_dir() and list(out.rglob("*.py"))


# ---------------------------------------------------------------------------
# 派单第 7 条：compat 与 fast 的组合闸
# ---------------------------------------------------------------------------


def test_c9a_combo_gate_rejects_unpaired_form_with_named_message():
    """不给 --fast → 拒绝且消息同时点名两个键与「不隐式打开」（F-23A 文案面）。

    正对照=合法组合与缺省形态都不 raise（否则「拒绝」是恒拒）。CLI 与生成器两侧消息
    必须同源——这里读的是生成器侧判据本体的串。
    """
    with pytest.raises(ValueError) as got:
        cli._compat_gate("legacy-suite", False)
    msg = str(got.value)
    assert "--compat" in msg and "--fast" in msg and "隐式" in msg, msg
    cli._compat_gate("legacy-suite", True)
    cli._compat_gate("none", False)
    cli._compat_gate("none", True)
    problem = emit_compat.compat_combo_problem("legacy-suite", False)
    assert problem and "--fast" in problem
    assert emit_compat.compat_combo_problem("legacy-suite", True) == ""
    assert emit_compat.compat_combo_problem("none", False) == ""


@pytest.mark.parametrize("compat,fast", [
    ("none", False), ("none", True), ("legacy-suite", True), ("legacy-suite", False),
    ("bogus", True), ("bogus", False), ("", True), ("LEGACY-SUITE", True),
])
def test_c9b_two_entries_agree_on_combo_judge(compat, fast):
    """两入口（CLI `_compat_gate` vs 生成器 `compat_combo_problem`）**逐组合等值**。

    #18 把唯一判据位落在 emit_compat，而 cli.py 的 `_compat_gate` 仍是自己那条
    inline if（盘上实测 21:58 版未改）——两把尺一旦分叉，就会出现「CLI 拒、直调生成器
    过」或反向的漂移（#92 的入口面，与 lead 认定的 C9① 缝隙同族）。本格不要求实现
    长什么样，只钉**行为逐组合相等**；红 = 哪一格不等值就是漂移点。
    """
    problem = emit_compat.compat_combo_problem(compat, fast)
    cli_rejected = False
    cli_message = ""
    try:
        cli._compat_gate(compat, fast)
    except ValueError as exc:
        cli_rejected, cli_message = True, str(exc)
    assert cli_rejected == bool(problem), (
        "两入口分叉：compat=%r fast=%r → CLI 拒=%s（%r），生成器判据=%r。"
        "同一组合判据只能有一份（#92），CLI 侧应改为调用 compat_combo_problem。"
        % (compat, fast, cli_rejected, cli_message, problem))


@pytest.mark.parametrize("env_form", ["absolute-web", "relative-standard"])
def test_c9c_refused_compat_forms_leave_no_half_artifact(tmp_path, monkeypatch, env_form):
    """写盘前拒绝且**零残留**：api_call 腿 / page_object 腿 / 缺声明位 / 函数名核验不中。

    `env_form` 两态都跑（lead #28 第④条）：#27 P0 之后我不能再只喂「测试方便的那种
    绝对路径」——真实接线是 `.env` 里的相对子目录名。同一批拒绝判据必须在两种形态下
    各自成立，否则一条腿绿、另一条腿等于没测。
    每类各一条独立断言 + 消息点名是哪一类（F-23A），正对照=合法 legacy_pages 形态产出。
    """
    base = tmp_path / env_form
    proj, web = _fake_legacy_project(base, fns=("_xcheck_open_list", "_xcheck_pick_row"))
    if env_form == "absolute-web":
        _set_legacy_env(monkeypatch, proj, web)
    else:
        _set_legacy_env(monkeypatch, proj, "iservice")
    cases = [
        ("api_call 腿", {"id": "f-api", "steps": [
            {"id": "s1", "task": "api_call", "method": "GET", "path": "/a",
             "save_as": "r"}], "assert": []}, "api_call"),
        ("page_object 腿", {"id": "f-po", "steps": [
            {"id": "s2", "task": "open_doc_list", "page_object": "order_header",
             "read_only": True}], "assert": []}, "page_object"),
        ("缺声明位", {"id": "f-none", "steps": [
            {"id": "s3", "task": "open_doc_list", "read_only": True}], "assert": []},
         "声明位"),
        ("函数名核验不中", {"id": "f-badfn", "steps": [
            {"id": "s4", "task": "open_doc_list", "intent": "x", "read_only": True,
             "legacy_pages": "_xcheck_not_there"}], "assert": []}, "legacy_pages"),
    ]
    for i, (label, flow, expect_in_msg) in enumerate(cases):
        out_name = "refused%d" % i
        out = base / out_name
        with pytest.raises(ValueError) as got:
            emit_pytest_package(out_dir=out, flow=flow, components={},
                                fast=True, compat="legacy-suite")
        assert not out.exists(), "%s（%s）：拒绝却建了目录（半成品假可用）" % (
            label, env_form)
        assert expect_in_msg in str(got.value), \
            "%s（%s）：消息没点名判据位（%r）" % (label, env_form, str(got.value)[:140])
    # 正对照：合法形态在同 env_form 下正常产出（证上面四类不是恒拒）
    good = base / "good"
    written = emit_pytest_package(out_dir=good, flow=_compat_flow(), components={},
                                  fast=True, compat="legacy-suite")
    assert written and good.is_dir()


def test_xz_this_file_itself_parses():
    """常驻自哨（gap9 lead 要求）：本文件的字面量写坏会让**整场收集**中断。

    一条 collection error 顶掉所有人的门禁，比任何断言失败都贵；本格让「文件本身能
    parse」成为显式判据而不是巧合——bytes 里放非 ASCII、模板串引号嵌套这两类事故
    都在这一格里现形（本波已经历过一次）。
    """
    ast.parse(Path(__file__).resolve().read_text(encoding="utf-8"))

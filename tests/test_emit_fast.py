"""generator/emit_fast（Gap9 快模式底座模板）离线判据测。

零网络、零浏览器、零凭据（AGENTS §2；F-1 裁决：http.server/子进程真请求不进 tests/，
真请求取证=待授权 R5 由 lead 跑）。判据面：

- E.1.1 语法：两份模板 ast.parse+compile 绿。
- E.1.3/F-3/F-4 形态判据：每处 wait_for_timeout 实参恰为裸 TICK_MS（**对抗注入**：
  拿改坏 TICK_MS * 2 的文本喂同一条尺，必须红——否则判据只是「恰好没写」）；
  字面毫秒等待调用=0；time.sleep=0；pytest.skip=0。
- E.1.4 锁步：TICK_MS/_TRANSIENT_MARKS/RUN_STATUS_VOCAB/fail_class 值域与
  atlas.waits / atlas.failures 真值比对（从模板文本提取 + 加载产物模块双路）。
- E.1.5（F-1 离线半段）：假 step 记录直喂 RunWriter → runs.jsonl → emit_allure
  核状态映射（passed/failed+fail_class/env_blocked skipped+reason 原文/timeout broken），
  断言不存在任何洗绿分支。
- F-2：NotWired ⊂ EnvBlocked 类型级判据；真 NotImplementedError 不落 env_blocked（正向对照）。
- F-5：run 级 reason 只取框架点名文案——消息带 ?ticket= 的假异常不得把 query 值写上盘。
- F-7：缺配置/未接线两义分立，先查 env 再判接线。

每条负向断言同测带正向对照（scout 纪律）：先证明结构在位，再证明坏形态会红。
"""
from __future__ import annotations

import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

import atlas.waits as atlas_waits
from atlas.failures import FLOW_STATUS_TIMEOUT, FailureClass, is_operational
from atlas.allure_report import emit_allure
from atlas.generator.emit_fast import (
    ENV_KEYS, FAST_FIXTURE_NAMES, FAST_SUPPORT_MODULE_NAME, FastEmitError,
    emit_fast_conftest_module, emit_fast_support_module, write_fast_artifacts,
)

SUPPORT_SRC = emit_fast_support_module()
CONFTEST_SRC = emit_fast_conftest_module()

# 旧套件/session.py:26-31 同款六项禁用参数（测试侧独立声明=第二只眼，漂移即红）
SESSION_SIX_ARGS = ["--disable-gpu", "--disable-dev-shm-usage", "--disable-crash-reporter",
                    "--disable-breakpad", "--no-first-run", "--disable-extensions"]

# 产物允许 import 的顶层模块（F9 依赖栈：stdlib + pytest + playwright；_atlas_fast /
# atlas_pages 是产物目录内部模块；atlas.* 一律禁止——形态 3 自包含）
_ALLOWED_TOP = {"__future__", "os", "re", "json", "threading", "time", "uuid", "contextlib",
                "datetime", "pathlib", "msvcrt", "fcntl", "pytest", "playwright",
                "_thread", "_atlas_fast", "atlas_pages"}


# ---------------------------------------------------------------------------
# 加载辅助
# ---------------------------------------------------------------------------


def _assert_compiles(src: str) -> None:
    ast.parse(src)
    compile(src, "<emit_fast-template>", "exec")


def _load_artifact(tmp_path: Path, monkeypatch, request):
    """把两份模板写成真文件并 import（conftest 依赖 sys.path 上的 _atlas_fast）。

    只 import stdlib 层（support）与 conftest 模块本体——不跑任何用例、不起浏览器。
    """
    out = tmp_path / "artifact"
    out.mkdir(exist_ok=True)
    (out / f"{FAST_SUPPORT_MODULE_NAME}.py").write_text(SUPPORT_SRC, encoding="utf-8")
    (out / "conftest.py").write_text(CONFTEST_SRC, encoding="utf-8")
    monkeypatch.syspath_prepend(str(out))
    tag = request.node.name.replace("[", "_").replace("]", "_")
    for name in ("_atlas_fast", f"_fast_conftest_{tag}"):
        request.addfinalizer(lambda n=name: sys.modules.pop(n, None))
    import importlib
    support = importlib.import_module("_atlas_fast")
    spec = importlib.util.spec_from_file_location(
        f"_fast_conftest_{tag}", out / "conftest.py")
    conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conftest)
    return support, conftest


def _fixture_fn(fixture_obj):
    """取 fixture 装饰前的原函数（pytest 9 的 FixtureFunctionDefinition 暴露 __wrapped__）。"""
    return getattr(fixture_obj, "__wrapped__", None) or fixture_obj


class FakePage:
    """假 page：只实现底座用到的能力面，wait_for_timeout 断言实参=裸 TICK_MS（行为级 F-4）。"""

    def __init__(self, *, evaluate_seq=None, url_seq=None):
        self.evaluate_seq = list(evaluate_seq or [])
        self.url_seq = list(url_seq or [])
        self.ticks: list = []
        self.shots: list = []

    def wait_for_selector(self, selector, state=None, timeout=None):
        return None

    def evaluate(self, script, arg=None):
        if self.evaluate_seq:
            item = self.evaluate_seq.pop(0)
        else:
            item = 0
        if isinstance(item, BaseException):
            raise item
        return item

    def wait_for_timeout(self, ms):
        self.ticks.append(ms)

    @property
    def url(self):
        if self.url_seq:
            return self.url_seq.pop(0)
        return "about:blank"

    def is_closed(self):
        return False

    def screenshot(self, path=None, full_page=False):
        self.shots.append(path)
        Path(path).write_bytes(b"png-bytes")


class ShutteringPage(FakePage):
    """evaluate 首次抛导航瞬态、随后正常——「穿越 reload」的载体。"""


# ---------------------------------------------------------------------------
# C.1 公开面：契约值逐字
# ---------------------------------------------------------------------------


def test_public_surface_matches_contract():
    assert FAST_SUPPORT_MODULE_NAME == "_atlas_fast"
    assert issubclass(FastEmitError, ValueError)
    assert FAST_FIXTURE_NAMES == ("sole_page", "page", "context", "browser", "get_page",
                                  "plan_ctx", "_case_watchdog", "_fast_runlog", "_write_gate")
    # C.1 原九键 + F-15 补 BASE_URL/CDP/CDP_PORT + gap9b :122 补 INTERACTIVE/ATTACH_MATCH
    # + 重构波 T3/T4/T5 补 PARALLEL/TRACE/BROWSER_CHANNEL
    assert ENV_KEYS == {
        "CASE_TIMEOUT": "600", "ATLAS_FAST_ENV": "local",
        "ATLAS_FAST_LOGIN_INDICATOR": None, "ATLAS_FAST_ACCOUNT": "DEMO_USERNAME",
        "ATLAS_FAST_PASSWORD": "DEMO_PASSWORD", "ATLAS_FAST_ALLOW_WRITES": "0",
        "ATLAS_RUNS_DIR": None, "ATLAS_BROWSER_EXECUTABLE": None,
        "CDP_DEBUG": "0", "CDP_PORT": "9222",
        "ATLAS_FAST_BASE_URL": None, "ATLAS_FAST_CDP": None,
        "ATLAS_FAST_INTERACTIVE": "0", "ATLAS_FAST_ATTACH_MATCH": None,
        "ATLAS_BROWSER_CHANNEL": None, "ATLAS_FAST_PARALLEL": None,
        "ATLAS_FAST_TRACE": "0", "ATLAS_FAST_TRACE_ACCEPT_SECRETS": "0"}
    assert ENV_KEYS["ATLAS_BROWSER_CHANNEL"] is None      # T5 缺省=chromium（无字面默认）
    assert ENV_KEYS["ATLAS_FAST_PARALLEL"] is None        # T3 缺省=单进程
    assert ENV_KEYS["ATLAS_FAST_TRACE"] == "0"            # T4 缺省关=成功腿零开销
    assert ENV_KEYS["ATLAS_FAST_TRACE_ACCEPT_SECRETS"] == "0"   # T4 红线#2 第二确认键缺省关
    assert ENV_KEYS["ATLAS_FAST_LOGIN_INDICATOR"] is None   # 无默认（红线 #11）
    assert ENV_KEYS["ATLAS_FAST_CDP"] is None               # 缺省=launch 通道（B-3）
    assert ENV_KEYS["ATLAS_FAST_INTERACTIVE"] == "0"        # 在场闸缺省关（:122 明文）
    assert ENV_KEYS["ATLAS_FAST_ATTACH_MATCH"] is None      # 无默认域名（#11）
    # 正向对照：其余键确有回落默认
    assert ENV_KEYS["CASE_TIMEOUT"] == "600"


# F-15/B-3 自证测：外部回落名单（Atlas 既有 env 命名，产物只作 fallback 读，不是 FAST 旋钮）
_EXTERNAL_FALLBACK_KEYS = {"DEMO_USERNAME", "DEMO_PASSWORD", "ATLAS_BASE_URL"}
# 读取形态：os.environ.get("K") 与纯函数注入口 env.get("K")/env_snapshot.get("K")——
# session_channel 家族吃传入 dict，键名同样是字面量，扫描不留死角。
_ENV_READ_RE = re.compile(
    r"""(?:os\.environ|env|env_snapshot)\.get\(\s*["']([A-Za-z0-9_]+)["']""")


def test_env_knobs_manifest_covers_every_template_read():
    """F-15 配套义务：ENV_KEYS 是「运行期旋钮**唯一**清单」——这条判据必须自证。

    正向：模板内每一处环境读取的键名 ∈ ENV_KEYS ∪ 外部回落名单；
    反向：清单里每个键都被模板真实读取（漏读=死旋钮，F-15 原文「清单漏键=判据当场自破」
    的镜像形态）。正向对照：扫描确有命中且含三个外部回落键——否则「无越界」是恒真。
    （B-3 落地时本测实证：补 INTERACTIVE/ATTACH_MATCH 两键未接读点→精确红点名死旋钮。）"""
    for src in (SUPPORT_SRC, CONFTEST_SRC):
        # 读取形态闭合：只放 .get("字面量")（索引/getenv 会绕过本扫描）
        assert "os.environ[" not in src and "os.getenv" not in src
    reads = set(_ENV_READ_RE.findall(SUPPORT_SRC)) | set(_ENV_READ_RE.findall(CONFTEST_SRC))
    assert len(reads) >= len(ENV_KEYS), f"扫描失效：命中 {sorted(reads)}"
    assert _EXTERNAL_FALLBACK_KEYS <= reads, "回落链必须真在模板里被读到（判据非恒真）"
    unknown = reads - set(ENV_KEYS) - _EXTERNAL_FALLBACK_KEYS
    assert not unknown, f"模板出现清单外旋钮（F-15：补 ENV_KEYS 或改模板，别口头放行）: {sorted(unknown)}"
    dead = set(ENV_KEYS) - reads
    assert not dead, f"清单里没人读的死旋钮: {sorted(dead)}"
    # 正向对照：B-3 三条件键 + 选页键都在实读面
    assert {"ATLAS_FAST_CDP", "ATLAS_FAST_INTERACTIVE", "ATLAS_FAST_ATTACH_MATCH"} <= reads


# 消费方禁另抄（F-15 第3条 + G1）：这些判据常量的值只许 import，不许第二份字面量
_CREDENTIAL_TABLE_NAMES = ("RUN_STATUS_VOCAB", "FAIL_CLASS_MAP", "REASON_MAX_LEN",
                           "SCALAR_MAX_LEN", "ENV_KEYS")
_RUN_STATUS_VALUES = frozenset(
    ("passed", "failed", "env_blocked", "interrupted", "waiting", "timeout"))
_FAIL_CLASS_MAP_VALUES = frozenset(("assertion", "interaction", "env_blocked", "unknown"))


def _copied_credential_literals(src: str) -> list[str]:
    """检出「整表复制」形态：模块级重定义判据名，或字面容器覆盖整张表的值域。

    单个字符串（如 help 文案里的 env_blocked）不算——禁的是第二份**表**，不是词汇。
    判据函数不得先于被检对象死（#89 家族）：文件本身语法坏时点名「无法解析」，
    让红归因到坏文件，而不是本测裸崩成「判据测坏了」的假象。"""
    hits: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"文件无法解析（语法坏，复制判据无从排除）：line {exc.lineno}: {exc.msg}"]
    for node in tree.body:
        if isinstance(node, ast.Assign):
            node_targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            node_targets = [node.target]
        else:
            node_targets = []
        for t in node_targets:
            if isinstance(t, ast.Name) and t.id in _CREDENTIAL_TABLE_NAMES:
                hits.append(f"模块级重定义 {t.id}")
    for node in ast.walk(tree):
        consts: list = []
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            consts = [e.value for e in node.elts if isinstance(e, ast.Constant)]
        elif isinstance(node, ast.Dict):
            consts = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            consts += [v.value for v in node.values if isinstance(v, ast.Constant)]
        strings = {c for c in consts if isinstance(c, str)}
        if len(strings) >= 4 and strings >= _RUN_STATUS_VALUES:
            hits.append(f"字面容器复制 RUN_STATUS_VOCAB 全值域: {sorted(strings)}")
        elif len(strings) >= 4 and strings >= _FAIL_CLASS_MAP_VALUES:
            hits.append(f"字面容器复制 FAIL_CLASS_MAP 值域: {sorted(strings)}")
    return hits


def test_consumers_must_import_credential_tables_not_copy_them():
    """F-15 第3条：渲染层（generator 包）与 xcheck 产物测只许 import 三张表。

    反面形态很具体：谁为让自己测通过而复制一份映射表，产物侧与 Atlas 侧归因就各自演化，
    漂移只在真机上以「同一条失败两处分类不同」暴露（#92）。
    本测自身是锁步位（第二次声明=防线本体），故排除在外；emit_fast.py 是唯一合法定义方。"""
    root = Path(__file__).resolve().parents[1]
    targets = [p for p in (root / "src" / "atlas" / "generator").glob("*.py")
               if p.name != "emit_fast.py"]
    xcheck = root / "tests" / "test_fast_artifact_executable.py"
    if xcheck.exists():
        targets.append(xcheck)
    offenders = {}
    for p in targets:
        hits = _copied_credential_literals(p.read_text(encoding="utf-8", errors="replace"))
        if hits:
            offenders[p.name] = hits
    assert not offenders, f"消费方另抄判据表（只许 import emit_fast/_atlas_fast）: {offenders}"
    # 正向对照①：扫描不是摆设——注入一份复制表必须被检出
    mutant = ('RUN_STATUS_VOCAB = ("passed", "failed", "env_blocked", '
              '"interrupted", "waiting", "timeout")\n')
    assert _copied_credential_literals(mutant)
    mutant2 = ('_MAP = {"AssertionError": "assertion", "TimeoutError": "interaction", '
               '"EnvBlocked": "env_blocked", "rest": "unknown"}\n')
    assert _copied_credential_literals(mutant2)
    # 正向对照②：合法定义方确实只有 emit_fast（表在本模块真存在，不是空判）。
    # 名单从 _CREDENTIAL_TABLE_NAMES 派生（:190 唯一声明位，此处不再手抄第二份——G1 同源修）
    define_src = (root / "src" / "atlas" / "generator" / "emit_fast.py").read_text(encoding="utf-8")
    for name in _CREDENTIAL_TABLE_NAMES:
        assert f"{name} = " in define_src or f"{name} = (" in define_src, name


def test_template_first_lines_are_contract_pinned():
    assert SUPPORT_SRC.splitlines()[0] == (
        '"""生成候选（generator/emit_fast）：快模式底座（自包含：仅标准库+playwright，'
        "可脱离 Atlas 运行）。")
    assert CONFTEST_SRC.splitlines()[0] == (
        '"""生成候选（generator/emit_fast）：快模式 fixtures（单浏览器/单登录/看门狗/写闸/runlog）')


def test_templates_compile_and_emitters_are_pure_text():
    _assert_compiles(SUPPORT_SRC)
    _assert_compiles(CONFTEST_SRC)
    assert emit_fast_support_module() is SUPPORT_SRC
    assert emit_fast_conftest_module() is CONFTEST_SRC
    # 红线 #9：emit_fast 本体不 import 被执行产物、不起子进程、不碰网络
    emit_src = (Path(__file__).resolve().parents[1] / "src" / "atlas" / "generator" /
                "emit_fast.py").read_text(encoding="utf-8")
    for banned in ("subprocess", "socket", "http.", "urllib", "importlib"):
        assert banned not in emit_src, banned
    # 防环：不 import emit_pytest
    assert "emit_pytest" not in [n for n in _imported_names(emit_src)]


def _imported_names(src: str) -> list[str]:
    """AST 收集 import 顶层名（含函数体内的 lazy import——扫描不留死角）。"""
    names: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
    return names


def test_artifact_import_surface_is_bounded():
    """F9/E.1.9：产物只 import stdlib/pytest/playwright/内部模块；绝不 import atlas 包。

    正向对照：扫描前先证明 playwright/pytest 确实在清单里被引用（扫描恒假的温床）。"""
    for label, src in (("support", SUPPORT_SRC), ("conftest", CONFTEST_SRC)):
        names = _imported_names(src)
        extra = set(names) - _ALLOWED_TOP
        assert not extra, f"{label} 越界 import: {sorted(extra)}"
        assert "atlas" not in names
    # 正向对照
    assert "playwright" in _imported_names(CONFTEST_SRC)
    assert "pytest" in _imported_names(CONFTEST_SRC)
    assert "requests" not in _imported_names(SUPPORT_SRC)
    assert "allure" not in "".join(_imported_names(SUPPORT_SRC) + _imported_names(CONFTEST_SRC))


def test_conftest_atlas_fast_imports_cover_usage():
    """NameError 形态防线：conftest 用到的每个 _atlas_fast 符号必须在 import 清单里。

    模板只 compile 不代表运行得起来——NotWired 曾「raise 在体内、名字没进 import」，
    ast.parse 全绿、真调用才炸（本节 test_cdp_endpoint… 实抓）。"""
    imported: set = set()
    support_defined = {n.name for n in ast.parse(SUPPORT_SRC).body
                       if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    support_defined |= {t.id for n in ast.parse(SUPPORT_SRC).body if isinstance(n, ast.Assign)
                        for t in n.targets if isinstance(t, ast.Name)}
    for node in ast.parse(CONFTEST_SRC).body:
        if isinstance(node, ast.ImportFrom) and node.module == "_atlas_fast":
            imported = {a.name for a in node.names}
    assert imported, "conftest 必须从 _atlas_fast 导入底座符号"
    assert imported <= support_defined, f"导入了底座不存在的名字: {imported - support_defined}"
    used = {"EnvBlocked", "NotWired", "RunWriter", "classify_fail_class", "framework_reason",
            "launch_kwargs", "load_plan_ctx", "save_plan_ctx", "set_current_page",
            "FLOW_STATUS_TIMEOUT", "session_channel", "attach_denied_by_prod"}
    missing = {s for s in used if re.search(rf"\b{s}\b", CONFTEST_SRC) and s not in imported}
    assert not missing, f"conftest 引用但未导入: {missing}"


def test_no_business_literals_in_templates():
    """红线 #11：模板零业务字面量（正向对照：env 键名确在，走旋钮而非硬编码）。"""
    for label, src in (("support", SUPPORT_SRC), ("conftest", CONFTEST_SRC)):
        assert not re.search(r"https?://", src), f"{label} 含 URL 字面量"
        assert "isc-app" not in src
        assert "${" not in src, f"{label} 不该内嵌 DSL 占位符（产物底座自己无插值）"
        assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", src), f"{label} 含 UUID 形态"
    assert 'os.environ.get("ATLAS_FAST_LOGIN_INDICATOR"' in CONFTEST_SRC   # 指示器只走 env


# ---------------------------------------------------------------------------
# E.1.3 / F-3 / F-4：等待形态判据（实参=裸 TICK_MS），含对抗注入
# ---------------------------------------------------------------------------


def _tick_arg_violations(src: str) -> list[str]:
    """每处 wait_for_timeout( 的实参必须是裸标识符 TICK_MS。

    同一条尺拒：字面毫秒、乘算（TICK_MS * 2）、其它表达式/变量。"""
    bad = []
    for arg in re.findall(r"wait_for_timeout\(([^()]*)\)", src):
        if arg.strip() != "TICK_MS":
            bad.append(arg.strip())
    return bad


def test_tick_form_judgment_rejects_injected_multiply():
    """F-4 对抗测：注入 `TICK_MS * 2` 的同一条扫描尺必须红——判据强度取决于它拒什么。"""
    # 正向对照：真模板里 tick 调用确实存在且全合规（否则「0 违规」恒真）
    occurrences = re.findall(r"wait_for_timeout\(", SUPPORT_SRC)
    assert len(occurrences) >= 3, "四个语义等待的轮询 tick 必须真实存在"
    assert _tick_arg_violations(SUPPORT_SRC) == []
    mutant = SUPPORT_SRC.replace("page.wait_for_timeout(TICK_MS)",
                                 "page.wait_for_timeout(TICK_MS * 2)", 1)
    assert mutant != SUPPORT_SRC, "注入本身要先成立（模板里确有该调用点）"
    assert _tick_arg_violations(mutant) == ["TICK_MS * 2"]
    literal = SUPPORT_SRC.replace("page.wait_for_timeout(TICK_MS)",
                                  "page.wait_for_timeout(2000)", 1)
    assert _tick_arg_violations(literal) == ["2000"]


def test_semantic_waiters_defined_and_used():
    """F3 正向对照：语义等待不是摆设——四个 waiter 定义在底座且模板内被引用/导出。"""
    for fn in ("def wait_for_rows(", "def wait_for_mask_gone(", "def wait_for_vue_field(",
               "def wait_for_url_change("):
        assert fn in SUPPORT_SRC, fn
    # 「等条件成立而非睡死」的判据形态：轮询循环 + 业务条件求值
    assert "timeout_ms // TICK_MS" in SUPPORT_SRC
    assert "count >= min_rows" in SUPPORT_SRC
    # 负向：conftest 不许出现任何等待睡眠
    assert "wait_for_timeout" not in CONFTEST_SRC
    assert _tick_arg_violations(CONFTEST_SRC) == []


def test_no_fixed_sleep_channel_anywhere():
    """F-3 定稿：time.sleep 不在产物里（它是绕开 TICK 判据的第二睡眠通道）。

    正向对照：time 模块确被使用（perf_counter 计步耗时）——扫描的不是「没用 time」，
    而是「time 没被拿来睡」。"""
    for label, src in (("support", SUPPORT_SRC), ("conftest", CONFTEST_SRC)):
        assert not re.search(r"\.sleep\(", src), f"{label} 含 sleep 调用"
        assert not re.search(r"wait_for_timeout\(\s*[0-9]", src)
    assert "time.perf_counter()" in SUPPORT_SRC


def test_transient_error_split_present():
    """#133 移植判据：瞬态可穿越 reload、不可恢复立即传播（正向在测里真执行）。"""
    assert '_TRANSIENT_MARKS = ("execution context was destroyed", "navigation", "detached")' \
        in SUPPORT_SRC
    assert "if not _is_transient(exc):" in SUPPORT_SRC


# ---------------------------------------------------------------------------
# E.1.4 锁步判据（模板文本/加载模块 ↔ atlas 真值）
# ---------------------------------------------------------------------------


def test_transient_marks_lockstep_with_atlas_waits(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support._TRANSIENT_MARKS == atlas_waits._TRANSIENT_MARKS


def test_tick_ms_lockstep_with_waits_source(tmp_path, monkeypatch, request):
    """waits.py 无 TICK 常量，锁步押在它的轮询步长字面量上（timeout_ms // 100）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    waits_src = Path(atlas_waits.__file__).read_text(encoding="utf-8")
    ticks = set(re.findall(r"timeout_ms // (\d+)", waits_src))
    assert ticks == {"100"}, "上游 tick 变了要先同步判据，再重新生成产物"
    assert support.TICK_MS == 100


def test_run_status_vocab_lockstep_with_failures(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support.RUN_STATUS_VOCAB == ("passed", "failed", "env_blocked",
                                        "interrupted", "waiting", "timeout")
    assert support.FLOW_STATUS_TIMEOUT == FLOW_STATUS_TIMEOUT
    legal = {f.value for f in FailureClass} | {"passed", "failed"} | {FLOW_STATUS_TIMEOUT}
    assert set(support.RUN_STATUS_VOCAB) <= legal
    # 操作/环境家族 + timeout 必须在产物词汇里（消费侧才认得）
    for member in ("env_blocked", "interrupted", "waiting", FLOW_STATUS_TIMEOUT):
        assert member in support.RUN_STATUS_VOCAB
    # 正向对照：is_operational 认家族三成员，timeout 刻意不入族（评审 C9-E）
    assert all(is_operational(v) for v in ("env_blocked", "interrupted", "waiting"))
    assert not is_operational(FLOW_STATUS_TIMEOUT)


def test_fail_class_values_are_failure_class_members(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    mapped = {fc for _, fc in support.FAIL_CLASS_MAP}
    all_values = mapped | {support.FAIL_CLASS_DEFAULT, support.FAIL_CLASS_INTERRUPTED,
                           support.FAIL_CLASS_WAITING}
    members = {f.value for f in FailureClass}
    assert all_values <= members, f"产物自造了词表项: {all_values - members}"
    # C.1 四值齐备（assertion/interaction/env_blocked/unknown）
    assert {"assertion", "interaction", "env_blocked", "unknown"} <= all_values
    # 正向对照：产物里根本没有带引号的 "skipped" 状态词（洗绿入口焊死在消费侧之外）
    assert '"skipped"' not in SUPPORT_SRC and '"skipped"' not in CONFTEST_SRC


def test_session_launch_args_lockstep(tmp_path, monkeypatch, request):
    """F2/E.1.6：六项禁用参数与 session.py 同集合（测试侧独立声明，改一漏一必红）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    kwargs = support.launch_kwargs(headless=True)
    assert kwargs["args"] == SESSION_SIX_ARGS
    assert kwargs["headless"] is True and kwargs["slow_mo"] == 0
    assert "executable_path" not in kwargs   # 负向：未设 env 不注 executor
    # 正向对照：有头/CDP/系统浏览器逃生舱逐项在位
    headed = support.launch_kwargs(headless=False)
    assert headed["slow_mo"] == 300 and "--start-maximized" in headed["args"]
    monkeypatch.setenv("CDP_DEBUG", "1")
    assert support.launch_kwargs(headless=False)["args"][-1] == "--remote-debugging-port=9222"
    # CDP_DEBUG 不翻转无头默认下的参数面（无头腿仍恰为六项）
    assert support.launch_kwargs(headless=True)["args"] == SESSION_SIX_ARGS
    monkeypatch.delenv("CDP_DEBUG")
    monkeypatch.setenv("ATLAS_BROWSER_EXECUTABLE", "/usr/bin/chromium")
    assert support.launch_kwargs(headless=True)["executable_path"] == "/usr/bin/chromium"


# ---------------------------------------------------------------------------
# F1/F2 conftest 结构（单浏览器/选项防线/插件注销）
# ---------------------------------------------------------------------------


def test_conftest_single_browser_structure():
    """E.1.6：sole_page 是 module scope；launch 点全文唯一；page/context/browser 转发。"""
    tree = ast.parse(CONFTEST_SRC)
    fn_scopes = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                dec_name = (dec.func.attr if isinstance(dec.func, ast.Attribute)
                            else getattr(dec.func, "id", ""))
                if dec_name == "fixture":
                    kw = {k.arg: ast.literal_eval(k.value) for k in dec.keywords}
                    fn_scopes[node.name] = kw.get("scope", "function")
    for name in FAST_FIXTURE_NAMES:
        assert name in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}, name
    assert fn_scopes.get("sole_page") == "module"
    # 正向对照：launch 恰一处（=0 意味着根本没有浏览器，判据要拒的是「多处」不是「没有」）
    launches = re.findall(r"pw\.chromium\.launch\(", CONFTEST_SRC)
    assert len(launches) == 1
    assert "viewport" in CONFTEST_SRC and "1920" in CONFTEST_SRC and "1080" in CONFTEST_SRC
    assert "return sole_page" in CONFTEST_SRC          # page 转发给 sole_page
    assert "accept_downloads=True" in CONFTEST_SRC


def test_conftest_plugin_and_option_defenses():
    assert "config.pluginmanager.getplugin(\"playwright\")" in CONFTEST_SRC
    assert "config.pluginmanager.unregister(" in CONFTEST_SRC
    assert "--headed" in CONFTEST_SRC and "--headless" in CONFTEST_SRC
    assert 'os.environ.get("CDP_DEBUG") == "1"' in CONFTEST_SRC   # 强制有头对齐 session.py
    # 正向对照：注销逻辑在 pytest_configure 里真实接线
    configure_body = CONFTEST_SRC.split("def pytest_configure(config):")[1].split("def ")[0]
    assert "playwright" in configure_body


def test_conftest_credentials_only_from_env():
    """红线 #1：凭据只从 env 读；模板里不出现任何默认口令/账号字面量。"""
    assert 'os.environ.get("ATLAS_FAST_ACCOUNT"' in CONFTEST_SRC
    assert 'os.environ.get("DEMO_USERNAME"' in CONFTEST_SRC      # 回落链在位
    assert 'os.environ.get("ATLAS_FAST_PASSWORD"' in CONFTEST_SRC
    assert "password=" in CONFTEST_SRC and 'os.environ.get("PASSWORD"' not in CONFTEST_SRC


def test_conftest_no_bare_skip_and_explicit_block():
    """F5：产物用例层禁裸 pytest.skip；正向对照——显式 EnvBlocked 终止点真实在位。"""
    assert "pytest.skip(" not in CONFTEST_SRC
    assert CONFTEST_SRC.count("raise EnvBlocked(") >= 3
    assert "require_ctx" in (SUPPORT_SRC)
    assert "缺前置键" in SUPPORT_SRC   # F5 点名文案（同 Atlas FAQ 分批纪律）


def test_f7_two_meanings_split_and_order(tmp_path, monkeypatch, request):
    """F-7：缺配置(EnvBlocked「缺配置：」) vs 未接线(NotWired「登录未接线：」)，先 env 后接线。"""
    src = CONFTEST_SRC.split("def get_page(")[1]
    env_pos = src.index("缺配置：ATLAS_FAST_LOGIN_INDICATOR")
    cred_pos = src.index("缺配置：ATLAS_FAST_ACCOUNT")
    wire_pos = src.index("登录未接线：atlas_pages")
    assert env_pos < cred_pos < wire_pos, "求值次序固定：先查 env，再判接线"
    # 未接线的异常类型必须是 NotWired（不是 EnvBlocked/NotImplementedError）
    seg = src[wire_pos - 60:wire_pos]
    assert "NotWired" in seg
    # 禁止 or 文案混两义（判据面=get_page 函数体；模块 docstring 引用禁则是元评论，不算）
    assert "未接线或未配置" not in src
    # 实查为权威：复用依据是 locator(indicator).count()，磁盘标记只记录
    assert "page.locator(indicator).count() > 0" in src
    assert 'plan_ctx["_logged_in_account"]' in src and "只做记录" in src


# ---------------------------------------------------------------------------
# F-2：NotWired 类型级判据（行为面，直接驱动产物模块）
# ---------------------------------------------------------------------------


def test_notwired_is_envblocked_subclass(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert issubclass(support.NotWired, support.EnvBlocked)
    # reason 必须点名：裸一条构造期拒（与 Atlas AwaitingHuman 同纪律）
    with pytest.raises(ValueError):
        support.NotWired("   ")
    with pytest.raises(ValueError):
        support.AwaitingHuman("")
    assert support.classify_fail_class(
        support.NotWired("登录未接线：order_list.query 按钮")) == "env_blocked"


def test_notwired_lands_env_blocked_with_positive_control(tmp_path, monkeypatch, request):
    """E.1.11：NotWired 腿 runs.jsonl status=env_blocked（正向对照：真 AssertionError 仍 failed）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "runs.jsonl"
    w = support.RunWriter("test_ui::test_query", "local", path=runs)
    exc = None
    try:
        with w.step("query"):
            raise support.NotWired("未接线：OrderList.query 按钮")
    except support.EnvBlocked as e:
        exc = e
    status, reason = ("env_blocked", support.framework_reason(exc))
    w.close(status, reason=reason)
    # 正向对照：已接线路径上的真断言失败不被同一支洗掉
    w2 = support.RunWriter("test_ui::test_echo", "local", path=runs)
    with pytest.raises(AssertionError):
        with w2.step("assert 列表回显"):
            raise AssertionError("mismatch")
    w2.close("failed")
    lines = [json.loads(x) for x in runs.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["status"] == "env_blocked"
    assert "OrderList.query" in lines[0]["reason"]          # 点名组件+元素
    assert lines[0]["steps"][0]["fail_class"] == "env_blocked"
    assert lines[1]["status"] == "failed"
    assert "reason" not in lines[1]                          # 失败行不写 reason（同 Atlas 口径）
    assert lines[1]["steps"][0]["fail_class"] == "assertion"


def test_notimplemented_error_is_not_unwired(tmp_path, monkeypatch, request):
    """F-2 边界：真 NotImplementedError（代码缺陷）落 unknown——不许当未接线洗成 skipped。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support.classify_fail_class(NotImplementedError()) == "unknown"
    assert support.classify_fail_class(RuntimeError("x")) == "unknown"
    assert support.classify_fail_class(KeyboardInterrupt()) == "interrupted"
    # 正向对照：四类映射逐项在位
    assert support.classify_fail_class(AssertionError("x")) == "assertion"
    assert support.classify_fail_class(TimeoutError()) == "interaction"
    assert support.classify_fail_class(support.EnvBlocked("缺配置：X")) == "env_blocked"
    assert support.classify_fail_class(support.AwaitingHuman("等审批")) == "waiting"


# ---------------------------------------------------------------------------
# F-5：reason 边界（框架点名文案 only，不复制第二把净化刀）
# ---------------------------------------------------------------------------


def test_framework_reason_blocks_uncontrolled_text(tmp_path, monkeypatch, request):
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "runs.jsonl"
    # 对抗样本：消息带 SSO 回调 query 的假异常喂进收尾判据
    hostile = RuntimeError("GET https?://sso.example/callback?ticket=abc123def456 500")
    status, reason = conftest._runlog_status_reason([("call", hostile)])
    assert (status, reason) == ("failed", "")
    w = support.RunWriter("test_x::t", "local", path=runs)
    w.close(status, reason=reason)
    line = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
    assert "ticket" not in json.dumps(line, ensure_ascii=False)   # 边界本体
    assert "reason" not in line                                   # 非空才写键
    # 正向对照：框架点名文案照写，且截 200
    named = support.EnvBlocked("缺前置键 orderNo：先跑前置用例/勿删 plan_ctx.json" + "啊" * 300)
    status2, reason2 = conftest._runlog_status_reason([("setup", named)])
    assert status2 == "env_blocked" and "缺前置键 orderNo" in reason2
    assert len(reason2) <= 200
    # 断言消息不进 reason（走 step 名，allure 从 steps 取）
    status3, reason3 = conftest._runlog_status_reason(
        [("call", AssertionError("期望 BUG-000 实得 SPM-612"))])
    assert (status3, reason3) == ("failed", "")


def test_first_exception_wins_and_control_flow(tmp_path, monkeypatch, request):
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    blocked = support.EnvBlocked("写操作保护：ATLAS_FAST_ALLOW_WRITES 未置位（策略阻挡）")
    # setup 被写闸拦（call 不会跑）→ env_blocked 且 reason 点名旋钮
    status, reason = conftest._runlog_status_reason([("setup", blocked)])
    assert status == "env_blocked" and "ATLAS_FAST_ALLOW_WRITES" in reason
    assert conftest._runlog_status_reason([]) == ("passed", "")
    s, r = conftest._runlog_status_reason([("call", KeyboardInterrupt())])
    assert (s, r) == ("interrupted", "")
    s, r = conftest._runlog_status_reason(
        [("call", support.AwaitingHuman("等 UAT 验证码") )])
    assert (s, r) == ("waiting", "等 UAT 验证码")


# ---------------------------------------------------------------------------
# F6/F7：RunWriter schema + 看门狗不翻绿 + ATLAS_RUNS_DIR
# ---------------------------------------------------------------------------


def test_run_writer_schema_and_default_close(tmp_path, monkeypatch, request):
    """E.1.5 前半：行 schema 逐键核对（缺省 close 不写 reason 键，与 recorder.py 逐字同纪律）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "reports" / "runs.jsonl"
    w = support.RunWriter("test_a::test_one", "local", path=runs)
    with w.step("open list"):
        pass
    with w.step("assert 列表回显"):
        pass
    w.close()
    line = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
    assert set(line) == {"run_id", "case", "env", "mode", "status",
                         "started_at", "finished_at", "steps"}
    assert line["status"] == "passed" and len(line["run_id"]) == 32
    assert line["started_at"].endswith("+00:00")   # UTC ISO
    for step in line["steps"]:
        assert set(step) == {"step", "op", "ok", "dur_ms"}
        assert step["ok"] is True
    assert line["steps"][1]["step"].startswith("assert ")
    # 二次 close 幂等：不再落第二行（看门狗行不被翻绿的机制底座）
    before = runs.read_text(encoding="utf-8")
    w.close("passed")
    assert runs.read_text(encoding="utf-8") == before
    assert w.closed is True


def test_run_writer_rejects_unknown_status(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "runs.jsonl"
    w = support.RunWriter("t::x", "local", path=runs)
    with pytest.raises(ValueError):
        w.close("skipped")   # 产物词表里没有 skipped——洗绿入口在写侧焊死
    # 正向对照：词表内终态照常可写
    w.close("failed")
    assert json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])["status"] == "failed"


def test_watchdog_writes_timeout_line_and_cannot_be_overwritten(tmp_path, monkeypatch, request):
    """F6/E.1.7：timeout 行 + reason 点名 CASE_TIMEOUT；close 幂等使收尾不得改写。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "runs.jsonl"
    w = support.RunWriter("test_slow::test_hang", "local", path=runs)
    with w.step("open list"):
        pass
    reason = conftest._watchdog_timeout_reason(600.0)
    assert "watchdog CASE_TIMEOUT=" in reason and "600s" in reason
    w.close(support.FLOW_STATUS_TIMEOUT, reason=reason)
    w.close("passed")          # 主线程收尾迟到——必须 no-op
    w.close("interrupted")     # 任何翻族尝试同样无效（close 幂等）
    lines = [json.loads(x) for x in runs.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["status"] == "timeout"
    assert "CASE_TIMEOUT" in lines[0]["reason"]
    # 源码级：watchdog 只写 FLOW_STATUS_TIMEOUT 一个词，不出现 skipped/passed/interrupted 改写
    wd_seg = CONFTEST_SRC.split("def _case_watchdog")[1].split("@pytest.fixture")[0]
    assert "writer.close(FLOW_STATUS_TIMEOUT" in wd_seg
    assert wd_seg.index("writer.close(") < wd_seg.index("_thread.interrupt_main()"), \
        "F6 次序判据：先落 timeout 行，再打断主线程"
    for green in ('"passed"', '"skipped"', '"interrupted"'):
        assert green not in wd_seg


def test_atlas_runs_dir_escape_hatch(tmp_path, monkeypatch, request):
    """F7：ATLAS_RUNS_DIR 与 Atlas 同语义（相对值按目标父目录解析；绝对值原样）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    w0 = support.RunWriter("t::a", "local", path=tmp_path / "reports" / "runs.jsonl")
    assert w0.path == tmp_path / "reports" / "runs.jsonl"       # 负向：未设 env 走原路径
    monkeypatch.setenv("ATLAS_RUNS_DIR", "parallel-b")
    w1 = support.RunWriter("t::b", "local", path=tmp_path / "reports" / "runs.jsonl")
    assert w1.path == tmp_path / "reports" / "parallel-b" / "runs.jsonl"
    monkeypatch.setenv("ATLAS_RUNS_DIR", str(tmp_path / "abs-dir"))
    w2 = support.RunWriter("t::c", "local", path=tmp_path / "reports" / "runs.jsonl")
    assert w2.path == tmp_path / "abs-dir" / "runs.jsonl"       # 正向：绝对值原样
    # 默认路径=产物目录 reports/runs.jsonl（底座 __file__ 定位；先撤 override 再验缺省）
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    w3 = support.RunWriter("t::d", "local")
    assert w3.path.name == "runs.jsonl" and w3.path.parent.name == "reports"
    # 正向对照：缺省路径正是产物目录内那份（_load_artifact 写模板的目录=tmp/artifact）
    assert w3.path == tmp_path / "artifact" / "reports" / "runs.jsonl"


def test_screenshot_only_on_failure_and_absolute(tmp_path, monkeypatch, request):
    """F4/E.1：成功步骤零截图；失败腿截图落 reports/artifacts 且 attachment 为绝对路径。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "reports" / "runs.jsonl"
    page = FakePage()
    support.set_current_page(page)
    try:
        w = support.RunWriter("test_ui::test_query", "local", path=runs)
        with w.step("open list"):
            pass
        with pytest.raises(RuntimeError):
            with w.step("click query"):
                raise RuntimeError("intercepted")
        w.close("failed")
        line = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
        ok_step, bad_step = line["steps"]
        assert "attachment" not in ok_step                      # 成功腿不取证（红线 #8）
        shot = Path(bad_step["attachment"])
        assert shot.is_absolute() and shot.exists()
        assert shot.parent.name == support.ARTIFACTS_DIR_NAME
        assert shot.parent == runs.parent / "artifacts"
        assert shot.name == "test_ui__test_query_2.png"         # <case>_<step序号>（:: 消毒成双下划线）
        assert bad_step["fail_class"] == "unknown"
    finally:
        support.set_current_page(None)


def test_capture_failure_never_masks_real_error(tmp_path, monkeypatch, request):
    """取证尽力而为：截图自身抛错不顶掉真因、不改变失败判定（session.py 纪律移植）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "runs.jsonl"

    class BadShotPage(FakePage):
        def screenshot(self, path=None, full_page=False):
            raise RuntimeError("browser crashed")

    support.set_current_page(BadShotPage())
    try:
        w = support.RunWriter("t::x", "local", path=runs)
        with pytest.raises(AssertionError):                     # 真因原样传播
            with w.step("assert x"):
                assert False
        w.close("failed")
        line = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
        assert "attachment" not in line["steps"][0]
        assert line["steps"][0]["fail_class"] == "assertion"
    finally:
        support.set_current_page(None)


def _fake_request(op=None, node=None):
    """造 conftest fixture 需要的最小 request：node（runlog 用）+ function（写闸读元数据）。"""
    fn = None
    if op is not None:
        def fn():  # noqa: E301 - 载体函数
            pass
        fn.__fast_case__ = {"op": op, "intent": "保存订单"}

    class R:
        pass
    r = R()
    r.function = fn
    r.node = node
    return r


def _fake_node(name="test_case"):
    class Node:
        path = Path("test_order_api.py")
        originalname = "test_01_query"
        name = "test_01_query"
        _fast_excinfos: list = []
    return Node()


def test_default_env_and_case_shape_from_env_knobs(monkeypatch, tmp_path, request):
    """F7：env 取 ATLAS_FAST_ENV（默认 local）、case=<module>::<func>、行落 conftest 钉死路径。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.delenv("ATLAS_FAST_ENV", raising=False)
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    runs = conftest.RUNS_PATH          # <out>/reports/runs.jsonl（C.1 钉死）
    assert runs.name == "runs.jsonl" and runs.parent.name == "reports"

    runlog = _fixture_fn(conftest._fast_runlog)
    node = _fake_node()
    gen = runlog(_fake_request(node=node))
    writer = next(gen)
    assert writer.env == "local"                       # 默认值（负向：未设 env 也不裸崩）
    assert writer.case == "test_order_api::test_01_query"
    with writer.step("get list"):
        pass
    with pytest.raises(StopIteration):
        next(gen)
    line = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
    assert line["status"] == "passed" and line["env"] == "local"
    assert "reason" not in line
    # 正向对照：env 置位后取显式值（旋钮不是死字面量）
    monkeypatch.setenv("ATLAS_FAST_ENV", "sit")
    writer2 = next(runlog(_fake_request(node=_fake_node())))
    assert writer2.env == "sit"

    # 写闸（E.1.8 离线半段）：op=write 且未置位 → setup 拦、点名旋钮
    monkeypatch.delenv("ATLAS_FAST_ALLOW_WRITES", raising=False)
    gate = _fixture_fn(conftest._write_gate)
    with pytest.raises(support.EnvBlocked) as ei:
        next(gate(_fake_request(op="write"), writer))
    assert "ATLAS_FAST_ALLOW_WRITES" in str(ei.value)
    assert "非缺陷" in str(ei.value)                   # F8：策略阻挡归 env_blocked
    # 正向对照①：置位后放行（body 可达）；②：非写 op 恒放行——闸只拦 write 不拦读腿
    monkeypatch.setenv("ATLAS_FAST_ALLOW_WRITES", "1")
    try:
        next(gate(_fake_request(op="write"), writer))
        blocked_after_allow = False
    except support.EnvBlocked:
        blocked_after_allow = True
    assert blocked_after_allow is False
    try:
        next(gate(_fake_request(op="ui"), writer))
        blocked_ui = False
    except support.EnvBlocked:
        blocked_ui = True
    assert blocked_ui is False


def test_write_gate_blocks_body_reaches_runlog(tmp_path, monkeypatch, request):
    """F8 联动：写闸 raise 在 setup → makereport 收集 → _fast_runlog 落 env_blocked+reason。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.delenv("ATLAS_FAST_ALLOW_WRITES", raising=False)
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)

    node = _fake_node()
    req = _fake_request(op="write", node=node)
    runlog = _fixture_fn(conftest._fast_runlog)
    gate = _fixture_fn(conftest._write_gate)
    gen = runlog(req)
    writer = next(gen)                                # run 行先就位（fixture 依赖序）
    with pytest.raises(support.EnvBlocked) as ei:      # 写闸拦下 body（setup 阶段异常）
        next(gate(req, writer))
    # 模拟 pytest 的 makereport 收集（真实由 conftest hookwrapper 完成，注入同形态条目）
    node._fast_excinfos.append(("setup", ei.value))
    with pytest.raises(StopIteration):
        next(gen)
    line = json.loads(conftest.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert line["status"] == "env_blocked"
    assert "ATLAS_FAST_ALLOW_WRITES" in line["reason"]      # reason 点名旋钮（E.1.8）
    assert "非缺陷" in line["reason"]


# ---------------------------------------------------------------------------
# B-3.5 safe_persist：plan_ctx 标量闸（值永不回显）+ F-15 名册锁步
# ---------------------------------------------------------------------------


def test_safe_persist_scalar_gate_matrix_and_silence(tmp_path, monkeypatch, request):
    """:70 矩阵：str/int/float 放行；bool/容器/None/空文本/长文本 拒 + 一行 WARN。
    WARN **点名键、绝不回显值**（坏形态常是正文/凭据——回显=二次扩散）。
    正向对照：合法标量不产生任何 WARN 输出。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    # G1 显式钉（防「巧合性防线」：y*500 那类样本只在常量恰好=500 时才挡得住改错方向）：
    # 两个阈值各管一面、刻意不等——SCALAR_MAX_LEN=plan_ctx 结论载荷上限（**机读回读**，
    # 腰斩的半截单号=#124 同族坏数据继续跑，故整键拒收不截断）；
    # REASON_MAX_LEN=run 级 reason 展示截断（**人读**点名文案，剩半句仍指路，200 是 Atlas 口径）。
    # 数值不同是设计（F-27 §3 provenance），下一轮「顺手统一」= 语义塌缩，本断言即报警位。
    assert support.SCALAR_MAX_LEN == 500
    assert support.REASON_MAX_LEN == 200
    assert support.SCALAR_MAX_LEN != support.REASON_MAX_LEN
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert support.safe_persist("orderNo", "SO-1") is True
        assert support.safe_persist("qty", 12) is True
        assert support.safe_persist("rate", 1.5) is True
        assert support.safe_persist("_tc1_ok", True) is False        # bool 拒（#117 不猜）
        assert support.safe_persist("resp", {"body": "s3cr3t-token"}) is False   # 容器拒
        assert support.safe_persist("rows", [1, 2]) is False
        assert support.safe_persist("nada", None) is False
        assert support.safe_persist("blank", "   ") is False
        assert support.safe_persist("page_html", "x" * 501) is False  # 长文本拒
        assert support.safe_persist("html500", "y" * 500) is True     # 边界内放行（正向）
    out = buf.getvalue()
    assert "s3cr3t" not in out and "SO-1" not in out      # 值永不回显（拒的值与放行的值都不）
    assert out.count("WARN") == 6, out                    # 六个拒收各一行
    assert "resp" in out and "page_html" in out and "_tc1_ok" in out
    # 消费点单点：save_plan_ctx 是唯一闸位，conftest/渲染层不再各自滤形
    assert SUPPORT_SRC.count("def safe_persist") == 1
    assert "safe_persist(key, ctx[key])" in SUPPORT_SRC   # save_plan_ctx 逐键过闸
    assert "safe_persist" not in CONFTEST_SRC             # conftest 不复制第二道

    # save_plan_ctx 联动：一条坏值不得顶掉整批前置结论（坏键丢、好键照常落）
    p = tmp_path / "ctx.json"
    mixed = {"good": "ok", "bad": {"leak": "v"}, "flag": False, "n": 42}
    support.save_plan_ctx(p, mixed, list(mixed))
    assert json.loads(p.read_text(encoding="utf-8")) == {"good": "ok", "n": 42}


def test_business_sentinel_scan_zero_hits():
    """B-4.6（:99 语义）：客户域名/端口/工号/账号/业务头 **零进**两份产物模板（#11）。
    正向对照：attach 行为测用自造哨兵值经 env 注入且可通（见 test_attach_*），
    证明「值从 env 来」这条路真实存在——不是靠删功能躲扫描。
    （本测自己的名单是扫描目标声明，不构成「使用」；判据面=模板产物，不反扫自身。）"""
    sentinels = ("icssit", "9305", "OSIT00067349", "zhangfj73", "isc-app")
    for label, src in (("support", SUPPORT_SRC), ("conftest", CONFTEST_SRC)):
        for token in sentinels:
            assert token not in src, f"{label} 含业务字面量 {token!r}"
    # 正向对照：运行时注入的自造值走 env，模板零默认
    self_src = (Path(__file__).resolve().parent / "test_emit_fast.py").read_text(
        encoding="utf-8")
    assert "sentinel.invalid" in self_src and "sentinel.invalid" not in CONFTEST_SRC


def test_prod_prefix_lockstep_with_config_py(tmp_path, monkeypatch, request):
    """B-4.9：产物 attach 的 prod 前缀判据与 atlas config 等值——从 **config.py 源文本**
    提取真实前缀比对（不比手抄常量，F-3 锁步同族）。正向对照：非前缀环境判生产 False。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    config_src = (Path(__file__).resolve().parents[1] / "src" / "atlas" /
                  "config.py").read_text(encoding="utf-8", errors="replace")
    prefixes = set(re.findall(r'startswith\("(prod-[^"]*)"\)', config_src))
    assert prefixes == {"prod-"}, f"config.py 的生产前缀判据变了/提不到: {prefixes}"
    prefix = prefixes.pop()
    base = {"ATLAS_FAST_CDP": "ep", "ATLAS_FAST_INTERACTIVE": "1"}
    assert support.session_channel(dict(base, ATLAS_FAST_ENV=prefix + "eu")) == "launch"
    assert support.session_channel(dict(base, ATLAS_FAST_ENV=prefix + "amer")) == "launch"
    assert support._is_production({"ATLAS_FAST_ENV": prefix}) is True
    # 正向对照：非前缀不误杀（pre-/production 之外的任意环境仍能 attach）
    assert support.session_channel(dict(base, ATLAS_FAST_ENV="sit")) == "attach"
    assert support._is_production({"ATLAS_FAST_ENV": "pre-eu"}) is False


# ---------------------------------------------------------------------------
# F3 语义等待行为（假 page，真执行冻结派生实现）
# ---------------------------------------------------------------------------


def test_stripped_allow_writes_still_blocks_never_passes(tmp_path, monkeypatch, request):
    """F-13 行为面（配合 dispatch.child_env 的 pop 裁决）：ATLAS_FAST_ALLOW_WRITES 被
    **剥离**（env 根本不在）时，写腿必须仍被拦停落 env_blocked+点名文案——body 一次都不
    执行、绝不因剥离而变 passed。防的坏形态：把「剥离危险 env」做成「剥离后当普通 run
    跑」，pop 本身就成了第二条悄悄执行写操作的入口（红线 #13/#2）。
    正向对照同测：显式置 "1" 时 body 才可达、run 行 passed。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    runlog = _fixture_fn(conftest._fast_runlog)
    gate = _fixture_fn(conftest._write_gate)

    def _simulate_write_case(allow_env):
        """按 pytest 生命周期跑一轮写腿：runlog setup → gate → (body) → runlog teardown。"""
        if allow_env is None:
            monkeypatch.delenv("ATLAS_FAST_ALLOW_WRITES", raising=False)  # 被 pop 剥离的形态
        else:
            monkeypatch.setenv("ATLAS_FAST_ALLOW_WRITES", allow_env)
        node = _fake_node()
        body_ran = []

        def _case():
            body_ran.append(True)

        _case.__fast_case__ = {"op": "write", "intent": "保存订单"}
        req = _fake_request(node=node)
        req.function = _case
        gen = runlog(req)
        writer = next(gen)
        try:
            next(gate(req, writer))             # setup 闸：未置位在此 raise
        except support.EnvBlocked as exc:
            # 真实 pytest 由 makereport hookwrapper 收集 setup 异常，这里同形态注入
            node._fast_excinfos.append(("setup", exc))
        else:
            with writer.step("save order"):     # 闸放行才进 body（step 记证据）
                _case()
        with pytest.raises(StopIteration):
            next(gen)
        line = json.loads(conftest.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
        return body_ran, line

    # 剥离（缺省 0 语义）：body 零执行 + 行仍 env_blocked——不是 passed、不是 failed
    ran, line = _simulate_write_case(None)
    assert ran == []
    assert line["status"] == "env_blocked"
    assert line["steps"] == []                          # 写操作连 step 都没进=零执行痕迹
    assert "ATLAS_FAST_ALLOW_WRITES" in line["reason"]
    # 显式 "0" 与「不存在」同语义（缺省 0 不是放行）
    ran0, line0 = _simulate_write_case("0")
    assert ran0 == [] and line0["status"] == "env_blocked"
    # 正向对照：人显式放行 → body 恰好执行一次，run 行 passed、不写 reason
    ran1, line1 = _simulate_write_case("1")
    assert ran1 == [True] and line1["status"] == "passed"
    assert line1["steps"][0]["step"] == "save order"
    assert "reason" not in line1
    # 判据位置单点：运行期读旋钮只此一处（_write_gate），产物底座里没有第二把写闸
    assert CONFTEST_SRC.count('os.environ.get("ATLAS_FAST_ALLOW_WRITES"') == 1
    assert SUPPORT_SRC.count('os.environ.get("ATLAS_FAST_ALLOW_WRITES"') == 0


def test_wait_for_rows_semantics(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    # 正向：一次到位
    assert support.wait_for_rows(FakePage(evaluate_seq=[5]), min_rows=3, timeout_ms=500) == 5
    # 瞬态穿越 reload：第一拍执行上下文销毁，第二拍出数（#133 放行腿）
    transient = RuntimeError("Execution context was destroyed, most likely because of a navigation")
    page = FakePage(evaluate_seq=[transient, 2])
    assert support.wait_for_rows(page, min_rows=2, timeout_ms=1000) == 2
    assert page.ticks and all(t == support.TICK_MS for t in page.ticks)
    # 合法空表 min_rows=0：如实返回 0，不恒超时（真实 Chrome 冒烟抓过的坑）
    assert support.wait_for_rows(FakePage(evaluate_seq=[0]), min_rows=0, timeout_ms=300) == 0
    # 不可恢复错误立即传播，不烧窗口谎报（#133 拦截腿）
    dead = FakePage(evaluate_seq=[RuntimeError("Target page, context or browser has been closed")])
    with pytest.raises(RuntimeError, match="Target"):
        support.wait_for_rows(dead, min_rows=1, timeout_ms=60000)
    assert dead.ticks == []          # 立即传播=一睡未睡
    # 窗口耗尽是带归因的 TimeoutError
    with pytest.raises(TimeoutError, match="未达"):
        support.wait_for_rows(FakePage(evaluate_seq=[0] * 40), min_rows=3, timeout_ms=200)


def test_wait_for_vue_field_and_url_change(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support.wait_for_vue_field(FakePage(evaluate_seq=[None, "PT001"]),
                                      "saleOrderForm.paymentTerms", timeout_ms=500) == "PT001"
    with pytest.raises(TimeoutError, match="无值"):
        support.wait_for_vue_field(FakePage(evaluate_seq=[None] * 50), "a.b", timeout_ms=200)
    fatal = FakePage(evaluate_seq=[SyntaxError("bad js <<")])
    with pytest.raises(SyntaxError):
        support.wait_for_vue_field(fatal, "a.b", timeout_ms=60000)
    # URL 变化
    page = FakePage(url_seq=["https://x/list", "https://x/list", "https://x/detail"])
    assert support.wait_for_url_change(page, timeout_ms=500) == "https://x/detail"
    with pytest.raises(TimeoutError, match="未变化"):
        support.wait_for_url_change(FakePage(url_seq=["u"] * 50), timeout_ms=200)


# ---------------------------------------------------------------------------
# plan_ctx / fast_case 行为
# ---------------------------------------------------------------------------


def test_plan_ctx_roundtrip_and_resilience(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    p = tmp_path / "reports" / "plan_ctx.json"
    # 缺失 → 空起点
    assert support.load_plan_ctx(p) == {}
    # 正常回环 + 白名单过滤（B-3.5：bool 被标量闸拒收，合法标量照常往返）
    p.parent.mkdir(parents=True, exist_ok=True)
    ctx = {"_tc1_ok": True, "orderNo": "X-1", "__internal": 1, "docNo": "D-9"}
    support.save_plan_ctx(p, ctx, ["_tc1_ok", "orderNo", "docNo"])
    loaded = support.load_plan_ctx(p)
    assert loaded == {"orderNo": "X-1", "docNo": "D-9"}   # bool 拒 + 未列键不进
    assert "_tc1_ok" not in loaded
    assert not list(p.parent.glob("*.tmp"))            # 原子换：无 .tmp 残留
    # 坏 JSON / 顶层非映射 / 坏字节 → 点名 + 空起点（不裸崩也不静默当数据）
    p.write_text("{ not json", encoding="utf-8")
    assert support.load_plan_ctx(p) == {}
    p.write_bytes(b"[1, 2]")
    assert support.load_plan_ctx(p) == {}
    p.write_bytes(b'{"a": \xff\xfe}')
    assert support.load_plan_ctx(p) == {}
    # require_ctx：缺失显式 EnvBlocked 点名，存在返回值
    with pytest.raises(support.EnvBlocked, match="orderNo"):
        support.require_ctx({}, "orderNo")
    assert support.require_ctx({"orderNo": "X"}, "orderNo") == "X"


def test_fast_case_marks_metadata(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)

    @support.fast_case(op="write", intent="保存订单")
    def _case():
        return "ran"

    assert _case.__fast_case__ == {"op": "write", "intent": "保存订单"}
    assert _case() == "ran"                            # 装饰器不吞 body
    with pytest.raises(ValueError):
        support.fast_case(op="  ")                     # 裸标记不猜（写闸判据入口）


# ---------------------------------------------------------------------------
# write_fast_artifacts（确定性/冲突/force）
# ---------------------------------------------------------------------------


def test_write_fast_artifacts_round_and_determinism(tmp_path):
    a = tmp_path / "a"
    written = write_fast_artifacts(a)
    assert [Path(p).name for p in written] == ["_atlas_fast.py", "conftest.py"]
    assert (a / "_atlas_fast.py").read_text(encoding="utf-8") == SUPPORT_SRC
    # 同内容重写=幂等（无 force 也放行——契约说「内容不同且无 force」才拒）
    write_fast_artifacts(a)
    # 确定性：两个目录字节级一致（无时间戳/随机）
    b = tmp_path / "b"
    write_fast_artifacts(b)
    for name in ("_atlas_fast.py", "conftest.py"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_write_fast_artifacts_conflict_names_file_then_force(tmp_path):
    out = tmp_path / "pkg"
    write_fast_artifacts(out)
    victim = out / "conftest.py"
    victim.write_text("# 人手改过一行的产物\n", encoding="utf-8")
    with pytest.raises(FastEmitError) as ei:
        write_fast_artifacts(out)
    assert "conftest.py" in str(ei.value)              # 点名冲突文件
    assert (out / "conftest.py").read_text(encoding="utf-8").startswith("#")  # 没被偷改
    write_fast_artifacts(out, force=True)              # 人审确认后放行
    assert (out / "conftest.py").read_text(encoding="utf-8") == CONFTEST_SRC
    # 正向对照：底座文件内容未被这次冲突波及（先判后写，无半状态）
    assert (out / "_atlas_fast.py").read_text(encoding="utf-8") == SUPPORT_SRC


def test_write_fast_artifacts_survives_bad_bytes_existing(tmp_path):
    """存量坏字节：replace 读入判「不同」→点名冲突，绝不 UnicodeDecodeError 裸崩（#112）。"""
    out = tmp_path / "pkg"
    out.mkdir()
    (out / "_atlas_fast.py").write_bytes(b'"\xff\xfe broken"')
    with pytest.raises(FastEmitError) as ei:
        write_fast_artifacts(out)
    assert "_atlas_fast.py" in str(ei.value)


# ---------------------------------------------------------------------------
# plan_ctx 跨批落盘链（lead 补漏 2：fixture teardown 必须真调 save_plan_ctx）
# ---------------------------------------------------------------------------


def test_plan_ctx_fixture_teardown_persists_whitelist(tmp_path, monkeypatch, request):
    """F5 断链防线：渲染层只写内存 ctx，**持久化在 plan_ctx fixture 收尾**——这条腿断了
    失败形态是静默的（本批正常、换一批找不到前置），所以必须端到端驱动生成器验证。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    plan_ctx = _fixture_fn(conftest.plan_ctx)

    # 批 1：测试写键（bool 被 B-3.5 标量闸拒收；内部 __ 键过滤）→ teardown 落盘
    gen = plan_ctx()
    ctx = next(gen)
    assert ctx == {}                              # 文件缺失 → 空起点
    ctx["_tc1_ok"] = True                         # B-3.5：bool 拒落（渲染层须写 "1"）
    ctx["orderNo"] = "SO-FAST-1"
    ctx["__ephemeral"] = {"别落盘": True}
    with pytest.raises(StopIteration):
        next(gen)                                 # teardown 触发 save_plan_ctx（含标量闸）
    persisted = json.loads(conftest.PLAN_CTX_PATH.read_text(encoding="utf-8"))
    assert persisted == {"orderNo": "SO-FAST-1"}
    assert "__ephemeral" not in persisted         # 内部键不外泄
    assert "_tc1_ok" not in persisted             # bool 被 safe_persist 拒收（WARN 点名不回显）

    # 批 2（新 fixture 实例=换进程语义）：前置键跨批可见，require_ctx 可用
    gen2 = plan_ctx()
    ctx2 = next(gen2)
    assert support.require_ctx(ctx2, "orderNo") == "SO-FAST-1"
    with pytest.raises(support.EnvBlocked, match="priceList"):
        support.require_ctx(ctx2, "priceList")    # 缺失=显式点名，绝不静默跳
    with pytest.raises(StopIteration):
        next(gen2)

    # 坏 JSON 起点容错：不崩、不顶用例，按空 ctx 起步（下轮合法写入照常）
    conftest.PLAN_CTX_PATH.write_text("{ 坏掉的 json", encoding="utf-8")
    gen3 = plan_ctx()
    ctx3 = next(gen3)
    assert ctx3 == {}
    ctx3["_tc2_ok"] = "yes"                       # 标量形态（bool 会被 B-3.5 拒）
    with pytest.raises(StopIteration):
        next(gen3)
    assert json.loads(conftest.PLAN_CTX_PATH.read_text(encoding="utf-8")) == {"_tc2_ok": "yes"}

    # 无键且无文件 → 不造空文件（零涟漪：纯 API 单跑批次不留垃圾）
    conftest.PLAN_CTX_PATH.unlink()
    gen4 = plan_ctx()
    next(gen4)
    with pytest.raises(StopIteration):
        next(gen4)
    assert not conftest.PLAN_CTX_PATH.exists()


def test_plan_ctx_save_is_in_teardown_after_yield():
    """结构防线：save_plan_ctx 必须在 plan_ctx fixture 的 **yield 之后**（teardown 段）。"""
    seg = CONFTEST_SRC.split("def plan_ctx():")[1].split("@pytest.fixture")[0]
    assert "yield ctx" in seg and "save_plan_ctx(PLAN_CTX_PATH, ctx, keys)" in seg
    assert seg.index("yield ctx") < seg.index("save_plan_ctx(")
    # 白名单过滤在 conftest 侧生效（不是把整个 ctx 原样倒盘）
    assert 'startswith("__")' in seg


# ---------------------------------------------------------------------------
# gap9b B-3 会话通道（判据 :80/:79/:89；测规格 :97；fake playwright 注入，零真驱动）
# ---------------------------------------------------------------------------


class _FPWFixture:
    """假 playwright.sync_api：只记调用 + 预置「人的」浏览器页，绝不碰真驱动/网络。"""

    def __init__(self, monkeypatch):
        self.log: list = []
        self.attach_browser = None  # 预置：connect_over_cdp 返回它（模拟人的已开浏览器）
        pkg = type("playwright", (), {})()
        mod = type("sync_api", (), {})()
        outer = self

        class _Page:
            def __init__(self, url, ctx):
                self.url = url
                self.context = ctx
                self.closed = False

            def close(self):
                self.closed = True
                outer.log.append(("page.close", self.url))

        class _Ctx:
            def __init__(self, browser):
                self.pages = []
                self.browser = browser
                self.closed = False

            def new_page(self):
                p = _Page("about:blank", self)
                self.pages.append(p)
                outer.log.append("new_page")
                return p

            def close(self):
                self.closed = True
                outer.log.append("ctx.close")

        class _Browser:
            def __init__(self):
                self.contexts = []
                self.closed = False

            def new_context(self, **kwargs):
                c = _Ctx(self)
                self.contexts.append(c)
                outer.log.append("new_context")
                return c

            def close(self):
                self.closed = True
                outer.log.append("browser.close")

        class _Chromium:
            def launch(self, **kwargs):
                outer.log.append("launch")
                return _Browser()

            def connect_over_cdp(self, endpoint):
                outer.log.append(("connect_over_cdp", endpoint))
                return outer.attach_browser if outer.attach_browser is not None else _Browser()

        class _PW:
            def __init__(self):
                self.chromium = _Chromium()

            def stop(self):
                outer.log.append("pw.stop")

        class _Manager:
            def start(self):
                return _PW()

        self.Browser = _Browser
        self.Ctx = _Ctx
        self.Page = _Page
        mod.sync_playwright = lambda: _Manager()
        pkg.sync_api = mod
        monkeypatch.setitem(sys.modules, "playwright", pkg)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", mod)

    def seed_attach_browser(self, urls):
        """造一个「人的」浏览器：1 context + 指定 URL 的页，供 MATCH 点名测试预置。"""
        browser = self.Browser()
        ctx = self.Ctx(browser)
        browser.contexts.append(ctx)
        for u in urls:
            ctx.pages.append(self.Page(u, ctx))
        self.attach_browser = browser
        return browser


class _FakeReq:
    class config:  # noqa: N801 - sole_page 只用到 getoption
        @staticmethod
        def getoption(name):
            return False


def test_session_channel_truth_table_eight_way(tmp_path, monkeypatch, request):
    """B-3 :80 八向真值表（:97）：CDP×INTERACTIVE×prod 逐格对条文——全组合只有 (1,1,非prod)
    一格 attach；正向对照同测：attach_denied_by_prod 只在 CDP∧prod 两格为 True。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    expect = {  # (CDP, INTERACTIVE, prod) -> channel
        (0, 0, 0): "launch", (0, 0, 1): "launch", (0, 1, 0): "launch", (0, 1, 1): "launch",
        (1, 0, 0): "launch", (1, 0, 1): "launch", (1, 1, 0): "attach", (1, 1, 1): "launch",
    }
    denied_expect = {(1, 0, 1), (1, 1, 1)}   # CDP 在场 ∧ prod（B-3.4，与在场闸无关）
    for (cdp, intr, prod), want in expect.items():
        env = {"ATLAS_FAST_CDP": "ep" if cdp else "",
               "ATLAS_FAST_INTERACTIVE": "1" if intr else "0",
               "ATLAS_FAST_ENV": "prod-eu" if prod else "sit"}
        got = support.session_channel(env)
        assert got == want, f"CDP={cdp} INT={intr} prod={prod} → {got}，契约要 {want}"
        denied = support.attach_denied_by_prod(env)
        assert denied == ((cdp, intr, prod) in denied_expect), (cdp, intr, prod)
    # 正向对照：纯函数不吃进程环境（os.environ 有 CDP 也不改变判据）
    monkeypatch.setenv("ATLAS_FAST_CDP", "poison")
    assert support.session_channel({}) == "launch"
    # 大小写与空白归一（"1 " 算在场；"2"/"true" 不算——只认 "1"，不猜）
    assert support.session_channel({"ATLAS_FAST_CDP": "ep",
                                    "ATLAS_FAST_INTERACTIVE": " 1 ",
                                    "ATLAS_FAST_ENV": "sit"}) == "attach"
    assert support.session_channel({"ATLAS_FAST_CDP": "ep",
                                    "ATLAS_FAST_INTERACTIVE": "true",
                                    "ATLAS_FAST_ENV": "sit"}) == "launch"


def test_attach_requires_present_declaration_launch_otherwise(tmp_path, monkeypatch, request):
    """B-3 :89 语义闸（在场声明）行为面：CDP 残留 + 无 INTERACTIVE → 调 launch、
    **不调 connect_over_cdp**（无人值守去点人正在用的标签页=没有失败信号的事故形态）；
    正向对照：补上 INTERACTIVE=1 + MATCH 命中 → attach 生效且不 launch。"""
    _, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    fake = _FPWFixture(monkeypatch)
    fake.seed_attach_browser(["https://sentinel.invalid/target-app/doc"])
    sole = _fixture_fn(conftest.sole_page)

    # 格 1：CDP 在场、在场闸未声明 → launch（connect 一次都不调）
    monkeypatch.setenv("ATLAS_FAST_CDP", "http://127.0.0.1:9222")
    monkeypatch.delenv("ATLAS_FAST_INTERACTIVE", raising=False)
    gen = sole(_FakeReq())
    next(gen)
    assert "launch" in fake.log
    assert not [x for x in fake.log if isinstance(x, tuple) and str(x[0]).startswith("connect")]
    with pytest.raises(StopIteration):
        next(gen)

    # 格 2 正向对照：三条件齐 → attach，且不 launch
    fake.log.clear()
    monkeypatch.setenv("ATLAS_FAST_INTERACTIVE", "1")
    monkeypatch.setenv("ATLAS_FAST_ATTACH_MATCH", "target-app")
    gen = sole(_FakeReq())
    page = next(gen)
    assert "launch" not in fake.log
    assert ("connect_over_cdp", "http://127.0.0.1:9222") in fake.log
    assert "target-app" in page.url          # 点到的是点名命中的页，不是别人的 about:blank
    # attach 收尾=只断连（B-3.3）：人的 context/page/browser 一个都不 close
    with pytest.raises(StopIteration):
        next(gen)
    assert "browser.close" not in fake.log and "ctx.close" not in fake.log
    assert not [x for x in fake.log if "page.close" in str(x)]
    assert "pw.stop" in fake.log


def test_attach_prod_falls_back_to_launch_with_warn(tmp_path, monkeypatch, request):
    """B-3.4：CDP 在场 + prod- 前缀 → 落 **launch** + WARN ATTACH-DISALLOWED-IN-PROD
    （ASCII 前缀 X4），不给强制开关——不抛错也不 attach（契约 :80 第三条件；F-19 更正后
    引契约行号不引转述）。正向对照：CDP 在场 + 非 prod → attach 无 WARN。
    launch 生命周期自理：prod 落 launch 后收尾关自己开的浏览器。"""
    _, conftest = _load_artifact(tmp_path, monkeypatch, request)
    import contextlib
    import io
    fake = _FPWFixture(monkeypatch)
    fake.seed_attach_browser(["https://sentinel.invalid/target-app/doc"])
    sole = _fixture_fn(conftest.sole_page)
    monkeypatch.setenv("ATLAS_FAST_CDP", "http://127.0.0.1:9222")
    monkeypatch.setenv("ATLAS_FAST_INTERACTIVE", "1")
    monkeypatch.setenv("ATLAS_FAST_ATTACH_MATCH", "target-app")

    # prod：落 launch + WARN（capsys 外自管捕获，防跨用例串扰）
    buf = io.StringIO()
    monkeypatch.setenv("ATLAS_FAST_ENV", "prod-eu")
    with contextlib.redirect_stdout(buf):
        gen = sole(_FakeReq())
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)
    out = buf.getvalue()
    assert "WARN: ATTACH-DISALLOWED-IN-PROD" in out
    # F-27 §1 三要素自钉（按要素核，不钉整句——将来措辞可调而义务不漂移）：
    # ①已设 CDP ②prod- 前缀 ③降级去向=自起浏览器+env 凭据登录。「意图被降级要说清降成了什么」
    warn_line = [ln for ln in out.splitlines() if "ATTACH-DISALLOWED-IN-PROD" in ln]
    assert len(warn_line) == 1
    line = warn_line[0]
    assert "CDP" in line, "①缺失：没点名 ATLAS_FAST_CDP 在场"
    assert "prod-" in line, "②缺失：没说清 prod- 前缀是拒 attach 的原因"
    assert ("自起" in line or "launch" in line) and ("凭据" in line or "登录" in line), \
        "③缺失：只说降级不说降到哪（自起浏览器+env 凭据登录）=WARN 噪音"
    # 模板文本级同钉（不跑行为也能挡「顺手删半句」——xcheck 尺的产物层同款，各层各钉一次）
    assert "自起浏览器并用 env 凭据登录" in CONFTEST_SRC
    assert "launch" in fake.log
    assert not [x for x in fake.log if isinstance(x, tuple) and str(x[0]).startswith("connect")]
    assert "browser.close" in fake.log            # launch 自己开的，收尾自理（对比 attach）
    # 正向对照：同配置换 sit → attach 生效且不再喊
    fake.log.clear()
    buf2 = io.StringIO()
    monkeypatch.setenv("ATLAS_FAST_ENV", "sit")
    with contextlib.redirect_stdout(buf2):
        gen = sole(_FakeReq())
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)
    assert ("connect_over_cdp", "http://127.0.0.1:9222") in fake.log
    assert "ATTACH-DISALLOWED-IN-PROD" not in buf2.getvalue()
    assert "browser.close" not in fake.log        # attach 收尾只断连（B-3.3）


def test_attach_match_named_page_selection_zero_fallback(tmp_path, monkeypatch, request):
    """B-3.1/:79：MATCH 未设 / 0 命中 / 多命中 / contexts=0 全部 EnvBlocked 点名，
    **零位置兜底**；唯一命中 → 取该页（身份核对，非位置）。正向对照：唯一命中路径可用。"""
    _, conftest = _load_artifact(tmp_path, monkeypatch, request)
    fake = _FPWFixture(monkeypatch)
    sole = _fixture_fn(conftest.sole_page)

    def _env(cdp=True, intr=True, match=None):
        monkeypatch.setenv("ATLAS_FAST_CDP", "http://127.0.0.1:9222")
        if intr:
            monkeypatch.setenv("ATLAS_FAST_INTERACTIVE", "1")
        else:
            monkeypatch.delenv("ATLAS_FAST_INTERACTIVE", raising=False)
        if match is None:
            monkeypatch.delenv("ATLAS_FAST_ATTACH_MATCH", raising=False)
        else:
            monkeypatch.setenv("ATLAS_FAST_ATTACH_MATCH", match)
        monkeypatch.delenv("ATLAS_FAST_ENV", raising=False)

    # ① MATCH 未设 → EnvBlocked 点名键名（即便 pages 非空，也不许拿 pages[0]）
    _env(match=None)
    fake.seed_attach_browser(["https://sentinel.invalid/unrelated"])
    with pytest.raises(conftest.EnvBlocked) as ei:
        sole(_FakeReq()).send(None)
    assert "ATLAS_FAST_ATTACH_MATCH" in str(ei.value)

    # ② 0 命中 → EnvBlocked 点名「现有页数 + 匹配值」
    _env(match="target-app")
    fake.seed_attach_browser(["https://other.invalid/a", "https://other.invalid/b"])
    with pytest.raises(conftest.EnvBlocked) as ei:
        sole(_FakeReq()).send(None)
    msg = str(ei.value)
    assert "target-app" in msg and "2" in msg           # 页数与匹配值都在
    assert "pages[0]" in msg                            # 消息明说：不回落

    # ③ 多命中 → EnvBlocked（不猜标签页；按位置取首个=pages[0] 换皮，禁）
    fake.seed_attach_browser(["https://x/target-app/1", "https://y/target-app/2"])
    with pytest.raises(conftest.EnvBlocked) as ei:
        sole(_FakeReq()).send(None)
    assert "2" in str(ei.value) and "不猜" in str(ei.value)

    # ④ contexts=0 → EnvBlocked 点名，不伪造 new_context
    _env(match="target-app")
    fake.attach_browser = fake.Browser()               # 空 contexts
    with pytest.raises(conftest.EnvBlocked) as ei:
        sole(_FakeReq()).send(None)
    assert "contexts=0" in str(ei.value)

    # ⑤ 唯一命中 → 身份正确（正向对照：返回的是那个页对象本身）
    target_holder = {}

    def _seed_unique():
        b = fake.Browser()
        c = fake.Ctx(b)
        b.contexts.append(c)
        p1 = fake.Page("https://other.invalid/x", c)
        p2 = fake.Page("https://app.internal/target-app/doc", c)
        c.pages += [p1, p2]
        target_holder["p"] = p2
        target_holder["p1"] = p1
        fake.attach_browser = b

    _seed_unique()
    gen = sole(_FakeReq())
    page = next(gen)
    assert page is target_holder["p"]
    assert page.url == "https://app.internal/target-app/doc"
    with pytest.raises(StopIteration):
        next(gen)
    assert target_holder["p1"].closed is False         # 别人的页没被碰


def test_no_positional_fallback_in_conftest():
    """AST 级禁令（:79）：conftest 模板代码里 `pages[0]`/`contexts[0]` 位置访问 = 0 处
    （消息文案里的「不回落 pages[0]」字样不算——只抓下标节点）。正向对照：detector
    对注入样本必须命中，否则这条禁令是空转。"""

    def _positional(src):
        hits = []
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr in ("pages", "contexts")
                    and isinstance(node.slice, ast.Constant) and node.slice.value == 0):
                hits.append(node.value.attr)
        return hits

    assert _positional(CONFTEST_SRC) == []
    assert _positional(SUPPORT_SRC) == []
    assert _positional("def f(b):\n    return b.contexts[0]\n") == ["contexts"]


def test_channel_judgment_single_site_indicator_authority_shared(tmp_path, monkeypatch, request):
    """判据位单点（:89 语义闸「不复制」）：三条件组合只在 _atlas_fast.session_channel；
    conftest 不重定义、不再读 INTERACTIVE、不写 prod- 字面判据。指示器实查仍是唯一
    会话判据（attach≠会话有效；attach 永不发登录动作）。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    # 底座侧：三条件键的字面量读取集中在 session_channel 家族（唯一实现位）
    for key in ("ATLAS_FAST_CDP", "ATLAS_FAST_INTERACTIVE", "ATLAS_FAST_ENV"):
        assert SUPPORT_SRC.count(f'env.get("{key}"') >= 1
    assert 'startswith("prod-")' in SUPPORT_SRC
    # conftest 侧零复制（判据面：读调用/判据式不复制；docstring 提及旋钮名是说明文，不算）
    assert "def session_channel" not in CONFTEST_SRC
    assert 'os.environ.get("ATLAS_FAST_INTERACTIVE"' not in CONFTEST_SRC
    assert 'startswith("prod-")' not in CONFTEST_SRC
    assert CONFTEST_SRC.count("session_channel(") >= 2        # sole_page + get_page 都走单点
    # 会话判据唯一：复用与登录后复核同一条实查（一判据两调用点），磁盘标记不放行
    assert CONFTEST_SRC.count("page.locator(indicator).count() > 0") == 1
    assert CONFTEST_SRC.count("page.locator(indicator).count() == 0") == 1
    assert 'if plan_ctx.get("_logged_in_account")' not in CONFTEST_SRC
    # 端点/MATCH 值只从 env 读（无默认、无业务字面量；连兜底端口都不写进 conftest）
    assert "ATLAS_FAST_ATTACH_MATCH" in CONFTEST_SRC
    assert "9222" not in CONFTEST_SRC

    class _Loc:
        def __init__(self, n):
            self._n = n

        def count(self):
            return self._n

    class _AttachPage:
        def __init__(self, n):
            self._n = n

        def locator(self, sel):
            return _Loc(self._n)

    # 行为面（B-3.2）：attach 三条件齐 + 实查未登录 → EnvBlocked「人工登录」，
    # 绝不走 NotWired/login 路径（在人的浏览器里发登录动作=越权，#13）
    monkeypatch.setenv("ATLAS_FAST_LOGIN_INDICATOR", "logged-in-marker")
    monkeypatch.setenv("ATLAS_FAST_ACCOUNT", "a")
    monkeypatch.setenv("ATLAS_FAST_PASSWORD", "b")
    monkeypatch.setenv("ATLAS_FAST_BASE_URL", "http://irrelevant.invalid")
    monkeypatch.setenv("ATLAS_FAST_CDP", "http://127.0.0.1:9222")
    monkeypatch.setenv("ATLAS_FAST_INTERACTIVE", "1")
    monkeypatch.delenv("ATLAS_FAST_ENV", raising=False)
    get_page = _fixture_fn(conftest.get_page)
    with pytest.raises(support.EnvBlocked) as ei:
        get_page(_AttachPage(0), {})()
    assert "人工登录" in str(ei.value) and "attach" in str(ei.value)
    # 正向对照①：attach + 实查在场 → 直接复用（不发登录、不 launch）
    reused = get_page(_AttachPage(1), {})()
    assert reused._n == 1
    # 正向对照②：同页未登录但走 launch（无 CDP）→ 仍是 F-7 NotWired 未接线语义
    monkeypatch.delenv("ATLAS_FAST_CDP")
    with pytest.raises(support.NotWired):
        get_page(_AttachPage(0), {})()


# ---------------------------------------------------------------------------
# F-1 离线半段：runs.jsonl → emit_allure 状态映射往返（零网络零浏览器）
# ---------------------------------------------------------------------------


def test_allure_roundtrip_no_greenwashing(tmp_path, monkeypatch, request):
    """E.1.5（F-1 离线形态）：假 step 记录驱动 RunWriter → emit_allure 逐条核对映射。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    runs = tmp_path / "reports" / "runs.jsonl"
    page = FakePage()
    support.set_current_page(page)
    try:
        # 1) passed
        w = support.RunWriter("test_api::test_ok", "local", path=runs)
        with w.step("get list"):
            pass
        with w.step("assert 存活"):
            pass
        w.close()
        # 2) failed + assertion
        w = support.RunWriter("test_api::test_mismatch", "local", path=runs)
        with pytest.raises(AssertionError):
            with w.step("assert resultCode"):
                raise AssertionError("expected BUG-000")
        w.close("failed")
        # 3) env_blocked（NotWired 经继承）+ reason 原文
        nw = support.NotWired("登录未接线：OrderList.query 按钮")
        status, reason = conftest._runlog_status_reason([("setup", nw)])
        w = support.RunWriter("test_ui::test_query", "local", path=runs)
        with pytest.raises(support.EnvBlocked):
            with w.step("query"):
                raise nw
        w.close(status, reason=reason)
        # 4) watchdog timeout
        w = support.RunWriter("test_ui::test_hang", "local", path=runs)
        w.close(support.FLOW_STATUS_TIMEOUT,
                reason=conftest._watchdog_timeout_reason(600.0))
    finally:
        support.set_current_page(None)

    out = tmp_path / "allure-results"
    stats = emit_allure(runs, out, attachments_dir=tmp_path / "reports")
    assert stats["results"] == 4
    results = {}
    for f in out.glob("atlas-*-result.json"):
        r = json.loads(f.read_text(encoding="utf-8"))
        results[r["name"]] = r
    by_case = {k.split("::")[-1]: v for k, v in results.items()}
    assert by_case["test_ok"]["status"] == "passed"
    assert by_case["test_mismatch"]["status"] == "failed"
    labels = {l["name"]: l["value"] for l in by_case["test_mismatch"]["labels"]}
    assert labels["atlas.fail_class"] == "assertion"
    # env_blocked → skipped + blocked label + **reason 原文保留**（不是泛化 "environment blocked"）
    blocked = by_case["test_query"]
    assert blocked["status"] == "skipped"
    blk_labels = {l["name"]: l["value"] for l in blocked["labels"]}
    assert blk_labels["atlas.fail_class"] == "env_blocked"
    assert blk_labels["atlas.blocked"] == "true"
    assert "OrderList.query" in blocked["statusDetails"]["message"]
    # 看门狗 timeout → broken（fail-loud，不洗 skipped/interrupted）
    assert by_case["test_hang"]["status"] == "broken"
    # 洗绿总闸：非 passed 行不许有 allure passed
    assert [n for n, r in results.items()
            if r["status"] == "passed"] == ["test_api::test_ok"]
    # 截图附件真实进了结果集：两条失败步骤各一张（test_mismatch 的 assert 步 + query 步），
    # 成功腿零截图（红线 #8——总数恰为 2 即同时钉住「失败有、成功无」两面）
    step_att = [s.get("attachments") for r in results.values() for s in r["steps"]]
    atts = [a for sa in step_att for a in sa]
    assert len(atts) == 2 and all(a["type"] == "image/png" for a in atts)


# ===========================================================================
# 重构波 T3 并行分片（ATLAS_FAST_PARALLEL + --parallel N）
# ===========================================================================


def test_parallel_workers_parsing(tmp_path, monkeypatch, request):
    """T3 开关解析：缺省/空白/1=单进程；>1=worker 数；非整数/<=0 点名（不静默当单进程）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support.parallel_workers({}) == 1
    assert support.parallel_workers({"ATLAS_FAST_PARALLEL": ""}) == 1
    assert support.parallel_workers({"ATLAS_FAST_PARALLEL": "1"}) == 1
    assert support.parallel_workers({"ATLAS_FAST_PARALLEL": " 4 "}) == 4
    with pytest.raises(support.EnvBlocked, match="ATLAS_FAST_PARALLEL"):
        support.parallel_workers({"ATLAS_FAST_PARALLEL": "abc"})
    with pytest.raises(support.EnvBlocked, match="正整数"):
        support.parallel_workers({"ATLAS_FAST_PARALLEL": "0"})
    with pytest.raises(support.EnvBlocked, match="正整数"):
        support.parallel_workers({"ATLAS_FAST_PARALLEL": "-3"})


def test_parallel_setup_problem_missing_xdist_named(tmp_path, monkeypatch, request):
    """T3 缺依赖点名：单进程不触发；>1 缺 xdist 给 pip install 指引（不静默降级）。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support.parallel_setup_problem(1, xdist_available=False) == ""
    assert support.parallel_setup_problem(0, xdist_available=False) == ""
    assert support.parallel_setup_problem(4, xdist_available=True) == ""
    problem = support.parallel_setup_problem(4, xdist_available=False)
    assert "pytest-xdist" in problem and "pip install pytest-xdist" in problem
    assert "不静默降级" in problem


def test_conftest_parallel_gate_raises_usage_error(tmp_path, monkeypatch, request):
    """T3 行为面：声明 >1 且缺 xdist → pytest_configure 抛 UsageError（fail-loud 点名）。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.setitem(sys.modules, "xdist", None)   # __import__("xdist") → ImportError
    monkeypatch.setenv("ATLAS_FAST_PARALLEL", "4")

    class _PM:
        def getplugin(self, name):
            return None

        def unregister(self, plugin):
            pass

    class _Cfg:
        pluginmanager = _PM()

    with pytest.raises(pytest.UsageError, match="pytest-xdist"):
        conftest.pytest_configure(_Cfg())
    # 正向对照：单进程（未设旋钮）不抛
    monkeypatch.delenv("ATLAS_FAST_PARALLEL", raising=False)
    conftest.pytest_configure(_Cfg())
    # 坏值也点名（非整数）
    monkeypatch.setenv("ATLAS_FAST_PARALLEL", "abc")
    with pytest.raises(pytest.UsageError, match="ATLAS_FAST_PARALLEL"):
        conftest.pytest_configure(_Cfg())


_PARALLEL_FLOW = {"id": "par", "steps": [
    {"id": "s1", "task": "api_call", "method": "GET", "path": "/api/x",
     "save_as": "r1", "read_only": True}], "assert": ["r1.ok == true"]}


def test_emit_pytest_package_parallel_readme_and_gate(tmp_path):
    """T3 生成期：parallel>1 写 README 并行段（用法/依赖/代价边界）；闸拒绝先于写盘。"""
    from atlas.generator.emit_pytest import PytestEmitError, emit_pytest_package
    out = tmp_path / "def"
    emit_pytest_package(out_dir=out, flow=_PARALLEL_FLOW, fast=True)
    assert "并行分片" not in (out / "README.md").read_text(encoding="utf-8")
    out2 = tmp_path / "par"
    emit_pytest_package(out_dir=out2, flow=_PARALLEL_FLOW, fast=True, parallel=4)
    readme = (out2 / "README.md").read_text(encoding="utf-8")
    assert "并行分片" in readme and "ATLAS_FAST_PARALLEL=4" in readme
    assert "pytest-xdist" in readme and "pytest -n 4" in readme
    assert "plan_ctx" in readme          # 边界写清（并行下跨批前置不可靠）
    with pytest.raises(PytestEmitError):
        emit_pytest_package(out_dir=tmp_path / "slow", flow=_PARALLEL_FLOW, parallel=2)
    with pytest.raises(PytestEmitError):
        emit_pytest_package(out_dir=tmp_path / "neg", flow=_PARALLEL_FLOW, fast=True, parallel=-1)
    assert not (tmp_path / "slow").exists() and not (tmp_path / "neg").exists()


def test_cli_export_pytest_parallel(tmp_path):
    """T3 CLI：--parallel 透传；>1 非快模式 rc2 且不建目录。"""
    import yaml
    from atlas.cli import main
    p = tmp_path / "flow.yaml"
    p.write_text(yaml.safe_dump(_PARALLEL_FLOW), encoding="utf-8")
    out = tmp_path / "pkg"
    assert main(["export-pytest", str(p), "-o", str(out), "--fast", "--parallel", "4"]) == 0
    assert "ATLAS_FAST_PARALLEL=4" in (out / "README.md").read_text(encoding="utf-8")
    out2 = tmp_path / "pkg2"
    assert main(["export-pytest", str(p), "-o", str(out2), "--parallel", "4"]) == 2
    assert not out2.exists()


# ===========================================================================
# 重构波 T4 失败腿 trace 归档（ATLAS_FAST_TRACE，成功腿零开销）
# ===========================================================================


class _TracingPage:
    """假 page：context.tracing 记 start/stop；stop(path=) 落字节（零浏览器零 socket）。"""

    def __init__(self):
        self.calls = []
        self.context = types.SimpleNamespace(tracing=self)

    def start(self, **kw):
        self.calls.append(("start", kw))

    def stop(self, path=None):
        self.calls.append(("stop", path))
        if path:
            Path(path).write_bytes(b"zip-bytes")


def test_trace_enabled_parsing_and_path(tmp_path, monkeypatch, request):
    """红线 #2 双确认：仅 TRACE=1 **不足以**开；需 TRACE=1 ∧ ACCEPT_SECRETS=1。坏值按关。"""
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support.trace_enabled({}) is False
    assert support.trace_enabled({"ATLAS_FAST_TRACE": "1"}) is False                 # 缺第二键→不开
    assert support.trace_enabled({"ATLAS_FAST_TRACE": "1",
                                  "ATLAS_FAST_TRACE_ACCEPT_SECRETS": "1"}) is True
    # 坏值一律按关（精确 "1"，不 strip/不折叠）——凭据面从宽=事故
    for bad in ("true", "yess", "1 ", " 1", "TRUE"):
        assert support.trace_enabled({"ATLAS_FAST_TRACE": bad,
                                      "ATLAS_FAST_TRACE_ACCEPT_SECRETS": "1"}) is False, bad
        assert support.trace_enabled({"ATLAS_FAST_TRACE": "1",
                                      "ATLAS_FAST_TRACE_ACCEPT_SECRETS": bad}) is False, bad
    # trace_ack_required / WARN：TRACE=1 缺确认 → 需要告警，文案带 ASCII 前缀与第二键名
    assert support.trace_ack_required({"ATLAS_FAST_TRACE": "1"}) is True
    assert support.trace_ack_required({"ATLAS_FAST_TRACE": "1",
                                       "ATLAS_FAST_TRACE_ACCEPT_SECRETS": "1"}) is False
    assert support.trace_ack_required({}) is False
    warn = support.trace_ack_warning({"ATLAS_FAST_TRACE": "1"})
    assert warn.startswith("WARN: TRACE-ACK-REQUIRED")
    assert "ATLAS_FAST_TRACE_ACCEPT_SECRETS" in warn and "密码" in warn
    assert support.trace_ack_warning({}) == ""
    tp = support.trace_path("/x/reports/runs.jsonl", "abc123")
    assert tp.parent.name == support.TRACES_DIR_NAME and tp.name == "abc123.zip"


def test_trace_zero_overhead_when_disabled(tmp_path, monkeypatch, request):
    """默认关（两键都空）=成功腿零开销：连 start 都不调；运行行不写 trace 键。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.delenv("ATLAS_FAST_TRACE", raising=False)
    monkeypatch.delenv("ATLAS_FAST_TRACE_ACCEPT_SECRETS", raising=False)
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    page = _TracingPage()
    support.set_current_page(page)
    try:
        runlog = _fixture_fn(conftest._fast_runlog)
        gen = runlog(_fake_request(node=_fake_node()))
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        support.set_current_page(None)
    assert page.calls == []                            # 零 tracing 调用
    line = json.loads(conftest.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert "trace" not in line and "trace_secrets_ack" not in line


def test_trace_single_key_not_enough(tmp_path, monkeypatch, request):
    """红线 #2：只给 TRACE=1 → 仍**无** trace.zip、零 start_tracing；pytest_configure 喊 WARN。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.setenv("ATLAS_FAST_TRACE", "1")
    monkeypatch.delenv("ATLAS_FAST_TRACE_ACCEPT_SECRETS", raising=False)
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)

    def _Cfg():
        class _PM:
            def getplugin(self, n):
                return None

            def unregister(self, p):
                pass

        class _C:
            pluginmanager = _PM()
        return _C()

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        conftest.pytest_configure(_Cfg())
    assert "WARN: TRACE-ACK-REQUIRED" in buf.getvalue()      # 会话级点名 WARN
    assert "ATLAS_FAST_TRACE_ACCEPT_SECRETS" in buf.getvalue()

    page = _TracingPage()
    support.set_current_page(page)
    try:
        node = _fake_node()
        node._fast_excinfos.append(("call", AssertionError("boom")))  # 失败腿也不给 zip
        runlog = _fixture_fn(conftest._fast_runlog)
        gen = runlog(_fake_request(node=node))
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        support.set_current_page(None)
    assert page.calls == []                                  # 第二键缺失 → 零 tracing
    line = json.loads(conftest.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert "trace" not in line and "trace_secrets_ack" not in line


def test_trace_failure_leg_writes_zip_path(tmp_path, monkeypatch, request):
    """双确认齐 + 失败腿：start→stop(path) 落 trace.zip，路径+可审计标记写进 runs.jsonl。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.setenv("ATLAS_FAST_TRACE", "1")
    monkeypatch.setenv("ATLAS_FAST_TRACE_ACCEPT_SECRETS", "1")
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    page = _TracingPage()
    support.set_current_page(page)
    try:
        node = _fake_node()
        node._fast_excinfos.append(("call", AssertionError("boom")))
        runlog = _fixture_fn(conftest._fast_runlog)
        gen = runlog(_fake_request(node=node))
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        support.set_current_page(None)
    line = json.loads(conftest.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert line["status"] == "failed"
    assert line["trace"].endswith(".zip")
    trace = Path(line["trace"])
    assert trace.is_absolute() and trace.exists()
    assert trace.parent.name == support.TRACES_DIR_NAME
    assert line["trace_secrets_ack"] is True                 # 红线 #2 可审计标记
    assert [c[0] for c in page.calls] == ["start", "stop"]
    assert page.calls[1][1] is not None


def test_trace_success_leg_no_file(tmp_path, monkeypatch, request):
    """双确认齐 + 成功腿：start+stop 但 stop 不带 path（丢弃），无 zip、无 trace 键。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.setenv("ATLAS_FAST_TRACE", "1")
    monkeypatch.setenv("ATLAS_FAST_TRACE_ACCEPT_SECRETS", "1")
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    page = _TracingPage()
    support.set_current_page(page)
    try:
        runlog = _fixture_fn(conftest._fast_runlog)
        gen = runlog(_fake_request(node=_fake_node()))
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        support.set_current_page(None)
    assert [c[0] for c in page.calls] == ["start", "stop"]
    assert page.calls[1][1] is None
    line = json.loads(conftest.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert line["status"] == "passed" and "trace" not in line
    traces_dir = conftest.RUNS_PATH.parent / support.TRACES_DIR_NAME
    assert not traces_dir.exists() or not list(traces_dir.glob("*.zip"))


def test_trace_without_page_is_noop(tmp_path, monkeypatch, request):
    """双确认齐但无页面（纯 API 腿）：start_tracing(None)=False，不崩、不写 trace 键。"""
    support, conftest = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.setenv("ATLAS_FAST_TRACE", "1")
    monkeypatch.setenv("ATLAS_FAST_TRACE_ACCEPT_SECRETS", "1")
    monkeypatch.delenv("ATLAS_RUNS_DIR", raising=False)
    support.set_current_page(None)
    runlog = _fixture_fn(conftest._fast_runlog)
    gen = runlog(_fake_request(node=_fake_node()))
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)
    line = json.loads(conftest.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert "trace" not in line


def test_trace_credential_risk_is_documented():
    """凭据红线**如实标注** + 双确认硬化（不声称安全）：模板含风险注释/规避/只写路径/审计标记。"""
    assert "凭据红线" in SUPPORT_SRC
    assert "trace.zip" in SUPPORT_SRC
    assert "fill" in SUPPORT_SRC and "明文可能落进 trace.zip" in SUPPORT_SRC
    assert "ATLAS_FAST_TRACE_ACCEPT_SECRETS" in SUPPORT_SRC   # 第二确认键
    assert "TRACE-ACK-REQUIRED" in SUPPORT_SRC               # 点名 WARN 前缀（ASCII/X4）
    # 运行行只写**路径** + 双确认审计标记（不回写内容）
    assert "self.trace = str(path)" in SUPPORT_SRC
    assert 'record["trace"] = self.trace' in SUPPORT_SRC
    assert 'record["trace_secrets_ack"] = True' in SUPPORT_SRC


# ===========================================================================
# 重构波 T5 浏览器矩阵（ATLAS_BROWSER_CHANNEL + ATLAS_BROWSER_EXECUTABLE）
# ===========================================================================


def test_browser_channel_matrix_and_unknown_named(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    assert support.browser_channel({}) == "chromium"                 # 默认 chromium
    assert support.browser_channel({"ATLAS_BROWSER_CHANNEL": "chromium"}) == "chromium"
    assert support.browser_channel({"ATLAS_BROWSER_CHANNEL": " Chrome "}) == "chrome"  # 归一化
    assert support.browser_channel({"ATLAS_BROWSER_CHANNEL": "firefox"}) == "firefox"
    assert support.browser_channel({"ATLAS_BROWSER_CHANNEL": "webkit"}) == "webkit"
    with pytest.raises(support.EnvBlocked, match="ATLAS_BROWSER_CHANNEL"):
        support.browser_channel({"ATLAS_BROWSER_CHANNEL": "safari"})   # 未知点名，不静默回退
    # 家族映射：chrome ⊂ chromium 家族（channel=chrome），firefox/webkit 各自
    assert support.browser_family("chromium") == "chromium"
    assert support.browser_family("chrome") == "chromium"
    assert support.browser_family("firefox") == "firefox"
    assert support.browser_family("webkit") == "webkit"
    assert support.DEFAULT_BROWSER_CHANNEL == "chromium"


def test_missing_browser_hint_names_playwright_install(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    hint = support.missing_browser_hint(
        RuntimeError("Executable doesn't exist at /x/firefox ... python -m playwright install"),
        "firefox")
    assert "playwright install firefox" in hint and "ATLAS_BROWSER_EXECUTABLE" in hint
    # 正向对照：非缺浏览器错误（端口占用/超时）不套「缺浏览器」的皮
    assert support.missing_browser_hint(RuntimeError("Address already in use"), "chrome") == ""
    assert support.missing_browser_hint(TimeoutError("boom"), "chromium") == ""
    assert "webkit" in support.missing_browser_hint(RuntimeError("playwright install"), "webkit")


def test_launch_kwargs_channel_matrix(tmp_path, monkeypatch, request):
    support, _ = _load_artifact(tmp_path, monkeypatch, request)
    monkeypatch.delenv("ATLAS_BROWSER_EXECUTABLE", raising=False)
    assert support.launch_kwargs(True, {})["channel"] is None                 # chromium 自带
    assert support.launch_kwargs(True, {"ATLAS_BROWSER_CHANNEL": "chrome"})["channel"] == "chrome"
    assert support.launch_kwargs(True, {"ATLAS_BROWSER_CHANNEL": "firefox"})["channel"] is None
    kw = support.launch_kwargs(True, {"ATLAS_BROWSER_CHANNEL": "chrome",
                                      "ATLAS_BROWSER_EXECUTABLE": "/usr/bin/chrome"})
    assert kw["executable_path"] == "/usr/bin/chrome" and kw["channel"] is None


def test_conftest_browser_launch_wiring():
    """结构面：按家族选 launch 对象；缺浏览器 hint 已接线；chromium launch 位唯一。"""
    for frag in ("browser_channel(env_snapshot)", "browser_family(browser_ch",
                 "pw.firefox.launch(", "pw.webkit.launch("):
        assert frag in CONFTEST_SRC, frag
    assert CONFTEST_SRC.count("pw.chromium.launch(") == 1     # 单浏览器权威（xcheck 同尺）
    assert "missing_browser_hint(exc, browser_ch)" in CONFTEST_SRC
    assert (CONFTEST_SRC.index("browser_channel(env_snapshot)")
            < CONFTEST_SRC.index("sync_playwright().start()"))   # 判据先于驱动 start

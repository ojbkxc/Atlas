"""入口输入预检的**序列单点**守卫。

## 这条防什么

`check_demo_api` / `require_account` / `check_legacy_required` 早就各自是单点函数了
（这个仓库擅长的那件事做得很好）。但**调用序列**被抄了 5 份：`run`、`agent --execute`、
多 flow、`explore`、`explore --auto`（外加 `DemoRuntime.__enter__`）。收敛判据函数不等于
收敛序列——第 5 份漏的时候，没有任何东西会红。

实测（2026-11-16，工作树 a65d4a1 之后的 HEAD）：

    $ atlas run f.yaml      → rc 2，stderr 一行「ValueError: 缺少 sit 对应的 DEMO_URL_SIT 配置」
    $ atlas explore         → rc 1，stderr **16 行裸 traceback**（runtime.py 内联判据穿透 main）

同一条配置，两个入口两种口径。`_explore` 只调了 `require_account`，漏了 base_url 与
DemoApi 两条——而 `_explore_auto` 同样漏。这就是「compile 绿 ≠ run 绿」的同族第 3 例：
**预检侧与执行侧各写一份装配逻辑，改了一处没改另一处。**

本文件的判据分两层：

1. **结构面**（`test_entry_points_all_use_the_single_preflight`）：AST 扫描 `cli.py`，
   所有会构造 `DemoRuntime` 的入口必须在构造前调过 `precheck_session_inputs`。
   这条防「第 6 个入口又抄漏」。
2. **行为面**（`test_missing_base_url_is_rc2_on_every_entry`）：对每个入口真的跑一遍
   `main()`，缺 base_url 时必须 rc 2 + 一行 stderr + 无 traceback。
   这条防「结构上叫了但没真生效」。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "src" / "atlas" / "cli.py"


# ---------------------------------------------------------------------------
# 1. 结构面：每个构造 DemoRuntime 的入口都必须先过单点预检
# ---------------------------------------------------------------------------

def _entry_functions_building_runtime() -> dict[str, ast.FunctionDef]:
    """`cli.py` 里所有**会开启真实会话**的函数。

    两种形态都算（第一版只认第一种，实测漏掉了 `_explore`——它不直接写
    `DemoRuntime(...)`，而是委托 `explore.start()`，后者内部才构造。这正是「守卫自己
    留一个洞」的形态，所以判据要按**能力**认而不是按**语法**认）：

    1. 直接 `DemoRuntime(...)`；
    2. 调用 `start(...)`（`atlas.explore.start`——登录 + 开浏览器 + 冻会话）。
    """
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    found: dict[str, ast.FunctionDef] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (
                    node.func.id == "DemoRuntime" or node.func.id == "start"):
                found[fn.name] = fn
                break
    return found


# 会开启真实会话的入口函数清单（**新增入口时这里必须同步**）。
# 这不是重复登记——它的作用是让「加了个入口但忘了接预检」变成一条会红的断言，
# 而不是靠人记得。清单本身若与 AST 结果不等，说明有人加了入口却没走预检。
#
# 2026-11-16 结构治理后更新：`main()` 里内联的 run / agent --execute 两个分支被抽成
# `_run` / `_agent`（`main()` 473 → 102 行，分派收敛到 `_DISPATCH` 表），所以会话入口
# 从「main 一个」变成「_run + _agent 两个」。清单跟着源码走，不改判据强度。
_EXPECTED_SESSION_ENTRIES = {
    "_run",             # 单 flow 执行轨：with DemoRuntime(...)
    "_agent",           # agent --execute：with DemoRuntime(config)
    "_run_multi_flows", # 多 flow 一次登录
    "_explore",         # 委托 explore.start
    "_explore_auto",    # 直接 DemoRuntime(...).__enter__()
}


def _calls_preflight(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "precheck_session_inputs"):
            return True
    return False


def test_session_entry_inventory_is_current():
    """入口清单与实际会开启会话的函数**必须相等**（两个方向都查）。

    - 实际有、清单没有 → 有人加了新入口，本守卫不知道它该不该预检（先登记再看）；
    - 清单有、实际没有 → 入口被删/改名，清单条目已失效（下次会误放行）。
    """
    actual = set(_entry_functions_building_runtime())
    assert actual == _EXPECTED_SESSION_ENTRIES, (
        "会开启真实会话的入口清单与实际不符。\n"
        f"  实际多出（新增入口？请接 precheck_session_inputs 后登记）：{sorted(actual - _EXPECTED_SESSION_ENTRIES)}\n"
        f"  清单失效（入口已删/改名？请清理清单）：{sorted(_EXPECTED_SESSION_ENTRIES - actual)}")


def test_entry_points_all_use_the_single_preflight():
    """核心判据：开启真实会话的每个 CLI 入口，必须先调单点预检。

    违规 = 有人加了第 6 个入口（或在新分支里再构造一次 runtime）却没接预检 →
    缺配置时裸 traceback 穿透 main()，用户拿到 rc 1 + 十几行栈。
    """
    entries = _entry_functions_building_runtime()
    assert entries, "扫描没找到任何会话入口——AST 判据本身失效了"
    missing = sorted(name for name, fn in entries.items() if not _calls_preflight(fn))
    assert not missing, (
        "下列入口会开启真实会话但**没有**先调 runtime.precheck_session_inputs："
        f"{missing}\n缺 base_url/账号/坏路径时会裸 traceback 穿透 main()（rc 1 + 十行栈），"
        "而 run 轨同配置是 rc 2 + 一行——正是本次收敛要消灭的「抄漏一条」。")


def test_preflight_has_no_inline_copies_left():
    """反向判据：`cli.py` 里不许再有内联的预检序列残留。

    具体形态 = 直接调用 `check_demo_api` / `check_legacy_required`。这两个函数仍然是
    单点判据（`runtime.py` 内保留），但 **CLI 侧不得直呼**——一调就说明又有人手抄了
    一段序列，而序列的唯一来源是 `precheck_session_inputs`。
    （`load_legacy_dotenv` 不在禁用名单：`doctor` 用它读 base_url 来源，那是诊断不是预检。）
    """
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    banned = {"check_demo_api", "check_legacy_required"}
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in banned):
                offenders.append(f"{fn.name}():{node.lineno} {node.func.id}()")
    assert not offenders, (
        "cli.py 里还有内联预检调用（序列唯一来源是 precheck_session_inputs）：\n  "
        + "\n  ".join(sorted(set(offenders))))


# ---------------------------------------------------------------------------
# 2. 行为面：每个入口缺 base_url 时都给 rc 2 + 一行 + 无 traceback
# ---------------------------------------------------------------------------

def _bare_project(tmp_path: Path) -> Path:
    """一个「什么都没配」的 Atlas 项目：有 .atlas-root，没有 URL/账号/旧仓。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".atlas-root").write_text("", encoding="utf-8")
    (root / "flow.yaml").write_text(
        "id: probe\nsteps:\n  - task: api_call\n    method: GET\n    path: /x\n",
        encoding="utf-8")
    return root


def _clear_url_env(monkeypatch) -> None:
    """撤掉所有环境地址/账号来源——让「缺配置」成为唯一事实。

    只清 URL / 账号类键，**不动** `ATLAS_LEGACY_PROJECT` / `ATLAS_DEMO_API_PROJECT`：
    那两个是「指向不存在的路径」的故意配置，清掉会变成另一种失败形态。
    """
    import os
    for key in list(os.environ):
        if key.endswith("_URL_SIT") or key.endswith("_URL_UAT") or key == "ATLAS_SESSION_ACCOUNT":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ATLAS_ENTRYPOINT", raising=False)


@pytest.mark.parametrize("argv_tail", [
    ["run", "flow.yaml"],
    ["explore"],
    ["explore", "--auto", "--goal", "document_no"],
    ["agent", "--execute", "--goal", "document_no"],
])
def test_missing_base_url_is_rc2_on_every_entry(tmp_path, monkeypatch, capsys, argv_tail):
    """缺 base_url：**每个**入口都是 rc 2 + 一行 stderr + 无 traceback。

    这是实测暴露的那个缺陷的回归钉：`explore` / `explore --auto` 此前给 rc 1 +
    16 行裸 traceback，而 `run` 给 rc 2 + 一行。收敛到单点预检后四者必须一致。

    注：`explore` 需要 LLM 三键才算「输入合法」，但 base_url 检查在 LLM 配置之前——
    缺 URL 时不该走到 LLM 判定（更基础的错先报）。`--goal` 给了，避免 goal 缺失抢先。
    """
    from atlas.cli import main

    root = _bare_project(tmp_path)
    _clear_url_env(monkeypatch)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(tmp_path / "no-such-legacy"))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path / "no-such-api"))
    # 显式声明插件，避免 `resolve_plugin()` 的回落 WARN 混进 stderr 行数断言
    # （那是 en-1 的有意提示，与本测要验的「一行错误」无关，用显式声明把它排除）。
    monkeypatch.setenv("ATLAS_PLUGIN", "demo")
    # LLM 三键设齐，确保 explore --auto 的失败点只能是 base_url 而不是 LLM 未配置
    for key, val in (("ATLAS_LLM_BASE_URL", "http://127.0.0.1:1"),
                     ("ATLAS_LLM_API_KEY", "k"), ("ATLAS_LLM_MODEL", "m")):
        monkeypatch.setenv(key, val)

    argv = ["--root", str(root), *[str(root / a) if a == "flow.yaml" else a
                                   for a in argv_tail]]
    rc = main(argv)
    err = capsys.readouterr().err

    assert rc == 2, f"缺 base_url = 输入错误，应 rc 2；实得 {rc}（stderr={err!r}）"
    assert "Traceback" not in err, f"不得裸 traceback 穿透 main()：{err!r}"
    assert "URL_" in err, f"消息必须点名缺的是哪个环境 URL 键（可操作性）：{err!r}"
    assert len([ln for ln in err.strip().splitlines() if ln.strip()]) == 1, (
        f"必须**一行** stderr（不是十几行栈）：{err!r}")


def test_preflight_order_loads_dotenv_before_reading_base_url(tmp_path, monkeypatch):
    """顺序判据：旧仓 .env 的 URL 必须能被继承（`load_legacy_dotenv` 在最前）。

    这条防「收敛时把顺序改错了」——若 base_url 判定早于 dotenv 加载，只在旧仓 .env
    里写了 URL 的正常路径会被误拒（#115 教训：别把正常路径翻成错误）。
    """
    from atlas.runtime import precheck_session_inputs
    from atlas.config import AtlasConfig

    root = _bare_project(tmp_path)
    legacy = tmp_path / "legacy"
    (legacy / "legacy-web").mkdir(parents=True)
    (legacy / ".env").write_text("DEMO_URL_SIT=https://inherited.example/\n", encoding="utf-8")
    api = tmp_path / "api"
    (api / "scripts").mkdir(parents=True)
    (api / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    _clear_url_env(monkeypatch)

    config = AtlasConfig.from_root(root)
    # 不抛 = 继承成功（若顺序错，这里会因为「缺 DEMO_URL_SIT」抛 ValueError）
    precheck_session_inputs(config, account="someone", no_login=False, legacy_required=True)
    # base_url 会做末尾斜杠归一（`validate_base_url` 的既有行为），只断言主机部分
    assert config.base_url.rstrip("/") == "https://inherited.example", \
        f"旧仓 .env 的 URL 未被继承：{config.base_url!r}"


def test_no_login_skips_account_gate(tmp_path, monkeypatch):
    """`--no-login` 不查账号：没有会话就没有「拿空账号去登录」这件事（#115）。

    这条防「收敛时把 no_login 分支丢了」——那会把合法的 no-login 路径翻成 rc 2。
    """
    from atlas.runtime import precheck_session_inputs
    from atlas.config import AtlasConfig

    root = _bare_project(tmp_path)
    legacy = tmp_path / "legacy"
    (legacy / "legacy-web").mkdir(parents=True)
    api = tmp_path / "api"
    (api / "scripts").mkdir(parents=True)
    (api / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    monkeypatch.setenv("DEMO_URL_SIT", "https://sit.example/")
    monkeypatch.delenv("ATLAS_SESSION_ACCOUNT", raising=False)
    monkeypatch.setenv("ATLAS_PLUGIN", "iservice")   # iservice 的 session account 为空

    config = AtlasConfig.from_root(root)
    # no_login=True：账号为空也应放行（旧仓 + DemoApi 都在，其它判据通过）
    precheck_session_inputs(config, account=None, no_login=True, legacy_required=True)

    # 反向对照：no_login=False 时同一份配置必须拒（否则上面的放行是恒真）
    with pytest.raises(ValueError, match="没有可用账号"):
        precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)

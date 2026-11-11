"""DSL 声明位（gap9b B-2）校验与调度派发面加固测（owner: cli-impl）。

全部离线：零网络、零浏览器、零真实子进程。覆盖三面：
1. `page_object`/`legacy_pages` 形态判据（compile 静态段单点，A.5 挂载点=同源收益测）；
2. `atlas export-pytest` 入口三态（B-1：rc 0/2、3 不使用；结构坏不产出半成品）；
3. 调度对称三件套之「argv 面 raise + env 面三键 pop + 登记期闸门」
   （行为面「剥离≠放行」测归 support-impl，gap9b C 表钉死，本文件不代写）。
"""
from __future__ import annotations
import inspect
import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from atlas.cli import main, _full_registry
from atlas.dsl import FlowCompiler
from atlas.scheduling import dispatch, state as st
from atlas.scheduling.dispatch import (FAST_CDP_ENV_KEY, FAST_ENV_KEYS_STRIPPED,
                                       build_command, child_env, guard_fast_cdp)

CDP_VALUE = "http://localhost:9222"

GOOD_STEP = {"task": "api_call", "path": "/api/list", "method": "GET", "save_as": "resp_1"}


def _flow(step: dict) -> dict:
    """第 2 步 = 合法 api_call 步 + 被测声明键（步本身注册合法，报错只剩声明位一因）。"""
    return {"id": "decl_probe", "steps": [dict(GOOD_STEP), {**GOOD_STEP, **step}]}


def _compiler() -> FlowCompiler:
    return FlowCompiler(_full_registry())


# ===================== B-2 声明位：形态判据（静态段单点） =====================

def test_page_object_valid_scalar_compiles():
    """良形 page_object 放行；且证明 _RESERVED_KEYS 收录——键若漏进 Task 工厂，
    api_call 构造当场 TypeError「unexpected keyword argument」。"""
    good = _flow({"page_object": "SalesOrder"})
    tasks = _compiler().validate_flow(good)
    assert len(tasks) == 2


def test_page_object_bad_shapes_named_with_index_and_value():
    # 容器/None/bool/空串/纯空白全拒，点名步骤序号与值（#77/#134 同族）
    for bad in ([], {}, None, True, False, "", "   "):
        flow = _flow({"page_object": bad})
        with pytest.raises(ValueError) as ei:
            _compiler().validate_flow(flow)
        msg = str(ei.value)
        assert "page_object" in msg and "步骤 2" in msg, f"{bad!r} 未被点名: {msg}"


def test_legacy_pages_bad_shapes_named():
    for bad in ([], None, False, {}):
        flow = _flow({"legacy_pages": bad})
        with pytest.raises(ValueError) as ei:
            _compiler().validate_flow(flow)
        assert "legacy_pages" in str(ei.value) and "步骤 2" in str(ei.value)


def test_legacy_pages_dotted_rejected_explains_module_constant():
    # B-4.5：消息含「模块位由产物常量」——dsl 不抄常量值（#92），指常量名
    flow = _flow({"legacy_pages": "a.b"})
    with pytest.raises(ValueError) as ei:
        _compiler().validate_flow(flow)
    msg = str(ei.value)
    assert "模块位由产物常量" in msg and "步骤 2" in msg


def test_two_decl_keys_mutually_exclusive_named():
    # B-2 互斥：一步同带两键拒绝点名（#84 多来源同红线）
    flow = _flow({"page_object": "SalesOrder", "legacy_pages": "do_thing"})
    with pytest.raises(ValueError) as ei:
        _compiler().validate_flow(flow)
    assert "步骤 2" in str(ei.value)


def test_bad_decl_rejects_whole_flow_atomically():
    """原子应用纪律：第 2 步坏不产出整体；错误带定位（既有 FlowValidationError 形态）。"""
    flow = _flow({"page_object": {"nested": 1}})
    with pytest.raises(ValueError) as ei:
        _compiler().validate_flow(flow)
    assert "-->" in str(ei.value) or "步骤 2" in str(ei.value)


def test_static_gate_same_source_across_compile_and_cli(tmp_path):
    # B-4.4③ 同源测：同一坏 flow，compile()/validate_flow()/CLI `atlas compile` 三处同红——
    # 证明 A.5 挂载点收益：入口零复制判据
    flow = _flow({"page_object": []})
    compiler = _compiler()
    with pytest.raises(ValueError) as e1:
        compiler.compile(flow)
    with pytest.raises(ValueError) as e2:
        compiler.validate_flow(flow)
    assert "page_object" in str(e1.value)
    assert "page_object" in str(e2.value)
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(flow, allow_unicode=True), encoding="utf-8")
    assert main(["compile", str(p)]) == 2


def test_flows_without_decl_keys_untouched():
    # #115 正向对照：存量 flow（无新键）判据零触碰
    good = {"id": "plain", "steps": [dict(GOOD_STEP)]}
    assert len(_compiler().validate_flow(good)) == 1


# ===================== B-1 入口：atlas export-pytest 三态 =====================

FLOW_YAML = ("id: export_demo\nsteps:\n"
             "  - task: api_call\n    path: /api/list\n    method: GET\n"
             "    save_as: resp_1_list\n")


def _flow_file(tmp_path) -> Path:
    p = tmp_path / "flow.yaml"
    p.write_text(FLOW_YAML, encoding="utf-8")
    return p


def test_export_pytest_emits_and_lists_files(tmp_path, capsys):
    p = _flow_file(tmp_path)
    out = tmp_path / "pkg"
    assert main(["export-pytest", str(p), "-o", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fast"] is False
    assert payload["written"] and all(Path(x).exists() for x in payload["written"])
    names = {f.name for f in out.iterdir()}
    assert any(n.startswith("test_") for n in names) and "_atlas_support.py" in names
    assert "conftest.py" not in names  # 缺省形态 = gap7 逐字（fast 文件不进包）
    assert not (out / "reports").exists()


def test_export_pytest_fast_adds_fast_artifacts(tmp_path):
    p = _flow_file(tmp_path)
    out = tmp_path / "pkgfast"
    assert main(["export-pytest", str(p), "-o", str(out), "--fast"]) == 0
    names = {f.name for f in out.iterdir()}
    assert {"_atlas_fast.py", "conftest.py"} <= names


def test_export_pytest_invalid_flow_rc2_no_dir_created(tmp_path):
    # 结构坏 → rc2 且产物目录**都没建**（校验先于导出，半成品不落盘）
    p = tmp_path / "broken.yaml"
    p.write_text("id: bad\nsteps:\n  - task: unregistered_task\n", encoding="utf-8")
    out = tmp_path / "never"
    assert main(["export-pytest", str(p), "-o", str(out)]) == 2
    assert not out.exists()


def test_export_pytest_missing_file_rc2_not_rc3(tmp_path):
    # rc 口径对照（B-4.1）：错误面只有 2，不存在返回 3 的路径
    assert main(["export-pytest", str(tmp_path / "ghost.yaml")]) == 2


def test_export_pytest_nonempty_dir_requires_force(tmp_path):
    p = _flow_file(tmp_path)
    out = tmp_path / "twice"
    assert main(["export-pytest", str(p), "-o", str(out)]) == 0
    assert main(["export-pytest", str(p), "-o", str(out)]) == 2
    assert main(["export-pytest", str(p), "-o", str(out), "--force"]) == 0


def test_export_pytest_deterministic(tmp_path):
    # 确定性：同 flow 重复导出内容相等（无时间戳/随机）
    p = _flow_file(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["export-pytest", str(p), "-o", str(a)]) == 0
    assert main(["export-pytest", str(p), "-o", str(b)]) == 0
    fa = {f.name: f.read_bytes() for f in a.iterdir()}
    fb = {f.name: f.read_bytes() for f in b.iterdir()}
    assert fa == fb


# ===================== B-3.5 调度两轴：argv raise + env 三键 pop =====================

def test_fast_cdp_key_name_single_source():
    assert FAST_CDP_ENV_KEY == "ATLAS_FAST_CDP"
    assert FAST_ENV_KEYS_STRIPPED == ("ATLAS_FAST_CDP", "ATLAS_FAST_INTERACTIVE",
                                      "ATLAS_FAST_ALLOW_WRITES")


def test_build_command_rejects_fast_cdp_smuggled_in_run_args():
    task = {"flow": "flows/patrol.yaml",
            "run_args": ["--harvest", f"{FAST_CDP_ENV_KEY}={CDP_VALUE}"]}
    with pytest.raises(ValueError) as ei:
        build_command(task, Path("."))
    assert "调度派发禁用 attach 通道" in str(ei.value)
    assert FAST_CDP_ENV_KEY in str(ei.value)


def test_build_command_rejects_cdp_flag_approximation():
    # 契约 B-3.5：`--cdp` 旗标近似形态同拒，判据不猜携带形态
    with pytest.raises(ValueError):
        build_command({"flow": "f.yaml", "run_args": ["--cdp-url", CDP_VALUE]},
                      Path("."))


def test_build_command_rejects_bare_key_reference():
    with pytest.raises(ValueError):
        build_command({"flow": "f.yaml", "run_args": ["--flag", "ATLAS_FAST_CDP"]},
                      Path("."))


def test_build_command_zero_ripple_for_normal_run_args():
    # #115：闸不得把能用路径翻成错误——常规 argv 形态逐字不变
    cmd = build_command({"flow": "flows/x.yaml", "run_args": ["--harvest"]},
                        Path("."))
    assert cmd == [sys.executable, "-m", "atlas", "run", "flows/x.yaml", "--harvest"]
    assert not any("ATLAS_FAST" in str(item) for item in cmd)


def test_child_env_strips_three_keys_from_base():
    base = {k: CDP_VALUE for k in FAST_ENV_KEYS_STRIPPED}
    base.update({"DEMO_USERNAME": "u1", "PYTHONPATH": "/keep/me"})
    env = child_env(Path("."), base=base)
    for key in FAST_ENV_KEYS_STRIPPED:
        assert key not in env, key
    assert env["DEMO_USERNAME"] == "u1"   # 只剔清单键，不误伤其他继承项
    # 正向对照（B-3.5 钉）：既有注入行为不变
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONPATH"].startswith(str(Path(".") / "src")) or "/keep/me" in env["PYTHONPATH"]


def test_child_env_strips_keys_from_process_env(tmp_path, monkeypatch):
    # 案发现场本体：人 shell export 过三键 → os.environ 继承面。cron 最小 env 未必有，
    # 但 schtasks/交互调起会带——不剔除，无人值守子进程就会接上人已登录的浏览器/带写放行。
    for key in FAST_ENV_KEYS_STRIPPED:
        monkeypatch.setenv(key, "1" if key != FAST_CDP_ENV_KEY else CDP_VALUE)
    env = child_env(tmp_path)
    for key in FAST_ENV_KEYS_STRIPPED:
        assert key not in env, key


def test_child_env_never_injects_allow_writes_even_when_absent():
    # 「剥离≠放行」的接线面：child_env 只减不增——它自己绝不写 ALLOW_WRITES=1
    env = child_env(Path("."), base={"PATH": "x"})
    assert "ATLAS_FAST_ALLOW_WRITES" not in env
    assert not any("ALLOW_WRITES" in k for k in env)


# ---- 判据形态：显式 raise 不靠 assert（C1-C 先例）----

def test_guard_uses_explicit_raise_not_assert():
    src = textwrap.dedent(inspect.getsource(guard_fast_cdp))
    assert "raise ValueError" in src
    assert "\n    assert " not in src and not src.lstrip().startswith("assert")


# ---- 跨文件哨兵：键名判据只许存在于 dispatch 一处 ----

def test_fast_env_keys_judgment_not_duplicated_across_scheduling():
    # #92：判据不留第二份。scheduling/ 其他模块不得各自抄键名或各自补剔除；
    # dispatch.py 内 CDP 键名字面恰一处（常量定义点，tuple 引用变量不重复字面）。
    sched_dir = Path(dispatch.__file__).parent
    for path in sorted(sched_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "dispatch.py":
            assert text.count('"ATLAS_FAST_CDP"') == 1, "键名出现第二份字面=判据漂移入口"
        else:
            assert "ATLAS_FAST_CDP" not in text, path.name


# ===================== 登记期闸门（add/edit 当场 rc2，脏台账不落账） =====================

def _sched_root(tmp_path) -> Path:
    base = tmp_path / "atlas"
    base.mkdir()
    (base / ".atlas-root").write_text("", encoding="utf-8")
    flows = base / "flows"
    flows.mkdir()
    (flows / "patrol.yaml").write_text(FLOW_YAML, encoding="utf-8")
    return base


def _cli(root, *argv):
    return main(["--root", str(root), "schedule", *argv])


def test_schedule_add_smuggled_cdp_rc2_and_not_registered(tmp_path):
    root = _sched_root(tmp_path)
    rc = _cli(root, "add", "patrol", "--flow", "flows/patrol.yaml", "--manual",
              "--run-arg", f"ATLAS_FAST_CDP={CDP_VALUE}")
    assert rc == 2
    doc = st.load_doc(st.tasks_path(root))
    assert "patrol" not in doc["tasks"]      # 报错落在人还能改的时刻，台账干净
    assert not st.tasks_path(root).exists() or doc["tasks"] == {}


def test_schedule_add_normal_run_args_still_registered(tmp_path):
    # 正向对照（#115）：不带脏键的登记路径逐字不变
    root = _sched_root(tmp_path)
    assert _cli(root, "add", "patrol", "--flow", "flows/patrol.yaml",
                "--manual", "--run-arg=--harvest") == 0
    doc = st.load_doc(st.tasks_path(root))
    assert doc["tasks"]["patrol"]["run_args"] == ["--harvest"]


def test_schedule_edit_smuggled_cdp_rc2_ledger_untouched(tmp_path):
    # 登记期拒的是 CDP 键（B-3.5 argv 面判据对象）；ALLOW_WRITES 的案发面在 env
    # 继承（child_env pop，上测），argv 面不扩权——闸只拦契约要求的字面（红线 #12）
    root = _sched_root(tmp_path)
    assert _cli(root, "add", "patrol", "--flow", "flows/patrol.yaml", "--manual") == 0
    before = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["run_args"]
    assert _cli(root, "edit", "patrol", "--run-arg",
                f"ATLAS_FAST_CDP={CDP_VALUE}") == 2
    after = st.load_doc(st.tasks_path(root))["tasks"]["patrol"]["run_args"]
    assert after == before                    # 脏配置不覆写台账

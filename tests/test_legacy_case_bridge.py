"""legacy_case 桥接的回归：路径解析、SKIP→EnvBlocked、save_as 自兑现、ctx 不被重复播种。

桥接的价值在「零改写」：它不重写 step_N、不改断言，只负责定位旧用例、把整条用例交给旧
`run_case_py`、把旧 runner 的 SKIP 信号翻译成 Atlas `EnvBlocked`。因此这里用**假旧项目**
（tmp_path 下自建 `{web}/testcases/...` + `lib/runner.py`）钉死桥接契约，不依赖真实 SIT 环境：
- 真项目缺席时（CI/他人机器）本组测试照样绿；
- 同时反向证明桥接**没有**自己实现一份 ctx 播种（播种由 fake runner 做，桥接只透传 ctx 对象）。
"""
import sys
import types

import pytest

from atlas.failures import EnvBlocked
from atlas.plugins.demo.legacy_case import LegacyCaseTask

WEB = "iservice"
MODULE = "suite_x/01_case/case.yaml"


def _make_legacy(tmp_path, runner_source: str) -> "tuple[object, object]":
    """造一个最小旧项目：{tmp}/legacy_proj/{web}/testcases/... + lib/runner.py。"""
    root = tmp_path / "legacy_proj"
    web = root / WEB
    runner_path = web / "lib" / "runner.py"
    runner_path.parent.mkdir(parents=True)
    runner_path.write_text(runner_source, encoding="utf-8")
    case_dir = web / "testcases" / "suite_x" / "01_case"
    case_dir.mkdir(parents=True)
    (case_dir / "case.yaml").write_text("cases: []\n", encoding="utf-8")
    return root, web


def _config(tmp_path, legacy_root):
    import os
    os.environ["ATLAS_LEGACY_PROJECT"] = str(legacy_root)
    os.environ["ATLAS_LEGACY_WEB"] = WEB
    (tmp_path / ".atlas-root").write_text("", encoding="utf-8")
    os.environ["ATLAS_ROOT"] = str(tmp_path)


class FakeActor:
    page = "PAGE"

    def __init__(self):
        self.ctx = {"preset": 1}


def _forget_runner():
    sys.modules.pop("lib.runner", None)
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _isolate_lib_modules():
    _forget_runner()
    yield
    _forget_runner()


def test_seeds_nothing_and_delegates_whole_case(tmp_path):
    """桥接把 page/ctx/两个 skip 开关原样交给旧 run_case_py，自己不播种 ctx。"""
    captured = {}
    src = (
        "def run_case_py(yaml_path, case_id, page, ctx, *, skip_precondition=False,"
        " skip_postcondition=False):\n"
        "    captured = __import__('builtins').captured\n"
        "    captured.update(yaml_path=yaml_path, case_id=case_id, page=page,\n"
        "                    ctx=ctx, skip_precondition=skip_precondition,\n"
        "                    skip_postcondition=skip_postcondition)\n"
        "    ctx['so_no'] = 'SO-1'\n"
    )
    root, _ = _make_legacy(tmp_path, src)
    _config(tmp_path, root)
    import builtins
    builtins.captured = captured
    try:
        actor = FakeActor()
        task = LegacyCaseTask(MODULE, "TC1", skip_postcondition=True)
        result = task.perform(actor)
    finally:
        del builtins.captured

    assert result is None
    assert captured["case_id"] == "TC1"
    assert captured["yaml_path"].replace("\\", "/").endswith(MODULE)
    assert captured["page"] == "PAGE"
    assert captured["ctx"] is actor.ctx            # 同一对象透传，不复制/不重播种
    assert actor.ctx == {"preset": 1, "so_no": "SO-1"}   # 只有旧 runner 写了 so_no
    assert captured["skip_postcondition"] is True
    assert captured["skip_precondition"] is False


def test_skip_signal_becomes_env_blocked(tmp_path):
    """旧 runner 的 pytest.skip（SKIP，不是失败）→ Atlas EnvBlocked，供 on_env_blocked: skip。"""
    src = (
        "import pytest\n"
        "def run_case_py(yaml_path, case_id, page, ctx, *, skip_precondition=False,"
        " skip_postcondition=False):\n"
        "    pytest.skip('客户缺默认税率')\n"
    )
    root, _ = _make_legacy(tmp_path, src)
    _config(tmp_path, root)
    task = LegacyCaseTask(MODULE, "TC2")
    with pytest.raises(EnvBlocked, match="缺默认税率"):
        task.perform(FakeActor())


def test_save_as_self_fulfils_from_document_key(tmp_path):
    """声明 save_as 时按 document_key 从 ctx 取单号返回 {save_as: …}（produces 自兑现）。"""
    src = (
        "def run_case_py(yaml_path, case_id, page, ctx, *, skip_precondition=False,"
        " skip_postcondition=False):\n"
        "    ctx['rm_no_1'] = 'RM-9'\n"
    )
    root, _ = _make_legacy(tmp_path, src)
    _config(tmp_path, root)
    task = LegacyCaseTask(MODULE, "TC10", save_as="rm_no_1", document_key="rm_no_1")
    assert task.metadata.produces == ("rm_no_1",)
    assert task.perform(FakeActor()) == {"rm_no_1": "RM-9"}


def test_missing_save_as_key_is_honest_none(tmp_path):
    """源 yaml 声明了抽取键但旧 .py 从未写它（TC11 rm_no_2 真实形态）：
    桥接如实回 None，不猜值、不伪造单号。"""
    src = (
        "def run_case_py(yaml_path, case_id, page, ctx, *, skip_precondition=False,"
        " skip_postcondition=False):\n"
        "    pass\n"
    )
    root, _ = _make_legacy(tmp_path, src)
    _config(tmp_path, root)
    task = LegacyCaseTask(MODULE, "TC11", save_as="rm_no_2", document_key="rm_no_2")
    assert task.perform(FakeActor()) == {"rm_no_2": None}


def test_created_document_enters_recycle_list(tmp_path):
    """红线 3：桥接不经 provider.quota，真实建出的单号必须自己并入回收清单，
    否则 atlas recycle 永远列不出本次造的单（R3）。"""
    import json
    src = (
        "def run_case_py(yaml_path, case_id, page, ctx, *, skip_precondition=False,"
        " skip_postcondition=False):\n"
        "    ctx['so_no'] = 'SO-RECYCLE-1'\n"
    )
    root, _ = _make_legacy(tmp_path, src)
    _config(tmp_path, root)
    assert LegacyCaseTask(MODULE, "TC1", save_as="so_no").perform(FakeActor()) == {"so_no": "SO-RECYCLE-1"}

    path = tmp_path / "reports" / "recycle_list.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [r["document"]["orderNo"] for r in records] == ["SO-RECYCLE-1"]
    assert records[0]["entity"].endswith("#TC1")


def test_read_only_case_never_writes_recycle_list(tmp_path):
    """write=False 的只读用例即使 ctx 里恰好有单号也不得写回收清单（不制造假记录）。"""
    src = (
        "def run_case_py(yaml_path, case_id, page, ctx, *, skip_precondition=False,"
        " skip_postcondition=False):\n"
        "    ctx['so_no'] = 'SO-READONLY'\n"
    )
    root, _ = _make_legacy(tmp_path, src)
    _config(tmp_path, root)
    LegacyCaseTask(MODULE, "TC1", write=False, save_as="so_no").perform(FakeActor())
    assert not (tmp_path / "reports" / "recycle_list.jsonl").exists()


def test_no_save_as_never_writes_recycle_list(tmp_path):
    """未声明 save_as 时没有可回收单号，不得写清单（TC3/TC6 等被拦截用例的真实形态）。"""
    src = (
        "def run_case_py(yaml_path, case_id, page, ctx, *, skip_precondition=False,"
        " skip_postcondition=False):\n"
        "    ctx['so_no'] = 'SO-IGNORED'\n"
    )
    root, _ = _make_legacy(tmp_path, src)
    _config(tmp_path, root)
    LegacyCaseTask(MODULE, "TC3").perform(FakeActor())
    assert not (tmp_path / "reports" / "recycle_list.jsonl").exists()


def test_metadata_domain_is_iservice_and_write_guard(tmp_path):
    """domain 必须是 iservice（知识过滤 `domain in scope`）；write 决定 read_only。"""
    write_task = LegacyCaseTask(MODULE, "TC1", case_intent="TC1 建单")
    assert write_task.metadata.domain == "iservice"
    assert write_task.metadata.read_only is False
    assert write_task.metadata.intent == "TC1 建单"
    assert write_task.name.endswith("#TC1")
    read_task = LegacyCaseTask(MODULE, "TC1", write=False)
    assert read_task.metadata.read_only is True


def test_module_cannot_escape_testcases_root(tmp_path):
    root, web = _make_legacy(tmp_path, "def run_case_py(*a, **k):\n    pass\n")
    _config(tmp_path, root)
    task = LegacyCaseTask("../outside.yaml", "TC1")
    with pytest.raises(ValueError, match="testcases"):
        task.yaml_path()


def test_missing_yaml_names_the_path(tmp_path):
    root, _ = _make_legacy(tmp_path, "def run_case_py(*a, **k):\n    pass\n")
    _config(tmp_path, root)
    task = LegacyCaseTask("suite_x/99_nope/nope.yaml", "TC1")
    with pytest.raises(FileNotFoundError, match="旧用例 yaml 不存在"):
        task.yaml_path()


def test_constructor_rejects_bad_types():
    for kwargs in ({"module": ""}, {"case_id": ""}, {"write": "yes"},
                   {"skip_postcondition": "true"}, {"save_as": None}):
        with pytest.raises(ValueError):
            LegacyCaseTask(**{"module": MODULE, "case_id": "TC1", **kwargs})


def test_web_root_is_put_on_sys_path(tmp_path):
    """旧用例/旧库都 `from lib.… import …`，不把 web 根放进 sys.path 会 No module named 'lib'。"""
    root, web = _make_legacy(tmp_path, "def run_case_py(*a, **k):\n    pass\n")
    _config(tmp_path, root)
    LegacyCaseTask(MODULE, "TC1").perform(FakeActor())
    assert str(web.resolve()) in sys.path
    assert isinstance(sys.modules.get("lib.runner"), types.ModuleType)

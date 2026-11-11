"""C6 版本三元组（design_c6 §5）：指纹确定性 / source 三态 / flow 两态 / 落盘接线。

核心目的：把「上周能跑今天炸」从玄学变成可 diff 的事实——所以三元组必须**确定**
（同输入同值）、**可辨**（private/builtin/unreadable、source/derived 分得清）、
**缺了能检**（旧 runs.jsonl 不冒充有版本）。
"""
import json

import pytest
import yaml

from atlas import versioning
from atlas.dsl import FlowCompiler, FlowDocument, TaskRegistry
from atlas.recorder import RunRecorder


@pytest.fixture
def kb_dir(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "pitfalls.json").write_bytes(b'{"pitfalls": []}')
    sub = root / "contracts"
    sub.mkdir()
    (sub / "core.json").write_bytes(b"[]")
    return root


def _line_of(path):
    return json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])


# ── §5.1 指纹确定性 ────────────────────────────────────────────────────

def test_fingerprint_is_deterministic(kb_dir):
    first = versioning.knowledge_fingerprint(kb_dir)
    assert first == versioning.knowledge_fingerprint(kb_dir)
    assert first["source"] == "private" and first["files"] == 2
    assert len(first["sha1"]) == 12


def test_one_byte_change_moves_the_fingerprint(kb_dir):
    before = versioning.knowledge_fingerprint(kb_dir)["sha1"]
    (kb_dir / "pitfalls.json").write_bytes(b'{"pitfalls": [{}]}')
    assert versioning.knowledge_fingerprint(kb_dir)["sha1"] != before


def test_adding_or_removing_file_moves_the_fingerprint(kb_dir):
    before = versioning.knowledge_fingerprint(kb_dir)
    (kb_dir / "extra.json").write_bytes(b"{}")
    after = versioning.knowledge_fingerprint(kb_dir)
    assert after["sha1"] != before["sha1"]
    assert after["files"] == before["files"] + 1
    (kb_dir / "extra.json").unlink()
    assert versioning.knowledge_fingerprint(kb_dir)["sha1"] == before["sha1"]


def test_directory_order_does_not_change_fingerprint(tmp_path):
    """排序生效：同一批文件以不同写入顺序落盘，指纹必须逐字相等（否则跨机对比失效）。"""
    a, b = tmp_path / "a", tmp_path / "b"
    for root, files in ((a, ("z.json", "a.json")), (b, ("a.json", "z.json"))):
        root.mkdir()
        for name in files:
            (root / name).write_bytes(b"{}")
    assert versioning.knowledge_fingerprint(a) == versioning.knowledge_fingerprint(b)


def test_fingerprint_keys_are_posix_and_sorted(tmp_path):
    """X1 专项：指纹键必须是 POSIX 相对路径且排序——反斜杠混进键，同一份知识库在
    Windows/Linux 上就算出两个 sha1，「跨机对比」这个前提当场失效。"""
    root = tmp_path / "kb"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "sub" / "deep" / "x.json").write_bytes(b"{}")
    (root / "z.json").write_bytes(b"{}")
    (root / "a.json").write_bytes(b"{}")
    entries = versioning._dir_entries(root)
    keys = [e.split(":")[0] for e in entries]
    assert keys == ["a.json", "sub/deep/x.json", "z.json"], keys
    assert all("\\" not in key for key in keys)
    assert entries == sorted(entries)


# ── §5.2 source 三态 ───────────────────────────────────────────────────

def test_source_builtin_when_private_missing(tmp_path):
    result = versioning.knowledge_fingerprint(tmp_path / "nope")
    assert result["source"] == "builtin" and result["sha1"]
    assert versioning.knowledge_fingerprint(None)["source"] == "builtin"


def test_source_unreadable_is_explicit_not_silent(tmp_path, monkeypatch):
    """目录存在但读崩 → source=unreadable + error，绝不裸崩、绝不静默顶一个假 sha1。"""
    root = tmp_path / "kb"
    root.mkdir()
    (root / "a.json").write_bytes(b"{}")

    def boom(self, *args, **kwargs):
        raise PermissionError("被占用")
    monkeypatch.setattr("pathlib.Path.read_bytes", boom)
    result = versioning.knowledge_fingerprint(root)
    assert result["source"] == "unreadable" and result["sha1"] == ""
    assert "PermissionError" in result["error"]


def test_private_root_resolution_falls_back_to_env(monkeypatch, tmp_path):
    """唯一取源：actor.knowledge.root → 插件装配 → ATLAS_KNOWLEDGE_DIR（不留第二份判据）。"""
    class FakeKb:
        root = tmp_path / "kb"
    class FakeActor:
        knowledge = FakeKb()
    assert versioning.private_knowledge_root(FakeActor()) == FakeKb.root
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    assert versioning.private_knowledge_root(object()) is None
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(tmp_path / "priv"))
    assert versioning.private_knowledge_root(object()) == tmp_path / "priv"


# ── §5.3 flow 摘要两态 ─────────────────────────────────────────────────

def test_flow_digest_uses_source_md5_when_available(tmp_path):
    text = "id: f\nsteps:\n- task: noop\n"
    doc = FlowDocument(yaml.safe_load(text), source_text=text, source_name="f.yaml")
    digest = versioning.flow_digest(doc)
    assert digest["kind"] == "source" and digest["md5"] == doc.md5


def test_flow_digest_derived_for_inline_dict_and_stable():
    flow = {"id": "f", "steps": [{"task": "api_call", "path": "/x"}]}
    first = versioning.flow_digest(flow)
    assert first["kind"] == "derived" and len(first["md5"]) == 12
    assert versioning.flow_digest(flow) == first
    # 键序不同不改摘要（sort_keys）
    other = {"steps": [{"path": "/x", "task": "api_call"}], "id": "f"}
    assert versioning.flow_digest(other) == first


def test_overlay_copy_keeps_source_kind(tmp_path):
    """dsl.overlay() 的副本必须仍是 source——CLI run 用 `{**flow}` 解包会丢形态，
    丢形态就悄悄降级成 derived（设计 §5.3 的防复发钉桩）。"""
    text = "id: f\nsteps:\n- task: noop\n"
    doc = FlowDocument(yaml.safe_load(text), source_text=text, source_name="f.yaml")
    merged = doc.overlay(engine=None)
    assert versioning.flow_digest(merged)["kind"] == "source"
    assert versioning.flow_digest({**merged})["kind"] == "derived"


def test_derived_digest_ignores_injected_engine_config():
    """flow['engine'] 是 AtlasConfig（str 带内存地址）——进了摘要就每次 run 变一个值，
    三元组「本机今天 vs 上周」的前提当场失效。"""
    class Cfg:
        def __str__(self):
            return "<AtlasConfig at 0xdeadbeef>"
    plain = {"id": "f", "steps": []}
    with_cfg = {"id": "f", "steps": [], "engine": Cfg()}
    assert versioning.flow_digest(plain) == versioning.flow_digest(with_cfg)


# ── §5.4 recorder 接线与零涟漪 ─────────────────────────────────────────

def test_recorder_without_version_writes_no_key(tmp_path):
    path = tmp_path / "runs.jsonl"
    RunRecorder("case", "sit", path).close("passed")
    assert "version" not in _line_of(path)


def test_recorder_with_version_writes_expected_shape(tmp_path):
    path = tmp_path / "runs.jsonl"
    triple = versioning.version_triple(None, flow={"steps": []})
    RunRecorder("case", "sit", path, version=triple).close("passed")
    record = _line_of(path)["version"]
    assert set(record) == {"atlas", "knowledge", "flow"}
    assert record["knowledge"]["source"] in ("private", "builtin")
    assert record["flow"]["kind"] == "derived"


def test_dsl_run_lands_triple_into_runs(tmp_path):
    """真实执行链接线：dsl.run 建 recorder 时算三元组——文件形态的 flow 必须报 source。"""
    from atlas import Actor
    from atlas.config import AtlasConfig

    flow_text = "id: triple-case\nsteps:\n- task: noop\n"
    flow_path = tmp_path / "flow.yaml"
    flow_path.write_text(flow_text, encoding="utf-8")

    compiler = FlowCompiler(TaskRegistry())
    compiler.registry.register("noop", lambda **kw: _NoopTask())
    config = AtlasConfig(tmp_path, mode="run")
    doc = compiler.load(flow_path)
    compiler.run(doc.overlay(engine=config), Actor())   # CLI run 的真实形态：overlay 保形态
    record = _line_of(config.reports_dir / "runs.jsonl")
    assert record["case"] == "triple-case"
    assert record["version"]["flow"]["kind"] == "source"
    assert record["version"]["flow"]["md5"] == doc.md5
    assert record["version"]["atlas"]


class _NoopTask:
    from atlas.core import TaskMetadata
    metadata = TaskMetadata("demo", "noop", produces=("x",), read_only=True)
    name = "noop"

    def perform(self, actor):
        return {"x": 1}


# ── §5.5/§5.6 Allure parameters 与 doctor ─────────────────────────────

def test_allure_parameters_only_env_for_old_records(tmp_path):
    from atlas.allure_report import _run_to_result
    run = {"case": "c", "env": "sit", "status": "passed", "steps": [],
           "started_at": "2026-11-06T00:00:00+00:00",
           "finished_at": "2026-11-06T00:00:01+00:00"}
    result = _run_to_result(run, tmp_path, None, tmp_path / "runs.jsonl")
    assert result["parameters"] == [{"name": "env", "value": "sit"}]


def test_allure_parameters_carry_suffixes_when_version_present(tmp_path):
    from atlas.allure_report import _run_to_result
    run = {"case": "c", "env": "sit", "status": "passed", "steps": [],
           "started_at": "2026-11-06T00:00:00+00:00",
           "finished_at": "2026-11-06T00:00:01+00:00",
           "version": {"atlas": "0.1.0",
                       "knowledge": {"sha1": "abc123", "source": "private", "files": 3},
                       "flow": {"md5": "9f8e", "kind": "source"}}}
    params = {p["name"]: p["value"] for p in _run_to_result(
        run, tmp_path, None, tmp_path / "runs.jsonl")["parameters"]}
    assert params["atlas_version"] == "0.1.0"
    assert params["knowledge_sha1"] == "abc123#private"
    assert params["flow_md5"] == "9f8e#source"
    assert params["env"] == "sit"


def test_allure_bad_version_shape_does_not_break_result(tmp_path):
    from atlas.allure_report import _run_to_result
    run = {"case": "c", "env": "sit", "status": "passed", "steps": [], "version": "字符串"}
    assert _run_to_result(run, tmp_path, None, tmp_path / "runs.jsonl")["parameters"] == \
        [{"name": "env", "value": "sit"}]


def test_doctor_reports_version_triple(tmp_path, capsys):
    from atlas.cli import main
    assert main(["--root", str(tmp_path), "doctor"]) in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    triple = payload["version_triple"]
    from atlas import __version__
    assert triple["atlas"] == __version__
    assert triple["knowledge"]["source"] in ("private", "builtin", "unreadable")
    assert isinstance(triple["tz_data"]["ok"], bool)
    if not triple["tz_data"]["ok"]:
        assert "tzdata" in triple["tz_data"]["hint"]


def test_summary_exposes_version_drift(tmp_path):
    """读侧：同一份 runs.jsonl 里知识指纹变过 → summary 的 drifted 点名该维度。"""
    from atlas.reporting import summarize
    runs = tmp_path / "runs.jsonl"
    rows = [
        {"run_id": "1", "case": "a", "env": "sit", "status": "passed", "steps": [],
         "version": {"atlas": "0.1.0",
                     "knowledge": {"sha1": "old1", "source": "private", "files": 3},
                     "flow": {"md5": "f1", "kind": "source"}}},
        {"run_id": "2", "case": "a", "env": "sit", "status": "failed", "steps": [],
         "version": {"atlas": "0.1.0",
                     "knowledge": {"sha1": "new2", "source": "private", "files": 4},
                     "flow": {"md5": "f1", "kind": "source"}}},
        {"run_id": "3", "case": "a", "env": "sit", "status": "passed", "steps": []},
    ]
    runs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    summary = summarize(runs)
    assert summary["versions"]["drifted"] == ["knowledge"]
    assert summary["versions"]["runs_without_version"] == 1
    assert summary["versions"]["knowledge"]["old1#private"] == 1
    assert summary["versions"]["atlas"]["0.1.0"] == 2


def test_summary_all_same_triple_reports_no_drift(tmp_path):
    from atlas.reporting import summarize
    runs = tmp_path / "runs.jsonl"
    triple = {"atlas": "0.1.0", "knowledge": {"sha1": "s", "source": "builtin", "files": 7},
              "flow": {"md5": "m", "kind": "derived"}}
    rows = [{"run_id": str(i), "case": "a", "env": "sit", "status": "passed",
             "steps": [], "version": triple} for i in range(2)]
    runs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert summarize(runs)["versions"]["drifted"] == []


def test_tz_data_status_shape():
    result = versioning.tz_data_status()
    assert set(result) == {"ok", "name", "hint"}
    assert result["name"] == "Asia/Shanghai"
    if not result["ok"]:
        assert "tzdata" in result["hint"], "不可用必须点名 pip install tzdata，不静默回落 UTC"

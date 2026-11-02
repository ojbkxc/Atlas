from atlas.reporting import export_runlog, summarize
from atlas.recorder import RunRecorder
from atlas.contracts import ContractRegistry


def test_runlog_export_uses_step_metrics(tmp_path):
    recorder = RunRecorder("case", "sit", tmp_path / "atlas.jsonl")
    with recorder.step("1") as step:
        step["api_ms"] = 12
    recorder.close()
    output = export_runlog(tmp_path / "atlas.jsonl", tmp_path / "runs.jsonl")
    assert '"api_ms": 12' in output.read_text(encoding="utf-8")


def test_runlog_export_handles_empty_steps(tmp_path):
    """空 steps 的 run 导出空文件。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({"run_id": "r1", "case": "t", "env": "s", "steps": []}) + "\n",
                    encoding="utf-8")
    output = tmp_path / "out.jsonl"
    export_runlog(runs, output)
    assert output.read_text(encoding="utf-8").strip() == ""


def test_summarize_aggregates_multiple_runs(tmp_path):
    """summarate 聚合多次运行的状态与失败类。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps({"case": "t1", "status": "passed", "steps": [{"step": "go", "dur_ms": 10}]}) + "\n"
        + json.dumps({"case": "t2", "status": "failed",
                      "steps": [{"step": "save", "dur_ms": 20, "fail_class": "interaction"}]}) + "\n",
        encoding="utf-8")
    report = summarize(runs)
    assert report["runs"] == 2
    assert report["status"]["passed"] == 1
    assert report["status"]["failed"] == 1
    assert report["fail_classes"]["interaction"] == 1


def test_toolbox_sensor_takes_one_snapshot_and_imports_weak_contracts():
    class Bridge:
        def __init__(self): self.calls = 0
        def call(self, method, params=None):
            self.calls += 1
            return {"items": [{"method": "POST", "path": "/orders", "status": 200}]}
    from atlas.toolbox import WebToolboxSensor
    bridge = Bridge()
    registry = ContractRegistry()
    assert WebToolboxSensor(bridge, registry).learn_contracts() == 1
    assert bridge.calls == 1
    assert ("POST", "/orders") in registry.contracts

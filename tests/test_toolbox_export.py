import json
from atlas.toolbox import WebToolboxSensor


class Bridge:
    def __init__(self):
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        return {
            "export_har": {"log": {"entries": []}},
            "export_python": "import requests\n",
            "console_read": {"items": [{"level": "error", "text": "TypeError"}]},
        }.get(method, {"items": []})


def test_sensor_exports_har_and_python():
    sensor = WebToolboxSensor(Bridge())
    assert sensor.export_har()["log"]["entries"] == []
    assert "requests" in sensor.export_python()


def test_sensor_reads_only_error_console():
    bridge = Bridge()
    errors = WebToolboxSensor(bridge).console_errors()
    assert errors[0]["text"] == "TypeError"
    assert bridge.calls[-1] == ("console_read", {"level": "error", "limit": 100})


def test_sensor_saves_har_to_disk(tmp_path):
    path = WebToolboxSensor(Bridge()).save_har(tmp_path / "artifacts" / "x.har")
    assert json.loads(path.read_text(encoding="utf-8"))["log"]["entries"] == []


def test_sensor_learn_contracts_imports_liveness_ones():
    """learn_contracts 把 200 抓包登记为 minimal（存活级），不自动升 high。
    high 要求 data 为对象，会误判返回数组的列表接口；minimal 只判存活，安全且符合
    「新增接口默认落 minimal」的分级纪律。"""
    from atlas.contracts import ContractRegistry
    class LearningBridge:
        def call(self, method, params=None):
            return {"items": [
                {"method": "POST", "path": "/api/test", "status": 200},
                {"method": "GET", "path": "/api/other", "status": 500},
            ]}
    registry = ContractRegistry([])
    sensor = WebToolboxSensor(LearningBridge(), contracts=registry)
    count = sensor.learn_contracts()
    assert count == 1
    assert registry.stats().get("minimal") == 1
    assert registry.stats().get("high") is None          # 不再误升 high
    # 关键：登记后对返回数组 data 的列表接口不得误判为失败（high 级会抛「data 应为对象」）
    registry.validate("POST", "/api/test", {"ok": True, "data": [{"paymentTerms": "NET30"}]})


def test_sensor_learn_contracts_requires_registry():
    """未配置 ContractRegistry 时 learn_contracts 报错。"""
    import pytest
    with pytest.raises(RuntimeError, match="未配置"):
        WebToolboxSensor(Bridge()).learn_contracts()


def test_sensor_list_get_replay_snapshot_delegate_to_bridge():
    """感知器官方法委托给 bridge，参数透传。"""
    bridge = Bridge()
    sensor = WebToolboxSensor(bridge)
    sensor.list_requests(method="POST")
    assert bridge.calls[-1] == ("net_list", {"method": "POST"})
    sensor.get_request("r1")
    assert bridge.calls[-1] == ("net_get", {"id": "r1"})
    sensor.replay(method="POST")
    assert bridge.calls[-1] == ("chain_replay", {"method": "POST"})
    sensor.snapshot()
    assert bridge.calls[-1] == ("page_snapshot", None)


def test_sensor_export_jmx_delegates_to_bridge():
    """export_jmx 委托给 bridge。"""
    bridge = Bridge()
    WebToolboxSensor(bridge).export_jmx(method="POST")
    assert bridge.calls[-1] == ("export_jmx", {"method": "POST"})

"""toolbox 是感知器官，不替代 Playwright 执行底座。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Protocol

class ToolboxBridge(Protocol):
    def call(self, method: str, params: dict | None = None) -> Any: ...

class WebToolboxSensor:
    def __init__(self, bridge: ToolboxBridge, contracts=None):
        self.bridge, self.contracts = bridge, contracts

    def list_requests(self, **filters) -> Any:
        return self.bridge.call("net_list", filters)

    def get_request(self, request_id: str) -> Any:
        return self.bridge.call("net_get", {"id": request_id})

    def replay(self, **filters) -> Any:
        return self.bridge.call("chain_replay", filters)

    def snapshot(self) -> Any:
        return self.bridge.call("page_snapshot")

    def learn_contracts(self) -> int:
        """导入已审核的弱契约，不盲目把 数百条接口全变成强 schema。"""
        if self.contracts is None:
            raise RuntimeError("未配置 ContractRegistry")
        from .contracts import Contract
        captured = self.list_requests()
        requests = captured.get("items", captured) if isinstance(captured, dict) else captured
        count = 0
        for item in requests if isinstance(requests, list) else []:
            method, path = item.get("method", "GET"), item.get("path", "")
            if path and item.get("status") == 200:
                self.contracts.add(Contract(name=path, method=method, path=path, level="weak"))
                count += 1
        return count

    def export_jmx(self, **filters) -> Any:
        return self.bridge.call("export_jmx", filters)

    def export_har(self, **filters) -> Any:
        return self.bridge.call("export_har", filters)

    def export_python(self, **filters) -> Any:
        return self.bridge.call("export_python", filters)

    def console_errors(self, limit: int = 100) -> list:
        """只取 error 级控制台输出，供失败调查；不替代后端响应实证。"""
        result = self.bridge.call("console_read", {"level": "error", "limit": limit})
        items = result.get("items", result) if isinstance(result, dict) else result
        return items if isinstance(items, list) else []

    def save_har(self, path: Path, **filters) -> Path:
        """导出抓包前必须确认不含生产凭据；本方法不做脱敏，由使用者负责。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.export_har(**filters), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path


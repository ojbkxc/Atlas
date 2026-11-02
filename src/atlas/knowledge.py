"""项目知识是数据，不固化进引擎；写入均支持人工审核。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .failures import FailureClass, classify_failure

class KnowledgeBase:
    def __init__(self, root: Path):
        self.root = root

    def load(self, name: str) -> Any:
        path = self.root / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def pitfalls(self, domain: str = "") -> list[dict]:
        rows = self.load("pitfalls").get("pitfalls", [])
        return [row for row in rows if not domain or domain in row.get("scope", [])]

    def match_pitfall(self, exc: BaseException, domain: str = "") -> dict | None:
        text = f"{type(exc).__name__} {exc}".lower()
        matches = [row for row in self.pitfalls(domain)
                   if row.get("trigger", "").lower() in text]
        return max(matches, key=lambda row: len(row.get("trigger", "")), default=None)

    def draft_pitfall(self, exc: BaseException, *, symptom: str, root_cause: str,
                      remedy: str, scope: list[str], source: str) -> dict:
        """只生成待审核草稿，未经人审不写进正式知识库。"""
        kind: FailureClass = classify_failure(exc)
        return {"id": "draft-" + str(abs(hash(symptom))), "trigger": type(exc).__name__,
                "symptom": symptom, "root_cause": root_cause, "remedy": remedy,
                "scope": scope, "severity": "medium", "source": source,
                "failure_class": kind.value, "status": "pending_review"}

    def save_reviewed(self, name: str, records: list[dict], *, approved: bool = False) -> Path:
        if not approved:
            raise PermissionError("知识库写回需要人工审核；请传 approved=True 明确批准")
        path = self.root / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({name: records}, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

class SelectorRegistry:
    def __init__(self, components: dict):
        self.components = components.get("components", components)

    def get(self, component: str, element: str) -> str:
        try:
            return self.components[component]["selectors"][element]
        except KeyError as exc:
            raise KeyError(f"选择器未注册：{component}.{element}") from exc

    def find(self, component: str) -> dict:
        """返回组件的全部选择器与说明，供 agent 探查后再动手。"""
        if component not in self.components:
            raise KeyError(f"组件未注册：{component}")
        return self.components[component]

    def names(self) -> list[str]:
        return sorted(self.components)

    def missing(self) -> list[str]:
        """列出缺少 selectors 的组件，区分“只有知识说明”与“可直接定位”。"""
        return sorted(name for name, spec in self.components.items() if not spec.get("selectors"))

    def draft_update(self, component: str, element: str, selector: str) -> dict:
        return {"component": component, "element": element, "selector": selector,
                "status": "pending_review"}

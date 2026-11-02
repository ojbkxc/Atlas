"""零依赖运行记录；run 模式不保存成功截图。"""
from __future__ import annotations
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from .failures import classify_failure

class RunRecorder:
    def __init__(self, case: str, env: str, path: Path, mode: str = "run"):
        self.case, self.env, self.path, self.mode = case, env, path, mode
        self.run_id = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.steps: list[dict] = []
        self._closed = False

    @contextmanager
    def step(self, step: str, op: str = "action") -> Iterator[dict]:
        item = {"step": str(step), "op": op, "started": time.perf_counter()}
        try:
            yield item
        except BaseException as exc:
            item["fail_class"] = classify_failure(exc).value
            raise
        finally:
            started = item.pop("started")
            item["dur_ms"] = round((time.perf_counter() - started) * 1000, 1)
            item["ok"] = "fail_class" not in item
            self.steps.append(item)

    def close(self, status: str = "passed") -> Path:
        if self._closed:
            return self.path
        self._closed = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"run_id": self.run_id, "case": self.case, "env": self.env,
                  "mode": self.mode, "status": status, "started_at": self.started_at,
                  "finished_at": datetime.now(timezone.utc).isoformat(), "steps": self.steps}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())  # 崩溃时也不丢记录或写残行；读取侧另有残行容错
        return self.path

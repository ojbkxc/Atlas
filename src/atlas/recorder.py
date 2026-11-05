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
            # #126 面A：定性器不得先于被定性对象死（#89 同红线在 recorder 层的对应面）。
            # 业务插件自定义异常的 __str__ 自身抛错（引用未初始化属性——真实可达雷区）时，
            # classify_failure 在 except 块里崩：新异常顶掉真因（During handling 链），且
            # fail_class 没赋上 → finally 判 `ok = "fail_class" not in item` 把失败步骤记成
            # ok=True 假绿（#102 同族：坏形态伪装成功结果）。诚实回落 unknown：定性失败
            # 本身就是无法归因，与 classify_failure(None) 的既有语义一致，绝不吞真因。
            try:
                item["fail_class"] = classify_failure(exc).value
            except Exception:
                item["fail_class"] = "unknown"
            raise
        finally:
            started = item.pop("started")
            item["dur_ms"] = round((time.perf_counter() - started) * 1000, 1)
            item["ok"] = "fail_class" not in item
            self.steps.append(item)

    def close(self, status: str = "passed") -> Path:
        if self._closed:
            return self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"run_id": self.run_id, "case": self.case, "env": self.env,
                  "mode": self.mode, "status": status, "started_at": self.started_at,
                  "finished_at": datetime.now(timezone.utc).isoformat(), "steps": self.steps}
        # default=str：某个异常字段（Path/set/自定义对象）不得让整轮证据静默丢失。
        # close 在 flow 的 finally 里调用，序列化若抛错还会顶掉真实用例结果；审计日志保真优先于严格类型。
        # #126 面B：`str` 兜底自身不是全函数——业务异常对象 __str__ 引用未初始化属性时
        # default=str 直接崩 RuntimeError，整轮记录落不了盘（违背上面注释的初衷），且 close
        # 在 finally 里顶掉用例真因、_closed 不置位重试永远崩。永不抛的兜底：str 失败落
        # 类型占位符——记录可以有噪音，绝不能丢整轮证据或替调用方崩。
        def _safe_default(obj):
            try:
                return str(obj)
            except Exception:
                return f"<unrepresentable {type(obj).__name__}>"
        line = json.dumps(record, ensure_ascii=False, default=_safe_default)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())  # 崩溃时也不丢记录或写残行；读取侧另有残行容错
        self._closed = True  # 仅在证据确实落盘后置位：写失败不谎报「已关闭」，仍可重试
        return self.path

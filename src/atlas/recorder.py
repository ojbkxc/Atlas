"""零依赖运行记录；run 模式不保存成功截图。"""
from __future__ import annotations
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from .failures import classify_failure

# ── 进程安全（并发执行层短板 #2）─────────────────────────────────────────────
# runs.jsonl 是行级 append，多进程并发跑 `atlas run` 时裸 open("a") 会交错出残行。
# 唯一写点 close() 改为「排他文件锁内整行一次写入」——实现已提到 `atlas.atomic_io`
# （wave4 cc-1：六条无锁追加路径统一复用同一把锁，一处实现不留第二把尺；runs.jsonl 传裸
# `\n` 保持 recorder 既有 LF 约定）。import 别名保持本模块调用点不变。
from .atomic_io import append_line_locked as _append_line_locked


def _split_runs_path(path: Path, plugin_name: str) -> Path:
    """en-8 写侧：把 `runs.jsonl` 改名为同级 `runs.<plugin>.jsonl`（单文件多平台混写分流）。

    只在 ``ATLAS_RUNS_SPLIT`` 精确等于 ``"plugin"`` 时经调用方触达；插件名做文件系统
    安全校验（拒路径分隔符等），坏名当场点名 ValueError（显式 opt-in 后才可能发生，
    不让一个奇怪插件名悄悄写出 `../` 越界路径）。
    """
    if not plugin_name or any(ch in plugin_name for ch in ("/", "\\", " ")):
        raise ValueError(f"ATLAS_RUNS_SPLIT=plugin 需要合法的插件名（收到 {plugin_name!r}）")
    return path.with_name(f"{path.stem}.{plugin_name}{path.suffix}")


class RunRecorder:
    def __init__(self, case: str, env: str, path: Path, mode: str = "run",
                 version: dict | None = None):
        # version：C6 版本三元组（引擎版本 + 知识库指纹 + flow 摘要），由调用方（dsl.run）
        # 算好传入——**recorder 保持零依赖纪律**，不在这里 import versioning/知识库。
        # 缺省 None 时不写键：存量 runs.jsonl 形态零涟漪（#115 不把正常路径翻成错误），
        # 读侧（reporting/allure）按键存在与否分流。
        # ATLAS_RUNS_DIR（并发逃生舱，与上面的文件锁双保险）：设置后本进程的 run 记录
        # 改写到该目录下的同名文件——文件锁解决「共享目录里多进程交错」，本变量解决
        # 「并发进程根本不想共享目录」（如并行跑两个 BU 的巡检各出各的 runs.jsonl）。
        # 相对值按**传入 path 的父目录**（默认即 reports/）解析，不赌 CWD——与 atlas
        # 既有路径纪律同款；绝对值原样使用。未设置时行为逐字不变。
        override = os.environ.get("ATLAS_RUNS_DIR", "").strip()
        directory = Path(override) if override else None
        if directory is not None and not directory.is_absolute():
            directory = path.parent / directory
        self.case, self.env, self.mode = case, env, mode
        self.path = (directory / path.name) if directory is not None else path
        # en-8：单文件多平台混写 → 按平台分流到 `runs.<plugin>.jsonl`（opt-in）。
        # 只认精确值 "plugin"（对齐 ATLAS_ALLOW_PROD_WRITE 只认精确 "1" 的既有纪律：
        # 默认关的开关不因近似拼写启用）；未设置/其余值一律不改变 path（逐字节旧行为）。
        # 插件名来自 resolve_plugin().name 单点，不在引擎里散写业务字面量（红线 #11）；
        # 惰性 import 保住「默认路径零新增依赖」——只有真正 opt-in 才去装配插件。
        if os.environ.get("ATLAS_RUNS_SPLIT") == "plugin":
            from .plugin_api import resolve_plugin
            self.path = _split_runs_path(self.path, getattr(resolve_plugin(), "name", ""))
        self.version = version
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

    def close(self, status: str = "passed", reason: str = "") -> Path:
        # C9：`reason` 是 run 级「为什么是这个终态」的落盘面（等人时点名在等什么、需要谁）。
        # 与 C6 `version` 参同款纪律——**末位可选参、非空才写键**：缺省不写=存量
        # runs.jsonl 形态零涟漪（passed/failed/env_blocked/interrupted 行逐字节不变，#115
        # 不把正常路径翻成错误），只有真正带上原因的终态（waiting）多出一个顶层键。
        # 读侧已有消费方（allure_report 的 `run.get("reason")`），本参数把历史上恒空的那条
        # 读线接上；写侧不做任何判定，纯落盘——分类判据仍只在 failures 一处（#92）。
        if self._closed:
            return self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"run_id": self.run_id, "case": self.case, "env": self.env,
                  "mode": self.mode, "status": status, "started_at": self.started_at,
                  "finished_at": datetime.now(timezone.utc).isoformat(), "steps": self.steps}
        if reason:
            record["reason"] = reason
        # C6：三元组只在调用方给了才写键（缺省不写=旧形态）；键位固定在 steps 之后，
        # 读侧按键取值、不按下标。序列化失败兜底同下方 _safe_default（证据优先于严格类型）。
        if self.version is not None:
            record["version"] = self.version
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
        # 进程安全写点：排他文件锁内整行追加（见模块头注释）。编码显式 UTF-8；
        # os.open 目录等失败仍抛 OSError → _closed 不置位（可重试，见下方契约）。
        _append_line_locked(self.path, (line + "\n").encode("utf-8"))
        self._closed = True  # 仅在证据确实落盘后置位：写失败不谎报「已关闭」，仍可重试
        return self.path

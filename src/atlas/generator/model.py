"""生成器内部数据模型：文档解析后的统一 Endpoint 形态。

不依赖具体文档格式（OpenAPI/Swagger/Markdown/纯文本）；sources.py 嗅探后把各
种来源归一到这里的 dataclass，再由 emit_* 映射成 DSL/契约/探针。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# 置信度：high=文档明确给出，medium=推断，low=猜测/缺样例。
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


@dataclass
class Param:
    """单个参数（path/query/header）。"""
    where: str               # "path" | "query" | "header"
    name: str
    required: bool = False
    schema: dict | None = None
    example: Any = None
    confidence: str = CONFIDENCE_HIGH


@dataclass
class BodySpec:
    """请求体规格。

    kind: "json" → example_obj 是 dict/list（emit 成 YAML 映射 → json=）
          "form" → pairs 是 list[(k,v)]（emit 成 a=1&b=2 字符串 → data=）
          "text" → text 字符串（→ data=）
          "unsupported" → 不生成 body（如 multipart 文件上传，报告点名手工处理）
          "none" → 无请求体
    """
    kind: str = "none"
    example_obj: Any = None
    pairs: list[tuple[str, str]] = field(default_factory=list)
    text: str = ""
    schema: dict | None = None
    confidence: str = CONFIDENCE_HIGH


@dataclass
class ResponseSpec:
    """响应规格，用于契约候选与断言生成。"""
    is_object: bool = False      # 响应 data 是对象
    is_array: bool = False       # 响应 data 是数组
    has_result_code: bool = False  # 对象含 resultCode
    sample: Any = None           # 样例 JSON（如有）
    # core 档 required 路径（仅 core 时填充）：{path: type}
    required_paths: dict[str, str] = field(default_factory=dict)
    confidence: str = CONFIDENCE_HIGH


@dataclass
class Endpoint:
    """一个文档端点归一后的形态。"""
    method: str = "POST"
    path: str = ""
    summary: str = ""
    params: list[Param] = field(default_factory=list)
    body: BodySpec = field(default_factory=BodySpec)
    response: ResponseSpec = field(default_factory=ResponseSpec)
    base: str = ""                # servers[0].url / basePath，仅报告提示
    # 文档显式标注（x-atlas-* 扩展）
    level_override: str = ""      # "core" | "high" | ""
    scenario_override: str = ""   # "a" | "b" | "c" | "d" | ""
    save_as_override: str = ""
    flow_override: str = ""       # x-atlas-flow
    confidence: str = CONFIDENCE_HIGH
    low_confidence_notes: list[str] = field(default_factory=list)

    @property
    def is_write(self) -> bool:
        """写操作判定（场景 D）：方法或路径含写语义关键词。"""
        write_methods = {"POST", "PUT", "PATCH", "DELETE"}
        text = (self.summary + " " + self.path).lower()
        write_kw = ("save", "submit", "create", "delete", "remove", "update",
                    "saveorder", "savesalesorder")
        return self.method.upper() in write_methods or any(k in text for k in write_kw)

    def low_confidence(self) -> list[dict]:
        """低置信度项单列（--strict 时 rc 2 的依据）。"""
        items = []
        if self.confidence == CONFIDENCE_LOW:
            items.append({"where": f"{self.method} {self.path}",
                          "reason": "端点识别置信度低"})
        for note in self.low_confidence_notes:
            items.append({"where": f"{self.method} {self.path}", "reason": note})
        # body 缺样例
        if self.body.kind == "json" and self.body.example_obj is None:
            items.append({"where": f"{self.method} {self.path} body",
                          "reason": "请求体无样例，已生成空映射待填"})
        return items

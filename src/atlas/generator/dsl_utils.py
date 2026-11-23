"""DSL 生成共享纯函数：slug、占位符扫描、body 分流判定。

与 scripts/import_jmeter.py 共享的纪律（照搬）：
- 占位符 `${...}` 递归下钻 path/body/headers，逐条告警，保留原样待人审接 env
- body 分流：dict/list → json=；str/bytes → data= 原样（不可混淆，否则后端假 400）

本包内先独立实现；「单一真相」待后续 P3 收敛（与 Gap5 §4.2 同手法），届时让
import_jmeter.py 改为 import 这里，回归测钉死行为零变化。
"""
from __future__ import annotations
import re
import warnings
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def slugify(text: str, *, fallback: str = "flow") -> str:
    """把文档标题/路径派生成稳定 flow id（无随机、无时间）。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip()).strip("-").lower()
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned or fallback


def identifier_slug(text: str, *, fallback: str = "step") -> str:
    """slugify 后转成**合法 Python 标识符片段**（非字母数字 → `_`）。

    判据单点：产物要把名字写进 `def`/`class`/文件名时都走这里（emit_pytest 的模块位/类名、
    emit_probe 的探针身份）。旧形态各自 `slugify` 后直接拼进代码，`get-web-example` 这种
    带 `-` 的 slug 会产出 `class get-web-example…` —— 产物语法错、且生成期看不到（#9 同族）。
    """
    s = slugify(text, fallback=fallback).replace("-", "_")
    s = re.sub(r"[^0-9a-zA-Z_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = f"s_{s}" if s else fallback
    return s


def pascal_identifier(text: str, *, fallback: str = "Component") -> str:
    """`get-web-example` → `GetWebExample`（类名惯制）；空输入给 fallback。

    与 `identifier_slug` 同一族：产物类名唯一取自这里（emit_pytest 页对象、emit_probe 探针），
    不各写一份 Pascal 逻辑（#92）。
    """
    parts = re.split(r"[^0-9a-zA-Z]+", text)
    out = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return out or fallback


def artifact_compile_problem(source: str, name: str) -> str | None:
    """产物源码能不能**按 CI 严档**编译（非法转义算问题）——返回问题文本，全绿返回 None。

    为什么不能只 `ast.parse`/裸 `compile`：``\\s`` 这类**非法转义**在默认档只是
    ``SyntaxWarning``（被忽略），``ast.parse`` 照样通过；产物于是「生成 rc 0 / 结构齐全 /
    一 import 就炸」，而生成期自检只编译本仓源码、看不见自己写出去的文件（#9 同一条缝，
    2026-11-23 真机：通用知识包 notes 里的正则 ``/确\\s*定/`` 进了 ``atlas_pages.py`` 的类
    docstring，3.13 + ``-W error::SyntaxWarning`` 直接 SyntaxError）。
    判据单点：写盘前的自检（emit_pytest / emit_compat）都调这里，不各写一份口径。
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            compile(source, name, "exec")
    except (SyntaxError, SyntaxWarning) as exc:
        return f"{name}: {type(exc).__name__}: {exc}"
    return None


def artifact_compile_problems(items) -> list[str]:
    """批量形态：``[(文件名, 源码), …]`` → 问题清单（空 = 全部可安全编译）。"""
    problems: list[str] = []
    for name, source in items:
        problem = artifact_compile_problem(source, str(name))
        if problem:
            problems.append(problem)
    return problems


def docstring_text(value: Any) -> str:
    """把外部文本（知识 ``notes``／文档 ``intent``／选择器元素名）渲染成**非 raw docstring 的安全内容**。

    为什么要有这一把刀（真机抓出，产物侧缺陷）：通用知识包 ``components.json`` 的 notes 里写着
    正则 ``/确\\s*定/``，原样插进产物 ``\"\"\"…\"\"\"`` 后 ``\\s`` 是**非法转义** —— 产物在 3.11 只报
    ``SyntaxWarning``，在 CI 常规档 ``-W error::SyntaxWarning`` 或更严格解释器上直接
    ``SyntaxError``：``atlas gen`` rc 0 写出「看起来完整」的包，消费方一 import 就崩，而生成期
    自检只编译**本仓源码**，看不见自己写出去的文件（与「只生成不执行」#9 同一条缝）。
    判据单点在此（emit_pytest / emit_compat 共用，不许各写一份）：转义反斜杠与双引号——引号转义后
    ``__doc__`` 取值逐字不变，只去掉危险形态；其余字符（中文/单引号/换行）都合法，原样保留，
    **不做** stderr 净化那类内容改写。
    """
    text = value if isinstance(value, str) else str(value or "")
    return text.replace("\\", "\\\\").replace('"', '\\"')


def scan_placeholders(value: Any, where: str, index: int,
                      found: list[dict]) -> None:
    """递归收集占位符命中项，写入 found（原地累积）。"""
    if isinstance(value, str):
        if _PLACEHOLDER.search(value):
            found.append({"step": index, "where": where, "value": value})
    elif isinstance(value, dict):
        for k, v in value.items():
            scan_placeholders(v, f"{where}.{k}", index, found)
    elif isinstance(value, list):
        for pos, v in enumerate(value):
            scan_placeholders(v, f"{where}[{pos}]", index, found)


def body_to_yaml_value(body: Any) -> Any:
    """把 BodySpec/dict/list/str 转成写入 flow step body 的值。

    dict/list 原样（YAML 加载即 dict/list → json=）；str/bytes 原样（→ data=）。
    这是 import_jmeter.to_atlas_dsl 的 json.loads 兜底同口径。
    """
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, str):
        return body
    return body


def form_pairs_to_string(pairs: list[tuple[str, str]]) -> str:
    """form 参数拼成 a=1&b=2 字符串（→ data= 原样）。"""
    return "&".join(f"{k}={v}" for k, v in pairs)


def collect_path_query(path: str, params: list) -> str:
    """path + query 参数拼进 path（api_call 无 query 旋钮，照搬 §4.1）。

    params 是 Param 列表（where in path/query）。path 里的 {x} 占位保留为 ${x}。
    """
    # path 占位 {id} → ${id}（DSL 插值），不猜值
    rendered = re.sub(r"\{([^}]+)\}", r"${_\1}", path)
    query = [p for p in params if p.where == "query"]
    if query:
        rendered += "?" + "&".join(
            f"{p.name}={p.example if p.example is not None else ''}" for p in query)
    return rendered

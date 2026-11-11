"""版本三元组（C6）：引擎版本 + 知识库指纹 + flow 摘要——唯一计算点。

排查「上周能跑今天炸」的第一手资料不是猜：本轮 run 用的引擎版本、私有知识库内容指纹、
flow 源文本 md5 三项都进 runs.jsonl 与 Allure parameters，前后两次跑直接 diff 三元组。

纪律：
- **纯读**指纹，不新增任何知识写路径（红线 #9/#10）；指纹是数据不是引擎业务字面量（#11）。
- 知识库版本**不用 git 资产版本冒充**——AGENTS 明说私有库是人手改的，git sha 盖不住未提交
  改动，所以取目录内容指纹（design_c6 §6）。
- sha1 而非内置 `hash()`：`hash(str)` 受 PYTHONHASHSEED 影响，跨进程/跨机不等值
  （`knowledge.draft_pitfall` 已有同款先例）。
- 键用 `as_posix()` 且排序：Windows 的反斜杠路径不得进指纹键，否则同一份知识库两台机器
  算出两个 sha1，「跨机可对比」这个前提当场失效（X1 专项测）。
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

# 内置（包内）知识库目录：私有目录缺失时的指纹来源
_BUILTIN_KNOWLEDGE = Path(__file__).resolve().parent / "plugins" / "demo" / "knowledge"
# flow dict 里不属于 flow 语义的注入键：AtlasConfig 的 str() 带内存地址，进 derived 摘要
# 就等于每次 run 得到不同 md5——「三元组定位本机今天 vs 上周」的前提被自己打掉。
_NON_FLOW_KEYS = frozenset({"engine"})


def _sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _dir_entries(base: Path) -> list[str]:
    """目录内全部 `*.json` 的条目串：`<posix 相对路径>:<文件字节 sha1>`。

    单独成函数是为了让「键是 POSIX 分隔且已排序」这一点可被直接断言（X1 专项测）——
    反斜杠混进键，同一份知识库在两台机器上就会算出两个 sha1，跨机对比前提当场失效。
    """
    entries = []
    for path in sorted(base.rglob("*.json")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        entries.append(f"{rel}:{_sha1_bytes(path.read_bytes())}")
    return sorted(entries)


def _digest_dir(base: Path, source: str) -> dict:
    """两级 sha1：逐文件字节摘要 → 排序拼接 → 总摘要[:12]。

    读崩（权限/目录被占/坏字节）→ `{"sha1": "", "source": "unreadable", "error": ...}`：
    诊断可以有噪音不能丢整份（#64，doctor 的 `_read_text_safe` 先例）。
    """
    try:
        entries = _dir_entries(base)
        files = len(entries)
        joined = "|".join(entries)
        return {"sha1": _sha1_bytes(joined.encode("utf-8"))[:12],
                "source": source, "files": files}
    except Exception as exc:
        return {"sha1": "", "source": "unreadable",
                "error": f"{type(exc).__name__}: {exc}"}


def knowledge_fingerprint(private_root: "Path | str | None") -> dict:
    """知识库指纹三态：private（私有目录有效）/ builtin（包内虚构默认）/ unreadable。

    `private_root` 指向不存在的路径按 builtin 计——那是 Atlas 的既有读语义（逐文件回退），
    指纹跟着语义走才不会出现「读的是包内、指纹报私有」的自相矛盾。
    """
    base = Path(private_root) if private_root is not None else None
    if base is not None and base.is_dir():
        return _digest_dir(base, "private")
    return _digest_dir(_BUILTIN_KNOWLEDGE, "builtin")


def flow_digest(flow: object) -> dict:
    """flow 摘要两态：文件加载来的 FlowDocument 用现成 `md5`（kind=source，不重算）；
    内存构造/dict 走派生 sha1（kind=derived）——降级是明写的，不是假象。
    """
    md5 = getattr(flow, "md5", None)
    source_text = getattr(flow, "source_text", "")
    if isinstance(md5, str) and md5 and source_text:
        return {"md5": md5, "kind": "source"}
    try:
        payload = {k: v for k, v in (flow or {}).items() if k not in _NON_FLOW_KEYS} \
            if isinstance(flow, dict) else flow
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        # 摘要器不得先于被摘要对象死：坏形态落 repr，kind 仍是 derived（不猜内容）。
        text = repr(flow)
    return {"md5": _sha1_bytes(text.encode("utf-8"))[:12], "kind": "derived"}


def version_triple(private_root: "Path | str | None", flow: object = None) -> dict:
    """三元组：`{"atlas": 版本, "knowledge": 指纹, "flow": 摘要|None}`。

    flow 为 None（doctor 等无 flow 场景）时 `flow` 键显式为 None——缺什么就写缺什么，
    不塞一个假摘要冒充「已核对」。
    """
    from . import __version__
    return {"atlas": __version__,
            "knowledge": knowledge_fingerprint(private_root),
            "flow": flow_digest(flow) if flow is not None else None}


def private_knowledge_root(actor: object = None) -> "Path | None":
    """私有知识根的唯一取源（不留第二份判据）。

    优先用**已在场的** `actor.knowledge.root`（KnowledgeBase 的写根，未设
    ATLAS_KNOWLEDGE_DIR 时为 None=只读库）；拿不到装配时才惰性走
    `resolve_plugin().knowledge()`，最后回落环境变量——三者同义，取到哪个都是同一判据。
    """
    root = getattr(actor, "knowledge", None)
    root = getattr(root, "root", None) if root is not None else None
    if root is not None:
        return Path(root)
    try:
        from .plugin_api import resolve_plugin   # 惰性：dsl ← plugin_api ← demo 会成环
        root = resolve_plugin().knowledge().root
    except Exception:
        root = None
    if root is not None:
        return Path(root)
    env = os.environ.get("ATLAS_KNOWLEDGE_DIR", "").strip()
    return Path(env) if env else None


def tz_data_status(name: str = "Asia/Shanghai") -> dict:
    """X6 前置体检：系统 tzdb / tzdata 是否可用（C1 调度的排雷项，归 C6 顺手落）。

    Windows 无系统 tzdb，`zoneinfo.ZoneInfo` 需要 `pip install tzdata`——**不硬依赖**
    （本函数只体检），不可用时点名提示而不是静默回落 UTC（UTC 偏移会让巡检窗口整体错位）。
    """
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return {"ok": True, "name": name, "hint": ""}
    except Exception as exc:
        return {"ok": False, "name": name,
                "hint": f"{type(exc).__name__}: {exc}；Windows 需 pip install tzdata"}

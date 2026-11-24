"""DevOps 平台同步（把 UI AUTO TEST 的 devops_sync.py 能力移植到 Atlas，场景 C-D1~C-D5）。

机制：JSON-RPC over HTTP 调 MCP 服务器 ``devops-mcp-agile``（streamable_http），
用到 5 个工具：``resolve_test_directory_by_path`` / ``list_testcases`` /
``import_testcases_to_library`` / ``create_test_plan`` / ``batch_relate_test_plan``。

落点文件（均在项目根，gitignore 外由调用方决定是否纳入版本控制）：
- ``devops-sync.yaml``        配置（system_id / system_name / exec_user / testcase_directory；
                              可选 ``mcp_server`` 显式选 MCP 服务器、``send_midea_mkt_id`` 开关）
- ``reports/allure-results/`` Allure 原始结果集（``*-result.json``，复用 gap5 emit 产物）
- ``reports/allure-history/`` 运行历史目录名（推导 run_key，可选）
- ``devops-sync-state.yaml``  状态（``{suite}_{run_key}`` → testPlanId，防止重复建计划）

MCP 服务器解析（**不硬编码服务器名**——本机是 sit/uat/prd，旧项目是 agile）：
``devops-sync.yaml`` 的 ``mcp_server`` / ``ATLAS_DEVOPS_MCP_SERVER`` 显式名 > ``ATLAS_DEVOPS_MCP_URL``
显式地址 > ``~/.agents/mcp.json`` 中唯一的 ``devops-mcp*`` 条目；**多个候选时报错要求显式指定**
（不静默择一环境）。``midea_mkt_id``（sit=505/uat=528/prd=570，来自 mcp.json）默认以
``X-Midea-Mkt-Id`` header 透传，可用 ``send_midea_mkt_id: false`` 关闭——引擎不含具体数值。

纪律（对齐 AGENTS/CLAUDE 红线）：
- **不引入运行时新依赖**：只用 stdlib + ``requests`` + ``PyYAML``（均已在 pyproject deps）。
- **默认 dry-run**：``sync_plan(execute=False)`` 只解析、只算 payload、**不发任何网络请求**；
  真实调用必须显式 ``execute=True``（对外平台同步属敏感操作，AGENTS §4 Ask first——默认不联网）。
- **引擎不含业务字面量**（红线 #11）：org_id/system_id/system_name 等真实值只来自配置文件
  或测试 fake，本模块零硬编码业务值；唯一常量 ``EXECUTION_METHOD`` 是 DevOps 平台规定的协议
  枚举值（guide §5.2 明确"值固定不变，不得改写"），属协议常量而非业务字面量。
- **读侧容错**（#131/#112）：结果集/状态文件的坏字节、坏 JSON、腰斩残行一律跳过或 replace
  回落，绝不因一条坏文件整份崩。
- **凭据不落盘**：MCP URL 常带 ``?token=…``，展示/摘要一律过
  ``credential_gate.sanitize_url``（#92 单一净化刀）；token 永不进 stdout/state。
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .allure_report import ALLURE_RESULTS_DIR
from typing import Any

import yaml

from .allure_analysis import load_allure_results
from .credential_gate import sanitize_url

# ── 协议常量（DevOps 平台规定值，非业务字面量）──────────────────────────
# guide §5.2：executionMethod 必须传 "UI自然语言测试"，不得省略/替换/自行改写。
EXECUTION_METHOD = "UI自然语言测试"
RELATE_ACTION = "RELATE"
MCP_SERVER_PREFIX = "devops-mcp"
LEGACY_MCP_SERVER_NAME = "devops-mcp-agile"
# midea_mkt_id 透传 header 键（市场 ID，sit=505/uat=528 来自 mcp.json；不写死在引擎）
MKT_ID_HEADER = "X-Midea-Mkt-Id"

# ── 默认路径/文件名 ─────────────────────────────────────────────────────
CONFIG_FILENAME = "devops-sync.yaml"
STATE_FILENAME = "devops-sync-state.yaml"
RESULTS_SUBPATH = ("reports", ALLURE_RESULTS_DIR)
HISTORY_SUBPATH = ("reports", "allure-history")
MCP_CONFIG_RELPATH = (".agents", "mcp.json")

# ── 字段映射表（guide §2.1 / §2.3）─────────────────────────────────────
# severity → priority（标签缺失默认 P1）
PRIORITY_MAP = {
    "blocker": "P0",
    "critical": "P0",
    "normal": "P1",
    "minor": "P2",
    "trivial": "P2",
}
# Allure status → DevOps execResult（1=通过 2=失败 3=阻塞；未知按失败）
EXEC_RESULT_MAP = {"passed": 1, "failed": 2, "broken": 2, "skipped": 3}


class DevopsSyncError(ValueError):
    """用户可修复的配置/环境错误（CLI 接 ``_user_error`` → rc 2 口径）。

    继承 ValueError：既有 CLI ``except (OSError, ValueError)`` 兜底链自动接住，
    lead 接线时无需为它新增分支（#79/#80 一行 stderr + rc 2）。
    """


# ══════════════════════════════════════════════════════════════════════════
# 配置解析（C-D1）
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class DevopsSyncConfig:
    """``devops-sync.yaml`` 的解析结果（字段语义见 guide §1）。"""

    system_id: str
    system_name: str
    exec_user: str
    testcase_directory: str
    testplan_title_template: str = "{suite_label}自动化测试计划_{timestamp}"
    org_id: str = ""
    suite_directories: dict[str, str] = field(default_factory=dict)
    # 可选的 MCP 服务器名（如 devops-mcp-sit / devops-mcp-uat；不设则自动探测，
    # 多个候选时报错要求显式指定——不静默择一）。
    mcp_server: str = ""
    # midea_mkt_id 透传开关：True=把 mcp.json 里该服务器的 midea_mkt_id 作为
    # X-Midea-Mkt-Id header 发出；False=不发（默认开，纯 header 透传不改变业务语义）。
    send_midea_mkt_id: bool = True

    def directory_for(self, suite_filter: str | None) -> str:
        """当前 suite 的上传目录：``suite_directories`` 专属目录优先，否则默认目录。"""
        if suite_filter and suite_filter in self.suite_directories:
            return self.suite_directories[suite_filter]
        return self.testcase_directory


def config_path(root: str | Path, *, path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else Path(root) / CONFIG_FILENAME


# `devops-sync.yaml` 的**合法键**——唯一真相是 `DevopsSyncConfig` 的字段名（下方
# `_warn_unknown_config_keys` 与 dataclass 锁步，改字段必须同步改这里；测试
# `tests/test_devops_config_unknown_keys.py` 逐项比对两者）。
# 为什么需要它（2026-11-24 实测）：YAML 里写错的键**被静默忽略**——我把 MCP 地址写成
# 嵌套的 `mcp:\n  url: …`，dry-run 照常 rc 0 且 `mcp_url` 是空串，直到 `--execute`
# 才发现「没配 MCP」。配置面静默吞键与「静默丢弃」同族：错的是人的手，痛的是后来的人。
_CONFIG_KEYS = (
    "system_id", "system_name", "exec_user", "testcase_directory",
    "testplan_title_template", "org_id", "suite_directories",
    "mcp_server", "send_midea_mkt_id",
)


def _warn_unknown_config_keys(data: dict, cfg_path: Path) -> None:
    """未知键 → 一行 WARN（点名键与合法键全集），**不阻断**。

    只警告不报错的理由：配置文件里常有人留自己的注释性键（`# todo: …` 之类的扩展位），
    直接 rc 2 会把能用配置翻成错误（#115 不把正常路径翻成错误）。但静默更坏——
    拼错的键（`mcp:` / `directory:`）会让人以为配好了。
    """
    unknown = sorted(str(k) for k in data if str(k) not in _CONFIG_KEYS)
    if unknown:
        print(f"WARN: {cfg_path} 含未知配置键 {unknown}——已被忽略；"
              f"合法键：{list(_CONFIG_KEYS)}", file=sys.stderr)


def load_config(root: str | Path, *, path: str | Path | None = None) -> DevopsSyncConfig:
    """读取 ``devops-sync.yaml``。

    ⛔ 文件不存在 → DevopsSyncError，点名路径与需补字段（guide C-D1）。
    ⛔ system_id / system_name / exec_user 为空 → DevopsSyncError（org_id 脚本不使用）。
    ⛔ 非映射顶层 / 坏 YAML → DevopsSyncError 点名文件（不裸崩）。
    """
    cfg_path = config_path(root, path=path)
    if not cfg_path.exists():
        raise DevopsSyncError(
            f"找不到 DevOps 同步配置：{cfg_path}。\n"
            "请参考模板新建 devops-sync.yaml，至少填写 system_id / system_name / exec_user，"
            "并设置 testcase_directory（用例库目标目录，格式「系统名/目录名」）。"
        )
    try:
        raw_text = cfg_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DevopsSyncError(f"无法读取配置 {cfg_path}：{exc}") from exc
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise DevopsSyncError(f"配置 {cfg_path} 不是合法 YAML：{exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise DevopsSyncError(
            f"配置 {cfg_path} 顶层必须是映射（键值对），实际是 {type(data).__name__}"
        )

    for key in ("system_id", "system_name", "exec_user"):
        if not data.get(key):
            raise DevopsSyncError(f"配置 {cfg_path} 缺少必填字段 {key}（或值为空）")

    system_name = str(data["system_name"])
    testcase_directory = data.get("testcase_directory") or f"{system_name}/未分类目录"
    template = (
        data.get("testplan_title_template") or "{suite_label}自动化测试计划_{timestamp}"
    )
    suite_directories = data.get("suite_directories") or {}
    if not isinstance(suite_directories, dict):
        raise DevopsSyncError(
            f"配置 {cfg_path} 的 suite_directories 必须是映射（suite→目录），"
            f"实际是 {type(suite_directories).__name__}"
        )
    _warn_unknown_config_keys(data, cfg_path)
    return DevopsSyncConfig(
        system_id=str(data["system_id"]),
        system_name=system_name,
        exec_user=str(data["exec_user"]),
        testcase_directory=str(testcase_directory),
        testplan_title_template=str(template),
        org_id=str(data.get("org_id") or ""),
        suite_directories={str(k): str(v) for k, v in suite_directories.items()},
        mcp_server=str(data.get("mcp_server") or ""),
        send_midea_mkt_id=bool(data.get("send_midea_mkt_id", True)),
    )


def mcp_config_path() -> Path:
    """``~/.agents/mcp.json``（DevOps MCP 服务器地址来源，guide/脚本一致）。"""
    return Path.home().joinpath(*MCP_CONFIG_RELPATH)


@dataclass
class McpServer:
    """``mcpServers.<name>`` 条目的解析结果（只取同步需要的字段）。"""

    name: str
    url: str
    midea_mkt_id: int | None = None
    type: str = "streamable_http"


def load_mcp_servers(mcp_config: str | Path | None = None) -> dict[str, dict]:
    """读 ``~/.agents/mcp.json`` → ``mcpServers`` 映射（坏形态不崩，读失败点名）。"""
    cfg = Path(mcp_config) if mcp_config is not None else mcp_config_path()
    if not cfg.exists():
        raise DevopsSyncError(
            f"MCP 配置文件不存在：{cfg}。"
            "\n请在 mcp.json 的 mcpServers 下配置 devops-mcp-* 服务器（url 带平台 key），"
            "或设置 ATLAS_DEVOPS_MCP_URL / ATLAS_DEVOPS_MCP_SERVER。"
        )
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        try:
            data = json.loads(cfg.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DevopsSyncError(f"MCP 配置 {cfg} 解析失败：{exc}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DevopsSyncError(f"MCP 配置 {cfg} 解析失败：{exc}") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise DevopsSyncError(
            f"MCP 配置 {cfg} 中没有 mcpServers 映射（顶层需为 {{\"mcpServers\": {{...}}}}）。"
        )
    return {str(k): v for k, v in servers.items() if isinstance(v, dict)}


def _devops_server_names(servers: dict[str, dict]) -> list[str]:
    """候选服务器：以 ``devops-mcp`` 开头的条目 + ``devops-mcp-agile``（旧脚本名兼容）。"""
    return sorted(
        name for name in servers
        if name.startswith(MCP_SERVER_PREFIX) or name == LEGACY_MCP_SERVER_NAME
    )


def resolve_mcp_server(
    *,
    server_name: str | None = None,
    mcp_config: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> McpServer:
    """解析 DevOps MCP 服务器（**不硬编码服务器名**——本机配置是 sit/uat 而非 agile）。

    配置顺序（都不命中则点名列出 mcp.json 现有服务器名，不静默择一）：
    1. ``devops-sync.yaml`` 的可选 ``mcp_server:`` / ``ATLAS_DEVOPS_MCP_SERVER``（显式名字）；
    2. ``ATLAS_DEVOPS_MCP_URL``（显式地址，覆盖文件探测）；
    3. ``mcp.json`` 中 ``devops-mcp*`` 前缀条目——**唯一**则用之，**多个**则报错要求显式指定。
    """
    env = env if env is not None else os.environ
    name = (server_name or env.get("ATLAS_DEVOPS_MCP_SERVER", "")).strip()
    url = env.get("ATLAS_DEVOPS_MCP_URL", "").strip()

    if name:
        servers = load_mcp_servers(mcp_config)
        entry = servers.get(name)
        if not isinstance(entry, dict) or not entry.get("url"):
            existing = ", ".join(_devops_server_names(servers)) or "(无 devops-mcp* 条目)"
            raise DevopsSyncError(
                f"mcp.json 中没有可用的服务器 {name!r}（需含 url 字段）。"
                f"\n现有 devops 相关服务器：{existing}"
            )
        return _server_from_entry(name, entry)

    if url:      # 显式地址优先于文件探测（用户明确覆盖）
        mkt = env.get("ATLAS_DEVOPS_MIDEA_MKT_ID", "").strip()
        return McpServer(name="<ATLAS_DEVOPS_MCP_URL>", url=url,
                         midea_mkt_id=int(mkt) if mkt.isdigit() else None)

    servers = load_mcp_servers(mcp_config)
    candidates = _devops_server_names(servers)
    if len(candidates) == 1:
        return _server_from_entry(candidates[0], servers[candidates[0]])
    if len(candidates) > 1:
        raise DevopsSyncError(
            "mcp.json 中发现多个 devops MCP 服务器，无法自动判定该用哪个："
            f"{', '.join(candidates)}。"
            "\n请在 devops-sync.yaml 中设 mcp_server: <name>（或设 ATLAS_DEVOPS_MCP_SERVER），"
            "不要指望工具替你猜环境。"
        )

    existing = ", ".join(servers) or "(mcpServers 为空)"
    raise DevopsSyncError(
        "未找到可用的 DevOps MCP 服务器。"
        f"\n  mcp.json 现有服务器：{existing}"
        "\n请在 devops-sync.yaml 设 mcp_server: <name>（如 devops-mcp-sit），"
        "或设 ATLAS_DEVOPS_MCP_SERVER / ATLAS_DEVOPS_MCP_URL。"
    )


def _server_from_entry(name: str, entry: dict) -> McpServer:
    mkt = entry.get("midea_mkt_id")
    try:
        mkt_id = int(mkt) if mkt is not None and str(mkt).strip() != "" else None
    except (TypeError, ValueError):
        mkt_id = None
    return McpServer(
        name=name,
        url=str(entry.get("url", "")),
        midea_mkt_id=mkt_id,
        type=str(entry.get("type", "streamable_http")),
    )


def resolve_mcp_url(
    *,
    explicit: str | None = None,
    server_name: str | None = None,
    mcp_config: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """地址便捷入口（保留旧签名语义）。``explicit`` 最优先，否则走 resolve_mcp_server。"""
    if explicit:
        return explicit
    return resolve_mcp_server(
        server_name=server_name, mcp_config=mcp_config, env=env
    ).url


# ══════════════════════════════════════════════════════════════════════════
# MCP 响应解析（踩坑 #5 中文乱码 / #6 纯 JSON / SSE）
# ══════════════════════════════════════════════════════════════════════════

def repair_mojibake(text: str) -> str:
    """SSE 返回的 UTF-8 常被当作单字节编码解码，需 ``encode(<单字节>).decode('utf-8')`` 修复。

    两级口径（2026-11-24 补第二级）：先按 `latin-1`（原实现，无行为变化）；**编码失败**时才试
    `cp1252`。为什么需要第二级：Windows 侧把 UTF-8 字节按 cp1252 解出的形态里，0x80–0x9F
    区间被映射到 `‹`(U+2039)/`•`(U+2022)/`”`(U+201D) 这类 **>0xFF** 的码位——`latin-1`
    `encode` 必失败，旧实现于是**原样返回乱码**，标题/步骤名就这么带着乱码进了 DevOps
    （静默写坏数据，与「静默丢弃」同族：不报错、只是内容是错的）。

    安全性由「双向失败即原文」兜住，实测（`tests/test_devops_sync.py` 钉住）：
    - 正常中文/emoji：`latin-1` 与 `cp1252` 都编码失败 → 原文；
    - 合法西欧文本（`München`/`naïve`）：`latin-1` 可编码但 utf-8 解码失败，
      `cp1252` 亦然 → 原文（**不会二次损坏**）；
    - 纯 ASCII：两条路径都往返恒等。
    """
    for encoding in ("latin-1", "cp1252"):
        try:
            return text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def _content_text(data: dict) -> tuple[bool, str]:
    """从 JSON-RPC 响应体提取 (is_error, text)，逐条 content 修中文乱码。"""
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        result = {}
    is_error = bool(result.get("isError", False))
    text = ""
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                text += repair_mojibake(str(item["text"]))
    return is_error, text


def parse_mcp_response(raw: str) -> tuple[bool, str]:
    """解析 MCP 响应 → ``(is_error, text)``。支持 SSE 与纯 JSON（踩坑 #6）。

    1) SSE：逐行找 ``data:`` 前缀，解析其 JSON-RPC 体；
    2) 回退：整段按纯 JSON 解析（无 ``data:`` 前缀的服务器）；
    3) 回退：JSON-RPC 错误体（``error`` 键）→ ``(True, 错误 JSON 文本)``。
    """
    for line in str(raw).splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            try:
                return _content_text(json.loads(payload))
            except json.JSONDecodeError:
                continue

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict):
        if "error" in data:
            return True, json.dumps(data["error"], ensure_ascii=False)
        return _content_text(data)

    return True, "No SSE data found"


def extract_test_plan_id(text: str) -> str | None:
    """从响应文本中提取 testPlanId（guide/脚本口径：``testPlanId["\\s:]+(VALZ\\d+)``）。"""
    match = re.search(r'testPlanId["\s:]+(VALZ\d+)', text)
    if match:
        return match.group(1)
    match = re.search(r'"testPlanId"\s*:\s*"(VALZ\d+)"', text)
    return match.group(1) if match else None


def extract_directory_id(text: str) -> str | None:
    """从 ``resolve_test_directory_by_path`` 响应中提取 directoryId。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict):
        did = data.get("directoryId")
        if did:
            return str(did)
    match = re.search(r'"directoryId"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else None


def extract_imported_ids(text: str, count: int) -> list[str]:
    """从 ``import_testcases_to_library`` 响应中按导入顺序提取 testCaseId 列表。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict):
        idx_map = data.get("idxToTestId")
        if isinstance(idx_map, dict) and idx_map:
            return [str(idx_map[str(i)]) for i in range(count) if str(i) in idx_map]
        id_list = data.get("testIdList")
        if isinstance(id_list, list) and id_list:
            return [str(x) for x in id_list]
    # 回退：正则去重保序
    return list(dict.fromkeys(re.findall(r"VALZ\d+", text)))


# ══════════════════════════════════════════════════════════════════════════
# MCP JSON-RPC 客户端（requests 为唯一运行时依赖）
# ══════════════════════════════════════════════════════════════════════════

class McpClient:
    """极简 MCP streamable_http 客户端。

    只做 pure JSON-RPC（``initialize`` + ``tools/call``），不引第三方 MCP SDK（红线 #4）。
    ``call_tool`` 返回 ``(is_error, text)``——与 fake 客户端同一契约，便于离线注入。

    真实连通性说明：本机 devops-mcp-sit/uat 实测返回 ``HTTP 400 Invalid Access``（官方
    ``ws mcp list`` 同错，网络/路径正常）——所以真实路径只保证**失败时点名归因**：
    401/403 → 认证明文提示；400 → 提示 key/midea_mkt_id 可能失效；网络错误/非 JSON →
    点名服务器与净化后 URL，绝不裸 traceback。
    """

    def __init__(self, url: str, *, timeout: float = 60.0, session: Any = None,
                 server_name: str = "", midea_mkt_id: int | None = None,
                 send_midea_mkt_id: bool = True):
        import requests  # 局部 import：模块本身可在无 requests 的纯解析场景被导入

        self.url = url
        self.server_name = server_name
        self.timeout = timeout
        self._session = session or requests.Session()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # midea_mkt_id 可配置透传（默认开；sit=505/uat=528 来自 mcp.json，绝不写死引擎）
        if send_midea_mkt_id and midea_mkt_id is not None:
            headers[MKT_ID_HEADER] = str(midea_mkt_id)
        self._session.headers.update(headers)
        self.session_id: str | None = None

    @classmethod
    def from_server(cls, server: McpServer, *, send_midea_mkt_id: bool = True,
                    timeout: float = 60.0, session: Any = None) -> "McpClient":
        return cls(server.url, timeout=timeout, session=session,
                   server_name=server.name, midea_mkt_id=server.midea_mkt_id,
                   send_midea_mkt_id=send_midea_mkt_id)

    def _label(self) -> str:
        name = self.server_name or "(未命名)"
        return f"{name} @ {sanitize_url(self.url)}"

    def _post(self, method: str, params: dict) -> str:
        import requests

        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4())[:8],
            "method": method,
            "params": params,
        }
        try:
            resp = self._session.post(self.url, json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DevopsSyncError(
                f"连接 MCP 服务器失败（{self._label()}）：{type(exc).__name__}: {exc}"
                "\n请检查网络是否可达、mcp.json 中 url 是否正确。"
            ) from exc
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session.headers["mcp-session-id"] = sid
            self.session_id = sid
        if resp.status_code in (401, 403):
            raise DevopsSyncError(
                f"MCP 认证失败（HTTP {resp.status_code}，{self._label()}）。"
                f"\n请检查 mcp.json 中该 devops-mcp-* 服务器的 key 是否有效/未过期；"
                "更新方式：DevOps 平台 → 个人设置 → Token/Key 管理 → 生成新 key → 替换 url 的 key 参数。"
            )
        if resp.status_code == 400:
            raise DevopsSyncError(
                f"MCP 请求被拒（HTTP 400，{self._label()}）：{resp.text[:200]}"
                "\n经验：devops-mcp-sit/uat 的 400 Invalid Access 多为 url 上的 key 失效/"
                "无权限，或 midea_mkt_id（market id）与目标环境不匹配。请核对 mcp.json 该条目。"
            )
        if resp.status_code != 200:
            raise DevopsSyncError(
                f"MCP 请求失败（HTTP {resp.status_code}，{self._label()}）：{resp.text[:200]}"
            )
        return resp.text

    def initialize(self, *, protocol_version: str = "2025-03-26") -> None:
        text = self._post(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "atlas", "version": "1.0"},
            },
        )
        # 初始化响应必须是可解析的 JSON-RPC；HTML 错误页等非 JSON 立即点名（不漂到后续工具调用）
        is_err, detail = parse_mcp_response(text)
        if detail == "No SSE data found" or (is_err and not detail):
            raise DevopsSyncError(
                f"MCP initialize 响应无法解析（{self._label()}）：{text[:200]}"
                "\n服务器返回的可能不是 JSON-RPC（HTML 错误页/网关拦截）。"
            )

    def call_tool(self, name: str, arguments: dict) -> tuple[bool, str]:
        raw = self._post("tools/call", {"name": name, "arguments": arguments})
        is_err, text = parse_mcp_response(raw)
        if text == "No SSE data found":
            raise DevopsSyncError(
                f"MCP 工具 {name} 响应无法解析（{self._label()}）：{raw[:200]}"
                "\n服务器返回的可能不是 JSON-RPC/SSE（HTML 错误页/网关拦截）。"
            )
        return is_err, text


# ══════════════════════════════════════════════════════════════════════════
# Allure result → DevOps 用例映射（C-D2，guide §2）
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class DevopsCase:
    """单条 Allure result 映射后的 DevOps 用例（去重前/后统一形态）。"""

    title: str
    priority: str               # P0/P1/P2
    steps: list[dict]           # [{testStep, testExpectation}]
    exec_result: int            # 1/2/3
    status: str                 # Allure status
    full_name: str
    start: int
    suite: str
    existing_devops_id: str | None = None
    new_devops_id: str | None = None


def _labels_dict(result: dict) -> dict[str, str]:
    """``labels``（[{name,value}]）→ {name: value}，坏条目跳过（#127 同纪律）。"""
    labels = result.get("labels")
    out: dict[str, str] = {}
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, dict) and item.get("name") is not None:
                out[str(item["name"])] = str(item.get("value", ""))
    return out


_ENV_LIKE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def _looks_like_title(text: str) -> bool:
    """``subSuite`` 是否像「用例标题」而非「环境码」。

    两类生产者的约定不同：
    - UI AUTO TEST（allure-pytest）：``subSuite`` = 中文用例标题（含空白/中文）；
    - Atlas 直出（allure_report）：``subSuite`` = env（如 ``sit``），标题在 ``name``。

    标题特征：含空白或非 ASCII（中文等）；环境码特征：纯小写 ASCII 标识符。
    """
    return bool(re.search(r"[\s\u4e00-\u9fff]", text)) or not _ENV_LIKE_RE.match(text)


def extract_title(result: dict) -> str:
    """从 Allure result 提取用例标题（兼容 UI AUTO TEST 与 Atlas 两种产物）。

    优先级：
    1) 显式 ``labels[atlas.case_title]``——生产者声明，最可靠；
    2) ``labels[subSuite]``——**仅当它像标题时**（去 ``x - y`` 业务前缀）；
       Atlas 直出的 ``subSuite=env``（``sit``）不像标题 → 跳过，避免把环境当用例名；
    3) ``description`` 第一行——去 ``TC\\d+ -`` 前缀；
    4) ``name``——Atlas 约定 ``name`` 即用例标题，去 ``test_tcN_`` 前缀/下划线。
    均去除末尾中英文句号；四级都空 → 空串。
    """
    labels = _labels_dict(result)

    explicit = labels.get("atlas.case_title", "").strip()
    if explicit:
        return re.sub(r"[\u3002.]$", "", explicit).strip()

    sub_suite = labels.get("subSuite", "").strip()
    if sub_suite and _looks_like_title(sub_suite):
        title = sub_suite
        if " - " in title:
            title = title.rsplit(" - ", 1)[-1].strip()
        title = re.sub(r"[\u3002.]$", "", title).strip()
        if title:
            return title

    desc = result.get("description") or ""
    first_line = str(desc).strip().split("\n")[0].strip()
    if first_line:
        title = re.sub(r"^TC\d+\s*[-\u2013]+\s*", "", first_line).strip()
        title = re.sub(r"[\u3002.]$", "", title).strip()
        if title:
            return title

    name = str(result.get("name", ""))
    title = re.sub(r"^test_tc\d+_", "", name, flags=re.IGNORECASE)
    title = title.replace("_", " ").strip()
    return re.sub(r"[\u3002.]$", "", title).strip()


def priority_from_labels(labels: dict[str, str]) -> str:
    return PRIORITY_MAP.get(labels.get("severity", ""), "P1")


def steps_from_result(result: dict) -> list[dict]:
    """``result.steps[]`` → [{testStep, testExpectation}]（guide §2.2）。

    testStep 去 ``[步骤N]``/``[断言N]`` 前缀；testExpectation 取 ``断言：`` 后文字，否则「操作成功」。
    无有效步骤 → 兜底一条「执行测试 / 操作成功」（与脚本一致）。
    """
    steps: list[dict] = []
    raw_steps = result.get("steps")
    if isinstance(raw_steps, list):
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            name = str(step.get("name", ""))
            test_step = re.sub(r"^\[(?:步骤|断言)\d+\]\s*", "", name).strip()
            if "断言：" in name:
                expectation = name.split("断言：", 1)[1].strip()
            else:
                expectation = "操作成功"
            if test_step:
                steps.append({"testStep": test_step, "testExpectation": expectation})
    if not steps:
        steps = [{"testStep": "执行测试", "testExpectation": "操作成功"}]
    return steps


def parse_devops_case(result: dict) -> DevopsCase:
    """单条 Allure result → DevopsCase（字段映射严格按 guide §2）。"""
    labels = _labels_dict(result)
    status = str(result.get("status", "broken") or "broken")
    try:
        start = int(result.get("start", 0) or 0)
    except (TypeError, ValueError):
        start = 0
    return DevopsCase(
        title=extract_title(result),
        priority=priority_from_labels(labels),
        steps=steps_from_result(result),
        exec_result=EXEC_RESULT_MAP.get(status, 2),
        status=status,
        full_name=str(result.get("fullName", "")),
        start=start,
        suite=labels.get("suite", ""),
    )


def _read_raw_results(results_dir: str | Path) -> tuple[list[dict], int]:
    """读 ``*-result.json``，返回 (原始 result dict 列表, 跳过坏文件数)。

    #131/#112 读侧容错：坏字节 replace 回落、坏 JSON/非对象跳过计数，绝不整份崩。
    目录不存在 → DevopsSyncError（rc 2，先跑测试生成结果）。
    """
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        raise DevopsSyncError(
            f"Allure 结果集目录不存在：{results_dir}。\n"
            "请先运行测试生成 reports/allure-results/*-result.json（atlas run + atlas allure）。"
        )
    raws: list[dict] = []
    skipped = 0
    for fpath in sorted(results_dir.glob("*-result.json")):
        try:
            text = fpath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
        except OSError:
            skipped += 1
            continue
        try:
            data = json.loads(text)
        except Exception:
            skipped += 1
            continue
        if not isinstance(data, dict):
            skipped += 1
            continue
        raws.append(data)
    return raws, skipped


def load_cases(
    results_dir: str | Path,
    *,
    suite_filter: str | None = None,
    known_ids: dict[str, str] | None = None,
) -> dict:
    """解析结果集 → 去重后的用例列表（C-D2）。

    - ``suite_filter``：只保留 ``labels[suite] == suite_filter`` 的结果文件；
    - **去重**：同一 title 多次运行堆积时，取 ``start`` 最大（最新执行）的一条；
    - ``known_ids``：可选 ``{title: devops_case_id}``——已同步过的用例直接复用 ID，
      不重复导入（对应脚本的 case.yaml ``devops_case_id`` 查重，此处与文件系统解耦）。

    返回 dict：``cases``(list[DevopsCase]) / ``duplicates`` / ``parsed`` /
    ``skipped_files`` / ``suite_label``。
    """
    raws, skipped = _read_raw_results(results_dir)

    by_title: dict[str, list[DevopsCase]] = {}
    parsed = 0
    suite_label = ""
    for raw in raws:
        labels = _labels_dict(raw)
        suite_val = labels.get("suite", "")
        if suite_filter and suite_val != suite_filter:
            continue
        title = extract_title(raw)
        if not title:
            continue
        parsed += 1
        by_title.setdefault(title, []).append(parse_devops_case(raw))
        if not suite_label:
            parent = labels.get("parentSuite", "")
            sub = labels.get("subSuite", "")
            if parent:
                suite_label = parent
            elif " - " in sub:
                suite_label = sub.split(" - ", 1)[0].strip()

    cases: list[DevopsCase] = []
    duplicates: list[dict] = []
    for title, runs in by_title.items():
        latest = max(runs, key=lambda c: c.start)
        if len(runs) > 1:
            duplicates.append(
                {
                    "title": title,
                    "kept_start": latest.start,
                    "dropped": [
                        {"start": c.start, "status": c.status}
                        for c in runs
                        if c is not latest
                    ],
                }
            )
        cases.append(latest)

    if known_ids:
        for case in cases:
            existing = known_ids.get(case.title)
            if existing:
                case.existing_devops_id = str(existing)

    return {
        "cases": cases,
        "duplicates": duplicates,
        "parsed": parsed,
        "skipped_files": skipped,
        "suite_label": suite_label,
    }


def classify_results(
    results_dir: str | Path, *, suite_filter: str | None = None
) -> dict:
    """复用 ``allure_analysis.load_allure_results`` 的分类视图（passed/failed/... 计数）。

    本模块的 payload 映射需要 ``steps``/``start``（``AllureFinding`` 刻意不带——它是分类
    专用视图），故用例映射走 ``load_cases`` 自读原始 JSON；而状态分类仍复用同一读侧实现，
    避免第二份口径（#92）。``suite_filter`` 与 ``load_cases`` 同口径过滤，保证同一 suite
    的「解析 N 条」与「通过/失败/阻塞」自洽（否则汇总数把别的 suite 混进来=假绿）。
    目录缺失时 ``load_allure_results`` 抛 FileNotFoundError，调用方应先跑 ``load_cases``
    （其消息更友好）。
    """
    findings = load_allure_results(Path(results_dir))
    counts = {"total": 0, "passed": 0, "failed": 0, "broken": 0,
              "skipped": 0, "other": 0}
    for finding in findings:
        if suite_filter and finding.labels.get("suite", "") != suite_filter:
            continue
        counts["total"] += 1
        if finding.status in counts:
            counts[finding.status] += 1
        else:
            counts["other"] += 1
    return counts


# ══════════════════════════════════════════════════════════════════════════
# 状态文件（C-D4 / C-D4b）
# ══════════════════════════════════════════════════════════════════════════

def state_path(root: str | Path) -> Path:
    return Path(root) / STATE_FILENAME


def suite_state_key(suite_filter: str | None, run_key: str) -> str:
    """状态键格式 ``{suite}_{run_key}``（guide §5；无 suite 用 ``all``）。"""
    return f"{suite_filter or 'all'}_{run_key}"


def resolve_run_key(root: str | Path, *, now: datetime | None = None) -> str:
    """推导 run_key：优先 ``reports/allure-history`` 下最新的时间戳目录名，否则用当前时间。

    格式 ``yyyyMMdd_HHmmss``（guide C-D4）。
    """
    history = Path(root).joinpath(*HISTORY_SUBPATH)
    if history.is_dir():
        dirs = sorted(d.name for d in history.iterdir() if d.is_dir())
        if dirs:
            return dirs[-1]
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def load_state(root: str | Path) -> dict:
    """读 ``devops-sync-state.yaml``；不存在 → {}（首次同步）。

    坏字节 replace 回落（#112）；坏 YAML / 顶层非映射 → DevopsSyncError 点名文件——
    静默当空表会导致重复建计划（比慢更坏），故不降级。
    """
    path = state_path(root)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DevopsSyncError(f"无法读取状态文件 {path}：{exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DevopsSyncError(f"状态文件 {path} 不是合法 YAML：{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise DevopsSyncError(
            f"状态文件 {path} 顶层必须是映射，实际是 {type(data).__name__}"
        )
    return data


def save_state(root: str | Path, state: dict) -> None:
    """原子写 ``devops-sync-state.yaml``（先写 .tmp 再 os.replace，防半写）。

    ``allow_unicode`` 保留中文、``sort_keys=False`` 保留插入顺序（与脚本产出一致）。
    """
    path = state_path(root)
    tmp = path.with_name(path.name + ".tmp")
    text = yaml.dump(
        state, allow_unicode=True, default_flow_style=False, sort_keys=False
    )
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ══════════════════════════════════════════════════════════════════════════
# Payload 构造器（C-D3 / C-D4 / C-D5）
# ══════════════════════════════════════════════════════════════════════════

def clean_case_title(title: str) -> str:
    """导入前清洗标题：去掉 ``-`` ``（）`` ``()`` 等会静默失败的特殊字符（踩坑 #1）。"""
    return re.sub(r"[-（）()]", "", title)


def build_directory_arguments(testcase_directory: str) -> dict:
    """``resolve_test_directory_by_path`` 的 arguments。"""
    return {"directoryPath": testcase_directory}


def build_import_arguments(
    config: DevopsSyncConfig, directory_id: str, cases: list[DevopsCase]
) -> dict:
    """``import_testcases_to_library`` 的 arguments（嵌套 request + clientRequestId）。

    executionMethod 固定 ``UI自然语言测试``（踩坑 #1）；title 已清洗（踩坑 #3 静默 invalidDataList）。
    """
    test_case_list = [
        {
            "title": clean_case_title(case.title),
            "priority": case.priority,
            "executionMethod": EXECUTION_METHOD,  # 必填，固定不变
            "steps": case.steps,
        }
        for case in cases
    ]
    return {
        "request": {
            "systemId": config.system_id,
            "directoryId": directory_id,
            "testCaseList": test_case_list,
        },
        "clientRequestId": str(uuid.uuid4()),
    }


def build_create_plan_arguments(config: DevopsSyncConfig, title: str) -> dict:
    """``create_test_plan`` 的 arguments（executionMethod 非 executeType，踩坑 #2）。"""
    return {
        "request": {
            "title": title,
            "systemId": config.system_id,
            "executionMethod": EXECUTION_METHOD,  # 必填，固定不变
            "isPicture": False,
            "testplanPolicy": 0,
        },
        "clientRequestId": str(uuid.uuid4()),
    }


def build_relate_arguments(
    config: DevopsSyncConfig, test_plan_id: str, pairs: list[tuple[DevopsCase, str]]
) -> dict:
    """``batch_relate_test_plan`` 的 arguments（必须嵌套 request，踩坑 #4）。"""
    return {
        "request": {
            "testPlanId": test_plan_id,
            "systemId": config.system_id,
            "action": RELATE_ACTION,
            "testCases": [
                {
                    "testCaseId": tc_id,
                    "execResult": case.exec_result,
                    "execUser": config.exec_user,
                }
                for case, tc_id in pairs
            ],
        },
        "clientRequestId": str(uuid.uuid4()),
    }


def render_plan_title(
    template: str, suite_label: str, run_key: str
) -> str:
    """测试计划标题：``{suite_label}`` / ``{timestamp}`` 变量替换（guide §1）。"""
    return template.replace("{suite_label}", suite_label).replace("{timestamp}", run_key)


# ══════════════════════════════════════════════════════════════════════════
# 主流程（C-D1 ~ C-D5）
# ══════════════════════════════════════════════════════════════════════════

def _case_summary(case: DevopsCase) -> dict:
    return {
        "title": case.title,
        "priority": case.priority,
        "exec_result": case.exec_result,
        "status": case.status,
        "steps": len(case.steps),
        "full_name": case.full_name,
        "existing_devops_id": case.existing_devops_id,
        "new_devops_id": case.new_devops_id,
    }


def sync_plan(
    root: str | Path,
    *,
    suite_filter: str | None = None,
    execute: bool = False,
    client: Any = None,
    known_ids: dict[str, str] | None = None,
    config_path: str | Path | None = None,
    results_dir: str | Path | None = None,
    mcp_url: str | None = None,
    mcp_server: str | None = None,
    mcp_config: str | Path | None = None,
    now: datetime | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    """执行一次 DevOps 同步规划（C-D1~C-D5），返回结构化结果。

    **默认 dry-run**：``execute=False`` 时只解析、只构造 payload、**不发任何请求**；
    ``execute=True`` 时需 ``client``（或自动构造 ``McpClient``）真实调用 MCP。
    ``client`` 为 ``McpClient``-形状对象（``initialize()`` + ``call_tool(name, args)``）。

    服务器解析：``mcp_server`` 显式 > ``devops-sync.yaml`` 的 ``mcp_server`` >
    ``ATLAS_DEVOPS_MCP_SERVER`` > mcp.json 中唯一的 ``devops-mcp*`` 条目；
    多个候选时要求显式指定（不静默择一）。``mcp_url`` 显式给地址时跳过服务器解析。

    返回 dict 含：``dry_run`` / ``executed`` / 用例摘要 / 去重项 / 四步 payload 摘要 /
    导入与复用计数 / 计划 ID / 关联条数 / 每步执行记录 ``steps``。
    用户可修复错误一律抛 ``DevopsSyncError``（rc 2）。
    """
    root = Path(root)
    cfg = load_config(root, path=config_path)
    directory = cfg.directory_for(suite_filter)

    rdir = Path(results_dir) if results_dir is not None else root.joinpath(*RESULTS_SUBPATH)
    loaded = load_cases(rdir, suite_filter=suite_filter, known_ids=known_ids)
    cases: list[DevopsCase] = loaded["cases"]
    if not cases:
        raise DevopsSyncError(
            f"结果集 {rdir} 中无可用用例（suite_filter={suite_filter or '(全部)'}）。\n"
            "请确认测试已执行且 allure-results 内有 *-result.json。"
        )

    allure_summary = classify_results(rdir, suite_filter=suite_filter)
    suite_label = loaded["suite_label"] or suite_filter or "自动化测试"
    run_key = resolve_run_key(root, now=now)
    state_key = suite_state_key(suite_filter, run_key)
    testplan_title = render_plan_title(cfg.testplan_title_template, suite_label, run_key)

    state = load_state(root)
    plan_reused = bool(state.get(state_key, {}).get("testPlanId")) if isinstance(
        state.get(state_key), dict
    ) else False
    test_plan_id: str | None = (
        state[state_key]["testPlanId"] if plan_reused else None
    )

    reused_cases = [c for c in cases if c.existing_devops_id]
    to_import = [c for c in cases if not c.existing_devops_id]

    # ── payload 构造（dry-run 也构造，供人工核对）──────────────────────
    payloads: dict[str, Any] = {}
    if to_import:
        payloads["resolve_test_directory_by_path"] = build_directory_arguments(directory)
        payloads["import_testcases_to_library"] = build_import_arguments(
            cfg, "<directoryId from resolve_test_directory_by_path>", to_import
        )
    if not plan_reused:
        payloads["create_test_plan"] = build_create_plan_arguments(cfg, testplan_title)
    relate_placeholder = test_plan_id or "<testPlanId from create_test_plan>"
    pairs_placeholder = [
        (c, c.existing_devops_id or "<testCaseId>") for c in cases
    ]
    payloads["batch_relate_test_plan"] = build_relate_arguments(
        cfg, relate_placeholder, pairs_placeholder
    )

    steps: list[dict] = []

    def _record(name: str, ok: bool | None, detail: str = "") -> None:
        steps.append({"step": name, "ok": ok, "detail": detail})

    if not execute:
        _record("dry_run", None, "未发送任何请求（默认 dry-run；真实调用需 execute=True）")
        # dry-run 也尽力解析目标服务器（供人工核对），解析失败只记一行不阻塞
        dry_url, dry_name = "", ""
        try:
            if mcp_url:
                dry_url, dry_name = mcp_url, (mcp_server or cfg.mcp_server or "<explicit-url>")
            else:
                srv = resolve_mcp_server(
                    server_name=mcp_server or cfg.mcp_server or None,
                    mcp_config=mcp_config, env=env,
                )
                dry_url, dry_name = srv.url, srv.name
            _record("resolve_mcp_server", True, f"server={dry_name} url={sanitize_url(dry_url)}")
        except DevopsSyncError as exc:
            _record("resolve_mcp_server", False, str(exc).splitlines()[0])
        return _result(
            root=root, cfg=cfg, dry_run=True, executed=False, suite_filter=suite_filter,
            suite_label=suite_label, run_key=run_key, state_key=state_key,
            testcase_directory=directory, cases=cases, duplicates=loaded["duplicates"],
            skipped_files=loaded["skipped_files"], allure_summary=allure_summary,
            testplan_title=testplan_title, test_plan_id=test_plan_id,
            plan_reused=plan_reused, reused=len(reused_cases),
            imported=0, relate_count=0, payloads=payloads, steps=steps,
            mcp_url=dry_url, mcp_server_name=dry_name,
        )

    # ── 真实执行 ───────────────────────────────────────────────────────
    # mcp_url 显式地址优先；否则解析服务器条目（名称可配置，不硬编码 agile）
    server: McpServer | None = None
    if mcp_url:
        url = mcp_url
        server_name = mcp_server or cfg.mcp_server or "<explicit-url>"
    else:
        server = resolve_mcp_server(
            server_name=mcp_server or cfg.mcp_server or None,
            mcp_config=mcp_config, env=env,
        )
        url, server_name = server.url, server.name
    if client is None:
        client = (McpClient.from_server(server, send_midea_mkt_id=cfg.send_midea_mkt_id)
                  if server is not None else McpClient(url, server_name=server_name))
    client.initialize()
    _record("initialize", True, f"server={server_name} url={sanitize_url(url)}")

    directory_id = "<directoryId>"
    imported = 0
    if to_import:
        is_err, dir_text = client.call_tool(
            "resolve_test_directory_by_path",
            build_directory_arguments(directory),
        )
        directory_id = extract_directory_id(dir_text) or ""
        if is_err or not directory_id:
            _record("resolve_test_directory_by_path", False, dir_text[:200])
            raise DevopsSyncError(
                f"解析用例库目录失败：{dir_text[:200]}\n"
                f"请检查 system_name 大小写与目录路径格式：「{directory}」。"
            )
        _record("resolve_test_directory_by_path", True, f"directoryId={directory_id}")

        import_args = build_import_arguments(cfg, directory_id, to_import)
        payloads["import_testcases_to_library"] = import_args
        is_err, import_text = client.call_tool(
            "import_testcases_to_library", import_args
        )
        new_ids = extract_imported_ids(import_text, len(to_import))
        for i, case in enumerate(to_import):
            case.new_devops_id = new_ids[i] if i < len(new_ids) else None
            if case.new_devops_id:
                imported += 1
        _record(
            "import_testcases_to_library", not is_err,
            f"新建 {imported}/{len(to_import)} 条；{import_text[:120]}",
        )

    if not plan_reused:
        create_args = build_create_plan_arguments(cfg, testplan_title)
        payloads["create_test_plan"] = create_args
        is_err, create_text = client.call_tool("create_test_plan", create_args)
        test_plan_id = extract_test_plan_id(create_text)
        if is_err or not test_plan_id:
            _record("create_test_plan", False, create_text[:200])
            raise DevopsSyncError(f"创建测试计划失败：{create_text[:200]}")
        _record("create_test_plan", True, f"testPlanId={test_plan_id}")
        # C-D4b：创建成功立即回写 state（防重复建计划）
        state[state_key] = {
            "testPlanId": test_plan_id,
            "testPlanTitle": testplan_title,
            "syncTime": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "system_id": cfg.system_id,
        }
        save_state(root, state)
        _record("save_state", True, f"key={state_key}")
    else:
        _record("create_test_plan", True, f"复用已有 testPlanId={test_plan_id}")

    pairs = [(c, c.existing_devops_id or c.new_devops_id) for c in cases]
    pairs = [(c, tid) for c, tid in pairs if tid]
    relate_args = build_relate_arguments(cfg, test_plan_id or "", pairs)
    payloads["batch_relate_test_plan"] = relate_args
    is_err, relate_text = client.call_tool("batch_relate_test_plan", relate_args)
    _record("batch_relate_test_plan", not is_err, relate_text[:200])

    return _result(
        root=root, cfg=cfg, dry_run=False, executed=True, suite_filter=suite_filter,
        suite_label=suite_label, run_key=run_key, state_key=state_key,
        testcase_directory=directory, cases=cases, duplicates=loaded["duplicates"],
        skipped_files=loaded["skipped_files"], allure_summary=allure_summary,
        testplan_title=testplan_title, test_plan_id=test_plan_id,
        plan_reused=plan_reused, reused=len(reused_cases),
        imported=imported, relate_count=len(pairs), payloads=payloads, steps=steps,
        mcp_url=url, mcp_server_name=server_name,
    )


def _result(
    *,
    root: Path,
    cfg: DevopsSyncConfig,
    dry_run: bool,
    executed: bool,
    suite_filter: str | None,
    suite_label: str,
    run_key: str,
    state_key: str,
    testcase_directory: str,
    cases: list[DevopsCase],
    duplicates: list[dict],
    skipped_files: int,
    allure_summary: dict,
    testplan_title: str,
    test_plan_id: str | None,
    plan_reused: bool,
    reused: int,
    imported: int,
    relate_count: int,
    payloads: dict,
    steps: list[dict],
    mcp_url: str | None,
    mcp_server_name: str = "",
) -> dict:
    passed = sum(1 for c in cases if c.exec_result == 1)
    blocked = sum(1 for c in cases if c.exec_result == 3)
    failed = len(cases) - passed - blocked
    # mcp_url 展示一律净化（可能带 ?token）；仅在真实执行时才解析，dry-run 为 None
    display_url = sanitize_url(mcp_url) if mcp_url else ""
    return {
        "dry_run": dry_run,
        "executed": executed,
        "root": str(root),
        "mcp_url": display_url,
        "mcp_server": mcp_server_name,
        "dry_run_note": (
            "默认 dry-run：未发送任何请求；加 execute=True 真实同步"
            if dry_run else ""
        ),
        "suite_filter": suite_filter,
        "suite_label": suite_label,
        "run_key": run_key,
        "state_key": state_key,
        "system_id": cfg.system_id,
        "system_name": cfg.system_name,
        "exec_user": cfg.exec_user,
        "testcase_directory": testcase_directory,
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "blocked": blocked,
        "allure_summary": allure_summary,
        "duplicates": duplicates,
        "skipped_files": skipped_files,
        "cases": [_case_summary(c) for c in cases],
        "testplan_title": testplan_title,
        "test_plan_id": test_plan_id,
        "plan_reused": plan_reused,
        "imported_new": imported,
        "reused": reused,
        "relate_count": relate_count,
        "payloads": payloads,
        "steps": steps,
    }


def format_sync_summary(result: dict) -> str:
    """人读版摘要（CLI 打印；标记用 ASCII 以免 GBK 控制台崩——Atlas 历次教训）。"""
    lines = [
        "【DevOps 同步摘要】",
        f"  项目根：     {result['root']}",
        f"  suite：      {result['suite_filter'] or '(全部)'}",
        f"  run_key：    {result['run_key']}（state key: {result['state_key']}）",
        f"  系统：       {result['system_name']}（{result['system_id']}）",
        f"  用例库目录： {result['testcase_directory']}",
        f"  执行人：     {result['exec_user']}",
        f"  解析用例：   {result['total']} 条"
        f"（通过 {result['passed']} / 失败 {result['failed']} / 阻塞 {result['blocked']}）",
    ]
    if result["skipped_files"]:
        lines.append(f"  坏文件跳过： {result['skipped_files']}")
    if result["duplicates"]:
        lines.append(f"  去重丢弃：   {len(result['duplicates'])} 条（同标题取 start 最新）")
        for dup in result["duplicates"]:
            lines.append(
                f"    - {dup['title']}（保留 start={dup['kept_start']}，"
                f"丢 {len(dup['dropped'])} 条历史）"
            )
    lines.append(
        f"  导入用例：   新建 {result['imported_new']} 条 / 复用 {result['reused']} 条"
    )
    lines.append(
        f"  测试计划：   {result['testplan_title']} "
        f"(ID: {result['test_plan_id'] or '待创建'}"
        f"{'，复用' if result['plan_reused'] else ''})"
    )
    lines.append(f"  关联用例：   {result['relate_count']} 条")
    if result["mcp_url"] or result["mcp_server"]:
        lines.append(
            f"  MCP 服务器： {result['mcp_server'] or '(未指定)'}"
            f"{(' ' + result['mcp_url']) if result['mcp_url'] else ''}"
        )
    if result["dry_run"]:
        lines.append("")
        lines.append("  [DRY-RUN] 未发送任何请求。将发送的 payload：")
        for name in (
            "resolve_test_directory_by_path",
            "import_testcases_to_library",
            "create_test_plan",
            "batch_relate_test_plan",
        ):
            payload = result["payloads"].get(name)
            if payload is not None:
                lines.append(f"    - {name}: {json.dumps(payload, ensure_ascii=False)[:200]}")
    return "\n".join(lines)

"""凭据泄漏面单一判据（C4）：字面敏感头闸 + URL/异常文本净化。

**唯一实现**（M13「单一实现」/ #92「禁复制第二份判据」）：`scripts/import_jmeter.py`、
`src/atlas/generator/emit_flow.py`、`src/atlas/failures.py` 三处消费方只许调本模块，
不得各自再写一份名单或第二把净化刀。

名单口径：通用 HTTP 凭据词表，**不是**某业务的头清单（AGENTS 红线 #11「引擎不含业务
字面量」）——`isc-axios-sessionid` 这类业务前缀名靠 `"sessionid"` 子串命中，业务名本身
不进名单。

四条落点：
- **C4①** 迁移器/生成器产出的**字面**敏感头值 → `${ATLAS_HDR_<n>}` 占位；真实值永不进
  YAML/stdout/stderr（`D:/jmx` 实测 582 个 Header 元素全是字面 UUID，随时会泄）。
- **C4②** 异常/URL 文本净化：`sanitize_url` / `sanitize_text`，由 `failures` 单点派生，
  一处覆盖三条落盘路径（journal 草稿 evidence.text、agent 留痕 reason、
  `degraded_to_ui.reason`）。
- **C4-A**（评审必改）：`sanitize_url` **逐键命中才 redact**，非敏感业务参数
  （docNo/page/ou 等）原样保留——整段抹 query 会销毁追责证据（#85 证据面），并把漂移面
  从「敏感键值」放大到「全部 query 值」。fragment **分形态**：`/` 开头的路由型 fragment
  保留路由段、只逐键 redact 其 query（ICS 是 hash 路由 SPA，整抹等于把「在哪个页面」这条
  取证证据也抹掉）；其余形态维持整抹（`#access_token=…` 这类不透明令牌的典型形态）。
- **C4-B**（评审必改）：净化只服务**落盘**；**定性输入必须用净化前原文**
  （`failures.classify_failure` 走 raw 文本）。否则关键词恰好只出现在敏感 k=v 的值里
  （`?code=401`、`token=…timeout…`）时，净化把 SESSION 翻成 unknown——脱敏反而损归因，
  比泄露更隐蔽。
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"

# ── 名单（通用 HTTP 凭据词表）────────────────────────────────────────────
SENSITIVE_HEADER_TOKENS: tuple[str, ...] = (
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "token", "access_token", "accesstoken", "refreshtoken",
    "password", "passwd", "secret", "api-key", "apikey", "x-api-key",
    "sessionid", "session-id", "jsessionid", "xsrftoken", "x-csrf-token",
    "ticket", "x-auth",
)

# URL query / form 串的敏感键：**短词精确命中为主**（`key`/`code`/`state` 做子串会误伤
# `monkey`、`statusCode` 这类业务参数，与 C4-A「保留非敏感取证参数」同口径）；
# 长词做子串兜住 `x-access-token`、`isc-axios-sessionid` 这类带前缀的变体。
SENSITIVE_QUERY_KEYS: tuple[str, ...] = (
    "token", "access_token", "code", "ticket", "state", "password", "pwd",
    "secret", "jsessionid", "sessionid", "key", "apikey",
)
_QUERY_SUBSTRINGS: tuple[str, ...] = (
    "authorization", "cookie", "sessionid", "session-id", "token", "password",
    "passwd", "secret", "apikey", "api-key", "xsrftoken", "csrf", "ticket",
    "credential", "signature",
)

# 第二判据分支的键名「凭据语义」提示：名字不在名单里但语义是凭据，**且值是字面秘密**时
# 才拦——纯数字业务键不因「值像 hex」被误伤。
_CREDENTIAL_NAME_HINTS: tuple[str, ...] = (
    "auth", "key", "token", "secret", "cookie", "passw", "session", "ticket",
    "credential", "sid", "sign",
)

# ── 形态识别 ────────────────────────────────────────────────────────────
_UUID = re.compile(r"(?i)^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_JWT = re.compile(r"(?i)^ey[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+")
_HEX_RUN = re.compile(r"(?i)^[0-9a-f]{32,}$")
_B64_RUN = re.compile(r"(?i)^[0-9a-z+/=_-]{32,}$")
# 中日韩汉字区（U+4E00-U+9FFF）与空白：业务文案不是秘密 token。码位用 chr() 取而不是
# 把 CJK 区间的边界字符直接写进源码——X4 哨兵（tests/test_xplat_sentinels.py）要求
# src/scripts 的字符串字面量在 GBK 控制台可编，而 U+9FFF 恰好不在 GBK 覆盖内。
_CJK_SPACE = re.compile("[ \t\r\n\f\v]|[" + chr(0x4E00) + "-" + chr(0x9FFF) + "]")

# 已外部化的值（JMeter `${VAR}` / `${__P()}`、Atlas `${ENV}`）不是字面秘密：那是人审接
# env 的位，闸不重复告警（否则占位符被翻成占位符，接 env 指引反而丢了）。
_EXTERNALIZED = re.compile(r"\$\{[^}]*\}|__P\(")

_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>()\[\]{}]+")
# k=v：值段必须**非空**且不含 `<`——已净化过的 `=<redacted>` 不再二次匹配（幂等；否则
# sub 只吃掉 `key=` 半截，把 `<redacted>` 叠成 `<redacted><redacted>`）。
_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]{0,63})=([^&\s'\"<>]+)")
# `名: 值` 行（头形态）。键名类排除 `=` `"` `{` `[` `(` 与换行：URL（`URL=https://…`）与
# JSON 行都不走这条刀（JSON 由 _QUOTED_KV_RE 负责），业务取证文本不被整行吞掉。
_NAME_VALUE_LINE_RE = re.compile(r"(?m)^([ \t]*[^:=\"'{[(\r\n]{1,80}?)[ \t]*:[ \t]*([^\r\n]+)")
# JSON/报文里的 `"敏感键": "值"`：只擦值段，键名与结构保留（人审看得出是哪个字段坏的）。
_QUOTED_KV_RE = re.compile(r'"([A-Za-z0-9_.-]{1,80})"([ \t]*:[ \t]*)"([^"\\]*)"')
_PLACEHOLDER_RE = re.compile(r"\$\{[^{}]*\}")
_HDR_PLACEHOLDER_RE = re.compile(r"\$\{ATLAS_HDR_(\d+)\}")


def _text_of(value: object) -> str:
    """永不抛的字符串化（#89 定性器不得先于被定性对象死；净化入口同一纪律）。"""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


def is_externalized(value: object) -> bool:
    """值是否已是变量/占位符形态（`${...}` 或 `__P(`）——已外部化的值不是字面秘密。"""
    return bool(_EXTERNALIZED.search(_text_of(value)))


def looks_literal_secret(value: object) -> bool:
    """值形态启发式（确定可测、不猜）：字面且呈 UUID / JWT / ≥32 位 hex 或 base64 样连续串。

    恒 False：空串、纯数字流水号、中日韩文案、带空格的句子、已外部化占位符。
    """
    text = _text_of(value).strip()
    if not text or is_externalized(text):
        return False
    if _UUID.match(text) or _JWT.match(text):
        return True
    if _CJK_SPACE.search(text):
        # 带空白的是句子/报文、带中日韩汉字的是业务文案，都不是单个秘密 token
        return False
    if _HEX_RUN.match(text):
        return True
    # base64 样连续串要求「有熵」：大小写混排或含 +/= 且长度达标（纯数字流水号不算）
    if len(text) >= 32 and _B64_RUN.match(text):
        return text != text.lower() or any(ch in text for ch in "+/=")
    return False


def header_is_sensitive(name: object, value: object) -> bool:
    """敏感头判据（唯一闸口）：键名命中名单 ∨（值呈字面秘密 ∧ 键名有凭据语义）。

    已外部化值恒 False——`Authorization: Bearer ${tok}` 是人审接 env 的位，不重复告警。
    """
    text = _text_of(value)
    if is_externalized(text):
        return False
    lowered = _text_of(name).lower()
    if any(token in lowered for token in SENSITIVE_HEADER_TOKENS):
        return True
    return looks_literal_secret(text) and any(hint in lowered for hint in _CREDENTIAL_NAME_HINTS)


def query_key_is_sensitive(key: object) -> bool:
    """URL query / form 键的敏感判据（与头名单同一批词，短词只精确命中）。"""
    lowered = _text_of(key).lower()
    if lowered in SENSITIVE_QUERY_KEYS:
        return True
    return any(token in lowered for token in _QUERY_SUBSTRINGS)


def placeholder_for(index: int) -> str:
    """占位键形态：`${ATLAS_HDR_<n>}`——DSL 既有 `${...}` 插值承接，人审接 env 即可跑。"""
    return "${ATLAS_HDR_%d}" % int(index)


def placeholder_index(placeholder: object) -> int | None:
    """从占位串取回序号；不是本闸产出的占位符返回 None。"""
    match = _HDR_PLACEHOLDER_RE.search(_text_of(placeholder))
    return int(match.group(1)) if match else None


def _sanitize_query(query: str) -> str:
    """逐键命中才 redact（C4-A）：非敏感键值原样保留，顺序与重复键不重排。"""
    if not query:
        return query
    parts = []
    for pair in query.split("&"):
        key, sep, value = pair.partition("=")
        # 空值不 redact：`code=` 这种残段没有可遮蔽的东西。漏了这条，第二次净化会在
        # 已有 `<redacted>` 后再叠一个（URL 刀把 `<` 当终止符，匹配到 `code=` 就补一发），
        # 幂等性当场破——报告里出现 `<redacted><redacted>` 是噪音，也是「净化器自己坏了」
        # 的假象。
        if sep and value and query_key_is_sensitive(key) and not value.startswith(REDACTED):
            parts.append(f"{key}={REDACTED}")
        else:
            parts.append(pair)
    return "&".join(parts)


def _sanitize_fragment(fragment: str) -> str:
    """fragment 分形态：路由型 fragment 保留路由、只逐键 redact 其 query；其余整抹。

    ICS 是 hash 路由 SPA（`#/tsp/views/parts/list?name=sparePartList`）——旧实现
    fragment 一律整抹成 `<redacted>`，后果是**取证证据被销毁**：探索 trace / 审核塔
    草稿里看不出当前在哪个页面，`explore:step:<url>` 的 identity 还全部坍缩成一个
    （塔里去重合并、候选凭空消失）。而路由段本身不携带凭据（不含 `=`、不像字面秘密），
    真正危险的只是路由后的 query（`#/callback?code=…&state=…`）。

    判据（形态，不猜业务）：`/` 开头的 fragment 视为**路由型**——逐段保留，但对"像字面
    秘密"的段（`#/callback/eyJhbGci…` 这类把 token 塞进路径的形态）逐段 `looks_literal_secret`
    抹掉；`?` 之后的 query 走同一把 `_sanitize_query`。不以 `/` 开头的 fragment
    （`#access_token=xyz`、`#abcdef`）维持整抹（那是凭据/不透明令牌的典型形态）。
    逐段判据对真实路由零误伤（实测 `/spm/views/...`、`/tsp/views/...` 各段全 False，
    JWT 段 True）。
    """
    route, sep, query = fragment.partition("?")
    if not route.startswith("/"):
        return REDACTED
    route = "/".join(REDACTED if seg and looks_literal_secret(seg) else seg
                     for seg in route.split("/"))
    if not sep:
        return route
    return f"{route}?{_sanitize_query(query)}"


def sanitize_url(url: object) -> str:
    """URL 净化：**userinfo（`user:pass@`）整段 redact** + query 逐键 redact + fragment 按形态处理。

    相对 URL（`/web/x?token=…`）同样处理。坏形态原样返回（永不抛）：净化是尽力而为
    的遮蔽，不得把诊断文本改成崩溃或空串。
    """
    text = _text_of(url)
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    netloc = parts.netloc
    if "@" in netloc:
        # `https://user:pass@host/x`：userinfo 是经典凭据形态，且**不带敏感键名**——
        # 逐键 redact 抓不到它（2026-11-23 实测：文档摘要里的 `ops:S3cretPwd@…` 原样进了
        # flow YAML 与产物 docstring）。整段替换成 REDACTED@host，host 保留（归因还需要它）。
        netloc = f"{REDACTED}@{netloc.rsplit('@', 1)[1]}"
    if not (parts.query or parts.fragment) and netloc == parts.netloc:
        return text
    fragment = _sanitize_fragment(parts.fragment) if parts.fragment else ""
    try:
        return urlunsplit((parts.scheme, netloc, parts.path,
                           _sanitize_query(parts.query), fragment))
    except ValueError:
        return text


def _redact_kv(match: re.Match) -> str:
    key, value = match.group(1), match.group(2)
    if not query_key_is_sensitive(key) or value.startswith(REDACTED):
        return match.group(0)
    return f"{key}={REDACTED}"


def _redact_quoted(match: re.Match) -> str:
    key, glue, value = match.group(1), match.group(2), match.group(3)
    if not query_key_is_sensitive(key) or value.startswith(REDACTED):
        return match.group(0)
    return f'"{key}"{glue}"{REDACTED}"'


def _redact_header_lines(text: str) -> str:
    out: list[str] = []
    cursor = 0
    for match in _NAME_VALUE_LINE_RE.finditer(text):
        name, value = match.group(1), match.group(2)
        if value.startswith(REDACTED) or not header_is_sensitive(name, value):
            continue
        out.append(text[cursor:match.start(2)])
        out.append(REDACTED)
        cursor = match.end(2)
    out.append(text[cursor:])
    return "".join(out)


def sanitize_text(text: object) -> str:
    """异常/日志文本净化（C4② 的纯函数核）——四道刀，一把实现：

    1) `scheme://…` URL → `sanitize_url`（逐键 redact，业务参数保留）；
    2) 任意 `敏感键=值`（form 串、`?a=1&token=2` 残段）→ `键=<redacted>`；
    3) JSON/报文里的 `"敏感键": "值"` → 只擦值段；
    4) `名单键: 值` 行（`Cookie: sid=…`、`Authorization: Bearer eyJ…`、
       `isc-axios-sessionid: 550e…`）→ 值段整抹。

    为什么第 4 道是「抹到行尾」而不是第 2 道的逐段判熵：名单键的**值本身就是凭据**，
    「Bearer 前缀 + 短 token」这类形态逐段判熵会漏；值带空格时只擦半截比不擦更坏。
    非名单键（`Server:`、`Referer:`、`Content-Type:`）整行不动——取证信息保留（C4-A 同族）。

    幂等：已 redact 的键再进一次不变形。永不抛：入参非 str 走 `str()` 回落，坏 `__str__`
    回落空串（#89/#126 同纪律）。
    """
    try:
        result = _URL_RE.sub(lambda m: sanitize_url(m.group(0)), _text_of(text))
        result = _KV_RE.sub(_redact_kv, result)
        result = _QUOTED_KV_RE.sub(_redact_quoted, result)
        return _redact_header_lines(result)
    except Exception:
        # 净化器自身绝不先于被净化对象死：坏形态宁可原样返回（调用方另有兜底）。
        return _text_of(text)


def mask_placeholder_value(value: object) -> str:
    """未解析占位符清单的值段净化（C4-C）。

    JMeter 把回退值嵌在括号里：`${__P(name,字面默认值)}`——旧清单直接 `val[:80]` 打印，
    等于把 DEFAULT 段原样打进 stderr（`import_jmeter.py` 既有暴露面之一）。这里只抹**值段**：
    默认段呈字面秘密、或变量名本身带凭据语义时替换为 `<redacted>`；变量名与括号结构保留
    （那是人审接 env 需要的信息）。普通 k=v 与 URL 走同一把 `sanitize_text` 刀。
    """
    def _mask(match: re.Match) -> str:
        whole = match.group(0)
        head, sep, tail = whole[2:-1].partition(",")
        if not sep or not tail:
            return whole
        closing = ")" if tail.endswith(")") else ""
        inner = tail[:-1] if closing else tail
        if looks_literal_secret(inner) or header_is_sensitive(head, inner):
            return "${" + head + "," + REDACTED + closing + "}"
        return whole

    return sanitize_text(_PLACEHOLDER_RE.sub(_mask, _text_of(value)))


def gate_flow_headers(flow: object) -> list[tuple[int, str, str]]:
    """flow dict 的 steps[*].headers 就地过闸：命中的值替换为占位，返回映射清单。

    返回 `(步骤序号, header 名, env 建议键)`——**不回显值**（值永不进任何输出通道）。
    步骤序号从 1 起，与 `unresolved_placeholders` 口径一致。同一 (键, 值) 在全 flow 复用
    同一占位号：582 个重复 sessionid 头只需人审接一个 env 键，不是接 582 个。
    """
    steps = flow.get("steps") if isinstance(flow, dict) else None
    if not isinstance(steps, list):
        return []
    mapping: dict[tuple[str, str], str] = {}
    hits: list[tuple[int, str, str]] = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        headers = step.get("headers")
        if not isinstance(headers, dict):
            continue
        for name, value in list(headers.items()):
            if not header_is_sensitive(name, value):
                continue
            key = (str(name), _text_of(value))
            if key not in mapping:
                mapping[key] = placeholder_for(len(mapping) + 1)
            placeholder = mapping[key]
            headers[name] = placeholder
            hits.append((index, str(name), placeholder[2:-1]))
    return hits


def gated_placeholders_in(flow: object) -> list[tuple[int, str, str]]:
    """从 flow 反查本闸留下的占位（生成器路径的汇总提示用，不重新判定敏感与否）。"""
    steps = flow.get("steps") if isinstance(flow, dict) else None
    if not isinstance(steps, list):
        return []
    found: list[tuple[int, str, str]] = []
    for index, step in enumerate(steps, 1):
        headers = step.get("headers") if isinstance(step, dict) else None
        if not isinstance(headers, dict):
            continue
        for name, value in headers.items():
            if placeholder_index(value) is not None:
                found.append((index, str(name), _text_of(value)))
    return found

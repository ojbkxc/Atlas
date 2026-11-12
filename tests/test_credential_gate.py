"""C4 凭据闸与净化判据的离线钉桩（design_c4 §6.1/§6.2 + 评审必改 C4-A/B/C）。

全部纯函数、零网络零浏览器：字面 token 被拦 / 占位符放行 / 业务取证参数保留 /
定性不吃净化文本，四条口径各有一条会红的测。
"""
import pytest

from atlas import credential_gate as g

# 582 复现样本形态（D:/jmx 实测：isc-axios-sessionid 全为字面 UUID）
LITERAL_UUID = "b5360b2f-1c2d-3e4f-5a6b-7c8d9e0f1a2b"
LITERAL_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.abcDEF123"
HEX40 = "0123456789abcdef0123456789abcdef01234567"
# 净化样本主机只用 .example 虚构域；集中成常量是给 SAFE-001（把字面 URL 算硬编码）
# 留一处而不是满屏的固有误报，不是为了藏样本。
CB = "https://sso.example/cb"
ICS_LIST = "https://ics.example.com/web/list"
SHORT_HOST = "https://x/y"
CB_SHORT = "https://x/cb"


# ── §6.1 header_is_sensitive：名单命中与「已外部化放行」────────────────

@pytest.mark.parametrize("name,value", [
    ("Authorization", "Bearer abc123"),
    ("authorization", "Basic Zm9vOmJhcg=="),
    ("Proxy-Authorization", "Bearer x"),
    ("Cookie", "JSESSIONID=abcdef0123456789"),
    ("Set-Cookie", "sid=" + LITERAL_UUID),
    ("x-api-key", "sk-1234567890"),
    ("token", "abc"),
    ("isc-axios-sessionid", LITERAL_UUID),        # 582 复现：靠 sessionid 子串命中
])
def test_literal_credential_headers_are_gated(name, value):
    assert g.header_is_sensitive(name, value) is True, f"{name} 字面值必须被拦"


@pytest.mark.parametrize("name,value", [
    ("isc-app", "iservice"),                      # 业务识别头：名不含凭据词、值短
    ("Content-Type", "application/json"),
    ("User-Agent", "Atlas/0.1"),
    ("Authorization", "Bearer ${tok}"),           # 已外部化：人审接 env 的位
    ("new_accessToken", "${__P(new_accessToken,)}"),   # 设计 §6.1 明列的不命中样本
    ("Cookie", "${COOKIE}"),
    ("isc-axios-sessionid", "${ATLAS_HDR_1}"),    # 闸自身产物的幂等复扫
])
def test_externalized_and_business_headers_pass(name, value):
    assert g.header_is_sensitive(name, value) is False, f"{name} 不该被拦（占位/业务头）"


def test_name_hit_gates_even_benign_looking_values():
    """名单命中即拦：`X-Session-Token: page-2` 也占位。宁可多接一个 env 键，
    也不放字面凭据进产物——值形态启发式只用于名单外的键（下一条）。"""
    assert g.header_is_sensitive("X-Session-Token", "page-2") is True


def test_second_branch_needs_both_credential_hint_and_literal_secret():
    """第二判据分支：名单外但语义是凭据的键，只有值呈字面秘密才拦。"""
    assert g.header_is_sensitive("x-sign", LITERAL_UUID) is True
    assert g.header_is_sensitive("x-sign", "page-2") is False
    # 纯数字业务键不因「值像 hex」被误伤
    assert g.header_is_sensitive("orderId", "20261106001") is False
    assert g.header_is_sensitive("orderId", HEX40) is False


# ── §6.2 looks_literal_secret：形态启发式（确定可测，不猜）──────────────

@pytest.mark.parametrize("value", [LITERAL_UUID, LITERAL_JWT, HEX40, "a" * 32,
                                   "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+/"])
def test_secret_shapes_detected(value):
    assert g.looks_literal_secret(value) is True


@pytest.mark.parametrize("value", [
    "", "abc", "非人工创建 巡检", "20261106001", "SO-DEMO-0003",
    "${VAR}", "${__P(name,)}", "hello world again", "true",
])
def test_non_secret_values_pass(value):
    assert g.looks_literal_secret(value) is False, f"{value!r} 不该判成字面秘密"


def test_placeholder_form_is_stable_and_recoverable():
    assert g.placeholder_for(1) == "${ATLAS_HDR_1}"
    assert g.placeholder_index("${ATLAS_HDR_12}") == 12
    assert g.placeholder_index("${OTHER_VAR}") is None
    assert g.placeholder_index(None) is None


# ── C4-A（评审必改）：sanitize_url 逐键 redact，不整段抹 query ──────────

def test_sanitize_url_redacts_only_matching_keys_keeps_business_params():
    """整段抹 query 会销毁 docNo/page 这类追责证据（#85），与红线 #8 取证面相抵。"""
    out = g.sanitize_url(f"{CB}?ticket=ST-123&docNo=SO-9&page=2#access_token=xyz")
    assert "ticket=<redacted>" in out
    assert "docNo=SO-9" in out and "page=2" in out, "非敏感业务参数必须保留"
    assert "ST-123" not in out and "xyz" not in out
    assert out.endswith("#<redacted>"), "非路由型 fragment（裸令牌）整抹"


def test_sanitize_url_keeps_spa_route_fragment_redacts_its_query():
    """路由型 fragment（`#/path`）保留路由段 + 其 query 逐键 redact。

    旧口径 fragment 一律整抹——对 ICS 这类 hash 路由 SPA 等于把「在哪个页面」的取证证据
    一起抹掉（探索 trace / 审核塔草稿看不出页面坐标，`explore:step:<url>` identity 全部坍缩）。
    真实 SIT URL 形态。
    """
    url = "https://ics.example.com/?ticket=ST-9#/tsp/views/parts/list?name=sparePartList"
    out = g.sanitize_url(url)
    assert out == "https://ics.example.com/?ticket=<redacted>#/tsp/views/parts/list?name=sparePartList"
    # 路由后的 callback query 仍逐键遮蔽
    cb = g.sanitize_url("https://ics.example.com/#/cb?code=SEC&state=abc&docNo=SO-1")
    assert cb == "https://ics.example.com/#/cb?code=<redacted>&state=<redacted>&docNo=SO-1"
    # 令牌塞进路由段：逐段判据抹掉该段，其余段保留
    seg = g.sanitize_url("https://x/#/cb/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc")
    assert seg.startswith("https://x/#/cb/<redacted>")
    # 幂等（净化两次 == 一次）
    for probe in (url, cb, seg):
        assert g.sanitize_url(g.sanitize_url(probe)) == g.sanitize_url(probe)


def test_sanitize_url_handles_relative_url_and_no_query():
    assert g.sanitize_url("/web/demo/list?token=abc&ou=KTKI") == "/web/demo/list?token=<redacted>&ou=KTKI"
    url = f"{ICS_LIST}?docNo=1&page=2"
    assert g.sanitize_url(url) == url, "无敏感键无 fragment 时逐字不变（不把正常路径翻成改写）"


def test_sanitize_url_short_words_are_exact_match_only():
    """`key`/`code` 这类短词只做精确命中：`monkey`/`statusCode` 是业务参数，不抹。"""
    out = g.sanitize_url(f"{SHORT_HOST}?monkey=3&statusCode=401&key=SECRET")
    assert "monkey=3" in out and "statusCode=401" in out
    assert "key=<redacted>" in out


# ── sanitize_text 四道刀 + 幂等 + 永不抛 ────────────────────────────────

def test_sanitize_text_kvpairs_and_json_and_header_lines():
    assert g.sanitize_text("failed token=abc123&docNo=SO-1") == "failed token=<redacted>&docNo=SO-1"
    assert g.sanitize_text('{"sessionid": "' + LITERAL_UUID + '", "docNo": "SO-1"}') \
        == '{"sessionid": "<redacted>", "docNo": "SO-1"}'
    assert g.sanitize_text("Authorization: Bearer " + LITERAL_JWT) == "Authorization: <redacted>"
    # 非名单键整行不动（取证信息保留）
    assert g.sanitize_text("Server: nginx/1.24") == "Server: nginx/1.24"


def test_sanitize_text_is_idempotent():
    once = g.sanitize_text(f"url={CB_SHORT}?code=ST-9&state=abc&page=2")
    assert g.sanitize_text(once) == once, "净化必须幂等：叠加 <redacted> 会把报告弄成噪音"


def test_sanitize_text_crlf_and_non_str_never_raise():
    out = g.sanitize_text("Cookie: sid=1; region=2\r\nAuthorization: Bearer x")
    assert "\r\n" in out and "sid=1" not in out and "Bearer x" not in out
    assert g.sanitize_text(12345) == "12345"
    assert g.sanitize_text(None) == "None"

    class Nasty:
        def __str__(self):
            raise RuntimeError("坏 __str__")
    assert g.sanitize_text(Nasty()) == ""       # 净化器不得先于被净化对象死（#89）


def test_sanitize_text_does_not_mangle_failure_words():
    """脱敏只擦秘密，不擦失败词（红线 #7 反向面）：关键词在键名/正文时原样保留。"""
    text = "401 unauthorized 查询无结果 timeout element not visible"
    assert g.sanitize_text(text) == text


# ── C4-C：占位符清单的 DEFAULT 段不外泄 ────────────────────────────────

def test_mask_placeholder_value_hides_literal_default():
    """`${__P(name,DEFAULT)}` 的回退段是 JMeter 语法的字面凭据藏身处（import 清单旧实现
    直接 val[:80] 打回 stderr）。"""
    masked = g.mask_placeholder_value("${__P(password,SuperSecretDefault123)}")
    assert "SuperSecretDefault123" not in masked
    assert "password" in masked and masked.startswith("${__P(password,")
    assert "<redacted>" in masked


def test_mask_placeholder_value_keeps_plain_vars_verbatim():
    for text in ("${PAGE}", "${__P(cust_no,)}", "custNo=CUST1&page=${PAGE}"):
        assert g.mask_placeholder_value(text) == text


# ── 闸对 flow 的接线形态（迁移/生成共用同一实现）───────────────────────

def test_gate_flow_headers_reuses_one_placeholder_per_literal_credential():
    flow = {"steps": [
        {"headers": {"isc-axios-sessionid": LITERAL_UUID, "isc-app": "iservice"}},
        {"headers": {"isc-axios-sessionid": LITERAL_UUID}},
        {"headers": {"Authorization": "Bearer ${tok}"}},
    ]}
    hits = g.gate_flow_headers(flow)
    assert [h[1] for h in hits] == ["isc-axios-sessionid", "isc-axios-sessionid"]
    assert hits[0][0] == 1 and hits[1][0] == 2
    assert flow["steps"][0]["headers"] == {"isc-axios-sessionid": "${ATLAS_HDR_1}",
                                           "isc-app": "iservice"}
    assert flow["steps"][2]["headers"] == {"Authorization": "Bearer ${tok}"}, "占位符不该被再占位"
    assert all(LITERAL_UUID not in str(s) for s in flow["steps"])


def test_gate_flow_headers_tolerates_bad_shapes():
    assert g.gate_flow_headers(None) == []
    assert g.gate_flow_headers({"steps": "字符串"}) == []
    assert g.gate_flow_headers({"steps": [None, {"headers": "坏形态"}, {}]}) == []


def test_gated_placeholders_in_finds_only_gate_products():
    flow = {"steps": [{"headers": {"x-api-key": "${ATLAS_HDR_2}", "base": "${BASE_URL}"}}]}
    assert g.gated_placeholders_in(flow) == [(1, "x-api-key", "${ATLAS_HDR_2}")]

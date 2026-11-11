"""gap12 W1：LLM 客户端离线判据（零网络——注入 transport / monkeypatch urlopen）。

判据面（`docs/gaps/gap12-llm-autonomous-explore.md` E.1–E.3）：
env 缺席逐键点名；HTTP/连接/畸形响应**绝不吞成空串**；正常响应逐字返回；
请求形态（端点/头/载荷）可核。
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from atlas import llm


def _ok_payload(content: str = "hello") -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}
                      ).encode("utf-8")


# ===================== E.1 env 门：缺席逐键点名 =====================

def test_missing_all_keys_names_all_three():
    with pytest.raises(llm.LLMNotConfigured) as ei:
        llm.LLMConfig.from_env({})
    msg = str(ei.value)
    for key in ("ATLAS_LLM_BASE_URL", "ATLAS_LLM_API_KEY", "ATLAS_LLM_MODEL"):
        assert key in msg, key


def test_missing_one_key_names_that_one():
    env = {"ATLAS_LLM_BASE_URL": "http://x/v1", "ATLAS_LLM_API_KEY": "k"}
    with pytest.raises(llm.LLMNotConfigured) as ei:
        llm.LLMConfig.from_env(env)
    assert "缺 ATLAS_LLM_MODEL" in str(ei.value)   # 点名的「缺」清单里只有它


def test_blank_value_counts_as_missing():
    env = {"ATLAS_LLM_BASE_URL": "http://x/v1", "ATLAS_LLM_API_KEY": "   ",
           "ATLAS_LLM_MODEL": "m"}
    with pytest.raises(llm.LLMNotConfigured) as ei:
        llm.LLMConfig.from_env(env)
    assert "ATLAS_LLM_API_KEY" in str(ei.value)


def test_config_normalizes_base_url():
    cfg = llm.LLMConfig.from_env({"ATLAS_LLM_BASE_URL": "http://x/v1/",
                                   "ATLAS_LLM_API_KEY": "k", "ATLAS_LLM_MODEL": "m"})
    assert cfg.base_url == "http://x/v1"      # 尾斜杠剥掉，拼端点不出现双斜杠
    assert (cfg.api_key, cfg.model) == ("k", "m")


# ===================== E.3 正常响应逐字返回 + 请求形态可核 =====================

def test_complete_returns_content_and_sends_openai_shape():
    seen: dict = {}

    def transport(url, body, headers, timeout):
        seen.update({"url": url, "body": json.loads(body.decode("utf-8")),
                     "headers": headers, "timeout": timeout})
        return _ok_payload("世界")

    cfg = llm.LLMConfig("http://gw/v1", "sk-1", "model-x")
    client = llm.LLMClient(cfg, timeout=12.5, transport=transport)
    out = client.complete([{"role": "user", "content": "hi"}], max_tokens=64)
    assert out == "世界"
    assert seen["url"] == "http://gw/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-1"
    assert seen["timeout"] == 12.5
    assert seen["body"]["model"] == "model-x"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["body"]["max_tokens"] == 64


def test_complete_omits_max_tokens_when_none():
    seen: dict = {}
    client = llm.LLMClient(llm.LLMConfig("http://gw/v1", "k", "m"),
                           transport=lambda u, b, h, t: (seen.update(json.loads(b)), _ok_payload())[1])
    client.complete([{"role": "user", "content": "x"}])
    assert "max_tokens" not in seen


# ===================== E.2 错误绝不吞成空串 =====================

def test_http_error_maps_to_llm_error_with_status_and_snippet(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {},
                                     io.BytesIO(b'{"error":"boom"}'))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    client = llm.LLMClient(llm.LLMConfig("http://gw/v1", "k", "m"))
    with pytest.raises(llm.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    msg = str(ei.value)
    assert "500" in msg and "boom" in msg          # 状态码 + 响应片段都在


def test_connection_error_maps_to_llm_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    client = llm.LLMClient(llm.LLMConfig("http://gw/v1", "k", "m"))
    with pytest.raises(llm.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    assert "timed out" in str(ei.value)


def test_non_json_response_is_error_not_empty_string():
    client = llm.LLMClient(llm.LLMConfig("http://gw/v1", "k", "m"),
                           transport=lambda u, b, h, t: b"<html>502</html>")
    with pytest.raises(llm.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    assert "JSON" in str(ei.value)


def test_response_missing_choices_is_error():
    client = llm.LLMClient(llm.LLMConfig("http://gw/v1", "k", "m"),
                           transport=lambda u, b, h, t: b'{"id":"x"}')
    with pytest.raises(llm.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    assert "choices" in str(ei.value)


def test_non_string_content_is_error():
    raw = json.dumps({"choices": [{"message": {"content": {"a": 1}}}]}).encode("utf-8")
    client = llm.LLMClient(llm.LLMConfig("http://gw/v1", "k", "m"),
                           transport=lambda u, b, h, t: raw)
    with pytest.raises(llm.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    assert "字符串" in str(ei.value)

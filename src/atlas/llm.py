"""LLM 客户端（gap12 W1）：OpenAI 兼容 chat completions，**仅标准库**。

设计约束（`docs/gaps/gap12-llm-autonomous-explore.md` §C）：
- env 门：`ATLAS_LLM_BASE_URL` / `ATLAS_LLM_API_KEY` / `ATLAS_LLM_MODEL` 三者齐备才可用；
  缺席**逐键点名**抛 `LLMNotConfigured`——不静默降级、不 fallback 假数据。
- 错误**绝不吞**：超时/HTTP≥400/畸形响应一律抛 `LLMError`（带状态码与响应片段），
  绝不返回空串冒充成功（空串会被上层读成「模型说没事干」）。
- 不新增运行时依赖：走 `urllib.request`；测试用注入 `transport` 离线跑，零网络。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

_ENV_BASE = "ATLAS_LLM_BASE_URL"
_ENV_KEY = "ATLAS_LLM_API_KEY"
_ENV_MODEL = "ATLAS_LLM_MODEL"
_ENV_REQUIRED = (_ENV_BASE, _ENV_KEY, _ENV_MODEL)


class LLMError(RuntimeError):
    """LLM 调用失败（配置/网络/HTTP/响应形态）；消息自带可核片段。"""


class LLMNotConfigured(LLMError):
    """三个 env 键不齐——点名缺哪个，不在别处猜默认端点。"""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, env: dict | None = None) -> "LLMConfig":
        env = os.environ if env is None else env
        missing = [k for k in _ENV_REQUIRED if not (env.get(k) or "").strip()]
        if missing:
            raise LLMNotConfigured(
                "LLM 未配置：缺 " + "、".join(missing)
                + f"（三个键都要设：{'、'.join(_ENV_REQUIRED)}）")
        return cls(env[_ENV_BASE].strip().rstrip("/"),
                   env[_ENV_KEY].strip(),
                   env[_ENV_MODEL].strip())


def _urllib_transport(url: str, body: bytes, headers: dict, timeout: float) -> bytes:
    """默认传输：POST 并读回响应体；HTTP/连接错误映射成 `LLMError`（不吞）。"""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        raise LLMError(f"LLM HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM 连接失败：{exc.reason}") from exc


def _parse_content(raw: bytes) -> str:
    """OpenAI 形状响应 → content 字符串；任何形态问题都点名抛错。"""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM 响应不是合法 JSON：{raw[:200]!r}") from exc
    try:
        content = obj["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM 响应缺 choices[0].message.content：{str(obj)[:200]}") from exc
    if not isinstance(content, str):
        raise LLMError(f"LLM content 不是字符串：{type(content).__name__}")
    return content


class LLMClient:
    """最小 chat 客户端；`transport` 可注入（测试离线跑，零网络）。"""

    def __init__(self, config: LLMConfig, *, timeout: float = 60.0, transport=None):
        self.config = config
        self.timeout = timeout
        self._transport = transport or _urllib_transport

    def complete(self, messages: list[dict], *, temperature: float = 0.0,
                 max_tokens: int | None = None, timeout: float | None = None) -> str:
        payload: dict = {"model": self.config.model, "messages": list(messages),
                         "temperature": temperature}
        # `is not None` 而不是 `if max_tokens`（2026-11-25 审计 A-04）：`0` 为假 → 键根本不写
        # → 想「不限输出」的人拿到 provider 默认上限（通常很小），表现为 JSON 被截断，
        # 而归因会漂到 `_parse_content`（指向「模型输出缺字段」而不是「你把 max_tokens
        # 写成 0 了」）。负数则原样进 payload（provider 侧 400）。
        # 「缺省」与「显式零」是两件事（#115 同族）：None = 不传，其他值必须过值域闸。
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
                raise LLMError(
                    f"max_tokens 必须是正整数（收到 {max_tokens!r}）——"
                    f"不传=用 provider 默认上限；写 0 会被当空值静默丢弃，反而拿到默认上限")
            payload["max_tokens"] = int(max_tokens)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.config.base_url}/chat/completions"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.config.api_key}"}
        raw = self._transport(url, body, headers, timeout if timeout is not None else self.timeout)
        return _parse_content(raw)

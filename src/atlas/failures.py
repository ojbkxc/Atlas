from __future__ import annotations
from enum import Enum

class FailureClass(str, Enum):
    ENV_BLOCKED = "env_blocked"
    SESSION = "session"
    INTERACTION = "interaction"
    CONTRACT = "contract"
    DATA = "data"
    ASSERTION = "assertion"
    UNKNOWN = "unknown"

class EnvBlocked(Exception):
    """环境限制是独立终态，不伪装成通过，也不伪装成代码失败。"""

class ContractViolation(AssertionError):
    pass

class FlowCompositionError(ValueError):
    pass

_RULES = (
    (FailureClass.ENV_BLOCKED, ("envblocked", "stepskipped", "bug-653", "default tax rate",
                               "bug-999", "nullpointerexception", "环境阻塞", "产品设计限制")),
    (FailureClass.SESSION, ("401", "403", "unauthorized", "auth_expired", "signin", "cookie", "session")),
    (FailureClass.CONTRACT, ("contractviolation", "bug-", "cannot be empty", "required", "invalid")),
    (FailureClass.ASSERTION, ("assertionerror", "mismatch", "expected", "unexpected", "断言")),
    (FailureClass.INTERACTION, ("timeout", "not visible", "click", "locator", "intercept", "element")),
    (FailureClass.DATA, ("not found", "no such", "no data", "empty", "找不到")),
)

def classify_failure(exc: BaseException | None) -> FailureClass:
    if exc is None:
        return FailureClass.UNKNOWN
    text = f"{type(exc).__name__} {exc}".lower()
    return next((kind for kind, words in _RULES if any(word in text for word in words)),
                FailureClass.UNKNOWN)

def api_evidence(original: BaseException, replay: dict) -> FailureClass:
    """UI 失败时重放同接口：API 正常归交互；API 失败优先识别会话/环境/契约。"""
    if replay.get("ok"):
        return FailureClass.INTERACTION
    status = replay.get("status")
    if status in {401, 403} or replay.get("data", {}).get("auth_expired"):
        return FailureClass.SESSION
    text = str(replay.get("data", replay.get("error", ""))).lower()
    evidence = RuntimeError(text)
    if status and status >= 500:
        return FailureClass.ENV_BLOCKED
    classified = classify_failure(evidence)
    return classified if classified != FailureClass.UNKNOWN else FailureClass.ENV_BLOCKED

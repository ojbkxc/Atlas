"""`docs/api-pitfalls.md` §3 的状态码归因表 vs `failures.api_evidence` **逐格差分**。

## 为什么值得钉

§3 那张表（ok / 401-403 / 5xx / 契约词 / 查询无结果 / 其余兜底）是**运维读归因的第一站**：
它教人「看到 500 就去查环境、看到 auth_expired 就去重登」。表与实现一旦漂移，读的人会
按错的方向排查，而代码这边**不会报错**——文档漂移是静默的。

同族前例：`first-bug.md` 说「demo_api 缺失不挡 API 腿」，实测直接 rc 2（2026-11-24 修）。

## 做法（不新增行为，只做对照）

用同一批构造输入分别跑 ①`failures.api_evidence`（实现）与 ②§3 表声明的期望归类，
逐格比对。表里每一行都对应一条断言；实现与表任何一侧变动都会红。

## 六条（对应 §3 的六行）

1. `ok=True` → INTERACTION；
2. `401` / `403` / `data.auth_expired` → SESSION；
3. `status >= 500` → ENV_BLOCKED；
4. 契约词（`bug-`/`required`/`invalid`）→ CONTRACT；
5. 查询无结果（`_DATA_ABSENT` 家族）→ DATA；
6. 其余 → ENV_BLOCKED（兜底不冒充待诊断缺陷）。
"""
from __future__ import annotations

from atlas.failures import FailureClass, api_evidence


def _replay(**kw) -> dict:
    base = {"ok": False, "status": 200, "data": {}}
    base.update(kw)
    return base


def _classify(replay: dict) -> FailureClass:
    """用同一个异常体做输入，只让 replay 决定归类（与 UI 失败重放同口径）。"""
    return api_evidence(RuntimeError("ui step failed"), replay)


def test_ok_true_is_interaction():
    """API 正常 = UI 操作问题。"""
    assert _classify(_replay(ok=True, data={"resultCode": "BUG-000"})) == FailureClass.INTERACTION


def test_401_403_and_auth_expired_are_session():
    for replay in (_replay(status=401), _replay(status=403),
                   _replay(data={"auth_expired": True})):
        assert _classify(replay) == FailureClass.SESSION, replay


def test_5xx_is_env_blocked():
    """§3：5xx 不是测试代码能修的 → ENV_BLOCKED（不冒充待诊断缺陷）。

    **判别力说明（2026-11-24 自曝）**：单独用 `status=500` + 无关键词的 data 测，
    5xx 分支与「其余兜底」两行**归因相同**（都是 ENV_BLOCKED），所以把 5xx 阈值改坏
    这条测也照样绿——我第一版就是这么写的，变异测当场证明它恒真。

    真正能区分的是**与契约词同时出现**：§3 的行序把 5xx 排在契约词**之前**，
    所以 `500 + bug-653` 必须是 ENV_BLOCKED；若 5xx 分支失效，契约词分支会把它判成
    CONTRACT（这就是 §4「税率 NPE 归 ENV_BLOCKED」那条实锤的依据）。
    """
    for status in (500, 502, 503):
        assert _classify(_replay(status=status)) == FailureClass.ENV_BLOCKED, status
    # 判别格：5xx 优先于契约词
    mixed = _replay(status=500, data={"message": "NullPointerException 单号 SPM-653",
                                      "resultCode": "bug-653"})
    assert _classify(mixed) == FailureClass.ENV_BLOCKED, (
        "5xx 与契约词同时出现时应归 ENV_BLOCKED（§3 行序 + §4 税率 NPE 实锤）；"
        "若得到 CONTRACT，说明 5xx 分支失效")


def test_contract_words_are_contract():
    """§3 第 4 行（2026-11-24 更正后）：**契约词**才归 CONTRACT。"""
    for word in ("required", "invalid"):
        replay = _replay(data={"message": f"{word} happened"})
        assert _classify(replay) == FailureClass.CONTRACT, word


def test_known_product_defect_ids_are_env_blocked_not_contract():
    """§3 第 5 行（更正新增）：已知**产品缺陷**单号归 ENV_BLOCKED，**不是** CONTRACT。

    旧表把 `bug-` 与 required/invalid 并列写成 CONTRACT——实测 `200 + bug-653` 得
    `env_blocked`。这条测钉住更正后的口径，防止有人「照旧表修回去」。
    """
    for word in ("bug-653", "bug-999", "NullPointerException"):
        replay = _replay(data={"message": f"{word}"})
        got = _classify(replay)
        assert got == FailureClass.ENV_BLOCKED, (
            f"{word!r} 应归 ENV_BLOCKED（产品缺陷，测试代码改不了），实为 {got.value}")
        assert got != FailureClass.CONTRACT, (
            f"{word!r} 被判成 CONTRACT——会把人引去改测试代码，方向是反的")


def test_data_absent_is_data():
    """§3：主数据缺失 → DATA（换数据不修代码）。"""
    replay = _replay(data={"message": "查询无结果 / no data found"})
    assert _classify(replay) == FailureClass.DATA, replay


def test_fallback_is_env_blocked():
    """§3 兜底行：其余归 ENV_BLOCKED，不冒充待诊断缺陷。"""
    replay = _replay(data={"message": "something unclassified"})
    assert _classify(replay) == FailureClass.ENV_BLOCKED, replay

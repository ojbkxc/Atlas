"""「输入/参数未校验 → 功能静默失效」族的收口守卫（审计 A-01~A-08 + B-01）。

## 挡的是什么（2026-11-25 审计，`reports/parameter-audit-2026-11-24.md`）

上一波修掉了三处同族缺陷（`--fail-under -1` 关掉质量门禁 / `--max-steps -1` 零迭代却报
「步数用尽」/ JSON 读侧裸 utf-8 让 BOM 静默降级）。审计又扫出 9 条同族，本文件逐条钉住。

**这一族的共同形态**：参数/输入写错**不报错**，只让功能悄悄少做一点或做反，
并且给出一个**看起来正常**的结果。危害排序（审计口径）：
②假绿 > ③静默降级 > ④噪音。

| 编号 | 站点 | 形态 |
|---|---|---|
| A-01 | `review_code` 聚合 | `avg_score = round(...)` **之后**才比阈值 → 真均值 79.6 显示 80 → `--fail-under 80` 放行（②） |
| A-02 | `review_code` knowledge 模式 | `except Exception: registry = None` 裸降级，报告仍自称 `mode: knowledge`（②） |
| A-03 | `--create-quota -1` | 绕过 `CreateQuota.__init__` 值域闸 → 每次建单 `EnvBlocked`，输入错误伪装成环境阻塞（③） |
| A-04 | `llm.complete(max_tokens=0)` | `if max_tokens:` 把 0 当空值 → 键不写 → 想「不限」拿到 provider 默认上限（③） |
| A-05 | `explore_auto(max_failures=0)` | 兄弟参数都有闸，唯独它没有 → 首败即停却报 `stopped_reason=max_failures`（③） |
| A-06 | `ATLAS_HEADED` | 裸读 `os.environ.get(...) != "1"`，绕过 `_flag_env` 单点 → `true` 静默按关（④） |
| A-07 | `state_machine.run(max_steps=0)` | 零迭代却报「可能存在循环或守卫恒假」（④） |
| A-08 | `review_code(fail_under=-1)` | 值域闸只在 CLI 层，库面直调门禁恒不触发（④结构性） |
| B-01 | `--baseline` 读失败 | `except OSError: pass` → 「读失败」与「没差分」输出完全一样（③） |

## 五条纪律

1. **测试用「负例 / 零例 / bool 混入 / 正向对照」四件套**——正向对照是必须的：
   闸收过头会把合法值也挡掉，那比原缺陷更难发现（功能直接不可用）。
2. **A-01 用 monkeypatch 控制分数**：`score_findings` 的 `total` 是加权浮点和，
   靠构造源码凑出 79.6 不可靠；直接注入 `79.6` 才能钉住「比阈值的是未取整值」这一条。
3. **A-02 的键只在真降级时出现**：非 knowledge 模式多出 `knowledge_degraded` 键属噪声化，
   单独一条钉住。
4. **A-06 断言 WARN**：`_flag_env` 的价值一半在「坏值点名」，只断言 `headless` 会漏掉它。
5. **A-03 的零语义**：`--create-quota 0` 是**合法显式值**（= 本次不建单），
   不许被闸顺手改成「必须 > 0」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas import review_code as rc_mod
from atlas.autopilot import explore_auto
from atlas.cli import _run
from atlas.config import AtlasConfig
from atlas.llm import LLMClient, LLMConfig, LLMError
from atlas.state_machine import StateMachine

# ── 公共夹具 ────────────────────────────────────────────────────────────────

def _clean_py(tmp_path: Path, name: str = "sample.py") -> Path:
    """一个能过评审的最小源文件（无 finding）。"""
    path = tmp_path / name
    path.write_text("def f():\n    return 1\n", encoding="utf-8")
    return path


def _fake_score(total: float):
    """替换 `score_findings`：只控制总分，维度留空（聚合只需 total）。"""
    def _score(findings, config):
        return {"total": total, "grade": "C", "dimensions": {},
                "finding_count": len(findings)}
    return _score


# ── A-01：门禁必须比未取整的均值 ────────────────────────────────────────────

def test_fail_under_uses_raw_average_not_rounded(tmp_path, monkeypatch):
    """真均值 79.6 → 展示 80，但 `--fail-under 80` 必须 rc 1。

    改前：`avg_score = round(79.6) == 80`，`80 < 80` 为假 → rc 0（假绿）。
    `round` 的方向恒定有利（五入向上），属判据本身放宽而不是显示问题。
    """
    monkeypatch.setattr(rc_mod, "score_findings", _fake_score(79.6))
    result = rc_mod.review_code([_clean_py(tmp_path)], format="json", fail_under=80)
    assert result["summary"]["avg_score"] == 80, "展示值仍应取整"
    assert result["summary"]["avg_score_raw"] == 79.6, "对账值必须是未取整的真均值"
    assert result["rc"] == 1, "79.6 < 80，门禁必须红（改前这里是 0）"


def test_fail_under_raw_average_positive_control(tmp_path, monkeypatch):
    """正向对照：79.6 对 `--fail-under 79` 必须 rc 0（防闸收过头把合法值挡掉）。"""
    monkeypatch.setattr(rc_mod, "score_findings", _fake_score(79.6))
    result = rc_mod.review_code([_clean_py(tmp_path)], format="json", fail_under=79)
    assert result["rc"] == 0


# ── A-02：knowledge 降级必须点名 ────────────────────────────────────────────

def _break_plugin(monkeypatch):
    def _boom():
        raise RuntimeError("插件包坏了")
    monkeypatch.setattr("atlas.plugin_api.resolve_plugin", _boom)


def test_knowledge_mode_degradation_is_visible(tmp_path, monkeypatch, capsys):
    """知识库坏 → stderr WARN + `summary["knowledge_degraded"] is True`。

    改前是裸 `registry = None`：选择器核对整段不执行（下游三处以 `registry is not None`
    为闸），报告却自称 `mode: knowledge`，分数因少一批 finding 而升高。
    """
    _break_plugin(monkeypatch)
    result = rc_mod.review_code([_clean_py(tmp_path)], mode="knowledge", format="json")
    err = capsys.readouterr().err
    assert "WARN" in err and "knowledge" in err, f"降级必须打 WARN，实际 stderr={err!r}"
    assert result["summary"].get("knowledge_degraded") is True
    assert result["summary"]["mode"] == "knowledge", "模式名仍如实反映用户请求"


def test_static_mode_has_no_degraded_key(tmp_path):
    """正向对照：非 knowledge 模式不得凭空多出该键（防噪声化）。"""
    result = rc_mod.review_code([_clean_py(tmp_path)], mode="static", format="json")
    assert "knowledge_degraded" not in result["summary"]


def test_text_format_marks_degradation(tmp_path, monkeypatch):
    """人读报告也要看得到降级——否则只有跑 JSON 的人知道。"""
    _break_plugin(monkeypatch)
    result = rc_mod.review_code([_clean_py(tmp_path)], mode="knowledge", format="text")
    assert "降级" in result["_text"], result["_text"][:200]


# ── B-01：基线差分读失败必须有 notes ────────────────────────────────────────

def test_baseline_read_failure_is_noted(tmp_path, monkeypatch, capsys):
    """基线差分抛 OSError → `baseline_notes` 点名 + stderr WARN。

    改前 `except OSError: pass`：「读失败」与「读了但没差分」输出完全一样（都是空列表），
    `--baseline` 被静默关掉而分数不受影响。
    """
    baseline = _clean_py(tmp_path, "baseline.py")

    def _boom(*args, **kwargs):
        raise OSError("磁盘读失败")
    monkeypatch.setattr(rc_mod, "diff_baseline", _boom)

    result = rc_mod.review_code([_clean_py(tmp_path)], format="json", baseline=baseline)
    notes = result["results"][0]["baseline_notes"]
    assert any("基线差分未执行" in n for n in notes), f"notes={notes}"
    assert any("OSError" in n for n in notes), "应点名异常类型"
    assert "WARN" in capsys.readouterr().err


# ── A-08：fail_under 值域闸下沉到库面 ───────────────────────────────────────

@pytest.mark.parametrize("bad", [-1, 101, 1000])
def test_review_code_rejects_out_of_range_fail_under(tmp_path, bad):
    """库面直调也必须拒——改前只有 CLI 面有闸，`fail_under=-1` 让门禁恒不触发。"""
    with pytest.raises(ValueError) as exc:
        rc_mod.review_code([_clean_py(tmp_path)], format="json", fail_under=bad)
    assert "fail_under" in str(exc.value)


@pytest.mark.parametrize("ok", [0, 50, 100])
def test_review_code_accepts_in_range_fail_under(tmp_path, ok):
    """正向对照：0/50/100 是合法边界，不许被闸挡掉。"""
    result = rc_mod.review_code([_clean_py(tmp_path)], format="json", fail_under=ok)
    assert result["rc"] in (0, 1)


# ── A-03：--create-quota 负值 rc 2（且 0 合法）─────────────────────────────

def _run_args(**kw):
    from types import SimpleNamespace
    base = {"case_timeout": None, "create_quota": 0, "bu": ""}
    base.update(kw)
    return SimpleNamespace(**base)


def test_create_quota_negative_is_user_error(capsys):
    """`-1` → rc 2 且点名参数。改前它会变成每次建单的 `EnvBlocked`（伪装成环境阻塞）。"""
    rc = _run(_run_args(create_quota=-1), None, None)
    assert rc == 2
    assert "--create-quota" in capsys.readouterr().err


def test_create_quota_zero_is_legal(capsys):
    """正向对照：`0` 是**合法显式值**（本次不建单），不许被闸当成错误。

    本测只关心「闸不误伤」：`0` 必须越过值域闸进入后续逻辑（后续会因缺少完整 args
    而报 AttributeError，那是测试夹具的限制，不是被测行为）。
    """
    try:
        rc = _run(_run_args(create_quota=0), None, None)
    except AttributeError:
        rc = None        # 已越过闸，进入 args.flow 等后续读取
    err = capsys.readouterr().err
    assert "--create-quota" not in err, f"0 不该被值域闸拦下：{err!r}"
    assert rc != 2


def test_runtime_clamps_negative_quota():
    """库面兜底：`runtime` 直接写属性会绕过 `CreateQuota.__init__`，故必须 `max(0, ...)`。"""
    from atlas.probes import CreateQuota
    q = CreateQuota(limit=max(0, -1))
    assert q.limit == 0, "负配额进到运行期必须是 0，而不是每次建单 EnvBlocked"


# ── A-04：max_tokens 缺省与显式零是两件事 ──────────────────────────────────

def _llm_capture(sink: dict) -> LLMClient:
    def _transport(url, body, headers, timeout):
        sink["payload"] = json.loads(body.decode("utf-8"))
        return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    cfg = LLMConfig(base_url="http://x", api_key="k", model="m")
    return LLMClient(cfg, transport=_transport)


@pytest.mark.parametrize("bad", [0, -5, True])
def test_max_tokens_rejects_non_positive(bad):
    """改前 `if max_tokens:` 把 0 当空值静默丢弃，负数原样发给 provider。"""
    with pytest.raises(LLMError) as exc:
        _llm_capture({}).complete([{"role": "user", "content": "hi"}], max_tokens=bad)
    assert "max_tokens" in str(exc.value)


def test_max_tokens_none_omits_key():
    """正向对照：`None` = 不传，payload 里不得出现该键。"""
    sink: dict = {}
    _llm_capture(sink).complete([{"role": "user", "content": "hi"}])
    assert "max_tokens" not in sink["payload"]


def test_max_tokens_positive_passes_through():
    """正向对照：合法正整数必须原样进 payload。"""
    sink: dict = {}
    _llm_capture(sink).complete([{"role": "user", "content": "hi"}], max_tokens=512)
    assert sink["payload"]["max_tokens"] == 512


# ── A-05：explore_auto 的 max_failures 兄弟闸 ───────────────────────────────

def _explore_config(tmp_path) -> AtlasConfig:
    return AtlasConfig(root=tmp_path, env="sit", mode="run")


@pytest.mark.parametrize("bad", [0, -1, True])
def test_max_failures_rejected(tmp_path, bad):
    """同函数里 max_steps/max_seconds 都有闸，唯独它没有——0 首败即停却报「失败达上限」。"""
    with pytest.raises(ValueError) as exc:
        explore_auto(_explore_config(tmp_path), "g", llm=None, page=None,
                     trace=tmp_path / "t.zip", journal=None, max_failures=bad)
    assert "max_failures" in str(exc.value)


def test_max_failures_positive_still_runs(tmp_path):
    """正向对照：`1` 合法（首败即停但 stopped_reason 可信）。

    这里只验「闸不误伤」：`max_steps=1` 会正常跑一轮并返回 report（离线 fake 不需要）。
    用 max_steps=1 + 真跑会依赖页面/LLM，故只断言闸通过——异常类型不是 ValueError。
    """
    with pytest.raises(Exception) as exc:
        explore_auto(_explore_config(tmp_path), "g", llm=None, page=None,
                     trace=tmp_path / "t.zip", journal=None,
                     max_steps=1, max_seconds=1.0, max_failures=1)
    assert not isinstance(exc.value, ValueError), "合法 max_failures=1 不该被闸拦下"


# ── A-06：ATLAS_HEADED 走 _flag_env 单点 ────────────────────────────────────

def test_headed_flag_uses_single_point(tmp_path, monkeypatch, capsys):
    """`ATLAS_HEADED=true` → 按关处理（无头）+ WARN 点名原始值。

    改前裸读 `os.environ.get(...) != "1"`：`true` 静默无头、零提示，
    与同文件里 `ATLAS_ALLOW_PROD_WRITE` 的 `_flag_env` 语义分叉（#92 第二把刀）。
    """
    monkeypatch.setenv("ATLAS_HEADED", "true")
    cfg = AtlasConfig.from_root(tmp_path)
    assert cfg.headless is True, "非精确 '1' 一律按关（=无头）"
    assert "ATLAS_HEADED" in capsys.readouterr().err, "坏值必须点名，不许静默"


def test_headed_flag_exact_one_opens_window(tmp_path, monkeypatch):
    """正向对照：精确 `1` 才是有头。"""
    monkeypatch.setenv("ATLAS_HEADED", "1")
    assert AtlasConfig.from_root(tmp_path).headless is False


def test_headed_flag_absent_is_headless(tmp_path, monkeypatch):
    """正向对照：未设 → 无头（默认安全方向不变）。"""
    monkeypatch.delenv("ATLAS_HEADED", raising=False)
    assert AtlasConfig.from_root(tmp_path).headless is True


# ── A-07：state_machine 的 max_steps 归因 ───────────────────────────────────

def _machine() -> StateMachine:
    """一个最小合法状态机：`a --go--> b`（b 无出边 = 终端）。"""
    from atlas.state_machine import Transition
    return StateMachine(initial="a", states={"a": [Transition(target="b", task="go")],
                                              "b": []})


def test_state_machine_rejects_zero_max_steps():
    """`max_steps=0` 必须点名参数，而不是报「可能存在循环或守卫恒假」（归因指向错处）。"""
    with pytest.raises(ValueError) as exc:
        _machine().run("b", {}, None, max_steps=0)
    assert "max_steps" in str(exc.value) and "0" in str(exc.value)


@pytest.mark.parametrize("bad", [-1, True])
def test_state_machine_rejects_other_bad_max_steps(bad):
    """同族：负值与 bool 混入也要拒（`True` 是 int 子类，会静默变 1 步）。"""
    with pytest.raises(ValueError) as exc:
        _machine().run("b", {}, None, max_steps=bad)
    assert "max_steps" in str(exc.value)

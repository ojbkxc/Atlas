"""gap12 W2：自主探索循环离线判据（fake LLM + fake page，零网络零浏览器）。

判据面（`docs/gaps/gap12-llm-autonomous-explore.md` E.4–E.9）：白名单/风险门/闭环/
终止条件/生产闸/留痕。每条负向断言带正向对照（同一 fake 换个 reply 就应相反）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from atlas import autopilot, llm
from atlas.agent import AtlasAgent
from atlas.experience import ExperienceJournal

SNAP = '- button "Save"\n- button "Add"\n- textbox "Search"\n'


class FakeLocator:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    def click(self, timeout=None):
        if self.page.fail_click:
            raise TimeoutError("click boom")
        self.page.actions.append(("click", self.sel))

    def fill(self, value):
        self.page.actions.append(("fill", self.sel, value))


class _Keyboard:
    @staticmethod
    def press(key):
        pass


class FakePage:
    def __init__(self, snapshot=SNAP):
        self.url = "http://sit/list"
        self.actions: list = []
        self._snap = snapshot
        self.fail_click = False
        self.keyboard = _Keyboard()

    def aria_snapshot(self):
        return self._snap

    def get_by_role(self, role, name=None, **kw):
        return FakeLocator(self, (role, name))

    def goto(self, url, **kw):
        self.url = url
        self.actions.append(("goto", url))

    def wait_for_timeout(self, ms):
        self.actions.append(("wait", ms))

    def title(self):
        return "T"


class FakeLLM:
    """按序吐 reply；用尽后恒答 done（保证循环能停）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list = []

    def complete(self, messages, **kw):
        self.prompts.append(messages)
        return self.replies.pop(0) if self.replies else '{"action":"done"}'


@pytest.fixture
def env(tmp_path):
    config = SimpleNamespace(is_production=False, reports_dir=tmp_path / "rep",
                             artifacts_dir=tmp_path / "art")
    return SimpleNamespace(config=config, journal=ExperienceJournal(tmp_path / "exp"),
                           trace=tmp_path / "rep" / "explore.jsonl", tmp_path=tmp_path)


def _run(env, replies, **kw):
    page = kw.pop("page", None) or FakePage()
    llm_fake = FakeLLM(replies)
    report = autopilot.explore_auto(env.config, "探索列表页", llm=llm_fake,
                                    page=page, trace=env.trace, journal=env.journal, **kw)
    return report, page, llm_fake


# ===================== parse_action：白名单与形态 =====================

def test_parse_action_rejects_non_whitelist():
    with pytest.raises(autopilot.ActionRejected) as ei:
        autopilot.parse_action('{"action":"eval","js":"document.cookie"}')
    assert "白名单" in str(ei.value)


def test_parse_action_requires_role_and_name_for_click():
    with pytest.raises(autopilot.ActionRejected) as ei:
        autopilot.parse_action('{"action":"click","name":"Add"}')
    assert "role+name" in str(ei.value)


def test_parse_action_rejects_non_json():
    with pytest.raises(autopilot.ActionRejected):
        autopilot.parse_action("我先看看页面再决定")


def test_parse_action_accepts_fenced_json():
    assert autopilot.parse_action('```json\n{"action":"done"}\n```') == {"action": "done"}


def test_parse_action_accepts_whitelist_click():
    act = autopilot.parse_action('{"action":"click","role":"button","name":"Add"}')
    assert act["action"] == "click"


# ===================== E.4 白名单动作才执行 =====================

def test_non_whitelist_action_never_executes(env):
    report, page, _ = _run(env, ['{"action":"eval","js":"1"}',
                                 '{"action":"click","role":"button","name":"Add"}',
                                 '{"action":"done"}'])
    assert report.rejected == 1
    assert ("click", ("button", "Add")) in page.actions      # 正向对照腿：合法动作照跑
    assert report.stopped_reason == "done"


# ===================== E.5 风险门：写操作停 =====================

def test_write_action_blocked_by_approval(env):
    report, page, _ = _run(env, ['{"action":"click","role":"button","name":"Save"}'])
    assert report.stopped_reason == "approval_required"
    assert page.actions == []                                # 没有任何点击发生
    assert "审批拦截" in report.steps[-1]["note"]


def test_write_action_runs_when_approved(env):
    report, page, _ = _run(env, ['{"action":"click","role":"button","name":"Save"}',
                                 '{"action":"done"}'], approved=True)
    assert ("click", ("button", "Save")) in page.actions     # 显式批准后可执行


def test_plain_click_is_low_risk_and_runs(env):
    report, page, _ = _run(env, ['{"action":"click","role":"button","name":"Add"}',
                                 '{"action":"done"}'])
    assert ("click", ("button", "Add")) in page.actions
    assert report.stopped_reason == "done"


# ===================== E.6 全链闭环 + 沉淀 =====================

def test_happy_path_records_checkpoints_and_compiles_drafts(env):
    report, page, llm_fake = _run(env, [
        '{"action":"click","role":"button","name":"Add"}',
        '{"action":"fill","role":"textbox","name":"Search","value":"M7026817"}',
        '{"action":"record","note":"列表已就绪"}',
        '{"action":"done"}'])
    assert report.stopped_reason == "done"
    assert ("fill", ("textbox", "Search"), "M7026817") in page.actions
    assert report.drafts["steps"] == 3                       # 三个执行步各一个 checkpoint
    assert env.trace.is_file()
    # 提示词确实带上了目标与候选（可核性）
    assert "探索列表页" in llm_fake.prompts[0][1]["content"]
    assert "Add" in llm_fake.prompts[0][1]["content"]


# ===================== E.7 终止条件 =====================

def test_max_steps_stops_loop(env):
    report, _, _ = _run(env, ['{"action":"record"}'] * 5, max_steps=2)
    assert report.stopped_reason == "max_steps"
    assert len(report.steps) == 2


def test_max_failures_stops_loop(env):
    page = FakePage()
    page.fail_click = True                                   # 每次点击都超时
    report, _, _ = _run(env, ['{"action":"click","role":"button","name":"Add"}'] * 5,
                        page=page, max_failures=3)
    assert report.stopped_reason == "max_failures"
    assert report.failures == 3


def test_max_seconds_stops_loop(env):
    ticks = iter([0.0, 0.0, 999.0, 999.0, 999.0])
    report, _, _ = _run(env, ['{"action":"record"}'] * 5, max_seconds=10,
                        clock=lambda: next(ticks, 999.0))
    assert report.stopped_reason == "max_seconds"


# ===================== E.8 生产闸 =====================

def test_production_is_refused(env):
    env.config.is_production = True
    with pytest.raises(PermissionError) as ei:
        _run(env, ['{"action":"done"}'])
    assert "生产环境" in str(ei.value)


# ===================== E.9 决策留痕（给了 agent 就落盘） =====================

def test_decisions_are_recorded_when_agent_given(env):
    agent = AtlasAgent(journal=env.tmp_path / "decisions.jsonl")
    report, _, _ = _run(env, ['{"action":"click","role":"button","name":"Save"}'],
                        agent=agent)
    assert report.stopped_reason == "approval_required"
    assert (env.tmp_path / "decisions.jsonl").is_file()


def test_llm_errors_propagate_not_swallowed(env):
    class BoomLLM:
        def complete(self, messages, **kw):
            raise llm.LLMError("LLM HTTP 500：boom")
    with pytest.raises(llm.LLMError):
        autopilot.explore_auto(env.config, "g", llm=BoomLLM(), page=FakePage(),
                               trace=env.trace, journal=env.journal)


# ===================== E.10 CLI 入口（explore --auto，离线假会话） =====================

def _fake_runtime(monkeypatch, page):
    """离线替身：DemoRuntime 换成不碰浏览器的假会话（与 test_explore_trace 同模式）。"""
    closed = []

    class _Runtime:
        def __init__(self, config, login=True, account=None):
            self.account = account or "fake"
            self.actor = SimpleNamespace(page=page)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)
            return False

    monkeypatch.setattr("atlas.runtime.DemoRuntime", _Runtime)
    return closed


def _satisfy_session_precheck(tmp_path, monkeypatch):
    """把入口输入预检所需的三个输入配齐（base_url / 旧仓 / DemoApi）。

    2026-11-16 起预检序列收敛到 `runtime.precheck_session_inputs`，CLI 各入口都会真跑
    它（此前 `_explore_auto` 只查账号，缺 URL 也能一路跑到假 runtime）。本文件的测验证
    的是探索循环本身，不是预检，所以用这个 helper 把输入补齐而不是在每条测试里重复。

    刻意造**真目录**（不是 patch 判据）：patch 判据会让「预检是否真被执行」变成不可见，
    而这正是收敛那波要保住的性质。
    """
    legacy = tmp_path / "legacy"
    (legacy / "legacy-web").mkdir(parents=True, exist_ok=True)
    api = tmp_path / "api"
    (api / "scripts").mkdir(parents=True, exist_ok=True)
    (api / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    monkeypatch.setenv("DEMO_URL_SIT", "https://sit.example/")
    monkeypatch.setenv("ATLAS_SESSION_ACCOUNT", "someone")
    # 显式声明插件：避免 fallback WARN 混进被测的 stdout/stderr 断言
    monkeypatch.setenv("ATLAS_PLUGIN", "demo")


def test_keyboard_interrupt_propagates_with_prior_decisions_kept(env):
    """E.9：控制流异常原样传播，且中断前已落痕的决策不许被吞（#114/#85 同纪律）。"""
    class _KiPage(FakePage):
        def get_by_role(self, role, name=None, **kw):
            class _Loc:
                def click(self, timeout=None):
                    raise KeyboardInterrupt()
            return _Loc()

    agent = AtlasAgent(journal=env.tmp_path / "decisions.jsonl")
    with pytest.raises(KeyboardInterrupt):
        autopilot.explore_auto(env.config, "g", llm=FakeLLM(
            ['{"action":"record","note":"先记一步"}',
             '{"action":"click","role":"button","name":"Add"}']),
            page=_KiPage(), trace=env.trace, journal=env.journal, agent=agent)
    # 下一步的点击抛 KI：中断前的 record 决策已在盘上，KI 不被吞成「执行失败」
    text = (env.tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    assert "record" in text


def test_cli_auto_without_goal_is_user_error(tmp_path, capsys, monkeypatch):
    from atlas.cli import main
    assert main(["--root", str(tmp_path), "explore", "--auto"]) == 2
    assert "--goal" in capsys.readouterr().err


def test_cli_auto_without_llm_config_names_missing_keys(tmp_path, capsys, monkeypatch):
    from atlas.cli import main
    for key in ("ATLAS_LLM_BASE_URL", "ATLAS_LLM_API_KEY", "ATLAS_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert main(["--root", str(tmp_path), "explore", "--auto", "--goal", "探索列表页"]) == 2
    assert "ATLAS_LLM_BASE_URL" in capsys.readouterr().err


def test_cli_auto_refuses_production_before_opening_browser(tmp_path, capsys, monkeypatch):
    """生产闸早于开浏览器：假会话一旦被进入就记账，断言它压根没被进入。"""
    from atlas.cli import main
    entered = []

    class _Runtime:
        def __init__(self, *a, **kw):
            entered.append(True)

    monkeypatch.setattr("atlas.runtime.DemoRuntime", _Runtime)
    assert main(["--root", str(tmp_path), "--env", "prod-eu", "explore", "--auto",
                 "--goal", "g"]) == 1
    assert "生产" in capsys.readouterr().err and entered == []


def test_cli_auto_full_chain_lands_pending_review_drafts(tmp_path, capsys, monkeypatch):
    """目标进去、草稿出来：CLI 端到端（假浏览器 + 假 LLM 客户端，零网络零浏览器）。"""
    from atlas.cli import main
    page = FakePage()
    _fake_runtime(monkeypatch, page)

    class _Client:
        def __init__(self, config, **kw):
            self.replies = ['{"action":"click","role":"button","name":"Add"}',
                            '{"action":"record","note":"列表已就绪"}',
                            '{"action":"done"}']

        def complete(self, messages, **kw):
            return self.replies.pop(0) if self.replies else '{"action":"done"}'

    monkeypatch.setattr(llm, "LLMClient", _Client)
    monkeypatch.setenv("ATLAS_LLM_BASE_URL", "http://gw/v1")
    monkeypatch.setenv("ATLAS_LLM_API_KEY", "k")
    monkeypatch.setenv("ATLAS_LLM_MODEL", "m")
    # 入口输入预检（2026-11-16 收敛到 runtime.precheck_session_inputs）现在真的会跑，
    # 缺 base_url/旧仓/DemoApi 任一条都会 rc 2——本测验的是「探索链路把事情做完」，
    # 不是预检，所以把这三个输入配齐（此前这里能过，是因为 _explore_auto 压根没预检）。
    _satisfy_session_precheck(tmp_path, monkeypatch)
    trace = tmp_path / "trace.jsonl"
    assert main(["--root", str(tmp_path), "explore", "--auto", "--goal", "探索列表页",
                 "--trace", str(trace)]) == 0
    raw = capsys.readouterr().out
    out = json.loads(raw[raw.index("{"):])   # checkpoint 行先于 JSON 摘要在 stdout（真机同形）
    assert out["stopped_reason"] == "done" and out["drafts"]["steps"] == 2
    assert ("click", ("button", "Add")) in page.actions
    assert trace.is_file()
    assert (tmp_path / "reports" / "experience_drafts.jsonl").is_file()
    assert (tmp_path / "reports" / "agent_decisions.jsonl").is_file()


# ===================== gap13：定位与组件库同源（不留第二把尺） =====================

def test_click_uses_shared_locator_resolution_exact_name(env):
    """autopilot 的 click 走 `locators.resolve`：role 的 name 精确匹配（点错比找不到更坏）。"""
    seen: list = []

    class _Page(FakePage):
        def get_by_role(self, role, name=None, **kw):
            seen.append((role, name, kw))
            return FakeLocator(self, (role, name))

    report, page, _ = _run(env, ['{"action":"click","role":"button","name":"Add"}',
                                 '{"action":"done"}'], page=_Page())
    assert report.stopped_reason == "done"
    assert seen == [("button", "Add", {"exact": True})]


# ===================== C4：URL 出境/落盘前净化（红线 #2） =====================

def test_observe_sanitizes_url_before_llm_egress(env):
    """`_observe` 的 url 出境（拼进 LLM user 消息）——真实 SIT 第一轮实测带 SSO 票据，
    不过刀就是把凭据发给外部端点（无法回收）。同时路由坐标必须保留（LLM 靠它判断在哪页）。"""
    class _TicketPage(FakePage):
        def __init__(self):
            super().__init__()
            self.url = "https://sit.example/?ticket=ST-3547-secret#/tsp/views/parts/list?name=sparePartList"

    report, page, llm_fake = _run(env, ['{"action":"done"}'], page=_TicketPage())
    prompt = json.dumps(llm_fake.prompts[0], ensure_ascii=False)
    assert "ST-3547-secret" not in prompt, "SSO 票据发到 LLM 端点 = 凭据出境"
    assert "/tsp/views/parts/list" in prompt, "页面路由坐标必须保留（LLM 据此导航）"
    assert "ticket=<redacted>" in prompt


def test_checkpoint_url_is_sanitized_on_disk(env):
    """trace 落盘的 url 过刀：否则 --compile 会把票据抄进审核塔草稿。"""
    class _TicketPage(FakePage):
        def __init__(self):
            super().__init__()
            self.url = "https://sit.example/?ticket=ST-9&docNo=SO-1#/list?page=2"

    _run(env, ['{"action":"click","role":"button","name":"Add"}', '{"action":"done"}'],
         page=_TicketPage())
    raw = env.trace.read_text(encoding="utf-8")
    assert "ST-9" not in raw, "trace 落盘带票据 = 红线 #2 违反"
    assert "ticket=<redacted>" in raw and "docNo=SO-1" in raw, "逐键遮蔽，业务参数保留"

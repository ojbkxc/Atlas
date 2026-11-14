"""wave3 P0-A：AI 自主功能测试闭环的判据面（gap14）。

覆盖：assert 四类判据（元素可见 / 文案匹配 / 接口状态码与结构 / 跨模块值比对）、
record 存改前值、parse_action 形态校验、失败归类走 classify_failure 单一真相、
runs.jsonl 证据落盘（请求实参过净化刀）、坑点自动提炼（未命中 approved 才提炼、
坏摘要点名跳过）、以及 allure/summary 消费面。

离线：fake LLM + fake page（零网络零浏览器，localhost 也算网络所以不打真端口）。
每条负向断言带正向对照。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from atlas import autopilot, llm
from atlas.experience import ExperienceJournal

SNAP = '- button "Add"\n- text "共 3 行"\n'


class FakeLocator:
    def __init__(self, page, sel, count=1, visible=True, text=""):
        self.page, self.sel = page, sel
        self._count, self._visible, self._text = count, visible, text

    def click(self, timeout=None):
        if self.page.fail_click:
            raise TimeoutError("click boom")
        self.page.actions.append(("click", self.sel))

    def fill(self, value):
        self.page.actions.append(("fill", self.sel, value))

    def count(self):
        return self._count

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text


class _Keyboard:
    @staticmethod
    def press(key):
        pass


class FakePage:
    """带事件面（on/emit）与定位形态（count/visible/text 可配）的假页面。"""

    def __init__(self, snapshot=SNAP):
        self.url = "http://sit/list"
        self.actions: list = []
        self._snap = snapshot
        self.fail_click = False
        self.keyboard = _Keyboard()
        self.handlers: dict = {}
        self.counts: dict = {}    # 定位键 → 匹配数
        self.visibles: dict = {}  # 定位键 → 可见性
        self.texts: dict = {}     # 定位键 → 文本

    def aria_snapshot(self):
        return self._snap

    def _opts(self, key):
        return {"count": self.counts.get(key, 1),
                "visible": self.visibles.get(key, True),
                "text": self.texts.get(key, "")}

    def get_by_role(self, role, name=None, **kw):
        return FakeLocator(self, (role, name), **self._opts(("role", name)))

    def get_by_label(self, label, **kw):
        return FakeLocator(self, ("label", label), **self._opts(("label", label)))

    def get_by_test_id(self, testid, **kw):
        return FakeLocator(self, ("testid", testid), **self._opts(("testid", testid)))

    def locator(self, css):
        return FakeLocator(self, ("css", css), **self._opts(("css", css)))

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, obj):
        for handler in self.handlers.get(event, []):
            handler(obj)

    def goto(self, url, **kw):
        self.url = url
        self.actions.append(("goto", url))

    def wait_for_timeout(self, ms):
        self.actions.append(("wait", ms))

    def title(self):
        return "T"


class FakeRequest:
    def __init__(self, method="POST", url="http://sit/api/orders",
                 post_data='{"user":"u"}'):
        self.method, self.url, self.post_data = method, url, post_data


class FakeResponse:
    def __init__(self, url="http://sit/api/orders", status=200,
                 ctype="application/json", body='{"data":{"code":0,"list":[1,2]}}'):
        self.url, self.status = url, status
        self._ctype, self._body = ctype, body

    def headers(self):
        return {"content-type": self._ctype}

    def text(self):
        return self._body


class FakeLLM:
    """按序吐 reply；用尽后恒答 done（保证循环能停）。

    before_reply 钩子：在每次决策调用前注入页面事件（请求/响应观察必须在
    _wire_events 注册之后才进证据面，钩子把时序钉在循环内部）。
    """

    def __init__(self, replies, before_reply=None):
        self.replies = list(replies)
        self.prompts: list = []
        self.before_reply = before_reply

    def complete(self, messages, **kw):
        self.prompts.append(messages)
        if self.before_reply is not None:
            self.before_reply(messages)
        return self.replies.pop(0) if self.replies else '{"action":"done"}'


@pytest.fixture
def env(tmp_path):
    config = SimpleNamespace(is_production=False, env="sit",
                             reports_dir=tmp_path / "rep",
                             artifacts_dir=tmp_path / "art")
    return SimpleNamespace(config=config, journal=ExperienceJournal(tmp_path / "exp"),
                           trace=tmp_path / "rep" / "explore.jsonl", tmp_path=tmp_path)


def _ctx():
    return autopilot.ExploreContext()


def _runs(env):
    """runs.jsonl 是多行 JSONL：取最后一行（同文件多 run 时逐个核对）。"""
    lines = [line for line in (env.tmp_path / "rep" / "runs.jsonl")
             .read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1])


def _drafts(env):
    path = env.tmp_path / "exp" / "experience_drafts.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ===================== parse_action：assert 形态判据 =====================

def test_parse_action_assert_requires_kind():
    with pytest.raises(autopilot.ActionRejected, match="kind"):
        autopilot.parse_action('{"action":"assert","note":"x"}')


def test_parse_action_assert_unknown_kind():
    with pytest.raises(autopilot.ActionRejected, match="kind"):
        autopilot.parse_action('{"action":"assert","kind":"eval"}')


def test_parse_action_visible_requires_locator():
    with pytest.raises(autopilot.ActionRejected):
        autopilot.parse_action('{"action":"assert","kind":"visible"}')


def test_parse_action_text_requires_contains():
    with pytest.raises(autopilot.ActionRejected, match="contains"):
        autopilot.parse_action('{"action":"assert","kind":"text"}')


def test_parse_action_api_requires_status_or_path():
    with pytest.raises(autopilot.ActionRejected):
        autopilot.parse_action('{"action":"assert","kind":"api"}')


def test_parse_action_api_status_must_be_int():
    with pytest.raises(autopilot.ActionRejected, match="status"):
        autopilot.parse_action('{"action":"assert","kind":"api","status":"200"}')


def test_parse_action_api_path_requires_type_or_equals():
    with pytest.raises(autopilot.ActionRejected):
        autopilot.parse_action('{"action":"assert","kind":"api","json_path":"data.code"}')


def test_parse_action_compare_requires_key_or_locator_a():
    with pytest.raises(autopilot.ActionRejected):
        autopilot.parse_action('{"action":"assert","kind":"compare"}')


def test_parse_action_compare_key_requires_locator():
    with pytest.raises(autopilot.ActionRejected, match="locator"):
        autopilot.parse_action('{"action":"assert","kind":"compare","key":"total"}')


def test_parse_action_compare_locator_a_requires_b():
    with pytest.raises(autopilot.ActionRejected, match="locator_b"):
        autopilot.parse_action('{"action":"assert","kind":"compare","locator_a":".a"}')


def test_parse_action_compare_bad_op():
    with pytest.raises(autopilot.ActionRejected, match="op"):
        autopilot.parse_action('{"action":"assert","kind":"compare","key":"k","locator":".a","op":"gt"}')


def test_parse_action_record_key_requires_scalar_value():
    with pytest.raises(autopilot.ActionRejected, match="value"):
        autopilot.parse_action('{"action":"record","key":"total"}')
    with pytest.raises(autopilot.ActionRejected, match="value"):
        autopilot.parse_action('{"action":"record","key":"total","value":{"a":1}}')


def test_parse_action_accepts_all_four_kinds():
    assert autopilot.parse_action(
        '{"action":"assert","kind":"visible","role":"button","name":"Add"}')["kind"] == "visible"
    assert autopilot.parse_action(
        '{"action":"assert","kind":"text","contains":"ok"}')["kind"] == "text"
    assert autopilot.parse_action(
        '{"action":"assert","kind":"api","status":200}')["kind"] == "api"
    assert autopilot.parse_action(
        '{"action":"assert","kind":"compare","key":"k","locator":".a"}')["kind"] == "compare"


# ===================== visible：元素存在/可见 =====================

def test_assert_visible_pass():
    detail = autopilot._execute(FakePage(),
                                {"action": "assert", "kind": "visible",
                                 "role": "button", "name": "Add"}, _ctx())
    assert detail["count"] == 1 and detail["locator"] == 'role=button[name="Add"]'


def test_assert_visible_fail_count_zero():
    page = FakePage()
    page.counts[("role", "Add")] = 0
    with pytest.raises(AssertionError, match="元素可见"):
        autopilot._execute(page, {"action": "assert", "kind": "visible",
                                  "role": "button", "name": "Add"}, _ctx())


def test_assert_visible_fail_hidden():
    page = FakePage()
    page.visibles[("role", "Add")] = False
    with pytest.raises(AssertionError, match="元素可见"):
        autopilot._execute(page, {"action": "assert", "kind": "visible",
                                  "role": "button", "name": "Add"}, _ctx())


def test_assert_visible_css():
    detail = autopilot._execute(FakePage(),
                                {"action": "assert", "kind": "visible",
                                 "css": ".el-table"}, _ctx())
    assert detail["locator"] == "css=.el-table"


# ===================== text：文案匹配 =====================

def test_assert_text_page_pass():
    detail = autopilot._execute(FakePage(),
                                {"action": "assert", "kind": "text",
                                 "contains": "共 3 行"}, _ctx())
    assert detail["where"] == "页面快照"


def test_assert_text_page_fail():
    with pytest.raises(AssertionError, match="文案匹配"):
        autopilot._execute(FakePage(), {"action": "assert", "kind": "text",
                                        "contains": "共 9 行"}, _ctx())


def test_assert_text_element_pass():
    page = FakePage()
    page.texts[("role", "Status")] = "已保存"
    detail = autopilot._execute(page, {"action": "assert", "kind": "text",
                                       "role": "textbox", "name": "Status",
                                       "contains": "已保存"}, _ctx())
    assert detail["where"].startswith("元素")


def test_assert_text_element_missing():
    page = FakePage()
    page.counts[("role", "Status")] = 0
    with pytest.raises(AssertionError, match="不存在"):
        autopilot._execute(page, {"action": "assert", "kind": "text",
                                  "role": "textbox", "name": "Status",
                                  "contains": "x"}, _ctx())


# ===================== api：接口状态码与结构 =====================

def _ctx_with_response(status=200, body='{"data":{"code":0,"list":[1,2]}}',
                       url="http://sit/api/orders"):
    ctx = _ctx()
    ctx.record_response(url, status, "application/json", body)
    return ctx


def test_assert_api_status_pass():
    detail = autopilot._execute(FakePage(),
                                {"action": "assert", "kind": "api", "status": 200},
                                _ctx_with_response())
    assert detail["status_ok"] is True


def test_assert_api_status_fail():
    with pytest.raises(AssertionError, match="接口状态码"):
        autopilot._execute(FakePage(), {"action": "assert", "kind": "api", "status": 200},
                           _ctx_with_response(status=500))


def test_assert_api_no_response():
    with pytest.raises(AssertionError, match="没有观察到任何响应"):
        autopilot._execute(FakePage(), {"action": "assert", "kind": "api", "status": 200},
                           _ctx())


def test_assert_api_json_path_type_pass():
    detail = autopilot._execute(FakePage(),
                                {"action": "assert", "kind": "api",
                                 "json_path": "data.list", "type": "array"},
                                _ctx_with_response())
    assert detail["type"] == "array"


def test_assert_api_json_path_type_fail():
    with pytest.raises(AssertionError, match="接口结构"):
        autopilot._execute(FakePage(),
                           {"action": "assert", "kind": "api",
                            "json_path": "data.list", "type": "object"},
                           _ctx_with_response())


def test_assert_api_json_path_equals_pass():
    detail = autopilot._execute(FakePage(),
                                {"action": "assert", "kind": "api",
                                 "json_path": "data.code", "equals": 0},
                                _ctx_with_response())
    assert detail["equals"] == 0


def test_assert_api_json_path_equals_fail():
    with pytest.raises(AssertionError, match="接口结构"):
        autopilot._execute(FakePage(),
                           {"action": "assert", "kind": "api",
                            "json_path": "data.code", "equals": 1},
                           _ctx_with_response())


def test_assert_api_bad_path_names_full_path():
    """路径打点走 tasks._dig 单一真相：坏路径点名整条路径（不复制第二份判据）。"""
    with pytest.raises(AssertionError, match="data.missing"):
        autopilot._execute(FakePage(),
                           {"action": "assert", "kind": "api",
                            "json_path": "data.missing", "type": "string"},
                           _ctx_with_response())


def test_assert_api_bad_json_body():
    with pytest.raises(AssertionError, match="不是合法 JSON"):
        autopilot._execute(FakePage(),
                           {"action": "assert", "kind": "api",
                            "json_path": "data.code", "type": "number"},
                           _ctx_with_response(body="not json at all"))


def test_assert_api_url_contains_picks_match():
    ctx = _ctx()
    ctx.record_response("http://sit/api/other", 500, "application/json", "{}")
    ctx.record_response("http://sit/api/orders", 200, "application/json",
                        '{"data":{"code":0}}')
    detail = autopilot._execute(FakePage(),
                                {"action": "assert", "kind": "api",
                                 "url_contains": "/api/orders", "status": 200}, ctx)
    assert detail["url"] == "http://sit/api/orders"


def test_assert_api_url_contains_no_match():
    ctx = _ctx_with_response()
    with pytest.raises(AssertionError, match="没有观察到任何响应"):
        autopilot._execute(FakePage(),
                           {"action": "assert", "kind": "api",
                            "url_contains": "/api/nope", "status": 200}, ctx)


# ===================== compare：跨模块值比对（改前值 vs 改后值） =====================

def test_record_stores_value_then_compare_neq_pass():
    page = FakePage()
    page.counts[("css", ".el-table tbody tr")] = 4
    ctx = _ctx()
    autopilot._execute(page, {"action": "record", "key": "total", "value": "3"}, ctx)
    detail = autopilot._execute(page,
                                {"action": "assert", "kind": "compare", "key": "total",
                                 "locator": ".el-table tbody tr", "op": "neq"}, ctx)
    assert detail["recorded"] == "3" and detail["current"] == "4"


def test_compare_eq_pass_numeric_coercion():
    """数值强制有牙：「3.0」(字符串) vs 3 (数字) 必须过——纯字符串比较会判
    「3.0」≠「3」而假红，只有数值强制路径能过（钉的是强制本身，不是恒真）。"""
    page = FakePage()
    page.counts[("css", ".el-table tbody tr")] = 3
    ctx = _ctx()
    autopilot._execute(page, {"action": "record", "key": "total", "value": "3.0"}, ctx)
    detail = autopilot._execute(page,
                                {"action": "assert", "kind": "compare", "key": "total",
                                 "locator": ".el-table tbody tr", "op": "eq"}, ctx)
    assert detail["op"] == "eq"


def test_compare_eq_fail_numeric_coercion_negative():
    """负向对照：「3.0」vs 4 必须 fail——数值强制不是一律放行（同型不同值）。"""
    page = FakePage()
    page.counts[("css", ".el-table tbody tr")] = 4
    ctx = _ctx()
    autopilot._execute(page, {"action": "record", "key": "total", "value": "3.0"}, ctx)
    with pytest.raises(AssertionError, match="值比对"):
        autopilot._execute(page,
                           {"action": "assert", "kind": "compare", "key": "total",
                            "locator": ".el-table tbody tr", "op": "eq"}, ctx)


def test_compare_eq_fail():
    page = FakePage()
    page.counts[("css", ".el-table tbody tr")] = 4
    ctx = _ctx()
    autopilot._execute(page, {"action": "record", "key": "total", "value": "3"}, ctx)
    with pytest.raises(AssertionError, match="值比对"):
        autopilot._execute(page,
                           {"action": "assert", "kind": "compare", "key": "total",
                            "locator": ".el-table tbody tr", "op": "eq"}, ctx)


def test_compare_unrecorded_key():
    with pytest.raises(AssertionError, match="未记录"):
        autopilot._execute(FakePage(),
                           {"action": "assert", "kind": "compare", "key": "nope",
                            "locator": ".el-table tbody tr", "op": "eq"}, _ctx())


def test_compare_locator_a_b_eq():
    page = FakePage()
    page.counts[("css", ".a")] = 5
    page.counts[("css", ".b")] = 5
    detail = autopilot._execute(page,
                                {"action": "assert", "kind": "compare",
                                 "locator_a": ".a", "locator_b": ".b", "op": "eq"}, _ctx())
    assert detail["a"] == "5" and detail["b"] == "5"


def test_compare_locator_a_b_neq_fail():
    page = FakePage()
    page.counts[("css", ".a")] = 5
    page.counts[("css", ".b")] = 6
    with pytest.raises(AssertionError, match="值比对"):
        autopilot._execute(page,
                           {"action": "assert", "kind": "compare",
                            "locator_a": ".a", "locator_b": ".b", "op": "eq"}, _ctx())


def test_compare_read_text():
    page = FakePage()
    page.texts[("css", ".total")] = "1200 元"
    ctx = _ctx()
    autopilot._execute(page, {"action": "record", "key": "amount", "value": "1200 元"}, ctx)
    detail = autopilot._execute(page,
                                {"action": "assert", "kind": "compare", "key": "amount",
                                 "locator": ".total", "read": "text", "op": "eq"}, ctx)
    assert detail["current"] == "1200 元"


# ===================== 失败归类：classify_failure 单一真相 =====================

def test_assert_failure_classified_assertion_via_loop(env):
    """失败步骤的 fail_class 由 recorder→classify_failure 单点产出（本模块零判词）。"""
    page = FakePage()
    page.counts[("role", "Add")] = 0
    autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"assert","kind":"visible","role":"button","name":"Add"}',
        '{"action":"done"}']), page=page, trace=env.trace, journal=env.journal)
    step = _runs(env)["steps"][0]
    assert step["fail_class"] == "assertion"


# ===================== runs.jsonl：证据与缺陷落盘 =====================

def _emit_request_response(page):
    """决策调用前注入请求/响应事件（观察注册在循环内，时序必须发生在 _wire_events 之后）。"""
    def hook(messages):
        page.emit("request", FakeRequest(post_data='{"user":"u","token":"abc123-secret-xyz"}'))
        page.emit("response", FakeResponse(status=500))
    return hook


def test_failed_assert_lands_runs_jsonl_with_evidence(env):
    page = FakePage()
    page.counts[("role", "Add")] = 0
    report = autopilot.explore_auto(env.config, "g", llm=FakeLLM(
        ['{"action":"assert","kind":"visible","role":"button","name":"Add"}',
         '{"action":"done"}'], before_reply=_emit_request_response(page)),
        page=page, trace=env.trace, journal=env.journal)
    runs = _runs(env)
    assert runs["status"] == "failed" and runs["mode"] == "explore-auto"
    assert runs["case"] == "g"
    step = runs["steps"][0]
    assert step["ok"] is False and step["op"] == "assert"
    assert step["fail_class"] == "assertion"
    evidence = step["evidence"]
    assert evidence["requests"][0]["method"] == "POST"
    assert evidence["responses"][0]["status"] == 500
    assert evidence["assert_kind"] == "visible"
    assert report.failures == 1


def test_request_post_data_sanitized_on_disk(env):
    """请求实参落盘过净化刀（红线 #2）：post_data 里的凭据不进 runs.jsonl。"""
    page = FakePage()
    page.counts[("role", "Add")] = 0

    def hook(messages):
        page.emit("request", FakeRequest(
            url="http://sit/api/orders?ticket=ST-9&docNo=SO-1",
            post_data='{"user":"u","token":"abc123-secret-xyz"}'))

    autopilot.explore_auto(env.config, "g", llm=FakeLLM(
        ['{"action":"assert","kind":"visible","role":"button","name":"Add"}',
         '{"action":"done"}'], before_reply=hook),
        page=page, trace=env.trace, journal=env.journal)
    raw = (env.tmp_path / "rep" / "runs.jsonl").read_text(encoding="utf-8")
    assert "abc123-secret-xyz" not in raw, "凭据落盘 = 红线 #2 违反"
    assert "ST-9" not in raw, "SSO 票据落盘 = 红线 #2 违反"
    assert "ticket=<redacted>" in raw and "docNo=SO-1" in raw, "逐键遮蔽，业务参数保留"


def test_passed_assert_lands_runs_jsonl_passed(env):
    report = autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"assert","kind":"text","contains":"共 3 行"}',
        '{"action":"done"}']), page=FakePage(), trace=env.trace, journal=env.journal)
    runs = _runs(env)
    assert runs["status"] == "passed"
    assert runs["steps"][0]["ok"] is True and runs["steps"][0]["op"] == "assert"
    assert report.asserts == 1


def test_click_failure_lands_runs_jsonl_interaction(env):
    page = FakePage()
    page.fail_click = True
    autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"click","role":"button","name":"Add"}',
        '{"action":"done"}', '{"action":"done"}']), page=page,
        trace=env.trace, journal=env.journal, max_failures=1)
    runs = _runs(env)
    assert runs["status"] == "failed"
    assert runs["steps"][0]["fail_class"] == "interaction"
    assert "evidence" in runs["steps"][0]


def test_crash_path_closes_recorder_interrupted(env):
    """崩溃路径也落盘（finally）：KI → interrupted，LLM 错 → failed，不洗绿。"""
    class _KiPage(FakePage):
        def get_by_role(self, role, name=None, **kw):
            class _Loc:
                def click(self, timeout=None):
                    raise KeyboardInterrupt()
            return _Loc()

    with pytest.raises(KeyboardInterrupt):
        autopilot.explore_auto(env.config, "g", llm=FakeLLM([
            '{"action":"click","role":"button","name":"Add"}']),
            page=_KiPage(), trace=env.trace, journal=env.journal)
    assert _runs(env)["status"] == "interrupted"

    class BoomLLM:
        def complete(self, messages, **kw):
            raise llm.LLMError("LLM HTTP 500：boom")

    with pytest.raises(llm.LLMError):
        autopilot.explore_auto(env.config, "g", llm=BoomLLM(), page=FakePage(),
                               trace=env.trace, journal=env.journal)
    assert _runs(env)["status"] == "failed"


# ===================== 坑点自动提炼 =====================

def test_unmatched_failure_extracts_pending_review_draft(env):
    page = FakePage()
    page.counts[("role", "Add")] = 0
    autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"assert","kind":"visible","role":"button","name":"Add"}',
        '{"trigger":"列表按钮不可见","symptom":"count=0","root_cause":"选择器漂移","remedy":"改选择器"}',
        '{"action":"done"}']), page=page, trace=env.trace, journal=env.journal)
    pitfalls = [d for d in _drafts(env) if d.get("kind") == "pitfall"]
    assert len(pitfalls) == 1
    row = pitfalls[0]
    assert row["status"] == "pending_review"
    assert row["failure_class"] == "assertion"
    assert row["exception_type"] == "AssertionError"
    ev = row["evidence"]
    assert ev["verification"] == {"command": "atlas explore --auto --goal g",
                                  "exit_code": 1, "assertions": 1, "verdict": "fail"}
    assert ev["summary"]["remedy"] == "改选择器"
    assert "requests" in ev and "responses" in ev and "snapshot" in ev


def test_matched_approved_pitfall_no_draft_and_note(env):
    """命中 approved 坑点的失败不重复提炼（match_pitfall 自愈准入同语义）。"""
    page = FakePage()
    page.counts[("role", "Add")] = 0
    knowledge = SimpleNamespace(match_pitfall=lambda exc, status=None: {"id": "known-1"})
    llm_fake = FakeLLM(['{"action":"assert","kind":"visible","role":"button","name":"Add"}',
                        '{"action":"done"}'])
    report = autopilot.explore_auto(env.config, "g", llm=llm_fake, page=page,
                                    trace=env.trace, journal=env.journal,
                                    knowledge=knowledge)
    assert _drafts(env) == []
    assert "命中已审核坑点" in report.steps[0]["note"]
    # 摘要调用没发生：LLM 只被问了动作决策（assert + done 两次）
    assert len(llm_fake.prompts) == 2


def test_bad_summary_skips_extraction_with_note(env):
    """LLM 摘要形态坏 → 不造草稿，步骤留痕点名跳过原因（不静默回落）。"""
    page = FakePage()
    page.counts[("role", "Add")] = 0
    report = autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"assert","kind":"visible","role":"button","name":"Add"}',
        '{"action":"done"}',      # 摘要被当成动作 JSON → 缺四字段 → 形态坏
        '{"action":"done"}']), page=page, trace=env.trace, journal=env.journal)
    assert _drafts(env) == []
    assert "坑点提炼跳过" in report.steps[0]["note"]


def test_summary_missing_field_rejected():
    with pytest.raises(autopilot.SummaryRejected, match="缺字段"):
        autopilot._parse_summary('{"trigger":"t","symptom":"s","root_cause":"r"}')


def test_summary_unknown_key_rejected():
    with pytest.raises(autopilot.SummaryRejected, match="未知键"):
        autopilot._parse_summary(
            '{"trigger":"t","symptom":"s","root_cause":"r","remedy":"m","extra":1}')


def test_summary_sanitized():
    summary = autopilot._parse_summary(
        '{"trigger":"t","symptom":"s","root_cause":"r","remedy":"http://x/?ticket=ST-9"}')
    assert "ST-9" not in summary["remedy"]


def test_llm_summary_error_skips_with_note_and_run_failed(env):
    class BoomAfterFirst:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return '{"action":"assert","kind":"visible","role":"button","name":"Add"}'
            raise llm.LLMError("LLM HTTP 500：boom")

    page = FakePage()
    page.counts[("role", "Add")] = 0
    with pytest.raises(llm.LLMError):
        autopilot.explore_auto(env.config, "g", llm=BoomAfterFirst(), page=page,
                               trace=env.trace, journal=env.journal)
    # 失败已落盘（提炼跳过不顶失败）
    runs = _runs(env)
    assert runs["status"] == "failed"
    assert runs["steps"][0]["fail_class"] == "assertion"


# ===================== 消费面：allure / summary 直接可吃 =====================

def test_ai_run_record_consumable_by_allure(env, tmp_path):
    from atlas.allure_report import emit_allure
    autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"assert","kind":"text","contains":"共 3 行"}',
        '{"action":"done"}']), page=FakePage(), trace=env.trace, journal=env.journal)
    out = emit_allure(env.tmp_path / "rep" / "runs.jsonl", tmp_path / "allure")
    assert out["results"] == 1


def test_ai_run_record_consumable_by_summary(env):
    from atlas.reporting import summarize
    autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"assert","kind":"text","contains":"共 3 行"}',
        '{"action":"done"}']), page=FakePage(), trace=env.trace, journal=env.journal)
    summary = summarize(env.tmp_path / "rep" / "runs.jsonl")
    assert summary["runs"] == 1
    assert summary["status"] == {"passed": 1}


def test_failed_ai_run_record_visible_in_summary_defects(env):
    from atlas.reporting import summarize
    page = FakePage()
    page.counts[("role", "Add")] = 0
    autopilot.explore_auto(env.config, "g", llm=FakeLLM([
        '{"action":"assert","kind":"visible","role":"button","name":"Add"}',
        '{"action":"done"}']), page=page, trace=env.trace, journal=env.journal)
    summary = summarize(env.tmp_path / "rep" / "runs.jsonl")
    assert summary["status"] == {"failed": 1}
    assert summary["fail_classes"] == {"assertion": 1}

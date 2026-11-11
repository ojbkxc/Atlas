"""C12 写不盲重试离线钉桩（design_c12 §5 全清单 + 评审必改 C12-A/B/C/D/E/F 追加项）。

判据单一真相在 `atlas/delivery.py`，这里逐行钉真值表与接线形态；全部离线（窄 fake），
不触网络/浏览器/凭据（AGENTS §2）。存量降级/heal/OSError 传播红线组由
`test_plugin_and_legacy.py` 全量复跑兜底（§6 兼容表）。
"""
import json
from pathlib import Path

import pytest

from atlas import Actor, delivery
from atlas import failures as fl
from atlas.abilities import CallApi
from atlas.config import AtlasConfig
from atlas.core import Channel, TaskMetadata
from atlas.creators import make_scripted_creator
from atlas.engine import ExecutionEngine
from atlas.failures import DeliveryUncertain, EnvBlocked, classify_failure, classify_text
from atlas.probes import CallableProbe, CreateQuota, DataProvider, ScriptedCreator
from atlas.recorder import RunRecorder


# ── 测试替身 ────────────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, payload=None):
        self._payload = payload or {"resultCode": "0", "data": {}}
    def json(self):
        return self._payload
    ok = True
    status_code = 200
    url = "http://t/x"
    headers = {}


class FakeHttp:
    """requests.Session 替身：record 每次真实「发出」，可注入异常。"""
    def __init__(self, exc=None):
        self.sent = 0
        self.exc = exc
        self.headers = {}
    def request(self, *args, **kwargs):
        self.sent += 1
        if self.exc:
            raise self.exc
        return FakeResp()


class DeclaredLegTask:
    """auto + 自定义 _perform_api 腿；腿内抛什么由子类/参数决定。"""
    def __init__(self, *, read_only, exc, declared, api=None, name="leg_task"):
        self.metadata = TaskMetadata("demo", "选客户", channel=Channel.AUTO,
                                     api_operation="customer_detail",
                                     produces=("v",), read_only=read_only)
        self.supports_api = True
        self.api_leg_side_effect_free = declared
        self._exc = exc
        self._api = api
        self.ui_called = False
        self.api_calls = 0
        self.name = name

    def _perform_api(self, actor):
        self.api_calls += 1
        if self._api is not None:                      # 模拟 demo_api 自有传输腿
            self._api.request_seq += 1                 # CallApi 在场而它「发」了请求
        raise self._exc

    def perform(self, actor):
        self.ui_called = True
        return {"v": "from_ui"}


def _http_actor(exc=None):
    return Actor(abilities=[CallApi("http://t", session=(FakeHttp(exc)))])


# ── §5.1 真值表逐行（含 CONTROL_FLOW 委托与 fail-closed 分流）──────────

@pytest.mark.parametrize("read_only,attempted,exc,expect", [
    (True, True, RuntimeError("x"), True),        # 行2：只读任意 attempted 安全
    (True, False, RuntimeError("x"), True),
    (False, False, RuntimeError("x"), True),      # 行3：写+可证未发出=等价首次
    (False, True, RuntimeError("x"), False),      # 行4：主红线——写+已发出绝不重跑
    (False, True, NotImplementedError("x"), False),
    (True, True, KeyboardInterrupt(), False),     # 行1：控制流优先，单点委托 #108
    (False, False, KeyboardInterrupt(), False),
    (True, False, GeneratorExit(), False),        # CONTROL_FLOW 三元组同源
], ids=["read+sent", "read+unseen", "write+unseen", "write+sent", "write+sent-nie",
        "KI+read", "KI+write-unseen", "GE+read"])
def test_truth_table_rows(read_only, attempted, exc, expect):
    """真值表 4 行 + 控制流委托 + 两个未知分流（design_c12 §2）。"""
    assert delivery.safe_to_auto_reexecute(read_only=read_only, attempted=attempted,
                                           exc=exc) is expect


def test_control_flow_delegation_is_single_truth():
    """判据内委托 failures.CONTROL_FLOW，不留第四份 tuple（#114/#92）。"""
    import atlas.delivery as dv
    for kind in fl.CONTROL_FLOW:
        assert dv.safe_to_auto_reexecute(read_only=True, attempted=False, exc=kind()) is False
    src = Path(dv.__file__).read_text(encoding="utf-8")
    assert "CONTROL_FLOW" in src and "KeyboardInterrupt" not in src


# ── §5.2/5.3 canonical_json 与 token ───────────────────────────────────

def test_canonical_json_key_order_and_nesting():
    """键序无关（YAML 排版不该改把手）；嵌套同样排序。"""
    a = delivery.canonical_json({"b": 1, "a": [{"z": 1, "y": 2}]})
    b = delivery.canonical_json({"a": [{"y": 2, "z": 1}], "b": 1})
    assert a == b
    assert " " not in a.replace('{"', "{").replace("y\": 2", ""), "无空格分隔承诺"


def test_canonical_json_bad_shape_names_error():
    """坏形态点名 ValueError，不猜：不可序列化值、非 str 键都拒。"""
    import datetime
    with pytest.raises(ValueError):
        delivery.canonical_json({"createdAfter": datetime.date(2026, 11, 1)})
    with pytest.raises(ValueError, match="1"):
        delivery.canonical_json({1: "x"})


def test_idempotency_token_deterministic_and_scoped():
    """同参两次相等；entity/scope/where 任一变→变；16 全 hex。"""
    t1 = delivery.idempotency_token("SalesOrder", {"customer": "C1"})
    t2 = delivery.idempotency_token("SalesOrder", {"customer": "C1"})
    assert t1 == t2
    assert len(t1) == delivery.TOKEN_HEX_LEN == 16
    assert all(c in "0123456789abcdef" for c in t1)
    assert t1 != delivery.idempotency_token("ReturnOrder", {"customer": "C1"})
    assert t1 != delivery.idempotency_token("SalesOrder", {"customer": "C1"}, scope="f1")
    assert t1 != delivery.idempotency_token("SalesOrder", {"customer": "C2"})


# ── §5.4 request_seq 插桩 ──────────────────────────────────────────────

def test_request_seq_increments_before_send_result():
    """requests 层抛 ConnectionError 仍 +1（进入发送路径即计数）；未调用不涨。"""
    import requests
    api = CallApi("http://t", session=FakeHttp(requests.ConnectionError("断连")))
    assert api.request_seq == 0
    with pytest.raises(requests.ConnectionError):
        api.request("POST", "/x")
    assert api.request_seq == 1
    idle = CallApi("http://t", session=FakeHttp())
    assert idle.request_seq == 0


def test_request_seq_counts_pre_send_rejection():
    """组包校验失败（坏 body 类型）也已计数——口径是「进入发送路径」，保守方向。"""
    api = CallApi("http://t", session=FakeHttp())
    with pytest.raises(TypeError):
        api.request("POST", "/x", body=object())
    assert api.request_seq == 1


# ── §5.5 写+已发出 → DeliveryUncertain（主红线 #9）─────────────────────

class GenericPostTask:
    """auto + 通用 POST 腿（无 _perform_api）——CallApi 插桩面可证的形态。"""
    supports_api = True
    params = {}
    def __init__(self, read_only=True, name="generic_post"):
        self.metadata = TaskMetadata("demo", "q", channel=Channel.AUTO,
                                     api_operation="/x", produces=("v",),
                                     read_only=read_only)
        self.name = name
        self.ui_called = False

    def perform(self, actor):
        self.ui_called = True
        return {"v": "from_ui"}


def test_write_attempted_api_failure_raises_delivery_uncertain(tmp_path):
    """CallApi seq 变动 + 写任务：绝不降级重跑，记录带 delivery_uncertain，cause 保留。"""
    runs = tmp_path / "runs.jsonl"
    rec = RunRecorder("c12-write", "sit", runs)
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    actor = _http_actor(RuntimeError("API 502"))     # 「已进入发送路径」后炸
    task = GenericPostTask(read_only=False)
    with pytest.raises(DeliveryUncertain) as excinfo:
        engine.execute(actor, task)
    assert task.ui_called is False, "写操作被第二次自动执行——违红线 #9"
    assert isinstance(excinfo.value.__cause__, RuntimeError), "#90：原异常作 cause 保留"
    rec.close("failed")
    step = json.loads(runs.read_text(encoding="utf-8").strip().splitlines()[-1])["steps"][-1]
    assert step["delivery_uncertain"] is True
    assert step["ok"] is False and step["fail_class"] == "interaction"
    assert "degraded_to_ui" not in step, "拦截即止，不留降级痕迹"


def test_generic_post_attempted_seq_note_and_read_path_untouched(tmp_path):
    """同一 fake 的只读形态照常降级（#115 判据收紧不翻正常路径），写形态消息带 seq 证据。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor(RuntimeError("API 502"))
    read_task = GenericPostTask(read_only=True, name="generic_read")
    result = engine.execute(actor, read_task)
    assert result.ok and read_task.ui_called is True
    assert actor.api.request_seq == 1
    with pytest.raises(DeliveryUncertain) as excinfo:
        engine.execute(Actor(abilities=[CallApi("http://t", session=FakeHttp(
            RuntimeError("API 502")))]), GenericPostTask(read_only=False, name="generic_write"))
    msg = str(excinfo.value)
    assert "seq 1->2" in msg or "seq 0->1" in msg, "拦截消息要给出可核对的发送证据"


def test_delivery_uncertain_message_and_record_cp936(tmp_path):
    """X4/§5.18：新消息与落盘键值整串可 cp936 直编（返回串哨兵扫不到，直接断言）。"""
    msg = str(DeliveryUncertain(
        "投递不确定：API 尝试已发出请求（seq 3->4）且任务非只读，"
        "禁止自动降级重跑；结果请人工核实后再决定是否重放：cause=RuntimeError"))
    msg.encode("cp936")
    payload = json.dumps({"step": "x", "delivery_uncertain": True,
                          "idem": "1a2b3c4d5e6f7a8b"}, ensure_ascii=False)
    payload.encode("cp936")


# ── §5.6/§5.7 正常路径不翻（#115）──────────────────────────────────────

def test_write_not_attempted_generic_leg_still_degrades(tmp_path):
    """通用 POST 腿在 request() 之前失败（装配/参数坏）=可证未发出→降级恰 1 次。

    构造：真 CallApi 实例、把 post 换成「进 request 前就抛」的替身——seq 恒 0，
    CallApi 在场（`CallApi in actor.abilities` 为真，插桩读得到），这是真值表行 3
    唯一可证的形态（评审 C12-A：行 3 依据限定「CallApi 通用 POST 面可证」）。
    """
    api = CallApi("http://t", session=FakeHttp())
    def _pre_send_fail(*a, **k):
        raise RuntimeError("参数组包失败")
    api.post = _pre_send_fail
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = Actor(abilities=[api])
    task = GenericPostTask(read_only=False)
    result = engine.execute(actor, task)
    assert result.ok and actor.ctx["v"] == "from_ui"
    assert api.request_seq == 0
    assert actor.ctx.get("_auto_degraded_to_ui"), "既有降级留痕键语义不动"


def test_declared_read_semantic_leg_on_write_task_degrades(tmp_path):
    """C12-A 选项 (a) 放行腿：写元数据 + 声明读语义 API 腿 → 降级行为逐字保持（#115）。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=False, exc=NotImplementedError("API 查客户失败"),
                           declared=True)
    # 声明位放行，但传输面有 demo_api「已发」记录——fail-closed 优先级见下一个测
    engine.execute(actor, task)
    assert task.ui_called is True
    assert actor.ctx.get("_auto_degraded_to_ui")


def test_read_task_attempted_still_degrades(tmp_path):
    """§5.7：只读任务 + 已发出 → 照常降级，真值表行 2（#115 正常路径不翻）。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=True, exc=RuntimeError("API 坏了"), declared=False)
    engine.execute(actor, task)
    assert task.ui_called is True


# ── C12-A 真口子三形态各配各测（评审：demo_api ≠ 无 CallApi）──────────

def test_custom_leg_write_undeclared_fail_closed_demo_api_shape(tmp_path):
    """实锤形态一（评审 C12-A）：SelectCustomerTask 同构——read_only=False + demo_api
    发出后 raise NIE。CallApi 在场而 seq 恒不动（传输不经它），**未声明**的自定义腿
    对写任务按已发出 fail-closed：这正是设计初版放过、评审拦回的那条口子。
    """
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()          # CallApi 在场，seq 全程不动
    task = DeclaredLegTask(read_only=False, exc=NotImplementedError("API 查客户失败"),
                           declared=False)
    with pytest.raises(DeliveryUncertain):
        engine.execute(actor, task)
    assert task.ui_called is False
    assert actor.api.request_seq == 0, "判据在此形态不读 seq——读了就会把「已发出」误判成未发出"


def test_custom_leg_write_undeclared_fail_closed_even_if_seq_moves(tmp_path):
    """实锤形态二：自定义腿内部真用了 CallApi（seq 动了）——同样拦，且拦的方向与
    seq 无关（未声明=不信任），防「改成信任 seq」的后门回归。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=False, exc=RuntimeError("发出后炸"),
                           declared=False, api=actor.api)
    with pytest.raises(DeliveryUncertain):
        engine.execute(actor, task)
    assert task.ui_called is False


def test_custom_leg_write_declared_does_not_trust_seq_only(tmp_path):
    """声明了读语义腿：放行降级（读语义腿无投递副作用）——与上一测构成「声明/未声明」
    成对回归，防止把声明位改成无条件信任 seq。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=False, exc=NotImplementedError("x"),
                           declared=True, api=actor.api)
    engine.execute(actor, task)
    assert task.ui_called is True


def test_no_callapi_actor_write_not_degraded(tmp_path):
    """§5.9 修正形态二：纯 UI actor（CallApi 缺席）写任务 → attempted 按已发出计，不降级。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = Actor()                # 无 CallApi

    class GenericNoApi:
        supports_api = True
        params = {}
        metadata = TaskMetadata("demo", "q", channel=Channel.AUTO, api_operation="/x",
                                produces=("v",), read_only=False)
        name = "generic_no_api"
        def perform(self, actor):
            return {"v": "from_ui"}

    with pytest.raises(DeliveryUncertain):
        engine.execute(actor, GenericNoApi())


def test_control_flow_during_api_attempt_propagates_interrupted(tmp_path):
    """§5.8：KI 在 API 尝试中 → 传播 interrupted（#108 面扩到降级腿，不被吞成降级/重试）。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=True, exc=KeyboardInterrupt(), declared=True)
    with pytest.raises(KeyboardInterrupt):
        engine.execute(actor, task)
    assert task.ui_called is False
    assert engine.results[-1].failure_class == "interrupted"


def test_generic_route_guard_shape_preserved(tmp_path):
    """C12-B：API 通道且无 supports_generic_route 的 Task 直返 perform——既有语义
    （不试 API、不占降级腿、更不产 DeliveryUncertain）逐字保形。"""
    class CustomApiTask:
        metadata = TaskMetadata("demo", "自定义 api", channel=Channel.API,
                                api_operation="/x", produces=("v",), read_only=False)
        name = "custom_api"
        def perform(self, actor):
            return {"v": "own_channel"}

    engine = ExecutionEngine(AtlasConfig(tmp_path))
    result = engine.execute(Actor(), CustomApiTask())
    assert result.ok is True and result.failure_class is None


# ── §5.10 heal 只读面不扩（钉现状，零改动承诺的回归面）─────────────────

def test_heal_gates_refuse_write_retry(tmp_path):
    from atlas.healing import Healer
    from atlas.knowledge import KnowledgeBase
    kb = KnowledgeBase(tmp_path)
    healer = Healer(kb)

    class Writer:
        metadata = TaskMetadata("demo", "保存", channel=Channel.UI, produces=("v",),
                                read_only=False)
        name = "writer"
        def perform(self, actor):
            return {}

    assert healer.can_auto_heal(Writer(), RuntimeError("x")) is False
    with pytest.raises(PermissionError):
        healer.retry_read_only(Writer(), Actor(), lambda: pytest.fail("不该执行"))


# ── §5.11 归类：类型短路，文本不造归因 ────────────────────────────────

def test_classify_delivery_uncertain_is_interaction():
    assert classify_failure(DeliveryUncertain("投递不确定")) == fl.FailureClass.INTERACTION
    assert classify_text("delivery uncertain 投递不确定") == fl.FailureClass.UNKNOWN, \
        "关键词表永不产出该归类（外部文本不猜投递状态）"


def test_env_blocked_and_unknown_untouched_for_delivery():
    """不进 ENV_BLOCKED（不是策略阻挡）、不落 unknown（不污染缺陷分布 #114 面A）。"""
    assert classify_failure(DeliveryUncertain("x")) not in {fl.FailureClass.ENV_BLOCKED,
                                                            fl.FailureClass.UNKNOWN}


# ── §5.12/5.13/5.14 supply：先探测、失败不盲重发、台账含把手 ──────────

def test_supply_find_hit_never_calls_create(tmp_path):
    calls = []
    probe = CallableProbe("SalesOrder", lambda w: {"orderNo": "SO1"},
                          lambda w: calls.append(w) or {"remark": "非人工创建"})
    provider = DataProvider(AtlasConfig(tmp_path), [probe])
    assert provider.supply({"entity": "SalesOrder"}).action == "find"
    assert calls == [], "「写操作先探测再决策」的既有 find 先腿零改动"


def test_supply_find_only_missing_never_occupies_quota(tmp_path):
    probe = CallableProbe("SalesOrder", lambda w: None,
                          lambda w: pytest.fail("find_only 禁止造数"))
    provider = DataProvider(AtlasConfig(tmp_path), [probe])
    with pytest.raises(EnvBlocked, match="find_only"):
        provider.supply({"entity": "SalesOrder", "strategy": "find_only"})
    assert provider.quota.created == [], "既有形态钉桩：缺数不造单不占额"


def test_supply_create_failure_keeps_quota_and_idem(tmp_path):
    """create 抛错：原异常原样传播（#82 真因优先），台账行已含 idem+where=证据。"""
    def boom(where):
        raise ConnectionError("建单网关 502 断连")
    probe = CallableProbe("SalesOrder", lambda w: None, boom)
    provider = DataProvider(AtlasConfig(tmp_path), [probe], create_quota=1)
    with pytest.raises(ConnectionError):
        provider.supply({"entity": "SalesOrder", "where": {"customer": "C1"}})
    assert len(provider.quota.created) == 1
    entry = provider.quota.created[0]
    assert entry["idem"] == delivery.idempotency_token("SalesOrder", {"customer": "C1"})
    path = tmp_path / "recycle.jsonl"
    provider.quota.save_for_recycle(path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["idem"] == entry["idem"]


def test_supply_retrigger_quota_full_blocks_second_create(tmp_path):
    """C12-D 修正口径：现引擎无自动二次 supply 通路；业务方手工重触发时，
    create_quota=1 下第二次被已占配额拦成 EnvBlocked——creator 实调恰 1 次。"""
    calls = []
    def create(where):
        calls.append(where)
        if len(calls) == 1:
            raise ConnectionError("第一次投递不确定")
        return {"remark": "非人工创建", "orderNo": "SP-2"}
    probe = CallableProbe("SalesOrder", lambda w: None, create)
    provider = DataProvider(AtlasConfig(tmp_path), [probe], create_quota=1)
    with pytest.raises(ConnectionError):
        provider.supply({"entity": "SalesOrder"})
    with pytest.raises(EnvBlocked, match="配额"):
        provider.supply({"entity": "SalesOrder"})
    assert len(calls) == 1


def test_provision_no_auto_re_supply_path(tmp_path):
    """C12-D 的行为面钉桩：dsl.provision 对每个 requires 条目**恰调一次** supply——
    引擎不存在「失败后自动再 supply」的重试腿（EnvBlocked+skip 也是撤步不是重投）。
    源码计数 + 调用计数双钉；判据本体在 dsl 侧，这里只断言形态（#92 不复制判据）。"""
    src = (Path(__file__).resolve().parent.parent / "src" / "atlas" / "dsl.py"
           ).read_text(encoding="utf-8")
    start = src.index("    def provision(self, flow: dict, actor)")
    body = src[start:src.index("    @staticmethod", start)]
    assert body.count("provider.supply(") == 1

    calls = []
    class OnceProbe:
        entity = "SalesOrder"
        def find(self, where):
            calls.append(where)
            raise EnvBlocked("环境缺数")
        def create(self, where):
            raise AssertionError("不该到这")
    flow = {"requires": {"src": {"entity": "SalesOrder", "strategy": "find_only",
                                 "on_env_blocked": "skip"}}}
    from atlas.dsl import FlowCompiler, TaskRegistry
    provider = DataProvider(AtlasConfig(tmp_path), [OnceProbe()], create_quota=0)
    compiler = FlowCompiler(TaskRegistry(), provider=provider)
    out = compiler.provision(flow, Actor())
    assert len(calls) == 1 and out == {}


def test_old_quota_entries_survive_without_idem(tmp_path):
    """#112 读侧容错：无 idem 键的历史条目照常回收。"""
    path = tmp_path / "recycle.jsonl"
    path.write_text(json.dumps({"entity": "SalesOrder", "document": {"orderNo": "SP0"}}) + "\n",
                    encoding="utf-8")
    quota = CreateQuota(3)
    quota.reserve("SalesOrder", {"where": {"x": 1}})          # 缺省不给 idem=不写键
    assert "idem" not in quota.created[0]
    quota.save_for_recycle(path)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[0]["document"]["orderNo"] == "SP0"


# ── §5.15 honors_idempotency：脚本面形态分流 ──────────────────────────

def _echo_script(tmp_path):
    """把**收到的 stdin 原文**回显进结果 JSON——逐字节对比的取证面。"""
    script = tmp_path / "creator.py"
    script.write_text("import json, sys\n"
                      "raw = sys.stdin.read()\n"
                      "json.loads(raw)   # 坏形态照旧炸在脚本侧（#61 消费的是原 where）\n"
                      "print(json.dumps({'remark': '非人工创建', 'orderNo': 'SP1',\n"
                      "                   '_stdin_raw': raw}, ensure_ascii=False))\n",
                      encoding="utf-8")
    return script


def test_honors_false_stdin_bytes_identical_true_contains_key(tmp_path):
    script = _echo_script(tmp_path)
    where = {"customer": "CUST0000001", "qty": 1}
    plain = make_scripted_creator(script)
    raw = json.loads(plain(dict(where))["_stdin_raw"])
    assert raw == where, "缺省形态：进脚本的 JSON 逐字节=旧形态（不塞脚本不认识的键）"
    assert delivery.IDEM_WHERE_KEY not in json.dumps(raw)
    honoring = make_scripted_creator(script, honors_idempotency=True,
                                     idem_entity="SalesOrder")
    got = json.loads(honoring(dict(where))["_stdin_raw"])
    assert got[delivery.IDEM_WHERE_KEY] == delivery.idempotency_token("SalesOrder", where)
    assert {k: v for k, v in got.items() if k != delivery.IDEM_WHERE_KEY} == where


def test_scripted_creator_plain_fn_honors_flag():
    received = {}
    def create_fn(where):
        received.update(where)
        return {"remark": "非人工创建", "orderNo": "SP2"}
    creator = ScriptedCreator(create_fn, honors_idempotency=True, idem_entity="SalesOrder")
    creator({"customer": "C1"})
    assert received[delivery.IDEM_WHERE_KEY] == delivery.idempotency_token(
        "SalesOrder", {"customer": "C1"})


# ── §5.16 净化对抗：16-hex 把手活着，32-hex 样本照旧遮蔽 ──────────────

def test_token_survives_sanitize_and_32hex_sample_redacted():
    """§5.16 对抗样本（按净化刀真实口径写）：

    - `secret=<32hex>`：键名命中名单 + 值呈字面秘密 → 刀落，值段变 <redacted>；
    - `idem=<16hex>`：键名不在名单、**值形态也不够格**（<32 hex 不触发 looks_literal_secret）
      → 把手活着落盘——这正是把 token 截到 16 位的原因（design_c12 §4）。
    注意净化刀的敏感分支要「键名 ∨ 值形态」双条件，裸 hex 不带键本来就不擦——所以这里
    两种形态都带键对比，才证明的是**形态选择**而不是刀的覆盖范围。
    """
    from atlas.credential_gate import looks_literal_secret, sanitize_text
    token = delivery.idempotency_token("SalesOrder", {"customer": "C1"})
    secret32 = "550e8400e29b41d4a716446655440000"
    assert looks_literal_secret(secret32) is True and looks_literal_secret(token) is False
    line = f"回收把手 idem={token} 请人工核实"
    assert token in sanitize_text(line), "16-hex 把手被净化刀吞掉=关联把手销毁"
    leaked = f"异常上下文 secret={secret32} host=ics.example"
    cleaned = sanitize_text(leaked)
    assert secret32 not in cleaned and "host=ics.example" in cleaned


def test_idem_key_root_not_credential_word():
    from atlas.credential_gate import header_is_sensitive, looks_literal_secret
    assert not header_is_sensitive("atlas_idem", "abc123def456ab12")
    assert not looks_literal_secret(delivery.idempotency_token("E", {}))


# ── §5.17 requests 无隐式重试（防线前置，不靠人记）────────────────────

def test_callapi_has_no_implicit_retry(tmp_path):
    api = CallApi("http://t", session=__import__("requests").Session())
    for scheme in ("http://", "https://"):
        adapter = api.session.get_adapter(scheme + "x")
        assert getattr(adapter, "max_retries", 0) == 0 or \
            getattr(getattr(adapter, "max_retries", None), "total", 0) == 0, \
            "装配引入重试=第二执行通道，C12 判据前提失效"
    retry_marks = ("HTTPAdapter(", "Retry(", "max_retries =")
    # **判据串自证**（与 impl_c9 的「扫描必须抓到已知命中」同族，但本测无合法命中可锚定，
    # 故用合成正样本证 matcher 本身有效）：判据串若拼错/漏字符，全盘零命中也会「通过」，
    # 那正是红线 #7 家族里最隐蔽的假绿——扫不到东西 ≠ 没有东西。
    positive = tmp_path / "positive.py"
    positive.write_text("from requests.adapters import HTTPAdapter\n"
                        "a = HTTPAdapter(max_retries = 3)\n"
                        "b = Retry(total=2)\n", encoding="utf-8")
    caught = [t for t in retry_marks if t in positive.read_text(encoding="utf-8")]
    assert len(caught) == len(retry_marks), f"扫描判据串失效（只认出 {caught}）=本测恒真空"
    src_root = Path(__file__).resolve().parent.parent / "src"
    scanned = list(src_root.rglob("*.py"))
    assert len(scanned) > 50, f"源码扫描落空（只扫到 {len(scanned)} 个文件）=本测恒真空跑"
    names = {p.name for p in scanned}
    assert {"abilities.py", "engine.py", "probes.py", "creators.py"} <= names, \
        "扫描面漏掉重试装配可能落点的关键文件=面缩小造成假绿"
    hits = [str(p) for p in scanned
            if any(t in p.read_text(encoding="utf-8") for t in retry_marks)]
    assert hits == [], f"src 出现重试库装配={hits}（引库必须先过 C12 判据重审）"
    # 反向自证：同一判据对合成正样本必须命中（否则上面那句 == [] 是判据坏了的假绿）
    assert [str(p) for p in tmp_path.rglob("*.py")
            if any(t in p.read_text(encoding="utf-8") for t in retry_marks)] == [str(positive)]


# ── C12-E 判据级硬闸：投递不确定/等人 一律**按类型**拒绝重试 ────────────

def test_delivery_uncertain_never_step_retried_even_with_retry_enabled(tmp_path, monkeypatch):
    """step_retry>0 时写任务 DeliveryUncertain → perform 恰 1 次。

    C12-E 已放行（2026-11-06）：判据不再依赖 `_retry_eligible` 的 read_only 前置偶然挡住，
    `waits.is_recoverable` 直接按类型 False（failures.NEVER_AUTO_RETRY 单点）。
    本波前这里钉的是「归类面判可重试、被元数据门挡住」，现钉的是「归类面仍 INTERACTION
    （归因不改，红线 #7 保持 failed），但重试面按类型拒绝」——两条独立成立才叫判据级。
    """
    monkeypatch.setenv("ATLAS_STEP_RETRY", "2")
    monkeypatch.setenv("ATLAS_STEP_RETRY_INTERVAL_MS", "0")
    from atlas.failures import classify_failure
    from atlas.waits import is_recoverable
    exc = DeliveryUncertain("x")
    assert classify_failure(exc) == fl.FailureClass.INTERACTION, "归因面不变：DU 仍算真失败"
    assert is_recoverable(exc) is False, "C12-E：重试面按类型拒绝"
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=False, exc=RuntimeError("已发出"), declared=False)
    with pytest.raises(DeliveryUncertain):
        engine.execute(actor, task)
    assert task.api_calls == 1 and task.ui_called is False


def test_retry_eligible_refuses_delivery_uncertain_types(tmp_path):
    """判据级钉子（C12-E 放行版）：**即使 read_only=True**（元数据说安全）DU 也不重试。

    这条才是升级的实质：旧形态下 `read_only=True` + DU 会穿过元数据门进重试循环，而
    C12-A 已经论证过「元数据/seq 证明不了没发出」，拿它当放行依据自相矛盾。删掉类型闸
    （waits.py 的 NEVER_AUTO_RETRY 分支）本测立刻翻红。
    """
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    task = DeclaredLegTask(read_only=True, exc=RuntimeError("x"), declared=True)
    task.metadata = TaskMetadata("demo", "w", channel=Channel.AUTO,
                                 api_operation="q", produces=("v",), read_only=True)
    assert engine._retry_eligible(task, DeliveryUncertain("x")) is False


@pytest.mark.parametrize("builder,exc_type", [
    # 文案里带瞬态标记（waits._TRANSIENT_MARKS 的三个 mark 之一）——旧实现按文本先判可
    # 重试，「等人」/「投递不确定」会被就地重跑，这是类型闸必须**先于**文本判据的原因。
    (lambda: fl.AwaitingHuman("等待人工确认，前序 navigation 中断"), "AwaitingHuman"),
    (lambda: DeliveryUncertain("cause=detached frame during navigation"), "DeliveryUncertain"),
])
def test_never_auto_retry_beats_transient_text(builder, exc_type):
    """正向对照自证：同一文案换个异常类型就必须判可重试，否则本测是恒真。"""
    from atlas.waits import _is_transient, is_recoverable
    exc = builder()
    assert _is_transient(exc), f"前提：{exc_type} 文案确实命中瞬态标记（不成立则本测恒真）"
    assert is_recoverable(exc) is False, f"{exc_type} 应被类型闸挡下"
    # 同文案的普通 RuntimeError（非 NEVER_AUTO_RETRY 成员）→ 仍判可重试
    assert is_recoverable(RuntimeError(str(exc))) is True


# ── C12-F 撞序：canonical 失败让位 creators #61 点名 ───────────────────

def test_canonical_failure_yields_to_unserializable_naming(tmp_path):
    """where 含 date：idem 留空/不注入，报错口径仍是 #61 的「哪个键什么类型+脚本路径」。"""
    import datetime
    script = tmp_path / "c.py"
    script.write_text("import json,sys\njson.load(sys.stdin)\n", encoding="utf-8")
    creator = make_scripted_creator(script, honors_idempotency=True)
    with pytest.raises(TypeError, match="createdAfter"):
        creator({"where": {"createdAfter": datetime.date(2026, 11, 1)}})


def test_supply_canonical_failure_keeps_creator_error(tmp_path):
    """supply 面同一条让位纪律：坏 where 不炸在 delivery，仍由建单腿报原错。"""
    import datetime
    def create(where):
        raise TypeError("建单脚本的 where 含无法 JSON 序列化的值（键 createdAfter 是 date）")
    probe = CallableProbe("SalesOrder", lambda w: None, create)
    provider = DataProvider(AtlasConfig(tmp_path), [probe], create_quota=3)
    with pytest.raises(TypeError, match="createdAfter"):
        provider.supply({"entity": "SalesOrder",
                         "where": {"createdAfter": datetime.date(2026, 11, 1)}})
    entry = provider.quota.created[0]
    assert "idem" not in entry, "canonical 失败让位：idem 留空=不写键（C6 先例形态）"


# ── §5.19 存量兼容：degraded reason 取值形态逐字不变（C4② 协作面）─────

def test_degraded_reason_shape_unchanged_on_read_path(tmp_path):
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=True, exc=RuntimeError("API 坏了"), declared=False)
    engine.execute(actor, task)
    entries = actor.ctx["_auto_degraded_to_ui"]
    assert entries == [{"task": "leg_task", "reason": "API 坏了"}], \
        "runs.jsonl 已落盘取值形态翻新=存量断言翻红面"


# ── C12-C 三源行为钉（协调补丁 1 的行为侧；注释文案落点归 impl_c9）────

def test_three_retry_sources_distinct_keys(tmp_path):
    """盘上重试源=三处（heal / 步骤级 flaky / 有界降级），三键各自成立、互不冒充。

    设计初版只评估了两源（评审必改 C12-C）；本测把三源钉成一份回归清单：
    `healed`（自愈恰一次）、`retried`（flaky 计数）、`degraded_to_ui`（有界降级）。
    reporting.export_runlog 读 `step["retries"]` 的存量 dead-read 不在本波（lead 拍板
    记录不动，独立缺口上报）。
    """
    from atlas.healing import Healer
    from atlas.knowledge import KnowledgeBase
    import os

    runs = tmp_path / "runs.jsonl"

    # ① 有界降级（attempted=False 形态）
    rec = RunRecorder("c12-degrade", "sit", runs)
    engine = ExecutionEngine(AtlasConfig(tmp_path), recorder=rec)
    engine.execute(_http_actor(), DeclaredLegTask(read_only=True,
                                                  exc=RuntimeError("坏"), declared=True))
    rec.close("passed")
    step = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])["steps"][-1]
    assert "degraded_to_ui" in step and "retried" not in step and "healed" not in step

    # ② 步骤级 flaky 重试（只读 + 交互类，重试成功→retried 键）
    class Flaky:
        metadata = TaskMetadata("demo", "抖", channel=Channel.UI, produces=("v",),
                                read_only=True)
        name = "flaky"
        n = 0
        def perform(self, actor):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("element not visible")
            return {"v": "ok2"}

    runs2 = tmp_path / "runs2.jsonl"
    rec2 = RunRecorder("c12-flaky", "sit", runs2)
    os.environ["ATLAS_STEP_RETRY"] = "1"
    os.environ["ATLAS_STEP_RETRY_INTERVAL_MS"] = "0"
    try:
        ExecutionEngine(AtlasConfig(tmp_path), recorder=rec2).execute(Actor(), Flaky())
    finally:
        os.environ.pop("ATLAS_STEP_RETRY"); os.environ.pop("ATLAS_STEP_RETRY_INTERVAL_MS")
    rec2.close("passed")
    step2 = json.loads(runs2.read_text(encoding="utf-8").splitlines()[-1])["steps"][-1]
    assert step2.get("retried") == 1 and step2["ok"] is True
    assert "healed" not in step2 and "degraded_to_ui" not in step2

    # ③ heal 自愈（已审核知识 + 只读→healed 键）
    kb = KnowledgeBase(tmp_path)
    kb.save_reviewed("pitfalls", [{"id": "k1", "trigger": "Timeout", "symptom": "s",
                                   "root_cause": "r", "remedy": "退一下", "scope": ["demo"],
                                   "severity": "medium", "source": "t",
                                   "status": "approved"}], approved=True)
    class Healable:
        metadata = TaskMetadata("demo", "超时", channel=Channel.UI, produces=("v",),
                                read_only=True)
        name = "healable"
        n = 0
        def perform(self, actor):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("Timeout")
            return {"v": "ok3"}

    runs3 = tmp_path / "runs3.jsonl"
    rec3 = RunRecorder("c12-heal", "sit", runs3, mode="heal")
    healer = ExecutionEngine(AtlasConfig(tmp_path, mode="heal"), knowledge=kb,
                             healer=Healer(kb), recorder=rec3)
    healer.execute(Actor(), Healable())
    rec3.close("passed")
    step3 = json.loads(runs3.read_text(encoding="utf-8").splitlines()[-1])["steps"][-1]
    assert step3.get("healed") is True and step3["ok"] is True
    assert "retried" not in step3 and "degraded_to_ui" not in step3


def test_delivery_uncertain_is_runtimeerror_subclass(tmp_path):
    """继承 RuntimeError 是有意（#115）：仍按 RuntimeError 处理的旧消费方不翻裸崩，
    新消费方按 isinstance 精判——两头同时成立才算兼容。"""
    exc = DeliveryUncertain("x")
    assert isinstance(exc, RuntimeError)
    try:
        raise exc
    except RuntimeError as caught:          # 旧口径照常接得住
        assert isinstance(caught, DeliveryUncertain)
        assert classify_failure(caught) == fl.FailureClass.INTERACTION


def test_delivery_uncertain_survives_old_runtimeerror_consume_paths(tmp_path):
    """except 块内 raise 子类不会被同块再捕获（自环风险，design_c12 §7 交靶 1）：
    传播路径=engine 记 failed + 原样上抛，不产生第二次 _perform。"""
    engine = ExecutionEngine(AtlasConfig(tmp_path))
    actor = _http_actor()
    task = DeclaredLegTask(read_only=False, exc=RuntimeError("e"), declared=False)
    with pytest.raises(DeliveryUncertain):
        engine.execute(actor, task)
    assert task.api_calls == 1, "降级闸自身不得把拦截翻成重跑"


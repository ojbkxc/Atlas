"""PRIVACY.md 的「每条承诺配一条会红的测」哨兵（design_c4 §2.4 C4④，M11 手法）。

PRIVACY 里的口径若是散文，就会和磁盘行为漂移（本项目反复栽过的 #102/#105 同族：
文档声称的防线从未被机制兑现）。这里把 `PRIVACY.md` 的每条运行产物承诺钉成一条断言：
文档写「不会落 X」→ 测证明落盘文件里确实没有 X；文档写「会落 Y」→ 测证明 Y 真在盘上。
"""
import json
from pathlib import Path

import pytest
import yaml

from atlas import Actor, versioning
from atlas import failures as fl
from atlas.config import AtlasConfig
from atlas.core import Channel, FunctionalTask, TaskMetadata
from atlas.credential_gate import sanitize_url
from atlas.dsl import TaskRegistry
from atlas.engine import ExecutionEngine
from atlas.recorder import RunRecorder

LITERAL_UUID = "b5360b2f-1c2d-3e4f-5a6b-7c8d9e0f1a2b"
PRIVACY = Path(__file__).resolve().parent.parent / "PRIVACY.md"
CB = "https://sso.example/cb"    # 虚构样本主机，集中一处（见 SAFE-001 口径）
PORTAL = "https://ics.example.com/portal/top"


@pytest.fixture(scope="module")
def privacy_text():
    return PRIVACY.read_text(encoding="utf-8")


def _runs_line(path):
    return json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])


def _recorder(tmp_path, case):
    config = AtlasConfig(tmp_path, mode="run")
    return config, RunRecorder(case, config.env, config.reports_dir / "runs.jsonl", "run")


# ── (a) ctx 不落盘 ─────────────────────────────────────────────────────

def test_ctx_is_never_serialized_into_runs(tmp_path):
    """PRIVACY「业务上下文 actor.ctx 不会由 RunRecorder 自动序列化」。"""
    config, recorder = _recorder(tmp_path, "privacy-case")
    actor = Actor()
    actor.ctx["doc_no"] = "SO-DEMO-0009"
    actor.ctx["customer"] = {"code": "CUST0000001", "name": "某客户"}
    registry = TaskRegistry()
    registry.register("note", lambda **kw: FunctionalTask(
        TaskMetadata("demo", "note", produces=("note",), channel=Channel.API),
        lambda a: {"note": "ok"}, "note"))
    ExecutionEngine(config, recorder=recorder).execute(actor, registry.create("note"))
    recorder.close("passed")

    line = _runs_line(config.reports_dir / "runs.jsonl")
    assert "ctx" not in line
    text = json.dumps(line, ensure_ascii=False)
    assert "SO-DEMO-0009" not in text and "CUST0000001" not in text, "ctx 业务值漏进运行记录"


# ── (b) 字面凭据不进迁移/生成产物 ─────────────────────────────────────

def test_migration_products_contain_no_literal_credential(tmp_path, monkeypatch, capsys):
    """PRIVACY「字面凭据闸」：迁移产物（YAML + 终端）里 582 同款字面 sessionid 必须为 0。"""
    from scripts.import_jmeter import main as jmeter_main

    jmx = tmp_path / "p.jmx"
    jmx.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2"><hashTree>
<HeaderManager><collectionProp name="HeaderManager.headers">
<elementProp name="" elementType="Header"><stringProp name="Header.name">isc-axios-sessionid</stringProp>
<stringProp name="Header.value">SECRETPlaceholder</stringProp></elementProp>
</collectionProp></HeaderManager><hashTree/>
<HTTPSamplerProxy testname="查"><stringProp name="HTTPSampler.path">/web/x</stringProp>
<stringProp name="HTTPSampler.method">POST</stringProp></HTTPSamplerProxy><hashTree/>
</hashTree></jmeterTestPlan>""".replace("SECRETPlaceholder", LITERAL_UUID), encoding="utf-8")
    out = tmp_path / "flow.yaml"
    monkeypatch.setattr("sys.argv", ["import_jmeter.py", str(jmx), "-o", str(out)])
    assert jmeter_main() == 0
    produced = out.read_text(encoding="utf-8") + capsys.readouterr().out
    assert LITERAL_UUID not in produced
    assert "${ATLAS_HDR_1}" in produced


def test_gen_flow_headers_gate_is_applied_on_producer_side():
    """同一条闸必须也管住 `atlas gen`：全局头里手写的字面鉴权值不进生成产物。"""
    from atlas.generator.emit_flow import emit_flow
    from atlas.generator.model import Endpoint

    flow, _manual, _low = emit_flow(
        [Endpoint(method="GET", path="/api/x", summary="查一下")], scenario="a",
        global_headers={"Authorization": "Bearer " + LITERAL_UUID, "isc-app": "iservice"})
    dumped = yaml.dump(flow, allow_unicode=True, sort_keys=False)
    assert LITERAL_UUID not in dumped
    assert flow["steps"][0]["headers"]["Authorization"] == "${ATLAS_HDR_1}"
    assert flow["steps"][0]["headers"]["isc-app"] == "iservice", "业务识别头不许被误伤"


# ── (c) 异常/URL 净化 ──────────────────────────────────────────────────

def test_sso_callback_exception_text_is_sanitized_on_disk_forms():
    """PRIVACY「异常 query 净化」：SSO 回调文案的落盘形态只剩 <redacted>。"""
    exc = RuntimeError(f"回调失败 URL={CB}?ticket=ST-1&code=SEC-2&docNo=SO-9")
    text = fl.exception_text(exc)
    assert "ST-1" not in text and "SEC-2" not in text
    assert "docno=so-9" in text, "整段抹 query 与 PRIVACY 的逐键口径不符（也销毁取证参数）"


def test_runtime_writes_sanitized_page_url_into_exceptions():
    """PRIVACY 承诺覆盖框架自己写进异常的 URL（SSO 回调 query 常带 ticket/code）。"""
    cleaned = sanitize_url(f"{PORTAL}?ticket=ST-abc&jsessionid=DEADBEEF#/sso")
    assert "ST-abc" not in cleaned and "DEADBEEF" not in cleaned
    assert "#<redacted>" in cleaned
    source = (Path(__file__).resolve().parent.parent / "src" / "atlas" / "runtime.py"
              ).read_text(encoding="utf-8")
    assert "sanitize_url(page.url)" in source, "runtime 回到裸 page.url = 承诺失效"


# ── (d) 步骤里的业务字段如实声明 ───────────────────────────────────────

def test_degraded_field_really_lands_in_steps(tmp_path):
    """PRIVACY 现口径明写 steps 含 degraded_to_ui（净化后摘要）——测保证描述不是空话。"""
    class AutoTask:
        name = "auto_task"
        supports_api = True
        supports_generic_route = False
        metadata = TaskMetadata("demo", "auto_task", produces=("v",),
                                channel=Channel.AUTO, api_operation="query", read_only=True)

        def _perform_api(self, actor):
            raise RuntimeError("API 坏了 token=abc123456789")

        def perform(self, actor):
            return {"v": "from_ui"}

    config, recorder = _recorder(tmp_path, "privacy-degraded")
    ExecutionEngine(config, recorder=recorder).execute(Actor(), AutoTask())
    recorder.close("passed")
    step = _runs_line(config.reports_dir / "runs.jsonl")["steps"][-1]
    assert step["degraded_to_ui"], "PRIVACY 声明的步骤字段在盘上不存在=文档说谎"
    assert "abc123456789" not in json.dumps(step), "降级摘要未走净化刀"


def test_privacy_document_declares_the_fields_that_really_land(privacy_text):
    """文档诚实面：steps 实含 degraded_to_ui / dispatch / attachment，必须写进事实条目。
    C9（W6 段，impl_c9）追加 run 顶层 `reason`——只有 waiting 行才出现、内容是净化后摘要，
    「会落盘却没写进 PRIVACY」与「写进了 PRIVACY 却落不了盘」是同一枚硬币的两面（M11）。"""
    for field in ("degraded_to_ui", "dispatch", "attachment", "reason",
                  "delivery_uncertain", "idem"):     # C12（W5 段）：投递不确定标记 + 幂等把手
        assert field in privacy_text, f"{field} 会落盘却没写进 PRIVACY=口径失真"
    assert "actor.ctx" in privacy_text
    assert "waiting" in privacy_text, "失败类清单没补 waiting——取值域扩了文档却没说"""


def test_c12_declared_fields_really_land(tmp_path):
    """C12 两键的反向哨兵（与 degraded 那条同一手法）：文档说「会落盘」，盘上就得有。
    ①写任务的 API 尝试已发出 → step 记录 `delivery_uncertain=true` 且 ok=false
      （投递不确定保持 failed，红线 #7 不借终态洗绿）；
    ②建单失败 → 回收行 `idem` 是 16-hex 把手，穿越净化刀后仍可读（关联把手不销毁）。
    """
    from atlas import delivery
    from atlas.abilities import CallApi
    from atlas.credential_gate import sanitize_text
    from atlas.probes import CallableProbe, DataProvider

    class Http:
        headers = {}
        def request(self, *args, **kwargs):
            raise RuntimeError("API 502 已发出")

    class WriteTask:
        supports_api = True
        params = {}
        metadata = TaskMetadata("demo", "q", channel=Channel.AUTO, api_operation="/x",
                                produces=("v",), read_only=False)
        name = "write_step"
        def perform(self, actor):
            return {"v": "ui"}

    config = AtlasConfig(tmp_path, mode="run")
    runs = config.reports_dir / "runs.jsonl"
    recorder = RunRecorder("privacy-delivery", config.env, runs, "run")
    engine = ExecutionEngine(config, recorder=recorder)
    with pytest.raises(fl.DeliveryUncertain):
        engine.execute(Actor(abilities=[CallApi("http://t", session=Http())]), WriteTask())
    recorder.close("failed")
    step = _runs_line(runs)["steps"][-1]
    assert step.get("delivery_uncertain") is True, "PRIVACY 声明的 C12 键在盘上不存在=文档说谎"
    assert step["ok"] is False and step["fail_class"] == "interaction"

    provider = DataProvider(config, [CallableProbe(
        "SalesOrder", lambda w: None,
        lambda w: (_ for _ in ()).throw(ConnectionError("建单断连")))], create_quota=1)
    with pytest.raises(ConnectionError):
        provider.supply({"entity": "SalesOrder", "where": {"customer": "CUST0000001"}})
    row = provider.quota.created[0]
    assert row["idem"] == delivery.idempotency_token("SalesOrder", {"customer": "CUST0000001"})
    assert row["idem"] in sanitize_text(f"回收把手 idem={row['idem']} 待人工核实"), \
        "16-hex 形态若被净化刀吞掉，PRIVACY 承诺的关联把手就是空话"


# ── 版本三元组的隐私边界 ───────────────────────────────────────────────

def test_version_triple_contains_no_knowledge_content(tmp_path):
    """PRIVACY「version 只含版本号与哈希，不含知识内容与凭据」。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "pitfalls.json").write_text(json.dumps(
        {"pitfalls": [{"trigger": "内部系统真实异常文案", "remedy": "客户号 CUST0000001"}]}),
        encoding="utf-8")
    payload = json.dumps(versioning.version_triple(kb, flow={"steps": []}), ensure_ascii=False)
    assert "内部系统真实异常文案" not in payload
    assert "CUST0000001" not in payload
    assert len(json.loads(payload)["knowledge"]["sha1"]) == 12


# ── 分发口径（C4③）落在文案里，不只是文档里 ───────────────────────────

@pytest.mark.parametrize("clause", ["字面凭据闸", "逐键", "净化前原文", "--bind 127.0.0.1"])
def test_privacy_document_keeps_its_promise_sections(privacy_text, clause):
    """哨兵另一半：条目被删/改写时先在这里红，提醒「测与文档必须同时改」。"""
    assert clause in privacy_text


def test_gen_cli_reports_placeholder_mapping_only(tmp_path, capsys):
    """`atlas gen` 的 CLI 面：`--headers-file` 里手写的字面鉴权值只剩占位，
    终端只报「第几步哪个头 → 哪个 env 键」，值不进 stdout/stderr/产物文件。"""
    from atlas.cli import main

    doc = tmp_path / "api.json"
    doc.write_text(json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "客户查询", "version": "1"},
        "paths": {"/api/customer/detail": {"get": {"summary": "查客户",
                                                   "responses": {"200": {"description": "ok"}}}}},
    }), encoding="utf-8")
    headers = tmp_path / "headers.json"
    headers.write_text(json.dumps({"isc-app": "iservice",
                                   "Authorization": "Bearer " + LITERAL_UUID}),
                       encoding="utf-8")
    out = tmp_path / "gen"
    rc = main(["--root", str(tmp_path), "gen", str(doc), "--headers-file", str(headers),
               "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    produced = [f for f in out.rglob("*") if f.is_file()]
    assert produced, "gen 没产出任何文件"
    text = "".join(f.read_text(encoding="utf-8") for f in produced)
    assert LITERAL_UUID not in captured.out + captured.err + text
    assert "ATLAS_HDR_1" in captured.err, "CLI 必须报占位映射"
    assert "${ATLAS_HDR_1}" in text, "产物里只剩占位符"


def test_cli_allure_serve_hint_mentions_loopback(tmp_path, capsys):
    from atlas.cli import main
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({"run_id": "1", "case": "c", "env": "sit",
                                "status": "passed", "steps": [],
                                "started_at": "2026-11-06T00:00:00+00:00",
                                "finished_at": "2026-11-06T00:00:01+00:00"}) + "\n",
                    encoding="utf-8")
    out = tmp_path / "allure-results"
    assert main(["--root", str(tmp_path), "allure", str(runs), "--out", str(out),
                 "--serve"]) == 0
    err = capsys.readouterr().err
    assert "allure serve" in err
    assert "--bind 127.0.0.1" in err, "分发提示回到可被局域网监听的口径"


def test_scaffold_allure_templates_mention_loopback():
    from atlas import scaffold
    assert "--bind 127.0.0.1" in scaffold._RUN_ALLURE, "脚手架脚本没带上回环口径"
    assert "--bind 127.0.0.1" in scaffold._README

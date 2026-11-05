import json
import pytest
from atlas.cli import main
from atlas.reporting import summarize
from atlas.recorder import RunRecorder
from atlas.state_machine import StateMachine


def test_state_machine_loads_yaml(tmp_path):
    path = tmp_path / "sm.yaml"
    path.write_text("initial: a\nstates:\n  a:\n    - {to: b, task: go}\n  b: []\n", encoding="utf-8")
    machine = StateMachine.load(path)
    assert machine.path("b") == ["go"]
    assert "b" in machine.terminal


def test_state_machine_rejects_unknown_target_state(tmp_path):
    path = tmp_path / "sm.yaml"
    path.write_text("initial: a\nstates:\n  a:\n    - {to: b, task: go}\n  b: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        StateMachine.load(path).path("c")


def test_summary_reports_failures_and_slowest_steps(tmp_path):
    path = tmp_path / "runs.jsonl"
    ok = RunRecorder("caseA", "sit", path)
    with ok.step("1") as s:
        s["api_ms"] = 5
    ok.close("passed")
    bad = RunRecorder("caseB", "sit", path)
    with pytest.raises(TimeoutError):
        with bad.step("2"):
            raise TimeoutError()
    bad.close("failed")
    result = summarize(path)
    assert result["runs"] == 2
    assert result["cases"] == 2
    assert result["status"] == {"passed": 1, "failed": 1}
    assert result["fail_classes"] == {"interaction": 1}
    assert result["slowest_steps"][0]["case"] in {"caseA", "caseB"}


def test_summary_missing_file_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError):
        summarize(tmp_path / "missing.jsonl")


def test_cli_machine_and_selectors(tmp_path, capsys):
    sm = tmp_path / "sm.yaml"
    sm.write_text("initial: a\nstates:\n  a:\n    - {to: b, task: go}\n  b: []\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "machine", str(sm), "--target", "b"]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == ["go"]
    assert main(["--root", str(tmp_path), "selectors"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "payment_terms" in out["components"]


def test_cli_tasks_outputs_full_catalog(tmp_path, capsys):
    """atlas tasks 输出所有核心 Task 的元数据，供 agent 拼装。"""
    assert main(["--root", str(tmp_path), "tasks"]) == 0
    tasks = json.loads(capsys.readouterr().out)
    names = {t["name"] for t in tasks}
    assert {"open_doc_list", "click_new", "select_customer", "click_save", "wait_save_result"} <= names
    # 元数据完整性
    for t in tasks:
        assert t["domain"] == "demo"
        assert t["channel"] in {"ui", "api", "auto"}


def test_cli_compose_topologically_orders_for_goal(tmp_path, capsys):
    """atlas compose --goal document_no 按依赖排序输出 Task 名。"""
    assert main(["--root", str(tmp_path), "compose", "--goal", "document_no"]) == 0
    composed = capsys.readouterr().out.strip().splitlines()
    assert "open_doc_list" in composed
    assert "click_save" in composed
    assert composed.index("open_doc_list") < composed.index("click_save")
    assert composed.index("click_save") < composed.index("wait_save_result")


def test_cli_planning_commands_report_unreachable_goal_friendly(tmp_path, capsys):
    """#80：compose / agent 对用户传入的「拼不出来的 goal」抛裸 FlowCompositionError traceback（rc 1），
    与已修的 compile/run 文件输入口径不一致。现统一为一行 stderr + rc 2，点名目标。agent 走默认只规划
    （不登录）路径，坏 goal 不得触达 DemoRuntime/浏览器。"""
    assert main(["--root", str(tmp_path), "compose", "--goal", "no_such_goal_xyz"]) == 2
    err = capsys.readouterr().err
    assert "no_such_goal_xyz" in err and "FlowCompositionError" in err
    assert main(["--root", str(tmp_path), "agent", "--goal", "no_such_goal_xyz"]) == 2
    err2 = capsys.readouterr().err
    assert "no_such_goal_xyz" in err2                     # agent 规划期同样友好报错


def test_cli_contracts_reports_level_distribution(tmp_path, capsys, monkeypatch):
    """atlas contracts 输出契约分级覆盖情况。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)   # 钉死读包内默认（CLI 现尊重私有覆盖）
    assert main(["--root", str(tmp_path), "contracts"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["levels"]["core"] >= 1
    assert out["levels"]["high"] >= 1
    assert out["total"] >= 7


def test_cli_contracts_respects_private_override(tmp_path, monkeypatch, capsys):
    """#78：atlas contracts 展示必须走 私有→包内 回退。旧实现硬编码包内 knowledge_root，
    真实项目用 ATLAS_KNOWLEDGE_DIR 覆盖契约时，展示的是虚构 demo 契约、运行时校验的却是私有契约
    ——展示 ≠ 校验（#74 只修了 runtime 侧，CLI 侧仍漂移）。修复后 CLI 与运行时同源。"""
    priv = tmp_path / "kb"; (priv / "contracts").mkdir(parents=True)
    (priv / "contracts" / "core.json").write_text(json.dumps({"contracts": [
        {"name": "priv-real-health", "method": "GET", "path": "/priv/health", "level": "minimal"}]}),
        encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(priv))
    assert main(["--root", str(tmp_path), "contracts"]) == 0
    out = json.loads(capsys.readouterr().out)
    names = {c["name"] for c in out["contracts"]}
    assert "priv-real-health" in names                        # 私有契约被展示
    assert not any(p["path"].endswith("findEntireCustDetails") for p in out["contracts"])  # 包内被整体替换


def test_cli_contracts_validate_ok_when_all_contracts_effective(tmp_path, monkeypatch, capsys):
    """#78：默认包内契约每条都有真实 path+name，--validate 判定全部生效 → rc 0。"""
    monkeypatch.delenv("ATLAS_KNOWLEDGE_DIR", raising=False)
    assert main(["--root", str(tmp_path), "contracts", "--validate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["problems"] == []


def test_cli_contracts_validate_flags_inert_contract(tmp_path, monkeypatch, capsys):
    """#78：path 为空的契约在 `atlas contracts` 清单里冒充覆盖率，但 Contract.validate()
    遇空 path 直接 return（永不生效）。--validate 必须点名它并回 rc 2，否则「看似有断言层实则空转」
    违反红线 #7/#12。"""
    priv = tmp_path / "kb"; (priv / "contracts").mkdir(parents=True)
    (priv / "contracts" / "core.json").write_text(json.dumps({"contracts": [
        {"name": "ok-one", "method": "GET", "path": "/x", "level": "minimal"},
        {"name": "ghost", "method": "GET", "path": "", "level": "high"},
        {"name": "", "method": "POST", "path": "/named-path", "level": "minimal"}]}),
        encoding="utf-8")
    monkeypatch.setenv("ATLAS_KNOWLEDGE_DIR", str(priv))
    assert main(["--root", str(tmp_path), "contracts", "--validate"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    joined = " ".join(out["problems"])
    assert "ghost" in joined and "path 为空" in joined          # 空 path 点名
    assert "name 为空" in joined                                # 空 name 点名归因


def test_cli_contracts_validate_reports_unparseable_file(tmp_path, monkeypatch, capsys):
    """#78：两级契约都缺失/无法解析时，--validate 给可读报告 + rc 2，不再裸 traceback。
    （私有写坏会回退包内；只有包内也指到不存在目录才进 unresolved 分支。）"""
    import atlas.cli as cli
    from atlas.plugins.demo.plugin import DemoPlugin
    empty = tmp_path / "empty"; empty.mkdir()
    class _NoKb(DemoPlugin):
        def __init__(self):
            self.knowledge_root = empty / "pkg"
            self.private_root = empty / "priv"
    monkeypatch.setattr(cli, "resolve_plugin", _NoKb)
    assert main(["--root", str(tmp_path), "contracts", "--validate"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["source"] == "unresolved"
    assert "无法解析" in out["error"]


def test_cli_knowledge_queries_pitfalls(tmp_path, capsys):
    """atlas knowledge 输出坑点知识库。"""
    assert main(["--root", str(tmp_path), "knowledge"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) >= 30
    assert all(r.get("status") == "approved" for r in rows)


def test_cli_agent_propose_plans_without_executing(tmp_path, capsys):
    """atlas agent --goal 只规划不执行，输出风险与 Task 清单。"""
    assert main(["--root", str(tmp_path), "agent", "--goal", "document_no"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["goal"] == "document_no"
    assert "risk" in out
    assert "auto_allowed" in out
    assert len(out["tasks"]) >= 7
    assert all("channel" in t and "read_only" in t for t in out["tasks"])


def test_cli_recycle_empty_list(tmp_path, capsys):
    """atlas recycle 无回收清单时输出空列表。"""
    assert main(["--root", str(tmp_path), "recycle",
                 "--input", str(tmp_path / "nonexistent.jsonl")]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_recycle_survives_truncated_utf8(tmp_path, capsys):
    """atlas recycle 读腰斩坏字节清单：旧实现整读崩裸 UnicodeDecodeError（调用点不在
    try 包裹内，rc2 友好口径都够不着）——一条写坏的历史记录让人连要回收的单都列不出，
    违反 #71 残行计数承诺。修复后坏行落 skipped 计数、好行照常列出、rc 0。"""
    bad = tmp_path / "recycle_list.jsonl"
    good = {"entity": "sales_order", "document": "SO-001"}
    bad.write_bytes(json.dumps(good, ensure_ascii=False).encode("utf-8") + b"\n"
                    + b'{"entity": "\xe4\xbd')          # 截断的多字节，无尾换行
    assert main(["--root", str(tmp_path), "recycle", "--input", str(bad)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["document"] for r in out] == ["SO-001"]


def test_cli_doctor_reports_environment(tmp_path, capsys):
    """atlas doctor 输出环境与资产检查信息。"""
    main(["--root", str(tmp_path), "doctor"])
    out = json.loads(capsys.readouterr().out)
    assert "python" in out and "env" in out
    assert "legacy_pages" in out and "demo_api" in out
    assert out["knowledge_pitfalls"] == 30


def test_cli_legacy_dry_run_outputs_command(tmp_path, monkeypatch, capsys):
    """atlas legacy --dry-run 输出 pytest 命令与 cwd，不执行。
    dry-run 现在会真正检查目录（缺失 → rc2，见 test_legacy_step_loading），
    故指向真实存在的旧项目目录以验证「输出命令+cwd」的原意。"""
    legacy = tmp_path / "legacy-suite"
    (legacy / "legacy-web").mkdir(parents=True)
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    assert main(["--root", str(tmp_path), "legacy", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "pytest" in out
    assert "cwd=" in out


def test_cli_report_exports_runlog_format(tmp_path):
    """atlas report 将 JSONL 转为 runlog 步骤格式。"""
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "run_id": "r1", "case": "test", "env": "sit",
        "steps": [{"step": "go", "op": "action", "ok": True, "dur_ms": 10}]
    }) + "\n", encoding="utf-8")
    output = tmp_path / "runlog.jsonl"
    assert main(["--root", str(tmp_path), "report", str(runs), str(output)]) == 0
    line = json.loads(output.read_text(encoding="utf-8").strip())
    assert line["run_id"] == "r1"
    assert line["case"] == "test"
    assert line["step"] == "go"
    assert line["ok"] is True


def test_cli_tasks_output_is_utf8_decodable():
    """Windows 控制台默认 GBK，CLI 中文 intent 曾乱码；main 强制 stdout UTF-8。"""
    import io, sys
    from pathlib import Path
    raw = io.BytesIO()
    buf = io.TextIOWrapper(raw, encoding="gbk")  # 模拟 Windows GBK 控制台
    old = sys.stdout
    sys.stdout = buf
    try:
        assert main(["--root", str(Path(".")), "tasks"]) == 0
    finally:
        buf.flush()
        sys.stdout = old
        buf.detach()  # 只解绑，不关底层 BytesIO
    text = raw.getvalue().decode("utf-8")
    assert "打开业务单据列表" in text  # 中文 intent 正常而非乱码


def test_cli_compile_validates_flow_offline(tmp_path, capsys):
    """atlas compile 离线校验 flow：任务引用、assert 块早失败，不登录不执行。"""
    flow = tmp_path / "f.yaml"
    flow.write_text(
        "steps:\n  - {task: open_doc_list}\n"
        "assert:\n  - document_no != ''\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(flow)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tasks"] == ["open_doc_list"]
    assert out["asserts"] == 1


def test_cli_compile_rejects_unknown_task(tmp_path, capsys):
    """未注册 Task：离线门禁拒绝并点名。#79 起走友好 stderr + rc 2，不再抛裸 KeyError traceback。"""
    flow = tmp_path / "bad.yaml"
    flow.write_text("steps:\n  - {task: no_such_task}\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(flow)]) == 2
    assert "no_such_task" in capsys.readouterr().err


def test_cli_compile_validates_requires_before_login(tmp_path, capsys):
    """#97：compile 注释声称「数据供给字段在 compile 期就报错」，旧实现 compile() 从不碰
    requires——坏供给块要到 atlas run 登录+开浏览器后的 provision() 才炸。compile 必须接住。"""
    flow = tmp_path / "f.yaml"
    flow.write_text('requires:\n  source_order: "不是映射"\nsteps:\n  - {task: open_doc_list}\n',
                    encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(flow)]) == 2
    assert "必须是映射" in capsys.readouterr().err


def test_cli_run_rejects_bad_flow_without_browser(tmp_path, capsys):
    """#97：真实踩点——atlas run 跑坏 flow 旧实现先起真实 Chromium 走真实 SIT 登录，
    再在 compile/provision 里裸崩 KeyError（登录前的 load 检查只挡文件形态没挡结构）。
    现在结构错误在 DemoRuntime 构造**之前** rc 2——用耗时上界钉死：登录+开浏览器必然远超
    5 秒，rc2 且近乎瞬时 = 绝不启动浏览器（不浪费真实会话、不留半截现场）。"""
    flow = tmp_path / "bad.yaml"
    flow.write_text("steps:\n  - {task: no_such_task}\n", encoding="utf-8")
    import time
    started = time.monotonic()
    rc = main(["--root", str(tmp_path), "run", str(flow)])
    elapsed = time.monotonic() - started
    err = capsys.readouterr().err
    assert rc == 2 and "no_such_task" in err
    assert elapsed < 5, f"rc2 应瞬时返回，实际 {elapsed:.1f}s——疑似浏览器已启动"


def test_cli_run_valid_flow_missing_env_config_fails_fast(tmp_path, monkeypatch, capsys):
    """#103：flow 合法但「缺环境 URL / DemoApi 路径指错」时，旧实现裸 traceback（探针实锤：
    合法 flow + 未配置 DEMO_URL_SIT → __enter__ 的 ValueError 穿出 with 语句直接崩）。
    修复后入口预检与 __enter__ 同口径：rc 2 一行 stderr 点名 env 键，且绝不启动浏览器。"""
    monkeypatch.delenv("DEMO_URL_SIT", raising=False)
    flow = tmp_path / "good.yaml"
    flow.write_text("steps:\n  - {task: open_doc_list}\n", encoding="utf-8")
    import time
    started = time.monotonic()
    rc = main(["--root", str(tmp_path), "run", str(flow)])
    elapsed = time.monotonic() - started
    err = capsys.readouterr().err
    assert rc == 2 and "DEMO_URL_SIT" in err            # 点名缺失的 env 键
    assert elapsed < 5, f"rc2 应瞬时，实际 {elapsed:.1f}s——浏览器不得启动"
    # DemoApi 路径指错（URL 已配）：同样 rc 2 且点名 ATLAS_DEMO_API_PROJECT，不开浏览器。
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path / "不存在的旧项目"))
    started = time.monotonic()
    rc = main(["--root", str(tmp_path), "run", str(flow)])
    elapsed = time.monotonic() - started
    err = capsys.readouterr().err
    assert rc == 2 and "ATLAS_DEMO_API_PROJECT" in err
    assert elapsed < 5, f"路径预检必须 fail-fast，实际 {elapsed:.1f}s"


def test_cli_run_bad_legacy_web_rc2_before_browser(tmp_path, monkeypatch, capsys):
    """#138：ATLAS_LEGACY_WEB 指错时 run 旧实现完全不预检——真实 Chromium 已起、SIT 登录已烧
    才在 _load(login.py) 撞裸 FileNotFoundError（不点名配置键 #90）。修复后 CLI run 预检与
    __enter__ 同口径：rc 2 点名 ATLAS_LEGACY_WEB，浏览器不启动（仿 #103 DemoApi 预检测试）。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("DEMO_URL_SIT", "http://sit.example.com")
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(tmp_path))
    legacy = tmp_path / "legacy"
    legacy.mkdir()   # 根存在、web 缺失 → 撞 ATLAS_LEGACY_WEB 分支
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "wrong-web")
    flow = tmp_path / "good.yaml"
    flow.write_text("steps:\n  - {task: open_doc_list}\n", encoding="utf-8")
    import time
    started = time.monotonic()
    rc = main(["--root", str(tmp_path), "run", str(flow)])
    elapsed = time.monotonic() - started
    err = capsys.readouterr().err
    assert rc == 2 and "ATLAS_LEGACY_WEB" in err
    assert elapsed < 5, f"预检必须 fail-fast，实际 {elapsed:.1f}s——浏览器不得启动"


def test_dsl_validate_flow_points_step_and_policy(tmp_path, capsys):
    """#97：validate_flow 单一来源覆盖：步骤非映射点名序号与形态；requires 策略坏点名键名
    （旧实现 from_dict 报错不带是哪个 requires 键，多条件 flow 里人要回头猜哪条写错）；
    flow 级 on_env_blocked 登录前拒。"""
    from atlas.cli import main
    f1 = tmp_path / "s.yaml"
    f1.write_text("steps:\n  - 一个裸字符串步骤\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(f1)]) == 2
    assert "步骤 1 必须是映射" in capsys.readouterr().err
    f2 = tmp_path / "r.yaml"
    f2.write_text("steps:\n  - {task: open_doc_list}\n"
                  "requires:\n  x:\n    entity: SalesOrder\n    on_env_blocked: bogus\n",
                  encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(f2)]) == 2
    assert "requires.x" in capsys.readouterr().err
    f3 = tmp_path / "p.yaml"
    f3.write_text("steps:\n  - {task: open_doc_list}\non_env_blocked: nope\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "compile", str(f3)]) == 2
    assert "flow 级" in capsys.readouterr().err


def test_cli_user_input_commands_report_friendly_errors(tmp_path, capsys):
    """#79/#80：machine/summary/report/compile/run 对「用户传入的坏路径」统一给一行 stderr + rc 2，
    不再抛裸 traceback（与 overlay/recycle/review/explore 同口径；rc 2 = 输入错误）。
    run 的 load 在 DemoRuntime 构造/登录之前失败，坏 flow 路径绝不启动浏览器（fail fast）。"""
    missing = tmp_path / "does_not_exist"
    cases = {
        "machine": ["machine", str(missing) + ".yaml"],
        "summary": ["summary", str(missing) + ".jsonl"],
        "report": ["report", str(missing) + ".jsonl", str(tmp_path / "out.jsonl")],
        "compile": ["compile", str(missing) + ".yaml"],
        "run": ["run", str(missing) + ".yaml"],
    }
    for name, cmd in cases.items():
        rc = main(["--root", str(tmp_path), *cmd])
        err = capsys.readouterr().err
        assert rc == 2, f"{name} 应 rc2 而非裸 traceback，实际 {rc}"
        assert "does_not_exist" in err or "不存在" in err, f"{name} 未点名缺失文件：{err!r}"


def test_cli_machine_reports_invalid_target_friendly(tmp_path, capsys):
    """状态机文件合法但目标不可达：path() 的 ValueError 转成 rc 2 一行报错，不裸 traceback。"""
    sm = tmp_path / "sm.yaml"
    sm.write_text("initial: a\nstates:\n  a:\n    - {to: b, task: go}\n  b: []\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "machine", str(sm), "--target", "zzz"]) == 2
    assert "zzz" in capsys.readouterr().err


def test_summary_tolerates_malformed_jsonl_lines(tmp_path):
    """崩溃时 JSONL 可能写了一半的行；summarize 跳过并计数，不静默也不崩溃。"""
    import json as _json
    p = tmp_path / "broken.jsonl"
    p.write_text(
        _json.dumps({"run_id": "r1", "case": "c1", "status": "passed", "steps": []}) + "\n"
        + "NOT_JSON\n"
        + _json.dumps({"run_id": "r2", "case": "c1", "status": "failed", "steps": []}) + "\n",
        encoding="utf-8")
    result = summarize(p)
    assert result["runs"] == 2
    assert result["skipped_malformed_lines"] == 1
    assert result["status"] == {"passed": 1, "failed": 1}


def test_recorder_close_is_fsynced(tmp_path):
    """close() 必须 fsync：进程崩溃时 JSONL 不丢记录、不产生残行。"""
    import os
    from unittest.mock import patch
    from atlas.recorder import RunRecorder
    p = tmp_path / "runs.jsonl"
    rec = RunRecorder("c1", "sit", p)
    with rec.step("s1"):
        pass
    with patch("os.fsync", wraps=os.fsync) as spy:
        rec.close()
    assert spy.called  # close 内部真实调用了 fsync
    assert p.exists() and rec.close() == p  # 幂等不重复写


def test_cli_doctor_reports_base_url_source(tmp_path, monkeypatch, capsys):
    """base_url 口径必须与 runtime 一致：Atlas .env 缺失时继承旧项目 .env。

    背景：本机 Atlas 无 .env 时 doctor 曾报 false，但 atlas run 实际能连
    （继承 legacy .env），误导诊断。unset/legacy.env/atlas.env 三种来源如实上报。
    """
    monkeypatch.delenv("DEMO_URL_SIT", raising=False)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["base_url_source"] == "unset"
    assert checks["base_url_configured"] is False

    (legacy / ".env").write_text("DEMO_URL_SIT=https://legacy.example\n", encoding="utf-8")
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["base_url_source"] == "legacy.env"
    assert checks["base_url_configured"] is True

    monkeypatch.delenv("DEMO_URL_SIT", raising=False)
    (tmp_path / ".env").write_text("DEMO_URL_SIT=https://atlas.example\n", encoding="utf-8")
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["base_url_source"] == "atlas.env"


def test_cli_doctor_checks_docker_compose_consistency(tmp_path, capsys):
    """doctor 校验 Docker/compose 资产一致性：python/python3 漂移时报非零。"""
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    # 无 Docker 资产的 tmp root 视为一致
    assert checks["docker_assets_consistent"] is True

    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.12-slim\nRUN python3 -m pytest -q\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  x:\n    command: sh -c \"python -m pytest -q\"\n", encoding="utf-8")
    main(["--root", str(tmp_path), "doctor"])
    checks = json.loads(capsys.readouterr().out)
    assert checks["docker_cmd_matches_compose"] is False
    assert checks["docker_assets_consistent"] is False


def test_cli_doctor_survives_non_utf8_dockerfile(tmp_path, capsys):
    """#83：present-but-malformed（非 UTF-8）的 Dockerfile 不得让 doctor 抛 UnicodeDecodeError
    崩掉整个诊断（doctor 的职责是汇报状态、CI/人据此判断）。旧实现裸 read_text(utf-8) → traceback
    + 零 JSON 输出。修复后完整输出 JSON，把不可读记为 docker_unreadable + 不一致。"""
    (tmp_path / "Dockerfile").write_bytes(b"FROM py:3.11\nRUN python \xff\xfe pytest\n")
    assert main(["--root", str(tmp_path), "doctor"]) == 1          # 不一致(rc1)，但不是崩溃
    checks = json.loads(capsys.readouterr().out)                   # 能解析 = doctor 完整输出了诊断
    assert checks["docker_unreadable"]                             # 点名是哪个文件读不出
    assert checks["docker_assets_consistent"] is False


def test_cli_doctor_survives_non_utf8_compose(tmp_path, capsys):
    """#83：Dockerfile 正常但 docker-compose.yml 非 UTF-8：_compose_cmd_matches 无法确认一致性 →
    判不一致并完整汇报，绝不裸崩。"""
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.12-slim\nRUN python3 -m pytest -q\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_bytes(
        b"services:\n  x:\n    command: \xff python -m pytest\n")
    assert main(["--root", str(tmp_path), "doctor"]) == 1
    checks = json.loads(capsys.readouterr().out)
    assert checks["docker_cmd_matches_compose"] is False
    assert checks["docker_assets_consistent"] is False

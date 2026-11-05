"""造单回收命令测试。"""
import json
import pytest
from atlas.probes import CreateQuota
from atlas.cli import main


def test_quota_save_for_recycle(tmp_path):
    quota = CreateQuota(3)
    quota.reserve("SalesOrder", {"where": {"customer": "CUST0000001"}})
    quota.record_document("SalesOrder", {"orderNo": "SO-DEMO-0007", "remark": "测试非人工创建"})
    path = tmp_path / "recycle.jsonl"
    quota.save_for_recycle(path)
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["entity"] == "SalesOrder"
    assert record["document"]["orderNo"] == "SO-DEMO-0007"


def test_quota_save_appends_existing(tmp_path):
    path = tmp_path / "recycle.jsonl"
    path.write_text(json.dumps({"entity": "Customer", "document": {"code": "C1"}}) + "\n",
                    encoding="utf-8")
    quota = CreateQuota(3)
    quota.reserve("SalesOrder", {})
    quota.record_document("SalesOrder", {"orderNo": "SP001"})
    quota.save_for_recycle(path)
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


def test_cli_recycle_lists_records(tmp_path, capsys):
    path = tmp_path / "recycle.jsonl"
    path.write_text(json.dumps({"entity": "SalesOrder", "document": {"orderNo": "SP99"}}) + "\n",
                    encoding="utf-8")
    assert main(["--root", str(tmp_path), "recycle", "--input", str(path)]) == 0
    out = capsys.readouterr().out
    assert "SP99" in out


def test_cli_recycle_empty(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "recycle",
                 "--input", str(tmp_path / "nonexist.jsonl")]) == 0
    assert capsys.readouterr().out.strip() == "[]"


def test_cli_recycle_cleanup_blocked_in_production(tmp_path, capsys):
    path = tmp_path / "recycle.jsonl"
    path.write_text(json.dumps({"entity": "SalesOrder", "document": {"orderNo": "SP99"}}) + "\n",
                    encoding="utf-8")
    rc = main(["--root", str(tmp_path), "--env", "prod-hk", "recycle",
               "--input", str(path), "--cleanup"])
    assert rc == 1
    assert "生产环境禁止" in capsys.readouterr().err


def test_cli_recycle_cleanup_lists_records_in_non_prod(tmp_path, capsys):
    """非生产 --cleanup 列出记录但不自动删除，需人审。"""
    path = tmp_path / "recycle.jsonl"
    path.write_text(json.dumps({"entity": "SalesOrder", "document": {"orderNo": "SP99"}}) + "\n",
                    encoding="utf-8")
    rc = main(["--root", str(tmp_path), "recycle", "--input", str(path), "--cleanup"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "SP99" in err
    assert "人工确认" in err


def test_cli_recycle_lists_multiple_records(tmp_path, capsys):
    """recycle 列出多条记录。"""
    path = tmp_path / "recycle.jsonl"
    path.write_text(
        json.dumps({"entity": "SalesOrder", "document": {"orderNo": "SP1"}}) + "\n"
        + json.dumps({"entity": "Customer", "document": {"orderNo": "C1"}}) + "\n",
        encoding="utf-8")
    rc = main(["--root", str(tmp_path), "recycle", "--input", str(path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2


def test_save_for_recycle_survives_torn_and_non_object_lines(tmp_path):
    """残行/非对象不得顶掉整份回收清单：一条写坏的历史行不应让本次造单信息也存不下去
    （save_for_recycle 用 open('w') 重写全表，旧实现 json.loads 遇坏行抛错→本次回收永久丢失）。"""
    path = tmp_path / "recycle.jsonl"
    good = json.dumps({"entity": "Customer", "document": {"code": "C1"}})
    path.write_text(good + "\n" + '{"entity": "SalesOrder", "document": {"orderNo": "SP' + "\n"
                    + "[1, 2, 3]" + "\n", encoding="utf-8")      # 残行 + 非对象行
    quota = CreateQuota(3)
    quota.reserve("SalesOrder", {})
    quota.record_document("SalesOrder", {"orderNo": "SP-NEW", "remark": "非人工创建"})
    quota.save_for_recycle(path)                                 # 修复前这里抛 JSONDecodeError
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert {r["entity"] for r in lines} == {"Customer", "SalesOrder"}  # 坏行丢弃，好行+新单保留


def test_cli_recycle_survives_torn_line(tmp_path, capsys):
    """atlas recycle 读侧同样要扛残行：一个坏行不应让人连待回收的单都列不出。"""
    path = tmp_path / "recycle.jsonl"
    path.write_text(json.dumps({"entity": "SalesOrder", "document": {"orderNo": "SP99"}}) + "\n"
                    + '{"entity": "X", "doc' + "\n", encoding="utf-8")   # 残行
    assert main(["--root", str(tmp_path), "recycle", "--input", str(path)]) == 0
    out = capsys.readouterr()
    listed = json.loads(out.out)                                 # stdout 仍是纯 JSON 数组
    assert [r["document"]["orderNo"] for r in listed] == ["SP99"]
    assert "无法解析" in out.err                                 # 残行被点名跳过，不静默


def test_cli_recycle_cleanup_non_dict_document_no_crash(tmp_path, capsys):
    """--cleanup 列出时 document 非 dict 不得 AttributeError：降级显示 ?。"""
    path = tmp_path / "recycle.jsonl"
    path.write_text(
        json.dumps({"entity": "SalesOrder", "document": "corrupted-string"}) + "\n"
        + json.dumps({"entity": "Customer", "document": {"orderNo": "C1"}}) + "\n",
        encoding="utf-8")
    rc = main(["--root", str(tmp_path), "recycle", "--input", str(path), "--cleanup"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "SalesOrder: ?" in err      # 非 dict 的 document 不再崩，降级 ?
    assert "Customer: C1" in err


def test_runtime_exit_persists_quota_documents_for_recycle(tmp_path):
    """#86 断链回归：本次 quota 建过的单必须在 run 退出时落盘——
    旧实现 save_for_recycle 从未接入执行链，真实 SIT 建单只进内存、进程退出即丢。"""
    from atlas.config import AtlasConfig
    from atlas.probes import DataProvider
    from atlas.runtime import DemoRuntime
    config = AtlasConfig(tmp_path)
    runtime = DemoRuntime(config, login=False)
    runtime.provider = DataProvider(config, [], create_quota=2)
    runtime.provider.quota.reserve("SalesOrder", {"where": {"customer": "CUST0000001"}})
    runtime.provider.quota.record_document("SalesOrder", {"orderNo": "SO-EXIT-7", "remark": "非人工创建"})
    runtime.__exit__(None, None, None)
    path = config.reports_dir / "recycle_list.jsonl"
    assert path.exists()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(r["document"].get("orderNo") == "SO-EXIT-7" for r in records)


def test_runtime_exit_skips_recycle_write_without_creations(tmp_path):
    """默认配额 0 / 未造单：退出时一个字都不写，回收清单不被空轮扰动。"""
    from atlas.config import AtlasConfig
    from atlas.probes import DataProvider
    from atlas.runtime import DemoRuntime
    config = AtlasConfig(tmp_path)
    runtime = DemoRuntime(config, login=False)
    runtime.provider = DataProvider(config, [], create_quota=0)
    runtime.__exit__(None, None, None)
    assert not (config.reports_dir / "recycle_list.jsonl").exists()


def test_runtime_exit_recycle_failure_surfaces_without_case_error(tmp_path):
    """回收落盘失败（reports 路径被文件占住）且无在用例异常：失败如实上报，不静默丢回收信息。"""
    from atlas.config import AtlasConfig
    from atlas.probes import DataProvider
    from atlas.runtime import DemoRuntime
    blocker = tmp_path / "reports"
    blocker.write_text("占位", encoding="utf-8")
    config = AtlasConfig(tmp_path)
    runtime = DemoRuntime(config, login=False)
    runtime.provider = DataProvider(config, [], create_quota=1)
    runtime.provider.quota.record_document("SalesOrder", {"orderNo": "SO-X", "remark": "非人工创建"})
    with pytest.raises(OSError):
        runtime.__exit__(None, None, None)


def test_runtime_exit_recycle_failure_does_not_bury_case_error(tmp_path):
    """有在用例异常时：回收落盘失败绝不顶掉真因（with 语义下用例异常继续传播）。"""
    from atlas.config import AtlasConfig
    from atlas.probes import DataProvider
    from atlas.runtime import DemoRuntime
    blocker = tmp_path / "reports"
    blocker.write_text("占位", encoding="utf-8")
    config = AtlasConfig(tmp_path)
    runtime = DemoRuntime(config, login=False)
    runtime.provider = DataProvider(config, [], create_quota=1)
    runtime.provider.quota.record_document("SalesOrder", {"orderNo": "SO-X", "remark": "非人工创建"})
    runtime.__exit__(RuntimeError, RuntimeError("用例真因"), None)   # 不应抛：清理失败让位真因


def test_runtime_exit_both_cleanup_errors_both_surfaced(tmp_path):
    """#121 面D：回收落盘失败 + 会话关闭失败同时发生——旧实现 session.__exit__ 抛错直接向外
    传播，`recycle_error` 永远够不着 raise：运维只看到「浏览器关不上」，不知道回收清单也没写
    进去（#85 拦截无痕同红线，清理错误链也要全留痕）。现逐个 try 收集，单错误原样抛（既有
    两条 #86 测口径不变），双错误聚合成 ExceptionGroup 两个都点名。有在用例异常时仍让位真因。"""
    from atlas.config import AtlasConfig
    from atlas.probes import DataProvider
    from atlas.runtime import DemoRuntime
    blocker = tmp_path / "reports"
    blocker.write_text("占位", encoding="utf-8")     # 回收落盘撞非目录 → OSError
    config = AtlasConfig(tmp_path)
    runtime = DemoRuntime(config, login=False)
    runtime.provider = DataProvider(config, [], create_quota=1)
    runtime.provider.quota.record_document("SalesOrder", {"orderNo": "SO-X", "remark": "非人工创建"})

    class CrashSession:
        def __exit__(self, exc_type, exc, tb): raise OSError("浏览器关闭失败：驱动已崩")
    runtime.session = CrashSession()
    with pytest.raises(ExceptionGroup) as caught:
        runtime.__exit__(None, None, None)
    messages = [str(e) for e in caught.value.exceptions]
    assert len(messages) == 2
    assert any("reports" in m for m in messages), f"回收错误必须也在组里: {messages}"
    assert any("驱动已崩" in m for m in messages)
    # 有在用例异常时双清理错误也不顶真因（#82 口径）
    runtime.__exit__(RuntimeError, RuntimeError("用例真因"), None)


def test_cli_recycle_default_follows_reports_dir(tmp_path, capsys):
    """CLI 默认与 run 落盘同源：不传 --input 读 root/reports/recycle_list.jsonl，不再赌 CWD。"""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "recycle_list.jsonl").write_text(
        json.dumps({"entity": "SalesOrder", "document": {"orderNo": "SO-DEFAULT"}}) + "\n",
        encoding="utf-8")
    assert main(["--root", str(tmp_path), "recycle"]) == 0
    assert "SO-DEFAULT" in capsys.readouterr().out
"""造单回收命令测试。"""
import json
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
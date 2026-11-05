"""探索 trace→草稿编译与生产防线。浏览器实会话不在离线测试面，按纪律另行真实验证。"""
import json
from atlas.experience import ExperienceJournal
from atlas.explore import compile_trace, extract_candidates
from atlas.cli import main


def test_extract_candidates_only_actionable_dedup_and_capped():
    snapshot = (
        '- page "销售订单"\n'
        '  - button "Save"\n'
        '  - textbox "Payment Terms"\n'
        '  - generic "随便什么文本"\n'
        '  - text "说明"\n'
        '  - button "Save"\n'          # 重复：去重
        '  - combobox "Status" [expanded]\n'
    )
    got = extract_candidates(snapshot)
    assert got == [{"role": "button", "name": "Save"},
                   {"role": "textbox", "name": "Payment Terms"},
                   {"role": "combobox", "name": "Status"}]


def test_extract_candidates_drops_punctuation_only_names():
    """真塔实测：分页箭头 </> 与空名混进候选——纯符号名无审核价值，直接滤掉。"""
    snapshot = (
        '- button "<"\n'
        '- button ">"\n'
        '- button "«"\n'
        '- button "OK"\n'
        '- textbox "备注"\n'           # 中文名单字符也算短：两字保留
    )
    assert extract_candidates(snapshot) == [{"role": "button", "name": "OK"},
                                            {"role": "textbox", "name": "备注"}]


def test_compile_trace_produces_pending_drafts_only(tmp_path):
    trace = tmp_path / "trace.jsonl"
    snap = tmp_path / "001.aria.yaml"
    snap.write_text('- button "Confirm"\n- textbox "Remark"\n', encoding="utf-8")
    trace.write_text(json.dumps({"checkpoint": 1, "url": "https://sit.example/list",
                                 "snapshot_file": str(snap)}) + "\n", encoding="utf-8")
    journal = ExperienceJournal(tmp_path)
    summary = compile_trace(trace, journal)
    assert summary["steps"] == 1 and summary["selector_candidates"] == 2
    rows = [json.loads(l) for l in
            (tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 3  # 1 步骤 + 2 选择器候选
    assert all(r["status"] == "pending_review" for r in rows)  # 原矿，不是资产
    kinds = {r["kind"] for r in rows}
    assert kinds == {"trace_step", "selector_candidate"}


def test_compile_trace_dedups_selector_across_checkpoints(tmp_path):
    snap = tmp_path / "s.yaml"
    snap.write_text('- button "Save"\n', encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps({"checkpoint": 1, "url": "https://a/1", "snapshot_file": str(snap)}) + "\n"
        + json.dumps({"checkpoint": 2, "url": "https://a/2", "snapshot_file": str(snap)}) + "\n",
        encoding="utf-8")
    journal = ExperienceJournal(tmp_path)
    summary = compile_trace(trace, journal)
    assert summary["steps"] == 2
    assert summary["selector_candidates"] == 1  # Save 只报一次，第二个 checkpoint 不重复占塔


def test_compile_trace_survives_torn_utf8_bytes(tmp_path):
    """#109：交互会话 Ctrl-C 把最后一行多字节字符腰斩在字节上（非法 UTF-8 尾序列）。
    旧实现 read_text 裸 UnicodeDecodeError 顶掉整份编译——#68 的逐行容错一行都没轮到，
    因为崩在读文件本身。修复后：坏字节回落 replace 读，残尾行自然落进 malformed_lines，
    前面的合法 checkpoint 照常编译。"""
    trace = tmp_path / "trace.jsonl"
    snap = tmp_path / "001.aria.yaml"
    snap.write_text('- button "Confirm"\n', encoding="utf-8")
    good = json.dumps({"checkpoint": 1, "url": "https://sit/x", "snapshot_file": str(snap)}) + "\n"
    # 残尾：断在「按」字的第二个字节上 → 非法 UTF-8
    torn = '{"checkpoint":2,"url":"https://sit/按钮' .encode("utf-8")[:-1]
    trace.write_bytes(good.encode("utf-8") + torn)
    summary = compile_trace(trace, ExperienceJournal(tmp_path))
    assert summary["checkpoints"] == 1
    assert summary["malformed_lines"] == 1        # 坏尾行点名计数，不静默
    assert summary["steps"] == 1 and summary["selector_candidates"] == 1


def test_compile_trace_reads_torn_snapshot_not_batch_killer(tmp_path):
    """#109：checkpoint 写快照同样可被 Ctrl-C 腰斩出非法 UTF-8 尾字节。
    旧实现 snap.read_text 裸 UnicodeDecodeError 顶掉整份编译（#68 只护了 trace 行，
    快照读取面漏了）。修复后：replace 读入，截断前的完整候选仍是有效原矿，
    腰斩掉的字符不产生垃圾候选，其余 checkpoint 照常编译。"""
    snap_ok = tmp_path / "001.aria.yaml"
    snap_ok.write_text('- button "Confirm"\n- textbox "Remark"\n', encoding="utf-8")
    snap_torn = tmp_path / "002.aria.yaml"
    # 首行完整；第 2 行在「客」的第一个字节后被腰斩 → 非法 UTF-8 尾序列
    full = '- button "提交按钮"\n- textbox "客户"\n'.encode("utf-8")
    snap_torn.write_bytes(full[:38])   # 「客」3 字节只写到 2 个 → 尾部非法 UTF-8 序列
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps({"checkpoint": 1, "url": "https://a/1", "snapshot_file": str(snap_ok)}) + "\n"
        + json.dumps({"checkpoint": 2, "url": "https://a/2", "snapshot_file": str(snap_torn)}) + "\n",
        encoding="utf-8")
    journal = ExperienceJournal(tmp_path)
    summary = compile_trace(trace, journal)
    assert summary["steps"] == 2                          # 坏快照不顶编译
    assert summary["selector_candidates"] >= 2            # Confirm/Remark/提交按钮 仍在
    rows = [json.loads(l) for l in
            (tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    names = {r.get("name") for r in rows}
    assert "Confirm" in names and "提交按钮" in names     # 截断前的完整候选不丢
    assert not any("\ufffd" in (r.get("name") or "") for r in rows)  # 替换字符不冒充候选


def test_compile_trace_survives_torn_and_non_object_lines(tmp_path):
    """探索会话逐行 append 且无 fsync，崩溃/中断可能留下残行；一条坏行绝不能顶掉整份 trace 编译。
    与 reporting 的 JSONL 读取容错一致：跳过并计数（malformed_lines），合法 checkpoint 照常编译。"""
    trace = tmp_path / "trace.jsonl"
    snap = tmp_path / "001.aria.yaml"
    snap.write_text('- button "Confirm"\n', encoding="utf-8")
    trace.write_text(
        json.dumps({"checkpoint": 1, "url": "https://sit/x", "snapshot_file": str(snap)}) + "\n" +
        '{"checkpoint":2,"url":"https://sit/2","sn' + "\n" +            # 崩溃残行（JSONDecodeError）
        "null\n" +                                                       # 合法 JSON 但非对象
        json.dumps({"checkpoint": 3, "url": "https://sit/y", "snapshot_file": ""}) + "\n",
        encoding="utf-8")
    summary = compile_trace(trace, ExperienceJournal(tmp_path))
    assert summary["checkpoints"] == 2          # 只有 2 个合法对象行被编译
    assert summary["malformed_lines"] == 2      # 残行 + null 计入上报，不静默冒充成功
    assert summary["steps"] == 2 and summary["selector_candidates"] == 1


def test_explore_refuses_production(tmp_path, monkeypatch, capsys):
    """探索者会真实点击：生产环境连会话都不给开。"""
    monkeypatch.delenv("CDP_DEBUG", raising=False)
    assert main(["--root", str(tmp_path), "--env", "prod-eu", "explore"]) == 1
    assert "生产" in capsys.readouterr().err


def test_explore_compile_via_cli(tmp_path, capsys):
    snap = tmp_path / "001.aria.yaml"
    snap.write_text('- textbox "Customer"\n', encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"checkpoint": 1, "url": "https://sit.example/x",
                                 "snapshot_file": str(snap)}) + "\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "explore", "--compile", str(trace)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["selector_candidates"] == 1 and out["next"].startswith("atlas review")

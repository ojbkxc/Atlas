"""探索 trace→草稿编译与生产防线。浏览器实会话不在离线测试面，按纪律另行真实验证。"""
import json
from pathlib import Path
from types import SimpleNamespace

import atlas.explore as explore
from atlas.experience import ExperienceJournal
from atlas.explore import compile_trace, extract_candidates, start
from atlas.review import ReviewQueue
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


def test_recompile_after_decision_exactly_one_row_per_candidate(tmp_path):
    """终验#8 防回退：同一探针候选登记两次→队列恰一条（跨决定态幂等）。

    旧 append_draft 同 id 合并只认 pending_review 行：首轮草稿处置（批准/驳回）后
    二次 explore --compile 同一 trace，追加出同 id 第二行——磁盘实锤三条探针草稿
    （aaa919a656d3/1f52715010fc/6aae7590ce3c）各两份。重复行还放大 reject/approve
    按 id 的全行翻转，把已批准的历史状态改写（跨决定历史污染）。现同 id 命中一律
    只增 seen、刷新 evidence：恰一条、决定态不回翻、pending 行照常可审。
    """
    snap = tmp_path / "001.aria.yaml"
    snap.write_text('- button "Confirm"\n', encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"checkpoint": 1, "url": "https://sit.example/list",
                                 "snapshot_file": str(snap)}) + "\n", encoding="utf-8")
    journal = ExperienceJournal(tmp_path)
    compile_trace(trace, journal)
    # 人先处置 trace_step（对应终验清塔真实动作），selector_candidate 留 pending
    queue = ReviewQueue(tmp_path)
    step_row = next(r for r in queue.rows() if r["kind"] == "trace_step")
    queue.reject(step_row["id"], reason="probe 素材暂不成 flow")
    # 二次编译同一份 trace：候选「再登记」，队列仍恰两条（每 id 一条）
    compile_trace(trace, journal)
    rows = [json.loads(l) for l in
            (tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2, "同 id 再登记绝不追加第二条（#118 去重纪律跨决定态）"
    assert len({r["id"] for r in rows}) == 2
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["trace_step"]["status"] == "rejected"      # 终态记录不回翻成待审
    assert by_kind["trace_step"]["seen"] == 2                 # 复登记只计数
    assert by_kind["selector_candidate"]["status"] == "pending_review"
    assert by_kind["selector_candidate"]["seen"] == 2         # pending 合并计数不丢
    assert [r["id"] for r in queue.pending("selector_candidate")] == \
        [by_kind["selector_candidate"]["id"]]                 # 塔内仍恰一条可审


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


# ---------- start() 的 cmd 文件通道（无头模式，协议见 explore.py 模块 docstring） ----------

class _FakePage:
    def __init__(self):
        self.url = "https://sit.example/list"

    def title(self):
        return "Fake Console"

    def aria_snapshot(self):
        return '- button "Save"\n- textbox "Remark"\n'


def _fake_explore_session(tmp_path, monkeypatch):
    """离线替身：DemoRuntime 换成不碰浏览器的假会话，轮询间隔归零，CDP env 隔离。"""
    closed = []

    class _Runtime:
        def __init__(self, config, login=True, account=None):
            self.account = account or "fake"
            self.actor = SimpleNamespace(page=_FakePage())

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)
            return False

    monkeypatch.setattr("atlas.runtime.DemoRuntime", _Runtime)
    monkeypatch.setattr(explore, "_CMD_POLL_INTERVAL", 0.0)   # 测试不真睡
    monkeypatch.delenv("CDP_DEBUG", raising=False)            # start() setdefault 写回，结束还原
    config = SimpleNamespace(is_production=False,
                             reports_dir=tmp_path / "reports",
                             artifacts_dir=tmp_path / "artifacts")
    return config, closed


def test_explore_cmd_file_blank_line_records_checkpoint(tmp_path, monkeypatch):
    """无头主缺陷回归：stdin 不可用（console 立刻 EOF）时 cmd 文件空行照样落 checkpoint。

    旧实现 console=input 在 Windows 后台管道上直接 EOF 崩会话 → 0 checkpoint、
    trace 为空、--compile 没输入。cmd 文件通道下：空行=回车=checkpoint，消费后
    文件清空；console EOF 只表示「暂无输入」不再顶掉会话。会话启动即清空 cmd
    文件（残留 stop 不许串场），所以驱动命令由 fake console 兼任写入（=会话起来
    后外部驱动才写，与真实时序一致）。"""
    cmd = tmp_path / "explore.cmd"
    config, closed = _fake_explore_session(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_console(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            cmd.write_text("\n", encoding="utf-8")   # 外部驱动此刻请求一个 checkpoint
            raise EOFError                            # 无头 stdin：读取立刻 EOF（缺陷现场）
        return "stop"

    trace = start(config, trace=tmp_path / "trace.jsonl",
                  console=fake_console, cmd_file=cmd)
    rows = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["checkpoint"] for r in rows] == [1]     # trace 行格式不动（compile 契约）
    assert rows[0]["url"] == "https://sit.example/list"
    assert rows[0]["title"] == "Fake Console"
    assert rows[0]["snapshot_lines"] == 2
    assert Path(rows[0]["snapshot_file"]).is_file()
    assert cmd.read_text(encoding="utf-8") == ""      # 命令已消费清空，绝不二次执行
    assert closed == [True]                           # 会话正常收尾，不是崩出去


def test_explore_cmd_file_stop_ends_session(tmp_path, monkeypatch):
    """stop 命令经 cmd 文件干净结束会话：0 checkpoint 不留半截，文件不残留，runtime 收尾。"""
    cmd = tmp_path / "explore.cmd"
    config, closed = _fake_explore_session(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_console(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            cmd.write_text("STOP\n", encoding="utf-8")   # 大小写不敏感
            raise EOFError
        raise AssertionError("stop 消费后会话应已结束，console 不应被再次等待")

    trace = start(config, trace=tmp_path / "trace.jsonl",
                  console=fake_console, cmd_file=cmd)
    assert trace == tmp_path / "trace.jsonl"
    assert not trace.exists() or trace.read_text(encoding="utf-8") == ""   # 0 checkpoint
    assert calls["n"] == 1
    assert closed == [True]
    assert cmd.read_text(encoding="utf-8") == ""       # stop 已消费，不残留不重触发


def test_explore_cmd_file_via_env_var(tmp_path, monkeypatch):
    """ATLAS_EXPLORE_CMD_FILE 环境变量接线：cli.py 一行不改即可开无头通道。"""
    cmd = tmp_path / "env_channel.cmd"
    monkeypatch.setenv("ATLAS_EXPLORE_CMD_FILE", str(cmd))
    config, closed = _fake_explore_session(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_console(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            cmd.write_text("\n", encoding="utf-8")
            raise EOFError
        return "stop"

    trace = start(config, trace=tmp_path / "trace.jsonl", console=fake_console)
    rows = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["checkpoint"] for r in rows] == [1]
    assert cmd.read_text(encoding="utf-8") == ""
    assert closed == [True]


def test_explore_cmd_file_and_console_coexist(tmp_path, monkeypatch):
    """两通道共存：cmd 文件两个空行=两个 checkpoint，console 回车=第三个；文件里
    已到达的命令先处理（先到者），通道间不互抢不漏记。"""
    cmd = tmp_path / "explore.cmd"
    config, closed = _fake_explore_session(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_console(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            cmd.write_text("\n\n", encoding="utf-8")   # 外部驱动连按两次回车
            raise EOFError
        if calls["n"] == 2:
            return ""                                   # 人在 console 上回车 → 第三个
        return "stop"

    trace = start(config, trace=tmp_path / "trace.jsonl",
                  console=fake_console, cmd_file=cmd)
    rows = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["checkpoint"] for r in rows] == [1, 2, 3]
    for i in (1, 2, 3):
        assert (tmp_path / "artifacts" / "exploration" / "trace" / f"{i:03d}.aria.yaml").is_file()
    assert calls["n"] == 3
    assert cmd.read_text(encoding="utf-8") == ""
    assert closed == [True]


def test_explore_cmd_file_unknown_command_named_not_executed(tmp_path, monkeypatch, capsys):
    """未知命令不冒充 checkpoint 也不无限残留：stderr 点名（ASCII WARN: 前缀）后清空。"""
    cmd = tmp_path / "explore.cmd"
    config, closed = _fake_explore_session(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_console(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            cmd.write_text("status?\n", encoding="utf-8")
            raise EOFError
        return "stop"

    trace = start(config, trace=tmp_path / "trace.jsonl",
                  console=fake_console, cmd_file=cmd)
    assert not trace.exists() or trace.read_text(encoding="utf-8") == ""   # 未落任何 checkpoint
    err = capsys.readouterr().err
    assert "WARN" in err and "status?" in err
    assert cmd.read_text(encoding="utf-8") == ""
    assert closed == [True]


# ---------- C4：读侧同防线（旧 trace 带票据 → 塔行二次净化） ----------

def test_compile_trace_sanitizes_legacy_ticket_url(tmp_path):
    """旧 trace 是净化前落盘的（真实 SIT 实测带 `?ticket=ST-…`）——读侧必须同防线，
    否则 `--compile` 把凭据二次落盘进审核塔草稿（红线 #2）。净化幂等，业务参数保留。"""
    trace = tmp_path / "trace.jsonl"
    snap = tmp_path / "s.yaml"
    snap.write_text('- button "Save"\n', encoding="utf-8")
    trace.write_text(json.dumps({
        "checkpoint": 1, "snapshot_file": str(snap),
        "url": "https://sit.example/?ticket=ST-9&docNo=SO-1#/list?page=2"}) + "\n",
        encoding="utf-8")
    summary = compile_trace(trace, ExperienceJournal(tmp_path))
    raw = (tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8")
    assert "ST-9" not in raw, "旧 trace 的凭据被二次落盘到塔 = 红线 #2 违反"
    assert "ticket=<redacted>" in raw and "docNo=SO-1" in raw
    assert "/list?page=2" in raw, "路由坐标保留（塔行要能看出在哪个页面）"
    assert summary["steps"] == 1 and summary["selector_candidates"] == 1


def test_record_checkpoint_breaks_after_torn_tail_keeps_both_evidence(tmp_path):
    """rb-2：explore 的 trace 追加此前缺 schedule_runs/experience 都有的**尾行补断行**守卫。
    复刻上一轮 Ctrl-C 腰斩（UTF-8 完整但 JSON 半行的残尾）后，下一次 checkpoint 被裸 append
    黏在残行后 → 合成一条坏行 → compile_trace「跳过残行」把**两条证据一起**跳掉。
    修复后：追加前检尾字节非换行先补断行，新 checkpoint 独占一行必被读回（残行照旧跳过）。
    负向对照：同一 trace 直接追加（不补断行）确实只读回 1 条——证明守卫有牙而非恒真。"""
    from atlas.explore import _record_checkpoint
    from atlas.atomic_io import append_text_locked

    class Page:
        url = "https://sit/x"
        def title(self): return "t"
        def aria_snapshot(self): return '- button "Confirm"\n'

    shots = tmp_path / "shots"; shots.mkdir()
    # 腰斩残行：UTF-8 完整、JSON 半行、无尾换行（真机 Ctrl-C 的典型形态）
    torn = json.dumps({"checkpoint": 1, "url": "https://sit/x"})[:20]
    t_guarded = tmp_path / "guarded.jsonl"
    t_guarded.write_text(torn, encoding="utf-8")
    _record_checkpoint(Page(), t_guarded, shots, 2)
    def _readable(path):
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))     # 与 compile_trace 同口径：残行解析必败→跳过
            except json.JSONDecodeError:
                continue
        return out
    rows = _readable(t_guarded)
    assert [r["checkpoint"] for r in rows] == [2], f"新 checkpoint 必须独占一行被读回：{rows}"

    # 负向对照：同样起点，裸追加（不补断行）→ 黏连成一条坏行，checkpoint 2 消失
    t_naive = tmp_path / "naive.jsonl"
    t_naive.write_text(torn, encoding="utf-8")
    append_text_locked(t_naive, json.dumps({"ts": "x", "checkpoint": 2, "url": "https://sit/x",
                                            "title": "t", "snapshot_file": "f",
                                            "snapshot_lines": 1}, ensure_ascii=False))
    assert _readable(t_naive) == [], "不补断行的对照应一条可读记录都不剩（黏连坏行）"

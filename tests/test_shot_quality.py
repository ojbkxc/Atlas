"""wave4 C 轴 pf-6：取证截图降质 opt-in + 附件复制原子/并行小优化。

判据（任务书 pf-6）：
- ATLAS_SHOT_QUALITY 未设 → PNG 保真：screenshot 实参与历史逐字一致（path +
  full_page=False）、文件名 .png（负向对照：不设 env 时逐字节等价，锁步钉住）。
- `jpeg` / `jpeg,q=N` → type=jpeg(+quality=N)、后缀 .png→.jpg——后缀必须随内容变
  （附件媒体类型按后缀派生，.png 名装 JPEG 字节 = 假内容）。
- 坏形态（未知格式/未知选项/quality 非整数或越界）→ ValueError 点名，且**不被**
  取证的尽力而为 except 吞成 None（解析在 snapshot 的 try 之外）。
- 失败腿截图不可省：旋钮只降编码保真度——capture(失败) 仍产出截图，后缀随旋钮。
- 附件复制：多 ref 批量并行、条目序 == 输入序、落盘原子（tmp+replace，无 .tmp 残留）；
  单 ref 直落不启线程池；产物字节与旧实现逐字一致。
"""
import json
from types import SimpleNamespace

import pytest

from atlas.allure_report import emit_allure, write_attachment


# ---- 取证截图降质旋钮（session.snapshot）-----------------------------------

class _FakePage:
    def __init__(self, fail=False):
        self.closed = False
        self.url = "http://sit.example.com"
        self.shot_calls = []
        self.fail = fail

    def is_closed(self):
        return self.closed

    def screenshot(self, **kwargs):
        if self.fail:
            raise RuntimeError("browser died mid-shot")
        self.shot_calls.append(kwargs)


def _session(tmp_path, page=None):
    from atlas.session import BrowserSession
    sess = BrowserSession(SimpleNamespace(artifacts_dir=tmp_path / "artifacts", mode="run"))
    sess.page = page if page is not None else _FakePage()
    return sess


def test_snapshot_default_png_call_shape_and_suffix(tmp_path, monkeypatch):
    """负向对照：不设 env → screenshot 实参与历史逐字一致、后缀 .png（默认路径逐字节不变）。"""
    from atlas.session import SHOT_QUALITY_ENV
    monkeypatch.delenv(SHOT_QUALITY_ENV, raising=False)
    page = _FakePage()
    sess = _session(tmp_path, page)
    path = sess.snapshot("save_FAIL")
    assert page.shot_calls == [{"path": str(path), "full_page": False}]
    assert path.name.endswith(".png")
    assert path.parent == tmp_path / "artifacts" / "snapshots"


def test_snapshot_jpeg_optin_sets_type_and_jpg_suffix(tmp_path, monkeypatch):
    from atlas.session import SHOT_QUALITY_ENV
    monkeypatch.setenv(SHOT_QUALITY_ENV, "jpeg")
    page = _FakePage()
    sess = _session(tmp_path, page)
    path = sess.snapshot("save_FAIL")
    assert path.name.endswith(".jpg")                     # 后缀随内容变
    assert page.shot_calls[0]["type"] == "jpeg"
    assert page.shot_calls[0]["full_page"] is False
    assert "quality" not in page.shot_calls[0]             # 未指定 q → 不塞 quality


def test_snapshot_jpeg_quality_values(tmp_path, monkeypatch):
    from atlas.session import SHOT_QUALITY_ENV
    for raw, expected in (("jpeg,q=80", 80), ("jpeg,q=0", 0), ("jpeg,q=100", 100),
                          (" jpeg , q=70 ", 70)):
        monkeypatch.setenv(SHOT_QUALITY_ENV, raw)
        page = _FakePage()
        sess = _session(tmp_path, page)
        sess.snapshot("s")
        assert page.shot_calls[-1].get("quality") == expected, raw


@pytest.mark.parametrize("raw", ["png", "webp", "jpeg2", "jpe", "jpeg,q=abc",
                                 "jpeg,q=999", "jpeg,q=-1", "jpeg,bogus=1",
                                 "jpeg,", "jpeg,q="])
def test_bad_quality_value_raises_loud_not_swallowed(tmp_path, monkeypatch, raw):
    """坏形态点名不静默回落：ValueError 直接从 snapshot 抛出——**不被**取证的
    尽力而为 except 吞成 None（吞掉=取证静默消失 + 「没降质」被当成配置生效=假配置）。"""
    from atlas.session import SHOT_QUALITY_ENV
    monkeypatch.setenv(SHOT_QUALITY_ENV, raw)
    sess = _session(tmp_path, _FakePage())
    with pytest.raises(ValueError, match="ATLAS_SHOT_QUALITY"):
        sess.snapshot("save_FAIL")
    assert sess.page.shot_calls == []                       # 点名失败即不再截图


def test_capture_failure_leg_still_screenshots_under_knob(tmp_path, monkeypatch):
    """失败腿截图不可省：降质旋钮只降编码保真度，不提供「不截图」通道。"""
    from atlas.session import SHOT_QUALITY_ENV
    monkeypatch.setenv(SHOT_QUALITY_ENV, "jpeg,q=60")
    page = _FakePage()
    sess = _session(tmp_path, page)
    path = sess.capture("save", success=False)
    assert path is not None and path.name.endswith(".jpg")
    assert "save_FAIL" in path.name
    assert page.shot_calls[0]["type"] == "jpeg"
    assert page.shot_calls[0]["quality"] == 60


def test_snapshot_screenshot_failure_returns_none_best_effort(tmp_path, monkeypatch):
    """取证尽力而为纪律不变：截图抛错降级返回 None，绝不改变用例判定。"""
    from atlas.session import SHOT_QUALITY_ENV
    monkeypatch.delenv(SHOT_QUALITY_ENV, raising=False)
    sess = _session(tmp_path, _FakePage(fail=True))
    assert sess.snapshot("save_FAIL") is None


# ---- 附件复制：原子纪律 + 批量并行 --------------------------------------------

def _png(tmp_path, name):
    folder = tmp_path / "artifacts" / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    data = b"\x89PNG\r\n\x1a\n fake png bytes " + name.encode()
    (folder / name).write_bytes(data)
    return data


def _runs_line(runs, step_extra):
    runs.write_text(json.dumps({
        "run_id": "r1", "case": "tc", "env": "sit", "status": "failed",
        "steps": [{"step": "save", "op": "ui", "ok": False,
                   "fail_class": "assertion", "dur_ms": 5, **step_extra}]}) + "\n",
        encoding="utf-8")


def test_batch_copy_preserves_entry_order(tmp_path):
    from atlas.allure_report import _write_attachments_batch
    data = _png(tmp_path, "x.png")
    out = tmp_path / "allure-results"
    out.mkdir()
    refs = [{"path": "artifacts/snapshots/x.png", "label": f"L{i}"} for i in range(4)]
    entries = _write_attachments_batch(out, refs, tmp_path, tmp_path / "runs.jsonl")
    assert [e["name"] for e in entries] == ["L0", "L1", "L2", "L3"]   # 条目序 == 输入序
    for entry in entries:
        assert (out / entry["source"]).read_bytes() == data


def test_batch_one_bad_ref_does_not_break_batch(tmp_path):
    """逐条占位降级独立：一条坏附件不顶掉整批（报告可以有噪音，不能丢整份）。"""
    from atlas.allure_report import _write_attachments_batch
    _png(tmp_path, "good1.png")
    _png(tmp_path, "good2.png")
    out = tmp_path / "allure-results"
    out.mkdir()
    refs = [{"path": "artifacts/snapshots/good1.png", "label": "G1"},
            {"path": "artifacts/snapshots/gone.png", "label": "GONE"},
            {"path": "artifacts/snapshots/good2.png", "label": "G2"}]
    entries = _write_attachments_batch(out, refs, tmp_path, tmp_path / "runs.jsonl")
    assert [e["name"] for e in entries] == ["G1", "GONE", "G2"]
    assert entries[1]["type"] == "text/plain"
    assert "gone.png" in (out / entries[1]["source"]).read_text(encoding="utf-8")
    assert entries[0]["type"] == "image/png"
    assert entries[2]["type"] == "image/png"


def test_batch_leaves_no_tmp_files(tmp_path):
    """原子纪律的可观测面：批量复制后目录无 .tmp 残档（tmp+replace 消费干净）。"""
    from atlas.allure_report import _write_attachments_batch
    for i in range(3):
        _png(tmp_path, f"s{i}.png")
    out = tmp_path / "allure-results"
    out.mkdir()
    refs = [{"path": f"artifacts/snapshots/s{i}.png"} for i in range(3)]
    _write_attachments_batch(out, refs, tmp_path, tmp_path / "runs.jsonl")
    assert not list(out.glob("*.tmp"))


def test_atomic_write_bytes_cleanup_on_failure(tmp_path, monkeypatch):
    """原子写失败路径：replace 抛错 → tmp 残档尽力清理、异常原样传播（真因优先）。

    哨兵打在 `atomic_io.os` 上，不是 `allure_report.os`：`_atomic_write_bytes` 是薄委托，
    真正的 `os.replace` 调用点在 `atomic_io.atomic_write_bytes` 内（pf-6 #92 收口后
    allure_report 只 import 了 `atomic_write_bytes`，模块命名空间里没有 `os` 绑定）。
    """
    from atlas import allure_report as ar
    from atlas import atomic_io
    path = tmp_path / "x.bin"

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(atomic_io.os, "replace", boom)
    with pytest.raises(OSError, match="disk on fire"):
        ar._atomic_write_bytes(path, b"data")
    assert not (tmp_path / "x.bin.tmp").exists()
    assert not path.exists()


def test_single_ref_batch_equals_write_attachment(tmp_path):
    """单 ref 直落（不启线程池）与 write_attachment 语义一致：类型 + 逐字节复制。"""
    from atlas.allure_report import _write_attachments_batch
    data = _png(tmp_path, "one.png")
    out = tmp_path / "allure-results"
    out.mkdir()
    entry = _write_attachments_batch(
        out, [{"path": "artifacts/snapshots/one.png", "label": "ONE"}],
        tmp_path, tmp_path / "runs.jsonl")[0]
    direct = write_attachment(out, tmp_path / "artifacts" / "snapshots" / "one.png", "ONE")
    assert entry["type"] == direct["type"] == "image/png"
    assert (out / entry["source"]).read_bytes() == data
    assert (out / direct["source"]).read_bytes() == data


def test_jpg_attachment_media_type_and_emit_chain(tmp_path):
    """降质截图（.jpg）经 engine 记录的 root 相对路径 → emit 复制 → 媒体类型 image/jpeg
    （后缀派生，不靠猜）。"""
    folder = tmp_path / "artifacts" / "snapshots"
    folder.mkdir(parents=True)
    jpeg_bytes = b"\xff\xd8\xff\xe0 jpeg-bytes"
    (folder / "shot.jpg").write_bytes(jpeg_bytes)
    runs = tmp_path / "runs.jsonl"
    _runs_line(runs, {"attachment": {"path": "artifacts/snapshots/shot.jpg",
                                     "label": "失败截图"}})
    out = tmp_path / "allure-results"
    emit_allure(runs, out, attachments_dir=tmp_path)
    result = json.loads(sorted(out.glob("atlas-*-result.json"))[0].read_text(encoding="utf-8"))
    att = result["steps"][0]["attachments"][0]
    assert att["type"] == "image/jpeg"
    assert (out / att["source"]).read_bytes() == jpeg_bytes


def test_step_attachment_array_order_lockstep(tmp_path):
    """step 级 attachment（单键）+ attachments（复数键）合并批次的条目序锁步：
    attachment 引用在前、attachments 引用在后（与旧逐字循环逐字一致）。"""
    _png(tmp_path, "a.png")
    _png(tmp_path, "b.png")
    runs = tmp_path / "runs.jsonl"
    _runs_line(runs, {
        "attachment": {"path": "artifacts/snapshots/a.png", "label": "A"},
        "attachments": [{"path": "artifacts/snapshots/b.png", "label": "B"}]})
    out = tmp_path / "allure-results"
    emit_allure(runs, out, attachments_dir=tmp_path)
    result = json.loads(sorted(out.glob("atlas-*-result.json"))[0].read_text(encoding="utf-8"))
    names = [a["name"] for a in result["steps"][0]["attachments"]]
    assert names == ["A", "B"]


def test_run_level_attachments_batch_order(tmp_path):
    """run 级 attachments 多 ref：批量复制 + 顺序锁步 + 无 tmp 残留。"""
    _png(tmp_path, "r1.png")
    _png(tmp_path, "r2.png")
    _png(tmp_path, "r3.png")
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "run_id": "r1", "case": "tc", "env": "sit", "status": "failed",
        "steps": [],
        "attachments": [{"path": "artifacts/snapshots/r1.png", "label": "R1"},
                        {"path": "artifacts/snapshots/r2.png", "label": "R2"},
                        {"path": "artifacts/snapshots/r3.png", "label": "R3"}]}) + "\n",
        encoding="utf-8")
    out = tmp_path / "allure-results"
    emit_allure(runs, out, attachments_dir=tmp_path)
    result = json.loads(sorted(out.glob("atlas-*-result.json"))[0].read_text(encoding="utf-8"))
    assert [a["name"] for a in result["attachments"]] == ["R1", "R2", "R3"]
    assert not list(out.glob("*.tmp"))

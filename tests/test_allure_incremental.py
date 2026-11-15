"""pf-9（wave4 C 轴）：allure-analyze 增量读。

判据面（离线合成，零网络零浏览器）：
- 指纹未变的 result 跳过解析（skipped_unchanged 计数，findings 不增）
- 新落盘 result 必读（负向对照：追加文件后 total 增加）
- mtime/size 变更必重读（负向对照：改文件内容后反映）
- 坏字节/坏 JSON 文件每次计数 skipped——增量不得绕过容错纪律（#131/#112）
- 指纹缓存坏 → WARN 一行 + 全量解析（点名不静默）
- 增量与全量结果等价（fail_class 分布逐键一致——findings 同源，红线 #7 判据不动）
- 指纹文件 atlas- 前缀（clean 随结果文件同生命周期）；删除的 result 从指纹自然消失
- incremental=False 逃生通道与默认增量同结果
"""
from __future__ import annotations
import json
import os

from atlas.allure_analysis import (_FINGERPRINT_FILENAME, analyze_allure,
                                   load_allure_results)


def _write_result(results_dir, name, *, status="failed", fail_class="assertion",
                  message="boom"):
    """写一条合成 *-result.json（自产往返形态：带 atlas.fail_class label）。"""
    results_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "name": name,
        "fullName": f"suite.{name}",
        "status": status,
        "statusDetails": {"message": message, "trace": ""},
        "labels": ([{"name": "atlas.fail_class", "value": fail_class}]
                   if fail_class else []),
        "steps": [{"name": "assert a", "status": "failed"}],
    }
    fpath = results_dir / f"atlas-{name}-result.json"
    fpath.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return fpath


def _touch_mtime(fpath, ns_shift):
    st = fpath.stat()
    os.utime(fpath, ns=(st.st_atime_ns + ns_shift, st.st_mtime_ns + ns_shift))


class TestIncrementalSkipUnchanged:
    def test_second_run_skips_unchanged(self, tmp_path):
        rdir = tmp_path / "allure-results"
        _write_result(rdir, "tc1")
        _write_result(rdir, "tc2", status="passed", fail_class="", message="")

        first = analyze_allure(rdir)
        assert first["total"] == 2
        assert first["skipped_unchanged"] == 0

        second = analyze_allure(rdir)
        assert second["total"] == 2
        assert second["skipped_unchanged"] == 2   # 指纹未变 → 全跳过

    def test_new_result_is_read(self, tmp_path):
        """负向对照：新落盘 result 必须被读到（不许被缓存跳过）。"""
        rdir = tmp_path / "allure-results"
        _write_result(rdir, "tc1")
        analyze_allure(rdir)

        _write_result(rdir, "tc2", status="passed", fail_class="", message="")
        after = analyze_allure(rdir)
        assert after["total"] == 2
        assert after["skipped_unchanged"] == 1   # 只有 tc1 跳过，tc2 必读

    def test_mtime_change_forces_reread(self, tmp_path):
        """负向对照：mtime 变更必重读——改内容后反映（failed → passed）。"""
        rdir = tmp_path / "allure-results"
        fpath = _write_result(rdir, "tc1", status="failed", fail_class="assertion")
        analyze_allure(rdir)

        fpath.write_text(json.dumps({
            "name": "tc1", "fullName": "suite.tc1", "status": "passed",
            "statusDetails": {"message": "", "trace": ""},
            "labels": [], "steps": [],
        }, ensure_ascii=False), encoding="utf-8")
        _touch_mtime(fpath, 10 ** 12)

        after = analyze_allure(rdir)
        assert after["total"] == 1
        assert after["passed"] == 1 and after["failed"] == 0

    def test_size_change_forces_reread(self, tmp_path):
        """负向对照：size 变更（mtime 粒度可能吃掉）也必重读。"""
        rdir = tmp_path / "allure-results"
        fpath = _write_result(rdir, "tc1", message="boom")
        analyze_allure(rdir)

        result = json.loads(fpath.read_text(encoding="utf-8"))
        result["statusDetails"]["message"] = "boom-with-more-text"
        fpath.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        _touch_mtime(fpath, 10 ** 12)   # mtime 保底（size 已变，双保险）

        after = analyze_allure(rdir)
        assert after["skipped_unchanged"] == 0

    def test_bad_file_not_cached_repeats_skipped(self, tmp_path):
        """容错纪律不绕过：坏文件不写指纹——每次计数 skipped（#131/#112）。"""
        rdir = tmp_path / "allure-results"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "atlas-bad-result.json").write_bytes(b"\xff\xfe\x00bad bytes")
        _write_result(rdir, "tc1")

        first = analyze_allure(rdir)
        assert first["skipped_malformed_files"] == 1
        assert first["total"] == 1

        second = analyze_allure(rdir)
        assert second["skipped_malformed_files"] == 1   # 坏文件每次都计数
        assert second["skipped_unchanged"] == 1

    def test_corrupt_fingerprint_falls_back_full(self, tmp_path, capsys):
        """指纹缓存坏 JSON → WARN 一行 + 全量解析（点名不静默）。"""
        rdir = tmp_path / "allure-results"
        _write_result(rdir, "tc1")
        analyze_allure(rdir)

        (rdir / _FINGERPRINT_FILENAME).write_text("{not json", encoding="utf-8")

        after = analyze_allure(rdir)
        assert after["skipped_unchanged"] == 0
        assert after["total"] == 1
        err = capsys.readouterr().err
        assert "警告" in err and _FINGERPRINT_FILENAME in err

    def test_incremental_matches_full_results(self, tmp_path):
        """等价性：增量与全量 findings 同源——fail_class 分布逐键一致（红线 #7）。"""
        rdir = tmp_path / "allure-results"
        _write_result(rdir, "tc1", status="failed", fail_class="assertion")
        _write_result(rdir, "tc2", status="failed", fail_class="data")
        _write_result(rdir, "tc3", status="passed", fail_class="", message="")

        inc = analyze_allure(rdir)
        full = analyze_allure(rdir, incremental=False)
        assert inc["total"] == full["total"]
        assert inc["passed"] == full["passed"]
        assert inc["failed"] == full["failed"]
        assert inc["skipped"] == full["skipped"]
        assert inc["fail_class_distribution"] == full["fail_class_distribution"]
        assert inc["buckets"] == full["buckets"]

    def test_fingerprint_file_has_atlas_prefix(self, tmp_path):
        """指纹文件 atlas- 前缀（emit_allure 的 clean 随结果文件同生命周期）。"""
        rdir = tmp_path / "allure-results"
        _write_result(rdir, "tc1")
        analyze_allure(rdir)
        fp = rdir / _FINGERPRINT_FILENAME
        assert fp.exists()
        assert fp.name.startswith("atlas-")

    def test_deleted_result_drops_from_fingerprint(self, tmp_path):
        """删除的 result 从指纹自然消失（touched 只含现存文件）。"""
        rdir = tmp_path / "allure-results"
        f1 = _write_result(rdir, "tc1")
        analyze_allure(rdir)
        f1.unlink()
        _write_result(rdir, "tc2", status="passed", fail_class="", message="")

        after = analyze_allure(rdir)
        assert after["total"] == 1
        fp = json.loads((rdir / _FINGERPRINT_FILENAME).read_text(encoding="utf-8"))
        assert "atlas-tc1-result.json" not in fp
        assert "atlas-tc2-result.json" in fp

    def test_load_allure_results_incremental_param(self, tmp_path):
        """load_allure_results 透传 incremental（外部消费方逃生通道）。"""
        rdir = tmp_path / "allure-results"
        _write_result(rdir, "tc1")
        analyze_allure(rdir)
        assert len(load_allure_results(rdir)) == 1
        assert len(load_allure_results(rdir, incremental=False)) == 1

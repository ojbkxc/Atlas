from atlas.reporting import export_runlog, summarize
from atlas.recorder import RunRecorder
from atlas.contracts import ContractRegistry


def test_runlog_export_uses_step_metrics(tmp_path):
    recorder = RunRecorder("case", "sit", tmp_path / "atlas.jsonl")
    with recorder.step("1") as step:
        step["api_ms"] = 12
    recorder.close()
    output = export_runlog(tmp_path / "atlas.jsonl", tmp_path / "runs.jsonl")
    assert '"api_ms": 12' in output.read_text(encoding="utf-8")


def test_runlog_export_handles_empty_steps(tmp_path):
    """空 steps 的 run 导出空文件。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({"run_id": "r1", "case": "t", "env": "s", "steps": []}) + "\n",
                    encoding="utf-8")
    output = tmp_path / "out.jsonl"
    export_runlog(runs, output)
    assert output.read_text(encoding="utf-8").strip() == ""


def test_runlog_export_survives_keyless_and_non_object_lines(tmp_path):
    """一条缺主键/非对象的坏行绝不能顶掉整份报告：好记录照常导出。
    input 由 CLI 用户传入，可能是外部/过滤后的 jsonl。"""
    import json
    runs = tmp_path / "runs.jsonl"
    good = {"run_id": "r1", "case": "A", "env": "s", "status": "passed",
            "steps": [{"step": "1", "op": "action", "ok": True, "dur_ms": 10}]}
    runs.write_text(
        json.dumps(good) + "\n" +
        json.dumps({"case": "C", "steps": [{"step": "x"}]}) + "\n" +   # 合法 JSON 但缺 run_id
        "123\n" +                                                       # 合法 JSON 但非对象
        "{TORN\n",                                                      # 残行
        encoding="utf-8")
    out = export_runlog(runs, tmp_path / "out.jsonl")
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").strip().splitlines()]
    assert [r["run_id"] for r in rows] == ["r1"]                       # 只有主键齐的好行保住
    assert rows[0]["ok"] is True and rows[0]["dur_ms"] == 10


def test_summarize_survives_non_object_lines(tmp_path):
    """聚合器对任意 JSONL 不整体崩：裸 null/数字与残行都计入残行数（与 export 容错一致）。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps({"run_id": "r1", "case": "A", "status": "passed", "steps": []}) + "\n" +
        "null\n123\n{坏残行\n",
        encoding="utf-8")
    s = summarize(runs)
    assert s["runs"] == 1 and s["status"] == {"passed": 1}
    assert s["skipped_malformed_lines"] == 3                           # null + 123 + 残行


def test_reporting_survives_malformed_steps_and_nonnumeric_durms(tmp_path):
    """steps:[null]/字符串步、非数值 dur_ms 不得顶掉整份聚合或报告——外部/手改 jsonl 可达，
    且违反本模块自注「一条坏行绝不能顶掉整份报告」。修复前 steps:[null] → .get AttributeError 崩。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({"run_id": "r1", "case": "A", "status": "passed",
        "steps": [None, "junk", {"step": "ok", "dur_ms": 5},
                  {"step": "weird", "dur_ms": "abc"}]}) + "\n", encoding="utf-8")
    s = summarize(runs)                                  # 不抛；非数值 dur_ms 归 0，sort 不崩
    assert s["runs"] == 1
    assert [x["step"] for x in s["slowest_steps"]] == ["ok", "weird"]   # None/字符串步跳过
    out = export_runlog(runs, tmp_path / "out.jsonl")    # 不抛
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").strip().splitlines()]
    assert [r["step"] for r in rows] == ["ok", "weird"]  # 坏步不写、好步保住


def test_reporting_survives_non_list_steps(tmp_path):
    """steps 是非列表（字符串）：当空处理，绝不按字符迭代后 .get 崩。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({"run_id": "r1", "case": "A", "status": "passed",
                                "steps": "corrupted-string"}) + "\n", encoding="utf-8")
    assert summarize(runs)["runs"] == 1                  # 不抛
    assert export_runlog(runs, tmp_path / "out.jsonl").read_text(encoding="utf-8").strip() == ""


def test_summarize_aggregates_multiple_runs(tmp_path):
    """summarate 聚合多次运行的状态与失败类。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps({"case": "t1", "status": "passed", "steps": [{"step": "go", "dur_ms": 10}]}) + "\n"
        + json.dumps({"case": "t2", "status": "failed",
                      "steps": [{"step": "save", "dur_ms": 20, "fail_class": "interaction"}]}) + "\n",
        encoding="utf-8")
    report = summarize(runs)
    assert report["runs"] == 2
    assert report["status"]["passed"] == 1
    assert report["status"]["failed"] == 1
    assert report["fail_classes"]["interaction"] == 1


def test_reporting_survives_truncated_utf8_tail_line(tmp_path):
    """中断把尾行腰斩在多字节字符中间（append 写中文 case 名中途 Ctrl-C，#112 同族形态）：
    旧实现 with path.open(encoding="utf-8") 迭代在字节崩点抛 UnicodeDecodeError——
    本模块 docstring 承诺「两个聚合器对任意 JSONL 都不得整体失败」永不兑现（库调用方直接崩，
    CLI summary 因 UnicodeDecodeError 是 ValueError 子类被 _user_error 兜成 rc2、不崩但把
    含有效行的文件整体拒掉且误归因成"用户输入错误"——中断写坏的盘不是用户输入错误）。
    #112「两处读侧必须同防线」已覆盖 experience/review/explore，reporting 是漏网的第 4 处。
    修复后：坏字节 replace 读入，残行 JSON 解析必败自然跳过，截断前的合法行照常聚合。"""
    import json
    runs = tmp_path / "runs.jsonl"
    good = {"run_id": "r1", "case": "TC1 中文用例", "status": "passed",
            "steps": [{"step": "go", "ok": True, "dur_ms": 10}]}
    runs.write_bytes(
        json.dumps(good, ensure_ascii=False).encode("utf-8") + b"\n"
        + b'{"run_id": "r2", "case": "\xe4\xbd'   # 截断的多字节：只写了「你」的前两字节
    )
    s = summarize(runs)                            # 不抛（修复前 UnicodeDecodeError）
    assert s["runs"] == 1                           # 截断前的 r1 照常统计
    assert s["status"] == {"passed": 1}
    assert s["cases"] == 1
    out = export_runlog(runs, tmp_path / "out.jsonl")   # 不抛
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").strip().splitlines()]
    assert [r["run_id"] for r in rows] == ["r1"]   # 合法行保住，腰斩行自然跳过


def test_reporting_midline_truncated_utf8_not_silent_valid_run(tmp_path):
    """坏字节落在字符串值中间、JSON 骨架仍完整：replace 读入后该行**解析成功**——
    这是诚实结果（数据确实被写坏了，U+FFFD 替换字符就是坏数据的形状），不得伪装成
    原字符、也不得因此整读崩。钉死「不静默丢证据也不静默修证据」边界。"""
    import json
    runs = tmp_path / "runs.jsonl"
    runs.write_bytes(
        b'{"run_id": "r1", "case": "A", "status": "passed", "steps": [{"step": "go"}]}\n'
        + b'{"run_id": "r2", "case": "X\xe4Y", "status": "failed", "steps": [{"step": "go"}]}\n'
    )
    s = summarize(runs)
    assert s["runs"] == 2                           # 骨架完整 → 解析成功如实统计
    assert s["status"] == {"passed": 1, "failed": 1}
    assert s["skipped_malformed_lines"] == 0        # 不谎报残行数
    out = export_runlog(runs, tmp_path / "out.jsonl")
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").strip().splitlines()]
    assert [r["run_id"] for r in rows] == ["r1", "r2"]


def test_toolbox_sensor_takes_one_snapshot_and_imports_weak_contracts():
    class Bridge:
        def __init__(self): self.calls = 0
        def call(self, method, params=None):
            self.calls += 1
            return {"items": [{"method": "POST", "path": "/orders", "status": 200}]}
    from atlas.toolbox import WebToolboxSensor
    bridge = Bridge()
    registry = ContractRegistry()
    assert WebToolboxSensor(bridge, registry).learn_contracts() == 1
    assert bridge.calls == 1
    assert ("POST", "/orders") in registry.contracts


def test_learn_contracts_real_entries_shape_not_silent_zero():
    """真实 web-toolbox 的 net_list 权威形态是 {"total", ..., "entries": [...]}（扩展 v1.1.4
    实测 231 条抓包取证），不是离线 fake 的 {"items": ...}。旧实现只认 items：真实形态下
    拿到 dict 本身、isinstance(list) 恒 False → 静默登记 0 条，感知器官真实接线名存实亡
    ——又一例「离线全绿 ≠ 能力可用」。entries 优先、items 兼容旧 fake、裸列表兼容。
    同时修计数虚报：真实抓包同路径轮询重复出现，add 按 (method,path) 去重，逐条 count
    与 registry 实际大小漂移。fixture 现取完整页（total==页大小）——#120 起截断页
    （total>len(entries)）被点名拒绝，残缺登记见 test_learn_contracts_truncated_page_rejected。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    captured = {"total": 5, "returned": 5, "entries": [
        {"id": "a", "method": "GET", "path": "/assets/x.js", "status": 200},
        {"id": "b", "method": "POST", "path": "/web/mdm/findCust", "status": 200},
        {"id": "c", "method": "POST", "path": "/web/mdm/findCust", "status": 200},  # 轮询重复
        {"id": "d", "method": "GET", "path": "/web/gone", "status": 500},           # 非200
        {"id": "e", "method": "GET", "path": "", "status": 200},                    # 空 path
    ]}
    class RealBridge:
        def call(self, method, params=None): return captured
    registry = ContractRegistry()
    n = WebToolboxSensor(RealBridge(), registry).learn_contracts()
    assert n == 2                                    # 去重净增，非条目数 3
    assert ("GET", "/assets/x.js") in registry.contracts
    assert ("POST", "/web/mdm/findCust") in registry.contracts
    assert ("GET", "/web/gone") not in registry.contracts
    # 再登记一次净增 0（覆盖不重复计），计数与 registry 真大小同轨
    assert WebToolboxSensor(RealBridge(), registry).learn_contracts() == 0
    # #140 翻转本测旧断言：错误 dict 旧「不崩不登记」返回 0——把感知器官坏了折叠成
    # 「登记完成」的静默假绿（#102 同族，且与 console_errors 同形态点名拒绝漂移）。
    # 现与 console_errors 共用 _unwrap_records 单一真相，错误形态必须点名 RuntimeError。
    class ErrBridge:
        def call(self, method, params=None): return {"status": "error", "message": "未知方法"}
    with pytest.raises(RuntimeError, match="net_list 返回 dict 但无 entries/items"):
        WebToolboxSensor(ErrBridge(), ContractRegistry()).learn_contracts()


def test_save_har_error_text_refuses_bad_file(tmp_path):
    """#104 真实取证（web-toolbox v1.1.4）：export_har 过滤无匹配返回**纯文本**
    「错误: 过滤后没有条目」。旧实现无条件 json.dumps → 写成 `""错误: 过滤后没有条目""`
    的字符串字面量——文件「保存成功」实为坏 HAR，真因埋进引号。修复后点名原文拒写坏档。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    class RealErrorBridge:
        def call(self, method, params=None): return "错误: 过滤后没有条目"
    out = tmp_path / "out.har"
    with pytest.raises(ValueError, match="过滤可能没命中"):
        WebToolboxSensor(RealErrorBridge()).save_har(out, pathIncludes="/nope")
    assert not out.exists()                       # 坏档不落盘——假 .har 比报错更难排查


def test_save_har_json_text_not_double_encoded(tmp_path):
    """#104：工具文档口径「返回 JSON 文本」（str）。旧实现 json.dumps(str) 双重编码，
    顶层成带转义的字符串而非 {log:...}，HAR 工具打开才炸。修复后先解析再落盘，文件可直接解析。"""
    import json
    from atlas.toolbox import WebToolboxSensor
    payload = {"log": {"version": "1.2", "entries": []}}
    class TextBridge:
        def call(self, method, params=None): return json.dumps(payload)
    out = WebToolboxSensor(TextBridge()).save_har(tmp_path / "a.har")
    assert json.loads(out.read_text(encoding="utf-8")) == payload   # 顶层是 dict 不是字符串


def test_save_har_dict_passthrough_and_bad_type(tmp_path):
    """dict 正常落盘（离线 fake 形态兼容）；None/异常类型点名拒绝，不写 null 假档。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    payload = {"log": {"version": "1.2", "entries": [{"x": 1}]}}
    class DictBridge:
        def call(self, method, params=None): return payload
    out = WebToolboxSensor(DictBridge()).save_har(tmp_path / "ok.har")
    assert out.read_text(encoding="utf-8").count('"entries"') == 1
    class NoneBridge:
        def call(self, method, params=None): return None
    bad = tmp_path / "bad.har"
    with pytest.raises(ValueError, match="NoneType"):
        WebToolboxSensor(NoneBridge()).save_har(bad)
    assert not bad.exists()


def test_console_errors_bridge_error_not_silent_empty():
    """#105 真实 bridge 取证（v1.1.4）：console_read 在部分 build 返回纯文本「错误: 未知方法」，
    也有错误 dict / None 形态。旧实现 `isinstance(items, list) else []` 把这些**全部折叠成 []**——
    排障时调查者读成「前端无错误」，实为感知器官坏了的假阴性（#102 静默 0 条、#104 假 .har 同族）。
    修复后错误形态点名拒绝，绝不伪装成合法空结果。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    class TextErr:
        def call(self, method, params=None): return "错误: 未知方法: console.read"
    with pytest.raises(RuntimeError, match="未知方法"):
        WebToolboxSensor(TextErr()).console_errors()
    class NoneErr:
        def call(self, method, params=None): return None
    with pytest.raises(RuntimeError, match="NoneType"):
        WebToolboxSensor(NoneErr()).console_errors()
    class ErrDict:   # 错误 dict 但无 entries/items 键
        def call(self, method, params=None): return {"status": "error", "message": "boom"}
    with pytest.raises(RuntimeError, match="无 entries/items"):
        WebToolboxSensor(ErrDict()).console_errors()


def test_console_errors_legitimate_empty_still_returns_list():
    """#105 合法空表是业务事实（本轮无报错）：{"items": []} 与裸 [] 仍如实返回 []，
    修复只针对错误形态，不把真空结果也误报成异常（与 wait_for_rows 的 min_rows=0 同纪律）。"""
    from atlas.toolbox import WebToolboxSensor
    class EmptyItems:
        def call(self, method, params=None): return {"items": []}
    assert WebToolboxSensor(EmptyItems()).console_errors() == []
    class BareList:
        def call(self, method, params=None): return []
    assert WebToolboxSensor(BareList()).console_errors() == []
    class RealErrors:
        def call(self, method, params=None): return {"items": [{"level": "error", "text": "TypeError"}]}
    assert WebToolboxSensor(RealErrors()).console_errors() == [{"level": "error", "text": "TypeError"}]


def test_console_errors_real_bridge_entries_shape(tmp_path):
    """#120 面1：真实桥接 console.read 权威形态是 {total, filtered, entries}
    （bridge-client.js v1.1.4，与 net_list 同族键名），不是离线 fake 的 items——
    旧实现只认 items，真实抓到的错误被点名「无 items 列表」当场拒绝：把正常路径翻成
    错误（#115 教训反向面；#102 键名漂移同族）。现 entries 优先、items 兼容旧 fake。"""
    from atlas.toolbox import WebToolboxSensor
    class RealBridge:
        def call(self, method, params=None):
            return {"total": 5, "filtered": 1, "entries": [{"level": "error", "text": "TypeError: x"}]}
    assert WebToolboxSensor(RealBridge()).console_errors() == [{"level": "error", "text": "TypeError: x"}]
    # 真实空缓冲（level 过滤后无错误）是业务事实，如实 []
    class RealEmpty:
        def call(self, method, params=None): return {"total": 5, "filtered": 0, "entries": []}
    assert WebToolboxSensor(RealEmpty()).console_errors() == []
    # entries 非 list 的坏形态仍点名拒绝（#105 防线不因新键名放行）
    import pytest
    class BadEntries:
        def call(self, method, params=None): return {"entries": "notalist", "total": 3}
    with pytest.raises(RuntimeError, match="entries/items"):
        WebToolboxSensor(BadEntries()).console_errors()


def test_learn_contracts_truncated_page_rejected():
    """#120 面B：真实 net_list 分页默认 limit=50、total 报全量（v1.1.4 实测 231 条只回前
    50）。旧实现拿一页登记一页零告警——部分结果伪装全量完成（#102 静默 0 条同族假绿），
    用户以为「契约都学了」，远处接口缺契约静默不校验。现 total>页大小点名终止，
    不带 total 的旧形态零漂移。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    page = [{"id": f"r{i}", "method": "GET", "path": f"/api/x{i}", "status": 200} for i in range(50)]
    class TruncatedBridge:
        def call(self, method, params=None): return {"total": 231, "returned": 50, "entries": page}
    with pytest.raises(RuntimeError, match="分页截断"):
        WebToolboxSensor(TruncatedBridge(), ContractRegistry()).learn_contracts()
    # 完整页（total==len(entries)）正常登记
    class FullBridge:
        def call(self, method, params=None): return {"total": 2, "entries": page[:2]}
    assert WebToolboxSensor(FullBridge(), ContractRegistry()).learn_contracts() == 2
    # 旧 fake items 形态无 total 键——不触发闸门，零漂移
    class LegacyFake:
        def call(self, method, params=None): return {"items": page[:2]}
    assert WebToolboxSensor(LegacyFake(), ContractRegistry()).learn_contracts() == 2


def test_learn_contracts_error_shapes_named_not_silent_zero(tmp_path):
    """#140：learn_contracts 把 bridge 错误形态折叠成静默 0——错误 dict 旧断言甚至把
    bug 钉成规格（见翻转后的 real_entries 测），纯文本错误（console_read v1.1.4 同
    build 实证形态）与 None 同样无声。「登记 0 条」在调查者眼里等于「没有可学的接口」，
    实为感知器官坏了（#102 静默假绿），且与 console_errors 同形态点名拒绝漂移（#120
    只收敛了成功键名，错误防线各修各的=改一漏一 #92）。现两函数共用 _unwrap_records
    单一真相。合法空抓包 {"entries": []} 必须仍如实 0——「本轮无数据」是业务事实
    不是异常（#115 不得把正常路径翻成错误）。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    class Bridge:
        def __init__(self, payload):
            self.payload = payload
        def call(self, method, params=None):
            return self.payload
    with pytest.raises(RuntimeError, match="net_list 返回了非预期形态 str"):
        WebToolboxSensor(Bridge("错误: 未知方法: net.list"), ContractRegistry()).learn_contracts()
    with pytest.raises(RuntimeError, match="NoneType"):
        WebToolboxSensor(Bridge(None), ContractRegistry()).learn_contracts()
    # 合法空表：如实 0，不点名
    assert WebToolboxSensor(Bridge({"total": 0, "entries": []}), ContractRegistry()).learn_contracts() == 0


def test_learn_contracts_missing_key_not_misattributed_as_truncation():
    """#140 附带面：{"total": 231} 无 entries 键（bridge 应答形态漂移/坏 build），旧实现
    先过分页闸门报「只取回 0 条」——把键缺失误归因成分页截断（#90 归因错位），用户
    照着提示加 limit 重取，还是拿不到数据。现解包先于闸门，点名真因「无 entries/items」。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    class Bridge:
        def call(self, method, params=None): return {"total": 231}
    with pytest.raises(RuntimeError, match="无 entries/items") as exc:
        WebToolboxSensor(Bridge(), ContractRegistry()).learn_contracts()
    assert "分页截断" not in str(exc.value)


def test_save_har_error_dict_refuses_bad_file(tmp_path):
    """#140 第二面：#104 堵了 export_har 的 str 错误文本，**错误 dict 后门**没堵——
    {"status":"error","message":"过滤后没有条目"} 类型检查全过、照常落盘，产出
    「保存成功」的坏 .har：真因埋进 JSON 字段，下游 DevTools/Postman 打开才炸
    （#102 坏形态伪装合法结果）。现 dict 形态按 HAR 1.2 规范校验顶层 log 键，
    缺 log 点名拒写且不创建文件。合法 {log:…} 档照常写（零涟漪）。"""
    import pytest
    from atlas.toolbox import WebToolboxSensor
    class Bridge:
        def __init__(self, payload):
            self.payload = payload
        def call(self, method, params=None):
            return self.payload
    bad = tmp_path / "bad.har"
    with pytest.raises(ValueError, match="缺 log 结构"):
        WebToolboxSensor(Bridge({"status": "error", "message": "过滤后没有条目"})).save_har(bad)
    assert not bad.exists()
    good = tmp_path / "good.har"
    WebToolboxSensor(Bridge({"log": {"version": "1.2", "entries": []}})).save_har(good)
    assert good.is_file() and '"version": "1.2"' in good.read_text(encoding="utf-8")

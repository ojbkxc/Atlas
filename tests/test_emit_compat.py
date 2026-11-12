"""generator/emit_compat（gap11 #18）：--compat legacy-suite 产物判据面。

离线、零网络、零浏览器执行——盘上文本 + AST/正则扫描 + sha256 字节比对 +
假旧仓子进程 collect-only（只收集不发请求不起浏览器，F-1/R5 边界内；
这条**是**任务判据 D.1.5 原文点名要的「纯净 sys.path 不含 atlas src」实证）。

「删掉即失去哪道防线」点名纪律（快照测惯例）：
- 扫描面测被删 → --compat 产物带着 atlas import 进旧仓也没人拦（旧仓机器没装
  atlas，运行期 ModuleNotFoundError=第一红线 C.3 破防，#21 跨切判据失去锚点）；
- vendoring 等值/篡改测被删 → emit 层手抄第二份垫片逻辑、源改了不重新生成都静默
  通过（#92/#15 漂移面 + G11-4 字节锁步失守，CRLF/BOM 这类文本读法看不见的改写
  会被「两边一起归一」掩盖成恒绿）；
- fixture 对拍测被删 → 产物同名 fixture 的 scope/autouse 面漂移（browser 掉出
  session=sole_page 重建义务漂移，旧仓形态被悄悄改变）；
- 组合闸测被删 → 生成器入口绕过 CLI 出半成品（lead P0/xcheck C9① 缝隙重开）。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.generator.emit_compat import (
    COMPAT_ALLOWED_ROOTS, CONTRACT_FIXTURES, LEGACY_BUSINESS_SELECTOR_MARKS,
    SHIM_SOURCE_FILES, CompatEmitError, _compat_runtime_lines, _legacy_web_dir,
    atlas_import_hits, atlas_import_text_hits, compat_combo_problem,
    compat_persist_keys, compat_specs_from_flow, emit_compat_package,
    is_atlas_free, render_compat_conftest, render_compat_pytest_ini,
    render_compat_readme, vendor_body, vendor_is_current, vendor_provenance,
    vendor_shim_source,
)
from atlas.generator.emit_pytest import PytestEmitError, emit_pytest_package
from atlas.generator.model import Endpoint, ResponseSpec

REPO_ROOT = Path(__file__).resolve().parents[1]


def _assert_compiles(source: str):
    ast.parse(source)
    compile(source, "<gen>", "exec")


def _read_endpoint() -> Endpoint:
    return Endpoint(
        method="GET", path="/api/order/list", summary="查询订单列表",
        response=ResponseSpec(is_object=True, has_result_code=True,
                              sample={"resultCode": "BUG-000"}))


# ---- AST 权威扫描：正/负/对抗三面 ----

_AST_POSITIVES = [
    ("import atlas\n", "import atlas"),
    ("from atlas import x\n", "from atlas import"),
    ("from atlas.config import AtlasConfig\n", "绝对根 atlas"),
    ("import atlas.runtime as rt\n", "别名形态也命中"),
    ("def f():\n    import atlas.legacy_fast\n", "函数体缩进也命中"),
]


@pytest.mark.parametrize("src", [p[0] for p in _AST_POSITIVES], ids=[p[1] for p in _AST_POSITIVES])
def test_atlas_import_ast_scan_hits(src):
    assert atlas_import_hits(src)


@pytest.mark.parametrize("src", [
    "from .model import Endpoint\n",               # 相对 import 与 atlas 包无关
    "import atlas_helper\n",                        # 前缀相似不误伤（模块根是 atlas_helper）
    "from atlasize import x\n",
    "x = 1  # 以后可以 import atlas 试试（注释叙述不算语句）\n",
    '"""纪律陈述：旧仓运行时绝不 import atlas 包"""\n',  # docstring 叙述行——垫片源就有
], ids=["relative", "prefix-atlas_helper", "prefix-atlasize", "comment", "docstring"])
def test_atlas_import_ast_scan_no_false_positive(src):
    assert atlas_import_hits(src) == []


def test_text_sentinel_scan_statement_vs_narrative():
    """文本哨兵与 AST 分工：语句行必命中；docstring/注释叙述必不命中（vendoring 逐字节
    等于源、源 docstring 就有这句纪律——把叙述判违规=逼产物偏离源=两闸打架漏防）。"""
    assert atlas_import_text_hits("from atlas.config import AtlasConfig\n")
    assert atlas_import_text_hits("    import atlas.legacy_fast\n")
    assert atlas_import_text_hits("- **旧仓运行时绝不 import atlas 包**——本文件…\n") == []
    assert atlas_import_text_hits('"""绝不 import atlas 包"""\n') == []


def test_positive_control_real_repo_file_must_hit():
    """扫描器非恒真的正向对照：仓内真实 atlas 侧文件（顶层 from atlas... import）必命中
    两种扫描——否则「产物 0 命中」可能只是扫描器坏了。"""
    src = (REPO_ROOT / "examples" / "api_create_then_ui_verify.py").read_text(encoding="utf-8")
    assert atlas_import_hits(src, "examples/api_create_then_ui_verify.py")
    assert atlas_import_text_hits(src)


def test_business_selector_positive_control_legacy_repo():
    """选择器哨兵非恒真：旧仓 conftest 实文必命中（不可见时如实 skip，不装验证过）。"""
    legacy_conftest = Path(os.environ.get("ATLAS_LEGACY_WEB", r"D:\GitHub\UI AUTO TEST\iservice")) \
        / "conftest.py"
    if not legacy_conftest.is_file():
        pytest.skip("旧仓 conftest 本机不可见——哨兵正向对照不假绿")
    src = legacy_conftest.read_text(encoding="utf-8", errors="replace")
    assert any(mark in src for mark in LEGACY_BUSINESS_SELECTOR_MARKS), \
        "旧仓实文不含哨兵=名单过期，请更新 emit_compat 名单并回 lead"


def test_syntax_error_propagates_not_false_clean():
    """坏语法源不在扫描器处静默放行：SyntaxError 原样抛（扫描器不得先于被扫描对象死
    并给出假干净结论，#89 同族）。"""
    with pytest.raises(SyntaxError):
        atlas_import_hits("def broken(:\n")


def test_bytes_input_scans_strictly_not_silently_empty():
    """bytes 输入正面判据（support-impl 22:3x 点名崩溃的回归面）：合法 UTF-8 bytes 照常
    扫出命中；坏字节当场 CompatEmitError 点名——哨兵绝不在坏输入上静默返回 0 命中。"""
    hits = atlas_import_text_hits(b"from atlas.config import AtlasConfig\n")
    assert len(hits) == 1
    assert atlas_import_text_hits(b"# only stdlib here\n") == []
    with pytest.raises(CompatEmitError):
        atlas_import_text_hits(b"\xff\xfe\x00bad bytes\x00")
    with pytest.raises(CompatEmitError):
        atlas_import_hits(b"\xff\xfe\x00bad bytes\x00", "corrupt.py")


# ---- 垫片源本体判据（vendoring 前提；红=支持面变更，先点名 support-impl #17）----


@pytest.mark.parametrize("rel", list(SHIM_SOURCE_FILES), ids=["legacy_fast", "fastplugin"])
def test_shim_sources_are_atlas_free_and_present(rel):
    """两垫片必须存在且自身 atlas-free——vendoring 的前提；这条红时**别改 emit_compat**，
    归因在 support-impl（垫片开始 import atlas=旧仓运行期必炸，第一红线源头破防）。"""
    path = REPO_ROOT / rel
    assert path.is_file(), f"垫片源缺失：{rel}（#17 未落地）"
    src = path.read_text(encoding="utf-8")
    assert is_atlas_free(src, rel) == []
    ast.parse(src)  # 逐字节 vendor 前钉可编译，坏源不进产物通道


# ---- vendoring：字节级等值 + provenance + 篡改 + 缺失（G11-4 + lead 字节级裁定）----


def test_vendor_provenance_and_byte_equality(tmp_path):
    source = b"# shim v1\nX = 1\n"
    (tmp_path / "src" / "atlas").mkdir(parents=True)
    (tmp_path / "src" / "atlas" / "legacy_fast.py").write_bytes(source)
    vendored = vendor_shim_source(tmp_path, "src/atlas/legacy_fast.py")
    rel, sha = vendor_provenance(vendored)
    assert rel == "src/atlas/legacy_fast.py"
    assert sha == hashlib.sha256(source).hexdigest()  # sha 对**字节**算（非文本编码往返）
    assert vendor_body(vendored) == source            # 逐字节=源（除头）
    assert vendor_is_current(vendored, source)


def test_vendor_crlf_survives_byte_for_byte(tmp_path):
    """CRLF 对抗样本（lead 点名要的）：文本读法会把 CRLF 归一成 LF、两边一起归一=锁步
    恒绿的假绿面；字节级判据必须保留 CRLF，且源被改成 LF 未重新 vendor → 必须 False。"""
    source = b"# shim\r\nX = 1\r\nY = 2\r\n"
    d = tmp_path / "src" / "atlas"
    d.mkdir(parents=True)
    (d / "legacy_fast.py").write_bytes(source)
    vendored = vendor_shim_source(tmp_path, "src/atlas/legacy_fast.py")
    assert b"\r\n" in vendor_body(vendored)           # 换行未被悄悄改写
    assert vendor_body(vendored) == source
    assert not vendor_is_current(vendored, source.replace(b"\r\n", b"\n"))


def test_vendor_bom_is_part_of_bytes(tmp_path):
    """BOM 进字节判据：utf-8 BOM 三字节属于源字节，sha/正文都得带上（文本读法丢真）。"""
    d = tmp_path / "src" / "atlas"
    d.mkdir(parents=True)
    source = "﻿# 带 BOM 的垫片\nX = 1\n".encode("utf-8")
    (d / "fastplugin.py").write_bytes(source)
    vendored = vendor_shim_source(tmp_path, "src/atlas/fastplugin.py")
    assert vendor_body(vendored) == source
    assert hashlib.sha256(vendor_body(vendored)).hexdigest() == vendor_provenance(vendored)[1]


def test_vendor_text_interface_rejected(tmp_path):
    """字节级接口拒收 str：防「文本归一 vs 字节锁步」两套口径混用互相掩盖。"""
    d = tmp_path / "src" / "atlas"
    d.mkdir(parents=True)
    (d / "legacy_fast.py").write_bytes(b"# v\n")
    vendored = vendor_shim_source(tmp_path, "src/atlas/legacy_fast.py")
    with pytest.raises(CompatEmitError):
        vendor_body(vendored.decode("utf-8"))
    with pytest.raises(CompatEmitError):
        vendor_is_current(vendored, "# v\n")


def test_vendor_detects_stale_source(tmp_path):
    """源改了、产物没重新生成 → False（sha 面与字节面双翻）。"""
    src_file = tmp_path / "src" / "atlas" / "fastplugin.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_bytes(b"# v1\n")
    vendored = vendor_shim_source(tmp_path, "src/atlas/fastplugin.py")
    assert vendor_is_current(vendored, src_file.read_bytes())
    src_file.write_bytes(b"# v2 changed\n")
    assert not vendor_is_current(vendored, src_file.read_bytes())


def test_vendor_missing_source_raises(tmp_path):
    with pytest.raises(CompatEmitError) as ei:
        vendor_shim_source(tmp_path, "src/atlas/legacy_fast.py")
    assert "垫片源文件缺失" in str(ei.value)  # 点名文件，不产半成品（#9）


def test_vendor_without_provenance_header_rejected():
    """手抄内容混进产物通道的防线：无头文本直接点名拒绝，不静默当全文。"""
    with pytest.raises(CompatEmitError) as ei:
        vendor_body("# 手抄的第二份垫片逻辑（无 provenance 头）\n".encode("utf-8"))
    assert "provenance" in str(ei.value)
    with pytest.raises(CompatEmitError):
        vendor_provenance("# 手抄内容\n".encode("utf-8"))


def test_vendored_real_shims_stay_atlas_free():
    """端到端：进产物的是 vendored 字节（头+体），双扫描同样 0 命中——头里的
    source=src/atlas/... 路径串不得被文本哨兵误判成 import 语句。"""
    for rel in SHIM_SOURCE_FILES:
        vendored = vendor_shim_source(REPO_ROOT, rel)
        assert is_atlas_free(vendored.decode("utf-8"), rel) == []
        assert vendor_is_current(vendored, (REPO_ROOT / rel).read_bytes())


# ---- 组合闸（xcheck C9①：生成器入口不得绕过 CLI 校验）----


def test_compat_combo_gate_single_point():
    """判据唯一函数位 compat_combo_problem：CLI 语义逐字复现（配对文案+未知值点名）。"""
    assert compat_combo_problem("none", False) == ""       # 存量形态零涟漪
    assert compat_combo_problem("legacy-suite", True) == ""
    assert "--fast" in compat_combo_problem("legacy-suite", False)
    unknown = compat_combo_problem("jmeter", True)
    assert "未知 --compat" in unknown and "legacy-suite" in unknown


def _handwritten_compat_combo(text: str) -> bool:
    """入口体内**手抄 compat×fast 组合判断**的检出（收紧到原意，lead 2026-11-11 裁决）。

    旧判据是裸子串 `assert "not fast" not in body`——它**误伤**任何含 `not fast` 的无关
    判据（真实案例：T3 的 `parallel > 1 and not fast`，语义与 compat 无关却触发假阳性）。
    收紧为只认真正要防的形态：**同一行**里字面 compat 值与 fast **同现**（组合判断），
    而非「compat 出现」本身——合法的 `if compat == "legacy-suite":`（纯路由，无 fast）
    不误伤。保护强度不降：真手抄 `compat == "legacy-suite" and not fast` 仍被抓到（见正向对照）。
    """
    compat_literal = re.compile(
        r"""compat\s*==\s*["'](?:legacy-suite|none)["']|["'](?:legacy-suite|none)["']\s*==\s*compat""")
    for ln in text.splitlines():
        if compat_literal.search(ln) and re.search(r"\bfast\b", ln):
            return True
        # 组合形态兜底：compat 与 fast 取反同现（手抄组合判断的另一种写法）
        if "compat" in ln and "not fast" in ln:
            return True
    return False


def test_compat_combo_problem_source_single_point():
    """「判据只一份」的源码面：cli.py 的 _compat_gate 若自带第二份组合字面，此测点名——
    现读两边源码，确认 generator 面只 import 共享函数，不复制 if 内容。"""
    src = (REPO_ROOT / "src" / "atlas" / "generator" / "emit_pytest.py").read_text(encoding="utf-8")
    assert "compat_combo_problem" in src            # 生成器面=调用共享判据
    body = src.split("def emit_pytest_package", 1)[1].split("\ndef ", 1)[0]
    # 生成器入口体内不得出现手抄的组合字面判断（`compat == "legacy-suite" and not fast` 类）
    assert not _handwritten_compat_combo(body.replace("compat_combo_problem", ""))
    # 正向对照（保护强度不降）：真手抄组合形态仍被同一判据抓到
    assert _handwritten_compat_combo('if compat == "legacy-suite" and not fast:\n    pass\n')
    assert _handwritten_compat_combo('if "legacy-suite" == compat and fast:\n    pass\n')
    assert _handwritten_compat_combo("if compat and not fast:\n    pass\n")
    # 负向对照：无关的 `and not fast`（如 T3 parallel 判据）与合法纯路由不误伤
    assert not _handwritten_compat_combo('if parallel > 1 and not fast:\n    raise X\n')
    assert not _handwritten_compat_combo('if compat == "legacy-suite":\n    return y\n')


def test_compat_routing_rejects_before_mkdir(tmp_path):
    """P0 缝隙回归：emit_pytest_package 直调（不经 CLI）组合非法 → PytestEmitError +
    out_dir 不得被创建。正向对照：合法组合不拦（放行到 emit_compat 自己的闸）。"""
    out = tmp_path / "pkg"
    with pytest.raises(PytestEmitError) as ei:
        emit_pytest_package(out_dir=out, flow=_COMPAT_FLOW, fast=False,
                            compat="legacy-suite")
    assert "--fast" in str(ei.value)
    assert not out.exists()
    out2 = tmp_path / "pkg2"
    with pytest.raises(PytestEmitError):
        emit_pytest_package(out_dir=out2, flow=_COMPAT_FLOW, fast=True, compat="jmeter")
    assert not out2.exists()


def test_compat_none_zero_ripple_tree(tmp_path):
    """compat="none"（含不传）与现状逐字节相等——12 项哈希守的是渲染函数面，本测守的是
    **入口新增 kwarg 的路由面**（守场点名：删掉它=「compat 参默认值渗旧形态」失守）。"""
    a, b = tmp_path / "a", tmp_path / "b"
    w_a = emit_pytest_package(out_dir=a, endpoints=[_read_endpoint()], flow_id="z",
                              components={"components": {}})
    w_b = emit_pytest_package(out_dir=b, endpoints=[_read_endpoint()], flow_id="z",
                              components={"components": {}}, compat="none")

    def tree(p):
        return {str(x.relative_to(p)): x.read_bytes()
                for x in sorted(p.rglob("*")) if x.is_file()}
    assert tree(a) == tree(b)
    assert w_a and w_b


# ---- 产物渲染面（C.1-C.5）----

_COMPAT_FLOW = {"id": "iservice-prod-am-compat", "steps": [
    {"task": "legacy_case",
     "module": "prod_am_regression/01_spare_part_list_query/spare_part_list_query.yaml",
     "case_id": "TC1", "case_intent": "TC1 备件列表查询", "write": False,
     "skip_postcondition": True},
    {"task": "legacy_case",
     "module": "prod_am_regression/02_product_list_query/product_list_query.yaml",
     "case_id": "TC2", "case_intent": "TC2 产品列表查询", "write": False,
     "skip_postcondition": False, "requires": ["_tc1_ok"]},
    {"task": "cancel_old", "intent": "委托旧撤单函数", "read_only": True,
     "legacy_pages": "demo_cancel_fn"},
]}


def _fake_legacy_web(tmp_path, fn_names=("demo_cancel_fn",)):
    """假旧仓夹具（D.1.5 原文「不碰真旧仓」）：只为符号核验与 collect 提供 lib/pages.py。"""
    web = tmp_path / "fake_web"
    (web / "lib").mkdir(parents=True, exist_ok=True)
    body = "".join(f"def {n}(page, ctx):\n    return True\n" for n in fn_names)
    (web / "lib" / "pages.py").write_text("# 假旧仓 pages（测试夹具）\n" + body,
                                          encoding="utf-8")
    return web


def test_compat_specs_classify_and_persist_keys():
    steps = compat_specs_from_flow(_COMPAT_FLOW)
    assert len(steps) == 3
    keys = compat_persist_keys(steps)
    assert {"_logged_in_account", "_tc1_ok", "_tc2_ok"} <= keys
    # 委托腿不产 _tcN_ok（不写成功标记）——白名单里出现它=凭空多出没人写的键（假记录面）
    assert "_tc3_ok" not in keys
    # requires 引用到的键必须在白名单（否则前置批次的值落不了盘，跨批断链静默失效）
    assert all(k in keys for k in steps[1].requires)


def test_compat_test_module_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    mod = next((out / "testcases").glob("*/test_*.py")).read_text(encoding="utf-8")
    assert "class TestIserviceProdAmCompatPlan:" in mod
    assert "@pytest.mark.order(1)" in mod and "@pytest.mark.order(3)" in mod
    # C5c 形态：定位/委托/缺前置是模块级普通函数调用，不是 fixture 注入
    assert "runner = _legacy_import(\"lib.runner\")" in mod
    assert ("runner.run_case_py(case_file, 'TC1', page, plan_ctx, "
            "skip_postcondition=True)") in mod
    assert "case = runner.load_yaml_case(case_file, 'TC1')" in mod
    assert "_legacy_testcases_root() /" in mod
    assert "skip_postcondition=False" in mod
    assert "plan_ctx['_tc2_ok'] = True" in mod
    assert "_require_ctx(plan_ctx, '_tc1_ok'" in mod
    assert "_call_pages_fn('demo_cancel_fn', page, plan_ctx)" in mod
    assert "def test_tc1_" in mod and "(self, get_page, plan_ctx):" in mod
    # C5c：用例模块零 fixture（旧仓 7 件套只在 conftest——模块里多一个把手=多一处同步分叉）
    assert "@pytest.fixture" not in mod
    assert "pytest.skip(" not in mod                       # F5 禁裸 skip
    assert "[写操作-需人工确认]" not in mod                  # 本 fixture 全只读
    assert "def _title(text):" in mod                      # allure 缺席降级位在
    assert "@allure.title" not in mod                      # 不硬依赖 allure import


def test_compat_artifacts_atlas_free_and_selector_free(tmp_path, monkeypatch):
    """第一红线全家扫描：产物每个 .py AST+文本双 0 命中；业务选择器 0 命中
    （正向对照=上一条 test_business_selector_positive_control_legacy_repo）。"""
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    py_files = sorted(Path(x).resolve() for x in out.rglob("*.py"))
    assert py_files, "产物里一个 py 都没有=空壳假成功"
    for p in py_files:
        src = p.read_text(encoding="utf-8")
        _assert_compiles(src)
        assert is_atlas_free(src, str(p)) == [], p
        for mark in LEGACY_BUSINESS_SELECTOR_MARKS:
            assert mark not in src, (p, mark)


def test_compat_artifact_import_roots_within_third_party(tmp_path, monkeypatch):
    """D.1.5「三值外零命中」的判据消费方（COMPAT_ALLOWED_ROOTS 的**唯一**用点）。

    扫产物每个 .py 的 AST import 根，越界 → 红。stdlib 不在名单里，故对**标准库**
    必须能判可：判据口径 = 顶层包名不在 sys.stdlib_module_names ∧ 不在
    COMPAT_ALLOWED_ROOTS ∧ 不是产物内模块（垫片 fastplugin、同名 testcases 包）。

    怎么坏（删掉这条即失去哪道防线）：COMPAT_ALLOWED_ROOTS 会退回「注释里的承诺」——
    有人给产物模板加一句 `import requests`，全仓无一条测会红，旧仓机器上要等到
    真跑才 ModuleNotFoundError（#106 断链同族：判据在位但没人消费）。
    """
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    py_files = sorted(out.rglob("*.py"))
    assert py_files, "产物里一个 py 都没有=空壳假成功"

    local = {p.stem for p in py_files} | {p.parent.name for p in py_files}
    roots: set[str] = set()
    for p in py_files:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots.add(node.module.split(".")[0])
    roots.discard("__future__")
    third_party = {r for r in roots
                   if r not in sys.stdlib_module_names and r not in local}
    assert third_party <= set(COMPAT_ALLOWED_ROOTS), \
        f"产物出现名单外第三方 import 根（旧仓机器没装=运行期必炸）：{sorted(third_party)}"
    assert "atlas" not in roots, "第一红线：产物 import 根含 atlas"

    # 正向对照：名单判定非恒真——把「atlas 家族越界」喂进同一判据必须命中
    assert "atlas" not in COMPAT_ALLOWED_ROOTS
    assert {"requests"} - set(COMPAT_ALLOWED_ROOTS)


def _fixture_surface(src: str) -> dict:
    """AST 结构化提取 fixture 名 → (scope, autouse)——不比 unparse 字符串（引号会被归一，
    字符串比对在双引号字面量上必然假红；判据比的是装饰器 kwargs 的语义值）。"""
    found: dict[str, tuple] = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if (isinstance(d, ast.Call)
                        and getattr(d.func, "attr", "") == "fixture"
                        and getattr(getattr(d.func, "value", None), "id", "") == "pytest"):
                    kw = {k.arg: k.value for k in d.keywords}

                    def _lit(v):
                        return v.value if isinstance(v, ast.Constant) else None

                    found[node.name] = (_lit(kw.get("scope")),
                                        bool(_lit(kw.get("autouse")) if "autouse" in kw else False))
    return found


def test_compat_conftest_fixture_contract_ast():
    """C.5 scope/autouse 对拍：AST 读装饰器 kwargs 逐条核，不靠字符串碰巧。
    （browser=session 是修正②的兑现位——漂成 module 会静默增加 sole_page 重建。）"""
    steps = compat_specs_from_flow(_COMPAT_FLOW)
    src = render_compat_conftest(steps, "x")
    _assert_compiles(src)
    found = _fixture_surface(src)
    for name, scope, autouse in CONTRACT_FIXTURES:
        assert name in found, f"产物 conftest 缺同名 fixture：{name}"
        got_scope, got_autouse = found[name]
        if autouse:
            assert got_autouse is True, (name, found[name])
            assert got_scope is None, (name, found[name])
        else:
            assert got_scope == scope and got_autouse is False, (name, found[name])


def test_compat_conftest_fixture_set_is_exactly_seven(tmp_path, monkeypatch):
    """xcheck C5c 镜像测（守「不新增 fixture 名」）：产物 conftest 的 fixture 名集
    **恰好等于**契约七件套——多一个（此前 require_ctx/legacy_runner/legacy_testcases/
    legacy_pages_call 四个薄包装）= 旧仓与产物的语义面多一处要同步维护的分叉（#92 形态）；
    少一个 = 旧同名 fixture 的 case 拿不到注入直接红。用例模块另须零 fixture。"""
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    conf = (out / "conftest.py").read_text(encoding="utf-8")
    mod = next((out / "testcases").glob("*/test_*.py")).read_text(encoding="utf-8")
    assert set(_fixture_surface(conf)) == {name for name, _s, _a in CONTRACT_FIXTURES}
    for name in ("legacy_runner", "legacy_testcases", "legacy_pages_call", "require_ctx"):
        assert f"def {name}(" not in conf, f"私有 fixture 薄包装回潮：{name}"
        assert f"def {name}(" not in mod, f"私有 fixture 薄包装回潮：{name}"
    assert _fixture_surface(mod) == {}
    # helper 单一渲染源锁步：conftest 与用例模块的普通函数面同源在场（改一漏一必两处红）
    for fn in ("_legacy_sys_path", "_legacy_import", "_legacy_testcases_root",
               "_require_ctx", "_call_pages_fn", "class EnvBlocked"):
        assert fn in conf and fn in mod, fn



def test_contract_fixtures_match_legacy_repo_ast():
    """判据原文「从旧仓 conftest AST 提，不手抄」：旧仓可见时契约表名集 ⊆ 旧仓实名集且
    scope/autouse 逐条相等；不可见如实 skip（不装验证过，#64 诚实面）。"""
    web = os.environ.get("ATLAS_LEGACY_WEB", r"D:\GitHub\UI AUTO TEST\iservice")
    legacy_conftest = Path(web) / "conftest.py"
    if not legacy_conftest.is_file():
        pytest.skip("旧仓 conftest 本机不可见——交叉对拍不假绿")
    legacy = _fixture_surface(legacy_conftest.read_text(encoding="utf-8", errors="replace"))
    for name, scope, autouse in CONTRACT_FIXTURES:
        assert name in legacy, f"契约名 {name} 在旧仓 conftest 不存在——契约漂移，红"
        got_scope, got_autouse = legacy[name]
        if autouse:
            assert got_autouse is True, name
        else:
            assert got_scope == scope, (name, legacy[name])


def test_compat_pytest_ini_criteria_surface():
    """C.1 v2：产物自带 ini——判据面对齐旧仓（修正③：strict-markers/order 注册/no:playwright）。"""
    ini = render_compat_pytest_ini()
    for expect in ("testpaths = testcases", "python_classes = Test*",
                   "--strict-markers", "-p no:playwright", "-p no:randomly",
                   "    order:"):
        assert expect in ini, expect
    assert "atlas" not in ini  # 垫片经 conftest 委托 vendored fastplugin，不走 -p atlas.*


# ---- #35：产物 README 的两处失效文案（假承诺文件 + 已撤语义）----

def test_compat_readme_promises_no_unproduced_file(tmp_path, monkeypatch):
    """缺陷 1 判据（#106/#139 同族：README 宣称的产物没有生产方）。

    原文写「运行记录面=旧 allure-results + `reports/settle_stats.json`（三度量…）」，而
    `settle_stats` 全仓只活在这行模板文本与本测的存在性断言里——**零生产方**。旧仓的人拿到
    产物照 README 去找这个文件，永远找不到；断言当初还替它背书（存在性测把假承诺钉成绿）。
    处置选 lead 的 (a)（如实描述，不新增落盘面=不开新的 PRIVACY 义务）。两头都钉：
    - 反向：`settle_stats` 在产物 README 与本模块模板里都不得再出现（回潮即红）；
    - 正向：README 必须说清「只在显式设 `ATLAS_FAST_WAITS_STATS` 时落逐调用 jsonl，
      **不设=不产生任何统计文件**」，且只提 atlas 侧真有生产方的那个键。
    「那个键真有生产方」不靠本测自证，由 `test_compat_stats_env_lands_jsonl_end_to_end`
    在子进程里跑产物装配链实证——否则「如实描述」只是换了一句新的空话。
    """
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "settle_stats" not in readme, "零生产方的文件又回到产物 README"
    assert "settle_stats" not in (REPO_ROOT / "src" / "atlas" / "generator"
                                 / "emit_compat.py").read_text(encoding="utf-8"), \
        "模板侧回潮（README 与模板两处同源，删一处留一处=下一轮又长回来）"
    assert "ATLAS_FAST_WAITS_STATS" in readme
    for expect in ("逐调用 jsonl", "不设该键=不产生任何统计文件"):
        assert expect in readme, expect


def test_compat_readme_waits_claim_matches_tightened_semantics(tmp_path, monkeypatch):
    """缺陷 2 判据（文案事实已失效）：#33 撤了「界面安静即返回」这条准入（真机 A/B 两轮实证
    它把失败点从 step 7 前移到 step 1/step 3），README 还在用它当判据=向用户承诺一个已被
    证否的行为。同时「不得比原版更早失败」在旧口径下已被真机证否、在新口径下**从未复验**，
    按红线 #7/诚实面不许当承诺写在产物里。
    #36 追加：等待腿不再默认开——README 必须写明「实验档、需显式 opt-in、启用后要过失败点
    对齐闸」，不许回潮成「默认已开」。
    钉三头：撤掉的话不许回来、收紧后的判据必须在场、诚实标注不许被擦成承诺。"""
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    readme = (out / "README.md").read_text(encoding="utf-8")
    for gone in ("界面安静即返回", "不得比原版更早失败", "默认已开（unset/空值时补 'mask'"):
        assert gone not in readme, gone
    for expect in ("先观测到忙", "从未观测到忙 → 按原 ms 睡满", "绝不更长", "剩余预算",
                   "不构成提前许可", "不作「不会比原版更早失败」的承诺",
                   "不是默认开的功能", "不再代置位", "失败点与基线"):
        assert expect in readme, expect


def _stats_probe(tmp_path, monkeypatch, *, stats_env: bool):
    """子进程里跑**产物自己的装配链**（conftest.pytest_configure → 一次 wait_for_timeout →
    pytest_unconfigure），看 README 承诺的统计落盘是否真发生。

    playwright 用 stub 模块（`Page` 换成 FakePage）——判据问的是「jsonl 有没有生产方」，
    不是「浏览器能不能起」；真起浏览器属真机授权项（R 序列），F-1 不许在这里烧。
    """
    web = _fake_legacy_web(tmp_path)
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(web))
    # 两次调用共用同一个 tmp_path，产物目录必须分开——emit 有「目录非空拒写」闸（#force 语义），
    # 复用一个 pkg 会让第二格红在闸门上而不是判据上。
    out = tmp_path / ("pkg_stats" if stats_env else "pkg_nostats")
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    stats_path = out / "reports" / "settle.jsonl"
    script = (
        "import json, os, sys, types\n"
        f"sys.path.insert(0, {str(out)!r})\n"
        "fake_pw = types.ModuleType('playwright')\n"
        "sub = types.ModuleType('playwright.sync_api')\n"
        "class FakePage:\n"
        "    def evaluate(self, js):\n"
        "        return 'quiet'\n"
        "    def wait_for_timeout(self, ms=0):\n"
        "        raise AssertionError('垫片没装上：原始 sleep 被叫到了')\n"
        "sub.Page = FakePage\n"
        "sub.sync_playwright = lambda *a, **k: (_ for _ in ()).throw"
        "(AssertionError('本判据不起浏览器'))\n"
        "fake_pw.sync_api = sub\n"
        "sys.modules['playwright'] = fake_pw\n"
        "sys.modules['playwright.sync_api'] = sub\n"
        "import conftest, fastplugin\n"
        "cfg = types.SimpleNamespace()\n"
        "conftest.pytest_configure(cfg)\n"
        "FakePage().wait_for_timeout(120)\n"
        "conftest.pytest_unconfigure(cfg)\n"
        f"p = {str(stats_path)!r}\n"
        "st = fastplugin._STATE.get('stats')\n"
        "print(json.dumps({'file_exists': os.path.isfile(p),\n"
        "  'calls': len(st.calls) if st else 0,\n"
        "  'keys': sorted(st.calls[0].keys()) if st and st.calls else []}))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k in ("SYSTEMROOT", "PATH", "PATHEXT", "COMSPEC", "PYTHONIOENCODING",
                    "LOCALAPPDATA", "USERPROFILE")}
    env["ATLAS_LEGACY_WEB"] = str(web)
    # #36：等待垫片不再默认开——本格问的是「STATS 键有没有生产方」，必须先显式启用等待腿；
    # 否则垫片不装、原始 wait_for_timeout 被叫到，测的就不是统计面了（会红在错的地方）。
    env["ATLAS_FAST_WAITS"] = "mask"
    if stats_env:
        env["ATLAS_FAST_WAITS_STATS"] = str(stats_path)
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=240)
    assert r.returncode == 0, (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:]
    import json as _json
    return _json.loads(r.stdout.strip().splitlines()[-1])


def test_compat_stats_env_lands_jsonl_end_to_end(tmp_path, monkeypatch):
    """README 新文案的两条主张都用产物装配链实证（不是拿 src 侧测借光）：
    - 显式设 `ATLAS_FAST_WAITS_STATS` → 逐调用 jsonl **真落盘**，且七键面齐全
      （caller_file/line/func/requested_ms/actual_ms/probe/fallback——设计 §A + G11-4 形态）；
    - 不设该键 → **不产生任何统计文件**（README 就是这么写的；若实现悄悄写默认路径，
      README 就又变成新的假承诺，这格是反向对照）。
    """
    got = _stats_probe(tmp_path, monkeypatch, stats_env=True)
    assert got["calls"] >= 1, got
    assert got["file_exists"] is True, got
    assert set(got["keys"]) == {"caller_file", "caller_line", "caller_func",
                                "requested_ms", "actual_ms", "probe", "fallback"}, got
    off = _stats_probe(tmp_path, monkeypatch, stats_env=False)
    assert off["calls"] >= 1, off          # 垫片照样在跑（统计对象仍计数）
    assert off["file_exists"] is False, off   # 但没设键就不许落文件


def test_compat_readme_declares_deps_and_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    readme = (out / "README.md").read_text(encoding="utf-8")
    for expect in ("pytest-order>=1.1", "-p no:playwright", "ATLAS_LEGACY_WEB",
                   "ATLAS_FAST_LOGIN_INDICATOR", "不 import atlas",
                   "ATLAS_FAST_WAITS_STATS", "不产 runs.jsonl", "只生成不执行", "绝不写旧仓"):
        # 真命题=「README 说明了统计只在显式设 ATLAS_FAST_WAITS_STATS 时落盘」——原断言写的是
        # "settle_stats"，那文件全仓零生产方（#35 缺陷 1：文档承诺了没人写的文件），
        # 断言跟着承诺=把假绿钉进测里，故一并换成有生产方的那个键名。
        assert expect in readme, expect


# ---- #31：兼容包默认置位（--compat 按构造就是快包，不设 env 也不能空转）----

def _probe_fast_defaults(tmp_path, monkeypatch, extra_env=None, break_install=False):
    """子进程里跑产物 conftest 的 pytest_configure，回传 env 实值与 fastplugin 装配状态。

    必须子进程：装配是**进程级副作用**（类级补丁 `playwright.sync_api.Page` + allure 包装），
    在本 pytest 进程里跑会把补丁留在后面所有测试的 Page 类上（与 #27 那条 sys.path 外溢
    同一族教训，见 `_artifact_runtime_ns` docstring）。

    `break_install=True` 把 `fastplugin.pytest_configure` 换成必抛错的桩——产物 conftest
    那条腿是 `try/except → stderr WARN`（装配坏不许顶掉整个 run，设计对），所以 rc 与
    collect-only 全都看不出事；这一格存在的意义就是证明**判据读的是 `_STATE` 事实而不是
    rc**：坏装配下 `installed` 必须翻假，否则上面那几格是恒绿。
    """
    web = _fake_legacy_web(tmp_path)
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(web))   # 生成期符号核验用（子进程 env 另建）
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    break_line = ("fastplugin.pytest_configure = lambda cfg: (_ for _ in ()).throw"
                  "(RuntimeError('故意坏装配'))\n" if break_install else "")
    script = (
        "import json, os, sys, types\n"
        f"sys.path.insert(0, {str(out)!r})\n"
        "import conftest, fastplugin\n"
        + break_line +
        "conftest.pytest_configure(types.SimpleNamespace())\n"
        "print(json.dumps({'waits': os.environ.get('ATLAS_FAST_WAITS'),\n"
        "  'shots': os.environ.get('ATLAS_FAST_NO_STEP_SHOTS'),\n"
        "  'installed': fastplugin._STATE['waits_installed'],\n"
        "  'shots_installed': fastplugin._STATE['shots_installed']}))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k in ("SYSTEMROOT", "PATH", "PATHEXT", "COMSPEC", "PYTHONIOENCODING",
                    "LOCALAPPDATA", "USERPROFILE")}
    env["ATLAS_LEGACY_WEB"] = str(web)
    for key in ("ATLAS_FAST_WAITS", "ATLAS_FAST_NO_STEP_SHOTS", "ATLAS_FAST_WAITS_STATS",
                "ATLAS_FAST_MIN_MS", "ATLAS_FAST_SETTLE_ANCHOR", "ATLAS_FAST_LOGIN_INDICATOR"):
        env.pop(key, None)                      # 基线=**一个 ATLAS_FAST_* 都不设**
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180)
    assert r.returncode == 0, (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:]
    import json as _json
    return _json.loads(r.stdout.strip().splitlines()[-1])


def test_compat_conftest_sets_fast_defaults_before_delegation():
    """判据 1（形态）：置位必须在**委托 fastplugin 之前**——顺序错了就是原缺陷（委托时 env
    还空着，垫片照样不装，产物看着有那几行却仍然空转）。同时钉「不用 setdefault」的语义
    与 CLI 面一致（setdefault 不覆盖空串，`ATLAS_FAST_WAITS=` 那格会两侧分叉）。"""
    src = render_compat_conftest(compat_specs_from_flow(_COMPAT_FLOW), "x")
    _assert_compiles(src)
    i_loop = src.index("for _key, _default in _FAST_DEFAULTS:")
    i_delegate = src.index("fastplugin.pytest_configure(config)")
    assert i_loop < i_delegate, "置位晚于委托=垫片照旧不装（#31 原缺陷换形态）"
    assert "if not os.environ.get(_key):" in src
    # 带括号的调用形态（注释里讲「为什么不用 setdefault」不算调用）
    assert "os.environ.setdefault(" not in src
    assert "_FAST_DEFAULTS = (" in src          # 清单在模块层，值来自 CLI 面同源


def test_compat_artifact_fast_defaults_align_cli_side():
    """跨面锁步（lead：两边语义一字不差）：产物文本里的默认清单 ==
    `legacy.LegacyAdapter.FAST_DEFAULTS`（CLI 面唯一清单）。CLI 侧加/改/删旋钮，这格必红；
    反向也一样——emit_compat 手抄第二份清单同样是这格红（#92）。"""
    from atlas.legacy import LegacyAdapter
    src = render_compat_conftest(compat_specs_from_flow(_COMPAT_FLOW), "x")
    literal = ast.parse(src).body
    emitted = None
    for node in literal:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_FAST_DEFAULTS" for t in node.targets):
            emitted = ast.literal_eval(node.value)
    assert emitted is not None, "产物 conftest 没有 _FAST_DEFAULTS 清单位"
    assert tuple(emitted) == tuple(LegacyAdapter.FAST_DEFAULTS), (
        emitted, LegacyAdapter.FAST_DEFAULTS)


def test_compat_artifact_no_env_enables_shots_only(tmp_path, monkeypatch):
    """#36（取代 #31 的等待腿默认置位）：产物在**一个 ATLAS_FAST_* 都不设**的子进程里
    跑 pytest_configure——截图抑制必须真装上（shots_installed=True），**等待垫片不得装**
    （waits_installed=False）：mask/mask+xhr 在真机上间歇把失败点前移，按 G11-1 不能默认发。
    判据读的是 `_STATE` 装配事实，不是 env 面文本——#30 的「以为开了其实没装」教训沿用。"""
    got = _probe_fast_defaults(tmp_path, monkeypatch)
    assert got["waits"] is None, got             # 不再代置位
    assert got["shots"] == "1", got
    assert got["installed"] is False, got        # 等待垫片不装
    assert got["shots_installed"] is True, got   # 截图抑制真装上


def test_compat_artifact_empty_env_does_not_enable_waits(tmp_path, monkeypatch):
    """`ATLAS_FAST_WAITS=''`（显式空值）→ 等待垫片保持关闭：空值不是「补默认」的入口，
    更不是「开等待腿」的入口（清单内只有截图抑制）。与 CLI 面同一把尺（#92）。"""
    got = _probe_fast_defaults(tmp_path, monkeypatch, {"ATLAS_FAST_WAITS": "",
                                                       "ATLAS_FAST_NO_STEP_SHOTS": ""})
    assert got["waits"] in (None, ""), got
    assert got["installed"] is False, got
    assert got["shots"] == "1", got              # 截图抑制仍补默认
    assert got["shots_installed"] is True, got


def test_compat_artifact_explicit_waits_opt_in_still_works(tmp_path, monkeypatch):
    """显式 opt-in 的正向对照（#36 撤的是默认，不是能力）：设 `ATLAS_FAST_WAITS=mask`
    → 产物装配链上等待垫片真装上。没有这格，「默认关」可能被实现成「永远关」。"""
    got = _probe_fast_defaults(tmp_path, monkeypatch, {"ATLAS_FAST_WAITS": "mask"})
    assert got["waits"] == "mask", got
    assert got["installed"] is True, got


def test_compat_probe_is_not_vacuous_when_install_leg_breaks(tmp_path, monkeypatch):
    """差分自证（WARN-only 降级腿是 rc 类判据的盲区）：把 fastplugin 的装配换成必抛错的桩，
    产物 conftest 仍 rc0（那条腿设计上就只 WARN，不许顶掉整个 run），但 `installed` 必须翻假。
    删掉这格 → 上面的行为测可能在「只看 rc/只看 env」的写法下恒绿；这格证明它们读的是
    装配事实本身。**必须显式 opt-in 等待腿**（#36 后默认不装，不设 env 时本格会恒真）。"""
    got = _probe_fast_defaults(tmp_path, monkeypatch,
                               {"ATLAS_FAST_WAITS": "mask"}, break_install=True)
    assert got["installed"] is False, got
    assert got["waits"] == "mask", got           # 置位发生在委托之前，坏装配不影响 env 面


def test_compat_artifact_respects_explicit_off(tmp_path, monkeypatch):
    """判据 3：显式 `ATLAS_FAST_WAITS=off`（与 NO_STEP_SHOTS=0）→ 产物保持用户值不被改写，
    垫片不装=旧行为可退回（默认清单≠强制开；这是「显式设置恒优先」的那半条闸）。"""
    got = _probe_fast_defaults(tmp_path, monkeypatch, {"ATLAS_FAST_WAITS": "off",
                                                       "ATLAS_FAST_NO_STEP_SHOTS": "0"})
    assert got["waits"] == "off", got
    assert got["shots"] == "0", got
    assert got["installed"] is False, got



@pytest.mark.parametrize("bad_flow,mark", [
    ({"id": "x", "steps": [{"task": "api_call", "method": "GET", "path": "/a"}]}, "API 腿"),
    ({"id": "x", "steps": [{"task": "pick", "intent": "选", "page_object": "ghost"}]},
     "page_object"),
    ({"id": "x", "steps": [{"task": "do_something", "intent": "做"}]}, "缺声明位"),
    ({"id": "x", "steps": []}, "至少一个 step"),
], ids=["api_call", "page_object", "missing-decl", "empty"])
def test_compat_rejects_unsupported_legs_by_name(bad_flow, mark, tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "p"
    with pytest.raises(CompatEmitError) as ei:
        emit_compat_package(out_dir=out, flow=bad_flow)
    assert mark in str(ei.value), (mark, str(ei.value))
    assert not out.exists() or not any(out.iterdir())  # 拒绝先于写盘，零残留


def test_compat_symbol_check_names_absent_function(tmp_path, monkeypatch):
    """legacy_pages 函数名对不上 → 生成期点名拒（可核即核，拒半成品）；
    正向对照=名字对得上放行；env 不可见→不装验证过（运行期 EnvBlocked 兜，另一条测守）。"""
    web = _fake_legacy_web(tmp_path, fn_names=("other_fn",))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(web))
    out = tmp_path / "p"
    with pytest.raises(CompatEmitError) as ei:
        emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    assert "demo_cancel_fn" in str(ei.value) and "不在旧 lib.pages" in str(ei.value)
    assert not out.exists() or not any(out.iterdir())
    # 正向对照：补齐函数名后放行
    _fake_legacy_web(tmp_path, fn_names=("other_fn", "demo_cancel_fn"))
    written = emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    assert written
    # env 不可见：不拦生成（运行期 fixture EnvBlocked 兜底是另一条链）
    monkeypatch.delenv("ATLAS_LEGACY_WEB", raising=False)
    monkeypatch.delenv("ATLAS_LEGACY_PROJECT", raising=False)
    out2 = tmp_path / "p2"
    assert emit_compat_package(out_dir=out2, flow=_COMPAT_FLOW)


# ---- 旧仓路径解析（lead P0 实测：Atlas 标准 .env = PROJECT 绝对根 + WEB 相对子目录名）----

def _artifact_runtime_ns() -> dict:
    """把 `_compat_runtime_lines()` 模板文本单独 exec 出来核对**运行行为**。

    只核对不 import 的东西（os/Path/importlib + sys 的**副本**），不载整个 conftest——
    pytest、playwright、fastplugin 都不是这条判据要的对象；判据问的是「给定 env 两键，
    产物侧解析出的目录是哪个」，模板文本比对看不出错位的路径拼接。

    sys 必须是副本：`_legacy_sys_path()` 成功路径会 `sys.path.insert(0, web)`（产物设计
    如此，旧 conftest 同款）。在本 pytest 进程里直接跑会把 tmp 假旧仓的 `lib/` 灌进全局
    sys.path——实案：`test_legacy_step_loading.py::
    test_from_module_probe_miss_gets_attributed_import_error` 的「祖先无 lib」前提被打破
    （stray 模块的 `from lib.pages import …` 改报「无法导入 open_doc_list」而非
    ModuleNotFoundError 'lib'），报的是别人的红。「插进真实 sys.path」这一面由子进程
    collect-only 那条测守（进程隔离，污染不出去）。
    """
    import importlib as _importlib

    class _SysShim:
        path: list = list(sys.path)
        stderr = sys.stderr

    ns = {"os": os, "sys": _SysShim(), "Path": Path, "importlib": _importlib}
    exec("\n".join(_compat_runtime_lines()), ns)
    return ns


def test_legacy_web_standard_env_relative_form(tmp_path, monkeypatch):
    """今天红的格（lead 23:1x 实测复现）：标准 .env 形态 `PROJECT=<绝对根>` +
    `WEB=iservice`（相对子目录名）下兼容包必须导得出。
    旧实现把 WEB 当独立绝对路径 → 「读不到 lib/pages.py」拒产，用户照 Atlas 文档配置
    就永远拿不到产物——不是环境问题，是 #18 交付阻塞缺陷。"""
    root = tmp_path / "UI AUTO TEST"          # 带空格的真实旧仓目录名形态
    web = root / "iservice"
    (web / "lib").mkdir(parents=True)
    (web / "lib" / "pages.py").write_text(
        "def demo_cancel_fn(page, ctx):\n    return True\n\n"
        "def other_fn(page, ctx):\n    return True\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(root))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    assert _legacy_web_dir() == web           # 生成期：PROJECT/WEB
    out = tmp_path / "pkg"
    assert emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    # 产物侧同答（模板 exec 出真函数跑，不比字符串）；sys 用副本，插桩不外溢（见 helper 注）
    ns = _artifact_runtime_ns()
    assert Path(ns["_legacy_sys_path"]()) == web
    assert str(web) in ns["sys"].path and str(web) not in sys.path


def test_legacy_web_absolute_form_still_works(tmp_path, monkeypatch):
    """向后兼容格（今天绿，别改坏）：WEB 直接给绝对路径、PROJECT 不配 → 照常导出，
    两侧都按 WEB 自身定位（pathlib 拼接同语义，不新写第三条规则）。"""
    monkeypatch.delenv("ATLAS_LEGACY_PROJECT", raising=False)
    web = _fake_legacy_web(tmp_path)
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(web))
    assert _legacy_web_dir() == web
    out = tmp_path / "pkg"
    assert emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    ns = _artifact_runtime_ns()
    assert Path(ns["_legacy_sys_path"]()) == web


def test_legacy_web_relative_without_project_names_the_key(tmp_path, monkeypatch):
    """缺 PROJECT + 相对 WEB → **点名是哪个键**（两面）：生成期 CompatEmitError、
    产物运行期 EnvBlocked，都不是裸 FileNotFoundError/NotADirectoryError（#103 归因
    口径：路径问题不许漂成框架缺陷）。生成期还须零残留。"""
    monkeypatch.delenv("ATLAS_LEGACY_PROJECT", raising=False)
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    out = tmp_path / "pkg"
    with pytest.raises(CompatEmitError) as ei:
        emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    assert "ATLAS_LEGACY_PROJECT" in str(ei.value)
    assert not out.exists() or not any(out.iterdir())
    ns = _artifact_runtime_ns()
    with pytest.raises(ns["EnvBlocked"]) as eb:
        ns["_legacy_sys_path"]()
    assert "ATLAS_LEGACY_PROJECT" in str(eb.value)
    assert issubclass(ns["EnvBlocked"], RuntimeError)   # 自家终态类型，不是 OSError 冒充


def test_legacy_path_resolution_lockstep_with_config(tmp_path, monkeypatch):
    """镜像锁步（lead 点名要的那把闸）：Atlas 侧唯一真相是
    `config.check_legacy_paths(legacy_project, legacy_web)`（exec_dir=project/web）。
    - 生成期相对形态的**存在性判据**必须委托它：坏 WEB 时消息原文来自 config，
      不是 emit_compat 自造话术（#92 改一漏一的形态就是两份话术）；
    - 产物模板文本必须镜像同一条规则（内联不可避免——C.3 不许 import atlas——
      所以钉「拼接式 + 键名 + 注释点名 config.check_legacy_paths」三处）。
    config 侧规则变了（换键/多一层/改消息出处），这里必红。
    """
    root = tmp_path / "proj"
    web = root / "iservice"
    (web / "lib").mkdir(parents=True)
    (web / "lib" / "pages.py").write_text("def f(page, ctx):\n    return True\n",
                                          encoding="utf-8")
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(root))
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "iservice")
    assert _legacy_web_dir() == web
    monkeypatch.setenv("ATLAS_LEGACY_WEB", "nope")
    with pytest.raises(CompatEmitError) as ei:
        _legacy_web_dir()
    assert "旧项目 web 子目录不存在" in str(ei.value)   # config 的原话，未复写
    text = "\n".join(_compat_runtime_lines())
    assert "Path(proj) / root" in text                  # 同一条拼接式
    assert "_LEGACY_PROJECT_ENV_KEY" in text and 'os.environ.get(_LEGACY_PROJECT_ENV_KEY' in text
    assert "config.check_legacy_paths" in text          # 注释点名镜像出处



def test_bridge_step_bad_shape_rejected(tmp_path, monkeypatch):
    """桥接腿声明位坏形点名步骤：缺 module/缺 case_id/绝对路径/上跳/非布尔
    skip_postcondition——五种都拒且零残留（原子应用纪律）。"""
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    bad_flows = [
        {"id": "x", "steps": [{"task": "legacy_case", "case_id": "TC1"}]},
        {"id": "x", "steps": [{"task": "legacy_case", "module": "a/b.yaml"}]},
        {"id": "x", "steps": [{"task": "legacy_case", "module": "/abs/a.yaml",
                               "case_id": "TC1"}]},
        {"id": "x", "steps": [{"task": "legacy_case", "module": "../a.yaml",
                               "case_id": "TC1"}]},
        {"id": "x", "steps": [{"task": "legacy_case", "module": "a/b.yaml",
                               "case_id": "TC1", "skip_postcondition": "yes"}]},
    ]
    for bf in bad_flows:
        out = tmp_path / f"p{id(bf)}"
        with pytest.raises(CompatEmitError) as ei:
            emit_compat_package(out_dir=out, flow=bf)
        assert "步骤 1" in str(ei.value), str(ei.value)
        assert not out.exists() or not any(out.iterdir())


def test_compat_package_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    a, b = tmp_path / "a", tmp_path / "b"
    emit_compat_package(out_dir=a, flow=_COMPAT_FLOW)
    emit_compat_package(out_dir=b, flow=_COMPAT_FLOW)

    def tree(p):
        return {str(x.relative_to(p)): x.read_bytes()
                for x in sorted(p.rglob("*")) if x.is_file()}
    assert tree(a) == tree(b)


def test_compat_package_refuses_nonempty_without_force(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(_fake_legacy_web(tmp_path)))
    out = tmp_path / "p"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(CompatEmitError):
        emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    assert (out / "keep.txt").read_text(encoding="utf-8") == "x"  # 用户文件原样
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW, force=True)


def test_compat_package_requires_flow_source(tmp_path):
    """endpoints/空 flow 进 compat=点名拒绝（兼容腿把手只在 flow 里，不产空壳）。"""
    with pytest.raises(CompatEmitError):
        emit_compat_package(out_dir=tmp_path / "p", flow={})
    with pytest.raises((CompatEmitError, PytestEmitError)):
        emit_pytest_package(out_dir=tmp_path / "p2", endpoints=[_read_endpoint()],
                            fast=True, compat="legacy-suite")


def test_artifact_collects_without_atlas_on_path(tmp_path, monkeypatch):
    """D.1.5 主判据：纯净 sys.path 下产物 collect-only rc0——「旧仓机器没装 atlas」的实证形态。

    光靠「PYTHONPATH 里不放 src」在本机不成立：atlas 是 pip -e 装进 site-packages 的，
    干净 env 照样 import 得到——判据因此是**遮蔽式阻断桩**（PYTHONPATH 首位放一个
    `atlas/__init__.py`，一被 import 就 ImportError 点名），产物若真引 atlas 必红。
    对照格（同 env 下一个 import atlas 的用例必被阻断）证明这把闸非恒真。
    子进程只收集，不发请求不起浏览器（F-1 合规；真跑腿的取证在 R 序列，不在门禁套件）。
    """
    web = _fake_legacy_web(tmp_path)
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(web))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    # 阻断桩：ASCII 消息（X4——它会出现在子进程 stderr，中文段在 GBK 控制台会二次崩）
    block = tmp_path / "atlas_block"
    (block / "atlas").mkdir(parents=True)
    (block / "atlas" / "__init__.py").write_text(
        "raise ImportError('ATLAS-IMPORT-BLOCKED: artifact must not import atlas')\n",
        encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k in ("SYSTEMROOT", "PATH", "PATHEXT", "COMSPEC", "PYTHONIOENCODING",
                    "LOCALAPPDATA", "USERPROFILE")}
    env["PYTHONPATH"] = str(block) + os.pathsep + str(web)  # 只有桩+假旧仓，无 atlas src

    def _run(*args, cwd=None):
        return subprocess.run([sys.executable, "-m", "pytest", *args], env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=cwd, timeout=180)

    r = _run("-q", "--collect-only", str(out))
    assert r.returncode == 0, (r.stdout or "")[-2500:] + (r.stderr or "")[-1500:]
    assert "3 tests collected" in r.stdout, r.stdout[-1500:]
    assert "error" not in r.stdout.lower().replace("0 error", ""), r.stdout[-1500:]
    assert "ATLAS-IMPORT-BLOCKED" not in (r.stdout + r.stderr)  # 产物确实没碰桩

    # 正对照：同 env 里真 import atlas 的文件必被这把闸打死（否则上面的绿是恒真）
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "test_probe.py").write_text("import atlas\n\n\ndef test_x():\n    assert True\n",
                                         encoding="utf-8")
    rc_probe = _run("-q", "--collect-only", str(probe), cwd=str(probe.parent))
    assert rc_probe.returncode != 0 and "ATLAS-IMPORT-BLOCKED" in (rc_probe.stdout + rc_probe.stderr), \
        "阻断桩没生效=这格绿是假的（判据恒真）"



def test_compat_conftest_get_page_uses_env_indicator():
    """登录判定形态（lead 注意②兑现面）：指示器只读 env、登录动作只走旧 lib.login 委托，
    conftest 模板内既无业务选择器也无自实现表单代码。"""
    src = render_compat_conftest(compat_specs_from_flow(_COMPAT_FLOW), "x")
    assert 'os.environ.get(_INDICATOR_ENV_KEY, "").strip()' in src
    assert "缺配置：ATLAS_FAST_LOGIN_INDICATOR" in src or "_INDICATOR_ENV_KEY} 未设置" in src
    assert "_legacy_import(\"lib.login\")" in src        # 委托旧实现，不复制登录表单
    assert "def do_login" not in src                      # 产物不自造登录实现
    assert "page._headless = headless" in src             # 旧仓赋值保留（R-3）


def test_compat_conftest_get_page_reads_case_top_level_account(tmp_path, monkeypatch):
    """§17：产物必须读 **case 顶层 `account`**（旧 yaml `cases[].account` 的实际位置）。

    怎么坏（删掉这条即失去哪道防线）：旧 conftest 只读 `case['session']['account']`，
    而旧 yaml **没有 session 键**——旧仓靠测试文件显式 `get_page(..., account=TC1_ACCOUNT)`
    兜住；兼容产物没有该传参位，只抄旧链则 account 恒空 → 回落 env。当「env 账号 ≠ 本 case
    账号」时（实测 prod_am 需 iservice，本机 ISERVICE_USERNAME 是他人账号）**静默登错账号**：
    不报错、不 EnvBlocked，跟踪单跑在别人账号上。这格拿真 yaml 形态喂产物 conftest 的
    `_factory`，断言 account 取自 case 顶层而不是 env。
    """
    web = _fake_legacy_web(tmp_path)
    monkeypatch.setenv("ATLAS_LEGACY_WEB", str(web))
    out = tmp_path / "pkg"
    emit_compat_package(out_dir=out, flow=_COMPAT_FLOW)
    script = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(out)!r})\n"
        "import conftest\n"
        "seen = {}\n"
        "class _Login:\n"
        "    @staticmethod\n"
        "    def do_login(page, base_url, username='', password=''):\n"
        "        seen['account'] = username\n"
        "    @staticmethod\n"
        "    def do_logout(page):\n"
        "        pass\n"
        "conftest._legacy_import = lambda name: _Login\n"
        "class _Page:\n"
        "    class _Loc:\n"
        "        @staticmethod\n"
        "        def count(): return 0\n"          # 未登录 → 走登录腿，暴露传入账号
        "    def locator(self, sel): return self._Loc()\n"
        "    def set_default_timeout(self, ms): pass\n"
        "fixture = next(conftest.get_page.__wrapped__(_Page(), {}))\n"
        "case = {'account': 'case-acct', '_base_url': 'https://x.example.com/'}\n"
        "fixture(case, {})\n"
        "print(json.dumps(seen))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k in ("SYSTEMROOT", "PATH", "PATHEXT", "COMSPEC", "PYTHONIOENCODING",
                    "LOCALAPPDATA", "USERPROFILE")}
    env["ATLAS_LEGACY_WEB"] = str(web)
    env["ATLAS_FAST_LOGIN_INDICATOR"] = ".user-info"   # 过指示器前置，才轮到账号解析
    env["ATLAS_FAST_ACCOUNT"] = "env-acct"             # 反向对照：env 有不许盖过 case
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:]
    seen = json.loads([l for l in r.stdout.splitlines() if l.startswith("{")][-1])
    assert seen["account"] == "case-acct", (
        f"账号必须取自 case 顶层（旧 yaml cases[].account），实际 {seen['account']!r}"
        f"——回落 env 会在 env 账号≠case 账号时静默登错账号")

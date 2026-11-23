"""探针骨架（`generator/emit_probe`）身份与产物守卫。

## 挡的是什么（分析出的两条真实缺陷，都不报错、只是「名不副实」）

`atlas gen` 会写 `probes/<名>.candidate.py`，类名与 `entity` 名同源。旧实现在
`generator/__init__.py` 里手写 `slugify(摘要或文件名, fallback="GeneratedProbe") + "Probe"`：

1. **中文标题**（本项目主线文档语言）→ `slugify` 把非 ASCII 全抹掉 → 空 → 落兜底
   `GeneratedProbe` → 再拼 `Probe` = `GeneratedProbeProbe`；
2. **中文里夹的 ASCII 残渣** → `订单 \\s 查询` 只剩 `s` → `class sProbe(DataProbe)`：
   既不是业务名也不是兜底名，且长度 1 的标识符多半是转义/标点漏出来的。

两条都源于「拿字符串当身份，但不判它是不是有效身份」。判据单点改为
`emit_probe.probe_entity_name`（summary → 文件名 stem → 兜底，取第一个够长的 ASCII slug）。

## 三条

1. `test_probe_entity_name_is_valid_and_meaningful`：取值表（英文/中文/残渣/引号/反斜杠/空）
   全部是合法标识符、无 `ProbeProbe` 双后缀、无长度 <3 的残渣名，且确定性（同输入同输出）。
2. `test_probe_skeleton_is_warning_clean_and_find_only`：产物按 CI 严档可编译；
   `create()` 恒 `NotImplementedError`（红线 3/12/16 不在骨架里造假建单）。
3. `test_generator_uses_single_named_entry`：源码面——`generator/__init__.py` 必须调
   `probe_entity_name(...)`，且不得再出现 `+ "Probe"` 这类就地拼名（防漂移回旧形态）。
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

from atlas.generator.emit_probe import (
    PROBE_FALLBACK_NAME,
    emit_probe_skeleton,
    probe_entity_name,
)
from atlas.generator.model import Endpoint

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GENERATOR_INIT = Path(__file__).resolve().parents[1] / "src" / "atlas" / "generator" / "__init__.py"


def test_probe_entity_name_is_valid_and_meaningful():
    cases = {
        "get web example order": "GetWebExampleOrderProbe",
        "查询订单列表": PROBE_FALLBACK_NAME,          # 中文摘要本身给不出 ASCII 名
        "订单 \\s 查询": PROBE_FALLBACK_NAME,         # 残渣 `s` 不算名字
        'cmd"weird': "CmdWeirdProbe",
        "GET /api/x": "GetApiXProbe",
        "": PROBE_FALLBACK_NAME,
    }
    for summary, expected in cases.items():
        got = probe_entity_name(summary)
        assert got == expected, f"摘要 {summary!r} → {got!r}（期望 {expected!r}）"
        assert _IDENT.match(got), f"{got!r} 不是合法 Python 标识符"
        assert "ProbeProbe" not in got, f"{got!r}：兜底词自带后缀，不该再拼一次"
        assert got == probe_entity_name(summary), "取值必须确定性（同输入同输出）"

    # 摘要给不出名字时退到文件名（stem），再退兜底——两级都要能用
    assert probe_entity_name("查询订单", "api_sample") == "ApiSampleProbe"
    assert probe_entity_name(None, None) == PROBE_FALLBACK_NAME
    # 反向对照：够长的英文 slug 不得被兜底吞掉
    assert probe_entity_name("sales order") == "SalesOrderProbe"
    # ③ 缺陷本体：带 `-` 的 slug 曾原样进 `class` 行（产物语法错）——必须已是合法标识符
    assert probe_entity_name("get web example customer code") == "GetWebExampleCustomerCodeProbe"


def test_probe_skeleton_is_warning_clean_and_find_only():
    name = probe_entity_name("查询订单列表", "api_sample")
    source = emit_probe_skeleton([Endpoint(method="GET", path="/api/x", summary="查询")],
                                 entity_name=name)
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        compile(source, "<probe>", "exec")
    assert f"class {name}(DataProbe):" in source
    assert f'entity = "{name}"' in source
    # find 是 TODO 骨架（不伪造查询），create 恒拒绝（红线 3/12/16）
    assert "raise NotImplementedError" in source
    create_body = source.split("def create", 1)[1]
    assert "NotImplementedError" in create_body


def test_generator_uses_single_named_entry():
    text = _GENERATOR_INIT.read_text(encoding="utf-8")
    assert "probe_entity_name(" in text, "探针身份必须走 emit_probe.probe_entity_name 单点"
    assert 'fallback="GeneratedProbe") + "Probe"' not in text, "旧的就地拼名形态又回来了"

    # 正向对照：旧公式在英文摘要上产出的名字必须**仍是非法标识符**——否则本文件的前提消失，
    # 需要重新评估（守卫不能靠「旧代码碰巧也合法」维持绿灯）。
    from atlas.generator.dsl_utils import slugify

    old_name = slugify("Get web example customer code", fallback="GeneratedProbe") + "Probe"
    assert old_name == "get-web-example-customer-codeProbe"
    assert not _IDENT.match(old_name), "旧公式产出的名字变合法了——重新核对本守卫的前提"


# ---- 4. 端到端：真的走一遍 gen，产物探针必须是能编译的合法模块 ----

def test_gen_writes_compilable_probe_artifact(tmp_path):
    """中文标题 + 英文标题两份文档各跑一次 `generate`：探针文件名/类名合法且可严档编译。

    这条挡的是「身份判据对了但写盘路径没接上」：旧产物
    `probes/get-web-example-customer-codeProbe.candidate.py` 里是
    `class get-web-example-customer-codeProbe(DataProbe):` —— 语法错的 .py，`gen` 仍 rc 0。
    """
    from atlas.generator import GenOptions, generate

    docs = {
        "en_case.md": ("# Get web example customer code\n\n"
                       "## GET /web/example/customer/code\n\n| 参数 | 说明 |\n|---|---|\n"
                       "| code | 客户编码 |\n"),
        "cn_case.md": ("# 查询客户编码\n\n"
                       "## GET /web/example/customer/code\n\n| 参数 | 说明 |\n|---|---|\n"
                       "| code | 客户编码 |\n"),
    }
    for name, text in docs.items():
        source = tmp_path / name
        source.write_text(text, encoding="utf-8")
        out = tmp_path / f"out_{name}"
        result = generate(source, out, GenOptions(scenario="a"))
        probe = Path(result.probe_path) if getattr(result, "probe_path", "") else None
        probes = list((out / "probes").glob("*.candidate.py"))
        assert len(probes) == 1, f"{name}: 探针产物数量异常 {probes}{probe or ''}"
        src = probes[0].read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            compile(src, str(probes[0]), "exec")
        class_line = next(ln for ln in src.splitlines() if ln.startswith("class "))
        ident = class_line.split()[1].split("(")[0]
        assert _IDENT.match(ident), f"{name}: 探针类名 {ident!r} 不是合法标识符"

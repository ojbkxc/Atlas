"""generator/markdown.py：JSON 样例的请求/响应**大小写不敏感**判定（lead P0 追加，#27 同批）。

盘面缺陷（lead 核原文）：`low = (preceding or "").lower()` 算出来**从没用**，下一行用
原文 `preceding` 匹配关键词——中文关键词无大小写不受影响，但英文大写开头
（`Response:` / `Request Body` / `Body`）永远匹配不上，落进 else 分支按「第一个对象当
请求、数组当响应」瞎猜，请求/响应误判=假阴性同族 #102。

「删掉即失去哪道防线」点名：这四格被删 → 该漏接线可以回潮而全仓依旧全绿（原缺陷正是
全绿状态下潜伏的）；大写英文文档样例的归属判定就再没有锚点。其中两格是**正对照**
（小写/中文形态今天也绿且不许改坏，#115「修归因不得把正常路径翻成错误」）。

文件归属偏离：lead 点名把本修与 #27 一起交，但 tests/test_gen.py 是并行队友在改的文件
（任务原文点名勿动），故判据测落本新文件——不与他人编辑面相撞。
"""
from __future__ import annotations

from atlas.generator.markdown import parse_markdown


def _one(preceding: str, payload: str):
    """文档块=METHOD+path（_extract_method_path 认它）+ 上下文行 + json 栅栏；返回唯一 Endpoint。"""
    doc = f"## 样例\n\nGET /api/demo\n\n{preceding}\n\n```json\n{payload}\n```\n"
    eps = parse_markdown(doc)
    assert len(eps) == 1, eps
    return eps[0]


def test_uppercase_response_keyword_is_response():
    """今天红的格：`Response:` 大写开头必须判响应。

    旧实现用原文比对 → 匹配不上 → 走 else 把第一个对象当**请求体**（body.kind=json），
    响应面为空——生成的 flow/契约把响应样例发成请求正文，就是这类误判的下游形态。
    """
    ep = _one("Response:", '{"resultCode": "0", "data": {"id": 1}}')
    assert ep.body.kind == "none", f"响应样例被当成请求体：{ep.body}"
    assert ep.response.sample == {"resultCode": "0", "data": {"id": 1}}
    assert ep.response.is_object is True


def test_uppercase_body_keyword_is_request():
    """今天红的格：`Body` 大写开头 + 数组载荷 → 显式判请求体。

    旧实现匹配不上 "body"，落进 else 的「数组当响应」分支，把请求正文读成响应规格
    （is_array=True、请求面空）——载荷方向整个反过来。
    """
    ep = _one("Body", '["C1", "C2"]')
    assert ep.body.kind == "json" and ep.body.example_obj == ["C1", "C2"]
    assert ep.response.is_array is False and ep.response.sample is None


def test_lowercase_chinese_request_still_request():
    """正对照（今天绿，不许改坏）：中文 `请求参数` 在 low 面照样命中。"""
    ep = _one("请求参数", '{"pt": "A1"}')
    assert ep.body.kind == "json" and ep.body.example_obj == {"pt": "A1"}


def test_lowercase_and_chinese_response_still_response():
    """正对照：小写 `response` / 中文 `返回结果` 形态行为逐字不变（改动只放大写面）。"""
    for preceding in ("response", "返回结果"):
        ep = _one(preceding, '{"resultCode": "0"}')
        assert ep.response.sample == {"resultCode": "0"}, preceding
        assert ep.response.has_result_code is True, preceding
        assert ep.body.kind == "none", preceding

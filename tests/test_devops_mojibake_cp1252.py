"""`repair_mojibake` 的 **cp1252 形态**守卫：Windows 乱码不许静默带着进 DevOps。

## 挡的是什么（2026-11-24 实测）

`devops_sync.repair_mojibake` 旧实现只认 `latin-1`
（`text.encode('latin-1').decode('utf-8')`）。但 Windows 侧把 UTF-8 字节按 **cp1252** 解出来的
形态里，0x80–0x9F 被映射到 `‹`(U+2039)/`•`(U+2022)/`”`(U+201D) 等 **>0xFF** 码位 ——
`latin-1` 编码**必然失败**，于是函数**原样返回乱码**：

```
"测试用例".encode("utf-8")                    → b'\xe6\xb5\x8b...'
  .decode("cp1252", errors="replace")         → 'æµ‹è¯•ç”¨ä¾‹'   ← 旧实现返回这个
```

后果与「静默丢弃」同族：不报错，只是**写进 DevOps 的标题/步骤名是乱码**（`parse_mcp_response`
修不好，`_content_text` 修不好，一路到用例库）。而 `latin-1` 形态一直是好的，所以本地/CI
测试全绿。

## 修法与安全边界

两级：`latin-1` 优先（原语义零变化）→ **编码失败才试 `cp1252`** → 都失败返回原文。
安全性靠「双向失败即原文」：合法西欧文本（`München`/`naïve`）两条路径都解不出 utf-8，
原样返回，不会被二次损坏。

## 五条

1. `test_cp1252_mojibake_is_repaired`：`æµ‹è¯•ç”¨ä¾‹` → `测试用例`（改前恒原文）；
2. `test_latin1_mojibake_unchanged`：原 `latin-1` 形态仍修（零涟漪）；
3. `test_correct_text_is_untouched`：正常中文 / emoji / 纯 ASCII 原样；
4. `test_western_text_not_double_damaged`：`München`/`naïve`/`ü` 原样（**正向对照**：
   防「为了修乱码把合法文本改坏」）；
5. `test_parse_mcp_response_repairs_cp1252`：链路级——cp1252 乱码经 `parse_mcp_response`
   出来是干净中文（证明修复真接在消费面上，不是只有单函数可用）。
"""
from __future__ import annotations

import json

from atlas import devops_sync as ds

ORIGINAL = "测试用例"
LATIN1_FORM = ORIGINAL.encode("utf-8").decode("latin-1")
CP1252_FORM = ORIGINAL.encode("utf-8").decode("cp1252", errors="replace")


def test_cp1252_mojibake_is_repaired():
    assert CP1252_FORM != ORIGINAL, "前提消失：cp1252 形态本该是乱码"
    assert ds.repair_mojibake(CP1252_FORM) == ORIGINAL, (
        f"cp1252 乱码未被修复（旧实现只认 latin-1，>0xFF 码位让它编码失败并原样返回）："
        f"{CP1252_FORM!r}")


def test_latin1_mojibake_unchanged():
    assert ds.repair_mojibake(LATIN1_FORM) == ORIGINAL


def test_correct_text_is_untouched():
    for text in ("测试用例", "同步成功", "🎉", "hello", "", "test_1"):
        assert ds.repair_mojibake(text) == text, f"{text!r} 被改动"


def test_western_text_not_double_damaged():
    """正向对照：合法西欧文本不得被「修复」改坏（cp1252 可编码但非合法 utf-8 → 原文）。"""
    for text in ("München", "naïve", "ü", "café", "Ångström"):
        assert ds.repair_mojibake(text) == text, f"{text!r} 被二次损坏"


def test_parse_mcp_response_repairs_cp1252():
    """链路级：修复必须接在消费面（`_content_text` → `parse_mcp_response`）。

    注意返回语义：`(is_error, text)`——第一个是**错误标志**，不是「成功」。
    2026-11-24 我第一版写成 `ok is True` 当场红，正因为它在这里叫 is_error。
    """
    payload = json.dumps({"result": {"isError": False,
                                     "content": [{"text": CP1252_FORM}]}},
                         ensure_ascii=False)
    is_error, text = ds.parse_mcp_response(payload)
    assert is_error is False, f"正常响应被读成错误：{text!r}"
    assert text == ORIGINAL, f"链路仍返回乱码：{text!r}"

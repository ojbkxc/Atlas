"""跨平台静态哨兵（design-plan §4 的 X1/X4 验收测，目录级递归扫描）。

设计口径（`design-review.md` §2 X4 + §6-D，约束性）：
- 扫描**目录级递归** src/ 与 scripts/，不按文件清单点名——后续波次新增的文件自动入网，
  本文件此后各波禁改（波间涟漪归各波主人「改代码保绿」）。
- X4 的判据是「试编 GBK」，不是字符黑名单：`→`/`█` 在 GBK 可编，属风格禁止项，
  **不得当红灯依据**（评审实测：src+scripts 的真崩点只有 import_jmeter 的 `⚠`，已改 WARN）。
- 终端字面量之外，**返回串也要测编码本身**（team-lead 收口项②，review.py 的 U+2194 先例：
  哨兵只扫 print 会放过「返回给 CLI 打印的字符串」这条路径）——C4 的输出面模块
  （credential_gate/failures/import_jmeter）里所有字符串常量一律试编 GBK。
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 虚构样本主机（.example）：净化样本必须有真实 URL 形态，集中一处常量而不是满屏字面量
CB = "https://sso.example/cb"
SRC_DIRS = (ROOT / "src", ROOT / "scripts")
# 返回串纪律适用的模块（C4 输出面）：这些文件里**任何**字符串常量都得能进 GBK 终端
STRICT_MODULES = frozenset({"src/atlas/credential_gate.py", "src/atlas/failures.py",
                            "scripts/import_jmeter.py"})
_BS = chr(92)
_DRIVE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
_SEGMENT = re.compile(r"[A-Za-z0-9_.\-]{2,}")


def _py_files():
    files = []
    for base in SRC_DIRS:
        files.extend(sorted(base.rglob("*.py")))
    assert files, "扫描目录为空=哨兵自己失效（scope_empty 直接红，不给静默假绿）"
    return files


def _string_constants(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def _terminal_call_literals(tree):
    """print(...) / *.stdout.write(...) / *.stderr.write(...) 里的字符串常量。"""
    hit_ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name != "print" and not name.endswith((".stdout.write", ".stderr.write")):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                hit_ids.add(id(sub))
    return hit_ids


def _looks_like_windows_path(value: str) -> bool:
    r"""只拦「反斜杠拼出来的路径」：盘符形态，或 ≥3 段且各段是路径安全字符。

    不拦正则/转义串——`\s`、`\d{4}` 这类模式串与 `type:\s*ignore` 都是既有合法形态，
    把它们当红灯就是误报（评审 X4 对「清单松」的裁决）。
    """
    if _BS not in value:
        return False
    if _DRIVE.search(value):
        return True
    segments = re.split(re.escape(_BS), value)
    if len(segments) < 3:
        return False
    return all(_SEGMENT.fullmatch(part) for part in segments[1:])


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_terminal_literals_are_gbk_encodable(path):
    """X4：GBK 控制台遇到非 GBK 字符直接 UnicodeEncodeError 崩掉命令（explore.py 真机先例）。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    targets = _terminal_call_literals(tree)
    for node in _string_constants(tree):
        if id(node) not in targets:
            continue
        try:
            node.value.encode("gbk")
        except UnicodeEncodeError as exc:
            bad = node.value[exc.start]
            pytest.fail(f"{path.relative_to(ROOT).as_posix()}:{node.lineno} 终端字面量含 "
                        f"U+{ord(bad):04X}（{bad!r}），GBK 控制台会崩——改 ASCII 标记（WARN/OK）")


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_hardcoded_windows_paths(path):
    """X1：新代码路径走 pathlib/POSIX 相对字符串，源码里不留反斜杠硬编码路径。"""
    source = path.read_text(encoding="utf-8")
    for node in _string_constants(ast.parse(source)):
        if _looks_like_windows_path(node.value):
            pytest.fail(f"{path.relative_to(ROOT).as_posix()}:{node.lineno} 字符串字面量像"
                        f" Windows 路径：{node.value[:60]!r}（用 pathlib 拼接）")


@pytest.mark.parametrize("rel", sorted(STRICT_MODULES))
def test_c4_output_modules_keep_every_string_literal_gbk_safe(rel):
    """收口项②：净化器/迁移器**返回**的字符串也会被打印——常量层就得整体可编 GBK。

    只看 print 字面量会漏掉「函数返回一个含 U+2194 的串再由别处 print」这条路径
    （review.py 刚踩过的先例）。这三个模块是 C4 的输出面，纪律收在最严处。
    """
    source = (ROOT / rel).read_text(encoding="utf-8")
    for node in _string_constants(ast.parse(source)):
        try:
            node.value.encode("gbk")
        except UnicodeEncodeError as exc:
            bad = node.value[exc.start]
            pytest.fail(f"{rel}:{node.lineno} 含非 GBK 字符 U+{ord(bad):04X}（{bad!r}）："
                        f"该模块的返回串会进终端，改 ASCII 或转义")


def test_sanitizer_outputs_survive_gbk_console():
    """行为面（不只静态）：净化后的文本必须能直接写进 GBK 控制台。"""
    from atlas import credential_gate as g
    samples = [
        f"回调失败 URL={CB}?ticket=ST-1&code=401",
        "Cookie: sid=abc; 中文业务文案 查询无结果",
        "${__P(password,0123456789abcdef0123456789abcdef01234567)}",
        f"顶栏指示器 .top-bar reload 2 次后仍未出现，URL={CB}#/sso",
    ]
    for text in samples:
        cleaned = g.sanitize_text(text)
        cleaned.encode("gbk")
        assert g.REDACTED in cleaned or text == cleaned, f"样本没被处理也没报错：{text}"

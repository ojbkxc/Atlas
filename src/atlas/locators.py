"""UI 定位描述的**单一解析点**：CSS 字符串与结构化描述（ARIA 优先）两种形态。

组件库 `components.json` 的 `selectors` 值允许两种形态：
- 字符串 = CSS（**存量形态，逐字节兼容**，老数据不改也能跑）
- 对象   = `{"role": "button", "name": "保存"}` / `{"label": ...}` / `{"testid": ...}` / `{"css": ...}`

为什么要有这一层：探索链路（`explore` / `autopilot`，gap12）产出的候选本来就是 ARIA
role+name，但组件库只收 CSS 字符串，导致「产得出、入不了库」——人必须把 role+name 手抄成
CSS 才能入库，抄错就变成第二份漂移知识。本模块让组件库**直接收结构化描述**，并把
「描述 → Playwright locator」收敛到 `resolve` 一处（#92 不留第二把尺）。

分工（谁在什么面说话）：
- `parse` 是**形态判据**：写路径（`knowledge.save_component_selector`）与批准路径
  （`review.approve_to_selector`）在落盘前调它——坏形态当场点名，脏数据进不了知识文件。
- `resolve` 是**执行判据**：`SelectorRegistry.resolve` 与 `autopilot._locator` 都委托它。
- `canonical` 是**留痕文本**：dict 直接切片会 TypeError（`selector[:120]` 的真实踩点），
  任何日志/决策留痕/证据卡片一律用它，不裸用原值。

role/label/testid/css 是 Playwright 通用概念，不是业务字面量——放本层不违反红线 #11；
具体是哪个角色/什么名字，仍只存在于业务插件的私有知识里。
"""
from __future__ import annotations

import json

# 允许的定位键（顺序即文档顺序）。`name` 不是独立定位键，只配 role。
KINDS = ("role", "label", "testid", "css")
# 非 css 形态 → Playwright 取用方法名（键集合由 KINDS 派生，不留第二份名单）
_ARIA_GETTERS = {"role": "get_by_role", "label": "get_by_label", "testid": "get_by_test_id"}


class LocatorSpecError(ValueError):
    """定位描述形态坏（未知键 / 多个定位键 / 空值 / 类型错）——点名，绝不猜。"""


def _text(value, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocatorSpecError(f"{where} 必须是非空字符串，实为 {value!r}")
    return value


def parse(spec) -> dict:
    """定位描述 → 规范形态 `{"kind": ..., ...}`；坏形态点名 `LocatorSpecError`。

    规范形态是**唯一**对外可依赖的中间表示：产物生成期按它固化，执行期按它取 locator。
    """
    if isinstance(spec, str):
        return {"kind": "css", "css": _text(spec, where="CSS 选择器")}
    if not isinstance(spec, dict):
        raise LocatorSpecError(
            f"定位描述必须是字符串（CSS）或映射（结构化），实为 {type(spec).__name__}")
    if not spec:
        raise LocatorSpecError(f"定位描述是空映射：{'/'.join(KINDS)} 至少要给一个")
    unknown = sorted(k for k in spec if k not in (*KINDS, "name"))
    if unknown:
        # 打错的键（先用写对了定位键，仍逐字点名，不静默忽略）
        raise LocatorSpecError(
            f"定位描述有未知键 {unknown}；只认 {list(KINDS)}（role 可带 name）")
    present = [k for k in KINDS if k in spec]
    if not present:
        raise LocatorSpecError(f"定位描述缺少定位键 {'/'.join(KINDS)} 之一")
    if len(present) > 1:
        # 同时给 role 与 css：谁优先是业务判断，本层不猜（猜错=执行时点到别的元素）
        raise LocatorSpecError(f"定位描述同时给了多个定位键 {present}：无法判定用哪个（不猜）")
    kind = present[0]
    if kind == "css":
        if "name" in spec:
            raise LocatorSpecError("css 形态不接受 name 键（name 只配 role）")
        return {"kind": "css", "css": _text(spec["css"], where="css 值")}
    if kind in ("label", "testid"):
        if "name" in spec:
            raise LocatorSpecError(f"{kind} 形态不接受 name 键")
        return {"kind": kind, kind: _text(spec[kind], where=f"{kind} 值")}
    out = {"kind": "role", "role": _text(spec["role"], where="role 值")}
    if "name" in spec:
        if spec["name"] is None:
            # 键在值 null 默认不生效（#77）：null 与「没写」两种意图不同，点名让人删键或填值
            raise LocatorSpecError("role 的 name 是 null：要么给非空串，要么删掉该键")
        out["name"] = _text(spec["name"], where="role 的 name")
    return out


def normalize(spec):
    """定位描述 → **落盘形态**（人可读、且能再 `parse` 回来）。

    与 `parse` 的差别只有一处、也必须只有一处：CSS（含手写的 `{"css": ...}`）归一成
    **字符串**（存量形态原样保留，产物侧走老的字符串分支），其余形态去掉内部 `kind` 标签，
    只留 `{"role": .., "name": ..}` / `{"label": ..}` / `{"testid": ..}`。

    为什么不能直接落 `parse` 的 IR：IR 带 `kind` 键，而 `kind` 不是合法定位键——
    存 IR 就等于存了一份**自己解析不了**的知识（回放时当场点名未知键，笔迹上像坏数据）。
    """
    parsed = parse(spec)
    if parsed["kind"] == "css":
        return parsed["css"]
    return {key: value for key, value in parsed.items() if key != "kind"}


def from_text(remedy: str = "", *, role: str = "", name: str = "") -> object:
    """CLI / 决策模板的**文本输入** → 定位描述（字符串或 dict）。

    - 给了 `--role`（非空）→ `{"role": .., "name": ..}`（`--role/--name` 是糖，落到同一判据）
    - 否则 `--remedy` 以 `{` 开头 → **必须**是合法 JSON 映射：写坏花括号不许被当成
      CSS 字面量静默入库（那会把「我以为在写结构化描述」变成一份永远定位不到的 CSS）
    - 其余 → 原样当作 CSS 字符串

    `--remedy` 与 `--role` 同时给 = 两种意图并存，不猜谁赢，点名。
    """
    if role and (remedy or "").strip():
        raise LocatorSpecError("--remedy 与 --role 不能同时给：选择器写一个位置就够了")
    if role:
        return {"role": role, **({"name": name} if name else {})}
    text = (remedy or "").strip()
    if not text.startswith("{"):
        return remedy
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LocatorSpecError(
            f"--remedy 以 '{{' 开头但不是合法 JSON（{exc.msg}）："
            f'要写结构化描述请给 --remedy \'{{"role":"button","name":"保存"}}\'') from exc
    return normalize(obj)   # 以 '{' 开头的合法 JSON 必然是对象（JSON 只有对象这一种形态）


def canonical(spec) -> str:
    """描述 → 可读单行文本（留痕/证据卡片/错误消息用）。

    存在的理由很具体：`selector[:120]` 这类旧写法一旦收到 dict 就 `TypeError`
    （dict 不可切片），批准会把留痕顶掉——凡是要落盘或打印的位置都走这里。
    """
    parsed = parse(spec)
    kind = parsed["kind"]
    if kind == "css":
        return f"css={parsed['css']}"
    if kind == "role":
        name = parsed.get("name")
        return f"role={parsed['role']}" + (f'[name="{name}"]' if name else "")
    return f"{kind}={parsed[kind]}"


def resolve(page, spec):
    """定位描述 → Playwright locator（**唯一**执行判据）。

    `name` 走 `exact=True`：候选的 name 取自 aria snapshot 的**无障碍名原文**，精确匹配才是
    「原样回放」；默认的子串匹配会让 `name="保存"` 同时命中「保存并提交」，在回归里点错元素
    比定位失败更坏（失败可见，点错静默）。
    """
    parsed = parse(spec)
    kind = parsed["kind"]
    if kind == "css":
        return page.locator(parsed["css"])
    if kind == "role":
        if "name" in parsed:
            return page.get_by_role(parsed["role"], name=parsed["name"], exact=True)
        return page.get_by_role(parsed["role"])
    getter = getattr(page, _ARIA_GETTERS[kind])
    return getter(parsed[kind])

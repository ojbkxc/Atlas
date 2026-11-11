"""gap11 #17：pytest 插件——旧套件运行期注入垫片（只在 `atlas legacy --fast` 的 python 进程内）。

自包含纪律（emit-impl 会把本文件与 legacy_fast.py **按文件内容 vendor** 进产物，
单一来源=真文件，禁止在 emit 层手抄第二份逻辑，G11-4）：
- **不 import atlas 包**：legacy_fast 用同目录绝对路径 standalone 加载——vendoring 后
  两文件仍相邻，同一条路成立；本文件顶层只用标准库。
- playwright/allure 都是**可选**依赖：缺席或打补丁失败一律 WARN 一行、用例照常跑
  （垫片是加速器不是准入闸；「注入坏=整场崩」会白烧登录会话，#103 同族）。

三个开关，默认全关（env 未开时**零行为变化**，判据测⑧钉死）：
- `ATLAS_FAST_WAITS`：1/on/true→mask 档，或档位名直选（`mask` / `mask+xhr` /
  `mask+xhr+mutation`；`xhr`、`mutation` 是同档简写——**mask 恒含、后两档递加**，
  设计 §A 判据 2）。配套两个只在闸开时才求值的旋钮：
  * `ATLAS_FAST_MIN_MS`（缺省 400）——有界下限，reload 后探针误判静默的兜底之一；
  * `ATLAS_FAST_SETTLE_ANCHOR`（缺省空）——应用就绪锚点选择器，只来自 env（红线 #11）。
- `ATLAS_FAST_NO_STEP_SHOTS`：包装 `allure.attach`，**只丢**名字位（kwargs["name"] 或
  第二个位置参数——`attach(body, name, type, ext)` 的签名位）恰为 `step<N>_result` 的
  附件；`_FAIL` 与任何其它名字原样透传。**正文长得不像名字一律不猜**（旧实现按「第一个
  字符串参数」判会把 `attach("step1_result")` 这种正文是字符串的调用误丢=丢证据）。
  旧 runner 的失败落盘 `_save_fail_screenshot` 走 `page.screenshot()` 字节、不经
  attach——天然不受影响（本插件从不碰 screenshot，测里钉这条边界）。

统计：`ATLAS_FAST_WAITS_STATS` 指定 jsonl 路径（每调用一行 caller/requested/actual/
probe/fallback——「省了多少」的唯一证据源，设计 E 度量 3）；未设置则统计只在进程内，
收尾按设计 §A 的聚合形态打一行汇总（calls/original_ms/actual_ms/saved/fallbacks/
no_busy_observed + floor/anchor 生效值——A/B 复核要看得见兜底层开着没开，也要分得清
「探针真出错」与「压根没忙碌信号因而睡满」这两件不同的事，见 legacy_fast 头注判据 2）。
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

_STEP_RESULT = re.compile(r"^step\d+_result$")

_SHIM = None
_STATE = {"waits_installed": False, "shots_installed": False,
          "probe": "", "min_ms": None, "anchor": ""}
_SHOTS = {"dropped": 0, "passed": 0}
_ORIG_ATTACH = None  # (module, original, wrapper)


def _load_shim():
    """standalone 加载同目录 legacy_fast.py（不经 atlas 包；重复调用幂等）。"""
    name = "_atlas_legacy_fast_standalone"
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    here = Path(__file__).resolve().parent
    path = here / "legacy_fast.py"
    if not path.is_file():
        raise FileNotFoundError(f"垫片源码缺失：{path}（vendor 时须与 fastplugin 相邻）")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)  # 半成品不留全局名
        raise
    return mod


def _env_on(key: str) -> bool:
    """三态 env：空/0/off/false→False 静默；1/on/true→True；其它非空→WARN + False。

    手滑值（`yes`/`ture`）静默不装 = 「以为快了其实没快」的假阴性同族（lead 追加 4，
    与 `waits_gate` 未知值不启用同一口径）——在场闸的反面也是闸。
    """
    raw = os.environ.get(key, "").strip().lower()
    if raw in ("", "0", "off", "false"):
        return False
    if raw in ("1", "on", "true"):
        return True
    print(f"WARN: {key}={raw!r} 不是 {{1,on,true}}，按关闭处理"
          "（疑似拼写不静默当成已开启——漏装比装坏更难发现）")
    return False


# ---------------------------------------------------------------------------
# 等待垫片（类级 Page 补丁）
# ---------------------------------------------------------------------------


def _install_waits() -> None:
    global _SHIM
    shim = _load_shim()
    _SHIM = shim
    gate = shim.waits_gate()
    if not gate:
        return  # 闸关：min_ms/anchor 连求值都不求值（零行为变化的强形态）
    from playwright.sync_api import Page  # 可选依赖：缺席由调用方 WARN
    stats = shim.FastWaitStats()
    min_ms = shim.min_ms_gate()
    anchor = shim.anchor_gate()
    shim.install_fast_waits(Page, probe=gate, min_ms=min_ms, anchor=anchor,
                            stats=stats)
    _STATE["waits_installed"] = True
    _STATE["stats"] = stats
    _STATE["probe"] = gate
    _STATE["min_ms"] = min_ms
    _STATE["anchor"] = anchor


def _uninstall_waits() -> None:
    if not _STATE["waits_installed"] or _SHIM is None:
        return
    try:
        from playwright.sync_api import Page
        _SHIM.uninstall(Page)
    except Exception as exc:
        print(f"WARN: fast waits 还原失败（进程即将退出，影响限于本次会话）: "
              f"{type(exc).__name__}")
    _STATE["waits_installed"] = False
    stats = _STATE.get("stats")
    if stats is None:
        return
    out = os.environ.get("ATLAS_FAST_WAITS_STATS", "").strip()
    if out:
        n = stats.to_jsonl(out)
        print(f"fast waits stats: {n} calls -> {out}")
    agg = stats.aggregate()
    print(f"fast waits summary: calls={agg['calls']} "
          f"original_ms={agg['original_ms_total']} actual_ms={agg['actual_ms_total']} "
          f"saved_ms={agg['original_ms_total'] - agg['actual_ms_total']} "
          f"fallbacks={agg['fallbacks']} no_busy_observed={agg['no_busy_observed']} "
          f"floor_ms={_STATE.get('min_ms')} "
          f"anchor={_STATE.get('anchor') or '-'} "
          f"tiers={sorted(agg['by_stage'])}（0 节省不是失败；actual≤original 是硬不变量。"
          "fallbacks=**真回退**（探针错误/无 evaluate/档位降级），"
          "no_busy_observed=**没见过忙碌信号因而睡满**（fail-safe，不是故障）——"
          "两个数各答各的问题，相加不是失败率。floor/anchor 见 legacy_fast 头注）")


# ---------------------------------------------------------------------------
# 成功截图抑制（allure.attach 包装）
# ---------------------------------------------------------------------------


def _attach_name(args, kwargs):
    """attach 的**名字位**（lead 追加 2）：`allure.attach(body, name=None, type, ext)`
    的签名位=第二个位置参数，或 kwargs["name"]。

    旧实现取「第一个字符串参数」——`allure.attach("step1_result")`（正文是字符串、没传
    name）会被误丢=丢证据。名字只有可能出现在 name 位，别处一律视为正文透传。
    """
    name = kwargs.get("name")
    if isinstance(name, str):
        return name
    if len(args) > 1 and isinstance(args[1], str):
        return args[1]
    return None


def _attach_wrapper(original):
    def patched(*args, **kwargs):
        name = _attach_name(args, kwargs)
        if name is not None and _STEP_RESULT.match(name):
            _SHOTS["dropped"] += 1
            return None  # 只丢 step<N>_result；bytes 正文不读不回显
        _SHOTS["passed"] += 1
        return original(*args, **kwargs)
    return patched


def _install_shots() -> None:
    global _ORIG_ATTACH
    if not _env_on("ATLAS_FAST_NO_STEP_SHOTS"):
        return
    import allure  # 可选依赖
    original = allure.attach
    wrapper = _attach_wrapper(original)
    allure.attach = wrapper
    _ORIG_ATTACH = (allure, original, wrapper)
    _STATE["shots_installed"] = True


def _uninstall_shots() -> None:
    if _ORIG_ATTACH is not None:
        module, original, wrapper = _ORIG_ATTACH
        current = getattr(module, "attach", None)
        if current is wrapper:
            module.attach = original          # 我们的补丁在最外层：正常还原
        elif current is original:
            pass                              # 已被他处还原：不重复动
        else:
            print("WARN: allure.attach 在我们之后又被改写，fastplugin 不覆盖其补丁")
    _STATE["shots_installed"] = False
    if _SHOTS["dropped"] or _SHOTS["passed"]:
        print(f"fast shots summary: dropped={_SHOTS['dropped']} "
              f"passed={_SHOTS['passed']}（_FAIL 与其它附件全在 passed 里；"
              "失败截图走 page.screenshot 字节面，不经此闸）")


# ---------------------------------------------------------------------------
# pytest 钩子
# ---------------------------------------------------------------------------


def pytest_configure(config):
    if os.environ.get("ATLAS_FAST_WAITS", "").strip():
        try:
            _install_waits()
        except Exception as exc:
            print(f"WARN: fast waits 注入失败，旧用例按原固定睡眠跑: "
                  f"{type(exc).__name__}: {exc}")
    if _env_on("ATLAS_FAST_NO_STEP_SHOTS"):
        try:
            _install_shots()
        except Exception as exc:
            print(f"WARN: 截图抑制注入失败（allure 缺席/改写？），全部附件照留: "
                  f"{type(exc).__name__}")


def pytest_unconfigure(config):
    _uninstall_waits()
    _uninstall_shots()

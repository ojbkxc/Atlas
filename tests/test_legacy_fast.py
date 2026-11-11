"""gap11 #17：legacy_fast 垫片 + fastplugin 离线判据测；#33 按 lead P0 裁定收紧提前判据。

零网络零浏览器：假时钟 + 「JS 判据语义模型」页（P0 之后返回**状态码**）+ 假 playwright/
假 allure。覆盖设计 `docs/gaps/gap11-legacy-fast-compat.md` §D.1 的离线格与 lead G11-1
三条修法：

| 测 | 守的路径 |
|---|---|
| test_p0_never_busy_* / test_p0_mask_appears_then_vanishes / test_p0_busy_observation_is_per_call_not_sticky / test_p0_protocol_shape_verdicts_are_never_early | **#33 本体**：提前资格=本笔调用内观测到「忙→静」；从未见忙→睡满 + `no-busy-observed`；观测按调用不按页面；协议外形态 fail-closed |
| test_p0_never_busy_sleeps_out_requested_all_three_tiers | 三档各一对（从未见忙→睡满 / 该档忙过→提前），档位简写不许留一个空档 |
| test_p0_early_exit_is_guarded_by_busy_seen_ast | 结构防线：提前那条 return 必须在 busy_seen 守卫分支内（将来删条件只留 quiet 必红） |
| test_p0_two_counters_are_semantic_not_arithmetic | lead 追加：`fallbacks`（真回退）与 `no_busy_observed`（fail-safe）语义分立、可并存、不进 by_stage 键 |
| test_d1_1_* 四态 | §A 硬判据 1「绝不更长」+ 3「异常三分」（`_run` 回退 / `_settle_probe` 传播 / 不可恢复睡满再抛）；判据 2 的「提前」格改写到上面 p0 组 |
| test_d1_9_* | G11-1 修法 1/2/3 仍在位（readyState/锚点是**门**不是忙碌观测；min_ms 下限不动） |
| test_d1_2_* | §A 三档 probe（**mask 恒含、xhr/mutation 递加**）+ hook 未装上降 mask + WARN（缺陷 2 的防线）；no-hook/no-activity **不计入**忙碌观测 |
| test_d1_3_mask_probe_judges_all_masks | 多遮罩教训的 JS 构成面（真两遮罩场景属 #20 真机 R-G3，见该测诚实边界） |
| test_d1_4_* | §A 硬判据 4：轮询走进程侧 `time.sleep`，垫片内不出现 `wait_for_timeout`/`wait_for_function`（递归禁令） |
| test_d1_7_* | stats 不变量 `actual ≤ requested` 恒真（property 表，含提前与睡满两条出口）+ 聚合形态 + 落盘尽力而为 |
| test_d1_8_* | 垫片只换 `wait_for_timeout` 一法，其余方法面不动 |
| test_env_* / test_plugin_* | 派单判据⑧「env 未开=零行为变化」+ 类级 Page 补丁 + 截图抑制三证据 + 注入失败不崩 |
| test_vendor_* | G11-4 vendoring（emit-impl 按真文件字节 vendor，旧仓运行时不 import atlas） |

诚实边界：假页按条件文本里在场的那几段**推导状态码**，钉的是**档位构成 + 忙碌观测 +
预算算术**，不是 JS 本身；真实 JS 求值（含两遮罩并存、hook 跨导航存活）由 #20 真机 A/B
兜。每条负向断言同测带正向对照。被 P0 改写判定的旧格（原 `test_d1_1_quiet_page_returns_early`、
`test_d1_1_last_partial_tick_still_probes_quiet_page_returns_zero` 等）在各自 docstring 里
标了「旧语义不安全（A/B 实测失败点前移）」，不留两个版本并存。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import sys
import types
from pathlib import Path

import pytest

from atlas import fastplugin, legacy_fast, waits


SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "atlas"


# ---------------------------------------------------------------------------
# 假时钟与「JS 判据语义模型」页
# ---------------------------------------------------------------------------


class FakeClock:
    """进程侧虚拟时钟：设计 D.1.1 要求「假时钟测钉死不多不少」的注入缝。

    `_RealClock` 的孪生面（同样只暴露 now_ms/sleep_ms），垫片不感知真假。
    """

    def __init__(self):
        self.now = 0.0
        self.slept: list = []

    def now_ms(self) -> float:
        return self.now

    def sleep_ms(self, ms: float) -> None:
        if ms > 0:
            self.now += float(ms)
            self.slept.append(float(ms))

    def advance(self, ms: float) -> None:      # 页侧「真实耗时」由假页驱动
        self.now += float(ms)


TRANSIENT_EXC = RuntimeError(
    "Execution context was destroyed, most likely because of a navigation")
UNRECOVERABLE_EXC = RuntimeError("Target page, context or browser has been closed")
UNKNOWN_EXC = RuntimeError("boom: page.evaluate returned a non-serializable value")


class ModelPage:
    """静默条件的语义模型（不执行 JS，见模块 docstring 的诚实边界）。

    state 要素 = JS 判据的各段：ready / masks_visible / pending / mutated /
    mutated_ago / anchor_visible。cost_ms=每次 evaluate 的页侧耗时（驱动假时钟，让
    「回退剩余预算」的算术可核）。raise_seq 逐次注入求值异常（None 占位=该次正常）。

    **P0 之后本模型返回状态码而不是布尔**（`busy-*` / `quiet` / 门码）：布尔表达不了
    「曾忙碌」，而「先忙后静」正是提前返回的唯一准入——返回值形态跟着被测判据走，
    否则测的是我对旧协议的想象，不是垫片真正读到的东西。
    seq=逐拍覆盖 state 的脚本（第 N 拍用 `seq[N]`，**超出后回 base state**）——
    跨拍转换（遮罩出现→消失）只能这么表达；垫片每次 settle 把 busy_seen 清零，
    页侧脚本也得同域重置，所以用 `arm()` 显式上膛而不是自动循环。
    """

    def __init__(self, *, state=None, clock=None, cost_ms=0, hooked=True,
                 raise_seq=None, init_exc=None, seq=None):
        self.state = {"ready": True, "masks_visible": False, "pending": 0,
                      "mutated": False, "mutated_ago": 9_999,
                      "anchor_visible": True}
        if state:
            self.state.update(state)
        self.clock = clock
        self.cost_ms = cost_ms
        self.hooked = hooked
        self.raise_seq = list(raise_seq or [])
        self.init_exc = init_exc
        self.orig_calls: list = []           # 原始 wait_for_timeout（真实要花 ms）
        self.conditions: list = []           # 求值过的静默条件
        self.hook_calls: list = []
        self.init_calls: list = []
        self.wff_calls: list = []            # 判据 4：必须恒空
        self.arm(seq)

    def arm(self, seq=None):
        """给下一笔等待上膛：第 N 拍套 `seq[N]`，**拍号用尽后回 base state**。

        同时把拍号归零——「第几拍」属于本次等待：垫片每笔 settle 重置 busy_seen，
        页侧脚本必须同域重置，否则第二笔继承第一笔的尾部，测的就不是同一条规则了。
        """
        self._seq = [dict(item) for item in (seq or [])]
        self._poll = 0

    def _poll_state(self) -> dict:
        """拍号=第几次**静默求值**（抛异常的拍不消耗脚本：那一拍页没答话，没什么可表达）。"""
        st = dict(self.state)
        if self._poll < len(self._seq):
            st.update(self._seq[self._poll])
        self._poll += 1
        return st

    def wait_for_timeout(self, ms=0):
        self.orig_calls.append(ms)
        if self.clock is not None:
            try:
                self.clock.advance(max(0, int(ms)))
            except (TypeError, ValueError):
                pass

    def wait_for_function(self, condition, timeout=None, polling=None):
        self.wff_calls.append({"condition": condition, "timeout": timeout,
                               "polling": polling})
        raise AssertionError("垫片不得用 wait_for_function 轮询（设计 §A 判据 4）")

    def add_init_script(self, js):
        if self.init_exc is not None:
            raise self.init_exc
        self.init_calls.append(js)

    def evaluate(self, js):
        if self.clock is not None:
            self.clock.advance(self.cost_ms)
        if "XMLHttpRequest.prototype" in js:
            self.hook_calls.append("xhr")
            return None
        if "MutationObserver" in js:
            self.hook_calls.append("mutation")
            return None
        if js in (legacy_fast._JS_XHR_PRESENT, legacy_fast._JS_MUTATION_PRESENT):
            return self.hooked
        self.conditions.append(js)
        if self.raise_seq:
            exc = self.raise_seq.pop(0)
            if exc is not None:
                raise exc
        return self._judge(js)

    # -- 判据计算（按文本里在场的那几段逐段短路，与 build_condition 同构）------
    def _judge(self, js: str) -> str:
        st = self._poll_state()
        # 忙碌段在前（与 build_condition 的次序一致）：门不得抢在「此刻仍忙」前面作答
        if ".el-loading-mask" in js and st["masks_visible"]:
            return legacy_fast._CODE_BUSY_MASK
        if "__atlasFastXhr" in js:
            if not self.hooked:
                return legacy_fast._CODE_NO_HOOK        # 不可评估 ≠ 忙碌，也 ≠ 静默
            if st["pending"] > 0:
                return legacy_fast._CODE_BUSY_XHR
        if "__atlasFastMutated" in js:
            if not (self.hooked and st["mutated"]):
                return legacy_fast._CODE_NO_ACTIVITY
            m = re.search(r"\(performance\.now\(\) - window\.__atlasFastLast\) "
                          r">= (\d+)", js)
            if m and st["mutated_ago"] < int(m.group(1)):
                return legacy_fast._CODE_BUSY_MUTATION
        if "document.readyState" in js and not st["ready"]:
            return legacy_fast._CODE_NOT_READY
        anchor = re.search(r'querySelectorAll\(("(?:[^"\\]|\\.)*")\)', js)
        if anchor and not st["anchor_visible"]:
            return legacy_fast._CODE_NOT_ANCHORED
        return legacy_fast._CODE_QUIET

    @property
    def anchor_asked(self):
        """最近一次条件里被要求的锚点选择器（json 字面量解回 str），无则 None。"""
        if not self.conditions:
            return None
        m = re.search(r'querySelectorAll\(("(?:[^"\\]|\\.)*")\)',
                      self.conditions[-1])
        return json.loads(m.group(1)) if m else None


class CannedPage:
    """直接吐**状态码序列**的页：把三态协议本身（而非 state 组合）交给测控。

    存在理由：`ModelPage` 按判据文本推导码，能测「档位构成」，但测不到协议边上的
    怪形态——`no-hook` 反复出现（hook 装好后中途丢）、旧布尔 `True`（外部 fake 的
    形状）、非字符串返回值。这些形态各自对应一条 fail-closed 规则，只能直喂。
    拍号用尽后停在最后一项（与 ModelPage.arm 同一口径）。
    """

    def __init__(self, codes, *, clock=None, hooked=True):
        self.codes = [c for c in codes]
        self.clock = clock
        self.hooked = hooked
        self.polls = 0
        self.orig_calls: list = []
        self.seen_conditions: list = []

    def wait_for_timeout(self, ms=0):
        self.orig_calls.append(ms)
        if self.clock is not None:
            self.clock.advance(max(0, int(ms)))

    def evaluate(self, js):
        if "XMLHttpRequest.prototype" in js or "MutationObserver" in js:
            return None
        if js in (legacy_fast._JS_XHR_PRESENT, legacy_fast._JS_MUTATION_PRESENT):
            return self.hooked
        self.polls += 1
        self.seen_conditions.append(js)
        idx = min(self.polls - 1, len(self.codes) - 1)
        return self.codes[idx]


class NoEvaluatePage:
    """老驱动/怪对象：没有 evaluate——垫片无从求值判据。"""

    def __init__(self, clock=None):
        self.clock = clock
        self.orig_calls: list = []

    def wait_for_timeout(self, ms=0):
        self.orig_calls.append(ms)
        if self.clock is not None:
            self.clock.advance(max(0, int(ms)))       # 回退睡的是真时间，账要如实推进


@pytest.fixture(autouse=True)
def _shim_state_isolation():
    """垫片全局登记册跨测隔离：两个模块拷贝（import 的 + plugin standalone 的）都清。

    泄漏形态很具体：上一测装了类级补丁没收尾，下一测看到「已安装」跳过捕获原始——
    还原扑空、旧仓进程带着补丁退场。
    """
    for mod in _all_shim_modules():
        mod._INSTALLED.clear()
    snap = (dict(fastplugin._STATE), dict(fastplugin._SHOTS),
            fastplugin._ORIG_ATTACH, fastplugin._SHIM)
    fastplugin._STATE.clear()
    fastplugin._STATE.update({"waits_installed": False, "shots_installed": False,
                             "probe": "", "min_ms": None, "anchor": ""})
    fastplugin._SHOTS.clear()
    fastplugin._SHOTS.update({"dropped": 0, "passed": 0})
    fastplugin._ORIG_ATTACH = None
    fastplugin._SHIM = None
    yield
    for mod in _all_shim_modules():
        mod._INSTALLED.clear()
    fastplugin._STATE.clear()
    fastplugin._STATE.update(snap[0])
    fastplugin._SHOTS.clear()
    fastplugin._SHOTS.update(snap[1])
    fastplugin._ORIG_ATTACH = snap[2]
    fastplugin._SHIM = snap[3]


def _all_shim_modules():
    mods = [legacy_fast]
    standalone = sys.modules.get("_atlas_legacy_fast_standalone")
    if standalone is not None:
        mods.append(standalone)
    return mods


def _install(page, *, clock, **kw):
    """install 的测试壳：假时钟默认注入，避免判据测真睡。"""
    kw.setdefault("min_ms", 0)
    return legacy_fast.install_fast_waits(page, clock=clock, **kw)


# ---------------------------------------------------------------------------
# D.1.1 假时钟四态（§A 硬判据 1/2/3）
# ---------------------------------------------------------------------------


def test_p0_never_busy_page_sleeps_out_requested_original_never_called():
    """P0 主格（**替换**旧 `test_d1_1_quiet_page_returns_early_*`）：即静默页不再提前。

    旧语义不安全（A/B 实测失败点前移）：r1/r2 里 `page.py:237 req=6000→act=400`、
    `login.py:484 req=3000→act=400` 就是这么放行的——语言切换/OU 切换那类等待**压根不带
    遮罩**，首拍即「静默」，旧测把这当成成绩（actual==0、saved==5000）。
    现在的答案：从未观测到忙碌信号 → 与旧行为逐字等价地睡满 requested，原始睡眠**也不调**
    （睡满用的是进程侧 tick，不是把等待再交回 `wait_for_timeout` 叠一层——那是判据 1 的反面）。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk)                       # 一切判据从第一拍就静默、且从没忙过
    stats = _install(page, clock=clk)
    page.wait_for_timeout(5000)
    assert page.orig_calls == []                      # 不叠原睡=绝不更长（判据 1）
    assert clk.now == 5000                            # 睡满，一毫秒都不少
    entry = stats.calls[0]
    assert entry["requested_ms"] == entry["actual_ms"] == 5000
    assert entry["fallback"] == "no-busy-observed"    # fail-safe 面，不是回退
    assert entry["probe"] == "mask"
    assert stats.saved_ms == 0                        # 没插手=没省，账如实
    assert stats.fallback_count == 0                  # 探针一次没错（lead 追加的口径）
    assert stats.no_busy_count == 1
    # 正向对照（同页同预算，先忙一拍再静默）：提前确实做得到，上面的「睡满」不是恒真
    clk2 = FakeClock()
    ok = ModelPage(clock=clk2, seq=[{"masks_visible": True}])
    stats_ok = _install(ok, clock=clk2)
    ok.wait_for_timeout(5000)
    assert stats_ok.calls[0]["actual_ms"] == legacy_fast._TICK_MS   # 一拍见忙、二拍落定
    assert stats_ok.calls[0]["fallback"] == ""
    assert stats_ok.saved_ms == 5000 - legacy_fast._TICK_MS


def test_p0_mask_appears_then_vanishes_returns_early_never_appears_full():
    """lead 验收格 3：遮罩档「出现→消失」=唯一合法的提前证据；从未出现=睡满。

    两腿必须同测（缺一条另一条就是恒真）：
    - 见过可见 `.el-loading-mask` ≥1 拍，其后遮罩落定 → 提前（下限 0）
    - 全程零遮罩（页面一直「安静」）→ 睡满，且 note=no-busy-observed
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, seq=[{"masks_visible": True},
                                     {"masks_visible": False}])
    stats = _install(page, clock=clk)
    page.wait_for_timeout(3000)
    assert len(page.conditions) == 2                  # 忙一拍 + 静默一拍
    assert stats.calls[0]["actual_ms"] == legacy_fast._TICK_MS
    assert stats.calls[0]["fallback"] == ""
    assert page.orig_calls == []

    clk2 = FakeClock()
    quiet = ModelPage(clock=clk2, state={"masks_visible": False})
    stats2 = _install(quiet, clock=clk2)
    quiet.wait_for_timeout(3000)
    assert clk2.now == 3000 == stats2.calls[0]["actual_ms"]
    assert stats2.calls[0]["fallback"] == "no-busy-observed"


def test_p0_busy_observation_is_per_call_not_sticky():
    """忙碌观测**按调用记**（进程侧局部量），不是页面粘滞属性——这条决定 P0 会不会复发。

    场景就是 A/B 的现场：同一 page 上先有一次「被遮罩挡着」的等待（拿到资格），紧接着
    一次「没有遮罩可等」的语言切换等待。若 busy_seen 留在 window 上粘滞（第二笔继承），
    第二笔又会 400ms 放行=失败点前移原地复活。页侧粘滞还有第二种漏法：导航会清 window
    状态，于是「跨文档」与「同文档两笔」行为不一致——所以观测归调用，天然每笔重置。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk)
    stats = _install(page, clock=clk)
    page.arm([{"masks_visible": True}])               # 第一笔：见忙→落定
    page.wait_for_timeout(2000)
    first = stats.calls[0]
    assert first["fallback"] == ""                    # 拿到了提前资格
    assert first["actual_ms"] == legacy_fast._TICK_MS
    spent_after_first = clk.now

    page.arm([])                                      # 第二笔：从此再没有忙碌信号
    page.wait_for_timeout(2000)
    second = stats.calls[1]
    assert second["actual_ms"] == 2000, "上一笔见过的遮罩不许给这一笔放行"
    assert second["fallback"] == "no-busy-observed"
    assert clk.now == spent_after_first + 2000        # 睡满，不多不少
    assert stats.saved_ms == 2000 - legacy_fast._TICK_MS   # 只有第一笔省了


def test_d1_1_never_quiet_returns_exactly_requested_not_longer():
    """态2 永不静默：预算制=**恰 ms 返回**（不多不少），且绝不叠原睡眠（缺陷 1 的另一半）。

    「不多」= 判据 1；「不少」= 不能比原等待更短地谎报（否则等于把没等到的页面放行）。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, state={"masks_visible": True})
    stats = _install(page, clock=clk)
    page.wait_for_timeout(5000)
    assert page.orig_calls == []                       # 不叠加=绝不更长
    assert clk.now == 5000                             # 恰 ms，一毫秒不多
    # 轮询确实在跑（每拍 tick），末拍剩不足一拍时睡满不再探（判据 1 的越顶防线）
    polls = len(page.conditions)
    assert 5000 // legacy_fast._TICK_MS - 2 <= polls <= 5000 // legacy_fast._TICK_MS
    entry = stats.calls[0]
    assert entry["actual_ms"] == entry["requested_ms"] == 5000
    assert entry["fallback"] == ""                     # 窗口耗尽≠回退


def test_d1_1_probe_error_falls_back_to_remaining_budget_only():
    """态3 未知异常：回退**原始**等待的**剩余量**（缺陷 1 的修法），总时长恰=req。

    场景（lead 原话口径）：求值花掉 200ms 后抛非 TimeoutError → 只许再睡 5000-200；
    stats 的 actual_ms 记**真实总耗时**，不许再写假账 req-0。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, cost_ms=200, raise_seq=[UNKNOWN_EXC])
    stats = _install(page, clock=clk)
    page.wait_for_timeout(5000)
    assert page.orig_calls == [4800]                   # 剩余量，不是全额
    assert clk.now == 5000                             # 200 已花 + 4800 回退 = 恰 req
    entry = stats.calls[0]
    assert entry["actual_ms"] == 5000 == entry["requested_ms"]
    assert entry["fallback"] == "probe-error:RuntimeError"
    assert stats.saved_ms == 0                         # 回退那一笔没省，如实
    # 正向对照：同档位「先忙后静」时走早退路、原始零调用、note 空
    # （P0 后必须 arm 出忙碌拍——静默页本身不再构成提前理由，见 test_p0_never_busy_*）
    clk2 = FakeClock()
    ok = ModelPage(clock=clk2, seq=[{"masks_visible": True}])
    stats_ok = _install(ok, clock=clk2)
    ok.wait_for_timeout(5000)
    assert ok.orig_calls == [] and stats_ok.calls[0]["fallback"] == ""
    assert stats_ok.saved_ms == 5000 - legacy_fast._TICK_MS


def test_d1_1_transient_error_keeps_waiting_inside_budget():
    """异常三分·瞬态：导航中（上下文销毁）=「尚未静默」，预算内继续等，**不回退不抛**。

    P0 顺带钉的一条：**瞬态不计入忙碌观测**——「我不知道页面在干什么」≠「我看到它忙过」，
    拿异常当忙碌证据就等于用未知换提前资格（与 xhr `no-hook` 同一族 fail-closed）。
    所以这里比旧语义多花一拍才拿到「先忙后静」的资格。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, raise_seq=[TRANSIENT_EXC, TRANSIENT_EXC],
                     seq=[{"masks_visible": True}])
    stats = _install(page, clock=clk)
    page.wait_for_timeout(1000)
    assert page.orig_calls == []                       # 没走回退路径
    assert clk.now == 3 * legacy_fast._TICK_MS         # 两拍瞬态 + 一拍见忙，第三拍落定
    entry = stats.calls[0]
    assert entry["fallback"] == ""                     # 见过忙碌→静默，正常提前（非回退）
    assert entry["actual_ms"] == clk.now == 75
    # 负向对照：同样两拍瞬态、但**从未**见过忙碌 → 睡满，绝不因异常拍拿到资格
    clk2 = FakeClock()
    never = ModelPage(clock=clk2, raise_seq=[TRANSIENT_EXC, None])
    stats2 = _install(never, clock=clk2)
    never.wait_for_timeout(200)
    assert stats2.calls[0]["actual_ms"] == 200
    assert stats2.calls[0]["fallback"] == "no-busy-observed"


def test_d1_1_unrecoverable_sleeps_out_budget_then_propagates():
    """异常三分·不可恢复：Target closed → 按预算睡完再把**原语义抛出**（不吞不改）。

    吞掉=把「会话已死」伪装成「等待成功」，真因漂到远处某步（#102/#133 同族）。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, cost_ms=100, raise_seq=[UNRECOVERABLE_EXC])
    stats = _install(page, clock=clk)
    with pytest.raises(RuntimeError) as got:
        page.wait_for_timeout(3000)
    assert "Target page" in str(got.value)             # 原异常原样传播
    assert page.orig_calls == []                       # 睡预算用进程侧 sleep，不调原始
    assert clk.now == 3000                             # 睡满=不多不少
    entry = stats.calls[0]
    assert entry["fallback"] == "unrecoverable:RuntimeError"
    assert entry["actual_ms"] == entry["requested_ms"] == 3000


def test_d1_1_control_flow_error_propagates_immediately():
    """控制流（KeyboardInterrupt）不被垫片的任何一层吞掉，也不补睡预算（#114/#108）。"""
    clk = FakeClock()
    page = ModelPage(clock=clk, raise_seq=[KeyboardInterrupt()])
    _install(page, clock=clk)
    with pytest.raises(KeyboardInterrupt):
        page.wait_for_timeout(5000)
    assert clk.now == 0                                # 未被「按预算睡完」偷走中断
    assert page.orig_calls == []


def test_d1_1_assertion_error_is_never_downgraded():
    """lead 追加 1：AssertionError 是 Exception 子类，但**永不被当探针错误回退**。

    回退=把「用例失败」吞成「继续跑」（红线 #7 的入口形态）。与 engine `_perform` 只捕
    (NotImplementedError, RuntimeError) 同族：判据在 except 分支的**次序**里，不在注释里。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, raise_seq=[AssertionError("step 7 结果不符")])
    stats = _install(page, clock=clk)
    with pytest.raises(AssertionError):
        page.wait_for_timeout(5000)
    assert page.orig_calls == []                       # 没有回退=没有把失败洗成等待
    assert stats.calls == []                           # 也不给它记一笔「等过了」的假账
    # 正向对照：同位置换普通异常，确实走回退（证明上条的「不回退」不是恒真）
    clk2 = FakeClock()
    page2 = ModelPage(clock=clk2, raise_seq=[UNKNOWN_EXC])
    stats2 = _install(page2, clock=clk2)
    page2.wait_for_timeout(5000)
    assert page2.orig_calls == [5000] and stats2.calls[0]["fallback"]


def test_d1_1_no_evaluate_falls_back_full_without_crash():
    """态4 前置：page 没有 evaluate 能力 → 回退原始全额、不崩、stats 点名原因。"""
    clk = FakeClock()
    page = NoEvaluatePage(clock=clk)
    stats = legacy_fast.install_fast_waits(page, clock=clk, min_ms=0)
    page.wait_for_timeout(800)                         # 不抛
    assert page.orig_calls == [800]                    # 一分钱时间都没花过→全额
    assert stats.calls[0]["fallback"] == "no evaluate"
    assert stats.calls[0]["actual_ms"] == 800          # 回退睡的是真时间，账如实
    # 正向对照：有 evaluate 的同 ms 走语义路径（P0 后要**先见忙**才谈提前，故上膛一拍）
    ok_clk = FakeClock()
    ok = ModelPage(clock=ok_clk, seq=[{"masks_visible": True}])
    stats_ok = _install(ok, clock=ok_clk)
    ok.wait_for_timeout(800)
    assert ok.orig_calls == [] and stats_ok.calls[0]["fallback"] == ""
    assert stats_ok.calls[0]["actual_ms"] == legacy_fast._TICK_MS


# ---------------------------------------------------------------------------
# G11-1 三条修法（lead 点名的 D.1.9 三假页判据）
# ---------------------------------------------------------------------------


def test_d1_9_loading_document_is_not_quiet_even_with_no_masks():
    """修法 1 仍在位：reload 后 readyState=loading、零遮罩、零在途——**不得 0ms 放行**。

    P0 之后这条更硬：readyState 是**门**，不是忙碌观测（lead 的忙信号词表只有
    遮罩 / 在途请求 / 真实 DOM 变化三项）。所以「ready 翻真」既不放行也**不给资格**——
    旧测把 ready 翻真当成即可提前，属旧语义不安全（A/B 实测失败点前移），本格改写。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, state={"ready": False, "masks_visible": False,
                                       "pending": 0})
    stats = _install(page, clock=clk)
    page.wait_for_timeout(8000)
    assert clk.now == 8000                             # 等满，一毫秒都不提前
    assert stats.calls[0]["actual_ms"] == 8000
    assert stats.calls[0]["fallback"] == "no-busy-observed"
    # 第二腿（P0 新语义）：ready 翻真但**从没忙过** → 依旧睡满，门开了不等于等过东西
    page.state["ready"] = True
    page.arm([])
    page.wait_for_timeout(8000)
    assert stats.calls[1]["actual_ms"] == 8000, "ready 翻真不是忙碌观测"
    assert stats.calls[1]["fallback"] == "no-busy-observed"
    # 正向对照：ready + 见过遮罩一拍 → 门与资格齐了，才提前
    page.arm([{"masks_visible": True}])
    page.wait_for_timeout(8000)
    assert stats.calls[2]["actual_ms"] == legacy_fast._TICK_MS
    assert stats.calls[2]["fallback"] == ""


def test_d1_9_anchor_invisible_blocks_early_pass():
    """修法 3 仍在位：锚点非空时「骨架 complete 但应用未起」不得提前放行。

    P0 补的第二腿：**锚点可见也不是忙碌观测**（它是门）——旧测「锚点可见即提前」
    属旧语义不安全，现要求「见过忙碌 + 锚点可见」两条同时成立才提前。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, state={"anchor_visible": False})
    stats = _install(page, clock=clk, anchor=".app-main")
    page.wait_for_timeout(2000)
    assert clk.now == 2000                             # 锚点不可见=未静默
    assert stats.calls[0]["actual_ms"] == 2000
    assert page.anchor_asked == ".app-main"            # 锚点真进了 JS，不是摆设
    # 第二腿：锚点可见、但本笔从未见过忙碌 → 还是睡满
    page.state["anchor_visible"] = True
    page.arm([])
    page.wait_for_timeout(2000)
    assert stats.calls[1]["actual_ms"] == 2000, "锚点可见不是忙碌观测"
    assert stats.calls[1]["fallback"] == "no-busy-observed"
    # 正向对照：见过遮罩一拍 + 锚点可见 → 提前
    page.arm([{"masks_visible": True}])
    page.wait_for_timeout(2000)
    assert stats.calls[2]["actual_ms"] == legacy_fast._TICK_MS


def test_d1_9_min_ms_floor_applied_and_clamped_by_requested():
    """修法 2 **不动**：拿到提前资格后仍补足到 min_ms；min_ms>requested 按 requested 截断。

    P0 只收紧「什么时候允许提前」，下限那一段算术一字未改——所以本格改成「先见忙一拍」
    再取下限，而不是改判据（旧语义不安全那句写在下面正向对照的注释里）。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, seq=[{"masks_visible": True}])
    stats = _install(page, clock=clk, min_ms=400)
    page.wait_for_timeout(5000)                        # 见忙→落定→下限 400
    assert stats.calls[0]["actual_ms"] == 400
    assert clk.slept[-1] == 375.0                      # 25 已花，进程侧补到 400
    assert page.orig_calls == []                       # 补睡不经 wait_for_timeout（判据 4）
    # 截断（lead 明列）：requested=100 < 下限 400 → 最多 100，绝不更长
    clk2 = FakeClock()
    page2 = ModelPage(clock=clk2, seq=[{"masks_visible": True}])
    stats2 = _install(page2, clock=clk2, min_ms=400)
    page2.wait_for_timeout(100)
    assert stats2.calls[0]["actual_ms"] == 100
    assert clk2.now == 100                             # 一毫秒都没多睡
    # 负向对照（P0 本体）：同样的下限、同样 5000ms，但**从没忙过** → 下限用不上，睡满
    clk3 = FakeClock()
    page3 = ModelPage(clock=clk3)
    stats3 = _install(page3, clock=clk3, min_ms=400)
    page3.wait_for_timeout(5000)
    assert stats3.calls[0]["actual_ms"] == 5000
    assert stats3.calls[0]["fallback"] == "no-busy-observed"

def test_d1_9_default_floor_is_400(monkeypatch):
    """默认下限=400（G11-1 缺省）：拿到资格后 5000→400 仍是 12 倍节省。

    P0 之后「拿到资格」是前置条件（本格上膛一拍遮罩）；没资格的那批走
    `test_p0_never_busy_page_sleeps_out_requested_original_never_called`。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, seq=[{"masks_visible": True}])
    stats = legacy_fast.install_fast_waits(page, clock=clk)   # 走真实默认，不注入 min_ms
    page.wait_for_timeout(5000)
    assert stats.calls[0]["actual_ms"] == legacy_fast._DEFAULT_MIN_MS == 400
    monkeypatch.delenv("ATLAS_FAST_MIN_MS", raising=False)
    assert legacy_fast.min_ms_gate() == 400            # env 缺省与代码默认同一把尺


def test_p0_never_busy_sleeps_out_requested_all_three_tiers():
    """lead 验收格 1（三档各一对）：从未观测到忙碌 → actual==requested，逐档成立。

    旧语义的绿测假定「静默⇒提前」，本格的负向腿逐档把它否掉；正向腿用**该档自己的**
    忙碌信号（遮罩 / 在途请求 / 真实 DOM 变化），保证「睡满」不是因为档位没判据而恒真。
    """
    cases = [
        ("mask", [{"masks_visible": True}]),
        ("mask+xhr", [{"pending": 5}]),
        # mutation 档的「落定」= 见过真实变化后再静默满 quiet 窗，两步都要写出来
        ("mutation", [{"mutated": True, "mutated_ago": 0},
                      {"mutated": True, "mutated_ago": 9_999}]),
    ]
    for probe, busy in cases:
        clk = FakeClock()
        never = ModelPage(clock=clk)
        stats = _install(never, clock=clk, probe=probe)
        never.wait_for_timeout(1500)
        assert stats.calls[0]["actual_ms"] == 1500, probe
        assert stats.calls[0]["fallback"] == "no-busy-observed", probe
        # 正向对照：该档的忙碌信号出现一拍，随后落定 → 提前
        clk2 = FakeClock()
        busy_page = ModelPage(clock=clk2, seq=busy)
        stats2 = _install(busy_page, clock=clk2, probe=probe)
        busy_page.wait_for_timeout(1500)
        assert stats2.calls[0]["actual_ms"] == legacy_fast._TICK_MS, probe
        assert stats2.calls[0]["fallback"] == "", probe


# ---------------------------------------------------------------------------
# D.1.2 三档 probe：mask 恒含、xhr/mutation 递加 + 降级
# ---------------------------------------------------------------------------


def test_d1_2_tier_composition_mask_always_present():
    """缺陷 2 的修法：三档共用同一把尺（vis 函数 + mask 忙碌段 + readyState 门），递加不替代。

    正向对照=mask 档绝不含 xhr/mutation 段（不是「拼了就算」的字符串巧合）。
    P0 之后判据吐**状态码**，「递加」按段序核：可见性尺在头、mask 忙碌段紧随其后、
    档位增量插在它与 readyState 门之间、门与 `quiet` 尾三档逐字一致。
    用源码常量当锚点而不是手抄文本——手抄会让这条测在判据改写后各自演化（#92）。
    """
    def head(cond: str) -> str:
        """递加不许碰的公共头：可见性尺 + mask 忙碌段（档位增量只能插在其后）。"""
        pre = cond.split(legacy_fast._JS_MASK_BUSY_CLAUSE)[0]
        return pre + legacy_fast._JS_MASK_BUSY_CLAUSE

    tail = legacy_fast._JS_NOT_READY_CLAUSE + legacy_fast._JS_RETURN_QUIET + " }"
    base = legacy_fast.build_condition(("mask",))
    assert legacy_fast._JS_VISIBLE_FN in base
    assert legacy_fast._JS_MASK_BUSY_CLAUSE in base
    assert "'busy-mask'" in base and base.endswith(tail)
    assert "__atlasFastXhr" not in base and "__atlasFastLast" not in base
    with_xhr = legacy_fast.build_condition(("mask", "xhr"))
    assert head(with_xhr) == head(base)                # 公共头逐字在前，只做递加
    assert legacy_fast._JS_XHR_GATE_CLAUSE in with_xhr  # hook 不在场=不可评估
    assert legacy_fast._JS_XHR_BUSY_CLAUSE in with_xhr  # pending>0=忙碌观测
    assert with_xhr.endswith(tail)                     # 门与尾不因档位变
    with_mut = legacy_fast.build_condition(("mask", "xhr", "mutation"), quiet_ms=200)
    assert head(with_mut) == head(with_xhr)
    assert "__atlasFastMutated" in with_mut and ">= 200" in with_mut
    assert "'busy-mutation'" in with_mut and "'no-activity'" in with_mut
    assert ".el-loading-mask" in with_mut and "document.readyState" in with_mut
    assert with_mut.endswith(tail)
    # 递加不是替代：三档的公共头完全一致（同一把尺，不留三份判据 #92）
    assert head(base) == head(with_xhr) == head(with_mut)
    # 忙碌段必须排在门之前：「此刻仍忙」不能被 readyState 门抢先答成 not-ready
    assert (with_mut.index(legacy_fast._JS_MASK_BUSY_CLAUSE)
            < with_mut.index(legacy_fast._JS_XHR_BUSY_CLAUSE)
            < with_mut.index("'busy-mutation'")
            < with_mut.index(legacy_fast._JS_NOT_READY_CLAUSE))


def test_d1_2_xhr_clause_requires_pending_zero_not_hook_absence():
    """「不留 xhr 恒 0 的假静默」：在途请求≠0 不放行；hook 不在场如实降 mask。

    P0 追加的一层：hook 不在场=**不可评估**（`no-hook`），它既不放行也**不计入忙碌观测**
    ——把「我不知道」记成「我看到它忙过」等于用未知换提前资格，与假静默同雷。
    """
    cond = legacy_fast.build_condition(("mask", "xhr"))
    assert legacy_fast._CODE_NO_HOOK in cond           # hook 缺席=不可评估
    assert legacy_fast._CODE_BUSY_XHR in cond          # 在途>0=忙碌
    # 判据真在管账：pending=7 的页面在 xhr 档必须等满预算
    clk = FakeClock()
    page = ModelPage(clock=clk, state={"pending": 7})
    stats = _install(page, clock=clk, probe="mask+xhr")
    page.wait_for_timeout(600)
    assert stats.calls[0]["actual_ms"] == 600
    assert stats.calls[0]["probe"] == "mask+xhr" and stats.calls[0]["fallback"] == ""
    # 正向对照一：在途请求落定（见过 pending>0 再归零）才提前
    page_ok = ModelPage(clock=FakeClock(), seq=[{"pending": 5}])
    stats_ok = _install(page_ok, clock=page_ok.clock, probe="mask+xhr")
    page_ok.wait_for_timeout(600)
    assert stats_ok.calls[0]["actual_ms"] == legacy_fast._TICK_MS
    assert stats_ok.calls[0]["fallback"] == ""
    # 负向对照（P0）：xhr 一直是 0 也**从没忙过** → 睡满，「没请求」不等于「等完了」
    page_idle = ModelPage(clock=FakeClock())
    stats_idle = _install(page_idle, clock=page_idle.clock, probe="mask+xhr")
    page_idle.wait_for_timeout(600)
    assert stats_idle.calls[0]["actual_ms"] == 600
    assert stats_idle.calls[0]["fallback"] == "no-busy-observed"
    # 正向对照二（也是负向对照）：hook 装不上时**降级并如实记账**，不冒充在判 xhr
    page_ng = ModelPage(clock=FakeClock(), hooked=False)
    stats_ng = _install(page_ng, clock=page_ng.clock, probe="mask+xhr")
    page_ng.wait_for_timeout(600)
    assert stats_ng.calls[0]["probe"] == "mask"
    assert stats_ng.calls[0]["fallback"].startswith("hook-degraded:xhr")
    # 协议格：hook 装好后中途丢（页侧只剩 no-hook）→ 睡满，且 no-hook 不算忙碌观测
    lost = CannedPage([legacy_fast._CODE_NO_HOOK], clock=FakeClock())
    stats_lost = legacy_fast.install_fast_waits(lost, clock=lost.clock, probe="mask+xhr",
                                                min_ms=0)
    lost.wait_for_timeout(400)
    assert stats_lost.calls[0]["actual_ms"] == 400
    assert stats_lost.calls[0]["fallback"] == "no-busy-observed"


def test_d1_2_hook_missing_degrades_to_mask_with_warn(capsys):
    """设计 §A 既定降级：hook 未装上 → 降 mask + WARN 一行（单侧测，不留假账）。"""
    clk = FakeClock()
    page = ModelPage(clock=clk, hooked=False)
    stats = _install(page, clock=clk, probe="mask+xhr+mutation")
    page.wait_for_timeout(1000)
    out = capsys.readouterr().out
    assert "WARN" in out and "hook" in out and "mask" in out
    assert page.conditions                             # 仍按 mask 档求值过
    assert "__atlasFastXhr" not in page.conditions[-1]
    assert stats.calls[0]["probe"] == "mask"
    assert stats.fallback_count == 1                   # 降级=真回退，仍计数
    assert stats.no_busy_count == 1                    # 同时也是 fail-safe 睡满
    assert stats.calls[0]["fallback"].startswith("hook-degraded:xhr:")
    assert stats.calls[0]["fallback"].endswith("no-busy-observed")
    # 正向对照：hook 在场时走满档（xhr+mutation 段都在条件里）
    page_ok = ModelPage(clock=FakeClock(), seq=[{"masks_visible": True}])
    _install(page_ok, clock=page_ok.clock, probe="mask+xhr+mutation")
    page_ok.wait_for_timeout(1000)
    assert "XMLHttpRequest.prototype" in "".join(page_ok.init_calls)
    assert page_ok.hook_calls == ["xhr", "mutation"]   # 两档 hook 都真装过
    assert legacy_fast._JS_XHR_BUSY_CLAUSE in page_ok.conditions[-1]
    assert "__atlasFastLast" in page_ok.conditions[-1]


def test_d1_2_mutation_quiet_window_is_budget_bounded():
    """quiet 窗本身受「绝不更长」预算约束：quiet>max_ms 时按 max_ms 截断。"""
    cond_big = legacy_fast.build_condition(("mask", "xhr", "mutation"), quiet_ms=900)
    assert ">= 900" in cond_big
    clk = FakeClock()
    page = ModelPage(clock=clk, state={"mutated": True, "mutated_ago": 100})
    stats = _install(page, clock=clk, probe="mutation", quiet_ms=900)
    page.wait_for_timeout(200)                         # 预算 200 < quiet 900
    assert ">= 200" in page.conditions[-1]             # 截断进 JS，不是拿 900 死等
    assert stats.calls[0]["actual_ms"] == 200          # 静默窗未达=等满，绝不更长
    # 正向对照：见过真实变化（第一拍 mutated_ago<窗 → busy-mutation），窗落定 → 提前
    clk2 = FakeClock()
    ok = ModelPage(clock=clk2, state={"mutated": True, "mutated_ago": 9_999},
                   seq=[{"mutated": True, "mutated_ago": 0}])
    stats2 = _install(ok, clock=clk2, probe="mutation", quiet_ms=900)
    ok.wait_for_timeout(200)                           # 窗截到 200，第二拍即落定
    assert ok.conditions[-1] == ok.conditions[0]       # 同一条件反复问，不重拼
    assert stats2.calls[0]["actual_ms"] == legacy_fast._TICK_MS
    assert stats2.calls[0]["fallback"] == ""
    # 负向对照（P0）：mutated=False（装钩后一次真变化都没有）→ no-activity，睡满
    clk3 = FakeClock()
    still = ModelPage(clock=clk3, state={"mutated": False})
    stats3 = _install(still, clock=clk3, probe="mutation", quiet_ms=900)
    still.wait_for_timeout(200)
    assert stats3.calls[0]["actual_ms"] == 200
    assert "'no-activity'" in still.conditions[-1]
    assert stats3.calls[0]["fallback"] == "no-busy-observed"


def test_d1_2_probe_alias_and_tier_gate_vocabulary(monkeypatch):
    """档位简写 xhr/mutation 与全名同档（mask 恒含）；未知取值不启用 + WARN。"""
    monkeypatch.setenv("ATLAS_FAST_WAITS", "xhr")
    assert legacy_fast.waits_gate() == "xhr"
    assert legacy_fast.layers_for("xhr") == ("mask", "xhr")
    monkeypatch.setenv("ATLAS_FAST_WAITS", "mask+xhr+mutation")
    assert legacy_fast.layers_for(legacy_fast.waits_gate()) == (
        "mask", "xhr", "mutation")
    buf = io.StringIO()
    monkeypatch.setenv("ATLAS_FAST_WAITS", "mask+mutation")   # 跳档不认（不在表里）
    with contextlib.redirect_stdout(buf):
        assert legacy_fast.waits_gate() == ""
    assert "WARN" in buf.getvalue()
    for off in ("", "0", "off", "false"):
        monkeypatch.setenv("ATLAS_FAST_WAITS", off)
        assert legacy_fast.waits_gate() == ""
    for on in ("1", "ON", "True"):
        monkeypatch.setenv("ATLAS_FAST_WAITS", on)
        assert legacy_fast.waits_gate() == "mask"


def test_d1_2_transients_marks_lockstep_with_waits():
    """瞬态文本判据与 `waits.py::_TRANSIENT_MARKS` 同源（vendored 复制的唯一合法形态）。

    G11-4 要求按文件 vendor，不能 import atlas——那就锁步测钉死「改一漏一必红」。
    """
    assert legacy_fast._TRANSIENT_MARKS == waits._TRANSIENT_MARKS
    assert legacy_fast._TRANSIENT_MARKS                       # 非空=测不是恒真
    assert legacy_fast._is_transient(TRANSIENT_EXC) is True
    assert legacy_fast._is_transient(UNRECOVERABLE_EXC) is False
    assert legacy_fast._is_unrecoverable(UNRECOVERABLE_EXC) is True
    assert legacy_fast._is_unrecoverable(UNKNOWN_EXC) is False
    # 坏 __str__ 不顶判据（判据函数不得先于被判定对象死，#89/#127 同源纪律）
    class BadStr(Exception):
        def __str__(self):
            raise RuntimeError("nope")
    assert legacy_fast._exception_text(BadStr()) == "BadStr"
    assert legacy_fast._is_transient(BadStr()) is False


# ---------------------------------------------------------------------------
# D.1.3 多遮罩 + D.1.4 递归禁令 + D.1.8 只换一法
# ---------------------------------------------------------------------------


def test_d1_3_mask_probe_judges_all_masks_not_first():
    """旧 wait_loading 实测教训：全枚举判「有没有可见遮罩」，不是只看第一个。

    诚实边界：本测钉 **JS 构成面**（`some(vis)` 全枚举、零位置索引）。设计 D.1.3 的
    「真构造两遮罩（首隐次显）→ 判未静默」需要真实 JS 求值，属 #20 真机项（R-G3），
    离线不假称已证。
    """
    cond = legacy_fast.build_condition(("mask",))
    assert "querySelectorAll('.el-loading-mask')" in cond
    assert ".some(vis)" in cond
    assert "[0]" not in cond                           # 只判第一个=已知坑，禁
    for token in ("getClientRects", "visibility", "display"):
        assert token in cond                           # 可见性三关在位
    # 语义模型对照：首隐次显在本判据下不可表达为「静默」（只要有可见遮罩即未静默）
    clk = FakeClock()
    page = ModelPage(clock=clk, state={"masks_visible": True})
    _install(page, clock=clk)
    page.wait_for_timeout(300)
    assert clk.now == 300


def test_d1_4_poll_path_never_touches_page_timers():
    """§A 判据 4 的递归禁令：垫片自己的求值路径上不许出现 wait_for_timeout /
    wait_for_function（轮询只用进程侧 time.sleep）。AST 权威，非文本巧合。"""
    banned = {"wait_for_timeout", "wait_for_function"}
    tree = ast.parse((SRC_DIR / "legacy_fast.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_settle_probe", "_install_hook", "build_condition", "settle"):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(
                        inner.func, ast.Attribute):
                    assert inner.func.attr not in banned, (
                        f"{node.name} 里出现 {inner.func.attr}（递归/越权风险）")
    # 行为面正向对照：静默路径上原始睡眠零调用（test_d1_1_quiet 同族，此处再钉一次）
    clk = FakeClock()
    page = ModelPage(clock=clk)
    _install(page, clock=clk)
    page.wait_for_timeout(1000)
    assert page.orig_calls == [] and page.wff_calls == []


def test_d1_8_only_wait_for_timeout_is_replaced():
    """设计 D.1.8：垫片只换 `wait_for_timeout` 一法，其余 14 方法+`_headless` 不动。"""
    # 旧 A0 表的 page 方法面（15 法）——垫片能写入的属性只许是其中之一，且只许一个
    page_methods = {"evaluate", "fill", "get_by_role", "goto", "keyboard", "locator",
                    "mouse", "reload", "screenshot", "url", "wait_for_function",
                    "wait_for_load_state", "wait_for_selector", "wait_for_timeout",
                    "wait_for_url"}
    tree = ast.parse((SRC_DIR / "legacy_fast.py").read_text(encoding="utf-8"))
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute):
                    assigned.add(tgt.attr)
    touching_page = assigned & page_methods
    assert touching_page == {"wait_for_timeout"}, sorted(touching_page)   # 正向+负向同测
    assert "cause" in assigned          # _Unrecoverable 自带字段，不是 page 方法（对照）
    src = (SRC_DIR / "legacy_fast.py").read_text(encoding="utf-8")
    assert "_headless" not in src                       # 赋值面归产物 conftest（R-3）


def test_only_patched_method_restores_class_methods():
    """uninstall 后其余方法仍是原对象（还原不越权）。"""
    page = ModelPage(clock=FakeClock())
    before = (page.wait_for_function, page.evaluate, page.add_init_script)
    _install(page, clock=page.clock)
    assert page.wait_for_timeout is not ModelPage.wait_for_timeout  # shadow 在位
    assert "wait_for_timeout" in page.__dict__          # 实例级 shadow 确实在
    legacy_fast.uninstall(page)
    assert "wait_for_timeout" not in page.__dict__      # shadow 已撤，回类方法
    assert (page.wait_for_function, page.evaluate, page.add_init_script) == before
    assert legacy_fast.uninstall(page) == 0             # 幂等
    assert legacy_fast.uninstall(NoEvaluatePage()) == 0  # 没装过的也 0


# ---------------------------------------------------------------------------
# install/uninstall 语义与 stats（⑥⑦ + D.1.7）
# ---------------------------------------------------------------------------


def test_install_twice_never_recurses():
    """同 target 重复 install 只更新参数：原始引用始终是**第一个**（递归防线）。"""
    clk = FakeClock()
    page = ModelPage(clock=clk, seq=[{"masks_visible": True}])
    true_original = page.wait_for_timeout          # 未打补丁时的类方法 bound
    stats1 = _install(page, clock=clk)
    stats2 = _install(page, clock=clk, probe="mask+xhr")
    assert stats2 is not stats1                      # 新 stats 显式接管（不悄悄合流）
    page.wait_for_timeout(300)
    # 恰一层语义等待：一拍见忙 + 一拍落定=两次求值。两层包裹会把同一笔求成四次
    # （P0 之前这里等于 1 次「静默即返回」；改判据后用**拍数**继续钉同一件事）
    assert len(page.conditions) == 2
    legacy_fast.uninstall(page)
    page.wait_for_timeout(50)
    assert page.orig_calls == [50]                   # 还原到真原始，非中间层
    assert true_original.__func__ is ModelPage.wait_for_timeout


def test_zero_and_negative_timeouts_delegate_to_original():
    """lead 追加 3：`requested <= 0` 一律**转交原始函数**，垫片不解释、不放大、不记假账。

    0 是 no-op（原始自己知道），负数保持旧实现抛给 playwright 的异常语义——
    垫片把它翻成「什么都不做」= 把用户的负数错误静默吞掉。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk)
    stats = _install(page, clock=clk)
    page.wait_for_timeout(0)
    assert page.orig_calls == [0]                      # 原始收到了一次 0，不是被跳过
    assert page.conditions == []                       # 未经语义路径
    page.wait_for_timeout(-5)
    assert page.orig_calls == [0, -5]                  # 负数原样转交，不猜
    assert page.conditions == []
    page.wait_for_timeout(None)                        # 非数值同样转交（旧调用方形态）
    assert page.orig_calls == [0, -5, None]
    assert stats.calls == []                           # 没有等待发生=一条不记
    legacy_fast.uninstall(page)


def _direct_wait_caller(page, ms):
    """⑥专用具名壳：caller 归因必须指到这一行，不是 unknown。"""
    page.wait_for_timeout(ms)


def test_d1_7_stats_jsonl_fields_and_caller_attribution(tmp_path):
    """逐调用 jsonl 的七键形态（派单判据⑥）+ 归因指到旧用例帧，不是垫片自己。"""
    clk = FakeClock()
    page = ModelPage(clock=clk)
    stats = _install(page, clock=clk)
    _direct_wait_caller(page, 2000)
    legacy_fast.uninstall(page)
    out = tmp_path / "waits.jsonl"
    assert stats.to_jsonl(out) == 1
    entry = json.loads(out.read_text(encoding="utf-8"))
    assert set(entry) == {"caller_file", "caller_line", "caller_func",
                          "requested_ms", "actual_ms", "probe", "fallback"}
    assert entry["caller_func"] == "_direct_wait_caller"
    assert entry["caller_file"].endswith("test_legacy_fast.py")   # 不是垫片自己的帧
    assert entry["caller_line"] > 0
    assert entry["probe"] == "mask"


def test_d1_7_stats_aggregate_shape_and_saved_never_negative():
    """聚合形态**六键**齐（P0 追加 `no_busy_observed`）、`actual_ms_total ≤ original`。

    两个计数语义分立是本次裁定带来的形态要求：`fallbacks`=真回退（旧口径「0=探针没错」
    必须继续成立），`no_busy_observed`=fail-safe 睡满（正常路径，不是故障）。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk)
    stats = _install(page, clock=clk)
    page.arm([{"masks_visible": True}])              # 早退：见忙一拍→落定
    page.wait_for_timeout(5000)
    early_actual = stats.calls[0]["actual_ms"]
    assert early_actual == legacy_fast._TICK_MS and stats.calls[0]["fallback"] == ""
    page.arm([{"masks_visible": True}])              # 遮罩一直在=忙过但没落定→等满
    page.state["masks_visible"] = True
    page.wait_for_timeout(1000)
    assert stats.calls[1]["fallback"] == ""          # 见过忙碌≠no-busy
    page.state["masks_visible"] = False
    page.arm([])                                     # 从没忙过→睡满并记 fail-safe
    page.wait_for_timeout(2000)
    agg = stats.aggregate()
    assert set(agg) == {"calls", "original_ms_total", "actual_ms_total",
                        "fallbacks", "no_busy_observed", "by_stage"}
    assert agg["calls"] == 3
    assert agg["original_ms_total"] == 8000
    assert agg["actual_ms_total"] == early_actual + 1000 + 2000
    assert agg["actual_ms_total"] <= agg["original_ms_total"]
    assert agg["by_stage"]["mask"]["calls"] == 3     # 档位名当键，note 永不进键
    assert agg["fallbacks"] == 0                     # 探针一次没错：旧口径继续读得出 0
    assert agg["no_busy_observed"] == 1              # 只有第三笔是 fail-safe 睡满
    assert stats.saved_ms == 5000 - early_actual     # 正数=真省了（P0 后比旧语义小，如实）
    # 非负性由「绝不更长」保证；若将来有路径能写出负数，这里必须红（不是 abs 抹真账）
    assert stats.saved_ms >= 0


def test_p0_two_counters_are_semantic_not_arithmetic():
    """同一笔可以两条都占：`fallbacks + no_busy_observed` **不等于** calls，也别拿它反推。

    lead 给的验收口径是「fallbacks 仍能读到 0」「no_busy 单独上升」；顺手钉三件容易
    被想当然的事：①降级+从未见忙并存时两个数各 +1（不是二选一）②by_stage 的键恒为
    档位名，note 不混进去③no-busy 不影响 `saved_ms` 的非负性。
    """
    stats = legacy_fast.FastWaitStats()
    stats.record(caller_file="a.py", caller_line=1, caller_func="f",
                 requested_ms=1000, actual_ms=1000, probe="mask",
                 fallback="hook-degraded:xhr:ValueError;no-busy-observed")
    stats.record(caller_file="a.py", caller_line=2, caller_func="f",
                 requested_ms=1000, actual_ms=25, probe="mask+xhr", fallback="")
    stats.record(caller_file="a.py", caller_line=3, caller_func="f",
                 requested_ms=1000, actual_ms=1000, probe="mask",
                 fallback="no-busy-observed")
    agg = stats.aggregate()
    assert agg["calls"] == 3
    assert agg["fallbacks"] == 1                     # 只有降级那笔算真回退
    assert agg["no_busy_observed"] == 2              # 第一、三笔都睡满
    assert agg["fallbacks"] + agg["no_busy_observed"] == 3
    assert set(agg["by_stage"]) == {"mask", "mask+xhr"}
    assert all("busy" not in k for k in agg["by_stage"])
    # 拆解点自证：note 单字段承载两事实，读写一把尺（split_note 与 _join_notes 同域）
    real, no_busy = legacy_fast.split_note(
        legacy_fast._join_notes("hook-degraded:xhr:ValueError",
                                legacy_fast._NOTE_NO_BUSY))
    assert real == ("hook-degraded:xhr:ValueError",)
    assert no_busy == ("no-busy-observed",)
    assert legacy_fast.split_note("")[0] == () and legacy_fast.split_note("")[1] == ()
    assert legacy_fast.split_note("probe-error:X")[0] == ("probe-error:X",)


def test_d1_7_actual_never_exceeds_requested_property():
    """property 测（D.1.7）：任意状态×档位×耗时组合下**垫片侧绝不越顶**。

    两个方向分开钉（与 xcheck C1 x2 同口径）：
    - 垫片**自己**花的（`clk.slept` 之和）≤ 预算——这是唯一可控的方向；
    - 页侧求值成本可以反噬预算（evaluate 一旦发出就退不回来），越顶上界恰是一趟
      cost；这种情况账面仍记 `max_ms`（不许把页侧耗时记成「省了」，也不许记成垫片睡的）。
    """
    table = [
        ({}, "mask", 0, 5000),
        ({"masks_visible": True}, "mask", 0, 2000),
        ({"ready": False}, "mask", 50, 8000),
        ({"pending": 7}, "mask+xhr", 0, 500),
        ({"mutated": True, "mutated_ago": 0}, "mutation", 10, 100),
        ({"anchor_visible": False}, "mask", 0, 400),
        ({}, "mask", 0, 100),
        ({"ready": False, "anchor_visible": False}, "mask+xhr+mutation", 25, 5000),
        ({"masks_visible": True}, "mask", 6000, 5000),      # 页侧成本反噬预算
    ]
    # 第四列 seq：P0 之后**提前那条路**也要过同一把尺（一拍见忙→落定），否则这张表
    # 只剩「睡满」一个方向，越顶防线等于没测到另一条出口
    seqs = [None, [{"masks_visible": True}], [{"pending": 4}],
            [{"mutated": True, "mutated_ago": 0}]]
    for state, probe, cost, req in table:
        for seq in seqs:
            for floor in (0, 400, 10_000):               # 下限越顶的形态也在表里
                clk = FakeClock()
                page = ModelPage(clock=clk, state=dict(state), cost_ms=cost,
                                 seq=seq)
                stats = _install(page, clock=clk, probe=probe, min_ms=floor)
                page.wait_for_timeout(req)
                entry = stats.calls[0]
                tag = (state, probe, cost, req, floor, seq)
                assert entry["actual_ms"] <= req, tag            # 账面如实，不虚报
                assert entry["requested_ms"] == req, tag
                assert sum(clk.slept) <= req, tag                # 垫片补睡之和 ≤ 预算
                assert clk.now <= req + cost, tag      # 越顶只可能来自页侧一趟成本
                if cost == 0:
                    assert clk.now <= req, tag                   # 无页侧成本时硬不变量
                assert page.orig_calls == [] or entry["fallback"], tag  # 叠原睡必标注
                # 没上膛（从没拿到提前资格）的页，账面恒=预算：睡满不是「约等于」，是分毫不差
                if seq is None:
                    assert entry["actual_ms"] == req, tag


def test_p0_protocol_shape_verdicts_are_never_early():
    """协议面（直喂码，不靠 state 推导）：拿不到「忙碌」这一事实的形态一律不提前。

    每格都对应一个真实漏点：
    - 裸 `True` = **旧布尔协议**（外部 fake/旧 JS 形状）。布尔表达不了「曾忙碌」，
      按「不可评估」处理=睡满；这正是 xcheck C1/C7 那批「即静默→远早返回」会转红的原因，
      方向是收紧不是放宽。
    - `no-hook` / `no-activity` = 探针装不上/一次真变化都没有：**我不知道**≠我看到它忙过。
    - 非字符串（None/42）= 页侧返回了协议外的东西 → 同样 fail-closed。
    """
    cases = [
        ("旧布尔 True（外部 fake 形状）", [True]),
        ("旧布尔 False", [False]),
        ("no-hook（hook 中途丢）", [legacy_fast._CODE_NO_HOOK]),
        ("no-activity（装钩后没真变化）", [legacy_fast._CODE_NO_ACTIVITY]),
        ("not-ready（只有门，没有忙）", [legacy_fast._CODE_NOT_READY]),
        ("协议外形态 None", [None]),
        ("协议外形态 42", [42]),
        ("未知字符串码", ["something-else"]),
    ]
    for label, codes in cases:
        clk = FakeClock()
        page = CannedPage(codes, clock=clk)
        stats = legacy_fast.install_fast_waits(page, clock=clk, min_ms=0)
        page.wait_for_timeout(400)
        entry = stats.calls[0]
        assert entry["actual_ms"] == 400, label        # 一分不省=与旧行为等价
        assert entry["fallback"] == "no-busy-observed", label
    # 正向对照：同一路径先吐 busy-mask 再吐 quiet → 提前（证明上面的「睡满」不是恒真）
    clk2 = FakeClock()
    ok = CannedPage([legacy_fast._CODE_BUSY_MASK, legacy_fast._CODE_QUIET], clock=clk2)
    stats_ok = legacy_fast.install_fast_waits(ok, clock=clk2, min_ms=0)
    ok.wait_for_timeout(400)
    assert stats_ok.calls[0]["actual_ms"] == legacy_fast._TICK_MS
    assert stats_ok.calls[0]["fallback"] == ""
    # 只忙不静=窗口耗尽，**不是**回退也不记 no-busy（两种事实不许混）
    clk3 = FakeClock()
    busy_only = CannedPage([legacy_fast._CODE_BUSY_MASK], clock=clk3)
    stats3 = legacy_fast.install_fast_waits(busy_only, clock=clk3, min_ms=0)
    busy_only.wait_for_timeout(200)
    assert stats3.calls[0]["actual_ms"] == 200
    assert stats3.calls[0]["fallback"] == ""


def test_p0_early_exit_is_guarded_by_busy_seen_ast():
    """防「把 busy 条件悄悄删掉只留 quiet」的形态测：提前那条 return 必须在 busy_seen 分支里。

    行为测能证「现在是对的」，证不了「将来改判据时不会只删一半」——本波 P0 的成因正是
    「静默即提前」看起来像判据、实际是猜。所以用 AST 钉住**结构**：
    `_settle_probe` 里凡返回 `spent`（提前出口）的 return，其祖先 If 测试必须引用 busy_seen。
    """
    tree = ast.parse((SRC_DIR / "legacy_fast.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_settle_probe")
    early_returns = []

    def dump(node) -> str:
        return ast.dump(node)

    def walk(node, guards):
        for child in ast.iter_child_nodes(node):
            inner_guards = guards
            if isinstance(child, ast.If):
                inner_guards = guards + [dump(child.test)]
            if isinstance(child, ast.Return) and "spent" in dump(child):
                early_returns.append((child, inner_guards))
            walk(child, inner_guards)

    walk(fn, [])
    assert early_returns, "提前出口消失=判据被整体删掉，这条测必须红着问"
    for node, guards in early_returns:
        assert any("busy_seen" in g for g in guards), (
            f"legacy_fast.py:{node.lineno} 的提前 return 不在 busy_seen 守卫分支内"
            "（=回到「静默即提前」的旧语义）")


def test_d1_1_last_partial_tick_still_probes_before_sleeping():
    """末拍不足一拍也**必须探**：requested==tick 的页推进 0（判据 2 只按判据提前）。

    P0 换了这条的表达方式：静默页不再 0ms 放行（那是旧语义不安全的本体，A/B 实测失败点
    前移），所以「每拍都探」改用「资格已拿到 + 只剩不足一拍」来证——垫片没有为了少探一次
    而多睡，也没有为了算术干净而跳过探针。
    """
    for req in (legacy_fast._TICK_MS + 1, legacy_fast._TICK_MS + 2):
        clk = FakeClock()
        page = ModelPage(clock=clk, seq=[{"masks_visible": True}])
        stats = _install(page, clock=clk)
        page.wait_for_timeout(req)
        assert len(page.conditions) == 2, req           # 第二拍只剩 1-2ms 也照样探
        assert stats.calls[0]["actual_ms"] == legacy_fast._TICK_MS, req
        assert page.orig_calls == [], req               # 提前=不叠原睡
    # 负向对照：同预算下从没忙过的页睡满（上面那条「25」不是恒真），但仍**探过**
    clk2 = FakeClock()
    quiet = ModelPage(clock=clk2)
    stats2 = _install(quiet, clock=clk2)
    quiet.wait_for_timeout(1)
    assert len(quiet.conditions) == 1                   # 不足一拍也探，不为算术省一次
    assert stats2.calls[0]["actual_ms"] == 1 == stats2.calls[0]["requested_ms"]
    assert stats2.calls[0]["fallback"] == "no-busy-observed"


def test_d1_7_stats_dump_failure_is_best_effort(tmp_path):
    """落盘尽力而为：目录位被占 → 返回 0 + WARN，统计仍在内存（不顶被测行为）。"""
    stats = legacy_fast.FastWaitStats()
    stats.record(caller_file="f.py", caller_line=1, caller_func="g",
                 requested_ms=10, actual_ms=5, probe="mask")
    blocked = tmp_path / "waits.jsonl"               # 先把这个名字占成**文件**
    blocked.write_text("x", encoding="utf-8")        # 再当目录用 → mkdir 必炸
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert stats.to_jsonl(blocked / "sub" / "x.jsonl") == 0
    assert "WARN" in buf.getvalue()
    assert len(stats.calls) == 1
    # 正向对照：可写路径真落得下（单形态=逐调用 jsonl；聚合面走 aggregate() 由
    # fastplugin 收尾行消费，**不落第二份文件**——设计 v2/G11-4 把 stats 钉成
    # 「jsonl + summary 一行」，#23 评审单 B-08 据此删掉 to_stats_json）
    ok = tmp_path / "ok"
    assert stats.to_jsonl(ok / "a.jsonl") == 1
    rows = [json.loads(line) for line in
            (ok / "a.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1 and rows[0]["requested_ms"] == 10


# ---------------------------------------------------------------------------
# env 门与公开 API 的入参纪律
# ---------------------------------------------------------------------------


def test_min_ms_gate_matrix(monkeypatch):
    """`ATLAS_FAST_MIN_MS`：缺省/坏值都回落默认 400；0 合法（纯探针档）；负值回落。

    坏旋钮不该顺手取消兜底——下限正是 reload 误判的补丁（G11-1）。
    """
    monkeypatch.delenv("ATLAS_FAST_MIN_MS", raising=False)
    assert legacy_fast.min_ms_gate() == 400
    monkeypatch.setenv("ATLAS_FAST_MIN_MS", "0")
    assert legacy_fast.min_ms_gate() == 0
    monkeypatch.setenv("ATLAS_FAST_MIN_MS", "250")
    assert legacy_fast.min_ms_gate() == 250
    for bad in ("abc", "-5", "12.5", ""):
        monkeypatch.setenv("ATLAS_FAST_MIN_MS", bad)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = legacy_fast.min_ms_gate()
        if bad == "":
            assert got == 400                        # 空=未置位，不是坏值（无 WARN）
            assert buf.getvalue() == ""
        else:
            assert got == 400, bad
            assert "WARN" in buf.getvalue(), bad


def test_anchor_gate_reads_env_only(monkeypatch):
    """锚点**只来自 env**（红线 #11）：缺省空=不启用，空白视同空。"""
    monkeypatch.delenv("ATLAS_FAST_SETTLE_ANCHOR", raising=False)
    assert legacy_fast.anchor_gate() == ""
    monkeypatch.setenv("ATLAS_FAST_SETTLE_ANCHOR", "  ")
    assert legacy_fast.anchor_gate() == ""
    monkeypatch.setenv("ATLAS_FAST_SETTLE_ANCHOR", ".app-main")
    assert legacy_fast.anchor_gate() == ".app-main"


def test_anchor_entering_js_is_json_escaped_not_concatenated():
    """lead 实现口径「禁止裸拼字符串」：选择器进 JS 必须转义，含引号也不炸语法。"""
    nasty = '.a[data-x="y"]'
    cond = legacy_fast.build_condition(("mask",), anchor=nasty)
    assert json.dumps(nasty) in cond                   # 走 json 字面量（带引号转义）
    assert f"querySelectorAll({nasty})" not in cond    # 裸拼形态必须不在
    # 对抗样本：能把 JS 截断的取值——必须整段留在字符串字面量里
    evil = 'x"); alert(1); ("'
    cond2 = legacy_fast.build_condition(("mask",), anchor=evil)
    assert json.dumps(evil) in cond2                   # 转义原文在场
    assert evil not in cond2                           # 未转义原文**不得**出现（截断入口）
    assert "\n" not in cond2                           # 单行，塞不进新语句
    # 正向对照：普通选择器照样进 anchor 判据段（不是转义后没人用）
    assert "querySelectorAll(" in cond and ".some(vis)" in cond


def test_bad_anchor_falls_back_to_original_wait_not_early_pass():
    """选择器写坏 → 求值抛 SyntaxError → 未知异常路径：回退剩余预算，**绝不提前放行**。

    垫片坏=退回旧行为，不是「静默成功」。
    """
    clk = FakeClock()
    page = ModelPage(clock=clk, cost_ms=50,
                     raise_seq=[SyntaxError("Invalid CSS selector")])
    stats = _install(page, clock=clk, anchor="###bad###")
    page.wait_for_timeout(3000)
    assert page.orig_calls == [2950]                  # 50 已花，只补剩余
    assert stats.calls[0]["fallback"] == "probe-error:SyntaxError"
    assert stats.calls[0]["actual_ms"] == 3000


def test_settle_and_install_argument_validation():
    """直调面也不猜：坏档位/坏 min_ms/坏 quiet_ms/坏 anchor 一律构造期点名。"""
    clk = FakeClock()
    with pytest.raises(ValueError):
        legacy_fast.install_fast_waits(ModelPage(clock=clk), probe="bogus")
    with pytest.raises(ValueError):
        legacy_fast.settle(ModelPage(clock=clk), 100, probe="mask+mutation")
    with pytest.raises(ValueError):
        legacy_fast.install_fast_waits(ModelPage(clock=clk), min_ms=-1)
    with pytest.raises(ValueError):
        legacy_fast.install_fast_waits(ModelPage(clock=clk), min_ms=True)   # bool≠数
    with pytest.raises(ValueError):
        legacy_fast.install_fast_waits(ModelPage(clock=clk), quiet_ms=0)
    with pytest.raises(ValueError):
        legacy_fast.install_fast_waits(ModelPage(clock=clk), anchor=["a"])
    # 正向对照：合法形态（含显式 0 下限、None 默认）装配成功，并真走语义路径
    page = ModelPage(clock=clk, seq=[{"masks_visible": True}])
    stats = legacy_fast.install_fast_waits(page, clock=clk, probe="mask+xhr",
                                           min_ms=0, quiet_ms=None, anchor="")
    page.wait_for_timeout(100)
    assert stats.calls[0]["actual_ms"] == legacy_fast._TICK_MS
    assert stats.calls[0]["fallback"] == ""          # 拿到资格才谈提前
    # 公开 settle 的界守 max_ms（正向）+ probe 异常向上抛给调用方（负向）
    # 假时钟必须显式注入：settle 走真实 clock 会**真睡**（P0 之后睡满的那批尤其贵）
    ok = ModelPage(clock=FakeClock(), seq=[{"masks_visible": True}])
    actual, note = legacy_fast.settle(ok, 500, min_ms=0, clock=ok.clock)
    assert note == "" and actual == legacy_fast._TICK_MS
    # settle 的 P0 面：同样的页**没上膛**=从没忙过 → 睡满 + note 点名（二元组契约不变）
    never = ModelPage(clock=FakeClock())
    actual2, note2 = legacy_fast.settle(never, 500, min_ms=0, clock=never.clock)
    assert actual2 == 500 and note2 == legacy_fast._NOTE_NO_BUSY
    bad = ModelPage(clock=FakeClock(), raise_seq=[UNKNOWN_EXC])
    with pytest.raises(RuntimeError):
        legacy_fast.settle(bad, 500, min_ms=0, clock=bad.clock)


# ---------------------------------------------------------------------------
# fastplugin：类级 Page 补丁 + 截图抑制 + 零行为变化（派单⑦⑧）
# ---------------------------------------------------------------------------


def _fake_playwright_module():
    calls = {"page": []}

    class FakePage:
        """按**新协议**答话的假页：第一拍「忙」，其后「静默」。

        为什么不再是 `return True`：布尔在 P0 之后=「不可评估」（表达不了曾忙碌），
        那样每次插件测都要真睡满预算（3000+2000ms 真墙钟），且测的是「垫片不插手」
        而不是接线本身。上膛一拍既不真睡，也仍然走完整语义路径。
        """

        def __init__(self):
            self.rec = []
            self.polls = 0

        def wait_for_timeout(self, ms=0):
            self.rec.append(ms)

        def evaluate(self, js):
            if "XMLHttpRequest.prototype" in js or "MutationObserver" in js:
                return None
            if js in (legacy_fast._JS_XHR_PRESENT, legacy_fast._JS_MUTATION_PRESENT):
                return True
            self.polls += 1
            return ("busy-mask" if self.polls == 1
                    else legacy_fast._CODE_QUIET)

    mod = types.ModuleType("playwright")
    sub = types.ModuleType("playwright.sync_api")
    sub.Page = FakePage
    mod.sync_api = sub
    return mod, sub, FakePage, calls


def _fake_allure_module():
    recorded = []

    def attach(*args, **kwargs):
        recorded.append((args, kwargs))

    mod = types.ModuleType("allure")
    mod.attach = attach
    return mod, recorded


@pytest.fixture()
def _plugin_env_on(monkeypatch):
    """垫片开关统一置位（下限 0=假页求值即静默，判据测不真睡）。"""
    for key in ("ATLAS_FAST_WAITS", "ATLAS_FAST_NO_STEP_SHOTS",
                "ATLAS_FAST_WAITS_STATS", "ATLAS_FAST_MIN_MS",
                "ATLAS_FAST_SETTLE_ANCHOR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ATLAS_FAST_MIN_MS", "0")


def test_plugin_class_level_patch_covers_conftest_made_pages(_plugin_env_on,
                                                             monkeypatch):
    """类级补丁（派单原话：实例级盖不住旧 conftest 自己 new 的 page）：注入后**之后**
    新建的实例也被接管；unconfigure 逐字还原类方法。"""
    mod, sub, FakePage, _calls = _fake_playwright_module()
    monkeypatch.setitem(sys.modules, "playwright", mod)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sub)
    monkeypatch.setenv("ATLAS_FAST_WAITS", "1")
    original_method = FakePage.wait_for_timeout
    fastplugin.pytest_configure(types.SimpleNamespace())
    assert FakePage.wait_for_timeout is not original_method
    p1, p2 = FakePage(), FakePage()
    assert p1.wait_for_timeout is not original_method   # 后建的页也在闸内
    p1.wait_for_timeout(3000)
    p2.wait_for_timeout(2000)
    assert p1.rec == [] and p2.rec == []               # 原始固定睡眠零调用
    fastplugin.pytest_unconfigure(types.SimpleNamespace())
    assert FakePage.wait_for_timeout is original_method
    p3 = FakePage()
    p3.wait_for_timeout(100)
    assert p3.rec == [100]                             # 还原后行为回原


def test_plugin_passes_tier_floor_and_anchor_to_shim(_plugin_env_on, monkeypatch,
                                                     capsys):
    """两个新旋钮经插件传到垫片并**在收尾可见**（A/B 复核要知道兜底层开着没开）。"""
    mod, sub, FakePage, _calls = _fake_playwright_module()
    monkeypatch.setitem(sys.modules, "playwright", mod)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sub)
    monkeypatch.setenv("ATLAS_FAST_WAITS", "mask+xhr+mutation")
    monkeypatch.setenv("ATLAS_FAST_MIN_MS", "250")
    monkeypatch.setenv("ATLAS_FAST_SETTLE_ANCHOR", ".app-main")
    fastplugin.pytest_configure(types.SimpleNamespace())
    assert fastplugin._STATE["waits_installed"] is True
    assert fastplugin._STATE["probe"] == "mask+xhr+mutation"
    assert fastplugin._STATE["min_ms"] == 250          # 不经用例真睡也能核接线
    assert fastplugin._STATE["anchor"] == ".app-main"
    fastplugin.pytest_unconfigure(types.SimpleNamespace())
    out = capsys.readouterr().out
    assert "floor_ms=250" in out and "anchor=.app-main" in out
    assert "original_ms=0" in out                      # 没跑用例=账本如实空，不编数


def test_plugin_shot_suppression_drops_only_step_result(_plugin_env_on, monkeypatch):
    """⑦（lead 追加 2 收紧）：**只认 name 位**——kwargs["name"] 或第二个位置参数。

    旧实现取「第一个字符串参数」，`allure.attach("step1_result")`（正文=字符串、没传
    name）会被误丢=丢证据；现在正文长得像名字一律透传（不猜）。

    失败截图边界（派单点名要钉）：`_save_fail_screenshot` 走 page.screenshot 字节、
    不经 attach——本闸只包 allure.attach，screenshot 属性面**零接触**（负向：包装器
    源码不引用 screenshot；正向：_FAIL 附件照常进 recorded）。"""
    allure_mod, recorded = _fake_allure_module()
    monkeypatch.setitem(sys.modules, "allure", allure_mod)
    monkeypatch.setenv("ATLAS_FAST_NO_STEP_SHOTS", "1")
    fastplugin.pytest_configure(types.SimpleNamespace())
    png = b"png-bytes"
    allure_mod.attach(png, "step3_result", "png")      # ①丢弃（name 位=第二位置参数）
    allure_mod.attach(png, "step12_result", "png")     # ②丢弃（多位数）
    allure_mod.attach(png, "step3_FAIL", "png")        # ③透传（失败取证）
    allure_mod.attach(png, "custom_note")              # ④透传
    allure_mod.attach(png, name="step4_result")        # ⑤kwargs 名也拦（同判据面）
    allure_mod.attach(png, name="step4_FAIL")          # ⑥kwargs 失败名透传
    allure_mod.attach("step1_result")                  # ⑦正文像名字但 name 位空=透传
    allure_mod.attach(png)                             # ⑧无名字：透传不猜
    allure_mod.attach("step5_result", "说明文字")       # ⑨正文+别的 name=透传
    assert len(recorded) == 6                          # ③④⑥⑦⑧⑨
    names = [(kw.get("name") if "name" in kw
              else (args[1] if len(args) > 1 and isinstance(args[1], str) else None))
             for args, kw in recorded]
    assert "step3_FAIL" in names and "custom_note" in names
    assert "step4_FAIL" in names and "说明文字" in names
    assert fastplugin._SHOTS["dropped"] == 3           # ①②⑤
    assert fastplugin._SHOTS["passed"] == 6
    tree = ast.parse((SRC_DIR / "fastplugin.py").read_text(encoding="utf-8"))
    called_attrs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attrs.add(node.func.attr)
    assert "screenshot" not in called_attrs            # 字节面零接触
    # 判据形态自证：包装器读的是 name 位，不是「第一个字符串参数」
    src = (SRC_DIR / "fastplugin.py").read_text(encoding="utf-8")
    assert "_first_string_arg" not in src              # 旧猜法已删，不留第二把尺
    fastplugin.pytest_unconfigure(types.SimpleNamespace())
    # 还原正向对照：撤闸后 step9_result **照旧进入** recorded（丢弃器已卸）
    allure_mod.attach(png, "step9_result", "png")
    assert len(recorded) == 7


def test_plugin_env_off_means_zero_behavior_change(_plugin_env_on, monkeypatch):
    """⑧：闸「未置位」与显式 `off`/`0`/`false` 四态**逐字同果**——类方法身份不变、
    attach 不被包装、一次 wait 走原始、`waits_gate()` 返回值与未置位字节相同。

    为什么要四态同测（lead 22:2x 裁决 2 的核实项）：xcheck 的 C1 off 格会在**闸关**的
    两种形态下分别取数，若 `off` 走出与未置位不同的第二态（例如装了再直通、或多打一行
    WARN），门禁就会在「同一个关」上拿到两个答案——那是「以为关了其实没关」的姊妹形态。
    加强面：两个兜底旋钮（MIN_MS/ANCHOR）单独置位但 WAITS 关着也必须零变化。
    正向对照同测：置位后确实变（「不变」不是恒真）。
    """
    mod, sub, FakePage, _calls = _fake_playwright_module()
    allure_mod, recorded = _fake_allure_module()
    monkeypatch.setitem(sys.modules, "playwright", mod)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sub)
    monkeypatch.setitem(sys.modules, "allure", allure_mod)
    monkeypatch.setenv("ATLAS_FAST_MIN_MS", "1000")    # 只开兜底旋钮，闸不开
    monkeypatch.setenv("ATLAS_FAST_SETTLE_ANCHOR", ".app-main")
    original_method = FakePage.wait_for_timeout
    original_attach = allure_mod.attach

    for raw in (None, "off", "0", "false"):           # None=未置位（默认关的基准态）
        if raw is None:
            monkeypatch.delenv("ATLAS_FAST_WAITS", raising=False)
        else:
            monkeypatch.setenv("ATLAS_FAST_WAITS", raw)
        assert legacy_fast.waits_gate() == "", raw     # 四态返回值逐字相同
        monkeypatch.delenv("ATLAS_FAST_NO_STEP_SHOTS", raising=False)
        recorded.clear()
        fastplugin.pytest_configure(types.SimpleNamespace())
        assert FakePage.wait_for_timeout is original_method, raw   # 类方法没被换
        assert allure_mod.attach is original_attach, raw           # attach 没被包装
        p = FakePage()
        p.wait_for_timeout(700)
        assert p.rec == [700], raw                     # 全是原始路径
        allure_mod.attach(b"x", "step1_result")
        assert len(recorded) == 1, raw                 # 未开闸=一条不丢
        assert fastplugin._STATE["waits_installed"] is False, raw
        fastplugin.pytest_unconfigure(types.SimpleNamespace())
        assert FakePage.wait_for_timeout is original_method, raw

    # 正向对照：置位即变（上面四态的「不变」不是恒真）
    monkeypatch.setenv("ATLAS_FAST_WAITS", "1")
    monkeypatch.setenv("ATLAS_FAST_NO_STEP_SHOTS", "1")
    fastplugin.pytest_configure(types.SimpleNamespace())
    assert FakePage.wait_for_timeout is not original_method
    assert allure_mod.attach is not original_attach
    fastplugin.pytest_unconfigure(types.SimpleNamespace())


def test_plugin_env_on_warns_on_unknown_value(monkeypatch):
    """lead 追加 4：`ATLAS_FAST_NO_STEP_SHOTS=yes` 这类手滑值必须 WARN，不静默不装。

    「以为快了其实没快」与「注入坏但没人说」是同一族假阴性；口径与 `waits_gate` 一致。
    """
    def probe(raw):
        monkeypatch.setenv("ATLAS_FAST_NO_STEP_SHOTS", raw)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = fastplugin._env_on("ATLAS_FAST_NO_STEP_SHOTS")
        return got, buf.getvalue()

    for off in ("", "0", "off", "false"):
        got, out = probe(off)
        assert got is False, off
        assert out == "", off                          # 关闭态静默：合法取值不该刷屏
    for on in ("1", "ON", "True"):
        got, out = probe(on)
        assert got is True, on
        assert out == "", on                           # 正向对照：开族同样静默
    for typo in ("yes", "ture", " enable "):
        got, out = probe(typo)
        assert got is False, typo                      # 手滑值=不开，但必须说出来
        assert "WARN" in out and "ATLAS_FAST_NO_STEP_SHOTS" in out, typo


def test_plugin_injection_failure_warns_not_crashes(monkeypatch, capsys):
    """注入失败=WARN 一行、用例照常跑（垫片是加速器不是准入闸；崩=白烧会话 #103）。

    正向对照：可用假模块时装得上（上面几条已证），本测只证坏面。"""
    for name in ("playwright", "playwright.sync_api", "allure"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    # 空壳 ModuleType 无 __path__：`from playwright.sync_api import Page` 在
    # import 机制处失败，真包也休想被顺路捞到
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "allure", types.ModuleType("allure"))
    monkeypatch.setenv("ATLAS_FAST_WAITS", "1")
    monkeypatch.setenv("ATLAS_FAST_NO_STEP_SHOTS", "1")
    fastplugin.pytest_configure(types.SimpleNamespace())       # 不许抛
    out = capsys.readouterr().out
    assert "WARN" in out
    assert fastplugin._STATE["waits_installed"] is False
    assert fastplugin._STATE["shots_installed"] is False
    fastplugin.pytest_unconfigure(types.SimpleNamespace())     # 收尾无残留爆炸


def test_plugin_stats_flush_on_unconfigure(monkeypatch, tmp_path):
    """统计落盘链：ATLAS_FAST_WAITS_STATS 置位 → jsonl + 汇总行（含兜底层）；
    未置位 → 不落盘（内存汇总照打，0 也是结论不是缺席）。"""
    mod, sub, FakePage, _calls = _fake_playwright_module()
    monkeypatch.setitem(sys.modules, "playwright", mod)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sub)
    out = tmp_path / "reports" / "waits.jsonl"
    monkeypatch.setenv("ATLAS_FAST_WAITS", "mask")
    monkeypatch.setenv("ATLAS_FAST_MIN_MS", "0")
    monkeypatch.setenv("ATLAS_FAST_WAITS_STATS", str(out))
    fastplugin.pytest_configure(types.SimpleNamespace())
    FakePage().wait_for_timeout(1234)
    fastplugin.pytest_unconfigure(types.SimpleNamespace())
    entry = json.loads(out.read_text(encoding="utf-8"))
    assert entry["requested_ms"] == 1234 and entry["probe"] == "mask"
    assert entry["fallback"] == "" and entry["caller_line"] > 0
    assert entry["actual_ms"] <= entry["requested_ms"]
    # 未置路径：不造文件（负向），汇总仍打（正向）
    out2 = tmp_path / "reports2" / "waits.jsonl"
    mod2, sub2, FakePage2, _c2 = _fake_playwright_module()
    monkeypatch.setitem(sys.modules, "playwright", mod2)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sub2)
    monkeypatch.delenv("ATLAS_FAST_WAITS_STATS", raising=False)
    fastplugin.pytest_configure(types.SimpleNamespace())
    FakePage2().wait_for_timeout(10)
    fastplugin.pytest_unconfigure(types.SimpleNamespace())
    assert not out2.exists()


# ---------------------------------------------------------------------------
# G11-4 vendoring 自包含
# ---------------------------------------------------------------------------


def test_vendor_import_surface_is_stdlib_or_optional_only():
    """vendoring 硬约束：两文件 import 面 ⊆ 标准库∪{playwright, allure}，无相对 import、
    无 atlas 引用（emit-impl 按真文件字节 vendor 进旧仓，那边没装 atlas）。"""
    allowed = {"__future__", "os", "re", "json", "sys", "time", "pathlib",
               "importlib", "types", "playwright", "allure"}
    for fname in ("legacy_fast.py", "fastplugin.py"):
        src = (SRC_DIR / fname).read_text(encoding="utf-8")
        names = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    pytest.fail(f"{fname} 含相对 import（vendor 后无包上下文必炸）")
                if node.module:
                    names.add(node.module.split(".")[0])
        extra = names - allowed
        assert not extra, f"{fname} 越界 import: {sorted(extra)}"
        assert "atlas" not in names       # AST 权威；docstring 提「不 import atlas」是说明文


def test_vendor_no_business_selector_default():
    """红线 #11：锚点选择器**只来自 env**，vendored 源码里不许有业务字面量默认值。"""
    for fname in ("legacy_fast.py", "fastplugin.py"):
        src = (SRC_DIR / fname).read_text(encoding="utf-8")
        assert ".app-main" not in src, fname          # lead 的示例值不得进代码
        assert "app_main" not in src, fname


def test_vendor_standalone_load_exposes_contract_symbols():
    """standalone 加载真跑一次：`_load_shim` 不经 atlas 包也能给出全部契约符号。"""
    saved = sys.modules.pop("_atlas_legacy_fast_standalone", None)
    try:
        shim = fastplugin._load_shim()
        for sym in ("install_fast_waits", "settle", "FastWaitStats", "uninstall",
                    "waits_gate", "min_ms_gate", "anchor_gate", "layers_for",
                    "build_condition"):
            assert hasattr(shim, sym), sym
        # installed_targets 已按 #23 评审单 B-07 删除（src/fastplugin/docs 三处零消费，
        # 还原面走 uninstall(None)）——名册里不列它，也不许「顺手加回来」
        assert not hasattr(shim, "installed_targets")
        assert not hasattr(shim.FastWaitStats, "to_stats_json")   # B-08 同批删
        assert shim is not legacy_fast                # 这条加载路径不走 atlas 包
        assert shim.__file__ == str(SRC_DIR / "legacy_fast.py")
        # 契约签名不变（G11-1 明写 settle 返回二元组）。假时钟注入：P0 之后没上膛的页
        # 睡满 500ms 是**真睡**，判据测不烧墙钟
        clk = FakeClock()
        page = ModelPage(clock=clk)
        out = shim.settle(page, 500, min_ms=0, clock=clk)
        assert isinstance(out, tuple) and len(out) == 2
        assert out == (500, shim._NOTE_NO_BUSY)       # standalone 那份也带新语义
    finally:
        sys.modules.pop("_atlas_legacy_fast_standalone", None)
        if saved is not None:
            sys.modules["_atlas_legacy_fast_standalone"] = saved


# ---------------------------------------------------------------------------
# 调用点规则（site rules）：按等待点语义条件放行（gap11 安全提速）
# ---------------------------------------------------------------------------


class RulePage:
    """规则路径专用假页：evaluate 按脚本答真/假（规则走进程侧 tick 轮询）。

    规则路径**不得**用 `wait_for_function`：真机实测它在页面导航中会把 6s 预算拖成
    9.7s（Playwright 重新绑定帧），违反「绝不更长」。这条边界和通用档判据 4 同源。
    """

    def __init__(self, clock=None, ok_seq=None, ok_default=False, cost_ms=0):
        self.clock = clock
        self.ok_seq = list(ok_seq or [])
        self.ok_default = ok_default
        self.cost_ms = cost_ms
        self.evals: list = []
        self.orig_calls: list = []

    def wait_for_timeout(self, ms=0):
        self.orig_calls.append(ms)
        if self.clock is not None:
            self.clock.advance(max(0, int(ms)))

    def evaluate(self, js):
        self.evals.append(js)
        if self.clock is not None:
            self.clock.advance(self.cost_ms)
        if self.ok_seq:
            return self.ok_seq.pop(0)
        return self.ok_default

    def wait_for_function(self, *a, **kw):
        raise AssertionError("规则路径不得用 wait_for_function（导航中预算会失控）")


def _rule(**kw):
    base = {"file": "login.py", "line": 159, "func": "_hover_menu_li",
            "kind": "js", "js": "() => true"}
    base.update(kw)
    return base


def _pin_caller(monkeypatch, file="D:/legacy/login.py", line=159,
                func="_hover_menu_li"):
    monkeypatch.setattr(legacy_fast, "_caller_frame", lambda: (file, line, func))


def test_site_rule_js_hit_returns_early_original_never_called(monkeypatch):
    _pin_caller(monkeypatch)
    clk = FakeClock()
    page = RulePage(clock=clk, ok_default=True)
    stats = _install(page, clock=clk, site_rules=[_rule()])
    page.wait_for_timeout(2000)
    assert page.orig_calls == []                      # 规则路径不叠原始睡眠
    assert page.evals and "() => true" in page.evals[0]
    entry = stats.calls[0]
    assert entry["probe"] == "rule:js"
    assert entry["fallback"] == ""
    assert entry["actual_ms"] < 2000
    assert stats.saved_ms == 2000 - entry["actual_ms"]


def test_site_rule_timeout_sleeps_out_and_marks_rule_timeout(monkeypatch):
    _pin_caller(monkeypatch)
    clk = FakeClock()
    page = RulePage(clock=clk, ok_default=False)
    stats = _install(page, clock=clk, site_rules=[_rule()])
    page.wait_for_timeout(2000)
    assert page.orig_calls == []
    assert clk.now == 2000                            # 超时=睡满剩余，绝不更长
    entry = stats.calls[0]
    assert entry["actual_ms"] == 2000
    assert entry["fallback"] == "rule-timeout"
    assert stats.saved_ms == 0


def test_site_rule_url_and_mask_gone_render_expected_js(monkeypatch):
    _pin_caller(monkeypatch)
    clk = FakeClock()
    url_page = RulePage(clock=clk, ok_default=True)
    stats = _install(url_page, clock=clk, site_rules=[
        _rule(kind="url", contains="saleOrderManagement/spm_sale_order")])
    url_page.wait_for_timeout(3000)
    assert "saleOrderManagement/spm_sale_order" in url_page.evals[0]
    assert stats.calls[0]["probe"] == "rule:url"

    clk2 = FakeClock()
    gone_page = RulePage(clock=clk2, ok_default=True)
    stats2 = _install(gone_page, clock=clk2, site_rules=[_rule(kind="mask_gone")])
    gone_page.wait_for_timeout(1500)
    assert "el-loading-mask" in gone_page.evals[0]
    assert stats2.calls[0]["probe"] == "rule:mask_gone"


def test_site_rule_mask_cycle_requires_mask_then_gone(monkeypatch):
    """两阶段：先等遮罩出现（证明请求已发出），再等遮罩消失；从未出现=睡满不赌。"""
    _pin_caller(monkeypatch)
    clk = FakeClock()
    page = RulePage(clock=clk, ok_seq=[True, True])
    stats = _install(page, clock=clk, site_rules=[
        _rule(kind="mask_cycle", appear_ms=800)])
    page.wait_for_timeout(5000)
    assert len(page.evals) == 2                       # appear 一拍 + gone 一拍
    assert "el-loading-mask" in page.evals[0]
    assert stats.calls[0]["probe"] == "rule:mask_cycle"
    assert stats.calls[0]["fallback"] == ""

    # 遮罩从未出现 → 只问 appear 窗口，随后睡满（请求窗口只能由固定等待兜）
    clk2 = FakeClock()
    never = RulePage(clock=clk2, ok_default=False)
    stats2 = _install(never, clock=clk2, site_rules=[
        _rule(kind="mask_cycle", appear_ms=800)])
    never.wait_for_timeout(5000)
    assert never.orig_calls == [4200]                 # 剩余预算交回**原始**等待（不是整块 sleep）
    assert clk2.now == 5000
    assert stats2.calls[0]["actual_ms"] == 5000       # 预算严格不越顶
    assert stats2.calls[0]["fallback"] == "rule-timeout"


def test_site_rule_never_kind_sleeps_out_via_original_wait(monkeypatch):
    """黑名单点（`never`）：原样睡满，且睡眠走**原始** `wait_for_timeout`。

    真机事故换来的边界：整块 `time.sleep(ms)` 不喂 Playwright 同步 API 的消息泵，
    6s 整块睡后 `Locator.evaluate` 会超时（同配置间歇复现）；原始等待与基线一致，
    既泵消息又不叠预算。
    """
    _pin_caller(monkeypatch)
    clk = FakeClock()
    page = RulePage(clock=clk, ok_default=True)
    stats = _install(page, clock=clk, site_rules=[_rule(kind="never")])
    page.wait_for_timeout(3000)
    assert page.evals == []                           # 黑名单点不做语义求值
    assert page.orig_calls == [3000]                  # 原始等待（会泵消息）
    assert clk.now == 3000
    entry = stats.calls[0]
    assert entry["probe"] == "rule:never"
    assert entry["actual_ms"] == entry["requested_ms"] == 3000
    assert stats.saved_ms == 0


def test_site_rule_miss_falls_back_to_generic_probe(monkeypatch):
    """规则不命中的点行为不变：仍走通用档（从未见忙→睡满 + no-busy-observed）。"""
    _pin_caller(monkeypatch, line=999)
    clk = FakeClock()
    page = ModelPage(clock=clk)
    stats = _install(page, clock=clk, site_rules=[_rule()])
    page.wait_for_timeout(3000)
    assert clk.now == 3000
    entry = stats.calls[0]
    assert entry["probe"] == "mask"
    assert entry["fallback"] == "no-busy-observed"


def test_site_rule_func_must_match_when_given():
    rules = [_rule(func="want")]
    assert legacy_fast._match_rule(rules, ("D:/x/login.py", 159, "other")) is None
    assert legacy_fast._match_rule(rules, ("D:/x/login.py", 159, "want")) is rules[0]
    # 文件名按 basename 匹配（caller 是全路径，旧仓目录不固定）
    assert legacy_fast._match_rule(rules, ("C:/other/login.py", 159, "want")) is rules[0]
    assert legacy_fast._match_rule(rules, ("C:/other/other.py", 159, "want")) is None


def test_site_rules_loader_skips_bad_entries(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [
        {"file": "a.py", "line": 1, "kind": "js", "js": "() => true"},
        {"file": "b.py", "line": 0, "kind": "js", "js": "() => true"},
        {"file": "c.py", "line": 2, "kind": "bogus"},
        {"file": "d.py", "line": 3, "kind": "url"},
        {"file": "e.py", "line": 4, "kind": "mask_cycle"},
        "not-a-dict",
    ]}), encoding="utf-8")
    rules = legacy_fast.load_site_rules(str(path))
    assert [r["file"] for r in rules] == ["a.py", "e.py"]


def test_site_rules_env_is_read_by_install(monkeypatch, tmp_path):
    _pin_caller(monkeypatch)
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [_rule()]}), encoding="utf-8")
    monkeypatch.setenv("ATLAS_FAST_SITE_RULES", str(path))
    clk = FakeClock()
    page = RulePage(clock=clk, ok_default=True)
    stats = _install(page, clock=clk)                 # 不显式传 site_rules
    page.wait_for_timeout(2000)
    assert stats.calls[0]["probe"] == "rule:js"
    assert stats.calls[0]["fallback"] == ""

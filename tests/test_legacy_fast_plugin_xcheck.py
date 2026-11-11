"""gap11 #21（xcheck 第二把尺）：垫片「不越界 / 不洗绿 / 截图边界」的跨切面对抗判据。

出处：`docs/gaps/gap11-legacy-fast-compat.md` §A 硬判据 1-4、§B.1/B.2/B.3、§D.1.1/D.1.7、
§E 三项度量，与 `docs/gaps/gap11-lead-rulings.md` G11-1（reload 语义洞三修法）+ lead
追加 1/2/4。零网络零浏览器零凭据；等待全部走注入的虚拟时钟，不真睡。

**与 #17 自测（tests/test_legacy_fast.py）的分工**——那条尺打「直调 install_fast_waits/
settle 的参数面」，本尺打它打不到的面（每格注释点名守哪条防线）：

| 格 | 守的路径 |
|---|---|
| test_x1 / test_x2 / test_x3 / test_x7 | §A 判据 1 的**墙钟轴**：量「虚拟时钟总推进」（不是实现自报的 stats.actual_ms），含页侧耗时反噬预算、类级多页序贯、tick 非整除算术扫描。**#33 后 x1/x3 的两支柱**：一直忙→恰睡满且 `fallback` 空、见忙后落定→真提前；「从未见忙」那一支归 x15/x16 与 #17 的 p0 组守，不在这里重复 |
| test_x4 | §A 判据 3 的**次数轴**：原始睡眠恰一次且金额为 remaining（不叠加、不全额）；对照腿先出忙信号，证「瞬态被消化后仍能提前」而不是「睡满也算过」 |
| test_x5 ~ test_x8 | 红线 #7 的**框架终态轴**：真 pytest 子进程跑，终态词从盘上 report 记录读——「垫片把失败吞成 skip/继续跑」在单测里看不见 |
| test_x9 | §E 判据位：失败步号**四态恒等**（never/quiet/**busy_quiet**/error）。`busy_quiet` 是唯一真走提前返回的端到端腿——P0 现场就是「提前把失败点从 7 挪到 1/3」，缺它=提前路径在本尺无人守 |
| test_x10 / test_x11 / test_x12 | §B.1 证据 ②/③ + §B.2 诚实边界：失败落盘**字节**仍在、`page.screenshot()` 次数 ON/OFF 相等（反「把截图短路成 no-op 假提速」）、allure 缺席零变化 |
| test_x13 / test_x14 | 静态面：垫片不持有把失败写成 skip 的手段（红线 #7 入口）、探针路径不调 `wait_for_timeout`（§A 判据 4 源文面）；两把尺都带真文件正对照 |
| test_x15 ~ test_x17 | G11-1 的 **env 轴**（三假页经 `ATLAS_FAST_MIN_MS`/`ATLAS_FAST_SETTLE_ANCHOR` 生效，非直调参）+ 闸关时三假页逐字走原始等待。**#33 后每腿都先出一拍忙碌信号**（否则「下限/截断/锚点门」在「从未见忙→睡满」下同答=装饰格），并各带 `fallback` 值区分「见过忙但门没开」（空串）与「从未见忙」（`no-busy-observed`） |
| test_x18 | §A 判据 2 的降级可见性：hook 未装上时 stats 记**实际生效**档位，不留「以为在判 xhr」的假账 |

**#33「先忙才许提前」的三档配对格在哪**：`tests/test_legacy_fast.py` 的
`test_p0_never_busy_sleeps_out_requested_all_three_tiers` /
`test_p0_busy_observation_is_per_call_not_sticky` /
`test_p0_protocol_shape_verdicts_are_never_early` /
`test_p0_early_exit_is_guarded_by_busy_seen_ast`（support-impl 名下，直调/包内 import 通道）。
本尺**不复制**那四条（#92 不留第二把尺），本尺的独立面是：standalone 按文件路径加载的垫片
（产物真实读法）、env→plugin→install 接线通道、类级补丁 + 多页序贯、真 pytest 子进程终态、
以及 compat 产物在阻断沙箱里的 vendored 行为（`tests/test_legacy_compat_xcheck.py::test_c4a`）。
新增判据前先扫这两份文件确认没人守过——这条流程本身在 #32 上栽过一次，别再犯。

诚实边界：①假页不执行 JS，钉的是档位构成 + 预算算术，真实 JS 求值/两遮罩并存归 #20
真机（同 #17 自测口径）；②test_x5 起的子进程包是 **#18 兼容产物 conftest 的简化替身**
（装配段将来应由产物承担），本处只钉垫片语义与框架终态；③mini-runner 按旧仓
`lib/runner.py:303-343` 形态**简化复刻**（lead 口径：关键面保持、不逐字拷整段）。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "atlas"
SHIM_FILE = SRC / "legacy_fast.py"
PLUGIN_FILE = SRC / "fastplugin.py"
OLD_REPO = Path(r"D:/GitHub/UI AUTO TEST/iservice")

# §E 判据常量：旧套件同链路真实失败点（证据 reports/r1r6_20261109/SUMMARY.md:21 的
# step7_FAIL_20261109_202147.png）。#20 真机 A/B 必须引用同一常量——不留第二份定义。
EXPECTED_FAIL_STEP = 7
STEP_MS = [100, 200, 300, 400, 500, 600]          # 失败前的通过步（len == EXPECTED-1）
FAIL_WAIT_MS = 8000                                # 失败步自己那次固定睡眠（旧仓形态）
TOTAL_REQUESTED_MS = sum(STEP_MS) + FAIL_WAIT_MS

ENV_KEYS = ("ATLAS_FAST_WAITS", "ATLAS_FAST_NO_STEP_SHOTS", "ATLAS_FAST_WAITS_STATS",
            "ATLAS_FAST_MIN_MS", "ATLAS_FAST_SETTLE_ANCHOR")


# ---------------------------------------------------------------------------
# 盘上源文件的 standalone 加载（= 产物运行期真实读法：无包上下文）
# ---------------------------------------------------------------------------


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, "源文件读不到：%s" % path
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture()
def shim():
    """legacy_fast.py 以文件路径加载（不走 `import atlas.legacy_fast`）。

    守的形态：§C.3 产物零 import atlas——垫片必须能脱离包上下文运行（相对 import
    在无包时当场炸，这条通道本身就是判据）。
    """
    name = "_xcheck_shim_under_test"
    mod = _load(SHIM_FILE, name)
    mod._INSTALLED.clear()
    yield mod
    mod._INSTALLED.clear()
    sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# 虚拟时钟与假页（本尺独立实现）
# ---------------------------------------------------------------------------


class VClock:
    """注入缝 = `install_fast_waits(clock=...)`；总推进即「墙钟」的离线替身。"""

    def __init__(self):
        self.now = 0.0
        self.slept: list = []

    def now_ms(self) -> float:
        return self.now

    def sleep_ms(self, ms) -> None:
        if ms is not None and float(ms) > 0:
            self.now += float(ms)
            self.slept.append(float(ms))

    def advance(self, ms) -> None:
        self.now += float(ms)


class Page:
    """静默条件的语义模型（独立于 #17 的模型，各写一份才能互相核对）。

    **#33 之后协议形态**：判据 JS 由 `build_condition` 拼成**一把多段函数**，求值结果是
    **状态码**而不是布尔——忙碌码 `busy-mask`/`busy-xhr`/`busy-mutation` 三选一比谁先命中，
    门码 `not-ready`/`not-anchored`/`no-hook`/`no-activity`，最后才是 `quiet`。
    布尔在本尺里已**不再是合法返回值**（实现侧 `_classify_verdict` 把非字符串 fail-close
    成门=继续等），所以本模型按**拼装次序**答码，而不是按「这段 JS 是哪个关键词」答布尔
    ——次序本身是判据的一部分（「此刻仍在忙」必须被记成忙碌观测，不能被后面的门抢先答
    「还没静默」，那是 P0 失败点前移的正解形态）。

    scenario：quiet=**整段从未观测到忙**（判据 2 的 fail-safe 腿，应睡满）/
    never=一直忙（忙碌码恒发，永不转静默）/ error=求值抛未知错 /
    unrecoverable=Target closed 族 / assertion=求值里抛 AssertionError（lead 追加 1）。
    `script`：**逐拍**覆盖，每拍先回落构造基线再套当前拍（拍号用尽=纯基线）。
    `script=[{"masks": True}]` 因此真的等于「第一拍忙、之后静默」=唯一合法的提前放行
    路径。回落语义不是讲究：setattr 若持久，第一拍的忙会粘到最后一拍，页面永不转静默，
    于是所有「提前」断言都在「睡满」上同答——剧本坏了比产品坏了更难查，因为它让判据
    看起来只是变慢了（lead 2026-11-10 02:3x 抓到的正是这个形态）。
    cost_ms=每次 evaluate 的页侧耗时。
    """

    _SCRIPTABLE = ("masks", "pending", "mutated", "mutated_ago", "ready",
                   "anchor_visible", "hook_ok")

    def __init__(self, *, clock=None, scenario="quiet", cost_ms=0, ready=True,
                 masks=False, pending=0, mutated=True, mutated_ago=9_999,
                 anchor_visible=True, hook_ok=True, script=None):
        self.clock = clock
        self.scenario = scenario
        self.cost_ms = cost_ms
        self.ready = ready
        self.masks = masks
        self.pending = pending
        self.mutated = mutated
        self.mutated_ago = mutated_ago
        self.anchor_visible = anchor_visible
        self.hook_ok = hook_ok
        self._base = {key: getattr(self, key) for key in self._SCRIPTABLE}
        self._ticks = list(script or [])
        if self._ticks and scenario != "quiet":
            raise AssertionError("scenario=%r 与 script 不能同用——剧本会被静默忽略，"
                                 "那条腿就成了装饰" % scenario)
        self.orig_sleeps: list = []        # 原始 wait_for_timeout 收到的 ms
        self.conditions: list = []
        self.screenshots = 0
        self._seq: list = []

    def _apply_tick(self):
        """剧本的一拍：回落基线 → 套当前拍覆盖（用尽即纯基线）。"""
        for key in self._SCRIPTABLE:
            setattr(self, key, self._base[key])
        if self._ticks:
            for key, val in self._ticks.pop(0).items():
                if key not in self._SCRIPTABLE:
                    raise AssertionError(
                        "script 键 %r 不在可剧本字段 %s 内——拼错的剧本会静默失效"
                        % (key, self._SCRIPTABLE))
                setattr(self, key, val)

    def wait_for_timeout(self, ms=0):
        self.orig_sleeps.append(ms)
        if self.clock is not None:
            try:
                self.clock.advance(max(0, int(ms)))
            except (TypeError, ValueError):
                pass

    def screenshot(self):
        self.screenshots += 1
        return b"\x89PNG\r\n\x1a\nXCHK"

    def wait_for_function(self, *a, **kw):        # §A 判据 4：被叫到即违规
        raise AssertionError("垫片不得用 wait_for_function 轮询")

    def add_init_script(self, js):
        return None

    def evaluate(self, js):
        if self.clock is not None:
            self.clock.advance(self.cost_ms)
        if "XMLHttpRequest.prototype" in js or "MutationObserver" in js:
            return None
        if js.startswith("() => !!"):             # hook 在场核对仍是布尔通道
            return self.hook_ok
        self.conditions.append(js)
        if self._seq:
            exc = self._seq.pop(0)
            if exc is not None:
                raise exc                           # 抛错拍不消耗剧本（剧本描述的是判据答复）
        self._apply_tick()                          # 每拍：回落基线 + 当前拍覆盖
        return self._judge(js)

    def arm(self, *excs):
        self._seq.extend(excs)
        return self

    def _judge(self, js: str) -> str:
        """按 `build_condition` 的**拼装次序**答状态码（忙碌段 → 门 → quiet）。

        次序是判据的一部分：「此刻仍在忙」必须先于门被答出，否则无遮罩的等待会被门
        抢先读成「还没静默」=P0 失败点前移的正解形态（#33 收紧本体）。
        """
        if self.scenario == "error":
            raise RuntimeError("unknown probe failure in page")
        if self.scenario == "unrecoverable":
            raise RuntimeError("Target page, context or browser has been closed")
        if self.scenario == "assertion":
            raise AssertionError("用例断言在探针求值路径上抛出")
        if self.scenario == "never":              # 一直忙：本档最高的忙碌码恒发
            return self._busy_code(js)
        if self.scenario != "quiet":
            raise AssertionError("未知 scenario %r——本尺不静默兜底" % (self.scenario,))

        # ── 忙碌段在前 ──
        if ".el-loading-mask" in js and self.masks:
            return "busy-mask"
        if "__atlasFastXhr" in js:
            if not self.hook_ok:                  # hook 不在=不可评估，也不算忙碌观测
                return "no-hook"
            if self.pending > 0:
                return "busy-xhr"
        if "__atlasFastObserver" in js:
            if not self.hook_ok or not self.mutated:
                return "no-activity"              # 「装钩后一直没动」不算忙碌观测
            m = re.search(r"performance\.now\(\) - window\.__atlasFastLast\) >= (\d+)", js)
            if m and self.mutated_ago < int(m.group(1)):
                return "busy-mutation"            # 真变动但静默窗未满=仍在忙
        # ── 门在中 ──
        if "document.readyState" in js and not self.ready:
            return "not-ready"
        m = re.search(r"querySelectorAll\((\"(?:[^\"\\]|\\.)*\")\)", js)
        if m and json.loads(m.group(1)) != ".el-loading-mask" and not self.anchor_visible:
            return "not-anchored"
        # ── 静默只在最后出 ──
        return "quiet"

    def _busy_code(self, js: str) -> str:
        """scenario=never 用：挑本档拼装里存在的忙碌段答一个忙碌码。"""
        if ".el-loading-mask" in js:
            return "busy-mask"
        if "__atlasFastXhr.pending" in js:
            return "busy-xhr"
        if "__atlasFastLast" in js:
            return "busy-mutation"
        raise AssertionError("never 档拿不到忙碌段判据（拼装次序变了？）：%s" % js[:160])


def _install(mod, page, *, clock, **kw):
    kw.setdefault("min_ms", 0)
    return mod.install_fast_waits(page, clock=clock, **kw)


# ---------------------------------------------------------------------------
# §A 判据 1：墙钟轴
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("requested", [25, 26, 100, 999, 1000, 4013, 5000, 60_000])
def test_x1_c1_budget_sweep_wallclock_never_exceeds_requested(shim, requested):
    """判据 1「绝不更长」的算术扫描（含非 tick 整除）：总推进恰=req，不叠原睡眠。

    守 legacy_fast.py 的「末拍不足一拍就睡满不再探」越顶防线——25ms 步长取整最容易
    多睡一拍。本尺不信实现自报的 actual_ms，直接量注入时钟。
    **#33 收紧后的两支柱**：①主腿=一直忙（busy 码恒发）永不转静默 → 恰睡满 req，且
    `fallback` **不得**是 `no-busy-observed`（见过忙=正常路径，两支柱混了 A/B 就读反）；
    ②正向对照=**先出一拍忙信号再转静默**（唯一合法的提前放行形态）→ 真提前且省下来。
    对照不许写成「全程 quiet 也提前」——那是被本次撤掉的旧语义；也不许写成「什么都不省」
    的同答形态（§G.5 第 7 条：no-op 与正确收紧全体同答）。
    """
    clk = VClock()
    page = Page(clock=clk, scenario="never")
    stats = _install(shim, page, clock=clk)
    page.wait_for_timeout(requested)
    assert clk.now <= requested
    assert page.orig_sleeps == []
    assert stats.calls[0]["actual_ms"] == requested      # 也不凭空缩短
    assert stats.calls[0]["fallback"] == ""              # 一直忙≠没见过忙，两类事实不混
    if requested >= 2 * shim._TICK_MS:
        assert clk.now == requested                      # 等满，一毫秒不多

    clk2 = VClock()
    ok = Page(clock=clk2, script=[{"masks": True}])      # 第一拍遮罩在场=观测到忙
    stats2 = _install(shim, ok, clock=clk2)
    ok.wait_for_timeout(requested)
    assert ok.orig_sleeps == []
    if requested >= 2 * shim._TICK_MS:
        assert 0 < clk2.now < requested                  # 真提前，但第一拍必是忙（非 0 推进）
        assert stats2.saved_ms == requested - clk2.now > 0
        assert stats2.calls[0]["fallback"] == ""
    else:
        assert clk2.now <= requested                     # 预算不足两拍：没有提前的物理空间


def test_x2_c1_page_cost_beyond_budget_adds_no_shim_sleep(shim):
    """页侧求值自身反噬预算（cost_ms > requested）：垫片**不得再补任何睡眠**。

    「≤ requested」唯一守不住的方向是页执行本身慢——判据要钉的是垫片不添油加醋：
    回退睡的是 remaining（此处 0），睡眠列表不许出现正数补睡；账面也不许把页侧
    6000ms 记成「省了」。正向对照：页侧零耗时时同一预算正常等满（等待确实在垫片侧）。
    """
    clk = VClock()
    page = Page(clock=clk, scenario="never", cost_ms=6000)
    stats = _install(shim, page, clock=clk)
    page.wait_for_timeout(5000)
    assert clk.slept == []
    assert page.orig_sleeps == []
    assert stats.calls[0]["actual_ms"] <= 5000
    assert stats.saved_ms >= 0

    clk2 = VClock()
    p2 = Page(clock=clk2, scenario="never")
    _install(shim, p2, clock=clk2)
    p2.wait_for_timeout(5000)
    assert clk2.slept and sum(clk2.slept) == 5000


def test_x3_c1_class_level_multi_page_sequence_budget(shim):
    """类级补丁 + 同进程多页序贯：Σactual ≤ Σrequested 恒真（§E 度量 3 聚合轴）。

    守「逐笔合格但总账越界」形态。正向对照=其中一页**先观测到忙、再转静默**后总账严格
    下降——没有这一格，不变量恒等号成立也是绿的（假绿）。uninstall 幂等同格核。
    #33 之后两支柱分开可见才算钉住：从未见忙的页睡满并计入 `no_busy_observed`，见过忙
    的页提前且 `fallback` 空。混成一个口径，A/B 的 `fallbacks` / `no_busy_observed`
    就会互读（把 fail-safe 睡满当探针大面积回退=方向反了）。
    """
    class PageClass:
        def __init__(self, busy_first=False):
            self.orig_sleeps: list = []
            self._busy_first = busy_first       # 第一拍遮罩在场=合法的忙碌观测

        def wait_for_timeout(self, ms=0):
            self.orig_sleeps.append(ms)

        def evaluate(self, js):
            if js.startswith("() => !!"):       # hook 在位核对仍是布尔通道
                return True
            if self._busy_first:
                self._busy_first = False
                return "busy-mask"
            return "quiet"

        def screenshot(self):
            return b"png"

    clk = VClock()
    original_method = PageClass.wait_for_timeout
    stats = shim.install_fast_waits(PageClass, clock=clk, min_ms=0, probe="mask")
    p1, p2 = PageClass(), PageClass()
    for ms in (1000, 2000, 3000):
        p1.wait_for_timeout(ms)
    p2.wait_for_timeout(500)
    agg = stats.aggregate()
    assert agg["original_ms_total"] == 6500
    assert agg["actual_ms_total"] <= agg["original_ms_total"]
    assert agg["calls"] == 4 and sum(s["calls"] for s in agg["by_stage"].values()) == 4
    assert set(agg["by_stage"]) == {"mask"}
    assert clk.slept and p1.orig_sleeps == [] and p2.orig_sleeps == []
    assert agg["no_busy_observed"] == 4 and agg["fallbacks"] == 0    # 四笔全是 fail-safe 睡满
    # 正向对照：观测到忙→转静默 的那一笔才允许提前，总账随之严格下降
    p3 = PageClass(busy_first=True)
    p3.wait_for_timeout(4000)
    assert stats.calls[-1]["actual_ms"] == shim._TICK_MS < 4000
    assert stats.calls[-1]["fallback"] == ""                  # 提前≠回退，也≠睡满
    agg2 = stats.aggregate()
    assert agg2["no_busy_observed"] == 4                      # 提前那笔不计进睡满
    assert agg2["actual_ms_total"] < agg2["original_ms_total"]
    assert shim.uninstall(PageClass) == 1
    assert PageClass.wait_for_timeout is original_method
    assert shim.uninstall(PageClass) == 0                 # 幂等
    assert PageClass().orig_sleeps == []


def test_x4_c1_probe_error_falls_back_original_exactly_once(shim):
    """§A 判据 3：未知异常 → 原始睡眠**恰一次**、金额=remaining、标 fallback。

    同时否掉两形态：①不回退（假静默放行 #102 同族）②全额/多次回退（判据 1 破口）。
    正向对照=导航瞬态不是异常：预算内继续轮询、原始睡眠零调用；#33 之后对照页还要
    「先见忙再落定」才谈得上提前——若只答 quiet，`fallback` 会变成 `no-busy-observed`
    （fail-safe 睡满），那条腿就不再是「瞬态被正确消化」的证据了。
    """
    clk = VClock()
    page = Page(clock=clk, cost_ms=120)
    page.arm(RuntimeError("unknown probe failure"))
    stats = _install(shim, page, clock=clk)
    page.wait_for_timeout(5000)
    assert page.orig_sleeps == [4880]
    entry = stats.calls[0]
    assert entry["fallback"] == "probe-error:RuntimeError"
    assert entry["actual_ms"] <= entry["requested_ms"]
    assert clk.now <= 5000

    clk2 = VClock()
    t = Page(clock=clk2, script=[{"masks": True}])
    t.arm(RuntimeError("Execution context was destroyed, most likely because of a "
                       "navigation"))
    stats2 = _install(shim, t, clock=clk2)
    t.wait_for_timeout(1000)
    assert t.orig_sleeps == []
    assert stats2.calls[0]["fallback"] == ""              # 既没降级回退，也不是 fail-safe 睡满
    assert 0 < clk2.now < 1000                            # 瞬态之后继续轮询并真提前（第二拍见忙）


# ---------------------------------------------------------------------------
# 红线 #7 框架终态轴：真 pytest 子进程（lead 派单第 2 条）
# ---------------------------------------------------------------------------

_SUB_CONFTEST = '''
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent
SHIM_PATH = os.environ.get("XCHECK_SHIM_PATH", "")
SCENARIO = os.environ.get("XCHECK_SCENARIO", "quiet")
MIN_MS = int(os.environ.get("XCHECK_MIN_MS", "0"))
OUTCOMES = PKG / "outcomes.jsonl"
STEPS = PKG / "steps.jsonl"
SHOTS = PKG / "screenshots"


class VClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def now_ms(self):
        return self.now

    def sleep_ms(self, ms):
        if ms is not None and float(ms) > 0:
            self.now += float(ms)
            self.slept.append(float(ms))


CLOCK = VClock()


class FakePage:
    """旧套件页对象替身：原始睡眠只记账不真睡（出处形态 lib/runner.py 的调用面）。

    **#33 协议**：判据求值答**状态码**，布尔在这条通道被实现侧 fail-close 成「不可评估」
    （=继续等到预算耗尽）。scenario 语义：never=一直忙 / quiet=从未见忙 /
    busy_quiet=第一拍见忙、随后静默（唯一合法的提前放行形态）/ error / assertion。
    """

    def __init__(self):
        self.sleeps = []
        self.screenshot_calls = 0

    def wait_for_timeout(self, ms=0):
        self.sleeps.append(ms)

    def screenshot(self):
        self.screenshot_calls += 1
        return b"\\x89PNG\\r\\n\\x1a\\nFAKEBODY"

    def evaluate(self, js):
        if SCENARIO == "never":
            return "busy-mask"
        if SCENARIO == "busy_quiet":
            if not hasattr(self, "_seen"):
                self._seen = 0
            self._seen += 1                     # 第一拍遮罩在场，之后转静默
            return "busy-mask" if self._seen == 1 else "quiet"
        if SCENARIO == "error":
            raise RuntimeError("unknown probe failure in page")
        if SCENARIO == "assertion":
            raise AssertionError("用例断言在探针求值路径上抛出")
        return "quiet"


_shim = None
_shim_load_error = ""
if SHIM_PATH:
    try:
        _spec = importlib.util.spec_from_file_location("_xcheck_shim_sub", SHIM_PATH)
        _shim = importlib.util.module_from_spec(_spec)
        sys.modules[_spec.name] = _shim
        _spec.loader.exec_module(_shim)
    except Exception as _exc:
        _shim_load_error = type(_exc).__name__
        _shim = None

SHIM_INSTALLED = False
SHIM_WARN = ""
if os.environ.get("ATLAS_FAST_WAITS", "").strip():
    if _shim is None:
        SHIM_WARN = ("WARN: fast waits 注入失败，旧用例按原固定睡眠跑: "
                     + (_shim_load_error or "no shim"))
    else:
        try:
            _shim.install_fast_waits(FakePage, clock=CLOCK, probe="mask",
                                     min_ms=MIN_MS)
            SHIM_INSTALLED = True
        except Exception as _exc:
            SHIM_WARN = "WARN: fast waits 注入失败: " + type(_exc).__name__
if SHIM_WARN:
    print(SHIM_WARN)


def _record(path, row):
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\\n")


def run_steps(page, steps):
    """旧仓 runner 失败面的简化复刻（出处 lib/runner.py:303-318 与 :324-343）。

    简化处：_allure 用页对象记账代替、_save_fail_screenshot 只保留 write_bytes。
    保持的关键面：成功步 attach 名 step<N>_result、失败步 attach 名 step<N>_FAIL 且
    字节另落盘、异常原样 `raise AssertionError(...) from e`。
    """
    for n, fn in enumerate(steps, 1):
        try:
            fn()
            page.attach = getattr(page, "attach", [])
            page.attach.append("step" + str(n) + "_result")
            _record(STEPS, {"step": n, "outcome": "ok"})
        except AssertionError as exc:
            page.attach = getattr(page, "attach", [])
            page.attach.append("step" + str(n) + "_FAIL")
            SHOTS.mkdir(parents=True, exist_ok=True)
            (SHOTS / ("step" + str(n) + "_FAIL.png")).write_bytes(page.screenshot())
            _record(STEPS, {"step": n, "outcome": "fail", "exc": type(exc).__name__})
            raise AssertionError("[断言失败] 步骤 " + str(n) + " 结果不符") from exc
        except Exception as exc:
            _record(STEPS, {"step": n, "outcome": "fail", "exc": type(exc).__name__})
            raise RuntimeError("[执行失败] 步骤 " + str(n)) from exc


@pytest.fixture()
def page():
    return FakePage()


def pytest_runtest_logreport(report):
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    _record(OUTCOMES, {"nodeid": report.nodeid, "when": report.when,
                       "outcome": report.outcome,
                       "crash": (crash.message if crash else "")})


def pytest_sessionfinish(session, exitstatus):
    _record(PKG / "session.jsonl", {
        "exitstatus": int(exitstatus),
        "shim_installed": SHIM_INSTALLED,
        "warn": SHIM_WARN,
        "clock_now": CLOCK.now,
        "scenario": SCENARIO,
        "waits_env": os.environ.get("ATLAS_FAST_WAITS", "")})
'''

_SUB_TEST = '''
from conftest import run_steps

STEP_MS = %s
FAIL_WAIT_MS = %d


def test_bridge_like_case(page):
    steps = [(lambda ms: (lambda: page.wait_for_timeout(ms)))(m) for m in STEP_MS]

    def failing():
        page.wait_for_timeout(FAIL_WAIT_MS)
        assert False, "业务断言不符"

    steps.append(failing)
    steps.append(lambda: page.wait_for_timeout(5000))   # 失败后不该被跑到
    run_steps(page, steps)
'''


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ATLAS_", "DEMO_", "ISC_", "CDP_", "CASE_"))
           and k not in ENV_KEYS}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def _build_pkg(tmp_path, *, scenario, shim_path, waits, min_ms="0"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "conftest.py").write_text(_SUB_CONFTEST, encoding="utf-8")
    (tmp_path / "test_case.py").write_text(
        _SUB_TEST % (repr(STEP_MS), FAIL_WAIT_MS), encoding="utf-8")
    for f in tmp_path.glob("*.py"):
        ast.parse(f.read_text(encoding="utf-8"))       # 生成物自身的坏档防线（wave1 教训）
    env = _clean_env(XCHECK_SCENARIO=scenario, XCHECK_SHIM_PATH=str(shim_path),
                     XCHECK_MIN_MS=min_ms)
    if waits is not None:
        env["ATLAS_FAST_WAITS"] = waits
    return tmp_path, env


def _run_pkg(tmp_path, env):
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s",
                        "-p", "no:cacheprovider", str(tmp_path)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(tmp_path))

    def _rows(name):
        p = tmp_path / name
        if not p.is_file():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
                if l.strip()]

    return r, _rows("outcomes.jsonl"), _rows("steps.jsonl"), _rows("session.jsonl")


def _terminals(rows):
    return {r["when"]: r["outcome"] for r in rows}, any(
        r["outcome"] == "skipped" for r in rows)


def test_x5_c2_wait_then_assertion_failure_stays_failed_with_shim_on(tmp_path):
    """lead 派单第 2 条本体：垫片在场，「wait 后断言失败」终态仍是 failed。

    不许变形：skipped（红线 #7 的洗绿入口）/ error（收集期崩）/ passed。判据读盘上
    report 记录，不信实现自报。前置断言 shim_installed=True——空跑的「没变形」不算数。
    """
    pkg, env = _build_pkg(tmp_path / "on", scenario="quiet", shim_path=SHIM_FILE,
                          waits="1")
    r, rows, steps, sess = _run_pkg(pkg, env)
    assert sess and sess[0]["shim_installed"] is True, sess
    by_when, saw_skip = _terminals(rows)
    assert by_when.get("call") == "failed", by_when
    assert not saw_skip, rows
    assert by_when.get("setup") == "passed", by_when
    assert r.returncode == 1, r.stdout[-800:]
    assert "1 failed" in r.stdout and "1 skipped" not in r.stdout
    assert "1 passed" not in r.stdout
    fails = [s for s in steps if s["outcome"] == "fail"]
    assert [f["exc"] for f in fails] == ["AssertionError"]


def test_x6_c2_probe_error_and_assertion_never_degraded(tmp_path):
    """两条降级形态分开钉：①未知异常回退后失败仍在原步（7）；②求值路径上的
    AssertionError **不被当探针故障吞掉**（lead 追加 1 / legacy_fast.py:603-608）。

    ②的判据形态是「失败步号=1」：若实现把它当探针错误降级，等待会正常返回、用例
    继续跑到 step 7 才炸——失败点被推迟=真因漂到远处（#102/#133 同族）。两态都不得
    出现 skipped。
    """
    pkg, env = _build_pkg(tmp_path / "err", scenario="error", shim_path=SHIM_FILE,
                          waits="1")
    r, rows, steps, sess = _run_pkg(pkg, env)
    by_when, saw_skip = _terminals(rows)
    assert by_when.get("call") == "failed" and not saw_skip, by_when
    assert r.returncode == 1
    assert [s["step"] for s in steps if s["outcome"] == "fail"] == [EXPECTED_FAIL_STEP]

    pkg2, env2 = _build_pkg(tmp_path / "asrt", scenario="assertion",
                            shim_path=SHIM_FILE, waits="1")
    r2, rows2, steps2, sess2 = _run_pkg(pkg2, env2)
    by2, skip2 = _terminals(rows2)
    assert by2.get("call") == "failed" and not skip2, by2
    assert r2.returncode == 1
    fails2 = [s for s in steps2 if s["outcome"] == "fail"]
    assert [f["step"] for f in fails2] == [1], steps2     # 就地抛，不降级后漂
    assert fails2[0]["exc"] == "AssertionError"


def test_x7_c2_shim_on_off_terminal_state_identical(tmp_path):
    """差分判据：同一用例在垫片 ON/OFF 两态的框架终态**逐项相等**。

    「不洗绿」的可核形态不是「failed 看起来还在」，而是两态等值 + 等待归属变了
    （ON 落在垫片侧、OFF 落回原始固定睡眠）。正向对照=两态确实走不同路径，否则
    「等值」恒真。附 §A 判据 1 在真会话上的总账：ON 永不静默时总推进恰=Σrequested。
    """
    pkg_on, env_on = _build_pkg(tmp_path / "on", scenario="never",
                                shim_path=SHIM_FILE, waits="1")
    ro, rows_o, steps_o, sess_o = _run_pkg(pkg_on, env_on)
    pkg_off, env_off = _build_pkg(tmp_path / "off", scenario="never",
                                  shim_path=SHIM_FILE, waits=None)
    rf, rows_f, steps_f, sess_f = _run_pkg(pkg_off, env_off)

    assert _terminals(rows_o)[0] == _terminals(rows_f)[0]
    assert steps_o == steps_f
    assert ro.returncode == rf.returncode == 1
    assert sess_o[0]["shim_installed"] is True
    assert sess_f[0]["shim_installed"] is False           # 路径确实不同
    assert sess_o[0]["clock_now"] == TOTAL_REQUESTED_MS   # 不多不少
    assert sess_f[0]["clock_now"] == 0                   # OFF 不经垫片时钟


def test_x8_c2_injection_failure_warns_and_case_still_runs_old_way(tmp_path):
    """垫片源不可用 → WARN 一行、用例按**原固定睡眠**跑、终态仍 failed。

    守两面：「注入坏=整场崩」（#103 白烧会话同族）与「注入失败被静默当成已优化」
    （快没生效但读的人以为快了）。正向对照=WARN 文案在 stdout 且终态没变成 error。
    """
    missing = tmp_path / "no_such_shim.py"
    pkg, env = _build_pkg(tmp_path / "inj", scenario="quiet", shim_path=missing,
                          waits="1")
    r, rows, steps, sess = _run_pkg(pkg, env)
    assert sess[0]["shim_installed"] is False
    assert sess[0]["warn"].startswith("WARN: fast waits 注入失败")
    assert "WARN: fast waits" in r.stdout
    by_when, saw_skip = _terminals(rows)
    assert by_when.get("call") == "failed" and not saw_skip
    assert r.returncode == 1
    assert [s["outcome"] for s in steps] == ["ok"] * (EXPECTED_FAIL_STEP - 1) + ["fail"]


def test_x9_c8_failed_step_never_moves_across_probe_modes(tmp_path):
    """§E 判据位：失败点必须仍是 step 7——四态恒等。

    前移或后移都是「更快=行为变了」的现场，不许当成绩。读三处一致证据：步骤记录、
    attach 名、失败落盘文件名；step 8 的任何痕迹都判红。#20 真机 A/B 引用同一常量
    EXPECTED_FAIL_STEP（不留第二份步号定义）。
    **#33 之后第四态 `busy_quiet` 是本格的要害**：它是四态里唯一真走「提前返回」的那条
    腿——P0 的现场正是「提前把失败点从 step 7 挪到 step 1/3」，所以「提前合法化了」之后
    失败点仍不许动，只能由这条端到端腿证。只留 never/quiet/error 三态=全走睡满，
    等于没测提前路径（判据自身成盲区）。
    """
    assert len(STEP_MS) == EXPECTED_FAIL_STEP - 1          # 夹具形态自证
    for scenario in ("never", "quiet", "busy_quiet", "error"):
        pkg, env = _build_pkg(tmp_path / scenario, scenario=scenario,
                              shim_path=SHIM_FILE, waits="1")
        r, rows, steps, sess = _run_pkg(pkg, env)
        fails = [s["step"] for s in steps if s["outcome"] == "fail"]
        assert fails == [EXPECTED_FAIL_STEP], (scenario, steps)
        oks = [s["step"] for s in steps if s["outcome"] == "ok"]
        assert oks == list(range(1, EXPECTED_FAIL_STEP)), (scenario, oks)
        assert not any(s["step"] > EXPECTED_FAIL_STEP for s in steps)
        assert not (pkg / "screenshots" / "step8_FAIL.png").exists()
        assert _terminals(rows)[0].get("call") == "failed", scenario
        if scenario == "busy_quiet":
            # 对照自证：这一态必须真的省过——否则上面四态同答是恒绿
            assert sess[0]["shim_installed"] is True, sess
            assert sess[0]["clock_now"] < TOTAL_REQUESTED_MS, sess
        elif scenario in ("never", "quiet"):
            assert sess[0]["clock_now"] == TOTAL_REQUESTED_MS, (scenario, sess)
        # error 态不比 clock_now：那一笔时间落在**原始睡眠**里，而假页的
        # wait_for_timeout 只记账不推进虚拟时钟（夹具形态，不是产品结论）——
        # 拿它当判据就是把夹具写成交货口径。失败步号那一行已经守住了本命题。


# ---------------------------------------------------------------------------
# §B 截图抑制：字节面与计数面（lead 要求分两条，不合成恒真）
# ---------------------------------------------------------------------------


def _mini_runner(allure_mod, page, shots_dir, steps):
    """按旧仓 `lib/runner.py:303-343` 形态跑步骤，返回失败落盘文件列表。"""
    files = []
    for n, fn in enumerate(steps, 1):
        try:
            fn()
            allure_mod.attach(page.screenshot(), name="step%d_result" % n,
                              attachment_type=allure_mod.attachment_type.PNG)
        except AssertionError:
            allure_mod.attach(page.screenshot(), name="step%d_FAIL" % n,
                              attachment_type=allure_mod.attachment_type.PNG)
            shots_dir.mkdir(parents=True, exist_ok=True)
            p = shots_dir / ("step%d_FAIL_xcheck.png" % n)
            p.write_bytes(page.screenshot())                # :312-321 字节直写
            files.append(p)
            raise
    return files


def _plugin_loaded(monkeypatch, name, **env):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return mod


def test_x10_c3b_fail_screenshot_bytes_survive_suppression(tmp_path, monkeypatch):
    """派单第 3 条·第二段（独立成测）：`_save_fail_screenshot` 的**字节落盘面**不受抑制。

    判据是盘上文件与 sha256，不是计数——§B.1 证据 ② 说抑制器碰不到它，本尺验收
    「真碰不到」：失败 PNG 仍在、字节等于页侧正文、FAIL 附件仍在 passed 计数里。
    """
    recorded = []
    allure_mod = types.ModuleType("allure")
    allure_mod.attach = lambda *a, **kw: recorded.append(kw.get("name"))
    allure_mod.attachment_type = types.SimpleNamespace(PNG="PNG")
    monkeypatch.setitem(sys.modules, "allure", allure_mod)
    plugin = _plugin_loaded(monkeypatch, "_xcheck_plugin_bytes",
                            **{"ATLAS_FAST_NO_STEP_SHOTS": "1"})
    try:
        plugin.pytest_configure(types.SimpleNamespace())
        assert plugin._STATE["shots_installed"] is True
        page = Page(clock=None)
        shots = tmp_path / "screenshots"

        def boom():
            assert False, "业务断言不符"

        with pytest.raises(AssertionError):
            _mini_runner(allure_mod, page, shots, [lambda: None, boom])
        files = sorted(shots.glob("step*_FAIL_xcheck.png"))
        assert [f.name for f in files] == ["step2_FAIL_xcheck.png"]
        body = files[0].read_bytes()
        assert hashlib.sha256(body).hexdigest() == \
            hashlib.sha256(b"\x89PNG\r\n\x1a\nXCHK").hexdigest()
        assert recorded == ["step2_FAIL"]                  # 失败附件没被抑制
        assert plugin._SHOTS["dropped"] == 1               # step1_result 被丢
        assert plugin._SHOTS["passed"] == 1
    finally:
        plugin.pytest_unconfigure(types.SimpleNamespace())
        sys.modules.pop(plugin.__name__, None)


def test_x11_c3c_screenshot_call_count_identical_on_off(monkeypatch):
    """§B.2 诚实边界的反作弊格：抑制 ON/OFF 两态 `page.screenshot()` 次数**必须相等**。

    省的是 attach IO 与报告体积，不是 PNG 编码调用；若实现把 screenshot 短路成
    no-op 来「更快」，§E 度量 2 就是虚报，本格当场红。名字轴由 #17 的
    tests/test_legacy_fast.py:945 守（本尺不重复）。
    """
    counts, dropped, reached, wrapped = {}, {}, {}, {}
    for mode in ("off", "on"):
        recorded = []
        allure_mod = types.ModuleType("allure")
        allure_mod.attach = lambda *a, **kw: recorded.append(kw.get("name"))
        allure_mod.attachment_type = types.SimpleNamespace(PNG="PNG")
        original_attach = allure_mod.attach            # configure 前捕获本体
        monkeypatch.setitem(sys.modules, "allure", allure_mod)
        env = {"ATLAS_FAST_NO_STEP_SHOTS": "1"} if mode == "on" else {}
        plugin = _plugin_loaded(monkeypatch, "_xcheck_plugin_%s" % mode, **env)
        try:
            plugin.pytest_configure(types.SimpleNamespace())
            page = Page(clock=None)
            for n in (1, 2, 3):
                allure_mod.attach(page.screenshot(), name="step%d_result" % n,
                                  attachment_type=allure_mod.attachment_type.PNG)
            counts[mode] = page.screenshots
            dropped[mode] = dict(plugin._SHOTS)
            reached[mode] = len(recorded)              # 真进 allure 的张数
            wrapped[mode] = allure_mod.attach is not original_attach
            assert page.screenshots == 3                   # 调用没被吞成 no-op
        finally:
            plugin.pytest_unconfigure(types.SimpleNamespace())
            sys.modules.pop(plugin.__name__, None)
    assert counts["on"] == counts["off"] == 3          # §B.2：编码调用一次没少
    assert reached["off"] == 3 and reached["on"] == 0   # 省的是 attach IO/报告体积
    assert dropped["on"] == {"dropped": 3, "passed": 0}
    # off 态闸根本不开：attach 仍是原函数本体（「不装」的直接证据，不靠计数反推），
    # 计数面因此全 0——包装器没上，就没有账（判据⑧零行为变化的形态面）。
    assert wrapped == {"on": True, "off": False}
    assert dropped["off"] == {"dropped": 0, "passed": 0}


def test_x12_c3_allure_absent_means_suppressor_not_installed(monkeypatch):
    """§B.1 证据 ③：allure 缺席 → 抑制器不装 + WARN，行为逐字不变（正对照）。

    旧代码本就短路 `_do_screenshot`（`_ALLURE_AVAILABLE`），抑制器同样不该假装在位。
    """
    monkeypatch.setitem(sys.modules, "allure", None)       # import allure 当场崩
    plugin = _plugin_loaded(monkeypatch, "_xcheck_plugin_absent",
                            **{"ATLAS_FAST_NO_STEP_SHOTS": "1"})
    try:
        plugin.pytest_configure(types.SimpleNamespace())
        assert plugin._STATE["shots_installed"] is False
        assert plugin._ORIG_ATTACH is None
        assert plugin._SHOTS == {"dropped": 0, "passed": 0}
        assert plugin._env_on("ATLAS_FAST_NO_STEP_SHOTS") is True   # 闸确实想开
    finally:
        sys.modules.pop(plugin.__name__, None)


# ---------------------------------------------------------------------------
# 静态面
# ---------------------------------------------------------------------------


def _skip_machinery_hits(source, name):
    """AST 扫「能把用例写成 skip/xfail」的调用形态与信号名引用。"""
    tree = ast.parse(source, filename=name)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in ("skip", "xfail"):
                hits.append("%s:%d .%s()" % (name, node.lineno, f.attr))
        elif isinstance(node, ast.Name) and node.id in ("Skipped", "Outcome"):
            hits.append("%s:%d name %s" % (name, node.lineno, node.id))
    return hits


def test_x13_c2_shim_pair_holds_no_skip_machinery():
    """红线 #7 入口封堵：垫片两文件里没有任何把失败写成 skip 的手段。

    正对照（证扫描器非恒真）：旧仓 lib/runner.py 有真 `_pytest.skip(...)` 调用
    （:381/:452），同一把尺必须命中；旧仓缺席时退化为注入样本 + 如实报出对照来源。
    """
    for path in (SHIM_FILE, PLUGIN_FILE):
        hits = _skip_machinery_hits(path.read_text(encoding="utf-8"), path.name)
        assert hits == [], hits
    runner = OLD_REPO / "lib" / "runner.py"
    if runner.is_file():
        control_src, control_name = runner.read_text(encoding="utf-8-sig"), "旧仓 runner.py"
    else:                                    # CI 无旧仓：用注入样本自证尺有效
        control_src = "import pytest\ndef t():\n    pytest.skip('x')\n"
        control_name = "注入样本（旧仓缺席）"
    hits = _skip_machinery_hits(control_src, control_name)
    assert hits, "对照零命中：本尺恒真（来源=%s）" % control_name


def _wait_for_timeout_calls(source, name):
    """AST：`X.wait_for_timeout(...)` 形态的**调用**（赋值/属性读取不算）。"""
    out = []
    for node in ast.walk(ast.parse(source, filename=name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "wait_for_timeout":
            out.append("%s:%d" % (name, node.lineno))
    return out


def test_x14_c1_probe_path_never_calls_wait_for_timeout():
    """§A 判据 4 源文面：垫片自己的睡眠只走注入时钟 sleep_ms，不调被替换的方法。

    正对照：旧仓 lib/pages.py 必然命中同一把尺（那 61 处就是本波要消灭的形态）；
    旧仓缺席时用本文件的注入样本兜对照，并如实注明来源（对照缺席=尺失效）。
    """
    for path in (SHIM_FILE, PLUGIN_FILE):
        hits = _wait_for_timeout_calls(path.read_text(encoding="utf-8"), path.name)
        assert hits == [], "%s 出现 wait_for_timeout 调用：%s" % (path.name, hits)
    assert "clk.sleep_ms(" in SHIM_FILE.read_text(encoding="utf-8")
    pages = OLD_REPO / "lib" / "pages.py"
    if pages.is_file():
        control = _wait_for_timeout_calls(pages.read_text(encoding="utf-8-sig"),
                                          "pages.py")
        assert control, "旧仓 pages.py 零命中：本尺恒真"
    else:
        sample = "class P:\n    def w(self):\n        self.wait_for_timeout(10)\n"
        assert _wait_for_timeout_calls(sample, "注入样本"), "对照恒真"


# ---------------------------------------------------------------------------
# G11-1 三假页：env 轴
# ---------------------------------------------------------------------------


def _env_install(monkeypatch, scenario_state, *, waits, min_ms=None, anchor=None):
    """走 env 装垫片（真实通道：plugin._load_shim → gates → install）。

    返回 (plugin, shim_mod, clock, page, holder, cleanup)。clock 通过替换
    `_RealClock` 注入，所以 env→install 这条链不被测试参数污染。
    """
    plugin = _plugin_loaded(monkeypatch, "_xcheck_plugin_env")
    if waits is not None:
        monkeypatch.setenv("ATLAS_FAST_WAITS", waits)
    if min_ms is not None:
        monkeypatch.setenv("ATLAS_FAST_MIN_MS", min_ms)
    if anchor is not None:
        monkeypatch.setenv("ATLAS_FAST_SETTLE_ANCHOR", anchor)
    shim_mod = plugin._load_shim()
    clk = VClock()
    monkeypatch.setattr(shim_mod._RealClock, "now_ms", staticmethod(clk.now_ms))
    monkeypatch.setattr(shim_mod._RealClock, "sleep_ms", staticmethod(clk.sleep_ms))
    page = Page(clock=clk, **scenario_state)
    holder = {}
    real_install = shim_mod.install_fast_waits

    def spy(target, **kw):
        holder["stats"] = real_install(page, **kw)   # 装到假页实例，行为可核
        return holder["stats"]
    monkeypatch.setattr(shim_mod, "install_fast_waits", spy)

    fake_pw = types.ModuleType("playwright")
    sub = types.ModuleType("playwright.sync_api")

    class _P:
        pass
    sub.Page = _P
    fake_pw.sync_api = sub
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sub)
    plugin.pytest_configure(types.SimpleNamespace())   # 真走闸（不直调私有装配）

    def cleanup():
        plugin.pytest_unconfigure(types.SimpleNamespace())
        shim_mod._INSTALLED.clear()
        sys.modules.pop(plugin.__name__, None)
        # standalone 垫片模块是全局共享名：本尺改过它的 _RealClock，退场时一并交还，
        # 不把打过补丁的模块留给别的测试文件（并行窗口互污染防线）。
        sys.modules.pop("_atlas_legacy_fast_standalone", None)
    return plugin, shim_mod, clk, page, holder, cleanup


def test_x15_c7_loading_page_is_not_passed_early_via_env(monkeypatch):
    """G11-1 (a)：readyState='loading' 且零遮罩 → env 闸开也**不得 0ms 放行**。

    判据双向：actual=req（不提前）且原始睡眠零调用（不叠加）。另核 env 值真落到
    了 `_STATE`（接线可见，不留「旋钮在文档里、代码不读」的假可用）。
    #33 之后分两支各答一件事：①从未见忙、只有 `not-ready` 门 → 睡满且记
    `no-busy-observed`；②**见过忙但门一直没开** → 同样睡满，但 `fallback` 必须是空串
    （「见过忙碌」只给资格、不给放行；把它算进 fail-safe 睡满就是把两件不同的事混成一堆，
    A/B 的 `no_busy_observed` 会虚高）。
    """
    out = _env_install(monkeypatch, {"scenario": "quiet", "ready": False},
                       waits="1", min_ms="400")
    plugin, shim_mod, clk, page, holder, cleanup = out
    try:
        assert plugin._STATE["waits_installed"] is True
        assert plugin._STATE["min_ms"] == 400
        page.wait_for_timeout(5000)
        assert clk.now == 5000
        assert any("document.readyState" in js for js in page.conditions)
        assert holder["stats"].calls[0]["actual_ms"] == 5000
        assert holder["stats"].calls[0]["fallback"] == "no-busy-observed"
        assert page.orig_sleeps == []
    finally:
        cleanup()
    monkeypatch.undo()

    plugin2, _s2, clk2, page2, holder2, cleanup2 = _env_install(
        monkeypatch, {"scenario": "quiet", "ready": False,
                      "script": [{"masks": True}]},
        waits="1", min_ms="400")
    try:
        page2.wait_for_timeout(5000)
        assert clk2.now == 5000                          # 见过忙≠可以走：门没开照样等满
        assert holder2["stats"].calls[0]["actual_ms"] == 5000
        assert holder2["stats"].calls[0]["fallback"] == ""   # 但不是 fail-safe 睡满那一类
        assert page2.orig_sleeps == []
    finally:
        cleanup2()


def test_x16_c7_anchor_invisible_floor_and_clamp_via_env(monkeypatch):
    """G11-1 (b) + 截断：锚点不可见不得提前；`min_ms > requested` 按 requested 截断。

    三格三断言，各自带对照：(b) 见过忙但锚点门没开→等满（`fallback` 空串，不是 fail-safe
    睡满那一类）；锚点可见且见忙→提前并补足到下限（恰 min_ms）；requested=100 < 下限 400
    → actual==100（下限不顶破预算）。#33 之后三条**都必须先出一拍忙碌信号**，否则它们
    在「从未见忙→睡满」下同答=装饰格（lead 通过闸同款约束：不许把判据降级成恒真）。
    选择器只来自 env（红线 #11）——代码默认值里出现它归 #17 的禁词尺管。
    """
    plugin, _s, clk, page, holder, cleanup = _env_install(
        monkeypatch, {"scenario": "quiet", "anchor_visible": False,
                      "script": [{"masks": True}]},
        waits="mask", min_ms="0", anchor=".xchk-anchor")
    try:
        assert plugin._STATE["anchor"] == ".xchk-anchor"
        page.wait_for_timeout(2000)
        assert clk.now == 2000
        assert any(".xchk-anchor" in js for js in page.conditions)
        assert holder["stats"].calls[0]["fallback"] == ""     # 见过忙≠门开了就能走
    finally:
        cleanup()
    monkeypatch.undo()

    plugin2, _s2, clk2, page2, _h2, cleanup2 = _env_install(
        monkeypatch, {"scenario": "quiet", "script": [{"masks": True}]},
        waits="mask", min_ms="400", anchor=".xchk-anchor")
    try:
        page2.wait_for_timeout(5000)
        # 见忙→落定发生在 25ms，下限把这一笔补到恰 400（#33 后这条腿才真在守下限：
        # 没有忙碌前置时它会因「压根没提前」同答 5000 而失去区分力）
        assert clk2.now == 400
        assert page2.orig_sleeps == []
    finally:
        cleanup2()
    monkeypatch.undo()

    plugin3, _s3, clk3, page3, holder3, cleanup3 = _env_install(
        monkeypatch, {"scenario": "quiet", "script": [{"masks": True}]},
        waits="mask", min_ms="400")
    try:
        page3.wait_for_timeout(100)
        assert clk3.now == 100                           # 下限 400 不得把 100 的预算顶破
        assert holder3["stats"].calls[0]["actual_ms"] <= 100
        assert holder3["stats"].calls[0]["fallback"] == ""
    finally:
        cleanup3()


def test_x17_c7_gate_off_three_pages_all_take_original_wait(monkeypatch):
    """G11-1 末格 + §D.1 零涟漪：闸未置位时三种假页**逐字走原始等待**。

    下限/锚点旋钮单独置位也不算开闸（兜底层不许自己把垫片打开）。正向对照=同一页
    开闸后不走原始等待，证「不变」不是恒真。
    #33 之后对照页必须**先出一拍忙碌信号再转静默**：`{"scenario": "quiet"}` 现在的含义
    是「从未见忙的页」，拿它当对照测的就不是「下限生效」而是「fail-safe 生效」——
    两件不同的事不许混在同一格（lead 02:3x 点名）。
    """
    states = [{"scenario": "never", "ready": False},
              {"scenario": "never", "anchor_visible": False},
              {"scenario": "quiet", "script": [{"masks": True}]}]
    for st in states:
        plugin, shim_mod, clk, page, holder, cleanup = _env_install(
            monkeypatch, st, waits=None, min_ms="400", anchor=".xchk-anchor")
        try:
            assert plugin._STATE["waits_installed"] is False
            page.wait_for_timeout(3000)
            assert page.orig_sleeps == [3000]              # 原样一次固定睡眠
            assert page.conditions == [] and clk.now == 3000
        finally:
            cleanup()
        monkeypatch.undo()
    plugin4, _s4, clk4, page4, _h4, cleanup4 = _env_install(
        monkeypatch, {"scenario": "quiet", "script": [{"masks": True}]},
        waits="1", min_ms="400")
    try:
        page4.wait_for_timeout(3000)
        assert page4.orig_sleeps == [] and clk4.now == 400   # 见忙后落定，下限补到恰 400
        assert page4.conditions, "对照页一次判据都没问=这条对照恒真"
    finally:
        cleanup4()


def test_x18_c1_stats_record_effective_tier_after_degrade(shim, capsys):
    """§A 判据 2 的可见性面：hook 未生效降 mask 时 stats 记**实际生效**档位。

    不留「我以为在判 xhr」的假账（降级是设计行为，但降了要如实）。同时核不变量
    Σactual ≤ Σrequested 在「降级 + 永不静默」序贯下成立。正向对照=hook 在场时
    档位照记名义全档。
    """
    clk = VClock()
    page = Page(clock=clk, scenario="never", hook_ok=False)
    stats = _install(shim, page, clock=clk, probe="mask+xhr")
    page.wait_for_timeout(2000)
    out = capsys.readouterr().out
    assert "WARN" in out and "mask" in out, out
    entry = stats.calls[0]
    assert entry["probe"] == "mask"
    assert entry["fallback"].startswith("hook-degraded:xhr")
    assert entry["actual_ms"] == 2000

    clk2 = VClock()
    page2 = Page(clock=clk2, scenario="never", hook_ok=True)
    stats2 = _install(shim, page2, clock=clk2, probe="mask+xhr")
    page2.wait_for_timeout(2000)
    assert stats2.calls[0]["probe"] == "mask+xhr"
    agg = stats.aggregate()
    assert agg["actual_ms_total"] <= agg["original_ms_total"]


def test_xz_this_file_itself_parses():
    """常驻自哨（gap9 lead 要求）：本文件字面量写坏会中断**整场收集**。

    一条 collection error 顶掉所有人的门禁，比断言失败贵得多；把「文件本身能 parse」
    做成显式判据（模板引号嵌套、bytes 里放非 ASCII 这两类事故都在这一格现形）。
    """
    ast.parse(Path(__file__).resolve().read_text(encoding="utf-8"))

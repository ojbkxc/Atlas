"""gap11 #17：旧仓运行期垫片——**预算制**有界语义等待（wait_for_timeout 替代），零修改旧仓代码。

契约出处：`docs/gaps/gap11-legacy-fast-compat.md` §A（scout 设计，硬判据 1-4 + 三档
probe + 下限/锚点）与 `docs/gaps/gap11-lead-rulings.md` G11-1（reload 语义洞，三条全做）。

形态纪律（硬约束）：
- 仅标准库。**旧仓运行时绝不 import atlas 包**——本文件由 `fastplugin.py` 以绝对文件路径
  standalone 加载（importlib.util.spec_from_file_location），不带包上下文，所以本模块
  自身也**禁用相对 import**（无包时 `from .x` 会当场炸）；emit 侧按**真文件字节** vendor
  （G11-4），锁步测在 `tests/test_emit_compat.py`。
- env 门默认关：`ATLAS_FAST_WAITS` 未置位 → 一切行为逐字节不变（判据测⑧钉死）。

四条硬判据（设计 §A 判据 1/3/4 + lead P0 裁定 2026-11-09 的判据 2 收紧形态）：
1. **绝不更长**：预算制 `remaining = ms - 已花`，probe 轮询、下限补睡、异常兜睡**都只花
   remaining**。`actual_ms ≤ requested_ms` 恒成立（property 测 D.1.7）。永不静默 → **恰 ms
   返回**（不多不少，假时钟钉死）；**每拍都探**（含末拍不足一拍——「静默页零推进」是判据 2，
   不许用少探一次去换算术干净）。页侧求值成本自身反噬预算不是垫片能退的东西，可核的不变量
   因此是**垫片补睡之和 ≤ 预算**，账面仍记 `max_ms`：既不许把页侧耗时记成「省了」，也不许
   为它多睡一拍。
2. **只按「忙碌→静默」的转换提前**（原判据「只按判据提前」的收紧形态，lead 2026-11-09
   裁的 P0）：早返回的准入**不是**「这一拍探针静默」，而是**本次 settle 调用内先观测到
   忙碌信号、随后落定**——mask 档=见过可见 `.el-loading-mask`（≥1 拍）；xhr 档=见过
   `pending>0`；mutation 档=见过一次**真实** DOM 变化、其后静默满 quiet 窗。**从未观测到
   忙碌信号 → 绝不提前，睡满 requested**，fallback 记 `no-busy-observed`。
   动机是真机 A/B（r1/r2 两轮）：`--fast` 把失败点从基线的 step 7 **前移**到 step 1
   （`[语言切换失败] 目标 English US，实际 ''`）与 step 3（`Page.evaluate: 客户名称 label
   未找到`）；stats 里 `page.py:237 req=6000→act=400`、`login.py:484 req=3000→act=400`。
   这类等待**压根不带遮罩**——纯探针首拍即真，`min_ms` 下限只是把 400ms 挡在门口，挡不住
   「没有东西可等」被读成「已经等完了」。设计 §A 的「旧仓在等遮罩」前提对这部分等待不成立。
3. **probe 异常分三类**：导航瞬态（同源文本，见 `_TRANSIENT_MARKS`）=「尚未静默」**在预算内
   继续等**；不可恢复（Target closed 族）=**按预算睡完后原样抛出**（不吞不改——吞掉会把
   「会话已死」伪装成「等待成功」，#102/#133 同族）；其它未知异常=回退**原始** `wait_for_timeout`
   的**剩余量**（不是全额，否则违反判据 1），记 fallbacks。
4. **轮询用进程侧 `time.sleep(SETTLE_TICK_MS)`**（25ms 级），绝不用 `wait_for_timeout` 自身
   轮询（递归禁令：垫片自己的轮询路径上原始睡眠零调用，测里钉）。

其余硬边界一律不动（lead 原话：只改「什么时候允许提前返回」**这一个**判据）：绝不更长、
`min_ms` 下限、预算制回退、诚实 `actual_ms`、档位构成（mask 恒含 + xhr/mutation 递加）。
默认档也不换（换 mutation 档=用「更贵的探针」掩盖「判据本身不安全」，问题会在下一个不带
mutation 的等待上重新出现）。

静默判据（G11-1 公式经 P0 收紧后的完整形态）：
```
可提前 ⇔ 本次调用内**见过忙碌信号**（mask 可见 / xhr pending>0 / 真 mutation）
        ∧ 此刻 document.readyState==='complete'
        ∧ 此刻无任何可见 .el-loading-mask（全枚举，不是只判第一个——旧 wait_loading 实测教训）
        ∧ [档位增量：在途请求=0 / mutation 静默窗]
        ∧ [ATLAS_FAST_SETTLE_ANCHOR 非空时：该锚点可见]
        —— 求值不了的那一段（hook 不在场）=「不可评估」：继续等，且**不**记为忙碌
actual_ms ≥ min_ms（下限），且恒 ≤ 原请求 ms（绝不更长）
从未见过忙碌信号 → 睡满 requested_ms，fallback=no-busy-observed
```
判据返回**状态码**而不是布尔，正因为 Python 侧要把「忙碌」「静默」「不可评估」三种事实分开；
布尔只剩两态，「一直静默（压根没活动）」与「忙碌后落定」在布尔面上同形——那正是本 P0 的成因。

**语义后果（不许当性能损失修掉）**：垫片从此只缩短「真的被遮罩/在途请求/DOM 变化挡着」的
等待，`saved_ms` 会比 A/B 那轮低。那是正确性不是退步；把判据放宽回去做数字=把失败点前移
重新请回来（A/B 的通过闸=失败点与基线逐点一致，不是节省量）。

**统计面随之分叉（同一次裁定带来的形态要求）**：`no-busy-observed` 自此是**多数正常调用**
的 note，绝不能再算进 `fallbacks`——否则 A/B 读到 `fallbacks=37` 会判成「探针大面积出错」，
方向恰好反了。故 `FastWaitStats` 两个计数语义分立（`fallbacks`=真回退、
`no_busy_observed`=fail-safe 睡满），jsonl 仍是七个键、`by_stage` 的键仍是档位名。

**为什么下限与锚点仍在位（P0 之后它们是第二层）**：mask+readyState 的语义前提是「骨架已
渲染、只是被遮罩挡着」。旧仓大量形态是 `page.reload()` 后立刻 `wait_for_timeout(5000/8000)`
（`open_doc_list`、`open_draft_detail`）——那一刻没有任何忙碌信号可观测，判据 2 直接睡满；
`min_ms` 与锚点兜的是「忙碌信号见过、但应用还没起」这一残余形态，G11-1 定了它们，不许顺手撤。
锚点选择器**只来自 env**（红线 #11：业务字面量不进代码，示例见设计文档 §A）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ── JS 面（判据返回**状态码**，唯一拼装点=build_condition）──────────────────
# 三态而不是布尔：早返回的准入是「先看到忙碌、随后静默」（判据 2 / lead P0 裁定），
# 布尔面上「压根没活动」与「忙碌后落定」同形——就是本次失败点前移的成因。
# 忙碌码（busy-*）会置位本次调用的 busy_seen；静默码只在 busy_seen 已置时放行；
# 门码（not-ready/not-anchored/no-hook/no-activity）= 「还没静默」但**不**算忙碌观测。
_CODE_QUIET = "quiet"
_CODE_BUSY_MASK = "busy-mask"
_CODE_BUSY_XHR = "busy-xhr"
_CODE_BUSY_MUTATION = "busy-mutation"
_CODE_NOT_READY = "not-ready"
_CODE_NOT_ANCHORED = "not-anchored"
_CODE_NO_HOOK = "no-hook"
_CODE_NO_ACTIVITY = "no-activity"
_BUSY_PREFIX = "busy-"
_NOTE_NO_BUSY = "no-busy-observed"   # lead 定死的 fallback 字样（A/B 差分按它分组）

# Python 侧把上面的码归成三「类」（类别是内部口径，不是协议字面量；词表只有上面那一组）。
_KIND_BUSY = "busy"
_KIND_QUIET = "quiet"
_KIND_GATE = "gate"


def _js_lit(code: str) -> str:
    """码值进 JS 的字面量写法（词表全在本模块，无用户输入→无需转义，仍集中一处）。"""
    return "'" + str(code) + "'"


# 可见性判据（遮罩与锚点共用一把尺）：全枚举 + 三关（rect/visibility/display），
# 「第一个隐藏、第二个正挡按钮」的旧实测教训就在这把尺里（2026-10-30）。
_JS_VISIBLE_FN = (
    "const vis = (e) => { const s = getComputedStyle(e);"
    " return e.getClientRects().length > 0 && s.visibility !== 'hidden'"
    " && s.display !== 'none' && e.style.display !== 'none'; };"
)

# uf-6：忙碌遮罩探针的选择器。**点名文档化**（不族化）：本值是 Element-UI 的
# `loading-mask` 遮罩类——一个**平台/UI 库特定**的业务字面量。本模块按 `--compat`
# 原字节 vendor 进旧仓产物，旧仓的 UI 框架就是 Element-UI，改这个值=改旧仓实测行为，
# 故**不做族化**（族化会把「等遮罩」判据换成别的语义，破坏既有真机 A/B 基线）。
# 非 Element-UI 平台不从这条路径拿垫片——`--compat legacy-suite` 的产物面向的就是那个
# 旧仓，探针选择器是它的平台事实，不在通用产物侧配。真正的族化入口是「通用产物」侧的
# `waits.py` 语义等待（不受本常量约束）；本常量只做「把业务字面量收成一处 + 点名」。
MASK_PROBE_SELECTOR = ".el-loading-mask"

# 遮罩在场=忙碌信号（本档唯一的「真在等」证据形态）；缺席不是忙碌，也不是放行理由。
_JS_MASK_BUSY_CLAUSE = (
    f" if (Array.from(document.querySelectorAll('{MASK_PROBE_SELECTOR}')).some(vis))"
    " return " + _js_lit(_CODE_BUSY_MASK) + ";"
)

# hook 与「hook 在位」分离：hook 不在场=**不可评估**（继续等），既不静默也**不**算忙碌观测
# ——把「探针装不上」记成忙碌，等于用「我不知道」换一次提前放行，与设计 §A 点名的
# 「xhr 恒 0 假静默」是同一道洞的另一侧。
_JS_XHR_HOOK = (
    "(() => { if (window.__atlasFastXhr) return;"
    " const st = {pending: 0}; window.__atlasFastXhr = st;"
    " const X = XMLHttpRequest.prototype; const oOpen = X.open, oSend = X.send;"
    " X.open = function() { this.__atlasFastCounted = false;"
    " return oOpen.apply(this, arguments); };"
    " X.send = function() { if (!this.__atlasFastCounted) {"
    " this.__atlasFastCounted = true; st.pending++;"
    " const done = () => { st.pending = Math.max(0, st.pending - 1); };"
    " this.addEventListener('loadend', done); }"
    " return oSend.apply(this, arguments); };"
    " const oFetch = window.fetch;"
    " if (oFetch) { window.fetch = function() { st.pending++;"
    " const p = oFetch.apply(this, arguments);"
    " p.catch(() => {}).finally(() => { st.pending = Math.max(0, st.pending - 1); });"
    " return p; }; } })();"
)
_JS_XHR_PRESENT = "() => !!window.__atlasFastXhr"
_JS_XHR_GATE_CLAUSE = " if (!window.__atlasFastXhr) return " + _js_lit(_CODE_NO_HOOK) + ";"
_JS_XHR_BUSY_CLAUSE = (
    " if (window.__atlasFastXhr.pending > 0) return " + _js_lit(_CODE_BUSY_XHR) + ";"
)

# mutation hook：`__atlasFastLast` 在**装钩时刻**就记了一次时间，所以「装钩后静默满窗」
# 本身证明不了页面真的动过（P0 的第三种形态）。因此观察者回调另记 `__atlasFastMutated`
# ——忙碌观测只认「有真实变化」，静默窗只在 mutated 为真时才参与判定。
_JS_MUTATION_HOOK = (
    "(() => { if (window.__atlasFastObserver) return;"
    " window.__atlasFastLast = performance.now();"
    " window.__atlasFastMutated = false;"
    " const obs = new MutationObserver(() => {"
    " window.__atlasFastLast = performance.now();"
    " window.__atlasFastMutated = true; });"
    " obs.observe(document.documentElement,"
    " {subtree: true, childList: true, attributes: true, characterData: true});"
    " window.__atlasFastObserver = true; })();"
)
_JS_MUTATION_PRESENT = "() => !!window.__atlasFastObserver"
_JS_MUTATION_CLAUSE_TEMPLATE = (
    " if (!window.__atlasFastObserver || !window.__atlasFastMutated) return"
    " " + _js_lit(_CODE_NO_ACTIVITY) + ";"
    " if (!window.__atlasFastLast"
    " || !((performance.now() - window.__atlasFastLast) >= {quiet_ms})) return"
    " " + _js_lit(_CODE_BUSY_MUTATION) + ";"
)
# readyState / 锚点是**门**（此刻能否算静默），不是忙碌观测——lead 的忙信号词表只有
# mask / xhr pending / 真实 mutation 三项。
_JS_NOT_READY_CLAUSE = (
    " if (document.readyState !== 'complete') return " + _js_lit(_CODE_NOT_READY) + ";"
)
_JS_ANCHOR_CLAUSE_TEMPLATE = (
    " if (!Array.from(document.querySelectorAll({anchor})).some(vis)) return"
    " " + _js_lit(_CODE_NOT_ANCHORED) + ";"
)
_JS_RETURN_QUIET = " return " + _js_lit(_CODE_QUIET) + ";"

# 档位 → 需装的页内 hook（顺序即递加顺序；任一装不上=整档降 mask，见 _settle_probe）
_HOOK_INSTALLS = (
    ("xhr", _JS_XHR_HOOK, _JS_XHR_PRESENT),
    ("mutation", _JS_MUTATION_HOOK, _JS_MUTATION_PRESENT),
)

_TICK_MS = 25                      # 进程侧轮询步长（设计 §A 判据 4：25ms 级）
_DEFAULT_MIN_MS = 400              # G11-1 修法 2 的默认下限（运行时仍按 requested 截断）
_DEFAULT_QUIET_MS = 200            # mutation 静默窗默认（受预算截断）
_ON_VALUES = ("1", "on", "true")
_OFF_VALUES = ("", "0", "off", "false")

# tier 别名 → 层集合。**mask 恒含、xhr/mutation 递加**（设计 §A 判据 2 的档位口径）；
# 旧的裸 "xhr"/"mutation" 取值是同一档位的简写，不是「只判 xhr」。
_TIERS = {
    "mask": ("mask",),
    "mask+xhr": ("mask", "xhr"),
    "xhr": ("mask", "xhr"),
    "mask+xhr+mutation": ("mask", "xhr", "mutation"),
    "mutation": ("mask", "xhr", "mutation"),
}

# 异常分类文本：与 `atlas/waits.py::_TRANSIENT_MARKS` **同源**（vendored 文件不能 import
# atlas，G11-4；锁步测 test_transients_marks_lockstep_with_waits 钉死「改一漏一必红」）。
_TRANSIENT_MARKS = ("execution context was destroyed", "navigation", "detached")
# 不可恢复：会话/浏览器已死。这类抛错**绝不**能翻成「等待成功」——垫片坏不如下线，
# 但「页面已死却返回 quiet」会把真因漂到远处某步（#102/#133 同族）。
_UNRECOVERABLE_MARKS = ("target closed", "browser has been closed",
                        "connection is closed", "connection closed")

# install 状态登记（id(target) → (target, original, wrapper)）：uninstall 幂等还原的权威。
# 强引用是有意的：垫片生命周期=一次 legacy 进程，收尾必 uninstall；WeakSet 反而会让
# 「页面先被回收」把登记悄悄丢掉、还原扑空。
_INSTALLED: dict = {}

_THIS_FILE = str(Path(__file__).resolve())


# ---------------------------------------------------------------------------
# env 门（全部默认关/默认值，未知取值不启用 + WARN）
# ---------------------------------------------------------------------------


def waits_gate() -> str:
    """`ATLAS_FAST_WAITS` → 生效档位名（canonical tier 字符串）；"" 表示关闭（默认）。

    未知取值**不启用**并 WARN（手滑的值不该把一个「默认关」的垫片开起来——与 ENV_KEYS
    「None=缺配即拒」同一族口径）。
    """
    raw = os.environ.get("ATLAS_FAST_WAITS", "").strip().lower()
    if raw in _OFF_VALUES:
        return ""
    if raw in _ON_VALUES:
        return "mask"
    if raw in _TIERS:
        return raw
    print(f"WARN: ATLAS_FAST_WAITS={raw!r} 不是 {{1,on,true,mask,mask+xhr,"
          "mask+xhr+mutation}}（xhr/mutation 为同档简写），按关闭处理"
          "（默认关的开关不因疑似拼写启用）")
    return ""


def min_ms_gate() -> int:
    """`ATLAS_FAST_MIN_MS` → 有界下限 ms；缺省/坏值一律回落默认 400。

    坏值**不**改变默认（同 waits_gate 口径）：拼错的旋钮不该顺手把下限取消掉——
    下限正是 reload 误判的兜底，静默失效=把 G11-1 的补丁按回洞上。
    `0` 是合法显式值（A/B 要量「纯探针、无下限」那一档）。
    """
    raw = os.environ.get("ATLAS_FAST_MIN_MS", "").strip()
    if not raw:
        return _DEFAULT_MIN_MS
    try:
        val = int(raw)
    except ValueError:
        print(f"WARN: ATLAS_FAST_MIN_MS={raw!r} 不是非负整数，回落默认 "
              f"{_DEFAULT_MIN_MS}ms（坏旋钮不顶掉兜底）")
        return _DEFAULT_MIN_MS
    if val < 0:
        print(f"WARN: ATLAS_FAST_MIN_MS={val} 为负，回落默认 {_DEFAULT_MIN_MS}ms；"
              "要「无下限」请显式写 0")
        return _DEFAULT_MIN_MS
    return val


def anchor_gate() -> str:
    """`ATLAS_FAST_SETTLE_ANCHOR` → 应用就绪锚点选择器；缺省空=不启用。

    选择器**只来自 env**（红线 #11）。写坏的选择器不会静默放行：querySelectorAll 抛错
    → 归「未知异常」路径 → 回退原始睡眠（垫片坏=退回旧行为，不提前放行）。
    """
    return os.environ.get("ATLAS_FAST_SETTLE_ANCHOR", "").strip()


def layers_for(gate: str) -> tuple:
    """档位名 → 判据层集合（唯一映射点，`waits_gate` 与直调 API 共用）。"""
    if not gate:
        return ()
    return _TIERS[gate]


# ---------------------------------------------------------------------------
# 统计面（「省了多少」的唯一证据源 = 设计 E 度量 3 的原始账）
# ---------------------------------------------------------------------------


def _join_notes(*parts) -> str:
    """note 的唯一**拼装**点（与下面 `split_note` 的拆解点同域，读写一把尺 #92）。"""
    return ";".join(p for p in parts if p)


def split_note(note: str) -> tuple:
    """note 的**唯一拆解点**（`;` 连接的两类事实分回来，判据只此一份 #92）。

    为什么必须拆而不是「非空即回退」：`no-busy-observed` 是 P0 之后**绝大多数正常调用**
    的取值（凡是没被忙碌信号守护过的等待都睡满预算），把它计进 `fallbacks` 会让 A/B
    判读把「探针从没出错」读成「探针大面积回退」——方向完全反了。两类事实各自独立计数，
    允许在同一笔上并存（档位降级 + 从未见忙），所以 `fallbacks + no_busy_observed`
    **可以大于 calls**，不许拿「≤ calls」当恒等式去反推实现。
    """
    parts = tuple(p for p in str(note or "").split(";") if p)
    return (tuple(p for p in parts if p != _NOTE_NO_BUSY),
            tuple(p for p in parts if p == _NOTE_NO_BUSY))


class FastWaitStats:
    """逐调用统计 + 设计 §A 要求的 per-case 聚合。

    只增不删；`to_jsonl(path)` 落盘尽力而为（写失败 WARN，不顶被测行为）。
    saved_ms = Σ(requested-actual)，在预算制下**不可能为负**（判据 1）；仍按实值打印，
    真出现负数就是垫片违约，必须显式可见而不是被 abs() 藏起来。

    两类计数**语义分立**（lead 2026-11-09 追加，P0 之后必需）：
    - `fallbacks` = **真回退**（探针错误 / 无 evaluate / 不可恢复 / 档位降级），
      旧证据口径「0=探针从没出错」**继续成立**；
    - `no_busy_observed` = fail-safe 睡满的笔数（正常路径，不是故障），P0 之后
      这条会**单独上升**。
    七键面（jsonl）一个不加一个不减：两类事实仍共用 `fallback` 单字段（`;` 连接）。
    """

    def __init__(self):
        self.calls: list = []

    def record(self, *, caller_file: str, caller_line: int, caller_func: str,
               requested_ms, actual_ms, probe: str, fallback: str = "") -> dict:
        entry = {"caller_file": caller_file, "caller_line": int(caller_line),
                 "caller_func": caller_func, "requested_ms": requested_ms,
                 "actual_ms": actual_ms, "probe": probe, "fallback": fallback}
        self.calls.append(entry)
        return entry

    @property
    def saved_ms(self) -> int:
        return sum(int(c["requested_ms"]) - int(c["actual_ms"]) for c in self.calls)

    @property
    def fallback_count(self) -> int:
        """真回退笔数——**不含** `no-busy-observed`（那是正常 fail-safe，不是回退）。"""
        return sum(1 for c in self.calls if split_note(c["fallback"])[0])

    @property
    def no_busy_count(self) -> int:
        """fail-safe 睡满（本次调用从头到尾没见过忙碌信号）的笔数。"""
        return sum(1 for c in self.calls if split_note(c["fallback"])[1])

    def aggregate(self) -> dict:
        """设计 §A stats 形态 + P0 追加的 `no_busy_observed` 独立计数键。

        不变量 `actual_ms_total ≤ original_ms_total` 恒真（property 测钉死）。
        by_stage 按**实际生效**的档位归集——hook 降级后记 mask，不记名义档位；
        **note 永不进 by_stage 的键**（那是判据事实不是档位，混进去等于把 fail-safe
        当档位统计）。
        """
        by_stage: dict = {}
        for c in self.calls:
            stage = by_stage.setdefault(str(c["probe"]), {"calls": 0,
                                                          "original_ms_total": 0,
                                                          "actual_ms_total": 0})
            stage["calls"] += 1
            stage["original_ms_total"] += int(c["requested_ms"])
            stage["actual_ms_total"] += int(c["actual_ms"])
        return {"calls": len(self.calls),
                "original_ms_total": sum(int(c["requested_ms"]) for c in self.calls),
                "actual_ms_total": sum(int(c["actual_ms"]) for c in self.calls),
                "fallbacks": self.fallback_count,
                "no_busy_observed": self.no_busy_count,
                "by_stage": by_stage}

    def _dump(self, path, text: str) -> int:
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(text)
            return len(self.calls)
        except OSError as exc:
            print(f"WARN: fast waits stats 落盘失败（统计只留内存）: "
                  f"{type(exc).__name__}")
            return 0

    def to_jsonl(self, path) -> int:
        """逐调用 jsonl（字段面=派单判据⑥固定七键，A/B 差分用；设计 §E 度量 3 的派算源）。"""
        return self._dump(path, "".join(
            json.dumps(entry, ensure_ascii=False, default=str) + "\n"
            for entry in self.calls))


# ---------------------------------------------------------------------------
# 时钟与异常分类（测试可注入假时钟——设计 D.1.1「假时钟测钉死不多不少」）
# ---------------------------------------------------------------------------


class _RealClock:
    """进程侧单调毫秒时钟 + 睡眠。**垫片自己的睡眠只用 time.sleep**（判据 4：
    绝不用 wait_for_timeout 轮询=递归防线）。"""

    @staticmethod
    def now_ms() -> float:
        return time.perf_counter() * 1000.0

    @staticmethod
    def sleep_ms(ms: float) -> None:
        if ms > 0:
            time.sleep(ms / 1000.0)


class _NoProbeCapability(Exception):
    """page 没有 evaluate——垫片无从求值判据，由调用方回退原始睡眠。"""


class _Unrecoverable(Exception):
    """会话/浏览器已死：已按预算睡完，原语义异常必须继续传播（不吞不改，判据 3）。"""

    def __init__(self, cause: BaseException):
        super().__init__(f"unrecoverable probe failure: {type(cause).__name__}")
        self.cause = cause


def _exception_text(exc: BaseException) -> str:
    """异常定性文本（类型名 + 消息）。

    vendored 文件不能 import `atlas.failures.exception_text`（G11-4），但**纪律同源**：
    业务异常 `__str__` 自身抛错时回落类型名，判据函数不得先于被判定对象死（#89/#127）。
    """
    try:
        return f"{type(exc).__name__} {exc}"
    except Exception:
        return type(exc).__name__


def _is_transient(exc: BaseException) -> bool:
    """导航瞬态（执行上下文销毁/frame detached）=「页面还没就绪」，不是失败：预算内继续等。"""
    text = _exception_text(exc).lower()
    return any(mark in text for mark in _TRANSIENT_MARKS)


def _is_unrecoverable(exc: BaseException) -> bool:
    text = _exception_text(exc).lower()
    return any(mark in text for mark in _UNRECOVERABLE_MARKS)


def _caller_frame() -> tuple:
    """最近一个「不在垫片文件内」的栈帧 → (file, line, function)。

    沿 sys._getframe 走，跳过所有 f_globals.__file__ 命中 legacy_fast/fastplugin 的帧
    （垫片内部 helper 再多也不冒充调用方）。栈尽则如实标 unknown——归因宁可缺也不假。
    """
    frame = sys._getframe(1)
    while frame is not None:
        fname = frame.f_globals.get("__file__", "")
        if fname and Path(fname).resolve().stem in ("legacy_fast", "fastplugin"):
            frame = frame.f_back
            continue
        if frame.f_code.co_filename == _THIS_FILE or (
                frame.f_globals.get("__name__", "") == "legacy_fast"):
            frame = frame.f_back
            continue
        if frame is not None:
            return (frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name)
    return ("unknown", 0, "unknown")


# ---------------------------------------------------------------------------
# JS 条件拼装（唯一拼装点）
# ---------------------------------------------------------------------------


def build_condition(layers, *, quiet_ms: int = _DEFAULT_QUIET_MS,
                    anchor: str = "") -> str:
    """按档位拼**状态码**判据 JS（恒含 readyState 门 + mask；档位递加；锚点可选）。

    次序是判据的一部分，不是排版：忙碌段（mask/xhr/mutation）在前——「此刻仍在忙」必须
    被记成忙碌观测，不能被后面的门抢先答成「还没静默」；门（readyState/锚点）在中；
    `quiet` 只在最后才出。**求值不了的一段**（hook 不在场）出 `no-hook`/`no-activity`：
    继续等，且**不**计入忙碌观测——用「我不知道」换一次提前放行，就是设计 §A 点名的
    「xhr 恒 0 假静默」的姊妹形态。
    """
    body = [_JS_VISIBLE_FN]
    if "mask" in layers or not layers:
        body.append(_JS_MASK_BUSY_CLAUSE)
    if "xhr" in layers:
        body.append(_JS_XHR_GATE_CLAUSE)
        body.append(_JS_XHR_BUSY_CLAUSE)
    if "mutation" in layers:
        body.append(_JS_MUTATION_CLAUSE_TEMPLATE.replace(
            "{quiet_ms}", str(int(quiet_ms))))
    body.append(_JS_NOT_READY_CLAUSE)
    if anchor:
        # json.dumps 出合法 JS 字符串字面量（含引号的 CSS 也转义，非 ASCII 走 \\uXXXX）
        body.append(_JS_ANCHOR_CLAUSE_TEMPLATE.replace(
            "{anchor}", json.dumps(str(anchor))))
    body.append(_JS_RETURN_QUIET)
    return "() => {" + "".join(body) + " }"


def _classify_verdict(raw) -> str:
    """把 page.evaluate 的返回码归成 busy/quiet/gate 三类（未知形态→gate，fail-closed）。

    **非字符串一律按 gate**：旧布尔协议在这条线上被如实判成「不可评估」——`True` 不再
    意味着可以提前，因为布尔表达不了「曾忙碌」（lead P0 裁定的必需信息）。这不是兼容破
    口，是判据本体：宁可睡满预算（慢），不放过「压根没活动」（错）。
    """
    if isinstance(raw, str):
        if raw.startswith(_BUSY_PREFIX):
            return _KIND_BUSY
        if raw == _CODE_QUIET:
            return _KIND_QUIET
    return _KIND_GATE


def _install_hook(page, hook: str, present_check: str):
    """装页内 hook：add_init_script 尽力而为（导航后重放）+ evaluate 补当前文档。

    返回 (ok, exc)：ok=判据求值为真（hook 真在场）。装不上≠静默——调用方按判据 3
    降级并 WARN。
    """
    adder = getattr(page, "add_init_script", None)
    if callable(adder):
        try:
            adder(hook)
        except Exception:
            pass  # 导航级注入尽力而为；当前文档还有 evaluate 兜底
    try:
        page.evaluate(hook)
        return bool(page.evaluate(present_check)), None
    except Exception as exc:
        return False, exc


# ---------------------------------------------------------------------------
# 调用点规则（site rules）：把「通用档位」换成「该等待点真正在等的条件」
# ---------------------------------------------------------------------------
# 通用档位（mask/xhr/mutation）只能表达「页面此刻静不静」，表达不了「这次等待在等
# 什么」。真机 A/B 的失败点前移全部发生在**数据依赖等待**上：点查询后的 5000ms 是等
# 表格刷新、点菜单后的 3000ms 是等路由到列表页——遮罩消失时这两件事都可能还没完成。
# 规则层让「每个等待点」声明自己的就绪条件（JS/URL/遮罩循环），命中则按条件放行；
# 条件超时一律睡满剩余（fail-safe），未命中的点仍走通用档。规则文件由
# ATLAS_FAST_SITE_RULES 指向（business 字面量不进代码，红线 #11）。
_RULE_KINDS = ("js", "url", "mask_gone", "mask_cycle", "never")

_RULE_MASK_VISIBLE_JS = (
    f"() => Array.from(document.querySelectorAll('{MASK_PROBE_SELECTOR}')).some(function (el) {{"
    " var r = el.getBoundingClientRect();"
    " return r.width > 0 && r.height > 0; })"
)
_RULE_MASK_GONE_JS = (
    f"() => !Array.from(document.querySelectorAll('{MASK_PROBE_SELECTOR}')).some(function (el) {{"
    " var r = el.getBoundingClientRect();"
    " return r.width > 0 && r.height > 0; })"
)


def _rule_problem(item) -> str:
    """一条规则的最小形态校验；返回问题文本（空=合法）。"""
    if not isinstance(item, dict):
        return "不是对象"
    if not isinstance(item.get("file"), str) or not item["file"]:
        return "缺 file（旧仓文件名，basename 匹配）"
    line = item.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
        return "line 必须是正整数"
    kind = item.get("kind")
    if kind not in _RULE_KINDS:
        return f"kind={kind!r} 未知（只许 {sorted(_RULE_KINDS)}）"
    if kind == "js" and not isinstance(item.get("js"), str):
        return "js 规则缺 js 表达式"
    if kind == "url" and not isinstance(item.get("contains"), str):
        return "url 规则缺 contains"
    if "func" in item and not isinstance(item["func"], str):
        return "func 若给必须是字符串"
    return ""


def load_site_rules(path) -> list:
    """读规则 JSON；坏文件/坏条目 WARN 跳过——规则是加速面，不许把旧用例拖崩。

    文件形态：`{"rules": [...]}`（也接受裸列表）。条目字段：
    `file`（basename）、`line`（等待调用点行号）、`kind`（js/url/mask_gone/
    mask_cycle）、`func`（可选，匹配调用函数名）、`js`/`contains`（按 kind）、
    `appear_ms`（mask_cycle 等遮罩出现的窗口，默认 2000）。
    """
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as exc:
        print(f"WARN: fast waits 规则文件不可用（{path}）："
              f"{type(exc).__name__}: {exc}")
        return []
    raw = doc.get("rules") if isinstance(doc, dict) else doc
    if not isinstance(raw, list):
        print("WARN: fast waits 规则文件形态不对（缺 rules 列表），本次只用通用档")
        return []
    rules = []
    for item in raw:
        problem = _rule_problem(item)
        if problem:
            tag = ""
            if isinstance(item, dict):
                tag = f"{item.get('file')}:{item.get('line')} "
            print(f"WARN: fast waits 规则跳过 {tag}（{problem}）")
            continue
        rules.append(dict(item))
    return rules


def _match_rule(rules, caller):
    """按「文件名 basename + 调用行号（+ 可选函数名）」匹配调用点规则。"""
    if not rules:
        return None
    file_name = os.path.basename(caller[0] or "")
    line = caller[1]
    func = caller[2]
    for rule in rules:
        if rule["file"] != file_name or rule["line"] != line:
            continue
        want_func = rule.get("func")
        if want_func and want_func != func:
            continue
        return rule
    return None


def _poll_js(page, js: str, budget_ms: int, clk) -> bool:
    """在预算内用 evaluate 轮询语义条件，True=满足/False=超时。

    不用 `wait_for_function`：真机实测它在页面导航中会把 6s 预算拖成 9.7s（Playwright
    导航后重新绑定帧），违反「绝不更长」。这里与通用档同构——进程侧 tick 轮询，预算
    严格由 `budget_ms` 截断；导航瞬态（求值抛同源文本）视为「还不能评估」，继续等。

    **兼作「睡满」的载体**：tick 之间调用 `page.evaluate`，同时喂 Playwright 同步 API
    的消息泵。整块 `time.sleep` 不泵消息，真机实测会让随后的 `Locator.evaluate` 超时
    （同配置间歇复现）——所以 never/超时补睡都走本函数传 `_RULE_FALSE_JS`。
    """
    t0 = clk.now_ms()
    while True:
        try:
            if page.evaluate(js):
                return True
        except Exception as exc:
            if _is_transient(exc):
                pass
            elif _is_unrecoverable(exc):
                raise
            else:
                raise
        rem = budget_ms - (clk.now_ms() - t0)
        if rem <= 0:
            return False
        clk.sleep_ms(min(_TICK_MS, rem))


def _settle_rule(page, max_ms: int, rule: dict, clk, sleep_fn) -> tuple:
    """按调用点规则等待，返回 (actual_ms, note)。

    note="" 条件满足提前返回；`rule-timeout` 条件未满足，睡满剩余预算
    （mask_cycle 的「遮罩从未出现」同样睡满——请求没发出的窗口只能由固定等待兜）。
    规则路径**不**使用 busy_seen：语义条件比通用档更精确，不需要「先忙后静」的概
    率准入；这也让同一等待点的节省不再取决于运行时运气。

    `sleep_fn(ms)` = 调用**原始** `wait_for_timeout` 的剩余量。**绝不用整块
    `time.sleep`**：真机实测它不喂 Playwright 同步 API 的消息泵，6s 整块睡后
    `Locator.evaluate` 会 30s 超时（同配置间歇复现；基线的原生等待、通用档的
    25ms tick+求值都不出这个问题）。这里「已 tick 过的 + 剩余」= 原预算，不叠加。
    """
    t0 = clk.now_ms()
    kind = rule["kind"]

    def _remaining():
        return max(0, max_ms - int(round(clk.now_ms() - t0)))

    def _sleep_out():
        rem = _remaining()
        if rem > 0:
            sleep_fn(rem)

    if kind == "never":
        # 黑名单点：已证实「任何提前都会改行为」（如语言切换点击后的 reload 窗口，
        # 既无可靠 DOM 完成信号，遮罩也会先于页面重载消失）→ 原样睡满（旧行为等价）。
        _sleep_out()
        return int(round(clk.now_ms() - t0)), ""
    if kind == "js":
        ok = _poll_js(page, rule["js"], _remaining(), clk)
    elif kind == "url":
        ok = _poll_js(
            page,
            "() => document.location.href.includes(" + json.dumps(rule["contains"]) + ")",
            _remaining(), clk)
    elif kind == "mask_gone":
        ok = _poll_js(page, _RULE_MASK_GONE_JS, _remaining(), clk)
    else:  # mask_cycle
        appear_ms = min(int(rule.get("appear_ms", 2000)), _remaining())
        if not _poll_js(page, _RULE_MASK_VISIBLE_JS, appear_ms, clk):
            # 遮罩从未出现=「请求可能还没发出」的窗口，不能赌它已经完成 → 睡满
            _sleep_out()
            return int(round(clk.now_ms() - t0)), "rule-timeout"
        ok = _poll_js(page, _RULE_MASK_GONE_JS, _remaining(), clk)

    if ok:
        return int(round(clk.now_ms() - t0)), ""
    _sleep_out()
    return int(round(clk.now_ms() - t0)), "rule-timeout"


# ---------------------------------------------------------------------------
# settle：预算制有界语义等待
# ---------------------------------------------------------------------------


def _settle_probe(page, max_ms: int, layers: tuple, quiet_ms, min_ms, anchor,
                  clk) -> tuple:
    """在 page 上有界 settle，返回 (actual_ms, note, effective_layers)。

    note="" 正常；`hook-degraded:*` 表示档位如实降级（effective 随之降为 ("mask",)）；
    `no-busy-observed` 表示**本次调用从头到尾没观测到忙碌信号**，按判据 2 睡满了预算
    （两者可并存，`_join_notes` 拼装）。
    抛错语义（判据 3 的三分）：
    - `_NoProbeCapability`：page 没有 evaluate，调用方回退原始睡眠；
    - `_Unrecoverable`：Target closed 族——**已按预算睡完**，调用方原样抛 cause；
    - 其它异常原样抛（调用方回退原始睡眠的**剩余量**）。
    导航瞬态在本函数内消化（视为未静默，继续等到预算耗尽）。
    """
    if not callable(getattr(page, "evaluate", None)):
        raise _NoProbeCapability()

    t0 = clk.now_ms()
    effective = list(layers) or ["mask"]
    degraded = ""

    for layer, hook, present in _HOOK_INSTALLS:
        if layer not in effective:
            continue
        ok, exc = _install_hook(page, hook, present)
        if ok:
            continue
        # 设计 §A：hook 丢失/未装上 → 自动降 mask + WARN 一行（降级是设计行为，
        # 但降到的那一档必须如实可见，不留「我以为在判 xhr」的假账）。
        why = type(exc).__name__ if exc else "presence check false"
        print(f"WARN: fast waits {layer} hook 未生效（{why}），本次降级为 mask 档"
              f"（导航清 hook 时的既定行为；stats 记实际生效档位）")
        effective = ["mask"]
        degraded = f"hook-degraded:{layer}:{why}"
        break

    # quiet 窗受预算截断（设计 §A：「quiet 窗本身受『绝不更长』预算约束」）
    quiet = min(int(quiet_ms), max_ms) if "mutation" in effective else 0
    condition = build_condition(effective, quiet_ms=quiet, anchor=anchor)
    eff = tuple(effective)
    # 忙碌观测**按调用记**（进程侧局部量）：页侧粘滞标记会被上一次等待见过的遮罩解锁
    # 这一次「无遮罩等待」，等于从另一个口子把 P0 请回来。导航后重置同样是这个理由。
    busy_seen = False

    while True:
        remaining = max_ms - (clk.now_ms() - t0)
        if remaining <= 0:
            # 预算耗尽 = 恰 ms 返回（判据 1 的另一半：绝不更长，也绝不更短）。
            # 没见过忙碌信号时在这里补 no-busy-observed：这一笔**不是**提前放行，
            # 睡满 requested 与旧行为逐字等价（A/B 差分按该标记分组）。
            # 页侧求值自身反噬预算（cost>remaining）时这里同样如实记 max_ms：垫片**没**
            # 为它补睡（下面 sleep 一律 min(tick, remaining)），账面也不许把页侧耗时
            # 记成「省了」（xcheck C1 x2 格同一口径：判据钉的是垫片不添油加醋）。
            return int(max_ms), _join_notes(
                degraded, "" if busy_seen else _NOTE_NO_BUSY), eff
        try:
            kind = _classify_verdict(page.evaluate(condition))
        except Exception as exc:
            if _is_transient(exc):
                kind = _KIND_GATE                # 导航中=未静默，预算内继续等
            elif _is_unrecoverable(exc):
                clk.sleep_ms(max(0.0, max_ms - (clk.now_ms() - t0)))   # 按预算睡完
                raise _Unrecoverable(exc) from None  # 原语义继续传播（不吞不改）
            else:
                raise
        if kind == _KIND_BUSY:
            busy_seen = True                     # 见过忙碌 ≠ 可以走，只是拿到走的资格
        elif kind == _KIND_QUIET and busy_seen:
            # 唯一的提前出口：忙碌→静默的转换被**本调用**观测到（判据 2）
            spent = clk.now_ms() - t0
            floor = min(int(min_ms), int(max_ms))    # 下限永不把预算顶破（G11-1）
            if floor - spent > 0:
                clk.sleep_ms(floor - spent)          # 有界下限补睡（进程侧 sleep，判据 4）
                spent = clk.now_ms() - t0
            return int(round(spent)), degraded, eff
        # 此刻静默但从未见过忙碌 → **不提前**，继续睡到预算耗尽（P0 的修法本体）
        clk.sleep_ms(min(_TICK_MS, max(0.0, max_ms - (clk.now_ms() - t0))))


def settle(page, max_ms, *, probe: str = "mask", quiet_ms=None, min_ms=None,
           anchor: str = "", clock=None) -> tuple:
    """公开语义 settle（契约签名）：等界面**从忙碌落到安静**，最晚不超 max_ms。

    返回 (actual_ms, note)——G11-1 明写这条契约不变（二元组、语义不变，只有**提前
    的准入**收紧了）。note="" 正常；`hook-degraded:*`=档位如实降级；
    `no-busy-observed`=本次调用从头到尾没见过忙碌信号，因此**睡满了 max_ms**
    （与旧行为逐字等价，不是回退、不是失败，是判据 2 的 fail-safe 面）。
    硬边界：**最坏=原等待，绝不更长**（预算制），**下限 min_ms 也不越顶**
    （`min_ms > max_ms` 按 max_ms 截断）。探针装不上/求值抛错时**抛给调用方**处理
    （垫片内回退由 install 的 wrapper 负责，本函数是纯语义面，供判据测直用）。
    """
    layers = layers_for(_normalize_probe(probe))
    if not layers:
        raise ValueError(f"未知探针 {probe!r}：只许 {sorted(_TIERS)}"
                         "（或空=关闭，关闭请直接调原始 wait_for_timeout）")
    clk = clock if clock is not None else _RealClock
    actual, note, _ = _settle_probe(page, int(max_ms), tuple(layers),
                                    _DEFAULT_QUIET_MS if quiet_ms is None
                                    else int(quiet_ms),
                                    _DEFAULT_MIN_MS if min_ms is None else int(min_ms),
                                    str(anchor or ""), clk)
    return actual, note


def _normalize_probe(probe) -> str:
    """直调 API 的档位名归一：与 env 门共用一张表（不留第二份档位判据，#92）。"""
    if not isinstance(probe, str):
        return ""
    raw = probe.strip().lower()
    if raw in _ON_VALUES:
        return "mask"
    return raw if raw in _TIERS else ""


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


def install_fast_waits(target, *, probe: str = "mask", quiet_ms=None, min_ms=None,
                       anchor: str = "", stats=None, clock=None, site_rules=None):
    """把 target 的 wait_for_timeout 换成预算制有界 settle；返回 FastWaitStats。

    target 可以是**页实例**（该页局部生效）或 **Page 类**（类级生效——旧 conftest
    自己 new 出来的页也覆盖，fastplugin 用这条路）。同一 target 重复 install 只更新
    参数，不二次包裹（绝不递归）。原始函数引用在 install 时捕获进闭包：回退时调的是
    **原始**等待的**剩余量**，不是被替换后的属性、也不是全额（判据 1）。

    stats 的 `fallback` 单字段自此承载两类事实（`;` 连接，七键面不变）：
    **真回退**=`probe-error:*` / `no evaluate` / `unrecoverable:*` / `hook-degraded:*`，
    与 **fail-safe 睡满**=`no-busy-observed`（本次等待从头到尾没见过忙碌信号，于是按
    旧行为睡满——不是回退、不是错误）。聚合面分开计数（`fallbacks` 只数前者，
    `no_busy_observed` 只数后者），A/B 判读口径：`fallbacks=0` 仍=「探针一次没错」，
    `no_busy_observed` 上升=「垫片没插手的那批等待在正常工作」。**别把两者相加当
    失败率**——同一笔可以两条都占。
    """
    layers = layers_for(_normalize_probe(probe))
    if not layers:
        raise ValueError(f"未知探针 {probe!r}：只许 {sorted(_TIERS)}")
    # None=用默认（400/200），显式值才校验；min_ms 允许 0（A/B 要量「无下限」那一档）
    if min_ms is not None and (isinstance(min_ms, bool)
                               or not isinstance(min_ms, (int, float))
                               or min_ms < 0):
        raise ValueError(f"min_ms 必须为非负数标量或 None（收到 {min_ms!r}）；"
                         "要取消下限请显式写 0")
    if quiet_ms is not None and (isinstance(quiet_ms, bool)
                                 or not isinstance(quiet_ms, (int, float))
                                 or quiet_ms <= 0):
        raise ValueError(f"quiet_ms 必须为正数标量或 None（收到 {quiet_ms!r}）")
    if not isinstance(anchor, str):
        raise ValueError(f"anchor 必须是选择器字符串（收到 {type(anchor).__name__}）")

    key = id(target)
    state = _INSTALLED.get(key)
    if state is not None and state[0] is target:
        _orig = state[1]  # 复用捕获的原始引用——绝不从已替换的属性再取（递归防线）
    else:
        _orig = target.wait_for_timeout

    if stats is None:
        stats = FastWaitStats()
    clk = clock if clock is not None else _RealClock
    quiet = _DEFAULT_QUIET_MS if quiet_ms is None else int(quiet_ms)
    floor = _DEFAULT_MIN_MS if min_ms is None else int(min_ms)
    # 调用点规则：显式传入优先；否则读 env（未设置=纯通用档，行为与 #36 后一致）
    if site_rules is None:
        rules_path = os.environ.get("ATLAS_FAST_SITE_RULES", "").strip()
        rules = load_site_rules(rules_path) if rules_path else []
    elif isinstance(site_rules, str):
        rules = load_site_rules(site_rules)
    else:
        # 显式传入的规则同样过形态校验（坏条目跳过并 WARN，不把旧用例拖崩）
        rules = []
        for item in site_rules or []:
            problem = _rule_problem(item)
            if problem:
                print(f"WARN: fast waits 规则跳过（{problem}）")
                continue
            rules.append(dict(item))
    the_anchor = anchor
    is_class = isinstance(target, type)

    def _call_original(the_page, ms):
        if is_class:
            _orig(the_page, ms)
        else:
            _orig(ms)

    def _run(the_page, requested):
        try:
            req = int(requested)
        except (TypeError, ValueError):
            # 非数值 requested：垫片不猜语义，原样转交**原始**等待（旧调用方负责自己形态）
            return _call_original(the_page, requested)
        if req <= 0:
            # 0/负数都转交原始：0 是 no-op，负数保持旧实现抛给 playwright 的语义——
            # 垫片不自己解释「≤0 该怎样」，也不记 stats（根本没有等待发生）。
            return _call_original(the_page, requested)
        caller = _caller_frame()
        t0 = clk.now_ms()
        note = ""
        probe_label = "+".join(layers)          # 名义档位；异常路径无 effective 可记
        rule = (_match_rule(rules, caller)
                if callable(getattr(the_page, "evaluate", None)) else None)
        try:
            if rule is not None:
                # 规则路径：按该等待点的语义条件放行；超时用**原始**等待睡满剩余
                # （sleep_fn 走 _call_original：绝不用整块 time.sleep，见 _settle_rule）
                actual, note = _settle_rule(the_page, req, rule, clk,
                                            lambda ms: _call_original(the_page, ms))
                probe_label = "rule:" + rule["kind"]
            else:
                actual, note, eff = _settle_probe(the_page, req, layers, quiet, floor,
                                                  the_anchor, clk)
                probe_label = "+".join(eff)      # 降级后如实记实际生效档位（不留假账）
        except _NoProbeCapability:
            _call_original(the_page, max(0, req - (clk.now_ms() - t0)))
            actual, note = int(round(clk.now_ms() - t0)), "no evaluate"
        except _Unrecoverable as un:
            # 预算已在 _settle_probe 内睡满：记账后把原语义抛出去
            stats.record(caller_file=caller[0], caller_line=caller[1],
                         caller_func=caller[2], requested_ms=req,
                         actual_ms=min(req, int(round(clk.now_ms() - t0))),
                         probe=probe_label,
                         fallback=f"unrecoverable:{type(un.cause).__name__}")
            raise un.cause from un
        except AssertionError:
            # 用例失败**永不被降级**（lead 追加 1，engine `_perform` 只捕
            # (NotImplementedError, RuntimeError) 同族）：AssertionError 是 Exception
            # 子类，今天 Playwright 不产它，但形状入口在——吞下去就是把失败洗成「继续跑」
            # （红线 #7 的入口形态，与「定性器先于被定性对象死」#89 一并防）。
            raise
        except Exception as exc:
            rem = max(0, req - (clk.now_ms() - t0))
            _call_original(the_page, rem)        # 回退**剩余量**（全额=违反判据 1）
            actual = int(round(clk.now_ms() - t0))
            note = f"probe-error:{type(exc).__name__}"
        stats.record(caller_file=caller[0], caller_line=caller[1], caller_func=caller[2],
                     requested_ms=req, actual_ms=actual, probe=probe_label,
                     fallback=note)
        return None

    if is_class:
        def _patched(self, timeout=0, _r=_run):
            return _r(self, timeout)
        target.wait_for_timeout = _patched
        wrapper = _patched
    else:
        def _patched(timeout=0, _r=_run):
            return _r(target, timeout)
        target.wait_for_timeout = _patched
        wrapper = _patched

    _INSTALLED[key] = (target, _orig, wrapper)
    return stats


def uninstall(target=None) -> int:
    """还原被替换的 wait_for_timeout；幂等——重复调用/未装过都返回 0。

    target=None → 还原全部登记项（fastplugin 的 pytest_unconfigure 用这条路）。
    类级还原用原始函数赋回；实例级直接删掉 shadow 属性（恢复类方法原状）。
    """
    count = 0
    keys = list(_INSTALLED.keys()) if target is None else [id(target)]
    for key in keys:
        state = _INSTALLED.pop(key, None)
        if state is None:
            continue
        obj, original, wrapper = state
        if getattr(obj, "wait_for_timeout", None) is wrapper:
            if isinstance(obj, type):
                obj.wait_for_timeout = original
            else:
                try:
                    del obj.wait_for_timeout
                except AttributeError:
                    pass
            count += 1
    return count


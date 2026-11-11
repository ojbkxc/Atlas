"""C3 迁移验收三件套 · 哨兵双向断言（design_c3 §2/§3；W3 owner: impl_migrate）。

三选一方向（`SentinelRule.direction`）：
- **erosion**：吸收件自有改造锚点**必须在**——被回退即红（LiteFlow/legacy_case 的改造行）。
- **resurrection**：搬走的公共实现**不得在迁入侧重定义**——legacy_case 桥的 `run_case_py`
  播种逻辑唯一真相在旧 runner（迁移方案 R5 红线），迁入侧（桥本体 + flows/iservice）
  重新出现定义形态即红。
- **forbidden-marker**：删掉的面不得被干净合并带回来（`lane:` 死键、陈旧文档数 720、
  `shell=True`、内联 `(KeyboardInterrupt, SystemExit)` 双元组、调度核外的 cron 依赖）。

锚点核定（design_c3 §10，W3 第一步已逐一 grep 对磁盘原文）：
- dsl.py `FlowDocument/_walk_marks/"--> "/_check_slots/dispatch` 在位；engine.py
  `_flush_degradations/_flush_routes` 在位；recorder.py `_safe_default` 在位；
  failures.py `CONTROL_FLOW` 在位；legacy_case.py `runner.run_case_py(` 委托在位。
- `(KeyboardInterrupt, SystemExit)` 双元组：engine/healing/recorder 磁盘 0 命中
  （failures.py 的三元组定义是**单一真相本体**，不在 forbidden scope）。
- `lane:`：dsl.py 磁盘 0 命中（`lanes` 内部属性名不含 `lane:` 子串，已核对）。
- 文档陈旧数：README:409 的「720→772」是**增幅历史记录**、合法；故 marker 收紧为
  「当前值声称」形态 `720 passed` / `tests-720`（磁盘 0 命中）。口径现为 1327（W2 后）。
- flows 侧 `extract_steps:` 在 tc01 yaml **注释行**出现（源码用例出处说明）——扫描前
  按行剥 `#` 注释（见 `_scan_text`），结构重排形态只在非注释面判红。
- `shell=True`：原 scripts/linux_acceptance.py 预存违例已**清偿**（argv 化，
  2026-11-06 #1 小修，全仓 0 命中）；哨兵 scope 保留 src/atlas 防回归。

变异注入自检纪律（design_c3 §3，手动一次性、禁 mutmut/cosmic-ray——治理取舍=门禁
可复现性，非红线字面禁，gap6 E2 同款口径）：
- 每条规则必须有 MUTATION_RECORDS 记录，**无记录=该哨兵视同不存在**（M11 语义）；
  `test_mutation_records_complete` 把纪律做成门禁，`test_injected_rule_goes_red`
  用 tmp 副本重放注入手法（等效于 revert 磁盘原文；W1/W2 改动未提交，**绝不直接
  revert 真文件**——temp 复制注入不碰 git 工作区）。
- 失败处置纪律：哨兵红 = **「重放自有改造」不是删断言**；resurrection 报「像被删不
  像被搬」先 `git log -S <锚> -- <scope>` 找去向再决定恢复。

红线 #11：marker 全是代码锚点字符串，零业务字面量。X1：scope 为 POSIX 相对 glob。
跨平台：读侧 `errors="replace"`（与 review_code 同纪律，防 .pyc/坏字节整份崩）。
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SentinelRule:
    name: str                    # 例 "liteflow-atomic-compile"
    direction: str               # "erosion" | "resurrection" | "forbidden-marker"
    scope: tuple[str, ...]       # POSIX 相对 glob，如 ("src/atlas/dsl.py",)
    must_contain: tuple[str, ...] = ()   # erosion：自有改造锚点（存在才过）
    origin: str = ""             # resurrection：搬出目标（旧 runner 在仓外，写说明）
    symbols: tuple[str, ...] = ()        # resurrection：搬走的实现不得在迁入侧重定义
    markers: tuple[str, ...] = ()        # forbidden-marker：必须保持「不存在」的字面


DIRECTIONS = ("erosion", "resurrection", "forbidden-marker")

# ── 首批规则（§2；锚点均按 2026-11-06 磁盘原文核定）───────────────────────

RULES = (
    # erosion：LiteFlow 吸收件不回退
    SentinelRule(
        name="liteflow-atomic-compile",
        direction="erosion",
        scope=("src/atlas/dsl.py",),
        must_contain=("FlowDocument", "_walk_marks", "--> ", "_check_slots",
                      "dispatch"),
    ),
    SentinelRule(
        name="liteflow-engine-flush",
        direction="erosion",
        scope=("src/atlas/engine.py",),
        must_contain=("_flush_degradations", "_flush_routes"),
    ),
    SentinelRule(
        name="recorder-fallback-default",
        direction="erosion",
        scope=("src/atlas/recorder.py",),
        must_contain=("_safe_default",),
    ),
    SentinelRule(
        name="control-flow-single-truth",
        direction="erosion",
        scope=("src/atlas/failures.py",),
        must_contain=("CONTROL_FLOW",),
    ),
    SentinelRule(
        name="legacy-case-delegation",
        direction="erosion",
        scope=("src/atlas/plugins/demo/legacy_case.py",),
        must_contain=("def _load_runner", "runner.run_case_py("),
    ),
    # resurrection：桥接只许委托旧 runner（R5 红线），播种逻辑不得在迁入侧重现
    SentinelRule(
        name="legacy-seeding-not-reforked-bridge",
        direction="resurrection",
        scope=("src/atlas/plugins/demo/legacy_case.py",),
        origin="旧项目 lib.runner.run_case_py（ATLAS_LEGACY_WEB，仓外）",
        symbols=('def run_case_py', 'ctx["_data"] ='),
    ),
    SentinelRule(
        name="legacy-seeding-not-reforked-flows",
        direction="resurrection",
        scope=("flows/iservice/**/*.yaml",),
        origin="旧骨架 yaml 的 test_data/assert_steps/extract_steps/cases 播种形态",
        symbols=("test_data:", "assert_steps:", "extract_steps:", "cases:"),
    ),
    # forbidden-marker：删掉的面不复活
    SentinelRule(
        name="dsl-lane-key-dead",
        direction="forbidden-marker",
        scope=("src/atlas/dsl.py",),
        markers=("lane:",),
    ),
    SentinelRule(
        name="doc-stale-count-720",
        direction="forbidden-marker",
        scope=("README.md", "AGENTS.md", "CLAUDE.md"),
        # 口径现 1327；「720→772」增幅史句合法，禁的是把 720 当**当前值**的声称形态
        markers=("720 passed", "tests-720"),
    ),
    SentinelRule(
        name="engine-no-shell-true",
        direction="forbidden-marker",
        scope=("src/atlas/**/*.py",),
        markers=("shell=True",),   # X2；linux_acceptance.py 原预存债已清偿见 doc
    ),
    SentinelRule(
        name="ki-tuple-single-truth",
        direction="forbidden-marker",
        scope=("src/atlas/engine.py", "src/atlas/healing.py",
               "src/atlas/recorder.py"),
        markers=("(KeyboardInterrupt, SystemExit)",),  # #114 单一真相第 5 处漏网反钉
    ),
    SentinelRule(
        name="scheduling-cron-impl-single",
        direction="forbidden-marker",
        scope=("src/atlas/scheduling/*.py",),
        # C1 交接（impl_sched）：自写 5 段解析器是唯一实现；「同一能力两份实现」防重定义。
        # import 形态判据（cron_parse.py 的论证注释提及 croniter/APScheduler 字样，
        # 不含 import 行，不会误红）。APScheduler 确触 §4 常驻运行时禁；croniter 不引
        # 是「纯 stdlib 先例+治理取舍」（E2 口径，理由不得写成红线禁止）。
        markers=("import croniter", "from croniter",
                 "import apscheduler", "from apscheduler"),
    ),
)

BY_RULE = {r.name: r for r in RULES}

# ── 变异注入自检记录表（design_c3 §3；逐条一次性验证，M11：无记录=不存在）────
# 记录字段：注入手法 / 期望红的测试 / 验证日期 / by。
# 验证方式：本波用 tmp 副本注入（`test_injected_rule_goes_red` 在每次全门禁里重放
# 同一手法），等效「故意 revert 锚点」且不碰 W1/W2 未提交的工作区。

MUTATION_RECORDS: dict[str, dict[str, str]] = {
    "liteflow-atomic-compile": {
        "injection": "把副本中 `FlowDocument` 全部改名（模拟改造行被回退）",
        "expect_red": "test_injected_rule_goes_red[liteflow-atomic-compile]（同 "
                      "test_real_rules_green[liteflow-atomic-compile]）",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "liteflow-engine-flush": {
        "injection": "把副本中 `_flush_degradations` 改名（游标搬运回退=降级无痕复活）",
        "expect_red": "test_injected_rule_goes_red[liteflow-engine-flush]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "recorder-fallback-default": {
        "injection": "把副本中 `_safe_default` 改名（default=str 兜底被回退=坏 __str__ 丢整轮）",
        "expect_red": "test_injected_rule_goes_red[recorder-fallback-default]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "control-flow-single-truth": {
        "injection": "把副本中 `CONTROL_FLOW` 改名（单一真相本体失踪）",
        "expect_red": "test_injected_rule_goes_red[control-flow-single-truth]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "legacy-case-delegation": {
        "injection": "把副本中 `runner.run_case_py(` 改名（委托被拆=桥开始自己干活）",
        "expect_red": "test_injected_rule_goes_red[legacy-case-delegation]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "legacy-seeding-not-reforked-bridge": {
        "injection": "副本追加一行 `def run_case_py(ctx): pass`（播种实现回迁入侧）",
        "expect_red": "test_injected_rule_goes_red[legacy-seeding-not-reforked-bridge]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "legacy-seeding-not-reforked-flows": {
        "injection": "副本 flow 尾加顶层 `test_data: {}` 键（旧骨架播种形态回灌）",
        "expect_red": "test_injected_rule_goes_red[legacy-seeding-not-reforked-flows]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "dsl-lane-key-dead": {
        "injection": "副本追加 `# lane: 复活测试`…剥注释后仍注入裸键行 `lane: x`",
        "expect_red": "test_injected_rule_goes_red[dsl-lane-key-dead]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "doc-stale-count-720": {
        "injection": "副本 README 尾加 `**720 passed**`（陈旧当前值声称）",
        "expect_red": "test_injected_rule_goes_red[doc-stale-count-720]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "engine-no-shell-true": {
        "injection": "副本 dsl.py 尾加 `subprocess.run(cmd, shell=True)`",
        "expect_red": "test_injected_rule_goes_red[engine-no-shell-true]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "ki-tuple-single-truth": {
        "injection": "副本 engine.py 尾加 `except (KeyboardInterrupt, SystemExit): pass`"
                     "（#114 内联第 5 处漏网的形态反向钉）",
        "expect_red": "test_injected_rule_goes_red[ki-tuple-single-truth]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
    "scheduling-cron-impl-single": {
        "injection": "副本 cron_parse.py 尾加 `import croniter`（同一能力第二实现回流）",
        "expect_red": "test_injected_rule_goes_red[scheduling-cron-impl-single]",
        "verified": "2026-11-06", "by": "impl_migrate",
    },
}


# ── 检查器（root 参数化：真盘与 tmp 副本共用同一判据，禁两份实现 #92）───────

def _scope_files(root, globs: tuple[str, ...]) -> list:
    files = []
    for pattern in globs:
        if any(ch in pattern for ch in "*?["):
            matched = sorted(root.glob(pattern))
        else:
            matched = [root / pattern]
        for m in matched:
            if m.is_file():
                files.append(m)
    return files


def _scan_text(path) -> str:
    """读文本并剥 `#` 注释行/段。

    - 读侧 `errors="replace"`（X5/#131 同纪律：坏字节不整份崩）。
    - 剥注释是**降敏**方向（注释里的锚字样不算重定义/回灌），真实代码形态才判红；
      已知代价：字符串字面量里的 `#` 之后内容同样不可见——首批 marker 均不含 `#`，
      扩大 marker 清单时需重估此点。
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for line in text.splitlines():
        cut = line.find("#")
        if cut == 0:
            out.append("")                      # 整行注释：剥净
        elif cut > 0:
            out.append(line[:cut])              # 行尾注释：剥 # 之后
        else:
            out.append(line)
    return "\n".join(out)


def check_rule(root, rule: SentinelRule) -> list[str]:
    """返回违规清单（空=绿）。scope 无文件=哨兵失效，直接点名（不给静默假绿）。"""
    files = _scope_files(root, rule.scope)
    if not files:
        return [f"{rule.name}: scope {list(rule.scope)} 无文件=哨兵失效"]
    blob = "\n".join(_scan_text(f) for f in files)
    violations: list[str] = []
    if rule.direction == "erosion":
        for anchor in rule.must_contain:
            if anchor not in blob:
                violations.append(f"{rule.name}: 自有改造锚点缺失 {anchor!r}（被回退？）")
    else:  # resurrection / forbidden-marker：判「不存在」
        needles = rule.symbols if rule.direction == "resurrection" else rule.markers
        for needle in needles:
            if needle in blob:
                src = rule.origin or "forbidden"
                violations.append(f"{rule.name}: {needle!r} 重新出现（{src}）")
    return violations


# ── 1. 真规则全绿（磁盘实态）───────────────────────────────────────────────

@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.name)
def test_real_rules_green(rule):
    assert check_rule(ROOT, rule) == [], f"哨兵红：{rule.name}"


# ── 2. 形态自检（数据即测试的前提：写反方向的规则自己要先红）──────────────

def test_rule_shapes_valid():
    names = set()
    for r in RULES:
        assert r.direction in DIRECTIONS, r.name
        assert r.scope, r.name
        assert r.name not in names, f"规则重名 {r.name}"
        names.add(r.name)
        if r.direction == "erosion":
            assert r.must_contain and not r.symbols and not r.markers, r.name
        elif r.direction == "resurrection":
            assert r.symbols and not r.must_contain, r.name
        else:
            assert r.markers and not r.must_contain and not r.symbols, r.name
    assert len(RULES) <= 12, "首批规则上限（design_c3 §3：手动一轮可覆盖）"


def test_scope_empty_is_violation(tmp_path):
    """scope 指空 = 哨兵自己失效，必须红（xplat 同款 scope_empty 纪律）。"""
    rule = SentinelRule(name="ghost", direction="erosion",
                        scope=("nowhere/**/*.py",), must_contain=("x",))
    v = check_rule(tmp_path, rule)
    assert v and "哨兵失效" in v[0]


# ── 3. 合成红样（三方向各钉判据方向性：该红的红、不该红的不红）────────────

def _mk(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_synthetic_erosion_red_and_green(tmp_path):
    rule = SentinelRule(name="e", direction="erosion", scope=("pkg/mod.py",),
                        must_contain=("_anchor_a", "_anchor_b"))
    _mk(tmp_path, "pkg/mod.py", "def _anchor_a(): ...\n")           # 缺 b → 红
    assert any("_anchor_b" in v for v in check_rule(tmp_path, rule))
    _mk(tmp_path, "pkg/mod.py", "_anchor_a\n_anchor_b\n")            # 齐 → 绿
    assert check_rule(tmp_path, rule) == []


def test_synthetic_resurrection_red_and_green(tmp_path):
    rule = SentinelRule(name="r", direction="resurrection", scope=("flows/x.yaml",),
                        origin="旧 runner", symbols=('def old_impl',))
    _mk(tmp_path, "flows/x.yaml", "# 注释里提 def old_impl 不算重定义\nsteps: []\n")
    assert check_rule(tmp_path, rule) == []                          # 注释面豁免
    _mk(tmp_path, "flows/x.yaml", "steps:\n  - def old_impl here\n")
    assert check_rule(tmp_path, rule)                                  # 代码面红


def test_synthetic_forbidden_marker_red(tmp_path):
    rule = SentinelRule(name="f", direction="forbidden-marker", scope=("src/a.py",),
                        markers=("banned_kw",))
    _mk(tmp_path, "src/a.py", "x = banned_kw\n")
    v = check_rule(tmp_path, rule)
    assert v and "重新出现" in v[0]


def test_bad_bytes_does_not_crash_scan(tmp_path):
    """读侧 replace（X5/#131）：坏字节文件点名继续，不 UnicodeDecodeError 整崩。"""
    rule = SentinelRule(name="b", direction="forbidden-marker", scope=("src/*.py",),
                        markers=("shell=True",))
    p = tmp_path / "src" / "junk.py"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"def \xff\xfe broken(\n")
    assert check_rule(tmp_path, rule) == []   # 不崩、无违规——评审继续


# ── 4. 变异自检记录表纪律（§8.2：缺记录=该哨兵视同不存在 → 元测试红）──────

def test_mutation_records_complete():
    assert set(MUTATION_RECORDS) == {r.name for r in RULES}, \
        "每条哨兵必须有变异注入记录，且不得有孤儿记录（M11）"
    for name, rec in MUTATION_RECORDS.items():
        assert rec["injection"], name
        assert rec["expect_red"], name
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rec["verified"]), \
            f"{name}: 验证日期必须是 YYYY-MM-DD"
        assert rec["by"], name


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.name)
def test_injected_rule_goes_red(rule):
    """变异注入重放：tmp 副本按记录表手法注入 → 同一 check_rule 必须红。

    「故意 revert 锚点」的等效实现（W1/W2 改动未提交，绝不直接改磁盘原文）；
    断言的不是表格存在、而是**表格内容今天仍然为真**——伪造记录活不过这条测。
    """
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        injected = False
        for src in _scope_files(ROOT, rule.scope):
            dst = root / src.relative_to(ROOT)
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8", errors="replace")
            if not injected:
                if rule.direction == "erosion":
                    text = text.replace(rule.must_contain[0], "~eroded~")
                elif rule.direction == "resurrection":
                    text += f"\n{rule.symbols[0]}\n"
                else:
                    text += f"\nx = '{rule.markers[0]}'\n"
                injected = True
            dst.write_text(text, encoding="utf-8")
        assert injected, f"{rule.name}: scope 无文件，无法重放注入"
        v = check_rule(root, rule)
        assert v, f"{rule.name}: 注入后哨兵仍绿=该断言失效（红样不红，红线没写）"

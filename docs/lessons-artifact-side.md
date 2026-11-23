# 产物侧缺陷族：13 波闭环的排查手册

> 2026-11-23 一整轮「分析不停完善」的沉淀。这 13 波全部是**产物侧**缺陷——不在本仓源码里，
> 而在 Atlas **写给别人的文件**里（`gen`/`export-pytest`/`import_jmeter` 的产物、`runs.jsonl`、
> review-code 的报告）。它们共同点：**离线全量测全绿、rc 0、产物看起来完整**，只有把
> 「生成 → 消费」两端拼起来、或用严档编译产物文件时才现形。
>
> 验证计数口径一律看 [README「当前验证边界」](../README.md)（lead 统一收口，四处同数）。

## 为什么这族缺陷最危险

1. **测不到自己写出去的东西**。生成期自检只编译/校验**本仓源码**，从不编译自己写出去的
   文件——而产物是「要交给别人独立运行」的包（自包含、零 `import atlas`），坏在产物里 =
   消费方一 import 就炸，但 Atlas 自己永远绿。
2. **两端各写一半**。生成器写 `save_as: resp_1_get-web-example-…`（带 `-`），解析器用 `\w+`
   （不认 `-`）——同一产品的两半对不起来，各自都「正确」，拼起来就丢断言。
3. **默认档不报错**。非法转义（`\s`）在默认档只是被忽略的 `SyntaxWarning`；`grade` 起点
   写错导致 `>` 永不成立——都不抛异常，于是都不修。

## 排查表（按「症状 → 族 → 判据」）

| # | 症状 | 族 | 判据单点 | 守卫 |
|---|---|---|---|---|
| 1 | 产物 `-W error::SyntaxWarning` 直接 SyntaxError | 转义面 | `dsl_utils.docstring_text` | `tests/test_artifact_docstring_safety.py` |
| 2 | `class get-web-example-…Probe(DataProbe):` 语法错 | 标识符面 | `emit_probe.probe_entity_name` | `tests/test_probe_skeleton.py` |
| 3 | 导出产物断言变 `TODO(人审)` | 生成↔解析两端 | `identifier_slug` + 键位 `[A-Za-z_][\w-]*` | `tests/test_generated_assert_roundtrip.py` |
| 4 | flow 级 Task 形态断言凭空消失 | 同上 | `emit_pytest.flow_assert_notes` | `tests/test_flow_assert_export_coverage.py` |
| 5 | 没发请求的步骤 `api_ms` = 上一步耗时 | 跨步粘性 | `CallApi.reset_timing()` | `tests/test_step_api_timing_attribution.py` |
| 6 | `gen` 产物连文件名都带 token | 凭据输入面 | `credential_gate.sanitize_url/text` | `tests/test_generator_secret_redaction.py` |
| 7 | JMeter 迁移 YAML 带字面凭据 | 同上（另一条链） | `import_jmeter.apply_credential_gate` | `tests/test_import_jmeter_secret_gate.py` |
| 8 | review-code 27 critical / grade F | 规则适用面（吞异常档） | `GREEN-001-DECLARED` | `tests/test_review_code_declared_swallow.py` |
| 9 | 注释/docstring 里的 URL 被判硬编码 | 规则适用面（注释档） | `SAFE-001-COMMENT` | `tests/test_review_code_safe001_noise.py` |
| 10 | 规则文档自己被打成「注释掉的断言」 | 规则适用面（文档档） | `_is_commented_assert_code` | `tests/test_review_code_assert006_noise.py` |
| 11 | 引擎轮询实现被判「请改用 waits」 | 规则适用面（测试 vs 引擎） | `_is_test_module` | `tests/test_review_code_maint001_scope.py` |
| 12 | `critical=0/avg=99` 报告仍 `grade: F` | 聚合初始化 | `max_grade = "A"` 起点 | `tests/test_review_code_summary_grade.py` |
| 13 | 探索跑完证据为空、终端零提示 | 证据链裸吞 | WARN + `sanitize_text` | `tests/test_autopilot_evidence_warn.py` |

另两条「已兑现的承诺钉成守卫」：`tests/test_runs_read_side_tolerance.py`（六类坏行不顶掉
整份）、`tests/test_headless_default.py`（无头默认——一行改动就反转全项目真机行为而离线
测看不出来）。

## 修法模式（可复用的四条）

1. **判据单点**：每波修完都把判据收敛到一个函数/常量（`#92` 不留第二把刀），
   消费方只许 import。本仓最典型的：`dsl_utils` 现在持有
   `docstring_text`/`identifier_slug`/`pascal_identifier`/`artifact_compile_problems`
   四把「产物写出去之前」的刀。
2. **写盘前严档自检**：`artifact_compile_problems` 按 CI 严档（`-W error::SyntaxWarning`）
   编译全部产物源码，`mkdir` 之前拒绝、零残留。三个包入口（`emit_pytest`/`emit_compat`/
   `emit_fast`）同一口径。
3. **变异验证**：每个守卫都做过「把修好的地方改回坏形态 → 精确红 → 恢复后 sha256 一致」。
   只跑绿不算证据（有一次守卫空转：裸 shell 行先撞 `MAINT-006` 语法错，根本没走到目标
   判据——改用合法 Python 承载同一形态后才真区分新旧）。
4. **适用面写清**：review-code 那五波全是同一种病——规则本身没错，**适用面**没写清
   （文档 vs 代码、注释 vs 可执行、测试 vs 引擎、有理由的吞 vs 裸吞）。修法不是调阈值，
   是给每条规则补「什么情况下这条不适用」的判据。

## 工具命令（下次遇到同族症状）

```bash
# 产物严档自检（快）：
python -W error::SyntaxWarning -m py_compile <产物>.py
# 产物内是否残留 atlas import（应零命中）：
grep -rE "^\s*(from|import)\s+atlas" <产物目录>
# 整树 review-code（适用面修复后的口径：0 critical 为基线）：
PYTHONPATH=src python -m atlas review-code src
# 守卫族全绿 + 计数四处同数：
PYTHONPATH=src python -m pytest -q
```

> 新增经验条目请追加在本表末尾并在 [reader-guide.md](reader-guide.md) 索引里补一行
> （AGENTS §5 文档维护义务）。

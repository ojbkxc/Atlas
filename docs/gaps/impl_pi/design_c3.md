# 设计 C3：迁移验收三件套（波次 W3，与 C5 并行）

> 上游：gap6 §3.1-C3、§4-C3、§9〔E4〕（磁盘无 `case-migration-mapping.csv`，实为 gitignore 的 `docs/case-migration-mapping.md` 且它是**映射清单不是失败集合基线**——基线产物必须新建；`review_code.py:791 diff_baseline` 已存在但比的是源码断言密度/弱化，与失败集合子集互补，不可读成已被覆盖）。
> 实施者：impl_migrate（任务 #7；不碰 cli.py/recorder/engine）。

## 1. 三件套与目标

给 772 门禁补「测试的测试」：① 哨兵双向断言（搬走的不得复活 / 自有改造不得被回退 / 删除的面不得被干净合并带回来）；② 变异注入自检（每条哨兵断言「失效时测试会红」，**手动一次性**验证并留档）；③ 失败集合子集对比（迁移引入的新破损 = 红，禁跳测试/降标准制造绿灯）。正处吸收窗口（LiteFlow/legacy_case/手册），先于治理自动化落地。

## 2. 哨兵：`tests/test_migration_sentinels.py`（新）

数据即测试（纯 stdlib，marker 全为代码锚点字符串，无业务字面量——红线 #11）：

```python
@dataclass(frozen=True)
class SentinelRule:
    name: str                    # 例 "liteflow-atomic-compile"
    direction: str               # "erosion" | "resurrection" | "forbidden-marker"
    scope: list[str]             # POSIX 相对 glob，如 ["src/atlas/dsl.py"]
    must_contain: list[str]      # erosion：自有改造锚点（存在才过）
    origin: str = ""             # resurrection：搬出目标文件
    symbols: list[str] = ()      # resurrection：搬走的实现不得在迁入侧重定义
    markers: list[str] = ()      # forbidden-marker：必须保持「不存在」的字面
```

首批规则（实现期逐条核对磁盘原文后钉死，锚点以盘为准）：
- **erosion**（吸收 LiteFlow 改造不回退）：dsl.py 含 `FlowDocument`/`_walk_marks`/`"--> "` 指针形态/`_check_slots`/`dispatch` 路由判据；engine.py 含 `_flush_degradations`/`_flush_routes`；recorder.py 含 `_safe_default`；failures.py 含 `CONTROL_FLOW` 且 **`engine.py`/`healing.py`/`recorder.py` 内联 `(KeyboardInterrupt, SystemExit)` 双元组为 forbidden-marker**（#114 单一真相第 5 处漏网的教训反向钉）。
- **resurrection**（legacy_case 桥语义不双份）：`flows/iservice/*.yaml` 与桥接测试侧不得重新实现 `run_case_py` 播种逻辑（symbol 锚 `ctx["_data"]` 直写、`step_N` 循环复刻）——桥接只许委托旧 runner（迁移方案 R5 红线）。
- **forbidden-marker**（删掉的面不复活）：dsl 语法 `lane:` 键（死字段清理已删）、文档陈旧数 `720`（README/CLAUDE/AGENTS 扫描——口径 772）、`shell=True`（X2 哨兵化）。

## 3. 变异注入自检（手动一次性，治理口径 = gap6 E2 续：不引 mutmut/cosmic-ray 是门禁可复现性取舍，非红线字面禁）

纪律（写进本文件头注释并执行一轮）：逐条规则**故意 revert 锚点**（复制回搬出实现 / 删掉改造行 / 把 lane 键加回）→ 对应哨兵测**必须红** → 恢复 → 在同文件表格里记 `rule | 注入手法 | 期望红的测试 | 验证日期 | by`。
- 哨兵数量有限（首批 ≤12 条），手动一轮可覆盖；**每条哨兵必须有验证记录，无记录=该哨兵视同不存在**（M11 语义：不加测的红线等于没写）。
- 失败处置纪律：**「重放自有改造」，不是删断言**——resurrection 报「像被删不像被搬」的形态（pi 侧项目选择器失踪案例）按删除排查，先 `git log -S` 找去向再决定恢复。

## 4. `scripts/migration_gate.py`（新）——失败集合子集对比

```python
def run_pytest(extra_args: list[str] = []) -> tuple[set[str], int]:
    """subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=no", "-rf",
       "--disable-warnings", *extra_args], cwd=root, text=True, encoding="utf-8",
       errors="replace", env={**os.environ,"PYTHONPATH":"src","PYTHONIOENCODING":"utf-8"})
       （X2/X5/X7 全套 argv）。解析行首 `FAILED ` / `ERROR ` 收 node id 集合 +
       `collected N` 的 N。返回 (failures, collected)。"""

def compare(baseline: set[str], current: set[str]) -> dict:
    """{"subset_ok": current ⊆ baseline, "new_failures": 排序点名, "resolved": 排序列出}
       减少=改善（列出）；新增=迁移引入破损（阻断）。"""

def main(argv) -> int:
    # --record-baseline → 写 reports/migration/failures-baseline.json（当前失败集快照，含
    #   generated_at/platform/python/count；reports/ 已 gitignore——测试名含内部用例号，敏感口径与映射表同级）
    # 默认：读基线对比。collected==0 → rc1 点名「静默空跑」（-k 打错/路径过滤，对 pytest
    #   同样成立的 SOP 检查点，fork-sync :80 案例）；无基线文件 → rc2 指引先 --record-baseline；
    #   有 new_failures → rc1 逐条点名。全绿且 subset_ok → rc0 且**明打「子集成立≠迁移成功，
    #   真机取证另跑」**（§6）。
```
追加写盘走断行纪律（X5，同 experience.py 手法）；JSON 读坏 → 点名「先重录基线」，不静默空比。

## 5. 与既有门的交叉关系（交付文档义务，写进 `docs/gaps/impl_pi/c3-baseline.md`）

| 门 | 量 | 不覆盖 |
|---|---|---|
| `review-code --baseline`（已存在） | 源码断言密度/弱化差分 | 测试**失败集合**子集 |
| `migration_gate`（本项） | 失败集合 ⊆ 基线、静默空跑 | 断言是否被悄悄放宽（源码面） |
| 真机取证（纪律） | fake/真实 schema 漂移（`No module named 'lib'`、`page_key` 教训） | —— 三者互补，**离线绿不可互相顶替**，更不可顶替真机 |
基线来源事实：30 条迁移映射的**清单**在 `docs/case-migration-mapping.md`（gitignore，逐条 TC→flow 对照）；本 gate 的失败集合基线是它之外的**新建产物**。

## 6. 真机耦合条款（gap6 §7 继承）

哨兵守代码漂移、gate 守失败集合，**守不住 fake/真实 schema 漂移**：每次大迁移验收须配 memory 纪律「真机 run 2-3 次取证」，`c3-baseline.md` 固定记录双栏结论（离线子集 + 真机终态），任何一栏缺失不得宣布迁移成功（pi 假模型验流程/真 provider 另跑的对照，§5 交叉表同款）。

## 7. 跨平台条款

X1：scope/marker 路径 POSIX glob；X2：pytest 调用 argv（`-k` 值永不进 shell）；X4：门禁输出 ASCII 标记 `GATE-OK/GATE-RED/GATE-SKIP-EMPTY`，中文正文 GBK 安全；X5：基线写盘断行 + 读侧 replace；X7：本项不发信号；子进程 pytest 在 Windows 控制台继承 GBK 环境——统一 env `PYTHONIOENCODING=utf-8` + `encoding="utf-8", errors="replace"`（memory PT 坑直修）。哨兵测自身两平台同绿（marker 扫描用 `read_text(encoding="utf-8", errors="replace")` 同读侧纪律）。

## 8. 测试清单（`tests/test_migration_sentinels.py` + `tests/test_migration_gate.py` 新）

1. 哨兵三类各至少 1 真规则全绿 + 合成红样（tmp 目录喂假文件断言方向性——erosion 缺锚点红、resurrection 重定义红、forbidden 出现红）。
2. 变异自检**记录表存在且逐条有日期**（测「每条 rule 有 mutation_record」的元测试，缺记录红——把纪律做成门禁，M11 精神）。
3. gate：`run_pytest` 解析样本文本（FAILED/ERROR/collected 三形态，含 CRLF 样本）；compare 子集三态（⊆/新增/减少）；collected=0 → rc1；无基线 → rc2；基线坏 JSON → 点名。
4. gate 端到端：tmp 迷你 pytest 树（3 测 1 红）录基线→破坏 1 绿→新增失败点名 rc1→恢复→rc0。
5. 新增测下限 **≥14**；772 不回退。

## 9. 红线五查与不做项

`stdlib ✓ / 断言 ✓（gate 禁的是造假绿，不松任何判定）/ 知识写路径 ✓ / 依赖 ⚠=治理取舍已钉（变异工具不用 mutmut，理由写对：门禁可复现性，不是「红线禁止」——E2）/ 离线 ✓（gate 真跑 pytest 属本地非网络，CI 可用）`。
**不做**：自动变异引擎、常驻门禁、跳测试白名单机制（存在即造假绿通道，直接不给）。

## 10. 未验证/开放问题

- 首批哨兵锚点字符串须在实现期逐一 `grep` 对磁盘原文核定（设计期列的是**候选锚**，锚漂移=哨兵假红/假绿，核定是 W3 第一步）。
- `docs/case-migration-mapping.md` 在 gitignore 内：哨兵**不读该文件**（CI 上不存在），只读磁盘代码；映射清单仅人审引用。

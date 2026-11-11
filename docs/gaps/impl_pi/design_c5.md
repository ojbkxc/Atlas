# 设计 C5：Ownership 边界账本 + 漂移分级 + merge 前分类（波次 W3，与 C3 并行）

> 上游：gap6 §3.1-C5、§4-C5、§9〔C5 注：`fork-ownership.json` 实有 `riskModel.basis="structuralDrift"`、`thresholds{high:250,medium:30}`、`dispositions`、`decisionLog`、`autoPushPolicy`；**分类轴 cosmetic/structural，risk 等级由 structural 行数阈值 250/30 推得**——不得写成 risk 是枚举；forbiddenMarkers 属 C3 哨兵侧，不在账本〕。
> 实施者：impl_bounds（任务 #6；W3 内 `cli.py` 仅 doctor 分支一处）。

## 1. 目标与边界

Atlas 长期吸收外部/遗留资产（LiteFlow/手册/legacy），要防两件事：自有改造被侵蚀、以及「看似错误实为刻意」的删除被干净合并带回来。账本是**数据非引擎字面量**（红线 #11——引擎运行时永不解析它；只有 doctor 与分类脚本消费）；分类发生在 **merge/吸收 gate**，不是 git fork CI。

## 2. `docs/ownership/atlas-boundaries.json`（新，入 git——内容全是路径/决策，无业务字面量）

```json
{
  "schema_version": 1,
  "risk_model": {
    "basis": "structural_drift",
    "thresholds": {"structural_medium": 30, "structural_high": 250},
    "note": "行数不是风险本体：表层样式/文案大 diff=cosmetic；跨文件搬移/结构改写=structural"
  },
  "regions": [
    {"id": "engine-core",
     "region": "engine-owned",                    // 三值枚举：engine-owned | absorbed | private-knowledge
     "paths": ["src/atlas/*.py", "src/atlas/generator/*.py"],
     "disposition": "",                            // "" | reduce | accepted（技术债显式标价，C17 同族）
     "decision_log": []},
    {"id": "liteflow-compile-semantics",
     "region": "absorbed",
     "paths": ["src/atlas/dsl.py"],
     "disposition": "reduce",
     "decision_log": [{"date": "2026-11-05", "decision": "retain",
                       "rationale": "原子编译/md5/^指针/{{slot}}/dispatch 已按 Atlas 形态吸收，不回退", "by": "atlas-liteflow-absorb"}]},
    {"id": "legacy-case-bridge", "region": "absorbed", "paths": ["src/atlas/plugins/demo/legacy_case.py"],
     "disposition": "accepted",
     "decision_log": [{"date": "2026-11-04", "decision": "accept-debt",
                       "rationale": "桥接语义依赖旧 runner 播种，消除=重写 30 条用例执行面，暂付代价", "by": "case-porter"}]},
    {"id": "iservice-flows", "region": "private-knowledge",
     "paths": ["flows/iservice/", "knowledge/"],
     "disposition": "", "decision_log": []},
    {"id": "atlas-style-exempt", "region": "engine-owned",
     "paths": ["docs/**/*.md"], "style_paths": true, "disposition": "", "decision_log": []}
  ]
}
```
- 路径重叠裁决：**最长非通配前缀/更具体 glob 优先**；同一路径被两条同特异度规则认领 → 账本判非法（doctor 点名）。
- 字段级校验（唯一判据在分类脚本模块级，doctor 复用同一函数——#92）：`schema_version` int；`region` ∈ 三枚举；`paths` 非空 list of POSIX 相对串（X1：出现 `"\\"` 点名）；`disposition` ∈ 三枚举；`decision_log` 每条必带 `date+decision+rationale+by`（**accepted 项无 rationale = 非法**：说不清怎么消除的债才算 accepted）。
- `style_paths: true` 的区域任何 churn 判 cosmetic（风险模型的形式化承接）。

## 3. `scripts/boundary_classify.py`（新）——入站变更集分类 gate

```python
def load_ledger(path: Path) -> tuple[dict, list[str]]          # (账本, 问题清单)；坏形态逐条点名不裸崩
def resolve_region(ledger, rel_path: str) -> str | None         # 最长特异度优先；None=未登记（提示补账）
def parse_unified_diff(text: str) -> dict[str, dict]            # 自写：file → {added, removed, moved_in/out候选行集}
def classify_change(ledger, files: dict) -> list[dict]
    # 每文件：style_paths→cosmetic；跨文件搬移启发式（A 删除行集与 B 新增行集归一化 Jaccard≥0.8）→structural；
    # churn=added+removed <30→cosmetic；≥30→structural(medium)；≥250→structural(high)
def main(argv) -> int
    # 用法：git diff 产物走 stdin 或 --diff <file>（**argv 传参，绝不 shell**，X2；分支名/路径转义 memory 坑）
    # 输出 JSON：每条 {file, region, class: cosmetic|structural, risk: low|medium|high, disposition_hint, decision_log_tail}
    # verdict：命中 absorbed/engine-owned 的 structural → "进人审塔"；--to-tower 时经
    #   ExperienceJournal.append_draft 登记 kind="boundary_review" 草稿（复用既有塔与 _atomic_write，
    #   不新开塔——红线 #9/#10；command/verdict 三元组位留空 → unverified，晋级走 C2 闸，天然自洽）
    # cosmetic 命中 → "自动放行"；rc：0=无 structural，1=有 structural，2=用法/账本坏
```
- **merge 前分类是义务**：事后无法用 diff 还原入站集，gate 必须在吸收动作前跑（README 吸收流程小节 + AGENTS §2 验证纪律补一行——W3 文档单主是 impl_bounds，两处各一段，合并归 §5）。
- review.py `approve()` 对 kind="boundary_review" 点名拒绝（并入 `trace_step`/`code_review` 那条「不自动写知识」文案家族，一行；W3 无人并占 review.py）。

## 4. doctor 一致性项（cli.py doctor 分支，W3 单主）

```python
checks["boundary_ledger"] = {"present": bool, "regions": n, "problems": [...],
                             "accepted_debts": [{"id","decision_log_tail"}]}
checks["boundary_assets_consistent"] = (无 problems) 与 (paths 至少命中一条磁盘路径)
# rc 并入既有 docker_ok 同侧：账本「存在但坏」→ rc1（present-but-broken=漂移，与 docker 资产同纪律 #134）；
# 文件缺失 → present:false，**不改 rc**（新波次前的历史分支/精简克隆不该被账本卡死）
```

## 5. 跨平台条款

X1：paths 全 POSIX 相对串 + `PurePosixPath` 语义 glob 匹配（`fnmatch`，不触文件系统差异）；X2：脚本零 subprocess（diff 从 stdin/文件来，git 命令由人执行——**不给 classify 造 git 调用面**，避免 Windows shell 差异）；X4：verdict 文案 `REVIEW-TOWER`/`AUTO` ASCII 标记；X5：读 diff 文件 `errors="replace"`；账本读写 UTF-8 显式。

## 6. 测试清单（`tests/test_boundaries.py` 新）

1. schema 校验：三 region 枚举/重叠特异度裁决/同特异度冲突点名/accepted 无 rationale 非法/反斜杠路径点名。
2. classify：纯样式大 diff→cosmetic；250±/30± 阈值三 case；跨文件搬移（构造 Jaccard 高对）→structural；未登记路径→hint。
3. --to-tower：草稿进既有队列、approve 被点名拒绝、C2 unverified 闸对其生效（联动测）。
4. doctor：present/broken 两态与 rc 行为（缺文件不改 rc 要测——防把账本做成新全局门）。
5. decision_log 尾部摘要进 verdict 输出（重议防御的可见性）。
6. 新增测下限 **≥12**；772 不回退。

## 7. 红线五查与不吸收项

`stdlib ✓ / 断言 ✓ / 知识写路径 ✓（boundary_review 走既有 pending_review 唯一写路径）/ 依赖 ✓ / 离线 ✓`。
**不做**：git 钩子/CI 集成（ATLAS 的 Dockerfile/CI 本就未实测，红线 #15 不借机扩面）；autoPushPolicy 类自动推送语义（Atlas 无该形态）；把账本读进引擎运行时（红线 #11 反例就在自家 pitfall 史）。

## 8. 未验证/开放问题

- 跨文件搬移启发式 Jaccard≥0.8 在真实重构 diff 上的误报率未测——首批以 absorbed 区域小面运行，命中进人审不误杀（结构性误判的代价=多一次人审，可接受；漏判代价由 C3 哨兵兜底，二者互为交叉验证）。
- `decision_log.by` 取团队/人名自由文本，身份真实性不在本项校验范围。

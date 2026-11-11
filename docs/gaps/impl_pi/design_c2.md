# 设计 C2：证据式验收三元组（波次 W1，次序第二位）

> 上游：gap6 §3.1-C2、§4-C2、§7（override 判定**已拍板**：阻断晋级、人可显式 override 带警示留痕）。
> 价值口径：把红线 #7「禁止为变绿放宽断言」从审核时的「注意」升级为「没格式就不可批准（除非人点名 override）」。
> 实施者：impl_tower（任务 #4，与 #3 并行；文件集不相交）。

## 1. 三元组落哪层（拍板答案）

| 层 | 载体 | 职责 |
|---|---|---|
| **数据层（experience）** | 草稿行既有 `evidence` dict 内新增规范键 `verification` = 三元组 | **唯一存储位**；`experience.py` **零签名改动**（evidence 是现成通道，#118 刷新语义天然继承） |
| **判定层（review）** | `review.py` 模块级 `verification_problems()` + approve 入口闸 | **唯一强制点**：批准（含 --apply 批量）在此校验；列表/证据卡显示 UNVERIFIED |
| **提案层（healing）** | `HealingProposal.evidence` 可选字段，__post_init__ 形态校验 | 提案携带证据进塔（`--to-tower` 时并入草稿 evidence），晋级仍走 review 闸 |

单一判据：三处都只调 `review.verification_problems(ev)` 一个函数（#92；healing→review 无反向依赖，import 不成环）。

## 2. Schema 字段级

`evidence.verification`（缺失或非映射 → unverified）：

```json
{
  "command":    "python -m pytest tests/... -k tc3"  或 "atlas run flows/x.yaml --env sit"（复现命令，非空 str，展示用；执行由人，Atlas 不代跑）,
  "exit_code":  0,                                    // int，命令真实退出码
  "assertions": 12,                                   // int>=0，断言数（pytest 收集/执行口径或 Allure steps 计数，来源见 command 语义）
  "verdict":    "pass"                                // "pass"|"fail" 显式结论行——坑点草稿记 "fail" 合法（证明失败真实复现过），闸只查完备与形态，不要求「必须绿」（否则违反红线 7 的本意）
}
```

批准留痕新增键（override 时）：`review_decisions.jsonl` 行加 `"unverified_override": true, "override_reason": "<--reason 原文>"`；草稿行加 `"approved_despite_unverified": true`。

## 3. 接口签名

### 3.1 `src/atlas/review.py`（改）

```python
VERIFICATION_KEYS = ("command", "exit_code", "assertions", "verdict")

def verification_problems(evidence: object) -> list[str]:
    """单一判据：返回缺失/形态问题清单；空列表 = verified。
    纯函数绝不抛（evidence 为 None/list/str 给「非映射」问题项而不是 AttributeError，#77/#130 家族）。
    形态规则：command 非空 str；exit_code int 非 bool；assertions int>=0 非 bool；
    verdict ∈ {"pass","fail"} 严格字面量。"""

def _is_verified(row: dict) -> bool:
    return not verification_problems((row.get("evidence") or {}).get("verification"))
```

- `approve(...)` 签名尾部追加 `allow_unverified: bool = False, override_reason: str = ""`：
  `kind ∈ {pitfall, selector_candidate, bu_overlay}` 且 `not _is_verified(row)` 且非 allow_unverified →
  `raise ValueError(f"缺少证据三元组（{problems}），该项 unverified 不可晋级；确需人工批准：atlas review --approve <id> --allow-unverified --reason \"<依据>\"")`。
  allow_unverified=True 时必须带 override_reason（空 → ValueError 点名「override 必须留原因」），批准成功路径写上述两键并 stderr 一行 `UNVERIFIED-override: <id> <原因前 80 字符>`（X4 ASCII 前缀）。
- `trace_step`/`code_review` 维持原「不可批准」拒绝，闸不加第二扇门。
- `evidence_card(row)`：行首增状态位 `[VERIFIED]` / `[UNVERIFIED]`，后者附缺失键清单——审核塔 30 秒判断的前提是看得见缺什么。
- `apply()`：批量逐条走 `approve`，override 不经批量（决策模板无 `--allow-unverified` 通道，**有意**：override 必须是单条显式决定，批量放行 unverified 等于没闸）。

### 3.2 `src/atlas/healing.py`（改）

```python
@dataclass
class HealingProposal:
    task: str; trigger: str; remedy: str; pitfall_id: str
    status: str = "pending_review"
    evidence: dict | None = None      # {"verification": {...}} 同 schema

    def __post_init__(self):
        if self.evidence is not None:
            problems = verification_problems(self.evidence.get("verification"))
            if problems:
                raise ValueError(f"HealingProposal.evidence.verification 形态不完备: {problems}")
```

`propose()` 行为不变；准入逻辑（#108/#7 不放宽断言）零改动——**三元组门控「晋级」，不新增「执行」**，自愈执行仍只认 approved 知识 + 只读 Task。

### 3.3 协调补丁（design-plan §3，文本在此，执行按归属）

- **补丁 1（engine.py，impl_report 随 C4② 同文件落）**：engine 失败路径 `journal.draft(...)` 增传
  `evidence={"verification": {"command": f"atlas run {case} --env {env}", "exit_code": 1, "assertions": <recorder 已执行 op=="assert" 步骤数>, "verdict": "fail"}}`
  ——语义「本草稿的证据=刚发生的失败复现」，诚实可得才填；engine 拿不到的场景不填（不猜）。
- **补丁 2（cli.py `_review`，impl_report 收到 impl_tower「C2 review.py 就绪」消息后落）**：parser 增 `--allow-unverified`（store_true）与既有 `--reason` 复用；`approve` 调用点透传 `allow_unverified=args.allow_unverified, override_reason=args.reason`。未接线期间闸照常生效（缺省不允许），只是 override 暂不可用——不阻塞 #3。

## 4. 向后兼容口径（gap6 已定，写死防评审歧义）

- 已 approved/rejected 存量条目：**不复查**（闸只作用于「批准」动作）。
- 存量 pending 草稿（无 verification）：晋级时被拦 → 人跑复现补三元组或 override——这正是「新晋级才要求」的执行形态；`rows()` 补 id 的 legacy 归一逻辑不动。
- 不可批准 ≠ 删除：unverified 行照常列出、可 reject 留原因。

## 5. 跨平台条款

X1/X2/X5：本项不触 subprocess/路径/换行；X4：警示行 ASCII 前缀（§3.1）；X9：override 落盘走既有 `_rewrite`（tmp+os.replace）与 `_decide` 尽力而为（#130 面A 语义不变）。

## 6. 测试清单（`tests/test_c2_verification.py` 新文件 + 既有文件零触碰）

1. `verification_problems`：完备/缺 command/verdict 拼错/exit_code bool/assertions 负数/evidence None/list——全给问题清单不给异常。
2. 闸拦新草稿（pitfall/selector/overlay 三 kind）→ ValueError 文案含 `--allow-unverified` 指引；`_is_verified` 与判据一致。
3. override：带 reason → 批准成功、行加 `approved_despite_unverified`、decisions 行含 `unverified_override`；无 reason → 拒。
4. 存量 approved 零涟漪：无 verification 的已批行不受影响；legacy 补 id 行为不变。
5. 批量 `--apply`：含 unverified 行进 failed 点名（不经 override）。
6. healing：evidence 坏形态构造期 ValueError 点名缺失键；合法形态零涟漪；KI 自愈拒绝测（既有）不翻。
7. allure-analyze 造的草稿带三元组（verdict 由 Allure status 派生：passed→pass，其余 fail）。
8. 证据卡显示 `[UNVERIFIED]` + 缺失清单。
9. 新增测下限 **≥14**；772 不回退。

## 7. 红线五查

`stdlib ✓ / 断言：只加格式约束不放宽任何既有判定 ✓ / 知识写路径：零新增（仍只 --approve）✓ / 依赖 ✓ / 离线 ✓`。红线 #13/#10 精神：override 是人的显式决定且必留痕，机器永远不自动放行；批量通道有意不配 override。

## 8. 未验证/开放问题

- engine 自动三元组的 `assertions` 计数口径（recorder steps 里 `op=="assert"`）在补丁 1 落地时以真实 flow 冒烟核对一次；若既有 step 命名不含 "assert"，计数为 0 **也诚实**（断言数=0 恰是 GREEN 类反模式的可见信号，review_code ASSERT-* 互补）。
- Allure 来源草稿的 `command` 复现性（原结果集能否重跑）未证——文案标注「来源: allure 分析」，人审自行判断证据强度。

---

## 勘误（评审 pi_critique · 2026-11-06 · design-review.md §4.2，约束性，非设计主体）

1. **§6「既有文件零触碰」不成立**：磁盘实测 `test_review_queue.py`（8 处 approve，`_seed` 造的 pitfall/selector 草稿无 verification）、`test_evidence_and_knowledge.py`（2 处）、`test_overlay_pipeline.py`（1 处）在闸落地后必红 ≥9 条，破 772 门禁。裁决：增**补丁 3**——上述 3 文件的 seed/fixture 草稿行补 `evidence.verification` 真三元组（只喂闸，**不放宽任何断言**），归属 impl_tower（W1），列明于本勘误即满足 design-plan §2「确需改须在协调补丁列明」的例外通道。落实本条后本设计视同 PASS-with-conditions（另见 C2-B：`op=="assert"` 的真实 op 字面值实现期核盘钉死）。
2. C2 其余条款（override 单条显式、批量无 override、verdict=fail 合法、存量 approved 不复查）经红线五查与死锁审查通过。

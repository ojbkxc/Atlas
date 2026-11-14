"""审核吞吐入口：草稿→证据卡片→批量人审→晋级/驳回→决策留痕。

精炼厂的瓶颈从来不是矿，是塔。这个模块只做一件事：
把"判断一条提案"的成本压到分钟级——列表带证据、id 前缀定位、
批准即写知识、驳回留原因、全部落审计。critical 档无自动路径。
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

from . import locators
from .atomic_io import append_text_locked, read_text_retry

QUEUE_FILE = "experience_drafts.jsonl"
DECISIONS_FILE = "review_decisions.jsonl"
PENDING, APPROVED, REJECTED = "pending_review", "approved", "rejected"

# C2 证据式验收三元组（design_c2 §2）：草稿 evidence.verification 的规范键。
# 复现命令 / 真实退出码 / 断言数 / 显式结论行。verdict 允许 "fail"——坑点草稿的
# 三元组证明的是「失败真实复现过」，闸只查完备与形态、不要求必须绿（否则违反
# 红线 #7 的本意：逼人造绿）。
VERIFICATION_KEYS = ("command", "exit_code", "assertions", "verdict")

# 闸的适用面（design_c2 §3.1）：「可批准」kind 才要求三元组；
# trace_step/code_review/boundary_review 本就不可批准，闸不加第二扇门。
# flow_candidate（落库线）：跑绿固化的动作序列草稿，engine/dsl 侧自动带
# verdict="pass" 的成功三元组——与失败草稿同一把闸，机器产的证据正常晋级、
# 手工造的没证据照样拦（红线 #13：机器永不自动放行）。
GATED_KINDS = ("pitfall", "selector_candidate", "bu_overlay", "flow_candidate")


def verification_problems(evidence: object) -> list[str]:
    """单一判据：返回缺失/形态问题清单；空列表 = verified。

    纯函数绝不抛——evidence 为 None/list/str 给「非映射」问题项而不是 AttributeError
    （#77/#130 家族：判据函数不得先于被判定对象死）。形态规则：command 非空 str；
    exit_code int 非 bool；assertions int>=0 非 bool；verdict ∈ {"pass","fail"} 严格字面量。
    """
    if not isinstance(evidence, dict):
        label = "缺失" if evidence is None else f"非映射（实为 {type(evidence).__name__}）"
        return [f"evidence.verification {label}，需齐备 {'/'.join(VERIFICATION_KEYS)} 四键"]
    problems: list[str] = []
    command = evidence.get("command")
    if not (isinstance(command, str) and command.strip()):
        problems.append("command 必须是非空字符串（复现命令，展示用；执行由人，Atlas 不代跑）")
    exit_code = evidence.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        problems.append(f"exit_code 必须是 int（非 bool），实为 {type(exit_code).__name__}")
    assertions = evidence.get("assertions")
    if isinstance(assertions, bool) or not isinstance(assertions, int):
        problems.append(f"assertions 必须是 int（非 bool），实为 {type(assertions).__name__}")
    elif assertions < 0:
        problems.append(f"assertions 不能为负，实为 {assertions}")
    verdict = evidence.get("verdict")
    if verdict not in ("pass", "fail"):
        problems.append(f'verdict 必须是显式结论行 "pass"|"fail"，实为 {verdict!r}')
    return problems


def _verification_of(row: dict):
    """取草稿行的 evidence.verification——evidence 坏形态（None/str/list，#77/#130 家族：
    键在值 null 时 `or {}` 只兜假值、非映射真值会裸 AttributeError）一律当「缺失」交给
    判据函数，绝不先于被判定对象死。单一提取点（#92），_is_verified/闸/证据卡共用。"""
    ev = row.get("evidence")
    return ev.get("verification") if isinstance(ev, dict) else None


def _is_verified(row: dict) -> bool:
    return not verification_problems(_verification_of(row))


def _override_marks(despite_unverified: bool, override_reason: str) -> dict:
    """override 批准时并入决策留痕行的两键（design_c2 §2）；正常批准返回空。"""
    if not despite_unverified:
        return {}
    return {"unverified_override": True, "override_reason": override_reason}


# #130 面B：pending 判据的单一真相（#92 改一漏一）——旧实现三套口径并存：pending()/
# resolve() 用 `status == PENDING`（无 status 的 legacy 行不可见），stats()/reject_kind()
# 用 `get(status, PENDING)`（同一行算 pending 且可批量驳回）。CLI 列表显示「审核队列清空」
# 而 --stats 报 pending>0、--reject-kind 能驳回列表里看不见的行——三入口三套真相。
# 收敛方向与 rows() 的 legacy 注释同口径（「历史积压照样可审」）：**未被显式批准/驳回的
# 都算待审**——missing 与 pending_review 都是「还没人决定」，正是可审素材。
def _is_pending(row: dict) -> bool:
    return row.get("status") not in (APPROVED, REJECTED)


class ReviewQueue:
    def __init__(self, reports_dir: Path):
        self.dir = Path(reports_dir)
        self.path = self.dir / QUEUE_FILE

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        # #112：与 ExperienceJournal._read_queue 同族——尾行被中断腰斩出非法 UTF-8 时
        # 裸 UnicodeDecodeError 让整塔读崩，好草稿全部不可见（#109 trace 本体同口径：
        # 坏字节 replace 读入，残行 JSON 解析必败自然跳过）。两处读侧必须同防线。
        # cc-1 读侧配套：队列由 append_text_locked 加锁写，Windows 并发读撞锁报
        # PermissionError（瞬态）——read_text_retry 重试即过。
        try:
            raw = read_text_retry(self.path, encoding="utf-8")
        except UnicodeDecodeError:
            raw = read_text_retry(self.path, encoding="utf-8", errors="replace")
        rows = []
        for line in raw.splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # #130 面C：合法 JSON 但非映射（`[1,2]`/`"x"`/`null` 整行）——旧实现
                # row.get 裸 AttributeError 顶掉整塔（读崩与坏字节同红线 #112 注释
                # 「两处读侧必须同防线」当时只修了字节面）。与残行同纪律跳过：非对象行
                # 本来就无法表达为草稿，随 _rewrite 原子规范化清掉，不静默伪装合法行。
                if not isinstance(row, dict):
                    continue
                if not row.get("id"):
                    # 旧版草稿没有 id：按同一口径补算，历史积压照样可审
                    import hashlib
                    identity = "|".join(str(row.get(k, "")) for k in
                                        ("task", "exception_type", "failure_class", "pitfall_id"))
                    row["id"] = hashlib.sha1(identity.encode()).hexdigest()[:12]
                rows.append(row)
        return rows

    def pending(self, kind: str = "") -> list[dict]:
        # #130 面B：判据收敛 _is_pending 单一真相——旧 `== PENDING` 把无 status 的 legacy
        # 行藏出列表，而 stats()/reject_kind() 把它们算 pending（可批量驳回列表里看不见
        # 的行）——同一队列三套口径，CLI「审核队列清空」与 --stats pending>0 同屏矛盾。
        rows = [r for r in self.rows() if _is_pending(r)]
        # 塔堆高时先清一类：selector_candidate 批量过，坑点逐条过
        if kind:
            rows = [r for r in rows if r.get("kind", "pitfall") == kind]
        return rows

    def resolve(self, ref) -> dict:
        """按 id/前缀（≥唯一）或已取出的行定位一条 pending 草稿。"""
        if isinstance(ref, dict):
            if not _is_pending(ref):
                raise KeyError("该行不在 pending 状态")
            return ref
        ref = str(ref).strip()
        if not ref:
            # 空引用不是通配：str.startswith("") 恒真会命中全部 pending，塔里恰好只剩一条时
            # 会把「没填 id 的模板行」默认批准掉——违反「空行失败点名，不是自动批准」红线。
            # 显式拒绝，让 --apply 把它归入 failed 并点名、非零退出。
            raise KeyError("引用（id/前缀）不能为空：--apply 的每行必须带明确 id")
        hits = [r for r in self.pending() if str(r.get("id", "")).startswith(ref)]
        if not hits:
            raise KeyError(f"队列中没有 pending 的 {ref!r}")
        if len(hits) > 1:
            raise ValueError(f"{ref!r} 前缀命中 {len(hits)} 条，请加长：{[h['id'] for h in hits]}")
        return hits[0]

    def _rewrite(self, rows: list[dict]) -> None:
        from .experience import ExperienceJournal
        ExperienceJournal._atomic_write(self.path, rows)

    def _decide(self, decision: dict) -> None:
        # #130 面A：留痕尽力而为——调用顺序是「知识/组件写回 → 队列状态改写 → 决策留痕」，
        # 前两个业务事实已达成且幂等闸门（resolve 只认 pending）让批准**不可重放**：留痕
        # 路径被占/权限坏时旧实现裸 OSError 把 CLI 翻成 traceback——审核者看到「崩了」以为
        # 没批准成功，重试被 resolve 拒绝「没有 pending」，批准成功的信号永久丢失（知识实际
        # 已入库，无人知晓）。与 #110 agent._record 同纪律：只兜 OSError（盘/路径类故障），
        # 非 OSError 是真 bug 不静默吞；内存与业务状态不因此回滚。
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            # wave4 cc-1/rb-7：决策留痕裸 open("a") 并发丢行（静默）——统一走原子追加器。
            append_text_locked(self.dir / DECISIONS_FILE,
                               json.dumps(decision, ensure_ascii=False))
        except OSError:
            pass

    def reject(self, ref: str, reason: str = "") -> dict:
        row = self.resolve(ref)
        rows = self.rows()
        for r in rows:
            # 终验#8 续修：只翻转 pending 行。旧实现按 id 翻转**全部**同 id 行——
            # 磁盘实锤：11-04 已批准的两条 selector_candidate 历史行，被 11-06 清塔
            # 驳回同名 pending 重复行时静默改写成 rejected（历史改写缺陷，与
            # append_draft「决定态不回翻」同一纪律；reject_kind 批量面早就
            # _is_pending 过滤，单条 reject 是同族漏网）。已决定行是终态记录，
            # 不可被后续同名操作回翻——驳回只处置「还没人决定」的行。
            if r.get("id") == row["id"] and _is_pending(r):
                r["status"] = REJECTED
                r["reject_reason"] = reason
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._rewrite(rows)
        self._decide({"action": "reject", "id": row["id"], "task": row.get("task"),
                      "reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return row

    def reject_kind(self, kind: str, reason: str) -> int:
        """一类 pending 全部驳回。驳回不动知识只动队列，但原因必填——留痕要有人判断垫底。"""
        if not reason:
            raise ValueError("批量驳回必须给原因：--reason \"为什么这一类都不入\"")
        rows = self.rows()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        count = 0
        for r in rows:
            if _is_pending(r) and r.get("kind", "pitfall") == kind:
                r["status"] = REJECTED
                r["reject_reason"] = reason
                r["decided_at"] = now
                count += 1
                self._decide({"action": "reject", "id": r["id"], "task": r.get("task"),
                              "reason": reason, "batch": kind, "at": now})
        self._rewrite(rows)
        return count

    def template_rows(self, kind: str = "selector_candidate") -> list[dict]:
        """导出人可编辑的决策模板：选择器候选带 component/element + 定位位，坑点/坐标带 remedy。

        模板行必须自带 task 上下文——真机 --apply 暴露过缺口：只有 id 的模板人无法辨认。
        选择器候选另带 `role`/`name`（**用草稿原值预填**，gap13）：探索产出就是 ARIA 候选，
        预填后人只需决定「写进哪个组件的哪个元素」，不必把无障碍名手抄成 CSS；
        `selector` 位留空——人想用 CSS 就在这格写，写了就优先于 role/name。
        批量批准不等于自动批准：component/element 空的行仍会失败并点名。
        """
        out = []
        for r in self.pending(kind):
            row_kind = r.get("kind", "pitfall")
            if row_kind == "selector_candidate":
                out.append({"id": r["id"], "task": r.get("task", ""),
                            "kind": "selector_candidate",
                            "component": "", "element": "", "selector": "",
                            "role": str(r.get("role") or ""), "name": str(r.get("name") or "")})
            else:
                out.append({"id": r["id"], "task": r.get("task", ""),
                            "kind": row_kind, "remedy": ""})
        return out

    @staticmethod
    def _row_selector(row: dict):
        """决策行 → 定位描述：`selector` 优先；缺省用 `role`/`name` 组装（同一 parse 判据）。

        为什么 selector 优先：人显式写了 CSS 就是显式选择，不被预填的 role/name 盖掉。
        """
        selector = row.get("selector") or row.get("remedy", "")
        if selector:
            return selector
        role = str(row.get("role") or "").strip()
        if not role:
            return ""
        name = str(row.get("name") or "").strip()
        return {"role": role, **({"name": name} if name else {})}

    def apply(self, knowledge, decisions: list[dict], *,
              require_evidence: bool = False) -> dict:
        """人填好模板后的批量执行：仍逐条走 approve 分发、逐条留痕；单行失败不拦其余。

        #109：人编辑决策文件时把整行改成 JSON 数组/null 是「合法 JSON 但非映射」——
        旧实现 `row.get` 裸 AttributeError 顶掉整批：前面已批准的写回不回滚，后面合法行全部不跑，
        CLI 拿不到 failed 清单（rc2 口径失效）。与 #94/#95/#97 同红线：非对象形态点名拒绝，不裸崩。

        C2 松绑：`require_evidence` 透传给每条 `approve`——默认 False（闸不生效，与单条
        默认一致）。批量通道仍**不**透传 allow_unverified（决策模板无此旋钮，有意）：
        要旧硬保证就由 CLI 传 `--require-evidence`；批量放行 unverified 从不等于没闸，
        只是「默认不强制」这个更宽的语义现在覆盖批量入口。
        """
        result = {"applied": 0, "failed": []}
        for row in decisions:
            if not isinstance(row, dict):
                result["failed"].append({"id": "?", "error": f"决策行必须是映射，实为 {type(row).__name__}"})
                continue
            ref = str(row.get("id", ""))
            try:
                self.approve(knowledge, ref,
                             remedy=self._row_selector(row),
                             component=row.get("component", ""),
                             element=row.get("element", ""),
                             require_evidence=require_evidence)
                result["applied"] += 1
            except (KeyError, ValueError) as exc:
                result["failed"].append({"id": ref, "error": str(exc)})
        return result

    def approve(self, knowledge, ref: str, *, remedy, component: str = "",
                element: str = "", trigger: str = "", symptom: str = "",
                root_cause: str = "", scope: list[str] | None = None,
                source: str = "review", allow_unverified: bool = False,
                override_reason: str = "", require_evidence: bool = False) -> dict:
        """批准入口按 kind 分发：选择器候选进组件库，坐标差异进 overlay 库，
        坑点进知识，trace 步骤拒之塔外。

        C2 证据闸（design_c2 §3.1；重构波松绑）：**改为显式 opt-in**——
        `require_evidence=True` 时，四个可批准 kind 必须携带完备的
        evidence.verification 四元组才可晋级（缺者 unverified：不删除、照常列出、
        可 reject 留原因，只拦「批准」这一个动作，红线 #7 硬化：没格式就不晋级）；
        人可显式 override（allow_unverified + 必须带原因）双留痕并 stderr 警示。

        **默认（require_evidence=False）不再强制**：缺证据的草稿照常批准，且**不打**
        override 标（没开闸就谈不上「豁免」）。这是本轮**有意的行为变更**（松绑），
        风险已写进 AGENTS/CLAUDE——闸本体（`verification_problems` + override 机制）
        保留未删，只是默认不生效；CI/人想要旧硬保证就传 `--require-evidence`。
        """
        row = self.resolve(ref)
        kind = row.get("kind", "pitfall")
        if kind == "trace_step":
            raise ValueError("trace_step 是步骤素材不是知识：人工写成 flow YAML 后 "
                             "atlas compile 校验，或直接 reject 留原因")
        if kind == "code_review":
            raise ValueError("code_review 是代码质量评审意见：不自动写知识。"
                             "请人工按规则处置（修改代码或在 CI 中处理）")
        if kind == "boundary_review":
            raise ValueError("boundary_review 是边界漂移分类决策：不自动写知识。"
                             "请人工改判 docs/ownership/atlas-boundaries.json 条目或 decision_log 后重跑分类")
        problems: list[str] = []
        if kind in GATED_KINDS and require_evidence:
            problems = verification_problems(_verification_of(row))
            if problems:
                if not allow_unverified:
                    raise ValueError(
                        f"缺少证据三元组（{'; '.join(problems)}），该项 unverified 不可晋级；"
                        '确需人工批准：atlas review --approve <id> --require-evidence '
                        '--allow-unverified --reason "<依据>"')
                if not override_reason.strip():
                    raise ValueError("override 必须留原因：--reason \"<凭什么豁免证据三元组>\""
                                     "——无痕迹的 override 等于没闸（红线 #13）")
        despite = bool(problems)  # 仅 override 显式放行时为非空
        reason = override_reason if despite else ""
        if kind == "bu_overlay":
            result = self.approve_to_overlay(knowledge, row, remedy=remedy, source=source,
                                             despite_unverified=despite, override_reason=reason)
        elif kind == "flow_candidate":
            # 落库线：跑绿固化的动作序列 → 公共任务库。--remedy 可选：显式指定库名，
            # 缺省回落草稿的 case 名（跑绿的 flow id 本就是该序列在 runs.jsonl 里的真名）。
            result = self.approve_to_task_library(knowledge, row, name=remedy, source=source,
                                                  despite_unverified=despite,
                                                  override_reason=reason)
        elif kind == "selector_candidate":
            if not (component and element):
                raise ValueError("批准 selector_candidate 必须指定组件与元素："
                                 "atlas review --approve <id> --component <c> "
                                 "--element <e> （--remedy \"<CSS>\" 或 --role/--name）")
            result = self.approve_to_selector(knowledge, row, component=component,
                                              element=element, selector=remedy, source=source,
                                              despite_unverified=despite, override_reason=reason)
        else:
            result = self.approve_to_pitfall(knowledge, row, remedy=remedy, trigger=trigger,
                                             symptom=symptom, root_cause=root_cause,
                                             scope=scope, source=source,
                                             despite_unverified=despite, override_reason=reason)
        if despite:
            # X4：警示行 ASCII 前缀（GBK 控制台不崩）；原因截前 80 字符进终端，
            # 决策留痕行里存的是 --reason 原文。
            print(f"UNVERIFIED-override: {row.get('id')} {override_reason[:80]}",
                  file=sys.stderr)
        return result

    def approve_to_overlay(self, knowledge, ref, *, remedy: str,
                           source: str = "review", despite_unverified: bool = False,
                           override_reason: str = "") -> dict:
        """批准坐标差异：remedy 填目标 BU 的真实值，写进私有 overlay 库。"""
        row = self.resolve(ref)
        if row.get("kind") != "bu_overlay":
            raise ValueError(f"{row.get('id')} 不是 bu_overlay（kind={row.get('kind', 'pitfall')}）")
        # #130 面D：evidence 键存在值为 null 时 `.get(key, {})` 默认不生效（#77 红线，
        # #127 在 knowledge 修过的同形态）——批准当场 AttributeError、塔清不动（#73 场景）。
        # 与 evidence_card 的 `or {}` 同口径回落。
        ev = row.get("evidence") or {}
        other_bu = ev.get("other_bu", "")
        key = str(row.get("task", "")).split("overlay:", 1)[-1]
        if not (other_bu and key and remedy):
            raise ValueError("批准坐标需要目标 BU、键与值：--remedy \"<该 BU 的真实坐标值>\"")
        record = knowledge.save_overlay(other_bu, key, remedy, approved=True, source=source)
        rows = self.rows()
        for r in rows:
            # ③ 镜像纪律（终验#8 续修，与 reject pending-only、append_draft「决定态
            # 不回翻」同族）：批准只翻 pending 行——同 id 的已驳回历史行不得被
            # 后续同名批准静默回翻成 approved（驳回决定被改写，#130 重放防线的翻转面）。
            if r.get("id") == row["id"] and _is_pending(r):
                r["status"] = APPROVED
                r["overlay_ref"] = f"{other_bu}.{key}"
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                if despite_unverified:
                    r["approved_despite_unverified"] = True
        self._rewrite(rows)
        self._decide({"action": "approve_overlay", "id": row["id"], "task": row.get("task"),
                      "bu": other_bu, "key": key, "value": remedy[:120],
                      **_override_marks(despite_unverified, override_reason),
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return record

    def approve_to_task_library(self, knowledge, ref, *, name: str = "",
                                source: str = "review", despite_unverified: bool = False,
                                override_reason: str = "") -> dict:
        """批准流程候选入库：写公共任务库（task_library.json），人审是唯一入口。

        落库线的「公共任务库」半边：跑绿固化的动作序列经人审批准后成为跨流程可复用
        资产。库按 name 唯一——同名再批准是更新不是堆叠（save_task_entry 的
        overwritten 显式回传）；flow 改了重跑会产生新 id 的新草稿，人审 diff 后批准
        即更新同库同名条目。名字缺省回落草稿 task（case 名）：跑绿的 flow id 本就是
        该序列的真名，--remedy 才是显式改名。
        """
        row = self.resolve(ref)
        if row.get("kind") != "flow_candidate":
            raise ValueError(f"{row.get('id')} 不是 flow_candidate"
                             f"（kind={row.get('kind', 'pitfall')}）")
        entry_name = str(name or row.get("task", "")).strip()
        if not entry_name:
            raise ValueError("任务库条目名为空：--remedy \"<库名>\" 显式指定，"
                             "或补齐草稿的 task 字段")
        ev = row.get("evidence") or {}
        record = knowledge.save_task_entry(
            {"name": entry_name, "steps": ev.get("steps", []),
             "assert": ev.get("assert"),
             "source_run": ev.get("run_id", ""),
             "description": f"来源：跑绿固化（草稿 {row.get('id')}）"},
            approved=True, source=source)
        rows = self.rows()
        for r in rows:
            # ③ 镜像纪律（与 reject/approve_to_* 同族）：批准只翻 pending 行，
            # 已驳回历史行不回翻（决定态是终态记录）。
            if r.get("id") == row["id"] and _is_pending(r):
                r["status"] = APPROVED
                r["task_ref"] = entry_name
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                if despite_unverified:
                    r["approved_despite_unverified"] = True
        self._rewrite(rows)
        self._decide({"action": "approve_flow", "id": row["id"], "task": row.get("task"),
                      "library_name": entry_name, "steps": record["steps"],
                      **_override_marks(despite_unverified, override_reason),
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return record

    def approve_to_selector(self, knowledge, ref, *, component: str, element: str,
                            selector, source: str = "review",
                            despite_unverified: bool = False,
                            override_reason: str = "") -> dict:
        """批准并写回组件注册表：remedy 位放选择器本体，人审是唯一入库路径。

        `selector` 两形态（gap13）：CSS 字符串，或结构化描述 `{"role":..,"name":..}` /
        `{"label":..}` / `{"testid":..}`——探索产出的 ARIA 候选不再需要人手抄成 CSS。
        形态判据在 `locators.parse`（写盘前），这里只做「空值点名 + 留痕用 canonical 文本」。
        """
        row = self.resolve(ref)
        if row.get("kind") != "selector_candidate":
            raise ValueError(f"{row.get('id')} 不是 selector_candidate"
                             f"（kind={row.get('kind', 'pitfall')}）")
        if not selector:
            raise ValueError('批准选择器必须提供选择器本身：--remedy "<CSS 或定位描述>"；'
                             'ARIA 候选可直接 --remedy \'{"role":"button","name":"保存"}\' '
                             '或 --role/--name')
        record = knowledge.save_component_selector(component, element, selector,
            approved=True, source=source,
            note=f"人审批准入库（来源探索候选 {row.get('id')}）")
        rows = self.rows()
        for r in rows:
            # ③ 镜像纪律（终验#8 续修）：批准只翻 pending 行，已驳回历史行不回翻
            # （与 reject/_is_pending、append_draft 决定态幂等同一族防线）。
            if r.get("id") == row["id"] and _is_pending(r):
                r["status"] = APPROVED
                r["selector_ref"] = f"{component}.{element}"
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                if despite_unverified:
                    r["approved_despite_unverified"] = True
        self._rewrite(rows)
        # 留痕文本走 `locators.canonical`：旧写法 `selector[:120]` 收到结构化描述（dict）
        # 直接 TypeError——批准已落库却崩在留痕，等于写回事实无痕（#110/#85 同族）。
        self._decide({"action": "approve_selector", "id": row["id"], "task": row.get("task"),
                      "component": component, "element": element,
                      "selector": locators.canonical(selector)[:120],
                      "overwritten": record["overwritten"],
                      **_override_marks(despite_unverified, override_reason),
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return record

    def approve_to_pitfall(self, knowledge, ref: str, *, remedy: str,
                           trigger: str = "", symptom: str = "", root_cause: str = "",
                           scope: list[str] | None = None, source: str = "review",
                           despite_unverified: bool = False,
                           override_reason: str = "") -> dict:
        """批准并提升为已审核知识（写路径唯一入口：人在命令行点这个头）。

        #109：trigger 回落必须对「键存在但为空串」同样生效——`row.get("exception_type", "runtime_error")`
        只在键缺失时给默认，探索类草稿 exception_type="" 原样透出 → 批准的坑点 trigger 为空，
        被 #59 数据完整性防线永久拒绝匹配：批准「成功」、留痕齐全，知识却永不生效（死资产假成功，
        与 #102 静默 0 条同族）。空串与缺失一律回落到非空默认，绝不产出空 trigger 坑点。
        """
        row = self.resolve(ref)
        if not remedy:
            raise ValueError("提升知识必须提供 remedy：没有修复动作的坑点无法被 heal 命中")
        from .experience import ExperienceJournal
        journal = ExperienceJournal(self.dir)
        pitfall = journal.approve_pitfall(knowledge, row, approved=True,
            trigger=trigger or row.get("exception_type") or "runtime_error",
            symptom=symptom or row.get("failure_class") or "step_failed",
            root_cause=root_cause or row.get("task", ""),
            remedy=remedy, scope=scope or [row.get("task", "general")], source=source)
        rows = self.rows()
        for r in rows:
            # ③ 镜像纪律（终验#8 续修）：批准只翻 pending 行，已驳回历史行不回翻
            # （与 reject/_is_pending、append_draft 决定态幂等同一族防线）。
            if r.get("id") == row["id"] and _is_pending(r):
                r["status"] = APPROVED
                r["pitfall_ref"] = pitfall["id"]
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                if despite_unverified:
                    r["approved_despite_unverified"] = True
        self._rewrite(rows)
        self._decide({"action": "approve", "id": row["id"], "task": row.get("task"),
                      "remedy": remedy[:120], "pitfall": pitfall["id"],
                      **_override_marks(despite_unverified, override_reason),
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return pitfall

    def stats(self) -> dict:
        rows = self.rows()
        key_of = {PENDING: "pending", APPROVED: "approved", REJECTED: "rejected"}
        out = {"total": len(rows), "pending": 0, "approved": 0, "rejected": 0,
               "pending_by_kind": {}, "pending_by_class": {}}
        for r in rows:
            # #130 面B：判据与 pending()/reject_kind() 收敛 _is_pending——三套旧口径里
            # 这里是「缺省即 pending」派，与列表派互斥导致 CLI 同屏矛盾（列表空、stats 非 0）。
            if _is_pending(r):
                out["pending"] += 1
                kind = r.get("kind", "pitfall")
                out["pending_by_kind"][kind] = out["pending_by_kind"].get(kind, 0) + 1
                fc = r.get("failure_class", "unknown")
                out["pending_by_class"][fc] = out["pending_by_class"].get(fc, 0) + 1
            else:
                out[key_of.get(r.get("status"), "pending")] += 1
        return out


def evidence_card(row: dict) -> str:
    """一条草稿的 30 秒判断材料：是什么、哪一步、怎么复现、证据在哪。

    C2（design_c2 §3.1）：行首状态位 [VERIFIED]/[UNVERIFIED]，后者附缺失键清单——
    审核塔 30 秒判断的前提是看得见缺什么。只标注可批准的三 kind；
    trace_step/code_review 本就不可批准，不误导「补三元组就能批」。
    """
    kind = row.get("kind", "pitfall")
    problems: list[str] = []
    if kind in GATED_KINDS:
        problems = verification_problems(_verification_of(row))
    badge = ""
    if kind in GATED_KINDS:
        badge = "[VERIFIED]  " if not problems else "[UNVERIFIED]  "
    lines = [f"{badge}id={row.get('id')}  kind={kind}  "
             f"class={row.get('failure_class', '?')}  seen={row.get('seen', 1)}"]
    if problems:
        lines.append(f"  证据 : 缺三元组 -> {'; '.join(problems)}"
                     "（补齐 evidence.verification 或 override 批准：--allow-unverified --reason \"<依据>\"）")
    exc = row.get("exception_type", "")
    lines.append(f"  task : {row.get('task', '?')}" + (f"（{exc}）" if exc else ""))
    if row.get("pitfall_id"):
        lines.append(f"  坑点 : {row['pitfall_id']}（同坑复发——若 remedy 已够，reject 即可）")
    if row.get("kind") == "bu_overlay":
        ev = row.get("evidence") or {}
        # X4：坐标对比箭头用纯 ASCII `<->`（旧的双向箭头字形 U+2194 不可 cp936 编码，
        # 返回串被 cli print 到 GBK 控制台会崩——explore.py:60 真机崩例同族）。它藏在
        # 返回串不是 print 字面量，test_xplat_sentinels 扫不到，防线在
        # tests/test_c2_verification.py 的直接断言（卡片整体可 cp936 编码）。
        lines.append(f"  坐标 : {ev.get('base_bu')}={ev.get('base_value')!r} <-> "
                     f"{ev.get('other_bu')}={ev.get('other_value')!r}")
        lines.append(f"  批准 : atlas review --approve {row.get('id')} --remedy \"<目标BU真实值>\""
                     "（写私有 overlay 库；未经人审不落坐标）")
        return "\n".join(lines)
    if row.get("kind", "pitfall") == "flow_candidate":
        # 落库线：跑绿固化的候选卡片——展示序列规模与成功复现命令，批准即入库
        ev = row.get("evidence") or {}
        steps = ev.get("steps") if isinstance(ev.get("steps"), list) else []
        verif = ev.get("verification") if isinstance(ev.get("verification"), dict) else {}
        lines.append(f"  序列 : {len(steps)} 步（跑绿固化，steps/assert 原文在 evidence）")
        if verif.get("command"):
            lines.append(f"  复现 : {verif['command']}（verdict={verif.get('verdict', '?')}）")
        lines.append(f"  入库 : atlas review --approve {row.get('id')} [--remedy \"<库名>\"]"
                     "（写私有公共任务库 task_library；未经人审不落库）")
        return "\n".join(lines)
    ev = row.get("evidence") or {}
    if ev.get("url"):
        lines.append(f"  现场 : {ev['url']}")
    if ev.get("snapshot_file"):
        lines.append(f"  快照 : {ev['snapshot_file']}")
    # Allure 分析来源：显示 source 标识与现场文本（供人审 30 秒判断）
    if ev.get("source") == "allure":
        lines.append("  来源 : allure 分析（atlas allure-analyze --to-tower）")
    if ev.get("text"):
        lines.append(f"  现场文本 : {str(ev['text'])[:200]}")
    if row.get("kind", "pitfall") == "pitfall":
        lines.append("  复现 : atlas run <同一 flow>（先 atlas review --stats 看同键积压）")
        lines.append(f"  批准 : atlas review --approve {row.get('id')} --remedy \"<修复动作>\"")
    elif row.get("kind") == "selector_candidate":
        lines.append(f"  入库 : atlas review --approve {row.get('id')} --component <组件> "
                     f"--element <元素> --remedy \"<选择器>\"（直接写回 components.json）")
    else:  # trace_step：步骤素材，不是可批准的知识
        lines.append("  处置 : 人工写成 flow YAML 后 atlas compile；"
                     f"不单独批准：atlas review --reject {row.get('id')} --reason \"<原因>\"")
    return "\n".join(lines)


def grouped_view(rows: list[dict]) -> str:
    """聚类视图：批量类（selector_candidate/trace_step）按证据源压成紧凑表，
    pitfall 保留全证据卡片——批量决定看表，逐条判断看卡。"""
    if not rows:
        return "审核队列清空。"
    out = []
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r.get("kind", "pitfall"), []).append(r)
    # 坑点逐条判断：全卡片
    for row in by_kind.get("pitfall", []):
        out.append(evidence_card(row))
        out.append("-" * 70)
    for kind in ("selector_candidate", "trace_step", "flow_candidate"):
        group = by_kind.get(kind, [])
        if not group:
            continue
        # 同快照/同 URL 归一堆：一屏看完再决定批量还是逐条
        clusters: dict[str, list[dict]] = {}
        for r in group:
            ev = r.get("evidence") or {}
            src = ev.get("snapshot_file") or ev.get("url") or r.get("task", "?")
            clusters.setdefault(src, []).append(r)
        for src, items in clusters.items():
            out.append(f"== {kind} ×{len(items)}  源: {src}")
            for r in items:
                name = r.get("name") or r.get("checkpoint") or r.get("task", "")
                role = r.get("role", "step")
                out.append(f"  {r['id']}  {role:<10} {str(name)[:40]:<42} seen={r.get('seen', 1)}")
            out.append(f"  批量: atlas review --kind {kind} --template mapping.jsonl"
                       " → 人填 → --apply；噪声: --reject <id> --reason \"<原因>\"")
            out.append("-" * 70)
    return "\n".join(out)

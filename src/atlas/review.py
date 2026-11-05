"""审核吞吐入口：草稿→证据卡片→批量人审→晋级/驳回→决策留痕。

精炼厂的瓶颈从来不是矿，是塔。这个模块只做一件事：
把"判断一条提案"的成本压到分钟级——列表带证据、id 前缀定位、
批准即写知识、驳回留原因、全部落审计。critical 档无自动路径。
"""
from __future__ import annotations
import json
import time
from pathlib import Path

QUEUE_FILE = "experience_drafts.jsonl"
DECISIONS_FILE = "review_decisions.jsonl"
PENDING, APPROVED, REJECTED = "pending_review", "approved", "rejected"

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
        try:
            raw = self.path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
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
            with (self.dir / DECISIONS_FILE).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(decision, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def reject(self, ref: str, reason: str = "") -> dict:
        row = self.resolve(ref)
        rows = self.rows()
        for r in rows:
            if r.get("id") == row["id"]:
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
        """导出人可编辑的决策模板：选择器候选带 component/element/selector，坑点/坐标带 remedy。

        模板行必须自带 task 上下文——真机 --apply 暴露过缺口：只有 id 的模板人无法辨认。
        批量批准不等于自动批准：这份文件必须被人改过再 --apply，空字段的行会失败并点名。
        """
        out = []
        for r in self.pending(kind):
            row_kind = r.get("kind", "pitfall")
            if row_kind == "selector_candidate":
                out.append({"id": r["id"], "task": r.get("task", ""),
                            "kind": "selector_candidate",
                            "component": "", "element": "", "selector": ""})
            else:
                out.append({"id": r["id"], "task": r.get("task", ""),
                            "kind": row_kind, "remedy": ""})
        return out

    def apply(self, knowledge, decisions: list[dict]) -> dict:
        """人填好模板后的批量执行：仍逐条走 approve 分发、逐条留痕；单行失败不拦其余。

        #109：人编辑决策文件时把整行改成 JSON 数组/null 是「合法 JSON 但非映射」——
        旧实现 `row.get` 裸 AttributeError 顶掉整批：前面已批准的写回不回滚，后面合法行全部不跑，
        CLI 拿不到 failed 清单（rc2 口径失效）。与 #94/#95/#97 同红线：非对象形态点名拒绝，不裸崩。
        """
        result = {"applied": 0, "failed": []}
        for row in decisions:
            if not isinstance(row, dict):
                result["failed"].append({"id": "?", "error": f"决策行必须是映射，实为 {type(row).__name__}"})
                continue
            ref = str(row.get("id", ""))
            try:
                self.approve(knowledge, ref,
                             remedy=row.get("selector") or row.get("remedy", ""),
                             component=row.get("component", ""),
                             element=row.get("element", ""))
                result["applied"] += 1
            except (KeyError, ValueError) as exc:
                result["failed"].append({"id": ref, "error": str(exc)})
        return result

    def approve(self, knowledge, ref: str, *, remedy: str, component: str = "",
                element: str = "", trigger: str = "", symptom: str = "",
                root_cause: str = "", scope: list[str] | None = None,
                source: str = "review") -> dict:
        """批准入口按 kind 分发：选择器候选进组件库，坐标差异进 overlay 库，
        坑点进知识，trace 步骤拒之塔外。"""
        row = self.resolve(ref)
        kind = row.get("kind", "pitfall")
        if kind == "trace_step":
            raise ValueError("trace_step 是步骤素材不是知识：人工写成 flow YAML 后 "
                             "atlas compile 校验，或直接 reject 留原因")
        if kind == "code_review":
            raise ValueError("code_review 是代码质量评审意见：不自动写知识。"
                             "请人工按规则处置（修改代码或在 CI 中处理）")
        if kind == "bu_overlay":
            return self.approve_to_overlay(knowledge, row, remedy=remedy, source=source)
        if kind == "selector_candidate":
            if not (component and element):
                raise ValueError("批准 selector_candidate 必须指定组件与元素："
                                 "atlas review --approve <id> --component <c> "
                                 "--element <e> --remedy \"<选择器>\"")
            return self.approve_to_selector(knowledge, row, component=component,
                                            element=element, selector=remedy, source=source)
        return self.approve_to_pitfall(knowledge, row, remedy=remedy, trigger=trigger,
                                       symptom=symptom, root_cause=root_cause,
                                       scope=scope, source=source)

    def approve_to_overlay(self, knowledge, ref, *, remedy: str,
                           source: str = "review") -> dict:
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
            if r.get("id") == row["id"]:
                r["status"] = APPROVED
                r["overlay_ref"] = f"{other_bu}.{key}"
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._rewrite(rows)
        self._decide({"action": "approve_overlay", "id": row["id"], "task": row.get("task"),
                      "bu": other_bu, "key": key, "value": remedy[:120],
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return record

    def approve_to_selector(self, knowledge, ref, *, component: str, element: str,
                            selector: str, source: str = "review") -> dict:
        """批准并写回组件注册表：remedy 位放选择器本体，人审是唯一入库路径。"""
        row = self.resolve(ref)
        if row.get("kind") != "selector_candidate":
            raise ValueError(f"{row.get('id')} 不是 selector_candidate"
                             f"（kind={row.get('kind', 'pitfall')}）")
        if not selector:
            raise ValueError("批准选择器必须提供选择器本身：--remedy \"<选择器>\"")
        record = knowledge.save_component_selector(component, element, selector,
            approved=True, source=source,
            note=f"人审批准入库（来源探索候选 {row.get('id')}）")
        rows = self.rows()
        for r in rows:
            if r.get("id") == row["id"]:
                r["status"] = APPROVED
                r["selector_ref"] = f"{component}.{element}"
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._rewrite(rows)
        self._decide({"action": "approve_selector", "id": row["id"], "task": row.get("task"),
                      "component": component, "element": element, "selector": selector[:120],
                      "overwritten": record["overwritten"],
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return record

    def approve_to_pitfall(self, knowledge, ref: str, *, remedy: str,
                           trigger: str = "", symptom: str = "", root_cause: str = "",
                           scope: list[str] | None = None, source: str = "review") -> dict:
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
            if r.get("id") == row["id"]:
                r["status"] = APPROVED
                r["pitfall_ref"] = pitfall["id"]
                r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._rewrite(rows)
        self._decide({"action": "approve", "id": row["id"], "task": row.get("task"),
                      "remedy": remedy[:120], "pitfall": pitfall["id"],
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
    """一条草稿的 30 秒判断材料：是什么、哪一步、怎么复现、证据在哪。"""
    lines = [f"id={row.get('id')}  kind={row.get('kind', 'pitfall')}  "
             f"class={row.get('failure_class', '?')}  seen={row.get('seen', 1)}"]
    exc = row.get("exception_type", "")
    lines.append(f"  task : {row.get('task', '?')}" + (f"（{exc}）" if exc else ""))
    if row.get("pitfall_id"):
        lines.append(f"  坑点 : {row['pitfall_id']}（同坑复发——若 remedy 已够，reject 即可）")
    if row.get("kind") == "bu_overlay":
        ev = row.get("evidence") or {}
        lines.append(f"  坐标 : {ev.get('base_bu')}={ev.get('base_value')!r} ↔ "
                     f"{ev.get('other_bu')}={ev.get('other_value')!r}")
        lines.append(f"  批准 : atlas review --approve {row.get('id')} --remedy \"<目标BU真实值>\""
                     "（写私有 overlay 库；未经人审不落坐标）")
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
    for kind in ("selector_candidate", "trace_step"):
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

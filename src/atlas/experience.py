"""执行经验先草稿、后人审；第二次同坑从正式库直接命中。

en-3（wave5）：草稿 identity 掺**平台维度**（`resolve_plugin().name`）。旧 identity
`task|exception_type|failure_class|pitfall_id` 无平台段——两个平台出现同任务名+同异常
时 id 相同，`append_draft` 的同 id 合并会把两边证据互相顶掉（后到者覆盖先到者，
人审只看到一方现场）。**存量草稿 id 会变**（含空段的旧 id 不再生成）：旧行仍可列出/
审批，新草稿不再与旧 id 合并——这是本修复的有意行为变更。
"""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from .atomic_io import atomic_write_text, read_text_retry, update_text_locked
from .failures import classify_failure, exception_text


def _plugin_segment() -> str:
    """en-3：草稿 identity 的平台段（`resolve_plugin().name` 单点，不猜）。

    解析失败（插件模块坏）回落空段：仅退化到旧 identity，不阻断登记——草稿登记在
    engine 失败路径的 except 块里，抛错会把原用例异常顶掉（#100/#89 同红线）。
    """
    try:
        from .plugin_api import resolve_plugin
        return str(getattr(resolve_plugin(), "name", "") or "")
    except Exception:
        return ""


class ExperienceJournal:
    def __init__(self, root: Path):
        self.root = root

    def draft_from_text(self, *, task: str, text: str, failure_class: str,
                        exception_type: str = "", pitfall_id: str = "",
                        kind: str = "pitfall", evidence: dict | None = None) -> dict:
        """从文本造草稿（Allure 分析用）；draft(exc) 委托它。

        只造 pending_review——不新增知识写路径（红线 #9/#10）。text 存进 evidence
        供人审核阅（Allure 结果的 message/trace 没有异常对象，人审需要看原文）。
        同 id 合并 seen（#118 证据刷新保留）：append_draft 对同 id 行增 seen 并刷新 evidence。
        en-3：identity 首段掺插件名（跨平台同任务+同异常不再合并、证据不互相顶掉）。
        """
        plugin = _plugin_segment()
        identity = f"{plugin}|{task}|{exception_type}|{failure_class}|{pitfall_id}"
        row = {"id": hashlib.sha1(identity.encode()).hexdigest()[:12],
               "plugin": plugin,
               "task": task, "exception_type": exception_type,
               "failure_class": failure_class, "pitfall_id": pitfall_id,
               "kind": kind, "status": "pending_review",
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        merged_evidence = dict(evidence or {})
        if text:
            merged_evidence.setdefault("text", text)
        row["evidence"] = merged_evidence
        return row

    def draft(self, *, task: str, exc: BaseException, pitfall_id: str = "",
              kind: str = "pitfall", evidence: dict | None = None) -> dict:
        """异常对象 → pending_review 草稿；委托 draft_from_text 保持单一真相。"""
        failure_class = classify_failure(exc).value
        return self.draft_from_text(task=task, text=exception_text(exc),
                                    failure_class=failure_class,
                                    exception_type=type(exc).__name__,
                                    pitfall_id=pitfall_id, kind=kind, evidence=evidence)

    def draft_flow(self, *, case: str, digest: dict, evidence: dict | None = None) -> dict:
        """跑绿的 flow → pending_review 的 flow_candidate 草稿（落库线「跑成功→自动固化」）。

        identity = harvest:flow:<flow 摘要>：同一份 flow 重跑 id 不变，append_draft 既有
        语义自动只增 seen、刷新最新证据（不堆重复行）；flow 内容改了=新资产=新 id 新行。
        failure_class="success" 是草稿侧标签（classify_failure 不产它，explore 的
        "exploration" 同款先例），塔的 pending_by_class 由此分得开成功固化与失败草稿。
        只造草稿不写知识（红线 #9/#10）：晋级走 review 塔的人审批准。
        """
        identity = f"harvest:flow:{digest.get('md5', '') if isinstance(digest, dict) else digest}"
        row = {"id": hashlib.sha1(identity.encode()).hexdigest()[:12],
               "task": case, "exception_type": "", "failure_class": "success",
               "pitfall_id": "", "kind": "flow_candidate", "status": "pending_review",
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        merged_evidence = dict(evidence or {})
        if isinstance(digest, dict):
            merged_evidence.setdefault("flow_digest", dict(digest))
        row["evidence"] = merged_evidence
        return row

    def append_draft(self, experience: dict) -> Path:
        if experience.get("status") != "pending_review":
            raise ValueError("经验日志只接受 pending_review 草稿")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "experience_drafts.jsonl"
        # 同 id 已在队列则只增 seen 计数，不产生第二条审核负担。
        # 幂等判据跨决定态（终验#8 事故修复）：旧条件只认同 id 的 pending_review 行，
        # 已批准/已驳回的历史行不挡再登记——同一份探索 trace 处置后二次
        # explore --compile，append_draft 找不到 pending 同 id 行、直写旁路式地追加出
        # 重复行（aaa919a656d3/1f52715010fc/6aae7590ce3c 各两份，磁盘复现）。重复行还
        # 放大 reject/approve 的按 id 全行翻转：一条新 pending 的驳回会把旧已批准行的
        # 审核状态改写（跨决定历史污染）。现同 id 命中一律只增 seen、刷新 evidence，
        # 绝不追加第二条；决定态行的 status 不回翻（批准/驳回本身是终态记录，
        # seen/evidence 是聚合计数与最新证据，不伪装成新的待审素材）。
        #
        # wave4 w4-3：「同 id 合并」是天然读-改-写；旧实现「锁外 `_read_queue` + 锁内写」
        # 在 6 线程竞争下读侧撞写锁、`read_text_retry` 的 3×50ms 预算耗尽 →
        # `PermissionError` 逃出 → **工作线程死亡、丢行**（循环 150 次实测 10 次失败 =
        # 6.7%，verify3 复现）。现读-改-写整体进 `update_text_locked` 的**同一把锁**
        # （进程内 `_INPROC_LOCK` + 跨进程文件锁）：读在同一临界区内用**同一个 fd** 完成，
        # 不再存在「锁外读撞写锁」这个面。
        update_text_locked(path, lambda old_text: self._merge_draft(old_text, experience))
        return path

    @staticmethod
    def _merge_draft(old_text: str, experience: dict) -> str:
        """`update_text_locked` 的锁内变换：返回新全文（换行 `os.linesep` 保旧字节形态）。

        同 id 命中 → 增 seen、刷新 evidence（#118）后整份重写；否则尾部追加（残行缺
        尾换行时先补断行——#112：裸 append 把新草稿黏在无尾换行残行后，读侧「跳过残行」
        连坐把这条完整合法的新草稿一起跳掉，登记成功却永久不可见）。
        换行用 `os.linesep`：与旧追加路径（`append_text_locked`）/旧合并路径
        （`_atomic_write` 文本模式）逐字节一致（Windows CRLF / POSIX LF）。
        """
        sep = os.linesep
        rows = ExperienceJournal._parse_rows(old_text)
        draft_id = experience.get("id")
        for row in rows:
            if draft_id and row.get("id") == draft_id:
                row["seen"] = row.get("seen", 1) + 1
                # #118：同 id 再登记时刷新证据——bu_overlay 的 id 按「键+两侧 BU」定，
                # 坐标文件修正后（如 BU-B 的 ou B→C）id 不变而 evidence 变；旧实现只增
                # seen 不更新证据，审核塔停在过期值，人审按 --remedy 批准的「目标BU真实值」
                # 基于旧证据落成错误坐标（#102/#105 坏形态伪装有效结果同族）。对 pitfall
                # 无害：同 id 的证据本就由同一异常派生，刷新是等值覆盖。
                if "evidence" in experience:
                    row["evidence"] = experience["evidence"]
                return sep.join(json.dumps(r, ensure_ascii=False) for r in rows) + sep
        line = json.dumps(experience, ensure_ascii=False)
        needs_break = bool(old_text) and old_text[-1] not in ("\n", "\r")
        return old_text + (sep if needs_break else "") + line + sep

    @staticmethod
    def _parse_rows(raw: str) -> list[dict]:
        """逐行容错解析 JSONL 草稿：残行 / 合法 JSON 但非映射行一律跳过。

        坏字节已由调用方 replace 回落（#112）。`_read_queue`（读盘）与本模块的锁内
        变换 `_merge_draft`（已在内存的全文）共用这一把容错尺（#92 一处实现）。
        """
        rows = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # 崩溃残行：跳过但不丢后面的审核队列
            # #130 面C：合法 JSON 但非映射行（`[1,2]`/null/字符串）——旧实现
            # row.get 裸 AttributeError：append_draft（engine 失败路径的登记器）
            # 崩在 except 块里顶掉原用例异常（#100 定性器红线），审核塔同崩。
            # 与 review.rows 同纪律跳过——坏行无法表达为草稿，随原子重写清掉。
            if isinstance(row, dict):
                rows.append(row)
        return rows

    @staticmethod
    def _read_queue(path: Path) -> list[dict]:
        if not path.exists():
            return []
        # #112：队列尾行可被中断腰斩在字节上（append 写 UTF-8 中文中途 Ctrl-C）——
        # 旧 read_text 裸 UnicodeDecodeError：append_draft（engine 失败路径的草稿登记）
        # 崩在 except 块里会把原用例异常顶掉（#100 定性器红线）；审核塔读队列同崩，
        # 好行全被一条坏字节拖死（#109 trace 本体同族）。坏字节 replace 读入后
        # 残行 JSON 解析必败，自然落进既有「跳过残行」分支，合法行照常可见。
        # cc-1 读侧配套：本文件由加锁写路径写，Windows 上并发读撞锁报
        # PermissionError（瞬态）——read_text_retry 重试即过。
        try:
            raw = read_text_retry(path, encoding="utf-8")
        except UnicodeDecodeError:
            raw = read_text_retry(path, encoding="utf-8", errors="replace")
        return ExperienceJournal._parse_rows(raw)

    @staticmethod
    def _atomic_write(path: Path, rows: list[dict]) -> None:
        """审核状态改写必须原子+fsync：半行 JSONL 会毁掉整个队列。

        wave4 rb-*：委托 `atomic_io.atomic_write_text`（tmp+fsync+os.replace 一处实现，
        不留第二把尺）。文本模式写，字节形态与旧实现逐字节一致。
        """
        atomic_write_text(path, "".join(json.dumps(row, ensure_ascii=False) + "\n"
                                        for row in rows))

    def approve_pitfall(self, knowledge, draft: dict, *, trigger: str, symptom: str,
                        root_cause: str, remedy: str, scope: list[str], source: str,
                        approved: bool = False) -> dict:
        if not approved:
            raise PermissionError("只有人工审核后才能把经验提升为知识")
        pitfall = knowledge.draft_pitfall(RuntimeError(trigger), symptom=symptom,
            root_cause=root_cause, remedy=remedy, scope=scope, source=source)
        pitfall.update(id="reviewed-" + pitfall["id"].removeprefix("draft-"),
                       trigger=trigger, status="approved")
        # 内容相同 id 即相同（sha1）：重复批准换的是 scope/source，不是再堆一条
        rows = [r for r in knowledge.pitfalls() if r.get("id") != pitfall["id"]]
        rows.append(pitfall)
        knowledge.save_reviewed("pitfalls", rows, approved=True)
        return pitfall


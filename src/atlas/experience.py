"""执行经验先草稿、后人审；第二次同坑从正式库直接命中。"""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from .failures import classify_failure, exception_text


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
        """
        identity = f"{task}|{exception_type}|{failure_class}|{pitfall_id}"
        row = {"id": hashlib.sha1(identity.encode()).hexdigest()[:12],
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

    def append_draft(self, experience: dict) -> Path:
        if experience.get("status") != "pending_review":
            raise ValueError("经验日志只接受 pending_review 草稿")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "experience_drafts.jsonl"
        # 同 id 已在队列则只增 seen 计数，不产生第二条审核负担。
        existing = self._read_queue(path)
        for row in existing:
            if row.get("id") == experience.get("id") and row.get("status") == "pending_review":
                row["seen"] = row.get("seen", 1) + 1
                # #118：同 id 再登记时刷新证据——bu_overlay 的 id 按「键+两侧 BU」定，
                # 坐标文件修正后（如 BU-B 的 ou B→C）id 不变而 evidence 变；旧实现只增
                # seen 不更新证据，审核塔停在过期值，人审按 --remedy 批准的「目标BU真实值」
                # 基于旧证据落成错误坐标（#102/#105 坏形态伪装有效结果同族）。对 pitfall
                # 无害：同 id 的证据本就由同一异常派生，刷新是等值覆盖。
                if "evidence" in experience:
                    row["evidence"] = experience["evidence"]
                self._atomic_write(path, existing)
                return path
        # #112：上一轮写盘中途 Ctrl-C 会留下无换行尾的残行（腰斩出非法 UTF-8 字节，
        # 或 UTF-8 完整但 JSON 半行）。裸 append 把整条新草稿黏在残行之后——读侧
        # 「跳过残行」跳掉的正是这条完整合法的新草稿：登记成功、无异常、草稿却永久
        # 不可见（静默丢审核素材，与 #102 静默 0 条同族假成功）。追加前先补断行，
        # 新草稿独占一行必被读回；旧残字节让读侧 replace 回落自然跳过。
        needs_break = path.exists() and path.stat().st_size > 0
        if needs_break:
            with path.open("rb") as stream:
                stream.seek(-1, os.SEEK_END)
                needs_break = stream.read(1) not in (b"\n", b"\r")
        with path.open("a", encoding="utf-8") as stream:
            if needs_break:
                stream.write("\n")
            stream.write(json.dumps(experience, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return path

    @staticmethod
    def _read_queue(path: Path) -> list[dict]:
        if not path.exists():
            return []
        # #112：队列尾行可被中断腰斩在字节上（append 写 UTF-8 中文中途 Ctrl-C）——
        # 旧 read_text 裸 UnicodeDecodeError：append_draft（engine 失败路径的草稿登记）
        # 崩在 except 块里会把原用例异常顶掉（#100 定性器红线）；审核塔读队列同崩，
        # 好行全被一条坏字节拖死（#109 trace 本体同族）。坏字节 replace 读入后
        # 残行 JSON 解析必败，自然落进既有「跳过残行」分支，合法行照常可见。
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = path.read_text(encoding="utf-8", errors="replace")
        rows = []
        for line in raw.splitlines():
            if line.strip():
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
    def _atomic_write(path: Path, rows: list[dict]) -> None:
        """审核状态改写必须原子+fsync：半行 JSONL 会毁掉整个队列。"""
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())
        os.replace(tmp, path)

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


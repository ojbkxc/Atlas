"""en-3：草稿 identity 掺平台维度——跨平台同任务+同异常不再合并、证据不互相顶掉。

判据：
1. 两个假插件对同一 task+异常产出**不同**草稿 id；
2. 同插件重复登记仍只增 seen（#118 去重语义不回归）；
3. 证据刷新语义（同 id 再登记刷新 evidence）不回归；
4. 插件解析失败回落空段（不阻断登记——登记在 engine except 块里，#100/#89）。
"""
from __future__ import annotations

import json

import pytest

from atlas import experience
from atlas.experience import ExperienceJournal


@pytest.fixture()
def journal(tmp_path):
    return ExperienceJournal(tmp_path)


def _fake_plugin(monkeypatch, name):
    class _P:
        pass
    p = _P()
    p.name = name
    monkeypatch.setattr("atlas.plugin_api.resolve_plugin", lambda: p)


def test_two_plugins_get_different_ids(journal, monkeypatch):
    _fake_plugin(monkeypatch, "platform-a")
    a = journal.draft_from_text(task="open_list", text="boom", failure_class="interaction")
    _fake_plugin(monkeypatch, "platform-b")
    b = journal.draft_from_text(task="open_list", text="boom", failure_class="interaction")
    assert a["id"] != b["id"], "跨平台同任务+同异常必须分开（否则证据互相顶掉）"
    assert a["plugin"] == "platform-a" and b["plugin"] == "platform-b"


def test_same_plugin_dedup_and_seen(journal, monkeypatch, tmp_path):
    _fake_plugin(monkeypatch, "platform-a")
    first = journal.draft_from_text(task="t", text="boom", failure_class="f",
                                    evidence={"verification": {"verdict": "fail"}})
    journal.append_draft(first)
    again = journal.draft_from_text(task="t", text="boom2", failure_class="f",
                                    evidence={"verification": {"verdict": "fail"}})
    journal.append_draft(again)
    rows = [json.loads(line) for line in
            (tmp_path / "experience_drafts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    same = [r for r in rows if r["id"] == first["id"]]
    assert len(same) == 1, "同插件同坑只应一行"
    assert same[0]["seen"] >= 2


def test_plugin_resolution_failure_falls_back_to_empty_segment(journal, monkeypatch):
    monkeypatch.setattr("atlas.plugin_api.resolve_plugin",
                        lambda: (_ for _ in ()).throw(RuntimeError("plugin broken")))
    row = journal.draft_from_text(task="t", text="x", failure_class="f")
    assert row["plugin"] == ""
    assert row["id"]  # 仍能造草稿，不抛

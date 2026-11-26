"""allure 面的「评审降级」可见性（台账 L01 收口，上游 A-02 的留白）。

## 挡的是什么

`--review-mode knowledge` 下知识库坏掉时，`review_code` 会降级为 static（选择器核对整段
不执行），但**只看 allure 报告的人看不到这件事**：`emit_allure_review` 每文件一个
result、没有 summary/容器概念，而 `knowledge_degraded` 当时只写进了 `summary` 与 text 格式。

后果：CI 里看 allure 的人以为「knowledge 模式跑过、没发现问题」，实际是**核对根本没做**。
分数因此偏高、`--fail-under` 可能由红转绿（假绿）。

## 为什么不用「降级容器 / 伪 result」

`*-result.json` 的消费方不止 allure 渲染器——`devops_sync.load_allure_results` 会把每个
`*-result.json` 当成**一个用例**去同步。造一个伪 result 会凭空多出一条不存在的用例，
那是红线 #7 的反向违反（不能把没跑的东西说成跑了）。

因此标记钉在**每个真实 result** 上，语义也更准确：「本文件的这次评审降级了」。

## 四条

1. `test_degraded_emits_status_prefix_and_label`：降级时 statusDetails 消息含「降级」且
   有 `atlas.knowledge_degraded=true` label；
2. `test_normal_has_no_degraded_marker`：正向对照——未降级时**不出现**该 label
   （防噪声化，与 summary 侧同纪律）；
3. `test_review_code_allure_end_to_end_carries_degradation`：真跑
   `review_code(mode="knowledge", format="allure")`（插件解析必炸）→ 落盘产物能读到标记；
4. `test_allure_analyze_still_reads_degraded_output`：加了 label 之后
   `load_allure_results` 仍能正常读取（不得为了可见性把结果集改成读不动的东西）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas import review_code as rc_mod
from atlas.allure_analysis import load_allure_results


def _file_result(*, degraded: bool) -> dict:
    fr = {
        "file": "sample.py",
        "kind": "pytest",
        "findings": [],
        "score": {"total": 90, "grade": "A", "dimensions": {}},
        "mode": "knowledge",
        "baseline_notes": [],
        "finding_count": 0,
    }
    if degraded:
        fr["knowledge_degraded"] = True
    return fr


def _results(out_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(out_dir.glob("*-result.json"))]


# ── 1/2：直接打穿 emit ─────────────────────────────────────────────────────

def test_degraded_emits_status_prefix_and_label(tmp_path):
    out = tmp_path / "allure"
    rc_mod.emit_allure_review([_file_result(degraded=True)], out)
    rows = _results(out)
    assert len(rows) == 1, "只应有一个真实 result（不许伪造额外 result）"
    res = rows[0]
    message = res["statusDetails"]["message"]
    assert "降级" in message, f"报告读者必须一眼看到降级，实际 message={message!r}"
    assert "选择器核对未执行" in message, "要点明降级后果，不能只说「降级」"
    labels = {lb["name"]: lb["value"] for lb in res["labels"]}
    assert labels.get("atlas.knowledge_degraded") == "true", f"机器可筛的 label 缺失：{labels}"


def test_normal_has_no_degraded_marker(tmp_path):
    """正向对照：未降级不得凭空多出 label / 消息前缀（与 summary 侧同纪律）。"""
    out = tmp_path / "allure"
    rc_mod.emit_allure_review([_file_result(degraded=False)], out)
    res = _results(out)[0]
    labels = {lb["name"]: lb["value"] for lb in res["labels"]}
    assert "atlas.knowledge_degraded" not in labels, f"未降级却带了标记：{labels}"
    assert "降级" not in res["statusDetails"]["message"]


# ── 3：端到端（真跑 review_code）────────────────────────────────────────

def test_review_code_allure_end_to_end_carries_degradation(tmp_path, monkeypatch):
    """知识库不可用 → 产物里能看到降级。这是「CI 只看 allure」那条路的唯一证据。"""
    def _boom():
        raise RuntimeError("插件包坏了")
    monkeypatch.setattr("atlas.plugin_api.resolve_plugin", _boom)

    src = tmp_path / "sample.py"
    src.write_text("def f():\n    return 1\n", encoding="utf-8")
    out = tmp_path / "allure"
    result = rc_mod.review_code([src], mode="knowledge", format="allure", out=out)
    assert result["summary"].get("knowledge_degraded") is True

    rows = _results(out)
    assert rows, "没有产出 allure result"
    assert any(lb["name"] == "atlas.knowledge_degraded" and lb["value"] == "true"
               for lb in rows[0]["labels"]), rows[0]["labels"]


# ── 4：结果集仍可被消费 ───────────────────────────────────────────────────

def test_allure_analyze_still_reads_degraded_output(tmp_path):
    """加了 label 之后 `load_allure_results` 仍能正常读取（不破坏既有消费方）。

    它返回 `list[AllureFinding]`，坏文件是**跳过 + stderr 计数**而不是抛异常——
    所以这里的判据是「读得到，且条数对得上」，而不是「没抛异常就算过」（那会漏掉
    “全部静默跳过”的情形）。
    """
    out = tmp_path / "allure"
    rc_mod.emit_allure_review([_file_result(degraded=True)], out)
    findings = load_allure_results(out)
    assert len(findings) == 1, f"降级标记把结果集读坏了，只读到 {len(findings)} 条"

# ── 5/6：容器级结果（契约字面要求）────────────────────────────────────────

def _containers(out_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(out_dir.glob("*-container.json"))]


def test_degraded_emits_container_referencing_all_results(tmp_path):
    """降级时产出 **container**（契约字面要求「容器级结果」）。

    注意 container 在 Allure 里**不渲染成用例**——它只是结构承载。真正的可见性来自
    每个 result 的 statusDetails/label（上面前两条）。两者缺一都不算做完：
    只做 label 会被问「容器呢」，只做 container 则等于没写。
    """
    out = tmp_path / "allure"
    rc_mod.emit_allure_review([_file_result(degraded=True), _file_result(degraded=True)], out)
    boxes = _containers(out)
    assert len(boxes) == 1, f"降级应产出恰好一个 container，实际 {len(boxes)}"
    box = boxes[0]
    assert "降级" in box["name"], f"容器名要点明降级：{box['name']!r}"
    assert "未执行" in box["description"], "容器描述要说明后果（选择器核对未执行）"
    uuids = {r["uuid"] for r in _results(out)}
    assert set(box["children"]) == uuids, "children 必须引用全部 result uuid"
    assert not out_dir_extra(box), "container 只需 children/befores/afters，不该塞私有字段"


def out_dir_extra(box: dict) -> list[str]:
    """container 里出现 Allure 不认识的私有键（除 name/description 这两个无害附加项）。"""
    allowed = {"uuid", "name", "description", "children", "befores", "afters"}
    return sorted(set(box) - allowed)


def test_normal_has_no_container(tmp_path):
    """正向对照：未降级不得凭空多一个 container（与 label 侧同纪律）。"""
    out = tmp_path / "allure"
    rc_mod.emit_allure_review([_file_result(degraded=False)], out)
    assert _containers(out) == [], "未降级却产出了 container"

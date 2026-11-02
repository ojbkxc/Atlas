"""harvest_knowledge 脚本测试：从 markdown 提取知识草稿。"""
from scripts.harvest_knowledge import extract


def test_extract_parses_bullet_lines(tmp_path):
    md = tmp_path / "pitfalls.md"
    md.write_text(
        "# 坑点\n\n"
        "- **保存按钮无响应：** Vue 双模型未同步，需 otherHeader\n"
        "- **税率 NPE：** 后端 productTaxRate 为 null\n"
        "- 普通行不是坑点\n",
        encoding="utf-8")
    rows = extract(md, source="sit-notes")
    assert len(rows) == 2
    assert rows[0]["symptom"] == "保存按钮无响应"
    assert rows[0]["root_cause"] == "Vue 双模型未同步，需 otherHeader"
    assert rows[0]["status"] == "pending_review"
    assert rows[0]["scope"] == ["demo"]
    assert rows[0]["source"] == "sit-notes"
    assert rows[1]["symptom"] == "税率 NPE"


def test_extract_ids_are_stable(tmp_path):
    md = tmp_path / "pitfalls.md"
    md.write_text("- **坑：** 内容", encoding="utf-8")
    rows1 = extract(md, source="test")
    rows2 = extract(md, source="test")
    assert rows1[0]["id"] == rows2[0]["id"]
    assert rows1[0]["id"].startswith("draft-")


def test_extract_skips_non_bullet_lines(tmp_path):
    md = tmp_path / "pitfalls.md"
    md.write_text("# 标题\n普通段落\n- **坑：** 内容\n", encoding="utf-8")
    rows = extract(md, source="test")
    assert len(rows) == 1


def test_extract_different_sources_have_different_ids(tmp_path):
    md = tmp_path / "pitfalls.md"
    md.write_text("- **坑：** 内容", encoding="utf-8")
    rows_a = extract(md, source="source-a")
    rows_b = extract(md, source="source-b")
    assert rows_a[0]["id"] != rows_b[0]["id"]

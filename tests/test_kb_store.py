# -*- coding: utf-8 -*-
"""kb/store.py 的测试：上传、元数据、namespace 隔离、软删除。

★ 这里不测检索/把关 —— 那是 service.RAG._guard 的领域；
  本文件只守住“语料层”的契约，避免把两套判据写进两个地方。
"""

from __future__ import annotations

import pytest

from kb.source import UnsupportedFormat, UploadSource
from kb.store import KnowledgeStore

MD = """---
doc: 学生请销假制度
version: 2026-01-01
source: 学生工作处
---
本制度适用于全体在校学生。

## 第三条 审批
请假一天以内由班主任审批。

## 第四条 销假
返校后应当在 3 个工作日内销假。
"""


def test_upload_markdown_chunks_and_metadata(tmp_path):
    store = KnowledgeStore(tmp_path)
    rec = store.upload("demo", "leave.md", MD.encode("utf-8"), acl=["hr"])

    assert rec["title"] == "学生请销假制度"      # 取自 frontmatter doc
    assert rec["source"] == "学生工作处"          # 取自 frontmatter source
    assert rec["version"] == "2026-01-01"        # 取自 frontmatter version
    assert rec["tombstone"] is False
    assert rec["acl"] == ["hr"]

    chunks = store.load_chunks("demo")
    assert len(chunks) == 3                       # 前言 + 第三条 + 第四条
    assert {c["clause"] for c in chunks} == {"前言", "第三条 审批", "第四条 销假"}
    for c in chunks:
        assert c["namespace"] == "demo"
        assert c["doc_id"] == rec["doc_id"]
        assert c["acl"] == ["hr"]
        assert c["content_hash"] == rec["content_hash"]
        assert c["text_full"]                      # 保留全文


def test_namespace_isolation(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.upload("a", "faq.md", b"## Q1\nA1\n")
    store.upload("b", "faq.md", b"## Q1\nB1\n")

    a = store.load_chunks("a")
    b = store.load_chunks("b")
    assert [c["text"] for c in a] == ["A1"]
    assert [c["text"] for c in b] == ["B1"]


def test_delete_is_tombstone_and_not_returned(tmp_path):
    store = KnowledgeStore(tmp_path)
    rec = store.upload("demo", "faq.md", b"## Q1\nA1\n")
    assert store.delete("demo", rec["doc_id"]) is True

    assert store.load_chunks("demo") == []
    all_recs = store.list_documents("demo", include_deleted=True)
    assert len(all_recs) == 1
    assert all_recs[0]["tombstone"] is True
    # 再删一次应当返回 False（幂等，不重复记）
    assert store.delete("demo", rec["doc_id"]) is False


def test_txt_and_html(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.upload("demo", "notes.txt", "第一段。\n\n第二段。".encode("utf-8"))
    store.upload("demo", "page.html", b"<html><body><p>Hello</p><p>World</p></body></html>")

    chunks = store.load_chunks("demo")
    assert any(c["text"] == "第一段。" for c in chunks)
    assert any("Hello" in c["text"] for c in chunks)
    assert any("World" in c["text"] for c in chunks)


def test_filename_cannot_escape_namespace(tmp_path):
    store = KnowledgeStore(tmp_path)
    rec = store.upload("demo", "../../evil.md", b"## Q\nA\n")
    assert "/" not in rec["file"] and "\\" not in rec["file"]
    assert (tmp_path / "demo" / "files" / rec["file"]).is_file()
    assert not (tmp_path / "evil.md").exists()


def test_unsupported_format_raises(tmp_path):
    store = KnowledgeStore(tmp_path)
    with pytest.raises(UnsupportedFormat):
        store.upload("demo", "x.exe", b"MZ")


def test_reupload_updates_metadata_from_frontmatter(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.upload("demo", "a.md", b"---\ndoc: V1\nversion: 2026-01-01\nsource: HR\n---\n## Q\nA1\n")
    store.upload("demo", "a.md", b"---\ndoc: V2\nversion: 2026-02-01\nsource: Finance\n---\n## Q\nA2\n")

    rows = store.list_documents("demo")
    assert len(rows) == 1
    assert rows[0]["title"] == "V2"
    assert rows[0]["version"] == "2026-02-01"
    assert rows[0]["source"] == "Finance"
    assert store.load_chunks("demo")[0]["text"] == "A2"


def test_load_chunks_reports_missing_file(tmp_path):
    """文件被外部删掉时不能静默当成 0 块 —— 要留下原因。"""
    store = KnowledgeStore(tmp_path)
    rec = store.upload("demo", "faq.md", b"## Q1\nA1\n")
    (tmp_path / "demo" / "files" / rec["file"]).unlink()

    assert store.load_chunks("demo") == []
    assert store.last_errors and rec["doc_id"] in store.last_errors[0]


def test_upload_source_contract(tmp_path):
    root = tmp_path / "src"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "a.md").write_text("## Q\nA\n", encoding="utf-8")

    src = UploadSource(root, "demo")
    metas = src.list_documents()
    assert len(metas) == 1
    raw = src.fetch(metas[0].doc_id)
    assert raw.meta.namespace == "demo"
    assert "## Q" in raw.text

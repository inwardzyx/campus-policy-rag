# -*- coding: utf-8 -*-
"""企业语料 HTTP 接口 + namespace 检索的快速测试。

不加载 embedding 模型：
  · /kb 接口只依赖 store，用 tmp store + 桩 reload；
  · search 的 namespace 过滤用假 route_orders，验证“过滤后再 RRF”这一层。
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

import service as svc
from kb.store import KnowledgeStore


@pytest.fixture()
def kb_client(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path)
    monkeypatch.setattr(svc.rag, "store", store)
    monkeypatch.setattr(svc.rag, "ready", True)
    monkeypatch.setattr(svc.rag, "reload_enterprise", lambda: None)
    return TestClient(svc.app), store


def test_kb_upload_list_delete(kb_client):
    client, _ = kb_client
    md = "---\ndoc: FAQ\nversion: 2026-01-01\nsource: 客服部\n---\n## Q1\n七天无理由退货。\n"
    r = client.post("/kb/demo/documents?filename=faq.md&acl=public", content=md.encode("utf-8"))
    assert r.status_code == 200, r.text
    doc_id = r.json()["document"]["doc_id"]

    r = client.get("/kb/demo/documents")
    assert r.status_code == 200
    assert len(r.json()["documents"]) == 1

    r = client.delete(f"/kb/demo/documents/{doc_id}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/kb/demo/documents").json()["documents"] == []


def test_kb_rejects_unsupported_format(kb_client):
    client, _ = kb_client
    r = client.post("/kb/demo/documents?filename=x.exe", content=b"MZ")
    assert r.status_code == 415


def test_final_acl_filter_blocks_private_blocks():
    items = [
        ({"doc": "PUB", "clause": "c", "acl": ["public"]}, 0.9),
        ({"doc": "HR", "clause": "c", "acl": ["hr"]}, 0.8),
    ]
    assert len(svc._final_acl_filter(items, None, "test")) == 1
    assert len(svc._final_acl_filter(items, ["hr"], "test")) == 2
    assert len(svc._final_acl_filter(items, ["finance"], "test")) == 1


def test_search_acl_filters_before_rrf():
    r = svc.RAG()
    r.chunks = [
        {"doc": "PUB", "clause": "c", "text": "p", "namespace": "demo", "acl": ["public"]},
        {"doc": "HR", "clause": "c", "text": "h", "namespace": "demo", "acl": ["hr"]},
    ]
    r.bm25 = object()
    r.route_orders = lambda q: (
        q,
        np.array([0.9, 0.8], dtype="float32"),
        np.array([0, 1]),
        np.array([0, 1]),
    )

    assert [c["doc"] for c, _ in r.search("q", namespace="demo")] == ["PUB"]
    assert [c["doc"] for c, _ in r.search("q", namespace="demo", acl=["hr"])] == ["PUB", "HR"]
    assert [c["doc"] for c, _ in r.search("q", namespace="demo", acl=["finance"])] == ["PUB"]


def test_search_filters_namespace_before_rrf():
    r = svc.RAG()
    r.chunks = [
        {"doc": "D", "clause": "c", "text": "d", "namespace": "default"},
        {"doc": "A", "clause": "c", "text": "a", "namespace": "a"},
        {"doc": "B", "clause": "c", "text": "b", "namespace": "b"},
    ]
    r.bm25 = object()
    r.route_orders = lambda q: (
        q,
        np.array([0.9, 0.8, 0.7], dtype="float32"),
        np.array([0, 1, 2]),
        np.array([0, 1, 2]),
    )

    assert [c["doc"] for c, _ in r.search("q", k=5)] == ["D"]          # 默认只查 default
    assert [c["doc"] for c, _ in r.search("q", k=5, namespace="a")] == ["A"]
    assert r.search("q", k=5, namespace="missing") == []

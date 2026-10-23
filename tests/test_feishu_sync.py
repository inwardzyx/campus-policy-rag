# -*- coding: utf-8 -*-
"""飞书 Wiki 连接器测试：树遍历、docx 正文、增量/删除、权限跳过、分页。

不访问真实飞书：FakeClient 只实现 list_nodes/raw_docx。
"""

from __future__ import annotations

import pytest

from kb.feishu import FeishuClient, FeishuError, FeishuWikiSource
from kb.store import KnowledgeStore


class FakeClient:
    def __init__(self, tree: dict, contents: dict):
        self.tree = tree
        self.contents = contents

    def list_nodes(self, space_id, parent=None):
        return list(self.tree.get(parent, []))

    def raw_docx(self, document_id):
        if document_id not in self.contents:
            raise FeishuError(1770032, "forbidden", 403)
        return self.contents[document_id]


def _node(token, obj, typ="docx", title="标题", child=False, ts="1767225600"):
    return {"node_token": token, "obj_token": obj, "obj_type": typ,
            "title": title, "has_child": child, "node_type": "origin",
            "obj_edit_time": ts}


def _fixture():
    tree = {
        None: [_node("n1", "d1", title="报销制度", child=True),
               _node("n2", "s1", typ="sheet", title="表格")],
        "n1": [_node("n3", "d3", title="薪酬保密")],
    }
    contents = {
        "d1": "## 第三条 报销时限\n费用发生后 30 日内提交。",
        "d3": "## 第三条 薪酬保密\n不得向同事透露个人薪酬。",
    }
    return FakeClient(tree, contents)


def test_sync_adds_docx_skips_other_types(tmp_path):
    store = KnowledgeStore(tmp_path)
    src = FeishuWikiSource(_fixture(), "sp1", "demo", acl=["hr"], sleep_sec=0)
    stats = src.sync(store)

    assert stats["added"] == 2
    assert stats["skipped"] == 1          # sheet
    chunks = store.load_chunks("demo")
    assert len(chunks) == 2
    assert {c["doc"] for c in chunks} == {"报销制度", "薪酬保密"}
    assert all(c["source"].startswith("feishu://sp1/") for c in chunks)
    assert all(c["acl"] == ["hr"] for c in chunks)


def test_sync_is_incremental_and_updates_changed_doc(tmp_path):
    store = KnowledgeStore(tmp_path)
    fake = _fixture()
    src = FeishuWikiSource(fake, "sp1", "demo", sleep_sec=0)
    assert src.sync(store)["added"] == 2
    assert src.sync(store)["unchanged"] == 2

    fake.contents["d1"] = "## 第三条 报销时限\n改为 60 日内提交。"
    stats = src.sync(store)
    assert stats["updated"] == 1 and stats["unchanged"] == 1
    assert any("60 日" in c["text"] for c in store.load_chunks("demo"))


def test_sync_deletes_docs_removed_from_wiki(tmp_path):
    store = KnowledgeStore(tmp_path)
    fake = _fixture()
    src = FeishuWikiSource(fake, "sp1", "demo", sleep_sec=0)
    src.sync(store)

    fake.tree["n1"] = []
    stats = src.sync(store)
    assert stats["deleted"] == 1
    assert [c["doc"] for c in store.load_chunks("demo")] == ["报销制度"]


def test_sync_records_permission_error_and_continues(tmp_path):
    store = KnowledgeStore(tmp_path)
    fake = _fixture()
    fake.contents.pop("d3")                # 模拟应用没有这篇的阅读权限
    stats = FeishuWikiSource(fake, "sp1", "demo", sleep_sec=0).sync(store)

    assert stats["added"] == 1
    assert stats["skipped"] == 2          # sheet + 无权限 docx
    assert stats["errors"] and "forbidden" in stats["errors"][0]["error"]


def test_tenant_token_reads_top_level(monkeypatch):
    """飞书 token 接口把 token 放顶层，不在 data 里 —— 读错就会误报 no_token。"""
    client = FeishuClient("id", "secret")
    monkeypatch.setattr(client, "_request",
                        lambda *a, **k: {"tenant_access_token": "t-1", "expire": 7200})
    assert client.tenant_access_token() == "t-1"


def test_list_nodes_paginates(monkeypatch):
    client = FeishuClient("id", "secret")
    pages = [
        {"items": [{"node_token": "a"}], "has_more": True, "page_token": "p2"},
        {"items": [{"node_token": "b"}], "has_more": False},
    ]
    calls = []

    def fake_request(method, path, params=None, **kwargs):
        calls.append(dict(params or {}))
        return pages.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)
    nodes = client.list_nodes("sp1")
    assert [n["node_token"] for n in nodes] == ["a", "b"]
    assert "page_token" not in calls[0]
    assert calls[1]["page_token"] == "p2"

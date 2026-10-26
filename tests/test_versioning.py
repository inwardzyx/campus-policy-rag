# -*- coding: utf-8 -*-
"""as-of 版本裁决（批 4）的守门用例。

## 盯的四条
  ① **event_time 真的裁决**：晚于事件日的条款必须被丢（reason=not_yet_effective），
     而且**答案里不能出现**（不是只在 trace 里标一下）；
  ② **现行查询丢废止版**：不带 event_time 时 `status=deprecated` 被丢（reason=stale）；
  ③ **同一条款多版本只留当时最新的那一版**（其余 reason=superseded）；
  ④ **日期未知不算 1970**：`effective_date=""` 的块保留并计入 `unknown_date_blocks`，
     **不假装能裁决** —— 它也不会被当成"已废止"丢掉。

★ 变异体在 `scripts/mutate_versioning.py`：忽略 event_time / 不丢废止版，两条都必须变红。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

import service as svc                           # noqa: E402
from kb import attribution, versioning as ver   # noqa: E402


def ch(doc="制度.md", clause="第四条", eff="", status="active", text="正文"):
    return {"doc": doc, "clause": clause, "text": text, "text_full": text,
            "effective_date": eff, "status": status, "acl": ["public"]}


# ---------------------------------------------------------------- ① event_time

def test_future_clause_is_dropped_when_event_time_is_past() -> None:
    items = [ch(eff="2026-01-01")]
    kept, dropped, rep = ver.filter_versions(items, event_time="2025-06-01")
    assert kept == [] and rep["dropped_by_reason"] == {"not_yet_effective": 1}
    assert dropped[0]["_drop_reason"] == "not_yet_effective"


def test_effective_on_the_same_day_counts_as_in_force() -> None:
    items = [ch(eff="2025-06-01")]
    kept, dropped, _ = ver.filter_versions(items, event_time="2025-06-01")
    assert len(kept) == 1 and dropped == [], "生效日当天就该按新条款算"


def test_old_version_wins_for_a_past_event() -> None:
    old = ch(eff="2024-01-01", text="旧版")
    new = ch(eff="2026-01-01", text="新版")
    kept, dropped, rep = ver.filter_versions([new, old], event_time="2025-06-01")
    assert [c["text_full"] for c in kept] == ["旧版"]
    assert rep["dropped_by_reason"] == {"not_yet_effective": 1}


# ---------------------------------------------------------------- ② 现行查询

def test_deprecated_is_dropped_without_event_time() -> None:
    kept, dropped, rep = ver.filter_versions(
        [ch(eff="2020-01-01", status="deprecated"), ch(eff="2025-01-01")])
    assert len(kept) == 1 and kept[0]["effective_date"] == "2025-01-01"
    assert rep["dropped_by_reason"] == {"stale": 1}


def test_deprecated_kept_when_event_time_precedes_its_replacement() -> None:
    """★ 废止不等于"从没存在"：问的是废止前的事，就必须用当时有效的旧条款。

    同时：那时还没生效的新版**必须被丢**（reason=not_yet_effective），
    这是"按时间裁决"的另一半，别只测一半。
    """
    kept, dropped, _ = ver.filter_versions(
        [ch(eff="2020-01-01", status="deprecated"), ch(eff="2025-01-01")],
        event_time="2021-01-01")
    assert [c["effective_date"] for c in kept] == ["2020-01-01"]
    assert [d["_drop_reason"] for d in dropped] == ["not_yet_effective"]


# ---------------------------------------------------------------- ③ 多版本

def test_latest_version_at_that_time_wins() -> None:
    items = [ch(eff="2023-01-01"), ch(eff="2024-06-01"), ch(eff="2025-09-01")]
    kept, dropped, rep = ver.filter_versions(items, event_time="2025-01-01")
    assert [c["effective_date"] for c in kept] == ["2024-06-01"]
    assert rep["dropped_by_reason"] == {"superseded": 1, "not_yet_effective": 1}


def test_different_clauses_are_not_merged() -> None:
    kept, dropped, rep = ver.filter_versions(
        [ch(clause="第四条", eff="2024-01-01"), ch(clause="第五条", eff="2024-01-01")])
    assert len(kept) == 2 and dropped == [] and rep["groups"] == 2


# ---------------------------------------------------------------- ④ 日期未知

def test_unknown_date_is_kept_and_counted() -> None:
    kept, dropped, rep = ver.filter_versions([ch(eff="")], event_time="2025-06-01")
    assert len(kept) == 1, "日期未知不能当成'未生效'丢掉"
    assert rep["unknown_date_blocks"] == 1
    assert dropped == []


# ---------------------------------------------------------------- ⑤ 数据层接线

def test_upload_reads_effective_date_and_status(tmp_path) -> None:
    from kb.store import KnowledgeStore

    store = KnowledgeStore(tmp_path)
    body = ("---\ndoc: 报销制度\nversion: 2\nsource: 测试\n"
            "effective_date: 2025-03-01\nstatus: deprecated\n---\n\n"
            "## 第三条\n报销时限三十日。\n").encode("utf-8")
    rec = store.upload("hr", "报销制度.md", body)
    assert (rec["effective_date"], rec["status"]) == ("2025-03-01", "deprecated")
    chunks = store.load_chunks("hr")
    assert chunks and chunks[0]["effective_date"] == "2025-03-01"
    assert chunks[0]["status"] == "deprecated"


def test_upload_without_frontmatter_dates_is_unknown_not_fake() -> None:
    from kb.store import KnowledgeStore
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        store = KnowledgeStore(td)
        body = "---\ndoc: 制度\nversion: 1\nsource: x\n---\n\n## 第一条\n正文。\n".encode()
        rec = store.upload("hr", "制度.md", body)
        assert rec["effective_date"] == "" and rec["status"] == "active"


# ---------------------------------------------------------------- ⑥ 服务接线

@pytest.fixture
def rag(monkeypatch):
    monkeypatch.setattr(svc.rag, "ready", True)
    state = {"cands": []}
    monkeypatch.setattr(svc.rag, "search",
                        lambda q, k=5, namespace=None, acl=None:
                        [(c, 0.9) for c in state["cands"]])
    monkeypatch.setattr(svc.rag, "rerank",
                        lambda q, cands: [(c, s, 9, "假精排") for c, s in cands])
    monkeypatch.setattr(svc.rag, "answer", lambda q, chunks: "按第四条办理[1]。")
    return state


def test_chat_with_event_time_filters_before_answering(rag) -> None:
    rag["cands"] = [ch(eff="2026-01-01", text="新版条款"), ch(eff="2024-01-01", text="旧版条款")]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr",
                                        event_time="2025-06-01"))
    assert r.context.as_of["event_time"] == "2025-06-01"
    assert r.context.as_of["dropped_by_reason"] == {"not_yet_effective": 1}
    assert [b.reason for b in r.context.excluded] == ["not_yet_effective"]
    assert len(r.context.blocks) == 1


def test_chat_without_event_time_drops_deprecated(rag) -> None:
    rag["cands"] = [ch(eff="2020-01-01", status="deprecated"), ch(eff="2025-01-01")]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    assert r.context.as_of["event_time"] is None
    assert r.context.as_of["dropped_by_reason"] == {"stale": 1}


def test_invalid_event_time_is_rejected_by_validation() -> None:
    with pytest.raises(Exception):
        svc.ChatRequest(question="q", event_time="2025/06/01")


def test_version_drop_maps_to_stale_version_bucket(rag) -> None:
    """归因：gold 被版本裁决丢掉 ⇒ stale_version，而不是 retrieval_miss。"""
    rag["cands"] = [ch(eff="2026-01-01")]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr",
                                        event_time="2025-06-01"))
    case = {"id": "q1", "type": "answer", "gold": ["制度.md", "第四条"]}
    got = attribution.classify(case, r.model_dump() if hasattr(r, "model_dump") else r)
    assert got["bucket"] == "stale_version", got

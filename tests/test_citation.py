# -*- coding: utf-8 -*-
"""引用锚点（batch 2）的守门用例。

## 盯的四条
  ① **编号合法性**：答案写了 `[7]` 但只喂了 3 块 ⇒ 必须进 `unknown_ids`，且
     `degraded["citation"]` 非空（**降级可见**，不是悄悄放过）；
  ② **不标也算错**：有结论却没引用 ⇒ `uncited_count > 0`；但"我不知道"这类句子不算；
  ③ **只对 namespace 生效**：docs/ 旧路径没有 `context`、prompt 仍走旧模板（golden 在
     `tests/test_context.py` 里逐字节守着），流式也不多发 `citation` 事件；
  ④ **模板真的编号了**：v2 prompt 里块前缀是 `[1] [2]` 且带引用要求 —— 否则"引用"只是口头要求。

★ ①的变异版本在 `scripts/mutate_citation.py`：把编号整体偏移一位，用例必须变红。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

import service as svc                           # noqa: E402
from kb import citation as cit                  # noqa: E402
from test_chat_stream import _events            # noqa: E402


# ---------------------------------------------------------------- ① 纯函数

def test_parse_ids_dedupes_and_keeps_order() -> None:
    assert cit.parse_ids("结论[2]……再[1]……又[2]") == [2, 1]
    assert cit.parse_ids("没有引用") == []


def test_all_citations_valid() -> None:
    blocks = [{"clause": "第四条"}, {"clause": "第五条"}]
    rep = cit.check("可以申请[1]，但要审批[2]。", blocks)
    assert rep["used_ids"] == [1, 2]
    assert rep["unknown_ids"] == []
    assert rep["validity"] == 1.0


def test_unknown_id_is_flagged() -> None:
    blocks = [{"clause": "第四条"}, {"clause": "第五条"}]
    rep = cit.check("可以申请[1]，依据见[7]。", blocks)
    assert rep["used_ids"] == [1]
    assert rep["unknown_ids"] == [7]
    assert rep["validity"] == 0.5


def test_no_citation_at_all_is_none_not_zero() -> None:
    rep = cit.check("可以申请，但要审批。", [{"clause": "第四条"}])
    assert rep["used_ids"] == [] and rep["unknown_ids"] == []
    assert rep["validity"] is None, "一条引用都没有时 validity 必须是 None（缺失≠0）"
    assert rep["uncited_count"] >= 1


def test_refusal_sentence_is_not_counted_as_uncited() -> None:
    rep = cit.check("资料中没有相关规定，我不知道。", [{"clause": "第四条"}])
    assert rep["uncited_count"] == 0, "说不知道的句子不该被算成'没标出处'"


def test_paragraph_level_citation_covers_its_sentences() -> None:
    """★ 回归用例：模型是**段末**标 `[1]`，一条引用覆盖整段。

    按句子切会把同段里"其实已被 [1] 覆盖"的句子全算成没标注 —— 第一版就是这样，
    实测把 20 题的 `uncited_count` 假高了 3~5 倍。这条用例守着"别改回按句子算"。
    """
    answer = ("第一条结论成立；第二条也不冲突；最后一句才是引用。[1]\n\n"
              "资料中没有其他规定，我不知道。")
    rep = cit.check(answer, [{"clause": "第四条"}])
    assert rep["uncited_count"] == 0, rep
    assert rep["n_paragraphs"] == 2


def test_uncited_paragraph_is_counted() -> None:
    answer = "先给一段没有任何出处的结论，长度够十个字符。\n\n结论二也有出处。[1]"
    rep = cit.check(answer, [{"clause": "第四条"}])
    assert rep["uncited_count"] == 1
    assert rep["uncited_samples"] and "没有任何出处" in rep["uncited_samples"][0]


def test_refusal_phrase_variants_are_not_counted_as_uncited() -> None:
    """★ 实测变体：模型写"无法完整回答""并未系统列出"，也属于"说资料不够"。

    只认"无法回答"会把这些段落算成没出处（2026-10-25 实测把 q2 假高了 1 段）。
    """
    answer = "根据现有资料，无法完整回答学校有哪些处分等级。\n\n并未系统列出全部等级。"
    rep = cit.check(answer, [{"clause": "第四条"}])
    assert rep["uncited_count"] == 0, rep


def test_clause_numbers_in_answer_are_reported() -> None:
    blocks = [{"clause": "第三章-第四条"}]
    rep = cit.check("按第四条可以申请[1]。", blocks)
    assert rep["clause_numbers_in_answer"] == ["第四条"]


# ---------------------------------------------------------------- ③④ 接线

@pytest.fixture
def rag(monkeypatch):
    monkeypatch.setattr(svc.rag, "ready", True)
    state = {"cands": [], "answer_text": "按第四条可以申请[1]。", "prompts": [], "fed": []}

    monkeypatch.setattr(svc.rag, "search",
                        lambda q, k=5, namespace=None, acl=None:
                        [(c, 0.9) for c in state["cands"]])
    monkeypatch.setattr(svc.rag, "rerank",
                        lambda q, cands: [(c, s, 9, "假精排") for c, s in cands])

    def fake_answer(q, chunks):
        state["prompts"].append(svc.RAG.build_answer_prompt(q, chunks))
        state["fed"].append([dict(c) for c in chunks])
        return state["answer_text"]

    monkeypatch.setattr(svc.rag, "answer", fake_answer)
    return state


def _chunk(doc="d.md", clause="第四条"):
    return {"doc": doc, "clause": clause, "text": "条款正文" * 20,
            "text_full": "条款正文" * 20, "acl": ["public"]}


def test_namespace_prompt_is_numbered_and_citation_attached(rag) -> None:
    rag["cands"] = [_chunk(clause="第四条"), _chunk(clause="第五条")]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    prompt = rag["prompts"][0]
    assert "[1] 条款正文" in prompt and "[2] 条款正文" in prompt
    assert "引用要求" in prompt
    assert r.context.citation["used_ids"] == [1]
    assert r.context.citation["unknown_ids"] == []


def test_unknown_citation_id_is_reported_as_degraded(rag) -> None:
    rag["cands"] = [_chunk(clause="第四条")]
    rag["answer_text"] = "按第四条可以申请[1]，另见[7]。"
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    assert r.context.citation["unknown_ids"] == [7]
    assert r.context.degraded.get("citation") == "unknown_id", "越界引用必须体现在 degraded 里"


def test_docs_path_has_no_citation_and_keeps_legacy_prompt(rag) -> None:
    rag["cands"] = [_chunk(clause="第四条")]
    r = svc.handle_chat(svc.ChatRequest(question="q"))
    assert r.context is None
    assert "[1] " not in rag["prompts"][0], "旧路径 prompt 不许出现编号"
    assert rag["prompts"][0].startswith("你是学校学生事务的答疑助手。")


# ---------------------------------------------------------------- ③ 流式

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import limits as limits_mod

    lim = limits_mod.Limiter(
        store=limits_mod.InProcessStore(),
        limits=limits_mod.Limits(burst=10, per_min=0.0, daily_req=100, daily_tokens=100000,
                                 fail_mode="closed", trust_proxy=False, allow_client_id=False))
    monkeypatch.setattr(svc, "LIMITER", lim)
    return TestClient(svc.app)


@pytest.fixture
def stream_rag(monkeypatch):
    monkeypatch.setattr(svc.rag, "ready", True)
    state = {"cands": [], "fed": [], "text": "按第四条可以申请[1]，另见[7]。"}

    monkeypatch.setattr(svc.rag, "search",
                        lambda q, k=5, namespace=None, acl=None:
                        [(c, 0.9) for c in state["cands"]])
    monkeypatch.setattr(svc.rag, "rerank",
                        lambda q, cands: [(c, s, 9, "假精排") for c, s in cands])

    def fake_stream(q, chunks):
        state["fed"].append([dict(c) for c in chunks])
        for ch in state["text"]:
            yield ch

    monkeypatch.setattr(svc.rag, "answer_stream", fake_stream)
    return state


def test_stream_emits_citation_after_deltas_for_namespace(client, stream_rag) -> None:
    stream_rag["cands"] = [_chunk(clause="第四条")]
    with client.stream("POST", "/chat/stream",
                       json={"question": "q", "namespace": "hr"}) as resp:
        raw = "".join(resp.iter_text())
    kinds = [e for e, _ in _events(raw)]
    assert kinds[0] == "sources" and kinds[-1] == "citation", kinds
    assert kinds.count("citation") == 1
    payload = [d for e, d in _events(raw) if e == "citation"][0]
    assert payload["citation"]["unknown_ids"] == [7]


def test_stream_docs_path_has_no_citation_event(client, stream_rag) -> None:
    stream_rag["cands"] = [_chunk(clause="第四条")]
    with client.stream("POST", "/chat/stream", json={"question": "q"}) as resp:
        raw = "".join(resp.iter_text())
    kinds = [e for e, _ in _events(raw)]
    assert "citation" not in kinds, f"旧路径多了一个事件：{kinds}"

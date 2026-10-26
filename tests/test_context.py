# -*- coding: utf-8 -*-
"""Context Layer v1（`kb/context.py` + service 接线）的守门用例。

## 这组用例盯的五条

  ① **预算按 chars 算**：4000 总预算、`retrieved_cap=1840`、output 预留 400 独立；
  ② **整块丢，不截半块**：超预算的块进 excluded(reason=budget)，单块超上限进
     excluded(reason=oversize)，正文一起不喂；
  ③ **旧路径逐字节不变**：`docs/`（namespace=None）下 prompt 用 golden 字面量对照，
     `context` 恒为 None，流式 payload 里**不出现** `context` 键；
  ④ **trace 说真话**：被裁掉的块真的没进生成模型；trace 里不复制正文；
  ⑤ **拒答也要有 trace**：`no_block_fits` / `low_score` / `not_ready` 三种都能看出原因。

★ 第 ③ 条是本轮唯一不能靠"大概一样"放过去的：`docs/` 的 105 题老路径
  （A/B 脚本、评测集、`/search` 对照）都建立在它逐字段一致上。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

import service as svc                           # noqa: E402
from kb import context as ctx_mod               # noqa: E402

# ★ 复用 test_chat_stream 的 SSE 解析（同一份实现，不在这里抄第二遍）。
from test_chat_stream import _events            # noqa: E402

BUDGET = ctx_mod.Budget()                       # 默认 4000 / 0.46 / 400 ⇒ 检索额度 1840


def chunk(doc: str, text: str, acl=None) -> dict:
    """造一个与 loader 同形的块：`doc`/`clause`/`text`/`text_full`/`acl`。"""
    return {"doc": doc, "clause": "第 1 条", "text": text, "text_full": text,
            "acl": acl or ["public"]}


def sized(n: int, doc: str = "d.md") -> dict:
    """造一个正文恰好 n 个字符的块（`len()` 是这套预算的唯一度量）。"""
    return chunk(doc, "字" * n)


# ==================================================================
# ① 预算：默认值、env 覆盖、坏值可见
# ==================================================================

def test_budget_defaults_match_the_agreed_numbers() -> None:
    """默认预算 = 4000 chars / 46% / 预留 400 ⇒ 检索额度 1840，八层额度合计 4000。"""
    b = ctx_mod.Budget.from_env({})
    assert (b.total_chars, b.retrieved_ratio, b.output_reserve_chars) == (4000, 0.46, 400)
    assert b.retrieved_cap == 1840
    caps = b.layer_caps()
    assert caps["retrieved"] == 1840 and caps["output"] == 400
    assert sum(caps.values()) == 4000, f"八层额度加起来不等于总预算：{caps}"


def test_budget_env_overrides_are_honoured() -> None:
    """三个 env 都能改，且改完仍然是"总预算 = 各层之和"。"""
    b = ctx_mod.Budget.from_env({"CONTEXT_BUDGET_CHARS": "2000",
                                 "CONTEXT_RETRIEVED_RATIO": "0.5",
                                 "CONTEXT_OUTPUT_RESERVE_CHARS": "200"})
    assert (b.total_chars, b.retrieved_cap, b.output_reserve_chars) == (2000, 1000, 200)
    assert sum(b.layer_caps().values()) == 2000


def test_bad_env_value_falls_back_and_is_recorded() -> None:
    """坏值退回默认，但**记进 ignored_env** —— 否则配错了没人知道。

    ★ 这条断言的是"看得见"，不是"能用"：静默按默认预算跑，是最难查的那类问题。
    """
    b = ctx_mod.Budget.from_env({"CONTEXT_OUTPUT_RESERVE_CHARS": "abc",
                                 "CONTEXT_RETRIEVED_RATIO": "0",   # 非法：会被切掉全部额度
                                 "CONTEXT_BUDGET_CHARS": "abc"})
    assert (b.total_chars, b.retrieved_cap, b.output_reserve_chars) == (4000, 1840, 400)
    joined = " ".join(b.ignored_env)
    for key in ("CONTEXT_OUTPUT_RESERVE_CHARS=abc", "CONTEXT_RETRIEVED_RATIO=0",
                "CONTEXT_BUDGET_CHARS=abc"):
        assert key in joined, f"{key} 被静默吞掉了：{b.ignored_env}"


def test_reserve_not_smaller_than_total_is_zeroed_and_recorded() -> None:
    """预留 ≥ 总预算 ⇒ 检索永远没额度。按 0 处理，并把这件事记下来。"""
    b = ctx_mod.Budget.from_env({"CONTEXT_BUDGET_CHARS": "300",
                                 "CONTEXT_OUTPUT_RESERVE_CHARS": "400"})
    assert b.output_reserve_chars == 0
    assert any("CONTEXT_OUTPUT_RESERVE_CHARS=400" in x for x in b.ignored_env)


def test_trace_declares_eight_layers_but_only_retrieved_is_enabled() -> None:
    """八层都在 trace 里（固定顺序），但只有 retrieved 有 used_chars。"""
    kept, trace = ctx_mod.select_retrieved([sized(100)], budget=BUDGET, namespace="hr")
    d = trace.to_dict()
    assert list(d["budget"]["layers"]) == list(ctx_mod.LAYERS)
    assert d["budget"]["enabled_layers"] == ["retrieved"]
    assert d["budget"]["layers"]["retrieved"]["used_chars"] == 100
    others = {k: v["used_chars"] for k, v in d["budget"]["layers"].items() if k != "retrieved"}
    assert sum(others.values()) == 0, f"没接的层不该有 used_chars：{others}"
    # ★ 关键：未实现的层必须**自称未实现**，不能只给一个 0（0 会被读成"用了 0"）
    for name, info in d["budget"]["layers"].items():
        if name == "retrieved":
            assert info["status"] == "implemented" and info["reason"] == ""
        else:
            assert info["status"] == "reserved" and info["reason"], (
                f"{name} 层是 reserved 却没有理由 —— 报告里又会变成假指标")


def test_implementing_memory_requires_a_deliberate_test_change() -> None:
    """**锁定测试**：memory（以及其余 6 层）现在明确是"未实现"。

    ★ 这是一道刻意的闸门：谁要真去接记忆，就必须**改这条用例**、并同步 `RESERVED_REASONS`。
      否则"顺手把 used_chars 从 0 改成非 0"就会让报告悄悄多出一个没人验证过的指标。
    """
    from kb import context as ctx_mod

    _, trace = ctx_mod.select_retrieved(
        [{"doc": "d.md", "clause": "第 1 条", "text": "正文", "text_full": "正文"}],
        budget=ctx_mod.Budget(), namespace="hr")
    budget = trace.to_dict()["budget"]

    assert budget["implemented_layers"] == ["retrieved"]
    assert budget["enabled_layers"] == ["retrieved"]
    assert {x["layer"] for x in budget["reserved_layers"]} == set(ctx_mod.LAYERS) - {"retrieved"}
    memory = budget["layers"]["memory"]
    assert memory["status"] == "reserved"
    assert "未实现" in memory["reason"]


# ==================================================================
# ② 整块丢，不截半块
# ==================================================================

def test_select_keeps_whole_blocks_until_cap() -> None:
    """4 块 × 500 chars，额度 1840 ⇒ 只放 3 块（1500），第 4 块整块丢。"""
    chunks = [sized(500, f"d{i}.md") for i in range(4)]
    kept, trace = ctx_mod.select_retrieved(chunks, budget=BUDGET, namespace="hr")
    d = trace.to_dict()
    assert [c["doc"] for c in kept] == ["d0.md", "d1.md", "d2.md"]
    assert [b["chars"] for b in d["blocks"]] == [500, 500, 500]
    assert [(b["source"], b["chars"], b["reason"]) for b in d["excluded"]] == \
        [("d3.md", 500, "budget")]
    assert d["degraded"] == {"retrieval": "budget_truncated"}
    assert d["budget"]["retrieved_used"] == 1500
    assert d["budget"]["retrieved_unused"] == 340


def test_oversize_block_is_dropped_without_blocking_the_rest() -> None:
    """单块 2000 > 1840 ⇒ oversize 丢掉；**后面的小块照样放**（不能整条链停住）。"""
    kept, trace = ctx_mod.select_retrieved([sized(2000, "big.md"), sized(100, "small.md")],
                                           budget=BUDGET, namespace="hr")
    d = trace.to_dict()
    assert [c["doc"] for c in kept] == ["small.md"]
    assert [(b["source"], b["chars"], b["reason"]) for b in d["excluded"]] == \
        [("big.md", 2000, "oversize")]


def test_neither_truncation_nor_half_block() -> None:
    """excluded 里记的 chars 是**整块**长度，不是"还能放下的余量"。

    ★ 这是"截半块比少一条危险"那条决定的可执行版本：一旦有人改成截断，
      chars 会变成 340 这类余量，这条用例立刻响。
    """
    chunks = [sized(1500, "a.md"), sized(500, "b.md")]   # 余量 340，b 放不下
    kept, trace = ctx_mod.select_retrieved(chunks, budget=BUDGET, namespace="hr")
    assert [c["doc"] for c in kept] == ["a.md"]
    ex = trace.to_dict()["excluded"]
    assert [(b["source"], b["chars"]) for b in ex] == [("b.md", 500)]


def test_no_block_fits_when_every_block_is_oversize() -> None:
    """所有块都放不下 ⇒ `no_block_fits`（服务层据此拒答，不许零上下文硬生成）。"""
    kept, trace = ctx_mod.select_retrieved([sized(3000, "x.md"), sized(2000, "y.md")],
                                           budget=BUDGET, namespace="hr")
    assert kept == []
    assert trace.to_dict()["degraded"] == {"retrieval": "no_block_fits"}


# ==================================================================
# ③ / ④ / ⑤ 服务接线（不加载模型：search/rerank/answer 全部可控）
# ==================================================================

@pytest.fixture
def rag(monkeypatch):
    """假管道：`state["cands"]` 决定检索结果，`state["rerank_score"]` 决定是否拒答。"""
    monkeypatch.setattr(svc.rag, "ready", True)
    state = {"cands": [], "rerank_score": 9, "answer_calls": [], "prompts": [],
             "search_kwargs": []}

    def fake_search(q, k=5, namespace=None, acl=None):
        state["search_kwargs"].append({"k": k, "namespace": namespace, "acl": acl})
        return [(c, 0.9) for c in state["cands"]]

    def fake_rerank(q, cands):
        return [(c, s, state["rerank_score"], "假精排") for c, s in cands]

    def fake_answer(q, chunks):
        state["answer_calls"].append([c["doc"] for c in chunks])
        state["prompts"].append(svc.RAG.build_answer_prompt(q, chunks))
        return "假答案"

    monkeypatch.setattr(svc.rag, "search", fake_search)
    monkeypatch.setattr(svc.rag, "rerank", fake_rerank)
    monkeypatch.setattr(svc.rag, "answer", fake_answer)
    return state


GOLDEN_PROMPT = (
    "你是学校学生事务的答疑助手。只根据下面的资料回答，资料里没有的就明确说不知道。\n"
    "资料：\n- 条款甲正文\n- 条款乙正文\n\n问题：宿舍能用电热毯吗？"
)


def test_docs_path_prompt_is_byte_identical_and_has_no_context(rag) -> None:
    """③ 旧路径：prompt 逐字节一样、`context` 为 None、search 走旧签名（不带 namespace）。"""
    rag["cands"] = [chunk("a.md", "条款甲正文"), chunk("b.md", "条款乙正文")]
    r = svc.handle_chat(svc.ChatRequest(question="宿舍能用电热毯吗？"))
    assert rag["prompts"] == [GOLDEN_PROMPT], f"旧路径 prompt 变了：{rag['prompts']!r}"
    assert r.context is None, "docs/ 默认路径不该有上下文层"
    assert rag["search_kwargs"] == [{"k": 5, "namespace": None, "acl": None}]
    # 字段集：只多一个 `context`（默认 None），语义与 `session_used` 同款
    assert set(r.model_dump()) == {"answer", "sources", "took_ms", "rerank_used",
                                   "knowledge_hit", "usage", "session_used", "context"}


def test_sso_roles_do_not_turn_on_the_context_layer(rag) -> None:
    """③ 反向：带 roles（ACL）但没 namespace ⇒ 仍然走旧路径。

    ★ 这条防的是"gating 被人顺手扩成 roles"：那会把企业身份带进 105 题老路径的响应里，
      而老路径的答案/字段是 A/B 与评测的基准。
    """
    rag["cands"] = [chunk("a.md", "条款甲正文")]
    r = svc.handle_chat(svc.ChatRequest(question="q"), roles=["hr"])
    assert r.context is None
    assert rag["search_kwargs"][0]["acl"] == ["hr"], "roles 仍要覆盖请求体 acl"


def test_namespace_chat_prunes_blocks_from_the_prompt_too(rag) -> None:
    """④ 3 块 × 900 = 2700 > 1840 ⇒ 只喂 2 块；`sources` 只剩 2 条。

    ★ 核心断言是 `answer_calls`：trace 说"丢了 big2"就必须真的没喂 ——
      "trace 一套、喂进去另一套"是这一版最容易犯的错。
    """
    rag["cands"] = [sized(900, f"big{i}.md") for i in range(3)]
    r = svc.handle_chat(svc.ChatRequest(question="报销多久内提交？", namespace="hr"))
    assert r.context is not None and r.context.namespace == "hr"
    assert rag["answer_calls"] == [["big0.md", "big1.md"]], "裁掉的块必须真的不进生成"
    assert [s.doc.split("｜")[0] for s in r.sources] == ["big0.md", "big1.md"]
    assert [b.source for b in r.context.excluded] == ["big2.md"]
    assert r.context.degraded == {"retrieval": "budget_truncated"}
    assert len(r.context.trace_id) == 32


def test_trace_does_not_copy_the_block_text(rag) -> None:
    """④ trace 只存定位信息：正文仍在 `sources` 里，但**不进 trace**。"""
    secret = "唯一标记-XQZ-7788-" + "正文" * 50
    rag["cands"] = [chunk("a.md", secret)]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    dumped = json.dumps(r.context.model_dump(), ensure_ascii=False)
    assert "XQZ-7788" not in dumped, "trace 里复制了正文"
    assert any("XQZ-7788" in s.text for s in r.sources), "sources 仍要带全文"


def test_namespace_with_no_block_fitting_refuses_without_generating(rag) -> None:
    """⑤ 候选有、预算放不下任何一块 ⇒ 拒答，**一次生成都不许调**。"""
    rag["cands"] = [sized(5000, "huge.md")]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    assert r.sources == [] and r.knowledge_hit is False
    assert r.context.degraded == {"retrieval": "no_block_fits"}
    assert rag["answer_calls"] == [], "零上下文硬生成 —— 拒答机制被绕过"
    assert r.context.excluded[0].reason == "oversize"


def test_low_score_refusal_still_carries_a_trace(rag) -> None:
    """⑤ 精排低分拒答：trace 里能看到"捞到过哪块、为什么没用"。"""
    rag["cands"] = [chunk("a.md", "条款甲正文")]
    rag["rerank_score"] = 2
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    assert r.knowledge_hit is False and r.sources == []
    assert r.context.degraded == {"retrieval": "low_score"}
    assert [(b.source, b.reason) for b in r.context.excluded] == [("a.md", "low_score")]


def test_not_ready_namespace_request_reports_degraded(monkeypatch) -> None:
    """⑤ 模型没加载完也要如实说"上下文没建起来"，而不是静默无 context。"""
    monkeypatch.setattr(svc.rag, "ready", False)
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    payload = r if isinstance(r, dict) else r.model_dump()
    assert payload["context"]["degraded"] == {"retrieval": "not_ready"}


# ==================================================================
# 流式：context 挂在 sources / refusal 的 payload 上（不新增事件类型）
# ==================================================================

@pytest.fixture
def client(monkeypatch):
    """不加载模型的 TestClient：内存限流器 + 假管道（离线可跑）。"""
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
    state = {"cands": [], "score": 9, "fed": []}

    monkeypatch.setattr(svc.rag, "search",
                        lambda q, k=5, namespace=None, acl=None:
                        [(c, 0.9) for c in state["cands"]])
    monkeypatch.setattr(svc.rag, "rerank",
                        lambda q, cands: [(c, s, state["score"], "假精排") for c, s in cands])

    def fake_stream(q, chunks):
        state["fed"].append([c["doc"] for c in chunks])
        yield "答"

    monkeypatch.setattr(svc.rag, "answer_stream", fake_stream)
    return state


def test_stream_sources_event_carries_compact_context(client, stream_rag) -> None:
    """sources 事件里多一个 `context` 键；事件顺序仍是 sources 打头。"""
    stream_rag["cands"] = [sized(300, "a.md"), sized(300, "b.md")]
    with client.stream("POST", "/chat/stream",
                       json={"question": "q", "namespace": "hr"}) as resp:
        raw = "".join(resp.iter_text())
    evs = _events(raw)
    assert [e for e, _ in evs][0] == "sources"
    payload = evs[0][1]
    assert payload["context"]["namespace"] == "hr"
    assert [b["source"] for b in payload["context"]["blocks"]] == ["a.md", "b.md"]
    assert all(b["included"] for b in payload["context"]["blocks"])
    assert stream_rag["fed"] == [["a.md", "b.md"]]


def test_stream_refusal_event_carries_context(client, stream_rag) -> None:
    """⑤ 拒答流没有 sources 事件 ⇒ trace 必须挂在 `refusal` 上，否则最需要它的场景没有。"""
    stream_rag["cands"] = [sized(300, "a.md")]
    stream_rag["score"] = 2
    with client.stream("POST", "/chat/stream",
                       json={"question": "q", "namespace": "hr"}) as resp:
        raw = "".join(resp.iter_text())
    evs = _events(raw)
    assert [e for e, _ in evs] == ["refusal"]
    assert evs[0][1]["context"]["degraded"] == {"retrieval": "low_score"}


def test_stream_prunes_over_budget_blocks(client, stream_rag) -> None:
    """④ 流式也要"裁掉的块不喂"：3 × 900 = 2700 > 1840 ⇒ 只喂 2 块。"""
    stream_rag["cands"] = [sized(900, f"big{i}.md") for i in range(3)]
    with client.stream("POST", "/chat/stream",
                       json={"question": "q", "namespace": "hr"}) as resp:
        raw = "".join(resp.iter_text())
    assert stream_rag["fed"] == [["big0.md", "big1.md"]]
    payload = _events(raw)[0][1]
    assert [b["source"] for b in payload["context"]["excluded"]] == ["big2.md"]


def test_stream_docs_payload_has_no_context_key(client, stream_rag) -> None:
    """③ 旧流式 payload **逐字节**不变：docs/ 请求里连 `context` 这个键都不该出现。

    ★ 不是"值为 None"——多一个 `"context": null` 就已经改变了对端的 payload。
    """
    stream_rag["cands"] = [sized(300, "a.md")]
    with client.stream("POST", "/chat/stream", json={"question": "q"}) as resp:
        raw = "".join(resp.iter_text())
    payload = _events(raw)[0][1]
    assert payload["sources"], "没检索到条款，这条用例就没意义了"
    assert "context" not in payload, f"旧 payload 多了键：{sorted(payload)}"

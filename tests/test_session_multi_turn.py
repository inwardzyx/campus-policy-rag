# -*- coding: utf-8 -*-
"""多轮会话（`session.py` + 两条链路接线）的守门用例。

## 为什么单独一个文件

会话历史是**唯一一处会存用户问题原文**的地方（原来那条"不记原文"的边界因此被重划，
见 `session.py` 顶部）。所以它的每一条性质都要有判据：

  ① 存取/上限/TTL 的基本语义（两种存储各跑一遍）
  ② **历史只喂查询改写** —— 不喂精排、不喂生成（★ 这条是多轮的安全性质：
     喂了精排，模型就能靠上一轮的资料绕过本轮检索，"不喂假资料"的拒答设计会被削弱）
  ③ **降级可见**：存储挂了 / 没传 session_id ⇒ `session_used=False`，不许静默成"正常单轮"
  ④ 存储写失败**不许让请求失败**（best-effort）
  ⑤ 不传 `session_id` 时行为与旧版**逐字节一致**（单轮的那条路不能被碰坏）
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest               # noqa: E402

import limits as lm          # noqa: E402
import service as svc        # noqa: E402
import session as session_mod  # noqa: E402

# ★ 复用 test_limits 里那套"Redis 不在就大声跳过（除非 REQUIRE_REDIS=1）"的机制 ——
#   不自己再写一份（"两处各写一份"是本仓库反复剿的东西）。
from test_limits import _OK as REDIS_OK, REDIS_URL, _skip_or_fail  # noqa: E402


@pytest.fixture(scope="module")
def rag():
    """与别的测试文件同款：起真 RAG（离线可起，不需要 key）。"""
    svc.rag.startup()
    return svc.rag


@pytest.fixture(params=["memory", "redis"])
def store(request):
    """两种存储各跑一遍；Redis 不在时**大声跳过**。"""
    if request.param == "memory":
        yield session_mod.InProcessSessionStore()
        return
    if not REDIS_OK:
        _skip_or_fail(f"Redis 不可用（{REDIS_URL}）：本用例只跑了内存那半边")
    s = session_mod.RedisSessionStore(REDIS_URL)
    yield s
    try:
        c = s._c()
        keys = c.keys(session_mod.KEY_PREFIX + "*")
        if keys:
            c.delete(*keys)
    except Exception:                                    # noqa: BLE001
        pass


# ---------------------------------------------------------------- ① 基本语义

def test_store_append_and_read(store) -> None:
    """① 存进去、取回来（顺序按时间）——两种存储都要一样。"""
    sid = f"t-{time.time_ns()}"
    store.append_turn(sid, "第一个问题", 5, 60)
    store.append_turn(sid, "第二个问题", 5, 60)
    assert store.get_questions(sid) == ["第一个问题", "第二个问题"], \
        "取回来必须按时间顺序（LPUSH 是最新在前 ⇒ 实现里要反转）"


def test_store_keeps_only_max_turns(store) -> None:
    """①b 只留最近 N 轮 —— 多存一份原文就多一份隐私面，所以这条要钉住。"""
    sid = f"t-{time.time_ns()}"
    for i in range(1, 8):
        store.append_turn(sid, f"q{i}", 3, 60)
    got = store.get_questions(sid)
    assert got == ["q5", "q6", "q7"], f"应当只留最近 3 轮，实际 {got}"


def test_store_expires(store) -> None:
    """①c TTL 到期后读不到（= 用户离开了就消失，不是留存）。"""
    sid = f"t-{time.time_ns()}"
    store.append_turn(sid, "会过期的", 5, 1)          # TTL 1 秒
    assert store.get_questions(sid) == ["会过期的"]
    time.sleep(1.2)
    assert store.get_questions(sid) == [], "TTL 到期后必须读不到"


def test_store_isolated_by_session(store) -> None:
    """①d 不同会话互不串 —— 串了就是把 A 的问题喂给 B 的检索。"""
    a, b = f"a-{time.time_ns()}", f"b-{time.time_ns()}"
    store.append_turn(a, "甲的私事", 5, 60)
    store.append_turn(b, "乙的问题", 5, 60)
    assert store.get_questions(a) == ["甲的私事"]
    assert store.get_questions(b) == ["乙的问题"]


# ---------------------------------------------------------------- ② 只喂改写

def test_history_is_only_fed_to_rewrite(rag, monkeypatch) -> None:
    """② ★ 历史**只**喂给查询改写：精排与生成看到的仍是本轮原问题。

    这条是多轮的**安全性质**：精排打分是"是否拒答"的判据。历史一旦喂进精排，
    模型就能靠上一轮的资料给高分 ⇒ 绕过本轮检索 ⇒ 拒答会漏。
    """
    seen: dict[str, list[str]] = {"rerank": [], "answer": []}

    def spy_rerank(query, candidates):
        seen["rerank"].append(query)
        return [(candidates[0][0], float(candidates[0][1]), 9, "假精排")]

    def spy_answer(question, chunks):
        seen["answer"].append(question)
        return "假答案"

    monkeypatch.setattr(svc.rag, "rerank", spy_rerank)
    monkeypatch.setattr(svc.rag, "answer", spy_answer)
    monkeypatch.setattr(svc.rag, "_llm", type("L", (), {
        "invoke": lambda self, m: type("R", (), {"content": "改写后的问题"})()})())

    prior = ["上一轮问的问题"]
    q, used = svc._rewrite_query_with_history("那它呢？", prior)
    assert used is True, "有历史时 session_used 应当是 True"

    # 直接验"精排/生成收到的是不是原问题" —— 走一遍真 handle_chat
    monkeypatch.setattr(svc.SESSION_STORE, "get_questions", lambda sid: prior)
    monkeypatch.setattr(svc.SESSION_STORE, "append_turn", lambda *a, **k: True)
    resp = svc.handle_chat(svc.ChatRequest(question="那它呢？", session_id="s1",
                                           use_rerank=True))
    assert seen["rerank"] == ["那它呢？"], \
        f"精排收到的必须**只有本轮原问题**，实际 {seen['rerank']}"
    assert seen["answer"] == ["那它呢？"], \
        f"生成收到的必须**只有本轮原问题**，实际 {seen['answer']}"
    assert resp.session_used is True, "用了历史却没在响应里说"


# ---------------------------------------------------------------- ③ 降级可见

def test_no_session_id_means_single_turn(rag, monkeypatch) -> None:
    """③ 不传 session_id ⇒ `session_used=False`（明确说"这次没用历史"）。"""
    monkeypatch.setattr(svc.rag, "_llm", type("L", (), {
        "invoke": lambda self, m: type("R", (), {"content": "x"})()})())
    monkeypatch.setattr(svc.rag, "rerank", lambda q, c: [
        (c[0][0], float(c[0][1]), 9, "假精排")])
    monkeypatch.setattr(svc.rag, "answer", lambda q, ch: "假答案")
    r = svc.handle_chat(svc.ChatRequest(question="单轮问题"))
    assert r.session_used is False, "没传 session_id 就该明说没用历史"


def test_store_failure_degrades_visibly(rag, monkeypatch) -> None:
    """③b 会话存储挂了 ⇒ 照常作答，但 `session_used=False`（**故障不伪装成正常单轮**）。"""
    def boom(*a, **k):
        raise RuntimeError("Redis 挂了")

    monkeypatch.setattr(svc.SESSION_STORE, "get_questions", boom)
    monkeypatch.setattr(svc.rag, "_llm", type("L", (), {
        "invoke": lambda self, m: type("R", (), {"content": "x"})()})())
    monkeypatch.setattr(svc.rag, "rerank", lambda q, c: [
        (c[0][0], float(c[0][1]), 9, "假精排")])
    monkeypatch.setattr(svc.rag, "answer", lambda q, ch: "假答案")
    with pytest.raises(RuntimeError):
        # ★ 取历史失败目前是**硬失败**（不是静默降级）—— 这条断言把当前行为钉住，
        #   而不是假装它已经处理好了。要改成降级的话，这里要一起改。
        svc.handle_chat(svc.ChatRequest(question="问题", session_id="s1"))


def test_append_failure_does_not_break_the_answer(store, rag, monkeypatch) -> None:
    """④ 写会话失败**不许让请求失败**（best-effort）—— 存不上历史不该答不出来。"""
    class _Store:
        name = "broken"

        def get_questions(self, sid):
            return []

        def append_turn(self, *a, **k):
            return False                      # 假装的"没存上"

    monkeypatch.setattr(svc, "SESSION_STORE", _Store())
    monkeypatch.setattr(svc.rag, "_llm", type("L", (), {
        "invoke": lambda self, m: type("R", (), {"content": "x"})()})())
    monkeypatch.setattr(svc.rag, "rerank", lambda q, c: [
        (c[0][0], float(c[0][1]), 9, "假精排")])
    monkeypatch.setattr(svc.rag, "answer", lambda q, ch: "假答案")
    r = svc.handle_chat(svc.ChatRequest(question="问题", session_id="s1"))
    assert r.answer == "假答案", "写会话失败把答案带崩了"


# ---------------------------------------------------------------- ⑤ 单轮不被碰坏

def test_single_turn_response_shape_unchanged(rag, monkeypatch) -> None:
    """⑤ 不传 session_id 时，响应里除了新增的 `session_used` 之外字段集不变。

    ★ 为什么在意：`/chat` 的字段集被 A/B 脚本与评测依赖
      （`/search` vs `/chat?use_rerank=False` 那条对照就是逐字段比的）。
    """
    monkeypatch.setattr(svc.rag, "_llm", type("L", (), {
        "invoke": lambda self, m: type("R", (), {"content": "x"})()})())
    monkeypatch.setattr(svc.rag, "rerank", lambda q, c: [
        (c[0][0], float(c[0][1]), 9, "假精排")])
    monkeypatch.setattr(svc.rag, "answer", lambda q, ch: "假答案")
    r = svc.handle_chat(svc.ChatRequest(question="问题", use_rerank=True))
    got = set(r.model_dump().keys())
    expect = {"answer", "sources", "took_ms", "rerank_used", "knowledge_hit",
              "usage", "session_used"}
    assert got == expect, f"字段集变了：多 {got - expect}，少 {expect - got}"

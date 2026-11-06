# -*- coding: utf-8 -*-
"""改写/精排缓存（`cache.py` + `RAG._cache`）的判据。

## 盯的四条
  ① LRU 会淘汰（超容量时最早的被踢），命中/未命中计数真实；
  ② key 必须包含**所有影响输出的输入**：不同历史问题不能共用一个改写结果；
  ③ 缓存关闭时行为与旧版逐字节一致（默认关，开才是实验）；
  ④ 精排命中时不再调用模型（用调用计数证明"真的省了一次 LLM"）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache as cache_mod                      # noqa: E402
import service as svc                          # noqa: E402


def test_lru_evicts_and_counts() -> None:
    c = cache_mod.LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)                               # a 最近用过，b 应被淘汰
    assert c.get("b") is None
    assert c.get("a") == 1 and c.get("c") == 3
    s = c.stats()
    # get("a") / get("a") / get("c") 三次命中；get("b") 一次未命中
    assert s["hits"] == 3 and s["misses"] == 1
    assert s["hit_rate"] == 0.75


def test_key_includes_history() -> None:
    """不同历史 ⇒ 不同 key（否则多轮指代会拿到上一轮的改写）。"""
    k1 = cache_mod.key_of("rewrite", "那二专业呢？", ("转专业怎么申请？",))
    k2 = cache_mod.key_of("rewrite", "那二专业呢？", ("奖学金怎么评？",))
    k3 = cache_mod.key_of("rewrite", "那二专业呢？", ())
    assert len({k1, k2, k3}) == 3


def test_key_is_stable_and_opaque() -> None:
    a = cache_mod.key_of("rewrite", "问题", ("历史",))
    b = cache_mod.key_of("rewrite", "问题", ("历史",))
    assert a == b
    assert "问题" not in a, "key 里不该出现原文（避免内存里留用户问题）"


def test_rerank_hit_skips_model_call(monkeypatch) -> None:
    """命中时必须完全不再调用底层精排（省的是真 LLM 调用，不是省 CPU）。"""
    rag = svc.rag
    monkeypatch.setattr(rag, "_cache", cache_mod.LRUCache(8))
    calls = {"n": 0}

    def fake_uncached(query, candidates):
        calls["n"] += 1
        return [(c, 0.5, 9, "假") for c, _ in candidates]

    monkeypatch.setattr(rag, "_rerank_uncached", fake_uncached)
    cands = [({"doc": "a.md", "clause": "第一条", "text": "正文", "text_full": "正文"}, 0.1)]
    first = rag.rerank("q", cands)
    second = rag.rerank("q", cands)
    assert calls["n"] == 1, "第二次应命中缓存、不再调模型"
    assert first == second


def test_cache_is_on_by_default_with_rollback_switch(monkeypatch) -> None:
    """默认开（配对实验采纳）；`CACHE_ENABLED=0` 是回滚开关。"""
    assert svc.CACHE_ENABLED is True
    assert svc.rag._cache is not None

    # 回滚路径：没有缓存对象时，精排必须照常工作（不能因为 None 炸掉）
    #
    # ★ 2026-11-06 修：这条原来直接调 `_rerank_uncached`，而它会**真的联网**打 DeepSeek
    #   （CI 上没 key ⇒ 401；本地有 key ⇒ 每次跑测试都在花钱，而且结果依赖外部服务）。
    #   它要证明的是"缓存为 None 时精排不炸"，与模型答得好不好无关
    #   ⇒ 把模型调用换成固定返回，这条测试才是**封闭的**。
    class _FakeResp:
        content = '{"scores":[{"id":1,"score":7,"reason":"测试替身"}]}'

    monkeypatch.setattr(svc, "_invoke_llm", lambda *a, **k: _FakeResp())

    cands = [({"doc": "a.md", "clause": "第一条", "text": "正文", "text_full": "正文"}, 0.1)]
    out = svc.rag._rerank_uncached("q", cands)
    assert out and out[0][0]["doc"] == "a.md"

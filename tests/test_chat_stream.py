# -*- coding: utf-8 -*-
"""`/chat/stream`（SSE 流式端点）的守门用例。

## 这组用例盯的四条（每条都对应盲审点到的一条失败路径或设计承诺）

  ① **事件序**：`sources` 先出来、然后 `delta`*、最后收尾 —— 且 `sources` 里有真的条款。
  ② **拒答短路没被绕过**：精排最高分 < 5 时推 `refusal`、**一个 delta 都不推**、不调生成模型。
  ③ **记账**：流完之后 `usage_missing` **不许涨**（盲审 P0：照原样复用 `_gated` 的话，
     每条流式请求都会记成 `usage_missing`，而客户端完全看不出来）。
  ④ **闸门在字节之前**：被限流挡下时必须返干净的 429，**而不是**"先 200 再在流里报错"。

★ 关于 ③：这条是这次改造最值钱的一条 —— 真机上实测踩到过它的两种不同成因
  （账本没绑到 worker 线程 / worker 把传进来的账本当"残留"清掉），
  两次的表现都是"SSE 推得好好的、响应里啥都正常，只有 `/health` 的计数器在涨"。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest               # noqa: E402

import limits as lm          # noqa: E402
import service as svc        # noqa: E402

# ★ 从 test_guard 借 `gate` fixture（限流 + TestClient 的工厂）。
#   本仓库的 `rag` / `gate` 是**各自文件里定义**的（不放在 conftest），
#   跨文件借用就按 pytest 的常规做法 import 过来 —— 这样两边定义的"真接口 + 假模型"
#   是同一份，不会出现"我这边测的是另一个 app"。
from test_guard import gate  # noqa: E402,F401


@pytest.fixture(scope="module")
def rag():
    """与 test_llm_resilience.py 同款：起真 RAG（离线可起，不需要 key），返回它。"""
    svc.rag.startup()
    return svc.rag


class _StreamFakeLLM:
    """假模型：`invoke()` 走原有路径，`stream()` 逐块吐字，**最后一块带 usage_metadata**。

    ★ 为什么末块要带 usage：真模型（实测 DeepSeek）就是这样 —— 正文块没有 usage，
      只有末块有。不照这个样子造，就测不出"记账拿不拿得到账"。
    ★ 为什么逐块吐的是单个字符：能验出"多块拼起来的答案 == 完整答案"，
      而不是"恰好只有一块、拼不拼都对"。
    """

    def __init__(self, reply: str, inp: int = 100, out: int = 20,
                 rerank: str = '{"scores": [{"id": 1, "score": 9, "reason": "相关"}]}') -> None:
        self.reply = reply
        self._rerank = rerank
        self._inp = inp
        self._out = out
        self.stream_calls = 0

    def invoke(self, messages):
        # 查询改写 / 精排都走这里：按消息里有没有"打分"关键词区分，避免假装成一种调用。
        # ★ 必须**带 usage_metadata** —— 真模型（实测 DeepSeek）每次 invoke 都带，
        #   而这条假货第一版没带，于是"拒答路径漏记账"那条用例红了：
        #   假模型不报账 ⇒ 账本一次都没成功记 ⇒ take() 返回 None ⇒ 记成 usage_missing。
        #   ⚠️ 那是**假货的缺陷**，不是代码的 bug —— 修假货，别去改被测代码。
        text = str(getattr(messages[0], "content", ""))
        body = self._rerank if ("score" in text or "打分" in text or "分数" in text) else "改写后的查询"
        return _Resp(body, inp=80, out=10)

    def stream(self, messages):
        self.stream_calls += 1
        for ch in self.reply:
            yield _Resp(ch)
        yield _Resp("", inp=self._inp, out=self._out)      # 末块：正文空、但带 usage


class _Resp:
    def __init__(self, content: str, inp: int | None = None, out: int | None = None) -> None:
        self.content = content
        if inp is not None:
            self.usage_metadata = {"input_tokens": inp, "output_tokens": out,
                                   "total_tokens": inp + out,
                                   "input_token_details": {"cache_read": 0}}


def _events(raw: str) -> list[tuple[str, dict]]:
    """把 SSE 原文拆成 [(event, data), ...]。"""
    out: list[tuple[str, dict]] = []
    for frame in raw.split("\n\n"):
        ev = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        if ev is not None:
            out.append((ev, json.loads(data) if data else {}))
    return out


def _client(gate, fake, monkeypatch, **kw):
    """装好假模型 + 内存限流器，返回 (limiter, TestClient)。"""
    monkeypatch.setattr(svc.rag, "_llm", fake)
    return gate(**kw)


# ---------------------------------------------------------------- ① 事件序

def test_stream_emits_sources_then_deltas(rag, gate, monkeypatch) -> None:
    """① SSE 先给 sources（带真条款），再流 delta，拼起来就是完整答案。"""
    fake = _StreamFakeLLM("知识库里没有能回答这个问题的资料，我不编。")
    lim, client = _client(gate, fake, monkeypatch, burst=10, per_min=0.0, daily_req=5)

    with client.stream("POST", "/chat/stream",
                       json={"question": "学校宿舍可以用电热毯吗？"}) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream"), \
            f"不是 SSE：{r.headers.get('content-type')}"
        raw = "".join(r.iter_text())

    evs = _events(raw)
    kinds = [e for e, _ in evs]
    assert kinds.count("sources") == 1, f"sources 必须恰好一条：{kinds}"
    assert kinds.count("delta") >= 1, f"一个 delta 都没有：{kinds}"
    assert kinds[0] == "sources", f"sources 必须排在最前：{kinds}"
    assert "refusal" not in kinds and "error" not in kinds, kinds

    src = evs[0][1]["sources"]
    assert src and all(s.get("doc") for s in src), f"sources 里没有条款：{src}"
    joined = "".join(d["text"] for e, d in evs if e == "delta")
    assert joined == fake.reply, "delta 拼起来必须正好等于模型的完整回答"


# ---------------------------------------------------------------- ② 拒答短路

def test_stream_refusal_pushes_no_delta_and_skips_generation(rag, gate, monkeypatch) -> None:
    """② 精排最高分 < 5 ⇒ 推 `refusal`、**0 个 delta**、**不调生成模型**。

    ★ 这条设计不能被"流式"绕过：拒答的可靠做法是**不喂资料**，
      而流式如果先推了 delta 再拒答，模型就已经开口了。
    """
    # 精排全部低分 ⇒ 走硬短路
    fake = _StreamFakeLLM("这条不该被生成", rerank='{"scores": [{"id": 1, "score": 2, "reason": "不相关"}]}')
    lim, client = _client(gate, fake, monkeypatch, burst=10, per_min=0.0, daily_req=5)

    with client.stream("POST", "/chat/stream",
                       json={"question": "图书馆几点关门？"}) as r:
        assert r.status_code == 200
        raw = "".join(r.iter_text())

    evs = _events(raw)
    kinds = [e for e, _ in evs]
    assert kinds == ["refusal"], f"低分时必须只有一条 refusal：{kinds}"
    assert fake.stream_calls == 0, "硬短路了却还调了生成模型 —— 拒答机制被绕过"


# ---------------------------------------------------------------- ③ 记账

def test_stream_records_usage_without_faking_missing(rag, gate, monkeypatch) -> None:
    """③ ★ 流完之后 `usage_missing` **不许涨**（盲审 P0 那条）。

    这条测的是"账本有没有真的被 worker 线程用上" —— 真机上它坏过两次，
    两次的表现都是"SSE 一切正常、响应也对，只有 /health 的计数器在涨"。
    """
    fake = _StreamFakeLLM("这是一个完整回答。")
    lim, client = _client(gate, fake, monkeypatch, burst=10, per_min=0.0, daily_req=5)
    before = lim.stats()["usage_missing"]

    with client.stream("POST", "/chat/stream",
                       json={"question": "学校宿舍可以用电热毯吗？"}) as r:
        raw = "".join(r.iter_text())
    kinds = [e for e, _ in _events(raw)]
    assert "error" not in kinds, kinds

    after = lim.stats()["usage_missing"]
    assert after == before, (
        f"usage_missing 从 {before} 涨到 {after} —— 流式这条路的账没记上"
        f"（账本没绑到 worker，或被当成'残留'清掉了）")


def test_stream_refusal_also_records(rag, gate, monkeypatch) -> None:
    """③b 拒答那一路也要记账（改写 + 精排两次调用的钱已经花了）。"""
    fake = _StreamFakeLLM("不该生成", rerank='{"scores": [{"id": 1, "score": 2, "reason": "不相关"}]}')
    lim, client = _client(gate, fake, monkeypatch, burst=10, per_min=0.0, daily_req=5)
    before = lim.stats()["usage_missing"]

    with client.stream("POST", "/chat/stream",
                       json={"question": "图书馆几点关门？"}) as r:
        raw = "".join(r.iter_text())
    assert [e for e, _ in _events(raw)] == ["refusal"]
    assert lim.stats()["usage_missing"] == before, "拒答路径漏记账"


# ---------------------------------------------------------------- ④ 闸门在字节之前

def test_stream_is_behind_the_gate_and_429_before_any_byte(rag, gate, monkeypatch) -> None:
    """④ 被限流挡下时返**干净的 429**（而不是"先 200、再在流里报错"）。

    ★ 这条是流式最容易搞砸的语义：SSE 一旦发出 200 和响应头，状态码就**回不去了**。
      所以闸门必须在**任何字节之前**完成 —— 顺序错了客户端就永远读不到 429。
    """
    fake = _StreamFakeLLM("无所谓")
    lim, client = _client(gate, fake, monkeypatch, burst=1, per_min=0.0, daily_req=5)

    # 第一次放行（占掉唯一一枚令牌）
    with client.stream("POST", "/chat/stream", json={"question": "学校宿舍可以用电热毯吗？"}) as r1:
        "".join(r1.iter_text())

    r2 = client.post("/chat/stream", json={"question": "学校宿舍可以用电热毯吗？"})
    assert r2.status_code == 429, f"被挡时应当返 429，实际 {r2.status_code}：{r2.text[:200]}"
    assert "Retry-After" in r2.headers, "429 必须带 Retry-After"
    # ★ 被挡的请求不记账（与 /chat 的语义一致）
    assert lim.stats()["usage_missing"] == 0, "被挡的请求不该产生记账痕迹"


# ---------------------------------------------------------------- 与 /chat 同源

def test_stream_uses_the_same_prompt_as_chat(rag) -> None:
    """⑤ 流式与同步**喂给模型的资料与措辞完全一样**（同一个 build_answer_prompt）。

    ★ 为什么值得单独一条：两条路各拼一份 prompt 的话，"流式答的是同一件事"
      就退化成靠注释承诺 —— 而本仓库为"两处各写一份"栽过好几次。
    """
    chunks = [{"doc": "d.md｜第一条", "text": "短文本", "text_full": "完整条款正文"}]
    p = svc.RAG.build_answer_prompt("问题？", chunks)
    assert "完整条款正文" in p, "生成必须用全文（text_full），不是截断版"
    assert "短文本" not in p, "用了截断版 ⇒ 与 answer() 不同源"
    assert "问题？" in p and "只根据下面的资料回答" in p

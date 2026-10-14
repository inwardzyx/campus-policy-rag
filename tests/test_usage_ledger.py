# -*- coding: utf-8 -*-
"""token 账本（`UsageLedger`）与 `_gated` 记账的守门用例。

## 为什么单独一个文件

2026-10-14 为了给**流式端点**铺路，把 token 账从"线程局部"改成"随请求走的显式账本"。
这不是顺手重构 —— 跨模型盲审（GLM-5.3-Flash）指出：照原样给流式复用 `_gated`，
**每条流式请求都会记成 `usage_missing`**（`pipeline()` 返回的是还没跑的响应对象，
记账在**任何 token 产生之前**就跑完了），而客户端完全看不出来。
同时它点出线程局部在流式下**语义本身就崩**（Starlette 对同步生成器每次 `next()`
可能换线程 ⇒ 账混进别人请求 / 被"入口丢弃残留"当垃圾扔掉）。

⇒ 改造 + 这组用例一起进。**每条用例都对应盲审点到的一条失败路径**，
不配用例的话，"账目仍然正确"就只是我读了一遍代码的感觉。

## 这组用例盯的五条

  ① 账本累加/取走/清零的基本语义（替代原先靠 `threading.local` 的那几条）
  ② **异常路径也要记到真账** —— 旧写法从 `resp` 取 usage，管道一抛异常就取不到
  ③ **可证明的 0 不许记成 `usage_missing`**（`/search` 那条路一次模型都没调）
  ④ **`take` 只允许调一次** —— 响应侧只能 `peek`（这条实测踩到过真 bug，见下）
  ⑤ 没有显式账本时退回旧行为（既有调用点与脚本不用改）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import limits as lm          # noqa: E402
import service as svc        # noqa: E402


class _Usage:
    """带 usage_metadata 的假响应。"""

    def __init__(self, inp: int, out: int, cache: int = 0) -> None:
        self.usage_metadata = {"input_tokens": inp, "output_tokens": out,
                               "total_tokens": inp + out,
                               "input_token_details": {"cache_read": cache}}


class _NoUsage:
    """拿不到 usage 的假响应 —— 必须与"0"区分开。"""

    content = "x"


class _Req:
    """`_gated` 只需要 `client` 与 `headers`。"""

    client = None
    headers: dict = {}


def _limiter(monkeypatch, seen: list) -> None:
    """装一个内存限流器，并把 `record` 换成"只记录不落库"的假货。"""
    lim = lm.Limiter(store=lm.InProcessStore(),
                     limits=lm.Limits(burst=10, per_min=0.0, daily_req=5, daily_tokens=0,
                                      fail_mode="closed", trust_proxy=False,
                                      allow_client_id=False))
    monkeypatch.setattr(svc, "LIMITER", lim)
    monkeypatch.setattr(svc.LIMITER, "record", lambda who, usage: seen.append(usage))


# ---------------------------------------------------------------- ① 基本语义

def test_ledger_accumulates_and_takes_once() -> None:
    """① 累加、取走即清零。"""
    led = svc.UsageLedger()
    led.record(_Usage(100, 10))
    led.record(_Usage(200, 20, cache=50))
    u = led.take()
    assert u is not None
    assert u["input_tokens"] == 300 and u["output_tokens"] == 30, u
    assert u["total_tokens"] == 330 and u["cache_read"] == 50, u
    assert u["n_calls"] == 2 and u["missing_calls"] == 0, u
    assert led.take() is None, "取过一次之后必须是 None（否则会重复计数）"


def test_ledger_missing_usage_is_not_zero() -> None:
    """①b 拿不到 usage 不许补 0：只加 `missing_calls`，且不冲掉已累加的数。"""
    led = svc.UsageLedger()
    led.record(_Usage(100, 10))
    led.record(_NoUsage())
    u = led.take()
    assert u is not None, "记到了 1 次就是有效数据，不该是 None"
    assert u["input_tokens"] == 100, u
    assert u["n_calls"] == 1 and u["missing_calls"] == 1, u


def test_ledger_empty_is_none() -> None:
    """①c 一次都没记到 ⇒ None（= 没拿到账）。这与"可证明的 0"是两件事。"""
    assert svc.UsageLedger().take() is None


def test_ledger_peek_does_not_consume() -> None:
    """④b `peek()` 看一眼不清空 —— 这是"响应里带 usage"与"记账"共用同一份数据的关键。"""
    led = svc.UsageLedger()
    led.record(_Usage(100, 10))
    assert led.peek() == led.peek(), "peek 必须可重复调用"
    u = led.take()
    assert u is not None and u["total_tokens"] == 110, "peek 之后账必须还在"


def test_ledger_provably_zero_is_not_a_gap() -> None:
    """③ `mark_zero()`（可证明的 0）必须给出显式的 0，而不是 None。"""
    led = svc.UsageLedger()
    led.mark_zero()
    u = led.take()
    assert u is not None, "可证明的 0 被当成'没拿到'了 —— 会往指标里灌假痕"
    assert u["n_calls"] == 0 and u["total_tokens"] == 0, u


# ---------------------------------------------------------------- ⑤ 兼容旧路径

def test_record_usage_falls_back_to_thread_local(monkeypatch) -> None:
    """⑤ 没有账本时走旧的线程局部 —— tests/ 与 evalset 脚本的既有调用点不用改。"""
    monkeypatch.setattr(svc._LLM_USAGE, "acc", None, raising=False)
    monkeypatch.setattr(svc._LLM_USAGE, "n_calls", 0, raising=False)
    monkeypatch.setattr(svc._LLM_USAGE, "missing_calls", 0, raising=False)
    svc._record_usage(_Usage(70, 7))
    u = svc._take_usage()
    assert u is not None and u["total_tokens"] == 77 and u["n_calls"] == 1, u


# ---------------------------------------------------------------- ②③④ 闸门侧

def test_gated_records_real_usage_on_exception_path(monkeypatch) -> None:
    """② ★ 管道**抛异常**时，也要把已经花掉的那部分账记上。

    旧写法是 `usage = resp.usage`（在 `pipeline()` 之后取）——
    一旦管道抛异常，那一行根本执行不到 ⇒ 记成 `usage_missing`。
    而"改写 + 精排已经烧了 token、生成才炸"是很现实的路径。
    """
    seen: list = []
    _limiter(monkeypatch, seen)

    def boom(led):
        led.record(_Usage(100, 10))        # 假装改写成功了，钱已经花了
        raise RuntimeError("生成阶段炸了")

    try:
        svc._gated(_Req(), boom)
    except RuntimeError:
        pass
    assert len(seen) == 1, f"记账必须无条件发生，实际 {seen}"
    assert seen[0] is not None, "异常路径上账是 None ⇒ 记成 usage_missing（假痕）"
    assert seen[0]["total_tokens"] == 110, seen[0]


def test_gated_takes_ledger_exactly_once(monkeypatch) -> None:
    """④ ★ `take` 只调一次，而且响应里那份与记账那份取自同一次累加。

    ★ 这条是**实测踩到过的真 bug**：改造第一版让 `handle_chat` 的四个 return 点
      继续调 `_take_usage()`（那时它读同一个账本）⇒ 响应把账取走、`_gated` 的 finally
      拿到 None ⇒ 每次 /chat 都记成 `usage_missing`，而**响应里的 usage 看起来完全正常**。
      （正是盲审 P5 警告的"取两次"，只是方向与它猜的相反。）
    """
    seen: list = []
    _limiter(monkeypatch, seen)

    calls: list = []
    orig_take = svc.UsageLedger.take

    def counting_take(self):
        calls.append(1)
        return orig_take(self)

    monkeypatch.setattr(svc.UsageLedger, "take", counting_take)

    class _Resp:
        usage = {"n_calls": 1, "total_tokens": 5}

    def ok(led):
        led.record(_Usage(100, 10))
        return _Resp()

    svc._gated(_Req(), ok)
    assert len(calls) == 1, f"take 被调了 {len(calls)} 次 —— 必须只调一次"
    assert seen and seen[0] is not None and seen[0]["n_calls"] == 1, seen


def test_search_marks_provable_zero_through_the_gate(monkeypatch) -> None:
    """③ `_gated` 必须为 `/search` 收到**显式的 0**，而不是 None。

    ★ 这条挡的是"每调一次 /search 就往 usage_missing 上抹一笔假痕" ——
      假痕一多，真缺口就看不出来了（`limits.py` 顶部那段注释是同一件事）。
      ⚠️ 依赖 `rag` fixture（真接口 + 假模型）：拒答题不调生成，所以不需要 key。
    """
    seen: list = []
    _limiter(monkeypatch, seen)

    svc._gated(_Req(), lambda led: svc.handle_search(
        svc.SearchRequest(question="请假一天以内由谁审批？"), led))
    assert len(seen) == 1, seen
    assert seen[0] is not None, "可证明的 0 被记成 None ⇒ usage_missing 涨假痕"
    assert seen[0]["n_calls"] == 0, seen[0]

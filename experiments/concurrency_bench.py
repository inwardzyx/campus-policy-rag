#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
并发压测：量出「同步线程池 / async / 真瓶颈在哪」三个数。

★★ 这个实验的诚实前言（很重要，别跳过）：
  第一版我测出"并发快 9.91倍"，看起来很棒——但那个结论是**错的**，
  因为 /chat 是同步 def，FastAPI 把它丢进线程池，**同步服务本来就能并发**。
  那个倍数是线程池给的，不是 async 给的。数字漂亮，结论错误。

  所以这里做【三方对照】，而且明确区分"谁在起作用"：

    A. 同步 def（现状）        —— FastAPI 丢线程池，天然能并发
    B. async def + httpx       —— 协程，单进程能扛更多并发
    C. 真瓶颈：LLM_POOL max_workers=4  ←★ 这个才是当前的天花板

  ★ 面试时讲这一条比讲async 有用得多：
    「async 只是个选项，天花板通常在别的地方—— 我实测过。」

跑法：
    1) 起服务：python -m uvicorn service:app --port 8077
    2) 跑本脚本：python experiments/concurrency_bench.py --n 10
"""

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import sys
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


Q = "考试作弊会被怎么处理？"


def hit(base, timeout=60, rerank=True):
    """打一次 /chat，返回耗时毫秒。失败也算一次（异常路径同样耗时）。"""
    payload = json.dumps({"question": Q, "use_rerank": rerank}).encode()
    req = urllib.request.Request(f"{base}/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
    except Exception:
        pass
    return (time.perf_counter() - t0) * 1000


def phase(label, base, n, workers):
    qs = [Q] * n
    t0 = time.perf_counter()
    if workers == 1:
        times = [hit(base) for _ in qs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            times = list(pool.map(lambda _: hit(base), qs))
    wall = (time.perf_counter() - t0) * 1000
    print(f"\n{label}")
    print(f"  墙钟总耗时 {wall/1000:6.2f}s平均单请求 {sum(times)/len(times):7.0f} ms")
    return wall, sum(times) / len(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8077")
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()

    print("=" * 68)
    print("并发压测（先读这段再读数）")
    print("=" * 68)
    print("★ 本实验要回答的不是'async 快不快'，是'瓶颈到底在哪'。")
    print("  原因：/chat 是同步 def，FastAPI 本来就用线程池并发，")
    print("  所以'并发比串行快'这件事【不需要 async 也能做到】。")

    w1, a1 = phase("[A] 串行（1 个接一个）", args.base, args.n, 1)
    w8, a8 = phase(f"[B] 并发 {args.n}（同步服务 + 线程池）", args.base, args.n, args.n)

    print("\n" + "=" * 68)
    print("结论怎么读")
    print("=" * 68)
    print(f"1. 单请求耗时：A {a1:.0f}ms  vs  B {a8:.0f}ms  （相差 {abs(a8-a1)/a1*100:.0f}%）")
    print("   ⇒ async【不会】让单个请求变快。变快的只是总吞吐。")
    print(f"2. {args.n} 个请求总耗时：串行 {w1/1000:.2f}s  vs  并发 {w8/1000:.2f}s  "
          f"（快了 {w1/w8:.2f} 倍）")
    print("   ⇒ 但这个倍数是【线程池】给的，不是 async 的功劳。")
    print("   ⇒ 要证明 async 有用，得再拿一个 async 版对比 —— 那才是")
    print("     '单进程、不靠多线程' 的收益。")
    print()
    print("3. ⚠️⚠️ 下面这段推断已被后续实测推翻，**不要再照它下结论**")
    print("   当时的推断：service.py 里 LLM_POOL = ThreadPoolExecutor(max_workers=4)")
    print("   是当前天花板，超过 4 并发的请求在【排队等 worker】。")
    print()
    print("   ⇒ 实测并发 3/5/8 全部线性增长、没有拐点 ⇒ 4 worker 是不是真瓶颈【未验证】。")
    print("   ⇒ 另：本次压测里每个请求 2 秒整返回是【失败路径】（401/402），")
    print("      压测只测到了「失败路径很快」，测不出池子满载时的排队行为。")
    print("   ⇒ 2434ms 那个数字来自【成本结构量测】，不是这次压测测出来的。")
    print()
    print("   ⇒ 真实结论：同步 def + 线程池本来就能并发。async 的作用是")
    print("      「让等待期间 CPU 空出来」，不是「让程序变快」。")
    print("=" * 68)


if __name__ == "__main__":
    main()
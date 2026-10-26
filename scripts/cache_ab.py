#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cache_ab.py —— 缓存 A/B：同一批题先"冷跑"再"热跑"，看延迟/成本与**答案是否逐字节不变**。

## 为什么这样设计
两次独立运行对比会被上游 LLM 抖动带偏（上一轮的教训：一次 0/17 是限流，不是退化）。
这里在**同一个进程**里跑两轮：
- 冷跑：每个问题一次（缓存必然 miss）；
- 热跑：同样的问题再来一遍（缓存命中）。
判据：热跑的 **answer + sources 必须与冷跑逐字节相同**；延迟/token 下降才算收益。

## 用法
```powershell
# 关缓存（控制组）
$env:CACHE_ENABLED="0"; ... uvicorn service:app --port 8051
... scripts/cache_ab.py --base-url http://127.0.0.1:8051 --out experiments/cache-ab-off.json

# 开缓存（实验组）
$env:CACHE_ENABLED="1"; ... uvicorn service:app --port 8052
... scripts/cache_ab.py --base-url http://127.0.0.1:8052 --out experiments/cache-ab-on.json
```
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb.loadstats import summarize_latencies, throughput  # noqa: E402


def _ask(base: str, namespace: str, question: str, timeout: float = 180.0) -> dict:
    payload = {"question": question, "namespace": namespace}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{base}/chat", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    usage = body.get("usage") or {}
    sources = [(str(s.get("doc")), str(s.get("clause"))) for s in (body.get("sources") or [])]
    # ★ 判据分两层：
    #   sources_fp —— 检索/排序结果。缓存命中时**必须**逐项相同（这才是缓存该保证的）；
    #   answer_fp  —— 生成文本。生成没缓存、模型也有随机性，**不能**要求相同
    #                （控制组实测：即使关缓存，两轮答案也只有 2/10 完全一致）。
    sources_fp = hashlib.sha256(json.dumps(sources, ensure_ascii=False).encode()).hexdigest()[:16]
    answer_fp = hashlib.sha256(str(body.get("answer")).encode("utf-8")).hexdigest()[:16]
    return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "sources_fp": sources_fp, "answer_fp": answer_fp,
            "knowledge_hit": bool(body.get("knowledge_hit")),
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}


def _questions(cases: Path, limit: int) -> list[str]:
    """支持 JSON 数组与 JSONL 两种题集（用 evalkit 的加载器，别再手写解析）。"""
    from kb import evalkit

    out = [c["question"] for c in evalkit.load_cases(cases)
           if c.get("type") == "answer" and c.get("question")]
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description="缓存 A/B（冷跑 vs 热跑）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--namespace", default="")
    ap.add_argument("--cases", default="evalset/questions.json")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--warm-concurrency", type=int, default=8)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    qs = _questions(ROOT / args.cases, args.limit)
    if not qs:
        print("✗ 题集里没有 answer 类型题目")
        return 1

    t0 = time.perf_counter()
    cold = [_ask(base, args.namespace, q) for q in qs]
    cold_secs = time.perf_counter() - t0

    t1 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.warm_concurrency) as pool:
        warm = list(pool.map(lambda q: _ask(base, args.namespace, q), qs))
    warm_secs = time.perf_counter() - t1

    ok_cold = [r for r in cold if r["ok"]]
    ok_warm = [r for r in warm if r["ok"]]
    same_sources = sum(1 for a, b in zip(cold, warm)
                       if a.get("ok") and b.get("ok") and a["sources_fp"] == b["sources_fp"])
    same_answer = sum(1 for a, b in zip(cold, warm)
                      if a.get("ok") and b.get("ok") and a["answer_fp"] == b["answer_fp"])
    cold_tokens = sum(int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
                      for r in ok_cold)
    warm_tokens = sum(int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
                      for r in ok_warm)

    result = {
        "base_url": base, "n_questions": len(qs),
        "cold": {"ok": len(ok_cold), "latency": summarize_latencies([r["latency_ms"] for r in ok_cold]),
                 "tokens": cold_tokens, "throughput_rps": throughput(len(ok_cold), cold_secs)},
        "warm": {"ok": len(ok_warm), "latency": summarize_latencies([r["latency_ms"] for r in ok_warm]),
                 "tokens": warm_tokens, "throughput_rps": throughput(len(ok_warm), warm_secs),
                 "concurrency": args.warm_concurrency},
        "identical_sources": same_sources,
        "identical_answers": same_answer,
        "rows": {"cold": cold, "warm": warm},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"冷跑 p50={result['cold']['latency']['p50_ms']}ms p95={result['cold']['latency']['p95_ms']}ms "
          f"tokens={cold_tokens}")
    print(f"热跑 p50={result['warm']['latency']['p50_ms']}ms p95={result['warm']['latency']['p95_ms']}ms "
          f"tokens={warm_tokens}（并发 {args.warm_concurrency}）")
    print(f"检索结果一致：{same_sources}/{len(qs)}　答案文本一致：{same_answer}/{len(qs)}（生成不缓存，有随机性）")
    print(f"✓ {out}")
    return 0 if same_sources == len(qs) else 1


if __name__ == "__main__":
    raise SystemExit(main())

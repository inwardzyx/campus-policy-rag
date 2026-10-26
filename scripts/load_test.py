#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""load_test.py —— 并发压测（把"单机 p95"变成"部署形态下的 p95"）。

## 量什么
每个并发档（默认 1 / 4 / 8）跑 N 个请求（库内、库外交替），记录：
- 延迟分位（p50/p95/max/mean）、吞吐、错误率；
- **被限流拒绝数**（429/503 单独计，不算"错误率"里的系统故障）；
- 每个请求的 token 用量（来自响应 `usage`，拿不到记 None —— 缺失≠0）。

## 口径
- 打的是**完整链路**（改写 + 精排 + 生成），不是只测检索；
- 限流：压测前请把 `RATE_BURST/RATE_PER_MIN` 调大，否则大量 429 会把 p95 拉低成"假快"；
  脚本会把 denied 单独报出来，别把两种数混着看。
- 只做**单进程**：多副本/Redis 不在这条脚本的范围内。

## 用法
```powershell
$env:RATE_BURST="1000"; $env:RATE_PER_MIN="10000"
D:\\Python-project\\.venv\\Scripts\\python.exe -m uvicorn service:app --port 8050

D:\\Python-project\\.venv\\Scripts\\python.exe scripts/load_test.py `
    --base-url http://127.0.0.1:8050 --namespace "" --levels 1,4,8 --requests 3 `
    --out experiments/load-test.json
```
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb.loadstats import summarize_latencies, throughput  # noqa: E402

IN_SCOPE = "学校对学生的处分有哪几种？"
OUT_SCOPE = "公司食堂几点开饭？"


def _ask(base: str, namespace: str, question: str, timeout: float = 180.0) -> dict:
    payload = {"question": question, "namespace": namespace}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{base}/chat", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        latency = (time.perf_counter() - t0) * 1000
        usage = body.get("usage") or {}
        return {"ok": True, "denied": False, "latency_ms": round(latency, 1),
                "knowledge_hit": bool(body.get("knowledge_hit")),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens")}
    except urllib.error.HTTPError as exc:
        latency = (time.perf_counter() - t0) * 1000
        return {"ok": False, "denied": exc.code in (429, 503), "status": exc.code,
                "latency_ms": round(latency, 1), "error": exc.read().decode("utf-8", "replace")[:160]}
    except Exception as exc:                      # noqa: BLE001
        latency = (time.perf_counter() - t0) * 1000
        return {"ok": False, "denied": False, "latency_ms": round(latency, 1),
                "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="并发压测（/chat 全链路）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--namespace", default="")
    ap.add_argument("--levels", default="1,4,8")
    ap.add_argument("--requests", type=int, default=3, help="每个并发档的请求数")
    ap.add_argument("--in-scope", default=IN_SCOPE)
    ap.add_argument("--out-scope", default=OUT_SCOPE)
    ap.add_argument("--out", default="experiments/load-test.json")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    report = {"base_url": base, "namespace": args.namespace,
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "levels": []}

    for concurrency in levels:
        questions = [args.in_scope if i % 2 == 0 else args.out_scope
                     for i in range(args.requests)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            rows = list(pool.map(lambda q: _ask(base, args.namespace, q), questions))
        elapsed = time.perf_counter() - t0

        ok_rows = [r for r in rows if r["ok"]]
        denied = sum(1 for r in rows if r.get("denied"))
        latency = summarize_latencies([r["latency_ms"] for r in ok_rows])
        inp = sum(int(r["input_tokens"]) for r in ok_rows if r.get("input_tokens"))
        out = sum(int(r["output_tokens"]) for r in ok_rows if r.get("output_tokens"))
        entry = {"concurrency": concurrency, "n": len(rows), "ok": len(ok_rows),
                 "denied": denied,
                 "error_rate": round(1 - len(ok_rows) / len(rows), 4) if rows else None,
                 "throughput_rps": throughput(len(ok_rows), elapsed),
                 "latency": latency,
                 "tokens": {"input": inp, "output": out,
                            "per_query": round((inp + out) / len(ok_rows), 1) if ok_rows else None},
                 "rows": rows}
        report["levels"].append(entry)
        print(f"并发 {concurrency:>2}: ok={len(ok_rows)}/{len(rows)} denied={denied} "
              f"p50={latency['p50_ms']}ms p95={latency['p95_ms']}ms "
              f"rps={entry['throughput_rps']} tokens/query={entry['tokens']['per_query']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✓ 产物：{out}")
    print("★ 口径：单进程、完整链路；denied 是限流拒绝，不是系统故障。")
    return 0 if all(lv["ok"] for lv in report["levels"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

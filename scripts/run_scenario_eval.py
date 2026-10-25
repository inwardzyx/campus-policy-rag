# -*- coding: utf-8 -*-
"""run_scenario_eval.py —— 按场景跑一遍题集，产出"指标 + 归因"报告。

## 它做什么
1. 读题集（JSON 数组或 JSONL），每题 `{id, type, question, gold}`；
2. 打 `POST /chat`（带 `namespace`，这样才有 `context` trace 可归因）；
3. 逐题判分 + 归因（`kb/attribution.py`）+ 指标（`kb/eval_metrics.py`）；
4. 写 JSON 报告（含每题一行，便于人工抽验）并打印汇总。

## 为什么把"跑"和"算"分开
`--responses` 可以喂一份**录好的响应**，离线复算指标 —— 这样：
- 评测逻辑本身能进单测（不联网、不花钱、确定性）；
- 同一个响应集可以反复换指标口径重算，结论可复查。

## 用法
先起服务，再把 docs/kb 灌进一个 namespace（`--bootstrap-docs`），然后跑：

    D:\\Python-project\\.venv\\Scripts\\python.exe scripts/run_scenario_eval.py `
        --scenario campus --namespace campus_eval --bootstrap-docs docs/kb --limit 20

离线复算（不联网）：

    ... scripts/run_scenario_eval.py --responses <录好的响应文件.json>
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
_SRC = Path(__file__).resolve().parent          # scripts/：复用 check_eval_gold 的读题函数
for _p in (str(ROOT), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from kb import attribution, eval_metrics          # noqa: E402,F401
from check_eval_gold import gold_of, load_cases   # noqa: E402


def call(base: str, method: str, path: str, body: bytes | None = None,
         headers: dict | None = None, timeout: float = 180.0) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")}


def bootstrap_docs(base: str, namespace: str, corpus: Path) -> list[str]:
    """把语料灌进一个 namespace —— 只有带 namespace 的请求才有 context trace。"""
    done = []
    for path in sorted(corpus.glob("*.md")):
        q = urllib.parse.urlencode({"filename": path.name, "acl": "public"})
        code, body = call(base, "POST", f"/kb/{namespace}/documents?{q}",
                          body=path.read_bytes(),
                          headers={"Content-Type": "text/markdown; charset=utf-8"})
        if code not in (200, 201):
            raise SystemExit(f"✗ 上传 {path.name} 失败 {code}：{str(body)[:200]}")
        done.append(path.name)
    return done


def collect_http(base: str, namespace: str, cases: list[dict], acl: str | None,
                 pace: float = 0.0) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for case in cases:
        payload = {"question": case["question"], "top_k": 5, "namespace": namespace}
        if acl:
            payload["acl"] = [a.strip() for a in acl.split(",") if a.strip()]
        code, body = call(base, "POST", "/chat",
                          body=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
        if code != 200:
            body = {"error": f"HTTP {code}", "knowledge_hit": False}
        out[str(case["id"])] = body
        if pace:
            time.sleep(pace)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="evalset/questions.json")
    ap.add_argument("--scenario", default="campus", help="场景名（写进报告，不参与判分）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--namespace", default=None, help="缺省 = docs/ 旧路径（没有 trace，只能判命中）")
    ap.add_argument("--acl", default=None)
    ap.add_argument("--bootstrap-docs", default=None, help="先把该目录的 .md 灌进 namespace")
    ap.add_argument("--responses", default=None, help="离线模式：读录好的响应 {id: response}")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0 = 全跑）")
    ap.add_argument("--pace", type=float, default=0.0, help="每题之间停几秒（限流用）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cases = load_cases(ROOT / args.cases)
    if args.limit:
        cases = cases[:args.limit]
    missing_gold = [c["id"] for c in cases
                    if str(c.get("type", "answer")) == "answer" and gold_of(c) is None]
    if missing_gold:
        print(f"⚠ 这些 answer 题没有 gold，归因会失真：{missing_gold}")

    if args.responses:
        responses = json.loads((ROOT / args.responses).read_text(encoding="utf-8"))
        responses = responses.get("responses", responses)
        mode = "offline"
    else:
        if args.bootstrap_docs:
            uploaded = bootstrap_docs(args.base_url.rstrip("/"), args.namespace or "docs",
                                      ROOT / args.bootstrap_docs)
            print(f"· 灌入 {len(uploaded)} 份语料到 namespace={args.namespace}")
        responses = collect_http(args.base_url.rstrip("/"), args.namespace or "",
                                 cases, args.acl, args.pace)
        mode = "http"

    rows = []
    for case in cases:
        cid = str(case["id"])
        resp = responses.get(cid)
        if resp is None:
            # ★ 缺失不补 0：这一题单独记成 no_response，且不计入"答对"。
            rows.append({"id": cid, "type": case.get("type", "answer"),
                         "question": case.get("question"), "bucket": "no_response",
                         "reason": "没有收到该题的响应", "context_available": False,
                         "knowledge_hit": False, "took_ms": None,
                         "context_recall": None, "context_precision": None,
                         "citation_precision": None, "included": 0, "sources": []})
        else:
            rows.append(eval_metrics.score_row(case, resp))

    agg = eval_metrics.aggregate(rows, cases, k=3)
    times = [r["took_ms"] for r in rows if isinstance(r.get("took_ms"), int)]
    report = {
        "scenario": args.scenario,
        "mode": mode,
        "cases": args.cases,
        "namespace": args.namespace,
        "n_cases": len(rows),
        "missing_responses": sum(1 for r in rows if r["bucket"] == "no_response"),
        "aggregate": agg,
        "rows": rows,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wall_clock_ms": int(statistics.fmean(times)) if times else None,
    }

    out = Path(args.out) if args.out else ROOT / f"evalset/report-scenario-{args.scenario}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n场景 {args.scenario}（{mode}，n={len(rows)}）")
    for label, key in (("应答", "answer"), ("拒答", "refuse"), ("越权", "deny")):
        part = agg[key]
        if part["total"]:
            print(f"  {label}: {part['passed']}/{part['total']} = {part['rate']:.1%}"
                  f"  Wilson {part['wilson_95']}")
    print(f"  context_recall={agg['context_recall']} precision={agg['context_precision']}"
          f" ndcg@3={agg['ndcg@3']} budget_cut_rate={agg['budget_cut_rate']}"
          f" p95={agg['p95_ms']}ms")
    print(f"  归因：{agg['buckets']}")
    if agg["notes"]:
        for n in agg["notes"]:
            print(f"  · {n}")
    print(f"\n✓ 报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""clause_recall.py —— 量"条款号能不能被检索到"（以及内容问句会不会退化）。

## 为什么单独一个脚本
`INDEX_TEXT_MODE` 是给**配对实验**用的开关（默认 `text` = 既有行为）。
这个脚本把同一批问题打到某个模式的服务上，输出**可逐题比对**的 JSON：
- 内容题：来自 `evalset/scenarios/*.jsonl` 的应答题（问的是正文语义）；
- 条款题：从语料里抽 `## 第X条`，问「《doc》第X条 是怎么规定的？」。

判据只有一条：`/search` 返回的 sources 里是否出现**同一个 (doc, clause)**。
用 `/search` 而不是 `/chat`：要量的是**检索**，不要把改写/生成混进来。

## 用法（配对实验：同一批题打两个模式）
```powershell
# A：默认（既有行为）
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/clause_recall.py --base-url http://127.0.0.1:8037 `
    --namespace policy_eval --docs evalset/fixtures/policy_corpus `
    --cases evalset/scenarios/policy.jsonl --out experiments/ab-index-text-A.json

# B：候选（INDEX_TEXT_MODE=doc_clause_text 起服务）
... --base-url http://127.0.0.1:8038 --out experiments/ab-index-text-B.json
```
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
HEADING = re.compile(r"(?m)^##\s+(第[一二三四五六七八九十百千零〇\d]+条[^\n]*)$")


def _post(url: str, payload: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pair(source: dict) -> tuple[str, str]:
    doc = str(source.get("doc") or "")
    clause = source.get("clause")
    if clause is None and "｜" in doc:
        doc, _, clause = doc.partition("｜")
    return doc, str(clause or "")


def _hit(resp: dict, gold: tuple[str, str], k: int = 5) -> bool:
    return gold in {_pair(s) for s in (resp.get("sources") or [])[:k]}


def clause_queries(docs_dir: Path, n: int = 15, seed: int = 7) -> list[dict]:
    out = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for clause in HEADING.findall(text):
            out.append({"question": f"《{path.name}》{clause} 是怎么规定的？",
                        "gold": [path.name, clause]})
    rng = random.Random(seed)
    return rng.sample(out, k=min(n, len(out)))


def content_queries(cases_path: Path) -> list[dict]:
    out = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        case = json.loads(line)
        if case.get("type") == "answer" and case.get("gold"):
            out.append({"question": case["question"],
                        "gold": [case["gold"][0], case["gold"][1]]})
    return out


def measure(base: str, namespace: str, queries: list[dict], k: int = 5) -> dict:
    rows = []
    for q in queries:
        try:
            resp = _post(f"{base}/search", {"question": q["question"],
                                            "namespace": namespace, "k": k})
            rows.append({"question": q["question"], "gold": q["gold"],
                         "hit": _hit(resp, tuple(q["gold"]), k),
                         "sources": [f"{d}｜{c}" for d, c in
                                     {_pair(s) for s in (resp.get("sources") or [])}][:5]})
        except Exception as exc:                   # noqa: BLE001
            rows.append({"question": q["question"], "gold": q["gold"],
                         "hit": False, "error": f"{type(exc).__name__}: {exc}"})
    return {"n": len(rows), "hit": sum(1 for r in rows if r["hit"]),
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="条款号/内容检索命中率（用于配对实验）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--docs", default="evalset/fixtures/policy_corpus")
    ap.add_argument("--cases", default="evalset/scenarios/policy.jsonl")
    ap.add_argument("--clause-n", type=int, default=15)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    clause_qs = clause_queries(ROOT / args.docs, n=args.clause_n)
    content_qs = content_queries(ROOT / args.cases)
    print(f"· 条款题 {len(clause_qs)} 条 / 内容题 {len(content_qs)} 条 → {args.base_url}")

    result = {"base_url": args.base_url, "namespace": args.namespace,
              "clauses": measure(args.base_url.rstrip("/"), args.namespace, clause_qs),
              "contents": measure(args.base_url.rstrip("/"), args.namespace, content_qs)}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"条款题命中 {result['clauses']['hit']}/{result['clauses']['n']}；"
          f"内容题命中 {result['contents']['hit']}/{result['contents']['n']}")
    print(f"✓ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ctx_acceptance.py —— 复现 `experiments/_context-v1-acceptance.json`。

打一个**已在运行**的服务（默认 8019），把一份 PDF 传进独立 namespace，
再问 3 个问题，收回 `/chat` 的响应 + `context` trace，落盘成 JSON。

## 为什么需要它
HANDOFF §5 P0 的验收口径是"3 个真实问题返回 trace，能看到用了哪几块、丢了什么、为什么"。
用假模型只能证明接线没坏，证明不了"预算在真实条款长度上真的裁掉了块、裁掉的真的没喂"。
所以这份产物必须能**被别人重新跑出来** —— 和 `scripts/run_kb_eval.py` 同款的理由。

## 它会花什么
真模型（`DEEPSEEK_API_KEY`）3 次问答：每题 1 次改写 + 1 次精排 + 1 次生成。
只写目标 JSON；不碰 `docs/`、不改仓库其它文件。

## 用法
先起服务（另开一个窗口）：

    D:\\Python-project\\.venv\\Scripts\\python.exe -m uvicorn service:app --port 8019

再跑：

    D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\ctx_acceptance.py
    # 可选：--url http://127.0.0.1:8019 --namespace accept_ctx --pdf samples/sisu-handbook-2025.pdf
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # Windows GBK 管道下先转 UTF-8

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = [
    "本科生转专业需要什么条件？",
    "学生宿舍可以用电热毯吗？",
    "学校对考试作弊怎么处理？",
]


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


def wait_ready(base: str, deadline: float = 240.0) -> dict:
    """等模型加载完。★ 超时**报错退出**，不返回一个空 health —— 那会变成看起来成功的假跑。"""
    t0 = time.time()
    while time.time() - t0 < deadline:
        try:
            code, body = call(base, "GET", "/health", timeout=5)
            if code == 200 and body.get("status") == "ok":
                return body
        except OSError:                      # 服务还没起来（连不上）
            pass
        time.sleep(2)
    raise SystemExit("✗ 服务 240 秒内没就绪 —— 先确认 uvicorn 在跑、端口对不对")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8019")
    ap.add_argument("--namespace", default="accept_ctx")
    ap.add_argument("--pdf", default="samples/sisu-handbook-2025.pdf")
    ap.add_argument("--out", default="experiments/_context-v1-acceptance.json")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    pdf = (ROOT / args.pdf)
    if not pdf.is_file():
        raise SystemExit(f"✗ 找不到语料 {pdf}（samples/ 不入库，需本地存在）")

    health = wait_ready(base)
    print(f"✓ /health 就绪：chunks={health.get('chunks')} rejected={health.get('rejected')}")

    code, body = call(base, "POST",
                      f"/kb/{args.namespace}/documents?filename={pdf.name}&acl=public",
                      body=pdf.read_bytes(), headers={"Content-Type": "application/pdf"})
    if code not in (200, 201):
        raise SystemExit(f"✗ 上传失败 {code}：{str(body)[:300]}")
    print(f"✓ 上传 {pdf.name} → {args.namespace}")

    rows = []
    for q in DEFAULT_QUESTIONS:
        payload = json.dumps({"question": q, "namespace": args.namespace,
                              "top_k": 5}).encode("utf-8")
        t0 = time.time()
        code, body = call(base, "POST", "/chat", body=payload,
                          headers={"Content-Type": "application/json"})
        ctx = body.get("context")
        rows.append({
            "question": q,
            "status": code,
            "knowledge_hit": body.get("knowledge_hit"),
            "answer": (body.get("answer") or "")[:300],
            "took_ms": body.get("took_ms", int((time.time() - t0) * 1000)),
            "sources": [{"doc": s.get("doc"), "chars": len(s.get("text") or ""),
                         "rerank_score": s.get("rerank_score"), "reason": s.get("reason")}
                        for s in (body.get("sources") or [])],
            "context": None if ctx is None else {
                "trace_id": ctx.get("trace_id"),
                "namespace": ctx.get("namespace"),
                "blocks": ctx.get("blocks"),
                "excluded": ctx.get("excluded"),
                "degraded": ctx.get("degraded"),
                "budget": {k: ctx.get("budget", {}).get(k) for k in
                           ("total_chars", "retrieved_cap", "output_reserve_chars",
                            "retrieved_used", "retrieved_unused", "enabled_layers")},
            },
        })
        fed = [b["source"] for b in (ctx or {}).get("blocks", [])]
        dropped = [(b["source"], b["reason"]) for b in (ctx or {}).get("excluded", [])]
        print(f"— {q}  hit={rows[-1]['knowledge_hit']} {rows[-1]['took_ms']}ms "
              f"喂入={fed} 丢弃={dropped}")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "note": "Context Layer v1 真实验收：namespace /chat 的 context trace（正文不入 trace）",
        "port": base.rsplit(":", 1)[-1],
        "namespace": args.namespace,
        "corpus": pdf.name,
        "questions": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 产物：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

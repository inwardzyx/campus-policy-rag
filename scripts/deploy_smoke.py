#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""deploy_smoke.py —— 部署后一条命令验收（给人看，也给证据包用）。

## 验什么（缺一条就不算上线成功）
1. `/health`：200 且 `status=ok`、`chunks>0`（模型加载完、语料进来了）；
2. 库内问题：`/chat` 有答案，且 `sources[]` 里有 `doc`/`clause`（**带出处**）；
3. 库外问题：`/chat` 明确拒答（`knowledge_hit=False`），不编；
4. `/console`：200（控制台能打开）；
5. `/limits`：200 且额度字段存在（**钱包护栏真的生效**，不是 0）；
6. `/admin/audit`：给了 admin token 时，应能读到本次请求产生的审计行。

## 用法
```powershell
# 本地或公网都行；带 --seed 时会先把演示语料灌进 namespace
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/deploy_smoke.py `
    --base-url https://xxx.trycloudflare.com --namespace demo `
    --seed evalset/fixtures/policy_corpus --admin-token $env:KB_ADMIN_TOKEN `
    --out experiments/deploy-smoke.json
```
退出码：任一项失败 = 1（可直接进 CI / 部署流水线）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]

#: 库内问题（演示语料里一定有的）
IN_SCOPE = "一线城市出差住宿一晚最多报销多少？"
#: 库外问题（演示语料里一定没有的）
OUT_SCOPE = "公司食堂几点开饭？"


def _get(url: str, headers: dict | None = None, timeout: float = 30.0):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _post(url: str, payload: dict, timeout: float = 180.0) -> dict:
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


def seed_docs(base: str, namespace: str, docs_dir: Path, admin_token: str = "") -> int:
    """把演示语料灌进 namespace（版本号取 frontmatter 的 effective_date）。"""
    n = 0
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        version = time.strftime("%Y-%m-%d")
        if text.startswith("---"):
            for line in text.split("---", 2)[1].splitlines():
                if line.strip().startswith("effective_date:"):
                    version = line.split(":", 1)[1].strip() or version
        query = urllib.parse.urlencode({"filename": path.name, "acl": "public",
                                        "version": version})
        url = f"{base}/kb/{urllib.parse.quote(namespace)}/documents?{query}"
        headers = {"x-kb-admin-token": admin_token} if admin_token else {}
        req = urllib.request.Request(url, data=path.read_bytes(), method="POST",
                                     headers=headers)
        urllib.request.urlopen(req, timeout=180).read()
        n += 1
    return n


def run_checks(base: str, namespace: str, admin_token: str = "") -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        status, body = _get(f"{base}/health")
        health = json.loads(body)
        add("health", status == 200 and health.get("status") == "ok" and health.get("chunks", 0) > 0,
            f"status={health.get('status')} chunks={health.get('chunks')}")
    except Exception as exc:                          # noqa: BLE001
        add("health", False, f"{type(exc).__name__}: {exc}")

    try:
        resp = _post(f"{base}/chat", {"question": IN_SCOPE, "namespace": namespace})
        pairs = [_pair(s) for s in (resp.get("sources") or [])]
        add("chat_in_scope", bool(resp.get("knowledge_hit")) and bool(pairs)
            and all(p[1] for p in pairs),
            f"hit={resp.get('knowledge_hit')} sources={pairs[:3]}")
    except Exception as exc:                          # noqa: BLE001
        add("chat_in_scope", False, f"{type(exc).__name__}: {exc}")

    try:
        resp = _post(f"{base}/chat", {"question": OUT_SCOPE, "namespace": namespace})
        add("chat_out_of_scope_refused", not resp.get("knowledge_hit"),
            f"hit={resp.get('knowledge_hit')} answer={str(resp.get('answer'))[:40]!r}")
    except Exception as exc:                          # noqa: BLE001
        add("chat_out_of_scope_refused", False, f"{type(exc).__name__}: {exc}")

    try:
        status, body = _get(f"{base}/console")
        add("console", status == 200 and "控制台" in body, f"http={status} bytes={len(body)}")
    except Exception as exc:                          # noqa: BLE001
        add("console", False, f"{type(exc).__name__}: {exc}")

    try:
        status, body = _get(f"{base}/limits")
        payload = json.loads(body)
        limits = payload.get("limits") or {}
        add("limits", status == 200 and bool(limits),
            f"store={limits.get('store')} daily_req={limits.get('daily_req')}")
    except Exception as exc:                          # noqa: BLE001
        add("limits", False, f"{type(exc).__name__}: {exc}")

    if admin_token:
        headers = {"x-kb-admin-token": admin_token}
        try:
            status, body = _get(f"{base}/admin/audit?limit=5", headers=headers)
            payload = json.loads(body)
            add("admin_audit", status == 200 and payload.get("n", 0) > 0,
                f"http={status} n={payload.get('n')}")
        except Exception as exc:                      # noqa: BLE001
            add("admin_audit", False, f"{type(exc).__name__}: {exc}")
    else:
        add("admin_audit", True, "跳过（未提供 --admin-token）")

    return checks


def md_report(base: str, namespace: str, checks: list[dict]) -> str:
    ok = sum(1 for c in checks if c["ok"])
    lines = [f"# 部署验收 · {base}（namespace={namespace}）", "",
             f"> 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　通过 **{ok}/{len(checks)}**", "",
             "| 检查 | 结果 | 详情 |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['check']} | {'✓' if c['ok'] else '✗'} | {c['detail']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="部署验收（健康/带出处作答/拒答/控制台/额度/审计）")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--namespace", default="demo")
    ap.add_argument("--seed", default="", help="演示语料目录（给了就先灌进 namespace）")
    ap.add_argument("--admin-token", default="")
    ap.add_argument("--out", default="experiments/deploy-smoke.json")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    if args.seed:
        try:
            n = seed_docs(base, args.namespace, ROOT / args.seed, args.admin_token)
            print(f"· 已灌入 {n} 篇演示语料到 namespace={args.namespace}")
        except Exception as exc:                      # noqa: BLE001
            print(f"✗ 灌语料失败：{type(exc).__name__}: {exc}")
            return 1

    checks = run_checks(base, args.namespace, args.admin_token)
    for c in checks:
        print(f"{'✓' if c['ok'] else '✗'} {c['check']}: {c['detail']}")
    ok = sum(1 for c in checks if c["ok"])
    print(f"\n通过 {ok}/{len(checks)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"base_url": base, "namespace": args.namespace,
                               "checks": checks, "passed": ok, "total": len(checks)},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(md_report(base, args.namespace, checks), encoding="utf-8", newline="\n")
        print(f"✓ 证据：{md}")
    print(f"✓ 产物：{out}")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

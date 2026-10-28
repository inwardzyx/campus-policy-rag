#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""demo.py —— 演示前一键自检：起服务 → 打真实接口 → 打印可讲的结论。

## 为什么要有它
演示当天最怕三件事：端口被占、模型没加载完、`/admin` 忘了关。
这个脚本一次把三件事都验了，跑完直接告诉你"可以开始讲了"。

## 它检查什么（都是真打接口，不是读配置）
1. 服务能起来，`/health` 显示语料块数；
2. 库内问题：答了，且**带出处**（doc + 条款）；
3. 库外问题：**明确拒答**（不编）；
4. 条款号查询：能按"第X条"检索到；
5. `/metrics` 可读（Prometheus 文本）；
6. **公网/反代下 `/admin` 必须是 403**（安全探针）。

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/demo.py --port 8765
# 已经起好服务，只做检查：
... scripts/demo.py --base-url http://127.0.0.1:8765
```
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
IN_SCOPE = "学校对学生的处分有哪几种？"
OUT_SCOPE = "公司食堂几点开饭？"
CLAUSE_Q = "学生纪律处分管理规定 第二章-第四条 是怎么规定的？"
ADMIN_PATH = "/admin/audit"


def _call(base: str, path: str, payload: dict | None = None,
          headers: dict | None = None, timeout: float = 120.0) -> tuple[int, dict | str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as exc:                       # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def wait_health(base: str, timeout: float = 180.0) -> dict | None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        code, body = _call(base, "/health", timeout=5)
        if code == 200 and isinstance(body, dict):
            return body
        last = str(body)[:80]
        time.sleep(2)
    print(f"✗ 服务 {timeout:.0f}s 内没就绪（最后一次：{last}）")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="演示前一键自检")
    ap.add_argument("--base-url", default=None, help="已起好的服务；不给就自己起")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--namespace", default="")
    ap.add_argument("--keep", action="store_true", help="检查完不杀服务（演示时用）")
    args = ap.parse_args()

    srv = None
    base = args.base_url
    if not base:
        base = f"http://127.0.0.1:{args.port}"
        env = {**os.environ, "RATE_BURST": "1000", "RATE_PER_MIN": "10000",
               "LANGSMITH_TRACING": "false", "PYTHONIOENCODING": "utf-8"}
        log = ROOT / "demo-server.log"
        print(f"· 启动服务 → {base}（日志 {log.name}）")
        with open(log, "w", encoding="utf-8") as fh:
            srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "service:app",
                                    "--port", str(args.port)], cwd=str(ROOT), env=env,
                                   stdout=fh, stderr=subprocess.STDOUT)
    ok = True
    try:
        health = wait_health(base)
        if not health:
            return 1
        print(f"✓ health：status={health.get('status')} chunks={health.get('chunks')} "
              f"rejected={health.get('rejected')}")

        # ① 库内：答 + 出处
        code, resp = _call(base, "/chat", {"question": IN_SCOPE, "namespace": args.namespace})
        if code == 200 and isinstance(resp, dict) and resp.get("knowledge_hit") and resp.get("sources"):
            src = resp["sources"][0]
            where = src.get("doc") or ""
            if "｜" not in where and src.get("clause"):
                where = f"{where}｜{src.get('clause')}"
            print(f"✓ 库内问题：命中，出处 = {where}")
        else:
            ok = False
            print(f"✗ 库内问题没答好（HTTP {code}）")

        # ② 库外：必须拒答
        code, resp = _call(base, "/chat", {"question": OUT_SCOPE, "namespace": args.namespace})
        if code == 200 and isinstance(resp, dict) and not resp.get("knowledge_hit"):
            print(f"✓ 库外问题：拒答 →「{(resp.get('answer') or '')[:40]}」")
        else:
            ok = False
            print(f"✗ 库外问题没有被明确拒答（HTTP {code}）")

        # ③ 条款号检索
        code, resp = _call(base, "/chat", {"question": CLAUSE_Q, "namespace": args.namespace})
        hit = code == 200 and isinstance(resp, dict) and resp.get("sources")
        print(("✓" if hit else "✗") + " 条款号查询：" +
              (f"命中 {resp['sources'][0].get('doc')}" if hit else "没命中"))
        ok = ok and hit

        # ④ /metrics
        code, body = _call(base, "/metrics", timeout=10)
        good = code == 200 and "rag_requests_total" in str(body)
        print(("✓" if good else "✗") + " /metrics：Prometheus 文本可读")
        ok = ok and good

        # ⑤ 安全：模拟反代头 + 无 token ⇒ /admin 必须 403
        code, _ = _call(base, ADMIN_PATH, headers={"cf-connecting-ip": "1.2.3.4"})
        good = code in (401, 403)
        print(("✓" if good else "✗") + f" /admin 无 token → HTTP {code}（应为 401/403）")
        ok = ok and good

        print("\n" + ("✅ 可以开始演示：" if ok else "⚠️ 有检查没过：") + base)
        print("   演示脚本：库内问题 → 库外拒答 → 条款号 → 评测报告页 → 数据边界说明")
        print("   停服务：Ctrl+C" if args.keep else "   （脚本会自己停掉服务）")
        return 0 if ok else 2
    finally:
        if srv is not None and not args.keep:
            srv.terminate()
            try:
                srv.wait(timeout=20)
            except subprocess.TimeoutExpired:
                srv.kill()


if __name__ == "__main__":
    raise SystemExit(main())

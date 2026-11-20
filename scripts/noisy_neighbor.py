# -*- coding: utf-8 -*-
"""Noisy neighbor 实验：一个租户打满 CPU，另一个租户的延迟会不会变差。

## 设计（四阶段）

1. **基线**：只有 B 在打（4 并发），60 秒，记录 p50/p95/p99
2. **噪音**：A 加入（48 并发，/search），60 秒，B 继续
3. **恢复**：A 停，B 继续 60 秒

## 两个租户怎么区分

`X-Client-Id` + 服务端 `ALLOW_CLIENT_ID_HEADER=1` ⇒ limits 按 client 分桶。
★ 诚实说明：这是"内部分桶"，不是租户鉴权（limits.py 注释里写了它不是身份）。

## 输出

JSON（逐请求延迟）+ 一行结论（B 的 p95 劣化百分比）。
"""
from __future__ import annotations

import json
import statistics
import sys
import threading
import time
import http.client
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8036"
OUT = Path(r"D:\_review_rag-cs\experiments\noisy-neighbor.json")
QUESTION = "宿舍里使用违规电器会挨什么处分？"


def one(conn: "http.client.HTTPConnection", client_id: str) -> tuple[int, float]:
    """复用同一条 keep-alive 连接 —— 否则每请求一个新端口，Windows 上几万次就耗尽。"""
    body = json.dumps({"question": QUESTION})
    t0 = time.perf_counter()
    try:
        conn.request("POST", "/search", body,
                     {"Content-Type": "application/json",
                      "X-Client-Id": client_id,
                      # ★ 必须带角色头：8036 是 AUTH_MODE=proxy，缺了就是 401（实测 8ms 返回）
                      "X-Auth-Roles": "public"})
        r = conn.getresponse()
        r.read()
        return r.status, (time.perf_counter() - t0) * 1000
    except Exception:
        return 0, (time.perf_counter() - t0) * 1000


class Loader(threading.Thread):
    """一个客户端：按顺序打请求，把 (时间戳, 延迟, 状态) 记到共享列表。"""

    def __init__(self, client_id: str, sink: list):
        super().__init__(daemon=True)
        self.client_id = client_id
        self.sink = sink
        self.stop = threading.Event()

    def run(self):
        conn = http.client.HTTPConnection("127.0.0.1", 8036, timeout=60)
        while not self.stop.is_set():
            code, ms = one(conn, self.client_id)
            self.sink.append({"client": self.client_id, "at": time.time(),
                              "ms": round(ms, 1), "code": code})
            if code == 0:                      # 连接坏了就重开
                try:
                    conn.close()
                except Exception:
                    pass
                conn = http.client.HTTPConnection("127.0.0.1", 8036, timeout=60)
            time.sleep(0.01)
        try:
            conn.close()
        except Exception:
            pass


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return round(xs[k], 1)


def summarize(rows: list[dict], label: str) -> dict:
    lat = [r["ms"] for r in rows]
    codes = {}
    for r in rows:
        codes[r["code"]] = codes.get(r["code"], 0) + 1
    return {"phase": label, "n": len(rows),
            "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
            "codes": codes}


def main() -> int:
    rows: list[dict] = []
    # ★ B 用 2 个并发：上限是每调用方 2 个，B 自己不能撞上限（否则测到的是自我限流）
    b_threads = [Loader("tenant-b", rows) for _ in range(2)]
    for t in b_threads:
        t.start()

    time.sleep(5)                      # B 预热
    t0 = time.time()
    time.sleep(60)                     # 阶段 1：只有 B
    t1 = time.time()

    a_threads = [Loader("tenant-a", rows) for _ in range(48)]
    for t in a_threads:
        t.start()
    time.sleep(60)                     # 阶段 2：A 加入
    t2 = time.time()
    for t in a_threads:
        t.stop.set()
    time.sleep(3)
    time.sleep(57)                     # 阶段 3：A 停，B 继续
    t3 = time.time()
    for t in b_threads:
        t.stop.set()
    time.sleep(2)

    def pick(a: float, b: float, cid: str) -> list[dict]:
        return [r for r in rows if a <= r["at"] < b and r["client"] == cid]

    phases = {
        "1_基线_B单独": summarize(pick(t0, t1, "tenant-b"), "1_基线_B单独"),
        "2_噪音_A打满": summarize(pick(t1, t2, "tenant-b"), "2_噪音_A打满"),
        "3_恢复_A停": summarize(pick(t2, t3, "tenant-b"), "3_恢复_A停"),
    }
    a_rows = pick(t1, t2, "tenant-a")
    phases["A_自身"] = summarize(a_rows, "A_自身")

    base = phases["1_基线_B单独"]["p95"]
    noisy = phases["2_噪音_A打满"]["p95"]
    degrade = ((noisy - base) / base * 100) if base else 0.0

    OUT.write_text(json.dumps({"phases": phases,
                               "degradation_pct": round(degrade, 1),
                               "raw": rows}, ensure_ascii=False, indent=1), encoding="utf-8")

    print("阶段                  n     p50      p95      p99     状态码")
    for k in ("1_基线_B单独", "2_噪音_A打满", "3_恢复_A停", "A_自身"):
        s = phases[k]
        print(f"{k:20s} {s['n']:5d}  {s['p50']:7.1f}  {s['p95']:7.1f}  {s['p99']:7.1f}  {s['codes']}")
    print(f"\nB 的 p95：基线 {base}ms → 噪音期 {noisy}ms（{degrade:+.1f}%）")
    print(f"已写 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""MCP Server 的协议自检 —— 每一条都【真起一个 server 进程】验，不是读代码。

★ 为什么必须写成脚本而不是靠手敲：
  2026-10-12 手敲测试时发现了一个真 bug（notification 被回了一条 id=null 的响应），
  而它【只有真跑才暴露】—— 读代码看着是对的（"未知方法分支里没检查 rid"很难一眼看出）。
  ⇒ 同一个坑不要踩第二次：把它固化成可重跑的检查。

★ 这套检查本身的价值不只在于"server 现在是对的"，
  而在于它验证了 server 的失败方式：
  **任何坏输入都必须变成"回一条错误"，而不是"进程崩掉/ 静默卡死"。**
  因为客户端正在那头等着 —— server 一崩，客户端就永远等不到回应。

跑法：
    <虚拟环境>/Scripts/python.exe mcp/test_server_protocol.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(_ROOT, "mcp", "server.py")


def call(reqs: list[dict]) -> list[dict]:
    """喂一批请求给 server 进程，收它的所有输出行（每行一条 JSON）。"""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs)
    proc = subprocess.run(
        [sys.executable, SERVER],
        input=payload, capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


CHECKS: list[tuple[str, list[dict], object]] = [
    (
        "initialize 返回协议版本与能力声明",
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2026-07-28", "capabilities": {},
                     "clientInfo": {"name": "t", "version": "1"}}}],
        lambda rs: rs[0]["result"]["protocolVersion"] == "2026-07-28"
        and "tools" in rs[0]["result"]["capabilities"],
    ),
    (
        "tools/list 只返回两个工具，且不含负例工具",
        [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
        lambda rs: [t["name"] for t in rs[0]["result"]["tools"]] == ["search_policy", "ask_policy"],
    ),
    (
        "★ 调不存在的工具 → isError，进程不崩（负例路径）",
        [{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
          "params": {"name": "get_my_grades", "arguments": {"student_id": "2023"}}}],
        lambda rs: rs[0]["result"].get("isError") is True
        and "get_my_grades" in rs[0]["result"]["content"][0]["text"],
    ),
    (
        "★ 参数填错 → isError，进程不崩",
        [{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
          "params": {"name": "search_policy", "arguments": {"wrong_field": "x"}}}],
        lambda rs: rs[0]["result"].get("isError") is True,
    ),
    (
        "未知方法 → JSON-RPC 标准错误码 -32601",
        [{"jsonrpc": "2.0", "id": 5, "method": "tools/destroy"}],
        lambda rs: rs[0]["error"]["code"] == -32601,
    ),
    (
        "★★ notification（无 id）→ 一个字都不能回（实测踩过的 bug）",
        [{"jsonrpc": "2.0", "method": "notifications/initialized"}],
        lambda rs: rs == [],
    ),
    (
        "多请求按顺序一一对应，id 不串",
        [{"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
         {"jsonrpc": "2.0", "id": 11, "method": "tools/list"}],
        lambda rs: [r["id"] for r in rs] == [10, 11],
    ),
]


def main() -> int:
    print(f"server = {SERVER}\n")
    failed = 0
    for name, reqs, check in CHECKS:
        try:
            rs = call(reqs)
            ok = check(rs)
        except Exception as e:                       # 检查器自己出问题也要报，不能静默
            ok, rs = False, f"{type(e).__name__}: {e}"
        print(("  [OK] " if ok else "  [!!] ") + name)
        if not ok:
            failed += 1
            print(f"实际收到：{rs}")

    print()
    if failed:
        print(f"{failed}/{len(CHECKS)} 条没通过 —— server 还不能拿去接 client。")
        return 1
    print(f"全部 {len(CHECKS)} 条通过 —— 可以进阶段 2（手写 client）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

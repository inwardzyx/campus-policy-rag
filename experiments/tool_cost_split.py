# -*- coding: utf-8 -*-
"""量：两个工具各自的**每次调用**花多少 token，以及"检索端点不调生成"能省多少。

★ 为什么必须先量（2026-10-13）：
    `mcp/tools_impl.py` 的 `search_policy` 打的是 `/chat?use_rerank=False` ——
    这条路**不改写、不精排**，但 `service.py` 的 /chat **无论如何都会跑一遍生成**，
    而工具只读 `data["sources"]` ⇒ **那次生成的 token 是被直接丢掉的**。
    要动它（加一个不调生成的检索端点）之前，必须先把"能省多少"量出来，不能拍脑袋。

★ 量法（不是拿均值估，是**1:1 重放**）：
    已存的 agent 会话产物里逐次记着工具调用的**实参**（`trace[].called[].args`）。
    本脚本把那些 `search_policy` / `ask_policy` 调用**原样重放**给真服务，
    逐次读 `/chat` 返回的 `usage`，再按工具汇总。
    ★ 并且和 `agent/_token_cost.json` 里**已记录的工具侧总量**对账：
      重放合计若与记录值量级一致，才说明这个拆分可信（对不上就说明我漏了什么）。

★ 成本：只重放**去重后**的调用（同一问句只打一次，但按出现次数计权），
  默认上限 80 次；量级是几千 token，几分钱。

跑法（仓库根目录）：
    D:\\Python-project\\.venv\\Scripts\\python.exe experiments/tool_cost_split.py

产物：`agent/_tool_cost_split.json`（逐次记录，机器读；`agent/_*.json` 按 .gitignore 不进仓库）
      —— 改完之后再跑一次同一个脚本，就能对比"改动前 / 改动后"。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("SPLIT_PORT", "8017"))
API = f"http://127.0.0.1:{PORT}"
OUT = ROOT / "agent" / "_tool_cost_split.json"
TRANSCRIPTS = [
    ROOT / "agent" / "_agent_out.json",
    ROOT / "agent" / "_langgraph_out.json",
    ROOT / "mcp" / "_loop_out.json",
    ROOT / "mcp" / "_loop_out_run2.json",
    ROOT / "mcp" / "_probe_out.json",
]
TOOLS = ("search_policy", "ask_policy")


def http(method: str, url: str, body: dict | None = None, timeout: float = 120.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


# ---------------------------------------------------------------- 抽调用
def walk_calls(node, out: list[dict]) -> None:
    """递归找 {'name': ..., 'args': {...}} 这种形状（不依赖具体产物的层级）。"""
    if isinstance(node, dict):
        name = node.get("name")
        args = node.get("args")
        if name in TOOLS and isinstance(args, dict):
            out.append({"tool": name, "args": args})
        for v in node.values():
            walk_calls(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_calls(v, out)


def load_calls() -> tuple[list[dict], list[str]]:
    calls, used = [], []
    for p in TRANSCRIPTS:
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        got: list[dict] = []
        walk_calls(d, got)
        if got:
            used.append(f"{p.relative_to(ROOT).as_posix()}({len(got)})")
            calls += got
    return calls, used


# ---------------------------------------------------------------- 起服务
def start_service() -> subprocess.Popen:
    env = {**os.environ,
           "PORT": str(PORT), "HOST": "127.0.0.1",
           "HF_ENDPOINT": os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"),
           # ★ 量测要打几十次 /chat，必须把闸门全关掉（否则撞 429，量出来的是限流不是成本）
           "RATE_BURST": "1000000", "RATE_PER_MIN": "1000000",
           "DAILY_REQ_QUOTA": "0", "DAILY_TOKEN_QUOTA": "0",
           "GLOBAL_DAILY_REQ_QUOTA": "0", "GLOBAL_DAILY_TOKEN_QUOTA": "0",
           "LIMIT_FAIL_MODE": "open"}
    env.pop("REDIS_URL", None)          # 内存限流即可，不依赖 Redis
    log = open(ROOT / "agent" / "_tool_cost_split_svc.log", "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "service.py"], cwd=str(ROOT), env=env,
                         stdout=log, stderr=subprocess.STDOUT)
    print(f"  起服务 pid={p.pid}（端口 {PORT}）……")
    t0 = time.time()
    while time.time() - t0 < 240:
        try:
            code, body = http("GET", f"{API}/health", timeout=5)
            if code == 200 and body.get("status") == "ok":
                print(f"  就绪（{time.time() - t0:.1f}s，{body.get('chunks')} 块）")
                return p
        except Exception:                          # noqa: BLE001
            pass
        time.sleep(3)
    p.terminate()
    raise SystemExit("✗ 服务 240 秒没就绪 —— 看 agent/_tool_cost_split_svc.log")


# ---------------------------------------------------------------- 重放
def tokens_of(u: dict | None) -> int:
    """从 /chat 的 usage 里取总 token。

    ★★ 第一版我在这里写的是 `u.get("total")` —— 于是 14 次重放全报 **0 token**，
      而日志里 `POST https://api.deepseek.com/v1/chat/completions 200 OK` 明明成功。
      **服务的键名是 `total_tokens`**（见 `service.py::_record_usage`），
      `total` 是 `agent/_token_cost.json` 那份**归一化后**的键名 —— 我混了两个口径。
      ⇒ 教训：量一个东西之前，先确认**量具读的字段真的存在**；
        否则"量出来是 0"会被当成"没有成本"（这次差一点）。
    """
    if not isinstance(u, dict):
        return 0
    return int(u.get("total_tokens") or u.get("total") or 0)


def replay(tool: str, args: dict) -> dict:
    """按**工具的实参**原样打一次 /chat，读它花掉的 usage。"""
    if tool == "search_policy":
        payload = {"question": args.get("query", ""), "use_rerank": False,
                   "top_k": int(args.get("k", 5))}
    else:
        payload = {"question": args.get("question", ""), "use_rerank": True}
    t0 = time.time()
    code, body = http("POST", f"{API}/chat", payload)
    u = (body or {}).get("usage")
    return {"code": code, "ms": int((time.time() - t0) * 1000),
            "usage": u, "tokens": tokens_of(u),
            "n_calls": (u or {}).get("n_calls"),
            "knowledge_hit": (body or {}).get("knowledge_hit"),
            "rerank_used": (body or {}).get("rerank_used")}


def main() -> int:
    calls, used = load_calls()
    if not calls:
        print("✗ 没从会话产物里抽出任何工具调用 —— 先确认那几个产物还在")
        return 2
    print(f"从 {len(used)} 份会话产物里抽出 {len(calls)} 次工具调用：")
    for u in used:
        print(f"    {u}")

    # 去重（同一 (tool, 实参) 只打一次），但按出现次数计权
    uniq: dict[tuple, int] = {}
    for c in calls:
        key = (c["tool"], json.dumps(c["args"], ensure_ascii=False, sort_keys=True))
        uniq[key] = uniq.get(key, 0) + 1
    print(f"去重后要真打 {len(uniq)} 次（上限 80；同一问句只打一次、按出现次数计权）\n")
    if len(uniq) > 80:
        print("✗ 去重后仍超过 80 次 —— 先调大上限或缩小样本，别偷偷省着量")
        return 2

    svc = start_service()
    records = []
    try:
        for i, ((tool, args_json), times) in enumerate(sorted(uniq.items()), 1):
            args = json.loads(args_json)
            r = replay(tool, args)
            r.update({"tool": tool, "args": args, "times": times})
            records.append(r)
            q = (args.get("query") or args.get("question") or "")[:28]
            print(f"  [{i}/{len(uniq)}] {tool:14s} ×{times}  {r['tokens']:6d} token  "
                  f"n_calls={r['n_calls']}  hit={r['knowledge_hit']}  {r['ms']:5d}ms  {q}")
    finally:
        svc.terminate()
        try:
            svc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            svc.kill()

    # ------------------------------------------------------------ 汇总
    print("\n" + "=" * 72)
    print("按工具汇总（token 按出现次数加权）")
    print("=" * 72)
    summary = {}
    for tool in TOOLS:
        rs = [r for r in records if r["tool"] == tool]
        w = sum(r["tokens"] * r["times"] for r in rs)
        n = sum(r["times"] for r in rs)
        hits = sum(r["times"] for r in rs if r["knowledge_hit"])
        summary[tool] = {"calls": n, "tokens": w,
                         "per_call": round(w / n, 1) if n else 0,
                         "hit_calls": hits,
                         "refuse_calls": n - hits}
        print(f"  {tool:14s} 调用 {n:3d} 次（命中 {hits:3d} / 拒答 {n - hits:3d}）"
              f"  合计 {w:6d} token  每次均 {summary[tool]['per_call']:7.1f}")

    # 已记录的工具侧总量（对账用）
    tc = json.loads((ROOT / "agent" / "_token_cost.json").read_text(encoding="utf-8"))
    rec_tool = sum((r.get("tool_usage") or {}).get("total", 0)
                   for r in tc.get("agent", []) if not r.get("error"))
    rec_calls = sum(r.get("n_tool_calls") or 0 for r in tc.get("agent", [])
                    if not r.get("error"))
    replay_total = sum(r["tokens"] * r["times"] for r in records)
    print(f"\n对账：重放合计 {replay_total} token / 记录的工具侧合计 {rec_tool} token"
          f"（{replay_total / rec_tool * 100:.0f}%）；"
          f"重放调用数 {sum(r['times'] for r in records)} / 记录 {rec_calls}")

    # 省多少：search_policy 那条路**整段消失**（不调生成 ⇒ 0 LLM token）
    save = summary["search_policy"]["tokens"]
    n_q = len([r for r in tc.get("agent", []) if not r.get("error")])
    tool_mean = rec_tool / n_q
    agent_mean = sum((r.get("usage") or {}).get("total", 0)
                     for r in tc.get("agent", []) if not r.get("error")) / n_q
    dir_mean = sum((r.get("usage_raw") or {}).get("total_tokens", 0)
                   for r in tc.get("direct", []) if not r.get("error")) / n_q
    print("\n" + "=" * 72)
    print("改动后能省多少（预测值 —— 改完要回来对）")
    print("=" * 72)
    print(f"  search_policy 这条路的 token 全消失 : {save} token/遍"
          f"（{summary['search_policy']['calls']} 次调用）")
    print(f"  工具侧均值  {tool_mean:7.1f} → {tool_mean - save / n_q:7.1f} token/题"
          f"（-{save / n_q:.1f}）")
    print(f"  agent 总均值 {agent_mean:7.1f} → {agent_mean - save / n_q:7.1f} token/题")
    print(f"  直打均值    {dir_mean:7.1f}（不变）")
    print(f"  倍率（含工具）{(agent_mean + tool_mean) / dir_mean:.2f}× → "
          f"{(agent_mean + tool_mean - save / n_q) / dir_mean:.2f}×")
    print("\n★ 这里的 save 是『整段消失』：sources-only 端点连生成都不调，"
          "所以那几次调用变成 0 LLM token（检索本身不花 token）。")

    OUT.write_text(json.dumps({"summary": summary, "records": records,
                               "replay_total": replay_total,
                               "recorded_tool_total": rec_tool,
                               "recorded_tool_calls": rec_calls,
                               "n_questions": n_q,
                               "tool_mean_per_q": tool_mean,
                               "agent_mean_per_q": agent_mean,
                               "direct_mean_per_q": dir_mean,
                               "predicted_save_per_pass": save,
                               "predicted_save_per_q": save / n_q},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n逐次记录已落盘：{OUT.relative_to(ROOT)}（agent/_*.json 按 .gitignore 不进仓库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

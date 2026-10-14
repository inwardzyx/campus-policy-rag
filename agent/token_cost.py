# -*- coding: utf-8 -*-
"""Token 与成本计量 —— 把"这个 agent 跑一次多少钱"变成能算的数。

★★★ 为什么要做这个（它不是锦上添花，是 JD 点名的缺口）：

  仓库里 token 用量只在 experiments/ 下的两个【教学脚本】里被打印过一次，
  主服务和整条 agent 链路一次都没量过。
  ⇒ 被问"你这个 agent 跑一次多少钱、比直连贵多少、你怎么控"时答不出来。
  ⇒ 而"成本纪律"恰恰是招聘方明确列出的考察项。

------------------------------------------------------------------
★★★ 三条防污染纪律（写这个文件时正在跑 105 题 × 3 遍的回归批）：

  ① **不改任何正在被 import 的模块**
     本文件只 import，不 monkeypatch、不改 probe_loop / tools_impl /
     policy_agent / langgraph_agent。
     理由：回归进程已经把这些模块 import 进内存了，我改磁盘上的文件，
     对它当前这批**没影响**，但会让下一批跑在不同代码上 ——
     而"同一实验的前后半段用了不同代码"是我们今天反复拒绝的那类错误。

  ② **不与回归抢 RAG 服务**
     agent 路径的每个工具调用都 HTTP 打 127.0.0.1:8000。
     两个进程同时打会互相拖慢，严重时超时 → 污染回归那批数据。
     ⇒ 本文件默认【只生成代码不执行】，等回归结束再跑。

  ③ **token 从响应对象读，不改调用链**
     LangChain 的 AIMessage 自带 usage_metadata，
     直接读即可，不需要改 bind_tools 或 invoke 的任何参数。
     ⇒ 这也保证"计量"不会反过来影响"被计量的行为"。

------------------------------------------------------------------
★★ DeepSeek 的价格口径（写进文档，面试时必须说清是哪个）：

  计量出来的 token 数是【精确的】，但换算成钱依赖价格表，价格会变。
  ⇒ 所以本脚本把 token 和金额【分开列】，金额行标注"按 YYYY-MM-DD 价格表"。
  ⇒ 只报 token 不报钱，比报一个会过期的数字诚实。
     （与项目里"漏答率和 Recall 是两把尺子，别混用"同原则：口径要能溯源。）

------------------------------------------------------------------
跑法（必须等回归跑完再执行）：
    python -m uvicorn service:app --port 8000
    <虚拟环境>/Scripts/python.exe agent/token_cost.py --n 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

import env_compat                                    # noqa: E402
env_compat.ensure_mmh3()                            # noqa: E402
env_compat.ensure_uuid_utils()                      # noqa: E402
from dotenv import load_dotenv                       # noqa: E402
load_dotenv(os.path.join(_ROOT, ".env"))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_deepseek import ChatDeepSeek                     # noqa: E402

from mcp.probe_loop import MAX_ROUNDS, SYSTEM_PROMPT, run_one   # noqa: E402
from mcp.tool_schemas import MODEL_SCHEMAS                      # noqa: E402
from mcp.tools_impl import _get_json, health                     # noqa: E402
from mcp.client import StdioMCPClient                            # noqa: E402
# ★ print 里的 ⇒/✅/✗ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 5 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ★★★ 价格表必须带日期。DeepSeek 调过价，写一个没日期的数字就是在写猜测。
#   换成你实际用的那天的价格；面试时说"按 X 月价格表"比说个数字可信。
PRICE_TABLE = {
    "_as_of": "2026-10（DeepSeek 官方价格，请按投递时的实际价格替换）",
    # 单位：元 / 百万 token
    "deepseek-chat": {"input_cache_hit": 0.2, "input": 2.0, "output": 8.0},
}


def usage_of(ai_msg) -> dict:
    """从一条 AIMessage 里读 token 用量。

    ★ 为什么不自己数 token：
      自己数要么不准（分词器对不上），要么得额外装 tiktoken（=新依赖）。
      而 LangChain 已经在调 API 时拿到了服务返回的 usage，直接读最准。
    ★ 兼容两种字段名：
      - langchain-core 1.6+：ai_msg.usage_metadata = {input_tokens, output_tokens, total_tokens, ...}
      - 更早/别的 provider：ai_msg.response_metadata["token_usage"] = {prompt_tokens, ...}
      ⇒ 两个都试，取不到就返回 None，【不猜、不补 0】
      （填 0 会让"平均 token 数"变成假的，而假的均值比没有均值更坏）
    """
    um = getattr(ai_msg, "usage_metadata", None)
    if isinstance(um, dict) and um.get("total_tokens"):
        return {
            "input": um.get("input_tokens", 0),
            "output": um.get("output_tokens", 0),
            "total": um.get("total_tokens", 0),
            "cache_hit": (um.get("input_token_details") or {}).get("cache_read", 0),
            "source": "usage_metadata",
        }
    rm = getattr(ai_msg, "response_metadata", None) or {}
    tu = rm.get("token_usage") or rm.get("usage")
    if isinstance(tu, dict) and (tu.get("total_tokens") or tu.get("prompt_tokens")):
        return {
            "input": tu.get("prompt_tokens", tu.get("input_tokens", 0)),
            "output": tu.get("completion_tokens", tu.get("output_tokens", 0)),
            "total": tu.get("total_tokens", 0),
            "cache_hit": tu.get("prompt_cache_hit_tokens", 0),
            "source": "response_metadata.token_usage",
        }
    return None


# ==================================================================
# 工具侧的账：**重放取账**（2026-10-14 改；原来靠标记行里的 tokens_* 字段）
# ==================================================================
# ★★ 为什么改：账目字段原来跟着标记行一起返回，而标记行整段会**重新进模型上下文**
#   （每一轮重发）。13 次 `search_policy` 调用实测：标记行合计 1,040 字，账目字段是其中大头，
#   而模型完全不需要知道自己花了多少钱 —— 这是纯噪声。
#   删掉之后**模型可见的其余文本一个字节都没变**（`scripts/ab_payload_trim.py` 逐字节比），
#   所以这是"可机械证明的等价改动"，不是"我觉得没用"。
#
# ★ 代价（如实写在这里）：重放要求**被计量的那个服务在测量结束后仍然可用**，
#   而且重放时语料/模型不能变 —— 否则服务端记账会和当时不一致。
#   所以这类"拿不到账"的机会比原来多：`missing` 的语义因此更重要，别把它读成 0。


class ToolLedger:
    """按【记录下来的调用参数】重放每个工具调用，取服务端自己报的 usage。

    ★ 两条账一起留着，**互为交叉验证**：
      · 重放         —— 重放取到的 token（现在的主口径）
      · `llm_calls` —— 标记行里工具自己报的"服务端调了几次模型"
    两者对不上就是"重放走的不是当时那条路"，必须报出来，不许挑一个信。
    """

    def __init__(self) -> None:
        self.missing = 0                    # 重放失败 / 没拿到 usage 的次数（≠ 花了 0）
        self.n_calls = 0                    # 一共重放了几次工具调用
        self.llm_calls_reported = 0
        self.llm_calls_missing = 0
        self.mismatch: list[str] = []

    def resolve(self, row: dict) -> None:
        """把一道题里每次工具调用的账，按调用重放并回填到 `tool_usage` / `tool_by_name`。

        ★ 逐调用回填（而不是整题一个数）：`tool_by_name` 是"该动哪个工具"的依据，
          颗粒度丢了那张表就没意义了。
        """
        agg = {"input": 0, "output": 0, "total": 0, "cache_hit": 0, "n_calls": 0}
        by_name: dict[str, dict] = {}
        missing = 0
        for c in row.pop("_tool_calls", []):
            name, args, reported = c["name"], c["args"], c.get("reported_calls")
            slot = by_name.setdefault(
                name, {"calls": 0, "tokens": 0, "input": 0, "output": 0, "missing": 0})
            slot["calls"] += 1
            self.n_calls += 1
            path = TOOL_ENDPOINT.get(name)
            if path is None:
                # 模型幻觉出的工具名：它根本没到服务端 ⇒ 这里也算一次"没拿到"
                slot["missing"] += 1
                missing += 1
                continue
            try:
                data = _get_json(path, _replay_payload(name, args))
                u = data.get("usage")
                if not isinstance(u, dict):
                    raise ValueError("响应里没有 usage")
            except Exception as e:                              # noqa: BLE001
                slot["missing"] += 1
                missing += 1
                print(f"    [账] 重放 {name} 失败（{type(e).__name__}）—— 计缺失，不补 0")
                continue
            slot["tokens"] += u.get("total_tokens", 0)
            slot["input"] += u.get("input_tokens", 0)
            slot["output"] += u.get("output_tokens", 0)
            for k_src, k_dst in (("input_tokens", "input"), ("output_tokens", "output"),
                                 ("total_tokens", "total"), ("cache_read", "cache_hit"),
                                 ("n_calls", "n_calls")):
                agg[k_dst] += u.get(k_src, 0)
            # ---- 交叉验证：工具自己报的调用次数 vs 重放拿到的次数 ----
            if reported is None:
                self.llm_calls_missing += 1
            else:
                self.llm_calls_reported += reported
                got = u.get("n_calls", 0)
                if int(got) != int(reported):
                    self.mismatch.append(
                        f"{row['id']} {name}：标记行报 llm_calls={reported}，"
                        f"重放拿到 n_calls={got}")
        row["tool_usage"] = agg
        row["tool_usage"]["cost"] = cost_of(agg)     # 与旧版字段对齐（正文里报过工具侧成本）
        row["tool_by_name"] = by_name
        row["tool_usage_missing"] = missing
        self.missing += missing


def _replay_payload(name: str, args: dict) -> dict:
    """把当时的工具参数还原成服务端请求体。"""
    if name == "search_policy":
        return {"question": args.get("query", ""), "top_k": args.get("k", 5)}
    return {"question": args.get("question", ""), "use_rerank": True}


TOOL_ENDPOINT = {"search_policy": "/search", "ask_policy": "/chat"}


def sum_usage(ai_msgs: list) -> dict:
    """把一条链路里所有 LLM 调用的 token 加起来。

    ★ 为什么必须【按调用累加】而不是看最后一次：
      agent 循环每轮都调一次模型，工具返回的内容还会进下一轮的 prompt
      ⇒ token 是【随轮数增长】的。只看最后一次 = 只量了尾轮，
      会得出"多轮不花钱"的错误结论 —— 而那恰恰是多轮最该量的地方。
    """
    agg = {"input": 0, "output": 0, "total": 0, "cache_hit": 0, "n_calls": 0,
           "missing_usage": 0}
    for m in ai_msgs:
        u = usage_of(m)
        if not u:
            agg["missing_usage"] += 1
            continue
        agg["n_calls"] += 1
        for k in ("input", "output", "total", "cache_hit"):
            agg[k] += u[k]
    return agg


def cost_of(usage: dict, model: str = "deepseek-chat") -> float | None:
    """把 token 换算成元。★ 价格缺失就返回 None，不拿 0 冒充"免费"。"""
    p = PRICE_TABLE.get(model)
    if not p or usage.get("total", 0) == 0:
        return None
    hit = usage.get("cache_hit", 0)
    fresh = max(usage["input"] - hit, 0)          # 缓存命中的部分按更低的价
    per_million = (fresh * p["input"] + hit * p["input_cache_hit"]
                   + usage["output"] * p["output"]) / 1_000_000
    return round(per_million, 6)


# ==================================================================
# 路径①：直打 /chat —— 这里的 usage 从服务的 usage 字段读
# ==================================================================


def measure_direct(qs: list) -> list[dict]:
    rows = []
    for i, q in enumerate(qs, 1):
        try:
            data = _get_json("/chat", {"question": q["question"], "use_rerank": True})
        except Exception as e:                                  # noqa: BLE001
            rows.append({"id": q["id"], "error": f"{type(e).__name__}"})
            print(f"  [直打 {i}/{len(qs)}] {q['id']:5} ★{type(e).__name__}")
            continue
        # ★ 服务端返回的 usage 字段名要和实际对齐 —— 先看有哪些键，
        #   读不到就说读不到，不硬套一个名字。
        usage = data.get("usage") or data.get("token_usage")
        if not isinstance(usage, dict):
            usage = {"_keys": sorted(k for k in data if "usage" in k.lower()
                                     or "token" in k.lower())}
        rows.append({"id": q["id"], "type": q["type"], "usage_raw": usage,
                     "error": None})
        print(f"  [直打 {i}/{len(qs)}] {q['id']:5} usage={usage}")
    return rows


# ==================================================================
# 路径②：走 agent —— token 从每轮的 AIMessage 累加
# ==================================================================


def measure_agent(qs: list, bound, client: StdioMCPClient) -> list[dict]:
    """量 agent 路径。

    ★ 这里【不能直接用 run_one 的返回值】——它已经把 messages 丢掉、只留了 trace。
      但 trace 里存的是「工具调用的记录」，不是「模型的消息」。
      ⇒ 两个选择：
        (a) 给 run_one 加一个返回 messages 的开关  ← ✗ 会改正在被 import 的模块
        (b) 在这里自己跑一遍循环                          ← ✓ 只写在本文件里
      ⇒ 选 (b)。代价是循环逻辑在本文件里重写了 20 行，
        但【隔离性】比"少写20 行"重要 —— 改动正在跑批依赖的模块是不能接受的。
      ★ 而且这 20 行是【刻意重复】的，注释里已标注：真要合并，
        应该等回归跑完之后，合并时两版一起改并重跑基线。
    """
    from agent.policy_agent import McpCaller, _DictLike
    import mcp.probe_loop as PL

    original = PL.TOOL_IMPL
    PL.TOOL_IMPL = _DictLike(McpCaller(client))
    rows = []
    try:
        for i, q in enumerate(qs, 1):
            msgs = [SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=q["question"])]
            ai_msgs = []
            truncated = False
            # ★★ 工具侧的账（2026-10-12 加、2026-10-14 改成重放取账）：
            #   这里**只记调用参数**，账等服务端重放时再取（见 ToolLedger 的长注释）。
            #   为什么不在循环里顺手取账：那需要把账目字段塞进工具返回，
            #   而工具返回每一轮都会重新进模型上下文 —— 那是纯噪声，
            #   且会污染"模型看到的文本"这个被其它判据依赖的量。
            tool_calls_rec: list[dict] = []
            try:
                for rnd in range(MAX_ROUNDS):
                    resp = bound.invoke(msgs)
                    ai_msgs.append(resp)
                    if not resp.tool_calls:
                        break
                    msgs.append(resp)
                    for tc in resp.tool_calls:
                        from mcp.tools_impl import (TOOL_IMPL, ToolError,
                                                    parse_signal_calls)
                        fn = TOOL_IMPL.get(tc["name"])
                        out = (f"没有名为 {tc['name']} 的工具。现有工具："
                               f"{sorted(TOOL_IMPL)}。" if fn is None
                               else _safe_call(fn, tc["args"]))
                        # ★ 记下这次调用（名字 + 参数）供重放；顺带留一份工具自己报的
                        #   调用次数 —— 它和重放拿到的次数互为交叉验证（见 ToolLedger）。
                        tool_calls_rec.append(
                            {"name": tc["name"], "args": dict(tc["args"]),
                             "reported_calls": parse_signal_calls(str(out))})
                        from langchain_core.messages import ToolMessage
                        msgs.append(ToolMessage(content=str(out),
                                                tool_call_id=tc["id"]))
                else:
                    truncated = True
            except Exception as e:                              # noqa: BLE001
                rows.append({"id": q["id"], "error": f"{type(e).__name__}"})
                print(f"  [agent {i}/{len(qs)}] {q['id']:5} ★{type(e).__name__}")
                continue
            usage = sum_usage(ai_msgs)
            usage["cost"] = cost_of(usage)
            rows.append({"id": q["id"], "type": q["type"], "usage": usage,
                         # ★ 第二本账（工具内部那一侧）由 ToolLedger 在跑完之后回填，
                         #   字段名与旧版一致：tool_usage / tool_by_name / tool_usage_missing
                         "_tool_calls": tool_calls_rec,
                         "n_tool_calls": sum(len(t.tool_calls or [])
                                             for t in ai_msgs),
                         "truncated": truncated, "error": None})
            print(f"  [agent {i}/{len(qs)}] {q['id']:5} "
                  f"LLM 调用 {usage['n_calls']} 次　"
                  f"入 {usage['input']} / 出 {usage['output']} / 合计 {usage['total']} token"
                  f"　工具调用 {len(tool_calls_rec)} 次（账稍后重放取）"
                  f"{'　★有调用没拿到 usage' if usage['missing_usage'] else ''}")
    finally:
        PL.TOOL_IMPL = original
    return rows


def _safe_call(fn, kwargs) -> str:
    from mcp.tools_impl import ToolError
    try:
        return fn(**kwargs)
    except ToolError as e:
        return f"工具调用失败：{e}"


# ------------------------------------------------------------------


def _summarize_run(run_i: int, d_rows: list, a_rows: list) -> dict:
    """把一遍的测量压成几个数（供 --runs 报波动范围用）。

    ★ 三个数并列，**不合并**：
      · direct_mean   直打侧每题 token（一次 /chat）
      · agent_own      agent 自己那几轮
      · tool_mean     工具内部（/chat 那一侧）—— 2026-10-12 之前这笔账根本不存在
      · ratio_all     (agent_own + tool) / direct —— 这才是"agent 贵几倍"的真分子
    """
    d_ok = [r for r in d_rows if not r.get("error")
            and isinstance(r.get("usage_raw"), dict) and "n_calls" in r["usage_raw"]]
    a_ok = [r for r in a_rows if not r.get("error") and isinstance(r.get("usage"), dict)]
    d_mean = (sum(r["usage_raw"]["total_tokens"] for r in d_ok) / len(d_ok)) if d_ok else 0.0
    a_mean = (sum(r["usage"]["total"] for r in a_ok) / len(a_ok)) if a_ok else 0.0
    t_mean = (sum(r.get("tool_usage", {}).get("total", 0) for r in a_ok) / len(a_ok)) if a_ok else 0.0
    t_missing = sum(r.get("tool_usage_missing", 0) for r in a_ok)
    # ★ 按工具名汇总（2026-10-13）：用来回答"这笔工具侧的账里，哪个工具占了多少"。
    #   两个工具打的是不同的路（search 只跑生成、ask 还跑改写+精排），
    #   所以"该动哪个"必须看这张表，不能看总数。
    by_tool: dict[str, dict] = {}
    for r in a_ok:
        for name, slot in (r.get("tool_by_name") or {}).items():
            agg = by_tool.setdefault(name, {"calls": 0, "tokens": 0, "missing": 0})
            agg["calls"] += slot.get("calls", 0)
            agg["tokens"] += slot.get("tokens", 0)
            agg["missing"] += slot.get("missing", 0)
    return {"run": run_i, "n_direct": len(d_ok), "n_agent": len(a_ok),
            "direct_mean": d_mean, "agent_own_mean": a_mean, "tool_mean": t_mean,
            "tool_usage_missing": t_missing, "tool_by_tool": by_tool,
            "ratio_own": (a_mean / d_mean) if d_mean else 0.0,
            "ratio_all": ((a_mean + t_mean) / d_mean) if d_mean else 0.0}


# ------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="量前 N 题（默认 10）")
    ap.add_argument("--only-refuse", action="store_true",
                    help="只量拒答题（拒答是短回答，token 成本天然更低）")
    ap.add_argument("--runs", type=int, default=1,
                    help="跑几遍（★ 默认 1；要报波动范围就 >=2。"
                         "单次跑出来的倍率支持不了小数第二位的精度，"
                         "见 evalset/report-token-cost.md 的更正块）")
    ap.add_argument("--out", default="agent/_token_cost.json",
                    help="明细写到哪（默认 agent/_token_cost.json）。"
                         "★ 做**前后对比实验**时必须换一个路径："
                         "默认那份是 evalset/report-token-cost.md 的真相源，"
                         "被覆盖会让 check_token_report.py 全线报红（那是设计如此）。")
    args = ap.parse_args()

    try:
        print(f"[前置] RAG 服务：{health()}")
    except Exception as e:                                       # noqa: BLE001
        print(f"[!!] RAG 服务不可用：{e}")
        return None

    raw = json.load(open(os.path.join(_ROOT, "evalset", "heldout", "questions.json"),
                         encoding="utf-8"))
    all_qs = raw if isinstance(raw, list) else raw["questions"]
    qs = [q for q in all_qs if q["type"] == "refuse"] if args.only_refuse else all_qs
    qs = qs[:args.n]
    print(f"题集：前 {len(qs)} 道　{dict((t, sum(1 for q in qs if q['type'] == t)) for t in {q['type'] for q in qs})}")
    print(f"价格表口径：{PRICE_TABLE['_as_of']}\n")

    bound = ChatDeepSeek(model="deepseek-chat", temperature=0).bind_tools(MODEL_SCHEMAS)

    runs_summary: list[dict] = []
    for run_i in range(1, args.runs + 1):
        if args.runs > 1:
            print("\n" + "#" * 68)
            print(f"##### 第 {run_i}/{args.runs} 遍 #####")
            print("#" * 68)
        print("=== 直打 /chat ===")
        d_rows = measure_direct(qs)
        print("\n=== 走 agent（MCP + 工具 + 循环）===")
        with StdioMCPClient() as client:
            a_rows = measure_agent(qs, bound, client)
        # ★★ 工具侧的账在这里取（2026-10-14 改）：按刚才记录的调用参数**重放**，
        #   不再从工具返回的标记行里读 —— 那会把账目数字塞回模型上下文。
        #   见 ToolLedger 的注释；★ 这一步要求 RAG 服务仍然可用。
        print("\n=== 重放取工具侧的账 ===")
        ledger = ToolLedger()
        for r in a_rows:
            if not r.get("error"):
                ledger.resolve(r)
        print(f"  重放完成：{ledger.n_calls} 次工具调用"
              f"（其中 {ledger.missing} 次没拿到账 —— 计缺失，不补 0）")
        if ledger.mismatch:
            print(f"  ★★ 交叉验证不一致 {len(ledger.mismatch)} 处"
                  f"（标记行的 llm_calls vs 重放拿到的 n_calls）：")
            for m in ledger.mismatch[:10]:
                print(f"     {m}")
        if ledger.llm_calls_missing:
            print(f"  ★ 有 {ledger.llm_calls_missing} 次调用没带上 llm_calls —— "
                  f"那部分无法交叉验证（≠ 花了 0）")
        runs_summary.append(_summarize_run(run_i, d_rows, a_rows))

    # ----汇总 ----
    print("\n" + "=" * 68)
    print("汇总")
    print("=" * 68)
    ok = [r for r in a_rows if not r.get("error")]
    if ok:
        avg = {k: sum(r["usage"][k] for r in ok) / len(ok)
               for k in ("input", "output", "total", "n_calls")}
        avg_cost = [r["usage"]["cost"] for r in ok if r["usage"]["cost"] is not None]
        print(f"  agent 路径：{len(ok)} 题计入")
        print(f"    平均 LLM 调用 {avg['n_calls']:.2f} 次/题")
        print(f"    平均 input {avg['input']:.0f}　output {avg['output']:.0f}　"
              f"合计 {avg['total']:.0f} token/题")
        if avg_cost:
            print(f"    平均成本 {sum(avg_cost) / len(avg_cost):.6f} 元/题"
                  f"（{PRICE_TABLE['_as_of']}）")
        miss = sum(r["usage"]["missing_usage"] for r in ok)
        if miss:
            print(f"    ★ 有 {miss} 次调用没读到 usage —— 均值因此偏低，别当真值用")

    # ---- 直打侧汇总 ----
    # ★ 这段以前只会打印一条"usage 实际形态"（那是加计量之前的临时展示）。
    #   服务端真把 usage 报上来之后还那么打，等于【量都量了却不给结论】。
    d_ok = [r for r in d_rows
            if not r.get("error") and isinstance(r.get("usage_raw"), dict)
            and "n_calls" in r["usage_raw"]]
    d_total = len(d_rows)
    print(f"\n  直打 /chat：{len(d_ok)}/{d_total} 题计入"
          f"（{d_total - len(d_ok)} 题没有可用 usage，不计入均值）")
    if d_ok:
        keys = ("input_tokens", "output_tokens", "total_tokens", "n_calls")
        d_avg = {k: sum(r["usage_raw"][k] for r in d_ok) / len(d_ok) for k in keys}
        print(f"    平均 LLM 调用 {d_avg['n_calls']:.2f} 次/题"
              f"　（一次 /chat 内部要走：查询改写 → rerank 打分 → 生成）")
        print(f"    平均 input {d_avg['input_tokens']:.0f}　"
              f"output {d_avg['output_tokens']:.0f}　"
              f"合计 {d_avg['total_tokens']:.0f} token/题")
        d_cost = [cost_of({"input": r["usage_raw"]["input_tokens"],
                            "output": r["usage_raw"]["output_tokens"],
                            "total": r["usage_raw"]["total_tokens"],
                            "cache_hit": r["usage_raw"].get("cache_read", 0)})
                  for r in d_ok]
        d_cost = [c for c in d_cost if c is not None]
        if d_cost:
            print(f"    平均成本 {sum(d_cost) / len(d_cost):.6f} 元/题"
                  f"（{PRICE_TABLE['_as_of']}）")
        # ★ missing_calls 不为 0 也要说：均值混了漏记的部分，会【偏低】而不是偏高
        d_miss = sum(r["usage_raw"].get("missing_calls", 0) for r in d_ok)
        if d_miss:
            print(f"    ★ 有 {d_miss} 次调用没读到 usage —— 均值因此偏低，别当真值用")
        if ok:
            a_avg_total = sum(r["usage"]["total"] for r in ok) / len(ok)
            ratio = a_avg_total / d_avg["total_tokens"] if d_avg["total_tokens"] else 0
            print(f"    ⇒ 同样这批题，agent 侧 {a_avg_total:.0f} token/题，"
                  f"是直打的 {ratio:.1f} 倍")
    else:
        # 保留原来那两行提示：读不到就说读不到，不拿 0 冒充"直连不花钱"
        sample = next((r["usage_raw"] for r in d_rows
                       if not r.get("error") and r.get("usage_raw")), None)
        print(f"    直打 /chat 的 usage 实际形态：{sample}")
        print("    ★ 服务端没返回可用的 usage 字段 ⇒ 直打侧只能报 agent 侧。")
        print("      ★ 别拿 0 冒充'直连不花钱'——那是不测就说没有。")

    if len(runs_summary) > 1:
        print("\n" + "=" * 68)
        print(f"{len(runs_summary)} 遍的波动范围")
        print("★ 单次点值支持不了小数第二位的精度 —— 这正是不默认多跑几遍就报数的风险")
        print("=" * 68)
        for r in runs_summary:
            miss = (f"　★工具侧有 {r['tool_usage_missing']} 次没报上账"
                    if r["tool_usage_missing"] else "")
            print(f"  第 {r['run']} 遍：直打 {r['direct_mean']:.0f}　"
                  f"agent 自身 {r['agent_own_mean']:.0f}　工具侧 {r['tool_mean']:.0f}　"
                  f"⇒ 仅自身 {r['ratio_own']:.2f}× / 含工具 {r['ratio_all']:.2f}×{miss}")
        for k, label in (("ratio_own", "仅 agent 自身"), ("ratio_all", "含工具侧")):
            vs = [r[k] for r in runs_summary if r[k]]
            if vs:
                print(f"  {label}的倍率范围：{min(vs):.2f} ~ {max(vs):.2f}")

    out = os.path.join(_ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"price_table": PRICE_TABLE, "n_questions": len(qs),
                   # ★ 多遍的汇总（默认 1 遍时也写，长度 1）
                   "runs_n": len(runs_summary), "runs": runs_summary,
                   # direct/agent 保留【最后一遍】的逐题明细 —— 逐格对账器认这两个键
                   "direct": d_rows, "agent": a_rows},
                  f, ensure_ascii=False, indent=2)
    # ★ 按工具名的那张表单独打印（2026-10-13）：它是"该动哪个工具"的依据。
    for r in runs_summary:
        if r.get("tool_by_tool"):
            print(f"\n工具侧按工具拆（第 {r['run']} 遍）：")
            for name, slot in sorted(r["tool_by_tool"].items(),
                                     key=lambda kv: -kv[1]["tokens"]):
                per = slot["tokens"] / slot["calls"] if slot["calls"] else 0
                print(f"  {name:16s} 调用 {slot['calls']:3d} 次　合计 {slot['tokens']:6d} token"
                      f"　每次均 {per:7.1f}"
                      f"{'　★有 %d 次没报上账' % slot['missing'] if slot['missing'] else ''}")
    print(f"\n明细已写入 {os.path.relpath(out, _ROOT)}")
    # ★ 2026-10-12 修：这里原来没有 return ⇒ main() 隐式返回 None
    #   ⇒ 外层 `if main() is None: sys.exit(1)` 把【成功】也判成失败，
    #     整个脚本跑完永远退出码 1。任何拿它做流水线的人都会看到"失败了"，
    #     而它其实刚把明细写完 —— 又一处"退出码在说谎"。
    return 0


if __name__ == "__main__":
    if main() is None:
        sys.exit(1)

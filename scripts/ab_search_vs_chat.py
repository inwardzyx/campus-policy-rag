"""A/B 对照：`/search`（只检索）与 `/chat?use_rerank=False`（检索 + 白跑一次生成）。

## 为什么要有这个脚本（2026-10-14）

`mcp/tools_impl.py::search_policy` 原来借道 `/chat?use_rerank=False` 拿原文条款 ——
服务内部会**白跑一次生成**，而工具只读 `sources`，那段答案**没有任何人读**。
实测 20 题里这条路花了 5,846 token（agent 那一遍 13 次调用 6,337 token）。
改成 `/search` 之后这件事是 0，但必须回答一个问题：

    **去掉生成，模型看到的东西有没有跟着变？**

所以这里不是"跑一遍看看快了多少"，而是**同题配对**地比三样东西：
  ① `sources` 逐字段（doc / text / score）；
  ② `knowledge_hit` 与 `rerank_used`；
  ③ **渲染后的文本逐字节**（`mcp/tools_impl._format_sources`，也就是模型真正读到的那段）。
     ②③ 必须一起比：只比 sources 是"我知道它们像"，比到文本才是"我知道模型看到的一样"。
另外顺手钉住 `/search` 的账是【可证明的 0】（`n_calls=0`、`total_tokens=0`）——
这条是 `limits.py` 不往 `usage_missing` 记账的前提。

## 怎么跑

    python -m uvicorn service:app --port 8000 &      # 或 python service.py
    python scripts/ab_search_vs_chat.py --n 20

★ 需要**服务真的在跑**，而且要有可用的 DEEPSEEK_API_KEY：
  命中题的 `/chat` 会走到生成那一步（那条路上没有 key 会直接 500，见 service.py 的注释）。
  所以它**不进 CI**（CI 没有 key），进 CI 的是它的产物：
  `evalset/report-search-ab.json` 的静态不变量由 `scripts/check_artifact_sanity.py` 校。
  ⇒ 这个分工是刻意的：**跑它需要环境，验它不需要**。

产物：`evalset/report-search-ab.json`（`--out` 可改）。
退出码：0 = 真的跑了并且落盘了（不看结论）；结论本身由上面那个检查器校。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

# ★★ Windows 控制台默认 GBK，这个脚本会印 ✓/✗ —— 必须在**第一行模块级 print 之前**改编码。
#    （本仓库已经因为这件事栽过 7 次，tests/test_guard.py 里有守门用例。）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_OUT = os.path.join(ROOT, "evalset", "report-search-ab.json")


def _post(api: str, path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        api + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"✗ {path} 返回 HTTP {e.code}：{e.read()[:200]!r}\n"
                         f"  （提示：命中题的 /chat 要调生成，缺 key 会 500；"
                         f"这是脚本的前置条件，不是被测代码的问题）")
    except urllib.error.URLError as e:
        raise SystemExit(f"✗ 连不上 {api}：{e.reason}\n"
                         f"  先起服务：python -m uvicorn service:app --port 8000")


def _load_questions(n: int) -> list[dict]:
    path = os.path.join(ROOT, "evalset", "heldout", "questions.json")
    raw = json.load(open(path, encoding="utf-8"))
    qs = raw if isinstance(raw, list) else raw["questions"]
    return [q if isinstance(q, dict) else {"question": q} for q in qs[:n]]


def _fields(srcs: list[dict]) -> list[dict]:
    """只取参与渲染的三个字段 —— 别的字段（reason / rerank_score）不喂给模型。"""
    return [{k: s.get(k) for k in ("doc", "text", "score")} for s in srcs]


def _render_md(d: dict, per_q: list[dict]) -> str:
    """人读的那份：**每一行都是机器能抽的 `- key：value`**（判据见 check_artifact_sanity）。

    ★ 为什么要顺手写 md：这个仓库里"给人看的结论"和"给机器校的数字"必须同源。
      只写 json ⇒ README 引用数字时没人能一眼看出它从哪来；
      只写 md ⇒ 没有可重算的明细。两份一起写，且**同一趟生成**，才谈得上同源。
    """
    n = d["n_pairs"]
    lines = [
        "# A/B 对照：`/search`（只检索、不生成）与 `/chat?use_rerank=False`（检索 + 白跑生成）",
        "",
        "> 由 `scripts/ab_search_vs_chat.py` 生成 —— **别手改**：数字由检查器对着 "
        "`report-search-ab.json` 校。",
        "> 背景：`search_policy` 原来借道 `/chat?use_rerank=False` 拿原文条款，"
        "而它只读 `sources` ⇒ 服务内部那次生成**没有任何人读**。",
        "",
        f"- 生成时间：{d['generated_at']}",
        f"- 服务地址：{d['api']}",
        f"- 配对题数：{n}（同题、同 top_k=5；题集 `evalset/heldout/questions.json` 前 "
        f"{n} 题）",
        f"- sources 逐字段一致：{sum(r['fields_identical'] for r in per_q)}/{n}",
        f"- 渲染文本逐字节一致：{sum(r['text_identical'] for r in per_q)}/{n}"
        f"（`mcp/tools_impl._format_sources`，即模型真正读到的那段）",
        f"- 命中判断一致：{sum(r['hit_agrees'] for r in per_q)}/{n}",
        f"- /search 合计 token：{d['search_total_tokens']}"
        f"（单次响应最多 {d['search_max_calls_per_response']} 次 LLM 调用）",
        f"- 旧路径 /chat 合计 token：{d['chat_total_tokens']}"
        f"（{d['chat_calls_total']} 次生成调用）",
        f"- 省下：{d['tokens_saved_total']} token = {d['tokens_saved_per_question']} token/题"
        f" = {d['tokens_saved_per_call']} token/次调用",
        f"- 折成钱（按 {d['price_per_mtoken']} 元/百万 token）："
        f"{d['yuan_saved_per_question']} 元/题　★ 只是量级参考，别当报价",
        "",
        "★ 未就绪（模型加载中）时两条路都返回空 sources、`knowledge_hit` 都为真 —— "
        "这一点是**照抄旧语义**的，见 `service.py::handle_search` 的注释。",
        "",
        "## 逐题",
        "",
        "| # | 问题 | 命中 | 条数 | 字段一致 | 文本一致 | /search token | 旧路径 token |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(per_q, 1):
        q = r["question"].replace("|", "\\|")
        lines.append(
            f"| {i} | {q} | {'是' if r['knowledge_hit'] else '否'} | {r['n_sources']} | "
            f"{'✓' if r['fields_identical'] else '✗'} | "
            f"{'✓' if r['text_identical'] else '✗'} | {r['search_tokens']} | "
            f"{r['chat_tokens']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=DEFAULT_API, help="RAG 服务地址")
    ap.add_argument("--n", type=int, default=20, help="比前 N 题")
    ap.add_argument("--out", default=DEFAULT_OUT, help="产物路径（JSON）")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--price-per-mtoken", type=float, default=1.0,
                    help="只用来把省下的 token 折成钱，默认按 1 元/百万 token（量级参考）")
    args = ap.parse_args()

    from mcp.tools_impl import _format_sources      # ★ 渲染的**唯一**实现，见它的 docstring

    per_q, n_bad = [], 0
    search_tok = chat_tok = 0
    for q in _load_questions(args.n):
        text = q["question"]
        a = _post(args.api, "/search", {"question": text, "top_k": 5}, args.timeout)
        b = _post(args.api, "/chat", {"question": text, "use_rerank": False, "top_k": 5},
                  args.timeout)
        sa, sb = _fields(a.get("sources") or []), _fields(b.get("sources") or [])
        # ③ 渲染后逐字节 —— 模型真正读到的那段文本
        ta = _format_sources(a.get("sources") or [])
        tb = _format_sources(b.get("sources") or [])
        ua = a.get("usage") or {}
        ub = b.get("usage") or {}
        rec = {
            "question": text,
            "knowledge_hit": bool(a.get("knowledge_hit")),
            "hit_agrees": a.get("knowledge_hit") == b.get("knowledge_hit"),
            "rerank_agrees": a.get("rerank_used") == b.get("rerank_used"),
            "n_sources": len(sa),
            "fields_identical": sa == sb,
            "text_identical": ta == tb,
            "search_tokens": int(ua.get("total_tokens") or 0),
            "search_calls": int(ua.get("n_calls") or 0),
            "chat_tokens": int(ub.get("total_tokens") or 0),
            "chat_calls": int(ub.get("n_calls") or 0),
        }
        rec["ok"] = bool(rec["hit_agrees"] and rec["rerank_agrees"]
                         and rec["fields_identical"] and rec["text_identical"]
                         and rec["search_tokens"] == 0 and rec["search_calls"] == 0)
        if not rec["ok"]:
            n_bad += 1
            print(f"✗ {text[:26]}：字段 {rec['fields_identical']} 文本 {rec['text_identical']} "
                  f"命中 {rec['hit_agrees']} /search 的账 {rec['search_tokens']}")
        search_tok += rec["search_tokens"]
        chat_tok += rec["chat_tokens"]
        per_q.append(rec)

    n = len(per_q)
    saved = chat_tok - search_tok
    out = {
        "generated_by": "scripts/ab_search_vs_chat.py",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "api": args.api,
        "n_pairs": n,
        "all_fields_identical": all(r["fields_identical"] for r in per_q),
        "all_text_identical": all(r["text_identical"] for r in per_q),
        "all_hits_agree": all(r["hit_agrees"] for r in per_q),
        "search_total_tokens": search_tok,
        "search_max_calls_per_response": max((r["search_calls"] for r in per_q), default=0),
        "chat_total_tokens": chat_tok,
        "chat_calls_total": sum(r["chat_calls"] for r in per_q),
        "tokens_saved_total": saved,
        "tokens_saved_per_question": round(saved / n, 1) if n else 0.0,
        "tokens_saved_per_call": (round(saved / sum(r["chat_calls"] for r in per_q), 1)
                                  if sum(r["chat_calls"] for r in per_q) else 0.0),
        "yuan_saved_per_question": round(saved / n / 1e6 * args.price_per_mtoken, 8) if n else 0.0,
        "price_per_mtoken": args.price_per_mtoken,
        "n_mismatch": n_bad,
        "per_question": per_q,
    }
    # ★ 先落盘再印结论：脚本"跑过了"和"结论好不好"是两件事，
    #   退出码只担保前者（结论由 CI 里的 check_artifact_sanity.py 校）。
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    md_path = os.path.splitext(args.out)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_md(out, per_q))

    print(f"配对 {n} 题：字段一致 {sum(r['fields_identical'] for r in per_q)}/{n}，"
          f"渲染文本逐字节一致 {sum(r['text_identical'] for r in per_q)}/{n}，"
          f"命中判断一致 {sum(r['hit_agrees'] for r in per_q)}/{n}")
    print(f"/search 合计 {search_tok} token（单次响应最多 "
          f"{out['search_max_calls_per_response']} 次 LLM 调用）"
          f"　旧路径 /chat 合计 {chat_tok} token")
    print(f"⇒ 省 {saved} token = {out['tokens_saved_per_question']} token/题"
          f" = {out['tokens_saved_per_call']} token/次调用")
    print(f"产物：{args.out} ／ {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""eval/agent_metrics.py —— 把 agent 的三条指标算出来，落成可比对、可复算的产物。

## 为什么不能直接拿 `/chat` 的基线做减法

`/chat` 基线的 `recall5` 是**粗捞 top-5**里有没有 gold；agent 返回的 `sources` 是
`/chat` 管道**精排后最多 3 条**。两个数的分子分母都不同 ⇒ 相减没有意义。
所以这里另起一个指标名 `gold_in_sources`（agent 返回的来源里有没有 gold），
并在报告里写明"它不是 recall5，别混用"—— 这个仓库以前就栽过"两把尺子并排"。

## 指标（分母各自算）

| 指标 | 定义 | 分母 |
|---|---|---|
| `gold_in_sources` | agent 返回的 `sources[].doc` 里出现 gold 的 (文件, 条款) | 应答 44 |
| `no_miss` | 没拒答且至少一条来源（应答题不该被"查不到"打发） | 应答 44 |
| `refuse_ok` | 拒答题 `refused == true` | 拒答 61 |
| `done_rate` | `stopped_reason == done`（完整跑完的占比） | 全部 105 |
| `avg_steps` / `avg_tokens` | 每题的步数与 token 均值 | 全部 105 |

## 用法

```bash
# 起服务（AGENT_ENABLED=1），然后
python eval/agent_metrics.py --live --base-url http://127.0.0.1:8031
# 只重渲染（不跑模型）
python eval/agent_metrics.py --from-json eval/reports/agent-baseline.json
```
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUT_JSON = os.path.join(_ROOT, "eval", "reports", "agent-baseline.json")
OUT_MD = os.path.join(_ROOT, "eval", "reports", "agent-baseline.md")

#: 进程启动时采一次状态（**必须在写任何产物之前**：先写 JSON 再 capture 会把自己算成脏）。
_STATE: dict | None = None


def _post(base: str, path: str, payload: dict, timeout: int = 300) -> dict:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _split_doc(doc: str) -> tuple[str, str]:
    """`/chat` 的 Source.doc 形如 `文件名｜条款`；拆不开就整条当文件名、条款留空。"""
    if "｜" in doc:
        a, _, b = doc.partition("｜")
        return a, b
    return doc, ""


def gold_hit(q: dict, sources: list[dict]) -> bool:
    gold = tuple(q.get("gold") or [])
    if not gold:
        return False
    for s in sources:
        doc, clause = _split_doc(str(s.get("doc") or ""))
        # ★ 条款写法可能带章节前缀（gold: `第三章-第十九条（三）`，来源: `第十八条`）——
        #   用"互相包含"而不是 `==`：这个仓库的 gold 匹配一直是这么做的（eval/summarize.py 同理）。
        if doc == gold[0] and (clause and (clause in gold[1] or gold[1] in clause)):
            return True
    return False


def run_live_chat(base: str, limit: int | None, only: str) -> list[dict]:
    """同一刻跑 `/chat`，把字段对齐成与 agent 同一张表。

    ★ 为什么必须"同一刻"：已入库的 `/chat` 基线是**另一批跑批**（不同时间、不同进程、
      甚至不同代码版本）。拿它跟今天的 agent 数并列，等于拿两把尺子量同一个东西 ——
      这个仓库以前就栽过（35/44 vs 30/44 那次）。
    """
    qs = json.load(open(os.path.join(_ROOT, "evalset", "heldout", "questions.json"),
                        encoding="utf-8"))["questions"]
    if only != "all":
        qs = [q for q in qs if q.get("type") == only]
    if limit:
        qs = qs[:limit]
    rows = []
    for i, q in enumerate(qs, 1):
        t0 = time.time()
        try:
            r = _post(base, "/chat", {"question": q["question"]})
        except Exception as e:                              # noqa: BLE001
            r = {"knowledge_hit": None, "_err": f"{type(e).__name__}: {e}"}
        hit = r.get("knowledge_hit")
        rows.append({
            "id": q["id"], "type": q["type"],
            "gold_in_sources": gold_hit(q, r.get("sources") or []) if q.get("type") == "answer" else None,
            "answered_with_src": bool(hit) and bool(r.get("sources")),
            "refused": (not hit) if hit is not None else None,
            "stopped_reason": "done" if r.get("_err") is None else "error",
            "steps": 1 if hit else 0,
            "rounds": 1,
            "tokens": int(((r.get("usage") or {}).get("n_calls") or 0) * 700),  # 只作量级参考
            "sec": round(time.time() - t0, 1),
            "error": r.get("_err"),
        })
        print(f"[{i}/{len(qs)}] {q['id']:<5} {q['type']:<7} chat hit={hit} src={len(r.get('sources') or [])}")
    return rows


def run_live(base: str, limit: int | None, only: str) -> list[dict]:
    qs = json.load(open(os.path.join(_ROOT, "evalset", "heldout", "questions.json"),
                        encoding="utf-8"))["questions"]
    if only != "all":
        qs = [q for q in qs if q.get("type") == only]
    if limit:
        qs = qs[:limit]
    rows = []
    for i, q in enumerate(qs, 1):
        t0 = time.time()
        try:
            r = _post(base, "/agent", {"question": q["question"]})
            err = None
        except Exception as e:                              # noqa: BLE001
            r, err = {}, f"{type(e).__name__}: {e}"
        rows.append({
            "id": q["id"], "type": q["type"],
            "gold_in_sources": gold_hit(q, r.get("sources") or []) if q.get("type") == "answer" else None,
            "answered_with_src": (not r.get("refused")) and bool(r.get("sources")),
            "refused": r.get("refused"),
            "stopped_reason": r.get("stopped_reason") or "error",
            "steps": len(r.get("steps") or []),
            "rounds": r.get("rounds") or 0,
            "tokens": r.get("tokens") or 0,
            "sec": round(time.time() - t0, 1),
            "error": err,
        })
        print(f"[{i}/{len(qs)}] {q['id']:<5} {q['type']:<7} {rows[-1]['stopped_reason']:<11}"
              f" src={len(r.get('sources') or [])} steps={rows[-1]['steps']} tok={rows[-1]['tokens']}")
    return rows


def _version_lines() -> list[str]:
    """与其它报告同款的四行头（没这四行，新鲜度检查会判 UNVERIFIABLE）。

    ★ 踩过一次坑：`capture()` 的层级是 `docs["dirs"]["docs/kb"]["hash"]`、
      python 在 `runtime["python"]` —— 我第一版按 `docs["docs/kb"]` 读，
      结果语料哈希**静默为空**，报告被判 UNVERIFIABLE（不报错，只是判不出来）。
    """
    from pathlib import Path

    import kb.runstate as rs
    st = _STATE if _STATE is not None else rs.capture(Path(_ROOT), note="eval/agent_metrics")
    git = st.get("git") or {}
    dirs = (st.get("docs") or {}).get("dirs") or {}
    dep = rs.deps_state(_ROOT)
    lay = rs.layout_state()
    kb = (dirs.get("docs/kb") or {}).get("hash") or ""
    inbox = (dirs.get("docs/inbox") or {}).get("hash") or ""
    return [
        f"- 运行指纹：`{st.get('fingerprint')}`（`kb/runstate.py`；git `{str(git.get('sha'))[:12]}`"
        f" dirty=`{git.get('dirty')}`，python {(st.get('runtime') or {}).get('python')}）",
        f"- 依赖指纹：{'、'.join(f'`{k}`={v}' for k, v in dep.items()) or '**未记录**'}"
        f"（`requirements*.txt` 的 sha256 前 8 位）",
        f"- 版面后端：auto 会选 `{lay.get('auto_would_use')}`；"
        f"可用 `{'、'.join(lay.get('available') or []) or '无'}`；PDF_BACKEND=`{lay.get('pdf_backend')}`",
        f"- 语料哈希：`docs/kb`={kb[:8]}；`docs/inbox`={inbox[:8]}",
    ]


def render(rows: list[dict], *, base: str = "", note: str = "", compare: list[dict] | None = None,
           title: str = "# agent 基线（105 题独立集，`/agent` 端到端）") -> str:
    ans = [r for r in rows if r["type"] == "answer"]
    ref = [r for r in rows if r["type"] == "refuse"]
    n = len(rows) or 1

    def pct(a: int, b: int) -> str:
        return f"{a}/{b}" if b else "—/0"

    gold = sum(1 for r in ans if r["gold_in_sources"])
    nomiss = sum(1 for r in ans if r["answered_with_src"])
    refok = sum(1 for r in ref if r["refused"] is True)
    done = sum(1 for r in rows if r["stopped_reason"] == "done")
    avg_steps = round(sum(r["steps"] for r in rows) / n, 2)
    avg_tok = round(sum(r["tokens"] for r in rows) / n)
    reasons: dict[str, int] = {}
    for r in rows:
        reasons[r["stopped_reason"]] = reasons.get(r["stopped_reason"], 0) + 1

    lines = [
        title,
        "",
        "> ★ 本文件由 `eval/agent_metrics.py` 生成，**不要手改**（下次生成会覆盖）。",
        f"> 跑法：{base or '（历史跑批）'}",
        "",
    ] + _version_lines() + [
        "",
        "## 结论",
        "",
        "| 指标 | 值 | 分母 | 说明 |",
        "|---|---|---|---|",
        f"| `gold_in_sources` | {pct(gold, len(ans))} | 应答 {len(ans)} | agent 返回的来源里有 gold 条款 |",
        f"| `no_miss` | {pct(nomiss, len(ans))} | 应答 {len(ans)} | 没拒答且带来源（应答题不该被「查不到」打发） |",
        f"| `refuse_ok` | {pct(refok, len(ref))} | 拒答 {len(ref)} | 拒答题真的拒了 |",
        f"| `done_rate` | {pct(done, len(rows))} | 全部 {len(rows)} | `stopped_reason == done` 的占比 |",
        f"| `avg_steps` | {avg_steps} | 全部 {len(rows)} | 每题工具调用次数 |",
        f"| `avg_tokens` | {avg_tok} | 全部 {len(rows)} | 每题 token（含循环里的每一轮） |",
        "",
        "**收尾原因分布**：" + "、".join(f"`{k}` {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])),
        "",
        "## 两条口径警告（别把这两个数相减）",
        "",
        "1. **`gold_in_sources` ≠ `recall5`**：前者算的是 agent 返回的 `sources`（`/chat` 精排后最多 3 条），",
        "   后者算的是粗捞 top-5。分子分母都不同，**相减没有意义**。",
        "2. **agent 与 `/chat` 是两个被测对象**：`eval/gate.py` 的配对检验只允许同一层的同一条路径",
        "   （否则「换了被测对象」会被当成「改动有效」）。要比较只能并列看，不能做 McNemar。",
        "",
    ]
    if compare:
        def agg(rs):
            a = [r for r in rs if r["type"] == "answer"]
            f = [r for r in rs if r["type"] == "refuse"]
            return (
                sum(1 for r in a if r["gold_in_sources"]),
                sum(1 for r in a if r["answered_with_src"]),
                sum(1 for r in f if r["refused"] is True),
                len(a), len(f),
            )

        ag = agg(rows)
        ch = agg(compare)
        lines += [
            "",
            "## 同一刻 `/chat` 对照（同一批题、同一进程、同一时刻）",
            "",
            "> ★ 这张表的两个数来自**同一刻**的两次跑批（`agent-baseline.json` 与",
            "> `chat-baseline-same-moment.json`），不是拿历史基线比 —— 拿历史基线比会把",
            "> 「不同时间/不同代码」误当成「不同方法」。",
            "",
            "| 指标 | `/agent` | `/chat`（同刻） | 差 |",
            "|---|---|---|---|",
            f"| `gold_in_sources` | {ag[0]}/{ag[3]} | {ch[0]}/{ch[3]} | {ag[0] - ch[0]:+d} |",
            f"| `no_miss` | {ag[1]}/{ag[3]} | {ch[1]}/{ch[3]} | {ag[1] - ch[1]:+d} |",
            f"| `refuse_ok` | {ag[2]}/{ag[4]} | {ch[2]}/{ch[4]} | {ag[2] - ch[2]:+d} |",
            "",
            "**结论**：agent 在这三条上**全面更差**，拒答差距最大。",
            "",
            "机制**已用单题复现钉住**（不是猜）：`s10`（问「退学后还能回来接着读吗」）",
            "第一步 `ask_policy(原问题)` 返回 `no_hit`（走的正是 `/chat` 那道闸），",
            "第二步模型改写问法 `search_policy(\"退学 复学 重新入学\")` **命中 3 条**，",
            "返回的来源是 `学生应征入伍管理办法` —— 与问题无关。",
            "而 loop 的 `refused` 判定是「**任何一次**命中即不拒答」⇒ 这条路把拒答绕过去了。",
            "",
            "⇒ 缺陷在**循环的拒答语义**，不在工具：应当以「原问题的证据」为准，",
            "以及限制「换个问法重试直到命中」这种路径。",
            "",
            "⇒ 产品结论：`/agent` **不能作为默认路径**（`AGENT_ENABLED` 默认 off 是对的）；",
            "要上默认，先加一条「拒答口径以**原问题**为准」的守卫。",
            "",
        ]
    lines += ["", "## 逐题", "", "| 题号 | 类型 | 收尾 | gold | 有来源 | 步数 | token | 秒 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['type']} | {r['stopped_reason']} | "
                     f"{'✓' if r['gold_in_sources'] else ('—' if r['gold_in_sources'] is None else '✗')} | "
                     f"{'✓' if r['answered_with_src'] else '✗'} | {r['steps']} | {r['tokens']} | {r['sec']} |")
    if note:
        lines += ["", "## 备注", "", note]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--base-url", default="http://127.0.0.1:8031")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="all", choices=["all", "answer", "refuse"])
    ap.add_argument("--from-json", default="")
    ap.add_argument("--chat", action="store_true")
    ap.add_argument("--compare", default="", help="同一刻 /chat 的 JSON，用于生成对照表")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    global _STATE
    from pathlib import Path as _P

    import kb.runstate as _rs
    _STATE = _rs.capture(_P(_ROOT), note="eval/agent_metrics")   # ★ 先采状态，再写产物

    if a.live and a.chat:
        rows = run_live_chat(a.base_url, a.limit or None, a.only)
        base = f"`{a.base_url}` 的 `/chat`（同一刻现跑）"
    elif a.live:
        rows = run_live(a.base_url, a.limit or None, a.only)
        base = f"`{a.base_url}`（现跑）"
    elif a.from_json:
        rows = json.load(open(a.from_json, encoding="utf-8"))
        base = f"从 `{os.path.basename(a.from_json)}` 重渲染"
    else:
        print("要么 --live，要么 --from-json", file=sys.stderr)
        return 2

    global OUT_JSON, OUT_MD
    if a.chat:                                # ★ 同刻对照写自己的文件，不覆盖 agent 基线（--from-json 也算）
        OUT_JSON = OUT_JSON.replace("agent-baseline", "chat-baseline-same-moment")
        OUT_MD = OUT_MD.replace("agent-baseline", "chat-baseline-same-moment")
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(rows, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    cmp_rows = json.load(open(a.compare, encoding="utf-8")) if a.compare else None
    open(OUT_MD, "w", encoding="utf-8").write(
        render(rows, base=base, note=a.note, compare=cmp_rows,
               title=("# `/chat` 同刻基线（105 题，与 agent 同一批）" if a.chat
                      else "# agent 基线（105 题独立集，`/agent` 端到端）")))

    ans = [r for r in rows if r["type"] == "answer"]
    ref = [r for r in rows if r["type"] == "refuse"]
    print(f"\ngold_in_sources {sum(1 for r in ans if r['gold_in_sources'])}/{len(ans)}"
          f"　no_miss {sum(1 for r in ans if r['answered_with_src'])}/{len(ans)}"
          f"　refuse_ok {sum(1 for r in ref if r['refused'] is True)}/{len(ref)}")
    print(f"已写 {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

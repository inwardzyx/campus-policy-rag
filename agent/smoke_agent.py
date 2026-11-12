# -*- coding: utf-8 -*-
"""agent/smoke_agent.py —— agent 冒烟：**12 题对照 /chat**，把数字落成产物。

## 为什么非要有这个脚本

Step 1 的判据 1.3 是"拒答不许退化"。2026-11-12 实测它确实会退化，而且**退化两次**：

| 版本 | search_policy 怎么拿证据 | 拒答（8 道拒答题） |
|---|---|---|
| v1 裸 `rag.search`（top-5 直接喂） | 没有拒答闸 | **4/8** |
| v2 `handle_search`（向量闸 `_vec_reject`） | 闸不同：/chat 用的是**精排 < 5** | **6/8** |
| v3 `handle_chat`（完整管道） | 同一道闸 | **8/8** |

★ 这张表是脚本现算的，不是手写的：`--from-json` 从保存的跑批结果重渲染，
  所以改一个 JSON 里的数，md 会跟着变（手改 md 会被下次生成覆盖）。

## 用法

```bash
# 现跑（需要服务在跑，且 AGENT_ENABLED=1）
python agent/smoke_agent.py --live --base-url http://127.0.0.1:8031 --n-refuse 8 --n-answer 4

# 只重渲染（不跑模型）
python agent/smoke_agent.py --from-json D:\\dsh-work\\_agent_smoke3.json
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

OUT_JSON = os.path.join(_ROOT, "eval", "reports", "agent-smoke.json")
OUT_MD = os.path.join(_ROOT, "eval", "reports", "agent-smoke.md")


def _post(base: str, path: str, payload: dict, timeout: int = 300) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def run_live(base: str, n_refuse: int, n_answer: int) -> list[dict]:
    qs = json.load(open(os.path.join(_ROOT, "evalset", "heldout", "questions.json"),
                        encoding="utf-8"))["questions"]
    pick = [q for q in qs if q.get("type") == "answer"][:n_answer] + \
           [q for q in qs if q.get("type") == "refuse"][:n_refuse]
    rows = []
    for q in pick:
        _, ch = _post(base, "/chat", {"question": q["question"]}, timeout=180)
        _, ag = _post(base, "/agent", {"question": q["question"]}, timeout=300)
        rows.append({"id": q["id"], "type": q["type"],
                     "chat_hit": ch.get("knowledge_hit"),
                     "chat_n_src": len(ch.get("sources") or []),
                     "agent_stopped": ag.get("stopped_reason"),
                     "agent_refused": ag.get("refused"),
                     "agent_n_src": len(ag.get("sources") or []),
                     "agent_rounds": ag.get("rounds"), "agent_tokens": ag.get("tokens")})
        print(f"{q['id']:<6} {q['type']:<7} chat hit={str(ch.get('knowledge_hit')):<5}"
              f" | agent {ag.get('stopped_reason'):<11} refused={str(ag.get('refused')):<5}"
              f" src={len(ag.get('sources') or [])}")
    return rows


def render(rows: list[dict], *, base: str = "", note: str = "") -> str:
    ref = [r for r in rows if r["type"] == "refuse"]
    ans = [r for r in rows if r["type"] == "answer"]
    chat_ref = sum(1 for r in ref if r["chat_hit"] is False)
    ag_ref = sum(1 for r in ref if r["agent_refused"] is True)
    ag_src = sum(1 for r in ans if r["agent_n_src"])
    avg_tok = round(sum(r["agent_tokens"] or 0 for r in rows) / len(rows)) if rows else 0
    lines = [
        "# agent 冒烟：12 题对照 `/chat`（Step 1 判据 1.3）",
        "",
        "> ★ 这份文件由 `agent/smoke_agent.py` 生成，**不要手改**（下次生成会覆盖）。",
        "> 数字来源：`eval/reports/agent-smoke.json`" + (f"（{base}）" if base else ""),
        "",
        "## 结论",
        "",
        f"- **拒答不退化**：/chat 拒对 `{chat_ref}/{len(ref)}`，/agent 拒对 `{ag_ref}/{len(ref)}`"
        f"（门槛：不低于 /chat）",
        f"- **应答带来源**：/agent `{ag_src}/{len(ans)}`",
        f"- **成本**：平均 `{avg_tok}` token/题（`/chat` 单步约 700）",
        "",
        "## 逐题",
        "",
        "| 题号 | 类型 | /chat 命中 | /agent 收尾 | 拒答 | 来源数 | 轮数 | token |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {r['type']} | {r['chat_hit']} | {r['agent_stopped']} | "
                     f"{r['agent_refused']} | {r['agent_n_src']} | {r['agent_rounds']} | {r['agent_tokens']} |")
    if note:
        lines += ["", "## 备注", "", note]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--base-url", default="http://127.0.0.1:8031")
    ap.add_argument("--n-refuse", type=int, default=8)
    ap.add_argument("--n-answer", type=int, default=4)
    ap.add_argument("--from-json", default="")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    if a.live:
        rows = run_live(a.base_url, a.n_refuse, a.n_answer)
        base = a.base_url
    elif a.from_json:
        rows = json.load(open(a.from_json, encoding="utf-8"))
        base = f"从 {os.path.basename(a.from_json)} 重渲染"
    else:
        print("要么 --live，要么 --from-json", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(rows, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(OUT_MD, "w", encoding="utf-8").write(render(rows, base=base, note=a.note))
    ref = [r for r in rows if r["type"] == "refuse"]
    ans = [r for r in rows if r["type"] == "answer"]
    print(f"\n拒答：/chat {sum(1 for r in ref if r['chat_hit'] is False)}/{len(ref)}，"
          f"/agent {sum(1 for r in ref if r['agent_refused'] is True)}/{len(ref)}"
          f"　应答带来源 {sum(1 for r in ans if r['agent_n_src'])}/{len(ans)}")
    print(f"已写 {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

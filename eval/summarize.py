# -*- coding: utf-8 -*-
"""eval/summarize.py —— 把 promptfoo 的输出 JSON 折成一份能入库的基线报告

为什么还要这一步（promptfoo 自己就会打表）：
  ① promptfoo 的表是给"看"的，不是给"引"的：README/简历里引的数字必须能指回一份产物；
  ② 三条业务指标的分母不一样（44 / 44 / 61），靠 promptfoo 的总体 pass rate 会被
     61 道拒答题稀释掉，看不出"应答层悄悄退化了"；
  ③ `--compare` 能把 `evalset/report-*.md`（run_eval.py 的旧报告）读进来逐项对账 ——
     两个 harness 用同一份判分实现，数字不一致就说明接线错了，必须报红。

跑法：
    python eval/summarize.py --input eval/reports/promptfoo-offline.json \
        --out eval/BASELINE-offline.md \
        --compare evalset/report-vector-heldout.md
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 指标名 → (适用题型, 人话说明, run_eval 报告里的口径名)
METRICS = {
    "recall5": ("answer", "gold 条款进了粗捞 top-5", "Recall@5"),
    "no_miss": ("answer", "应答题没被误拒（knowledge_hit==true）", "漏答率（反向）"),
    "refuse_ok": ("refuse", "拒答题拒对了（knowledge_hit==false）", "拒答准确率"),
}


def _reasons(case: dict) -> dict[str, str]:
    """每个断言为什么那样判（promptfoo 把逐条断言的原因放在 componentResults 里）。"""
    out = {}
    comps = (case.get("gradingResult") or {}).get("componentResults") or []
    asserts = ((case.get("testCase") or {}).get("assert")) or []
    for i, c in enumerate(comps):
        name = None
        if i < len(asserts) and isinstance(asserts[i], dict):
            name = asserts[i].get("metric")
        if name:
            out[name] = str(c.get("reason") or "")
    return out


def load_cases(path: Path) -> list[dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in d["results"]["results"]:
        out = c.get("response", {}).get("output")
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except json.JSONDecodeError:
                out = {"error": "输出不是 JSON"}
        out = out or {}
        cases.append({
            "qid": (c.get("vars") or {}).get("qid") or out.get("qid"),
            "type": out.get("type"),
            "layer": out.get("layer"),
            "boot": out.get("boot") or {},
            "scores": c.get("namedScores") or {},
            "reasons": _reasons(c),
            "latency_ms": c.get("latencyMs"),
            "error": out.get("error"),
            "answer": out.get("answer"),
            "gold_rank": out.get("gold_rank"),
            "cands": out.get("cands") or [],
        })
    return cases


def tally(cases: list[dict]) -> dict:
    """每项指标只统计「适用它的那些题」——分母必须各自算，混在一起就是个假分数。"""
    res = {}
    for name, (qtype, _, _) in METRICS.items():
        sub = [c for c in cases if c["type"] == qtype]
        passed = [c for c in sub if c["scores"].get(name) == 1]
        res[name] = {"pass": len(passed), "total": len(sub)}
    return res


def parse_run_eval_report(path: Path) -> dict:
    """从 run_eval.py 产出的 .md 里读回三条数（不手抄任何数字）。"""
    txt = path.read_text(encoding="utf-8")
    got = {}
    m = re.search(r"Recall@5：(\d+)/(\d+)", txt)
    if m:
        got["recall5"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"漏答率：(\d+)/(\d+)", txt)
    if m:
        got["no_miss"] = (int(m.group(2)) - int(m.group(1)), int(m.group(2)))
    m = re.search(r"拒答准确率：(\d+)/(\d+)", txt)
    if m:
        got["refuse_ok"] = (int(m.group(1)), int(m.group(2)))
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="promptfoo -o 写出的 json")
    ap.add_argument("--out", required=True, help="要写的 md")
    ap.add_argument("--compare", default=None, help="evalset/report-*.md，用来对账")
    args = ap.parse_args()

    cases = load_cases(Path(args.input))
    cases.sort(key=lambda c: c["qid"] or "")
    n = tally(cases)
    layer = next((c["layer"] for c in cases if c["layer"]), "?")
    boot = next((c["boot"] for c in cases if c.get("boot")), {})

    lines = [
        f"# promptfoo 基线（held-out {len(cases)} 题）",
        "",
        f"- 评测层：**{layer}**（由 case_server 的 EVAL_LAYER 决定，这里是服务自报的值）",
        f"- 语料：入库 {boot.get('chunks', '?')} 块 / 拦下 {boot.get('rejected', '?')} 块",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 数据来源：`{Path(args.input).as_posix()}`（本报告由 `eval/summarize.py` 生成，数字不手抄）",
        "",
        "| 指标 | 通过/总数 | 口径 |",
        "|---|---|---|",
    ]
    for name, (_, desc, _) in METRICS.items():
        r = n[name]
        lines.append(f"| `{name}` | {r['pass']}/{r['total']} | {desc} |")

    # ★ 对账结果必须反映到**退出码**上（2026-11-11 实测踩到：
    #   第一版只 print 了 [FAIL]，函数照样 return 0 —— 一条只在屏幕上报红、
    #   退出码是绿的判据，接到 CI/钩子上就等于不存在。变异测试当场抓住了它。）
    n_mismatch = 0
    if args.compare:
        ref = parse_run_eval_report(Path(args.compare))
        lines += ["", f"## 与 run_eval.py 对账（`{Path(args.compare).as_posix()}`）", "",
                  "| 指标 | 本报告 | run_eval | 一致 |", "|---|---|---|---|"]
        for name in METRICS:
            mine = (n[name]["pass"], n[name]["total"])
            theirs = ref.get(name)
            if theirs is None:
                lines.append(f"| `{name}` | {mine[0]}/{mine[1]} | 读不到 | ⚠ 缺失 |")
                n_mismatch += 1
                continue
            same = mine == theirs
            n_mismatch += 0 if same else 1
            lines.append(f"| `{name}` | {mine[0]}/{mine[1]} | {theirs[0]}/{theirs[1]} | "
                         f"{'✅' if same else '❌ 不一致'} |")
        if n_mismatch == 0:
            print(f"[ok] 与 {args.compare} 逐项一致")
        else:
            print(f"[FAIL] 与 {args.compare} 有 {n_mismatch} 项不一致 —— "
                  f"两个 harness 用的是同一份判分实现，不一致说明接线/口径出了问题",
                  file=sys.stderr)

    lines += ["", "## 逐题明细", "", "| qid | 类型 | Recall@5 | 未漏答 | 拒答正确 | gold 排名 | 说明 |",
              "|---|---|---|---|---|---|---|"]
    for c in cases:
        def mark(name):
            if name not in METRICS or c["type"] != METRICS[name][0]:
                return "—"
            return "✅" if c["scores"].get(name) == 1 else "❌"
        note = ""
        if c["error"]:
            note = f"ERROR: {c['error']}"
        elif c["type"] == "answer" and c["scores"].get("recall5") != 1:
            note = "gold 没进前 5 → 检索问题"
        elif c["type"] == "answer" and c["scores"].get("no_miss") != 1:
            note = "gold 进了/没进却仍被拒 → 判据问题"
        elif c["type"] == "refuse":
            note = "" if c["scores"].get("refuse_ok") == 1 else "被误答 → 判据太松"
        lines.append(f"| {c['qid']} | {c['type']} | {mark('recall5')} | {mark('no_miss')} | "
                     f"{mark('refuse_ok')} | {c['gold_rank'] if c['gold_rank'] is not None else '—'} | {note} |")

    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] 已写 {args.out}")
    for name, (_, desc, _) in METRICS.items():
        print(f"  {name:10s} {n[name]['pass']}/{n[name]['total']}   {desc}")
    return 1 if n_mismatch else 0


if __name__ == "__main__":
    raise SystemExit(main())

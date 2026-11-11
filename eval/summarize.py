# -*- coding: utf-8 -*-
"""eval/summarize.py —— 把 promptfoo 的输出 JSON 折成能入库的报告（数字全部现算）

三种用法：

1）出一份逐题报告（默认）：
    python eval/summarize.py --input eval/reports/promptfoo-offline.json \\
        --out eval/reports/BASELINE-offline.md \\
        --compare eval/reports/run_eval-offline-heldout.log

2）刷新 README 里那块基线表（只改两个 marker 之间的内容，其余一个字不动）：
    python eval/summarize.py --render-index --md eval/README.md

3）对账失败时的退出码：非 0（判据红了，退出码也得红 —— 见 eval/README.md 坑 6）。

为什么还要这一步（promptfoo 自己就会打表）：
  ① promptfoo 的表是给"看"的，不是给"引"的：README/简历里引的数字必须能指回一份产物；
  ② 三条业务指标的分母不一样（应答题 / 应答题 / 拒答题），靠 promptfoo 的总体 pass rate
     会被拒答题稀释掉，看不出"应答层悄悄退化了"；
  ③ `--compare` 能把 run_eval.py 的报告读进来逐项对账 —— 两个 harness 用同一份判分实现，
     数字不一致就说明接线错了，必须报红。

★ 口径的代码级来源是本文件下面的 `METRICS` 表；人类可读版见 `eval/README.md` 的「三条指标与它们的分母」一节。
★ 本文件里**没有任何写死的指标数字**：分母来自题目文件、分子来自跑批 JSON。
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ★ 口径的**代码级唯一来源**就是下面这张表；人类可读版在 eval/README.md 的「三条指标」一节。
#   为什么不再单开 metrics_spec.py（2026-11-11 盲审）：本仓已有 kb/evalkit/metrics.py 承担
#   evalkit 那套指标的「登记 + 缺失语义」；再为三条 heldout 指标开一个新模块，
#   等于给单人单仓加一层没有第二个消费者的抽象。
TYPE_ANSWER = "answer"
TYPE_REFUSE = "refuse"
METRICS = {
    "recall5": {"type": TYPE_ANSWER, "desc": "gold 条款进了粗捞 top-5（Recall@5）",
                "denominator": "应答题数"},
    "no_miss": {"type": TYPE_ANSWER, "desc": "应答题没被误拒（knowledge_hit == true）",
                "denominator": "应答题数"},
    "refuse_ok": {"type": TYPE_REFUSE, "desc": "拒答题拒对了（knowledge_hit == false）",
                  "denominator": "拒答题数"},
}

#: 只有 online 层的结果能谈「系统的拒答能力」（offline 是单闸 0.55 的边界）
REFUSAL_LAYERS = ("online",)

DISCIPLINE = [
    "漏答率 ≠ Recall 的补集：一个看 gold 有没有进前 5，一个看管道最后有没有拒答。",
    "拒答正确率只能用 online 层；offline 的拒答数是单闸（向量分 0.55）边界，不代表系统能力。",
    "两层数字不可比，只能并列。",
]


def applies_to(name: str, qtype: str) -> bool:
    """这个指标算不算在这道题上（分母就是按这个筛出来的）。"""
    return METRICS[name]["type"] == qtype

BEGIN = "<!-- BEGIN:BASELINE"
END = "<!-- END:BASELINE -->"


def _reasons(case: dict) -> dict[str, str]:
    """每条断言为什么那样判（promptfoo 把逐条断言的原因放在 componentResults 里）。"""
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
    """每项指标只统计"适用它的那些题"——分母必须各自算，混在一起就是个假分数。"""
    res = {}
    for name in METRICS:
        qtype = METRICS[name]["type"]
        sub = [c for c in cases if c["type"] == qtype]
        passed = [c for c in sub if c["scores"].get(name) == 1]
        res[name] = {"pass": len(passed), "total": len(sub)}
    return res


def parse_run_eval_text(txt: str) -> dict:
    """从 run_eval.py 的输出现场读回三条数（不手抄任何数字）。"""
    got = {}
    m = re.search(r"Recall@5：(\d+)/(\d+)", txt)
    if m:
        got["recall5"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"漏答率：(\d+)/(\d+)", txt)
    if m:
        # run_eval 报的是"漏答"，本仓库的指标是"没漏答" ⇒ 取补。这一步是**唯一**允许的换算，
        # 因为两个数说的是同一件事的正反面（不是 Recall 与漏答那种两码事）。
        got["no_miss"] = (int(m.group(2)) - int(m.group(1)), int(m.group(2)))
    m = re.search(r"拒答准确率：(\d+)/(\d+)", txt)
    if m:
        got["refuse_ok"] = (int(m.group(1)), int(m.group(2)))
    return got


def parse_run_eval_report(path: Path) -> dict:
    return parse_run_eval_text(path.read_text(encoding="utf-8"))


def _fingerprint_lines(boot: dict) -> list[str]:
    """报告头的"这是同一个系统吗"三行。

    ★ 缺失时写「未记录」而不是省略 —— 省略会让读者以为"这版没有指纹这个概念"，
      而不是"这次跑批没接上指纹"（缺失 ≠ 零）。
    """
    rs = boot.get("runstate") or {}
    if not rs.get("fingerprint"):
        return ["- 运行指纹：**未记录**（该跑批用的 case_server 还没有接 `kb/runstate.py` 指纹）"]
    docs = "、".join(f"`{k}`={str(v)[:8]}" for k, v in (rs.get("docs") or {}).items()) or "—"
    corp = "、".join(f"`{k}`={str(v)[:8]}" for k, v in (rs.get("corpus") or {}).items()) or "—"
    return [
        f"- 运行指纹：`{rs['fingerprint']}`（`kb/runstate.py`；git `{str(rs.get('git_sha'))[:12]}`"
        f" dirty=`{rs.get('git_dirty')}`，python {rs.get('python')}）",
        f"- 语料哈希：{docs}；企业 demo：{corp}（不参与本评测）",
        "- 切块与索引**不物化**：它们是（语料哈希 × 代码版本）的确定函数 ⇒ **指纹即索引版本**。",
    ]


def _metric_table(n: dict) -> list[str]:
    lines = ["| 指标 | 通过/总数 | 口径 | 分母 |", "|---|---|---|---|"]
    for name, m in METRICS.items():
        r = n[name]
        lines.append(f"| `{name}` | {r['pass']}/{r['total']} | {m['desc']} | {m['denominator']} |")
    return lines


def render_case_report(cases: list[dict], src: Path, compare: Path | None) -> str:
    n = tally(cases)
    layer = next((c["layer"] for c in cases if c["layer"]), "?")
    boot = next((c["boot"] for c in cases if c.get("boot")), {})

    lines = [
        f"# promptfoo 基线（held-out {len(cases)} 题）",
        "",
        f"- 评测层：**{layer}**（由 case_server 的 EVAL_LAYER 决定，这里是服务自报的值）",
        f"- 评测语料：docs/ 入库 {boot.get('docs_chunks', '?')} 块 / 拦下 {boot.get('rejected', '?')} 块"
        f"（另有企业 demo {boot.get('enterprise_chunks', '?')} 块**不参与**本评测 ——"
        f" 评测请求不带 namespace，`rag.search()` 只查 default 语料）",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        *_fingerprint_lines(boot),
        f"- 数据来源：`{src.as_posix()}`（本报告由 `eval/summarize.py` 生成，数字不手抄）",
        "",
        *_metric_table(n),
        "",
        "**口径纪律**（定义见 `eval/summarize.py` 的 `METRICS`，别在这份报告里另立一套）：",
    ]
    lines += [f"- {d}" for d in DISCIPLINE]
    if not (layer in REFUSAL_LAYERS):
        lines += ["", f"> ⚠️ 本层是 `{layer}` ⇒ **`refuse_ok` 这一行不能用来谈「系统的拒答能力」**，"
                       "它是单闸（向量分 0.55）边界；要谈拒答请跑 `EVAL_LAYER=online`。"]

    if compare:
        ref = parse_run_eval_report(compare)
        lines += ["", f"## 与 run_eval.py 对账（`{compare.as_posix()}`）", "",
                  "| 指标 | 本报告 | run_eval | 一致 |", "|---|---|---|---|"]
        for name in METRICS:
            mine = (n[name]["pass"], n[name]["total"])
            theirs = ref.get(name)
            if theirs is None:
                lines.append(f"| `{name}` | {mine[0]}/{mine[1]} | 读不到 | ⚠ 缺失 |")
                continue
            same = mine == theirs
            lines.append(f"| `{name}` | {mine[0]}/{mine[1]} | {theirs[0]}/{theirs[1]} | "
                         f"{'✅' if same else '❌ 不一致'} |")

    lines += ["", "## 逐题明细", "",
              "| qid | 类型 | Recall@5 | 未漏答 | 拒答正确 | gold 排名 | 说明 |",
              "|---|---|---|---|---|---|---|"]
    for c in sorted(cases, key=lambda c: c["qid"] or ""):
        def mark(name: str) -> str:
            if name not in METRICS or not applies_to(name, c["type"]):
                return "—"
            return "✅" if c["scores"].get(name) == 1 else "❌"
        note = ""
        if c["error"]:
            note = f"ERROR: {c['error']}"
        elif c["type"] == TYPE_ANSWER and c["scores"].get("recall5") != 1:
            note = "gold 没进前 5 → 检索问题"
        elif c["type"] == TYPE_ANSWER and c["scores"].get("no_miss") != 1:
            note = "gold 进了/没进却仍被拒 → 判据问题"
        elif c["type"] == TYPE_REFUSE:
            note = "" if c["scores"].get("refuse_ok") == 1 else "被误答 → 判据太松"
        lines.append(f"| {c['qid']} | {c['type']} | {mark('recall5')} | {mark('no_miss')} | "
                     f"{mark('refuse_ok')} | {c['gold_rank'] if c['gold_rank'] is not None else '—'} "
                     f"| {note} |")
    return "\n".join(lines) + "\n"


def render_index(md_path: Path) -> int:
    """刷新 README 里两个 marker 之间的基线表（只认 reports/promptfoo-*.json）。"""
    jsons = sorted((Path(__file__).resolve().parent / "reports").glob("promptfoo-*.json"))
    if not jsons:
        print("[FAIL] reports/promptfoo-*.json 一份都没有 —— 扫到 0 条不许报绿", file=sys.stderr)
        return 1

    rows, layers = [], []
    for p in jsons:
        cases = load_cases(p)
        n = tally(cases)
        layer = next((c["layer"] for c in cases if c["layer"]), "?")
        layers.append(layer)
        rows.append((layer, n, p.name, len(cases)))

    out = [f"{BEGIN} （由 eval/summarize.py --render-index 生成；手改会被下次生成覆盖） -->",
           "",
           "| 层 | `recall5` | `no_miss` | `refuse_ok` | 题数 | 产物 |",
           "|---|---|---|---|---|---|"]
    for layer, n, name, cnt in rows:
        out.append(f"| {layer} | {n['recall5']['pass']}/{n['recall5']['total']} "
                   f"| {n['no_miss']['pass']}/{n['no_miss']['total']} "
                   f"| {n['refuse_ok']['pass']}/{n['refuse_ok']['total']} | {cnt} | `reports/{name}` |")
    out += ["", f"> 口径定义见 `eval/summarize.py` 的 `METRICS`；本表来自 {len(jsons)} 份跑批 JSON。"]
    bad = [l for l in layers if not (l in REFUSAL_LAYERS)]
    if bad:
        out.append(f"> ⚠️ 层 `{'`、`'.join(bad)}` 的 `refuse_ok` 不能用来谈「系统的拒答能力」："
                   "offline 层拒答只靠向量分 0.55 一道闸，那是已知边界。")
    out.append(END)

    txt = md_path.read_text(encoding="utf-8")
    if BEGIN not in txt or END not in txt:
        print(f"[FAIL] {md_path} 里找不到 marker（{BEGIN} / {END}），不敢瞎插", file=sys.stderr)
        return 1
    head = txt.split(BEGIN)[0]
    tail = txt.split(END, 1)[1]
    md_path.write_text(head + "\n".join(out) + tail, encoding="utf-8")
    print(f"[ok] 已刷新 {md_path} 的基线表（{len(jsons)} 份 JSON：{', '.join(layers)}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="promptfoo -o 写出的 json")
    ap.add_argument("--out", help="要写的 md")
    ap.add_argument("--compare", default=None, help="run_eval 的报告/日志，用来对账")
    ap.add_argument("--render-index", action="store_true", help="刷新 README 里的基线表")
    ap.add_argument("--md", default=None, help="配合 --render-index：要刷新的 md")
    args = ap.parse_args()

    if args.render_index:
        if not args.md:
            ap.error("--render-index 必须配 --md")
        return render_index(Path(args.md))

    if not (args.input and args.out):
        ap.error("默认模式需要 --input 和 --out（或用 --render-index）")

    cases = load_cases(Path(args.input))
    report = render_case_report(cases, Path(args.input), Path(args.compare) if args.compare else None)
    Path(args.out).write_text(report, encoding="utf-8")

    n = tally(cases)
    print(f"[ok] 已写 {args.out}")
    for name, m in METRICS.items():
        print(f"  {name:10s} {n[name]['pass']}/{n[name]['total']}   {m['desc']}")

    # ★ 对账结果必须反映到**退出码**上（第一版只 print 红字、返回 0 —— 变异用例当场抓住）
    if not args.compare:
        return 0
    ref = parse_run_eval_report(Path(args.compare))
    bad = 0
    for name in METRICS:
        mine = (n[name]["pass"], n[name]["total"])
        theirs = ref.get(name)
        if theirs is None or mine != theirs:
            bad += 1
    if bad:
        print(f"[FAIL] 与 {args.compare} 有 {bad} 项不一致 —— 两个 harness 用的是同一份判分实现，"
              f"不一致说明接线/口径出了问题", file=sys.stderr)
        return 1
    print(f"[ok] 与 {args.compare} 逐项一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

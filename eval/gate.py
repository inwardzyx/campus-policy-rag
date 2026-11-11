# -*- coding: utf-8 -*-
"""eval/gate.py —— 配对门禁的**再导出** + 一层"把 promptfoo 跑批接上去"的薄壳

## ★ 为什么这个文件几乎不写逻辑（2026-11-11 盲审第 3 条）

配对 McNemar 的实现早就在 `kb/loopgate.py`（`mcnemar_exact` / `decide` / `is_ok`），
而且已经被 `scripts/mutate_eval_loop.py` 的变异 C 证明过"改坏会红"。
在这里重写一遍 = 造第二把尺子 —— 正是本仓一直在防的病（同一个公式两处实现，最后没人知道哪份是对的）。

所以本文件只做两件事：

1. **再导出**：`from eval.gate import mcnemar_exact, decide, is_ok` 可用（门禁就是那一份）。
2. **适配**：把两份 promptfoo 的 `-o` JSON 折成 loopgate 认识的 rows，算配对翻转 + p。

## 三条纪律

- 只比**同一层**的两次跑批。跨层（offline vs online）**直接拒绝**：两层判据不同
  （向量分 0.55 vs 精排分 <5），配对比出来的"改善"没有意义。
- **不自动判"该不该发"**：本脚本只出「翻转表 + p」。`decide()` 的 verdict 会一起打印，
  但那只是"按 loopgate 默认规则算出来的参考值"，**不是发布建议** —— alpha 与取舍交调用方。
  这样做的理由：本仓的假红已经出过事（常红等于把警报贴住），门禁不该替人做决定。
- 扫到 0 条可比对（题集交集为空 / 该指标一题都不适用）→ **拒绝输出**（exit 2），不许"通过"。

## 用法

```powershell
python eval/gate.py --a eval/reports/promptfoo-offline.json `
                    --b eval/reports/promptfoo-offline-v2.json --metric recall5
```

退出码：0 = 出数（**不代表"可以发"**）；2 = 拒绝比较（跨层 / 题集不一致 / 无题可比）。
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import summarize                                    # noqa: E402
# ★ 再导出：门禁逻辑只有 kb/loopgate.py 这一份，本文件不重写
from kb.loopgate import apply_significance, decide, is_ok, mcnemar_exact   # noqa: E402,F401

#: 低于这个题数就提醒"别把方向当结论"（不拒绝出数 —— 精确检验本身会给出很大的 p）
SMALL_N = 20

#: 噪声地板（由 `eval/noise_floor.py` 量出来，不是手写的印象值）
NOISE_FLOOR = HERE / "reports" / "noise-floor.json"


def _noise_floor_lines(layer: str) -> list[str]:
    """读量出来的地板；没量过就明说"未量"（缺失 ≠ 0）。"""
    if not NOISE_FLOOR.exists():
        return ["- 噪声地板：**未量**（缺 `reports/noise-floor.json`）—— 这时别解释小幅差异，"
                "先跑 `eval/noise_floor.py` 把地板量出来。"]
    nf = json.loads(NOISE_FLOOR.read_text(encoding="utf-8"))
    rng = nf.get("observed_range") or {}
    txt = "、".join(f"{k} ±{v.get('range')}" for k, v in rng.items()) or "（空）"
    lines = [f"- 噪声地板（{nf.get('layer')} 层，{len(nf.get('runs') or [])} 次同配置实测）：{txt}"
             f" ⇒ 小于等于这个幅度的差异**不解释**（出处 `reports/noise-floor.md`）"]
    if nf.get("layer") != layer:
        lines.append(f"  ⚠️ 地板量的是 `{nf.get('layer')}` 层，本次比的是 `{layer}` 层 —— 层不同，地板不适用")
    return lines


def _bucket(case: dict, metric: str) -> str:
    """把一条 case 的判据结果翻成 loopgate 的 bucket 语义（is_ok 只认 bucket）。

    ★ 为什么不自己判 ok：`is_ok` 是门禁与 runstate 的**共同**判据（"用报告里的 bucket，
      而不是自己再算一遍指标"）。这里顺着它，别再造一套。
    """
    ok = case["scores"].get(metric) == 1
    if summarize.METRICS[metric]["type"] == summarize.TYPE_REFUSE:
        return "refuse_ok" if ok else "answered"        # 拒答题：答了就不算对
    return "ok" if ok else "miss"


def _rows(cases: list[dict], metric: str) -> list[dict]:
    return [{"id": c["qid"], "type": c["type"], "bucket": _bucket(c, metric)} for c in cases
            if summarize.applies_to(metric, c["type"])]


def _load(path: Path) -> list[dict]:
    return summarize.load_cases(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="基线跑批 JSON（promptfoo -o 的产物）")
    ap.add_argument("--b", required=True, help="候选跑批 JSON")
    ap.add_argument("--metric", default="recall5", choices=list(summarize.METRICS))
    ap.add_argument("--md", default=None, help="（可选）把结果写成 md")
    args = ap.parse_args()

    a, b = _load(Path(args.a)), _load(Path(args.b))
    la = next((c["layer"] for c in a if c["layer"]), "?")
    lb = next((c["layer"] for c in b if c["layer"]), "?")
    if "?" in (la, lb) or la != lb:
        print(f"[REFUSE] 跨层不可比：A 层={la} / B 层={lb}。"
              f"offline 与 online 的判据不同（向量分 0.55 vs 精排分 <5），配对比没有意义。",
              file=sys.stderr)
        return 2
    if {c["qid"] for c in a} != {c["qid"] for c in b}:
        print("[REFUSE] 两次跑批的题目集合不一致 —— 配对比较要求同一批题。", file=sys.stderr)
        return 2

    ra, rb = _rows(a, args.metric), _rows(b, args.metric)
    if not ra or not rb:
        print(f"[REFUSE] 指标 `{args.metric}` 在这两份跑批里一题都不适用（扫到 0 条不算通过）。",
              file=sys.stderr)
        return 2

    # min_gain=0：本脚本不判"该不该发"，所以不让"净收益不足"这条规则把 verdict 压成 reject
    res = decide(ra, rb, min_gain=0)
    flips = ([("变大", q, "胜") for q in res["gains"]] +
             [("变差", q, "负") for q in res["losses"]])

    lines = [
        f"# 配对比较（指标 `{args.metric}`，层 `{la}`）",
        "",
        f"- A：`{Path(args.a).as_posix()}`",
        f"- B：`{Path(args.b).as_posix()}`",
        f"- 可比题数：{res['n_common']}（该指标适用者）",
        f"- 翻转：G={len(res['gains'])}（B 变好）/ L={len(res['losses'])}（B 变差）",
        f"- **McNemar 精确 p = {res['mcnemar_p']}**（实现见 `kb/loopgate.py::mcnemar_exact`）",
        "",
        "> ⚠️ 本脚本**不判断该不该发**。p 与 alpha 的取舍、以及「两题翻转值不值得发」，交给调用方。",
        f"> `decide()` 的 verdict 参考值：`{res['verdict']}`（理由：{res['reasons'] or '无'}）"
        " —— 那是 loopgate 的默认规则，不是发布建议。",
    ]
    lines += _noise_floor_lines(la)
    if res["n_common"] < SMALL_N:
        lines.append(f"> ⚠️ 可比题数 {res['n_common']} < {SMALL_N}：即使 p 很小，也只是**方向性证据**，"
                     "别写成结论。")

    if flips:
        lines += ["", "## 逐题翻转", "", "| qid | 方向 | A | B |", "|---|---|---|---|"]
        amap = {r["id"]: r for r in ra}
        bmap = {r["id"]: r for r in rb}
        for direction, qid, _ in flips:
            lines.append(f"| {qid} | {direction} | {'对' if is_ok(amap[qid]) else '错'} "
                         f"| {'对' if is_ok(bmap[qid]) else '错'} |")
    else:
        lines += ["", "（没有任何一题翻转：两份跑批在这个指标上逐题一致）"]

    report = "\n".join(lines) + "\n"
    print(report)
    if args.md:
        Path(args.md).write_text(report, encoding="utf-8")
        print(f"[ok] 已写 {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

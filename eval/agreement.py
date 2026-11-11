# -*- coding: utf-8 -*-
"""eval/agreement.py —— 人机一致性（Cohen's κ）：**只算，不替人标**

## 它解决什么问题

前面三条指标（recall5 / no_miss / refuse_ok）全是"机器判机器"，不需要人。
但"答案写得对不对"这件事机器判不了（`evalset/heldout/README.md:119-124` 早就写明了：
本轮只看检索层两个指标，不评答案质量）。要评就得人看 —— 而人看就出现一个新问题：
**人和系统的判断一致吗？两个人之间一致吗？** 这就是 κ。

★ 为什么必须写"样本 < 20 就拒绝输出 κ"：
  n=20 时 κ 的 95% 区间宽得能盖住 0.2~0.8；n=5 时 κ 可以因为一道题就翻符号。
  输出一个没有区间的 κ，比不输出更坏 —— 它看起来像结论。

## 用法

```powershell
# ① 生成 20 条待标注样本（题目来自 held-out，标签留空）
python eval/agreement.py --make-sample 20

# ② 人标完两边（label_a / label_b）之后算一致性
python eval/agreement.py --input eval/annotations/sample-20.json
```

退出码：0 = 出数；2 = 拒绝（样本不足 / 无法定义 / 读不到）。

## 口径（与 `eval/annotation-rubric.md` 必须一致）

标签集合固定为四个：`correct`（答对且引用对）/ `partial`（答对一半或有错引）/
`wrong`（答错）/ `refused`（拒答）。拒答题的"拒对了"记 `correct`，"该拒不拒"记 `wrong`。
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
QUESTIONS = REPO_ROOT / "evalset" / "heldout" / "questions.json"
SAMPLE_DIR = HERE / "annotations"

LABELS = ("correct", "partial", "wrong", "refused")
MIN_N = 20                     # ★ 少于这个数就拒绝输出 κ（理由见模块 docstring）


def cohen_kappa(a: list[str], b: list[str]) -> tuple[float, float, float]:
    """返回 (κ, po, pe)。**pe=1 时 κ 无定义** —— 由调用方拒绝，不要偷偷返回 1.0。"""
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    labels = set(ca) | set(cb)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in labels)
    if pe >= 1.0:
        return float("nan"), po, pe
    return (po - pe) / (1 - pe), po, pe


def make_sample(n: int, out: Path) -> int:
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    qs = data["questions"] if isinstance(data, dict) else data
    ans = [q for q in qs if q["type"] == "answer"][:n // 2]
    ref = [q for q in qs if q["type"] == "refuse"][:n - len(ans)]
    rows = [{"id": q["id"], "type": q["type"], "question": q["question"],
             "gold": q.get("gold"), "answer": "",
             "label_a": None, "label_b": None, "note": ""} for q in ans + ref]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] 已写 {out}：{len(rows)} 条（answer {len(ans)} + refuse {len(ref)}）")
    print("     把答案贴进 answer 字段，再按 eval/annotation-rubric.md 填 label_a / label_b。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None, help="标注文件（每行要有 label_a / label_b）")
    ap.add_argument("--min-n", type=int, default=MIN_N)
    ap.add_argument("--make-sample", type=int, default=0, help="生成 N 条待标注样本")
    ap.add_argument("--out", default=str(SAMPLE_DIR / "sample-20.json"))
    args = ap.parse_args()

    if args.make_sample:
        return make_sample(args.make_sample, Path(args.out))
    if not args.input:
        ap.error("要么 --input，要么 --make-sample N")

    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = [r for r in rows if r.get("label_a") and r.get("label_b")]
    n = len(rows)

    # ★ 拒绝必须先于任何计算：先算后拒绝，很容易"顺手打印一个"然后被截图引走
    if n < args.min_n:
        print(f"[REFUSE] 有效标注只有 {n} 条 < {args.min_n} —— 按约定**不输出 κ**。\n"
              f"         理由：小样本的 κ 区间宽到没有判别力，输出它比不输出更坏。\n"
              f"         当前文件：{args.input}", file=sys.stderr)
        return 2

    unknown = sorted(({r["label_a"] for r in rows} | {r["label_b"] for r in rows}) - set(LABELS))
    if unknown:
        print(f"[REFUSE] 出现口径外的标签：{unknown}（允许：{list(LABELS)}）", file=sys.stderr)
        return 2

    a = [r["label_a"] for r in rows]
    b = [r["label_b"] for r in rows]
    kappa, po, pe = cohen_kappa(a, b)
    if kappa != kappa:                                    # NaN：pe == 1
        print(f"[REFUSE] 无法定义 κ：两个人对每一题的标签分布完全一致（pe=1，"
              f"即都是同一个标签）—— 这时 κ 的分母是 0，输出 1.0 会是假的。", file=sys.stderr)
        return 2

    conf = Counter(zip(a, b))
    print(f"[ok] n={n}  po={po:.4f}  pe={pe:.4f}  **Cohen's κ = {kappa:.4f}**")
    print("     混淆（A→B）：" + "；".join(f"{x}→{y}:{c}" for (x, y), c in sorted(conf.items())))
    # ★ 不给"κ 多少算好"的自动判词：那是领域取舍（Landis-Koch 那套 0.6/0.8 的阈值
    #   在标注任务上经常被滥用），交给人判。
    print("     （本脚本不判定 κ 算不算好 —— 阈值是领域取舍，别让脚本替你说。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

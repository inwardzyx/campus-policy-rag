# -*- coding: utf-8 -*-
"""把「噪声地板」量出来，而不是凭印象写。

## 为什么

`eval/gate.py` 判"两次跑批有没有变"时，必须先知道**多大的差异是噪声**：
online 层的查询改写与精排都由模型产生，同配置重跑本来就会动。凭印象写"±1~3"是**数字手写**，
而且下次换模型/换 prompt 这个印象就过期了。

⇒ 这里用 N 次**同配置**重跑把地板量出来，并落成表；表里每一格都能指回一份 compact 记录
（该次跑的**逐题结论**，不是聚合数），`eval/gate.py` 再引用这份地板。

## 用法

    python eval/noise_floor.py --runs <run1.json> <run2.json> <run3.json>

产物：
    reports/noise-floor.md            人看（表 + 观测极差 + 结论句）
    reports/noise-floor.json          机器看（gate 读这个）
    reports/noise/run-<i>-<名>.json   每次跑的逐题结论（compact；原始 promptfoo JSON 按 .gitignore 不入库）

★ 为什么 compact 记录要入库：原始 JSON 近 1MB 且"跑一次覆盖一次"，按仓库口径不入库；
  但"这一格指回哪次跑"必须有个**能看的落盘物**。compact 记录就是它（每份 ~30KB，逐题结论齐全）。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import summarize                                    # noqa: E402

OUT_MD = HERE / "reports" / "noise-floor.md"


def _version_lines() -> list[str]:
    """报告头三字段：git + 两个语料哈希（同一把尺子：kb.runstate）。"""
    import subprocess
    sys.path.insert(0, str(HERE.parent))
    try:
        import kb.runstate as rs
        dirs = ((rs.capture(HERE.parent).get("docs") or {}).get("dirs") or {})
        kb = str((dirs.get("docs/kb") or {}).get("hash") or "")[:8]
        inbox = str((dirs.get("docs/inbox") or {}).get("hash") or "")[:8]
    except Exception as e:                             # noqa: BLE001
        return [f"- ⚠️ 拿不到语料哈希（{type(e).__name__}: {e}）⇒ 本文件会被判 UNVERIFIABLE"]
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(HERE.parent), capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout.strip()
    try:
        dep, lay = rs.deps_state(HERE.parent), rs.layout_state()
    except Exception:                                  # noqa: BLE001
        dep, lay = {}, {}
    return [
        f"- 运行指纹相关三字段：git `{sha}`；`docs/kb`={kb}；`docs/inbox`={inbox}",
        f"- 依赖指纹：{'、'.join(f'`{k}`={v}' for k, v in dep.items()) or '**未记录**'}"
        f"（`requirements*.txt` 的 sha256 前 8 位）",
        f"- 版面后端：auto 会选 `{lay.get('auto_would_use')}`；"
        f"可用 `{'、'.join(lay.get('available') or []) or '无'}`；"
        f"不可用 `{'、'.join(lay.get('unavailable') or []) or '无'}`；"
        f"PDF_BACKEND=`{lay.get('pdf_backend')}`",
    ]
OUT_JSON = HERE / "reports" / "noise-floor.json"
COMPACT_DIR = HERE / "reports" / "noise"


def _compact(cases: list[dict]) -> dict:
    """逐题结论（不含长答案）：这是"表里那一格"背后的原始证据。"""
    return {
        "layer": next((c["layer"] for c in cases if c["layer"]), "?"),
        "n_cases": len(cases),
        "tally": summarize.tally(cases),
        "cases": {
            c["qid"]: {"type": c["type"], "scores": c["scores"]}
            for c in sorted(cases, key=lambda c: c["qid"] or "")
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True, help="同配置重复跑出的 promptfoo JSON")
    ap.add_argument("--out-md", default=str(OUT_MD))
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--compact-dir", default=str(COMPACT_DIR))
    ap.add_argument("--note", default="",
                    help="写进报告头的一句实测口径说明（缓存状态 / 进程数 / 失败的尝试）")
    args = ap.parse_args()

    if len(args.runs) < 2:
        print("[FAIL] 只有 1 次跑没法谈「噪声」——至少 2 次，本仓库的实测用 3 次", file=sys.stderr)
        return 2

    compact_dir = Path(args.compact_dir)
    compact_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, rp in enumerate(args.runs, start=1):
        src = Path(rp)
        if not src.exists():
            print(f"[FAIL] 读不到 {src}", file=sys.stderr)
            return 2
        cases = summarize.load_cases(src)
        layer = next((c["layer"] for c in cases if c["layer"]), "?")
        rec = {"run": f"run{i}", "source": src.name, "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
               **_compact(cases)}
        out = compact_dir / f"run-{i}.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append({"run": f"run{i}", "compact": out.relative_to(HERE).as_posix(),
                     "layer": layer, **summarize.tally(cases)})

    layers = {r["layer"] for r in rows}
    if len(layers) != 1:
        print(f"[FAIL] 这些跑批不是一个层：{sorted(layers)} —— 不同层的数字不可比", file=sys.stderr)
        return 2

    # 观测极差（按"题数"算，别用百分比：44 题的 1% 是 0.44 题，没有意义）
    floor = {}
    for name in summarize.METRICS:
        vals = [r[name]["pass"] for r in rows]
        floor[name] = {"min": min(vals), "max": max(vals), "range": max(vals) - min(vals),
                       "total": rows[0][name]["total"]}

    layer = rows[0]["layer"]
    head = _version_lines()
    lines = [
        f"# 噪声地板（{layer} 层，同配置重跑 {len(rows)} 次）",
        "",
        f"- 生成者：`eval/noise_floor.py`；数据：`reports/noise/run-*.json`（每次跑的逐题结论）。",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        *head,
        *([f"- 口径说明：{args.note}"] if args.note else []),
        "- ⚠️ 「观测极差」是按**题数**算的（max−min）。它是**观测**，不是置信区间；"
        "样本只有 "
        f"{len(rows)} 次，所以它只能当下界用：**小于等于这个幅度就别解释成「变好/变坏」**。",
        "",
        "| 指标 | " + " | ".join(r["run"] for r in rows) + " | 观测极差（题） |",
        "|---|" + "---|" * len(rows) + "---|",
    ]
    for name, m in summarize.METRICS.items():
        cells = " | ".join(f"{r[name]['pass']}/{r[name]['total']}" for r in rows)
        lines.append(f"| `{name}` | {cells} | ±{floor[name]['range']} |")
    lines += ["", "**观测极差**：" + "、".join(
        f"{n} {'±' + str(floor[n]['range']) if floor[n]['range'] else '0'}" for n in summarize.METRICS)
        + " ⇒ 小于等于这个幅度的差异**不解释**。", "",
        "> 每次跑都能追回去：`reports/noise/run-<i>.json` 里是该次跑的**逐题结论**"
        "（`cases[qid].scores`），聚合数就是从它算出来的。", ""]
    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")
    Path(args.out_json).write_text(json.dumps(
        {"layer": layer, "runs": [r["compact"] for r in rows],
         "observed_range": floor, "at": datetime.now().strftime("%Y-%m-%d %H:%M")},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[ok] 已写 {args.out_md} 与 {args.out_json}")
    for name in summarize.METRICS:
        f = floor[name]
        print(f"  {name:10s} {f['min']}..{f['max']} / {f['total']}   极差 ±{f['range']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

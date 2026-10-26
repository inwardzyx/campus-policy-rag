#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cost_report.py —— 从压测产物算"每问 token"与"每问成本"。

## 为什么价格不写死
模型的单价会变、也常按合同价不同。写死一个数字，报告就会在别人眼里"很精确"，
实际上是过期的 —— 这比没有数字更坏。所以：
- **token 数**：从压测产物现算（可复现）；
- **金额**：只有你给了 `PRICE_INPUT_PER_M` / `PRICE_OUTPUT_PER_M`（元/百万 token）才算；
  没给就明确写"未配置单价，不估算金额"。

## 用法
```powershell
# 只算 token
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/cost_report.py --in experiments/load-test.json

# 算金额（按你的合同价，示例：输入 1 元/百万、输出 2 元/百万）
$env:PRICE_INPUT_PER_M="1"; $env:PRICE_OUTPUT_PER_M="2"
... scripts/cost_report.py --in experiments/load-test.json --md experiments/cost-report.md
```
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _prices() -> tuple[float | None, float | None]:
    def num(name: str) -> float | None:
        raw = (os.environ.get(name) or "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None
    return num("PRICE_INPUT_PER_M"), num("PRICE_OUTPUT_PER_M")


def main() -> int:
    ap = argparse.ArgumentParser(description="从压测产物算每问 token / 成本")
    ap.add_argument("--in", dest="src", default="experiments/load-test.json")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    p_in, p_out = _prices()
    lines = ["# 成本报告（来自压测产物）", "",
             f"> 来源：`{args.src}`　生成：{data.get('generated_at')}", "",
             "| 并发 | 成功 | token/问 | 输入/问 | 输出/问 | 每问成本 |", "|---|---|---|---|---|---|"]
    for lv in data.get("levels") or []:
        tokens = lv.get("tokens") or {}
        n = lv.get("ok") or 0
        per_q = tokens.get("per_query")
        inp_q = round(tokens.get("input", 0) / n, 1) if n else None
        out_q = round(tokens.get("output", 0) / n, 1) if n else None
        if p_in is not None and p_out is not None and n:
            cost = (tokens.get("input", 0) / 1e6 * p_in
                    + tokens.get("output", 0) / 1e6 * p_out) / n
            cost_s = f"¥{cost:.4f}"
        else:
            cost_s = "未配置单价"
        lines.append(f"| {lv.get('concurrency')} | {n}/{lv.get('n')} | {per_q} | {inp_q} | {out_q} | {cost_s} |")

    lines += ["", "## 口径与限制", "",
              "- token 数来自每个响应里的 `usage`（拿不到的请求不计入，缺失≠0）；",
              "- 金额仅在你给了单价时计算；没给就不估算（避免过期价格冒充实测）；",
              "- 单进程、无缓存、含改写+精排+生成三次调用 —— 这是**当前形态**的成本，不是优化后的。",
              ""]
    md = "\n".join(lines)
    print(md)
    if args.md:
        out = Path(args.md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8", newline="\n")
        print(f"✓ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

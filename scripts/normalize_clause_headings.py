#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""normalize_clause_headings.py —— 把"加粗条款行"转成 markdown 标题，让切块器能按条切。

## 为什么需要
真实公司制度常见写法：

```
## 第一章 总则
**第一条【目的与依据】**
为规范公司劳动关系……
```

切块器 `kb/source.py::chunk_markdown` 只按 `^## ` 切。于是整章（含几十条）会挤成一块：
检索粒度变粗、gold 只能指到"章"，指标看起来正常但不可用。

本脚本把 `**第X条【…】**` 行转成 `## 第X条【…】`（章标题保留），切块器就能一条一块。

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/normalize_clause_headings.py `
    --in "D:\\WorkBuddy\\...\\企业规章制度（50条）.md" `
    --out evalset/fixtures/company_corpus/企业规章制度（50条）.md
```
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: `**第一条【目的与依据】**` / `**第十二条 离职**` 两种写法都认
CLAUSE_BOLD = re.compile(r"^\s*\*\*\s*(第[零一二三四五六七八九十百]+条(?:【[^】]*】)?)\s*\*\*\s*$", re.M)
MD_HEADING = re.compile(r"^(#{1,6})\s+", re.M)


def normalize(text: str) -> tuple[str, int]:
    converted = CLAUSE_BOLD.sub(lambda m: f"## {m.group(1).strip()}", text)
    return converted, len(CLAUSE_BOLD.findall(text))


def main() -> int:
    ap = argparse.ArgumentParser(description="把加粗条款行转成 ## 标题")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    src = Path(args.src)
    text = src.read_text(encoding="utf-8")
    out, n = normalize(text)
    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8", newline="\n")
    print(f"✓ 转换 {n} 条条款标题 → {dst}")
    if n == 0:
        print("✗ 一条都没转换：确认源文件里条款是否写成 **第X条【…】**")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""import_eval_jsonl.py —— 把外部生成的多跳评测集导入成本项目题集格式。

## 输入形态（WorkBuddy 产出的 JSONL）
```json
{"id": "A1", "type": "多跳合成", "level": "L2",
 "question": "...", "evidence": "第十四条、第二十三条",
 "answer": "...", "wrong_answer": "...", "evidence_articles": [14, 23]}
```

## 输出形态（本项目 `evalset/scenarios/*.jsonl`）
```json
{"id": "c50-A1", "type": "answer", "question": "...",
 "gold": [["企业规章制度（50条）.md", "第十四条【迟到、早退与旷工】"],
          ["企业规章制度（50条）.md", "第二十三条【请假与加班期间的薪酬计算】"]],
 "tags": ["多跳合成", "L2"], "expected_answer": "...", "wrong_answer": "..."}
```

- 多跳题引用多条 ⇒ `gold` 用**列表的列表**（本项目 `gold_pairs()`：命中任一即算对）；
- `不可答/边界` ⇒ `type=refuse`、`gold=null`；
- `evidence_articles` 的数字要映射到**本语料里真实存在的条款标题**，映射不上就报错（不编）。

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/import_eval_jsonl.py `
    --in "D:\\WorkBuddy\\...\\企业规章制度_问答评测集_50题.jsonl" `
    --corpus evalset/fixtures/company_corpus --out evalset/scenarios/company-52.jsonl
```
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb.values import _cn_to_int, _normalize_cn     # noqa: E402

HEADING = re.compile(r"^##\s+(第([零一二三四五六七八九十百]+)条(【[^】]*】)?)\s*$", re.M)


def article_map(corpus: Path) -> tuple[str, dict[int, str]]:
    """返回 (文档名, {条文号: 标题})。标题就是切块器会用的 clause 文本。"""
    md = sorted(corpus.glob("*.md"))
    if len(md) != 1:
        raise SystemExit(f"语料目录里应恰好 1 份 .md，实际 {len(md)} 份")
    doc = md[0]
    text = _normalize_cn(doc.read_text(encoding="utf-8"))
    mapping: dict[int, str] = {}
    for m in HEADING.finditer(text):
        num = _cn_to_int(m.group(2))
        mapping[num] = m.group(1).strip()
    if not mapping:
        raise SystemExit("语料里没找到 `## 第X条…` 标题，先跑 normalize_clause_headings.py")
    return doc.name, mapping


def main() -> int:
    ap = argparse.ArgumentParser(description="导入外部 JSONL 评测集")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    doc_name, articles = article_map(Path(args.corpus))
    rows = [json.loads(line) for line in Path(args.src).read_text(encoding="utf-8").splitlines() if line.strip()]

    out: list[dict] = []
    problems: list[str] = []
    for row in rows:
        cid = f"c50-{row.get('id')}"
        typ_zh = str(row.get("type") or "")
        is_refuse = "不可答" in typ_zh or "边界" in typ_zh
        nums = [int(n) for n in (row.get("evidence_articles") or [])]
        if not is_refuse and not nums:
            problems.append(f"{cid} 没有条文号")
            continue
        gold = None
        if not is_refuse:
            gold = []
            for n in nums:
                heading = articles.get(n)
                if heading is None:
                    problems.append(f"{cid} 引用了不存在的第 {n} 条")
                    continue
                gold.append([doc_name, heading])
            if not gold:
                continue
        out.append({
            "id": cid,
            "type": "refuse" if is_refuse else "answer",
            "question": row.get("question"),
            "gold": gold,
            "tags": [typ_zh, row.get("level")],
            # 保留人工答案/干扰项，便于日后做"事实型答案校验"（现在不参与判分）
            "expected_answer": row.get("answer"),
            "wrong_answer": row.get("wrong_answer"),
        })

    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"✓ 导入 {len(out)} 题 → {dst}（条文映射 {len(articles)} 条）")
    if problems:
        print(f"✗ {len(problems)} 条问题：")
        for p in problems[:10]:
            print("  - " + p)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

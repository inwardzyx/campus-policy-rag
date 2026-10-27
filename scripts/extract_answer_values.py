#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""extract_answer_values.py —— 从 gold 条款里抽"值"，生成 `expect` 映射。

## 为什么是脚本而不是手填
32 道答案题逐个手填容易抄错；而"条款里出现的值"是可以机械抽的。
抽出来的是**候选值集合**（不是"标准答案"）：报告里 `answer_value` 的判据是
"答案里出现集合里的任意一个值"，属于**弱判据**，所以报告同时给出 `answer_value_mode=auto`。

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\extract_answer_values.py `
    --cases hr --corpus docs/kb --out evalset/expect/hr.json
```
（hr 是合成语料，语料在 evalset/fixtures/hr_corpus。）
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

from kb import evalkit                       # noqa: E402
from kb.values import extract                # noqa: E402

HEADING = re.compile(r"^(#{1,6})\s*(.+?)\s*$", re.M)
#: 这些单位的数字通常是"频率/序号"噪声（如 "40 小时/月" 里的 1 月），不当答案值。
NOISY_UNITS = {"", "个", "次", "名"}


def parse_corpus(corpus: Path) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for md in sorted(corpus.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        marks = list(HEADING.finditer(text))
        for idx, m in enumerate(marks):
            start = m.end()
            end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
            body = text[start:end].strip()
            if body:
                out[(md.name, m.group(2).strip())] = body
    return out


def asked_units(question: str) -> set[str]:
    """问题里提到的单位词（"多少学时/几天/比例/多少钱"）。没有 ⇒ 这题不该判值。"""
    from kb.values import _UNIT_ALIAS          # 复用同一张单位表，避免两处口径

    q = question or ""
    return {unit for raw, unit in _UNIT_ALIAS.items() if raw in q}


def values_for(corpus: dict[tuple[str, str], str], doc: str, clause: str,
               units: set[str] | None = None) -> list[str]:
    text = corpus.get((doc, clause))
    if text is None:
        norm = clause.replace(" ", "")
        for (d, title), body in corpus.items():
            if d == doc and (title.replace(" ", "") in norm or norm in title.replace(" ", "")):
                text = body
                break
    if not text:
        return []
    out: list[str] = []
    for num, unit in extract(text):
        if unit in NOISY_UNITS:
            continue
        if units and unit not in units:
            continue
        label = f"{num:g} {unit}" if unit else f"{num:g}"
        if label not in out:
            out.append(label)
    return out[:4]


def main() -> int:
    ap = argparse.ArgumentParser(description="抽 gold 条款里的值，生成 expect 映射")
    ap.add_argument("--cases", required=True, help="套件名或题集路径")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    path = evalkit.resolve_suite(args.cases, ROOT)
    cases = evalkit.load_cases(path)
    corpus = parse_corpus(Path(args.corpus))

    expect: dict[str, list[str]] = {}
    missing: list[str] = []
    for case in cases:
        if str(case.get("type")) != "answer":
            continue
        gold = case.get("gold") or case.get("expected_sources")
        if not gold or len(gold) < 2:
            missing.append(f"{case.get('id')}: 没有 gold")
            continue
        units = asked_units(str(case.get("question") or ""))
        if not units:
            missing.append(f"{case.get('id')}: 问题不涉及数值（不判值，缺失≠0）")
            continue
        vals = values_for(corpus, str(gold[0]), str(gold[1]), units)
        if vals:
            expect[str(case["id"])] = vals
        else:
            missing.append(f"{case.get('id')}: 条款里抽不出带单位的值")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mode": "auto", "expect": expect}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"✓ {len(expect)} 道题抽到值 → {out}")
    if missing:
        print(f"· {len(missing)} 道题没有值（报告里 answer_value 记 None）：")
        for line in missing[:8]:
            print("   " + line)
    return 0 if expect else 1


if __name__ == "__main__":
    raise SystemExit(main())

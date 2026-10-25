# -*- coding: utf-8 -*-
"""check_eval_gold.py —— 题集里的 gold 与语料是否还对得上。

## 为什么需要
语料一改（改名、合并、删条款），题集没跟着改，那道题就会**静默变成"必错"**：
评测报告上看起来是"模型退化了"，实际上 gold 指向的东西已经不存在。
这类假红会让人不信评测，比没有评测更坏。

## 判据（三条，缺一条就是假绿）
1. 每条 answer 题的 gold `[doc, clause]` 里，`doc` 必须存在于语料目录；
2. 该文件里必须有 `## <clause>` 这样的标题（本仓库切块用标题做条款名）；
3. **扫到 0 条 gold 必须报红** —— 空转的"通过"是假绿。

用法：
    D:\\Python-project\\.venv\\Scripts\\python.exe scripts/check_eval_gold.py
    ... --cases evalset/questions.json --corpus docs/kb   （等 hr 场景题集落地后同样只加 --cases）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def load_cases(path: Path) -> list[dict]:
    """同时认 JSON 数组（老题集）与 JSONL（场景题集）。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl" or text.lstrip().startswith("{"):
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    return list(json.loads(text))


def gold_of(case: dict) -> tuple[str, str] | None:
    raw = case.get("gold") or case.get("expected_sources")
    if not raw or len(raw) < 2:
        return None
    return str(raw[0]), str(raw[1])


def check_cases(cases: list[dict], corpus_dir: Path) -> tuple[list[str], int]:
    """返回 `(错误列表, 检查过的 gold 条数)`。0 条 gold 由调用方判成红。"""
    errors: list[str] = []
    checked = 0
    for case in cases:
        if str(case.get("type", "answer")) != "answer":
            continue
        gold = gold_of(case)
        if gold is None:
            errors.append(f"{case.get('id')}: answer 题没有 gold（没法判分）")
            continue
        doc, clause = gold
        checked += 1
        path = corpus_dir / doc
        if not path.is_file():
            errors.append(f"{case.get('id')}: gold 文档不存在：{corpus_dir.name}/{doc}")
            continue
        body = path.read_text(encoding="utf-8")
        # 切块用标题做条款名，所以 gold 的 clause 必须能作为标题出现
        if f"## {clause}" not in body:
            errors.append(f"{case.get('id')}: 《{doc}》里找不到标题「## {clause}」")
    return errors, checked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+", default=["evalset/questions.json"],
                    help="题集文件（JSON 数组或 JSONL）")
    ap.add_argument("--corpus", default="docs/kb", help="语料目录（相对仓库根）")
    args = ap.parse_args()

    corpus = ROOT / args.corpus
    if not corpus.is_dir():
        print(f"✗ 语料目录不存在：{corpus} —— 拒绝报绿")
        return 1

    total_checked = 0
    errors: list[str] = []
    for rel in args.cases:
        path = ROOT / rel
        if not path.is_file():
            errors.append(f"题集不存在：{rel}")
            continue
        cases = load_cases(path)
        errs, checked = check_cases(cases, corpus)
        total_checked += checked
        errors.extend(f"{rel} → {e}" for e in errs)
        print(f"· {rel}: {len(cases)} 题，检查 {checked} 条 gold")

    if errors:
        print("\n✗ gold 与语料对不上：")
        for e in errors:
            print(f"  - {e}")
        return 1
    if total_checked == 0:
        print("✗ 一条 gold 都没检查到 —— 空转不许报绿（题集格式变了？）")
        return 1
    print(f"\n✓ {total_checked} 条 gold 全部能在 {args.corpus} 里找到对应标题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

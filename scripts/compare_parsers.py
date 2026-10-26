#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""compare_parsers.py —— 同一批 PDF，`pypdf` vs `Docling` 的抽取保真度对照。

## 为什么要有这个
"换解析器能提升召回"是行业里被说烂的一句话，但我们从没量过。这里给出**可复算的代理指标**：
抽出多少字符、识别出多少 `##` 条款标题、保留了多少表格行、中文占比、耗时。

## 必须说清的口径
这些是**保真度代理**，不是 Recall@5。要算 Recall 需要"每题对应的正确文档/条款"的人工题集，
那是另一件事（题集在 `evalset/scenarios/`，本次不做）。代理指标能回答的问题是：
"换成 Docling，是不是至少**抽出了更多结构**？扫描件是不是从"几乎没字"变成"有字"？"

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/compare_parsers.py
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/compare_parsers.py --pdf samples/sisu-handbook-scan.pdf
```
产物：`experiments/report-parse-quality.json`（机器读）+ 终端表格（人读）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "report-parse-quality.json"
CJK = re.compile(r"[\u4e00-\u9fff]")


def _stats(text: str) -> dict:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    cjk = len(CJK.findall(text))
    return {
        "chars": len(text),
        "nonempty_lines": len(lines),
        "headings": len(re.findall(r"(?m)^\s*#{1,3}\s", text)),
        "table_rows": sum(1 for ln in lines if ln.count("|") >= 2),
        "cjk_chars": cjk,
        "cjk_ratio": round(cjk / max(1, len(text)), 4),
    }


def parse_pypdf(path: Path) -> dict:
    from pypdf import PdfReader

    t0 = time.time()
    reader = PdfReader(str(path))
    pages = [p.extract_text() or "" for p in reader.pages]
    text = "\n".join(pages)
    return {"ok": True, "pages": len(reader.pages), "secs": round(time.time() - t0, 2),
            **_stats(text), "text_head": text.strip()[:200]}


def parse_docling(path: Path) -> dict:
    try:
        from docling.document_converter import DocumentConverter
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"docling 不可用：{type(exc).__name__}: {exc}"}
    t0 = time.time()
    try:
        conv = DocumentConverter()
        result = conv.convert(str(path))
        md = result.document.export_to_markdown()
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "secs": round(time.time() - t0, 2), **_stats(md),
            "text_head": md.strip()[:200]}


def main() -> int:
    ap = argparse.ArgumentParser(description="pypdf vs Docling 抽取保真度对照")
    ap.add_argument("--pdf", nargs="*", default=None)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    pdfs = [Path(p) for p in (args.pdf or sorted((ROOT / "samples").glob("*.pdf")))]
    if not pdfs:
        print("✗ 没找到 PDF（默认看 samples/*.pdf）")
        return 1

    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "pdfs": []}
    for pdf in pdfs:
        if not pdf.is_file():
            print(f"✗ 不存在：{pdf}")
            continue
        print(f"\n=== {pdf.name}（{pdf.stat().st_size // 1024} KB）")
        row = {"file": pdf.name, "kb": pdf.stat().st_size // 1024,
               "pypdf": parse_pypdf(pdf), "docling": parse_docling(pdf)}
        report["pdfs"].append(row)
        for name in ("pypdf", "docling"):
            r = row[name]
            if not r.get("ok"):
                print(f"  {name:8s} ✗ {r.get('error')}")
                continue
            print(f"  {name:8s} ok  字符 {r['chars']:>7} | 行 {r['nonempty_lines']:>5} | "
                  f"标题 {r['headings']:>4} | 表格行 {r['table_rows']:>4} | "
                  f"中文占比 {r['cjk_ratio']:.2%} | {r['secs']}s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✓ 报告：{out}")
    print("★ 口径：这是**保真度代理**（抽出多少结构），不是 Recall@5。要 Recall 得有人工题集。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

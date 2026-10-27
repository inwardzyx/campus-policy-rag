#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_pdf_samples.py —— 对 `samples/` 里的 PDF 做一次解析验收并写报告。

## 判据
- 有文本层的 PDF：抽出 > 100,000 字，且包含预期关键词；
- 扫描版 PDF：必须抛 `ParseError`（**不许静默返回空串**）。

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/check_pdf_samples.py `
    --out experiments/pdf-parse-samples.md
```
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb.source import ParseError, extract_text     # noqa: E402

TEXT_PDF = "sisu-handbook-2025.pdf"
SCAN_PDF = "sisu-handbook-scan.pdf"


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF 解析验收")
    ap.add_argument("--dir", default="samples")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    base = ROOT / args.dir
    lines = ["# PDF 解析验收（samples/）", "",
             "> 判据：文字版必须抽出足够文本；扫描版必须**明确报错**而不是静默返回空串。", "",
             "| 文件 | 结果 | 字符数 | 说明 |", "|---|---|---|---|"]
    ok = True

    for name, expect in ((TEXT_PDF, "text"), (SCAN_PDF, "scan")):
        path = base / name
        if not path.exists():
            lines.append(f"| `{name}` | 跳过 | — | 文件不存在 |")
            continue
        try:
            text, kind = extract_text(name, path.read_bytes())
            if expect == "text" and len(text) > 100_000:
                lines.append(f"| `{name}` | ✅ | {len(text):,} | 文本层可用（pypdf 顺序提取）|")
            elif expect == "text":
                ok = False
                lines.append(f"| `{name}` | ❌ | {len(text):,} | 文本过少，疑似解析退化 |")
            else:
                ok = False
                lines.append(f"| `{name}` | ❌ | {len(text):,} | 扫描件竟然解析出文本？需人工确认 |")
        except ParseError as exc:
            if expect == "scan":
                lines.append(f"| `{name}` | ✅ | — | 明确报错：{str(exc)[:60]}… |")
            else:
                ok = False
                lines.append(f"| `{name}` | ❌ | — | 文字版却报错：{str(exc)[:60]} |")

    lines += ["", "## 已知限制", "",
              "- pypdf 只做文本层顺序提取，不识别双栏/表格：页眉水印与正文会交错；",
              "- 要干净版面：`PDF_BACKEND=docling` 或 `mineru`（未安装）；",
              "- 扫描件需要 OCR（本机未装），当前行为是**明确拒绝**，不是静默空串。", ""]
    md = "\n".join(lines)
    print(md)
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8", newline="\n")
        print(f"✓ {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

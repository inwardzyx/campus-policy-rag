# -*- coding: utf-8 -*-
"""容器内 Docling worker：PDF -> Markdown。

为什么用 worker 而不是 docling CLI 旗标：
    CLI 的 --to/--ocr/--output 在不同版本里变过；直接调 Python API 更稳，
    也方便显式打开 OCR（扫描件必需）。

用法（容器内）：
    python /app/docling_worker.py /io/in/doc.pdf /io/out/doc.md
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: docling_worker.py <input.pdf> <output.md>", file=sys.stderr)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = True                 # 扫描件必须；文本层 PDF 也安全
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    result = converter.convert(str(src))
    md = result.document.export_to_markdown()
    if not str(md).strip():
        print("docling produced empty markdown", file=sys.stderr)
        return 3
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(str(md), encoding="utf-8")
    print(f"wrote {dst} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

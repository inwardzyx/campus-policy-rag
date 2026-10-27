# -*- coding: utf-8 -*-
"""`samples/` 里两份 PDF 的解析验收（文字版 vs 扫描版）。

## 为什么要有这条
"支持 PDF"很容易变成一句空话：扫描件被静默解析成空字符串，然后进库、检索永远为空，
而所有指标看起来都"跑过了"。这条测试盯的是**两种结局都必须是明确的**：
- 有文本层 ⇒ 抽出足够多的字；
- 没有文本层 ⇒ 抛 `ParseError`（明确告诉用户"需要 OCR/版面解析"），**不许返回空串冒充成功**。

## 已知限制（如实记录，不在测试里假装没有）
pypdf 只做"文本层顺序提取"，不识别双栏/表格版面：
文字版 34 万字符里，页眉水印和双栏内容会交错。要干净版面得走 `PDF_BACKEND=docling/mineru`。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                       # noqa: E402

from kb.source import ParseError, extract_text      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")
TEXT_PDF = "sisu-handbook-2025.pdf"
SCAN_PDF = "sisu-handbook-scan.pdf"


def _read(name: str) -> bytes:
    return open(os.path.join(SAMPLES, name), "rb").read()


@pytest.mark.skipif(not os.path.exists(os.path.join(SAMPLES, TEXT_PDF)),
                    reason="samples 里的文字版 PDF 不在")
def test_text_pdf_extracts_enough_text() -> None:
    text, kind = extract_text(TEXT_PDF, _read(TEXT_PDF))
    assert kind == ".pdf"
    assert len(text) > 100_000, f"只抽出 {len(text)} 字，疑似解析退化"
    assert "上海外国语大学" in text
    assert "学生手册" in text


@pytest.mark.skipif(not os.path.exists(os.path.join(SAMPLES, SCAN_PDF)),
                    reason="samples 里的扫描版 PDF 不在")
def test_scanned_pdf_fails_loudly_not_silently() -> None:
    with pytest.raises(ParseError) as exc:
        extract_text(SCAN_PDF, _read(SCAN_PDF))
    msg = str(exc.value)
    assert "没有可提取文本" in msg and "扫描" in msg

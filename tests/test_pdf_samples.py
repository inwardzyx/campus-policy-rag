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


def _layout_backends_available() -> list[str]:
    """本机可用的版面解析后端（docling / mineru）。取不到就返回空 —— 那就是"无后端"环境。"""
    try:
        from kb.layout import BackendUnavailable, select_backend
    except Exception:                                  # noqa: BLE001
        return []
    out: list[str] = []
    for name in ("docling", "mineru"):
        try:
            if select_backend(name).available():
                out.append(name)
        except (BackendUnavailable, Exception):         # noqa: BLE001
            continue
    return out


@pytest.mark.skipif(not os.path.exists(os.path.join(SAMPLES, SCAN_PDF)),
                    reason="samples 里的扫描版 PDF 不在")
def test_scanned_pdf_never_silently_empty() -> None:
    """不变量：扫描版 PDF **不许「返回空文本且不报错」** —— 任何环境都成立。

    ★ 2026-11-11 改：原来这条只会断言"必须抛 ParseError"。本机装了 docling 之后，
      `kb/_read_pdf` 的 auto 分支会回退到版面解析、真解析出 3481 字 ⇒ 老断言失败。
      当时的两个"省事"做法都不行：
        · `skipif 有版面后端` → 这条不变量在有后端的机器上**永久消失**（假绿的另一种形态）；
        · 直接把断言改成"要么有文本要么报错" → 在**无**后端的机器上就没人守 ParseError 了。
      ⇒ 按环境分派断言，**不变量本身保留**：
        · 无 layout 后端 → 必须抛 ParseError（"没有可提取文本" + 提到"扫描"）
        · 有 layout 后端 → 要么给非空文本，要么明确报错；**不许静默空**
    ★ 依赖声明：`requirements.txt` 里**没有** docling（CI 不装 ⇒ 走第一支）；
      本机可选装，见 `requirements-optional.txt`。
    """
    backends = _layout_backends_available()
    if not backends:
        with pytest.raises(ParseError) as exc:
            extract_text(SCAN_PDF, _read(SCAN_PDF))
        msg = str(exc.value)
        assert "没有可提取文本" in msg and "扫描" in msg
        return

    # 有版面后端：允许解析成功，但**绝不许**"返回空文本且不报错"
    try:
        text, kind = extract_text(SCAN_PDF, _read(SCAN_PDF))
    except ParseError as exc:
        # 明确报错是合法结局之一
        assert "没有可提取文本" in str(exc) or "扫描" in str(exc)
        return
    assert kind == ".pdf"
    assert text.strip(), f"本机有版面后端 {backends} 却返回了空文本 —— 静默空，正是这条要拦的"

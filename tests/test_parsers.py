# -*- coding: utf-8 -*-
"""PDF/DOCX 解析测试：不依赖 python-docx / reportlab。

DOCX 用标准库 zip+XML 造最小文件；PDF 用最小合法 PDF 字节流造一页文本。
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest

from kb.source import ParseError, extract_text
from kb.store import KnowledgeStore

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _p(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _tc(text: str) -> str:
    return f"<w:tc>{_p(text)}</w:tc>"


def make_docx() -> bytes:
    body = (
        _p("报销制度", "Heading1")
        + _p("本制度适用于全体员工。")
        + _p("第三条 报销时限", "Heading2")
        + _p("费用发生后 30 日内提交。")
        + "<w:tbl><w:tr>" + _tc("项目") + _tc("标准") + "</w:tr>"
        + "<w:tr>" + _tc("住宿") + _tc("500 元/晚") + "</w:tr></w:tbl>"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def make_pdf(text: str = "Hello PDF") -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    header = b"%PDF-1.4\n"
    body = b""
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(header) + len(body))
        body += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(header) + len(body)
    xref = b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
               + b" /Root 1 0 R >>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF")
    return header + body + xref + trailer


def test_docx_extracts_headings_tables_and_paragraphs():
    text, suffix = extract_text("制度.docx", make_docx())
    assert suffix == ".docx"
    assert "## 报销制度" in text
    assert "## 第三条 报销时限" in text
    assert "费用发生后 30 日内提交。" in text
    assert "住宿 | 500 元/晚" in text


def test_store_chunks_docx_by_heading(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.upload("demo", "制度.docx", make_docx())
    clauses = [c["clause"] for c in store.load_chunks("demo")]
    assert "第三条 报销时限" in clauses


def test_pdf_extracts_text():
    text, suffix = extract_text("x.pdf", make_pdf("Hello PDF"))
    assert suffix == ".pdf"
    assert "Hello PDF" in text


def test_scanned_pdf_raises_parse_error():
    with pytest.raises(ParseError) as exc:
        extract_text("blank.pdf", make_pdf(""))
    assert "没有可提取文本" in str(exc.value)


def test_broken_docx_raises_parse_error():
    with pytest.raises(ParseError):
        extract_text("x.docx", b"not a zip")

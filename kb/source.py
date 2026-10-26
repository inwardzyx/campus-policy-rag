# -*- coding: utf-8 -*-
"""kb/source.py —— 企业语料的「来源契约」与最小组件

为什么单独抽这一层：
    原来的 loader 只认 `docs/` 下的 .md（顶部 --- 元信息 + ## 条款）。
    企业语料不是这样：可能是上传的 PDF/Word/HTML、飞书知识库、Confluence。
    如果继续把「从哪读」写死在 service.py 里，每接一个来源就要改一次主流程。

这里只做两件事：
    1. 定义 Source 契约（list/fetch）和统一的 DocumentMeta；
    2. 提供 UploadSource（本地文件/上传目录）这个最小实现。

★ 不在这里做：PDF/Word 的重型解析（用 Docling/MinerU/Unstructured）、
  向量化、权限过滤、检索。那些分别是 parser/ingest/retrieval 层的事。

★ 兼容性：产出的 chunk dict 保留现有 service._guard 认的字段
  （doc / clause / version / source / text / text_full），额外字段只增不改，
  这样现有六道把关不用动。
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Protocol
from xml.etree import ElementTree as ET

from kb.normalize import ensure_headings  # noqa: E402


class UnsupportedFormat(Exception):
    """文件格式暂不支持 —— 报清楚缺什么依赖，别静默返回空文本。"""


class ParseError(Exception):
    """格式支持，但内容解析不出来（扫描版 PDF / 坏 DOCX / 加密文件）。"""


@dataclass(frozen=True)
class DocumentMeta:
    """一篇文档的元数据。

    namespace：知识库/租户隔离键；检索只能在同一 namespace 内做。
    doc_id：文档稳定 ID（同一 namespace + 文件名），用于更新/删除。
    content_hash：内容哈希（sha256），用于增量同步时判断“变没变”。
    acl：可见角色/部门；默认 ("public",)。真正的过滤在检索层做。
    tombstone：已删除标记；删除不物理抹掉记录，便于审计与增量同步。
    """

    namespace: str
    doc_id: str
    title: str
    source: str
    version: str
    updated_at: str
    acl: tuple[str, ...] = ("public",)
    content_hash: str = ""
    tombstone: bool = False
    # ★ 2026-10-26 加（as-of 版本裁决用）：
    #   effective_date：生效日 YYYY-MM-DD；空串 = **日期未知**（不是 1970，别拿它排序）。
    #   status：active / deprecated；deprecated 的块在"现行"查询里会被丢掉（reason=stale）。
    effective_date: str = ""
    status: str = "active"


@dataclass
class RawDocument:
    meta: DocumentMeta
    text: str
    suffix: str = ".md"


class Source(Protocol):
    """来源契约。

    语法点：`Protocol` 是“结构化子类型”——任何类只要实现了这两个方法，
    就算 Source，不需要显式继承。测试里可以用假 Source 替换上传目录。
    """

    def list_documents(self) -> list[DocumentMeta]:
        ...

    def fetch(self, doc_id: str) -> RawDocument:
        ...


def doc_id_for(namespace: str, filename: str) -> str:
    """同一 namespace 下用文件名当稳定身份；改名 = 新文档（可接受）。"""
    return hashlib.sha1(f"{namespace}:{filename}".encode("utf-8")).hexdigest()[:12]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析可选的 --- 元信息；没有就返回 ({}, 原文)。

    只支持 key: value 的简单格式，不引入 PyYAML —— 元信息就几个字段，
    手写解析更好测，也少一个依赖。
    """
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line:
            continue
        k, sep, v = line.partition(":")
        if not sep:
            return {}, text          # 元信息格式不对 → 当没有，正文仍是全文
        meta[k.strip()] = v.strip()
    return meta, parts[2]


def _make_chunk(meta: DocumentMeta, clause: str, text: str) -> dict:
    """统一 chunk 形状：保留现有字段，附加企业元数据。"""
    text = text.strip()
    return {
        # —— 现有 service._guard / search 认的字段 ——
        "doc": meta.title,
        "clause": clause.strip() or "全文",
        "version": meta.version,
        "source": meta.source,
        "text": text,
        "text_full": text,
        # —— 企业层新增字段（只增不改）——
        "namespace": meta.namespace,
        "doc_id": meta.doc_id,
        "acl": list(meta.acl),
        "updated_at": meta.updated_at,
        "content_hash": meta.content_hash,
        "tombstone": meta.tombstone,
        "effective_date": meta.effective_date,
        "status": meta.status,
    }


def chunk_markdown(text: str, meta: DocumentMeta) -> list[dict]:
    """按 `## 条款` 切块；没有 ## 就整篇一块（不丢正文）。"""
    _, body = parse_frontmatter(text)
    sections = re.split(r"(?m)^##\s+", body)
    chunks: list[dict] = []
    head = sections[0].strip()
    if head:
        # 企业文档常有“目的/适用范围”前言；不丢，单独成块
        chunks.append(_make_chunk(meta, "前言", head))
    for sec in sections[1:]:
        clause, _, content = sec.partition("\n")
        content = content.strip()
        if content:
            chunks.append(_make_chunk(meta, clause.strip(), content))
    if not chunks:
        chunks.append(_make_chunk(meta, "全文", body.strip()))
    return chunks


def chunk_plain_text(text: str, meta: DocumentMeta) -> list[dict]:
    """纯文本按空行分段；整篇一段也能工作。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return [_make_chunk(meta, "全文", text)]
    return [_make_chunk(meta, f"第{i}段", p) for i, p in enumerate(paras, 1)]


class _HTMLText(HTMLParser):
    """极简 HTML 取文本：跳过 script/style，保留可见文字。

    不追求排版还原；复杂 HTML 应该走 Unstructured/Docling。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    p = _HTMLText()
    p.feed(raw)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _read_pdf_pypdf(data: bytes) -> str:
    """pypdf 轻量提取：只适合有文本层的 PDF。"""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise UnsupportedFormat(
            "PDF 解析需要 pypdf：D:\\Python-project\\.venv\\Scripts\\python.exe -m pip install pypdf "
            "（或先用 Docling/MinerU 转成 md）"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"PDF 打不开（文件损坏或格式不对）：{exc}") from exc
    if getattr(reader, "is_encrypted", False):
        # 空密码能解的加密 PDF 很常见；解不开就明确报错，别返回空文本。
        try:
            if not reader.decrypt(""):
                raise ParseError("PDF 有密码，无法解析")
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"PDF 解密失败：{exc}") from exc

    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            pages.append(text.strip())
    if not pages:
        raise ParseError(
            "PDF 没有可提取文本（很可能是扫描件/图片版）。"
            "可设 PDF_BACKEND=docling 或 mineru 走版面解析。"
        )
    return "\n\n".join(pages)


def _layout_pdf(data: bytes, backend_name: str) -> str:
    """把 PDF 交给 Docling/MinerU 转 Markdown（临时文件只活在这一刻）。"""
    from kb.layout import BackendUnavailable, select_backend

    backend = select_backend(backend_name)
    if not backend.available():
        hint = {
            "docling": "D:\\Python-project\\.venv\\Scripts\\python.exe -m pip install docling",
            "mineru": "安装 MinerU 并设 MINERU_CMD/MINERU_ARGS（见 docs）",
        }.get(backend_name, "")
        raise ParseError(f"{backend_name} 后端未安装/不可用。{hint}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = Path(f.name)
    try:
        return backend.to_markdown(path)
    except BackendUnavailable as exc:
        raise ParseError(str(exc)) from exc
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _read_pdf(data: bytes) -> str:
    """PDF 入口：按 PDF_BACKEND 选 pypdf / Docling / MinerU。

    auto（默认）：先 pypdf；失败（扫描件/无文本层）再依次试已安装的 Docling、MinerU。
    """
    backend = (os.environ.get("PDF_BACKEND") or "auto").strip().lower()
    if backend == "pypdf":
        return _read_pdf_pypdf(data)
    if backend in ("docling", "mineru"):
        return _layout_pdf(data, backend)
    if backend != "auto":
        raise ParseError(f"未知 PDF_BACKEND={backend!r}（支持 pypdf/docling/mineru/auto）")

    try:
        return _read_pdf_pypdf(data)
    except ParseError as orig:
        from kb.layout import BackendUnavailable, select_backend

        failures: list[str] = []
        for name in ("docling", "mineru"):
            try:
                be = select_backend(name)
                if not be.available():
                    continue
                return _layout_pdf(data, name)
            except (BackendUnavailable, ParseError) as exc:
                # 装了但跑不起来（例如 Windows 应用控制策略拦了 .pyd）也要继续试下一个，
                # 最后把真实原因一起报出去 —— 不要用一个后端的失败掩盖原始“无文本”。
                failures.append(f"{name}: {exc}")
        if failures:
            raise ParseError(f"{orig} 后端尝试均失败：{'；'.join(failures)}") from orig
        raise ParseError(
            f"{orig} 当前也没装 Docling/MinerU；装一个或先把扫描件转成 md 再上传。"
        ) from orig


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(f"{_W}t")).strip()


def _docx_table_text(tbl) -> str:
    rows: list[str] = []
    for tr in tbl.findall(f"{_W}tr"):
        cells: list[str] = []
        for tc in tr.findall(f"{_W}tc"):
            cells.append(" ".join(_docx_para_text(p) for p in tc.findall(f"{_W}p")).strip())
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _read_docx(data: bytes) -> str:
    """用标准库解析 DOCX（zip + XML），不依赖 python-docx/lxml。

    ★ 为什么不用 python-docx：它依赖 lxml，本机应用控制策略可能拦 DLL；
      DOCX 本质是 zip 里的 word/document.xml，标准库足够。
    ★ 只取正文段落和表格；页眉/页脚/批注不取（企业问答通常不需要）。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ParseError(f"DOCX 结构不对（缺 word/document.xml 或不是 zip）：{exc}") from exc

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ParseError(f"DOCX XML 解析失败：{exc}") from exc

    body = root.find(f".//{_W}body")
    if body is None:
        raise ParseError("DOCX 里没有 body")

    blocks: list[str] = []
    for child in list(body):
        if child.tag == f"{_W}p":
            text = _docx_para_text(child)
            if not text:
                continue
            style = child.find(f"{_W}pPr/{_W}pStyle")
            val = (style.get(f"{_W}val", "") if style is not None else "")
            # Heading 1/2/3 → ## 让现有 chunk_markdown 能按标题切块
            if val.lower().startswith("heading") or "标题" in val:
                blocks.append(f"## {text}")
            else:
                blocks.append(text)
        elif child.tag == f"{_W}tbl":
            table = _docx_table_text(child)
            if table.strip():
                blocks.append(table)

    text = "\n\n".join(blocks).strip()
    if not text:
        raise ParseError("DOCX 里没有可提取的正文（可能全是图片/文本框）")
    return text


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """按扩展名取文本，返回 (text, suffix)。不支持的格式明确报错。"""
    suffix = Path(filename).suffix.lower()
    if suffix in (".md", ".markdown"):
        return data.decode("utf-8-sig"), suffix
    if suffix == ".txt":
        return data.decode("utf-8-sig"), suffix
    if suffix in (".html", ".htm"):
        return html_to_text(data.decode("utf-8-sig", errors="replace")), suffix
    if suffix == ".pdf":
        return _read_pdf(data), suffix
    if suffix == ".docx":
        return _read_docx(data), suffix
    raise UnsupportedFormat(f"暂不支持 {suffix or '无扩展名'}；支持 .md/.txt/.html/.pdf/.docx")


def chunk_document(raw: RawDocument) -> list[dict]:
    # DOCX 解析器会把 Heading 转成 `## `；Docling/MinerU 产出的 PDF Markdown 也有标题。
    if raw.suffix in (".md", ".markdown", ".docx"):
        return chunk_markdown(raw.text, raw.meta)
    if raw.suffix == ".pdf":
        # ★ 2026-10-26 实测：pypdf 抽真实 PDF 只有正文、**没有任何 `##`**
        #   （339,901 字符 / 0 标题 / 0 表格行），直接定长切块会把条款号丢掉，
        #   引用就无法指回条款。先把 `第X条` 提成标题；提不出来才退回定长切块。
        text, added = ensure_headings(raw.text)
        # 已经有 `#` 标题（Docling/结构化 PDF）或刚补出条款 → 按标题切
        if added or re.search(r"(?m)^#{1,3}\s+", text):
            return chunk_markdown(text, raw.meta)
        return chunk_plain_text(raw.text, raw.meta)
    if raw.suffix in (".html", ".htm"):
        return chunk_plain_text(raw.text, raw.meta)
    return chunk_plain_text(raw.text, raw.meta)


class UploadSource:
    """上传目录来源：root/namespace/ 下的文件就是该知识库的文档。

    ★ 只负责“读文件 + 解析成 RawDocument”；manifest/版本/删除由 KnowledgeStore 管。
    """

    def __init__(
        self,
        root: str | Path,
        namespace: str = "default",
        *,
        acl: Iterable[str] = ("public",),
        version: str = "1",
    ) -> None:
        self.root = Path(root) / namespace
        self.namespace = namespace
        self.acl = tuple(acl)
        self.version = version

    def _files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.rglob("*") if p.is_file())

    def list_documents(self) -> list[DocumentMeta]:
        out: list[DocumentMeta] = []
        for p in self._files():
            rel = p.relative_to(self.root).as_posix()
            out.append(
                DocumentMeta(
                    namespace=self.namespace,
                    doc_id=doc_id_for(self.namespace, rel),
                    title=p.stem,
                    source=f"upload:{rel}",
                    version=self.version,
                    updated_at="",
                    acl=self.acl,
                    content_hash="",
                )
            )
        return out

    def fetch(self, doc_id: str) -> RawDocument:
        for p in self._files():
            rel = p.relative_to(self.root).as_posix()
            if doc_id_for(self.namespace, rel) == doc_id:
                text, suffix = extract_text(p.name, p.read_bytes())
                meta = DocumentMeta(
                    namespace=self.namespace,
                    doc_id=doc_id,
                    title=p.stem,
                    source=f"upload:{rel}",
                    version=self.version,
                    updated_at="",
                    acl=self.acl,
                    content_hash=content_hash(text),
                )
                return RawDocument(meta=meta, text=text, suffix=suffix)
        raise KeyError(f"找不到文档：{doc_id}")

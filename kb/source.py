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
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Protocol


class UnsupportedFormat(Exception):
    """文件格式暂不支持 —— 报清楚缺什么依赖，别静默返回空文本。"""


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


def _read_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise UnsupportedFormat(
            "PDF 解析需要 pypdf：D:\\Python-project\\.venv\\Scripts\\python.exe -m pip install pypdf "
            "（或先用 Docling/MinerU 转成 md）"
        ) from exc
    import io

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()


def _read_docx(data: bytes) -> str:
    try:
        import docx  # type: ignore
    except ImportError as exc:
        raise UnsupportedFormat(
            "DOCX 解析需要 python-docx：D:\\Python-project\\.venv\\Scripts\\python.exe -m pip install python-docx "
            "（或先用 Docling/MinerU 转成 md）"
        ) from exc
    import io

    d = docx.Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in d.paragraphs if p.text.strip()).strip()


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
    if raw.suffix in (".md", ".markdown"):
        return chunk_markdown(raw.text, raw.meta)
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

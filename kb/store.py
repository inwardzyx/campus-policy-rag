# -*- coding: utf-8 -*-
"""kb/store.py —— 按 namespace 管理上传语料的清单、文件与删除

为什么要这一层：
    service.py 现在启动时读死 `docs/`；企业场景要“上传一个知识库 → 建库 → 可删”。
    这一层把「文档清单（manifest）+ 原始文件 + tombstone」落盘，
    检索层以后只要按 namespace 取 chunks，不用关心文件在哪。

★ 不在这里做：向量化、检索、权限过滤。它只负责“语料是什么、属于谁、还在不在”。

目录结构：
    <root>/<namespace>/manifest.json
    <root>/<namespace>/files/<doc_id><suffix>

★ 删除不物理抹 manifest：标 tombstone=True，保留审计痕迹；
  文件本身删掉（省空间），load_chunks 会跳过 tombstone。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from kb.source import (
    DocumentMeta,
    RawDocument,
    chunk_document,
    content_hash,
    doc_id_for,
    extract_text,
    parse_frontmatter,
)

_NS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KnowledgeStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # ★ 解析失败不静默：load_chunks 跳过坏文件，但把原因留在这里，
        #   CLI/接口可以把它报出来（项目原则：扫到 0 条/空转必须报红）。
        self.last_errors: list[str] = []

    # ---------- 路径与清单 ----------
    def _ns_dir(self, namespace: str) -> Path:
        if not _NS_RE.match(namespace):
            raise ValueError("namespace 只能含字母/数字/下划线/连字符，长度 1–64")
        d = self.root / namespace
        d.mkdir(parents=True, exist_ok=True)
        (d / "files").mkdir(exist_ok=True)
        return d

    def _manifest_path(self, namespace: str) -> Path:
        return self._ns_dir(namespace) / "manifest.json"

    def _load_manifest(self, namespace: str) -> list[dict]:
        p = self._manifest_path(namespace)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _save_manifest(self, namespace: str, records: list[dict]) -> None:
        p = self._manifest_path(namespace)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    # ---------- 写 ----------
    def upload(
        self,
        namespace: str,
        filename: str,
        data: bytes,
        *,
        title: str | None = None,
        source: str | None = None,
        version: str | None = None,
        acl: list[str] | None = None,
        effective_date: str | None = None,
        status: str | None = None,
    ) -> dict:
        """上传/覆盖一篇文档，返回 manifest 记录。

        filename 只取 basename，防止 `../../x` 逃出 namespace 目录。
        """
        safe_name = Path(filename).name
        if not safe_name:
            raise ValueError("filename 不能为空")
        text, suffix = extract_text(safe_name, data)
        fm, _ = parse_frontmatter(text) if suffix in (".md", ".markdown") else ({}, text)

        ns_dir = self._ns_dir(namespace)
        doc_id = doc_id_for(namespace, safe_name)
        h = content_hash(text)
        now = _now()

        records = self._load_manifest(namespace)
        old = next((r for r in records if r.get("doc_id") == doc_id), None)
        if old and old.get("tombstone"):
            old = None

        doc_title = title or fm.get("doc") or (old or {}).get("title") or Path(safe_name).stem
        doc_source = source or fm.get("source") or (old or {}).get("source") or f"upload:{safe_name}"
        doc_version = version or fm.get("version") or (old or {}).get("version") or "1"
        # ★ 时间维：frontmatter 里没写就是"未知/现行"，**不猜**。
        # ★ 连接器同步时可以显式给（有些系统的元数据不在正文 frontmatter 里）；
        #   显式值优先，其次 frontmatter，最后沿用旧记录/默认。
        doc_effective = str(effective_date if effective_date is not None
                            else (fm.get("effective_date") or (old or {}).get("effective_date") or ""))
        doc_status = str(status if status is not None
                         else (fm.get("status") or (old or {}).get("status") or "active")).lower()
        doc_acl = list(acl) if acl is not None else list((old or {}).get("acl") or ["public"])

        file_path = ns_dir / "files" / f"{doc_id}{suffix}"
        file_path.write_bytes(data)

        rec = {
            "namespace": namespace,
            "doc_id": doc_id,
            "title": doc_title,
            "source": doc_source,
            "version": doc_version,
            "updated_at": now,
            "created_at": (old or {}).get("created_at", now),
            "acl": doc_acl,
            "content_hash": h,
            "effective_date": doc_effective,
            "status": doc_status,
            "tombstone": False,
            "suffix": suffix,
            "file": file_path.name,
        }
        records = [r for r in records if r.get("doc_id") != doc_id]
        records.append(rec)
        self._save_manifest(namespace, records)
        return rec

    def delete(self, namespace: str, doc_id: str) -> bool:
        """软删除：manifest 标 tombstone，文件删掉；检索不再返回。"""
        records = self._load_manifest(namespace)
        hit = next((r for r in records if r.get("doc_id") == doc_id), None)
        if not hit or hit.get("tombstone"):
            return False
        hit["tombstone"] = True
        hit["updated_at"] = _now()
        p = self._ns_dir(namespace) / "files" / str(hit.get("file", ""))
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
        self._save_manifest(namespace, records)
        return True

    # ---------- 读 ----------
    def list_documents(self, namespace: str, *, include_deleted: bool = False) -> list[dict]:
        records = self._load_manifest(namespace)
        if include_deleted:
            return records
        return [r for r in records if not r.get("tombstone")]

    def load_chunks(self, namespace: str) -> list[dict]:
        """把该 namespace 下所有 active 文档切成 chunk（带元数据）。

        ★ 不做六道把关；把关仍由 service.RAG._guard 统一做（同一套判据，
          不在两个地方各写一份）。
        """
        ns_dir = self._ns_dir(namespace)
        out: list[dict] = []
        self.last_errors = []
        for rec in self.list_documents(namespace):
            p = ns_dir / "files" / str(rec.get("file", ""))
            if not p.is_file():
                self.last_errors.append(f"{rec.get('doc_id')}: 文件不存在 {p.name}")
                continue
            try:
                text, suffix = extract_text(p.name, p.read_bytes())
            except Exception as exc:
                self.last_errors.append(f"{rec.get('doc_id')}: {exc}")
                continue
            meta = DocumentMeta(
                namespace=namespace,
                doc_id=str(rec["doc_id"]),
                title=str(rec.get("title") or p.stem),
                source=str(rec.get("source") or f"upload:{p.name}"),
                version=str(rec.get("version") or "1"),
                updated_at=str(rec.get("updated_at") or ""),
                acl=tuple(rec.get("acl") or ["public"]),
                content_hash=str(rec.get("content_hash") or ""),
                tombstone=False,
                effective_date=str(rec.get("effective_date") or ""),
                status=str(rec.get("status") or "active"),
            )
            raw = RawDocument(meta=meta, text=text, suffix=suffix)
            out.extend(chunk_document(raw))
        return out

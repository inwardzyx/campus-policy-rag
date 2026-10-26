# -*- coding: utf-8 -*-
"""连接器内核：一个协议 + 一个增量同步引擎。

## 为什么要把"连接器"和"同步"拆开
- 连接器只回答两个问题：**现在有哪些文档**（含 ACL/版本/生效日）、**某一篇的正文是什么**；
- 同步引擎只做一件事：把"连接器看到的世界"与"本地 manifest"对齐，并**只处理变更**。

这样飞书、Confluence、共享盘、上传目录都只是"连接器实现"，同步逻辑只有一份
（本仓库为"同一颗炸弹抄两份"栽过）。也正因为拆开了，**没有飞书 scope 也能用 fixture
把增量与权限同步的判据全部跑通**。

## 增量的判据（不是"时间戳"）
变更 = `content_hash` 不同 **或** ACL 不同 **或** 标题/版本不同。
★ 为什么不只看时间戳：飞书/网盘的 `updated_at` 可能滞后或时区不一致；
  哈希是内容本身的指纹，ACL 变化又不会改内容 —— 两者必须一起看。
  （这也是本轮"权限变更必须下一次检索就生效"能成立的机制。）
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from kb.source import RawDocument, content_hash, doc_id_for


@dataclass(frozen=True)
class ConnectorDoc:
    """连接器看到的一篇文档（**不含正文**：正文按需 fetch，避免一次拉全量）。"""

    key: str                  # 稳定键（fixture=文件名；飞书=node_token）：决定 doc_id
    title: str
    source: str               # 出处（连接器名 + 路径/URL）
    version: str = "1"
    acl: tuple[str, ...] = ("public",)
    effective_date: str = ""
    status: str = "active"
    content_hash: str = ""    # 连接器能算就算（省一次 fetch/比较），算不出留空

    @property
    def filename(self) -> str:
        """落盘文件名：由稳定键派生，保证多次同步 doc_id 不变。"""
        return f"{self.key}.md"


class Connector(Protocol):
    name: str

    def list_documents(self) -> list[ConnectorDoc]: ...

    def fetch(self, doc: ConnectorDoc) -> RawDocument: ...

    def owns(self, record: Mapping[str, Any]) -> bool:
        """这条 manifest 记录是不是本连接器带来的（决定 delete_missing 能不能删它）。

        ★ 必须是"归属判定"而不是"namespace 里没看见就删"：一个 namespace 可能同时有
          连接器同步的文档和人工上传的文档，后者不能被连接器一轮同步清掉。
        """
        ...


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0                 # 连接器自己跳过的（非正文类型/空正文/快捷方式重复）
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    changed_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _acl_tuple(acl: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(sorted({str(a).strip().lower() for a in (acl or ["public"]) if str(a).strip()}))


def sync_connector(connector: Connector, store: Any, *, namespace: str,
                   dry_run: bool = False, delete_missing: bool = True) -> SyncReport:
    """把连接器的世界同步进 store。只处理变更；`dry_run=True` 时一个字节都不写。"""
    report = SyncReport(dry_run=dry_run)
    existing = {str(r.get("doc_id")): r for r in store.list_documents(namespace, include_deleted=True)}
    seen: set[str] = set()

    for doc in connector.list_documents():
        doc_id = doc_id_for(namespace, doc.filename)
        seen.add(doc_id)
        old = existing.get(doc_id)
        try:
            raw = connector.fetch(doc)
        except Exception as exc:                      # noqa: BLE001
            # 单篇失败不中断整轮同步，但**必须留下痕迹**（否则就是静默丢文档）
            report.errors.append(f"{doc.key}: {type(exc).__name__}: {exc}")
            continue

        new_hash = content_hash(raw.text)
        reasons = []
        if old is None or old.get("tombstone"):
            reasons.append("added")
        else:
            if old.get("content_hash") != new_hash:
                reasons.append("content")
            if _acl_tuple(old.get("acl")) != _acl_tuple(doc.acl):
                reasons.append("acl")
            if (str(old.get("title") or "") != doc.title
                    or str(old.get("version") or "") != doc.version):
                reasons.append("meta")

        if not raw.text.strip():
            # 空正文不写库（写进去只会变成"入库后被把关拦掉"，且错误更难查）
            report.skipped += 1
            continue
        if not reasons:
            report.unchanged += 1
            continue

        if dry_run:
            report.changed_reasons.append(f"{doc.key}: {','.join(reasons)}(dry-run)")
        else:
            store.upload(namespace, doc.filename, raw.text.encode("utf-8"),
                         title=doc.title, source=doc.source, version=doc.version,
                         acl=list(doc.acl),
                         effective_date=doc.effective_date, status=doc.status)
        if "added" in reasons:
            report.added += 1
        else:
            report.updated += 1
        report.changed_reasons.append(f"{doc.key}: {','.join(reasons)}")

    if delete_missing:
        for doc_id, rec in existing.items():
            if doc_id in seen or rec.get("tombstone"):
                continue
            if not connector.owns(rec):
                continue                 # 不归它管的一律不删（否则会误删人工上传的语料）
            if dry_run:
                report.changed_reasons.append(f"{rec.get('title')}: delete(dry-run)")
            else:
                store.delete(namespace, doc_id)
            report.deleted += 1

    return report

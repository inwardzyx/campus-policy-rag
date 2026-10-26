# -*- coding: utf-8 -*-
"""fixture 连接器：用本地目录模拟"外部系统"，让增量与权限同步能离线验收。

## 为什么需要它
真实连接器（飞书/Confluence）要么缺 scope，要么需要一个真租户；而"增量同步"与
"权限变更下一次检索就生效"这两条判据**不能等外部条件**才验 —— 否则它们永远只是注释。
fixture 把"外部世界"缩成一个目录：

    <dir>/manifest.json     # 文档清单：title/acl/version/effective_date/status
    <dir>/docs/<key>.md     # 正文

改 manifest 里的 acl 就等价于"飞书里调整了空间成员"；删一行就等价于"文档被删"。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from kb.connectors.core import ConnectorDoc
from kb.source import DocumentMeta, RawDocument


class FixtureConnector:
    name = "fixture"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.docs_dir = self.root / "docs"
        self.manifest_path = self.root / "manifest.json"

    def _manifest(self) -> list[dict[str, Any]]:
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        # ★ 空列表不能走 `or`：`[] or data` 会退回整个 dict，然后 list(dict) 得到键名。
        #   （测试里"文档被删空"正是这个形态。）
        docs = data.get("docs") if isinstance(data, dict) else data
        return list(docs or [])

    def list_documents(self) -> list[ConnectorDoc]:
        out = []
        for item in self._manifest():
            key = str(item.get("key") or "")
            if not key:
                raise ValueError(f"manifest 条目缺 key：{item}")
            out.append(ConnectorDoc(
                key=key,
                title=str(item.get("title") or key),
                source=str(item.get("source") or f"fixture:{key}"),
                version=str(item.get("version") or "1"),
                acl=tuple(item.get("acl") or ("public",)),
                effective_date=str(item.get("effective_date") or ""),
                status=str(item.get("status") or "active"),
            ))
        return out

    def owns(self, record: Mapping[str, Any]) -> bool:
        return str(record.get("source") or "").startswith("fixture:")

    def fetch(self, doc: ConnectorDoc) -> RawDocument:
        path = self.docs_dir / f"{doc.key}.md"
        if not path.is_file():
            raise FileNotFoundError(f"fixture 缺正文：{path.name}")
        text = path.read_text(encoding="utf-8")
        meta = DocumentMeta(namespace="", doc_id=doc.key, title=doc.title,
                            source=doc.source, version=doc.version, updated_at="",
                            acl=doc.acl, effective_date=doc.effective_date,
                            status=doc.status)
        return RawDocument(meta=meta, text=text, suffix=path.suffix)

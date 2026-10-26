# -*- coding: utf-8 -*-
"""飞书 wiki 连接器：把飞书知识空间适配成 `Connector`（同步逻辑仍只有一份）。

## 与 `FeishuWikiSource` 的关系
`FeishuWikiSource` 是**历史入口**（CLI 与老测试在用）。现在它的 `sync()` 只是
"构造本连接器 + 调通用引擎 + 把报告映射回旧字段"——**不再自己实现一套哈希/删除逻辑**。
两套实现的坏处本仓库很清楚：改一处忘一处，而且漏的那处不会报错。

## 依赖的飞书 scope（对应代码里的三类调用）
- `wiki:wiki:readonly`：`/wiki/v2/spaces`、`/wiki/v2/spaces/{id}/nodes`
- `docx:document:readonly`：`/docx/v1/documents/{id}/raw_content`
- 应用还必须被**加入知识空间成员**（空间权限 ≠ scope）

## ACL 的来源（v1 的诚实边界）
飞书没有"按节点给应用角色"的接口，所以 ACL 来自**调用方给的映射**（`--acl`）。
真正的组织/群 → roles 同步（含离职失效）在 S1 之后单独做；那时只需换掉这个来源，
本文件与引擎都不用动。
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from kb.connectors.core import ConnectorDoc
from kb.feishu import FeishuError, _edit_date
from kb.source import DocumentMeta, RawDocument


class FeishuConnector:
    name = "feishu"

    def __init__(self, client: Any, space_id: str, *, acl: list[str] | None = None,
                 sleep_sec: float = 0.25, max_depth: int = 20) -> None:
        self.client = client
        self.space_id = space_id
        self.acl = list(acl or ["public"])
        self.sleep_sec = sleep_sec
        self.max_depth = max_depth
        # 诊断信息：跳过多少节点、遍历时哪些节点报错（由调用方映射成报告字段）
        self.skipped = 0
        self.walk_errors: list[dict[str, Any]] = []
        self._obj_tokens: dict[str, str] = {}
        self._docs: list[ConnectorDoc] | None = None

    # ---------- Connector ----------
    def owns(self, record: Mapping[str, Any]) -> bool:
        return str(record.get("source") or "").startswith(f"feishu://{self.space_id}/")

    def list_documents(self) -> list[ConnectorDoc]:
        if self._docs is None:
            docs: list[ConnectorDoc] = []
            self._walk(None, 0, docs)
            self._docs = docs
        return self._docs

    def fetch(self, doc: ConnectorDoc) -> RawDocument:
        content = self.client.raw_docx(self._obj_tokens.get(doc.key) or "")
        if self.sleep_sec:
            time.sleep(self.sleep_sec)          # 飞书限频约 5 次/秒
        meta = DocumentMeta(namespace="", doc_id=doc.key, title=doc.title,
                            source=doc.source, version=doc.version, updated_at="",
                            acl=doc.acl)
        return RawDocument(meta=meta, text=content, suffix=".md")

    # ---------- 遍历 ----------
    def _walk(self, parent: str | None, depth: int, out: list[ConnectorDoc]) -> None:
        if depth > self.max_depth:
            self.walk_errors.append({"node": parent, "error": "超过 max_depth，疑似环"})
            return
        try:
            nodes = self.client.list_nodes(self.space_id, parent)
        except FeishuError as exc:
            self.walk_errors.append({"node": parent, "error": str(exc)})
            return
        for node in nodes:
            token = str(node.get("node_token") or "")
            title = str(node.get("title") or token)
            if not token:
                self.skipped += 1
                continue
            if node.get("node_type") == "shortcut":
                # 快捷方式自身没有正文，指向实体；用 origin_node_token 去重
                token = str(node.get("origin_node_token") or token)
            if node.get("obj_type") != "docx":
                self.skipped += 1
                continue
            if token in self._obj_tokens:
                continue
            self._obj_tokens[token] = str(node.get("obj_token") or "")
            out.append(ConnectorDoc(
                key=token, title=title,
                source=f"feishu://{self.space_id}/{token}",
                version=_edit_date(node), acl=tuple(self.acl)))
            if node.get("has_child"):
                self._walk(token, depth + 1, out)

# -*- coding: utf-8 -*-
"""kb/feishu.py —— 飞书知识库（Wiki）只读连接器

为什么单独一个连接器：
    企业语料常常不在本地文件里，而在飞书知识库的树形节点中。
    这一层只负责“从飞书拉正文 + 元数据”，再交给 KnowledgeStore 落盘；
    它不碰检索、不碰六道把关（把关仍在 service.RAG._guard）。

已实现的接口（飞书开放平台）：
    POST /auth/v3/tenant_access_token/internal        取 tenant_access_token
    GET  /wiki/v2/spaces/:space_id/nodes               分页列子节点（parent_node_token）
    GET  /docx/v1/documents/:document_id/raw_content   取 docx 纯文本

权限（应用必须同时满足）：
    · wiki:wiki:readonly 或 wiki:node:read / wiki:node:retrieve
    · docx:document:readonly（或 docx:document）
    · 应用被加入该知识空间，且对节点有阅读权限
未满足时飞书返回 131006 / 1770032，本模块会把它记成 skipped，不中断整棵树。

★ 明确的边界：本连接器**没有**同步节点级权限。当前 acl 由 --acl 统一给；
  真正的权限同步要看飞书是否暴露节点级权限接口，不能靠猜。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Iterator

from kb.source import content_hash, doc_id_for
from kb.store import KnowledgeStore

DEFAULT_BASE = "https://open.feishu.cn/open-apis"


class FeishuError(RuntimeError):
    def __init__(self, code: int | str, msg: str, status: int | None = None) -> None:
        super().__init__(f"飞书 API 错误 code={code} status={status}: {msg}")
        self.code = code
        self.msg = msg
        self.status = status


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, *, base_url: str = DEFAULT_BASE,
                 timeout: float = 30.0) -> None:
        if not app_id or not app_secret:
            raise ValueError("FEISHU_APP_ID / FEISHU_APP_SECRET 不能为空")
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ---------- HTTP ----------
    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: dict | None = None, auth: bool = True,
                 raw: bool = False) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self.tenant_access_token()}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_body = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except Exception as exc:
            raise FeishuError("network", str(exc)) from exc

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise FeishuError("bad_json", raw_body[:300], status) from exc
        code = payload.get("code", 0)
        if code not in (0, "0", None):
            raise FeishuError(code, payload.get("msg", ""), status)
        # ★ tenant_access_token 接口把 token 放在**顶层**，不在 data 里；
        #   其它业务接口才在 data。所以这里要能选返回整包。
        return payload if raw else (payload.get("data") or {})

    def tenant_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp:
            return self._token
        data = self._request(
            "POST", "/auth/v3/tenant_access_token/internal", auth=False,
            body={"app_id": self.app_id, "app_secret": self.app_secret},
            raw=True,
        )
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuError("no_token", "响应里没有 tenant_access_token")
        self._token = token
        # expire 是秒；留 60 秒余量，避免边界过期
        self._token_exp = now + max(60, int(data.get("expire", 7200)) - 60)
        return token

    # ---------- Wiki ----------
    def iter_spaces(self, page_size: int = 50) -> Iterator[dict]:
        page_token = None
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", "/wiki/v2/spaces", params=params)
            for item in data.get("items") or []:
                yield item
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break

    def list_nodes(self, space_id: str, parent_node_token: str | None = None,
                   page_size: int = 50) -> list[dict]:
        out: list[dict] = []
        page_token = None
        while True:
            params = {"page_size": page_size}
            if parent_node_token:
                params["parent_node_token"] = parent_node_token
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", f"/wiki/v2/spaces/{space_id}/nodes", params=params)
            out.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return out

    def raw_docx(self, document_id: str) -> str:
        data = self._request("GET", f"/docx/v1/documents/{document_id}/raw_content")
        return str(data.get("content") or "")


def _edit_date(node: dict) -> str:
    """把 obj_edit_time（秒级时间戳字符串）转 YYYY-MM-DD；失败返回 '1'。"""
    raw = node.get("obj_edit_time")
    try:
        ts = int(str(raw))
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "1"


class FeishuWikiSource:
    def __init__(self, client: FeishuClient, space_id: str, namespace: str,
                 *, acl: list[str] | None = None, sleep_sec: float = 0.25) -> None:
        self.client = client
        self.space_id = space_id
        self.namespace = namespace
        self.acl = list(acl or ["public"])
        self.sleep_sec = sleep_sec

    def sync(self, store: KnowledgeStore, *, delete_missing: bool = True,
             dry_run: bool = False, max_depth: int = 20) -> dict:
        stats = {"space_id": self.space_id, "namespace": self.namespace,
                 "added": 0, "updated": 0, "unchanged": 0,
                 "skipped": 0, "deleted": 0, "errors": [], "dry_run": dry_run}
        existing = {r["doc_id"]: r for r in store.list_documents(self.namespace)}
        seen_ids: set[str] = set()

        def walk(parent: str | None, depth: int) -> None:
            if depth > max_depth:
                stats["errors"].append({"node": parent, "error": "超过 max_depth，疑似环"})
                return
            try:
                nodes = self.client.list_nodes(self.space_id, parent)
            except FeishuError as exc:
                stats["errors"].append({"node": parent, "error": str(exc)})
                return
            for node in nodes:
                node_token = node.get("node_token") or ""
                title = node.get("title") or node_token
                if not node_token:
                    stats["skipped"] += 1
                    continue
                if node.get("node_type") == "shortcut":
                    # 快捷方式：节点自身没有正文，指向实体；用 origin_node_token 去重
                    node_token = node.get("origin_node_token") or node_token
                obj_type = node.get("obj_type")
                if obj_type != "docx":
                    stats["skipped"] += 1
                    continue

                doc_id = doc_id_for(self.namespace, f"{node_token}.md")
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
                try:
                    content = self.client.raw_docx(str(node.get("obj_token") or ""))
                except FeishuError as exc:
                    stats["skipped"] += 1
                    stats["errors"].append({"node": node_token, "title": title, "error": str(exc)})
                    continue
                if not content.strip():
                    stats["skipped"] += 1
                    continue

                h = content_hash(content)
                old = existing.get(doc_id)
                if old and old.get("content_hash") == h and not old.get("tombstone"):
                    stats["unchanged"] += 1
                else:
                    if not dry_run:
                        store.upload(
                            self.namespace, f"{node_token}.md", content.encode("utf-8"),
                            title=title,
                            source=f"feishu://{self.space_id}/{node_token}",
                            version=_edit_date(node),
                            acl=self.acl,
                        )
                    stats["updated" if old else "added"] += 1
                if self.sleep_sec:
                    time.sleep(self.sleep_sec)
                if node.get("has_child"):
                    walk(node_token, depth + 1)

        walk(None, 0)

        if delete_missing and not dry_run:
            prefix = f"feishu://{self.space_id}/"
            for rec in store.list_documents(self.namespace):
                if str(rec.get("source", "")).startswith(prefix) and rec["doc_id"] not in seen_ids:
                    store.delete(self.namespace, rec["doc_id"])
                    stats["deleted"] += 1
        return stats

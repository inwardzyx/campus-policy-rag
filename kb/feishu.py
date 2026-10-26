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
import re
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


_SCOPE_RE = re.compile(r"[a-z][a-z0-9_]*(?::[a-z0-9_]+)+")


def explain_error(exc: "FeishuError") -> str:
    """把飞书的报错翻译成"下一步点哪里"的清单。

    ★ 为什么值得单独写：`99991672 Access denied` 是最常见的拦路虎，而它的 msg 里其实
      **已经列出缺哪些权限**；真正卡人的是"加了权限还必须发版本 + 把应用加进知识空间"
      这两步（scope ≠ 空间成员）。把这两句写进报错旁边，比写进文档有效得多。
    """
    code = str(getattr(exc, "code", ""))
    msg = str(getattr(exc, "msg", exc))
    scopes = sorted(set(_SCOPE_RE.findall(msg)))
    lines = [f"飞书接口报错：code={code} msg={msg}"]
    if scopes:
        lines.append("飞书提示缺少/涉及的权限：" + ", ".join(scopes))
    if code == "99991672" or "Access denied" in msg:
        lines += [
            "按顺序处理（少一步都不生效）：",
            "1) 开发者后台 → 自建应用 → 权限管理：添加上面列出的只读权限；",
            "2) 版本管理与发布 → 创建版本 → 申请发布（**不发版，权限不生效**）；",
            "3) 管理后台 → 应用管理 → 审批通过（企业自建应用要管理员批）；",
            "4) 目标知识空间 → 设置 → 成员管理 → 添加你的应用（可阅读）"
            "—— scope 不等于空间权限；",
            "5) 重跑：python scripts/feishu_sync.py --list-spaces",
        ]
    elif code in ("10003", "10014"):
        # ★ 10003 最常被误读成"参数写错"，其实是**凭证不对**（占位符 cli_xxx、抄错、
        #   或应用还没发布）。把这条写在报错旁边，比让人去搜错误码快得多。
        lines += [
            "这个码多半是**凭证问题**，不是参数问题：",
            "1) 确认 App ID 是 cli_ 开头的一整串、App Secret 是完整字符串（不是 cli_xxx/xxx 这种占位符）；",
            "2) 在开发者后台 → 你的应用 → 凭证与基础信息 里重新复制；",
            "3) 确认应用已发布（未发布的应用换不到 tenant_access_token）；",
            "4) 本机可以在仓库根放一个 .env（FEISHU_APP_ID=... / FEISHU_APP_SECRET=...），"
            "脚本现在会自动读。",
        ]
    elif code == "no_token":
        lines += ["检查 App ID / App Secret 是否抄错，以及应用是否已发布。" ]
    elif code == "131006":
        lines += ["应用对目标文档/空间没有读权限：把应用加入空间成员，或让文档所有者授予阅读。" ]
    elif code in ("network", "bad_json"):
        lines += ["网络/响应异常：先确认能访问 open.feishu.cn，再重试。" ]
    return "\n".join(lines)


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
        """委托给通用同步引擎（`kb/connectors/core.py`），这里只做字段映射。

        ★ 为什么保留这个方法：CLI 与老测试都在用它。但它**不再自己实现**
          哈希/删除逻辑 —— 否则就变成两套实现，改一处忘一处（本仓库的老病）。
        """
        from kb.connectors.core import sync_connector
        from kb.connectors.feishu import FeishuConnector

        conn = FeishuConnector(self.client, self.space_id, acl=self.acl,
                               sleep_sec=self.sleep_sec, max_depth=max_depth)
        rep = sync_connector(conn, store, namespace=self.namespace,
                             dry_run=dry_run, delete_missing=delete_missing)
        errors = list(conn.walk_errors)
        for item in rep.errors:
            key, _, rest = item.partition(":")
            errors.append({"node": key, "error": rest.strip() or item})
        return {"space_id": self.space_id, "namespace": self.namespace,
                "added": rep.added, "updated": rep.updated, "unchanged": rep.unchanged,
                # ★ 三部分都要算：连接器遍历时跳过的（非 docx/空节点）+ 引擎跳过（空正文）
                #   + 拉正文失败的（老字段语义里它们也算 skipped，同时进 errors）。
                "skipped": conn.skipped + rep.skipped + len(rep.errors),
                "deleted": rep.deleted, "errors": errors, "dry_run": dry_run}

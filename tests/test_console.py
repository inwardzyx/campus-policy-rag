# -*- coding: utf-8 -*-
"""薄控制台（S2-3）：静态页 + 两个管理 API。

## 盯的三条
  ① 页面能打开，且只依赖**已存在的**端点（页面里写的路径必须真在路由表里——否则点了 404）；
  ② 渲染只用 textContent，**没有 `.innerHTML =`**（本仓库硬规矩：数据文本一律 textContent）；
  ③ 页面本身不含任何数据（数据要靠 admin token / 本机回环去拿）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import service as svc                           # noqa: E402
from fastapi.testclient import TestClient       # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "static" / "console.html").read_text(encoding="utf-8")


def client() -> TestClient:
    return TestClient(svc.app)


def test_console_page_renders() -> None:
    resp = client().get("/console")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "控制台" in resp.text


def test_console_only_calls_known_endpoints() -> None:
    """页面里出现的 `/admin/...`、`/kb/...` 路径必须在路由表里（防止改后端忘了改页面）。"""
    routes = {getattr(r, "path", "") for r in svc.app.routes}
    for path in ("/admin/tenants", "/admin/audit", "/kb/"):
        assert path in PAGE
    # 精确端点（页面里写死的两处）
    assert "/admin/tenants" in routes
    assert "/admin/audit" in routes
    assert any(p.startswith("/kb/{namespace}") for p in routes)


def test_console_has_no_innerhtml_assignment() -> None:
    assert ".innerHTML" not in PAGE, "控制台渲染必须用 textContent（XSS 硬规矩）"
    assert "textContent" in PAGE


def test_console_page_carries_no_data() -> None:
    """页面是静态壳：不许把租户/审计数据写进 HTML。"""
    # ★ 只查"真的数据痕迹"：`session_id` 本身是页面代码里的参数名，不算数据。
    for marker in ("tk_", '"rows": [', '"tenants": ['):
        assert marker not in PAGE, f"页面里出现了数据痕迹：{marker}"

# -*- coding: utf-8 -*-
"""`/metrics` 与 `/admin/usage`：容量/成本的可观测入口。

## 盯的三条
  ① `/metrics` 是 Prometheus 文本、含关键指标名（没有它，接监控等于空话）；
  ② `/admin/usage` 走管理闸门：直连本机 200，经隧道/反代且无 token → 403；
  ③ 用量计数真的会累加（不然面板永远显示 0 —— 又是假指标）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import service as svc                           # noqa: E402
from fastapi.testclient import TestClient       # noqa: E402


def _client() -> TestClient:
    return TestClient(svc.app)


def test_metrics_exposes_prometheus_text() -> None:
    resp = _client().get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    for name in ("rag_requests_total", "rag_input_tokens_total", "rag_output_tokens_total",
                 "rag_usage_missing_total", "rag_chunks", "rag_denied_requests_total",
                 "rag_uptime_seconds"):
        assert name in body, f"缺指标：{name}"
    assert body.count("# TYPE") >= 5, "Prometheus 格式至少要有 TYPE 声明"


def test_usage_counters_accumulate() -> None:
    before = svc._USAGE_TOTALS["requests"]
    svc._note_usage("hr", {"input_tokens": 100, "output_tokens": 20})
    svc._note_usage("hr", None)
    assert svc._USAGE_TOTALS["requests"] == before + 2
    assert svc._USAGE_TOTALS["input_tokens"] >= 100
    assert svc._USAGE_TOTALS["missing_usage"] >= 1, "拿不到用量要单独记，不能补 0"
    assert svc._USAGE_TOTALS["by_tenant"]["hr"]["requests"] >= 2


def test_admin_usage_is_guarded(monkeypatch) -> None:
    monkeypatch.delenv("KB_ADMIN_TOKEN", raising=False)
    assert _client().get("/admin/usage").status_code == 200, "直连本机应放行"
    assert _client().get("/admin/usage",
                         headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 403

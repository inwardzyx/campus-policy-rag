# -*- coding: utf-8 -*-
"""追踪兜底（service.py 的 `_guard_tracing_env`）的判据。

## 盯的三条
  ① 机器上有全局 `LANGSMITH_TRACING=true` 但**没有** API key ⇒ 必须强制关掉
     （否则每个请求都 401 重试，而且 prompt 照样发到第三方）；
  ② 有 key 时才允许开（这是"用户真的想用 LangSmith"的唯一合法状态）；
  ③ preflight 要能把这件事**报出来**（开着 = warn，不是 ok）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import service as svc                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import preflight                               # noqa: E402


def test_tracing_forced_off_without_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    svc._guard_tracing_env()
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert svc._TRACING_FORCED_OFF is True


def test_tracing_allowed_with_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_test_key")
    svc._guard_tracing_env()
    assert os.environ["LANGSMITH_TRACING"] == "true", "有 key 时不该强关"


def test_preflight_warns_when_tracing_on() -> None:
    on = preflight.check_tracing({"LANGSMITH_TRACING": "true"}, ROOT)
    assert on.status == "warn", "追踪开着必须让人知道（数据外发）"
    assert "langchain" in on.detail.lower() or "第三方" in on.detail

    off = preflight.check_tracing({"LANGSMITH_TRACING": "false"}, ROOT)
    assert off.status == "ok"

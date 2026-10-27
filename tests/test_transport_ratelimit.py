# -*- coding: utf-8 -*-
"""限流处理（`kb/evalkit/transport.py`）的判据。

## 背景（实测复现）
默认限流 `RATE_BURST=10 / RATE_PER_MIN=20`，连续跑评测时第二、三轮会收到 429。
旧行为把 429 变成 `{"knowledge_hit": False}` ⇒ 报告静默变成 0/17（0.5 秒跑完还显示"完成"）。
这类"配置问题伪装成模型退化"必须被打断，而不是被记录。

## 盯的三条
  ① 429 会**退避重试**，重试成功则正常返回；
  ② 连续 429 超限 ⇒ 抛 `TransportError`（不是伪造一条失败记录）；
  ③ 其它非 200（如 400）仍按原样返回 error dict（那不是限流，不该重试）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                    # noqa: E402

from kb.evalkit import transport as tr           # noqa: E402


def _mk(monkeypatch, codes: list[int]):
    t = tr.HttpTransport("http://127.0.0.1:1")
    seq = list(codes)

    def fake_call(method, path, body=None, headers=None):
        code = seq.pop(0)
        return code, ({"ok": True, "knowledge_hit": True} if code == 200 else {"error": "x"})

    monkeypatch.setattr(t, "_call", fake_call)
    monkeypatch.setattr(tr.time, "sleep", lambda s: None)   # 测试不真等
    return t


def test_429_then_ok_is_retried(monkeypatch) -> None:
    t = _mk(monkeypatch, [429, 200])
    resp = t.ask({"id": "q1", "question": "x"})
    assert resp.get("ok") is True


def test_persistent_429_raises(monkeypatch) -> None:
    t = _mk(monkeypatch, [429, 429, 429, 429])
    with pytest.raises(tr.TransportError):
        t.ask({"id": "q1", "question": "x"})


def test_non_rate_limit_error_is_not_retried(monkeypatch) -> None:
    t = _mk(monkeypatch, [400])
    resp = t.ask({"id": "q1", "question": "x"})
    assert resp.get("error") == "HTTP 400"

# -*- coding: utf-8 -*-
"""审计落盘（批 5）的守门用例。

## 盯的四条
  ① **只落决策，不落原文**：文件里不能出现问题原文、也不能出现块正文；
  ② **只对 namespace 请求写**：`docs/` 旧路径一行都不写（不改变旧行为）；
  ③ **写失败不拖垮请求**：盘不可写时接口照常返回，且有 warning；
  ④ **保留期清理不毁脏行**：解析不了的行保留（审计文件被手工改过时，
     删掉"看不懂的东西"比留着更危险）。

★ 变异体在 `scripts/mutate_audit.py`：把问题原文写进审计 / 清理时删掉脏行，两条都必须变红。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

import service as svc                           # noqa: E402
from kb import audit as audit_mod               # noqa: E402


def resp(answer="按第四条办理[1]。", hit=True, sources=("制度.md｜第四条",)):
    return {
        "answer": answer, "knowledge_hit": hit, "rerank_used": True, "took_ms": 12,
        "sources": [{"doc": s, "text": "正文不许进审计"} for s in sources],
        "context": {
            "trace_id": "t" * 32, "namespace": "hr",
            "blocks": [{"source": "制度.md", "clause": "第四条", "chars": 20,
                        "included": True, "reason": "fits"}],
            "excluded": [], "degraded": {}, "budget": {"retrieved_used": 20},
            "citation": {"used_ids": [1], "unknown_ids": [], "validity": 1.0},
            "as_of": {"event_time": "2025-06-01", "dropped_by_reason": {}},
        },
    }


class Req:
    def __init__(self, question="报销时限是多久？", namespace="hr"):
        self.question = question
        self.namespace = namespace


# ---------------------------------------------------------------- ① 只落决策

def test_event_has_no_raw_question_and_no_block_text() -> None:
    ev = audit_mod.build_event(Req(), resp(), route="/chat", roles=["hr"])
    raw = json.dumps(ev, ensure_ascii=False)
    assert "报销时限是多久" not in raw, "问题原文进了审计 —— 这条线不该有 PII"
    assert "正文不许进审计" not in raw, "块正文进了审计 —— 只该存 doc/clause 定位"
    assert ev["question_sha256"] == audit_mod.question_hash("报销时限是多久？")
    assert ev["question_chars"] == len("报销时限是多久？")
    assert ev["decision"] == "answer" and ev["namespace"] == "hr"
    assert ev["citation"]["validity"] == 1.0
    # ★ sources 里存的是 "doc｜clause" 拼串，审计要拆开 —— 否则"用了哪一条"查不动
    assert ev["sources"] == [{"doc": "制度.md", "clause": "第四条"}]
    assert ev["as_of"]["event_time"] == "2025-06-01"


def test_decisions_are_classified() -> None:
    assert audit_mod.build_event(Req(), resp(hit=False, sources=()),
                                 route="/chat")["decision"] == "refuse"
    assert audit_mod.build_event(Req(), {"error": "boom"}, route="/chat")["decision"] == "error"


# ---------------------------------------------------------------- ② 只对 namespace

def test_docs_path_writes_nothing(tmp_path, monkeypatch) -> None:
    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(target))
    ok = audit_mod.record_response(Req(namespace=None), resp(), route="/chat")
    assert ok is False and not target.exists(), "docs/ 旧路径不该产生审计文件"


def test_namespace_request_writes_one_line(tmp_path, monkeypatch) -> None:
    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(target))
    assert audit_mod.record_response(Req(), resp(), route="/chat", roles=["hr"]) is True
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["roles"] == ["hr"] and ev["decision"] == "answer"


def test_audit_can_be_switched_off(tmp_path, monkeypatch) -> None:
    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(target))
    monkeypatch.setenv("AUDIT_ENABLED", "0")
    assert audit_mod.record_response(Req(), resp(), route="/chat") is False
    assert not target.exists()


# ---------------------------------------------------------------- ③ 失败不影响请求

def test_unwritable_path_does_not_raise(tmp_path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")
    assert audit_mod.record({"a": 1}, path=blocker / "audit.jsonl") is False


# ---------------------------------------------------------------- ④ 清理

def test_prune_removes_old_and_keeps_bad_lines(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    now = time.time()
    old = time.strftime("%Y-%m-%dT%H:%M:%S+0800", time.localtime(now - 40 * 86400))
    new = time.strftime("%Y-%m-%dT%H:%M:%S+0800", time.localtime(now - 1 * 86400))
    path.write_text("\n".join([
        json.dumps({"ts": old, "trace_id": "old"}),
        json.dumps({"ts": new, "trace_id": "new"}),
        "这不是 JSON（手工改过？）",
    ]) + "\n", encoding="utf-8")
    kept, removed = audit_mod.prune(path, days=30, now=now)
    body = path.read_text(encoding="utf-8")
    assert removed == 1 and kept == 2
    assert "old" not in body and "new" in body
    assert "这不是 JSON" in body, "解析不了的行必须保留（删掉它比留着更危险）"


# ---------------------------------------------------------------- ⑤ 模板：版本措辞

def test_template_asks_for_version_only_when_as_of_present() -> None:
    chunks = [{"doc": "d.md", "clause": "第四条", "text": "正文", "text_full": "正文",
               "cite_id": 1}]
    plain = svc.RAG.build_answer_prompt("问", chunks)
    assert "按" not in plain.split("引用要求")[0] or "版本" not in plain
    timed = svc.RAG.build_answer_prompt("问", [{**chunks[0], "as_of_note": "2025-06-01"}])
    assert "按 2025-06-01 时点有效的版本" in timed
    assert "引用要求" in timed


def test_legacy_prompt_has_no_version_line() -> None:
    chunks = [{"doc": "d.md", "clause": "第四条", "text": "正文", "text_full": "正文"}]
    prompt = svc.RAG.build_answer_prompt("问", chunks)
    assert "版本" not in prompt and "[1]" not in prompt

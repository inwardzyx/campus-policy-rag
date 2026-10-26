# -*- coding: utf-8 -*-
"""评测内核（kb/evalkit）的守门用例。

## 盯的四条
  ① **schema 有校验**：空题集 / 重复 id / 非法 type / answer 缺 gold 都必须被抓到
     （空转报红，坏题集会静默失真）；
  ② **指标可插拔**：`register_metric` 之后 `aggregate` 与报告自动带上它，runner 不用改；
  ③ **缺失 ≠ 零**：没有 trace / 没有引用报告时必须是 `None`，报告里要带"为什么"的说明；
  ④ **transport 可换**：录好的响应 / 直连管道与 HTTP 对 runner 完全等价。

★ 变异体在 `scripts/mutate_evalkit.py`：把 `_mean` 改成空集返回 0、把重复 id 检查删掉，
  两条都必须变红。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

import service as svc                           # noqa: E402
from kb import evalkit                          # noqa: E402
from kb.evalkit import metrics as kit_metrics   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def case(cid="c1", typ="answer", gold=("制度.md", "第四条"), q="问?"):
    return {"id": cid, "type": typ, "question": q,
            "gold": list(gold) if gold else None}


def ok_response():
    return {
        "answer": "按第四条办[1]。", "knowledge_hit": True, "rerank_used": True,
        "took_ms": 100, "sources": [{"doc": "制度.md｜第四条", "text": "正文"}],
        "context": {"trace_id": "t" * 32, "namespace": "hr",
                    "blocks": [{"source": "制度.md", "clause": "第四条", "chars": 20,
                                "included": True, "reason": "fits"}],
                    "excluded": [], "degraded": {}, "budget": {"retrieved_used": 20},
                    "citation": {"used_ids": [1], "unknown_ids": [], "validity": 1.0,
                                 "uncited_count": 0}},
    }


# ---------------------------------------------------------------- ① schema

def test_normalize_accepts_both_gold_spellings() -> None:
    a = evalkit.normalize_case({"id": 1, "question": "q", "gold": ["d", "第 1 条"]})
    b = evalkit.normalize_case({"id": 2, "question": "q", "expected_sources": ["d", "第 1 条"]},
                               scenario="hr")
    assert a["gold"] == ["d", "第 1 条"] and a["type"] == "answer" and a["id"] == "1"
    assert b["gold"] == ["d", "第 1 条"] and b["scenario"] == "hr"


def test_validation_catches_broken_suites() -> None:
    issues = evalkit.validate_cases([])
    assert issues and "空" in issues[0]

    bad = [case("a"), case("a"), {"id": "b", "type": "weird", "question": "q"},
           {"id": "c", "type": "answer", "question": "q"}]
    text = " ".join(evalkit.validate_cases(bad))
    assert "id 重复" in text and "type=" in text and "没有 gold" in text


def test_valid_suite_has_no_issues() -> None:
    assert evalkit.validate_cases([case(), case("r1", typ="refuse", gold=None)]) == []


def test_resolve_suite_by_name_and_path() -> None:
    assert evalkit.resolve_suite("campus", ROOT).name == "questions.json"
    with pytest.raises(FileNotFoundError) as err:
        evalkit.resolve_suite("不存在的套件", ROOT)
    assert "可用套件" in str(err.value)


def test_list_suites_only_returns_existing_files() -> None:
    for name, rel in evalkit.list_suites(ROOT).items():
        assert (ROOT / rel).is_file(), f"{name} 指向不存在的 {rel}"


# ---------------------------------------------------------------- ② 指标可插拔

def test_register_metric_shows_up_in_aggregate_and_specs(monkeypatch) -> None:
    spec = kit_metrics.MetricSpec("my_metric", "测试用", "count", "永远算得出",
                                  lambda rows, cases, o: (len(rows), None))
    original = list(kit_metrics.METRICS)
    try:
        evalkit.register_metric(spec)
        rows = [evalkit.score_row(case(), ok_response())]
        agg = evalkit.aggregate(rows, [case()], k=3)
        assert agg["my_metric"] == 1
        assert "my_metric" in evalkit.metric_specs({"k": 3})
    finally:
        kit_metrics.METRICS = original


def test_a_broken_metric_does_not_break_the_report(monkeypatch) -> None:
    def boom(rows, cases, o):
        raise RuntimeError("炸了")

    spec = kit_metrics.MetricSpec("boom_metric", "故意抛异常", "count", "算不出", boom)
    original = list(kit_metrics.METRICS)
    try:
        evalkit.register_metric(spec)
        agg = evalkit.aggregate([evalkit.score_row(case(), ok_response())], [case()], k=3)
        assert agg["boom_metric"] is None
        assert any("boom_metric" in n and "计算失败" in n for n in agg["notes"])
    finally:
        kit_metrics.METRICS = original


# ---------------------------------------------------------------- ③ 缺失 ≠ 零

def test_missing_trace_and_citation_are_none_not_zero() -> None:
    rows = [evalkit.score_row(case(), {"knowledge_hit": False, "sources": [],
                                       "took_ms": 5})]
    agg = evalkit.aggregate(rows, [case()], k=3)
    assert agg["context_recall"] is None
    assert agg["context_precision"] is None
    assert agg["citation_validity"] is None
    assert agg["p95_ms"] == 5


def test_report_is_self_describing() -> None:
    rows = [evalkit.score_row(case(), ok_response())]
    agg = evalkit.aggregate(rows, [case()], k=3)
    report = evalkit.build_report(suite="campus", mode="recorded", cases=[case()],
                                  rows=rows, metrics=agg,
                                  specs=evalkit.metric_specs({"k": 3}))
    assert report["schema_version"] == evalkit.SCHEMA_VERSION
    scalar_keys = {k for k, v in agg.items()
                   if k not in ("buckets", "failure_buckets", "notes", "n_cases")}
    assert scalar_keys <= set(report["metric_specs"]), "有指标没写说明，null 会被当 0 读"
    for spec in report["metric_specs"].values():
        assert spec["unit"] and spec["desc"] and spec["missing"]


# ---------------------------------------------------------------- ④ transport

def test_recorded_transport_maps_and_reports_missing(tmp_path) -> None:
    path = tmp_path / "resp.json"
    path.write_text(json.dumps({"responses": {"c1": ok_response()}}), encoding="utf-8")
    t = evalkit.RecordedTransport.from_file(path)
    assert t.ask(case("c1"))["knowledge_hit"] is True
    assert t.ask(case("c2")) is None and t.missing == ["c2"]


def test_inproc_transport_calls_the_same_pipeline(monkeypatch) -> None:
    seen = {}

    class FakeResp:
        def model_dump(self):
            return {"knowledge_hit": True, "sources": [], "took_ms": 1}

    def fake_handle(req):
        seen["namespace"] = req.namespace
        seen["event_time"] = req.event_time
        return FakeResp()

    monkeypatch.setattr(svc, "handle_chat", fake_handle)
    t = evalkit.InProcTransport(namespace="hr", event_time="2025-06-01")
    assert t.ask(case())["knowledge_hit"] is True
    assert seen == {"namespace": "hr", "event_time": "2025-06-01"}


# ---------------------------------------------------------------- ⑤ CLI

def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "run_scenario_eval.py"), *args],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_cli_offline_run_writes_versioned_report(tmp_path) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([case("c1"), case("r1", typ="refuse", gold=None)],
                                ensure_ascii=False), encoding="utf-8")
    responses = tmp_path / "resp.json"
    responses.write_text(json.dumps({"c1": ok_response(),
                                     "r1": {"knowledge_hit": False, "sources": [],
                                            "took_ms": 7}}), encoding="utf-8")
    out = tmp_path / "report.json"
    r = _run_cli(["--cases", str(cases), "--responses", str(responses), "--out", str(out)])
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["schema_version"] == evalkit.SCHEMA_VERSION
    assert report["n_cases"] == 2 and report["aggregate"]["answer"]["passed"] == 1


def test_cli_refuses_empty_suite(tmp_path) -> None:
    cases = tmp_path / "empty.json"
    cases.write_text("[]", encoding="utf-8")
    out = tmp_path / "report.json"
    r = _run_cli(["--cases", str(cases), "--transport", "inproc", "--out", str(out)])
    assert r.returncode == 1 and "空" in r.stdout
    assert not out.exists(), "空题集不该产出报告"

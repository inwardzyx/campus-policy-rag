# -*- coding: utf-8 -*-
"""评测层（归因 + 指标 + gold 检查）的守门用例。

## 盯的四条
  ① **归因能把六种失败分开**：`retrieval_miss` / `rank_shift` / `budget_cut` /
     `gen_uncited` / `acl_leak` / `stale_version` 各给一条，且互相不会串；
  ② **没有 trace 就不猜原因**：`docs/` 旧路径没有 context 时，`context_available=False`，
     不把"不知道"硬写成 `retrieval_miss`；
  ③ **缺失 ≠ 零**：`context_recall` 拿不到时是 `None`，不是 0；
  ④ **gold 检查的空转必须报红**：扫到 0 条 gold 时脚本退出码非 0。

★ 为什么这些要单独测：归因是"调优往哪改"的唯一依据。它一旦错（比如把所有失败
  都归成 retrieval_miss），后面的预算/排序/模板改动就会全部打偏 —— 而且看起来一切正常。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

from kb import attribution, eval_metrics        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def case(cid="c1", typ="answer", gold=("制度.md", "第二章-第四条"), question="？"):
    return {"id": cid, "type": typ, "question": question, "gold": list(gold) if gold else None}


def response(hit=True, blocks=(), excluded=(), candidates=(), citation=None):
    """构造一个带 context trace 的响应；块用 (doc, clause, reason) 元组表示。"""
    ctx = {
        "trace_id": "t" * 32,
        "namespace": "hr",
        "blocks": [{"source": d, "clause": c, "chars": 100, "included": True, "reason": "fits"}
                   for d, c in [(b[0], b[1]) for b in blocks]],
        "excluded": [{"source": d, "clause": c, "chars": 100, "included": False, "reason": r}
                     for d, c, r in excluded],
        "candidates": [{"source": d, "clause": c, "rank": i + 1}
                       for i, (d, c) in enumerate(candidates)],
        "degraded": {},
        "budget": {"retrieved_cap": 1840},
    }
    if citation is not None:
        ctx["citation"] = citation
    sources = [{"doc": f"{d}｜{c}", "text": "正文", "score": 0.9}
               for d, c in [(b[0], b[1]) for b in blocks]]
    return {"knowledge_hit": hit, "sources": sources, "took_ms": 100, "context": ctx}


# ---------------------------------------------------------------- ① 六个桶

def test_gold_included_is_ok() -> None:
    got = attribution.classify(case(), response(blocks=[("制度.md", "第二章-第四条")]))
    assert got["bucket"] == "ok" and got["context_available"] is True


def test_gold_excluded_by_budget_is_budget_cut() -> None:
    got = attribution.classify(
        case(), response(blocks=[("制度.md", "第一条")],
                         excluded=[("制度.md", "第二章-第四条", "budget")]))
    assert got["bucket"] == "budget_cut"
    assert "budget" in got["reason"]


def test_gold_oversize_also_counts_as_budget_cut() -> None:
    got = attribution.classify(
        case(), response(excluded=[("制度.md", "第二章-第四条", "oversize")]))
    assert got["bucket"] == "budget_cut"


def test_gold_in_candidates_but_not_included_is_rank_shift() -> None:
    """★ 这条是"候选可见性"的判据：没有 candidates 字段时它会误判成 retrieval_miss。"""
    got = attribution.classify(
        case(), response(blocks=[("制度.md", "第一条")],
                         candidates=[("制度.md", "第一条"), ("制度.md", "第二章-第四条")]))
    assert got["bucket"] == "rank_shift"


def test_gold_absent_everywhere_is_retrieval_miss() -> None:
    got = attribution.classify(case(), response(blocks=[("别的.md", "第一条")]))
    assert got["bucket"] == "retrieval_miss"


def test_citation_absent_marks_gen_uncited_only_when_field_exists() -> None:
    """batch 2 之前没有 citation 字段 ⇒ 不许凭空判 gen_uncited。"""
    without = attribution.classify(case(), response(blocks=[("制度.md", "第二章-第四条")]))
    assert without["bucket"] == "ok"
    with_empty = attribution.classify(
        case(), response(blocks=[("制度.md", "第二章-第四条")],
                         citation={"used_ids": [], "unknown_ids": []}))
    assert with_empty["bucket"] == "gen_uncited"


def test_stale_reason_maps_to_stale_version() -> None:
    got = attribution.classify(
        case(), response(excluded=[("制度.md", "第二章-第四条", "stale")]))
    assert got["bucket"] == "stale_version"


def test_deny_case_hit_leaks_and_miss_is_ok() -> None:
    denied = attribution.classify(case(typ="deny", gold=None), response(hit=False))
    leaked = attribution.classify(case(typ="deny", gold=None),
                                  response(hit=True, blocks=[("hr.md", "第一条")]))
    assert (denied["bucket"], leaked["bucket"]) == ("ok", "acl_leak")


def test_refuse_case_hit_is_answered_unexpectedly() -> None:
    assert attribution.classify(case(typ="refuse", gold=None),
                                response(hit=True, blocks=[("x.md", "第一条")]))["bucket"] \
        == "answered_unexpectedly"
    assert attribution.classify(case(typ="refuse", gold=None),
                                response(hit=False))["bucket"] == "refuse_ok"


# ---------------------------------------------------------------- ② 没有 trace 时不猜

def test_no_context_does_not_invent_a_reason() -> None:
    resp = {"knowledge_hit": False, "sources": [], "took_ms": 10}
    got = attribution.classify(case(), resp)
    assert got["context_available"] is False
    assert "旧路径" in got["reason"], got


# ---------------------------------------------------------------- ③ 指标

def test_context_recall_is_none_without_trace() -> None:
    row = eval_metrics.score_row(case(), {"knowledge_hit": False, "sources": []})
    assert row["context_recall"] is None, "没有 trace 时必须报缺失，不能补 0"
    assert row["context_precision"] is None


def test_aggregate_rates_and_budget_cut_rate() -> None:
    cases = [case("a1"), case("a2"), case("a3"), case("r1", typ="refuse", gold=None)]
    responses = [
        response(blocks=[("制度.md", "第二章-第四条")]),                       # ok
        response(blocks=[("制度.md", "第一条")],
                 excluded=[("制度.md", "第二章-第四条", "budget")]),           # budget_cut
        response(hit=False, candidates=[("制度.md", "第二章-第四条")]),         # rank_shift
        {"knowledge_hit": False, "sources": [], "took_ms": 5},                 # refuse_ok
    ]
    rows = [eval_metrics.score_row(c, r) for c, r in zip(cases, responses)]
    agg = eval_metrics.aggregate(rows, cases)
    assert agg["answer"] == {"passed": 1, "total": 3, "rate": 0.3333,
                             "wilson_95": agg["answer"]["wilson_95"]}
    assert agg["refuse"]["rate"] == 1.0
    assert agg["failure_buckets"]["budget_cut"] == 1
    assert agg["failure_buckets"]["rank_shift"] == 1
    assert agg["budget_cut_rate"] == 0.3333
    assert agg["context_recall"] == 0.3333          # 只有第一题 gold 进了 included
    assert agg["citation_precision"] is None        # batch 2 之前恒为缺失


def test_ndcg_matches_ir_measures_ground_truth() -> None:
    """gold 排在第 2 位 ⇒ nDCG@3 = 1/log2(3)（用 ir-measures 现算，不手写实现）。

    ★ 这条在 CI 里会 skip：`pytrec-eval-terrier` 没有 Linux / cp314 的 wheel（实测），
      而 CI 不装评测依赖。所以本仓库的 nDCG 计算是**本地/评测机验证**，不是 CI 验证 ——
      这是已知缺口，别把它当成"CI 覆盖了指标正确性"。
    """
    pytest.importorskip("ir_measures", reason="评测依赖（requirements-eval.txt）未装")
    cases = [case("a1")]
    rows = [eval_metrics.score_row(cases[0],
                                   {"knowledge_hit": True, "took_ms": 10,
                                    "sources": [{"doc": "别的.md｜第一条", "text": ""},
                                                {"doc": "制度.md｜第二章-第四条", "text": ""}]})]
    agg = eval_metrics.aggregate(rows, cases)
    assert agg["ndcg@3"] == pytest.approx(0.6309, abs=1e-3)


def test_ndcg_missing_dependency_is_none_not_zero(monkeypatch) -> None:
    """拿不到 `ir_measures` 时必须是 `None` + 说明，**不许补 0**（缺失 ≠ 零）。"""
    monkeypatch.setitem(sys.modules, "ir_measures", None)
    value, note = eval_metrics._ndcg([], [case("a1")])
    assert value is None
    assert "ir_measures" in note


# ---------------------------------------------------------------- ④ gold 检查

def test_gold_check_passes_when_corpus_matches(tmp_path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_eval_gold", ROOT / "scripts" / "check_eval_gold.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    (tmp_path / "制度.md").write_text("# 制度\n\n## 第二章-第四条\n内容\n", encoding="utf-8")
    errors, checked = mod.check_cases([case()], tmp_path)
    assert (errors, checked) == ([], 1)


def test_gold_check_flags_missing_clause(tmp_path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_eval_gold2", ROOT / "scripts" / "check_eval_gold.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    (tmp_path / "制度.md").write_text("# 制度\n\n## 第一章-第一条\n", encoding="utf-8")
    errors, checked = mod.check_cases([case()], tmp_path)
    assert checked == 1 and len(errors) == 1 and "第二章-第四条" in errors[0]


def test_gold_check_zero_gold_exits_nonzero(tmp_path) -> None:
    """空转必须报红：题集里一条 gold 都没有时，退出码必须非 0。"""
    empty = tmp_path / "cases.json"
    empty.write_text(json.dumps([{"id": "r1", "type": "refuse", "question": "？",
                                  "gold": None}], ensure_ascii=False), encoding="utf-8")
    corpus = tmp_path / "kb"
    corpus.mkdir()
    r = subprocess.run([PY, str(ROOT / "scripts" / "check_eval_gold.py"),
                        "--cases", str(empty), "--corpus", str(corpus)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode != 0, f"零 gold 却报绿：{r.stdout[-300:]}"
    assert "空转" in (r.stdout + r.stderr)

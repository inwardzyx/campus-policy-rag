# -*- coding: utf-8 -*-
"""会话轨迹级评测（A+B）的守门用例。

## 盯的四条
  ① **轨迹的键存在**：带 session_id 的请求，trace 里必须有 session_id / turn_index，
     并且同一会话第二轮 turn_index=2；改写真的发生时才记录 `rewritten_query`；
  ② **改写可判**：`rewrite_hit` 按"改写后检索词是否含 gold 的条款号/文档名"判定，
     没有改写或没有 gold 时是 `None`（缺失≠0）；
  ③ **会话题集有校验**：空 turns / 轮次缺 gold 必须被抓；
  ④ **历史绝不进生成**：这是**静态性质**，用断言守，不靠指标"观察"。

★ 变异体 `scripts/mutate_session.py`：让 rewrite_hit 恒 False、把 session_id 从 trace 丢掉，
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
from kb.evalkit import session as session_mod    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def session_case(cid="s1", turns=None):
    return {"id": cid, "type": "session", "scenario": "t",
            "turns": turns or [
                {"question": "请假一天以内由谁审批？", "type": "answer",
                 "gold": ["学生请销假制度.md", "总则-第三条"]},
                {"question": "那一个礼拜呢？", "type": "answer",
                 "gold": ["学生请销假制度.md", "总则-第三条"]},
            ]}


def row(cid, bucket="ok", rewritten=None, gold=("学生请销假制度.md", "总则-第三条")):
    return {"id": cid, "bucket": bucket, "rewritten_query": rewritten,
            "gold": list(gold) if gold else None}


# ---------------------------------------------------------------- ② 改写判定

def test_rewrite_resolved_checks_ellipsis_filling_not_citations() -> None:
    """★ 判据是"省掉的上下文有没有被补回"，不是"改写里有没有条款号"。

    第一版判据要求改写含 gold 条款号/文档名 —— 实测 12 轮改写全都合理却全判 0，
    因为**查询改写产出的是问句、不是引用**。这条用例守住新口径。
    """
    prev = "请假一天以内由谁审批？"
    assert session_mod.rewrite_resolved("请假一周以内由谁审批？", "那一个礼拜呢？", prev) is True
    assert session_mod.rewrite_resolved("一个礼拜的假怎么请", "那一个礼拜呢？", prev) is False
    # 本轮本身是完整问题（没有省字）⇒ 不是它的考场，必须返回 None
    assert session_mod.rewrite_resolved("请一个礼拜的假由谁批", "我要请一个礼拜的假，需要哪一级批准？",
                                       prev) is None
    assert session_mod.rewrite_resolved(None, "那一个礼拜呢？", prev) is None


def test_session_scoring_counts_pass_rate_and_repeats() -> None:
    case = session_case()
    rows = [row("s1#1", rewritten="请假一天以内由谁审批"),
            row("s1#2", bucket="retrieval_miss", rewritten="请假一周以内由谁审批？")]
    got = session_mod.score_session(case, rows)
    assert got["turns"] == 2 and got["passed"] == 1
    assert got["turn_pass_rate"] == 0.5 and got["session_pass"] is False
    assert got["rewrite_used"] == 2
    # 只有第 2 轮是省字追问（第 1 轮没有上一轮），且补全了"请假" ⇒ 1/1
    assert got["ellipsis_turns"] == 1 and got["rewrite_resolved_rate"] == 1.0
    assert got["repeat_questions"] == 0


def test_repeated_question_is_flagged() -> None:
    turns = [{"question": "请假一天以内由谁审批？", "type": "answer",
              "gold": ["a.md", "第 1 条"]},
             {"question": "请假一天以内由谁审批？ ", "type": "answer",
              "gold": ["a.md", "第 1 条"]}]
    got = session_mod.score_session({"id": "s", "turns": turns},
                                    [row("s#1"), row("s#2")])
    assert got["repeat_questions"] == 1 and got["repeat_question_rate"] == 0.5


def test_aggregate_sessions_wilson_and_rates() -> None:
    sessions = [
        {"turns": 2, "passed": 2, "session_pass": True, "rewrite_used": 1,
         "ellipsis_turns": 1, "rewrite_resolved_rate": 1.0, "repeat_questions": 0},
        {"turns": 2, "passed": 1, "session_pass": False, "rewrite_used": 1,
         "ellipsis_turns": 1, "rewrite_resolved_rate": 0.0, "repeat_questions": 0},
    ]
    agg = session_mod.aggregate_sessions(sessions)
    assert agg["session_pass"]["passed"] == 1 and agg["n_sessions"] == 2
    assert agg["turn_pass_rate"] == 0.75 and agg["rewrite_resolved_rate"] == 0.5
    assert agg["ellipsis_turns"] == 2
    assert agg["session_pass"]["wilson_95"][0] > 0


# ---------------------------------------------------------------- ③ 校验

def test_validation_catches_bad_sessions() -> None:
    broken = [
        {"id": "a", "type": "session", "turns": []},
        {"id": "b", "type": "session", "turns": [{"question": "q"}]},
        {"id": "c", "type": "session", "turns": [{"question": "", "type": "refuse"}]},
    ]
    text = " ".join(evalkit.validate_cases(broken))
    assert "没有 turns" in text and "没有 gold" in text and "question 为空" in text


# ---------------------------------------------------------------- ① 轨迹的键

@pytest.fixture
def rag(monkeypatch):
    monkeypatch.setattr(svc.rag, "ready", True)
    state = {"cands": [{"doc": "学生请销假制度.md", "clause": "总则-第三条",
                        "text": "请假一天以内由辅导员审批。" * 3,
                        "text_full": "请假一天以内由辅导员审批。" * 3, "acl": ["public"]}]}
    monkeypatch.setattr(svc.rag, "search",
                        lambda q, k=5, namespace=None, acl=None:
                        [(c, 0.9) for c in state["cands"]])
    monkeypatch.setattr(svc.rag, "rerank",
                        lambda q, cands: [(c, s, 9, "假精排") for c, s in cands])
    monkeypatch.setattr(svc.rag, "answer", lambda q, chunks: "由辅导员审批[1]。")

    def fake_rewrite(self, q, prior_questions=None):
        return "请假一个礼拜的审批 总则-第三条" if prior_questions else q

    monkeypatch.setattr(svc.RAG, "rewrite_query", fake_rewrite)
    return state


def test_trace_carries_session_keys_and_turn_index(rag) -> None:
    sid = "sess-test-a"
    r1 = svc.handle_chat(svc.ChatRequest(question="请假一天以内由谁审批？",
                                         namespace="hr", session_id=sid))
    r2 = svc.handle_chat(svc.ChatRequest(question="那一个礼拜呢？",
                                         namespace="hr", session_id=sid))
    assert r1.context.session_id == sid and r1.context.turn_index == 1
    assert r2.context.session_id == sid and r2.context.turn_index == 2
    assert r1.context.rewritten_query is None, "第一轮没有历史，不该有改写"
    assert r2.context.rewritten_query and "第三条" in r2.context.rewritten_query


def test_rewrite_hit_is_computed_from_the_trace_not_guessed(rag) -> None:
    sid = "sess-test-b"
    svc.handle_chat(svc.ChatRequest(question="请假一天以内由谁审批？",
                                    namespace="hr", session_id=sid))
    r2 = svc.handle_chat(svc.ChatRequest(question="那一个礼拜呢？",
                                         namespace="hr", session_id=sid))
    resp = r2.model_dump()
    turn = {"id": "x#2", "type": "answer", "question": "那一个礼拜呢？",
            "gold": ["学生请销假制度.md", "总则-第三条"]}
    r = evalkit.score_row(turn, resp)
    assert r["rewritten_query"] == r2.context.rewritten_query
    # 指代补全：上一轮问"请假一天以内…"，本轮"那一个礼拜呢？"省掉了"请假"，
    # 改写结果（"请假一个礼拜的审批 总则-第三条"）把它补回来了。
    assert session_mod.rewrite_resolved(
        r["rewritten_query"], "那一个礼拜呢？", "请假一天以内由谁审批？") is True


def test_trace_itself_carries_session_keys() -> None:
    """★ 直接打 context 层：`select_retrieved` 收了 session_meta 就必须写进 trace。

    为什么单独一条：响应里的键可能来自 summary（service 层挂的），
    与 trace 里有没有是**两回事**。只测响应会漏掉 trace 这条路径
    （变异体 B 第一次就是这么"没变红"的）。
    """
    from kb import context as ctx_mod

    kept, trace = ctx_mod.select_retrieved(
        [{"doc": "d.md", "clause": "第三条", "text": "正文", "text_full": "正文"}],
        budget=ctx_mod.Budget(), namespace="hr",
        session_meta={"session_id": "s-1", "turn_index": 2,
                      "rewritten_query": "改写后的检索词"})
    d = trace.to_dict()
    assert (d["session_id"], d["turn_index"], d["rewritten_query"]) == \
        ("s-1", 2, "改写后的检索词")


# ---------------------------------------------------------------- ④ 历史不进生成

def test_history_never_reaches_generation_prompt() -> None:
    """★ 静态性质：生成 prompt 只有"本轮问题 + 本轮块"，不含任何历史问题。"""
    chunks = [{"doc": "d.md", "clause": "第三条", "text": "正文", "text_full": "正文",
               "cite_id": 1}]
    prompt = svc.RAG.build_answer_prompt("那一个礼拜呢？", chunks)
    assert "那一个礼拜呢？" in prompt
    assert "请假一天以内由谁审批" not in prompt, "历史漏进了生成 prompt —— 拒答设计被削弱"


# ---------------------------------------------------------------- ⑤ CLI（离线）

def test_cli_offline_session_report(tmp_path) -> None:
    cases = tmp_path / "sessions.jsonl"
    cases.write_text(json.dumps(session_case(), ensure_ascii=False) + "\n", encoding="utf-8")

    def resp(bucket_hit: bool, rewritten=None):
        ctx = {"trace_id": "t" * 32, "namespace": "hr", "blocks": [], "excluded": [],
               "degraded": {}, "budget": {}, "session_id": "s1-abc", "turn_index": 1,
               "rewritten_query": rewritten}
        return {"answer": "答", "knowledge_hit": bucket_hit, "sources": [], "took_ms": 10,
                "context": ctx}

    responses = {"s1#1": resp(False), "s1#2": resp(False, "请假一个礼拜 总则-第三条")}
    rp = tmp_path / "resp.json"
    rp.write_text(json.dumps(responses), encoding="utf-8")
    out = tmp_path / "report.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_session_eval.py"),
                        "--cases", str(cases), "--responses", str(rp), "--out", str(out)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["schema_version"] == "session-eval/1"
    assert report["n_sessions"] == 1
    assert report["aggregate"]["session_session_pass"]["total"] == 1
    assert report["aggregate"]["session_rewrite_used"] == 1

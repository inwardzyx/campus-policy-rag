# -*- coding: utf-8 -*-
"""会话轨迹打分：**只测我们真的有的那条跨轮链路**（查询改写）。

## 为什么只有这几个指标
系统的跨轮状态只有 `session.py` 里那份"最近 5 轮问题原文"（30 分钟 TTL），
而历史**只喂查询改写**、不喂精排与生成（刻意的安全性质，见 service.py 的注释）。
所以"轨迹"在这里不是 agent 的多步决策，而是：
  ① 这一轮有没有真的用上历史改写（`rewrite_used`）；
  ② 改写有没有解对指代（`rewrite_hit`：改写后的检索词里出现 gold 的条款号/文档名）；
  ③ 每一轮答得对不对（沿用 `evalkit.score_row` 的桶）；
  ④ 同一会话里有没有反复问同一句（`repeat_question_rate`）。
★ 不测 step/tool/goal —— 我们没有多步工具，硬造指标就是自欺（HANDOFF §5 写了触发条件）。

## 一个必须守住的静态性质
历史**绝不进生成 prompt**。这条不靠指标"观察"，靠用例**断言**：
`tests/test_session_eval.py::test_history_never_reaches_generation_prompt`。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from kb import attribution
from kb.eval_runner import wilson_interval

_CLAUSE_TOKEN = re.compile(r"第[一二三四五六七八九十百零\d]+条")
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub("", str(text or ""))


def _gold_terms(gold: Sequence[str] | None) -> list[str]:
    """gold 的"可核对词"：条款号（第三条）+ 文档名主干（学生请销假制度）。"""
    if not gold or len(gold) < 2:
        return []
    doc, clause = str(gold[0]), str(gold[1])
    terms = set(_CLAUSE_TOKEN.findall(clause))
    stem = Path(doc).stem
    if stem:
        terms.add(stem)
    return sorted(terms)


def _content_terms(text: str) -> list[str]:
    """取实词（长度 ≥2）。用 jieba 是因为它本来就在依赖里；分词只用于"补全判定"。"""
    import jieba                                  # 项目已有依赖，避免另引分词器

    return [t for t in jieba.cut(str(text or ""))
            if len(t) >= 2 and t.strip() and not t.isspace()]


#: 指代标记：以这些字开头的问题几乎一定是"接着上一轮说"
_REF_MARKERS = ("那", "这", "它", "其", "该")
#: 极短问题（≤8 字）也按省字追问算：完整问句极少这么短
_SHORT_CHARS = 8


def is_ellipsis_question(current_question: str, prev_question: str | None = None) -> bool:
    """这轮是不是"接着上一轮说的省略句"。

    ★ 判据必须是"形式"（指代标记 / 极短），不能是"上轮有、本轮没有的词多不多"：
      任何一句完整问题都不会逐字重复上一轮，用"缺词"判会把**完整问题也算成省略**，
      于是"改写有没有补全"这个指标的分母就被污染了（第一次就是这么错的）。
    """
    cur = str(current_question or "").strip()
    if not cur:
        return False
    if len(cur) <= _SHORT_CHARS:
        return True
    return cur.startswith(_REF_MARKERS)


def ellipsis_terms(current_question: str, prev_question: str) -> list[str]:
    """本轮"省掉"的上下文实词：上轮有、本轮没有的那些词（仅在省略句上才有意义）。"""
    cur = _norm(current_question)
    return [t for t in _content_terms(prev_question) if _norm(t) not in cur]


def rewrite_resolved(rewritten_query: str | None, current_question: str,
                     prev_question: str) -> bool | None:
    """改写结果是否把省掉的上下文补回来了。

    - 没有改写（第一轮 / 没触发）⇒ None（缺失≠0）；
    - 本轮不是省字追问（没有省掉的实词）⇒ None（不是它的考场）；
    - 否则：省掉的实词里至少一个出现在改写结果中 ⇒ True。
    """
    if not rewritten_query:
        return None
    if not is_ellipsis_question(current_question):
        return None                      # 本轮本身就是完整问题：不是它的考场
    missing = ellipsis_terms(current_question, prev_question)
    if not missing:
        return None
    text = _norm(rewritten_query)
    return any(_norm(t) in text for t in missing)


def score_session(case: Mapping[str, Any], turn_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """一个会话一行。`turn_rows` 与 `case["turns"]` 按下标对齐。"""
    turns = list(case.get("turns") or [])
    n = len(turns)
    passed = sum(1 for r in turn_rows if r.get("bucket") in ("ok", "refuse_ok"))

    rewrite_flags: list[bool] = []
    rewrite_used = 0
    for i, row in enumerate(turn_rows):
        rq = row.get("rewritten_query")
        if not rq:
            continue
        rewrite_used += 1
        # ★ 只统计"应答轮 + 有 gold"：拒答题的主题切换（"那食堂几点开饭？"）看着也像省字，
        #   但那里根本没有"该补全的上下文"，把它算进分母只会让指标恒定偏低。
        turn = turns[i] if i < len(turns) else {}
        if (row.get("type") or turn.get("type")) != "answer" or \
                not (row.get("gold") or turn.get("gold")):
            continue
        # ★ 疑问句以**题面**为准（row 里的 question 可能被截断或没带）：
        #   用 row 的会让"省字判定"静默失效 —— 第一次就是这么算出 ellipsis_turns=0 的。
        cur_q = (turns[i].get("question") if i < len(turns) else row.get("question")) or ""
        prev_q = (turns[i - 1].get("question")
                  if 0 <= i - 1 < len(turns) else "") or ""
        resolved = rewrite_resolved(rq, cur_q, prev_q)
        if resolved is not None:
            rewrite_flags.append(bool(resolved))

    questions = [_norm(t.get("question")) for t in turns]
    repeats = len(questions) - len(set(questions))

    return {
        "id": case.get("id"),
        "turns": n,
        "passed": passed,
        "turn_pass_rate": round(passed / n, 4) if n else None,
        "session_pass": bool(n) and passed == n,
        "rewrite_used": rewrite_used,
        # ★ 只统计"真省字"的轮次（分母可能小于 rewrite_used，报告里另给 ellipsis_turns）
        "ellipsis_turns": len(rewrite_flags),
        "rewrite_resolved_rate": (round(sum(rewrite_flags) / len(rewrite_flags), 4)
                                  if rewrite_flags else None),
        "repeat_questions": repeats,
        "repeat_question_rate": round(repeats / n, 4) if n else None,
        "buckets": {r["id"]: r.get("bucket") for r in turn_rows},
    }


def aggregate_sessions(sessions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """会话级汇总。比率都带 Wilson 区间，与单题口径一致（复用 eval_runner 的实现）。"""
    rows = list(sessions)
    n = len(rows)
    if not n:
        return {"n_sessions": 0, "session_pass": None, "turn_pass_rate": None,
                "rewrite_resolved_rate": None, "rewrite_used": None,
                "ellipsis_turns": None, "repeat_question_rate": None}

    passes = sum(1 for s in rows if s.get("session_pass"))
    turns = sum(int(s.get("turns") or 0) for s in rows)
    turn_passed = sum(int(s.get("passed") or 0) for s in rows)
    hits = [s["rewrite_resolved_rate"] for s in rows
            if s.get("rewrite_resolved_rate") is not None]
    ellipsis = sum(int(s.get("ellipsis_turns") or 0) for s in rows)
    used = sum(int(s.get("rewrite_used") or 0) for s in rows)
    repeats = sum(int(s.get("repeat_questions") or 0) for s in rows)
    lo, hi = wilson_interval(passes, n)
    return {
        "n_sessions": n,
        "n_turns": turns,
        "session_pass": {"passed": passes, "total": n,
                         "rate": round(passes / n, 4), "wilson_95": [lo, hi]},
        "turn_pass_rate": round(turn_passed / turns, 4) if turns else None,
        "rewrite_used": used,
        "ellipsis_turns": ellipsis,
        "rewrite_resolved_rate": round(sum(hits) / len(hits), 4) if hits else None,
        "repeat_question_rate": round(repeats / turns, 4) if turns else None,
    }

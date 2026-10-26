# -*- coding: utf-8 -*-
"""run_session_eval.py —— 会话级（多轮）评测入口。复用 evalkit 的题集/指标/报告。

## 它测什么（以及刻意不测什么）
测：每一轮答得对不对、这一轮有没有用历史改写、改写有没有解对指代、会话里有没有重复问。
**不测**：step/tool/goal（我们没有多步工具）——见 HANDOFF §5 的触发条件。

## 三种跑法
```powershell
# ① 打服务（需要 namespace：只有带 namespace 的响应里才有 trace/rewrite 可见）
... scripts/run_session_eval.py --cases evalset/scenarios/sessions/campus-followups.jsonl `
    --namespace campus_eval --bootstrap-docs docs/kb `
    --base-url http://127.0.0.1:8028 --out evalset/report-sessions-campus-followups.json

# ② 离线复算：响应文件键是 "<case_id>#<turn_index>"（1 起）
... scripts/run_session_eval.py --responses <响应文件.json>

# ③ 直连管道（不起 HTTP；模型需已加载）
... scripts/run_session_eval.py --transport inproc --namespace campus_eval
```
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb import evalkit                             # noqa: E402
from kb.evalkit import session as session_mod      # noqa: E402

SCHEMA = "session-eval/1"


def _turn_cases(case: dict) -> list[dict]:
    """把一个会话摊平成逐轮 case（id=`<session>#<turn>`），供 score_row 复用。"""
    out = []
    for i, turn in enumerate(case.get("turns") or [], start=1):
        out.append({"id": f"{case.get('id')}#{i}",
                    "type": turn.get("type", "answer"),
                    "question": turn.get("question"),
                    "gold": turn.get("gold"),
                    "scenario": case.get("scenario")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="evalset/scenarios/sessions")
    ap.add_argument("--suite", default=None)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--namespace", default=None)
    ap.add_argument("--bootstrap-docs", default=None)
    ap.add_argument("--responses", default=None, help="离线：键为 <case_id>#<turn>")
    ap.add_argument("--transport", default=None, choices=["http", "recorded", "inproc"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pace", type=float, default=0.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    spec = Path(args.cases)
    if spec.is_dir():
        files = sorted(spec.glob("*.jsonl"))
        if not files:
            print(f"✗ {spec} 下没有 .jsonl 会话套件")
            return 1
        path = files[0]
        print(f"· 目录下有 {len(files)} 个会话套件，本次用 {path.name}")
    else:
        try:
            path = evalkit.resolve_suite(args.cases, ROOT)
        except FileNotFoundError as exc:
            print(f"✗ {exc}")
            return 1

    sessions = evalkit.load_cases(path)
    if args.limit:
        sessions = sessions[:args.limit]
    issues = evalkit.validate_cases(sessions)
    if issues:
        print("✗ 会话题集有问题：")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    mode = args.transport or ("recorded" if args.responses else "http")
    if mode == "recorded":
        transport = evalkit.make_transport("recorded", responses=args.responses)
        print(f"· 离线复算：{args.responses}")
    elif mode == "inproc":
        transport = evalkit.make_transport("inproc", namespace=args.namespace)
    else:
        transport = evalkit.make_transport("http", base_url=args.base_url,
                                           namespace=args.namespace, pace=args.pace)
        if args.bootstrap_docs:
            uploaded = transport.bootstrap_docs(args.bootstrap_docs)
            print(f"· 灌入 {len(uploaded)} 份语料到 namespace={args.namespace}")

    t0 = time.time()
    session_rows = []
    all_turn_rows = []
    all_turn_cases = []
    for case in sessions:
        sid = f"{case['id']}-{uuid.uuid4().hex[:8]}"
        rows = []
        for turn in _turn_cases(case):
            payload = dict(turn, session_id=sid)
            resp = transport.ask(payload)
            row = evalkit.score_row(turn, resp if resp is not None else {"knowledge_hit": False})
            if resp is None:
                row["bucket"] = "no_response"
                row["reason"] = "没有收到该轮的响应"
            rows.append(row)
            all_turn_rows.append(row)
            all_turn_cases.append(turn)
        session_rows.append(session_mod.score_session(case, rows))

    turn_metrics = evalkit.aggregate(all_turn_rows, all_turn_cases, k=3)
    sess_metrics = session_mod.aggregate_sessions(session_rows)
    metrics = {f"session_{k}": v for k, v in sess_metrics.items()}
    metrics.update({k: v for k, v in turn_metrics.items() if k != "buckets"})
    metrics["turn_buckets"] = turn_metrics.get("buckets")

    specs = {f"session_{k}": {"desc": d, "unit": u, "missing": m}
             for k, d, u, m in (
                 ("session_pass", "整个会话每题都对的会话占比", "rate", "无会话时为 None"),
                 ("turn_pass_rate", "逐轮正确率（会话内所有轮次合并）", "rate", "无轮次时为 None"),
                 ("rewrite_used", "真的用上历史改写的轮次数", "count", "0 = 没触发改写"),
                 ("ellipsis_turns", "真正「省字追问」的轮次数（改写指标的分母）", "count",
                  "没有省字追问时为 0"),
                 ("rewrite_resolved_rate", "省掉的上下文实词被改写补回的比率", "rate",
                  "没有省字追问或没触发改写时为 None"),
                 ("repeat_question_rate", "同会话内重复问题的比例", "rate", "无轮次时为 None"),
                 ("n_sessions", "会话数", "count", "恒可算"),
                 ("n_turns", "轮次数", "count", "恒可算"),
             )}
    specs.update(evalkit.metric_specs({"k": 3}))

    report = {
        "schema_version": SCHEMA,
        "suite": args.suite or path.stem,
        "mode": transport.name,
        "cases_file": str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path),
        "namespace": args.namespace,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wall_clock_ms": int((time.time() - t0) * 1000),
        "n_sessions": len(session_rows),
        "metric_specs": specs,
        "aggregate": metrics,
        "sessions": session_rows,
        "rows": all_turn_rows,
    }
    out = Path(args.out) if args.out else ROOT / f"evalset/report-sessions-{report['suite']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    sess = metrics["session_session_pass"]
    print(f"\n会话 {report['suite']}（{report['mode']}，{len(session_rows)} 个会话 / "
          f"{sess_metrics['n_turns']} 轮）")
    print(f"  整个会话全对: {sess['passed']}/{sess['total']} = {sess['rate']:.1%}  "
          f"Wilson {sess['wilson_95']}")
    print(f"  逐轮正确率: {sess_metrics['turn_pass_rate']}")
    print(f"  改写：用了 {sess_metrics['rewrite_used']} 轮，其中省字追问 "
          f"{sess_metrics['ellipsis_turns']} 轮，指代补全率 {sess_metrics['rewrite_resolved_rate']}")
    print(f"  重复提问率: {sess_metrics['repeat_question_rate']}")
    print(f"\n✓ 报告：{out}（schema {SCHEMA}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

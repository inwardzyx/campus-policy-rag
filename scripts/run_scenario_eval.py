# -*- coding: utf-8 -*-
r"""run_scenario_eval.py —— 请求级评测的 **CLI**（逻辑都在 `kb/evalkit/`）。

设计原则：脚本只负责"解析参数 + 选 transport + 打印"，不实现判分/指标/报告。
这样以后加会话轨迹评测时，只需要多一个组装方式，不会出现第二套指标实现。

## 三种跑法
```powershell
# ① 打服务（把 docs/kb 灌进 namespace，这样才有 context trace）
D:\Python-project\.venv\Scripts\python.exe scripts/run_scenario_eval.py `
    --suite campus --namespace campus_eval --bootstrap-docs docs/kb `
    --base-url http://127.0.0.1:8020 --out evalset/report-scenario-campus.json

# ② 离线复算（读录好的响应，不联网、不花钱）
... scripts/run_scenario_eval.py --responses <响应文件.json> --out <报告.json>

# ③ 直连管道（不起 HTTP 服务；需要模型已加载，测试里可用假模型）
... scripts/run_scenario_eval.py --transport inproc --suite campus
```

## 退出码
`0` 跑完并写出报告；`1` 题集不合法/空题集/找不到套件（**空转不许报绿**）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb import evalkit                             # noqa: E402
from kb import attribution                          # noqa: E402
from kb import runstate                             # noqa: E402


def _pick_transport(args) -> evalkit.transport.Transport:
    if args.transport:
        mode = args.transport
    elif args.responses:
        mode = "recorded"
    else:
        mode = "http"
    if mode == "recorded":
        if not args.responses:
            raise SystemExit("✗ --transport recorded 需要 --responses <文件>")
        t = evalkit.make_transport("recorded", responses=args.responses)
    elif mode == "inproc":
        t = evalkit.make_transport("inproc", namespace=args.namespace,
                                   event_time=args.event_time)
    else:
        t = evalkit.make_transport("http", base_url=args.base_url, namespace=args.namespace,
                                   acl=args.acl, event_time=args.event_time, pace=args.pace)
        if args.bootstrap_docs:
            uploaded = t.bootstrap_docs(args.bootstrap_docs, acl=args.bootstrap_acl)
            print(f"· 灌入 {len(uploaded)} 份语料到 namespace={args.namespace}")
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=None, help="题集路径或套件名（默认 campus）")
    ap.add_argument("--suite", default=None, help="套件名（写进报告；默认取题集名）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--namespace", default=None,
                    help="缺省 = docs/ 旧路径（没有 trace，只能判命中）")
    ap.add_argument("--acl", default=None)
    ap.add_argument("--event-time", default=None, help="as-of 时点 YYYY-MM-DD")
    ap.add_argument("--bootstrap-docs", default=None, help="先把该目录的 .md 灌进 namespace")
    ap.add_argument("--expect", default=None,
                    help="expect 映射文件（scripts/extract_answer_values.py 的产物）")
    ap.add_argument("--bootstrap-acl", default=None,
                    help="灌语料时的 acl（frontmatter 里的 acl 优先）")
    ap.add_argument("--responses", default=None, help="离线模式：录好的响应 {id: response}")
    ap.add_argument("--transport", default=None, choices=["http", "recorded", "inproc"])
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0 = 全跑）")
    ap.add_argument("--pace", type=float, default=0.0, help="每题之间停几秒（限流用）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--allow-invalid", action="store_true",
                    help="题集有问题是只告警不退出（默认退出：坏题集会静默失真）")
    args = ap.parse_args()

    spec = args.cases or "campus"
    try:
        path = evalkit.resolve_suite(spec, ROOT)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1
    cases = evalkit.load_cases(path)
    if args.limit:
        cases = cases[:args.limit]

    if args.expect:
        mapping = json.loads(Path(args.expect).read_text(encoding="utf-8")).get("expect") or {}
        for case in cases:
            if str(case.get("id")) in mapping:
                case["expect"] = mapping[str(case["id"])]
        print(f"· 注入 expect：{len(mapping)} 条")

    issues = evalkit.validate_cases(cases)
    if issues:
        print("✗ 题集有问题：")
        for issue in issues:
            print(f"  - {issue}")
        if not args.allow_invalid:
            return 1
        print("（--allow-invalid：继续跑，但上面的题会让指标失真）")

    # ★ 用套件名跑时，报告标签应该是套件名（campus），不是文件名（questions）——
    #   否则同一套题在报告里会出现两个名字，看板按 suite 聚合就会裂开。
    known = evalkit.list_suites(ROOT)
    suite = args.suite or (spec if spec in known else path.stem)
    transport = _pick_transport(args)
    # ★ 题集可能在仓库外（临时文件/别人给的套件）—— relative_to 会抛 ValueError，
    #   所以这里兜底成绝对路径，别让"只是打印一行"把整个评测搞挂。
    try:
        shown = path.relative_to(ROOT)
    except ValueError:
        shown = path
    print(f"· 套件 {suite}（{shown}），{len(cases)} 题，transport={transport.name}")

    # ★ 拍状态快照（run-state/1）：让这份报告能自证"我是在哪个系统上跑出来的"。
    #   必须**开跑之前**拍 —— 跑完再拍的话，中途改了文件/prompt 就会记成改后的状态，
    #   那正好是最需要它准确的那一刻出错。
    #   为什么放在这里而不是"单独一个脚本让人记得跑"：忘记拍的代价是回到"数字是孤儿"，
    #   而这恰好是本项目已经踩过的坑 ⇒ 自动做，不依赖记性。
    rs = runstate.record(ROOT, namespace=args.namespace or "",
                         note=f"suite={suite}",
                         docs_dir=args.bootstrap_docs or "")
    print(f"· 状态指纹 {rs['state_fingerprint']}（run {rs['run_id']}）")

    t0 = time.time()
    rows = []
    try:
        for case in cases:
            resp = transport.ask(case)
            rows.append(evalkit.score_row(case, resp if resp is not None else
                                          {"knowledge_hit": False}))
            if resp is None:
                rows[-1]["bucket"] = "no_response"
                rows[-1]["reason"] = "没有收到该题的响应"
    except evalkit.TransportError as exc:
        # ★ 限流/过载要**中断**，不能产出"看起来变差了"的报告（实测：429 被当成答错，0/17）
        print(f"\n✗ 评测中断：{exc}")
        return 1
    elapsed = int((time.time() - t0) * 1000)

    metrics = evalkit.aggregate(rows, cases, k=3)
    specs = evalkit.metric_specs({"k": 3})
    report = evalkit.build_report(
        suite=suite, mode=transport.name, cases=cases, rows=rows,
        metrics=metrics, specs=specs,
        meta={"cases_file": (str(path.relative_to(ROOT))
                            if str(path).startswith(str(ROOT)) else str(path)),
              "namespace": args.namespace, "event_time": args.event_time,
              "limit": args.limit or None, "wall_clock_ms": elapsed,
              # ★ 系统状态（三层 versioning 的第三层）：这两个字段让"这个数字属于哪个系统"
              #   成为**机器可判**的事实，而不是靠人回忆。回滚/配对都靠它。
              "run_id": rs["run_id"], "state_fingerprint": rs["state_fingerprint"],
              "run_state": rs["run_dir"],
              "missing_responses": [r["id"] for r in rows if r["bucket"] == "no_response"]})
    out = Path(args.out) if args.out else ROOT / f"evalset/report-{suite}.json"
    evalkit.write_report(report, out)
    # 报告复制进 run 目录：状态和它的结果待在同一个文件夹里，拷走就能复盘
    runstate.attach_report(ROOT, rs["run_id"], out)

    print()
    print(evalkit.format_summary(report))
    print(f"\n✓ 报告：{out}（schema {evalkit.SCHEMA_VERSION}）")
    print(f"✓ 状态：{rs['run_id']}（指纹 {rs['state_fingerprint']}，回滚用 scripts/rollback.py）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

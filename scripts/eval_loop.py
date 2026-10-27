#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval_loop.py —— 评测闭环 v0：失败回收 → 候选 → 门禁 → 回滚。

## 闭环长什么样

```
跑评测（基线）→ 从失败的题里挖"候选"（查询扩展词）
      → 用候选再跑一遍（同名题集、同进程外的新进程）
      → 门禁（净收益、无回退、拒答不回退、无越权）
          → accept：写 registry，active 指向候选，服务用 EXPAND_LEXICON 加载
          → reject：候选留档，active 不变
```

## 为什么候选是"查询扩展词"
失败大头是"检索没抓到"（用户词 vs 条款词对不上）。扩展词改的是**数据文件**，
不是代码：回滚 = 把 active 指回上一版 / 置空；不用改代码、不用重新部署。

## 子命令
- `mine`：从报告里挖候选（不跑模型，纯读文件）
- `gate`：用两份报告做配对裁决（纯函数）
- `run`：串联 mine → 两次评测 → gate → registry（**会调模型**）
- `rollback`：把 active 回退到上一个 accept 的版本（或置空）

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\eval_loop.py run `
    --suite campus --corpus docs/kb --out-dir evalset/loop --min-gain 1
```
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb.loopgate import apply_significance, decide  # noqa: E402

HEADING = re.compile(r"^(#{1,6})\s*(.+?)\s*$", re.M)
STOP = set("的 了 是 在 有 和 与 及 或 对 把 被 为 从 到 中 上 下 什么 哪些 怎么 如何 多少 哪 吗 呢 请 问 学校 学生 规定".split())


# ------------------------------------------------------------------ 工具

def load_report(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_corpus(corpus: Path) -> dict[tuple[str, str], str]:
    """把语料切成 {(文件名, 条款标题): 正文}，用于挖候选词。"""
    out: dict[tuple[str, str], str] = {}
    for md in sorted(corpus.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        marks = list(HEADING.finditer(text))
        for idx, m in enumerate(marks):
            title = m.group(2).strip()
            start = m.end()
            end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
            body = text[start:end].strip()
            if body:
                out[(md.name, title)] = body
    return out


def tokens(text: str) -> list[str]:
    import jieba

    return [t for t in jieba.lcut(text) if len(t) >= 2 and t not in STOP and not t.isdigit()]


def _find_clause(corpus: dict[tuple[str, str], str], doc: str, clause: str) -> str | None:
    if (doc, clause) in corpus:
        return corpus[(doc, clause)]
    norm = clause.replace(" ", "")
    for (d, title), body in corpus.items():
        if d == doc and (title.replace(" ", "") in norm or norm in title.replace(" ", "")):
            return body
    return None


# ------------------------------------------------------------------ mine

def mine(report: dict, corpus_dir: Path, max_entries: int = 12) -> list[dict]:
    corpus = parse_corpus(corpus_dir)
    entries: list[dict] = []
    missed = [r for r in report.get("rows") or []
              if r.get("type") == "answer" and r.get("bucket") != "ok" and r.get("gold")]
    for row in missed:
        doc, clause = row["gold"][0], row["gold"][1]
        gold_text = _find_clause(corpus, doc, clause)
        if not gold_text:
            continue
        q_tokens = tokens(row.get("question") or "")
        g_tokens = tokens(gold_text)
        missing = [t for t in g_tokens if t not in q_tokens]
        if not missing or not q_tokens:
            continue
        # add 词：在 gold 里出现越多次越优先（同分按长度）
        add = sorted(dict.fromkeys(missing), key=lambda t: (-g_tokens.count(t), -len(t)))[:3]
        entries.append({
            "id": f"cand-{row['id']}",
            "source_case": row["id"],
            "question": row.get("question"),
            # ★ 用**整句**做 match：只影响这一道题。
            #   取前两个词（如"公司"）会误伤拒答题 —— 第一次真实闭环就是这么被门禁拦下的。
            "match": [row.get("question") or ""],
            "add": add,
        })
        if len(entries) >= max_entries:
            break
    return entries


# ------------------------------------------------------------------ gate / registry

def load_registry(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": "eval-loop/1", "active": None, "history": []}


def save_registry(path: Path, reg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def gate_reports(before_path: Path, after_path: Path, candidates_path: Path,
                 registry_path: Path, min_gain: int = 1, alpha: float | None = None) -> dict:
    before = load_report(before_path)
    after = load_report(after_path)
    verdict = decide(before.get("rows") or [], after.get("rows") or [], min_gain=min_gain)
    if alpha is not None:
        verdict = apply_significance(verdict, alpha)

    reg = load_registry(registry_path)
    loop_id = f"loop-{time.strftime('%Y%m%d-%H%M%S')}"
    entry = {
        "id": loop_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "candidates": str(candidates_path),
        "before": str(before_path),
        "after": str(after_path),
        "gate": verdict,
        "status": "active" if verdict["verdict"] == "accept" else "rejected",
    }
    reg["history"].append(entry)
    if verdict["verdict"] == "accept":
        reg["active"] = {"id": loop_id, "candidates": str(candidates_path), "gate": verdict}
    save_registry(registry_path, reg)
    return entry


def rollback(registry_path: Path) -> dict | None:
    reg = load_registry(registry_path)
    accepted = [h for h in reg.get("history", []) if h.get("status") == "active"]
    if not accepted:
        reg["active"] = None
        save_registry(registry_path, reg)
        return None
    accepted.pop()                                   # 当前这版不要
    reg["active"] = ({"id": accepted[-1]["id"], "candidates": accepted[-1]["candidates"],
                      "gate": accepted[-1]["gate"]} if accepted else None)
    for h in reg["history"]:
        if h["id"] == (accepted[-1]["id"] if accepted else None):
            h["status"] = "active"
    save_registry(registry_path, reg)
    return reg["active"]


# ------------------------------------------------------------------ run

def _wait_health(port: int, timeout: float = 120.0) -> None:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(1.0)
    raise SystemExit(f"服务 {port} 在 {timeout}s 内没就绪")


def _run_eval(cases: str, out: Path, env_extra: dict, corpus: str | None, port: int,
              namespace: str | None = None) -> None:
    """起一个**真实 HTTP 服务**跑评测，跑完杀掉。

    ★ 为什么不用 inproc：inproc 要求先 rag.startup()，runner 不会自动做 ——
      第一次跑闭环时它给出 0/15（假失败），把门禁结论带偏。HTTP 服务启动即加载语料，
      与线上同源。
    """
    env = {**os.environ, "RATE_BURST": "1000", "RATE_PER_MIN": "10000",
           "PYTHONIOENCODING": "utf-8", **env_extra}
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "service:app", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_health(port)
        cmd = [sys.executable, str(ROOT / "scripts" / "run_scenario_eval.py"),
               "--transport", "http", "--base-url", f"http://127.0.0.1:{port}",
               "--cases", cases, "--out", str(out)]
        if namespace:
            cmd += ["--namespace", namespace]
        if corpus:
            cmd += ["--bootstrap-docs", corpus]
        print("→ " + " ".join(cmd))
        r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = (r.stdout or "").strip().splitlines()[-3:]
        for line in tail:
            print("   " + line)
        if r.returncode != 0:
            raise SystemExit(f"评测失败（exit {r.returncode}）：\n{(r.stdout or '')[-800:]}\n{(r.stderr or '')[-800:]}")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=20)
        except subprocess.TimeoutExpired:
            srv.kill()


def cmd_run(args) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline = out_dir / "baseline.json"
    candidate = out_dir / "candidate.json"
    cands = out_dir / "candidates.json"
    registry = out_dir / "registry.json"

    def _env(spec: str | None) -> dict:
        out = {}
        for part in (spec or "").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    base_env = _env(args.baseline_env)
    cand_env = _env(args.candidate_env) if args.candidate_env is not None else dict(base_env)

    print("① 基线评测（无扩展词）")
    _run_eval(args.suite, baseline, {**base_env, "EXPAND_LEXICON": ""}, args.corpus, 8071,
              args.namespace)

    print("② 从失败题挖候选")
    entries = mine(load_report(baseline), Path(args.corpus), args.max_candidates)
    cands.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    print(f"   挖到 {len(entries)} 条候选 → {cands}")
    if not entries:
        print("✗ 基线全对，没有可挖的失败（闭环无事可做）")
        return 1

    print("③ 候选评测（EXPAND_LEXICON=候选）")
    _run_eval(args.suite, candidate, {**cand_env, "EXPAND_LEXICON": str(cands)}, args.corpus, 8072,
              args.namespace)

    print("④ 门禁裁决")
    entry = gate_reports(baseline, candidate, cands, registry, args.min_gain, args.alpha)
    g = entry["gate"]
    print(json.dumps({"verdict": g["verdict"], "reasons": g["reasons"],
                      "gains": g["gains"], "losses": g["losses"]}, ensure_ascii=False))
    print(f"   registry → {registry}")
    return 0 if g["verdict"] == "accept" else 2


def cmd_mine(args) -> int:
    entries = mine(load_report(args.report), Path(args.corpus), args.max_candidates)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"✓ {len(entries)} 条候选 → {out}")
    return 0 if entries else 1


def cmd_gate(args) -> int:
    entry = gate_reports(Path(args.before), Path(args.after), Path(args.candidates),
                         Path(args.registry), args.min_gain, args.alpha)
    print(json.dumps(entry["gate"], ensure_ascii=False, indent=2))
    return 0 if entry["gate"]["verdict"] == "accept" else 2


def cmd_rollback(args) -> int:
    active = rollback(Path(args.registry))
    print(json.dumps(active, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="评测闭环 v0")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mine", help="从报告挖候选（不跑模型）")
    m.add_argument("--report", required=True)
    m.add_argument("--corpus", default="docs/kb")
    m.add_argument("--max-candidates", type=int, default=12)
    m.add_argument("--out", required=True)
    m.set_defaults(func=cmd_mine)

    g = sub.add_parser("gate", help="用两份报告裁决")
    g.add_argument("--before", required=True)
    g.add_argument("--after", required=True)
    g.add_argument("--candidates", required=True)
    g.add_argument("--registry", default="evalset/loop/registry.json")
    g.add_argument("--min-gain", type=int, default=1)
    g.add_argument("--alpha", type=float, default=None,
                   help="给值则启用严格模式：McNemar 不显著就拒（如 0.05）")
    g.set_defaults(func=cmd_gate)

    r = sub.add_parser("run", help="完整闭环（会调模型）")
    r.add_argument("--suite", default="campus")
    r.add_argument("--namespace", default=None,
                   help="评测用的 namespace（企业语料必须给，否则会查默认语料）")
    r.add_argument("--baseline-env", default="",
                   help="基线服务的额外环境变量（KEY=VAL,KEY2=VAL2），用于演示退化-修复")
    r.add_argument("--candidate-env", default=None,
                   help="候选服务的额外环境变量；缺省=沿用 baseline-env")
    r.add_argument("--corpus", default="docs/kb")
    r.add_argument("--out-dir", default="evalset/loop")
    r.add_argument("--max-candidates", type=int, default=12)
    r.add_argument("--min-gain", type=int, default=1)
    r.add_argument("--alpha", type=float, default=None,
                   help="给值则启用严格模式：McNemar 不显著就拒（如 0.05）")
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("rollback", help="回退 active")
    b.add_argument("--registry", default="evalset/loop/registry.json")
    b.set_defaults(func=cmd_rollback)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

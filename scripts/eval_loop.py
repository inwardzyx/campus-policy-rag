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
from kb import runstate                             # noqa: E402

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
    # ★ 只挖"检索/排序"类失败：查询扩展词救不了 budget_cut / gen_uncited（盲审 P2-1）
    MINEABLE = ("retrieval_miss", "rank_shift")
    missed = [r for r in report.get("rows") or []
              if r.get("type") == "answer" and r.get("bucket") in MINEABLE and r.get("gold")]
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

#: 服务启动时默认加载的候选（`EXPAND_LEXICON` 未设时用它）
ACTIVE_LEXICON = ROOT / "evalset" / "loop" / "active.json"


def _write_active(source: Path) -> None:
    src = Path(source)
    if not src.exists():
        print(f"· 候选文件不存在，跳过写 active：{src}")
        return
    ACTIVE_LEXICON.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_LEXICON.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _clear_active() -> None:
    if ACTIVE_LEXICON.exists():
        ACTIVE_LEXICON.unlink()


def load_registry(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": "eval-loop/1", "active": None, "history": []}


def save_registry(path: Path, reg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _state_of(report: dict) -> dict | None:
    """从报告 meta 找回那次跑的 `state.json`。

    meta 里有两个线索：`run_state`（相对仓库根目录的 run 目录）与 `run_id`。
    前者优先（它指向的那份就是当时拍的原文），后者兜底（目录被清理过也能试一下）。
    """
    meta = report.get("meta") or {}
    rel = meta.get("run_state")
    if rel:
        p = Path(str(rel))
        p = p if p.is_absolute() else ROOT / p
        sp = p / "state.json"
        if sp.exists():
            try:
                return json.loads(sp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
    run_id = meta.get("run_id")
    if run_id:
        try:
            return runstate.load(ROOT, str(run_id))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
    return None


def gate_reports(before_path: Path, after_path: Path, candidates_path: Path,
                 registry_path: Path, min_gain: int = 1, alpha: float | None = None,
                 allow_env: tuple[str, ...] = ("EXPAND_LEXICON",),
                 require_state: bool = True) -> dict:
    """配对裁决。**先验状态，再比题目。**

    ★ 为什么状态要在题目之前判（2026-10-28 加）：
      门禁原本只看"哪几题对错变了"。但两次跑如果**不是同一个系统**（代码改了、
      语料漂了、`.env` 里某个旋钮没清），"变好"就可能是环境给的，不是候选给的。
      本项目已经踩过这个：+1 的增益小于同日同配置基线的自身波动（41↔39），
      于是诚实的结论只能是"没有证据"。
      ⇒ 现在：两次跑的差异必须**只落在已声明的旋钮里**（`allow_env`），否则 reject。
    """
    before = load_report(before_path)
    after = load_report(after_path)
    verdict = decide(before.get("rows") or [], after.get("rows") or [], min_gain=min_gain)
    if alpha is not None:
        verdict = apply_significance(verdict, alpha)

    # ---- 状态判据（前置）----
    state_info: dict = {"ok": True, "skipped": True, "reasons": []}
    if require_state:
        sb, sa = _state_of(before), _state_of(after)
        if sb is None or sa is None:
            state_info = {
                "ok": False, "skipped": False,
                "reasons": [f"缺少状态快照（{'before' if sb is None else 'after'} 找不到 state.json）"
                            f"⇒ 无法证明两次跑的是同一个系统"],
                "deltas": [], "unexpected": [], "allow_env": list(allow_env),
            }
        else:
            state_info = runstate.state_match(sb, sa, allow_env=allow_env)
            state_info["skipped"] = False
        if not state_info["ok"]:
            verdict = dict(verdict)
            verdict["verdict"] = "reject"
            verdict["reasons"] = list(verdict.get("reasons") or []) + state_info["reasons"]

    reg = load_registry(registry_path)
    loop_id = f"loop-{time.strftime('%Y%m%d-%H%M%S')}"
    accepted = verdict["verdict"] == "accept"
    entry = {
        "id": loop_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "candidates": str(candidates_path),
        "before": str(before_path),
        "after": str(after_path),
        "gate": verdict,
        # ★ 状态判据单独留一份：以后要复盘"当时为什么拒"，看这里比看 reasons 快
        "state": state_info,
        "allow_env": list(allow_env),
        "status": "active" if accepted else "rejected",
    }
    # ★ 状态机：同一时刻只能有一个 active，其余 accepted 标记 superseded（盲审 P1-3）
    if accepted:
        for h in reg.get("history", []):
            if h.get("status") == "active":
                h["status"] = "superseded"
        reg.setdefault("accepted_order", []).append(loop_id)
        reg["active_id"] = loop_id
    reg["history"].append(entry)
    reg["active"] = (({"id": loop_id, "candidates": str(candidates_path), "gate": verdict})
                     if accepted else reg.get("active"))
    save_registry(registry_path, reg)

    # ★ 真正的消费方：把候选写成服务启动时会加载的文件（盲审 P1-3：之前没人读 registry）
    if accepted:
        _write_active(candidates_path)
    return entry


def rollback(registry_path: Path) -> dict | None:
    """退回上一个 accept 的版本；没有就清空。

    ★ 旧实现每次把 history 里所有 active 都算进去、只 pop 一个、被弹出的仍标 active，
      连退两次会卡死（盲审 P1-3）。现在用显式的 accepted_order 栈。
    """
    reg = load_registry(registry_path)
    order: list[str] = list(reg.get("accepted_order") or [])
    if order:
        order.pop()                              # 丢掉当前这版
    by_id = {h["id"]: h for h in reg.get("history", [])}
    for h in reg.get("history", []):
        if h.get("status") in ("active", "superseded"):
            h["status"] = "superseded"
    reg["accepted_order"] = order
    current = order[-1] if order else None
    if current and current in by_id:
        reg["active_id"] = current
        reg["active"] = {"id": current, "candidates": by_id[current]["candidates"],
                         "gate": by_id[current]["gate"]}
        by_id[current]["status"] = "active"
        _write_active(Path(by_id[current]["candidates"]))
    else:
        reg["active_id"] = None
        reg["active"] = None
        _clear_active()
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


def _allow_env(spec: str | None) -> tuple[str, ...]:
    """`--allow-env` 解析：逗号分隔，空串 = 不声明任何干预。"""
    return tuple(k.strip() for k in (spec or "").split(",") if k.strip())


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
    entry = gate_reports(baseline, candidate, cands, registry, args.min_gain, args.alpha,
                         allow_env=_allow_env(args.allow_env),
                         require_state=not args.no_state_gate)
    g = entry["gate"]
    print(json.dumps({"verdict": g["verdict"], "reasons": g["reasons"],
                      "gains": g["gains"], "losses": g["losses"],
                      "state_ok": entry["state"].get("ok")}, ensure_ascii=False))
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
                         Path(args.registry), args.min_gain, args.alpha,
                         allow_env=_allow_env(args.allow_env),
                         require_state=not args.no_state_gate)
    print(json.dumps({**entry["gate"], "state": entry["state"]}, ensure_ascii=False, indent=2))
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
    g.add_argument("--allow-env", default="EXPAND_LEXICON",
                   help="声明本次干预会改动的环境键（逗号分隔）；差异不在此列 ⇒ 直接 reject")
    g.add_argument("--no-state-gate", action="store_true",
                   help="跳过状态判据（只用于复盘 run-state 之前产出的历史报告）")
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
    r.add_argument("--allow-env", default="EXPAND_LEXICON",
                   help="声明本次干预会改动的环境键（逗号分隔）；差异不在此列 ⇒ 直接 reject")
    r.add_argument("--no-state-gate", action="store_true",
                   help="跳过状态判据（不建议：那样门禁证明不了『只改了一处』）")
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("rollback", help="回退 active")
    b.add_argument("--registry", default="evalset/loop/registry.json")
    b.set_defaults(func=cmd_rollback)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

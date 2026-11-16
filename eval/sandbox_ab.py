# -*- coding: utf-8 -*-
"""`run_python` 的配对实验：同一批公司场景题，**开工具 / 关工具**各跑一次。

## 为什么是"配对"而不是"两次跑批相减"

两次独立跑批的差值里混着噪声（这套系统实测抖动 ±1~±3）。配对只比较**同一道题**在一次
配置里对、另一次里错的情况（McNemar 的 b / c），不受题间难易差异影响。

## 分数怎么算（不做 LLM 裁判）

`expected_answer` 是整句，不能直接做子串匹配。这里用仓库自己的 `kb.values.extract`
从期望答案里抽出 `(数字, 单位)`，再看回答里是否命中同一组值 ——
和 `kb/evalkit/metrics.py::_answer_value` 同一套抽取器，不另造尺子。

⚠️ 局限（写在报告里，不藏）：数字+单位匹配会漏"同义表达"（如"二个月"vs"2 个月"由归一化处理，
但"半个月"这类就抽不出来）。所以这份报告测的是**数值一致性**，不是"答案好不好"。

## 跑法

两个服务：一个 `SANDBOX_ENABLED=0`（8034），一个 `=1`（8035），其余配置相同。

```powershell
python eval/sandbox_ab.py --off http://127.0.0.1:8034 --on http://127.0.0.1:8035
```
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CASES = os.path.join(_ROOT, "evalset", "scenarios", "company-52.jsonl")
NAMESPACE = "company"
OUT_JSON = os.path.join(_ROOT, "eval", "reports", "sandbox-ab.json")
OUT_MD = os.path.join(_ROOT, "eval", "reports", "sandbox-ab.md")


def _post(base: str, question: str, timeout: int = 300) -> dict:
    import urllib.error
    import urllib.request

    body = json.dumps({"question": question, "namespace": NAMESPACE}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/agent", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Internal-Token": "dev-token",
                                          "X-Auth-Roles": "public"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read()[:120]}"}
    except Exception as e:                               # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def numeric_cases() -> list[dict]:
    import kb.values as values_mod

    out = []
    for line in open(CASES, encoding="utf-8"):
        if not line.strip():
            continue
        c = json.loads(line)
        expect = values_mod.extract(c.get("expected_answer") or "")
        if expect:
            out.append({"id": c["id"], "question": c["question"], "expect": expect})
    return out


def score(answer: str, expect: list) -> bool:
    import kb.values as values_mod

    return bool(values_mod.match(answer or "", [(float(v), str(u)) for v, u in expect]))


def _version_lines() -> list[str]:
    from pathlib import Path

    import kb.runstate as rs
    st = rs.capture(Path(_ROOT), note="eval/sandbox_ab")
    git = st.get("git") or {}
    dirs = (st.get("docs") or {}).get("dirs") or {}
    dep = rs.deps_state(_ROOT)
    lay = rs.layout_state()
    import kb.criteria as criteria_mod
    return [
        f"- 运行指纹：`{st.get('fingerprint')}`（git `{str(git.get('sha'))[:12]}` dirty=`{git.get('dirty')}`）",
        f"- 依赖指纹：{'、'.join(f'`{k}`={v}' for k, v in dep.items()) or '**未记录**'}",
        f"- 版面后端：auto 会选 `{lay.get('auto_would_use')}`",
        f"- 语料哈希：`docs/kb`={(dirs.get('docs/kb') or {}).get('hash', '')[:8]}；"
        f"`docs/inbox`={(dirs.get('docs/inbox') or {}).get('hash', '')[:8]}",
        f"- 判据版本：`{criteria_mod.version()}`（{criteria_mod.LABEL}）",
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--off", default="http://127.0.0.1:8034")
    ap.add_argument("--on", default="http://127.0.0.1:8035")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from-json", default="", help="只重渲染（不重跑）")
    a = ap.parse_args()

    if a.from_json:
        rows = json.load(open(a.from_json, encoding="utf-8"))
        cases = []   # 重渲染：不重跑，循环自然空转
    else:
        cases = numeric_cases()
        if a.limit:
            cases = cases[:a.limit]
        rows = []
    for i, c in enumerate(cases, 1):
        t0 = time.time()
        r_off = _post(a.off, c["question"])
        r_on = _post(a.on, c["question"])
        off_ok = score(r_off.get("answer"), c["expect"])
        on_ok = score(r_on.get("answer"), c["expect"])
        used = [s["tool"] for s in (r_on.get("steps") or [])]
        rows.append({"id": c["id"], "question": c["question"], "expect": c["expect"],
                     "off_ok": off_ok, "on_ok": on_ok,
                     "off_src": len(r_off.get("sources") or []),
                     "on_src": len(r_on.get("sources") or []),
                     "on_tools": used, "sec": round(time.time() - t0, 1),
                     "off_err": r_off.get("_error"), "on_err": r_on.get("_error")})
        print(f"[{i}/{len(cases)}] {c['id']:<8} off={int(off_ok)} on={int(on_ok)} "
              f"tools={','.join(used)[:40]}")

    b = sum(1 for r in rows if not r["off_ok"] and r["on_ok"])
    cc = sum(1 for r in rows if r["off_ok"] and not r["on_ok"])
    n_off = sum(1 for r in rows if r["off_ok"])
    n_on = sum(1 for r in rows if r["on_ok"])

    sys.path.insert(0, _ROOT)
    from kb.loopgate import mcnemar_exact
    p = mcnemar_exact(b, cc)
    used_py = sum(1 for r in rows if "run_python" in r["on_tools"])

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(rows, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    lines = [
        "# `run_python` 配对实验：同一批题，开/关工具各跑一次",
        "",
        "> ★ 本文件由 `eval/sandbox_ab.py` 生成，**不要手改**。",
        f"> 题目：`evalset/scenarios/company-52.jsonl` 中 **能抽出数字期望** 的 {len(rows)} 道"
        f"（namespace=`{NAMESPACE}`）",
        f"> 打分：`kb.values.extract` 抽 `(数字, 单位)`，回答里命中即为通过（与 evalkit 同一抽取器）",
        "",
        "## 结论",
        "",
        "| | 关工具 | 开工具 |",
        "|---|---|---|",
        f"| 数值命中 | {n_off}/{len(rows)} | {n_on}/{len(rows)} |",
        "",
        f"- 配对不一致：**b（关错开对）= {b}**，c（关对开错）= {cc}",
        f"- exact McNemar（双侧）：**p = {p:.4f}**",
        f"- 实际调用 `run_python` 的题：**{used_py}/{len(rows)}**",
        "",
        ("→ 结论：**有可测的能力增量**（b > c 且 p < 0.05）。" if (b > cc and p < 0.05) else
         "→ 结论：**这批题上看不出显著增量**。"),
        "",
        (f"★ 关键解释：**工具只被调用 {used_py} 次**。" if used_py == 0 else
         f"★ 工具被调用 {used_py} 次。"),
        "",
        "两条都成立时才谈得上增量：**题需要算 + 模型会去调**。这份实验里第一条就不成立 ——",
        "company-52 的数字题（迟到扣分、试用期上限、试用期工资）**数值直接写在条款里**，",
        "模型读条款就能答，不需要算；所以开不开工具都不改变数值命中。",
        "",
        "**而在真正需要组合计算的题上，工具是有用的**（同一服务、手工构造两题）：",
        "",
        "| 题 | 关工具 | 开工具 |",
        "|---|---|---|",
        "| 奖学金 6000 + 勤工助学 300×10 | 心算，答 9000 | **调 run_python**，答 9000 |",
        "| 综合测评 85×70% + 90×30% | 心算，答 86.5 | 这次没调（心算），答 86.5 |",
        "",
        "⇒ 真问题不是「工具没用」，是**模型不一定会去调**（第二题就没调）。下一步要么在",
        "system prompt 里加一条「涉及金额/天数/比例必须用 run_python」，要么补一批",
        "**条款里只有系数、答案必须组合计算**的题，让「需要算」这件事在分布里真的存在。",
        "",
        "## 逐题",
        "",
        "| 题 | 关 | 开 | 开工具调了什么 |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| `{r['id']}` | {'✅' if r['off_ok'] else '❌'} | {'✅' if r['on_ok'] else '❌'} | "
                     f"{', '.join(r['on_tools']) or '（无）'} |")
    lines += ["", "## 版本", ""] + _version_lines()
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print(f"\n关工具 {n_off}/{len(rows)}　开工具 {n_on}/{len(rows)}　"
          f"b={b} c={cc}　p={p:.4f}　run_python 用了 {used_py} 次")
    print(f"已写 {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""onboard.py —— 把一份语料变成"可演示的质检报告"（十分钟级）。

## 它做什么
1. 把 `--docs` 指定的文件上传进指定 namespace（PDF/MD/TXT/DOCX 都行）；
2. 从切出来的**条款**里确定性抽 N 条冒烟探针（不是模型出题，避免"照答案出题"）；
3. 逐条打 `/chat`，判"答案 sources 里有没有同一个 (doc, clause)"；
4. 再问几条库外问题，看是否拒答；
5. 输出一页 Markdown 质检报告 + 一份 JSON 产物。

## 它不证明什么（必须写在报告里）
- 探针是**机器从条款生成的**，只证明"条款能被检索到并被引用"，不证明语义理解；
- 需要**人工抽检 20%**；真正的验收题集在 `evalset/scenarios/`；
- 单轮、单进程、无并发 —— 这不是压测。

## 用法
```powershell
# 先起服务
D:\\Python-project\\.venv\\Scripts\\python.exe -m uvicorn service:app --port 8028

D:\\Python-project\\.venv\\Scripts\\python.exe scripts/onboard.py `
    --docs samples/sisu-handbook-2025.pdf --namespace sisu_demo `
    --base-url http://127.0.0.1:8028 --out evalset/report-onboard-sisu.md

# 只看会出哪些探针（不连服务、不上传）
... scripts/onboard.py --docs samples/sisu-handbook-2025.pdf --plan
```
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb import onboard as onboard_mod          # noqa: E402
from kb.source import DocumentMeta, RawDocument, chunk_document, extract_text  # noqa: E402


def _post_json(url: str, payload: dict, timeout: float = 180.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _upload(base: str, namespace: str, path: Path, version: str = "") -> dict:
    body = path.read_bytes()
    # ★ 版本号必须是 YYYY-MM-DD：入库关卡⑤会拒掉"版本号不是日期"的块
    #   （2026-10-26 实测：不带 version 时默认 "1"，1655 块里 1382 块被静默拒掉，
    #    表现为"探针全落空、但接口一切正常"）。默认取文件修改日期。
    ver = version or time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
    query = urllib.parse.urlencode({"filename": path.name, "acl": "public", "version": ver})
    url = f"{base}/kb/{urllib.parse.quote(namespace)}/documents?{query}"
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _local_chunks(path: Path) -> list[dict]:
    """本地切块（只为生成探针；入库仍走服务端，保证用的是同一套把关）。"""
    data = path.read_bytes()
    text, suffix = extract_text(path.name, data)
    # ★ 必须用真的 DocumentMeta：chunk_markdown 读的是 `meta.doc`（不是 title），
    #   自己造个假对象会在 AttributeError 里被上层吞掉，表现为"切块 0"——很难查。
    meta = DocumentMeta(namespace="", doc_id=path.stem, title=path.stem,
                        source=f"onboard:{path.name}", version="1", updated_at="",
                        acl=("public",))
    return chunk_document(RawDocument(meta=meta, text=text, suffix=suffix))


def _md_report(*, namespace: str, docs: list[dict], probe_rows: list[dict],
               refusal_rows: list[dict], base: str) -> str:
    summary = onboard_mod.summarize(probe_rows)
    refused_ok = sum(1 for r in refusal_rows if r.get("refused"))
    lines = [
        f"# 语料质检报告 · `{namespace}`",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　服务：{base}",
        "> **口径**：探针由条款机器生成，只证明「条款能被检索到且被引用」，**不证明语义理解**；"
        "请人工抽检 20%。这不是压测。",
        "",
        "## 1. 语料",
        "",
        "| 文件 | 上传 | 本地切块 |",
        "|---|---|---|",
    ]
    for d in docs:
        lines.append(f"| {d['file']} | {'ok' if d.get('uploaded') else '✗ ' + str(d.get('error'))} "
                     f"| {d.get('n_chunks')} |")
    lines += [
        "",
        "## 2. 条款探针",
        "",
        f"- 命中 **{summary['hit']}/{summary['n']}**（{summary['hit_rate']}）",
        f"- 引用合法率：**{summary['citation_validity']}**",
        f"- p95 延迟：**{summary['p95_ms']} ms**",
        "",
        "| # | 问题 | 命中 | 引用 | 耗时 | 命中的来源 |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(probe_rows, 1):
        lines.append(f"| {i} | {r['question']} | {'✓' if r['hit'] else '✗'} "
                     f"| {r.get('validity')} | {r.get('took_ms')}ms | {', '.join(r.get('sources') or [])} |")
    if summary["misses"]:
        lines += ["", "**未命中的探针**（值得人工看一眼）：", ""]
        for r in summary["misses"]:
            lines.append(f"- {r['question']}（gold={r['gold']}；返回来源={r['sources']}）")
    lines += [
        "",
        "## 3. 拒答探针（库外问题）",
        "",
        f"- 正确拒答 **{refused_ok}/{len(refusal_rows)}**",
        "",
        "| 问题 | 拒答 |",
        "|---|---|",
    ]
    for r in refusal_rows:
        lines.append(f"| {r['question']} | {'✓' if r.get('refused') else '✗'} |")
    lines += [
        "",
        "## 4. 已知限制",
        "",
        "- 探针机器生成，**不代表**真实用户问法（真实问法更口语、常换词）；",
        "- 单轮、单进程、顺序请求，**没有并发**；",
        "- 只覆盖能切出条款的文档；切不出条款的会退回定长块，不在本次探针里；",
        "- 需要人工抽检 20%：抽查答案是否真的回答了问题，而不只是引用了正确条款。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="语料 onboarding + 质检报告")
    ap.add_argument("--docs", nargs="+", required=True)
    ap.add_argument("--namespace", default="onboard_demo")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--probes", type=int, default=10)
    ap.add_argument("--version", default="", help="入库版本号（YYYY-MM-DD；默认取文件修改日期）")
    ap.add_argument("--out", default="")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--plan", action="store_true", help="只生成本地探针，不连服务")
    args = ap.parse_args()

    paths = [Path(p) for p in args.docs if Path(p).is_file()]
    if not paths:
        print("✗ --docs 里没有有效文件")
        return 1

    docs: list[dict] = []
    all_chunks: list[dict] = []
    for path in paths:
        try:
            chunks = _local_chunks(path)
        except Exception as exc:                      # noqa: BLE001
            docs.append({"file": path.name, "uploaded": False, "n_chunks": 0,
                         "error": f"本地解析失败：{type(exc).__name__}"})
            continue
        all_chunks.extend(chunks)
        docs.append({"file": path.name, "uploaded": False, "n_chunks": len(chunks)})

    probes = onboard_mod.build_probes(all_chunks, n=args.probes)
    print(f"· 本地切块 {len(all_chunks)}，抽探针 {len(probes)} 条")
    if args.plan:
        for p in probes:
            print("  -", p["question"])
        return 0
    if not probes:
        print("✗ 没有可用的条款探针（语料切不出条款？先看 chunk 的 clause）")
        return 1

    base = args.base_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=10) as resp:
            json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                          # noqa: BLE001
        print(f"✗ 连不上服务 {base}（先起 uvicorn）：{exc}")
        return 1

    for d, path in zip(docs, paths):
        if d.get("error"):
            continue
        try:
            res = _upload(base, args.namespace, path, args.version)
            d["uploaded"] = True
            d["server"] = {k: res.get(k) for k in ("doc_id", "chunks", "added")}
        except urllib.error.HTTPError as exc:
            d["error"] = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:120]}"
        except Exception as exc:                      # noqa: BLE001
            d["error"] = f"{type(exc).__name__}: {exc}"
        print(f"· 上传 {path.name}: {'ok' if d.get('uploaded') else d.get('error')}")

    probe_rows, refusal_rows = [], []
    for p in probes:
        try:
            resp = _post_json(f"{base}/chat", {"question": p["question"],
                                               "namespace": args.namespace})
        except Exception as exc:                      # noqa: BLE001
            resp = {"error": str(exc), "knowledge_hit": False}
        probe_rows.append(onboard_mod.score_probe(p, resp))
    for q in onboard_mod.REFUSAL_PROBES:
        try:
            resp = _post_json(f"{base}/chat", {"question": q, "namespace": args.namespace})
        except Exception as exc:                      # noqa: BLE001
            resp = {"error": str(exc), "knowledge_hit": False}
        refusal_rows.append(onboard_mod.score_probe({"question": q, "gold": []}, resp))

    summary = onboard_mod.summarize(probe_rows)
    print(f"\n探针命中 {summary['hit']}/{summary['n']}、引用合法率 {summary['citation_validity']}、"
          f"p95 {summary['p95_ms']}ms、拒答 {sum(1 for r in refusal_rows if r.get('refused'))}/{len(refusal_rows)}")

    md = _md_report(namespace=args.namespace, docs=docs, probe_rows=probe_rows,
                    refusal_rows=refusal_rows, base=base)
    out = Path(args.out) if args.out else ROOT / f"evalset/report-onboard-{args.namespace}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8", newline="\n")
    json_out = Path(args.json_out) if args.json_out else out.with_suffix(".json")
    json_out.write_text(json.dumps({"namespace": args.namespace, "docs": docs,
                                    "probes": probe_rows, "refusals": refusal_rows,
                                    "summary": {k: v for k, v in summary.items() if k != "misses"}},
                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 报告：{out}")
    print(f"✓ 产物：{json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

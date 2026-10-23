# -*- coding: utf-8 -*-
"""scripts/run_kb_eval.py —— 跑人工出的企业语料独立评测集

用法：
    D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\run_kb_eval.py `
      --url http://127.0.0.1:8000 --namespace demo `
      --file evalset\\enterprise_eval_sample.jsonl `
      --out evalset\\report-demo.json

★ 只调 /search（不烧 LLM token）；判分逻辑在 kb/eval_runner.py。
★ 如果服务限流（RATE_BURST 默认很小），跑 70 题会被 429；
  起服务时把 RATE_BURST / RATE_PER_MIN 调大，或用 --pace 放慢。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from kb.eval_runner import run_cases, summarize  # noqa: E402


def _load_cases(path: Path) -> list[dict]:
    cases = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{i} JSON 解析失败：{exc}") from exc
    if not cases:
        raise SystemExit(f"{path} 里没有题目 —— 空题集会制造假绿，不许跑。")
    return cases


def _post_json(url: str, payload: dict, retries: int = 3) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "X-Client-Id": "kb-eval"},
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            text = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {text}") from exc
        except Exception as exc:
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser(description="企业语料独立评测（人工题集）")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--acl", default="", help="默认角色，逗号分隔；case 里的 acl 优先")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--pace", type=float, default=0.0, help="每题之间 sleep 秒数，防限流")
    ap.add_argument("--out", default="", help="报告 JSON 落盘路径")
    args = ap.parse_args()

    cases = _load_cases(Path(args.file))
    default_acl = [x.strip() for x in args.acl.split(",") if x.strip()] or None

    def search(case: dict) -> dict:
        payload = {
            "question": case["question"],
            "top_k": args.top_k,
            "namespace": args.namespace,
        }
        acl = case.get("acl", default_acl)
        if acl:
            payload["acl"] = acl
        if args.pace:
            time.sleep(args.pace)
        return _post_json(args.url.rstrip("/") + "/search", payload)

    results = run_cases(cases, search)
    summary = summarize(results)
    summary["namespace"] = args.namespace
    summary["file"] = str(Path(args.file))
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n报告已写入：{out}")

    # 有一题都没跑成（全是 error）→ 报红；单纯某题没通过仍返回 0，由人看报告。
    if results and all("error" in r for r in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

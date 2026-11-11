# -*- coding: utf-8 -*-
"""eval/repro_old_code.py —— 复现「旧报告当时到底跑的是什么」，用来判断两组数**能不能相减**

## 为什么要有这个脚本

`evalset/report-vector-heldout.md` 落款 2026-10-10（commit `a9e4f85`）写着 Recall@5 35/44；
今天 `eval/` 的基线是 30/44（新脚本 + 新代码）。于是很容易顺口说一句"检索退化了"——
但那是**两个不同测量路**上的两个数：

```text
旧报告：commit a9e4f85 的 evalset/run_eval.py + 那一刻的 service.py（chat(req) 就是管道本身）
今天  ：今天的 evalset/run_eval.py           + 今天的 service.py（管道被拆成 handle_chat）
```

要谈差异，先得把它们摆到同一条路上。本脚本做的事就是**把测量路的四要素钉住并落盘**：

| 要素 | 怎么记录 |
|---|---|
| 代码版本 | worktree 的 `git rev-parse HEAD` + `git status --porcelain`（脏没脏） |
| 数据版本 | `evalset/heldout/questions.json` 的 sha256 + `docs/` 语料树的 sha256 |
| 判分脚本版本 | 实际执行的那个 `run_eval.py` 的 sha256（并注明它来自哪个 commit） |
| 入口语义 | 旧 service 有没有 `handle_chat`；没有则用 shim，并把"这是语义假设"写进产物 |

## 三种跑法（`--variant`）

- `old`：**旧脚本 + 旧代码** —— 复现旧报告当时的测量路。这一条是"旧产物准不准"的检验。
- `current-shim`：**当前脚本 + 旧代码** —— 唯一的跨版本路径，但它有两个硬伤，
  所以只能当诊断、**不能当"可比数"**：
    ① 旧 service 没有 `handle_chat`，必须打 shim `handle_chat = chat`
       （2026-10-13 才把管道从 `chat()` 里拆出来，所以这个映射是**假设**，不是事实）；
    ② 当前脚本会传 `use_rewrite=`，而旧 `ChatRequest` **没有这个字段** ——
       pydantic 默认 `extra='ignore'` ⇒ 它会被**静默丢弃**（不报错、不提示）。
       这正说明"换个脚本去跑旧代码"这条路本身不干净。
- `current`：**当前脚本 + 当前代码** —— 只做记录（正常基线请用 `run_eval.py` 自己跑）。

## 跑法

```powershell
git worktree add D:\\_repro a9e4f85          # 只需一次
D:\\Python-project\\.venv\\Scripts\\python.exe eval\\repro_old_code.py --variant old
D:\\Python-project\\.venv\\Scripts\\python.exe eval\\repro_old_code.py --variant current-shim
```

## 产物（都由本脚本生成，数字不手写）

    eval/reports/repro-old-code.json   —— 每次跑的完整记录（会追加，不覆盖历史）
    eval/reports/repro-old-code.md     —— 由那份 JSON 渲染
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import summarize                              # noqa: E402  ★ 复用同一把尺子解析数字

RECORD_JSON = HERE / "reports" / "repro-old-code.json"
RECORD_MD = HERE / "reports" / "repro-old-code.md"

SHIM = '''# -*- coding: utf-8 -*-
"""临时 shim：旧 service 没有 handle_chat（2026-10-13 才把管道从 chat() 拆出来）。
★ 这是**语义假设**（chat ≡ handle_chat），不是事实 —— 所以这条路径的结果只能当诊断。
★ 必须先自己把仓库根塞进 sys.path：本脚本的 sys.path[0] 是 evalset/，
  而 `import service` 要的是仓库根（2026-11-11 第一次跑就是漏了这句，ModuleNotFoundError）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import service as svc                               # noqa: E402

if not hasattr(svc, "handle_chat"):
    svc.handle_chat = svc.chat

# ★ 必须自己配 logging：run_eval.py 里的 basicConfig 在 `if __name__ == "__main__"` 下面，
#   被 import 时不会执行 ⇒ 报告那几行 INFO 会被直接丢掉（2026-11-11 实测：
#   退出码 0、一个字都没有 —— 直接跑是"全绿"，其实什么都没测）。
import logging                                      # noqa: E402
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

import _run_eval_current as m                       # noqa: E402
raise SystemExit(m.main())
'''


# 索引探针：只建库、不跑题，量"评测实际用的索引"到底是多大。
# ★ 为什么不能直接用 run_eval 日志里那句"入库 N 块"：那是**合计**，
#   等于 docs/ 默认语料 + kb_data/ 里 demo 的企业 namespace（若存在）。
#   而评测请求不带 namespace ⇒ search() 只查 default ⇒ 企业块压根不参与。
#   2026-11-11 我第一版就拿合计去比（332 vs 263），差点把"本机有 demo 数据"
#   写成"切分逻辑变了"。这条探针就是为了不再犯。
PROBE = '''
import hashlib
import json
import logging
import os
import sys

logging.disable(logging.INFO)
sys.path.insert(0, os.getcwd())
import service as svc

svc.rag.startup()
rag = svc.rag
# ★ `_doc_chunks` 是 2026-10-12 才加的属性，旧 commit 上没有 —— 用 namespace 兜底，
#   否则旧版本探出来的数是 None，而 None 会被读成"那个版本没有语料"（缺失≠零，这里要的是真值）。
docs = getattr(rag, "_doc_chunks", None)
if docs is None:
    docs = [c for c in rag.chunks if (c.get("namespace") or "default") == "default"]
print("PROBE:" + json.dumps({
    "docs_chunks": len(docs),
    "enterprise_chunks": len(rag.chunks) - len(docs),
    "rejected": len(rag.rejected),
    "chunk_text_sha256": hashlib.sha256(
        "\\n".join(sorted(c["text"] for c in docs)).encode("utf-8")).hexdigest(),
}, ensure_ascii=False))
'''


def probe_index(root: Path) -> dict:
    """在给定的代码版本里建一次库，量出评测真正用的那部分索引。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "LANGSMITH_TRACING": "false"}
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=str(root), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env)
    for line in (r.stdout or "").splitlines():
        if line.startswith("PROBE:"):
            return json.loads(line[len("PROBE:"):])
    return {"error": (r.stderr or r.stdout or "")[-300:]}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def tree_sha(root: Path, sub: str) -> tuple[str, int]:
    """目录树的 sha256（按相对路径排序后逐个文件摘要）—— 用来钉住"语料是哪一版"。"""
    base = root / sub
    items = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            items.append(f"{p.relative_to(root).as_posix()}:{sha256_file(p)}")
    h = hashlib.sha256("\n".join(items).encode("utf-8"))
    return h.hexdigest(), len(items)


def git(args, cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


def prepare(worktree: Path, variant: str) -> tuple[Path, dict]:
    """返回真正要执行的脚本路径，以及"我为了跑它改动了什么"的记录。"""
    notes = []
    if variant == "old":
        return worktree / "evalset" / "run_eval.py", {"script_origin": "worktree（旧 commit 自带）",
                                                     "worktree_modified": []}
    if variant == "current":
        return REPO / "evalset" / "run_eval.py", {"script_origin": "当前仓库",
                                                  "worktree_modified": []}
    if variant != "current-shim":
        raise SystemExit(f"未知 variant: {variant}")

    cur = REPO / "evalset" / "run_eval.py"
    dst = worktree / "evalset" / "_run_eval_current.py"
    shim = worktree / "evalset" / "_shim_run_current.py"
    dst.write_text(cur.read_text(encoding="utf-8"), encoding="utf-8")
    shim.write_text(SHIM, encoding="utf-8")
    notes.append("把当前的 evalset/run_eval.py 复制进 worktree（_run_eval_current.py）")
    notes.append("新增 shim _shim_run_current.py：handle_chat = chat（语义假设）")
    notes.append("当前脚本传的 use_rewrite 在旧 ChatRequest 上是 extra ⇒ 被 pydantic 静默丢弃")
    return shim, {"script_origin": "当前仓库（复制进 worktree 执行）",
                  "worktree_modified": [dst.name, shim.name], "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=["old", "current-shim", "current"])
    ap.add_argument("--worktree", default=r"D:\_repro")
    ap.add_argument("--questions", default="evalset/heldout/questions.json")
    ap.add_argument("--reset", action="store_true", help="清空历史记录（重做一整套时用）")
    args = ap.parse_args()

    wt = Path(args.worktree)
    if not (wt / "service.py").exists():
        raise SystemExit(f"{wt} 看起来不是 worktree（没有 service.py）")

    # ★ 旧/跨版本这两路在 worktree 里跑，current 这一路在**当前仓库**里跑 ——
    #   否则 "当前脚本+当前代码" 会被 worktree 的 service.py 顶掉，测的就不是它。
    root = REPO if args.variant == "current" else wt

    # ★ 钉版本：先记，再跑 —— 跑完再记就可能记到已经被我改过的状态
    before = {"commit": git(["rev-parse", "HEAD"], root),
              "status_clean": git(["status", "--porcelain"], root) == ""}
    q = root / args.questions
    q_sha = sha256_file(q)
    data_sha, n_files = tree_sha(root, "docs")
    script, how = prepare(wt, args.variant)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "LANGSMITH_TRACING": "false"}
    r = subprocess.run([sys.executable, str(script), "--questions", args.questions,
                        "--label", "repro"], cwd=str(root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    out = (r.stdout or "") + (r.stderr or "")

    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(out)
        log = Path(f.name)
    nums = summarize.parse_run_eval_report(log)
    log.unlink(missing_ok=True)
    # ★ 索引单独量（见 PROBE 的注释）：不能用日志里那句合计
    idx = probe_index(root)

    rec = {
        "variant": args.variant,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exit_code": r.returncode,
        "code": {"commit": before["commit"], "root": str(root),
                 "worktree_clean_before": before["status_clean"],
                 "worktree_clean_after": git(["status", "--porcelain"], root) == ""},
        "data": {"questions_sha256": q_sha, "docs_tree_sha256": data_sha, "docs_files": n_files},
        "index": idx,
        "script": {"path": str(script) if not str(script).startswith(str(wt))
                   else str(script.relative_to(wt)),
                   "sha256": sha256_file(script), **how},
        "python": sys.version.split()[0],
        "numbers": {k: list(v) for k, v in sorted(nums.items())},
        "raw_tail": "\n".join([ln for ln in out.strip().splitlines() if "入库" in ln][-1:]
                              + out.strip().splitlines()[-4:]),
    }
    RECORD_JSON.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if RECORD_JSON.exists() and not args.reset:
        history = json.loads(RECORD_JSON.read_text(encoding="utf-8"))
    history.append(rec)
    RECORD_JSON.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    render(history)
    print(f"[ok] {args.variant} exit={r.returncode} "
          f"docs_chunks={idx.get('docs_chunks')} enterprise={idx.get('enterprise_chunks')} "
          f"numbers={rec['numbers']}")
    print(f"[ok] 记录已追加：{RECORD_JSON}")
    return 0 if r.returncode == 0 else 1


def render(history: list[dict]) -> None:
    """把 JSON 渲染成 md —— 报告里一个数都不手写。"""
    lines = ["# 旧报告能不能复现 / 两组数能不能相减（脚本生成，别手改）", "",
             "> 生成者：`eval/repro_old_code.py`；数据来源：`eval/reports/repro-old-code.json`。", ""]

    # 汇总表：一眼看"同不同路"。变体的语义见脚本顶部 docstring。
    lines += ["| 变体 | 代码 | 判分脚本 | docs 块 | 企业 demo 块 | chunk 文本 sha | recall5 | no_miss | refuse_ok |",
              "|---|---|---|---|---|---|---|---|---|"]
    for rec in history:
        n, idx = rec["numbers"], rec["index"]
        cell = (lambda k: f"{n[k][0]}/{n[k][1]}" if k in n else "**空**")
        short = {"old": "旧代码", "current-shim": "旧代码+shim", "current": "当前代码"}[rec["variant"]]
        who = {"old": "旧（worktree）", "current-shim": "当前（复制进 worktree）",
               "current": "当前"}[rec["variant"]]
        lines.append(f"| `{rec['variant']}` | {short} `{rec['code']['commit'][:8]}` | {who} "
                     f"| {idx.get('docs_chunks', '?')} | {idx.get('enterprise_chunks', '?')} "
                     f"| `{str(idx.get('chunk_text_sha256', '?'))[:12]}…` "
                     f"| {cell('recall5')} | {cell('no_miss')} | {cell('refuse_ok')} |")
    lines.append("")

    for rec in history:
        lines += [f"## `{rec['variant']}`（{rec['at']}）", "",
                  f"- 代码：commit `{rec['code']['commit'][:12]}`（root `{rec['code']['root']}`），"
                  f"跑之前干净=`{rec['code']['worktree_clean_before']}`，"
                  f"跑之后干净=`{rec['code']['worktree_clean_after']}`",
                  f"- 数据：questions `{rec['data']['questions_sha256'][:16]}…`，"
                  f"docs 树 `{rec['data']['docs_tree_sha256'][:16]}…`（{rec['data']['docs_files']} 个文件）",
                  f"- 索引：docs 入库 {rec['index'].get('docs_chunks', '?')} 块 / "
                  f"拦下 {rec['index'].get('rejected', '?')} 块；企业 demo "
                  f"{rec['index'].get('enterprise_chunks', '?')} 块（本评测不查）；"
                  f"chunk 文本 sha `{str(rec['index'].get('chunk_text_sha256', '?'))[:16]}…`",
                  f"- 脚本：`{rec['script']['path']}` sha256 `{rec['script']['sha256'][:16]}…`"
                  f"（{rec['script']['script_origin']}）", f"- 退出码：{rec['exit_code']}", ""]
        if rec["script"].get("worktree_modified"):
            lines.append(f"- ⚠️ 为跑这一次改动了 worktree：{rec['script']['worktree_modified']}")
        for n in rec["script"].get("notes") or []:
            lines.append(f"  - {n}")
        if rec["numbers"]:
            lines += ["", "| 指标 | 通过/总数 |", "|---|---|"]
            for k, (a, b) in rec["numbers"].items():
                lines.append(f"| `{k}` | {a}/{b} |")
        else:
            lines += ["", "⚠️ **没解析出任何数字** —— 见下面的输出尾巴，别把空结果当 0。"]
        lines += ["", "```text", rec["raw_tail"], "```", ""]
    RECORD_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

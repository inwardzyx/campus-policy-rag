# -*- coding: utf-8 -*-
"""报告新鲜度检查：把「哪些结论已经过期」变成一条命令算出来的事实。

## 为什么需要它（这个仓库真实踩过的坑）

2026-10-10 的 `evalset/report-vector-heldout.md` 写着 Recall@5 35/44，今天同一套 105 题跑出来是
30/44。而 `scripts/check_readme_numbers.py` 一直是**绿的** —— 因为它比的是「README ↔ 产物」，
两份**一起**过期就是"一致"。它管不了另一半：**产物 ↔ 当前代码与数据**。本文件补的就是这一半。

## 判定语义（四种状态，缺字段不许当"新鲜"）

| 状态 | 判据 |
|---|---|
| `FRESH` | 报告头记的 git sha == 当前 HEAD，且 docs/kb、docs/inbox 两个语料哈希 == 当前值 |
| `STALE_CODE` | 代码变过（见下面那条"怎么判代码变过"） |
| `STALE_DATA` | 语料哈希不同（docs/kb 或 docs/inbox） |
| `STALE_BOTH` | 两者都不同 |
| `UNVERIFIABLE` | 报告头缺 git sha 或语料哈希 ⇒ **无法判断，不许当新鲜** |

★ 只比三个字段：`git_sha`、`docs/kb` 哈希、`docs/inbox` 哈希。
  **不比 runstate 的聚合指纹**：它含 dirty 与工作区未提交文件，而报告文件自己就在工作区里
  ⇒ 一改报告指纹就变，永远判 STALE。报告头里那句「判系统同一性看 git_sha + 语料哈希」就是这条。

★ 报告头写了 `dirty=True` ⇒ 该报告是在脏工作区产的，代码可能与 commit 不一致
  ⇒ 直接 `STALE_CODE`，原因里写明。

## 「代码变过」怎么判（这一条是**对规格的补充**，不是简化）

规格原话是「报告头的 git_sha == 当前 HEAD 才算 FRESH」。这条路对**已入库的报告是走不通的**：
报告在 HEAD=X 的工作区里生成（记下 X），然后作为新提交 C 入库 ⇒ HEAD 变成 C ≠ X，
于是**任何入库的报告一出生就是 STALE_CODE**（实测见 `--strict-sha` 的输出）。

⇒ 默认判据改为：`report_sha == HEAD`（同一次运行后立即检查时成立）
  **或** `report_sha` 是 HEAD 的祖先，且 `git diff --name-only <report_sha>..HEAD`
  里**没有任何影响结果的文件**（见 `NON_AFFECTING`）。
  换句话说：**只有"会影响基线数字的文件"变了才算代码变**；改 README/报告/测试不算。
  想回到字面口径就加 `--strict-sha`。

⚠️ `NON_AFFECTING` 是**排除清单**：清单外的一切都算"影响结果"。
⇒ 新增一个代码文件而忘了登记，检查会**误报 STALE**（宁可误报，不可漏报）。

## 引用判定（豁免挂在【引用】上，不挂在【报告】上）

扫 `README.md` / `START.md` / `DECISIONS.md` / `HANDOFF.md` / `eval/README.md` 里出现的报告路径：

- 被引用（该行**没有**历史标记）+ 非 FRESH ⇒ **失败**（exit 1），并点出 `文件:行号`
- 被引用 + 该行有标记（`历史快照` / `勿引用` / `旧代码` / `已过期` / `复现不出` / `不可复现`）⇒ 只打印
- 未被引用 ⇒ 只打印，不影响退出码（**历史快照合法存在**）

★★ 2026-11-11 修过一处真洞：豁免原来挂在**报告**上（在 `HISTORICAL` 清单里就永不放行），
  于是「一份 UNVERIFIABLE 的报告被未标历史地引用了两次」也能 exit 0 —— 「未标历史」那列成了装饰。
  规格要的是：
    · 报告过期      → 合法（历史快照本来就该过期）
    · 引用过期报告  → 不合法（这正是 README 引用不可复现数字的那个坑）
  ⇒ `HISTORICAL` 现在只有"这是历史快照（预期会过期）"这个**展示**含义，**不再授予任何豁免**。

## 用法

    python scripts/check_report_freshness.py            # 只打印，不红（日常改动不打断）
    python scripts/check_report_freshness.py --strict   # 有「被引用的过期/不可证」就 exit 1（CI 用）
    python scripts/check_report_freshness.py --strict-sha  # 字面口径：sha 必须等于 HEAD

退出码：0 = 没有"被引用的过期/不可证报告"；1 = 有；2 = 自身运行失败（读不到 git / 解析器崩）。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: 新一轮基线（会被这条判据拦住的那一类）
BASELINE_GLOB = "eval/reports/*.md"
#: 显式登记的历史快照：打印状态，但**永不阻断**（"过期"是它们的正常状态）
HISTORICAL = (
    "evalset/report-vector-heldout.md",      # 2026-10-10 的 held-out 快照（旧代码）
    "evalset/report-rerank-heldout.md",      # 同上（rerank 层）
    # ★ 一次性复现记录：它是"旧代码能不能复现"的证据，本来就不该新鲜。
    "eval/reports/repro-old-code.md",
    # ★ 记录类：噪音地板度量的是**模型抖动**，不是"某版本的结论"；它过期是正常的
    #   （换模型/prompt 之后本来就该重量）。引用它必须标「实测记录」。
    "eval/reports/noise-floor.md",
)
#: 扫引用的文档
DOCS = ("README.md", "START.md", "DECISIONS.md", "HANDOFF.md", "eval/README.md")
#: 引用行里出现这些词 ⇒ 认为作者是在**引用一段历史**，不算"拿它当当前结论"
#: 引用行里出现这些词 ⇒ 作者是在**明确引用一段历史**，不算"拿它当当前结论"。
#: 规格写法：`（历史快照，勿引用）`。
MARKERS = ("历史快照", "勿引用", "实测记录", "旧代码", "已过期", "复现不出", "不可复现", "过期声明")
#: ★ 改了也**不影响**基线数字的路径（排除清单；清单外一律算"影响结果"）
NON_AFFECTING = re.compile(
    r"^(?:"
    r"eval/reports/.*"                 # 报告自己
    r"|_reviews/.*"                    # 盲审记录
    r"|\.github/.*"                    # CI 配置（不改判分逻辑）
    r"|tests/.*"                       # 测试
    r"|scripts/.*"                     # 检查器 / 变异脚本（都不参与判分，改它们不影响基线数字）
    r"|docs?/.*\.md$"                  # 文档
    r"|.*\.md$"                        # 任何 markdown（README/START/DECISIONS/HANDOFF/报告说明）
    r"|eval/(?:repro_old_code|agreement|gate)\.py"  # 评测周边工具，不参与判分
    # ★ `summarize.py` 只把已记录的跑批折成 md（数字来自 JSON，不来自它自己的运行）：
    #   改它不改变一次测量的结果，只改变渲染。**代价**：md 与渲染器是否同步，本判据不管
    #   —— 改过渲染逻辑就自己重生成 md；数字定义的正确性由 tests/test_eval_reconcile.py 与
    #   `summarize --compare` 守。
    r"|eval/summarize\.py"
    r")$"
)

RE_GIT = re.compile(r"git\s+`([0-9a-f]{7,40})`")
RE_KB = re.compile(r"`docs/kb`[=:]\s*`?([0-9a-f]{8,64})`?")
RE_INBOX = re.compile(r"`docs/inbox`[=:]\s*`?([0-9a-f]{8,64})`?")
RE_DIRTY = re.compile(r"dirty\s*[=:]\s*`?(True|False|true|false)`?")


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{(r.stderr or '').strip()[:200]}")
    return (r.stdout or "").strip()


def current_state() -> dict:
    """当前 HEAD + 当前语料哈希。★ 语料哈希直接问 kb.runstate，不自己再算一遍（第二把尺子）。"""
    import kb.runstate as rs
    st = rs.capture(ROOT, note="check_report_freshness")
    dirs = (st.get("docs") or {}).get("dirs") or {}
    docs = {k: str((v or {}).get("hash") or "") for k, v in dirs.items()}
    # ★ 关键防线：拿不到当前语料哈希时，**不许**用它去比 ——
    #   `"abc…".startswith("")` 恒为 True，会把"语料没变"凭空判成真（假绿）。
    docs_ok = bool(docs.get("docs/kb")) and bool(docs.get("docs/inbox"))
    return {
        "sha": _git("rev-parse", "HEAD"),
        "dirty": bool((st.get("git") or {}).get("dirty")),
        "docs": docs,
        "docs_ok": docs_ok,
        "fingerprint": st.get("fingerprint"),
    }


def parse_header(path: Path) -> dict:
    """从报告头取三样：git sha、docs/kb 哈希、docs/inbox 哈希（取不到就是 None）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {"error": f"读不到：{e}"}
    head = "\n".join(text.splitlines()[:40])          # 只看头部，别把正文里的 sha 也捡进来
    m_git, m_kb, m_inbox = RE_GIT.search(head), RE_KB.search(head), RE_INBOX.search(head)
    m_dirty = RE_DIRTY.search(head)
    return {
        "git": m_git.group(1) if m_git else None,
        "docs_kb": m_kb.group(1) if m_kb else None,
        "docs_inbox": m_inbox.group(1) if m_inbox else None,
        "dirty": (m_dirty.group(1).lower() == "true") if m_dirty else None,
    }


def code_changed_since(sha: str) -> tuple[bool, list[str], str]:
    """report_sha → HEAD 之间有没有"会影响结果"的文件变过。

    返回 (变了没, 影响文件的清单, 说明)。sha 不被 git 认（例如手改成一个不存在的 sha）时
    返回 (True, [], "git 不认识这个 sha") —— 判 STALE_CODE 而不是崩。
    """
    r = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        # ★ 区分两种"不认识"：手改成不存在的 sha（报告坏了）vs 浅克隆（环境不完整）。
        #   后者是**假红**，原因必须说清楚 —— 否则人会去改报告，而真正该做的是 git fetch --unshallow。
        shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=str(ROOT),
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stdout.strip()
        if shallow == "true":
            return True, [], (f"拿不到 {sha[:12]}… 这个提交（本地是浅克隆）⇒ 判 STALE；"
                              f"先 `git fetch --unshallow` 再跑")
        return True, [], f"git 不认识这个 sha：{sha[:12]}…（可能被手改过）"
    r = subprocess.run(["git", "diff", "--name-only", f"{sha}..HEAD"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return True, [], f"无法 diff {sha[:12]}..HEAD"
    files = [f.strip() for f in (r.stdout or "").splitlines() if f.strip()]
    affecting = [f for f in files if not NON_AFFECTING.match(f)]
    if not files:
        return False, [], "与 HEAD 是同一个提交"
    if not affecting:
        return False, [], f"其后只改了不影响结果的文件（{len(files)} 个，如 {files[0]}）"
    return True, affecting, f"其后改了 {len(affecting)} 个影响结果的文件（如 {affecting[0]}）"


def classify(path: Path, cur: dict, *, strict_sha: bool) -> tuple[str, str]:
    h = parse_header(path)
    if h.get("error"):
        return "UNVERIFIABLE", h["error"]
    missing = [k for k in ("git", "docs_kb", "docs_inbox") if not h.get(k)]
    if missing:
        return "UNVERIFIABLE", f"报告头缺字段：{' / '.join(missing)}"

    if not cur.get("docs_ok", True):
        return "UNVERIFIABLE", "拿不到当前语料哈希（kb.runstate 没给出 docs/kb 或 docs/inbox）"

    cur_kb, cur_inbox = cur["docs"].get("docs/kb", ""), cur["docs"].get("docs/inbox", "")
    data_same = (h["docs_kb"].startswith(cur_kb) or cur_kb.startswith(h["docs_kb"])) and \
                (h["docs_inbox"].startswith(cur_inbox) or cur_inbox.startswith(h["docs_inbox"]))

    if strict_sha:
        code_same, why = (h["git"] == cur["sha"]), ("与 HEAD 相同" if h["git"] == cur["sha"]
                                                    else "sha 与 HEAD 不同（字面口径）")
    else:
        if cur["sha"].startswith(h["git"]) or h["git"].startswith(cur["sha"]):
            code_same, why = True, "与 HEAD 相同"
        else:
            code_same, affecting, why = code_changed_since(h["git"])
            code_same = not code_same

    if h.get("dirty") is True:                       # 脏工作区产的报告：代码不可信
        code_same = False
        why = "生成时工作区不干净（dirty=True）"

    if code_same and data_same:
        return "FRESH", why
    if code_same and not data_same:
        return "STALE_DATA", "语料哈希与当前不同（docs/kb 或 docs/inbox 变过）"
    if data_same and not code_same:
        return "STALE_CODE", why
    return "STALE_BOTH", f"{why}；且语料哈希也不同"


def scan_references(docs: tuple[str, ...]) -> dict[str, list[tuple[str, int, str, bool]]]:
    """{报告相对路径: [(文件, 行号, 原文, 是否标了历史记号)]}"""
    refs: dict[str, list[tuple[str, int, str, bool]]] = {}
    # ★ 也认 `reports/xxx.md` 这种相对写法（eval/README.md 里用的就是它）——
    #   否则"被引用"会静默漏掉，判据看着绿其实没扫到东西。
    pat = re.compile(r"(?:(eval/)?reports/[\w.\-]+\.md|evalset/report-[\w.\-]+\.md)")
    for d in docs:
        p = ROOT / d
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            for m in pat.finditer(line):
                target = m.group(0)
                if target.startswith("reports/"):    # 相对 eval/ 的写法，归一成仓库相对路径
                    target = "eval/" + target
                if "sanity" in target:               # 免得把 "check_artifact_sanity" 之类的词当报告
                    continue
                refs.setdefault(target, []).append((d, i, line.strip(), any(k in line for k in MARKERS)))
    return refs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="有被引用的过期/不可证报告就 exit 1（CI 用）")
    ap.add_argument("--strict-sha", action="store_true", help="字面口径：报告 sha 必须等于 HEAD")
    args = ap.parse_args()

    try:
        cur = current_state()
    except Exception as e:                            # noqa: BLE001
        print(f"[FAIL] 拿不到当前状态：{e}", file=sys.stderr)
        return 2

    # ★ 去重：`repro-old-code.md` 既在 glob 里、又在 HISTORICAL 里，
    #   不去重会打印两遍，"历史快照 N 份"这个数也会跟着虚高（自己算的数先别撒谎）。
    seen: dict[str, Path] = {}
    for p in sorted(ROOT.glob(BASELINE_GLOB)) + [ROOT / h for h in HISTORICAL if (ROOT / h).exists()]:
        seen.setdefault(p.resolve().as_posix(), p)
    reports = list(seen.values())
    if not reports:
        print("[FAIL] 一份报告都没扫到 —— 扫到 0 条不许报绿", file=sys.stderr)
        return 2

    refs = scan_references(DOCS)
    hist = {h for h in HISTORICAL}

    rows, blocking = [], []
    for p in reports:
        rel = p.relative_to(ROOT).as_posix()
        try:
            status, why = classify(p, cur, strict_sha=args.strict_sha)
        except Exception as e:                        # noqa: BLE001
            print(f"[FAIL] 解析 {rel} 时崩了：{e}", file=sys.stderr)
            return 2
        use = refs.get(rel, [])
        live = [u for u in use if not u[3]]
        is_hist = rel in hist       # 仅用于展示（"预期会过期"），**不再豁免任何引用**
        blocking_here = bool(live) and status != "FRESH"
        if blocking_here:
            blocking.append((rel, status))
        rows.append({
            "rel": rel, "hist": is_hist, "status": status, "why": why,
            "refs": len(use), "live": len(live), "block": blocking_here,
        })

    w = max(len(r["rel"]) for r in rows)
    print(f"当前 HEAD {cur['sha'][:12]}  语料 docs/kb={cur['docs'].get('docs/kb', '?')[:8]} "
          f"docs/inbox={cur['docs'].get('docs/inbox', '?')[:8]}"
          f"（工作区{'脏' if cur['dirty'] else '干净'}）\n")
    print(f"{'报告':<{w}}  {'状态':<13} {'被引用':<8} 原因")
    print("-" * (w + 13 + 8 + 42))
    for r in rows:
        use = f"{r['refs']} 处" + (f"（{r['live']} 处未标历史）" if r["live"] else "") if r["refs"] else "—"
        flag = " ❌" if r["block"] else ("  ·" if r["hist"] else "")
        print(f"{r['rel']:<{w}}  {r['status']:<13} {use:<8} {r['why']}{flag}")

    n_block = len(blocking)
    print()
    if n_block:
        print(f"✗ {n_block} 份**被引用**的报告不是 FRESH —— 引用它们的地方必须改成指向新鲜产物，"
              f"或把引用行标成历史快照（如 `（历史快照，勿引用）`）：", file=sys.stderr)
        for rel, st in blocking:
            print(f"    {rel}  →  {st}", file=sys.stderr)
            for f, line_no, text, _marked in [u for u in refs.get(rel, []) if not u[3]]:
                snippet = text if len(text) <= 110 else text[:110] + "…"
                print(f"        {f}:{line_no}  {snippet}", file=sys.stderr)
    else:
        print("✓ 没有「被引用的过期/不可证报告」"
              f"（历史快照 {sum(1 for r in rows if r['hist'])} 份：报告本身允许过期，"
              f"但引用它们必须带历史标记）")

    if args.strict and n_block:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

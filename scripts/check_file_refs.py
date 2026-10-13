# -*- coding: utf-8 -*-
"""核仓库里写下的【仓库内路径】是否真的存在 —— 文档/脚本里的路径也是"声称值"。

★ 为什么要有这一条（2026-10-13，一次扫出 4 个实例）：
    被引用、但**不存在**的文件（都是被 git 跟踪的文件里的引用）：
      · `tests/test_tooling_encoding.py`  ← scripts/check_token_report.py 的注释　refcheck-ignore
        （那道编码防线的真身是 tests/test_guard.py 里那条参数化用例 ——
          注释指错了地方，下一个人按注释去找、找不到，也就不会去补那 4 个用例）
      · `agent/probe_toolnode_errors.py`  ← agent/render_regression_report.py 与它生成的报告　refcheck-ignore
        （真身在 `mcp/probe_toolnode_errors.py`，少写了 4 个字符）
      · `evalset/report.md`               ← README　refcheck-ignore
        （那份数据的真身是 `evalset/report-vector.md`）
      · `tests/fixtures/dirty_inbox/README.md` ← tests/test_guard.py　refcheck-ignore
        （真身是 `tests/fixtures/dirty_inbox说明.md`）
    四个都是"读的人照着去找，找不到，然后什么都不会发生"。
    判据纪律里已经写过：**声称值必须有人盯**。路径也是声称值，
    而且比数字更隐蔽 —— 数字对不上会有人怀疑，路径指错了只会让人以为自己记错了。

★ 判定规则：两侧都**问 git**，不维护白名单（白名单会漂）——
    ① 被引用的文件在            ⇒ OK
    ② 不在，但被 .gitignore 忽略 ⇒ 不算错（运行时产物/私人笔记，别人 clone 本来就没有）
    ③ 其余不在                  ⇒ 报红（含"曾经在 git 里、现在被删了"）
    载体侧同理：被 .gitignore 忽略的载体（cc_*.md、CLAUDE.md 这类私人草稿）**不扫** ——
    它们里面写着"计划要建的 docs/kb/xxx.md"（还没建）与"已删除的方案文件"，　refcheck-ignore
    那是**草稿的正常内容**，不是指错路。本判据守的是【别人 clone 下来会读到的东西】。

★ 三种"故意写一个不存在的路径"的出路（别硬报红，也别默默放过）：
    · 运行时产物   ⇒ 什么都不用做：② 自动放过，并打印一行说明
    · 已删除的原型 ⇒ 加进 ALLOW，写清"为什么它该不在"
    · 正文里在**引述**一个错路径（如本文件的注释） ⇒ 那一行加 `refcheck-ignore`

★ 已知边界（写清楚，免得下一个人以为它管得比实际宽）：
    · 只认带扩展名的路径，且必须落在已知的仓库目录里（见 TOPDIRS）。
      不带扩展名的引用（如 "fixtures/dirty_inbox/04"）不查。
    · URL 整段先剥掉；不查外链是否可达（那是网络的事）。
    · 不查"小节/锚点是否还在"（如 `§8.2`）—— 那要读 markdown 结构。

跑法（仓库根目录）：
    python scripts/check_file_refs.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ★ 与同族脚本一致：✓✗ 在 cp936 里编不出来，钩子里 stdout 是管道 + GBK。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# 只认这些顶层目录 —— 否则 `json.load`、`os.path` 这类全被当成路径。
TOPDIRS = ("scripts/", "tests/", "agent/", "mcp/", "kb/", "evalset/",
           "docs/", "experiments/", "data/", "web/")

EXT = ("py", "md", "json", "jsonl", "sh", "yml", "yaml", "txt",
       "toml", "cfg", "sql", "csv", "html", "css", "js", "ts")

# ★ 路径里可能有中文文件名（本仓库真有：tests/fixtures/dirty_inbox说明.md）。
_NAME = r"A-Za-z0-9_\u4e00-\u9fff"
REF_RE = re.compile(rf"[{_NAME}][{_NAME}./-]*\.(?:" + "|".join(EXT) + r")\b")
URL_RE = re.compile(r"https?://\S+")
IGNORE_MARK = "refcheck-ignore"      # 整行跳过（用于正文引述错路径）

# ★ 明确要放过的不存在路径：**每一行都要经得起下一个人问"为什么"**。
ALLOW: dict[str, str] = {
    "evalset/probe_slot_gating.py":
        "C 槽位门控的原型，实测失败后**故意删除**（失败的正则留在仓库里是负资产）；"
        "experiments/_真实校语料入库实验.md 那一段原文已写明『已删除』",
}

CARRIER_GLOBS = ("*.md", "*.py", "scripts/**/*.py", "scripts/**/*.md",
                 "scripts/hooks/*", "tests/**/*.py", "agent/**/*.py",
                 "mcp/**/*.py", "kb/**/*.py", "evalset/**/*.md",
                 "experiments/**/*.md", "docs/**/*.md",
                 # ★ 2026-10-13 补：CI 文件也引用大量仓库内路径（脚本、Dockerfile、compose、
                 #   测试文件…），而它是最不能指错的地方 —— 指错了整条流水线直接红。
                 #   恰恰因为它"跑在远端"、本机跑不到，本判据之前一直漏着它。
                 ".github/**/*.yml")
SKIP_PARTS = (".git", ".venv", "node_modules", "__pycache__", ".pytest_cache")


def _git(*args: str, stdin: str | None = None) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=str(ROOT), input=stdin,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, (r.stdout or "")


def ignored(paths: list[str]) -> set[str] | None:
    """哪些 path 被 .gitignore 忽略。git 不可用/判不出 ⇒ 返回 None（不许当"没有忽略项"）。

    ★★ 环境事实（2026-10-13 实测，栽过一次）：**别用 `check-ignore --stdin`。**
      从 Python 的 subprocess 喂 stdin 给 git 时，git 一个路径都没收到
       ⇒ 退出码 1 + 空输出 ⇒ 本函数返回空集合
       ⇒ 于是"被 .gitignore 忽略的东西"全被当成"路径指错了"（报了一屏假红）。
      而同一台机器上 `git check-ignore <路径>`（位置参数）是好的。
      同一段代码在 PowerShell 管道里也正常 —— 所以这个坑只在"Python 喂 git stdin"时出现。
      ⇒ 改成位置参数分批传（每次 60 个，避免命令行过长）。
      ⚠️ 顺带：本函数**必须能证明自己在坏情况下会响** —— 见下面 _self_check()。
    """
    code, _ = _git("--version")
    if code != 0:
        return None
    if not paths:
        return set()
    out_all: set[str] = set()
    for i in range(0, len(paths), 60):
        chunk = paths[i:i + 60]
        code, out = _git("check-ignore", *chunk)
        # check-ignore：有被忽略的 ⇒ 0；一个都没有 ⇒ 1。两者都不是错误。
        if code not in (0, 1):
            return None
        out_all |= {ln.strip() for ln in out.splitlines() if ln.strip()}
    return out_all


def _self_check() -> str | None:
    """证明"问 git 要忽略集"这条通路真的在工作（否则整条判据是假绿）。

    ★ 做法：拿一个**确实被忽略且确实存在**的文件当探针（由 git 自己列出来，不是我写死的），
      再问一次 check-ignore —— 若它说"没被忽略"，那说明机制坏了。
      本项目已记过两次"扫不到东西的检查器一定在报绿"，这次是同一类。
    """
    code, out = _git("ls-files", "-oi", "--exclude-standard")
    if code != 0:
        return "git ls-files 跑不通 —— 判不出 .gitignore 忽略集"
    probe = next((ln.strip() for ln in out.splitlines() if ln.strip()), None)
    if probe is None:
        print("  （本仓库当前没有任何被忽略的文件，跳过忽略集自检）")
        return None
    got = ignored([probe])
    if got is None or probe not in got:
        return (f"忽略集自检没过：git 说 `{probe}` 被忽略（ls-files -oi 列出来了），"
                f"但 check-ignore 不认它 ⇒ 这条通路坏了")
    return None


def carriers() -> list[Path]:
    out: list[Path] = []
    for pat in CARRIER_GLOBS:
        for f in ROOT.glob(pat):
            if not f.is_file():
                continue
            if any(p in f.parts for p in SKIP_PARTS):
                continue
            out.append(f)
    return sorted(set(out))


def extract(text: str) -> tuple[list[tuple[int, str]], int]:
    """抽出 (行号, 路径)；第二个返回值是被 `refcheck-ignore` 跳过的行数。

    ★ 被跳过多少行要**报出来**：否则"哪条指错了就拿标记糊一下"会让这条判据
      变成摆设，而外面看着还是全绿。
    """
    hits: list[tuple[int, str]] = []
    silenced = 0
    for i, line in enumerate(text.splitlines(), 1):
        if IGNORE_MARK in line:
            silenced += 1
            continue
        clean = URL_RE.sub(" ", line)
        for m in REF_RE.finditer(clean):
            # 取"以已知顶层目录开头"的那个后缀：
            #   `$REPO/agent/_token_cost.json` → `agent/_token_cost.json`
            parts = m.group(0).split("/")
            rel = next(("/".join(parts[k:]) for k in range(len(parts))
                        if "/".join(parts[k:]).startswith(TOPDIRS)), None)
            if rel:
                hits.append((i, rel))
    return hits, silenced


def main() -> int:
    err = _self_check()
    if err:
        print(f"✗ {err}")
        print("  本判据靠 .gitignore 区分『别人 clone 上本来就没有』与『路径指错了』——")
        print("  这条通路坏了就会满屏假红，所以先修它，别去看下面的结果。")
        return 1

    files = carriers()
    skipped_carriers = ignored([f.relative_to(ROOT).as_posix() for f in files])
    if skipped_carriers is None:
        print("✗ 判不出引用是不是运行时产物（git 不可用）—— 本判据**不许**在这种情况下报绿。")
        print("  它靠 `.gitignore` 区分『别人 clone 上本来就没有』与『路径指错了』。")
        return 1
    if skipped_carriers:
        files = [f for f in files
                 if f.relative_to(ROOT).as_posix() not in skipped_carriers]

    # path → [(载体, 行号), ...]：**同一路径被多处引用要全部报出来**。
    # ★ 第一版按路径去重，只报第一个载体 —— 于是 `tests/fixtures/dirty_inbox/README.md`　refcheck-ignore
    #   先被一份私人草稿报了，tracked 的 tests/test_guard.py 那条就被**吞掉**了。
    #   "先报到的赢"在判据里是 bug：它让修完第一处之后，剩下几处永远看不见。
    refs: dict[str, list[tuple[str, int]]] = {}
    n_refs = 0
    silenced = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relf = f.relative_to(ROOT).as_posix()
        hits, sil = extract(text)
        silenced += sil
        for ln, rel in hits:
            n_refs += 1
            if rel not in ALLOW and (ROOT / rel).exists():
                continue
            refs.setdefault(rel, []).append((relf, ln))

    absent = [p for p in refs if not (ROOT / p).exists()]
    ign = ignored(absent)
    if ign is None:
        print("✗ 判不出被引用的文件是不是运行时产物（git 不可用）—— 不许在这种情况下报绿。")
        return 1

    bad: list[str] = []
    notes: list[str] = []
    for rel, where in sorted(refs.items()):
        locs = "、".join(f"{c}:{ln}" for c, ln in where)
        if rel in ALLOW:
            notes.append(f"  {rel}（ALLOW：{ALLOW[rel]}）\n      引用于 {locs}")
        elif rel in ign:
            notes.append(f"  {rel}（被 .gitignore 忽略 ⇒ 别人 clone 上本来就没有，不算错）\n"
                         f"      引用于 {locs}")
        else:
            bad.append(f"  `{rel}` —— 这个文件**不存在**\n      引用于 {locs}")

    # ★ 下限断言：扫不到东西的检查器一定在报绿（本项目已记过两次）。
    if not files:
        bad.append("  载体文件一个都没扫到 —— 载体列表/glob 坏了，这是假绿。")
    if n_refs == 0:
        bad.append("  载体里一个仓库内路径都没解析到 —— 正则或载体列表坏了，这是假绿。")

    if notes:
        print("说明（不算错）：")
        print("\n".join(notes))
        print()

    if bad:
        print(f"✗ {len(bad)} 处路径指错了：")
        print("\n".join(bad))
        print()
        print("  ⇒ 要么把路径改成真身，要么那个文件真的丢了。")
        print("     「文档里指着的文件找不到」= 读的人照着去找、找不到、然后什么都不会发生。")
        return 1

    print(f"✓ 检查了 {len(files)} 个文件里的 {n_refs} 处仓库内路径引用（{len(refs)} 个路径需判定），"
          f"全部存在"
          + (f"；另有 {len(notes)} 个不是错（见上）" if notes else "")
          + (f"；{silenced} 行按 {IGNORE_MARK} 跳过" if silenced else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

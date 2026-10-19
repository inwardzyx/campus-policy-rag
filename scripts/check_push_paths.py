#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_push_paths.py —— 挡住「即将 push、但本不该进仓库」的文件。

## 为什么需要它（2026-10-11，一起真实事故）

提交 `7252a69`（2026-09-24 10:53）把 `CLAUDE.md` + 4 个 `cc_*.md` **推上了公开仓库**。
次日 16:14 主干被 `git filter-branch` 重写，这些文件不在当前树里了 ——
**但 GitHub 上那个提交仍然按 SHA 可达**，而且

    https://github.com/inwardzyx/campus-policy-rag/commit/7252a69.patch
    （★ 这是当时的 URL；仓库 2026-10-19 改名后旧地址仍有跳转）

一个普通 URL 就能把这 5 个文件的**完整正文**吐出来（实测 HTTP 200 / 20788 字节）。
链接入口就在公开的 Actions 页面上（run #1）。

**教训（一句话）：push 是单向门。**
  · `.gitignore` 只挡"以后"，对"历史上进过库的东西"一个字都不说；
  · 删除本地分支对远端**毫无帮助** —— 远端对象的存在性**只由「是否 push 过」决定**，
    此后一切本地操作与之无关（我在这件事上连错两次，都是没抓住这一条）。

## 它在整条防线里的位置

  P1（本脚本，**推前**）：拦"即将推上去的可疑路径"——这是主防线，防呆。
  P2（推后审计，见 scripts/hooks/README.md）：枚举公开面上印出来的 SHA，
     判断它是否是主干上的孤儿，是则取其 `files[]` 与同一张模式表求交。
  ⚠️ 两者不能互相替代：P1 挡不住"已经在远端的孤儿"，
     P2 覆盖不到"从未在任何公开面出现过的 SHA"（对人眼同样覆盖不到）。

## 用法

    # 由 .git/hooks/pre-push 调用：从 stdin 读 git 给的 `<local ref> <local sha> <remote ref> <remote sha>`
    <py> scripts/check_push_paths.py

    # 自检/测试：直接给路径，不走 git
    <py> scripts/check_push_paths.py --paths CLAUDE.md README.md
    <py> scripts/check_push_paths.py --paths README.md src.py     # 应当通过

退出码：0 = 可以推；1 = 拦下。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent

# ── 模式表：镜像 .gitignore 的【意图】，但只列"推上去后果严重"的那些 ──
#    为什么不直接读 .gitignore：那里面的条目有些是允许被跟踪的（比如 .cache/ 无所谓），
#    而这里要拦的是"泄露/误导"级别的。所以是一张**人工挑过的**表，每条带理由。
FORBIDDEN: list[tuple[str, str]] = [
    (r"^CLAUDE\.md$", "AI 协作配置（.gitignore 写明不进公开仓库）"),
    (r"^cc_[^/]*\.(md|log)$", "AI 协作讨论稿（含 AI 对作者的对话口吻）"),
    (r"^\.env$", "密钥文件"),
    (r"^\.env\.[^/]*$", "密钥文件"),
    (r"^\.tmp_extract/", "语料转换中间产物（源头是学院内部文件）"),
    (r"\.key$", "密钥文件"),
    (r"^\.demo_frames/", "录动图的中间帧（上百张，不进仓库）"),
    (r"^\.demo_chrome_profile/", "Chrome 临时 profile"),
]
# ── 显式放行：与 .gitignore 里 `!.env.example` 那条对应 ──
ALLOWED = [r"^\.env\.example$"]


def check(paths: list[str]) -> list[str]:
    """返回被拦下的 `路径 ← 理由` 列表（空 = 通过）。"""
    bad = []
    for p in paths:
        p = p.strip().replace("\\", "/")
        if not p:
            continue
        if any(re.search(a, p) for a in ALLOWED):
            continue
        for pat, why in FORBIDDEN:
            if re.search(pat, p):
                bad.append(f"{p}  ← {why}")
                break
    return bad


# ⚠️ 两条【本项目实测出来的】边界（2026-10-11）：
#   1. `--not --remotes` 在新分支时可能**少算**：它排除"任何 remote 上已有的提交"，
#      所以往**另一个** remote 推一整条新分支时，会漏掉 origin 已含的那部分。
#      本仓库只有一个 remote（origin），实际不受影响；多 remote 时要改成
#      `--not <该 remote 的 refs>`。★ 但真正危险的那类（推一个**谁都没有**的孤儿提交）
#      是拦得住的 —— 已用真实 push 验过。
#   2. 本 clone 是**浅克隆**（`.git/shallow` 定格在初始提交 109801e，2026-09-22），
#      所以本地任何"历史审计"只覆盖 09-22 之后。本案的 7252a69 是 09-24，在范围内。
def paths_being_pushed(stdin_text: str) -> list[str]:
    """按 git pre-push 的 stdin 协议，算出这次要推的提交涉及的**文件并集**。

    stdin 每行：`<local ref> <local sha> <remote ref> <remote sha>`
    · remote sha 全 0 = 新分支（远端还没有）⇒ 从 `--not --remotes` 取
    · local sha 全 0  = 删分支 ⇒ 跳过
    """
    ZERO = "0" * 40
    files: set[str] = set()
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _lref, lsha, _rref, rsha = parts[:4]
        if lsha == ZERO:
            continue
        if rsha == ZERO:
            cmd = ["git", "-C", str(REPO), "log", "--pretty=format:", "--name-only",
                   lsha, "--not", "--remotes"]
        else:
            cmd = ["git", "-C", str(REPO), "log", "--pretty=format:", "--name-only",
                   f"{rsha}..{lsha}"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60).stdout
        except Exception as e:                      # git 不可用 → 不静默放行
            print(f"✗ 算不出要推的文件（{type(e).__name__}: {str(e)[:80]}）—— 请人工确认")
            raise SystemExit(1)
        files.update(x.strip() for x in out.splitlines() if x.strip())
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", nargs="*", default=None,
                    help="自检用：直接给路径，不走 git")
    A = ap.parse_args()

    raw_stdin = ""
    if A.paths is not None:
        files = A.paths
        print(f"[push 路径检查] 直接检查 {len(files)} 条路径（自检模式）")
    else:
        raw_stdin = sys.stdin.read()
        files = paths_being_pushed(raw_stdin)
        print(f"[push 路径检查] 本次要推的提交涉及 {len(files)} 条路径")

    bad = check(files)
    # ★★ 0 条路径不许报绿（盲审 F7，2026-10-13 补）：
    #   这一条盯的是**判据自己瞎掉**的形态 —— 空 stdin（钩子协议变了、被谁重定向了、
    #   或者 `git rev-list` 返回了空）会让这个检查一条路径都不查，然后打一句"✔ 没有命中"。
    #   本项目已记过两次"扫不到东西的检查器一定在报绿"，而这是**最该挡的那道出口检查**。
    #   ⚠️ 但要区分两种"0 条"（第一版没区分，于是把合法的"删远端分支"判成红）：
    #     · `--paths` 自检模式给空列表 —— 调用方自己的事；
    #     · stdin 里**全是**删除（local sha 全 0）—— 删远端 ref 本来就没有文件要检查；
    #     · 其余情况下的 0 条 —— 协议/取数坏了，必须红。
    if not files and A.paths is None:
        def _is_delete(line: str) -> bool:
            parts = line.split()
            return len(parts) >= 2 and parts[1].startswith("0000")

        lines = [ln for ln in raw_stdin.splitlines() if ln.strip()]
        if lines and all(_is_delete(ln) for ln in lines):
            print("[push 路径检查] 本次只是在删远端 ref（local sha 全 0）⇒ 没有文件要检查")
            return 0
        print("[push 路径检查] ✗ 从 stdin 算出来的待推路径是 **0 条** —— 这不对：")
        print("  要么钩子的 stdin 协议变了（githooks(5)：ref 行走 stdin），")
        print("  要么 `git rev-list/diff-tree` 没取到东西。")
        print("  ⇒ 检查**没有覆盖任何文件**，所以不许报「没有命中禁推模式」。")
        print("  自测命令：printf 'refs/heads/main <sha> refs/heads/main <sha>\\n' | bash .git/hooks/pre-push origin url")
        return 1
    if not bad:
        print("[push 路径检查] ✔ 没有命中禁推模式")
        return 0

    print("\n★ 拦下 —— 以下路径不该被推上去：")
    for b in bad:
        print("  · " + b)
    print("""
★ 为什么这条比它看起来严重：
  · **push 是单向门**。`.gitignore` 只挡"以后"，对"历史上进过库的东西"一个字都不说。
  · 删除本地分支/改本地历史对远端**毫无帮助** —— 远端对象的存在性
    只由「是否 push 过」决定（本项目为此付过一次真实代价：提交 7252a69）。
  · 一旦推上去，即使之后重写历史，GitHub 仍按 SHA 提供 `.patch` 全文。

要绕过：`git push --no-verify`。
  但规矩是：**用了 --no-verify，就别在别处写"检查通过了"。**
  绕过检查不是漏洞，**假装检查过了**才是。
""")
    return 1


if __name__ == "__main__":
    sys.exit(main())

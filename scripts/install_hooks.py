#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""install_hooks.py —— 让「本地两道防线」在别人 clone 之后也装得上。

## 为什么需要它

`scripts/hooks/README.md:160-163` 自己写着：

    本仓库的本地钩子（pre-commit / pre-push）**是唯一真正会挡的两道**，
    而它们**不进仓库**（`.git/hooks/` 被 gitignore），换 clone 就没了
    —— 别人（或未来的你）clone 下来默认一道都没有。
    这才是这道防线真正的薄弱点，比远端那条更值得补。

git **没有**「clone 之后自动执行」的钩子（这是设计，不是缺陷；否则 clone 一个
别人的仓库就等于执行它的代码）。所以能做到的最好是两件事：
    ① 装起来只要**一条命令**；
    ② 装在**别人一定会看到的地方**（README 的「30 秒跑起来」+ 跑 pytest 时的提醒）。

## 为什么用 core.hooksPath，而不是拷到 .git/hooks/

老办法是 `cp scripts/hooks/pre-commit .git/hooks/`。它有两个问题：

1. **副本会过期**：改了 `scripts/hooks/` 里那份，`.git/hooks/` 里那份还是旧的。
   这不是理论 —— `scripts/hooks/README.md:31-36` 记着一次真实复发：
   「修好提交之后 `scripts/hooks/pre-commit` 又被覆盖回旧版，
     差点让"检查通过"变成一句假话」。
   ⇒ 用 `core.hooksPath` 之后**没有第二份副本**，"副本过期"这一整类问题不存在。
2. 得拷两次 + `chmod +x`，而 `chmod` 在 Windows 上是空操作（钩子靠 shebang 跑）。

## 用法

    python scripts/install_hooks.py            # 装（幂等，重复跑没事）
    python scripts/install_hooks.py --check    # 只查装没装：装了 exit 0，没装 exit 1
    python scripts/install_hooks.py --quiet    # 少说话（给 conftest 用）

★ 本脚本自己**要能证明生效**，不能只打印一句 ✔：
  装完它会真的执行一次 `git hook run pre-commit`，并要求输出里出现
  钩子自己的横幅 `[pre-commit]` —— 那才说明 git 找到并执行了**我们那份**。
  （只看配置文件写没写进去是不够的：路径写错、文件缺失、shebang 坏掉，
    都会让"配好了"变成一句空话。）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ★ stdout 显式转 UTF-8：本脚本会打 ✓ / ⚠ 这类字符，
#   而 stdout 被重定向或捕获时（Windows 上是 GBK）它们编不出来，
#   会崩在打印上 —— 看起来像"检查失败"，其实是"打印失败"。
#   （同款病在本仓库已经出现过三次，见 check_readme_numbers.py 那段注释。）
#
# ★★ 但【不能在 import 时做】——`tests/conftest.py` 要复用本模块的判断函数，
#    而 pytest 导入 conftest 时 `sys.stdout` 是它自己的捕获对象，
#    根本没有 `reconfigure` 方法 ⇒ AttributeError 会让【所有测试】在收集阶段就崩。
#    ⇒ 所以只把它放进 main()，让 import 本模块没有任何副作用。
def _force_utf8_stdout() -> None:
    """把 stdout 切到 UTF-8；做不到就算了（pytest 的捕获层本来就是 UTF-8）。"""
    reconf = getattr(sys.stdout, "reconfigure", None)
    if reconf is None:
        return                      # 不是普通 TextIOWrapper（如 pytest 的捕获对象）
    try:
        reconf(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        # 流已经写过东西就不能改编码了。这是"改不了编码"，不是"检查失败"，
        # 但也别静默 —— 打一行说明（此时用默认编码，可能打不出 ✓，所以用纯 ASCII）。
        print("note: stdout encoding could not be switched to utf-8; "
              "output may be escaped")


# 期望的配置值。用【相对路径】：git 会相对仓库工作树解析，
# 已实测从仓库根和从子目录里跑 git 都能找到（2026-10-11）。
EXPECTED_HOOKS_PATH = "scripts/hooks"
HOOK_NAMES = ("pre-commit", "pre-push")


def _git(*args: str, cwd: str | None = None) -> tuple[int, str]:
    """跑一条 git 命令，返回 (退出码, 输出)。git 不可用时不静默放行。"""
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def repo_root() -> Path:
    code, out = _git("rev-parse", "--show-toplevel")
    if code != 0 or not out:
        print(f"✗ 这里不是 git 仓库（git rev-parse 返回 {code}：{out[:120]}）")
        raise SystemExit(2)
    return Path(out)


def configured_value(root: Path) -> str | None:
    """读【本地】配置里的 core.hooksPath；没设返回 None。"""
    code, out = _git("config", "--local", "--get", "core.hooksPath", cwd=str(root))
    return out if code == 0 and out else None


def hook_file_problems(root: Path) -> list[str]:
    """仓库里那两个钩子文件本身的体检 —— 有问题就说出来，不假装没事。"""
    problems: list[str] = []
    for name in HOOK_NAMES:
        p = root / EXPECTED_HOOKS_PATH / name
        if not p.exists():
            problems.append(f"{EXPECTED_HOOKS_PATH}/{name} 不存在 —— 这条防线是空的")
            continue
        raw = p.read_bytes()
        if not raw.strip():
            problems.append(f"{EXPECTED_HOOKS_PATH}/{name} 是空文件")
        if b"\r\n" in raw:
            # ★ CRLF 会让 shebang 变成 "#!/usr/bin/env bash\r"，
            #   Git Bash 报 bad interpreter —— 而且报错指不到原因。
            #   （.gitattributes 已把这两类钉成 LF，这里是"钉住了没"的现算检查。）
            problems.append(f"{EXPECTED_HOOKS_PATH}/{name} 是 CRLF 行尾 —— "
                            f"shebang 会变成 '#!/usr/bin/env bash\\r'，Git Bash 跑不起来")
        if not raw.startswith(b"#!"):
            problems.append(f"{EXPECTED_HOOKS_PATH}/{name} 第一行不是 #!（没有 shebang）")
    return problems


def installed(root: Path) -> tuple[bool, str]:
    """装没装。返回 (是否已装, 一句人话说明为什么)。"""
    val = configured_value(root)
    if val is None:
        return False, "core.hooksPath 没设 —— git 用的是 .git/hooks/（不进仓库，clone 后是空的）"
    if val.replace("\\", "/").strip("/") != EXPECTED_HOOKS_PATH:
        return False, f"core.hooksPath 指向 {val!r}，不是 {EXPECTED_HOOKS_PATH!r}"
    probs = hook_file_problems(root)
    if probs:
        return False, "配置对，但钩子文件本身有问题：" + "；".join(probs)
    return True, f"core.hooksPath = {EXPECTED_HOOKS_PATH}，两个钩子文件都在且格式正确"


def live_proof(root: Path) -> tuple[bool, str]:
    """真跑一次 pre-commit，看它自己的横幅出不出来。

    为什么要这道：配置写对了不等于 git 会执行它（路径解析、shebang、解释器
    都可能让"配好了"变成空话）。要求出现钩子自己打印的 `[pre-commit]`，
    那才证明**我们那份**真的被 git 找到并执行了。
    """
    code, out = _git("hook", "run", "pre-commit", cwd=str(root))
    if "[pre-commit]" not in out:
        return False, (f"执行了 `git hook run pre-commit`（退出码 {code}），"
                       f"但输出里没有钩子自己的横幅 `[pre-commit]` —— "
                       f"说明 git 没有执行我们那份。原始输出：{out[:300]!r}")
    return True, (f"实跑了一次 pre-commit：出现了钩子横幅 `[pre-commit]`"
                  f"（本次退出码 {code}；非 0 表示钩子里的检查本身没过，"
                  f"那是检查结果，不是没装上）")


def main() -> int:
    ap = argparse.ArgumentParser(description="把本地两道钩子装上（core.hooksPath）")
    ap.add_argument("--check", action="store_true", help="只查，不装：装了 exit 0，没装 exit 1")
    ap.add_argument("--quiet", action="store_true", help="少说话")
    ap.add_argument("--no-live", action="store_true",
                    help="跳过「真跑一次」的现场证明（默认会跑；CI 里想快可以用）")
    args = ap.parse_args()

    _force_utf8_stdout()
    root = repo_root()
    say = (lambda *a: None) if args.quiet else print

    ok, why = installed(root)
    if args.check:
        if ok:
            say(f"✓ 本地钩子已装：{why}")
            return 0
        say(f"✗ 本地钩子【没装】：{why}")
        say("  装它：python scripts/install_hooks.py")
        return 1

    if ok:
        say(f"✓ 已经装过了：{why}")
    else:
        say(f"… 现在没装：{why}")
        # 先体检文件，别把一条注定跑不起来的配置写进去
        probs = hook_file_problems(root)
        if probs:
            print("✗ 先修钩子文件本身，否则装上也白装：")
            for p in probs:
                print("    · " + p)
            return 2
        code, out = _git("config", "--local", "core.hooksPath", EXPECTED_HOOKS_PATH,
                         cwd=str(root))
        if code != 0:
            print(f"✗ 写配置失败（git config 退出码 {code}）：{out[:200]}")
            return 2
        ok2, why2 = installed(root)
        if not ok2:
            print(f"✗ 写完配置后复查不通过：{why2} —— 不假装成功")
            return 2
        say(f"✓ 已装上：{why2}")

    # ★ 现场证明：不能说"配好了"就完事
    if args.no_live:
        say("⚠ 跳过了「真跑一次」的现场证明（--no-live）—— 上面只证明配置写对了，"
            "没证明 git 真的会执行它")
        return 0
    ok3, why3 = live_proof(root)
    say(("✓ " if ok3 else "✗ ") + why3)
    if not ok3:
        return 3

    say("")
    say("现在起：`git commit` 会跑 pre-commit，`git push` 会跑 pre-push。")
    say("★ 这两道是【唯一真正会挡】的防线（远端 CI 只会喊、不拦合并，"
        "见 scripts/hooks/README.md 的「远端那道防线」一节）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

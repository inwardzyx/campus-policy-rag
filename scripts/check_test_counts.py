# -*- coding: utf-8 -*-
"""README 声明的「每个 CI job 跑哪些测试文件」必须与 `ci.yml` 真实一致 —— 现解析，不许手抄。

## 为什么要有这个脚本（2026-10-14 立，同日被跨模型盲审打回重做）

第一版写在一份**过期的数字**上：README 曾写「前两个 job 加起来覆盖全部 57 条（22 + 35）」，
实测已收集 145 条（loader 22 + limits 56 + guard 50 + eval_set 5 + llm_resilience 12），
而且和同一份 README 里「60 passed」那句自相矛盾。**过期条数比没有条数更坏：它长得像实测。**

★★ 但第一版有一个更严重的错，是 **GLM-5.3-Flash 盲审**抓出来的（本地全量 venv 里根本看不出来）：

    第一版去跑 `pytest tests --collect-only`（要 import 全部测试模块），
    而 `test_guard / test_eval_set / test_llm_resilience` **顶层 `import service`**，
    `service.py` 顶层又要 numpy / jieba / fastapi / fastembed / langchain-deepseek。
    CI 的**快 job 只装 `pytest + redis`** ⇒ 收集阶段必然 ImportError ⇒ 快 job 红。
    ⇒ 我把「判据纪律」这一步亲手弄成了红灯，而**本地永远复现不出来**。

⇒ 这一版彻底不碰 pytest，只做**静态声明比对**：
    ① 从 `ci.yml` **现解析**出每个 job 实际跑了哪些 `tests/test_*.py`（不再手抄一份 JOBS！）；
    ② 从 README 表格里抠出它声称每个 job 跑哪些文件；
    ③ **磁盘上所有 `tests/test_*.py` 必须被至少一个 job 覆盖**
       （这条才是 README 那句「不存在只在本地跑过的测试」的实质）；
    ④ 反向：声明的文件必须真实存在（防"声明一个不存在的文件凑数"）。

★ 为什么"不手抄 JOBS"是关键：第一版的 `JOBS` 是 `ci.yml` 的**手抄副本**，
  而检查器从不读 `ci.yml` ⇒ 从某个 job 删掉一个测试文件、两边都不动，
  就会"三方一致、检查器绿、README 说假话"。
  这违反了本仓自己写在 `tradeoffs.md` 的规矩：**声称值一律从被检查对象里解析，不许硬编码常量**。

★ 附带一个副产品（不是判据，只打印）：跑一次 `pytest --collect-only` 报出真实条数，
  好让"过去那种 57 条"当场现形。**它挂了不影响结论** —— 因为本检查判的是**文件集合**，
  不是条数（条数会随加用例自然增长，本来就该由 pytest 输出说话，不该冻进文档）。

用法：
    <venv>/Scripts/python.exe scripts/check_test_counts.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ★ print 里的 ✓/✗ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
TESTS_DIR = ROOT / "tests"

TEST_FILE_RE = re.compile(r"tests/(test_[A-Za-z0-9_]+\.py)")


def ci_jobs() -> dict[str, list[str]]:
    """从 `ci.yml` **现解析**：每个 job 实际跑了哪些测试文件。

    ★ 为什么要走 YAML 解析而不是正则扫全文：
      全文扫会把**注释里提到**的测试文件也算成"这个 job 跑了它"，
      而 `ci.yml` 的注释里恰好写满了文件名（那些注释是在解释"为什么"）。
      ⇒ 只在 `jobs.<id>.steps[].run` 里找 `pytest`，才等于"真的会执行"。
    """
    import yaml                                   # PyYAML 在 requirements.txt 里（有实测确认）

    doc = yaml.safe_load(CI.read_text(encoding="utf-8"))
    jobs: dict[str, list[str]] = {}
    for job_id, job in (doc.get("jobs") or {}).items():
        found: list[str] = []
        for step in (job.get("steps") or []):
            run = step.get("run")
            if not run:
                continue
            # ★ 逐行看：一行里同时出现 pytest 与测试文件名，才算"这一行真的在跑它"
            for line in run.splitlines():
                if "pytest" not in line:
                    continue
                found += [f"tests/{n}" for n in TEST_FILE_RE.findall(line)]
        jobs[job_id] = sorted(set(found))
    return jobs


def readme_claims() -> dict[str, list[str]]:
    """从 README 那张表里抠出它声称每个 job 跑哪些测试文件。

    ★ 返回 **list 不是 set**：重复写要能被发现（第一版用 set，变异②"多写凑数"当场漏检）。
    ★ 只认**表格行**（`| **fast** | …`）—— 否则会把 README 顶部那段 `pytest tests/` 示例
      也算进来（那段没有 job 名，会被忽略），以及把正文里的其它提及卷进来。
    """
    text = README.read_text(encoding="utf-8")
    claims: dict[str, list[str]] = {}
    for m in re.finditer(r"^\|\s*\*\*([A-Za-z0-9_]+)\*\*\s*\|(.*)$", text, re.M):
        claims[m.group(1)] = [f"tests/{n}" for n in TEST_FILE_RE.findall(m.group(2))]
    return claims


def on_disk() -> list[str]:
    """仓库里真实存在的测试文件（相对路径、正斜杠）。"""
    return sorted(f"tests/{p.name}" for p in TESTS_DIR.glob("test_*.py"))


def collected_total() -> int | None:
    """副产品：跑一次 collect 报出真实条数。**失败就返回 None，不影响判据。**"""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(TESTS_DIR), "--collect-only", "-q"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT), timeout=180,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except Exception:                                       # noqa: BLE001
        return None
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+)\s+tests? collected", out)
    return int(m.group(1)) if m else None


def main() -> int:
    if not CI.exists():
        print(f"✗ 找不到 {CI.relative_to(ROOT)} —— 没有真相源可比，拒绝报绿")
        return 1
    jobs = ci_jobs()
    if not jobs:
        print("✗ 从 ci.yml 里解析出 0 个 job —— 解析器坏了，这是空转的红不是通过")
        return 1
    covered: dict[str, set[str]] = {k: set(v) for k, v in jobs.items()}
    if not any(covered.values()):
        print("✗ ci.yml 里一个测试文件都没解析到 —— "
              "要么 CI 真的不跑测试，要么本解析器没跟上（★ 别直接删这条，先看 ci.yml）")
        return 1

    claims = readme_claims()
    disk = set(on_disk())
    bad: list[str] = []

    # ---- ① README 每个 job 的声明 vs ci.yml ----
    for job, files in sorted(jobs.items()):
        want = files
        got = claims.get(job)
        if got is None:
            bad.append(f"  ci.yml 里的 job「{job}」在 README 表格里找不到对应行 —— "
                       f"（README 那张表的 job 名要和 ci.yml 的 job id 对得上）")
            continue
        from collections import Counter
        cw, cg = Counter(want), Counter(got)
        missing, extra = cw - cg, cg - cw
        if missing or extra:
            bad.append(f"  job「{job}」：README 少写了 {sorted(missing.elements()) or '无'}，"
                       f"多写了 {sorted(extra.elements()) or '无'}（ci.yml 实际：{want}）")

    # ---- ② README 里出现、但 ci.yml 里不存在的 job ----
    for job in sorted(set(claims) - set(jobs)):
        if claims[job]:
            bad.append(f"  README 表格里的 job「{job}」在 ci.yml 里不存在 —— "
                       f"要么它被删了，要么名字改了")

    # ---- ③ 磁盘上的测试文件必须被至少一个 job 覆盖 ----
    union = set().union(*covered.values()) if covered else set()
    uncovered = disk - union
    if uncovered:
        bad.append(f"  ★ 这些测试文件不在任何 CI job 里：{sorted(uncovered)} ⇒ "
                   f"README 那句「不存在只在本地跑过的测试」就是假话")

    # ---- ④ 反向：声明的文件必须真实存在（防"声明一个不存在的文件凑数"）----
    phantom = sorted(union - disk)
    if phantom:
        bad.append(f"  这些文件被 CI/README 声明了，但磁盘上不存在：{phantom} —— "
                   f"要么写错了名字，要么文件被删了而声明没跟上")

    # ---- ⑤ 弱护栏：声明了却没有测试函数的文件（正则粗查，只提示不拦）----
    emptyish = []
    for f in sorted(union & disk):
        txt = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        if not re.search(r"^\s*def\s+test_", txt, re.M):
            emptyish.append(f)

    total = collected_total()

    # ---- 打印 ----
    print(f"从 ci.yml 现解析（真相源）：{CI.relative_to(ROOT)}")
    for job in sorted(jobs):
        print(f"  {job:10s} {', '.join(jobs[job]) or '（不跑测试文件）'}")
    print(f"  磁盘上的测试文件：{len(disk)} 个")
    print(f"  被 CI 覆盖：{len(union & disk)} 个"
          f"{'　★ 未覆盖 ' + str(sorted(uncovered)) if uncovered else '（全覆盖）'}")
    if total is None:
        print("  真实条数：未算出（collect 失败或超时）—— ★ 不影响本判据，本判据比的是文件集合")
    else:
        print(f"  真实条数：{total}（现算；文档里**不要**写死这个数，它会随加用例自然增长）")
    if emptyish:
        print(f"  ⚠ 这些文件里没搜到 `def test_`（只提示，不拦）：{emptyish}")

    if bad:
        print(f"\n✗ {len(bad)} 处对不上：")
        print("\n".join(bad))
        return 1
    print("✓ README 的 job→测试文件声明与 ci.yml 一致；前两个 job 覆盖全部测试文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())

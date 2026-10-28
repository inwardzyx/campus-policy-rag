# -*- coding: utf-8 -*-
r"""rollback.py —— 回滚的**唯一入口**：先看清楚，再决定退。

## 为什么要有这个脚本

回滚的敌人不是"git 不好用"，是**回滚太慢**。一个要 30 分钟才能回滚的系统，
团队会变得极度保守 —— 没人敢动 prompt。所以这个脚本只干一件事：
**把"退到哪、退什么、退完怎么验"在 1 秒内算出来**，让回滚变成一个 1 分钟的动作。

## 四个动作

```powershell
# ① 我跑过哪几次？（带指纹，一眼看出哪两次其实是同一个系统）
python scripts/rollback.py --list

# ② 那次跑的到底是啥状态
python scripts/rollback.py --show 20261028-101500-a1b2c3d4

# ③ 现在这个系统，还是上次跑评测时的那个吗？（回滚后的验收判据）
python scripts/rollback.py --verify

# ④ 退到某次跑之前的状态：生成计划 + rollback.ps1（**不自动执行**）
python scripts/rollback.py --to 20261028-101500-a1b2c3d4
```

## 为什么"生成脚本"而不是"直接执行"

要退的东西有三类，风险完全不同：

| 要退的 | 怎么退 | 能不能自动 |
|---|---|---|
| 代码 commit | `git checkout <sha>` | ⚠️ 会丢掉当前工作区 |
| 未提交改动 | `git apply worktree.diff` | ⚠️ 冲突就半截 |
| .env / 环境变量 | 改写 .env 或设环境变量 | ⚠️ 里面可能有真 key |

**三类都会改动你正在用的东西**，所以这里只生成、不执行 —— 生成物是一个可以逐行看的
`rollback.ps1`，你确认后再自己跑。这比"一个按钮按下去然后祈祷"更符合本项目的风格：
**判据要能被人复核**。

## 退出码
`0` 正常；`1` 参数/文件问题（找不到 run、状态缺失）；`2` `--verify` 判定"不是一个系统"。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb import runstate                              # noqa: E402


# ==================================================================
# 打印
# ==================================================================
def _print_list() -> int:
    runs = runstate.list_runs(ROOT)
    if not runs:
        print("还没有任何 run。跑一次评测就会自动生成：")
        print("  python scripts/run_scenario_eval.py --cases hr --namespace hr_eval ...")
        return 0
    print(f"{'run_id':30s} {'指纹':14s} {'commit':9s} {'脏':2s} {'报告':4s} 说明")
    print("-" * 92)
    for r in runs:
        print(f"{r['run_id']:30s} {r['fingerprint']:14s} {r['sha']:9s} "
              f"{'是' if r['dirty'] else '否':2s} {'有' if r['has_report'] else '无':4s} {r['note']}")
    print()
    print("★ 指纹相同的两次 = **同一个系统**，它们的指标差异只可能来自样本/随机性。")
    return 0


def _print_show(run_id: str) -> int:
    try:
        s = runstate.load(ROOT, run_id)
    except FileNotFoundError:
        print(f"✗ 找不到 run：{run_id}")
        return 1
    g, rt = s.get("git") or {}, s.get("runtime") or {}
    print(f"run      : {run_id}")
    print(f"时间     : {s.get('created_at')}   说明: {s.get('note') or '-'}")
    print(f"指纹     : {s.get('fingerprint')}")
    print(f"代码     : {g.get('sha','?')[:12]}（{g.get('branch','?')}）"
          f"{'  ⚠ 未提交改动 ' + str(g.get('dirty_file_count')) + ' 个文件' if g.get('dirty') else '  clean'}")
    print(f"Python   : {rt.get('python')}  ({rt.get('executable')})")
    print(f"语料目录 : {s.get('corpus_docs_dir') or '-'}")
    print("\n配置（只列与结果相关的）：")
    env = s.get("env") or {}
    if not env:
        print("  （无）")
    for k, v in env.items():
        print(f"  {k:26s} = {v.get('value', '<secret:' + v.get('sha8', '') + '>')}"
              f"   [{v.get('source')}]")
    ns = (s.get("corpus") or {}).get("namespaces") or {}
    print("\n语料：")
    if not ns:
        print("  （无）")
    for name, c in ns.items():
        print(f"  {name:20s} {c.get('docs', 0)} 篇  hash={c.get('hash', '-')}"
              f"  受限 {c.get('with_acl', 0)} 篇")

    docs = (s.get("docs") or {}).get("dirs") or {}
    if docs:
        print("\n语料目录（不经 kb_data 的那条路径）：")
        for d, v in docs.items():
            print(f"  {d:20s} {v.get('files', 0)} 个文件  hash={v.get('hash') or '-'}")

    # ★ 未跟踪文件（`git diff` 看不见的那一半）——新写的模块就在这里面
    g2 = s.get("git") or {}
    if g2.get("untracked_count"):
        print(f"\n未跟踪文件 {g2['untracked_count']} 个（内容哈希已计入指纹）：")
        for f in (g2.get("untracked") or [])[:8]:
            print(f"  {f.get('path','?'):44s} {str(f.get('sha256',''))[:12]}  {f.get('bytes','?')}B")
    rp = runstate.runs_root(ROOT) / run_id / "report.json"
    if rp.exists():
        rep = json.loads(rp.read_text(encoding="utf-8"))
        agg = rep.get("aggregate") or {}
        print(f"\n报告：{rep.get('suite')} n={rep.get('n_cases')}")
        for key in ("answer", "refuse", "deny"):
            v = agg.get(key)
            if isinstance(v, dict) and v.get("total"):
                print(f"  {key}: {v['passed']}/{v['total']} = {v['rate']:.1%}")
    return 0


def _print_verify(against: str | None = None) -> int:
    """当前系统 vs 某次 run：是不是同一个。**这是回滚后的验收动作。**

    ★ 默认比最近一次；`--against <run_id>` 可以指定"我要退回到的那一次"——
      回滚验收必须比**目标**，比最近一次是没有意义的（最近一次可能正是你要退掉的那次）。
    """
    runs = runstate.list_runs(ROOT)
    if not runs:
        print("✗ 没有历史 run，无从比对。")
        return 1
    last = next((r for r in runs if r["run_id"] == against), runs[0]) if against else runs[0]
    if against and last["run_id"] != against:
        print(f"✗ 找不到 run：{against}")
        return 1
    try:
        target = runstate.load(ROOT, last["run_id"])
    except FileNotFoundError:
        print(f"✗ 状态文件丢了：{last['run_id']}")
        return 1

    # ★ 用**记录下来的** namespace / docs_dir 重拍，否则语料那块永远对不上
    ns = next(iter(((target.get("corpus") or {}).get("namespaces") or {})), "")
    now = runstate.capture(ROOT, namespace=ns, note="verify",
                          docs_dir=target.get("corpus_docs_dir") or "")
    deltas = runstate.diff_states(target, now)

    print(f"基线 run : {last['run_id']}（{last['created_at']}）")
    print(f"基线指纹 : {target.get('fingerprint')}")
    print(f"当前指纹 : {now['fingerprint']}")
    if not deltas:
        print("\n✓ 同一个系统 —— 两次跑的指标可以直接比较。")
        return 0
    print(f"\n✗ 不是一个系统（{len(deltas)} 处差异）：")
    for d in deltas:
        print(f"  - {d}")
    print("\n⇒ 此时拿新数字和旧报告比，差异不一定是改动带来的。先回滚或重新拍基线。")
    return 2


def _print_diff(a_id: str, b_id: str) -> int:
    try:
        a, b = runstate.load(ROOT, a_id), runstate.load(ROOT, b_id)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1

    print(f"A = {a_id}（指纹 {a.get('fingerprint')}）")
    print(f"B = {b_id}（指纹 {b.get('fingerprint')}）")
    deltas = runstate.diff_states(a, b)
    if not deltas:
        print("\n状态：完全相同（同一个系统）。")
    else:
        print(f"\n状态差异（{len(deltas)} 处）：")
        for d in deltas:
            print(f"  - {d}")

    ra, rb = runstate.load_report(ROOT, a_id), runstate.load_report(ROOT, b_id)
    if not (ra and rb):
        print("\n（有一侧没有 report.json，跳过逐题对比）")
        return 0
    cmp = runstate.compare_reports(ra, rb)
    print(f"\n逐题对比（共同 {cmp['n_common']} 题）：")
    print(f"  修好 {len(cmp['fixed'])} 题：{', '.join(cmp['fixed'][:12]) or '-'}")
    print(f"  弄坏 {len(cmp['broke'])} 题：{', '.join(cmp['broke'][:12]) or '-'}")
    print(f"  不变 {cmp['unchanged']} 题")
    if cmp["only_a"] or cmp["only_b"]:
        print(f"  ⚠ 题目集合不同：仅 A {cmp['only_a']} / 仅 B {cmp['only_b']}"
              f"（这类差异不能当成系统变化）")
    if cmp["broke"]:
        print("\n★ 有回归：总数可能仍是 +N，但这几题是真被弄坏的 —— 必须逐题看原因。")
    return 0


# ==================================================================
# 回滚计划
# ==================================================================
def _plan_rollback(run_id: str) -> tuple[list[str], list[str], dict]:
    """返回 (给人看的计划, PowerShell 脚本行, 原始状态)。"""
    target = runstate.load(ROOT, run_id)
    g = target.get("git") or {}
    ns = next(iter(((target.get("corpus") or {}).get("namespaces") or {})), "")
    now = runstate.capture(ROOT, namespace=ns, note="plan",
                          docs_dir=target.get("corpus_docs_dir") or "")
    deltas = runstate.diff_states(now, target)

    plan: list[str] = []
    ps: list[str] = [
        "# 由 scripts/rollback.py 生成 —— 逐行确认后再执行。",
        f"# 目标状态：{run_id}（指纹 {target.get('fingerprint')}）",
        f"# 生成时间：{now.get('created_at')}",
        "Set-Location -LiteralPath '" + str(ROOT) + "'",
        "",
    ]

    if not deltas:
        plan.append("✓ 当前已经是目标状态，不需要回滚。")
        ps.append('Write-Host "当前已是目标状态，无需回滚"')
        return plan, ps, target

    # ---- ① 代码 ----
    cur_sha = (now.get("git") or {}).get("sha") or ""
    if cur_sha != (g.get("sha") or ""):
        plan.append(f"① 代码：{cur_sha[:8]} → {g.get('sha','?')[:8]}")
        plan.append("   先存下当前未提交改动（否则 git checkout 会丢）：")
        plan.append("     git stash push -u -m \"pre-rollback\"")
        plan.append(f"   再切回去： git checkout {g.get('sha','?')[:12]}")
        ps += [
            '# ① 代码：先备份当前改动，再切到目标 commit',
            'git stash push -u -m "pre-rollback"',
            f'git checkout {g.get("sha", "")[:12]}',
            "",
        ]

    # ---- ② 未提交改动（目标那次的 diff）----
    diff_file = runstate.runs_root(ROOT) / run_id / "worktree.diff"
    if (now.get("git") or {}).get("diff_sha256") != (g.get("diff_sha256") or ""):
        if diff_file.exists():
            plan.append(f"② 未提交改动：目标那次带 {g.get('diff_bytes',0)}B diff，已存在 run 目录里")
            plan.append(f"   重放： git apply --3way \"{diff_file}\"")
            plan.append("   ⚠ 冲突时别硬来：先看 `git status`，改完再 apply")
            ps += [
                '# ② 重放目标那次的未提交改动（冲突就直接停下来看）',
                f'git apply --3way -v "{diff_file.as_posix()}"',
                "",
            ]
        else:
            plan.append(f"② 未提交改动：目标那次有 diff（{g.get('diff_bytes',0)}B），"
                        f"但 worktree.diff 没存下来 ⇒ **这一段无法还原**，只能看 state.json 的哈希。")
            ps += ["# ② 目标那次的 worktree.diff 缺失，无法重放", ""]

    # ---- ③ 配置 ----
    oe = target.get("env") or {}
    ne = now.get("env") or {}
    changed = {k: (ne.get(k), oe.get(k)) for k in set(oe) | set(ne)
               if oe.get(k) != ne.get(k)}
    if changed:
        plan.append(f"③ 配置：{len(changed)} 个键要改回")
        ps.append("# ③ 配置：改回目标那次的取值（含 .env 与进程环境两处）")
        for k, (cur, want) in sorted(changed.items()):
            if "value" in (want or {}):
                want_v = want["value"]
                plan.append(f"   {k}: {cur.get('value') if cur else '未设'} → {want_v}")
                # 只回写 .env（持久、可复原）；不碰系统级环境变量
                ps.append(f"#    {k}: {cur.get('value') if cur else '(未设)'} -> {want_v}")
                ps.append(f"$env:{k} = '{want_v}'")
            else:
                plan.append(f"   {k}: 密钥类，指纹 {want.get('sha8','')}（值不落盘，需人工确认）")
                ps.append(f"#    {k}: 密钥类，值未记录 —— 需人工确认（指纹 {want.get('sha8','')}）")
        ps.append("# 上面 $env: 只对当前窗口生效；要持久化请同步改 .env，否则下次启动又漂回去")
        ps.append("")

    # ---- ④ 语料 ----
    cn = ((now.get("corpus") or {}).get("namespaces") or {}).get(ns) or {}
    ct = ((target.get("corpus") or {}).get("namespaces") or {}).get(ns) or {}
    if cn != ct:
        plan.append(f"④ 语料 {ns}：{cn.get('docs',0)} 篇/{cn.get('hash','-')} → "
                    f"{ct.get('docs',0)} 篇/{ct.get('hash','-')}")
        docs_dir = target.get("corpus_docs_dir") or "docs/kb"
        plan.append(f"   重灌：删 kb_data/{ns} 后重建（源目录 {docs_dir}）")
        plan.append(f"     Remove-Item -Recurse -Force kb_data/{ns}")
        plan.append(f"     再跑一次带 --bootstrap-docs {docs_dir} --namespace {ns} 的评测")
        ps += [
            f'# ④ 语料不一致：清掉命名空间后重灌（源目录 {docs_dir}）',
            f'# Remove-Item -Recurse -Force "kb_data/{ns}"',
            "",
        ]

    plan.append("")
    plan.append("退完验收：python scripts/rollback.py --verify --against "
                f"{run_id}  →  必须输出「同一个系统」")
    # ★ 用 sys.executable 而不是裸 `python`：这台机器上 PATH 里排前面的是 conda base
    #   和 Python 3.8，裸敲命中的不是项目 venv（本项目已记过这条坑）。
    exe = sys.executable or "python"
    ps += [
        "# 验收：指纹必须与目标一致（用项目 venv 的完整路径，裸 python 会命中别的解释器）",
        f'& "{exe}" scripts/rollback.py --verify --against {run_id}',
    ]
    return plan, ps, target


def _do_plan(run_id: str) -> int:
    try:
        plan, ps, _ = _plan_rollback(run_id)
    except FileNotFoundError:
        print(f"✗ 找不到 run：{run_id}")
        return 1

    print(f"回滚计划（目标 {run_id}）：\n")
    for line in plan:
        print(line)          # 计划里的缩进由生成处负责，这里不再二次缩进

    run_dir = runstate.runs_root(ROOT) / run_id
    out = run_dir / "rollback.ps1"
    out.write_text("\n".join(ps) + "\n", encoding="utf-8")
    print(f"\n✓ 已生成脚本：{out}")
    print("  逐行确认后执行；完成后跑 --verify 验收。")
    return 0


# ==================================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="评测 run 的状态查看 / 对比 / 回滚计划")
    ap.add_argument("--list", action="store_true", help="列出所有 run（含指纹）")
    ap.add_argument("--show", metavar="RUN_ID", help="看某次 run 的完整状态")
    ap.add_argument("--to", metavar="RUN_ID", help="生成回滚到该 run 的计划与脚本")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"), help="对比两次 run（状态 + 逐题）")
    ap.add_argument("--verify", action="store_true", help="当前系统 vs 某次 run 是否一致")
    ap.add_argument("--against", metavar="RUN_ID", default=None,
                    help="配合 --verify：指定要比的基线（默认最近一次）")
    args = ap.parse_args(argv)

    actions = [bool(args.list), bool(args.show), bool(args.to), bool(args.diff), args.verify]
    if sum(1 for a in actions if a) != 1:
        ap.print_help()
        return 1

    if args.list:
        return _print_list()
    if args.show:
        return _print_show(args.show)
    if args.diff:
        return _print_diff(args.diff[0], args.diff[1])
    if args.verify:
        return _print_verify(args.against)
    return _do_plan(args.to)


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""证明"给直打侧补usage 计量"这个改动【没有改变模型行为】。

★ 为什么需要它（2026-10-12）：
  `service.py` 加 usage 之后，我上一轮说过"改完必须重跑一遍拒答回归"。
  但那句话把两件事混在一起了：
    - token 计量：这是个【读取】改动，测的是"拿到 usage 没有"
    - 硬答率：理论上【完全不该变】（模型输入一字未改）
  ⇒ 用"跑 3 遍看硬答率还在不在 10.5%~15.8% 区间"来证明"没变"，
    是**拿 5 个百分点的底噪当信号** —— 那个区间本来就能覆盖随机波动。

  正确做法是本脚本：改前/改后逐字比对答案文本。
  文本逐字相同 ⇒ 模型输入必然一字未改 ⇒ 硬答率必然相同 ⇒ 回归不用重跑。

★ 所以本脚本替代的是"重跑 3 遍回归"，不是补充它。

⚠️⚠️⚠️ 判据为什么【不能是逐字比对】（2026-10-12 实测连踩四次）：
  这个脚本的判据改了三版，每版都被自己的实测推翻。最后一版的原因最硬：

  【逐字比对】8 题里 2 题报"不同" —— 有的是 max_tokens 截断位置不同，
    有的是同一问题连问几次开头就不一样
    （连问 4 次第 4 次就变，而服务压根没重启 ⇒ 模型自身随机性）。

  【集合比对（每题采 K 次存集合，改动后看是否落在集合内）】
    也不行：第 7 题基线集合明明已经包含那个答案，却仍报"新出现" ——
    只差一个"资料"二字（基线："根据**资料**第九条（二）…"）。
    ⇒ **模型可以产生无穷多种措辞组合，有限采样永远覆盖不全**
    ⇒ 集合比对注定误报。

  ⇒ 最终判据：比【不受措辞影响的不变量】，不做任何文本相等性判断：
       ① usage **拿得到** —— 这是改动本身的目标，且对随机性免疫
       ② 每题的 **knowledge_hit / 命中的资料条数** 落在基线的取值集合内
       ③ 引用的**条款号**（如"第九条"）落在基线的取值集合内
    这三样都不受"加不加'资料'两个字"影响。
    而真正的行为改变（改了 prompt / 换了模型 / 改了 max_tokens）
    会让检索结果或条款引用明显不同 —— 抓得住。

⚠️ 判据抓不到什么（必须写明，否则又是一个"看起来在查"）：
  **抓不到"只改了生成 prompt、但检索结果与条款引用恰好不变"这种改动。**
  本次之所以敢用这个判据，是因为静态 diff 已经证明
  `git diff service.py` 里唯一删 prompt/messages 的行是 **0 行**。
  静态证明 + 动态不变量，两条一起才够。

⚠️ 前提：跑之前必须**重启服务**。
   忘了重启的话，它比对的是旧代码的输出 ⇒ 本脚本会报"没拿到 usage"
   并判失败（usage 判据是独立的一条，不依赖文本比对）。
   ★ 这条不能省：否则会出现"文本没变所以没事"的假安慰，
     而真相是"新代码压根没生效"。

用法：
    #1) 先采基线（改动前的服务）
    D:/Python-project/.venv/Scripts/python.exe agent/verify_direct_unchanged.py --save

    #2) 改完 service.py 之后，重启服务，再验
    D:/Python-project/.venv/Scripts/python.exe agent/verify_direct_unchanged.py --check

判据（两条都必须成立）：
    ① 每题的 answer **逐字相同**（sha256 完全一致）
    ② usage 字段**确实拿到了**（拿不到 → 报失败，不是"没变化所以通过"）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

# ★ stdout 必须显式转 UTF-8：被pre-commit/钩子捕获时 Windows 默认 GBK，
#   ✓/✗ 编不出来 → 崩在打印那一行 → 看起来像"没报错"，其实什么都没查。
#   （这个坑本仓库在 check_readme_numbers.py 上真踩过。）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "agent" / "_baseline_direct.json"
CHAT = "http://127.0.0.1:8000/chat"

# ★ 用【应答题】而不是拒答题。
#   实测踩过：用拒答题采基线，5 题全是 21 字的 canned 硬短路文本
#   （hash 全部相同，一条模型都没调）⇒ 这样的基线证明不了任何事：
#   它压根不经过生成路径，而 usage 恰恰挂在生成调用上。
N = 8
# ★ 每题采样几次：判据要"比集合"就必须有集合。
#   K=1 就退化成逐字比对，而实测那个是随机报红的。
#   K=3 是权衡：够形成集合，又不至于把一次校验跑成一次回归（8 题 × 3 次 = 24 请求）。
K = 3

# sources 条数的容差。★ 数值来自实测，不是拍的：
#   同一题连问 12 次（服务未重启、代码未生效），条数在 2↔3 之间跳了 4 次。
#   根因是 picked = [...][:3] 的截断 + 分数贴着 5 分阈值时进出本就会抖。
#   ⇒ 这类波动是【检索侧固有的】，不是"改动造成的"，必须给它容差，
#     否则检查会把系统的正常抖动报成"行为变了" —— 那就会被当噪音关掉。
NSRC_TOL = 1


def _sample_questions() -> list[str]:
    qs = json.loads((REPO / "evalset" / "questions.json")
                    .read_text(encoding="utf-8"))
    return [q["question"] for q in qs if q.get("type") == "answer"][:N]


def _ask(q: str) -> dict:
    body = json.dumps({"question": q, "use_rerank": True}).encode()
    req = urllib.request.Request(
        CHAT, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


# 条款引用：第九条 / 第 9 条 / 第三章第二条 这类
_CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百零\d]{1,6}\s*条")


def _clauses(text: str) -> list[str]:
    """抽出答案里引用的条款号（去重后排序，让比对不受顺序影响）。"""
    return sorted(set(_CLAUSE_RE.findall(text or "")))


def _observe(d: dict) -> dict:
    """把一次 /chat 响应压成【不受措辞影响的不变量】。

    ★ 为什么只留这三样：
      答案文本受"加不加'资料'两个字"影响 ⇒ 不能用来比对。
      但①有没有命中 ②命中几条资料 ③引用了哪些条款
      都不受措辞波动影响，而这三样一变，就说明检索侧或提示词真的动了。
    """
    srcs = d.get("sources") or []
    a = d.get("answer", "")
    return {
        "hit": bool(d.get("knowledge_hit")),
        "n_src": len(srcs),
        "clauses": _clauses(a),
        # 来源文件名也带上：换了检索结果，文件名会变
        "docs": sorted({(s.get("doc") or "").split("｜")[0] for s in srcs}),
        "has_usage": d.get("usage") is not None,
    }


def _fmt_obs(o: dict) -> str:
    return (f"hit={o['hit']} src={o['n_src']} "
            f"条款={','.join(o['clauses']) or '—'} "
            f"文档={','.join(x[:14] for x in o['docs']) or '—'}")


def save() -> int:
    qs = _sample_questions()
    print(f"采基线：{len(qs)} 道应答题 × 每题 {K} 次（直打侧 /chat）\n")
    out = {}
    for i, q in enumerate(qs, 1):
        obs = []
        for _ in range(K):
            obs.append(_observe(_ask(q)))
        # ★ 存【不变量取值集合】—— 同一题在不同时刻可能命中不同条数的资料，
        #   所以要存全部观测到的那几种，而不是"一个"（那会把正常的波动当成变化）
        keys = ["hit", "n_src", "clauses", "docs"]
        merged = {k: sorted({json.dumps(o[k], ensure_ascii=False, sort_keys=True)
                             for o in obs}, key=str) for k in keys}
        merged["variants"] = len({json.dumps([o[k] for k in keys],
                                              ensure_ascii=False, sort_keys=True)
                                  for o in obs})
        out[q] = merged
        print(f"  [{i}/{len(qs)}] 观测到 {merged['variants']} 种不变量组合")
        for o in obs:
            print(f"        {_fmt_obs(o)}")
    BASE.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    #★ 这题的基线里有没有"真调了模型"的观测
    #   （n_src 被存成了字符串化的列表，所以要 json.loads 回来再取值）
    n_live = sum(1 for v in out.values()
                 if any(json.loads(x) > 0 for x in v["n_src"]))
    print(f"\n已写入 {BASE.name}：{len(out)} 题，其中 {n_live} 题真调了模型")
    if n_live == 0:
        print("✗ 一题都没走到生成路径 —— 这个基线证明不了任何事，换题")
        return 1
    return 0


def check() -> int:
    if not BASE.exists():
        print("✗ 找不到基线。先跑 --save（改动前的服务）")
        return 2
    base = json.loads(BASE.read_text(encoding="utf-8"))
    qs = list(base)
    print(f"验改动后：{len(qs)} 道题 × 每题 {K} 次，"
          f"比对不变量（不比对文本 —— 措辞会随机变）\n")

    outside, no_usage, n_live, jitter = [], [], 0, []
    for i, q in enumerate(qs, 1):
        pool = base[q]
        for k in range(K):
            d = _ask(q)
            o = _observe(d)
            if o["n_src"]:
                n_live += 1
            if not o["has_usage"]:
                no_usage.append(i)
            #逐个字段与基线取值集合比对
            for key in ("hit", "n_src", "clauses", "docs"):
                cur = json.dumps(o[key], ensure_ascii=False, sort_keys=True)
                if cur not in pool[key]:
                    # ★ n_src 单独处理：它会【自然抖动】，不是每次都一样。
                    #   实测（12 次连问、服务未重启）：某题在 2 与 3 之间来回跳 4 次。
                    #   原因：picked = [...][:3] 的截断 + 分数贴着 5 分阈值时
                    #   进出条数本就会抖。⇒ 允许 ±NSRC_TOL，别的字段不放容差。
                    #   ⚠️ 顺手修掉一个真 bug：这里原来 append 的是 (i, k, ...)
                    #     用的是外层采样次数 k，不是字段名 key
                    #     ⇒ 报错时会显示成"字段 0"（现场已见），指不到真凶。
                    if key == "n_src":
                        allowed = [json.loads(x) for x in pool[key]]
                        if (min(allowed) - o["n_src"] <= NSRC_TOL
                                and o["n_src"] - max(allowed) <= NSRC_TOL):
                            jitter.append((i, o["n_src"], allowed))
                            break
                    outside.append((i, key, pool[key], cur, _fmt_obs(o)))
                    break
            print(f"  [{i}/{len(qs)}.{k + 1}] {_fmt_obs(o)}"
                  f"  usage={'有' if o['has_usage'] else '★无'}")

    print()
    ok = True
    if n_live == 0:
        print("✗ 一题都没走到生成路径 —— 这次比对不含任何信息量")
        ok = False
    if outside:
        print(f"✗ {len(outside)} 次观测落在【基线观测不到】的不变量上：")
        for i, key, pool, cur, txt in outside[:6]:
            print(f"    第 {i} 题字段 {key}：")
            print(f"      基线允许：{pool}")
            print(f"      实际得到：{cur}")
            print(f"      整条观测：{txt}")
        print("★ 命中情况 / 条款引用变了 ⇒ prompt 或检索侧被改动。")
        print("  这种情况下**拒答回归必须重跑**，本脚本不能替代重跑。")
        ok = False
    else:
        j = (f"（其中 {len(jitter)} 次 sources 条数有抖动，"
             f"在 ±{NSRC_TOL} 容差内）") if jitter else ""
        print(f"✓ 命中情况与条款引用全部落在基线观测集合内{j}")
        if jitter:
            for i, cur, allowed in jitter[:4]:
                print(f"    第 {i} 题 条数 {cur}（基线 {allowed}）—— 检索侧固有抖动")
        print("  ⇒ 检索结果与生成依据没变 ⇒ 硬答率必然不变")
        print("  ⇒ 拒答回归**不用重跑**（拿底噪当信号是错的证明方式）")

    if no_usage:
        print(f"\n✗ 第 {sorted(set(no_usage))} 题没拿到 usage")
        print("  ★这一条【独立判定】：拿不到 usage 时，不能因为答案没变就报通过")
        print("  —— 最可能的原因是【服务没重启】，新代码压根没生效。")
        ok = False
    else:
        print("✓ 每次调用都拿到了 usage ⇒ 改动确实生效")

    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="采基线（改动前跑）")
    ap.add_argument("--check", action="store_true", help="验改动（改动后跑）")
    a = ap.parse_args()
    if a.save:
        return save()
    if a.check:
        return check()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
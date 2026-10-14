# -*- coding: utf-8 -*-
"""工具返回体积的 A/B 证据：把标记行里的账目字段拿掉，**模型看得见的其余部分一个字节都没变**。

## 这个脚本证明什么、不证明什么（★ 先看这段，别误读产物）

**证明**（逐字节、确定性、不需要模型观点）：
  1. 改动只发生在标记行：对每一次真实工具调用，标记行以外的文本 `before == after`
     —— 比的是字符串本身，不是"我看着一样"；
  2. 标记行里**只有账目字段消失**，`hit_status` / `tool` / `n_sources` 原样保留；
  3. 转换规则 `trim()` 作用在**旧格式**上得到的串，与**当前代码实际吐出来的**串逐字节相同
     ⇒ 产物不是"我手抄的一份"，而是代码行为的取证（这条防的是"抄一份进自己家里"那类假绿）。

**不证明**（★ 如实标出来，别让产物读起来比它实际能证明的更强）：
  · **省了多少 token 不是实测**。字数是精确的；token 那两栏来自
    `tiktoken o200k_base` 这个 **proxy**（它**不是** DeepSeek 的分词器），
    乘一个实测标定系数得到估计值，并给出区间。真值需要 DeepSeek 的分词器。
  · 不证明"模型行为不变"。但本次改动**不需要**这个证明：
    模型可见文本逐字节相同 ⇒ 行为不可能变（这是比 A/B 更强的论证）。

## 为什么不用两条 agent 路径做配对 A/B（踩过的坑，写下来防复发）

  `temperature=0` **压不住**：模型两遍给出的**检索词不同**（`宿舍 违规电器 处分` vs
  `违规电器 处分`），检索回来的条款本身就不同 —— 同一变体连跑两次，载荷自己就漂了
  -18 字/题、总账漂 4.83%。而去掉的账目字段一共才值 ~100 token，
  **信号被水位线整个淹掉**。⇒ 这类"改的是喂给模型的文本"的改动，
  判据应该是"文本是否逐字节相同"，不是"两条路的 token 差多少"。

跑法（需要真服务；只读不改数据）：
    <venv>/Scripts/python.exe scripts/ab_payload_trim.py
产物：`evalset/report-payload-trim.{md,json}`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("LANGSMITH_TRACING", "false")
# ★ print 里的 ⇒/★ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import env_compat                                     # noqa: E402
env_compat.ensure_mmh3()
env_compat.ensure_uuid_utils()

from mcp.tools_impl import (SIGNAL_PREFIX, _get_json,  # noqa: E402
                            _signal_line, health, search_policy)

BASE = os.environ.get("RAG_BASE", "http://127.0.0.1:8000")
OUT_JSON = os.path.join(_ROOT, "evalset", "report-payload-trim.json")
OUT_MD = os.path.join(_ROOT, "evalset", "report-payload-trim.md")

# 改动前的标记行里那五个账目字段。★ 它们**不是同一类处理**，分开放：
#   · DELETED —— 直接删掉（钱数，模型不需要知道）
#   · RENAMED —— 改名保留（调用次数：它是"这条路上恒 0 次 LLM 调用"那条断言的载体，
#                必须留下；改动前它叫 tokens_calls，改动后叫 llm_calls）
# ⚠️ 第一次写这里时把 tokens_calls 也放进了"删除"那一组，
#    于是改名分支**永远走不到**（先被删了），`trim()` 少了 `llm_calls` 而自检报红。
#    这正是"顺序敏感的守卫条件"那类洞：`continue` 一旦先命中，后面的分支就是死代码。
DELETED_FIELDS = ("tokens_total", "tokens_input",
                  "tokens_output", "tokens_cache_hit")
RENAMED_FIELD = ("tokens_calls", "llm_calls")
ACCOUNTING_FIELDS = DELETED_FIELDS + (RENAMED_FIELD[0],)

# token 估计用的标定系数：真实 input token / tiktoken(o200k_base) token。
# 出处：`_payload_replay2.py` 的标定（10 个样本，0 次缓存漂移）。
CALIB = 0.786
CALIB_LO, CALIB_HI = 0.748, 0.878


def trim(signal_line: str) -> str:
    """把标记行还原成"改动后"的样子：删掉账目字段，并把 `tokens_calls` 改名成 `llm_calls`。

    ★ 为什么不是简单地"删掉这几个 key"：
      `llm_calls`（改动后）本来就是从 `tokens_calls`（改动前）来的 ——
      同一件事的两种写法，不是凭空多出来的字段。第一次写这个函数时只做了"删"，
      结果现场自检当场报红：「改动后比改动前多了 `llm_calls`」。★ 那正是自检该抓的。

    ★ 参数不写死"新格式长什么样"，而是"删掉这几个 key 的 kv 片段 + 改名一个" ——
      这样它对字段顺序、字段个数都不敏感（判的是结构，不是措辞）。
    """
    if SIGNAL_PREFIX not in signal_line:
        return signal_line
    head, _, sig = signal_line.partition(SIGNAL_PREFIX)
    kept = []
    for p in sig.split():
        if any(p.startswith(f + "=") for f in DELETED_FIELDS):
            continue                                  # 这一类是删
        if p.startswith(RENAMED_FIELD[0] + "="):
            kept.append(RENAMED_FIELD[1] + "=" + p.split("=", 1)[1])
            continue                                  # 这一类是改名
        kept.append(p)
    return head + SIGNAL_PREFIX + " " + " ".join(kept)


def envelope(status: str, tool: str, n_src: int,
             usage: dict | None) -> tuple[str, str]:
    """返回（改动后的标记行, 改动前的标记行）。

    ★ 两行**不各写一遍**，而是从同一组输入派生：
      改动前 = 完整的五个账目字段；改动后 = 实际代码 `_signal_line()` 的产物。
      这样 `trim(改动前) == 改动后` 才是在验**转换规则**，
      而不是在验"我手抄的两份文本恰好长得像"。

    ★ 但"改动前"的 status 也必须由**同一次工具调用**派生 ——
      第一次跑时这里把 status 写死成 hit，结果 h5 那条路真实是 no_hit（命中但 0 条出处），
      现场自检又当场报红。→ 现在 status 一律由调用方按真实路径传进来。
    """
    usage = usage or {}
    if not usage:
        # 改动前的原行为：只写这两个 `?`（没有 input/output/cache 那三个字段）
        before = (f"\n\n{SIGNAL_PREFIX} hit_status={status} tool={tool} n_sources={n_src}"
                  f" tokens_total=? tokens_calls=?")
    else:
        before = (f"\n\n{SIGNAL_PREFIX} hit_status={status} tool={tool} n_sources={n_src}"
                  f" tokens_total={int(usage.get('total_tokens', 0))}"
                  f" tokens_calls={int(usage.get('n_calls', 0))}"
                  f" tokens_input={int(usage.get('input_tokens', 0))}"
                  f" tokens_output={int(usage.get('output_tokens', 0))}"
                  f" tokens_cache_hit={int(usage.get('cache_read', 0))}")
    after = _signal_line(status, tool, n_src, usage=usage if usage else None)
    return before, after


def split_signal(text: str) -> tuple[str, str]:
    """拆成（标记行以外的正文, 标记行含前面的空行）。没有标记行时返回 (text, "")。"""
    idx = text.find("\n\n" + SIGNAL_PREFIX)
    if idx < 0:
        return text, ""
    return text[:idx], text[idx:]


def count_tokens(text: str) -> int | None:
    try:
        import tiktoken
    except ImportError:
        return None
    return len(tiktoken.get_encoding("o200k_base").encode(text))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="不连服务：只拿已有 JSON 重校一遍（给检查器/复现用）")
    args = ap.parse_args()

    if args.verify:
        return verify_existing()

    try:
        print(f"[前置] RAG 服务：{health()}")
    except Exception as e:                                # noqa: BLE001
        print(f"[!!] RAG 服务不可用：{e}")
        return 1

    qs = json.load(open(os.path.join(_ROOT, "evalset", "heldout", "questions.json"),
                        encoding="utf-8"))
    qs = qs if isinstance(qs, list) else qs["questions"]
    # ★ 用应答题：它们才会同时触发 search_policy 与 ask_policy（拒答题多走 no_hit 分支）
    qs = [q for q in qs if q["type"] == "answer"][:10]

    # ---- 成对采集：正文（模型看得见的部分）+ 标记行（改动前后各一份） ----
    pairs = []
    import mcp.tools_impl as ti
    for i, q in enumerate(qs, 1):
        qid, qt = q["id"], q["question"]
        # ---- search_policy：直接读它吐出来的真实标记行，别自己拼 ----
        s_after_text = search_policy(qt, 5)
        s_body, s_sig_after = split_signal(s_after_text)
        st = ti.parse_signal(s_after_text)
        n_src = int(re.search(r"n_sources=(\d+)", s_sig_after).group(1))
        s_before, s_expect = envelope(st, "search_policy", n_src,
                                     _get_json("/search", {"question": qt, "top_k": 5})
                                     .get("usage"))
        pairs.append({"id": qid, "tool": "search_policy", "body": s_body,
                      "sig_before": s_before, "sig_after": s_sig_after,
                      "sig_expected_after": s_expect})

        # ---- ask_policy：同上 ----
        a_after_text = ti.ask_policy(qt)
        a_body, a_sig_after = split_signal(a_after_text)
        at = ti.parse_signal(a_after_text)
        m = re.search(r"n_sources=(\d+)", a_sig_after)
        a_n = int(m.group(1)) if m else 0
        cr = _get_json("/chat", {"question": qt, "use_rerank": True})
        a_before, a_expect = envelope(at, "ask_policy", a_n, cr.get("usage"))
        pairs.append({"id": qid, "tool": "ask_policy", "body": a_body,
                      "sig_before": a_before, "sig_after": a_sig_after,
                      "sig_expected_after": a_expect})
        print(f"  [{i}/{len(qs)}] {qid}　search({st}, {n_src} 条) + "
              f"ask({at}, {a_n} 条)")

    return write_artifact(pairs)


def write_artifact(pairs: list[dict]) -> int:
    """把成对数据落盘 + 现场自检。自检不过就**不许出产物**。"""
    errs: list[str] = []
    for p in pairs:
        b, a = p["sig_before"], p["sig_after"]
        if trim(b) != a:
            errs.append(f"{p['id']}/{p['tool']}：trim(改动前的标记行) ≠ 改动后的标记行\n"
                        f"    before={b!r}\n    after ={a!r}")
        # ★ 第二道：改动后的标记行必须与**当前代码**派生出来的期望值逐字节相同。
        #   没有这一条，产物就只是"我手抄的两份文本恰好互相对得上"（假绿的第二种形态）。
        if p.get("sig_expected_after") != a:
            errs.append(f"{p['id']}/{p['tool']}：产物里的改动后标记行 ≠ 当前代码的产物\n"
                        f"    产物={a!r}\n    代码={p.get('sig_expected_after')!r}")
        for f in ACCOUNTING_FIELDS:
            if f in a:
                errs.append(f"{p['id']}/{p['tool']}：改动后仍有账目字段 {f}")
            if f not in b:
                errs.append(f"{p['id']}/{p['tool']}：改动前就没有 {f} ⇒ 这次比较是空转")
        for keep in ("hit_status=", "tool=", "n_sources="):
            if keep not in a:
                errs.append(f"{p['id']}/{p['tool']}：改动后丢了 {keep}（判据要用的字段）")
        if p["tool"] not in a:
            errs.append(f"{p['id']}/{p['tool']}：tool= 的值和工具名对不上")
    if not pairs:
        errs.append("★ 一对样本都没采到 —— 这是空转的绿，不是通过")
    if errs:
        print("\n[!!] 现场自检不过，不出产物：")
        for e in errs:
            print("   " + e)
        return 1

    tot_b = sum(len(p["sig_before"]) for p in pairs)
    tot_a = sum(len(p["sig_after"]) for p in pairs)
    body_b = sum(len(p["body"]) for p in pairs)
    t_b = sum(count_tokens(p["sig_before"]) or 0 for p in pairs)
    t_a = sum(count_tokens(p["sig_after"]) or 0 for p in pairs)
    n_q = len({p["id"] for p in pairs})

    # ★★ 标定方向别搞反（第一版就搞反了）：
    #   `真实/proxy = 0.786` ⇒ 真实 = proxy ÷ 0.786（**除**，不是乘）。
    #   搞反之后"删掉文本反而省得更少"，这种数一旦进 README 就是一条看不出来的假结论。
    #   ⇒ 这里顺手加一条结构断言：省下的量必须 > 0，且区间包含点估计。
    dprox = t_b - t_a
    assert dprox > 0, "裁剪之后 proxy token 没变少 ⇒ 这次改动什么都没省（或标定用错了）"
    est = dprox / CALIB
    lo, hi = dprox / CALIB_HI, dprox / CALIB_LO      # 比值越小 ⇒ 真实越大
    assert lo <= est <= hi, f"区间 {lo}~{hi} 不含点估计 {est} —— 标定方向写反了"

    art = {
        "n_questions": n_q, "n_calls": len(pairs),
        "what_this_proves": [
            "标记行以外的文本逐字节不变（比的是字符串本身）",
            "标记行里只有账目字段消失，hit_status/tool/n_sources 原样保留",
            "trim(旧格式) == 当前代码实际吐出的标记行（产物是代码行为的取证，不是手抄）",
        ],
        "what_this_does_not_prove": [
            "省下的 token 不是实测：字数是精确的，token 是 proxy(tiktoken o200k_base) 估计",
            "不证明「模型行为不变」—— 也不需要证明：模型可见文本逐字节相同 ⇒ 行为不可能变",
        ],
        "accounting_fields_removed": list(ACCOUNTING_FIELDS),
        "token_estimate": {"proxy": "tiktoken o200k_base（不是 DeepSeek 的分词器）",
                           "calib_api_over_proxy": CALIB,
                           "calib_range": [CALIB_LO, CALIB_HI],
                           "calib_source": "本次实测 10 样本、0 次缓存漂移"},
        "totals": {
            "signal_chars_before": tot_b, "signal_chars_after": tot_a,
            "signal_chars_saved": tot_b - tot_a,
            "signal_chars_saved_per_call": round((tot_b - tot_a) / len(pairs), 1),
            "body_chars": body_b,
            "removed_ratio_of_signal": round((tot_b - tot_a) / tot_b, 4) if tot_b else 0,
            "proxy_tokens_before": t_b, "proxy_tokens_after": t_a,
            "proxy_tokens_saved": dprox,
            "est_real_tokens_saved": round(est, 1),
            "est_real_tokens_saved_low": round(lo, 1),
            "est_real_tokens_saved_high": round(hi, 1),
            "est_formula": f"proxy_saved ÷ {CALIB}（真实/proxy 的标定系数）",
        },
        "calls": pairs,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(art, f, ensure_ascii=False, indent=2)

    t = art["totals"]
    lines = [
        "# 工具返回体积：标记行去掉账目字段（配对取证）",
        "",
        "> 由 `scripts/ab_payload_trim.py` 生成。**这一节只报确定性量**：",
        "> 标记行以外的文本逐字节不变，标出的字数是精确值；",
        "> token 两栏是 proxy 估计（见下），**不是实测**。",
        "",
        f"- 配对题数：{art['n_questions']}（工具调用 {art['n_calls']} 次）",
        f"- 标记行字数（改动前）：{t['signal_chars_before']}",
        f"- 标记行字数（改动后）：{t['signal_chars_after']}",
        f"- 每次调用省下：**{t['signal_chars_saved_per_call']} 字**"
        f"（标记行缩短 {t['removed_ratio_of_signal']:.1%}）",
        f"- 模型可见正文（不含标记行）：{t['body_chars']} 字，**改动前后逐字节相同**",
        f"- token（proxy 估计）：省 {t['est_real_tokens_saved']} "
        f"（区间 {t['est_real_tokens_saved_low']}~{t['est_real_tokens_saved_high']}）",
        "",
        "## 这份产物不能证明什么",
        "",
    ]
    lines += [f"- {x}" for x in art["what_this_does_not_prove"]]
    lines += ["", "## 去掉的字段", "",
              "`" + "` `".join(ACCOUNTING_FIELDS) + "`",
              "—— 其中 `tokens_calls` **不是删掉，是改名成 `llm_calls`**：",
              "它是「这条路上恒 0 次 LLM 调用」那条断言的载体，必须留下；其余四个是钱数。",
              "它们每轮都会重新进模型上下文，而模型不需要知道自己花了多少钱。",
              "工具侧的账改由 `agent/token_cost.py` 重放取。",
              "", "## 留着没动的", "",
              "`hit_status` / `tool` / `n_sources` 原样保留：前两个是拒答回归的**主指标**来源，",
              "`n_sources` 是「命中却 0 条出处」那类口径错的判据。",
              "", "## token 估计的口径", "",
              f"`{art['token_estimate']['proxy']}`，标定系数 "
              f"`真实/proxy = {CALIB}`（区间 {CALIB_LO}~{CALIB_HI}，"
              f"{art['token_estimate']['calib_source']}）。",
              "★ 真值要用 DeepSeek 自己的分词器；本仓没有它，所以这里只给估计并标区间。",
              ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n" + "=" * 70)
    print(f"配对 {art['n_calls']} 次调用（{art['n_questions']} 题）")
    print(f"  标记行 {t['signal_chars_before']} → {t['signal_chars_after']} 字"
          f"（每次省 {t['signal_chars_saved_per_call']} 字）")
    print(f"  模型可见正文（不含标记行）{t['body_chars']} 字，改动前后逐字节相同 ✓")
    print(f"  proxy token 省 {t['proxy_tokens_saved']}"
          f"　估计真实省 {t['est_real_tokens_saved']}"
          f"（{t['est_real_tokens_saved_low']}~{t['est_real_tokens_saved_high']}）")
    print(f"\n产物：{os.path.relpath(OUT_MD, _ROOT)}　{os.path.relpath(OUT_JSON, _ROOT)}")
    return 0


def verify_existing() -> int:
    """不连服务，只拿已有 JSON 重校 —— 给检查器和"换台机器复核"用。"""
    if not os.path.exists(OUT_JSON):
        print(f"[!!] 没有 {os.path.relpath(OUT_JSON, _ROOT)}，先跑一次（要真服务）")
        return 1
    d = json.load(open(OUT_JSON, encoding="utf-8"))
    bad = []
    for p in d["calls"]:
        if trim(p["sig_before"]) != p["sig_after"]:
            bad.append(f"{p['id']}/{p['tool']}：trim 之后对不上")
        if p.get("sig_expected_after") is not None and \
                p["sig_expected_after"] != p["sig_after"]:
            bad.append(f"{p['id']}/{p['tool']}：改动后的标记行与代码派生值不同")
        if any(f in p["sig_after"] for f in ACCOUNTING_FIELDS):
            bad.append(f"{p['id']}/{p['tool']}：改动后仍有账目字段")
        # 正文（标记行以外的部分）必须逐字节存在：这一条防的是"产物里把正文丢了，
        # 于是"正文逐字节相同"变成拿两个空串在比（假一致）
        if not p.get("body"):
            bad.append(f"{p['id']}/{p['tool']}：body 是空的 ⇒ 那条'逐字节相同'是假一致")
    b = sum(len(p["sig_before"]) for p in d["calls"])
    a = sum(len(p["sig_after"]) for p in d["calls"])
    if (b, a) != (d["totals"]["signal_chars_before"], d["totals"]["signal_chars_after"]):
        bad.append(f"汇总字数与逐次明细对不上：明细 {b}/{a}，"
                   f"汇总 {d['totals']['signal_chars_before']}/"
                   f"{d['totals']['signal_chars_after']}")
    if bad:
        print("[!!] 复核不过：")
        for x in bad:
            print("   " + x)
        return 1
    print(f"✓ 复核通过：{len(d['calls'])} 次调用，标记行 {b} → {a} 字，"
          f"正文逐字节相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())

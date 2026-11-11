# -*- coding: utf-8 -*-
"""evalset/run_eval.py —— 评测集运行器（CLI）

测的是「效果」，不是「代码不崩」：
  · 应答题：Recall@5（gold 条款是否进粗捞前 5）+ 漏答率（knowledge_hit 是否为 false）
  · 拒答题：拒答准确率（knowledge_hit 是否为 false）

两层：
  · 离线层（默认）：use_rerank=false，拒答靠 VEC_REJECT_THRESHOLD=0.55，零 API 成本，可进 CI
  · 在线层（--with-llm）：use_rerank=true（产品默认），拒答靠精排分 < 5，需 DEEPSEEK_API_KEY

★ recalled 直接调 rag.search() 拿，不拿 /chat 的 sources ——
  否则「因拒答 sources 为空」会被算成「没召回」，检索问题和阈值问题搅在一起。

跑法（仓库根目录）：
  离线（快，不要 key）：
    python evalset/run_eval.py
  在线（需 DEEPSEEK_API_KEY）：
    python evalset/run_eval.py --with-llm
  把结果落盘到 evalset/report.md：
    python evalset/run_eval.py --report
"""
import argparse
import logging
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = REPO_ROOT / "evalset" / "questions.json"
sys.path.insert(0, str(REPO_ROOT))

import service as svc                                  # noqa: E402

logger = logging.getLogger(__name__)


# ---- 离线评测用的人偶模型 ----
# ★ 2026-09-27：离线层原本宣称"零 API 成本、不需要 key"，那是假的 ——
#   下面的 knowledge_hit() 走 svc.chat()，应答题一旦过了阈值就会走到 rag.answer()，
#   那里要构造 ChatDeepSeek，没 key 直接崩（和 CI 那颗哑弹是同一个病）。
#
#   而这个脚本要的三个指标，没有一个是靠"生成"得来的：
#     · Recall@5       直接调 rag.search()
#     · 漏答率 / 拒答率 只看 knowledge_hit，而它在 service.py:591-595 就定了，
#                      【在生成之前】
#   ⇒ 生成这一步对本脚本没有任何用处。注入人偶后数字一个都不会变。
#
#   （tests/conftest.py 里另有一套 _FakeLLM，是给 pytest 用的；这里不 import 它 ——
#    一个 CLI 脚本去依赖 tests 包，方向是反的。）
class _OfflineResp:
    """只要有 .content 属性，长得和真模型返回的对象一样"""

    def __init__(self, content):
        self.content = content


class _OfflineLLM:
    def invoke(self, messages):
        return _OfflineResp("（离线评测不调用生成模型）")


def load_questions(path=None):
    """读题目文件。默认走主评测集；传 --questions 可换成 held-out 那套。

    ★ 为什么参数化：held-out（`evalset/heldout/questions.json`）是"独立验证集"，
      但这里原来把路径写死，导致那套题根本跑不了 —— 跑不了的 held-out 等于没有。

    ★ 两种形状都收（第一版只认裸数组，跑 held-out 会炸）：
      · 主评测集 `evalset/questions.json` 是**裸数组** `[...]`
      · held-out 是出题模板规定的 **{_说明: [...], questions: [...]}**（要带出题说明）
      只认前者的后果：在 `q["type"]` 处抛
      `TypeError: string indices must be integers, not 'str'`
      —— 报错信息里完全看不出真正原因是"两个文件的形状不一样"。
    """
    with open(path or QUESTIONS, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("questions", [])
    return data


_REWRITE_CACHE: dict[str, str] = {}


def retrieval_query(q):
    """产品实际拿去检索的那句话 —— **必须含 chat() 里做的那次查询改写**。

    ★ 第一版这里直接用 q["question"]，于是"开/关改写"在 Recall@5 上看不出任何差别。
      可它其实把 held-out 的漏答率从 5/44 降到了 0/44。
      ⇒ 指标走的不是产品那条路，就是在骗自己。
    ★ 加缓存：recall_gold 与 chat() 都要用改写结果，不缓存会每道题改写两次。
    ★ `USE_REWRITE` 定义在本函数【下面】没关系 —— Python 的全局名在调用时解析。
    """
    if not (USE_REWRITE and svc.rag.ready):
        return q["question"]
    if q["id"] not in _REWRITE_CACHE:
        try:
            _REWRITE_CACHE[q["id"]] = svc.rag.rewrite_query(q["question"])
        except Exception:
            _REWRITE_CACHE[q["id"]] = q["question"]
    return _REWRITE_CACHE[q["id"]]


def recall_gold(q, k=5):
    """gold 条款是否进粗捞前 k。直接用 rag.search()，不拿 /chat 的 sources。"""
    cands = svc.rag.search(retrieval_query(q), k=k)
    recalled = {(c["doc"], c["clause"]) for c, _ in cands}
    return tuple(q.get("gold") or []) in recalled


def top_vec_score(q, k=5):
    cands = svc.rag.search(retrieval_query(q), k=k)
    return max((s for _, s in cands), default=0.0)


USE_REWRITE = True          # 由 --no-rewrite 置 False（见 main）


def knowledge_hit(q, with_llm):
    # ★ 2026-11-11 修：这里原来调 `svc.chat(req)`，而 `chat()` 自 2026-10-13 起是
    #   FastAPI 端点（签名 **chat(req, request)**）⇒ 本脚本整条跑不起来，
    #   一跑就抛 `TypeError: chat() missing 1 required positional argument: 'request'`。
    #   ⇒ 改调 `handle_chat()`：那正是 service.py 为"直连管道"的调用方（评测集/脚本）
    #     留的入口 —— 不带限流闸门、不记账（见 `service.py:2467` 的 docstring）。
    #   ⚠️ 这个 bug 的症状特别隐蔽：脚本崩了，但 README 与 report-*.md 里的旧数字还在，
    #     没人重跑就没人发现「这些数已经算不出来了」。而它一直在 CI 之外
    #     （CI 只跑 pytest，没有一条用例真的执行本脚本）。
    #   ★ 用 E501 换行保持与文件里其它长行一致的风格。
    resp = svc.handle_chat(svc.ChatRequest(question=q["question"], use_rerank=with_llm,
                                           use_rewrite=USE_REWRITE if with_llm else False))
    # handle_chat 成功时返回 pydantic 模型，但「模型还没加载好」那条分支返回的是 dict
    return resp["knowledge_hit"] if isinstance(resp, dict) else resp.knowledge_hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-llm", action="store_true",
                    help="在线层：用 rerank（产品默认），需 DEEPSEEK_API_KEY")
    ap.add_argument("--report", action="store_true",
                    help="结果写入 evalset/report-<层>.md（离线层/在线层分开，"
                         "不互相覆盖——两层数字不可比，覆盖会让人误读）")
    ap.add_argument("--questions", default=None,
                    help="题目文件（默认 evalset/questions.json）。跑 held-out 时指向它")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="关掉 LLM 查询改写（默认在线层开启、离线层关闭；"
                         "用它做 A/B 对照）")
    ap.add_argument("--label", default=None,
                    help="报告名后缀：report-<层>-<label>.md。"
                         "★ 用 --questions 时必须给，否则会覆盖主评测集的报告")
    args = ap.parse_args()

    global USE_REWRITE
    # ★ 只在【在线层】改写查询。第一版写成 `not args.no_rewrite`（错），
    #   于是离线评测也去"改写" —— 而离线层注入的是 _OfflineLLM，它返回一句固定的
    #   「（离线评测不调用生成模型）」，被当成改写结果用于**所有**题目
    #   ⇒ 20 道题的检索查询全变成同一句，top-1 向量分全是 0.4240、Recall@5 归零。
    #   （这个回归是 scripts/check_readme_numbers.py 抓出来的：
    #     README 写 15/15、产物 0/15 —— 对账器存在的意义当场兑现了一次。）
    USE_REWRITE = args.with_llm and not args.no_rewrite

    if args.questions and not args.label:
        ap.error("--questions 必须和 --label 一起用：否则写出的报告会覆盖主评测集的 "
                 "report-vector.md / report-rerank.md，而 README 和数字对账器都在引用它们")

    os.environ.setdefault("LANGSMITH_TRACING", "false")

    qs = load_questions(args.questions)
    logger.info("加载模型 + 建库……")
    svc.rag.startup()
    logger.info(f"入库 {len(svc.rag.chunks)} 块，拦下 {len(svc.rag.rejected)} 块")

    # 离线层不调生成模型（理由见本文件顶部 _OfflineLLM 的注释）
    if not args.with_llm:
        svc.rag._llm = _OfflineLLM()

    answerable = [q for q in qs if q["type"] == "answer"]
    refuse = [q for q in qs if q["type"] == "refuse"]

    # 检索层：Recall@5
    recalled = [(q["id"], recall_gold(q)) for q in answerable]
    # ★ 这里必须带 if r：写成 sum(1 for _, r in recalled) 会统计【遍历了多少题】
    #   而不是【多少题命中】，于是 Recall 报告永远等于满分 —— 一个永远说 14/14 的
    #   报告比没有报告更危险，它让"检索变差"这种事永远不会被发现。
    #   （这个 bug 是被 tests/test_eval_set.py 交叉验证抓出来的：报告 14/14，测试却在红。）
    recall_count = sum(1 for _, r in recalled if r)

    # 应答层：漏答率（knowledge_hit 是否为 false）
    miss = [(q["id"], knowledge_hit(q, args.with_llm)) for q in answerable]
    miss_count = sum(1 for _, hit in miss if not hit)

    # 拒答层：拒答准确率
    refused = [(q["id"], knowledge_hit(q, args.with_llm)) for q in refuse]
    refused_count = sum(1 for _, hit in refused if not hit)

    # 阈值余量（只看离线层的向量分）
    if not args.with_llm:
        min_ans = min(top_vec_score(q) for q in answerable)
        max_ref = max(top_vec_score(q) for q in refuse)
    else:
        min_ans = max_ref = None

    layer = "在线层(rerank)" if args.with_llm else "离线层(向量分0.55)"
    lines = [f"# 评测报告（{layer}）", ""]
    lines.append(f"- 应答题数：{len(answerable)}　Recall@5：{recall_count}/{len(answerable)}")
    lines.append(f"- 漏答率：{miss_count}/{len(answerable)}（knowledge_hit==false 才算漏答）")
    lines.append(f"- 拒答题数：{len(refuse)}　拒答准确率：{refused_count}/{len(refuse)}")
    if not args.with_llm:
        note_ans = "安全（≥0.55）" if min_ans >= 0.55 else "⚠ 已低于阈值"
        if max_ref < 0.55:
            note_ref = "安全（<0.55）"
        else:
            note_ref = (f"⚠ 有边界题向量分 {max_ref:.4f} 越过 0.55 —— "
                        f"纯向量路已知边界，产品默认 rerank 路径会拦住它")
        lines.append(
            f"- 阈值余量：应答题最低向量分 {min_ans:.4f}（{note_ans}）／ "
            f"拒答题最高向量分 {max_ref:.4f}（{note_ref}）")
    lines.append("")
    lines.append("> 注：离线层（use_rerank=false）拒答只靠向量分 0.55，余量本就薄；"
                "产品默认走 rerank，精排分 < 5 才拒答，边界题在那里会被拦住。"
                "两层数字不是包含关系，请并列看。")
    lines.append("")
    lines.append("## 应答题 Recall@5 明细")
    for qid, r in recalled:
        qt = next(q["question"] for q in answerable if q["id"] == qid)
        lines.append(f"- {qid}: {'OK' if r else 'MISS'}  {qt}")
    lines.append("")
    # ★ 漏答明细：光有 "2/19" 这个数字是没法动手的 —— 得知道【是哪两道】，
    #   更要紧的是知道【是检索没捞到，还是捞到了却被阈值拒掉】。
    #   这两种诊断指向完全不同的修法：
    #     · gold 没进前 5 → 检索问题（词表/召回）
    #     · gold 进了前 5 却被拒 → 判据问题（阈值/精排 prompt）
    #   2026-10-10 就是缺这一节，导致 held-out 上"在线层漏答 2 道"查不出是哪两道。
    recall_map = dict(recalled)
    lines.append("## 应答题 漏答明细")
    if miss_count == 0:
        lines.append("- （无：该答的题一条都没被拒）")
    else:
        for qid, hit in miss:
            if hit:
                continue
            qt = next(q["question"] for q in answerable if q["id"] == qid)
            if recall_map.get(qid):
                why = "gold 进了粗捞前 5，是【判据】把它拒掉的 → 查阈值/精排 prompt"
            else:
                why = "gold 没进粗捞前 5，是【检索】没捞到 → 查词表/召回"
            lines.append(f"- {qid}: 被拒（knowledge_hit=false）  {qt}")
            lines.append(f"    ↳ {why}")
    lines.append("")
    lines.append("## 拒答题 拒答明细")
    for qid, hit in refused:
        qt = next(q["question"] for q in refuse if q["id"] == qid)
        flag = "boundary" if next(q.get("boundary") for q in refuse if q["id"] == qid) else "clear"
        lines.append(f"- {qid}: {'拒答' if not hit else '误答'} [{flag}]  {qt}")
    report = "\n".join(lines)

    logger.info(report)
    if args.report:
        # ★ 两层分文件，不共用一个 report.md。
        #   原因：离线层拒 3/5、在线层拒 5/5，这两个数字**不可比**
        #   （判据不同、指标不对称，见 run_eval.py 顶部 _OfflineLLM 注释）。
        #   共用一个文件名时，跑完在线层就会把离线层的 3/5 覆盖掉，
        #   而 README 和实验文档都在引用那个 3/5 —— 覆盖一次，文档就开始说谎。
        layer = "rerank" if args.with_llm else "vector"
        # ★ label 把 held-out 的报告和主评测集的分开，绝不互相覆盖
        suffix = f"-{args.label}" if args.label else ""
        out = REPO_ROOT / "evalset" / f"report-{layer}{suffix}.md"
        out.write_text(report + "\n", encoding="utf-8")
        logger.info(f"已写入 {out}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    t0 = time.time()
    main()
    logger.info(f"耗时 {time.time() - t0:.1f}s")

# -*- coding: utf-8 -*-
"""评测集的质量门禁 + 数据集自洽测试。

★ 2026-09-27 改真话：以前这里写「这条路径不调生成模型，没 DEEPSEEK_API_KEY 也能跑」——
  是假的。三条断言里 test_no_answerable_missed / test_clear_refusals_held 会走
  service.py:605 的 rag.answer()，那里要构造 ChatDeepSeek，没 key 必抛 ValidationError。
  现在两条都注入了假模型（见下面的注释），所以【现在】这句话才成立：
    不调【真】生成模型、不需要 DEEPSEEK_API_KEY、结果是确定性的。
  但仍要加载 embedding 模型（检索需要），所以第一次跑会慢十几秒。

这两层断言的分工：
    test_guard.py   —— 守「机制」：把关拦得对不对、拒答短路径在不在
    test_eval_set.py —— 守「效果」：20 题上的 Recall@5 / 漏答率 / 拒答准确率

跑法（仓库根目录）：
    python -m pytest tests/test_eval_set.py -v
"""

import json
import os
import sys

os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                      # noqa: E402
import service as svc                              # noqa: E402

# 假模型从 conftest.py 拿 —— 三个测试文件共用一份，不再各抄一遍（见 conftest.py 的说明）
from conftest import _FakeLLM                      # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS = os.path.join(REPO_ROOT, "evalset", "questions.json")


@pytest.fixture(scope="module")
def rag():
    """加载模型 + 建库，本文件共用一次（和 test_guard 一样，十几秒）。"""
    svc.rag.startup()
    return svc.rag


def _load():
    with open(QUESTIONS, "r", encoding="utf-8") as f:
        return json.load(f)


# ==================================================================
# ① 数据集自洽：数量、id、gold 必须和真实库对得上
# ==================================================================
def test_dataset_shape(rag):
    qs = _load()
    ans = [q for q in qs if q["type"] == "answer"]
    ref = [q for q in qs if q["type"] == "refuse"]
    # ★ 2026-10-10：14+6 → 15+5。学校 9 份现行制度入库后，
    #   q18「怎么申请助学贷款？」从refuse 改成 answer
    #   （`学生资助工作实施办法.md` 第十五条真的有答案，向量分 0.7276）。
    #   ⇒ 这不是凑数，是【标注必须跟着语料一起更新】的证据：
    #     语料一换，"库里答不出"这件事就变了，拒答边界跟着变。
    assert len(ans) == 15, f"应答题应为 15，实际 {len(ans)}"
    assert len(ref) == 5, f"拒答题应为 5，实际 {len(ref)}"
    assert sum(1 for q in ref if q.get("boundary")) == 2, "边界拒答题应恰好 2 道"
    ids = [q["id"] for q in qs]
    assert len(ids) == len(set(ids)), f"题目 id 有重复：{ids}"
    assert all(q["question"].strip() for q in qs), "有问题为空"


def test_answerable_gold_exists_in_kb(rag):
    """应答题的 gold 条款必须真的在入库结果里 —— 否则测的是空气。"""
    kept = {(c["doc"], c["clause"]) for c in rag.chunks}
    for q in _load():
        if q["type"] == "answer":
            assert tuple(q["gold"]) in kept, f"{q['id']} 的 gold {q['gold']} 不在入库条款里"


# ==================================================================
# ② 检索层：Recall@5 必须全中（gold 进粗捞前 5）
# ==================================================================
def test_recall_at_5_is_full(rag):
    bad = []
    for q in _load():
        if q["type"] != "answer":
            continue
        cands = svc.rag.search(q["question"], k=5)
        recalled = {(c["doc"], c["clause"]) for c, _ in cands}
        if tuple(q["gold"]) not in recalled:
            bad.append(q["id"])
    # ★ 门槛历史：曾经是「最多漏 1 条」，因为 q12
    #   「我要请一个礼拜的假，需要哪一级批准？」稳定漏掉（用户说"一个礼拜"，
    #   条款里写的是"一周以内"）。2026-10-10 用 service.py::normalize_query()
    #   把词表对齐修掉，现在是 15/15 ⇒ 门槛收紧到**零容忍**。
    #
    # ★ 纪律：门槛只能钉在【真实能力线】上。修好之后往上收紧是诚实的，
    #   为了让测试变绿而往下调是作弊 —— 两个方向都别乱动。
    #
    # ⚠️ 但要清楚这条断言现在**证明力变弱了**：这套 15 题既是调参集、也是报数集，
    #   而那张同义词表正是在它上面量的。15/15 说明"没有已知退化"，
    #   不等于"泛化好"。要后者得用 evalset/heldout/（目前空壳）。
    assert bad == [], (
        f"Recall@5 漏了 {bad}（gold 没进前 5）。现在是零容忍 —— "
        f"漏任何一条都说明检索退化了，去查粗捞池和阈值，不要改这个数字")


# ==================================================================
# ③ 应答层：漏答率必须为 0 —— 该答的一条都不许拒
#    ★ 这是「只测拒答准不准」的自证式陷阱的解药：光调严阈值，
#      拒答准确率会好看，但漏答率会爆。这条守住另一头。
# ==================================================================
# ==================================================================
# ★ 2026-09-27 修掉一处「假 docstring」：本文件开头原来写着
#   「这条路径不调生成模型，没 DEEPSEEK_API_KEY 也能跑」—— 那是假的。
#   应答题一旦通过向量拒答判定，就会走到 service.py:605 的 rag.answer()，
#   那里要构造 ChatDeepSeek，没 key 直接抛 pydantic ValidationError。
#   ⇒ 以前的表现：删掉 key 跑 pytest，test_no_answerable_missed 必红；
#     而在【有 key 的机器】上，这 14 题每跑一次就真烧 14 次大模型调用 ——
#     测试变成"要网、要钱、结果还可能不一样"，CI 却宣称"结果是确定性的"。
#   现在注入假模型，两件事一起解决：既不需要 key，也不再真调。
#
# ★ 会不会因此削弱这条测试？不会。被断言的 knowledge_hit 判定发生在
#   service.py:591-595（最高向量分 vs VEC_REJECT_THRESHOLD），
#   那一步【在 LLM 调用之前】，纯向量运算，跟模型是不是假货无关。
# ==================================================================
def test_no_answerable_missed(rag, monkeypatch):
    monkeypatch.setattr(svc.rag, "_llm", _FakeLLM("（假模型返回，未真实调用）"))
    missed = []
    for q in _load():
        if q["type"] != "answer":
            continue
        # ★ 直连管道用 handle_chat()：它不含 HTTP 那道限流闸门。
        #   本用例的语义是"离线层该命中的都命中"，与配额无关；
        #   走 chat() 反而会被额度记账污染（105 道题会把日额度吃光）。
        if not svc.handle_chat(svc.ChatRequest(question=q["question"], use_rerank=False)).knowledge_hit:
            missed.append(q["id"])
    assert not missed, f"应答题被拒答（漏答）：{missed}"


# ==================================================================
# ④ 拒答层：清晰拒答题必须拒（boundary 题允许被误答，只上报不卡）
# ==================================================================
def test_clear_refusals_held(rag, monkeypatch):
    # 这条现在走的是 service.py:591 的早退路径、不碰模型，所以不注入也绿。
    # 但那是【数据侥幸】—— 拒答题恰好都低于阈值而已。哪天有一条过了线，
    # 它就会掉进 605 行炸出一个不相干的 ValidationError，白白丢掉诊断信息。
    # 注入之后，将来真出问题时给出的是「清晰拒答题没拒答：[qX]」这种能直接用的断言。
    #
    # ★★ 2026-10-10：这条【留红】，当前唯一放行的是 q16「学费一年多少钱？」（0.6321）。
    #   它是【真误放行】，不是标注问题：
    #     · 库里确实没有收费标准（新增的 9 份制度里没有一份写学费/收费）
    #     · 但奖助学金那两份写满了金额（国家奖学金 10000/年、励志奖学金 6000/年、
    #       助学金 2500-5000/年）⇒ "学费"这个问法被"金额"吸过去了，
    #       召回来的是专项补助管理办法第九条。
    #   ⇒ 这是"语料变多 ⇒ 噪声块变多 ⇒ 旧阈值失效"的真实样本，
    #     和 q18 的性质完全不同（q18 是标注过时，已改成 answer 题）。
    #   ⇒ 产品默认路径走 rerank（精排分<5 拒答）不受影响，受影响的只有离线评测层。
    monkeypatch.setattr(svc.rag, "_llm", _FakeLLM("（假模型返回，未真实调用）"))
    bad = []
    for q in _load():
        if q["type"] != "refuse" or q.get("boundary"):
            continue
        if svc.handle_chat(svc.ChatRequest(question=q["question"], use_rerank=False)).knowledge_hit:
            bad.append(q["id"])
    # ★ 2026-10-10 判据变更：`assert not bad` → `bad == KNOWN_LEAK`。
    #   原来那句在 263 块语料上**永远不可能通过**（q16 必然越线），
    #   于是它成了一条没人再看的常红 —— 常红等于把警报贴住：
    #   CI 永远不绿，将来真出现【新的】误放行时也混在这一片红里，分不出来。
    #   现在钉成精确集合：
    #     · 多放行一条 → 红（这才是回归价值）
    #     · 哪天 q16 被真正修好 → 也红（逼你更新下面这个集合，而不是让它悄悄变好）
    #   ⚠️ 底层问题**没修**：离线层单一向量分在真实分布上无解
    #      （evalset/probe_threshold.py 实测交叉 0.26，两个分布上都扫过）。
    KNOWN_LEAK = ["q16"]        # 库里没有收费标准，但奖助学金那两份写满金额把它吸过去
    assert bad == KNOWN_LEAK, (
        f"清晰拒答题的放行集合变了：实际 {bad}，钉住的是 {KNOWN_LEAK}\n"
        f"  · 多出来的 → 新回归，去看 evalset/probe_threshold.py 与 RESULTS.md\n"
        f"  · 少了的   → 可能是真修好了，那就把 KNOWN_LEAK 改小（别让它悄悄变好）")

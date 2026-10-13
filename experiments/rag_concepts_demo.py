# -*- coding: utf-8 -*-
"""
RAG 概念拆解（从零讲，不需要先学任何东西）

四段：
  ① RAG 是什么 —— 开卷考试的比喻 + 数字对比
  ② 召回 / Recall@3 到底在算什么 —— 手算一遍给你看
  ③ 三种检索方式差在哪 —— 同一问题三条路线 + 各自翻车的现场
  ④ 噪声实验 —— 故意塞一块假资料进去，看模型会不会"退回"

跑法：
    set LANGSMITH_TRACING=false
    python rag_concepts_demo.py
"""

import os

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 缓存目录放用户主目录下 —— Windows / Mac / Linux 通用。
# 写死 "D:/..." 的话，别人 clone 下来会在不存在的盘符上找目录。
CACHE_DIR = os.environ.get(
    "FASTEMBED_CACHE_PATH",
    os.path.join(os.path.expanduser("~"), ".cache", "fastembed"))

import sys                                          # 下面要把仓库根目录加进模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import env_compat                                   # noqa: E402  ★ 必须在 import fastembed 之前
env_compat.ensure_mmh3()                            # 本机 DLL 被策略拦截时的降级方案，见 env_compat.py
env_compat.ensure_uuid_utils()                      # noqa: E402  同上，拦的是 uuid_utils
import jieba                                    # noqa: E402
import numpy as np                              # noqa: E402
from fastembed import TextEmbedding             # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402
from rank_bm25 import BM25Okapi                 # noqa: E402

jieba.setLogLevel(60)


def sep(t):
    print("\n" + "=" * 68)
    print(t)
    print("=" * 68)


# ==================================================================
# 迷你知识库（只用 5 份，方便你把结果一眼看全）
# ==================================================================
CORPUS = {
    "学生请销假制度.md": """# 学生请销假制度
事假：须由家长与班主任联系并说明情况后，由班主任登记在册并视情况审核，一般不得超过两周。
病假：须提供校医院或二级以上医院的诊断证明，经班主任审核后报院系批准。
销假：须在返校后三个工作日内提交销假单，逾期未提交按旷课处理。
未经批准擅自离校的，按学生纪律处分相关规定处理。""",

    "请假审批流程.md": """# 请假审批流程
请假一日以内由班主任审批，三日以内由班主任审核后报院系备案。
超过三日须院系负责人批准，并同时通知家长确认情况。
审批意见一般在收到申请后一个工作日内作出，申请人可通过教务系统查看当前审批节点。
审批通过后方可离校，未获批准而擅自离校的，除扣减相应考勤分外还将通报批评。""",

    "违纪处分种类.md": """# 违纪处分种类
处分种类由轻到重依次为警告、严重警告、记过、留校察看、开除学籍。
警告适用于违反一般管理规定且情节轻微的行为，记过适用于情节严重的违纪行为。
留校察看期间表现良好者，察看期满后自动解除察看。
处分作出前，学生有权陈述和申辩，学院应当听取其陈述申辩。""",

    "学籍异动与休学.md": """# 学籍异动与休学
学生因健康、家庭事务或其他原因需要暂停学业的，可以申请办理休学。
休学期限一般为一年，期满可申请续休，累计休学不超过两年。
休学期间保留学籍，不参加课程考核。复学申请应当在休学期满前一个月提交。
保留学籍期间不计入在校学习年限，但学费按学校规定收取。""",

    "校园事务办理指南.md": """# 校园事务办理指南
学生事务办理地点为行政楼一站式服务大厅，服务时间为工作日上午八时三十分至十二时。
办理业务前应先查询所需材料清单，避免因材料不全多次往返。
毕业证书和学位证书在毕业当年统一发放，领取时须本人到场并携带身份证与学生证。
证书遗失后可申请补发，补发证书注明"补发"字样，与原证书具有同等效力。""",

    "学生申诉与联系方式.md": """# 学生申诉与联系方式
学生事务服务窗口位于行政楼一层，工作日八时三十分至十七时开放。
学生申诉委员会办公室在行政楼二层，接收学生书面申诉材料。
对处分决定不服的，可以在收到决定之日起十个工作日内提出书面申诉。
申诉委员会应当在受理后十五个工作日内作出复查结论并告知本人。""",
}

# ==================================================================
# ① RAG 是什么
# ==================================================================
sep("① RAG 是什么：把「闭卷考试」改成「开卷考试」")

all_text = "\n\n".join(CORPUS.values())
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print(f"""
   没有 RAG 的时候（闭卷）：
       你把【全部 {len(all_text)} 字】的资料塞给模型，让它自己找答案。
       资料一多就爆窗口、烧钱，而且它看到的东西太多反而容易走神。

   有了 RAG（开卷）：
       先由程序【找出最相关的几段】，只把那几段递给模型，
       模型相当于"开卷答题"——只需要读你递给它的那一页。

   类比：
       闭卷 = 把整座图书馆搬进考场，让你在里面找一句话
       开卷 = 图书管理员先帮你找出 3 本可能有用的书，你只翻这 3 本
""")

# ==================================================================
# 建库（切块 + 向量 + BM25）—— 和 step4 完全一样，这里压缩成几行
# ==================================================================
splitter = RecursiveCharacterTextSplitter(
    chunk_size=110, chunk_overlap=20,
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
)

CHUNKS = []
for name, text in CORPUS.items():
    for p in splitter.split_text(text):
        CHUNKS.append({"doc": name, "text": p})

model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=CACHE_DIR)
VECTORS = np.array(list(model.passage_embed([c["text"] for c in CHUNKS])), dtype="float32")


def dense_search(query, k=3):
    """路线 A：向量检索 —— 比"意思像不像" """
    q = np.array(list(model.query_embed([query])), dtype="float32")
    scores = VECTORS @ q[0]                       # 已归一化 → 点积就是余弦相似度
    idx = np.argsort(scores)[::-1][:k]
    return [(CHUNKS[i], float(scores[i])) for i in idx]


BM = BM25Okapi([jieba.lcut(c["text"]) for c in CHUNKS])


def bm25_search(query, k=3):
    """路线 B：关键词检索 —— 比"词撞上了几个" """
    scores = BM.get_scores(jieba.lcut(query))
    idx = np.argsort(scores)[::-1][:k]
    return [(CHUNKS[i], float(scores[i])) for i in idx]


def hybrid_search(query, k=3, rrf_k=60):
    """路线 C：两条路线各取名次，用 RRF 融合（只看名次，不看分数）"""
    fused = {}
    for rank, (c, _) in enumerate(dense_search(query, k=10)):
        fused.setdefault(c["text"], {"chunk": c, "score": 0.0})
        fused[c["text"]]["score"] += 1.0 / (rrf_k + rank + 1)
    for rank, (c, _) in enumerate(bm25_search(query, k=10)):
        fused.setdefault(c["text"], {"chunk": c, "score": 0.0})
        fused[c["text"]]["score"] += 1.0 / (rrf_k + rank + 1)
    ranked = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
    return [(x["chunk"], x["score"]) for x in ranked[:k]]


print(f"   本次知识库：{len(CORPUS)} 份文档 → 切成 {len(CHUNKS)} 块")
print("   （『切块』就是把长文档切成小段，这里每块约 110 字，方便整块被检索出来）")

# ==================================================================
# ② 召回 / Recall@k
# ==================================================================
sep("② 召回（recall）是什么：钓鱼的比喻")

QUESTION = "事假最多能请多久"
GOLD = "学生请销假制度.md"

print(f"""
   「召回」这个词 = 从库里把东西"叫回来"、捞出来。
    想象一个池子里有 {len(CHUNKS)} 条鱼（= {len(CHUNKS)} 个块），
    其中只有 1 条是你想要的（正确答案所在的块）。
    你撒一网捞 3 条上来 —— 这 3 条就是 top-3，也叫「召回的 3 条」。

   Recall@3（读作 recall at 3）= 你捞 3 条，【有没有捞到那条对的】。
      捞到了 → 这次算 1 分；没捞到 → 0 分。
      把所有问题都测一遍，平均分就是 recall@3。

   Recall@1 = 只捞 1 条，而且必须是第 1 条就对。显然比 recall@3 难得多。
""")

# 手算：把这一题的完整排名打出来，标出正确答案在第几位
ranked_all = dense_search(QUESTION, k=len(CHUNKS))
print(f"   来看这一题的真实排名（问题：{QUESTION}）")
print(f"   正确答案在《{GOLD}》，看看它排第几：")
gold_rank = None
for i, (c, s) in enumerate(ranked_all[:8], start=1):
    if c["doc"] == GOLD and gold_rank is None:
        gold_rank = i
        mark = "★ ← 正确答案第 1 次出现"
    elif c["doc"] == GOLD:
        mark = "☆ 同一份文档的另一个块（也算命中）"
    else:
        mark = ""
    print(f"      第 {i} 名 [{s:.3f}] 《{c['doc']}》{c['text'][:24].replace(chr(10),' ')}… {mark}")

print(f"""
   ⇒ 正确答案排在第 {gold_rank} 名。

      这一题的 Recall@1 = {"1" if gold_rank == 1 else "0"}（第 1 条就对了才算 1）
      这一题的 Recall@3 = {"1" if gold_rank <= 3 else "0"}（前 3 条里有它就算 1）

   把所有题目的 Recall@3 加起来平均 = step4 里那张表的 92% / 100%。
   所以那张表读法就是：『捞 3 条，100 次里有 100 次捞到了对的』。
""")

# ==================================================================
# ③ 三种检索方式差在哪
# ==================================================================
sep("③ 三种检索方式：一条路看意思，一条路看字面")

print(f"   同一个问题：{QUESTION}\n")

for label, fn in [("A. 向量（看意思）", dense_search),
                  ("B. BM25（看字面）", bm25_search),
                  ("C. 混合 RRF", hybrid_search)]:
    print(f"   {label}")
    for i, (c, s) in enumerate(fn(QUESTION, 3), start=1):
        print(f"       {i}. [{s:.3f}] 《{c['doc']}》{c['text'][:30].replace(chr(10), ' ')}…")
    print()

print("""
   A 向量（dense）：把文字变成一串数字（512 个），比"方向像不像"
       优点：懂同义词。你说"在家躺了半个月还能接着念吗"，文档写的是"休学"，它知道是一回事。
       缺点：看不懂精确的编号、型号、人名。

   B BM25（sparse）：不转数字，就数"这个词出现了几次"
       优点：精确编号一抓一个准。
       缺点：你换个说法它就瞎了。

   C 混合：两条路各捞 10 条，按下名次加权合起来（RRF）
       工业界默认用这个 —— 因为两条路的翻车场景不重叠。
""")

# 各自的翻车现场
sep("③ 加餐：各自翻车的现场（这是理解它们差别最快的方式）")

print("   【BM25 翻车现场】用户换了个说法，关键词对不上")
q2 = "在家躺了半个月还能接着念吗"
GOLD2 = "学籍异动与休学.md"
print(f"   问题：{q2}")
print(f"   文档里写的是『休学』，这句话一个『休学』都没有。正确答案在《{GOLD2}》")
for label, fn in [("向量", dense_search), ("BM25", bm25_search)]:
    got = fn(q2, 1)[0][0]["doc"]
    print(f"       {label:<6} 第 1 条 → 《{got}》{'  ✓ 对了' if got == GOLD2 else '  ✗ 错了'}")

print("\n   【向量翻车现场】精确编号，几条内容长得几乎一样")
ID_DOCS = [
    {"doc": "学籍2024010101", "text": "学号 2024010101：事假已批准，销假单已归档，审批人王老师。"},
    {"doc": "学籍2024010202", "text": "学号 2024010202：事假已批准，销假单已归档，审批人李老师。"},
    {"doc": "学籍2024010303", "text": "学号 2024010303：病假已批准，销假单已归档，审批人张老师。"},
]
id_vecs = np.array(list(model.passage_embed([d["text"] for d in ID_DOCS])), dtype="float32")
q3 = "2024010101 的审批人是谁"
qv = np.array(list(model.query_embed([q3])), dtype="float32")
sims = id_vecs @ qv[0]
id_bm = BM25Okapi([jieba.lcut(d["text"]) for d in ID_DOCS])
bs = id_bm.get_scores(jieba.lcut(q3))

print(f"   问题：{q3}（三条内容几乎一样，只有编号和审批人不同）")
print(f"   {'':<6}{'2024010101':>13}{'2024010202':>13}{'2024010303':>13}   第一名      领先第二名")
for label, arr in [("向量", sims), ("BM25", bs)]:
    order = np.argsort(arr)[::-1]
    top = ID_DOCS[order[0]]["doc"]
    margin = arr[order[0]] - arr[order[1]]
    ok = "✓" if top == "学籍2024010101" else "✗"
    print(f"   {label:<6}{arr[0]:>13.3f}{arr[1]:>13.3f}{arr[2]:>13.3f}   {top}{ok}   {margin:.3f}")
print("""
   重点看【领先第二名】这一列：这个差值越大，说明第一名越有把握。
   反过来，差值很小 = 检索"自己也拿不准"，这时候就很容易把错的排在前面。
   （BM25 在库特别小的时候会算出负分，这是正常的，只看相对大小就行。）""")

# ==================================================================
# ④ 噪声实验：模型会不会"退回"？
# ==================================================================
sep("④ 你猜的『会退回吗』—— 实测：不会。它会硬着头皮答")

from langchain_core.messages import HumanMessage, SystemMessage   # noqa: E402
from langchain_deepseek import ChatDeepSeek                       # noqa: E402

llm = ChatDeepSeek(model="deepseek-chat", temperature=0)
SYSTEM = ("你是学校学生事务的答疑助手。只能依据【资料】里的内容回答。\n"
          "如果资料里没有答案，必须明确回答『资料里没有提到这个问题』，绝对不要编造。")


def ask(context, question):
    prompt = f"【资料】\n{context}\n\n【用户问题】{question}"
    return llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)]).content


Q = "事假最多能请多久？超过两周要谁批？"
good = "\n\n".join(f"[{c['doc']}]\n{c['text']}" for c, _ in hybrid_search(Q, 3))
noise = "\n\n".join(f"[{c['doc']}]\n{c['text']}"
                    for c, _ in hybrid_search(Q, 2) + [(
                        {"doc": "校园事务办理指南.md",
                         "text": "毕业证书和学位证书在毕业当年统一发放，领取时须本人到场。"}, 0)])
# ★ 假资料必须和真资料【矛盾】才能演示"模型不退回" ——
#   真资料说"事假一般不得超过两周"，这里造一条说"不超过一个月"。
#   换语料时必须同步改这一条，否则矛盾演示失效（真话假话一致就看不出问题）。
fake = good + "\n\n[学生请销假制度补充规定]\n自 2026 年起，事假一律不得超过一个月，无需院系审批。"

print(f"   问题固定为：{Q}\n")
print("   ── 实验 1：给干净的 3 块 ──")
print(f"   {ask(good, Q)[:200]}\n")
print("   ── 实验 2：把 1 块【无关】的（毕业证书）混进去 ──")
print(f"   {ask(noise, Q)[:200]}\n")
print("   ── 实验 3：塞一块【假的】资料（说不超过一个月）──")
print(f"   {ask(fake, Q)[:250]}\n")

print("""
   看清楚这三个结果的差别，这是 RAG 最重要的一条规律：

   实验 1 → 答得又准又全（两周，还补充了"超过三日须院系负责人批准"）

   实验 2 → 还是答对了，但【变简略了】——它丢掉了"超过三日须院系负责人批准"那句补充。
            所以噪声不是"无害"的，它会稀释答案质量，只是不至于答错。

   实验 3 → 最危险。资料里同时有"不超过两周"和"不超过一个月"两条，
            模型【不会退回、不会质疑资料】，它默认你给的资料都是真的，
            于是它把两条都说了出来 —— 用户看到两个数字，彻底懵了。

   ⇒ 所以你猜的"会不会退回去"，答案分两种：
       · 资料里【完全没有】答案 → 会拒绝回答（这个靠提示词能做到）
       · 资料里有、但【是错的/矛盾的】 → 不会拒绝，会照单全收
     这就是「垃圾进，垃圾出」：检索质量决定回答的上限。
     提示词写得再狠，也救不回被污染的上下文。

   ⇒ 所以工业界在"召回"之后还要加一步【重排序 rerank】：
      先粗捞 20 条（便宜、快），再用一个更准的模型给「问题和每一条」单独打分，
      只留最靠谱的 3 条。噪声在这一步被筛掉。
     （实验 3 那种假资料 rerank 也救不了 —— 因为它根本不该进你的库。
       所以真正的防线是【入库时把关】，而不是等它混进上下文再补救。）
""")

print("=" * 68)

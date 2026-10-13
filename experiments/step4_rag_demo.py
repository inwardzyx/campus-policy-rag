# -*- coding: utf-8 -*-
"""
Step 4：RAG 完整链路（真家伙版）

    文档 → 切块(chunk) → 转向量(embedding) → 存向量库 → 用时检索(top-k)

本次用到的真库（都已装好）：
    langchain-text-splitters  递归字符切块器（工业标准切法）
    fastembed + bge-small-zh  真中文 embedding 模型，512 维，本地 ONNX 推理
    faiss-cpu                 Meta 的向量索引（本机会被安全策略拦，已做自动降级）
    rank_bm25 + jieba         稀疏检索（关键词路线），和向量路线做混合
    langgraph + deepseek      检索结果喂给真大模型作答

跑法（务必先关 trace）：
    set LANGSMITH_TRACING=false
    python step4_rag_demo.py

首次运行会加载本地模型（约 1 秒，之后缓存在用户主目录下的 ~/.cache/fastembed）。
"""

import os
import time
from pathlib import Path
from typing import TypedDict

# ⚠️ 这两行必须在 import 之前：告诉下载器走国内镜像，并指定模型缓存位置
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 缓存目录放用户主目录下 —— Windows / Mac / Linux 通用。
# 写死 "D:/..." 的话，别人 clone 下来会在不存在的盘符上找目录。
CACHE_DIR = os.environ.get(
    "FASTEMBED_CACHE_PATH",
    os.path.join(os.path.expanduser("~"), ".cache", "fastembed"))

import sys                                          # 下面要把仓库根目录加进模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import env_compat                                   # noqa: E402  ★ 必须在 import fastembed / langchain 之前
env_compat.ensure_mmh3()                            # 本机 DLL 被策略拦截时的降级方案，见 env_compat.py
env_compat.ensure_uuid_utils()                      # 同上，拦的是 uuid_utils（下面第 41 行的
                                                    #   langchain_text_splitters 会经langchain_core 触发它）
import jieba                                         # noqa: E402
import numpy as np                                  # noqa: E402
from fastembed import TextEmbedding                  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402
from rank_bm25 import BM25Okapi                      # noqa: E402

# FAISS 是"可选加速件"：这台机器的应用程序控制策略有时会拦掉它的 DLL，
# 拦了就退回 numpy —— 几万块以内 numpy 完全够用，真到百万级再想办法装 FAISS。
#
# ★ 这里有个坑（本轮踩到）：光`try: import faiss` 是不够的 ——
#   被应用控制策略拦截的 DLL sometimes 能"导入成功"，
#   但模块里该有的属性是空的，于是 faiss.IndexFlatIP 抛 AttributeError，
#   而 try/except 只包住了 import 那一步，抓不到后面的 AttributeError。
#   ⇒ 必须【用一下】才算探测成功。这跟 pytest 那个 uuid_utils 插件是同一类问题：
#     "能力装在入口早的地方"，不然你根本不知道它什么时候失效。
try:
    import faiss

    _probe = faiss.IndexFlatIP(1)                  # ← 真正试一次：造个 1 维索引
    del _probe
    HAVE_FAISS = True
except Exception as e:                # ImportError / OSError(DLL 被拦) / AttributeError 都算
    faiss = None
    HAVE_FAISS = False
    FAISS_ERR = f"{type(e).__name__}: {e}"

jieba.setLogLevel(60)                           # 关掉 jieba 的加载日志

DOCS_DIR = Path(__file__).parent / "step4_docs"

# ==================================================================
# 知识库：6 份文档（脚本自动写到 step4_docs/，你可以打开看）
# ★ 2026-10-09 从 10 份虚构电商文档改成 6 份校园政策文档（对齐主服务 docs/kb 的
#   真实校规语料）。为什么是 6 份而不是硬凑 10 份：手头只有2 篇真实校规，
#   剩下的主题（奖学金办法、综合测评）真实语料里没有，硬写就是造假 ——
#   而"虚构语料"恰恰是这个项目最早被批评的点，不该在教学脚本里复现。
#   6 份正好与 rag_concepts_demo.py 对齐。
# 故意写得长一点 —— 文档太短的话，"切多大一块"这件事就没差别了
# ==================================================================
CORPUS = {
    "学生请销假制度.md": """# 学生请销假制度
本校实行请假审批与销假核销制度，学生因故离校必须履行审批手续，未经批准擅自离校的按纪律处分处理。
请假分为事假、病假、公假三类。事假指因家庭事务等个人原因需要离校；病假指因身体不适需要就医或休息；公假指因参加学校组织的活动而出具的证明。

事假须由家长与班主任联系并说明情况后，由班主任登记在册并视情况审核，事假一般不得超过两周。
连续两周以上的请假，需由院系负责人批准，并同时通知家长确认情况。
病假须提供校医院或二级以上医院的诊断证明，经班主任审核后报院系批准，证明材料由班主任留存。

销假须在返校后三个工作日内提交销假单，由班主任核销销假单并更新考勤记录。
逾期未提交销假单的，按旷课处理；连续七日未销假的，学院可按有关规定处理。
销假单是考勤核销的唯一凭证，学生本人须妥善保管，遗失后可申请补办。

请假期间发生的考试，由教务科另行安排，任课教师不得以缺勤为由直接判定成绩不合格。
因病因公请假期间的课程考核，学生须在销假后一周内联系任课教师办理缓考手续。
缓考成绩与正常考核等效，有效期为该学期末，学生不得无故放弃缓考。""",

    "请假审批流程.md": """# 请假审批流程
请假审批按请假时长分级，一日以内由班主任审批，三日以内由班主任审核后报院系备案，超过三日须院系负责人批准。
审批意见一般在收到申请后一个工作日内作出，申请人可通过教务系统查看当前审批节点。
审批通过后方可离校，未获批准而擅自离校的，除扣减相应考勤分外还将通报批评。

请假申请须在离校前提交，确因突发情况无法提前申请的，应在离校后二十四小时内补办并说明原因。
补办申请需要额外提供佐证材料，由班主任核实后报院系处理。
学院不承担因未履行请假手续而产生的任何费用，包括但不限于交通、医疗与住宿费用。

审批流程中的学生事务办公室负责受理和分发，工作日八时三十分至十七时开放。
院系负责人审批环节在每周五下午集中处理，节假日前一工作日的申请原则上当日完成审批。
超出上述工作时间的紧急申请，可拨打院系值班电话说明情况，由值班人员代为登记。

审批结果通过教务系统推送，学生须在离校前确认审批状态为"已批准"。
审批状态显示"审批中"即表示手续尚未完成，此时离校仍属擅自离校。
教务系统每学期导出一次请假台账，作为奖助学金评定和年度考核的依据之一。""",

    "违纪处分种类.md": """# 违纪处分种类
学生对违反法律法规和学校规章的行为，视情节轻重给予相应处分。处分种类由轻到重依次为警告、严重警告、记过、留校察看、开除学籍。
警告适用于违反一般管理规定且情节轻微的行为；严重警告适用于情节较重并造成不良影响的行为。
记过适用于情节严重的违纪行为，留校察看适用于屡次违纪或情节恶劣的行为，察看期为一年。

处分作出前，学生有权陈述和申辩。学院应当在调查结束后将拟处分意见告知本人，并听取其陈述申辩。
学生确有特殊情况的，学院应当酌情从轻或者减轻处分；处分明显不当的，应当及时予以撤销。
处分决定应当向本人宣布，并通报所在班级，由班主任将处分决定通知学生家长。

处分期限与撤销：警告与严重警告一般不影响学生评优评先资格，记过及以上处分解锁期限为一年。
留校察看期间表现良好者，察看期满后自动解除察看；察看期内再次违纪的，予以开除学籍。
处分决定撤销后，学生已取得的处分记录同步删除，不再影响后续评奖评优。

处分材料由学院归档保存，保存期限为五年。学生对处分决定不服的，可以在收到决定之日起十个工作日内向学生申诉委员会提出书面申诉。
申诉委员会应当在受理后十五个工作日内作出复查结论并告知本人。""",

    "学籍异动与休学.md": """# 学籍异动与休学
学生因健康、家庭事务或其他原因需要暂停学业的，可以申请办理休学。休学期限一般为一年，期满可申请续休，累计休学不超过两年。
因健康原因休学的，须提供二级以上医院出具的长期治疗证明；因其他原因休学的，须提交家长书面同意与所在系部意见。

休学期间保留学籍，不参加课程考核。复学申请应当在休学期满前一个月提交，由原就读系部审核并报教务处备案。
因健康原因复学的，须提供康复证明并通过校医院体检；因其他原因复学的，须重新办理入学注册手续。
休学期间不得参加学校组织的各类考试与学业评估，擅自参加的成绩不予认定。

学籍异动包括转专业、转系、休学、复学、退学与保留学籍六类。转专业每年集中受理一次，申请条件与名额由各系部公布。
退学学生须提交书面申请，经学院审核同意后办理退学手续，退学后两年内申请重新入学的，按当年的招生政策执行。
保留学籍仅适用于休学与复学期间，期限为两年，两年内未办理复学手续的，学校将注销其学籍。

保留学籍期间不计入在校学习年限，但学费按学校规定收取。确因家庭经济困难无法缴纳学费的，可按规定申请缓缴或减免。
办理学籍异动手续一般需要三个工作日，学生须携带本人学生证、身份证及相关证明材料到教务处办理。
学籍异动信息将同步更新至学籍库和毕业证书数据，办理后应及时核对自己的学籍信息。""",

    "校园事务办理指南.md": """# 校园事务办理指南
学生事务办理地点为行政楼一站式服务大厅，服务时间为工作日上午八时三十分至十二时、下午十四时至十七时。
办理业务前可通过教务系统或服务大厅门口的公告屏查询所需材料清单，避免因材料不全多次往返。
材料清单中涉及复印件的，须为原件的清晰复印件，不能使用传真件或复印件的复印件。

证明类材料由开具部门负责核验真伪，学生本人不得代他人开具。委托他人办理的，须出具委托书并附双方身份证件。
办理结果可选择现场领取或邮寄，邮寄费用由学生承担，邮寄件在受理后三个工作日内寄出。
大厅提供免费咨询窗口，复杂业务可先咨询再办理，避免填错表格。

毕业证书和学位证书在毕业当年统一发放，领取时须本人到场并携带身份证与学生证。
因故不能本人领取的，可委托他人代领，代领人须携带本人委托书、双方身份证件及毕业生本人签字的说明。
证书遗失后可申请补发，补发证书注明"补发"字样，与原证书具有同等效力。
用人单位在招聘时需要核实学历的，可通过学信网查询，毕业生本人无需到学校开具证明。

学生公寓的报修、门禁卡补办、钥匙更换等日常事务，由后勤服务中心受理，处理时限为三个工作日。
涉及校园卡余额充值与消费查询的，可自助在食堂门口的终端机上办理，也可以到服务台人工处理。
校园卡丢失后应立即挂失，挂失后原卡不再具备支付功能，补办新卡需本人携带身份证件办理。""",

    "学生申诉与联系方式.md": """# 学生申诉与联系方式
学生事务服务窗口位于行政楼一层，工作日八时三十分至十七时开放。
学生申诉委员会办公室在行政楼二层，接收学生书面申诉材料。

对处分决定不服的，可以在收到决定之日起十个工作日内向学生申诉委员会提出书面申诉。
申诉委员会应当在受理后十五个工作日内作出复查结论并告知本人。
申诉期间不影响学生的正常学习与生活安排，但处分记录仍然保留至复查结束。

线上事项可通过教务系统提交，提交后会生成受理编号，可在系统内查询办理进度。
材料不全时，系统会列明缺哪一项并保留已提交内容，补齐后无需重新填写。
超过十五个工作日未收到结论的，可向教务处反映，由教务处督促申诉委员会出具说明。""",
}


def sep(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


DOCS_DIR.mkdir(exist_ok=True)
for name, text in CORPUS.items():
    (DOCS_DIR / name).write_text(text, encoding="utf-8")   # ⚠️ Windows 必须写 encoding

# ==================================================================
# ① 问题有多大
# ==================================================================
sep("① 为什么必须做 RAG：全文塞进去要多少钱")
all_text = "\n\n".join(CORPUS.values())
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print(f"   现在：{len(CORPUS)} 份文档，共 {len(all_text)} 字 ≈ {int(len(all_text) * 0.7)} tokens")
print(f"   真实项目：300 份 → 约 30 倍 → 一次提问就吃掉几万 token")
print(f"   而且多轮对话【每轮都要重发一遍历史】，成本是线性叠加的。")
print()
print("   ⇒ RAG 的全部动机就一句：只把相关的那几段塞进上下文。")

# ==================================================================
# ② 切块：手写硬切 vs 工业切块器
# ==================================================================
sep("② 切块：硬切 vs RecursiveCharacterTextSplitter")


def chunk_hard(text, size=80):
    """方案 A：不看内容，每 80 字一刀（会切断句子）"""
    return [text[i:i + size] for i in range(0, len(text), size)]


def chunk_recursive(text, size=300, overlap=50):
    """方案 B：递归字符切块器 —— 先按段落切，切不开再按句子，最后才按字硬切"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        # separators 的顺序就是"退让顺序"：先试空行，再换行，再句号……
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        length_function=len,
    )
    return splitter.split_text(text)


sample = CORPUS["学生请销假制度.md"]
print(f"   同一份《学生请销假制度.md》（{len(sample)} 字）")
print(f"\n   硬切 80 字 → {len(chunk_hard(sample))} 块，看其中两块：")
for c in chunk_hard(sample)[:2]:
    print(f"       「…{c[-40:]}」")
print(f"\n   递归切块 300 字/重叠 50 → {len(chunk_recursive(sample))} 块，看其中两块：")
for c in chunk_recursive(sample)[:2]:
    print(f"       「{c[:52]}…」")
print()
print("   硬切的问题：句子被拦腰截断，比如『事假一般不得超过两周；连续两周以上的』+『请假，需由院系负责人批准』。")
print("   单独召回任何一半都不完整 → 模型只能瞎猜。")
print("   递归切块器会优先在句号、换行处下刀，保住语义完整。")


def build_chunks(method, size=300, overlap=50):
    """把整个知识库切成带来源的块（每个块记住自己来自哪份文档）"""
    out = []
    for name, text in CORPUS.items():
        pieces = chunk_hard(text) if method == "hard" else chunk_recursive(text, size, overlap)
        for p in pieces:
            out.append({"doc": name, "text": p})
    return out


# ==================================================================
# ③ 向量化 + 向量库
# ==================================================================
sep("③ 转向量：bge-small-zh-v1.5（真中文 embedding 模型）")

t0 = time.time()
model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=CACHE_DIR)
print(f"   模型加载：{time.time() - t0:.2f} 秒（已缓存，不联网）")

demo_vecs = np.array(list(model.passage_embed(["事假最多能请多久"])), dtype="float32")
print(f"   向量形状：{demo_vecs.shape}  ← 512 维的 float32 数组")
print(f"   范数 = {np.linalg.norm(demo_vecs[0]):.4f}  ← 已归一化，点积就等于余弦相似度")
print()
print("   归一化是个关键技巧：算相似度时只要做一次点积(a @ b)，")
print("   不用再除以两个模长 —— 向量库正是靠这个提速。")


def _topk(scores, k):
    """取分数最大的 k 个下标，并按分数从高到低排好"""
    k = min(k, len(scores))
    idx = np.argpartition(-scores, k - 1)[:k]        # 只分区不排序，比 argsort 快
    return idx[np.argsort(-scores[idx])]             # 对这 k 个再排一次序


class VectorStore:
    """
    向量库：把每块的向量堆成一个矩阵，检索时做一次矩阵×向量。

    有 FAISS 就用 FAISS（IndexFlatIP = 内积索引）；
    没有就用 numpy 的矩阵乘法 —— 因为向量已归一化，点积就是余弦相似度。
    """

    def __init__(self, chunks):
        self.chunks = chunks
        t = time.time()
        # passage_embed：给"文档块"用的编码（bge 系列对查询和文档用不同前缀）
        self.vectors = np.array(list(model.passage_embed([c["text"] for c in chunks])),
                                dtype="float32")
        self.dim = self.vectors.shape[1]
        if HAVE_FAISS:
            self.index = faiss.IndexFlatIP(self.dim)   # IP = Inner Product 内积
            self.index.add(self.vectors)               # 一次性灌进索引
        self.build_time = time.time() - t

    def search(self, query, k=3):
        q = np.array(list(model.query_embed([query])), dtype="float32")
        if HAVE_FAISS:
            scores, ids = self.index.search(q, k)      # 返回 (分数二维数组, 下标二维数组)
            return [(self.chunks[i], float(s)) for s, i in zip(scores[0], ids[0]) if i != -1]
        scores = self.vectors @ q[0]                   # ← 一次矩阵乘法，全库相似度都出来了
        return [(self.chunks[i], float(scores[i])) for i in _topk(scores, k)]


CHUNKS = build_chunks("recursive")
backend = "FAISS IndexFlatIP" if HAVE_FAISS else "numpy 矩阵乘法"
print(f"\n   检索后端：{backend}")
if not HAVE_FAISS:
    print(f"      原因：{FAISS_ERR}")
    print(f"      几万块以内 numpy 够快；真到百万级再想办法装 FAISS。")

vs = VectorStore(CHUNKS)
print(f"   建库：{len(CHUNKS)} 块 → 维度 {vs.dim}，耗时 {vs.build_time:.2f} 秒")

q = "事假最多能请多久？超过两周要谁批？"
print(f"\n   向量检索「{q}」：")
for c, s in vs.search(q, k=3):
    print(f"       [{s:.3f}] 《{c['doc']}》{c['text'][:34].replace(chr(10), ' ')}…")

hit = sum(len(c["text"]) for c, _ in vs.search(q, k=3))
print(f"\n   只塞这 3 块进上下文：{hit} 字 vs 全文 {len(all_text)} 字")
print(f"   ⇒ 省掉 {100 - int(hit / len(all_text) * 100)}% 的 token。300 份文档时差距会放大几十倍。")

# ==================================================================
# ④ 另一条路线：BM25 关键词检索 + 混合检索
# ==================================================================
sep("④ 另一条路线：BM25 关键词检索，以及两者混合")


class BM25Store:
    """BM25：老牌关键词检索。不需要 embedding，靠词频和逆文档频率打分"""

    def __init__(self, chunks):
        self.chunks = chunks
        # jieba 分词：中文必须先切成词，BM25 才知道"请假"是一个词
        self.tokens = [jieba.lcut(c["text"]) for c in chunks]
        self.bm25 = BM25Okapi(self.tokens)

    def search(self, query, k=3):
        scores = self.bm25.get_scores(jieba.lcut(query))
        # argsort 是从小到大，[::-1] 翻过来就是从大到小
        return [(self.chunks[i], float(scores[i])) for i in np.argsort(scores)[::-1][:k]]


bs = BM25Store(CHUNKS)
print(f"   BM25 检索同一个问题：")
for c, s in bs.search(q, k=3):
    print(f"       [{s:.2f}] 《{c['doc']}》{c['text'][:34].replace(chr(10), ' ')}…")

print("""
   两条路线的脾气不一样：
     向量(dense)  → 懂语义同义词，问「家里有事得回去」也能找到讲请假的文档
                    但它看不懂精确型号、编号、专有名词
     BM25(sparse) → 关键词精确匹配，「2024010101」这种编号它一抓一个准
                    但用户换个说法它就瞎了

   所以工业界几乎都用【混合检索】：两条路线各取 top-k，再融合排序。""")


def hybrid_search(store_v, store_b, query, k=3, rrf_k=60):
    """RRF 融合：不看原始分数（两条路线分数不可比），只看排名"""
    fused = {}
    for rank, (c, _) in enumerate(store_v.search(query, k=10)):
        fused.setdefault(c["text"], {"chunk": c, "score": 0.0})
        fused[c["text"]]["score"] += 1.0 / (rrf_k + rank + 1)   # 名次越靠前加分越多
    for rank, (c, _) in enumerate(store_b.search(query, k=10)):
        fused.setdefault(c["text"], {"chunk": c, "score": 0.0})
        fused[c["text"]]["score"] += 1.0 / (rrf_k + rank + 1)
    ranked = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
    return [(x["chunk"], x["score"]) for x in ranked[:k]]


print(f"   混合检索（RRF）：")
for c, s in hybrid_search(vs, bs, q, k=3):
    print(f"       [{s:.4f}] 《{c['doc']}》{c['text'][:34].replace(chr(10), ' ')}…")

# ==================================================================
# ⑤ 评测：这才是能写进简历的东西
# ==================================================================

EVAL = [
    # —— 基础题：关键词和文档高度重合 ——
    ("事假最多能请多久", "学生请销假制度.md"),
    ("请假超过三天要谁批准", "请假审批流程.md"),
    ("警告和严重警告有什么区别", "违纪处分种类.md"),
    ("休学期限是多久", "学籍异动与休学.md"),
    ("毕业证书丢了怎么补发", "校园事务办理指南.md"),
    ("销假单要在什么时候交", "学生请销假制度.md"),
    ("处分决定不服可以申诉吗", "违纪处分种类.md"),
    ("复学需要什么材料", "学籍异动与休学.md"),
    ("校园卡丢了怎么办", "校园事务办理指南.md"),
    # —— 换说法：用户不会照着文档说话 ——
    ("家里有事得回去一趟", "学生请销假制度.md"),
    ("病了想去看医生", "学生请销假制度.md"),
    ("处分会不会影响我拿奖学金", "违纪处分种类.md"),
    ("休学期间还能参加考试吗", "学籍异动与休学.md"),
    ("我想换个专业", "学籍异动与休学.md"),
    ("请假条交上去一直没批", "请假审批流程.md"),
    ("快递室能寄东西吗", "校园事务办理指南.md"),
    # —— 刁钻题：答案埋在文档的第二段、第三段 ——
    ("超过七天不销假会怎么处理", "学生请销假制度.md"),
    ("缺课了考试怎么算", "学生请销假制度.md"),
    ("审批状态一直显示审批中", "请假审批流程.md"),
    ("周末离校要不要申请", "请假审批流程.md"),
    ("留校察看期间表现良好会怎样", "违纪处分种类.md"),
    ("处分材料要保存多久", "违纪处分种类.md"),
    ("不服处分要到哪里申诉", "学生申诉与联系方式.md"),
    ("申诉要交什么材料", "学生申诉与联系方式.md"),
    ("因病休学复学要体检吗", "学籍异动与休学.md"),
    ("保留学籍期间要交学费吗", "学籍异动与休学.md"),
    ("代领毕业证书要带什么", "校园事务办理指南.md"),
    ("办理业务在哪里", "校园事务办理指南.md"),
]

sep(f"⑤ 召回评测：{len(EVAL)} 道题的标准考卷，三条路线 PK")


def evaluate(search_fn, k=3):
    """考卷打分：top1 命中率 + top3 命中率"""
    hit1 = hitk = 0
    wrong = []
    for question, gold in EVAL:
        docs = [c["doc"] for c, _ in search_fn(question, k=k)]
        if docs and docs[0] == gold:
            hit1 += 1
        if gold in docs:
            hitk += 1
        else:
            wrong.append((question, gold, docs[0] if docs else "-"))
    return hit1 / len(EVAL), hitk / len(EVAL), wrong


rows = [
    ("BM25 关键词", lambda qq, k: bs.search(qq, k)),
    ("向量 dense", lambda qq, k: vs.search(qq, k)),
    ("混合 RRF", lambda qq, k: hybrid_search(vs, bs, qq, k)),
]

print(f"{'检索方式':<14}{'第1条就命中':<14}{'前3条里命中':<14}")
print("-" * 42)
results = {}
for label, fn in rows:
    r1, r3, wrong = evaluate(fn)
    results[label] = (r1, r3, wrong)
    print(f"{label:<14}{r1:>8.0%}      {r3:>10.0%}")

print("\n   ↑ 这组数字就是简历/面试上那句话的来源：")
print(f"     『我用 {len(EVAL)} 条考卷做召回评测，把 recall@1 从 "
      f"{results['BM25 关键词'][0]:.0%} 提到 {results['混合 RRF'][0]:.0%}』")
print("     没有这张表，你只能说『我做了个 RAG』—— 那是人人都写的一句废话。")

print("\n   错题分析（混合检索没在 top3 找到的）：")
if results["混合 RRF"][2]:
    for question, gold, got in results["混合 RRF"][2]:
        print(f"       ✗ {question[:22]:<24} 期望《{gold}》 实际《{got}》")
else:
    print("       （top3 全对）")
    # ★ 下面这段里的数字是本机实测（2026-10-09，校园语料6 份文档），
    #   换语料后要重跑这个脚本再改 —— 别照抄上一版的数字。
    print(f"""
   ⚠️ 注意这个现象：库太小（只有 {len(CHUNKS)} 块）时，top3 必中，recall@3 会【饱和】。
   这不是你的检索做得好，是考卷失去区分度了。真实项目里：
       · 库变大（几百上千块），recall@3 才有意义
       · 或者改用更严的指标：recall@1（本例 {results['混合 RRF'][0]:.0%}）/ MRR
       · 一句话：指标要用【能拉开差距】的那一个，别挑好看的报。""")

print("\n   再测一个变量：切块大小对召回的影响（混合检索）")
print(f"   {'块大小':<8}{'重叠':<8}{'块数':<8}{'recall@1':<10}{'recall@3':<10}")
for size in (80, 120, 200, 300, 400):
    ch = build_chunks("recursive", size=size, overlap=size // 6)
    vs2 = VectorStore(ch)
    bs2 = BM25Store(ch)
    r1, r3, _ = evaluate(lambda qq, k=3: hybrid_search(vs2, bs2, qq, k))   # 立即调用，不受迟绑定影响
    print(f"   {size:<8}{size // 6:<8}{len(ch):<8}{r1:>6.0%}    {r3:>6.0%}")
print("\n   ⇒ 块太小语义被切断，块太大噪声变多。挑『召回涨不动』的那个拐点。")
print("      本例 recall@3 已经饱和，所以要看 recall@1 那一列的差别。")

# ==================================================================
# ⑥ 接进 LangGraph
# ==================================================================
sep("⑥ 接进 LangGraph：检索节点 → 回答节点")

from langchain_core.messages import HumanMessage, SystemMessage   # noqa: E402
from dotenv import load_dotenv
load_dotenv()
from langchain_deepseek import ChatDeepSeek                       # noqa: E402
from langgraph.graph import END, START, StateGraph                # noqa: E402


class State(TypedDict):
    question: str
    context: str
    answer: str


def retrieve(state):
    """节点 1：检索（纯本地，不花钱）"""
    hits = hybrid_search(vs, bs, state["question"], k=3)
    ctx = "\n\n".join(f"[{c['doc']}] {c['text']}" for c, _ in hits)
    print(f"   [retrieve] 召回 {len(hits)} 块 / {len(ctx)} 字（全文是 {len(all_text)} 字）")
    return {"context": ctx}


SYSTEM = (
    "你是学校学生事务的答疑助手。只能依据【资料】里的内容回答。\n"
    "如果资料里没有答案，必须明确回答『资料里没有提到这个问题』，绝对不要自己编造。\n"
    "回答时在末尾注明信息来源的文档名。"
)

llm = ChatDeepSeek(model="deepseek-chat", temperature=0)


def answer(state):
    """节点 2：资料 + 问题一起交给模型"""
    prompt = f"【资料】\n{state['context']}\n\n【用户问题】{state['question']}"
    resp = llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)])
    return {"answer": resp.content}


g = StateGraph(State)
g.add_node("retrieve", retrieve)
g.add_node("answer", answer)
g.add_edge(START, "retrieve")
g.add_edge("retrieve", "answer")
g.add_edge("answer", END)
app = g.compile()

for qq in ["事假最多能请多久？超过两周要谁批？",
           "处分要保留多久？我想申诉",
           "学校附近有哪些奶茶店？"]:          # ← 故意问知识库里没有的
    print(f"\n   用户问：{qq}")
    try:
        out = app.invoke({"question": qq, "context": "", "answer": ""})
        print(f"   回答：{out['answer'][:200]}")
    except Exception as e:
        print(f"   [调用失败 {type(e).__name__}] {str(e)[:80]}（网络/额度问题，检索部分不受影响）")

print("""
   第三问是故意的：资料里根本没有「奶茶店」这种校外信息。
   看它有没有老实说「没有提到」—— 这是 RAG 最重要的防线。
   如果它开始编，说明 SYSTEM 提示词的约束还不够硬，要继续加码。""")

# ==================================================================
# ⑦ 坑
# ==================================================================
sep("⑦ 这四步里最容易踩的坑")
print("""
1. 【切块】不是越小越好。中文经验值 200~500 字/块，重叠 10%~20%。
   重叠是为了防止答案正好横跨两个块。

2. 【top-k】不是越大越好。k 大 → 召回率高，但噪声和 token 也多。
   正确做法：用考卷测 k=1/3/5/10，挑「召回率涨不动」的拐点。

3. 【召不回就一定编】。所以 SYSTEM 必须写死「没有就说没有」，
   而且这句话要用考卷验证过（第 ⑥ 段第三问）。

4. 【换 embedding = 全库重算】。所以原文一定要留着，向量只当缓存。

5. 【Windows 读文件永远写 encoding="utf-8"】，不写会用 GBK → UnicodeDecodeError。

6. 【混合检索的分数不能直接相加】：向量分是 0~1 的余弦，BM25 分能到十几。
   所以要用 RRF（只看名次不看分数），这就是 1/(60+rank) 的由来。

7. 【评测集要先于优化存在】。没有考卷就去调参数，等于闭着眼睛调，
   你根本不知道改了是变好还是变坏。

8. 【循环图 + 开着 LANGSMITH_TRACING = 卡死】。跑练习脚本前先 set LANGSMITH_TRACING=false。
""")

print("=" * 68)
print(f"知识库文件：{DOCS_DIR}")
print(f"模型缓存：{CACHE_DIR}（91MB，别删，删了要重下 4 分钟）")
print("=" * 68)

# -*- coding: utf-8 -*-
"""
Step 5：入库把关（Ingest Guard）+ 重排序（Rerank）

上一章留下两个问题：
    Q1: 库里混进【假资料/旧资料】怎么办？→ 答案在【入库时把关】，不是事后补救
    Q2: 召回的 top-k 里有噪声怎么办？    → 先粗捞 20 条，再精排留 3 条（rerank）

一句话比喻：
    RAG 的检索像【图书馆借书】。
    入库把关 = 收书时的质检员（盗版书、重复书、过期书，一律不许上架）
    Rerank   = 管理员先搬 20 本给你，再一本本翻，只留最靠谱的 3 本

跑法（务必先关 trace，否则每次 LLM 调用都上报）：
    set LANGSMITH_TRACING=false
    python step5_ingest_guard_demo.py

本文件会真调用 DeepSeek 若干次（rerank 打分 + 最终作答），成本不到一分钱。
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import TypedDict

# ⚠️ 必须在 import 之前：走国内镜像 + 指定模型缓存
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
import numpy as np                              # noqa: E402
from fastembed import TextEmbedding             # noqa: E402
from dotenv import load_dotenv
load_dotenv()
from langchain_deepseek import ChatDeepSeek     # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ==================================================================
model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=CACHE_DIR)
llm = ChatDeepSeek(model="deepseek-chat", temperature=0)   # 自动读环境变量里的 key


def sep(title):
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


# ==================================================================
# ① 一批"脏数据"：真实入库时会遇到的 6 类问题
#    每一行字段说明：
#      text    块内容
#      doc     来自哪份文档
#      version 这块的更新日期（用来判断新旧）
#      source  来源（真实项目里必须有，不然没法追溯）
# ==================================================================
RAW_CHUNKS = [
    # ---- 正常的好块 ----
    dict(doc="学生请销假制度.md", version="2026-03-01", source="学生手册",
         text="事假须由家长与班主任联系并说明情况后，由班主任登记在册并视情况审核，事假一般不得超过两周。"),
    dict(doc="学生请销假制度.md", version="2026-03-01", source="学生手册",
         text="销假须在返校后三个工作日内提交销假单，由班主任核销并更新考勤记录，逾期未销假按旷课处理。"),
    dict(doc="请假审批流程.md", version="2026-02-10", source="教务处",
         text="请假一日以内由班主任审批，超过三日须院系负责人批准，并同时通知家长确认情况。"),
    dict(doc="违纪处分种类.md", version="2026-01-20", source="学生手册",
         text="处分种类由轻到重依次为警告、严重警告、记过、留校察看、开除学籍，处分前学生有权陈述和申辩。"),
    dict(doc="学籍异动与休学.md", version="2026-02-01", source="教务处",
         text="休学期限一般为一年，期满可申请续休，累计休学不超过两年，保留学籍期间不参加课程考核。"),
    dict(doc="校园事务办理指南.md", version="2026-01-05", source="学生事务办公室",
         text="学生事务办理地点为行政楼一站式服务大厅，服务时间为工作日上午八时三十分至十二时、下午十四时至十七时。"),

    # ---- 脏块 1：切块碎屑（太短，没有语义）----
    # ★ 为什么必须人工造：实测真实校规最短 22 字，<15 字的块数为 0。
    #   直接搬docs/kb/ 的真实语料，这个关卡会打出"拦下 0 块"，演示直接消失。
    #   仓库里 tests/test_guard.py 的注释也记着这个盲区。
    dict(doc="学生请销假制度.md", version="2026-03-01", source="学生手册",
         text="见附件。"),
    dict(doc="请假审批流程.md", version="2026-02-10", source="教务处",
         text="（见下表）"),

    # ---- 脏块 2：完全重复（复制粘贴了两遍）----
    dict(doc="学生请销假制度.md", version="2026-03-01", source="学生手册",
         text="事假须由家长与班主任联系并说明情况后，由班主任登记在册并视情况审核，事假一般不得超过两周。"),

    # ---- 脏块 3：近似重复（改了几个字，意思一样）----
    #★ 这一对是关卡 3 的命门：第 1 条和这条用词高度重合但不完全相同，
    #   相似度落在 DUP_THRESHOLD 之上。改写时【不要】把这句改得差别太大，
    #   否则余弦掉到阈值以下，关卡 3 就拦不住了（实测值见下方注释与打印）。
    dict(doc="学生请销假制度.md", version="2026-03-01", source="班主任话术库",
         text="事假需要家长与班主任联系说明情况，班主任登记在册后视情况审核，事假原则上不超过两周。"),

    # ---- 脏块 4：过期版本（和上面的新规定冲突，数字是错的）----
    dict(doc="学生请销假制度.md", version="2024-05-01", source="旧版学生手册（已下线）",
         text="事假须经家长与班主任联系说明后由班主任审核，事假一般不得超过一个月。"),

    # ---- 脏块 5：夹带个人隐私（PII）----
    dict(doc="校园事务办理指南.md", version="2026-02-01", source="学生事务工单导出",
         text="学生张明，学号 2024010101，手机号 13812345678，身份证号 440301199001011234，请妥善保管。"),

    # ---- 脏块 6：来源不明（不知道哪来的，出问题没法追溯）----
    dict(doc="未知.md", version="", source="",
         text="内部消息：下个月起所有请假申请都要家长陪签，先别通知学生。"),
]


def norm(text):
    """归一化：去掉空白和标点，用来做精确去重（'请假两周' 和 '请假  两周' 视为相同）"""
    return re.sub(r"[\s\W_]+", "", text)


# ==================================================================
sep("① 入库把关：五道关卡，脏数据一律不许上架")
# ==================================================================
print("""
真实项目里，知识库不是一次建好就完了 —— 它是【持续有人往里塞东西】的：
    官网爬来的、系统导出的、同事手动贴的、旧系统迁移过来的……
所以必须有个"质检员"站在入库口，逐块检查。下面是五道关卡：
""")

MIN_LEN = 15          # 关卡1：少于 15 个字 → 判定为碎屑
# 关卡3 的阈值怎么定？实测出来的（不是拍脑袋）：
#   意思相同、措辞不同的两块  → 相似度 0.968   ← 要拦住
#   内容不同的两块            → 相似度 0.785   ← 不能误伤
# 所以阈值取中间：0.90。真实项目里这个值必须用你自己的语料测一遍再定。
# ★ 2026-10-09 换校园语料后重测过（原电商语料是 0.9274 / 0.7848）。
#   换语料必须重测—— 阈值跟着语料走，照抄上一版的数字就是自欺。
DUP_THRESHOLD = 0.90
PII_PATTERNS = [      # 关卡4：个人隐私
    (r"1[3-9]\d{9}", "手机号"),
    (r"\d{17}[\dXx]", "身份证号"),
    (r"\d{16,19}", "银行卡号"),
]


class Chunk(TypedDict):
    text: str
    doc: str
    version: str
    source: str


def guard_chunks(chunks):
    """入库质检：返回 (放行的块, 被拦下的块及原因)"""
    kept, rejected = [], []

    # ---------- 关卡 1：太短的碎屑 ----------
    for c in chunks:
        if len(c["text"].strip()) < MIN_LEN:
            rejected.append((c, f"关卡1 内容太短（{len(c['text'].strip())} 字），多半是切块碎屑"))
        else:
            kept.append(c)
    print(f"   关卡1 太短碎屑  ：{len(chunks)} 块 → 拦下 {len(chunks) - len(kept)} 块")

    # ---------- 关卡 2：完全重复（哈希精确比对）----------
    before = len(kept)
    seen, stage2 = set(), []
    for c in kept:
        h = hashlib.md5(norm(c["text"]).encode()).hexdigest()
        if h in seen:
            rejected.append((c, "关卡2 与已有块完全重复（内容一模一样）"))
        else:
            seen.add(h)
            stage2.append(c)
    kept = stage2
    print(f"   关卡2 完全重复  ：{before} 块 → 拦下 {before - len(kept)} 块")

    # ---------- 关卡 3：近似重复（向量相似度比对）----------
    before = len(kept)
    if kept:
        vecs = np.array(list(model.passage_embed([c["text"] for c in kept])), dtype="float32")
        stage3 = []
        for i, c in enumerate(kept):
            dup_of, sim = None, 0.0
            for kept_c, j in stage3:
                # 余弦相似度：两个向量点乘（bge 的向量已归一化，所以点乘就是余弦）
                s = float(vecs[i] @ vecs[j])
                if s > DUP_THRESHOLD and s > sim:
                    dup_of, sim = kept_c, s
            if dup_of is not None:
                rejected.append((c, f"关卡3 近似重复（相似度 {sim:.3f} > {DUP_THRESHOLD}，"
                                    f"与《{dup_of['doc']}》那块是一个意思）"))
            else:
                stage3.append((c, i))
        kept = [c for c, _ in stage3]
    print(f"   关卡3 近似重复  ：{before} 块 → 拦下 {before - len(kept)} 块")
    print(f"                     （阈值 {DUP_THRESHOLD}：实测同义块 0.968、不同义块 0.785）")

    # ---------- 关卡 4：个人隐私（PII）----------
    before = len(kept)
    stage4 = []
    for c in kept:
        hit = None
        for pat, name in PII_PATTERNS:
            if re.search(pat, c["text"]):
                hit = name
                break
        if hit:
            rejected.append((c, f"关卡4 含个人隐私（{hit}），不许入库"))
        else:
            stage4.append(c)
    kept = stage4
    print(f"   关卡4 个人隐私  ：{before} 块 → 拦下 {before - len(kept)} 块")

    # ---------- 关卡 5：来源不明 ----------
    before = len(kept)
    stage5 = []
    for c in kept:
        if not c["source"] or not c["version"]:
            rejected.append((c, "关卡5 缺少来源/日期，出问题无法追溯"))
        else:
            stage5.append(c)
    kept = stage5
    print(f"   关卡5 来源不明  ：{before} 块 → 拦下 {before - len(kept)} 块")

    return kept, rejected


kept_chunks, rejected_chunks = guard_chunks(RAW_CHUNKS)
print(f"\n   最终：{len(RAW_CHUNKS)} 块 → 放行 {len(kept_chunks)} 块，拦下 {len(rejected_chunks)} 块")
print("\n   被拦下的（每一条都有具体原因 —— 报错信息要能指导下一步动作）：")
for c, reason in rejected_chunks:
    print(f"     ✗ 《{c['doc']}》{c['text'][:30]}…")
    print(f"        ↳ {reason}")


# ==================================================================
# 隐藏关卡：过期版本冲突（同一份文档的新旧两个版本都在库里）
# 这一关不能靠单块判断，要【按文档分组、比日期】
# ==================================================================
print("\n   【补充关卡：版本冲突】同一份文档如果有新旧两版，只留新的：")
old_one = dict(doc="学生请销假制度.md", version="2024-05-01", source="旧版学生手册（已下线）",
               text="事假须经家长与班主任联系说明后由班主任审核，事假一般不得超过一个月。")
new_one = dict(doc="学生请销假制度.md", version="2026-03-01", source="学生手册",
               text="事假须由家长与班主任联系并说明情况后，由班主任登记在册并视情况审核，事假一般不得超过两周。")
both = [old_one, new_one]
newest = max(both, key=lambda c: c["version"])    # max + key = 按某个字段取最大的那个
print(f"       旧版（{old_one['version']}）：{old_one['text'][:28]}…")
print(f"       新版（{new_one['version']}）：{new_one['text'][:28]}…")
print(f"     → 保留：{newest['version']} 版。旧版即使内容不同，也不能和新版同时存在。")
print("""
     ★ 这一关最重要，也最容易被忽略。
       旧版说"事假不超过一个月"，新版说"不超过两周" —— 差一个字，结论完全不同。
       真实项目里那种矛盾资料不是我塞的 —— 是【旧版文档没删干净】自己混进去的。
       所以：同一主题只允许有一个"当前版本"，这是入库把关的头号任务。
""")


# ==================================================================
sep("② 把关前后对比：同样的问题，两种库，两种答案")
# ==================================================================
def build_index(chunks):
    v = np.array(list(model.passage_embed([c["text"] for c in chunks])), dtype="float32")
    return chunks, v


def search(chunks, vecs, query, k=3):
    q = np.array(list(model.query_embed([query])), dtype="float32")
    scores = vecs @ q[0]
    order = np.argsort(-scores)[:k]          # argsort 从小到大，取负号就是从大到小
    return [(chunks[i], float(scores[i])) for i in order]


# 三个库：
#   A 脏库   = 原始数据，一道关都不把（重复/碎屑/隐私/旧版本全在）
#   B 净库   = 五道关卡放行后的
#   C 净库+旧版 = 把关了，但忘了删旧版本（模拟最常见的线上事故）
dirty = list(RAW_CHUNKS)
clean = [c for c in kept_chunks if c["version"] != "2024-05-01"]
with_old = clean + [old_one]

IDX = {name: build_index(cs) for name, cs in
       [("脏库", dirty), ("净库", clean), ("净库+旧版", with_old)]}


EMB_CACHE = {}


def embed_one(text):
    """单条文本的向量（带缓存，避免同一条重复算向量）"""
    if text not in EMB_CACHE:
        EMB_CACHE[text] = np.array(list(model.passage_embed([text])), dtype="float32")[0]
    return EMB_CACHE[text]


def show(name, hits):
    """打印检索结果，并标出哪些是重复占坑的（数据说话，不靠我嘴说）"""
    print(f"\n   【{name}】top-{len(hits)}")
    prev = []
    for i, (c, s) in enumerate(hits, 1):
        tag = ""
        for p in prev:
            sim = float(embed_one(c["text"]) @ embed_one(p))
            if sim > DUP_THRESHOLD:
                tag = f"   ← 与第 {prev.index(p) + 1} 条重复，白占一个名额"
                break
        prev.append(c["text"])
        bad = "  ⚠ 旧版本，数字是错的" if c["version"] == "2024-05-01" else ""
        print(f"     {i}. [{s:.3f}] 《{c['doc']}》{c['text'][:30]}…{bad}{tag}")


QUESTION = "事假最多能请多久？超过两周要谁批？"
print(f"   问题：{QUESTION}")

hits = {}
for name, (cs, vs) in IDX.items():
    hits[name] = search(cs, vs, QUESTION, 3)
    show(name, hits[name])

print("""
   ⇒ 看懂这两件事，入库把关的价值就清楚了：""")


def ask(context_chunks, question):
    ctx = "\n".join(f"- {c['text']}" for c in context_chunks)
    prompt = (
        "你是学校学生事务的答疑助手。只根据下面的资料回答，资料里没有的就明确说不知道。\n"
        f"资料：\n{ctx}\n\n问题：{question}"
    )
    return llm.invoke([HumanMessage(content=prompt)]).content


print("\n   把三批资料分别喂给同一个大模型（模型没变、提示词没变，只有资料不同）：")
answers = {}
for name in ["脏库", "净库", "净库+旧版"]:
    answers[name] = ask([c for c, _ in hits[name]], QUESTION)
    print(f"\n   【{name}的回答】")
    print("     " + answers[name].replace("\n", "\n     "))

print("""
   ⇒ 三组对比说明两件事：

     ① 重复块会【白占名额】。top-3 里两条说的是同一件事，
        等于你花 3 份 token 只买到 1 份信息 —— 该进来的其他知识被挤出去了。
        这就是为什么"去重"看着不起眼，却是性价比最高的一关。

     ② 旧版本混进来 = 上一章我手动塞假资料的【真实版】。
        上一章是我故意塞的；真实项目里没人故意，都是"旧文档忘了删"。
        模型分不清哪个是新版 —— 这次它比较老实，把两个数字都列出来然后说"无法确定"；
        运气差一点时，它会挑一个自信地答错。两种结果用户都不满意，
        而正确答案明明就在库里、只是被一份旧文档挤兑了。

     ★ 所以：模型没换、提示词没换，换的只是库里有什么。
       防线要建在【入库口】，不是提示词里。
       提示词写得再狠，也拦不住库里躺着一份旧政策。""")


# ==================================================================
sep("③ Rerank：先粗捞 20 条，再精排留 3 条")
# ==================================================================
print("""
为什么需要 rerank？因为第一步的向量检索是【粗捞】：
    · 它给每一块单独算向量，问题和块之间没有"互相看见"
    · 所以它快（毫秒级），但不够准

Rerank 的做法：把【问题】和【候选块】拼在一起，让模型逐条判断"这条到底有没有用"。
    更准，但更慢更贵 —— 所以只对粗捞出来的 20 条做，不对全库做。

   粗捞（便宜、快、召回多）  →  rerank（贵、慢、只留最好的）  →  生成
      20 条                        3 条                        1 个答案

本文件用【大模型打分】做 rerank（bge-reranker 那种专用模型要装 torch，2GB 太重）。
这也是工业界真实在用的方案之一，好处是零安装、还能输出理由。
""")

CANDIDATE_Q = "处分要保留多久？我想申诉。"
clean_chunks, clean_vecs = IDX["净库"]
candidates = search(clean_chunks, clean_vecs, CANDIDATE_Q, k=len(clean_chunks))
print(f"   问题：{CANDIDATE_Q}")
print(f"   粗捞 {len(candidates)} 条（本例库小，真实项目粗捞 20-50 条）：")
for i, (c, s) in enumerate(candidates, 1):
    print(f"     {i}. [{s:.3f}] 《{c['doc']}》{c['text'][:34]}…")

# 让大模型给每条打分：一次请求全部打完（省 token，也省时间）
numbered = "\n".join(f"{i}. {c['text']}" for i, (c, _) in enumerate(candidates, 1))
rerank_prompt = (
    "下面是检索到的候选资料，请判断每条对回答这个问题有多大帮助。\n"
    f"问题：{CANDIDATE_Q}\n\n候选资料：\n{numbered}\n\n"
    "只输出 JSON，格式：{\"scores\": [{\"id\": 1, \"score\": 0到10的整数, \"reason\": \"一句话\"}]}\n"
    "不要输出任何其他内容。"
)
t0 = time.time()
resp = llm.invoke([HumanMessage(content=rerank_prompt)]).content
cost = time.time() - t0

# 解析 JSON：模型可能在外面套 ```json 标记，先剥掉
raw = re.sub(r"^```(?:json)?|```$", "", resp.strip(), flags=re.M).strip()
try:
    data = json.loads(raw)
    scores = {int(x["id"]): (int(x["score"]), x.get("reason", "")) for x in data["scores"]}
except Exception as e:                      # 解析失败就退回原顺序，保证流程不崩
    print(f"   （JSON 解析失败 {type(e).__name__}，退回粗捞顺序）\n   原始返回：{resp[:200]}")
    scores = {i: (0, "") for i in range(1, len(candidates) + 1)}

print(f"\n   大模型精排结果（耗时 {cost:.1f} 秒）：")
ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
for id_, (sc, reason) in ranked:
    c = candidates[id_ - 1][0]
    print(f"     {sc:>2} 分  《{c['doc']}》{c['text'][:30]}…")
    if reason:
        print(f"          ↳ {reason}")

top3 = [candidates[id_ - 1][0] for id_, _ in ranked[:3]]
print(f"\n   精排后只留 {len(top3)} 条喂给模型，其余 {len(candidates) - len(top3)} 条丢弃。")
final = ask(top3, CANDIDATE_Q)
print("\n   【最终回答】")
print("     " + final.replace("\n", "\n     "))

print("""
   ★ rerank 的一个额外好处：它顺手把【噪声】挡在生成之前。
       粗捞的第 2、3 名常常是"沾点边但没用"的，rerank 会把它们打低分踢掉。
     这就是为什么工业界说 "召回看 recall，排序看 precision" ——
       粗捞阶段宁可多捞（别漏），精排阶段宁可少留（别脏）。
""")


# ==================================================================
sep("④ 小结：一条 RAG 流水线的完整防线")
# ==================================================================
print("""
   文档进来
      ↓
   【入库把关】← 本次重点。五道关卡 + 版本冲突处理
      ↓           拦掉：碎屑 / 重复 / 近似重复 / 隐私 / 无来源 / 旧版本
   干净的库
      ↓
   【粗捞 top-20】向量 + BM25 混合，追求"别漏"
      ↓
   【rerank top-3】精排，追求"别脏"
      ↓
   【生成】只根据这 3 条回答，没有就说不知道
      ↓
   【评测】考卷打分（recall@1 / recall@3）← 上一章做的
      ↓
   改一个环节 → 重跑考卷 → 看分数变化

   每一道关卡都是可以量化的：
     · 入库把关  → 拦下率（本次拦了 %d/%d = %.0f%%）
     · 粗捞      → recall@20（漏了没？）
     · rerank    → precision@3（留下来的有用吗？）
     · 生成      → 拒答率 / 幻觉率
   简历上写"我把召回从 X 提到 Y"只讲了其中一格；
   能讲清整条链上每一格怎么测，才是真正的工程能力。
""" % (len(rejected_chunks), len(RAW_CHUNKS), 100 * len(rejected_chunks) / len(RAW_CHUNKS)))

print("下一步 Step 6：把这一整套包成 FastAPI 服务（/chat 接口），让别人能用 HTTP 访问。")

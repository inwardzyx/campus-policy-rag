# -*- coding: utf-8 -*-
"""
Step 6：把 RAG 整条链包成 HTTP 服务（FastAPI）

前面 5 步做的东西都跑在一个 .py 脚本里 —— 只有你自己能用。
这一步把它变成【别人能用 HTTP 访问的服务】：

    你的脚本（自己跑）  →  HTTP 服务（别人访问）
    print 输出结果      →  浏览器 / 手机 / 前端 / 其他程序都能调

三个新名词，一句话解释：
    FastAPI   = 写接口的框架。你写一个函数，它帮你变成 HTTP 接口
    uvicorn   = 真正跑起来的服务器（FastAPI 只负责"定义"，uvicorn 负责"运行"）
    pydantic  = 校验请求体的工具（规定"你 POST 过来的 JSON 必须长这样"）

跑法（务必先关 trace）：
    set LANGSMITH_TRACING=false
    <你的虚拟环境>/Scripts/python.exe step6_fastapi_service.py

跑起来后：
    打开浏览器访问 http://127.0.0.1:8000    ← 一个能聊天的网页
    接口文档（自动生成）http://127.0.0.1:8000/docs
    命令行测试见文件底部注释

按 Ctrl+C 停止服务。
"""

import hashlib
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# ⚠️ 必须在 import 之前：走国内镜像 + 指定模型缓存
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
CACHE_DIR = os.environ.get(
    "FASTEMBED_CACHE_PATH",
    str(Path.home() / ".cache" / "fastembed"),
)

import numpy as np                                  # noqa: E402
import uvicorn                                      # noqa: E402
from fastapi import FastAPI                         # noqa: E402
from fastapi.responses import HTMLResponse          # noqa: E402
from fastembed import TextEmbedding                 # noqa: E402
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import env_compat                                   # noqa: E402  ★ 必须在 import langchain 之前
env_compat.ensure_mmh3()                            # noqa: E402  本机 DLL 被策略拦截时的降级方案
env_compat.ensure_uuid_utils()                      # noqa: E402  同上，拦的是 uuid_utils

from langchain_core.messages import HumanMessage    # noqa: E402
from langchain_deepseek import ChatDeepSeek         # noqa: E402
from pydantic import BaseModel, Field               # noqa: E402
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ==================================================================
# ① 知识库 + 入库把关（和 Step 5 完全一样的逻辑，这里精简成一份）
# ==================================================================
RAW_DOCS = [
    dict(doc="学生请销假制度.md", clause="请假-事假", version="2026-03-01", source="学生手册",
         text="事假须由家长与班主任联系并说明情况后，由班主任登记在册并视情况审核，事假一般不得超过两周。"),
    dict(doc="学生请销假制度.md", clause="请假-病假", version="2026-03-01", source="学生手册",
         text="病假须提供校医院或二级以上医院的诊断证明，经班主任审核后报院系批准，证明材料由班主任留存。"),
    dict(doc="请假审批流程.md", clause="审批权限", version="2026-02-10", source="教务处",
         text="请假一日以内由班主任审批，超过三日须院系负责人批准，并同时通知家长确认情况。"),
    dict(doc="违纪处分种类.md", clause="处分种类", version="2026-01-20", source="学生手册",
         text="处分种类由轻到重依次为警告、严重警告、记过、留校察看、开除学籍，处分前学生有权陈述和申辩。"),
    dict(doc="学籍异动与休学.md", clause="休学期限", version="2026-02-01", source="教务处",
         text="休学期限一般为一年，期满可申请续休，累计休学不超过两年，保留学籍期间不参加课程考核。"),
    dict(doc="校园事务办理指南.md", clause="办理时间", version="2026-01-05", source="学生事务办公室",
         text="学生事务办理地点为行政楼一站式服务大厅，服务时间为工作日上午八时三十分至十二时、下午十四时至十七时。"),
    # ↓ 下面是脏数据，会被入库把关拦掉（故意留着，证明关卡真的在工作）
    # ★ 注意上面两条 clause 是"请假-事假"和"请假-病假"—— 同一个 doc、不同 clause。
    #   这是版本冲突关卡的命门：若只用 doc 名分组，这两条会互相挤掉（详见下面那段注释）。
    dict(doc="学生请销假制度.md", clause="请假-事假", version="2024-05-01", source="旧版学生手册（已下线）",
         text="事假须经家长与班主任联系说明后由班主任审核，事假一般不得超过一个月。"),
    dict(doc="校园事务办理指南.md", clause="学生信息", version="2026-02-01", source="学生事务工单导出",
         text="学生张明，学号 2024010101，手机号 13812345678，身份证号 440301199001011234，请妥善保管。"),
    dict(doc="学生请销假制度.md", clause="请假-事假", version="2026-03-01", source="班主任话术库",
         text="事假需要家长与班主任联系说明情况，班主任登记在册后视情况审核，事假原则上不超过两周。"),
]

MIN_LEN = 15
DUP_THRESHOLD = 0.90
PII_PATTERNS = [(r"1[3-9]\d{9}", "手机号"), (r"\d{17}[\dXx]", "身份证号"), (r"\d{16,19}", "银行卡号")]


def norm(text):
    return re.sub(r"[\s\W_]+", "", text)


# ==================================================================
# ② 全局资源：模型和库只在【服务启动时】加载一次
#    ★ 这是服务化最重要的一条：绝不能在每个请求里重新加载模型！
# ==================================================================
class RAG:
    """把所有重家伙（模型、向量库）装在里面，全局只有一份"""

    def __init__(self):
        self.ready = False

    def startup(self):
        t0 = time.time()
        self.model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=CACHE_DIR)
        self.llm = ChatDeepSeek(model="deepseek-chat", temperature=0)

        kept, rejected = self._guard(RAW_DOCS)
        self.chunks = kept
        self.rejected = rejected
        self.vectors = np.array(
            list(self.model.passage_embed([c["text"] for c in kept])), dtype="float32")
        self.ready = True
        self.boot_time = time.time() - t0

    def _guard(self, docs):
        """五道关卡 + 版本冲突（逻辑和 Step 5 一样）"""
        kept, rejected = [], []
        for c in docs:
            if len(c["text"].strip()) < MIN_LEN:
                rejected.append((c, "太短碎屑"))
            else:
                kept.append(c)

        seen, stage2 = set(), []
        for c in kept:
            h = hashlib.md5(norm(c["text"]).encode()).hexdigest()
            if h in seen:
                rejected.append((c, "完全重复"))
            else:
                seen.add(h)
                stage2.append(c)
        kept = stage2

        vecs = np.array(list(self.model.passage_embed([c["text"] for c in kept])), dtype="float32")
        stage3 = []
        for i, c in enumerate(kept):
            dup = any(float(vecs[i] @ vecs[j]) > DUP_THRESHOLD for j in range(len(stage3)))
            if dup:
                rejected.append((c, "近似重复"))
            else:
                stage3.append(i)
        kept = [kept[j] for j in stage3]

        stage4 = []
        for c in kept:
            hit = next((name for pat, name in PII_PATTERNS if re.search(pat, c["text"])), None)
            if hit:
                rejected.append((c, f"含个人隐私（{hit}）"))
            else:
                stage4.append(c)
        kept = stage4

        # 版本冲突：同一 (doc, clause) 只留 version 最大的
        # ★ 这里的 key 必须是 (文档, 条款) 两样一起，不能只用文档名！
        #   只用文档名的话，"学生请销假制度.md"里【事假】和【病假】是两条完全不同的规定，
        #   会被误判成"同一条的新旧两版"，结果一条把另一条挤掉 —— 库里凭空少一条规则。
        newest = {}
        for c in kept:
            key = (c["doc"], c["clause"])          # ← 两样一起当身份证
            if key not in newest:
                newest[key] = c
                continue
            old = newest[key]
            if c["version"] > old["version"]:       # 新来的更新 → 换掉旧的
                rejected.append((old, f"旧版本，被新版取代（{old['version']} → {c['version']}）"))
                newest[key] = c
            else:                                   # 旧版本 / 同版本后来者 → 新来的被拦
                rejected.append((c, f"旧版本，库里已有更新的 {old['version']}"))
        kept = list(newest.values())
        return kept, rejected

    def search(self, query, k=5):
        q = np.array(list(self.model.query_embed([query])), dtype="float32")[0]
        scores = self.vectors @ q
        order = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in order]

    def rerank(self, query, candidates):
        """让大模型给每条候选打分，返回 [(块, 向量分, 精排分, 理由)]，按精排分从高到低

        为什么两个分数都要留着？
            向量分 = 粗捞阶段的（电脑按字面/语义相似度算的，快但粗）
            精排分 = 大模型重新看的（慢但准）
        两个都返回，你就能在网页上直观看到"粗捞排第一的，精排未必第一" —— 这正是 rerank 的意义。
        """
        numbered = "\n".join(f"{i}. {c['text']}" for i, (c, _) in enumerate(candidates, 1))
        prompt = (
            "判断下列每条资料对回答这个问题有多大帮助。\n"
            f"问题：{query}\n\n候选资料：\n{numbered}\n\n"
            "只输出 JSON：{\"scores\":[{\"id\":1,\"score\":0到10的整数,\"reason\":\"一句话\"}]}"
        )
        resp = self.llm.invoke([HumanMessage(content=prompt)]).content
        raw = re.sub(r"^```(?:json)?|```$", "", resp.strip(), flags=re.M).strip()
        try:
            data = json.loads(raw)
            sc = {int(x["id"]): (int(x["score"]), x.get("reason", "")) for x in data["scores"]}
        except Exception:
            sc = {i: (0, "") for i in range(1, len(candidates) + 1)}
        # 大模型只回 id 和分数，我们按 id 把【原始候选】捞回来（candidates 下标从 0 开始，id 从 1 开始）
        out = []
        for i, (score, reason) in sorted(sc.items(), key=lambda kv: kv[1][0], reverse=True):
            chunk, vec_score = candidates[i - 1]
            out.append((chunk, float(vec_score), score, reason))
        return out

    def answer(self, question, chunks):
        ctx = "\n".join(f"- {c['text']}" for c in chunks)
        prompt = ("你是学校学生事务的答疑助手。只根据下面的资料回答，资料里没有的就明确说不知道。\n"
                  f"资料：\n{ctx}\n\n问题：{question}")
        return self.llm.invoke([HumanMessage(content=prompt)]).content


rag = RAG()


# ==================================================================
# ③ FastAPI：把函数变成 HTTP 接口
# ==================================================================
# lifespan：服务启动时干一次（加载模型），关闭时收尾。
# @ 开头的叫【装饰器】，它把下面那个函数"包装"一下交给框架 —— 你不用手动调用它。
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("服务启动中：加载模型 + 建库（只在启动时做一次）……")
    rag.startup()
    print(f"✓ 就绪：{len(rag.chunks)} 块入库，拦下 {len(rag.rejected)} 块，"
          f"耗时 {rag.boot_time:.1f} 秒")
    yield                       # ← 这一行表示"服务开始接客"
    print("服务关闭")


app = FastAPI(title="校园政策问答", lifespan=lifespan)


# pydantic 模型：规定"请求体必须长这样"，传错了框架自动返回 422，不用你写判断
class ChatRequest(BaseModel):
    question: str = Field(..., description="用户的问题", min_length=1)
    top_k: int = Field(5, description="粗捞几条", ge=1, le=10)
    use_rerank: bool = Field(True, description="是否启用 rerank 精排")


class Source(BaseModel):
    doc: str
    text: str
    score: float
    rerank_score: Optional[int] = None
    reason: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    took_ms: int
    rerank_used: bool


@app.get("/health")
def health():
    """健康检查：部署后第一件事就是访问它，看服务活着没"""
    return {"status": "ok" if rag.ready else "loading",
            "chunks": len(rag.chunks),
            "rejected": len(rag.rejected)}


@app.get("/guard-report")
def guard_report():
    """入库把关报告：哪些被拦了、为什么 —— 这是你敢把仓库给人看的底气"""
    return {"kept": [{"doc": c["doc"], "clause": c["clause"], "text": c["text"]}
                     for c in rag.chunks],
            "rejected": [{"doc": c["doc"], "clause": c.get("clause", ""),
                          "text": c["text"][:40], "reason": r}
                         for c, r in rag.rejected]}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """核心接口：问题进来 → 粗捞 → rerank → 生成 → 带出处返回"""
    t0 = time.time()
    if not rag.ready:
        return {"answer": "服务还在加载模型，请稍等几秒再试", "sources": [],
                "took_ms": 0, "rerank_used": False}

    cands = rag.search(req.question, k=req.top_k)

    if req.use_rerank:
        ranked = rag.rerank(req.question, cands)          # [(块, 向量分, 精排分, 理由)]
        picked = [x for x in ranked if x[2] >= 5][:3]     # 只留 5 分以上的，最多 3 条
        if not picked:                                    # 全部低分 = 库里没有相关资料
            picked = ranked[:1]
        sources = [Source(doc=f"{c['doc']}｜{c['clause']}", text=c["text"],
                          score=round(v, 3), rerank_score=s, reason=r)
                   for c, v, s, r in picked]
        ctx = [c for c, _, _, _ in picked]
    else:
        picked = cands[:3]
        sources = [Source(doc=f"{c['doc']}｜{c['clause']}", text=c["text"],
                          score=round(s, 3)) for c, s in picked]
        ctx = [c for c, _ in picked]

    answer = rag.answer(req.question, ctx)
    return ChatResponse(answer=answer, sources=sources,
                        took_ms=int((time.time() - t0) * 1000),
                        rerank_used=req.use_rerank)


# ==================================================================
# ④ 一个能直接聊天的网页（GET / ）
#    意义：简历上的链接点开是这个页面，不是一串 JSON —— 面试官体验完全不同
# ==================================================================
HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>校园政策问答</title>
<style>
 body{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.7}
 h2{font-weight:500} textarea{width:100%;height:60px;font-size:14px;padding:8px}
 button{padding:8px 20px;font-size:14px;cursor:pointer;margin-top:8px}
 .box{background:#f6f6f4;border-radius:8px;padding:12px 16px;margin-top:14px;white-space:pre-wrap}
 .src{font-size:13px;color:#666;border-left:3px solid #ddd;padding-left:10px;margin-top:6px}
 .tag{display:inline-block;font-size:12px;background:#e8f0fe;color:#185FA5;border-radius:4px;padding:1px 6px;margin-right:6px}
</style></head><body>
<h2>校园政策问答（RAG）</h2>
<p style="color:#666;font-size:14px">LangGraph + bge-small-zh 检索 + 入库把关 + rerank + DeepSeek 生成</p>
<textarea id="q" placeholder="试试：事假最多能请多久？ / 处分要保留多久？ / 学校附近有哪些奶茶店？"></textarea><br>
<button onclick="ask()">提问</button>
<label style="margin-left:12px;font-size:13px"><input type="checkbox" id="rr" checked> 启用 rerank</label>
<div class="box" id="ans" style="display:none"></div>
<div id="src"></div>
<script>
async function ask(){
  const q=document.getElementById('q').value.trim();
  if(!q){alert('请输入问题');return;}
  const rr=document.getElementById('rr').checked;
  const a=document.getElementById('ans'),s=document.getElementById('src');
  a.style.display='block';a.textContent='思考中…';s.innerHTML='';
  const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question:q,use_rerank:rr})});
  const d=await r.json();
  a.textContent=d.answer;
  s.innerHTML='<p style="font-size:13px;color:#888;margin-top:12px">用时 '+d.took_ms+' ms　引用资料：</p>'+
    (d.sources||[]).map(x=>'<div class="src"><span class="tag">'+x.doc+'</span>'+
      (x.rerank_score!=null?'<span class="tag">精排 '+x.rerank_score+' 分</span>':'')+
      x.text+(x.reason?'<br><i style="color:#999">'+x.reason+'</i>':'')+'</div>').join('');
}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


# ==================================================================
# ⑤ 启动（只有 python xxx.py 直接运行时才会执行；被 import 时不会）
# ==================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("服务启动后访问：")
    print("   http://127.0.0.1:8000        聊天页面")
    print("   http://127.0.0.1:8000/docs   自动生成的接口文档")
    print("   http://127.0.0.1:8000/health 健康检查")
    print("按 Ctrl+C 停止")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

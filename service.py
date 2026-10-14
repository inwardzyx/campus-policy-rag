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

跑法（务必先关 trace，在仓库根目录跑）：
    python service.py

跑起来后：
    打开浏览器访问 http://127.0.0.1:8000    ← 一个能聊天的网页
    接口文档（自动生成）http://127.0.0.1:8000/docs
    命令行测试见文件底部注释

按 Ctrl+C 停止服务。
"""

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# 先读仓库根目录的 .env（没有这个文件就静默跳过，什么都不影响）。
# 为什么用 .env 而不是 setx / export？
#   · setx 是 Windows 专有命令，服务器（Linux）上不存在 —— 部署时那套直接失效
#   · setx 设完要重开终端才生效，而且改的是系统级环境变量
#   · .env 跟着项目走，clone 下来复制一份就能跑，三个平台一致
# 已经有同名环境变量时不会覆盖它（load_dotenv 默认 override=False），
# 所以想临时换 key，直接设环境变量比改文件方便。
# 模板见 .env.example。
from dotenv import load_dotenv                        # noqa: E402
load_dotenv()

# ⚠️ 必须在 import 之前：走国内镜像 + 指定模型缓存
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 缓存目录默认放在【用户主目录】下（Windows / Mac / Linux 通用）。
#   想换位置就设环境变量 FASTEMBED_CACHE_PATH。
#   os.path.expanduser("~") 会把 ~ 展开成当前用户的主目录，
#   比写死 "D:/..." 好 —— 别人 clone 下来不会在你不存在的盘符上找目录。
CACHE_DIR = os.environ.get(
    "FASTEMBED_CACHE_PATH",
    os.path.join(os.path.expanduser("~"), ".cache", "fastembed"))

import numpy as np                                  # noqa: E402
import jieba                                        # noqa: E402
import uvicorn                                      # noqa: E402
import env_compat                                   # noqa: E402  ★ 必须放在 import fastembed / langchain 之前
env_compat.ensure_mmh3()                            # 本机 DLL 被策略拦截时的降级方案，见 env_compat.py
env_compat.ensure_uuid_utils()                      # 同上，拦的是 uuid_utils（langchain 用它生成 trace id）
from kb.loader import load_documents                # noqa: E402  知识库从 docs/ 加载，不再写死 RAW_DOCS
import limits as limits_mod                         # noqa: E402  限流 + 日配额（见 limits.py）
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, StreamingResponse   # noqa: E402
from fastembed import TextEmbedding                 # noqa: E402
from langchain_core.messages import HumanMessage    # noqa: E402
from langchain_deepseek import ChatDeepSeek         # noqa: E402
from pydantic import BaseModel, Field               # noqa: E402
from rank_bm25 import BM25Okapi                     # noqa: E402
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout  # noqa: E402

logger = logging.getLogger(__name__)

# ==================================================================
# ① 知识库 + 入库把关
#    知识库不再写死在代码里 —— 改成从 docs/ 目录加载（见 kb/loader.py）。
#    docs/kb/    = 已审核、可直接用的干净条款
#    docs/inbox/ = 待审核、可能含脏数据的副本（旧版 / 话术库改写版）
#    两者都丢进同一套「入库把关」(_guard)，脏的自然被拦，干净的自然留下。
#    想加文档？往 docs/ 扔一个 .md 就行，不用改代码。
# ==================================================================
DOCS_DIR = Path(__file__).parent / "docs"

MIN_LEN = 15

# 近似重复关的阈值。★ 0.96 是量出来的，不是拍脑袋（scripts/ 下两个脚本可复现）：
#
#   【该留的一侧】真语料量出来的，可信：
#       把 83 个块两两全量算余弦（3403 对），能走到关卡 3 的块里最像的一对
#       是《纪律处分规定》第二十四条 vs 第二十五条 = 0.9230（都是合法条款，不该删）。
#
#   【该拦的一侧】真语料里【一对都没有】—— 关卡 3 在真实入库中拦下 0 条。
#       所以这一侧的 0.9839 ~ 0.9987 只能来自手写的同义改写样本和
#       tests/fixtures/dirty_inbox/03-近似重复.md（措辞微调的改写版）。
#       ★ 别拿 1.0000 那对「内设机构」当证据：它只有 4 个字，
#         在关卡 1（< 15 字）就被拦掉了，压根走不到关卡 3。
#
#   取 0.96：离 0.9230 有 0.037、离 0.9839 有 0.024，两边都有余量。
#   故意偏严（宁可漏拦一条重复，也别误删一条合法条款）—— 漏拦只是召回里多一条
#   相似的，误删是**知识永久丢失**。这两个错误不对称。
#
#   ★ 这里踩过一个坑，记下来免得重犯：
#     一开始是 0.90，结果第二十四条 vs 第二十五条（0.9230）把第二十五条挤掉了 —— 误伤。
#     第一反应是"光看相似度分不开，得加身份判断（同 doc + 同 clause 才判重）"。
#     **这个条件加错了**，两个理由：
#       ① 全量一量才发现，"身份相同"的配对在真实语料里**一对都没有**
#          （loader 给子条款加了（五）（六）后缀，clause 名天然就不同），
#          加了等于让关卡 3 一条都拦不到；
#       ② 更重要的是原则上就错：跨文档 / inbox 里进来的重复副本，
#          clause 名本来就可能不一样，用身份去卡等于把这类重复全放行。
#     正确解法是把阈值抬到断层里，一行常量，不加条件。
#     ★ 教训：先全量量分布再改规则，别拿两三个样本就下结论 ——
#       那次正是靠两三个样本得的结论，得出了一个错的规则。
DUP_THRESHOLD = 0.96
PII_PATTERNS = [(r"1[3-9]\d{9}", "手机号"), (r"\d{17}[\dXx]", "身份证号"), (r"\d{16,19}", "银行卡号")]

# ==================================================================
# 关卡 5.5：废止声明不进库（2026-10-10 加）
# ==================================================================
# ★ 这条关卡是【真实语料逼出来的】，不是预防性设计。
#
#   学校 9 份现行制度的最后一条都是「附则」，内容形如：
#       "本办法自通过之日起实施，由党委学生工作部负责解释，
#        原《广东交通职业技术学院学生奖励管理办法》（粤交职院〔2024〕89号）同时废止。"
#   实测 6 条，两两相似度高达 0.9560 / 0.9517 / 0.9366 —— 只差文件名和文号。
#   两个后果，都实测过：
#     ① 照现状（不加这一关）只拦下 1 条、留 2 条，**留哪条取决于入库顺序**，
#        而且被拦那条的理由写的是「近似重复」—— 理由完全误导。
#     ② 它们挤占召回位：学生问"奖学金怎么申请"，召回来的是"旧版已废止"，
#        回答不了任何现行问题。
#
# ★ 为什么判据不拿「长度」当条件（初版踩过，已改）：
#   初版用「≤220 字」当特征之一，理由是"附则通常很短"。
#   实测漏了 1 条：专项补助管理办法第十七条 = 废止声明 + 后面接了一长串
#   **拍平的附件表格**（原文 1870 字，被 max_chars=400 截断后正好 400 字）。
#   ⇒ 它不是"短附则"，是"附则 + 附件"。
#   而长度是【会被上游改写的量】—— 今天 398 字、换个 max_chars 就变 300 或 500，
#   拿它当判据等于把判据绑在另一个可调参数上（同一类病：见 `_version_key`
#   那个"判据和被测对象不同源"的教训）。
#   长度只留作【兜底下限】（<30 字不参与判定），主力判据是开头句。
#
# ★ 三特征为什么够窄也够宽（收紧前先量过）：
#   "废止"两字在正文里很常见（"违反规定的按某某处理并废止其资格"），
#   单看它会误伤。实测同时满足三特征的块在全库里【恰好就是那 6 条废止声明】，
#   一条真条款都没被牵连 ⇒ 区分度够。
#
# ★ 局限（必须承认）：这是【形状匹配】不是【语义判断】。
#   一份文件如果写"本办法自 2025 年 11 月 6 日起施行"而不提"原…同时废止"，
#   就拦不住。真正的语义判据是"这条讲的是过去还是现在"，
#   那需要 LLM 判定 —— 成本高、且拒答判据本身不可验证，不划算。
#   现在这条至少做到【已知的 6 条一条不留】，且漏进来的会被 /guard-report 看到。
STALE_MARK = "同时废止"                                   # 特征1：明确的废止措辞
STALE_DOCNO = re.compile(r"[〔\[（(]\s*[^\s〕\]）)]{0,4}\d{4}\s*[〕\]）)]")  # 特征2：带〔2024〕89号 形式的文号
STALE_HEAD_RE = re.compile(r"^本办法自.{0,24}?(施行|实施)")                   # 特征3：附则开头「本办法自…施行」
STALE_MIN_CHARS = 30                                      # 兜底下限：半句话不参与判定


def _stale_clause_reason(text: str) -> str | None:
    """判断一条文本是不是「废止声明」；是则返回拒收理由，不是则返回 None。

    ★ 为什么返回理由字符串而不是 bool：
      入库把关的每一条拒收都要能说清"为什么"——
      如果只返回 True，日志里就会写"被拦"，排查时得回头猜是哪条规则。
      （这也是 `_guard` 里其余 5 关一贯的写法。）
    ★ 为什么要实测"会不会误伤"再定这条判据（而不是凭感觉写正则）：
      收紧规则前先量"三特征同时满足的块有哪些"，
      实测答案是【恰好那 6 条废止声明，一条真条款都没有】。
      这就是判据够窄的证据 —— 不是"我觉得不会误伤"。
    """
    t = text.strip()
    if len(t) < STALE_MIN_CHARS:
        return None                      # 半句话不参与判定
    if STALE_MARK not in t:
        return None
    if not STALE_DOCNO.search(t):
        return None
    if not STALE_HEAD_RE.match(t):
        return None
    return (f"废止声明（讲的是旧版已失效，回答不了任何现行问题；"
            f"含「{STALE_MARK}」+〔文号〕+「本办法自…施行」开头）")

# 关掉 rerank 时，用【向量分】判断要不要拒答（精排分此时不存在）。
# ★★ 2026-10-10 实测结论更新（这一段以前写的是"早就交叉了"，现在交叉幅度变大很多）：
#   语料从 68 块加到 263 块（学校 10 份现行制度）之后，`evalset/probe_threshold.py`
#   复跑（本机实测，阈值 0.30~0.95 以 0.01 步长扫）：
#       应答题最低 0.5968（q3）／拒答题最高 0.6321（q16）—— 两侧【交叉 0.0353】
#       要全放行必须 ≤0.5968，要全拦住必须 >0.6321 ⇒ 可行区间是空的
#   ★ 本节旧版本写的是「拒答题最高 0.7276（q18）、差 0.13」—— 那一条【已作废】：
#     q18「怎么申请助学贷款」在语料扩容后从拒答题改成了应答题
#     （见 evalset/questions.json 的 note），拿它当拒答题论据会把交叉幅度算大 3 倍多。
#   ⇒ 单个向量分这个判据在 263 块语料上已经【不够用了】，不是调参能救的。
#
# ★ 为什么线上不动这个阈值：
#   产品默认路径是 use_rerank=true，靠【精排分<5】拒答，不看这个向量分。
#   真正该改的是【拒答判据】（多信号：向量分 + 精排分 + 关键词覆盖度），
#   那是另一件事，见 README 下一步清单。
#   现在动它只会让"该拒的"好看、"该答的"更危险 —— 漏答率才是硬指标。
VEC_REJECT_THRESHOLD = 0.55

# 单次大模型调用的超时上限（秒），可用环境变量覆盖。
# ★ 重要：这个超时【没法】通过 ChatDeepSeek(timeout=...) 来设置 —— 实测它的 model_fields
#   里压根没有 timeout 字段，而且 model_config 是 extra='ignore'，
#   意味着你传了不会报错，但会被【静默忽略】：看着像加了超时，其实一秒都没生效。
#   所以只能在调用点自己兜（见下面的 _invoke_llm）。
LLM_TIMEOUT_SEC = float(os.environ.get("LLM_TIMEOUT_SEC", "30"))


class LLMCallError(Exception):
    """一次大模型调用失败了：超时 / 限流 / 网络错误 / 返回了解析不了的东西。

    为什么要单独定义一种异常，而不是就地吞掉？
        它必须和「知识库里没这个东西」严格区分开 ——
        后者是正常的业务结果（拒答），前者是【故障】。
        两者一旦混在一起，就会把"模型挂了"伪装成"库里没有"，
        调用方看到的是一条平平无奇的拒答，永远发现不了服务其实已经不正常了。
    """


# 专门给大模型调用用的小线程池：为了能在调用点加超时（主线程不能无限期干等）。
_LLM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")


# ★★ Token 计量（2026-10-12 补）：usage 一直都在 AIMessage 上，
#   但三处调用点都只取 `.content` ⇒ usage 丢在那儿，谁也拿不到。
#
#   ★ 为什么在【这里】旁路记录，而不是把三处调用点改成
#     `msg = _invoke_llm(...); msg.content ...; msg.usage_metadata`：
#     因为那样要动三个地方的代码，任何一处手滑都可能改到 prompt / messages，
#     而"改 prompt"会**静默改变模型行为** —— 那时候回归报告里的
#     硬答率还是"旧数据"，没人发现。
#     在这里记录则：调用点一个字符都不改，模型输入必然一字不变。
#     ⇒ 用结构保证"只读不写"，而不是靠小心。
#
#   ★ 为什么用线程局部（threading.local）而不是全局字典：
#     FastAPI 是多线程的（uvicorn 默认线程池），
#     全局字典会被并发请求互相覆盖 ⇒ 算出来的均值是两个请求的混合物。
#     线程局部保证每个请求读到的是自己那次调用的 usage。
_LLM_USAGE = threading.local()

# ★★★ 2026-10-14：账本从"线程局部"改成"随请求走的显式对象 + contextvar"。
#
# ## 为什么非改不可（跨模型盲审 GLM-5.3-Flash 抓的，是流式改造的前置条件）
#
# 要加 SSE 流式端点，而流式把「返回响应」和「跑完管道」拆成了**两个时刻**：
#   · 若照抄 `_gated(lambda: StreamingResponse(gen()))`，`_gated` 的 finally 会在
#     **任何 token 产生之前**就跑完 ⇒ `resp` 上没有 usage ⇒ 每条流式请求都记成
#     `usage_missing` ⇒ `daily_tokens` 那道软闸对流式**永远不动**，而客户端完全看不出来。
#
# 更要命的是线程局部本身在流式下**语义就崩了**：
#   Starlette 对「同步生成器」是每次 `next()` 丢进线程池跑一次，**跨 yield 不保证同一线程**。
#   于是：① 累加落在 A 线程、取账落在 B 线程 ⇒ 记成 `usage_missing`；
#        ② 入口那句"丢弃残留"可能丢掉的正是**另一个请求**正在累加的半截账；
#        ③ 反过来线程复用会把别人的账混进来。
#   ★ 这不是流式引入的新病 —— 它是**本来就在**的隐患（同步请求恰好每次都在同一线程，
#     所以一直没暴露）。流式只是把它逼出来了。⇒ 顺手根治，别留给下一个人。
#
# ## 为什么用 contextvar 而不是"加一个 ledger 参数穿透三层"
#   调用链是 `handle_chat → rag.rewrite_query / rerank / answer → _invoke_llm`，
#   要穿透**四个方法签名**，还要动 tests/evalset 里一堆直接调用点（扩散面太大）。
#   contextvar 的传播语义正好是我们需要的（**已实测，本机 Python 3.14**）：
#     · `asyncio.to_thread`  → 带上下文 ✅（`LLM_POOL` 就是 ThreadPoolExecutor，
#       提交时会 `copy_context()` ⇒ 池内线程看得到同一个账本）
#     · 裸 `threading.Thread` → **不带** ❌
#   ⇒ 只要保证「在 to_thread 之前 set」，账本就能穿过线程池那一跳。
#
# ## 兼容面（不能破坏）
#   `_record_usage` / `_take_usage` 是**模块级函数，测试长期直接调用**
#   （`tests/test_llm_resilience.py` 有 5 处直接读写 `svc._LLM_USAGE`）。
#   ⇒ 保留它们：**没有显式账本时，行为与旧版逐字节一致**（走线程局部）；
#     有账本时走账本。两条路只差"账放哪儿"。
_CURRENT_LEDGER: contextvars.ContextVar["UsageLedger | None"] = \
    contextvars.ContextVar("rag_usage_ledger", default=None)


class UsageLedger:
    """一次请求的 token 账本。**累加、可重复取、取完清零**。

    ★ 为什么单独一个类而不是继续用一堆散落的属性：
      流式下"谁在什么时候取账"变得微妙（SSE 最后要推 usage，而 `_gated` 的 finally
      也要记账）—— 盲审点到的那条 P5 就是"取两次、第二次取到 None"。
      把它做成一个对象之后，「取一次、两处共用」是**结构上自然而然**的，
      而不是靠两处代码碰巧都写对。
    """

    def __init__(self) -> None:
        self._acc: dict | None = None
        self.n_calls = 0
        self.missing_calls = 0
        # ★ 单独一个标记，而不是靠 n_calls==0 推断 —— 见 mark_zero() 的注释
        self._zero = False

    def record(self, resp) -> None:
        """把一次模型调用的 usage 累加进来；取不到就只加 `missing_calls`（**绝不填 0**）。"""
        um = getattr(resp, "usage_metadata", None)
        if not isinstance(um, dict):
            self.missing_calls += 1
            return
        try:
            inp = um.get("input_tokens")
            out = um.get("output_tokens")
            tot = um.get("total_tokens")
            if inp is None or out is None:
                self.missing_calls += 1
                return
            cache_read = 0
            det = um.get("input_token_details")
            if isinstance(det, dict):
                cache_read = det.get("cache_read") or 0
            prev = self._acc or {"input_tokens": 0, "output_tokens": 0,
                                 "total_tokens": 0, "cache_read": 0}
            self._acc = {
                "input_tokens": prev["input_tokens"] + int(inp),
                "output_tokens": prev["output_tokens"] + int(out),
                "total_tokens": prev["total_tokens"]
                                + int(tot if tot is not None else inp + out),
                "cache_read": prev["cache_read"] + int(cache_read),
            }
            self.n_calls += 1
        except (TypeError, ValueError):
            self.missing_calls += 1

    def mark_zero(self) -> None:
        """记一次【可证明的 0 次调用】—— 与"什么都没记"**不是一回事**。

        ★ 为什么非要有这个方法（2026-10-14 实测踩到，被 `test_guard.py` 当场抓住）：
          改造前，`/search` 是在**响应里**塞 `_zero_usage()` 那个显式 0，`_gated` 从 resp 取出来
          交给 `LIMITER.record` ⇒ 记成"真的没调"，`usage_missing` 不涨。
          改成从账本取之后，`/search` 这条路一次模型都没调 ⇒ 账本 `take()` 返回 None ⇒
          被记成 `usage_missing` ⇒ **每次 /search 都往"token 账有缺口"上抹一笔假痕**。
          而"假痕一多，真缺口就看不出来了"是本仓库反复剿的东西（`limits.py` 顶部有长注释）。
        ⇒ 所以账本必须能表达"我确实一次都没调"，而不是只有"我没记到"。
        """
        self._acc = _zero_usage()
        self.n_calls = 0
        self._zero = True

    def peek(self) -> dict | None:
        """**看一眼但不清空** —— 给"响应里要带 usage"用。

        ★★ 为什么必须有这个方法（2026-10-14 实测踩到的真 bug，差点漏过去）：
          改造第一版让 `handle_chat` 的四个 return 点继续调 `_take_usage()`，
          而它现在读的是**同一个账本** ⇒ 响应把账取走了，`_gated` 的 finally 再取就是
          `None` ⇒ `LIMITER.record(who, None)` ⇒ **每次 /chat 都记成 `usage_missing`**，
          而响应里的 usage 看起来完全正常（`n_calls=3`），全靠 `/health` 的计数器现形。
          （这正好是盲审 P5 警告的"取两次"，只是方向与它猜的相反：
            它猜'事件先取走、record 拿空'，实际是'响应先取走、record 拿空'。）
        ⇒ 规则改成：**账本只由 `_gated` 清空**；响应侧只能 peek。
          这样"响应里那份"和"记账那份"**取自同一次累加**，天然一致（P5 的要求）。
        """
        if self._zero:
            out = dict(self._acc or {})
            out["n_calls"] = 0
            return out
        if not self._acc or self.n_calls == 0:
            return None
        out = dict(self._acc)
        out["n_calls"] = self.n_calls
        out["missing_calls"] = self.missing_calls
        return out

    def take(self) -> dict | None:
        """取走并清零。**返回 None 的唯一条件：整次请求一次都没成功记账。**"""
        acc, n, missing, zero = self._acc, self.n_calls, self.missing_calls, self._zero
        self._acc, self.n_calls, self.missing_calls, self._zero = None, 0, 0, False
        if zero:
            # 【可证明的 0】：确实一次模型都没调。照实返回 0，**不返回 None**
            # （None 会被 `LIMITER.record` 记成 usage_missing = 假痕）
            out = dict(acc or {})
            out["n_calls"] = 0
            return out
        if not acc or n == 0:
            return None
        out = dict(acc)
        out["n_calls"] = n
        out["missing_calls"] = missing
        return out


def set_current_ledger(ledger: "UsageLedger | None"):
    """绑定/解绑当前上下文的账本。返回一个可传给 `ContextVar.reset()` 的 token。

    ★ 为什么要返回 token 而不是简单地 set(None) 复原：
      请求是**嵌套/回收**线程的，直接设回 None 会把"更外层"的账本一起清掉。
      用 `reset(token)` 才是精确复原。这条在 `/chat` 与 `/search` 共用 `_gated` 之后尤其重要。
    """
    return _CURRENT_LEDGER.set(ledger)


def current_ledger() -> "UsageLedger | None":
    return _CURRENT_LEDGER.get()


def _record_usage(resp) -> None:
    """把一次调用的 usage **累加**掉（取不到就跳过，不填 0）。

    ★ 为什么要区分"没有 usage"和"usage 是 0"：
      填 0 会让"没拿到"看起来像"真的没用 token"，
      而均值里混一个 0 会把它拉低 —— 这与本项目在
      agent 侧踩过的坑同源（`missing_usage` 那一栏就是为此存在的）。
      ⇒ 做法是：这一项取不到就整项不动（保留之前累加出来的数），
        并且把 missing_calls 加一，让"漏了几次"本身也变成一个可读的数。

    ★★★ 必须是【累加】而不是【覆盖】。踩过的坑：
      原来这里写的是 `_LLM_USAGE.last = {...}` —— 每调一次就把上一次冲掉。
      而一次 /chat 要调三次模型（查询改写 → rerank 打分 → 生成），
      于是返回的 usage 只剩【最后一次】（生成）那一段，
      input_tokens 报 157 而真实值在 700上下 —— 少算了三分之二。
      更有欺骗性的是 n_calls：因为 n_calls 是在同一个赋值里现算的
      `getattr(_LLM_USAGE, "n_calls", 0) + 1`，而那个属性从没人写过，
      每次都读到 0 ⇒ 恒等于 1 ⇒ 【每一次】请求都显示只调了 1 次模型。
      这两个数一起看像"合理"，实际全错 —— 只能靠"拿真实响应逐段对照"发现。

    ★ 2026-10-14：有显式账本就写账本（流式/并发安全），
      没有就退回旧的线程局部 —— **测试与脚本的既有调用点因此一个字都不用改**。
    """
    led = current_ledger()
    if led is not None:
        led.record(resp)
        return

    um = getattr(resp, "usage_metadata", None)
    prev = getattr(_LLM_USAGE, "acc", None) or {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_read": 0,
    }
    n_calls = getattr(_LLM_USAGE, "n_calls", 0)
    missing = getattr(_LLM_USAGE, "missing_calls", 0)
    if not isinstance(um, dict):
        # ★ 这次没拿到，但之前累加的数要保住 —— 不能整段清零
        _LLM_USAGE.missing_calls = missing + 1
        return
    try:
        inp = um.get("input_tokens")
        out = um.get("output_tokens")
        tot = um.get("total_tokens")
        if inp is None or out is None:
            _LLM_USAGE.missing_calls = missing + 1
            return
        cache_read = 0
        det = um.get("input_token_details")
        if isinstance(det, dict):
            cache_read = det.get("cache_read") or 0
        _LLM_USAGE.acc = {
            "input_tokens": prev["input_tokens"] + int(inp),
            "output_tokens": prev["output_tokens"] + int(out),
            "total_tokens": prev["total_tokens"]
                           + int(tot if tot is not None else inp + out),
            "cache_read": prev["cache_read"] + int(cache_read),
        }
        # ★ 真正写进【线程属性】—— 原来漏了这一行，n_calls 才恒等于 1
        _LLM_USAGE.n_calls = n_calls + 1
    except (TypeError, ValueError):
        _LLM_USAGE.missing_calls = missing + 1


def _usage_for_response(led: "UsageLedger | None" = None) -> dict | None:
    """拼响应时取 usage：**有账本就 peek（不清空），没有就走旧路径**。

    ★ 为什么不清空：账本由 `_gated` 在 finally 里**唯一一次** take 掉（见 `peek()` 的注释）。
      `handle_chat` 只是"把这次的账也放进响应里给调用方看"，不是消费它。
    """
    if led is not None:
        return led.peek()
    return _take_usage()


def _take_usage() -> dict | None:
    """取走并清空当前账本（跨调用要清，否则会重复计数）。

    ★ 返回 None 的条件只有一种：整次请求【一次都没成功记账】。
      「记到了 N 次、其中 M 次拿不到」是有效数据，要如实返回并带上 missing_calls。
    ★ 2026-10-14：同 `_record_usage` —— 有显式账本走账本，否则走旧的线程局部。
      ⚠️ 取账只认**当前上下文**那一个账本：流式下事件与 `_gated` 的记账必须**共用同一次 take**
        （盲审 P5：take 两次，第二次拿到 None，于是真账被事件拿走、record 记成缺失）。
    """
    led = current_ledger()
    if led is not None:
        return led.take()

    acc = getattr(_LLM_USAGE, "acc", None)
    n = getattr(_LLM_USAGE, "n_calls", 0)
    missing = getattr(_LLM_USAGE, "missing_calls", 0)
    _LLM_USAGE.acc = None
    _LLM_USAGE.n_calls = 0
    _LLM_USAGE.missing_calls = 0
    if not acc or n == 0:
        return None
    out = dict(acc)
    out["n_calls"] = n
    # ★ 记到了但有漏的，也算非零 —— 报告侧据此判断这批数据能不能信
    out["missing_calls"] = missing
    return out


def _invoke_llm(llm, messages, timeout=None):
    """调一次大模型，最多等 timeout 秒；超时和【调用期】报错统一包装成 LLMCallError。

    ★ 2026-09-27 把"调用期"三个字说清楚（原来只写"报错"，是句假话）：
      这里 try 包的是 fut.result(...)，也就是【模型已经在跑之后】出的错。
      而 `llm` 这个实参是在【调用本函数之前】求值的 —— service.py 里两处写法
      `_invoke_llm(self.llm, ...)` 会先触发 RAG.llm 属性去构造 ChatDeepSeek，
      构造期抛的错（典型：没配 DEEPSEEK_API_KEY → pydantic ValidationError）
      【根本进不到这个 try】，会以原始异常一路裸奔出去。
      ⇒ 这是刻意的，不是遗漏：缺 key 是【配置错误】，应当直接炸出来让人看见，
        不该被包装成"调用失败"再降级成 200（那正是本仓库在剿的静默失败面）。
        真正该改的是这句话的措辞，而不是去 catch 它。

    为什么要用线程池包这一层？
        llm.invoke() 是同步阻塞的，模型卡住时整个接口会跟着卡死（请求堆积 → 服务不可用）。
        丢进线程池后用 future.result(timeout=...) 取结果，主线程最多等 timeout 秒就走人。

    ★ timeout 参数这里默认写成 None、在函数体内再去读 LLM_TIMEOUT_SEC，
      而不是直接写成 timeout=LLM_TIMEOUT_SEC —— 因为 Python 的默认参数在
      【函数定义的那一刻】就求值完毕了，写死的话以后改 LLM_TIMEOUT_SEC
      （或者测试里 monkeypatch 它）都不会生效，又是一个"看着能配、其实配不动"的坑。

    ★ 坦白一个不完美的地方：超时后那个后台线程其实还在跑（cancel() 对已经开始执行的
      任务无效），它不会拖垮接口，但也没法真正掐断 —— 这是同步调用的天花板，
      要彻底解决得改异步 + 客户端级超时，属于下一阶段的事，这里先记着。
    """
    if timeout is None:
        timeout = LLM_TIMEOUT_SEC
    fut = _LLM_POOL.submit(llm.invoke, messages)
    try:
        resp = fut.result(timeout=timeout)
        _record_usage(resp)          # ★ 旁路记账，不改返回值
        return resp
    except FuturesTimeout:
        fut.cancel()
        raise LLMCallError(f"大模型调用超时（超过 {timeout:g} 秒）")
    except LLMCallError:
        raise
    except Exception as e:
        raise LLMCallError(f"大模型调用失败：{type(e).__name__}: {e}")


def norm(text):
    return re.sub(r"[\s\W_]+", "", text)


def _mask_pii(text):
    """把关拦下的资料若含隐私，返回前脱敏 —— 否则 /guard-report 会把真实手机号/身份证原样吐出去。"""
    t = text
    for pat, name in PII_PATTERNS:
        t = re.sub(pat, f"【{name}已隐匿】", t)
    return t


def _version_key(v: str):
    """把 version 字符串解析成可比较的元组：'2026-09-25' → (2026, 9, 25)。解析不出来返回 None。

    ★ 为什么不能直接比字符串（2026-09-25 实测）：
        '2026-9-5' > '2026-09-25' 结果是 **True** —— 字符串是逐位比字符编码的，
        比到第 6 位 '9' > '0' 就下结论了，它不知道"9 月 < 10 月"这层意思。
        后果不是"报个错"，而是【新旧判反】：一个没补零的旧版会把库里真正的新版挤掉，
        而拒绝理由还写成「被新版取代（2026-09-25 → 2026-9-5）」—— 说得越确定越误导。
        这正是本项目最忌讳的一类 bug：行为错了，日志却很笃定。

    ★ 为什么解析失败【不】悄悄退回字符串比较：
        那等于"看着校验过了，其实没有"，比不校验更危险 ——
        下一个读代码的人会以为这里已经安全了。

    ★ 语法点（两处生成器表达式，都是"边算边取、不先建整个列表"的写法）：
        all(p.isdigit() for p in parts)  —— 逐个判断是否全是数字字符，遇到第一个 False 就停
        tuple(int(p) for p in parts)     —— 逐个转成 int，再打包成元组
      必须 int() 转数字：元组比较是逐项按【数值】比，(2026, 9, 5) < (2026, 9, 25) ✓
      而字符串元组 ('9',) > ('25',) 就又变成字符比较了，等于白改。
    """
    parts = v.strip().split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None                      # 不是 YYYY-MM-DD，比不出新旧
    return tuple(int(p) for p in parts)


# ==================================================================
# ①.5 口语 → 制度语 归一化（★ 只作用于检索，不碰精排/生成的 prompt）
# ==================================================================
# ★ 为什么需要它：
#   q12「我要请一个礼拜的假，需要哪一级批准？」在 263 块真实语料上稳定漏掉 ——
#   学生说"一个礼拜"，而 `学生请销假制度.md｜总则-第三条` 写的是
#   「一周以内由二级学院审批；一周以上由二级学院、学生工作处(部)、教务处审批」。
#   · BM25 走字面：jieba 切出的"礼拜"在整库一次都不出现 ⇒ 该题 BM25 排第 80 名
#   · 向量路只沾到一点语义 ⇒ 第 7 名
#   · RRF 把两路【相加】⇒ 融合后排第 10 名，掉出前 5（粗捞池只有 5）
#   ⇒ 病根是【词表没对齐】，不是融合公式错，也不是"混合检索没用"。
#
# ★ 纪律：这张表必须【极小 + 每条都能指到语料原文】。
#   它是在同一套 15 题上量出来的（这套题既是调参集也是报数集），
#   所以它证明的是"这个已知的口语差异被修掉了"，
#   **不是**"系统对口语的泛化能力变好了"。后者要先有独立出题集（evalset/heldout/）。
#   加新词之前先问：语料里那个词长什么样？答不出来就别加。
_QUERY_NORMALIZE: tuple[tuple[str, str], ...] = (
    ("一个礼拜", "一周"),   # 语料原文：一周以内由二级学院审批（请销假制度 总则-第三条）
    ("一礼拜",   "一周"),   # 同上的省略写法
    ("礼拜",     "周"),     # 兜底（"礼拜三"这类）
)


def normalize_query(q: str) -> str:
    """把口语说法换成语料里的制度说法。

    长的模式排在前面，否则"一个礼拜"会先被"礼拜"拆成"一个周"。
    """
    out = q
    for src, dst in _QUERY_NORMALIZE:
        out = out.replace(src, dst)
    return out


# ★ 查询改写用的 prompt —— 与 normalize_query() 是同一个目的的**两级**：
#   那张手抄同义词表零成本、确定性，但只覆盖我想到的说法（礼拜→周）；
#   模型改写能处理没想到的，代价是每次查询多一次调用。
#   ★ 这个 prompt 里**不许出现任何评测题**，否则就是把 held-out 变成调参集。
REWRITE_PROMPT = """把下面这句学生的口语问题，改写成**学校规章制度里会用的说法**，用于检索。

要求：
1. 只输出改写后的一句话，不要解释、不要引号、不要编号。
2. 保留原问题的**意图和关键限定**（时间、对象、金额、情形都不能丢）。
3. 把口语词换成制度用语（例："一个礼拜"→"一周"；"翘课"→"旷课"；"直接开掉"→"开除学籍"）。
4. 如果原句已经很书面，就原样返回，别画蛇添足。

学生的问题：{q}
改写后："""


# ==================================================================
# ② 全局资源：模型和库只在【服务启动时】加载一次
#    ★ 这是服务化最重要的一条：绝不能在每个请求里重新加载模型！
# ==================================================================
class RAG:
    """把所有重家伙（模型、向量库）装在里面，全局只有一份"""

    def __init__(self):
        self.ready = False
        self._llm = None          # 真正的大模型对象，第一次用到才建（见下面的 llm）
        # ★ 元信息不合格而被跳过的文件（2026-10-12 补）：原来它只活在 startup 那一瞬的
        #   日志里，服务起来之后【没有任何接口】能告诉你"我少加载了几个文件"。
        #   见 health() 与 kb/loader.py 里那句被修正的注释。
        self.load_errors: list[str] = []
        # ★★ 2026-10-12 修：chunks / rejected 原来【只在 startup() 里赋值】，
        #   而 startup() 要跑十几秒（加载模型 + 建库）。
        #   ⇒ 服务刚起来的那十几秒里访问 /health，会在 len(rag.chunks) 上抛
        #     AttributeError → **返回 500，而不是 health() 文档里承诺的 status="loading"**
        #     ——也就是说那个 loading 分支根本走不到，健康检查在最需要它的时候是坏的。
        #   （CI 没发现，是因为容器 job 的探活用 `curl -sf`：`-f` 遇 5xx 就失败，
        #     它只是在继续轮询，而不是读到了 loading。）
        #   ⇒ 先把它们初始化成空，让 /health 在启动期真的能回答。
        self.chunks: list[dict] = []
        self.rejected: list[tuple] = []

    @property
    def llm(self):
        """大模型【第一次要用的时候才创建】。

        @property 的作用：让 `rag.llm` 用起来像一个普通属性（不用加括号），
        但每次访问它会走一遍这个函数 —— 也就是"用到才建、只建一次"。

        为什么要这么绕，不在 startup() 里直接建？
            ChatDeepSeek(...) 在【构造的时候】就会校验 DEEPSEEK_API_KEY，
            没配 key 直接抛：
                ValidationError: If using default api base, DEEPSEEK_API_KEY must be set.
            那样一来，只想跑入库把关自检（这条路径压根用不到大模型）的人也会被卡住 ——
            面试官 clone 下来第一步就失败，CI 也跑不了。
        ★ 自检 / 测试这条路径全程不碰 llm，所以没 key 也能跑完。
        """
        if self._llm is None:
            # max_retries=2：遇到限流或瞬时网络抖动时自动重试 2 次。
            # ★ 这个字段是真存在的（实测 ChatDeepSeek.model_fields 里有），而且
            #   【默认值是 None，也就是不重试】—— 所以必须显式写上才有效果。
            #   （对照：timeout 字段并不存在，写了会被静默忽略，见 LLM_TIMEOUT_SEC 的注释。）
            self._llm = ChatDeepSeek(model="deepseek-chat", temperature=0,
                                     max_retries=2)
        return self._llm

    def startup(self):
        t0 = time.time()
        self.model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=CACHE_DIR)
        # ★ 这里【故意不建 LLM】，原因见上面的 llm 属性

        # ★ 知识库从 docs/ 加载（不再写死 RAW_DOCS）：
        #   kb 先、inbox 后，保证「干净条款先入、脏副本作为重复/旧版被拦」，
        #   而不是反过来把干净条款挤掉。loader 不碰模型，可独立单测。
        raw_docs, load_errors = load_documents(DOCS_DIR)
        self.load_errors = list(load_errors)      # ★ 存下来给 /health 看，别只打一行日志
        if load_errors:
            logger.warning(f"有 {len(load_errors)} 个文件解析失败（缺元信息/编码错），已跳过：")
            for e in load_errors:
                logger.warning(f"   - {e}")
        if not raw_docs:
            logger.warning("没有从 docs/ 加载到任何文档！检查 DOCS_DIR 路径与文件元信息。")

        kept, rejected = self._guard(raw_docs)
        self.chunks = kept
        self.rejected = rejected
        self.vectors = np.array(
            list(self.model.passage_embed([c["text"] for c in kept])), dtype="float32")
        # BM25 索引（字面检索）：jieba 分词后建索引，和向量检索互补
        #   向量看"意思像不像"，BM25 看"字面有没有出现" —— 两者都会单用时翻车，见 search() 注释
        self.bm25 = BM25Okapi([list(jieba.cut_for_search(c["text"])) for c in kept])
        self.ready = True
        self.boot_time = time.time() - t0

    def _guard(self, docs):
        """六道关卡 + 版本冲突。

        ⚠️ **关卡顺序不再与 experiments/step5_ingest_guard_demo.py 一致**（2026-10-12）：
        废止声明关是本项目后加的，step5 里没有，所以"顺序一致"只对前五道成立。
        这不是笔误，是**两处代码从此分叉**了 —— 要对齐得改step5，不要改这边。

        关卡 1 太短碎屑 → 关卡 5.5 废止声明 → 关卡 2 完全重复 → 关卡 3 近似重复
        → 关卡 4 个人隐私 → 关卡 5 缺少来源/日期（出问题无法追溯）。

        ⚠️ 2026-10-12 修正：这行docstring 原来写的是「1→2→3→4→5」五关连排，
        **漏掉了 5.5**，而代码里 5.5 实际排在关卡 2 之后 —— 文档与实现不一致。
        这次把 5.5 **真的挪到了关卡 3 近似重复之前**（理由见那一段的注释），
        所以顺序才改成上面这样。
        ⇒ 教训与「关卡 5」那段是同一条：**别靠记忆判断关卡顺序，去看代码。**

        ★ 关卡 5 是 2026-09-23 补上的，补的理由值得记一笔：
          之前这里只移植了前 4 关，docstring 却写着"五道关卡" ——
          **文档说五关、实现只有四关**，这本身就是缺陷：读者以为有兜底，其实没有。
          当时没移植是怕"来源白名单"把自有文档也拦掉；去读 step5 的代码
          （step5_ingest_guard_demo.py:212）才发现，它只判断 source/version
          **是否为空**，压根不做白名单 —— 自有文档本来都带这两个字段，
          移植过来零误伤。别靠记忆判断，去看实现。

        ⚠️ 关卡 5 的边界要说清楚，别当它是万能：
          它只保证 source/version 字段【非空】，不保证 source【可信】。
          一份自己填了 source=官网帮助中心 的文档照样能过这一关。
          所以 docs/inbox/ 依然是「待人工审核区」—— 审完才移进 docs/kb/，
          不要把 inbox 当可信来源（入库入口见 kb/loader.py）。

        ★ inbox 里其实是【两类】东西，用途完全不同，别混：
          ① 待审副本 —— 审完会被移进 docs/kb/。
          ② **明知是脏的「标本」** —— 永久留在 inbox，唯一用途是持续证明
             「把关在真实数据上真的在拦」。docs/inbox/网页抓取残留.md 就是标本：
             15 条全是从真实网页抓下来的导航残留，实测全被关卡①拦下、一条都进不了库。
             标本不是待审队列 —— 它永远不会通过审核，也不该通过。
             所以别看到 inbox 里还有文件，就以为"审核积压了"。
        """
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
        # ==================================================================
        # 关卡 5.5：废止声明不进库（2026-10-10 加，判据见 STALE_MARK 处的说明）
        # ==================================================================
        # ★ 为什么必须在【近似重复关之前】：
        #   实测只喂 3 条废止声明，现有关卡的结果是「拦 1 条留 2 条」，
        #   被拦那条的理由写的是「近似重复」——
        #   而它们相似度高达 0.9560 / 0.9517，谁先入库谁就留下，另一条被当重复删。
        #   ⇒ ① 留几条不确定（取决于顺序）② 理由完全误导（真正原因是"这是废止声明"）。
        #   所以这一关要走在前面，让它们带着【正确的理由】被拦。
        stage55 = []
        for c in kept:
            why = _stale_clause_reason(c["text"])
            if why:
                rejected.append((c, why))
            else:
                stage55.append(c)
        kept = stage55


        vecs = np.array(list(self.model.passage_embed([c["text"] for c in kept])), dtype="float32")
        stage3 = []
        for i, c in enumerate(kept):
            # ★ stage3 里存的是【下标】，所以要比对的直接就是 vecs[j]。
            #   不能写成 range(len(stage3)) —— 那样 j 变成"第几个通过的"，
            #   一旦前面拦掉过东西，两套编号就错位了：
            #   新块会去比【已被拦掉的】，却漏掉【真正该比的已通过块】，于是漏拦。
            #   tests/test_guard.py::test_near_dup_after_earlier_rejection 守住这里。
            dup = any(float(vecs[i] @ vecs[j]) > DUP_THRESHOLD for j in stage3)
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

        # 关卡5 缺少来源/日期：连出处都记不下来的内容，出了纠纷无法追溯，不收。
        #   和 step5 的差别只有一处：这里用 c.get(...) 而不是 c["source"]。
        #   万一调用方递进来的 dict 少一个键，应当【拦下并说明原因】，
        #   而不是抛 KeyError 把整个服务带崩 —— 把关的失败方式也该是"拦"，不是"炸"。
        stage5 = []
        for c in kept:
            if not c.get("source") or not c.get("version"):
                rejected.append((c, "缺少来源/日期，出问题无法追溯"))
            else:
                stage5.append(c)
        kept = stage5


        # 版本冲突：同一 (doc, clause) 只留 version 最大的
        # ★ 这里的 key 必须是 (文档, 条款) 两样一起，不能只用文档名！
        #   只用文档名的话，"退款政策.md"里【已发货】和【未发货】是两条完全不同的规定，
        #   会被误判成"同一条的新旧两版"，结果一条把另一条挤掉 —— 库里凭空少一条规则。
        newest = {}
        for c in kept:
            key = (c["doc"], c["clause"])          # ← 两样一起当身份证
            if key not in newest:
                newest[key] = c
                continue
            old = newest[key]
            # ⚠️ 将来要做「超长块切成多块」时，这里有个坑必须先看（2026-09-25 实测）：
            #   如果切出来的多块沿用**同一个 clause 名 + 同一个 version**，
            #   `c["version"] > old["version"]` 是 False → 走 else 分支 →
            #   第 2 块起全被当成"旧版本"拦掉，而且拒绝理由写的是
            #   「库里已有更新的 2026-01-01」——**完全误导**（其实是同一个版本）。
            #   这不是版本冲突，是带着错误日志的静默丢数据。
            #   → 所以真要切多块，块名必须错开人工命名空间：
            #     人工子条款用「（一）（二）」，自动切的用「#2」「#3」。
            new_k = _version_key(c["version"])
            old_k = _version_key(old["version"])
            if new_k is None or old_k is None:
                # 比不出新旧时【不许猜】—— 明确拦下并说清为什么。
                # 两个方向的错误不对称：宁可"新来的一条进不了库、但有日志说明原因"，
                # 也不要"猜错了、把库里正确的那一版挤掉"（和 DUP_THRESHOLD 宁严勿松同一个道理）。
                rejected.append((c, f"版本号不是 YYYY-MM-DD，无法比较新旧：{c['version']!r}"))
            elif new_k > old_k:                     # 新来的确实更新 → 换掉旧的
                rejected.append((old, f"旧版本，被新版取代（{old['version']} → {c['version']}）"))
                newest[key] = c
            else:                                   # 旧版本 / 同版本后来者 → 新来的被拦
                rejected.append((c, f"旧版本，库里已有更新的 {old['version']}"))
        kept = list(newest.values())
        return kept, rejected

    def rewrite_query(self, q: str) -> str:
        """把口语问题改写成制度用语 —— **只用于检索**。

        ★ 为什么需要它（held-out 实测，不是猜的）：
          44 道应答题上只做**一次**改写，就有 7 道从"捞不到"变成"捞到"、0 道被弄坏
          （McNemar p=0.0156）；Recall@5 从 35/44 提到 42/44。
          修好的里面包括 h8（勤工助学每月工时）与 h23（处分 × 奖助学金）——
          这两道连"换更大的 embedding 模型"都救不回来
          （见 evalset/heldout/RESULTS.md 三之补三）。
        ★ 与 normalize_query() 的分工：那张表零成本、确定，但只覆盖我想到的说法；
          改写处理没想到的。两级串联：先过表，再让模型改写。
        ★ 代价：每次查询多一次模型调用。失败时**退回原问题**（产品不该因此不答）。
          ——注意这个兜底只适合产品；探针里兜底会把"探针坏了"伪装成"改写没用"。
        """
        resp = _invoke_llm(self.llm, [HumanMessage(content=REWRITE_PROMPT.format(q=q))]).content
        out = (resp or "").strip().strip('"').strip()
        if not out or len(out) > 120:          # 模型跑偏（空 / 超长）→ 不用它
            logger.warning("查询改写结果异常（长度 %d），本次用原问题检索", len(out))
            return q
        return out

    def route_orders(self, query):
        """两路检索的排名 —— **生产 search() 与三路消融探针共用这一个入口**。

        ★ 为什么要抽出来：`evalset/probe_ablation.py` 原本自己 embed、自己分词，
          复制了一份检索实现。2026-10-10 给检索加了口语归一化之后，
          `run_eval --report` 报 Recall@5 15/15，而探针仍报 q12 融合第 10 名 ——
          **因为探针绕过了 search()，拿不到那一步**。
          这正是本项目反复踩的"两处各写一份判据"：改了一处，另一处照旧。
          ⇒ 唯一入口就是这里，探针也必须从它取名次。

        返回 (归一化后的 query, 向量分, 向量排名, BM25 排名)。
        """
        query = normalize_query(query)
        q = np.array(list(self.model.query_embed([query])), dtype="float32")[0]
        vec_scores = self.vectors @ q                       # 每块与问题的向量相似度
        vec_order = np.argsort(-vec_scores)                 # 按分数从高到低的下标
        tokens = list(jieba.cut_for_search(query))          # 中文按"检索粒度"分词
        bm25_order = np.argsort(-self.bm25.get_scores(tokens))
        return query, vec_scores, vec_order, bm25_order

    def search(self, query, k=5):
        """混合检索：向量（看意思）+ BM25（看字面），用 RRF 融合两路的排名

        为什么不只用向量？（都是本项目实测过的翻车现场）
            · "东西坏了能修吗"（文档里写的是"保修"）→ 向量能找到，BM25 找不到
            · "A1001 运单号"（三条只差编号）→ BM25 稳，向量第一二名只差 0.008（拿不准）
        两路互补，谁也别丢。

        ★ RRF（倒数排名融合）：每路的贡献是 1/(60+名次)。
          用【名次】而不是【原始分数】，是因为向量分在 0~1 之间、BM25 分能到十几，
          直接相加的话 BM25 会把向量完全淹没 —— 不同量纲的分数不能相加，这是常见踩坑。
        """
        # 口语归一化 + 两路排名都在 route_orders 里（探针走的是同一个入口）
        _, vec_scores, vec_order, bm25_order = self.route_orders(query)

        rrf = {}
        for rank, idx in enumerate(vec_order):
            rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rank)
        for rank, idx in enumerate(bm25_order):
            rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rank)

        top = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:k]
        # 返回向量分只是为了展示（网页上那个"向量分"标签），真正决定顺序的是 RRF
        return [(self.chunks[idx], float(vec_scores[idx])) for idx, _ in top]

    def rerank(self, query, candidates):
        """让大模型给每条候选打分，返回 [(块, 向量分, 精排分, 理由)]，按精排分从高到低

        为什么两个分数都要留着？
            向量分 = 粗捞阶段的（电脑按字面/语义相似度算的，快但粗）
            精排分 = 大模型重新看的（慢但准）
        两个都返回，你就能在网页上直观看到"粗捞排第一的，精排未必第一" —— 这正是 rerank 的意义。
        """
        # ★ 精排要让模型判断"这条到底能不能答"，必须给它全文
        #   （截断掉的那部分里可能正有用户要的那个值）
        numbered = "\n".join(f"{i}. {c.get('text_full', c['text'])}"
                             for i, (c, _) in enumerate(candidates, 1))
        prompt = (
            "判断下列每条资料对回答这个问题有多大帮助。\n"
            f"问题：{query}\n\n候选资料：\n{numbered}\n\n"
            "只输出 JSON：{\"scores\":[{\"id\":1,\"score\":0到10的整数,\"reason\":\"一句话\"}]}"
        )
        resp = _invoke_llm(self.llm, [HumanMessage(content=prompt)]).content
        raw = re.sub(r"^```(?:json)?|```$", "", resp.strip(), flags=re.M).strip()
        try:
            data = json.loads(raw)
            sc = {int(x["id"]): (int(x["score"]), x.get("reason", "")) for x in data["scores"]}
        except Exception:
            # ★ 这里【绝不】再静默给全 0 分 —— 那正是最危险的一种写法：
            #   "模型返回了垃圾" 会被伪装成 "库里没有这个东西"（全 0 分 → 触发拒答），
            #   调用方看到的是一条平平无奇的拒答，故障就被永久掩盖了。
            #   现在改成：记下原始返回 + 抛 LLMCallError，由 /chat 决定降级还是暴露。
            logger.error("rerank 返回的内容不是合法 JSON，原始返回前 300 字符：%r", raw[:300])
            raise LLMCallError("rerank 返回非 JSON，无法解析评分")
        # 大模型只回 id 和分数，我们按 id 把【原始候选】捞回来（candidates 下标从 0 开始，id 从 1 开始）
        out = []
        valid_ids = range(1, len(candidates) + 1)
        for i, (score, reason) in sorted(sc.items(), key=lambda kv: kv[1][0], reverse=True):
            # ★ id 越界校验：大模型会幻觉出不存在的 id（超出范围、0 甚至负数），
            #   以前直接 candidates[i - 1] 会 IndexError → 接口裸 500。现在跳过并记日志。
            if i not in valid_ids:
                logger.warning("rerank 回了越界的 id=%s（本次候选只有 %d 条），已跳过",
                               i, len(candidates))
                continue
            chunk, vec_score = candidates[i - 1]
            out.append((chunk, float(vec_score), score, reason))
        return out

    @staticmethod
    def build_answer_prompt(question, chunks) -> str:
        """拼生成用的 prompt —— **`answer()` 与流式端点共用的唯一实现**。

        ★ 为什么抽成 staticmethod 而不是让流式那条自己拼一遍：
          两条路必须喂**完全一样**的资料与措辞，否则"流式和非流式答的是同一件事"
          就成了靠注释承诺的话（本仓库为这种"两处各写一份"栽过好几次）。
          抽出来之后，测试可以直接比两边的 prompt 字符串。
        """
        ctx = "\n".join(f"- {c.get('text_full', c['text'])}" for c in chunks)
        return ("你是学校学生事务的答疑助手。只根据下面的资料回答，资料里没有的就明确说不知道。\n"
                f"资料：\n{ctx}\n\n问题：{question}")

    def answer(self, question, chunks):
        # ★ 生成同样用全文：喂给模型的资料不该比库里的少
        prompt = self.build_answer_prompt(question, chunks)
        # 超时 / 限流 / 网络错误都会被 _invoke_llm 统一转成 LLMCallError 抛出来，
        # 不再让异常一路裸奔成 500 —— /chat 会接住它、记日志并降级。
        return _invoke_llm(self.llm, [HumanMessage(content=prompt)]).content

    def answer_stream(self, question, chunks):
        """流式生成：**逐块 yield 文本增量**，并在最后一块把 usage 记进账本。

        ★ 超时怎么算（这是流式与 `_invoke_llm` 最大的不同）：
          `_invoke_llm` 用 `future.result(timeout=)` 兜超时，而流式是**迭代**，
          没法用一个 timeout 覆盖整段 ⇒ 改成检查**整段已耗时**是否超 `LLM_TIMEOUT_SEC`。
          ⚠️ 这不是同一个语义：`_invoke_llm` 是"等结果最多 N 秒"，这里是"整段最多 N 秒"。
            两者都不是"能掐断"（同步调用的天花板，README 已记）。
        ★ 记账：每块都过一遍 `_record_usage` —— 正文块没有 usage 会被它忽略（只计数不记 token），
          最后那块带 `usage_metadata` 才真正累加。**实测确认 DeepSeek 在末块带 usage**。
        """
        prompt = self.build_answer_prompt(question, chunks)
        deadline = time.time() + LLM_TIMEOUT_SEC
        for chunk in self.llm.stream([HumanMessage(content=prompt)]):
            _record_usage(chunk)                       # ★ 旁路记账，不改返回值
            text = getattr(chunk, "content", None)
            if text:
                yield text                            # 只在真的有正文时 yield（首块常常是空串）
            if time.time() > deadline:
                raise LLMCallError(f"流式生成超时（整段超过 {LLM_TIMEOUT_SEC:g} 秒）")


rag = RAG()


# ==================================================================
# ③ FastAPI：把函数变成 HTTP 接口
# ==================================================================
# ==================================================================
# 限流 + 日配额：进口处的那道闸（实现在 limits.py）
# ==================================================================
# ★ 为什么服务层只留这三行：判定逻辑（令牌桶 / 日额度 / 故障模式）全部在 limits.py 里，
#   它不 import service，可以脱离模型和 HTTP 单独测（tests/test_limits.py 跑了 38 个用例）。
#   服务这边只负责一件事：**把 HTTP 请求翻译成"谁"**，然后在 /chat 前后各问一次。
#
# ★ 配置全走环境变量（.env / compose 都能给），默认值见 limits.Limits：
#   REDIS_URL          给了就用 Redis（多进程共享），不给就用进程内内存（单进程）
#   RATE_BURST / RATE_PER_MIN / DAILY_REQ_QUOTA / DAILY_TOKEN_QUOTA
#   LIMIT_FAIL_MODE    closed（默认，存储挂了就拒绝服务=护钱包）| open（放行=保可用）
#   TRUST_PROXY_HEADERS / ALLOW_CLIENT_ID_HEADER   身份怎么认，默认都不信（见 limits.client_id）
LIMITER = limits_mod.build_limiter(os.environ, log=logger)

# ★ 本次进程的身份（2026-10-13 加，起因是盲审 F1）：见 /health 里那段说明。
BOOT_ID = uuid.uuid4().hex[:12]


# lifespan：服务启动时干一次（加载模型），关闭时收尾。
# @ 开头的叫【装饰器】，它把下面那个函数"包装"一下交给框架 —— 你不用手动调用它。
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("服务启动中：加载模型 + 建库（只在启动时做一次）……")
    # ★ 启动时把限流口径打出来。为什么值得单独一行日志：
    #   限流配错（比如以为有 Redis 结果用了进程内存储）**不会报任何错**，
    #   只会安静地"限额被放大 N 倍"。日志 + /health 是唯一能看见它的地方。
    _st = LIMITER.stats()
    logger.info("限流：存储=%s（可用=%s） 突发=%d/桶 速率=%g/分 日请求=%d 日token=%d 故障模式=%s",
                _st["store"], _st["store_ok"], LIMITER.limits.burst, LIMITER.limits.per_min,
                LIMITER.limits.daily_req, LIMITER.limits.daily_tokens, LIMITER.limits.fail_mode)
    if _st["store"] == "memory":
        logger.warning("★ 限流用的是【进程内存储】⇒ 只对单进程有效："
                       "uvicorn 多 worker / 多副本下每个进程各算各的，配额会被放大。"
                       "要真限流请设 REDIS_URL（docker compose 里已经配好）。")
    rag.startup()
    logger.info(f"就绪：{len(rag.chunks)} 块入库，拦下 {len(rag.rejected)} 块，"
                f"耗时 {rag.boot_time:.1f} 秒")
    yield                       # ← 这一行表示"服务开始接客"
    logger.info("服务关闭")


app = FastAPI(
    title="校园政策问答",
    description=(
        "RAG 拒答服务：入库把关六道关卡 + BM25/向量混合检索（RRF） + "
        "大模型精排。精排最高分 < 5 时直接返回，不调用生成模型。"
    ),
    lifespan=lifespan,
)


# pydantic 模型：规定"请求体必须长这样"，传错了框架自动返回 422，不用你写判断
class ChatRequest(BaseModel):
    # max_length=500：不加这道闸的话，别人可以丢一篇几万字的问题过来 ——
    #   ① 这段文本会被原样拼进 prompt，直接烧掉大量 token（费钱又慢）；
    #   ② 极端情况下会顶爆模型的上下文窗口，本来能答的问题也变成报错。
    #   pydantic 会在入口处就挡掉并返回 422，不用我们自己写 if 判断。
    question: str = Field(..., description="用户的问题", min_length=1, max_length=500)
    top_k: int = Field(5, description="粗捞几条", ge=1, le=10)
    use_rerank: bool = Field(True, description="是否启用 rerank 精排")
    # ★ None = 跟着 use_rerank 走（见 chat() 里的说明）：
    #   产品默认路径（rerank）本来就要调模型；离线路径不许偷偷调模型。
    use_rewrite: bool | None = Field(None, description="是否用 LLM 改写检索查询（None=跟着 use_rerank）")


class ChatStreamRequest(BaseModel):
    """`/chat/stream` 的请求体 —— 与 `ChatRequest` 的区别与理由。

    ★ 为什么**单独一个模型**而不是给 `ChatRequest` 加字段：
      `ChatRequest` 是 `/chat` 的对外契约，它的字段集被 `tests` 与 A/B 脚本依赖
      （"除这次改动外逐字段不变"是能被验证的承诺）。把流式开关塞进去，
      等于让两条路的契约耦合 —— 而流式的语义（SSE、状态码回不去）本来就不同。
    ★ `use_rerank` 刻意**不留**（与 `/search` 当初的理由同源）：这条端点的全部意义是
      "边生成边推"，关掉精排就退化成粗捞直出，而那条路已经有 `/search` 了。
      留一个开关 = 留一条没人测的后门。
    """

    question: str = Field(..., description="用户的问题", min_length=1, max_length=500)
    top_k: int = Field(5, description="粗捞几条", ge=1, le=10)


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
    # 是否命中了知识库。False = 库里没有，已拒答，一条资料都没喂给模型。
    # ★ 这个字段是"可追溯"的一部分：调用方能明确区分「答错了」和「拒绝作答」。
    knowledge_hit: bool = True
    # ★ Token 计量（2026-10-12 加）。取不到时是 None 而不是 0 ——
    #   填 0 会让"没拿到"看起来像"真的没用 token"。
    #   默认 None 让旧调用方拿不到时也不报错。
    usage: Optional[dict] = None


class SearchRequest(BaseModel):
    """`/search` 的请求体：只检索、不生成（工具 `search_policy` 用）。

    ★ 只有两个字段，**故意没有** use_rerank / use_rewrite：
      这条端点的契约就是"一个 LLM 调用点都没有"（见 `handle_search`）。
      在这里留一个开关，等于给"哪天有人把它打开"留了后门，
      而整条链（工具侧报账、token 报告、README 的数字）都建立在"它恒为 0"上。
    """
    question: str = Field(..., description="检索什么", min_length=1, max_length=500)
    top_k: int = Field(5, description="粗捞几条", ge=1, le=10)


class SearchResponse(BaseModel):
    """`/search` 的响应：`sources` / `knowledge_hit` 与 `/chat?use_rerank=False` **必然一致**。

    ★ 没有 answer 字段 —— 这不是"少返回点东西"，而是这条端点存在的全部理由：
      服务不再生成一段**没人读**的答案（实测每次 ~488 token，占工具侧总账的 24%）。
    """
    sources: list[Source]
    took_ms: int
    rerank_used: bool
    knowledge_hit: bool = True
    usage: Optional[dict] = None


@app.get("/health")
def health():
    """健康检查：部署后第一件事就是访问它，看服务活着没

    ★ 2026-10-12 补 `load_errors` / `load_error_files`：以前**只有启动那一瞬的日志**
      能看出"有文件被跳过"，服务起来之后就没有任何接口看得见了 ——
      而"某个 .md 元信息写错 ⇒ 它整篇静默不入库 ⇒ 该答的题变成拒答"是一条**静默缩水**路径。
      `kb/loader.py` 里原本写着"startup 日志和 /health 的数字都能看见它"，
      但当时 /health 只返回 chunks/rejected，那句话是**假的**；现在它成真了。
    ★ 只返回**文件名**（`Path(...).name`），不返回完整路径 —— 后者会把本机目录结构漏出去。
    """
    return {"status": "ok" if rag.ready else "loading",
            # ★ 2026-10-13 加 boot_id（盲审 F1）：端到端脚本里有两条断言是"重启之后 X 还在"
            #   （③ 重启服务、⑦ 重启 Redis）。第一版只信"重启命令退出码为 0" ——
            #   而 `--restart-cmd "true"`（什么都没重启）能让七条全绿：
            #   计数当然没变，因为压根没重启。⇒ 得有样东西**只在重启后才变**。
            #   boot_id 在进程启动时生成一次，重启后对不上才算真重启过。
            "boot_id": BOOT_ID,
            "chunks": len(rag.chunks),
            "rejected": len(rag.rejected),
            "load_errors": len(rag.load_errors),
            "load_error_files": [Path(p).name for p in rag.load_errors],
            # ★ 2026-10-13 加：限流这一层现在是什么状态。
            #   为什么非要放在 /health 里：限流配错（比如以为在用 Redis、
            #   其实退回了进程内存储）**不会报错**，只会安静地把配额放大 N 倍。
            #   部署完之后第一眼看的就是这个接口，所以口径必须出现在这里。
            "limits": LIMITER.stats()}


@app.get("/limits")
def limits_status(request: Request):
    """问一句"我这个客户端今天还能用多少"（运维与演示用）。

    ★ 为什么这个接口本身**不设闸**：它只是读计数，没有成本；
      而且被限流的人最需要知道的就是"我为什么被挡、什么时候恢复"。
      挡住它等于把唯一的解释渠道也堵死。
    ★ 它只返回**调用方自己**的用量 —— 不返回别人的，也不返回全局明细。
    """
    who = limits_mod.client_id(
        request.client.host if request.client else None,
        {k.lower(): v for k, v in request.headers.items()},
        LIMITER.limits)
    return {"usage": LIMITER.usage_today(who), "limits": LIMITER.stats()}


@app.get("/guard-report")
def guard_report():
    """入库把关报告：哪些被拦了、为什么 —— 这是你敢把仓库给人看的底气"""
    return {"kept": [{"doc": c["doc"], "clause": c["clause"], "text": c["text"]}
                     for c in rag.chunks],
            "rejected": [{"doc": c["doc"], "clause": c.get("clause", ""),
                          "text": _mask_pii(c["text"]), "reason": r}
                         for c, r in rag.rejected]}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    """核心接口：问题进来 → 粗捞 → rerank → 生成 → 带出处返回

    ★ 2026-10-13 加：**进口那道闸**（限流 + 日配额），顺序是"先问一次、干完再记账"。
      为什么要拆成两层（`chat` 只做闸门与记账，活交给 `_answer`）：
      `_answer` 里有 **5 个 return 点**（没就绪 / 精排拒答 / 粗捞拒答 / 生成失败 / 成功），
      把记账写在每个 return 前面，就必然漏掉几个 —— 而漏掉的那个分支
      （最可能是"拒答"）恰恰是刷子最爱走的。拆出来之后记账只有一处，漏不了。
      ★ 拒答路径也计数：它照样花了改写 + 精排两次调用（见下面 _answer 里的注释）。
      ★ 额度用尽返 429 且带 `Retry-After`；限流存储挂了返 503（见 limits.Decision.http_status）。
      ★ `/health` 与 `/limits` **不设闸**：健康检查被限流挡住会变成"服务看起来死了"。

    ★ 2026-10-12 补：入口处先【丢掉】本线程上一次留下的 usage 残留。
      为什么必须有这一步：usage 是 `threading.local()` 累加的，而**清空只发生在
      走到某个 return 的时候**（四个 ChatResponse 返回点各调一次 `_take_usage()`）。
      于是任何"记了账、但中途抛异常"的请求会把残留留给**同一线程的下一个请求**，
      那个请求的数字就凭空多一截 —— 而它看起来完全合理
      （和 2026-10-12 刚修的那次"三次调用记成一次"是同一类病：**看着对，其实错**）。
      在入口清一次，才是真正关掉这条路径的动作：残留只能害到"下一个请求"，而下一个请求
      在这里就把脏数据丢掉了。
    """
    # ★ 2026-10-14：闸门 + 记账抽进下面的 `_gated()` —— 因为新加的 `/search` 也要走
    #   同一道闸。两条入口共用一份实现，才不会重演 R4 那次的病：新入口自己抄一遍记账、
    #   然后在某个分支上漏掉，"配额"在那条路上就整个不存在。
    return _gated(request, lambda led: handle_chat(req, led))


# ==================================================================
# 流式端点（SSE）—— 2026-10-14 加
# ==================================================================
# ★★ 为什么不是 `return _gated(request, lambda led: StreamingResponse(gen()))`：
#   `_gated` 的契约是"pipeline() 同步跑完、从返回值里取 usage 记账"。而流式把
#   「返回响应」和「跑完管道」拆成两个时刻 ⇒ 那样写的话记账会在**任何 token 产生之前**
#   跑完、`resp` 上没有 usage ⇒ **每条流式请求都记成 `usage_missing`**，
#   而客户端完全看不出来（流照推）。**这是跨模型盲审（GLM-5.3-Flash）指出的第一条**。
#
# ⇒ 这里手写闸门顺序，三条不变式照旧、只是位置换了：
#     ① `LIMITER.check()` 必须在**任何 SSE 字节写出之前** —— 这样被挡时还能返干净的
#        429/503，而且"被挡的请求不记账"这条语义不破；
#     ② 记账在**生成器的终结处**（`finally`），且是**终结代码的第一句、独立包裹** ——
#        绝不能和别的可能抛异常的动作（比如存会话历史）同一个 try；
#     ③ `usage` 只 take 一次，`done` 事件与 `record` **共用同一份**
#        （盲审 P5：取两次的话第二次是 None，真账会被事件拿走、记账拿空）。
#
# ★ 为什么用「后台线程 + 队列」而不是直接迭代同步生成器：
#   `self.llm.stream()` 是**同步阻塞**的。在 `async def` 里直接迭代它会把事件循环卡住，
#   于是**并发请求互相饿死** —— 而"能并发"本来是这个服务的一个卖点（同步 def + 线程池）。
#   丢进专职线程、生成器只从队列读，事件循环全程不被占住；账本也跟着那次执行走
#   （`_record_usage` 在 worker 里调，读的是 worker 自己那份 context 副本）。
#
# ★★ 已知边界（如实写在这里，别让读的人以为是遗漏）：
#   SSE 一旦发出 200 和响应头，**HTTP 状态码就回不去了**。生成阶段失败只能推
#   `event: error` —— 而**现成的同步 `/chat` 至少是 200 + 失败文案**，所以这个
#   error 事件是必须的，不推就比现状还倒退（盲审 P2）。
_STREAM_SENTINEL = object()


def _sse(event: str, data: dict) -> str:
    """把一条事件编成 SSE 帧。★ `ensure_ascii=False`：中文答案不该被转义成 \\uXXXX。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_stream_worker(req: ChatStreamRequest, led: UsageLedger, out: "queue.Queue") -> None:
    """在**专职线程**里跑"检索 → 精排 → 流式生成"，把事件塞进队列。

    ★ 这个函数**只负责产事件**，不碰闸门也不碰记账 —— 那两件事由外层的
      `chat_stream()` 与生成器的 `finally` 负责。分开是为了让"记账无条件发生"
      这件事只有一个地方要做（R4 的教训）。
    """
    # ★★★ 在本线程**显式绑定**这次请求的账本（这条实测踩了三次才定位对）：
    #   async 生成器跑在 **anyio 的任务上下文**里，与 worker 线程拿到的上下文副本
    #   **不是同一份** ⇒ 在生成器里 `set_current_ledger(led)`，worker 里
    #   `current_ledger()` 读到的**是另一个对象**
    #   （实测：worker 里 `current = ...337792`，而传入的 `led = ...9827200`）。
    #   ⇒ 不赌 contextvars 跨线程/跨任务的传播路径：**worker 一开始就把账本钉在自己线程上**，
    #     之后本线程内所有 `_record_usage` 读到的都是它。
    set_current_ledger(led)
    try:
        # ★★★ 这里**不能**再调 `_take_usage()` 当"丢弃残留"（这是同一个坑的另一半）：
        #   账本已经由调用方创建好并传进来，而 `_take_usage()` 读的是"当前上下文那个账本"
        #   ⇒ 它会把**这一个**当成残留清掉 ⇒ 账全丢 ⇒ `led.take()` 返回 None
        #   ⇒ 每次流式请求都记成 `usage_missing`，**而 SSE 流照样推得漂漂亮亮**。
        #   ⇒ 规矩：**显式传进来的账本就是这次请求唯一的账本，不要再去找"当前上下文"的那个。**
        if not rag.ready:
            out.put(("error", {"message": "服务还在加载模型，请稍等几秒再试"}))
            return

        search_q = req.question
        try:
            search_q = rag.rewrite_query(req.question)
        except LLMCallError as e:
            logger.warning("（流式）查询改写不可用（%s），本次用原问题检索", e)

        cands = rag.search(search_q, k=req.top_k)
        ranked = None
        try:
            ranked = rag.rerank(req.question, cands)
        except LLMCallError as e:
            logger.warning("（流式）rerank 不可用（%s）", e)

        # ★★ 拒答短路原样保留：精排最高分 < 5 ⇒ 推 refusal，**一个 delta 都不推**，
        #    也压根不调生成模型。这条设计不能因为"流式"就被绕过。
        if not ranked or ranked[0][2] < 5:
            out.put(("refusal", {
                "answer": "知识库里没有能回答这个问题的资料，我不编。",
                "sources": [], "rerank_used": bool(ranked)}))
            return

        picked = [x for x in ranked if x[2] >= 5][:3]
        sources = [{"doc": f"{c['doc']}｜{c['clause']}",
                    "text": c.get("text_full", c["text"]),
                    "score": round(v, 3), "rerank_score": s, "reason": r}
                   for c, v, s, r in picked]
        # ★ 先推 sources：这是"首字节"真正有用的地方 —— 用户能立刻看到"查到了哪几条、
        #   精排给了几分"，不用等整段答案生成完（而这正是 `/chat` 现在做不到的）。
        out.put(("sources", {"sources": sources, "rerank_used": True}))

        n_delta = 0
        for piece in rag.answer_stream(req.question, [c for c, _, _, _ in picked]):
            out.put(("delta", {"text": piece}))
            n_delta += 1
        out.put(("__ok__", {"n_delta": n_delta}))
    except LLMCallError as e:
        out.put(("error", {"message": f"模型调用失败：{e}"}))
    except Exception as e:                                      # noqa: BLE001
        logger.exception("（流式）管道异常")
        out.put(("error", {"message": f"{type(e).__name__}: {e}"}))
    finally:
        out.put(_STREAM_SENTINEL)          # ★ 无论如何都要收尾，否则消费端会一直等


async def _chat_stream_events(req: ChatStreamRequest, led: UsageLedger, who: str):
    """异步生成器：把 worker 线程产的事件转成 SSE 帧，**并在终结处记账**。

    ★ `who` 是**显式传进来的**，不用模块级变量：并发两个流式请求时，
      模块级那个值会被后一个请求覆盖，于是**记账记到别人头上**（谁都没发现的那种错）。
    """
    out: "queue.Queue" = queue.Queue()
    # ★ 账本的绑定**不在这里做**，在 `_sse_stream_worker` 里做 —— 原因见那段注释：
    #   async 生成器（anyio 任务上下文）与 worker 线程拿到的上下文副本**不是同一份**，
    #   在这里 set 等于白 set（实测：worker 里读到的 ledger 与传入的 `led` 是两个不同对象）。
    worker = threading.Thread(target=_sse_stream_worker, args=(req, led, out), daemon=True)
    worker.start()
    try:
        while True:
            item = await asyncio.to_thread(out.get)      # ★ 等队列不占事件循环
            if item is _STREAM_SENTINEL:
                break
            event, data = item
            if event == "__ok__":
                continue
            yield _sse(event, data)
    finally:
        worker.join(timeout=5)
        # ★★★ 记账：终结代码的**第一句**，独立包裹 —— 盲审 P4 指出，若把别的可能抛异常
        #   的动作写在同一段里，那个动作一炸就把记账一起带走（R4 的形态会复发）。
        #   ★ 而且账从**账本**取，不从"客户端听到的部分"取 —— 生成阶段即使中途出错，
        #     改写 + 精排那部分钱已经花了，必须照实入账（按实际花掉的钱记）。
        usage = led.take()
        LIMITER.record(who, usage)


@app.post("/chat/stream")
def chat_stream(req: ChatStreamRequest, request: Request):
    """流式问答（SSE）。事件顺序：`sources` → `delta`* → `done` / `refusal` / `error`。

    ★ 与 `/chat` 的关系：**共用 `rag.search` / `rag.rerank` / `build_answer_prompt`**，
      所以"检索与拒答的判断"与同步那条**同源**；差别只在"生成是边推边给"。
    ★ 版本/身份口径与 `/chat` 完全一致（同一道闸、同一个 `client_id`）。
    """
    # ① 闸门在任何字节之前 —— 被挡时还能返干净的 429/503（且这些请求不记账）
    who = limits_mod.client_id(
        request.client.host if request.client else None,
        {k.lower(): v for k, v in request.headers.items()},
        LIMITER.limits)
    decision = LIMITER.check(who)
    if not decision.allowed:
        raise HTTPException(
            status_code=decision.http_status(),
            detail={"reason": decision.reason, "detail": decision.detail},
            headers={"Retry-After": str(decision.retry_after)})

    led = UsageLedger()
    # ★★★ 必须把账本**绑到当前上下文**，worker 线程才记得上账。
    #   这里是实测踩到的坑（就是盲审 P0 那条的形态）：只在下面 new 一个账本、
    #   不绑 contextvar 的话，worker 里的 `_record_usage` 找不到账本 ⇒ 账全丢
    #   ⇒ `led.take()` 返回 None ⇒ 记成 `usage_missing`，**而 SSE 流照样推得漂漂亮亮**。
    #   （实测：拒答那一路 usage_missing 每次都涨 1。）
    #   ⚠️ 这里不 reset：生成器在**同一个请求上下文**里跑完并 take 掉账本，
    #      而 `StreamingResponse` 的生成器引用的是这次请求的上下文副本。
    set_current_ledger(led)
    return StreamingResponse(_chat_stream_events(req, led, who),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _gated(request: Request, pipeline):
    """`/chat` 与 `/search` 共用的**闸门 + 记账**：先问一次，干完再记账。

    ★★ 记账必须**无条件**发生（盲审 R4）：第一版把 `LIMITER.record(...)` 写在
      `return resp` 前面，于是任何从 `handle_chat` 冒出来的异常都会绕过记账 ——
      那次请求**只在桶里扣了一枚令牌、日额度上完全不存在**。
      而 `handle_chat` 只接 `LLMCallError`；别的异常一路冒到 FastAPI 变成 500。
      有一条**当时就可达**的路径：没配 `DEEPSEEK_API_KEY` 时，默认路径每次 /chat
      都抛 pydantic 校验错（"缺 key 就该炸出来"是刻意的）⇒ 日额度那道闸全废，
      只剩桶。README 写着"拒答也计数"，但"异常也计数"漏了 —— 记账应当无条件。
      ⚠️ 拿不到 usage 时传 None 进去，`record` 会把它记成 `usage_missing`（不补 0）。

    ★ 被挡的请求（429 / 503）走 `raise`，压根到不了这里 —— `check()` 那一步就返回了。
      所以"被挡的请求不记账"这条仍然成立（有回归用例守着）。
    ★ `pipeline` 传函数、在这里调，是为了让"记账"落在**同一个 try/finally** 里：
      哪个端点进来都一样，没有第二条记账路径可以漂移。

    ★★★ 2026-10-14（为流式改造铺路，跨模型盲审给的设计）：
      `pipeline` 现在收一个**本次请求的账本** `UsageLedger` 作参数，并把它绑到 contextvar 上。
      两个理由：
        ① **异常路径也能记到真账**（旧写法从 `resp` 里取，异常时取不到）；
        ② **流式/并发下不再依赖线程局部**（见 `UsageLedger` 上面那段长注释）。
      ⚠️ 调用方必须写成 `lambda led: handle_xxx(req)` —— 账本由这里创建并传下去，
         **绝不允许哪个端点自己 new 一个**（那就会退化成"两条记账路径"，正是 R4 的病根）。
    """
    who = limits_mod.client_id(
        request.client.host if request.client else None,
        {k.lower(): v for k, v in request.headers.items()},
        LIMITER.limits)
    decision = LIMITER.check(who)
    if not decision.allowed:
        raise HTTPException(
            status_code=decision.http_status(),
            detail={"reason": decision.reason, "detail": decision.detail},
            headers={"Retry-After": str(decision.retry_after)})

    led = UsageLedger()
    token = set_current_ledger(led)          # ★ 账本随这次请求走（见 UsageLedger 的注释）
    try:
        resp = pipeline(led)
        return resp
    finally:
        # ★★ 记账必须无条件（R4 那次的血）：即使上面抛异常也要走到这里。
        #   而且它必须是**终结代码的第一句**——盲审 P4 指出：若把别的可能抛异常的动作
        #   （比如"存会话历史"）写在它前面或同一个 try 里，那个动作一炸就把记账一起带走。
        #
        # ★ 账从【账本】取，不从 `resp` 取 —— 这是这次改造最实质的一处收益：
        #   旧写法 `usage = resp.usage` 在**异常路径上根本执行不到**，于是
        #   「改写 + 精排已经烧了 token、然后生成抛异常」这种请求会记成 `usage_missing`。
        #   账本是边调边累加的 ⇒ 异常时它手里就是那半截真账（"实际花掉的钱"）。
        #   ⚠️ take 只调用一次：流式那边事件与记账必须共用同一份，不能各取一次（盲审 P5）。
        usage = led.take()
        _CURRENT_LEDGER.reset(token)          # 精确复原（不是 set(None) —— 那会清掉外层账本）
        LIMITER.record(who, usage)


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, request: Request):
    """只检索、不生成 —— 给"只要原文条款"的工具用（`mcp/tools_impl.py::search_policy`）。

    ★ 为什么要单开一条（2026-10-14，**先量后改**）：
      `search_policy` 要的只是 `sources`，原来借道 `/chat?use_rerank=False` 拿，
      代价是服务内部**白跑一次生成**。同一批 20 题实测：
      这条路 13 次调用共 6,337 token，其中每一个都是没人读的答案文本，
      占工具侧总账（26,277）的 24% ⇒ 去掉之后工具返回文本**逐字节不变**。
      改前的数在 `agent/_tool_split_before.json`，改后同一条命令再量一遍对答案。

    ★ 闸门：和 `/chat` 完全一样（同一个 `_gated`）。它不是 `/health` 那种"看一眼"的
      接口 —— 它照样吃 CPU（向量 + BM25 + RRF），不设闸就等于开了个免费入口。
      ⚠️ 但它**不烧 token**，所以记账时 token 那部分是可证明的 0（见 `handle_search`）。
    """
    return _gated(request, lambda led: handle_search(req, led))


def _vec_reject(cands) -> bool:
    """粗捞路径的拒答判据：**全部候选里最高的向量分**低于阈值 ⇒ 库里没有。

    ★ 为什么是 max 而不是 RRF 融合后的第一名：见 `handle_chat` 里那段长注释
      （RRF 可能把 BM25 命中的块顶到第一，而它向量分未必最高 ⇒ 用第一名会误拒）。
    ★ 2026-10-14 抽成函数：`/chat?use_rerank=False` 与 `/search` 必须给出同一个判断。
    """
    return (not cands) or max((s for _, s in cands), default=0.0) < VEC_REJECT_THRESHOLD


def _vec_sources(cands):
    """粗捞路径的 sources 与生成用上下文 —— `/chat` 与 `/search` 共用的唯一实现。"""
    picked = [x for x in cands if x[1] >= VEC_REJECT_THRESHOLD][:3]
    sources = [Source(doc=f"{c['doc']}｜{c['clause']}",
                      text=c.get("text_full", c["text"]),
                      score=round(s, 3)) for c, s in picked]
    return sources, [c for c, _ in picked]


def handle_chat(req: ChatRequest, led: "UsageLedger | None" = None):
    """问答主体：**不含 HTTP 那道限流闸门**（HTTP 入口是上面的 `chat()`）。

    ★ 为什么把它单独拎出来：闸门要 `Request` 对象（拿客户端身份），
      而"直连管道"的调用方（评测集、回归测试、脚本）没有 HTTP 请求，
      它们关心的是检索/拒答/生成的行为，不该被限额影响，也不该往额度里记账。
      ⇒ 拆开之后：`chat()` = 闸门 + 记账，`handle_chat()` = 管道本身。
    ★ 2026-10-14：`led` 是账本（默认 None = 走旧的线程局部）。
      `_gated` 里的 `pipeline(led)` 会把它传进来；直连调用不传 ⇒ 行为与旧版一致。
    """
    _take_usage()                      # ★ 丢弃残留（不是读取）—— 见 chat() 的 docstring
    t0 = time.time()
    if not rag.ready:
        return {"answer": "服务还在加载模型，请稍等几秒再试", "sources": [],
                "took_ms": 0, "rerank_used": False}

    # ★ 查询改写：把"学生怎么说"翻译成"制度怎么写"，**只用于检索**。
    #   精排与生成看到的仍是 req.question（用户原话）—— 答的是他真正问的问题。
    use_rewrite = req.use_rewrite if req.use_rewrite is not None else req.use_rerank
    search_q = req.question
    if use_rewrite:
        try:
            search_q = rag.rewrite_query(req.question)
            if search_q != req.question:
                logger.info("检索查询已改写：%r → %r", req.question, search_q)
        except LLMCallError as e:
            # 降级不崩：改写挂了就用原问题检索 —— 绝不因此拒答
            logger.warning("查询改写不可用（%s），本次用原问题检索", e)

    cands = rag.search(search_q, k=req.top_k)

    took = lambda: int((time.time() - t0) * 1000)          # 匿名函数，算"到现在用了多少毫秒"

    use_rerank = req.use_rerank
    ranked = None
    if use_rerank:
        try:
            ranked = rag.rerank(req.question, cands)      # [(块, 向量分, 精排分, 理由)]
        except LLMCallError as e:
            # ★ 降级而不是崩：rerank 这一层挂了（超时 / 限流 / 返回了解析不了的 JSON），
            #   就退回粗捞路径继续答 —— 绝不把它伪装成"库里没有"直接拒答。
            #   use_rerank 置 False 后下面会走 else 分支，响应里也会如实标 rerank_used=False，
            #   调用方一眼能看出"这次没走精排"，而不会误以为是模型觉得库里没有。
            logger.warning("rerank 不可用（%s），本次降级为粗捞路径", e)
            use_rerank = False

    if use_rerank:
        # ★★ 拒答硬短路：连最高分的候选都不到 5 分 = 库里根本没这个东西。
        #    这时【直接返回，压根不调用生成模型】。
        #    之前的写法是"全部低分也硬塞第一条进去"，寄希望于提示词让模型拒答 ——
        #    但提示词会漏，喂了假资料模型就会照着编。真正可靠的做法是【不喂】。
        if not ranked or ranked[0][2] < 5:
            # ★ 这里也要带 usage：短路虽然没调生成模型，
            #   但【查询改写】和【rerank 打分】那两次调用已经花掉了 token。
            #   只在成功路径记usage 的话，这部分开销会整段丢失，
            #   而它恰恰是"拒答很便宜"这个说法最容易骗人的地方。
            return ChatResponse(
                answer="知识库里没有能回答这个问题的资料，我不编。",
                sources=[], took_ms=took(), rerank_used=True, knowledge_hit=False,
                usage=_usage_for_response(led))

        picked = [x for x in ranked if x[2] >= 5][:3]     # 只留 5 分以上的，最多 3 条
        sources = [Source(doc=f"{c['doc']}｜{c['clause']}",
                          text=c.get("text_full", c["text"]),
                          score=round(v, 3), rerank_score=s, reason=r)
                   for c, v, s, r in picked]
        ctx = [c for c, _, _, _ in picked]
    else:
        # ★★ 关掉 rerank 时【照样要拒答】。
        #    之前的写法是"没有分数可判断，只能照常生成" —— 那等于网页上取消勾选一下，
        #    整个拒答机制就被绕过去了，而且 knowledge_hit 还是 true（调用方以为命中了）。
        #    精排分不存在，就用向量分（0~1 的余弦相似度），阈值见 VEC_REJECT_THRESHOLD 的注释。
        # ★ 用【全部候选里最高的向量分】判断拒答，而不是 RRF 融合后的第一名。
        #   RRF 可能把 BM25 命中的块顶到第一，而它向量分未必最高 ——
        #   用第一名会"把高向量分块挤到第二 → 误拒该答的问题"。用 max 更稳。
        # ★ 2026-10-14：判据与 sources 的构造抽成 `_vec_reject()` / `_vec_sources()`
        #   （定义在 handle_chat 上面）—— 因为 `/search`（只检索不生成）必须给出
        #   **逐字段一样**的结果。抽成共用实现之后，"两条路给出不同答案"在结构上
        #   就不可能发生，而不是靠一句"它们应该一样"的注释。真 A/B 对照见
        #   tests/test_guard.py 的「只检索端点」组。
        if _vec_reject(cands):
            return ChatResponse(
                answer="知识库里没有能回答这个问题的资料，我不编。",
                sources=[], took_ms=took(), rerank_used=False, knowledge_hit=False,
                usage=_usage_for_response(led))

        sources, ctx = _vec_sources(cands)
        # 关掉 rerank 时用的是粗捞的向量分，比精排分粗（"意思接近但答非所问"更容易漏进来），
        # 所以默认还是开着 rerank —— 关掉只是留一个对比开关，不是关闭防线。

    try:
        answer = rag.answer(req.question, ctx)
    except LLMCallError as e:
        # 资料命中了，但生成这一步挂了 —— 如实告诉调用方"是模型的问题"，
        # 不能伪装成拒答（knowledge_hit 仍是 True，因为资料确实命中了）。
        logger.error("生成答案失败：%s", e)
        return ChatResponse(
            answer="⚠️ 已找到相关资料，但模型调用失败（超时或限流），请稍后重试。",
            sources=sources, took_ms=took(), rerank_used=use_rerank, knowledge_hit=True,
            usage=_usage_for_response(led))

    # ★ 2026-09-27：这里【显式】写上 knowledge_hit=True，不再靠上面的默认值。
    #   之前靠 ChatResponse 的字段默认值躺赢 —— 一旦有人改动那个默认值，
    #   成功路径的语义会悄悄跟着变，而且看这段代码的人根本看不出来它依赖了默认值。
    return ChatResponse(answer=answer, sources=sources, took_ms=took(),
                        rerank_used=use_rerank, knowledge_hit=True,
                        usage=_usage_for_response(led))


def _zero_usage() -> dict:
    """「确定的 0 次 LLM 调用」的账 —— 与 `_take_usage()` 返回的 None **不是一回事**。

    `_take_usage()` 的 None 把两种可能混在一起（真的没调 / 调了但没拿到账）。
    `/search` 这条路上**没有任何一次 LLM 调用**，是【可证明的 0】⇒ 这里返回显式的 0：
      · 工具侧于是记成 `llm_calls=0`（真的没调），而不是 `?`（账没拿到）——
        见 `mcp/tools_impl.py::_usage_fields`（★ 2026-10-14 起标记行**只报调用次数**，
        不再报 token 数：那五个账目字段每一轮都会重新进模型上下文，是纯噪声）；
      · `limits.py::Limiter.record()` 也据此**不往 `usage_missing` 上记账**：
        否则每一次 /search 都会往"token 账有缺口"上抹一笔假痕，把指标稀释成噪音。
    ★ 每次返回**新 dict**：pydantic 响应里塞一个共享可变对象，一旦有人原地改它，
      所有响应会跟着变 —— 这类"看着对、其实错"的账，本项目已经吃过几次。
    """
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cache_read": 0, "n_calls": 0}


def handle_search(req: SearchRequest, led: "UsageLedger | None" = None):
    """只检索、不生成（HTTP 入口是上面的 `search()`，闸门与 `/chat` 共用 `_gated`）。

    ★ 与 `/chat?use_rerank=False` 的关系：`sources` / `knowledge_hit` **必然一致**，
      因为两边共用 `_vec_reject()` / `_vec_sources()`。测试另外做真 A/B 对照，
      不靠"它们应该一样"这句话。

    ★ 未就绪（模型还在加载）时**照抄 /chat 的老语义**：`sources=[]` 且
      `knowledge_hit` 留默认 True ⇒ 工具侧照样输出那句「没有找到」。
      这是**刻意保留**的：本次改动的契约是"除了不再生成，其余逐字段不变"，
      顺手改掉它就变成"大体一样"，而后者没法验证。
      （把"未就绪"说成"库里没有"确实是句错话，但它是**两条路共有**的老问题，
      要改就一起改、并单独留痕 —— 不塞进这次改动里。）
    """
    _take_usage()                      # ★ 丢弃残留（不是读取）—— 理由同 handle_chat 入口
    # ★ 2026-10-14：把"这条路上一次模型都没调"这件事**写进账本**。
    #   不写的话，`_gated` 从账本取到的是 None ⇒ 记成 `usage_missing` ⇒
    #   每次 /search 都往"token 账有缺口"上抹一笔假痕（`test_guard.py` 当场抓住过）。
    if led is not None:
        led.mark_zero()
    t0 = time.time()
    if not rag.ready:
        return SearchResponse(sources=[], took_ms=0, rerank_used=False,
                              usage=_zero_usage())
    cands = rag.search(req.question, k=req.top_k)
    if _vec_reject(cands):
        return SearchResponse(sources=[], took_ms=int((time.time() - t0) * 1000),
                              rerank_used=False, knowledge_hit=False,
                              usage=_zero_usage())
    sources, _ = _vec_sources(cands)
    return SearchResponse(sources=sources, took_ms=int((time.time() - t0) * 1000),
                          rerank_used=False, knowledge_hit=True,
                          usage=_zero_usage())


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
<p style="color:#666;font-size:14px">bge-small-zh 向量检索 + BM25 混合 + 入库把关 + 大模型精排 + DeepSeek 生成</p>
<textarea id="q" placeholder="试试：学校对学生的处分有哪几种？ / 请假一天以内由谁审批？ / 图书馆几点关门？（最后一条库里没有，看它怎么拒答）"></textarea><br>
<button onclick="ask()">提问</button>
<label style="margin-left:12px;font-size:13px"><input type="checkbox" id="rr" checked> 启用 rerank</label>
<div class="box" id="ans" style="display:none"></div>
<div id="src"></div>
<script>
// ★ 不用 innerHTML 拼字符串：资料来源、理由都来自知识库文本，
//   一旦知识库的 .md 里混进 <img onerror=...> 之类，innerHTML 会把它当标签执行（XSS）。
//   全部改用 textContent / createTextNode —— 内容永远被当【纯文本】，不解析成标签。
function tag(t){const e=document.createElement('span');e.className='tag';e.textContent=t;return e;}
async function ask(){
  const q=document.getElementById('q').value.trim();
  if(!q){alert('请输入问题');return;}
  const rr=document.getElementById('rr').checked;
  const a=document.getElementById('ans'),s=document.getElementById('src');
  a.style.display='block';a.textContent='思考中…';s.replaceChildren();
  const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question:q,use_rerank:rr})});
  const d=await r.json();
  a.textContent=d.answer;
  if(d.knowledge_hit===false){
    const p=document.createElement('p');
    p.style.cssText='font-size:13px;color:#b45f04;margin-top:12px';
    p.textContent='未命中知识库 · 已拒绝作答（一条资料都没喂给模型）';
    s.replaceChildren(p);
    return;
  }
  const head=document.createElement('p');
  head.style.cssText='font-size:13px;color:#888;margin-top:12px';
  head.textContent='用时 '+d.took_ms+' ms　引用资料：';
  s.replaceChildren(head);
  (d.sources||[]).forEach(x=>{
    const div=document.createElement('div');div.className='src';
    div.appendChild(tag(x.doc));
    if(x.rerank_score!=null)div.appendChild(tag('精排 '+x.rerank_score+' 分'));
    if(x.rerank_score==null && x.score!=null)div.appendChild(tag('向量分 '+x.score));
    div.appendChild(document.createTextNode(x.text));
    if(x.reason){
      const i=document.createElement('i');i.style.color='#999';i.textContent=x.reason;
      div.appendChild(document.createElement('br'));div.appendChild(i);
    }
    s.appendChild(div);
  });
}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


# ==================================================================
# ⑤ 启动（只有 python xxx.py 直接运行时才会执行；被 import 时不会）
# ==================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # ★ 2026-10-11 改成可配（HOST / PORT）。起因是 Docker：
    #   容器里的 127.0.0.1 只有容器自己看得见 —— 宿主机的 `-p 8000:8000`
    #   映射的是容器的网卡，救不了 loopback。所以容器里必须能绑 0.0.0.0。
    #   ★ 默认值【一个字符都没改】：不在容器里跑时行为与以前完全一致
    #     （仍然只监听本机，不会莫名其妙把服务暴露到局域网）。
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))

    # ★ 下面这几行原来把 127.0.0.1:8000 写死了：HOST/PORT 一旦被改，
    #   它们就会打印【一个并不存在的地址】—— 又属于"看着对、其实不对"那一类。
    #   现在改成引用上面两个变量。绑 0.0.0.0 时给人看的是 127.0.0.1
    #   （0.0.0.0 不是一个能拿去浏览器里打开的地址）。
    show = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    base = f"http://{show}:{port}"
    logger.info("=" * 60)
    logger.info("服务启动后访问（实际绑定 %s:%s）：", host, port)
    logger.info("   %-28s 聊天页面", base)
    logger.info("   %-28s 自动生成的接口文档", base + "/docs")
    logger.info("   %-28s 健康检查", base + "/health")
    if host in ("0.0.0.0", "::"):
        logger.info("★ 绑的是 %s：同网段的人都能访问（本机用 %s）", host, base)
    logger.info("按 Ctrl+C 停止")
    logger.info("=" * 60)
    uvicorn.run(app, host=host, port=port, log_level="info")

# -*- coding: utf-8 -*-
"""
阶段 0「安全 / 可靠硬门槛」的守门测试

这一组用例盯的是同一件事，一句话概括：
    【模型出问题 ≠ 知识库里没有】

    前者是【故障】，必须报错或降级，而且要留下日志，让人能发现；
    后者是【正常的业务结果】（库里确实没有 → 拒答）。
    两者一旦混同，服务挂了也会表现成一条平平无奇的拒答，
    调用方永远察觉不到，故障就被永久掩盖了 —— 这正是最危险的一类 bug。

跑法（仓库根目录）：
    python -m pytest tests/test_llm_resilience.py -v

★ 不需要 DEEPSEEK_API_KEY：全部用假模型，一条都不真调大模型。
"""

import json
import os
import sys

# 这两行必须在 import 项目模块之前（和 test_guard.py 同样的原因）
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                      # noqa: E402
import service as svc                              # noqa: E402

# 假模型从 conftest.py 拿 —— 三个测试文件共用一份，不再各抄一遍（见 conftest.py 的说明）
from conftest import _FakeLLM, _FakeResp, _SlowLLM, _UsageLLM  # noqa: E402


@pytest.fixture(scope="module")
def rag():
    """建库（加载 embedding 模型要十几秒，整个文件共用一份）"""
    svc.rag.startup()
    return svc.rag


# ==================================================================
# ① 超时：模型卡住不能拖垮接口
# ==================================================================
def test_timeout_becomes_llm_call_error(monkeypatch):
    """模型卡住时必须变成 LLMCallError，而不是让调用方无限期干等。

    ★ 这条顺带守住一个隐蔽的坑：如果 _invoke_llm 的签名写成
      timeout=LLM_TIMEOUT_SEC（默认参数），那么这里 monkeypatch 模块变量
      根本不会生效（默认参数在【函数定义那一刻】就求值完了），
      测试就会假绿。所以函数体内再读一次是必须的。
    """
    monkeypatch.setattr(svc, "LLM_TIMEOUT_SEC", 0.05)
    with pytest.raises(svc.LLMCallError):
        svc._invoke_llm(_SlowLLM(), ["hi"])


# ==================================================================
# ② 模型返回垃圾：必须报错，绝不能静默给全 0 分
# ==================================================================
def test_rerank_bad_json_raises_not_silent_zero(rag, monkeypatch):
    """★ 这一组里最关键的一条。

    以前的写法是"解析失败就给所有候选 0 分" —— 这看似温和，实际最危险：
    全 0 分 → 最高分 < 5 → 触发拒答 → 界面上显示"知识库里没有"。
    于是"模型返回了垃圾"（故障）被完美伪装成"库里没有"（正常业务结果），
    日志里也什么都不留，没人会知道服务其实已经不正常了。
    现在必须抛 LLMCallError，让 /chat 去决定降级还是暴露。
    """
    monkeypatch.setattr(svc.rag, "_llm", _FakeLLM("抱歉，我这次不想输出 JSON"))
    q = "什么情况会被开除学籍？"
    cands = svc.rag.search(q, k=3)
    assert cands, "检索应该命中，否则这条用例没真正测到 rerank"

    with pytest.raises(svc.LLMCallError):
        svc.rag.rerank(q, cands)


# ==================================================================
# ③ 幻觉 id：越界不能 500，要跳过
# ==================================================================
def test_rerank_skips_hallucinated_id(rag, monkeypatch):
    """模型会幻觉出不存在的 id（超出候选数量、0 甚至负数）。

    以前直接 candidates[i - 1] 会 IndexError → 接口裸 500。
    现在应该跳过越界的那条，合法的照常保留。
    """
    payload = json.dumps({"scores": [
        {"id": 1, "score": 9, "reason": "相关"},
        {"id": 99, "score": 10, "reason": "幻觉出来的 id"},
    ]})
    monkeypatch.setattr(svc.rag, "_llm", _FakeLLM(payload))
    q = "什么情况会被开除学籍？"
    cands = svc.rag.search(q, k=3)

    out = svc.rag.rerank(q, cands)

    assert len(out) == 1, f"越界的 id=99 应被跳过，只留 id=1，实际返回 {len(out)} 条"
    assert out[0][2] == 9, "留下来的应该是 id=1 那条（精排分 9）"


# ==================================================================
# ④ 超长输入：在入口就被挡掉
# ==================================================================
def test_question_too_long_is_rejected():
    """没有 max_length 的话，别人丢一篇几万字过来会被原样拼进 prompt。

    后果有两个：烧掉大量 token（费钱又慢），极端时顶爆模型上下文窗口。
    pydantic 在入口挡掉并返回 422，不用业务代码里写 if。
    """
    from fastapi.testclient import TestClient

    client = TestClient(svc.app)
    r = client.post("/chat", json={"question": "啊" * 5000})
    assert r.status_code == 422, f"期望 422，实际 {r.status_code}"


# ==================================================================
# ⑤ 端到端降级：模型挂了要如实说，不能伪装成"库里没有"
# ==================================================================
def test_chat_degrades_instead_of_faking_rejection(rag, monkeypatch):
    """★ 端到端兜底：资料命中了、但生成那一步挂了，knowledge_hit 必须还是 True。

    如果这里返回 False，调用方会以为"这个问题库里根本没有"，
    于是故障又一次被伪装成正常业务结果 —— 和 ② 是同一个病的两种表现。
    """
    from fastapi.testclient import TestClient

    monkeypatch.setattr(svc, "LLM_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(svc.rag, "_llm", _SlowLLM())

    # 不用 with TestClient(...)：rag 已由 fixture 启动，避免 lifespan 再加载一次模型
    client = TestClient(svc.app)
    r = client.post("/chat", json={"question": "什么情况会被开除学籍？",
                                   "use_rerank": False})

    assert r.status_code == 200, f"期望 200，实际 {r.status_code}：{r.text[:200]}"
    d = r.json()
    assert d["knowledge_hit"] is True, "命中了就是命中了，不能因为模型挂了就说库里没有"
    assert "模型调用失败" in d["answer"], f"应如实说明是模型的问题，实际：{d['answer'][:80]}"


# ==================================================================
# ⑥ Token 计量：usage 必须【累加】，不能只留最后一次
# ==================================================================
# 这一组盯的是一个已经真发生过的 bug（2026-10-12 修）：
#   一次 /chat 要调三次模型（查询改写 → rerank 打分 → 生成），
#   而记账写成 `_LLM_USAGE.last = {...}`（覆盖），于是：
#     · 返回的 usage 只剩【最后一次】（生成）那一段 —— 少算约 2/3
#     · n_calls 恒等于 1（它在同一个赋值里现算 getattr(...,0)+1，
#       而那个线程属性从没人写过，每次都读到 0）
#   两个数一起看【像合理的】，所以第一轮跑批完全没发现异常。
#   只有"逐段对照真实响应"或者"让每次调用的 token 数不同"才暴露得出来。
class _NoUsage:
    """连 usage_metadata 都没有的返回 —— 模拟"拿不到"（老版本 langchain 就是这样）。"""

    def __init__(self, content="{}"):
        self.content = content


def test_usage_is_accumulated_across_calls():
    """★ 三次调用、三份不同的 usage，取出来必须是【和】而不是【最后一份】。

    断言写成具体数字而不是"比最后一次大" —— 后者两种实现都能过，等于没测。
    ★ 三次的值写成 100 / 200 / 300 这种一眼能加的（和 = 600），
      别写成 100*n 然后在断言里手算 —— 我第一版就是这么写的，
      把 100+200+300 算成了 300，测的是自己的算术不是代码。
    """
    svc._LLM_USAGE.acc = None
    svc._LLM_USAGE.n_calls = 0
    svc._LLM_USAGE.missing_calls = 0

    from conftest import _UsageResp
    for inp, out in ((100, 10), (200, 20), (300, 30)):
        svc._record_usage(_UsageResp("{}", inp=inp, out=out))

    u = svc._take_usage()
    assert u is not None, "记到了 3 次，取 usage 不该返回 None"
    assert u["input_tokens"] == 600, (
        f"100+200+300 应得 600，实际 {u['input_tokens']} —— "
        f"若是 300 说明只留了最后一次，那正是 2026-10-12 修掉的那个 bug")
    assert u["output_tokens"] == 60, f"10+20+30 应得 60，实际 {u['output_tokens']}"
    assert u["n_calls"] == 3, f"应报 3 次调用，实际 {u['n_calls']} —— 恒等于 1 就是那个 bug"


def test_missing_usage_does_not_erase_what_was_recorded():
    """★ 某一次拿不到 usage，【不能把之前记下的清零】。

    这正是 service.py 里那两处短路 return 的处境：
    改写和精排已经花了 token，只是生成那一步没拿到/没调用。
    一笔有、一笔没有，正确结果是"记到的那部分照报 + 明说漏了几次"，
    而不是"清零"也不是"整项当 0"。
    """
    from conftest import _UsageResp
    svc._LLM_USAGE.acc = None
    svc._LLM_USAGE.n_calls = 0
    svc._LLM_USAGE.missing_calls = 0

    svc._record_usage(_UsageResp("{}", inp=100, out=10))
    svc._record_usage(_NoUsage())                      # 这次没拿到

    u = svc._take_usage()
    assert u is not None, "记到过一次就不该返回 None"
    assert u["input_tokens"] == 100, f"拿不到的那次不该影响已记下的 100，实际 {u['input_tokens']}"
    assert u["n_calls"] == 1, f"只有一次成功记账，实际 {u['n_calls']}"
    assert u["missing_calls"] == 1, f"漏掉 1 次要如实报出来，实际 {u.get('missing_calls')}"


def test_take_usage_resets_between_requests():
    """取走之后必须清干净 —— 否则第二个请求会把第一个的数算进来。"""
    from conftest import _UsageResp
    svc._LLM_USAGE.acc = None
    svc._LLM_USAGE.n_calls = 0
    svc._LLM_USAGE.missing_calls = 0

    svc._record_usage(_UsageResp("{}", inp=100, out=10))
    first = svc._take_usage()
    second = svc._take_usage()
    assert first["input_tokens"] == 100
    assert second is None, f"取过一次就该是空，实际 {second} —— 会重复计数"


def test_no_usage_at_all_returns_none():
    """一次都没记到就返回 None（不是 0）—— "没拿到"和"真的是 0"必须能区分。"""
    svc._LLM_USAGE.acc = None
    svc._LLM_USAGE.n_calls = 0
    svc._LLM_USAGE.missing_calls = 0
    svc._record_usage(_NoUsage())
    assert svc._take_usage() is None, "一次都没成功记账就该是 None"


def test_chat_endpoint_reports_accumulated_usage(rag, monkeypatch):
    """★ 端到端：打一次 /chat，usage 的 n_calls 必须等于真实调用次数。

    这是唯一能抓住"覆盖 vs 累加"的判据 —— 因为它把记账挂在真实调用链上，
    而真实链路里改写/精排/生成各调一次模型。
    假模型每次 token 数不同（100/200/300），所以两种实现给出的数不同。
    """
    from fastapi.testclient import TestClient

    fake = _UsageLLM('{"scores": [{"id": 1, "score": 9, "reason": "相关"}]}')
    monkeypatch.setattr(svc.rag, "_llm", fake)

    client = TestClient(svc.app)
    r = client.post("/chat", json={"question": "什么情况会被开除学籍？",
                                   "use_rerank": True})
    assert r.status_code == 200, f"期望 200，实际 {r.status_code}"
    u = r.json().get("usage")
    assert isinstance(u, dict), f"响应里应有 usage，实际 {r.json().get('usage')!r}"
    assert u["n_calls"] == fake.calls, (
        f"记账说调了 {u['n_calls']} 次，假模型实际被调了 {fake.calls} 次 —— "
        "两者对不上就是记账漏了或重复了")
    assert u["n_calls"] >= 2, (
        f"默认路径至少是【改写 + 精排 + 生成】三次调用，实际只记了 {u['n_calls']} 次")


def test_usage_residue_from_a_failed_request_is_not_charged_to_the_next(monkeypatch):
    """★ 上一个请求"记了账、然后异常退出"留下的 usage 残留，不许算到下一个请求头上。

    起因（2026-10-12）：usage 是 `threading.local()` 累加的，而**清空只发生在
    走到某个 return 的时候**（四个 ChatResponse 返回点各调一次 `_take_usage()`）。
    ⇒ 任何"记了账但中途抛异常"的请求，会把残留留给**同一线程的下一个请求**，
      那个请求的数字就凭空多一截 —— 而它看起来完全合理。
      这和刚修掉的「三次调用记成一次」是同一类病：**看着对，其实错**。
    修法：`chat()` 入口先**丢掉**（不是读取）残留。这个用例就是那个修复的守门人：
    把入口那行 `_take_usage()` 删掉，这里立刻红。
    """
    class _Resp:
        usage_metadata = {"input_tokens": 999, "output_tokens": 999, "total_tokens": 1998}

    svc._record_usage(_Resp())      # 模拟：上一个请求记了账，然后在 return 之前抛了异常

    class _StubRag:
        ready = True
        load_errors = []
        chunks = [{"doc": "d", "clause": "c", "text": "t"}]
        rejected = []

        def search(self, q, k=5):
            return [({"doc": "d", "clause": "c", "text": "t"}, 0.9)]   # 高于拒答阈值

        def answer(self, q, ctx):
            return "答"

    monkeypatch.setattr(svc, "rag", _StubRag())
    resp = svc.handle_chat(svc.ChatRequest(question="随便问一句",
                                           use_rerank=False, use_rewrite=False))
    got = (resp.usage or {}).get("total_tokens")
    assert got != 1998, (
        f"上一个请求的残留被算到这一个头上了：usage={resp.usage!r}"
        "（说明 chat() 入口没有清空线程局部的 usage 累加器）")


def test_health_answers_before_startup_finishes(monkeypatch):
    """★ /health 在【服务还没就绪】时必须能给出回答，而不是 500。

    起因（2026-10-12）：`chunks` / `rejected` 原来**只在 `startup()` 里赋值**，
    而 `startup()` 要十几秒（加载模型 + 建库）——
    于是那一段时间访问 `/health` 会在 `len(rag.chunks)` 上抛 AttributeError → **500**，
    也就是说 `health()` 文档里承诺的 `status="loading"` **根本走不到**：
    健康检查在最需要它的时候是坏的（部署平台/uptime 探针看到的全是 500）。

    ★ CI 为什么没发现：容器 job 的探活用 `curl -sf`，`-f` 遇 5xx 就失败 ⇒
    它只是"继续轮询"，而不是读到了 `loading`。**能跑通 ≠ 那个分支被测过。**
    """
    monkeypatch.setattr(svc, "rag", svc.RAG())        # 一个从没 startup 过的实例
    h = svc.health()                                 # ← 修之前这里会抛 AttributeError
    assert h["status"] == "loading", f"启动期应当回答 loading，实际 {h}"
    assert h["chunks"] == 0 and h["rejected"] == 0 and h["load_errors"] == 0, h

"""
LangGraph 控制流综合实战 demo（09-55 集知识点串讲）
=====================================================
运行环境：D:\\Python-project\\.venv （langgraph 1.2.11）
运行命令：D:\\Python-project\\.venv\\Scripts\\python.exe langgraph_control_flow_demo.py

说明：全部用「假模型 / 假工具」，不调任何真实 API，不需要 API key，离线可跑。
每个 demo_xxx() 函数对应课程的一批知识点，函数开头标注了对应的集数。
"""

import operator
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, TypedDict

from langgraph.cache.memory import InMemoryCache
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import CachePolicy, Command, RetryPolicy, Send
import sys
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")



def banner(n: int, title: str):
    print("\n" + "=" * 60)
    print(f"[{n}] {title}")
    print("=" * 60)


def tool_calls_of(msg):
    """兼容 dict 消息和消息对象两种形态，取出 tool_calls"""
    if isinstance(msg, dict):
        return msg.get("tool_calls") or []
    return getattr(msg, "tool_calls", None) or []


# ---------------------------------------------------------------- 19-21 状态
def demo_01_state_and_reducer():
    banner(1, "状态读写与 reducer（19-21 集）")

    class State(TypedDict):
        messages: Annotated[list, add_messages]  # 配 reducer：追加
        step: int                                 # 没配 reducer：覆盖

    def node_a(state):
        return {"messages": [{"role": "user", "content": "你好"}],
                "step": state["step"] + 1}

    def node_b(state):
        return {"messages": [{"role": "assistant", "content": "我在"}],
                "step": state["step"] + 1}

    g = StateGraph(State)
    g.add_node("node_a", node_a)
    g.add_node("node_b", node_b)
    g.add_edge(START, "node_a")
    g.add_edge("node_a", "node_b")
    g.add_edge("node_b", END)

    out = g.compile().invoke({"messages": [], "step": 0})
    print(f"messages 条数 = {len(out['messages'])}   ← 配了 add_messages：两个节点的都留着")
    print(f"step = {out['step']}   ← 没配 reducer：后写的覆盖先写的，最后是 2")
    print(f"最后一条消息内容 = {out['messages'][-1].content}")


# -------------------------------------------------------------- 30-31 并行
def demo_02_parallel():
    banner(2, "并行执行：一个节点连多条边（30-31 集）")

    class State(TypedDict):
        results: Annotated[list, operator.add]   # 并行写同一字段，必须配 reducer

    def source(state):
        return {"results": ["A 出发"]}

    def fast(state):
        return {"results": ["fast 完成（快）"]}

    def slow(state):
        time.sleep(0.5)
        return {"results": ["slow 完成（慢，睡了 0.5 秒）"]}

    def join(state):
        return {"results": ["汇合完成"]}

    g = StateGraph(State)
    g.add_node("source", source)
    g.add_node("fast", fast)
    g.add_node("slow", slow)
    g.add_node("join", join)
    g.add_edge(START, "source")
    g.add_edge("source", "fast")   # 同一个 source 连两条边
    g.add_edge("source", "slow")   # → fast 和 slow 并行跑
    g.add_edge("fast", "join")     # 汇合点
    g.add_edge("slow", "join")
    g.add_edge("join", END)

    app = g.compile()
    print("用 stream() 观察：谁先跑完谁先出现，但 join 一定在最后（super-step 等最慢的）")
    for chunk in app.stream({"results": []}):
        print("   ", chunk)


# ------------------------------------------------------------ 29 add_sequence
def demo_03_add_sequence():
    banner(3, "add_sequence：线性流程批量连线（29 集）")

    class State(TypedDict):
        log: Annotated[list, operator.add]

    def step1(state):
        return {"log": ["step1"]}

    def step2(state):
        return {"log": ["step2"]}

    def step3(state):
        return {"log": ["step3"]}

    g = StateGraph(State)
    g.add_sequence([step1, step2, step3])   # 节点名默认用函数名
    g.add_edge(START, "step1")
    g.add_edge("step3", END)

    print(g.compile().invoke({"log": []}))


# ------------------------------------------------- 32-33 条件分支 + path_map
def demo_04_conditional_pathmap():
    banner(4, "条件分支 + path_map（32-33 集）")

    class State(TypedDict):
        score: int
        log: Annotated[list, operator.add]

    def judge(state):
        return {"log": [f"得分 {state['score']}"]}

    def route(state):
        # 只返回抽象标签，不关心图里节点叫什么 —— 这就是解耦
        return "pass" if state["score"] >= 60 else "fail"

    def on_pass(state):
        return {"log": ["→ 及格，结束"]}

    def on_fail(state):
        return {"log": ["→ 补考"]}

    g = StateGraph(State)
    g.add_node("judge", judge)
    g.add_node("on_pass", on_pass)
    g.add_node("on_fail", on_fail)
    g.add_edge(START, "judge")
    g.add_conditional_edges("judge", route, path_map={"pass": "on_pass",
                                                      "fail": "on_fail"})
    g.add_edge("on_pass", END)
    g.add_edge("on_fail", END)

    app = g.compile()
    for s in (85, 30):
        print(f"  分数 {s} →", app.invoke({"score": s, "log": []})["log"])


# ------------------------------------------- 42/44 静态循环 + recursion_limit
def demo_05_static_loop_and_limit():
    banner(5, "静态循环（计数器）+ 循环保险丝（42、44 集）")

    class State(TypedDict):
        count: int                             # 覆盖型字段
        log: Annotated[list, operator.add]     # 追加型字段

    def work(state):
        return {"count": state["count"] + 1,
                "log": [f"第 {state['count'] + 1} 圈"]}

    def loop_or_stop(state):
        return "work" if state["count"] < 3 else END     # 跑满 3 圈就停

    def always_loop(state):
        return "work"                                    # 故意写坏：永远不停

    def build(route_fn):
        g = StateGraph(State)
        g.add_node("work", work)
        g.add_edge(START, "work")
        g.add_conditional_edges("work", route_fn)
        return g.compile()

    print("正常版（跑满 3 圈停）：", build(loop_or_stop).invoke({"count": 0, "log": []}))

    print("\n故意写坏（永远循环），这时靠 recursion_limit 兜底：")
    try:
        build(always_loop).invoke({"count": 0, "log": []},
                                  config={"recursion_limit": 6})
    except GraphRecursionError:
        print("   抛 GraphRecursionError → 图被强制刹车，没有烧钱转到底")


# ------------------------------------------- 40-41/43 假 agent 动态循环
def demo_06_fake_agent_loop():
    banner(6, "假 agent：model ↔ tools 动态循环（40-41、43 集）")

    TOOLS = {"get_weather": lambda city: f"{city} 28℃ 晴"}

    class State(TypedDict):
        messages: Annotated[list, add_messages]
        log: Annotated[list, operator.add]

    def fake_model(state):
        """假装是大模型：第一次说 我要查天气，第二次说 够了"""
        already_called = any("调用工具" in x for x in state["log"])
        if not already_called:
            return {
                "messages": [{"role": "assistant", "content": "", "tool_calls": [
                    {"name": "get_weather", "args": {"city": "广州"}, "id": "call_1"}]}],
                "log": ["模型：我不知道天气，要用工具"],
            }
        return {"messages": [{"role": "assistant", "content": "广州今天 28℃，挺热的"}],
                "log": ["模型：够了，给出最终回答"]}

    def tools_node(state):
        """真的去执行工具（对应 ToolNode 干的事）"""
        call = tool_calls_of(state["messages"][-1])[0]
        get = (lambda k: call.get(k)) if isinstance(call, dict) else (lambda k: getattr(call, k, None))
        name, args, cid = get("name"), get("args"), get("id")
        result = TOOLS[name](**args)
        # tool_call_id 必须带上，它把「工具结果」和「哪次调用」对上号
        return {"messages": [{"role": "tool", "name": name, "content": result,
                              "tool_call_id": cid}],
                "log": [f"调用工具 {name} → {result}"]}

    def should_continue(state):
        """对应官方的 tools_condition：看最后一条消息有没有 tool_calls"""
        return "tools" if tool_calls_of(state["messages"][-1]) else END

    g = StateGraph(State)
    g.add_node("model", fake_model)
    g.add_node("tools", tools_node)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", should_continue)
    g.add_edge("tools", "model")     # ← 这条往回的边 = 循环

    out = g.compile().invoke({"messages": [], "log": []})
    print("执行轨迹：")
    for line in out["log"]:
        print("   ", line)
    print("最终回答：", out["messages"][-1].content)


# ---------------------------------------------- 37/45 Command 控制与主动退出
def demo_07_command():
    banner(7, "Command：改状态 + 决定去哪，一步完成（37、45 集）")

    class State(TypedDict):
        score: int
        log: Annotated[list, operator.add]

    def checker(state) -> Command:
        if state["score"] < 0:      # 紧急情况：干活的人自己喊停
            return Command(update={"log": ["分数异常，紧急刹车"]}, goto=END)
        return Command(update={"log": ["分数正常，继续"]}, goto="work")

    def work(state):
        return {"log": ["work 节点干完活"]}

    g = StateGraph(State)
    g.add_node("checker", checker)
    g.add_node("work", work)
    g.add_edge(START, "checker")
    g.add_edge("work", END)
    # 注意：checker 没有静态出边，去哪全由 Command 说了算

    app = g.compile()
    print("正常输入 score=80 →", app.invoke({"score": 80, "log": []})["log"])
    print("异常输入 score=-1 →", app.invoke({"score": -1, "log": []})["log"])


# ------------------------------------------------ 39 Send 动态扇入 map-reduce
def demo_08_send_map_reduce():
    banner(8, "Send：动态扇入，派 N 个实例各干各的（39 集）")

    class State(TypedDict):
        topics: list
        topic: str                                  # 每个派生实例自己的输入
        summaries: Annotated[list, operator.add]

    def start(state):
        return {"topics": ["天气", "交通", "美食"]}   # 几个主题运行时才知道

    def dispatch(state):
        # 有几个 topic 就现场派几个 summarize，各带各的输入
        return [Send("summarize", {"topic": t}) for t in state["topics"]]

    def summarize(state):
        return {"summaries": [f"{state['topic']} -> 总结完成"]}

    g = StateGraph(State)
    g.add_node("start", start)
    g.add_node("summarize", summarize)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", dispatch)
    g.add_edge("summarize", END)

    out = g.compile().invoke({"topics": [], "topic": "", "summaries": []})
    print("汇总结果：", out["summaries"])


# ----------------------------------------------------- 48-49 重试 RetryPolicy
def demo_09_retry():
    banner(9, "RetryPolicy：偶发失败自动重试（48-49 集）")

    class State(TypedDict):
        log: Annotated[list, operator.add]

    counter = {"n": 0}

    def flaky(state):
        counter["n"] += 1
        if counter["n"] < 3:
            raise ConnectionError(f"网络抖了一下（第 {counter['n']} 次失败）")
        return {"log": [f"第 {counter['n']} 次终于成功"]}

    g = StateGraph(State)
    g.add_node("flaky", flaky, retry=RetryPolicy(
        max_attempts=3,
        retry_on=(ConnectionError,),   # 只重试网络类偶发错误
        initial_interval=0.1,          # 第一次等 0.1 秒（演示用，真实场景给 1-2 秒）
        backoff_factor=2.0,            # 每次翻倍，这叫指数退避
    ))
    g.add_edge(START, "flaky")
    g.add_edge("flaky", END)

    out = g.compile().invoke({"log": []})
    print("结果：", out["log"], f"（内部实际尝试了 {counter['n']} 次）")


# ------------------------------------- 50-51 超时 + 错误处理 + 降级兜底
def demo_10_timeout_and_fallback():
    banner(10, "超时控制 + 错误处理 + 降级兜底（50-51 集）")

    class State(TypedDict):
        result: str
        error: str
        log: Annotated[list, operator.add]

    def slow_call():
        time.sleep(5)          # 假装这是个很慢的外部服务
        return "真实结果"

    def call_service(state):
        """超时设在你调外部服务的那一刻，并用 try/except 把错误变成数据"""
        with ThreadPoolExecutor() as pool:
            future = pool.submit(slow_call)
            try:
                return {"result": future.result(timeout=1), "error": None,
                        "log": ["调用成功"]}
            except Exception as e:
                return {"result": None, "error": f"{type(e).__name__}: 超时了",
                        "log": ["调用失败，错误已写进 State"]}

    def route(state):
        return "fallback" if state.get("error") else "normal"

    def normal(state):
        return {"log": [f"正常返回：{state['result']}"]}

    def fallback(state):
        return {"log": ["降级：服务不可用，先用固定话术顶一下"]}

    g = StateGraph(State)
    g.add_node("call_service", call_service)
    g.add_node("normal", normal)
    g.add_node("fallback", fallback)
    g.add_edge(START, "call_service")
    g.add_conditional_edges("call_service", route)
    g.add_edge("normal", END)
    g.add_edge("fallback", END)

    out = g.compile().invoke({"result": None, "error": None, "log": []})
    print("错误字段：", out["error"])
    for line in out["log"]:
        print("   ", line)


# ------------------------------------------------- 52-55 节点缓存 CachePolicy
def demo_11_cache():
    banner(11, "节点缓存：CachePolicy + 缓存仓库（52-55 集）")

    class State(TypedDict):
        n: int

    def slow(state):
        time.sleep(1.0)             # 假装很贵：真实场景是 LLM 调用或检索
        return {"n": state["n"] + 1}

    g = StateGraph(State)
    g.add_node("slow", slow, cache_policy=CachePolicy(ttl=60))   # 零件一：节点声明
    g.add_edge(START, "slow")
    g.add_edge("slow", END)

    app = g.compile(cache=InMemoryCache())                       # 零件二：图配仓库

    t0 = time.time()
    print("第一次：", app.invoke({"n": 0}), f"耗时 {time.time() - t0:.2f} 秒")

    t0 = time.time()
    print("第二次：", app.invoke({"n": 0}), f"耗时 {time.time() - t0:.2f} 秒  ← 命中缓存，节点根本没跑")

    t0 = time.time()
    print("换个输入：", app.invoke({"n": 100}), f"耗时 {time.time() - t0:.2f} 秒  ← 输入变了，缓存没命中")


# ---------------------------------------------------------- 13 图结构可视化
def demo_12_visualize():
    banner(12, "图结构可视化（13 集）")

    class State(TypedDict):
        log: Annotated[list, operator.add]

    def route(state):
        return "go"      # 返回抽象标签，交给 path_map 查表

    g = StateGraph(State)
    g.add_node("a", lambda s: {"log": ["a"]})
    g.add_node("b", lambda s: {"log": ["b"]})
    g.add_edge(START, "a")
    g.add_conditional_edges("a", route, path_map={"go": "b"})
    g.add_edge("b", END)

    try:
        print(g.compile().get_graph().draw_ascii())
    except ImportError:
        print("缺依赖，先装一下：D:\\Python-project\\.venv\\Scripts\\python.exe -m pip install grandalf")
    print("注意：* 是固定边（add_edge），. 是条件边（add_conditional_edges）")
    print("提示：app.get_graph().draw_mermaid_png() 可以导出 PNG（需要联网走 mermaid.ink）")


if __name__ == "__main__":
    demo_01_state_and_reducer()
    demo_02_parallel()
    demo_03_add_sequence()
    demo_04_conditional_pathmap()
    demo_05_static_loop_and_limit()
    demo_06_fake_agent_loop()
    demo_07_command()
    demo_08_send_map_reduce()
    demo_09_retry()
    demo_10_timeout_and_fallback()
    demo_11_cache()
    demo_12_visualize()
    print("\n全部跑完 ✓  每个 demo_xx 函数都可以单独注释掉，只跑你想看的那一段。")

# -*- coding: utf-8 -*-
"""
Python 语法补漏：LangGraph 代码里天天见到的那些写法
============================================================
跑法：python python_syntax_for_langgraph.py

结构：每个语法点 = 一句话说明 + 最小例子 + 「LangGraph 里在哪见过」
不需要装任何包，标准库就能跑。
"""


def s(n, title):
    print("\n" + "=" * 62)
    print(f"第 {n} 组：{title}")
    print("=" * 62)


# =========================================================
s(1, "函数签名：冒号和箭头都是「给人看的备注」")
# =========================================================

def apply_update(channels: dict, updates: dict) -> dict:
    """channels: dict 是备注；-> dict 也是备注；末尾这个冒号才是函数体开始"""
    return channels


import sys
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("调用：", apply_update({"a": 1}, {"b": 2}))
print("把备注全删掉，完全等价 ↓")


def apply_update_plain(channels, updates):
    return channels


print("调用：", apply_update_plain({"a": 1}, {"b": 2}))
print("→ Python 运行时【不检查】类型，你传个列表进去照样跑，只是 IDE 画黄线")
print("→ LangGraph 里：def node(state: State) -> dict   ← 照着抄就行，不用怕")


# =========================================================
s(2, "字典：State 本质就是个字典")
# =========================================================

state = {"messages": ["你好", "第 2 圈：还有错误"], "count": 2}

print("① 取值          ：", state["count"])
print("② 取不存在的键  ：state['score'] 会直接崩（KeyError）")
print("   所以用 .get()：", state.get("score"), " ← 不存在就返回 None，不崩")
print("   给个默认值   ：", state.get("score", 0))
print("③ 负索引        ：", state["messages"][-1], " ← -1 = 倒数第一个")
print("   （[0] 是正数第一个，[-1] 是倒数第一个，[-2] 是倒数第二个）")
print("④ in 用在字符串上 = 包含判断：", "错误" in state["messages"][-1])
print("   in 用在列表上   = 成员判断：", "你好" in state["messages"])
print("⑤ f-string 往字符串里塞变量：", f"一共 {state['count']} 条消息")
print("   （前面的 f 不能漏，{} 里写变量或表达式）")
print("→ LangGraph 里：state['messages']、state.get('error') 到处都是")


# =========================================================
s(3, "推导式：一句话造出一个新容器")
# =========================================================

nums = [1, 2, 3]
d = {"a": 1, "b": 2}

print("原列表：", nums)
print("列表推导 [x * 10 for x in nums]        ：", [x * 10 for x in nums])
print("带过滤   [x for x in nums if x > 1]    ：", [x for x in nums if x > 1])
print("字典推导 {k: v * 10 for k, v in d.items()}：", {k: v * 10 for k, v in d.items()})
print("键值反过来 {v: k for k, v in d.items()}    ：", {v: k for k, v in d.items()})

print("\n字典推导展开成普通循环，完全等价 ↓")
result = {}
for k, v in d.items():
    result[k] = v * 10
print("  ", result)

print("\n记忆法：外层符号决定造出什么 —— [] 造列表，{} 造字典")
print("→ LangGraph 里：{k: c.value for k, c in channels.items()}")
print("→ 还有 Send 那集：[Send('reduce', {'topic': t}) for t in state['topics']]")


# =========================================================
s(4, "三种简写：三元表达式、lambda、关键字参数")
# =========================================================

score = 75
print("三元表达式一行版：", "及格" if score >= 60 else "补考")
print("展开成 if/else ↓")
if score >= 60:
    print("   及格")
else:
    print("   补考")
print("读法：『如果条件成立就取冒号左边，否则取右边』")

print("\nlambda = 不起名字的一次性小函数")
add = lambda a, b: a + b
print("  add(1, 2) =", add(1, 2))
print("  等价于：def add(a, b): return a + b")
print("→ LangGraph 里：key_func=lambda s: s['query']")


print("\n关键字参数：写名字调用，不靠位置")
def make(ttl, key_func=None):
    return f"ttl={ttl}, key_func={key_func}"


print(" ", make(60))                              # 位置参数：按顺序对号入座
print(" ", make(ttl=60))                          # 关键字参数：点名道姓
print(" ", make(key_func="abc", ttl=10))          # 点名了，顺序乱了也没事
print("→ LangGraph 里：RetryPolicy(max_attempts=3, retry_on=(ConnectionError,))")


# =========================================================
s(5, "TypedDict / 继承 / Annotated：State 是怎么定义的")
# =========================================================

from typing import Annotated, TypedDict


class InputState(TypedDict):
    question: str


class PrivateState(TypedDict):
    docs: list


class GraphState(InputState, PrivateState):
    """括号里写谁，就白得谁的字段 —— 这叫『继承』"""
    pass          # pass = 什么都不做，纯粹占个位


print("GraphState 的字段：", list(GraphState.__annotations__.keys()))
print("  ↑ 多继承 = 把几个 TypedDict 的字段并成一份")


def add_messages(old, new):
    return (old or []) + new


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]     # 类型是 list，后面贴了张便签


print("\nAnnotated = 给类型后面『贴便签』")
print("  类型本身：", ChatState.__annotations__["messages"].__origin__)
print("  便签内容：", ChatState.__annotations__["messages"].__metadata__)
print("→ LangGraph 读到这张便签，就知道『新值来了要追加，不能覆盖』")


# =========================================================
s(6, "异常：raise / try / except")
# =========================================================

def call_service(nth):
    if nth <= 2:
        raise ConnectionError("连接被重置")     # raise = 主动抛出一个异常
    return "成功"


print("① 不接住就崩。外面套 try/except 才接得住：")
try:
    call_service(1)
except Exception as e:
    print("   接住了 →", type(e).__name__, ":", e)

print("\n② LangGraph 的『降级』写法：接住后当成数据，不往外抛")
error = None
try:
    r = call_service(1)
except Exception as e:
    error = str(e)
print("   error =", error)
print("   → 写进 State，再用条件边送去兜底节点")

print("\n③ except 后面写具体类型 = 只接那一种")
try:
    call_service(1)
except ConnectionError as e:
    print("   只接 ConnectionError →", e)
print("→ 这正好对应 RetryPolicy(retry_on=(ConnectionError,))：")
print("  节点【不吞异常】，框架才有的重试；吞了就永远不触发")


# =========================================================
s(7, "实战：一段真实代码，逐行标出用了哪些语法")
# =========================================================

print("""
class State(TypedDict):                       ① class 定义 ② 继承 TypedDict
    messages: Annotated[list, add_messages]   ③ 类型标注 ④ Annotated 贴便签
    count: int

def should_continue(state: State) -> str:     ⑤ 类型标注 + ⑥ 箭头（都是备注）
    last = state["messages"][-1]              ⑦ 字典取值 ⑧ 负索引取最后一项
    if "错误" in last:                         ⑨ in 判断字符串是否包含
        return "rewrite"
    return END                                 ⑩ 普通 return，END 是个常量

g.add_node("slow", fn,                        ⑪ 关键字参数
           retry=RetryPolicy(max_attempts=3),
           cache_policy=CachePolicy(ttl=60))
app = g.compile(cache=InMemoryCache())
app.invoke({"messages": []}, config={"recursion_limit": 10})
                                              ⑫ 字面量字典直接当参数传进去
""")

# =========================================================
s(8, "拆开一个真实节点函数：零简写版对照")
# =========================================================

import time
from concurrent.futures import ThreadPoolExecutor


def external_service():
    """假装是个很慢的外部接口"""
    time.sleep(0.6)
    return "「广州天气」的结果"


def call_with_timeout(fn, timeout):
    """fn = 一件『待会儿要做的事』；timeout = 最多等几秒"""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn).result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def call_api_verbose(state, timeout):
    """零简写版：不用 lambda，也不把一长串表达式塞进 return"""
    try:
        # ① 先定义一个「待会儿要做的事」
        def job():
            return external_service()

        # ② 把这件事交给 call_with_timeout，让它最多等 timeout 秒
        result = call_with_timeout(job, timeout=timeout)

        # ③ 没出事：装进字典，正常返回
        out = {"result": result, "error": ""}
        return out

    except Exception as e:
        # ④ 出事了：e 是「异常对象」，str(e) 是它的一句话说明
        out = {"error": f"{type(e).__name__}: {e}"}
        #        ↑ 连【类型名】一起记，因为有些异常（比如超时）的说明文字是空的
        return out


print("给 2 秒（够用）  →", call_api_verbose({}, 2))
print("给 0.3 秒（不够）→", call_api_verbose({}, 0.3))
print("  ↑ 第二次没崩，error 里躺着一句说明 —— 这就是降级")
print("  ↑ 注意 error 只有『TimeoutError:』没有内容：超时异常的说明文字本身是空的，")
print("    所以真实项目里要写成 f'{type(e).__name__}: {e}'，连类型名一起记")

print("\nlambda 就是把上面那个 job 压缩成一行：")
job = lambda: external_service()
print("  完整版：def job(): return external_service()")
print("  简写版：job = lambda: external_service()")
print("  两者 job() 结果一样：", job())

print("\n★ 为什么必须传『函数本身』，不能写 external_service()：")
print("  写 external_service()  = 事情【当场就做完了】，把结果传过去")
print("  写 external_service    = 把【要做的事】传过去，由对方决定何时做")
print("  前者做完才交给 call_with_timeout，超时机制根本来不及生效 —— 新手最常踩的坑")

print("\n最后：缩进。Python 靠缩进认代码块，try / except 必须对齐：")
print("""
  def call_api(state):
      try:                        ← 缩进 4 格
          return {...}            ← 再缩进 4 格，属于 try
      except Exception as e:      ← 和 try 对齐
          return {...}            ← 属于 except
""")

print("\n" + "=" * 62)
print("第 9 组：app = g.compile(checkpointer=ckpt) 这一行到底在干嘛")
print("=" * 62)


# 先造两个"假类"，把真实的 LangGraph 行为模仿出来，不装任何依赖也能跑
class FakeSaver:
    """假装是存档器（InMemorySaver / PostgresSaver）"""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<存档器 {self.name}>"


class FakeGraph:
    """假装是 StateGraph（图纸）"""

    def compile(self, checkpointer=None, cache=None):
        # ← 注意这里的参数写法：checkpointer=None 叫"默认参数"
        return FakeApp(self, checkpointer)


class FakeApp:
    """假装是 compile 的产物（能跑的应用）"""

    def __init__(self, graph, checkpointer):
        self.graph = graph
        self.checkpointer = checkpointer

    def __repr__(self):
        return f"<应用，存档器={self.checkpointer}>"

    def invoke(self, inputs):
        return f"跑完了，用的是 {self.checkpointer}"


print("\n① g.compile(...)  = 调用【对象的方法】")
print("   语法：对象名.方法名()")
g = FakeGraph()                    # g 是一个对象（图纸）
print("   g 本身        ：", type(g).__name__)
print("   还没 compile  ：图纸不能 invoke，只能继续 add_node / add_edge")

print("\n② checkpointer=ckpt = 【关键字参数】")
print("   就是第 4 组讲过的『点名道姓传参』，名字是框架规定好的，不能自己改")
ckpt = FakeSaver("postgres")       # 先造好存档器，存进变量 ckpt
print("   ckpt 这个变量 ：", ckpt)

print("\n③ app = ... = 【赋值】")
print("   把右边算出来的结果，起个名字叫 app，以后用 app 就能找到它")

print("\n④ 三个零件合起来就是那一行：")
# 这一行是"简写版"，等价于下面拆开的三步
app = g.compile(checkpointer=ckpt)
print("   app = g.compile(checkpointer=ckpt)  →", app)

print("\n⑤ 拆成零简写版（三步，完全等价）：")
ckpt_2 = FakeSaver("postgres")        # 第一步：准备好存档器
app_2 = g.compile(checkpointer=ckpt_2)  # 第二步：把存档器交给 compile
print("   结果：", app_2)              # 第三步：app_2 就是成品，后面 app_2.invoke()

print("\n⑥ 为什么 compile 不能省？两种对象能干的事不一样：")
print("   g（图纸）    ：能 add_node / add_edge —— 还在设计阶段")
print("   app（成品）  ：能 invoke / stream / get_state —— 已经能跑")
print("   跑一下试试  ：", app.invoke({"messages": ["你好"]}))

print("\n⑦ 同一个 g，换不同存档器，得到不同的 app：")
print("   不存档     ：", g.compile())
print("   内存存档   ：", g.compile(checkpointer=FakeSaver("内存")))
print("   Postgres   ：", g.compile(checkpointer=FakeSaver("postgres")))
print("   → 图的定义一行没改，只换了 compile 的括号里传什么")

print("\n★ 新手最容易搞混的两件事：")
print("   1. compile 里的是【全图公共设施】，add_node 里的是【单个节点私人设置】")
print("      add_node(..., retry=...)      只管那一个节点")
print("      compile(cache=...)            整张图共用")
print("   2. 括号里的名字是【框架规定死的】，写错不会报错，而是被当成普通参数忽略或报未知参数错")
print("      正确：compile(checkpointer=ckpt)")
print("      错误：compile(saver=ckpt)      ← 自己起的名字，框架不认")

print("\n\n")
print("=" * 62)
print("第 10 组：open(...).close() 和 invoke(None, cfg)")
print("（71-73 集崩溃恢复那两行）")
print("=" * 62)

print("\n原句：")
print("   open(FLAG, \"w\").close()     # 造一个空文件当‘bug 修好了’的标记")
print("   app.invoke(None, cfg)       # None = 不传新输入，从存档接着跑")

print("\n① open(FLAG, \"w\").close() —— 链式调用：括号后面还能再点一下")
print("   拆成零简写版：")
flag_path = "demo_bug_fixed.flag"        # 真在磁盘上造一个，让你看见确实生效了
f = open(flag_path, "w")                 # 第一步：open() 打开（没有就新建），返回一个【文件对象】
f.close()                                # 第二步：对那个对象调 .close()，关掉它
print("   f = open(flag, 'w')  → ", type(f).__name__, "（这就是‘文件对象’）")
print("   f.close()            → 关掉，内容就真的写到磁盘上了")
import os as _os
print("   文件现在存在吗：", _os.path.exists(flag_path), " 大小：", _os.path.getsize(flag_path), "字节")
print("   → open(...).close() 只是把上面两行挤成一行：拿 open 的返回值，立刻关掉")
print("   → 用途：文件里什么都不写，光‘存在’这个事实本身就是信号（这里当‘bug 修好了’标记）")
print("   更常见的写法是 with：with open(flag, 'w') as f: pass   （不用手动 close）")
_os.remove(flag_path)

print("\n② app.invoke(None, cfg) —— None 也是一个合法的‘值’")
print("   None 的意思：‘这里什么都没有’（别的语言里叫 null / nil）")
print("   它是【位置参数】：靠站的位置决定它是谁，第一个位置就是第 1 个参数")


class FakeApp:
    """模仿 langgraph 的 app，用打印说明 None 和正常输入的区别"""
    def invoke(self, user_input, cfg=None):
        if user_input is None:
            return "（没给新输入）→ 从存档接着跑：boom → finish"
        return f"（收到新输入 {user_input}）→ 加进存档，从头往后跑：prepare → boom → finish"


app = FakeApp()
print("   传正常输入：", app.invoke({"messages": ["开始"]}, {}))
print("   传 None   ：", app.invoke(None, {}))
print("   → 判断‘有没有给输入’要用 is None，不是 == None：")
print("     x is None →", None is None, " | x == None →", None is None, "（新手常写成 == None，能用但不规范）")

print("\n③ 为什么这俩会凑在一起（71-73 集的场景）：")
print("   第一次跑 → boom 节点崩溃 → 但 prepare 的结果已经存档了")
print("   造标记文件   = 模拟‘工程师把 bug 修好了’")
print("   invoke(None) = 程序重启后，什么都不用传，直接从存档那一步接着跑")

print("\n\n")
print("=" * 62)
print("第 11 组：open(FLAG, \"w\") 里 FLAG 和 \"w\" 分别是什么")
print("=" * 62)

print("\n① FLAG —— 一个普通的字符串，内容是【文件路径】")
print("   它在 demo 里是这么来的：")
print("   FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bug_fixed.flag')")
print("   拆开看：")
print("     __file__                 当前这个 .py 文件的路径")
print("     os.path.abspath(__file__) 转成绝对路径")
print("     os.path.dirname(...)      去掉文件名，只留文件夹")
print("     os.path.join(文件夹, 'bug_fixed.flag')  拼出完整路径")
import os as _os2
print("   拼出来的真值：", _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), "bug_fixed.flag"))
print("   它的类型：", type(_os2.path.join("a", "b")).__name__, " —— 就是个字符串，跟 'C:/xx/yy.txt' 没区别")
print("   → 起个大写的名字只是为了标明‘这是个常量，别改它’（约定，不是语法强制）")

print("\n② \"w\" —— 打开方式（mode），一个字母就决定了行为")
print("   'r'  读（默认，不写 mode 就是这个）：文件不存在会报错")
print("   'w'  写：文件不存在就新建；【已存在就清空重写】")
print("   'a'  追加：写在文件末尾，不清空原来内容")
print("   'b'  配在后面表示二进制，如 'rb' / 'wb'")

_p = "demo_mode.txt"
with open(_p, "w") as _f:
    _f.write("第一次\n")
with open(_p, "w") as _f:
    _f.write("第二次\n")
print("   实测：用 'w' 写两次 →", repr(open(_p).read()), "  ← 第一次的内容被清空了")
with open(_p, "a") as _f:
    _f.write("第三次\n")
print("   实测：再用 'a' 写一次 →", repr(open(_p).read()), "  ← 追加在后面")
_os2.remove(_p)

print("\n③ 回到那行 open(FLAG, \"w\").close() ：")
print("   用【写方式】打开这个路径的文件（没有就新建），然后立刻关掉")
print("   → 结果：磁盘上多了一个 0 字节的空文件 bug_fixed.flag")
print("   → 我们要的不是文件内容，而是‘它存在’这个事实（当作 bug 修好的信号）")

print("\n\n")
print("=" * 62)
print("第 12 组：回档三行 —— next(生成器)、字典套字典、跨行写字典")
print("（74 集：从历史里挑一份快照，回档重跑）")
print("=" * 62)

print("\n原句：")
print('   target = next(s for s in history if s.metadata["step"] == 1)')
print('   cfg2 = {"configurable": {"thread_id": tid,')
print('                            "checkpoint_id": target.config["configurable"]["checkpoint_id"]}}')
print("   app.invoke(None, cfg2)")


class FakeSnap:
    """模仿 get_state_history 返回的每一份快照"""
    def __init__(self, step, cid):
        self.metadata = {"step": step}
        self.config = {"configurable": {"thread_id": "T1", "checkpoint_id": cid}}


history = [FakeSnap(3, "aaa333"), FakeSnap(2, "bbb222"), FakeSnap(1, "ccc111"), FakeSnap(0, "ddd000")]

print("\n① next(s for s in history if ...) —— ‘找出第一个符合条件的’")
print("   拆成零简写版（完全等价）：")
target = None
for s in history:                      # 一个个看
    if s.metadata["step"] == 1:        # 条件是：step 等于 1
        target = s                     # 找到了就记下来
        break                          # 停，不再往后看
print("   找到的是 step =", target.metadata["step"], "，它的 id =", target.config["configurable"]["checkpoint_id"])
print("   速记：next( 一个个看 for ... if 条件 ) = 取第一个满足条件的；找不到会抛 StopIteration")
print("   注意 s.metadata['step'] 是【两层取值】：先取 metadata 这个字典，再取里面的 step")

print("\n② target.config[\"configurable\"][\"checkpoint_id\"] —— 剥洋葱，一层一层取")
print("   第 1 层 target.config            =", target.config)
print("   第 2 层 target.config[\"configurable\"] =", target.config["configurable"])
print("   第 3 层 再取 [\"checkpoint_id\"]        =", target.config["configurable"]["checkpoint_id"])
print("   → 连续的 [...][...] 就是‘取出来还是字典，再往里取一层’")

print("\n③ 字典字面量跨行写 —— 括号没闭合，Python 就当还是同一行")
tid = "T1"
cid = target.config["configurable"]["checkpoint_id"]
cfg2 = {"configurable": {"thread_id": tid,
                         "checkpoint_id": cid}}
print("   结果：", cfg2)
print("   零简写版（一步步往里塞，完全等价）：")
cfg3 = {}
cfg3["configurable"] = {}
cfg3["configurable"]["thread_id"] = tid
cfg3["configurable"]["checkpoint_id"] = cid
print("   结果：", cfg3, " → 跟上面一样：", cfg3 == cfg2)

print("\n④ 三行合起来的意思：")
print("   第 1 行：在存档历史里，找到 step 等于 1 的那一份快照")
print("   第 2 行：造一个‘回档地址’——哪个 thread + 跳到哪一份快照")
print("   第 3 行：传 None（不给新输入）+ 这个地址 → 从那一份快照重新往后跑")
print("   → 这就是 74 集的‘时间旅行’，跑完会分叉出一条新时间线（75 集的 fork）")

print("\n\n")
print("=" * 62)
print("第 13 组：InMemoryStore、store、store.get 到底谁是类谁是对象")
print("=" * 62)

# 用两个假类演示（不装 langgraph 也能跑）——逻辑和真的完全一样
class InMemoryStore:                       # ① 这是个【类】：模具，大写开头
    def __init__(self):
        self._data = {}

    def put(self, namespace, key, value):  # ② 类里面 def 出来的叫【方法】
        self._data[(namespace, key)] = value

    def get(self, namespace, key):         # ③ get 也是方法
        v = self._data.get((namespace, key))
        return Item(v, namespace, key) if v else None


class Item:                                # ④ get 返回的东西：一个打包好的对象
    def __init__(self, value, namespace, key):
        self.value = value
        self.namespace = namespace
        self.key = key


print("\n① 谁是谁（真在 langgraph 里跑过的结果）：")
print("   type(InMemoryStore) = 类本身（模具）")
print("   type(store)         = InMemoryStore（造出来的对象）")
print("   type(store.get)     = method（绑定在对象上的函数）")
print()
print("   判断口诀：【首字母大写的是类，小写变量是对象】")

store = InMemoryStore()                    # 类名() = 造对象，跟 StateGraph(State) 一模一样
print("\n   store = InMemoryStore()")
print("   → type(store) =", type(store).__name__)

print("\n② store.get(...) 是【对象.方法()】——对一个东西下达一个动作")
print("   类比：")
print("     store.get(抽屉, 标签)     对 store 下达‘取’的动作")
print("     app.invoke(输入, cfg)     对 app 下达‘跑’的动作")
print("     g.add_node('a', fn)       对 g 下达‘加节点’的动作")
print("   → 全是同一个语法：对象名 . 动作名 ( 参数 )")

store.put(("users", "inward"), "prefs", {"theme": "深色"})
item = store.get(("users", "inward"), "prefs")
print("\n③ get 拿回来的不是裸数据，是一个打包好的 Item 对象：")
print("   type(item)  =", type(item).__name__)
print("   item.value  =", item.value, "  ← 【要的东西在这里面】")
print("   item.key    =", item.key)
print("   item.namespace =", item.namespace)
print("   → 为什么多包一层？因为除了内容，还得带上‘它在哪个抽屉、贴的什么标签’")

print("\n④ 常见坑：item 和 item.value 不是一回事")
print("   print(item)        →", item, "  （对象本尊，看不懂）")
print("   print(item.value)  →", item.value, "  （这才是你要的数据）")
print("   → 跟 checkpoint 那边的 snap.values 一个道理：先拿盒子，再掏东西")

print("\n\n")
print("=" * 62)
print("第 14 组：def chat_node(state, *, store) 里那个孤零零的 * 是干嘛的")
print("=" * 62)


def chat_node(state, *, store):        # * 是【分隔符】：它右边的参数只能按名字传
    return f"state={state} | store={store}"


print("\n① * 的作用：把它后面的参数变成【只能用名字传】")
print("   正确：", chat_node({"q": 1}, store="仓库A"))
try:
    chat_node({"q": 1}, "仓库A")       # 想把 store 按位置传
except TypeError as e:
    print("   按位置传会报错：", e)
print("   → 报错信息里的 keyword-only 就是‘只能用名字’的意思")

print("\n② 为什么框架要这么写？")
print("   节点函数第一个参数永远是 state（框架按位置喂）")
print("   后面的 store / config / runtime 是按【名字】喂的：框架内部是 node(state, store=...)")
print("   加 * 就是防止有人不小心把别的玩意儿当位置参数塞进来")

print("\n③ 不传行不行？不行（* 只管‘怎么传’，不管‘能不能省’）")
try:
    chat_node({"q": 1})
except TypeError as e:
    print("   报错：", e)
print("   想让它可省略，得给默认值：def node(state, *, store=None)")


def chat_node2(state, *, store=None):
    return store


print("   给了默认值后不传：", chat_node2({"q": 1}))

print("\n④ 顺带把另外三行也过一遍：")
print('   prefs = store.get(("users", "inward"), "prefs")')
print("     → 去抽屉拿卡片（第 12 组讲过）：返回 Item 盒子，没存过是 None")
print('   theme = prefs.value["theme"] if prefs else "未知"')
print("     → 三元表达式（第 13 组 / 第 4 组）：prefs 有值就取 theme，是 None 就用默认值")
print('   return {"messages": [f"已按{theme}个性化"]}')
print("     → 返回【增量字典】：键是字段名 messages，值是【列表】（列表推导式不用，就一个元素）")
print("     → 外面那对 {} 是字典，里面那对 [] 是列表，f 开头的字符串里 {} 是变量占位符")

print("\n" + "=" * 62)
print("第 15 组：prefs.value[\"theme\"] —— 点号取属性 + 中括号取字典，两种取法串一起")
print("=" * 62)


# 用一个假 Item 模仿 store.get 的返回值，不装 langgraph 也能跑
class FakeItem:
    def __init__(self, value):
        self.value = value      # 盒子里装的东西
        self.key = "prefs"


prefs = FakeItem({"theme": "深色", "city": "广州"})

print("① 分两步看清楚（推荐新手这么读）：")
print("   prefs            =", type(prefs).__name__, "盒子对象")
print("   prefs.value      =", prefs.value, "  <- 点号：取对象的【属性】")
print('   prefs.value["theme"] =', prefs.value["theme"], "  <- 中括号：取字典的【键】")

print("\n② 拆成两行写，完全等价：")
card = prefs.value                    # 先把字典掏出来，起个名
theme = card["theme"]                 # 再取键
print("   card =", card)
print('   card["theme"] =', theme)
print("   和一行写完一样吗：", theme == prefs.value["theme"])

print("\n③ 两种取法的区别（记住这张表）：")
print("   对象.属性    → 点号 + 名字，不用引号，取的是【对象身上挂的东西】")
print("   字典[键]     → 中括号 + 字符串，必须引号，取的是【字典里某一格】")
print("   顺序固定：先拆盒子（.value）拿到字典，再取格子（[\"theme\"]）")

print("\n④ 取一个不存在的键会怎样：")
try:
    prefs.value["没有这个键"]
except KeyError as e:
    print("   KeyError:", e, "  <- 直接崩")

print('   安全写法：prefs.value.get("没有这个键", "默认值") =',
      prefs.value.get("没有这个键", "默认值"))
print("   ↑ .get(键, 兜底值)：取不到就返回兜底值，不崩（第 2 组讲过）")

print("\n⑤ 完整防御版（实战里就这么写）：")
theme_safe = prefs.value.get("theme", "默认主题") if prefs else "未知"
print("   theme =", theme_safe)
print("   ↑ 两层保险：prefs 可能是 None（没存过）→ 三元兜底；")
print("     value 里可能没这个键 → .get() 兜底")

print("\n" + "=" * 62)
print("第 16 组：app.invoke(Command(resume=\"yes\"), cfg) —— 第一个参数换了身份")
print("=" * 62)


class FakeCommand:
    def __init__(self, resume=None, goto=None):
        self.resume = resume      # 人类给的回复
        self.goto = goto          # （37 集用过）要跳去哪个节点

    def __repr__(self):
        return f"Command(resume={self.resume!r})"


class FakeApp2:
    def invoke(self, first, cfg):
        if isinstance(first, dict):
            return f"收到普通输入 {first}，从头跑一遍"
        if isinstance(first, FakeCommand):
            return f"收到指令，把 {first.resume!r} 交给正在等着的那个节点，继续跑"
        if first is None:
            return "啥也没给（None），从存档那一步接着跑"
        return "看不懂"


print("① 平时 invoke 第一个参数传的是【字典】（用户的输入）：")
print("   ", FakeApp2().invoke({"log": ["你好"]}, {"thread_id": "A"}))
print("\n② 这次传的是【Command 对象】（一条指令，不是数据）：")
print("   ", FakeApp2().invoke(FakeCommand(resume="yes"), {"thread_id": "A"}))
print("\n③ 还有第三种：传 None（74 集断点续跑）：")
print("   ", FakeApp2().invoke(None, {"thread_id": "A"}))

print("\n④ Command(resume=...) 里的 resume 必须写名字（关键字参数）：")
cmd = FakeCommand(resume="yes")
print("   造出来：", cmd, "  取出来：", cmd.resume)
print('   写成 Command("yes") 会报 TypeError —— 框架不知道这个 "yes" 是给谁的')

print("\n⑤ 所以那一整行的意思是：")
print('   out = app.invoke( Command(resume="yes"), cfg )')
print("        ↑ 把结果存起来   ↑ 递话：告诉他「人回复了 yes」  ↑ 哪个存档槽")

print("\n" + "=" * 62)
print("第 17 组：for p in pending: / if \"财务\" in p.value —— 遍历 + 判断两件套")
print("（87 集：两个人同时举手，回复要『对号入座』）")
print("=" * 62)


class Interrupt:                     # 极简替身；真的那个在 langgraph.types 里
    def __init__(self, id, value):
        self.id = id                 # 身份证号（字符串）
        self.value = value           # 手上举的那张纸条（字符串）
    def __repr__(self):
        return f"Interrupt(id={self.id[:8]}…, value={self.value!r})"


pending = [Interrupt("a3f9c2e1", "财务：这笔钱能批吗？"),
           Interrupt("b7c104d9", "法务：这份合同有问题吗？")]

print("\n① pending 是什么：一个【列表】，里面躺着 2 个 Interrupt 对象")
print("   len(pending) =", len(pending), "  类型：", type(pending).__name__)
print("   它从哪来：out = app.invoke(...);  pending = out['__interrupt__']")

print("\n② for p in pending:  = 『挨个拿出来，这一轮叫它 p』")
print("   语法：for 变量名 in 列表:   然后【缩进】写『对每个要做什么』")
print("   手工数下标版（完全等价，但啰嗦）：")
for i in range(len(pending)):
    print("     第", i, "个：", pending[i])
print("   for 循环版（推荐，不用数下标）：")
for p in pending:
    print("     ", p)

print("\n③ p.value = 点号取【属性】：这个中断举的那张纸条（是个字符串）")
for p in pending:
    print("     id =", p.id[:8], "…  value =", repr(p.value),
          " 类型：", type(p.value).__name__)
print("   → 对应真代码：interrupt('财务：这笔钱能批吗？') 里那句话，就是 value")

print("\n④ if \"财务\" in p.value:  = 【包含】判断（不是遍历！）")
print("   in 有两种意思，看它出现在哪：")
print("     for x in 列表      → 遍历：一个个拿出来    （配 for 用）")
print("     '财务' in 字符串   → 判断：里头有没有这两个字（结果是 True / False）")
print("   实测：")
for p in pending:
    hit = "财务" in p.value
    print("     '财务' 在", repr(p.value), "里吗 →", hit)

print("\n⑤ 两行合起来做的事：给每个中断配一句回复，键用它的身份证号")
answers = {}
for p in pending:
    if "财务" in p.value:
        answers[p.id] = "批"
    else:
        answers[p.id] = "没问题"
print("   answers =", answers)
print("   → 这一坨字典最后交给框架：Command(resume=answers)")

print("\n⑥ 为什么要『看内容认人』，不能写 answers[pending[0].id] = '批'？")
print("   因为并行跑的时候，谁先举手【不保证顺序】。把列表倒过来试试：")
pending2 = list(reversed(pending))
answers2 = {}
for p in pending2:
    answers2[p.id] = "批" if "财务" in p.value else "没问题"   # 三元表达式（第 4 组）
print("   倒序后的 answers =", answers2)
print("   跟正序结果一样吗：", answers2 == answers, " ← 看内容认人，顺序怎么变都不会错")
print("   如果按下标猜：pending[0] 一会是财务一会是法务 → 回复就发错人了")

print("\n⑦ 缩进坑（你贴给我的那段 if 没有缩进，Python 会直接报错）：")
print("""   错误：
   for p in pending:
   if "财务" in p.value:        ← 没往右挪 → IndentationError

   正确：
   for p in pending:
       if "财务" in p.value:    ← for 冒号的下一行，往右缩进 4 个空格
           answers[p.id] = "批"
   """)
print("   → 冒号 : 的意思是『下面缩进的内容归我管』；缩进退回去了 = 这个块结束")
print("   → 缩进用 4 个空格，别混用 Tab（混用会报 TabError）")

print("\n" + "=" * 62)
print("第 18 组：读检查点快照时冒出来的几个小语法（切片 / or 兜底 / 元组 / while）")
print("（91 集 get_state 那段）")
print("=" * 62)

cid = "1f1b4b00-218b-6c41-8001-818729753751"

print("\n① cid[:8] —— 【切片】：字符串跟列表一样能切")
print("   完整：", cid)
print("   cid[:8]  =", cid[:8], " ← 从头取 8 个")
print("   cid[-8:] =", cid[-8:], " ← 取尾巴 8 个（UUID 前 8 位容易相同，看尾巴更保险）")
print("   cid[9:13]=", cid[9:13], " ← [起点:终点]，终点不算")

print("\n② x or {} —— or 当【兜底】用（不是'或者'）")
print("   snap.parent_config 在链条头上是 None，直接 ['configurable'] 会崩")
print("   None or {}  →", None or {}, " ← 左边是空/None，就取右边")
print("   'abc' or {} →", "abc" or {}, " ← 左边有值，就用左边")
print("   → 速记：a or b = 『a 能用就用 a，不然用 b』")

print("\n③ snap.next 打印出来是 ('check',) —— 带逗号的是【元组】")
nxt = ("check",)
print("   type =", type(nxt).__name__, " 取值 nxt[0] =", nxt[0])
print("   一个元素的元组必须写那个逗号：('check') 是字符串，('check',) 才是元组")
print("   判空：if not snap.next:   （空元组 () 就是假）")

print("\n④ while True: + break —— 『一直干，直到喊停』")
print("   用在 97 集的单步调试：一直推门，直到 next 为空")
step = 0
while True:                    # 条件永远为真 = 死循环，所以里面必须有 break
    step += 1
    if step >= 3:              # 满足这个条件就跳出
        break                  # 立刻结束整个循环
print("   循环跑了", step, "次后 break 出来")
print("   → while 后面跟的是『继续的条件』；True 就是一直继续，靠 break 收尾")

print("\n⑤ 字典推导式带 if —— 挑着抄几个键（第 3 组讲过，这里复习）")
metadata = {"step": 1, "writes": None, "source": "loop", "parents": {}}
picked = {k: v for k, v in metadata.items() if k in ("step", "writes")}
print("   原来：", metadata)
print("   挑完：", picked)
print("   → 读法：{键:值 for 键,值 in 字典.items() if 条件}")

print("\n" + "=" * 62)
print("第 19 组：any(t.interrupts for t in snap.tasks) —— 生成器 + any()")
print("（判断『有没有人举手』的那一行）")
print("=" * 62)


class T:                       # 假的 task，只有两个属性
    def __init__(self, name, interrupts):
        self.name = name
        self.interrupts = interrupts


tasks = [T("finance", ()),                                  # 没举手：空元组
         T("legal", (("法务：合同有问题吗？", "d5ff4969…"),))]  # 举手了：非空

print("\n① any(...) = 『这一群里，有没有【至少一个】是真的？』")
print("   any([False, False]) →", any([False, False]))
print("   any([False, True])  →", any([False, True]))
print("   any([True,  True])  →", any([True, True]))
print("   any([])             →", any([]), " ← 一个都没有，就是 False")
print("   人话：一群人里有没有人举手？有一个就算有")

print("\n② 括号里 t.interrupts for t in tasks = 【生成器表达式】")
print("   它跟列表推导式（第 3 组）长得几乎一样，只是 [] 换成 ()：")
print("   [t.interrupts for t in tasks] →", [t.interrupts for t in tasks])
print("   区别：[] 当场造出整个列表；() 是『边用边给』，更省内存")
print("   写在 any(...) 里面时，圆括号就是 any 自己的那对括号，所以只写一次")

print("\n③ 为什么 t.interrupts 能直接当真假用？——【空的就是假的】")
print("   空元组 ()     → bool() =", bool(()))
print("   空列表 []     → bool() =", bool([]))
print("   None          → bool() =", bool(None))
print("   0 和 ''       → bool() =", bool(0), "/", bool(""))
print("   非空元组 (1,) → bool() =", bool((1,)))
print("   → 所以 interrupts = () 就是 False（没人举手），有内容就是 True")

print("\n④ 整行拆成零简写版（完全等价，新手建议这么读）")
found = False
for t in tasks:                # 挨个看
    if t.interrupts:           # 有内容 = 真
        found = True
        break                  # 找到一个就够了，后面的不用看
print("   手写版 found =", found)
print("   any 一行版   =", any(t.interrupts for t in tasks))
print("   一样吗：", found == any(t.interrupts for t in tasks))

print("\n⑤ 回到那一行，完整读法：")
print("   any( t.interrupts  for  t  in  snap.tasks )")
print("        ① 取这个属性   ② 挨个  ③ 从这个列表里")
print("   → 把每个 task 的 interrupts 拿出来瞄一眼，")
print("     只要有一个不是空的 → True（有人举手）")
print("     全是空的           → False（只是门口的锁）")
print("   实测：静态断点 =", any(t.interrupts for t in [T("check", ())]),
      "；动态中断 =", any(t.interrupts for t in tasks))

print("\n" + "=" * 62)
print("20、Command(resume=...) 和 invoke(None, cfg)")
print("=" * 62)

# ① 那个省略号 ... 到底是什么
print("① 先说最重要的一点：教程里写的 ... 不是让你照抄的代码！")
print("   type(...) =", type(...).__name__, " ← 它是 Python 内置的一个对象，叫 Ellipsis（省略号）")
print("   教程里写 Command(resume=...) 意思是『这里填你自己的值』")
print("   真要跑，必须换成具体的值：Command(resume='yes')")
print("   它只是文档里的占位符，等同于作文里的『××』。")

# ② Command(resume=...) 拆开看
class Command:                       # 手写一个假的，专门用来看参数怎么传
    def __init__(self, resume=None, goto=None):
        self.resume = resume         # resume 和 goto 都是【关键字参数】
        self.goto = goto

c = Command(resume="yes")
print("\n② Command(resume='yes') = 造一个小信封，信封上写着 resume='yes'")
print("   Command       → 一个【类】（做信封的模具），首字母大写 + 括号 = 造一个出来")
print("   resume='yes'  → 【关键字参数】：名字=值 的写法，不靠位置靠名字")
print("   造出来：resume =", c.resume, "，goto =", c.goto, "← 没传的用默认值 None")
print("   好处：Command(resume='yes') 和 Command('yes') 意思一样，但前者一眼看出'yes'是给 resume 的")

# ③ resume 递进去的东西，会原样从 interrupt() 出来
print("\n③ resume 递什么，节点里的 interrupt() 就返回什么（实测四种类型）：")
for v in ["yes", 42, {"by": "张三"}, ["a", "b"]]:
    print(f"   resume={v!r:<20} → ans = {v!r}   (type={type(v).__name__})")

# ④ invoke 的两个参数
def invoke(inp, cfg):                # 假 invoke，只为看两个参数各自是什么
    return f"输入={inp!r}，配置={cfg!r}"

print("\n④ invoke(None, cfg) 的两个参数：")
print("   第 1 个 None → ", invoke(None, {"configurable": {"thread_id": "t1"}}))
print("       None = 空、什么都没有。意思是『我不给新输入，你接着上次的存档往下跑』")
print("   第 2 个 cfg →  装着 thread_id 的字典，告诉它去哪个存档槽里翻")
print("   这两个是【位置参数】：谁在前谁在后是固定的，不能写成 invoke(cfg, None)")

# ⑤ 对比 invoke 的正常用法
print("\n⑤ 对照：平时怎么调用，解冻时怎么调用")
print("   第一次启动：app.invoke({'log': []}, cfg)   ← 第 1 个参数是【新输入】")
print("   门口上锁：app.invoke(None, cfg)            ← 第 1 个参数是 None，不给新东西")
print("   有人举手：app.invoke(Command(resume='yes'), cfg)  ← 第 1 个参数是一张纸条")
print("   共同点：不管第 1 个参数塞什么，它都站在 invoke 的第一个位置上。")

# ⑥ 用错会怎样（实测结论）
print("\n⑥ 用错了会怎样（在 langgraph_resume_two_ways.py 里实测）：")
print("   举手时误用 invoke(None, cfg)      → 不报错，但【又冻在原地】，白跑一趟")
print("   门锁时误用 Command(resume='yes')  → 不报错，纸条没人接，效果等同推门")
print("   ⚠ 所以：用反了不崩，但第一种会死循环卡住 —— 得靠 any(...) 判断清楚")

print("\n⑦ 一句话记法：")
print("   卡住的地方【有没有人在等一个答案】？")
print("     有人等 → Command(resume=...)   （递纸条）")
print("     没人等 → invoke(None, cfg)     （推门）")

# =========================================================
# 注意：变量名 s 在前面某组里被拿去当快照对象了，这里换用 sec
print("\n" + "=" * 62)
print("第 21 组：值班表 —— 把函数当值存进字典，再 fn.invoke(参数)")
print("=" * 62)
# =========================================================

# 出处（langgraph_toolnode_demo.py 101 集手写工具节点）：
#     for tc in last.tool_calls:
#         fn = {"query_leave_record": query_leave_record}[tc["name"]]
#         result = fn.invoke(tc["args"])

print("画面：车间门口有张【值班表】，左边写工人名字，右边站着工人本人。")
print("老板开的工单上只写了名字，你得先查表找到人，再把活儿派给他。")

# ① 两个普通函数 —— 就是"工人"
def query_leave_record(student_id):
    return f"学号 {student_id}：事假已批准"

def get_weather(city):
    return f"{city}：晴，28 度"

# ② 值班表：字典的【值】可以是函数
#    注意是 query_leave_record 不是 query_leave_record() —— 不加括号 = 把人请来站着，
#    加括号 = 当场让他干活（那存的就是干完的结果了）
roster = {"query_leave_record": query_leave_record, "get_weather": get_weather}
print("\n① 值班表里存的是什么：", roster)
print("   值那一列是 <function ...> —— 函数本身是可以被当成一个值传来传去的")

# ③ tc 是什么？一个字典（工单），里面有三样东西
tc = {"name": "query_leave_record", "args": {"student_id": "2024010101"}, "id": "call_001"}
print("\n② tc（工单）是个字典：", tc)
print("   tc['name'] =", tc["name"], " ← 要找哪个工人")
print("   tc['args'] =", tc["args"], " ← 交给他的一组参数，本身又是个字典")

# ④ 那行代码的真面目：造字典 和 取键 挤在同一行
fn = roster[tc["name"]]
print("\n③ 拆开写是这样：")
print("   roster            =", roster)
print("   tc['name']        =", tc["name"])
print("   roster[tc['name']]=", fn, " ← 查表，查出来的是个函数")
print("   原写法 {'query_leave_record': query_leave_record}[tc['name']] "
      "只是把上面两步并成一行：现造一张表，立刻查一次")

# ⑤ fn.invoke(tc['args']) —— invoke 是工具对象身上的方法，收一个字典
class FakeTool:                      # 模拟 @tool 造出来的东西（真工具也是这个用法）
    def __init__(self, func):
        self.func = func
    def invoke(self, args: dict):    # args 是一个字典
        return self.func(**args)     # ** 把字典摊开成关键字参数

tool_obj = FakeTool(query_leave_record)
print("\n④ fn.invoke(tc['args']) 在干什么：")
print("   tc['args']        =", tc["args"])
print("   fn.invoke(那个字典) =", tool_obj.invoke(tc["args"]))
print("   内部其实等价于：query_leave_record(student_id='2024010101')")
print("   —— **args 这个语法就是把字典 {'student_id': '2024010101'} 摊成 student_id='2024010101'")

print("\n⑤ 零简写版（把那两行还原成最啰嗦但最好懂的样子）：")
name = tc["name"]                    # 先看工单上写的名字
if name == "query_leave_record":
    worker = query_leave_record
elif name == "get_weather":
    worker = get_weather
arg_dict = tc["args"]                # 再看工单上写的参数
if name == "query_leave_record":
    result = worker(student_id=arg_dict["student_id"])
print("   结果：", result)
print("   对比原版两行：字典查表 替代了 if/elif，invoke(字典) 替代了手写参数")

print("\n⑥ 三个新手坑：")
print("   1) roster = {..., 'f': f()}   ← 多写一对括号，存进去的是【结果】不是【人】，"
      "后面再 fn.invoke 会报 'str' object has no attribute 'invoke'")
print("   2) tc['name'] 有引号，是字典取键；如果写成 tc.name 会 AttributeError —— "
      "工单是字典不是对象（前几轮讲过：点号取名字，中括号取内容）")
print("   3) fn.invoke 后面必须有括号，fn.invoke 本身只是'提到这个方法'，不会干活")

print("\n⑦ 一句话记法：")
print("   字典 = 值班表（名字 → 人）；tc['name'] = 工单上的名字；")
print("   fn.invoke(tc['args']) = 把工单上的参数交给这个人去执行")

# =========================================================
print("\n" + "=" * 62)
print("第 22 组：括号套括号怎么读 —— 从里往外剥")
print("=" * 62)
# =========================================================

# 出处：g.add_node("tools", ToolNode([query_leave_record]))

print("画面：套娃。每一层括号都是一次『动作』，读法永远是【从最里面往外】。")
print("    g.add_node(\"tools\", ToolNode([query_leave_record]))")
print("                                    └── ① 方括号 = 装筐")
print("                        └───────────── ② 圆括号 = 造节点")
print("     └──────────────────────────────── ③ 圆括号 = 挂到图上")

# 用三个普通函数模拟一遍，纯标准库就能跑
def tool_node(tools_list):
    return f"工具节点（管着 {len(tools_list)} 个工具：{tools_list}）"

def add_node(name, node):
    return f"图上多了一个节点：{name} = {node}"

def query_leave_record(student_id):
    return f"学号 {student_id} 事假已批准"

print("\n① 最里面：方括号 [ ] = 一个列表（装东西的筐）")
basket = [query_leave_record]
print("   [query_leave_record] =", basket)
print("   筐里只有 1 个，但【筐】这个形式不能省 —— 因为可能有多个：")
print("   [query_leave_record, get_weather, get_user]  ← 三个工具也是同一个写法")

print("\n② 往外一层：圆括号 ( ) = 调用/构造")
node = tool_node(basket)
print("   ToolNode(筐) =", node)

print("\n③ 最外面：圆括号 ( ) = 调用方法")
print("   add_node('tools', 那个节点) =", add_node("tools", node))

print("\n④ 原版一行 = 上面三步压缩；拆成三行是这样：")
basket = [query_leave_record]           # 装筐
node = tool_node(basket)              # 造节点
add_node("tools", node)               # 挂上去
print("   三行还是一行，效果完全一样 —— 一行只是懒得给中间产物起名字")

print("\n⑤ 为什么要列表？（实测，langgraph 1.2.11）")
print("   ToolNode([query_leave_record])  → 正常，内部 1 个工具")
print("   ToolNode(query_leave_record)    → ValueError: The first argument must be")
print("                                   a string or a callable with a __name__ ...")
print("   ⚠ 所以：哪怕只有一个工具，那对中括号也不能省 —— 换个角度想，")
print("     列表的意思是『这是一批工具』，函数本身的意思是『这是一个工具』，类型不一样")

print("\n⑥ 三种括号的分工（记住就不会再混）：")
print("   ( )  圆括号 = 【做事】调用函数 / 造对象")
print("   [ ]  方括号 = 【装筐】列表")
print("   { }  花括号 = 【装表】字典（第 21 组的值班表就是它）")

print("\n⑦ 读长代码的通用办法：")
print("   看到一堆括号糊在一起 → 先找最里面那一层，问自己『它先算出什么』，")
print("   拿到结果再往外走一层。像剥洋葱，一次只剥一层。")

print("=" * 62)
print("第 23 组：hasattr / isinstance / getattr / := （扒源码必撞的四个）")
print("=" * 62)


class Msg:
    def __init__(self, calls):
        self.tool_calls = calls


a = Msg([])                    # 没有工单的消息
b = Msg([{"name": "x"}])       # 有工单的消息

print("\n1) hasattr(对象, '格子名')：先问『你身上有这个格子吗』，再决定要不要取")
print("   hasattr(a, 'tool_calls') =", hasattr(a, "tool_calls"))
print("   hasattr(a, '不存在的')   =", hasattr(a, "不存在的"))
print("   直取 a.不存在的 会崩 AttributeError；先用 hasattr 挡一下就不会")

print("\n2) isinstance(对象, 类)：问『你是不是这个品种』")
print("   isinstance(a, Msg)   =", isinstance(a, Msg))
print("   isinstance([], list) =", isinstance([], list))
print("   isinstance('abc', list) =", isinstance("abc", list))

print("\n3) getattr(对象, '名字', 默认值)：点号的【字符串版】，取不到就给默认值")
print("   getattr(a, 'tool_calls', [])  =", getattr(a, "tool_calls", []))
print("   getattr(a, '没有的', '兜底值') =", getattr(a, "没有的", "兜底值"))
print("   （第 20 组的彩蛋就是它：名字是字符串变量时，点号用不了，只能 getattr）")

print("\n4) := 海象运算符：边赋值边用（tools_condition 源码里就有这个）")
data = {"messages": [1, 2, 3]}

msgs = data.get("messages")        # 两行版：先赋值
if msgs:                           # 再判断
    print("   两行版：", msgs, "最后一条 =", msgs[-1])

if (msgs := data.get("messages")):  # 一行版：赋值的结果直接当判断条件
    print("   一行版：", msgs, "最后一条 =", msgs[-1])

print("\n   括号能不能省（本机实测 compile 的结论）：")
print("     if (m := 1):            OK")
print("     if m := 1:              OK ← if/while 的顶层可以省")
print("     if True and (m := 1):   OK ← 夹在大表达式里【必须】括号")
print("     if True and m := 1:     SyntaxError: cannot use assignment")
print("                                          expressions with expression")
print("     x = (y := 1)            OK")
print("     x = y := 1              SyntaxError: invalid syntax")
print("   → 源码里 (messages := ...) 那两对括号是【被迫的】，不是装饰")

print("\n   短路陷阱（这才是它难读的真正原因）：")
print("     if (isinstance(s, dict) and (m := s.get(k, []))) or (m := getattr(s, k, [])):")
print("     or 左边成立时，右边【根本不执行】→ m 到底被赋成哪个，要看左边成不成立。")
print("   → 新手建议：拆成三行，看懂就行，别学源码这么写。")
print("\n   拆开后的三行版（完全等价，推荐）：")
state = {"messages": [1, 2, 3]}
messages = state.get("messages", [])      # ① 先取，取不到就用空列表兜底
if messages:                              # ② 判断
    last = messages[-1]                   # ③ 再用
    print("   三行版最后一条 =", last)

print("\n   三种『没有』要分清（实测，这是 := 最坑的地方）：")


def f():
    print("      （函数真的被调用了一次）")
    return []


if (m := f()):                            # 函数执行了，返回空列表
    print("      True 分支")
else:
    print(f"      False 分支 | m = {m} | 类型是 {type(m).__name__} "
          f"| 长度 {len(m)} | 是 None 吗：{m is None}")
    print("      → 赋值【发生了】，拿到的是【空列表】这个真实对象，不是『没有值』")

if False and (n := f()):                  # 短路：f() 压根没执行
    pass
try:
    print("      n =", n)
except NameError as e:
    print(f"      NameError: {e}")
    print("      → 这【才是】没赋值：连函数都没跑，变量根本不存在")

print("=" * 62)
print("第 24 组：pathlib —— 现代 Python 读写文件的方式（做 RAG 必用）")
print("=" * 62)

from pathlib import Path
import sys

base = Path(__file__).parent / "step3_docs"      # 用 / 拼路径
print("\n1) Path 对象用【斜杠 /】拼路径，不用字符串加法")
print("   base =", base)
print("   → 好处：Windows 的反斜杠不用转义，Mac/Linux 上也能跑")

p = base / "学生请销假制度.md"
print("\n2) 常用属性（都是属性，不是方法，【不加括号】）")
print("   p.name   =", p.name)          # 文件名
print("   p.parent =", p.parent)        # 所在目录
print("   p.exists() =", p.exists())    # ← 这个是方法，【要括号】

print("\n3) 遍历目录：glob('*.md') 按后缀找文件")
if base.exists():
    names = [f.name for f in sorted(base.glob("*.md"))]
    #                       └ sorted() 排序：不排序的话顺序是【随机的】
    print("   找到：", names)

print("\n4) 读写：read_text / write_text")
if p.exists():
    text = p.read_text(encoding="utf-8")
    print("   前 20 个字：", text[:20].replace("\n", " "))

print("\n   ★ Windows 大坑：必须写 encoding='utf-8'")
print("     不写的话 Python 会用系统默认编码（中文 Windows 是 GBK），")
print("     读 UTF-8 文件会报 UnicodeDecodeError，或者读出乱码。")
print("     以后凡是 open() / read_text() / write_text()，都把 encoding 写上。")

print("\n" + "=" * 62)
print("看不懂任何一行，回来按编号查上面对应的那一组就行。")
print("=" * 62)

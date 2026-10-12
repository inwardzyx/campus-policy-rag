# Token 计量：已完成的部分、未完成的部分、以及为什么现在不补

> 生成方式：`agent/token_cost.py` 实测 + `curl` 直查 `/chat` 响应结构。
> 数字来自 `agent/_token_cost.json`（中间产物，按 `.gitignore` 不进仓库，
> 重跑脚本可再生成）。本文件里的每个数字都能对着下面写的命令复现。

## 一、结论先说

| 路径 | token 计量状态 |
|---|---|
| **走 agent**（MCP + 工具 + 多轮循环） | ✅ **已完成**，10/10 题全部拿到 usage |
| **直打 `/chat`** | ❌ **未完成**，但根因已定位清楚，不是脚本 bug |

**不是"没做"，是"做了一半，且没做完的那一半根因明确"。**

## 二、agent 侧实测数据（10 道应答题）

| 题号 | input | output | total | 缓存命中 | 命中率 | LLM 调用 | 成本(元) |
|---|---|---|---|---|---|---|---|
| h1  | 1731 | 298 | 2029 | 1150 | 66.4% | 2 | 0.003776 |
| h2  | 1670 | 383 | 2053 | 1280 | 76.6% | 2 | 0.004100 |
| h3  | 1926 | 349 | 2275 | 1658 | 86.1% | 2 | 0.003660 |
| h4  | 1754 | 275 | 2029 | 1024 | 58.4% | 2 | 0.003865 |
| h5  | 1698 | 270 | 1968 | 1024 | 60.3% | 2 | 0.003713 |
| h6  | 1368 | 110 | 1478 |  896 | 65.5% | 2 | 0.002003 |
| h7  | 1631 | 307 | 1938 | 1152 | 70.6% | 2 | 0.003644 |
| h8  | 1651 | 190 | 1841 | 1152 | 69.8% | 2 | 0.002748 |
| h9  | 1767 | 299 | 2066 | 1024 | 58.0% | 2 | 0.004083 |
| h10 | 1777 | 261 | 2038 | 1280 | 72.0% | 2 | 0.003338 |
| **均值** | **1697** | **274** | **1972** | **1164** | **68.6%** | **2.00** | **0.003493** |

**数据质量自检**（这三条比均值本身更重要）：

- `missing_usage = 0` —— 20 次 LLM 调用（10 题 × 2 次）**全部**返回了 usage，
  没有一次靠估算或补 0。
- `truncated = 0` —— 10 题都没有触到 `MAX_ROUNDS`。
- 每题恰好 **2.00 次** LLM 调用，与 agent 循环的设计一致：
  第 1 次带工具清单决定调哪个工具，第 2 次基于工具结果出答案。

### 这组数字里最值得说的三点

**① 缓存命中率 68.6%，比预想低。**
system prompt + 工具 schema 那部分是稳定的，本该几乎全命中。
实测每题有 512~574 token 没走上缓存。合理解释：10 道题各问各的内容，
**变化的 user 消息打断了前缀**，缓存只能命中 system prompt + schema 那一段。

**② output 只占 13.9%（274/1972），input 占 86.1%。**
这个比例决定了优化方向：**省 token 的杠杆在 prompt 和检索结果压缩上，
不在"让模型少说话"上**。

**③ 成本 0.0035 元/题，10 题合计 0.0349 元。**
按 DeepSeek 官方价（2026-10：缓存命中 0.2 元/M、未命中 2 元/M、输出 8 元/M）。
⚠️ **价格会变，成本数字会过期，token 数字不会。**
所以 `token_cost.py` 把两者分开报——投递时只须换价格表即可重算，
token 部分永远是实测值。

## 三、直打侧为什么是空的：根因已定位

`curl` 直查 `/chat` 的真实响应：

```
顶层键: ['answer', 'sources', 'took_ms', 'rerank_used', 'knowledge_hit']
```

**五个键里没有任何一个跟 token 有关** —— 服务端就没返回 usage，
不是脚本读错了地方。

顺着代码查到丢失点，`service.py`：

```python
# service.py:225  _invoke_llm 内部返回的是【完整 AIMessage】，usage 就在上面
fut = _LLM_POOL.submit(llm.invoke, messages)
return fut.result(timeout=timeout)          # ← usage 在这

# service.py:574 / :643 / :676  三处调用点各自只取了 .content
return _invoke_llm(self.llm, [HumanMessage(content=prompt)]).content   # ← 丢在这
```

⇒ **技术上完全能补，改动很小**（在 `_invoke_llm` 里顺手把 usage 累加到模块级计数器，
`/chat` 返回时带上）。这是**没做**，不是**做不到**。

## 四、为什么现在不补（这是本节真正想留下的判断）

补计量要改 `service.py` 并重启服务。而此刻**61 题 × 3 遍的拒答回归正在跑**：

1. 中途重启 → 正在跑的请求会有一批 connection error，当遍会缺题；
2. 即使脚本能续跑，**当遍的口径就和已完成的遍不一致了**；
3. 最麻烦的是第 3 条：直打侧的 token 是**和拒答率跑批同一批请求**产出的。
   换服务版本会让两批数据失去可比性——而这次回归的全部价值就建立在
   "agent 侧 vs 直打侧同批对比"上。

⇒ **判据：一个改动是否会污染正在采集的数据。会，就排队。**

代价是直打侧的 token 数据晚几个小时才有。晚几个小时拿到一个**干净**的数字，
比现在拿到一个**混了重启**的数字划算。

## 五、待办（回归跑完之后做）

1. 改 `service.py` 的三处 `_invoke_llm(...).content`，
   把 `usage_metadata` 透传到 `/chat` 响应；
2. 重跑 `agent/token_cost.py --n 10`，补齐直打侧一栏；
3. ★ **重跑一遍拒答回归** —— 服务改了，直打侧基线口径就变了，
   拿旧数据对比是不成立的；
4. 把两条路径的 token/题 做比值，回答"接了 agent 之后每题贵了多少"。

## 六、复现方式

```bash
# 1. 先起 RAG 服务
D:/Python-project/.venv/Scripts/python.exe -m uvicorn service:app --port 8000

# 2. 量 token（注意 -u，否则重定向到管道时看不到逐题输出）
D:/Python-project/.venv/Scripts/python.exe -u agent/token_cost.py --n 10

# 3. 核对直打侧 usage 到底有没有（第二节根因）
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"宿舍可以带大功率电器吗","use_rerank":true}' \
  | D:/Python-project/.venv/Scripts/python.exe -c "import json,sys;print(list(json.load(sys.stdin).keys()))"
# 跑完会看到 ['answer','sources','took_ms','rerank_used','knowledge_hit'] —— 没有 usage
```

## 七、这套计量里守住的三条纪律

1. **取不到就返回 `None`，不填 0。** 填 0 会让"平均 token 数"变成假的，
   而**假的均值比没有均值更坏**（它看起来像答案）。
2. **token 与钱分开报。** token 永久有效，钱依赖价格表、必然过期。
3. **不改正在被 import 的模块。** `token_cost.py` 里刻意重写了 20 行循环
   而不是给 `run_one` 加参数——因为 `run_one` 正被跑批依赖着。
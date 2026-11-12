# agent 基线（105 题独立集，`/agent` 端到端）

> ★ 本文件由 `eval/agent_metrics.py` 生成，**不要手改**（下次生成会覆盖）。
> 跑法：从 `agent-baseline.json` 重渲染

- 运行指纹：`d5825827e95d`（`kb/runstate.py`；git `b1deac231fd6` dirty=`True`，python None）
- 依赖指纹：`requirements.txt`=e15469e2、`requirements-optional.txt`=9d4b995d（`requirements*.txt` 的 sha256 前 8 位）
- 版面后端：auto 会选 `docling`；可用 `docling、docling-local`；PDF_BACKEND=`auto`
- 语料哈希：`docs/kb`=；`docs/inbox`=

## 结论

| 指标 | 值 | 分母 | 说明 |
|---|---|---|---|
| `gold_in_sources` | 30/44 | 应答 44 | agent 返回的来源里有 gold 条款 |
| `no_miss` | 37/44 | 应答 44 | 没拒答且带来源（应答题不该被「查不到」打发） |
| `refuse_ok` | 26/61 | 拒答 61 | 拒答题真的拒了 |
| `done_rate` | 101/105 | 全部 105 | `stopped_reason == done` 的占比 |
| `avg_steps` | 1.95 | 全部 105 | 每题工具调用次数 |
| `avg_tokens` | 1914 | 全部 105 | 每题 token（含循环里的每一轮） |

**收尾原因分布**：`done` 101、`no_tool` 4

## 两条口径警告（别把这两个数相减）

1. **`gold_in_sources` ≠ `recall5`**：前者算的是 agent 返回的 `sources`（`/chat` 精排后最多 3 条），
   后者算的是粗捞 top-5。分子分母都不同，**相减没有意义**。
2. **agent 与 `/chat` 是两个被测对象**：`eval/gate.py` 的配对检验只允许同一层的同一条路径
   （否则「换了被测对象」会被当成「改动有效」）。要比较只能并列看，不能做 McNemar。


## 同一刻 `/chat` 对照（同一批题、同一进程、同一时刻）

> ★ 这张表的两个数来自**同一刻**的两次跑批（`agent-baseline.json` 与
> `chat-baseline-same-moment.json`），不是拿历史基线比 —— 拿历史基线比会把
> 「不同时间/不同代码」误当成「不同方法」。

| 指标 | `/agent` | `/chat`（同刻） | 差 |
|---|---|---|---|
| `gold_in_sources` | 30/44 | 41/44 | -11 |
| `no_miss` | 37/44 | 43/44 | -6 |
| `refuse_ok` | 26/61 | 41/61 | -15 |

**结论**：agent 在这三条上**全面更差**，拒答差距最大。

机制**已用单题复现钉住**（不是猜）：`s10`（问「退学后还能回来接着读吗」）
第一步 `ask_policy(原问题)` 返回 `no_hit`（走的正是 `/chat` 那道闸），
第二步模型改写问法 `search_policy("退学 复学 重新入学")` **命中 3 条**，
返回的来源是 `学生应征入伍管理办法` —— 与问题无关。
而 loop 的 `refused` 判定是「**任何一次**命中即不拒答」⇒ 这条路把拒答绕过去了。

⇒ 缺陷在**循环的拒答语义**，不在工具：应当以「原问题的证据」为准，
以及限制「换个问法重试直到命中」这种路径。

⇒ 产品结论：`/agent` **不能作为默认路径**（`AGENT_ENABLED` 默认 off 是对的）；
要上默认，先加一条「拒答口径以**原问题**为准」的守卫。


## 逐题

| 题号 | 类型 | 收尾 | gold | 有来源 | 步数 | token | 秒 |
|---|---|---|---|---|---|---|---|
| h1 | answer | done | ✓ | ✓ | 2 | 1923 | 11.3 |
| h2 | answer | done | ✓ | ✓ | 2 | 2015 | 11.0 |
| h3 | answer | done | ✓ | ✓ | 2 | 2256 | 12.8 |
| h4 | answer | done | ✗ | ✗ | 1 | 1592 | 5.5 |
| h5 | answer | done | ✗ | ✗ | 1 | 1554 | 6.8 |
| h6 | answer | done | ✗ | ✗ | 1 | 1485 | 5.8 |
| h7 | answer | done | ✓ | ✓ | 2 | 1871 | 9.9 |
| h8 | answer | done | ✗ | ✗ | 2 | 1675 | 8.6 |
| h9 | answer | done | ✗ | ✓ | 2 | 1911 | 9.7 |
| h10 | answer | done | ✓ | ✓ | 2 | 2055 | 9.1 |
| h11 | answer | done | ✓ | ✓ | 2 | 1648 | 8.6 |
| h12 | answer | done | ✓ | ✓ | 2 | 2215 | 12.2 |
| h13 | answer | done | ✗ | ✗ | 1 | 1528 | 5.8 |
| h14 | answer | done | ✓ | ✓ | 2 | 2132 | 11.2 |
| h15 | refuse | done | — | ✗ | 1 | 1473 | 4.4 |
| h16 | refuse | done | — | ✗ | 1 | 1503 | 5.5 |
| h17 | refuse | done | — | ✗ | 1 | 1519 | 5.3 |
| h18 | refuse | done | — | ✗ | 2 | 1615 | 7.9 |
| h19 | refuse | done | — | ✗ | 3 | 1811 | 8.9 |
| h20 | refuse | done | — | ✗ | 2 | 1671 | 7.6 |
| h21 | answer | done | ✓ | ✓ | 2 | 1726 | 10.1 |
| h22 | answer | done | ✓ | ✓ | 2 | 1888 | 10.0 |
| h23 | answer | done | ✗ | ✓ | 2 | 2471 | 11.6 |
| h24 | answer | done | ✗ | ✓ | 2 | 2169 | 11.7 |
| h25 | answer | done | ✓ | ✓ | 3 | 2670 | 16.7 |
| r1 | answer | done | ✓ | ✓ | 2 | 1714 | 9.3 |
| r2 | answer | done | ✓ | ✓ | 2 | 2159 | 12.8 |
| r3 | answer | done | ✓ | ✓ | 3 | 2246 | 13.2 |
| r4 | answer | done | ✓ | ✓ | 2 | 2429 | 14.3 |
| r5 | answer | done | ✓ | ✓ | 2 | 2051 | 11.9 |
| r6 | answer | done | ✓ | ✓ | 2 | 2039 | 11.9 |
| r7 | answer | done | ✓ | ✓ | 2 | 1797 | 12.1 |
| r8 | answer | done | ✗ | ✓ | 2 | 1936 | 15.1 |
| r9 | answer | done | ✗ | ✗ | 2 | 1860 | 17.5 |
| r10 | answer | done | ✗ | ✗ | 1 | 1482 | 5.2 |
| r11 | answer | done | ✗ | ✓ | 2 | 2000 | 8.8 |
| r12 | answer | done | ✓ | ✓ | 2 | 2001 | 9.9 |
| r13 | answer | done | ✗ | ✓ | 2 | 2098 | 12.8 |
| r14 | answer | done | ✗ | ✓ | 2 | 2045 | 11.6 |
| r15 | answer | done | ✓ | ✓ | 2 | 2151 | 12.7 |
| r16 | answer | done | ✓ | ✓ | 2 | 2173 | 11.2 |
| r17 | answer | done | ✓ | ✓ | 2 | 1802 | 10.9 |
| r18 | answer | done | ✓ | ✓ | 2 | 2084 | 14.3 |
| r19 | answer | done | ✓ | ✓ | 2 | 1698 | 8.5 |
| r20 | answer | done | ✓ | ✓ | 2 | 1980 | 10.3 |
| r21 | refuse | no_tool | — | ✗ | 0 | 696 | 1.3 |
| r22 | refuse | no_tool | — | ✗ | 0 | 720 | 1.4 |
| r23 | refuse | no_tool | — | ✗ | 0 | 696 | 1.4 |
| r24 | refuse | no_tool | — | ✗ | 0 | 739 | 1.6 |
| r25 | refuse | done | — | ✗ | 2 | 1671 | 6.2 |
| r26 | refuse | done | — | ✗ | 2 | 1609 | 6.8 |
| r27 | refuse | done | — | ✓ | 2 | 1929 | 9.5 |
| r28 | refuse | done | — | ✓ | 2 | 1791 | 8.8 |
| r29 | refuse | done | — | ✗ | 2 | 1646 | 8.1 |
| r30 | refuse | done | — | ✓ | 2 | 1774 | 11.4 |
| r31 | refuse | done | — | ✗ | 2 | 1734 | 7.9 |
| r32 | refuse | done | — | ✗ | 2 | 1686 | 8.4 |
| r33 | refuse | done | — | ✗ | 2 | 1639 | 7.9 |
| r34 | refuse | done | — | ✓ | 2 | 2396 | 11.9 |
| r35 | refuse | done | — | ✓ | 2 | 1895 | 10.8 |
| r36 | refuse | done | — | ✓ | 2 | 1879 | 8.6 |
| r37 | refuse | done | — | ✓ | 2 | 2658 | 14.8 |
| r38 | refuse | done | — | ✓ | 2 | 2036 | 10.8 |
| r39 | refuse | done | — | ✗ | 2 | 1772 | 8.2 |
| r40 | refuse | done | — | ✗ | 2 | 1803 | 8.1 |
| r41 | refuse | done | — | ✗ | 2 | 1733 | 8.4 |
| r42 | refuse | done | — | ✓ | 2 | 1875 | 10.1 |
| r43 | refuse | done | — | ✗ | 2 | 1764 | 9.5 |
| r44 | refuse | done | — | ✗ | 2 | 1653 | 6.6 |
| r45 | refuse | done | — | ✗ | 1 | 1513 | 5.2 |
| r46 | refuse | done | — | ✓ | 2 | 2474 | 12.1 |
| r47 | refuse | done | — | ✓ | 2 | 2023 | 9.1 |
| r48 | refuse | done | — | ✓ | 3 | 2304 | 13.0 |
| r49 | refuse | done | — | ✓ | 2 | 2068 | 10.8 |
| r50 | refuse | done | — | ✓ | 3 | 2155 | 12.4 |
| s26 | answer | done | ✓ | ✓ | 3 | 2404 | 15.3 |
| s27 | answer | done | ✓ | ✓ | 2 | 2486 | 10.9 |
| s28 | answer | done | ✓ | ✓ | 2 | 2269 | 12.2 |
| s29 | answer | done | ✓ | ✓ | 2 | 1907 | 9.1 |
| s30 | answer | done | ✓ | ✓ | 2 | 1954 | 10.2 |
| s1 | refuse | done | — | ✓ | 2 | 2122 | 10.4 |
| s2 | refuse | done | — | ✓ | 2 | 1795 | 8.6 |
| s3 | refuse | done | — | ✓ | 2 | 1905 | 9.8 |
| s4 | refuse | done | — | ✓ | 2 | 1934 | 8.9 |
| s5 | refuse | done | — | ✓ | 3 | 2218 | 13.0 |
| s6 | refuse | done | — | ✓ | 2 | 1952 | 8.9 |
| s7 | refuse | done | — | ✗ | 2 | 1729 | 7.1 |
| s8 | refuse | done | — | ✓ | 2 | 1909 | 8.2 |
| s9 | refuse | done | — | ✓ | 2 | 1806 | 8.6 |
| s10 | refuse | done | — | ✓ | 2 | 1895 | 8.0 |
| s11 | refuse | done | — | ✓ | 2 | 1874 | 8.7 |
| s12 | refuse | done | — | ✓ | 2 | 2254 | 12.0 |
| s13 | refuse | done | — | ✓ | 3 | 2814 | 14.4 |
| s14 | refuse | done | — | ✗ | 2 | 1700 | 6.9 |
| s15 | refuse | done | — | ✓ | 2 | 1973 | 9.2 |
| s16 | refuse | done | — | ✓ | 3 | 2331 | 12.6 |
| s17 | refuse | done | — | ✓ | 2 | 1787 | 9.2 |
| s18 | refuse | done | — | ✓ | 2 | 1689 | 8.6 |
| s19 | refuse | done | — | ✓ | 2 | 1885 | 10.8 |
| s20 | refuse | done | — | ✗ | 2 | 1699 | 6.6 |
| s21 | refuse | done | — | ✓ | 3 | 2321 | 12.7 |
| s22 | refuse | done | — | ✗ | 2 | 1633 | 6.8 |
| s23 | refuse | done | — | ✓ | 2 | 2247 | 12.3 |
| s24 | refuse | done | — | ✓ | 4 | 3955 | 17.9 |
| s25 | refuse | done | — | ✓ | 2 | 2089 | 10.1 |

## 备注

105 题全量；对照来自同一刻 /chat 跑批。

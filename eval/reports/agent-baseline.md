# agent 基线（105 题独立集，`/agent` 端到端）

> ★ 本文件由 `eval/agent_metrics.py` 生成，**不要手改**（下次生成会覆盖）。
> 跑法：`http://127.0.0.1:8031`（现跑）

- 运行指纹：`c32a2603f8a5`（`kb/runstate.py`；git `da68be58bc65` dirty=`True`，python 3.14.6）
- 依赖指纹：`requirements.txt`=e15469e2、`requirements-optional.txt`=9d4b995d（`requirements*.txt` 的 sha256 前 8 位）
- 版面后端：auto 会选 `docling`；可用 `docling、docling-local`；PDF_BACKEND=`auto`
- 语料哈希：`docs/kb`=d39a8542；`docs/inbox`=cb62f2bf

## 结论

| 指标 | 值 | 分母 | 说明 |
|---|---|---|---|
| `gold_in_sources` | 42/44 | 应答 44 | agent 返回的来源里有 gold 条款 |
| `no_miss` | 42/44 | 应答 44 | 没拒答且带来源（应答题不该被「查不到」打发） |
| `refuse_ok` | 40/61 | 拒答 61 | 拒答题真的拒了 |
| `done_rate` | 63/105 | 全部 105 | `stopped_reason == done` 的占比 |
| `avg_steps` | 1.95 | 全部 105 | 每题工具调用次数 |
| `avg_tokens` | 1946 | 全部 105 | 每题 token（含循环里的每一轮） |

**收尾原因分布**：`done` 63、`gate_refused` 35、`rewrite_limit` 4、`no_tool` 3

## 两条口径警告（别把这两个数相减）

1. **`gold_in_sources` ≠ `recall5`**：前者算的是 agent 返回的 `sources`（`/chat` 精排后最多 3 条），
   后者算的是粗捞 top-5。分子分母都不同，**相减没有意义**。
2. **agent 与 `/chat` 是两个被测对象**：`eval/gate.py` 的配对检验只允许同一层的同一条路径
   （否则「换了被测对象」会被当成「改动有效」）。要比较只能并列看，不能做 McNemar。


## 逐题

| 题号 | 类型 | 收尾 | gold | 有来源 | 步数 | token | 秒 |
|---|---|---|---|---|---|---|---|
| h1 | answer | done | ✓ | ✓ | 2 | 2082 | 6.4 |
| h2 | answer | done | ✓ | ✓ | 2 | 2098 | 14.3 |
| h3 | answer | done | ✓ | ✓ | 2 | 2341 | 19.3 |
| h4 | answer | done | ✓ | ✓ | 1 | 1710 | 12.2 |
| h5 | answer | done | ✗ | ✓ | 2 | 2252 | 13.9 |
| h6 | answer | done | ✓ | ✓ | 1 | 1678 | 7.4 |
| h7 | answer | done | ✓ | ✓ | 2 | 1886 | 12.4 |
| h8 | answer | done | ✓ | ✓ | 2 | 1751 | 9.8 |
| h9 | answer | gate_refused | ✗ | ✗ | 2 | 1599 | 6.9 |
| h10 | answer | done | ✓ | ✓ | 2 | 2023 | 11.2 |
| h11 | answer | done | ✓ | ✓ | 2 | 1690 | 12.8 |
| h12 | answer | done | ✓ | ✓ | 2 | 2268 | 10.7 |
| h13 | answer | done | ✓ | ✓ | 1 | 1536 | 6.1 |
| h14 | answer | done | ✓ | ✓ | 2 | 2102 | 13.1 |
| h15 | refuse | gate_refused | — | ✗ | 1 | 1504 | 8.9 |
| h16 | refuse | gate_refused | — | ✗ | 1 | 1504 | 4.8 |
| h17 | refuse | gate_refused | — | ✗ | 1 | 1493 | 4.5 |
| h18 | refuse | gate_refused | — | ✗ | 1 | 1516 | 2.4 |
| h19 | refuse | gate_refused | — | ✗ | 3 | 1807 | 10.2 |
| h20 | refuse | gate_refused | — | ✗ | 2 | 1697 | 8.7 |
| h21 | answer | done | ✓ | ✓ | 2 | 1768 | 10.2 |
| h22 | answer | done | ✓ | ✓ | 2 | 1936 | 15.3 |
| h23 | answer | done | ✓ | ✓ | 2 | 2473 | 14.6 |
| h24 | answer | done | ✓ | ✓ | 2 | 2288 | 12.3 |
| h25 | answer | done | ✓ | ✓ | 3 | 2801 | 17.1 |
| r1 | answer | done | ✓ | ✓ | 2 | 1966 | 10.6 |
| r2 | answer | done | ✓ | ✓ | 2 | 2219 | 13.9 |
| r3 | answer | rewrite_limit | ✓ | ✗ | 2 | 777 | 12.1 |
| r4 | answer | done | ✓ | ✓ | 2 | 2299 | 20.2 |
| r5 | answer | done | ✓ | ✓ | 2 | 2157 | 10.9 |
| r6 | answer | done | ✓ | ✓ | 2 | 2295 | 13.3 |
| r7 | answer | done | ✓ | ✓ | 2 | 1986 | 15.3 |
| r8 | answer | done | ✓ | ✓ | 2 | 2346 | 11.4 |
| r9 | answer | done | ✓ | ✓ | 2 | 1957 | 15.2 |
| r10 | answer | done | ✓ | ✓ | 1 | 1495 | 5.4 |
| r11 | answer | done | ✓ | ✓ | 2 | 2103 | 11.3 |
| r12 | answer | done | ✓ | ✓ | 2 | 1995 | 10.1 |
| r13 | answer | done | ✓ | ✓ | 2 | 2192 | 11.1 |
| r14 | answer | done | ✓ | ✓ | 2 | 2257 | 11.6 |
| r15 | answer | done | ✓ | ✓ | 2 | 2443 | 11.0 |
| r16 | answer | done | ✓ | ✓ | 2 | 2197 | 10.8 |
| r17 | answer | done | ✓ | ✓ | 2 | 1871 | 10.1 |
| r18 | answer | done | ✓ | ✓ | 2 | 2283 | 11.5 |
| r19 | answer | done | ✓ | ✓ | 2 | 1710 | 9.2 |
| r20 | answer | done | ✓ | ✓ | 2 | 2175 | 14.1 |
| r21 | refuse | no_tool | — | ✗ | 0 | 692 | 14.8 |
| r22 | refuse | gate_refused | — | ✗ | 2 | 1734 | 11.9 |
| r23 | refuse | no_tool | — | ✗ | 0 | 696 | 5.1 |
| r24 | refuse | no_tool | — | ✗ | 0 | 694 | 4.5 |
| r25 | refuse | gate_refused | — | ✗ | 2 | 1740 | 12.1 |
| r26 | refuse | gate_refused | — | ✗ | 2 | 1613 | 8.2 |
| r27 | refuse | done | — | ✓ | 2 | 2012 | 12.1 |
| r28 | refuse | done | — | ✓ | 2 | 1800 | 11.7 |
| r29 | refuse | gate_refused | — | ✗ | 2 | 1656 | 9.8 |
| r30 | refuse | done | — | ✓ | 2 | 1842 | 11.3 |
| r31 | refuse | gate_refused | — | ✗ | 2 | 1700 | 8.7 |
| r32 | refuse | gate_refused | — | ✗ | 2 | 1727 | 8.6 |
| r33 | refuse | gate_refused | — | ✗ | 2 | 1641 | 8.0 |
| r34 | refuse | rewrite_limit | — | ✗ | 3 | 2193 | 15.9 |
| r35 | refuse | done | — | ✓ | 2 | 1881 | 15.7 |
| r36 | refuse | done | — | ✓ | 2 | 1983 | 14.4 |
| r37 | refuse | done | — | ✓ | 2 | 2474 | 21.1 |
| r38 | refuse | done | — | ✓ | 2 | 1995 | 10.9 |
| r39 | refuse | gate_refused | — | ✗ | 2 | 1991 | 13.1 |
| r40 | refuse | gate_refused | — | ✗ | 2 | 1728 | 12.1 |
| r41 | refuse | gate_refused | — | ✗ | 2 | 1757 | 12.2 |
| r42 | refuse | gate_refused | — | ✗ | 2 | 1823 | 13.7 |
| r43 | refuse | gate_refused | — | ✗ | 2 | 1764 | 13.5 |
| r44 | refuse | gate_refused | — | ✗ | 2 | 1647 | 9.0 |
| r45 | refuse | gate_refused | — | ✗ | 1 | 1505 | 5.1 |
| r46 | refuse | done | — | ✓ | 2 | 2699 | 15.7 |
| r47 | refuse | gate_refused | — | ✗ | 2 | 2021 | 9.2 |
| r48 | refuse | done | — | ✓ | 3 | 2301 | 14.0 |
| r49 | refuse | done | — | ✓ | 2 | 2037 | 21.6 |
| r50 | refuse | done | — | ✓ | 2 | 1846 | 10.3 |
| s26 | answer | done | ✓ | ✓ | 3 | 2368 | 25.4 |
| s27 | answer | done | ✓ | ✓ | 2 | 2614 | 29.0 |
| s28 | answer | done | ✓ | ✓ | 2 | 2293 | 15.3 |
| s29 | answer | done | ✓ | ✓ | 2 | 1984 | 11.7 |
| s30 | answer | done | ✓ | ✓ | 2 | 2066 | 16.5 |
| s1 | refuse | done | — | ✓ | 2 | 2179 | 14.6 |
| s2 | refuse | gate_refused | — | ✗ | 2 | 1715 | 27.0 |
| s3 | refuse | done | — | ✓ | 2 | 1975 | 23.1 |
| s4 | refuse | rewrite_limit | — | ✗ | 3 | 1819 | 17.2 |
| s5 | refuse | gate_refused | — | ✗ | 3 | 2182 | 13.1 |
| s6 | refuse | gate_refused | — | ✗ | 2 | 1813 | 12.9 |
| s7 | refuse | gate_refused | — | ✗ | 1 | 1492 | 5.5 |
| s8 | refuse | gate_refused | — | ✗ | 2 | 1875 | 12.9 |
| s9 | refuse | gate_refused | — | ✗ | 2 | 1792 | 8.7 |
| s10 | refuse | gate_refused | — | ✗ | 2 | 1895 | 4.2 |
| s11 | refuse | done | — | ✓ | 2 | 1917 | 14.6 |
| s12 | refuse | done | — | ✓ | 2 | 2549 | 14.9 |
| s13 | refuse | done | — | ✓ | 3 | 3003 | 10.1 |
| s14 | refuse | gate_refused | — | ✗ | 2 | 1764 | 8.9 |
| s15 | refuse | done | — | ✓ | 2 | 2403 | 18.2 |
| s16 | refuse | gate_refused | — | ✗ | 3 | 2407 | 13.0 |
| s17 | refuse | rewrite_limit | — | ✗ | 3 | 1696 | 13.8 |
| s18 | refuse | gate_refused | — | ✗ | 2 | 1679 | 9.0 |
| s19 | refuse | done | — | ✓ | 2 | 2299 | 14.9 |
| s20 | refuse | gate_refused | — | ✗ | 2 | 1774 | 7.9 |
| s21 | refuse | gate_refused | — | ✗ | 3 | 2377 | 12.6 |
| s22 | refuse | gate_refused | — | ✗ | 2 | 1695 | 9.9 |
| s23 | refuse | done | — | ✓ | 2 | 2602 | 18.5 |
| s24 | refuse | done | — | ✓ | 2 | 2214 | 14.2 |
| s25 | refuse | done | — | ✓ | 2 | 2144 | 15.1 |

## 备注

105 题全量（2026-11-13 01:2x 跑批，拒答语义已修：gate 以原问题为准 + 改写去重上限 2）。

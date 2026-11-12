# `/chat` 同刻基线（105 题，与 agent 同一批）

> ★ 本文件由 `eval/agent_metrics.py` 生成，**不要手改**（下次生成会覆盖）。
> 跑法：从 `chat-baseline-same-moment.json` 重渲染

- 运行指纹：`91d4887ddb56`（`kb/runstate.py`；git `d392550ef350` dirty=`False`，python 3.14.6）
- 依赖指纹：`requirements.txt`=e15469e2、`requirements-optional.txt`=9d4b995d（`requirements*.txt` 的 sha256 前 8 位）
- 版面后端：auto 会选 `docling`；可用 `docling、docling-local`；PDF_BACKEND=`auto`
- 语料哈希：`docs/kb`=d39a8542；`docs/inbox`=cb62f2bf

## 结论

| 指标 | 值 | 分母 | 说明 |
|---|---|---|---|
| `gold_in_sources` | 41/44 | 应答 44 | agent 返回的来源里有 gold 条款 |
| `no_miss` | 43/44 | 应答 44 | 没拒答且带来源（应答题不该被「查不到」打发） |
| `refuse_ok` | 41/61 | 拒答 61 | 拒答题真的拒了 |
| `done_rate` | 105/105 | 全部 105 | `stopped_reason == done` 的占比 |
| `avg_steps` | 0.6 | 全部 105 | 每题工具调用次数 |
| `avg_tokens` | 1820 | 全部 105 | 每题 token（含循环里的每一轮） |

**收尾原因分布**：`done` 105

## 两条口径警告（别把这两个数相减）

1. **`gold_in_sources` ≠ `recall5`**：前者算的是 agent 返回的 `sources`（`/chat` 精排后最多 3 条），
   后者算的是粗捞 top-5。分子分母都不同，**相减没有意义**。
2. **agent 与 `/chat` 是两个被测对象**：`eval/gate.py` 的配对检验只允许同一层的同一条路径
   （否则「换了被测对象」会被当成「改动有效」）。要比较只能并列看，不能做 McNemar。


## 逐题

| 题号 | 类型 | 收尾 | gold | 有来源 | 步数 | token | 秒 |
|---|---|---|---|---|---|---|---|
| h1 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| h2 | answer | done | ✓ | ✓ | 1 | 2100 | 0.9 |
| h3 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| h4 | answer | done | ✓ | ✓ | 1 | 2100 | 1.3 |
| h5 | answer | done | ✗ | ✓ | 1 | 2100 | 1.2 |
| h6 | answer | done | ✓ | ✓ | 1 | 2100 | 0.8 |
| h7 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| h8 | answer | done | ✓ | ✓ | 1 | 2100 | 0.7 |
| h9 | answer | done | ✗ | ✗ | 0 | 1400 | 0.0 |
| h10 | answer | done | ✓ | ✓ | 1 | 2100 | 0.9 |
| h11 | answer | done | ✓ | ✓ | 1 | 2100 | 0.8 |
| h12 | answer | done | ✓ | ✓ | 1 | 2100 | 1.7 |
| h13 | answer | done | ✓ | ✓ | 1 | 2100 | 1.2 |
| h14 | answer | done | ✓ | ✓ | 1 | 2100 | 2.4 |
| h15 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| h16 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| h17 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| h18 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| h19 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| h20 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| h21 | answer | done | ✓ | ✓ | 1 | 2100 | 0.8 |
| h22 | answer | done | ✓ | ✓ | 1 | 2100 | 1.3 |
| h23 | answer | done | ✓ | ✓ | 1 | 2100 | 2.6 |
| h24 | answer | done | ✓ | ✓ | 1 | 2100 | 0.8 |
| h25 | answer | done | ✓ | ✓ | 1 | 2100 | 0.9 |
| r1 | answer | done | ✓ | ✓ | 1 | 2100 | 1.4 |
| r2 | answer | done | ✓ | ✓ | 1 | 2100 | 1.6 |
| r3 | answer | done | ✓ | ✓ | 1 | 2100 | 0.6 |
| r4 | answer | done | ✓ | ✓ | 1 | 2100 | 1.9 |
| r5 | answer | done | ✓ | ✓ | 1 | 2100 | 1.1 |
| r6 | answer | done | ✓ | ✓ | 1 | 2100 | 1.4 |
| r7 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| r8 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| r9 | answer | done | ✓ | ✓ | 1 | 2100 | 1.1 |
| r10 | answer | done | ✓ | ✓ | 1 | 2100 | 1.2 |
| r11 | answer | done | ✓ | ✓ | 1 | 2100 | 2.2 |
| r12 | answer | done | ✓ | ✓ | 1 | 2100 | 1.5 |
| r13 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| r14 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| r15 | answer | done | ✓ | ✓ | 1 | 2100 | 1.3 |
| r16 | answer | done | ✓ | ✓ | 1 | 2100 | 1.5 |
| r17 | answer | done | ✓ | ✓ | 1 | 2100 | 1.3 |
| r18 | answer | done | ✓ | ✓ | 1 | 2100 | 1.0 |
| r19 | answer | done | ✓ | ✓ | 1 | 2100 | 1.3 |
| r20 | answer | done | ✗ | ✓ | 1 | 2100 | 1.1 |
| r21 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r22 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r23 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r24 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r25 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r26 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r27 | refuse | done | — | ✓ | 1 | 2100 | 0.8 |
| r28 | refuse | done | — | ✓ | 1 | 2100 | 1.0 |
| r29 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r30 | refuse | done | — | ✓ | 1 | 2100 | 1.0 |
| r31 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r32 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r33 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r34 | refuse | done | — | ✓ | 1 | 2100 | 1.5 |
| r35 | refuse | done | — | ✓ | 1 | 2100 | 0.7 |
| r36 | refuse | done | — | ✓ | 1 | 2100 | 1.3 |
| r37 | refuse | done | — | ✓ | 1 | 2100 | 1.4 |
| r38 | refuse | done | — | ✓ | 1 | 2100 | 1.1 |
| r39 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r40 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r41 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r42 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r43 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r44 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r45 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r46 | refuse | done | — | ✓ | 1 | 2100 | 1.7 |
| r47 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| r48 | refuse | done | — | ✓ | 1 | 2100 | 1.9 |
| r49 | refuse | done | — | ✓ | 1 | 2100 | 0.9 |
| r50 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s26 | answer | done | ✓ | ✓ | 1 | 2100 | 1.2 |
| s27 | answer | done | ✓ | ✓ | 1 | 2100 | 1.7 |
| s28 | answer | done | ✓ | ✓ | 1 | 2100 | 1.7 |
| s29 | answer | done | ✓ | ✓ | 1 | 2100 | 0.8 |
| s30 | answer | done | ✓ | ✓ | 1 | 2100 | 1.2 |
| s1 | refuse | done | — | ✓ | 1 | 2100 | 1.2 |
| s2 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s3 | refuse | done | — | ✓ | 1 | 2100 | 1.0 |
| s4 | refuse | done | — | ✓ | 1 | 2100 | 0.6 |
| s5 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s6 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s7 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s8 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s9 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s10 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s11 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s12 | refuse | done | — | ✓ | 1 | 2100 | 1.5 |
| s13 | refuse | done | — | ✓ | 1 | 2100 | 1.5 |
| s14 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s15 | refuse | done | — | ✓ | 1 | 2100 | 1.6 |
| s16 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s17 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s18 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s19 | refuse | done | — | ✓ | 1 | 2100 | 0.8 |
| s20 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s21 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s22 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |
| s23 | refuse | done | — | ✓ | 1 | 2100 | 1.2 |
| s24 | refuse | done | — | ✓ | 1 | 2100 | 1.1 |
| s25 | refuse | done | — | ✗ | 0 | 1400 | 0.0 |

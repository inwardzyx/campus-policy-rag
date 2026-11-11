# promptfoo 基线（held-out 105 题）

- 评测层：**online**（由 case_server 的 EVAL_LAYER 决定，这里是服务自报的值）
- 语料：入库 332 块 / 拦下 23 块
- 生成时间：2026-11-11 15:05
- 数据来源：`reports/promptfoo-online.json`（本报告由 `eval/summarize.py` 生成，数字不手抄）

| 指标 | 通过/总数 | 口径 |
|---|---|---|
| `recall5` | 40/44 | gold 条款进了粗捞 top-5 |
| `no_miss` | 42/44 | 应答题没被误拒（knowledge_hit==true） |
| `refuse_ok` | 41/61 | 拒答题拒对了（knowledge_hit==false） |

## 逐题明细

| qid | 类型 | Recall@5 | 未漏答 | 拒答正确 | gold 排名 | 说明 |
|---|---|---|---|---|---|---|
| h1 | answer | ✅ | ✅ | — | 1 |  |
| h10 | answer | ✅ | ✅ | — | 1 |  |
| h11 | answer | ✅ | ✅ | — | 1 |  |
| h12 | answer | ✅ | ✅ | — | 1 |  |
| h13 | answer | ✅ | ✅ | — | 1 |  |
| h14 | answer | ✅ | ✅ | — | 1 |  |
| h15 | refuse | — | — | ✅ | — |  |
| h16 | refuse | — | — | ✅ | — |  |
| h17 | refuse | — | — | ✅ | — |  |
| h18 | refuse | — | — | ✅ | — |  |
| h19 | refuse | — | — | ✅ | — |  |
| h2 | answer | ✅ | ✅ | — | 2 |  |
| h20 | refuse | — | — | ✅ | — |  |
| h21 | answer | ✅ | ✅ | — | 1 |  |
| h22 | answer | ✅ | ✅ | — | 1 |  |
| h23 | answer | ❌ | ❌ | — | — | gold 没进前 5 → 检索问题 |
| h24 | answer | ✅ | ✅ | — | 2 |  |
| h25 | answer | ✅ | ✅ | — | 1 |  |
| h3 | answer | ✅ | ✅ | — | 1 |  |
| h4 | answer | ✅ | ✅ | — | 1 |  |
| h5 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| h6 | answer | ✅ | ✅ | — | 2 |  |
| h7 | answer | ✅ | ✅ | — | 1 |  |
| h8 | answer | ✅ | ✅ | — | 1 |  |
| h9 | answer | ❌ | ❌ | — | — | gold 没进前 5 → 检索问题 |
| r1 | answer | ✅ | ✅ | — | 1 |  |
| r10 | answer | ✅ | ✅ | — | 1 |  |
| r11 | answer | ✅ | ✅ | — | 2 |  |
| r12 | answer | ✅ | ✅ | — | 1 |  |
| r13 | answer | ✅ | ✅ | — | 1 |  |
| r14 | answer | ✅ | ✅ | — | 5 |  |
| r15 | answer | ✅ | ✅ | — | 2 |  |
| r16 | answer | ✅ | ✅ | — | 1 |  |
| r17 | answer | ✅ | ✅ | — | 1 |  |
| r18 | answer | ✅ | ✅ | — | 2 |  |
| r19 | answer | ✅ | ✅ | — | 1 |  |
| r2 | answer | ✅ | ✅ | — | 1 |  |
| r20 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| r21 | refuse | — | — | ✅ | — |  |
| r22 | refuse | — | — | ✅ | — |  |
| r23 | refuse | — | — | ✅ | — |  |
| r24 | refuse | — | — | ✅ | — |  |
| r25 | refuse | — | — | ✅ | — |  |
| r26 | refuse | — | — | ✅ | — |  |
| r27 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r28 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r29 | refuse | — | — | ✅ | — |  |
| r3 | answer | ✅ | ✅ | — | 1 |  |
| r30 | refuse | — | — | ✅ | — |  |
| r31 | refuse | — | — | ✅ | — |  |
| r32 | refuse | — | — | ✅ | — |  |
| r33 | refuse | — | — | ✅ | — |  |
| r34 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r35 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r36 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r37 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r38 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r39 | refuse | — | — | ✅ | — |  |
| r4 | answer | ✅ | ✅ | — | 1 |  |
| r40 | refuse | — | — | ✅ | — |  |
| r41 | refuse | — | — | ✅ | — |  |
| r42 | refuse | — | — | ✅ | — |  |
| r43 | refuse | — | — | ✅ | — |  |
| r44 | refuse | — | — | ✅ | — |  |
| r45 | refuse | — | — | ✅ | — |  |
| r46 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r47 | refuse | — | — | ✅ | — |  |
| r48 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r49 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r5 | answer | ✅ | ✅ | — | 1 |  |
| r50 | refuse | — | — | ✅ | — |  |
| r6 | answer | ✅ | ✅ | — | 1 |  |
| r7 | answer | ✅ | ✅ | — | 1 |  |
| r8 | answer | ✅ | ✅ | — | 2 |  |
| r9 | answer | ✅ | ✅ | — | 1 |  |
| s1 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s10 | refuse | — | — | ✅ | — |  |
| s11 | refuse | — | — | ✅ | — |  |
| s12 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s13 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s14 | refuse | — | — | ✅ | — |  |
| s15 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s16 | refuse | — | — | ✅ | — |  |
| s17 | refuse | — | — | ✅ | — |  |
| s18 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s19 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s2 | refuse | — | — | ✅ | — |  |
| s20 | refuse | — | — | ✅ | — |  |
| s21 | refuse | — | — | ✅ | — |  |
| s22 | refuse | — | — | ✅ | — |  |
| s23 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s24 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s25 | refuse | — | — | ✅ | — |  |
| s26 | answer | ✅ | ✅ | — | 2 |  |
| s27 | answer | ✅ | ✅ | — | 1 |  |
| s28 | answer | ✅ | ✅ | — | 1 |  |
| s29 | answer | ✅ | ✅ | — | 1 |  |
| s3 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s30 | answer | ✅ | ✅ | — | 1 |  |
| s4 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s5 | refuse | — | — | ✅ | — |  |
| s6 | refuse | — | — | ✅ | — |  |
| s7 | refuse | — | — | ✅ | — |  |
| s8 | refuse | — | — | ✅ | — |  |
| s9 | refuse | — | — | ✅ | — |  |

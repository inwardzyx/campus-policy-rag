# promptfoo 基线（held-out 105 题）

- 评测层：**offline**（由 case_server 的 EVAL_LAYER 决定，这里是服务自报的值）
- 语料：入库 332 块 / 拦下 23 块
- 生成时间：2026-11-11 14:58
- 数据来源：`reports/promptfoo-offline.json`（本报告由 `eval/summarize.py` 生成，数字不手抄）

| 指标 | 通过/总数 | 口径 |
|---|---|---|
| `recall5` | 30/44 | gold 条款进了粗捞 top-5 |
| `no_miss` | 42/44 | 应答题没被误拒（knowledge_hit==true） |
| `refuse_ok` | 12/61 | 拒答题拒对了（knowledge_hit==false） |

## 与 run_eval.py 对账（`reports/run_eval-offline-heldout.log`）

| 指标 | 本报告 | run_eval | 一致 |
|---|---|---|---|
| `recall5` | 30/44 | 30/44 | ✅ |
| `no_miss` | 42/44 | 42/44 | ✅ |
| `refuse_ok` | 12/61 | 12/61 | ✅ |

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
| h17 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| h18 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| h19 | refuse | — | — | ✅ | — |  |
| h2 | answer | ✅ | ✅ | — | 1 |  |
| h20 | refuse | — | — | ✅ | — |  |
| h21 | answer | ✅ | ✅ | — | 1 |  |
| h22 | answer | ✅ | ✅ | — | 3 |  |
| h23 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| h24 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| h25 | answer | ✅ | ✅ | — | 1 |  |
| h3 | answer | ✅ | ✅ | — | 2 |  |
| h4 | answer | ✅ | ✅ | — | 1 |  |
| h5 | answer | ❌ | ❌ | — | — | gold 没进前 5 → 检索问题 |
| h6 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| h7 | answer | ✅ | ✅ | — | 1 |  |
| h8 | answer | ✅ | ✅ | — | 3 |  |
| h9 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| r1 | answer | ✅ | ✅ | — | 2 |  |
| r10 | answer | ✅ | ✅ | — | 1 |  |
| r11 | answer | ✅ | ✅ | — | 3 |  |
| r12 | answer | ✅ | ✅ | — | 1 |  |
| r13 | answer | ✅ | ✅ | — | 1 |  |
| r14 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| r15 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| r16 | answer | ✅ | ✅ | — | 1 |  |
| r17 | answer | ✅ | ✅ | — | 1 |  |
| r18 | answer | ❌ | ❌ | — | — | gold 没进前 5 → 检索问题 |
| r19 | answer | ✅ | ✅ | — | 4 |  |
| r2 | answer | ✅ | ✅ | — | 1 |  |
| r20 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| r21 | refuse | — | — | ✅ | — |  |
| r22 | refuse | — | — | ✅ | — |  |
| r23 | refuse | — | — | ✅ | — |  |
| r24 | refuse | — | — | ✅ | — |  |
| r25 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r26 | refuse | — | — | ✅ | — |  |
| r27 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r28 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r29 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r3 | answer | ✅ | ✅ | — | 4 |  |
| r30 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r31 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r32 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r33 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r34 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r35 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r36 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r37 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r38 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r39 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r4 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| r40 | refuse | — | — | ✅ | — |  |
| r41 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r42 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r43 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r44 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r45 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r46 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r47 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r48 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r49 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r5 | answer | ✅ | ✅ | — | 1 |  |
| r50 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| r6 | answer | ✅ | ✅ | — | 1 |  |
| r7 | answer | ✅ | ✅ | — | 2 |  |
| r8 | answer | ✅ | ✅ | — | 4 |  |
| r9 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| s1 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s10 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s11 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s12 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s13 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s14 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s15 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s16 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s17 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s18 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s19 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s2 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s20 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s21 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s22 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s23 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s24 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s25 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s26 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| s27 | answer | ✅ | ✅ | — | 1 |  |
| s28 | answer | ✅ | ✅ | — | 1 |  |
| s29 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| s3 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s30 | answer | ❌ | ✅ | — | — | gold 没进前 5 → 检索问题 |
| s4 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s5 | refuse | — | — | ✅ | — |  |
| s6 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s7 | refuse | — | — | ✅ | — |  |
| s8 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |
| s9 | refuse | — | — | ❌ | — | 被误答 → 判据太松 |

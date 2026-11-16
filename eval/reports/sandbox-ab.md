# `run_python` 配对实验：同一批题，开/关工具各跑一次

> ★ 本文件由 `eval/sandbox_ab.py` 生成，**不要手改**。
> 题目：`evalset/scenarios/company-52.jsonl` 中 **能抽出数字期望** 的 23 道（namespace=`company`）
> 打分：`kb.values.extract` 抽 `(数字, 单位)`，回答里命中即为通过（与 evalkit 同一抽取器）

## 结论

| | 关工具 | 开工具 |
|---|---|---|
| 数值命中 | 19/23 | 18/23 |

- 配对不一致：**b（关错开对）= 0**，c（关对开错）= 1
- exact McNemar（双侧）：**p = 1.0000**
- 实际调用 `run_python` 的题：**0/23**

→ 结论：**这批题上看不出显著增量**。

★ 关键解释：**工具只被调用 0 次**。

两条都成立时才谈得上增量：**题需要算 + 模型会去调**。这份实验里第一条就不成立 ——
company-52 的数字题（迟到扣分、试用期上限、试用期工资）**数值直接写在条款里**，
模型读条款就能答，不需要算；所以开不开工具都不改变数值命中。

**而在真正需要组合计算的题上，工具是有用的**（同一服务、手工构造两题）：

| 题 | 关工具 | 开工具 |
|---|---|---|
| 奖学金 6000 + 勤工助学 300×10 | 心算，答 9000 | **调 run_python**，答 9000 |
| 综合测评 85×70% + 90×30% | 心算，答 86.5 | 这次没调（心算），答 86.5 |

⇒ 真问题不是「工具没用」，是**模型不一定会去调**（第二题就没调）。下一步要么在
system prompt 里加一条「涉及金额/天数/比例必须用 run_python」，要么补一批
**条款里只有系数、答案必须组合计算**的题，让「需要算」这件事在分布里真的存在。

## 逐题

| 题 | 关 | 开 | 开工具调了什么 |
|---|---|---|---|
| `c50-A1` | ✅ | ✅ | search_policy, ask_policy |
| `c50-A3` | ✅ | ✅ | ask_policy |
| `c50-A4` | ✅ | ✅ | ask_policy, search_policy |
| `c50-A5` | ✅ | ✅ | ask_policy, search_policy |
| `c50-A7` | ✅ | ✅ | ask_policy, search_policy |
| `c50-A10` | ✅ | ✅ | ask_policy, search_policy |
| `c50-A12` | ❌ | ❌ | ask_policy, search_policy |
| `c50-A13` | ✅ | ✅ | ask_policy, search_policy |
| `c50-A15` | ✅ | ✅ | search_policy, ask_policy |
| `c50-A16` | ❌ | ❌ | search_policy, search_policy |
| `c50-A17` | ✅ | ✅ | search_policy, search_policy |
| `c50-A19` | ✅ | ✅ | search_policy, ask_policy |
| `c50-B3` | ✅ | ✅ | search_policy, search_policy |
| `c50-B5` | ✅ | ✅ | ask_policy, search_policy, search_policy |
| `c50-B6` | ✅ | ✅ | ask_policy, search_policy |
| `c50-B7` | ✅ | ❌ | ask_policy, search_policy |
| `c50-B8` | ✅ | ✅ | ask_policy, search_policy |
| `c50-B9` | ❌ | ❌ | search_policy, search_policy, search_policy, ask_policy |
| `c50-B10` | ✅ | ✅ | search_policy, search_policy |
| `c50-C1` | ❌ | ❌ | search_policy, search_policy |
| `c50-C5` | ✅ | ✅ | ask_policy, search_policy, search_policy |
| `c50-C9` | ✅ | ✅ | ask_policy, search_policy |
| `c50-D2` | ✅ | ✅ | ask_policy, search_policy |

## 版本

- 运行指纹：`1a14bd95f34e`（git `10477f7b9164` dirty=`True`）
- 依赖指纹：`requirements.txt`=e15469e2、`requirements-optional.txt`=9d4b995d
- 版面后端：auto 会选 `docling`
- 语料哈希：`docs/kb`=d39a8542；`docs/inbox`=cb62f2bf
- 判据版本：`ec2d60d8`（agent gate 以原问题为准 + 改写去重上限 2；评分 k=5；指标分母各自算）

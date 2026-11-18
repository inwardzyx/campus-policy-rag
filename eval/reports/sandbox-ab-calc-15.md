# `run_python` 配对实验：同一批题，开/关工具各跑一次

> ★ 本文件由 `eval/sandbox_ab.py` 生成，**不要手改**。
> 题目：`evalset\scenarios\compute-15.jsonl` 的 **15** 道（namespace=`company`）
> 打分：`kb.values.extract` 抽 `(数字, 单位)`，回答里命中即为通过（与 evalkit 同一抽取器）

## 结论

| | 关工具 | 开工具 |
|---|---|---|
| 数值命中 | 11/15 | 13/15 |

- 配对不一致：**b（关错开对）= 2**，c（关对开错）= 0
- exact McNemar（双侧）：**p = 0.5000**
- 实际调用 `run_python` 的题：**13/15**

→ 结论：**数值命中几乎不变**（+2 / −0），这批题模型心算也够准。

★ 工具被调用 13 次。

★ 这批题按条款公式现算期望值（21.75 天、150%/200%/300%、迟到 30 分钟以上按半日事假），所以「命中」= 数值算对。
★ 工具采用率 100% 说明「需要算」在分布里成立了；但准确率几乎不变 ⇒ 工具的价值是**计算过程可复核**，不是「更准」。
★ 判分口径的局限：答案只写「不用给钱」而没有数字时会被判错（0 元类题目尤其）。

## 逐题

| 题 | 关 | 开 | 开工具调了什么 |
|---|---|---|---|
| `calc-01` | ❌ | ❌ | search_policy, ask_policy |
| `calc-02` | ✅ | ✅ | search_policy, ask_policy, run_python |
| `calc-03` | ✅ | ✅ | search_policy, ask_policy, run_python |
| `calc-04` | ✅ | ✅ | search_policy, ask_policy, run_python |
| `calc-05` | ✅ | ✅ | search_policy, ask_policy, run_python |
| `calc-06` | ✅ | ✅ | search_policy, ask_policy, run_python |
| `calc-07` | ✅ | ✅ | search_policy, ask_policy, run_python |
| `calc-08` | ❌ | ✅ | search_policy, ask_policy, run_python |
| `calc-09` | ✅ | ✅ | ask_policy, search_policy, run_python |
| `calc-10` | ✅ | ✅ | run_python |
| `calc-11` | ✅ | ✅ | run_python |
| `calc-12` | ❌ | ✅ | search_policy, ask_policy, run_python |
| `calc-13` | ✅ | ✅ | search_policy, search_policy, run_python |
| `calc-14` | ✅ | ✅ | search_policy, search_policy, run_python |
| `calc-15` | ❌ | ❌ | search_policy, ask_policy |

## 版本

- 运行指纹：`f686faa9bf21`（git `f90b7376c9bb` dirty=`True`）
- 依赖指纹：`requirements.txt`=98cf4408、`requirements-optional.txt`=9d4b995d
- 版面后端：auto 会选 `docling`
- 语料哈希：`docs/kb`=fe2bb077；`docs/inbox`=6f3d4043
- 判据版本：`fe0cf083`（agent gate 以原问题为准 + 改写去重上限 2；评分 k=5；指标分母各自算）

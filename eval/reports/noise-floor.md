# 噪声地板（online 层，同配置重跑 4 次）

- 生成者：`eval/noise_floor.py`；数据：`reports/noise/run-*.json`（每次跑的逐题结论）。
- 生成时间：2026-11-12 08:53
- 运行指纹相关三字段：git `96b161dfcf9bddc8543e10a4030b71f8feb60840`；`docs/kb`=d39a8542；`docs/inbox`=cb62f2bf
- 依赖指纹：`requirements.txt`=e15469e2、`requirements-optional.txt`=9d4b995d（`requirements*.txt` 的 sha256 前 8 位）
- 版面后端：auto 会选 `docling`；可用 `docling、docling-local`；不可用 `mineru`；PDF_BACKEND=`auto`
- 口径说明：3 次重跑来自同一个服务进程（缓存开）⇒ 逐次完全一致；第 4 列是另一个进程的基线跑批 ⇒ 极差反映的是【进程/批次之间】的差异，不是同进程内的抖动。另：关缓存的新进程跑批本日试过 1 次，在第 11 题挂死（LLM 单调用无超时会挂住），未完成 ⇒ 本表是【下界】。
- ⚠️ 「观测极差」是按**题数**算的（max−min）。它是**观测**，不是置信区间；样本只有 4 次，所以它只能当下界用：**小于等于这个幅度就别解释成「变好/变坏」**。

| 指标 | run1 | run2 | run3 | run4 | 观测极差（题） |
|---|---|---|---|---|---|
| `recall5` | 41/44 | 42/44 | 42/44 | 42/44 | ±1 |
| `no_miss` | 44/44 | 44/44 | 44/44 | 44/44 | ±0 |
| `refuse_ok` | 39/61 | 38/61 | 38/61 | 38/61 | ±1 |

**观测极差**：recall5 ±1、no_miss 0、refuse_ok ±1 ⇒ 小于等于这个幅度的差异**不解释**。

> 每次跑都能追回去：`reports/noise/run-<i>.json` 里是该次跑的**逐题结论**（`cases[qid].scores`），聚合数就是从它算出来的。

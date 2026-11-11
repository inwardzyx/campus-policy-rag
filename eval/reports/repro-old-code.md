# 旧报告能不能复现 / 两组数能不能相减（脚本生成，别手改）

> 生成者：`eval/repro_old_code.py`；数据来源：`eval/reports/repro-old-code.json`。

- 说明：本文件描述 **旧代码**（worktree 里那个 commit）下的测量，**不是**当前基线。
- 运行指纹相关三字段：git `a9e4f85e3d07a513ea70e6efc5f6c58559f93c3e`；`docs/kb`=d39a8542；`docs/inbox`=cb62f2bf（用于 `scripts/check_report_freshness.py` 比对；缺失则会被判 UNVERIFIABLE）

| 变体 | 代码 | 判分脚本 | docs 块 | 企业 demo 块 | chunk 文本 sha | recall5 | no_miss | refuse_ok |
|---|---|---|---|---|---|---|---|---|
| `old` | 旧代码 `a9e4f85e` | 旧（worktree） | 263 | 0 | `9f35434c857c…` | 35/44 | 42/44 | 9/61 |
| `current-shim` | 旧代码+shim `a9e4f85e` | 当前（复制进 worktree） | 263 | 0 | `9f35434c857c…` | 35/44 | 42/44 | 9/61 |
| `current` | 当前代码 `466d368c` | 当前 | 263 | 69 | `9f35434c857c…` | 30/44 | 42/44 | 12/61 |
| `current` | 当前代码 `466d368c` | 当前 | 263 | 69 | `9f35434c857c…` | 30/44 | 42/44 | 12/61 |

## `old`（2026-11-11 16:17:08）

- 代码：commit `a9e4f85e3d07`（root `D:\_repro`），跑之前干净=`False`，跑之后干净=`False`
- 数据：questions `45b72e253a7ccd42…`，docs 树 `6beb6f5968481a5f…`（16 个文件）
- 索引：docs 入库 263 块 / 拦下 23 块；企业 demo 0 块（本评测不查）；chunk 文本 sha `9f35434c857cc2f9…`
- 脚本：`evalset\run_eval.py` sha256 `e68e28c3f1f70020…`（worktree（旧 commit 自带））
- 退出码：0


| 指标 | 通过/总数 |
|---|---|
| `no_miss` | 42/44 |
| `recall5` | 35/44 |
| `refuse_ok` | 9/61 |

```text
2026-11-11 16:16:40,987 [INFO] __main__: 入库 263 块，拦下 23 块
- s23: 误答 [clear]  我今年录取后直接去当兵了、没来报到，家里挺困难的，国家助学金能申请吗？
- s24: 误答 [clear]  处分规定里白纸黑字写『学生工作处』，可现在学校文件都写『党委学生工作部』，我要交申诉材料到底找哪个部门？
- s25: 误答 [clear]  五一我外校的高中同学跟着我们班出去活动，路上受了伤，学校这规定管他吗？
2026-11-11 16:16:42,787 [INFO] __main__: 耗时 23.5s
```

## `current-shim`（2026-11-11 16:18:07）

- 代码：commit `a9e4f85e3d07`（root `D:\_repro`），跑之前干净=`False`，跑之后干净=`False`
- 数据：questions `45b72e253a7ccd42…`，docs 树 `6beb6f5968481a5f…`（16 个文件）
- 索引：docs 入库 263 块 / 拦下 23 块；企业 demo 0 块（本评测不查）；chunk 文本 sha `9f35434c857cc2f9…`
- 脚本：`evalset\_shim_run_current.py` sha256 `81fe4f9b6e3d8fdb…`（当前仓库（复制进 worktree 执行））
- 退出码：0

- ⚠️ 为跑这一次改动了 worktree：['_run_eval_current.py', '_shim_run_current.py']
  - 把当前的 evalset/run_eval.py 复制进 worktree（_run_eval_current.py）
  - 新增 shim _shim_run_current.py：handle_chat = chat（语义假设）
  - 当前脚本传的 use_rewrite 在旧 ChatRequest 上是 extra ⇒ 被 pydantic 静默丢弃

| 指标 | 通过/总数 |
|---|---|
| `no_miss` | 42/44 |
| `recall5` | 35/44 |
| `refuse_ok` | 9/61 |

```text
2026-11-11 16:17:32,022 [INFO] _run_eval_current: 入库 263 块，拦下 23 块
- s22: 误答 [clear]  生源地助学贷款毕业后多久要开始还？有没有宽限期？
- s23: 误答 [clear]  我今年录取后直接去当兵了、没来报到，家里挺困难的，国家助学金能申请吗？
- s24: 误答 [clear]  处分规定里白纸黑字写『学生工作处』，可现在学校文件都写『党委学生工作部』，我要交申诉材料到底找哪个部门？
- s25: 误答 [clear]  五一我外校的高中同学跟着我们班出去活动，路上受了伤，学校这规定管他吗？
```

## `current`（2026-11-11 18:09:40）

- 代码：commit `466d368cb0e4`（root `D:\_review_rag-cs`），跑之前干净=`False`，跑之后干净=`False`
- 数据：questions `45b72e253a7ccd42…`，docs 树 `6beb6f5968481a5f…`（16 个文件）
- 索引：docs 入库 263 块 / 拦下 23 块；企业 demo 69 块（本评测不查）；chunk 文本 sha `9f35434c857cc2f9…`
- 脚本：`D:\_review_rag-cs\evalset\run_eval.py` sha256 `649d4b62c51131aa…`（当前仓库）
- 退出码：0


| 指标 | 通过/总数 |
|---|---|
| `no_miss` | 42/44 |
| `recall5` | 30/44 |
| `refuse_ok` | 12/61 |

```text
2026-11-11 18:09:05,756 [INFO] __main__: 入库 332 块，拦下 23 块
- s23: 误答 [clear]  我今年录取后直接去当兵了、没来报到，家里挺困难的，国家助学金能申请吗？
- s24: 误答 [clear]  处分规定里白纸黑字写『学生工作处』，可现在学校文件都写『党委学生工作部』，我要交申诉材料到底找哪个部门？
- s25: 误答 [clear]  五一我外校的高中同学跟着我们班出去活动，路上受了伤，学校这规定管他吗？
2026-11-11 18:09:10,627 [INFO] __main__: 耗时 39.2s
```

## `current`（2026-11-11 18:12:39）

- 代码：commit `466d368cb0e4`（root `D:\_review_rag-cs`），跑之前干净=`False`，跑之后干净=`False`
- 数据：questions `45b72e253a7ccd42…`，docs 树 `6beb6f5968481a5f…`（16 个文件）
- 索引：docs 入库 263 块 / 拦下 23 块；企业 demo 69 块（本评测不查）；chunk 文本 sha `9f35434c857cc2f9…`
- 脚本：`D:\_review_rag-cs\evalset\run_eval.py` sha256 `649d4b62c51131aa…`（当前仓库）
- 退出码：0


| 指标 | 通过/总数 |
|---|---|
| `no_miss` | 42/44 |
| `recall5` | 30/44 |
| `refuse_ok` | 12/61 |

```text
2026-11-11 18:12:08,935 [INFO] __main__: 入库 332 块，拦下 23 块
- s23: 误答 [clear]  我今年录取后直接去当兵了、没来报到，家里挺困难的，国家助学金能申请吗？
- s24: 误答 [clear]  处分规定里白纸黑字写『学生工作处』，可现在学校文件都写『党委学生工作部』，我要交申诉材料到底找哪个部门？
- s25: 误答 [clear]  五一我外校的高中同学跟着我们班出去活动，路上受了伤，学校这规定管他吗？
2026-11-11 18:12:14,012 [INFO] __main__: 耗时 29.5s
```

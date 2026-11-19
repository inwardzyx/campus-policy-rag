# agent 基线（105 题独立集，`/agent` 端到端）

> ★ 本文件由 `eval/agent_metrics.py` 生成，**不要手改**（下次生成会覆盖）。
> 跑法：`http://127.0.0.1:8033`（现跑）

- 运行指纹：`8355c9606283`（`kb/runstate.py`；git `c72a52c6c0ee` dirty=`True`，python 3.14.6）
- 依赖指纹：`requirements.txt`=98cf4408、`requirements-optional.txt`=9d4b995d（`requirements*.txt` 的 sha256 前 8 位）
- 版面后端：auto 会选 `docling`；可用 `docling、docling-local`；PDF_BACKEND=`auto`
- 语料哈希：`docs/kb`=fe2bb077；`docs/inbox`=6f3d4043
- 判据版本：`0a440c38`（agent gate 以原问题为准 + 改写去重上限 2；评分 k=5；指标分母各自算；改这些文件会让旧报告失效）

## 结论

| 指标 | 值 | 分母 | 说明 |
|---|---|---|---|
| `gold_in_sources` | 8/10 | 应答 10 | agent 返回的来源里有 gold 条款 |
| `no_miss` | 9/10 | 应答 10 | 没拒答且带来源（应答题不该被「查不到」打发） |
| `refuse_ok` | —/0 | 拒答 0 | 拒答题真的拒了 |
| `done_rate` | 9/10 | 全部 10 | `stopped_reason == done` 的占比 |
| `avg_steps` | 1.7 | 全部 10 | 每题工具调用次数 |
| `avg_tokens` | 1974 | 全部 10 | 每题 token（含循环里的每一轮） |

**收尾原因分布**：`done` 9、`gate_refused` 1

## 两条口径警告（别把这两个数相减）

1. **`gold_in_sources` ≠ `recall5`**：前者算的是 agent 返回的 `sources`（`/chat` 精排后最多 3 条），
   后者算的是粗捞 top-5。分子分母都不同，**相减没有意义**。
2. **agent 与 `/chat` 是两个被测对象**：`eval/gate.py` 的配对检验只允许同一层的同一条路径
   （否则「换了被测对象」会被当成「改动有效」）。要比较只能并列看，不能做 McNemar。


## 逐题

| 题号 | 类型 | 收尾 | gold | 有来源 | 步数 | token | 秒 |
|---|---|---|---|---|---|---|---|
| h1 | answer | done | ✓ | ✓ | 2 | 2110 | 10.5 |
| h2 | answer | done | ✓ | ✓ | 2 | 2103 | 12.8 |
| h3 | answer | done | ✓ | ✓ | 2 | 2241 | 14.2 |
| h4 | answer | done | ✓ | ✓ | 1 | 1713 | 6.1 |
| h5 | answer | done | ✗ | ✓ | 1 | 1913 | 6.9 |
| h6 | answer | done | ✓ | ✓ | 1 | 1794 | 6.0 |
| h7 | answer | done | ✓ | ✓ | 2 | 1934 | 9.8 |
| h8 | answer | done | ✓ | ✓ | 2 | 1747 | 7.3 |
| h9 | answer | gate_refused | ✗ | ✗ | 2 | 2037 | 8.8 |
| h10 | answer | done | ✓ | ✓ | 2 | 2143 | 12.3 |

## 备注

多轮改动后的冒烟（前 10 题）

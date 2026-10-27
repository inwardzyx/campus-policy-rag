# 盲审响应（2026-10-27）

> 盲审报告：`experiments/blind-review-2026-10-27.md`（ZCode / GLM-5.3-Flash，只读）
> 本文逐条说明：**采纳并修了什么**、**哪些只是改文档**、**哪些作为已知风险保留**。

## P0

| # | 指控 | 处理 |
|---|---|---|
| P0-1 | "第一次 accept"的 +1 小于同日运行波动（41 vs 39），`mcnemar_p=1.0` 未披露 | **采纳**。文档改成"机制跑通，不构成收益证据"，并写明 41/46 那次运行与 p=1.0 |
| P0-2 | `answer` 率实为"gold 进上下文率"，头版套件没有答案校验 | **采纳**（部分）。指标描述改为"检索命中率（不是答案正确性）"；`answer_value` 对 company-52 仍为 None（缺 expect），在文档中明写 |

## P1

| # | 指控 | 处理 |
|---|---|---|
| P1-1 | `_ok` 对 deny 要求 `refuse_ok`，导致越权修复不计 gains | **已修**：deny 正确桶改为 `ok`；新增用例 `test_deny_ok_counts_as_gain` |
| P1-2 | `context_precision = hits/len(gold)` 名实不符 | **已修**：recall = hits/len(gold)；precision = hits/len(included)；测试同步 |
| P1-3 | `rollback()` 状态机坏；registry 无消费方 | **已修**：显式 `accepted_order` 栈 + 单一 active；accept 写 `evalset/loop/active.json`，服务启动**自动加载**；`rollback` 连续调用可退到空 |
| P1-4 | 文档漏报 41/46 与 p=1.0 | **已修文档**：见 HANDOFF / enterprise-kb 的更正 |

## P2

| # | 指控 | 处理 |
|---|---|---|
| P2-1 | mine 逐题补丁、无泛化评估；budget_cut 也挖 | **部分修**：mine 只挖 `retrieval_miss`/`rank_shift`；泛化性未解决，文档标注"候选=逐题补丁" |
| P2-2 | 固定端口 + 健康检查不验进程身份 | **未修**（已知风险）：在 loopgate 文档与本文标注；后续可加 `--port` 与身份校验 |
| P2-3 | gate 不校验报告完整性 | **已修**：before/after 行数或交集不一致 ⇒ 直接 reject，并加测试 |
| P2-4 | 拒答题"对冲式回答"可绕过 refuse_ok | **未修**（取舍）：拒绝标记优先的设计未变，作为已知风险记录 |
| P2-5 | `_norm_source` 互顶 / 条款精确串匹配 | **未修**（低概率）：多文档同名条款的风险记录在案 |
| P2-6 | expand 的 `already` 字符集语义混淆 | **未修**：有幂等测试兜底 |
| P2-7 | gate 复用路径、覆盖即毁证 | **已记录**：建议后续每轮带时间戳落盘 |

## 对结论的修正（写进文档的头条）

- ❌ "闭环带来了 +1 收益" ⇒ ✅ "闭环机制端到端跑通；accept 发生，但 +1 小于运行间波动（41↔39），
  `mcnemar_p=1.0`，**不构成收益证据**"。
- ❌ "应答 39/46" ⇒ ✅ "**检索命中** 39/46（答案正确性在 company-52 上未度量）"。
- ✅ 仍然成立的两条：**门禁会拦坏候选**（6 gain / 11 loss 那次 reject）、**闭环可端到端执行**。

# agent 冒烟：12 题对照 `/chat`（Step 1 判据 1.3）

> ★ 这份文件由 `agent/smoke_agent.py` 生成，**不要手改**（下次生成会覆盖）。
> 数字来源：`eval/reports/agent-smoke.json`（从 _agent_smoke3.json 重渲染）

## 结论

- **拒答不退化**：/chat 拒对 `8/8`，/agent 拒对 `8/8`（门槛：不低于 /chat）
- **应答带来源**：/agent `4/4`
- **成本**：平均 `1592` token/题（`/chat` 单步约 700）

## 逐题

| 题号 | 类型 | /chat 命中 | /agent 收尾 | 拒答 | 来源数 | 轮数 | token |
|---|---|---|---|---|---|---|---|
| h1 | answer | True | done | False | 2 | 2 | 1902 |
| h2 | answer | True | done | False | 3 | 2 | 2183 |
| h3 | answer | True | done | False | 3 | 2 | 2292 |
| h4 | answer | True | done | False | 1 | 2 | 1903 |
| h15 | refuse | False | done | True | 0 | 2 | 1470 |
| h16 | refuse | False | done | True | 0 | 2 | 1499 |
| h17 | refuse | False | done | True | 0 | 2 | 1495 |
| h18 | refuse | False | done | True | 0 | 2 | 1513 |
| h19 | refuse | False | done | True | 0 | 2 | 1758 |
| h20 | refuse | False | done | True | 0 | 2 | 1697 |
| r21 | refuse | False | no_tool | True | 0 | 1 | 697 |
| r22 | refuse | False | no_tool | True | 0 | 1 | 694 |

## 备注

第三次跑批：search_policy 改走 handle_chat（与 /chat 同一道精排闸）。前两版分别是 4/8 与 6/8（见 PLAN.md 的对照表）。

# 场景题集（scenarios）

## 为什么按场景分文件

同一句"报销多久内提交"，在 **HR**、**合规**、**客服** 三个场景里判据不同：

- HR：能不能按制度办（考勤/假期/报销时限）；
- 合规：有没有越权、有没有引用到不该看的条款；
- 客服：答得对不对、拒得干不干脆。

把三类题混在一个文件里，会得到一个"整体 78%"的漂亮数字，然后不知道该修哪。
所以：**一个场景一个文件，各自冻结、各自出报告**。

## 文件格式（JSONL，一行一题）

```json
{"id": "hr-01", "type": "answer", "scenario": "hr",
 "question": "年假可以结转到明年吗？",
 "gold": ["考勤与休假管理办法.md", "第三章-第十二条"],
 "acl": ["public"]}
{"id": "hr-14", "type": "refuse", "scenario": "hr",
 "question": "公司年会在哪办？", "gold": null}
{"id": "hr-18", "type": "deny", "scenario": "hr",
 "question": "董事会的薪酬决议怎么写的？", "gold": null, "acl": ["hr"]}
```

- `type`：`answer` / `refuse` / `deny`（与 `kb/eval_runner.py`、`evalset/enterprise_eval_template.md` 同口径）；
- `gold`：`[文档名, 条款标题]`，**必须**能在语料里找到 `## <条款标题>`（有脚本查，见下）；
- `deny` 题的 `acl` 是**提问者身份**，不是文档的 acl —— 它模拟"这个人不该看到这些"。

## 现状

| 场景 | 状态 | 题集 | 语料 |
|---|---|---|---|
| `campus` | ✅ 可跑（复用老题集，20 题：15 答 + 5 拒） | `evalset/questions.json` | `docs/kb/`（11 份校规） |
| `hr` / `compliance` / `support` | ⏳ 待真实语料（HANDOFF P1） | 未出题（**不编数据**） | 未上传 |

`campus` 直接复用 `evalset/questions.json`（不复制一份，避免两份 gold 漂移）；
其余场景等真实制度文档进来再出题——**没有语料就不出题**，否则评测只能测"模型会不会编"。

## 出题与验收纪律

1. 出题人**不看**检索代码与历史结果（沿用 `enterprise_eval_template.md` 的隔离要求）；
2. 冻结后跑，不用它调参；调参用 `heldout` 之外的题；
3. 每批改动前后用 `scripts/run_scenario_eval.py` 跑同一份题集，**配对对照**；
4. gold 与语料的联动用 `scripts/check_eval_gold.py` 守：扫到 0 条 gold 直接报红。

## 跑法

```powershell
# 1) 把语料灌进一个 namespace（只有带 namespace 的请求才有 context trace）
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --scenario campus --namespace campus_eval --bootstrap-docs docs/kb `
    --cases evalset/questions.json --out evalset/report-scenario-campus.json

# 2) 离线复算（用录好的响应重算指标，不联网）
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --responses <录好的响应文件.json> --out evalset/report-scenario-campus.json
```

## 报告里看什么

- 三类题的正确率 + Wilson 区间（`answer/refuse/deny`）；
- 上下文指标：`context_recall`、`context_precision`、`ndcg@3`、`budget_cut_rate`、`p95_ms`；
- **归因分布**：`retrieval_miss / rank_shift / budget_cut / gen_uncited / acl_leak / stale_version`。
  调优只能针对"当前最多的那个桶"做单点改动，然后用同一批题**配对**重跑。

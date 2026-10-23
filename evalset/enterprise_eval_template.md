# 企业语料独立评测模板（人工出题）

> 目的：给每个企业 namespace 一份**可信**的评测，而不是“LLM 自己出题自己打分”。
> 配套：`scripts/run_kb_eval.py`、`kb/eval_runner.py`、`evalset/enterprise_eval_sample.jsonl`。

## 0. 先分清两种评测

| 类型 | 谁出题 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| **smoke**（`GET /kb/{ns}/eval`） | 程序自动 | 库内可检索、库外会拒、ACL 不漏 | 不能证明“答得对/答得全” |
| **独立集**（本模板） | 人，且**不看检索代码/结果** | 真实问题上的命中、拒答、越权 | 不能靠它调参后再报同一套 |

**纪律**：独立集出题人不能是写检索的人；至少要在“不接触代码、不接触已有结果”的会话里出题。
出完题先冻结，再跑；跑完不要拿它调 prompt/参数。调参用另一套 dev 集。

## 1. 题量与构成（每个企业语料）

| 题型 | 数量 | 例子 |
|---|---|---|
| `answer` 应答 | 40 | 制度/FAQ 里有明确条款的问题 |
| `refuse` 拒答 | 20 | 库里没有的问题（开放域/别的公司/未来事件） |
| `deny` 越权 | 10 | 只对某角色可见的条款，用无权限角色问 |

共 70 题。少于 30 题的报告只能叫“抽样”，不要写“准确率”。

## 2. 题目从哪来

1. **真实问题优先**：客服工单、HR/IT 群聊、销售 FAQ、用户搜索日志；
2. 没有日志就做**角色走查**：新员工/HR/财务/访客各写 10 个“他真会问的问题”；
3. **不要**从文档标题反推问题（那会天然命中，虚高）；
4. 每题必须能指到**人工确认过的条款/FAQ 条目**（`expected_sources`）。

## 3. JSONL 字段

```json
{"id":"A01","type":"answer","question":"报销时限是多久？","expected_sources":["报销制度","第三条"],"acl":["public"],"note":"财务部确认"}
{"id":"R01","type":"refuse","question":"图书馆几点关门？","acl":["public"]}
{"id":"D01","type":"deny","question":"员工薪酬可以互相透露吗？","acl":["finance"],"note":"该条只有 hr 可见"}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 稳定编号 |
| `type` | 是 | `answer` / `refuse` / `deny` |
| `question` | 是 | 用户原话，不要改写成“标准问法” |
| `expected_sources` | answer 必填 | 子串列表；每个都要出现在某条 source 的 `doc` 里 |
| `acl` | 否 | 该题调用方角色；不填用命令行 `--acl` |
| `note` | 否 | 依据/确认人 |

## 4. 判分（`kb/eval_runner.py`）

- `answer`：`expected_sources` 里每个子串都能在某条 `source.doc` 找到 → 通过；
- `refuse`：`knowledge_hit=false` 且 `sources=[]` → 通过；
- `deny`：同 `refuse`（越权时**不能返回任何 source**）；
- 报告带 **Wilson 95% 区间**；不要只报一个百分比。

## 5. 跑法

```powershell
$py='D:\Python-project\.venv\Scripts\python.exe'
& $py scripts\run_kb_eval.py `
  --url http://127.0.0.1:8000 `
  --namespace demo `
  --file evalset\enterprise_eval_sample.jsonl `
  --out evalset\report-demo.json
```

`/search` 不调 LLM；要先起服务、并确保该 namespace 已上传语料。

## 6. 报告怎么写（诚实版）

- 写清：题量、题型分布、出题人/确认人、是否冻结、有没有用它调参；
- 写清：`answer` 的通过是“source 命中”，不是“答案完全正确”（后者需人工 rubric）；
- 写清：自动 smoke 与独立集是两套东西，别混；
- 失败样例要原样列出，不要只报总数。

# eval/ —— 评测壳（promptfoo + 业务判据）

**一句话**：把 `evalset/heldout/questions.json` 那 **105 道题**（44 应答 + 61 拒答）跑一遍，
产出一份**能对比后续改动**的分数，落盘成 `eval/reports/BASELINE-<层>.md`。

> 为什么用 promptfoo 打底、哪三块必须自研（McNemar 配对门禁 / 业务判据 / 失败归因）：
> 见 `DECISIONS.md`（待写，HANDOFF 第 5 节待办 2、3）。这里只写"怎么用、边界在哪"。

## 接线（为什么长这样）

```text
promptfoo（Node，负责调度/聚合/逐题表）
   │  POST http://127.0.0.1:8091/case   {"qid": "h1", "question": "…"}
   ▼
eval/case_server.py（Python，进程内跑真管道）
   │  import evalset/run_eval.py 里的函数 ← ★ 判分口径的唯一来源
   ▼
service.py / rag：rag.search()（算 Recall@5）、handle_chat()（算 knowledge_hit）
```

三条设计约束，每条都是被实际踩到逼出来的：

1. **不写第二份判分公式。** `Recall@5` 的官方口径是 `rag.search()` 的 top-5，**不是**
   `/chat` 返回的 `sources`（`evalset/run_eval.py` 顶部那条注释解释了为什么：因拒答而
   `sources` 为空时，会被误算成"没召回"）。⇒ `case_server.py` 直接 `import run_eval`，不重算。
2. **不用 promptfoo 的 python provider。** 它会 spawn 子进程并用**管道**通信，
   而本机沙箱把 Node 子进程的管道挡掉（EPERM）。http provider 只走本机 socket，不受影响。
3. **题面不手抄。** `tests.yaml` 由 `gen_tests.py` 从 `questions.json` 生成；
   服务端会把收到的题面与 `questions.json` 对一遍，不一致就返回 `drift`，
   断言那边**一律判红** —— 旧 `tests.yaml` 跑出来的分数是假的。

## 跑法

两个终端。**命令**：

```powershell
# 终端 1 —— 起评测服务（离线层：零 API 成本）
cd D:\_review_rag-cs
$env:PYTHONIOENCODING='utf-8'
D:\Python-project\.venv\Scripts\python.exe eval\case_server.py

# 换成产品默认路径（rerank + 查询改写，会花 DeepSeek 的 token）：
#   $env:EVAL_LAYER='online'   再跑上面那条命令
```

**跑完会看到**（终端 1）：

```text
[boot] 题目 105 道，层=offline，正在加载模型 + 建库……
[boot] 就绪 19.3s：入库 332 块 / 拦下 23 块，监听 127.0.0.1:8091
[case] h1 answer hit=True gold=True 812ms
```

```powershell
# 终端 2 —— 生成题目 + 跑 promptfoo + 出报告
cd D:\_review_rag-cs\eval
npm install                      # 沙箱里要加 --ignore-scripts，见坑 4
D:\Python-project\.venv\Scripts\python.exe gen_tests.py
npx promptfoo eval -c promptfooconfig.yaml --no-cache -j 1 -o reports\promptfoo-offline.json
D:\Python-project\.venv\Scripts\python.exe summarize.py --input reports\promptfoo-offline.json `
    --out reports\BASELINE-offline.md --compare reports\run_eval-offline-heldout.log
```

**跑完会看到**（终端 2 末尾）：

```text
[ok] 与 reports\run_eval-offline-heldout.log 逐项一致
[ok] 已写 reports\BASELINE-offline.md
  recall5    30/44   gold 条款进了粗捞 top-5
```

对账用的那份 run_eval 日志这样重生成（**不花 API 钱**，约 26 秒）：

```powershell
cd D:\_review_rag-cs
D:\Python-project\.venv\Scripts\python.exe evalset\run_eval.py --questions evalset\heldout\questions.json `
    --label heldout-check | Set-Content -Encoding utf8 eval\reports\run_eval-offline-heldout.log
```

- ★ **不要用 `>` 重定向**：Windows PowerShell 5.1 的 `>` 写的是 UTF-16LE，
  而 `summarize.py` 按 UTF-8 读 ⇒ 正则一条都匹配不上，对账会显示"读不到"（红，但原因假）。
- ★ `--label heldout-check` 是必须的：不带 label 时 `run_eval.py` 会拒跑 ——
  它怕覆盖主评测集的 `evalset/report-vector.md`（那份被 README 与数字对账器引用）。

## 三条指标与它们的**分母**

| 指标（promptfoo metric 名） | 分母 | 口径 |
|---|---|---|
| `recall5` | 44（应答题） | gold 条款进了粗捞 top-5 |
| `no_miss` | 44（应答题） | `knowledge_hit==true`，即没被误拒（"漏答率"的反面） |
| `refuse_ok` | 61（拒答题） | `knowledge_hit==false`，即拒对了 |

★ 分母必须各自算：promptfoo 的总 pass rate 会被 61 道拒答题稀释，
"应答层悄悄退化了"在那一个数里看不出来。

## 本机实测（2026-11-11，held-out 105 题）

数字**只**从产物读，不手抄；每行都附了产出它的那份报告。

| 层 | `recall5` | `no_miss` | `refuse_ok` | 产物 |
|---|---|---|---|---|
| offline（只用向量分 0.55 判拒答） | 30/44 | 42/44 | 12/61 | `reports/BASELINE-offline.md` |
| online（产品默认：rerank + 查询改写） | 40/44 | 42/44 | 41/61 | `reports/BASELINE-online.md` |

能读出来的结论（**只到"方向"这一级**，不要当显著性问题下结论 ——
配对显著性检验正是待办 2 要做的 `eval/gate.py`）：

- 查询改写 + 精排把应答题的检索命中从 30/44 提到 40/44，而**漏答率一模一样（42/44）**——
  即"多捞回来的题"里没有一道改变漏答；掉的那 2 道在检索层就没捞到
  （两份报告的逐题明细里，`h23`、`h9` 都标着"gold 没进前 5 → 检索问题"）。
- 离线层拒答准确率只有 12/61 —— 这不是 bug，是**只有向量分 0.55 一道闸**的已知边界
  （`evalset/report-vector-heldout.md` 顶部那段注释早就写明了）。把它当基线用时，
  拒答这一维必须用 online 层的数。
- 耗时：online 层 6 分 14 秒（105 题，`-j 1`，服务端串行），offline 层 3 秒。

## 对账：证明"接线是忠实的"，以及顺带发现的一个问题

`summarize.py --compare` 会把 run_eval 那份日志里的三个数**解析回来**（不手抄），逐项比；
不一致时**打印红字并以非 0 退出**。

- 2026-11-11 实测：同一时刻 promptfoo 与 `run_eval.py`（改后，见文末）**逐项一致**
  （30/44、42/44、12/61）。这一致**只证明接线对**（两边共用 `run_eval.py` 的同一份函数），
  **不证明指标本身是对的** —— 指标的合理性是另一件事（见 DECISIONS.md 待写）。
- 反向验证：把日志里的 `30/44` 改成 `35/44`、`12/61` 改成 `9/61` 两处，
  对账立刻变成 2 项 ❌ 且退出码 1。（这条变异用例当场抓出了"只 print 红字、退出码却是 0"
  的假绿，见坑 6。）
- ⚠️ **顺带发现：`evalset/report-vector-heldout.md` 里的旧数字已经复现不出来了。**
  同一套 105 题，旧报告是 Recall@5 **35/44** / 漏答 2/44 / 拒答 **9/61**，
  今天同一份代码是 **30/44** / 2/44 / **12/61**；`report-rerank-heldout.md` 同样有偏移
  （旧 42/44、漏答 1/44、拒答 39/61）。两份报告的落款是 2026-10-10，
  而此后 `service.py` 有 65 个 commit、语料也变过 ⇒ **旧产物、以及 README 里引它们的那些数，已经过期**。
  成因尚未逐条定位（本次没做 `git bisect`），所以这里只报事实、不做归因。
  ⇒ 建议：把这两份旧报告标注为历史快照；替换与归因交给待办 2/3 一起做。

## 坑（全部实测踩到）

1. **`tests: file://tests.yaml` 要求文件是裸数组。** 文件里再套一层 `tests:` 会被当成
   "一个测试用例"，报 `Test case must contain assert, vars, options, metadata…`。
2. **http provider 返回 `Content-Type: application/json` 时，断言拿到的 `output` 已经是对象**，
   不是字符串。断言里 `JSON.parse(output)` 会炸（`output.slice is not a function`）——
   所以 `assert_case.js` 两种都收。
3. **断言自己的参数在 `context.config` 里**（不是 `context.vars`）。`vars` 是**每题**给的，
   `config` 是**每条断言**给的 —— 一道题有两条不同判据时只能靠 `config`。
4. **沙箱里 `npm install` 会挂在 esbuild 的 postinstall**（它 spawn 子进程，EPERM）。
   加 `--ignore-scripts` 即可（esbuild 的二进制来自 optionalDependency，不靠 postinstall）。
5. **必须 `--no-cache`。** promptfoo 的缓存键不包含"被评测服务的内容"，
   服务改了也可能命中旧结果 —— 那会得到一个与本次改动无关、却看着很合理的分数。
6. **判据红了，退出码也得红。** 第一版 `summarize.py` 只 `print` 了 `[FAIL]`，
   函数照样 `return 0` —— 这种判据接到钩子/CI 上等于不存在。
   （它是被"把对数改坏、期望变红"那条变异用例抓出来的。）
7. **离线层的拒答数不能拿来当"系统拒答能力"**：12/61 是阈值 0.55 的单闸边界，
   产品默认路径是 online 层的 41/61；两层数字不可比，只能并列看。

## 顺带修掉的一个 bug（`evalset/run_eval.py`）

`knowledge_hit()` 原来调 `svc.chat(req)`，而 `chat()` 自 2026-10-13 起是 FastAPI 端点
（签名 `chat(req, request)`）⇒ **整个 run_eval.py 跑不起来**，一跑就 `TypeError`。
改成调 `handle_chat()`（service.py 给"直连管道"的调用方留的入口，不带限流闸门）。
这个 bug 一直躲在 CI 之外：CI 只跑 pytest，没有一条用例真的执行这个脚本 ——
而屏幕上看不出来：README 与 `report-*.md` 里的旧数字还在，
没人重跑就没人发现"这些数已经算不出来了"。

## 还没做（HANDOFF 第 5 节待办 2）

- `eval/gate.py`：配对 McNemar（两次跑的逐题结果配对比较，判"改好了"还是"抖了"）
- `eval/agreement.py`：人机一致性 κ（样本 <20 时拒绝输出 κ）

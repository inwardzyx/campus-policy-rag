# hooks —— 提交前 / 推送前的机械检查

## 装（每个新clone 都要做一次）

```bash
python scripts/install_hooks.py
```

⚠️ `.git/hooks/` **不进仓库**，所以别人 clone 下来默认没有这个钩子。
这是 git 的设计，不是 bug（否则 clone 一个别人的仓库就等于执行他的代码）。
⇒ 能做的是两件事，**现在两件都做了**：
1. **一条命令装上**（幂等，重复跑没事）：`scripts/install_hooks.py`
2. **装在别人一定会看到的地方**：README〈30 秒跑起来〉第 4 步；
   以及跑 pytest 时 `tests/conftest.py` 会打一行 ⚠️ 提醒
   —— ★ **只提醒、不让测试变红**（clone 下来看到 "60 passed" 变成
   "59 passed, 1 failed"，只会让人以为这作品是坏的；"提醒"是能看见，
   "失败"是看起来坏了，两回事）。

### 为什么是 core.hooksPath，而不是往 .git/hooks/ 拷副本

旧办法是 `cp scripts/hooks/pre-commit .git/hooks/` + `chmod +x`。两个问题：

1. **副本会过期**：改了仓库里那份，`.git/hooks/` 里那份还是旧的。
   这不是理论 —— 见下面「两个钩子里都有同一个已踩过的坑」里那次真实复发：
   「修好提交之后 `scripts/hooks/pre-commit` 又被覆盖回旧版，
    差点让'检查通过'变成一句假话」。
   ⇒ `core.hooksPath=scripts/hooks` 让 git **直接跑仓库里那份**：没有第二份副本，
     "副本过期"这一整类问题不存在；改了立刻生效，而且改动进 git 历史、可 review。
2. **可执行位**（这条我一开始判断错了，留在这儿当教训）：
   POSIX 上 git **不会执行没有可执行位的钩子**。用 `cp` 的老办法得对**副本**
   `chmod +x`；换成 `core.hooksPath` 之后，git 执行的是仓库里那份**被跟踪的文件**，
   于是**它的文件模式**（`100755` / `100644`）直接决定 POSIX 上跑不跑 ——
   而这个模式存在 **git 索引**里，不是本地 `chmod`，也**不是** `.gitattributes` 能管的。
   ★ 这个坑在 Windows 上**完全看不出来**（Git for Windows 靠 shebang 跑、不看那个位）。
   本仓库为此在 CI 上真红过一次：**run #27（2026-10-12）** ——
   两个钩子在索引里都是 `100644` ⇒ `install_hooks.py` 返回 3、现场证明看不到
   `[pre-commit]` 横幅 ⇒ **"一条命令装上"这个新功能在 Linux 上其实是坏的**，
   而我在 Windows 上测了三遍都是绿的。
   修法：`git update-index --chmod=+x scripts/hooks/pre-commit scripts/hooks/pre-push`
   （改的是 git 索引里的模式）。`scripts/install_hooks.py` 会把这条当体检项**现算**
   （`git ls-files -s`），不靠人记得。

★ 安装脚本会**自己证明生效**，不是只打印一句 ✔：
真跑一次 `git hook run pre-commit`，要求输出里出现钩子自己的横幅 `[pre-commit]`
—— 只把配置写进去不算数（路径解析、shebang、可执行位、解释器缺一不可）。
★ 它还会顺手体检那两个钩子文件（存在 / 非空 / **不是 CRLF** / 有 shebang /
**索引里是 100755**）：CRLF 会让 shebang 变成 `#!/usr/bin/env bash\r`，
Git Bash 报 bad interpreter，而且报错指不到原因。


## 为什么钩子里不派 cc / DSH

每起一个 `cc` 进程≈**18K token 冷启动**（实测，2026-08-08 那次 13 分钟内换6 种
调用方式，付了 7 次冷启动的钱）。55 个提交就是 100 万 token 上下，
而绝大多数提交（改 README 措辞）根本不需要审稿人。

⇒ **派 agent 留给「改既有代码之前」这个真触发点**（见 `CLAUDE.md` 硬规矩 2）。
钩子只做一件事：**几毫秒能查完、且不留假象**的检查。

## 跑什么

| 钩子 | 调用 | 查什么 |
|---|---|---|
| `pre-commit` | `scripts/check_readme_numbers.py` | README 里写的数字 vs 脚本落盘的真实产物 |
| `pre-push` ① | `scripts/check_push_paths.py` | **即将推上去的文件**里有没有"本不该进仓库"的 |
| `pre-push` ② | `scripts/check_artifact_sanity.py` | 产物**本身是不是像跑出来的**（坏环境下跑的结果会覆盖好产物） |
| `pre-push` ③ | `scripts/check_token_report.py` | token 报告里的每个数 vs 实测 JSON（**逐格**） |

## ★ pre-push ③：token 报告对账（2026-10-12）

写 `evalset/report-token-cost.md` 时我手工核对了 27 项，
**却在正文里自己抓到 3 处算错**（缓存命中率 67.1%→实际 66.5%、
output 差值 46→47、"52 次调用"→53）。

⇒ "写完自己读一遍"这种核对**读的是自己刚写的句子，不是数据**。
所以凡是"正文里出现了由数据算出来的数"，就得有一条自动判据盯着。

它的变异测试是 `scripts/mutate_check_token.py`（5 条）。

⚠️ **两个必须记住的边界**：

1. **缺数据时不能"当成通过"。** 这个检查要读 `agent/_token_cost.json`，
   而它按 `.gitignore` **不进仓库** ⇒ 别人 clone 下来没有。
   钩子的处理是：**明确说缺、并给出怎么补**，而不是静默跳过 ——
   静默跳过等于这道防线在别人机器上是假的。
2. **注入失败必须算失败。** `mutate_check_token.py` 第一版里
   "变异原文没找到"只打一行警告、退出码仍是 0 ⇒ 实测踩到过：
   成本列那条把**上一张表**的列结构记错了，注入失败被跳过，
   而全套变异测试全绿 ⇒ 那条判据实际上从没被验证过。

⚠️ **两个钩子里都有同一个已踩过的坑**：`[ -x "$PY" ]` 在 Git Bash 下对
`D:/...` 这种 Windows 形式路径判不出可执行 ⇒ 静默 false ⇒ 解释器变空 ⇒
检查被整段跳过而钩子照样 exit 0。⇒ 两处都改成 `[ -f ]` + 验活 +
**拿不到解释器就报错退出**。这个 bug **真实复发过一次**：
修好提交之后 `scripts/hooks/pre-commit` 又被覆盖回旧版，
差点让"检查通过"变成一句假话。**改这两个钩子前先读一眼上面这段。**

## ★ pre-push ②：产物合理性检查（2026-10-11）

`evalset/report-vector.md` 曾被一次**坏环境下跑的**评测覆盖成
Recall@5 **0/15**、两个不同题集的极值撞成同一个常数 **0.4240**，
而 `run_eval.py` **自己不报错**（它忠实报告了它算出来的东西）。
它又是别的文档引用的基准 —— 被悄悄改掉后 README 的数字跟着一起变错。

⇒ 检查只拦"**明显不是跑出来的**"，**刻意不设性能下界**
（那会把真实退化也拦下来，而真实退化是要允许发生并被记录的）。

它的变异测试是 `scripts/mutate_check_artifact.py`（**14 条**，含负向）。
★ 其中一条变异改的是**检查器自己的清单**而不是产物 ——
因为"漏登记"意味着那份产物**永远不会被检查**，而这正是上一版真实的洞
（`report-rerank-main-norw.md` 从未被检查过）。

## ★ pre-push：为什么会有它（2026-10-11，一起真实事故）

提交 `7252a69`（2026-09-24 **10:53**）把 `CLAUDE.md` + 4 个 `cc_*.md`
**推上了公开仓库**。次日 **16:14** 主干被 `git filter-branch` 重写、
16:37 force-push —— 材料在远端 main 上活了 **约 29.7 小时**。

重写之后这些文件不在当前树里了（`GET /contents/CLAUDE.md` → 404），**但是**：

- 那个提交**仍按 SHA 可达**（不被任何分支指向，却照样能取）；
- `https://github.com/<owner>/<repo>/commit/7252a69.patch`
  一个普通 URL 就返回 **200 / 20788 字节**，5 个文件的**完整正文**都在里面；
- 链接入口就在**公开的 Actions 页面**上（run #1）。

⇒ 教训一句话：**push 是单向门。**
  · `.gitignore` 只挡"以后"，对"历史上进过库的东西"一个字都不说；
  · **删除本地分支/改本地历史对远端毫无帮助** —— 远端对象的存在性
    只由「是否 push 过」决定，此后一切本地操作与之无关。

（在同一件事上，DSH 先误判"从未外泄"、又误以为删本地备份分支有用，
 两次都是"拿局部为真的检查去支撑全局结论"。cc 的复核把时间线从
 `.git/logs/` 的 push 日志里重建出来，才算把这条讲清楚。）

## P1 / P2：两道防线，不能互相替代

- **P1（推前，本钩子）**：拦"即将推上去的可疑路径"。防呆。
  ★ 已用**真实 push** 验过：把 `7252a69` 推到一个本地裸仓库 → 钩子列出 5 个文件并拒绝。
    也就是说**当年若有它，那次事故不会发生**。
- **P2（推后审计，未实现）**：枚举**公开面上印出来的 SHA**（Actions runs 的 `head_sha`）
  → 判断它是否能从 main 到达（不能 ⇒ 孤儿）→ 取该 commit 的 `files[]` 与同一张模式表求交。
  本案可查（run #1 就印着那个 SHA）。
- **做不到的那一半**：**没有 API 能枚举远端的不可达对象**。所以"从未在任何公开面
  出现过的孤儿 SHA"查不到 —— 对人也一样查不到。别为此写一个假检查器。

## 已知边界

- **本 clone 是浅克隆**（`.git/shallow` 定格在初始提交，2026-09-22），
  所以本地任何历史审计只覆盖那之后。
- `--not --remotes` 在"往另一个 remote 推新分支"时可能少算（多 remote 场景），
  详见 `check_push_paths.py` 里的注释。

## ★★★ 远端那道防线：实测是空的（2026-10-11）

上面 P1/P2 讲的全是**本地钩子**。本节记的是**远端**（GitHub）那道，
以及一次把它测穿了的实验。

### 实验：故意让容器 job 变红

开PR #1（分支 `tmp-verify-gate-can-fail`，已关闭），只改`service.py` 一行：
把 `/health` 的 `chunks` 硬编码成 `0`（变异选在这里，是因为它躲得过
前面所有步骤 —— `test_guard.py` 不碰 `/health`，`/health` 仍返回 `status=ok`，
所以只有第⑤ 步那条`grep -qE '"chunks":[1-9][0-9]+'` 抓得到它）。

结果：

| job | 结果 | 符合预期 |
|---|---|---|
| 快 job · 加载器 |✅ 绿 9s | ✅不碰 `/health` |
| 慢 job · 把关+门禁 | ✅ 绿 1m | ✅ 那三个测试文件都不碰 `/health` |
| **容器 job** | ❌ **红**（第 ⑤ 步 `exit 1`） | ★ **实验成功** |

⇒ 至此三段都验证过了：

| 说法 | 状态 |
|---|---|
| 断言会响 | ✅ 已证（9 条变异含反例+ 这次远程实验） |
| job 会红 ⇒ workflow 会红 | ✅ 已证（PR 上显示 `1 failing check`） |
| **workflow 红 ⇒ 推不上去** | ❌ **已证伪** |

### ★ 证伪的那一条：红灯**锁不住** Merge

实验当时的 PR 页面：容器 job 红着、`1 failing check` 挂着，
而 **`Merge pull request` 按钮依然可点**，旁边还写着
"No conflicts with base branch / Merging can be performed automatically"。

⇒ 查 `Settings → Branches`，答案是
**"Classic branch protections have not been configured"** —— **一条规则都没有**。

**GitHub 只有在仓库设置里把某个 check 标成 required（必需检查）时，
红灯才真正锁住合并。** `pull_request` 触发的检查默认**不是** required。

⇒ 所以这个仓库在**补规则之前**的真实状态是：

- **本地两道（钩子）**：真的挡（已用真实 push 验过）
- **远端那道（CI）**：⚠️ **只会喊，不会拦** —— 它能告诉你"红了"，
  但你（和任何协作者）该合还是能合

★ **"CI 绿了"和"CI 拦得住"是两件事**，今天第一次测后者，就发现不成立。
这比"CI 能不能变红"重要一个量级：前者的坏后果是"没发现问题"，
而后者是**"问题被发现了，防线却没挡住"** —— 比没有 CI 更危险，因为它给人虚假的安全感。

### 现在的状态：远端补了 rule，但**故意不把 CI 变成门禁**（2026-10-11）

已在 `Settings → Branches` 建了一条 Branch rule（新版 rulesets），覆盖 `main`：

| 选项 | 状态 | 作用 |
|---|---|---|
| **Block force pushes** | ✅ 已开 | 挡住"手滑 force push 改写历史" |
| **Restrict deletions** | ✅ 已开 | 挡住"手滑删掉 main" |
| Require status checks to pass | ⬜ **故意不开** | ★ 开它会**禁掉直接 push 到 main**（见下） |
| 其余 9 项 | ⬜ 不开 | 与本仓库要解决的问题无关 |

⇒ **所以那道缺口仍然在**：CI 红灯**依然不拦合并**。
这是**知情的取舍**，不是漏配 —— 因为把 CI 变成门禁的代价
（每次推送都得走 PR + 等几分钟）大于它当下解决的问题（单人项目，一天推好几次）。
⇒ 什么时候该翻成 B：**当"CI 红了却被人合进去"真的发生过一次**的时候。
在那之前，靠下面两条本地防线 + PR 页面上看得见的红灯。

★ 顺带一句：本仓库的本地钩子（`pre-commit` / `pre-push`）
**是唯一真正会挡的两道**，而它们**不进仓库**（`.git/hooks/` 被 gitignore），
换 clone 就没了 —— 别人（或未来的你）clone 下来默认一道都没有。
这才是这道防线真正的薄弱点，比远端那条更值得补。

### 要补的话，在 Settings → Branches 加一条 rule

⚠️ **先分清两套系统**（2026-10-11 实测页面）：
- **Classic branch protections** —— 旧版，页面会明说 "have not been configured"
- **Branch rules（rulesets）** —— ★ 新版，现在的正路，选项更多也更清楚

★ **先看清楚 `Require status checks to pass` 的代价**——
GitHub 自己在那栏底下写着：

> "commits must first be pushed to another ref where the checks pass"

⇒ 它不只是"合并前要绿"，它**禁掉「直接 push 到 main」**。
本仓库是单人项目、每天推好几次，一旦开这一项，工作流就变成
"每改一行都得开 PR"，而慢 job + 容器 job 合计要好几分钟。

⇒ 所以这**不是"哪个开关更严"的选择，是"要不要改工作流"的选择**。两条都诚实：

| 选项 | 换来什么 | 代价 |
|---|---|---|
| **A. 只开防误操作的项**（见下） | 挡住"手滑改历史 / 手滑删主干" | ⚠️ **CI 仍然是"只会喊不会拦"**。等于接受"红灯是提醒，不是门禁" |
| **B. 开 required checks**（勾容器 job / 慢 job） | ★ 红灯真正锁住合并，CI 升级成门禁 | **每次推送都得走 PR**，CI 跑完（数分钟）才推得进去 |

#### 新版 UI 上，A 方案具体勾哪几项（2026-10-11 实际采用）

| 选项 | 开不开 | 为什么 |
|---|---|---|
| **Block force pushes** | ✅ **开** | ★ 最重要的一条。本仓库有 `filter-branch` 的前科 —— 2026-09-24 那次改写历史虽然目的正当（清掉误推的文件），但**手法本身就危险**，而它能发生正是因为没有规则拦 |
| **Restrict deletions** | ✅ **开** | 不让删掉 main。本仓库已经因为那次事故做过一次"远端删除是单向门"的复盘 |
| Restrict creations / Restrict updates | ❌ 不开 | 这两项是"只允许有 bypass 权限的人创建/更新"，= 变相强制走 PR，同 B 的代价 |
| Require a pull request before merging | ❌ 不开 | B 的加强版，单人项目纯负担 |
| Require linear history / Require signed commits | ❌ 不开 | 与本仓库要解决的问题无关；signed commits 还要求配 GPG/SSH 签名 |
| Require deployments / code scanning / code quality / coverage / Copilot review | ❌ 不开 | 这些功能本仓库没启用，勾了只会卡住自己 |
| **Require status checks to pass** | ❌ **不开**（选 A 时） | 见上，开它=禁掉直推 main |

⚠️ **新版里没有 "Do not allow bypassing" 这一条**（那是旧版 classic 的说法）。
新版靠 **bypass list** 控制：创建 rule 时那个 "Bypass list" **留空**
⇒ 没有任何人能绕过这条 rule（含管理员）。
⚠️ 别往 bypass list 里加自己 —— 一加就等于给自己开后门，rule 立刻形同虚设。

⚠️ **Block force pushes 开启后的副作用**（不是 bug，是设计）：
万一真需要改写 main 历史（像 2026-09-24 那次清理泄露文件），会撞上这条规则。
届时正确做法是**临时在 Settings 里禁用这条 rule**，改完再启用 ——
**不要用 `--force` 硬冲，也不要为了绕过它把规则删掉**。

## ⚠️ 这套东西自己也被审过

`check_readme_numbers.py` 的**第一版跑出来是"✓ 通过"**，
但变异测试（`scripts/mutate_check_numbers.py`）把 README 的 `0.5968`改成 `0.9999` 后，
它**照样报通过** —— 因为第一版只查产物侧、没查 README 侧。

⇒ 这和loader 清洗账塌成 0 是**同一个模式**：探头只照了一面，
却把"没发现问题"读成"没问题"。

现在两段都查，且加了**探头自检**（一条 README 侧断言都没执行到 ⇒ 报"探头失灵"）。

```bash
# 想确认这套检查自己还有效（改了脚本之后必跑）
D:/Python-project/.venv/Scripts/python.exe scripts/mutate_check_numbers.py
```

当前状态：**5 个变异全部检出，恢复后仍报绿。**

## 绕过

`git commit --no-verify` 能跳过（git 原生行为）。

⚠️ 但规矩是：**用了 `--no-verify`，commit 信息里就不能写"检查通过了"。**
绕过检查本身不是漏洞，**假装检查过了**才是。
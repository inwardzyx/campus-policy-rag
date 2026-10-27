
# 部署到公网：路线选择 + 上线前必查清单 + 回放式演示

> **跑评测前先调高 `RATE_PER_MIN`**（默认 20 次/分，连续跑会被限流）。client 现在遇 429 会退避重试，仍失败则**中断并报错**——不会再产出"0/15"的假报告。详见 `experiments/eval-latency-and-limits.md`。


> ★ 为什么要有这一页：这个项目 2026-10-18 之前**从没部署过**（简历 §1 明写"未部署公网"）。
> 而"项目上线 + 给得出网址"是可信度杠杆最大的一步 —— 但**死链比没有链接更糟**，
> 所以这一页的重点不是"怎么起"，是**"什么时候该印进简历、什么时候不能"**。
>
> 读完应该能回答三个问题：① 我这套东西能放进哪类服务（多数不能）；② 上线前必须验什么；
> ③ 如果不想承担"常驻 + 密钥"的成本和风险，有没有**零风险的替代**（有：回放式演示）。

## 三条路线对照

| 路线 | 成本 | 能印进简历吗 | 关键限制 |
|---|---|---|---|
| **① Cloudflare Tunnel**（从本机穿出去） | **¥0** | ❌ **不能** —— 电脑关机/睡眠就死链，重启还换 URL | 需要本机常开 |
| **② 国内轻量服务器**（阿里云 / 腾讯云，2G 内存起） | **~¥30-40/月** | ✅ **能** —— 固定 IP，随时可点 | 要装 Docker；**IP:端口不需要备案**，绑域名才要 |
| **③ HuggingFace Spaces** | ¥0 | ❌ **不能** —— HF 整站在国内不可访问，面试官点不开 | 免费但对你没用 |

**结论**：
- 想**这两天就有一个能点的地址**（约面试前开一下）⇒ ①
- 想**印在简历上、HR 半夜点也活着** ⇒ ②
- ③ 直接跳过。

## 上线前必查清单（**逐条都要过，缺一条就别公开**）

> ★ 2026-10-26 起，其中能自动化的几项（key / kb_data 可写 / 审计 / 租户表 / 模型缓存）
> 已经做成一条命令：`python scripts/preflight.py`（容器里 `docker compose run --rm preflight`）。
> 它**先查再起**：这些项坏了的表现是「服务起来了、`/chat` 全 500」，事后排查很贵。

### 1. 钱包有没有兜住

```powershell
# 演示期建议把全站日额度压到 20 万 token（约 ¥0.5-1.6/天上限）
$env:GLOBAL_DAILY_TOKENS="200000"
$env:GLOBAL_DAILY_REQ_QUOTA="500"
```

起服务后**必须亲自验**：

```powershell
curl http://127.0.0.1:8000/limits
```

返回体是 `{"usage": <你自己的用量>, "limits": {...}}`。**四个故障计数与存储后端都在 `limits` 里**
（`denied_rate` / `denied_quota` / `store_errors` / `usage_missing` + `store` / `fail_mode`），
同一份 `stats()` 也挂在 `/health` 的 `limits` 字段下。
★ **`store` 必须是 `redis`**：如果是 `memory`，多进程下每个进程各算各的，额度会被放大 N 倍
（服务启动日志里有专门的 WARNING 提示这件事）。

### 2. 密钥有没有漏

```powershell
git ls-files | Select-String -Pattern '^\.env$'     # 必须为空
```

`.dockerignore` 里排除 `.env`（已有）；`/health` 只回文件名不回完整路径（已有）。
★ **公开演示不要把 `DEEPSEEK_API_KEY` 塞进镜像**，用运行时环境变量注入。

### 3. 有没有把不该公开的东西带出去

- `/guard-report` **会公开 23 条被拦材料的内容与理由** —— 这是**校规原文**，不是个人隐私，
  但如果语料里混进了真实姓名/学号，先删（`docs/inbox/` 的人工审核环节就是干这个的）。
- 会话层持有最近 5 轮问题原文（TTL 30 分钟），**不进日志、不落库** —— 公开后依然要守这条。

### 4. 服务真的起来了

```powershell
curl http://127.0.0.1:8000/health
```

跑完会看到 `status:"ok"`、`chunks:263`、`rejected:23`、`load_errors:0`。
★ `chunks` 不是 263 就别对外 —— 说明有文件静默没入库，答出来的东西是残缺的。

---

## 路线①：Cloudflare Tunnel（今天就能做完，¥0）

**前提**：不需要 Docker，直接跑 Python。

```powershell
# 1) 起服务（另开一个窗口，或作为后台任务）
cd D:\_review_rag-cs
$env:HF_ENDPOINT="https://hf-mirror.com"
D:\Python-project\.venv\Scripts\python.exe service.py

# 2) 装 cloudflared（一次性）
winget install --id Cloudflare.cloudflared

# 3) 开隧道（会打印一个 https://xxx.trycloudflare.com 的地址）
cloudflared tunnel --url http://127.0.0.1:8000
```

**验证**：把打印出来的 `https://xxx.trycloudflare.com/health` 丢到手机热点下打开
（换网络才能证明真的从公网可达，本机访问可能走本地回环）。

⚠️ **这个地址每次重启都变**，所以：
- **不要印进简历**；
- 用法是"约到面试了，我开一下，把地址发你"。
- 想让地址固定 ⇒ 需要一个域名 + `cloudflared tunnel create`（命名隧道），或者直接走路线②。

---

## 路线②：国内轻量服务器（能印进简历的那条）

1. 买：阿里云/腾讯云"轻量应用服务器"，**2G 内存起**（onnxruntime + 模型约 0.5-0.8G），
   系统选 Ubuntu 22.04。**不绑域名的话不需要备案**，用 `http://IP:8000` 访问。
2. 装 Docker：
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
3. 拉代码 + 配环境变量（**不要把 .env 提交进仓库**）：
   ```bash
   git clone https://github.com/inwardzyx/campus-policy-rag.git
   cd campus-policy-rag
   cp .env.example .env      # 然后编辑：填 DEEPSEEK_API_KEY，压小额度
   ```
4. 起：
   ```bash
   docker compose up -d --build
   ```
5. 验证（**在公网上验，不要只在本机**）：
   ```bash
   curl http://<你的IP>:8000/health
   ```
6. 防火墙/安全组放行 8000。

⚠️ 首次启动要下 92MB 模型（`HF_ENDPOINT` 已默认走 hf-mirror），**约 1-3 分钟**；
这段时间 `/health` 会回 `status:"loading"`，属正常。

---

## 四类"看起来能部署"的服务，实际分别能干什么

| 类别 | 能跑这个服务吗 | 常驻？ | 国内可达 | 成本 | 正确用途 |
|---|---|---|---|---|---|
| **内网穿透**（cloudflared / frp / 花生壳） | ✅ 整站（服务仍在你自己机器上跑） | ❌ **本机关机/睡眠即死链** | ✅ | 0 | **面试前临时开**；不能印简历 |
| **云端编程环境**（Codespaces / Cloud Studio / Replit） | ✅ 能（8G 内存够） | ❌ **闲置约 30 分钟自动停** + 月度小时数上限 | ⚠️ 不稳 | 0（免费额度） | 自己远程开发；**不是给 HR 的链接** |
| **网站托管**（Vercel / Netlify / GitHub Pages / CloudBase 静态） | ❌ **纯静态，跑不了 FastAPI** | ✅ | ⚠️ 视服务商 | 0 | 放 **case study 页 / 回放演示** |
| **对象存储**（OSS / COS / S3） | ❌ 同上 | ✅ | ✅ | 极低 | 放 GIF、截图、预计算结果 |

### ★ 为什么"网站托管"连静态 API 都做不了（实测，非推断）

腾讯云 CloudBase 官方文档〈默认域名访问限制及中间页〉写明：

> 当访客使用浏览器通过「默认域名」（`*.tcloudbaseapp.com`、`*.run.tcloudbase.com` 等）访问时，
> 浏览器直接访问的请求会展示"访问提示中间页"；**对其他请求会添加 `Content-Disposition: attachment`
> 响应头（浏览器直接打开会提示下载）**。移除中间页的唯一合规途径是**配置自定义域名**，
> 而自定义域名**必须完成 ICP 备案**。

⇒ 对 JSON 接口来说这是**结构性不可用**：`/chat` 返回的 JSON 会被浏览器当成文件下载。
**不是"难看一点"，是品类不对。**（来源：https://docs.cloudbase.net/service/alias）

### 为什么只有轻量服务器能印进简历

这个服务的三个属性决定了它挑环境：
1. **长驻进程**（不是请求-响应即销毁的函数）—— 排除 serverless / 静态托管；
2. **约 500-800MB 模型常驻内存**—— 排除小内存免费层，冷启动还要重下 92MB；
3. **一个会花钱的 key**—— 环境必须能安全注入密钥。

⇒ **"闲时自动停"的环境都会让链接变死链**（死链比没有链接更糟）；
**"纯静态托管"根本跑不起来**。所以能同时满足"常驻 + 国内可达 + 可注入密钥"的，
只剩**一台常开的机器**（国内轻量，`http://IP:8000`；**IP 直连不需要备案**，绑域名才要）。

---

## 回放式演示：零风险、永远活着的替代方案

**做法**：把一批题的**真实响应**（答案原文 + 引的条款 + 精排分 + 耗时）预先跑出来存成 JSON，
做一个**纯静态页**呈现：点问题 → 显示当时真实答案与分数。

**必须写明「录制回放，非实时」** —— 这不是"假装在线"，而是把"可验证"做到不依赖服务器。

| | 实时部署 | 回放式演示 |
|---|---|---|
| 链接可靠性 | 依赖服务器/本机 | **静态托管，永远活着** |
| 成本 | ¥0~40/月 | **0** |
| 被刷爆风险 | 有（已在额度里兜住） | **无** |
| 密钥暴露 | 有（要注入） | **无**（不跑服务） |
| 实时性 | 有 | **没有（必须明说）** |
| 技术面试官怎么看 | "能跑" | "知道自己能给什么、不能给什么" |

★ **两种都要的话**：静态页做"点击即看"，页顶放一句"实时交互版可另约（我会开隧道）"——
把可靠性交给静态页，把"真能跑"留给面试当天。

---

## 印进简历的说法（等真的稳定跑起来再写）

现在简历 §1 写的是：

> **一个真的服务，不是脚本**（个人自用原型：无真实用户，也未部署公网 —— 这是设计取舍，不是遗漏）

部署稳定后改成：

> **已部署可访问**：`http://<地址>`（`/search` 纯检索 + `/guard-report` 入库把关可匿名体验；
> `/chat` 有全站日额度上限，防刷爆 key）

★ **改之前先自问三句**：
1. 我半夜点开它还活着吗？
2. 被刷满额度时，它会不会安静地拒绝而不是报错？（会：429 + `Retry-After`）
3. 有没有任何东西是我**不希望被看到**的？（`/guard-report` 的语料原文先自查）

---

# 附：私有化单机部署（compose，2026-10-26）

> 上面那节讲的是"要不要公开、怎么公开"；这一节讲**内网私有化**：单机 compose、少量租户、
> 数据不出内网。适用形态与不适用形态见文末「已知边界」。

## A1. 前置条件

| 项 | 要求 | 说明 |
|---|---|---|
| Docker | 24+ / compose v2 | 老版本把 `docker compose` 换成 `docker-compose` |
| 机器 | 4 核 / 8 GB / 5 GB 磁盘 | 无 GPU 也能跑（embedding 在 CPU，模型约 91 MB） |
| 出网/离线 | 首次拉镜像 + 下模型 | 离线：先 `docker save` 镜像 + 预热 `model-cache` 卷 |
| 模型 key | `DEEPSEEK_API_KEY` | 不配则 `/chat` 全 500（`preflight` 会拦） |

## A2. 五步上线

```bash
cp .env.example .env                       # 至少填 DEEPSEEK_API_KEY / KB_ADMIN_TOKEN
docker compose run --rm preflight          # ① 自检（不占端口、不写数据）
docker compose up -d --build               # ② 起服务（redis → rag）
curl -s http://127.0.0.1:8000/health       # ③ 等就绪（首次会先返回 loading）
# ④ 浏览器打开 http://<机器IP>:8000/console ，顶部填 KB_ADMIN_TOKEN
curl -s -X POST http://127.0.0.1:8000/chat -H 'Content-Type: application/json' \
  -d '{"question":"一线城市出差住宿费上限是多少？","namespace":"demo"}'
```

## A3. 关键新变量（S2 之后）

| 变量 | 默认 | 说明 |
|---|---|---|
| `KB_ADMIN_TOKEN` | 空 | 管理接口/控制台凭证；**不设只允许容器内回环** |
| `MULTI_TENANT` | `0` | `1` 时 `/chat`、`/search`、写接口都要 `X-Api-Key` |
| `TENANTS_PATH` | `/app/kb_data/tenants.json` | 租户表（只存 key 的 sha256） |
| `AUDIT_LOG_PATH` | `/app/kb_data/audit.jsonl` | 审计落盘位置 |

## A4. 数据持久化（**最容易踩的一处**）

语料、审计、租户表都在 `/app/kb_data`，已挂到 `kb-data` 卷。**不挂卷的话 `down` 一次全丢**。

```bash
docker run --rm -v rag-cs_kb-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/kb-data-$(date +%F).tgz -C /data .
```

审计保留期（先 `--dry-run`）：

```bash
docker compose exec rag python scripts/prune_audit.py --days 180 --dry-run
```

## A5. 开通租户

```bash
docker compose exec rag python scripts/create_tenant.py \
  --id hr --name "人力资源部" --namespaces hr,hr/policy     # key 只打印一次
# .env 里设 MULTI_TENANT=1 后 docker compose up -d
```

## A6. 扫描件（可选，含安全提醒）

默认 `pypdf` 只抽文本层；扫描件走 `PDF_BACKEND=docling`（`Dockerfile.docling`）。
⚠️ 该后端是"容器内再 `docker run`"，需要挂 `/var/run/docker.sock`（≈ 给容器宿主 root）。
很多企业安全策略不接受 —— 这类环境请让安全团队评估，或把 OCR 后的 Markdown 再上传
（`POST /kb/{ns}/documents` 支持 `.md`）。

## A7. 资源与容量（实测 / 外部参考分开标）

| 指标 | 数值 | 来源 |
|---|---|---|
| 每问 LLM 调用 | **3 次**（改写 + 精排 + 生成） | 本仓库实测 `usage.n_calls` |
| p95 延迟（20 题） | **≈ 3.8 s** | `evalset/report-scenario-policy.json` |
| embedding 缓存 | 约 **91 MB** | 本仓库实测 |
| 14B INT4 显存 / 单卡并发 6–7 | 约 10 GB | **外部从业者复盘**，非本仓库实测 |

## A8. 已知边界

1. 单机编排：无副本、无 TLS、无负载均衡（公网见本文上半部分）；
2. Redis 无密码，只在 compose 内网可达；
3. 审计是单文件 JSONL：多副本共享同一文件不安全；
4. **没有计费**：租户配额是"防刷"级别；
5. 配额数值不分租户自定义（所有租户共用 `DAILY_REQ_QUOTA`）。

---

# 附：路线 A（Cloudflare Tunnel，15 分钟）与上线后 runbook

## A0. 三步起一个可点的 URL

```powershell
# ① 装 cloudflared（Windows，winget 一条命令；已装可跳过）
winget install --id Cloudflare.cloudflared -e

# ② 起服务（另开窗口；额度压低，护钱包）
cd D:\_review_rag-cs
$env:DEEPSEEK_API_KEY="你的 key"; $env:KB_ADMIN_TOKEN="随机 32 位"
$env:GLOBAL_DAILY_TOKENS="200000"; $env:GLOBAL_DAILY_REQ_QUOTA="500"
D:\Python-project\.venv\Scripts\python.exe -m uvicorn service:app --port 8000

# ③ 开隧道（第三个窗口），记下它打印的 https://xxx.trycloudflare.com
cloudflared tunnel --url http://127.0.0.1:8000
```

⚠️ **开隧道前必须先设 `KB_ADMIN_TOKEN`**：cloudflared 转发到 `127.0.0.1`，服务端看到的来源是回环，没配 token 时 `/admin/*`、上传/删除会变成公网可访问。
代码已加防线（无 token 且带隧道/反代头 → 403），但**公开演示请一律配 token**。

然后**验收**（把 URL 换成隧道地址）：

```powershell
D:\Python-project\.venv\Scripts\python.exe scripts/deploy_smoke.py `
  --base-url https://xxx.trycloudflare.com --namespace demo `
  --seed evalset/fixtures/policy_corpus --admin-token $env:KB_ADMIN_TOKEN `
  --out experiments/deploy-smoke.json --md experiments/deploy-smoke.md
```

6/6 全绿才算上线成功；`experiments/deploy-smoke.md` 就是**部署证据**（可直接进简历附件/README）。

⚠️ 免费 Tunnel 的 URL **每次重启都会变**：适合"面试前打开"，**别印进简历**。要固定链接走路线 B。

## A9. 上线后 runbook

| 场景 | 看哪里 / 怎么办 |
|---|---|
| 服务是否活着 | `GET /health`：`status=ok` 且有 `chunks`；`loading` = 模型还在下 |
| 变慢/出错 | `GET /limits` 看 `denied_rate`/`store`；`GET /admin/audit?limit=20` 看最近决策 |
| 额度用尽 | 返回 429（带 `Retry-After`）或 503（`LIMIT_FAIL_MODE=closed` + 存储挂）→ 调 `.env` 里的额度并重启 |
| 重启 | `docker compose restart rag`（compose）或 Ctrl+C 后重跑 uvicorn |
| 备份 | 卷 `kb-data`（语料/审计/租户表）；命令见本文 §A4 |
| 回滚检索行为 | `.env` 设 `INDEX_TEXT_MODE=text` 后重启（回到"只索引正文"） |
| 审计导出 | `GET /admin/audit?tenant=&session_id=&limit=1000`（带 `x-kb-admin-token`） |
| 答案突然都拒答 | 先看 `/limits` 是不是被限流、`/health` 的 `load_errors` 是否非 0、`chunks` 是否变少 |

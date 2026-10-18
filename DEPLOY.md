# 部署到公网：三条路线 + 上线前必查清单

> ★ 为什么要有这一页：这个项目 2026-10-18 之前**从没部署过**（简历 §1 明写"未部署公网"）。
> 而"项目上线 + 给得出网址"是可信度杠杆最大的一步 —— 但**死链比没有链接更糟**，
> 所以这一页的重点不是"怎么起"，是**"什么时候该印进简历、什么时候不能"**。

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
   git clone https://github.com/inwardzyx/rag-customer-service.git
   cd rag-customer-service
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

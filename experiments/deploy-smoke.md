> ⚠️ 本次用的是**临时 Tunnel**（URL 会随重启失效），只用于证明公网链路；要固定链接请走 `DEPLOY.md` 的路线 B。管理接口那项因未提供 token 被跳过。

# 部署验收 · https://pages-dean-textiles-disciplinary.trycloudflare.com（namespace=）

> 时间：2026-10-26 18:27:13　通过 **6/6**

| 检查 | 结果 | 详情 |
|---|---|---|
| health | ✓ | status=ok chunks=263 |
| chat_in_scope | ✓ | hit=True sources=[('学生纪律处分管理规定.md', '第二章-第四条'), ('学生纪律处分管理规定.md', '第四章-第二十六条'), ('学生纪律处分管理规定.md', '第一章-第三条')] |
| chat_out_of_scope_refused | ✓ | hit=False answer='知识库里没有能回答这个问题的资料，我不编。' |
| console | ✓ | http=200 bytes=8788 |
| limits | ✓ | store=memory daily_req=None |
| admin_audit | ✓ | 跳过（未提供 --admin-token） |

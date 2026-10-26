# 部署验收 · https://post-roles-quarters-biol.trycloudflare.com（namespace=）

> 时间：2026-10-26 18:34:24　通过 **6/6**

| 检查 | 结果 | 详情 |
|---|---|---|
| health | ✓ | status=ok chunks=263 |
| chat_in_scope | ✓ | hit=True sources=[('学生纪律处分管理规定.md', '第二章-第四条'), ('学生纪律处分管理规定.md', '第四章-第二十六条'), ('学生纪律处分管理规定.md', '第一章-第三条')] |
| chat_out_of_scope_refused | ✓ | hit=False answer='知识库里没有能回答这个问题的资料，我不编。' |
| console | ✓ | http=200 bytes=8788 |
| limits | ✓ | store=memory daily_req=None |
| admin_audit | ✓ | 跳过（未提供 --admin-token） |

## 公网安全检查（手工探针）

| 探针 | 结果 | 说明 |
|---|---|---|
| `GET /admin/tenants`（无 token，走公网） | **403** | 管理接口未对公网开放（服务端已配 `KB_ADMIN_TOKEN`）|
| `GET /console` | 200 且含「控制台」 | 静态壳可访问，数据仍需 token |

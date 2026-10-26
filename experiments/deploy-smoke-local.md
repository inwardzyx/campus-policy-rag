# 部署验收 · http://127.0.0.1:8040（namespace=demo）

> 时间：2026-10-26 18:08:54　通过 **6/6**

| 检查 | 结果 | 详情 |
|---|---|---|
| health | ✓ | status=ok chunks=310 |
| chat_in_scope | ✓ | hit=True sources=[('差旅与费用报销管理办法（2026修订版）.md', '第八条（住宿标准）'), ('差旅与费用报销管理办法（2024版）.md', '第四条（住宿标准）')] |
| chat_out_of_scope_refused | ✓ | hit=False answer='知识库里没有能回答这个问题的资料，我不编。' |
| console | ✓ | http=200 bytes=8788 |
| limits | ✓ | store=memory daily_req=None |
| admin_audit | ✓ | http=200 n=2 |

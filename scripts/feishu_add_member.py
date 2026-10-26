# -*- coding: utf-8 -*-
"""feishu_add_member.py —— 用 API 把应用（或用户/群）加为知识空间成员。

## 为什么需要它
飞书知识库的「成员设置」页在部分版本里**只有用户/部门/群，没有"应用"**，
而 `tenant_access_token` 读取 wiki 节点要求"tenant 是空间成员"（否则报
`131006 wiki space permission denied, tenant needs read permission`）。
这个脚本直接调成员接口，把应用自己加进去 —— 绕开 UI 的缺口。

## 需要什么
- scope：`wiki:wiki`（**写**，不是 readonly）；加完**必须发版**才生效；
- 应用必须是该空间的管理员/创建者身份不可用，所以：**用空间管理员账号**跑不行 ——
  本脚本用的是 tenant_access_token（应用身份），所以应用需要被允许管理该空间；
  若接口返回 403/无权限，说明你们租户不允许应用改空间成员，只能让管理员在管理后台开。

## 用法
```powershell
# 看现有成员（也用来判断这个租户支不支持 app 类型）
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/feishu_add_member.py `
    --space-id 7700832345401593092 --list

# 把应用自己加为可阅读成员（member_id 默认取 FEISHU_APP_ID）
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/feishu_add_member.py `
    --space-id 7700832345401593092 --add --role reader
```
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:                                              # 读仓库根的 .env（没有就跳过）
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:                                 # noqa: BLE001
    pass

from kb.feishu import FeishuClient, FeishuError, explain_error  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="知识空间成员：查看 / 添加")
    ap.add_argument("--space-id", required=True)
    ap.add_argument("--app-id", default=os.environ.get("FEISHU_APP_ID", ""))
    ap.add_argument("--app-secret", default=os.environ.get("FEISHU_APP_SECRET", ""))
    ap.add_argument("--base-url", default="https://open.feishu.cn/open-apis")
    ap.add_argument("--list", action="store_true", help="列出当前成员")
    ap.add_argument("--add", action="store_true", help="添加成员")
    # ★ 不写死 choices：不同版本对"应用"的 member_type 叫法不一致（app/openapp/appid…），
    #   写死只会让人在报错里反复试；宁可放开，让服务端告诉我们该用什么。
    ap.add_argument("--member-type", default="appid",
                    help="appid（应用，实测有效）/ openid（用户）/ openchat（群）/ 其它服务端支持的值")
    ap.add_argument("--member-id", default="", help="默认取 FEISHU_APP_ID（把应用自己加进去）")
    ap.add_argument("--role", default="member", help="member（可阅读，实测有效）/ admin；以服务端为准")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.app_id or not args.app_secret:
        raise SystemExit("缺 FEISHU_APP_ID / FEISHU_APP_SECRET（可写进 .env）")
    client = FeishuClient(args.app_id, args.app_secret, base_url=args.base_url)
    path = f"/wiki/v2/spaces/{args.space_id}/members"

    try:
        if args.list:
            data = client._request("GET", path, params={"page_size": 50})
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if not args.add:
            raise SystemExit("请指定 --list 或 --add")
        member_id = args.member_id or args.app_id
        body = {"member_type": args.member_type, "member_id": member_id,
                "member_role": args.role}
        print("将提交：" + json.dumps(body, ensure_ascii=False))
        if args.dry_run:
            print("（dry-run，未真发请求）")
            return 0
        data = client._request("POST", path, body=body)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print("\n✓ 已添加；接着跑：connector_sync --connector feishu --space-id ... --dry-run")
        return 0
    except FeishuError as exc:
        print(explain_error(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

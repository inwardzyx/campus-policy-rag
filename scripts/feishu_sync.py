# -*- coding: utf-8 -*-
"""scripts/feishu_sync.py —— 把飞书知识库同步进本地 namespace

用法（应用需先开通 wiki/docx 权限，并被加入知识空间）：
    $env:FEISHU_APP_ID = "cli_xxx"
    $env:FEISHU_APP_SECRET = "xxx"
    D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\feishu_sync.py `
      --space-id 6946843325487912356 --namespace feishu_demo `
      --acl public --root kb_data

    # 只列知识空间（排错用）
    ... --list-spaces

★ 目前只同步 docx 正文；sheet/bitable/file 会记 skipped。
★ 节点级权限没有同步：acl 由 --acl 统一给（生产应接 SSO/权限源）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ★ 读仓库根的 .env（没有也照常跑）：省的每次开新窗口都重新 set 环境变量。
#   只读、不打印；`.env` 已在 .gitignore 里。
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:                                 # noqa: BLE001
    pass

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from kb.feishu import FeishuClient, FeishuWikiSource  # noqa: E402
from kb.store import KnowledgeStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="飞书知识库 → 本地 namespace")
    ap.add_argument("--space-id", default="", help="知识空间 ID（my_library 也可）")
    ap.add_argument("--namespace", default="", help="落到哪个本地 namespace")
    ap.add_argument("--app-id", default=os.environ.get("FEISHU_APP_ID", ""))
    ap.add_argument("--app-secret", default=os.environ.get("FEISHU_APP_SECRET", ""))
    ap.add_argument("--base-url", default="https://open.feishu.cn/open-apis")
    ap.add_argument("--root", default=str(ROOT / "kb_data"))
    ap.add_argument("--acl", default="public", help="逗号分隔，如 hr,finance")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-delete", action="store_true", help="不要删除飞书里已移除的文档")
    ap.add_argument("--list-spaces", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.25, help="每次拉正文后的 sleep，防 5 次/秒限频")
    args = ap.parse_args()

    if not args.app_id or not args.app_secret:
        raise SystemExit("缺 FEISHU_APP_ID / FEISHU_APP_SECRET（或 --app-id/--app-secret）")
    client = FeishuClient(args.app_id, args.app_secret, base_url=args.base_url)

    if args.list_spaces:
        spaces = list(client.iter_spaces())
        print(json.dumps(spaces, ensure_ascii=False, indent=2))
        return 0

    if not args.space_id or not args.namespace:
        raise SystemExit("--space-id 和 --namespace 必填（或用 --list-spaces 先看 space_id）")

    acl = [x.strip() for x in args.acl.split(",") if x.strip()] or ["public"]
    store = KnowledgeStore(args.root)
    source = FeishuWikiSource(client, args.space_id, args.namespace,
                              acl=acl, sleep_sec=args.sleep)
    stats = source.sync(store, delete_missing=not args.no_delete, dry_run=args.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if not stats["errors"] else 1


if __name__ == "__main__":
    # ★ 飞书报错一律翻译成"下一步点哪里"，而不是丢一个 99991672 让人去搜。
    from kb.feishu import FeishuError, explain_error

    try:
        raise SystemExit(main())
    except FeishuError as exc:
        print(explain_error(exc))
        raise SystemExit(2)

# -*- coding: utf-8 -*-
"""connector_sync.py —— 用统一引擎同步任意连接器（`fixture` 可离线跑，`feishu` 需 scope）。

## 为什么单开一个 CLI 而不是改 `feishu_sync.py`
`feishu_sync.py` 是老入口（测试与文档都指向它），先保持不动；
新入口用**同一套引擎**，等飞书 scope 开了再决定要不要合并。

## 用法
```powershell
# 离线验收：fixture 连接器（改 manifest 里的 acl 等价于"飞书调整了空间成员"）
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/connector_sync.py `
    --connector fixture --source evalset/fixtures/connector_demo --namespace demo

# 真实飞书（需要 scope + 应用在知识空间里）
$env:FEISHU_APP_ID="cli_xxx"; $env:FEISHU_APP_SECRET="xxx"
... scripts/connector_sync.py --connector feishu --space-id <id> --namespace feishu_demo --acl hr
```
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ★ 读仓库根的 .env（没有也照常跑）：省的每次开新窗口都重新 set 环境变量。
#   只读、不打印；`.env` 已在 .gitignore 里。
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:                                 # noqa: BLE001
    pass

from kb.connectors.core import sync_connector      # noqa: E402
from kb.connectors.fixture import FixtureConnector  # noqa: E402
from kb.store import KnowledgeStore                 # noqa: E402


def _connector(args):
    if args.connector == "fixture":
        if not args.source:
            raise SystemExit("✗ --connector fixture 需要 --source <目录>")
        return FixtureConnector(args.source)
    from kb.connectors.feishu import FeishuConnector
    from kb.feishu import FeishuClient

    app_id = args.app_id or os.environ.get("FEISHU_APP_ID", "")
    app_secret = args.app_secret or os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        raise SystemExit("✗ feishu 连接器需要 FEISHU_APP_ID / FEISHU_APP_SECRET（或 --app-id/--app-secret）")
    if not args.space_id:
        raise SystemExit("✗ feishu 连接器需要 --space-id（先用 feishu_sync.py --list-spaces 查）")
    client = FeishuClient(app_id, app_secret, base_url=args.base_url)
    acl = [x.strip() for x in (args.acl or "public").split(",") if x.strip()] or ["public"]
    return FeishuConnector(client, args.space_id, acl=acl, sleep_sec=args.sleep)


def main() -> int:
    ap = argparse.ArgumentParser(description="统一连接器同步（fixture / feishu）")
    ap.add_argument("--connector", default="fixture", choices=["fixture", "feishu"])
    ap.add_argument("--source", default="", help="fixture：连接器目录（含 manifest.json 与 docs/）")
    ap.add_argument("--space-id", default="", help="feishu：知识空间 ID")
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--root", default=str(ROOT / "kb_data"))
    ap.add_argument("--acl", default="public", help="feishu：逗号分隔的角色（v1 的 ACL 来源）")
    ap.add_argument("--app-id", default="")
    ap.add_argument("--app-secret", default="")
    ap.add_argument("--base-url", default="https://open.feishu.cn/open-apis")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-delete", action="store_true")
    args = ap.parse_args()

    connector = _connector(args)
    store = KnowledgeStore(args.root)
    try:
        report = sync_connector(connector, store, namespace=args.namespace,
                                dry_run=args.dry_run,
                                delete_missing=not args.no_delete)
    except Exception as exc:                          # noqa: BLE001
        # 飞书侧的错误翻译成"下一步点哪里"；其它错误照原样抛信息
        if hasattr(exc, "code") and hasattr(exc, "msg"):
            from kb.feishu import explain_error
            print(explain_error(exc))
            return 2
        raise

    extra = {}
    if hasattr(connector, "skipped"):
        extra["skipped"] = connector.skipped
    if hasattr(connector, "walk_errors") and connector.walk_errors:
        extra["walk_errors"] = connector.walk_errors
    payload = {"connector": connector.name, "namespace": args.namespace, **report.as_dict(), **extra}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""create_tenant.py —— 建一个租户，并把**明文 key 打印一次**（之后只剩哈希）。

## 为什么单独一个脚本
租户 key 是"给出去就不再持有"的凭证：只能在这里打印一次，不能靠"再查一次"。
把它做成 CLI 而不是藏在服务里，是为了让**开通租户**成为一个可审计的动作
（谁在什么时候跑过这条命令，终端/CI 日志里都有），而不是某个接口的副作用。

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/create_tenant.py `
    --id hr --name "人力资源部" --namespaces hr,hr/policy
```
★ 输出里那行 `KEY=tk_...` 只出现这一次；丢了只能重新建租户（`create` 会拒绝重复 id）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tenants as tenants_mod                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="创建租户（key 只打印一次）")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--namespaces", default="", help="逗号分隔；留空 = 不受限（不建议给租户用）")
    ap.add_argument("--path", default=str(ROOT / "kb_data" / "tenants.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    namespaces = [x.strip() for x in args.namespaces.split(",") if x.strip()] or None
    if namespaces is None:
        print("! 未指定 --namespaces：该租户可访问**所有**命名空间（只建议给 default 用）")
    if args.dry_run:
        print(f"（dry-run）将创建 id={args.id!r} namespaces={namespaces} → {args.path}")
        return 0

    reg = tenants_mod.Registry(Path(args.path), enabled=True)
    reg.load()
    try:
        tenant, key = reg.create(args.id, name=args.name, namespaces=namespaces)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1
    print(f"✓ 租户已建：id={tenant.id} name={tenant.name} namespaces={tenant.namespaces}")
    print(f"KEY={key}")
    print("★ 这串 key 只显示这一次；服务端只存 sha256，丢了只能重建租户。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

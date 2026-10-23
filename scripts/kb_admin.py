# -*- coding: utf-8 -*-
"""scripts/kb_admin.py —— 语料层命令行：上传 / 列表 / 删除 / 看 chunk

用法（在仓库根目录）：
    D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\kb_admin.py upload --ns demo --file a.md
    ... list --ns demo
    ... delete --ns demo --doc-id <id>
    ... chunks --ns demo

★ 默认落在仓库根下 kb_data/（已 gitignore）；可用 --root 指定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ★ 打印中文 JSON 前先切 UTF-8：Windows 管道默认 GBK，会把中文写成乱码。
#   这是脚本入口，必须在第一次 print 之前；库代码不重复做。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from kb.store import KnowledgeStore  # noqa: E402


def _store(args) -> KnowledgeStore:
    return KnowledgeStore(args.root)


def cmd_upload(args) -> int:
    store = _store(args)
    acl = [a.strip() for a in args.acl.split(",") if a.strip()] if args.acl else None
    for f in args.file:
        p = Path(f)
        rec = store.upload(
            args.ns,
            p.name,
            p.read_bytes(),
            title=args.title,
            source=args.source,
            version=args.version,
            acl=acl,
        )
        print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    rows = _store(args).list_documents(args.ns, include_deleted=args.all)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_delete(args) -> int:
    ok = _store(args).delete(args.ns, args.doc_id)
    print(json.dumps({"deleted": ok, "doc_id": args.doc_id}, ensure_ascii=False))
    return 0 if ok else 1


def cmd_chunks(args) -> int:
    store = _store(args)
    chunks = store.load_chunks(args.ns)
    print(json.dumps({
        "count": len(chunks),
        "errors": store.last_errors,
        "chunks": chunks[: args.limit],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="企业知识库语料管理")
    ap.add_argument("--root", default=str(ROOT / "kb_data"), help="语料根目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("upload", help="上传文档并登记")
    up.add_argument("--ns", required=True, help="namespace/知识库名")
    up.add_argument("--file", nargs="+", required=True)
    up.add_argument("--title")
    up.add_argument("--source")
    up.add_argument("--version")
    up.add_argument("--acl", help="逗号分隔，如 hr,finance")
    up.set_defaults(func=cmd_upload)

    ls = sub.add_parser("list", help="列出文档")
    ls.add_argument("--ns", required=True)
    ls.add_argument("--all", action="store_true", help="含 tombstone")
    ls.set_defaults(func=cmd_list)

    rm = sub.add_parser("delete", help="软删除")
    rm.add_argument("--ns", required=True)
    rm.add_argument("--doc-id", required=True)
    rm.set_defaults(func=cmd_delete)

    ck = sub.add_parser("chunks", help="查看切出来的块")
    ck.add_argument("--ns", required=True)
    ck.add_argument("--limit", type=int, default=5)
    ck.set_defaults(func=cmd_chunks)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

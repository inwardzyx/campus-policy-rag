#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""prune_audit.py —— 按保留期清理审计日志（独立入口，不在服务里自动删）。

为什么单独一个脚本：自动删会在服务运行时改写同一个文件，容易和并发追加打架；
清理应该是**可审计的运维动作**：谁在什么时候删了多少行，脚本会打出来。

用法：
    D:\\Python-project\\.venv\\Scripts\\python.exe scripts/prune_audit.py
    ... --path kb_data/audit.jsonl --days 30
    ... --dry-run          # 只报数，不写回
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb import audit as audit_mod               # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None, help="默认取 AUDIT_LOG_PATH 或 kb_data/audit.jsonl")
    ap.add_argument("--days", type=int, default=None, help="默认取 AUDIT_RETENTION_DAYS 或 30")
    ap.add_argument("--dry-run", action="store_true", help="只报数，不写回")
    args = ap.parse_args()

    path = Path(args.path) if args.path else audit_mod.log_path()
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        print(f"✗ 审计文件不存在：{path} —— 不报绿（没东西可清不等于清理成功）")
        return 1

    days = args.days if args.days is not None else audit_mod.retention_days()
    total = len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])

    if args.dry_run:
        # 干跑：用同一个判定函数算一遍，但不写回
        import json
        import time as _t
        cutoff = _t.time() - days * 86400
        stale = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ts = _t.mktime(_t.strptime(json.loads(line)["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:                       # noqa: BLE001
                continue
            stale += ts < cutoff
        print(f"[dry-run] {path}: 共 {total} 行，保留期 {days} 天，将删除 {stale} 行")
        return 0

    kept, removed = audit_mod.prune(path, days=days)
    print(f"✓ {path}: 共 {total} 行 → 保留 {kept} 行，删除 {removed} 行（保留期 {days} 天）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

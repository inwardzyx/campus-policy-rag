# -*- coding: utf-8 -*-
"""审计落盘：每个 namespace 请求写一行 JSONL，可追、可查、可保留期清理。

## 为什么需要它
`ContextTrace` 只在**响应体**里活着 —— 用户看过就没了。合规要的是"三个月前那次问答，
系统当时依据了哪几条、按谁的身份、有没有越权、答案里引了哪条"。
所以把 trace 的**决策部分**落盘，而不是把整段上下文搬进日志。

## 三条硬约束（比"多记点"重要得多）
1. **不写问题原文**：只存 `sha256(question)[:16]` 与字数。
   原文进日志 = PII 进了另一个系统，而它有自己的保留期与访问控制。
2. **不写块正文**：只存 `doc` / `clause` 这类定位信息（与 trace 同口径）。
3. **失败不拖垮请求**：盘满/没权限时只打一次 warning 并继续 —— 审计写不上，
   不该让这一轮答不出来（与 session 的取舍一致）。**但要能看见**：warning 里带原因。

## 保留期
每条记录带 `retention_days`，清理交给 `scripts/prune_audit.py`（按 `ts` 删旧行）。
为什么不在这里自动删：自动删会在"服务正在跑"时改写同一个文件，容易与并发写打架；
清理是运维动作，应该有独立的、可审计的入口。

## 开关
`AUDIT_ENABLED=0` 关闭；`AUDIT_LOG_PATH` 改路径（默认 `kb_data/audit.jsonl`）。
**只对带 namespace 的请求记录**：`docs/` 的 105 题旧路径不写盘（不改变旧行为）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

DEFAULT_PATH = "kb_data/audit.jsonl"
DEFAULT_RETENTION_DAYS = 30
_warned = False


def enabled(env: Mapping[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    return (e.get("AUDIT_ENABLED") or "1").strip().lower() not in ("0", "false", "off", "no")


def log_path(env: Mapping[str, str] | None = None) -> Path:
    e = os.environ if env is None else env
    return Path(e.get("AUDIT_LOG_PATH") or DEFAULT_PATH)


def retention_days(env: Mapping[str, str] | None = None) -> int:
    e = os.environ if env is None else env
    raw = (e.get("AUDIT_RETENTION_DAYS") or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_RETENTION_DAYS
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return n if n > 0 else DEFAULT_RETENTION_DAYS


def question_hash(question: str) -> str:
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()[:16]


def _split_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """把 `{"doc": "制度.md｜第三条", ...}` 拆成 doc / clause 两段。"""
    raw = str(source.get("doc") or "")
    doc, sep, clause = raw.partition("｜")
    return {"doc": doc or raw, "clause": (clause if sep else None)}


def build_event(req: Any, response: Mapping[str, Any], *, route: str,
                roles: Sequence[str] | None = None) -> dict[str, Any]:
    """把一次请求压成一行审计事件（**只放决策与定位信息**）。"""
    ctx = response.get("context") if isinstance(response, Mapping) else None
    ctx = ctx if isinstance(ctx, Mapping) else None
    sources = response.get("sources") or []
    citation = (ctx or {}).get("citation") or {}
    as_of = (ctx or {}).get("as_of") or {}
    blocks = (ctx or {}).get("blocks") or []

    if response.get("error"):
        decision = "error"
    elif not response.get("knowledge_hit", True) or not sources:
        decision = "refuse"
    else:
        decision = "answer"

    question = str(getattr(req, "question", "") or "")
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "route": route,
        "trace_id": (ctx or {}).get("trace_id"),
        "namespace": getattr(req, "namespace", None),
        # ★ 身份只存角色名（组织维度），问题只存哈希 —— 原文/个人标识都不进这条记录。
        "roles": list(roles or []),
        "question_sha256": question_hash(question),
        "question_chars": len(question),
        "decision": decision,
        "knowledge_hit": bool(response.get("knowledge_hit", False)),
        "rerank_used": bool(response.get("rerank_used", False)),
        # ★ sources 里的 doc 是 "doc｜clause" 拼好的（见 service.py 构造处），
        #   这里拆开存：只存整串会让 "clause" 永远为 null，审计查不动"用了哪一条"。
        "sources": [_split_source(s) for s in sources if isinstance(s, Mapping)],
        "n_blocks": len(blocks),
        "chars_used": ((ctx or {}).get("budget") or {}).get("retrieved_used"),
        "as_of": {"event_time": as_of.get("event_time"),
                  "dropped_by_reason": as_of.get("dropped_by_reason")} if as_of else None,
        "citation": {"used_ids": citation.get("used_ids"),
                     "unknown_ids": citation.get("unknown_ids"),
                     "validity": citation.get("validity")} if citation else None,
        "took_ms": response.get("took_ms"),
        "answer_chars": len(str(response.get("answer") or "")),
        "retention_days": retention_days(),
    }


def record(event: Mapping[str, Any], *, path: Path | None = None) -> bool:
    """追加一行。返回是否写入成功（失败只 warning 一次，不影响请求）。"""
    global _warned
    target = path or log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
        return True
    except OSError as exc:                                # noqa: BLE001
        if not _warned:
            logger.warning("审计写入失败（%s）：%s —— 之后同类失败不再重复告警", target, exc)
            _warned = True
        return False


def record_response(req: Any, response: Mapping[str, Any], *, route: str,
                    path: Path | None = None,
                    roles: Sequence[str] | None = None) -> bool:
    """为带 namespace 的请求写一行；docs/ 旧路径**不写**（不改变旧行为）。"""
    if not getattr(req, "namespace", None) or not enabled():
        return False
    return record(build_event(req, response, route=route, roles=roles), path=path)


def prune(path: Path, *, days: int, now: float | None = None) -> tuple[int, int]:
    """按 `ts` 删掉超过保留期的行。返回 `(保留行数, 删除行数)`。

    ★ 解析不了的行**保留**：审计文件里出现脏行说明有人手工改过，
      这时"删掉看不懂的东西"比"留着并让它被看见"危险。
    """
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    kept: list[str] = []
    removed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ts = time.mktime(time.strptime(json.loads(line)["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:                                  # noqa: BLE001
            kept.append(line)
            continue
        if ts >= cutoff:
            kept.append(line)
        else:
            removed += 1
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return len(kept), removed

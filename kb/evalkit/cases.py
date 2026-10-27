# -*- coding: utf-8 -*-
"""题集：一份 schema、一套校验、一种套件解析方式。

## 为什么 schema 要统一
现在有 `evalset/questions.json`（老的校园题集，JSON 数组）、`evalset/scenarios/*.jsonl`
（场景题集，JSONL）。以后还会有会话轨迹题集、工具轨迹题集。
如果每个入口各写一个 loader，字段名一漂移，gold 就会静默变成"没有 gold"，
归因随之把它误判成 `retrieval_miss` —— 这类错不会有报错，只会让指标慢慢失真。

## 兼容口径
- `gold` / `expected_sources` 两种写法都认（老题集用前者，企业模板用后者）；
- `type` 缺省 `answer`；`scenario` 缺省取套件名；
- **未知字段保留**（比如 `note`、`boundary`），不因为校验把它们丢掉。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

VALID_TYPES = ("answer", "refuse", "deny", "session")

#: 会话题里每一轮的 type 只许是这三类（"session" 不能套娃）
TURN_TYPES = ("answer", "refuse", "deny")

#: 套件名 → 相对仓库根的路径（新增场景套件只加一行）
SUITES: dict[str, str] = {
    "campus": "evalset/questions.json",
}


def _normalize_gold(raw: Any) -> list | None:
    """gold 的两种写法都要保留原样：

    · `[doc, clause]`（老题集，单条）→ 原样
    · `[[doc, clause], ...]`（多跳题）→ 保留**全部**（命中任一即算对）

    ★ 曾经这里统一压成"前两个字符串"，多跳题的第二条 gold 会在入口静默消失。
    """
    if not raw:
        return None
    if len(raw) == 2 and isinstance(raw[0], str) and isinstance(raw[1], str):
        return [str(raw[0]), str(raw[1])]
    pairs = [[str(item[0]), str(item[1])] for item in raw
             if isinstance(item, (list, tuple)) and len(item) >= 2]
    return pairs or None


def normalize_turn(turn: Mapping[str, Any], *, scenario: str | None = None) -> dict[str, Any]:
    """会话里的一轮：形状与单题一致，但没有自己的 id/scenario。"""
    out = {"question": str(turn.get("question") or ""),
           "type": str(turn.get("type") or "answer").lower()}
    out["gold"] = _normalize_gold(turn.get("gold") or turn.get("expected_sources"))
    if scenario:
        out["scenario"] = scenario
    return out


def normalize_case(case: Mapping[str, Any], *, scenario: str | None = None) -> dict[str, Any]:
    """把一行题统一成内部形状；**不改语义**，只补缺省与别名。"""
    out = dict(case)
    out["id"] = str(case.get("id") or "")
    out["type"] = str(case.get("type") or "answer").lower()
    out["question"] = str(case.get("question") or "")
    if scenario and not out.get("scenario"):
        out["scenario"] = scenario
    out["gold"] = _normalize_gold(case.get("gold") or case.get("expected_sources"))
    if isinstance(case.get("turns"), list):
        out["turns"] = [normalize_turn(t, scenario=scenario) for t in case["turns"]]
    return out


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """读 JSON 数组或 JSONL；空文件返回空列表（由 `validate_cases` 判成红）。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    rows: list[dict[str, Any]]
    if p.suffix.lower() == ".jsonl" or text.lstrip().startswith("{"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = list(json.loads(text))
    scenario = p.stem
    return [normalize_case(r, scenario=scenario) for r in rows]


def list_suites(root: str | Path) -> dict[str, str]:
    """列出可用套件：内置的 + `evalset/scenarios/*.jsonl`（按文件名）。"""
    base = Path(root)
    found = dict(SUITES)
    for p in sorted((base / "evalset" / "scenarios").glob("*.jsonl")):
        found.setdefault(p.stem, str(p.relative_to(base)).replace("\\", "/"))
    for name, rel in list(found.items()):
        if not (base / rel).is_file():
            found.pop(name)                     # 声明了但文件不在：不假装可用
    return found


def resolve_suite(spec: str, root: str | Path) -> Path:
    """`--cases` 既可以是路径，也可以是套件名。都找不到时报清楚有哪些可用。"""
    base = Path(root)
    direct = Path(spec)
    if direct.is_file():
        return direct
    if (base / spec).is_file():
        return base / spec
    suites = list_suites(base)
    if spec in suites:
        return base / suites[spec]
    raise FileNotFoundError(
        f"找不到题集 {spec!r}。可用套件：{sorted(suites) or '（无）'}；"
        f"也可以直接给路径（JSON 数组或 JSONL）。")


def validate_cases(cases: Iterable[Mapping[str, Any]]) -> list[str]:
    """返回问题列表；**空题集本身就是一个错误**（空转不许报绿）。"""
    issues: list[str] = []
    rows = list(cases)
    if not rows:
        return ["题集为空：0 道题不是\"通过\"，是空转"]
    seen: set[str] = set()
    for i, case in enumerate(rows):
        cid = str(case.get("id") or "")
        where = f"第 {i + 1} 行" + (f"（id={cid}）" if cid else "")
        if not cid:
            issues.append(f"{where}: 缺 id（报告要靠它关联响应）")
        elif cid in seen:
            issues.append(f"{where}: id 重复（{cid}）—— 摘要会互相覆盖")
        seen.add(cid)
        typ = str(case.get("type") or "answer")
        # ★ 会话题没有顶层 question（问题在 turns 里）；别用单题规则去要求它。
        if typ != "session" and not case.get("question"):
            issues.append(f"{where}: question 为空")
        if typ not in VALID_TYPES:
            issues.append(f"{where}: type={typ!r} 不在 {VALID_TYPES}")
        if typ == "session":
            # ★ 会话题：轮次为空 = 空转；每一轮自己也要过同样的校验。
            turns = case.get("turns") or []
            if not turns:
                issues.append(f"{where}: session 题没有 turns（空会话等于空转）")
            for ti, turn in enumerate(turns):
                twhere = f"{where} 第 {ti + 1} 轮"
                if not turn.get("question"):
                    issues.append(f"{twhere}: question 为空")
                ttype = str(turn.get("type") or "answer")
                if ttype not in TURN_TYPES:
                    issues.append(f"{twhere}: type={ttype!r} 不在 {TURN_TYPES}")
                if ttype == "answer" and not turn.get("gold"):
                    issues.append(f"{twhere}: answer 轮没有 gold（没法判分）")
        elif typ == "answer" and not case.get("gold"):
            issues.append(f"{where}: answer 题没有 gold（{typ} 题无法判分，归因会失真）")
    return issues

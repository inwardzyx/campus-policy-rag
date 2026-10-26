# -*- coding: utf-8 -*-
"""上下文层 v1：八层声明 + 字符预算 + ContextTrace。

## 为什么按 chars 估，而不是 token
本机没有可信的 tokenizer（DeepSeek 的 tokenizer 不在客户端里），写 token 数就等于**编**。
`len(str)` 是可复现的；对外字段一律叫 `chars`，别让它看起来像 token。

## v1 只让 retrieved 层真正占预算
八层（system/policy/history/task/memory/retrieved/tool/output）先把结构立起来，
除 `retrieved` 外的层是 `status="reserved"` + 明确理由（不是 `used_chars=0` 了事），
并在 budget 里报 `enabled_layers` / `reserved_layers` —— 让"声明了"和"真的注入了"一眼分开，
而不是把八层都填上好看的数字。v1 不改答案 prompt 的措辞，只决定**喂哪几块**。

## 超预算：整块丢，不截半块
制度问答里"截一半的条款"比"少一条"更危险（半句常常能反读原意）。
所以只在块边界裁剪：放不下的整块进 excluded（reason=budget），
单块本身就超过 cap 的进 excluded（reason=oversize）——**不截断、不拼半块**。

## 为什么没用完的预算不借给别的层
固定 cap 才可复现。自动借用会让同一问题在不同候选集下预算行为不一致，trace 也就没法比。
未用额度记在 `budget.retrieved_unused` 里：看得到，但不参与分配。

## 边界
不 import pydantic / 不 import service / 不碰 LLM —— 这样它能在没有模型、没有 key 的
环境里被单测（与 `limits.py`、`session.py` 同款的分层理由）。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: 八层的**固定顺序**（稳定层在前、变化层在后，见设计稿 §3.3 第 4 条）。
#: 顺序是元组而不是 set：trace 里层的排列要稳定，否则每次 diff 都在抖动。
LAYERS: tuple[str, ...] = (
    "system", "policy", "history", "task",
    "memory", "retrieved", "tool", "output",
)

#: 设计稿 §3.1 的层间占比（合计 1.00）。v1 只有 retrieved 那一条真的生效。
LAYER_RATIOS: dict[str, float] = {
    "system": 0.06,
    "policy": 0.04,
    "history": 0.10,
    "task": 0.06,
    "memory": 0.08,
    "retrieved": 0.46,
    "tool": 0.10,
    "output": 0.10,
}

DEFAULT_BUDGET_CHARS = 4000
DEFAULT_RETRIEVED_RATIO = 0.46
DEFAULT_OUTPUT_RESERVE_CHARS = 400

#: 块的去向。只有 `fits` 会真的喂给生成模型 —— 其余三个都意味着"没进上下文"。
REASON_FITS = "fits"
REASON_BUDGET = "budget"
REASON_OVERSIZE = "oversize"
REASON_LOW_SCORE = "low_score"

#: degraded 的取值（`degraded["retrieval"]`）。
DEGRADED_NO_BLOCK_FITS = "no_block_fits"
DEGRADED_BUDGET_TRUNCATED = "budget_truncated"
DEGRADED_NOT_READY = "not_ready"


def new_trace_id() -> str:
    """给一次请求一个 trace_id。用 uuid4 而不是自增号：多进程/多机也不会撞。"""
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Block:
    """trace 里的一个块 —— **只放定位信息，不放正文**。

    ★ 为什么不放正文：trace 会出现在响应里、将来还会进日志；副本越多泄漏面越大。
      而 `sources` 里已经有全文了，再抄一遍纯属浪费。
    """

    source: str
    clause: str
    chars: int
    included: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "clause": self.clause, "chars": self.chars,
                "included": self.included, "reason": self.reason}


@dataclass(frozen=True)
class Budget:
    """字符预算。三个数都能用环境变量覆盖，默认值见上面的常量。

    ★ 非法值**不静默**：`from_env()` 会退回默认值，并把"哪个 env 被忽略了"记进
      `ignored_env`，最终出现在 trace 的 `budget.env_ignored` 里 —— 配错误了要看得见，
      而不是安静地按另一套预算跑。
    """

    total_chars: int = DEFAULT_BUDGET_CHARS
    retrieved_ratio: float = DEFAULT_RETRIEVED_RATIO
    output_reserve_chars: int = DEFAULT_OUTPUT_RESERVE_CHARS
    ignored_env: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.total_chars <= 0:
            raise ValueError(f"total_chars 必须 > 0，收到 {self.total_chars}")
        if not (0 < self.retrieved_ratio <= 1):
            raise ValueError(f"retrieved_ratio 必须在 (0, 1]，收到 {self.retrieved_ratio}")
        if self.output_reserve_chars < 0:
            raise ValueError(f"output_reserve_chars 不能为负，收到 {self.output_reserve_chars}")
        if self.retrieved_cap <= 0:
            raise ValueError(
                f"预算切出来的检索额度是 {self.retrieved_cap} chars —— "
                f"total={self.total_chars} × ratio={self.retrieved_ratio}")

    @property
    def retrieved_cap(self) -> int:
        """retrieved 层的硬上限（chars）。**固定值，不从别层借额度。**"""
        return max(1, int(round(self.total_chars * self.retrieved_ratio)))

    def layer_caps(self) -> dict[str, int]:
        """每层声明的额度。v1 里只有 retrieved / output 这两个数会真的被用到。

        ★ 不变式：**八层加起来恒等于 total_chars**（默认配置下恰好与设计稿 §3.1 一致）。
          为什么不直接拿 `total × 设计稿占比`：`retrieved_ratio` 和预留都能被 env 改，
          照抄占比的话八层之和会超过总预算（实测 ratio=0.5 时是 2080 > 2000）——
          一份自相矛盾的预算表比没有预算表更糟。
          ⇒ retrieved / output 先扣掉，剩下的额度再按设计稿比例**重新分配**给其余六层。
        """
        caps: dict[str, int] = {"retrieved": self.retrieved_cap,
                                "output": self.output_reserve_chars}
        others = [name for name in LAYERS if name not in caps]
        remaining = max(0, self.total_chars - self.retrieved_cap - self.output_reserve_chars)
        weight = sum(LAYER_RATIOS[name] for name in others) or 1.0
        for name in others:
            caps[name] = int(remaining * LAYER_RATIOS[name] / weight)
        # 取整的零头补给占比最大的那层，让求和**正好**等于总预算（不是"约等于"）。
        biggest = max(others, key=lambda n: LAYER_RATIOS[n])
        caps[biggest] += remaining - sum(caps[n] for n in others)
        return {name: caps[name] for name in LAYERS}      # 顺序固定 = LAYERS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Budget":
        e = os.environ if env is None else env
        ignored: list[str] = []
        total = _env_int(e, "CONTEXT_BUDGET_CHARS", DEFAULT_BUDGET_CHARS, ignored)
        ratio = _env_ratio(e, "CONTEXT_RETRIEVED_RATIO", DEFAULT_RETRIEVED_RATIO, ignored)
        reserve = _env_int(e, "CONTEXT_OUTPUT_RESERVE_CHARS",
                           DEFAULT_OUTPUT_RESERVE_CHARS, ignored, allow_zero=True)
        if reserve >= total:
            # 预留比总预算还大 ⇒ 检索永远没额度。这不是"小问题"，是配置错：
            # 按 0 处理并记下来（trace 里 output 层的 budget_chars 会是 0，看得见）。
            ignored.append(f"CONTEXT_OUTPUT_RESERVE_CHARS={reserve}（≥ 总预算 {total}）")
            reserve = 0
        return cls(total_chars=total, retrieved_ratio=ratio,
                   output_reserve_chars=reserve, ignored_env=tuple(ignored))


@dataclass
class ContextTrace:
    """一次请求的上下文账本：用了哪几块、丢了哪几块、为什么。

    `degraded` 是**空 dict = 没降级**；非空则 `{"retrieval": 原因}`。
    用 dict 而不是 bool：只报"降级了"等于让人去翻日志找原因。
    """

    trace_id: str
    namespace: str
    blocks: list[Block]
    excluded: list[Block]
    degraded: dict[str, str]
    budget: dict[str, Any]
    # ★ 2026-10-25 加：候选清单（只有 source/clause/rank，不带正文也不带分数）。
    #   为什么必须有：没有它就无法区分"检索压根没捞到"（retrieval_miss）和
    #   "捞到了但排在第 4 名"（rank_shift）—— 这两种失败的修法完全不同，
    #   而只看 included/excluded 时它们长得一模一样。
    candidates: list[dict[str, Any]] = field(default_factory=list)
    # ★ 版本裁决报告（as-of）：event_time / 分组数 / 各原因丢弃数 / 日期未知块数。
    #   没有它就看不出"这条为什么没用"到底是被预算裁的，还是被版本裁决掉的。
    as_of: dict[str, Any] | None = None
    # ★ 2026-10-26 加（轨迹/审计的键）：没有这两样，"会话第 3 轮答错" 无法定位。
    #   turn_index 的含义是"本次请求在窗口内的第几轮"（窗口 = session 保留的最近 N 轮），
    #   超过窗口会饱和在 N+1 —— 这是保留策略的已知限制，不是 bug（见 session.py）。
    session_id: str | None = None
    turn_index: int | None = None
    # ★ 改写后的检索词（只进 trace，不进审计）：多轮唯一的跨轮机制，不暴露就测不了。
    rewritten_query: str | None = None
    # ★ 2026-10-26 加：压缩报告（mode/before_chars/after_chars/dedup_dropped/...）。
    #   默认 off ⇒ 空 dict；空 dict 与"压了但没变"是两件事，前者表示没启用。
    compression: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "namespace": self.namespace,
            "blocks": [b.to_dict() for b in self.blocks],
            "excluded": [b.to_dict() for b in self.excluded],
            "degraded": dict(self.degraded),
            "budget": dict(self.budget),
            "candidates": [dict(c) for c in self.candidates],
            "compression": dict(self.compression) if self.compression else None,
            "as_of": dict(self.as_of) if self.as_of else None,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "rewritten_query": self.rewritten_query,
        }


# ==================================================================
# 选块
# ==================================================================

def _session_kwargs(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """把会话相关的三个键挑出来。**只认白名单**：trace 是响应体的一部分，
    随手塞字段会让它慢慢变成"什么都往里放"的垃圾桶。"""
    if not meta:
        return {}
    out = {}
    if meta.get("session_id"):
        out["session_id"] = str(meta["session_id"])
    if meta.get("turn_index") is not None:
        out["turn_index"] = int(meta["turn_index"])
    if meta.get("rewritten_query"):
        out["rewritten_query"] = str(meta["rewritten_query"])
    return out


def _chunk_text(chunk: Mapping[str, Any]) -> str:
    """块真正被喂进 prompt 的那段文本（`build_answer_prompt` 用 text_full 优先）。"""
    return str(chunk.get("text_full") or chunk.get("text") or "")


def _chunk_ids(chunk: Mapping[str, Any]) -> tuple[str, str]:
    return str(chunk.get("doc") or ""), str(chunk.get("clause") or "")


#: 未实现/未注入的层：**必须给理由**。
#: ★ 这一版之前的坑：八层都有 `budget_chars`，memory 写着 `used_chars: 0` —— 读报告的人
#:   分不清"这轮没用记忆"和"记忆根本没做"。两种含义长得一样，就是假指标。
#:   现在每层带 `status`（implemented / reserved）+ `reason`：
#:   `reserved` + `used_chars=0` 只能读成"没实现"，不可能误读成"用了 0"。
RESERVED_REASONS: dict[str, str] = {
    "system": "未注入：system prompt 是固定的，不占上下文预算",
    "policy": "未注入：策略文本固定，不占上下文预算",
    "history": "未注入：历史只喂查询改写，**不进上下文**（避免靠上一轮资料绕过本轮检索）",
    "task": "未接入：任务层没有内容来源",
    "memory": "未实现：缺少可靠的写入/失效策略；PII 与过期事实的合规风险大于收益（见 HANDOFF §5 触发条件）",
    "tool": "未实现：只有 search_policy 一个工具，工具输出不写回上下文",
    "output": "未注入：输出预留额度由 output_reserve_chars 控制，该层本身不写内容",
}


def _budget_dict(budget: Budget, used: int) -> dict[str, Any]:
    caps = budget.layer_caps()
    layers = {
        name: {"budget_chars": caps[name],
               "used_chars": (used if name == "retrieved" else 0),
               "status": ("implemented" if name == "retrieved" else "reserved"),
               "reason": ("" if name == "retrieved" else RESERVED_REASONS[name])}
        for name in LAYERS
    }
    return {
        "total_chars": budget.total_chars,
        "retrieved_cap": budget.retrieved_cap,
        "output_reserve_chars": budget.output_reserve_chars,
        "retrieved_used": used,
        "retrieved_unused": max(0, budget.retrieved_cap - used),
        # ★ 把"哪些层真的有内容"单独列出来：v1 只有 retrieved，
        #   免得有人看着八层的 budget 以为 system/policy 也注入了。
        "enabled_layers": ["retrieved"],
        "implemented_layers": ["retrieved"],
        "reserved_layers": [{"layer": n, "reason": RESERVED_REASONS[n]}
                            for n in LAYERS if n != "retrieved"],
        "layers": layers,
        "env_ignored": list(budget.ignored_env),
    }


def select_retrieved(
    chunks: Sequence[Mapping[str, Any]],
    *,
    budget: Budget,
    namespace: str,
    trace_id: str | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    compression: Mapping[str, Any] | None = None,
    extra_dropped: Sequence[Mapping[str, Any]] | None = None,
    as_of: Mapping[str, Any] | None = None,
    session_meta: Mapping[str, Any] | None = None,
) -> tuple[list[Mapping[str, Any]], ContextTrace]:
    """按**给定顺序**贪心放入，返回 `(真正喂给模型的块, trace)`。

    ★ 两个返回值必须一起用：预算裁掉的块**不能还留在**喂给生成模型的那份里，
      否则 trace 说的就是假话（"丢了一块"但其实喂了）。调用方拿到 kept 要原样替换 ctx。

    ★ 顺序即优先级：调用方传进来的是精排后的顺序（高分在前），本函数不再排序 ——
      排序规则属于检索层，放在这里会变成"两处各写一份"。
    """
    tid = trace_id or new_trace_id()
    cap = budget.retrieved_cap
    kept: list[Mapping[str, Any]] = []
    blocks: list[Block] = []
    # ★ 版本裁决丢掉的块先落进 excluded：它们**没进过预算**，但必须能被看见，
    #   否则归因会把"旧版被丢"误判成"检索没捞到"。
    excluded: list[Block] = [
        Block(*_chunk_ids(c), len(_chunk_text(c)), False, str(c.get("_drop_reason") or "stale"))
        for c in (extra_dropped or [])
    ]
    used = 0
    for chunk in chunks:
        text = _chunk_text(chunk)
        n = len(text)
        source, clause = _chunk_ids(chunk)
        if chunk.get("_drop_reason"):
            # 压缩阶段判定的"整块丢"（如近重复）：照样记进 excluded，
            # 这样 trace 里能看出丢了哪块、为什么、多大 —— 而不是凭空少一块。
            excluded.append(Block(source, clause, n, False, str(chunk["_drop_reason"])))
            continue
        if n > cap:
            # 单块就超上限：整块丢。截一半会把条款读反，比少一条危险。
            excluded.append(Block(source, clause, n, False, REASON_OVERSIZE))
            continue
        if used + n > cap:
            excluded.append(Block(source, clause, n, False, REASON_BUDGET))
            continue
        kept.append(chunk)
        used += n
        blocks.append(Block(source, clause, n, True, REASON_FITS))

    degraded: dict[str, str] = {}
    if chunks and not kept:
        # 候选不为空、却一块都放不下 ⇒ 必须是"拒答"，不允许零上下文硬生成。
        degraded["retrieval"] = DEGRADED_NO_BLOCK_FITS
    elif excluded:
        degraded["retrieval"] = DEGRADED_BUDGET_TRUNCATED

    trace = ContextTrace(trace_id=tid, namespace=namespace, blocks=blocks,
                         excluded=excluded, degraded=degraded,
                         budget=_budget_dict(budget, used),
                         candidates=[dict(c) for c in (candidates or [])],
                         compression=dict(compression) if compression else None,
                         as_of=dict(as_of) if as_of else None,
                         **(_session_kwargs(session_meta)))
    return kept, trace


def exclusion_trace(
    chunks: Sequence[Mapping[str, Any]],
    *,
    budget: Budget,
    namespace: str,
    reason: str,
    trace_id: str | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    session_meta: Mapping[str, Any] | None = None,
) -> ContextTrace:
    """没走到"选块"那一步的请求（低分拒答 / 模型没就绪）：块全部记 excluded，预算一个没花。

    ★ 为什么拒答也要 trace：拒答是最需要"为什么"的场景。只回一个 `sources=[]`，
      事后完全看不出"当时捞到过什么、为什么一块都没用"。
    """
    tid = trace_id or new_trace_id()
    excluded = [Block(*_chunk_ids(c), len(_chunk_text(c)), False, reason) for c in chunks]
    return ContextTrace(trace_id=tid, namespace=namespace, blocks=[], excluded=excluded,
                        degraded={"retrieval": reason},
                        budget=_budget_dict(budget, 0),
                        candidates=[dict(c) for c in (candidates or [])],
                        **(_session_kwargs(session_meta)))


# ==================================================================
# env 解析（坏值一律"退回默认 + 记账"，不抛、也不静默）
# ==================================================================

def _env_int(env: Mapping[str, str], name: str, default: int, ignored: list[str],
             *, allow_zero: bool = False) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        ignored.append(f"{name}={raw}（不是整数）")
        return default
    if value < 0 or (value == 0 and not allow_zero):
        ignored.append(f"{name}={raw}（超出取值范围）")
        return default
    return value


def _env_ratio(env: Mapping[str, str], name: str, default: float, ignored: list[str]) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        ignored.append(f"{name}={raw}（不是数字）")
        return default
    if not (0 < value <= 1):
        ignored.append(f"{name}={raw}（必须在 (0, 1]）")
        return default
    return value

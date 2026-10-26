# -*- coding: utf-8 -*-
"""块级规则压缩：只在**块边界/行边界**做减法，绝不重写、绝不截半句。

## 为什么不用 LLM 摘要
ACE（Agentic Context Engineering, 2025）给的血证：让模型整体重写上下文，
18,282 token 会塌成 122 token，准确率反而低于不改。制度问答里一句话的限定条件丢了，
意思就变了。所以本模块只做三件可复现的事，且**全部可关**。

## 为什么只做"去重 + 噪声行"，不做"句子抽取"
2026-10-26 先在 `docs/kb` 上量过（271 块）：
- 块本来就短：中位 **75 字**、均值 118、最大 1870；
- 页眉/页码/点线等噪声行：**0 处命中**（loader 已经清过页脚）；
- 近重复对（3-gram Jaccard ≥ 0.8）：**5 对**（其中 3 对是完全相同文本、跨文件重复的样板条款）；
- 超过检索额度（1840）的块：**1 块**。
⇒ 这个语料上"句子抽取"几乎无处可抽，抽了反而更容易抽掉答案句。
  ⇒ 本轮只做去重 + 噪声行，**并保留这两条在别的语料（上传的 PDF/HTML）上生效**。
  ⇒ "超长块按句拆成两段"会影响出处展示与引用编号的语义，**明确推迟**（记在 HANDOFF §5）。

## 开关
`CONTEXT_COMPRESS=rule` 才启用（默认 `off`）。为什么默认关：
本轮要拿"开/关"两组做配对对照；默认打开等于**不量就改**。
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping, Sequence

MODE_OFF = "off"
MODE_RULE = "rule"
DEFAULT_MODE = MODE_OFF
DEFAULT_DEDUP_JACCARD = 0.8

#: 噪声行：页码、纯数字、分隔线、点线。**只在整行匹配时删**（不删正文里的数字）。
_NOISE_LINE_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?|\d{1,3}|[-—_=*·•]{3,}|\.{4,}|…{3,})\s*$"
)


def mode_from_env(env: Mapping[str, str] | None = None) -> str:
    e = os.environ if env is None else env
    raw = (e.get("CONTEXT_COMPRESS") or "").strip().lower()
    return MODE_RULE if raw == MODE_RULE else MODE_OFF


def _grams(text: str, n: int = 3) -> set[str]:
    """字符 n-gram（去空白）——中文不需要分词，3-gram 对近似重复够用且零依赖。"""
    t = "".join((text or "").split())
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def jaccard(a: str, b: str, n: int = 3) -> float:
    ga, gb = _grams(a, n), _grams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _text(chunk: Mapping[str, Any]) -> str:
    return str(chunk.get("text_full") or chunk.get("text") or "")


def _strip_noise(text: str) -> tuple[str, int]:
    """删掉整行都是页码/分隔线的行；返回 (新文本, 删掉的行数)。"""
    lines = text.split("\n")
    kept = [ln for ln in lines if not _NOISE_LINE_RE.match(ln)]
    return "\n".join(kept), len(lines) - len(kept)


def compress_chunks(
    chunks: Sequence[Mapping[str, Any]],
    *,
    mode: str = DEFAULT_MODE,
    dedup_jaccard: float = DEFAULT_DEDUP_JACCARD,
) -> tuple[list[dict], dict[str, Any]]:
    """返回 `(压缩后的块副本, 报告)`。

    - 被去重的块**不删除**，而是打上 `_drop_reason="dup"`：选块那一步会把它记进
      `excluded`（带 chars），这样 trace 里能看见"丢了哪块、为什么、多大"。
    - 副本：不改原 dict（`sources` 与 trace 还用同一批对象）。
    """
    report: dict[str, Any] = {"mode": mode, "dedup_dropped": 0, "noise_lines_removed": 0,
                              "before_chars": sum(len(_text(c)) for c in chunks),
                              "after_chars": 0}
    out: list[dict] = []

    if mode != MODE_RULE:
        report["after_chars"] = report["before_chars"]
        return [dict(c) for c in chunks], report

    kept_texts: list[str] = []
    for chunk in chunks:
        text = _text(chunk)
        # ① 近重复：与已保留的任一块 3-gram Jaccard ≥ 阈值 ⇒ 整块丢（保留先到的那块：
        #    它排序更靠前；"保留更长的"会让同一份语料在两次请求里留下不同的块，不可复现）。
        if any(jaccard(text, other, ) >= dedup_jaccard for other in kept_texts):
            copy = dict(chunk)
            copy["_drop_reason"] = "dup"
            out.append(copy)
            report["dedup_dropped"] += 1
            continue

        # ② 噪声行（当前语料 0 处命中，保留给上传的 PDF/HTML 语料）
        cleaned, removed = _strip_noise(text)
        report["noise_lines_removed"] += removed

        copy = dict(chunk)
        if removed and cleaned.strip():
            copy["text_full"] = cleaned
            # `text` 是截断版，同步更新以免两条路喂的内容不一致
            if "text" in copy:
                copy["text"] = cleaned[:len(str(copy.get("text") or ""))] or cleaned
        kept_texts.append(_text(copy))
        out.append(copy)

    report["after_chars"] = sum(len(_text(c)) for c in out if not c.get("_drop_reason"))
    return out, report

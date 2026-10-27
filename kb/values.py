# -*- coding: utf-8 -*-
"""答案值校验：从条款里抽"值"，再检查答案里有没有。

## 为什么需要它
现有 answer 指标判的是"**gold 条款有没有进上下文**"——检索对了就算过。
但用户要的是"**数值答对**"（8 学时 vs 12 学时）。这两件事差很远：
条款进上下文、答案却写错数，现有指标看不出来。

## 口径（诚实版）
- `extract()`：从条款正文抽 (数值, 单位) 对，单位做同义归一（小时/时、天/日…）；
- `match()`：答案里出现**任意一个**期望值 ⇒ 命中。这是**弱判据**（条款里有多个值时更松），
  所以报告里把 `answer_value_mode` 标成 `auto`，别把它当"完全正确"；
- 没有可抽的值 ⇒ 返回空列表（**缺失**），不是"通过"也不是"0 分"。
"""
from __future__ import annotations

import re

#: 单位同义归一：键是正则里能出现的写法，值统一成一个。
_UNIT_ALIAS = {
    "小时": "小时", "时": "小时", "学时": "学时", "节": "节",
    "天": "天", "日": "天", "周": "周", "星期": "周", "月": "月", "年": "年",
    "元": "元", "块": "元", "%": "%", "％": "%", "次": "次", "人": "人",
    "分": "分", "门": "门", "学分": "学分", "个": "个", "名": "名",
}
_UNIT_RE = "|".join(sorted(_UNIT_ALIAS, key=len, reverse=True))
_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(" + _UNIT_RE + r")?")
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}
_CN_RUN = re.compile(r"([一二两三四五六七八九十]{1,3})\s*(?=" + "|".join(sorted(_UNIT_ALIAS, key=len, reverse=True)) + r")")


def _cn_to_int(run: str) -> int:
    """把"三/十二/二十/二十三"这类中文数字转成整数（只覆盖 1–99，够条款用）。"""
    if "十" not in run:
        return sum(_CN_NUM.get(ch, 0) for ch in run) if len(run) == 1 else 0
    left, _, right = run.partition("十")
    tens = _CN_NUM.get(left, 1) if left else 1
    ones = _CN_NUM.get(right, 0) if right else 0
    return tens * 10 + ones


def _normalize_cn(text: str) -> str:
    """把"三天/两周/十二人"换成阿拉伯数字，后面的正则才认得。"""
    def repl(m: "re.Match[str]") -> str:
        n = _cn_to_int(m.group(1))
        return str(n) if n else m.group(1)
    return _CN_RUN.sub(repl, text or "")


def _norm_num(raw: str) -> float:
    return float(raw)


def extract(text: str) -> list[tuple[float, str]]:
    """抽 (数值, 单位) 对；没有单位的数字也保留（单位记空串）。

    ★ 保持出现顺序、去重：条款里"40 小时/月"会抽到 (40,'小时') 与 (1,'月')，
      后者是频率噪声——由调用方决定要不要过滤（这里不猜）。
    """
    out: list[tuple[float, str]] = []
    for m in _VALUE_RE.finditer(_normalize_cn(text)):
        unit_raw = m.group(2) or ""
        unit = _UNIT_ALIAS.get(unit_raw, unit_raw)
        pair = (_norm_num(m.group(1)), unit)
        if pair not in out:
            out.append(pair)
    return out


def match(answer: str, expected: list[tuple[float, str]]) -> bool:
    """答案里出现任一期望值 ⇒ True。expected 为空时**没有判据**，调用方应记 None。"""
    if not expected:
        return False
    got = extract(answer or "")
    return any(pair in got for pair in expected)

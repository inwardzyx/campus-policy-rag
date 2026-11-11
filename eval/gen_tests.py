# -*- coding: utf-8 -*-
"""eval/gen_tests.py —— 把 held-out 题目转成 promptfoo 的 tests.yaml

为什么要生成、而不是手写：
  105 道题的题面、id、类型都活在 `evalset/heldout/questions.json` 里。手抄一份到 YAML，
  两边迟早不一致 —— 而不一致时的症状是"分数变了"，不是"文件对不上"，极难发现。
  ⇒ `tests.yaml` 是**派生物**，只由本脚本写；断言里那条 drift 检查负责在运行期抓旧文件。

跑法（仓库根目录）：
    python eval/gen_tests.py
产物：
    eval/tests.yaml
"""
import json
import os
import sys
from pathlib import Path

# Windows 的 GBK 管道会把中文打崩，且报出来的那句话还是假的（见 ~/.dsh/AGENTS.md）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
# 仓库根 = eval/ 的上一级；本机把 eval/ 放在别处调试时用 POLICY_RAG_REPO 指过去
REPO_ROOT = Path(os.environ.get("POLICY_RAG_REPO") or HERE.parent)
QUESTIONS = REPO_ROOT / "evalset" / "heldout" / "questions.json"
OUT = HERE / "tests.yaml"

# 每类题挂哪些判据：应答题看「检索到没到」+「有没有被误拒」，拒答题只看「有没有被误答」
CHECKS_BY_TYPE = {
    "answer": [("recall5", "gold 条款进了粗捞 top-5（Recall@5）"),
               ("no_miss", "knowledge_hit==true（没被误拒）")],
    "refuse": [("refuse_ok", "knowledge_hit==false（拒对了）")],
}


def q(v) -> str:
    """YAML 里的字符串：JSON 的双引号标量是合法 YAML，且中文不用转义。"""
    return json.dumps(v, ensure_ascii=False)


def main() -> int:
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    qs = data["questions"] if isinstance(data, dict) else data

    seen = set()
    for x in qs:
        if x["id"] in seen:
            print(f"[FAIL] id 重复：{x['id']} —— 配对比较会把两道题当成一道", file=sys.stderr)
            return 2
        seen.add(x["id"])
        if x.get("type") not in CHECKS_BY_TYPE:
            print(f"[FAIL] {x['id']} 的 type={x.get('type')!r} 不在 {list(CHECKS_BY_TYPE)}", file=sys.stderr)
            return 2

    lines = [
        "# ★ 本文件由 eval/gen_tests.py 生成，别手改（改了下次生成就没了）",
        f"# 来源：evalset/heldout/questions.json（{len(qs)} 题）",
        "# ★ 必须是**裸数组**：promptfoo 用 `tests: file://tests.yaml` 载入时，",
        "#   文件里再套一层 `tests:` 会被当成「一个测试用例」，报",
        "#   「Test case must contain assert, vars, ...」。",
    ]
    for x in qs:
        lines += [
            "- vars:",
            f"    qid: {q(x['id'])}",
            f"    question: {q(x['question'])}",
            "  assert:",
        ]
        for name, desc in CHECKS_BY_TYPE[x["type"]]:
            lines += [
                f"    # {desc}",
                "    - type: javascript",
                "      value: file://assert_case.js",
                f"      metric: {name}",
                "      config:",
                f"        check: {name}",
            ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    n = {t: sum(1 for x in qs if x["type"] == t) for t in CHECKS_BY_TYPE}
    n_assert = n["answer"] * 2 + n["refuse"]
    print(f"[ok] 已写 {OUT}：{len(qs)} 题 "
          f"= answer {n['answer']} + refuse {n['refuse']}；断言 {n_assert} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

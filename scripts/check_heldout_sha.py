# -*- coding: utf-8 -*-
"""held-out 评测集的校验和 —— 回答"评测集内容变了、版本号没变"这件事。

## 为什么需要它（2026-11-11 盲审指出的一个真盲区)

`kb/runstate.py` 的指纹能钉住「代码 + docs 语料 + 环境」，但**钉不住 held-out 那 105 题**：
原来 `evalset/heldout/questions.json` 没有任何校验和或版本字段，只有一段自由文本 `_说明`。
于是"悄悄改了一道题的 gold 再跑一遍"这种事故，指纹不会变、报告不会变，
分数变了也只会被解释成"模型抖动"。

⇒ 这里给题目文件钉一个 sha256。规则很简单：
  **改题 ⇒ 必须同时更新 `evalset/heldout/questions.sha256`**（= 主动承认"这是新版本"），
  否则本检查器报红。

## 用法

    python scripts/check_heldout_sha.py
    python scripts/check_heldout_sha.py --questions <别的题目文件>   # 跑变异用例时用

退出码：0 = 一致；1 = 不一致（内容变了但 sha 没更新）；2 = 契约本身缺了（读不到文件/没有 sha 行）

## 反向验证（`tests/test_heldout_sha.py` 里常驻）

把 `questions.json` 拷一份、改**一个字符**，用 `--questions` 指过去 ⇒ 必须 exit 1。
"""
import argparse
import hashlib
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = ROOT / "evalset" / "heldout" / "questions.json"
SHA_FILE = ROOT / "evalset" / "heldout" / "questions.sha256"


def read_expected(sha_file: Path) -> tuple[str | None, str | None]:
    """从 sha 文件里读出一行（格式同 `sha256sum`：`<hex>  <path>`）。"""
    if not sha_file.exists():
        return None, f"没有 sha 文件：{sha_file}（契约没建立，不能算通过）"
    for line in sha_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            return parts[0].lower(), None
        return None, f"{sha_file} 里的 sha 行格式不对：{line!r}"
    return None, f"{sha_file} 里一条 sha 行都没有（空文件不能算通过）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--sha-file", default=str(SHA_FILE))
    args = ap.parse_args()

    q = Path(args.questions)
    if not q.exists():
        print(f"[FAIL] 读不到题目文件：{q}", file=sys.stderr)
        return 2
    expected, why = read_expected(Path(args.sha_file))
    if expected is None:
        print(f"[FAIL] {why}", file=sys.stderr)
        return 2

    actual = hashlib.sha256(q.read_bytes()).hexdigest()
    if actual != expected:
        print(f"[FAIL] held-out 题目内容与 questions.sha256 不一致：", file=sys.stderr)
        print(f"        期望 {expected}", file=sys.stderr)
        print(f"        实际 {actual}（{q}）", file=sys.stderr)
        print("       ⇒ 改题就把 questions.sha256 一起更新（那等于宣布「这是新版本」），"
              "并把基线重跑一遍；两份旧报告与 README 里引用它们的数会因此过期。",
              file=sys.stderr)
        return 1
    print(f"[ok] held-out 题目 sha256 一致：{actual[:16]}…（{q.name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

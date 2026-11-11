# -*- coding: utf-8 -*-
"""gate 的判据 —— 用**合成跑批**证明三件事：跨层拒绝、翻转表会列出、p 会随翻转变化。

为什么不用真实跑批做用例：真实跑批要起服务 + 建库（十几秒），而且结果会随模型变；
门禁的接线（配对 / 层校验 / p）与模型无关，用合成 JSON 就能钉住，且**确定性**。

★ 变异点（本文件保证会红的三处）：
  1. 跨层调用 → 必须 exit 2（把层校验删掉，`test_cross_layer_is_refused` 立刻红）
  2. 把一题从"对"改成"错"，翻转表必须列出它（把 losses 漏掉，`test_flip_is_listed` 红）
  3. 单边翻转变多 → p 变小（把 mcnemar 换成返回常数，`test_p_shrinks_when_flips_get_one_sided` 红）
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "eval" / "gate.py"
PY = sys.executable


def _case(qid: str, typ: str, metric_ok: dict[str, bool], layer: str = "offline") -> dict:
    """造一条 promptfoo 形状的最小 case（只保留 summarize.load_cases 读的字段）。"""
    scores = {name: (1 if ok else 0) for name, ok in metric_ok.items()}
    return {
        "vars": {"qid": qid},
        "namedScores": scores,
        "response": {"output": {"qid": qid, "type": typ, "layer": layer,
                                "boot": {}, "gold_rank": None}},
        "gradingResult": {"componentResults": []},
        "testCase": {"assert": []},
        "latencyMs": 0,
    }


def _write(path: Path, cases: list[dict]) -> Path:
    path.write_text(json.dumps({"results": {"results": cases}}, ensure_ascii=False), encoding="utf-8")
    return path


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(GATE), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})


def _answer_set(n: int) -> list[str]:
    return [f"q{i}" for i in range(1, n + 1)]


def test_cross_layer_is_refused(tmp_path):
    qids = _answer_set(20)
    a = _write(tmp_path / "a.json", [_case(q, "answer", {"recall5": True}) for q in qids])
    b = _write(tmp_path / "b.json", [_case(q, "answer", {"recall5": True}, layer="online") for q in qids])
    r = _run("--a", str(a), "--b", str(b), "--metric", "recall5")
    assert r.returncode == 2, f"跨层居然没被拒绝：{r.stdout}{r.stderr}"
    assert "跨层" in r.stderr


def test_flip_is_listed(tmp_path):
    """A 全对；B 有 2 题变错、2 题原本错现在对（构造 4 个翻转）。"""
    qids = _answer_set(20)
    a = _write(tmp_path / "a.json", [_case(q, "answer", {"recall5": True}) for q in qids])
    b_cases = []
    for i, q in enumerate(qids):
        ok = True
        if q in ("q1", "q2"):
            ok = False          # 变差
        if q in ("q3", "q4"):
            pass                # 本来就好，不变
        b_cases.append(_case(q, "answer", {"recall5": ok}))
    # 另造两题 A 错 B 对（变好）
    a_cases = [_case(q, "answer", {"recall5": q not in ("q5", "q6")}) for q in qids]
    b_cases = [_case(q, "answer", {"recall5": q not in ("q1", "q2")}) for q in qids]
    a = _write(tmp_path / "a2.json", a_cases)
    b = _write(tmp_path / "b2.json", b_cases)
    r = _run("--a", str(a), "--b", str(b), "--metric", "recall5")
    assert r.returncode == 0, r.stderr
    assert "| q1 |" in r.stdout and "| q2 |" in r.stdout, f"翻转表没列出变差的题：\n{r.stdout}"
    assert "| q5 |" in r.stdout and "| q6 |" in r.stdout, f"翻转表没列出变好的题：\n{r.stdout}"


def test_p_shrinks_when_flips_get_one_sided(tmp_path):
    """2 好 2 差（p=1.0）→ 3 好 1 差（同边）⇒ p 必须变小。

    构造方式：用「哪些题是错的」这个集合来定义一份跑批，翻转就从集合差里直接读出来。
    """
    qids = _answer_set(20)

    def build(name: str, bad: set[str]) -> Path:
        return _write(tmp_path / name, [_case(q, "answer", {"recall5": q not in bad}) for q in qids])

    # 平衡：A 错 {q1,q2}，B 错 {q3,q4} ⇒ G=2（q3,q4）/ L=2（q1,q2）⇒ p=1.0
    a_bal, b_bal = build("a_bal.json", {"q1", "q2"}), build("b_bal.json", {"q3", "q4"})
    # 单边：A 错 {q1..q4}，B 错 {q1, q10} ⇒ G=3（q2,q3,q4）/ L=1（q10）⇒ p=0.625
    a_one, b_one = build("a_one.json", {"q1", "q2", "q3", "q4"}), build("b_one.json", {"q1", "q10"})

    r1 = _run("--a", str(a_bal), "--b", str(b_bal), "--metric", "recall5")
    r2 = _run("--a", str(a_one), "--b", str(b_one), "--metric", "recall5")
    assert r1.returncode == 0 and r2.returncode == 0, (r1.stderr, r2.stderr)

    def p_of(out: str) -> float:
        import re
        m = re.search(r"McNemar 精确 p = ([\d.]+)", out)
        assert m, f"输出里找不到 p：\n{out}"
        return float(m.group(1))

    p_balanced, p_one_sided = p_of(r1.stdout), p_of(r2.stdout)
    assert p_balanced == 1.0, f"2 好 2 差应当是 p=1.0，实际 {p_balanced}"
    assert p_one_sided < p_balanced, f"翻转变单边后 p 应当变小：{p_one_sided} vs {p_balanced}"

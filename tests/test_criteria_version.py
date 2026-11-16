# -*- coding: utf-8 -*-
"""`kb/criteria.py` 的判据：判据版本必须**真的会随判据变化而变化**。

★ 这条不是测"哈希函数对不对"，而是测它**能不能红**：
  如果 `version()` 只对文件名聚合、或者缓存了结果，那么改了 gate 语义版本也不变 ——
  于是旧报告继续绿，而分数已经不可比了。这才是要防的失败。
"""
import os
import shutil
import sys

os.environ["LANGSMITH_TRACING"] = "false"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import kb.criteria as criteria                                  # noqa: E402


def test_version_is_stable_for_same_content():
    assert criteria.version() == criteria.version()
    assert len(criteria.version()) == 8


def test_all_criteria_files_exist():
    """清单里的文件必须都在 —— 少了文件版本照样算得出来，但那就成了"判据没变"的假象。"""
    for rel in criteria.CRITERIA_FILES:
        assert os.path.exists(os.path.join(REPO_ROOT, rel)), rel


def test_version_changes_when_a_criteria_file_changes(tmp_path):
    """★ 核心：在临时副本里改一个字节，版本必须变。"""
    for rel in criteria.CRITERIA_FILES:
        src = os.path.join(REPO_ROOT, rel)
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    before = criteria.version(str(tmp_path))

    target = tmp_path / "agent" / "loop.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# mutation\n", encoding="utf-8")
    after = criteria.version(str(tmp_path))

    assert before != after, "改了判据文件而版本没变 —— 旧报告会继续被判为可比"


def test_missing_file_changes_version(tmp_path):
    """缺失 ≠ 没变：删掉一个判据文件，版本也必须不同。"""
    for rel in criteria.CRITERIA_FILES:
        src = os.path.join(REPO_ROOT, rel)
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    before = criteria.version(str(tmp_path))
    (tmp_path / "agent" / "loop.py").unlink()
    assert criteria.version(str(tmp_path)) != before

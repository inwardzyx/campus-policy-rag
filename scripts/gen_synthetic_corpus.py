# -*- coding: utf-8 -*-
"""生成**合成制度语料**，只为「向量检索规模基准」服务。

## 为什么不直接扩真实语料

真实制度文档每一份都要人工写、要核对条款，扩到 1000 块不现实。
而向量库的选型只跟**规模**有关（召回率、p50 延迟随 N 变化），跟条款内容是否真实无关。
⇒ 用结构化模板生成"形状一致"的语料：每份文档 = 若干条 `## 第 N 条【主题】`，
和真实语料同一切分方式（`kb/source.chunk_markdown` 按标题切），每条 = 一块。

## ★ 边界（必须写清楚，别污染评测）

- 生成到单独 namespace：`synth`（不是 `company` / 不是 `docs`）
- **不进任何评测**：`eval/` 里的题目和报告只认 docs/kb、company 这些真实语料
- 生成结果不入库（`kb_data/synth/` 已在 .gitignore 里），只入 `experiments/vector-store-bench.md` 的结论

## 用法

```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\gen_synthetic_corpus.py --docs 34
```
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb.source import content_hash, doc_id_for          # noqa: E402

TOPICS = [
    ("考勤管理", ["迟到", "早退", "旷工", "打卡", "外出", "补卡", "排班", "加班审批"]),
    ("薪酬管理", ["固定工资", "绩效工资", "津贴", "补贴", "代发", "调薪", "工资条"]),
    ("报销管理", ["差旅报销", "交通费", "住宿费", "餐补", "发票", "审批流", "超标"]),
    ("采购管理", ["采购申请", "比价", "招标", "供应商准入", "验收", "付款", "合同"]),
    ("信息安全", ["账号", "口令", "数据分级", "外发", "终端", "漏洞", "应急响应"]),
    ("出差管理", ["出差申请", "行程变更", "住宿标准", "交通标准", "差旅补助"]),
    ("培训管理", ["新员工培训", "在岗培训", "外部培训", "培训协议", "考核"]),
    ("绩效管理", ["目标设定", "中期回顾", "评级", "申诉", "强制分布"]),
    ("招聘管理", ["需求提报", "面试流程", "背景调查", "offer 审批", "入职"]),
    ("离职管理", ["离职申请", "交接", "竞业限制", "离职证明", "结算"]),
    ("保密管理", ["商业秘密", "保密协议", "涉密载体", "对外披露"]),
    ("供应商管理", ["准入", "评级", "黑名单", "年度评审"]),
    ("质量管理", ["来料检验", "过程检验", "不合格品", "纠正措施"]),
    ("安全生产", ["隐患排查", "事故报告", "劳保用品", "特种作业"]),
    ("环境保护", ["废弃物", "排放", "应急预案", "监测"]),
    ("车辆管理", ["用车申请", "油费", "保养", "事故处理"]),
    ("食堂管理", ["就餐", "食品安全", "留样", "投诉"]),
    ("宿舍管理", ["入住", "退宿", "用电安全", "卫生检查"]),
    ("门禁管理", ["工牌", "访客", "权限申请", "异常记录"]),
    ("IT 资产管理", ["领用", "归还", "报废", "盘点", "数据擦除"]),
    ("合同管理", ["起草", "评审", "用印", "归档", "履约跟踪"]),
    ("印章管理", ["用印申请", "审批", "登记", "外带"]),
    ("档案管理", ["归档范围", "保管期限", "借阅", "销毁"]),
    ("会议管理", ["会议申请", "纪要", "决议跟踪"]),
    ("车辆油卡", ["充值", "对账", "异常"]),
    ("安全管理", ["消防", "应急演练", "安保巡检"]),
    ("工会与福利", ["节日福利", "体检", "慰问", "活动经费"]),
    ("知识产权", ["专利", "软著", "商标", "归属"]),
    ("客户投诉", ["受理", "分级", "时限", "回访"]),
    ("数据治理", ["主数据", "质量校验", "血缘", "权限"]),
]

VERBS = ["应当", "必须", "不得", "原则上", "可以"]
LIMITS = ["2 个工作日", "3 个工作日", "5 个工作日", "10 个工作日", "当月", "当季", "年度"]
PENALTY = ["书面提醒", "扣减当月绩效 2 分", "计一次违规", "通报批评", "上级审批后方可豁免"]


def make_doc(topic: str, keys: list[str], n_clauses: int, serial: int) -> str:
    lines = [f"# {topic}管理办法（合成语料 #{serial:02d}）", ""]
    for i in range(1, n_clauses + 1):
        key = keys[(i - 1) % len(keys)]
        depth = (i * 7 + serial * 3) % 5
        verb = VERBS[depth % len(VERBS)]
        limit = LIMITS[(i + serial) % len(LIMITS)]
        pen = PENALTY[(i * 2 + serial) % len(PENALTY)]
        lines.append(f"## 第{i}条【{key}】")
        lines.append(
            f"涉及{key}的事项，{verb}在{limit}内完成申请与审批；"
            f"申请人须在系统提交说明并附相关材料，审批人对材料完整性负责。"
            f"未按要求办理的，{pen}；造成损失的，按第三条与第四十四条处理。"
        )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=34)
    ap.add_argument("--clauses", type=int, default=30)
    ap.add_argument("--ns", default="synth")
    a = ap.parse_args()

    base = ROOT / "kb_data" / a.ns
    (base / "files").mkdir(parents=True, exist_ok=True)
    manifest = []
    now = datetime.now(timezone.utc).isoformat()
    for i in range(a.docs):
        topic, keys = TOPICS[i % len(TOPICS)]
        title = f"{topic}管理办法（合成 #{i+1:02d}）"
        text = make_doc(topic, keys, a.clauses, i + 1)
        did = doc_id_for(a.ns, f"{title}.md")
        (base / "files" / f"{did}.md").write_text(text, encoding="utf-8")
        manifest.append({
            "namespace": a.ns, "doc_id": did, "title": title,
            "source": f"synthetic:{title}.md", "version": "1", "updated_at": now,
            "created_at": now, "acl": ["public"], "content_hash": content_hash(text),
            "effective_date": "", "status": "active", "tombstone": False,
            "suffix": ".md", "file": f"{did}.md",
        })
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"[ok] {a.ns}: {len(manifest)} 份文档 × {a.clauses} 条 ≈ {len(manifest)*a.clauses} 块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

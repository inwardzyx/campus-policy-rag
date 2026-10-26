# -*- coding: utf-8 -*-
"""兼容层：`kb.eval_metrics` 的旧名字转发到 `kb.evalkit.metrics`。

## 为什么要留这一层
`tests/test_eval_attribution.py`、`scripts/run_scenario_eval.py` 以及几份报告生成流程
都 import 了 `kb.eval_metrics`。评测内核拆进 `kb/evalkit/` 之后，
**旧入口不该因此改一行** —— 改 import 是小事，但它会让"这次改动到底动了什么"变模糊。

★ 新代码请直接用 `from kb import evalkit`；这一层只是兼容，不再加功能。
"""
from __future__ import annotations

from kb.evalkit.metrics import (METRICS, MetricSpec, _citation_field,  # noqa: F401
                                _included, _mean, _ndcg, _rate, aggregate,
                                metric_specs, register_metric, score_row)

__all__ = ["METRICS", "MetricSpec", "aggregate", "score_row", "register_metric",
           "metric_specs", "_ndcg", "_mean", "_citation_field", "_included", "_rate"]

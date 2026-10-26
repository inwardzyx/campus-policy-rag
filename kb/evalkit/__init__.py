# -*- coding: utf-8 -*-
"""evalkit —— 请求级评测的**可复用**内核（cases / metrics / report / transport）。

## 为什么要有这个包（而不是继续堆脚本）
请求级评测现在有：场景 runner、6 个指标、六桶归因、审计、CI 回归。
再往后还会有：会话轨迹评测、工具轨迹评测、不同语料/场景的套件。
如果每个都写一个脚本，就会重演本仓库最贵的教训：**同一颗炸弹抄两份**。

所以拆成四个可替换的部件，任何一个新入口都只是"组装"：

| 部件 | 职责 | 换掉它意味着 |
|---|---|---|
| `cases` | 题集 schema + 校验 + 套件解析 | 换成新题型（会话/工具轨迹） |
| `metrics` | 指标**注册表** + 逐题判分 | 加指标只加一条注册，不改 runner |
| `report` | 带 `schema_version` 的报告 + 打印 | 接看板/CI 只认这个 schema |
| `transport` | HTTP / 录好的响应 / 直连管道 | 换执行方式（不联网、不起服务） |

## 设计上的三条硬规矩
1. **缺失有名字**：每个指标都要声明"拿不到时是什么、为什么"（`MetricSpec.missing`），
   `None` 不许被 0 冒名；报告自带指标说明，消费方不用猜。
2. **空转报红**：0 道题不是"通过"，`validate_cases` 会把它当错误。
3. **加指标不改 runner**：`register_metric()` 之后，`aggregate()` 与报告自动带上它。
"""
from __future__ import annotations

from kb.evalkit.cases import (VALID_TYPES, list_suites, load_cases,  # noqa: F401
                              normalize_case, resolve_suite, validate_cases)
from kb.evalkit.metrics import (METRICS, MetricSpec, aggregate,  # noqa: F401
                                metric_specs, register_metric, score_row)
from kb.evalkit.report import (SCHEMA_VERSION, build_report,  # noqa: F401
                               format_summary, write_report)
from kb.evalkit.transport import (HttpTransport, InProcTransport,  # noqa: F401
                                  RecordedTransport, make_transport)

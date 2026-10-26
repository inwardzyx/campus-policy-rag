# -*- coding: utf-8 -*-
"""连接器：把"外部系统"收敛成两个动作（列文档 / 取正文），同步逻辑只有一份。

| 文件 | 作用 |
|---|---|
| `core.py` | `Connector` 协议 + 增量同步引擎（哈希/ACL/元数据三重比较） |
| `fixture.py` | 本地目录连接器（无外部依赖，离线验收增量与权限同步） |
| `feishu.py` | 飞书 wiki 连接器（复用 `kb/feishu.py` 的客户端） |

设计要点见 `core.py` 顶部：**变更 = 内容哈希 ∪ ACL ∪ 元数据**，不是只看时间戳；
删除只作用于连接器**自己拥有**的文档（按 source 前缀判定），否则会误删共享 namespace 里别人上传的语料。
"""
from __future__ import annotations

from kb.connectors.core import Connector, ConnectorDoc, SyncReport, sync_connector  # noqa: F401

__all__ = ["Connector", "ConnectorDoc", "SyncReport", "sync_connector"]

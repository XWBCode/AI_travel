"""AI 旅行助手 - Python AI 服务。

职责边界（项目总纲 3.3）：
- 只做 LLM 调用、Prompt 管理、上下文组织、Agent 编排、工具调用、RAG 检索。
- 不直接连接 MySQL，不持有用户权限数据，业务数据一律通过 Java 内部接口获取。
"""

__version__ = "0.1.0"

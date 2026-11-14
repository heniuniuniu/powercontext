"""sddctx —— 面向软件持续演进的 SDD 工程上下文模块。

模块职责：
1. 把规格、代码、测试导入 seekdb／OceanBase（工程资料与索引）；
2. 根据开发任务检索出**当前有效**的规格条目、待改代码符号与验证测试；
3. 把任务进展与验证结果写入 PowerContext，重启后读取并复用；
4. 把组装好的上下文交给使用 MiniMax 的开发助手完成代码修改。

对外入口见 :mod:`sddctx.cli`（命令行）与 :mod:`sddctx.server`（HTTP API）。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]

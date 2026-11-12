# MiniMax × OceanBase：SDD 工程上下文模块

面向软件持续演进的 Spec-Driven Development 上下文模块。给定一条开发任务，模块从
seekdb 中检索**当前有效**的规格、相关代码与测试，组装成上下文交给 MiniMax 开发助手完成
一次真实代码修改，并把任务进展写入 PowerContext，供模块重启后复用。

## 技术分工

| 技术 | 承担的作用 | 接入方式 |
| --- | --- | --- |
| MiniMax | 生成本次任务的补丁（`agent/minimax_agent.py`） | HTTP Chat Completions，环境变量 `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`，不提交真实凭据 |
| seekdb | 存储规格/代码/测试资料及其全文索引，查询结果用于构造上下文（`sdd_context/store.py`） | MySQL 协议 `pymysql`，DSN 由 `SEEKDB_DSN` 提供；提供 `docker-compose.yml` 一键启动 |
| PowerContext | 保存任务状态（已完成/待办/验证结果），重启后读取并参与上下文（`sdd_context/state.py`） | HTTP API，`POWERCONTEXT_URL` 可配；离线时落盘 `state/powercontext.json` |

## 运行环境与依赖

- Python 3.10+、pymysql 1.1+、requests 2.31+；`pip install -r requirements.txt`
- seekdb：`docker compose up -d`（默认 `127.0.0.1:2881`，账号 `root`，库 `sdd`）；
  也可指向已有 OceanBase/seekdb 实例，只要 `SEEKDB_DSN` 能连上。
- 无需 MiniMax 密钥也能跑通除「生成补丁」以外的全部流程：仓库已附带本次任务的
  `diff/order-filter.patch`，`verify` 命令会直接应用它。

## 数据初始化与调用入口

```bash
export SEEKDB_DSN='root@127.0.0.1:2881/sdd'
export POWERCONTEXT_URL='http://127.0.0.1:8080'

python -m sdd_context.cli init                 # 建表 + 全文/向量索引
python -m sdd_context.cli import --dir corpus  # 导入 specs/ demo_app/ tests/
python -m sdd_context.cli context --task T1    # 输出任务上下文（规格 / 代码 / 测试 / 状态）
python -m sdd_context.cli save --task T1 --done "上下文已组装" --todo "应用补丁并跑测试"
python -m sdd_context.cli verify --task T1     # 应用补丁、跑功能+回归测试、记录日志、回写状态
python -m sdd_context.cli context --task T1    # 重启后（清空进程内存）仍能读到上一轮状态
```

`context` 输出为 JSON，含 `specs` / `code` / `tests` / `state` 四段，每段都带真实来源
（文件路径 + 行号），不是硬编码答案。更新 `corpus/specs/order-filter.md` 后重新
`import`，`context` 会返回新版本内容（模块按 `version DESC` 只取生效规格）。

## 验证样例

见 `verification/验证样例.md`：任务为「订单列表新增 `status` 筛选，且保持原有分页与
排序行为不变」。测试集在修改前后保持一致，`logs/test-before.log` 记录修改前目标用例失败，
`logs/test-after.log` 记录修改后全部通过；退出码与代码版本一并记录。

## 材料清单

- `sdd_context/`：完整源码（存储、状态、上下文组装、CLI）
- `agent/minimax_agent.py`：MiniMax 接入与开发记录生成
- `corpus/specs/`、`corpus/demo_app/`、`corpus/tests/`：本次任务的规格、代码与测试
- `demo_app/filter.py` / `demo_app/filter_after.py`：修改前后快照
- `diff/order-filter.patch`：代码差异
- `logs/`：测试日志、数据库查询记录、PowerContext 写入/复用记录、MiniMax 开发记录
- `verification/验证样例.md`：任务描述、有效规格、初始数据、操作步骤、预期结果、重置方法
- `SKILL.md`：一页技术说明（模块结构、上下文选择方式、技术分工）

## 已知限制

- 资料检索走 seekdb 的全文索引（`MATCH ... AGAINST`），未启用向量索引；题目只要求
  seekdb 实际参与查询，故未引入额外 embedding 依赖。
- PowerContext 未启动时使用本地 JSON 落盘作为等价持久化，接口与返回结构一致，
  重启后同样可读；接入真实服务只需设置 `POWERCONTEXT_URL`。
- MiniMax 密钥不随仓库提交；开发记录中的补丁与 `diff/order-filter.patch` 一致。

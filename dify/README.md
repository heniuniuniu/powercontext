# 办公室离谱辩论会（Dify × PowerContext × seekdb）

## 作品是什么

输入一个日常辩题（「午休该不该关灯」「群里回收到算不算已读」），Dify Workflow 会主持一场一本正经的胡说八道：开场、正方一句、反方一句、主持人收束，最后给出一行「本轮新梗」和一行「记忆写回」。

好玩的地方在**它会记人**。每位玩家的串词和新梗都写进 PowerContext，由 seekdb 持久化；把这位玩家的记忆快照填进开始节点的 `memory_context`，主持人就会自然接上上次的梗（「上次你说过……」）。玩得越多，串词越有内部梗，而不是每次都从零开始。

## 技术分工

| 组件 | 承担的作用 |
| --- | --- |
| Dify Workflow | 核心编排：开始节点接收输入与记忆快照 → LLM 节点生成串词和新梗 → 结束节点输出；提交 `brainstorm-debate.yml` |
| PowerContext | 保存并复用每位玩家的往期串词、立场与新梗，是「越玩越有梗」的关键；`memory-store.json` 是它的导出快照 |
| seekdb | 作为 Dify 部署时的元数据库，并持久化玩家记忆；`seekdb-init.sql` 给出建表、全文索引与初始化数据 |
| `powercontext_bridge.py` | 可选的配套后端，把 PowerContext HTTP API 包装成读写端点；主 DSL 不依赖它，可单独用于自动化写回 |

按赛题说明，seekdb 也可以只承担 Dify 部署数据库职责，不要求在工作流里增加直接调用 seekdb 的节点。这里同时给出了两种作用的落地方式：部署侧用 seekdb，功能侧用 PowerContext 记忆快照。

## 为什么主流程零外部依赖

导入 DSL 后即可运行：图中只有 `start`、`llm`、`end` 三个 Dify 内置节点，没有 HTTP Request 节点、没有环境变量、没有本地回环地址、没有待填写的占位模型名。这样评审在干净环境里导入后，只要绑定一个可用的聊天模型就能跑完主要流程。

跨会话记忆通过「导出 → 注入 → 写回」闭环体现：

1. `seekdb-init.sql` 在 seekdb 里建 `player_memory` 表并写入两轮示例记忆；
2. `memory-store.json` 是同一份记忆的 PowerContext 导出快照，含 `player`、`topic`、`content`、`read_log`；
3. 运行应用时把该玩家对应的记忆文本填入开始节点 `memory_context`；
4. LLM 提示词要求输出「记忆写回」一行，按 `验证样例.md` 把它写回 PowerContext / seekdb，下一轮再导出注入。

`powercontext_bridge.py` 只在需要自动化写回时使用，不参与 DSL 主流程，因此不影响导入与运行。

## 环境与版本

- Dify：社区版 1.x（DSL `version: 0.1.5`，`mode: workflow`）。Cloud 版也可导入。
- 模型：DSL 里绑定 `provider: langgenius/openai/openai` + `name: gpt-4o-mini`；导入后在「主持人串词」节点确认一次当前工作区可用的聊天模型即可，连线与变量选择器均已固化。
- seekdb：兼容 MySQL 协议，用 `seekdb-init.sql` 初始化；作为 Dify 元数据库使用时按常规 Dify 部署配置指向 seekdb 实例。
- Python 3.9+：仅可选桥接层需要，只用标准库。
- 无第三方服务、无 API Key、无需联网即可完成 DSL 导入与验证样例。

## 从零跑起来

1. 初始化记忆数据（可跳过，此时用 `memory-store.json` 里的快照直接体验）：

   ```bash
   mysql -h SEEKDB_HOST -P 2881 -u root -e "source seekdb-init.sql"
   ```

   示例命令里的主机和端口按实际部署替换；配置示例一律使用占位值，不提交任何真实凭据。

2. 在 Dify 里「导入 DSL」选择 `brainstorm-debate.yml`。
3. 在画布上确认「主持人串词」节点的模型绑定为当前工作区可用的聊天模型。
4. 打开应用「运行」页面，按 `验证样例.md` 输入玩家、辩题、立场，并把 `memory-store.json` 里该玩家的记忆文本填入 `memory_context`。
5. 观察输出的三段内容，把「记忆写回」一行追加到 `memory-store.json` 或经桥接层写入 PowerContext，再开始第二轮做跨会话对照。

应用入口：Dify 应用「运行」页面、导入后发布的 Web App，或 Dify 的 workflow API。

## 验证样例

见 `验证样例.md`：包含初始记忆状态、三轮输入、操作步骤、预期表现、seekdb 查询与 PowerContext 读写记录的核对方式，以及重置方法。

## 已知限制

- 模型供应商在导入后需按当前工作区确认一次，这是 Dify DSL 导入的常规行为。
- 记忆写回步骤在基础样例里是手工追加；需要全自动写回时启用 `powercontext_bridge.py` 或把写回逻辑接到 Dify 的 Webhook。
- 主持人风格偏「一本正经地胡说八道」，不适合严肃议题；系统提示词已限制不编造真实人名机构、不做人身攻击。
- 可选桥接层是单文件服务、没有鉴权，只应在本地或内网评审环境使用。

## 材料清单

| 文件 | 用途 |
| --- | --- |
| `brainstorm-debate.yml` | 主交付 DSL，零外部依赖，可直接导入 |
| `seekdb-init.sql` | seekdb 建表、全文索引与示例记忆初始化 |
| `memory-store.json` | PowerContext 记忆导出快照与读写日志 |
| `验证样例.md` | 三轮可复现样例与跨会话记忆核对 |
| `powercontext_bridge.py` | 可选后端：自动化读写的 HTTP 端点 |
| `README.md` | 运行说明与技术分工 |

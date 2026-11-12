# 办公室离谱辩论会（Dify × PowerContext）

## 作品是什么

输入一个日常辩题（「午休该不该关灯」「群里回收到算不算已读」），Dify Workflow 会主持一场一本正经的胡说八道：开场、正方一句、反方一句、主持人收束，最后给出一行「本轮新梗」。

好玩的地方在**它会记人**。每位玩家的串词和新梗都写进 PowerContext；同一昵称下次再来，主持人会自然接上上次的梗（「上次你说过……」）。玩得越多，串词越有内部梗，而不是每次都从零开始。

## 技术分工

| 组件 | 承担的作用 |
| --- | --- |
| Dify Workflow | 核心编排：接收输入 → 读取记忆 → 模型生成 → 写回记忆 → 输出；提交 `brainstorm-debate.yml` DSL |
| PowerContext | 保存并复用每位玩家的往期串词与新梗，跨会话生效，是本作品「越玩越有梗」的关键 |
| `powercontext_bridge.py` | 把 PowerContext HTTP API 包装成 Dify HTTP Request 节点可直接调用的两个端点，并记录读写日志 |

PowerContext 确实参与了功能：同一玩家第二次运行时，`/memory/read` 会返回第一次写入的内容，串词里会出现对旧梗的呼应。**只连一下不算数**，验证样例里有可核对的读写记录。未配置 PowerContext 时桥接层会退化成本地 JSON，方便评审在干净环境里先把流程跑通，响应体的 `source` 字段会明确标注当前模式。

## 环境与版本

- Dify：社区版 1.x（DSL `version: 0.1.5`，`mode: workflow`）。Cloud 版也可导入。
- 模型：DSL 里已写成可直接导入的 `provider: langgenius/openai/openai` + `name: gpt-4o-mini`；
  若你的 Dify 装的是别的模型插件，导入后在「主持人串词」节点重新选一次即可，流程不变。
- Python 3.9+，桥接层只用标准库，无第三方依赖。
- 网络：Dify 容器需要能访问桥接服务。Docker 部署时用 `http://host.docker.internal:8765`；Linux 可用宿主机 IP 或把两个服务放进同一 compose 网络。

## 从零跑起来

1. 启动桥接层（PowerContext 地址用占位值，按实际版本替换）：

   ```bash
   cd dify
   export PC_API_BASE="https://<your-powercontext-host>"
   export PC_API_KEY="<PLACEHOLDER_API_KEY>"
   export PC_READ_PATH="/v1/memories/search"
   export PC_WRITE_PATH="/v1/memories"
   python3 powercontext_bridge.py
   curl http://127.0.0.1:8765/health
   ```

   不配置 `PC_API_BASE` 也能启动，此时走本地 `memory-store.json` 兜底。端点路径按所用 PowerContext 版本调整，改环境变量即可，不用改代码。

2. 在 Dify 里「导入 DSL」选择 `brainstorm-debate.yml`。
3. 导入后在画布上做两处确认：`主持人串词` 节点的模型绑定为当前工作区可用的聊天模型；把环境变量 `PC_BRIDGE_URL` 指到桥接层（默认 `http://host.docker.internal:8765`）。DSL 中所有节点与连线已完整连通，不含任何占位模型名。
4. 打开应用的运行界面，按 `验证样例.md` 输入三组参数，观察输出与桥接层日志。
5. 应用入口：Dify 应用「运行」页面，或导入后发布的 Web App；也可用 Dify 的 workflow API 调用。

## 验证样例

见 `验证样例.md`，包含初始状态、输入、操作步骤、预期表现，以及 PowerContext 读写记录的核对方式和重置方法。

## 已知限制

- DSL 里的模型与变量选择器在导入后需手动绑定一次，这是 Dify DSL 导入的常规行为。
- 主持人风格偏「一本正经地胡说八道」，不适合严肃议题；系统提示词已限制不编造真实人名机构、不做人身攻击。
- 桥接层是单文件服务，没有鉴权，只应在本地或内网评审环境使用；对外部署请自行加网关鉴权。
- 兜底模式的记忆落在 `memory-store.json`，仅用于演示，不替代 PowerContext 的实际参与。

## 材料清单

| 文件 | 用途 |
| --- | --- |
| `brainstorm-debate.yml` | Dify Workflow DSL，导入顺序：仅此一个文件 |
| `powercontext_bridge.py` | 记忆读写桥接服务及日志 |
| `README.md` | 本运行说明 |
| `验证样例.md` | 可复现的验证步骤与证据记录 |
| `memory-store.json` | 运行后生成，记录实际读写内容（提交时保留一次验证的结果） |

配置示例统一使用 `<PLACEHOLDER_API_KEY>` 一类占位值，不含真实凭据。DSL 结构参考 Dify 官方 workflow 导出格式编写，未复用第三方工作流代码。

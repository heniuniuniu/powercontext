# 运行说明（sddctx 上下文模块）

> 适用作品：规有所依——面向软件持续演进的 SDD 上下文管理系统
> 目录：`minimax/`

---

## 0. 一句话概览

```bash
cd minimax
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
.venv/bin/powercontext server run --env-file powercontext.env &     # 启动 PowerContext（任务状态存储）
.venv/bin/python scripts/verify.py                                  # 端到端验证（导入→检索→状态→MiniMax 改码→测试）
```

---

## 1. 环境依赖

| 组件 | 版本 | 用途 | 备注 |
| --- | --- | --- | --- |
| Python | ≥ 3.11（实测 3.13.9 / Windows） | 运行环境 | |
| `pylibseekdb` | 1.4.0.post2 | **嵌入式 seekdb**（OceanBase seekdb v1.4.0.0） | Windows / Linux / macOS 均有官方 wheel |
| `pymysql` | ≥ 1.1.1 | 走 MySQL 协议访问 seekdb / OceanBase | |
| `powercontext` | 1.5.1 | 任务状态持久化（本地 Server + Python Client SDK） | 需要 `typer` 才能使用其 CLI |
| `httpx` | ≥ 0.28 | 调用 MiniMax Messages API | |
| `pytest` | ≥ 8 | 目标功能测试 / 回归测试 | |

**三种技术的接入方式与版本**（题目要求在运行说明中注明）：

| 技术 | 接入方式 | 版本 |
| --- | --- | --- |
| seekdb | **嵌入式**：Python 绑定 `pylibseekdb`，本机后台进程 + MySQL 协议，SQL 走 `pymysql` | pylibseekdb 1.4.0.post2 / seekdb v1.4.0.0 |
| OceanBase | **同一套 SQL**，把 `SDDCTX_DB_MODE=server` 并设置 `SDDCTX_DB_HOST/PORT/USER/PASSWORD` 即可切到 OceanBase 集群 | 兼容 OceanBase 4.x MySQL 模式 |
| PowerContext | **Python Client SDK**（`powercontext.client.PowerContextClient`）+ 本地 Server（`powercontext server run`） | powercontext 1.5.1 |
| MiniMax | **HTTP API**：Anthropic Messages 协议 `POST {base}/messages` | MiniMax-M3.1-Flash |

---

## 2. 安装

```bash
cd minimax
python -m venv .venv
```

Linux / macOS：

```bash
.venv/bin/pip install -r requirements.txt
```

Windows（PowerShell）：

```powershell
.venv\Scripts\pip install -r requirements.txt
```

> **提示**：若 pip 在解析 `pylibseekdb` 时卡住（依赖回溯过慢），加 `--no-cache-dir`
> 或先单独安装：`pip install --no-cache-dir pylibseekdb==1.4.0.post2`。
> 该包在 Windows 上提供 `cp312-abi3-win_amd64` wheel，可直接用于 Python 3.12/3.13/3.14。

---

## 3. 启动 PowerContext（任务状态存储）

PowerContext 负责保存**任务进展与验证结果**，并支持模块重启后读取复用。
它是一个独立进程，需要在另一个终端保持运行。

```bash
cd minimax
.venv/bin/powercontext server run --env-file powercontext.env
```

`powercontext.env` 中只有本地路径与开关，**不含任何真实凭据**：

```
POWERCONTEXT_SERVER_WORKSPACE=./.sddctx/powercontext
POWERCONTEXT_SERVER_HTTP='{"host":"127.0.0.1","port":8000}'
POWERCONTEXT_SERVER_DATABASE='{"kind":"sqlite","url":"sqlite+aiosqlite:///./.sddctx/powercontext/powercontext.db"}'
POWERCONTEXT_SERVER_AUTH='{"enabled":false}'
POWERCONTEXT_SERVER_LOGGING='{"level":"INFO"}'
POWERCONTEXT_SERVER_MCP='{"enabled":false}'
```

> PowerContext 的 `ServerSettings` 使用 `env_prefix=POWERCONTEXT_SERVER_`、嵌套分隔符 `_`，
> **复杂字段用 JSON 值表达最不容易出错**（嵌套扁平写法在 1.5.1 上不生效）。
> 另外：`workspace` 指向的目录必须**已存在**，否则 Server 起不来。

就绪检查：

```bash
curl http://127.0.0.1:8000/health/ready      # => {"status":"ready", ...}
```

> **注意**：SQLite 后端要求 Server 以完整角色启动（不能加 `--role api`）。

---

## 4. 运行上下文模块

### 4.1 一次性端到端验证（推荐）

```bash
cd minimax
.venv/bin/python scripts/verify.py
```

它会依次完成并把每一步写进 `evidence/verify-*.json`：

| 步骤 | 内容 |
| --- | --- |
| doctor | 检查 seekdb / PowerContext / MiniMax 连通性 |
| init + ingest | 建库建表，把 `specs/`、`demo_app/`、`demo_app/tests/` 导入 seekdb |
| context | 按任务检索，输出**适用规格 / 已失效条目 / 待改代码 / 目标测试 / 回归测试** |
| state-save | 从**空状态**写入任务记录（已完成 / 待办） |
| **state-restart-read** | **在新进程中**执行 `python -m sddctx state show`，读取并复用该状态 |
| state-reused-in-context | 恢复出的状态被并入新生成的上下文 |
| minimax-agent | 把上下文交给 MiniMax，完成代码修改并落盘 |
| tests | 目标功能测试 + 回归测试 |
| state-final | 回写完成状态与验证结果 |

### 4.2 逐条命令

```bash
# 建库建表（幂等）
.venv/bin/python -m sddctx init

# 导入规格 / 代码 / 测试到 seekdb（幂等；会记录 inserted / updated / removed）
.venv/bin/python -m sddctx ingest

# 按任务检索上下文（默认输出 Markdown）
.venv/bin/python -m sddctx query "支持无门槛优惠券 threshold=0，门槛判定使用 >="
.venv/bin/python -m sddctx query --summary "<任务>"     # 只看摘要
.venv/bin/python -m sddctx query --json "<任务>"        # 完整 JSON
.venv/bin/python -m sddctx query --with-state "<任务>"  # 并入 PowerContext 中的任务状态

# 任务状态（PowerContext）
.venv/bin/python -m sddctx state save --task-id task-1 --done "已导入" --todo "待改码"
.venv/bin/python -m sddctx state show
.venv/bin/python -m sddctx state search "无门槛券"

# 完整闭环：上下文 -> MiniMax -> 落盘 -> 跑测试 -> 回写状态
.venv/bin/python -m sddctx agent --apply "支持无门槛优惠券 threshold=0，门槛判定使用 >="

# 单独跑测试（自动留存命令、退出码与输出到 evidence/）
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_threshold.py  --label target
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_regression.py --label regression

# 数据库取证
.venv/bin/python -m sddctx db --stats
.venv/bin/python -m sddctx db --sql "SELECT spec_id, status FROM spec_item ORDER BY spec_id"

# HTTP API（另一种调用入口）
.venv/bin/python -m sddctx serve --port 8765
curl "http://127.0.0.1:8765/v1/context?task=支持无门槛券"
```

---

## 5. MiniMax 凭据

按以下顺序解析（**均不写入仓库**）：

1. 环境变量 `SDDCTX_MINIMAX_API_KEY`（推荐用于评审环境）；
2. 本机 MiniMax Code 的 `~/.minimax/config.yaml` 中的 provider `apiKey`；
3. 本机 MiniMax Code 登录态 `~/.minimax/auth/**/auth.json` 中的 `accessToken`。

端点与模型可用环境变量覆盖：

```bash
export SDDCTX_MINIMAX_BASE_URL=https://agent.minimax.cn/mavis/api/v1/llm/v1
export SDDCTX_MINIMAX_MODEL=MiniMax-M3.1-Flash
export SDDCTX_MINIMAX_MAX_TOKENS=8192
export SDDCTX_MINIMAX_THINKING_BUDGET=0     # 限制思维链长度，避免吃光输出预算
```

> 仓库中的 `powercontext.env` 与示例配置**只含占位值**，不含真实凭据。

---

## 6. 切换到 OceanBase 集群（而不是嵌入式 seekdb）

嵌入式 seekdb 与 OceanBase 使用**完全相同的 SQL**，切换只需改环境变量：

```bash
export SDDCTX_DB_MODE=server
export SDDCTX_DB_HOST=127.0.0.1
export SDDCTX_DB_PORT=2881
export SDDCTX_DB_USER=root@demo
export SDDCTX_DB_PASSWORD=***        # 自行填写，不要提交
export SDDCTX_DB_NAME=sddctx

.venv/bin/python -m sddctx init
.venv/bin/python -m sddctx ingest
```

---

## 7. 复现「修改前失败 / 修改后通过」

仓库里的 `demo_app/` 已经是**修改后**的最终状态。复现修改前状态：

```bash
cd minimax
# 1) 回退到修改前快照
cp evidence/before/demo_app/pricing.py demo_app/pricing.py

# 2) 修改前：目标功能测试失败（因目标行为未实现），回归测试通过
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_threshold.py  --label before-target
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_regression.py --label before-regression
#   目标功能测试 => 5 failed, exit_code=1（CouponNotApplicable：目标行为未实现）
#   回归测试     => 9 passed, exit_code=0

# 3) 重新执行闭环，让 MiniMax 产出修改
.venv/bin/python scripts/verify.py
```

---

## 8. 重置（回到干净状态）

```bash
cd minimax
# 停止 PowerContext 后删除运行期数据即可完全重置
rm -rf .sddctx          # 内含嵌入式 seekdb 数据目录与 PowerContext 的 SQLite 库
rm -rf evidence         # 可选：清空留证
```

`evidence/` 目录下的内容全部是**可重新生成**的运行产物，不含任何凭据。

---

## 9. 已知限制

* **模型产出的是「要改的函数」，不是整个文件**。模块优先按函数级补丁落盘
  （`<<<PATCH file=… function=…>>>` + `ast` 定位行区间原地替换），整文件替换只作回退；
  落盘前做 `ast` 语法校验，写盘时保留原文件行尾，并统计 `unchanged_bytes`
  （本次 2859 字节逐字节未变），可在 `evidence/change.patch` 复核。
* **思维链预算**：`MiniMax-M3.1-Flash` 默认强制自适应思维链，思维链过长会耗尽
  `max_tokens` 导致没有补丁输出。模块通过 `thinking.budget_tokens=0` 限制思维链，
  并在 `stop_reason == "max_tokens"` 且无正文时自动翻倍 `max_tokens` 重试（最多 3 次）。
* **检索质量依赖资料质量**：规格条目必须写成 `## <编号> <标题>` + `- 状态：…` 的结构，
  否则会被当作普通正文，状态无法识别。
* **Scope 是服务端分配的不透明 ID**：首次运行会自动创建并缓存到
  `.sddctx/powercontext-scope.json`，之后复用同一个 Scope。
* **Windows 换行符**：仓库中的文件使用 LF；在 Windows 上 git 可能提示
  `LF will be replaced by CRLF`，不影响 Python 运行。
  补丁落盘会**保留原文件的行尾风格**（不会把整文件改写成 CRLF），
  否则「只改了目标函数」的承诺在字节级就不成立了。

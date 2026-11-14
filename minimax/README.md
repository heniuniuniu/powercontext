# 规有所依 —— 面向软件持续演进的 SDD 上下文管理系统

> MiniMax × OceanBase 赛事作品
> 上下文模块：**`sddctx`**（CLI + HTTP API）

在持续维护的软件项目里，需求规格、接口契约、设计决策、代码和测试都会演进。
AI 开发助手需要判断**当前该遵守什么要求**、**哪些决策已经失效**、
**先前验证是否仍然成立**。本作品实现并验证了这套工程上下文模块。

---

## 30 秒上手

```bash
cd minimax
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip

# 终端 A：启动 PowerContext（任务状态存储）
.venv/bin/powercontext server run --env-file powercontext.env

# 终端 B：端到端验证（导入 → 检索 → 状态 → MiniMax 改码 → 测试）
.venv/bin/python scripts/verify.py
```

---

## 它解决什么问题

| 助手常见的失败 | 本模块的做法 |
| --- | --- |
| 用了已过时的设计决策 | 规格按 `生效中` / `已失效` 分流，失效条目单独分区并标注「不得作为约束」 |
| 漏掉必须保持的既有行为 | 检索命中任一测试用例时，**整个测试文件**补齐进上下文 |
| 改完不知道对不对 | 目标功能测试 + 回归测试成对执行，记录命令、退出码与完整输出 |
| 任务中断后重复劳动 | 任务状态（已完成/待办/验证结果）写入 PowerContext，重启后读取并并入新上下文 |
| 改动越界、顺手重构 | 模型只返回**要改的函数**，模块用 `ast` 原地替换，其余字节不变 |
| 规格变了却还在用旧答案 | 重新导入按 `content_sha256` 判定增删改，检索结果随之更新（见下方「规格演进」） |

---

## 三项技术的真实分工

| 技术 | 作用 | 接入方式 | 版本 |
| --- | --- | --- | --- |
| **seekdb / OceanBase** | 存规格、代码符号、测试与倒排/全文索引；**检索结果决定上下文内容** | 嵌入式 `pylibseekdb`（MySQL 协议）· 或 OceanBase 集群（同一套 SQL） | seekdb v1.4.0.0 / pylibseekdb 1.4.0.post2 |
| **PowerContext** | 保存并复用任务进展与验证结果 | Python Client SDK + 本地 Server | 1.5.1 |
| **MiniMax** | 参与本次任务的代码生成与修改 | HTTP API（Anthropic Messages 协议） | MiniMax-M3.1-Flash |

---

## 一次真实任务（本次提交的验证对象）

* **业务工程**：`demo_app/` —— 订单优惠结算服务（`apply_coupon` / `settle`）
* **任务**：支持无门槛券（`threshold = 0`），门槛判定改为 `>=`，负门槛仍视为配置错误
* **目标功能测试**：`demo_app/tests/test_coupon_threshold.py`（5 条）
* **回归测试**：`demo_app/tests/test_coupon_regression.py`（9 条）

| | 修改前 | 修改后 |
| --- | --- | --- |
| 目标功能测试 | **5 failed**（exit_code=1，目标行为未实现） | **5 passed**（exit_code=0） |
| 回归测试 | 9 passed（exit_code=0） | 9 passed（exit_code=0） |
| 代码差异 | — | **2 行增、2 行删**（仅 `apply_coupon` 的门槛判定） |
| 改动行以外 | — | **2859 字节逐字节未变**（`unchanged_bytes`） |

最终 diff：

```diff
     subtotal = order.subtotal
-    if coupon.threshold <= 0:
+    if coupon.threshold < 0:
         raise CouponNotApplicable(...)
-    if subtotal <= coupon.threshold:
+    if coupon.threshold > 0 and subtotal < coupon.threshold:
         raise CouponNotApplicable(...)
```

> 差异不是「模型自己保证的」，而是**模块结构保证的**：模型只返回要改的函数，
> 模块用 `ast` 定位行区间原地替换，写盘前后做**逐字节比对**——只有那 2 行不同。

---

## 规格演进：改了规格、重新导入、上下文必须用新内容

本作品**实跑**过一次真实的规格演进（改文件 → 重新导入 → 重新检索），
留证在 `evidence/spec-evolution/`：

| 条目 | 演进前 | 演进后 |
| --- | --- | --- |
| `REQ-CPN-004` | v1.2 旧措辞，`sha=272be2df…` | **v1.3 新措辞**，`sha=63905905…` |
| `REQ-CPN-006` | v1.2 旧措辞，`sha=d92d84ae…` | **v1.3 新措辞**，`sha=b172b6d4…` |
| `ADR-006` | **不存在** | **新增**，进入上下文「适用规格」分区 |

导入端的变更清单同步记录：`REQ-CPN-004`/`REQ-CPN-006` 为 `updated`，`ADR-006` 为 `inserted`。
细节见 [`docs/VERIFICATION.md`](docs/VERIFICATION.md) §5.4。

---

## 目录结构

```
minimax/
├── README.md                 本文件
├── pyproject.toml / requirements.txt
├── powercontext.env          PowerContext Server 配置（不含凭据）
├── sddctx/                   ★ 上下文模块
├── specs/                    SDD 规格（需求 / 接口契约 / 决策记录）
├── demo_app/                 被修改的业务工程 + 目标/回归测试
├── scripts/verify.py         端到端验证驱动
├── docs/
│   ├── RUNBOOK.md            运行说明
│   ├── VERIFICATION.md       验证样例（任务、规格、步骤、预期、重置）
│   ├── TECH-OVERVIEW.md      一页技术说明
│   ├── MINIMAX-DEVLOG.md     MiniMax 开发记录
│   ├── DB-QUERY-LOG.md       数据库查询记录
│   └── POWCONTEXT-STATE-LOG.md  PowerContext 状态写入与复用记录
└── evidence/                 运行留证（可重新生成）
```

---

## 命令一览

```bash
sddctx init                                  # 建库建表
sddctx ingest                                # 导入规格 / 代码 / 测试到 seekdb
sddctx query "<任务>"                        # 检索并输出上下文（Markdown）
sddctx query --json --with-state "<任务>"   # 完整 JSON，并入 PowerContext 状态
sddctx state save --task-id t1 --done "..."  # 保存任务状态
sddctx state show                            # 读取当前状态与历史（独立进程亦可）
sddctx state search "<查询>"                 # 语义召回历史状态
sddctx agent --apply "<任务>"                # 上下文 → MiniMax → 落盘 → 跑测试 → 回写状态
sddctx test <路径> --label target            # 跑测试并留存日志
sddctx db --stats / --sql "<SQL>"             # 数据库取证
sddctx doctor                                # 环境自检
sddctx serve --port 8765                     # HTTP API
```

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | 运行说明：依赖版本、安装、启动、命令、重置、已知限制 |
| [`docs/VERIFICATION.md`](docs/VERIFICATION.md) | 验证样例：任务描述、有效规格、初始数据、操作步骤、预期结果、重置方法 |
| [`docs/TECH-OVERVIEW.md`](docs/TECH-OVERVIEW.md) | 一页技术说明：模块结构、上下文选择方式、各项技术分工 |
| [`docs/MINIMAX-DEVLOG.md`](docs/MINIMAX-DEVLOG.md) | MiniMax 开发记录：任务、上下文、模型产出、落盘与验证 |
| [`docs/DB-QUERY-LOG.md`](docs/DB-QUERY-LOG.md) | 数据库查询记录：实际执行的 SQL 与结果 |
| [`docs/POWCONTEXT-STATE-LOG.md`](docs/POWCONTEXT-STATE-LOG.md) | PowerContext 状态写入与复用记录 |

---

## 提交与评审

* 本作品的**全部材料**都在 `minimax/` 目录内，开发分支为 `2026-competition-ecosystem`。
* 目录里包含评审要求的六类材料：
  * **源码** —— `sddctx/`（上下文模块）、`demo_app/`（被修改的业务工程与测试）、`scripts/verify.py`
  * **运行说明** —— [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
  * **验证样例** —— [`docs/VERIFICATION.md`](docs/VERIFICATION.md)（含重置方法，可完整复现）
  * **代码差异** —— `evidence/change.patch`（+2 / −2）与 `evidence/before/`（修改前快照）
  * **测试结果** —— `evidence/*pytest*.json`（命令、退出码、完整输出）
  * **技术使用记录** —— `docs/DB-QUERY-LOG.md`、`docs/POWCONTEXT-STATE-LOG.md`、`docs/MINIMAX-DEVLOG.md`
  * **一页技术说明** —— [`docs/TECH-OVERVIEW.md`](docs/TECH-OVERVIEW.md)
* 上游 `https://github.com/oceanbase/powercontext.git` 是**公开只读**参考仓库
  （直接 push 会返回 `403 Permission denied`）；按赛事说明，评测使用
  「私有仓库导入后获得的同名分支」，因此提交保持在本地 `2026-competition-ecosystem` 分支上。

---

## 安全与合规

* 仓库内**不含任何真实凭据**；`powercontext.env` 只有本地路径与开关。
* MiniMax 凭据按 `SDDCTX_MINIMAX_API_KEY` → 本机 `~/.minimax/config.yaml` →
  本机登录态 `~/.minimax/auth/**/auth.json` 的顺序解析，均不写入仓库。
* `evidence/` 全部是可重新生成的运行产物。
* 本作品未复用第三方业务代码；`demo_app/` 为本作品自带的示例业务工程。

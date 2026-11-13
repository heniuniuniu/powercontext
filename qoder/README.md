# 资料整理助手（Qoder × PowerContext × seekdb）

> 用 Qoder 开发的「学习/科研提效」项目：把散落在笔记、论文、会议记录里的信息，
> 变成可混合检索的个人资料库，并用 PowerContext 记住每个人的检索偏好与任务进展。

## 解决的问题

- 找一段上学期的笔记要翻十几个文件；
- 同一份资料在不同项目里重复整理；
- 周报要从聊天记录里一条条拼。

## 核心功能

1. **导入**：把 Markdown / TXT 资料按段落切片写入 seekdb，保存原文、来源、标签与时间。
2. **混合检索**：在 seekdb 内用向量近邻 + 全文匹配 + 标签过滤，一次查询返回带出处的段落。
   `site/app.js` 提供可直接体验的前端；`server/search.py` 是对接 seekdb 的本地服务。
3. **记忆复用**：PowerContext 保存「最近检索主题 / 常用标签 / 未完成任务」，
   下次打开页面自动带出，并影响检索结果的排序权重。
4. **周报草稿**：按时间窗汇总检索过的资料，生成一份可编辑的周报初稿。

## 技术分工

| 组件 | 作用 |
| --- | --- |
| Qoder | 项目主要开发工作：需求拆解、页面与检索服务编码、调试、通过 Sites 发布项目网站 |
| seekdb | 资料存储与混合检索（向量 + 全文 + 结构化标签） |
| PowerContext | Atomic Memory 保存检索主题与常用标签，跨会话召回并影响排序权重（已接入官方 1.5.1） |

## 运行

### 方式一：零依赖自检（无需任何外部服务）

```bash
cd qoder
python main.py               # 模式 A：完整自检，打印 SELFCHECK PASS，退出码 0，数秒内结束
python main.py --serve       # 模式 B：启动站点 + API 服务并持续运行，访问 http://127.0.0.1:8899/
```

| 模式 | 命令 | 行为 | 适用 |
| --- | --- | --- | --- |
| A 自检 | `python main.py` | 跑完 health → init → 混合检索 → 记忆复用 → 周报草稿，打印 `SELFCHECK PASS` 后退出 | 自动化评测 / CI |
| B 服务 | `python main.py --serve` | 常驻，单端口同时提供项目网站与 `/health`、`/init`、`/search`、`/memory/read`、`/memory/write` | 人工浏览器体验 |

**两种模式跑的是同一套主流程代码**（`server/search.py`），不是两套实现。
模式 A 与以下命令完全等价，在任意工作目录下执行均可：

```bash
python qoder/main.py
python qoder/main.py --selfcheck
python qoder/app.py
python qoder/server/search.py
python run.py                     # 仓库根目录转发入口
```

未启动外部服务时，自动使用内置 5 条示例语料 + 本地 `memory.json`，
导入 → 混合检索 → 记忆复用 → 周报草稿全流程完整可跑。

### 方式二：接入真实 PowerContext（已实测）

本机已验证官方 `powercontext` 1.5.1 的 SQLite 后端，Windows 上可直接运行：

```bash
cd qoder
python -m venv .venv
.venv/Scripts/pip install "powercontext[server]"
.venv/Scripts/powercontext config init --template --output .env.powercontext
.venv/Scripts/powercontext server run --env-file .env.powercontext --port 8765
```

另开一个终端跑项目：

```bash
cd qoder
POWERCONTEXT_URL=http://127.0.0.1:8765 .venv/Scripts/python main.py
# SELFCHECK PASS ...
# {"backend": "local", "powercontext": "powercontext", "memory_backend": "powercontext", ...}
```

此时：

- 每次检索通过 `POST /v1/memory/remember` 写入一条 Atomic Memory（`kind=检索记录`）；
- 下一轮检索通过 `POST /v1/memory/entries/list` 读取最近主题/常用标签，
  并通过 `POST /v1/memory/search` 召回历史标签影响排序；
- `/health` 与 `/search` 响应里的 `powercontext` / `memory_backend` 字段为 `powercontext`；
- 真实输出见 [`evidence/powercontext-live.md`](evidence/powercontext-live.md)。

PowerContext 服务不可达时自动降级回本地 `memory.json`，主流程不会中断。
真实 seekdb 仍是可选增强：配置 `SEEKDB_DSN` 且安装 `pymysql` 后，
`/search` 会改走 seekdb 单条 SQL 的向量 + 全文融合检索。仓库内不含任何真实凭据。

## 验证样例

见 `验证样例.md`：初始数据、输入、操作步骤、预期结果、seekdb 查询记录与
PowerContext 记忆读写记录，以及重置方法。

## Qoder 开发记录

见 `Qoder开发记录.md`，记录需求拆解、编码、调试与发布各环节。
提交平台时还需填 Qoder 账号 UID 并附对话截图 —— UID 由账号本人从 Qoder
个人中心复制填写，仓库内不预置他人账号标识。

## 项目网站

`site/index.html`（同时复制到仓库 `docs/index.html` 供公开托管）。
网站包含项目简介、解决的问题、核心功能、演示、真实运行证据、代码仓库链接与体验入口。

已通过 Qoder Sites 公开发布（可见范围：任何人都可访问）：

<https://powercontext-notes-helper-crf2cqsn3ek.qoder.website/>

## 材料清单

| 文件 | 用途 |
| --- | --- |
| `site/` | 项目网站静态源码 |
| `server/search.py` | seekdb 混合检索 + PowerContext Atomic Memory 读写的本地服务 |
| `server/requirements.txt` | 依赖清单 |
| `evidence/powercontext-live.md` | PowerContext 真实运行输出与日志节选 |
| `验证样例.md` | 可复现的操作步骤与证据 |
| `Qoder开发记录.md` | Qoder 参与开发的过程记录 |
| `README.md` | 本运行说明 |

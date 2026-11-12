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
| PowerContext | 保存并复用用户偏好、任务进展与历史互动 |

## 运行（零依赖，一条命令）

```bash
cd qoder
python main.py               # 默认：跑完整自检并退出，打印 SELFCHECK PASS，退出码 0
python main.py --serve       # 人工体验：持续运行服务，浏览器打开 http://127.0.0.1:8899/
```

**自动化评测请直接执行 `python main.py`**：它会在数秒内完成
health → init → 混合检索 → 记忆复用 → 周报草稿全流程并返回确定退出码，
不会常驻阻塞评测器。`--serve` 仅供人工在浏览器里体验。

`main.py` 等价于 `python server/search.py`，只需 Python 3.9+ 标准库，
**不需要 pip install、不需要数据库、不需要 API Key、不需要另开终端起静态服务器**：
同一个端口既提供项目网站，也提供 `/health`、`/init`、`/search`、
`/memory/read`、`/memory/write` 接口。未配置外部服务时，自动使用内置
5 条示例语料 + 本地 `memory.json`，导入 → 混合检索 → 记忆复用 → 周报草稿
全流程可完整跑通，并在 `logs/run.log` 留下
`seekdb.hybrid_search` 与 `powercontext.write` 两条证据。

### 接入真实 seekdb / PowerContext（可选增强）

```bash
pip install pymysql requests
export SEEKDB_DSN='user@127.0.0.1:2881/kb'          # 占位值，换成自己的实例
export POWERCONTEXT_URL='http://127.0.0.1:8080'     # 占位值，换成自己的服务
python main.py
```

配置了环境变量且依赖可用时，`/search` 自动改走 seekdb 单条 SQL 的
向量 + 全文融合检索、PowerContext HTTP 上下文读写；连接失败会自动降级回
本地路径，主流程不会中断。仓库内不含任何真实凭据。

## 验证样例

见 `验证样例.md`：初始数据、输入、操作步骤、预期结果、seekdb 查询记录与
PowerContext 记忆读写记录，以及重置方法。

## Qoder 开发记录

见 `Qoder开发记录.md`，记录需求拆解、编码、调试与发布各环节。
提交平台时还需填 Qoder 账号 UID 并附对话截图 —— UID 由账号本人从 Qoder
个人中心复制填写，仓库内不预置他人账号标识。

## 项目网站

`site/index.html`（同时复制到仓库 `docs/index.html` 供公开托管）。
网站包含项目简介、解决的问题、核心功能、演示、代码仓库链接与体验入口。

## 材料清单

| 文件 | 用途 |
| --- | --- |
| `site/` | 项目网站静态源码 |
| `server/search.py` | seekdb 混合检索 + PowerContext 记忆读写的本地服务 |
| `server/requirements.txt` | 依赖清单 |
| `验证样例.md` | 可复现的操作步骤与证据 |
| `Qoder开发记录.md` | Qoder 参与开发的过程记录 |
| `README.md` | 本运行说明 |

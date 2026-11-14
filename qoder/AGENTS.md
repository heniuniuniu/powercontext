# 资料整理助手 · Qoder 项目约定

本目录是 2026 OceanBase 数据库大赛 Qoder 赛题的参赛作品，主要开发工作在 Qoder 中完成：
需求拆解、页面与检索服务编码、本地调试、以及通过 Qoder Sites 发布项目网站。
完整过程见 `Qoder开发记录.md`。

## 目标

把散落在笔记、论文、会议记录里的资料变成可混合检索的个人资料库，并用
PowerContext 记住检索偏好与任务进展。核心链路：
**导入语料 → seekdb 混合检索 → PowerContext 记忆复用 → 周报草稿**。

## 目录

- `main.py` / `app.py` / `run.py`：等价的零安装入口，转发到 `server/search.py`
- `server/search.py`：单端口站点 + `/health`、`/init`、`/search`、`/memory/*` API；
  含 seekdb 混合检索 SQL 与 PowerContext 状态读写；未配置外部服务时自动降级到内置语料
- `site/`：项目网站静态源码，`index.html` 预渲染了一份主流程真实输出
- `README.md`：运行说明；`验证样例.md`：可复现步骤与证据；`Qoder开发记录.md`：开发过程
- 仓库 `docs/`：网站公开托管副本；根目录 `run.py`：跨目录兼容入口

## 开发命令

```bash
python run.py                 # 自检，打印 SELFCHECK PASS，退出码 0
python run.py --serve         # 启动服务，浏览器打开 http://127.0.0.1:8899/
```

只需 Python 3.9+ 标准库。`requirements.txt` 里的 pymysql / requests 仅在接入真实
seekdb 实例或远程 PowerContext 服务时可选安装，不装也能跑通全部主流程；
`SEEKDB_DSN` / `POWERCONTEXT_URL` 用占位值配置，仓库内不含真实凭据。

## 约定

- 检索结果必须带出处（source + tag），不能只返回裸文本；
- 站点展示内容必须与代码实际输出一致（预渲染结果取自 `server/search.py` 的同一份语料）；
- 不引入需要 API Key 或公网访问的外部依赖阻断主流程；
- 修改入口行为时，必须同步更新 `README.md` 和 `验证样例.md`，避免说明与实际不一致。

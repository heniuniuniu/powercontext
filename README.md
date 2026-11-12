# PowerContext 赛题仓库（2026 OceanBase 数据库大赛）

本仓库按赛题分目录组织，`2026-competition-ecosystem` 分支为提测分支。

| 目录 | 赛题 |
| --- | --- |
| `qoder/` | Qoder · 资料整理助手（项目网站、运行说明、验证样例、开发记录） |
| `minimax/` | MiniMax · SDD 上下文管理系统 |
| `stepfun/` | StepFun · 端侧推理 + seekdb 混合检索 |
| `dify/` | Dify · Brainstorm Debate 智能应用 |
| `gaode/` | 高德地图 · 周边去哪儿 Skill |
| `docs/` | `qoder/site/` 的公开托管副本 |

## 最快跑通主流程（Qoder 项目）

```bash
python run.py                 # 仓库根目录零依赖入口，打印 SELFCHECK PASS 后退出
python qoder/main.py          # 等价写法
python qoder/main.py --serve  # 常驻站点 + API，浏览器打开 http://127.0.0.1:8899/
```

只需 Python 3.9+ 标准库，不需要安装依赖、不需要数据库、不需要 API Key。
完整运行说明见 [`qoder/README.md`](qoder/README.md)，可复现步骤见
[`qoder/验证样例.md`](qoder/验证样例.md)。

公开项目网站通过 Qoder Sites 发布：
<https://powercontext-notes-helper-crf2cqsn3ek.qoder.website/>

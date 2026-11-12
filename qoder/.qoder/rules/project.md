# Qoder 规则 · 资料整理助手

本项目的主要开发工作由 Qoder 完成（需求拆解、编码、调试、Qoder Sites 发布）。
改动请遵守以下约定，完整开发过程记录见 `../Qoder开发记录.md`。

## 技术边界

- 主流程必须零第三方依赖可运行：Python 3.9+ 标准库；
- seekdb 负责向量 + 全文 + 标签的混合检索，PowerContext 负责记忆与上下文复用；
- 未配置外部服务时降级到内置语料 + 本地记忆文件，且必须在日志中如实记录 backend=local；
- 不得把示例数据伪装成真实数据库查询结果。

## 运行与验证

```bash
python run.py                 # 自检，输出 SELFCHECK PASS，退出码 0
python run.py --serve         # 常驻站点 + API，http://127.0.0.1:8899/
```

`README.md` 与 `验证样例.md` 中的命令必须与实际行为一致：
自检命令跑完即退出，只有 `--serve` 才常驻。

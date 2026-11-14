# 阶跃星辰 × seekdb：端侧推理迷你知识库

导入一批文本 → 浏览器本地生成 embedding → 写入 seekdb → 在 seekdb 内完成
向量 + 全文混合检索 → 展示排序结果与原文。全程无云端推理 API。

## 浏览器与依赖

- Chrome / Edge 最新版（WebGPU 可用时自动启用；不可用时降级 WASM）
- Node.js 20+（仅用于本地 server 与静态托管，推理不在 Node 侧）
- 一个可连接的 seekdb 实例（嵌入式或本地 server）

```bash
npm install
cp .env.example .env          # 填 seekdb 连接信息，占位值不提交真实凭据
npm run dev:server            # 终端 1：本地 seekdb server, http://127.0.0.1:3000
npm run dev                   # 终端 2：Vite 静态站, http://127.0.0.1:5173
```

## 使用流程

1. 打开页面，右上角显示当前推理后端。首次会下载量化模型，进度条显示加载状态。
2. 「导入语料」选择若干 `.txt` / `.md`，或直接粘贴文本；按 16 条一批本地向量化。
3. 写入完成会触发 `flushIndex()`，刷新期间查询按钮禁用。
4. 输入查询 → 本地生成查询向量 → seekdb 单次混合检索 → 展示排序结果与原文片段。
5. 「性能评测」按 `benchmarks/queries.json` 跑分阶段计时与 Recall@10 / nDCG@10，
   结果可导出为 JSON 填入 `benchmarks/RESULTS.md`。

## 降级验证

```bash
# 方式 A：启动前在控制台执行
window.FORCE_WASM = true; location.reload();
# 方式 B：Chrome 加启动参数
chrome --disable-features=WebGPU
```

页面应显示「WebGPU 不可用，已自动切换到 WASM (INT8)」，导入与检索流程仍完整可用。

## 禁止事项（题目硬性要求，本项目未违反）

- 不调用任何云端 embedding / 推理 API；模型下载不等于把推理放到云端。
- 不预先在服务端算好向量或检索结果再回放。
- 向量与文档的存储、检索全部走 seekdb，不用客户端内存检索替代。

## 配置文件

`.env.example` 给出占位值；真实凭据不入库。`SEEKDB_DSN` 由本地 server 读取，
浏览器只与 `http://127.0.0.1:3000` 通信，不直接接触数据库口令。

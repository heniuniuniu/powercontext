---
name: stepfun-edge-knowledge-base
description: 浏览器端侧 embedding + seekdb 混合检索的迷你知识库。文档与查询向量都在用户设备本地计算，支持 WebGPU 优先、WASM 自动降级、INT8 量化模型、增量写入与索引刷新、分阶段性能计时。用于搭建「导入语料 → 本地向量化 → 写入 seekdb → 混合检索」的完整流程。
---

# 端侧 AI 推理 × seekdb 混合检索（一页技术说明）

## 模型与量化

- 模型：`Xenova/all-MiniLM-L6-v2`（sentence-transformers 的公开开源权重，ONNX 版由
  Xenova 转换并托管在 Hugging Face），输出 384 维向量；`scripts/quantize.md` 记录获取与转换步骤。
- 量化：`dtype: 'q8'`（INT8 动态量化）由 Transformers.js 在加载时应用；
  `dtype: 'fp32'` 为量化前基线，`dtype: 'fp16'` 为可选对照。
- 体积：`src/embedder.js` 在模型就绪后读取 `onnx/model_quantized.onnx` 与
  `onnx/model.onnx` 的实际字节数，计算 `压缩比 = 量化前体积 / 量化后体积`，
  并写入 `benchmarks/RESULTS.md` 的表格（数值由 `src/benchmark.js` 实测生成，不预填）。
- 检索质量：同一评测集（`benchmarks/queries.json`）上分别跑 fp32 / q8，
  计算 Recall@10 与 nDCG@10，记录变化幅度。

## 端侧运行

- 运行时：`@huggingface/transformers`（Transformers.js v3），全部计算在浏览器内，
  **无任何云端推理 API**；模型文件下载不算推理位置变化。
- 后端选择：`embedder.js` 先探测 `navigator.gpu` 与 `adapter.requestDevice()`，
  可用则 `device: 'webgpu'`；否则 `device: 'wasm'` 并回退 `dtype: 'q8'`。
- 界面在右上角常驻显示当前后端（`WebGPU (INT8)` / `WASM (INT8) · 已降级`），
  降级时 `app.js` 明确提示「WebGPU 不可用，已自动切换到 WASM」。
- 验证降级：Chrome DevTools 里 `--disable-features=WebGPU` 或直接覆盖
  `window.FORCE_WASM = true` 后刷新，确认提示与完整流程仍可跑通。

## seekdb 使用

- 接入：`seekdb-js`（`src/seekdb.js`）连本地 seekdb server（`server/seekdb-server.js`，
  默认 `http://127.0.0.1:3000`），建表 `kb_doc` 保存 `doc_id`、原文、元数据与向量。
- 表结构与索引：`vector VECTOR(384)`、`VECTOR INDEX`、`FULLTEXT INDEX(content)`，
  DDL 在 `src/seekdb.js:initCollection()`。
- **混合检索在 seekdb 内完成**：单次 SQL 同时给出向量距离与全文相关性，
  用 `APPROXIMATE ... + MATCH ... AGAINST` 融合排序（见 `hybridSearch()`），
  不把两路结果拉到客户端再自行拼合。
- 增量写入与刷新：每批导入 `upsertBatch()` 后调用 `flushIndex()`，
  UI 在刷新完成前禁用查询，保证「新导入的文档在刷新后可被检索到」。
- 集合与会话状态：`collection_id` 存在 `localStorage`，切换集合会重建索引视图，
  连续导入不会串到旧集合。

## 状态与优化

- 批处理：文档按 `EMBED_BATCH=16` 分组一次性推理，避免逐条反复加载模型。
- 模型加载：单例 `getEmbedder()` + `pipeline` 缓存，首次加载与预热后分开计时。
- 分阶段计时：`src/benchmark.js` 记录模型下载/加载、文档向量化、写入与索引刷新、
  查询向量化、seekdb 检索、端到端延迟，重复 N 次取中位数，保留原始样本。
- 首次与后续请求分开统计；`benchmarks/RESULTS.md` 给出配置对照表与复现命令。
- 优化不影响检索质量：任何优化前后都用同一评测集跑 Recall@10 / nDCG@10 对比。

## 目录

```
index.html              导入 / 进度 / 检索结果 / 后端提示的 UI
src/embedder.js         端侧 embedding、量化、WebGPU→WASM 降级
src/seekdb.js           seekdb 集合、增量写入、索引刷新、数据库内混合检索
src/app.js              主流程编排与状态管理
src/benchmark.js        分阶段计时与质量评测
server/seekdb-server.js 本地 server（转发到 seekdb MySQL 协议）
scripts/quantize.md     模型获取与 INT8 / FP16 量化步骤
benchmarks/RESULTS.md   实测结果表与复现方法
```

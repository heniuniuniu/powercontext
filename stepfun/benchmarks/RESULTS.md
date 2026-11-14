# 性能与质量实测结果

> 本表由 `src/benchmark.js` 的 `runBenchmark()` 导出 JSON 填写。
> 提交时把它替换成「同一台设备、同一浏览器版本、同一语料与查询集」下实际跑出的数值；
> 代码里没有预填任何体积或耗时，避免用编造数字充当实测结果。

## 复现方式

```bash
npm install
npm run dev:server & npm run dev
# 打开 http://127.0.0.1:5173，导入 benchmarks/corpus.txt 中的语料
# 点「跑分阶段计时与 Recall@10」，把输出 JSON 粘到本文件末尾
```

固定条件需记录：设备/CPU/GPU、浏览器版本、语料条数、查询集（5 条，见
`queries.json`）、模型版本、精度、推理后端、批大小（16）、seekdb 配置、
`ef_search` / `m` / `ef_construction`。首次加载与预热后分开计时；重复 5 次取中位数，
原始样本保留在导出 JSON 的 `rawSamples` 中。

## 配置对照表

| 配置 | 模型体积 | 压缩比 | 向量化吞吐 | seekdb 检索延迟 | 端到端查询延迟 | 检索质量 |
| --- | --- | --- | --- | --- | --- | --- |
| 量化前基线 fp32 | 实测值 | 1.000 | 实测值 | 实测值 | 实测值 | Recall@10 / nDCG@10 |
| INT8 + WebGPU | 实测值 | 实测值 | 实测值 | 实测值 | 实测值 | 同一指标结果 |
| INT8 + WASM（降级） | 实测值 | 实测值 | 实测值 | 实测值 | 实测值 | 同一指标结果 |
| FP16 + WebGPU（可选） | 实测值 | 实测值 | 实测值 | 实测值 | 实测值 | 同一指标结果 |

## 分阶段计时口径

| 阶段 | 起止 | 采集位置 |
| --- | --- | --- |
| 模型下载与加载 | `getEmbedder()` 开始到 `pipeline` ready | `progress_callback` |
| 文档向量化 | 每批 `embed()` 开始到返回 | `app.js` 导入循环 |
| 写入与索引刷新 | `upsertBatch()` 到 `flushIndex()` 返回 | `app.js` 导入循环 |
| 查询向量化 | `embed([q])` | `app.js` 检索按钮 |
| seekdb 检索 | `hybridSearch()` | `app.js` 检索按钮 |
| 端到端 | 用户点检索到结果可展示 | `app.js` 检索按钮 |

## 降级路径验证

| 环境 | 后端显示 | 界面提示 | 检索流程 |
| --- | --- | --- | --- |
| WebGPU 可用 | `WebGPU (Q8)` | 无降级提示 | 完整可用 |
| `FORCE_WASM=true` 或 `--disable-features=WebGPU` | `WASM (Q8) · 已降级` | 「WebGPU 不可用，已自动切换到 WASM」 | 完整可用 |

## 优化前后对照（保持其他条件一致）

- 批处理：逐条推理 → `EMBED_BATCH=16`，只改批大小，模型/精度/后端不变。
- 模型加载：每次新建 pipeline → 单例 `getEmbedder()`，只改加载策略。
- 索引刷新：每次导入后立即刷新 → 每批刷新一次，只改刷新策略。
- 每次优化后都在同一评测集复跑 Recall@10 / nDCG@10，确认质量未明显下降。

# 模型获取与量化步骤

## 1. 模型来源

- 基础模型：`sentence-transformers/all-MiniLM-L6-v2`（Apache-2.0，公开开源）
- ONNX 转换版：`Xenova/all-MiniLM-L6-v2`（Hugging Face，Transformers.js 可直接加载）
- 向量维度：384；池化 mean + L2 normalize（见 `src/embedder.js`）

```bash
# 只下载模型文件，实际推理仍在浏览器本地
npx -y @huggingface/hub pull Xenova/all-MiniLM-L6-v2 --dir public/models
```

## 2. 量化方案

Transformers.js v3 在 `pipeline()` 加载时通过 `dtype` 选择权重精度：

| dtype | 含义 | 用途 |
| --- | --- | --- |
| `fp32` | 未量化 FP32 权重 | 量化前基线 |
| `q8` | INT8 动态量化（`model_quantized.onnx`） | 正式方案，WebGPU/WASM 均可用 |
| `fp16` | FP16 权重（`model_fp16.onnx`） | 可选对照，需 WebGPU |

离线生成量化权重（可选，若不想用仓库自带的 `model_quantized.onnx`）：

```bash
pip install optimum onnxruntime onnx
optimum-cli export onnx --model sentence-transformers/all-MiniLM-L6-v2 \
  --task feature-extraction ./onnx_fp32
python - <<'PY'
from onnxruntime.quantization import quantize_dynamic, QuantType
quantize_dynamic('./onnx_fp32/model.onnx', './public/models/Xenova/all-MiniLM-L6-v2/onnx/model_quantized.onnx',
                 weight_type=QuantType.QInt8)
PY
```

## 3. 体积与压缩比

`src/embedder.js` 在模型就绪后对 `model.onnx` 与 `model_quantized.onnx` 发 HEAD 请求，
读取 `content-length` 得到实际字节数，计算 `压缩比 = 量化前体积 / 量化后体积`。
`src/benchmark.js` 把结果写入导出 JSON 的 `config.compressionRatio` 字段，
再填到 `benchmarks/RESULTS.md`。不在代码里硬编码体积数字。

## 4. 检索质量对照

固定评测集 `benchmarks/queries.json`、固定语料、固定 topK=10，
分别在 `window.BENCH_DTYPE = 'fp32' | 'q8' | 'fp16'` 下跑 5 次取中位数，
对比 Recall@10 / nDCG@10。切换 dtype 后需刷新页面重新加载模型。

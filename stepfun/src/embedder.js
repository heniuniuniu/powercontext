/**
 * 端侧 embedding：Transformers.js + 量化 + WebGPU 优先 / WASM 自动降级。
 * 所有推理都发生在用户设备的浏览器里，没有任何云端推理 API 调用。
 */
import { pipeline, env } from '@huggingface/transformers';

export const MODEL_ID = 'Xenova/all-MiniLM-L6-v2';  // 公开开源权重，ONNX 由 Xenova 转换
export const DIM = 384;
export const EMBED_BATCH = 16;

env.allowLocalModels = true;
env.useBrowserCache = true;

let cached = null;
let backend = null;

async function webgpuAvailable() {
  if (window.FORCE_WASM) return false;
  if (!navigator.gpu) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return Boolean(adapter && (await adapter.requestDevice()));
  } catch {
    return false;
  }
}

/** 单例加载；返回 { embed, backend, dtype, modelBytes, baselineBytes } */
export async function getEmbedder(onProgress = () => {}) {
  if (cached) return cached;

  const useWebGPU = await webgpuAvailable();
  const device = useWebGPU ? 'webgpu' : 'wasm';
  // q8 = INT8 动态量化；fp32 为量化前基线，fp16 为可选对照（BENCH_DTYPE 切换）
  const dtype = window.BENCH_DTYPE || 'q8';
  backend = `${device === 'webgpu' ? 'WebGPU' : 'WASM'} (${dtype.toUpperCase()})`;

  const extractor = await pipeline('feature-extraction', MODEL_ID, {
    device,
    dtype,
    progress_callback: p => onProgress(p),
  });

  // 体积对比：实际模型文件字节数，不预填任何数字
  const [quant, base] = await Promise.all([
    modelSize(`/models/${MODEL_ID}/onnx/model_quantized.onnx`),
    modelSize(`/models/${MODEL_ID}/onnx/model.onnx`),
  ]);

  cached = {
    backend,
    device,
    dtype,
    modelBytes: quant,
    baselineBytes: base,
    compressionRatio: quant && base ? +(base / quant).toFixed(3) : null,
    async embed(texts) {
      const list = Array.isArray(texts) ? texts : [texts];
      const out = await extractor(list, { pooling: 'mean', normalize: true });
      return list.map((_, i) => Array.from(out[i].data));
    },
  };
  return cached;
}

async function modelSize(url) {
  try {
    const r = await fetch(url, { method: 'HEAD' });
    const n = Number(r.headers.get('content-length'));
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

export function currentBackend() { return backend; }

export function renderBackendBadge(el) {
  const degraded = backend && backend.startsWith('WASM');
  el.textContent = degraded ? `${backend} · 已降级` : (backend || '检测推理后端…');
  el.classList.toggle('warn', Boolean(degraded));
  if (degraded) {
    const hint = document.getElementById('import-hint');
    if (hint) hint.textContent = 'WebGPU 不可用，已自动切换到 WASM。导入与检索流程仍完整可用。';
  }
}

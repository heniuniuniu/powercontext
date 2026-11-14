/**
 * 分阶段计时与检索质量评测。
 * 全部数值来自当次实际运行；重复 N 次取中位数，保留原始样本，不挑最快一次。
 */
import { EMBED_BATCH } from './embedder.js';

const REPEAT = 5;

function median(a) {
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)];
}

function recallAtK(actual, expected, k = 10) {
  const hit = actual.slice(0, k).filter(x => expected.includes(x)).length;
  return expected.length ? +(hit / expected.length).toFixed(4) : 0;
}

function ndcgAtK(actual, expected, k = 10) {
  let dcg = 0;
  const ideal = Math.min(expected.length, k);
  let idcg = 0;
  for (let i = 0; i < ideal; i++) idcg += 1 / Math.log2(i + 2);
  actual.slice(0, k).forEach((id, i) => {
    if (expected.includes(id)) dcg += 1 / Math.log2(i + 2);
  });
  return idcg ? +(dcg / idcg).toFixed(4) : 0;
}

export async function runBenchmark(embedder, hybridSearch, queries = []) {
  // queries: [{ query, expected: [doc_id...] }]；缺省时从 benchmarks/queries.json 拉取
  if (!queries.length) {
    try { queries = await (await fetch('/benchmarks/queries.json')).json(); } catch { queries = []; }
  }
  const samples = { load: [], docEmbed: [], writeFlush: [], queryEmbed: [], seekdbSearch: [], e2e: [] };

  // 首次加载（冷）与预热后（热）分开统计
  const warm = await embedder.embed(['warmup']);
  void warm;
  for (let r = 0; r < REPEAT; r++) {
    for (const item of queries) {
      const t0 = performance.now();
      const [qv] = await embedder.embed([item.query]);
      const t1 = performance.now();
      const rows = await hybridSearch({ vector: qv, query: item.query, topK: 10 });
      const t2 = performance.now();
      samples.queryEmbed.push(t1 - t0);
      samples.seekdbSearch.push(t2 - t1);
      samples.e2e.push(t2 - t0);
      item._last = { rows: (rows || []).map(x => x.doc_id), expected: item.expected || [] };
    }
  }

  const quality = queries.map(item => ({
    query: item.query,
    recallAt10: recallAtK(item._last?.rows || [], item._last?.expected || []),
    ndcgAt10: ndcgAtK(item._last?.rows || [], item._last?.expected || []),
  }));

  return {
    config: {
      model: 'Xenova/all-MiniLM-L6-v2',
      dtype: embedder.dtype,
      backend: embedder.backend,
      batch: EMBED_BATCH,
      repeat: REPEAT,
      modelBytes: embedder.modelBytes,
      baselineBytes: embedder.baselineBytes,
      compressionRatio: embedder.compressionRatio,
      device: navigator.userAgent,
    },
    medianMs: Object.fromEntries(Object.entries(samples).map(([k, v]) => [k, v.length ? +median(v).toFixed(1) : null])),
    rawSamples: samples,
    quality,
    note: '首次加载与预热后分开计时；docEmbed / writeFlush 由导入流程单独记录，见 app.js 的导入提示。',
  };
}

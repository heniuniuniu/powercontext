/** 主流程编排：导入 → 本地向量化 → 写入 seekdb → 索引刷新 → 混合检索。 */
import { getEmbedder, renderBackendBadge, EMBED_BATCH, currentBackend } from './embedder.js';
import { initCollection, upsertBatch, flushIndex, hybridSearch, activeCollection } from './seekdb.js';
import { runBenchmark } from './benchmark.js';

const $ = id => document.getElementById(id);
const badge = $('backend');
const bar = $('bar');
const btnImport = $('btn-import');
const btnSearch = $('btn-search');
const btnBench = $('btn-bench');

let embedder = null;
let ready = false;

function progress(pct) { bar.style.width = `${Math.round(pct * 100)}%`; }
function hint(msg) { $('import-hint').textContent = msg; }

async function boot() {
  try {
    embedder = await getEmbedder(p => {
      if (p?.status === 'progress' && p.total) progress(p.loaded / p.total);
    });
    renderBackendBadge(badge);
    await initCollection();
    ready = true;
    btnSearch.disabled = false;
    progress(1);
    hint(`模型就绪（${currentBackend()}），压缩比 ${embedder.compressionRatio ?? '待实测'}。可开始导入语料。`);
  } catch (e) {
    badge.textContent = '初始化失败';
    badge.classList.add('warn');
    hint(`初始化失败：${e.message}。请确认 npm run dev:server 已启动，且浏览器支持 WebGPU 或 WASM。`);
  }
}

function collectTexts() {
  const raw = $('corpus').value.trim();
  const lines = raw ? raw.split(/\r?\n/).map(s => s.trim()).filter(Boolean) : [];
  return lines.map((text, i) => ({ docId: `d${Date.now()}_${i}`, text, meta: { source: 'paste', line: i + 1 } }));
}

btnImport.addEventListener('click', async () => {
  if (!ready) return;
  const files = Array.from($('files').files || []);
  let docs = collectTexts();
  for (const f of files) docs.push({ docId: `f${Date.now()}_${f.name}`, text: await f.text(), meta: { source: f.name } });
  if (!docs.length) { hint('请先粘贴文本或选择 .txt/.md 文件。'); return; }

  btnImport.disabled = true;
  btnSearch.disabled = true;
  try {
    let done = 0;
    const t0 = performance.now();
    for (let i = 0; i < docs.length; i += EMBED_BATCH) {
      const batch = docs.slice(i, i + EMBED_BATCH);
      const vectors = await embedder.embed(batch.map(d => d.text));
      batch.forEach((d, j) => { d.vector = vectors[j]; });
      await upsertBatch(batch);
      done += batch.length;
      progress(done / docs.length);
      hint(`本地向量化并写入 ${done}/${docs.length} 条…`);
    }
    const t1 = performance.now();
    hint(`写入完成，正在刷新索引（写入耗时 ${(t1 - t0).toFixed(0)} ms）…`);
    await flushIndex();
    hint(`索引刷新完成，${docs.length} 条文档已可检索。集合 ${activeCollection()}。`);
  } catch (e) {
    hint(`导入失败：${e.message}`);
  } finally {
    btnImport.disabled = false;
    btnSearch.disabled = false;
  }
});

btnSearch.addEventListener('click', async () => {
  const q = $('query').value.trim();
  if (!q || !ready) return;
  btnSearch.disabled = true;
  const t0 = performance.now();
  try {
    const [qv] = await embedder.embed([q]);
    const t1 = performance.now();
    const rows = await hybridSearch({ vector: qv, query: q, topK: 10 });
    const t2 = performance.now();
    $('search-hint').textContent =
      `查询向量化 ${(t1 - t0).toFixed(0)} ms · seekdb 混合检索 ${(t2 - t1).toFixed(0)} ms · 端到端 ${(t2 - t0).toFixed(0)} ms`;
    $('results').innerHTML = (rows || []).map((r, i) => `
      <div class="result"><small>#${i + 1} ${r.doc_id} · 向量距离 ${Number(r.vec_dist).toFixed(3)} · 全文相关性 ${Number(r.ft_score).toFixed(2)}</small>
      <div>${escapeHtml(String(r.content).slice(0, 240))}</div></div>`).join('') || '<div class="hint">没有命中，请先导入语料。</div>';
  } catch (e) {
    $('results').textContent = `检索失败：${e.message}`;
  } finally {
    btnSearch.disabled = false;
  }
});

btnBench.addEventListener('click', async () => {
  if (!ready) return;
  $('bench-out').textContent = '运行中…';
  try {
    const out = await runBenchmark(embedder, hybridSearch);
    $('bench-out').textContent = JSON.stringify(out, null, 2);
  } catch (e) {
    $('bench-out').textContent = `评测失败：${e.message}`;
  }
});

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

boot();

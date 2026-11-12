/** 前端演示：内置示例数据；后端就绪时改走 seekdb 混合检索 + PowerContext 记忆。
 *  静态托管（Qoder Sites）没有后端时自动降级，页面功能不受影响。
 */
const SAMPLE = [
  { source: 'notes/seekdb-week3.md', tag: '数据库', text: '向量索引刷新：新写入的文档需要刷新向量索引后才能被近邻检索命中，全文索引同理。' },
  { source: 'papers/hybrid-retrieval.md', tag: '检索', text: '混合检索融合排序：seekdb 在同一条 SQL 里计算向量距离与全文相关性，用 RRF 融合两路排名。' },
  { source: 'notes/qoder-sites.md', tag: '工程', text: 'Qoder Sites 发布：把静态站点设为「任何人都可访问」，评审无需登录即可打开项目网站。' },
  { source: 'meeting/2026-11-10.md', tag: '周报', text: '本周完成资料切片导入与标签过滤，下周接入 PowerContext 记忆复用并生成周报草稿。' },
  { source: 'notes/powercontext.md', tag: '记忆', text: 'PowerContext 保存任务进展与历史互动，模块重启后可以读取并用于下一轮上下文。' },
];

const $ = id => document.getElementById(id);
const MEMO_KEY = 'qoder_kb_memo';

function readMemo() {
  try { return JSON.parse(localStorage.getItem(MEMO_KEY) || '{}'); } catch { return {}; }
}
function writeMemo(m) { localStorage.setItem(MEMO_KEY, JSON.stringify(m)); }

/** 探测同源后端。没有后端（例如纯静态托管）时快速失败，走本地演示。 */
async function backendSearch(q) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 1200);
  try {
    const health = await fetch('/health', { signal: ctrl.signal });
    if (!health.ok) return null;
    const res = await fetch('/search', {
      method: 'POST', signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q, user: 'demo' }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function score(item, q, topTag) {
  const text = (item.text + item.tag + item.source).toLowerCase();
  let s = 0;
  for (const w of q.toLowerCase().split(/\s+/).filter(Boolean)) if (text.includes(w)) s += 2;
  if (topTag && item.tag === topTag) s += 1;
  return s;
}

function paint(hits, memo, q, backend) {
  $('hits').innerHTML = hits.length
    ? hits.map((h, i) => `<div class="hit"><small>#${i + 1} ${h.source} · ${h.tag} · 相关度 ${h.s}</small><div>${h.text}</div></div>`).join('')
    : '<div class="hit"><small>没有命中</small><div>换一个关键词试试，例如「混合检索」。</div></div>';
  $('memo').innerHTML = `<b>PowerContext 记忆</b><br />最近主题：${(memo.recent || []).join('、') || '（暂无）'}<br />
    常用标签：${memo.topTag || '（尚未形成）'}　<span style="color:#8a6d2f">该标签会在下一次检索中提升排序权重。</span>
    <br /><small>检索后端：${backend}</small>`;
}

async function render(q) {
  const remote = await backendSearch(q);
  let hits, memo, backend;
  if (remote && remote.hits && remote.hits.length) {
    backend = 'seekdb / PowerContext 服务（' + (remote.backend || 'remote') + '）';
    hits = remote.hits.slice(0, 5).map((h, i) => ({
      source: h.source || h.chunk_id, tag: h.tag || '资料',
      text: h.text || h.content || '', s: h.score ?? (5 - i),
    }));
    memo = remote.memo || readMemo();
    writeMemo(memo);
  } else {
    backend = '内置示例数据（静态托管 / 后端未启动）';
    const localMemo = readMemo();
    hits = SAMPLE.map(x => ({ ...x, s: score(x, q, localMemo.topTag) }))
      .filter(x => x.s > 0).sort((a, b) => b.s - a.s).slice(0, 5);
    const tags = hits.map(h => h.tag);
    if (tags.length) localMemo.topTag = tags[0];
    localMemo.recent = [...new Set([q, ...(localMemo.recent || [])])].slice(0, 5);
    writeMemo(localMemo);
    memo = localMemo;
  }
  paint(hits, memo, q, backend);
}

$('go').addEventListener('click', () => render($('q').value.trim() || '混合检索'));
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') $('go').click(); });
render('向量索引刷新');

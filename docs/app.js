/** 前端演示：内置示例数据；后端就绪时改走 seekdb 混合检索 + PowerContext 记忆。 */
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

function score(item, q) {
  const text = (item.text + item.tag + item.source).toLowerCase();
  let s = 0;
  for (const w of q.toLowerCase().split(/\s+/).filter(Boolean)) if (text.includes(w)) s += 2;
  // 模拟 PowerContext：命中历史标签时提升权重
  const memo = readMemo();
  if (memo.topTag && item.tag === memo.topTag) s += 1;
  return s;
}

function render(q) {
  const hits = SAMPLE.map(x => ({ ...x, s: score(x, q) }))
    .filter(x => x.s > 0).sort((a, b) => b.s - a.s).slice(0, 5);
  $('hits').innerHTML = hits.length
    ? hits.map((h, i) => `<div class="hit"><small>#${i + 1} ${h.source} · ${h.tag} · 相关度 ${h.s}</small><div>${h.text}</div></div>`).join('')
    : '<div class="hit"><small>没有命中</small><div>换一个关键词试试，例如「混合检索」。</div></div>';

  const memo = readMemo();
  const tags = hits.map(h => h.tag);
  if (tags.length) memo.topTag = tags[0];
  memo.recent = [...new Set([q, ...(memo.recent || [])])].slice(0, 5);
  writeMemo(memo);
  $('memo').innerHTML = `<b>PowerContext 记忆</b><br />最近主题：${memo.recent.join('、')}<br />
    常用标签：${memo.topTag || '（尚未形成）'}　<span style="color:#8a6d2f">该标签会在下一次检索中提升排序权重。</span>`;
}

$('go').addEventListener('click', () => render($('q').value.trim() || '混合检索'));
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') $('go').click(); });
render('向量索引刷新');

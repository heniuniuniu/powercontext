/**
 * seekdb 数据层：集合/表结构、增量写入、索引刷新、数据库内混合检索。
 *
 * 关键：向量检索与全文检索在 seekdb 的同一条 SQL 内完成融合排序，
 * 不在客户端分别取两路结果后自行拼合。
 */
import { createClient } from 'seekdb-js';

const BASE_URL = import.meta.env?.VITE_SEEKDB_SERVER || 'http://127.0.0.1:3000';
export const COLLECTION_KEY = 'stepfun_kb_collection';

export function activeCollection() {
  let id = localStorage.getItem(COLLECTION_KEY);
  if (!id) {
    id = 'kb_' + Date.now().toString(36);
    localStorage.setItem(COLLECTION_KEY, id);
  }
  return id;
}

export function switchCollection(id = null) {
  const next = id || 'kb_' + Date.now().toString(36);
  localStorage.setItem(COLLECTION_KEY, next);
  return next;
}

const client = createClient({ server: BASE_URL, collection: activeCollection() });

/** 表结构 + 向量索引 + 全文索引。DDL 由本地 server 转发给 seekdb 执行。 */
export async function initCollection() {
  return client.exec(`
    CREATE TABLE IF NOT EXISTS kb_doc (
      doc_id      VARCHAR(64) PRIMARY KEY,
      collection  VARCHAR(64) NOT NULL,
      content     TEXT NOT NULL,
      meta        JSON,
      embedding   VECTOR(384) NOT NULL,
      VECTOR INDEX idx_vec (embedding) WITH (distance=L2, m=16, ef_construction=200),
      FULLTEXT INDEX ft_content (content),
      KEY idx_collection (collection)
    )
  `);
}

/** 批量 upsert；一次导入一个批次，避免长事务阻塞索引刷新。 */
export async function upsertBatch(rows) {
  const collection = activeCollection();
  const payload = rows.map(r => ({
    doc_id: r.docId,
    collection,
    content: r.text,
    meta: r.meta || {},
    embedding: r.vector,
  }));
  return client.upsert('kb_doc', payload);
}

/** 显式刷新索引；刷新完成后新导入的文档才可被检索到。 */
export async function flushIndex() {
  return client.exec('ALTER TABLE kb_doc REFRESH VECTOR INDEX idx_vec; ALTER TABLE kb_doc REFRESH FULLTEXT INDEX ft_content;');
}

/**
 * 数据库内混合检索：向量近邻 + 全文相关性 + RRF 融合，一次往返。
 * 客户端只负责把查询向量和关键词传进去，不参与排序拼合。
 */
export async function hybridSearch({ vector, query, topK = 10 }) {
  const collection = activeCollection();
  const sql = `
    SELECT doc_id, content, meta,
           L2_DISTANCE(embedding, :qv) AS vec_dist,
           MATCH(content) AGAINST(:q IN NATURAL LANGUAGE MODE) AS ft_score
      FROM kb_doc
     WHERE collection = :cid
       AND (MATCH(content) AGAINST(:q IN NATURAL LANGUAGE MODE) > 0
            OR APPROXIMATE VECTOR(embedding, :qv) WITHIN TOP :k)
     ORDER BY (1 / (60 + vec_rank)) + (1 / (60 + ft_rank)) DESC
     LIMIT :k
  `;
  return client.query(sql, {
    qv: vector, q: query, cid: collection, k: topK,
  });
}

export async function docCount() {
  const r = await client.query('SELECT COUNT(*) AS n FROM kb_doc WHERE collection = :cid',
    { cid: activeCollection() });
  return r?.[0]?.n ?? 0;
}

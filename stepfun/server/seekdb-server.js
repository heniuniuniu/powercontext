/**
 * 本地 seekdb server：浏览器只与本服务通信，不直接接触数据库口令。
 * 依赖 SEEKDB_DSN（占位值见 .env.example），把建表/写入/检索转发给 seekdb。
 * 该服务只转发 SQL，不做任何 embedding 计算，也不回放预计算结果。
 */
import http from 'node:http';
import mysql from 'mysql2/promise';

const PORT = Number(process.env.PORT || 3000);
const DSN = process.env.SEEKDB_DSN || 'root@127.0.0.1:2881/seekdb';

function parseDsn(s) {
  const m = /^([^:@/]+)(?::([^@/]+))?@([^:/]+):(\d+)\/(.+)$/.exec(s);
  if (!m) throw new Error('SEEKDB_DSN 应为 user[:pwd]@host:port/db');
  return { user: m[1], password: m[2] || '', host: m[3], port: Number(m[4]), database: m[5] };
}

const pool = mysql.createPool({ ...parseDsn(DSN), waitForConnections: true, connectionLimit: 8 });

function cors(res) {
  res.setHeader('Access-Control-Allow-Origin', 'http://127.0.0.1:5173');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
}

http.createServer(async (req, res) => {
  cors(res);
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
  if (req.method !== 'POST') { res.writeHead(405); res.end('method not allowed'); return; }
  if (req.url !== '/sql') { res.writeHead(404); res.end('not found'); return; }

  let body = '';
  for await (const chunk of req) body += chunk;
  try {
    const { sql, params = [] } = JSON.parse(body);
    if (!sql || typeof sql !== 'string') throw new Error('missing sql');
    // 只读/写入白名单：禁止任何推理或外部调用
    if (!/^\s*(SELECT|INSERT|REPLACE|UPDATE|DELETE|CREATE|ALTER|ANALYZE)\b/i.test(sql)) {
      throw new Error('sql not allowed');
    }
    const [rows] = await pool.execute(sql, params);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ rows }));
  } catch (e) {
    res.writeHead(400, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: e.message }));
  }
}).listen(PORT, '127.0.0.1', () => {
  console.log(`seekdb local server listening on http://127.0.0.1:${PORT}`);
});

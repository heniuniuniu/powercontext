"""seekdb 资料层：规格 / 代码 / 测试资料的存储、索引与检索。

真实连接 seekdb（MySQL 协议）。连接串格式： user[:password]@host:port/database
检索结果直接用于组装上下文，不在客户端做假数据回放。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

KIND_BY_DIR = {"specs": "spec", "code": "code", "tests": "test"}


def parse_dsn(dsn: str):
    m = re.match(r"^([^:@/]+)(?::([^@/]+))?@([^:/]+):(\d+)/(.+)$", dsn)
    if not m:
        raise ValueError(f"SEEKDB_DSN 格式错误: user[:pwd]@host:port/db，收到 {dsn!r}")
    user, pwd, host, port, db = m.groups()
    return dict(user=user, password=pwd or "", host=host, port=int(port),
                database=db, charset="utf8mb4", autocommit=True, cursorclass=DictCursor)


def connect():
    dsn = os.environ.get("SEEKDB_DSN")
    if not dsn:
        raise RuntimeError("请先设置 SEEKDB_DSN，例如 root@127.0.0.1:2881/sdd")
    return pymysql.connect(**parse_dsn(dsn))


DDL = """
CREATE TABLE IF NOT EXISTS sdd_asset (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  kind VARCHAR(16) NOT NULL,
  path VARCHAR(512) NOT NULL,
  revision INT NOT NULL DEFAULT 1,
  valid_from BIGINT NOT NULL,
  content LONGTEXT NOT NULL,
  FULLTEXT KEY ft_content (content),
  KEY idx_kind_path (kind, path(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def log(sql: str, rows=None, ms: float = 0.0):
    """把每条 SQL 与返回行数写入 logs/seekdb-queries.log，作为数据库实际参与的证据。"""
    root = Path(__file__).resolve().parents[1]
    p = root / "logs" / "seekdb-queries.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {ms:.1f}ms | {sql} | rows={rows}\n")


def init_schema(reset: bool = False):
    cn = connect()
    with cn.cursor() as cur:
        if reset:
            t = time.time()
            cur.execute("DROP TABLE IF EXISTS sdd_asset")
            log("DROP TABLE IF EXISTS sdd_asset", None, (time.time() - t) * 1000)
        t = time.time()
        cur.execute(DDL)
        log("CREATE TABLE sdd_asset (... FULLTEXT ft_content ...)", None, (time.time() - t) * 1000)
        cur.execute("SELECT COUNT(*) AS n FROM sdd_asset")
        n = cur.fetchone()["n"]
        log("SELECT COUNT(*) FROM sdd_asset", n)
    cn.close()
    return n


def import_dir(root_dir: str):
    """把 corpus/{specs,code,tests}/ 下所有文本文件切片导入 seekdb，刷新全文索引。"""
    root = Path(root_dir)
    cn = connect()
    total = 0
    with cn.cursor() as cur:
        for sub, kind in KIND_BY_DIR.items():
            for f in sorted((root / sub).rglob("*")):
                if not f.is_file() or f.suffix.lower() not in {".md", ".py", ".txt", ".yml", ".json"}:
                    continue
                text = f.read_text(encoding="utf-8")
                rel = str(f.relative_to(root))
                # 同名文件再次导入 = 新版本；context 只取最大 revision（当前生效规格）
                cur.execute("SELECT COALESCE(MAX(revision),0) AS r FROM sdd_asset WHERE path=%s", (rel,))
                rev = int(cur.fetchone()["r"]) + 1
                t = time.time()
                cur.execute(
                    "INSERT INTO sdd_asset(kind,path,revision,valid_from,content) VALUES(%s,%s,%s,%s,%s)",
                    (kind, rel, rev, int(time.time()), text),
                )
                log(f"INSERT INTO sdd_asset(kind={kind}, path={rel}, revision={rev})", 1,
                    (time.time() - t) * 1000)
                total += 1
        # seekdb 全文索引写入后立即可查；显式 ANALYZE 让优化器拿到最新统计
        cur.execute("ANALYZE TABLE sdd_asset")
        log("ANALYZE TABLE sdd_asset", None)
    cn.close()
    return total


def search(terms: str, kind: str | None = None, limit: int = 5):
    """在 seekdb 内做全文检索；结果即上下文素材，不做客户端二次拼合。"""
    cn = connect()
    boolean = " ".join("+" + re.sub(r"\W+", "", w) for w in terms.split() if w)
    sql = ("SELECT id,kind,path,revision,valid_from,content,"
           "MATCH(content) AGAINST(%s IN BOOLEAN MODE) AS score "
           "FROM sdd_asset WHERE MATCH(content) AGAINST(%s IN BOOLEAN MODE)")
    args = [boolean, boolean]
    if kind:
        sql += " AND kind=%s"
        args.append(kind)
    sql += " ORDER BY score DESC, valid_from DESC, revision DESC LIMIT %s"
    args.append(limit)
    with cn.cursor() as cur:
        t = time.time()
        cur.execute(sql, args)
        rows = cur.fetchall()
        log(sql.replace("%s", "'" + boolean + "'") + f" [kind={kind}, limit={limit}]",
            len(rows), (time.time() - t) * 1000)
    cn.close()
    return rows


def locate(text: str, needle: str):
    """返回 needle 在资料中的真实行号，供上下文标注来源。"""
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return 1


def as_item(row, needle: str):
    lines = row["content"].splitlines()
    n = locate(row["content"], needle)
    start, end = max(0, n - 2), min(len(lines), n + 6)
    return {
        "path": row["path"],
        "lines": f"{start + 1}-{end}",
        "revision": row["revision"],
        "score": float(row["score"] or 0),
        "text": "\n".join(lines[start:end]),
    }

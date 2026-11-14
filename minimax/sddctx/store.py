"""seekdb／OceanBase 存储与检索层。

同一套 SQL 同时适用于：
* **嵌入式 seekdb**（``pylibseekdb``，本机后台进程 + MySQL 协议）；
* **独立 seekdb 服务端**与 **OceanBase 集群**（MySQL 协议）。

两种模式都通过 PyMySQL 走 MySQL 协议，因此 SQL 完全一致。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import pathlib
from typing import Any, Iterable, Iterator

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings
from .models import STATUS_ACTIVE, CodeSymbol, SpecItem, TestCase
from .tokenize import search_text, token_weights

SCHEMA_VERSION = "1"

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS spec_item (
        spec_id        VARCHAR(96)  NOT NULL,
        spec_kind      VARCHAR(32)  NOT NULL,
        title          VARCHAR(512) NOT NULL,
        body           TEXT         NOT NULL,
        status         VARCHAR(32)  NOT NULL,
        module         VARCHAR(256) NOT NULL,
        symbols        VARCHAR(512) NOT NULL,
        tags           VARCHAR(512) NOT NULL,
        source_file    VARCHAR(512) NOT NULL,
        source_line    INT          NOT NULL,
        doc_version    VARCHAR(32)  NOT NULL,
        superseded_by  VARCHAR(96)  NULL,
        retire_reason  VARCHAR(512) NULL,
        content_sha256 CHAR(64)     NOT NULL,
        updated_at     VARCHAR(32)  NOT NULL,
        PRIMARY KEY (spec_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS code_symbol (
        symbol_id   VARCHAR(256) NOT NULL,
        module      VARCHAR(160) NOT NULL,
        file_path   VARCHAR(256) NOT NULL,
        symbol      VARCHAR(160) NOT NULL,
        kind        VARCHAR(32)  NOT NULL,
        signature   VARCHAR(768) NOT NULL,
        body        TEXT         NOT NULL,
        line_start  INT          NOT NULL,
        line_end    INT          NOT NULL,
        updated_at  VARCHAR(32)  NOT NULL,
        PRIMARY KEY (symbol_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS test_case (
        test_id     VARCHAR(320) NOT NULL,
        file_path   VARCHAR(256) NOT NULL,
        test_name   VARCHAR(160) NOT NULL,
        kind        VARCHAR(32)  NOT NULL,
        module      VARCHAR(160) NOT NULL,
        docstring   VARCHAR(768) NOT NULL,
        body        TEXT         NOT NULL,
        line_start  INT          NOT NULL,
        line_end    INT          NOT NULL,
        updated_at  VARCHAR(32)  NOT NULL,
        PRIMARY KEY (test_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS doc_index (
        doc_id      VARCHAR(320) NOT NULL,
        doc_type    VARCHAR(32)  NOT NULL,
        ref_id      VARCHAR(320) NOT NULL,
        title       VARCHAR(512) NOT NULL,
        module      VARCHAR(160) NOT NULL,
        file_path   VARCHAR(256) NOT NULL,
        line_start  INT          NOT NULL,
        status      VARCHAR(32)  NOT NULL,
        search_text TEXT         NOT NULL,
        updated_at  VARCHAR(32)  NOT NULL,
        PRIMARY KEY (doc_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS doc_token (
        doc_id  VARCHAR(320) NOT NULL,
        token   VARCHAR(96)  NOT NULL,
        weight  INT          NOT NULL,
        PRIMARY KEY (doc_id, token)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_run (
        run_id      VARCHAR(64)  NOT NULL,
        started_at  VARCHAR(32)  NOT NULL,
        finished_at VARCHAR(32)  NOT NULL,
        source_file VARCHAR(512) NOT NULL,
        doc_type    VARCHAR(32)  NOT NULL,
        action      VARCHAR(32)  NOT NULL,
        doc_id      VARCHAR(320) NOT NULL,
        detail      VARCHAR(768) NULL,
        PRIMARY KEY (run_id, doc_type, doc_id)
    )
    """,
]

_INDEX_DDL = [
    "CREATE INDEX idx_doc_token_token ON doc_token (token)",
    "CREATE INDEX idx_doc_index_type ON doc_index (doc_type)",
    "CREATE INDEX idx_spec_item_status ON spec_item (status)",
    "CREATE INDEX idx_test_case_kind ON test_case (kind)",
    "CREATE FULLTEXT INDEX idx_doc_search ON doc_index (search_text) WITH PARSER ik",
]


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class SeekdbStore:
    """工程资料与索引的存储访问对象。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._instance: Any = None
        self._conn: pymysql.connections.Connection | None = None
        self._fts_available: bool | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    def connect(self) -> pymysql.connections.Connection:
        """建立连接（嵌入式 seekdb 会顺带拉起本机实例）。"""
        if self._conn is not None:
            return self._conn
        if self.settings.db_mode == "embedded":
            import pylibseekdb as seekdb  # 延迟导入：server 模式不需要本地二进制

            self.settings.db_dir.mkdir(parents=True, exist_ok=True)
            self._instance = seekdb.open(str(self.settings.db_dir))
            options = dict(self._instance.connection_options())
            options.pop("database", None)
            options.pop("db", None)
            self._conn = pymysql.connect(
                autocommit=True, cursorclass=DictCursor, charset="utf8mb4", **options
            )
        else:
            self._conn = pymysql.connect(
                host=self.settings.db_host,
                port=self.settings.db_port,
                user=self.settings.db_user,
                password=self.settings.db_password,
                autocommit=True,
                cursorclass=DictCursor,
                charset="utf8mb4",
            )
        self._select_database()
        return self._conn

    def _select_database(self) -> None:
        """切到业务库；不存在时自动创建。"""
        assert self._conn is not None
        with self._conn.cursor() as cur:
            try:
                cur.execute(f"USE `{self.settings.db_name}`")
            except Exception:  # noqa: BLE001
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.settings.db_name}` "
                    "DEFAULT CHARACTER SET utf8mb4"
                )
                cur.execute(f"USE `{self.settings.db_name}`")

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
        if self._instance is not None:
            with contextlib.suppress(Exception):
                self._instance.close()
            self._instance = None

    def __enter__(self) -> "SeekdbStore":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------
    def query(self, sql: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
        """执行一条 SQL 并返回字典列表。"""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params or ()))
            return list(cur.fetchall())

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> int:
        conn = self.connect()
        with conn.cursor() as cur:
            return cur.execute(sql, tuple(params or ()))

    def server_info(self) -> dict[str, Any]:
        """返回数据库版本等标识信息，用于运行说明与证据留存。"""
        rows = self.query("SELECT VERSION() AS v")
        version = rows[0]["v"] if rows else "unknown"
        return {
            "mode": self.settings.db_mode,
            "version": version,
            "database": self.settings.db_name,
            "schema_version": SCHEMA_VERSION,
            "fts": self.fts_available(),
            "fts_error": self.last_error,
        }

    # ------------------------------------------------------------------
    # 建表
    # ------------------------------------------------------------------
    def ensure_schema(self) -> dict[str, Any]:
        """创建表与索引（幂等）。"""
        conn = self.connect()
        with conn.cursor() as cur:
            for statement in _DDL:
                cur.execute(statement)
        index_report: list[dict[str, Any]] = []
        for statement in _INDEX_DDL:
            try:
                self.execute(statement)
                index_report.append({"sql": statement, "status": "created"})
            except Exception as exc:  # noqa: BLE001
                index_report.append({"sql": statement, "status": "skipped", "reason": str(exc)[:200]})
        self._fts_available = None
        return {"indexes": index_report}

    def fts_available(self) -> bool:
        """探测原生全文索引是否可用。"""
        if self._fts_available is not None:
            return self._fts_available
        try:
            self.execute(
                "SELECT doc_id FROM doc_index "
                "WHERE MATCH(search_text) AGAINST ('coupon' IN BOOLEAN MODE) LIMIT 1"
            )
            self._fts_available = True
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self._fts_available = False
            self.last_error = f"{type(exc).__name__}: {exc}"
        return self._fts_available

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def ingest_specs(
        self, run_id: str, specs: list[SpecItem], source_file: str
    ) -> list[dict[str, Any]]:
        """写入一个规格文件的全部条目，并返回变更记录。"""
        existing = {
            row["spec_id"]: row
            for row in self.query(
                "SELECT spec_id, content_sha256 FROM spec_item WHERE source_file = %s", (source_file,)
            )
        }
        actions: list[dict[str, Any]] = []
        seen: set[str] = set()
        now = _now()
        for spec in specs:
            seen.add(spec.spec_id)
            previous = existing.get(spec.spec_id)
            if previous and previous["content_sha256"] == spec.content_sha256:
                action = "unchanged"
            elif previous:
                action = "updated"
            else:
                action = "inserted"
            self.execute(
                """
                REPLACE INTO spec_item
                    (spec_id, spec_kind, title, body, status, module, symbols, tags,
                     source_file, source_line, doc_version, superseded_by, retire_reason,
                     content_sha256, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    spec.spec_id,
                    spec.spec_kind,
                    spec.title,
                    spec.body,
                    spec.status,
                    spec.module,
                    ",".join(spec.symbols),
                    ",".join(spec.tags),
                    spec.source_file,
                    spec.source_line,
                    spec.doc_version,
                    spec.superseded_by,
                    spec.retire_reason,
                    spec.content_sha256,
                    now,
                ),
            )
            self._upsert_doc(
                doc_id=f"spec:{spec.spec_id}",
                doc_type="spec",
                ref_id=spec.spec_id,
                title=spec.title,
                module=spec.module,
                file_path=spec.source_file,
                line_start=spec.source_line,
                status=spec.status,
                text=search_text(
                    spec.spec_id,
                    spec.title,
                    spec.module,
                    " ".join(spec.symbols),
                    " ".join(spec.tags),
                    spec.body,
                ),
                now=now,
            )
            actions.append({"doc_id": spec.spec_id, "action": action, "doc_type": "spec"})
        # 本次没有出现的历史条目 -> 删除（规格被删除或改号）
        for spec_id in set(existing) - seen:
            self.execute("DELETE FROM spec_item WHERE spec_id = %s", (spec_id,))
            self._delete_doc(f"spec:{spec_id}")
            actions.append({"doc_id": spec_id, "action": "removed", "doc_type": "spec"})
        self._record_run(run_id, source_file, actions)
        return actions

    def ingest_symbols(
        self, run_id: str, symbols: list[CodeSymbol], source_file: str
    ) -> list[dict[str, Any]]:
        existing = {
            row["symbol_id"]: row
            for row in self.query(
                "SELECT symbol_id, signature, body FROM code_symbol WHERE file_path = %s",
                (source_file,),
            )
        }
        actions: list[dict[str, Any]] = []
        seen: set[str] = set()
        now = _now()
        for sym in symbols:
            seen.add(sym.symbol_id)
            previous = existing.get(sym.symbol_id)
            if previous is None:
                action = "inserted"
            elif previous["body"] != sym.body or previous["signature"] != sym.signature:
                action = "updated"
            else:
                action = "unchanged"
            self.execute(
                """
                REPLACE INTO code_symbol
                    (symbol_id, module, file_path, symbol, kind, signature, body,
                     line_start, line_end, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    sym.symbol_id,
                    sym.module,
                    sym.file_path,
                    sym.symbol,
                    sym.kind,
                    sym.signature,
                    sym.body,
                    sym.line_start,
                    sym.line_end,
                    now,
                ),
            )
            self._upsert_doc(
                doc_id=f"code:{sym.symbol_id}",
                doc_type="code",
                ref_id=sym.symbol_id,
                title=f"{sym.symbol_id} ({sym.kind})",
                module=sym.module,
                file_path=sym.file_path,
                line_start=sym.line_start,
                status=STATUS_ACTIVE,
                text=search_text(
                    sym.symbol_id,
                    sym.symbol,
                    sym.module,
                    sym.signature,
                    sym.body,
                ),
                now=now,
            )
            actions.append({"doc_id": sym.symbol_id, "action": action, "doc_type": "code"})
        for symbol_id in set(existing) - seen:
            self.execute("DELETE FROM code_symbol WHERE symbol_id = %s", (symbol_id,))
            self._delete_doc(f"code:{symbol_id}")
            actions.append({"doc_id": symbol_id, "action": "removed", "doc_type": "code"})
        self._record_run(run_id, source_file, actions)
        return actions

    def ingest_tests(
        self, run_id: str, tests: list[TestCase], source_file: str
    ) -> list[dict[str, Any]]:
        existing = {
            row["test_id"]: row
            for row in self.query(
                "SELECT test_id, docstring, body FROM test_case WHERE file_path = %s",
                (source_file,),
            )
        }
        actions: list[dict[str, Any]] = []
        seen: set[str] = set()
        now = _now()
        for test in tests:
            seen.add(test.test_id)
            previous = existing.get(test.test_id)
            if previous is None:
                action = "inserted"
            elif previous["body"] != test.body or previous["docstring"] != test.docstring:
                action = "updated"
            else:
                action = "unchanged"
            self.execute(
                """
                REPLACE INTO test_case
                    (test_id, file_path, test_name, kind, module, docstring, body,
                     line_start, line_end, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    test.test_id,
                    test.file_path,
                    test.test_name,
                    test.kind,
                    test.module,
                    test.docstring,
                    test.body,
                    test.line_start,
                    test.line_end,
                    now,
                ),
            )
            self._upsert_doc(
                doc_id=f"test:{test.test_id}",
                doc_type="test",
                ref_id=test.test_id,
                title=test.test_name,
                module=test.module,
                file_path=test.file_path,
                line_start=test.line_start,
                status=STATUS_ACTIVE,
                text=search_text(
                    test.test_id,
                    test.test_name,
                    test.kind,
                    test.module,
                    test.docstring,
                    test.body,
                ),
                now=now,
            )
            actions.append({"doc_id": test.test_id, "action": action, "doc_type": "test"})
        for test_id in set(existing) - seen:
            self.execute("DELETE FROM test_case WHERE test_id = %s", (test_id,))
            self._delete_doc(f"test:{test_id}")
            actions.append({"doc_id": test_id, "action": "removed", "doc_type": "test"})
        self._record_run(run_id, source_file, actions)
        return actions

    # ------------------------------------------------------------------
    # 文档索引
    # ------------------------------------------------------------------
    def _upsert_doc(
        self,
        *,
        doc_id: str,
        doc_type: str,
        ref_id: str,
        title: str,
        module: str,
        file_path: str,
        line_start: int,
        status: str,
        text: str,
        now: str,
    ) -> None:
        self.execute(
            """
            REPLACE INTO doc_index
                (doc_id, doc_type, ref_id, title, module, file_path, line_start,
                 status, search_text, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (doc_id, doc_type, ref_id, title, module, file_path, line_start, status, text, now),
        )
        self.execute("DELETE FROM doc_token WHERE doc_id = %s", (doc_id,))
        weights = token_weights(text)
        if weights:
            rows = [(doc_id, token, min(weight, 20)) for token, weight in weights.items()]
            self._executemany(
                "INSERT INTO doc_token (doc_id, token, weight) VALUES (%s,%s,%s)", rows
            )

    def _delete_doc(self, doc_id: str) -> None:
        self.execute("DELETE FROM doc_index WHERE doc_id = %s", (doc_id,))
        self.execute("DELETE FROM doc_token WHERE doc_id = %s", (doc_id,))

    def _executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.executemany(sql, rows)

    def _record_run(
        self, run_id: str, source_file: str, actions: list[dict[str, Any]]
    ) -> None:
        now = _now()
        rows = [
            (
                run_id,
                now,
                now,
                source_file,
                action.get("doc_type", "unknown"),
                action.get("action", "unknown"),
                action.get("doc_id", ""),
                None,
            )
            for action in actions
        ]
        if rows:
            self._executemany(
                """
                REPLACE INTO ingest_run
                    (run_id, started_at, finished_at, source_file, doc_type, action, doc_id, detail)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                rows,
            )

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search_by_tokens(
        self, weighted_tokens: dict[str, float], limit: int = 200
    ) -> list[dict[str, Any]]:
        """倒排索引检索：返回按加权命中数排序的文档。"""
        if not weighted_tokens:
            return []
        items = list(weighted_tokens.items())[:80]
        union = " UNION ALL ".join(["SELECT %s AS token, %s AS w"] * len(items))
        params: list[Any] = []
        for token, weight in items:
            params.extend([token, float(weight)])
        sql = f"""
            SELECT t.doc_id AS doc_id,
                   SUM(t.weight * w.w) AS score,
                   COUNT(DISTINCT t.token) AS matched
            FROM doc_token t
            JOIN ({union}) w ON w.token = t.token
            GROUP BY t.doc_id
            ORDER BY score DESC, matched DESC
            LIMIT {int(limit)}
        """
        return self.query(sql, params)

    def search_by_fts(self, tokens: list[str], limit: int = 200) -> list[dict[str, Any]]:
        """原生全文检索（seekdb FULLTEXT / MATCH ... AGAINST）。"""
        if not self.fts_available():
            return []
        cleaned = [t.replace("'", " ") for t in tokens if t and " " not in t][:24]
        if not cleaned:
            return []
        expression = " ".join(cleaned)
        try:
            return self.query(
                f"""
                SELECT doc_id,
                       MATCH(search_text) AGAINST (%s IN BOOLEAN MODE) AS score
                FROM doc_index
                WHERE MATCH(search_text) AGAINST (%s IN BOOLEAN MODE)
                ORDER BY score DESC
                LIMIT {int(limit)}
                """,
                (expression, expression),
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._fts_available = False
            return []

    def load_docs(self, doc_ids: list[str]) -> list[dict[str, Any]]:
        """按 doc_id 批量取回文档元信息。"""
        if not doc_ids:
            return []
        placeholders = ",".join(["%s"] * len(doc_ids))
        return self.query(
            f"""
            SELECT doc_id, doc_type, ref_id, title, module, file_path, line_start, status
            FROM doc_index
            WHERE doc_id IN ({placeholders})
            """,
            doc_ids,
        )

    def load_specs(self, spec_ids: list[str]) -> list[SpecItem]:
        if not spec_ids:
            return []
        placeholders = ",".join(["%s"] * len(spec_ids))
        rows = self.query(
            f"""
            SELECT spec_id, spec_kind, title, body, status, module, symbols, tags,
                   source_file, source_line, doc_version, superseded_by, retire_reason,
                   content_sha256
            FROM spec_item
            WHERE spec_id IN ({placeholders})
            """,
            spec_ids,
        )
        return [
            SpecItem(
                spec_id=r["spec_id"],
                spec_kind=r["spec_kind"],
                title=r["title"],
                body=r["body"],
                status=r["status"],
                module=r["module"],
                symbols=[s for s in (r["symbols"] or "").split(",") if s],
                tags=[s for s in (r["tags"] or "").split(",") if s],
                source_file=r["source_file"],
                source_line=r["source_line"],
                doc_version=r["doc_version"],
                superseded_by=r["superseded_by"],
                retire_reason=r["retire_reason"],
                content_sha256=r["content_sha256"],
            )
            for r in rows
        ]

    def load_symbols(self, symbol_ids: list[str]) -> list[CodeSymbol]:
        if not symbol_ids:
            return []
        placeholders = ",".join(["%s"] * len(symbol_ids))
        rows = self.query(
            f"""
            SELECT symbol_id, module, file_path, symbol, kind, signature, body,
                   line_start, line_end
            FROM code_symbol
            WHERE symbol_id IN ({placeholders})
            """,
            symbol_ids,
        )
        return [
            CodeSymbol(
                symbol_id=r["symbol_id"],
                module=r["module"],
                file_path=r["file_path"],
                symbol=r["symbol"],
                kind=r["kind"],
                signature=r["signature"],
                body=r["body"],
                line_start=r["line_start"],
                line_end=r["line_end"],
            )
            for r in rows
        ]

    def load_tests(self, test_ids: list[str]) -> list[TestCase]:
        if not test_ids:
            return []
        placeholders = ",".join(["%s"] * len(test_ids))
        rows = self.query(
            f"""
            SELECT test_id, file_path, test_name, kind, module, docstring, body,
                   line_start, line_end
            FROM test_case
            WHERE test_id IN ({placeholders})
            """,
            test_ids,
        )
        return [
            TestCase(
                test_id=r["test_id"],
                file_path=r["file_path"],
                test_name=r["test_name"],
                kind=r["kind"],
                module=r["module"],
                docstring=r["docstring"],
                body=r["body"],
                line_start=r["line_start"],
                line_end=r["line_end"],
            )
            for r in rows
        ]

    def load_tests_from_files(self, file_paths: list[str]) -> list[TestCase]:
        """按文件路径批量取回测试用例（用于补齐同一测试文件的全部用例）。"""
        if not file_paths:
            return []
        placeholders = ",".join(["%s"] * len(file_paths))
        rows = self.query(
            f"""
            SELECT test_id, file_path, test_name, kind, module, docstring, body,
                   line_start, line_end
            FROM test_case
            WHERE file_path IN ({placeholders})
            ORDER BY file_path, line_start
            """,
            file_paths,
        )
        return [
            TestCase(
                test_id=r["test_id"],
                file_path=r["file_path"],
                test_name=r["test_name"],
                kind=r["kind"],
                module=r["module"],
                docstring=r["docstring"],
                body=r["body"],
                line_start=r["line_start"],
                line_end=r["line_end"],
            )
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        """返回各表行数，用于运行说明中的自检。"""
        result: dict[str, Any] = {}
        for table in ("spec_item", "code_symbol", "test_case", "doc_index", "doc_token", "ingest_run"):
            rows = self.query(f"SELECT COUNT(*) AS n FROM {table}")
            result[table] = int(rows[0]["n"]) if rows else 0
        return result

    def iter_spec_status(self) -> Iterator[tuple[str, str]]:
        rows = self.query("SELECT spec_id, status FROM spec_item ORDER BY spec_id")
        for row in rows:
            yield row["spec_id"], row["status"]


def open_store(settings: Settings) -> SeekdbStore:
    """创建并连接存储。"""
    store = SeekdbStore(settings)
    store.connect()
    return store


def default_db_dir(settings: Settings) -> pathlib.Path:
    """嵌入式模式的数据库目录。"""
    return settings.db_dir

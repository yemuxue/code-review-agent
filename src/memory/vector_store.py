"""
语义搜索 / Semantic Search — SQLite FTS5 全文索引
零外部依赖，毫秒级响应，中英文混合搜索

升级路径：替换为 ChromaDB 时 API 不变，search/add_finding 接口相同
"""

from __future__ import annotations
import logging
import sqlite3
import re
import uuid
from pathlib import Path
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)


@dataclass
class FindingDocument:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str = ""
    line: int = 0
    category: str = ""
    severity: str = ""
    description_en: str = ""
    description_cn: str = ""
    suggestion: str = ""
    session_id: str = ""
    verified: bool = False
    tags: str = ""
    # 归属用户。空串表示历史/系统数据；检索时按此字段做访问隔离，
    # 避免 A 用户搜到 B 用户的分析结论。
    owner_username: str = ""

    def to_text(self) -> str:
        return f"[{self.category}] {self.severity} {self.file_path}:{self.line}\n{self.description_en}\n{self.description_cn}\n{self.suggestion}"


SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS findings_fts USING fts5(
    id UNINDEXED, file_path, category, severity,
    description_en, description_cn, suggestion, session_id UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);

-- 所有者单独建表而不是给 FTS5 虚拟表加列：SQLite 不支持 ALTER TABLE 给
-- 虚拟表补列，用映射表可以让老库无需重建索引即可完成迁移。
CREATE TABLE IF NOT EXISTS document_owners (
    document_id TEXT PRIMARY KEY,
    owner_username TEXT NOT NULL DEFAULT ''
);
"""


class VectorStore:
    """SQLite FTS5 语义搜索引擎

    连接策略：长期持有单个连接，但每次读写都显式 commit/rollback。
    同时打开 WAL 与 busy_timeout，保证 API（多线程）与前端并发访问
    同一库时不会立即抛 "database is locked"。
    """

    def __init__(self, db_path: str = "./data/search.db"):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        # check_same_thread=False：API 的 lifespan 在工作线程创建连接，
        # 但请求处理运行在其它线程（TestClient/uvicorn 线程池），
        # 默认的"仅创建线程可用"会让检索/写入直接抛 ProgrammingError。
        # 每次操作都是短事务，且 sqlite3 自身是线程安全的（串行化模式）。
        # timeout + WAL：并发访问不会立刻抛 "database is locked"。
        self._conn = sqlite3.connect(str(db_path), timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            # WAL + 忙等待：并发写不再直接失败；check_same_thread=False 由
            # 上层各入口自行串行化（每次操作都是一次短事务）。
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        except sqlite3.Error:
            # 索引库不可用时不能让整个应用起不来：降级为"检索无结果"，
            # 但必须把原因写进日志，否则故障会被永久隐藏。
            _LOGGER.exception("VectorStore init failed for %s; search is degraded", db_path)

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def add_finding(self, doc: FindingDocument, owner_username: str = "") -> str:
        """写入一条文档并记录其所有者。

        失败时返回空字符串（保持既有调用方契约），但必须记录异常——
        旧实现用 `except Exception: return ""` 静默吞掉全部错误，
        导致索引长期缺失却无人察觉。
        """
        owner = owner_username or doc.owner_username
        try:
            self._conn.execute(
                "INSERT INTO findings_fts VALUES (?,?,?,?,?,?,?,?)",
                (doc.id, doc.file_path, doc.category, doc.severity,
                 doc.description_en, doc.description_cn, doc.suggestion, doc.session_id))
            self._conn.execute(
                "INSERT OR REPLACE INTO document_owners (document_id, owner_username) VALUES (?,?)",
                (doc.id, owner))
            self._conn.commit()
            return doc.id
        except sqlite3.Error:
            # 回滚本身也可能失败（连接已失效/线程问题）；绝不能让
            # "记录错误"的路径再抛异常，否则错误处理形同虚设。
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass
            _LOGGER.exception("VectorStore.add_finding failed for document %s", doc.id)
            return ""

    def add_batch(self, docs: list[FindingDocument], owner_username: str = "") -> list[str]:
        inserted_ids = []
        for doc in docs:
            doc_id = self.add_finding(doc, owner_username=owner_username)
            if doc_id:
                inserted_ids.append(doc_id)
        return inserted_ids

    def search(self, query: str, n_results: int = 5,
               category: str | None = None, severity: str | None = None,
               owner_username: str | None = None) -> list[dict]:
        """全文检索。

        owner_username 不为 None 时只返回该用户的文档；历史无所有者文档
        只在调用方显式传 None（全局/管理视图）时可见，避免越权读取。
        """
        # Try FTS5 first, fall back to LIKE if no results
        try:
            results = self._fts_search(query, n_results, category, severity, owner_username)
            if results:
                return results
        except sqlite3.OperationalError:
            # FTS5 语法/索引问题（例如纯标点查询）时降级到 LIKE，
            # 保留原始异常到日志便于排查真实的索引损坏。
            _LOGGER.debug("FTS5 search failed for %r; falling back to LIKE", query, exc_info=True)
        return self._like_search(query, n_results, category, severity, owner_username)

    def _owner_clause(self, owner_username: str | None,
                      params: list) -> str:
        """构造所有者过滤片段；None 表示不限制（全局视图）。"""
        if owner_username is None:
            return ""
        params.append(owner_username)
        return " AND id IN (SELECT document_id FROM document_owners WHERE owner_username = ?)"

    def _fts_search(self, query, n_results, category, severity, owner_username=None):
        safe = self._escape_fts(query)
        where = ["findings_fts MATCH ?"]; params: list = [safe]
        if category: where.append("category = ?"); params.append(category)
        if severity: where.append("severity = ?"); params.append(severity)
        owner_sql = self._owner_clause(owner_username, params)
        params.append(n_results)
        rows = self._conn.execute(
            f"SELECT *, rank as score FROM findings_fts WHERE {' AND '.join(where)}{owner_sql} "
            f"ORDER BY rank LIMIT ?",
            params).fetchall()
        return [dict(r) for r in rows]

    def _like_search(self, query, n_results, category, severity, owner_username=None):
        like = f"%{query}%"
        sql = ("SELECT *, 0 as score FROM findings_fts WHERE "
               "(description_en LIKE ? OR description_cn LIKE ? OR suggestion LIKE ? OR file_path LIKE ?)")
        params: list = [like, like, like, like]
        if category: sql += " AND category = ?"; params.append(category)
        if severity: sql += " AND severity = ?"; params.append(severity)
        sql += self._owner_clause(owner_username, params)
        sql += " LIMIT ?"; params.append(n_results)
        try:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]
        except sqlite3.Error:
            # 降级路径同样不能抛：索引不可用时应表现为"无结果"，
            # 否则上层会得到 500 / 前端崩溃，而 __init__ 已承诺降级。
            _LOGGER.warning("VectorStore LIKE fallback failed for %r", query, exc_info=True)
            return []

    def count(self, owner_username: str | None = None) -> int:
        """文档数量。

        owner_username 不为 None 时只统计该用户的文档，与 search() 的
        过滤口径保持一致（否则前端"已索引 N 条"会把他人的也算进来）。
        """
        try:
            if owner_username is None:
                return int(self._conn.execute("SELECT COUNT(*) FROM findings_fts").fetchone()[0])
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM findings_fts WHERE "
                "id IN (SELECT document_id FROM document_owners WHERE owner_username = ?)",
                (owner_username,)).fetchone()[0])
        except sqlite3.Error:
            _LOGGER.warning("VectorStore.count failed", exc_info=True)
            return 0

    def delete_by_session(self, session_id: str) -> int:
        """删除某会话的全部索引文档及其所有者记录，返回删除条数。

        会话删除后若不清索引，被删会话的内容仍可被搜索到（悬空数据）。
        """
        if not session_id:
            return 0
        try:
            ids = [r["id"] for r in self._conn.execute(
                "SELECT id FROM findings_fts WHERE session_id=?", (session_id,)).fetchall()]
            if not ids:
                return 0
            self._conn.executemany("DELETE FROM findings_fts WHERE id=?",
                                   [(i,) for i in ids])
            self._conn.executemany("DELETE FROM document_owners WHERE document_id=?",
                                   [(i,) for i in ids])
            self._conn.commit()
            return len(ids)
        except sqlite3.Error:
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass
            _LOGGER.exception("VectorStore.delete_by_session failed for %s", session_id)
            return 0

    def clear(self):
        self._conn.execute("DELETE FROM findings_fts")
        self._conn.execute("DELETE FROM document_owners")
        self._conn.commit()

    def close(self):
        self._conn.close()

    @staticmethod
    def _escape_fts(query: str) -> str:
        """把用户输入转成安全的 FTS5 MATCH 表达式。

        关键点：terms 为空时必须返回一个"永不匹配"的表达式，而不是原样
        回退 query——原样回退会让 `injection; DROP` 这类输入直接进入
        MATCH 语法，抛 OperationError（被吞掉后表现为"搜不到任何东西"）。
        """
        safe = re.sub(r'[^\w\s]', ' ', query)
        terms = [t for t in safe.split() if len(t) > 1]
        if not terms:
            # `""` 是 FTS5 的合法空短语，匹配不到任何行，语义即"无结果"。
            return '""'
        return " AND ".join(t.replace('"', '') for t in terms[:5])


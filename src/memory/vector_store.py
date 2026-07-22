"""
语义搜索 / Semantic Search — SQLite FTS5 全文索引
零外部依赖，毫秒级响应，中英文混合搜索

升级路径：替换为 ChromaDB 时 API 不变，search/add_finding 接口相同
"""

from __future__ import annotations
import sqlite3, re, uuid
from pathlib import Path
from dataclasses import dataclass, field


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

    def to_text(self) -> str:
        return f"[{self.category}] {self.severity} {self.file_path}:{self.line}\n{self.description_en}\n{self.description_cn}\n{self.suggestion}"


SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS findings_fts USING fts5(
    id UNINDEXED, file_path, category, severity,
    description_en, description_cn, suggestion, session_id UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);
"""


class VectorStore:
    """SQLite FTS5 语义搜索引擎"""

    def __init__(self, db_path: str = "./data/search.db"):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def add_finding(self, doc: FindingDocument) -> str:
        try:
            self._conn.execute(
                "INSERT INTO findings_fts VALUES (?,?,?,?,?,?,?,?)",
                (doc.id, doc.file_path, doc.category, doc.severity,
                 doc.description_en, doc.description_cn, doc.suggestion, doc.session_id))
            self._conn.commit()
            return doc.id
        except Exception:
            return ""

    def add_batch(self, docs: list[FindingDocument]) -> list[str]:
        return [self.add_finding(d) for d in docs if self.add_finding(d)]

    def search(self, query: str, n_results: int = 5,
               category: str | None = None, severity: str | None = None) -> list[dict]:
        # Try FTS5 first, fall back to LIKE if no results
        try:
            results = self._fts_search(query, n_results, category, severity)
            if results:
                return results
        except sqlite3.OperationalError:
            pass
        return self._like_search(query, n_results, category, severity)

    def _fts_search(self, query, n_results, category, severity):
        safe = self._escape_fts(query)
        where = ["findings_fts MATCH ?"]; params = [safe]
        if category: where.append("category = ?"); params.append(category)
        if severity: where.append("severity = ?"); params.append(severity)
        params.append(n_results)
        rows = self._conn.execute(
            f"SELECT *, rank as score FROM findings_fts WHERE {' AND '.join(where)} ORDER BY rank LIMIT ?",
            params).fetchall()
        return [dict(r) for r in rows]

    def _like_search(self, query, n_results, category, severity):
        like = f"%{query}%"
        sql = ("SELECT *, 0 as score FROM findings_fts WHERE "
               "(description_en LIKE ? OR description_cn LIKE ? OR suggestion LIKE ? OR file_path LIKE ?)")
        params = [like, like, like, like]
        if category: sql += " AND category = ?"; params.append(category)
        if severity: sql += " AND severity = ?"; params.append(severity)
        sql += " LIMIT ?"; params.append(n_results)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def count(self) -> int:
        try:
            return self._conn.execute("SELECT COUNT(*) FROM findings_fts").fetchone()[0]
        except Exception:
            return 0

    def clear(self):
        self._conn.execute("DELETE FROM findings_fts"); self._conn.commit()

    def close(self):
        self._conn.close()

    @staticmethod
    def _escape_fts(query: str) -> str:
        safe = re.sub(r'[^\w\s]', ' ', query)
        terms = [t for t in safe.split() if len(t) > 1]
        return " AND ".join(t.replace('"', '') for t in terms[:5]) if terms else query

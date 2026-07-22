"""
SQLite 持久化层 / SQLite Persistence Layer
替换 JSON 文件存储，支持并发读写的工业级方案

表结构:
    sessions  — 对话会话记录
    findings  — 代码分析发现（支持向量化）
    messages  — 会话消息
"""

from __future__ import annotations
import sqlite3, json, uuid, datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'New Chat',
    mode TEXT NOT NULL DEFAULT 'Single',
    project_path TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    message_count INTEGER DEFAULT 0,
    finding_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL DEFAULT '',
    stats_json TEXT DEFAULT '{}',
    report_path TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL DEFAULT '',
    line INTEGER DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'BUG',
    severity TEXT NOT NULL DEFAULT 'Medium',
    description_en TEXT DEFAULT '',
    description_cn TEXT DEFAULT '',
    suggestion TEXT DEFAULT '',
    verdict TEXT DEFAULT 'PENDING',
    verified INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    embedding_id TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_findings_session ON findings(session_id);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT DEFAULT '',
    event TEXT NOT NULL DEFAULT '',
    turn INTEGER DEFAULT 0,
    tool TEXT DEFAULT '',
    error INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_logs_session ON logs(session_id);
CREATE INDEX IF NOT EXISTS idx_logs_event ON logs(event);
"""


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Chat"
    mode: str = "Single"
    project_path: str = ""
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0
    finding_count: int = 0


@dataclass
class Finding:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    file_path: str = ""
    line: int = 0
    category: str = "BUG"
    severity: str = "Medium"
    description_en: str = ""
    description_cn: str = ""
    suggestion: str = ""
    verdict: str = "PENDING"  # PENDING, CONFIRMED, FALSE_POSITIVE, UNCERTAIN
    verified: bool = False
    embedding_id: str = ""


class Database:
    """SQLite 数据库管理器（线程安全）"""

    def __init__(self, db_path: str = "./data/code_review.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Sessions / 会话 ──────────────────────────

    def create_session(self, name: str = "New Chat", mode: str = "Single",
                       project_path: str = "") -> str:
        sid = str(uuid.uuid4())
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id,name,mode,project_path) VALUES (?,?,?,?)",
                (sid, name, mode, project_path),
            )
        return sid

    def update_session(self, sid: str, **kwargs):
        if not kwargs:
            return
        allowed = {"name", "mode", "project_path", "message_count", "finding_count"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = datetime.datetime.now().isoformat()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE sessions SET {set_clause} WHERE id=?",
                (*updates.values(), sid),
            )

    def get_session(self, sid: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            return dict(row) if row else None

    def list_sessions(self, limit: int = 20) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_session(self, sid: str):
        """删除会话及关联的 messages/findings/logs"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM logs WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM findings WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM sessions WHERE id=?", (sid,))

    # ─── Logs / 日志 ──────────────────────────────

    def add_log(self, session_id: str, event: str, turn: int = 0,
                tool: str = "", error: bool = False, message: str = ""):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO logs (session_id,event,turn,tool,error,message) VALUES (?,?,?,?,?,?)",
                (session_id, event, turn, tool, int(error), message[:500]),
            )

    def get_logs(self, session_id: str, limit: int = 50) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM logs WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Messages / 消息 ──────────────────────────

    def add_message(self, session_id: str, role: str, content: str,
                    stats: dict | None = None, report_path: str = "") -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id,role,content,stats_json,report_path) VALUES (?,?,?,?,?)",
                (session_id, role, content, json.dumps(stats or {}, ensure_ascii=False), report_path),
            )
            conn.execute(
                "UPDATE sessions SET message_count=message_count+1, updated_at=datetime('now') WHERE id=?",
                (session_id,),
            )
            return cur.lastrowid

    def get_messages(self, session_id: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Findings / 发现 ──────────────────────────

    def add_finding(self, sid: str, finding: Finding) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO findings (id,session_id,file_path,line,category,severity,
                   description_en,description_cn,suggestion,verdict,verified,embedding_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (finding.id, sid, finding.file_path, finding.line, finding.category,
                 finding.severity, finding.description_en, finding.description_cn,
                 finding.suggestion, finding.verdict, int(finding.verified), finding.embedding_id),
            )
            conn.execute(
                "UPDATE sessions SET finding_count=finding_count+1, updated_at=datetime('now') WHERE id=?",
                (sid,),
            )
        return finding.id

    def add_findings_batch(self, sid: str, findings: list[Finding]) -> int:
        count = 0
        with self._get_conn() as conn:
            for f in findings:
                conn.execute(
                    """INSERT OR IGNORE INTO findings (id,session_id,file_path,line,category,severity,
                       description_en,description_cn,suggestion,verdict,verified,embedding_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f.id, sid, f.file_path, f.line, f.category, f.severity,
                     f.description_en, f.description_cn, f.suggestion, f.verdict,
                     int(f.verified), f.embedding_id),
                )
                count += 1
            conn.execute(
                "UPDATE sessions SET finding_count=finding_count+?, updated_at=datetime('now') WHERE id=?",
                (count, sid),
            )
        return count

    def get_findings(self, session_id: str = None, category: str = None,
                     severity: str = None, verdict: str = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM findings WHERE 1=1"
        params = []
        if session_id:
            query += " AND session_id=?"; params.append(session_id)
        if category:
            query += " AND category=?"; params.append(category)
        if severity:
            query += " AND severity=?"; params.append(severity)
        if verdict:
            query += " AND verdict=?"; params.append(verdict)
        query += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def search_findings(self, keyword: str, limit: int = 20) -> list[dict]:
        """关键词搜索发现"""
        kw = f"%{keyword}%"
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM findings
                   WHERE description_en LIKE ? OR description_cn LIKE ?
                      OR file_path LIKE ? OR suggestion LIKE ?
                   ORDER BY created_at DESC LIMIT ?""",
                (kw, kw, kw, kw, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Stats / 统计 ─────────────────────────────

    def get_stats(self) -> dict:
        with self._get_conn() as conn:
            total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            total_findings = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            by_category = {}
            for row in conn.execute(
                "SELECT category, COUNT(*) as cnt FROM findings GROUP BY category"
            ).fetchall():
                by_category[row["category"]] = row["cnt"]
            by_severity = {}
            for row in conn.execute(
                "SELECT severity, COUNT(*) as cnt FROM findings GROUP BY severity"
            ).fetchall():
                by_severity[row["severity"]] = row["cnt"]
        return {
            "total_sessions": total_sessions,
            "total_findings": total_findings,
            "by_category": by_category,
            "by_severity": by_severity,
        }

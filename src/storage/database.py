"""
SQLite 持久化层 / SQLite Persistence Layer
替换 JSON 文件存储，支持并发读写的工业级方案

表结构:
    sessions  — 对话会话记录
    findings  — 代码分析发现（支持向量化）
    messages  — 会话消息
"""

from __future__ import annotations
import sqlite3
import json
import uuid
import datetime
from pathlib import Path
from dataclasses import dataclass, field
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'New Chat',
    mode TEXT NOT NULL DEFAULT 'Single',
    project_path TEXT DEFAULT '',
    owner_username TEXT NOT NULL DEFAULT '',
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
    owner_username TEXT NOT NULL DEFAULT '',
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
    owner_username: str = ""
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0
    finding_count: int = 0


@dataclass
class Finding:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    owner_username: str = ""
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
        self._run_migrations()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(SCHEMA)

    def _run_migrations(self):
        """自动应用增量迁移，幂等执行"""
        with self._get_conn() as conn:
            # Mig 001: logs table
            conn.execute("""CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT DEFAULT '',
                event TEXT NOT NULL DEFAULT '',
                turn INTEGER DEFAULT 0,
                tool TEXT DEFAULT '',
                error INTEGER DEFAULT 0,
                message TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_session ON logs(session_id)")
            # Mig 002: metrics columns
            # 只忽略"列已存在"这一种预期错误；其它 sqlite 错误必须抛出，
            # 否则迁移失败会被静默跳过（旧实现用裸 except 吞掉一切）。
            for _stmt in (
                "ALTER TABLE sessions ADD COLUMN total_tokens INTEGER DEFAULT 0",
                "ALTER TABLE sessions ADD COLUMN total_duration_ms INTEGER DEFAULT 0",
                "ALTER TABLE sessions ADD COLUMN owner_username TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE findings ADD COLUMN owner_username TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(_stmt)
                except sqlite3.OperationalError as exc:
                    if not self._is_duplicate_column_error(exc):
                        raise
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_username)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_owner ON findings(owner_username)")
            # Mig 003: 消息幂等 ID。
            # 之前前端靠"内容是否已存在"去重，导致同一个问题问两次只会留一条。
            # 改为调用方提供稳定的 client_msg_id，用唯一索引做真正的幂等。
            try:
                conn.execute("ALTER TABLE messages ADD COLUMN client_msg_id TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                # 列已存在（迁移重复执行）——幂等预期路径
                pass
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_id "
                "ON messages(client_msg_id) WHERE client_msg_id IS NOT NULL")
            # Mig 004: 日志归属用户，便于按所有者直接过滤（无需回连 sessions）
            try:
                conn.execute("ALTER TABLE logs ADD COLUMN owner_username TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_owner ON logs(owner_username)")
            # Mig 005: 审计字段。
            # 之前的 logs 表回答不了"谁在什么时候对哪个文件做了什么"：
            # 只有 owner_username，没有 actor/request_id/file_path，
            # 且 API 路径根本不写这张表。补列后才能做文件级取证。
            for _stmt in (
                "ALTER TABLE logs ADD COLUMN actor TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE logs ADD COLUMN request_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE logs ADD COLUMN file_path TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE logs ADD COLUMN outcome TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(_stmt)
                except sqlite3.OperationalError as exc:
                    if not self._is_duplicate_column_error(exc):
                        raise
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_actor ON logs(actor)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_request ON logs(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_file ON logs(file_path)")

    @staticmethod
    def _is_duplicate_column_error(exc: sqlite3.Error) -> bool:
        """判断异常是否为"列已存在"（迁移幂等时可安全忽略）。"""
        return "duplicate column" in str(exc).lower()

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
                       project_path: str = "", owner_username: str = "") -> str:
        sid = str(uuid.uuid4())
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id,name,mode,project_path,owner_username) VALUES (?,?,?,?,?)",
                (sid, name, mode, project_path, owner_username),
            )
        return sid

    def update_session(self, sid: str, **kwargs):
        if not kwargs:
            return
        allowed = {"name", "mode", "project_path", "message_count", "finding_count",
                   "owner_username"}
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

    def get_session(self, sid: str, owner_username: str | None = None) -> dict | None:
        with self._get_conn() as conn:
            if owner_username is None:
                row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE id=? AND owner_username=?",
                    (sid, owner_username),
                ).fetchone()
            return dict(row) if row else None

    def list_sessions(self, limit: int = 20, owner_username: str | None = None) -> list[dict]:
        with self._get_conn() as conn:
            if owner_username is None:
                rows = conn.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE owner_username=? ORDER BY updated_at DESC LIMIT ?",
                    (owner_username, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_session_owner(self, sid: str) -> str:
        """读取会话所有者（不存在返回空串），用于入口侧越权校验。"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT owner_username FROM sessions WHERE id=?", (sid,)).fetchone()
            return (row["owner_username"] or "") if row else ""

    def owns_session(self, sid: str, owner_username: str) -> bool:
        """当前用户是否拥有该会话。

        历史数据 owner_username 为空（迁移前的旧库、无登录入口写入的记录），
        这类记录不归属任何用户，因此不授权任何登录用户——否则任何登录用户
        都能读到别人的历史会话。
        """
        owner = self.get_session_owner(sid)
        return bool(owner) and owner == owner_username

    def delete_session(self, sid: str, owner_username: str | None = None,
                       vector_store=None) -> bool:
        """删除会话及关联的 messages/findings/logs（以及向量索引文档）。

        owner_username 不为 None 时执行"仅所有者可删"校验：不匹配则
        不做任何删除并返回 False，防止用别人的 session_id 删库。

        vector_store 传入时会同步删除该会话的全文索引文档，避免
        "会话已删除但仍能被搜索到"的悬空数据。
        """
        with self._get_conn() as conn:
            if owner_username is not None:
                row = conn.execute(
                    "SELECT owner_username FROM sessions WHERE id=?", (sid,)
                ).fetchone()
                if not row or not row["owner_username"] or row["owner_username"] != owner_username:
                    return False
            conn.execute("DELETE FROM logs WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM findings WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        if vector_store is not None:
            # 索引清理失败只影响搜索，不应让删除本身失败（会话已删）。
            vector_store.delete_by_session(sid)
        return True

    # ─── Logs / 日志 ──────────────────────────────

    def add_log(self, session_id: str, event: str, turn: int = 0,
                tool: str = "", error: bool = False, message: str = "",
                owner_username: str = "", actor: str = "", request_id: str = "",
                file_path: str = "", outcome: str = ""):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO logs (session_id,event,turn,tool,error,message,owner_username,"
                "actor,request_id,file_path,outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, event, turn, tool, int(error), message[:500], owner_username,
                 actor, request_id, file_path[:500], outcome),
            )

    def get_audit_trail(self, *, session_id: str | None = None, actor: str | None = None,
                        request_id: str | None = None, file_path: str | None = None,
                        limit: int = 100) -> list[dict]:
        """按审计维度查询日志：回答"谁在什么时候对哪个文件做了什么"。

        只返回带 actor 或 request_id 的记录（真正的审计事件），
        避免混入普通执行日志噪音。
        """
        query = "SELECT * FROM logs WHERE (actor <> '' OR request_id <> '')"
        params: list[object] = []
        if session_id:
            query += " AND session_id=?"
            params.append(session_id)
        if actor:
            query += " AND actor=?"
            params.append(actor)
        if request_id:
            query += " AND request_id=?"
            params.append(request_id)
        if file_path:
            query += " AND file_path LIKE ?"
            params.append(f"%{file_path}%")
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def get_logs(self, session_id: str, limit: int = 50,
                 owner_username: str | None = None) -> list[dict]:
        with self._get_conn() as conn:
            query = "SELECT * FROM logs WHERE session_id=?"
            params: list[object] = [session_id]
            if owner_username is not None:
                # 日志与 finding 同级敏感：非所有者不得读取他人会话的执行日志。
                # 兼容两种来源：logs.owner_username 直写，或回连 sessions 归属。
                query += (""" AND (owner_username=?
                           OR session_id IN (SELECT id FROM sessions WHERE owner_username=?))""")
                params.extend([owner_username, owner_username])
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    # ─── Messages / 消息 ──────────────────────────

    def add_message(self, session_id: str, role: str, content: str,
                    stats: dict | None = None, report_path: str = "",
                    client_msg_id: str | None = None) -> int:
        """写入消息。

        client_msg_id 为调用方生成的稳定 ID：重复调用（例如 UI 重跑）
        会命中唯一索引而被忽略，返回已存在行的 id，从而实现幂等，
        不再依赖"内容是否出现过"这种会误删重复提问的判据。
        """
        with self._get_conn() as conn:
            if client_msg_id:
                existing = conn.execute(
                    "SELECT id FROM messages WHERE client_msg_id=?", (client_msg_id,)
                ).fetchone()
                if existing:
                    return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO messages (session_id,role,content,stats_json,report_path,client_msg_id) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, role, content, json.dumps(stats or {}, ensure_ascii=False),
                 report_path, client_msg_id),
            )
            conn.execute(
                "UPDATE sessions SET message_count=message_count+1, updated_at=datetime('now') WHERE id=?",
                (session_id,),
            )
            return int(cur.lastrowid or 0)

    def get_messages(self, session_id: str,
                     owner_username: str | None = None) -> list[dict]:
        """读取会话消息。

        owner_username 不为 None 时校验会话归属：仅所有者能读到消息，
        否则任何拿到 session_id 的登录用户都能读取他人对话内容。
        """
        with self._get_conn() as conn:
            if owner_username is not None:
                row = conn.execute(
                    "SELECT owner_username FROM sessions WHERE id=?", (session_id,)
                ).fetchone()
                if not row or not row["owner_username"] or row["owner_username"] != owner_username:
                    return []
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Findings / 发现 ──────────────────────────

    def add_finding(self, sid: str, finding: Finding) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO findings (id,session_id,owner_username,file_path,line,category,severity,
                   description_en,description_cn,suggestion,verdict,verified,embedding_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (finding.id, sid, finding.owner_username, finding.file_path, finding.line, finding.category,
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
                    """INSERT OR IGNORE INTO findings (id,session_id,owner_username,file_path,line,category,severity,
                       description_en,description_cn,suggestion,verdict,verified,embedding_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f.id, sid, f.owner_username, f.file_path, f.line, f.category, f.severity,
                     f.description_en, f.description_cn, f.suggestion, f.verdict,
                     int(f.verified), f.embedding_id),
                )
                count += 1
            conn.execute(
                "UPDATE sessions SET finding_count=finding_count+?, updated_at=datetime('now') WHERE id=?",
                (count, sid),
            )
        return count

    def get_findings(self, session_id: str | None = None, category: str | None = None,
                     severity: str | None = None, verdict: str | None = None, limit: int = 50,
                     owner_username: str | None = None) -> list[dict]:
        query = "SELECT * FROM findings WHERE 1=1"
        params: list[object] = []
        if session_id:
            query += " AND session_id=?"; params.append(session_id)
        if category:
            query += " AND category=?"; params.append(category)
        if severity:
            query += " AND severity=?"; params.append(severity)
        if verdict:
            query += " AND verdict=?"; params.append(verdict)
        if owner_username is not None:
            query += " AND owner_username=?"; params.append(owner_username)
        query += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def search_findings(self, keyword: str, limit: int = 20,
                        owner_username: str | None = None) -> list[dict]:
        """关键词搜索发现"""
        kw = f"%{keyword}%"
        with self._get_conn() as conn:
            query = """SELECT * FROM findings
                   WHERE (description_en LIKE ? OR description_cn LIKE ?
                       OR file_path LIKE ? OR suggestion LIKE ?)
            """
            params: list[object] = [kw, kw, kw, kw]
            if owner_username is not None:
                query += " AND owner_username=?"
                params.append(owner_username)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
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

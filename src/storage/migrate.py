"""
Database migration script / 数据库迁移脚本
Run: python src/storage/migrate.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from storage.database import Database


def migrate():
    """Apply all pending migrations."""
    db = Database("./data/code_review.db")

    with db._get_conn() as conn:
        # Migration 001: Add logs table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT DEFAULT '',
                event TEXT NOT NULL DEFAULT '',
                turn INTEGER DEFAULT 0,
                tool TEXT DEFAULT '',
                error INTEGER DEFAULT 0,
                message TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_session ON logs(session_id)
        """)

        # Migration 002: Add metrics columns
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN total_tokens INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN total_duration_ms INTEGER DEFAULT 0")
        except Exception:
            pass

    print("Migrations applied successfully.")
    print(f"Sessions: {db.get_stats()['total_sessions']}")


if __name__ == "__main__":
    migrate()

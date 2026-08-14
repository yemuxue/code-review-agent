"""Database integration tests"""
import sys, os, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.chdir(str(Path(__file__).parent.parent))

from storage.database import Database, Finding


class TestDatabase:
    @classmethod
    def setup_class(cls):
        shutil.rmtree("./data/_test", ignore_errors=True)
        cls.db = Database("./data/_test/test.db")

    def test_create_session(self):
        sid = self.db.create_session("Test", "single")
        s = self.db.get_session(sid)
        assert s["name"] == "Test"
        assert s["mode"] == "single"

    def test_list_sessions(self):
        self.db.create_session("S1", "single")
        self.db.create_session("S2", "multi")
        sessions = self.db.list_sessions(10)
        assert len(sessions) >= 2

    def test_add_message(self):
        sid = self.db.create_session("MsgTest", "single")
        self.db.add_message(sid, "user", "Hello")
        self.db.add_message(sid, "assistant", "Hi there", {"turns": 3})
        msgs = self.db.get_messages(sid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"

    def test_add_finding(self):
        sid = self.db.create_session("FTest", "single")
        f = Finding(session_id=sid, file_path="test.py", line=42, category="BUG",
                    severity="High", description_en="null pointer", description_cn="空指针",
                    suggestion="add check", verdict="CONFIRMED")
        self.db.add_finding(sid, f)
        findings = self.db.get_findings(session_id=sid)
        assert len(findings) == 1
        assert findings[0]["category"] == "BUG"

    def test_search_findings(self):
        sid = self.db.create_session("SearchTest", "single")
        self.db.add_finding(sid, Finding(session_id=sid, file_path="a.py", line=1,
            category="SECURITY", severity="High", description_en="SQL injection",
            description_cn="SQL注入", suggestion="Fix", verdict="CONFIRMED"))
        r = self.db.search_findings("injection")
        assert len(r) >= 1
        r2 = self.db.search_findings("注入")
        assert len(r2) >= 1

    def test_delete_cascade(self):
        sid = self.db.create_session("DelTest", "single")
        self.db.add_message(sid, "user", "test")
        self.db.add_finding(sid, Finding(session_id=sid, file_path="x.py", line=1,
            category="BUG", severity="Low", description_en="test", description_cn="测试",
            suggestion="", verdict="PENDING"))
        self.db.add_log(sid, "turn_start", 1, "read_file")
        self.db.delete_session(sid)
        assert self.db.get_session(sid) is None
        assert len(self.db.get_messages(sid)) == 0
        assert len(self.db.get_findings(session_id=sid)) == 0

    def test_get_stats(self):
        s = self.db.get_stats()
        assert s["total_sessions"] >= 0
        assert "by_category" in s
        assert "by_severity" in s

    def test_session_owner_filters_access(self):
        sid = self.db.create_session("Private", "single", owner_username="alice")

        assert self.db.get_session(sid, owner_username="bob") is None
        assert self.db.get_session(sid, owner_username="alice")["id"] == sid
        assert all(
            row["owner_username"] == "alice"
            for row in self.db.list_sessions(20, owner_username="alice")
        )

    @classmethod
    def teardown_class(cls):
        shutil.rmtree("./data/_test", ignore_errors=True)

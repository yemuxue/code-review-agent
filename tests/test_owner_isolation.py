"""所有者隔离回归测试 / Owner isolation regression tests。

覆盖整改清单 P0#4：会话、消息、日志、finding 与向量索引都必须按
owner_username 隔离，且"无归属"的历史数据不得对任何登录用户开放。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from storage.database import Database, Finding  # noqa: E402
from memory.vector_store import FindingDocument, VectorStore  # noqa: E402


class TestDatabaseOwnerIsolation:
    @classmethod
    def setup_class(cls):
        import shutil
        shutil.rmtree("./data/_test_owner", ignore_errors=True)
        cls.db = Database("./data/_test_owner/test.db")

    def test_other_user_cannot_read_session_or_messages(self):
        sid = self.db.create_session("Alice private", "single", owner_username="alice")
        self.db.add_message(sid, "user", "alice secret")

        assert self.db.get_session(sid, owner_username="bob") is None
        assert self.db.get_session(sid, owner_username="alice") is not None
        # 消息读取必须经由会话归属校验：非所有者拿不到该会话的 message 列表
        assert self.db.list_sessions(20, owner_username="bob") == []

    def test_delete_session_requires_matching_owner(self):
        sid = self.db.create_session("Bob private", "single", owner_username="bob")
        self.db.add_message(sid, "user", "bob secret")

        # 非所有者删除必须无效，且数据保持可读
        assert self.db.delete_session(sid, owner_username="alice") is False
        assert self.db.get_session(sid, owner_username="bob") is not None

        assert self.db.delete_session(sid, owner_username="bob") is True
        assert self.db.get_session(sid, owner_username="bob") is None

    def test_legacy_session_without_owner_is_not_owned_by_anyone(self):
        """迁移前 owner_username 为空的会话不授权任何登录用户。"""
        sid = self.db.create_session("Legacy", "single")  # owner 默认空串

        assert self.db.get_session_owner(sid) == ""
        assert self.db.owns_session(sid, "") is False
        assert self.db.owns_session(sid, "alice") is False
        assert self.db.delete_session(sid, owner_username="alice") is False

    def test_logs_are_filtered_by_owner(self):
        sid = self.db.create_session("LogOwner", "single", owner_username="carol")
        self.db.add_log(sid, "tool_call", 1, "read_file", message="secret trace")

        assert len(self.db.get_logs(sid, owner_username="carol")) == 1
        assert self.db.get_logs(sid, owner_username="dave") == []
        # None 表示全局视图（管理/内部调用），保留读取全部的能力
        assert len(self.db.get_logs(sid, owner_username=None)) == 1

    def test_update_session_can_backfill_owner(self):
        sid = self.db.create_session("Backfill", "single")
        self.db.update_session(sid, owner_username="erin")

        assert self.db.get_session_owner(sid) == "erin"
        assert self.db.owns_session(sid, "erin") is True

    def test_age_findings_are_filtered_by_owner(self):
        sid = self.db.create_session("FindOwner", "single", owner_username="frank")
        self.db.add_finding(sid, Finding(session_id=sid, file_path="a.py", line=3,
                                        category="BUG", severity="High",
                                        description_en="leak", description_cn="泄露",
                                        owner_username="frank"))

        assert len(self.db.get_findings(session_id=sid, owner_username="frank")) == 1
        assert self.db.get_findings(session_id=sid, owner_username="grace") == []
        assert len(self.db.search_findings("leak", owner_username="frank")) == 1
        assert self.db.search_findings("leak", owner_username="grace") == []

    @classmethod
    def teardown_class(cls):
        import shutil
        shutil.rmtree("./data/_test_owner", ignore_errors=True)


class TestVectorStoreOwnerIsolation:
    @classmethod
    def setup_class(cls):
        import shutil
        shutil.rmtree("./data/_test_vs_owner", ignore_errors=True)
        cls.vs = VectorStore("./data/_test_vs_owner/search.db")

    def test_search_only_returns_own_documents(self):
        self.vs.add_finding(FindingDocument(
            id="doc-alice", file_path="alice.py", category="BUG", severity="High",
            description_en="alice sql injection", description_cn="注入"), owner_username="alice")
        self.vs.add_finding(FindingDocument(
            id="doc-bob", file_path="bob.py", category="BUG", severity="High",
            description_en="bob sql injection", description_cn="注入"), owner_username="bob")

        alice_hits = self.vs.search("injection", owner_username="alice")
        assert [r["id"] for r in alice_hits] == ["doc-alice"]

        bob_hits = self.vs.search("injection", owner_username="bob")
        assert [r["id"] for r in bob_hits] == ["doc-bob"]

    def test_owner_none_keeps_global_view(self):
        """显式传 None 表示全局视图（管理端），不受所有者限制。"""
        ids = {r["id"] for r in self.vs.search("injection", owner_username=None)}
        assert {"doc-alice", "doc-bob"} <= ids

    def test_legacy_document_without_owner_row_is_hidden_from_users(self):
        self.vs.add_finding(FindingDocument(
            id="doc-legacy", file_path="legacy.py", category="BUG", severity="Low",
            description_en="legacy injection", description_cn="遗留"), owner_username="")

        assert all(r["id"] != "doc-legacy"
                   for r in self.vs.search("injection", owner_username="alice"))
        # 全局视图仍能看到历史数据
        assert any(r["id"] == "doc-legacy"
                   for r in self.vs.search("injection", owner_username=None))

    def test_like_fallback_also_honours_owner(self):
        """FTS 未命中而走 LIKE 分支时，所有者过滤同样生效。"""
        # 查询词只含单字符/标点，_escape_fts 会产生永不匹配的 FTS 表达式，
        # 从而强制走 LIKE 降级路径。
        results = self.vs.search("a", owner_username="alice")
        assert all(r["id"] != "doc-bob" for r in results)

    def test_category_and_severity_are_not_dropped(self):
        """search() 必须把 category/severity 传入底层查询（旧实现直接丢弃）。"""
        assert self.vs.search("injection", category="BUG", severity="High",
                              owner_username="alice")
        assert self.vs.search("injection", category="SECURITY",
                              owner_username="alice") == []

    def test_punctuation_only_query_returns_empty_without_error(self):
        assert self.vs.search(";;;", owner_username="alice") == []

    @classmethod
    def teardown_class(cls):
        import shutil
        cls.vs.close()
        shutil.rmtree("./data/_test_vs_owner", ignore_errors=True)

"""Vector store (FTS5) integration tests"""
import sys, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.vector_store import VectorStore, FindingDocument


class TestVectorStore:
    @classmethod
    def setup_class(cls):
        shutil.rmtree("./data/_test_vs", ignore_errors=True)
        cls.vs = VectorStore("./data/_test_vs/search.db")
        cls.vs.add_finding(FindingDocument(
            file_path="test.py", line=42, category="BUG", severity="High",
            description_en="SQL injection in raw query execution",
            description_cn="SQL注入漏洞在原始查询", suggestion="Use parameterized queries",
            session_id="test-1"))
        cls.vs.add_finding(FindingDocument(
            file_path="app.py", line=88, category="SECURITY", severity="High",
            description_en="Hardcoded API key in source code",
            description_cn="源代码中硬编码的API密钥", suggestion="Use env variable",
            session_id="test-1"))
        cls.vs.add_finding(FindingDocument(
            file_path="util.py", line=15, category="PERF", severity="Low",
            description_en="Unnecessary list copy in hot loop",
            description_cn="热循环中不必要的列表拷贝", suggestion="Use generator",
            session_id="test-2"))

    def test_count(self):
        assert self.vs.count() == 3

    def test_english_search(self):
        r = self.vs.search("SQL injection")
        assert len(r) >= 1
        assert r[0]["file_path"] == "test.py"

    def test_chinese_search(self):
        r = self.vs.search("注入")
        assert len(r) >= 1
        r2 = self.vs.search("硬编码")
        assert len(r2) >= 1

    def test_category_filter(self):
        r = self.vs.search("key", category="SECURITY")
        assert len(r) == 1
        assert r[0]["category"] == "SECURITY"

    def test_no_results(self):
        r = self.vs.search("xyz_nonexistent_abc")
        assert len(r) == 0

    def test_add_batch_inserts_each_document_once(self, tmp_path):
        store = VectorStore(str(tmp_path / "batch.db"))
        try:
            ids = store.add_batch([FindingDocument(description_en="insert once")])
            assert len(ids) == 1
            assert store.count() == 1
        finally:
            store.close()

    @classmethod
    def teardown_class(cls):
        cls.vs.close()
        shutil.rmtree("./data/_test_vs", ignore_errors=True)

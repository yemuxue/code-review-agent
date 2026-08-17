"""LangGraph 修复落盘与行为验证测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator
from src.multi_agent.agents import FIXER_SYSTEM_PROMPT


def test_old_backup_without_current_receipt_is_not_applied(tmp_path):
    """历史备份不能证明本轮 Fixer 已成功写入。"""
    target = tmp_path / "target.py"
    target.write_text("value = 1\n", encoding="utf-8")
    target.with_suffix(".py.bak").write_text("value = 0\n", encoding="utf-8")
    orchestrator = LangGraphOrchestrator(object(), [])

    result = orchestrator._verify_fix_node({
        "fixes": [{"finding_id": 1, "file_path": str(target), "status": "FIXED"}],
    })

    assert any(fix["status"] == "NOT_APPLIED" for fix in result["fixes"])


def test_passing_project_pytest_upgrades_applied_fix_to_verified(tmp_path):
    """只有受控 pytest 成功后，已落盘修复才可以标为已验证。"""
    target = tmp_path / "target.py"
    target.write_text("value = 2\n", encoding="utf-8")
    (tmp_path / "test_behavior.py").write_text(
        "def test_expected_behavior():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    orchestrator = LangGraphOrchestrator(object(), [])
    orchestrator._project_root = tmp_path
    orchestrator._write_receipts = {
        str(target.resolve()): {
            "after_sha256": orchestrator._text_sha256(target.read_text(encoding="utf-8")),
        },
    }

    result = orchestrator._verify_fix_node({
        "fixes": [{
            "finding_id": 1,
            "file_path": str(target),
            "status": "FIXED",
            "verify_command": "pytest test_behavior.py -q",
        }],
    })

    assert any(fix["status"] == "VERIFIED" for fix in result["fixes"])


def test_fixer_prompt_requires_machine_readable_behavior_test_command():
    """Fixer 必须提供可被验证节点解析的目标行为测试命令。"""
    assert "VERIFY|finding_id|pytest" in FIXER_SYSTEM_PROMPT


def test_behavior_verification_refuses_arbitrary_pytest_named_executable(tmp_path, monkeypatch):
    """绝对路径的同名可执行文件也不能绕过命令白名单。"""
    test_file = tmp_path / "test_behavior.py"
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    orchestrator = LangGraphOrchestrator(object(), [])
    orchestrator._project_root = tmp_path

    def unexpected_process(*args, **kwargs):
        raise AssertionError("不应启动未批准的可执行文件")

    monkeypatch.setattr("src.multi_agent.langgraph_orchestrator.subprocess.run", unexpected_process)
    passed, reason = orchestrator._run_behavior_verification(
        "C:/untrusted/pytest.exe test_behavior.py -q"
    )

    assert not passed
    assert "pytest" in reason

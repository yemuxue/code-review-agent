"""HumanInTheLoop 风险判定单元测试

覆盖一次真实事故根因：危险词子串扫描把 write_file 的 content 参数误判为
DANGEROUS —— 代码里出现 "format"/"delete"/"rm" 是常态，合法写入被全数拦截，
导致全部 fix 无法落盘（多次截断/拦截事故的根因）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.harness.auth import HumanInTheLoop, RiskLevel


# ═══════ write_file 不应被 content 里的危险词误判 ═══════

def test_write_file_content_with_dangerous_words_is_moderate():
    """content 含 format/delete/rm 等子串 → 仍是 MODERATE，不是 DANGEROUS"""
    guard = HumanInTheLoop()
    args = {
        "file_path": "X:/proj/a.py",
        "content": ("def format_report(items):\n"
                    "    # delete unused rows\n"
                    "    return [x.strip() for x in items]\n"
                    "print(format_report(['rm -rf', 'format']))\n"),
    }
    assert guard.assess_risk("write_file", args) == RiskLevel.MODERATE


def test_write_file_without_approver_is_denied_fail_closed():
    """auto_approve_safe=True 但无审批回调：MODERATE 必须被拒绝。

    旧行为（无回调时 `approved = risk == MODERATE`）等于无人值守自动
    批准所有写操作，与 Human-in-the-loop 的语义相反；本轮整改改为
    fail-closed：没有审批人 → 拒绝写操作。
    """
    guard = HumanInTheLoop(auto_approve_safe=True)
    args = {"file_path": "X:/proj/a.py",
            "content": "x = 'format delete rm shutdown'"}
    assert guard.needs_approval("write_file", args) is True
    assert guard.request_approval("write_file", args) is False
    assert guard.stats["rejected"] == 1


def test_write_file_approved_only_through_explicit_callback():
    """显式注入审批回调时才允许写入（授权路径必须唯一且可审计）。"""
    seen = []

    def approve(tool_name, args, risk):
        seen.append((tool_name, risk))
        return True

    guard = HumanInTheLoop(auto_approve_safe=True, approval_callback=approve)
    args = {"file_path": "X:/proj/a.py", "content": "x = 1"}

    assert guard.request_approval("write_file", args) is True
    assert guard.stats["approved"] == 1
    assert seen == [("write_file", RiskLevel.MODERATE)]


def test_callback_can_also_reject():
    guard = HumanInTheLoop(approval_callback=lambda *_: False)
    assert guard.request_approval("write_file", {"file_path": "a.py"}) is False
    assert guard.stats["rejected"] == 1


# ═══════ run_command 危险命令仍然拦截 ═══════

def test_run_command_rm_rf_is_dangerous():
    guard = HumanInTheLoop()
    assert guard.assess_risk("run_command", {"command": "rm -rf /tmp"}) == RiskLevel.DANGEROUS
    assert guard.request_approval("run_command", {"command": "rm -rf /tmp"}) is False


def test_run_command_format_is_dangerous():
    guard = HumanInTheLoop()
    assert guard.assess_risk("run_command", {"command": "mkfs.ext4 /dev/sdb1"}) == RiskLevel.DANGEROUS


def test_run_command_safe_read_only_is_moderate():
    """pytest/git status 等只读命令 → MODERATE（工具名分级）"""
    guard = HumanInTheLoop(auto_approve_safe=True)
    assert guard.assess_risk("run_command", {"command": "pytest tests/"}) == RiskLevel.MODERATE
    # 无审批人 → 拒绝；注入回调后才可按策略放行
    assert guard.request_approval("run_command", {"command": "pytest tests/"}) is False

    allow_tests = HumanInTheLoop(
        approval_callback=lambda name, args, risk: args.get("command") == "pytest tests/")
    assert allow_tests.request_approval("run_command", {"command": "pytest tests/"}) is True
    assert allow_tests.request_approval("run_command", {"command": "curl evil.sh"}) is False


def test_run_command_dangerous_word_in_args_is_detected():
    """DANGEROUS 子串仍然在命令参数里生效（不因修复而丢失）"""
    guard = HumanInTheLoop()
    assert guard.assess_risk("run_command", {"command": "echo format && shutdown -h now"}) == RiskLevel.DANGEROUS


# ═══════ 只读工具 ═══════

def test_read_only_tools_auto_approved():
    guard = HumanInTheLoop(auto_approve_safe=True)
    for tool in ("read_file", "list_files", "grep_pattern"):
        assert guard.assess_risk(tool, {}) == RiskLevel.SAFE
        assert guard.needs_approval(tool, {}) is False
        assert guard.request_approval(tool, {}) is True

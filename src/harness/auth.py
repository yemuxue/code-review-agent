"""
Human-in-the-loop Guard / 人机协作审批
危险工具调用前需人工确认

面试话术：'用权限分级 + 人工审批实现 Human-in-the-loop，危险操作不自动执行'
"""

from enum import Enum


class RiskLevel(Enum):
    SAFE = 0       # 只读操作：read_file, list_files, grep_pattern
    MODERATE = 1   # 写操作：write_file, git commit
    DANGEROUS = 2  # 危险操作：rm, delete, shell exec


RISK_RULES = {
    # SAFE tools
    "read_file": RiskLevel.SAFE,
    "list_files": RiskLevel.SAFE,
    "grep_pattern": RiskLevel.SAFE,
    "get_diff": RiskLevel.SAFE,
    "search_code": RiskLevel.SAFE,
    "clone_repo": RiskLevel.SAFE,
    # MODERATE tools
    "write_file": RiskLevel.MODERATE,
    "run_command": RiskLevel.MODERATE,
    # DANGEROUS patterns
    "rm": RiskLevel.DANGEROUS,
    "delete": RiskLevel.DANGEROUS,
    "format": RiskLevel.DANGEROUS,
    "shutdown": RiskLevel.DANGEROUS,
}


class HumanInTheLoop:
    """
    Human-in-the-loop 审批器。

    用法:
        guard = HumanInTheLoop()
        if guard.needs_approval("rm -rf /tmp"):
            if guard.request_approval("Delete /tmp?"):
                execute()
            else:
                return "User rejected"
    """

    def __init__(self, auto_approve_safe: bool = True, approval_callback=None):
        self.auto_approve_safe = auto_approve_safe
        self.approval_callback = approval_callback  # 外部审批函数
        self.stats = {"approved": 0, "rejected": 0, "auto_approved": 0}

    def assess_risk(self, tool_name: str, args: dict) -> RiskLevel:
        """评估工具调用的风险等级

        ⚠️ 危险参数子串扫描只对命令类工具（run_command）生效：
        不能把 DANGEROUS 子串（"rm"/"delete"/"format"/"shutdown"）用于
        write_file 的 content 参数——代码内容里出现这些词是常态，会把
        合法写入误判为 DANGEROUS 全数拦截（曾导致全部 fix 无法落盘）。
        """
        # 命令类工具：参数是 shell 命令，扫描危险子串 + 危险命令词
        if tool_name == "run_command":
            args_str = str(args).lower()
            for pattern, level in RISK_RULES.items():
                if pattern in args_str and level == RiskLevel.DANGEROUS:
                    return RiskLevel.DANGEROUS
            # 特殊危险命令（mkfs/dd if 等不在 RISK_RULES 里，必须在此检查——
            # 放在工具名循环后面会被 run_command→MODERATE 短路，成为死代码）
            cmd = args.get("command", "")
            if any(w in cmd for w in ["rm -rf", "format", "shutdown", "mkfs", "dd if"]):
                return RiskLevel.DANGEROUS
            if any(w in cmd for w in ["git push", "pip install", "npm install"]):
                return RiskLevel.MODERATE

        # 检查工具名（write_file/run_command → MODERATE；read_file 等 → SAFE）
        for pattern, level in RISK_RULES.items():
            if pattern in tool_name.lower():
                return level

        return RiskLevel.SAFE

    def needs_approval(self, tool_name: str, args: dict) -> bool:
        """是否需要人工审批"""
        if self.auto_approve_safe:
            return self.assess_risk(tool_name, args) != RiskLevel.SAFE
        return True

    def request_approval(self, tool_name: str, args: dict) -> bool:
        """请求人工审批。返回 True = 批准，False = 拒绝"""
        risk = self.assess_risk(tool_name, args)

        if risk == RiskLevel.SAFE and self.auto_approve_safe:
            self.stats["auto_approved"] += 1
            return True

        # 使用外部回调或默认行为
        if self.approval_callback:
            approved = self.approval_callback(tool_name, args, risk)
        else:
            # 默认：危险操作拒绝，中等操作批准
            approved = risk == RiskLevel.MODERATE

        if approved:
            self.stats["approved"] += 1
        else:
            self.stats["rejected"] += 1
        return approved

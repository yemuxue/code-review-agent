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
        """评估工具调用的风险等级"""
        args_str = str(args).lower()

        # 检查危险参数
        for pattern, level in RISK_RULES.items():
            if pattern in args_str and level == RiskLevel.DANGEROUS:
                return RiskLevel.DANGEROUS

        # 检查工具名
        for pattern, level in RISK_RULES.items():
            if pattern in tool_name.lower():
                return level

        # 命令行特殊检测
        if tool_name == "run_command":
            cmd = args.get("command", "")
            if any(w in cmd for w in ["rm -rf", "format", "shutdown", "mkfs", "dd if"]):
                return RiskLevel.DANGEROUS
            if any(w in cmd for w in ["git push", "pip install", "npm install"]):
                return RiskLevel.MODERATE

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

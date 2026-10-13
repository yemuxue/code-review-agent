"""
Database migration script / 数据库迁移脚本

Run: python -m src.storage.migrate

注意：Database() 在构造时已自动执行全部增量迁移，本脚本只是
"显式迁移 + 打印结果" 的运维入口，不重复实现迁移逻辑。
"""

import sys
from pathlib import Path

# 使用 src.* 绝对导入，与仓库其它模块一致。
# 旧实现把"仓库根"插入 sys.path 后 `from storage.database import ...`，
# 但仓库根下并没有 storage 包（真实包是 <root>/src/storage，且
# src/ 不是导入根），按文档命令执行必然 ModuleNotFoundError。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from src.storage.database import Database


def migrate():
    """Apply all pending migrations (delegates to Database.__init__)."""
    db = Database("./data/code_review.db")

    stats = db.get_stats()
    print("Migrations applied successfully.")
    print(f"Sessions: {stats['total_sessions']}")
    print(f"Findings: {stats['total_findings']}")


if __name__ == "__main__":
    migrate()

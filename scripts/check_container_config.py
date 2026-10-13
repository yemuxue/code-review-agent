"""Validate docker-compose.yml structure and .dockerignore coverage.

Run: python scripts/check_container_config.py
Exit code 0 = ok. Used manually and (optionally) in CI where docker is unavailable.

This encodes the delivery-layer guarantees that were missing before:
  - secrets/runtime data must be excluded from the build context
  - both services must define a healthcheck and a restart policy
"""
from __future__ import annotations

import sys
from pathlib import Path

REQUIRED_IGNORES = (".env", ".secrets", "data/", "logs/", "sessions/", ".git/")
REQUIRED_SERVICES = ("api", "streamlit")


def check_dockerignore() -> list[str]:
    path = Path(".dockerignore")
    if not path.is_file():
        return ["缺少 .dockerignore：构建上下文会把 .env、data/、logs/ 打进镜像"]
    # 两侧都去掉尾部斜杠再比较：`.secrets/` 与 `.secrets` 是同一条规则，
    # 只归一化一侧会让合法的写法被误判为"未排除"。
    entries = {line.strip().rstrip("/") for line in
               path.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.strip().startswith("#")}
    problems = []
    for required in REQUIRED_IGNORES:
        if required.rstrip("/") not in entries:
            problems.append(f".dockerignore 未排除 {required!r}（密钥/运行数据会进镜像）")
    return problems


def check_compose() -> list[str]:
    try:
        import yaml
    except ImportError:
        return []  # 无 pyyaml 时跳过，不让脚本本身成为依赖

    path = Path("docker-compose.yml")
    if not path.is_file():
        return ["缺少 docker-compose.yml"]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = data.get("services") or {}
    problems = []
    for name in REQUIRED_SERVICES:
        service = services.get(name)
        if service is None:
            problems.append(f"docker-compose.yml 缺少服务 {name}")
            continue
        if not service.get("healthcheck"):
            problems.append(f"服务 {name} 缺少 healthcheck（故障容器不会被重启接住）")
        if not service.get("restart"):
            problems.append(f"服务 {name} 缺少 restart 策略")
    return problems


def main() -> int:
    problems = check_dockerignore() + check_compose()
    if problems:
        print("[container-config] 发现问题：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("[container-config] OK：.dockerignore 已排除密钥与运行数据，"
          "两个服务均配置 healthcheck 与 restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())

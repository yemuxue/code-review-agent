import os
from pathlib import Path

class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""

def _load_dotenv():
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists(): return {}
    config = {}
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, _, v = line.partition("=")
            config[k.strip()] = v.strip().strip('"').strip("'")
    return config

for k, v in _load_dotenv().items():
    if k not in os.environ: os.environ[k] = v

def get_api_key():
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not key:
        raise ConfigError(
            "API key is missing. Set ANTHROPIC_AUTH_TOKEN in your environment "
            "or in the .env file (e.g. ANTHROPIC_AUTH_TOKEN=sk-...)."
        )
    return key

def get_base_url(): return os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
def get_model():
    # .env 文件优先于系统环境变量
    dotenv = _load_dotenv()
    return dotenv.get("ANTHROPIC_MODEL") or os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")

# 启动时校验关键配置，尽早暴露缺失的 API 密钥（fail fast）
get_api_key()

import logging
import os
from pathlib import Path

# Use python-dotenv when available: it handles quoted values, escaped
# characters and inline comments correctly. A small fallback parser is used
# only when python-dotenv is not installed.
try:
    from dotenv import dotenv_values
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _parse_dotenv(env_file):
    """Fallback .env parser (used only when python-dotenv is unavailable).

    Supports surrounding quotes (single/double), escaped quote characters and
    inline comments outside of quoted values.
    """
    config = {}
    with open(env_file, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            # Strip inline comments that appear outside of quoted values, so a
            # `# comment` after a closing quote is removed while a `#` inside
            # a quoted value is preserved.
            in_quote = None
            comment_pos = -1
            i = 0
            while i < len(line):
                ch = line[i]
                if in_quote:
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == in_quote:
                        in_quote = None
                elif ch in ('"', "'"):
                    in_quote = ch
                elif ch == "#":
                    comment_pos = i
                    break
                i += 1
            if comment_pos != -1:
                line = line[:comment_pos].rstrip()
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            # Remove a single pair of surrounding quotes and unescape them.
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                quote = v[0]
                v = v[1:-1].replace("\\" + quote, quote)
            config[k] = v
    return config


def _load_dotenv():
    """Read the .env file once and return its parsed key/value mapping."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return {}
    if _DOTENV_AVAILABLE:
        return {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    return _parse_dotenv(env_file)


# Load dotenv values exactly ONCE at import time and cache them in _DOTENV.
# Every getter below reads from this cached snapshot (plus os.environ), so
# there is no TOCTOU inconsistency and no double I/O on later calls.
_DOTENV = _load_dotenv()

for _k, _v in _DOTENV.items():
    if _k not in os.environ:
        os.environ[_k] = _v


def get_api_key():
    # Accept both the primary ANTHROPIC_AUTH_TOKEN and the common
    # ANTHROPIC_API_KEY env var names; the former takes precedence.
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ConfigError(
            "API key is missing. Set ANTHROPIC_AUTH_TOKEN (or ANTHROPIC_API_KEY) "
            "in your environment or in the .env file (e.g. "
            "ANTHROPIC_AUTH_TOKEN=sk-...)."
        )
    return key


def get_base_url():
    return os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")


def get_model():
    # Values were loaded once at import time (see _DOTENV / os.environ above),
    # so this getter does not re-read the .env file and stays consistent with
    # get_api_key() even if the .env file changes at runtime.
    return _DOTENV.get("ANTHROPIC_MODEL") or os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")


def init_config():
    """Explicitly validate required configuration (fail fast, on demand).

    Call this from an application entry point (e.g. main()) instead of relying
    on import-time validation, so the module stays importable in tests and CLI
    tools without a real API key.
    """
    api_key = get_api_key()  # raises ConfigError when the key is missing
    base_url = get_base_url()
    # SECURITY: verify the key/endpoint pairing. The key variable is named
    # ANTHROPIC_AUTH_TOKEN but the default endpoint is a DeepSeek URL. Sending
    # an Anthropic-only key to DeepSeek would leak it, so warn loudly when the
    # pairing looks inconsistent.
    if "deepseek" in base_url.lower():
        _LOGGER.warning(
            "ANTHROPIC_AUTH_TOKEN is being sent to a DeepSeek endpoint (%s). "
            "Ensure the configured key is a DeepSeek key, not an Anthropic-only key.",
            base_url,
        )
    return api_key

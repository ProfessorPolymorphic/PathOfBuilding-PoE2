"""Settings from environment variables, with app/.env as a fallback."""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file() -> None:
    """Minimal .env loader: KEY=VALUE lines; real environment variables win."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://mindrouter.uidaho.edu/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")


def missing_llm_settings() -> list[str]:
    return [k for k, v in {"LLM_API_KEY": LLM_API_KEY, "LLM_MODEL": LLM_MODEL}.items()
            if not v or v.startswith("mr2_paste")]


def require_llm() -> None:
    """For CLIs: exit with a helpful message if the LLM isn't configured."""
    if missing := missing_llm_settings():
        raise SystemExit(f"Set {', '.join(missing)} in {ENV_FILE} (see app/.env.example)")


class LLMNotConfigured(RuntimeError):
    pass


def require_llm_configured() -> None:
    """For the API: raise instead of exiting the server."""
    if missing := missing_llm_settings():
        raise LLMNotConfigured(f"chat is not configured: set {', '.join(missing)} (see app/.env.example)")

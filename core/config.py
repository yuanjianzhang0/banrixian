# -*- coding: utf-8 -*-
"""Centralized configuration management for Banrixian backend.

All environment-dependent and sensitive settings are loaded here, avoiding
hardcoded values in main.py or module files.
"""

from __future__ import annotations

import os
from typing import Literal


_DOTENV_LOADED = False


def _strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv_if_present() -> None:
    """Load a simple .env file before module-level config constants are set."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    current = os.path.abspath(os.getcwd())
    candidates: list[str] = []
    while True:
        candidates.append(os.path.join(current, ".env"))
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidates.append(os.path.join(project_root, ".env"))

    env_path = next((path for path in candidates if os.path.exists(path)), None)
    if not env_path:
        return

    try:
        with open(env_path, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = _strip_env_value(value)
    except OSError:
        return


load_dotenv_if_present()


def load_env_bool(key: str, default: bool = False) -> bool:
    """Load boolean from environment, handling common string representations."""
    value = os.getenv(key, "").strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    return default


def load_env_int(key: str, default: int = 0) -> int:
    """Load integer from environment with fallback to default."""
    try:
        return int(os.getenv(key, str(default)).strip())
    except ValueError:
        return default


# ==========================================
# Database Configuration
# ==========================================
DATABASE_HOST = os.getenv("DATABASE_HOST", "127.0.0.1")
DATABASE_PORT = load_env_int("DATABASE_PORT", 3306)
DATABASE_USER = os.getenv("DATABASE_USER", "banrixian_user")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "123456")
DATABASE_NAME = os.getenv("DATABASE_NAME", "banrixian")
DATABASE_CHARSET = os.getenv("DATABASE_CHARSET", "utf8mb4")
DATABASE_MAX_CONNECTIONS = load_env_int("DATABASE_MAX_CONNECTIONS", 30)
DATABASE_MIN_CACHED = load_env_int("DATABASE_MIN_CACHED", 5)
DATABASE_MAX_CACHED = load_env_int("DATABASE_MAX_CACHED", 15)

# ==========================================
# LLM Configuration
# ==========================================
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = load_env_int("LLM_MAX_TOKENS", 1800)

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Huawei Cloud
HUAWEI_MAAS_API_KEY = os.getenv("HUAWEI_MAAS_API_KEY", "")
HUAWEI_MAAS_BASE_URL = os.getenv("HUAWEI_MAAS_BASE_URL", "https://api.modelarts-maas.com")
HUAWEI_MAAS_MODEL = os.getenv("HUAWEI_MAAS_MODEL", "deepseek-r1-250528")

# Timeouts
CHAT_TIMEOUT_SECONDS = float(os.getenv("CHAT_TIMEOUT_SECONDS", "15"))
INTENT_TIMEOUT_SECONDS = float(os.getenv("INTENT_TIMEOUT_SECONDS", "8"))
WEATHER_TIMEOUT_SECONDS = float(os.getenv("WEATHER_TIMEOUT_SECONDS", "8"))

# ==========================================
# Weather Configuration
# ==========================================
WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "open-meteo").strip().lower()

# ==========================================
# SMS Configuration
# ==========================================
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "mock").strip().lower()
SMS_WEBHOOK_URL = os.getenv("SMS_WEBHOOK_URL", "").strip()
SMS_WEBHOOK_TOKEN = os.getenv("SMS_WEBHOOK_TOKEN", "").strip()
SMS_TIMEOUT_SECONDS = float(os.getenv("SMS_TIMEOUT_SECONDS", "8"))
SMS_DRY_RUN = load_env_bool("SMS_DRY_RUN", True)

# ==========================================
# Chat & Memory Configuration
# ==========================================
MAX_CHAT_HISTORY_ITEMS = load_env_int("MAX_CHAT_HISTORY_ITEMS", 10)

# ==========================================
# Server Configuration
# ==========================================
DEBUG = load_env_bool("DEBUG", False)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ==========================================
# Resolved Provider Detection
# ==========================================
def resolve_llm_provider() -> str:
    """Detect available LLM provider based on configured credentials."""
    if LLM_PROVIDER != "auto":
        return LLM_PROVIDER
    if OPENAI_API_KEY:
        return "openai"
    if HUAWEI_MAAS_API_KEY:
        return "huawei"
    return "local"


def is_provider_available(provider: str) -> bool:
    """Check if a specific provider has required credentials configured."""
    provider = provider.lower()
    if provider == "openai":
        return bool(OPENAI_API_KEY)
    if provider == "huawei":
        return bool(HUAWEI_MAAS_API_KEY)
    if provider in {"local", "fallback", "mock"}:
        return True
    return False

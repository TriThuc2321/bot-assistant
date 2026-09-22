"""Environment loading and validation (spec 00)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    openai_api_key: str | None
    vector_store_id: str | None
    vector_store_name: str
    zendesk_base_url: str
    zendesk_locale: str
    max_articles: int
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    output_dir: str
    dry_run: bool
    log_level: str


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_config(require_api_key: bool = True) -> Config:
    load_dotenv()

    api_key = _env("OPENAI_API_KEY") or _env("API_KEY") or None
    if require_api_key and not api_key:
        raise ConfigError("Missing OPENAI_API_KEY (or API_KEY) environment variable")

    cfg = Config(
        openai_api_key=api_key,
        vector_store_id=_env("VECTOR_STORE_ID") or None,
        vector_store_name=_env("VECTOR_STORE_NAME") or "optibot-kb",
        zendesk_base_url=(_env("ZENDESK_BASE_URL") or "https://support.optisigns.com").rstrip("/"),
        zendesk_locale=_env("ZENDESK_LOCALE") or "en-us",
        max_articles=_int("MAX_ARTICLES", 0),
        chunk_size_tokens=_int("CHUNK_SIZE_TOKENS", 800),
        chunk_overlap_tokens=_int("CHUNK_OVERLAP_TOKENS", 200),
        output_dir=_env("OUTPUT_DIR") or "articles",
        dry_run=_bool("DRY_RUN", False),
        log_level=_env("LOG_LEVEL") or "INFO",
    )

    if cfg.chunk_overlap_tokens > cfg.chunk_size_tokens // 2:
        raise ConfigError(
            f"CHUNK_OVERLAP_TOKENS ({cfg.chunk_overlap_tokens}) must be <= half of "
            f"CHUNK_SIZE_TOKENS ({cfg.chunk_size_tokens})"
        )
    if cfg.max_articles < 0:
        raise ConfigError("MAX_ARTICLES must be >= 0")
    return cfg

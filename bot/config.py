from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

VALID_MODES = {"polling", "webhook"}
VALID_ACTIONS = {"delete", "warn", "mute", "ban"}
TELEGRAM_WEBHOOK_PORTS = {80, 88, 443, 8443}


class ConfigError(RuntimeError):
    """Raised when the environment configuration is invalid."""


def _get(env: Mapping[str, str], key: str, default: str | None = None) -> str | None:
    value = env.get(key)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key)
    if raw is None:
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    mode: str = "polling"
    log_level: str = "INFO"
    db_path: Path = Path("data/bot.db")
    admin_cache_ttl: int = 300
    default_action: str = "delete"
    warn_limit: int = 3
    mute_seconds: int = 600
    violation_decay_seconds: int = 86400
    flood_max_messages: int = 5
    flood_window_seconds: int = 10
    captcha_enabled: bool = False
    captcha_timeout: int = 120
    captcha_kick_on_timeout: bool = True
    audit_chat_id: int | None = None
    webhook_url: str | None = None
    webhook_listen: str = "0.0.0.0"
    webhook_port: int = 8443
    webhook_path: str = "/telegram"
    webhook_secret: str | None = None
    webhook_cert: str | None = None
    webhook_key: str | None = None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from a mapping (defaults to ``os.environ``)."""
    if env is None:
        load_dotenv()
        env = os.environ

    token = _get(env, "BOT_TOKEN")
    if not token:
        raise ConfigError(
            "BOT_TOKEN is required. Copy .env.example to .env and set it."
        )

    mode = (_get(env, "MODE", "polling") or "polling").lower()
    if mode not in VALID_MODES:
        raise ConfigError(f"MODE must be one of {sorted(VALID_MODES)}, got {mode!r}")

    action = (_get(env, "DEFAULT_ACTION", "delete") or "delete").lower()
    if action not in VALID_ACTIONS:
        raise ConfigError(
            f"DEFAULT_ACTION must be one of {sorted(VALID_ACTIONS)}, got {action!r}"
        )

    audit_raw = _get(env, "AUDIT_CHAT_ID")
    audit_chat_id: int | None = None
    if audit_raw is not None:
        try:
            audit_chat_id = int(audit_raw)
        except ValueError as exc:
            raise ConfigError(
                f"AUDIT_CHAT_ID must be an integer, got {audit_raw!r}"
            ) from exc

    settings = Settings(
        bot_token=token,
        mode=mode,
        log_level=(_get(env, "LOG_LEVEL", "INFO") or "INFO").upper(),
        db_path=Path(_get(env, "DB_PATH", "data/bot.db") or "data/bot.db"),
        admin_cache_ttl=_get_int(env, "ADMIN_CACHE_TTL", 300),
        default_action=action,
        warn_limit=_get_int(env, "WARN_LIMIT", 3),
        mute_seconds=_get_int(env, "MUTE_SECONDS", 600),
        violation_decay_seconds=_get_int(env, "VIOLATION_DECAY_SECONDS", 86400),
        flood_max_messages=_get_int(env, "FLOOD_MAX_MESSAGES", 5),
        flood_window_seconds=_get_int(env, "FLOOD_WINDOW_SECONDS", 10),
        captcha_enabled=_get_bool(env, "CAPTCHA_ENABLED", False),
        captcha_timeout=_get_int(env, "CAPTCHA_TIMEOUT", 120),
        captcha_kick_on_timeout=_get_bool(env, "CAPTCHA_KICK_ON_TIMEOUT", True),
        audit_chat_id=audit_chat_id,
        webhook_url=_get(env, "WEBHOOK_URL"),
        webhook_listen=_get(env, "WEBHOOK_LISTEN", "0.0.0.0") or "0.0.0.0",
        webhook_port=_get_int(env, "WEBHOOK_PORT", 8443),
        webhook_path=_get(env, "WEBHOOK_PATH", "/telegram") or "/telegram",
        webhook_secret=_get(env, "WEBHOOK_SECRET"),
        webhook_cert=_get(env, "WEBHOOK_CERT"),
        webhook_key=_get(env, "WEBHOOK_KEY"),
    )

    if settings.mode == "webhook":
        if not settings.webhook_url:
            raise ConfigError("WEBHOOK_URL is required when MODE=webhook")
        try:
            parsed_url = urlsplit(settings.webhook_url)
            hostname = parsed_url.hostname
            url_port = 443 if parsed_url.port is None else parsed_url.port
        except ValueError as exc:
            raise ConfigError("WEBHOOK_URL must be a valid HTTPS URL") from exc
        if parsed_url.scheme != "https" or not hostname:
            raise ConfigError("WEBHOOK_URL must be HTTPS and include a hostname")
        if url_port not in TELEGRAM_WEBHOOK_PORTS:
            raise ConfigError(
                "WEBHOOK_URL port must be 443, 80, 88, or 8443; omitted ports "
                "default to 443"
            )
        if "?" in settings.webhook_url or "#" in settings.webhook_url:
            raise ConfigError("WEBHOOK_URL must not contain a query or fragment")
        if settings.webhook_path != parsed_url.path:
            raise ConfigError("WEBHOOK_PATH must exactly match the WEBHOOK_URL path")
        if not settings.webhook_secret:
            raise ConfigError("WEBHOOK_SECRET is required when MODE=webhook")
        if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", settings.webhook_secret) is None:
            raise ConfigError(
                "WEBHOOK_SECRET must be between 32 and 256 characters and contain "
                "only ASCII letters, digits, underscores, and hyphens"
            )
        if not 1 <= settings.webhook_port <= 65535:
            raise ConfigError("WEBHOOK_PORT must be between 1 and 65535")
        if bool(settings.webhook_cert) != bool(settings.webhook_key):
            raise ConfigError("WEBHOOK_CERT and WEBHOOK_KEY must be set together")

    return settings

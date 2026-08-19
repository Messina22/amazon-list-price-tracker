"""Configuration loading for the price tracker."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

# ${VAR} / ${VAR:-default} in notification settings, so secrets live in the
# environment (GitHub Actions secrets) rather than in this committed file.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(Exception):
    pass


def expand_env(value):
    """Expand ``${VAR}`` references inside strings, lists, and dicts."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), match.group(2) or ""), value
        )
    if isinstance(value, list):
        return [expand_env(entry) for entry in value]
    if isinstance(value, dict):
        return {key: expand_env(entry) for key, entry in value.items()}
    return value


@dataclass
class EmailConfig:
    enabled: bool = False
    to: list[str] = field(default_factory=list)
    sender: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    security: str = "starttls"  # starttls | ssl | none
    username: str = ""
    password: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.to and self.smtp_host and self.sender)


@dataclass
class WebhookConfig:
    enabled: bool = False
    url: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.url)


@dataclass
class NotificationsConfig:
    enabled: bool = False
    frequency: str = "weekly"  # daily | weekly
    day_of_week: str = "monday"  # weekly only
    baseline: str = "week"  # week | month | a number of days
    min_change_percent: float = 1.0
    state_file: Path = Path("data/notification_state.json")
    email: EmailConfig = field(default_factory=EmailConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)

    @property
    def channels_configured(self) -> bool:
        return self.email.configured or self.webhook.configured


@dataclass
class Config:
    list_url: str
    retention_days: int | None
    history_file: Path
    items_file: Path
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


def _as_bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n;]", value) if part.strip()]
    return [str(entry).strip() for entry in value if str(entry).strip()]


def _load_email(raw: dict) -> EmailConfig:
    try:
        port = int(raw.get("smtp_port") or 587)
    except (TypeError, ValueError):
        raise ConfigError("notifications.email.smtp_port must be a number")

    security = str(raw.get("security") or "starttls").strip().lower()
    if security not in {"starttls", "ssl", "none"}:
        raise ConfigError(
            "notifications.email.security must be one of: starttls, ssl, none"
        )

    return EmailConfig(
        enabled=_as_bool(raw.get("enabled")),
        to=_as_list(raw.get("to")),
        sender=str(raw.get("from") or raw.get("sender") or "").strip(),
        smtp_host=str(raw.get("smtp_host") or "").strip(),
        smtp_port=port,
        security=security,
        username=str(raw.get("username") or "").strip(),
        password=str(raw.get("password") or ""),
    )


def _load_notifications(raw: dict) -> NotificationsConfig:
    raw = expand_env(raw or {})
    if not isinstance(raw, dict):
        raise ConfigError("notifications must be a mapping")

    frequency = str(raw.get("frequency") or "weekly").strip().lower()
    if frequency not in {"daily", "weekly"}:
        raise ConfigError("notifications.frequency must be 'daily' or 'weekly'")

    day_of_week = str(raw.get("day_of_week") or "monday").strip().lower()
    if day_of_week not in WEEKDAYS:
        raise ConfigError(
            "notifications.day_of_week must be one of: " + ", ".join(WEEKDAYS)
        )

    baseline = str(raw.get("baseline") or "week").strip().lower()
    if baseline not in {"week", "month"}:
        # Also allow a raw number of days for anyone who wants a custom window.
        try:
            if int(baseline) <= 0:
                raise ValueError
        except ValueError:
            raise ConfigError(
                "notifications.baseline must be 'week', 'month', or a positive "
                "number of days"
            )

    try:
        min_change_percent = float(raw.get("min_change_percent") or 0)
    except (TypeError, ValueError):
        raise ConfigError("notifications.min_change_percent must be a number")
    if min_change_percent < 0:
        raise ConfigError("notifications.min_change_percent cannot be negative")

    webhook_raw = raw.get("webhook") or {}
    return NotificationsConfig(
        enabled=_as_bool(raw.get("enabled")),
        frequency=frequency,
        day_of_week=day_of_week,
        baseline=baseline,
        min_change_percent=min_change_percent,
        state_file=Path(raw.get("state_file") or "data/notification_state.json"),
        email=_load_email(raw.get("email") or {}),
        webhook=WebhookConfig(
            enabled=_as_bool(webhook_raw.get("enabled")),
            url=str(webhook_raw.get("url") or "").strip(),
        ),
    )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open() as f:
        raw = yaml.safe_load(f) or {}

    list_url = (raw.get("list_url") or "").strip()
    if not list_url:
        raise ConfigError(
            "No list_url configured. Edit config.yaml and set list_url to the "
            "share link of your public Amazon list."
        )

    retention_days = raw.get("retention_days")
    if retention_days is not None:
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            raise ConfigError("retention_days must be a whole number of days or null")
        if retention_days <= 0:
            raise ConfigError("retention_days must be positive (or null to keep forever)")

    return Config(
        list_url=list_url,
        retention_days=retention_days,
        history_file=Path(raw.get("history_file") or "data/price_history.csv"),
        items_file=Path(raw.get("items_file") or "data/items.json"),
        notifications=_load_notifications(raw.get("notifications") or {}),
    )

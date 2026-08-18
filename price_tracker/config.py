"""Configuration loading for the price tracker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class Config:
    list_url: str
    retention_days: int | None
    history_file: Path
    items_file: Path


class ConfigError(Exception):
    pass


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
    )

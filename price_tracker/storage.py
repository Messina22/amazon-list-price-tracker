"""Price history storage.

History is a plain CSV file committed to the repository, one row per item per
day. CSV keeps every daily run as a small, human-readable git diff and is easy
to load into pandas/spreadsheets later for graphing.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from .scraper import ListItem

FIELDNAMES = ["date", "key", "asin", "item_id", "title", "price", "currency", "available"]


@dataclass
class PriceRecord:
    date: str  # ISO date, e.g. 2026-08-18
    key: str  # stable item identity: ASIN when known, else the list item id
    asin: str
    item_id: str
    title: str
    price: str  # decimal string, empty when unavailable
    currency: str
    available: str  # "true" / "false"


def item_key(item: ListItem) -> str:
    return item.asin or item.item_id or item.title


def load_history(history_file: Path) -> list[PriceRecord]:
    if not history_file.exists():
        return []
    with history_file.open(newline="") as f:
        return [
            PriceRecord(**{field: row.get(field, "") for field in FIELDNAMES})
            for row in csv.DictReader(f)
        ]


def _write_history(history_file: Path, records: list[PriceRecord]) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with history_file.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))


def record_prices(
    history_file: Path, items: list[ListItem], on_date: date | None = None
) -> list[PriceRecord]:
    """Append today's prices to the history file.

    Running more than once on the same day replaces that day's rows instead of
    duplicating them, so the file always holds at most one row per item per day.
    """
    on_date = on_date or date.today()
    day = on_date.isoformat()

    new_records = [
        PriceRecord(
            date=day,
            key=item_key(item),
            asin=item.asin or "",
            item_id=item.item_id or "",
            title=item.title,
            price=f"{item.price:.2f}" if item.price is not None else "",
            currency=item.currency or "",
            available="true" if item.available else "false",
        )
        for item in items
    ]

    replaced_keys = {record.key for record in new_records}
    history = [
        record
        for record in load_history(history_file)
        if not (record.date == day and record.key in replaced_keys)
    ]
    history.extend(new_records)
    history.sort(key=lambda record: (record.date, record.key))
    _write_history(history_file, history)
    return new_records


def prune_history(history_file: Path, retention_days: int | None, today: date | None = None) -> int:
    """Delete records older than the retention window. Returns rows removed."""
    if retention_days is None:
        return 0
    today = today or date.today()
    cutoff = (today - timedelta(days=retention_days)).isoformat()

    history = load_history(history_file)
    kept = [record for record in history if record.date >= cutoff]
    removed = len(history) - len(kept)
    if removed:
        _write_history(history_file, kept)
    return removed


def write_items_snapshot(items_file: Path, items: list[ListItem]) -> None:
    """Save the latest view of the list (titles, URLs, current prices)."""
    items_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot = [
        {
            "key": item_key(item),
            "asin": item.asin,
            "item_id": item.item_id,
            "title": item.title,
            "price": item.price,
            "currency": item.currency,
            "available": item.available,
            "url": item.url,
        }
        for item in items
    ]
    items_file.write_text(json.dumps(snapshot, indent=2) + "\n")
